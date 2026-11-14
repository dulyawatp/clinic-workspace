# Encounter operator reconciliation: proposed design

## Purpose and scope

Provide a supervised path for resolving uncertain synthetic encounter draft
creation and Review Details saves without retrying an operation that may still
commit. This is a design, not an implemented or approved unlock mechanism.
Preserve native per-user authorization, shared physician editing, manual saves,
and existing pending/unknown locks.

## Approaches

1. **Operator attestation and a current Marley read:** simple, but unsafe. A
   request may still commit, and a current read cannot establish its outcome.
   Reject this as a basis for unlocking.
2. **Supervised resolution based on durable upstream evidence:** the selected
   direction for exploration. An operator can resolve a specific intent only
   when an independently verified, operation-bound terminal receipt or fence
   proves the relevant outcome. Otherwise the lock remains in place.
3. **Permanent lock without an operator workflow:** safe by default but leaves
   clinicians without a structured way to assess uncertain operations. Retain
   this behavior until approach 2 is actually proven.

## Current limitations

The proxy records local write intents but has no proof that an uncertain native
request has terminated. It cannot distinguish a crashed request from one still
being processed. Existing encounter requests have no verified native operation
receipt or fencing protocol. Proxy shutdown, elapsed time, session expiry,
current document contents, an error response, and operator confidence are not
independently sufficient proof.

The local database and its user/allow-list mappings are also necessary to find
and interpret existing intents. Their loss or replacement can orphan local
recovery state. Existing photo-publication inspection is a separate, read-only
mechanism and does not authorize encounter recovery.

## Proposed operator workflow

1. A separately authorized operator inspects the specific intent and available
   evidence through a default-deny, privacy-preserving surface. Operator access
   must be explicit, independently authenticated, scoped to the correct site,
   and revalidated at the point of resolution. Administrator status alone is
   not sufficient.
2. The system validates evidence bound to the exact operation, site, actor,
   target and payload or version, without exposing clinical text in ordinary
   audit output. A receipt must have verifiable provenance and durable
   transaction semantics; a merely matching document is not a receipt.
3. If verified evidence proves a committed operation **and rules out any
   remaining execution of that same operation**, reconcile the local outcome
   atomically and preserve an append-only decision record. Draft recovery must
   link the proven encounter; Review Details must preserve subsequent shared
   edits rather than overwrite them.
4. If verified evidence proves non-commit **and fences any later execution**,
   record the resolution atomically before allowing a new attempt. Neither
   absence of a receipt nor a current read is sufficient by itself.
5. If either condition cannot be established, return an unresolved result and
   retain the lock. Do not retry, delete or reset the intent.

An evidence or audit storage failure must leave the intent locked. Resolution
must be idempotent and safe across concurrent operators, proxy processes,
restarts and lost responses. Audit records should contain bounded opaque
identifiers, operator identity, decision, evidence reference and timestamps;
credentials, clinical content and patient identifiers stay private.

## Candidate implementation and gates

A future native extension could associate an opaque operation identifier with a
durable receipt committed atomically with the encounter mutation, together with
a proven protocol preventing late execution after a terminal decision. Receipt
presence alone is not enough unless the protocol also prevents duplicate or
delayed execution. This is a candidate, not a claim about installed Marley.

Existing requests were not issued with such an identifier; a new protocol
cannot retroactively produce trustworthy receipts for them. Existing uncertain
intents therefore remain locked unless independent evidence and fencing can be
established for that exact operation. No operator override is authorized merely
because an intent predates the protocol.

Before implementation or deployment: specify the native transaction and
fencing protocol, operator authorization, trust and retention model, database
recovery behavior and audit atomicity; test crash windows, delayed upstream
commits, multiple processes and competing operators on an isolated synthetic
environment. Installation or changes to Marley accounts and permissions
require appropriate separate approval. Never alter real patient data.

## Related risk requiring separate investigation

The draft-create route currently classifies several native response codes as
definite failures and releases its local intent. Whether intermediaries could
produce these responses after a commit has not been established. Investigate
that classification separately; do not infer non-commit from a status code
without evidence for the whole request path.
