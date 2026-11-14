# Automatic Durable Photo Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a development-only automatic photo-save pipeline with durable submission, a fresh native coordinator, protected first writes and truthful saved/pending outcomes.

**Architecture:** The authenticated POST stages SUBMITTED and lets the framework commit its request. After-commit notification and existing-scheduler rediscovery launch a fresh Python module using the tested native sites cwd. Only that child owns PREPARED and PUBLISHED transactions; native File authorization, exact bytes, receipts, exclusion and conservative recovery remain mandatory.

**Tech Stack:** Existing Frappe 16.53.0 / ERPNext 16.52.1 / Healthcare 16.6.1, Python stdlib unittest/subprocess/fcntl, native DocTypes/private File, existing MariaDB/Redis/short worker/scheduler. No new dependency or service.

**Spec:** `docs/superpowers/specs/2026-11-08-frappe-photo-publication-entry-design.md`, especially “Concrete development contracts for revised-plan review”; retained protected-write invariants in `docs/superpowers/specs/2026-11-07-frappe-native-photo-insertion-design.md`.

**Status:** User approved the revised plan (“approve”); execution started in `.superpowers/sdd/2026-11-08-frappe-async-photo-publication/progress.md`. Approval is NOT a claim the feature exists. Supersedes the old plan's HTTP-owned commits/clean-entry gate; retains the substantive journal/locks/writer/recovery work. User authorized development data/configuration changes and fresh synthetic implementation. No commits/staging/push, dependency installation or real-data activation. Preserve the existing specialist execution, mattpocock implement/TDD without installation, and separate Standards/Spec reviews.

## Global Constraints

- Saved means known durable publication, not accepted input or queued work.
- No core/runtime patch, active local.db swap, privileged File insertion, new service/dependency, automatic uncertain-save resumption, general original ingest, frontend activation or live deployment is proposed.
- Native File permissions and downstream vetoes remain authoritative. No File `ignore_permissions`, role grants to real users, client authority flags, transformation, implicit deduplication, truncating overwrite or abort-latch reset.
- SUBMITTED is payload acceptance only, not a pending protected key or saved draft. PREPARED/PUBLISHED semantics and crash gap remain unchanged; no RECEIPTED phase or filesystem journal is added.
- Current enabled clinicians may read saved domain records; only draft authors edit. Ordinary status/replay is actor-only; separate recovery operator allowlist defaults empty.
- User-approved Administrator exception: native operation read/list/report/export may expose staged payloads to trusted Administrator sessions. This does not permit clinical publication, protected File mutation or ordinary operation mutation/purge outside the server-owned repository.
- Synthetic original creation requires exact `clinic-photo-accept.local` and exact-True server `clinic_photos_synthetic_mode`; publication/submission/scheduler/process gates default off.
- Work on existing `clinic-photos-accept` development stack using fresh synthetic data/configuration as needed. No unnecessary unrelated-project destruction, broad prune, host sudo/tracing, new image pulls/builds or production-data copying.
- Unknown protection classification rejects ordinary uploads too; proved-unrelated behavior remains native. Administrator is not protected cleanup authority. PREPARED uncertainty never authorizes automatic insertion retry/unlink/key reuse.
- Single-host shared-sites-volume exclusion only; stable OS lock objects, no lease-only takeover. No implicit multi-host safety claim.
- A previous diagnostic observed one direct-bootstrap path with four setup/read statements and zero visible callbacks. It did not test HTTP authentication, CLI discovery, process ownership/publication or every cache branch. Its final command exited 1 during report serialization; reconstructed exit statuses are not original evidence.
- Production retention/purge/backup decisions do not block this synthetic development slice, but remain blockers to later real-data activation.
- Native-derived development compatibility profile: isolated MariaDB `REPEATABLE-READ` with `innodb_snapshot_isolation=OFF`, persisted and verified on fresh connections after restart. ON-profile collision replay failed and remains a preserved failure; no per-request transaction surgery or automatic protected-write retry.

## Review Focus

