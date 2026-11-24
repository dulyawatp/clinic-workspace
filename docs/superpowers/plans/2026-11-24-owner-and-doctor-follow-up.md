# Owner and Doctor Follow-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete live Doctor hours, enabled-schedule reuse, and safe ordinary staff account controls, and verify the outstanding onboarding cases.

**Architecture:** Keep the native read and write paths independently testable. Use Owner and Doctor session authority and native permissions rather than a new roster or schedule policy. Gate new writes independently where installed behavior requires review.

**Tech Stack:** React, TypeScript, Vitest, Frappe 16.54.0, Marley 16.6.1, Python unittest, and Playwright MCP.

**Spec:** `docs/superpowers/specs/2026-11-24-owner-and-doctor-follow-up-design.md`

## Global constraints

- Work in `owner-staff-onboarding` on the `sit-integration` baseline. No production rollout, role expansion on login, separate staff roster, or real email.
- Do not change native booking rules. Final acceptance uses the real React UI on approved isolated SIT, with native persistence checks only as corroboration.
- No automatic retries after uncertain writes. Do not deploy, grant new site access, or inject live faults without the applicable approval.

## Review focus

- A Doctor with missing, ambiguous or unreadable native records must not see sample or another doctor's hours.
- An enabled schedule that changes after review must not activate a doctor against stale hours.
- Unattached-draft reuse must not modify the existing schedule or its other links.
- A concurrent Desk edit after native logout must not be overwritten by staff disable.
- An uncertain invitation or concurrent Finish must not create a duplicate native message, login or schedule link.

## Task 1: Read the signed-in Doctor's recurring hours

- [ ] Test `clinic_ui.api.doctor_hours.get` (GET, no practitioner input): one authorized native practitioner returns readable enabled recurring slots; zero readable matches distinguish proven absence from ambiguous/denied reads; explicit non-DOC or invalid browser epoch is denied; unreadable unit is omitted. Assert no writes and no exposure of other practitioners.
- [ ] Implement the read in `clinic_ui/api/doctor_hours.py` and `endpoints/doctor_hours.py`. Return `{state: "ready", schedules: [{slots: [{day, from_time, to_time}], serviceUnit?: string}]}` or `{state: "empty" | "unavailable"}`. Use native permission-filtered reads and actual field authority; do not treat a filtered-out child as absence. Register the non-guest GET in whitelist tests.
- [ ] Implement `app/src/transport/native/doctor-hours.ts` and replace only the hours card in `app/src/features/clinical/DoctorHomePage.tsx`. Test loading, real hours, empty, and unavailable; invalidate results when workspace or auth epoch changes. Preserve other sample labels.
- [ ] Run the focused Python and Vitest cases and package typecheck. Verify React receives only own hours on approved SIT if native ACL permits; otherwise verify unavailable and report the scope of the ACL blocker.

## Task 2: Reuse an enabled Marley schedule

- [ ] Add read-only `reuse_options(practitioner)` to the existing Owner Finish API, returning readable eligible enabled schedules and appointment-enabled units for the configured company. Add `reuse_review(practitioner, schedule, service_unit)` to return the existing versioned review envelope, with `mode: "reuse"` and a native slot/link digest. Existing draft review remains unchanged.
- [ ] Test a separate explicit reuse branch in `endpoints/owner_doctor_finish.py`. POST uses existing Finish fields plus `mode: "reuse"` and `serviceUnit`; the existing draft contract is unchanged. Reject stale schedule/slot/practitioner versions, invalid unit, and any existing attachment before writes. Assert the attachment uses the reviewed schedule and no source mutation helper is reached. Cover the current copy path as a regression.
- [ ] Implement native locked rereads and a single new practitioner attachment through normal save hooks. Source permissions are read-only; preserve reviewed native data and all existing external attachments. Unsupported native source shapes or video schedules remain unavailable rather than being rewritten.
- [ ] Add the explicit choice, review, and shared-schedule warning in `AddDoctor.tsx`, `DoctorFinish.tsx`, the native Finish transport and EN/TH copy. Tests cover deliberate selection, stale recovery and unchanged draft path.
- [ ] Run focused tests and check native artifact. On approved SIT, verify both paths through React; corroborate that the shared source version, rows and prior links are unchanged and Reception can find the new doctor.

## Task 3: Staff enabled state without lost updates

- [ ] First write regression coverage around the pinned native save sequence. Simulate session cleanup committing and a concurrent Desk role or target edit; assert a post-logout/pre-write checkpoint rejects the change and does not overwrite the newer User or roles. Verify the checkpoint against the pinned controller and save sequence. If no safe checkpoint exists, keep disable off and document the blocker.
- [ ] Add independent `clinic_ui.api.owner_staff.set_enabled` POST `{userId, version, enabled}` returning saved `{userId, version, enabled}`. Expose an optional per-row `canSetEnabled` only for eligible ordinary accounts and an `enabledActions` capability for the site's validated write paths. Use fresh named Owner, CSRF and native User document/field permissions; preserve roles and User Type. Protect self, privileged accounts, Doctor-linked users, role profiles, and unknown role bundles.
- [ ] Add EN/TH status controls in `OwnerStaffPage.tsx`, with standalone disable confirmation and fresh read-back after a stale or uncertain result. Never automatically resubmit. Cover protected targets, stale edits and revocation messaging.
- [ ] Run focused tests. In approved SIT, verify persisted state, old session revoked even after re-enable, fresh login succeeds, and protected controls remain unavailable.

## Task 4: Close outstanding SIT acceptance

- [ ] Use capture-only Frappe password setup for a newly created REC+CAS account, then verify both React workspaces from a fresh session.
- [ ] With a separately approved isolated fault fixture, fail one exact invitation and retry it in React. Verify stable native queue identity, count and reset-key generation; no automatic retry on unknown.
- [ ] Start Finish from two Owner browser contexts against the same synthetic draft. Confirm one legitimate result and one refusal or reconciled response; verify exactly one native User, active link, published schedule and permission.
- [ ] Reconcile all writer lanes, run full Python unittest, app typecheck, Vitest and `git diff --check` once over the final changes. Compare any failing app tests with the pre-change baseline. Record exact evidence and limits in the SIT assessment.
