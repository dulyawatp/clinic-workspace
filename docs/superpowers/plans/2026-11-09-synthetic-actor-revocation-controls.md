# Synthetic Actor Revocation Controls Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Author and locally verify both disable and clinician-role-removal arms of `pub.access.disabled_or_revoked_actor`, without executing native fixtures.

**Architecture:** An external operator alone changes/restores the dedicated synthetic actor. Fresh test-only processes prove baseline, changed state, application denials, unchanged publication evidence and exact restoration; the parent never refreshes its transaction to see external changes. Keep the production publication entry and isolated-runner protocol unchanged.

**Tech Stack:** Existing Python stdlib unittest/subprocess/JSON/hashlib, existing Frappe native APIs and publication contracts. No dependencies or services added.

**Spec:** `docs/superpowers/specs/2026-11-09-synthetic-actor-revocation-controls-design.md` (user approved revised design). Approval packets: `docs/superpowers/plans/2026-11-09-photo-publication-blockers-and-validation-approvals.md`, Gates L/O/P/D/N/F.

## Authorization and Global Constraints

- User approved bounded LOCAL implementation after plan review. Native execution and runtime actions remain separately gated.
- BOTH arms and EXTERNAL OPERATOR authority are selected; tests never write User/Has Role/UserPermission or implement an operator mutation command.
- Disabled arm: enabled=False with baseline roles retained. Role arm: enabled=True, clinician=False, removing only assigned `CANDIDATE_CLINICIAN_ROLES` (`Physician`, `Nursing User`). Unrelated roles remain unchanged.
- Restore exact original enabled value and role assignments; never add previously absent roles. Patient/UserPermission revocation remains separate.
- No commits, staging or pushes. No native/bench/remote execution, deployment, schema sync, gate changes, runtime fixture mutation or final full discovery under this plan's local authoring scope.
- No production/core patches, fault hooks, File ignore_permissions, protected-byte seeding, active DB swaps, refresh commit/rollback, latch resets, uncertain-write retries/deletion or automatic purge.
- Publication stays `python -m clinic_photos.publication.process` from `/home/frappe/frappe-bench/sites`; the probe is not another publication entry.
- Status is read-only; replay-denial calls a potentially mutating API and must never be labelled read-only. No explicit probe commit/rollback or notification registration.
- Bootstrap/adoption failure is not application denial. Operator acknowledgement is attestation, not changed-state/restoration proof.
- Canonical enabled synthetic verifier is separate from actor and Administrator; internal evidence does not grant clinical/File rights or require recovery-operator membership.
- Both arms must pass local review before readiness promotion; all native cases remain UNRUN. No claim of intermediate-phase revocation, HTTP-session behavior, cross-store atomicity or overall acceptance.
- Preserve separate Standards/Spec reviews and local behavioral RED/GREEN versus structural pins, mutants and native evidence. Retain protected residue and original failures.

## Review Focus

1. A wrong/stale acknowledgement or role snapshot must fail before a denial can receive credit (Tasks 1, 3).
2. Alias resolution or inability to adopt a disabled actor must not become application-denial evidence (Tasks 1, 2).
3. A correct denial accompanied by writes/callbacks or changed committed evidence must fail (Tasks 2, 3).
4. Timeout, surviving child or uncertain restoration must preserve the original failure and block the next arm (Tasks 3, 4).
5. Probe/bootstrap diagnostics and forged responses must not leak fixture identifiers or satisfy the frozen case through a weaker boundary (Tasks 1, 2, 4).

## File map and interfaces

Paths below are relative to `frappe-apps/clinic_photos/`:

| File | Responsibility |
|---|---|
| Create `clinic_photos/tests/revocation_probe.py` | Strict private protocol, inert CLI, fresh native probes and read-only evidence; no mutation controller. |
| Create `tests/unit/test_revocation_probe.py` | Protocol/bootstrap/authority/evidence behavior with narrow native doubles. |
| Modify `clinic_photos/tests/test_publication.py` | Bounded operator handoff, child lifecycle, two-arm orchestration and existing required body (currently near line1465). |
| Modify `tests/unit/test_publication_native_cases.py` | Behavioral orchestration/operator/process failure controls and narrow source pins. |
| Modify existing row in `clinic_photos/tests/case_manifest.json` | Readiness only after both arms reviewed; unchanged ID/module/class/method/claim. |
| Modify `tests/unit/test_case_runner.py` only if existing readiness pins require it | Expected source readiness, never frozen identity/protocol changes. |

