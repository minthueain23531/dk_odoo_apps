# Dynamic Approval Matrix

## Company notification settings (17.0.1.8.0)

From 17.0.1.8.1, the same options are also available under **Settings > Dynamic
Approval** for the selected company. Save the settings after changing them.
Both settings screens read and write the same company fields.
Administrators can also open these settings directly from **Dynamic Approval >
Configuration > Settings** (17.0.1.8.2).

Open **Settings > Users & Companies > Companies**, select the company, then open
**Approval Notifications**. Web Notify controls the existing popup plus Discuss
inbox delivery. Email Notification independently queues an email for the next
eligible approvers. Enable either, both, or neither. Approval requirements are
unchanged, including when both are disabled. Existing companies default to Web
Notify enabled and Email disabled. The approval document's company determines
the settings, not the current user's selected company.

Emails use the standard outgoing queue (never synchronous SMTP during approval).
Configure the company email/sender, approver emails, outgoing server and a
reachable Odoo base URL. The editable template is **Dynamic Approval: Next
Approver** under technical email templates. Its document link requires normal
Odoo login and document access. Recipients without document access are skipped;
missing email addresses are logged and do not block approval.

Administrators can inspect failed/queued messages under **Settings > Technical >
Email > Emails** and retry failed messages there. **Approval Email Queued At** in
the transaction's approval levels records queue creation, not successful SMTP
delivery. Changing company settings affects subsequent notification attempts;
it does not recall queued mail or automatically resend past approvals. Email
and Web Notify track delivery separately to avoid duplicates on repeated calls.

Odoo 17 module by **devkid**. Configure sequential approval on existing public
`type="object"` form buttons without writing a model-specific Python override.

Requires the companion **web_push_notify** addon by devkid, which depends only
on standard Odoo Web, Mail and Bus. No dependency on the original `web_notify`.
The production release contains these two addons; the partner sample and isolated
test-fixture addon are optional development examples, not runtime dependencies.

## Upgrading from 17.0.1.6.0

Put both addon folders on the addons path, install `web_push_notify`, and upgrade
`dynamic_approval_matrix`. Restart every Odoo worker and reload connected clients
to replace the former popup listener. Do not uninstall `web_notify` merely because
this addon no longer needs it: other installed addons may still depend on it.
Existing Discuss messages remain intact. New deliveries use one shared message
per approval step, with one inbox notification for each eligible recipient.
The `_get_approval_notification_message()` override hook is unchanged, and the
legacy `dam_display_notification` client-action tag still uses a fixed 60 seconds.

## Setup

1. Install the module and restart the Odoo/PyCharm server, then refresh the browser.
2. Give setup administrators **Dynamic Approval / Approval Manager** access.
3. Open **Dynamic Approval → Approval Matrix → New**.
4. Select Model, Button, Company and a literal Domain (`[]` means every document).
5. Choose **None** for level-only approval, or **Amount** and the numeric amount field.
6. Add approvers and unique positive level numbers. Save.

The layout follows the original matrix: Status, Model, Domain, Remark, Active,
Company, and editable approver lines, with Button selection added. The numeric
field is configurable so different models can use different amount fields.

## Clicking the original button

The button stays visible. The first authorized approval click creates a transaction. If the clicking
user belongs to the current level, that click opens a **Signature + Remark** wizard.
Both are required. Only clicking **Approve** in that wizard completes the level;
closing or cancelling it does not approve. A user outside the current level gets
an Access Error explicitly stating that they cannot approve, with the current
level and approver names. No business action executes. Each signed wizard approves **only one** level, even when the same
person appears at several levels. One listed approver is sufficient per level.
All applicable levels must finish before the original method is called, using
the final approver's normal permissions and the original arguments/context.

For Amount rules, all levels whose inclusive range matches must approve, sorted
by level. Maximum zero means unlimited. Limits use company currency; monetary
fields are converted using the document currency/date. Level numbers may have
gaps. An uncovered amount or overlapping matching matrices blocks execution.

A dynamic form panel shows approvers, statuses and history. It previews setup
before the first click and shows snapshotted transaction steps afterwards.
Refresh updates another user's progress. Times in this panel are explicitly UTC.
Managers can also view read-only transactions from the application menu.
The transaction list/form shows Model (`ir.model`), Technical Model, Record ID,
Document Name and Button/Method. Each approved step permanently stores its PNG
signature and remark, visible in the panel/history and transaction detail forms.
Existing unsigned approvals are retained as historical records, never given a
fabricated signature. Existing document names are backfilled on upgrade.

Wizards are server-issued and bound to their creator, transaction and level.
The target, original button arguments and context cannot be edited through RPC.
Submission rechecks permissions, current level, document version and duplicate
use under the same database lock as approval. Failed business actions roll back
the signature approval; the user can correct the issue and retry.

Pending approvals are invalidated by a change to the selected raw amount,
currency, company or snapshotted domain applicability, not every document edit.
None rules do not track an amount. Fixing a missing bill date or changing a remark
preserves completed levels. A failed final business validation rolls back only
that submission, so the final approver can retry after correcting the document.
Date changes alone do not revalue an already-started approval cycle.
Completed execution cycles still use the document timestamp to detect a new cycle.
Pending cycles are preserved as superseded history. Setup edits do not rewrite
an existing transaction's approver list. Archiving a rule does not bypass an
unchanged pending request. A completed document version cannot execute twice.

## Scope and coexistence

- Public object buttons on persistent business models in active form views.
  Transient-model, action-type, inline x2many and core system-model buttons are
  excluded. Methods discovered at registry startup can be configured immediately;
  after adding new button code/views, restart **all workers**, then Refresh Buttons.
