from odoo import models


class ResPartner(models.Model):
    _name = 'res.partner'
    _inherit = ['res.partner', 'dam.approval.status.mixin']

    # res.* buttons are excluded by default; opt in only this sample button.
    _dam_approval_buttons = ('action_dam_sample_approval',)

    def action_dam_sample_approval(self):
        """Configure two approval levels on this button to try the modifier."""
        self.ensure_one()
        return True
