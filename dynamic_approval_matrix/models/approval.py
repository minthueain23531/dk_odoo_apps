import ast
import logging
import math
from urllib.parse import urlencode

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools import SQL, float_compare

from .guard import button_dispatch, discover_buttons, executing, install_guards, remove_guards

_logger = logging.getLogger(__name__)


class ApprovalButton(models.Model):
    _name = 'dam.button'
    _description = 'Dynamic Approval Button'
    _order = 'model_name, name'

    name = fields.Char(required=True)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(string='Technical Model', related='model_id.model', store=True, index=True)
    method_name = fields.Char(required=True)

    _sql_constraints = [
        ('model_method_unique', 'unique(model_id, method_name)', 'A button method must be unique per model.'),
    ]

    @api.model
    def _sync_catalog(self):
        buttons = discover_buttons(self.env)
        existing = {(b.model_name, b.method_name): b for b in self.sudo().search([])}
        for (model, method), label in buttons.items():
            if (model, method) not in existing:
                self.sudo().create({
                    'name': '%s (%s)' % (label, method),
                    'model_id': self.env['ir.model']._get_id(model),
                    'method_name': method,
                })

    @api.model
    def action_refresh(self):
        if not self.env.user.has_group('dynamic_approval_matrix.group_dam_manager'):
            raise AccessError(_('Only an Approval Manager can refresh buttons.'))
        self._sync_catalog()
        return {'type': 'ir.actions.client', 'tag': 'reload'}


