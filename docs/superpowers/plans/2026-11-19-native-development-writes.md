# Native development writes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove custom write locks from isolated synthetic development workflows while retaining native backend authority.

**Architecture:** Convert existing backend and frontend operations as complete lanes. Preserve legacy ledger reads for existing references, never mutate the historical ledger, and leave deferred clinical operations deferred.

**Tech Stack:** Frappe/Python, React/TypeScript, unittest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-19-native-development-writes-design.md`

## Global Constraints

- Only the isolated synthetic `marley-sit.local` site receives development behavior.
- No new API family, custom encryption, replacement fence, fabricated receipt or automatic write retry.
- Preserve native authorization, validation, controller lifecycle and transaction cleanup.
- Preserve existing historical ledger and the local appointment clock fix.

## Review Focus

- Existing unresolved fence entries must not block a converted lane or be changed.
- Native identifiers must never bypass read, create or write permissions.
- A lost response must not cause an automatic duplicate mutation.
- Historical references must continue to resolve without writing the old ledger.
- Deferred clinical endpoints must remain unavailable even when patient writes work.

### Task 1: Patient development workflow

**Files:** Existing patient endpoints, alias and directory handlers, gateway, session capability logic, patient transport and patient UI tests.

- [ ] Add failing backend tests for create, permission-aware discovery, read and update with unresolved historical fence entries; verify native permissions, CSRF, site restrictions and no ledger mutations.
- [ ] Add failing frontend tests proving stale storage locks are ignored, no acknowledgement is sent and an uncertain response is not automatically retried.
- [ ] Adapt existing backend and frontend patient paths to the agreed native result contract.
- [ ] Run focused Python and app tests, then review the actual diff and retained native permission checks.

### Task 2: Appointment development workflow

**Files:** Existing appointment endpoints, gateway, session capability logic, appointment transport and UI tests.

- [ ] Test native booking, discovery and supported lifecycle actions with existing and new patient identifiers, preserving native authorization and controller validation.
- [ ] Convert the complete backend/frontend appointment path without custom fence, budget, persistent browser lock or acknowledgement.
- [ ] Verify historical appointment references remain readable and unchanged; run focused Python and app tests.

### Task 3: Remaining native write workflows

**Files:** Existing procedure, stock, purchasing, finance and admin endpoints and corresponding transport and tests.

- [ ] Convert each supported workflow as a complete backend/frontend lane, preserving native document operations, permission checks and transaction cleanup.
- [ ] Keep unfinished lanes and deferred clinical operations unavailable rather than publishing partially converted actions.
- [ ] Test validation, native authority, response-loss handling and absence of historical ledger mutations per lane.

### Task 4: Integration and isolated verification

- [ ] Run backend unit tests, verifier tests, workspace tests, typecheck and `git diff --check`.
- [ ] Review site restrictions, historical ledger reads and every deferred capability.
- [ ] Deploy only with the approved targeted SIT procedure; preserve the old ledger and record its state before and after.
- [ ] Use Playwright MCP to test the converted React workflow against synthetic records; do not automatically retry uncertain writes.
