# Demo Clinic — Backend Contract Verification Plan v2.0
## OpenCode engineering gate: validate installed Marley Health + ERPNext before implementing mutations

**Companion to:** `Clinic_Functional_Requirements_v2.md` and `Clinic_Screen_Manifest_v2.json`  
**Status:** TECHNICAL CHECKLIST, **NOT** a verified API schema for the deployed site.  
**Applies to:** existing self-hosted Frappe/Marley Health/ERPNext and custom React frontend.

## 1. Roles of each artifact

- **Functional Requirements v2** defines what patients/staff see, enter and accomplish; OpenDesign can begin now.
- **Screen Manifest v2** verifies design coverage across 37 consolidated surfaces / 97 v1 entries.
- **This contract checklist** tells OpenCode what to inspect/test before committing REST payload structures, mutations, transactional steps or any new DocTypes.
- **Output from site verification** must be a new, signed-off file `Backend_Contracts_Installed_<date>.md` or typed schemas, including actual active versions, real fieldnames, exact verified methods, validations and test evidence. **Do not rename this checklist into a 'verified' contract.**

## 2. First inventory the deployed site

Use an authorized admin / backend developer account:

```bash
bench version
bench --site YOUR_SITE list-apps
```

Record Frappe, ERPNext, Marley/Healthcare commit/version, custom app versions, presence of `marley_frontend`, company `Demo Clinic` (`DC`), currencies, local timezone, Healthcare Settings, Stock Settings, batch/Serial and Batch Bundle behavior, payment modes, practitioner/service-unit setup, roles and active permission rules. Record `Custom Field` and `Property Setter` changes. **Never apply migrations or modify production settings merely to inspect.**

## 3. Schema inspection and endpoint testing

Frappe documents CRUD and remote methods over REST API v1/v2. When available and permitted in the installed version, examples include:

```text
GET /api/v2/doctype/Patient/meta
GET /api/v2/doctype/Patient%20Appointment/meta
GET /api/v2/doctype/Patient%20Encounter/meta
GET /api/v2/doctype/Clinical%20Note/meta
GET /api/v2/doctype/Clinical%20Procedure/meta
GET /api/v2/doctype/Vital%20Signs/meta
GET /api/v2/doctype/Item/meta
GET /api/v2/doctype/Batch/meta
GET /api/v2/doctype/Stock%20Entry/meta
GET /api/v2/doctype/Purchase%20Order/meta
GET /api/v2/doctype/Purchase%20Receipt/meta
GET /api/v2/doctype/Sales%20Invoice/meta
GET /api/v2/doctype/Payment%20Entry/meta
```

Do not assume a metadata endpoint is accessible to ordinary staff. A developer/admin can use authorized server inspection (`frappe.get_meta(doctype)`) if REST returns 403. Metadata inspection must include fieldname, fieldtype, options, reqd, mandatory_depends_on, read_only, default, hidden, fetch_from, child table schema, status docfields, exact dynamic links and allowed values. **Controller validation may be stricter than DocType JSON**, so test with disposable records, both successful and invalid submissions.

For each contract deliver:

| Required evidence | What to provide |
|---|---|
| Contract ID | `PATIENT-CREATE`, `APPOINTMENT-BOOK`, `APPOINTMENT-CHECKIN`, etc. |
| Actual installed version | package/git version and revision, date |
| Role permission | authorized roles and record-level restrictions |
| Request API | exact HTTP verb and path or whitelisted method, no guessed URLs |
| Field map | UI key → backend fieldname/child table/extension field, required conditions, defaults |
| Response | actual document ID/name, status, timestamps, canonical errors |
| Workflow | draft/submit/complete/cancel side effects, audit and idempotency |
| Test | test record IDs/screenshots or logs with sensitive information redacted |
| Frontend behavior | retry/conflict, success navigation, invalid or missing dependencies |
| Status | `VERIFIED` / `EXTENSION_REQUIRED` / `BLOCKED` / `DECISION_REQUIRED` |

## 4. Domain verification matrix