class ApprovalRule(models.Model):
    _name = 'dam.rule'
    _description = 'Dynamic Approval Matrix'
    _order = 'name, id'

    name = fields.Char(required=True, default='Approval Matrix')
    approval_type = fields.Selection([('none', 'None'), ('amount', 'Amount')],
                                     string='Status', required=True, default='none')
    model_id = fields.Many2one('ir.model', string='Model', required=True, ondelete='cascade',
                              domain="[('transient', '=', False)]")
    model_name = fields.Char(string='Technical Model', related='model_id.model', store=True, index=True)
    button_id = fields.Many2one('dam.button', string='Button', required=True, ondelete='restrict',
                               domain="[('model_id', '=', model_id)]")
    method_name = fields.Char(related='button_id.method_name', store=True, index=True)
    model_domain = fields.Char(string='Domain', required=True, default='[]')
    amount_field_id = fields.Many2one('ir.model.fields', string='Amount Field', ondelete='set null',
                                     domain="[('model_id', '=', model_id), ('ttype', 'in', ['monetary', 'float', 'integer'])]")
    company_id = fields.Many2one('res.company', required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related='company_id.currency_id')
    remark = fields.Char()
    active = fields.Boolean(default=True)
    line_ids = fields.One2many('dam.rule.line', 'rule_id', string='Approvers', copy=True)

    def _register_hook(self):
        result = super()._register_hook()
        install_guards(self.env)
        return result

    def _unregister_hook(self):
        remove_guards(self.env)
        return super()._unregister_hook()

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.button_id = False
        self.amount_field_id = False
        if self.model_id:
            self.amount_field_id = self.env['ir.model.fields'].search([
                ('model_id', '=', self.model_id.id), ('name', 'in', ['amount_total', 'amount']),
                ('ttype', 'in', ['monetary', 'float', 'integer']),
            ], order='name desc', limit=1)

    def action_refresh_buttons(self):
        return self.env['dam.button'].action_refresh()

    def _domain(self):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.model_domain or '[]')
            if not isinstance(domain, list):
                raise ValueError('Expected a list')
            expression.normalize_domain(domain)
            return domain
        except (ValueError, SyntaxError, TypeError, AssertionError) as error:
            raise ValidationError(_('Domain must be a literal Odoo domain list.')) from error

    @api.constrains('model_id', 'button_id', 'model_domain', 'approval_type', 'amount_field_id', 'line_ids', 'active')
    def _check_configuration(self):
        for rule in self:
            if rule.button_id.model_id != rule.model_id:
                raise ValidationError(_('Choose a button belonging to the selected model.'))
            if (rule.model_name, rule.method_name) not in getattr(self.env.registry, '_dam_guards', {}):
                raise ValidationError(_('This button is not guarded in this registry. Restart Odoo after adding new button views.'))
            try:
                self.env[rule.model_name].sudo().search(rule._domain(), limit=1)
            except (ValueError, TypeError) as error:
                raise ValidationError(_('The domain is not valid for this model.')) from error
            if rule.active and not rule.line_ids:
                raise ValidationError(_('Add at least one approval level.'))
            if rule.approval_type == 'amount' and (
                    not rule.amount_field_id or rule.amount_field_id.model_id != rule.model_id
                    or rule.amount_field_id.ttype not in ('monetary', 'float', 'integer')):
                raise ValidationError(_('Choose a numeric amount field on this model.'))

    def _amount(self, record):
        self.ensure_one()
        if self.approval_type != 'amount':
            return 0.0
        value = record[self.amount_field_id.name]
        if not math.isfinite(value) or value < 0:
            raise UserError(_('Approval amount must be a finite, non-negative number.'))
        field = record._fields[self.amount_field_id.name]
        currency_field = getattr(field, 'currency_field', None)
        if not currency_field and 'currency_id' in record._fields:
            currency_field = 'currency_id'
        currency = record[currency_field] if currency_field else self.company_id.currency_id
        if currency and currency != self.company_id.currency_id:
            date = next((record[name] for name in ('date', 'date_order', 'invoice_date')
                         if name in record._fields and record[name]), fields.Date.context_today(record))
            value = currency._convert(value, self.company_id.currency_id, self.company_id, fields.Date.to_date(date))
        return value

    def _applicable_lines(self, record):
        self.ensure_one()
        amount = self._amount(record)
        precision = self.currency_id.rounding
        lines = self.line_ids.sorted('level')
        if self.approval_type == 'amount':
            lines = lines.filtered(lambda line:
                float_compare(amount, line.min_amount, precision_rounding=precision) >= 0
                and (not line.max_amount or float_compare(amount, line.max_amount, precision_rounding=precision) <= 0))
        if not lines:
            raise UserError(_('No approval level covers this amount. Ask an Approval Manager to correct the matrix.'))
        return lines, amount

    def _approval_basis(self, record):
        self.ensure_one()
        basis = {'company_id': self.company_id.id, 'domain': self._domain(),
                 'type': self.approval_type}
        if self.approval_type == 'amount':
            name = self.amount_field_id.name
            field = record._fields[name]
            currency_field = getattr(field, 'currency_field', None)
            if not currency_field and 'currency_id' in record._fields:
                currency_field = 'currency_id'
            basis.update({'field': name, 'amount': record[name],
                          'currency_field': currency_field or False,
                          'currency_id': record[currency_field].id if currency_field else False})
        return basis

    @api.model
    def _company(self, record):
        company = record.company_id if 'company_id' in record._fields else self.env.company
        return company or self.env.company

    @api.model
    def _matching_rule(self, record, rules):
        company = self._company(record)
        matches = rules.filtered(lambda rule: rule.company_id == company and bool(
            record.sudo().filtered_domain(rule._domain())))
        if len(matches) > 1:
            raise UserError(_('More than one approval matrix matches this button and document. Correct the overlapping domains.'))
        return matches

    @api.model
    def _notify(self, message, remaining=False):
        return {
            'type': 'ir.actions.client', 'tag': 'dam_display_notification',
            'params': {'title': _('Approval'), 'message': message, 'type': 'info',
                       'sticky': remaining, 'next': {'type': 'ir.actions.act_window_close'} if remaining
                       else {'type': 'ir.actions.client', 'tag': 'reload'}},
        }

    @api.model
    def _lock_document(self, record, method):
        record.check_access('read')
        record.check_access('write')
        company = self._company(record)
        if company not in self.env.companies:
            raise AccessError(_('Select the document company before approving.'))
        # The upsert creates a write conflict under PostgreSQL REPEATABLE READ,
        # unlike advisory/row locks alone when approvals do not edit the document.
        self.env.cr.execute('''
            INSERT INTO dam_lock (model_name, res_id, method_name, revision)
            VALUES (%s, %s, %s, 1)
            ON CONFLICT (model_name, res_id, method_name)
            DO UPDATE SET revision = dam_lock.revision + 1
        ''', (record._name, record.id, method))
        # Row locking also serializes document edits. Serialization errors are
        # handled by Odoo's standard request retry mechanism.
        record.flush_recordset()
        self.env.cr.execute(SQL('SELECT id FROM %s WHERE id = %s FOR UPDATE',
                                SQL.identifier(record._table), record.id))
        record.invalidate_recordset()

    @api.model
    def _guard(self, record, method, rules, original, args, kwargs, approval_wizard=None, decision='approve'):
        self._lock_document(record, method)
        company = self._company(record)
        stamp = record.write_date.isoformat()
        Request = self.env['dam.request'].sudo()
        latest = Request.search([
            ('model_name', '=', record._name), ('res_id', '=', record.id),
            ('method_name', '=', method),
        ], order='id desc', limit=1)
        is_button = button_dispatch.get() == (self.env.cr, record._name, method, tuple(record.ids))
        if latest.state == 'rejected':
            if latest.company_id not in self.env.companies:
                raise AccessError(_('This button has a rejected approval in another company. Ask an Approval Manager to review it.'))
            rejected = latest.step_ids.filtered(lambda line: line.state == 'rejected')[:1]
            raise UserError(_('This approval was rejected by %(user)s at %(date)s (level %(level)s).\nReason: %(reason)s\nAsk an Approval Manager to reset this approval.',
                              user=rejected.rejected_by.name, date=rejected.rejected_at,
                              level=rejected.level, reason=rejected.remark))
        changed = latest and latest._document_changed(record)
        if approval_wizard is not None:
            approval_wizard.invalidate_recordset()
            if (approval_wizard.create_uid != self.env.user or approval_wizard.consumed
                    or approval_wizard.sudo().request_id != latest
                    or latest.state != 'pending' or changed):
                raise UserError(_('This approval is no longer current. Close the wizard and click the document button again.'))
        if changed:
            if latest.state == 'pending':
                latest.write({'state': 'superseded'})
                latest._event('superseded', _('Approval amount or applicability changed; a new approval cycle is required.'))
            latest = Request
        if latest.state in ('reset', 'superseded'):
            latest = Request
        if latest and not latest.approval_basis and latest.rule_id:
            latest.approval_basis = latest.rule_id._approval_basis(record)
        if latest and latest.state == 'done':
            raise UserError(_('This button was already executed for this document version.'))
        if not latest:
            rule = self._matching_rule(record, rules)
            if not rule:
                return original(record, *args, **kwargs)
            lines, amount = rule._applicable_lines(record)
            if not is_button:
                raise UserError(_('Approval is required. Open the document and use its button to approve.'))
            latest = Request.create({
                'name': '%s — %s' % (record.display_name, rule.button_id.name),
                'rule_id': rule.id, 'model_name': record._name, 'res_id': record.id,
                'document_name': record.display_name,
                'method_name': method, 'button_label': rule.button_id.name,
                'company_id': company.id, 'document_stamp': stamp,
                'approval_basis': rule._approval_basis(record),
                'requested_by': self.env.uid, 'amount': amount,
                'step_ids': [Command.create({'level': line.level,
                    'user_ids': [Command.set(line.user_ids.ids)]}) for line in lines],
            })
            latest._event('requested', _('Approval requested.'))
        step = latest.step_ids.filtered(lambda line: line.state == 'pending').sorted('level')[:1]
        if step:
            if not is_button and approval_wizard is None:
                raise UserError(_('Approval is incomplete. Use the document button to approve the remaining levels.'))
            if self.env.user not in step.user_ids:
                raise AccessError(_(
                    'You do not have permission to approve this request at level %(level)s. '
                    'The current approvers are: %(users)s.',
                    level=step.level, users=', '.join(step.user_ids.mapped('name'))))
            if approval_wizard is None:
                return self.env['dam.approval.wizard']._open(latest, step, record, args, kwargs)
            if approval_wizard.sudo().step_id != step:
                raise UserError(_('The current approval level changed. Open a new approval wizard.'))
            signature, remark = approval_wizard._validate_evidence()
            if decision == 'reject':
                step.write({'state': 'rejected', 'rejected_by': self.env.uid,
                            'rejected_at': fields.Datetime.now(), 'signature': signature, 'remark': remark})
                latest.write({'state': 'rejected'})
                approval_wizard.sudo().write({'consumed': True})
                latest._event('rejected', _('Level %s rejected.', step.level), step.level, step=step)
                return self._notify(_('Level %s rejected. The document button remains blocked until an Approval Manager resets it.', step.level), remaining=True)
            step.write({'state': 'approved', 'approved_by': self.env.uid,
                        'approved_at': fields.Datetime.now(), 'signature': signature, 'remark': remark})
            approval_wizard.sudo().write({'consumed': True})
            latest._event('approved', _('Level %s approved.', step.level), step.level, step=step)
            remaining = latest.step_ids.filtered(lambda line: line.state == 'pending').sorted('level')
            if remaining:
                remaining[0]._notify_approvers()
                return self._notify(_('Level %(done)s approved. Waiting for level %(next)s: %(users)s',
                    done=step.level, next=remaining[0].level,
                    users=', '.join(remaining[0].user_ids.mapped('name'))), remaining=True)
        # Never sudo the business action; preserve the original args/context/result.
        key = (self.env.cr, record._name, method, tuple(record.ids))
        token = executing.set(executing.get() | {key})
        try:
            result = original(record, *args, **kwargs)
        finally:
            executing.reset(token)
        # A returned wizard is not proof of execution. Keep the approved cycle
        # usable for its follow-up invocation on the same unchanged document.
        opens_wizard = isinstance(result, dict) and result.get('type') == 'ir.actions.act_window' and result.get('target') == 'new'
        record.invalidate_recordset(['write_date'])
        latest.write({'state': 'approved' if opens_wizard else 'done',
                      'document_stamp': record.write_date.isoformat()})
        latest._event('released' if opens_wizard else 'executed',
                      _('Approved button opened a wizard.') if opens_wizard else _('Original button method completed.'))
        return result

    @api.model
    def get_panel(self, model, res_id):
        """Read-only RPC. Access to the business record is always checked first."""
        if model not in self.env.registry or not isinstance(res_id, int) or isinstance(res_id, bool):
            return []
        record = self.env[model].browse(res_id).exists()
        if not record:
            return []
        record.check_access('read')
        company = self._company(record)
        if company not in self.env.companies:
            raise AccessError(_('The document company is not selected.'))
        rules = self.sudo().search([('model_name', '=', model), ('company_id', '=', company.id)])
        requests = self.env['dam.request'].sudo().with_context(bin_size=False).search([
            ('model_name', '=', model), ('res_id', '=', res_id), ('company_id', '=', company.id),
        ], order='id desc')
        result = []
        methods = sorted(set(rules.mapped('method_name')) | set(requests.mapped('method_name')))
        for method in methods:
            latest = requests.filtered(lambda request: request.method_name == method)[:1]
            if latest:
                result.append({
                    'key': method, 'label': latest.button_label, 'state': latest.state,
                    'request_id': latest.id,
                    'can_reset': latest.state in ('pending', 'rejected') and self.env.user.has_group('dynamic_approval_matrix.group_dam_manager'),
                    'changed': latest._document_changed(record),
                    'steps': [{'level': step.level, 'users': ', '.join(step.user_ids.mapped('name')),
                               'state': step.state, 'by': (step.rejected_by or step.approved_by).name or '',
                               'at': fields.Datetime.to_string(step.rejected_at or step.approved_at) or '',
                               'remark': step.remark or '', 'signature': step._signature_data()}
                              for step in latest.step_ids.sorted('level')],
                    'history': [{'id': event.id, 'at': fields.Datetime.to_string(event.event_at),
                                 'by': event.user_id.name, 'message': event.message,
                                 'remark': event.step_id.remark or '',
                                 'signature': event.step_id._signature_data() if event.step_id else ''}
                                for event in requests.filtered(lambda req: req.method_name == method).mapped('event_ids').sorted('id', reverse=True)],
                })
            else:
                rule = self._matching_rule(record, rules.filtered(lambda item: item.method_name == method))
                if not rule:
                    continue
                try:
                    lines, _amount = rule._applicable_lines(record)
                    error = ''
                except UserError as exc:
                    lines, error = self.env['dam.rule.line'], str(exc)
                result.append({'key': method, 'label': rule.button_id.name, 'state': 'not_started',
                               'changed': False, 'error': error,
                               'steps': [{'level': line.level, 'users': ', '.join(line.user_ids.mapped('name')),
                                          'state': 'pending', 'by': '', 'at': ''} for line in lines], 'history': []})
        return result

    @api.model
    def get_list_status(self, model, res_ids):
        """One bounded batch per visible page; never fetch signatures or history."""
        if (model not in self.env.registry
                or not isinstance(res_ids, list) or len(res_ids) > 2000
                or any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in res_ids)):
            raise UserError(_('Invalid approval status request.'))
        records = self.env[model].browse(res_ids)
        if model.startswith(('ir.', 'res.', 'dam.', 'base.')) and not getattr(records, '_dam_approval_buttons', ()):
            return {'enabled': False, 'statuses': {}}
        companies = self.env.companies.ids
        enabled = bool(self.sudo().search([
            ('model_name', '=', model), ('company_id', 'in', companies),
        ], limit=1))
        statuses = self._get_document_approval_status(records)
        return {'enabled': enabled or bool(statuses), 'statuses': statuses}

    @api.model
    def _get_document_approval_status(self, records):
        """Batch helper shared by list display and dam.approval.status.mixin.

        Return {record_id: {key, text, button}} for documents with history.
        Missing IDs mean not_started. The earliest button remains the anchor;
        its latest cycle supplies the status, including after an explicit reset.
        Caller access is checked before querying internal approval tables.
        """
        records = records.exists()
        records.check_access('read')
        if not records:
            return {}
        model = records._name
        companies = self.env.companies.ids
        self.env['dam.request'].flush_model(['model_name', 'res_id', 'method_name', 'company_id', 'state'])
        self.env['dam.step'].flush_model(['request_id', 'state', 'level'])
        # Lateral indexed lookups read only the first and latest cycles per
        # document. Query count is independent of the number of visible rows.
        self.env.cr.execute('''
            SELECT doc.id, latest.state, levels.level, first.button_label
              FROM unnest(%s::int[]) AS doc(id)
              JOIN LATERAL (
                  SELECT method_name, button_label FROM dam_request
                   WHERE model_name = %s AND res_id = doc.id AND company_id = ANY(%s)
                   ORDER BY id LIMIT 1
              ) first ON TRUE
              JOIN LATERAL (
                  SELECT id, state FROM dam_request
                   WHERE model_name = %s AND res_id = doc.id
                     AND method_name = first.method_name AND company_id = ANY(%s)
                   ORDER BY id DESC LIMIT 1
              ) latest ON TRUE
              LEFT JOIN LATERAL (
                  SELECT max(level) AS level FROM dam_step
                   WHERE request_id = latest.id AND state = 'approved'
              ) levels ON TRUE
        ''', (records.ids, model, companies, model, companies))
        statuses = {}
        for res_id, state, level, label in self.env.cr.fetchall():
            if state == 'done':
                key = 'done'
                text = _('Done')
            elif state == 'rejected':
                key = 'rejected'
                text = _('Rejected')
            elif state in ('reset', 'superseded') or level is None:
                key = 'not_started'
                text = _('Not Started')
            else:
                key = 'level_%s_approved' % level
                text = _('Level %s Approved', level)
            statuses[res_id] = {'key': key, 'text': text, 'button': label}
        return statuses


