# OpenDesign — Demo Clinic Functional Requirements v2.0 (copy/paste prompt)

Design a **complete, responsive, icon-first, bilingual TH/EN aesthetic-clinic application** using the attached:

1. `Clinic_Functional_Requirements_v2.md` — authoritative **UX and functional requirements**.
2. `Clinic_Screen_Manifest_v2.json` — **37 consolidated design surfaces**; exhaustive mapping of all **97 legacy v1 screens/states**.
3. `Clinic_Backend_Contract_Verification_v2.md` — engineering **unknowns**, not a verified backend contract. Do not invent source fieldnames, APIs or native functionality.

## Product and user experience

- Brand: **Demo Clinic**; current backend Frappe + Marley Health + ERPNext. **Keep the existing backend and its business logic**; design a new staff frontend, not a replacement healthcare/ERP database.
- Intended implementation after design: React + TypeScript + Vite + responsive accessible component system (shadcn/ui/Tailwind/Lucide is a candidate).
- User preference: **large visual task tiles with distinct icons and short labels**, simple status graphics, minimal text on homepages, clean premium aesthetic-clinic design with ivory/white and muted teal/sage.
- **Do not replace clinically important structured fields with infographics.** Precise clinical notes, doses, consent evidence, lab results and stock quantities need clear labeled forms.
- Desktop 1440px, tablet 834px, mobile 390px; touch-friendly, keyboard-accessible, Thai Unicode-safe, no important controls clipped.
- **Language switching is P0:** visible `ไทย | EN` on sign-in, all role headers, profile; switch instantly in any active draft without losing entered data. Thai is default for new users, English optional; persist per user. Produce both Thai and English labels, validation, errors, statuses, charts, headings, tabs, dialogs, print examples; keep entered clinical notes, names, files and backend IDs unchanged.

## Roles and scope (all required for complete design)

Receptionist, Doctor, Pharmacy/Inventory, Cashier, Owner/Manager, Administrator, and Nurse/Treatment Assistant (optional employment role, but design its assigned-task UI). Do not include CRM/marketing/loyalty/inpatient or a patient-facing portal.

P0 journeys:

- Patient search/registration → intake BEFORE photo (private/consent) → appointment/arrival/check-in → current visit.
- **One doctor clinical workspace** with persistent patient header and **sections**: History, Examination/Vitals, Clinical Note, Diagnoses, Photos, Before/After Compare + Draw Shapes, External Lab, Medication, Procedure, Review/Complete. **Never require repeating History/Examination into another editor.**
- Photo annotation tools: arrows, ellipse, rectangle, text, undo/redo, save **non-destructive versioned layer**, side-by-side comparison/slider; original never overwritten.
- **Manual External Lab**: Doctor orders → authorized staff sends/uploads → doctor reviews result with status/timestamps. Do not assume native Lab Test offers external provider tracking.
- Pharmacy: stock balance, batch/expiry, purchase orders, partial receipts, warehouse transfer, actual usage from procedure, ledger, restricted reconciliation.
- Cashier: invoice, Cash/Credit Card payment mode, receipt; no raw card credentials. Management: source-derived appointment, revenue, payment, inventory and purchasing dashboards. Admin: users, roles, doctor schedules, treatment/service configuration and bilingual settings.
- Exception flows: canceled/rescheduled appointment, walk-in, duplicate patient, absent consent, failed uploads, missing stock, submit timeout, stale draft, failed payment, denied access and no-locale-data-loss.

## Design outputs

1. First propose a **simple information architecture** for 37 surfaces, preserving all 97 legacy IDs as tabs/drawers/states. One role home and 4–6 labeled icon shortcuts per role.
2. Design a shared design system, responsive shell, Thai/English language switcher, icon tiles, lists, calendar, status chips, notes, photo editor, external lab timeline, inventory table, sales/payment form and chart components.
3. Design all P0 role journeys with realistic fictional records, not one item per dashboard. Include desktop/tablet/mobile layouts and Thai+English language variants.
4. Annotate each form field with: EN label, TH label, required/conditional/derived status, type, validation, candidate backend entity and `VERIFY`/`EXT` where not yet proven.
5. For every surface: title, ID, route, role, required sections, real user actions, success, validation, loading, empty, permission denied, session expired, save-draft/retry and confirmation states.
6. Prototypes must be navigable and task-complete, not a slideshow of decorative dashboards. No dead shortcut cards; print/export uses role permission.
7. Export components/assets, role sitemaps, form field annotations, action/permission/translation inventories and unresolved-backend checklist for OpenCode.

## Explicit design integrity rules

- Do not force 97 top-level pages. Use the **37 grouped surfaces** with tab/dialog state coverage.
- P0 features are requirements even when backend extensions are needed. Label extensions in design annotations; do not claim they already work natively.
- `Clinical Note` and `Patient Encounter` may be separate backend records. **One frontend workspace doesn't imply one backend save call.** Physical examination is not to be mapped into an unverified display/layout field.
- Photo capture can happen **before check-in**; design association with the Patient first and the Encounter later. Consent evidence visible and permission-controlled.
- All operations involving stock, submitted invoices, payments, appointment status, medical sign-off must be backed by *verified server workflows*, not speculative direct CRUD; exact payloads belong to later OpenCode verification.
- Display manager KPIs from plausible connected demo data, clearly watermarked Sample in mockups. Avoid fake data once implemented.

**Start your output by showing:** (1) role navigation sitemap, (2) design system with TH/EN typography and task tile iconography, (3) both Reception and Doctor homepages on desktop/tablet/mobile, (4) a unified Clinical Note workspace with before/after annotation, (5) a sample TH→EN switch while a draft is open. Then finish all remaining role modules and coverage states.
