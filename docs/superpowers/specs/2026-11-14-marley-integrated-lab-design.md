# Marley-integrated clinic UI: lab design

## Intent

Run the React clinic UI against the lab Marley instance with native Frappe
sessions and server-side clinic functionality, without a local Fastify proxy.
Exercise read and write workflows with fictional details in synthetic Marley
records. Production is a separate machine and is out of scope.

## Approaches

- **Hosted Fastify proxy:** reuses existing controls but retains a separate
  application service and its local intent database.
- **Marley-integrated application:** the user's chosen approach. Move the
  required server-side controls into Frappe and serve or route the React UI
  from the Marley origin. Migrate incrementally; unsupported operations remain
  disabled rather than bypassing a missing control.

## Lab transport and boundaries

- The user explicitly permits direct HTTP to the lab IP and port for functional
  tests, superseding the earlier tunnel-only restriction for this lab only.
  HTTP exposes credentials, session cookies and clinical data to network
  observers; use dedicated synthetic lab accounts and fictional details only.
  The user accepts exposure of synthetic data in the lab; credentials and
  session cookies remain sensitive. Do not use this exception for production.
- Keep credentials, identifiers, backups and evidence private. Do not alter
  real patient data, delete records, clear queues, or retry uncertain writes.
- Do not change Marley accounts or permissions without separate approval.
  No production deployment, staging, committing or pushing.

## Server and client behavior

- Authenticate as individual native Frappe users. Verify same-origin sessions,
  CSRF enforcement, role mapping, least-privilege native authorization and
  fail-closed handling for unsupported roles and routes. Never put a service
  credential in React.
- Replace proxy dependencies incrementally with authenticated Frappe methods.
  Keep the synthetic allow-list and server-side record authority checks for
  directory, search, linked records, photos and writes. The user chose to
  retain existing Marley access in the lab: native routes reachable by the
  same session may expose a broader set of records. The clinic allow-list is
  not a boundary for those routes and does not establish production-ready
  access control. Tests must access only explicitly synthetic records.
  A role's UI navigation is not an authorization boundary.
- Retain shared clinician reads of saved photos, immutable originals,
  independently versioned annotations and manual saves. Preserve Thai as the
  default, English, and accessibility. Preserve the existing photo-specific
  draft ownership, private-file authorization and same-patient reuse rules;
  these do not impose author-only editing on physician encounters.
- Before enabling a new mutation, establish native validation, authorization,
  an operation-bound idempotency and durable outcome protocol, and safe
  handling of uncertain results. A current read, timeout or operator assertion
  cannot prove an earlier request has finished. Existing legacy uncertain
  intents cannot be resolved merely by introducing a new protocol.
- Booking, payments, stock, formal clinical notes and administration remain
  disabled wherever their native contracts or existing permissions are
  unverified. Permission changes require separate approval.

## Migration and verification

1. Inspect the existing installed Frappe app and lab configuration read-only;
   define SPA hosting, native session/CSRF, and route namespace without
   changing existing site routes.
2. Design and test an initial end-to-end slice: synthetic patient directory
   read plus synthetic patient registration, with native sessions, server-side
   role checks and durable duplicate/uncertainty controls. Registration is a
   proposed first write, conditional on native contract, permission and safety
   verification; otherwise it remains disabled.
3. Migrate remaining supported surfaces individually, including photo
   functionality, then implement missing workflows only after verifying each
   native contract and required authority. Do not silently broaden access.
4. Run isolated automated unit, integration, failure-injection and browser
   tests; then run lab E2E with fictional records. Distinguish fixture,
   read-only, write and role-boundary evidence from complete workflows and
   production readiness. No uncertain operation is retried.

This design does not select a particular native receipt/fencing mechanism or
claim it is already proven. A detailed implementation plan must define it and
its transaction, crash, concurrent-execution and recovery tests before writes
are enabled. The existing `clinic_photos` installation remains in place; its
publication controls are not to be weakened.

## Migration and deployment safeguards

- Preserve the old proxy database and its identity and allow-list mappings.
  The new native path must not bypass pending or unknown legacy intents for
  the same logical mutation. If continuity cannot be established, block that
  mutation; a new operation identifier is not a reset.
- Inventory the lab's existing routes and installed extension before deployment.
  Verify a backup and a scoped rollback plan before changing lab software or
  configuration. A rollback or stale database restore must not silently erase
  operation receipts, locks, audit evidence or files. Keep publication
  default-off unless separately and explicitly enabled for a controlled test.
- For each migrated role and workflow, test authorized and denied access,
  synthetic scoping, duplicate prevention, version conflicts and shared edits,
  crash and uncertain-outcome behavior, as applicable. Keep a seven-role
  acceptance matrix; the first slice precedes, rather than replaces, the
  broader objective.