Do not modify `publication/*`, `access/*`, hooks, DocTypes or `run_isolated_cases.py`. If an interface cannot be implemented within this scope, stop and request a design amendment rather than adding a production seam.

### Private protocol decisions (shared by every task)

Use schema `1`, strict exact keys, duplicate-key rejection, no bool-as-int, bounded UTF-8 JSON and no arbitrary object deserialization. Canonical JSON for digests: sorted keys, compact separators, UTF-8; role assignment list sorted with duplicate multiplicity preserved. Digests bind complete private structures, not claims of authenticated physical observations.

**Probe input:** exact keys `schema, run_nonce, invocation_nonce, site, source_digest, operation_id, actor, verifier, arm, mode, baseline, request`.

- Nonces: distinct 32 lowercase hex; operation ID existing 32hex contract; source_digest 64hex from the existing reviewed source-root digest; site exact approved disposable site. Actor/verifier: nonempty bounded User names, max140 characters, distinct, neither Guest nor Administrator; later resolve and compare canonical stored User.name exactly.
- `arm`: `disabled|role_removed`; `mode`: `baseline|status_denial|replay_denial|verify|restored`. Each invocation is a fresh process with a new invocation_nonce. Only baseline accepts baseline=null; remaining modes receive the exact prior baseline object. Only replay_denial carries request; other modes require request=null.
- `request` is the original API wire object reconstructed from SaveRequest (base64 exact image bytes; parsed canonical annotation where applicable), validated by `contracts.validate_request(payload)`, never a parallel SaveRequest schema. This case uses `synthetic_original_create`; bind `journal.payload_digest(request)` and `journal.identity_digest(site, actor, kind, request_key)` to the existing PUBLISHED operation before calling replay once. Do not call `load_submission` on a PUBLISHED row.
- Bound the complete input to existing `validation.MAX_REQUEST_BYTES + 65536` bytes; bound request separately to MAX_REQUEST_BYTES. Read limit+1 then refuse oversize/trailing content, malformed/deep JSON or unknown keys with fixed safe diagnostics.
- Parent serializes before launch to a 0600 temporary file inside the private case directory, opens a read-only descriptor, closes the writer and unlinks the name before spawning; child receives it as stdin. No payload/env/argv secrets and no payload files left on disk by normal launch. Clean failed-prelaunch transport files only by exact owned identity; never clinical evidence.

**Private baseline object:** exact keys `enabled, roles, actor_digest, operation_digest, evidence_digest, counts`. `roles` is the complete canonical Has Role assignment projection `{role, parenttype, parentfield}` for this User, sorted lexicographically with multiplicity preserved; restoration requires exact equality of assignments, not newly generated child-row IDs or display order. Bind the private baseline to its canonical SHA256. `counts` has exact nonnegative integer keys `operations, files, photos, versions`; use scoped Patient/actor evidence where existing fixtures permit and whole-DocType counts otherwise, with the future native window explicitly quiescent. Concurrent unrelated writes cause refusal, never normalization.

`operation_digest` covers site/actor/kind/request_key/payload_digest/state, generation/reservation and complete publication link/version/expected bytes/creation identity from the coherent existing operation. `evidence_digest` additionally covers exact linked File/photo/version persisted identities and ownerless committed-byte verification (digest, size, device/inode). Reuse existing journal/writer verification and existing helper evidence fields; do not invent missing records, trust counts alone or open arbitrary supplied paths. Verifier checks canonical current enabled clinician state before internal evidence reads. A baseline also adopts the actor and requires actual `resolve_operation(...).status == 'saved'` before returning evidence.

**Probe output:** one JSON line, max16384 bytes; exact keys `schema, run_nonce, invocation_nonce, site_digest, source_digest, operation_id, arm, mode, result, enabled, clinician, baseline_digest, evidence_digest, counts, clean`.

