# Owner staff management implementation plan

> **For agentic workers:** Use the subagent-driven workflow. Check off each lane after review and use Playwright MCP for user acceptance.

**Goal:** A named Owner can manage staff access and onboard a doctor from React; Reception can then book the doctor's configured hours.

**Architecture:** Add a narrowly scoped Frappe staff surface in the production Clinic UI lane. List native `User` records directly; do not maintain a membership roster. Keep user, role, practitioner, and schedule data in native records; persist only unfinished onboarding progress if needed. Verify the complete handoff in the browser.

**Tech stack:** Frappe/Marley Python, React/TypeScript, Playwright MCP.

**Spec:** [Owner staff management and doctor onboarding](../specs/2026-11-24-owner-staff-onboarding-design.md).

## Global constraints

- Isolated `feat/owner-staff-onboarding` worktree based on `origin/sit-integration`; carry forward the prior two feature diffs locally, without changing either original worktree or pushing.
- Require the active `MGR` workspace and fresh native `System Manager` membership on every staff operation; respect native document permissions. No Administrator UAT, legacy gate promotion, or generic privileged CRUD.
- Acceptance is browser-only with Playwright MCP as fictional staff in isolated SIT. No production changes, real email, or changes to native booking rules.

## Review focus

- All readable native accounts can be listed without enrollment. Determine edit eligibility on each request from native permissions and account protections, not from onboarding progress.
- The React edit requests only approved role deltas, never an Owner or System Manager role or a User Type change. Native Frappe side effects are reported from read-back rather than suppressed or represented as part of the requested edit.
- Incomplete doctor setup creates no login and no enabled schedule. Read back native drafts and do not automatically replay uncertain writes.
- Native disable can revoke sessions during save. Keep it separate from any other write and verify the result as staff.
- Hours must be usable by native availability. A shared schedule is never edited to change a single doctor's hours.

## 1. Frappe user list and account lifecycle

- [ ] Add `clinic_ui.api.owner_staff` with purpose-specific methods `list_staff`, `create_staff`, `update_roles`, and `set_enabled`. Return paginated native user rows containing user ID, display name, enabled state, assigned clinic roles, edit eligibility, onboarding status, and a version for updates. Expose site-configured role choices only after confirming Owner + System Manager and native authority.
- [ ] Remove the incomplete membership roster path from the draft without changing the booking and switcher overlays. If an app-managed setup record is needed, use it only for in-progress onboarding and reconciliation; never require it to list or manage an existing eligible account.
- [ ] Submit only approved native Role deltas through normal Frappe User save under the Owner’s native permissions, then reread and return the resulting roles and additional native changes. Do not add a parallel policy engine or require a guarantee that native hooks touch no other roles. Create ordinary staff enabled without an Owner-assigned password or welcome mail. Keep disable standalone. Audit post-save hooks before introducing multi-document transactions.
- [ ] For the supported native `System User` path, include at least one enabled, approved Desk role in the initial ordinary staff User insert. Before editing, check both the `role_profiles` child table and legacy `role_profile_name`, and verify current and proposed native roles remain enabled and retain Desk access. Do not silently convert between Website User and System User.
- [ ] Reject normalized email collisions before native enabled User insert. Roll back an insert-time duplicate; enabling ordinary staff at creation avoids the disabled-User sign-out branch, not every native save hook. Confirm the persisted enabled account and approved roles, and report uncertain outcomes without resubmitting. Doctor creation stays out of this action.
- [ ] Add an Owner staff capability to shared contracts and production bootstrap, then review backend permissions and initial screen DTO together. No capability or access changes to the legacy Admin staff flow.

## 2. Doctor setup and invitation

- [ ] Extend the private deployment command for explicitly named Owners. Retain `Clinic Owner Practitioner Editor` with native `Healthcare Practitioner` read/write, and add a separately assigned `Clinic Owner Practitioner Creator` role for Practitioner Create. Do not change rights of previously configured Owners not selected for provisioning. Never change the shared Healthcare Administrator role. Preserve effective grants and refuse conflicts. Do not call from install, migration, sign-in, or HTTP. Document invocation, prerequisites and removal.
- [ ] In isolated synthetic SIT, exercise the same deployment command twice. Verify the named Owner can save a practitioner edit in Desk, an unconfigured Manager cannot, and existing clinical access is not narrowed. Use Playwright MCP for the user journeys; no live rollout.

