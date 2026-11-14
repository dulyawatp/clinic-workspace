# Appointment Role Boundaries Implementation Plan

**Goal:** Align appointment navigation with live authorization and improve the accuracy and coverage of read-only Marley role tests.

**Architecture:** Limit the appointment navigation item to DOC and REC in both fixture and live modes. Leave existing API authorization and fixture resource permissions unchanged. Extend the read-only role suite to test the appointment endpoint and disabled draft-recovery gate; document exactly what those checks prove.

**Tech stack:** React, TypeScript, Vitest, Playwright, Marley proxy.

**Spec:** User-approved design in the current conversation, 2026-11-14.

## Global constraints

- Do not stage, commit, push, change Marley accounts or permissions, or write Marley records.
- Keep credentials, identifiers, and evidence private; use the SSH tunnel for Marley access.
- Do not weaken uncertain-write locks or claim read-only checks prove draft recovery.
- Distinguish empty appointment responses and role-boundary checks from populated workflows and production readiness.

## Review focus

- DOC and REC retain appointment navigation and endpoint access.
- NUR and ADM no longer see the appointment navigation; direct live requests remain denied.
- PHA, CAS and MGR are also denied the endpoint.
- Recovery remains disabled in the read-only suite and no write opt-in is introduced.
- Fixture navigation expectations and the specification reflect the changed behavior.

### Task 1: Align navigation and fixture expectations

**Files:** `app/src/features/shell/nav.ts`, `app/src/features/shell/shell.test.tsx`, `app/e2e/role-matrix.spec.ts`.

- [ ] Add role navigation assertions for DOC and REC inclusion and NUR and ADM exclusion; run the focused test and confirm the pre-change failure.
- [ ] Set the appointment navigation roles to `["REC", "DOC"]`; update fixture role expectations and choose the system page for ADM's accessible destination.
- [ ] Run the focused shell unit test and relevant fixture role matrix; record commands and results.

### Task 2: Extend read-only Marley coverage

**Files:** `app/e2e-marley/roles.ts`, `app/e2e-marley/role-matrix.spec.ts`, `app/e2e-marley/SPEC.md`.

- [ ] Update NUR and ADM navigation expectations; assert appointment endpoint success for DOC and REC and denial for the other five roles. Check no-store and validate the returned shape without assuming a populated appointment.
- [ ] Assert the draft-recovery endpoint remains disabled under the suite's existing write-disabled configuration; do not enable write capability or claim actual recovery coverage.
- [ ] Correct stale appointment and denial coverage claims in the specification; describe configuration, safety boundaries, and remaining limitations accurately.
- [ ] Run applicable focused checks. Run the live read-only suite only if the private runtime and secure tunnel are available; report exact results without disclosing private information.

### Final validation

- [ ] Review all changed artifacts and run the relevant checks after reconciling changes.
- [ ] Report actual results, untested workflows, and any failures. Leave existing user changes untouched.
