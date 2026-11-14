# Encounter Reconciliation Evidence Plan

**Goal:** Establish whether a supervised encounter-intent resolution protocol can satisfy the approved safety requirements before implementing an unlock.

**Architecture:** Review the current proxy and Frappe integration, then specify an operation-bound native receipt and fencing protocol. Evaluate it against explicit failure scenarios using isolated synthetic tests only; any unproven case blocks an unlock.

**Tech stack:** TypeScript/Fastify, SQLite, Frappe/Python, MariaDB.

**Spec:** `docs/superpowers/specs/2026-11-14-encounter-operator-reconciliation-design.md`.

## Global constraints

- Do not stage, commit, push, modify live records, or change accounts or permissions.
- No lock deletion, operator override, automatic retry, or unlock based on a current read, timeout, proxy stop, or human attestation.
- Keep credentials, identifiers, clinical content and evidence private.
- Existing uncertain operations have no retroactive receipt; remain blocked unless independently proven safe.
- Any native extension installation or permission change requires separate approval.

## Review focus

- Delayed upstream commits after timeout must not race with resolution.
- A receipt from another operation, payload, actor, site or target must not authorize resolution.
- Lost responses, crashes and concurrent operators must not permit duplicate execution or unaudited resolution.
- Missing, restored or replaced local state must not be treated as proof of a terminal outcome.
- Permission-filtered or current reads and operator assertions must not prove non-commit.

### Task 1: Specify and assess the native protocol

**Inputs:** `proxy/src/routes/encounter-draft.ts`, `proxy/src/routes/encounter-review.ts`, their write stores, and `frappe-apps/clinic_photos/clinic_photos/publication/`.

- [ ] Document the existing write and lock state transitions, including the distinction between native and proxy transactions.
- [ ] Specify operation identity, payload binding, authenticated receipts, transaction boundaries, terminal outcomes, fencing of late requests, operator authorization and audit integrity. Mark every unverified deployment assumption.
- [ ] Walk through the five review-focus failure classes, including crash after native commit and before local persistence. Identify any case not resolved by the proposed protocol.
- [ ] Document why existing uncertain operations cannot be retroactively reconciled using a new receipt alone.

### Task 2: Assess response-classification risk

**Inputs:** `proxy/src/routes/encounter-draft.ts`, its tests, and the deployed request-path design as documented in the repository.

- [ ] List the response classes currently treated as definite non-creation and the evidence needed to prove that an intermediary cannot emit each after commit.
- [ ] Identify which conclusions are supported by local tests and which require separately authorized environment-specific verification. Do not send live writes.
- [ ] Propose a conservative change and focused regression tests only if the evidence warrants it; no behavior change is authorized by this plan.

### Deliverable and gate

- [ ] Write an evidence assessment in `docs/assessments/2026-11-14-encounter-reconciliation-evidence.md`, citing source paths and distinguishing demonstrated facts from hypotheses.
- [ ] Review the assessment against the approved design and report unresolved proof obligations and exact next steps.
- [ ] Stop before implementing a native receipt, operator surface or unlock. Those require a further implementation plan and any applicable separate deployment or permission approval.
