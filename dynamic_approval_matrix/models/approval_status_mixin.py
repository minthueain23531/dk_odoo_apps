from odoo import api, fields, models


class ApprovalStatusMixin(models.AbstractModel):
    _name = 'dam.approval.status.mixin'
    _description = 'Reusable First-Button Approval Status'

    # Explicit opt-in for buttons on otherwise excluded system models, such as
    # res.partner. Business models keep their existing button discovery behavior.
    _dam_approval_buttons = ()

    dam_approval_status = fields.Char(
        string='Approval Status', compute='_compute_dam_approval_status',
        compute_sudo=False,
        help='Same first-button status as the approval list column: not_started, '
             'level_N_approved, done or rejected. Intended for view modifiers.')

    @api.depends_context('uid', 'company', 'allowed_company_ids')
    def _compute_dam_approval_status(self):
        persisted = self._origin.filtered('id')
        statuses = self.env['dam.rule']._get_document_approval_status(persisted)
        for record in self:
            record.dam_approval_status = statuses.get(record._origin.id, {}).get('key', 'not_started')
