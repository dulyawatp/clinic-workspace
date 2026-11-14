# Synthetic actor revocation test controls — proposed local design

**Status:** revised design approved by user for concrete implementation planning. User selected BOTH disable
and clinician-role-removal arms and EXTERNAL OPERATOR authority. No implementation,
fixture mutation, deployment, schema/gate changes or native execution is authorized.

**Parent:** `docs/superpowers/plans/2026-11-09-photo-publication-blockers-and-validation-approvals.md`, GateL.
**Binding architecture:** `2026-11-08-frappe-photo-publication-entry-design.md` in this directory.

## Goal and explicit limit

Prove that an actor who could obtain a known saved publication while enabled cannot
obtain saved status or replay successfully after an authorized operator either disables
that canonical synthetic User or removes its clinician roles. Preserve bytes, operation identity and original failure
evidence. Avoid stale-parent-snapshot results and avoid production test hooks.

The single required case `pub.access.disabled_or_revoked_actor` must cover BOTH arms:
disabled with clinician-role baseline retained; enabled but with all assigned roles in
the existing `CANDIDATE_CLINICIAN_ROLES` set removed. Native Patient/UserPermission
revocation and File-veto attribution are different controls, not demonstrated here.
The required case remains blocked until both bodies and their controls are reviewed;
this document itself grants no manifest promotion or native acceptance.

## Existing facts and why another control is needed

- `inspection.resolve_operation(operation_id, *, actor)` freshly checks enabled clinician
  before loading durable evidence; a disabled actor must raise InspectionDenied.
- `submission.stage_submission(request, actor)` checks target/current actor authority
  before journal staging; a disabled actor must raise SubmissionAuthorityDenied.
- `current_actor()` queries enabled/roles each call, but queries can share one DB snapshot.
  Existing publish helper starts a parent read transaction after its legitimate commit.
  An external disable may therefore remain invisible to the parent.
- Do not add refresh commit/rollback, replace local.db, reset a latch, or launch the
  production publication module against PUBLISHED merely to elicit an earlier refusal.
- Native existing-HTTP-session invalidation after disabling is not pinned by current
  evidence. No live collation/auth/configuration behavior was observed for this design.

## Approaches

1. **Selected by user: fresh test-side probes plus external operator handoff.** Uses existing
   native init/connect/set_user semantics, app authority checks and real published fixture.
   Adds a narrowly scoped test helper process, no production endpoint/configuration flag.
2. **Existing HTTP status/save.** Fresh requests avoid parent snapshot staleness and use
   real sid/CSRF, but disabled-session handling may deny at the native login layer before
   the application guard. Useful later HTTP evidence; insufficient alone for this claim.
3. **Refresh or patch parent context.** Rejected: active DB swaps/runtime patches and
   arbitrary transaction resets violate the existing architecture; doubles are local
   controls only, not native revocation evidence.

User selected the full two-arm scope and external operator. The new test-side process
and operator protocol are included in this revised design for final approval before an
implementation brief is written. Test-owned User mutation was considered simpler but
not selected; authority separation is intentional, not additional proof of state.

## Proposed sequence

Run each arm with independent action/probe bindings and a fresh enabled-clinician
baseline. An arm cannot reuse another arm's denial or restoration result.

1. Read-only prerequisites establish exact approved site/schema/gates, canonical actor,
   synthetic ownership and enabled baseline. Actor is a dedicated case-owned synthetic
   identity; never reuse a real user or alter another test's shared active identity.
   Snapshot the exact original enabled value and role assignments for restoration.
2. Existing authorized staging → owned commit → fixed real publication child creates a
   genuine original. Capture coherent operation/File/domain/byte evidence and complete
   known residue before disabling. Record saved baseline through actual current access.
3. Parent emits a fixed control-wait state. Authorized operator receives site, case/run
   binding, opaque operation and canonical fixture identity through a restricted channel,
   authenticates independently and performs only the selected arm: (a) commit
   User.enabled=0 without changing roles, or (b) keep enabled=1 and remove all currently
   assigned clinician-candidate roles, leaving unrelated roles unchanged. No operator
   credential is given to tests/artifacts; no allowlist/Patient/UserPermission change.
4. Operator acknowledgement binds the exact run/action/fixture identity. It permits
   observation but is an attestation, not proof that DB state changed. Missing, mismatched
   or ambiguous acknowledgement stops the case; no blind re-disable or publication retry.
5. Start independent fresh probe processes after acknowledgement, with server-selected
   executable/sites cwd/site and opaque operation only in argv. Canonical actor and run
   binding travel through controlled environment or private input, not public output.
   Each process uses native init/connect(set_admin_as_user=False), verifies site, adopts
   canonical actor, and freshly proves the selected state before invoking the boundary:
   disabled arm enabled=False with baseline clinician classification retained; role
   arm enabled=True and clinician=False. Failure to adopt the identity is a bootstrap
   failure, never evidence that the application guard denied access.
6. **Status probe:** call resolve_operation; require exact InspectionDenied/fixed safe
   denial, not unknown/pending/saved and not a bootstrap error. This arm is read-only.