- Lost HTTP/queue acknowledgement: same actor/kind/request key resolves one durable operation, not another image/version (Tasks 2, 3, 9).
- Revocation between request, preparation and publication: reauthorize and deny before disclosing/publishing; captured identity alone grants nothing (Tasks 3, 4, 8, 9).
- Duplicate launch or timed-out parent with a surviving child: no second protected write, no PREPARED resumption (Tasks 4, 5, 9).
- Ordinary File dedup/rollback retargets a protected key: no committed alias or original mutation, including Administrator (Tasks 6, 7).
- Partial bytes or ambiguous DB commit: preserve durable intent/evidence, return unknown, never delete/retry automatically (Tasks 7–10).

## File structure and shared contracts

Paths below are relative to `frappe-apps/clinic_photos/`, unless prefixed `docs/`.

Shorthand `publication/` and `access/` paths mean `clinic_photos/publication/` and `clinic_photos/access/` within that app root; `tests/unit/` remains at the app root. For every task's focused RED/GREEN checks use `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p '<named test module>.py' -v` from the repository root. RED must fail on the intended assertion; GREEN requires exit 0 with no failures. Compile each changed Python file with `python3 -m py_compile <changed paths>`. No commit/staging step follows these checks.

- Modify existing `clinic_photos/publication/contracts.py`; create `publication/validation.py` for bounded annotation/image transport validation. `SaveRequest` remains immutable; public wire `image_base64` becomes internal `image_bytes` only after strict validation. Use `SubmittedOperation` for immutable server-bound submission identity, separate from `PreparedOperation`.
- Create `publication/{journal,submission,process,launcher,exclusion,writer,coordinator,inspection}.py`: operation persistence, HTTP staging/notification, standalone child entry, process launch/rediscovery, locks/owner, exact bytes/receipt, two-phase publication, restricted outcome inspection. Keep imports inert; no standalone scaffolding task.
- Modify existing `clinic_photos/api/photo_save.py`: keep `save(payload: dict | str) -> dict` as the public POST entry but replace its permanent clean-entry refusal with request staging, not coordinator commits. Add `status(operation_id: str) -> dict` and gated inspection wrapper.
- Create `clinic_photos/clinic_photos/doctype/clinic_photo_save_operation/{__init__.py,clinic_photo_save_operation.json,clinic_photo_save_operation.py}`; normal clinical client CRUD/list/export denied, with the user-approved trusted Administrator read/list/report/export exception above. Add operation protection lookup in `access/frappe_context.py`; preserve `access/permissions.py` native adapter.
- Modify only needed owner/first-insert/rollback/receipt seams in `access/{frappe_context,file_mixin,lifecycle,storage}.py`. Add scheduler hook in existing `hooks.py` without replacing current hooks.
- Add focused `tests/unit/test_async_publication_*.py`; extend existing native `clinic_photos/tests/test_publication.py`, isolated manifest/runner only with relevant native cases.

Exact public boundaries:

- `validation.validate_request(payload: dict | str) -> SaveRequest`; typed direct construction must enforce equivalent bounds, not bypass them.
- `submission.stage_submission(request: SaveRequest, actor: str) -> SaveOutcome`: no commit/rollback, File, byte write or key reservation.
- `journal.load_submission(operation_id: str, *, for_update: bool = False) -> SubmittedOperation`; `journal.prepare(submission: SubmittedOperation) -> PreparedOperation`; `journal.publish(operation: PreparedOperation, receipt: CreationReceipt, file_name: str, version: int | None) -> None`. Persistence helpers never commit.
- `process.run(operation_id: str, *, site: str) -> int`: fresh-context entry; only opaque IDs/site in argv. `coordinator.publish_submission(submission: SubmittedOperation) -> SaveOutcome` owns the two publication commits; returns outcome after durable verification.
- `launcher.launch_submission(operation_id: str) -> None`; `launcher.rediscover_submissions() -> int`. Neither writes File/domain publication state or claims saved.
- `exclusion.hold_exclusion(operation_id: str, target_identity: str)` context manager; `writer.write_first_content(operation: PreparedOperation, file_doc, content: bytes) -> CreationReceipt`; `writer.verify_created_object(receipt: CreationReceipt) -> None`.
- `inspection.resolve_known_outcome(operation_id: str) -> SaveOutcome`; `inspection.inspect_operation(operation_id: str) -> dict` restricted metadata only.

