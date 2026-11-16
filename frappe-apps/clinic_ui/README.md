# Clinic UI (native read slice and default-off local write path)

Isolated Frappe app candidate for the first Marley-native read slice of the
clinic UI. It provides four read methods and three default-off local POST
methods:

| Method | Path | Guest dispatch |
| --- | --- | --- |
| `GET` | `/api/method/clinic_ui.api.session.bootstrap` | yes — fixed 401 for anonymous/expired |
| `GET` | `/api/method/clinic_ui.api.directory.synthetic_patients` | no — framework 403 |
| `GET` | `/api/method/clinic_ui.api.appointments.synthetic_appointments` | no — framework 403 |
| `GET` | `/api/method/clinic_ui.api.patients.view` | no — framework 403 |
| `POST` | `/api/method/clinic_ui.api.patients.create` | no — default-off local write |
| `POST` | `/api/method/clinic_ui.api.patients.update` | no — default-off local write |
| `POST` | `/api/method/clinic_ui.api.patients.ack` | no — default-off local write acknowledgement |

Status: **candidate only**. Nothing here is installed, migrated, deployed or
activated on any site, and no account or permission is changed. Tests run with
local stubs and prove policy, DTO and gate behaviour — not installed-site
behaviour, the Frappe permission engine or HTTP routing.

A separate **local-only, default-off** native Patient create/edit path exists
under `clinic_ui.api.patients.create`, `...update` and `...ack`. It is not enabled by
this repository and is not production-ready. Its gate, payload, fence setup,
test commands and limitations are recorded in `LOCAL_WRITE_PATH.md`.

Non-goals: no DocType, no `doc_event`, no `override_whitelisted_methods`, no
registration and no deployment. The two local write methods are default-off,
disabled by configuration until an explicit local-site decision, and are not a
production capability. Existing `clinic_photos` behaviour is untouched.

The app now also hosts the built SPA shell at `/clinic` and `/clinic/*` (see
"Native SPA hosting"). That route family is the only website surface it adds.

## Layout

```
clinic_ui/
  api/         whitelisted read and default-off local write entry points
  endpoints/   framework-free request decisions and gate ordering
  core/        text rules, site configuration gate, session/role policy, epoch, DTOs
  core/assets.py  framework-free Vite manifest -> shell asset mapping
  clinic_ui/   the `Clinic UI` module package declared in modules.txt (empty anchor)
  www/         clinic.py + clinic.html — the native SPA shell route
  runtime/     frappe_gateway.py — the single adapter to Frappe
```

`modules.txt` declares one module, `Clinic UI`. Frappe scrubs it to `clinic_ui`
and, during install/migrate, imports `<app>.<module>` — `sync_for` does
`os.path.dirname(frappe.get_module(app_name + "." + module_name).__file__)`. The
nested `clinic_ui/clinic_ui/` package (with a real `__init__.py`, not a namespace
package whose `__file__` is `None`) is that anchor. It is intentionally empty:
this slice ships no DocType, page, report or workspace.

`core` and `endpoints` import no Frappe code; a test enforces this with `ast`.
`www/clinic.py` is the only website module and imports Frappe directly, alongside
`api/` and `runtime/`.

## Gate order (read endpoints; writes add step 6)

1. `Cache-Control: no-store` is set, including on the 401 and on every denial.
2. The request is classified as anonymous/expired or as an authenticated
   identity (below).
3. An authenticated session must be usable: a non-empty `sid` that is not
   `Guest`, a non-blank user identity that is not `Guest`/`Administrator`, and
   an enabled user.
4. The explicit site configuration must be present and valid for **this** site.
5. Exactly one clinic role code must resolve, and it must be allowed for the
   surface (`DOC`, `REC`, `NUR` — the whole first slice).
