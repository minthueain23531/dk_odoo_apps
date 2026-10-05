from odoo import fields, models


class ResCompany(models.Model):
    _inherit = 'res.company'

    dam_web_notify = fields.Boolean(
        string='Web Notify', default=True,
        help='Notify the next approvers with a browser popup and Discuss inbox message.')
    dam_email_notify = fields.Boolean(
        string='Email Notification', default=False,
        help='Queue an email to the next approvers. Disabling notifications does not change approval requirements.')
