# Permanent approval runtime compatibility

Keep this addon installed after removing payment_approval_matrix_migration.
It owns permanent cutover company fields, model/button scopes, partner guard
opt-in, legacy-engine bypass, report helpers and old approval_status syncing.

The migration addon is disposable after its 18.0.5.0.0 upgrade seeds the
permanent scopes and transfers company-field XML-ID ownership. Removing it
may delete migration batches, mappings, snapshots, evidence and logs. Dynamic
rules, levels, requests, steps, signatures and events belong to
dynamic_approval_matrix and remain. The runtime contains no references to
the disposable migration models and does not depend on the migration addon.

Uninstall only payment_approval_matrix_migration. Do not select this runtime,
dynamic_approval_matrix or payment_approval_matrix for removal.
