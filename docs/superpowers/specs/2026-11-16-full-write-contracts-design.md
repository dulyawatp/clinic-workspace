# Full write-contract wave design (scope C) — 2026-11-16

> Status: COMPLETE, builder-checked — awaiting user approval to plan/build.
> Requirements authority: digest of R3 (OpenDesign Requirements v3), R2
> package, OD handoff `clinic-backend-api-contract-spec.md` (SPEC),
> `clinic-actions-permissions.csv` (CSV), gate list (GATES), and **sit-stack
> meta inventory 2026-11-16** (read-only queries against `marley-sit.local`).

## Intent and why

Implement every designed write capability of the Clinic Application Design on
the isolated `marley-sit` stack, and extend session role codes to the seven
designed roles. SIT through the real UI per family with an explicit write
budget. The wave reuses the proven 2026-11-14 write mechanics for every
family; nothing about safety is re-decided.

## Architecture already pinned (not re-decided)

- Each write: prepare → closed-world contract verify → append-only fence
  claim → guarded mutation → closed-world re-read → receipt → one-shot ack
  barrier. One-shot acks; no retry; no auto-unlock; uncertain → surface locks
  until approved operator reconciliation.
- Capabilities stay `read_only` by default; enablement requires site-config
  deployment flags exactly like the five approved writes. Policy code gates
  only narrow; the exact native Frappe DocType permission check runs on every
  write and stays authoritative.
- Two-step Draft→Submit (`docstatus 0→1`), server naming series,
  CSRF+cookie session, `set_posting_time:1` on dated transactions, idempotent
  reconcile-by-request-key, no client arithmetic or direct status/ledger
  writes (SPEC:12–56, 426–433; R3V:141–148).
- No MariaDB schema changes. Target DocTypes must already exist on sit; a
  family whose DocTypes are absent is CUT to its designed not-available state
  — never improvised, never migrated.

## Sit-stack feasibility (meta inventory 2026-11-16)

Installed and submittable: `Patient Encounter`, `Clinical Procedure` (+ child
`Clinical Procedure Item`), `Vital Signs`, `Stock Entry`, `Purchase Order`,
`Purchase Receipt` (→ `Stock Reconciliation`, `Supplier`, `Customer`,
`Sales Taxes and Charges Template` doctypes present), `Sales Invoice`,
`Payment Entry`, plus `Patient Appointment` (status enum **includes
`Cancelled` and `No Show`**), `Clinical Note`, `Drug Prescription`, `Lab
Prescription`, `Lab Test`, `Service Request`, `Batch`, `Warehouse`,
`Healthcare Practitioner`, `Practitioner Schedule`, `Appointment Type`,
`Item`, `Price List`, `Mode of Payment`, `User`.

Absent on sit: **`Clinic Photo`, `Clinic Patient Consent`, `Clinic Photo
Annotation Version`** (they exist only on the separate photo-accept stack)
and **`Clinic External Lab Request`**. Masters are nearly empty (Item 0,
Supplier 0, Price List 0, Warehouse 0, Batch 0; one existing `Customer`).

**Consequence matrix:** photos write family and external-lab send/receive/
review/cancel are CUT (stays demo/not-available). Native lab ordering (Lab
Prescription inside encounter) LANDS. All other families LAND. If installing
the existing `clinic_photos` app onto sit is ever wanted, that is a separate
stack change requiring its own approval (it creates tables).

## Role architecture

- `SUPPORTED_ROLE_CODES` extends DOC/REC/NUR → + PHA/CAS/MGR/ADM (R3:62–70).
  `patient.write` role gates unchanged; `PENDING_WRITE_ROLE_CODES` etc.
  extended per family (roles in the Family matrix below).
- Frappe role → code mapping stays a site role map (`settings.as_role_map()`),
  configured for the new codes; synthetic users are single-role per code to
  keep resolve_role unambiguous.
