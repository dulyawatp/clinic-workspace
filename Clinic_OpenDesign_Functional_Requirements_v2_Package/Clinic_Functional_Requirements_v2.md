# Demo Clinic — Functional Requirements Specification v2.0
## Design-first specification for a responsive Thai/English aesthetic clinic application

**Prepared:** 03 November 2026  
**Status:** READY FOR UX DESIGN / **NOT** A VERIFIED BACKEND SUBMISSION CONTRACT  
**Primary consumer:** OpenDesign (information architecture, visual design, prototypes, design handoff)  
**Implementation consumer:** OpenCode, **after** installed-site backend/schema verification  
**Backend of record:** existing Frappe Framework + Marley Health (Healthcare) + ERPNext; no replacement or core fork  
**Intended frontend:** React + TypeScript + Vite + responsive component system (e.g., shadcn/ui, Tailwind, Lucide), subject to engineering approval  
**Existing company:** `Demo Clinic` (`DC`); Thai locale, currency THB, time zone Asia/Bangkok.  
**Audience:** small/medium private aesthetic clinic, Thailand. Names, counts, figures in designs MUST be fictional, explicitly marked *Demo*.

> **Central principle:** Design the complete clinic capability, but expose it through a **small number of task-oriented screens**, not the 97 separate screen/state entries of v1. One patient visit has **one clinical workspace**. History and examination are entered **once**, not retyped in a second note. P0 includes Before/After comparison and image annotation, manual external-lab workflow, inventory, invoicing, and bilingual TH/EN UI.

---

## 0. Revision summary and authoritative boundaries

### Changes from v1

1. **Consolidated information architecture.** The 97 legacy screen/state IDs are mapped to 37 shared design surfaces, tabs, dialogs and states in `Clinic_Screen_Manifest_v2.json`. The legacy-to-v2 mapping must remain exhaustive; no feature is dropped merely because its page is merged.
2. **One canonical consultation workspace.** History, physical examination, structured Clinical Note, diagnoses, orders, photos, treatment, lab and summary are sections of **one visit** with a persistent patient header and common draft state. Underlying Marley documents remain separate when required.
3. **Elevated to P0:** before/after photo comparison, non-destructive shape/arrow/text annotation, and **manual** external-lab request → send → result upload → doctor review. Their supporting backend extensions are explicitly **unverified dependencies**, not falsely claimed native functionality.
4. **Retained cross-role P0:** appointments and check-in, patient management, pharmacy and batch/expiry, procurement/receiving, cash or credit-card invoicing/payment, owner dashboard and administrative setup. Payroll, CRM, marketing, loyalty, inpatient and patient portal are excluded.
5. **New P0 localization:** immediate **TH/EN language switcher**, per-user persistence, full UI translation, bilingual terminology/glossary, responsive Thai rendering and language-specific print behavior. No automatic translation of clinical content.
6. **Separated concerns:** this document defines **UX behavior and user-entered data**. `Clinic_Backend_Contract_Verification_v2.md` identifies backend checks to resolve before OpenCode is permitted to implement create/submit/cancel/stock/payment operations. A designable field does not imply a proven existing DocType field.
7. **More precise data lifecycle:** photos captured before check-in, consent evidence, encounter linking, save/resume, failed requests, note-signoff, audit/immutability and double-submit prevention have explicit criteria.

### Release classification

- **P0 — Required in the initial complete clinic application:** all core roles, patient journey and operations listed below, bilingual UX, responsive behavior, role boundaries, save/recovery. Some P0 features require custom frontend and may require a small backend extension; P0 means **required**, not **already available**.
- **P1 — Post-launch improvements:** advanced metrics, automation/alerts beyond manual workflow, staff workflow enhancements, optional nurse aftercare modules, richer reporting and export formats.
- **P2 / excluded:** payroll, commission, CRM, marketing, loyalty/membership, wards/inpatient, insurance claims, patient portal, telemedicine, automatic third-party lab API integration and online card processing unless separately commissioned.

### Capability labels

| Label | Meaning |
|---|---|
| `STANDARD` | Supported by known general Frappe/ERPNext/Marley concepts; exact installed fields/methods still require site verification |
| `CONFIG` | Depends on company/master records, roles, settings or workflow setup |
| `VERIFY` | Behavior may vary by installed version, enabled apps and controller implementation |
| `EXT` | Intended custom frontend and/or new Frappe DocType/approved method; **not** assumed present |
| `UI` | Presentation/interaction only, no new backend persistence unless specified |

### Principles

- **Clinic-first:** no backend DocType terminology in primary staff navigation (e.g. display *Clinical Note*, not *Patient Encounter*; *Medicine Stock*, not *Stock Ledger Entry*).
- **Icon-first, not icon-only:** 4–6 large labeled task tiles per role home, concise icons and illustrated process cues; every button retains readable text, accessible name and status text. Medical documentation remains precise structured text fields, not decorative graphics.
- **Minimal steps:** common task reachable in 1–2 navigation actions; default patient/visit/doctor/company from current context; no duplicate data entry.
- **Backend remains authoritative:** clinical and financial record lifecycle, stock, permissions and audit are executed/validated by Frappe/Marley/ERPNext server methods, never by client-only counters or status edits.
- **Privacy by design:** patient notes and photos require server-enforced access, private file storage, authorized downloads and traceable access; no patient content in analytics or browser console.

---

## 1. Users, responsibility, and navigation

| Role (code) | Core responsibilities | Main navigation (max 4–6 visible) | Can be combined? |
|---|---|---|---|
| Receptionist (`REC`) | Find/register patient, photos with permission, appointments, arrival/check-in, follow-up | Today, Patients, Appointments, Photos, Checkout | May act as cashier if authorized |
| Doctor (`DOC`) | My patients, one clinical workspace, history/exam, note, diagnoses, photos/annotation, lab, medications, treatments | My Day, Patients, Clinical Note, Photos, Orders | May take vitals in small clinic |
| Nurse / Treatment Assistant (`NUR`) | Assigned queue, vitals, photos if allowed, treatment prep, external-lab handoff, aftercare | Assigned Patients, Vitals, Photos, Tasks | Optional role; delegated work can be done by doctor |
| Pharmacy / Inventory (`PHA`) | On-hand stock, batches/expiry, receive, transfer, actual usage, purchase order/supplier | Inventory, Batches & Expiry, Receive, Transfer, Purchasing | May be same staff as receptionist if authorized |
| Cashier (`CAS`) | Unbilled/completed visits, sales invoices, receive cash/card payment, receipts, approved refunds | Pending Bills, Invoices, Payments, Receipts | May be receptionist if authorized |
| Owner / Manager (`MGR`) | Appointments, treatments, revenue, paid vs outstanding, stock, purchases, operational issues | Overview, Operations, Finance, Inventory, Reports | No need for system-admin rights |
| Administrator (`ADM`) | Users/roles, clinicians/schedules, services, rooms, warehouses, pricing, settings, audits | Setup, Staff & Roles, Clinic Masters, Inventory Setup, System Status | May use ERPNext Desk for rare advanced tasks |

**Access rules:** role-specific navigation never substitutes for backend authorization. Roles are permission bundles; a user may hold multiple roles. If a person holds two roles, allow only the authorized Workspaces and actions; no public "switch to administrator" escalation. Reception can see patient demographic/arrival information but no full confidential clinical note by default. Cashier can access billable references but not unnecessary clinical details. Doctor access follows approved patient-care relationship and server permissions, not just a frontend `practitioner` filter. Inventory users do not read clinical notes. Admin can manage setup but access to clinical content remains governed by an explicit policy.

---

## 2. Interaction, accessibility and responsive design system (P0)

### Design anatomy

