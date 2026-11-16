# Local appointment writes implementation plan

> **For agentic workers:** Use subagent-driven development; execute tasks in order and retain the existing uncommitted work.

**Goal:** Test local-only booking, rescheduling and same-day check-in through the React UI.

**Architecture:** Preserve native authorization and controller validation; serialize cooperating writes through the existing private SQLite fence with distinct appointment receipts and explicit acknowledgements. Lock appointment rows against native concurrent writers, and constrain mutations to approved synthetic targets.

**Tech stack:** Frappe/Healthcare 16, Python, SQLite, React, TypeScript, isolated OrbStack.

**Spec:** `docs/superpowers/specs/2026-11-14-local-appointment-write-design.md`

## Global constraints

- No new MariaDB tables, migrations, deletions, existing lab access or automatic retries.
- Do not change the existing future appointment. Cancellation and practitioner scheduling remain disabled.
- Only the approved local Reception Patient read grant, doctor and Reception Company read grants, and two fictional test appointments are authorized; stop for any additional permission or material side effect.
- No commits or deployment until independently reviewed; retain all existing data and evidence.

## Review focus

- Role-wide versus document and field-level access: task 1 records the matrix; task 3 tests denial.
- Concurrent Desk updates: task 3 tests row lock, stale versions and date/status rereads.
- Crash between native commit and SQLite receipt: task 2 tests unresolved barriers across restart.
- Lost acknowledgement response: task 4 tests persistent client lock with no retry.
- Existing patient writes and discovery: tasks 2 and 5 test cross-kind isolation and regression.

## Task 1: Runtime contract and authority

**Files:** `docs/superpowers/specs/2026-11-14-local-appointment-write-design.md`; private local evidence.

- [ ] Inspect pinned installed controller, same-day check-in method, side effects, linked permissions, timezone and metadata read-only; document exact paths and an action/role/DocType permission matrix. Do not infer results from fixtures.
- [ ] Specify precise permitted status transitions, fields, settings and test-record budget. If native operations or linked permissions require unapproved changes, stop and ask.
- [ ] Verify the approved Patient and Company read changes are isolated to the synthetic site; prepare exact reversible commands and pre/post verification without applying them.

## Task 2: Fence and appointment receipts

**Files:** `frappe-apps/clinic_ui/clinic_ui/core/write_fence.py`, new `core/appointment_write.py`, corresponding `tests/unit/`.

- [ ] Write tests for distinct appointment kinds, patient receipt exclusion, cross-kind unresolved blocking, process restart, reserved keys and acknowledgement binding.
- [ ] Run focused tests and confirm the new-kind tests fail before implementation.
- [ ] Add distinct appointment codecs and additive fence kinds; retain patient codecs, site-wide barrier and current schema. Test crash and persistence failure at dispatch, native commit, receipt, response and acknowledgement boundaries.
- [ ] Run `python3 -m unittest tests.unit.test_write_fence -q` in `frappe-apps/clinic_ui`; require exit 0, and record all additional focused commands.

## Task 3: Native write endpoints and discovery

**Files:** `frappe-apps/clinic_ui/clinic_ui/api/appointments.py`, new `endpoints/appointments_write.py`, `endpoints/appointments.py`, `runtime/frappe_gateway.py`, `core/config.py`, `core/capabilities.py`, related unit tests.

- [ ] Write failing tests for local gate, strict bodies, role/document/field permissions, eligible target budget, read-only existing fixture, same-day site time, row locking, stale version, unsafe settings and side effects.
- [ ] Implement permission-checked native operations using task 1's verified contract. Acquire fence before native row lock; recheck state and save within one transaction. Leave cancellation and generic status setter unavailable.
- [ ] Add bounded verified appointment receipt discovery without exposing native IDs; retain five-field list and create a separate detail contract. Fail closed on overflow or ambiguous identifiers.
- [ ] Test native commit/rollback and latch failures, acknowledgement and cross-kind isolation, including no mutation on rejected calls. Run focused endpoint, gateway, capability, DTO and whitelist suites; record exact commands and exits.

## Task 4: React integration

**Files:** `app/src/transport/native/`, `app/src/features/appointments/`, `app/src/features/reception/`, relevant tests.

- [ ] Designer writes focused failing tests for capability gating, approved booking/reschedule/check-in, readable detail, cancellation denial and local lock retention after uncertain save or acknowledgement.
- [ ] Implement accessible real-UI flows using strict response validation, in-memory tokens and one-shot acknowledgement; no retry or automatic unlock. Coordinate contracts with task 3 and retain the native-only transport.
- [ ] Run focused tests and `pnpm --filter @clinic/app typecheck`; report exact commands, exits and results.

## Task 5: Reconcile, deploy and test

**Files:** `deploy/local-sit/` verifiers; `docs/assessments/2026-11-14-local-native-sit-report.md`; private evidence.

- [ ] Independently review security, failure protocol, mixed-version ledger compatibility and UI integration; reconcile defects before final checks.
- [ ] Run backend full unit suite, app tests and typecheck, plus `git diff --check`; inspect results. Do not claim unrun tests.
- [ ] Verify exact isolated target; record baseline and approved local permission changes. Drain old workers before any new appointment-kind event. Deploy without migrations and preserve all volumes, fence state and records.
- [ ] Through React, create one fictional booking, reschedule it, and check in a separate same-day fictional appointment. Confirm each acknowledgement and independently inspect native backend effects. Stop immediately on uncertainty, without retry or unlock.
- [ ] Record supported and blocked SIT variants, exact evidence and limitations; verify the existing appointment and patient workflows remain unchanged.
