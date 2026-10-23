from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    for company in env["res.company"].search([("sale_terms_template_enabled", "=", True)]):
        if company.sale_terms_template_html:
            env["sale.terms.template"].create({
                "name": "%s - Default Terms" % company.name,
                "company_id": company.id, "domain": "[]", "sequence": 1000,
                "terms_html": company.sale_terms_template_html,
            })
