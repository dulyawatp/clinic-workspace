# Local appointment-write SIT design

## Intent and scope

Exercise booking, rescheduling and same-day check-in through the React UI on
the isolated `marley-sit.local` site. The user approved local Reception Patient
read access, local Company read access for doctor and reception roles, one new
fictional booking and its reschedule, and a separate same-day fictional
appointment for check-in. Both read grants are role-wide on the isolated site.
Preserve the existing future
appointment and all test records. No lab access, new MariaDB tables, deletes,
automatic retries, cancellation or practitioner scheduling.

## Approaches

1. Use native permission-checked operations with the existing private SQLite
   fence and explicit acknowledgement. This is the approved direction.
2. Bypass native permissions or call the generic status setter directly:
   rejected because these bypass authorization or controller validation.
3. Keep appointment writes disabled: safe, but does not exercise the
   requested workflows.

## Safety and behavior

Before implementation or deployment, verify installed metadata, current
effective permissions, controller side effects and same-day check-in behavior
against the pinned local runtime. Stop if the proposed role grant cannot be
limited to the isolated site or if required side effects exceed approval.
The Reception Patient read grant is a role-wide local permission change, not
merely access to one fictional record; document its scope and preserve evidence.
Server-supplied configuration must not substitute for permission to read a
patient. Do not silently grant any other permissions.

Before enabling each action, record an explicit matrix for REC and DOC of
Patient read, Patient Appointment create/read/write, every required linked
DocType and document, and relevant field permissions and masking. Booking,
detail and check-in must independently enforce Patient read as well as
appointment authority. If native behavior requires a further permission grant,
stop and ask rather than treating a configured identifier as authorization.

Implement separate default-off, exact-site appointment write gating and
explicit CSRF checks. Use native document create/write authority and strict,
synthetic-only allowlists; derive server-owned fields and reject unsafe
controller settings and targets. Preserve native validation; do not use the
unvalidated generic status setter. Restrict check-in to verified eligible
statuses on the site's current date, rechecking under the write transaction.
Use optimistic version checks for rescheduling and check-in.

The SQLite fence serializes only cooperating adapter writers, not Desk, jobs
or other native APIs. For rescheduling and check-in, acquire the fence first,
then lock the native appointment row, reread its version, date, status and
authorization, and validate and save in the same database transaction.
Document and test this lock order and interactions with native writers.
Determine the site's timezone and current-date source. Pin the installed
native check-in method and the exact eligible statuses, permitted reschedule
fields, configuration prerequisites and allowed linked-record, notification,
job and commit effects from runtime evidence. Stop if these cannot be bounded;
do not substitute a direct status update.

Readable appointments are not automatically writable. Only the new booked
record may be rescheduled and only the dedicated same-day record may be
checked in; the existing future fixture is never writable. Enforce these
target restrictions and a conservative local test budget, rather than
exposing a generally writable synthetic appointment list.
The budget permits two appointment creates, one reschedule and one check-in;
the separate same-day fixture requires its own creation.

Reuse the existing site-wide SQLite fence without schema changes. Use distinct
appointment operation kinds, receipt/alias namespace and acknowledgement token
codec so patient receipt enumeration cannot interpret appointment receipts.
Bind acknowledgements to actor, site, kind, key and durable receipt. Keep a
site-wide unresolved barrier through commit and response preparation until a
single validated acknowledgement; a lost or uncertain response requires manual
investigation, not an automatic retry or unlock. Maintain the existing patient
write guarantees. Validate cross-kind concurrency and collisions, including
generated aliases and configured native identifiers.

Persist dispatch and barrier before native mutation; commit the native
transaction, persist the operation receipt while leaving the barrier
unresolved, prepare the response, then resolve the barrier only with a
separately validated acknowledgement. The native database and SQLite are not
atomic: crashes and failures at each boundary must retain an unresolved
barrier or manual-investigation latch, including rollback and receipt failures.
An acknowledgement response can be lost after the server has resolved the
barrier; the client must nevertheless stop and require investigation. One
terminal acknowledgement transition may support idempotent duplicate requests,
but the client must not retry automatically.

Two failure modes are recorded so the operator response is unambiguous. An
authorization denial or unsafe side effect raised inside a fence-held request
(a native `PermissionError` from the adapter's `deny()`, or an
`UnsafeSideEffect`) is post-dispatch: the operation terminates as `UNKNOWN`,
returns `409 {"code": "WRITE_LOCKED"}`, and the site-wide fence blocks every new
key for every actor until an operator reconciles the ledger. A `modified` bump
by the nightly healthcare scheduler between the preflight read and the write
fails cleanly at the pre-fence version check with `409 {"code":
"STALE_VERSION"}`: that check runs before the durable budget check and before
the fence is entered, so no budget key is consumed and no latch is set, and the
client may re-read and retry with the fresh version. Only a bump that lands
after the fence is held and the row is locked converts to `UNKNOWN` and a
manual-stop latch.

Add operation kinds compatibly to the fence verifier, barrier derivation and
read-only enumeration while keeping patient codecs and enumeration unchanged.
Before new appointment events are written, drain or replace all older workers
that might reject the expanded ledger; verify patient reads, writes and pending
acknowledgements across the deployment.

Expose newly booked synthetic appointments via verified, bounded receipt-based
discovery with current-user native read and field checks; retain the existing
static allowlist and never expose native identifiers. Preserve the list's
five-field contract; use a separate detail contract for version and action
eligibility. Enable only appropriate actions, with clear read-only states.

## Verification and limits

Test gates, authorization, side effects, stale updates, same-day eligibility,
uncertain outcomes, cross-kind fencing, acknowledgement, receipt discovery and
role denial before deployment. Independently review the safety boundary. Then
deploy only to the isolated site and test actual create, reschedule, view and
check-in through React, verifying backend effects and preserving evidence.
If a save or acknowledgement is uncertain, stop all writes without retrying.
Report partial SIT results and any unsupported cases; this is not
production-ready and does not imply safe restore or delivery guarantees.
