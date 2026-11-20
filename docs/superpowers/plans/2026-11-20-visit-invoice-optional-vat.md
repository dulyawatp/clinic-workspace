# Optional 7% VAT Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let eligible doctors preview and issue an appointment-backed invoice with explicit 7% VAT or authorized No VAT.

**Architecture:** ERPNext calculates and posts both modes using a server-verified context. Clinic binds that context into the signed preview and verifies native invoice and ledger facts before commit.

**Tech Stack:** Frappe, ERPNext, Python unittest, React, Vitest, Playwright MCP.

**Spec:** `docs/superpowers/specs/2026-11-20-visit-invoice-optional-vat-design.md`

## Global Constraints

- The consultation fee excludes VAT. Fixed or percentage discount reduces the fee first.
- No VAT requires both server-verified visit eligibility and independent doctor authorization.
- First release remains dedicated synthetic SIT only. Preserve the existing templates; no shared SIT, production tax configuration, payments, percentage other than 7%, inclusive pricing, or automatic tax classification.

## Review Focus

- Native default, inclusive or extra tax sneaks in: reject before posting or roll back.
- No VAT mode forged by a caller: deny before preview or submit.
- Changed VAT mode, template, account, or rate: refuse old preview.
- Native rounding or ledger split differs: refuse or roll back.
- VAT changes while a preview request is in flight: ignore the superseded result.

### Task 1: Native VAT terms and posting

**Files:** `frappe-apps/clinic_ui/clinic_ui/runtime/visit_invoice_{accounting,preview,posting,binding}.py`, `runtime/frappe_gateway.py`, `core/visit_invoice_token.py`, `endpoints/visit_invoice.py`, and matching Clinic Python unit tests.

**Interface:** Preview and submit accept required `vat_mode` (`"vat_7"` or `"no_vat"`); preview returns `currency`, `gross`, `discount`, `net`, `vat`, `total`, and `preview_token`. Amounts are fixed-point strings. Keep submit and lookup reply shapes unchanged. Provide a server-derived No VAT eligibility/authority flag for the appointment UI; enforcement remains server-side on both POSTs.

- [ ] Add failing tests for net 85.00, VAT 5.95 and payable 90.95; authorized zero VAT; missing/invalid mode; wrong, inclusive, disabled or extra tax; unauthorized/unproven No VAT; token replay with changed mode, rate or account; and persisted tax or ledger drift.
- [ ] Resolve exactly one verified ERPNext VAT template/account for the VAT branch. Prove No VAT eligibility using explicit trusted synthetic visit classification and doctor authorization; deny ambiguous/default tax contexts, Tax Rules and Item Tax. Apply only native discounts and taxes. Sign mode, normalized template/ledger facts and totals.
- [ ] Verify persisted net revenue, separate VAT liability and final receivable, exact invoice/visit references and unchanged stock. No VAT requires zero tax and only revenue/receivable rows. Preserve uncertain outcome/rollback behavior.
- [ ] Run targeted `python3 -B -m unittest` for accounting, preview, posting, binding, token and endpoints; require zero failures.

### Task 2: Doctor preview and confirmation

**Files:** `app/src/features/appointments/VisitInvoice.tsx`, `app/src/transport/native/visit-invoice.ts`, corresponding tests and locale catalogues.

- [ ] Add failing tests for default VAT, restricted No VAT, displayed amounts, stale preview after mode change (including in-flight responses), and unchanged unknown-submit reconciliation.
- [ ] Render both choices as permitted and bind the selected mode to preview and confirm. Display fee, discount, net, VAT and payable from the native response. Do not reuse a token after a change.
- [ ] Run `pnpm --filter @clinic/app test src/features/appointments src/transport/native`; require zero failures.

### Task 3: Verify, release, and accept

- [ ] Review pinned native defaults and transaction behavior. Run `pnpm test`, `pnpm typecheck`, `git diff --check`, Clinic's full Python unit suite, and `deploy/visit-invoice-sit` policy tests.
- [ ] Commit, push and verify the exact SHA before rebuilding and redeploying to the disposable SIT. Keep writes off until the independently reviewed synthetic tax template, income and VAT liability accounts, authorization and fixtures are verified; do not delete existing templates or start old queued jobs.
- [ ] Use Playwright MCP for 100.00 without discount (107.00 with VAT), 10.00 fixed discount (96.30 with VAT), 15% discount (90.95 with VAT), and authorized No VAT (85.00 after 15%). Use distinct pristine synthetic customers. Verify unauthorized No VAT, stale preview and duplicate refusal. Independently read back native invoices, links, totals and ledger rows, then turn writes off.
