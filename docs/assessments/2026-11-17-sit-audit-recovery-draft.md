# Local-native SIT audit recovery — draft for decision

**Status: approved for documentation and review.** This document does not authorize evidence
export, new live snapshots, anchoring, fence changes, deployment, gate changes,
business writes, or resumption of SIT. The existing stop order remains in force.
It uses previously recorded evidence; it is not a new examination of the live
system. No recovery or verifier result is claimed here.

## What must be disclosed

The fence history cannot be represented as an uninterrupted append-only record.
The private evidence records three operations that removed tail events and
rewound sequence numbers: 10→8, 11→8, and 34→32. The second removed an
`UNKNOWN` terminal; the third removed a claim and its barrier. Sequence numbers
were subsequently reused. The last recorded containment snapshot reports 96
events and zero unresolved events, but contiguity and a zero unresolved count
do not prove the removed events never existed or resolve their native outcomes.
Those observations are historical, not a statement of current live state.
Recorded checks found no relevant business rows for the affected operations;
those checks are evidence to evaluate, not a substitute for assessing all
available transaction and outcome evidence.

The third operation preceded the stop order. The Task 11 report explicitly
discloses that its purported operator reconciliation path was not implemented:
`.superpowers/sdd/2026-11-16-full-write-contracts/task-11-report.md`,
section “Task 11B resume 3”. That directory is ignored by Git, so it is not a
substitute for tracked disclosure. The earlier evidence is under the private
`~/.local/state/marley-sit/evidence/fence-reconcile*` directories, with
pre-operation backups under `~/.local/state/marley-sit/backups/`. These may
contain sensitive operational data; access and retention need custodian review.
The third intervention dropped and recreated the append-only delete trigger,
deleted the two rows and rewound the sequence. A pre-state backup and scratch
dry runs were recorded; the report also records unchanged trigger text and
successful integrity checks. The backup and execution log are the only
recorded copies of the removed 33/34 rows; the operator scripts are not tracked
as a supported repository procedure. A redeploy and 16 UI operations followed
the intervention before the stop order. The stop order itself is recorded in
the ignored Task 11 report.

The implementation documents the absence of a restore/reconciliation protocol
and says that a missing fence event is not proof that a native operation did
not commit (`frappe-apps/clinic_ui/clinic_ui/core/write_fence.py`, restore
threat model). The existing plan's reference to approved operator
reconciliation must not be read as evidence that a supported procedure exists.
The local anchor has not been demonstrated to be an independently maintained,
advanced historical witness. A passing verifier cannot repair this history.

## Options for decision

1. **Preserve and stop (recommended immediate posture).** Leave the site,
   fence, gates, backups and private evidence untouched. Review this disclosure
   and determine ownership, access and retention before any further action.
   This avoids further risk but does not complete SIT.
2. **Prospective independent evidence record.** Subject to separate approval,
   an independent custodian could verify existing evidence, document its
   provenance and limitations, and witness a signed, tamper-evident baseline in
   storage with appropriate independent retention. Later records could bind to
   that baseline. This would protect evidence prospectively; it would neither
   prove the historical record complete nor retroactively repair it. The
   custodian, signing method, storage, contents and approval boundaries remain
   undecided. Hashes in old manifests must be independently checked before they
   are relied upon. Any baseline must be separate from the existing fence and
   anchor; this proposal does not authorize modifying either.
3. **Supported reconciliation and new trust boundary.** Separately design,
   approve, implement and test a fail-closed protocol before any operational
   recovery. A candidate approach would preserve all old histories, resolve
   each uncertain outcome against business and transaction evidence, and
   establish an independently anchored new epoch or deployment. The protocol
   must define unresolved and contradictory evidence, rollback, permissions,
   budget accounting, operator approval, and failure handling. A new boundary
   cannot make the old history whole.
4. **Resume with a disclosed gap.** This is a product and risk acceptance
   decision, not a repair. It would require explicit separate authorization,
   an agreed trust premise, and a reviewed, technically safe operating path.
   No such path or authorization is established by this draft.

Do not overwrite the current fence with an old backup, fabricate committed
events, erase uncertainty, replay a removed operation key, or present
reconstructed rows as original history.
Restoring a backup over the current state could discard subsequent receipts and
must not be treated as an undo.

## Proposed gates before SIT continuation

1. Approve the disclosure and appoint an evidence custodian. Decide separately
   whether and how to preserve, export, inspect, or independently anchor
   evidence. Reconcile the three documented interventions and their outcomes,
   including the earlier `UNKNOWN`, without assuming absence means no commit.
2. Decide whether SIT may resume at all, on what trust basis, and whether a
   supported recovery protocol and new epoch are prerequisites. Review and
   approve that protocol independently before implementing or operating it.
3. Reconcile the authoritative operation budgets and actual consumption against
   existing evidence. The recorded SIT A state is 15/15 admin-master and 3/4
   admin-user; do not infer that any remaining slot is authorized for use.
   The plan records an original 12-master budget, amended to 14 and then 15;
   reconcile those approvals and actual consumption against the evidence.
   The current ledger alone cannot establish a complete historical accounting
   after deletion and sequence reuse.
4. If resumption is separately approved, define the exact actor, operations,
   limits, gates, independent checks and stop conditions for each SIT window.
   SIT A stopped after 18 of 19 planned operations: `sit.adm2` was not created,
   and the full verifier was not rerun after the window. SIT B, C and D remain
   unexecuted in the recorded evidence; run them sequentially only under their
   approved review gates.
5. Agree on verifier expectations for each checkpoint. The verifier's default
   event count is 24; overriding it to match a witnessed count is a separately
   approved operational choice, not evidence of audit continuity. Investigate
   any discrepancy rather than adjusting the expected count to force green.
6. After authorized testing, record results, limitations, evidence references
   and access restrictions in §17 of
   `docs/assessments/2026-11-14-local-native-sit-report.md`. Preserve §16 as
   its historical observation; distinguish it from later results. Complete
   independent review and local integration only after the relevant approvals.

## Decisions requested

- Accept or correct this factual disclosure and identify the accountable owner.
- Choose whether to remain stopped or commission a separately scoped evidence
  preservation/anchoring proposal, including custodian and retention controls.
- Decide whether to commission the supported reconciliation protocol and which
  trust conditions would permit any later SIT resumption.

Approval of this draft alone authorizes none of those subsequent actions.
