# Staff workspace switcher implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox syntax for tracking.

**Goal:** Staff with multiple assigned Frappe roles can select a React workspace from the profile menu without signing out.

**Architecture:** Clinic bootstrap returns eligible workspaces and, after selection, capabilities for exactly one active workspace. Keep selection local to the browser tab. Clinic-specific permissions are rechecked on the backend; ordinary Frappe resources continue to enforce Frappe permissions independently.

**Tech stack:** Frappe/Marley Python, React/TypeScript, unittest, Vitest, Playwright MCP.

**Spec:** [`2026-11-24-workspace-switcher-design.md`](../specs/2026-11-24-workspace-switcher-design.md).

## Global constraints

- Work on a branch from `origin/sit-integration`, separate from the practitioner booking changes.
- Keep existing single-role users working without a picker. Do not implement Owner account or role assignment yet.
- Do not modify Frappe role definitions, document permissions, production data, or generic native resource endpoints.
- The selected workspace narrows React's clinic context; it does not remove other native permissions granted to the user.
- Acceptance uses **Playwright MCP user UAT only**. Use the browser to verify the resulting user journeys. Static review and the native build prepare the feature for deployment.

## Review focus

- A multi-role account with no selection receives choices, not role-specific capabilities.
- Invalid, revoked, unauthorized or incomplete context fails closed and never silently switches to another role.
- Separate tabs can select independently; an in-flight operation stays bound to its original context.
- Clear role-specific state on switch without clearing unresolved write reconciliation markers.
- Inventory direct Frappe and photo calls; don't infer server enforcement from UI hiding or request headers alone.

## Task 1: Native session and authorization

**Files:** `clinic_ui/core/{policy,dto}.py`, `clinic_ui/endpoints/{common,session,production_read}.py`, `clinic_ui/api/session.py`, `clinic_ui/runtime/frappe_gateway.py`, and associated unit tests beneath `frappe-apps/clinic_ui/`.

- [ ] Review the contract for a session assigned both `REC` and `CAS`: bootstrap without a selection returns both in `eligibleWorkspaces` and no active capabilities; `workspace=CAS` returns active role `CAS`. Preserve legacy headerless single-role handling; deny revoked or unmapped selection, partial headers and stale session epochs without widening endpoint allowlists.
- [ ] Implement tab-local bootstrap with `workspace` and clinic RPC context headers `X-Clinic-Workspace` and `X-Clinic-Session-Epoch`. Resolve from current authenticated Frappe session, fresh assigned roles and exact site map. Bind each role-specific request to its allowed role; retain native Frappe permission checks and CSRF on writes.
- [ ] Audit the separate `clinic_photos` visit/annotation/upload gates and direct native resource calls. Carry verified selection into clinic-specific role checks without weakening existing actor/document rules; do not alter generic Frappe APIs. Document any surface that still uses only native permissions.
- [ ] Review the Python diff and record the clinic-specific and native endpoint coverage.

## Task 2: React profile menu and session switching

**Files:** `app/src/transport/native/{session,frappe}.ts`, `app/src/app/SessionProvider.tsx`, `app/src/features/shell/Topbar.tsx`, and focused session, shell and route tests.

- [ ] Define UAT cases for first-time `REC`/`CAS` selection; switching through the profile menu; hidden menu for one role; a revoked role clearing; stale data not resurfacing after a switch; and independent tabs.
- [ ] Parse bootstrap choices separately from active roles. Capture the active workspace and session epoch at operation start. Switch by confirming the chosen role, clearing protected UI state and routing to the confirmed role home; retain unresolved-write markers. Never automatically replay a refused write. Keep fixture/proxy mode unchanged.
- [ ] Review the frontend diff and build with `pnpm --filter @clinic/app build:native`. Do not loosen guards to pass.

## Task 3: Synthetic acceptance

- [ ] Build and deploy the exact feature revision to verified, isolated synthetic SIT. Use realistic fictional records and ordinary staff accounts, not production data or Administrator for the user journey.
- [ ] Run Playwright MCP as separate single-role and multi-role staff accounts. Use the React UI to verify first selection, profile switching, independent tabs, changed role availability after revocation, and denial of access to unassigned workspaces. Use Cashier workspace navigation or permitted reads, not payment settlement, which remains separately gated.
- [ ] Check resulting behavior against Frappe, and record UI and backend authorization evidence separately. Fix any issues the browser run exposes, rebuild, redeploy and rerun the affected journeys. Unit tests alone do not substitute for acceptance testing. No production deployment, production permission change, or production staff provisioning is part of this plan.