| Contract | Existing backend entities | Key unknowns to resolve before coding |
|---|---|---|
| `PATIENT-CREATE/UPDATE` | Patient, Gender, Contact, Customer | actual mandatory fields/defaults, Thai names, duplicate rules, Patient→Customer link, permitted identity fields |
| `PATIENT-SEARCH` | Patient and permissions | filters, pagination, phone lookup, sensitive field exposure and unauthorized query behavior |
| `CONSENT-CAPTURE` | **New/verified consent record** | consent type/scope/version, authorized signer, recording, withdrawal and edit restrictions, photo gating |
| `PHOTO-UPLOAD` | Frappe File and **proposed Clinic Photo** | private upload method, allowed types/limits, link to Patient before appointment/check-in, later encounter link, file access/retention |
| `PHOTO-ANNOTATE` | **proposed Clinic Photo Annotation** | annotation schema (vector coordinates, version, original, saved/rendered preview), zoom/crop transforms, audit/permissions |
| `PHOTO-COMPARE` | proposed photo metadata/pair links | one/multiple visits, same body area/viewpoint, before/after tags, original file immutable |
| `APPOINTMENT-BOOK` | Patient Appointment, Appointment Type, Healthcare Practitioner, Practitioner Schedule | appointment_for, slot method, duration, conflict/overlap rules, precise status workflow |
| `APPOINTMENT-CHECKIN` | Patient Appointment; optional Marley Frontend queue | exact installed check-in method, check-in settings, queue DocTypes, double-action behavior |
| `ENCOUNTER-CREATE` | Patient Encounter | mandatory patient/practitioner/type/date/time, appointment link, walk-in behavior, submit_orders_on_save side effects |
| `CLINICAL-NOTE-SAVE` | Clinical Note, Patient Encounter | exact note fields, linkage method if `reference_doc/reference_name` read-only, who can edit/sign, draft and finalized content rules |
| `EXAM-FINDINGS` | Vital Signs, Clinical Note; **possible custom structured Exam** | `Patient Encounter.physical_examination` is not assumed editable; authoritative storage of exam content, vitals units/ranges |
| `MEDICATION-ORDER` | Drug Prescription, Medication Request, Item | medication field and dose/interval switch, forms/duration, downstream request generation and dispensing status |
| `PROCEDURE-REQUEST/COMPLETE` | Clinical Procedure, template/items, Service Request | permitted procedure state methods, warehouse on consume, batch/bundle selection, exactly-once Stock Entry creation |
| `NATIVE-LAB` | Lab Prescription, Lab Test, Service Request | native order/result statuses, template/patient_sex requirements, creation/linking behavior |
| `EXTERNAL-LAB` | **proposed Clinic External Lab Request** | provider master, order→sent→received→reviewed, private files, staff/doctor permissions, interpretation and review lock |
| `STOCK-BALANCE/EXPIRY` | Item, Batch, Warehouse, Stock Ledger, Serial and Batch Bundle | positive available quantity by lot/warehouse, expiry thresholds, FEFO manual/automatic behavior |
| `PURCHASE-ORDER` | Supplier, Purchase Order | transaction/date/item taxes/currency/schedule, partial receipts/status |
| `STOCK-RECEIVE` | Purchase Receipt, Batch/serial bundle | tax/pricing defaults, new lot/expiry, GL/valuation side effects, document lifecycle |
| `STOCK-TRANSFER/RECONCILE` | Stock Entry, Stock Reconciliation | source/destination, tracked stock, negative/expired stock policy and separate reconciliation privilege |
| `SALES-INVOICE` | Customer, Sales Invoice, item child | billing item mapping from procedure, discount/taxes, duplicate charge, stock movement conflict |
| `PAYMENT-RECEIVE` | Payment Entry, Mode of Payment, company accounts | mode mapping Cash/Card, reference allocations, server posting, partial and repeat payment, receipt generation |
| `OWNER-METRICS` | allowed Reports/NumberCard-backed APIs/aggregates | exact KPI source, submitted-only logic, manager permissions and filters |
| `AUTH/ROLES` | User, Role, User Permission, DocType Permissions | clinic roles, patient scope, same-origin session, file access, CSRF and unauthorized states |
| `LOCALE-PREFERENCE` | Frappe User.language and/or approved custom user preference | persistence/sync method, authenticated language resolution, API error locale and custom React locale |
| `BILINGUAL-PRINT` | print formats/translations | TH/EN output choice, accounting requirements, unmodified clinical note contents and Thai fonts |

## 5. Special critical business rules

### Clinic Photo lifecycle