7. **Replay-denial probe:** feed the exact original validated request privately from the
   parent (not argv/logs), verify tuple/digest binding, and call stage_submission once;
   require exact SubmissionAuthorityDenied before journal/notification work. It must not
   call the harness's stage_and_commit helper, register a notification or explicitly
   commit/rollback. This is a **negative probe of a potentially mutating API**, not a
   read-only observer: on a regression the staging boundary could write. Unexpected
   success/write/callback evidence fails and stops; preserve uncertainty and resources,
   never hide it with rollback/cleanup or assume destruction proved absence of writes.
8. A separate fresh read-only verifier compares relevant committed row/link identities
   and counts to the baseline and performs the existing ownerless committed-byte proof.
   This internal test evidence grants the disabled actor no clinical read authority and
   is not operator inspect_operation; no allowlist membership is added. Parent post-disable
   DB reads are not used as freshness evidence. Cross-store/transaction atomicity is not claimed.
9. Operator restores the exact original enabled value and clinician-role assignments, with
   independently bound acknowledgement. A new fresh check must observe restoration;
   acknowledgement alone is insufficient. Fresh enabled/clinician classification and
   exact relevant role-assignment readback must match baseline before the next arm.
   Do not add roles that were absent originally. Restore failure/uncertainty stops the window,
   preserves evidence, prevents further submission, and is never reported as clean teardown.
10. Retain protected bytes/rows. No in-app purge or automatic exact-stack disposal follows;
    disposal needs its separate owner approval. Gates return to the approved end-of-window
    state under separate runtime authority, not by this test helper.

## Probe and handoff contracts to pin before implementation

- New test-only module candidate: `clinic_photos/tests/revocation_probe.py` under the app.
  It is inert on import, not whitelisted, not registered as a hook and not a second
  publication entry. Fixed modes: status-denial, replay-denial, read-only verification.
- Private request transport is bounded and closed before launch; exact current validator
  reconstructs SaveRequest. No PHI/credentials/session tokens in argv/stdout/diagnostics.
- Output is bounded, fixed-schema, bound to run nonce/site/source/mode/operation; reports
  only safe classification, enabled/clinician facts and approved proof digests/counts.
  It emits no actor/Patient/File/domain names, paths, payloads or raw tracebacks.
- Exact stdin/environment fields, allowlisted observer bootstrap, deadlines, exit meanings,
  write/callback detection and operator-ack channel must be specified and locally tested
  in the implementation plan. A transaction-write counter alone is not complete absence
  proof; committed fresh readback remains necessary. Unknown bootstrap refuses.
- Pin installed native init/connect/set_user behavior before relying on either arm:
  whether an already-disabled or role-revoked User can be adopted in this dedicated
  test process must be evidenced from pinned source and later the authorized window.
  No fallback impersonation bypass is permitted if adoption refuses.
- Request reconstruction uses `clinic_photos.publication.contracts.validate_request`
  (delegating to the existing validation implementation), with canonical request/digest
  equality to the original operation. Do not introduce another request schema.
- The post-action read-only verifier uses a separate externally supplied canonical
  enabled synthetic clinician, not the disabled/revoked actor or Administrator. It
  performs operation-bound internal test evidence reads and pure committed-byte proof;
  this is not clinical status for that verifier and grants no domain/File permissions.
  No recovery-operator membership is needed or added.
- Test runner BEGIN/END/RESIDUE protocol and frozen IDs remain unchanged. Additional
  helper artifacts do not themselves confer acceptance or authenticated disposal proof.
- Single fresh process per probe invocation, unique invocation binding. Parent may compare
  safe returned evidence but may not reuse a stale connection as post-disable proof.

## Local validation before any native window

Use narrow framework/process/operator-channel boundary doubles for BOTH arms to test: alias identity
collision; wrong-site/run/action acknowledgement; missing/malformed input; enabled actor
after claimed disable; wrong denial class; unexpected staging success or writes/callbacks;
probe timeout/surviving child; byte/link mismatch; restore acknowledgement without observed
restore. Capture behavioral RED before changes, not missing-symbol noise. Separately
review native attribution and protocol/privacy, not just counts. No double is native proof.

Potential source scope after approval: `clinic_photos/tests/revocation_probe.py`,
`tests/unit/test_revocation_probe.py`, existing `test_publication.py`,
`tests/unit/test_publication_native_cases.py`, necessary `test_case_runner.py` pins and
ONLY the existing required case's manifest row after BOTH arms meet local review.
No new required ID or partial registered case is introduced. Production modules, gates,
permissions, User records and installed schema remain untouched during local authoring.

## Decisions still requiring approval

1. Approve this revised full two-arm fresh-probe/operator-handoff design and explicit
   test-side file scope. User already selected both arms and external operator.
2. Then prepare a concrete implementation plan: private inputs/ack schema, action
   correlation, probe modes, positive controls, deadlines/exit handling and restoration
   checks must be pinned before writing code.
3. Later approve exact operator identity/action/ack channel and runtime mutation window.
   No such authority is granted now; deploy/schema/native packets remain separate.

**Recommendation:** approve this full two-arm design, then prepare its bounded local
implementation plan. Keep the required case blocked until both arms are authored and
reviewed, and native UNRUN until separately executed. This is not a completed mechanism
or permission to change production code.
