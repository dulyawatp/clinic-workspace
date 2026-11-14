# Photo publication recovery runbook (development only)

**Date:** 2026-11-08
**Scope:** the isolated, synthetic-only development slice of the durable photo
publication entry (`clinic_photos`). **Status:** default-off; no native acceptance and
no production readiness is claimed. This document describes read-only observation and
stop-mutation behaviour; it is not an installation, activation, retention, backup or
restore procedure.

## 1. What is available and what it can prove

Two read-only surfaces exist. Both require the server feature flag
`clinic_photos_publication_enabled` (exact boolean `True`). Only `status` is an HTTP
endpoint (POST-only, with the native session and explicit-CSRF contract); the operator
audit is a Python-internal read-only function with no added endpoint, gated by the same
flag, the operator allowlist and the operation's own site:

| Surface | Who | Returns |
|---|---|---|
| `clinic_photos.api.photo_save.status(operation_id)` (POST-only endpoint) | the originating currently enabled clinician, re-checked against current target authority | `pending`, `unknown`, or `saved` — never `saved` without full coherent evidence |
| `clinic_photos.publication.inspection.inspect_operation(operation_id)` (Python-internal, not an endpoint) | an enabled clinician in the same site who is listed in the operator allowlist | operation-bound audit: opaque ID, durable state, kind, payload digest, expected SHA-256, conservative audit outcome, consistency flag, byte-proof status |

Neither surface mutates state: no commit, rollback, state transition, File/content
creation, unlink or protected-key allocation happens on these paths, and the only
writer entry they reach is the side-effect-free committed-object readback
(`writer.verify_committed_object`).

### What `saved` now means

A published operation is reported `saved` only when all of the following hold during the
call (each check is bracketed around the byte proof; this is not an atomic cross-store
snapshot — see Limits): the durable outcome is bound to this exact operation and the
same-row derivation is re-loaded and equal after the byte proof; the persisted File row
and the immutable version/original linkage agree before and after; the current draft
head is a coherent committed version at or after this one (a historical version stays
itself after a later version); the caller's current clinician, native draft/photo/Patient
authority and protected-reference File read still hold after the proof; the fresh File
document carries no truthy cached content and its effective `get_content` chain completes
without a veto both before and after the proof; and the single side-effect-free
descriptor/hash/identity proof (`dev:inode`, mode, size, SHA-256, directory entry)
matches the object the publication created. Any mismatch or denial withholds `saved` and
reports `unknown`; nothing is repaired or re-derived.

Limits that remain: reads may share one database snapshot, so this is not a claim of
atomic cross-store authorization or of detecting a later concurrent revocation; and a
copy restored onto another host or volume cannot match the recorded creation identity,
so it reports `unknown` rather than a false `saved`.

## 2. Operator allowlist for recovery inspection

Exact site-configuration key: `clinic_photos_recovery_operators`.

* Default: **absent or empty** — inspection is denied.
* Shape: a list/tuple of exact user IDs. Any other shape (a bare string, a sequence
  containing a non-string) denies for everyone.
* Membership is exact; there is no prefix, wildcard or role expansion.
* The caller must also be a *currently* enabled clinician in the same site as the
  operation. `Guest`, `Administrator` and disabled users are denied even when listed;
  the trusted-Administrator operation read/report exception does not extend to
  clinical publication, protected bytes or this allowlist.
* Authority is re-read on every call. Removing the user from the allowlist, disabling
  the user, or losing clinician role ends inspection immediately.

Changing this value is a site-configuration change on the exact disposable
development site, performed only under the separate configuration authority; this
runbook does not authorize it and no command here is approved for execution.

## 3. Reading one operation without mutating anything

1. Keep the exact opaque operation ID returned to the clinician; never substitute a
   new request key or invent an ID.
