# Performance review — 2026-11-03

## Measured lock-check batch cost

Existing Python 3.12/Odoo 18 runtime, isolated database `tlc_verify_20261103`,
one active Sales Order rule, global lock enabled, open-period orders. Each case
was warmed once, then measured three times; the table reports median elapsed time.
Fixtures were rolled back. These numbers measure `_check_operation`, not full
CRUD execution, HTTP latency or browser rendering. They are local measurements,
not production capacity estimates.

| Records | Before SQL queries | After SQL queries | Before median ms | After median ms |
| --- | ---: | ---: | ---: | ---: |
| 1 | 3 | 3 | 2.96 | 2.39 |
| 100 | 102 | 3 | 105.62 | 3.35 |
| 500 | 502 | 3 | 531.01 | 7.72 |

Raw logs: Office/tlc-performance-before.log and Office/tlc-performance-after.log.

## Changes

- Resolve effective company/category locks once per rule and record batch instead
  of once per record. No global cache of exceptions, dates or user permissions.
- Filter by rule company alongside the record domain, resolve timezone once,
  and avoid building violation details for records beyond the latest lock date.
- Compile/validate static domains at configuration validation, rather than compile
  the same domain on every runtime check. Runtime still parses the static literal.
- Short-circuit All Fields checks and compute changed-field sets once.
- Batch button/detail discovery creates instead of refreshing the registry after
  every discovered button. Use a set to deduplicate detail mappings.
- Add indexes on stored rule/detail model names and rule company for lookups.
- Only structural rule/action/detail changes refresh method patches and signal
  other workers. Ordinary names, flags and date/filter settings are read fresh
  and do not need a registry reload. Automated-action compatibility remains.

## Loading scope and remaining costs

The extension does not wrap ordinary `read`, `search`, report rendering or browser
loading. Registry startup discovers configured models/actions and installs wrappers;
configuration lists use normal Odoo views. Writes/actions on configured models
still add rule/detail lookup queries and savepoints. Several applicable rules add
cost per rule; deeply nested detail paths add ORM reads. Full-page timings were not
measured, and project-specific large-volume workflow UAT remains appropriate.

Restart the service and upgrade this module to load updated code and create indexes.
The project database was not upgraded during this review.
