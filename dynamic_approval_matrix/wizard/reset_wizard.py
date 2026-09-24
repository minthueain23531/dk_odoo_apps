from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError


class ApprovalResetWizard(models.TransientModel):
    _name = 'dam.reset.wizard'
    _description = 'Reset Document Button Approval'

    request_id = fields.Many2one('dam.request', required=True, readonly=True, ondelete='cascade')
    reason = fields.Text()
    consumed = fields.Boolean(readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(_('Open Reset from the approval panel or transaction.'))
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            self.check_access('write')
            if set(vals) - {'reason'} or any(self.mapped('consumed')):
                raise AccessError(_('Only the reason of an unused reset wizard can be changed.'))
        return super().write(vals)

    def action_reset(self):
        self.ensure_one()
        self.check_access('write')
        if not self.env.user.has_group('dynamic_approval_matrix.group_dam_manager') or self.create_uid != self.env.user:
            raise AccessError(_('Only the Approval Manager who opened this wizard can reset it.'))
        if not self.reason or not self.reason.strip():
            raise ValidationError(_('Enter a reason for resetting this approval.'))
        request = self.request_id
        request.check_access('read')
        record = self.env[request.model_name].browse(request.res_id).exists()
        if not record:
            raise UserError(_('The original document no longer exists.'))
        self.env['dam.rule']._lock_document(record, request.method_name)
        self.invalidate_recordset()
        request.invalidate_recordset()
        latest = self.env['dam.request'].search([
            ('model_name', '=', request.model_name), ('res_id', '=', request.res_id),
            ('method_name', '=', request.method_name),
        ], order='id desc', limit=1)
        if self.consumed or latest != request or request.state not in ('pending', 'rejected'):
            raise UserError(_('This approval can no longer be reset. Refresh the document.'))
        request.sudo().write({'state': 'reset'})
        request._event('reset', _('Approval reset. Reason: %s', self.reason.strip()))
        self.sudo().write({'consumed': True})
        return self.env['dam.rule']._notify(_('Approval reset. The next button click starts a new approval cycle.'), remaining=True)
