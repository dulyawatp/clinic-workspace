# Durable Photo Save Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a default-off, two-phase durable photo-save coordinator and protected first writer, proving synthetic originals and manual annotation append without live activation.

**Architecture:** Commit a feature-specific PREPARED operation before bytes. Preserve the clinician and request-local database; guarded native File insertion creates bytes exclusively, then File/domain linkage and PUBLISHED commit together. Uncertain outcomes remain unresolved/quarantined, with no automatic write resumption or deletion.

**Tech Stack:** Existing Python Frappe app; installed Frappe 16.53.0; native File mixin; database DocTypes; directory-relative filesystem operations; stdlib unittest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-11-07-frappe-native-photo-insertion-design.md` (user approved design, planning only).

**Execution status:** User explicitly approved this plan. Execute task gates in order; migrations/transfers/runtime tests remain confined to the already approved disposable stack and may not precede their local safety/review prerequisites. Retain requested mattpocock `implement`, vertical-slice `tdd` and separate Standards/Spec review, reading skills without installation. Do not commit, stage or push; content snapshots and SHA-256 substitute for Git ranges because this repository has no HEAD. No live activation is authorized.

## Global Constraints

- All current enabled clinicians may read saved drafts; only each draft's author may edit. Pending/quarantined bytes are not saved-draft reads. No assignment restriction or cross-patient duplicate disclosure.
- Two phases only: PREPARED commit; native insertion/write, receipt/linkage/PUBLISHED commit. No independent RECEIPTED phase, second DB swap, filesystem journal or write before native insertion.
- Native File permissions and downstream vetoes remain authoritative. No File `ignore_permissions`, role grants, core patches, runtime monkeypatches, client capabilities or abort-latch resets.
- Exact immutable bytes/key/privacy/identity; no transformation, implicit deduplication, truncating overwrite, automatic protected cleanup or uncertain-write retry.
- Unknown classification rejects ordinary uploads too. Proved-unrelated uploads retain native behavior. Administrator status is not ordinary protected-cleanup authority.
- Coordinator/inspection default off; synthetic-original kind additionally restricted to the approved disposable site and explicit synthetic mode. Recovery operator allowlist defaults empty and still requires a current clinician.
- Single-host shared-sites-volume exclusion only after actual lock/writer-coverage proof. No lease-only takeover or unsupported direct-helper caller.
- Existing remote scope only: project `clinic-photos-accept`, site `clinic-photo-accept.local`, directory `/home/sunphunkosol/docker/clinic-photos-accept/`, loopback 8082. Existing services/production data remain untouched. No downloads/pulls/builds; only the already-approved pinned flit_core wheel exists.
- Backup destination/encryption/retention, authorized purge policy, tested recovery and consistent off-device restore remain live-activation blockers. This plan does not provision them or enable live writes/UI persistence.

## Review Focus

- Middleware/authentication leaves unrelated writes or callbacks: refuse before any coordinator commit (Task 1).
- Ordinary upload dedup retargets a protected nonreserved key, then fails: no alias commit or original mutation, including Administrator rollback (Task 3).
- Partial filesystem write or lost commit response: no blind retry/unlink; durable intent survives and exposes unknown/quarantine (Tasks 5–7).
- Revoked author replays a formerly successful request: deny before disclosing saved outcome (Task 7).
- Another process retains write authority or an unsafe helper/hook appears: refuse takeover/publication, not token-only safety claims (Tasks 1, 4, 9).

## File and interface map

All paths below are relative to `frappe-apps/clinic_photos/` unless prefixed `docs/` or `.superpowers/`.

- Create `clinic_photos/publication/contracts.py`: immutable request/intent/receipt/outcome types and safe errors; no Frappe import.
- Create `clinic_photos/publication/{journal,exclusion,writer,coordinator,inspection}.py`: respectively feature operation persistence, cross-phase locks, exclusive byte creation/receipts, two-phase orchestration, restricted evidence projection. Add package `__init__.py` with the first consuming task, not a standalone scaffolding task.
- Create `clinic_photos/api/photo_save.py`: default-off authenticated entry and inspection wrappers; add `api/__init__.py` if absent. Never enable the gate from a test's expected result or caller flag.
- Create `clinic_photos/clinic_photos/doctype/clinic_photo_save_operation/{clinic_photo_save_operation.json,clinic_photo_save_operation.py,__init__.py}`: feature-specific journal schema and lifecycle restrictions; no general file registry.
- Modify `clinic_photos/access/{frappe_context,file_mixin,lifecycle,storage}.py` only at the owner, registration lookup, insertion/rollback and receipt seams required by this spec. Preserve pure policy and repaired native permission adapter.
- Add `tests/unit/test_publication_*.py`; extend existing `tests/unit/{test_file_policy,test_storage,test_case_runner}.py` only where relevant. Native evidence goes in new `clinic_photos/tests/test_publication.py` and the existing isolated runner/manifest, never a multi-case shared-abort suite.
- Update `docs/assessments/2026-11-06-frappe-photo-extension-acceptance.md` and existing progress ledger with evidence, not anticipated passes.

### Shared contracts (defined in Task 1)

`SaveRequest(kind, request_key, draft, expected_version, original, annotation, synthetic_patient, image_bytes)` is an immutable dataclass: kind is `annotation_append` or `synthetic_original_create`; optional fields are validated per kind. Public JSON must not contain owner/generation/path/permission flags. Request size/schema limits must come from existing Clinic annotation/image contracts; if missing, stop to resolve limits before accepting payloads, not silently invent an unlimited API.

`PreparedOperation(id, actor, kind, payload_digest, key, generation, draft, expected_version, state)` is a server-produced immutable reference. `CreationReceipt(operation_id, generation, key, device, inode, size, sha256)` describes actual creation; exact File object binding remains private in-memory state and is never serialized as authority. `SaveOutcome(status, operation_id, version, file_name)` uses `saved`, `conflict`, `pending` or `unknown`; durable states remain PREPARED/PUBLISHED/ABORTED/QUARANTINED. Safe denial/validation exceptions do not contain arbitrary document dumps.

---

### Task 1: Prove and guard the authenticated transaction entry

**Files:** Create contracts and API files above; create `tests/unit/test_publication_entry.py` and native `clinic_photos/tests/test_publication.py`. Read existing `access/permissions.py`, native transaction evidence and proxy/auth contract; do not modify the permission adapter.

**Interfaces:** Produce `validate_request(payload: dict) -> SaveRequest`, `require_clean_entry() -> None`, and default-off `save(payload: dict) -> dict`. The eventual public method delegates to `coordinator.save_request(request: SaveRequest) -> SaveOutcome`; until implemented, it must refuse rather than return a placeholder success.

- [ ] Write RED `test_disabled_or_unclean_entry_never_commits`: assert default-off, GET/Guest/non-clinician denial, client flags rejected, dirty writes/callbacks refused and unchanged, zero commits on refusal. Add kind-specific input tests without accepting synthetic mode from the request.
- [ ] Run `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p 'test_publication_entry.py' -v`; record RED exit/reason.
- [ ] Implement only validation/gate/clean-entry refusal and shared dataclasses. A clean-boundary proof must cover authentication/middleware writes and callbacks; a simplistic boolean flag or empty callback queue alone is not sufficient.
- [ ] Run focused GREEN. After plan execution is authorized, perform one bounded synthetic native entry diagnostic proving same clinician/DB and no unrelated work across sequential commits. No File writes yet. If clean entry cannot be established, STOP: revise design rather than commit unrelated work or swap `local.db`.
- [ ] Separate Standards/Spec review; snapshot changed bytes and exact commands/exits. No commit. This gate must pass before a write-capable entry is enabled even synthetically.

### Task 2: Durable feature operation and pre-byte registration

**Files:** Create journal DocType and `publication/journal.py`; modify `access/frappe_context.py:lookup_protection`, `_key_registered`, `_feature_storage_key_rows`, registration-consistency lookup; add `tests/unit/test_publication_journal.py` and native cases.

**Interfaces:** `journal.prepare(request: SaveRequest, actor: str) -> PreparedOperation` stages intent only; coordinator owns commits. `journal.load(operation_id: str, *, for_update: bool) -> PreparedOperation`; `journal.publish(operation_id: str, receipt: CreationReceipt, file_name: str, version: str | None) -> None`; `journal.mark_unresolved(operation_id: str, state: str) -> None`. Helpers never commit.

- [ ] RED `test_prepared_key_remains_protected_after_publication_rollback`: PREPARED key resolves protection before File/domain records; ordinary reads/inserts deny; rollback leaves intent and key reserved. Test actor/kind/request-key uniqueness, payload mismatch, key uniqueness and no annotation-version placeholder.
- [ ] Run focused `unittest discover ... -p 'test_publication_journal.py' -v`; confirm intended RED.
- [ ] Implement exact fields/states/constraints in approved spec; operation records have no ordinary client CRUD/list permissions. Internal record persistence requires narrowly scoped coordinator authorization, never a generic privileged client mutation API. Protection lookup treats unresolved intent as protected without making it readable.
- [ ] GREEN focused tests plus isolated-site migration/constraint tests only after authorized transfer. Verify no existing site/schema touched. A publication rollback must not erase PREPARED.
- [ ] Standards/Spec gate and hashes; no commit. Do not add schema fields to unrelated DocTypes.

### Task 3: Close ordinary-upload alias and complete rollback boundaries

**Files:** Modify `access/file_mixin.py` insertion/validation/rollback/deletion wrappers and `frappe_context.py` identity guards; add `tests/unit/test_publication_boundaries.py`, extend native publication cases.

**Interfaces:** `require_insert_destination(file_doc, *, post_insert: bool) -> None`; `rollback_may_delegate(file_doc) -> bool`. Use these in pre-db_insert validation, a supported `insert` wrapper after complete native insertion, and `on_rollback` before any native filesystem effect. Maintain exact-object protected identity history privately.

- [ ] RED `test_unrelated_upload_retarget_then_rollback_preserves_original`: cover reserved and nonreserved keys, both duplicate-retarget sites, two-save original-content branch, move endpoints, newdoc without persisted row and Administrator. Assert no protected open-for-write/move/unlink, no committed alias, owner remains poisoned.
- [ ] RED unknown-classification tests: ordinary insertion rejects; rollback skips native mutation without deliberately raising and aborting remaining callbacks. Proved-unrelated insert/rollback delegates unchanged.
- [ ] Run focused boundary module, then implement the named wrappers. Filesystem-path endpoints must normalize against trusted site roots. No required DB journal write inside rollback; PREPARED remains unresolved when DB is unavailable. Do not clear abort latch or grant Administrator cleanup implicitly.
- [ ] GREEN focused and actual native alias/rejection/rollback cases. Verify File/native role/User Permission vetoes still win. Do not describe a post-insert rejection as prevention of row insertion: verify rollback and no original mutation instead.
- [ ] Standards/Spec gate. If reachable unguarded deletion helpers or hooks cannot be confined through supported boundaries, STOP rather than patch core.

### Task 4: Cross-phase exclusion and publication owner

**Files:** Create `publication/exclusion.py`; modify `frappe_context.py:owned_publication_transaction` integration only; add `tests/unit/test_publication_exclusion.py` and subprocess native tests.

**Interfaces:** `hold_exclusion(operation_identity: str, target_identity: str)` is a context manager held across both commits; annotation target is draft, synthetic-original target is patient-scoped creation identity. `PublicationOwner.abort(reason: str) -> None` poisons permanently; owner validation binds operation/generation to current actor and request.

- [ ] RED `test_second_process_cannot_publish_between_phase_commits`: operation then target lock order; stable lock objects never unlinked; unique key reservations; no expiry-only takeover; no child retaining write authority. Add loss-of-certainty denial.
- [ ] Run focused exclusion module; implement single-host directory-confined advisory locks and coordinator-owned release in `finally`, not rollback callbacks alone. Generation checks supplement, not replace, exclusion.
- [ ] GREEN local process tests; prove actual lock semantics on approved sites volume and writer coverage. Inventory effective hooks and installed callers without invoking unsafe hooks. An incompatible/unknown effective map or alternate writer blocks feature enablement.
- [ ] Standards/Spec gate. No generalized distributed lock service, new dependencies or unsupported multi-host claim.

### Task 5: Exact-object exclusive protected first writer

**Files:** Create `publication/writer.py`; modify `file_mixin.py` protected before-insert/save/duplicate paths and `frappe_context.py` write-chain seams; add `tests/unit/test_publication_writer.py`.

**Interfaces:** `write_first_content(operation: PreparedOperation, file_doc, content: bytes) -> CreationReceipt` is callable only inside the real owner's exact-object native insertion scope. It does not insert/commit domain records. `verify_created_object(receipt: CreationReceipt) -> None` verifies the confined object, not an unchecked path.

- [ ] RED `test_protected_insert_preserves_jpeg_bytes_and_canonical_key`: actual byte SHA-256/size/dimensions unchanged, including EXIF-bearing image; no native suffix, dedup, transform or URL rewrite. Test partial/short writes, symlink/root escape, preexisting file, hard-link ambiguity, failed fsync and hook incompatibility.
- [ ] Run focused writer module. Implement exclusive/no-follow directory-relative creation and retained-descriptor evidence; complete byte/directory durability before receipt. Preserve applicable native size/content/security checks, including rejection when an obligation cannot coexist with byte fidelity. Caller `overwrite/new_file/copy_existing` flags cannot authorize this mode.
- [ ] GREEN focused plus native synthetic content-bearing insertion. Verify no filesystem write occurs before PREPARED or outside native insertion; collision never truncates, failed creation never automatically unlinks/retries. Native compatibility hash is not an authority or dedup permission.
- [ ] Standards/Spec gate. Do not enable write entry until Tasks 1–4 gates pass. No test-only owner presented as durable authority.

### Task 6: Receipt bridge and two-phase publication

**Files:** Create `publication/coordinator.py`; connect API; modify supported File after-insert bridge and minimal domain lifecycle seams; add `tests/unit/test_publication_coordinator.py`.

**Interfaces:** `save_request(request: SaveRequest) -> SaveOutcome`; `bind_persisted_file(operation: PreparedOperation, file_doc, receipt: CreationReceipt) -> None`; `finalize_linkage(operation: PreparedOperation, file_doc, receipt: CreationReceipt) -> str | None`. Only `save_request` owns the two commits and outcome classification.

- [ ] RED `test_native_insert_receipt_and_domain_publish_atomically`: PREPARED commits with no bytes; native insert writes exactly once; provisional File-name bridge; full hooks complete; re-read object/row/aliases; consume once; File/version/draft/PUBLISHED commit together. Reject object substitution, altered attachment/privacy/key, receipt replay and same-key aliases.
- [ ] Run focused coordinator module. Implement annotation append and isolated-only synthetic-original variant; synthetic original has no draft/version/original prerequisite. Do not seed original by privileged direct disk/DB writes. No third receipt commit.
- [ ] GREEN tests for failure before/after write, DB insert, hooks, linkage and commit. Lost commit acknowledgement returns unknown, never rollback-delete or a second version; inspect reliable durable outcome before reporting saved. Keep teardown free of essential deferred mutations.
- [ ] Native synthetic-original then annotation-append proof through default-off method enabled only by explicit synthetic-site configuration in the authorized test lane. Stable actor/native File vetoes checked. Standards/Spec gate and evidence; no live activation.

### Task 7: Idempotency, conflicts and conservative inspection

**Files:** Extend coordinator/journal; create `publication/inspection.py`; expose gated API inspection; add `tests/unit/test_publication_recovery.py`.

**Interfaces:** `inspect_operation(operation_id: str) -> dict` returns restricted evidence only. `resolve_known_outcome(operation_id: str) -> SaveOutcome` is read-only with respect to uncertain files; no resume/purge API.

- [ ] RED `test_revoked_author_cannot_replay_saved_outcome`; same payload/key returns same version only after present-access checks, mismatched digest rejects, stale draft version conflicts before bytes, independent drafts do not conflict on a shared original. PREPARED retry returns pending/unknown without writer invocation.
- [ ] RED inspection tests: default-empty operator allowlist, clinician AND configured operator, no Guest/non-clinician/Administrator shortcut, operation-bound read/audit only, no image/JSON content, arbitrary path reads or credentials.
- [ ] Run focused recovery module; implement only approved conservative decisions. Reliable committed operation/domain/byte evidence can resolve a lost response; missing identity proof leaves quarantine/unresolved. No automatic cleanup or state repair from path/hash equality.
- [ ] GREEN and native concurrency/revocation/inspection checks. Document operator runbook: how to collect evidence, stop mutation, identify unknown outcome and seek separately authorized reconciliation/retention action; prohibit manual SQL surgery as routine recovery.
- [ ] Standards/Spec gate; pending clinical retention/purge policy remains an activation blocker, not an implementation default of indefinite retention.

### Task 8: Failure-safe evidence and exact-stack disposal binding

**Files:** Modify `clinic_photos/tests/{run_isolated_cases.py,case_manifest.json}`, unit `test_case_runner.py`; operational artifacts under `.superpowers/sdd/2026-11-06-frappe-photo-access-extension/disposable/native/` only.

**Interfaces:** Preserve proven selector/nonce/standalone-marker protocol. Define one versioned cleanup record binding run ID, site/project, all case IDs/nonces/raw-output hashes, source/contract hashes, rows AND storage keys, observed resources and actual disposal evidence. Do not accept current rows-only `residue_digest` as cleanup proof.

- [ ] RED `test_failed_end_still_preserves_residue_without_becoming_passed`: extract evidence even on failure while preserving failed verdict. Add substituted nonce/site/key/resource and absent disposal rejection tests; empty residue still requires observed teardown.
- [ ] Run focused runner tests, implement minimal parser/cleanup schema changes without weakening summary/count/identity/exit/terminal rules. Update producers/consumers together; old weaker records reject. Preserve all historical raw failures.
- [ ] GREEN focused tests and manifest validation/dry run. Expand isolated cases for this plan; every aborting case runs in its own process. Never count diagnostic cases or skipped/unrun cases as passes.
- [ ] Standards/Spec gate; actual disposal confirmation is generated only after later approved teardown and observed removal of exact resources, including sites volume containing private files.

### Task 9: Reconcile and verify the bounded synthetic slice

**Files:** Acceptance assessment/progress ledger, new immutable evidence directories; no unrelated source changes.

- [ ] Reconcile all task scopes/reviews and source hashes; resolve findings before final test evidence. Run once: `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -v`; retain full output, count and exit. Inspect failures before repair. Current 422-pass baseline does not cover these future changes; the focused fake-whitelist import limitation is not waived.
- [ ] Before remote effects confirm plan execution authorization, unchanged exact-stack scope and stopped prior processes. Hash-pinned transfer to ALL participating new app-container copies; do not use stale frontend/worker code for HTTP acceptance. Verify site/DB/mounts/networks/image and existing-service IDs/StartedAt/RestartCount/State, not uptime text.
- [ ] Execute one fresh native protocol gate if protocol changed, then canonical isolated policy/publication cases with fresh nonce/process per case, stop first failure, checkpoint raw output/residue. Include ordinary-upload alias/callback paths, crash/partial-write/commit uncertainty, independent processes, revoked users and real authenticated entry. Fault injection must be test-harness-only, not runtime monkeypatches or core changes; if a fault cannot safely be simulated, mark its gate unproved and do not activate.
- [ ] Run the existing 21 read/download HTTP cases only after native readiness and exact authenticated transport/session contract are proven. Credentials only through approved loopback tunnel or HTTPS, never host port 8080. Guest ZIP/direct protected status/cache/reader/export/background paths need actual evidence; no unsupported broad finance/import calls.
- [ ] Assess all results without false acceptance from missing cleanup. When testing is complete, perform only previously approved exact-stack teardown; verify container/network/DB/sites/log resource removal and bind the versioned record. Preserve failure evidence and report residue honestly. No broad prune or existing-service changes.
- [ ] Record passed/failed/unrun and every open gate. Off-device backup/restore, retention/purge authority, general original-ingest/reuse acceptance and live/frontend activation remain blocked unless separately approved and proved. Present results and stop; no commits or live enablement.

## Plan self-review and handoff

Spec coverage: Tasks 1–2 cover entry/intent/schema; 3 covers alias/deletion boundaries; 4 exclusion; 5 exact bytes; 6 receipts/domain atomicity; 7 authorization/retry/recovery; 8 failed-run/disposal provenance; 9 native/HTTP/environment evidence. The five Review Focus conditions are assigned to named tests above. Shared interface names are consistent; no helper besides coordinator commits. Tests/diagnostics and runtime gates are future work, not claimed results.

Retain the user's existing specialist execution and vertical-slice TDD choice; do not ask them to select a new execution method. Ask them to review and confirm this plan. Confirmation must precede code/migrations/remote retries; the design approval alone is insufficient. All tasks use focused red/green checks and separate Standards/Spec gates, with final local validation once after reconciliation. No commits, dependency installation or live activation is included.
