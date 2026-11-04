from datetime import date
from functools import wraps

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError


def make_wrapper(origin, operation):
    """Registry-local extension, preserving the complete original method chain."""
    if operation == 'create':
        @api.model_create_multi
        def wrapped(records, vals_list, **kwargs):
            # A failure rolls back the entire batch, even when the caller catches it.
            with records.env.cr.savepoint():
                result = wrapped.origin(records, vals_list, **kwargs)
                records.env['transaction.lock.rule']._check_operation(result, 'create')
            return result
    elif operation == 'write':
        @wraps(origin)
        def wrapped(records, vals, **kwargs):
            checker = records.env['transaction.lock.rule']
            checker._check_operation(records, 'write', vals)
            with records.env.cr.savepoint():
                result = wrapped.origin(records, vals, **kwargs)
                # Includes moving an open record into a closed period/company/scope.
                checker._check_operation(records, 'write', vals)
            return result
    elif operation == 'unlink':
        @wraps(origin)
        def wrapped(records, **kwargs):
            records.env['transaction.lock.rule']._check_operation(records, 'unlink')
            return wrapped.origin(records, **kwargs)
    else:
        @wraps(origin)
        def wrapped(records, *args, **kwargs):
            records.env['transaction.lock.rule']._check_operation(records, operation)
            with records.env.cr.savepoint():
                return wrapped.origin(records, *args, **kwargs)
    wrapped.origin = origin
    return wrapped


class TransactionLockEnforcement(models.Model):
    _inherit = 'transaction.lock.rule'

    def _register_hook(self):
        result = super()._register_hook()
        rules = self.sudo().with_context(active_test=True).search([])
        methods = {}
        for rule in rules:
            methods.setdefault(rule.model_name, set()).update(('create', 'write', 'unlink'))
            methods[rule.model_name].update(rule.action_ids.filtered('blocked').mapped('method_name'))
            for binding in rule.binding_ids.filtered('enabled'):
                methods.setdefault(binding.model_name, set()).update(('create', 'write', 'unlink'))
        for model_name, names in methods.items():
            if model_name not in self.env.registry:
                continue
            cls = self.env.registry[model_name]
            for name in names:
                origin = getattr(cls, name, None)
                if not callable(origin):
                    continue
                key = (model_name, name)
                # Other addons may also wrap methods. Never delete their wrappers.
                current = origin
                found = False
                seen = set()
                while current is not None and id(current) not in seen:
                    seen.add(id(current))
                    if getattr(current, '_transaction_lock_key', None) == key:
                        found = True
                        break
                    current = getattr(current, 'origin', None)
                if not found:
                    method = make_wrapper(origin, name)
                    method._transaction_lock_key = key
                    setattr(cls, name, method)
        return result

    def _effective_locks(self, company):
        """Evaluate exceptions afresh using Odoo's standard transaction timestamp."""
        self.ensure_one()
        company = company.with_context(ignore_exceptions=False)
        names = ['fiscalyear_lock_date']
        if self.lock_sales:
            names.append('sale_lock_date')
        if self.lock_purchases:
            names.append('purchase_lock_date')
        if self.lock_tax:
            names.append('tax_lock_date')
        # Odoo's helper handles current user, everyone, expiry and parent companies.
        locks = [(company._get_user_lock_date(name), name) for name in names]
        locks.append((max((c.hard_lock_date or date.min for c in company.sudo().parent_ids),
                          default=date.min), 'hard_lock_date'))
        return [(value, name) for value, name in locks if value > date.min]

    def _check_records(self, records, operation):
        self.ensure_one()
        # sudo only inspects scope/date/company. Original CRUD/action still runs
        # with the original environment and ACLs. sudo preserves the actor uid.
        # Every record in this rule shares its company and category selection.
        # Resolve exceptions once per rule/batch, never once per record. Keep this
        # local to the check so expiry/configuration changes cannot go stale.
        company = self.company_id
        locks = self._effective_locks(company)
        if not locks:
            return
        highest_lock = max(value for value, _name in locks)
        target_field = self.date_field_id.name
        is_datetime = self.date_field_id.ttype == 'datetime'
        timezone = pytz.timezone(self.timezone) if is_datetime else None
        scoped = records.sudo().filtered_domain([
            (self.company_field_id.name, '=', company.id), *self._domain()])
        for record in scoped:
            target = record[target_field]
            if target and is_datetime:
                target = pytz.UTC.localize(fields.Datetime.to_datetime(target)).astimezone(
                    timezone).date()
            elif target:
                target = fields.Date.to_date(target)
            if not target and self.missing_date_policy == 'allow':
                continue
            if target and target > highest_lock:
                continue
            violations = [(value, name) for value, name in locks if not target or target <= value]
            if violations:
                raise UserError(_(
                    'Transaction locked by rule "%(rule)s".\n'
                    'Operation: %(operation)s\nRecord: %(record)s\n'
                    'Target date: %(target)s\nApplicable locks: %(locks)s',
                    rule=self.name, operation=operation, record=record.display_name,
                    target=target or _('Missing'), locks=company._format_lock_dates(violations)))

    @api.model
    def _check_operation(self, records, operation, vals=None):
        if not records:
            return
        vals = vals or {}
        changed_fields = set(vals)
        # Read definitions as sudo so transaction users need no access to setup.
        # Do not sudo the company lock evaluation to a different user.
        rules = self.sudo().with_context(active_test=True).search([('model_name', '=', records._name)])
        for rule in rules:
            should_check = (
                operation == 'create' and rule.prevent_create
                or operation == 'unlink' and rule.prevent_delete
                or operation == 'write' and (
                    rule.lock_all_fields and bool(changed_fields - set(rule.exempt_field_ids.mapped('name')))
                )
                or operation not in ('create', 'write', 'unlink')
                and operation in rule.action_ids.filtered('blocked').mapped('method_name')
            )
            if should_check:
                rule._check_records(records, operation)
        if operation not in ('create', 'write', 'unlink'):
            return
        bindings = self.env['transaction.lock.binding'].sudo().search([
            ('model_name', '=', records._name), ('enabled', '=', True), ('rule_id.active', '=', True)])
        for binding in bindings:
            # All-fields protection includes adding/deleting/reparenting lines.
            if binding.rule_id.lock_all_fields:
                parents = records.sudo().mapped(binding.parent_link)
                binding.rule_id._check_records(parents, _('Detail %s', operation))
