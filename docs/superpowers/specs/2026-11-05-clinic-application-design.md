# Demo Clinic Application — Design & Implementation Specification

**Date:** 2026-11-05
**Status:** SPECIFICATION ONLY — no application code, no backend mutations, no contract verification performed.
**Target implementation:** React + TypeScript SPA with a same-origin backend proxy, against the existing Frappe / Marley Health / ERPNext installation.
**Design source:** OpenDesign project *Clinic Application Design* (`b741c393-f46e-46bb-8e71-c0652dc75d7c`), static HTML prototype + handoff inventories.
**Requirements source:** `OpenDesign Requirements v3/` (Functional Requirements v3.0, Screen Manifest v3, Change Log, Backend Contract Verification v3, Starter Prompt v3).
**Validation owner:** Orchestrator (this document is a design/engineering artefact; it is not signed-off acceptance evidence).

---

## 0. Reading rules and claim boundaries

**Approved phase amendment (2026-11-06):** The patient/photo-draft access amendment in `OpenDesign Requirements v3/Clinic_Functional_Requirements_v3.md` and `docs/superpowers/specs/2026-11-06-frappe-private-photo-drafts-design.md` supersedes conflicting read-scope text below for this feature. All authenticated clinicians may view all patients and saved drafts without assignment restrictions; only draft authors may edit. Photo/draft surfaces are DOC/NUR; the REC photo-read allowance in PAT-04 is deferred in this phase. Assigned worklists remain workflow filters; unrelated action permissions are unchanged. Native Frappe controls are preferred, with separate private original/annotation files. No backend capability or live write is activated by this amendment.

This document has one job: define what will be built, in what order, and what must be true before any screen is allowed to change server state. It deliberately does **not** assert that anything currently works.

| Claim type | Rule used in this document |
|---|---|
| Requirements | Quoted or paraphrased from `Clinic_Functional_Requirements_v3.md` / `Clinic_Screen_Manifest_v3.json`. Authoritative for scope. |
| Design source | Described from the OpenDesign static prototype and its handoff files. Describes **prototype behaviour only**. |
| Backend | Never asserted. Every backend statement is either `VERIFY` (unknown until inspected) or `EXT` (extension required). No endpoint, field name, status value or version is treated as proven. |
| Acceptance | Only defined as a **test to run**. No row in this document claims a passing result. |

### 0.1 What is explicitly NOT verified

1. **The design source API scaffold is not verified and must not be ported.**
   `assets/js/frappe-api.js` in the OpenDesign project is a browser-side Frappe transport scaffold. Nothing in the project history shows it was executed against an installed site or reviewed against the contract checklist. It is unverified code, not a contract.

   It also contains at least one pattern the requirements explicitly prohibit:

   - `checkInAppointment()` performs a direct `PUT` of `status: "Checked In"` on `Patient Appointment`.
     `Clinic_Backend_Contract_Verification_v3.md` (BC3-01) and FR v3 §8 NFR-06 forbid exactly this ("Do not implement with an unchecked direct `PUT status`").
   - `completeEncounterWorkflow()` creates `Patient Encounter` + `Clinical Note` and submits the encounter in one client-orchestrated chain, without any verified per-section persistence or duplicate-submission semantics (FR v3 F05 requires per-section persistence reporting and idempotent retry).
   - `completeCashierSettlement()` submits a `Sales Invoice` and a `Payment Entry` from the browser, computing allocation values client-side. Client-calculated financial values are explicitly untrusted (NFR-08).
   - CSRF token is read from a global (`window.csrf_token`); session handling assumes cookie `credentials: "include"` with no same-origin policy analysis.

   **Decision: the scaffold is reference-only. No line of it is imported into the React application.** New transport code is written against verified contracts, not against this file.

2. **The backend integration audit's version claims are unverified.**
   `handoff/clinic-backend-integration-audit.md` and `handoff/clinic-backend-api-contract-spec.md` state specific versions (Frappe v16.53.0, ERPNext v16.52.1, Marley Health v16.6.1) and label themselves "Canonical Technical Integration Specification". The same project's `handoff/README.md` and `handoff/clinic-backend-gates.md` state the opposite: no site, schema, endpoint, role rule or workflow was inspected, and `backend_status` remains `unverified_installed_site`.

   **Resolution used here:** the README/gates statement wins. Version numbers in the design source are treated as unsourced until an authorised operator records them from the installed site. They must not appear in UI copy, documentation, or code as fact.

3. **The prototype has no backend at all.**
   The integration audit records that the prototype's data layer reads/writes `window.localStorage` and makes zero network calls. `assets/js/frappe-api.js` is not referenced by any HTML entrypoint or script (checked: no `frappe-api` reference exists in the project), so its `fetch` code never runs. Therefore "design parity" and "backend parity" are separate milestones in this plan and must never be conflated.

4. **Existing site `10.0.100.10:8080` is an HTTP evaluation environment only.**
   Consequences that shape the design:
   - Plain HTTP is **not a secure context**. Browser camera capture (`getUserMedia`) is unavailable except on `localhost`. Photo capture on the evaluation site must fall back to file upload, and this must be visible in the UI, not a silent failure.
   - `Secure` cookies cannot be used; `HttpOnly` + `SameSite` still apply. This is acceptable for evaluation, not for production.
   - No HSTS, no transport confidentiality. Patient data must not be treated as protected in transit on this host.
   - Production deployment requires HTTPS and is out of scope here.
   - No credentials, API keys, or patient data are to be used against this host for this specification work, and no backend mutation is authorised.

---

## 1. Product definition

### 1.1 What is being built

A bilingual (Thai/English), responsive, role-aware clinic workspace for seven staff roles, replacing the static prototype with a React + TypeScript application talking to the existing Frappe-based backend through a same-origin proxy.

### 1.2 Scope (full intended scope)

| Dimension | Committed scope |
|---|---|
| Roles | 7 — `REC`, `DOC`, `NUR`, `PHA`, `CAS`, `MGR`, `ADM`. Multi-role users allowed; no role-switch escalation in UI. |
| Design surfaces | **37** (`APP-01…04`, `PAT-01…05`, `REC-01…05`, `DOC-01…04`, `NUR-01…02`, `PHA-01…07`, `CAS-01…03`, `MGR-01…02`, `ADM-01…05`). |
| Historical traceability | **97 of 97** v1 entries mapped, retained unchanged from v2 (manifest `coverage_check.status: complete`). |
| v3 changes | **31** change IDs (`V3-01…V3-31`), affecting **28** surfaces; **9** surfaces unchanged. |
| Required states per surface | **8** — populated, empty, loading, validation, error, success, permission-denied, unsaved/submit-confirmation. |
| Locales | `th` (default for new users) and `en`, switchable at any time including mid-draft. |
| Responsive targets | 1440 px desktop, 834 px tablet, 390 px phone (bands: 1280–1920 / 768–1279 / 360–767). |
| Clinical core | One canonical visit workspace (`DOC-02`); non-destructive before/after photo annotation; manual external-lab request → send → receive → review. |
| Operations | Inventory/batch/expiry, receiving, transfer, purchasing, invoicing, cash/card payment (mode only), manager reporting, admin setup. |
| Excluded (P2) | Patient portal, telehealth, inpatient/wards/ICU, insurance claims, payroll/commission, CRM/marketing/loyalty, AI diagnosis or risk scoring, automatic third-party lab API integration, online card processing, website CMS. |

### 1.3 Priority definitions used here

- **P0** — required for the initial complete application. P0 means *required*, not *already supported by the backend*.
- **P1** — post-launch, only if separately approved: appointment Kanban, request inbox, queue tokens/display board, queue analytics, stock value-at-risk, deeper service revenue breakdown. These are **not** drawn as release-required.
- **P2** — excluded as above.

### 1.4 Three distinct parity meanings

| Parity | Meaning | Evidence needed |
|---|---|---|
| **Visual parity** | Every P0 surface exists with its 8 states at 3 breakpoints in TH/EN, matching the approved design intent. | Rendered screens + visual/i18n review. Prototype is the reference, not a proof. |
| **Read parity** | The same surface shows real, server-authorised records with correct counts, filters, pagination. | Read-only contract tests against the installed site. |
| **Verified-write parity** | A mutation is enabled only after its contract is signed off and tested with disposable records, with durable reconciliation proven. | `Backend_Contracts_Installed_<date>.md` entry per operation + restart/timeout evidence + permission evidence. |

A surface can be at visual + read parity while every write on it is blocked. That is a **valid interim release, not completed P0**. Read-only delivery is a milestone that de-risks UI, permissions and localisation; it does not satisfy the P0 requirement for registration, booking, check-in, ordering, completion, stock, billing or review. P0 is complete only when the required operations are activated (§6.5) and their acceptance rows pass (§9.2, §9.3). A permanently read-only surface is acceptable only as an explicitly declined scope item, not by default.

---

## 2. Surface inventory and read waves

Wave numbers below are **Track D read/UI waves** (§8.1), not write permission. A surface's row says when its screens and reads ship; whether any action on it can write is decided separately by the activation task in §6.5. A blocked clinical write task never delays a surface's read wave, and a shipped surface does not imply its writes are enabled.

