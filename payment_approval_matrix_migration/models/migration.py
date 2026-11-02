import base64
import json
import ast

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.osv import expression

from ..mapping import MODEL_MAPPING, parse_legacy_action, validate_sequence, ArchiveOnly, coalesce_levels


def require_manager(env):
    if not (env.user.has_group('base.group_system') and env.user.has_group(
            'dynamic_approval_matrix.group_dam_manager')):
        raise AccessError(_('Migration requires Settings and Approval Manager access.'))


def snapshot(record):
    """Retain original fields without running legacy workflow methods."""
    names = {
        'approval.level.matrix': ['name', 'type', 'ir_model_id', 'ir_field_id', 'model_domain',
                                 'company_id', 'active', 'remark', 'line_ids', 'write_date'],
        'payment.approval.matrix': ['user_id', 'user_ids', 'min_amt', 'max_amt', 'remark',
                                    'currency_id', 'active', 'company_id', 'level', 'approval_id', 'write_date'],
        'payment.approval': ['amount', 'level', 'model', 'res_id', 'action', 'next_level_id',
                             'signature', 'remark', 'active', 'company_id', 'create_uid', 'create_date', 'write_date'],
    }[record._name]
    values = record.with_context(bin_size=False).read(names)[0]
    return json.dumps(values, default=lambda v: v.decode('ascii') if isinstance(v, bytes) else str(v),
                      ensure_ascii=False, sort_keys=True)


class MigrationBatch(models.Model):
    _name = 'pam.migration.batch'
    _description = 'Approval Migration Batch'

    name = fields.Char(required=True, default='Legacy Approval Migration')
    company_id = fields.Many2one('res.company', required=True, default=lambda s: s.env.company)
    config_ids = fields.One2many('pam.migration.config', 'batch_id')
    document_ids = fields.One2many('pam.migration.document', 'batch_id')
    report = fields.Text(readonly=True)
    staged_at = fields.Datetime(readonly=True)

    _sql_constraints = [('company_unique', 'unique(company_id)', 'Use the existing migration batch for this company.')]

    def write(self, vals):
        require_manager(self.env)
        if 'company_id' in vals and any(batch.staged_at for batch in self):
            raise UserError(_('A staged batch cannot be moved to another company.'))
        return super().write(vals)

    def _check_manager(self):
        self.ensure_one()
        require_manager(self.env)
        self.check_access('write')
        if self.company_id not in self.env.companies:
            raise AccessError(_('Select the migration company.'))
        # Serializes staging/imports even across concurrent browser sessions.
        self.env.cr.execute('SELECT id FROM pam_migration_batch WHERE id = %s FOR UPDATE', (self.id,))

    def action_stage(self):
        self._check_manager()
        Config = self.env['pam.migration.config']
        Document = self.env['pam.migration.document']
        Evidence = self.env['pam.migration.evidence']
        notes = []
        sources = self.env['approval.level.matrix'].sudo().with_context(active_test=False).search([
            ('company_id', '=', self.company_id.id)])
        shared = self.env['approval.level.matrix'].sudo().with_context(active_test=False).search_count([
            ('company_id', '=', False)])
        if shared:
            notes.append('%s company-less matrices require explicit company assignment in the legacy system.' % shared)
        for source in sources:
            mappings = MODEL_MAPPING.get(source.ir_model_id.model, [('UNMAPPED', '')])
            for method, amount_field in mappings:
                if Config.search_count([('batch_id', '=', self.id), ('source_id', '=', source.id),
                                        ('method_name', '=', method)]):
                    continue
                Config.create({'batch_id': self.id, 'source_id': source.id,
                               'model_name': source.ir_model_id.model or 'UNKNOWN', 'method_name': method,
                               'amount_field': amount_field,
                               'source_snapshot': snapshot(source),
                               'line_snapshot': json.dumps([json.loads(snapshot(line)) for line in source.line_ids])})
        histories = self.env['payment.approval'].sudo().with_context(active_test=False, bin_size=False).search([
            ('company_id', '=', self.company_id.id)], order='id')
        for source in histories:
            if Evidence.search_count([('source_id', '=', source.id)]):
                continue
            parsed = parse_legacy_action(source.action)
            method = parsed[2] if parsed and parsed[:2] == (source.model, source.res_id) else 'UNMAPPED'
            document = Document.search([('batch_id', '=', self.id), ('model_name', '=', source.model or 'UNKNOWN'),
                                        ('res_id', '=', source.res_id), ('method_name', '=', method)], limit=1)
            if not document:
                document = Document.create({'batch_id': self.id, 'model_name': source.model or 'UNKNOWN',
                                             'res_id': source.res_id, 'method_name': method})
            Evidence.create({'document_id': document.id, 'source_id': source.id,
                             'level': source.level, 'amount': source.amount,
                             'actor_id': source.create_uid.id, 'legacy_created_at': source.create_date,
                             'signature': source.signature, 'remark': source.remark,
                             'source_snapshot': snapshot(source)})
        notes.append('Staged %s mappings, %s document groups and %s evidence records. No target workflow executed.' % (
            len(self.config_ids), len(self.document_ids), len(self.document_ids.evidence_ids)))
        notes.append('Snapshots are immutable. Repeated levels, unknown actions and unverified decisions require review.')
        self.write({'report': '\n'.join(notes), 'staged_at': fields.Datetime.now()})
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_import_configs(self):
        self._check_manager()
        report = []
        for config in self.config_ids:
            try:
                with self.env.cr.savepoint():
                    config._import_rule()
            except (UserError, ValueError) as error:
                report.append('%s / %s: %s' % (config.model_name, config.method_name, error))
        self.report = '\n'.join(report) or 'Configuration import completed. All new rules are inactive; review before cutover.'
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    def action_export_report(self):
        self._check_manager()
        lines = [self.report or '']
        for doc in self.document_ids:
            lines.append('%s,%s,%s: %s; request=%s; %s' % (
                doc.model_name, doc.res_id, doc.method_name, doc.target_state,
                doc.request_id.id or '', doc.review_note or 'NOT REVIEWED'))
        attachment = self.env['ir.attachment'].create({
            'name': 'approval_migration_report.txt', 'type': 'binary',
            'datas': base64.b64encode('\n'.join(lines).encode('utf-8')),
            'mimetype': 'text/plain', 'res_model': self._name, 'res_id': self.id})
        return {'type': 'ir.actions.act_url', 'url': '/web/content/%s?download=true' % attachment.id, 'target': 'self'}


