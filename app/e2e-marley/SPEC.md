# Marley real-UI seven-role E2E (`e2e-marley`)

Real-browser role matrix against the **deployed `marley.local` site**, reached
only through an existing SSH loopback tunnel, driven through the **new React
clinic UI** and its **local Fastify proxy** in `UPSTREAM_MODE=live`.

Read-only by construction: the only state changes a run may cause are one login
per role, one logout per role and the proxy's own session records. Publication is
disabled for the whole run, so every product write capability is `read_only`.

This is **not** a claim that the product is complete. See
[Blocked / not covered](#blocked--not-covered) for the surfaces this suite
deliberately does not exercise.

## Prerequisites

1. SSH tunnel up: `127.0.0.1:18080` → the `marley.local` stack (both ends
   loopback; the suite never starts, stops or re-points the tunnel).
2. Private runtime directory, mode `700`, containing:
   - `roles/credentials.json`, `roles/marley_remaining_synthetic.json`
   - `qa/item_read_pharmacy.json`, `qa/synth_invoice_verify_20261113.json`
   - `ui_e2e_20261113/SYNTHETIC-CREATED.json`
3. Nothing else listening on `127.0.0.1:5183` (app) or `127.0.0.1:3101` (proxy):
   both servers are launched fresh with `reuseExistingServer: false`.

## Run

```sh
cd app
MARLEY_RUNTIME_DIR=/absolute/private/.runtime \
MARLEY_RESULTS_DIR=/absolute/private/results-dir \
  pnpm exec playwright test --config=playwright.marley.config.ts
```

`MARLEY_RESULTS_DIR` must be absolute and **outside this repository**; it holds
the JSON report, the throwaway proxy session database and Playwright's run
marker. No trace, screenshot or video is ever written (`trace/screenshot/video:
off`, `preserveOutput: "never"`).

Case count: **52** (7 per role × 7 + 3 matrix invariants), one worker, zero
retries, serial per role so each role logs in exactly once.

### Rate limit

The proxy allows **10 anonymous CSRF handshakes per client per 10 minutes**; one
login consumes one. A run uses exactly **7** (logouts reuse the authenticated
session token and are not counted). Each run starts a fresh proxy process, so its
throttle bucket is fresh as well — but the suite must not be looped or probed
ad hoc alongside it. A throttled login is surfaced as
`RATE_LIMITED: … UNTESTED` and is an environment condition, **never** a product
failure; there is no automatic retry.

## Role mapping (asserted, not assumed)

Native Frappe roles are resolved by the proxy; exactly one category may match a
login, otherwise the proxy refuses it. Verified against the live site's
`tabHas Role` rows and mirrored in `proxyEnv()`:

| UI role | Account                        | Native role that maps it           | Source                 |
| ------- | ------------------------------ | ---------------------------------- | ---------------------- |
| `DOC`   | `dc.doctor@example.invalid`    | `Physician`                        | clinician allow-list   |
| `NUR`   | synthetic `nur` account        | its unique marker role             | private synthetic file |
| `PHA`   | `dc.pharmacy@example.invalid`  | `DC Pharmacy`                      | custom role            |
| `CAS`   | synthetic `cas` account        | `Accounts User`                    | private synthetic file |
| `MGR`   | `dc.owner@example.invalid`     | `DC Owner`                         | custom role            |
| `REC`   | `dc.reception@example.invalid` | `DC Receptionist`                  | custom role            |
| `ADM`   | synthetic `adm` account        | its marker role (no standard role) | private synthetic file |

`Nursing User` is deliberately **not** used as the nurse allow-list entry: it is
also held by the reception and owner accounts, so it would span categories (and
the proxy would refuse those logins). Allow-lists are derived from the private
files at load time and never printed.

Live data allow-lists passed to the proxy (also from the private files):

- synthetic patient: the `SYNTHETIC-CREATED.json` `Patient` (workspace reference
  and native name identical)
- synthetic item: `qa/item_read_pharmacy.json` → `item_name_allowed`
- synthetic invoice: `qa/synth_invoice_verify_20261113.json` → `docname`, with
  expected totals read from its `observed` block

## Test cases

Every case asserts on a real rendered page or a real same-origin API response;
nothing is mocked. `<R>` is one of `DOC NUR REC PHA CAS MGR ADM`.

| ID               | Asserts                                                                                                                                                                                                                                                                                                        |
| ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `MAR-AUTH-<R>`   | form login (typed credentials, real `POST /api/auth/login`); authenticated shell; topbar role label equals the catalogue label; `GET /api/session` → `200`, `mode: "live"`, `roles` exactly `[<R>]`, matching user id, non-empty display name and session epoch; **every `write.*` capability is `read_only`** |
| `MAR-NAV-<R>`    | sidebar destinations equal the role's allow-list exactly (set equality: extra link = leak, missing link = gap)                                                                                                                                                                                                 |
| `MAR-DATA-<R>`   | the role's selected surface renders the assertions in the table below; the administrator surface is static, not live management                                                                                                                                                                             |
| `MAR-DENY-<R>`   | other roles' selected live endpoints answer `403`; the role's selected endpoints answer `200` with `cache-control: no-store`; `/api/read/patients` and one fixture file path answer `404`; no non-auth write was attempted                                                                                |
| `MAR-APPT-<R>`   | appointment endpoint returns `200` and an items array for DOC/REC, `403` for the other roles, and `no-store` for all; a populated listing is not exercised in this configuration |
| `MAR-RECOVERY-<R>` | encounter-draft recovery endpoint returns `403 WRITE_DISABLED` and `no-store` with write opt-in off; this tests the closed gate, not enabled recovery |
| `MAR-LOGOUT-<R>` | profile "Sign out" → login page; `GET /api/session` → `401`; a protected route redirects to login; no non-auth write attempted                                                                                                                                                                                 |
| `MAR-SPEC-001`   | seven distinct roles, seven distinct accounts, one source file                                                                                                                                                                                                                                                 |
| `MAR-SPEC-002`   | the live photo path is readable by `DOC` and `NUR` only                                                                                                                                                                                                                                                        |
| `MAR-SPEC-003`   | the suite's appointment reader matrix lists DOC and REC only |

### `MAR-DATA-<R>` detail

| Role  | Page                                | Asserted                                                                                                                                                                                                                                                                                                     |
| ----- | ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `DOC` | `/clinic/lab/photos[/<ref>]`        | exactly one authorized lab patient; gallery tile; private bytes `200` + `image/*` + `no-store` + non-empty body; `<img>` `src` matches the synthetic patient content-path pattern; browser decoded it (`naturalWidth/Height > 0`); save region present but file input and save button **disabled** with the "unavailable" copy |
| `NUR` | `/clinic/lab/photos[/<ref>]`        | same patient, same photo bytes and decoded image; the save region is **absent**                                                                                                                                                                                                                              |
| `REC` | `/clinic/patients`                  | live directory row for the allow-listed patient (workspace reference + non-empty display name)                                                                                                                                                                                                               |
| `PHA` | `/clinic/pharmacy`                  | exactly one row; item code equals the allow-listed `DEMO-` name; name/unit cells non-empty                                                                                                                                                                                                                   |
| `CAS` | `/clinic/finance`                   | exactly three metrics: submitted-invoice count, invoiced total, outstanding total, all equal to the verified invoice values with its currency                                                                                                                                                                |
| `MGR` | `/clinic/finance`, `/clinic/system` | same invoice totals, plus the audit/state surface renders with no clinic data rows                                                                                                                                                                                                                           |
| `ADM` | `/clinic/system`                    | audit/state surface renders with no clinic, pharmacy or finance data rows                                                                                                                                                                                                                                    |

## Blocked / not covered

The following are not exercised by this read-only suite and must not be
reported as passing on its evidence. Live write routes exist for some
workflows, but their opt-ins are disabled for this suite.

| Area                                                                                    | Status                                                                                                                                                                                |
| --------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Patient create / update (`write.patient.*`)                                             | BLOCKED — capability `read_only`; no synthetic record may be created                                                                                                                  |
| Appointment book / reschedule / cancel / check-in (`write.appointment.*`)               | BLOCKED — no booking workflow is verified. A synthetic appointment listing exists for DOC and REC; this suite has no appointment allow-list and cannot demonstrate a populated listing. Queue, checkout and reception KPIs have no corresponding live read path. |
| Payments: receive / refund / invoice submit (`write.payment.*`, `write.invoice.submit`) | BLOCKED — finance is summary-only; no payment or receipt surface is exercised                                                                                                         |
| Admin user / master data (`write.admin.*`)                                              | BLOCKED — the audit/state surface is a static layout sample; no user or master mutation is attempted                                                                                  |
| Clinical draft / complete, medication orders, lab order/review, procedure complete      | NOT COVERED — standalone synthetic draft creation, recovery and Review Details are separately opt-in; this suite does not test their enabled flows. `/clinic/visits/:id` remains fixture-only in live mode. |
| Photo consent / upload / link / annotate (`write.photo.*`)                              | BLOCKED — upload asserted disabled; the single saved photo is pre-existing evidence, not created by this suite                                                                        |
| Stock receive / transfer / adjust, purchase-order draft/submit                          | BLOCKED — pharmacy is item-metadata only, no stock quantities or movements                                                                                                            |
| Notifications, print preview, language persistence, keyboard/a11y sweeps                | NOT COVERED by this suite (separate fixture suites in `app/e2e`)                                                                                                                      |
| Non-clinician rejected-login path                                                       | NOT COVERED here (see `app/e2e-live` for the guest/secondary denial scenario)                                                                                                         |

## Safety properties

- **Write guard in the browser**: only `POST /api/auth/login` and
  `POST /api/auth/logout` may be non-GET; everything else non-GET and every
  cross-origin request is aborted before it leaves the browser, and any blocked
  attempt fails the case.
- **No secrets in output**: credentials are read from private mode-`600` files,
  typed into the form only, and never placed in a title, assertion message,
  console line or report field. Errors name the file or schema field, never a
  value.
- **No sensitive artefacts**: traces, screenshots and video are off; failure
  output is discarded; the reporter is line + JSON only, written under
  `MARLEY_RESULTS_DIR` (mode `700`, outside the repository). Existing evidence
  directories are never modified or deleted.
- **No backend changes**: no site config, role, permission, user, queue, flag or
  record is created, edited or removed. `e2e-live` and the fixture suite in
  `app/e2e` are untouched and unshared.
- **Single stack**: one freshly launched Vite server + proxy per run, both on
  loopback, `reuseExistingServer: false`; the tunnel is fixed at
  `http://127.0.0.1:18080` and is not started by Playwright.
