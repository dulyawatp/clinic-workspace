# Cashier full settlement and native receipts

Extend the existing Clinic cashier payment action to settle exactly one submitted invoice in full with Cash or Credit Card. ERPNext still creates and posts the Payment Entry, owns its ledger, and renders the receipt. This replaces the approved frontend-only blocker in the [cashier review design](2026-11-20-native-cashier-payment-receipt-design.md) for this action only.

**Approach.** Enforce full payment on the existing endpoint rather than add a second writer or rely on an editable amount or browser recheck. Preserve the authorization, deployment gates, durable budget, fence, and acknowledgment. The guarantee is for the Clinic payment at its commit. An independent ERPNext Desk operation that validated earlier is outside this endpoint and cannot be forced to revalidate by it. Do not claim a site-wide guarantee or change Desk.

**Posting.** Verify pinned native payment writers reach the same invoice update in their transaction. Hold the existing Clinic lease and a fresh native lock on the invoice through commit. Recheck authority, one allocation, exact current ledger and invoice balance, currency/account compatibility and native precision before dispatch. Reject stale, partial, over, split, and conversion requests. Verify the posted Payment Entry, invoice-linked allocation, scoped accounting results and exact zero balance before committing. Roll back mismatches, preserve no-retry behavior, and do not expose a receipt after uncertainty.

**Clinic UI.** Keep THB as the default filter, expose no editable amount, accept only Cash or Credit Card, and require the approved terminal reference for card. Do not enable the action against deployments that have not verified and explicitly advertised full-only support. Do not add patient identity access.

**Receipt.** Read the confirmed, submitted, invoice-linked Payment Entry under the cashier session and offer the site's verified native receipt print format. Do not infer receipt fields from the submitted form or override ERPNext permissions.

**Acceptance.** Use independently isolated synthetic Cash and Card deployments because the existing budget allows one successful payment per deployment. Leave shared SIT, account permissions, doctor billing, and payment budgets unchanged. Verify persisted invoice, Payment Entry, ledger and print evidence; prove the supported concurrency boundary before enabling the action.
