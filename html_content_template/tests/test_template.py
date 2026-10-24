from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


@tagged("post_install", "-at_install")
class TestHtmlTemplate(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.model = cls.env["ir.model"]._get("res.partner")
        cls.field = cls.env["ir.model.fields"]._get("res.partner", "comment")
        cls.partner = cls.env["res.partner"].create({"name": "Min Thu", "company_id": cls.env.company.id})
        cls.other = cls.partner.copy({"name": "Other"})
        cls.template = cls.env["html.content.template"].create({
            "name": "Default", "model_id": cls.model.id, "field_id": cls.field.id,
            "sequence": 1000, "content_html": "<p>Default HTML</p>",
        })

    def apply(self, records):
        return self.env["html.content.template"].action_apply_to_records(records)

    def test_domain_priority_and_snapshot(self):
        self.template.copy({"name": "Specific", "sequence": 1, "domain": "[('name', '=', 'Min Thu')]", "content_html": "<p>Specific HTML</p>"})
        self.apply(self.partner | self.other)
        self.assertIn("Specific HTML", self.partner.comment)
        self.assertIn("Default HTML", self.other.comment)
        self.template.content_html = "<p>Changed</p>"
        self.assertIn("Default HTML", self.other.comment)

    def test_company_isolation(self):
        company = self.env["res.company"].create({"name": "HTML Company B"})
        partner = self.partner.copy({"company_id": company.id})
        self.apply(partner)
        self.assertFalse(partner.comment)

    def test_invalid_configuration(self):
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.template.domain = "[('missing_field', '=', 1)]"
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.template.domain = "__import__('os')"
        with self.assertRaises(ValidationError), self.cr.savepoint():
            self.template.field_id = self.env["ir.model.fields"]._get("res.partner", "name")

    def test_binding_lifecycle(self):
        bindings = self.env["html.content.binding"].search([("model_id", "=", self.model.id)])
        self.assertEqual(len(bindings), 1)
        another = self.template.copy()
        self.assertEqual(self.env["html.content.binding"].search_count([("model_id", "=", self.model.id)]), 1)
        self.template.active = False
        self.assertTrue(bindings.exists())
        another.active = False
        self.assertFalse(bindings.exists())

    def test_server_action(self):
        binding = self.env["html.content.binding"].search([("model_id", "=", self.model.id)])
        binding.action_id.with_context(active_model="res.partner", active_ids=self.partner.ids, active_id=self.partner.id).run()
        self.assertIn("Default HTML", self.partner.comment)

    def test_no_match_keeps_text(self):
        self.template.domain = "[('name', '=', 'Nobody')]"
        self.partner.comment = "<p>Existing</p>"
        self.apply(self.partner)
        self.assertIn("Existing", self.partner.comment)

    def test_sales_destination_when_available(self):
        if "sale.order" not in self.env:
            self.skipTest("Sales is optional")
        self.template.copy({"model_id": self.env["ir.model"]._get_id("sale.order"), "field_id": self.env["ir.model.fields"]._get("sale.order", "note").id, "domain": "[('partner_id.name', '=', 'Min Thu')]"})
        order = self.env["sale.order"].create({"partner_id": self.partner.id})
        self.apply(order)
        self.assertIn("Default HTML", order.note)
        order.state = "sale"
        with self.assertRaises(UserError):
            self.apply(order)

    def test_apply_respects_write_access(self):
        user = new_test_user(self.env, login="html_content_basic_user", groups="base.group_user")
        self.template.copy({"model_id": self.env["ir.model"]._get_id("res.company"), "field_id": self.env["ir.model.fields"]._get("res.company", "report_footer").id})
        with self.assertRaises(AccessError):
            self.env["html.content.template"].with_user(user).action_apply_to_records(self.env.company.with_user(user))
        with self.assertRaises(AccessError):
            self.template.with_user(user).write({"content_html": "<p>Denied</p>"})

    def test_invoice_destination_when_available(self):
        if "account.move" not in self.env:
            self.skipTest("Accounting is optional")
        self.template.copy({"model_id": self.env["ir.model"]._get_id("account.move"), "field_id": self.env["ir.model.fields"]._get("account.move", "narration").id, "domain": "[('move_type', '=', 'out_invoice')]"})
        journal = self.env["account.journal"].create({"name": "HTML Invoice Journal", "code": "HTML", "type": "sale"})
        move = self.env["account.move"].create({"move_type": "out_invoice", "partner_id": self.partner.id, "journal_id": journal.id})
        self.apply(move)
        self.assertIn("Default HTML", move.narration)

    def test_form_header_button(self):
        binding = self.env["html.content.binding"].search([("model_id", "=", self.model.id)])
        self.assertTrue(binding.view_ids)
        for view in binding.view_ids:
            arch = view.inherit_id._get_combined_arch()
            buttons = arch.xpath("./header/button[@name='%s']" % binding.action_id.id)
            self.assertEqual(len(buttons), 1)
            self.assertEqual(buttons[0].get("string"), "Apply Terms")
        count = len(binding.view_ids)
        binding._sync_form_views()
        self.assertEqual(len(binding.view_ids), count)
        views = binding.view_ids
        self.template.active = False
        self.assertFalse(views.exists())

    def test_empty_domain_has_no_matching_search(self):
        from unittest.mock import patch
        partners = self.env["res.partner"].create([
            {"name": "Batch %s" % index, "company_id": self.env.company.id} for index in range(20)
        ])
        with patch.object(type(partners), "search", side_effect=AssertionError("Default must not search targets")):
            self.apply(partners)
        self.assertTrue(all("Default HTML" in partner.comment for partner in partners))

    def test_default_applies_to_archived_selected_record(self):
        self.partner.active = False
        self.apply(self.partner)
        self.assertIn("Default HTML", self.partner.comment)

    def test_multiple_fields_use_one_write(self):
        from unittest.mock import patch
        company = self.env.company
        model_id = self.env["ir.model"]._get_id("res.company")
        for field_name in ("report_header", "report_footer"):
            self.template.copy({"model_id": model_id, "field_id": self.env["ir.model.fields"]._get("res.company", field_name).id})
        original = type(company).write
        payloads = []

        def tracked_write(records, values):
            if records._name == "res.company" and records.ids == company.ids:
                payloads.append(values)
            return original(records, values)

        with patch.object(type(company), "write", tracked_write):
            self.apply(company)
        matching = [values for values in payloads if "report_header" in values or "report_footer" in values]
        self.assertEqual(len(matching), 1)
        self.assertEqual(set(matching[0]), {"report_header", "report_footer"})
