# Marley live integration design

## Intent and scope

Connect the new React UI to the same Frappe/Healthcare backend as Marley Healthcare and verify real workflows for REC, DOC, NUR, PHA, CAS, MGR and ADM. Production use is the goal; fixture tests or Frappe Desk tests do not establish live UI coverage. Deliver in phases, with unsupported functionality clearly unavailable until implemented and verified.

## Approaches

- **Selected: phased, least privilege.** Verify installed contracts and permissions, connect one workflow at a time using each user's native Frappe session, and test it in the React UI. Propose backend account and permission changes separately for approval. This limits clinical and operational risk while making incremental progress.
- **Use existing broad grants:** faster for the four existing identities but reception and owner currently have nursing permissions beyond their intended UI duties; it also leaves three dedicated test identities missing. Not selected.
- **Generic privileged backend proxy:** convenient but bypasses native user authority and is unsuitable for patient data. Rejected.

## Constraints and design

- Use Marley `marley.local` at `10.0.100.10:8080`, accessed securely through SSH or HTTPS; never send credentials over remote plaintext HTTP. Keep credentials and evidence private.
- Earlier inventory described this site as evaluation-only; the user selected it as the Marley backend but production deployment readiness and hosting are not established. Do not claim it is production-ready without verifying those separately. The current proxy requires an HTTPS origin or an HTTP loopback SSH tunnel, not the remote plaintext address.
- Retain separate fixture and live modes; never substitute fixture data in live mode. Unimplemented workflows must fail closed and be visibly unavailable.
- Authenticate individual users and make backend calls with their own Frappe sessions. Map native roles to UI roles only after verifying the installed assignments and intended authority; UI navigation is not an authorization boundary. Do not map every clinician to DOC.
- Start with verified, narrowly scoped live reads and synthetic records. Add other domains and writes incrementally after verifying their backend contracts and authorization. No role, permission, or account changes on Marley without explicit approval of the proposed changes.
- Existing Marley test accounts cover reception, doctor, pharmacy and owner; dedicated nurse, cashier and administrator test identities are not documented. Existing account role bundles are not assumed least-privilege. Verify record-level access as well as doctype permissions.
- Proxy code and configuration may be changed to support verified role mappings; changes to Marley accounts, roles and permissions require separate approval. The current proxy rejects the special Administrator identity and hardcodes DOC, so an appropriate dedicated admin role and identity need an explicit approved design rather than administrator impersonation.
- The Marley site does not currently have the isolated photo extension. Do not assume photo features exist there or deploy the extension without a separate approved plan. Preserve private file authorization, clinician-wide saved-photo reads, author-only draft edits, immutable originals, independently versioned annotations and manual saves when photo functionality is later enabled.
- Photo workflows on Marley remain blocked until that extension and its deployment are separately approved and verified; other roles must be tested against their own implemented domains, not the isolated photo lab.
- Preserve Thai default, English support and accessibility. Use only clearly synthetic test records for mutations, never modify real patient records, delete records, clear queues or replay uncertain operations. Keep backups; do not stage, commit or push.

## Verification and delivery

For each role and workflow, verify the installed field and permission contract, implement narrow per-user read/write paths, run focused automated checks, then exercise the actual React UI in a real browser against Marley using synthetic accounts and records. Include positive and negative authorization checks and report blocked or missing workflows honestly. Seven-role completion requires distinct appropriate identities and actual live UI evidence for each role; neither fixture coverage nor administrator impersonation qualifies. Maintain a private record of synthetic mutations and avoid unsafe retries.

The existing live browser harness is tied to the isolated photo site and must be adapted or replaced for Marley. Keep sanitized per-run assertions and outcomes in private evidence files without credentials, patient data, screenshots or traces. Pace logins within the proxy's rate limit; report throttling as untested, not as an authorization denial. Record proposed role mappings and permission changes for user approval; if a role lacks the necessary native authority, mark the relevant workflows blocked until approved changes are implemented. For each phase, finish only after positive and negative checks pass or explicitly report blockers; retain and verify an appropriate backup before backend changes.

## Current limits

Live proxy authentication currently assigns DOC to admitted users; fixture-backed read endpoints reject live mode; most role-specific live screens and writes are not implemented. Existing native grants and record scope must be verified on the target site rather than inferred from fixture mappings or isolated-lab results. The earlier Desk report overstated its assertions and is not evidence of React coverage.
