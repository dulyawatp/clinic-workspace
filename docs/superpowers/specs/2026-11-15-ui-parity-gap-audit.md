# UI parity gap audit (design vs React, 2026-11-15)

Read-only audit of the OpenDesign project "Clinic Application Design" (37
surfaces, 7 roles, v3 P0 packets) against the current React app. Data-wiring
legend: **native** = Frappe whitelisted method; **proxy-live** = `/api/live/*`
to the isolated SIT site; **fixture** = proxy read/MSW, fails closed in native
build; **unavailable** = no upstream.

## Surface inventory

15 of 37 designed surfaces have a React route. 22 have no route at all
(DOC-01, DOC-03, DOC-04, NUR-01, NUR-02, PHA-02–07, CAS-01–03, MGR-01–02,
ADM-01–05, PAT-02). Partial present surfaces: APP-02, APP-04, PAT-03, PAT-04,
PAT-05, REC-04, REC-05, DOC-02, PHA-01.

| ID | Design route | React status | Key missing components/states | Wiring | Backend notes |
|---|---|---|---|---|---|
| APP-01 | /clinic/login | implemented SignInPage | — | proxy auth | verified |
| APP-02 | /clinic (profile) | partial — route is HomePlaceholder; profile at separate `/clinic/profile`; no role home dispatch | language persistence; work-item dismiss | fixture | SIT finding #3 |
| APP-03 | /clinic/search | implemented | — | fixture | verified |
| APP-04 | /clinic/system | partial (static gallery + print templates) | integration-status/audit truth; no router role gate | static | admin: no backend |
| PAT-01 | /clinic/patients | implemented directory | duplicate-candidate state | native | verified |
| PAT-02 | /clinic/patients/new | **missing route** | 10-field create + duplicate detect + local draft + allergy/consent fields (React inline form has 3 fields) | native write | create/edit contract blocked |
| PAT-03 | /clinic/patients/:id | partial (header, 6 tabs, vitals, timeline) | consent-evidence block, clinical-redaction notice, last-visit/practitioner summary card | native + fixture tabs | read done; detail/timeline unavailable |
| PAT-04 | .../photos | partial (gallery, consent chip, upload) | consent capture (`write.photo.consent` unused), body-area tagging edit, intake flow | proxy-live | photos: save/status only |
| PAT-05 | .../photos/compare | partial (side-by-side + slider) | **annotation engine absent** (shapes, undo/redo, versions, layers) | fixture | no annotation storage |
| REC-01 | /clinic/reception | implemented (KPIs + drilldowns) | — | fixture KPIs | appointment family done |
| REC-02 | /clinic/reception/queue | implemented | — | fixture | unavailable on SIT |
| REC-03 | /clinic/appointments | implemented (calendar + filters + grid) | — | fixture availability | unavailable on SIT |
| REC-04 | /clinic/appointments/:id | partial (detail, reschedule, check-in) | cancel + no-show actions; create-return-visit | native view + writes | cancel contract absent |
| REC-05 | /clinic/reception/checkout | partial (handoff, outstanding, invoice list, payment gate) | follow-up booking action; incomplete-visit state | fixture | billing contract absent |
| DOC-01 | /clinic/doctor | **missing** | My Day, work counts, next-patient/continue-draft, own availability | registry `my-day-counts`, `clinical-next-patients` unused | no clinical backend |
| DOC-02 | /clinic/visits/:id | partial — 10 sections, 3 placeholders (procedures/lab/photos) | **entire write loop**: save/review/complete/amend, readiness guidance, lock-on-completed, external-lab panel | fixture | no contract |
| DOC-03 | /clinic/procedures/:id | **missing** | procedure detail, consumables, batch/warehouse, complete-once | registry unused | none |
| DOC-04 | /clinic/labs/:id | **missing** (28 `lab.*` keys unused) | 6 states, 18 result fields, upload/review/private file | registry unused | none |
| NUR-01 | /clinic/nurse | **missing** | assigned cohort, duties tiles | registry ready | none |
| NUR-02 | /clinic/nurse/tasks | **missing** | vitals form, prep checklist, aftercare, lab dispatch | registry ready | none |
| PHA-01 | /clinic/pharmacy | partial (read-only item list) | 5 warehouse KPIs, expiry alert, chart, movements, shortcuts | proxy-live stock | no stock/batch/PO contract |
| PHA-02–07 | /pharmacy/{items,batches,balances,receive,movements,purchasing} | **missing** | item detail; expiry windows; balances/ledger; PO receipt; transfer/usage/reconcile; purchasing | — | none |
| CAS-01–03 | /clinic/{cashier,invoices,payments} | **missing** | pending bills/shift; invoice lines/tax/discount; allocation/receipt/refund | — | none; no CAS actor |
| MGR-01–02 | /clinic/management{,/reports} | **missing** | 8 source KPIs, period, funnel; 5 report tabs + exports | — | none; no MGR actor |
| ADM-01–05 | /clinic/admin* | **missing** | setup, users/roles, masters, settings, audit/readiness | — | none; no ADM actor |

