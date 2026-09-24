from odoo import http
from odoo.http import request
from odoo.addons.web.controllers.dataset import DataSet

from ..models.guard import button_dispatch


class DynamicApprovalDataSet(DataSet):
    @http.route('/web/dataset/call_button/<path:path>', type='json', auth='user', readonly=False)
    def dam_call_button_path(self, model, method, args, kwargs, path=None):
        # Legacy overrides may publish only the bare URL, while Odoo 18's web
        # client uses the path form. A separate endpoint survives that override
        # and still executes the same controller super chain.
        return self.call_button(model, method, args, kwargs)

    @http.route()
    def call_button(self, model, method, args, kwargs, path=None):
        # This capability exists only in Python. RPC context cannot forge it.
        ids = tuple(args[0]) if args and isinstance(args[0], list) else ()
        token = button_dispatch.set((request.env.cr, model, method, ids))
        try:
            # Older custom controllers in this database omit the optional path
            # argument. It is unused by DataSet; keep their super chain compatible.
            return super().call_button(model, method, args, kwargs)
        finally:
            button_dispatch.reset(token)
