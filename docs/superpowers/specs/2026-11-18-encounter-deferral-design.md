# Encounter write deferral

## Intent

Defer encounter draft, completion, amendment and lab ordering until appointment lifecycle provenance and authorization are reviewed. A pinned native encounter draft save closes its appointment, whereas the completion contract requires the appointment to remain open or checked in. Note authorization and other cascade risks also remain unresolved.

## Approach

Reject new encounter mutations before fence dispatch and publish their capabilities as unavailable. Preserve fence acknowledgement and existing recovery mechanisms; do not delete data or alter unrelated write families. Hiding UI controls alone would not protect direct API callers.

For recovery, an exact request matching the authenticated actor's own `COMMITTED` receipt may return a fresh authorized encounter detail and reissue its acknowledgement token without dispatching or appending a fence event. The payload digest, kind and alias must match; uncertain operations remain locked. The returned result label identifies the historical operation, while encounter state reflects the current read.

This boundary applies to the native `clinic_ui` encounter endpoints, not to Frappe's separate resource API or the separately deployed proxy. Existing native DocPerm may still allow direct encounter writes; this deferral does not change permissions. Acknowledgement still requires the encounter gate to remain enabled. Keep it open for outstanding receipts; do not infer that closing the gate is safe.

Keep medication ordering deferred. The verifier must explicitly assert the deferred capabilities are not enabled, not simply omit them from its checks. Record affected SIT cases as blocked rather than passed.

This is local-only authorization. Deployment, permission changes and SIT writes require separate approval. No automatic retries, automatic unlocks, fabricated fence events or remote pushes.
