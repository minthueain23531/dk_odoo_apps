import base64
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from ..models.automatic import MigrationStopped


@tagged('post_install', '-at_install')
class MigrationTests(TransactionCase):
    """Run with Odoo --test-tags /payment_approval_matrix_migration on a test copy."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env.user.write({'groups_id': [Command.link(cls.env.ref(
            'dynamic_approval_matrix.group_dam_manager').id),
            Command.link(cls.env.ref('base.group_system').id)]})
        cls.company = cls.env['res.company'].create({'name': 'Isolated Approval Migration Test'})
        cls.env.user.write({'company_ids': [Command.link(cls.company.id)]})
        cls.approver = cls.env.ref('base.user_admin')
        cls.approver.write({'company_ids': [Command.link(cls.company.id)]})
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=cls.company.ids))
        cls.batch = cls.env['pam.migration.batch'].search([('company_id', '=', cls.company.id)], limit=1)
        if not cls.batch:
            cls.batch = cls.env['pam.migration.batch'].create({'company_id': cls.company.id})
        cls.source = cls.env['payment.approval'].create({
            'model': 'account.payment', 'res_id': 987654321, 'level': 1, 'amount': 23.5,
            'action': 'self.env["account.payment"].browse(987654321).with_context(rollback=True).action_post()',
            'signature': base64.b64encode(b'original-signature-evidence'), 'remark': 'Preserve exactly',
            'company_id': cls.company.id})

    def test_staging_preserves_evidence_and_is_idempotent(self):
        before = self.env['dam.request'].sudo().search_count([])
        self.batch.action_stage()
        row = self.env['pam.migration.evidence'].search([('source_id', '=', self.source.id)])
        self.assertEqual(len(row), 1)
        self.assertEqual(row.signature, self.source.signature)
        self.assertEqual(row.remark, self.source.remark)
        self.assertEqual(row.actor_id, self.source.create_uid)
        self.assertEqual(row.legacy_created_at, self.source.create_date)
        self.assertEqual(row.decision, 'unknown')
        self.assertEqual(row.document_id.target_state, 'archive')
        self.assertEqual(row.document_id.method_name, 'action_post')
        self.batch.action_stage()
        self.assertEqual(self.env['pam.migration.evidence'].search_count([('source_id', '=', self.source.id)]), 1)
        self.assertEqual(self.env['dam.request'].sudo().search_count([]), before)

    def test_snapshot_cannot_be_rewritten(self):
        self.batch.action_stage()
        row = self.env['pam.migration.evidence'].search([('source_id', '=', self.source.id)])
        with self.assertRaises(UserError):
            row.write({'remark': 'Altered'})
        with self.assertRaises(UserError):
            row.document_id.action_import()

    def test_non_manager_cannot_stage(self):
        user = self.env['res.users'].create({
            'name': 'Migration ordinary user', 'login': 'pam_migration_test_user',
            'groups_id': [Command.set(self.env.ref('base.group_user').ids)],
            'company_id': self.company.id, 'company_ids': [Command.set(self.company.ids)]})
        with self.assertRaises(AccessError):
            self.batch.with_user(user).action_stage()

    def test_missing_source_configuration_stops_instead_of_reporting_completed(self):
        action = self.env['pam.migration.run'].open_dashboard()
        dashboard = self.env['pam.migration.run'].browse(action['res_id'])
        dashboard.action_run()
        evidence = self.env['pam.migration.evidence'].search([('source_id', '=', self.source.id)])
        self.assertFalse(evidence)
        self.assertEqual(dashboard.state, 'failed')
        self.assertIn('configurations are missing', dashboard.summary)
        self.assertTrue(dashboard.log_ids.filtered(lambda item: item.status == 'error'))
        dashboard.action_run()
        self.assertEqual(self.env['pam.migration.evidence'].search_count([('source_id', '=', self.source.id)]), 0)

    def test_no_usable_target_configuration_is_also_fatal(self):
        self.env['approval.level.matrix'].sudo().create({
            'ir_model_id': self.env['ir.model']._get_id('account.payment'),
            'company_id': self.company.id, 'active': False, 'type': 'none',
            'model_domain': "[('id', '=', -987654321)]"})
        dashboard = self.env['pam.migration.run'].browse(self.env['pam.migration.run'].open_dashboard()['res_id'])
        dashboard.action_run()
        self.assertEqual(dashboard.state, 'failed')
        self.assertIn('No usable Dynamic configuration', dashboard.summary)
        self.assertFalse(self.batch.config_ids)
        self.assertFalse(self.batch.document_ids)

    def test_failed_attempt_stops_immediately(self):
        action = self.env['pam.migration.run'].open_dashboard()
        dashboard = self.env['pam.migration.run'].browse(action['res_id'])
        def fail():
            dashboard.summary = 'This change must roll back'
            raise UserError('Deliberate per-record failure')
        with self.assertRaises(MigrationStopped):
            dashboard._attempt('test.source', 1, fail)
        self.assertNotEqual(dashboard.summary, 'This change must roll back')

    def test_outer_transaction_rolls_back_targets_and_preserves_error_log(self):
        action = self.env['pam.migration.run'].open_dashboard()
        dashboard = self.env['pam.migration.run'].browse(action['res_id'])
        def failing_run(run, batch):
            run.env['dam.request'].sudo().create({
                'name': 'atomic-test-target', 'model_name': 'account.payment', 'res_id': 987654321,
                'method_name': 'action_post', 'button_label': 'Test', 'company_id': self.company.id,
                'requested_by': self.env.uid, 'document_stamp': 'test', 'state': 'done'})
            run._log('success', 'test.source', 1, 'Must disappear on rollback')
            raise MigrationStopped('test.source', 2, 'Deliberate fatal error')
        with patch.object(type(dashboard), '_run_items', failing_run):
            dashboard.action_run()
        self.assertEqual(dashboard.state, 'failed')
        self.assertFalse(self.env['dam.request'].sudo().search([('name', '=', 'atomic-test-target')]))
        self.assertFalse(dashboard.log_ids.filtered(lambda row: row.message == 'Must disappear on rollback'))
        self.assertTrue(dashboard.log_ids.filtered(lambda row: row.message == 'Deliberate fatal error'))

    def _owned_import(self):
        dashboard = self.env['pam.migration.run'].browse(self.env['pam.migration.run'].open_dashboard()['res_id'])
        self.env['dam.button']._sync_catalog()
        button = self.env['dam.button'].sudo().search([
            ('model_name', '=', 'account.payment'), ('method_name', '=', 'action_post')], limit=1)
        rule = self.env['dam.rule'].sudo().create({
            'name': 'Rollback fixture', 'model_id': button.model_id.id, 'button_id': button.id,
            'company_id': self.company.id, 'approval_type': 'none', 'active': False,
            'line_ids': [Command.create({'level': 1, 'user_ids': [Command.set(self.approver.ids)]})]})
        source_config = self.env['approval.level.matrix'].sudo().create({
            'ir_model_id': button.model_id.id, 'company_id': self.company.id,
            'model_domain': "[('id', '=', -123456)]", 'type': 'none'})
        self.env['pam.migration.config'].create({'batch_id': self.batch.id, 'source_id': source_config.id,
            'model_name': 'account.payment', 'method_name': 'action_post', 'rule_id': rule.id})
        request = self.env['dam.request'].sudo().create({
            'name': 'Fixture — migrated legacy approval', 'rule_id': rule.id,
            'model_name': 'account.payment', 'res_id': 987654321, 'method_name': 'action_post',
            'button_label': 'Test', 'company_id': self.company.id, 'requested_by': self.env.uid,
            'document_stamp': 'test', 'state': 'done'})
        self.env['pam.migration.document'].create({'batch_id': self.batch.id, 'model_name': 'account.payment',
            'res_id': 987654321, 'method_name': 'action_post', 'target_state': 'done', 'request_id': request.id})
        dashboard.last_run = fields.Datetime.now()
        return dashboard, rule, request, source_config

    def test_failure_preserves_previous_imports(self):
        dashboard, rule, request, source = self._owned_import()
        dashboard.state = 'done'
        with patch.object(type(dashboard), '_run_items', side_effect=MigrationStopped('test', 1, 'Failure')):
            dashboard.action_run()
        self.assertEqual(dashboard.state, 'failed')
        self.assertTrue(rule.exists())
        self.assertTrue(request.exists())
        self.assertTrue(source.exists())

    def test_activation_does_not_change_legacy_controls(self):
        dashboard, rule, request, source = self._owned_import()
        source.active = True
        with patch.object(type(dashboard), '_run_items', return_value=(2, 0)):
            dashboard.action_run()
        self.assertEqual(dashboard.state, 'done')
        self.assertTrue(rule.active)
        self.assertTrue(source.active)

    def test_unrelated_target_rule_does_not_block_import(self):
        dashboard, existing, request, source = self._owned_import()
        source.write({'line_ids': [Command.create({'level': 1,
            'user_ids': [Command.set(self.approver.ids)]})]})
        row = self.batch.config_ids.filtered(lambda r: r.source_id == source)
        row.write({'rule_id': False})
        with patch.object(type(dashboard), '_attempt', wraps=dashboard._attempt):
            dashboard.action_run()
        self.assertEqual(dashboard.state, 'done', dashboard.summary)
        self.assertTrue(existing.exists())
        self.assertTrue(request.exists())
        self.assertTrue(row.rule_id)
        self.assertNotEqual(row.rule_id, existing)

    def test_manual_target_deletion_releases_migration_links(self):
        dashboard, rule, request, source = self._owned_import()
        request.unlink()
        rule.unlink()
        self.batch.document_ids.invalidate_recordset(['request_id'])
        self.batch.config_ids.invalidate_recordset(['rule_id'])
        self.assertFalse(self.batch.document_ids.mapped('request_id'))
        self.assertFalse(self.batch.config_ids.mapped('rule_id'))
