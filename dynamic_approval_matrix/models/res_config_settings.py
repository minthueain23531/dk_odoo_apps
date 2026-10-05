from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    dam_web_notify = fields.Boolean(
        related='company_id.dam_web_notify', readonly=False)
    dam_email_notify = fields.Boolean(
        related='company_id.dam_email_notify', readonly=False)
