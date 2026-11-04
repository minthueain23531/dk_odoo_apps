# Validation — 2026-11-03

- Runtime: existing D:/Odoo/v18/.venv (Python 3.12.10).
- Odoo source: D:/Odoo/v18/odoo18e_standard_july14.
- Isolated PostgreSQL database: tlc_verify_20261103.
- Final run: **22 test methods, 0 failures, 0 errors**.
- base_automation was installed and its registry-refresh interaction was tested.
- XML views/security files loaded successfully through an Odoo module update.
- Python and XML syntax checks passed.
- Performance update: final rerun in tlc-performance-final-tests.log passed all
  22 test methods (0 failures/errors). See PERFORMANCE.md for benchmark scope.
- Original v18e_mtc_03 database was only queried for installed modules/model names;
  no module installation, company locks or business records were changed there.
- Final test log: Office/tlc-test-3.log. Earlier logs include failures fixed before
  the successful final run (reserved field naming, test action/date semantics and
  an absent stock fixture).

## Still required for project rollout

2026-11-04 checkbox correction: added Purchase regression covering closed-period
create, date/vendor-reference/detail edits with Create/Edit restrictions off,
Confirm/Delete restrictions on, and re-enabling CRUD restrictions. Earlier test
fixture required an explicit product_qty for a purchase note line on this build.

Install on a project staging database and configure the intended company/model/
date/action/detail mappings. No active rules are shipped. Project-specific
Packing List, subcontracting and stock backdate workflows need scenario-based UAT
with agreed mappings. Generic tests are not a claim that all custom workflows have
already been accepted. UI buttons are not hidden; server-side restrictions apply
when actions or saves are attempted.
