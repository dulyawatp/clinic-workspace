# Local native patient contracts and recovery: assessment

**Status: ASSESSED — patient writes remain DISABLED. Unapproved deletions
occurred during the rehearsal; see §9.**  
**Date:** 2026-11-14  
**Scope:** the isolated synthetic site `marley-sit.local` at
`http://127.0.0.1:18081` (Compose project `marley-sit`), its native
Patient/Gender metadata, effective permissions for the three synthetic actors,
separation from legacy data and intent stores, and an isolated backup/restore
rehearsal. No lab, production, real patient data or legacy intent store was
accessed. No product code, permission, account or write gate was changed.

This assessment distinguishes **verified on the running site** from
**unresolved**. A field, permission or recovery claim not backed by the evidence
below is marked unresolved rather than inferred.

## 1. Evidence basis

| Source | Use |
| --- | --- |
| Running `marley-sit.local` (Frappe 16.54.0, ERPNext 16.52.1, Healthcare 16.6.1, clinic_ui 0.0.1) | Live `DocType` metadata, `DocPerm`, `has_permission`, site configuration, HTTP session/directory behavior |
| `deploy/local-sit/` and Task 1 report | Stack identity, fixtures, read-only capability surface |
| `frappe-apps/clinic_ui/` | Existing gate order, DTOs, adapter and open native questions |
| `contracts/capabilities.json` and `app/src/` | Capability and client contracts |
| Private backup `20261114_134217` | Restore source |
| Separate rehearsal project `sit-rehearsal` | Restored-site verification and contained real-Frappe probes |

No clinical record, site configuration, permission or account on the source
stack was written. The only source-stack writes were the backup file set created
by `bench backup` and ordinary log output. The real-Frappe create/edit probes
ran only against the restored copy, after the restore succeeded, and were
removed afterwards. The rehearsal target is stopped; its volumes are retained.

## 2. Installed Patient and Gender metadata (verified)

### 2.1 Patient

`Patient` is a non-submittable DocType. `autoname` is `naming_series:`, but
Healthcare Settings has `patient_name_by = "Patient Name"`, so the docname is
derived from the computed full name rather than from the `HLC-PAT-...` series.
There are no `Custom Field` or `Property Setter` records for `Patient`.

| Field | Type | Required | Read-only | Relevant behavior |
| --- | --- | --- | --- | --- |
| `naming_series` | Select (`HLC-PAT-.YYYY.-`, `ER-.YYYY.-`) | no | no | Value present on create (`HLC-PAT-.YYYY.-`) but not the effective autoname while `patient_name_by = Patient Name` |
| `first_name` | Data | **yes** | no | `no_copy`; missing value returns 417 |
| `middle_name` | Data | no | no | Included in `patient_name` when present |
| `last_name` | Data | no | no | Included in `patient_name` when present |
| `patient_name` | Data | no | **yes** | Title field; recomputed by `Patient.set_full_name()` on every validate |
| `sex` | Link → `Gender` | **yes** | no | Invalid value returns 417; only `Female` exists on the site |
| `dob` | Date | no | no | Future date returns 417 (`Patient.validate_dob`) |
| `blood_group` | Select | no | no | Not part of the first slice |
| `status` | Select (`Active`, `Disabled`) | no | **yes** | Created record returned `Active` without a client-supplied value; **read-only metadata does not prevent a REST update** (see §2.3) |
| `mobile`, `phone`, `email` | Data | no | no | `email` plus default `invite_user = 1` can create a Website User and send a welcome email on update |
| `invite_user` | Check | no | no | Default `1`; dangerous when `email` is supplied |
| `user_id` | Read Only → `User` | no | no | Written by the website-user side effect, not by this slice |
| `customer` | Link → `Customer` | no | no | Auto-created and linked because `link_customer_to_patient = 1` |
| `uid` | Data (unique) | no | no | Identification number; excluded from the first slice |
| `patient_relation` | Table → `Patient Relation` | no | no | Excluded |
| clinical history fields (`allergies`, `medication`, `medical_history`, etc.) | Small Text | no | no | Excluded from the first slice |

Verified create side effects on the isolated site:

- `Customer` named after the patient is created and linked in the same
  transaction. On this site `Selling Settings.customer_group`, `territory` and
  `selling_price_list` are empty, so the Customer carries empty values rather
  than failing the insert.
- `Contact` is created only when `email`, `mobile` or `phone` is present
  (source inspection). The probe supplied none, and the Contact count returned
  to its pre-probe value of 3 after cleanup.
- `send_registration_msg = 0`, so no SMS is sent.
- `collect_registration_fee = 0`, so `after_insert` does not set `status` to
  `Disabled`.
- `link_customer_to_patient = 1`.
- A `Patient` create with `email` was not attempted. Source inspection shows
  `on_update` would create a Website User (`invite_user = 1`, empty `user_id`)
  and request a welcome email. That side effect is outside the transaction's
  rollback guarantee if email delivery occurs.

### 2.2 Gender

| Property | Verified value |
| --- | --- |
| `autoname` | `field:gender` |
| Fields | `gender` (Data, unique) |
| Records | exactly 1: `Female` |
| Read permission | `All` role has read; all three synthetic actors can read |
| Create/write permission | `System Manager` only; DOC/NUR/REC cannot add a Gender |

The create form must fetch the live `Gender` list, not hard-code options. Until
an approved master-data action adds more genders, the only selectable value is
`Female`.

### 2.3 Read-only is not server-enforced

On the restored copy, an authenticated DOC `PUT /api/resource/Patient/<name>`
with `{"status": "Disabled"}` returned **200** and the stored status changed to
`Disabled`. The field's `read_only` flag is a form/UI property, not a write
denial. The same probe confirmed that `first_name` changes recompute
`patient_name` while the docname stays unchanged.

Consequences:

- A clinic endpoint must use a strict allowlist and reject `status`,
  `patient_name`, `customer`, `user_id`, `invite_user`, `naming_series`, `uid`,
  `docstatus`, ownership and any unknown key. Relying on `read_only` or native
  `write` permission alone is unsafe.
- A native write capability is broader than the first slice: DOC and NUR also
  have `delete`, `report`, `export`, `print`, `email` and `share` on `Patient`.

