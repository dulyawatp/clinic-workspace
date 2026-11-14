# Native-first Frappe Photo Access Extension Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a locally reviewable Frappe feature-app candidate that restricts protected clinical files without core patches, then prove it on a separately authorized disposable deployment.

**Architecture:** Native role-read/owner-write draft DocTypes remain the primary model. A supported File mixin adds current-clinician and native-read checks; feature lifecycle and narrow external-entry-point restrictions close bypasses without replacing storage or streaming. This plan builds the access foundation, not the full upload/manual-save/recovery implementation owned by the existing durable-drafts plan.

**Tech Stack:** Python standard-library unit tests, native Frappe 16.53.0 extension/hooks/DocTypes, Frappe integration tests and HTTP tests; no new frontend dependencies.

**Spec:** `docs/superpowers/specs/2026-11-06-frappe-photo-access-extension-design.md` (approved); evidence in `docs/assessments/2026-11-06-frappe-native-draft-probe.md`.

## Global Constraints

- All authenticated clinicians may view all patients and saved drafts in this phase; only a draft's author may edit it. No patient-assignment restriction.
- Original image and annotation JSON are separate private files. Originals and committed annotation versions are immutable; manual save remains the only product save behavior.
- Prefer native Frappe; no core patch, monkeypatch, replacement streaming subsystem, SeaweedFS or master-account bypass.
- Existing fixture workspace stays fixture-only. Do not alter `proxy/src/server.ts` live-mode refusal or enable any write capability.
- Local app/test files may be prepared after plan approval. Installing an app, migrations, server configuration changes, new remote synthetic writes and live operations require separately authorized scope. Earlier temporary-probe approval is not app-install approval.
- No dependencies, commits, staging or deployment without owner authorization. Do not run destructive finance imports or access real records during verification.
- Administrator/database/storage operators remain trusted. Ordinary File shares do not enlarge the feature audience.

## Review Focus

1. Revoked owners and explicit shares: current enabled clinician membership is mandatory despite native shortcuts; Task 2 and Task 4.
2. Aliases and orphan intervals: storage identity, not optional `fid` or current attachment alone, decides protection; Task 2.
3. `get_content` and raw readers: neither `super()` nor unrelated import-document permission authorizes clinical bytes; Tasks 2–4.
4. Cross-author mutations: reads cannot confer replace/relink/reown/delete/publicize rights; Task 2 and Task 4.
5. Cache/delivery and partial failures: no-store reaches both download modes; a denied or failed operation must not leak bytes or appear committed; Tasks 3–4. Durable publish/recovery remains blocked under the parent plan.

## File structure and settled decisions

Create a standard native feature app under `frappe-apps/clinic_photos/`, with package `clinic_photos`. Its application source is distinct from the React/proxy workspace. Build metadata follows the supported Frappe app format; do not install build dependencies as part of this planning task.

- `clinic_photos/hooks.py`: supported File extension, restrictive permissions/query hooks, narrow whitelisted-method override, response-header hook.
- `clinic_photos/access/policy.py`: framework-independent policy value types and decisions.
- `clinic_photos/access/frappe_context.py`: fresh enabled-user/role lookup, native-read adapter, server-owned publication context.
- `clinic_photos/access/storage.py`: canonical private storage key and feature-record/alias lookup.
- `clinic_photos/access/file_mixin.py`: File download/content and lifecycle/derivative restrictions.
- `clinic_photos/access/permissions.py`: restrictive File document/list conditions; never broad grants.
- `clinic_photos/access/entrypoints.py`: guarded bank import wrapper and response no-store hook.
- `clinic_photos/clinic_photos/doctype/{clinic_photo,clinic_photo_draft,clinic_photo_annotation_version}/`: native JSON/controller modules.
- `tests/unit/`: pure policy, path and wrapper tests; no installed Frappe needed.
- `clinic_photos/tests/`: disposable-site integration and actual HTTP route tests.

