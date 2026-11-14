# Automatic photo publication entry — replacement design draft

**Status: replacement design draft; earlier broad construction audit was FAIL/UNKNOWN, superseded for development entry selection by the bounded Docker-only observation below.** User chose automatic completion and then authorized development data/configuration changes or fresh synthetic implementation. This addendum proposes replacing the request-local entry in the approved 2026-11-07 native photo insertion design. That design's protected writer, authorization, receipts, exclusion, immutable bytes and conservative recovery requirements remain binding. Existing Tasks 2–9 must be reconciled with a revised implementation plan; the local endpoint remains default-off and always refuses. Observation is not publication acceptance.

## Intent and non-goals

A clinician manually saves an annotation; publication completes automatically without an operator processing every submission. Authentication/request work must never be committed or rolled back by the publication coordinator. Saved means known durable publication, not accepted input or queued work.

No core/runtime patch, active local.db swap, privileged File insertion, new service/dependency, automatic uncertain-save resumption, general original ingest, frontend activation or production deployment is proposed. User selected automatic SUBMITTED rediscovery through the existing Frappe scheduler and authorized development data/configuration changes. Its implementation remains subject to revised-plan review, not another routine development-permission request. Synthetic original creation stays confined to the exact approved disposable site and explicit server mode, through the same protected coordinator/writer.

## Approaches and chosen direction

- Same HTTP handler owning two commits: blocked. Bounded audit could not prove unrelated request work absent; advisory counters and empty callbacks do not establish ownership.
- Operator-invoked fresh coordinator: simplest synthetic execution candidate, but changes clinician Save into an operator-dependent workflow. User did not select it.
- **Selected direction: ordinary authenticated submission, after-commit notification, worker launcher, fresh coordinator process.** Additional launcher machinery preserves automatic completion without assigning publication to an unproved HTTP/job transaction. Fresh-process bootstrap itself still needs proof.

## Revised transaction and result contract

1. **SUBMITTED:** authenticated POST validates a bounded request and stages one protected submission in the framework's ordinary request transaction. It uses no helper commit/rollback and performs no File/content creation or protected-key reservation. The framework alone finalizes this request. Actor/site come from authenticated server context, never payload or launcher input.
2. **PREPARED:** an extension-owned command in a fresh process, after ownership and authorization checks, allocates durable intent/key under the existing operation/target exclusion protocol. Commit exactly as required by the prior design, before protected bytes.
3. **PUBLISHED:** same owner retains exclusion, reauthorizes/rechecks version and creates bytes/File/version/linkage/receipt evidence, committing the known publication outcome atomically.

This expressly changes TWO request-local commits into THREE durable stages overall. User explicitly selected this design amendment with bounded payload storage initially restricted to synthetic data in the approved disposable stack; the complete replacement design still requires approval. SUBMITTED is payload acceptance only, not a pending protected key or saved draft. PREPARED/PUBLISHED semantics and crash gap remain unchanged; no RECEIPTED phase or filesystem journal is added.

POST returns `pending` plus opaque operation identity, never `saved`. Clients retain the same request key through retries. A transport failure or missing commit acknowledgement is unknown, not permission to use a fresh key. Status lookup/replay resolves the original tuple after current authorization. A known published outcome returns its immutable version only after coherent linkage/byte evidence and present access checks; failure after publication cannot turn it into permission for a second append.

Concurrent submissions for the same expected draft version may be accepted; coordinator conflict checks remain authoritative before bytes. Idempotency uses site, authenticated actor, kind and request key plus semantic payload digest; mismatched reuse rejects. Version 0 is valid for a fresh draft; booleans/negative values are not.

## Authenticated submission and protected storage

The publication POST authentication/session/CSRF contract must be pinned before implementation; existing cookie-based read harness and unsupported live proxy do not supply that contract. Anonymous, disabled and non-clinician actors deny; draft edits require current authorship and native permissions. All currently enabled clinicians retain the approved domain-read policy.

