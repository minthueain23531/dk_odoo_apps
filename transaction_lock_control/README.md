# Transaction Lock Control (Odoo 18)

Extends company accounting lock policies to explicitly configured business
transactions. No Odoo core files or existing addon files are modified. Installation
does not create active rules or change company lock dates.

## Setup

Accounting Administrator → App launcher → Transaction Lock Control.
The same rules are also available in Accounting → Configuration → Transaction Lock Rules.

The addon uses the core Accounting lock fields and exception logic on Community
and Enterprise. The standard Lock Journal Entries wizard belongs to Enterprise's
`account_accountant` module. On stock Community, lock dates must be configured
through an appropriate Community lock-date UI or supported administration workflow.

1. Select the company, persistent business model, target Date/Datetime field, and
   its Many2one company field. Infrastructure/transient models are not supported.
2. Select Sales, Purchases and/or Tax. Everything and Hard Lock are always checked.
3. Set a static record filter if a model contains different transaction types.
4. Select Prevent Create / Prevent Edit / Prevent Delete restrictions. Exempt fields are optional;
   target date, company and filter fields cannot be exempted.
5. Click **Discover Buttons & Detail Models**. Discovered public object methods are
   initially unblocked: deliberately enable Prevent Action for business operations.
   Direct One2many detail bindings are discovered and enabled. Review them.
6. Add nested detail models/Many2one parent paths where required. For example,
   `line_id.packing_lists_id` for packing lots. Only enabled bindings are enforced.
7. Test the rule on representative draft/done/cancel/return/reversal records before
   enabling it for normal use. Validate backdate fields against project code first.

## Project mapping starting points (review before enabling)

| Transactions | Model | Candidate date | Filter/categories |
| --- | --- | --- | --- |
| Packing List | packing.lists | date | type = receipt; Purchases |
| Shipping Note | packing.lists | date | type = delivery; Sales |
| GRN/Vendor Return/DO/Transfers | stock.picking | review scheduled_date versus custom effective/backdate field | split by project operation type and direction |
| Invoice/Sales Return | account.move | date | move_type in out_invoice/out_refund; Sales and optionally Tax |
| Bill/Vendor Credit | account.move | date | move_type in in_invoice/in_refund; Purchases and optionally Tax |

Project-specific subcontracting, stock out and material usage models must be
identified and configured explicitly. Discovery does not infer accounting meaning.
Returns/reversals are separate transactions and are evaluated using their own
configured date. A selected Return button on the original record can additionally
block opening that operation. Wizard completion must be reviewed; generated
transactions and mapped detail writes are checked independently.

## Enforcement and default Odoo compatibility

- Only configured active rules are applied. All applicable rules are cumulative.
- Uses Odoo's `res.company._get_user_lock_date()` freshly on each operation. This
  respects current user/everyone exceptions, expiry and ancestor companies without
  caching a time-sensitive computed field. Time is evaluated using Odoo's standard
  transaction timestamp. Hard Lock uses the ancestor maximum.
- Date <= effective lock date is locked. Datetime targets are converted from UTC
  to the rule timezone, consistently for all users. Default timezone: Asia/Rangoon.
- Missing date policy defaults to Block while an applicable lock is active.
- Rule company must match the record's company. Company-less transactions are
  outside a company-specific rule; make the company field required in the owning
  business model if such transactions must never be created.
- Create/write are checked inside savepoints, including after actual defaults,
  computed dates and company changes. When Prevent Edit is enabled, pre-write
  checks protect old locked values and post-write checks protect new values.
  When disabled, date/company/filter edits are permitted by this extension.
  Mixed batches roll back completely on violation.
- Prevent Edit covers explicit writes, including system business updates; exempt
  only fields reviewed as harmless. Chatter models are not patched. Detail add,
  edit, delete and reparent operations are blocked when Prevent Edit is enabled.
- Rules are read with sudo, while the original action/CRUD runs under its original
  environment and ACLs. No context bypass flag or blanket administrator bypass.
- Registry-local method wrappers follow Odoo's base_automation pattern and preserve
  the original method chain. Configuration changes signal registry reload to other
  workers. No files in core are changed. Module uninstall rebuilds a clean registry.
- UI buttons stay visible and fields may remain editable until Save/action. Server
  errors explain the violated rule and lock dates. UI hiding is not a security layer.
- Raw SQL writes bypass ORM enforcement, as they do Odoo ORM access checks. Do not
  use SQL to update protected business records.

### Accounting flow choice

The extension refuses closed-period operations; it does not silently move a custom
transaction date. On account.move, Prevent Create can intentionally reject drafts
that standard Odoo would accept and date-adjust later. Leave this unchecked if
draft creation should remain standard. Default accounting checks still run after
our checks; an exception for one category never overrides another or Hard Lock.

## Testing

Tests cover inclusive dates, CRUD, atomic batches, selected action methods,
configuration changes, category combinations, exceptions/expiry, hard lock,
user/company/ancestor scope, date/company/filter bypass attempts, direct detail
CRUD/reparenting, timezone boundaries, field exemptions and default accounting
lock behavior. Run on an isolated database:

    odoo-bin -c TEST.conf -d TEST_DB -i transaction_lock_control --test-enable \
      --test-tags /transaction_lock_control --stop-after-init --no-http --without-demo=all

Restart Odoo after placing this addon on addons_path, update the Apps list and
install Transaction Lock Control. Never point a test installation at a live DB.

## UI label update — 2026-11-03

Lock All Business Fields is now labelled Prevent Edit. The technical field
`lock_all_fields`, saved settings and enforcement are unchanged. Restart Odoo and
upgrade this module to refresh the field label/help and detail-tab text.

Performance review and local before/after measurements: see PERFORMANCE.md.

## Self-reference discovery fix — 2026-11-04

Discover skips detail relations pointing back to the main model (for example
project.task child_ids / parent_id). These records are evaluated independently
by the main rule; this does not add parent-to-subtask lock inheritance. Manual
self-model detail bindings remain invalid.

## Checkbox behavior correction — 2026-11-04

Prevent Create off allows creation under this rule. Prevent Edit off allows all
edits, including date/company/filter fields and configured details. Removed the
unconditional protected-field write check that contradicted this setting. Selected
actions still evaluate current record values at invocation; Prevent Delete is
independent. Other active rules, Odoo ACLs and core constraints still apply.
