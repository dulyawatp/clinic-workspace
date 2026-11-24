# React-only Doctor hours

## Scope

Doctors can view their own recurring working hours in React. They cannot edit hours. Owners continue to configure private and shared schedules using the existing React flow. Do not change appointment bookings, native Desk/API permissions, or document sharing.

## Approach

Keep the existing native Doctor-hours endpoint and React card. Instead of giving doctors general read access to `Practitioner Schedule`, add an explicitly enabled, site-scoped server read for this one purpose. The native session, fresh Doctor authority, selected Doctor workspace, and current session epoch must all match. Resolve exactly one active practitioner from the signed-in user's identity and follow only that practitioner's current native attachments. Neither a practitioner nor a schedule ID is accepted from the browser.

Return only the weekday and start/end times of enabled recurring slots. Keep the optional unit label subject to existing ordinary read permissions. Do not expose native schedule documents, other doctors, or appointments. Use one policy for private and shared schedules; removing an attachment removes those hours from the next read. Validate the exact native schema and respect configured field masking. Keep the existing permission-bound read as the default when the new feature is off. Fail closed on ambiguous identity, unreadable or unsupported required data, or authority drift; distinguish **Empty** from **Unavailable**.

This narrowly scoped read exposes the doctor's hours through React even when the doctor lacks generic `Practitioner Schedule` access. It does not add a role, document share, permission rule, or write path. The alternative of granting native schedule document access is unnecessary for this scope.

## Acceptance

- On approved isolated SIT, verify with fictional doctors in React: private hours, a shared schedule visible to each attached doctor, no unrelated hours, and hours disappearing after detachment. Verify the native schedule list and detail permissions have not changed.
- Cover missing and duplicate practitioner relationships, stale sessions, and revoked Doctor authority. Show no sample hours on empty or unavailable responses. Do not change the Owner flow, schedule records, or appointments as part of the new read feature.
- No production rollout or live configuration change without applicable separate approval.