- Qualifier tokens in CSV (`NUR-LIMITED`, `PHA-LIMITED`, `MGR-APPROVAL`,
  `MGR-AUTHORIZED`, `CAS (permission)`, `ADM-RECONCILE`…) fold to their base
  role as in the parity ruling, with the qualifier expressed as an extra
  condition (permission flag or approver identity), never a role code.
- Native authority: new roles receive **built-in module roles** where they
  exist (`Stock Manager/Stock User`, `Accounts User`, `Purchase User`,
  `Sales User`, `Item Manager`) plus their clinic role code; `Custom DocPerm`
  grants are created ONLY where built-ins are insufficient, itemized per
  grant in the plan and needing separate user approval (same doctrine as the
  earlier Patient/Company grants).

## Builder-check resolutions (2026-11-16, settled + verified)

| # | Question | Resolution |
|---|---|---|
| 1 | Budget unit + setup lane | One fence unit = one SIT write op through the contract; CSV:139–144's sub-steps bundle into one user-create op (create+assign+practitioner-link). Budget recalibrated: `admin_master` 12 (3 warehouses, 1 supplier, 4 items, 1 price list `Standard Selling`, 2 modes of payment, 1 procedure template). No non-contract setup lane needed: chart of accounts complete on sit (`Accounts Receivable/Payable - SITCO`, `Stock Adjustment - SITCO`, `Temporary Opening - SITCO`); company `SIT Synthetic Company`, **currency USD — contract reads company/currency from server records, never hardcodes SPEC's THB demo values**. Masters-empty is handled by the admin lane; note: no `Mode of Payment` rows exist yet (seeding required), `Service Request` count 0 (clean baseline for cascade assertions); procedure templates 0 (seeding required). |
| 2 | Reviewed boundary per ERPNext family | Refined bar (user to ratify): per-family pinned boundary = target controller + sha + MRO + the doc_events/`on_update`/`validate` hooks relevant to that doctype + scheduler `*` jobs touching it + our fence kinds — NOT the wider whole-module sweep. Appointment-style full boundary retained only for the appointment family. |
| 3 | Refusals vs 409 latch | All designed refusals are PRE-fence (policy/role/target/validation). The native-DocPerm-authority proof inside a held fence is NOT exercised this wave (would latch the site 409); recorded explicitly. Any accidental latch follows the stop protocol (no auto unlock). |
| 4 | Cancel/no-show mechanism | **Corrected post-implementation (2026-11-16, source-verified):** installed `cancel_appointment(appointment_id)` is a MODULE-LEVEL function (patient_appointment.py:652), not a controller method; it handles insurance coverage/fee validity/calendar and returns early when no practitioner — it does NOT set status. Cancel therefore pins the module-level call (name + source sha in the family boundary) for its native side-effect handling AND sets terminal status + reason/actor note via controller `save()` (same mechanism as no-show), verifying from returned state. Pre-fence refusal when linked invoice/insurance/Service-Request/mode-of-payment attachments exist (fail-closed, broader than invoice-only — recorded). Cancel/no-show are also published as enableable appointment-write capabilities (bootstrap gate flags), since Task 1's read-only default would have left the wired UI unreachable. No-show unchanged: native `save()` status write to installed enum value `No Show`; reason+actor appended to `notes`. No bare badge-matching writes. |
| 5 | Admin privilege boundary | Contract allowlist: assignable Frappe roles = the clinic role-code mappings + built-in module roles only; `System Manager` and `Healthcare Administrator` may never be assigned through the contract. The acting ADM synthetic user itself carries `System Manager` as its native authority (documented, sit-only). User credentials for the four SIT users are generated into the private gen-secrets store (700/600), welcome email suppressed, never in repo/evidence/fence receipts. |
| 6 | Native cascade exemption | Each family carries an explicit EXPECTED side-effect list used by closed-world revalidation (encounter: appointment→Closed, encounter→Completed, one submitted Service Request per procedure/lab prescription row; procedure: submitted Stock Entry + SLE/Bin/GL). The appointment-specific unsafe-side-effect settings gate applies only where a setting actually affects that family's path; family gates list which settings they check. |
| 7 | Existing users single-role | Verified: sit.doc=Physician, sit.nur=Nursing User, sit.rec=Healthcare Administrator — all single-role. No remediation. |
| 8 | Missing field/child/method | Preflight per family enumerates required fields/child tables/methods; a missing piece cuts that SUB-capability with a recorded fallback (e.g. invoice lands without a discount path if `deductions` child is absent), never the whole family silently. |