class ApprovalRuleLine(models.Model):
    _name = 'dam.rule.line'
    _description = 'Dynamic Approval Matrix Level'
    _order = 'level, id'

    rule_id = fields.Many2one('dam.rule', required=True, ondelete='cascade')
    company_id = fields.Many2one(related='rule_id.company_id', store=True)
    user_ids = fields.Many2many('res.users', 'dam_rule_line_user_rel', 'line_id', 'user_id',
                               string='Approvers', required=True, domain="[('share', '=', False)]")
    level = fields.Integer(default=1, required=True)
    min_amount = fields.Float(string='Min.', default=0)
    max_amount = fields.Float(string='Max. (0 = unlimited)', default=0)

    _sql_constraints = [('rule_level_unique', 'unique(rule_id, level)', 'Each level must be unique within a matrix.')]

    @api.constrains('level', 'user_ids', 'min_amount', 'max_amount', 'company_id')
    def _check_line(self):
        for line in self:
            if line.level < 1 or not line.user_ids:
                raise ValidationError(_('Every level needs a positive level number and at least one approver.'))
            if (not math.isfinite(line.min_amount) or not math.isfinite(line.max_amount)
                    or line.min_amount < 0 or line.max_amount < 0
                    or (line.max_amount and line.max_amount < line.min_amount)):
                raise ValidationError(_('Use non-negative ranges with maximum greater than or equal to minimum.'))
            if any(user.share or not user.active or line.company_id not in user.company_ids for user in line.user_ids):
                raise ValidationError(_('Approvers must be active internal users with access to this company.'))


