# Clinic lab role setup + verification (local preparation only)

Status: **executed 2026-11-13** on the isolated `clinic-photo-accept.local`
stack only. Five Roles and seven synthetic Users created in one transaction; real
HTTP login and private-file checks passed. See "Execution record" below.

## Approved shape

Five new Role records: `Clinic Lab Reception`, `Clinic Lab Pharmacy`,
`Clinic Lab Cashier`, `Clinic Lab Manager`, `Clinic Lab Administrator`.
No DocPerm, Custom DocPerm, User Permission, Role Profile or settings row is added.
`Physician` and `Nursing User` are reused unchanged and must already exist.

Seven fresh synthetic `System User` accounts, one exact role each, generated per run
nonce as `lab.<nonce>.<actor>@clinic-photo-accept.test`, `send_welcome_email=0`,
`flags.no_welcome_mail`. No `Administrator` / `System Manager` grant anywhere.

## Code

- `frappe-apps/clinic_photos/clinic_photos/tests/lab_roles.py` — plan, conflicts,
  private artifacts, read-only gateway, CLI.
- `frappe-apps/clinic_photos/tests/unit/test_lab_roles.py` — 18 offline tests.

## Procedure

1. **Read-only inventory** (parent, outside this code): site, existing `Role` names,
   existing `User` names. Save as `/private/lab/inventory.json`.
2. **Plan** (local artifacts only, still no site mutation):

   ```bash
   cd frappe-apps/clinic_photos
   python3 -m clinic_photos.tests.lab_roles plan \
     --inventory /private/lab/inventory.json --control-dir /private/lab \
     --run-nonce <16+ lowercase hex> --live
   ```

   Writes `plan.json` and `credentials.json`, both `0600`, into a `0700` directory
   that must be outside the app and outside any git repository. Refuses: wrong site,
   nonce clash or nonce not hex, any of the five roles already present, missing
   `Physician`/`Nursing User`, any planned email already present, forbidden role,
   non-`Role`/`User` operation. Credentials values are never printed.
3. **Review** `plan.json`: exactly 5 Role inserts and 7 User payloads with one role,
   `user_type: System User`, `send_welcome_email: 0`.
4. **Apply** (`apply` mode, one transaction, aborts on any collision; no cleanup):

   ```bash
   cd BENCH/sites
   PYTHONPATH=../apps/clinic_photos python3 -B -m clinic_photos.tests.lab_roles apply \
     --site clinic-photo-accept.local --control-dir /private/lab --live
   ```

   Writes `apply.json` (counts only) and prints
   `apply committed roles=5 users=7 docperm=0 custom_docperm=0 user_permission=0`.
   The five Roles carry no `DocPerm`, `Custom DocPerm` or `User Permission`; the
   seven Users each carry exactly one assigned role and no `Administrator` /
   `System Manager`. `new_password` is set from the private credentials file, so
   no welcome mail is produced (`send_welcome_email=0`,
   `flags.no_welcome_mail`).

   Async note: `User.on_update` enqueues `create_contact` with
   `enqueue_after_commit=True`, so the commit adds one pending job per new User
   to the site `default` queue. Nothing executes or clears those jobs here.
5. **Verify** (no data mutation; login writes session metadata; requires `--live`):

   ```bash
   cd BENCH/sites
   PYTHONPATH=../apps/clinic_photos python3 -B -m clinic_photos.tests.lab_roles verify \
     --site clinic-photo-accept.local --control-dir /private/lab \
     --photo-file <registered protected photo File> \
     --private-file <distinct is_private=1 File> \
     --transport http --base-url http://frontend:8080 \
     --host clinic-photo-accept.local --live
   ```

   `--photo-file` and `--private-file` are independent targets: `read_photo`
   (metadata) and `list_photo` use the photo File / its `Clinic Photo` record,
   while `read_private` fetches the bytes of the `--private-file` URL only.
   `--transport http` performs a genuine `POST /api/method/login` per account and
   reuses the session cookie for those requests; `--transport inprocess`
   (default) keeps the hash-only checks and no HTTP request. Write is evaluated
   with `has_permission` and is never sent, so no photo or draft is mutated.
   Per account it also asserts exactly one assigned role, zero `DocPerm` /
   `Custom DocPerm` for the five new roles, zero `User Permission` and
   `send_welcome_email` unset.

   Side effects of the HTTP login (not a pure read): Frappe inserts a `Sessions`
   row, updates `User.last_login` / `last_ip` / `last_active`, writes a login
   `Activity Log` row, and commits that session transaction. It sends no mail and
   enqueues nothing. Credentials, email addresses and File IDs are never printed.