**Selected integration for the implementation-plan draft:** same-origin native clinician `sid` session and explicit session-bound `X-Frappe-CSRF-Token`, over HTTPS or the approved loopback tunnel; reject alternate Authorization transport. Native CSRF processing has exemption paths, so the endpoint additionally requires a nonempty stored token and matching explicit header while preserving native checks. Token acquisition/rotation and rejection behavior require native verification; no CSRF exemption or elevated service identity is proposed. These are proposed builder decisions for plan review, not claims of working authentication.

Proposed schema amendment: extend the feature-specific operation record with SUBMITTED and bounded protected payload fields, initially without canonical key/generation/receipt. Transitioning to PREPARED adds authoritative reservation fields; ordinary operation CRUD remains denied. This is not a generic payload/job registry. Validation requires both stored content integrity and fresh domain authorization; digest equality is not authority.

Store canonical bounded annotation JSON, or canonical base64 encoding of validated synthetic image bytes, in separate Long Text operation payload fields as proposed below. Queue arguments, command line and job results contain opaque identity only, never patient identifiers, payload, credentials or capabilities. Encoding and size caps are explicit development proposals requiring revised-plan review.

Submission content is PHI and needs explicit size/schema/nesting/image-dimension limits, authorization and retention/purge policy. Submitted payload must survive queue loss and allow processing without filesystem staging. The initial slice is restricted to synthetic data on the exact approved disposable site; its residue follows existing exact-stack disposal authority, not an in-app deletion bypass. Production payload retention, purge authority and any terminal-content deletion remain unresolved activation blockers; no automatic deletion or indefinite production retention is authorized. Off-device consistent DB/private-file backup and tested restore remain activation blockers.

## Notification and worker launcher

Register native enqueue-after-commit notification within the submission transaction. No job is runnable on behalf of an uncommitted submission. Native after-commit delivery is notification, not a durable queue-outbox guarantee: queue failure, callback failure or lost notification can leave a committed SUBMITTED operation.

The existing worker executes a launcher only. It must not publish, choose actor/site/payload, change operation outcome or claim saved. It starts a fresh executable process using a fixed server-selected command and argument vector; no shell interpolation, client command/path/environment or inherited Python/Frappe transaction. No new service/system cron or deployment changes are implied; the existing scheduler's proposed extension trigger needs separate implementation/deployment approval. Exact supported executable/container invocation and process/resource/concurrency limits must be established before implementation.

Duplicate notification/worker retry may launch multiple contenders; stable operation/target locks and durable state checks prevent duplicate publication. A child may process only currently SUBMITTED work; it never repeats PREPARED insertion. Launcher failure/timeout is not proof the child stopped or rolled back. Do not release or bypass writer exclusion because queue state says failed, and do not infer ABORTED from a subprocess exit code.

User selected automatic repair of lost notifications through the existing Frappe scheduler. A bounded rediscovery trigger may inspect committed SUBMITTED identities and enqueue launcher notifications only; it never publishes, adopts an actor for publication, reserves a protected key or resumes PREPARED. Duplicate notifications remain harmless only through coordinator exclusion and state rechecks, not scheduler/queue status alone. No new service or system cron is proposed. Scheduler/queue unavailability can delay processing; pending must remain truthful, and automatic eventual completion is conditional on their recovery and current authorization. Exact supported trigger, scan bounds/backoff and bounded launch concurrency are technical design details still to establish. Authorized manual redispatch may supplement, but cannot be the normal lost-notification recovery mechanism.

## Fresh coordinator construction boundary

Use an extension-owned native command, not ordinary job callback or generic bench execute wrapper. A fresh process is necessary but insufficient: command discovery/imports/bootstrap, context creation and connection acquisition must be side-effect-audited against pinned installed configuration. Unsupported wrappers, unknown extensions/hooks or unrelated writes/callbacks block operation before any preparation commit.

**Development entry selection after Docker observation:** use an extension-owned fresh Python module/process following the tested direct init/connect sequence, fixed cwd `/home/frappe/frappe-bench/sites`, rather than a Bench-discovered command. The diagnostic's earlier bench cwd caused a confirmed relative logger path failure; sites cwd is part of the native runtime contract. This choice avoids adding untested app-command discovery to the publication path. The actual module, server-bound actor lookup, reauthorization and operation transactions still require implementation/tests; no request-local DB is swapped.

