"""Deprecated cutover storage and existing Partner button declaration only.
No legacy bypass, report replacement or business status synchronization.
"""
from odoo import api, fields, models


class CompanyCutover(models.Model):
    _inherit = 'res.company'

    pam_dynamic_cutover = fields.Boolean(default=False, readonly=True, groups='base.group_system', copy=False)
    pam_dynamic_cutover_at = fields.Datetime(readonly=True, groups='base.group_system', copy=False)


class PartnerApprovalCompatibility(models.Model):
    _inherit = 'res.partner'
    _dam_approval_buttons = ('action_approve',)


class PermanentApprovalScope(models.Model):
    _name = 'pam.runtime.scope'
    _description = 'Permanent Dynamic Approval Cutover Scope'

    company_id = fields.Many2one('res.company', required=True, ondelete='cascade', index=True)
    model_name = fields.Char(required=True, index=True)
    method_name = fields.Char(required=True, index=True)
    rule_ids = fields.Many2many('dam.rule', 'pam_runtime_scope_rule_rel', 'scope_id', 'rule_id')
    _sql_constraints = [('scope_unique', 'unique(company_id,model_name,method_name)', 'Cutover scope already exists.')]

    @api.model
    def _register_rules(self, rules):
        for rule in rules.sudo():
            domain = [('company_id', '=', rule.company_id.id), ('model_name', '=', rule.model_name),
                      ('method_name', '=', rule.method_name)]
            scope = self.sudo().search(domain, limit=1)
            if not scope:
                scope = self.sudo().create({'company_id': rule.company_id.id,
                    'model_name': rule.model_name, 'method_name': rule.method_name})
            if rule not in scope.rule_ids:
                scope.write({'rule_ids': [(4, rule.id)]})


