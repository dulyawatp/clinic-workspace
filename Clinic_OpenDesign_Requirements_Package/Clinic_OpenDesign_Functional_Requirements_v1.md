# Demo Clinic — Complete UX/UI Functional Requirements
## Marley Health + ERPNext backend | React frontend | OpenDesign → OpenCode handoff

**Version:** 1.0 design baseline · **Prepared:** 2026-11-03 · **Status:** DESIGN-READY, BACKEND-SCHEMA VERIFICATION REQUIRED  
**Client context:** Small/medium private aesthetic clinic, Thailand. Existing ERPNext Company: `Demo Clinic` (`DC`).  
**Purpose:** Give OpenDesign the complete application information architecture, screens, roles, interactions, and required form data; give OpenCode a precise frontend-to-backend implementation contract.  
**Proposed UI:** React + TypeScript + Vite + shadcn/ui + Tailwind + Lucide; desktop/tablet-first, responsive to phone.  
**Authoritative backend:** existing Frappe + Marley Health + ERPNext. Standard ERPNext Desk stays available to administrators. Do not fork or modify core just to simplify the UI.

> **Read this before designing:** Fields marked **M** are mandatory by the cited public DocType metadata where checked; **C** = conditional/backend-required for certain scenarios; **B** = mandatory business/UI requirement even if DocType allows blank; **O** = optional; **D** = derived/read-only; **X** = a proposed extension, not proven to exist. **M is NOT a guarantee of the actual deployed site**: the checked Marley source is `develop`, ERPNext source is `version-16`; installed versions, custom fields, property setters, controller validation, and permissions may differ. A mandatory runtime schema audit is in §13. Design every form so fields can be added, made mandatory, or hidden without redesigning the whole screen.

---

## 1. Product vision and boundaries

### 1.1 User goal

A clinic staff member should begin from **four to six large, icon-led actions**, complete the usual task in as few steps as practical, and never need to know ERPNext DocType names. Every icon MUST have a short visible label; never rely on color/icon alone. One patient visit links appointment → check-in → medical history/photography → clinical note/physical exam → procedures and/or lab → stock usage → invoice/payment → follow-up, without retyping patient identity.

### 1.2 In scope — complete target application

- Patient registration, search, demographics, contacts, allergies/history, visit timeline, appointments, doctors and rooms.
- Appointment calendar, queue, check-in, no-show, reschedule, cancellation, appointment statuses, revisit booking.
- Patient photography: intake/profile and visit-specific BEFORE/AFTER, private uploads, comparison, consent; shape/arrow/text annotation is a **custom UI capability**.
- Doctor consultation: Patient Encounter and linked Clinical Note, medical history, physical examination, vitals, diagnosis, prescribing, Clinical Procedures, lab orders, external lab tracking/results.
- Pharmacy/inventory: item catalog, batches/expiry, stock on hand, stock movement, receiving, transfer, treatment consumption, purchasing, traceability and expiry alerts.
- Billing: Sales Invoice, cash/credit-card *payment method* (no card numbers stored), receipts, outstanding, cancellation/refund permissions.
- Management: operational and financial dashboards, drill-down reports, audit/traceability.
- Admin: user/role management, practitioner schedules, service/appointment types, procedure templates, rooms, warehouses, price lists, configuration and system health.
- Nurse/treatment assistant role is optional organizationally but all associated pages/flows are designed and can be permission-mapped to Doctor if the clinic does not employ that role.

### 1.3 Not in scope by default

CRM, lead generation, campaigns, loyalty/points, memberships; inpatient wards/bed management; insurance claims; patient self-service portal; telemedicine; complex payroll; automated integrations to external labs; appointment SMS/LINE marketing. Payroll can be added later through Frappe HR after a separate requirements pass. Do not place any excluded modules in the navigation.

### 1.4 Capability flags

- **BASE:** Existing standard DocTypes / documented Frappe API; may still need custom *frontend*.
- **VERIFY:** Existing functionality/version/setup must be tested on installed site (check-in, appointment slots, procedure consumption, prescription-to-dispensing, etc.).
- **EXT:** Explicit frontend + likely custom backend DocType/API needed (photo annotation metadata, structured outside-lab routing, consent, extra dashboards).
- **CONFIG:** Existing master data/settings/permissions must be configured before use.

### 1.5 Priority

- **P0**: Day-to-day clinic functions across Reception, Doctor, Pharmacy, Cashier, Owner, Admin; complete patient journey; role access; patient photos as private attachments; billing, stock, reporting.
- **P1**: Photo annotations/comparison, detailed external-lab tracking, near-expiry active-stock dashboard, stock-to-visit reconciliation, richer KPIs, nurse workflows where used.
- **P2**: Deeper physician commissions/payroll, external lab API integration, advanced forecasting. Excluded from first release unless separately approved.

---

## 2. Design system and responsiveness — OpenDesign MUST follow

**Brand:** premium, reassuring, clean aesthetic clinic; neutral ivory/white base, muted teal/sage primary, subdued blue for information, amber for waiting/warnings, red only for destructive/critical alerts. Avoid dense ERP tables as landing pages. Use simple vector icons and optional *original illustration* only for empty states/learning aids, not as a substitute for medical data.

**Responsive layouts:**
- **Wide desktop ≥1280px:** persistent slim left navigation + header + 2–4-column actions, compact metric cards and spacious patient list.
- **Tablet 768–1279px:** collapsible navigation, 2-column action tiles, comfortable doctor note editor, photos side by side when room permits.
- **Phone 360–767px:** bottom navigation or drawer; single-column cards, patient/appointment rows become stacked cards; photo capture and quick check-in usable; no horizontal scrolling on vital clinical forms. Tables may use a dedicated full-screen detail view.
- **Minimum targets:** touch controls ≈44×44 px, 16px or larger form body text where practical, visible focus/keyboard support, readable contrast, state represented with text + icon + color.
- **Language:** simple English labels initially, architecture ready for Thai labels; THB currency, Asia/Bangkok timestamp, dd MMM yyyy UI dates, retain unambiguous API ISO timestamps. No hard-coded Buddhist or Gregorian conversion rules without requirement.
- **Global shell:** clinic brand, user/role switch only where actually authorized, current location/company, global patient search (permission controlled), notifications, profile/logout, breadcrumb/back, connection/error indicator. Clinical context header persistently shows patient name + record ID + encounter date + doctor.
- **Actions:** prominent primary action, secondary Save Draft, explicit confirmation for Submit/Complete/Cancel, no hidden save, unsaved-change warning, loading/empty/error/success screens, disabled and permission-denied states.
- **Icon language:** patient/user, calendar, camera, clipboard/medical note, stethoscope, flask/lab, medicine/pill, package/warehouse, receipt/payment, chart/dashboard, gear. Always show the label under/beside the icon.
- **Design component inventory:** AppShell, RoleHome, ActionTile, KPICard, PatientIdentityBar, AppointmentRow, StatusChip, QueueBoard, Calendar, Wizard/Stepper, DataEntryForm, ConfirmationDialog, ClinicalNoteEditor, VitalsCard, PhotoCapture, PhotoGrid, Annotator, SideBySideBeforeAfter, LabStatusTimeline, BatchExpiryTag, StockQuantity, LineItemEditor, MoneySummary, Print/Receipt, AuditTimeline, PermissionGuard, Empty/Loading/ErrorState.

---

## 3. Roles and permissions

**Roles:** `REC` Receptionist, `DOC` Doctor/Practitioner, `NUR` Nurse/Treatment Assistant (optional), `PHA` Pharmacy/Stock, `CAS` Cashier (may be combined with REC), `MGR` Clinic Owner/Manager, `ADM` Administrator. A single person may have more than one role, but permissions are cumulative only when authorized.