- **Global shell:** clinic identity (Demo Clinic), current authorized role/location/company, searchable patient lookup (role controlled), notifications, **TH | EN language toggle**, profile/logout, connection status.
- **Role homepage:** 4–6 recognizable task tiles, Today's Work/Next Patients, 2–4 relevant KPIs (record-derived), optional minimal chart. Do not show unimplemented cards or inert buttons.
- **Patient context header:** full name, patient ID, date/time, appointment/encounter ID, care practitioner, visit state; sticky on long clinical screens; show privacy boundaries when shared device is used.
- **Forms:** short groups, helpful placeholders, bilingual labels, conditional fields, inline validation, sticky *Save Draft* and *Continue/Review*, visible last-saved status; confirmation on irreversible transitions.
- **Status representation:** text + icon + color (do not rely on red/green alone). Status terms map to verified server values; visual labels are translations, not new server statuses.
- **No duplicate entry:** demographics, appointment, practitioner, visit, allergies and prior notes load from authoritative records; if displayed in multiple sections, only one edit target exists.

### Breakpoints and usability

| Device | Target | Navigation / behavior |
|---|---|---|
| Desktop | 1280–1920px incl. 1440px design frame | Persistent/collapsible sidebar, patient queue table, charts, image comparison side-by-side |
| Tablet | 768–1279px incl. 834px | Collapsible nav, two-column action cards, doctor note and photo editor optimized for touch, no obstructing sticky controls |
| Phone | 360–767px incl. 390px | Drawer or bottom nav, one-column task cards, visit sections as stacked accordions, photo capture, quick check-in, no horizontally clipped form fields |

- Minimum interactive target approximately **44×44 CSS px**, keyboard navigation, visible focus, accessible labels, error summary, clear primary action; support screen readers where feasible.
- Avoid modal-on-modal interactions, long dashboard text and horizontal scrolling on clinical forms. Overflowing finance/inventory tables may use dedicated drill-down or responsive rows.
- Photos: device camera support via browser capability with upload fallback. Large photos lazy-loaded; loading, retry and offline failure states required; do not silently compress away clinically relevant details.
- All list views support pagination, search/filter, empty/loading/error states, and selection/open details; use seeded demo data substantial enough to populate charts and lists.

---

## 3. Thai/English localization and language switching — **P0**

**Requirement L10N-01:** A visible language selector **`ไทย | EN`** is available on the sign-in page, within the global header for all authenticated roles, and in the user profile. Switching is possible at any time, including during a draft clinical visit, without losing entered data or resetting the current workflow.

### Localization behavior

| ID | Required behavior |
|---|---|
| L10N-01 | One-tap/two-tap TH↔EN switch; currently selected locale clearly indicated; accessible from desktop, tablet and mobile navigation |
| L10N-02 | Switch **UI strings only**: navigation, tile titles, page headings, field labels/help/placeholder, buttons, date labels, validation/errors, empty/loading states, success messages, confirmations, status badges, chart labels, tooltips and admin settings |
| L10N-03 | Thai is the **default new-user UI language** for Demo Clinic; English available immediately. Final default may be changed by authorized admin; existing user's saved preference wins |
| L10N-04 | Locale is **saved per user** across sign-ins and devices via a permitted application preference or verified user-language method. On login, apply saved preference; for unauthenticated sign-in use current-session/device preference until authentication. Never persist language by mutating arbitrary User fields without permission |
| L10N-05 | In-progress drafts stay intact on switch; preserve focus where possible, active tab, selected patient, scroll, unsaved fields, upload state and filter/date context; show warning if a page genuinely requires reload |
| L10N-06 | Do **not** machine-translate or modify content authored by clinicians/patients: free-text notes, original lab PDFs, person names, allergy names entered verbatim, provider names, item codes, attachment files, record IDs. Optional localized display names require explicit master data support |
| L10N-07 | Stable backend value keys / document IDs / API payloads remain locale-independent. Labels shown to users can translate `Draft`, `Completed`, `Cancelled`, while submitted enum/link values use actual backend values |
| L10N-08 | TH translation is complete for all P0 screens and error states at release; no placeholder English in Thai navigation. Unknown keys fall back to English and are logged in a translation QA report (without patient data) |
| L10N-09 | Thai and English text render in a legible licensed/installed Thai-capable font stack; avoid glyph clipping, broken line heights, cramped buttons. Do not embed or distribute proprietary fonts without authorization |
| L10N-10 | Date/time/currency: display Asia/Bangkok 24-hour time, THB `฿` with locale-appropriate grouping; choose consistent Thai UI date pattern (e.g. `03/11/2026` or `3 พ.ย. 2026`); API uses ISO dates/times and backend canonical numeric values. **Do not automatically switch Gregorian↔Buddhist year without approved clinic policy** |
| L10N-11 | Search accepts Thai and English, Unicode names, mixed Thai/Latin strings and common whitespace; no forced transliteration. Do not rely on English-only case transformations for Thai names |
| L10N-12 | Patient-facing and administrative print/export templates (receipt, invoice, treatment summary, lab request) define explicit selectable/default output language; rendering does **not** automatically translate user-authored medical content or original attachments. Accounting/statutory print wording and tax layout require separate Thai review |
| L10N-13 | All content and number formats stay consistent across role switch and logout/login; ensure same record and actual backend counts regardless of language |
| L10N-14 | Translate system-generated safe notifications and standard messages; do not rely on browser `Accept-Language` alone for logged-in Frappe API errors. Error code maps to local strings with safe server-message fallback |
| L10N-15 | Medical terminology translation reviewed by a Thai-speaking clinical stakeholder before release; provide short bilingual glossary and disambiguate words such as "Encounter", "Procedure", "Batch", "Clinical Note", "Visit" |

### Recommended label glossary (initial translations; clinical stakeholder to approve)

| English key / UI label | Thai label |
|---|---|
| Dashboard | ภาพรวม |
| Register Patient | ลงทะเบียนผู้ป่วย |
| Patient Search | ค้นหาผู้ป่วย |
| Appointment | นัดหมาย |
| Book Appointment | จองนัดหมาย |
| Today's Patients | ผู้ป่วยวันนี้ |
| Check In | รับเข้าตรวจ |
| Clinical Note | บันทึกการตรวจ (Clinical Note) |
| Medical History | ประวัติทางการแพทย์ |
| Physical Examination | การตรวจร่างกาย |
| Vital Signs | สัญญาณชีพ |
| Before Photos | รูปก่อนทำหัตถการ |
| After Photos | รูปหลังทำหัตถการ |
| Compare Photos | เปรียบเทียบรูปก่อนและหลัง |
| Annotate Photo | วาดกำกับบนรูป |
| External Lab | แล็บภายนอก |
| Lab Result | ผลตรวจทางห้องปฏิบัติการ |
| Treatment / Procedure | การรักษา / หัตถการ |
| Inventory | คลังเวชภัณฑ์ |
| Stock Balance | ยอดคงเหลือคลังสินค้า |
| Batch / Lot | ล็อตสินค้า (Batch/Lot) |
| Expiry Date | วันหมดอายุ |
| Receive Stock | รับสินค้าเข้าคลัง |
| Stock Transfer | โอนย้ายสินค้า |
| Sales Invoice | ใบแจ้งหนี้ / ใบขาย |
| Payment | รับชำระเงิน |
| Cash | เงินสด |
| Credit Card | บัตรเครดิต |
| Save Draft | บันทึกฉบับร่าง |
| Submit / Confirm | ยืนยันรายการ |
| Cancel | ยกเลิก |
| Settings | ตั้งค่า |
| English | English |
| Thai | ไทย |