1. Create `Patient` successfully.
2. Verify consent is present for the intended clinical photo processing under the clinic policy.
3. Upload an original with `is_private=1`, Patient ID and photo metadata; do not rely on `Patient.image` for a treatment gallery.
4. If no Encounter exists yet, maintain patient/appointment/intake association; after Check In/Encounter creation, link the **same original** to visit context via an authorized server method. Idempotent association; no orphan or duplicate image.
5. Store shape/arrow/text annotation as a versioned separate layer referring to original; never overwrite original. Save doctor identity and audit trail.
6. Define retention, revocation, deletion, consent scope and exports before production; these require clinic approval.

### One clinical workspace, multiple backend records

`DOC-02` is one frontend experience. Backend may consist of Patient Encounter + Clinical Note + Vital Signs + Service Request + Clinical Procedure + File + proposed External Lab records. Create a **visit session orchestration strategy** with exact persistence boundaries, draft ID, per-section completion status, known/unknown fields, link integrity, failures and duplicate action keys. History/exam are entered once and rendered consistently; do not submit text to display-only fields.

### Safe transactions

- Do not use generic REST `PUT status=Completed` as substitute for a verified submit/complete method.
- Never directly update quantity on Item, Batch or Stock Ledger Entry.
- Distinguish prescribed medications from actual dispensed/consumed and invoiced items.
- Always show source warehouse, batch/expiry, quantity and effect before stock submission; verify duplicate-click handling.
- Link patient to accounting Customer before invoicing; validate discount/tax settings and prevent the treatment stock being deducted both by procedure and invoice.
- Payment Entry `mode_of_payment=Credit Card` does not mean an online card gateway is integrated; never send PAN/CVV to this application.

## 6. Thai/English integration decisions

- Frontend UX supports `th` and `en` with **Thai default for new clinic users**, per-user preference and immediate switch without data loss.
- Frappe has a separate User-language resolver and translation catalogs. Decide whether frontend language preference is synchronized with `User.language` (only by allowed method), stored in permitted custom preference, or both.
- **Do not assume** browser `Accept-Language` changes the language of authenticated Frappe requests. Translate known frontend messages from stable error codes; show safe fallback for unexpected server messages.
- Thai UI copy is fully reviewed, and component labels have stable translation keys; never translate persisted status values, backend IDs or staff-authored clinical notes.
- Text date formatting vs backend date storage is separate; confirm clinic's Thai Buddhist/Gregorian year policy. Start with **Gregorian backend** and explicit locale formatting, no unapproved year conversion.
- Decide whether print/export language is user selection or clinic default, per print type. Statutory Thai receipts/invoices must be checked separately with clinic's accountant.
- If custom Frappe app server messages are localized, follow the installed version's CSV/PO translation packaging; this is separate from React's i18n catalog.

## 7. No-code vs custom extension decision register

| Requirement | Decision in v2 |
|---|---|
| React frontend and icon-first design | Custom frontend is intended, with existing Frappe backend |
| Patient/appointment/encounter/invoice/stock masters | Reuse existing backend where possible; site must verify |
| Photo multi-gallery, original association and annotations | Custom UI **and likely custom Frappe entities/methods** |
| External-lab outside-provider tracking | New/extended backend workflow likely needed |
| One clinical editor combining records | New orchestration layer likely needed; source records kept authoritative |
| Language switching | Frontend i18n + verified preference persistence; does not require altering Marley core |
| Role dashboards/reports | Existing aggregation where possible, custom approved aggregates if needed |
| Native Marley/ERPNext core source modifications | Avoid; extensions should be isolated in a separate app |

## 8. Required verification deliverables for OpenCode

1. Actual installed app version and config snapshot, sanitized.
2. Field/DocType schema export including server defaults and Custom Field modifications.
3. Finalized API and status-transition tables with test evidence for each domain above.
4. Server-enforced permission matrix with positive and negative tests for all seven roles.
5. Proposed extension model definitions for Clinical Note linkage if needed, Clinic Consent, Clinic Photo, Annotation, External Lab, and any orchestration methods.
6. Locale persistence contract, Thai/English error translation strategy, printing policy.
7. Transaction/idempotency and draft recovery test plan.
8. `Backend_Contracts_Installed_<date>.md` signed off before connecting destructive or accounting/clinical mutation buttons to production.

## References

- https://docs.frappe.io/framework/user/en/guides/integration/rest_api
- https://docs.frappe.io/framework/user/en/python-api/language
- https://docs.frappe.io/framework/user/en/translations
- https://docs.frappe.io/framework/user/en/guides/basics/translations
- https://github.com/earthians/marley
- https://docs.frappe.io/erpnext