## 3. Effective permissions (verified)

`User Permission` count is 0 and no `Permission Rule` applies. The `Patient`
`DocPerm` roles are `System Manager`, `Physician`, `Laboratory User` and
`Nursing User`. **`Healthcare Administrator` has no `Patient` permission at
all** on this site.

| Actor | Native role | Clinic code | Patient read | Patient create | Patient write | Patient delete | Gender read | Directory HTTP | Create HTTP |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `sit.doc@example.invalid` | Physician | DOC | yes | yes | yes | yes | yes | 200, 1 item | 200 |
| `sit.nur@example.invalid` | Nursing User | NUR | yes | yes | yes | yes | yes | 200, 1 item | 200 |
| `sit.rec@example.invalid` | Healthcare Administrator | REC | **no** | **no** | **no** | **no** | yes | 200, **0 items** | **403** |

All three accounts are enabled `System User` accounts with exactly one explicit
native role. `frappe.get_roles()` additionally returns the framework defaults
`All`, `Desk User` and `Guest`.

`Administrator` is authenticated but is refused by the clinic session policy:
the directory returns 403. The synthetic actors can log in and receive
`mode: "live"` bootstrap responses with roles `DOC`, `NUR` and `REC`
respectively.

### 3.1 Blocker: REC cannot register or read patients

The role map claims `REC → Healthcare Administrator`, but that role has no
`Patient` DocPerm on a fresh `healthcare` install. The directory endpoint
correctly returns an empty list for REC, and a real-Frappe create probe returned
403. This means:

- The first patient create/edit/view slice can be exercised as **DOC** and
  **NUR** only.
- Reception registration is **blocked** unless a reviewed decision changes the
  role mapping, grants the native permission, or assigns intake to a documented
  alternative actor. No permission or account change was made here; this
  requires separate approval.
- A green directory call for REC must not be read as access; its item count is
  zero.

## 4. Synthetic actors and fixture aliases

| Item | Verified value |
| --- | --- |
| Site | `marley-sit.local` |
| Workspace alias | `WS-SIT-1` |
| Allowlist entries | exactly 1 |
| Native Patient count | exactly 1 |
| Native Patient docname | equals `patient_name`; the identifier itself is withheld from this document |
| Fixture fields | `first_name`, `last_name`, `patient_name`, `sex`, `dob` and `status` exactly equal the constants in `deploy/local-sit/provision.py`; status is `Active` |
| Gender fixture | exactly `Female` |
| Non-system users | exactly 3 (the three actors above) |
| Auto-created Customer | exactly 1, named after the synthetic patient and linked |
| clinic_photos | present in the bench, **not installed** on the site |
| Apps | `frappe`, `erpnext`, `healthcare`, `clinic_ui` |

The allowlist native id was verified equal to the sole Patient docname without
printing it. The patient's display name can change on edit while the docname
stays fixed; the allowlist must therefore remain keyed to the docname, not to
the display name.

## 5. Isolation from legacy data and intent stores

### 5.1 Proved

- The site is a fresh `bench new-site` database on the new
  `marley-sit-db-data` volume. No legacy backup or data was imported.
- Running source services mount only `marley-sit-sites` and `marley-sit-logs`;
  there are **no host bind mounts** in any running source service (the one-shot
  provision service mounts only its own read-only script).
- The only published source port is `127.0.0.1:18081`; MariaDB, Redis and the
  websocket are unpublished.
- `FRAPPE_SITE_NAME_HEADER` is pinned to `marley-sit.local`; the site directory
  is the only site, and no `marley.local` directory exists.
- The source site configuration has no lab host name, lab origin, legacy URL or
  legacy intent path. It contains only its own DB/Redis settings and the
  `clinic_ui_*` keys.
- No repository code under `deploy/local-sit/` or `frappe-apps/clinic_ui/`
  references the lab host or port `18080`; the only occurrences are documentation
  and a negative assertion that `sites/marley.local` is absent.
- The legacy proxy is not running, and no `proxy/data` intent directory exists
  in this workspace. No legacy intent database is mounted into the SIT stack.
- The existing `127.0.0.1:18080` SSH tunnel remained listening under an `ssh`
  process throughout; no SIT command referenced or stopped it. The `litellm`
  Docker project was not touched.
- `python3 deploy/local-sit/bin/verify.py` passes **26/26** after the rehearsal
  target was recreated with names that do not match the `marley-sit-` filter.

### 5.2 Limits of the isolation claim

- Container network reachability to the host or the lab tunnel was not
  penetration-tested. The claim is configuration/mount/volume separation, not a
  network firewall proof.
- The legacy lab's remote intent store was not inspected (out of scope). Its
  absence locally is not evidence about its remote contents.

## 6. Legacy-intent continuity decision

**Decision: UNPROVEN — no continuity claim is made, and patient write gates
remain disabled.**

- Local SIT independence is proved for data, configuration, volumes, network
  and credentials.
- Legacy continuity is not proved. The remote lab intent store was not
  inspected, and a fresh site's independence does not establish that old
  unresolved intents do not exist or would not collide with a new operation.
- A local operation key can be scoped to this site without claiming legacy
  continuity, but that is a product decision for this synthetic site only. It
  must not be described as continuity with the legacy proxy or lab.
- Any future migration or shared use must separately preserve and check legacy
  intents. Introducing a new operation identifier is not a reset.

## 7. Backup and restore verification

### 7.1 Backup

Command, exit 0:

```bash
docker compose -p marley-sit -f deploy/local-sit/compose.yaml exec -T backend \
  bench --site marley-sit.local backup --with-files
```

Artifacts were copied to private state, mode 600, directory mode 700:

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| `...-database.sql.gz` | 1,116,784 | `90407a19b046affbaba2c77c23399b3655936c8478cc71e91beee73b70c8e1e8` |
| `...-files.tar` | 10,240 | `1524bc4fedfa3eebf60a59af4cedab7c3210969fc28e938080da8773cda5c50b` |
| `...-private-files.tar` | 10,240 | `83e2b12a828e3b2d0b5c2e97d2e2a6f7a47cd17c3adac1f95b2bf04c4b839c0d` |
| `...-site_config_backup.json` | 642 | `4a909b69e9cdafb79d2b69776580625022c33e6c06bbeb60eca8b979526a0097` |

