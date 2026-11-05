"""Preserve existing templates when separating their label and format."""


def migrate(cr, version):
    cr.execute(
        "ALTER TABLE sequence_format_template "
        "ADD COLUMN IF NOT EXISTS format varchar"
    )
    cr.execute(
        "UPDATE sequence_format_template "
        "SET format = name "
        "WHERE format IS NULL OR format = ''"
    )
