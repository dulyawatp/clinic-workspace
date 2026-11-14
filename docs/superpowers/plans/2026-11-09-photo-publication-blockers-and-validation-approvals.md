# Photo Publication Blockers and Validation Approval Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans for an explicitly approved implementation slice. Checkboxes below track gates, not permission to execute them.

**Goal:** Resolve the remaining native acceptance obligations without weakening their claims or mistaking local source readiness for native evidence.

**Architecture:** Retain the approved SUBMITTED → PREPARED → PUBLISHED fresh-process architecture. Separate test-control design, test-body authoring, deployment/schema synchronization, native execution and disposal into independently approved scopes. No new fault mechanism is selected by this document.

**Tech Stack:** Existing Python unittest, Frappe16.53.0/ERPNext16.52.1/Healthcare16.6.1 historical development profile, MariaDB and existing worker/scheduler; no new dependencies/services.

**Spec:** `docs/superpowers/specs/2026-11-08-frappe-photo-publication-entry-design.md`; retained `2026-11-07-frappe-native-photo-insertion-design.md` in the same directory. Parent sequence: `docs/superpowers/plans/2026-11-08-frappe-async-photo-publication.md`, Tasks10–11. Recovery constraints: `docs/2026-11-08-photo-publication-recovery-runbook.md`.

## Approval status

User approved **preparing this matrix and approval plan only**. No code authoring, fixture mutation, remote observation, deployment, schema synchronization, gate enabling, native execution, disposal or final full-suite run follows automatically. Existing prohibitions remain binding.

## Global Constraints

- Saved means known durable publication, not accepted input or queued work.
- No core/runtime patch, active local.db swap, privileged File insertion, new service/dependency, automatic uncertain-save resumption, general original ingest, frontend activation or live deployment is proposed.
- No File `ignore_permissions`, real-user role grants, protected-byte seeding, abort resets, uncertain-write retries/deletion or weakening native downstream vetoes.
- PREPARED never automatically resumes; lock/owner authority must survive both commits.
- Native entry remains fixed `python -m clinic_photos.publication.process`, sites cwd `/home/frappe/frappe-bench/sites`; no generic publication via `bench execute`, web or RQ callbacks.
- Only synthetic data on `clinic-photo-accept.local`; gates remain OFF outside a separately approved bounded test window. Default-empty operator allowlist is not changed merely to run denial cases.
- No commits, staging or pushes. No infrastructure/image/dependency changes or unrelated project cleanup.
- Separate Standards/Spec review remains required. Local controls, structural pins, mutants and native behavior are different evidence classes.
- Real-data activation remains blocked by retention/purge, consistent off-device backup and tested restore decisions.

## Review Focus

1. A denial at an earlier gate must not masquerade as the claimed native downstream veto/live-owner rule.
2. Revocation must take effect at the claimed phase; snapshot-only reads or arbitrary transaction resets cannot prove that timing.
3. Missing child acknowledgement/commit certainty must preserve intent and residue, never authorize a second append.
4. A healthy final state does not prove intermediate PREPARED durability or atomic rollback behavior.
5. Source hashes, operator disposal attestations and historical schema observations are not deployed-state/physical-disposal/native-acceptance proof.

## Verified local baseline and evidence limits

- Manifest locally enumerated:25 required publication IDs;8 source-executable and17 blocked;39 executable/17 blocked/56 overall. All native statuses UNRUN, zero verified publication cases.
- Pass1 three positive cases and pass2 non-allowlisted cross-actor denial have local Standards/Spec closure. Four earlier entry cases account for the other source-executable publication IDs.
- Latest retained focused GREEN:266 tests (57helper+187runner+22entry). Canonical-identity repair RED:6 behavioral failures plus1 source pin; no native run.
- Source operation DocType has22 fields. Historical installed schema had14, including the nullable unique `reservation_key`. Eight historically missing fields: `generation`, `expected_sha256`, `expected_size`, `published_file`, `published_domain_doctype`, `published_domain_name`, `published_version`, `creation_identity`. Fresh installed state is UNKNOWN. Earlier references to only three missing evidence columns are older snapshots, not the current full gap assessment.
- `REPEATABLE-READ`, `innodb_snapshot_isolation=OFF`, runtime versions and remote topology are historical observations, not revalidated here.
- Unauthorized earlier893-test discovery does not satisfy Task11. Final full discovery remains unrun under the present authorization.

## Exact 17-case blocker matrix

