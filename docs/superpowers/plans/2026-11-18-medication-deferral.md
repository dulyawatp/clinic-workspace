# Medication deferral implementation plan

> **For agentic workers:** Use focused subagent-driven tasks and validate each locally. Do not commit without authorization.

**Goal:** Align the native UI, SIT verifier and test documentation with the existing fail-closed medication contract.

**Architecture:** The native UI displays an unavailable state rather than allowing medication additions; the verifier separately asserts medication ordering remains disabled. The backend refusal remains authoritative.

**Tech Stack:** React, TypeScript, Vitest, Python unittest.

**Spec:** `docs/superpowers/specs/2026-11-18-medication-deferral-design.md`

## Global constraints

- Local work only; no deployment, live SIT writes, permission changes, automatic retries, automatic unlocks, fabricated events or remote pushes.
- Preserve existing medication data without silently clearing it.
- Do not weaken existing verifier checks or represent blocked SIT cases as passed.

## Review focus

- Gate enabled with medication capability enabled: verifier rejects it.
- Gate enabled with medication capability disabled: verifier does not require it enabled.
- Existing medication rows: UI does not silently erase them.
- Native medication entry unavailable: users cannot add medication rows.
- Other encounter capabilities and non-native surfaces retain their existing behavior.

### Task 1: Native UI deferral

**Files:** `app/src/features/clinical/AuthoredVisitWorkspace.tsx`, relevant locale files, and `app/src/features/clinical/authored-visit.test.tsx`.

- [ ] Add focused tests for native medication ordering unavailable and existing row preservation; run and record the failing result.
- [ ] Implement the smallest accessible native UI change, retaining other surfaces and existing data.
- [ ] Run the focused Vitest file and `npm run typecheck` using the project's installed package manager; report exact commands and statuses.

### Task 2: Verifier and SIT status

**Files:** `deploy/local-sit/bin/verify.py`, focused local Python tests, and `docs/assessments/2026-11-14-marley-native-sit-test-cases.md`.

- [ ] Add tests proving medication capability disabled is expected even with encounter gate enabled, and that an enabled medication capability is rejected; retain other capability checks. Run and record failing tests.
- [ ] Implement the explicit disabled-capability assertion and mark SIT-DOC-08 blocked pending a reviewed medication contract.
- [ ] Run focused local tests; report exact commands and statuses. Do not run the live verifier.

### Final verification

- [ ] Inspect both lanes and reconcile changes; run focused tests, native UI typecheck and `git diff --check`.
- [ ] Report remaining clinical and authorization blockers; seek separate approval before deployment or SIT writes.