| Surface | Route | Roles | Read wave | Write activation (§6.5) |
|---|---|---|---|---|
| APP-01 Sign-in / session / language | `/clinic/login` | ALL | 0 | Yes — session create |
| APP-02 Shell, role home dispatch, profile, notifications | `/clinic` | ALL | 0 | Locale preference write only |
| APP-03 Global authorised search | `/clinic/search` | PERMITTED | 0 | No (read) |
| APP-04 Print/export + global system states | `/clinic/system` | ALL | 0 / 7 | Print/export permission |
| PAT-01 Patient directory | `/clinic/patients` | REC, DOC, NUR, ADM | 1 | No (read) |
| PAT-02 Patient create/edit | `/clinic/patients/new` | REC, ADM | 1 | Yes |
| PAT-03 Patient profile/history/timeline | `/clinic/patients/:id` | AUTHORIZED | 1 | No (read) |
| PAT-04 Photo gallery/capture | `/clinic/patients/:id/photos` | REC, DOC, NUR | 3 | Yes — upload/consent |
| PAT-05 Compare + annotate | `/clinic/patients/:id/photos/compare` | DOC, NUR-LIMITED | 3 | Yes — annotation version |
| REC-01 Reception home | `/clinic/reception` | REC | 1 | No (read, aggregated) |
| REC-02 Queue + check-in | `/clinic/reception/queue` | REC, NUR | 2 | Yes — check-in |
| REC-03 Appointment calendar | `/clinic/appointments` | REC, DOC | 1 | No (read) |
| REC-04 Appointment editor | `/clinic/appointments/:id` | REC, DOC | 1 | Yes — book/reschedule/cancel |
| REC-05 Checkout + follow-up | `/clinic/reception/checkout` | REC, CAS | 1 | Yes — handoff/follow-up booking |
| DOC-01 My Day | `/clinic/doctor` | DOC | 2 | No (read) |
| DOC-02 **Visit workspace** | `/clinic/visits/:id` | DOC, NUR-LIMITED | 2 | Yes — draft, note, orders, complete |
| DOC-03 Procedure + stock consumption | `/clinic/procedures/:id` | DOC, NUR-LIMITED, PHA-LIMITED | 2 | Yes — complete/consume |
| DOC-04 Lab request/result detail | `/clinic/labs/:id` | DOC, NUR-AUTHORIZED | 4 | Yes — order/send/receive/review |
| NUR-01 Nurse home | `/clinic/nurse` | NUR | 2 | No (read) |
| NUR-02 Nurse tasks (vitals/prep/aftercare/lab dispatch) | `/clinic/nurse/tasks` | NUR, DOC-AUTHORIZED | 2 | Yes |
| PHA-01 Pharmacy home | `/clinic/pharmacy` | PHA | 5 | No (read) |
| PHA-02 Items catalogue | `/clinic/pharmacy/items` | PHA | 5 | Master edit only |
| PHA-03 Batches & expiry | `/clinic/pharmacy/batches` | PHA, MGR | 5 | No (read) + restricted write-offs elsewhere |
| PHA-04 Balances & ledger | `/clinic/pharmacy/balances` | PHA, MGR | 5 | No (read) |
| PHA-05 Receive stock | `/clinic/pharmacy/receive` | PHA | 5 | Yes — purchase receipt |
| PHA-06 Movements / reconciliation | `/clinic/pharmacy/movements` | PHA, DOC-LIMITED, ADM-RECONCILE | 5 | Yes — transfer/issue/reconcile |
| PHA-07 Purchasing & suppliers | `/clinic/pharmacy/purchasing` | PHA, MGR | 5 | Yes — PO draft/submit |
| CAS-01 Cashier home / pending bills | `/clinic/cashier` | CAS, REC-DELEGATED | 6 | No (read) |
| CAS-02 Invoices | `/clinic/invoices` | CAS, MGR | 6 | Yes — invoice draft/submit/void |
| CAS-03 Payment/receipt/refund | `/clinic/payments` | CAS, MGR-APPROVAL | 6 | Yes — payment/refund |
| MGR-01 Owner dashboard | `/clinic/management` | MGR | 7 | No (read) |
| MGR-02 Management reports | `/clinic/management/reports` | MGR | 7 | Export only |
| ADM-01 Setup home | `/clinic/admin` | ADM | 7 | No (read) |
| ADM-02 Users & roles | `/clinic/admin/users` | ADM | 7 | Yes |
| ADM-03 Clinical masters (incl. working hours V3-24) | `/clinic/admin/clinical` | ADM | 7 | Yes |
| ADM-04 Inventory/pricing/settings | `/clinic/admin/settings` | ADM, MGR-AUTHORIZED | 7 | Yes |
| ADM-05 Audit / readiness | `/clinic/admin/system` | ADM | 7 | No (read) |

The 9 surfaces unchanged in v3 (`APP-03`, `PAT-01`, `PAT-02`, `DOC-03`, `PHA-02`, `CAS-02`, `ADM-01`, `ADM-02`, `ADM-04`) still require full implementation; "unchanged" refers to the v2→v3 design delta only.

### 2.1 v3 change IDs → surface and read wave

| Change | Surface(s) | Priority | Read wave |
|---|---|---|---|
| V3-01 Reception operational counts/tiles | REC-01 | P0 | 1 |
| V3-02 Queue cards, check-in, doctor handoff | REC-02 | P0 | 2 |
| V3-03 Token board / queue analytics | REC-02, MGR-02 | P1 | 8 |
| V3-04 Calendar availability + conflict validation | REC-03, REC-04 | P0 | 1 |
| V3-05 Appointment Kanban | REC-03, REC-04 | P1 | 8 |
| V3-06 Appointment request inbox | REC-04 | P1 | 8 |
| V3-07 Patient summary card, latest vitals, timeline | PAT-03 | P0 | 1 |
| V3-08 Photo/consent summary anchored in profile | PAT-04, PAT-05 | P0 | 3 |
| V3-09 Doctor My Day next-patient and pending work | DOC-01 | P0 | 2 |
| V3-10 Status-aware visit guidance, one entry point | DOC-02 | P0 | 2 |
| V3-11 Lab worklist states + provider mapping | DOC-04, DOC-02 | P0 | 4 |
| V3-12 Lab report upload with provenance | DOC-04, DOC-02 | P0 | 4 |
| V3-13 Nurse assigned tasks and vitals (no ward/ICU) | NUR-01, NUR-02 | P0 | 2 |
| V3-14 Pharmacy dashboard shortages/expiry/PO | PHA-01 | P0 | 5 |
| V3-15 Expiry urgency 7/30/90 with drilldown | PHA-03 | P0 | 5 |
| V3-16 Stock value at risk | PHA-03, MGR-02 | P1 | 8 |
| V3-17 Stock balance positive vs usable KPI | PHA-04 | P0 | 5 |
| V3-18 Low-stock → draft PO handoff | PHA-07 | P0 | 5 |
| V3-19 PO partial-receipt progress | PHA-05, PHA-07 | P0 | 5 |
| V3-20 Controlled adjustment/transfer with reason | PHA-06 | P0 | 5 |
| V3-21 Cashier due + same-day cash/card summaries | CAS-01, CAS-03 | P0 | 6 |
| V3-22 Manager funnel, invoices vs collected | MGR-01, MGR-02 | P0 | 7 |
| V3-23 Service-item revenue breakdown | MGR-02 | P1 | 8 |
| V3-24 Working days/hours, breaks, availability | ADM-03 | P0 | 7 |
| V3-25 Role-aware notifications / work items | APP-02 | P0 | 0 |
| V3-26 Icon-led actions, chips, master/detail, mobile cards | APP-02, APP-04 | P0 | 0 |
| V3-27 TH/EN for all new labels, badges, charts, print | APP-01, APP-02, APP-04 | P0 | 0 (continuous) |
| V3-28 PreClinic examples are reference only; contracts unverified | ADM-05 | P0 | 7 |
| V3-29 Exclude wards/AI/CRM/telehealth/payroll/insurance | none | P2 | — |
| V3-30 Duplicate check-in / stale queue / expired stock / retry tests | APP-04, REC-02, PHA-03 | P0 | 2, 5 |
| V3-31 Reception→cashier handoff, no clinical leakage | REC-05, CAS-01 | P0 | 6 |

---

## 3. Target architecture

### 3.1 Stack (decided)

| Concern | Choice | Notes |
|---|---|---|
| Language | TypeScript, `strict` | No `any` in domain/transport layers. |
| UI runtime | React 18+ | Function components, no legacy class components. |
| Build | Vite | Static build served from the same origin as the proxy. |
| Routing | React Router, route-per-surface | Deep-linkable record detail per manifest routes; no dead-end modals for long clinical entry. |
| Server state | TanStack Query | Explicit cache keys per domain; no client-side aggregation of financial/stock truth. |
| Forms | React Hook Form + Zod | Zod schemas mirror *verified* contracts only; unknown fields stay `unknown`, never guessed. |
| Styling | CSS variables from existing design tokens + component styles | Keep the measured token set; no template CSS import. |
| i18n | `react-i18next` | Stable message IDs; locale separate from backend `User.language`. |
| Tests | Vitest + React Testing Library + MSW; Playwright for E2E | Contract fixtures are versioned and clearly labelled as fixtures. |
| Accessibility | axe-core in CI | Target WCAG 2.2 AA as a goal, not a compliance claim. |

