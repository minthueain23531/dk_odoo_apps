# One-click data migration

Upgrade this addon, then click Dynamic Approval > Run Migration > Run Migration.
Active supported source configurations and proven signed approval history are imported.
Existing Dynamic rules/requests are not reconciled, overwritten or deleted.
This addon retains its own source-to-target mappings to avoid recreating the same output on an accidental second click. If mapped targets are manually deleted, retry recreates them.
All new outputs from a failed attempt roll back together; previous completed imports remain untouched. The error is retained in Migration Logs.
Unsupported or ambiguous source history remains archive-only with a warning.
No legacy approval controls, report methods or business status fields are changed.
Uninstalling this addon removes migration staging/logs only, not Dynamic records.
The old optional runtime addon is no longer a dependency. Its upgrade retires cutover behavior and preserves its existing metadata and Partner button declaration. Partner is excluded by Dynamic native discovery without that declaration; new installations without it log Partner as unsupported.
