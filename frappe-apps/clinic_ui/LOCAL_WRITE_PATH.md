# Local synthetic Patient write path (default off)

**Status:** local-only implementation candidate. Not deployed, not enabled on
any site, not production-ready. No migration, MariaDB table, DocType, permission
or site-config change is made by this change. No Docker, site, deployment,
React or commit operation was performed by this change.

This path exists for the user-approved synthetic local OrbStack experiment. It
uses only the native Frappe `Patient` controller (`new_doc(...).insert()` and
`get_doc(...).save()`) and native `has_permission` checks. It never passes
`ignore_permissions` and never writes a native docname from the client.

## Methods

| Method | Path | Transport |
| --- | --- | --- |
| `POST` | `clinic_ui.api.patients.create` | JSON object |
| `POST` | `clinic_ui.api.patients.update` | JSON object |

Frappe performs its normal POST CSRF check before the method runs:
`frappe.auth.HTTPRequest.validate_csrf_token` (v16) rejects an unsafe method
when the `X-Frappe-CSRF-Token` header or body `csrf_token` does not match the
session token. The module never sets `ignore_csrf`.

In addition, the endpoint performs its **own explicit check**: it reads the
initialized session token from `frappe.local.session.data.csrf_token` and
compares it with the supplied body/header token using a constant-time compare.
Both must be non-empty. This check deliberately does not consult
`frappe.conf.ignore_csrf` and does not honour an allowed referrer, so those
framework bypass branches cannot authorize a clinic write. A missing token,
missing session token or mismatch is a generic `403` before any fence access.
The bootstrap read supplies the session CSRF token used by the client
transport.

### Create body

```json
{
  "operation_key": "16..128 url-safe chars",
  "first_name": "required",
  "last_name": "optional",
  "sex": "required, must exist in live Gender",
  "dob": "optional YYYY-MM-DD, not future"
}
```

### Update body

```json
{
  "operation_key": "a new 16..128 url-safe key for this edit",
  "workspace_patient_id": "server-issued alias",
  "base_modified": "authoritative modified token from view/create/edit",
  "first_name": "optional, cannot be cleared",
  "last_name": "optional, null/empty clears",
  "sex": "optional, cannot be cleared",
  "dob": "optional, null/empty clears"
}
```

Unknown keys are rejected, including `status`, `patient_name`, `customer`,
`user_id`, `invite_user`, `email`, `mobile`, `phone`, `naming_series` and any
native docname. `invite_user` is set to `0` server-side; no contact field is
accepted or written.

### Native controller side effects (verified Frappe healthcare v16)

The native `Patient` controller has side effects the synthetic path must not
trigger. Before any dispatch:

* `Healthcare Settings.collect_registration_fee`, `send_registration_msg` and
  `link_customer_to_patient` must all be falsy. A truthy value or a settings
  read failure denies the request. (`Patient.after_insert` would otherwise
  disable the patient or send an SMS; `Patient.on_update` would create or
  update a Customer.)
* For update, the target Patient must have empty `invite_user`, `email`,
  `mobile`, `phone`, `customer` and `user_id`. A missing row, read failure or
  any populated value denies before dispatch. This prevents a Contact/
  Customer/Website User side effect from the edit.
* `invite_user = 0` is set server-side on create and again on update. No
  contact, customer, user or status value is accepted from the client.

These are runtime checks in the adapter/endpoint and are unit-tested with a
fake Frappe. The current local site has `link_customer_to_patient = 1`, so this
path intentionally fails closed there until that setting is reviewed and
changed; no site setting was modified by this change.

### Pre-insert document inspection

Frappe applies field defaults when it constructs a Patient, and the v16
controller's `before_insert` adds Selling Settings defaults (`customer_group`,
`territory`, `default_price_list`, `default_currency`, `language`). Before any
insert or fence dispatch, the adapter:

