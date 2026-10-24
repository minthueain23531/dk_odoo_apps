{
    "name": "HTML Content Templates",
    "version": "18.0.1.1.0",
    "summary": "Apply company and domain based HTML to configurable model fields",
    "category": "Productivity",
    "license": "LGPL-3",
    "depends": ["base", "web"],
    "data": ["security/ir.model.access.csv", "security/template_security.xml", "views/template_views.xml"],
    "uninstall_hook": "uninstall_hook",
    "installable": True,
    "application": True,
}