- Result enum `observed|refused|failed|unavailable`; exit mapping 0/2/3/4 respectively. Only exit0 + result observed + every binding/state/evidence assertion receives credit. No raw exception, stdout noise, actor/Patient/File/domain/role names, paths or request data. Nullable evidence/state fields on unsuccessful results are allowed; never synthesize success defaults.
- Baseline and restored modes additionally return their full private baseline through a second anonymous descriptor supplied by parent, capped65536 bytes; it is not stdout or a retained public artifact. Parent supplies `CLINIC_PHOTOS_REVOCATION_BASELINE_FD` as the decimal writable descriptor number via a minimal child environment and pass_fds; it is required only for these modes and rejected otherwise. Use an anonymous temporary regular file (not a potentially blocking pipe), opened before spawn and read after verified child exit; no inherited descriptors beyond stdin/stdout/stderr and this one. Denial/verify modes must not write it. This allows exact role restoration without publishing role names.
- CLI: `python -m clinic_photos.tests.revocation_probe --site SITE --operation-id ID --mode MODE`; verify equality to stdin. Executable/cwd/source root come from existing trusted harness configuration, not payload. Module import performs no native import, process launch or I/O.

**Operator channel:** external future runtime packet supplies `CLINIC_PHOTOS_REVOCATION_CONTROL_DIR` (absolute, existing case-owned directory, effective-UID owned, mode0700, no symlink components); actor uses existing configured synthetic clinician, verifier uses new test-only `CLINIC_PHOTOS_REVOCATION_VERIFIER`. Missing configuration refuses before staging. No production config key is added.

- Each arm writes one private `ACTION_NONCE.request.json` using O_EXCL/O_NOFOLLOW,0600. Exact keys `schema, run_nonce, action_nonce, site, source_digest, operation_id, actor, arm, action, baseline_digest, baseline_roles`; action `change|restore`, unique32hex action_nonce; baseline_roles carries the private assignment projection. No operator credentials. Logs emit only `revocation_wait action_nonce=HEX action=change|restore arm=...`.
- Operator independently authenticates/commits and atomically publishes `ACTION_NONCE.ack.json`,0600. Exact keys `schema, run_nonce, action_nonce, site, source_digest, operation_id, actor, arm, action, baseline_digest, status`; status must be `done`, otherwise refuse. Strict equality to request; max4096 bytes; ownership/mode/regular-file/no-symlink checks at open and fstat; pre-existing/replayed/duplicate acknowledgement fails. Test consumes once, never executes its contents. An acknowledgement is unauthenticated operator attestation under filesystem trust, not DB proof.
- Wait by monotonic deadline120s per acknowledgement, poll at0.2s, no resubmission/retry. Each probe deadline30s; total case deadline1200s (existing isolated runner default1800s unchanged). Refuse insufficient remaining budget before an action. Runtime packet must reserve restore time; timeout is never restoration proof.
- On any failure after change request issuance, attempt one independently correlated external restore handoff in a finally path within the reserved budget, then fresh restored probe. This is fixture restoration, not publication retry. Preserve primary failure and append a fixed restoration-incomplete note; no next arm after any failure. If operator cannot restore the exact baseline role assignments, stop rather than silently weakening equality.
- Child timeout: terminate only owned Popen process/group, bounded2s wait then kill and bounded2s reap; inability to prove exit marks surviving-child/uncertain failure. Never broad process kill or ps-dependent discovery. No probe relaunch. Preserve known operation/keys and existing residue-incomplete semantics.

## Task 1: Strict private protocol and request/evidence bindings

**Files:** new probe module and its new unit test. **Consumes:** contracts.validate_request, journal.payload_digest/identity_digest, private protocol above. **Produces:** `decode_input(raw: bytes) -> dict`, `encode_request(request: SaveRequest) -> dict`, `validate_response(document: dict, expected: dict) -> dict`, `baseline_digest(baseline: dict) -> str` in the probe module. Functions are pure; Frappe is lazy-imported only in Task2.