2. Ask the originating clinician to call `status`. Treat every refusal
   (unknown ID, other user's operation, lost target authority, inconsistent row) as the
   same opaque answer: it deliberately discloses no state, patient, payload or path.
3. For an allowlisted operator, call `inspect_operation` and record the returned
   dictionary verbatim. It contains only the operation identity, durable state, kind,
   payload digest, expected SHA-256, conservative audit outcome, a consistency flag and
   the byte-proof flag — never annotation content, image payload, storage path, file
   name, version, patient/draft/original identifier, credential or other user identity.
   Coherent byte-verified publication evidence is labelled `published_verified`, a
   neutral audit fact rather than the ordinary clinical `saved` outcome; byte-proof
   failure reports `unknown` with `consistent` still describing the documents. An
   operator audit hashes the object internally without Patient or File read rights and
   grants none.
4. Record the time, the site, the operation ID and the response. Do not paste
   annotation JSON, image base64, session cookies, CSRF tokens or credentials into any
   ticket, log or chat.

Never use ad-hoc SQL, direct row edits, storage deletion or a permission bypass as a
recovery step. Manual state-changing reconciliation is not an approved routine and
needs separately authorized authority and proven evidence.

## 4. Interpreting the durable state

| Durable state | Read-only outcome | Meaning / allowed action |
|---|---|---|
| `SUBMITTED` | `pending` | Payload accepted only; no key, no bytes. A current-authority replay of the same tuple/digest resolves the same operation; a changed payload is a conflict and never replaces the stored request. |
| `PREPARED` | `unknown` | A protected key and expected byte evidence are durably reserved. This is **not** resumable here: never retry it, never reuse the key, never delete it, never rewrite the state. Escalate for separately authorized investigation. |
| `PUBLISHED` | `saved` (immutable version) or `unknown` | `saved` requires every proof above; `unknown` means at least one could not be established now. Either way, never treat `unknown` as a failed publication and never retry with a new request key. The operator audit reports the neutral `published_verified` label for the same coherent, byte-verified evidence, or `unknown`. |
| `ABORTED` | `unknown` | Nothing was published and no key/bytes exist. The durable record does not record *which* definite refusal it was, so no `rejected`/`conflict` outcome is invented. |
| `QUARANTINED` | `unknown` | Read-only holding state; no repair, purge or resumption is exposed. |

Cross-actor pending metadata and cross-patient duplicate matching are never disclosed:
status is actor-only, and recovery inspection returns no domain identifiers at all.

## 5. Stop-mutation procedure when something is uncertain

1. Stop producing new submissions: have the site owner set
   `clinic_photos_publication_enabled` to a non-`True` value on the exact development
   site. This closes submission, status and operator inspection without touching rows.
2. Do not delete protected bytes, do not unlink residue, do not clear reservations, do
   not edit operation rows, do not reuse a reserved key and do not reset a `PREPARED`
   or `PUBLISHED` row to an earlier state.
3. Preserve the current state exactly. Retain evidence already collected and hand the
   operation ID and observations to the owners of the relevant native gates. Inspection
   is unavailable while the flag is disabled; do not re-enable publication merely to
   collect diagnostics. Further evidence collection needs separate authorization.
4. A lost transport response or missing commit acknowledgement is **unknown**, not
   permission to retry with a fresh key.

## 6. Activation gates that remain open

The development slice must not be treated as active until all of the following are
closed by their own authority and evidence:

* **Native acceptance** for the submission, publication, recovery and inspection
  paths (Task 6/7/8/9 native gates are OPEN). Local focused suites are not native
  acceptance.
* **Schema and migration ordering.** The operation DocType declares the durable
  outcome columns in source only. The installed site does not have them yet, and every
  operation-row read in this slice depends on them: synchronize/migrate the exact
  development site **before** any code path that reads or writes an operation row is
  exercised there. A publication under the old schema fails integrity checks; it does
  not silently re-derive.
* **Payload retention and purge policy** for submitted content, and the production
  backup/tested-restore blocker. Neither is provided by this slice.
* **Development database profile**: `REPEATABLE-READ` with
  `innodb_snapshot_isolation=OFF` on the isolated development database, as established
  in the Task 2 native comparison. A different profile may abort the collision-recovery
  locking read; the correct response is to stop, not to retry or clear the transaction.
* **Operator allowlist** configured deliberately on the exact site, with a named
  responsible operator.

## 7. Synthetic development stack retention and disposal

* Retain the exact development stack by default. Do not destroy the only working
  development environment as part of a diagnostic or verification run.
* Disposal, when performed, applies to the **exact synthetic development stack only**,
  only when explicitly requested by the responsible owner, and after the evidence for
  that run has been retained.
* This runbook claims no production retention capability, no backup capability and no
  tested production restore. Those remain unresolved activation blockers.

## 8. Explicitly not authorized here

Installing or migrating a site, enabling the feature flag, editing the allowlist,
starting or restarting services, running scheduler or worker commands, copying code to
any remote host, invoking `bench` on a shared environment, deleting files, pruning
rows, and any SQL statement that changes state. Where such a step is mentioned above
it is an activation prerequisite for a separate, authorized procedure — not a runnable
instruction in this document.