The coordinator creates its sole Frappe context/connection from outset; it never replaces an active request DB or inherits worker connection authority. Initial framework bootstrap must not perform publication under Administrator. Server-bound submission actor is adopted through supported native context APIs and freshly checked for enabled status, clinician role, draft authorship, patient/original binding and native permissions before preparation and publication. Runtime API/command feasibility remains UNPROVEN; refusal is required if construction cannot meet these obligations.

**Superseded candidate:** the earlier Bench-discovered command is not selected. The implementation entry will be `python -m clinic_photos.publication.process --site <server-selected-site> --operation-id <opaque-id>`, from the fixed bench sites directory. It initializes context, calls native `connect(set_admin_as_user=False)` once, narrowly resolves the committed submission actor, then adopts that actor through native `set_user` before authorization/publication. The initial internal identity lookup grants no domain/publication authority. Neither generic `bench execute` nor app-command discovery belongs to this entry. The process module itself still requires runtime tests; the direct native bootstrap has bounded observation evidence below.

Existing scheduler-event registration is a plausible rediscovery trigger, but its native wrapper writes scheduler metadata/logs in its own transaction. Those writes remain outside the child coordinator and are not claimed absent. Registration/synchronization, enabled scheduling and queue health need separate deployment evidence; a running scheduler container alone is insufficient.

After entering the owned context, all mutations/callbacks must belong to the named operation. Known permitted read/bootstrap work must not be assumed write-free from counters alone. No commit/rollback is permitted to clean inherited unrelated work. Command completion must not hide a final framework commit or deferred mutation needed for success. Subprocess output uses bounded PHI-free outcome markers; durable operation/domain evidence, not stdout, establishes saved.

## Authorization, status and recovery

Status/replay must not expose patient existence, cross-patient matches, payload, storage paths or other users' operation metadata. Orchestrator's proposed boundary for approval: ordinary status/replay is restricted to the originating currently enabled clinician with current target authority; other clinicians use only existing authorized saved-domain reads, not pending-operation metadata. Submission actor governs idempotency and author-edit ownership. Revoked authors cannot publish or obtain a successful replay through retained identity. Recovery inspection remains separately restricted to enabled clinicians explicitly allowlisted as operators, default empty. This status restriction is a proposal, not a user-approved role change or an activated endpoint.

PREPARED uncertainty still blocks automatic resumption/deletion/key reuse. SUBMITTED is eligible for a fresh execution attempt only after current authorization and integrity checks; state must be rechecked under operation exclusion before preparation. Unknown submission commit, unknown preparation commit and unknown publication commit are distinct inspection outcomes. Native callbacks, launcher retries and process status do not replace the durable journal.

## Verification and approval gates

- Pin POST authentication/session/CSRF; prove denied/revoked/forged actor behavior and same-key uncertain submission reconciliation.
- Approve payload schema/transport/storage/limits and protected submission access/retention; test no PHI in queue, argv, outputs or logs.
- Establish command/launcher feasibility and side-effect-free bootstrap/owned connection using exact source plus native evidence, not fake queue/counter proof. Unknown bootstrap blocks later tasks.
- Native tests: request rollback yields no runnable publication; committed submission with lost notification stays discoverable pending; duplicate launches, revocation before both phases, draft conflict, child survival after timeout, crash/unknown commit and queue replay cannot produce duplicate versions or expose bytes.
- Retain all prior File/receipt/alias/concurrency/HTTP/disposal gates. No full-suite claim from local refusal tests; no live activation or frontend pending-state implementation approved here.

## Builder decisions resolved for revised-plan review

1. Exact supported fresh command/bootstrap and launcher invocation; operation-bound connection proof.
2. Exact POST authentication/session/CSRF and status visibility contract.
3. Payload schema, numeric limits and image transport/DB representation; initial synthetic-only storage/disposal is selected, while production retention and purge authority remain activation blockers.
4. Concrete existing-scheduler rediscovery trigger and bounded launch/resource behavior; automatic SUBMITTED notification repair is selected, never automatic PREPARED recovery.