**A:** complete authoring path available under current controls. **B:** controlled synthetic actor/permission fixture protocol needs approval. **C:** schema/connection, live-owner, phase or fault-observability design needs approval. Mixed entries require every listed control. Classification is conservative planning, not proof that alternatives are impossible. No row is A today; common runtime prerequisites do not by themselves settle the test-control issue.

All methods below reside in `frappe-apps/clinic_photos/clinic_photos/tests/test_publication.py`; immutable IDs/identities are pinned by `tests/run_isolated_cases.py` in that module directory. Full descriptions remain in `tests/case_manifest.json` there.

| Required ID | Exact method | Control needed | Full evidence obligation / why current helpers do not settle it |
|---|---|---|---|
| `pub.schema.reservation_null_and_unique` | `test_reservation_null_semantics_and_unique_index` | C: schema + lawful non-NULL reservation fixture | Two committed NULL reservations coexist; duplicate non-NULL key refuses. Metadata/NULL-only proof omits uniqueness; installed schema sync alone does not create lawful competing reservations. |
| `pub.schema.set_value_no_hooks_no_commit` | `test_set_value_has_no_hooks_and_no_commit` | C: independent connection + hook observation | Actual set_value bypasses document hooks and remains uncommitted to another connection. Separate process is a candidate, not permission to swap active local.db or register unsupported write hooks. |
| `pub.original.hooks_and_rollback_residue` | `test_native_hooks_and_rollback_residue_observed` | C: bound writer rollback observation | Observe native hooks for the real writer transaction and retained protected residue after rollback; no privileged seeded substitute. |
| `pub.reservation.survives_rollback` | `test_prepared_reservation_survives_rollback` | C: preparation/publication boundary control | Committed PREPARED key survives publication rollback and remains protected/resolvable. Need a genuine PREPARED floor, not a fabricated row. |
| `pub.reservation.retarget_reserved_refused` | `test_dedup_retarget_reserved_refused` | C: reserved key + operation-bound alias attempt | Retarget to another operation is refused at the intended native boundary, without insertion. Earlier no-owner denial is insufficient. |
| `pub.reservation.retarget_registered_refused` | `test_dedup_retarget_registered_refused` | C: bound retarget attempt | Registered non-reserved protected destination cannot be retargeted. Existing published fixture is necessary but an ownerless attempt may hit the wrong guard. |
| `pub.reservation.post_insert_retarget_requires_rollback` | `test_post_insert_retarget_requires_rollback` | C: post-insert in-transaction control | Native insert completes, retarget refuses, rollback preserves original identity/bytes. Cannot claim row was never staged. |
| `pub.reservation.original_path_rollback_guarded` | `test_original_path_rollback_guarded` | C: real compensation-path control | Exercise real shutil.move compensation under bound transaction; preserve protected endpoints/residue. Do not invoke destructive compensation against arbitrary files. |
| `pub.reservation.unsaved_file_rollback_guarded` | `test_unsaved_file_rollback_guarded` | C: bound unsaved File rollback | Prove unsaved File residue stays protected during actual rollback, not merely a local mock outcome. |
| `pub.reservation.administrator_delete_refused` | `test_administrator_delete_refused` | C: live-owner test authority | Administrator cannot delete committed protected records **under a live owner**. Ownerless denial is a different claim and remains insufficient. |
| `pub.atomicity.two_phase_atomic` | `test_prepared_commit_then_publication_is_atomic` | C: phase/failure observation | PREPARED committed before bytes; failure leaves no partial PUBLISHED linkage. Happy-path coherence alone does not prove this. |
| `pub.atomicity.same_owner_after_commit` | `test_same_owner_rechecks_after_commit` | C: owner continuity/exclusion observation | Only same live owner continues after preparation commit; version/authority rechecks happen. Final outcome alone cannot demonstrate owner continuity. |
| `pub.atomicity.revocation_between_phases` | `test_revocation_between_phases_blocks_publication` | B+C: synthetic revocation + phase coordination | Revocation lands after preparation and before publication; publication refuses without losing PREPARED floor. Neither revocation-only nor timing-only settles it. |
| `pub.atomicity.unknown_commit_not_retried` | `test_unknown_commit_outcome_is_not_retried` | C: commit-acknowledgement fault mechanism | Real unknown commit reports unknown, never fresh append/key/retry. Discarding a successful client response does not emulate unknown DB commit acknowledgement. |
| `pub.atomicity.duplicate_launch_no_resume` | `test_duplicate_launch_does_not_resume_prepared` | C: durable PREPARED fixture/launch observation | Duplicate loads advanced PREPARED state and never repeats insertion. PUBLISHED duplicate refusal is only a partial arm. |
| `pub.access.disabled_or_revoked_actor` | `test_disabled_or_revoked_actor_cannot_replay` | B: synthetic revocation protocol | Publish as enabled actor, then actually disable/revoke that actor; current replay/status cannot return saved and cannot append/change bytes. Requires approved operator action after publication. |
| `pub.access.native_file_veto` | `test_native_file_veto_denies_replay` | B, or C if no supported permission fixture exists | Otherwise-authorized clinician has genuine native File read veto despite coherent durable evidence; replay withholds saved (currently unknown). Earlier Patient/role denial or mocked inspection is not File-veto proof. |

