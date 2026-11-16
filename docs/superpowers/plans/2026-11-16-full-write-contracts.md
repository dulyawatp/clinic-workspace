# Full write-contracts implementation plan (scope C)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement every designed write capability on `marley-sit` — appointment cancel/no-show, encounter draft/complete/amend (medication + native lab order), procedure complete with stock consumption, stock receive/transfer/adjust, PO draft/submit + receipt, invoice submit, payment receive, admin users/masters — plus PHA/CAS/MGR/ADM session role codes, then verify each family through the real UI under an explicit fence budget.

**Architecture:** Every family reuses the proven appointment-write mechanics: prepare → closed-world contract verification → append-only fence claim → guarded mutation via pinned native methods (`docstatus` submit flows, `cancel_appointment`, `complete_procedure`) → closed-world re-read against an EXPECTED side-effect list → receipt → one-shot ack barrier. One shared budget/registry; per-family reviewed boundaries; capabilities stay `read_only` until site-config deployment gates open them.

**Tech stack:** Frappe/ERPNext v16 apps Python (`frappe-apps/clinic_ui`), React 18 + TypeScript SPA (`app/`), vitest + frappe unittest, Playwright-free (browser lanes via agent-browser), compose deploy tooling.

**Spec:** `docs/superpowers/specs/2026-11-16-full-write-contracts-design.md` (authority incl. sit inventory and builder-check resolutions; this plan argues from it).

## Global Constraints

- Write budget (user-approved 2026-11-16, FINAL): appointment_cancel 2 · appointment_no_show 1 · encounter_draft 2 · encounter_complete 1 · encounter_amend 1 · procedure_complete 1 · lab_order 1 · stock_receive 1 · stock_transfer 1 · stock_adjust 1 · purchase_order_draft 1 · purchase_order_submit 1 · purchase_receipt 1 · invoice_submit 1 · payment_receive 1 · admin_user 4 · admin_master 12. Exhaustion = no further writes without new approval.
- One-shot acks; no retry; no auto-unlock; uncertain writes fail closed (site lock until approved operator reconciliation).
- All designed refusals PRE-fence. No native-403-inside-fence test anywhere in this wave.
- No MariaDB schema changes; DocTypes exist on sit (design §Sit-stack feasibility). Photos + external-lab lifecycle CUT; refund CUT; lab review CUT; no invented tax/VAT/THB/7%-VAT (currency reads from company record).
- Family expected-side-effect lists are closed-world revalidation targets; the appointment-specific unsafe-settings gate applies only to families whose path those settings affect (listed per family).
- Admin contract allowlist: assignable roles = clinic role codes + built-in module roles; never System Manager/Healthcare Administrator. New user credentials → private gen-secrets store only, welcome email suppressed.
- Thai/EN i18n parity; 44px/focus; Sample/Demo labeling unchanged on demo surfaces; verified DOC/REC/NUR flows untouched.
- No pushes to any remote; commits stay on worktree branch `local-native-sit`.

## Review Focus

