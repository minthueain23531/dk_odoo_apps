from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for request in env['dam.request'].search([('document_name', '=', False)]):
        # Preserve the historical display name already captured in the title.
        request.document_name = request.name.rsplit(' — ', 1)[0]
