# Sales Terms & Conditions Templates

Version 18.0.2.1.0 â€” Odoo 18 â€” LGPL-3.

## Setup
Restart Odoo and install/upgrade sale_terms_template. Open Sales > Configuration >
Terms Templates as a Sales Manager. Each template has a company, sequence,
static sale.order domain and sanitized HTML content. Enter a Python-style domain literal directly, e.g. [('partner_id.name', '=', 'Min Thu')].
Python expressions and context variables are not supported.

Lowest sequence wins; equal sequences use the oldest template. Put the empty
[] domain default last (e.g. sequence 1000). Archived templates are ignored.
New quotations automatically use only empty-domain defaults. Save a draft and
click Apply Terms to select using its current data, including amount/products.
The confirmation warns that existing text will be replaced. If no rule matches,
standard Odoo/company/quotation-template terms apply. Confirmed and cancelled
orders cannot apply templates. Conditions do not automatically refresh on edits.

Applied Terms Template records the source; Terms & Conditions remains an editable
HTML snapshot. Changing a template never rewrites existing SO notes. Changing a
saved draft's company refreshes its default. Explicit note values and duplicated
notes are preserved. Report QWeb renders the HTML with shared typography/table
styles; XML is never executed from template text. Check printed PDF appearance
with your actual terms before rollout.

Sales users can read templates, Sales Managers can manage them, and a global
company rule restricts access to allowed companies. Domain matching uses current
user access rules and no sudo. Company is enforced independently of the domain.

## Upgrade from 18.0.1.0.0
Back up the database and restart Odoo before upgrading. The versioned post-migrate
script copies enabled legacy company HTML into default template records with
sequence 1000. Legacy fields remain hidden for the transition release. Existing
SO notes are preserved. Upgrade was tested on the separate test database; the
working v18e_mlh_01 database has not been upgraded by this task.

## Validation
Tests cover defaults, snapshots/manual edits, conditions/priority/archive,
invalid domains, company separation, standard fallback, explicit notes,
confirmed-order protection, report HTML, form creation, company switching,
and amount conditions. See sale_terms_template_v2_verified.log in the workspace.

## Performance cleanup (18.0.2.1.0)
Removed the unused Domain Builder model helper and obsolete legacy company
validation. Legacy company fields and migration remain to preserve old data.
Template selection batches companies/orders: one template search per operation,
one matching query per conditional rule/company, stopping when all orders match.
Domains are fully validated when saved; runtime selection parses the literal
without redundantly compiling a validation query. Standard fallback calculation
runs only for unmatched orders. Empty batches perform no template search.
A regression benchmark compares batched selection with individual selection for
12 orders. Timing/query counts depend on rules and installed modules; no claim
is made about arbitrary user-written domains or production throughput.