**Source anchors:** `publication/coordinator.py` `_commit`, `_prepare_phase`, `_publish_phase`, `_execute`; `publication/exclusion.py` `LockedOwner` and continuation methods; `publication/journal.py` `prepare`/`publish`; `access/frappe_context.py` `_publication_boundary`, `require_file_insert`, `require_file_rollback`, delete guards; `access/file_mixin.py` duplicate/rollback/trash hooks; `publication/inspection.py` `_require_current_file_access` and current-clinician gates. These are inspection targets, not authorization to invoke internals positively from a test.

## Approaches and recommended order

1. **Fixture-control design first (recommended):** settle a reversible, operator-controlled protocol for one owned synthetic actor. Smallest candidate is disabled/revoked replay. No production mechanism change; runtime window still separate.
2. **Observability design first:** could unlock more C rows, but requires a supported test-harness design and explicit safety/faithfulness review. No approved production fault/pause/commit-error seam exists. Do not install one by default.
3. **Run only the eight source-executable cases:** can collect useful bounded native evidence after separate runtime approval, but cannot close the remaining17 or overall matrix. This is an optional subset window, not full acceptance.

## Approval packets and execution order

### Gate L — local fixture-control design and subsequent authoring

**Files:** proposed next test slice: `test_publication.py`, `tests/unit/test_publication_native_cases.py`, new test-only `clinic_photos/tests/revocation_probe.py` and `tests/unit/test_revocation_probe.py`, and necessary `test_case_runner.py` pins. The existing manifest row may change readiness only after BOTH disabled and clinician-role-removal arms meet review; no partial ID/row is added. Actual implementation brief must be approved separately after control design.

**Consumes:** current published fixture helpers and `inspection.resolve_operation(operation_id, *, actor=...)`; existing native user/permission APIs only after verification. **Produces:** a reviewed operator-action protocol with exact synchronization, fixture ownership, revocation action, native denial result, safe restoration and residue evidence.

- [x] User selected LOCAL design for BOTH enable/disable and clinician-role-removal arms of `pub.access.disabled_or_revoked_actor`, with external operator actions. Patient/UserPermission revocation remains separate. Final revised design and implementation scope still require approval.
- [ ] Define how an authorized operator acts after publication without exposing credentials to tests or adding a production control endpoint. Record canonical synthetic actor identity and ownership; missing acknowledgement fails closed.
- [ ] Define restore/stop behavior. Revocation is an intentional fixture change, not a publication retry or latch reset; no real identities may be touched. Failure to restore stops the window and preserves evidence.
- [ ] Review proposed control protocol before writing the native body. Then authorize a bounded authoring brief with behavioral RED/local GREEN/raw logs and separate Standards/Spec review.
- [ ] Test earlier-gate attribution, canonical identity collisions, observer/author restoration and fixture-action failure locally. Native proof remains UNRUN until Gate N.

### Gate O — C-case observability decision (separate, not bundled)

**Files:** design document only until approval; no coordinator/writer/permission implementation changes are authorized.

- [ ] Select one concrete C obligation and design a supported test-harness-only control/observer with exact process, owner and transaction semantics.
- [ ] Demonstrate that its evidence would distinguish the intended failure from an earlier refusal; preserve real native File/hook/commit chain.
- [ ] Explicitly reject runtime/core monkeypatches, production fault flags, fabricated ownership/reservation rows or outcome-only substitutions. If no compliant mechanism is established, retain BLOCKED and report the unmet claim.
- [ ] Obtain separate approval before implementing or executing any mechanism. No unchanged requirement is waived by this plan.

### Gate P — fresh read-only development preflight

**Proposed target (historical, must be re-identified):** `sunphunkosol@10.0.100.10`, existing stack `/home/sunphunkosol/docker/clinic-photos-accept/`, exact site `clinic-photo-accept.local`; key path `~/.ssh/id_rsa_nop`. No credentials in evidence.