- Two visual buttons calling the same model method share approval protection.
- A guarded bulk operation must be opened one document at a time; partial batch
  execution is deliberately blocked.
- API/internal method calls cannot collect approval or bypass pending steps.
  Configure rules carefully for methods also used by automated jobs.
- A returned modal wizard is preserved and recorded as Approved—Wizard Pending.
  Its separate completion methods need their own rules if directly callable;
  guarding an opener does not secure unrelated downstream methods. Custom wizard
  workflows require integration testing before enabling a production rule.
- No dependency on or modification to `payment_approval_matrix`. Existing old
  approval Python overrides still execute normally. Do not enable both matrices
  on the same document/button unless two independent approval workflows are intended.
- When a signed level completes, eligible internal approvers in the next level
  receive a Web Push Notify popup and Discuss inbox message with Open Document.
  Recipients must be active, belong to the company and retain document read
  access. Only the next level is notified, once per step; transaction rollback
  also rolls back the notification. The current approver sees a one-minute
  confirmation naming the next level and its configured approvers.
- Approval popups automatically close after **60 seconds per browser**, including
  while hovered. Manual close and Open Document also dismiss the popup. This never
  deletes or marks the persisted Discuss/Inbox message as read. Other modules'
  notifications are unchanged.
- Approval execution errors roll back to a savepoint, preserving earlier levels.
  Business validation text appears in a red notification; unexpected exceptions
  show a developer-contact message, with full details only in server logs.
  Permission errors remain Access Error dialogs. Database concurrency failures
  still use Odoo's normal retry handling.
- Reject requires the current level approver's signature and remark. Rejected
  buttons stay blocked even after document edits or rule archival; clicking
  again shows the rejecting user, level, time and reason.
- Approval Managers (including the configured Administrator) can use **Reset
  Approval** on the form panel or transaction. A reason is mandatory. Only the
  selected pending/rejected button is reset; signatures/history remain intact.
  The next click starts a fresh cycle from the current setup. Executed approvals
  cannot be reset, and resetting never changes the business document state.
- Business list views append **Approval Status** for configured models. The
  first button to start an approval transaction remains the anchor; its latest
  cycle supplies Not Started, Level N Approved, Done or Rejected. Reset preserves
  that anchor. Hovering shows the button name. This display column is not a
  business-model field and does not provide server-side sorting/filtering/export.
- List status uses one batch RPC per visible page (chunks of 2,000 for unusually
  large pages), including expanded groups. Indexed first/latest-cycle lookups
  avoid loading signatures/history or issuing a query for every row. Guarded
  buttons skip the extra pending-request query when active rules already exist.
- No delegation, activity/email or automatic approval features are included.

## Security and lifecycle

Tables: `dam_button`, `dam_rule`, `dam_rule_line`, `dam_request`, `dam_step`,
`dam_event`, `dam_lock`, `dam_approval_wizard`, `dam_reset_wizard`, plus explicit `dam_*_user_rel` relations. No names overlap
the old module. Users cannot write approval transactions/history through RPC.
Only managers edit setup; approvers retain normal document permissions. The
read-only panel checks document access and company before returning history.

Registry-local method wrappers use an internal Python dispatch capability, never
a client-context skip flag. PostgreSQL conflict serialization prevents concurrent
approval requests from silently executing a document twice. Errors propagate so
Odoo rolls back the final approval together with a failed business method.

No dynamic business-model fields or persistent edits to original form views are
created. Uninstall restores wrappers and Odoo removes owned models/views/assets.
Restart all workers and reload open browser tabs after uninstall. Uninstall deletes
this module's setup and history: archive rules instead if history must be retained.

## Status field for custom addons

Inherit `dam.approval.status.mixin` alongside the business model and depend on
`dynamic_approval_matrix`. It supplies the non-stored Char field
`dam_approval_status`. This uses exactly the list column's first-button/latest-cycle
logic, with stable keys: `not_started`, `level_1_approved`, `level_2_approved`,
`level_N_approved` (any configured level), `done`, and `rejected`. It is not the
business document state. Reset maps to `not_started`; all-approved business
wizards remain at the last approved level until execution completes.

Include the field invisibly in the view, then use an Odoo 17 modifier such as
`readonly="dam_approval_status == 'level_1_approved'"`. This exact comparison no
longer applies at Level 2 or Done. View readonly is a UI behavior, not a server-side
write restriction. The non-stored field does not provide database search/grouping.

The private batch helper `dam.rule._get_document_approval_status(records)` returns
`{id: {'key': ..., 'text': ..., 'button': ...}}`; missing records have no cycle and
mean `not_started`. Document access and selected companies are checked. Request
and step changes invalidate computed status caches without business-document
writes. Approvals reload the calling form; panel resets also refresh clean forms.

System-model buttons remain excluded unless a custom model explicitly declares
`_dam_approval_buttons = ('method_name',)`. Only those methods are discoverable on
that model. The optional `dam_partner_internal` sample demonstrates this for
`res.partner` with a Sample Approval button and the phone modifier. Install the
sample, restart Odoo, refresh the button catalog, and configure two levels to see
the intermediate `level_1_approved` value. Approvers need normal contact write
access as well as membership of their approval level.

## Verification

An isolated Odoo TransactionCase fixture covers sequential approval, wrong users,
multiple approvers, sparse levels, amount coverage, history ACLs, forged context,
direct API calls, changed documents, configuration snapshots, archived rules,
duplicate clicks, batch rejection, error rollback, modal results and view cleanup.
The fixture is kept outside the installable module so production gets no test models.