**Localization assets for OpenDesign:** translate UI copy inventory; provide TH and EN text variants for every frame; test long labels; define date/number formatting examples, bilingual print examples and translation-ready design tokens. For OpenCode, choose a dedicated React localization library such as `i18next`/`react-i18next`; distinguish its UI locale from Frappe's `User.language`. Frappe supports its own translations and configurable user language, but synchronization between the custom React locale and backend must be verified on site.

---

## 4. Information architecture: consolidated, complete coverage

The companion `Clinic_Screen_Manifest_v2.json` contains **37 design surfaces** and an exhaustive `legacy_coverage` map to the 97 entries of v1. **Surface** means a reusable page or a complete task panel; nested tabs/drawers and states are required where indicated. It does NOT mean only 37 backend operations.

| Design surfaces | Role | Main page and contained states (no separate top-level page unless UX testing justifies it) |
|---|---|---|
| APP-01–APP-04 | Shared | Authentication; shell/home/profile/notifications; global search; print/exports/system states |
| PAT-01–PAT-05 | Permitted roles | Patient directory; create/edit; profile/history/timeline; photo gallery/capture; photo annotation/comparison |
| REC-01–REC-05 | Reception | Homepage; today's queue + check-in; calendar; appointment create/detail/reschedule/cancel; checkout/follow-up |
| DOC-01–DOC-04 | Doctor | My Day and recent encounters; **ONE Visit / Clinical Note workspace** with all tabs; procedure completion; lab request/result detail |
| NUR-01–NUR-02 | Nurse/assistant | Assigned tasks; vitals, prep and aftercare tabs |
| PHA-01–PHA-07 | Pharmacy | Home; item catalog; batches/expiry; stock balance/ledger; receiving; movement/usage/reconciliation; purchasing/suppliers |
| CAS-01–CAS-03 | Cashier | Home/pending bills; invoices; payment/receipt/refund states |
| MGR-01–MGR-02 | Owner/manager | Management homepage; operational/finance/stock/purchasing report tabs |
| ADM-01–ADM-05 | Admin | Setup dashboard; users/roles; clinical masters; inventory/pricing/system settings; audit/integration status |

### Mandatory navigation/routing rules

- Staff navigation shows **only the role's task surfaces**, with persistent cross-links to shared patient search/profile when permitted.
- Each record detail is deep-linkable and has breadcrumb/back; a modal may be used for quick actions, but any task requiring lengthy clinical entry should have a stable deep-link URL.
- One patient identity and one active visit context are preserved across Clinical Note tabs and photo/lab/procedure dialogs.
- A tab counts as complete only when OpenDesign describes its fields, primary/secondary actions, permission and empty/error states. Hidden tabs cannot be used to claim coverage.
- Use only one **Doctor Visit Workspace**: History, Examination/Vitals, Clinical Note, Diagnoses, Photos/Annotation, External Lab, Medications, Procedures, Review & Complete. Sections are **not** independent duplicate patient records.

---

## 5. Role-specific functional requirements and screen contracts

### 5.1 Shared patient and application functions

| ID | Surface | Functional requirements (P0 unless stated) | Primary actions / variants |
|---|---|---|---|
| APP-01 | Sign in / session | Authorized sign-in (reuse Frappe session), safe logout/session-expired recovery; TH/EN toggle before login | Sign In, Change Language, Forgot Credentials if supported, Session Expired |
| APP-02 | App shell and profile | Role-based home, correct company and user, language preference, notifications, error/connection messages | Profile, Language Toggle, Logout, Notifications |
| APP-03 | Global search | Authorized patient/appointment search; safe display of minimal identifying information; paginate and protect clinical content | Search, Filter, Open Patient/Appointment |
| APP-04 | Print and global system states | Authorized print/export, loading, empty, network failure, 403/expired login, unsaved conflict, double submit prevention | Print, Retry, Reauthenticate, Keep Draft, Confirm Cancel |
| PAT-01 | Patient directory | Search by name, mobile, patient ID, optional approved identity number; duplicate candidates; typeahead and pagination | Search, Register New, Open Patient |
| PAT-02 | Patient registration / edit | Demographics, contact, identity and consent-related data (see F01); validate before create and avoid duplicate patients | Register, Save Changes, Cancel |
| PAT-03 | Patient profile/history | Persistent identity header, visits, medical history, appointments, photo links; clinical details permission-filtered | View History, Book Visit, View Photos |
| PAT-04 | Photo gallery/capture | Private original photos, taken before or after check-in, category/body area/date, visit linking, consent, thumbnails/camera/upload; authorized deletion policy | Capture, Upload, Open, Tag, Link to Visit |
| PAT-05 | Before/After comparison and annotation | **P0** select comparable photos by visit/body area; side-by-side and slider; arrows/circles/rectangles/text/freehand where agreed; non-destructive save, versions, audit | Compare, Draw, Undo/Redo, Save Annotation, Reset View |

### 5.2 Receptionist (REC)

| ID | Surface | Required behavior | Important exceptions |
|---|---|---|---|
| REC-01 | Reception home | Four to six visual tiles: Register Patient, Book Appointment, Today, Take Photos, Find Patient, Checkout (authorized) | No finance/medical-note leakage |
| REC-02 | Today's queue + check-in | Appointment lists by time, doctor and status, quick search, patient preview, appointment open; check-in through verified backend workflow; triage/waiting indicator as supported | Walk-in, late arrival, already checked in, cancelled/no-show, backend unsupported queue |
| REC-03 | Calendar | Day/week doctor availability, filter practitioner/date/type, conflicts and blocked slots based on verified server | Busy/full day, invalid schedule, reschedule |
| REC-04 | Appointment workflow | Create, view, edit/reschedule, cancel with reason, navigate to patient and clinic record; patient/type/company/date/doctor required conditionally | Overlap, status conflict, stale appointment, prevent duplicates |
| REC-05 | Checkout & follow-up | Open visit/billing handoff, pending bill indicator, refer to cashier or authorized payment flow, book return visit | Incomplete consultation, payment unpaid, returning patient |

### 5.3 Doctor (DOC) — one canonical clinical workspace

| ID | Surface | Required behavior | Important exceptions |
|---|---|---|---|
| DOC-01 | My Day / visit queue | Only authorized appointments, draft notes, pending results, follow-up; quick open patient/visit, not raw ERP DocTypes | Doctor unavailable, unassigned case, after-hours |
| DOC-02 | **Current Visit / Clinical Note workspace** | **Single page with tabbed/stepper sections** listed below; patient and visit context always visible; cross-section Save Draft and Review/Complete | Walk-in, missing appointment, connection loss, invalid note, partial save, prior history readonly |
| DOC-03 | Procedure detail/complete | Clinical procedure, doctor, room, actual used items, batch, stock warehouse, safe complete/consume; show resulting linked stock reference | Low/expired stock, partial vial/units policy, double submit, warehouse missing |
| DOC-04 | Lab order/result detail | Native lab requests/results and **manual external lab** request tracking as permitted; review with attached report evidence | Awaiting results, external reference missing, reviewer permission |

**DOC-02 persistent sections (one workspace, not multiple independent workflows):**