### 3.2 Layering

```
app/            routes, layouts, role guards (UX only)
features/<dom>  screens, hooks, query keys, view models
domain/         types derived from verified contracts; capability model
transport/      single fetch wrapper, error taxonomy, idempotency keys
i18n/           th/en catalogues, formatters, glossary
ui/             design-system primitives (button, chip, table, drawer…)
proxy/          (server) same-origin BFF — not shipped to the browser
```

Hard rules:

- One adapter per domain: `patient`, `appointment`, `encounter/note`, `photo`, `lab`, `procedure`, `inventory`, `purchasing`, `billing`, `reporting`, `admin`.
- No component calls `fetch` directly. Only `transport/` may.
- No screen computes clinical, stock, tax or payment totals as truth. Displayed totals are labelled as server-provided or as a **preview** with the server value re-read after submit.
- No `DocType.fieldname` strings in user-visible copy. Developer annotations map UI key → backend field in one place.

### 3.3 Same-origin proxy (BFF)

The browser never talks to the Frappe host directly and never holds backend credentials.

```
Browser ──same origin──► Proxy (auth + BFF) ──server-side session──► Frappe / Marley / ERPNext
```

Requirements:

1. **Origin:** proxy serves the built SPA and the API namespace from one origin, so cookies are first-party and no CORS is needed.
2. **Session:** login is posted to the proxy; the proxy authenticates upstream and issues an `HttpOnly`, `SameSite=Lax`, `Path=/` session cookie. The session identifier is rotated on login and logout (fixation defence). No session token, API key or CSRF secret in `localStorage`, `sessionStorage`, or JS-readable memory. (Evaluation host is HTTP → no `Secure` flag possible; documented as evaluation-only.)
3. **Browser-facing request protection (independent of upstream CSRF):** every state-changing browser request — **including login and logout** — is rejected unless all of the following hold:
   - `Origin` (or `Referer` when `Origin` is absent) matches the configured application origin; if both are absent, fail closed.
   - A proxy-issued, session-bound CSRF token is present and valid: synchronizer token for authenticated sessions; a pre-session login token bound to a short-lived `HttpOnly` cookie for the login request. Token is rotated on login and logout.
   - The request uses a non-simple content type (JSON or form) and a non-`GET` method; state changes via `GET` are rejected.
   - Rejections return a generic `403` with no detail; a security event is logged without patient data or credentials.

   This protection is owned by the proxy and is **separate from upstream CSRF**. The two tokens are never the same value, and an upstream token never reaches the browser.
4. **Upstream CSRF:** if the installed backend requires a CSRF token, the proxy obtains and attaches it server-side, bound to the server-side upstream session. Mechanism is `VERIFY` (BC3-18); the proxy must not assume a specific header name in advance, and browser-side protection in item 3 must not be weakened while that mechanism is unknown.
5. **Allow-list:** the proxy exposes only the operations the app needs — no generic "call any method" pass-through, no arbitrary resource path forwarding. Each route is an explicit entry in a server-side registry.
6. **Shape control:** the proxy returns a narrow DTO per screen, not raw documents. Clinical free text, identity numbers and file URLs are not returned to roles that cannot display them.
7. **Capabilities:** the proxy returns a per-user capability descriptor (`can.write.patient`, `can.write.appointment.checkin`, `can.review.lab`, …). The UI uses it to hide/disable actions. **This is UX only.** The server still authorises every request.
8. **Errors:** stable machine codes (`AUTH_EXPIRED`, `FORBIDDEN`, `VALIDATION`, `CONFLICT_STALE`, `CONFLICT_DUPLICATE`, `UPSTREAM_UNAVAILABLE`, `RATE_LIMITED`, `FILE_TOO_LARGE`, `UNSUPPORTED`) mapped to localised strings in the frontend. Raw upstream messages and stack traces never reach ordinary staff; technical detail goes to an authorised diagnostics channel.
9. **Files:** private files are streamed by the proxy after an authorisation check; no public/CDN URLs, no `Cache-Control: public`, no file path in URL query strings.
10. **Rate/size limits:** request size, upload size and per-session request rate are enforced at the proxy with configurable values — actual limits are `VERIFY` (BC3-08), not assumed (the source explicitly refuses to assume a 10 MB cap).
11. **Logging:** no patient identifiers, free text, file names or credentials in request logs. Correlation IDs only.

### 3.4 Durable concurrency and reconciliation per enabled operation

Idempotency is not a framework to adopt in advance; it is a property each enabled write must demonstrate. The requirements demand duplicate-safe retries, timeout-after-success handling and recovery across interruptions (NFR-05, F05, AT-21, V3-AT-02), and a browser-local memory lock cannot provide that.

**Per-operation requirement.** Before an operation may be enabled, its activation task (§6.5) must define and implement:

1. A **durable** operation record that survives proxy restart, browser restart and network loss: key `(user, operation, requestKey)`, state `pending | committed | failed | unknown`, created document reference(s), timestamps. Durable means a store with persistence guarantees — not an in-process map.
2. **Write-ahead ordering:** record `pending` and flush before calling upstream; update to `committed` with the returned reference after; mark `unknown` when the outcome is indeterminate.
3. A **deterministic reconciliation read** for `unknown`: lookup by `requestKey`, then by the operation's natural key (e.g. patient + appointment slot, encounter + note section). The UI offers "check status", never a blind retry.
4. **Duplicate-safe upstream behaviour** or an equivalent read-back check the proxy performs before allowing a second attempt.
5. **Per-section durability** for multi-document operations: each section's record is independent, so a partial commit is visible and resumable without re-posting committed sections.
6. **Restart test evidence:** kill the proxy mid-request and after upstream commit; restart the browser mid-flow; re-query and prove no duplicate document, no lost committed record, and no false "failed" for a committed operation.

**Gate rule.** If any of 1–6 cannot be implemented or proven for an operation, that operation stays disabled (`BLOCKED_UNVERIFIED`). Shipping the operation without durable reconciliation is not an option. Conversely, do not build a speculative general-purpose idempotency/outbox framework for operations that are not yet approved; implement the minimum per task, and only for tasks in the activation plan.

Client-side button locking, disabled states and optimistic spinners remain UX affordances. They are never the duplicate guarantee.

### 3.5 Auth and role behaviour

- Route guards are UX convenience. A role that lacks permission sees a permission-denied state (`403` mapping), never an empty screen pretending there is no data.
- Multi-role users get the union of permitted workspaces. There is no "switch to administrator" control.
- Reception sees demographic/arrival data, not clinical notes by default. Cashier sees billing references, not clinical detail. Inventory roles never read clinical notes. Admin setup rights do not imply clinical-content rights (FR v3 §1).
- Session expiry during edit: re-authenticate and return to recoverable work (AT-24). Unsaved clinical text is preserved only where a verified server draft exists; otherwise the UI warns before discarding and never writes PHI to persistent local storage.

---

## 4. Localisation (TH/EN) — P0

### 4.1 Model

- Two locales only: `th` (default for new users) and `en`. Type: `type Locale = 'th' | 'en'`.
- Locale is a **UI** concern. Backend values, document IDs, enum values and payloads stay locale-independent (L10N-07).
- Persistence: the per-user preference is stored through a **permitted** mechanism only. Candidate mechanisms (`User.language`, an approved custom preference field, or both) are `VERIFY` (BC3-17 / LOCALE-PREFERENCE). Until verified, persistence is device-scoped and labelled as such in the profile screen. No arbitrary `User` field mutation.
- Pre-login: the sign-in screen offers `ไทย | EN`; the choice applies to the session and is not silently overwritten by the saved preference after login without telling the user.

### 4.2 Behaviour contract

| ID | Implementation obligation |
|---|---|
| L10N-01 | Switch reachable from sign-in, global header and profile; selected locale visibly indicated; keyboard accessible. |
| L10N-02 | All UI strings localised: nav, titles, labels, help, placeholders, validation, empty/loading/error, confirmations, status chips, chart axes, tooltips, admin settings. |
| L10N-03 | Thai default for a new user; English available immediately; clinic default changeable by an authorised admin. |
| L10N-04 | Persistence per user across sign-ins/devices **after** the mechanism is verified; otherwise documented device-only. |
| L10N-05 | Switching mid-draft preserves: unsaved fields, active tab, selected patient, scroll position, upload state, filter/date context. Any surface that cannot preserve state must warn before reload — and this should be avoided, not designed around. |
| L10N-06 | **Never translate** clinician/patient-authored content: notes, free text, person names, allergy text entered verbatim, provider names, item codes, record IDs, attachments, original lab PDFs. |
| L10N-07 | Status chips translate labels only; the underlying value is the backend value. |
| L10N-08 | TH coverage complete for every P0 surface and every error state; missing keys fall back to EN and are recorded in a translation QA report containing no patient data. |
| L10N-09 | Thai-capable font stack, no glyph clipping or cramped controls. Current token stack already includes Thai fallbacks; font licensing must be reviewed before distribution. |
| L10N-10 | Display: Asia/Bangkok, 24-hour time, THB `฿` with locale grouping, one consistent date pattern. API uses ISO dates and canonical numbers. **No automatic Gregorian↔Buddhist year conversion** without an approved clinic policy. |
| L10N-11 | Search accepts Thai, English, mixed scripts and Unicode names; no transliteration requirement; no English-only lowercasing assumptions for Thai. |
| L10N-12 | Print/export language is an explicit choice per template; user-authored content and attachments are never auto-translated. Statutory Thai invoice/receipt wording needs separate accountant review. |
| L10N-13 | Counts and records identical across locale switch and re-login. |
| L10N-14 | Known server error codes map to local strings with a safe fallback; browser `Accept-Language` is never relied on for authenticated API errors. |
| L10N-15 | Clinical terminology reviewed by a Thai-speaking clinical stakeholder; bilingual glossary published and used consistently (Encounter / Visit / Procedure / Batch / Clinical Note disambiguated). |