| Domain / operation | REC | DOC | NUR | PHA | CAS | MGR | ADM |
|---|---|---|---|---|---|---|---|
| Patient directory / identity | C/R/U | R | R | limited R | billing R | limited R | manage |
| Patient clinical history / notes | minimal R only | R/C/U/submit | limited R/C per policy | none | none | aggregate only | support by authorized policy |
| Appointments / check-in | C/R/U | assigned R | assigned R/check-in | none | billing R | report R | manage |
| Intake and patient photos | capture/upload | clinical R/C/annotate | capture/upload | none | none | aggregate only | storage config |
| Encounters / Clinical Notes | none | C/R/U/submit | vitals/assistant | none | none | aggregate only | config |
| Orders, treatment / clinical procedures | none | order/complete | assist under delegation | stock-related R | billable R | aggregate | configure |
| External lab results | status-only if delegated | full R/create/review | upload/track | none | billing only | aggregate | config |
| Item, batch, stock movement / purchase | none | availability only | room availability | full assigned scope | none | R/report/approve | configure |
| Sales invoices / payments | optional cashier delegation | clinical charges preview | none | none | full assigned scope | R/report/approve | configure |
| User/roles/master data | none | own schedule availability | none | limited item procurement | limited payment modes | policy approval | full config |

**Enforcement:** Backend permission checks and restricted whitelisted methods are authoritative. Hiding a UI menu does not grant/revoke permission. A Doctor must not fetch unrelated patients just by guessing document IDs. Sensitive patient files must be private; no public `/files/` link for clinical photographs. Provide 403, expired-session, and masked-search states. Never display production medical information in logs/analytics.

**Role home actions**
- REC: `New Patient`, `Book Appointment`, `Today's Queue`, `Patient Photos`, `Find Patient`, `Checkout` if cashier delegated.
- DOC: `My Patients`, `Start Consultation`, `Clinical Note`, `Photos`, `Lab`, `Procedures`.
- NUR: `Waiting Patients`, `Record Vitals`, `Take Photos`, `Procedure Prep`, `Aftercare`.
- PHA: `Stock on Hand`, `Batches & Expiry`, `Receive Stock`, `Stock Transfer`, `Treatment Usage`, `Purchase Orders`.
- CAS: `Pending Bills`, `Create Invoice`, `Receive Payment`, `Print Receipt`, `Outstanding`.
- MGR: `Clinic Overview`, `Appointments`, `Revenue`, `Stock/Expiry`, `Purchasing`, `Treatment Activity`.
- ADM: `Users & Roles`, `Practitioners & Schedules`, `Appointment Types`, `Treatment Templates`, `Items & Warehouses`, `Pricing`, `Configuration`.

---

## 4. Complete page / route registry (design every ID)

**Design rule:** Each ID below is a screen or explicitly named tab/drawer state. Tabs may share a single page template but must still be represented as OpenDesign frames. `/clinic/...` is a *proposed* route space, not a native Marley endpoint. Names/paths are design contracts only.