## Family contracts (applied resolutions in-line)

Rules binding all families: draft→submit, no direct status writes, ledger
mutations only through installed submittable docs, partial-failure
enumeration, 403/404/409/417/500 envelope verbatim, timeout-after-success
reconciles by request key before retry (CSV/NFR-05).

### (a) Appointment cancel/no-show — LANDS
- `Scheduled/Open/Confirmed → Cancelled` transition (SPEC:118–122) and
  **manual no-show** (CSV:58) — installed enum has both values, satisfying
  CSV:58's "only where the installed workflow supports the state" test.
  Status is set through the appointment's designed transition path, never a
  bare status write (R3:747).
- Roles: cancel `REC, DOC` (CSV:57); no-show `REC, DOC` (CSV:58 names
  REC-authorized; DOC included for the doctor's own surface; narrowing
  recorded). Confirmation required both (conf yes).
- Reason + actor recorded on the appointment: no dedicated field exists
  (inventory: only `notes`), so reason is appended to `notes` with actor+UTC
  timestamp, contract-pinned and verbatim-checked in closed-world re-read.
  Recorded as the field-mapping resolution of the SPEC/R2 "reason field
  VERIFY" gap.
- Fence kinds: `appointment_cancel` (2), `appointment_no_show` (1).

### (b) Encounter draft/complete + amendment — LANDS
- Draft `docstatus 0` holds `symptoms` (Complaint Detail), diagnosis child,
  `drug_prescription`, `lab_test_prescription`, `procedure_prescription`
  (SPEC:132–146). Clinical notes are written as separate `Clinical Note` docs
  (SPEC:157–166); `physical_examination`/`clinical_notes` are never write
  targets (D-05). Vitals via `Vital Signs` (R3:384).
- Complete/submit `docstatus 1` (DOC only, confirmed; CSV:71): closed-world
  re-read EXPECTS the SPEC:172–175 cascade — appointment `Closed`,
  encounter `Completed`, one submitted `Service Request` per
  procedure_prescription and lab_test_prescription row (drug prescriptions do
  NOT auto-cascade; ordered ≠ dispensed, R3:400). Required-before-complete
  field set enforced server-side with per-field error links (CSV:70).
- Amendment = **new `Clinical Note` addendum** referencing the finalized
  encounter (AT-25; never silently overwrite — D-06 resolved to snapshot +
  addendum); capability `write.encounter.amend` added to the contract list
  (gap list). Roles: draft/add-entry DOC (+ NUR with the per-section rule:
  vitals NUR, notes/diagnoses DOC — CSV:69/73); complete/review DOC only
  (CSV:70/71); record_vitals NUR/DOC (CSV:86).
- Fence kinds: `encounter_draft` (2), `encounter_complete` (1),
  `encounter_amend` (1).

### (c) Medication order — LANDS (inside encounter paths)
- No separate write endpoint: prescription rows are part of encounter draft
  (capability `write.medication.order` binds to the draft/complete kinds);
  `drug_code`-vs-`medication` VERIFY resolved to the installed
  `Drug Prescription` field set (inventory-verified at implementation).
  Server attests required item/dosage; no inferred dosing (R3:400).
- No separate SIT op or budget beyond the encounter kinds.

### (d) Procedure complete — LANDS
- `Clinical Procedure` per SPEC:185–220; complete = `run_doc_method
  complete_procedure` which auto-creates and submits the Material-Issue
  `Stock Entry`; exactly-once enforced server-side AND by a UI lock;
  cancelled procedure posts no stock (CSV:76). `price_list` requirement is
  real (SPEC:197) — SIT seeds `Standard Selling` via the admin-master lane.
