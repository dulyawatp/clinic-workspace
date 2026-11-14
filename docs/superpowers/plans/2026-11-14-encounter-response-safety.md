# Encounter Response Safety Plan

**Goal:** Retain durable encounter intent locks when a post-dispatch native response does not prove the write outcome.

**Architecture:** In the draft-create and Review Details routes, retain existing confirmed-success handling and pre-dispatch refusals. Classify unverified post-dispatch error responses as unknown; keep the existing unknown intent and no-retry behavior.

**Tech stack:** TypeScript, Fastify, SQLite, Vitest.

**Spec:** User-approved fail-closed design in this conversation; `docs/assessments/2026-11-14-encounter-reconciliation-evidence.md`.

## Constraints

- No live Marley writes, account or permission changes, staging, commits, or pushes.
- Never unlock an existing uncertain intent, retry an uncertain write, or infer non-commit from a current read.
- Preserve native authentication handling, including invalidating expired sessions where applicable.
- Existing permanent locks may increase; no reconciliation mechanism is implied.
- No patient, credential, identifier or private evidence in output.

## Tasks

### 1. Draft creation

**Files:** `proxy/src/routes/encounter-draft.ts`, `proxy/src/routes/encounter-draft.test.ts`.

- [ ] Add regression tests for each currently released post-dispatch status: 400, 401, 403, 404, 405, 409, 413, 417, 422, 429. Assert unknown outcome, retained lock, no second native POST on replay or a fresh key; preserve session expiry handling for 401 where applicable. Confirm tests fail before implementation.
- [ ] Implement conservative post-dispatch classification while preserving confirmed success and pre-dispatch failures; update old expectations that falsely assert retry safety.
- [ ] Run the focused draft tests and report exact command and status.

### 2. Review Details

**Files:** `proxy/src/routes/encounter-review.ts`, `proxy/src/routes/encounter-review.test.ts`.

- [ ] Add regression tests for post-dispatch 400, 401, 403, 404, 405, 422, 429. Assert unknown outcome, retained lock, no second native PUT; preserve session expiry handling for 401 where applicable. Confirm tests fail before implementation.
- [ ] Implement conservative post-dispatch classification while preserving confirmed success and pre-dispatch failures; update old expectations that falsely assert retry safety.
- [ ] Run the focused review tests and report exact command and status.

### Final verification

- [ ] Review both changes, including client interpretation of expired sessions and unknown outcomes.
- [ ] Run proxy tests and typecheck after reconciliation, and report results and limitations. No live write tests.
