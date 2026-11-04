from odoo import models


class BaseAutomation(models.Model):
    _inherit = 'base.automation'

    def _update_registry(self):
        result = super()._update_registry()
        # Odoo automation edits reinstall CRUD wrappers on the same registry
        # classes. Restore our outer checks after that standard refresh.
        if self.env.registry.ready:
            self.env['transaction.lock.rule']._register_hook()
        return result
