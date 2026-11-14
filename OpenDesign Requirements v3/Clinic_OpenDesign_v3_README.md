# Demo Clinic — OpenDesign Functional Requirements v3 Package

**Use this package to update your existing v2 OpenDesign prototype**, not to design a new application from scratch. The target is a responsive icon-first TH/EN frontend backed by the existing Marley Health + ERPNext installation.

## Files

- `Clinic_Requirements_v3_Change_Log.md` — **START HERE**: 31 exact v2→v3 changes, surface IDs, P0/P1/P2, PreClinic references, how to modify existing frames.
- `Clinic_Functional_Requirements_v3.md` — full self-contained specification: all v2 functional requirements retained plus v3 details, field/input rules and new acceptance tests.
- `Clinic_Screen_Manifest_v3.json` — 37 original surfaces, unchanged 97 legacy mappings, v3 change IDs per affected surface.
- `Clinic_OpenDesign_Starter_Prompt_v3.md` — paste directly into OpenDesign with attachments.
- `Clinic_Backend_Contract_Verification_v3.md` — OpenCode engineering gate; not a validated API contract.

## Key decisions

- **Keep all 37 v2 surfaces; 97/97 previous entry mappings retained.** No additional route mandated.
- Prioritize actionable receptionist queue, doctor tasks, safe lab upload, batch/expiry alerts and responsive KPIs. Make features usable, not just decorative.
- Original P0: consent, before/after photo annotation, clinical workspace, manual external lab, inventory/purchase and cash/card billing. TH/EN retained.
- PreClinic is a **commercial Bootstrap/HTML template**. It provides example presentation patterns, not a validated Frappe backend. Do not copy proprietary images, source code or branding.
- Only the documented/verified Marley+ERPNext actions may be wired to CRUD, submit, check-in, stock movement, invoices, payment and lab review.

## Suggested OpenDesign execution

Read Change Log → Functional Requirements v3 → Manifest; paste Prompt v3. First update shared components and the Reception/Doctor/Pharmacy/Manager homes; then flows. Return a delta table linking change IDs to new/modified frame IDs. Preserve unaffected v2 design files.
