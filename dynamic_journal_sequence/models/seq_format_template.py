from odoo import api, fields, models
from odoo.exceptions import ValidationError


class SeqFormatTemplate(models.Model):
    _name = "sequence.format.template"
    _description = "Journal Sequence Format Template"
    _order = "name, id"

    name = fields.Char(string="Name", required=True)
    format = fields.Char(
        string="Format",
        required=True,
        help=(
            "Required: {journal} and {seq:04d}. Optional: {seq_code} and "
            "{date:%y}, {date:%y%m}, or {date:%y%m%d}. Without a date, "
            "the sequence continues across dates. Put {seq:04d} at the end."
        ),
    )
    active = fields.Boolean(default=True)

    @api.constrains("format")
    def _check_format(self):
        for template in self:
            try:
                self.env["account.move"]._parse_sequence_template(template.format)
            except (ValueError, KeyError, TypeError) as error:
                raise ValidationError(str(error)) from error