1. builds the Patient with only the strict fields and `invite_user = 0`,
2. calls the controller's `before_insert` to expose those defaults,
3. rejects any populated side-effect trigger: `customer`, `customer_name`,
   `email`, `mobile`, `phone`, `user_id`, `address_line1`, `address_line2`,
   `city`, `state`, `pincode`, `country`, or a truthy `invite_user`.

`insert_native_patient` repeats the defaults/inspection immediately before the
actual insert, so a default that appears between preparation and insert is also
caught. The v16 controller triggers these paths: `create_customer` /
`update_linked_customer` (customer fields), `set_contact` and
`create_website_user` (email/mobile/phone/user/invite), and `make_address`
(any `address_line1`). Selling/currency/language defaults alone are not
side-effect triggers and are allowed; the Healthcare Settings checks above
block the Customer path. A rejected document is never inserted (tests assert
zero inserts and zero fences), so a defaulted trigger fails closed before
mutation.

### Post-commit response latch

The native transaction and the fence's `COMMITTED` terminal are durable before
the success DTO is built. If building that DTO fails (for example the native
detail is no longer readable or a value fails validation), the endpoint does
**not** rely on the committed key alone, because a committed terminal is not an
unresolved key and would let a new key run.

Instead it writes a separate durable manual-investigation latch. Two records
are attempted:

1. a private `write-fence.manual-investigation.latch` file in the fence
   directory, checked before every new claim and independent of SQLite or the
   interprocess lock, and
2. a reserved `DISPATCHED`/`UNKNOWN` event pair written under the same
   interprocess lock.

Either record is enough. The reserved identity is not a client operation key;
client keys with the `clinic-ui-` prefix are rejected and the reserved actor is
denied. Once latched:

* every new key for every actor is refused with `409 WRITE_LOCKED`,
* the block survives restart, a corrupt database and cannot be cleared by this
  code,
* an operator must inspect the deployment.

If the primary latch cannot be persisted, the endpoint falls back to a second
reserved event key derived from the operation identity and reason, still under
the same lock. If both records fail, it raises `ManualInvestigationRequired` to
Frappe cleanup instead of returning a normal response; the committed write is
never reported as success. If the fence directory itself is unwritable, new
dispatch attempts against that store also fail closed.

### Write barrier and explicit acknowledgement

Every create/update claim installs a second, reserved `DISPATCHED` record: a
deployment-wide write barrier bound to the original actor, kind and operation
key. It is written in the same fence transaction as the operation claim and
stays unresolved through the native commit, the durable `COMMITTED` terminal and
success-response construction. While it is unresolved, **no new operation key is
admitted for any actor**, so writes are strictly one-at-a-time across sessions
and restarts.

Only an explicit client acknowledgement resolves the barrier, by appending a
single `COMMITTED` terminal carrying an HMAC acknowledgement receipt. The token
binds site, original actor, kind, operation key and the durable create/update
receipt; it never exposes the native receipt or patient name. A valid ack is
idempotent (a repeat returns success without a second terminal). An invalid,
wrong-actor, non-committed or malformed ack leaves the barrier untouched.

If the success response is lost or the process dies after commit but before the
client acknowledges, the barrier stays unresolved. Nothing in this code clears
it automatically: new writes remain `409 WRITE_LOCKED` until an operator
investigates the deployment.

The client acknowledgement body is:

```json
{
  "operation_key": "the original create/update key",
  "kind": "create" or "update",
  "ack_token": "<64 lowercase hex ackToken from the success response>"
}
```

`POST /api/method/clinic_ui.api.patients.ack` uses the same read gate, explicit
CSRF check, exact-site write flag and deployment marker as the write endpoints,
but it does **not** require native Patient create/write permission. Acknowledging
is not a write. A malformed body returns `422 VALIDATION`; any invalid,
non-committed or store-failure outcome returns `409 WRITE_LOCKED` and leaves the
barrier in place. Success is `200 {"acknowledged": true}`.

The UI must therefore:

1. send one create/update,
2. keep the returned `ackToken` in memory (never log it, never reuse it for a
   different key),