class MigrationConfig(models.Model):
    _name = 'pam.migration.config'
    _description = 'Approval Configuration Mapping'

    batch_id = fields.Many2one('pam.migration.batch', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='batch_id.company_id', store=True)
    source_id = fields.Many2one('approval.level.matrix', required=True, ondelete='restrict', readonly=True)
    model_name = fields.Char(required=True, readonly=True)
    method_name = fields.Char(required=True)
    amount_field = fields.Char()
    source_snapshot = fields.Text(readonly=True)
    line_snapshot = fields.Text(readonly=True)
    rule_id = fields.Many2one('dam.rule', readonly=True, ondelete='set null')

    _sql_constraints = [('source_method_unique', 'unique(batch_id, source_id, method_name)', 'Mapping already exists.')]

    def write(self, vals):
        require_manager(self.env)
        if any(row.rule_id for row in self) and {'method_name', 'amount_field', 'source_id', 'batch_id'} & vals.keys():
            raise UserError(_('Imported mappings cannot be retargeted.'))
        if {'source_snapshot', 'line_snapshot'} & vals.keys():
            raise UserError(_('Source snapshots cannot be changed.'))
        return super().write(vals)

    def _import_rule(self):
        self.ensure_one()
        if self.rule_id:
            return self.rule_id
        self.batch_id._check_manager()
        source = self.source_id.sudo().with_context(active_test=False)
        if not source.ir_model_id:
            raise ArchiveOnly('Legacy configuration has no model. Original configuration snapshot retained.')
        if not source.active:
            raise ArchiveOnly('Inactive legacy configuration retained as a snapshot; no operational rule created.')
        if source.company_id != self.company_id or source.ir_model_id.model != self.model_name:
            raise UserError(_('Source company/model changed. Review the mapping.'))
        if snapshot(source) != self.source_snapshot or json.dumps([
                json.loads(snapshot(line)) for line in source.line_ids]) != self.line_snapshot:
            raise UserError(_('Source configuration changed after staging. Start with an updated staging batch.'))
        if (self.model_name, self.method_name) not in getattr(self.env.registry, '_dam_guards', {}):
            raise ArchiveOnly('Unsupported/unguarded legacy button: %s.%s. Configuration snapshot retained; no operational rule created.' % (self.model_name, self.method_name))
        self.env['dam.button']._sync_catalog()
        button = self.env['dam.button'].sudo().search([
            ('model_name', '=', self.model_name), ('method_name', '=', self.method_name)], limit=1)
        if not button:
            raise UserError(_('Target button was not discovered.'))
        try:
            source_domain = ast.literal_eval(source.model_domain or '[]')
        except (ValueError, SyntaxError, TypeError) as error:
            raise UserError(_('Legacy domain is not a literal domain list.')) from error
        if source.model_domain and not source_domain:
            raise ArchiveOnly('Explicit empty-list domain never matched in legacy inner_check; configuration retained as a snapshot.')
        # Legacy inner_check chose the first matching active matrix (default id
        # order). Exclude earlier domains so Dynamic cannot match two matrices.
        previous = self.env['approval.level.matrix'].sudo().search([
            ('company_id', '=', self.company_id.id), ('ir_model_id', '=', source.ir_model_id.id),
            ('id', '<', source.id)], order='id')
        earlier = []
        for prior in previous:
            domain = ast.literal_eval(prior.model_domain or '[]')
            if prior.model_domain and not domain:
                continue
            if not domain or domain == source_domain:
                raise ArchiveOnly('Legacy configuration was shadowed by earlier matrix %s; snapshot retained without merging differing approvers.' % prior.id)
            earlier.append(domain)
        effective_domain = expression.AND([source_domain, ['!'] + expression.OR(earlier)]) if earlier else source_domain
        amount_field = self.env['ir.model.fields'].sudo().search([
            ('model_id', '=', source.ir_model_id.id), ('name', '=', self.amount_field)], limit=1)
        approval_type = source.type
        constant_zero = source.type == 'amount' and not self.amount_field and any(
            method == self.method_name and not field for method, field in MODEL_MAPPING.get(self.model_name, []))
        if source.type == 'amount' and not amount_field and not constant_zero:
            raise UserError(_('A numeric amount field could not be determined.'))
        lines = source.line_ids.filtered('active')
        if constant_zero:
            approval_type = 'none'
            lines = lines.filtered(lambda line: line.min_amt <= 0 and (not line.max_amt or line.max_amt >= 0))
        if not lines:
            raise UserError(_('No active approval levels.'))
        try:
            merged = coalesce_levels([{'level': line.level, 'user_ids': line.user_ids.ids,
                'min_amount': line.min_amt, 'max_amount': line.max_amt} for line in lines], approval_type)
        except ValueError as error:
            raise UserError(str(error)) from error
        rule = self.env['dam.rule'].sudo().with_company(self.company_id).create({
            'name': source.name or 'Legacy Matrix %s' % source.id,
            'model_id': source.ir_model_id.id, 'button_id': button.id,
            'model_domain': repr(effective_domain), 'company_id': self.company_id.id,
            'approval_type': approval_type, 'amount_field_id': amount_field.id or False,
            'remark': source.remark, 'active': False,
            'line_ids': [Command.create(dict(line, user_ids=[Command.set(line['user_ids'])])) for line in merged]})
        self.rule_id = rule
        return rule