## Cross-cutting gaps

- Router has **no per-role guard** (only session gate): `/clinic/system`,
  `/clinic/finance`, photo routes reachable by roles the design excludes.
- Nav lacks doctor/nurse/cashier/management/admin destinations.
- `nav.ts` hides pharmacy/finance/live-photos in native mode; REC can
  deep-link photo routes regardless.
- Locale preference not persisted; work items not dismissible (v3 P0).
- React-invented routes with no design counterpart: `/clinic/finance`
  (4 aggregate numbers), `/clinic/lab/photos` (+`:id`) LivePhotoWorkspace.
- Safety affordances not in the design (StatusMappingNote,
  SessionCheckPanel, WriteGate/RoutedControl, skeletons) — keep.
- 488 `ops.v3.*` + 28 `lab.*` + `surface.*` translation keys pre-staged,
  zero referenced in `.tsx` — surfaces planned, unbuilt.
- Only DOC/NUR/REC synthetic identities exist; PHA/CAS/MGR/ADM unverifiable
  through real roles.

## Fixture coverage vs designed components (2026-11-15 audit)

Prototype sample data lives in `assets/js/data-core.js` (patients 180,
visits, appointments, consent `{photo,at,version}`), `data-ops.js`
(warehouses, 24 items, 33 batches, 66 movements, 4 suppliers, 5 POs, 113
invoices, payments, staff/schedules/templates, external labs, audit/system
checks), with KPIs, thresholds, PO progress, due balances, availability and
VAT/discount/paid states computed by prototype helper logic (seeded RNG,
relative dates) — not stored truth.

Components with adequate fixtures: pharmacy items/batches/movements/thresholds
(ledger synthesized to sum), PO list + partlyReceived receipt, suppliers,
cashier invoice lines/VAT/paid and payment allocations, management period
metrics (tables), admin staff/schedules/tiles + audit, nurse cohort vitals
sample, doctor today rows + visit draft DC-VIS-2041, lab ordered/sent/
received/reviewed (DC-LAB-77), checkout invoice links, PAT-02 form fields
including allergy known/none and consent true/false/null + duplicate
candidates.

Components with **no backing fixture** (designed but unproven): photo gallery
and all annotation/version samples (`DC.data.photos = []`); consent evidence
beyond `{photo,at,version}` (no signer/evidence/date, withdrawn/guardian/
expired, consent form record, redaction); patient identity/national-ID and
persisted emergency contact; lab `collected`/`cancelled` states, private
file, category/priority/summary (18-field panel mostly null), cancel action;
nurse duties tiles/upcoming procedures/assignment source, prep/aftercare
seeding; DOC-01 recent encounters; DOC-02 native lab section, amendment
sample; DOC-03 per-consumable lines/template-driven batches (renderer
hardcodes IT-07/qty 1, batch B-2411 suspect); cashier shift fixture, draft/
cancelled invoice or payment, discount-permission control, refund approval
sample; MGR-01 chart component + confirmed-revenue data; MGR-02 workload tab
(`DC.metrics.doctorWorkload` exists unused), exports/inventory-trend/PO
history; ADM-02 permission matrix, ADM-03 practitioners tab + labProvider
master, ADM-04 stock-threshold settings UI; REC-05 missing-invoice and
incomplete-visit samples; REC-04 return-visit action; PHA-06 transfer
movement rows + reconcile requests; inventory warehouse missing on POs,
dangling receipt DC-PR-0019, ledger sorted reversed vs its label; company
IDs null on every invoice/payment; fixed consent dates drift vs relative
dates.

Port implication: do not copy computed rows as truth — thresholds, PO
progress, due balances, stock usability, availability, tax/discount/paid and
KPI series must be recomputed client-side per prototype logic with a frozen
test clock; the synthesized stock ledger must be replaced by real
transactions when a stock contract exists.

## Top value gaps (audit order)

DOC-02 write loop; DOC-02 placeholder sections; DOC-01 My Day; DOC-04 lab
surface; NUR-01/02; PHA-02–07; CAS/MGR/ADM (11 surfaces); PAT-02 create;
PAT-05 annotation engine; REC-04 cancel/no-show + router role guards.