### 4.3 Copy inventory and key reuse

The design source contains a 406-key EN/TH starter catalogue plus a 34-entry glossary, and a separate runtime snapshot of 1070 keys that the prototype actually renders. These two key sets are documented as distinct; neither is backend-verified, but both are usable copy sources.

**Implementation rule: reuse the prototype catalogue.** Where a prototype key is structurally sound and its EN/TH values pass review, adopt it unchanged — same key, same namespace, same string. There is no requirement to author a fresh namespace, and no bidirectional reconciliation deliverable is required. Translation continuity is worth more than naming purity.

Narrow exceptions only, each recorded as a defect rather than a migration project:

- A key whose name misleads about the string it holds may be aliased to a new key; the old key is retained in the catalogue.
- A missing string is added in the existing namespace that owns the surface.
- A wrong or clinically unsafe translation is corrected in place after review.
- Prototype-only demo strings (fictional names, sample amounts) are not carried into the production catalogue.

Rules that still apply: no patient data in keys or values; missing keys fall back to EN and are reported without patient data; clinician-authored content is never translated (§4.2 L10N-06).

### 4.4 Formatting

- Dates: single clinic pattern, displayed from ISO server values; duration/relative labels computed from server timestamps, not device clock alone.
- Currency: THB, server-provided amounts formatted for display; never recomputed as truth.
- Numbers: Thai locale grouping, Latin digits by default (no Arabic-Indic substitution).

---

## 5. Responsive and accessible behaviour

### 5.1 Breakpoints

| Band | Nav | Layout obligations |
|---|---|---|
| 1280–1920 (design 1440) | Persistent/collapsible sidebar | Queue tables, side-by-side photo compare, charts, multi-column KPI row |
| 768–1279 (design 834) | Collapsible drawer | Two-column action cards; touch-optimised note and annotation tools; sticky controls must not cover content |
| 360–767 (design 390) | Drawer or bottom nav | One-column cards; visit sections as stacked accordions; camera/upload capture; quick check-in; no horizontally clipped form fields |

### 5.2 Data-density rules

- Clinical entry never requires horizontal scrolling. If a table is too wide, it becomes responsive rows or a dedicated drill-down.
- Queue, stock and invoice tables become stacked cards on phone, ordered by operational priority (patient/time/status or item/expiry/quantity).
- Primary action may be sticky **only** when it cannot obscure a field or the keyboard.
- Photo annotation on phone is read/light-touch; full editing is optimised for tablet and desktop. Annotation must not be silently disabled without explanation.
- Modals are never stacked on modals.

### 5.3 Accessibility and touch

- Minimum interactive target ~44×44 CSS px; visible `:focus-visible` ring; logical focus order; focus trap and restore for drawers; keyboard path for every P0 action.
- Status is always text + icon + colour — never colour alone.
- Error summary at form top, inline messages at field, `aria-describedby` linkage; validation announced, not only coloured.
- `prefers-reduced-motion` honoured.
- Screen-reader support "where feasible" per requirements: all controls have accessible names; charts have text/table equivalents.
- Contrast: use the measured token pairs; cyan is never paired with white text.

### 5.4 Performance (targets to measure, not promises)

- Define budgets locally and measure on clinic hardware after an environment baseline: route-level code splitting, lazy image loading, virtualised long lists, server-side pagination/filtering. Do not publish unmeasured response-time claims (NFR-09).
- Large photos lazy-load with explicit loading/retry/offline states; clinically relevant detail is never silently compressed away.

---

## 6. Read vs verified-write gates

This is the core safety model. It exists because the design source, the requirements and the audit all agree on one thing: nothing about the backend is currently proven.

### 6.1 Capability states per action

Every mutating action (create, save draft, submit, cancel, check-in, complete, receive, transfer, pay, review, approve) has exactly one state at runtime, derived from the server capability descriptor:

| State | UI treatment | Preconditions |
|---|---|---|
| `READ_ONLY` | Action absent or disabled with reason "not available in this deployment" | Default for every write until its gate clears |
| `BLOCKED_UNVERIFIED` | Action visible but disabled with an explicit "awaiting backend verification" reason (admin/diagnostics view only; not shown as a tease to clinic staff in production) | Contract not signed |
| `ENABLED_VERIFIED` | Action enabled | Signed contract + permission + feature flag |
| `DENIED_FOR_ROLE` | Action hidden; direct navigation shows permission-denied | Server says no |
| `ERROR_UPSTREAM` | Action disabled, retry path shown | Upstream unavailable |

Rules:

1. **Writes are off by default.** Shipping with all writes disabled and all reads working is the expected outcome of the UI/read waves. That state is an **interim release, not completed P0** (see §1.4 and §9.3): P0 completes only when the required write operations are activated and their acceptance rows pass.
2. **The client never decides permission.** It renders what the server says it may do. Every request is re-checked server-side.
3. **No optimistic writes** on clinical, stock, invoice or payment data. No local counter, status badge or balance is mutated before the server confirms.
4. **No generic status endpoint.** A chip that says "Checked In" may only exist if a verified method produced it. Direct `PUT status=...` is prohibited (BC3-01, NFR-06, FR v3 §13.1).
5. **No client truth for money or quantity.** Totals and balances are server values; previews are labelled previews and re-read after submit.
6. **Extensions are gated harder.** Any `EXT` entity (consent, photo, annotation, external lab, exam store, visit orchestration, queue) requires an approved definition before UI wiring (gate G5).

### 6.2 Contract record required per write

A write may be enabled only when a signed `Backend_Contracts_Installed_<date>.md` records, for that action:

1. Installed versions and config snapshot (sanitised) — gate G1.
2. Document/child-table metadata: fieldname, fieldtype, options, `reqd`, `mandatory_depends_on`, `read_only`, defaults, child schema, status fields — gate G2.
3. Actual route or whitelisted method; no guessed URLs.
4. Valid transitions, and the side effects (ledger, GL, cascades such as service requests).
5. Roles and record-scope rules with positive **and** negative permission tests — gate G3.
6. Example successful request/response on disposable records, plus failure cases.
7. Duplicate-click, timeout-after-success, stale-version and partial-failure behaviour — gate G4.
8. Cancellation/rollback/amend semantics where the document type supports them.
9. Reconciliation: the record created by the UI matches the underlying ledger/accounting/stock records.

**v3 contract checks (BC3) and what each unlocks:**

| Check | Unlocks | Check | Unlocks |
|---|---|---|---|
| BC3-01 Check-in method | REC-02 check-in | BC3-10 Expiry/valuation | PHA-03 alerts |
| BC3-02 Queue status/timing | REC-02 display states | BC3-11 Reorder → draft PO | PHA-01/07 reorder |
| BC3-03 Appointment request entity | P1 request inbox | BC3-12 PO/receipt progress | PHA-05/07 progress |
| BC3-04 Availability/hours | REC-03/04 booking, ADM-03 | BC3-13 Adjustment/write-off | PHA-06 adjustment |
| BC3-05 Recent vitals | PAT-03 summary | BC3-14 Payment metrics | CAS-01/03 summaries |
| BC3-06 Doctor worklist | DOC-01 | BC3-15 Service revenue | MGR-02 (P1 split) |
| BC3-07 External lab lifecycle | DOC-04 transitions | BC3-16 Notifications | APP-02 work items |
| BC3-08 Lab uploads | DOC-04 upload | BC3-17 TH/EN labels/errors/print | All P0 locales |
| BC3-09 Stock KPI | PHA-01/04 | BC3-18 Same-origin/session/CSRF | All authenticated calls |

### 6.3 Gate status by domain (all currently unverified)

