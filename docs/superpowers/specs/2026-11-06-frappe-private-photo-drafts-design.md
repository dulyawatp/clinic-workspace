# Frappe private photo and draft storage

**Status:** User-approved storage direction; installed-site contracts, permissions and activation remain unverified.  
**Date:** 2026-11-06

## Intent and scope

Clinicians must be able to manually save an annotation draft permanently and reopen their own draft after logging in again or from another authorized device. Use Frappe private files rather than the previously considered SeaweedFS deployment. The original clinic specification and backend verification gates continue to apply; this document adds the user's storage decisions, not proof of an installed implementation.

## Approved design

**Approved phase-specific amendment:** All authenticated clinicians may view all patients and their saved annotation drafts in this phase; there is no patient-assignment restriction. Only a draft's author may edit it. This supersedes the earlier author-only visibility requirement for this feature, not unrelated clinical permissions. Anonymous and non-clinician access must be denied. The exact installed role mapping remains to be verified. Prefer native Frappe DocTypes, role permissions, ownership rules and private File attachment/delivery; add only the smallest verified extension needed. No Frappe core patches or separate storage service are planned.

- Keep the original image immutable in a Frappe private file. Store the versioned annotation JSON in a **separate Frappe private file**, linked to a draft record that also references the photo and the original's verified hash and dimensions. The exact DocTypes, fields, attachment method and versioning behavior are subject to installed-site verification.
- Draft save is **manual**. All authenticated clinicians may reopen saved drafts, including from another authorized device; only the author may edit. Prefer native role-based read permissions and owner-restricted write permissions on the draft DocType. Verify equivalent protection for File metadata, attachment changes, deletion and byte replacement, not just the draft form. Attach annotation files to the draft record rather than using a Patient attachment as the draft access model. Infrastructure administrators remain a privileged trust boundary, not users this design can exclude from stored data.
- For an exact duplicate image within the same patient, warn and offer reuse/linkage of the existing original to another encounter. Keep annotation drafts separate by draft/author identity. Do not automatically merge clinical records, disclose cross-patient matches, or rely on filenames. Verify exact-byte comparison, permitted metadata and concurrent duplicate submissions before implementing this behavior.
- Backup both the Frappe database and private files to a separate device; the precise destination, encryption/key management, schedule, retention and tested restore procedure are deployment gates.
- The browser never receives master credentials or direct privileged storage credentials. Requests use the clinician's authenticated, authorized session through the proxy, with no public static photo links.

## Safety and recovery gates

Before enabling real reads or writes, record the installed Frappe/ERPNext versions and verify private-file upload/download behavior, storage backend, access hooks, owner and linked-document permissions, same-patient exact-duplicate lookup, supported attachment schema, file/record lifecycle and role/ownership tests. Prove clinician read access, author-only editing, denial for other clinicians' mutations, and denial of anonymous/non-clinician reads through every download path. Test role removal, File ownership and sharing, shared byte references, protected nginx delivery and browser cache behavior. Native File ownership and direct download behavior must not preserve non-clinician access after role removal. Prefer native controls, but do not claim they satisfy this gate before installed-site tests. Do not assume behavior observed in a different Frappe version.

Before enabling manual save, satisfy the existing per-operation default-deny, durable idempotency, reconciliation, crash/restart, conflict and audit requirements. Define how the JSON file and draft metadata remain consistent across partial failures, retries and concurrent saves. A failed or unknown save must never appear successful; keep recoverable local draft state until confirmed. Original files must not be overwritten. No versioning, record visibility or consent policy is assumed merely from use of Frappe files.

## Alternatives considered

SeaweedFS on the Mac mini was considered for private objects but not selected: it introduces a separate service and consistency/backup boundary. Direct JSON in Frappe document fields was considered; the user selected a separate private file for the annotation JSON. Earlier author-private visibility was replaced by clinician-wide viewing with author-only editing. A native-first Frappe approach is preferred over a custom delivery subsystem; a proxy-only access check is insufficient if direct backend routes bypass it.

## Non-goals and interim behavior

The current fixture-only photo preview remains separate. It does not connect to real files, activate uploads, save annotation drafts or establish production authorization. This design approves clinician-wide saved-draft viewing only; it does not approve cross-user editing, public links, real-data migration or consent-policy changes. It does not override other unresolved privacy and write-plan findings in `docs/assessments/2026-11-06-spec-kit-revalidation.md`.
