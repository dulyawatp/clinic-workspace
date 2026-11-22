# Native SIT smoke

Discovery only: `pnpm --filter @clinic/app exec playwright test --config playwright.sit.config.ts --list`

Before a future run, independently verify the deployed site identity is
`marley-sit.local`, its installed apps and native UI bundle, and that port
`127.0.0.1:18081` reaches that site. The environment flag is an operator
attestation, not proof of identity. Provide the provisioned synthetic normal
Physician account through `CLINIC_SIT_PHYSICIAN_USER` and
`CLINIC_SIT_PHYSICIAN_PASSWORD`; set `CLINIC_SIT_VERIFIED_SITE=marley-sit.local`
only after verification. Never print credentials or save authentication state.

Run with `pnpm --filter @clinic/app exec playwright test --config playwright.sit.config.ts`.
The smoke permits reads and the native login POST only. It does not verify photo
writes or installed-site permissions beyond the native physician bootstrap.

## Optional annotation write (not yet acceptance-tested)

`annotation-roundtrip.spec.ts` is separately armed with
`CLINIC_SIT_ANNOTATION_WRITE=fresh-synthetic-linked-draft`, plus
`CLINIC_SIT_APPOINTMENT` and `CLINIC_SIT_ENCOUNTER`. The operator must attest
these exact identifiers designate a fresh synthetic linked draft, with active
grant, both original photo slots present, and empty writable annotations
with no pending operation in either slot. Verify native `clinic_photos` installation
and effective staff permissions first. Missing opt-ins skip the test. The test
writes each slot at most once and checks each original request key at most
three times, with bounded backoff only for pending status.
If the outcome is pending or uncertain, reconcile that key privately through
native readback; never rerun the write against the same fixture.

The test exercises Before with mouse and After with Chromium CDP touch. It
compares both image hashes and Photo IDs, and verifies both saved drawings after
reload, including that Before remains unchanged after After's save. History is
not covered: an independently attested patient identifier and safe deterministic
History fixture were not supplied. Discovery does not prove runtime selectors,
permissions, persistence, touch behavior, or site identity.