| Domain | Gate status | Consequence |
|---|---|---|
| `AUTH-SESSION`, `ROLE-MATRIX` | UNVERIFIED / DECISION_REQUIRED | Sign-in may be built against the proxy; role matrix must be decided before any role-specific write |
| `PATIENT-CREATE/UPDATE/SEARCH` | UNVERIFIED | Directory read can be piloted; registration write stays disabled |
| `APPOINTMENT-BOOK/CHECKIN` | UNVERIFIED | Calendar read first; booking and check-in disabled |
| `ENCOUNTER-CREATE`, `CLINICAL-NOTE-SAVE`, `EXAM-FINDINGS` | UNVERIFIED | Visit workspace is read-only until note/exam persistence boundaries are proven |
| `CONSENT-CAPTURE`, `PHOTO-*`, `PHOTO-ANNOTATE/COMPARE` | EXTENSION_LIKELY | Photo features are designed and read-only; no capture write |
| `NATIVE-LAB`, `EXTERNAL-LAB` | UNVERIFIED / EXTENSION_LIKELY | Lab list read only; no status transitions, no review action |
| `PROCEDURE-REQUEST/COMPLETE` | UNVERIFIED | Procedure detail read only; no consumption |
| `STOCK-*`, `PURCHASE-*` | UNVERIFIED | Stock/batch/PO lists read only; no receipt, transfer, adjustment |
| `SALES-INVOICE`, `PAYMENT-RECEIVE` | UNVERIFIED | Invoice/payment screens read only |
| `OWNER-METRICS` | UNVERIFIED | Dashboards show "unavailable" rather than fabricated values |
| `LOCALE-PREFERENCE`, `BILINGUAL-PRINT` | UNVERIFIED / DECISION_REQUIRED | Device-scoped locale until proven; print templates need Thai review |

Reads are gated less strictly but not ungated. A read screen may be enabled when: the proxy session works, the query is server-authorised, the DTO exposes only fields the role may see, and a negative permission test exists for that role. Where `ROLE-MATRIX` is still `DECISION_REQUIRED`, reads run against a non-production dataset or stay behind a feature flag. No read may be pointed at real patient data before the role matrix is decided.

### 6.4 Prohibited implementations (hard list)

- Direct write of any workflow `status` field to match a badge.
- Client-set `docstatus`, or submit/cancel performed by generic resource update.
- Any write to stock ledger, batch quantity, item balance, GL entry or account balance.
- Client-computed invoice totals accepted without server re-read.
- Collecting, transmitting or storing PAN, CVV, or any card secret. "Credit Card" is a payment **mode**, not a gateway.
- Disabling or bypassing CSRF, cookie, or origin protections.
- Credentials, API keys or upstream tokens in browser storage or bundles.
- Patient content in analytics, error trackers, URLs, toast logs, console output or filenames.
- Silent auto-submission of medications/procedures on a generic "Save Draft".
- Impersonation / role-escalation controls.
- "All systems healthy" style status claims without a real check behind them (ADM-05).

### 6.5 Write activation tasks (independent, per operation)

Write activation is a set of **separate tasks**, not a phase. Each task enables only the operations named in it, requires its own signed contract evidence, and ships behind its own capability flag. A blocked clinical task does not block unrelated UI/read delivery or unrelated write tasks; conversely, enabling registration does not enable booking or clinical writing.

| Task | Operations enabled | Surfaces | Required contract evidence | Durable reconciliation key | Extra enable conditions |
|---|---|---|---|---|---|
| **WA-PAT-01** Registration & editing | Create patient; update permitted demographics/contact/allergy status | PAT-01, PAT-02, PAT-03 (refresh) | `PATIENT-CREATE/UPDATE` verified: mandatory fields, duplicate rule, Thai names, Patient→Customer link, permission negative tests | `requestKey` + natural key (normalised name + mobile + DOB where known) | Duplicate warning path proven; consent fields remain `EXT` and are not part of this task |
| **WA-APT-01** Booking lifecycle | Book, reschedule, cancel appointment; reject conflicts; mark no-show if authorised | REC-03, REC-04, REC-01 (counts) | `APPOINTMENT-BOOK` + BC3-04 availability: slot method, duration, overlap rule, status transitions, cancellation reason field | `requestKey` + natural key (patient + practitioner + date + time), then appointment read-back | Server re-validates availability on save; stale and conflict paths proven |
| **WA-APT-02** Check-in | Check in an eligible appointment (arrival only) | REC-02, REC-01 (counts), DOC-01 (queue) | `APPOINTMENT-CHECKIN` + BC3-01: exact method, eligible statuses, timestamp, permission, duplicate-click behaviour | `requestKey` + appointment ID + arrival indicator, read-back before retry | **Direct `PUT status` is forbidden**; if no verified method exists, this task stays blocked and REC-02 remains read-only |
| **WA-MED-01** Medication ordering | Add/remove medication order lines within the visit; save them to the authoritative record | DOC-02 (medications), DOC-04 (linked orders if applicable) | `MEDICATION-ORDER` verified: field/child schema, dose/interval mode, duration, downstream request generation, draft vs submit semantics | `requestKey` + encounter ID + line identity (medication + dose + sequence), read-back of the order set | Prescribed ≠ dispensed ≠ consumed ≠ invoiced; no auto-submission from Save Draft; may remain blocked while WA-VST-01 is evaluated (see dependency) |
| **WA-VST-01** Visit draft & completion | Per-section draft persistence (WA-VST-01a) and final note/encounter completion (WA-VST-01b) | DOC-02 (all sections), DOC-01 (drafts), DOC-03 (linked) | `ENCOUNTER-CREATE`, `CLINICAL-NOTE-SAVE`, `EXAM-FINDINGS`; submit/complete method and cascade, sign-off identity, immutability, amendment path | `requestKey` per section + encounter ID + section identity; completion keyed by encounter + note version, read-back before retry | 01a may ship alone (draft only). 01b requires 01a proven, and requires WA-MED-01 **only if** completion submits orders as a cascade; if the cascade is unavoidable and unverified, 01b stays blocked while 01a and other screens continue |

Rules for all activation tasks:

1. **Independent flags.** `can.write.patient.create`, `can.write.appointment.book`, `can.write.appointment.checkin`, `can.write.encounter.draft`, `can.write.encounter.complete`, `can.write.medication.order` are separate capabilities. No shared "clinical writes on" switch.
2. **Read/UI unaffected.** A blocked task hides or disables only its own controls; the surface, its read data and its other actions remain available.
3. **One task, one evidence pack.** The task's section of `Backend_Contracts_Installed_<date>.md` plus restart/timeout evidence (§3.4) must exist before the flag is set.
4. **Other writes follow the same pattern.** Procedure completion, lab transitions, photo/consent upload, stock receipt/transfer/adjustment, PO submit, invoice submit, payment, refund and admin masters each get their own task ID and flag; they are not enumerated here because none is closer to readiness than the five above.
5. **Rollback is a flag change.** Disabling a task returns its controls to `READ_ONLY` without affecting other tasks or the read surface.

### 6.6 Read activation (separate from write activation)

A read surface may ship while every write task is blocked. Read activation requires: proxy session working, server-authorised query, role-appropriate DTO, and a negative permission test. Where `ROLE-MATRIX` is `DECISION_REQUIRED`, reads run against a non-production dataset or behind a flag; no read touches real patient data before the matrix is decided. Read activation is tracked per surface and is not a precondition for unrelated write tasks.

---

## 7. Domain behaviour contracts (design-level)

These are the obligations the React implementation must satisfy. Field-level inputs are defined by FR v3 §6 and the handoff field inventories; backend targets remain unverified.

Form groups covered, in order: **F01** patient search/register/edit, **F02** appointment/reschedule/cancel, **F03** photo + annotation, **F04** check-in/queue/checkout, **F05** clinical visit + vitals, **F06** medication orders, **F07** procedure/consumables, **F08** native + manual external lab, **F09** item/batch/expiry, **F10** receive stock, **F11** transfer/issue/reconcile, **F12** purchase order/supplier, **F13** invoice/pricing, **F14** payment/receipt/refund, **F15** admin masters, **F16** shared form states (not a form). Counts: 225 field rows, 91 flagged `VERIFY`, 24 flagged `EXT`.

### 7.1 Patient (PAT-01…03)
- Search by name, mobile, patient ID, approved identity number; duplicates flagged as warnings, never auto-merged.
- Registration validates before create; on success shows backend Patient ID and next actions.
- Allergies use explicit `Known` / `None reported` / `Unknown` — blank must never read as "no allergy".
- Identity numbers are masked, permission-scoped, and never appear in queues or URLs.
- Profile: persistent identity header, latest vitals with measurement time and units (`No measurements yet` instead of zeros), timeline of appointments/visits/labs/treatments/photos/payments, all permission-filtered, one patient identity (no name-based mixing).

### 7.2 Appointments and queue (REC-01…05)
- Home shows 2–4 record-derived counts with a date scope and drill-down; tiles are 4–6 real actions only.
- Queue statuses (`Scheduled`, `Arrived`, `Waiting`, `Called`, `In Consultation`, `Completed`, `No-show`, `Cancelled`) are **display concepts**. Each must map to an installed status or a documented custom record. If only appointment states exist, wave D1 shows the appointment list and the token board is P1/`VERIFY`/`EXT`.
- Check-in: one eligible action per appointment, server-confirmed, duplicate-safe, disabled for invalid states.
- Booking: server re-validates availability on save; conflicts and stale records handled; cancel/reschedule require reason + confirmation.
- Walk-in: creates/links patient and visit per installed workflow; no invented queue number.

