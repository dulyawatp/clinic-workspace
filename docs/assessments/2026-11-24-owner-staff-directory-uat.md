# Owner staff directory user acceptance

Playwright MCP exercised the read-only Owner directory in the isolated synthetic `marley-sit-owner-uat` stack on loopback port 18087. The feature snapshot was based on `fa8f11a` and included the earlier workspace-switching and practitioner-booking changes. No production site or data was accessed.

| Actor | Browser result |
|---|---|
| Named Owner with System Manager | Opened **Staff & roles** and saw four existing Frappe accounts without enrollment. The Reception + Cashier account showed both roles. Owner/self and privileged rows showed their restrictions. No edit controls appeared. |
| Manager without System Manager | No Staff & roles navigation entry. Opening the directory directly showed permission denial and no rows. |
| Reception | No Staff & roles navigation entry. Opening the directory directly showed permission denial and no rows. |

The built-in Administrator did not appear in the tested native list. Only one page was available, so next/previous pagination was not exercised. Account writes, invitations, and doctor onboarding were not exposed or tested.

## Follow-up: role editing

On the same isolated site, with the updated role-edit build, the named Owner added an approved Cashier role to an existing fictional Reception account. React showed the confirmed native role set, including the untouched baseline role, and then listed both Cashier and Receptionist. The staff member signed in separately, saw both workspace choices, and opened Cashier. The Owner then removed Cashier; the save result retained both Reception and the unrelated baseline role. The Owner’s own row had no edit button.

Neither save triggered an additional native role change in this fixture; both receipts reported none. No account creation, activation, invitation, or doctor onboarding was exercised. No production site was accessed.

## Follow-up: native practitioner permissions

On the same synthetic SIT site, the private deployment command returned `provisioned` and then `unchanged`. It added the two missing roles to the named Owner only (`System Manager` was already present). Existing practitioner grants remained intact; the dedicated role added level-0 read/write only.

After first-run setup, the named Owner opened a fictional practitioner in Desk, changed its phone number, saved, and confirmed persistence after reload. The Owner restored the original number and confirmed it after reload. A Manager without the deployment grants could not open the route: Desk denied access at the Page level, before the practitioner form appeared.

This verifies the deployment permission and Desk edit workflow, not React practitioner editing, schedule setup, or doctor onboarding. No production site was accessed.

## Follow-up: React draft working hours

On the isolated synthetic site, the named Owner selected an existing disabled, unlinked practitioner and a fictional appointment service unit. The live weekly preview showed two Monday bars (09:00–12:00 and 13:00–17:00) with a visible gap. Moving the afternoon start to 11:00 removed the conflicting bars and produced an overlap error on save. Restoring 13:00 brought back both bars and allowed the draft to save.

Playwright opened the practitioner and schedule in Desk. The practitioner remained disabled and unlinked, with exactly one attachment for the selected unit. The attached schedule was disabled and had exactly the two intended Monday rows. After reload, the practitioner no longer appeared as eligible for a new draft. A Manager without System Manager could not open the React page and saw no working-hours form.

No account was created, linked, or activated; invitations and booking using these hours were not tested. No production site was accessed.

## Follow-up: React Add staff

On isolated SIT, the named Owner created fictional June Park through **Add staff**, selecting approved Reception and Cashier roles. The receipt reported Enabled and both persisted roles; the account was still visible in the native directory after reload.

The Owner submitted a different, existing staff email in uppercase. React reported a duplicate, the directory still contained one row for that email, and an already signed-in browser session for that staff member remained in Reception after reload. A Manager without System Manager received a route denial and saw no create form. This tested ordinary duplicate handling, not concurrent creation.

Password setup and sign-in as the new account were not tested: this stack has no enabled outgoing mail account or synthetic capture service. No reset or invitation was sent, and no production site was accessed.

## Follow-up: Full React Add Doctor

Playwright MCP on isolated Owner SIT (`127.0.0.1:18087`) verified the full path:

1. The named Owner created fictional **Rina Vale UAT** in Cardiology. Before Finish, the login was absent from the staff directory. The Owner saved Monday **09:00–12:00** and **13:00–17:00**, reloaded, selected the saved draft, and reviewed the same two blocks. **Finish setup** confirmed an enabled doctor login linked to the practitioner and an enabled schedule for the selected unit.
2. After reload, the Owner found the doctor in Staff & roles, reopened the invitation panel, and queued one invitation. The private Mailpit sink captured exactly one message for the fictional recipient. The doctor used the local link to set a password in Frappe, then signed in from a fresh browser context and opened the React Doctor workspace.
3. The synthetic Reception user selected that doctor and the fictional appointment type in React. Native availability showed both Monday work blocks, with the lunch break excluded. Reception chose the morning block, booked **2026-11-30 at 09:00**, and reopened the saved appointment after reload: **HLC-APP-2026-00001**, **Scheduled**.