`SubmittedOperation`: id/site/actor/kind/request_key/payload_digest/typed request/state; no storage key/receipt authority. `PreparedOperation`: add canonical key/generation/derived patient/target/expected byte evidence. `CreationReceipt`: existing stable fields plus private in-memory exact File-object binding. `SaveOutcome`: status pending/saved/conflict/rejected/unknown, operation_id, optional positive integer version/native File name. Pre-operation refusal is an exception, not an outcome with fabricated ID. Synthetic prepared draft is consistently None, not an empty-string placeholder.

## Task 1: Bound request contracts and synthetic transport

**Files:** Modify `publication/contracts.py`; create `publication/validation.py`, `tests/unit/test_async_publication_contracts.py`; update current entry tests only for explicitly replaced wire rules.

- [ ] Write RED `test_bounded_annotation_and_image_transport`: assert the exact development values/schema in the spec: 12-MiB request, 256-KiB annotation, nesting 8, 512 shapes, text 1,024, key 16–128, 140-character references, 8-MiB decoded strict base64, image axis 8,192/pixels 40,000,000. Pin boundary acceptance/rejection, all four shape variants, unknown fields, cycles/nonfinite/bool-as-number and direct dataclass equivalence. No freehand/HTML/data URL acceptance.
- [ ] Run `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p 'test_async_publication_contracts.py' -v`; retain intended RED.
- [ ] Implement the named validator/types; canonical JSON and decoded-byte digest have one representation, expected version 0 works, operation ID and synthetic None fields are consistent. Use existing native image inspection dependencies only; no re-encoding.
- [ ] Run the focused module GREEN and compile changed Python; separate Standards/Spec review and content hashes. No remote migration yet.

## Task 2: Durable SUBMITTED journal and scoped internal persistence

**Files:** Create operation DocType/controller and `publication/journal.py`, `tests/unit/test_async_publication_journal.py`; add only operation lookup seams to `access/frappe_context.py`.

- [ ] RED `test_submission_survives_lost_notification_without_reserved_bytes`: SUBMITTED stores actor/site/kind/key/digest and bounded payload, has no key/generation/File/receipt; duplicate tuple returns same ID, mismatch rejects, native rollback leaves no runnable row. Pending operation data never appears through ordinary list/read/export/CRUD, including client flags.
- [ ] Run focused journal RED, then implement the schema with server-generated UUID-hex ID and unique tuple digest `(site, actor, kind, request_key)`; unique canonical key applies only once allocated. Payload fields are Long Text canonical JSON/base64; stable receipt/evidence fields stay separate. Named states/outcomes match the spec.
- [ ] Internal persistence is operation-bound repository authority after explicit service authorization, never public owner/ignore-permission flags. Helpers cannot commit or bypass File permissions. Controller rejects ordinary field mutation; no draft/version placeholder in preparation.
- [ ] GREEN local tests, then deploy/migrate only the development site after scoped reviews; native checks prove constraints and denied CRUD. Retain exact site/container/source hashes and migration exit; no unrelated DocTypes changed.

## Task 3: Native authenticated submission and actor-only status

**Files:** Modify `api/photo_save.py`; create `publication/submission.py`, `tests/unit/test_async_publication_submission.py`.

- [ ] RED `test_submission_uses_framework_transaction_only`: POST-only native sid session plus nonempty stored CSRF token and matching explicit header; reject alternate Authorization, Guest/Administrator/non-clinician/disabled/revoked actor, missing/mismatched token, client identity/site/path flags and stale target authority. Assert zero explicit commits/rollbacks and no File/content write even with pending request callbacks.
- [ ] Run focused RED; implement native session/CSRF enforcement without weakening native checks, default-off publication gate and exact synthetic site/mode gate. Decode bounded payload, derive actor/target/patient, stage journal, register native enqueue-after-commit once and return pending+opaque ID. Preserve current native permission vetoes and non-author denial.
- [ ] RED `test_ordinary_status_is_author_only`: same-key/current-actor replay, opaque unknown/other-user denial, no patient/payload/path disclosure. Status does not mutate uncertain files. Wire `status` through inspection service only after Task 9 is ready; unavailable service refuses, not saved.
- [ ] GREEN plus real synthetic HTTP login/token acquisition/rotation/denial checks on approved loopback tunnel; never disable CSRF or use an API-key service identity to satisfy the test. Verify request rollback causes no publication and after-commit queue failure retains SUBMITTED. Standards/Spec gate.

