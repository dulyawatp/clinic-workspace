# Shared SIT payment fence replacement

Remove the Clinic file-backed payment fence dependency only for the explicitly
identified shared `marley-sit.local` deployment. Keep the one-payment limit.
Leave existing fence files, invoice and other writers, production behavior,
and the separate full-settlement release gate unchanged.

## Approach

Removing the fence and its quota outright would allow payments against
different invoices. Repairing the old fence would leave the dependency in
place. Use one pre-provisioned InnoDB row keyed by stable deployment identity
instead, with private server-owned deployment proof independent of fence
readiness. A hostname alone or a request flag cannot select the new path.

The payment transaction locks the deployment row before the invoice row and
both existing indexed ledger ranges. It preserves the locked permission and
balance reread and validates the accounting effects. The slot records the
operation and confirmed native Payment Entry in the same commit as posting.
Rollback consumes nothing. A lost response, cancel, restart, redeploy, or ACK
does not reopen a committed slot. The ACK confirms delivery only and does not
control admission. Retain the existing strict result, persistent browser stop
on uncertainty, and receipt permission checks.

Before cutover, inventory old payment history without writing to it. A known
committed payment makes the slot spent; ambiguous or unavailable history
blocks initialization as available until reconciled. Do not run both payment
paths concurrently. No shared-SIT initialization, posting, or enablement is
part of implementation acceptance; prove behavior on a disposable deployment
first. No new grants, budget increase, or other-site rollout.
