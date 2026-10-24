from . import models


def uninstall_hook(env):
    env["html.content.binding"].sudo().search([]).unlink()