- [ ] Write behavioral tests `test_exact_request_roundtrip`, `test_payload_or_identity_mismatch_refuses`, `test_duplicate_unknown_deep_oversize_input_refuses`, `test_bool_counts_and_wrong_bindings_refuse`, `test_private_baseline_digest_binds_all_roles`, `test_public_response_contains_no_private_identity`. Assert exact original decoded bytes, kind/request_key equality and digest equality, not constructor aliases such as annotation_json or operation.id.
- [ ] Run new focused suite; capture behavioral RED from real validation/binding assertions against a minimal callable implementation, not missing symbols/imports.
- [ ] Implement the four exact interfaces and bounded schema validation; use existing image/annotation validator and reject mismatches before native bootstrap. No generic protocol framework.
- [ ] Run focused suite GREEN; retain exact command, exit and raw log. Stop at this independently reviewable private-boundary deliverable; do not commit.

## Task 2: Fresh native baseline, denial, verification and restoration probes

**Files:** same probe module/unit tests. **Consumes:** Task1 interface. **Produces:** `run_probe(document: dict) -> tuple[int, dict, dict | None]` (exit, public response, private baseline) and `main(argv: list[str] | None = None) -> int`; native bootstrap/evidence helpers remain private.

- [ ] Write `test_bootstrap_alias_disabled_adoption_and_verifier_refuse` with canonical User lookup and set_user failure doubles. Test wrong site, gates not exactTrue, actor/verifier equality, alias spelling, unclean callback manager or nonzero/unknown write counter. No target boundary call on refusal.
- [ ] Write parameterized `test_both_changed_states_require_exact_denials`: disabled arm enabled=False plus original roles retained; role_removed enabled=True with all candidate roles absent and unrelated role assignments unchanged. Require exactly InspectionDenied for status and SubmissionAuthorityDenied for replay; unknown/saved/pending, different exceptions and bootstrap refusal fail.
- [ ] Write `test_denial_with_write_or_callback_fails`, `test_request_binding_precedes_replay`, `test_baseline_saved_and_verification_bind_bytes_links_counts`, `test_restoration_requires_exact_assignment_snapshot`, `test_failure_output_is_bounded_and_redacted`. A successful denial plus changed transaction counter/callback list fails even if fresh committed readback later matches. Test verifier never calls clinical status or operator audit for the actor.
- [ ] Capture focused behavioral RED; implement native init/connect(set_admin_as_user=False), exact site/gates/clean-context checks, canonical actor/verifier and native set_user adoption without bypass. Mirror inspected process.py clean-context checks read-only; do not invoke its publication run function. Unknown installed shapes refuse.
- [ ] Baseline/restored/verify use the enabled verifier for internal coherent evidence and byte proof. Baseline/restored additionally adopt the actor for saved positive control. Status/replay check changed actor facts in the same fresh connection as the target call. They never commit/rollback or register notifications; compare all four callback-manager contents and transaction-write counter before/after without clearing them. Destroy native context finally; destruction is not proof writes never happened.
- [ ] Implement CLI input/output boundaries; suppress raw framework diagnostics from the public response path, map all unexpected failures to fixed classifications while keeping detailed private local-test evidence free of payloads. Test context destruction failure cannot preserve observed success. Capture focused GREEN.
- [ ] Retain a source-evidence prerequisite for later native approval: pinned Frappe16.53.0 init/connect/set_user semantics, including disabled/role-revoked adoption. Existing retained evidence is reusable only if exact source/version binding matches; installed drift/adoption refusal remains native BLOCKED, not a local bypass.

## Task 3: External handoff, owned child lifecycle and complete two-arm case

**Files:** existing test_publication.py and test_publication_native_cases.py. **Consumes:** probe protocol, existing original_request/stage_and_commit/publish/record_residue, existing trusted source binding. **Produces:** test-side `run_revocation_probe(document: dict) -> tuple[dict, dict | None]`, `await_revocation_action(binding: dict, *, deadline: float) -> None`, `exercise_revocation_arm(arm: str) -> None`; place alongside existing helper methods without reorganizing unrelated tests.