6. For `POST` writes only, the explicit same-session CSRF check must pass (it
   does not honour `ignore_csrf` or an allowed referrer), the separate
   default-off write capability must be enabled for exactly `marley-sit.local`,
   and a private deployment marker bound to the fence store must exist outside
   site configuration. Then the native `Patient` create/write authority and the
   healthcare side-effect checks run before the operation fence or alias
   receipt is read.

### Anonymous/expired vs unauthorized

`bootstrap` deliberately dispatches guest requests. Without that, Frappe's
`is_whitelisted` raises a 403 for a missing or expired session and the client
never learns it must sign in. The dispatch is narrow and cannot authorize
anything:

| Caller | bootstrap | directory |
| --- | --- | --- |
| No session, expired session, `Guest` sid, `Guest`/blank user | **401** `{"code": "AUTH_EXPIRED"}` | framework 403 (non-guest) |
| Authenticated but `Administrator` or disabled user | 403 | 403 |
| Authenticated, configuration unavailable/invalid | 403 | 403 |
| Authenticated, no mapped role or ambiguous mapping | 403 | 403 |
| Authenticated, single allowed role | 200 live payload | 200 items |

The 401 path reads no site configuration, generates no CSRF token, reads no
patient record and returns a body with exactly one key. Anonymous and
unauthorized outcomes are never merged: the client can distinguish "sign in"
(401) from "you may not" (403), and neither reveals a user, role, epoch,
capability, session identifier or configuration value.

Anything else is refused. A 403 raises the framework's own permission error with
a generic message: no configuration state, role, session identifier, secret or
record detail is exposed.

Frappe also sanitizes `form_dict` for guest dispatches; these methods accept no
parameter, so nothing is read from it.

## Configuration (site config only)

Read from that site's own `site_config.json`. A value in `common_site_config`
cannot enable the feature implicitly. Any missing, malformed or ambiguous value
makes the whole feature unavailable (fail closed) — values are never repaired.

```json
{
  "clinic_ui_enabled": 1,
  "clinic_ui_synthetic_site": "<this site name>",
  "clinic_ui_epoch_secret": "<at least 32 characters of server-owned secret>",
  "clinic_ui_role_map": {
    "DOC": ["Physician"],
    "REC": ["Clinic Reception"],
    "NUR": ["Nurse"]
  },
  "clinic_ui_synthetic_patients": [
    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"}
  ],
  "clinic_ui_synthetic_appointments": [
    {
      "workspace_appointment_id": "APPT-1",
      "native_appointment_id": "NATIVE-APPT-1"
    }
  ]
}
```

| Key | Rules |
| --- | --- |
| `clinic_ui_enabled` | Only `true`/`1`/`"1"`/`"true"`/`"yes"` enable. |
| `clinic_ui_synthetic_site` | Non-blank string that must equal the current site exactly. |
| `clinic_ui_epoch_secret` | String of at least 32 characters. Never returned or logged. |
| `clinic_ui_role_map` | Non-empty mapping. Keys must be `DOC`/`REC`/`NUR`. Values are non-empty lists of distinct native role names (≤140 chars). A role name shared by two codes is refused. |
| `clinic_ui_synthetic_patients` | List (≤1000) of objects with exactly `workspace_patient_id` and `native_patient_id`. Trimmed, control-free, ≤256 chars. Order is preserved. An empty list is valid and returns no items. |
| `clinic_ui_synthetic_appointments` | Optional list (≤1000) of objects with exactly `workspace_appointment_id` and `native_appointment_id`. Same trimming and bounds. Missing or empty returns no appointment items. Only the workspace id is published. |

The patient and appointment allowlists share one public workspace namespace and
one server-owned native namespace. All workspace ids (patient and appointment)
must be disjoint from all native ids (patient and appointment), including across
entry and type boundaries. A repeated value or any public/native collision fails
the whole configuration closed.

The role map is server-owned. There is no client input, no filter, no limit and
no user parameter on the directory or appointment read endpoints; an unexpected
query parameter becomes a framework error rather than an accepted input.

## DTOs

