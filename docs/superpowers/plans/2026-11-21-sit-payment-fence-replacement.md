# Shared SIT Payment Fence Replacement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the shared SIT payment writer’s file-backed fence dependency without increasing its one-payment budget.

**Architecture:** One pre-provisioned InnoDB slot is locked and consumed in the ERPNext payment transaction. A shared server-side lane selector and deployment proof isolate changes from existing writers; the release predicate remains off.

**Tech Stack:** Frappe, ERPNext, MariaDB/InnoDB, Python `unittest`, existing React payment transport.

**Spec:** [Shared SIT payment fence replacement](../specs/2026-11-21-sit-payment-fence-replacement.md).

## Global Constraints

- Shared `marley-sit.local` deployment only; hostname alone is insufficient.
- Keep the one-payment limit, locked exact balance, native permissions,
  accounting checks, CSRF, and existing ACK wire shape.
- Keep the release gate off. No shared-SIT initialization, posting, or
  enablement during implementation acceptance. Preserve all old fence history.

## Review Focus

- Same hostname, different deployment identity: never select the new lane.
- Missing or ambiguous history: never provision an available slot.
- Concurrent payments against different invoices: at most one commit.
- Commit or ACK response lost: no automatic retry, quota stays spent.
- Existing invoice and payment fence records: legacy reads and ACK remain intact.

### Task 1: Deployment identity and transactional slot

**Files:** Create `frappe-apps/clinic_ui/clinic_ui/runtime/sit_payment_slot.py`
and `frappe-apps/clinic_ui/tests/unit/test_sit_payment_slot.py`. Keep schema
setup in a separately invoked operator step, not an app hook.

- [ ] Write tests for wrong deployment, missing row, already spent row, and a
  fresh slot; require exact server-owned identity and forbid fallback creation.
- [ ] Run `python3 -B -m unittest tests.unit.test_sit_payment_slot` from
  `frappe-apps/clinic_ui` and capture the expected failure.
- [ ] Implement `shared_sit_payment_identity(gateway) -> str | None`,
  `read_slot(gateway, deployment_id)`, and
  `lock_slot(gateway, deployment_id)` in the new module. Use the same Frappe
  connection as the posting query. Add `consume_slot(gateway, deployment_id,
  actor, operation_key, receipt) -> None`; update only the previously locked
  unspent row and fail closed unless exactly one row changes. No helper commits.
- [ ] Document operator schema initialization, row states, proof storage, and
  reconciliation. Explicitly fail closed on unavailable or unresolved legacy
  history; a confirmed legacy payment initializes spent. Test those decisions.
- [ ] Rerun the focused module; all cases must pass.

### Task 2: Select the lane, post, and acknowledge

**Files:** Modify `frappe-apps/clinic_ui/clinic_ui/endpoints/finance_write.py`,
`endpoints/common.py`, `endpoints/session.py`, and `runtime/frappe_gateway.py`
under the same package. Extend the finance endpoint/session unit tests and
`app/src/transport/native/finance-write.test.ts`.

- [ ] Write failing cases for no legacy fence access on the new lane, shared
  history preservation, denied permissions and CSRF, missing/spent slot,
  rollback after post-verification failure, and the release gate remaining off.
  Pin lost or invalid ACK to the existing persisted browser stop behavior.
- [ ] Run the existing focused finance endpoint/session and transport tests;
  capture the new failing cases.
- [ ] Share the lane decision across bootstrap, dispatch and ACK. On the new
  lane reset the snapshot, lock slot → invoice primary key → both PLE ranges,
  recheck authority and facts, post, verify, consume the slot, and commit once.
  Derive ACK from the committed receipt and bind it to the actor and operation.
  Do not reopen the slot or interfere with historical fence-backed ACKs.
- [ ] Rerun the focused tests and verify old lanes still dispatch and ACK via
  the fence, and no site advertises the closed release gate.

### Task 3: Disposable verification

- [ ] On a separately approved disposable image and database, run two
  connections against different synthetic invoices and prove only one payment
  and one slot consumption commit.
- [ ] Inject a rollback before commit and prove neither native accounting
  rows nor the slot change persist. Drop the commit response and prove
  persisted accounting and slot consumption agree. Verify the invoice and
  PLE locks remain held through commit and ACK does not restore quota.
- [ ] Without changing shared SIT, run Python unit discovery, `pnpm typecheck`,
  `pnpm test`, `pnpm --filter @clinic/app build:native`, and `git diff --check`.
  Record exact commands, outputs, and unverified cases. Treat any shared
  initialization or release enablement as a separate decision.
