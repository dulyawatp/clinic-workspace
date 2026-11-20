# Cashier Full Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Safely post and print full Cash and Credit Card settlements from Clinic.

**Architecture:** The existing cashier API locks and verifies settlement through native ERPNext commit. React uses that verified capability and native receipt read/print paths. Legacy deployments stay read-only in the new Clinic screen.

**Tech Stack:** React, Frappe/ERPNext v16, MariaDB, unittest, Vitest and Playwright.

**Spec:** [Cashier full settlement and native receipts](../specs/2026-11-20-cashier-full-settlement-design.md).

## Global Constraints

- No new writer, payment budget increase, permissions, doctor billing, Desk behavior, or shared SIT changes.
- Cash or Credit Card only; one invoice, one allocation, full balance, no conversion.
- Keep duplicate protection and uncertain-outcome stop behavior.

## Review Focus

- A concurrent earlier commit makes the reviewed amount stale; locking must survive through native commit.
- Missing or inconsistent account currency or precision fails closed; no implicit exchange conversion or rounding into a match.
- A false success must roll back; no receipt on ambiguous posting.
- Old deployments must not enable confirmation merely because their partial-capable write flag is enabled.
- Native read and print permissions are verified separately from purchase or payment authority.

### Task 1: Exact settlement contract and transaction

- [ ] Add failing tests in `tests/unit/test_finance_contract.py` and `test_endpoints_finance_write.py` under `frappe-apps/clinic_ui` for stale/partial/over/split, incompatible account/precision, exact residual, nonzero and misallocated post rollback, and unchanged uncertainty/replay/ACK semantics.
- [ ] Update `core/finance_contract.py`, `endpoints/finance_write.py` and `runtime/frappe_gateway.py` under `frappe-apps/clinic_ui/clinic_ui`: hold lease, fresh locked invoice and ledger verification, native post, voucher-scoped verification, native commit, then terminal fence. Keep endpoint and payload shape. Confirm zero/paid or roll back. Preserve failure classifications and leave enabled capabilities unchanged until Task 2.
- [ ] Run focused Python modules and validate pinned writer interleavings on an isolated site. Do not assert site-wide race safety.

### Task 2: Cashier confirmation and receipt

- [ ] Add failing native session/finance tests for explicit verified full-only support and an older partial-capable deployment, then implement narrow capability advertisement and `NativeCashierPaymentPage.tsx` confirmation using the existing `receivePayment` transport. Re-read before confirmation, keep amounts read-only, keep the operation identity and stop on uncertainty.
- [ ] Test and implement the submitted, invoice-linked Payment Entry read and permission-checked native print action after verifying the installed native contract. Reject mismatched or unverified receipt and do not print on uncertain outcome.
- [ ] Run focused application, Python and contract tests. Review backend and UI changes together.

### Task 3: Disposable acceptance

- [ ] Prepare two isolated deployments with different project, site/header, loopback port, image tag, private state and all volumes. Verify no target overlaps shared SIT. Use existing cashier authority and dedicated synthetic company/currency/account/invoice settings; if unavailable, keep the corresponding action unverified rather than widening permissions.
- [ ] Run Python unit tests, `pnpm typecheck`, `pnpm test`, `pnpm --filter @clinic/app build:native`, and `git diff --check`.
- [ ] Use the rebuilt UI to verify one synthetic Cash and one synthetic Card settlement on separate deployments. Inspect native persisted invoice, Payment Entry, GL/PLE and receipt output, and verify unauthorized sessions cannot view/print it. Capture private 1920×1080 evidence and report each unverified gate.