`bootstrap` (`GET /api/method/clinic_ui.api.session.bootstrap`):

```json
{
  "message": {
    "mode": "live",
    "sessionEpoch": "<64 hex chars>",
    "user": {
      "id": "user@example.test",
      "displayName": "User Example",
      "roles": ["DOC"]
    },
    "capabilities": {"write.patient.create": "enabled", "...": "read_only"},
    "csrfToken": "<frappe session csrf token>"
  }
}
```

- `mode` is always `live`; a missing mode is treated by the React client as a
  fixture session, which this app never emits.
- `sessionEpoch` is `HMAC-SHA256(secret, site || 0x00 || sid)` in hex. It is
  deterministic per session, differs across session/site/secret, is not
  reversible and never contains the raw `sid`. It is a cache-identity value, not
  a credential.
- `roles` always has exactly one mapped clinic code.
- Every capability name from `contracts/capabilities.json` is present. The
  default for every capability is `read_only`. Only `write.patient.create` and
  `write.patient.update` may be `enabled`, and only for a DOC/NUR session on the
  exact approved local site after the bootstrap endpoint verifies the write
  flag, deployment marker, a ready fence with no unresolved operation/barrier or
  latch, acceptable Healthcare Settings and the caller's native Patient
  permission. The DTO rejects `enabled` on every other capability. Any failure
  leaves them `read_only`; REC and every other role stay `read_only`.
- `csrfToken` is the Frappe server-side session CSRF token; a missing or blank
  token fails closed. It is not cached, persisted or logged.

Anonymous/expired (`bootstrap` only, HTTP 401):

```json
{"message": {"code": "AUTH_EXPIRED"}}
```

That is the whole body: no `user`, `roles`, `sessionEpoch`, `capabilities` or
`csrfToken`, and no site-configuration or database read happens first.

`synthetic_patients` (`GET /api/method/clinic_ui.api.directory.synthetic_patients`):

```json
{
  "message": {
    "items": [
      {
        "workspace_patient_id": "WS-1",
        "display_name": "Patient One"
      }
    ]
  }
}
```

The response carries no native docname. Two disjoint sources are merged: the
configured allowlist, and terminal committed create receipts discovered
read-only in the private write fence. Each created alias is re-derived from the
recorded creator, site and operation key, and an inconsistent, malformed,
duplicate or colliding record is omitted. Only entries for which the **current**
user passes a native `Patient` read permission check appear. A denied, missing or
malformed entry is omitted without any description, so the client cannot
distinguish "denied" from "not configured". A missing or corrupt fence leaves
the static allowlist intact and never falls back to an unfiltered Patient list.
No record field other than the display name is read into the response.

`synthetic_appointments` (`GET /api/method/clinic_ui.api.appointments.synthetic_appointments`):

```json
{
  "message": {
    "items": [
      {
        "workspace_patient_id": "WS-1",
        "appointment_id": "APPT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open"
      }
    ]
  }
}
```

The endpoint takes no client input. Each allowlisted native `Patient
Appointment` is read with the current user's own session only after the native
document read permission and the field-level permlevel/masking checks pass for
`patient`, `appointment_date`, `appointment_time` and `status`. The row is
emitted only when its native patient resolves through the explicit synthetic
patient allowlist; an unknown patient is omitted. The native patient id and the
native appointment docname never appear: `appointment_id` is the configured
workspace reference. A denied or malformed native read fails the whole response,
so no partial list and no unfiltered fallback is returned.

## Error semantics

| Condition | Result |
| --- | --- |
| No session, expired session, `Guest` sid, `Guest`/blank user | bootstrap: fixed 401 `{"code": "AUTH_EXPIRED"}`; directory and appointment reads: framework 403 before the endpoint body, which the native client reconciles through bootstrap to `AUTH_EXPIRED` |
| `Administrator` or disabled user | framework permission error (403) |
| Feature not enabled, other site name, missing/short secret | same permission error; the CSRF token is not even generated |
| Malformed or ambiguous role map | same permission error |
| No mapped role, or two clinic codes matching | same permission error |
| CSRF token absent or blank | same permission error |
| Allowlist malformed or duplicated | same permission error, before any patient read |
| Per-patient native authority denied/missing | entry omitted |
| Unexpected adapter failure | no partial payload is returned |

