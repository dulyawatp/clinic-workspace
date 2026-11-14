# Marley Native First Slice Plan

**Goal:** Move the clinic UI's first synthetic patient read and registration
slice toward native Marley sessions without relying on the local proxy.

**Architecture:** A Frappe-side clinic API enforces role, native authority,
synthetic scope and operation safety; React uses same-origin native sessions.
Registration remains disabled until its transaction and legacy-lock gates are
proven. The existing `clinic_photos` extension and data remain intact.

**Tech stack:** Frappe 16, MariaDB, Python, React/TypeScript, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-14-marley-integrated-lab-design.md`.

## Constraints

- Lab direct HTTP is explicitly accepted; never deploy to production.
- Test only fictional data in explicitly synthetic records.
- No account or permission changes without separate approval; no deletes,
  queue clearing, uncertain retries, staging, commits or pushes.
- Preserve existing proxy intent database, photo data, native permissions and
  publication gates. Do not bypass old uncertain intents.
- Keep credentials and sensitive runtime evidence private.

## Review focus

- Clinic endpoints must enforce synthetic scope; existing direct native
  resource access remains broader by explicit lab decision.
- Missing/revoked session, changed roles or invalid CSRF must fail closed.
- Same operation submitted concurrently, or a lost response after commit,
  must not create a duplicate.
- Old unresolved proxy intents, missing databases and restored state cannot
  authorize a new mutation.
- Existing Frappe routes, saved photos and shared clinician access must survive
  deployment and rollback.

## Task 1: Prepare deployable native session and directory contracts

**Inputs:** `app/src/app/SessionProvider.tsx`, `app/src/transport/auth.ts`,
`app/src/features/patients/live-directory.ts`,
`proxy/src/routes/live-metadata.ts`, `frappe-apps/clinic_photos/clinic_photos/`.

- [ ] Document exact native-session, CSRF and synthetic-directory DTOs, role
  mappings and error semantics; reuse existing client DTOs where practical.
- [ ] Select a Frappe SPA route that preserves existing `/clinic/*` deep links
  without changing existing `/api`, `/app` or `/desk` behavior. Validate the
  actual Frappe 16 routing and CSRF mechanisms against installed version.
- [x] Inventory direct native endpoint access for the synthetic test accounts
  without changing permissions or printing record contents. REC, DOC and NUR
  have broad native Patient access; the user chose to retain that access in the
  lab and scope the new clinic endpoints only.
- [ ] Define isolated tests for guest and revoked sessions, every role,
  denied patients, malformed DTOs, CSRF and existing-route preservation.

## Task 2: Registration safety gate

**Inputs:** `proxy/src/routes/patient-create.ts`,
`proxy/src/writes/patient-create-store.ts`,
`app/src/features/reception/PatientRegistration.tsx`, and the approved spec.

- [ ] Specify the exact receipt identity, payload binding, native transaction
  and uniqueness rules; ensure the submitted request key survives reloads.
- [ ] Specify how legacy proxy intents and their identity mappings are
  preserved and checked; missing or uncertain continuity blocks registration.
- [ ] Define database-backed tests for concurrent requests, payload conflicts,
  commit followed by lost response, process crash and stale backup restoration.
  A fake Frappe unit test alone is not transaction evidence.
- [ ] Keep registration disabled until an independent review and isolated
  transaction tests prove the gate. Never infer non-commit from a current read.

## Task 3: Implementation and lab validation, after gates

- [ ] Implement the approved native session and synthetic directory contracts
  test-first in Frappe and React. Unsupported features remain disabled.
- [ ] Implement registration only after Task 2's protocol and continuity
  proofs are approved; otherwise report it blocked.
- [ ] Before any lab deployment, verify a private backup and a rollback plan
  that cannot erase write receipts, intent locks, photos or audit evidence.
  Confirm the precise image, route and service changes; do not change roles.
- [ ] Run unit, database integration and browser tests in the lab. Separate
  positive and denial checks for all seven roles; use only synthetic records.
  Record exact commands, counts and limitations without exposing secrets.

## Planning gate

Tasks 1 and 2 require concrete route, schema and transaction decisions before
code changes. Record those decisions and their tests in a focused executable
plan; if the native security or legacy-intent gate cannot be met, do not
enable registration. Later appointment, photo, clinical, finance, stock and
administration slices require their own contracts and tests.