3. call `ack` exactly once with the original `operation_key`, `kind` and token
   after it has durably confirmed the success response,
4. not auto-retry `ack` and not generate a replacement token; if `ack` fails or
   the success response is lost, surface a manual-investigation condition.

### Success response

```json
{
  "status": "created" or "updated",
  "ackToken": "<64 lowercase hex>",
  "patient": {
    "workspace_patient_id": "...",
    "display_name": "...",
    "first_name": "...",
    "last_name": null,
    "sex": "Female",
    "dob": "1990-01-02",
    "status": "Active",
    "modified": "2026-11-14T09:30:00.123456"
  }
}
```

### Failure outcomes

| Status | Code | Meaning |
| --- | --- | --- |
| `403` | framework denial | session/configuration/role/native authority refused before dispatch |
| `422` | `VALIDATION` | strict body, date, operation-key or live-Gender failure; no dispatch |
| `409` | `WRITE_LOCKED` | fence/store unavailable, an unacknowledged write barrier, any site-wide unresolved key, key already dispatched/committed/unknown, an invalid acknowledgement, or a post-dispatch outcome is uncertain |
| `409` | `OPERATION_CONFLICT` | same actor/key bound to a different payload |
| `409` | `STALE_VERSION` | update `base_modified` did not match before dispatch; no field changed |

A post-dispatch failure is always uncertain. The operation key stays locked;
there is no automatic retry, no inferred no-commit and no success claim. The
endpoint marks the key `UNKNOWN` and then explicitly calls `frappe.db.rollback()`
so Frappe's end-of-request commit for unsafe methods cannot persist a partial
`Patient`, `Customer`, `Contact` or other native side effect.

If that rollback itself fails, the endpoint does **not** return a normal `409`.
It raises `RollbackFailed` so the exception reaches Frappe cleanup; returning a
handled error could let the implicit request-end commit run. The fence key is
already `UNKNOWN` (or stays `DISPATCHED` if the terminal append also failed),
so the lock is retained. A committed key is not replayed: a repeat returns
`WRITE_LOCKED`.

## Gate (default off)

Read surfaces continue to use the existing configuration. Writes additionally
require **all** of the following:

1. The resolved current site is exactly `marley-sit.local`.
2. The site's own `site_config.json` enables the separate write flag and names
   that same exact site:

```json
{
  "clinic_ui_native_patient_write_enabled": 1,
  "clinic_ui_native_patient_write_site": "marley-sit.local"
}
```

3. A private deployment marker exists **outside** `site_config.json` at
   `private/clinic_ui_native_write_deployment/marley-sit.local.json`, is owned
   by the site user, is not group/world readable or a symlink, names the same
   site, and contains the `deployment_id` of the initialized write-fence store.
4. The operator initialization command below has therefore already run.

Absent keys, a wrong site name, a copied configuration, a matching hostname, a
second site on the same bench, a missing marker or a marker bound to a different
fence deployment all leave writes off/denied. `marley.local` is refused by
construction regardless.

### Threat model (local-only)

The marker binds site identity to one fence deployment, so configuration alone
cannot enable a copied lab. It is still only a local experiment:

* Anyone who can write the site's private directory and fence store can forge
  the marker. That is the same trust boundary as the site database, which can
  already write Patient rows, and is out of scope here.
* A copied `site_config.json` plus hostname does not satisfy the marker unless
  the fence store and its `deployment_id` were also copied or re-initialized,
  which is an explicit operator action. The marker also binds the canonical
  private-directory path, so a copied site directory at a different location is
  refused even if the store and marker were copied with it.
* Restore/recovery is **not** independently anchored; restore detection remains
  waived for this local-only experiment.
* This is not a production authorization and makes no production claim.

## Stable alias and eligibility

No new table stores a mapping. A create returns a server-derived opaque alias:

```text
wpa1_<actor/site HMAC tag>_<operation-key HMAC tag>_<operation key>
```

