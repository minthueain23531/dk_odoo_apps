import ast

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.osv import expression
from odoo.tools import is_html_empty


class SaleTermsTemplate(models.Model):
    _name = "sale.terms.template"
    _description = "Sales Terms Template"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10, help="Lowest sequence wins; equal sequences use the oldest template.")
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    domain = fields.Char(required=True, default="[]", help="Static domain on sale.order. Empty [] is a default. Python expressions and context variables are not supported.")
    terms_html = fields.Html(required=True, translate=True, sanitize=True)
    def _parsed_domain(self, validate=True):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.domain)
            if not isinstance(domain, list):
                raise ValueError("Domain must be a list")
            if validate:
                expression.expression(domain, self.env["sale.order"])
            return domain
        except (ValueError, TypeError, SyntaxError, KeyError, IndexError, AttributeError) as error:
            raise ValidationError(_("Invalid Sales Order domain: %s", str(error))) from error

    @api.constrains("domain", "terms_html")
    def _check_terms(self):
        for record in self:
            record._parsed_domain()
            if is_html_empty(record.terms_html):
                raise ValidationError(_("Terms content cannot be empty."))
