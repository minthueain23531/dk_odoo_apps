from unittest.mock import patch

from markupsafe import Markup

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.service.model import get_public_method
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestWebPushNotify(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.users = cls.env['res.users'].create([{
            'name': name, 'login': name,
            'groups_id': [Command.set([cls.env.ref('base.group_user').id])],
            'company_id': cls.env.company.id,
            'company_ids': [Command.set(cls.env.company.ids)],
            'notification_type': 'email',
        } for name in ('wpn_first_test', 'wpn_second_test')])

    def test_batch_inbox_no_email_even_with_email_preference(self):
        before = self.env['mail.mail'].search_count([])
        message = self.users._push_notify('Review needed', title='WPN test', popup=False)
        self.assertEqual(len(message), 1)
        self.assertEqual(message.partner_ids, self.users.partner_id)
        self.assertEqual(len(message.notification_ids), 2)
        self.assertEqual(set(message.notification_ids.mapped('notification_type')), {'inbox'})
        self.assertFalse(any(message.notification_ids.mapped('is_read')))
        self.assertEqual(self.env['mail.mail'].search_count([]), before)

    def test_html_sanitized_and_plain_text_escaped(self):
        text = '<script>alert(1)</script><b>Text</b>'
        plain = self.users._push_notify(text, popup=False)
        self.assertIn('&lt;script&gt;', plain.body)
        html = self.users._push_notify(Markup('<p onclick="evil()">Text<script>evil()</script></p>'), popup=False)
        self.assertNotIn('<script', html.body)
        self.assertNotIn('onclick', html.body)

    def test_popup_only_and_options(self):
        before = self.env['mail.message'].search_count([])
        with patch.object(type(self.env['bus.bus']), '_sendone') as send:
            result = self.users._push_notify('Hello', inbox=False, duration=15000, sticky=True)
        self.assertFalse(result)
        self.assertEqual(send.call_count, 2)
        partner, channel, payload = send.call_args.args
        self.assertEqual(channel, 'web_push_notify.notification')
        self.assertEqual(payload['duration'], 15000)
        self.assertTrue(payload['sticky'])
        self.assertEqual(self.env['mail.message'].search_count([]), before)

    def test_rollback_removes_inbox(self):
        before = self.env['mail.message'].search_count([('subject', '=', 'WPN rollback')])
        with self.assertRaises(UserError), self.env.cr.savepoint():
            self.users._push_notify('Discard', title='WPN rollback')
            raise UserError('Rollback')
        self.assertEqual(self.env['mail.message'].search_count([('subject', '=', 'WPN rollback')]), before)

    def test_private_api_not_rpc_callable(self):
        with self.assertRaises(AccessError):
            get_public_method(self.users, '_push_notify')

    def test_inactive_and_portal_recipients_skipped(self):
        self.users[0].active = False
        portal = self.env['res.users'].create({
            'name': 'WPN portal', 'login': 'wpn_portal_test',
            'groups_id': [Command.set([self.env.ref('base.group_portal').id])],
        })
        message = (self.users | portal)._push_notify('Internal only', popup=False)
        self.assertEqual(message.partner_ids, self.users[1].partner_id)

    def test_company_isolation(self):
        company = self.env['res.company'].create({'name': 'WPN other company'})
        message = self.users.with_company(company)._push_notify('Other company', popup=False)
        self.assertFalse(message)

    def test_record_action_checks_each_recipient_access(self):
        secret = self.env['ir.config_parameter'].create({'key': 'wpn.test.secret', 'value': 'test'})
        action = {'type': 'ir.actions.act_window', 'res_model': 'ir.config_parameter',
                  'res_id': secret.id}
        self.assertFalse(self.users._push_notify('Private module data', action=action))
        partner = self.env['res.partner'].create({'name': 'WPN test document'})
        action.update({'res_model': 'res.partner', 'res_id': partner.id})
        message = self.users._push_notify('Public contact', action=action, popup=False)
        self.assertEqual(message.partner_ids, self.users.partner_id)

    def test_invalid_options_fail_before_delivery(self):
        for options in ({'duration': 0}, {'duration': True}, {'notification_type': 'bad'},
                        {'action': {'type': 'ir.actions.server', 'id': 1}}):
            with self.assertRaises(ValidationError):
                self.users._push_notify('Invalid', **options)
