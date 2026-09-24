from lxml import etree

from odoo import api, models


class Base(models.AbstractModel):
    _inherit = 'base'

    @api.model
    def get_view(self, view_id=None, view_type='form', **options):
        result = super().get_view(view_id=view_id, view_type=view_type, **options)
        # Inject into the response, never write back to ir.ui.view or add fields.
        # Include all eligible models so changing rules needs no view-cache clear.
        if (view_type == 'form' and not self._transient and self._auto
                and (not self._name.startswith(('ir.', 'res.', 'dam.', 'base.'))
                     or getattr(self, '_dam_approval_buttons', ()))):
            root = etree.fromstring(result['arch'])
            if not root.xpath(".//widget[@name='dam_approval_panel']"):
                host = root.find('sheet')
                if host is None:
                    host = root
                widget = etree.Element('widget', name='dam_approval_panel')
                host.append(widget)
                result = dict(result, arch=etree.tostring(root, encoding='unicode'))
        return result
