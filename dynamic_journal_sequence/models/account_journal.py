from odoo import fields, models


class AccountJournal(models.Model):
    _inherit = "account.journal"

    sequence_format_template_id = fields.Many2one(
        comodel_name="sequence.format.template",
        string="Sequence Format Template",
        domain=[("active", "=", True)],
        ondelete="restrict",
        help=(
            "Optional numbering format. Leave empty to use the standard "
            "Odoo sequence. Configure templates under Accounting settings."
        ),
    )