class ApprovalRequest(models.Model):
    _name = 'dam.request'
    _description = 'Dynamic Approval Transaction'
    _order = 'id desc'

    name = fields.Char(required=True)
    rule_id = fields.Many2one('dam.rule', ondelete='set null')
    model_id = fields.Many2one('ir.model', string='Model', compute='_compute_model_id', store=True, ondelete='set null')
    model_name = fields.Char(string='Technical Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    document_name = fields.Char(string='Document Name')
    method_name = fields.Char(string='Button Method', required=True, index=True)
    button_label = fields.Char(string='Button', required=True)
    company_id = fields.Many2one('res.company', required=True, index=True)
    requested_by = fields.Many2one('res.users', required=True)
    amount = fields.Monetary(currency_field='currency_id')
    currency_id = fields.Many2one(related='company_id.currency_id')
    document_stamp = fields.Char(required=True)
    approval_basis = fields.Json(copy=False)
    state = fields.Selection([('pending', 'Waiting for Approval'), ('approved', 'Approved — Wizard Pending'),
                              ('done', 'Executed'), ('rejected', 'Rejected'), ('reset', 'Reset'),
                              ('superseded', 'Document Changed')], default='pending', required=True)
    step_ids = fields.One2many('dam.step', 'request_id')
    event_ids = fields.One2many('dam.event', 'request_id')

    def _invalidate_document_status(self):
        """Approval writes can occur without writing the business document."""
        for model, requests in self.sudo().grouped('model_name').items():
            if model in self.env.registry and 'dam_approval_status' in self.env[model]._fields:
                self.env[model].browse(requests.mapped('res_id')).invalidate_recordset(
                    ['dam_approval_status'], flush=False)

    @api.model_create_multi
    def create(self, vals_list):
        requests = super().create(vals_list)
        requests._invalidate_document_status()
        return requests

    def write(self, vals):
        if {'model_name', 'res_id'} & vals.keys():
            self._invalidate_document_status()
        result = super().write(vals)
        if {'state', 'model_name', 'res_id', 'method_name', 'company_id'} & vals.keys():
            self._invalidate_document_status()
        return result

    def unlink(self):
        self._invalidate_document_status()
        return super().unlink()

    def _document_changed(self, record):
        self.ensure_one()
        if self.state in ('rejected', 'reset'):
            return False
        if self.state not in ('pending', 'approved'):
            return self.document_stamp != record.write_date.isoformat()
        basis = self.approval_basis
        if not basis:
            # Upgrade compatibility: keep existing progress when the configured
            # amount still matches the amount saved by the previous version.
            if not self.rule_id:
                return True
            if self.rule_id.approval_type == 'amount' and float_compare(
                    self.rule_id._amount(record), self.amount,
                    precision_rounding=self.currency_id.rounding):
                return True
            basis = self.rule_id._approval_basis(record)
        if self.env['dam.rule']._company(record).id != basis['company_id']:
            return True
        if not record.sudo().filtered_domain(basis['domain']):
            return True
        if basis['type'] == 'amount':
            name = basis['field']
            currency_field = basis['currency_field']
            if name not in record._fields or record[name] != basis['amount']:
                return True
            if currency_field and (currency_field not in record._fields
                                   or record[currency_field].id != basis['currency_id']):
                return True
        return False

    @api.depends('model_name')
    def _compute_model_id(self):
        for request in self:
            request.model_id = self.env['ir.model']._get(request.model_name)

    def _event(self, kind, message, level=0, step=None):
        self.ensure_one()
        self.env['dam.event'].sudo().create({'request_id': self.id, 'kind': kind,
            'level': level, 'message': message, 'user_id': self.env.uid,
            'step_id': step.id if step else False})

    def action_open_document(self):
        self.ensure_one()
        record = self.env[self.model_name].browse(self.res_id).exists()
        if not record:
            raise UserError(_('The original document no longer exists.'))
        record.check_access('read')
        return {'type': 'ir.actions.act_window', 'res_model': self.model_name,
                'res_id': self.res_id, 'view_mode': 'form', 'target': 'current'}

    def action_open_reset(self):
        self.ensure_one()
        self.check_access('read')
        if not self.env.user.has_group('dynamic_approval_matrix.group_dam_manager'):
            raise AccessError(_('Only an Approval Manager can reset approvals.'))
        if self.state not in ('pending', 'rejected'):
            raise UserError(_('Only pending or rejected approvals can be reset.'))
        record = self.env[self.model_name].browse(self.res_id).exists()
        if not record:
            raise UserError(_('The original document no longer exists.'))
        record.check_access('write')
        wizard = self.env['dam.reset.wizard'].sudo().create({'request_id': self.id})
        return {'type': 'ir.actions.act_window', 'name': _('Reset Approval'),
                'res_model': 'dam.reset.wizard', 'res_id': wizard.id,
                'views': [(False, 'form')], 'target': 'new'}

    def init(self):
        # Cover the latest-cycle guard and the first-button list anchor.
        self.env.cr.execute('CREATE INDEX IF NOT EXISTS dam_request_document_method_idx ON dam_request (model_name, res_id, method_name, id)')
        self.env.cr.execute('CREATE INDEX IF NOT EXISTS dam_request_document_first_idx ON dam_request (model_name, res_id, id)')


class ApprovalStep(models.Model):
    _name = 'dam.step'
    _description = 'Dynamic Approval Transaction Level'
    _order = 'level, id'

    request_id = fields.Many2one('dam.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    level = fields.Integer(required=True)
    user_ids = fields.Many2many('res.users', 'dam_step_user_rel', 'step_id', 'user_id', string='Approvers')
    state = fields.Selection([('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending', required=True)
    approved_by = fields.Many2one('res.users')
    approved_at = fields.Datetime()
    rejected_by = fields.Many2one('res.users')
    rejected_at = fields.Datetime()
    signature = fields.Binary(attachment=False, copy=False)
    remark = fields.Text(copy=False)
    notified_at = fields.Datetime(string='Approvers Notified At', readonly=True, copy=False)
    email_notified_at = fields.Datetime(string='Approval Email Queued At', readonly=True, copy=False)

    def _get_approval_document_url(self):
        self.ensure_one()
        request = self.request_id
        document = self.env[request.model_name].with_company(request.company_id).browse(request.res_id)
        return document.get_base_url().rstrip('/') + '/web#' + urlencode({
            'id': request.res_id, 'model': request.model_name, 'view_type': 'form'})

    def _queue_approval_emails(self, users):
        """Queue only; SMTP failures must not roll back a signed approval."""
        self.ensure_one()
        template = self.env.ref('dynamic_approval_matrix.mail_template_next_approver')
        queued = False
        for user in users:
            if not user.email:
                _logger.warning('Approval step %s: user %s has no email; email skipped', self.id, user.id)
                continue
            template.sudo().with_company(self.company_id).with_context(lang=user.lang).send_mail(
                self.id, force_send=False,
                email_values={'recipient_ids': [Command.set(user.partner_id.ids)],
                              'email_to': False, 'email_cc': False})
            queued = True
        if queued:
            self.sudo().write({'email_notified_at': fields.Datetime.now()})

    @api.model_create_multi
    def create(self, vals_list):
        steps = super().create(vals_list)
        steps.request_id._invalidate_document_status()
        return steps

    def write(self, vals):
        requests = self.request_id if 'request_id' in vals else self.env['dam.request']
        result = super().write(vals)
        if {'state', 'level', 'request_id'} & vals.keys():
            (requests | self.request_id)._invalidate_document_status()
        return result

    def unlink(self):
        self.request_id._invalidate_document_status()
        return super().unlink()

    def _get_approval_notification_message(self):
        """Return safe HTML for both the approval inbox message and popup.

        Custom addons can inherit dam.step and override this singleton hook.
        Use Markup.format() to escape dynamic document values.
        """
        self.ensure_one()
        request = self.request_id
        url = '/web#' + urlencode({'id': request.res_id, 'model': request.model_name, 'view_type': 'form'})
        return Markup('<p>{}</p><p><strong>{}</strong><br/>{}<br/>{}</p><p><a href="{}">{}</a></p>').format(
            _('Your approval is required.'), request.document_name or request.name,
            request.button_label, _('Approval Level: %s', self.level), url, _('Open Document'))

    def _notify_approvers(self):
        """Called after a signed level transition, in the same transaction."""
        self.ensure_one()
        if self.state != 'pending':
            return
        request = self.request_id
        company = request.company_id
        send_web = company.dam_web_notify and not self.notified_at
        send_email = company.dam_email_notify and not self.email_notified_at
        if not send_web and not send_email:
            return
        users = self.user_ids.filtered(lambda user: user.active and not user.share
                                       and request.company_id in user.company_ids)
        # Apply the same document access checks to both delivery channels.
        document = self.env[request.model_name].browse(request.res_id).exists()
        if not document:
            return
        eligible = users.browse()
        for user in users:
            try:
                document.with_user(user).with_context(allowed_company_ids=company.ids).check_access('read')
            except AccessError:
                continue
            eligible |= user
        users = eligible
        if not users:
            return
        message = self._get_approval_notification_message()
        action = {
            'type': 'ir.actions.act_window', 'name': request.document_name or request.name,
            'res_model': request.model_name, 'res_id': request.res_id,
            'views': [(False, 'form')], 'view_mode': 'form', 'target': 'current',
            'context': {'allowed_company_ids': [request.company_id.id],
                        'params': {'button_name': _('Open Document')}},
        }
        if send_web:
            delivery = users._push_notify(
                message, title=_('Approval Required'), notification_type='info',
                duration=60000, sticky=False, popup=True, inbox=True,
                action=action, company=company)
            if delivery:
                self.sudo().write({'notified_at': fields.Datetime.now()})
        if send_email:
            self._queue_approval_emails(users)

    def _signature_data(self):
        self.ensure_one()
        signature = self.with_context(bin_size=False).signature
        return signature.decode('ascii') if isinstance(signature, bytes) else signature or ''


class ApprovalEvent(models.Model):
    _name = 'dam.event'
    _description = 'Dynamic Approval History'
    _order = 'id desc'

    request_id = fields.Many2one('dam.request', required=True, ondelete='cascade', index=True)
    company_id = fields.Many2one(related='request_id.company_id', store=True)
    event_at = fields.Datetime(required=True, default=fields.Datetime.now)
    user_id = fields.Many2one('res.users', required=True)
    level = fields.Integer()
    kind = fields.Selection([(key, label) for key, label in [
        ('requested', 'Requested'), ('approved', 'Approved'), ('superseded', 'Superseded'),
        ('released', 'Wizard Opened'), ('executed', 'Executed'), ('rejected', 'Rejected'), ('reset', 'Reset')]], required=True)
    message = fields.Char(required=True)
    step_id = fields.Many2one('dam.step', ondelete='set null')
    signature = fields.Binary(related='step_id.signature')
    remark = fields.Text(related='step_id.remark')


class ApprovalLock(models.Model):
    _name = 'dam.lock'
    _description = 'Internal Approval Serialization Lock'
    _log_access = False

    model_name = fields.Char(required=True)
    res_id = fields.Integer(required=True)
    method_name = fields.Char(required=True)
    revision = fields.Integer(default=0)

    _sql_constraints = [('document_button_unique', 'unique(model_name, res_id, method_name)',
                         'A serialization lock must be unique per document button.')]