Prefix the source paths above with `frappe-apps/clinic_photos/`. Module directories also need normal `__init__.py` files. Do not add unrelated app modules.

**Role contract:** candidate DOC = Physician; candidate NUR = Nursing User. Unit/integration tests cover both; the installed role mapping is not activated until recorded and approved as a backend contract. Healthcare Administrator/System Manager alone are not clinician membership. Administrator fixture setup/cleanup is not an ordinary clinical-read principal.

**Feature records:** Clinic Photo stores patient reference, original File reference, canonical storage key, original SHA-256 and intrinsic dimensions. Clinic Photo Draft stores photo reference, immutable author, current version number and current version reference. Clinic Photo Annotation Version stores draft reference, version number, annotation File reference, canonical storage key and state `pending`/`committed`. File/hash/reference fields are server-owned. Do not add a generic storage registry: use these feature records, indexed storage keys and a unique draft/version constraint. No public create/upload/publish API is enabled by this access-foundation plan.

**Storage identity:** canonical site-relative private path after URL decoding, traversal/symlink confinement and native path validation; never a content hash alone. Resolve all File rows sharing that path. Use server-generated reserved `clinic-photo-<uuid>` / `clinic-annotation-<uuid>` basenames as an orphan fail-closed namespace, not sole evidence of ownership. Reject unregistered reserved files. Protected uploads must disable native cross-record dedup reuse; same-patient reuse is resolved at feature level before upload. The parent upload/save plan must establish this before any product upload is enabled. Do not overwrite existing files or silently change site-wide EXIF settings.

**Publication boundary:** no client-supplied owner or bypass flag. The parent durable-save operation must acquire a draft/version lock, register a pending version/storage key before bytes become reachable, create one separate private File, and commit linkage/version atomically with durable reconciliation. A private server-created, request-local context binds actor, draft, operation and expected storage key for narrowly permitted pending insertion/cleanup. An HTTP parameter or arbitrary `ignore_permissions` flag cannot create this context. Until that operation's idempotency/crash gates pass, only test factories may create these objects.

## Task 1: Pure policy and native schema candidate

**Files:** app skeleton/build metadata; `access/policy.py`; three DocType JSON/controllers; `tests/unit/test_policy.py`.

**Interfaces:** frozen `Actor(user: str, enabled: bool, clinician: bool)`; `ProtectedRecord(author: str, committed: bool)`; `may_read(actor: Actor, native_read: bool) -> bool`; `may_edit_draft(actor: Actor, author: str, native_write: bool) -> bool`.

- [ ] Write tests asserting `may_read` is true only for enabled clinicians with native read, and `may_edit_draft` only for enabled clinician authors with native write. Cover Guest, disabled users, non-clinicians, both clinician roles and an unrelated administrator role; deny cross-author edits.
- [ ] Run `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -p test_policy.py -v`; record the initial import/behavior failure.
- [ ] Implement only those policy functions and native schemas. Explicitly set every permission flag: general clinician rows read/create drafts but not write/delete/share; owner rows allow draft write, not share/delete. Versions and photo storage fields are not client-writable. Native `DocPerm` defaults are unsafe to omit.
- [ ] Repeat the command and record passing assertions. Confirm no runtime Frappe import is needed for pure tests and all schema rights are explicit. No app installation.

## Task 2: Protected identity and File boundaries

**Files:** `access/storage.py`, `frappe_context.py`, `file_mixin.py`, `permissions.py`, `hooks.py`; `tests/unit/test_storage.py`, `test_file_policy.py`; `clinic_photos/tests/test_file_policy.py`.

**Interfaces:** `canonical_private_key(file_url: str) -> str`; `classify_file(file_doc) -> ProtectedRecord | None`; `require_protected_read(file_doc) -> None`; `ProtectedFileMixin.is_downloadable()`, `.get_content(encodings=None)` preserve native signatures. Unrelated Files retain native behavior.

