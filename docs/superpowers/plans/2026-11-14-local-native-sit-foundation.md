# Local Native SIT Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Establish an isolated local Marley and React environment and evidence
the patient integration prerequisites without enabling unproven writes.

**Architecture:** A separate OrbStack Compose project hosts a synthetic-only
Frappe/ERPNext/Marley site and the existing native `clinic_ui` React bundle.
Native patient writes remain disabled until a separate patient-workflow plan
proves the required transactional and operational safeguards.

**Tech Stack:** Docker Compose, Frappe 16, ERPNext 16, Marley Healthcare,
Python, React, TypeScript, Playwright.

**Spec:** `docs/superpowers/specs/2026-11-14-local-native-sit-design.md`

## Global Constraints

- Existing lab and production are out of scope; use fictional data only.
- Bind published services to loopback; use a distinct project, site, volumes,
  and ports. Do not stop or alter the existing 18080 SSH tunnel.
- Keep credentials and browser evidence in private storage, never in Git.
- Do not enable patient or clinical writes until the gates in the spec and
  `docs/superpowers/plans/2026-11-14-marley-native-first-slice.md` are proven.
- No automatic retries or unlocking of uncertain mutations.
- Avoid commits unless separately requested.

## Review Focus

- Existing lab accidentally targeted: assert the configured site and origin are
  the new local site before any setup or browser test.
- Missing or incorrect site gates: assert bootstrap fails closed.
- Secrets exposed: inspect generated configuration and evidence permissions;
  do not print their contents.
- Missing native bundle: confirm manifest and asset URLs resolve.
- Unimplemented functionality mistaken for a pass: mark cases blocked or not
  run until both browser and backend assertions have evidence.

## Task 1: Reproducible isolated deployment

**Files:** Create deployment configuration under `deploy/local-sit/` and
document its commands in `deploy/local-sit/README.md`. Use the existing
`frappe-apps/clinic_ui/` and `frappe-apps/clinic_photos/` as source.

**Interfaces:** Produce a distinct local site and loopback URL, plus private
runtime paths for site secrets and synthetic account credentials. Record exact
image versions and resolved revisions.

- [ ] Inspect the official Frappe container deployment instructions and pin
  compatible Frappe 16, ERPNext 16 and Marley Healthcare revisions; verify
  actual versions inside the running stack instead of assuming image parity.
- [ ] Write and validate the Compose configuration with `docker compose
  -f deploy/local-sit/compose.yaml config -q`; verify host ports bind to
  loopback and project/volume names do not overlap existing stacks.
- [ ] Build the native React bundle using `pnpm --filter @clinic/app
  build:native`, build the backend image, and provision the isolated site.
- [ ] Create only fictional accounts and records on the new site; store their
  credentials outside the repository with owner-only permissions.
- [ ] Verify the exact site identity, `/clinic` shell, assets, session bootstrap,
  native allowlist and disabled write capabilities. Record commands, exit
  statuses and actual versions in a private deployment log.

## Task 2: Patient contracts and recovery evidence

**Files:** Create an assessment under `docs/assessments/` without private
identifiers or secrets. Read `frappe-apps/clinic_ui/` and the SIT catalogue.

**Interfaces:** Produce verified native Patient field and permission mappings,
the exact synthetic actors and fixture aliases, a documented isolated backup
and restore verification, and a decision on legacy-intent continuity.

- [ ] Inspect installed Patient and Gender metadata and verify effective
  create, read and update permissions for each synthetic actor; record
  verified mappings and unresolved questions.
- [ ] Verify the local site's separation from legacy data and intent stores;
  document the evidence and block any continuity claim that cannot be proved.
- [ ] Back up and restore to a separate isolated target, verify the restored
  site and preserve the backup privately. Do not run fault or concurrency
  tests before this succeeds.
- [ ] Produce a patient create/edit/view implementation plan with explicit
  transaction, idempotency, receipt, fencing, conflict and unknown-outcome
  tests. Keep write gates disabled until those tests pass against real Frappe.

## Task 3: Initial SIT report

**Files:** Create a dated report under `docs/assessments/`; keep sensitive
evidence outside the repository and refer to it by non-sensitive alias.

**Interfaces:** Report each executed variant, actor, expected and actual UI
and backend outcomes, status, evidence reference, and blockers.

- [ ] Execute only independently authorized and supported native React cases;
  collect backend evidence for each. Do not count proxy or fixture tests as SIT.
- [ ] Report unsupported create/edit and unexecuted cases as blocked or not run,
  not passed. List remaining role, feature, and traceability gaps explicitly.
- [ ] Record the exact command, exit status, date and evidence location for
  each verification and deployment check.