The proposals below resolve these four design choices for review; their runtime verification belongs to implementation. They are not approved merely by being written here. Production retention/purge and real-data activation remain separate unresolved policy gates.

## Concrete development contracts for revised-plan review

The following are **orchestrator-proposed development defaults**, not values previously specified by Clinic requirements. They resolve the former unbounded-input blocker for the bounded first slice; changing them requires matching contract/test changes, not arbitrary client flags. Production clinical suitability remains separate.

### Request and annotation schema

- Public JSON request cap: 12 MiB UTF-8, checked before parsing an inner string payload or decoding base64. Request key: 16–128 ASCII characters from `[A-Za-z0-9_-]`. Document references: nonempty, no surrounding whitespace, at most 140 characters. Operation IDs: 32 lowercase hex characters generated server-side.
- Canonical annotation cap: 256 KiB UTF-8. JSON nesting cap: 8 containers; reject cycles/non-string keys/NaN/infinity/bool-as-number. At most 512 shapes, IDs unique and 1–64 ASCII alphanumeric/underscore/hyphen characters.
- Exact annotation envelope: `{schema_version: 1, coordinate_space: "normalized", shapes: [...]}`; reject unknown envelope/shape fields. Initial backend shape types: arrow, ellipse, rectangle, text. Freehand is deferred, not silently accepted.
- Every shape has `id`, `type`, `color` (`#RRGGBB`), `x`, `y` in `[0,1]`. Arrow additionally has `end_x`, `end_y` in `[0,1]` and `stroke_width` in `(0,0.1]`. Rectangle/ellipse have positive `width`, `height` and `stroke_width` in `(0,0.1]`, with x+width/y+height <=1. Text has nonempty plain `text` (<=1,024 characters) and `font_size` in `(0,0.25]`; no HTML/SVG or executable markup field. Width/font/stroke values are normalized image-coordinate values, not viewport pixels. Undo/redo/zoom/pan are frontend state and not part of persisted geometry.
- `expected_version` is an integer >=0, excluding bool. Client-supplied owner, site, actor, generation, key/path, permission and synthetic-mode fields reject.
- Synthetic image transport: JSON `image_base64`, strict standard base64, no data-URL prefix or whitespace; decoded bytes <=8 MiB, PNG/JPEG only, each axis <=8,192 and total pixels <=40,000,000. Validate actual native-decoded format/dimensions without re-encoding; preserve original bytes. Stricter native File/security limits still win. Synthetic patient field is a validated existing test Patient reference; original/draft/expected-version/annotation fields are absent for this kind.
- Persist canonical annotation JSON or canonical base64 in separate Long Text payload fields on the operation, never a File before PREPARED. The worker reconstructs the immutable typed request and recomputes the semantic digest from decoded bytes/canonical JSON. Direct dataclass construction enforces the same invariants as public validation.

### Outcomes, access and scheduling

- Durable states: SUBMITTED, PREPARED, PUBLISHED, ABORTED, QUARANTINED. External outcomes: pending, saved, conflict, rejected, unknown. A well-formed accepted operation always has its opaque ID; pre-operation authentication/validation errors raise fixed-message native exceptions instead of fabricating an operation ID.
- Definite authorization/conflict rejection of SUBMITTED work may record ABORTED with rejected/conflict outcome and no key/bytes. PREPARED uncertainty remains pending/unknown; it never returns to SUBMITTED or runs insertion again. Published replay requires current actor/domain authorization and consistent durable evidence.
- Ordinary operation status/replay is actor-only and requires current target authority; clinician-wide saved-domain reads remain unchanged. Separate recovery inspection additionally requires the existing default-empty operator allowlist. Generic operation access remains denied to ordinary clinical clients. **User-approved exception:** trusted Administrator sessions may use native operation read/list/report/export, including staged annotation JSON and base64 image payloads. This is an explicit confidentiality-policy exception, not clinician authority, permission to publish, mutable protected File access or an automatic operation purge facility. Ordinary operation mutation still requires the server-owned repository boundary.
- Existing scheduler hook runs every minute, scans at most 25 SUBMITTED operations, and enqueues opaque IDs only. A site-scoped Redis cursor over `(creation, name)` rotates batches fairly, wraps at end and expires after 24 hours. Losing cursor may repeat notifications but cannot lose durable work; unavailable Redis/queue means pending, not saved. No publication state mutation is needed in the scheduler.
- Launcher uses the existing short queue, waits synchronously for one child, uses the installed venv executable and fixed sites cwd, and passes only site/opaque ID. Child environment is server-constructed from fixed runtime paths plus HOME/LANG/LC_ALL/PATH/PYTHONIOENCODING, never request headers/payload. Use a 120-second child deadline with 5-second termination grace and 64-KiB bounded diagnostic output. Timeout is unknown; durable state and retained OS exclusion, not exit code, determine whether another attempt is permissible.
- Parent queue work, scheduler metadata and Redis notifications remain outside the child database. Child holds operation then target locks through preparation/publication; duplicate launches load state under exclusion and never repeat PREPARED insertion. No lease/queue timeout is takeover authority.
- Development payloads remain synthetic and retained until exact-stack disposal; no automatic submission-content purge is introduced. Production retention/purge/backup approval does not block this development slice but does block any later real-data activation.

