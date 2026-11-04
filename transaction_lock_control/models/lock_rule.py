import ast
import re

from lxml import etree

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def allowed_model(model):
    """Do not permit rules on the infrastructure used to evaluate the rules."""
    return (
        model is not None and not model._abstract and not model._transient
        and not model._name.startswith(('ir.', 'base.', 'mail.', 'bus.', 'res.',
                                        'transaction.lock.', 'account.lock'))
    )


class TransactionLockRule(models.Model):
    _name = 'transaction.lock.rule'
    _description = 'Transaction Lock Rule'
    _order = 'company_id, name, id'

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one('res.company', required=True, index=True, default=lambda self: self.env.company)
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade',
                               domain=[('transient', '=', False)])
    model_name = fields.Char(related='model_id.model', store=True, index=True, string='Technical Model')
    date_field_id = fields.Many2one('ir.model.fields', required=True, ondelete='cascade',
                                   string='Target Date Field')
    company_field_id = fields.Many2one('ir.model.fields', required=True, ondelete='cascade',
                                      string='Record Company Field')
    record_domain = fields.Char(default='[]', required=True, string='Record Filter',
                                help='Static Odoo domain, for example [("picking_type_code", "=", "incoming")].')
    lock_sales = fields.Boolean(string='Sales')
    lock_purchases = fields.Boolean(string='Purchases')
    lock_tax = fields.Boolean(string='Tax')
    lock_all_fields = fields.Boolean(
        default=True, string='Prevent Edit',
        help='Block edits to locked records and configured detail lines, except Exempt Fields. '
             'When unchecked, edits including date, company and record-filter fields are allowed; '
             'selected actions still check the current record values.')
    exempt_field_ids = fields.Many2many('ir.model.fields', string='Exempt Fields',
                                       help='Explicit exceptions to field edit restrictions. Date/company/filter fields cannot be exempt.')
    prevent_create = fields.Boolean(default=True)
    prevent_delete = fields.Boolean(default=True)
    missing_date_policy = fields.Selection([('block', 'Block'), ('allow', 'Allow')],
                                           default='block', required=True,
                                           help='Behavior when any applicable lock is active but the target date is empty.')
    timezone = fields.Selection(selection=lambda self: self.env['res.partner']._fields['tz']._description_selection(self.env),
                                default='Asia/Rangoon', required=True,
                                help='Convert Datetime targets to a calendar date in this timezone, consistently for every user.')
    action_ids = fields.One2many('transaction.lock.action', 'rule_id', string='Actions')
    binding_ids = fields.One2many('transaction.lock.binding', 'rule_id', string='Detail Models')

    @api.onchange('model_id')
    def _onchange_model_id(self):
        self.date_field_id = False
        self.company_field_id = self.env['ir.model.fields'].search([
            ('model_id', '=', self.model_id.id), ('name', '=', 'company_id'),
            ('relation', '=', 'res.company'), ('ttype', '=', 'many2one')], limit=1)
        self.exempt_field_ids = False
        self.action_ids = False
        self.binding_ids = False

    def _domain(self, validate=False):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.record_domain or '[]')
            if not isinstance(domain, list):
                raise ValueError('Expected a list')
            # Compile, but never execute an arbitrary expression supplied in a rule.
            if validate:
                self.env[self.model_name].sudo()._where_calc(domain)
            return domain
        except (ValueError, SyntaxError, TypeError, KeyError) as exc:
            raise ValidationError(_('Invalid record filter: %s', str(exc))) from exc

    def _protected_fields(self):
        return {self.date_field_id.name, self.company_field_id.name} | {
            term[0].split('.')[0] for term in self._domain()
            if isinstance(term, (list, tuple)) and len(term) == 3
        }

    @api.constrains('model_id', 'date_field_id', 'company_field_id', 'record_domain', 'exempt_field_ids')
    def _check_configuration(self):
        for rule in self:
            model = self.env.get(rule.model_name)
            if not allowed_model(model):
                raise ValidationError(_('Choose a persistent business transaction model.'))
            if rule.date_field_id.model_id != rule.model_id or rule.date_field_id.ttype not in ('date', 'datetime'):
                raise ValidationError(_('Target Date Field must be a Date/Datetime field on the selected model.'))
            if (rule.company_field_id.model_id != rule.model_id
                    or rule.company_field_id.ttype != 'many2one'
                    or rule.company_field_id.relation != 'res.company'):
                raise ValidationError(_('Record Company Field must reference res.company on the selected model.'))
            rule._domain(validate=True)
            protected = rule._protected_fields()
            for field in rule.exempt_field_ids:
                if field.model_id != rule.model_id or field.name in protected:
                    raise ValidationError(_('Exempt fields must belong to the model and cannot include date, company or filter fields.'))
            # Changing a model must not leave previously configured mappings attached.
            rule.action_ids._check_method()
            rule.binding_ids._check_path()

    def action_discover(self):
        """Discover labels/methods, not arbitrary executable Python supplied by users."""
        self.ensure_one()
        model = self.env[self.model_name]
        existing = set(self.action_ids.mapped('method_name'))
        action_values = []
        for view in self.env['ir.ui.view'].search([('model', '=', self.model_name), ('active', '=', True)]):
            try:
                arch = etree.fromstring(view.arch_db.encode())
            except (etree.XMLSyntaxError, AttributeError):
                continue
            for button in arch.xpath('.//button[@type="object"]'):
                method = button.get('name')
                if (method and method not in existing and not method.startswith('_')
                        and callable(getattr(model, method, None))
                        and not getattr(getattr(model, method), '_api', None)
                        and method not in ('create', 'write', 'unlink')):
                    action_values.append({
                        'rule_id': self.id, 'name': button.get('string') or method,
                        'method_name': method, 'blocked': False,
                    })
                    existing.add(method)
        if action_values:
            self.env['transaction.lock.action'].create(action_values)
        binding_values = []
        existing_bindings = {(b.model_id.id, b.parent_link) for b in self.binding_ids}
        for field in model._fields.values():
            child = self.env.get(field.comodel_name) if field.type == 'one2many' else None
            # Self-referencing children (e.g. project.task.child_ids) are already
            # evaluated as main-model records. They are not valid detail bindings.
            if (allowed_model(child) and child._name != model._name
                    and field.inverse_name in child._fields
                    and child._fields[field.inverse_name].type == 'many2one'):
                child_model_id = self.env['ir.model']._get_id(child._name)
                key = (child_model_id, field.inverse_name)
                if key not in existing_bindings:
                    binding_values.append({
                        'rule_id': self.id, 'model_id': child_model_id,
                        'parent_link': field.inverse_name, 'enabled': True,
                    })
                    existing_bindings.add(key)
        if binding_values:
            self.env['transaction.lock.binding'].create(binding_values)
        return True

    def _refresh_patches(self):
        if self.env.registry.ready:
            self.env['transaction.lock.rule']._register_hook()
            # Standard Odoo registry signalling (same approach as base_automation).
            self.env.registry.registry_invalidated = True

    @api.model_create_multi
    def create(self, vals_list):
        rules = super().create(vals_list)
        rules._refresh_patches()
        return rules

    def write(self, vals):
        result = super().write(vals)
        # Non-structural settings are read fresh by the checker. They do not
        # require a registry reload in this worker or every other worker.
        if {'active', 'model_id'} & vals.keys():
            self._refresh_patches()
        return result

    def unlink(self):
        result = super().unlink()
        self._refresh_patches()
        return result