- [ ] Approve fresh read-only remote inventory scope. Record actual site/database, container IDs/images/mounts, sites cwd/venv, backend/queue/scheduler copies, pending work and owned processes.
- [ ] Compare source candidate hashes on every participating copy; source revision alone does not prove installed schema/configuration.
- [ ] Read installed operation columns/indexes and current DB profile without running operation readers that depend on absent columns. Reconfirm gates OFF and effective write/security hook compatibility.
- [ ] Produce a site-bound preflight report and exact deployment/schema delta before approving changes. Unknown targets, callbacks, source drift or activity stop the packet.

### Gate D — development deployment and narrow schema synchronization

- [ ] Approve the exact candidate revision/copy targets, scheduler registration implications and narrow schema delta from Gate P. No images/dependencies/services added.
- [ ] Account for existing synthetic state and preserve it by default. Obtain the responsible owner's development recovery decision; this is not a production backup/restore claim.
- [ ] With gates OFF and no active publication, deploy identical reviewed candidate bytes to every participating copy; verify actual hashes and necessary reload/restart scope separately before executing it.
- [ ] Proposed schema action is narrowly `frappe.reload_doc('clinic_photos', 'doctype', 'clinic_photo_save_operation', force=True)` in a separately specified supported maintenance invocation. This is forward-only and NOT authorized here; no full migrate/raw ALTER. Publication entry remains the fixed module, not this maintenance context.
- [ ] Verify all22 fields plus nullable unique reservation index and unchanged unrelated schema; retain raw output/exit. A failed or ambiguous synchronization stops; never replay an uncertain mutation automatically or silently downgrade schema/code.

### Gate N — bounded synthetic native window

- [ ] Approve exact case IDs/subset, reviewed source hashes, canonical external fixture identities and any Gate L/O controls individually. Required missing cases keep overall acceptance false.
- [ ] Approve exact temporary site configuration changes and duration: publication/synthetic flags exactTrue only on the disposable site. No allowlist entry needed for non-allowlisted denial; any positive operator audit requires a separate named membership approval.
- [ ] Use frozen isolated runner protocol/selector, trusted source root, expected site and unique nonces. Fix the existing Task5 owned-PID cleanup portability issue under a separate Task11 brief before relying on affected process evidence.
- [ ] Preserve phase/owner/fault cases blocked unless their approved control exists. Run each aborting case in fresh process; no latch reset or uncertain insertion retry.
- [ ] Retain raw stdout, exact exits, selected test identity/count, coherent operation/File/domain/version/bytes and conservative failure residue. Disk hashes do not make snapshot reads atomic; a partial inventory is not absence proof.
- [ ] For HTTP/queue tests, approve their own case list and bounded queue-outage/rollback controls. Native sid+explicitCSRF via HTTPS or loopback frontend `127.0.0.1:8082`; never send credentials to8080. Existing21private HTTP cases follow readiness, not before.
- [ ] Close temporary gates after the window, verify configuration restoration and owned-process/queue accounting without assuming timeout killed a child. On uncertainty stop new submissions; preserve PREPARED/residue and do not re-enable merely for diagnostics.

### Gate F — final reconciliation and disposal decisions

- [ ] Reconcile all writers and separate reviews. Only then explicitly authorize Task11 full local discovery ONCE: `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -v`. Save raw count/skips/exit; inspect failure excerpts before repair. Later edits invalidate affected evidence.
- [ ] Report per-case passed/failed/blocked/unrun. Frozen required IDs/identities, trusted source/site binding and disposal evidence govern; subset success is not complete acceptance.
- [ ] Retain the development stack by default. Exact-stack disposal needs separate owner approval and identity-complete evidence for DB/private files/volumes/resources; no individual protected-file purge or broad prune to make checks green. Operator attestations are not authenticated physical observations.
- [ ] Keep real-data activation separate and blocked pending retention/purge, off-device consistent backup and tested restore policy.

## Self-review and handoff

Local manifest enumeration verified exactly17 unique blocked IDs; source schema enumeration verified22 fields. The matrix preserves each full claim, separates mixed B+C controls and avoids the earlier three-versus-eight missing-column confusion. Gates L/O address proof attribution and test control; P/D establish actual installed candidate/schema; N addresses timing/unknown outcome/residue; F preserves honest evidence, full-suite authority and disposal limits. No step grants the permission its checkbox requests.

**Recommended next approval:** Gate L LOCAL fixture-control design for disabled/revoked actor replay only. This is the smallest useful decision; it does not enable runtime mutations or close any native gate. Keep the selected specialist execution/separate review method. Review this plan before authoring or approving any remote packet.
