# Clinic Platform Constitution

## Principles

### I. Clinical safety and verified contracts
The approved clinic specification and original requirements remain authoritative. Installed-site contracts, permissions and workflows must be verified before live integration or activation. Clinical, financial, inventory and photo writes default to disabled; each operation must meet its own documented verification and recovery gates. Fixture behavior is not production authorization or clinical acceptance.

### II. Privacy and server-side enforcement
Patient data, photos and credentials are private by default. Enforce permissions and record scope on the server, never solely in the UI. Do not place sensitive data in logs, analytics or URLs. Existing interim deviations and unresolved conflicts are tracked in `docs/assessments/2026-11-06-spec-kit-revalidation.md` and must not silently become production policy.

### III. Accurate and accessible presentation
Never invent clinical status, identity, units, consent, completeness or source data. Distinguish unknown, unsupported, denied and empty. Support Thai and English, keyboard operation and responsive layouts at 390, 834 and 1440 pixels; record actual accessibility evidence and limitations.

### IV. Evidence before completion
Use focused regression tests, integration checks and relevant browser verification; report exact commands, results and limitations. Separate fixture, local-proxy, installed-site and clinical-UAT evidence. Revalidate evidence after relevant edits. No unverified claims of live readiness.

### V. Preserve scope and traceability
Keep established specs and implementation plans, link requirements to tasks and evidence, and record unresolved decisions. Reuse existing read transport, fixtures, session and capability mechanisms; make minimal changes and avoid duplicate frameworks. Do not commit or change deployment state without user authorization.

## Delivery constraints

The frontend is React/TypeScript and the proxy uses fixture-only reads until backend verification. Browser access to the evaluation HTTP backend does not imply production security. Photo originals must remain immutable; preview annotations are local only until independently authorized and verified. Never claim production authorization from synthetic photo delivery.

## Governance

This document records existing approved constraints; it does not supersede the clinic specification or grant an exception. Conflicts between sources require explicit resolution at the authoritative source. Changes to principles require documented approval. Spec Kit feature artifacts link to, rather than replace, the existing approved documents.

**Version**: 1.0.0 | **Recorded**: 2026-11-06 | **Status**: project constraints recorded; no new clinical policy approved
