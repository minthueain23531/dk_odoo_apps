from odoo.exceptions import UserError, ValidationError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSaleTerms(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.customer = cls.env["res.partner"].create({"name": "Terms Customer"})
        cls.template = cls.env["sale.terms.template"].create({"name": "Default", "company_id": cls.env.company.id, "domain": "[]", "sequence": 1000, "terms_html": "<p>Default terms</p>"})

    def order(self, **values):
        return self.env["sale.order"].create({"partner_id": self.customer.id, **values})

    def test_default_snapshot(self):
        order = self.order()
        self.assertEqual(order.terms_template_id, self.template)
        self.template.terms_html = "<p>Changed</p>"
        self.assertIn("Default terms", order.note)
        order.note = "<p>Negotiated</p>"
        order.partner_id = self.customer.copy()
        self.assertIn("Negotiated", order.note)

    def test_condition_and_priority(self):
        rule = self.template.copy({"name": "Customer terms", "sequence": 1, "domain": repr([("partner_id", "=", self.customer.id)]), "terms_html": "<p>Customer terms</p>"})
        order = self.order()
        self.assertEqual(order.terms_template_id, self.template)
        order.action_apply_terms()
        self.assertEqual(order.terms_template_id, rule)
        self.assertIn("Customer terms", order.note)
        rule.active = False
        order.action_apply_terms()
        self.assertEqual(order.terms_template_id, self.template)

    def test_no_match_fallback(self):
        self.template.active = False
        self.env.company.write({"invoice_terms": "<p>Standard terms</p>", "terms_type": "plain"})
        self.env["ir.config_parameter"].sudo().set_param("account.use_invoice_terms", True)
        order = self.order()
        self.assertFalse(order.terms_template_id)
        self.assertIn("Standard terms", order.note)

    def test_explicit_note_and_confirmed_protection(self):
        order = self.order(note="<p>Explicit</p>")
        self.assertIn("Explicit", order.note)
        order.state = "sale"
        with self.assertRaises(UserError):
            order.action_apply_terms()

    def test_invalid_domain(self):
        for domain in ["[('missing_terms_field', '=', False)]", "__import__('os')", "['|']"]:
            with self.assertRaises(ValidationError), self.cr.savepoint():
                self.template.domain = domain

    def test_multi_company(self):
        company = self.env["res.company"].create({"name": "Terms Company B"})
        template = self.template.copy({"company_id": company.id, "terms_html": "<p>Company B</p>"})
        order = self.env["sale.order"].with_company(company).create({"partner_id": self.customer.id})
        self.assertEqual(order.terms_template_id, template)
        self.assertIn("Company B", order.note)

    def test_report(self):
        html, _ = self.env["ir.actions.report"]._render_qweb_html("sale.action_report_saleorder", self.order().ids)
        self.assertIn(b"Default terms", html)
        self.assertIn(b"o_sale_terms_template", html)

    def test_form_create_keeps_source(self):
        with Form(self.env["sale.order"]) as form:
            form.partner_id = self.customer
        self.assertEqual(form.record.terms_template_id, self.template)
        self.assertIn("Default terms", form.record.note)

    def test_saved_company_switch(self):
        company = self.env["res.company"].create({"name": "Switch Company"})
        template = self.template.copy({"company_id": company.id, "terms_html": "<p>Switched</p>"})
        order = self.order().with_context(allowed_company_ids=[self.env.company.id, company.id])
        order.company_id = company
        self.assertEqual(order.terms_template_id, template)
        self.assertIn("Switched", order.note)

    def test_template_domain_on_amount(self):
        rule = self.template.copy({"sequence": 1, "domain": "[('amount_total', '>=', 100)]"})
        order = self.order()
        order.action_apply_terms()
        self.assertEqual(order.terms_template_id, self.template)
        product = self.env["product.product"].create({"name": "Terms service", "type": "service"})
        self.env["sale.order.line"].create({"order_id": order.id, "product_id": product.id, "product_uom_qty": 1, "price_unit": 150})
        order.action_apply_terms()
        self.assertEqual(order.terms_template_id, rule)

    def test_batch_rule_matching(self):
        other = self.customer.copy({"name": "Batch other"})
        rule = self.template.copy({"sequence": 1, "domain": repr([("partner_id", "=", self.customer.id)])})
        orders = self.env["sale.order"].create([
            {"partner_id": self.customer.id}, {"partner_id": other.id}, {"partner_id": self.customer.id},
        ])
        orders.action_apply_terms()
        self.assertEqual(orders[0].terms_template_id, rule)
        self.assertEqual(orders[1].terms_template_id, self.template)
        self.assertEqual(orders[2].terms_template_id, rule)

    def test_batch_selection_query_count(self):
        self.template.copy({"sequence": 1, "domain": repr([("partner_id", "=", self.customer.id)])})
        orders = self.env["sale.order"].create([{"partner_id": self.customer.id} for _ in range(12)])
        orders._select_sale_terms_templates()  # Warm field caches consistently.
        start = self.cr.sql_log_count
        selected = orders._select_sale_terms_templates()
        batch_queries = self.cr.sql_log_count - start
        start = self.cr.sql_log_count
        for order in orders:
            order._select_sale_terms_templates()
        individual_queries = self.cr.sql_log_count - start
        self.assertEqual(len(selected), 12)
        self.assertLess(batch_queries, individual_queries)
        import logging
        logging.getLogger(__name__).info(
            "TERMS BENCHMARK: 12 orders; batch=%s SQL queries, individual=%s SQL queries",
            batch_queries, individual_queries,
        )