Read-only native checks corroborated the active practitioner link, enabled schedule, one native practitioner User Permission, matching disabled draft schedule, one appointment for the intended practitioner and unit, and one sent invitation in the test queue. The capture service had no published ports or relay. No production site was accessed.

The Doctor home still labels its working-hours panel as sample data; the booking availability came from native Marley. This run did not test a failed invitation retry, concurrent Finish requests, or behavior on other sites.

## Follow-up build: shared schedules, Doctor hours, and staff first sign-in

The reviewed follow-up build was deployed only to isolated Owner SIT on `127.0.0.1:18087`, after a fresh integrity-checked backup. The installed hook profile was reviewed before enabling schedule reuse on that site. No native permissions changed.

| Check | React Playwright MCP evidence | Native corroboration |
| --- | --- | --- |
| Ordinary staff first sign-in | A fictional dual-role account set its first password via Frappe's capture-only reset link. A fresh React login offered exactly Receptionist and Cashier. The user entered Reception and switched to Cashier under **Account → Switch workspace**. | One captured reset, one password hash, and the two original native roles. |
| Concurrent Finish | Two independent Owner browser contexts reviewed the same fictional private-hours draft and submitted once each. Both requests started before either response; one returned `200` with the receipt and the other returned `409` with a refusal. | One enabled Physician, one active practitioner link to one published schedule, and one native User Permission. No invitation was sent by Finish. |
| Reuse an enabled schedule | With reuse off, the option was absent. Once the isolated site qualified, a second fictional doctor started with blank schedule and unit selectors. The Owner explicitly selected both, reviewed the native `10:00:00–12:00:00` range and sharing warning, then finished with a `200` receipt. | One new practitioner attachment and User Permission; the existing source, its version, enabled state, slots, and original attachment were unchanged. No schedule was cloned. |
| Doctor hours under stock permissions | A freshly signed-in Physician opened the React Doctor dashboard. The native request returned `200` with **Unavailable**; zero rows rendered and the separate Sample banner remained. | The user could not read `Practitioner Schedule` under existing Marley permissions. Positive display was not verified on SIT; no read grant was added. |

Staff enable and disable remain Desk-only. The pinned Frappe save path can normalize other User fields and can run hooks after the proposed guard; React status writes were not enabled.

Local verification: 2,402 Python unit tests passed; app typecheck and diff checks passed. React Vitest: 1,921 passed, with the same two failures already reproduced on the pre–Add Doctor baseline (Cashier auth-epoch assertion and operations copy snapshot).

### Failed invitation and exact retry

With separate approval, the Owner queued one invitation for the fictional reuse doctor in React. Only the isolated site's idle scheduler and workers were paused. A single-process, socketless SMTP rejection exercised native Frappe's retry counter and terminal `Error`; Mailpit captured nothing before Retry. No SMTP settings or unrelated queue rows were changed.

After normal processing resumed, the Owner reopened Staff & roles, saw **Failed**, and clicked **Retry** once. React sent the original `previousQueueId` and received the same queue ID. The scheduler moved that row and its sole recipient to `Sent`; Mailpit captured exactly one matching Message-ID. The reset token fingerprint and generation timestamp were unchanged. Reopening the React panel showed **Sent**, hid Retry, and sent no invitation POST. `Sent` here means the private sink accepted the message, not that an external recipient received it. No external mail or production changes.

### React-only view of recurring working hours

After an integrity-checked backup, the scoped read was deployed and enabled only on isolated Owner SIT (`127.0.0.1:18087`). Fresh Playwright MCP sign-ins opened `/clinic/doctor` as two fictional Physicians. Both hours reads returned HTTP `200` and rendered `ready`: the private schedule showed only Monday 09:00–12:00 and 13:00–17:00; the shared schedule showed only Monday 10:00–12:00. No editing or generic schedule access was added. Separately, ordinary Frappe list and own/foreign schedule document reads were denied under the fictional Doctor actors, and the native role-permission fingerprint was unchanged.

After a separately approved password reset for the second fictional Doctor attached to the shared schedule, a fresh Playwright MCP sign-in also showed exactly Monday 10:00–12:00 (`200`, `ready`). No email was queued; roles, permissions, and attachment were unchanged. No native detach/restore was attempted: the pinned parent save runs hooks, and safe restoration of the original child row ID was not established. Local tests cover two independently attached doctors and revocation on detachment; that does not replace live acceptance of detachment. Local verification: 2,429 Python tests and app typecheck passed. React Vitest had 1,921 passes and the same two baseline failures noted above.
