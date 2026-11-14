# OpenDesign — Demo Clinic Design Prompt

You are designing an implementation-ready, responsive healthcare application for a small/medium private aesthetic clinic in Thailand. **Read the attached `Clinic_OpenDesign_Functional_Requirements_v1.md` in full, and use `Clinic_OpenDesign_Screen_Manifest.json` as the coverage checklist.** Do not omit any role, route, distinct form, or required field.

## Product / source of truth
- Product name: **Demo Clinic**. Existing ERPNext company is **Demo Clinic (DC)**.
- The backend is already deployed: Frappe + Marley Health + ERPNext. We are **not** replacing backend healthcare, inventory or accounting business logic.
- Frontend will be coded later in React + TypeScript + Vite + shadcn/ui + Tailwind CSS + Lucide. **Your task is to produce UX/UI designs and developer handoff, not a competing backend architecture.**
- First-class roles: Receptionist, Doctor, Pharmacy/Inventory, Cashier, Clinic Owner/Manager, Administrator; Nurse/Treatment Assistant optional but design its pages.
- Scope: registration, booking/queue/check-in, visit photos, consultation and clinical note, examination/vitals, procedure, medication, lab/external lab, batch/expiry/inventory and purchasing, invoice/payment (cash and credit-card method only), reports and staff/admin setup. **Do not add CRM, promotions, loyalty, inpatient wards or a self-service patient portal.**

## Required design appearance
- Premium aesthetic-clinic application: soft white/ivory, muted teal/sage, restrained accent colors, simple modern vector icons and visual guidance.
- **Icon-first and task-first:** 4–6 large labeled action tiles on role landing pages, concise visual steps, status chips, compact charts. Every icon has a readable label; never use infographic in place of a precise medical form.
- Responsive: desktop (1440px), tablet (834px), phone (390px). Create all necessary mobile navigation patterns, no broken tabular forms, touch-friendly targets.
- Shared UI library: shell/navigation, patient header, action cards, tabs, queue, calendar, clinical note editor, photo gallery, photo annotation/comparison, lab timeline, batch/expiry, invoice/cart, reports, admin settings, validation/empty/permission/error states.

## Required screens and journeys
1. Design a role sitemap and shared component system covering all **97** rows in the screen manifest. Multiple route IDs may share a template, but preserve their unique states and functions.
2. Design **all** primary role homepages, not only Reception.
3. Design the end-to-end patient journey: Search → New Patient → Photo Upload/Consent → Appointment → Check In → Doctor History/Vitals/Clinical Note → Lab/Procedure/Medications → Stock Consumption → Invoice → Cash/Card Payment → Follow-up. Include returning patients and walk-ins.
4. Design the full pharmacy journey: Stock → Batch/Expiry → PO → Receipt → Transfer → Treatment Consumption → Ledger. Include lot number, expiry date, on-hand warehouse amounts, and error statuses.
5. Design complete management drill-down dashboards with correctly titled KPI definitions; fictional demo records are acceptable and must be marked sample.
6. Design all admin/setup pages and nurse/helper views in the manifest.
7. Show at least: default populated state, empty/loading/error, success, validation, permission-denied, submit confirmation and destructive action modal, desktop/tablet/mobile variants. Emphasize 10–12 realistic sample records per list when useful; don't design empty dashboards.

## Backend mapping annotations — essential
- Annotate **every** create/edit form field with UI label, backend `DocType.fieldname`, required flag (**M** native checked; **C** conditional; **B** business; **O** optional; **D** derived; **X** extension), data type and special validation.
- Use exact source mappings from §5 of the attached specification. Do **not** invent backend field names for Marley; if a desired input has no valid native field, tag it **X / requires backend extension**.
- Important technical truth: `Patient Encounter.physical_examination` is a *layout field* and `Patient Encounter.clinical_notes` is an *HTML display field* in checked public Marley source; editable note content belongs in a separate `Clinical Note.note` record with an approved encounter link. Build one easy doctor UI but represent actual separate records in annotations.
- `Clinical Procedure.warehouse` is conditionally mandatory for stock consumption; `Clinical Procedure Item.item_code/qty/uom` required per row. `Patient` needs `first_name/sex`. `Patient Appointment` needs patient/type/date/company and appointment_for plus conditional appointment time. On actual installed version, runtime metadata may differ; do not label source-based mappings as deployed-site confirmed.
- For photo annotation and external-lab provider tracking, indicate custom backend data entities rather than depicting unsupported features as built-in.
- UI must never direct-write stock ledger, readonly statuses or submitted invoice fields.

## Deliverables
- Brand/design tokens, responsive layouts, icon inventory, type scale, reusable component library.
- Role-based sitemap and all corresponding high-fidelity page frames with Screen IDs from manifest.
- Clickable flows for Reception, Doctor, Pharmacy, Cashier, Manager and Admin.
- Component states/validation, form field table, backend mapping annotations, permitted actions and cross-route navigation for OpenCode.
- A **coverage matrix** listing every Screen ID as DESIGNED / NOT YET DESIGNED; no silent omissions.
- A **backend assumptions list**, especially external lab, photo metadata, check-in, appointment availability and clinical note linkage.

**Work in waves:** (1) shared design system and all role homes, (2) reception + doctor workflows, (3) pharmacy + cashier, (4) management + admin + optional nurse, (5) all missing states, links and mobile frames. Preserve consistency throughout. Your output is a design specification and screens, not a flattened single infographic.
