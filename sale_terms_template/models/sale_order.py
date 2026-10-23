from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.osv import expression


class SaleOrder(models.Model):
    _inherit = "sale.order"

    terms_template_id = fields.Many2one(
        "sale.terms.template", string="Applied Terms Template", readonly=True,
        copy=True, check_company=True, ondelete="set null",
        help="Source template. The Terms & Conditions text is a separate editable snapshot.",
    )

    def _select_sale_terms_templates(self, defaults_only=False):
        """One template read per batch, one domain query per rule/company.

        Local parsed domains avoid repeated validation/SQL compilation while
        preserving current user's access rules and sequence precedence.
        """
        selected = {}
        if not self:
            return selected
        templates = self.env["sale.terms.template"].search([
            ("company_id", "in", self.company_id.ids),
        ])
        for company in self.company_id:
            pending = self.filtered(lambda order: order.company_id == company)
            for template in templates.filtered(lambda record: record.company_id == company):
                if not pending:
                    break
                domain = template._parsed_domain(validate=False)
                if not domain:
                    matching = pending
                elif defaults_only:
                    continue
                else:
                    matching = self.env["sale.order"].search(expression.AND([
                        [("id", "in", pending.ids), ("company_id", "=", company.id)], domain,
                    ]))
                for order in matching:
                    selected[order] = template
                pending -= matching
        return selected

    @api.depends("partner_id", "company_id", "sale_order_template_id")
    def _compute_note(self):
        preserved = self.filtered(lambda order: bool(order._origin.id))
        for order in preserved:
            order.note = order._origin.note
        candidates = self - preserved
        if candidates:
            candidates._apply_sale_terms_template(defaults_only=True)

    def _apply_sale_terms_template(self, defaults_only=False):
        selected = self._select_sale_terms_templates(defaults_only=defaults_only)
        unmatched = self.filtered(lambda order: order not in selected)
        if unmatched:
            unmatched.note = False
            super(SaleOrder, unmatched)._compute_note()
        for order in self:
            template = selected.get(order, self.env["sale.terms.template"])
            order.terms_template_id = template
            if template:
                order.note = template.with_context(lang=order.partner_id.lang or self.env.lang).terms_html

    def action_apply_terms(self):
        if any(order.state not in ("draft", "sent") for order in self):
            raise UserError(_("Terms can only be applied to draft or sent quotations."))
        self._apply_sale_terms_template()
        return True

    @api.model_create_multi
    def create(self, vals_list):
        automatic = ["note" not in vals and "terms_template_id" not in vals for vals in vals_list]
        orders = super().create(vals_list)
        # A regular field assigned during note precomputation is not guaranteed
        # to survive create's prepared values; persist the source explicitly.
        automatic_orders = orders.browse([order.id for order, apply_default in zip(orders, automatic) if apply_default])
        selected = automatic_orders._select_sale_terms_templates(defaults_only=True)
        for order in automatic_orders:
            order.terms_template_id = selected.get(order, self.env["sale.terms.template"])
        return orders

    def write(self, vals):
        changed = self.env["sale.order"]
        if "company_id" in vals and "note" not in vals:
            changed = self.filtered(lambda order: order.company_id.id != vals["company_id"] and order.state in ("draft", "sent"))
        if changed and "terms_template_id" not in vals:
            super(SaleOrder, changed).write({"terms_template_id": False})
        result = super().write(vals)
        changed = changed.filtered(lambda order: order.state in ("draft", "sent"))
        if changed:
            changed._apply_sale_terms_template(defaults_only=True)
        return result
