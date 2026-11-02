"""One-click orchestration. Every item uses a savepoint; no workflow is executed."""
import json
import logging

from psycopg2 import OperationalError, InterfaceError

from odoo import api, fields, models, _, Command
from odoo.exceptions import UserError
from odoo.tools import html2plaintext

from ..mapping import (MODEL_MAPPING, parse_legacy_action, legacy_status,
                       decision_time_matches, signature_digest, ArchiveOnly, validate_sequence)
from .migration import require_manager, snapshot, MigrationConfig, MigrationEvidence, MigrationDocument

_logger = logging.getLogger(__name__)


class MigrationStopped(Exception):
    def __init__(self, model, source_id, message, res_id=0):
        super().__init__(message)
        self.model, self.source_id, self.res_id = model, source_id, res_id


class MigrationRun(models.Model):
    _name = 'pam.migration.run'
    _description = 'Automatic Approval Migration'

    name = fields.Char(default='Approval Migration', readonly=True)
    company_id = fields.Many2one('res.company', required=True, readonly=True, default=lambda s: s.env.company)
    last_run = fields.Datetime(readonly=True)
    summary = fields.Text(readonly=True)
    state = fields.Selection([('idle', 'Not Completed'), ('done', 'Completed'), ('failed', 'Stopped / Rolled Back')],
                             default='idle', required=True, readonly=True)
    log_ids = fields.One2many('pam.migration.log', 'run_id', readonly=True)
    _sql_constraints = [('company_unique', 'unique(company_id)', 'Dashboard already exists for this company.')]

    @api.model
    def open_dashboard(self):
        require_manager(self.env)
        # A menu click creates the dashboard, never a user-created migration form.
        self.env.cr.execute('SELECT id FROM res_company WHERE id = %s FOR UPDATE', (self.env.company.id,))
        dashboard = self.search([('company_id', '=', self.env.company.id)], limit=1)
        if not dashboard:
            dashboard = self.create({'company_id': self.env.company.id})
        return {'type': 'ir.actions.act_window', 'name': _('Approval Migration'),
                'res_model': self._name, 'res_id': dashboard.id, 'view_mode': 'form'}

    def _log(self, kind, model, source_id, message, details='', res_id=0):
        self.env['pam.migration.log'].create({
            'run_id': self.id, 'status': kind, 'source_model': model, 'source_res_id': source_id,
            'document_res_id': res_id, 'message': str(message), 'details': details})

    def _attempt(self, model, source_id, callback, res_id=0):
        try:
            warning = None
            with self.env.cr.savepoint():
                try:
                    result = callback()
                except ArchiveOnly as archive:
                    warning, result = str(archive), None
            self._log('warning' if warning else 'success', model, source_id,
                      warning or result or 'Migrated', res_id=res_id)
            return True
        except (OperationalError, InterfaceError):
            # Broken connections / serialization errors need Odoo's normal retry.
            raise
        except Exception as error:
            _logger.exception('Approval migration failed for %s,%s', model, source_id)
            raise MigrationStopped(model, source_id, str(error), res_id) from error

    def action_run(self):
        self.ensure_one()
        require_manager(self.env)
        self.check_access('write')
        if self.company_id != self.env.company:
            raise UserError(_('Switch to the dashboard company before running migration.'))
        self.env.cr.execute('SELECT id FROM pam_migration_run WHERE id = %s FOR UPDATE', (self.id,))
        batch = self.env['pam.migration.batch'].search([('company_id', '=', self.company_id.id)], limit=1)
        if not batch:
            batch = self.env['pam.migration.batch'].create({'company_id': self.company_id.id})
        batch._check_manager()
        last_log = self.env['pam.migration.log'].search([('run_id', '=', self.id)], order='id desc', limit=1).id or 0
        try:
            # One outer savepoint covers EVERY config, archive, cycle and success
            # log in this attempt. A failed item aborts immediately.
            with self.env.cr.savepoint():
                success, skipped = self._run_items(batch)
                batch.config_ids.filtered(lambda row: row.rule_id and row.source_id.active).mapped('rule_id').sudo().write({'active': True})
        except (OperationalError, InterfaceError):
            raise
        except Exception as error:
            model = getattr(error, 'model', self._name)
            source_id = getattr(error, 'source_id', self.id)
            self._log('error', model, source_id, str(error), res_id=getattr(error, 'res_id', 0))
            self.write({'state': 'failed', 'last_run': fields.Datetime.now(), 'summary':
                        'STOPPED AND ROLLED BACK. No target imports from this run were retained.\n%s' % error})
            return self._notification(True)
        warnings = self.env['pam.migration.log'].search_count([
            ('run_id', '=', self.id), ('id', '>', last_log), ('status', '=', 'warning')])
        self.write({'state': 'done', 'last_run': fields.Datetime.now(), 'summary':
            'Migration completed for %s. Target configurations/cycles: %s. Existing items: %s. Archive-only warnings: %s.\n'
            'Active-source rules are active. Existing Dynamic data and legacy approval controls are unchanged. Archive-only warnings are retained; business actions were not executed.' % (
                self.company_id.display_name, success, skipped, warnings)})
        return self._notification(False)

    def _notification(self, failed):
        return {'type': 'ir.actions.client', 'tag': 'display_notification', 'params': {
            'title': _('Migration Stopped') if failed else _('Migration Finished'), 'message': self.summary,
            'type': 'danger' if failed else 'success', 'sticky': True,
            'next': {'type': 'ir.actions.client', 'tag': 'reload'}}}

    def _run_items(self, batch):
        success = errors = skipped = 0
        sources = self.env['approval.level.matrix'].sudo().with_context(active_test=False).search([
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False)])
        history_exists = self.env['payment.approval'].sudo().with_context(active_test=False).search_count([
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False)])
        if not sources and history_exists:
            raise MigrationStopped('approval.level.matrix', 0,
                'Legacy transaction history exists, but source approval configurations are missing. '
                'Restore the original configurations before retrying; zero target imports cannot be marked completed.')
        for source in sources:
            mappings = MODEL_MAPPING.get(source.ir_model_id.model, [('UNMAPPED', '')])
            for method, amount_field in mappings:
                config = batch.config_ids.filtered(lambda row: row.source_id == source and row.method_name == method)
                if config.rule_id:
                    if snapshot(source) != config.source_snapshot or json.dumps([
                            json.loads(snapshot(line)) for line in source.line_ids]) != config.line_snapshot:
                        raise MigrationStopped(source._name, source.id, 'Source configuration changed after an earlier completed migration.')
                    skipped += 1
                    continue
                def migrate_config(source=source, method=method, amount_field=amount_field):
                    if not source.company_id:
                        raise UserError(_('Legacy matrix has no company; automatic company assignment would be ambiguous.'))
                    row = batch.config_ids.filtered(lambda item: item.source_id == source and item.method_name == method)
                    vals = {'source_snapshot': snapshot(source), 'line_snapshot': json.dumps([
                        json.loads(snapshot(line)) for line in source.line_ids])}
                    if not row:
                        row = self.env['pam.migration.config'].create(dict(vals, batch_id=batch.id,
                            source_id=source.id, model_name=source.ir_model_id.model or 'UNKNOWN',
                            method_name=method, amount_field=amount_field))
                    else:
                        if row.source_snapshot != vals['source_snapshot'] or row.line_snapshot != vals['line_snapshot']:
                            self._log('info', source._name, source.id, 'Refreshing failed mapping snapshot',
                                      (row.source_snapshot or '') + '\n' + (row.line_snapshot or ''))
                        super(MigrationConfig, row).write(vals)
                    row._import_rule()
                    return 'Configuration migrated: %s' % method
                ok = self._attempt(source._name, source.id, migrate_config)
                success += int(ok)
                errors += int(not ok)

        if history_exists and not batch.config_ids.mapped('rule_id'):
            raise MigrationStopped('approval.level.matrix', 0,
                'No usable Dynamic configuration was created for existing legacy history. Migration stopped; no target imports retained.')
        sources = self.env['payment.approval'].sudo().with_context(active_test=False, bin_size=False).search([
            '|', ('company_id', '=', self.company_id.id), ('company_id', '=', False)], order='id')
        for source in sources:
            old = self.env['pam.migration.evidence'].search([('source_id', '=', source.id)], limit=1)
            if old.document_id.request_id:
                if snapshot(source) != old.source_snapshot:
                    raise MigrationStopped(source._name, source.id, 'Source history changed after an earlier completed migration.', source.res_id)
                skipped += 1
                continue
            def archive(source=source, old=old):
                if not source.company_id:
                    raise UserError(_('Legacy history has no company. It has been retained in the source system.'))
                parsed = parse_legacy_action(source.action)
                method = parsed[2] if parsed and parsed[:2] == (source.model, source.res_id) else 'UNMAPPED'
                doc = self.env['pam.migration.document'].search([
                    ('batch_id', '=', batch.id), ('model_name', '=', source.model or 'UNKNOWN'),
                    ('res_id', '=', source.res_id), ('method_name', '=', method)], limit=1)
                if not doc:
                    doc = self.env['pam.migration.document'].create({'batch_id': batch.id,
                        'model_name': source.model or 'UNKNOWN', 'res_id': source.res_id, 'method_name': method})
                vals = {'document_id': doc.id, 'source_id': source.id, 'level': source.level,
                        'amount': source.amount, 'actor_id': source.create_uid.id,
                        'legacy_created_at': source.create_date, 'signature': source.signature,
                        'remark': source.remark, 'source_snapshot': snapshot(source)}
                if old:
                    if old.source_snapshot != vals['source_snapshot']:
                        self._log('info', source._name, source.id, 'Retained prior evidence snapshot before retry', old.source_snapshot)
                        if old.document_id != doc:
                            raise UserError(_('Source action changed cycle identity; original archive retained.'))
                        super(MigrationEvidence, old).write(dict(vals, decision='unknown'))
                else:
                    self.env['pam.migration.evidence'].create(vals)
                return 'Legacy evidence preserved'
            ok = self._attempt(source._name, source.id, archive, source.res_id)
            errors += int(not ok)

        for document in batch.document_ids:
            if document.request_id:
                continue
            ok = self._attempt('pam.migration.document', document.id,
                               document._auto_import, document.res_id)
            success += int(ok)
            errors += int(not ok)
        batch.staged_at = fields.Datetime.now()
        return len(batch.config_ids.mapped('rule_id')) + len(batch.document_ids.mapped('request_id')), skipped