1. **Overview:** current patient, visit date/time, appointment, allergies alert, prior visit timeline, assigned clinician. Read previous history; do not duplicate it into writable fields.
2. **History:** chief complaint, history of present illness, relevant past/surgical/family history, allergies, medications, relevant patient context; edit only the authoritative current-visit note/observation record. Show prior history read-only and reference it instead of copying.
3. **Examination / Vitals:** vitals observations with units; structured physical exam findings, aesthetic body area/site, examination details, attachments if any. Backend target for free-text exam is **TBD/extension if no valid native field**; never write into Marley layout/display fields.
4. **Clinical Note:** structured sections for assessment, plan, treatment rationale, follow-up and overall signed note; create or update linked `Clinical Note` as verified. Avoid a **second editable copy** of History/Examination. Review displays composed information from the same source.
5. **Photos:** current/past Before and After galleries, categorized body area/viewpoint/date; capture/upload, draw shapes and text, compare across visits, preserve originals, patient consent.
6. **External Lab:** select requested test, external provider, order/send state, sample reference, receive file/report, physician review, interpretation and timestamp. Do **not** pretend native Lab Test already supports all outside-provider states.
7. **Medication / Procedures:** prescribe medication with dose/duration, record requested procedure, actual treatment and stock use with approvals. These actions are distinct from dispensing, invoicing or stock issuing until the backend workflow connects them.
8. **Review & Complete:** summarize entered data, missing required fields, linked files/lab requests/procedures, draft timestamps and side effects; show exactly which documents/actions will be submitted. Allow a safe Save Draft before final submission; do not mark a document signed just because it was saved.

**Canonical data rule:** each editable clinical fact has **one authoritative backend location**. Tabs may share/reuse it; they may never require clinician to re-enter the same history/exam text as a separate full Clinical Note. If clinician edits a section from the summary, return to the single corresponding field. The final note can render a read-only composed view; whether the system stores a denormalized signed snapshot requires approved policy and backend design.

### 5.4 Nurse / Treatment Assistant (NUR)

- **NUR-01:** Assigned patients/tasks and upcoming procedures; status shows responsible doctor and urgency without inappropriate clinical disclosure.
- **NUR-02:** Vitals editor (units and required patient/date/time), permitted intake photo capture, treatment-prep checklist (room, equipment, supplies), approved external lab dispatch/result upload, aftercare note. Permission-dependent; if the clinic has no nurse, roles and screens are assigned to doctor, not dropped.

### 5.5 Pharmacy / Inventory (PHA)

| ID | Surface | Required behavior |
|---|---|---|
| PHA-01 | Pharmacy home | Large tiles: Stock Balance, Batch & Expiry, Receive Stock, Transfer, Purchasing, Usage; alerts for low/near-expiry with positive on-hand balance |
| PHA-02 | Items | Search, item details, group, stock/service distinction, tracking mode, UOM, preferred warehouse; owner/master editing permission scoped |
| PHA-03 | Batches & expiry | Item, lot, expiry, warehouse, usable quantity, expiring-within filters; receive-linked batch creation; expired stock visually blocked from normal use |
| PHA-04 | Balances & stock ledger | Warehouse/batch filters; balance and chronological immutable movements linked to source transactions; export by permission |
| PHA-05 | Receive stock | Supplier/PO (if linked), item/qty/UOM/warehouse/rate/batch/expiry, preview and submit Purchase Receipt safely |
| PHA-06 | Stock operations | Warehouse transfer, authorized issue/usage, treatment-usage link, restricted reconciliation; show before/after balance and audit. Reconciliation requires its own backend DocType/method |
| PHA-07 | Purchasing & suppliers | Supplier search/detail, Purchase Orders create/list/submit, partial receipts, replenishment status, overdue and pending quantities |

**Policy:** stock consumption occurs exactly once through server-controlled transaction(s). Batch/expiry selection uses the installed ERPNext's actual batch/Serial and Batch Bundle workflow. “Prescription” is not automatically equal to “Dispensed”. Any Botox fractional-vial logic, wastage or dose-to-vial conversion requires verified units and policy before declaring automatic consumption.

### 5.6 Cashier (CAS)

- **CAS-01:** Unbilled/completed patient visits and pending invoice queue, with essential patient billing ID and linked service but not unnecessary medical history.
- **CAS-02:** Draft/submit Sales Invoice: linked accounting customer, service/items, quantity, pricing, discount (if allowed), tax and total preview. Show existing linked charges to avoid double billing; permission-restricted void/amend.
- **CAS-03:** Receive cash or credit card **payment method** using verified Payment Entry workflow; full/partial payment where supported, receipt/print, authorized return/refund review. **Never handle or store PAN, CVV or other payment-card secrets.**

### 5.7 Clinic Owner / Manager (MGR)

- **MGR-01:** Visual overview with appointment count/status, patients seen, procedures, **invoiced revenue vs cash collected**, outstanding, inventory value and near-expiry positive stock. Date filters; chart click opens verifiable details.
- **MGR-02:** Tabs for appointments/doctor workload, treatment frequency, finance, medicine/inventory, purchasing, reports/exports. Show source date range, denominator and business meaning for each metric. No fabricated KPI values in production.
- **Dashboards must be populated** in the seeded demo with sufficient coherent records, not one record per scene. Suggested demo scale: 180 fictional patients, 540 appointments, 300 encounters, 170 procedures, 220 invoices, 24 stock items, 45 batches, 24 POs, 18 receipts, 80–120 movements. Use smaller safe scale if system capacity requires, and reconcile each KPI with submitted source records.

### 5.8 Administrator (ADM)

- **ADM-01:** Setup checklist, readiness warnings, doctor schedules, unconfigured payment/stock settings; only authorized status.
- **ADM-02:** Users/roles, role profiles, practitioner↔user linkage, per-role access test, default role/locale; prohibit implicit admin privilege escalation.
- **ADM-03:** Clinician/practitioner records, schedules, service units/rooms, appointment types/durations, procedure templates, lab templates, settings; forms based on installed master schema.
- **ADM-04:** Items, warehouses, price lists, cash/card modes and company accounts, batch/stock settings, clinic-level UX configuration and TH/EN default language; advanced ERPNext Desk may be linked, not embedded wholesale.
- **ADM-05:** Integration/metadata verification status, error log with sensitive data redacted, clinical/financial audit trail, backup/readiness indicators if available. Display verified actual health status only, never fake “all systems healthy.”

---
## 6. Complete form inputs and submission requirements (UX contract)

**Classification** for the tables below: `R` = required by this clinic UX; `C` = required when condition applies; `O` = optional; `A` = derived/autofilled, displayed where useful; `X` = extension requiring backend verification/implementation. **This is NOT a claim about `DocField.reqd`.** The actual backend `reqd`, controller validation, accepted options, field types, default values, child tables and authorization must be checked before OpenCode implements submissions.

**Every create/edit interface must provide:** editable and disabled states, field label in TH/EN, required/conditional marker, source/default, inline validation, save/draft where supported, submit/confirm where supported, error message, entity link, and successful backend record ID. Avoid displaying technical `DocType.fieldname` to end users; use it in hidden developer annotations.

### F01 — Patient search / register / edit (`PAT-01`, `PAT-02`)

| Visible field | UX | Candidate backend or new entity | Behavior and validations |
|---|---|---|---|
| Patient ID | A | `Patient.name` | Generated record ID; never invent on client |
| First name / ชื่อ | R | `Patient.first_name` | Thai/English Unicode; trim only safe whitespace |
| Last name / นามสกุล | R (clinic policy) | `Patient.last_name` | Source may not mark mandatory; configurable clinic policy |
| Gender / เพศ | R | `Patient.sex` | Link to valid configured `Gender`, not arbitrary translated string |
| Birth date / วันเกิด | R (clinic policy) | `Patient.dob` | Valid calendar date; date-only backend format |
| Mobile / เบอร์โทรศัพท์ | R (clinic policy) | `Patient.mobile` (verify) | Thai phone format; no fabricated country code; duplicate search |
| Identity type | C (clinic decision) | Custom/verified patient identity field | Thai ID or passport; only collect if necessary and privacy/legal basis confirmed |
| Identity number | C | Verified/custom restricted identity field | Validation differs by type, mask access, never show in queues or URLs |
| Email / อีเมล | O | `Patient.email` | Validate if entered |
| Address / ที่อยู่ | C (clinic policy) | Verified patient/contact/address link or extension | Do not assume one text field in Patient |
| Emergency contact | C (clinic policy) | Verified Contact or extension | Name, relationship, phone, restricted visibility |
| Allergies / แพ้ยา | R as known/unknown status | `Patient.allergies` or structured record | Explicit `Known`, `None reported`, `Unknown`; avoid equating blank to no allergy |
| Medical/surgical history | O at reception, R review by doctor | Existing Patient history / clinician note | Editable owner per clinical policy; no duplicate fields |
| Consent reference | C | Clinic Consent record/verified existing evidence (`X`) | Scope, version, signer/date, capture method and revocation where applicable |
| Profile photo | O | `Patient.image` or private File | Distinguish portrait from clinical before/after photos |
| Accounting customer | A/C | `Patient.customer` / verified mapping | UI does not invent Customer record; auto-link behavior verified |

