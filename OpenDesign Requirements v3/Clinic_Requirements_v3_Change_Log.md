# Demo Clinic — Functional Requirements v3: OpenDesign Change Log

**Version:** 3.0 | **Baseline:** v2.0 | **Prepared:** 03 November 2026
**Purpose:** Apply changes to the **existing** v2 OpenDesign project. Do not rebuild all screens.
**Important:** PreClinic is a commercial Bootstrap/HTML UI template, NOT verified running clinic software or a compatible Marley/ERPNext backend. Use interaction ideas and information hierarchy, NOT its code, brand, imagery or licensed assets.

## Decision summary

- **Preserve 37 existing v2 design surfaces and all 97 historical v1-to-v2 mappings.** No new top-level route is mandatory: the changes update existing pages, tabs, drawers and widgets.
- **P0 improvements:** action-oriented receptionist/doctor/pharmacy dashboards; accurate check-in/queue; patient summary; lab upload/review; urgency-based inventory alerts; real report definitions; working hours; TH/EN everywhere.
- **P1 optional designs:** appointment Kanban, appointment-request intake, queue tokens/display board, queue analytics, stock value at risk and service-item revenue breakdown where available.
- **Keep the existing clinical core:** one canonical DOC-02 visit editor; original photo files, consent and Before/After annotation P0; manual external lab P0; pharmacy/purchasing and cashier P0. No duplicated medical history.
- **Exclude:** patient portal, AI diagnosis/risk prediction, telehealth, inpatient wards/ICU, insurance, payroll, CRM/loyalty/marketing, website content management.
- **Implementation gate:** any new actions that change server state (Check In, Called, Dispense, Reorder, Write-off, Send, Receive, Approve, Cancel, Complete) require installed-site contract verification; never wire a UI button to generic status-field updates.

## Exact differences from v2