The alias contains no native docname. The existing static allowlist is
unchanged and is checked first. A sealed alias is eligible only when the current
actor has a terminal `COMMITTED` create receipt in the external fence. A
missing, dispatched, unknown or malformed receipt denies the read and locks the
write. The native Patient permission check remains authoritative on every
request. Rotation of `clinic_ui_epoch_secret` invalidates existing sealed
aliases; that is a documented limitation, not an automatic migration.

`patients.view` includes the authoritative `modified` field. Verified Frappe
v16 semantics: `modified` is a standard `default_fields` column, **not** a
DocField, so `Meta._fields`/`get_field("modified")` do not contain it
(`frappe/model/meta.py:514-515`, `frappe/model/__init__.py:84-93`). It is read
only after the document-level Patient read permission succeeds; all ordinary
DocFields still receive the existing permlevel and mask checks. The view also
resolves sealed aliases through the committed-create rule above.

## Persistent operation fence

The path reuses `clinic_ui.core.write_fence` for a persistent, append-only
operation ledger and interprocess lock. The lock is held across the native
`Patient` insert/save and the single explicit `frappe.db.commit()`. The
terminal `COMMITTED` receipt is appended after that commit. If the process dies
before the terminal append, the key remains `DISPATCHED`; restart does not
unlock it. A missing store or anchor fails closed.

The block is **site-wide**: before accepting any new operation key, the fence
checks the latest event for every actor and key. If any key is `DISPATCHED` or
`UNKNOWN`, every new key for every actor is refused with `SiteLocked` →
`409 WRITE_LOCKED`. There is no timeout, automatic unlock, actor exception or
"fresh key" workaround. A key that reaches a durable `COMMITTED` terminal no
longer blocks new work.

The store is **not** created by a request. Initialize the store, anchor and
deployment marker once per local site, only after review and explicit approval:

```bash
bench --site <local-site> execute \
  clinic_ui.runtime.patient_write_fence.initialize_for_site
```

The anchor lives under the same site private directory (`private/`), not an
independently backed-up system. That is sufficient for same-process restart
locking but does **not** detect a coordinated restore of the site private files.
The task owner explicitly waived prior receipt/legacy/restore gates for this
local-only experiment. This is not a restore-safety or production claim.

## Verification performed

```bash
cd frappe-apps/clinic_ui
python3 -m unittest discover -s tests -t . -q
```

Result on 2026-11-14: **542 tests run, 0 failures**. The obsolete
`patches.txt` expectation was replaced with an assertion that the untracked
schema patch is intentionally not registered.

Focused suites all pass:

```bash
python3 -m unittest \
  tests.unit.test_patient_write \
  tests.unit.test_patient_alias \
  tests.unit.test_fence_store \
  tests.unit.test_deployment_marker \
  tests.unit.test_capabilities \
  tests.unit.test_dto \
  tests.unit.test_policy \
  tests.unit.test_endpoints_session \
  tests.unit.test_endpoints_patient_view \
  tests.unit.test_endpoints_patient_write \
  tests.unit.test_gateway_adapter \
  tests.unit.test_config \
  tests.unit.test_whitelist_surface \
  tests.unit.test_traceback_suppression \
  tests.unit.test_write_fence
```

Result: **394 tests, 0 failures**. `python3 -m compileall -q clinic_ui tests`
also passes.

These are stub/unit tests. No Frappe site, HTTP server, CSRF failure path,
native controller validation, Customer/Contact side effect or real database
transaction was exercised. No Docker or site command was run.

## Limitations and review findings

1. **No real Frappe integration test was run.** The adapter is exercised with a
   fake Frappe module. Real `Patient` validation, `Customer` side effects,
   `modified` precision, CSRF rejection and HTTP status wrapping remain
   unverified on a live site. Frappe v16 source semantics for `modified` and
   the controller side effects were verified by reading upstream source, not
   by running an isolated site.
2. **Current local settings block writes.** The local site has
   `link_customer_to_patient = 1`, so the healthcare-settings check fails
   closed. Changing that setting is an operator decision and was not performed.