class MigrationDocument(models.Model):
    _name = 'pam.migration.document'
    _description = 'Legacy Approval Cycle Review'
    _rec_name = 'model_name'

    batch_id = fields.Many2one('pam.migration.batch', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='batch_id.company_id', store=True)
    model_name = fields.Char(required=True, readonly=True)
    res_id = fields.Integer(required=True, readonly=True)
    method_name = fields.Char(readonly=True)
    config_id = fields.Many2one('pam.migration.config', domain="[('batch_id', '=', batch_id), ('model_name', '=', model_name)]")
    evidence_ids = fields.One2many('pam.migration.evidence', 'document_id')
    target_state = fields.Selection([('archive', 'Archive / Needs Review'), ('pending', 'Continue Pending'),
                                     ('rejected', 'Rejected'), ('done', 'Completed')], default='archive', required=True)
    review_note = fields.Text(help='Document the external/chatter evidence used to verify decisions and cycle boundaries.')
    verified = fields.Boolean(string='I verified decisions, users, amounts and cycle boundaries')
    request_id = fields.Many2one('dam.request', readonly=True, ondelete='set null')

    _sql_constraints = [('document_unique', 'unique(batch_id, model_name, res_id, method_name)', 'Document group already exists.')]

    def write(self, vals):
        require_manager(self.env)
        if any(row.request_id for row in self) and set(vals) - {'request_id'}:
            raise UserError(_('Imported cycle reviews are immutable.'))
        return super().write(vals)

    def action_import(self):
        self.ensure_one()
        self.batch_id._check_manager()
        if self.request_id:
            return {'type': 'ir.actions.act_window', 'res_model': 'dam.request',
                    'res_id': self.request_id.id, 'view_mode': 'form'}
        if self.target_state == 'archive' or not self.verified or not (self.review_note or '').strip():
            raise UserError(_('Choose a target state and document the evidence review first.'))
        mapping = self.config_id
        if (not mapping.rule_id or mapping.batch_id != self.batch_id
                or mapping.model_name != self.model_name or mapping.company_id != self.company_id
                or not self.method_name or mapping.method_name != self.method_name):
            raise UserError(_('Choose an imported configuration matching this document and the parsed legacy button.'))
        if self.model_name not in self.env.registry:
            raise UserError(_('Document model no longer exists. Keep its archived evidence.'))
        document = self.env[self.model_name].browse(self.res_id).exists()
        if not document:
            raise UserError(_('Document no longer exists. Keep its archived evidence.'))
        self.env['dam.rule']._lock_document(document, mapping.method_name)
        self.invalidate_recordset()
        if self.request_id:
            raise UserError(_('This cycle has already been imported. Refresh the form.'))
        if self.env['dam.rule']._company(document) != self.company_id:
            raise UserError(_('Document company does not match.'))
        rule = mapping.rule_id.sudo()
        if not document.sudo().filtered_domain(rule._domain()):
            raise UserError(_('Current document does not match the migrated domain.'))
        lines, amount = rule._applicable_lines(document)
        evidence = self.evidence_ids.sorted(lambda row: (row.level, row.source_id.id))
        try:
            validate_sequence(evidence.mapped('level'), evidence.mapped('decision'), lines.mapped('level'), self.target_state)
        except ValueError as error:
            raise UserError(str(error)) from error
        status = document.approval_status if 'approval_status' in document._fields else ''
        if self.target_state == 'done' and status != 'Done':
            raise UserError(_('The original document is not marked Done.'))
        if self.target_state == 'rejected' and 'Rejected' not in (status or ''):
            raise UserError(_('The original document is not marked Rejected.'))
        if self.target_state == 'pending' and (status == 'Done' or 'Rejected' in (status or '')):
            raise UserError(_('The original document status conflicts with a pending cycle.'))
        steps = []
        for line in lines:
            old = evidence.filtered(lambda item: item.level == line.level)
            vals = {'level': line.level, 'user_ids': [Command.set(line.user_ids.ids)]}
            if old:
                if not old.actor_id or not old.signature or not (old.remark or '').strip():
                    raise UserError(_('Each reviewed decision requires its original user, signature and remark.'))
                if old.actor_id not in line.user_ids:
                    raise UserError(_('A legacy actor differs from the configured approvers. Reconcile manually.'))
                if rule.approval_type == 'amount' and not self.company_id.currency_id.is_zero(old.amount - amount):
                    raise UserError(_('Legacy approved amount differs from the current converted amount.'))
                if snapshot(old.source_id) != old.source_snapshot:
                    raise UserError(_('Legacy evidence changed after staging. Do not import stale evidence.'))
                vals.update({'state': old.decision, 'signature': old.signature, 'remark': old.remark,
                             ('approved_by' if old.decision == 'approved' else 'rejected_by'): old.actor_id.id})
                # Legacy create_date proves creation, not approval time. Do not invent a decision timestamp.
            steps.append(Command.create(vals))
        request = self.env['dam.request'].sudo().create({
            'name': '%s — migrated legacy approval' % document.display_name,
            'rule_id': rule.id, 'model_name': self.model_name, 'res_id': self.res_id,
            'document_name': document.display_name, 'method_name': self.method_name,
            'button_label': rule.button_id.name, 'company_id': self.company_id.id,
            'requested_by': self.env.uid, 'amount': amount, 'state': self.target_state,
            'document_stamp': document.write_date.isoformat(), 'approval_basis': rule._approval_basis(document),
            'step_ids': steps})
        for old in evidence:
            step = request.step_ids.filtered(lambda row: row.level == old.level)
            self.env['dam.event'].sudo().create({
                'request_id': request.id, 'kind': old.decision, 'user_id': old.actor_id.id,
                'level': old.level, 'step_id': step.id,
                'message': 'Imported reviewed legacy record %s; legacy creation time %s; decision time unknown. Reviewer %s: %s' % (
                    old.source_id.id, old.legacy_created_at, self.env.user.name, self.review_note)})
        self.request_id = request
        return {'type': 'ir.actions.act_window', 'res_model': 'dam.request',
                'res_id': request.id, 'view_mode': 'form'}


class MigrationEvidence(models.Model):
    _name = 'pam.migration.evidence'
    _description = 'Immutable Legacy Approval Evidence'
    _order = 'level, id'

    document_id = fields.Many2one('pam.migration.document', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='document_id.company_id', store=True)
    source_id = fields.Many2one('payment.approval', required=True, readonly=True, ondelete='restrict')
    level = fields.Integer(readonly=True)
    amount = fields.Float(readonly=True)
    actor_id = fields.Many2one('res.users', readonly=True, ondelete='restrict')
    legacy_created_at = fields.Datetime(readonly=True)
    signature = fields.Binary(attachment=False, readonly=True)
    remark = fields.Text(readonly=True)
    source_snapshot = fields.Text(readonly=True)
    decision = fields.Selection([('unknown', 'Unverified'), ('approved', 'Verified Approved'),
                                  ('rejected', 'Verified Rejected')], default='unknown', required=True)

    _sql_constraints = [('source_unique', 'unique(source_id)', 'This legacy evidence has already been staged.')]

    def write(self, vals):
        require_manager(self.env)
        if set(vals) - {'decision'} or any(row.document_id.request_id for row in self):
            raise UserError(_('Only the decision of unimported evidence can be reviewed.'))
        return super().write(vals)