### 7.3 Clinical workspace (DOC-02) — one workspace, many documents
- Single page, nine sections: Overview, History, Exam/Vitals, Clinical Note, Photos, External Lab, Medications/Procedures, Review & Complete (plus diagnoses within the note flow).
- **One authoritative location per clinical fact.** Prior history is read-only and referenced, never copied into a second editable field. Free text must not be written into layout/display-only fields (e.g. `physical_examination` is not assumed editable).
- Persistent patient header: name, ID, date/time, appointment/encounter ID, practitioner, visit state; sticky on long screens.
- Save Draft is per-section with per-section timestamps and failure reporting; the workspace never claims global success on partial failure.
- Review shows exactly which documents/actions will be submitted. Saving a draft is never presented as signing a note.
- Complete/Submit is a verified method, exactly once, with confirmation; finalised content cannot be silently overwritten (AT-25).

### 7.4 Photos (PAT-04/05) — `EXT`
- Lifecycle: consent → private original uploaded with Patient link (before check-in) → optional appointment association → encounter link without re-upload → annotation versions → audit.
- If encounter linking fails, originals remain attached to the patient/intake context and are retryable without duplication.
- Annotation is non-destructive: original immutable; arrow/ellipse/rectangle/text/freehand as agreed; zoom/pan, undo/redo, select/resize/delete; versioned JSON/vector layer; flattened preview only as a derived artefact.
- Compare across visits by same body area/viewpoint with dates shown; side-by-side and slider.
- No consent → capture blocked with a specific remedy, not a generic error.

### 7.5 Lab (DOC-04) — native + manual external
- Native and external are different underlying entities; the UI never invents native statuses.
- External lifecycle: `Ordered → Sent → Received → Reviewed` (+ `Cancelled` via authorised action), with actor and timestamps: sample sent, provider reference, received at, reviewed by/at, interpretation.
- `Reviewed` requires an authorised doctor; file upload requires a linked request; retries do not duplicate files or records; original PDF never overwritten.
- `Critical` flags only from a validated clinician-entered flag/measurement — never inferred from an uploaded file.

### 7.6 Inventory and purchasing (PHA-01…07)
- Expiry filters `Expired / 7 / 30 / 90 / All` show **usable positive on-hand** at the selected warehouse, with days remaining; overlapping alert windows explained; exclusive categories when summed.
- Low stock compares against configured reorder level; if not configured, show "not configured" rather than guessing.
- `Reorder` opens a **draft** PO prefilled with item/quantity/warehouse/date — never auto-submitted.
- PO progress derives from submitted receipts, not visual flags.
- Receiving and transfers preview stock effect and submit once; batch/expiry selection follows the installed batch/serial bundle behaviour (`VERIFY`).
- Stock Adjustment opens the proper reconciliation workflow with reason and confirmation — never a balance edit.
- `Value at Risk` is P1 and requires verified valuation; otherwise unavailable.

### 7.7 Billing (CAS-01…03, REC-05)
- Pending queue shows billing-relevant identifiers and linked service, no clinical history.
- Invoice: accounting customer linkage, service lines, discount by permission, tax by policy, total **from server**; show already-invoiced sources to prevent double billing.
- Payment: verified workflow, full/partial where supported, allocation per invoice, receipt only after confirmed posting; refund/return is a separate authorised lifecycle.
- Manager reporting keeps **invoiced revenue** and **collected cash** distinct.

### 7.8 Dashboards and admin (MGR, ADM)
- Every KPI declares date range, denominator and business meaning; drilldown opens the source records.
- Seeded demo scale (per requirements suggestion): ~180 patients, 540 appointments, 300 encounters, 170 procedures, 220 invoices, 24 items, 45 batches, 24 POs, 18 receipts, 80–120 movements — fictional and clearly marked. Smaller scale allowed if capacity requires, but every KPI must reconcile with source records.
- Admin: users/roles/practitioner links with per-role access test, schedules with breaks/leave (V3-24), masters, settings, TH/EN default, audit/integration status with redacted logs.
- No fabricated health status. `ADM-05` shows verified check results or "not checked".

---

## 8. Delivery plan: two independent tracks

Delivery is split so a blocked clinical write cannot stall unrelated screens.

- **Track D — UI and read delivery.** Builds surfaces, states, localisation, responsiveness and read integrations. Never waits for a write contract.
- **Track A — Write activation.** Enables one operation set at a time under §6.5. Runs in whatever order contracts clear, independently for each task.

Neither track blocks the other. A wave in Track D can ship while every Track A task is blocked; a Track A task can enable against an already-shipped surface. Within Track A, tasks are also independent: for example `WA-PAT-01` and `WA-APT-01` may enable while `WA-VST-01b` remains blocked.

### 8.1 Track D — UI and read waves

Waves are ordered for review efficiency, not for gating. A wave may be cut short; it may not be used to claim P0 completion.

| Wave | Delivers (UI + read) | Exit criteria |
|---|---|---|
| **D0 Foundation** | Vite React TS app; routing; tokens; primitives; TH/EN harness reusing prototype keys; same-origin proxy skeleton; login/logout/expiry; browser-facing CSRF/origin protection; capability descriptor; error taxonomy; global states; `APP-01…04`; responsive nav; 8 states scaffolded; global search read | Login works through the proxy; CSRF/origin rejection tests pass for login and one mutation-shaped request; locale switch preserves route + form state; no write route enabled; no secrets in bundle; axe passes on shell; `APP-*` reviewed at 390/834/1440 in both locales |
| **D1 Front desk read** | `REC-01` KPIs, `REC-03` calendar, `REC-04` detail, `REC-05` handoff view, `PAT-01…03` | Permission-correct reads with negative tests; counts reconcile; empty/loading/error/permission-denied real; no clinical leakage to reception/cashier |
| **D2 Clinical read** | `DOC-01`, `DOC-02` all sections read-only, `NUR-01/02` read, `REC-02` queue read | One encounter context across tabs; history read-only; no duplicate edit target; display-only queue states annotated |
| **D3 Photos read** | `PAT-04` gallery, `PAT-05` viewer/compare (no annotation save) | Private file access proven with negative tests; no URL leakage; consent status displayed truthfully |
| **D4 Labs read** | `DOC-04` list/detail, `DOC-02` lab section read | Correct entity per lab type; states annotated; no review action enabled |
| **D5 Pharmacy read** | `PHA-01…07` read, draft form UI without submit | Balances and expiry counts match source queries; unusable/expired distinguished; no stock write |
| **D6 Billing read** | `CAS-01…03` read, invoice/payment detail, `REC-05` | Invoiced vs collected distinct; no payment action; no clinical detail leak |
| **D7 Management/admin read** | `MGR-01/02`, `ADM-01…05`, print/export previews | KPIs reconcile or show "unavailable"; audit shows real checks; working-hours UI displays real schedule data |
| **D8 P1 optional UI** | Kanban, request inbox, token board, queue analytics, value-at-risk, service revenue | Only if approved; same records; marked P1; no unsupported channel implied |
| **D9 UI/read close-out** | Full responsive, a11y and TH/EN review of all 37 surfaces in their enabled state | All 8 states verified per surface; clipping/locale QA clean; interim-release statement signed (§9.3) |

### 8.2 Track A — Write activation tasks

Refs are identifiers, not a sequence: activation follows contract readiness, not the numbering. Default: blocked.

| Ref | Task | Enables | Dependency note |
|---|---|---|---|
| A1 | `WA-PAT-01` | Patient create/edit | None; booking, check-in and clinical tasks stay independent |
| A2 | `WA-APT-01` | Book/reschedule/cancel | Needs an existing patient record only; not gated by `WA-PAT-01` |
| A3 | `WA-APT-02` | Check-in | Not gated by clinical tasks; stays blocked if no verified method exists |
| A4 | `WA-VST-01a` | Visit section drafts | Check-in not required; walk-in encounter only if verified |
| A5 | `WA-MED-01` | Medication order lines | Independent of completion; prescribed ≠ dispensed |
| A6 | `WA-VST-01b` | Visit/note completion & sign-off | Requires 01a; requires `WA-MED-01` only if completion cascades order submission |
| A7+ | Procedure, lab, photo/consent, stock, purchasing, invoicing, payment, refund, admin | Each per its own task ID | Each independent |

Exit criteria per task are the evidence pack in §6.5 plus the relevant acceptance rows in §9.2.

### 8.3 Interim read-only release

After D9 with Track A empty or partial, the product may be released internally as an **interim read-only / partial-write release**. Its release note must state: which surfaces are read-only, which operations are disabled, and that P0 is **not** complete. This interim state is useful for workflow review, localisation sign-off and permission testing. It cannot be presented to the clinic as a delivered P0 application.

### 8.4 Acceptance close-out

Run the full AT/V3-AT matrix on clinic hardware with Thai-first staff testing; record evidence per row; list every explicit decline as a signed scope change. Close-out starts only when every required write task is either enabled or formally declined.

---

## 9. Testing and acceptance

### 9.1 Test layers

