# Live Doctor hours: native schedule access

Superseded for the current scope by [React-only Doctor hours](2026-11-24-doctor-hours-react-view-design.md). Native Desk/API schedule access and changes to sharing are not part of that design.

## Goal

Let every eligible Doctor see recurring working hours from the schedules currently attached to their own active Healthcare Practitioner record. Access should follow native attachments automatically, including changes made in Desk, without a one-user exception, copied schedule, or separate staff roster. This is not an appointment-availability change.

## Approaches

- **Selected: live attachment rules.** A dedicated read-only role provides the necessary field-level access. Frappe applies a relationship rule to standard document and list reads, based on the current native practitioner and attachment. There are no per-schedule grants to synchronize.
- **Rejected: materialized User Permissions or shares.** A backfill and maintenance job would need to track every attachment, unlink, reassignment, and disablement. Stale grants could outlive the link. Ordinary document sharing can also bypass the expected filtering.

## Access contract

- Start from an enabled, authenticated clinic Doctor account with exactly one active practitioner whose `user_id` equals that account. Never accept the doctor identity from a caller. Missing, ambiguous, malformed, or inactive relationships confer no new access.
- Give the new role only the read permissions necessary for the Doctor's own practitioner and its linked schedules, including the fields needed by the hours endpoint. Reuse existing practitioner permissions where sufficient; do not broaden unrelated DocTypes or grant create, write, delete, export, report, share, or administration rights. Keep existing field masking effective. Service-unit text stays optional and uses existing native permission.
- For the new authority, standard list and direct document reads must agree: a schedule is readable only when attached to the authorized practitioner via the expected native child table and link fields. Multiple doctors can read one shared schedule if independently attached. Detach, reassignment, account disablement, and practitioner inactivation must remove the new access without a background job or cache delay. The dashboard continues to omit disabled schedules, show **Empty** when it can prove there are no recurring hours, and show **Unavailable** when it cannot safely read them.
- Existing Owner, System Manager, and other staff permissions are unchanged. If a Doctor already has independent native authority, that authority remains; the new role cannot expand it to unrelated records, and the Doctor dashboard still resolves only the signed-in account's practitioner. Qualify mixed-role and role-profile provisioning explicitly rather than assuming all `Physician` users have the same effective rights.

## Native enforcement and rollout

Use the supported Frappe document and list permission mechanisms together, with an exact-site configuration that defaults off. The document and list rules should share one policy definition with equivalent conditions. Verify the deployed schema and active hook profile, including how list queries are constructed, before enabling the role.

**Sharing is a release gate.** Frappe v16 can fall back to DocShare after a document permission refusal, and can add shared records to list results. The design must prevent that fallback from defeating the new scope in supported normal Desk and API operations. Review existing shares (including Everyone), existing permissions, and changes to shares while enabled. Do not globally disable sharing or silently remove existing shares. If a site's share or hook profile conflicts with the guarantee and cannot be safely supported, refuse activation and leave existing rights untouched.

Provide a repeatable enable and disable procedure for the full eligible population. Include a dry-run showing the named role, exact permission delta, eligible and excluded accounts, incompatible shares/profiles, and how doctors onboarded later receive the role. Validate before granting; re-running must not create duplicates or overwrite site-specific role and permission customizations. Rollback removes only app-managed access, preserving unrelated grants. Do not issue an independently usable broad read grant before the restrictions are active. No live grant or deployment is authorized by this design alone.

## Acceptance

- Unit coverage for the policy, equivalent list/detail conditions, invisible unlinked and ambiguous records, schema and share-profile failures, and provisioning/rollback invariants.
- On approved isolated SIT using fictional accounts and the pinned native versions, show each Doctor their own expected hours in React via Playwright MCP. Corroborate native list and direct document access to attached records and denial of unrelated records, including attempted share-based access and ordinary list/API/Desk paths.
- Show two doctors reading a shared schedule; detach one through a supported native workflow and demonstrate that the same authenticated account loses scope while the other keeps it. Cover disabled or reassigned practitioners, new doctor onboarding, independent staff/Owner access, and absence of new write permissions.
- Do not change booking behavior or the existing React layout. Report baseline test failures separately from regressions. Do not expand permissions on production.
