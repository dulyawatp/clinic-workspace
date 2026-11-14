# Live browse-only acceptance (`e2e-live`)

Playwright tests that exercise the **deployed** `clinic-photo-accept.local` stack
through an existing SSH loopback tunnel. They are separate from `app/e2e`
(fixture/MSW suite) and never share its server, fixtures or storage state.

## What must already exist

1. Deployed app + live proxy answering on the remote loopback port, reachable at
   the fixed origin **`http://127.0.0.1:8083`**.
2. An SSH tunnel that maps that port to the same local port, for example:

   ```sh
   ssh -N -i <private-key> \
     -o ExitOnForwardFailure=yes \
     -L 127.0.0.1:8083:127.0.0.1:8083 <user>@<approved-host>
   ```

   Both ends stay on loopback; no site port is exposed. The tunnel is _not_
   started by Playwright: `playwright.live.config.ts` has no `webServer` entry,
   so a missing tunnel fails fast instead of silently starting a local stack.
3. A private credential file, mode `600` (or stricter), at an **absolute** path,
   with the existing schema:

   ```json
   { "site": "clinic-photo-accept.local", "user": "<synthetic clinician>", "password": "<secret>" }
   ```

   The `site` value must be exactly `clinic-photo-accept.local`. The file is read
   only by the test process; values are never printed, logged, attached to a
   report, or asserted on.

## Run

```sh
cd app
CLINIC_LIVE_CREDENTIAL_FILE=/absolute/private/path/creds.json \
  pnpm exec playwright test --config=playwright.live.config.ts
```

Optional non-clinician denial coverage (same schema, non-clinician account):

```sh
CLINIC_LIVE_CREDENTIAL_FILE=/absolute/private/path/creds.json \
CLINIC_LIVE_SECONDARY_CREDENTIAL_FILE=/absolute/private/path/secondary.json \
  pnpm exec playwright test --config=playwright.live.config.ts
```

Without the primary variable the browse test is **skipped** with an explicit
reason. Without the secondary variable the non-clinician test is **skipped** with
an explicit reason — a missing credential is never reported as a passing denial
test.

### Rate limit

The proxy allows **10 CSRF handshakes per client per 10 minutes**
(`CSRF_ISSUANCE_LIMIT_PER_CLIENT`), and each login consumes one. A full run uses
two (clinician + non-clinician), so do not loop the suite or run ad-hoc login
probes alongside it; a throttled login fails as
`sign-in was rejected: ... frequent ...` and is an environment condition, not a
product defect. Wait for the window to reset before re-running.

## Coverage

| Test                                   | Requires             | Asserts                                                                                                                                                                                                                                                                                       |
| -------------------------------------- | -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `photos-browse.spec.ts`                | primary credential   | form login, live-mode lab patient list, click into a patient, saved-photo tile with a real private image (`naturalWidth > 0`, `200`, `image/*`, `no-store`), save control disabled (button + file input + "unavailable" copy), Thai and English, fixture read/file routes fail closed (`404`) |
| `guest-denial.spec.ts` (guest)         | none                 | anonymous visit to `/clinic/lab/photos` lands on `/clinic/login`, no shell, `/api/live/synthetic-patients` and `/api/session` answer `401`                                                                                                                                                    |
| `guest-denial.spec.ts` (non-clinician) | secondary credential | login shows the sign-in error banner, no shell, `/api/session` `401`, lab page still redirects to login                                                                                                                                                                                       |

## Safety properties

- All non-auth writes are blocked **in the browser** before they can reach the
  site: only `POST /api/auth/login` and `POST /api/auth/logout` are allowed
  non-GET calls; `GET`/`HEAD`/`OPTIONS` cover reads, CSRF handshakes and image
  content. Same-origin requests only. Any blocked attempt fails the test.
- `POST /api/write/photo-save` and `/api/write/photo-status` are never called and
  never clicked.
- The save button is asserted **disabled**; it is not exercised.
- Traces, screenshots and video are off; the reporter is line-only; retries are
  `0`; `forbidOnly` prevents a stray `.only` from hiding coverage; failure
  artefacts are discarded (`preserveOutput: "never"`), so no error-context page
  snapshot survives on disk.
- Credentials and secrets never appear in titles, assertion messages or console
  output. Failure output is limited to URLs, statuses, headers and selectors.
- No users, site config, queues, flags or data are created or modified; the run
  performs reads plus one real login (and an optional rejected login).
