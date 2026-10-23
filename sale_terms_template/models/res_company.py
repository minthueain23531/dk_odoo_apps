from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    # Compatibility fields for the 18.0.1.0.0 data migration. No runtime use.
    sale_terms_template_enabled = fields.Boolean(string="Legacy Custom Sales Terms", readonly=True)
    sale_terms_template_html = fields.Html(string="Legacy Sales Terms", translate=True, readonly=True)