## Task 4: Fresh native process, launcher and automatic rediscovery

**Files:** Create `publication/process.py`, `publication/launcher.py`, `tests/unit/test_async_publication_process.py`; modify existing `hooks.py` to add rediscovery without altering current hooks.

- [ ] RED `test_process_uses_fresh_sites_context_and_persisted_actor`: fixed venv module argv/site/32-hex operation ID, sites cwd, allowlisted server environment, no payload/credentials/client actor in argv/stdout. Child creates one context/DB with no Administrator adoption, loads committed submission, adopts stored actor and reauthorizes. Invalid site/gate/ID/state denies before preparation; no Bench-discovery wrapper or active DB swap.
- [ ] Run focused RED, then implement guarded `if __name__ == '__main__'` entry with native destroy, no hidden final commit, bounded safe result markers. Unknown callbacks/unrelated bootstrap activity refuse rather than clear queues or transaction. Test real module startup using the now-proven cwd; do not substitute the earlier diagnostic as evidence for this module.
- [ ] RED `test_lost_notification_is_repaired_only_for_submitted`: existing scheduler minute hook, batch 25, fair `(creation,name)` Redis cursor/24-hour TTL and wrap, queue unavailable remains pending, only opaque IDs queued, no PREPARED/PUBLISHED launch retries. Duplicate notification and stale identity recheck under child exclusion.
- [ ] Implement one synchronous child per launcher invocation, 120-second deadline/5-second grace/64-KiB safe output; timeout is unknown, not rollback or proof child died. Native queue/scheduler wrapper transactions remain outside child. No new service/system cron/dependency.
- [ ] GREEN unit/process tests and development scheduler synchronization/launcher smoke evidence with a stub coordinator that refuses before bytes. Record no real publication until Tasks 5–8 pass. Standards/Spec review; no renewed host-tracing requirement.

## Task 5: Operation/target exclusion and real publication ownership

**Files:** Create `publication/exclusion.py`, `tests/unit/test_async_publication_exclusion.py`; integrate only owner seams in `access/frappe_context.py`.

- [ ] RED `test_duplicate_launch_cannot_take_over_surviving_writer`: operation then draft (annotation) or patient-creation (synthetic) lock order; stable server lock objects survive phase commits and are not unlinked. Duplicate child and parent timeout cannot authorize PREPARED retry; lost certainty poisons owner.
- [ ] Run focused RED, then implement confined OS advisory locks and monotonic operation generation/exact actor/object bindings; lease/queue state never substitutes. Release in coordinator finally, not callbacks alone; do not reset abort latch. Use native row uniqueness as an additional check.
- [ ] GREEN focused and actual two-process lock/parent-death tests on the shared development sites volume. Verify child cannot retain unsafe write authority after claimed completion. No distributed/multi-host claim. Standards/Spec gate.

## Task 6: Protect PREPARED keys and close native alias/rollback boundaries

**Files:** Extend `publication/journal.py`, `access/{frappe_context,file_mixin,storage}.py`; add `tests/unit/test_async_publication_boundaries.py` and isolated native cases.

- [ ] RED `test_prepared_reservation_survives_publication_rollback`: key is protected before File/domain rows and remains so after rollback; SUBMITTED alone does not reserve a key. Owner allocates derived canonical key/hash/size/target under lock; helpers stage only.
- [ ] RED `test_native_dedup_retarget_then_rollback_preserves_original`: reserved/nonreserved protected destinations, pre-db_insert and post-complete-insert checks, duplicate aliases, original_path endpoints, failed unsaved File and Administrator all refuse protected mutation/deletion. Unknown classification rejects ordinary insert and skips destructive compensation without deliberately breaking remaining callbacks; proved-unrelated delegates unchanged.
- [ ] Run focused RED; implement supported File insert/validate/on_rollback/delete boundaries, trusted root normalization and retained poisoned identity. No DB journal write required inside rollback; no File permission adapter/core/runtime patches.
- [ ] GREEN unit and installed native alias/veto/rollback cases. A post-insert rejection requires rollback; do not claim the row was never staged. Incompatible effective write/security hooks or reachable unguarded helpers block feature enablement, not all development implementation. Standards/Spec gate.

