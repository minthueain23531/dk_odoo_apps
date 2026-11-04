from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestTransactionLock(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.company.write({name: False for name in (
            'fiscalyear_lock_date', 'sale_lock_date', 'purchase_lock_date', 'tax_lock_date', 'hard_lock_date')})
        cls.partner = cls.env['res.partner'].create({'name': 'Lock test customer'})
        cls.model = cls.env['ir.model']._get('sale.order')
        cls.rule_vals = {
            'name': 'Sales transaction test', 'company_id': cls.company.id,
            'model_id': cls.model.id,
            'date_field_id': cls.env['ir.model.fields']._get('sale.order', 'date_order').id,
            'company_field_id': cls.env['ir.model.fields']._get('sale.order', 'company_id').id,
            'timezone': 'Asia/Rangoon',
        }

    def setUp(self):
        super().setUp()
        # Rolling back the rule rows does not undo registry method assignments.
        original_attributes = {model: dict(vars(model)) for model in self.env.registry.values()}
        self.addCleanup(self._restore_lock_methods, original_attributes)
        self.order = self._order('2026-09-30 12:00:00')
        self.rule = self.env['transaction.lock.rule'].create(self.rule_vals)

    @staticmethod
    def _restore_lock_methods(original_attributes):
        for model, originals in original_attributes.items():
            for name, method in list(vars(model).items()):
                if getattr(method, '_transaction_lock_key', None) != (model._name, name):
                    continue
                if name in originals:
                    setattr(model, name, originals[name])
                else:
                    delattr(model, name)

    def _order(self, date='2026-10-02 12:00:00', **extra):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'date_order': date, **extra,
        })

    def _lock(self, field='fiscalyear_lock_date', value='2026-10-01'):
        self.company.write({field: value})

    def _exception(self, user=None, end=None, field='fiscalyear_lock_date'):
        return self.env['account.lock_exception'].create({
            'company_id': self.company.id, 'user_id': user.id if user else False,
            'lock_date_field': field, 'lock_date': '2026-09-01',
            'end_datetime': end or fields.Datetime.now() + timedelta(minutes=5),
            'reason': 'Transaction lock integration test',
        })

    def test_inclusive_and_open_period(self):
        self._lock()
        with self.assertRaises(UserError):
            self.order.write({'client_order_ref': 'blocked'})
        with self.assertRaises(UserError):
            self._order('2026-10-01 00:00:00')
        opened = self._order()
        opened.write({'client_order_ref': 'allowed'})
        self.assertEqual(opened.client_order_ref, 'allowed')

    def test_create_batch_rollback(self):
        self._lock()
        before = self.env['sale.order'].search_count([('partner_id', '=', self.partner.id)])
        with self.assertRaises(UserError):
            self.env['sale.order'].create([
                {'partner_id': self.partner.id, 'date_order': '2026-10-02 00:00:00'},
                {'partner_id': self.partner.id, 'date_order': '2026-09-30 00:00:00'},
            ])
        self.assertEqual(before, self.env['sale.order'].search_count([('partner_id', '=', self.partner.id)]))

    def test_date_change_both_directions(self):
        opened = self._order()
        self._lock()
        with self.assertRaises(UserError):
            self.order.write({'date_order': '2026-10-03 00:00:00'})
        with self.assertRaises(UserError):
            opened.write({'date_order': '2026-09-30 00:00:00'})
        self.assertEqual(opened.date_order, fields.Datetime.to_datetime('2026-10-02 12:00:00'))

    def test_delete_and_archive_rule(self):
        self._lock()
        with self.assertRaises(UserError):
            self.order.unlink()
        self.rule.active = False
        self.order.write({'client_order_ref': 'allowed'})
        self.order.unlink()

    def test_mixed_recordset_is_atomic(self):
        opened = self._order()
        self._lock()
        with self.assertRaises(UserError):
            (opened | self.order).write({'client_order_ref': 'blocked batch'})
        self.assertFalse(opened.client_order_ref)

    def test_action_only_direct_call(self):
        self.rule.write({'lock_all_fields': False, 'prevent_create': False, 'prevent_delete': False})
        self.env['transaction.lock.action'].create({
            'rule_id': self.rule.id, 'name': 'Cancel', 'method_name': 'action_cancel', 'blocked': True})
        self._lock()
        with self.assertRaises(UserError):
            self.order.action_cancel()
        self.order.client_order_ref = 'still editable'
        self.assertEqual(self.order.state, 'draft')

    def test_purchase_create_edit_allowed_action_still_blocked(self):
        rule = self.env['transaction.lock.rule'].create({
            'name': 'Purchase action only', 'company_id': self.company.id,
            'model_id': self.env['ir.model']._get_id('purchase.order'),
            'date_field_id': self.env['ir.model.fields']._get('purchase.order', 'date_order').id,
            'company_field_id': self.env['ir.model.fields']._get('purchase.order', 'company_id').id,
            'prevent_create': False, 'lock_all_fields': False, 'prevent_delete': True,
        })
        self.env['transaction.lock.action'].create({
            'rule_id': rule.id, 'name': 'Confirm', 'method_name': 'button_confirm', 'blocked': True})
        self.env['transaction.lock.binding'].create({
            'rule_id': rule.id, 'model_id': self.env['ir.model']._get_id('purchase.order.line'),
            'parent_link': 'order_id'})
        self._lock()
        purchase = self.env['purchase.order'].create({
            'partner_id': self.partner.id, 'company_id': self.company.id,
            'date_order': '2026-09-30 12:00:00',
            'order_line': [Command.create({'display_type': 'line_note', 'name': 'Original note',
                                            'product_qty': 0.0})],
        })
        purchase.write({'partner_ref': 'Editable', 'date_order': '2026-09-29 12:00:00',
                        'company_id': self.company.id})
        purchase.order_line.write({'name': 'Editable detail'})
        self.assertEqual(purchase.partner_ref, 'Editable')
        self.assertEqual(purchase.order_line.name, 'Editable detail')
        with self.assertRaises(UserError):
            purchase.button_confirm()
        with self.assertRaises(UserError):
            purchase.unlink()
        purchase.date_order = '2026-10-02 12:00:00'
        purchase.date_order = '2026-09-30 12:00:00'
        with self.assertRaises(UserError):
            purchase.button_confirm()
        rule.lock_all_fields = True
        with self.assertRaises(UserError):
            purchase.partner_ref = 'Now blocked'
        rule.prevent_create = True
        with self.assertRaises(UserError):
            self.env['purchase.order'].create({
                'partner_id': self.partner.id, 'company_id': self.company.id,
                'date_order': '2026-09-30 12:00:00'})

    def test_automation_refresh_preserves_lock_checks(self):
        automation = self.env['base.automation'].create({
            'name': 'TLC compatibility', 'model_id': self.model.id,
            'trigger': 'on_create_or_write',
            'action_server_ids': [Command.create({
                'name': 'TLC no-op', 'model_id': self.model.id,
                'state': 'code', 'code': 'log("TLC automation ran")',
            })],
        })
        opened = self._order()
        opened.client_order_ref = 'automation and custom wrappers both work'
        self._lock()
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'blocked after automation creation'
        automation.active = False
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'blocked after automation refresh'

    def test_action_toggle_immediate(self):
        action = self.env['transaction.lock.action'].create({
            'rule_id': self.rule.id, 'name': 'Lock Order', 'method_name': 'action_lock', 'blocked': True})
        self.rule.write({'lock_all_fields': False, 'prevent_create': False})
        self._lock()
        with self.assertRaises(UserError):
            self.order.action_lock()
        action.blocked = False
        self.order.action_lock()
        self.assertTrue(self.order.locked)

    def test_categories_and_overlapping_locks(self):
        for flag, field in [('lock_sales', 'sale_lock_date'),
                            ('lock_purchases', 'purchase_lock_date'), ('lock_tax', 'tax_lock_date')]:
            self._lock(field)
            self.order.client_order_ref = 'category not selected'
            self.rule[flag] = True
            with self.assertRaises(UserError):
                self.order.client_order_ref = 'selected'
            self.rule[flag] = False
            self.company[field] = False
        self._lock(value='2026-09-01')
        self.rule.lock_sales = True
        self._lock('sale_lock_date')
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'sales later than global'

    def test_exception_and_expiry_same_environment(self):
        self._lock()
        exception = self._exception()
        self.order.client_order_ref = 'exception active'
        # No new environment and no custom cache clearing: expiry must be checked live.
        exception.end_datetime = fields.Datetime.now() - timedelta(minutes=1)
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'expired'

    def test_exception_does_not_override_other_locks(self):
        self._lock()
        self._exception()
        self.rule.lock_purchases = True
        self._lock('purchase_lock_date')
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'other lock'

    def test_hard_lock_ignores_exception(self):
        self._lock()
        self._exception()
        self._lock('hard_lock_date')
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'hard lock'

    def test_exception_for_current_user_only(self):
        self._lock()
        other = self.env['res.users'].with_context(no_reset_password=True).create({
            'name': 'Transaction user', 'login': 'transaction-lock-test',
            'company_id': self.company.id, 'company_ids': [Command.set(self.company.ids)],
            'groups_id': [Command.set([self.env.ref('sales_team.group_sale_salesman_all_leads').id,
                                       self.env.ref('account.group_account_user').id])],
        })
        self._exception(user=self.env.user)
        self.order.client_order_ref = 'me allowed'
        with self.assertRaises(UserError):
            self.order.with_user(other).write({'client_order_ref': 'other blocked'})
        with self.assertRaises(AccessError):
            self.rule.with_user(other).write({'active': False})

    def test_detail_crud_and_reparent(self):
        self.env['transaction.lock.binding'].create({
            'rule_id': self.rule.id, 'model_id': self.env['ir.model']._get_id('sale.order.line'),
            'parent_link': 'order_id'})
        line = self.env['sale.order.line'].create({
            'order_id': self.order.id, 'display_type': 'line_note', 'name': 'original'})
        opened = self._order()
        open_line = self.env['sale.order.line'].create({
            'order_id': opened.id, 'display_type': 'line_note', 'name': 'open'})
        self._lock()
        with self.assertRaises(UserError):
            line.name = 'blocked'
        with self.assertRaises(UserError):
            line.unlink()
        with self.assertRaises(UserError):
            self.env['sale.order.line'].create({
                'order_id': self.order.id, 'display_type': 'line_note', 'name': 'blocked'})
        with self.assertRaises(UserError):
            line.order_id = opened
        with self.assertRaises(UserError):
            open_line.order_id = self.order
        self.assertEqual(open_line.order_id, opened)

    def test_company_scope_and_changed_company(self):
        other_company = self.env['res.company'].create({'name': 'Other lock company'})
        self._lock()
        opened = self._order('2026-09-30 12:00:00', company_id=other_company.id)
        opened.client_order_ref = 'other company permitted'
        # Changing company into the locked rule is also blocked and rolled back.
        with self.assertRaises(UserError):
            opened.company_id = self.company
        self.assertEqual(opened.company_id, other_company)

    def test_parent_company_lock(self):
        child = self.env['res.company'].create({'name': 'Child lock company', 'parent_id': self.company.id})
        self.rule.company_id = child
        order = self._order('2026-09-30 12:00:00', company_id=child.id)
        self._lock()
        with self.assertRaises(UserError):
            order.client_order_ref = 'inherited lock'

    def test_record_filter(self):
        self.order.client_order_ref = 'restricted'
        self.rule.record_domain = "[('client_order_ref', '=', 'restricted')]"
        self._lock()
        other = self._order('2026-09-30 12:00:00', client_order_ref='outside')
        other.note = 'allowed'
        with self.assertRaises(UserError):
            self.order.client_order_ref = 'escape'
        with self.assertRaises(UserError):
            other.client_order_ref = 'restricted'
        self.assertEqual(other.client_order_ref, 'outside')

    def test_timezone_boundary(self):
        self._lock()
        # 18:00 UTC = next calendar day at 00:30 in Yangon.
        self._order('2026-10-01 18:00:00')
        with self.assertRaises(UserError):
            self._order('2026-10-01 17:00:00')

    def test_missing_date_policy_on_stock(self):
        picking_type = self.env['stock.picking.type'].search([
            ('company_id', '=', self.company.id), ('code', '=', 'internal')], limit=1)
        if not picking_type:
            location = self.env['stock.location'].create({'name': 'TLC stock', 'usage': 'internal'})
            picking_type = self.env['stock.picking.type'].create({
                'name': 'TLC internal', 'code': 'internal', 'sequence_code': 'TLC',
                'company_id': self.company.id, 'default_location_src_id': location.id,
                'default_location_dest_id': location.id,
            })
        picking = self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'location_id': picking_type.default_location_src_id.id,
            'location_dest_id': picking_type.default_location_dest_id.id,
        })
        rule = self.env['transaction.lock.rule'].create({
            'name': 'Stock missing date', 'company_id': self.company.id,
            'model_id': self.env['ir.model']._get_id('stock.picking'),
            'date_field_id': self.env['ir.model.fields']._get('stock.picking', 'date_done').id,
            'company_field_id': self.env['ir.model.fields']._get('stock.picking', 'company_id').id,
        })
        self._lock()
        with self.assertRaises(UserError):
            picking.origin = 'missing date blocked'
        rule.missing_date_policy = 'allow'
        picking.origin = 'missing date allowed'
        self.assertEqual(picking.origin, 'missing date allowed')

    def test_exempt_field(self):
        self.rule.exempt_field_ids = self.env['ir.model.fields']._get('sale.order', 'client_order_ref')
        self._lock()
        self.order.client_order_ref = 'allowed exception field'
        with self.assertRaises(UserError):
            self.order.note = 'blocked business field'
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            self.rule.exempt_field_ids = self.rule.date_field_id

    def test_discover_is_idempotent(self):
        self.rule.action_discover()
        actions, bindings = self.rule.action_ids.ids, self.rule.binding_ids.ids
        self.rule.action_discover()
        self.assertEqual(actions, self.rule.action_ids.ids)
        self.assertEqual(bindings, self.rule.binding_ids.ids)
        self.assertTrue(self.rule.action_ids.filtered(lambda a: a.method_name == 'action_confirm'))
        self.assertFalse(any(self.rule.action_ids.mapped('blocked')))

    def test_discover_skips_self_referencing_details(self):
        if 'project.task' not in self.env.registry:
            self.skipTest('Project is not installed')
        rule = self.env['transaction.lock.rule'].create({
            'name': 'Project self-reference regression', 'company_id': self.company.id,
            'model_id': self.env['ir.model']._get_id('project.task'),
            'date_field_id': self.env['ir.model.fields']._get('project.task', 'date_deadline').id,
            'company_field_id': self.env['ir.model.fields']._get('project.task', 'company_id').id,
        })
        rule.action_discover()
        self.assertFalse(rule.binding_ids.filtered(lambda binding: binding.model_id == rule.model_id))
        actions, bindings = rule.action_ids.ids, rule.binding_ids.ids
        rule.action_discover()
        self.assertEqual(actions, rule.action_ids.ids)
        self.assertEqual(bindings, rule.binding_ids.ids)

    def test_default_accounting_lock_still_applies(self):
        journal = self.env['account.journal'].create({
            'name': 'Lock test journal', 'code': 'TLC', 'type': 'general', 'company_id': self.company.id})
        accounts = self.env['account.account'].search([('company_ids', 'in', self.company.ids)], limit=2)
        if len(accounts) < 2:
            accounts = self.env['account.account'].create([
                {'name': 'TLC debit', 'code': 'TLC001', 'account_type': 'asset_current'},
                {'name': 'TLC credit', 'code': 'TLC002', 'account_type': 'liability_current'}])
        move = self.env['account.move'].create({
            'journal_id': journal.id, 'date': '2026-09-30',
            'line_ids': [Command.create({'name': 'Debit', 'account_id': accounts[0].id, 'debit': 10}),
                         Command.create({'name': 'Credit', 'account_id': accounts[1].id, 'credit': 10})]})
        move.action_post()
        self._lock()
        with self.assertRaises(UserError):
            move.button_draft()
