# Native Cashier Payment and Receipt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make invoice selection and receipt access native, and enable full-balance payment only when the installed backend supports it.

**Architecture:** Reuse the posted invoice reader and existing guarded payment transport. Add read-only discovery and receipt views only over verified native contracts; no alternate writer.

**Tech Stack:** React, TypeScript, Frappe/ERPNext, Vitest/MSW, Playwright.

**Spec:** [Native cashier payment and receipt](../specs/2026-11-20-native-cashier-payment-receipt-design.md).

## Global Constraints

- One invoice, one payment method, full current outstanding balance. The amount is read-only. Only Cash and Credit Card.
- No backend changes, permission grants or alternate write path. No live writes, permission changes or deployment without separate approval.

## Review Focus

- Billing customer is not assumed to be the patient.
- THB is a filter, not an exchange rate or settlement currency override.
- A stale or concurrent balance change must not result in partial posting through Clinic.
- Uncertain submission, unreadable details, and disallowed printing have distinct outcomes.

### Task 1: Verify the installed contract

- [ ] Trace `app/src/transport/native/finance-write.ts`, `frappe-apps/clinic_ui/clinic_ui/core/finance_contract.py`, and `frappe-apps/clinic_ui/clinic_ui/runtime/frappe_gateway.py`. Check authorization, posting-time full-balance enforcement, configured payment currency, reference handling and recovery. Record whether the existing action is safe for the full-only UI; do not infer atomicity from a read or a UI check.
- [ ] Verify as the synthetic CAS role which invoice list/detail fields and explicit patient relationships are readable. Check whether submitted Payment Entry read and the configured native print format can be verified without writes. Treat missing fixtures, ambiguous currency settings, and unverified print permissions as blockers to enabling those actions.

### Task 2: Invoice selector and payment

- [ ] Add RED tests in `app/src/features/finance/finance-write.test.tsx`: THB default, currency clears selection, only positive-balance submitted invoices, separate patient/customer identity, read-only amount, card reference only, stale balance stops, and backend evidence gate. Use mocked native data; no demo fallback.
- [ ] Extend `app/src/transport/native/posted-invoice.ts` or add a dedicated read adapter using Task 1’s proven list contract. Wire authorized selection in `app/src/features/finance/PaymentsPage.tsx` and `finance-write.tsx`. Preserve role/capability checks and the existing no-retry lock. Only enable posting when Task 1 confirms full-only settlement.
- [ ] Run `pnpm --filter @clinic/app test src/features/finance src/transport/native/posted-invoice.test.ts src/transport/native/finance-write.test.ts` and confirm GREEN.

### Task 3: Receipt and verification

- [ ] Add receipt RED tests for linked submitted Payment Entry, wrong ID or allocation, denied/missing data, uncertain result, and print access. Add a dedicated native receipt read adapter and connect it to the confirmed result only if Task 1 establishes the native read contract; add native print only where verified. Do not fabricate a receipt.
- [ ] Run focused tests, `pnpm typecheck`, `pnpm test`, `pnpm --filter @clinic/app build:native`, and `git diff --check`. Verify the rebuilt flow against SIT with read-only requests and cover missing records in offline tests. Report any live posting or receipt steps not verified.
