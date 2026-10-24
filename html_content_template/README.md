# HTML Content Templates

Standalone Odoo 18 module; no dependency on sale_terms_template, Sales or Accounting.

## Configuration
Install html_content_template, then open HTML Templates > Templates as a Settings
administrator. Set Company, Model, Field to Apply, Domain, Sequence, HTML Content.
The field must belong to the chosen persistent model and be writable HTML.
Writable stored computed fields such as sale.order.note are supported; related
fields, readonly fields and non-persistent computed fields are rejected.

Examples:
- Sales: model sale.order, field note, domain [('partner_id.name', '=', 'Min Thu')]
- Customer invoices: model account.move, field narration,
  domain [('move_type', '=', 'out_invoice')]

Use [] as a fallback with a high sequence (1000). Lowest sequence wins per target
field, then oldest template. Multiple destination fields can be filled in one
operation. Model-specific domains are validated; dynamic Python expressions are
not executed. Content is sanitized HTML, not an executable QWeb/XML template.

## Applying
Save records, select them in list view or open a record, then choose
Action > Apply Terms. One contextual server action is managed
per configured model. A browser refresh may be needed after first configuring a
model. Matching content REPLACES the destination field. No match leaves existing
content untouched. This release does not automatically apply on create/change.
Only draft/sent sale quotations and draft account moves can be updated by this
action; other models follow their own ORM write constraints.

Company_id is independently enforced when the target has that field. Shared
records with company_id=False are not matched. For models without company_id,
only the active company's templates apply. Per-record partner language is used
where available. Later template changes do not rewrite applied snapshots.

Current user's model, record and field write permissions apply. Content writes
never use sudo. Settings administrators alone configure templates. Limited sudo
is used to manage contextual action metadata and count configured model bindings
across company rules. Model fields keep their normal access restrictions.
Archived/deleted templates remove the contextual action when none remain active.
Uninstall removes generated actions through the uninstall hook.

## Deployment
Restart Odoo, update Apps List, install HTML Content Templates. No live database
installation is performed by source delivery. sale_terms_template is untouched.
This module does not change report layouts: existing reports must already render
the chosen destination HTML field. Generic automatic triggers and model-specific
PDF designs are outside this version.

Integration tests cover domains/priority/snapshots, company isolation, invalid
configuration, binding lifecycle/server-action execution, no-match preservation,
access denial and optional sale.order.note/account.move.narration destinations.

## Form header (18.0.1.0.2)
Apply Terms is also generated in configured models' standard form headers.
Buttons appear only for saved records; Sale/Invoice buttons require draft states.
The button asks before replacing fields. Generated extension views reference the
same server action and leave original views unchanged. Archiving/deleting the
last active template removes buttons and action; uninstall removes both.
Creating an active template creates its form buttons. Forms installed later
can be picked up by creating/re-activating a template for that model. Highly
custom primary forms that replace their inherited header may need adaptation.
Header generation, uniqueness and cleanup are covered by integration tests.

## Production review — 18.0.1.1.0
- Batch prefetch is retained for record/partner language reads.
- Default [] rules reuse the access-checked company-filtered records without a
  target search. Conditional rules query only remaining candidate IDs.
- Template translations are read once per template/language within each call.
- Identical multi-field payloads are grouped into one ORM write, preserving
  access checks, computed fields and model-specific write hooks.
- Content-only edits do not read model IDs or synchronize header views.
- Explicitly selected archived records are supported; include an active domain
  if you wish to exclude them.

Fresh-install distribution: historical migration scripts are intentionally excluded.
No global cache, direct SQL content writes, or target-record sudo is used.
Validation is integration testing on a separate database, not a production load
benchmark. Complex domains and other installed custom modules affect runtime.

Fresh installation is the supported deployment flow. No existing database migration is included.