- [ ] Write behavioral tests `test_operator_ack_exact_once_private_and_bounded`, `test_stale_wrong_identity_action_ack_refuses`, `test_unsafe_control_directory_refuses_before_stage`, `test_child_timeout_survival_and_forged_response_fail`, `test_restore_attempt_preserves_primary_failure`, `test_restore_ack_without_state_restored_blocks_next_arm`, `test_both_arms_have_independent_baselines_and_saved_controls`. Use injected stdlib/process/native boundaries, not a live process or User mutation. Mock monotonic time for deterministic bounded waits.
- [ ] Capture behavioral RED for actual orchestration assertions. Implement exact channel/deadlines/transport/owned child handling above, retaining private files as restricted evidence for the operator window; no broad cleanup or sensitive log dumps. Source digest must cover the new probe through the existing source-root algorithm; prove coverage with a local test, do not change runner algorithm.
- [ ] Replace only `test_disabled_or_revoked_actor_cannot_replay` placeholder with sequential disabled then role_removed arms. Each arm uses its own real original/publish path and complete known residue inventory before operator action; obtains fresh baseline, changed status/replay, separate fresh verifier equality and fresh restored saved control. No extra parent commits or stale post-action reads. Stage/publish only through existing helpers; no helper publication through probe.
- [ ] Integrate failure evidence: pre-register operation/key identities as existing helpers require; on changed state or uncertainty mark residue incomplete without letting inventory errors replace primary failure. Restoration is attempted externally after issued action even when acknowledgement was lost; it never authorizes another publication or next arm after failure.
- [ ] Run both focused helper suites GREEN with exact commands/logs. Add source pins only as supplemental evidence: no User writes, no explicit probe commit/rollback, no publication process status misuse, both arms required and no operator allowlist mutation. Do not promote readiness yet.

## Task 4: Separate review, readiness and bounded reconciliation

**Files:** existing manifest row, existing runner-unit readiness assertions if necessary; evidence ledger. **Consumes:** Tasks1–3 raw RED/GREEN and source artifacts. **Produces:** reviewed source-executable case with native UNRUN, or an explicit retained blocker.

- [ ] Read produced artifacts/diff and confirm only scoped files changed. Review Standards and Spec independently using the preserved specialist workflow; no reviewer native/remote/full-suite execution. Standards checks privacy, callback/write observation and owned processes; Spec checks both actual guard paths, positive controls, exact restoration and unchanged immutable identity.
- [ ] Repair findings in the owning session. Re-run affected focused tests only; reuse still-valid logs. A role restoration/installed adoption limitation is not repaired by weakening assertions or adding production mutation hooks.
- [ ] After BOTH arms and controls pass local review, change only existing row readiness/blocked_reason according to manifest's existing schema. Keep its ID, class, method, description, expects_denial and native status unchanged; adjust only necessary readiness pins. Expected planning delta is9 executable publication/16 blocked (40 executable/16 blocked/56 total), not native acceptance.
- [ ] Run final focused reconciliation once after writers/reviews: the three unit suites below. Retain exact exit/status/test counts and excerpts; parent verifies artifacts/source hashes. No Task11 full discovery, native claims or disposal acceptance.
- [ ] Update authoritative progress ledger with review paths, logs, actual counts, remaining blockers and all runtime prerequisites. Stop for separate Gate P/D/N authority; do not execute the operator protocol or change any runtime state.

## Exact focused verification commands (future authoring only)

Run from repository root; save raw stdout/stderr under the existing `.superpowers/sdd/2026-11-08-frappe-async-photo-publication/` evidence area with unique task labels. Exit0 and no unexpected skip is GREEN; expected RED must cite assertion failures. Do not assert future test counts in advance.

```sh
PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_revocation_probe.py -v
PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_publication_native_cases.py -v
PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_case_runner.py -v
```

Tasks1–2 own the new suite; Task3 owns the new and native-helper suites; Task4 owns the final three-suite reconciliation. No unrestricted discover command, bench invocation or native CLI command is an execution instruction here.

## Self-review and handoff

Spec coverage: full two-arm sequence→Task3; private inputs/ack correlation→Tasks1/3; native adoption/attributed denials→Task2; ownerless evidence/separate verifier→Task2; exact restoration/stop/surviving child→Tasks2/3; immutable runner/native limits→Task4. Review Focus has explicit named tests in each owner task. Interfaces and wire field names are consistent; task outputs are reusable without a new production abstraction. The plan adds no operator mutation automation and makes no native proof from doubles.

Approval needed before implementation. Preserve the existing specialist execution method and separate Standards/Spec review. Later runtime approval must name operator, exact synthetic actor/verifier/site/source, channel ownership, time window, quiescence, installed-schema/gates and native adoption evidence; this plan approves none of those actions.
