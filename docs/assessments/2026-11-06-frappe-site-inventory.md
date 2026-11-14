# Frappe evaluation-site inventory (partial)

**Date:** 2026-11-06  
**Environment:** `10.0.100.10:8080`, HTTP evaluation-only, not production.  
**Status:** Preliminary read-only inventory; not signed contract verification and not authorization to enable reads or writes.

- Unauthenticated `GET /api/method/ping`: HTTP 200, `pong`.
- Unauthenticated `GET /api/method/frappe.utils.change_log.get_versions`: HTTP 403, method not whitelisted. No credentials were sent over HTTP.
- SSH as authorized by the user revealed a running `marley-health-backend-1` container with image `marley-health:16.6.1`. From its bench directory, `bench version` reported Frappe 16.53.0, ERPNext 16.52.1, Healthcare 16.6.1.
- `bench --site marley.local list-apps` reported the same app versions and `UNVERSIONED` for each. This is version inventory, not a source-revision or integrity guarantee.
- Read-only metadata inspection succeeded from the bench `sites` working directory after initial interpreter, site-path and logger-directory errors. `File` has `is_private: Check`, `file_url: Code`, `attached_to_doctype: Link`, `attached_to_name: Data`, and `content_hash: Data`. No DocType name matched `%Annotation%` or `%Photo%`; other relevant DocTypes with different names are not ruled out. `ignore_file_permissions` hook was not configured. These observations do not establish record/file access behavior or a draft schema. The virtualenv emitted a `sys.prefix` warning; the requested metadata was returned successfully.
- No patient records, credentials, existing file contents, or configuration secrets were requested or recorded. No migrations or configuration changes were performed.

## Authorized synthetic File-permission probe

The user confirmed `marley.local` is disposable and explicitly approved creation and cleanup of two uniquely named synthetic users and one unattached private JSON File. The probe used `frappe.has_permission('File', ptype='read', doc=file, user=...)`, not HTTP downloads. The File was verified private and owned by the first synthetic user. Results: owner **allowed**; second synthetic user **denied**; Guest **denied**. The exact created File and both users were deleted successfully. Audit/log entries may remain.

This is evidence for one unattached File and those test identities only. It does **not** verify a patient-linked File, a future author-private draft DocType, direct HTTP file delivery, clinical roles, cross-patient isolation or production authorization. No signed contract has been established.

## Installed File permission code inspection

Read-only inspection of the installed Frappe 16.53.0 `frappe.core.doctype.file.file.has_permission` shows that the File owner can read a private file; a user with an individual File share may also be granted access; and, if attached, a user who can read the linked document may be granted File read access. The registered File permission hook points to this function. Therefore **attaching an annotation file directly to a Patient or other broadly readable record cannot be assumed to preserve author-only draft privacy**. Actual HTTP delivery and document permission behavior remain untested. The site contains `Clinical Note`, `Patient` and `File` DocTypes; their presence does not establish an author-private draft schema. Other app hooks exist, but their effect on the proposed operations has not been determined.

Further metadata inspection found no DocType name matching `%Draft%`. The installed `Clinical Note` metadata has one permission row, for System Manager, and links including Patient, practitioner, User and a dynamic document reference. The `File` metadata includes rows for System Manager and All, in addition to its File-specific permission hook. These are metadata observations, not proof of effective clinician permissions: user roles, shares, custom hooks, record scope and direct-file routes require explicit tests. No patient or clinical records were queried.

## Installed private-download source trace

Read-only SSH source inspection completed with exit status 0 for both Python extraction commands. No application initialization, document queries or file-byte reads were performed during this trace. Paths below are relative to `/home/frappe/frappe-bench/apps/frappe/frappe/` in the backend container.