`gzip -t` passed, both tar files listed successfully, the dump contains 881
`CREATE TABLE` statements, no `DEFINER=` clauses and no `marley.local` string.
The public/private file tars contain only empty directories on this site.

**Checksum re-verification.** The recorded digests were recomputed on
2026-11-14 with three independent paths: `sha256sum` on the source container
file, `shasum -a 256` on the private copy, and `openssl dgst -sha256` on the
private copy. All three agree:

| Artifact | Source container `sha256sum` | Private copy `shasum` / `openssl` |
| --- | --- | --- |
| public files tar | `1524bc4f…c50b` | `1524bc4f…c50b` |
| private files tar | `83e2b12a…c0d` | `83e2b12a…c0d` |

The original evidence contained a transcription error in the private-files digest. Independent hashing of the preserved backup and restore copies agrees with the private SHA256SUMS manifest; the evidence has been corrected. No artifact corruption was established.

The live `site_config.json` checksum equals the backup copy, and both are mode
600. The database dump contains no `clinic_ui_*` values because those live in
`site_config.json`, which is backed up separately.

### 7.2 Restore target

The restore used a genuinely separate target: Compose project `sit-rehearsal`,
network `sit-rehearsal_default`, volumes `sit-rehearsal-sites`,
`sit-rehearsal-logs` and `sit-rehearsal-db`, **no published ports**, and the
same digest-pinned MariaDB/Redis images plus the local backend tag. It shares
no volume, network or project with `marley-sit`.

Command, exit 0, final output `Site marley-sit.local has been restored with
files`:

```bash
docker compose -p sit-rehearsal -f "$STATE_DIR/restore/compose.yaml" \
  --env-file "$STATE_DIR/restore/restore.env" run --rm --no-deps backend \
  bench --site marley-sit.local restore <backup.sql.gz> \
    --with-public-files <files.tar> \
    --with-private-files <private-files.tar> \
    --mariadb-root-username root --mariadb-root-password "$RESTORE_DB_ROOT_PASSWORD" --force
```

Verified on the restored site before probe writes:

| Check | Result |
| --- | --- |
| Site and apps | `marley-sit.local`; `frappe`, `erpnext`, `healthcare`, `clinic_ui` |
| Versions | 16.54.0 / 16.52.1 / 16.6.1 / 0.0.1 |
| Patient / Gender / non-system users | 1 / 1 / 3 |
| Fixture fields | exact match to `provision.py`; Customer linked; no `user_id` |
| Effective Patient permissions | DOC read/create/write; NUR read/create/write; REC none |
| `clinic_ui_*` config | enabled, exact site, exact role map, 1 allowlist entry, 64-char epoch secret |
| Site config mode | 600 |
| Restored files | 0 public, 0 private (backup had only empty directories) |
| Anonymous bootstrap | 401 `AUTH_EXPIRED` |
| DOC / NUR | bootstrap 200 `live`; directory 1 item; `/clinic` 200; deep link 200 |
| REC | bootstrap 200 `live`; directory **0 items** |
| Administrator directory | 403 |
| Backend restart | `ping` returned `pong` |

The restored site therefore reproduces both the positive and the REC-denial
behavior of the source.

### 7.3 Restore procedure findings

1. **Database user host scope.** The restore process created the site DB user
   with a grant tied to the restoring container's IP. A later one-off container
   with a different IP received `Access denied`. An explicit
   `CREATE USER IF NOT EXISTS '<user>'@'%'` plus `GRANT` fixed the rehearsal
   target, after which a fresh container and a backend restart both connected.
   Any reusable restore procedure must include this step (or an equivalent
   stable-IP/`%` scope).
2. **Application cache.** After restore, Frappe's module map in the fresh Redis
   contained only `frappe` and `erpnext` until `apps.txt` was present and
   `bench --site marley-sit.local clear-cache` was run. Without it, a
   `has_permission` call raised `Module Healthcare not found`. The restore
   procedure must copy `apps.txt`/`apps.json` and clear the cache before
   verification.
3. **Image provenance.** The running source containers reference backend image
   ID `sha256:4a6a7cd0…`, which `docker image inspect` no longer resolves on
   this Docker host. The current `marley-sit/backend:16.6.1` tag has the same
   `/opt/sit/revisions.txt` (Frappe 16.54.0, ERPNext 16.52.1, Marley
   `dda107b…`, source `ef8b65a`) and identical hashes for the clinic_ui policy,
   adapter and manifest, so the restore used that tag. This anomaly predates
   Task 2 and did not modify the running stack; it should be checked before any
   future container recreation.

### 7.4 Restore fencing and recovery (required before any write gate)

The rehearsal proves that the site data, config and files can be restored. It
does **not** prove write recovery. The backup does not contain an independent
operation history, and a restored site may be older than the last committed
write. A missing receipt or Patient in a restored database is **not** proof that
the operation did not commit.

Required design before resuming writes:

1. **Independently preserved operation history.** Keep an append-only,
   private operation ledger and monotonic recovery epoch outside the site DB
   backup and outside the site's Docker volumes. It is not restored with
   `marley-sit-sites` or `marley-sit-db-data`; it has its own backup and
   retention. At minimum it records: operation key, site, actor, kind, target,
   payload digest, base version, dispatch state, terminal outcome, native
   receipt and the epoch under which each state was written.
2. **Write-ahead fence.** Persist a terminal-capable intent/fence before any
   native mutation. A delayed request must present the current epoch and pass
   the fence; an old-epoch request is rejected before mutation. A committed
   receipt and a terminal no-commit outcome are the only states that release
   the key; a timeout, current read, missing restored row or operator assertion
   does not.
3. **Restore quarantine.** On startup, compare the independently recorded
   epoch/sequence with the restored site. Any mismatch, any ledger operation
   with no corresponding site receipt, or any uncertainty enters
   `RESTORE_QUARANTINE`: all patient writes return a fixed 423/`QUARANTINE`
   response; reads may continue under normal authorization. Do not auto-retry,
   auto-unlock or recreate a Patient because the row is absent.
