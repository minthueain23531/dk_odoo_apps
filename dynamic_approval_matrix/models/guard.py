"""Registry-local guards; no global BaseModel monkey patch or context bypass."""
from contextvars import ContextVar
from functools import wraps

from lxml import etree

from odoo import _
from odoo.exceptions import UserError


button_dispatch = ContextVar('dam_button_dispatch', default=None)
executing = ContextVar('dam_executing', default=frozenset())
EXCLUDED_METHODS = {
    'create', 'write', 'unlink', 'read', 'search', 'copy', 'default_get',
    'fields_get', 'get_view', 'get_views', 'onchange', 'web_save', 'web_read',
    'toggle_active', 'action_archive', 'action_unarchive',
}


def discover_buttons(env):
    """Only persistent business models and public object buttons are eligible."""
    buttons = {}
    views = env['ir.ui.view'].sudo().with_context(lang='en_US').search([
        ('type', '=', 'form'), ('model', '!=', False),
    ])
    for view in views:
        model = view.model
        if model not in env.registry:
            continue
        record = env[model]
        restricted = model.startswith(('ir.', 'res.', 'dam.', 'base.'))
        allowed_buttons = getattr(record, '_dam_approval_buttons', ())
        if restricted and not allowed_buttons:
            continue
        if record._transient or not record._auto or not record._log_access:
            continue
        try:
            root = etree.fromstring(view.arch_db.encode(), parser=etree.XMLParser(resolve_entities=False))
        except (etree.XMLSyntaxError, AttributeError):
            continue
        for button in root.xpath(".//button[@type='object'][@name]"):
            # Inline x2many buttons belong to another model.
            if any(parent.tag == 'field' for parent in button.iterancestors()):
                continue
            name = button.get('name')
            if restricted and name not in allowed_buttons:
                continue
            method = getattr(type(record), name, None)
            if (not name.isidentifier() or name.startswith('_') or name in EXCLUDED_METHODS
                    or not callable(method) or getattr(method, '_api_private', False)
                    or getattr(method, '_api', None) in ('model', 'model_create')):
                continue
            buttons.setdefault((model, name), button.get('string') or name)
    return buttons


def _make_guard(original, method_name):
    # Bind in a closure, not keyword defaults that RPC callers could override.
    @wraps(original)
    def guarded(records, *args, **kwargs):
        key = (records.env.cr, records._name, method_name, tuple(records.ids))
        if key in executing.get():
            return original(records, *args, **kwargs)
        engine = records.env['dam.rule']
        rules = engine.sudo().search([
            ('model_name', '=', records._name), ('method_name', '=', method_name),
        ])
        pending = records.env['dam.request'].sudo().search([
            ('model_name', '=', records._name), ('method_name', '=', method_name),
            ('res_id', 'in', records.ids), ('state', 'in', ['pending', 'approved', 'rejected']),
        ], limit=1) if records and not rules else False
        if not rules and not pending:
            return original(records, *args, **kwargs)
        if len(records) != 1:
            raise UserError(_('This button has approval rules. Open one document at a time.'))
        return engine._guard(records, method_name, rules, original, args, kwargs)
    return guarded


def install_guards(env):
    guards = getattr(env.registry, '_dam_guards', {})
    for model, name in discover_buttons(env):
        cls = env.registry[model]
        if (model, name) in guards:
            continue
        original = getattr(cls, name)
        owned = name in cls.__dict__
        guarded = _make_guard(original, name)
        setattr(cls, name, guarded)
        guards[(model, name)] = (cls, original, owned, guarded)
    env.registry._dam_guards = guards


def remove_guards(env):
    for (_model, name), (cls, original, owned, guarded) in getattr(env.registry, '_dam_guards', {}).items():
        if cls.__dict__.get(name) is guarded:
            if owned:
                setattr(cls, name, original)
            else:
                delattr(cls, name)
    env.registry._dam_guards = {}