- Roles: complete DOC only (CSV:75); stock preview DOC/NUR/PHA (CSV:74);
  closed_cancel DOC (CSV:76); issue path from PHA-06 stays stock-issue
  semantics (CSV:109) — consume fence kind is the procedure completion.
- Fence kind: `procedure_complete` (1). Side effects: submitted Stock Entry,
  SLE rows, Bin/GL — EXPECTED in revalidation for this family.

### (e) Lab order / review — PARTIAL (native only)
- Native ordering LANDS: `Lab Prescription` rows inside encounter draft +
  submitted `Service Request` on complete (SPEC:145/174). Roles per CSV:78
  (`DOC`, confirmed).
- External request lifecycle (`Ordered/Sent/Received/Reviewed/Cancelled`,
  CSV:79–82) is CUT — `Clinic External Lab Request` is absent from sit and
  no migration is allowed. The designed external-lab UI keeps its explicit
  not-available state. Capability keeps `write.lab.review` read_only.

### (f) Photos — CUT on sit
- `Clinic Photo`/`Clinic Patient Consent`/annotation versions are absent on
  sit; family stays demo/not-available. The repo R3:15–20 amendment (approved
  2026-11-06) continues to govern READS if/when a stack adds these doctypes;
  CSV write rows remain the designed semantics. No contract work this wave.

### (g) Stock receive/transfer/adjust — LANDS
- Receive: batch master creation where `create_new_batch=0` precedes the
  submitted `Purchase Receipt` (SPEC:280–283); over-receipt blocked;
  `PHA` confirmed (CSV:99/106).
- Transfer: `Stock Entry` type `Material Transfer`, `set_posting_time:1`,
  per-row `s_warehouse`/`t_warehouse`, batch if `has_batch_no` (SPEC:234–259);
  insufficient/missing target blocks; PHA confirmed (CSV:108/112).
