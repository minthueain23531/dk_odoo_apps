# Web Push Notify — Odoo 17

Author: devkid. License: LGPL-3. Dependencies: standard Odoo `web`, `mail`, `bus`.

Reusable **in-app** notifications for installed Odoo backend users. This addon
does not implement browser/OS push, background service workers, SMS or email.
Popup delivery requires a connected Odoo web client. Optional Discuss inbox
messages persist independently of popup dismissal.

## Server API

Add `web_push_notify` to your addon manifest dependencies. From trusted Python:

```python
from markupsafe import Markup
from odoo import _

users._push_notify(
    Markup('<p>{}</p>').format(_('Please review this document.')),
    title=_('Review Required'), notification_type='info',
    duration=60000, sticky=False, popup=True, inbox=True,
    company=record.company_id,
    action={'type': 'ir.actions.act_window',
            'res_model': record._name, 'res_id': record.id},
)
```

The method is private and cannot be called directly through RPC. Your addon must
authorize the triggering operation and message contents before calling it. Do not
add a public wrapper that accepts arbitrary recipient IDs/content from clients.

- Recipients: active internal users with access to the selected company. Linked
  document read access is checked separately for each recipient.
- `message`: plain strings are escaped; `Markup` is HTML-sanitized.
- `notification_type`: `info`, `success`, `warning`, `danger`.
- `duration`: integer milliseconds, 1,000–86,400,000; default 60,000.
- `sticky=True`: popup remains until manually closed or its action succeeds.
- `popup` / `inbox`: independently enabled, both default to True.
- `action`: a window action for one existing record. The addon constructs a safe
  form-opening action and selected-company context; arbitrary custom contexts,
  server actions and client actions are intentionally not forwarded.
- `action_label`: defaults to Open Document.
- `company`: defaults to the current company and must be selected in the sender's
  environment. Call with the intended company for background/multi-company work.
- Return: the shared `mail.message`, or an empty recordset for popup-only/no
  eligible recipients. One message is created per call, with per-recipient inbox
  notifications. Recipients can see the shared recipient list.

No email is queued, even if recipients prefer email. No manual commits occur;
notifications and bus delivery follow the caller's database transaction. Repeated
calls create repeated notifications: event-level deduplication belongs to callers.

## Current-user client action

```python
return {
    'type': 'ir.actions.client', 'tag': 'web_push_notify.display',
    'params': {'title': 'Completed', 'message': 'The document was processed.',
               'type': 'success', 'duration': 15000, 'sticky': False,
               'next': {'type': 'ir.actions.act_window_close'}},
}
```

Client actions display plain text and do not create inbox messages.

## Installation, upgrades and tests

Install normally through Apps or `-i web_push_notify`. No external Python/JS
packages or third-party notification addons are required. Restart Odoo and reload
web clients when deploying updated Python/assets. Standard Odoo bus/reverse-proxy
configuration is required in multi-worker deployments.

Run `--test-enable --test-tags=/web_push_notify --stop-after-init` in a test database.
Run `node web_push_notify/tests/test_notification_service.cjs` from the parent
directory for the timer/action tests. No production users should receive test
messages. Uninstall dependent addons first; existing Discuss messages remain
standard Odoo mail records after this addon is removed.