4. **Reconciliation.** For every ledger entry that is dispatched/unknown or
   newer than the restored site's known point, verify the authoritative
   ledger terminal outcome and the native record. A ledger-committed operation
   whose native record is absent is reported as `COMMITTED_BUT_MISSING`, not as
   non-commit; it stays locked. A ledger entry with no terminal outcome stays
   `UNKNOWN`. Only an operator-reviewed reconciliation may record a new epoch
   and re-enable writes.
5. **Delayed requests.** A delayed request from before the restore must not
   mutate the restored site. Enforce the epoch/fence at the start of the
   transaction and re-check it at commit; if it cannot be checked atomically,
   reject the request rather than guessing.
6. **Tests before resuming writes:** restore a backup taken before a committed
   create; restore a backup taken after commit but before the ledger receipt;
   restore with the ledger missing, older or newer; send a delayed old-epoch
   request; replay a committed key after restore; attempt a second create for
   the same key. Expected: quarantine on mismatch, no duplicate, no
   missing-receipt retry, no mutation from old epoch, and no write release
   without a reconciled terminal outcome. These tests are **not run** in Task 2.

Until this design and its real-Frappe tests exist, the current backup/restore
rehearsal supports **read/recovery evidence only**, not write resumption.

## 8. First patient create/edit/view plan

**No implementation is authorized by this plan.** Write gates remain disabled:
all 35 advertised capabilities stay `read_only`, and the native session parser
continues to coerce every capability to `read_only`.

### 8.1 API surface

Use authenticated Frappe methods in `clinic_ui`, same-origin, with the existing
gate order: no-store → session validity → site configuration → clinic role →
native record authority.

**Mechanism status:** §8.2 is source-verified against the installed Frappe
16.54.0 but **not executed**. There is no table, migration patch, endpoint or
test yet; writes remain disabled. If any cited source fact is disproved during
implementation, §8.2 is BLOCKED until re-verified.

| Operation | Proposed method | Body / query | Notes |
| --- | --- | --- | --- |
| Create | `POST clinic_ui.api.patients.create` | `{operation_key, first_name, last_name?, sex}` | No client native id, no alias, no status, no email/mobile/phone, no `invite_user`; response carries the server-generated alias |
| Update | `POST clinic_ui.api.patients.update` | `{operation_key, workspace_patient_id, base_modified, first_name?, last_name?, sex?}` | Alias only; server maps alias to native docname; partial fields allowed but every supplied field is whitelisted |
| View | `GET clinic_ui.api.patients.view` | `?workspace_patient_id=...` | Alias only; returns display name, first/last/sex/dob/status; no native id, no identifiers |

Common rules:

- Strict input validation; unknown keys rejected (422). Trim names; reject
  control characters; enforce 1–140 characters; `sex` must exist in the live
  `Gender` list.
- Re-resolve the session user on every request; never accept a role or actor
  from the client. No `ignore_permissions` anywhere.
- `workspace_patient_id` must resolve through the server-owned allowlist; the
  client never supplies a native docname.
- All responses `Cache-Control: no-store`. Anonymous is 401 for bootstrap only;
  all other failures are the framework's 403, and validation is 422.
- Do not return native docnames in create/update/view DTOs. The existing
  directory DTO still exposes `native_patient_id`; that exposure should be
  reviewed separately before any client code starts using it for writes.

### 8.2 Server-only persistence mechanism (source-verified, not executed)

**Mechanism status: SOURCE-VERIFIED against installed Frappe 16.54.0 at
`/home/frappe/frappe-bench/apps/frappe`; NOT EXECUTED.** No write, migration,
patch or HTTP call was run. If implementation finds any cited fact false, this
section is **BLOCKED** until re-verified; do not substitute a DocType or an
`ignore_permissions` path.

#### 8.2.1 Storage: two raw MariaDB tables, no DocType

| Table | Purpose | Columns |
| --- | --- | --- |
| `clinic_ui_patient_alias` | durable workspace alias ↔ native Patient mapping | `workspace_patient_id` VARCHAR(64) PK, `site` VARCHAR(140), `native_patient_id` VARCHAR(140), `operation_key` VARCHAR(128), `created_by` VARCHAR(140), `creation` DATETIME(6); unique `(site, native_patient_id)`, unique `(site, operation_key)` |
| `clinic_ui_operation` | server-side idempotency record and committed receipt | `site`, `actor`, `kind`, `operation_key`, `payload_digest`, `base_modified`, `state`, `workspace_patient_id`, `native_patient_id`, `receipt_modified`, `creation`, `modified`; PK `(site, actor, kind, operation_key)` |

The tables are created once by an app migration patch (`patches.txt` entry) that
calls `frappe.db.sql_ddl` — verified to commit and execute DDL
(`frappe/database/database.py:451-458`); `patches.txt` is the app patch source
(`frappe/modules/patch_handler.py:113-125`). They are never created, altered or
dropped in a request.

No DocType is created for them. Each consequence was verified in source:

- **No DocPerm, no meta.** `Meta(doctype)` loads a `DocType` document and raises
  `DoesNotExistError` for an unknown name
  (`frappe/model/meta.py:145-160`).
- **No `/api/resource` route.** The v1 route table is exactly seven rules
  (`frappe/api/v1.py:146-152`). Every handler must resolve a DocType:
  `document_list` → `frappe.client.get_list` → `frappe.get_list` →
  `DatabaseQuery`/`QueryBuilder`, which needs meta for permission and field
  handling; `create_doc` → `frappe.new_doc` → `get_new_doc` → `make_new_doc` →
  `frappe.get_doc({...})`; `read_doc`/`update_doc`/`delete_doc`/
  `execute_doc_method` → `frappe.get_doc` → meta. `Meta(doctype)` raises
  `DoesNotExistError` for an unknown name. The exact failing call differs by
  path, so test 22 must pin the status; the invariant is that an unknown name
  never becomes a table read.
- **No `frappe.client` path.** `get_list`, `get_doc`, `insert`, `set_value` and
  `delete` resolve a DocType/meta first (`frappe/client.py:27-136`, `221-300`).
  `get_value` checks `frappe.has_permission` first (`frappe/client.py:135`),
  which resolves meta for any non-Administrator; for Administrator it falls
  through to `frappe.qb`, which maps the name to `tabclinic_ui_operation` — a
  table that does not exist. Either way no row is returned.