| ID | Proposed route / view | Role(s) | Priority | Main object / outcome |
|---|---|---|---|---|
| SH-01 | `/clinic/login` | all | P0 | session login; invalid/expired state |
| SH-02 | `/clinic/home` | all | P0 | role-directed home, switch only if permitted |
| SH-03 | `/clinic/search` | permitted | P0 | patient/appointment/global search with role filters |
| SH-04 | `/clinic/notifications` | all | P0 | assigned work, unread/read, deep links |
| SH-05 | `/clinic/profile` | all | P0 | user, role, language, logout |
| SH-06 | `/clinic/patients` | REC,DOC,NUR,limited | P0 | patient list/search/filter |
| SH-07 | `/clinic/patients/new` | REC,ADM | P0 | register patient form |
| SH-08 | `/clinic/patients/:id` | authorized | P0 | patient summary, identity, allergies, visit timeline |
| SH-09 | `/clinic/patients/:id/edit` | REC,ADM | P0 | edit allowed demographics |
| SH-10 | `/clinic/patients/:id/history` | DOC,NUR-limited | P0 | medical/surgical history, allergies, medications |
| SH-11 | `/clinic/patients/:id/timeline` | authorized | P0 | appointments, encounters, procedures, labs, bills permission-filtered |
| SH-12 | `/clinic/patients/:id/photos` | REC,DOC,NUR | P0 | profile and visit photo gallery |
| SH-13 | `/clinic/patients/:id/photo/new` | REC,DOC,NUR | P0 | camera/upload and photo context |
| SH-14 | `/clinic/photos/:id` | DOC,authorized | P0 | photo detail and original metadata |
| SH-15 | `/clinic/photos/:id/annotate` | DOC,authorized | P1/EXT | arrows, circles, rectangles, labels, undo, preserve original |
| SH-16 | `/clinic/patients/:id/before-after` | DOC,authorized | P1/EXT | side-by-side comparable visits/photo pairs |
| RC-01 | `/clinic/reception` | REC | P0 | front desk home KPIs and shortcuts |
| RC-02 | `/clinic/reception/queue` | REC,NUR | P0 | today's waiting/check-in list; action states |
| RC-03 | `/clinic/appointments` | REC,DOC | P0 | list and calendar; filter by practitioner/date/type |
| RC-04 | `/clinic/appointments/new` | REC | P0 | book appointment, available slots |
| RC-05 | `/clinic/appointments/:id` | REC,DOC | P0 | appointment details, status, actions |
| RC-06 | `/clinic/appointments/:id/reschedule` | REC | P0 | new slot, overlap rules and reason |
| RC-07 | `/clinic/appointments/:id/cancel` | REC | P0 | confirmation/reason; native status action |
| RC-08 | `/clinic/reception/check-in/:id` | REC,NUR | P0/VERIFY | check-in; may use standard or custom backend workflow |
| RC-09 | `/clinic/reception/check-out/:id` | REC,CAS | P0 | visit completion and invoice/follow-up handoff |
| RC-10 | `/clinic/reception/follow-up` | REC | P0 | book return visit for existing patient |
| DR-01 | `/clinic/doctor` | DOC | P0 | doctor home, personal schedule and tasks |
| DR-02 | `/clinic/doctor/my-appointments` | DOC | P0 | own appointment queue/calendar |
| DR-03 | `/clinic/encounters` | DOC | P0 | consultation list: draft/complete |
| DR-04 | `/clinic/encounters/new` | DOC | P0 | start encounter from appointment or patient |
| DR-05 | `/clinic/encounters/:id` | DOC | P0 | patient visit shell, navigation across medical sections |
| DR-06 | `/clinic/encounters/:id/history` | DOC | P0 | patient complaint and longitudinal history |
| DR-07 | `/clinic/encounters/:id/exam` | DOC,NUR limited | P0 | vitals, physical exam, clinical findings |
| DR-08 | `/clinic/encounters/:id/clinical-note` | DOC | P0 | create/edit linked Clinical Note; save draft |
| DR-09 | `/clinic/encounters/:id/diagnoses` | DOC | P0 | symptoms, diagnoses, clinical coding as supported |
| DR-10 | `/clinic/encounters/:id/medications` | DOC | P0 | medication prescriptions/orders |
| DR-11 | `/clinic/encounters/:id/procedures` | DOC | P0 | order / review clinical procedures |
| DR-12 | `/clinic/procedures/:id` | DOC,NUR limited,PHA limited | P0 | perform procedure, status, consumption panel |
| DR-13 | `/clinic/encounters/:id/labs` | DOC,NUR | P0 | order and review lab tests |
| DR-14 | `/clinic/labs/:id` | DOC,NUR authorized | P0 | lab request/test detail/result |
| DR-15 | `/clinic/encounters/:id/external-lab` | DOC,NUR | P1/EXT | outside provider, status, result upload and review |
| DR-16 | `/clinic/encounters/:id/photos` | DOC,NUR limited | P0 | visit-linked photo gallery / annotation link |
| DR-17 | `/clinic/encounters/:id/summary` | DOC | P0 | review then submit/complete; downstream orders |
| DR-18 | `/clinic/doctor/follow-ups` | DOC | P0 | patients awaiting clinical follow-up |
| NU-01 | `/clinic/nurse` | NUR | P1 | treatment assistant home |
| NU-02 | `/clinic/nurse/vitals/:appointmentId` | NUR,DOC | P0 if nurse used | record vitals; appointment/patient links |
| NU-03 | `/clinic/nurse/treatment-prep/:id` | NUR | P1 | patient, procedure, room and materials checklist |
| NU-04 | `/clinic/nurse/aftercare/:id` | NUR | P1 | recording recovery/aftercare note and handoff |
| PH-01 | `/clinic/pharmacy` | PHA | P0 | stock metrics, key alerts, shortcuts |
| PH-02 | `/clinic/pharmacy/items` | PHA | P0 | item catalog/search/filter |
| PH-03 | `/clinic/pharmacy/items/:id` | PHA | P0 | item detail/stock/batch settings |
| PH-04 | `/clinic/pharmacy/batches` | PHA | P0 | batch list, expiry filter |
| PH-05 | `/clinic/pharmacy/batches/:id` | PHA | P0 | batch detail, provenance, expiry, on hand |
| PH-06 | `/clinic/pharmacy/expiry` | PHA,MGR | P1 | near-expiry usable stock; 30/60/90-day thresholds |
| PH-07 | `/clinic/pharmacy/stock-balance` | PHA,MGR | P0 | stock by warehouse/item/batch |
| PH-08 | `/clinic/pharmacy/stock-ledger` | PHA,MGR | P0 | movement audit, document drilldown |
| PH-09 | `/clinic/pharmacy/receive` | PHA | P0 | purchase receipt for existing PO or direct receipt |
| PH-10 | `/clinic/pharmacy/transfers/new` | PHA | P0 | stock transfer/issue with batch details |
| PH-11 | `/clinic/pharmacy/usage` | PHA,DOC limited | P0 | consumables linked to procedure/stock ledger |
| PH-12 | `/clinic/pharmacy/purchase-orders` | PHA,MGR | P0 | PO list, status, supplier |
| PH-13 | `/clinic/pharmacy/purchase-orders/new` | PHA,MGR | P0 | prepare/submit PO according to policy |
| PH-14 | `/clinic/pharmacy/purchase-orders/:id` | PHA,MGR | P0 | order and receipt status |
| PH-15 | `/clinic/pharmacy/suppliers` | PHA,ADM | P0 | supplier search/view/setup permission |
| PH-16 | `/clinic/pharmacy/reconcile` | PHA,ADM | P1 | stock count / variance / submit restricted |
| CA-01 | `/clinic/cashier` | CAS,REC delegated | P0 | pending bills, payments, shortcuts |
| CA-02 | `/clinic/cashier/pending` | CAS | P0 | visits awaiting billing |
| CA-03 | `/clinic/invoices` | CAS,MGR | P0 | invoice list, outstanding, filter |
| CA-04 | `/clinic/invoices/new` | CAS | P0 | create sales invoice from encounter/procedure |
| CA-05 | `/clinic/invoices/:id` | CAS,MGR | P0 | invoice detail, submit, adjustments |
| CA-06 | `/clinic/invoices/:id/payment` | CAS | P0 | take cash or card *method*, payment entry |
| CA-07 | `/clinic/payments` | CAS,MGR | P0 | payment history and receipt list |
| CA-08 | `/clinic/payments/:id/receipt` | CAS | P0 | print/download receipt |
| CA-09 | `/clinic/cashier/refunds` | CAS,MGR | P1 | return/cancel workflow with permission/approval |
| MG-01 | `/clinic/management` | MGR | P0 | clinic owner overview KPI + trends |
| MG-02 | `/clinic/management/appointments` | MGR | P0 | visit volume, cancellation/no-show, doctor allocation |
| MG-03 | `/clinic/management/treatments` | MGR | P0 | treatment volumes by procedure and period |
| MG-04 | `/clinic/management/financial` | MGR | P0 | invoiced revenue, receipts, outstanding, trends |
| MG-05 | `/clinic/management/inventory` | MGR | P0 | inventory value, stock/expiry and purchases |
| MG-06 | `/clinic/management/purchasing` | MGR | P0 | spend, orders, received/unreceived |
| MG-07 | `/clinic/management/reports` | MGR | P0 | all reports directory/export (approved) |
| AD-01 | `/clinic/admin` | ADM | P0 | admin home: setup status, exceptions |
| AD-02 | `/clinic/admin/users` | ADM | P0 | users, roles and access policies |
| AD-03 | `/clinic/admin/practitioners` | ADM | P0 | doctor roster / linked users |
| AD-04 | `/clinic/admin/schedules` | ADM | P0 | practitioner schedule and availability |
| AD-05 | `/clinic/admin/appointment-types` | ADM | P0 | visit types, durations, charges where supported |
| AD-06 | `/clinic/admin/procedure-templates` | ADM | P0 | service/procedure and consumables defaults |
| AD-07 | `/clinic/admin/locations` | ADM | P0 | healthcare service units, rooms, warehouses |
| AD-08 | `/clinic/admin/items` | ADM,PHA delegated | P0 | medicine/product/service item setup |
| AD-09 | `/clinic/admin/pricing` | ADM,MGR authorized | P0 | price lists and payment methods |
| AD-10 | `/clinic/admin/lab-templates` | ADM | P0 | lab test catalog / external provider mapping (if used) |
| AD-11 | `/clinic/admin/clinic-settings` | ADM | P0 | company, default department, photo/expiry policies |
| AD-12 | `/clinic/admin/audit` | ADM,authorized | P0 | document/file changes, reversal/error logs |
| AD-13 | `/clinic/admin/integration-status` | ADM | P0 | backend versions, connectivity, master data readiness |
| SYS-01 | global Loading / Empty / Error | all | P0 | no records, retry, save failure, validation |
| SYS-02 | global Unauthorized / Session expired | all | P0 | 403 / login required / masked content |
| SYS-03 | global Unsaved / Conflict / Duplicate | all | P0 | protect work, prevent double submit |
| SYS-04 | global Print / Preview / Export | permitted | P0 | invoice/medical documents as permitted |

**Design volume:** 97 routes/states, not 90 completely different page templates. Reuse 15–20 page templates and shared components. OpenDesign must show all *distinct task states* even when built with the same template.

---

## 5. Required forms and field-level contract

**Column meanings:** `UI label → Backend target.field` · `class` · `validation / behavior`. Server-generated IDs/naming series must not be manually requested if backend supplies valid defaults. Do not make an optional UI field appear mandatory merely because it is useful; conversely, do not hide a mandatory backend field without providing a real default/reference.

### F01 — Register Patient (SH-07)

| UI label | Backend | Class | Validation / interaction |
|---|---|---|---|
| First name | `Patient.first_name` | **M** | nonempty, Unicode/Thai names allowed |
| Gender | `Patient.sex` | **M** | link to existing `Gender`, not free text |
| Middle name | `Patient.middle_name` | O | optional |
| Last name | `Patient.last_name` | O | optional by checked source; clinic may make B |
| Date of birth | `Patient.dob` | B | clinic intake expectation, not source-mandatory |
| Mobile phone | `Patient.mobile` | B | Thailand number validation; reuse existing record if match |
| Alternate phone | `Patient.phone` | O | optional |
| Email | `Patient.email` | O | optional |
| Blood group | `Patient.blood_group` | O | optional, not required for intake |
| Allergies | `Patient.allergies` | B | collect if known; allow `Unknown` as explicit business status via separate UI convention |
| Medical/surgical history | `Patient.medical_history`, `.surgical_history` | O | accessible after registration, not all fields on first form |
| Current medication | `Patient.medication` | O | physician-verified later |
| Profile image | `Patient.image` | O | photo attachment, not same as before/after set |
| Patient ID | `Patient.name` | D | generated; prominently display after save |
| Linked accounting customer | `Patient.customer` | C/D | verify Healthcare Settings auto-link; do not assume record always exists |
| Duplicate search | query `Patient` | B | before create, search name/mobile/DOB; user chooses reuse/create |