Every response, success or denial, is marked `no-store`; Frappe's default
`Cache-Control` for API responses is already no-store.

Privacy: the raw `sid`, the cookie, the CSRF token in logs, the epoch secret and
all record contents except the allowlisted display name are outside the payload
and outside logging.

## Native SPA hosting (`/clinic`, `/clinic/*`)

The app serves the built React SPA as a Frappe website page. The route rules are
the documented v16 `website_route_rules` hook, compiled by Werkzeug in
`frappe.website.path_resolver.resolve_from_map`:

```python
website_route_rules = [
    {"from_route": "/clinic", "to_route": "clinic"},
    {"from_route": "/clinic/<path:app_path>", "to_route": "clinic"},
]
```

| Path | Handled by |
| --- | --- |
| `/api/*`, `/app/*`, `/desk/*`, `/assets/*`, `/login`, other website pages | unchanged — the rules match only `/clinic` |
| `/clinic` | `clinic_ui/www/clinic.py` + `clinic_ui/www/clinic.html` |
| `/clinic/<any client route>` | same template; `app_path` is captured and ignored, the SPA router reads `window.location` |

No global `website_path_resolver` is registered: that hook would replace the
resolver for the whole site instead of adding one route family.

### Cache contract

The shell is a document, never a cached one:

- `clinic_ui/www/clinic.py` sets `no_cache = True`, so Frappe's `cache_html`
  window does not apply and a new build is used on the next request;
- `get_context` pins `Cache-Control: no-store,no-cache,must-revalidate,max-age=0`
  on every outcome, which is the header browsers and intermediaries see;
- the nested route is additionally uncacheable because
  `evaluate_dynamic_routes` marks any matched rule with captured arguments.

Known exception, by framework design: if an operator sets
`frappe.flags.force_website_cache` globally, `can_cache` ignores `no_cache` and
the HTML may be served from cache for up to 300 s. That flag already overrides
every `no_cache` page on the site; do not enable it on a site that hosts this
shell.

### Built assets

The SPA is built separately from Frappe and deployed into the app:

```bash
pnpm --filter @clinic/app build:native    # vite build --mode native
```

The native build is the only build that changes `base`, output and the transport
flag. All three are set by `app/vite.native.ts` from the mode alone:

| Mode | `base` | Output | Transport |
| --- | --- | --- | --- |
| default (`pnpm build`) | `/` (Vite default) | `app/dist/` | proxy — flag unset |
| `native` | `/assets/clinic_ui/ui/` | `frappe-apps/clinic_ui/clinic_ui/public/ui/` | Frappe — `define` pins `import.meta.env.VITE_CLINIC_NATIVE = "1"` |

The transport is a build-time constant (`src/transport/native/flag.ts`), so the
native mode *defines* it instead of reading a shell variable or `.env.native`: a
user `define` wins over the env values Vite loads, so `VITE_CLINIC_NATIVE=0` in
the environment cannot produce a Frappe-hosted bundle that still talks to the
proxy routes. `app/src/test/native-build.test.ts` builds through the real
`vite.config.ts` in both modes and evaluates the artifact: native returns
`nativeTransportEnabled() === true` and `assertLegacyRoute()` throws
`UNSUPPORTED`; the default build does the opposite, with and without
`VITE_CLINIC_NATIVE` in `process.env`.

Frappe serves an app's `public` folder at `/assets/<app>` (`/assets` is mapped to
`sites/assets` in `frappe/app.py`), so the built files are reachable at
`/assets/clinic_ui/ui/assets/<name>-<hash>.js`.