- **Generic doctype strings become `tab<name>`.** `Base.DocType` calls
  `get_table_name`, which returns `tab<name>` unless the name starts with `__`
  (`frappe/query_builder/builder.py:49-52`;
  `frappe/utils/__init__.py:1043-1049`). The ledger tables are deliberately
  named **without** the `tab` prefix, so even a path that skipped meta would
  query a non-existent `tabclinic_ui_operation`.
- **Only raw SQL reaches them.** `frappe.db.sql(query, values)` executes with
  `auto_commit=0` by default (`frappe/database/database.py:183-200`), so a
  parameterized `insert`/`select`/`update` on the literal table name is the only
  code path to them.

#### 8.2.2 Permission bypass: none, and strictly scoped

- The design calls **no** `ignore_permissions` anywhere. `Document.insert` and
  `_save` assign `self.flags.ignore_permissions` only when the argument is
  supplied (`frappe/model/document.py:700-701`, `810-811`); with no argument the
  flag stays unset and `Document.has_permission` delegates to
  `frappe.permissions.has_permission` (`document.py:639-650`). `_save` also
  calls `self.check_permission("write", "save")` (`document.py:819`).
- **Patient authorization is checked first**, before any alias or ledger read or
  write: `frappe.has_permission("Patient", "create")` for create, and
  `frappe.has_permission("Patient", "write", doc=patient)` for update. Note that
  `frappe.get_doc("Patient", name)` does **not** check permission by default
  (`document.py:391-404`), so the explicit check is mandatory. The subsequent
  `insert()`/`save()` re-check at the framework seam.
- The raw-table writes bypass DocPerm **by construction**, because they execute
  as the site DB user. That bypass is scoped to exactly these two server-owned
  tables. It never applies to `Patient`, never changes a role or permission, and
  is not a runtime flag that could be reused for another doctype. There is no
  "server capability" object that can hand out Patient authority.
- **Administrator is not a ledger client.** `frappe.permissions.has_permission`
  returns True for `Administrator` before role checks
  (`frappe/permissions.py:108-109`), but no route or client method can resolve a
  raw table: every path still needs DocType meta or builds `tab<name>`. Test 22
  must prove this for Administrator explicitly. A person with bench console or DB
  credentials is outside ordinary-user CRUD and outside this claim.
- Controller `has_permission` hooks can only deny, never grant
  (`frappe/permissions.py:483-501`); none is registered for these tables.

#### 8.2.3 Alias mapping

The static `clinic_ui_synthetic_patients` config is adequate for the seeded
fixture but not for creates. `clinic_ui_patient_alias` is the durable,
server-owned mapping. `workspace_patient_id` is generated server-side (immutable,
unique) and never accepted from the create client; `native_patient_id` is
unique per site and holds the Patient docname.

The alias row is inserted in the **same Frappe transaction** as the Patient and
the committed operation row. If any of the three fails, all roll back; there is
no Patient without alias/receipt and no alias without Patient. The seeded
`WS-SIT-1` fixture is migrated into this table by a reviewed seed step;
directory/view/edit resolve aliases through this table, never through a
client-supplied native id. Renaming a Patient changes `patient_name`, never the
alias or docname. A replay of the same operation key returns the same alias and
Patient; a concurrent different operation gets a different alias; an alias
collision is retried server-side or fails closed, not exposed to the client.

#### 8.2.4 Write path and transaction atomicity

Create — one request, one transaction:

1. Gate order: no-store → session validity → site configuration → clinic role
   (`endpoints/common.py`).
2. Validate the strict body; derive `site` and `actor` server-side from the
   session.
3. **Patient authorization first:**
   `frappe.has_permission("Patient", "create")`. False → generic 403 before any
   alias or ledger access.
4. `frappe.db.savepoint("clinic_ui_op")`
   (`frappe/database/database.py:1219-1223`).
5. Claim the key by inserting the `clinic_ui_operation` row with `state='pending'`
   through parameterized `frappe.db.sql`. The PK `(site, actor, kind,
   operation_key)` serializes duplicates. On duplicate key: roll back to the
   savepoint, read the existing row, and either return its committed
   receipt/alias (same `payload_digest`), return 409 (different digest), or
   return a lock/quarantine state (`pending`/unknown).
6. `frappe.new_doc("Patient", ...).insert()` — **no** `ignore_permissions`
   argument, so create permission and validations run.
7. Parameterized `insert into clinic_ui_patient_alias (...)` and
   `update clinic_ui_operation set state='committed', native_patient_id=%s,
   workspace_patient_id=%s, receipt_modified=%s ...` with server values only.
8. `frappe.db.commit()` exactly once (`frappe/database/database.py:1176-1190`).
   The framework would also commit unsafe methods at request end
   (`frappe/app.py:441-446`); the explicit commit fixes the boundary. Nothing
   that writes runs after it; a post-commit response failure is recoverable by
   replaying the key.
9. Any handled error: `frappe.db.rollback(save_point="clinic_ui_op")`
   (`database.py:1196-1214`) and raise a mapped error. An unhandled error is
   rolled back by the framework (`frappe/app.py:150-158`), so no partial
   Patient, alias or receipt survives.

Update uses the same order with `write` authority: resolve the alias row →
`frappe.get_doc("Patient", native_id)` → `frappe.has_permission("Patient",
"write", doc=patient)` → savepoint → `select ... for update` the Patient row →
compare `modified` with `base_modified` → apply whitelisted fields →
`doc.save()` with no `ignore_permissions` → write the committed operation row →
commit once. The alias row is not rewritten.

The auto-created Customer and any Contact are inside the same transaction, so a
rollback removes them too. Client-supplied `actor`, `site`, `state`, `receipt`,
`workspace_patient_id` or `native_patient_id` never overrides a server value.
The independent fence ledger from §7.4 remains a separate store and is **not**
claimed to be atomic with this transaction.

### 8.3 Native operation record and transaction

The site-side operation record is the raw `clinic_ui_operation` table defined in
§8.2.1; it has no DocType and therefore no DocPerm or list permission. Fields:

- `operation_key` (client-generated, high entropy, survives reload)
- `kind` (`create` / `update`)
- `actor` (native session user, server-derived)
- `site` (server-derived)
- `target` (empty for create; native docname for update)
- `workspace_patient_id` (server-generated; immutable)
- `payload_digest` (keyed digest of the canonical whitelisted payload)
- `base_modified` (update only)
- `state` (`pending` / `committed` / `conflict` / `unknown`)
- `native_patient_id` (native docname for a committed create/update)
- `receipt_modified` and a bounded audit timestamp

Uniqueness: `(site, actor, kind, operation_key)` unique. The **site-side**
operation record/receipt and the Patient mutation (plus alias mapping) commit in
the same Frappe transaction. The independent fence ledger from §7.4 is a second
store and cannot share that transaction; it is the recovery authority, not a
duplicate of the site receipt.

Two-store order:

1. Durably claim the operation key in the independent ledger (`DISPATCHED`).
2. In one Frappe transaction: insert the `pending` `clinic_ui_operation` row
   (site-side key claim), insert the Patient, insert the alias mapping, mark the
   operation row `committed` with the native receipt, commit once.
3. Append the terminal outcome (`COMMITTED` with the native receipt, or
   `NO_COMMIT`) to the independent ledger.

A crash after step 2 and before step 3 leaves the ledger `DISPATCHED`; the
operation is **UNKNOWN**, not retryable and not a non-commit. A crash before
step 2 leaves no site row but the ledger still says `DISPATCHED`; reconciliation
must establish a terminal no-commit before release. If the ledger itself is
unavailable or fails integrity checks, fail closed. Do not use the absence of a
site row or restored receipt as proof of non-commit.

The site-side unique key plus the Patient row lock make a delayed execution and
a retry mutually exclusive at the native transaction. The independent ledger
adds the pre-dispatch/terminal fence across restores. No separate pre-dispatch
no-commit fence is needed inside the Frappe transaction because the mutation and
its site receipt share it; `payload_digest` uses a server-owned key from site
configuration, never logged or returned.

- create (step 2): insert the `pending` operation row (the site-side key claim),
  insert the Patient, insert the alias mapping, mark the operation row
  `committed` with the native receipt, commit once. Any failure rolls back all
  four writes.
- update (step 2): `SELECT ... FOR UPDATE` the site operation row and the Patient
  row, compare `modified` with `base_modified`, apply whitelisted fields, mark
  the operation row `committed`, commit once. The alias mapping is not rewritten.
- A duplicate operation key is decided by the independent ledger first: if it is
  `COMMITTED`, read the site receipt/alias and return it; if it is
  `DISPATCHED`/`UNKNOWN`, return a lock/quarantine response and do not retry; if
  it is terminal `NO_COMMIT`, the key may be claimed again. The site-side
  `pending` insert is the second fence: a concurrent duplicate fails on the PK
  and rolls back to its savepoint. A payload mismatch returns 409 without
  mutation.
- No separate pre-dispatch HTTP request exists inside the site; the native
  method and site transaction are in one process. A lost response after the site
  commit is recoverable from the site receipt **and** the ledger terminal record;
  a crash before the site commit rolls back the site mutation, while the ledger
  remains `DISPATCHED` until reconciled.

Do not infer non-commit from a timeout, a current read, a 4xx/5xx, a missing
restored row or an operator assertion. A missing site row is not proof: the
independent ledger decides. If the ledger says `DISPATCHED`/`UNKNOWN`, or the
ledger is unavailable/corrupt, lock and escalate; only a terminal `NO_COMMIT`
releases the key. An operation row whose receipt is missing or inconsistent
indicates corruption or a restore mismatch, not a safe retry.

Create side effects must be inside the same transaction: the auto-created
Customer and any Contact. Block create if `send_registration_msg` becomes 1
(external SMS cannot be rolled back), if `collect_registration_fee` changes the
post-insert status path, or if an input would set `email`/`invite_user` and
create a Website User or send email. The first slice sends neither email nor
SMS.

### 8.4 Idempotency, receipt and conflict semantics

| Situation | Required result |
| --- | --- |
| Same key, same payload, already committed | Return the stored receipt, alias and same patient; no second Patient and no second alias |
| Same key, different payload | 409 `OPERATION_CONFLICT`; no mutation |
| Same key concurrently | One ledger claim/transaction wins; the other reads the committed receipt or returns a lock/quarantine; exactly one Patient and one alias |
| Same key from a different actor | Treated as that actor's own operation; never reveals the first actor's receipt, alias or patient |
| Commit succeeded, response lost | Retry with the same key returns the receipt and alias; no duplicate |
| Crash/failure in the site transaction | Roll back Patient, Customer/Contact, alias, `clinic_ui_operation` row and receipt together; the independent ledger remains `DISPATCHED` and the retry stays blocked until a terminal `NO_COMMIT` is established |
| Missing restored site receipt, ledger `COMMITTED` | `COMMITTED_BUT_MISSING`; quarantine; do not recreate, retry or infer non-commit |
| Missing restored site receipt, ledger `DISPATCHED`/`UNKNOWN` | Quarantine; no retry or unlock until reconciliation |
| Missing restored site receipt, ledger terminal `NO_COMMIT` | Key may be claimed again under the current epoch |
| Ledger unavailable, corrupt or epoch/sequence mismatch | Fail closed / `RESTORE_QUARANTINE`; no writes |
| Operation row/receipt inconsistent (corruption or restore mismatch) | Treat as `UNKNOWN`; lock and escalate; no automatic retry or unlock |
| Update with stale `base_modified` | 409 `STALE_VERSION`; no fields changed, later edit preserved |
| Concurrent updates at the same base | One commits; the other 409s; no lost update |
| Rename | `patient_name` recomputes; docname and workspace alias remain stable; response reports the new display name |
| Status / patient_name / customer / user_id / invite_user supplied | 422; no write, because native REST would otherwise accept them |

There is no verified business duplicate rule for two different operation keys
with the same demographics. Do not invent one. Report duplicates as a separate
product decision; the legacy SIT case `SIT-PAT-02` remains blocked until that
rule and the permission issue are resolved.

### 8.5 Client behavior

- Keep `write.patient.create` and `write.patient.update` as `read_only` until
  every test in §8.6 passes against real Frappe and is independently reviewed.