**Actions:** `Search for existing patient` → warn on likely match (name+phone+DOB where known) → `Register` or `Open Existing`; on successful save show Patient ID and next actions `Take Photos`, `Book Appointment`, `Open Record`. Identity collection and photo permission are distinct consent/policy decisions, not universal legal facts.

### F02 — Appointment / reschedule / cancel (`REC-03`, `REC-04`)

| Visible field | UX | Candidate backend | Behavior |
|---|---|---|---|
| Patient | R | `Patient Appointment.patient` | Existing Patient ID; no re-entry of demographics |
| Appointment type | R | `.appointment_type` | Master data selection |
| Appointment for / resource type | A/C | `.appointment_for` | Type-dependent value, not free-text |
| Doctor / practitioner | C | `.practitioner` | Required for practitioner appointment |
| Department / treatment room | C | `.department`, `.service_unit` | Based on type/availability |
| Date | R | `.appointment_date` | Clinic-local, ensure server slot check |
| Time / slot | C/R timed visits | `.appointment_time` | From validated availability; timezone aware |
| Duration | A/C | `.duration` | From type/schedule; no overlapping allowed unless policy explicitly permits |
| Company | A/R backend | `.company` | Demo Clinic's actual company ID |
| Notes | O | `.notes` | No confidential diagnostic text in public schedule/queue |
| Reschedule/cancel reason | C | Verified comment/workflow field or `X` | Persist reason/auditable actor; user cannot directly set readonly status |
| Status | A | `.status` | Display actual server status via localized label |

**Actions:** Book, Review & Confirm, Reschedule, Cancel, Mark No Show (authorized), Follow-up. Show conflicting slots and suggested alternatives only if backed by actual availability checks. Idempotent behavior for repeat clicks.

### F03 — Intake / Before / After photo + annotation (`PAT-04`, `PAT-05`, `DOC-02`)

| Visible field/action | UX | Backend target candidate | Notes |
|---|---|---|---|
| Patient ID | R | `Patient.name` reference | Always required; may exist before appointment/check-in |
| Appointment/visit | C | `Patient Appointment.name`, `Patient Encounter.name` | At intake link to appointment or pre-encounter context; associate later encounter without duplicate upload |
| Consent scope & evidence | R by clinic photo policy | `Clinic Consent` **X/VERIFY** | Link policy version, capture actor, timestamp, authorized permission/revocation action |
| Photo category | R | `Clinic Photo.photo_type` **X** | `Before`, `After`, `Profile`, `Clinical` or config-defined values |
| Body area / viewpoint | R for before/after | `Clinic Photo.body_area`, `.viewpoint` **X** | Configurable face/profile/left/right, standardized comparison |
| Date / photographer | A | `captured_at`, authenticated user **X** | Store server/UTC canonical timestamp and display clinic-local |
| Original file | R | Frappe `File` (private) | Supported formats/limits discovered on site; validate size/type and upload progress |
| File association | R | `File.attached_to_doctype` / `.attached_to_name` or approved binding API | Link Patient immediately; link encounter when available; no orphan records |
| Annotation tool | R/P0 | `Clinic Photo Annotation` **X** | Arrow, ellipse, rectangle, label/text; zoom/pan, undo/redo, select/delete/resize shape |
| Annotation persistence | R/P0 | Versioned JSON/vector layer + original file reference **X** | Preserve original, save draft/confirmed, render flattened preview only as derived artifact |
| Compare before/after | R/P0 | `Clinic Photo.pair_id` / visit/body area association **X** | Side-by-side and/or slider; compare across visits, same region/viewpoint, dates shown |
| Delete/replace | C | Approved file/photo lifecycle API | Restricted, audited, respect retention/consent; never destructive overwrite of signed clinical original |

**Critical photo lifecycle:** registration → private original uploaded with Patient link **before check-in** → optional appointment/pre-visit association → check-in creates/updates visit → backend links existing originals to the new encounter **without reupload** → doctor annotates/can compare → audit versions. If visit creation fails, photos remain safely attached to Patient/intake context and are recoverable. If consent cannot be established, block restricted photo capture under clinic policy and display a specific remedy, not a generic error.

### F04 — Check In, patient queue and checkout (`REC-02`, `REC-05`)

| Input | UX | Target/status |
|---|---|---|
| Patient Appointment ID | R | Actual `Patient Appointment.name`, lookup patient/doctor/date/type |
| Arrival timestamp/actor | A | Server recorded, never trusted browser-only |
| Queue / location | C | Installed native queue, appointment workflow or **X** fallback |
| Arrival status | A | Verified server status/action (not arbitrary update) |
| Notes/reason | O | Approved comment field if available |
| Checkout billing link | C | Invoice/visit link; not direct edit of appointment's readonly status |

One visible Check In action per eligible appointment; disabled for invalid state; double-click safe. Queue should show Waiting / With Doctor / Completed only if the installed workflow or approved custom backend supports these states. Design must not assume `marley_frontend` queue DocTypes exist.

### F05 — Clinical visit / one guided note (`DOC-02`) and vitals (`NUR-02`)

| Visible field / section | UX | Candidate backend | Critical condition |
|---|---|---|---|
| Patient | R/A | `Patient Encounter.patient` | Linked existing Patient |
| Practitioner | R/A | `.practitioner` | Authorized Healthcare Practitioner |
| Appointment type | R/A | `.appointment_type` | Verified valid configured type |
| Encounter date/time | R/A | `.encounter_date`, `.encounter_time` | Server/default clinic-local conversion |
| Appointment reference | C/A | `.appointment` | May be absent for walk-in |
| Complaint / symptoms | R clinic policy | Encounter symptoms child or structured Clinical Note | One edit target; don't write same fact twice |
| HPI / past/surgical history | R review, entries C | Clinical Note sections and patient source data | Existing history shown readonly; current visit findings one authoritative edit |
| Allergies/medications | R review | Patient/clinical record; review confirmation **X** if needed | Unknown vs none; audit updates |
| Vitals | C (when measured) | `Vital Signs.patient`, `.signs_date`, `.signs_time` and measured values | Per-measurement units, meaningful range; missing ≠ normal |
| Physical examination | R for visit | Structured Clinical Note section or approved `Clinic Exam` **X** | Do **not** submit free text to `Patient Encounter.physical_examination` layout field |
| Assessment/diagnoses | R clinic policy | Clinical Note + Encounter diagnosis child | Coded diagnosis optional depending procedure/policy |
| Clinical Note content | R before final completion | `Clinical Note.note` (verify) | Reuse history/exam source, no redundant second editor |
| Note/encounter reference | A | `Clinical Note.reference_doc`, `.reference_name` candidate | Source read-only; set via verified server method, not client force-update |
| Procedure/medication/lab orders | C | Encounter child tables and resulting requests | Validate actual row and submission semantics |
| Save Draft | R | Approved drafts method / document lifecycle | Persist progress in recoverable record(s), return ID/version/time |
| Complete/Submit | R | Verified server method(s) | Review dialog, identify generated orders, exactly once |