## Task 7: Exact-object exclusive first writer and receipt bridge

**Files:** Create `publication/writer.py`; change only protected first-save/duplicate/after-insert seams in `access/file_mixin.py` and write-chain context; add `tests/unit/test_async_publication_writer.py`.

- [ ] RED `test_first_writer_preserves_exact_jpeg_and_canonical_key`: server-bound PREPARED owner/exact unsaved File only; native File create/security checks remain active. Actual SHA-256/bytes/dimensions/key/privacy/owner unchanged, no suffix/EXIF transform/MD5 reuse or implicit aliases. Test symlink/escape/existing path/hard-link ambiguity/partial-write/fsync failure.
- [ ] Run focused RED; implement confined directory-relative exclusive/no-follow creation with retained descriptor identity, full byte and directory durability before receipt. Do not truncate, unlink failed bytes or force native overwrite/copy-existing flags. Compatible native hashes are metadata, never receipt authority.
- [ ] Implement provisional receipt bridge to persisted File name, exact object and references; after full hooks re-read actual persisted identity/bytes before linkage. Bind/consume once; substitution/replay/changed fields poison owner.
- [ ] GREEN local and native synthetic-original insertion through real owner. Synthetic fixture original must use this writer, not privileged DB/filesystem seeding. Existing protected originals stay immutable; no ordinary Administrator cleanup bypass. Standards/Spec gate.

## Task 8: Two publication commits, atomic linkage and definite rejection

**Files:** Create `publication/coordinator.py`; extend journal/process/domain lifecycle only as needed; add `tests/unit/test_async_publication_coordinator.py`.

- [ ] RED `test_prepare_precedes_bytes_and_publish_is_atomic`: child reads committed SUBMITTED, owns exclusion/context, reauthorizes and stages PREPARED+reservation then commits; next transaction rechecks actor/version/generation, inserts native File/exact bytes, completes hooks/linkage/receipt and PUBLISHED in one commit. No helper/hook/writer commit.
- [ ] Run focused RED; implement annotation append to author-owned draft and synthetic original with no nonexistent draft/version/original placeholders. Fresh version 0 is valid; next version derives server-side. All relevant original/patient/draft bindings are rederived/checked.
- [ ] RED phase-failure cases before/after write/insert/hooks/linkage/commit and revocation before each phase. Definite SUBMITTED denial/conflict may ABORT with rejected/conflict and no key/bytes. PREPARED retry never re-enters writer; lost acknowledgement is unknown, not rollback-delete or second version.
- [ ] GREEN focused and native first-original/first-annotation flow through real process. Return saved only from known committed coherent records/actual byte evidence; child exit/JSON alone cannot establish saved. Verify source/native role/User Permission vetoes still win. Standards/Spec gate.

## Task 9: Current-access replay, status and conservative inspection

**Files:** Create `publication/inspection.py`; finish status wrapper, journal/coordinator replay; add `tests/unit/test_async_publication_recovery.py` and development runbook.

- [ ] RED `test_revoked_author_cannot_replay_saved`: same tuple/digest returns same version only after present authorization; changed payload rejects; two drafts sharing an original do not conflict globally; stale same-draft version conflicts before bytes. Cross-user pending metadata and cross-patient duplicate matches never leak.
- [ ] RED `test_prepared_unknown_never_resumes_or_purges`: ambiguous domain/byte evidence stays pending/unknown/quarantined; no write, unlink, key reuse or silent repair. Read-only inspection requires current clinician AND explicit default-empty operator allowlist and returns operation-bound audit/hash/consistency, not contents/paths/credentials.
- [ ] Run focused RED; implement read-only known-outcome resolution, gates and safe audit. No automatic recovery/purge endpoint or manual SQL surgery as routine procedure. Wire Task 3 status only now; unavailable/unknown never reports saved.
- [ ] GREEN plus native concurrent retry/revocation/lost response/inspection evidence. Synthetic development retention is exact-stack disposal; no production retention or restore capability claim. Standards/Spec gate.