class AutomaticDocument(models.Model):
    _inherit = 'pam.migration.document'

    def _auto_import(self):
        try:
            return self._infer_auto_import()
        except ArchiveOnly:
            if not self.request_id:
                self.write({'target_state': 'archive', 'verified': False})
            raise

    def _infer_auto_import(self):
        self.ensure_one()
        if self.method_name == 'UNMAPPED' or self.model_name not in self.env.registry:
            raise ArchiveOnly('Unknown legacy button/model. Evidence archived; no approval inferred.')
        record = self.env[self.model_name].sudo().with_company(self.company_id).browse(self.res_id).exists()
        if not record:
            raise ArchiveOnly('Original document is missing. Original evidence archived.')
        if self.env['dam.rule']._company(record) != self.company_id:
            raise ArchiveOnly('Legacy source company differs from the business document company. Evidence archived without mixing companies.')
        source_rows = self.env['payment.approval'].sudo().with_context(active_test=False).search([
            ('company_id', '=', self.company_id.id), ('model', '=', self.model_name), ('res_id', '=', self.res_id)])
        expected_ids = []
        for source in source_rows:
            parsed = parse_legacy_action(source.action)
            if not parsed or parsed[:2] != (self.model_name, self.res_id):
                raise ArchiveOnly('This document has an ambiguous legacy action. Evidence archived.')
            if parsed[2] == self.method_name:
                expected_ids.append(source.id)
        if set(expected_ids) != set(self.evidence_ids.source_id.ids):
            raise UserError(_('Some source evidence could not be archived. Retry after fixing the logged source error.'))
        mappings = self.batch_id.config_ids.filtered(lambda row: row.rule_id
            and row.model_name == self.model_name and row.method_name == self.method_name
            and bool(record.sudo().filtered_domain(row.rule_id._domain())))
        if not mappings:
            raise ArchiveOnly('No supported migrated rule matches the current document. Original history archived; no live approval inferred.')
        if len(mappings) != 1:
            raise UserError(_('Expected one matching migrated configuration; found %s.', len(mappings)))
        evidence = self.evidence_ids.sorted(lambda row: (row.level, row.source_id.id))
        if len(evidence.mapped('level')) != len(set(evidence.mapped('level'))):
            raise ArchiveOnly('Repeated legacy levels/cycles are ambiguous. All original evidence archived.')
        status = record.approval_status if 'approval_status' in record._fields else ''
        try:
            state = legacy_status(status, evidence.mapped('level'))
        except ValueError as error:
            raise ArchiveOnly(str(error)) from error
        applicable = (mappings.rule_id.sudo()._applicable_lines(record)[0]
                      if state == 'pending' else mappings.rule_id.sudo().line_ids.sorted('level'))
        # Legacy chatter posts decision text and the original signature attachment
        # in the same message. Match creator, signature and exact decision label.
        used = set()
        decisions = {}
        messages = self.env['mail.message'].sudo().search([
            ('model', '=', self.model_name), ('res_id', '=', self.res_id)], order='id')
        for old in evidence:
            matches = []
            allowed = {'Level %s Approved' % old.level: 'approved'}
            if old.level == max(evidence.mapped('level')) and state == 'done':
                allowed['Final Approved'] = 'approved'
            if state == 'rejected' and old.level == max(evidence.mapped('level')):
                allowed['Record is Rejected'] = 'rejected'
            for message in messages:
                text = ' '.join(html2plaintext(str(message.body or '')).split())
                if (text not in allowed or message.id in used
                        or message.author_id != old.actor_id.partner_id
                        or not decision_time_matches(message.date, old.legacy_created_at)):
                    continue
                digest = signature_digest(old.signature)
                if digest and any(attachment.checksum == digest for attachment in message.attachment_ids):
                    matches.append((message, allowed[text]))
            if len(matches) != 1:
                raise ArchiveOnly('Legacy record %s has %s matching signed decision messages. Original history and signature archived.' % (old.source_id.id, len(matches)))
            message, decision = matches[0]
            used.add(message.id)
            old.decision = decision
            decisions[old.level] = (message, decision)
        self.write({'config_id': mappings.id, 'target_state': state, 'verified': True,
                    'review_note': 'Automatically matched exact legacy chatter decisions, creators and signature attachments. Message IDs: %s' % sorted(used)})
        self._create_automatic_request(record, mappings.rule_id.sudo(), evidence, state, applicable, decisions)
        return 'History migrated: %s' % state

    def _create_automatic_request(self, record, rule, evidence, state, applicable, decisions):
        if self.request_id:
            return self.request_id
        self.env['dam.rule']._lock_document(record, self.method_name)
        amount = evidence[-1].amount
        if state == 'pending':
            try:
                validate_sequence(evidence.mapped('level'), evidence.mapped('decision'), applicable.mapped('level'), state)
            except ValueError as error:
                raise ArchiveOnly('Pending history does not fit the remaining current levels: %s' % error) from error
            current_amount = rule._amount(record)
            if rule.approval_type == 'amount' and not self.company_id.currency_id.is_zero(current_amount - amount):
                raise ArchiveOnly('Pending approval amount changed. Original approvals archived; a new cycle is required.')
        elif state == 'done' and any(old.decision != 'approved' for old in evidence):
            raise ArchiveOnly('Completed status conflicts with signed decisions. Evidence archived.')
        elif state == 'rejected' and [old.decision for old in evidence] != ['approved'] * (len(evidence)-1) + ['rejected']:
            raise ArchiveOnly('Rejected status conflicts with signed decisions. Evidence archived.')
        steps = []
        for old in evidence:
            if not old.actor_id or not old.signature or not (old.remark or '').strip():
                raise ArchiveOnly('Missing original creator/signature/remark. Evidence archived.')
            if snapshot(old.source_id.sudo()) != old.source_snapshot:
                raise UserError(_('Source evidence changed during migration.'))
            message, decision = decisions[old.level]
            steps.append(Command.create({'level': old.level, 'user_ids': [Command.set(old.actor_id.ids)],
                'state': decision, 'signature': old.signature, 'remark': old.remark,
                ('approved_by' if decision == 'approved' else 'rejected_by'): old.actor_id.id,
                ('approved_at' if decision == 'approved' else 'rejected_at'): message.date}))
        if state == 'pending':
            for line in applicable.filtered(lambda row: row.level not in evidence.mapped('level')):
                steps.append(Command.create({'level': line.level, 'user_ids': [Command.set(line.user_ids.ids)]}))
        request = self.env['dam.request'].sudo().create({
            'name': '%s — migrated legacy approval' % record.display_name, 'rule_id': rule.id,
            'model_name': self.model_name, 'res_id': self.res_id, 'document_name': record.display_name,
            'method_name': self.method_name, 'button_label': rule.button_id.name,
            'company_id': self.company_id.id, 'requested_by': self.env.uid, 'amount': amount,
            'state': state, 'document_stamp': record.write_date.isoformat(),
            'approval_basis': rule._approval_basis(record), 'step_ids': steps})
        for old in evidence:
            message, decision = decisions[old.level]
            step = request.step_ids.filtered(lambda row: row.level == old.level)
            self.env['dam.event'].sudo().create({'request_id': request.id, 'kind': decision,
                'user_id': old.actor_id.id, 'level': old.level, 'step_id': step.id, 'event_at': message.date,
                'message': 'Automatically migrated signed legacy decision from mail.message %s; historical creator and signature preserved independently of current approvers.' % message.id})
        self.request_id = request
        return request


class MigrationLog(models.Model):
    _name = 'pam.migration.log'
    _description = 'Approval Migration Log'
    _order = 'id desc'

    run_id = fields.Many2one('pam.migration.run', required=True, ondelete='restrict', readonly=True)
    company_id = fields.Many2one(related='run_id.company_id', store=True)
    status = fields.Selection([('success', 'Success'), ('error', 'Error'), ('warning', 'Archived / Warning'), ('info', 'Information')], required=True, readonly=True)
    source_model = fields.Char(readonly=True)
    source_res_id = fields.Integer(readonly=True)
    document_res_id = fields.Integer(readonly=True)
    message = fields.Text(readonly=True)
    details = fields.Text(readonly=True)