3. **The fence and marker need explicit operator initialization.** Until the
   store, anchor and marker exist, every write is denied/`409 WRITE_LOCKED`.
   This is intentional fail-closed behavior.
4. **The fence anchor and marker are local, not independent.** Restore/recovery
   detection is waived for this local-only experiment. See the marker threat
   model: anyone who can write the private directory can forge the marker.
5. **No idempotent committed replay.** A committed operation key returns
   `WRITE_LOCKED`. UI must treat any post-dispatch failure as uncertain.
6. **Created patients are discoverable only through the fence.** The directory
   endpoint merges the static site allowlist with a hard-bounded, read-only
   enumeration of committed create receipts. The native docname never appears in
   the response. A created patient remains reachable through its sealed alias
   via view; update still requires the creator's actor-bound resolution. A
   truncated enumeration page, or a created slice that would exceed the client's
   1000-item response limit, omits the entire created slice and keeps the valid
   static items.
7. **Native Patient side effects are blocked, not eliminated.** The checks above
   deny when the installed settings or target state are unsafe. They are not a
   substitute for auditing the installed healthcare app version.
8. **Pre-existing untracked groundwork conflicts with the no-new-schema rule.**
   `clinic_ui/core/patient_write_ledger.py` and
   `clinic_ui/patches/v0_0_1_patient_write_ledger.py` existed before this change
   and describe raw MariaDB tables. They are not imported or wired by this
   implementation. They should be removed or quarantined by the owner before
   review; this change does not delete prior work without approval.
9. **The client already tolerates the optional detail fields.** The native
   directory parser consumes only the alias and display name, and the detail
   parser treats `modified` and `editable` as optional validated fields. No
   client file was changed for this slice.
10. **No deployment, migration or permission change.**
11. **Bootstrap capabilities enable only the reviewed patient writes.**
    `write.patient.create` and `write.patient.update` are published as `enabled`
    only for a `DOC` or `NUR` session when the full gate passes: exact approved
    site and write flag, private deployment marker, a safely initialized and
    ready fence store, Healthcare Settings without automatic side effects, and
    the caller's native Patient create/write permission. Readiness takes the same
    interprocess lock as a claim and returns false when a manual-investigation
    latch, a dispatched key, an unknown terminal or a committed-but-unacknowledged
    barrier exists. The claim path still re-checks under that lock, so readiness
    never authorizes a write by itself. `DOC`/`NUR` is one shared policy constant
    used by bootstrap and by the write/ack endpoints; REC and every other role
    stay `read_only` and are refused before native permission is consulted.
    `enabled` is rejected by the DTO for every capability except the two patient
    writes. Any check error fails closed. The client integration that consumes
    these flags is still a separate change. This does not modify any site or
    setting: the current local site keeps `link_customer_to_patient = 1`, so the
    bootstrap capability stays `read_only` until an operator reviews and
    changes it.
12. **Alias design interpretation.** The alias is server-derived, not random:
    the server wraps the client's idempotency operation key with site/actor HMAC
    tags. The static allowlist is unchanged and is checked first; a new patient
    is eligible only via a committed create receipt. If "without relaxing the
    allowlist" is read to forbid any alias outside the config list, then this
    design is not acceptable and create/edit must stay disabled; no-schema
    persistence cannot otherwise map a created patient back to an alias.
13. **UI cannot yet distinguish the new 409 codes.** The backend emits
    `WRITE_LOCKED`, `OPERATION_CONFLICT` and `STALE_VERSION`, and the error
    sanitizer preserves them. The current client error map
    (`app/src/domain/errors.ts` + `app/src/transport/error-map.ts`) recognizes
    none of them, so `fromWire` returns `UNKNOWN` and the HTTP status fallback
    maps every 409 to `CONFLICT_STALE` with `retryable: true`. That is unsafe
    for a post-dispatch uncertain write: the UI must treat it as locked/needs
    manual investigation, not retry. This requires a UI/transport owner change
    and was not made here (no React file changed).