class TransactionLockAction(models.Model):
    _name = 'transaction.lock.action'
    _description = 'Transaction Lock Action'
    _order = 'name, id'

    rule_id = fields.Many2one('transaction.lock.rule', required=True, ondelete='cascade')
    name = fields.Char(required=True, string='Button Label')
    method_name = fields.Char(required=True, string='Object Method')
    blocked = fields.Boolean(default=False, string='Prevent Action')

    _sql_constraints = [('unique_rule_method', 'unique(rule_id, method_name)', 'This method is already mapped.')]

    @api.constrains('rule_id', 'method_name')
    def _check_method(self):
        for action in self:
            name = action.method_name
            model = self.env.get(action.rule_id.model_name)
            method = getattr(model, name, None) if model is not None else None
            if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*', name or '')
                    or name in ('create', 'write', 'unlink') or not callable(method)
                    or getattr(method, '_api', None)):
                raise ValidationError(_('Choose a public record method on the rule model. Model-level methods and CRUD are not action mappings.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.rule_id._refresh_patches()
        return records

    def write(self, vals):
        rules = self.rule_id
        result = super().write(vals)
        if {'rule_id', 'method_name', 'blocked'} & vals.keys():
            (rules | self.rule_id)._refresh_patches()
        return result

    def unlink(self):
        rules = self.rule_id
        result = super().unlink()
        rules._refresh_patches()
        return result


class TransactionLockBinding(models.Model):
    _name = 'transaction.lock.binding'
    _description = 'Transaction Lock Detail Binding'

    rule_id = fields.Many2one('transaction.lock.rule', required=True, ondelete='cascade')
    model_id = fields.Many2one('ir.model', required=True, ondelete='cascade')
    model_name = fields.Char(related='model_id.model', store=True, index=True, string='Technical Model')
    parent_link = fields.Char(required=True,
                              help='Many2one path to the transaction, e.g. picking_id or line_id.packing_lists_id.')
    enabled = fields.Boolean(default=True)

    _sql_constraints = [('unique_rule_path', 'unique(rule_id, model_id, parent_link)', 'This detail path is already mapped.')]

    @api.constrains('rule_id', 'model_id', 'parent_link')
    def _check_path(self):
        for binding in self:
            model = self.env.get(binding.model_name)
            if not allowed_model(model) or binding.model_id == binding.rule_id.model_id:
                raise ValidationError(_('Choose a different persistent detail model.'))
            parts = (binding.parent_link or '').split('.')
            if len(parts) > 4:
                raise ValidationError(_('A detail path can contain at most four Many2one fields.'))
            for name in parts:
                field = model._fields.get(name)
                if field is None or field.type != 'many2one':
                    raise ValidationError(_('Every step of the parent path must be a Many2one field.'))
                model = self.env[field.comodel_name]
            if model._name != binding.rule_id.model_name:
                raise ValidationError(_('The parent path must end at the rule model.'))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records.rule_id._refresh_patches()
        return records

    def write(self, vals):
        rules = self.rule_id
        result = super().write(vals)
        if {'rule_id', 'model_id', 'parent_link', 'enabled'} & vals.keys():
            (rules | self.rule_id)._refresh_patches()
        return result

    def unlink(self):
        rules = self.rule_id
        result = super().unlink()
        rules._refresh_patches()
        return result
