# Encounter reconciliation: evidence assessment

**Status: BLOCKED_UNVERIFIED.** This assessment is based on repository
inspection, not a live-site experiment. No operator unlock, retry, account
change or Marley write is authorized by it.

## Existing behavior and trust boundaries

- Draft creation claims a SQLite intent before a native POST. A partial unique
  index prevents another unresolved intent for the same user and patient;
  successful native creation and local outcome persistence are separate
  transactions (`proxy/src/routes/encounter-draft.ts`,
  `proxy/src/writes/encounter-draft-store.ts`).
- Review Details claims a SQLite intent per user and encounter before a native
  PUT. A confirmed response releases the intent; an ambiguous result retains
  it (`proxy/src/routes/encounter-review.ts`,
  `proxy/src/writes/encounter-review-store.ts`). Shared physicians are not
  mutually excluded by that per-user local lock.
- A native commit followed by a proxy crash before local persistence can leave
  the local outcome unresolved. Neither current document contents nor a stopped
  proxy proves that the upstream request is terminal.
- Existing native requests have no verified operation-bound receipt. The local
  hashes do not bind the complete native operation, and a new protocol cannot
  retroactively identify or fence already-dispatched legacy requests.
- The photo-publication coordinator has its own exclusion and journal
  mechanisms; these do not establish encounter fencing
  (`frappe-apps/clinic_photos/clinic_photos/publication/`).

## Candidate protocol — not implemented or proven

1. Persist an opaque operation identifier before dispatch. Bind its immutable
   record to protocol version, site and recovery epoch, operation kind,
   authenticated native actor, target, complete canonical payload digest and,
   where applicable, expected encounter version. Use a keyed digest for
   sensitive payloads; the actor must come from the native session.
2. On Marley, serialize execution and operator fencing on the same durable
   operation record. Mutation and a committed receipt must share one native
   transaction. A terminal no-commit fence must prevent a delayed request
   from executing; a lock wait or timeout is not itself a fence.
3. Resolve an absent-record race through the same unique-record serialization,
   not by interpreting absence as non-commit. Terminal operations must never
   execute their mutation again.
4. Verify receipt provenance and all bindings. Separately authenticate and
   authorize the operator for the site and revalidate authorization at
   resolution. Administrator status alone grants no implicit access.
5. Atomically compare-and-set the exact local intent and append a bounded audit
   decision. A local audit or persistence failure leaves the intent blocked.
   Native and local commits are separate boundaries; no distributed transaction
   is assumed. Review resolution records the historical outcome and must not
   overwrite a later shared edit.

This works only if **every execution** of the operation passes through the
native fence. An added operation table cannot fence legacy REST requests that
already bypass it. Signature or authenticated transport alone cannot establish
that a receipt and mutation committed atomically.

## Failure cases and proof obligations

| Case | Required proof or behavior |
| --- | --- |
| Delayed request after timeout | Execution and terminal fence serialize; a fenced request cannot mutate later. Verify hooks and asynchronous effects cannot escape the transaction. |
| Wrong receipt | Reject mismatched operation, site/epoch, actor, target, kind, payload or base version. |
| Native commit followed by proxy crash | Recover from an authoritative committed receipt without replaying the clinical operation; preserve later shared edits. |
| Competing operators or lost response | Local compare-and-set and audit are atomic and idempotent; resolution cannot be applied twice or without an audit record. |
| Missing or restored state | Missing local state or receipt is not proof of no commit. A recovery gate must address both local and native restores; an epoch restored with the same database is not an independent restore detector. |
| Current read or operator attestation | Neither establishes terminality or non-commit. Hold the intent when no verifiable fence or receipt exists. |

Actual deployed transaction isolation, unique-insert races, transactional table
configuration, hooks, implicit commits, background side effects, permission
behavior and backup/restore semantics remain unverified. Tests must cover these
on an isolated synthetic system before implementation can be called safe.
Existing uncertain intents remain locked unless evidence and fencing specific
to those exact legacy operations can independently be established.

## Draft-create response classification

After sending a native POST, `proxy/src/routes/encounter-draft.ts` currently
classifies 401, 403, 400, 409, 417, 422, 404, 405, 413 and 429 as definite
non-creation or session expiry and releases the pending intent. Redirects,
transport failures, 5xx and unexpected responses retain it as unknown.
Pre-POST authorization failures are different: no native create has yet been
sent.

The offline tests inject a fake upstream and demonstrate how the proxy
classifies particular responses; they cannot prove when an intermediary may
emit or rewrite a response. The repository documents an SSH loopback tunnel
and a Frappe site fronted by nginx, but not a complete verified deployed hop
list, buffering and retry policy. Therefore it does **not** establish that any
of the released post-dispatch statuses proves non-commit throughout the whole
request path.

A conservative candidate is to retain the lock on unverified post-dispatch
statuses, starting with 409/417, which the review route already treats as
ambiguous. A native-looking error envelope alone is not proof of origin,
terminality or non-commit. Any behavior change needs its own scoped design,
regression tests and review; it has not been made here. Review-save status
classification also merits separate assessment.

## Next steps

1. Specify and independently review the native transaction, fencing,
   authorization, audit and restore protocols before implementation.
2. Validate the protocol in an isolated synthetic environment, including
   delayed commits, duplicate requests, competing operators, crashes and
   restored databases. No live writes are implied.
3. Investigate the deployed request path with appropriate authorization before
   making claims about response provenance. Until then, do not infer non-commit
   from those response codes.
4. Seek separate approval before installing any native extension or changing
   Marley accounts or permissions.
