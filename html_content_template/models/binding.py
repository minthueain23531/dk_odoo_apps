from lxml import etree

from odoo import api, fields, models


class HtmlContentBinding(models.Model):
    _name = "html.content.binding"
    _description = "HTML Content Action Binding"

    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade")
    action_id = fields.Many2one("ir.actions.server", required=True, ondelete="cascade")
    view_ids = fields.Many2many("ir.ui.view", "html_content_binding_view_rel", "binding_id", "view_id", readonly=True)
    _sql_constraints = [("model_unique", "unique(model_id)", "Only one HTML action per model is allowed.")]

    def _sync_form_views(self):
        """Generate module-owned extensions; never modify the original views."""
        views = self.env["ir.ui.view"]
        for binding in self:
            # Optional target modules may not yet be loaded during an upgrade.
            # End migration synchronizes again after the full registry is ready.
            if binding.model_id.model not in self.env:
                continue
            roots = views.search([
                ("model", "=", binding.model_id.model), ("type", "=", "form"),
                ("inherit_id", "=", False), ("active", "=", True),
            ])
            current = {view.inherit_id.id: view for view in binding.view_ids}
            stale = binding.view_ids.filtered(lambda view: view.inherit_id not in roots)
            if stale:
                stale.unlink()
            for root in roots:
                existing = current.get(root.id)
                if existing:
                    continue
                arch = root._get_combined_arch()
                data = etree.Element("data")
                if arch.xpath("./header"):
                    target = etree.SubElement(data, "xpath", expr="//form/header[1]", position="inside")
                else:
                    target = etree.SubElement(data, "xpath", expr="/form", position="inside")
                    target = etree.SubElement(target, "header")
                invisible = "not id"
                if binding.model_id.model == "sale.order":
                    invisible += " or state not in ('draft', 'sent')"
                elif binding.model_id.model == "account.move":
                    invisible += " or state != 'draft'"
                etree.SubElement(target, "button", {
                    "name": str(binding.action_id.id), "type": "action", "string": "Apply Terms",
                    "class": "btn-secondary", "groups": "base.group_user",
                    "invisible": invisible,
                    "confirm": "Replace matching fields with the applicable terms templates?",
                })
                view = views.create({
                    "name": "html.content.apply.terms.%s.%s" % (binding.model_id.model, root.id),
                    "model": binding.model_id.model, "inherit_id": root.id,
                    "priority": 99, "arch_db": etree.tostring(data, encoding="unicode"),
                })
                binding.view_ids = [fields.Command.link(view.id)]

    @api.model_create_multi
    def create(self, vals_list):
        bindings = super().create(vals_list)
        bindings._sync_form_views()
        return bindings

    def unlink(self):
        actions = self.action_id
        self.view_ids.unlink()
        result = super().unlink()
        actions.exists().unlink()
        return result