| Change ID | Action | Priority | Existing v2 surface(s) to update | Required design change | Reference demo page |
|---|---|---|---|---|---|
| V3-01 | ENHANCED | P0 | REC-01 | Reception dashboard: four operational counts, immediate shortcuts, actionable appointment preview | [PreClinic](https://preclinic.dreamstechnologies.com/html/receptionist-dashboard.html) |
| V3-02 | ENHANCED | P0 | REC-02 | Queue status/arrival cards, check-in action and doctor handoff with accurate state mapping | [PreClinic](https://preclinic.dreamstechnologies.com/html/queue-live.html) |
| V3-03 | NEW-OPTION | P1 | REC-02, MGR-02 | Optional token display board, ticket assignment and waiting-time analytics; separate queue backend required | [PreClinic](https://preclinic.dreamstechnologies.com/html/queue-analytics.html) |
| V3-04 | ENHANCED | P0 | REC-03, REC-04 | Appointment day/week calendar, doctor filters, prominent availability and conflict validation | [PreClinic](https://preclinic.dreamstechnologies.com/html/appointment-calendar.html) |
| V3-05 | NEW-OPTION | P1 | REC-03, REC-04 | Appointment Kanban mode as a switchable presentation of the same backend records | [PreClinic](https://preclinic.dreamstechnologies.com/html/appointment-kanban.html) |
| V3-06 | NEW-OPTION | P1 | REC-04 | Appointment request inbox for manually received phone/walk-in requests; no patient portal assumed | [PreClinic](https://preclinic.dreamstechnologies.com/html/appointment-requests.html) |
| V3-07 | ENHANCED | P0 | PAT-03 | Patient summary card, latest vitals with source date, last visit and clinical history timeline | [PreClinic](https://preclinic.dreamstechnologies.com/html/patient-details.html) |
| V3-08 | ENHANCED | P0 | PAT-04, PAT-05 | Patient photo/consent summary anchored in patient profile; retain original non-destructive annotation requirements | [PreClinic](https://preclinic.dreamstechnologies.com/html/patient-details.html) |
| V3-09 | ENHANCED | P0 | DOC-01 | Doctor My Day: next patient primary action, pending drafts/results/follow-ups and own availability | [PreClinic](https://preclinic.dreamstechnologies.com/html/doctor-dashboard.html) |
| V3-10 | ENHANCED | P0 | DOC-02 | Doctor visit workspace uses status-aware guidance, patient header and one entry for history/exam; no new clinical data duplication | [PreClinic](https://preclinic.dreamstechnologies.com/html/doctor-dashboard.html) |
| V3-11 | ENHANCED | P0 | DOC-04, DOC-02 | Lab worklist: pending collection/sent/received/reviewed states with manual external-lab provider mapping | [PreClinic](https://preclinic.dreamstechnologies.com/html/lab-tests.html) |
| V3-12 | ENHANCED | P0 | DOC-04, DOC-02 | Lab report upload panel with test linkage, privacy, review and attachment provenance | [PreClinic](https://preclinic.dreamstechnologies.com/html/upload-reports.html) |
| V3-13 | ENHANCED | P0 | NUR-01, NUR-02 | Nurse/assistant assigned tasks and vitals; omit ward, bed, ICU and automatic alerts | [PreClinic](https://preclinic.dreamstechnologies.com/html/nurse-dashboard.html) |
| V3-14 | ENHANCED | P0 | PHA-01 | Pharmacy dashboard: shortages, expiring stock, outstanding purchase items and quick transaction access | [PreClinic](https://preclinic.dreamstechnologies.com/html/pharmacist-dashboard.html) |
| V3-15 | ENHANCED | P0 | PHA-03 | Expired/7/30/90-day expiry urgency and drilldown by item, warehouse, available batch quantity | [PreClinic](https://preclinic.dreamstechnologies.com/html/expiry-alerts.html) |
| V3-16 | ENHANCED | P1 | PHA-03, MGR-02 | Value at risk based on verified current valuation and usable balance; no fabricated figures | [PreClinic](https://preclinic.dreamstechnologies.com/html/expiry-alerts.html) |
| V3-17 | ENHANCED | P0 | PHA-04 | Stock balance KPI: positive/onhand vs usable, low/out-of-stock and per-location filters | [PreClinic](https://preclinic.dreamstechnologies.com/html/stock-management.html) |
| V3-18 | ENHANCED | P0 | PHA-07 | Low-stock row to prefilled draft PO/replenishment handoff; actual ERPNext document lifecycle | [PreClinic](https://preclinic.dreamstechnologies.com/html/purchase-orders.html) |
| V3-19 | ENHANCED | P0 | PHA-05, PHA-07 | PO partial-receipt progress, expected arrival and supplier source; ERPNext statuses authoritative | [PreClinic](https://preclinic.dreamstechnologies.com/html/purchase-orders.html) |
| V3-20 | ENHANCED | P0 | PHA-06 | Controlled stock adjustment and transfer: reason, source transaction, permission and confirm; never direct-write quantity | [PreClinic](https://preclinic.dreamstechnologies.com/html/stock-management.html) |
| V3-21 | ENHANCED | P0 | CAS-01, CAS-03 | Cashier due/pending and same-day cash/card collection summaries with drilldowns to posted entries | [PreClinic](https://preclinic.dreamstechnologies.com/html/receptionist-dashboard.html) |
| V3-22 | ENHANCED | P0 | MGR-01, MGR-02 | Manager dashboard: appointment funnel, visit completion, doctor availability, invoices vs collected payments | [PreClinic](https://preclinic.dreamstechnologies.com/html/index.html) |
| V3-23 | NEW-OPTION | P1 | MGR-02 | Revenue by invoice service item/treatment category when source mapping proven; avoid asserting clinical procedure revenue | [PreClinic](https://preclinic.dreamstechnologies.com/html/index.html) |
| V3-24 | ENHANCED | P0 | ADM-03 | Staff working days/hours, break periods and availability policies mapped to practitioner schedule | [PreClinic](https://preclinic.dreamstechnologies.com/html/working-hours-settings.html) |
| V3-25 | ENHANCED | P0 | APP-02 | Role-aware notifications and actionable work items, with authorized navigation and dismissed/read state | [PreClinic](https://preclinic.dreamstechnologies.com/html/receptionist-dashboard.html) |
| V3-26 | ENHANCED | P0 | APP-02, APP-04 | Icon-led primary actions, compact status chips, consistent master/detail and mobile stacked cards | [PreClinic](https://preclinic.dreamstechnologies.com/html/index.html) |
| V3-27 | ENHANCED | P0 | APP-01, APP-02, APP-04 | TH/EN across new labels, badges, alerts, calendar, graphs and print; preserves v2 draft/locale guarantees | [PreClinic](https://preclinic.dreamstechnologies.com/html/language-settings.html) |
| V3-28 | CLARIFIED | P0 | ADM-05 | All interactive PreClinic examples are reference patterns only; installed Frappe contracts remain unverified | [PreClinic](https://preclinic.dreamstechnologies.com/html/index.html) |
| V3-29 | DEFERRED | P2 | Excluded / no route | Exclude hospital wards/beds, AI diagnosis, patient risk scoring, CRM, telehealth, payroll, insurance, marketing and social modules | [PreClinic](https://preclinic.dreamstechnologies.com/html/index.html) |
| V3-30 | ENHANCED | P0 | APP-04, REC-02, PHA-03 | Acceptance tests for duplicate check-in, stale queue, expired stock, transaction retry and permission-safe UX | [PreClinic](https://preclinic.dreamstechnologies.com/html/queue-live.html) |
| V3-31 | ENHANCED | P0 | REC-05, CAS-01 | Reception-to-cashier handoff with unpaid/paid state, no clinical-note exposure and authorized follow-up booking | [PreClinic](https://preclinic.dreamstechnologies.com/html/receptionist-dashboard.html) |

**Legend:** ENHANCED = existing requirement now more precise; NEW-OPTION = a distinct optional UI state/tab, not new mandatory route; CLARIFIED = integration truth; DEFERRED = not in target release. Detailed functional fields and acceptance below appear in the full v3 specification.

## OpenDesign incremental update order

1. Update shared shell and reusable visual components first: notification/task dropdown, status chips, filter drawers, responsive list/card, chart drilldowns, TH/EN variants.
2. Update Reception home and Queue, Appointment calendar/editor, Patient detail.
3. Update Doctor My Day and Lab upload/review inside the existing single Visit workspace.
4. Update Pharmacy home, stock/expiry and purchasing cards; keep existing stock transactions.
5. Update Cashier/Management/Admin panels, then all P0 permissions, errors and mobile designs.
6. Produce a **before/after v2→v3 diff per affected surface**. Preserve unmodified v2 frames and route IDs.

## OpenDesign per-screen change packet format

For each affected surface, submit `surface_id`, `v2_frame_id`, `action (modify/optional state/defer)`, `changed widgets`, `new/changed fields`, `new action states`, `TH strings`, `EN strings`, `responsive frames`, `clickable transition`, `backend verification flag` and `acceptance tests`. No dead icon/button and no copying screenshots as production UI.

## Source and licensing notes

The referenced PreClinic pages describe example UI patterns, not validated runtime business logic. License and intellectual-property restrictions apply if anyone later wants to reuse its source, images, screenshots or graphical assets. This specification uses independently authored requirements only.