## Task 10: Failure evidence and exact-stack provenance

**Files:** Modify native `tests/{run_isolated_cases.py,case_manifest.json}`, unit `test_case_runner.py`; add required publication native cases and assessments.

- [ ] RED `test_failed_end_preserves_residue_without_passing`: extract failed residue while retaining failed verdict; missing case/nonce/site/hash/storage-key/resource identity or observed disposal rejects cleanup confirmation. Empty rows do not prove filesystem cleanup.
- [ ] Run focused runner RED; implement versioned cleanup/evidence binding covering run/site/project/source/contract/nonces/raw-output hashes, rows AND protected storage keys and actual observed resource disposal. Preserve proven bare-method selector/original-stdout marker protocol and all historical raw failures.
- [ ] GREEN parser/producer/consumer checks, manifest dry-run and one protocol gate only if protocol changed. Never turn diagnostic/skipped/unrun cases into passes. No deletion occurs merely to make evidence green; disposal only when explicitly performed. Standards/Spec gate.

## Task 11: Reconcile and verify the working development slice

**Files:** Assessment, revised ledger, immutable native/HTTP evidence; no unrelated source changes.

- [ ] Reconcile all writers/reviews and source hashes; run full local unittest discovery ONCE after final reconciliation: `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -v`. Retain exact command/count/exit; inspect failures before repairs. Old 422 baseline and 29 refusal tests do not prove new behavior.
- [ ] Deploy the same candidate hashes to ALL participating app copies; migrate/synchronize only development site and verify DB/site/mount/image/cwd/worker/scheduler agreement. Preserve existing synthetic state or recreate it deliberately; no unnecessary unrelated-project reset.
- [ ] Execute isolated native cases with fresh nonce/process per aborting case: actor/permission vetoes, direct process, SUBMITTED queue-loss rediscovery, duplicate/surviving child, PREPARED crash/unknown commit, exact JPEG/JSON bytes, aliases/rollback, independent drafts and current-access replay. Fault injection is test-harness-only; no runtime monkeypatch/core patch.
- [ ] Run real session/CSRF POST-to-status publication on loopback/HTTPS, then the existing 21 private read/download cases after native readiness. Record actual saved version/File IDs and byte evidence; check Guest/direct/ZIP/cache denial paths. No credentials to host port 8080; no frontend persistence activation as a side effect.
- [ ] Independently verify logging restored, synthetic-only fixtures/residue accounted for and no unintended process/queue work. Retain developer stack for further work unless disposal is requested; do not automatically destroy the only development environment. Report passed/failed/unrun, precise residual gates and actual development readiness. No commits/staging/push.

## Self-review and handoff

Self-review completed against the replacement design: bounded contracts 1; durable submission 2–3; native auth/status 3/9; launcher/scheduler/process 4; ownership/exclusion 5; protection/rollback 6; writer/receipt 7; two-phase publication 8; recovery/privacy 9; evidence/provenance 10; native/HTTP/readiness 11. All five Review Focus conditions map to named tests. No task uses the obsolete same-request clean-entry prerequisite, and helpers never commit publication state. Shared types/signatures are consistent; no missing module is treated as a successful placeholder. Clarified app-relative paths, uniform focused test commands and rectangle/ellipse stroke bounds; removed obsolete unresolved-transport wording in the design. Native framework feasibility is a test obligation, not a completed proof.

The proposed development schema/limits, actor-only operation status, native session+explicit CSRF contract and direct-module entry are substantive revised-plan decisions. Review these together rather than reopen routine Docker permissions. Preserve the already-selected specialist/vertical-slice TDD/separate Standards+Spec execution method; no new method selection is needed. Execution ledger should mark the old HTTP plan superseded, preserve old snapshots/failures, and start a new baseline before the first writer.