**Submit:** `POST Patient`; on success show patient identity, `Take Photos`, `Book Appointment`, `View Record`. Any additional required `naming_series`/custom fields/defaults must be supplied by runtime schema.

### F02 — Book / Reschedule Appointment (RC-04/06)

| UI label | Backend | Class | Validation / interaction |
|---|---|---|---|
| Patient | `Patient Appointment.patient` | **M** | existing Patient link |
| Appointment type | `.appointment_type` | **M** | selected from configured Appointment Type |
| Appointment for | `.appointment_for` | **M/D** | fetched from type; preserve backend logic |
| Company | `.company` | **M** | default `Demo Clinic`, submit real company `name` |
| Date | `.appointment_date` | **M** | local date; backend may make read-only based on type |
| Time | `.appointment_time` | **C** | mandatory for timed Practitioner scheduling depending on check-in type |
| Practitioner | `.practitioner` | C | require in UI when appointment for Practitioner |
| Department / service unit | `.department`, `.service_unit` | C | conditional by appointment_for |
| Duration | `.duration` | D/C | from appointment type; check availability/slot boundaries |
| Notes / purpose | `.notes` | O | short notes; avoid sensitive diagnoses in calendar row |
| Status | `.status` | D | use permitted action/transition; do not arbitrary `PUT` read-only |
| Reason | extension or frontend-specific field | B/X | reschedule/cancel reason persisted in approved backend mechanism |

**Submit:** use backend slot/availability checks and approved appointment creation flow; on duplicate/overlap return actionable alternatives. Do not promise real-time availability based only on frontend calendar math. For reschedule and status change use the site's validated methods; do not treat readonly metadata fields as directly writable.

### F03 — Patient Photography (SH-13/15/16)

| UI label | Target | Class | Validation |
|---|---|---|---|
| Patient | `Patient.name` reference | B | existing patient, always linked |
| Appointment / Encounter | `Patient Appointment.name` / `Patient Encounter.name` | B | link to a visit where possible |
| Photo category | proposed `Clinic Photo.photo_type` | **X/B** | `Profile`, `Before`, `After`, `Clinical`, `Document` |
| Body area | `Clinic Photo.body_area` | X/B | configurable list, e.g. face/front/side |
| Capture date/time | `Clinic Photo.captured_at` | X/B | device timestamp + normalized server time |
| Image file | Frappe `File`, `is_private=1` | B | jpg/png/heic if supported, size/type limits, thumbnails |
| File association | `File.attached_to_doctype`, `.attached_to_name` | B | authorized patient/visit; exactly defined by server |
| Photographer | audit user / `owner` | D | current authenticated user |
| Consent reference | proposed `Clinic Photo.consent_id` | X/B | verified consent before images; configurable policy |
| Annotations | `Clinic Photo Annotation` or JSON blob + derived image | X/P1 | shape, coordinates, color, labels, version, original never overwritten |
| Pair/visit comparison | `Clinic Photo.pair_id` or query by visit/body area | X/P1 | selected before and after photos, optional date gap |

**P0:** capture/upload private files, view grid, zoom, delete rights, link to visit. **P1:** annotate with arrows/ellipse/rectangle/text, undo/redo and before/after slider. The plain `Patient.image` field is for a profile image, **not** a multi-photo/aesthetic treatment gallery. Required custom photo schema must be finalized before OpenCode implementation.

### F04 — Check In / Queue (RC-08, RC-02)

- Inputs: `Patient Appointment.name` **B**, authenticated user **D**, actual timestamp **D**, optional queue/service unit **C**, optional reason/notes **O**.
- Show patient/doctor/date/type, current status, conflicts, next destination; confirm `Check In`; server returns updated appointment status/queue record, or explicit version/setup error.
- If `marley_frontend` is installed, its queue model (Patient Token, Queue Assignment, etc.) may be reused; **do not design the frontend as if those DocTypes exist unless installation confirms them**.
- States: Scheduled, Confirmed, Checked In, in consultation (if derivable), Checked Out/Closed, Cancelled, No Show. Use actual backend status vocabulary. Enforce permission and repeat-click protection.

### F05 — Record Vitals (NU-02 / DR-07)

| UI label | Backend | Class |
|---|---|---|
| Patient | `Vital Signs.patient` | **M** |
| Date | `.signs_date` | **M**, default Today |
| Time | `.signs_time` | **M**, default Now |
| Appointment | `.appointment` | C/D |
| Encounter | `.encounter` | C/D |
| Body temperature | `.temperature` | O |
| Pulse | `.pulse` | O |
| Respiratory rate | `.respiratory_rate` | O |
| Systolic / diastolic BP | `.bp_systolic`, `.bp_diastolic` | O |
| Height / weight | `.height`, `.weight` | O |
| Note | `.vital_signs_note` | O |

Use valid ranges, documented units and concise abnormal-value visibility; no automatic diagnoses.

### F06 — Start Patient Encounter (DR-04) / Edit Clinical Visit (DR-05)

| UI label | Backend | Class | Details |
|---|---|---|---|
| Patient | `Patient Encounter.patient` | **M** | from appointment where possible |
| Practitioner | `.practitioner` | **M** | current doctor link, do not infer from user email alone |
| Appointment type | `.appointment_type` | **M** | must be real configured type |
| Encounter date | `.encounter_date` | **M** | default Today |
| Encounter time | `.encounter_time` | **M** | default Now |
| Appointment | `.appointment` | B/C | link when booked; walk-in optional |
| Company | `.company` | C | default `Demo Clinic` |
| Complaint / symptoms | `.symptoms` child table | B | at least one description for meaningful consult per clinic policy |
| Diagnosis | `.diagnosis` child table | B/C | may be provisional; valid linked coding if supplied |
| Procedure prescriptions | `.procedure_prescription` child table | C | selected treatments |
| Medication prescriptions | `.drug_prescription` child table | C | drug/dose/period/details; validate child schema |
| Lab prescriptions | `.lab_test_prescription` child table | C | selected tests; validate child schema |
| Status | `.status` | D | read-only; do not set directly |
| Submit orders on save | `.submit_orders_on_save` | C | make impact clear; prevent duplicate order creation |

**Critical schema fact:** In checked Marley source, `Patient Encounter.physical_examination` is a **Column Break layout field**, *not a clinical text input*. `Patient Encounter.clinical_notes` is an **HTML display field**, *not a generic editable note*. Implement structured examination content using real clinical-note/observation records or an approved backend extension. Do not send an examination string into either field. A separately linked `Clinical Note` DocType exists; see next form.

**States:** draft/unsaved, draft/saved, ready for review, submitted/ordered/completed based on real backend behavior, canceled/amended. Confirmation must show which lab/procedure/medication requests will be created; do not double submit.

### F07 — Clinical Note Editor (DR-08)

| UI label | Backend | Class | Details |
|---|---|---|---|
| Patient | `Clinical Note.patient` | **M** | current patient reference |
| Note content | `.note` | **B** | Text Editor; required by product for a completed note even if not native `reqd` |
| Note type | `.clinical_note_type` | O/C | existing Clinical Note Type if configured |
| Posting date/time | `.posting_date` | D/C | default and server semantics |
| Practitioner | `.practitioner` | D/C | read-only in checked source; server must set/derive |
| Reference Doc | `.reference_doc` | D/C | read-only in checked source; link to `Patient Encounter` via approved backend method |
| Reference Name | `.reference_name` | D/C | read-only dynamic link; approved backend method |
| Section headings | UI-only structure or custom note template | B | History of present illness, past medical history, allergies, physical exam, assessment, plan, follow-up; convert to `note` HTML where safe |

**Requirement:** One guided editor with tabs `History`, `Examination`, `Assessment`, `Plan`, plus Save Draft/Review. Persist with `Clinical Note.note`, linked to encounter by **tested server-side logic**. The source marks `reference_doc`, `reference_name`, and `practitioner` read-only; confirm how your deployed site creates links. If you need structured, separately queryable HPI/physical exam fields or electronic signature/amendment workflow, specify a new schema/API. Do not fake backend links by changing read-only client fields.