1. **Post-fence latch accident** — any refusal that must be free instead latches the site → test per family asserts its designed refusal returns before `write_fence.fence` is entered (owning task's unit tests + endpoint tests).
2. **Side-effect drift** — a write creates more/fewer linked docs than the family's EXPECTED list (e.g. Service Request count, Stock Entry, GL) → closed-world re-read tests per family assert the exact list (owning task).
3. **Contract/policy/capability drift** — `contracts/capabilities.json`, `core/capabilities.py`, `core/policy.py`, client session DTO disagree on new IDs/role codes → Task 1 introduces a lockstep sync test; every family task extends it.
4. **Silent timestamp/posting overwrite** — dated submits without `set_posting_time` get server-now silently → per-family contract pins the field and its test asserts the value round-trips (owning task).
5. **Master-first drift** — a family SIT runs before its masters exist → Task 12's SIT script has a hard preflight of seeded masters and refuses to proceed (no improvisation).

---

### Task 1: Foundation — capability list, policy roles, budgets registry

**Files:**
- Modify: `contracts/capabilities.json` (add: `write.appointment.no_show`, `write.encounter.amend`, `write.invoice.void_amend`, `write.stock.consume`; final ID freeze)
- Modify: `frappe-apps/clinic_ui/clinic_ui/core/capabilities.py` (names + `ENABLEABLE_NAMES` + validate), `core/policy.py` (`SUPPORTED_ROLE_CODES` + per-family role-code frozensets)
- Create: `frappe-apps/clinic_ui/clinic_ui/core/write_budgets.py` (kind→{"budget": n, "family": str} for all 17 kinds above)
- Modify: `core/patient_write.py`, `core/appointment_write.py` to read budgets from the registry (names unchanged)
- Modify: `app/src/transport/native/session.ts` role union + capability union (client contract)
- Test: `frappe-apps/clinic_ui/tests/unit/test_write_budgets.py`, extend `test_capabilities.py`, `test_policy.py`, app `src/transport/native/native.test.ts` contract-sync test

**Interfaces:**
- Produces: `write_budgets.budget_for(kind) -> int`, `write_budgets.ALL_KINDS -> tuple[str, ...]`; policy `APPOINTMENT_CANCEL_ROLE_CODES={"DOC","REC"}`, `APPOINTMENT_NOSHOW_ROLE_CODES={"DOC","REC"}`, `ENCOUNTER_ROLE_CODES={"DOC","NUR"}`, `ENCOUNTER_COMPLETE_ROLE_CODES={"DOC"}`, `ENCOUNTER_AMEND_ROLE_CODES={"DOC"}`, `PROCEDURE_ROLE_CODES={"DOC"}`, `STOCK_ROLE_CODES={"PHA"}`, `ADJUST_ROLE_CODES={"ADM"}`, `PO_ROLE_CODES={"PHA","MGR"}`, `INVOICE_ROLE_CODES={"CAS"}`, `INVOICE_VOID_ROLE_CODES={"MGR"}`, `PAYMENT_ROLE_CODES={"CAS"}`, `ADMIN_ROLE_CODES={"ADM"}` (`frozenset[str]` each).

- [ ] **Step 1:** Write failing tests: budgets registry contains exactly the 17 kinds with the approved numbers; capability lists in the four surfaces are step-synced (one test iterates contracts/capabilities.json + capabilities.py + session.ts unions).
- [ ] **Step 2:** Run `cd frappe-apps/clinic_ui && python3 -m unittest tests.unit.test_write_budgets tests.unit.test_capabilities tests.unit.test_policy` → FAIL (missing module/IDs).
- [ ] **Step 3:** Implement registry, policy frozensets, capability-list additions (all four files).
- [ ] **Step 4:** Same command → PASS; full backend suite + app typecheck green.

### Task 2: Appointment cancel + no-show (backend + UI wiring)

**Files:**
- Create: `clinic_ui/endpoints/appointments_cancel_write.py` (POST handlers `cancel`, `no_show`)
- Modify: `runtime/frappe_gateway.py` (cancel drives pinned `PatientAppointment.cancel_appointment` with source-sha pin + post-condition read: status `Cancelled`, calendar event cancelled when present; refusal when linked invoice handling fails; no-show = `save()` status write with notes append reason+actor, contract pins notes preimage+postimage)
- Modify: `core/appointment_contract.py` + `core/appointment_write.py` (kinds, budgets)
- hooks/urls: register endpoints as designed routes
- App: `app/src/features/appointments/LiveAppointmentPage.tsx` — wire the disabled controls to the real flow with one-shot ack + existing lock (`useAppointmentWriteLock`), confirmation dialog (conf yes rows), capability `write.appointment.cancel`/`no_show` gating
- Test: `tests/unit/test_endpoints_appointments_cancel.py`, `test_appointment_contract.py` additions; app `live-appointment-write.test.tsx` rewires the not-available assertions into wired-flow tests + `reception-parity.test.tsx` keeps refusal coverage

**Interfaces:**
- Consumes: Task 1 budgets/roles; existing fence claim/ack helpers used by `endpoints/appointments_write.py`.
- Produces: POST `clinic_ui.api.appointments_cancel_write.cancel|no_show` — request keys `patient`, `appointment_id`, `operation_key`, `reason` (cancel: required; no-show: required); ack endpoint unchanged.
- [ ] Cancel: fence kind `appointment_cancel`, budget 2; no-show kind `appointment_no_show`, budget 1.
- [ ] Role gates: policy frozensets from Task 1 (PRE-fence).
- [ ] Unit: red→green per step; assert refusal (wrong role/target status `Closed`/`Cancelled`) exits before fence claim; assert cancel post-conditions + expected-side-effect list (fee-validity/cancel notes from pinned method are ATTESTED, not deep-verified: contract reads returned state, records receipt fields).
- [ ] UI: disabled-note assertions replaced by wired confirm→submit→ack tests; no-retry lock test; i18n keys added th/en.

### Task 3: Encounter draft / complete / amend (+ medication, native lab order)

**Files:**
- Create: `clinic_ui/endpoints/encounters_write.py` (`save_draft`, `complete`, `amend`), `clinic_ui/core/encounter_contract.py`, `core/encounter_write.py`
- Modify: `runtime/frappe_gateway.py` (draft: `Patient Encounter` insert/update docstatus 0 with child rows — `symptoms`/`Complaint Detail`, `Patient Encounter Diagnosis`, `Drug Prescription`, `Lab Prescription` + `Clinical Note` separate docs + `Vital Signs`; complete: submit + EXPECTED cascade (appointment→Closed, encounter→Completed, exactly one submitted `Service Request` per procedure/lab prescription row); amend: new `Clinical Note` addendum referencing the finalized encounter, original untouched)
- Modify: `core/encounter_contract.py` gates: complete requires the required-before-complete field set (per-field problem text); settings gate: `Healthcare Settings` keys that actually affect the encounter path enumerated
- App: `app/src/features/clinical/*` — wire the demo write loop to real transport where capability enabled: `AuthoredVisitWorkspace` gains a native mode path (registry `ENC-` visits read + write where permitted), keep demo state for authored fixtures; `NUR` vitals wiring (`NUR-02`)
- Test: `tests/unit/test_endpoints_encounters_write.py`, `test_encounter_contract.py`; app `authored-visit.test.tsx` native-mode tests zero-writes-for-excluded-roles + wired flow for DOC

**Interfaces:**
- Produces: POST `save_draft {patient, operation_key, payload…}`, `complete {encounter, operation_key}`, `amend {encounter, operation_key, addendum}`; kinds `encounter_draft` (2), `encounter_complete` (1), `encounter_amend` (1).
- Roles: draft/notes NUR per section rule + DOC any section; complete/amend DOC (PRE-fence policy).
- [ ] Contract: medication rows required `drug_code` per INSTALLED field set (preflight `frappe.db.get_meta` in test fixture); lab order rides draft rows (no separate endpoint); amendment never rewrites the finalized note (test asserts addendum-created + original `modified` untouched).
- [ ] Side-effect gate: complete REFUSES when appointment linkage missing or appointment not `Open/Checked In` for its cascade (closed-world, pre-fence target check).

### Task 4: Procedure complete (consumable consumption)

**Files:**
- Create: `clinic_ui/endpoints/procedures_write.py` (`complete_procedure` wrapper), `core/procedure_contract.py`
- Modify: `runtime/frappe_gateway.py` (drive installed `complete_procedure` doc method with source sha pin; EXPECTED side effects: submitted Material-Issue `Stock Entry` name returned, status/`actual_end_datetime` completed; exactly-once: completed procedure refused pre-fence)
- App: `app/src/features/clinical/ProcedureDetailPage.tsx` — wire confirm-complete (conf yes, DOC, capability `write.stock.consume`/`procedure.complete`), stock preview stays read-only
- Test: `tests/unit/test_endpoints_procedures_write.py`, `test_procedure_contract.py` (needs a seeded `Standard Selling` + consumable item in fixtures; unit tests use mocks for ERPNext internals per existing gateway-adapter pattern)

**Interfaces:**
- Produces: POST `complete_procedure {procedure, operation_key}`; kind `procedure_complete` (1). Roles: DOC (PRE-fence). Cancelled procedure refused pre-fence.
- [ ] Contract asserts `price_list` present (else 417 with SPEC's message), batch validity per item rows in-contract before claim.

### Task 5: Admin users + masters

**Files:**
- Create: `clinic_ui/endpoints/admin_write.py` (`create_user`, `edit_master`), `core/admin_contract.py`
- Modify: `runtime/frappe_gateway.py` (create_user: User + Roles + optional practitioner link in ONE mutation, roles validated against the allowlist {clinic codes' Frappe roles + built-in module roles}; edit_master: per-DocType payload variants — warehouse, supplier, item(+price), price list, mode of payment(+account), procedure template)
- App: `app/src/features/admin/**` — wire users page + masters editors where capability enabled
- Test: `tests/unit/test_endpoints_admin_write.py`, `test_admin_contract.py` (allowlist refusal for System Manager assign attempt is the family's designed pre-fence refusal)

**Interfaces:**
- Produces: POST `create_user {operation_key, email?, full_name, clinic_role_code, practitioner?}` (email optional-if-username-provided per installed User semantics), `edit_master {operation_key, master_type, payload}`; kinds `admin_user` (4), `admin_master` (12). Roles: ADM (PRE-fence).
- [x] Duplicate/email failures refuse pre-fence; the privilege boundary refuses System Manager / Healthcare Administrator assignment pre-fence. "Disable last administrator" is designed (CSV:143) but disable is OUT of this wave's op set, so it remains **unexercised-designed**. Credentials: gateway sets `new_password` from a caller-supplied secret (no generation server-side); SIT passes the private-store value; welcome-email suppression pin (`send_welcome_email=0` semantics as installed).

### Task 6: Stock receive / transfer / adjust

**Files:**
- Create: `clinic_ui/endpoints/stock_write.py` (`receive`, `transfer`, `adjust`), `core/stock_contract.py`
- Modify: gateway (receive: Batch create-where-required + submitted `Purchase Receipt` (`set_posting_time:1`, PO reference optional); transfer: `Stock Entry` Material Transfer with per-row warehouses + batch; adjust: `Stock Reconciliation` with reason — ADM only)
- App: `app/src/features/pharmacy/**` wire Receiving/Transfers/Reconcile submissions (PHA / ADM) as designed
- Test: `tests/unit/test_endpoints_stock_write.py`, `test_stock_contract.py` (insufficient stock/missing batch/expired batch/over-receipt → pre-fence 417s with named lines)

**Interfaces:**
- Produces: kinds `stock_receive`(1), `stock_transfer`(1), `stock_adjust`(1). Roles: receive/transfer PHA + transfer per CSV `PHA`; adjust ADM.
- [x] EXPECTED side effects (receipts in revalidation): SLE rows counted per submitted doc; Bin deltas asserted; negative-result → pre-fence refusal (ledger never directly written).

### Task 7: Purchasing (PO draft/submit + receipt)

**Files:**
- Create: `clinic_ui/endpoints/purchase_write.py` (`save_po_draft`, `submit_po`, `receive`), `core/purchase_contract.py`
- Modify: gateway (PO draft docstatus 0 ≥1 itemized line; submit → docstatus 1 `To Receive and Bill`; receipt = Task 6's receive with PO reference; close blocked while receipt in progress — pre-fence)
- App: `app/src/features/pharmacy/purchasing` — wiring per design
- Test: `tests/unit/test_endpoints_purchase_write.py`, `test_purchase_contract.py`

**Interfaces:**
- Produces: kinds `purchase_order_draft`(1), `purchase_order_submit`(1), `purchase_receipt`(1, shared with Task 6 budget line — one SIT op covers both families' receipt test). Roles: PO draft/submit PHA+MGR.

### Task 8: Finance (invoice submit + payment receive)

**Files:**
- Create: `clinic_ui/endpoints/finance_write.py` (`save_invoice_draft`, `submit_invoice`, `receive_payment`), `core/finance_contract.py`
- Modify: gateway (invoice: customer must exist, `update_stock=0`, explicit `due_date`, currency/company from server, `selling_price_list` required; discount rides `deductions` child WITH recorded fallback cut if child absent — preflight in test; payment: `Payment Entry` Receive with `references[]` summing to paid amount, modes Cash/Card only, over-allocation/repeat/draft-invoice refused pre-fence)
- App: `app/src/features/finance/**` — wire invoice create/preview/submit, discount (permission-flagged), payment receive/allocation UI per design; void/amend control stays not-available unless Task 8 preflight proves the installed lifecycle (decision recorded either way)
- Test: `tests/unit/test_endpoints_finance_write.py`, `test_finance_contract.py`

**Interfaces:**
- Produces: kinds `invoice_submit`(1), `payment_receive`(1). Roles: CAS; void MGR (gated).
- [x] Client arithmetic display ≠ truth: contract re-reads totals server-side; tests assert posted total vs submitted payload.
  Implementation decisions (recorded): void/amend **cut** (no approved budget line and no reviewed
  cancel/amend lifecycle path — `write.invoice.void_amend` stays read_only, control not-available);
  refund **cut** (D-13); discount rides `deductions` only behind the `clinic_ui_invoice_discount_enabled`
  site flag + installed-child preflight (absent child cuts only the discount path); duplicate linked
  charges enforced as the duplicate-item guard because the installed `Sales Invoice Item` has no
  clinic charge-reference field.

### Task 9: Capability/verifier/deploy extension

**Files:**
- Modify: `deploy/local-sit/bin/verify.py` (expected enabled-write set becomes site-config dependent: assert the five current + any opened family write set from config, plus new capability IDs present in bootstrap), preflight script (family-master preflight checks)
- Modify: deploy scripts for role-map config keys (new codes)
- Test: verifier self-check; run against sit AFTER Task 10 deploy
- [ ] Verifier keeps asserting `write.appointment.cancel`… semantics per approved design; new kinds' READ_ONLY state asserted until their gates open.
- [ ] Preflight ruling (2026-11-16): `stock_receive` and `purchase_receipt` are distinct fence kinds; SIT C performs TWO receipt ops — one standalone receipt (kind `stock_receive`) and one PO-referenced receipt (kind `purchase_receipt`) — each consuming its own budget line.

### Task 10: Redeploy (approved pattern, frozen source)

**Files:** none (build/recreate only)
- [ ] `build:native` + `build backend` + force-recreate backend/websocket/queues + frontend in one block; bundle identity host==image==served; `verify.py` exit 0; scheduler paused for subsequent SIT write window (approved pause pattern); fence still 24/0 + budgets unchanged at this point.

### Task 11: SIT A — admin seeding + identities (writes through the contract)

**Files:** private SIT scripts under `~/.local/state/marley-sit/` (not repo); evidence dir
- [ ] Preflight: capabilities read, budget registry read-back, master-empty inventory noted. **Ruling (task-4 concern 3): define at this preflight how the seeded submitted Clinical Procedure comes into existence for SIT B — preferred: the installed Service Request→Clinical Procedure flow from task-12's encounter-complete cascade; if that flow does not auto-create the procedure, STOP and request user approval for an additional creation path/budget amendment before proceeding (never improvise a write).** Open ONLY `write.admin.user`, `write.admin.master` gates (site config; approved pause active).
- [ ] Execute 12 admin_master ops + 4 admin_user ops (PHA/CAS/MGR/ADM synthetic users) through the UI as the existing ADM-capable actor — one submit each, acks, receipts; credentials → private gen-secrets store; HAR per session.
- [ ] Independent verification: master/user rows + budgets consumed (12+4), fence receipts COMMITTED + barriers, no side effects beyond family list; then close the admin gates.

### Task 12: SIT B — clinical families via UI

- [ ] Open encounter/procedure/lab gates. As DOC: save encounter draft with vitals note + medication row (encounter_draft 2 — two drafts), complete the encounter (cascade asserted: appointment Closed, Service Requests count = prescription rows, encounter Completed), amend via addendum; procedure complete on a seeded procedure (Stock Entry returned); lab prescription rides a second draft. Budgets consumed exactly.
- [ ] NUR vitals recording ride-through verified. Refusals: NUR complete-attempt → pre-fence 403-equivalent, zero fence claim.

### Task 13: SIT C — stock/purchasing/finance via UI

- [ ] Open stock/po/invoice/payment gates. As PHA: receive (batch created where required), transfer between the two seeded warehouses, PO draft→submit→receipt; as ADM: stock reconciliation (reason recorded). As CAS: invoice draft→submit (server-total re-read; no tax rows; discount via permission path only if implemented) then payment receive with allocation summing to paid.
- [ ] Refusal per family pre-fence; budgets consumed exactly; side-effect assertions (SLE/GL counted, invoice Unpaid→Paid).

### Task 14: SIT D — cancel/no-show via UI + close-out

- [ ] Open appointment cancel/no-show gates. Op order and targets (ruling 2026-11-16): no-show appointment A (Scheduled; manual authorized action per CSV:58, reason recorded), then cancel appointment B (Checked In → Cancelled per SPEC:118–122 "any non-terminal"). **Actors (task-2 ruling): both ops run as REC, the actor whose committed bookings A and B are — the contract resolves targets to the actor's own committed booking (static allowlist; other clinicians' bookings never writable).** Budgets are caps, not quotas: `appointment_no_show` 1/1 and `appointment_cancel` 1/2 consumed; the second cancel slot stays unconsumed (no target remains; never re-book to create one). Post-conditions per Task 2 contract asserted for each op (terminal status set via controller save(); native module-level cancel side effects attested from returned state; note the no-calendar/department caveat is defence-in-depth).
- [ ] Browser matrix for the four new identities (surfaces reachable + denial panels + Sample labels + 390/834); fence closed at 24 + 4×(ops actually executed) events / 0 unresolved (4 events per op: claim, barrier, receipt, ack barrier); budgets consumed exactly as executed; zero side-effect drift; scheduler re-enabled (approved).
- [ ] Report: new §17 in `docs/assessments/2026-11-14-local-native-sit-report.md` (variant table + evidence pointers, no secrets); evidence manifest; SDD ledger closure.

## Sequencing & parallelism

- Backend tasks 2→5 sequential (share gateway/fence surfaces); 6→8 after 5 (admin masters needed by their unit fixtures only as data — mocks independent, so 6–8 may go parallel after 5).
- Task 9 after all backend; Task 10 after 9; 11 after 10; 12–14 strict sequence (shared write windows + fence).
- UI wiring (part of tasks 2–8) and their tests stay per-task.
