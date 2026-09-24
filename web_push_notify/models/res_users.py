from markupsafe import Markup, escape

from odoo import Command, _, models
from odoo.exceptions import AccessError, ValidationError
from odoo.tools import html_sanitize


class ResUsers(models.Model):
    _inherit = 'res.users'

    def _push_notify(self, message, *, title=None, notification_type='info',
                     duration=60000, sticky=False, popup=True, inbox=True,
                     action=None, action_label=None, company=None):
        """Notify active internal recipients from trusted server-side code.

        Plain strings are escaped; Markup is sanitized. Duration is milliseconds
        and sticky=True disables automatic dismissal. Inbox never sends email.
        Only record-opening window actions are supported. Recipients must belong
        to the selected company and be able to read the linked document.
        The caller must authorize the business event/content before calling this
        private (non-RPC) method. No manual commits or external network calls.
        Returns the shared mail.message, or an empty recordset for popup-only.
        """
        if notification_type not in ('info', 'success', 'warning', 'danger'):
            raise ValidationError(_('Unsupported notification type.'))
        if type(duration) is not int or not 1000 <= duration <= 86400000:
            raise ValidationError(_('Notification duration must be between 1 second and 24 hours.'))
        if not isinstance(message, str):
            raise ValidationError(_('Notification message must be text or Markup.'))
        company = company or self.env.company
        company.ensure_one()
        if company not in self.env.companies:
            raise AccessError(_('Select the notification company before sending.'))
        self.check_access('read')
        recipients = self.sudo().exists().filtered(
            lambda user: user.active and not user.share and company in user.company_ids)
        messages = self.env['mail.message']
        if not popup and not inbox:
            return messages

        if action is not None:
            if (not isinstance(action, dict) or action.get('type') != 'ir.actions.act_window'
                    or action.get('res_model') not in self.env.registry
                    or type(action.get('res_id')) is not int or action['res_id'] <= 0):
                raise ValidationError(_('Use a window action opening one existing document.'))
            # Construct a record-opening action, not arbitrary client/server code
            # or caller-controlled company/default context.
            model, res_id = action['res_model'], action['res_id']
            document = self.env[model].browse(res_id).exists()
            if not document:
                return messages
            allowed = recipients.browse()
            for user in recipients:
                try:
                    document.with_user(user).with_context(allowed_company_ids=company.ids).check_access('read')
                except AccessError:
                    continue
                allowed |= user
            recipients = allowed
            action = {
                'type': 'ir.actions.act_window', 'res_model': model, 'res_id': res_id,
                'views': [(False, 'form')], 'view_mode': 'form', 'target': 'current',
                'context': {'allowed_company_ids': company.ids},
            }
        if not recipients:
            return messages

        title = str(title or _('Notification'))
        safe_body = html_sanitize(str(message if isinstance(message, Markup) else escape(message)))
        partners = recipients.partner_id
        if inbox:
            # One shared message; standard mail code creates per-user inbox
            # notifications and sends the Discuss store update in a batch.
            messages = self.env['mail.message'].sudo().create({
                'subject': title, 'body': safe_body, 'message_type': 'user_notification',
                'subtype_id': self.env.ref('mail.mt_note').id,
                'author_id': self.env.user.partner_id.id, 'is_internal': True,
                'partner_ids': [Command.set(partners.ids)],
            })
            self.env['mail.thread'].sudo()._notify_thread_by_inbox(messages, [
                {'id': user.partner_id.id, 'uid': user.id, 'notif': 'inbox'} for user in recipients
            ])
        if popup:
            payload = {
                'title': title, 'message': safe_body, 'type': notification_type,
                'duration': duration, 'sticky': bool(sticky), 'action': action,
                'action_label': str(action_label or _('Open Document')),
            }
            for partner in partners:
                partner.sudo()._bus_send('web_push_notify.notification', payload)
        return messages
