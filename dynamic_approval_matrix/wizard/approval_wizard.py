import base64
import binascii
import io
import json
import logging

from PIL import Image, UnidentifiedImageError
from psycopg2 import OperationalError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import json_default


_logger = logging.getLogger(__name__)


class ApprovalWizard(models.TransientModel):
    _name = 'dam.approval.wizard'
    _description = 'Sign Approval Level'

    request_id = fields.Many2one('dam.request', required=True, readonly=True, ondelete='cascade')
    step_id = fields.Many2one('dam.step', required=True, readonly=True, ondelete='cascade')
    document_name = fields.Char(readonly=True)
    button_label = fields.Char(readonly=True)
    level = fields.Integer(readonly=True)
    approver_name = fields.Char(readonly=True)
    signature = fields.Binary(attachment=False)
    remark = fields.Text()
    call_payload = fields.Json(readonly=True)
    consumed = fields.Boolean(readonly=True, default=False)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Open the approval wizard from the document button.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            self.check_access_rights('write')
            self.check_access_rule('write')
            if set(vals) - {'signature', 'remark'} or any(self.mapped('consumed')):
                raise AccessError(_('Only the signature and remark of an unused wizard can be changed.'))
        return super().write(vals)

    @api.model
    def _open(self, request, step, record, args, kwargs):
        # These targets and arguments are issued only by the checked guard.
        # No executable strings and no client-controlled continuation targets.
        payload = json.loads(json.dumps({
            'args': args, 'kwargs': kwargs, 'context': dict(record.env.context),
        }, default=json_default))
        wizard = self.sudo().create({
            'request_id': request.id, 'step_id': step.id,
            'document_name': request.document_name, 'button_label': request.button_label,
            'level': step.level, 'approver_name': self.env.user.name, 'call_payload': payload,
        })
        return {
            'type': 'ir.actions.act_window', 'name': _('Approve Level %s', step.level),
            'res_model': self._name, 'res_id': wizard.id, 'view_mode': 'form',
            'views': [(self.env.ref('dynamic_approval_matrix.view_dam_approval_wizard').id, 'form')],
            'target': 'new', 'context': {'bin_size': False},
        }

    def _validate_evidence(self):
        self.ensure_one()
        if not self.remark or not self.remark.strip():
            raise ValidationError(_('Enter an approval remark.'))
        signature = self.with_context(bin_size=False).signature
        if not signature:
            raise ValidationError(_('Sign before approving.'))
        try:
            if len(signature) > 3 * 1024 * 1024:
                raise ValueError('Signature too large')
            raw = base64.b64decode(signature, validate=True)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != 'PNG' or max(image.size) > 4096:
                    raise ValueError('Expected a bounded PNG signature')
                image.verify()
        except (ValueError, binascii.Error, OSError, UnidentifiedImageError, Image.DecompressionBombError) as error:
            raise ValidationError(_('Provide a valid PNG signature smaller than 2 MB.')) from error
        if len(raw) > 2 * 1024 * 1024:
            raise ValidationError(_('Provide a signature smaller than 2 MB.'))
        return signature, self.remark.strip()

    def action_approve(self):
        return self._submit('approve')

    def action_reject(self):
        return self._submit('reject')

    def _submit(self, decision):
        self.ensure_one()
        self.check_access_rights('write')
        self.check_access_rule('write')
        if self.create_uid != self.env.user:
            raise AccessError(_('Only the user who opened this wizard can approve it.'))
        if self.consumed:
            raise UserError(_('This approval wizard was already used.'))
        self._validate_evidence()
        request = self.sudo().request_id
        if request.model_name not in self.env.registry:
            raise UserError(_('The original document model is no longer available.'))
        payload = self.call_payload
        record = self.env[request.model_name].with_context(payload['context']).browse(request.res_id).exists()
        if not record:
            raise UserError(_('The original document no longer exists.'))
        guard = getattr(self.env.registry, '_dam_guards', {}).get((record._name, request.method_name))
        if not guard:
            raise UserError(_('This button is no longer available for approval.'))
        engine = record.env['dam.rule']
        rules = engine.sudo().search([
            ('model_name', '=', record._name), ('method_name', '=', request.method_name),
        ])
        # Recheck owner, current level, document version and access under lock.
        try:
            # Roll back this approval and its business writes, not previously
            # committed levels. Do not manually commit/rollback the whole cursor.
            with self.env.cr.savepoint():
                result = engine._guard(record, request.method_name, rules, guard[1],
                                       payload['args'], payload['kwargs'], approval_wizard=self, decision=decision)
        except (AccessError, OperationalError):
            # Keep permission dialogs and Odoo's concurrency retry behavior.
            raise
        except UserError as error:
            return self._error_notification(str(error))
        except Exception:
            _logger.exception('Approval failed for %s,%s (%s)',
                              request.model_name, request.res_id, request.method_name)
            return self._error_notification(_(
                'An unexpected error occurred while approving. Please contact the developer. '
                'This approval was not saved; previously approved levels are unchanged.'))
        return result if isinstance(result, dict) else {'type': 'ir.actions.client', 'tag': 'reload'}

    def _error_notification(self, message):
        return {
            'type': 'ir.actions.client', 'tag': 'dam_display_notification',
            'params': {'title': _('Approval Error'), 'type': 'danger', 'message': message,
                       'next': {'type': 'ir.actions.act_window_close'}},
        }
