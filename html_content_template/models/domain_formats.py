from odoo import api, fields, models


class HtmlDomainFormats(models.Model):
    _name = "html.domain.format"
    _description = "Html Domain Formats"
    _order = "name, id"

    name = fields.Char(string="Name", required=True)
    domain = fields.Char(
        string="Domain",
        required=True,
    )
    active = fields.Boolean(default=True)