### F08 — Medication Prescription (DR-10)

**Parent:** `Patient Encounter.drug_prescription[]` child type `Drug Prescription`; resulting `Medication Request` must be checked/created through existing clinical workflow.

| UI label | Example backend | Class |
|---|---|---|
| Medication | `Drug Prescription.medication` or `.drug_code` | C; one valid medication/item as required by chosen flow |
| Dose | `.dosage` OR `.interval` | **C** based on `dosage_by_interval` |
| Duration | `.period` | **M** in checked child schema |
| Dosage form | `.dosage_form` | **M** in checked child schema |
| Quantity / repeats | `Medication Request.quantity`, `.number_of_repeats_allowed` | C |
| Instructions | `.comment`, `Medication Request.order_description` | O/B clinic policy |

Do not render a dispensing-complete badge merely because a prescription was submitted. Prescription, dispensing, invoicing, and stock reduction are separate operational events until verified integrations connect them.

### F09 — Order and Complete Clinical Procedure (DR-11/12)

| UI label | Backend | Class |
|---|---|---|
| Patient | `Clinical Procedure.patient` | **M** |
| Procedure template | `.procedure_template` | **M** |
| Appointment | `.appointment` | C |
| Practitioner | `.practitioner` | B/C |
| Treatment room/service unit | `.service_unit` | B/C |
| Start date/time | `.start_date`, `.start_time` | B/C |
| Consume stock | `.consume_stock` | C toggle |
| Warehouse | `.warehouse` | **C** mandatory when `consume_stock=1` |
| Consumable item | `.items[].item_code` (`Clinical Procedure Item`) | **M** per row |
| Quantity | `.items[].qty` | **M** per row, >0 |
| Unit | `.items[].uom` | **M** per row |
| Batch | `.items[].batch_no` | C for tracked stock; check actual stock method/bundle |
| Notes | `.notes` | O |
| Status | `.status` | D; use approved workflow |

**Requirement:** display selected stock, on-hand, batch expiry, quantity and cost-aware preview; block expired/insufficient stock as supported. `Complete & Consume` must call correct existing workflow and create stock movement exactly once. Show resulting Stock Entry/ledger reference; refresh balance and never directly edit the quantity field on Batch or Item.

### F10 — Lab / External Lab (DR-13/14/15)

**Native:** `Patient Encounter.lab_test_prescription[]` child `Lab Prescription` (e.g. `lab_test_code`, `lab_test_comment`), associated `Service Request`, and/or `Lab Test` (`patient` **M**, `template` **M**, `naming_series` **M**, `patient_sex` **M** in checked source, plus result fields, practitioner, date/status). Always fetch data from encounter/patient rather than requiring reentry.

**External lab extension (P1):** New record `Clinic External Lab Request` if no suitable native workflow proves sufficient. Fields: `patient` **B**, `encounter` **B**, `ordered_by_practitioner` **B**, `lab_provider` **B**, `requested_test` **B**, `requested_at` **B**, `status` **B** (`Ordered/Sent/Received/Reviewed/Cancelled`), `sample_collected_at` O, `external_reference` O, `expected_result_at` O, `received_at` C, `result_attachment_file_id` C on results, `reviewed_by` C, `reviewed_at` C, `interpretation` B on review. Model it as provider workflow, **not** automatically as native `Lab Test` unless tested.

### F11 — Stock Item / Batch Setup (AD-08, PH-03/05)

**Item** (`Item`): `item_code` **B/M to verify**, `item_name` **B**, `item_group` **B**, `stock_uom` **B**, `is_stock_item` **B**, `is_purchase_item` / `is_sales_item` conditional, `has_batch_no` **B for tracked medicines**, images optional, default warehouse/valuation configuration. Distinguish **service (non-stock Item)** from **medicine/supply (stock Item)**.

**Batch** (`Batch`): `batch_id` **M**, `item` **M**, `expiry_date` **B for expiring medicines** (not native `reqd` in checked v16 schema), `manufacturing_date` O, `supplier` D/C. Show stock on hand by batch/warehouse from ledger, not from mere static batch master row. Expiring warning only for positive usable stock and in-scope warehouses. Do not allow issue from an expired batch unless a documented override policy is configured and authorized.

### F12 — Receive Stock / Purchase Receipt (PH-09)

**Purchase Receipt**: `supplier` **B/M verify**, `company` **B**, posting date/time **B**, `items[]` **B** (at least one). Each receipt line: `item_code` **B**, `qty` **B >0**, `uom` **B**, `rate` **C**, `warehouse` **B**, batch/serial bundle or `batch_no` **C** according to v16 settings, `expiry_date` on linked new Batch **B for medicine**. Link purchase order if applicable; show before/after stock; submit once only.

### F13 — Purchase Order (PH-13)

**Purchase Order**: `supplier` **B**, `company` **B**, `transaction_date` **B**, `schedule_date` **B/C**, `items[]` **B**. Each line needs item, qty, uom, rate, required/delivery date, target warehouse where applicable. Supplier and purchase rates are linked ERPNext data; do not invent API fields. Support Draft → Submit → Partially Received → Completed/Closed via actual statuses.

### F14 — Stock Transfer / Issue / Reconciliation (PH-10/16)

**Stock Entry**: `company` **B**, `stock_entry_type` / `purpose` **B**, `posting_date` default **B**, source warehouse (`from_warehouse` or line `s_warehouse`) **C**, target warehouse (`to_warehouse` / line `t_warehouse`) **C**, `items[]` at least one. Each line: `item_code` **B**, `qty` **B**, UOM **C**, from/to warehouse **C**, batch identifier or `serial_and_batch_bundle` **C**. Transfer requires both locations, issue requires source. Stock Reconciliation is a separate restricted flow and DocType—do not implement as a Stock Entry pretending to be reconciliation.

### F15 — Invoice (CA-04/05)

**Sales Invoice**: `customer` **B** (linked from Patient, not Patient ID), `company` **B**, `posting_date` **B**, `due_date` **C**, `items[]` **B**. Line: `item_code` **B**, `qty` **B**, `rate` **B/C**, `uom` **C**, discount **O**, tax **C** from settings; invoice includes treatment/service and/or products per clinic policy. `is_pos`, `update_stock` and POS profile are *conditional*; do not turn on both treatment stock consumption and Sales Invoice stock deduction for same consumable. Prevent double billing of a clinical procedure, and link clinical source document by validated method/custom reference if necessary.

### F16 — Payment / Receipt (CA-06/08)

**Payment Entry**: `payment_type=Receive` **B**, `company` **B**, `party_type=Customer` **B**, `party` **B**, `posting_date` **B**, `mode_of_payment` **B** (Cash or Credit Card), `paid_from`/`paid_to` **C** actual company accounts, `paid_amount` **B >0**, `received_amount` **C**, `references[]` **B** to invoice and `allocated_amount` ≤ outstanding. Backend may provide defaults; preview exact allocation prior to submit. **Do not collect or save raw PAN/CVV/cardholder card data in this application.** Refund/return and void are restricted and auditable.

### F17 — User / Role / Schedule / Master Settings (AD)

- **User:** email/login, enabled, language, assigned roles; password handling via approved auth flow (never expose in logs). Doctor mapping to `Healthcare Practitioner.user_id` where supported.
- **Healthcare Practitioner:** practitioner name, department, linked user, enabled status; exact fields from runtime metadata.
- **Practitioner Schedule:** schedule name, workdays/time slots, service unit, doctor assignment via the installed model; validate clinic working hours, leave and overlap.
- **Appointment Type:** name, duration, practitioner/department/service-unit policy, billing item where supported.
- **Clinical Procedure Template:** procedure/service, price item, default consumables/room as supported; verify mapping.
- **Healthcare Service Unit:** actual non-root treatment room assignment; `Warehouse` location is a separate ERPNext master; do not attempt to edit a root/group node as a room.
- **Healthcare Settings/Stock Settings:** backend settings only, not public staff UI; appointment check-in, batch/serial behavior and patient/customer association must be measured on site.
- **Mode of Payment + Company Account:** `Cash`, `Credit Card` configured with valid account mapping; tax setup reviewed for Thai operations.
- **Notifications:** near-expiry/reorder and assigned lab actions only if configured; alerts aren't assumed out of box.

