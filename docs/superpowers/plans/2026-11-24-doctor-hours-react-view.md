# React-only Doctor Hours Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show signed-in doctors their own live recurring hours in React without granting native schedule-document access.

**Architecture:** Keep the existing Doctor card, API envelope, and ordinary permission-bound path. An exact-site, default-off switch selects a narrow native server read of recurring slots for the current authorized Doctor's own attachments. Only the minimum approved schedule fields bypass role-level document permission; all practitioner and optional unit checks remain permission-bound.

**Tech Stack:** React, Frappe 16.54.0, Marley 16.6.1, Python `unittest`, Vitest, Playwright MCP.

**Spec:** `docs/superpowers/specs/2026-11-24-doctor-hours-react-view-design.md`

## Global Constraints

- Existing `feat/owner-staff-onboarding` worktree and deployed follow-up are the baseline; preserve uncommitted work. No new product dependencies.
- No editing, roles, document shares, list/detail grants, or changes to appointments, booking, or Owner configuration.
- Preserve the no-argument GET `clinic_ui.api.doctor_hours.get` and existing `ready`/`empty`/`unavailable` response shapes.
- No deployment, live settings, or fixture mutation without the applicable approval. Use fictional accounts and only Owner SIT `marley-sit-owner-uat`, `marley-sit.local`, loopback `18087`.

## Review Focus

- A hidden duplicate practitioner is still ambiguous; permission-filtering cannot turn two active matches into one.
- A stale workspace, epoch, or revoked/disabled Doctor account cannot reach the scoped reader.
- An orphan link, wrong child parent/type/field, unsupported metadata, or mask must never disclose or partially populate required hours.
- Two doctors can read a legitimately shared schedule; detachment removes only the detached doctor's access on the next request.
- A foreign schedule stays inaccessible through generic native list and detail even when React can render the approved hours. Flag-off preserves legacy behavior.

---

### Task 1: Scoped native read and authority

**Files:**
- Modify: `frappe-apps/clinic_ui/clinic_ui/endpoints/doctor_hours.py`
- Create: `frappe-apps/clinic_ui/clinic_ui/endpoints/doctor_hours_self_read.py`
- Test: `frappe-apps/clinic_ui/tests/unit/test_doctor_hours_self_read.py`

**Interfaces:** `get(gateway)` still consumes the native session and has no caller-supplied identity. The new internal helper `read_attached_recurring_hours(frappe, user: str) -> dict` produces the existing response DTO. `clinic_ui_doctor_hours_self_read_enabled` must be the boolean `True`, and `clinic_ui_doctor_hours_self_read_site` must equal `gateway.site`; otherwise keep the existing permission-bound path.

- [ ] **Write failing tests.** `test_scoped_read_returns_only_currently_attached_ranges` asserts `{"state":"ready","schedules":[{"slots":[{"day":"Monday","from_time":"10:00:00","to_time":"12:00:00"}]}]}` for an otherwise unreadable schedule attached to the authorized doctor; prove unrelated schedule rows and raw records never enter the DTO. `test_shared_read_follows_live_attachment` uses two distinct users attached to one schedule, then detaches one and asserts `empty` for that user and unchanged `ready` for the other. `test_hidden_duplicate_and_wrong_parent_fail_closed` asserts `unavailable`, with no slot read, for two active matches including an ACL-hidden duplicate and for malformed or orphaned links. `test_scoped_read_requires_current_authority_and_exact_setting` covers stale epoch, mismatched SID, no fresh Doctor role, disabled user, and wrong-site or nonboolean opt-in; the latter two keep ordinary reads. Test masked or wrong-typed required fields, omitted unreadable unit label, disabled schedules, and reuse of canonical whole-second times.
- [ ] **Run RED.** From `frappe-apps/clinic_ui`, `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.unit.test_doctor_hours_self_read -q`. Expect a missing or nonfunctional scoped path, not a fixture error.
- [ ] **Implement the policy.** Preserve existing authentication and workspace checks; additionally verify persisted enabled status and current Doctor authority using the existing role map and native fresh-role lookup before calling the helper. Establish unambiguous active practitioner identity with an internal exact-user lookup capped at two, and retain native list/document and field read checks on the practitioner and attachment metadata. Read only the approved schedule parent `disabled` and child `day/from_time/to_time` fields plus structural keys needed to establish scope. Use parameterized or structured filters tied to the exact inspected practitioner, attachment identity and schedule; require the expected child parent/type/field for both tables. Never bypass a required field mask, read arbitrary scope, or return a parent/child record. Preserve existing time shaping and output order. Re-evaluate on every request; do not cache authority or slot rows. Optional unit text still uses ordinary permission.
- [ ] **Run GREEN.** `PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest tests.unit.test_doctor_hours_self_read tests.unit.test_doctor_hours tests.unit.test_whitelist_surface -q`; exit 0. Review exact authority and metadata boundaries independently before proceeding.

### Task 2: Integrated verification and isolated acceptance

**Files:** no React product changes expected. Record acceptance in `docs/assessments/2026-11-24-owner-staff-directory-uat.md`; keep operational evidence outside the repo.

- [ ] Run the full Python `unittest` suite, `pnpm --filter @clinic/app typecheck`, and `pnpm --filter @clinic/app test` (baseline Cashier auth-epoch and operations copy snapshot failures must be identified by name), then staged/unstaged diff checks. The existing Doctor home tests must pass without changes to the UI contract.
- [ ] After separate approval, verify exact Owner SIT identity and native schema, take a backup, deploy only there, and enable the two isolated settings. Use an attached private fixture and two fictional doctors on one shared fixture. Verify exact hours and no unrelated times in React using Playwright MCP.
- [ ] Under the same fictional identities, verify generic native `Practitioner Schedule` list/detail grants did not change. Detach one fictional doctor using a supported native workflow, then confirm the next React read loses those rows only for the detached doctor. Check flag-off fallback, preserve the unrelated schedule and appointment data, and document rollback by disabling only this switch.