**Vitals candidate fields:** `patient`, `signs_date`, `signs_time`, `temperature`, `pulse`, `respiratory_rate`, `bp_systolic`, `bp_diastolic`, `height`, `weight`, `vital_signs_note`. Verify exact installed schema and units.

**Draft persistence and recovery:** Clinical workspace treats multiple underlying records as a **single visible editing session**. Saves may touch several DocTypes; report per-section persistence/last saved and never claim global success when one failed. Retry idempotently; preserve unsaved data through temporary UI state and, where authorized, a server-backed draft. On reload, resume from persisted source of truth; when a network timeout occurs after submission, query by request key/record ID before trying again. No automatic submission of medications/procedures on a generic Save Draft unless backend specifically does so and the UI warns users.

### F06 — Medication orders (`DOC-02`)

| UI field | UX | Candidate target |
|---|---|---|
| Medication/item | R per row | `Drug Prescription.medication` or `.drug_code` — version-dependent |
| Dosage | R | `.dosage` or interval fields per actual mode |
| Duration/period | R | `.period` — confirm DocType |
| Dose form and route | C/R | `.dosage_form`, other installed fields |
| Frequency / interval | C | `.interval` or schedule fields |
| Qty/repeats | C | `Medication Request.quantity`, repeat fields |
| Instructions | O/R clinic policy | `.comment` / request description |

**Important:** ordered/prescribed ≠ dispensed ≠ consumed ≠ invoiced. Show separate status if operationally distinct. No automated drug recommendation or inferred dosing in UX.

### F07 — Procedure and consumables (`DOC-03`, `PHA-06`)

| UI field | UX | Candidate target |
|---|---|---|
| Patient | R | `Clinical Procedure.patient` |
| Procedure template | R | `.procedure_template` |
| Practitioner | C/R | `.practitioner` |
| Appointment/service request | C | `.appointment`, service request as supported |
| Room / service unit | C/R | `.service_unit` |
| Actual start date/time | C/R | `.start_date`, `.start_time` |
| Consume stock toggle | C | `.consume_stock` |
| Stock warehouse | R if consuming | `.warehouse` |
| Consumable lines | R if consuming | `Clinical Procedure Item` rows: `item_code`, `qty > 0`, `uom` |
| Batch / lot | C/R for batched item | batch identifier or Serial and Batch Bundle as installed |
| Procedure notes | O | `.notes` / verified related note |
| Status/resulting stock reference | A | Correct server workflow and Stock Entry/ledger link |

**Required UI:** Preview exact items, quantities, selected batches, expiry, source warehouse and available stock; warn/block insufficient/expired stock appropriately; `Complete Treatment` requires explicit confirmation and posts **one** backend transaction set. Display actual created IDs on success; never directly write to stock ledger/Batch count.

### F08 — Native laboratory and **manual external lab** (`DOC-02`, `DOC-04`)

| UI field | UX | Candidate target |
|---|---|---|
| Patient, encounter, ordering doctor | R/A | Existing Patient/Encounter/Healthcare Practitioner references |
| Requested test | R | Native `Lab Prescription` / `Lab Test` where supported, or `Clinic External Lab Request` **X** |
| Lab destination | R for external | External provider master/controlled text **X** |
| Order date/time | A | Server timestamp |
| Sample collected/sent date | C | **X** |
| External request/ref number | O/C | **X** |
| Requested by / sent by / received by | A | authenticated actor in transition audit |
| Status | R | `Ordered` → `Sent` → `Received` → `Reviewed`; or `Cancelled` via server-authorized action **X** |
| Result file | R to mark Received | private Frappe File + external lab request ID **X** |
| Review interpretation | R to mark Reviewed | doctor comment/clinical note/link **X** |
| Reviewed by/at | A | server-attested practitioner/time |

**Ownership matrix:** doctor orders/clinically interprets; nurse or authorized reception dispatches sample and uploads incoming report; doctor reviews and acknowledges; administrator configures external lab providers; owner sees counts/status but not unnecessary result content. Do not claim automated external-lab API integration. Native on-site Lab Test behavior must be verified; otherwise use an explicit extension record with this workflow.

### F09 — Inventory item, batch and expiry (`PHA-02`, `PHA-03`)

| Field | UX | Candidate target |
|---|---|---|
| Item code/name | R | `Item.item_code`, `.item_name` |
| Item group and UOM | R | `.item_group`, `.stock_uom` |
| Stock vs non-stock service | R | `.is_stock_item` |
| Batch tracking | R for traceable medicines | `.has_batch_no` and ERPNext stock settings |
| Batch ID, item | R | `Batch.batch_id`, `.item` |
| Expiry date | R clinic policy for expiring medicine | `Batch.expiry_date` |
| Quantity on hand by batch/warehouse | A | Backend Stock Balance/ledger aggregate, never hard-coded on Batch master |
| Reorder min/level | C | item reorder configuration |

Expired products must be distinguishable from soon-to-expire; reports should count only applicable positive usable quantity at the selected warehouse, and should disclose whether FEFO selection was manual or verified automatic behavior.

### F10 — Receive stock / Purchase Receipt (`PHA-05`)

Supplier `R`, Company `R/A`, PO reference `C`, posting date/time `R/A`, one or more lines `R`; each line `item_code`, `qty > 0`, `uom`, target warehouse, price/rate as required, batch assignment or new batch and expiry for batched medicine. Candidate backend `Purchase Receipt` and item child table; exact batch/Serial and Batch Bundle workflow **VERIFY**. Preview totals and stock effect; submit once, return receipt ID.

### F11 — Stock transfer / issue / reconcile (`PHA-06`)

Stock movement kind `R` (transfer/issue), company `R/A`, source warehouse `R`, target warehouse `R for transfer`, items `R` (item/qty/UOM), batch/bundle `C`, related procedure/reference `C`. Candidate backend `Stock Entry` and child items, but **Stock Reconciliation is a separate restricted DocType/workflow**. Preview stock impact, reject unauthorized negative issue and use exact backend submit method.

### F12 — Purchase order / supplier (`PHA-07`)

Supplier `R`, company `R/A`, transaction date `R/A`, required/delivery date `R/C`, at least one item line with item, quantity, UOM, rate, target warehouse as applicable; candidate `Purchase Order`. Supplier master requires legally/business-approved fields based on configured accounting/tax setup. Support draft/submitted/partly received/closed according to installed statuses and actual linked receipts.

### F13 — Invoice / pricing (`CAS-02`)

Patient reference `R`, **accounting Customer link `R/A`**, company `R/A`, date `R/A`, at least one billable service/product line `R` (item, qty, price), discounts `C` by permission, taxes `C` from policy, due date `C`, visit/procedure link `R` by clinic business rule, total preview `A`. Candidate `Sales Invoice` and `Sales Invoice Item`. Distinguish Treatment Expense vs Invoice Items; if treatment consumes stock separately, ensure invoice doesn't **also** deduct that same consumable unintentionally. Show billable source documents already invoiced, no duplicate charges.

### F14 — Payment / receipt / refund (`CAS-03`)

Invoice reference `R`, party/customer `A`, payment type Receive `A`, company `A`, payment date `R/A`, mode `R` (Cash or Credit Card), paid amount `R > 0`, account mapping and received currency `C`, invoice allocation `R`, references `A`, remaining balance `A`. Candidate `Payment Entry` and references lines. Prevent over-allocation or repeat payment without authorized advance handling. Receipt printing only after confirmed post. Refund/return requires distinct approved document lifecycle. Raw credit-card numbers and CVV are **never collected**.

### F15 — Admin master forms (`ADM-02`–`ADM-04`)

