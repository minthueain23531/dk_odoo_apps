# Odoo 17 addons

Source: the Odoo 18 addons in `D:\Odoo\odoo_apps\dk_odoo_apps`.
Destination: `D:\Odoo\odoo_apps\v17`.

- `web_push_notify`: reusable browser popup and Discuss notification service.
- `dynamic_approval_matrix`: approval workflow, signatures, reset, list/form
  statuses, company settings and queued next-approver email.
- `dam_partner_internal`: optional partner approval demonstration.

Use this directory in the Odoo 17 `addons_path`. Do not include the Odoo 18
directory in the same server's addon path: both versions use identical technical
module names. Install Web Push Notify and Dynamic Approval Matrix; install the
partner sample only when wanted. Restart Odoo and reload browser assets after
installation/upgrades.

Port adaptations: tree views, separate ACL/record-rule checks, Odoo 17 bus API,
list renderer column state and XML extension, translation API, version metadata
and migration directory. The optional suffixed button RPC endpoint is retained
alongside Odoo 17's standard route.

Dependencies are standard Community addons. Odoo 17 Enterprise uses the same
base APIs, so these addons are intended for both editions. Only the available
17 Community runtime has been tested; Enterprise-specific buttons and other
custom controller overrides need testing in the target installation.

This is an addon code port, not a downgrade/migration of an Odoo 18 database.
Use an Odoo 17 database. The existing live Odoo 17 and Odoo 18 database settings
are not changed by this delivery.

Validation: 84 Odoo 17 Community automated tests passed; notification JavaScript
tests passed; backend asset bundles compiled; uninstall/reinstall passed with
standard Discuss inbox history preserved. No live SMTP messages were sent.