| Layer | Tool | Scope | Notes |
|---|---|---|---|
| Unit | Vitest | formatters, validators, capability gating, state reducers | No network |
| Component | RTL | 8 states per surface, a11y roles/labels, locale switching without data loss | MSW fixtures, never live data |
| Contract (read) | Vitest + proxy harness | DTO shape, pagination, permission mapping, error codes | Allowed once a read contract is recorded |
| Contract (write) | scripted, disposable records only | idempotency, transitions, side effects, rollback | Requires signed contract + explicit approval; never on production data |
| E2E | Playwright | 7 role journeys, 3 viewports, TH and EN | Real proxy, disposable dataset |
| Accessibility | axe + manual keyboard | shell, forms, tables, drawers, photo editor | Goal WCAG 2.2 AA |
| Visual | screenshot diff | 3 breakpoints × 2 locales × key states | Catches Thai clipping |
| i18n QA | key-coverage script | missing keys, long-string overflow, untranslated UI inventory | Fails build on missing P0 keys |
| Resilience | fault injection | 401/403/timeout/stale/duplicate/partial failure; proxy kill mid-request; browser restart mid-flow; re-query after `unknown` | Asserts no duplicate writes, no lost committed record, no false failure |
| Security (proxy) | proxy test suite | cross-origin POST rejected; missing/invalid CSRF rejected on **login** and every mutation; token rotation on login/logout; cookie flags; absent Origin fails closed; no upstream token in any response | Must pass before D0 exits |

### 9.2 Mandatory acceptance mapping

All P0 rows must pass or be explicitly declined with a signed scope change. "Evidence" below is what must be recorded, not what exists.

**Core matrix (P0 unless marked):**

| ID | Scenario | Evidence required |
|---|---|---|
| AT-01 | Thai-named patient registration; duplicate detection | Created patient, Thai rendering intact, duplicate flagged |
| AT-02 | Book/reschedule/cancel appointment; invalid slot | Server-confirmed slot/status/doctor, no duplicate booking |
| AT-03 | Check-in and waiting queue | Correct patient/doctor, double check-in prevented, only supported states shown |
| AT-04 | Photos before check-in | Consent linked, originals private, later encounter link without re-upload |
| AT-05 | Prior history opened from visit | Read-only history, no duplicate entry |
| AT-06 | Physical exam and vitals entered | Saved to a valid record, never to display-only fields |
| AT-07 | Unified note saved and reopened | Single entry point, correct encounter linkage, draft version |
| AT-08 | Annotate before photo, compare with after, undo/redo | Layer persists separately, original unchanged, correct visit/body area |
| AT-09 | External lab request → send → upload → review | Status timeline, actors, private file, doctor sign-off |
| AT-10 | Complete treatment with an expiring batch | Available stock checked, one completion, exactly one stock transaction |
| AT-11 | PO → partial receipt with batch/expiry | Quantity matches ledger, PO balance correct |
| AT-12 | Transfer to treatment room | Source/destination/batch reconcile, no double posting |
| AT-13 | Invoice services and receive cash | Linked customer, correct tax/discount, allocation, receipt |
| AT-14 | Credit card as payment mode | Posted amount/account correct, no PAN/CVV collected |
| AT-15 | Owner KPI drilldown | Revenue, collected, outstanding and inventory reconcile to records |
| AT-16 | Role permissions | Each role sees only authorised information and actions |
| AT-17 | TH↔EN on every role home | All labels/buttons/charts/nav/errors translated, no inaccessible control |
| AT-18 | Language switch mid-note and mid-annotation | Unsaved data, tab, patient, annotation layer intact |
| AT-19 | Language persists across devices/sessions | Per-user preference persists; guest choice not silently overwritten |
| AT-20 | Thai/English errors, dates, money, print | No clipped Thai, consistent formats, print language policy respected |
| AT-21 | Broken connection and timeout-after-success | Draft recoverable, no duplicate appointment/procedure/invoice/stock/payment |
| AT-22 | Mobile and tablet walkthrough | Main workflow works at 390/834; note and photo editor touch-usable |
| AT-23 | 97 legacy entries traced in manifest | All mappings pass, including conditional tabs/drawers and system states |
| AT-24 | Session expiry during edit | Re-auth returns to recoverable work without leakage |
| AT-25 | Finalised note amended by permitted role | Correct lifecycle/audit, no silent overwrite |
| AT-26 (P1) | Advanced analytics and automated alerts | Truthful configured reports; not a P0 blocker |

**v3 matrix:**

| ID | Scenario | Evidence required |
|---|---|---|
| V3-AT-01 | Reception KPIs and row actions | Totals match same-day source; only eligible action; no private note leakage |
| V3-AT-02 | Double check-in, slow network, stale appointment | Exactly one check-in, accurate feedback, no duplicate queue record |
| V3-AT-03 | Doctor opens next visit from My Day | One encounter, same patient/doctor/appointment, prior data intact |
| V3-AT-04 | Returning patient with vitals and photos | Vitals show date+unit; photo visit/body area correct; privacy enforced |
| V3-AT-05 | External lab upload/review | Private file, correct request/reviewer, original retained, errors handled |
| V3-AT-06 | Expiry filters 7/30/90 by warehouse | Only positive on-hand batches, correct days-to-expiry, no double count |
| V3-AT-07 | Reorder from low-stock list | Draft PO prefilled, never auto-submitted, stock unchanged |
| V3-AT-08 | PO partial receipt progress | Quantities come from submitted receipts, match outstanding |
| V3-AT-09 | Manager KPI drilldown | Invoiced ≠ collected; definitions stated; chart matches documents |
| V3-AT-10 | Working hours, break/leave, conflicting booking | Slot blocked server-side, visual unavailable state, no overlap saved |
| V3-AT-11 | TH/EN mid-queue and during lab upload | Status/actions/errors translated, unsaved upload retained, IDs unchanged |
| V3-AT-12 | Role notification and follow-through | Only eligible role sees it, navigation authorised, expiry safe |
| V3-AT-13 | Front desk + pharmacy at 390/834/1440 | Primary info/actions accessible, nothing mandatory clipped |
| V3-AT-14 (P1) | Kanban and request-inbox variants | Same booking records, no duplicates, no unsupported channel |
| V3-AT-15 (P1) | Token board and wait analytics | Real timestamps and permissions, else marked unavailable |
| V3-AT-16 (P1) | Value at risk and service revenue | Verifiable valuation/invoice sources, no invented KPI |

### 9.3 Release gates (from requirements, restated as the project's own)

1. **UX/read completeness:** all P0 surfaces approved at 3 breakpoints, 8 states, 37 surfaces and 97 mappings accounted for, no duplicate history/exam editor. This gate can pass with writes blocked.
2. **Write activation:** every required write operation is either enabled via its own `WA-*` task with a complete evidence pack (§6.5) and restart/timeout proof (§3.4), or explicitly declined in writing. Partial activation is allowed only if the disabled remainder is declared.
3. **Backend readiness:** installed version and schema recorded, mappings reconciled with Custom Fields/Property Setters, transitions tested on disposable records, all `EXT` models approved and implemented.
4. **Bilingual acceptance:** TH coverage complete, empty untranslated inventory, glossary approved by a Thai-speaking clinician, content/state stable on switch.
5. **Operational acceptance:** AT-01…25 and V3-AT-01…13 pass or are declined in writing. P0 extensions cannot be relabelled "out of the box".
6. **Data reconciliation:** every UI number corresponds to permitted backend records. No placeholder KPIs after handoff.

**Interim release classification.** Passing gates 1, 3, 4 and 6 while gate 2 is empty or partial yields an **interim read-only / partial-write release**, not a completed P0 delivery. It may be used for internal workflow review, localisation sign-off and permission testing; it must be labelled as interim in the release note and in the product's admin status view. P0 is complete only when gate 2 and gate 5 are satisfied.

### 9.4 Definition of done for a single surface

A surface is done when: route + deep link work; all 8 states render; TH/EN complete with no clipped copy; 390/834/1440 reviewed; permission-denied path tested with a real unauthorised account; every control is wired to a real destination or explicitly disabled with a reason; no console errors; no PHI in logs/URLs/analytics; each control's activation state is displayed honestly (read-only vs enabled, per §6.5); and its acceptance rows are recorded. A surface can be DoD-complete for **read** while its writes remain blocked; that is not P0 completion for the surface's write-dependent acceptance rows.

---

## 10. Risks and open decisions