- When enabled later, persist the operation key across reloads for the same
  site, actor, epoch and payload. The legacy in-memory key map loses the key on
  reload and can duplicate a patient.
- Use the server-generated workspace alias from the create response and for all
  later view/edit calls; never send or store a native docname as a write input.
- Populate `sex` from the native Gender list (currently only `Female`).
- On `DISPATCHED`/`UNKNOWN`/quarantine, show a locked/needs-review state; retain
  the key; never re-key, auto-retry, recreate a missing row or claim success. On
  conflict, show the changed record and require an explicit reload/merge; do not
  silently overwrite.
- Do not route native writes through the legacy proxy. Do not enable a legacy
  fallback when a native capability is unavailable.

### 8.6 Required tests (real Frappe, isolated site)

Run on the restored rehearsal copy or another approved disposable target, never
the source stack, and record UI plus independent backend evidence. **None of
these tests has been run**; the §8.2 mechanism has not been implemented.

**Unit / contract (no Frappe):** strict body schema; unknown-field rejection;
allowlist alias resolution; no native id in DTO; capability remains `read_only`;
error mapping distinguishes 401/403/409/422/unknown.

**Real Frappe integration:**

1. DOC create (`first_name`, optional `last_name`, `sex= Female`) → 200; one
   Patient; one committed receipt; **one server-generated alias mapping
   atomically present**; Customer linked; no Contact, Website User, SMS or
   email; response display name; status `Active`.
2. NUR create → same as DOC.
3. REC create → 403; no Patient, alias or receipt.
4. Missing/incorrect CSRF → 400; no mutation.
5. Supplied `status`, `patient_name`, `customer`, `user_id`, `invite_user`,
   `email`, `naming_series` or an unknown key → 422; no mutation.
6. Missing `first_name`, missing `sex`, invalid `sex`, future `dob` → 422/417;
   no mutation.
7. Same operation key and payload twice → one Patient, one receipt, **same
   alias**, same response.
8. Same operation key with a changed payload → 409; no mutation.
9. Two concurrent requests with the same key → exactly one Patient, one
   committed receipt and one alias; the loser does not mutate.
10. Two concurrent creates with different keys → two Patients and two distinct
    aliases; no alias collision or reuse.
11. Commit followed by a dropped response (fault harness) → retry with the same
    key returns the receipt and alias; no duplicate and no re-key.
12. Process crash at each transaction boundary → no Patient without alias and
    receipt, no alias without Patient, no receipt without Patient; retry is safe
    or remains locked.
13. Alias or operation-row insert/update failure after Patient insert → whole
    transaction rolls back; no Patient, alias, Customer or Contact remains.
14. Directory after create → the new alias appears for DOC/NUR; REC remains 0
    items; the display name is current.
15. View by alias → 200 with only the whitelisted fields; native id absent.
16. Edit by alias → 200; alias and docname unchanged; display name updates;
    receipt committed. A later view by the same alias returns the new display.
17. Update with the correct `base_modified` → 200; `patient_name` updates;
    docname and alias unchanged; receipt committed.
18. Update with a stale `base_modified` → 409; no fields changed; a later shared
    edit survives.
19. Two concurrent updates at the same base → one 200 and one 409; no lost
    update.
20. Update `status` or any rejected field → 422; native `status` remains
    unchanged (this is the guard against the verified REST behavior).
21. Direct native docname supplied in create/view/edit input → 422/403; the
    server resolves only the alias mapping. REC view/edit → 403; anonymous →
    401; no native id in any response.
22. **Direct API denial (raw tables):** for each of DOC, NUR, REC,
    Administrator and System Manager, call
    `/api/resource/clinic_ui_operation`, `/api/resource/clinic_ui_patient_alias`,
    `frappe.client.get_list`, `get_doc`, `get_value`, `insert`, `set_value` and
    `delete` with those names. Expect a DocType-resolution failure (404 /
    `DoesNotExistError`) or a generic denial, no row, no count and no field
    leak. Also confirm `frappe.qb` maps any doctype string to `tab<name>`, so
    `tabclinic_ui_operation` is the only name those paths could reach and it
    does not exist.
23. **Ledger authority:** only the clinic method may write `clinic_ui_operation`
    and `clinic_ui_patient_alias`; a request outside it cannot insert, update,
    delete or mark a receipt. A client-supplied `actor`, `site`, `state`,
    `receipt`, `workspace_patient_id` or `native_patient_id` is rejected or
    ignored. No code path passes `ignore_permissions=True`.
24. **Cross-actor receipt denial:** actor B using actor A's operation key cannot
    read A's receipt, alias, Patient or state; B's create is either its own
    distinct operation or denied, never a disclosure. Replay only returns the
    current actor's own receipt, because the ledger key includes `actor` and the
    query filters on the server-derived actor.
25. **Patient authorization ordering:** a user with no Patient `create`/`write`
    permission calls the clinic method with a well-formed body. Expect 403 and
    **zero** `clinic_ui_operation` and `clinic_ui_patient_alias` rows, proving
    the Patient check runs before any ledger/alias access. Also: after alias
    resolution and even with a valid receipt, the current actor's native
    `has_permission` is re-checked on the exact Patient for read/write; REC is
    denied; a permission revoked mid-session is denied; a missing/unauthorized
    Patient returns a generic 403 or is omitted, with no cross-patient
    disclosure.
26. Revoked/expired session or changed role mid-flight → no mutation, no lock
    release, no data disclosure.
27. **Restore quarantine:** restore a backup taken before a committed create;
    the independent ledger has the receipt while the restored site does not.
    Expect quarantine, `COMMITTED_BUT_MISSING` for that operation, no automatic
    recreate, no retry, and all writes refused.
28. Restore a backup taken after commit but before the ledger receipt is
    terminal. Expect quarantine/`UNKNOWN`; reconciliation does not infer
    non-commit from the missing restored receipt and does not create a duplicate.
29. Restore with the ledger missing, older or newer than the site (separate
    restore points). Expect quarantine on mismatch; no write release without
    operator reconciliation and a new epoch.
30. Send a delayed request carrying the pre-restore epoch during quarantine and
    after it is lifted. Expect rejection before mutation; the restored site is
    unchanged.