## Limitations

- Offline tests prove plan/matrix/routing logic only; the `--live` run proves
  installed permissions.
- Built-in `File` grants (for example `All`) are untouched: this makes **no** claim
  that the five roles can read no file rows. It covers the named photo file, the
  named private file and the named accounts only.
- The photo must be registered by a `Clinic Photo` / annotation record or the run
  refuses before evaluating anything.
- No cleanup, rollback, delete or compensation exists anywhere; residue inspection
  is the parent's.

## Execution record (2026-11-13)

- Site identity checked before mutation: `clinic-photo-accept.local`,
  db `clinic_photo_accept`. Pre-state: 54 Roles (Physician and Nursing User
  present, no `Clinic Lab*`), 14 Users, 1708 `DocPerm`, 7 `Custom DocPerm`, 0
  `User Permission`, 3 `Clinic Photo`, 5 `File` (3 private, all attached to
  `Clinic Photo`; 0 non-photo private), 0 `Clinic Photo Draft`.
- DB+files backup taken to the site `private/backups` directory before any write.
- `apply` committed 5 Roles and 7 Users in one transaction. Post-state: 59 Roles,
  21 Users, 1708 `DocPerm`, 7 `Custom DocPerm`, 0 `User Permission`, 0
  `Email Queue Recipient` for the new accounts, 0 of them with
  `send_welcome_email=1`, 3 `Clinic Photo` / 5 `File` / 0 Draft (unchanged).
  Each new User has exactly one assigned role; none has `Administrator` or
  `System Manager`.
- `verify --transport http` result, run twice on the two private photo Files with
  the targets swapped (A: photo=F1/private=F2, B: photo=F2/private=F1): both runs
  `login=7/7`, `read_photo=2/8`, `read_private=2/8`, `list_photo=2/8`,
  `write_photo=0/8` (8 = seven accounts + Guest). Physician and Nursing User
  allowed; the five lab roles and Guest denied. `write_photo` is `has_permission`
  only — no HTTP write request was sent.
- Login side effects observed since the baseline taken after the first pass:
  `Sessions` 76 -> 118 and `Activity Log` 137 -> 179 (+7 per pass over six
  further passes), `User.last_login` set for all seven accounts. Queue unchanged
  at 19 default / 57 long, `Email Queue Recipient` for the lab accounts still 0,
  `Clinic Photo` 3 / `File` 5 / Draft 0 unchanged, Roles 59 / Users 21 /
  DocPerm 1708 / Custom DocPerm 7 / User Permission 0 unchanged.
- Queue: site `default` 12 -> 19 pending jobs (the seven `create_contact`
  enqueues), `long` 57 -> 57. No job was executed, retried or cleared.
- Inherited rights, not zero: `File` has a `DocPerm` for role `All`; for public
  files that is a real read grant, so the five lab roles can read public files.
  Private files are gated by `File.has_permission`, which defers to the attached
  document, so these roles get nothing for the clinic photo files. Physician and
  Nursing User retain their pre-existing broad rights (Clinic Photo and
  Annotation Version read, Draft create, owner-only Draft write); none of that
  was changed.
- Limitations: only observed claims. Evidence covers the two named private photo
  `File` rows, their `Clinic Photo` records and the seven named accounts; it says
  nothing about other files or other users. No distinct non-photo private `File`
  exists (0), so private-bytes was proven on two private photo `File` rows with
  the photo/private roles swapped; creating another File was outside the approved
  mutations. No `Clinic Photo Draft` rows exist, so author-only draft write was
  verified only as "permissions unchanged" (same four `DocPerm` rows), not
  exercised on a record. Guest has no login test by design (anonymous requests).
  Sessions and `Activity Log` rows from the logins are retained; no cleanup.
- Evidence paths: private run artifacts live in
  `/Users/sunphunkosol/.clinic-photo-accept-lab/` (`run/credentials.json`,
  `run/plan.json` 0600; `apply.json`, `verify_a.txt`, `verify_b.txt`), directory
  0700, outside the repo. Temporary server staging
  (`sites/.lab-rerun`, `app/.../tests/lab_roles.py`) was created by this run and
  removed afterwards; the Mac credentials file was not modified.
- Offline regression: `tests/unit/test_lab_roles.py` now also pins that
  `read_private` fetches the private URL (never the photo URL), compares the
  private size rather than the photo size, and records transport errors and
  unexpected statuses as failures instead of raising. 24 tests pass.