| # | Risk / decision | Impact | Owner | Handling |
|---|---|---|---|---|
| R1 | No installed-site evidence exists | All writes blocked indefinitely | Orchestrator + backend owner | Gate G1–G4 before any mutation; ship read-only if unresolved |
| R2 | Prototype scaffold looks production-ready (`frappe-api.js`) | Copy-paste would violate NFR-06 and BC3-01 | Implementers | Explicitly rejected in §0.1; new transport only |
| R3 | Version claims in design handoff are unsourced | Wrong schema assumptions | Orchestrator | Record actual versions from site; delete unsourced claims from UI/docs |
| R4 | Extensions (consent, photo, annotation, external lab, exam store, queue) may not exist | Three P0 journeys blocked | Backend owner | Approve extension definitions (G5) before the D3/D4 read waves and their activation tasks |
| R5 | Evaluation host is HTTP only | No camera capture; weak transport; no Secure cookies | Orchestrator | Upload fallback; evaluation-only label; production needs HTTPS |
| R6 | Locale persistence mechanism unknown | Preference may not persist cross-device | Backend owner | Decide `User.language` vs custom preference; else device-scoped |
| R7 | Thai clinical terminology approval pending | Release gate failure | Clinic stakeholder | Glossary review before sign-off |
| R8 | Design tokens derived from a commercial template | Licensing exposure if assets reused | Orchestrator | Use tokens/CSS only; no template assets, screenshots, copy or theme code |
| R9 | Two distinct translation key sets in source | Divergent copy | Implementers | Reuse the sound prototype catalogue; defect list for missing/unsafe keys only (no migration project) |
| R10 | Manager KPIs depend on unproven aggregates | Fabricated KPI temptation | Backend owner | Show "unavailable" until a verified source exists |
| R11 | Card handling scope creep | Compliance/security breach | Orchestrator | Payment mode only; PAN/CVV forbidden by design |
| R12 | Queue semantics conflated with appointment status | False operational state | Product | Display concepts annotated with source; token board P1 |

### 10.1 Assumptions

1. The requirements package in `OpenDesign Requirements v3/` is the authoritative scope; the prototype is the visual reference only.
2. The existing Frappe/Marley/ERPNext installation remains the system of record; no core source modification is planned (extensions in a separate app only).
3. The React app is served from the same origin as the proxy; no cross-origin API access is required.
4. Frappe remains responsible for authorisation, workflow, ledger and audit; the frontend never becomes authoritative.
5. Fictional demo data only; no real patient data in any wave or activation task.
6. `10.0.100.10:8080` is a disposable evaluation target, not production. No writes to it are authorised by this document.
7. Thai is the default UI language for new users; the clinic may change the default only through an authorised admin path.
8. Browser support target is current evergreen desktop + mobile browsers; no IE/legacy support.

---

## 11. Traceability to requirements

| Requirement area | Where addressed | Status |
|---|---|---|
| §1 roles, access rules | §1.2, §3.5, §7 | Covered |
| §2 interaction/accessibility/responsive | §5 | Covered |
| §3 L10N-01…15 | §4 | Covered |
| §4 37 surfaces, routing, one workspace | §2, §7.3 | Covered |
| §5.1 APP/PAT | §2, §7.1 | Covered |
| §5.2 REC | §7.2 | Covered |
| §5.3 DOC-01…04 | §7.3 | Covered |
| §5.4 NUR | §2, §7.3 | Covered (assigned tasks, no ward/ICU) |
| §5.5 PHA | §7.6 | Covered |
| §5.6 CAS | §7.7 | Covered |
| §5.7 MGR | §7.8 | Covered |
| §5.8 ADM | §7.8 | Covered |
| §6 F01–F16 field contracts | §6.2, §7 (referenced to source) | Covered as obligations; no backend claims |
| §7 workflows WF-01…07 | §7, §3.4 | Covered |
| §8 NFR-01…12 | §0.1, §3.3, §3.4, §5, §6.4 | Covered |
| §9 AT-01…26 + gates | §9 | Covered |
| §10 OpenDesign delivery instructions | §2, §8, §12 | Covered for implementation handoff |
| §11 source limitations | §0.1 | Covered |
| §12 v3 role changes V3-01…31 | §2, §7, §8 | Covered |
| §13 v3 semantics, V3-AT, handoff | §6, §9 | Covered |
| §14 PreClinic references | §10 R8 | Licensing boundary stated |

### 11.1 Non-functional obligations (NFR-01…12)

| ID | Obligation in this design | Where |
|---|---|---|
| NFR-01 | Server-authorised access on every call; UI permissions are UX only | §3.3, §3.5 |
| NFR-02 | Private medical data/photos by default; authorised file access, no public caching | §3.3 item 9, §7.4 |
| NFR-03 | No admin API key, card secret or processor credential in the browser; login and mutations protected at the proxy | §3.3, §6.4 |
| NFR-04 | No patient identifiers/notes in analytics, errors, URLs, console, toast logs | §3.3 item 11, §6.4 |
| NFR-05 | Duplicate clicks, timeout-after-success, stale versions, partial failure handled; durable reconciliation per enabled operation or the operation stays gated | §3.4, §6.5 |
| NFR-06 | Approved submit/cancel/amend workflows; no direct ledger or status mutation | §6.4 |
| NFR-07 | Photo original, annotation layer, comparison, consent and review traceable | §7.4 |
| NFR-08 | Validation in UI and again on server; no trust in client-computed values | §3.2, §6.4 |
| NFR-09 | Server pagination/filtering/aggregates; measured performance, no promises | §5.4 |
| NFR-10 | 1440/834/390 responsive; touch, keyboard, Thai UTF-8, 16 px forms, no hidden field | §5 |
| NFR-11 | Complete TH/EN; IDs and clinical content unchanged on switch | §4 |
| NFR-12 | Backups/restore/encryption/Thai legal acceptance documented separately; no automatic PDPA claim | §10 R-list, §11.2 |

### 11.2 Deliberate gaps (must be resolved before the affected wave or activation task)

1. Actual installed versions, apps, company, timezone, currency — unknown.
2. Role bundles and record-scope rules — undecided.
3. Consent model, photo/annotation schema, external lab entity, exam storage, queue entity — proposed only.
4. Locale persistence and print-language policy — undecided.
5. Real API routes/methods, required fields, transitions, side effects — unverified.
6. File limits, formats, and private-file access behaviour — unverified.
7. Demo dataset size and generation method — proposed, not built.
8. Production hosting/TLS/backup/privacy documentation — out of scope here; required separately (NFR-12).

---

## 12. Orchestrator validation checklist

The orchestrator owns validation. Suggested checks against this document, with the stated result expected:

| # | Check | Expected |
|---|---|---|
| V1 | Names all 37 surfaces with route and roles matching `Clinic_Screen_Manifest_v3.json` | Match |
| V2 | States the 97/97 legacy coverage and does not add or remove surfaces | Match |
| V3 | Covers all 31 v3 change IDs somewhere in scope/waves | Match |
| V4 | TH/EN requirements L10N-01…15 all addressed | 15/15 |
| V5 | Responsive targets 390/834/1440 plus 8 states per surface | Present |
| V6 | Same-origin proxy described with session/CSRF/no-secret rules | Present |
| V7 | Read vs verified-write gate defined, writes off by default | Present |
| V8 | Prohibited write patterns enumerated (direct status PUT, balance edits, card data) | Present |
| V9 | Design-source API scaffold explicitly marked unverified and not portable | Present |
| V10 | Site `10.0.100.10:8080` labelled HTTP evaluation only, camera/secure-cookie consequences noted | Present |
| V11 | Acceptance matrix references AT-01…25, V3-AT-01…13, P1 rows marked | Present |
| V12 | No claim that any test passed, endpoint exists, or field is verified | Must hold |
| V13 | No secrets, credentials or backend mutations introduced by this work | Must hold |
| V14 | UI/read delivery separated from write activation; blocked clinical task does not stop other screens | Present |
| V15 | Registration/edit, booking lifecycle/check-in, medication ordering and visit completion each mapped to a named activation task | 5 tasks named |
| V16 | Browser→proxy CSRF/origin protection covers login and all mutations, independent of upstream CSRF | Present |
| V17 | Per-operation durable reconciliation required (or operation gated); no speculative framework mandated | Present |
| V18 | Read-only release explicitly classified interim, not completed P0 | Present |
| V19 | Prototype translation keys reused; no mandatory fresh namespace or bidirectional reconciliation | Present |

**Current validation status:** not validated. No application code, no commits, no backend access, no secrets read, no site mutation performed while authoring this document.

---

## 13. Appendix — design source inventory used

| Source | Use in this document |
|---|---|
| `Clinic_Functional_Requirements_v3.md` | Scope, roles, L10N IDs, NFRs, AT matrix, gates |
| `Clinic_Screen_Manifest_v3.json` | 37 surfaces, routes, roles, states, 97 mappings, 31 changes |
| `Clinic_Requirements_v3_Change_Log.md` | V3-01…31 intent, P0/P1/P2 classification |
| `Clinic_Backend_Contract_Verification_v3.md` | BC3-01…18, required evidence per mutation |
| OpenDesign project `b741c393…` | Visual reference: 40 HTML entrypoints (37 surfaces + design system + states + launcher), token/stylesheet set, TH/EN dictionaries |
| `handoff/README.md`, `handoff/clinic-backend-gates.md` | `unverified_installed_site` status, G0–G7, D-01…D-22 |
| `handoff/clinic-backend-integration-audit.md` | Evidence that the prototype is `localStorage`-only with no network calls |
| `assets/js/frappe-api.js` | Unverified scaffold, rejected for reuse (see §0.1) |
| `DESIGN.md` / `brand-spec.md` | Token values, type scale, status colours, 44 px target, contrast rules |

Design-system note: the token set was reverse-engineered from a commercial template's public pages. Tokens and measured values may inform this implementation; its source code, images, screenshots, branding and copy may not be reused.