---

## 6. Exact day-in-the-life workflows and transitions

### WF-01 New patient, consultation, treatment, payment

1. Reception logs in → `DC Reception` → search existing patient by name/mobile; no match → Register Patient (F01).
2. Reception books Patient Appointment (F02), selecting type, doctor, date and available slot. Show confirmation and calendar.
3. Reception obtains clinic-required photo permission and uploads profile/intake before photos (F03). Originals stored private, linked to patient/visit.
4. On arrival Reception selects today's appointment → Check In (F04). Only current eligible bookings show action.
5. Nurse (if used) records vitals (F05); doctor selects own waiting appointment → Start Encounter (F06); patient identity/appointment/doctor auto-populate.
6. Doctor reviews medical/surgical history/allergies, records symptoms, examination and Clinical Note (F07), sets diagnoses/plan and may order Lab (F10)/Medication (F08)/Procedure (F09).
7. Doctor or treatment assistant records actual procedure and batch/warehouse-backed consumables; stock transaction appears in Stock Ledger (F09), with duplicate completion prevented.
8. Cashier generates Sales Invoice for billable services (F15), reviews already billed items and any discounts; receives Cash or Credit Card payment (F16); prints receipt.
9. Reception books follow-up appointment; patient Timeline shows authorized linked records. Manager KPIs update from submitted transactions.

### WF-02 Returning patient / photos comparison

Search and select existing patient → review previous encounters/photos → new appointment/check-in → create new before photos → select past before/after images grouped by same body area → annotate **copy/layer**, never destroy originals → save clinical note and comparison link.

### WF-03 External laboratory

Doctor selects/request test → staff marks external provider, collects/sends sample if needed → status timeline → upload private result → doctor reviews/interprets → note updated. Distinguish native Lab Test/Service Request from proposed external provider record. Cannot silently change result after review.

### WF-04 Pharmacy replenishment and expiry

Review stock by item/batch → see warnings for positive balance expiring within thresholds → create PO → submit/receive partial delivery → capture batch and expiry → stock ledger updated → treatment-room transfer → procedure consumes tracked stock → report balances reconcile. Early-expiry selection is FEFO only when actually configured/tested; do not fabricate automated selection.

### WF-05 Cancellations, exceptions, refunds

Appointment reschedule/cancel with reason; invalid practitioner availability error; cancelled visit not falsely billed; unavailable medicine blocks stock issue; duplicate payment cannot post; invoice cancellation/return follows accounting approval; failures show recoverable error with preserved draft.

### WF-06 Walk-in patient

Register/find patient → create walk-in encounter using current doctor + encounter type even when no appointment exists → photo/vitals/note/procedure → invoice/payment. No fake appointment ID required.

---

## 7. Every major screen template must include these elements

| Template | Used by | Required visual layout | Required interactions |
|---|---|---|---|
| A. Role Home | RC-01,DR-01,NU-01,PH-01,CA-01,MG-01,AD-01 | welcome, 4–6 large icon tiles, 2–4 small KPI cards, pending-task list | open action, date/filter, drilldown; no dead buttons |
| B. Search/Directory | SH-03/06,PH-02,PH-12,CA-03 | search above filters, results count, paginated cards/table | typeahead, filter, open detail, empty/error |
| C. Record Detail | SH-08,RC-05,DR-05,PH-03/05,CA-05 | sticky identity/header, status, contextual tabs, primary action | relevant edit/action, linked records, permission indicators |
| D. Create/Edit Form | SH-07/09,RC-04,DR-04,PH-09/13,CA-04/06 | 1–3 logical sections, mandatory indicators, in-context help | Save Draft/Submit, field validation, unsaved warning |
| E. Daily Queue | RC-02,DR-02,NU-01,CA-02 | chronological stack, readable status chips, next action | check-in/open/start consultation, refresh, filters |
| F. Calendar/Schedule | RC-03,DR-02,AD-04 | day/week, practitioner filters, colored status and time slots | select/open, book, reschedule; confirmed slot handling |
| G. Clinical Encounter | DR-05–17 | patient bar + tab navigation: History, Exam, Note, Orders, Photos, Summary | draft, clinical entry, submit confirmation, attachments |
| H. Photo Workspace | SH-12–16,DR-16 | thumbnails, capture/upload, visit/body-area tags, two-column compare | preview, crop/annotate in P1, preserve original, audit |
| I. Lab Tracker | DR-13–15 | request header, status timeline, result files, reviewer | order, track, upload, review, view original report |
| J. Inventory Report | PH-04–08,MG-05 | item/batch/warehouse filters, expiry chips, figures + trends | drill down to immutable ledger and source |
| K. Transaction Editor | PH-09/10/13,CA-04/06 | entity search, line items, totals, review/confirm | validate, draft, submit once, open resulting document |
| L. Management Dashboard | MG-01–07 | visual KPIs + readable labeled charts + drill-down | date filter, compare, source definition, export permitted |
| M. Settings/Admin | AD-01–13 | grouped settings, validation badges, list/detail | controlled master data updates and permissions |
| N. Print/Export | SYS-04 | print preview, document number, company/date | permitted export/print, watermark if draft |
| O. Global States | SYS-01–03 | skeleton/loading, no-result, error, denied, conflict | retry, return, login, duplicate warning |

**Each distinct page frame must show:** default populated state, loading, empty, validation error, forbidden/no-role, success/confirmation, narrow phone version, and tablet version for clinical/photo editors. Management metrics must have an explicit denominator/time range and source: e.g. *invoiced revenue* vs *cash received*; *appointments booked* vs *patients seen*.

---

## 8. Backend entity map and data integrity rules

| Frontend domain | Source DocType(s) | Never do this |
|---|---|---|
| Patient identity/history | `Patient`, `Patient Medical Record`, `Clinical Note` | assume free-text patient name is a Patient ID |
| Doctor & scheduling | `Healthcare Practitioner`, `Practitioner Schedule`, `Appointment Type`, `Patient Appointment` | set readonly appointment.status directly without approved action |
| Consultation | `Patient Encounter`, `Clinical Note`, `Vital Signs` | write exam text into layout field `physical_examination` or HTML view `clinical_notes` |
| Procedures | `Clinical Procedure`, `Clinical Procedure Item`, `Clinical Procedure Template`, `Service Request` | create duplicate consumption or skip validation |
| Medication | `Drug Prescription`, `Medication Request`, `Medication`, `Item` | treat prescription as dispensed stock automatically |
| Lab | `Lab Prescription`, `Lab Test`, `Service Request` | assume outside lab provider tracking exists out of box |
| Photo files | `File` + **X** photo metadata | place sensitive images in public folder or overwrite original |
| Stock | `Item`, `Batch`, `Warehouse`, `Stock Entry`, `Stock Ledger Entry`, `Serial and Batch Bundle` | change stock quantity by directly editing Item/Batch |
| Procurement | `Supplier`, `Purchase Order`, `Purchase Receipt` | mark PO fully received based on frontend counter alone |
| Billing | `Customer`, `Sales Invoice`, `Sales Invoice Item`, `Payment Entry` | link invoice directly to Patient without verifying Patient→Customer |
| Reporting | standard report APIs/queries & approved aggregate methods | count draft/canceled as completed sales or treatment |
| Authentication | `User`, roles, perms, Frappe session | ship admin API secret to browser |

