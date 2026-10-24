import ast

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.osv import expression
from odoo.tools import is_html_empty


class HtmlContentTemplate(models.Model):
    _name = "html.content.template"
    _description = "HTML Content Template"
    _order = "sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True, translate=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10, help="Lowest sequence wins per destination field; equal sequences use the oldest template.")
    company_id = fields.Many2one("res.company", required=True, index=True, default=lambda self: self.env.company)
    model_id = fields.Many2one("ir.model", required=True, ondelete="cascade", domain=[("transient", "=", False)])
    model_name = fields.Char(related="model_id.model")
    field_id = fields.Many2one("ir.model.fields", string="Field to Apply", required=True, ondelete="cascade",
        domain="[('model_id', '=', model_id), ('ttype', '=', 'html'), ('readonly', '=', False)]")
    domain = fields.Char(required=True, default="[]", help="Static Odoo domain on the selected model, e.g. [('partner_id.name', '=', 'Min Thu')]. [] matches every accessible record in the selected company.")
    content_html = fields.Html(string="HTML Content", required=True, translate=True, sanitize=True)

    @api.onchange("model_id")
    def _onchange_model_id(self):
        if self.field_id.model_id != self.model_id:
            self.field_id = False
            self.domain = "[]"

    def _parsed_domain(self, validate=False):
        self.ensure_one()
        try:
            domain = ast.literal_eval(self.domain)
            if not isinstance(domain, list):
                raise ValueError("Domain must be a list")
            if validate:
                expression.expression(domain, self.env[self.model_name])
            return domain
        except (ValueError, TypeError, SyntaxError, KeyError, IndexError, AttributeError) as error:
            raise ValidationError(_("Invalid domain: %s", str(error))) from error

    @api.constrains("model_id", "field_id", "domain", "content_html")
    def _check_configuration(self):
        for template in self:
            model = self.env[template.model_name]
            field = model._fields.get(template.field_id.name)
            if (model._transient or model._abstract or template.model_name.startswith("html.content.")
                    or template.field_id.model_id != template.model_id or not field
                    or field.type != "html" or field.readonly or (field.compute and not field.store and not field.inverse) or field.related):
                raise ValidationError(_("Select a writable, persistent HTML field on the selected model."))
            if is_html_empty(template.content_html):
                raise ValidationError(_("HTML content cannot be empty."))
            template._parsed_domain(validate=True)

    @api.model
    def _sync_bindings(self, model_ids):
        # Only administrators manage templates/actions; applying content never uses sudo.
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(_("Only Settings administrators may configure HTML templates."))
        binding_model = self.env["html.content.binding"].sudo()
        for model_id in set(model_ids):
            binding = binding_model.search([("model_id", "=", model_id)], limit=1)
            active = self.sudo().with_context(active_test=False).search_count([
                ("model_id", "=", model_id), ("active", "=", True),
            ], limit=1)
            if not active:
                binding.unlink()
            elif not binding:
                action = self.env["ir.actions.server"].sudo().create({
                    "name": "Apply Terms",
                    "model_id": model_id, "binding_model_id": model_id,
                    "binding_view_types": "list,form", "state": "code",
                    "groups_id": [(6, 0, [self.env.ref("base.group_user").id])],
                    "code": "action = env['html.content.template'].action_apply_to_records(records)",
                })
                binding_model.create({"model_id": model_id, "action_id": action.id})
            else:
                binding._sync_form_views()

    @api.model_create_multi
    def create(self, vals_list):
        templates = super().create(vals_list)
        templates._sync_bindings(templates.model_id.ids)
        return templates

    def write(self, vals):
        sync = "model_id" in vals or "active" in vals
        model_ids = self.model_id.ids if sync else []
        result = super().write(vals)
        if sync:
            self._sync_bindings(model_ids + self.model_id.ids)
        return result

    def unlink(self):
        model_ids = self.model_id.ids
        result = super().unlink()
        self._sync_bindings(model_ids)
        return result

    @api.model
    def action_apply_to_records(self, records):
        if not records:
            raise UserError(_("Select saved records before applying HTML templates."))
        if not self.env.user.has_group("base.group_user"):
            raise AccessError(_("Only internal users may apply HTML templates."))
        if records.env != self.env:
            records = self.env[records._name].browse(records.ids)
        records.check_access("read")
        records.check_access("write")
        if "state" in records._fields and records._name in ("sale.order", "account.move"):
            allowed = ("draft", "sent") if records._name == "sale.order" else ("draft",)
            if any(record.state not in allowed for record in records):
                raise UserError(_("Apply Terms only to draft quotations or draft invoices/journal entries."))
        templates = self.search([
            ("model_id.model", "=", records._name),
            ("company_id", "in", self.env.companies.ids),
        ])
        # Keep the batch prefetch set: browsing singleton IDs here causes N+1
        # reads of partner/language when applying to a large selection.
        record_by_id = {record.id: record for record in records}
        has_company = "company_id" in records._fields
        pending_by_field = {}
        assigned = {}
        for template in templates:
            field_name = template.field_id.name
            field = records._fields.get(field_name)
            if not field or field.type != "html" or field.readonly or (field.compute and not field.store and not field.inverse) or field.related:
                raise UserError(_("Template %s has an unsupported destination field.", template.display_name))
            if not has_company and template.company_id != self.env.company:
                continue
            key = (template.company_id.id, field_name)
            if key not in pending_by_field:
                pending_by_field[key] = records.filtered(
                    lambda record: record.company_id == template.company_id
                ) if has_company else records
            pending = pending_by_field[key]
            if not pending:
                continue
            domain = template._parsed_domain()
            # Access was checked on the entire selection above. Empty-domain
            # fallbacks need no additional database search.
            matching = self.env[records._name].with_context(active_test=False).search(
                expression.AND([[("id", "in", pending.ids)], domain])
            ) if domain else pending
            for record in matching:
                assigned.setdefault(record.id, {})[field_name] = template
            pending_by_field[key] -= matching

        # Translate each template/language once and write all matched fields in
        # a single ORM write per identical payload, keeping model hooks intact.
        content_cache = {}
        writes = {}
        partner_field = records._fields.get("partner_id")
        has_partner = partner_field and partner_field.type == "many2one" and partner_field.comodel_name == "res.partner"
        for record_id, fields_templates in assigned.items():
            record = record_by_id[record_id]
            lang = (record.partner_id.lang if has_partner else False) or self.env.lang
            values = {}
            for field_name, template in fields_templates.items():
                key = (template.id, lang)
                if key not in content_cache:
                    content_cache[key] = template.with_context(lang=lang).content_html
                values[field_name] = content_cache[key]
            writes.setdefault(tuple(sorted(values.items())), []).append(record_id)
        for payload, record_ids in writes.items():
            records.browse(record_ids).write(dict(payload))
        count = len(assigned)
        return {"type": "ir.actions.client", "tag": "display_notification", "params": {
            "title": _("HTML Templates"), "message": _("Applied HTML to %s record(s).", count),
            "type": "success" if count else "warning", "sticky": False,
            "next": {"type": "ir.actions.client", "tag": "reload"},
        }}