31. Replay a ledger-committed key after reconciliation. Expect the original
    receipt/alias and no second Patient; a missing restored row is still not
    proof of non-commit.
32. After a restored older DB, a committed key must not create a second Patient;
    an unknown key remains locked until terminal evidence exists.
33. Delayed retry after a long pause → receipt returned; no duplicate.
34. Missing site receipt with ledger terminal `NO_COMMIT` → retry with the same
    key creates exactly once; no stale lock.
35. Ledger unavailable, corrupt or sequence-mismatched → writes fail closed in
    quarantine; reads remain authorized but no mutation occurs.

**Failure to pass any of tests 3–5, 7–13, 18–20, 22–35 keeps writes disabled.**
The restore/quarantine tests (27–35) must pass against real Frappe before writes
are resumed; this assessment did not run them.

### 8.7 Open decisions before implementation

1. **REC access.** Grant/mapping change or restrict reception registration to
   DOC/NUR. Requires separate approval; do not grant permissions to make a test
   pass.
2. **Duplicate policy.** Define an approved demographic duplicate rule, or
   explicitly accept duplicate patient creation distinguished only by operation
   key.
3. **Directory native-id exposure.** Decide whether `native_patient_id` remains
   in the directory DTO; it must not become a write input.
4. **Email/invite_user.** Keep email out of the first slice or require an
   approved, transaction-safe side-effect design.
5. **Image/recreate procedure.** Resolve the missing backend image ID before a
   future `up`/recreate can change the deployed backend.
6. **Legacy continuity.** Remains unproven; see §6.
7. **Restore procedure.** Include the `%` DB grant, `apps.txt`/`apps.json`, and
   cache clear in any reusable runbook.
8. **Independent fence ledger.** Decide its storage location, independent
   backup/retention, write protocol and operator authority. It must not be
   restored with the site volumes; it must be able to detect an older site.
9. **Recovery epoch and quarantine owner.** Define who increments the epoch,
   who may reconcile, and how a quarantine is cleared with terminal evidence.
   Reads may continue; writes stay refused until then.
10. **Alias allocation and seed migration.** Approve the server-generated alias
    format, uniqueness/retry rule, and migration of the existing `WS-SIT-1`
    fixture into the durable mapping. Decide whether the static allowlist is
    removed or retained only as a seed.
11. **Operation-ledger storage approval.** Approve the exact table names
    (`clinic_ui_operation`, `clinic_ui_patient_alias`), the deliberately
    unprefixed naming (so generic Frappe doctype paths map to a non-existent
    `tab…` table), the one-time `patches.txt` DDL patch, retention/audit needs,
    and the site DB grant used by the clinic method. The mechanism itself is
    source-verified in §8.2; it is not executed or migration-tested.

## 9. Unapproved deletion disclosure (review defect)

**This is a deviation from the explicit instruction not to delete Docker
resources or records. It was not separately approved, and nothing in this
document implies approval.**

Two deletion actions occurred during the rehearsal:

| Action | Exact target | Effect | Approval |
| --- | --- | --- | --- |
| `frappe.delete_doc(..., force=1, ignore_permissions=True)` cleanup | 3 probe Patients in `sit-rehearsal`: `SITEDIT Probe`, `SITEDIT Probe - 1`, `SITEDIT NurseProbe` | Deleted the probe Patients; the loop also deleted any linked Contact and the auto-created Customer for each probe | **Not approved** |
| `docker compose -p marley-sit-restore ... down -v` | First rehearsal project `marley-sit-restore`: containers `backend`, `db`, `redis-cache`, `redis-queue`; volumes `marley-sit-restore-sites`, `marley-sit-restore-logs`, `marley-sit-restore-db`; network `marley-sit-restore_default` | Destroyed the entire first restored target, including its site DB, site files, logs and all records in that target; 2 earlier probe Patients had already been deleted by the same cleanup method before the volume removal | **Not approved** |

The review's count of 3 probe Patients refers to the final `sit-rehearsal`
target. The first target also held 2 probe Patients, so the total deleted probe
Patients is **5** (2 + 3), plus everything else destroyed with the first
target's volumes. The 2 first-target records and any audit/linkage rows in that
volume are not recoverable.

**Uncertainty about linked data and audit.** Each probe create auto-created a
`Customer`; the cleanup deleted the probe Customers, and the final counts showed
only the fixture Customer and the three pre-existing user Contacts. No probe
Contact was expected because the probes supplied no email/mobile/phone, and the
cleanup removed any linked Contact it found. However, the exact Customer rows,
Contact rows, Dynamic Links and any Frappe deletion/audit records created before
or during deletion cannot be enumerated after the fact. The first target's
volume destruction makes its linked/audit state unknowable. The private original
backup remains intact and can recreate the base fixture site, but it cannot
restore the deleted probe rows or later audit data.

**No source, lab or other project resource was deleted.** The source
`marley-sit` stack and its four volumes, the `litellm` project and the
`127.0.0.1:18080` tunnel were not deleted. The remaining `sit-rehearsal`
containers and volumes are retained; no further deletion was performed after
this review. Any future deletion requires explicit, separate approval.

## 10. What this assessment does not establish

- No lab, production or real patient access.
- No browser SIT; all HTTP checks were API-level.
- No fault-injection or concurrency tests were run in Task 2. The plan above
  defines them; they must run before any write gate is enabled.
- No independent operation ledger, recovery epoch, restore quarantine or
  reconciliation mechanism exists yet. The rehearsal is not write-recovery
  evidence, and a missing restored receipt was not treated as non-commit.
- No durable workspace alias mapping exists yet; the static allowlist is the
  current fixture source only.
- The §8.2 raw-table mechanism is source-verified, not executed: no
  `clinic_ui_operation`/`clinic_ui_patient_alias` table, migration patch,
  endpoint, denial test or cross-actor test exists.
- No server-only ledger authority or direct-CRUD/cross-actor denial tests have
  been implemented or run.
- No claim that the REC denial is a product defect or that it should be fixed
  by changing permissions; it is a verified mismatch requiring a decision.
- No claim that the restore target is a production backup/restore procedure;
  it is an isolated rehearsal with the caveats in §7.3.
- No secret, password, CSRF token, session id, epoch secret, native patient
  identifier or private path content is recorded here.