- [ ] Add doctor setup under Staff & roles: create or select an eligible native practitioner, set or reuse suitable hours, finish, then offer a separate invitation. Create new profiles disabled and unlinked; persist no login until Finish. Scope the lookup and enforce native permissions without elevation.
- [ ] Use the existing draft-hours service for new, doctor-specific hours. Recheck the practitioner and schedule on Finish; preserve unrelated rows and never enable or alter a schedule shared with another practitioner.
- [ ] Verify the pinned installed User, Practitioner, Practitioner Schedule, and User Permission hooks before creating the enabled doctor login, linking it and activating the prepared records in one transaction. Keep Finish unavailable if native hooks can commit midway. Acquire and recheck current native state and reject repeated or stale actions; reread before reporting success. Send invitations separately after commit, with no automatic duplicate send on uncertainty.
- [ ] Derive doctor readiness from native practitioner linkage and usable hours. Do not let a generic Enable action or assigning Doctor to an enabled user bypass incomplete doctor setup.

## 3. Owner React screen

- [ ] Put Staff & roles in Owner navigation only when the native capability permits it. Use the live Frappe user list, mark protected records read-only, and provide role multi-select, standalone account enable/disable, and resumable doctor setup. Keep sample and legacy Admin surfaces separate.
- [ ] Add Clinic UI transport with the current workspace context and CSRF. Clear or refetch superseded UI state on workspace change. Show saved, pending, denied, and unresolved steps without inventing success.
- [ ] Review the integrated diff and build native assets for isolated SIT. No automated test suite substitutes for user UAT.

## 4. Playwright MCP UAT

- [ ] Verify Add staff independently: as the named Owner, choose two approved roles and create a fictional account. Confirm React shows the account as enabled. Complete native password setup through synthetic mail capture (never real mail) and verify sign-in and both workspace choices. Submit an existing fictional staff email, confirm the form reports the duplicate, and verify that staff member's already-open workspace still works. Confirm that a Manager without the required access does not see Add staff. The ordinary duplicate journey does not establish concurrency safety.
- [ ] Verify the draft-hours slice independently: as the named Owner, select an existing disabled and unlinked fictional practitioner and an eligible service unit. In Monday’s live preview, check that 09:00–12:00 and 13:00–17:00 appear as separate bars with a one-hour gap; confirm an overlap is flagged, then correct it before saving. Use Desk to confirm exactly one disabled schedule is attached and the practitioner remains disabled and unlinked. Refresh the React screen and confirm an already staged practitioner is no longer eligible for another draft. Verify a Manager without the required access cannot open the form. This does not verify activation, invitations, or booking.
- [ ] On a fresh synthetic site deploy the exact combined feature build with isolated mail capture. Configure fictional Owner with System Manager and normal native Marley permissions, another Manager without System Manager, and normal Doctor, Reception and Cashier role mappings.
- [ ] As Owner, find an existing staff account without onboarding enrollment, verify a privileged account is visible but read-only, then create a Reception + Cashier account, assign both roles, finish setup, and verify its workspace choices via staff sign-in. Verify a Manager without System Manager and a non-Owner cannot enter Staff & roles.
- [ ] As Owner, create a disabled practitioner and resume the draft after reload. Verify no login exists before Finish, set working hours with a break, finish setup and observe the invitation only in the synthetic sink. As Doctor, sign in; as Reception, see the named doctor and book an expected slot. Exercise the link-existing path without reassigning a linked practitioner or mutating a shared schedule.
- [ ] Disable a managed staff account while signed in elsewhere. Verify the existing staff session and new login lose access, while the scheduled appointment remains visible to Reception. Record browser-visible results, fix failures and repeat affected journeys before declaring acceptance.
