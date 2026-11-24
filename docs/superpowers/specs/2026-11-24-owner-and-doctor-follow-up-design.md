# Owner and Doctor workspace follow-up

## Goal

Complete the missing React controls over native Frappe and Marley records. Preserve the Add Doctor flow verified in isolated SIT and keep booking rules native. No production rollout, role expansion on login, separate staff roster, or real email.

## Approach

Implement and verify each change separately rather than releasing one combined write path. Use native permission checks, save hooks, and read-back rather than recreating Frappe account or schedule rules in React. Browser acceptance stays on the named synthetic SIT; local implementation approval does not authorize unreviewed site changes or unsafe fault injection.

### 1. Ordinary staff account state

In Staff & roles, let an authorized Owner enable or disable an eligible ordinary Reception/Cashier account. Show the saved state and require explicit confirmation before disabling because Frappe revokes the staff user's sessions. Exclude the Owner's own account, protected/privileged accounts, and Doctor onboarding from these controls; keep advanced changes in Desk. Check fresh workspace and native authority, CSRF, target eligibility, and record version on every write. Keep this a standalone native save, preserve roles, and report any uncertainty without automatic retry. Verify disable, persisted state, active-session revocation, re-enable, and protected-target denial. Separately verify first password setup for a newly created dual-role account using capture-only mail and a fresh React session that can reach both workspaces.

The pinned Frappe logout path can commit before the User and its role rows are saved. Do not advertise React disable until a reviewed save checkpoint can reacquire the lock and recheck the original state after logout and before the actual User write. Desk remains the fallback where this cannot be established. A rejected save can still leave prior sessions revoked; read back the User before offering further action.

### 2. Doctor working hours

Replace sample working hours in the Doctor workspace with the signed-in doctor's native schedule. Resolve the active practitioner through the session's User link and native read permissions, not a client-selected practitioner ID. Show recurring hours and the service unit where readable; show distinct empty and unavailable states rather than falling back to sample hours. Label these as recurring hours, not bookable slots or a promise of availability.

Stock Marley does not grant Physician read access to Practitioner Schedule. Do not broaden permissions to make the panel work: show unavailable if denied, and ask separately before provisioning any narrowly scoped native access needed for positive SIT coverage.

### 3. Reuse enabled hours in Add Doctor

Keep the current private draft and fresh-schedule publication path. As a separate, explicit alternative, let the Owner review and select a readable enabled native schedule and a valid appointment service unit for the approved company. Explain that future changes to a shared schedule can affect all linked practitioners. At Finish, recheck current permissions, the reviewed schedule data and eligibility, and link the chosen schedule without writing, cloning, or toggling it. Preserve existing native booking and availability behavior; reject stale selections and do not overwrite an unrelated attachment. Verify the new Doctor link and the unchanged source schedule, including its existing practitioner links.

Offer reuse only for an unattached draft. The selected source is read-only; it must not be subject to the draft authoring limits or require Schedule create/write permissions. Refuse unsupported native shapes rather than changing them.

### Additional acceptance

Using fictional data, verify that an exact failed invitation retries the original native queue item rather than creating a second message or password token, and that an uncertain result does not trigger a resend. Exercise concurrent Finish from two Owner sessions and verify only one valid setup is persisted. These test cases require a bounded SIT setup, precise read-back, and no automatic retries.