### Development database compatibility profile

Native Task 2 testing on MariaDB 11.8.9 established a compatibility requirement for the selected unique-collision recovery path: retain `REPEATABLE-READ` and configure `innodb_snapshot_isolation=OFF` on the isolated development database, inherited by fresh connections. With the modern default ON, the recovery locking read raises `ER_CHECKREAD` and the engine aborts the transaction; the journal correctly propagates that failure but does not provide successful collision replay. A controlled ON/OFF comparison captured the exact statement and stable connection identity, and OFF preserved caller-owned work until the caller's commit.

This is a development deployment-profile decision under the user's explicit data/configuration authority, not an application per-request isolation switch or a claim of compatibility with every MariaDB default. Persist and verify the profile across the existing development database restart; production compatibility and any other deployment require their own review. Never clear a failed transaction or automatically retry protected insertion to hide an engine abort. Evidence: `.superpowers/sdd/2026-11-08-frappe-async-photo-publication/native-task-2/20261108T084139Z-snapshot-comparison/REPORT.md`.

These contracts are included in the revised implementation plan for explicit review. They are backend storage/validation decisions, not approval or implementation of a new frontend design.

## Bounded construction audit outcome

Preserved report: `.superpowers/sdd/2026-11-07-frappe-durable-photo-save/entry-construction-audit/report.md`. Collected backend/worker/scheduler image, path, mount and source projections match. This closes that narrow equivalence gap, not complete executable/environment equivalence. Captured startup executes `_virtualenv` and imports multiple command modules before command callback; transitive/system/environment startup and actual first connection/query remain unproved. FAIL/UNKNOWN is absence of sufficient ownership evidence, not observed unrelated writes or proof every supported alternative impossible.

No further audit or runtime smoke test follows automatically. A discovery-only smoke run cannot alone establish complete bootstrap ownership or unblock implementation. Before proposing another experiment, require an evidence path that can settle the full construction claim within explicit authorization; otherwise retain this blocked draft rather than iterating inconclusive checks.

### Later simplified development observation

User requested Docker-only investigation and authorized disposable development data/configuration. Result: `.superpowers/sdd/2026-11-07-frappe-durable-photo-save/entry-construction-audit/docker-only/INVESTIGATION-RESULT.md`. Tested direct bootstrap adopted an enabled synthetic clinician, issued connection setup and two reads, and reported all four native callback queues empty. No target DML or explicit transaction-control statement was observed. Autocommit was disabled; absence of explicit BEGIN is not absence of an implicit read transaction.

Logging restoration and fixture deletion were verified in saved artifacts. The harness's final manifest write failed (top-level exit 1); subsequent reconstructed statuses are not original exit evidence. Parent independently checked target/readback/cleanup markers and raw log hash. This supports selecting the narrow direct process/bootstrap for development implementation, not universal startup cleanliness, HTTP authentication, queue delivery or durable publication acceptance. Stop the broad infrastructure investigation and address those remaining concrete obligations in the revised implementation plan.
