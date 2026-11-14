# Frappe Private Photo Drafts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable manually saved photo annotation drafts, readable by all authenticated clinicians and editable only by their author, using separate Frappe private files after installed-site and operation gates are verified.

**Architecture:** Prefer native Frappe DocTypes, role permissions, owner-restricted writes and private File attachments/delivery. Keep originals immutable; store annotation JSON in a separate private file associated with a draft record and original metadata. The existing proxy uses the clinician's session, without master-account bypass; direct backend routes must enforce the same policy. Add only minimal verified extensions, without core patches. Concrete DocTypes and endpoints require installed-site verification.

**Tech Stack:** React/TypeScript, existing Fastify proxy, Frappe/ERPNext private File and verified site extensions; Vitest and Playwright.

**Spec:** `docs/superpowers/specs/2026-11-06-frappe-private-photo-drafts-design.md`; existing clinic spec and `docs/superpowers/plans/2026-11-05-clinic-06-write-activation.md` safety gates remain authoritative.

## Global Constraints

- No real-data access or writes until the owner authorizes site verification and the applicable signed installed-site contracts and operation gates pass. Fixture data is not production evidence.
- Manual save; all authenticated clinicians may view all patients and saved drafts in this phase, without patient-assignment restrictions. Only the author may edit a draft. Each original and annotation JSON is a separate private file. This approved amendment supersedes the earlier author-only visibility requirement for this feature.
- Exact-byte duplicate within the same patient: warn and offer reuse/linking; never leak cross-patient matches or merge records automatically.
- Original bytes remain immutable. Use the canonical versioned annotation document and verified original hash/dimensions; no invented metadata.
- Enforce clinician-role reads and author-only draft mutations on the server, including direct File access. Deny anonymous/non-clinician access; verify exact installed role mappings. Existing File owners must not retain access after losing the clinician role. Privileged infrastructure administrators remain a trust boundary.
- Back up database and private files to a separate device; destination, encryption, retention and tested restore are release gates.
- No commits without owner authorization. Do not weaken existing disabled-write defaults or silently override the audit findings.

## Review Focus

1. **Direct File/API bypass:** other clinicians may read, but cannot change another author's draft or annotation File; anonymous/non-clinicians cannot read, including former clinicians who own a File. Test record and byte routes, sharing and role revocation.
2. **File succeeds but draft record fails, or proxy dies after upstream commit:** no false success, no duplicate retry, deterministic reconciliation and recoverable draft; test both crash windows.
3. **Two simultaneous saves or duplicate uploads:** deterministic version conflict or duplicate resolution, no overwritten original or unintended merge; test concurrent requests.
4. **Original changes or metadata mismatches:** refuse to apply the annotation to the wrong bytes, preserving local unsaved work; test hash, dimensions and version mismatch.
5. **Mac or disk loss:** restore the database and both kinds of private file to an isolated target and verify references and access; record actual restore evidence.

## Task 1: Verify installed-site contract and access model

**Files:** `Clinic_Backend_Contract_Verification_v3.md` and existing `docs/` backend evidence location; `contracts/backend-manifest.json` only after verification. No application implementation in this task.

- [ ] Identify installed Frappe/ERPNext versions, custom apps/hooks, private File storage and backup facilities, on an owner-authorized disposable site.
- [ ] Verify clinician-scoped APIs and native DocTypes/permissions available for original file, draft record and annotation JSON private file. Record exact fields, role mapping, owner-write rules, file access behavior, version/concurrency rules, duplicate lookup, audit and cleanup. Prefer native configuration; identify only proven gaps requiring extension code.
- [ ] Test author and other-clinician reads; deny other-clinician edits through draft and File APIs, including attachment changes, deletion and byte replacement. Deny anonymous/non-clinician reads. No patient-assignment denial is required in this phase.
- [ ] Resolve installed-source findings in `docs/assessments/2026-11-06-frappe-site-inventory.md`: downloads call File's permission function directly, ownership can bypass linked-record checks, and any accessible File row sharing a URL can authorize bytes. Verify clinician-role removal, explicit shares, duplicate byte references, both download routes, nginx protected delivery and browser caching. Do not assume a generic File hook or private parent closes these paths. Record whether native controls suffice before proposing a minimal extension.
- [ ] Record dated, signed evidence and unresolved gaps. **Stop with writes disabled if any required permission, endpoint or contract is missing.** Update downstream task interfaces and exact filenames only after evidence exists; do not guess them from upstream documentation.

## Task 2: Resolve durable operation and ownership design

**Files:** approved write activation plan, `contracts/backend-manifest.json`, `proxy/src/operations/` as appropriate once Task 1 is verified; tests beside implementation.

- [ ] Resolve the operation-key scope and reconciliation conflicts recorded in `docs/assessments/2026-11-06-spec-kit-revalidation.md` before enabling these operations. Define a `(user, operation, requestKey)` ownership model and current-access reauthorization.
- [ ] Specify verified save transaction boundaries: creating/uploading the annotation file, associating/updating the clinician-readable, author-editable draft record and handling orphaned files. Define version conflicts, permission revocation and duplicate requests without overwriting originals.
- [ ] Add failing tests for duplicate replay across users/operations, revoked access, concurrent saves, partial file/record failure and deterministic upstream reconciliation after real process-kill windows. Implement only after Task 1 provides exact contracts; run those tests and document evidence.
- [ ] Keep activation `BLOCKED_UNVERIFIED` until all per-operation gates and restart tests pass.

## Task 3: Patient-scoped duplicate detection and reuse

**Files:** verified Frappe integration and `proxy/` operation files determined in Task 1; patient/photo UI in `app/src/features/photos/`; tests alongside those modules.

- [ ] Write tests for exact-byte same-patient duplicate warning and explicit reuse/link; same filename with different bytes remains distinct; no cross-patient match is returned or implied; simultaneous duplicates cannot accidentally create or merge records.
- [ ] Implement using the verified per-patient contract and authorized session; keep original metadata, encounter links and annotation drafts distinct. No cross-patient deduplication assumption.
- [ ] Run focused proxy and UI tests; report exact commands and results. Do not activate until authorization and duplicate-safety gates pass.

## Task 4: Manual draft save and reopen

**Files:** `app/src/features/photos/` draft/editor modules and verified proxy write/read adapters; tests alongside each module.

- [ ] Write tests for Save draft, visible saving/saved/failed/unknown states, author and other-clinician reopen on another authorized session, author-only edits, denied anonymous/non-clinician file reads, hash/dimension mismatch and no mutation of the original.
- [ ] Implement verified proxy and UI interfaces; retain recoverable local work after failed/unknown save, and protect dirty navigation. Do not treat file upload alone as a successful draft save.
- [ ] Test locale changes, role change/logout, stale content and concurrent version conflicts. Run focused tests, typecheck and lint; preserve evidence by environment class.

## Task 5: Backup, restore and acceptance

**Files:** operational runbook and evidence under `docs/`; exact names chosen after the destination and installed environment are verified.

- [ ] Document destination, encryption, key ownership, backup consistency, schedule, retention, monitoring and access controls for database plus original and annotation private files.
- [ ] Run an owner-authorized restore to an isolated target and verify original hashes, annotation files, draft links, clinician-read and author-write permissions. Record date, environment, operator and results; do not expose real patient data in reports.
- [ ] Run unit, integration and browser tests separately against fixtures and the disposable installed site; include negative permissions, cross-device manual save/reopen, duplicate handling, process-kill recovery and backup restore.
- [ ] Request explicit clinical/security approval before enabling any real write. If any contract, test, approval or restore is missing, keep the operation disabled and report the blocker.