**Backend integration contract:**
- Same-origin `/clinic` SPA via Frappe site or reverse proxy. Browser uses existing logged-in session and CSRF strategy appropriate to installed framework; no privileged token shipped to client.
- Use documented `GET/POST/PUT` resources for safe DocType CRUD; use whitelisted methods or doc-method routes for status transitions, submit/cancel, payment posting, stock, appointment availability, photos/linking; do not equate `PUT status=...` with executing a workflow.
- Document JSON keys are actual `fieldname`s, not UI labels. Link fields contain backend `name` keys. Child tables are arrays with child-DocType schema and correct `parentfield`; official create endpoints generally hydrate parent/child relationships server-side, but test on target release.
- Canonical responses include `name`, `docstatus`, `modified` where applicable. The frontend must handle 401/403/404/409 or server validation messages, stale-document conflicts, duplicate clicks and partial failure.
- Use server pagination/filtering for lists (target 180+ patients, 540+ appointments); no full patient-table download on login. KPI and reporting should use trustworthy server aggregates.
- Full file upload flow uses approved `/api/method/upload_file` or `/api/v2/method/upload_file` depending on version, with `is_private=1` and file record association; verify permissions and retention. Read files via authorized response; no exposed `file_url` that bypasses auth.
- Document printing/export should use existing Frappe/ERPNext print-format workflow; must not expose hidden clinical fields on an invoice receipt.

### Data relationships in UI state

`Patient.name` → `Patient Appointment.patient` → `Patient Encounter.appointment` & `.patient` → `Clinical Note` reference → `Service Request`/`Medication Request` → `Clinical Procedure.patient`/`.appointment` → `Stock Entry` / ledger; billing `Patient.customer` → `Sales Invoice.customer` → `Payment Entry.references`. Photos and outside-lab request link to Patient and Encounter via explicit referenced identifiers. This is a **target association model**; not every arrow is automatic in core.

---

## 9. Interactive states and business validations

1. **Duplicate patient:** warn on likely matching phone+name+DOB; allow authorized override with confirmation.
2. **Appointment:** don't show available if schedule blocked; repeat submit/latency cannot create two appointments.
3. **Doctor restrictions:** only authorized patient/appointment/clinical data; front-end filters are insufficient for security.
4. **Patient photos:** require consent status, private storage, explicit visit/body area, original preserved; device camera fallback to file upload.
5. **Notes:** Save Draft never implies signed/complete; show last saved time; write/submit lock based on docstatus/backend.
6. **Clinical note linkage:** handle Patient Encounter and Clinical Note separately; verify read-only references; no lost unsaved notes when leaving.
7. **Medication:** confirm drug, dose, duration; show allergy awareness without giving automated treatment recommendations.
8. **Procedure:** item qty >0, warehouse if consume, batch valid, preview stock movement, exactly-once operation and resulting ledger link.
9. **Lab:** distinguish ordered vs sample sent vs result received vs reviewed; reviewers can't silently overwrite original result.
10. **Batch expiry:** visually emphasize expired/near-expiry; expiry monitoring should reflect positive, usable balance and warehouse.
11. **Inventory:** receiving/transfer should show submitted document number; stock balance always backend-driven; never client-calculated as final authority.
12. **Invoice:** cannot charge same treatment twice; invoice total preview includes approved taxes/discounts; payment cannot exceed open amount without documented advance/credit logic.
13. **Payment:** cash/credit-card *payment mode* only; do not accept credit-card PAN/CVV. Print receipt after successful post.
14. **Audit:** show author/time/source on clinical/photo/financial changes; server stores authoritative audit trail.
15. **Timezone:** convert browser wall time to backend in approved way; date-only appointment remains clinic-local.
16. **Accessibility:** status text next to color, keyboards, tooltip for icon-only header actions, mobile form focus.

---

## 10. Acceptance scenarios (must be demo-able before release)

| ID | Scenario | Expected evidence |
|---|---|---|
| AT-01 | Register new patient, detect duplicate | single Patient ID, valid name/gender and no duplicate |
| AT-02 | Book and reschedule doctor appointment | schedule conflict validated, same logical appointment updated |
| AT-03 | Check-in and clinician queue | correct status/queue; no double check-in |
| AT-04 | Take three before photos | three authorized private File records with patient/visit context |
| AT-05 | Doctor opens prior patient history | read-only history, linked current encounter |
| AT-06 | Record vitals and clinical examination | correct Vital Signs / Clinical Note data; not fake HTML fields |
| AT-07 | Save a clinical note draft, reopen, review | content persists, patient/encounter linked through approved API |
| AT-08 | Add lab request and record result | correct Lab Test/Service Request or labeled EXT flow |
| AT-09 | Perform treatment with batch-tracked consumable | one procedure and exactly one stock movement, balances reconcile |
| AT-10 | Create invoice and take cash | customer matches patient, invoice submitted, payment allocated |
| AT-11 | Take credit-card mode payment | mode posted, no sensitive card data stored |
| AT-12 | Purchase order and receive partial stock | correct quantities, supplier and batch expiry |
| AT-13 | Transfer inventory to treatment room | source decreases, target increases, no negative/expired issue |
| AT-14 | Near-expiry report | counts only relevant available stock and correct expiry window |
| AT-15 | Role access check | receptionist can't read clinical notes, doctor can't view unrelated restricted records, cashier can't edit stock |
| AT-16 | Manager dashboard drilldown | appointment totals/revenue/payment/stock match raw submitted records |
| AT-17 | Return patient visit, before/after comparison | visit-based images correct and originals preserved |
| AT-18 | Errors: bad network, expired login, duplicate submit | no double transactions, user can recover draft |
| AT-19 | Mobile/tablet UX | no broken forms on 390px; photos and note usable on tablet |
| AT-20 | Audit and document lifecycle | correct docstatus and amendment/reversal permissions |

### Definition of done

- Every screen in §4 is accounted for as a page, tab or deliberate permission-based route; every action navigates to a supported state or a documented backend endpoint.
- Every form in §5 has a mapping from UI label to fieldname/API and is tested against **actual installed runtime metadata**.
- No direct update to readonly/status/stock ledger fields; sensitive file and doctor restrictions enforced on server.
- All P0 scenarios AT-01–AT-16, AT-18–AT-20 pass. P1 scenarios AT-17 and external lab annotations pass before advertising those features as ready.
- Clinic-staff usability check: typical receptionist registration/booking/check-in and doctor consultation can be completed without ever entering the old ERPNext Desk.

---

## 11. Screen-by-screen prompts for OpenDesign

**Global instruction:** Generate a complete responsive application design system and clickable flow prototype. Start with **information architecture, role-specific sitemap and all P0 screens**, then P1 screens. Design one shared component system and reuse it across roles. Do not invent field names or pretend screenshot mockups are working backend components. Every frame should display its `Screen ID` and a `Backend mapping` annotation that can be hidden from customer presentation.

**Reception flow:** show RC-01 Home → SH-06 Search → SH-07 New Patient → SH-12 Photos → RC-04 Book Appointment → RC-02 Queue → RC-08 Check In → RC-09 Checkout → CA-04/06 cashier handoff → RC-10 Follow-up. Show no-match, time conflict, cancel states.

**Doctor flow:** show DR-01 My Day → DR-02 My Patients → DR-04 Encounter → DR-06 History → DR-07 Exam/Vitals → DR-08 Clinical Note → DR-09 Diagnosis → DR-10 Medication / DR-11 Procedures / DR-13 Labs → DR-16 Photos → DR-17 Review & Submit. Show draft and signed/complete distinction. Patient name/header visible throughout.

**Pharmacy flow:** PH-01 → PH-07 Stock Balance → PH-04 Batch → PH-06 Expiry → PH-12/13 PO → PH-09 Receive → PH-10 Transfer → PH-11 Consumption → PH-08 Stock Ledger. Show one expired batch and how unavailable stock is clearly indicated.

**Cashier flow:** CA-01 Pending Bills → CA-04 Invoice editor → CA-05 Preview/Submit → CA-06 Payment (Cash vs Credit Card) → CA-08 Receipt. Show partial/outstanding state if allowed and safe refund review.

**Management flow:** MG-01 KPIs and charts → MG-02 Appointments → MG-03 Treatments → MG-04 Finance → MG-05 Inventory → MG-06 Purchasing, with date filter and drill-down. Use seeded realistic values, clearly marked sample.