- [ ] Write failing path/alias tests for URL decoding, traversal, non-private URLs, reserved-but-unregistered paths, shared URL aliases, hash-equal/different-path files and pending registrations. Do not classify an unrelated file merely because its content matches a clinical file.
- [ ] Write failing read tests proving both overridden methods deny revoked owners/shared non-clinicians, require native authorization even for clinicians, and check before in-memory-content return. Lifecycle tests reject cross-author changes, ownership transfer, relink, replacement, committed delete and private-to-public conversion. Read access must not become write permission.
- [ ] Run `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest discover -s frappe-apps/clinic_photos/tests/unit -v`; preserve RED evidence.
- [ ] Implement classification via authoritative feature references plus canonical alias identity and reserved orphan denial. Fresh actor lookup reads current enabled status and current role membership, not session-cached role grants. `get_content` explicitly calls native File/document read authorization; `super().get_content` alone is not authorization.
- [ ] Register `extend_doctype_class` for File. Apply restrictive document and query hooks to metadata reads; query hooks cover `get_list`, not privileged `get_all`. Keep privileged internal lookups scoped and never return their results directly. File lifecycle checks and native feature controllers reject protected mutations, including changes from previously protected state. Guard `save_file`/private transition methods before side effects, not only eventual `validate`.
- [ ] Deny `make_thumbnail` and `optimize_file` for protected bytes initially, and prevent content overwrite/private conversion by lower-level File methods. Internal pending insertion/cleanup requires the bound server context; generic flags do not bypass policy. Preserve legitimate unrelated File behavior.
- [ ] Repeat unit tests. Author original immutability and committed-version immutability must pass without relying on UI controls. Add Frappe integration tests but do not claim them run locally without a site.

## Task 3: External raw reader and response-cache closure

**Files:** `access/entrypoints.py`, `hooks.py`; `tests/unit/test_entrypoints.py`; `clinic_photos/tests/test_entrypoints.py`.

**Interfaces:** `convert_mt940_to_csv(data_import, mt940_file_path)` matches the installed ERPNext callable; `after_request(request, response) -> None` matches the installed response hook. The wrapper delegates unchanged for non-protected files.

**Approved ambiguity amendment:** If an input matches both protected and unrelated File records (including duplicate basenames or conflicting document-name/filename matches), refuse the ambiguous import rather than follow native arbitrary first-row selection. The user explicitly approved this conservative exception to unchanged unrelated delegation. Unambiguous unrelated imports retain native behavior; no records are modified or deleted by this refusal.

- [ ] Write failing tests that the Bank Statement Import wrapper canonicalizes/classifies the supplied path and rejects protected or reserved-orphan bytes **before** calling the raw-reader import function. Verify unrelated inputs delegate with unchanged arguments and exceptions; no global finance role changes.
- [ ] Write failing cache tests covering protected URL/API/ZIP responses and denials, normal/accelerated responses, and an unrelated response. Required protected headers: `Cache-Control: no-store`, `Pragma: no-cache`, `Expires: 0`; remove contradictory freshness headers.
- [ ] Run the full pure unit command from Task 2 and record RED.
- [ ] Register `override_whitelisted_methods` for `erpnext.accounts.doctype.bank_statement_import.bank_statement_import.convert_mt940_to_csv`; preserve its whitelisted POST-only contract and callable signature. Do not alter Chart of Accounts Importer: its local helper already uses `find_file_by_url`. Register the supported `after_request` hook and use a server-owned per-request protected-byte marker set before authorization; handle direct protected paths and ZIP marker propagation. No storage I/O in the header hook. Frappe catches after-hook exceptions, so this hook is never the byte-authorization boundary.
- [ ] Keep direct-image handling bounded: File thumbnail/optimization is denied in Task 2. Installed `get_thumbnail_base64_for_image` rejects private URLs before opening images; add that expectation to site acceptance, rather than monkeypatching the helper. Native PDF inlining reaches `find_file_by_url`. Any newly reachable direct reader is an activation blocker.
- [ ] Repeat unit tests and capture PASS. Define actual HTTP cases for raw-reader rejection and no-store but run no finance method or app deployment without authorization.