- Adjust: **restricted `Stock Reconciliation` only** (R3:460/720; CSV:110 —
  SPEC's silence is overridden by two-of-three sources): `ADM` confirmed,
  reason + approval trail recorded.
- Procedures consume stock via (d)'s mechanism, not a folded transfer.
- Fence kinds: `stock_receive` (1), `stock_transfer` (1),
  `stock_adjust` (1).

### (h) Purchase order draft/submit — LANDS
- Draft PO (`PHA, MGR`, no conf; CSV:113) ≥1 itemized line, real `Supplier`;
  submit (`PHA, MGR`, confirmed; CSV:114) → `To Receive and Bill`.
  Reorder opens a draft, never auto-submits (R3:718). Receive = submitted
  `Purchase Receipt` with PO reference, partial receipts reduce balance,
  over-receipt blocked (CSV:115). Close PO (CSV:117) blocked while a receipt
  is in progress.
- Fence kinds: `purchase_order_draft` (1), `purchase_order_submit` (1),
  `purchase_receipt` (1).

### (i) Invoice submit — LANDS
- Draft→submit `Sales Invoice` per SPEC:299–321 with `update_stock=0`
  (fortune rule; double-deduction guard), explicit `due_date`, customer must
  exist client-forbidden-to-create (CSV:121), no duplicate linked charges
  (CSV:122).
- Tax policy: sit has the doctype but no template and no policy record —
  nothing is invented (D-11 stays open): taxes remain empty and the UI
  presents subtotal arithmetic verbatim from the server. Discounts ride the
  child `deductions` line with a permission-gated audited path (CSV:123,
  `CAS + permission condition`); no fixed rate anywhere.
- Void/amend is a separate restricted lifecycle (MGR; CSV:126) — implemented
  as capability `write.invoice.void_amend` only if the installed state machine
  supports it cleanly; otherwise CUT and recorded. Decided at implementation
  behind a preflight check.
- Fence kind: `invoice_submit` (1).

### (j) Payment receive — LANDS; refund CUT
- `Payment Entry` type `Receive` with `references[]` allocation that MUST sum
  to paid amount (CSV:128); modes Cash/Credit Card only (R3:472 overrides
  SPEC's Bank Transfer); over-allocation/repeat/draft-invoice blocked
  (CSV:127; SPEC:32–40); partial payments where installed; no PAN/CVV ever.
- Refund: CUT — refund return lifecycle is undecided in requirements (D-13,
  REFUND-RETURN DECISION_REQUIRED) and has no SPEC section; `write.payment.
  refund` stays read_only and records the open decision.
- Fence kinds: `payment_receive` (1). Refund approver semantics recorded as
  the carried-open D-13.

### (k) Admin users + masters — LANDS (first)
- Users: create/assign-role/link-practitioner/access-test/disable per
  CSV:139–144 (ADM confirmed rows); last-administrator disable blocked; no
  implicit escalation; disabling checks links.
- Masters (CSV:145–152 + FF-admin): practitioner/schedule/service-unit/
  warehouse mapping, appointment types, procedure/lab template masters where
  installed, Item/UOM/price-list/batch settings, mode-of-payment→account
  mapping, language default. `ADM-03`'s bundled row splits into per-DocType
  payload variants; "lab template" master CUT (DocType unnamed anywhere).
- The SIT seeding of warehouse/items/supplier/price-list/etc. RUNS THROUGH
  THIS CONTRACT (it is the designed master-editing surface) — masters-empty
  is therefore a design input, not a workaround.
- Fence kinds: `admin_user` (4 — the PHA/CAS/MGR/ADM synthetic users are
  created through this contract during SIT), `admin_master` (12: 3
  warehouses, 1 supplier, 4 items, 1 price list `Standard Selling`, 2 modes
  of payment, 1 procedure template).

## Capability list changes

Added IDs (contract + policy + client contract files updated in lockstep):
`appointment.no_show`, `encounter.amend`, `invoice.void_amend`
(implementation-preflight-gated), `stock.consume`. Explicitly NOT added:
`lab.send/receive/cancel` (external cut), `payment.allocate` (part of
receive), `po.receive` (receipt submit covers). Final IDs freeze in the plan.

## Write budget proposal (isolated sit, this wave)

appointment_cancel 2 · appointment_no_show 1 · encounter_draft 2 ·
encounter_complete 1 · encounter_amend 1 · procedure_complete 1 ·
lab_order 1 (via encounter draft) · stock_receive 1 · stock_transfer 1 ·
stock_adjust 1 · purchase_order_draft 1 · purchase_order_submit 1 ·
purchase_receipt 1 · invoice_submit 1 · payment_receive 1 · admin_user 4 ·
admin_master 12 (3 warehouses · 1 supplier · 4 items · 1 price list · 2 modes
of payment · 1 procedure template). Every family also exercises one designed
pre-fence refusal (no budget: refused writes never claim the fence). Budget
exhaustion = no further writes without a new approval; scheduler paused for
write windows exactly as before.

## Non-goals / anti-goals

- No delete operations; no permission-model rewrite; no migrations/new
  tables (photos + external-lab families cut accordingly).
- No invented tax, VAT, discount rates, refund lifecycle, or FEFO/wastage
  policy (D-08/D-09/D-10/D-11/D-13 stay open and stay unwired).
- No production-readiness claims; isolation and evidence doctrine as before.

## What is left to the builder

- Per-family endpoint/gateway/contract/fence kinds following the appointment
  pattern; unit tests RED→GREEN per family; UI wiring of the currently
  disabled controls (cancel/no-show/clinical/ops/finance/admin) with one-shot
  acks and locks; i18n; capability-contract sync test; preflight + verifier
  extension for the new approved-write set; role map + built-in role
  assignments; deploy lane; per-family UI SIT lanes with evidence; the four
  synthetic users created through the admin contract and browser-verified
  (completes the postponed follow-up item 2).
