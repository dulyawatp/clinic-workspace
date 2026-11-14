# Demo Clinic — OpenDesign v2 package

**Purpose:** Design a modern, responsive TH/EN clinic frontend for the existing Marley Health + ERPNext backend without losing required operational coverage.

## Start here

1. Read `Clinic_Functional_Requirements_v2.md` (the **primary functional/UX specification**).
2. Give `Clinic_Screen_Manifest_v2.json` to OpenDesign as the completeness/traceability list: **37 design surfaces cover all 97 v1 screen/state IDs**.
3. Paste `Clinic_OpenDesign_Starter_Prompt_v2.md` into OpenDesign with the first two files attached.
4. Keep `Clinic_Backend_Contract_Verification_v2.md` available for technical annotations. It is **not** proof that the actual installed backend fields/methods work as listed. OpenCode must verify on site before implementing mutations.

## Important v2 decisions

- Seven roles (nurse organizationally optional), simpler task-based UI and one Doctor Clinical Workspace.
- TH/EN language switch is P0 and must work while editing without losing state. Thai-first default.
- P0 includes before/after capture, photo annotation, manual external-lab tracking, pharmacy, purchasing, invoice/payment, owner dashboards and role-appropriate administration.
- Core records stay in Marley/ERPNext. Any custom photo, consent or external-lab entity must be documented as an extension, not simulated as an existing Marley feature.
- UX design can proceed before full API verification, but exact form submission payloads and action methods must wait for installed-site validation.

## Package inventory

- `Clinic_Functional_Requirements_v2.md` — full specification.
- `Clinic_Screen_Manifest_v2.json` — 37 design surfaces, 97 legacy ID mapping, bilingual and responsive state checklists.
- `Clinic_OpenDesign_Starter_Prompt_v2.md` — concise prompt to start UX/UI generation.
- `Clinic_Backend_Contract_Verification_v2.md` — engineering verification plan for OpenCode.
