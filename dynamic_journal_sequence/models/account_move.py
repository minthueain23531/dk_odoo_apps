import re
import string

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class AccountMove(models.Model):
    _inherit = "account.move"

    seq_code = fields.Char(string="Sequence Code", copy=False)
    has_custom_sequence_template = fields.Boolean(
        compute="_compute_sequence_template_flags"
    )
    requires_seq_code = fields.Boolean(compute="_compute_sequence_template_flags")

    @api.depends(
        "journal_id.sequence_format_template_id",
        "journal_id.sequence_format_template_id.format",
    )
    def _compute_sequence_template_flags(self):
        for move in self:
            template = move.journal_id.sequence_format_template_id
            move.has_custom_sequence_template = bool(template)
            move.requires_seq_code = bool(
                template
                and any(
                    field_name == "seq_code"
                    for _literal, field_name, _spec, _conversion
                    in string.Formatter().parse(template.format)
                )
            )

    @staticmethod
    def _parse_sequence_template(template):
        """Validate a template and return its parsed fields and sequence width."""
        if not template:
            return [], 0
        parts = list(string.Formatter().parse(template))
        fields_found = []
        seq_width = None
        for _literal, field_name, format_spec, conversion in parts:
            if field_name is None:
                continue
            if conversion:
                raise ValueError("Template conversions are not supported.")
            if field_name in ("journal", "seq_code"):
                if format_spec:
                    raise ValueError(f"{{{field_name}}} does not accept a format specifier.")
            elif field_name == "date":
                supported_date_formats = (
                    "%y", "%Y", "%y%m", "%Y%m", "%y%m%d", "%Y%m%d"
                )
                if format_spec not in supported_date_formats:
                    raise ValueError(
                        "Date format must be %y, %Y, %y%m, %Y%m, %y%m%d, or %Y%m%d."
                    )
            elif field_name == "seq":
                match = re.fullmatch(r"0([1-9][0-9]?)d", format_spec)
                if not match:
                    raise ValueError("Sequence format must use zero padding, such as {seq:04d}.")
                seq_width = int(match.group(1))
            else:
                raise ValueError(f"Unsupported sequence placeholder: {{{field_name}}}.")
            fields_found.append(field_name)
        if fields_found.count("seq") != 1:
            raise ValueError("The template must contain {seq:04d} exactly once.")
        if not parts or parts[-1][1] != "seq":
            raise ValueError("The sequence placeholder must be the final part of the template.")
        if len(fields_found) != len(set(fields_found)):
            raise ValueError("Each sequence placeholder may appear only once.")
        if "journal" not in fields_found:
            raise ValueError("The template must include {journal}.")
        return parts, seq_width

    def _sequence_template_values(self):
        self.ensure_one()
        move_date = self.date or self.invoice_date or fields.Date.context_today(self)
        return {
            "journal": self.journal_id.code or "",
            "seq_code": self.seq_code or "",
            "date": move_date,
        }

    def _render_sequence_template(self, number):
        self.ensure_one()
        template = self.journal_id.sequence_format_template_id.format
        parts, width = self._parse_sequence_template(template)
        values = self._sequence_template_values()
        values["seq"] = f"{number:0{width}d}"
        output = []
        for literal, field_name, format_spec, _conversion in parts:
            output.append(literal)
            if field_name == "date":
                output.append(values["date"].strftime(format_spec))
            elif field_name == "seq":
                output.append(values["seq"])
            elif field_name:
                output.append(str(values[field_name]))
        return "".join(output)

    def _get_custom_sequence_prefix(self):
        self.ensure_one()
        _parts, width = self._parse_sequence_template(
            self.journal_id.sequence_format_template_id.format
        )
        sample = self._render_sequence_template(1)
        sequence_marker = f"{1:0{width}d}"
        marker_start = sample.rfind(sequence_marker)
        if marker_start < 0:
            raise ValidationError("Could not locate the sequence placeholder in the template.")
        return sample[:marker_start]

    def _get_starting_sequence(self):
        self.ensure_one()
        if self.journal_id.sequence_format_template_id:
            try:
                return self._render_sequence_template(1)
            except (ValueError, KeyError, TypeError) as error:
                raise ValidationError(str(error)) from error
        return super()._get_starting_sequence()

    def _get_sequence_format_param(self, previous):
        self.ensure_one()
        template = self.journal_id.sequence_format_template_id.format
        if not template or previous == "/":
            return super()._get_sequence_format_param(previous)
        try:
            _parts, width = self._parse_sequence_template(template)
        except (ValueError, KeyError, TypeError) as error:
            raise ValidationError(str(error)) from error

        # Matching the rendered prefix makes the core mixin advance only
        # within the bucket defined by the selected template.
        sample = self._render_sequence_template(1)
        sequence_marker = f"{1:0{width}d}"
        marker_start = sample.rfind(sequence_marker)
        if marker_start < 0:
            raise ValidationError("Could not locate the sequence placeholder in the template.")
        prefix = sample[:marker_start]
        suffix = sample[marker_start + len(sequence_marker):]
        regex = (
            re.escape(prefix)
            + r"(?P<seq>\d{%d,})" % width
            + re.escape(suffix)
        )
        match = re.fullmatch(regex, previous)
        if not match:
            raise ValidationError("Existing entry number does not match its journal template.")
        sequence = match.group("seq")
        format_values = {
            "prefix1": prefix,
            "suffix": suffix,
            "seq": int(sequence),
            "seq_length": max(width, len(sequence)),
            "year_length": 0,
            "year_end_length": 0,
        }
        return "{prefix1}{seq:0{seq_length}d}{suffix}", format_values

    def _deduce_sequence_number_reset(self, name):
        if self.journal_id.sequence_format_template_id:
            return "never"
        return super()._deduce_sequence_number_reset(name)

    def _get_last_sequence_domain(self, relaxed=False):
        if self.journal_id.sequence_format_template_id:
            self.ensure_one()
            if not self.date or not self.journal_id:
                return "WHERE FALSE", {}
            # A custom template fully defines the reset bucket through its
            # rendered prefix. Avoid account.move's date-range and regex
            # inference, which can otherwise reset or exclude custom formats.
            # Keep the standard journal, refund, and payment partitions below.
            where_string = (
                "WHERE journal_id = %(journal_id)s AND name != '/' "
                "AND sequence_prefix = %(custom_sequence_prefix)s"
            )
            params = {
                "journal_id": self.journal_id.id,
                "custom_sequence_prefix": self._get_custom_sequence_prefix(),
            }
            is_payment = self.origin_payment_id or self.env.context.get("is_payment")
            if self.journal_id.refund_sequence:
                if self.move_type in ("out_refund", "in_refund"):
                    where_string += " AND move_type IN ('out_refund', 'in_refund')"
                else:
                    where_string += " AND move_type NOT IN ('out_refund', 'in_refund')"
            elif self.journal_id.payment_sequence:
                if is_payment:
                    where_string += " AND origin_payment_id IS NOT NULL"
                else:
                    where_string += " AND origin_payment_id IS NULL"
            return where_string, params
        return super()._get_last_sequence_domain(relaxed=relaxed)

    def _must_check_constrains_date_sequence(self):
        if self.journal_id.sequence_format_template_id:
            return False
        return super()._must_check_constrains_date_sequence()

    def action_post(self):
        for move in self:
            if (
                move.state == "draft"
                and move.requires_seq_code
                and not (move.seq_code or "").strip()
            ):
                raise ValidationError(
                    "Please enter a Sequence Code before posting this journal entry."
                )
        return super().action_post()
