# SIT payment slot: operator design

This is a **future, separately approved operator procedure**, not an app hook
or an instruction to initialize shared SIT now. Native acceptance on a
disposable deployment precedes any shared-SIT enablement decision. Keep the
legacy fence, its files, records, quota and other writers unchanged.

## Isolation and proof

Only `marley-sit.local` plus the separate private marker enables this identity.
Use the existing `clinic_ui.core.deployment_marker.write_marker` operator API
with private directory `<site>/private/clinic_ui_sit_payment_proof`, site
`marley-sit.local`, and deployment ID `clinic-shared-sit-payment-v1`. It creates
the marker exclusively, mode 0600, under its 0700 marker directory, binding the
canonical proof-directory path. Create the proof directory privately, owned by
the Frappe service user; protect all parent directories from untrusted writes
and symlinks. Never provision this marker by application startup, request,
configuration copy, or an automatic retry. Do not copy it to another deployment.
Marker access assumes the site private directory and service account are trusted;
an operator with write access can forge it. The marker is independent of old
fence readiness. Its identity is fixed, not a secret or authentication token.

## Reconciliation before initialization

Stop and prevent concurrent old and new payment writers throughout inventory
and cutover. Preserve old fence and database evidence read-only. An authorized
operator must inventory all legacy payment operations, their dispatch/commit
states, native Payment Entries and accounting evidence, including uncertain
responses and outstanding operations. Record evidence and review disposition.
Unavailable fence/history, incomplete inventory, in-flight operations, unknown
commit outcomes, conflicting evidence, or any unresolved ambiguity **block
initialization as available**. Restore evidence and reconcile manually first;
never infer no payment from a missing or broken fence.

`initial_state("confirmed_empty")` means complete reviewed evidence proves no
legacy payment committed; only then may the row start `available`.
`initial_state("confirmed_payment")` means at least one payment committed and
the row must start `spent`, preserving the existing one-payment budget. Record
confirmed actor, operation key and native Payment Entry receipt when known;
unknown historical metadata may remain NULL, but must never reopen the slot.
All other outcomes are rejected. This function is a classification guard, not
an inventory tool: operators must supply truthful, reviewed evidence.

## Schema and one-time row

After separate approval, with writers stopped, create on the **site database**
using a separately invoked privileged operator session, never during a request:

```sql
CREATE TABLE `tabClinic UI SIT Payment Slot` (
  deployment_id VARCHAR(128) NOT NULL,
  state VARCHAR(16) NOT NULL,
  actor VARCHAR(255) NULL,
  operation_key VARCHAR(255) NULL,
  receipt VARCHAR(255) NULL,
  PRIMARY KEY (deployment_id)
) ENGINE=InnoDB;
```

Verify engine, primary key, database, identity and reconciliation evidence.
Insert exactly one row with the approved fixed deployment ID and the reviewed
state, plus any known legacy metadata. Do not use `INSERT IGNORE`, upserts,
automatic initialization, or replace an existing row. If schema or row already
exists, stop and investigate rather than resetting or modifying its state.
Protect database writes with existing site/operator permissions; no new grants.
Provision the private marker only for the approved deployment after review.

`available` admits one transaction; `spent` permanently blocks further payment.
Missing row, unexpected state, missing proof, wrong identity and SQL failures
fail closed. Callers must lock slot first, before invoice and ledger locks, on
the same Frappe transaction/connection, then consume after validating the
native payment, before the single commit. Helpers never commit. Rollback rolls
back both payment and slot; ACK, cancellation, redeploy, or lost response never
reset a committed slot. Investigate discrepancies manually with writers stopped;
never reopen or recreate a spent row to recover from uncertainty.