- **Users/Roles:** user identifier/login (Frappe User), display name, email as auth permits, enabled, role assignments, linked practitioner for doctors, initial language, role access test; no passwords in application logs.
- **Practitioners/Schedules:** doctor name, linked Frappe User, department, schedule name, day/time slots, exceptions, service unit, booking constraints; actual DocTypes/links **VERIFY**.
- **Appointment Types and Procedure Templates:** name, category, duration, pricing/service item, default requirements/consumables and instructions as installed; cannot silently assume template availability.
- **Rooms/Warehouses:** service unit tree, non-root actual treatment room; stock warehouse mapping and restrictions. Do not attempt to edit read-only root Healthcare Service Unit as a treatment room.
- **Item/Batch/Price/Payment configuration:** item group, UOM, batch tracking, valuation/default warehouses, price list, Cash/Card mode and actual account mapping; required accounting defaults must exist before cashier testing.
- **Language default and translations:** Thai default, English option, user-level preference, glossary approvals, printable template language selection; only authorized admins can change clinic default.
- **Audit/Integration:** installed version reporting, failed API metadata checks, permission matrix status, customization flags, no patient data in logs.

### F16 — Shared states for every form

- Initial loading / skeleton; empty or no permissions; missing dependent master records; partially completed drafts; inline validation; confirmation before submit/cancel; successful submitted record; readonly/signed; network timeout; stale record; repeat click; failed upload; role changed during edit; print/export denied; locale switched during unsaved edits.
- Errors must explain *what failed* and the user-safe remedy, with technical diagnostics accessible only to authorized support. Never expose PHI or raw stack traces to ordinary staff.

---

## 7. Essential workflow journeys and handoffs

| Journey | Mandatory sequence | Outcome |
|---|---|---|
| WF-01 New patient | Search → Register → collect photo consent → capture before photos → book appointment/walk-in → Check In → Doctor opens Current Visit → History/Exam/Note → Photos/Annotation → External Lab if needed → Treatment/stock → Invoice → Cash/Card → Follow-up | One coherent timeline of linked records; no retyping identifiers |
| WF-02 Returning patient | Search existing → prior history/photos read-only → new appointment/visit → before/after comparisons → updated Clinical Note → treatment → bill | Prior photos remain linked to original visit; cross-visit compare preserves originals |
| WF-03 External lab | Doctor orders → staff confirms outside lab → records sample/send → result uploaded privately → doctor reviews/records interpretation → completed status | Clear responsibility and timestamps; cannot mark Reviewed without doctor action |
| WF-04 Procurement | Low-stock/near-expiry review → supplier/PO → partial receipt with new batch/expiry → balance increases → room transfer → procedure consumes batch → stock ledger reconciles | Source and target stock true to submitted records |
| WF-05 Payment | Visit marked billable → invoice draft/review/submit → cashier payment cash/card → receipt → manager shows invoiced vs collected separately | No double-charge, no card details stored |
| WF-06 Exceptions | Cancel/reschedule, no-show, no appointment, missing photo consent, lab rejected/returned, stock shortage, unlinked customer, duplicate submit, lost network | Error states guide user; no silent data loss or repeated stock/accounting operations |
| WF-07 TH/EN interaction | Login Thai → start note → enter Thai note text → switch EN → continue editing → Save Draft → log out/in → language preference preserved → reopen note | Clinical content unchanged; labels and validation display in selected language |

### State transitions and recovery

1. **Patient:** pending form → backend Patient created → profile available; duplicate candidate is warning, not automatic destructive merge.
2. **Photo:** local selection → consent verified → private original upload → Patient association → optional Appointment/Encounter association → annotation version; temporary failed linking is visible and retryable without duplicate original.
3. **Appointment:** requested slot → backend confirmed booking → eligible for check-in → assigned doctor; explicit cancellation/no-show path; display only states actually available on installed site.
4. **Encounter/Note:** open/created → draft sections saved → review → submit/sign/complete by authorized user. A draft is not a completed clinical document; server attestation and immutable history as supported.
5. **External lab:** Requested → Sent → Received with file → Reviewed with doctor identity; exceptions include cancelled/re-requested and missing/invalid files.
6. **Procedure stock:** requested → actual stock/warehouse/batch selected → reviewed → complete & consume once → source reference displayed, no client-side balance update as final truth.
7. **Invoice/Payment:** draft invoice → submitted invoice → payment posted/allocated → receipt issued; cancellation, return and refund are separate authorized accounting operations.
8. **Locale:** can change while any step is in progress without mutation of backend values or disappearance of form data.

---

## 8. Security, privacy, integrity and operational non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | All access checked on server by Frappe roles, DocType permissions and permitted record scope; UI permissions alone insufficient |
| NFR-02 | Sensitive medical data and photos private by default; use authorized Frappe file access, audit access where feasible, prevent public CDN caching and shared workstation disclosure |
| NFR-03 | Browser never contains master admin API key, credit-card secret or payment processor credentials |
| NFR-04 | Clinical notes and patient identifiers must not enter analytics, error tracking, URL query parameters, toast logs or browser console in ordinary operation |
| NFR-05 | Mutations must handle duplicate clicks, timeout-after-success, stale document versions and partial failures; preserve draft/recovery context and reconcile with backend before retries |
| NFR-06 | Financial/stock documents use approved submit/cancel/amend workflows; do not directly mutate ledger or post read-only workflow statuses |
| NFR-07 | Photo original, annotation layer, comparison link, consent and physician review are traceable; retention/deletion and revocation follow client-approved policy |
| NFR-08 | Field validation happens in UI for immediate guidance and again at server for correctness; do not trust client-calculated doctor slots, medical permissions, inventory quantity, totals or statuses |
| NFR-09 | Lists and charts use server pagination/filtering/aggregates; performance targets set and tested on clinic hardware after environment baseline; don't promise unmeasured response times |
| NFR-10 | Responsive 1440px, 834px, 390px; supports touch, keyboard, Thai UTF-8 content and readable 16px forms; no critical field hidden on mobile |
| NFR-11 | UI locale TH/EN complete; backend IDs and original clinical data unchanged on switch; test long translated labels, messages, print, data export |
| NFR-12 | Backups, restore, encryption and Thai privacy/legal acceptance must be documented separately for production; this spec does not claim automatic PDPA compliance |

---

## 9. Acceptance criteria and user acceptance scenarios