**Admin flow:** AD-01 setup checklist → AD-02 roles → AD-03 practitioner → AD-04 schedule → AD-05 appointment types → AD-06 procedure templates → AD-07 locations → AD-08 items → AD-09 pricing → AD-11 settings → AD-13 backend readiness.

### UX guidance specific to this clinic

- Show the **main workflow as recognizable illustrated steps** on Role Home (Registration, Photos, Check-In, Consultation, Inventory, Payment). Use icons and short captions instead of paragraphs.
- Don't replace professional clinical forms with visuals where precision matters. Patient Encounter, notes, dosages, lab results and stock quantities must remain readable structured fields with explicit labels and units.
- Support doctor procedure checklist and photo comparison on tablets; desktop pharmacy needs dense but legible batch quantities, and desktop cashier needs line-item editing.
- Avoid modal-on-modal forms and unnecessarily deep navigation; use patient context header and wizard steps instead of opening a new unrelated page after every field.

---

## 12. Handoff format to OpenCode

For every screen frame export or annotate:

```
Screen ID: RC-04
Route: /clinic/appointments/new
Role: REC
Component: AppointmentBookingPage
Primary action: Create Appointment
Fields:
  patient -> Patient Appointment.patient [M, Link Patient]
  appointment_type -> Patient Appointment.appointment_type [M, Link Appointment Type]
  company -> Patient Appointment.company [M, default from session]
  appointment_date -> Patient Appointment.appointment_date [M]
  appointment_time -> Patient Appointment.appointment_time [C]
  practitioner -> Patient Appointment.practitioner [C]
API:
  list GET /api/v2/document/Patient Appointment (...)
  create POST /api/v2/document/Patient Appointment
  slot availability: exact server method TBD after code audit
States:
  loading, patient-not-found, empty-slots, overlap, validation,
  submitting, success, permission-denied, stale-document
Dependencies:
  Company / Patient / Appointment Type / Practitioner Schedule
```

**Implementation architecture:** `src/pages` by role/domain, `src/components` shared, `src/features/{patients,appointments,encounters,photos,labs,procedures,inventory,billing,reports,admin}`, `src/api/frappeClient.ts` and per-domain API adapters, `src/schemas` generated/validated from runtime DocType metadata, `src/auth`, `src/routes`, `src/i18n` and test fixtures. Prefer server-approved methods for business workflows; never build direct write access to stock ledger or accounting tables.

**Artifacts to export:** (a) responsive design system; (b) full page map; (c) role journeys; (d) Figma/OpenDesign frames with frame IDs; (e) field annotation table; (f) action/permission/state annotations; (g) dev assets and handoff notes; (h) separate list of unresolved backend assumptions.

---

## 13. MANDATORY installed-site schema & workflow verification (before OpenCode coding)

This document gives **design-complete fields**, but **not** a safe guarantee of your site's exact submit payload. Public source has multiple branches and ERPNext Settings can dynamically enforce additional requirements. Do not skip this gate.

### 13.1 Confirm site and installed apps

```bash
bench version
bench --site YOUR_SITE list-apps
```

Record Frappe, ERPNext, Healthcare/Marley, and presence/absence of `marley_frontend` and any custom apps; check enabled Healthcare Settings, Company/warehouse and custom Property Setters.

### 13.2 Fetch installed DocType metadata

With Frappe v15+ API v2, for a user with permission:

```
GET /api/v2/doctype/Patient/meta
GET /api/v2/doctype/Patient Appointment/meta
GET /api/v2/doctype/Patient Encounter/meta
GET /api/v2/doctype/Clinical Note/meta
GET /api/v2/doctype/Clinical Procedure/meta
GET /api/v2/doctype/Clinical Procedure Item/meta
GET /api/v2/doctype/Vital Signs/meta
GET /api/v2/doctype/Lab Test/meta
GET /api/v2/doctype/Item/meta
GET /api/v2/doctype/Batch/meta
GET /api/v2/doctype/Purchase Order/meta
GET /api/v2/doctype/Purchase Receipt/meta
GET /api/v2/doctype/Stock Entry/meta
GET /api/v2/doctype/Sales Invoice/meta
GET /api/v2/doctype/Payment Entry/meta
```

Extract `fieldname`, `fieldtype`, `options`, `reqd`, `mandatory_depends_on`, `depends_on`, `read_only`, `hidden`, `default`, `fetch_from`, `is_submittable`, valid child table schemas, and `permissions`. Also enumerate each `Custom Field` and active `Property Setter` and inspect controllers for validations not in JSON. `frappe.get_meta()` in server code includes custom fields and property setters. If the v2 meta route is denied, do this from admin/bench rather than weakening permissions.

### 13.3 Confirm exact methods/behaviors using disposable test records

- Appointment availability and booking method; check-in (`Patient Appointment` native vs `marley_frontend` queue), reschedule/cancel workflow, practitioner schedule and patient/customer automatic linking.
- Clinical Note create and link to encounter (read-only `reference_doc/reference_name` issue); note sign-off policy; encounter Submit and generated orders.
- Clinical Procedure `Complete & Consume` and Serial/Batch Bundle support in installed ERPNext; ensure exactly-once ledger effect.
- Lab prescription → Service Request → Lab Test and outside-lab model; which statuses/actions actually exist.
- Sales Invoice creation/submit and Payment Entry allocation for Cash/Credit Card; no double stock deduction.
- Photo file upload constraints, private file access, attachment to Patient and Encounter.

### 13.4 Required pre-design/pre-build checklist

- [ ] Backend versions locked; installed schemas exported for every DocType above.
- [ ] Required/default/conditional/readonly field mapping reviewed with backend engineer.
- [ ] Master-data dependencies enumerated (gender, department, practitioner, slot, appointment type, item, batch, warehouse, tax, payment account).
- [ ] Custom entities `Clinic Photo`, `Photo Annotation`, `External Lab Request`, and consent tracking accepted or mapped to native alternatives.
- [ ] Patient→Customer linkage and revenue-per-treatment source defined.
- [ ] Role-specific permission tests, supported statuses and error models documented.
- [ ] OpenDesign frames updated when installed schema differs.

---

## 14. Source code / documentation references (checked 2026-11-03)

**Marley (`develop`):**
- Patient: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/patient/patient.json
- Patient Appointment: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/patient_appointment/patient_appointment.json
- Patient Encounter: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/patient_encounter/patient_encounter.json
- Clinical Note: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/clinical_note/clinical_note.json
- Vital Signs: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/vital_signs/vital_signs.json
- Clinical Procedure: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/clinical_procedure/clinical_procedure.json
- Clinical Procedure Item: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/clinical_procedure_item/clinical_procedure_item.json
- Lab Test: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/lab_test/lab_test.json
- Drug Prescription: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/drug_prescription/drug_prescription.json
- Lab Prescription: https://raw.githubusercontent.com/earthians/marley/develop/healthcare/healthcare/doctype/lab_prescription/lab_prescription.json
- Marley Frontend README: https://github.com/earthians/marley_frontend (additional app, NOT assumed installed)

**ERPNext (`version-16`):**
- Item: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/stock/doctype/item/item.json
- Batch: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/stock/doctype/batch/batch.json
- Purchase Order: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/buying/doctype/purchase_order/purchase_order.json
- Purchase Receipt: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/stock/doctype/purchase_receipt/purchase_receipt.json
- Stock Entry: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/stock/doctype/stock_entry/stock_entry.json
- Sales Invoice: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/accounts/doctype/sales_invoice/sales_invoice.json
- Payment Entry: https://raw.githubusercontent.com/frappe/erpnext/version-16/erpnext/accounts/doctype/payment_entry/payment_entry.json

**Frappe API:** https://docs.frappe.io/framework/user/en/guides/integration/rest_api ; `frappe.get_meta()` https://docs.frappe.io/framework/user/en/api/document

**Important:** Links represent current public reference source, not validated site-specific payloads. Consult the installed application's repository version, patches, Custom Fields, Property Setters and controller methods before executing write operations.