`frappe-apps/clinic_ui/clinic_ui/public/ui/` is generated and gitignored — the
bundle is never committed. The shell never hardcodes a hash:

1. the native build emits `.vite/manifest.json` next to the assets;
2. `clinic_ui/core/assets.py` (pure, Frappe-free) reads the manifest from that
   build root (`<app>/public/ui`) and maps `index.html` -> entry `file` + `css`
   list to absolute URLs, rejecting any value that is not a plain relative
   `.js`/`.css` path (no `..`, no absolute path, no query, no scheme);
3. every referenced file must also be *deployed*: it has to exist under the build
   root, resolve inside it after following symlinks, and be a regular file — a
   manifest that names a file the build did not produce, or a link out of the
   build root, is refused;
4. `clinic_ui/www/clinic.py` puts those URLs in the context and the template
   renders them.

If the manifest is missing, unreadable, not valid UTF-8, malformed, has no shell
entry, or names a script/stylesheet that is not deployed under the build root,
the page returns **503** with no asset reference at all instead of a shell that
points at files a browser cannot load.

Deployment integration is not done here. A deployment must run the native build
before the assets are served, and `bench build`/`bench clear-cache` behaviour for
`public/` and for the cached `website_route_rules` hook (Frappe caches the rule
list in Redis outside dev-server mode) still has to be confirmed on the site.

## Tests

```bash
cd frappe-apps/clinic_ui
python3 -m unittest discover -s tests/unit -t . -v     # full suite, no network, no Frappe
```

The suite uses only `unittest`, `ast` and fake in-memory stubs; it never imports
Frappe and never touches a site, database or network. Coverage:

- `test_policy.py` — session validity, anonymous classification, guest/
  administrator/disabled denial, single-role resolution, ambiguity refusal.
- `test_config.py` — every configuration rule, fail-closed on each malformed
  shape, allowlist trimming/order/immutability.
- `test_epoch.py` — determinism, separation, hex/DT length, no raw sid,
  fail-closed inputs.
- `test_dto.py` — exact wire shapes, single role, no extra fields, bounds.
- `test_capabilities.py` — default capabilities `read_only`, patient writes
  enabled only when explicitly requested; parity with
  `contracts/capabilities.json`.
- `test_endpoints_session.py` / `test_endpoints_directory.py` — gate ordering,
  anonymous 401 dispatch vs authenticated 403 denial, CSRF fail-closed, omission
  of denied patients, no partial payload, no secret in payload or logs.
- `test_gateway_adapter.py` — the adapter mechanics with an injected fake Frappe
  module: 401 status key, no-store header, generic denial message, site-config
  source, CSRF fail-closed, per-patient authority.
- `test_whitelist_surface.py` — the app exposes exactly the two GET-only methods,
  guest dispatch exists for `bootstrap` only, and no mutation helper is declared.
- `test_hooks_routes.py` — the website route rules are exactly the two shell
  rules, target the one template, leave `/api`, `/desk`, `/app`, `/assets` and
  other site paths unmatched, and no global path resolver or redirect is added.
- `test_module_packages.py` — every `modules.txt` entry scrubs to a real package
  under the app package (`__init__.py` present, `__file__` not `None`), imports as
  `clinic_ui.<module>` without Frappe, and contains no code of its own.
- `test_shell_assets.py` / `test_shell_page.py` — the manifest -> asset mapping
  (deterministic, deduplicated, traversal and absolute paths refused, each
  referenced file must exist under the build root, symlink escapes and
  directories refused, non-UTF-8 manifest refused) and the shell context with a
  fake Frappe module: no-cache module flag, explicit `no-store` header on every
  outcome, exact context keys, 503 fail-closed for a missing manifest, a missing
  script, a missing stylesheet and an invalid manifest, and a Jinja render of the
  real template when Jinja is available.

What these tests do not prove: an installed Frappe site, effective DocPerms,
`has_permission` for the real roles, HTTP routing, CSRF enforcement on a live
request, static-file delivery from `sites/assets`, or any lab behaviour. See
"Open native questions".

