# Marley Synthetic Patient Directory Implementation Plan

> **For agentic workers:** Use subagent-driven development to implement and verify each task. Checkboxes track work; do not commit.

**Goal:** Show the physician's authorized, explicitly allow-listed Marley synthetic patients in the existing React patient directory, then verify it in a real browser.

**Architecture:** Reuse the existing per-user Frappe session and synthetic-patient endpoint; branch the patient directory by session mode, keeping fixture data and live data separate. This is the first phase only, not seven-role or production-readiness completion.

**Tech Stack:** React, TypeScript, TanStack Query, Frappe, Vitest, Playwright, SSH.

**Spec:** `docs/superpowers/specs/2026-11-13-marley-live-integration-design.md`.

## Global Constraints

- No account, role or permission changes without separate user approval; no real-patient mutations, deletions, queue clearing or uncertain retries.
- Synthetic records only; no credentials, PHI, traces or screenshots in output. Use SSH loopback tunnel, never remote plaintext credentials.
- Live never falls back to fixtures; preserve existing fixture mode, native permissions, Thai default, English and accessibility. Do not claim live rows are fictional fixture DTOs.
- No writes in this phase; do not stage, commit or push. Marley photo extension is absent and not in scope.

## Review Focus

- Unauthorized or expired sessions must not reveal rows (Task 1).
- Unexpected or malformed backend responses fail closed (Tasks 1–2).
- Changing user or mode must never show another session's cached rows (Task 2).
- Unsupported profile links and patient creation must not imply working functionality (Task 2).
- Throttling must not be reported as an authorization denial (Task 3).

### Task 1: Verify existing synthetic-patient authorization

**Files:** `proxy/src/routes/live-metadata.ts`, `proxy/src/routes/patient-authority.ts`; test `proxy/src/routes/live-metadata.test.ts`; private evidence under `../.runtime/`.

**Interface:** `GET /api/live/synthetic-patients` returns existing `items` with `workspace_patient_id`, `native_patient_id`, `display_name`; calls Frappe with the authenticated user's own session and the server-owned allow-list.

- [ ] Inspect the existing implementation and add focused tests: guest 401, non-DOC 403, fixture-mode 404, authorized row, denied row omitted, expired upstream invalidates session, malformed response fails closed, and two distinct sessions use their own native cookies. Run focused Vitest and record the initial failures.
- [ ] Make only changes required to satisfy those assertions without altering the existing endpoint contract. Run `pnpm --filter @clinic/proxy exec vitest run src/routes/live-metadata.test.ts` and relevant existing photo tests; require zero exit.
- [ ] Via SSH tunnel, read-only verify that the documented synthetic doctor can read the two existing synthetic Marley patients and record only sanitized outcomes in a private evidence file. If the allow-list, native authorization or endpoint contract is not verified, stop and report the blocker. Do not list or record real patients.

### Task 2: Live directory in existing UI

**Files:** `app/src/features/patients/PatientDirectoryPage.tsx`, `app/src/features/patients/usePatients.ts`, new live-directory helper and focused test in that feature; existing locale catalogs as needed.

**Interface:** consume Task 1's endpoint in live DOC sessions, validate its response, and render authorized `display_name` and workspace reference without constructing fixture `PatientSummary` values or exposing extra fields.

- [ ] Add tests for live rows, empty results, malformed/403 responses, session/mode cache isolation, no fixture rows in live, Thai and English, keyboard-accessible table, and unavailable profile/create actions. Run focused Vitest and confirm failures.
- [ ] Implement a mode-aware directory with a distinct live DTO and a query keyed by authentication generation. Preserve fixture behavior; filter/paginate only the returned allow-listed rows. Do not present unsupported phone, last-visit, profile or create functionality as real.
- [ ] Run `pnpm --filter @clinic/app exec vitest run src/features/patients` and relevant typecheck; require zero exit.

### Task 3: Marley browser evidence

**Files:** a separate Marley-specific live Playwright config/spec under `app/`, plus private sanitized evidence under `../.runtime/`; do not weaken isolated-lab tests.

**Interface:** a loopback-only browser test with private existing physician credentials and explicit Marley site identity, allowing only reads plus login/logout.

- [ ] Add assertions for real physician login, synthetic directory rendering, no fixture fallback, guest denial and disabled unsupported actions. Record role, assertion outcomes, commands and exit status without sensitive values. Pace logins and report HTTP 429 as untested.
- [ ] Run against the separately configured React UI and proxy connected through the SSH tunnel to Marley. Require passing assertions and verify no write requests were made; do not use Frappe Desk as the tested UI.
- [ ] Run relevant proxy and app tests, typecheck and lint once after changes; report exact command outcomes and any remaining blockers. This phase does not establish coverage for other roles or production readiness.
