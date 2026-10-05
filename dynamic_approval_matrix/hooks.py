from .models.guard import remove_guards


def post_init_hook(env):
    env['dam.button']._sync_catalog()


def uninstall_hook(env):
    remove_guards(env)