## Open native questions (blockers, no guess recorded)

The adapter and the shell were written against the Frappe v16.53.0 source, not
against an installed lab build. Before any lab use, verify on the installed
version — `bench version` and a read-only console check:

1. **Installed version.** The lab reported Frappe 16.53.0 in
   `docs/assessments/2026-11-06-frappe-site-inventory.md`; the route rules and the
   shell controller were read against the `v16.53.0` tag. Re-check both if the
   installed build moves.
2. **Guest dispatch.** `bootstrap` is whitelisted with `allow_guest=True` and
   returns HTTP 401 for anonymous callers. Verified in `version-16` source:
   `is_whitelisted` throws `PermissionError` (403) for a guest on a non-guest
   method, and `frappe.local.response["http_status_code"]` sets the status while
   being stripped from the JSON body, so the client receives
   `{"message": {"code": "AUTH_EXPIRED"}}`. Confirm on the installed build that
   the 401 status survives the request pipeline and that no reverse proxy
   rewrites it to 403 or an HTML error page.
3. **CSRF source.** `frappe.sessions.get_csrf_token()` is used. It lazily
   generates and stores `frappe.local.session.data.csrf_token`. If the installed
   build exposes a different mechanism, the bootstrap `csrfToken` contract must
   be re-derived rather than patched around.
4. **Session identifier.** `frappe.session.sid` is read from the request session.
   The epoch fails closed when it is absent; no fallback (cookie parsing, header)
   is implemented.
5. **Native patient authority.** `frappe.has_permission("Patient", ptype="read",
   doc=<name>, user=<current user>)` with a document name, plus
   `frappe.db.get_value("Patient", <name>, "patient_name")` for the display
   name. The doctype (`Patient`) and field (`patient_name`) are the assumed
   Marley/healthcare names and are not verified on the site.
   `Administrator` bypasses `has_permission` by framework design, which is why
   `Administrator` is denied at the session gate.
6. **Response headers.** `frappe.local.response_headers` is used to add
   `Cache-Control: no-store`; Frappe merges that mapping into the response.
7. **Site configuration scope.** The app reads the site's own
   `site_config.json` so `common_site_config` cannot enable it. If the lab keeps
   required values in `common_site_config`, this app will refuse to run — that is
   the intended default.
8. **Client sign-in path.** The native client must treat the bootstrap 401 as
   "anonymous" and route to sign-in. `app/src/app/SessionProvider.tsx` already
   maps 401 to the anonymous state for its own check; wiring the client to this
   endpoint is a later task, not part of this app.
9. **SPA shell rendering.** The shell relies on `clinic_ui/www/clinic.py`'s
   `get_context` being run before caching decisions (`cache_html` reads
   `context.no_cache` after the controller) and on `context["http_status_code"]`
   being honoured (`TemplatePage.update_context` copies it to
   `self.http_status_code`). Both are verified in v16.53 source but not on the
   site. Confirm that `/clinic` returns 200 with the hashed `<script type="module">`
   and `Cache-Control: no-store`, that `/clinic/patients/x` reloads on the same
   document, and that a site without a native build returns 503 rather than a
   shell pointing at missing files.
10. **Asset deployment.** `frappe.get_app_path("clinic_ui", "public", "ui")` is
    the only path read. It assumes the deployment copies (or symlinks) the app's
    `public` folder to `sites/assets/clinic_ui` and that the native build ran
    before that copy. `bench build` also caches `website_route_rules` in Redis
    when the dev server is off, so the new hook needs a cache clear to appear.
    Neither step has been exercised on the lab.
11. **Route ownership.** `TemplatePage` picks the last installed app that owns
    `www/clinic.html`. If any other app ships a `www/clinic.html`, the shell would
    be shadowed; the site inventory has not been re-checked for that collision.

No account, role, DocPerm or site configuration change is part of this app.