- `app.py:138–139` routes `/private/files/` to `utils.response.download_private_file`.
- `utils/response.py:294–306` explicitly rejects Guest, then calls `find_file_by_url(path, name=frappe.form_dict.fid)` and denies access if no downloadable File is found.
- `core/doctype/file/utils.py:462–475` retrieves File rows matching the URL (and optionally `fid`). It returns the first row whose `is_downloadable()` succeeds. Without `fid`, permission through **any matching File row** can authorize those bytes. A restrictive draft attachment alone therefore cannot protect bytes also referenced by a more broadly accessible File row.
- `core/doctype/file/file.py:916–917` implements `is_downloadable()` by calling the module's `has_permission(self, "read")` directly. This path does not itself call the generic `frappe.has_permission` dispatcher; adding a generic File permission hook alone must not be assumed to secure downloads. Linked-document checks still invoke that document's permission method.
- `core/doctype/file/file.py:1022–1059` permits Administrator, the authenticated File owner, an explicit File share, or access through the linked document. The owner branch does not itself check current patient access. Consequently, author ownership alone does not satisfy the approved requirement for both author identity and current patient/record authorization, including revocation.
- `handler.py:258–274` exposes a guest-callable `download_file` function but still uses `find_file_by_url` before reading content. Guest-callable does not mean private bytes are publicly readable; effective Guest behavior must be tested per route.
- `utils/response.py:327–358` can hand delivery to `/protected/` using `X-Accel-Redirect`, or stream through Werkzeug. The accelerated branch sets `Cache-Control: private,max-age=3600,stale-while-revalidate=86400`. Frontend nginx internal-location enforcement and shared-workstation cache/revocation behavior have **not** been verified.

These are installed-source observations, not HTTP acceptance tests. The author-private design requires an explicitly verified enforcement mechanism across all byte-serving routes, File ownership/shares, shared byte references and current patient-access revocation. No solution or bypass is deployed by this assessment.

## Native-first follow-up under amended access policy

The approved feature policy now allows all authenticated clinicians to view all patients and saved drafts in this phase, with author-only draft editing. Earlier author-private/patient-assignment statements above describe the policy at the time of those probes; they are not the current acceptance criteria. Historical results remain valid only for their tested scope. The amended design and plan are authoritative for this feature.

Two additional read-only SSH inspections exited 0:

- Installed permission metadata: Patient grants read/write/create/delete/share to System Manager, Physician, Laboratory User and Nursing User. Patient Encounter grants those rights to Physician. Clinical Note grants them to System Manager. File grants read/write/create/delete to System Manager and All, with share also granted to System Manager. These rows are not effective-access tests; File-specific checks still apply. Do not copy broad Patient or File mutation permissions into a draft DocType.
- Role metadata contains Physician, Healthcare Administrator and Nursing User. No user assignments were queried. Which installed roles map to this application's clinician capability still requires reconciliation; a role name alone is not a signed authorization contract.
- Frontend `/etc/nginx/conf.d/frappe.conf:35–38` marks the `/protected/` location `internal` and uses `try_files`. Lines 71–77 set the site header and `X-Use-X-Accel-Redirect True` before proxying to the backend. This establishes the inspected configuration, not a runtime HTTP bypass test or a complete configuration audit.

Native draft role-read plus owner-write permissions remain the preferred starting point. The existing File owner and explicit-share branches must still be evaluated for role removal and annotation mutation bypasses. No new DocType, role, permission row, test user or file was created in this follow-up.

## Outstanding acceptance

An authorized native DocType/File probe is recorded in `2026-11-06-frappe-native-draft-probe.md`. Corrected explicit permission rows supported Physician-wide reads and author-only writes in server-side checks. Revoked File owners and explicitly shared non-clinicians could still pass the byte-permission function. Thus native draft permissions are a useful foundation, but do not close the direct-file authorization gate. No HTTP acceptance or permanent extension deployment was performed.

Effective clinician-read/author-write permissions, annotation-file linkage, role revocation, custom hooks, duplicate-photo workflow, concurrency and recovery remain unverified. No signed `Backend_Contracts_Installed_<date>.md` was identified. Further synthetic writes need a separately approved scope; backup destination and restore evidence are still required before real photo/draft operations or write activation.
