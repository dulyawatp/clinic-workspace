# OpenDesign — UPDATE EXISTING Demo Clinic v2 UI to v3 (do not restart)

You are updating an **existing** OpenDesign UX/UI project for **Demo Clinic**. The system uses existing Frappe + Marley Health + ERPNext backend and a responsive React frontend. Your task is **design and UX handoff**, not backend implementation.

## Read these attached files in priority order
1. `Clinic_Requirements_v3_Change_Log.md` — what changed in v2; apply IDs `V3-01` to `V3-31`.
2. `Clinic_Functional_Requirements_v3.md` — complete authoritative functional scope, including v2 baseline and v3 detailed additions.
3. `Clinic_Screen_Manifest_v3.json` — keep the existing **37** surfaces and **97** inherited legacy coverage; `v3_change_ids` identifies frames to revise.
4. `Clinic_Backend_Contract_Verification_v3.md` — annotate unverified operations; do not assume Marley methods exist.

## Main instruction
**Incrementally update existing v2 pages and components. Do not regenerate all screens or introduce PreClinic's hospital application or copyrighted design assets.** Reuse existing design tokens and the visual, premium, icon-first clinic style. Its HTML demo is inspiration for interaction structure only.

## Required v3 updates
- Reception: actionable KPI cards, today's appointment rows with valid action, accurate check-in/queue states, day/week calendar and doctor availability. Optional Kanban/request inbox/token board are clearly flagged P1.
- Patient: recognizable profile summary, last visit and timestamped vitals; linked timeline, photos and consent. Preserve non-destructive Before/After shapes and comparison P0.
- Doctor: next patient, open draft, lab results to review, single existing Clinical Note/Encounter workspace; no duplicate history/examination entry.
- Lab: manual external lab stages, secure result upload, provider metadata and doctor review.
- Pharmacy: stock level, shortages, batch urgency 7/30/90, low-stock reorder to a draft purchase, correct partial-receipt indicators; distinguish safe inventory actions from unsafe direct stock field edits.
- Cashier: pending billing, same-day posted cash/credit-card collections.
- Manager: genuine KPI definitions and drilldown, invoice vs paid distinction, doctor schedule overview.
- Admin: working hours and breaks; role-aware notification worklist.
- Every change must have Thai AND English labels, status/error/empty/loading states, responsive 1440px desktop, 834px tablet and 390px phone versions.

## Required handoff
For each changed surface: `surface_id`, `existing_v2_frame`, `change IDs`, `components changed`, `fields`, `visible labels TH/EN`, `state transitions`, `permissions`, `backend uncertainty flags`, and `desktop/tablet/mobile variants`. Deliver a clickable updated prototype and a v2→v3 frame delta table. Keep all unmodified v2 frames unchanged. No inert buttons.

## Explicit exclusions
No wards/ICU, AI diagnosis, AI risk scoring, payroll, CRM/marketing/loyalty, insurance claims, patient portal, telehealth or broad website CMS. Do not reproduce PreClinic sample figures as actual clinic KPIs.

## Acceptance
Original v2 P0 acceptance still applies. Add V3-AT-01 through V3-AT-13 from v3. Optional P1 V3-AT-14..16 only when approved. Never implement backend mutations using UI assumptions; flag verification for OpenCode.