| ID | P | Scenario / test | Expected outcome |
|---|---|---|---|
| AT-01 | P0 | Register Thai-named patient; search exact and possible duplicate | One authorized patient created; Thai names readable; duplicates flagged |
| AT-02 | P0 | Book/reschedule/cancel doctor appointment; test invalid slot | Backend confirms slot, status and doctor; no duplicated booking |
| AT-03 | P0 | Check in and view waiting queue | Correct patient/doctor; double check-in prevented; actual supported state shown |
| AT-04 | P0 | Capture photos **before check-in** | Consent linked; originals private, associated with Patient and later Encounter without reupload |
| AT-05 | P0 | Open prior patient history from visit | Historical records presented readonly without duplicate typing |
| AT-06 | P0 | Enter physical exam and vitals | Values saved to valid field/record; don't write layout/display fields |
| AT-07 | P0 | Save and reopen unified Clinical Note | History/exam entered once; persists with correct encounter linkage and draft version |
| AT-08 | P0 | Annotate before photo, compare with after photo, undo/redo | Shapes and notes persist separately; original unchanged; visit/body area correct |
| AT-09 | P0 | External lab request, send, upload result, doctor review | Valid status timeline, actor, private result file and doctor signoff; no fake native status |
| AT-10 | P0 | Complete treatment using an expiring batch | Actual available stock checked, one procedure completion and exactly one stock transaction |
| AT-11 | P0 | Purchase Order → partial receipt with batch/expiry | New quantity matches ledger; remaining PO balance correct |
| AT-12 | P0 | Transfer to treatment room and inspect stock | Warehouse/source/destination/batch balances reconcile; no double posting |
| AT-13 | P0 | Invoice one or more services and receive Cash | Linked customer, correct tax/discount and invoice allocation; receipt available |
| AT-14 | P0 | Credit Card as payment mode | Correct posted amount and account; no PAN/CVV collected |
| AT-15 | P0 | Owner drilldown | Appointment, revenue, collected cash, outstanding and inventory KPIs reconcile with underlying records |
| AT-16 | P0 | Role permissions | Each role only sees authorized info/actions, including clinical photos and reports |
| AT-17 | P0 | Switch TH↔EN on each role homepage | Labels, buttons, charts, navigation, errors translated; no inaccessible controls |
| AT-18 | P0 | Switch language during unsaved clinical note and photo annotation | Unsaved data, active tab, selected patient and annotation layer remain intact |
| AT-19 | P0 | Persist language across devices/login sessions | Per-user selected language persists; guest login selector not overwritten unexpectedly |
| AT-20 | P0 | Validate Thai/English errors, dates, financial output, print | No truncated Thai labels, dates/currency consistent, print language policy respected; note content remains original |
| AT-21 | P0 | Simulate broken connection and timeout-after-success | Draft recoverable; no duplicate appointment/procedure/invoice/stock/payment; accurate retry feedback |
| AT-22 | P0 | Mobile and tablet walkthrough | Main workflow works at 390/834px; photo editor and notes touch-friendly |
| AT-23 | P0 | Verify all 97 old entries represented in consolidated manifest | All traceability mappings pass, including role-conditional tabs/drawers and system states |
| AT-24 | P0 | User-driven login/session expiry during edit | Re-auth and return to recoverable work without privacy leakage |
| AT-25 | P0 | Clinical note finalized, then amended by permitted role | Correct document lifecycle and audit; cannot silently overwrite finalized content |
| AT-26 | P1 | Advanced manager analytics and automated alerts | Truthful configured reports and alerts; not a P0 blocker |

### Release gates

- **UX completeness:** all P0 requirements have approved desktop/tablet/mobile frames; 37 surfaces and 97 legacy traceability entries accounted for; all role homes include realistic populated and error states; no duplicate history/exam editor.
- **Backend readiness:** installed version and active schema confirmed, source field mappings reconciled with Custom Fields/Property Setters, workflow endpoints and allowed state changes tested on disposable records; all `X` extension models agreed and implemented before release.
- **Bilingual acceptance:** Thai/English coverage complete for every P0 surface, validated by bilingual clinical stakeholders; untranslated UI inventory empty at sign-off; content and state stable on language switch.
- **Operational acceptance:** AT-01 through AT-25 pass or are explicitly declined with signed scope change. P0 extension dependencies cannot be relabeled “out of box” to bypass this gate.
- **Data reconciliation:** stock, invoice and patient records used in UI correspond to actual permitted backend records. Dashboards use genuine submitted data, not placeholder KPIs after demo handoff.

---

## 10. OpenDesign delivery instructions

OpenDesign **MUST** produce:

1. Role-specific information architecture and sitemap, based on **37 design surfaces** and an exhaustive mapping from 97 legacy states (manifest).
2. Design system: colors, spacing, icon language, components, type scale, Thai-capable font fallback, contrast, status chips, form input patterns, touch-size tokens.
3. **Bilingual screens:** Thai and English variants (or fully responsive translation-aware components) of every P0 surface; TH/EN language-switch component, in-progress note switch and sign-in switch states.
4. 7 role homes (Reception, Doctor, Nurse optional organizationally, Pharmacy, Cashier, Owner, Admin) with 4–6 icon+label task tiles and populated records.
5. Patient registration/profile, intake/camera/photo permission, one consultation workspace with all defined tabs, annotated before/after comparison, external lab tracking, inventory/purchasing, invoice/payment and dashboards.
6. Detailed form definitions: visible user labels in both languages, required/conditional markers, field types, example data, how field populates, key validation, submit/recovery/permission states. Mark backend uncertainty with `VERIFY` or `X` rather than inventing Frappe payload keys.
7. All major flows as clickable prototypes including return patient, walk-in, check-in, consultation draft, annotation, external lab, medicine stock/expiry, partial purchase receipt and payment.
8. All controls identified as real actions (not static icons), and all dialogues, bottom sheets, tabs and alerts enumerated. Every P0 action has a connected destination and error state.
9. Exportable design annotations for OpenCode: Design Surface ID, component name, route, role, required fields, primary actions, API intent, server-permission expectations, states and dependencies.
10. Separate unresolved technical assumptions list; design may show needed P0 extensions, but must clearly annotate that backend endpoints are not verified.

### Recommended prototype build order

1. Shared layout, sign-in and **TH/EN switch** on all breakpoints; seven role homepages.
2. Reception (Patient → Photos → Check-in), Doctor (single Visit Workspace → Photos/Annotation → Lab), and shared patient profile.
3. Pharmacy (stock/batch/PO/receipt/transfer/usage), Cashier (invoice/payment), Owner (dashboards).
4. Admin, role access edge cases, all error states, alternative workflows, bilingual print layouts.
5. Traceability and usability review with staff (Thai-first testing), then lock designs.

### OpenCode implementation handoff (post-design)

- Use design ID and field labels as UX contracts, not guessed backend `fieldname`s.
- Inspect actual backend **before** finalizing serializers or mutations. Define a single backend adapter per domain (patient, appointment, encounter/note, photo, lab, procedure, inventory, purchasing, billing, reporting).
- Create explicit API contract tests, permission tests and idempotency tests for state transitions. Existing Frappe metadata/DocType API can retrieve schemas, but controller validation must also be tested.
- Use a dedicated frontend i18n catalog with stable message IDs, typed locale `th|en`, test language persistence and fallback. Never translate free-text clinical content automatically.
- Develop additions as a separate Frappe custom app or approved extension; **do not modify Marley/ERPNext core source** unless a separate exception is authorized.

---

## 11. Source guidance and verification limitations

This is an implementation-oriented **functional and UX document**, not an exported schema of your installed server. Known public docs support Frappe's REST CRUD/whitelisted methods and metadata routes and its own translations/user-language settings, but **the actual Marley app versions, active Custom Fields, required-when expressions, privileges and check-in/stock workflow methods remain unverified**.

Reference materials:

- Frappe REST API and metadata: https://docs.frappe.io/framework/user/en/guides/integration/rest_api
- Frappe language resolution: https://docs.frappe.io/framework/user/en/python-api/language
- Frappe translation system: https://docs.frappe.io/framework/user/en/translations
- Frappe custom-app translations: https://docs.frappe.io/framework/user/en/guides/basics/translations
- Marley Health source: https://github.com/earthians/marley
- ERPNext docs: https://docs.frappe.io/erpnext
- Frappe UI implementation reference (not required for React frontend): https://github.com/frappe/frappe-ui

**Related package files:**

- `Clinic_Screen_Manifest_v2.json` — 37 consolidated design surfaces, all 97 v1 entries mapped.
- `Clinic_Backend_Contract_Verification_v2.md` — installed-site validation checklist and unresolved backend fields/workflows for OpenCode.
- `Clinic_OpenDesign_Starter_Prompt_v2.md` — copy/paste input brief for OpenDesign.

---

**Approval:** Clinic workflow, photo/consent rules, external-lab responsibilities, invoice/payment policy, language terminology, and data retention must be confirmed by authorized clinic stakeholders before production. The document is complete for **design coverage**, not a guarantee that every mutation is natively supported in your installed backend.
