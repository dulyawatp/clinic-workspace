# Existing Encounter Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Open a real saved encounter from the existing patient workflow in the approved layout.

**Architecture:** Read encounters through projected GET methods in `clinic_ui` authorized by Frappe. Consume them from the existing patient Visits/history and visit detail routes; reuse `EncounterLayout` and the existing draft creator.

**Tech Stack:** React, TypeScript, Vitest, Python unittest, Frappe, Playwright MCP.

**Spec:** [Existing encounter workspace](../specs/2026-11-20-open-saved-encounters-design.md).

## Global Constraints

- Work only inside the active clinic worktree, on a feature branch based on `sit-integration`. No wildcard access or scans outside it.
- Do not add another page, a rollout flag, a role or capability gate, or any additional Frappe write. No proxy or sample fallback for native reads.
- Preserve existing edit, submit, `clinic_photos`, and lab attachment behavior. This change does not activate unconnected writes.

## Review Focus

- Patient aliases must be resolved server-side; a route or query string cannot authorize access to a different patient.
- A denied, missing, or malformed record must not show a previous encounter or a sample.
- Native docnames must not be classified as fixture or legacy IDs based only on a prefix.
- Empty consultations and unconnected Clinical Note/photo/lab data must not be represented as saved or verified.
- The saved record must reopen through normal navigation after reload, without a new write.

### Task 1: Add Frappe-authorized encounter reads

**Files:** `frappe-apps/clinic_ui/clinic_ui/api/encounters.py`, `runtime/frappe_gateway.py`, and `frappe-apps/clinic_ui/tests/unit/test_encounter_create_api.py` plus focused read and whitelist tests.

**RPCs:** `list_for_patient(patient_ref: str, offset: int = 0)` → `{items, nextOffset}` and `view(encounter_id: str)` → one visit summary with `id`, `patientId`, `patientName`, `practitioner`, `encounterDate`, `docstatus`, `symptoms`, and `diagnoses`. List rows use the same identity and status fields without notes or child data.

- [ ] Add red tests for read-allowed and denied users, unrelated patients, alias resolution, blank or malformed identifiers, deterministic bounded pagination (20 rows), and field projection. Confirm no write calls.
- [ ] Implement GET-only methods. Use the established server-side patient reference resolver followed by Frappe patient read permission. List through Frappe's permission-aware API; load detail through native document permission checking and verify access to the related patient before returning display data. Do not use `get_all`, permission bypass, or a new allowlist.
- [ ] Run focused encounter read, create and whitelist tests from `frappe-apps/clinic_ui`; record exact commands and results.

### Task 2: Wire existing visit navigation

**Files:** `app/src/features/clinical/VisitWorkspacePage.tsx`, `ApprovedVisitWorkspace.tsx`, `app/src/features/patients/PatientProfilePage.tsx`, `VisitTimeline.tsx`, `NativeEncounterDraft.tsx`, a typed client under `app/src/transport/native/`, relevant tests and localized copy.

- [ ] Add red tests for native patient history to `/clinic/visits/:id` navigation, a reverse Visit history link based on the authorized encounter response, and reopen after reload; create confirmation linking to the same route; Frappe denial; no stale or fixture data; and no writes when opening.
- [ ] Wire existing history to the list and replace the current encounter presentation on `/clinic/visits/:id` with the approved layout. Use the native mode before fixture-prefix handling. Preserve existing explicit legacy bindings and fixture/proxy data and action behavior; reuse their existing controls rather than changing write semantics. Render supported native fields and keep unsupported actions visibly unavailable.
- [ ] Run focused app tests and typecheck. Run all app/proxy and affected Python tests and the native build before review.

### SIT acceptance

- [ ] Deploy only an explicitly approved tested revision from `sit-integration`. Run Playwright MCP using a saved synthetic draft: sign in, open its patient from normal navigation, choose Visits, click the real encounter, reload, then return via Visit history. Verify matching patient, encounter, layout and draft status on desktop and mobile. Confirm navigation sends no mutation request.