## Task 4: Disposable installed-site and HTTP acceptance

**Files:** `clinic_photos/tests/test_file_policy.py`, `test_entrypoints.py`, `test_http_photo_access.py`; `docs/assessments/2026-11-06-frappe-photo-extension-acceptance.md`.

**Consumes:** settled local candidate, reviewed policy/schema/hooks, explicitly authorized disposable target and installation/cleanup procedure. This task is blocked until that authorization; prior probe writes do not authorize it.

- [ ] Obtain approval for exact target, app install/migration/restart effects, synthetic principals/records, method calls and cleanup. Use a disposable clone if shared container changes would affect other applications. Never install by modifying the running container ad hoc.
- [ ] Run the app's focused integration suite using `bench --site "$PHOTO_TEST_SITE" run-tests --app clinic_photos` from its authorized bench; confirm the installed CLI supports that command before execution. Record MRO/method resolution and both Physician/Nursing User results. No production identity mapping is activated by a successful test.
- [ ] Implement the HTTP harness with standard-library urllib/unittest, no Frappe imports on the client. Require `CLINIC_PHOTO_TEST_BASE_URL` and `CLINIC_PHOTO_TEST_MANIFEST` (path to an access-restricted, untracked JSON file of exact synthetic record IDs and test-session references, provisioned only under the approved setup). Refuse a missing manifest or non-disposable target. Never print session values or commit the manifest. Run `PYTHONPATH=frappe-apps/clinic_photos python3 -m unittest clinic_photos.tests.test_http_photo_access -v`. Separately verify the authorized frontend nginx path before claiming accelerated-delivery acceptance. Never send credentials over evaluation HTTP; use authorized TLS or SSH-local transport, and redact cookies/tokens from reports.
- [ ] HTTP matrix: author, other clinician, non-clinician, Guest, role-revoked owner with existing session, disabled user and explicit File share; direct URL, API download, ZIP, optional `fid`, alias rows, list/metadata reads, mutation RPCs and raw-reader inputs. Compare body/byte hashes for allowed reads; denials contain no original/JSON bytes and leave state unchanged. Verify no-store headers through nginx and that `/protected/` cannot be requested directly.
- [ ] Test native thumbnail helper private-URL rejection, protected thumbnail/optimization denial, unrelated bank inputs unchanged, and protected bank inputs rejected before raw read/write. Do not use real accounts or perform destructive chart imports.
- [ ] Cleanup only exact synthetic objects in dependency order; verify File records, bytes, draft/version/photo records and users absent. Report retained migration/schema/audit effects honestly. If cleanup fails, stop and report exact remaining objects; do not broaden deletion.
- [ ] Reconcile implementer evidence and run final focused validation once through an independent reviewer/test owner. Record commands, exit statuses, test counts, environment, role mapping and unresolved readers. Do not sign installed contracts on the owner's behalf.

## Completion boundary and handoff

Completing Tasks 1–3 yields a local candidate, not a working live draft feature. Task 4 establishes only its stated disposable-site access claims. Manual-save publication, durable operation keys, conflict/restart reconciliation, upload/dedup behavior, signed installed contracts and backup/restore remain owned by `2026-11-06-frappe-private-photo-drafts.md` and Plan 06. No automatic capability activation, frontend persistence switch, production install or commit follows this plan.

**Self-review:** policy/schema→Task 1; alias/content/mutation/revocation→Task 2; raw reader/cache→Task 3; HTTP/MRO/actual state invariants→Task 4. Parent recovery/backup requirements remain explicit blocked dependencies rather than omitted or falsely completed tasks.
