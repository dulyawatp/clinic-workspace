# Local native SIT report — 2026-11-14

**Status:** read-only native browser SIT executed against the isolated local
stack. No native create/edit was available or executed. Native writes, clinical
workflows, registration, photo upload/publication and all PHA/CAS/MGR/ADM cases
remain blocked or not run. This report is not a release, integration-complete
or production-readiness claim.

**Date:** 2026-11-14  
**Source revision:** `ef8b65a` working tree (only new deployment/report files
uncommitted; no product code changed by this task)  
**Target:** native React UI at `http://127.0.0.1:18081/clinic/login` on the
isolated `marley-sit` Compose project  
**Actors:** three provisioned synthetic identities only — `DOC`, `NUR`, `REC`  
**Synthetic record:** one allow-listed patient, referred to below only by alias
`WS-SIT-1`  
**Private evidence alias:** `T3-20261114`  
**Private manifest:** `T3-20261114/evidence-manifest.json`  
**Private evidence location:** `~/.local/state/marley-sit/evidence/task3/`
(directory mode 700; every file mode 600; outside the repository)

## 1. Authorization, scope and safety

The user approved synthetic local create/edit operations, but no native
create/edit contract exists. This execution therefore did **not**:

- bypass, enable or weaken a disabled write gate;
- change any account, role, permission, site configuration or capability;
- delete any record, document, container, volume or backup;
- touch the existing lab (`127.0.0.1:18080`), the legacy proxy application,
  production, real-patient data or any existing user record;
- commit, push, create a subagent or alter product source.

Only synthetic credentials from the private credential file (mode 600) were
used. Passwords, cookies, session identifiers, CSRF tokens and the patient's
display name are deliberately absent from this report. There are no screenshots
in the repository and none were published.

**Data-state limit:** no pre-run database snapshot was taken. Therefore this
report makes no before/after comparison of patient fields, role assignments,
permissions or stock balances. It records only the final read-only counts and
the fact that no intentional business write was issued (see §4).

## 2. Environment and prerequisites

The running stack was inspected, not redeployed. Deployment was performed and
validated by Task 1; its 26/26 live verification remains the deployment
evidence. This task added only browser/API observations and did not revalidate
deployment independently.

| Check | Observation |
| --- | --- |
| Compose project | `marley-sit`; 9 containers up; only published port `127.0.0.1:18081` |
| Site | `marley-sit.local`; apps frappe, erpnext, healthcare, clinic_ui |
| Native session endpoint | `GET /api/method/clinic_ui.api.session.bootstrap` |
| Native directory endpoint | `GET /api/method/clinic_ui.api.directory.synthetic_patients` |
| Native live appointment endpoint | **Not implemented** — UI renders `UNSUPPORTED` |
| Native live photo endpoint | **Not implemented** — UI renders `UNSUPPORTED` |
| Native patient profile / encounter / clinical endpoints | **Not implemented** — UI renders `UNSUPPORTED` |
| Capabilities | 35 returned for every authenticated actor, all `read_only` |
| Native write methods | none; authenticated POST to the two GET-only methods returns 403 |
| Role mapping | exactly one native role per synthetic actor: `DOC`, `NUR`, `REC` |

### Actors

| Actor | Native role | Patient directory | UI identity/role evidence |
| --- | --- | --- | --- |
| DOC | `DOC` | 1 allow-listed record | Profile snapshot shows synthetic DOC identity and doctor role |
| NUR | `NUR` | 1 allow-listed record | Profile snapshot shows synthetic NUR identity and nurse role |
| REC | `REC` | 0 records; registration not offered | Bootstrap role only; no REC profile identity captured |

No PHA, CAS, MGR or ADM synthetic identity exists on this site, and creating
one was out of scope. Those roles and their cases are **not run**.

## 3. Commands, exit statuses and evidence

All URLs below are loopback-only. `$SID` denotes a cookie value read from the
private evidence directory; no secret is printed. HTTP statuses were observed
in the session transcript. The persisted evidence files contain response bodies,
snapshots and header lines, not every status code.

| Command | Exit / result | Evidence alias |
| --- | --- | --- |
| `docker ps --filter label=com.docker.compose.project=marley-sit --format ...` | 0; 9 containers up | `EV-DOCKER` |
| `agent-browser --session sit-anon open http://127.0.0.1:18081/clinic/login` | 0; login page loaded | `EV-SHELL-HDR` |
| `agent-browser --session sit-doc open …/clinic/login` then `fill @e7`, `fill @e8`, `click @e9` | 0; reached `/clinic` | `EV-DOC-LOGIN`, `EV-SESSION-DOC` |
| `agent-browser --session sit-nur open …/clinic/login` then `fill`, `fill`, `click` | 0; reached `/clinic` | `EV-NUR-LOGIN`, `EV-SESSION-NUR` |
| `agent-browser --session sit-rec open …/clinic/login` then `fill`, `fill`, `click` | 0; reached `/clinic` | `EV-REC-LOGIN`, `EV-SESSION-REC` |
| `agent-browser --session sit-invalid … fill @e7 <DOC id>`, `fill @e8 <bad password>`, `click @e9` | 0; stayed on `/clinic/login` | `EV-INVALID` |
| `agent-browser --session sit-doc click @e17` (profile) | 0; `/clinic/profile` | `EV-DOC-LOGIN` |
| `agent-browser --session sit-nur click @e18` (patients) | 0; `/clinic/patients`, one row | `EV-NUR-DIR` |
| `agent-browser --session sit-rec click @e14` (patients) | 0; `/clinic/patients`, empty state | `EV-REC-DIR` |
| `agent-browser --session sit-doc fill @e13 "ZZZ-NO-MATCH"` | 0; empty state | `EV-DOC-DIR` |
| `agent-browser --session sit-doc click @e15` (appointments) | 0; `UNSUPPORTED` panel | `EV-DOC-APPT` |
| `agent-browser --session sit-doc click @e39` (live photos) | 0; unavailable panel | `EV-DOC-PHOTO` |
| `agent-browser --session sit-nur click @e20` (queue) | 0; `UNSUPPORTED` panel | `EV-NUR-QUEUE` |
| `agent-browser --session sit-rec click @e21` (reception) | 0; `UNSUPPORTED` appointments panel | `EV-REC-RECEPTION` |
| `agent-browser --session sit-doc open …/clinic/patients/WS-SIT-1` | 0; authorized deep link opens; profile content unsupported | `EV-DOC-DEEP` |
| `agent-browser --session sit-anon open …/clinic/patients/WS-SIT-1` | 0; redirected to `/clinic/login?reason=expired` | `EV-ANON-DEEP` |
| `agent-browser --session sit-doc open …/clinic/system` | 0; static demo page rendered for DOC | `EV-DOC-SYSTEM`; policy sources in manifest |
| `agent-browser --session sit-rec open …/clinic/system` | 0; static demo page rendered for REC | `EV-REC-SYSTEM`; policy sources in manifest |
| `agent-browser --session sit-doc cookies get` → `curl -H "Cookie: sid=$SID" …/bootstrap` (before logout) | authenticated bootstrap body | `EV-SESSION-DOC` |
| `agent-browser --session sit-doc network requests --clear`, then `agent-browser --session sit-doc eval "(()=>{const b=[...document.querySelectorAll('button')].find(x=>x.textContent.includes('ออกจากระบบ')); b.click(); return 'clicked';})()"` | 0; URL eventually `/clinic/login?reason=expired`; final old-SID bootstrap body `AUTH_EXPIRED` | `EV-LOGOUT-DOC` |
| Same final DOM-button activation for NUR and REC | 0; final old-SID bootstrap body `AUTH_EXPIRED` for each | `EV-LOGOUT-NUR`, `EV-LOGOUT-REC` |
| `curl -H "Cookie: sid=$SID" …/api/method/clinic_ui.api.directory.synthetic_patients` (each role) | DOC 200/1, NUR 200/1, REC 200/0 | `EV-SESSION-DOC`, `EV-SESSION-NUR`, `EV-SESSION-REC` |
| Anonymous `curl` to bootstrap / directory | 401 `AUTH_EXPIRED` / 403 `PermissionError` | `EV-ANON-API` |
| Authenticated `POST` with valid CSRF to bootstrap / directory | 403 `PermissionError` for both | `EV-POST` |
| `curl -s -o /dev/null -D - …/clinic/login` | 200; `Cache-Control: no-store,no-cache,must-revalidate,max-age=0` | `EV-SHELL-HDR` |
| `curl -s -o /dev/null -D - …/clinic/patients/WS-SIT-1` | 200; same no-store header | `EV-SHELL-HDR` |
| `docker compose -p marley-sit -f deploy/local-sit/compose.yaml exec -T -w /home/frappe/frappe-bench/sites backend /home/frappe/frappe-bench/env/bin/python -` (read-only counts) | 0; final counts only: patients 1, non-system users 3, sessions 14, genders 1 | `EV-COUNTS` |
| `agent-browser --session sit-rec set viewport 390 844` / `834 1112` / `1440 900`; open `/clinic/patients`; evaluate `scrollWidth` | 0; document width equals viewport width at all three widths | `EV-VIEWPORT` |
| `chmod 700` evidence directory; `chmod 600` every evidence file | 0 | `EV-PERMS` |
| `python3 deploy/local-sit/bin/verify.py` — **Task 1, inherited; not rerun by Task 3** | 0; 26/26 live checks on 2026-11-14 | Task 1 private `verify-20261114T080630Z.json` |

No deployment, rebuild, restart or container recreation was performed by Task 3.
The only deployment evidence used here is Task 1's recorded 26/26 verification
against the same running `marley-sit` stack; this task inspected that stack and
did not revalidate deployment independently.

### Logout evidence — distinct attempts, not one click

The DOC sign-out evidence does **not** prove one successful click or a 200
logout response:

1. An early ref click left the UI authenticated; `doc-after-logout.txt` shows
   the signed-in profile, and `doc-bootstrap-after.json` contains an
   authenticated bootstrap body.
2. A later ref click with HAR recording produced `doc-logout.har` with
   **0 entries**. No request was persisted from that attempt.
3. A final DOM-button activation navigated to
   `/clinic/login?reason=expired`; `doc-bootstrap-after2.json` contains an
   `AUTH_EXPIRED` body.

NUR has `nur-bootstrap-after-logout.json` (authenticated) followed by
`nur-bootstrap-after-logout2.json` (`AUTH_EXPIRED`). REC has only the final
`rec-bootstrap-after-logout.json` (`AUTH_EXPIRED`). No HAR/network file was
persisted for the final POST of any role. The final `POST /logout` status was
observed in the session transcript only. The report does **not** attribute the
earlier no-op click to hydration timing or any other cause, and does not claim
`POST /logout` 200 as persisted evidence.

### Execution attempts and limitations

| Attempt | Observed | Resolution |
| --- | --- | --- |
| `agent-browser fill @e7 …` before a snapshot in a newly opened session | `✗ Unknown ref: e7`; element not found | Snapshotted the session first, then filled fresh refs |
| Early sign-out ref click | Click reported success but UI stayed authenticated and bootstrap body was authenticated; no persisted request in `doc-logout.har` | Later DOM-button activation produced an expired session; cause of the early no-op is not established |
| `docker compose exec` Python without a working directory | `FileNotFoundError: /home/frappe/logs/database.log`; counts not obtained | Repeated with `-w /home/frappe/frappe-bench/sites`; exit 0 |
| `docker compose exec` Python without `sites_path` | `IncorrectSitePath: marley-sit.local does not exist` | Passed `sites_path=/home/frappe/frappe-bench/sites`; exit 0 |

## 4. Independent backend evidence

| Actor | Bootstrap | Roles | Directory | Logout evidence | Limits |
| --- | --- | --- | --- | --- | --- |
| DOC | 200, `mode=live`, 35 caps, 64-hex epoch | `["DOC"]` | 200, 1 item, alias `WS-SIT-1` | Early attempt authenticated; final old-SID body `AUTH_EXPIRED` | No persisted final POST trace; cache not tested |
| NUR | 200, `mode=live`, 35 caps | `["NUR"]` | 200, 1 item, alias `WS-SIT-1` | Early authenticated; final old-SID body `AUTH_EXPIRED` | No persisted final POST trace; cache not tested |
| REC | 200, `mode=live`, 35 caps | `["REC"]` | 200, **0 items** | Final old-SID body `AUTH_EXPIRED` | No persisted final POST trace; cache not tested; directory mapping unresolved |
| Anonymous | 401 `{"code":"AUTH_EXPIRED"}` | — | 403 `PermissionError` | — | Guest boundary holds |
| Authenticated POST | 403 | — | 403 | — | GET-only surface and write gates hold |

**Final read-only counts only** (`backend-counts.txt`, recorded after the run):
1 Patient, 3 non-system users (the three synthetic accounts), 14 Sessions, 1
Gender. No pre-run snapshot exists, so no before/after comparison is possible
for patient fields, role assignments, permissions or stock balances. No
intentional business-document write was issued by this task; the only write
paths intentionally exercised were authentication login/logout. The final
Sessions count is 14, but there is no pre-run baseline.

The directory response contains `workspace_patient_id`, `native_patient_id` and
`display_name`; the directory UI displayed the alias and display name. The
native ID was not observed in the UI. Full DTO-minimisation conformance was not
tested.

## 5. Executed variants

Statuses are limited to `PASS`, `FAIL`, `BLOCKED`, `NOT RUN`, `NOT APPLICABLE`.
A `BLOCKED` result means at least one required assertion of the full catalogue
variant was not executed or its feature is absent/gated; it is **not** a pass.
A `PASS` below covers only the specific condition named.

| Variant | Actor | Steps / input | Expected UI and backend | Actual | Status | Evidence | Blocker / note |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `SIT-AUTH-01-H-DOC` | DOC | Sign in with synthetic DOC credentials | Role home, correct identity/role, native session, no password retained | Reached `/clinic`; profile shows synthetic DOC identity and doctor role; bootstrap roles `[DOC]`; **password-retention assertion not tested** | **BLOCKED** | `EV-DOC-LOGIN`, `EV-SESSION-DOC` | Full happy path incomplete; session/identity subcheck passes separately |
| `SIT-AUTH-01-H-NUR` | NUR | Sign in with synthetic NUR credentials | Same | Reached `/clinic`; profile shows synthetic NUR identity and nurse role; bootstrap roles `[NUR]`; **password-retention assertion not tested** | **BLOCKED** | `EV-NUR-LOGIN`, `EV-SESSION-NUR` | Full happy path incomplete |
| `SIT-AUTH-01-H-REC` | REC | Sign in with synthetic REC credentials | Same | Reached `/clinic`; bootstrap roles `[REC]`; **no REC profile identity/role captured; password retention not tested** | **BLOCKED** | `EV-REC-LOGIN`, `EV-SESSION-REC` | Full happy path incomplete |
| `SIT-AUTH-01-U-invalid-DOC` | DOC | One valid synthetic identifier + deliberately wrong password | Invalid credentials → safe denial; no protected data; distinguish authentication from authorization | Stayed on `/clinic/login`; password field empty; no authenticated shell. Exact error text was transcript-only | **BLOCKED** | `EV-INVALID` | Only one wrong-password attempt; disabled/unmapped/ambiguous/service-unavailable conditions not tested; auth-vs-authz distinction not established |
| `SIT-AUTH-02-H-DOC` | DOC | Sign out, then re-use prior cookie | Session revoked, protected cache cleared, later protected request denied | Early ref click left UI authenticated and bootstrap body authenticated; later final activation yielded `AUTH_EXPIRED`; **cache clear not tested; no persisted final POST trace** | **BLOCKED** | `EV-LOGOUT-DOC` | Full logout path incomplete and evidence ambiguous |
| `SIT-AUTH-02-H-NUR` | NUR | Same | Same | Early old-SID body authenticated; final old-SID body `AUTH_EXPIRED`; **cache clear not tested; no persisted final POST trace** | **BLOCKED** | `EV-LOGOUT-NUR` | Full logout path incomplete |
| `SIT-AUTH-02-H-REC` | REC | Same | Same | Final old-SID body `AUTH_EXPIRED`; **cache clear not tested; no persisted final POST trace** | **BLOCKED** | `EV-LOGOUT-REC` | Full logout path incomplete |
| `SIT-AUTH-03-H-DOC` | DOC | With valid session, open `/clinic/patients/WS-SIT-1` directly | Authorized page opens; session resumes | Page opened at the deep link; no redirect to login; profile content `UNSUPPORTED` | **PASS** (route/session only) | `EV-DOC-DEEP`, `EV-SESSION-DOC` | Patient profile content remains blocked under `SIT-PAT-04`, which is not run |
| `SIT-AUTH-03-U-anon` | anonymous | Open the same deep link as guest | Safe sign-in, no protected rows | Redirected to `/clinic/login?reason=expired`; no patient data | **PASS** | `EV-ANON-DEEP` | Expiry/revoked-session variants not separately tested |
| `SIT-AUTH-06-H-DOC` | DOC | Open each visible nav item | Each authorized surface opens with permitted content | Home, patients and profile open; appointments and live photos show `UNSUPPORTED`; search link present but not opened | **BLOCKED** | `EV-DOC-APPT`, `EV-DOC-PHOTO`, `EV-DOC-LOGIN` | Native appointment/photo read endpoints absent; search not fully exercised |
| `SIT-AUTH-06-H-NUR` | NUR | Open each visible nav item | Same | Patients open; queue and live photos show `UNSUPPORTED` | **BLOCKED** | `EV-NUR-DIR`, `EV-NUR-QUEUE`, `EV-NUR-PHOTO` | Queue and photo endpoints absent |
| `SIT-AUTH-06-H-REC` | REC | Open each visible nav item | Same | Reception/appointments/queue show `UNSUPPORTED`; patients empty; checkout not exercised | **BLOCKED** | `EV-REC-DIR`, `EV-REC-RECEPTION` | No native reception/booking/queue contract |
| `SIT-AUTH-06-U-system-DOC` | DOC | Direct deep link to `/clinic/system` (nav hidden; manifest roles ADM/MGR) | Server/route denies unauthorized role | Static demo page rendered; nav manifest restricts system to ADM/MGR, but route has no role guard; page states it loads no patient data and writes nothing | **BLOCKED** | `EV-DOC-SYSTEM`; manifest `policy_sources.system_route` | Policy decision required: whether direct route must deny or demo-only access is acceptable. No security failure established |
| `SIT-AUTH-06-U-system-REC` | REC | Direct deep link to `/clinic/system` | Same | Same static demo page rendered for REC | **BLOCKED** | `EV-REC-SYSTEM`; manifest `policy_sources.system_route` | Same unresolved policy decision; no sensitive data observed |
| `SIT-AUTH-07-H-all` | DOC/NUR/REC | Inspect capabilities; attempt POST with valid session/CSRF | Missing/malformed/disabled/unknown capability fails closed; only verified actions | 35 caps all `read_only`; write/draft controls absent; authenticated POST to both methods 403 | **BLOCKED** | `EV-SESSION-DOC`, `EV-SESSION-NUR`, `EV-SESSION-REC`, `EV-POST` | Malformed/disabled/unknown capability and approved-write behavior not tested |
| `SIT-PAT-01-H-DOC` | DOC | Open patient directory; search by alias/no-match | Find allow-listed patient, paginate, open record | One row (`WS-SIT-1`); pagination disabled for one row; no row-open action; no-match empty state safe | **BLOCKED** | `EV-DOC-DIR`, `EV-SESSION-DOC` | List/read subcheck passes; opening a patient record is not implemented |
| `SIT-PAT-01-H-NUR` | NUR | Open patient directory | Find allow-listed patient | One row (`WS-SIT-1`); no row-open action | **BLOCKED** | `EV-NUR-DIR`, `EV-SESSION-NUR` | Same missing record-open contract |
| `SIT-PAT-01-U-nomatch-DOC` | DOC | Search `ZZZ-NO-MATCH` | Truthful empty state, no identifying data | Empty state; 0 rows; no leakage | **PASS** | `EV-DOC-DIR` | Specific no-match condition only |
| `SIT-PAT-01-U-REC` | REC | Open patient directory | REC read behavior according to approved policy mapping | 0 items; UI states profiles/registration unavailable; backend 200/0 | **BLOCKED** | `EV-REC-DIR`, `EV-SESSION-REC`; `docs/assessments/2026-11-14-local-native-patient-contracts.md` | Task 2 found Healthcare Administrator has no `Patient` DocPerm in Healthcare 16.6.1. Policy mapping decision required; not confirmed as a requirement FAIL |
| `SIT-APP-01-H-DOC` | DOC | Open profile | Correct identity, role, company and verified profile data | Identity and role correct; company shows unavailable; profile read error | **BLOCKED** | `EV-DOC-LOGIN` | No native profile/company read contract |
| `SIT-APP-01-H-NUR` | NUR | Open profile | Same | Identity and role correct; company unavailable | **BLOCKED** | `EV-NUR-LOGIN` | No native profile/company read contract |
| `SIT-APP-01-H-REC` | REC | Open profile | Same | Profile link present in nav; identity/role not captured before logout | **BLOCKED** | `EV-REC-LOGIN` | Not completed for REC; native profile contract absent |
| `SIT-REC-01-H-REC` | REC | Open reception home and task tiles | Authorized task tiles; navigate to real work; no clinical/photo leakage | Reception home only renders unsupported live appointments; no registration control (write cap read-only) | **BLOCKED** | `EV-REC-RECEPTION` | Native reception workflow not implemented |
| `SIT-REC-02-H-REC` | REC | Open today's synthetic appointments | List practitioner/time/status; open patient preview | `UNSUPPORTED`; no list | **BLOCKED** | `EV-REC-RECEPTION` | Appointment read endpoint absent |
| `SIT-NUR-01-H-NUR` | NUR | Open assigned tasks/queue | Assigned patient/task view appropriate to permissions | Queue route `UNSUPPORTED`; no task surface | **BLOCKED** | `EV-NUR-QUEUE` | No nurse task/queue contract |
| `SIT-PHOTO-01-H-DOC` | DOC | Open live photo workspace | Saved photos for allow-listed patient | Workspace states synthetic patient list cannot be verified; no photos rendered | **BLOCKED** | `EV-DOC-PHOTO` | Live photo patient/photo endpoints absent |
| `SIT-PHOTO-01-H-NUR` | NUR | Open live photo workspace | Same | Same unavailable state | **BLOCKED** | `EV-NUR-PHOTO` | Same absent contract |
| `SIT-DOC-02-H-DOC` | DOC | Open a visit workspace | Canonical visit sections with real source data | No encounter/visit entry point; direct visit route not exercised because no synthetic encounter exists | **BLOCKED** | `EV-DOC-DIR`, `EV-DOC-DEEP` | No encounter/clinical read contract or fixture encounter |
| `SIT-I18N-01-H-DOC` | DOC | Switch TH→EN, reload | Labels translate; verified preference persists across authorized session | Labels translated to English; reload returned to Thai; UI states preference is current-workspace only | **FAIL** | `EV-I18N-DOC` | Locale is in-memory per page/session; catalogue persistence expectation unmet. Product/requirements decision also required |
| `SIT-I18N-01-H-REC` | REC | Switch TH→EN→TH on patient directory | Labels translate; preference persists | Labels translated both ways; **persistence not tested** | **BLOCKED** | `EV-I18N-REC` | Full case incomplete |
| `SIT-UX-02-layout-REC` | REC | 390×844, 834×1112, 1440×900 on directory | Mandatory content not clipped | Document scroll width equals viewport width at all three widths; **no claim that all controls are unclipped or that full flow works** | **BLOCKED** | `EV-VIEWPORT` | Only horizontal-overflow subcheck; full navigation/form flow impossible because create/edit and clinical forms are blocked |
| `SIT-INT-01-H-all` | anonymous only | Open `/clinic/login` and an anonymous deep link | Shell resolves; guest safe sign-in; no-store; `/api`, `/desk` unaffected | 200 shell with `no-store`; login UI shown; redirect URL and assets observed only in transcript | **BLOCKED** | `EV-SHELL-HDR`, `EV-ANON-DEEP` | Seven-role coverage and `/api` and `/desk` non-regression not tested; missing/malformed asset manifest not tested |

**Full-variant tally:** 32 variants — **3 PASS, 1 FAIL, 28 BLOCKED**, 0 NOT RUN
inside this table. The 3 PASS entries are specific conditions
(`AUTH-03-H-DOC` route/session only, `AUTH-03-U-anon`, and
`PAT-01-U-nomatch-DOC`).

### Supported subchecks (not full catalogue passes)

These are narrow observations kept separate so they are not mistaken for full
variant passes.

| Subcheck | Actor | Observation | Status | Evidence |
| --- | --- | --- | --- | --- |
| `SUB-AUTH-01-DOC-SESSION` | DOC | Login reached `/clinic`; bootstrap role `DOC`; profile identity/role correct | **PASS** | `EV-DOC-LOGIN` |
| `SUB-AUTH-01-NUR-SESSION` | NUR | Login reached `/clinic`; bootstrap role `NUR`; profile identity/role correct | **PASS** | `EV-NUR-LOGIN` |
| `SUB-AUTH-01-REC-SESSION` | REC | Login reached `/clinic`; bootstrap role `REC` | **PASS** | `EV-REC-LOGIN` |
| `SUB-AUTH-01-INVALID-DENIAL` | DOC | One wrong-password attempt stayed on sign-in; no authenticated shell; password field empty | **PASS** | `EV-INVALID` |
| `SUB-AUTH-02-DOC-FINAL-REVOKE` | DOC | Final old-SID bootstrap body `AUTH_EXPIRED`; early attempt ambiguous | **PASS** | `EV-LOGOUT-DOC` |
| `SUB-AUTH-02-NUR-FINAL-REVOKE` | NUR | Final old-SID bootstrap body `AUTH_EXPIRED` | **PASS** | `EV-LOGOUT-NUR` |
| `SUB-AUTH-02-REC-FINAL-REVOKE` | REC | Final old-SID bootstrap body `AUTH_EXPIRED` | **PASS** | `EV-LOGOUT-REC` |
| `SUB-AUTH-07-READONLY` | DOC/NUR/REC | 35 capabilities all `read_only`; authenticated POST 403 on both methods | **PASS** | `EV-SESSION-DOC`, `EV-SESSION-NUR`, `EV-SESSION-REC`, `EV-POST` |
| `SUB-AUTH-06-NAV-HIDDEN` | DOC/REC | System surface absent from visible nav (manifest roles ADM/MGR) | **PASS** | `EV-DOC-LOGIN`, `EV-REC-LOGIN`; policy source |
| `SUB-I18N-01-REC-SWITCH` | REC | TH→EN→TH labels translated; 0-row empty state remained | **PASS** | `EV-I18N-REC` |
| `SUB-INT-01-SHELL` | anonymous | Login shell 200 with no-store; guest deep link redirected; assets observed | **PASS** | `EV-SHELL-HDR`, `EV-ANON-DEEP` |
| `SUB-PAT-01-DOC-LIST` | DOC | One allow-listed alias listed; no record-open action | **PASS** | `EV-DOC-DIR` |
| `SUB-PAT-01-NUR-LIST` | NUR | One allow-listed alias listed | **PASS** | `EV-NUR-DIR` |
| `SUB-PAT-01-NOMATCH` | DOC | No-match yields empty state and 0 rows | **PASS** | `EV-DOC-DIR` |
| `SUB-UX-02-OVERFLOW` | REC | No horizontal document overflow at 390/834/1440 | **PASS** | `EV-VIEWPORT` |

### Evidence audit

Every full-variant and subcheck row above was resolved against
`evidence-manifest.json`. The manifest records each alias's files and locators,
plus these unresolved or transcript-only items:

- final `POST /logout` status for DOC/NUR/REC (transcript-only; no HAR/network
  file);
- exact invalid-credentials error text (transcript-only);
- successful-login password-retention assertion (no post-login field/screenshot);
- REC UI identity/role (not captured before logout);
- AUTH-02 cache-clear assertion (no cache/protected-row observation);
- AUTH-07 malformed/disabled/unknown capability behavior;
- INT-01 `/api` and `/desk` non-regression;
- before-run database state (no pre-run snapshot);
- clipped-control assertion (only horizontal overflow measured);
- system-route denial policy (not defined/proven).

`rec-viewports.txt` is **not authoritative**: its width stayed 1280 for all
three rows. The authoritative viewport files are
`rec-viewport-390.txt`, `rec-viewport-834.txt` and `rec-viewport-1440.txt`.

## 6. Blocked, unexecuted and unsupported coverage

These are **not** passes. No variant below was executed, or its required
feature is absent.

| Area | Cases | Reason |
| --- | --- | --- |
| Registration and demographic create/edit | `SIT-PAT-02`, `SIT-PAT-03`, `SIT-REC-05`–`SIT-REC-07` | Native create/edit contract and write approval absent; REC policy mapping unresolved |
| Patient profile/open/history | `SIT-PAT-04`, `SIT-PAT-05`, `SIT-PAT-06` | No native patient detail, timeline, vital or profile read endpoint |
| Clinical workspace | `SIT-DOC-01`–`SIT-DOC-09`, `SIT-CLIN-01`, `SIT-CLIN-02` | No synthetic encounter/visit, note, order, procedure or lab contract |
| Nursing | `SIT-NUR-02`, `SIT-NUR-03`, `SIT-LAB-01`, `SIT-LAB-02` | No task, vitals, prep, lab dispatch or handoff contract |
| Photos/annotations | `SIT-PHOTO-02`–`SIT-PHOTO-09` | Upload/publication/recovery gates not approved; endpoints absent |
| Reception/appointments | `SIT-REC-01`–`SIT-REC-09` | No native appointment, check-in, queue, checkout or KPI contract |
| Pharmacy and stock | `SIT-PHA-01`–`SIT-PHA-08`, `SIT-WF-01` | No PHA actor and no stock/batch/purchasing contract |
| Cashier and finance | `SIT-CAS-01`–`SIT-CAS-05` | No CAS actor and no invoice/payment contract |
| Manager | `SIT-MGR-01`–`SIT-MGR-03` | No MGR actor and no reporting contract |
| Administrator/configuration | `SIT-ADM-01`–`SIT-ADM-06` | No ADM actor; permission/account changes were not approved |
| Shared security/session | `SIT-AUTH-04`, `SIT-AUTH-05`, `SIT-AUTH-08`, `SIT-INT-02`–`SIT-INT-14` | No approved fault harness, session-switch harness, CSRF mutation surface or terminal-receipt proof |
| Accessibility/localization extensions | `SIT-UX-01`, `SIT-UX-03`, `SIT-I18N-02`, `SIT-I18N-03`, `SIT-FORM-01` | Forms/workflows unavailable; only basic keyboard semantics observed incidentally |
| Cross-workflow and optional V3 | `SIT-WF-02`, `SIT-REC-09`, `SIT-INT-08`, `SIT-INT-09` | Dependent contracts absent; optional items not implemented |

### Role coverage gap

Only DOC, NUR and REC exist. PHA, CAS, MGR and ADM have no synthetic identity on
this site. Their positive and denied variants are **NOT RUN**. Creating those
identities would require a separately approved permission/account change and
was not attempted.

### Traceability gap

This execution covers a small read-only slice of the catalogue. No complete
mapping of AT-01–25, V3-AT-01–13, F01–F16 or WF-01–07 to executed variants
exists yet. Unlisted inherited requirements remain coverage gaps, not implicit
passes. No release gate is satisfied.

## 7. Findings and blockers

1. **REC directory is empty; policy mapping is unresolved.** Authenticated REC
   directory returns 200 with 0 items; the UI says profiles and registration are
   unavailable. Task 2's contract assessment
   (`docs/assessments/2026-11-14-local-native-patient-contracts.md`) records that
   `Healthcare Administrator` has no `Patient` DocPerm in Healthcare 16.6.1.
   This is **BLOCKED pending a policy mapping decision**, not a confirmed
   requirement failure. No permission was changed.
2. **Direct `/clinic/system` access needs a policy decision.** The navigation
   manifest (`app/src/features/shell/nav.ts`) restricts the system surface to
   `ADM`/`MGR`, but the router
   (`app/src/app/router.tsx`) has no role guard. The page
   (`app/src/features/system/SystemStatesPage.tsx`) is a static demo that states
   it loads no patient data and writes nothing. Whether direct route access must
   deny is not defined or proven; this is not recorded as an established
   security failure.
3. **Locale preference is not persistent.** Thai→English translation works, but
   reload returns to Thai. The profile page says the language change applies
   only to the current workspace and is not saved. This conflicts with
   `SIT-I18N-01` as written; a requirements/product decision is needed.
4. **Unsupported native surfaces fail closed.** Appointments, queue, live photo
   workspace, patient detail and profile company render explicit unavailable
   states instead of fabricated data or proxy fallback. This is the correct
   safety behavior but leaves those SIT cases blocked.
5. **Write gates hold in the observed surface.** Every capability is
   `read_only`; create/draft controls are absent; authenticated POST with a
   valid CSRF token is refused 403 on both native methods. No create/edit was
   performed and no write path was bypassed.
6. **Data-state evidence is final-only.** `backend-counts.txt` records 1
   Patient, 3 non-system users, 1 Gender and 14 Sessions after the run. No
   pre-run snapshot exists. The only write paths intentionally exercised were
   authentication login/logout; no business-document write was issued. Patient
   fields, roles, permissions and stock balances were not compared.
7. **Logout evidence is mixed.** The early DOC attempt left the session
   authenticated; a later HAR is empty; the final activation yielded an expired
   session. No final POST status is persisted for any role. AUTH-02 remains
   blocked because cache clearing and a clean single-attempt trace were not
   proven.
8. **Native coverage is far below the SIT catalogue.** Only session bootstrap
   and a synthetic patient directory are implemented in the native build.
   Clinical, registration, appointment, photo, billing, stock, reporting and
   administration contracts are absent or blocked.

## 8. Evidence handling

| Alias | Content | Protection |
| --- | --- | --- |
| `T3-20261114` | Private directory holding browser snapshots/text, role cookie jars, bootstrap/directory JSON, POST-refusal JSON, shell headers, viewport results, backend counts | Directory 700; every file 600; outside repository |
| `evidence-manifest.json` | Alias-to-filename/locator manifest, including policy sources, transcript-only items and unresolved assertions | No cookies, tokens or patient identifiers; mode 600 |
| `evidence-perms.txt` | Directory/file permission inventory | Mode 600 |
| `EV-*` | Aliases used in §3–§5; each resolves to files/locators in the manifest | Same private directory; no secrets in this report |

The private manifest records `rec-viewports.txt` as superseded and
non-authoritative. Browser sessions remain available for operator inspection
where they were not signed out (`sit-anon`, `sit-invalid`); the three role
sessions were signed out. No credentials or session identifiers are stored in
the repository. No screenshots were captured or published.

## 9. Conclusion

The native React UI and Marley backend were exercised through a real browser for
the supported read-only slice. Three synthetic roles signed in; anonymous access
was denied; the allow-listed directory obeyed native role permissions for DOC/NUR
but exposed the unresolved REC mapping; native writes were unavailable and
refused; unsupported surfaces failed closed. Session revocation was observed for
each role at the final attempt, but the logout trace is incomplete and cache
clearing was not tested.

Create/edit, registration, clinical documentation, appointments, photos,
pharmacy, finance, reporting and administration remain **BLOCKED or NOT RUN**.
No complete-integration, release or production-readiness claim is made.

## 10. Post-deployment addendum — native patient VIEW slice (2026-11-14)

**Status:** partial native patient VIEW slice verified. DOC and NUR can open the
allow-listed patient from the directory and render allow-listed display fields;
REC, unknown alias and anonymous access fail closed, though REC and unknown-alias
denial bodies expose detailed backend tracebacks (error-safety FAIL); no proxy
fallback was observed. The full `SIT-PAT-04` profile assertion is
**BLOCKED/partial** because visits, history, appointments and linked photos are
not part of this slice.

**Deployment observed:** `marley-sit` containers were up after the new
deployment; the served UI used JS `index-iyF09pxE.js` and CSS
`index-P6wyMZyJ.css`. The new native method is
`GET /api/method/clinic_ui.api.patients.view`. No deployment, restart, rebuild,
permission change, deletion, create/edit or business write was performed by
this addendum. The running stack and private resources were preserved.

**Actors:** DOC, NUR, REC (existing synthetic identities only). No secrets or
screenshots are included. Private evidence alias: `PD-20261114` at
`~/.local/state/marley-sit/evidence/postdeploy-20261114/` (directory 700,
files 600); manifest: `evidence-manifest-postdeploy.json`.

### 10.1 Commands and statuses

| Command | Exit / result | Evidence |
| --- | --- | --- |
| `docker ps --filter label=com.docker.compose.project=marley-sit --format ...` | 0; 9 containers up; loopback `127.0.0.1:18081` | `PD-DEPLOY` |
| `agent-browser --session pd-doc open http://127.0.0.1:18081/clinic/login`; snapshot; `fill @e7`; `fill @e8`; `click @e9` | 0; redirected to `/clinic` | `PD-DOC-DIR`, `PD-DOC-PROFILE` |
| `agent-browser --session pd-doc click @e15` (patients); `click @e26` (Open record) | 0; `/clinic/patients/WS-SIT-1` | `PD-DOC-PROFILE` |
| `agent-browser --session pd-nur open …/clinic/login`; snapshot; `fill`; `fill`; `click`; `click @e15`; `click @e26` | 0; `/clinic/patients/WS-SIT-1` | `PD-NUR-DIR`, `PD-NUR-PROFILE` |
| `agent-browser --session pd-nur reload` | 0; deep link reloaded the same authorized profile | `PD-NUR-PROFILE` |
| `agent-browser --session pd-doc network har start/stop` around profile click | 0; only `GET /api/method/clinic_ui.api.patients.view?workspace_patient_id=WS-SIT-1` | `PD-DOC-PROFILE`, `PD-REQUESTS` |
| `agent-browser --session pd-nur network har start/stop` around profile click | 0; same native view request only | `PD-NUR-PROFILE`, `PD-REQUESTS` |
| `curl -H "Cookie: sid=$SID" …/api/method/clinic_ui.api.directory.synthetic_patients` (DOC/NUR/REC) | 200 with 1 / 1 / 0 items | `PD-BACKEND`, `PD-REC` |
| `curl -H "Cookie: sid=$SID" …/api/method/clinic_ui.api.patients.view?workspace_patient_id=WS-SIT-1` (DOC/NUR) | 200; exact seven-key payload | `PD-BACKEND` |
| Same view request after REC login | 403; no alias/display/native key; **detailed backend traceback present** (contents not copied) | `PD-REC`, `PD-DENIAL-SAFETY` |
| Same view request with DOC cookie and `workspace_patient_id=WS-UNKNOWN` | 403; no patient fields; **detailed backend traceback present** (contents not copied) | `PD-UNKNOWN`, `PD-DENIAL-SAFETY` |
| Anonymous `curl` to the same view request | 403; no patient fields; no detailed traceback observed | `PD-ANON` |
| `docker compose -p marley-sit -f deploy/local-sit/compose.yaml exec -T -w /home/frappe/frappe-bench/sites backend … python - "$NATIVE_ID"` with `frappe.db.get_value("Patient", …, field)` for six allowlisted fields | 0; read-only comparison source | `PD-BACKEND` |
| `agent-browser --session pd-nur set viewport 390 844` / `834 1112` / `1440 900`; evaluate profile bounds | 0; document width equals viewport; five key elements visible/inside all widths | `PD-RESPONSIVE` |
| `agent-browser --session pd-rec open …/clinic/patients/WS-SIT-1` | 0; UI permission-denied; native view request 403 | `PD-REC` |
| `agent-browser --session pd-anon open …/clinic/patients/WS-SIT-1` | 0; redirected to `/clinic/login?reason=expired` | `PD-ANON` |

**Native-only request check:** HAR files for DOC, NUR, REC and unknown-alias
profile navigation contain **zero** `/api/read`, `/api/live`, `/api/write`,
`/api/auth` or `/api/files` calls. The only patient read is
`clinic_ui.api.patients.view`. This is the no-proxy-fallback evidence.

### 10.2 Independent backend evidence

| Actor / case | API result | Independent read-only comparison |
| --- | --- | --- |
| DOC profile | 200; exact keys: `workspace_patient_id`, `display_name`, `first_name`, `last_name`, `sex`, `dob`, `status` | All six native source fields equal `Patient` DB values; no `native_patient_id` or contact key |
| NUR profile | 200; same exact keys | All six native source fields equal `Patient` DB values; no `native_patient_id` or contact key |
| REC profile | 403 | No patient fields; directory 200 with 0 items; **detailed backend traceback present** (contents not copied) |
| Unknown alias `WS-UNKNOWN` | 403 | No patient fields; **detailed backend traceback present** (contents not copied) |
| Anonymous | 403; UI redirects to sign-in via bootstrap | No patient fields; no detailed traceback observed |
| Cache headers | `Cache-Control: no-store` on success, denial, unknown and anonymous view responses | — |

**Error-safety finding:** the REC and unknown-alias 403 bodies contain detailed
backend tracebacks. The traceback contents were not copied into this report or
any public artifact. No alias, display name, native key, email-like,
phone/mobile, password or cookie/CSRF text was observed in those bodies. The
anonymous 403 body contained only framework message/type keys and no detailed
traceback. This is an error-safety failure against `SIT-INT-02`'s log/error
condition and is also relevant to `SIT-AUTH-08`'s sensitive-stack-trace
criterion; `SIT-AUTH-08` was not otherwise executed.

Alias chain observed: directory alias `WS-SIT-1` → profile route
`/clinic/patients/WS-SIT-1` → API `workspace_patient_id=WS-SIT-1` → UI
`patient-id` `WS-SIT-1`. All matched. UI DOB, sex and status labels matched the
API values; the display name matched the DB `patient_name`.

**Field-safety limit:** the native docname currently equals the display name on
this fixture, so absence of that string from UI text is not proof that a native
identifier was not rendered. The API key set is the reliable assertion: the
response does not contain `native_patient_id`, contact fields or clinical
fields.

### 10.3 Full catalogue variants

Catalogue variants and explicitly labelled conditions are listed here. `PASS`
is used only where the entire stated condition was exercised. Narrow checks are
in §10.4.

| Variant | Actor | Expected | Actual | Status |
| --- | --- | --- | --- | --- |
| `SIT-PAT-01-H-DOC` | DOC | Find allow-listed patient, paginate, open correct record | Directory listed one alias; Open-record link opened the correct profile. Name/mobile/ID search and real pagination were not exercised | **BLOCKED** (partial) |
| `SIT-PAT-01-H-NUR` | NUR | Same | Same directory/open result; name/mobile/ID search and real pagination were not exercised | **BLOCKED** (partial) |
| `SIT-PAT-04-H-DOC` | DOC | Profile with authorized visits, history, appointments and linked photos | Only display identity/facts rendered: display name, alias, DOB, sex, status. No visits, history, appointments or photos | **BLOCKED** (partial) |
| `SIT-PAT-04-H-NUR` | NUR | Same | Same display-only profile; no history/visit/photo sections | **BLOCKED** (partial) |
| `SIT-PAT-04-U-cross-patient-DOC` | DOC | Existing-patient cross-patient/direct-link access → denied/filtered; no data | Unknown alias was denied, but no second allowlisted patient exists, so existing cross-patient access was not tested | **BLOCKED** (partial) |
| `SIT-PAT-04-U-REC` | REC | Policy-dependent REC read behavior → denied/filtered; no clinical leakage | Directory 0 items; direct profile UI permission-denied; native view 403; denial body/UI had no alias, display name or native key, but contained a detailed backend traceback. REC patient-read policy remains unresolved | **BLOCKED** (policy-dependent) |
| `SIT-AUTH-03-U-anon` | anonymous | Guest → safe sign-in; no protected rows | Direct deep link redirected to `/clinic/login?reason=expired`; API 403; no patient body | **PASS** |
| `SIT-UX-02-profile-responsive` | DOC/NUR profile | 390/834/1440 key flow with no clipped mandatory content | Document width equals viewport; five key identity/fact elements visible/inside at all widths. Full navigation/form flow not tested | **BLOCKED** (partial) |
| `SIT-INT-02-U-error-body` | REC/DOC unknown alias | Denial/error body must not expose a sensitive stack trace | REC and unknown-alias 403 bodies contain detailed backend tracebacks; contents not copied. No patient/credential fields observed; anonymous body had no detailed traceback | **FAIL** |
| `SIT-INT-02-H` | DOC/NUR | Minimum permitted fields; private responses no-store; malformed/logs checked | Exact seven-key payload and `no-store` verified; error-body inspection found detailed tracebacks (see `SIT-INT-02-U-error-body`); malformed DTO, extra-field and log-capture checks not exercised | **BLOCKED** (partial) |
| `SIT-INT-06-H` | DOC/NUR/REC | Native server denies unauthorized role on each applicable operation | Patient view: DOC/NUR 200, REC 403. Other operations not part of this slice | **BLOCKED** (partial) |

**Addendum full-variant tally:** 11 variants — **1 PASS, 1 FAIL, 9 BLOCKED**
(partial). No full `SIT-PAT-04` happy-path pass is claimed; unknown alias is a
narrow subcheck, not proof of existing cross-patient denial.

### 10.4 Supplemental checks (not full catalogue passes)

| Check | Result | Evidence |
| --- | --- | --- |
| Directory → profile alias chain | PASS | `PD-BACKEND` |
| API payload equals independent DB read for DOC/NUR | PASS | `PD-BACKEND` |
| Exact API key set; no native/contact/clinical keys | PASS | `PD-BACKEND` |
| UI display name, DOB, sex and status labels match API/DB | PASS | `PD-BACKEND` |
| Native-only profile request; no proxy fallback | PASS | `PD-REQUESTS` |
| `no-store` on success and denial | PASS | `PD-BACKEND`, `PD-REC`, `PD-UNKNOWN`, `PD-ANON` |
| `SUB-PAT-04-U-REC-denial` | PASS | `PD-REC` |
| `SUB-PAT-04-U-unknown-alias-DOC` | PASS | `PD-UNKNOWN` |
| Anonymous UI redirect and API denial with no data | PASS | `PD-ANON` |
| Profile deep-link reload for NUR | PASS | `PD-NUR-PROFILE` |
| Responsive profile key elements at 390/834/1440 | PASS (scope-limited) | `PD-RESPONSIVE` |
| `SUB-INT-02-ERROR-BODY` | **FAIL** | `PD-DENIAL-SAFETY`; detailed tracebacks not copied |

The two `SUB-PAT-04-U-*` rows are narrow denial observations only. They do not
convert `SIT-PAT-04-U-REC` into a policy pass or `SIT-PAT-04-U-cross-patient-DOC`
into a cross-patient pass.

### 10.5 Limitations and unresolved evidence

- Full `SIT-PAT-04` remains blocked: no visits, history, appointments, linked
  photos, cross-patient switching or clinical data.
- Full `SIT-PAT-01` remains blocked: no mobile/ID search, invalid/out-of-scope
  search variants, or real pagination beyond one row.
- Full `SIT-UX-02` remains blocked: only the profile display state was tested at
  three widths, not navigation/form flows.
- `SIT-INT-02` malformed DTO, extra-field and log-capture checks were not
  exercised. Its error-body condition **FAILED**: REC and unknown-alias denial
  responses exposed detailed backend tracebacks; contents were not copied.
- REC native denial is observed, but the baseline REC patient-read policy
  mapping remains unresolved; the full policy-dependent `SIT-PAT-04-U-REC`
  variant is **BLOCKED**, not passed.
- No second allowlisted patient exists, so existing cross-patient data
  isolation was not tested; the unknown-alias denial is only a narrow subcheck.
- No screenshots were captured or published. No cookies, session identifiers,
  patient field values or credentials are in this report.

**Addendum conclusion:** the newly deployed patient VIEW slice passes the
supported display/alias checks for DOC/NUR, the narrow REC and unknown-alias
denial observations, and the anonymous denial check, with native-only requests
and DB-matched allowlisted fields. It is a partial SIT result with one
error-safety **FAIL**: detailed backend tracebacks were returned in REC and
unknown-alias denial responses. Full patient profile, cross-patient isolation,
search/pagination, clinical history, malformed-DTO/log checks and wider
integration requirements remain blocked or not run.

## 11. Safe error envelope retest (2026-11-14)

This section retests the deployed safe error envelope. The §10 error-safety
**FAIL** is retained as the historical pre-envelope result; it is not rewritten.

### 11.1 Environment and method

The `marley-sit` stack was up and the React UI served
`index-iyF09pxE.js` / `index-P6wyMZyJ.css`. Retest used `agent-browser` sessions
with the existing private synthetic credentials, HAR capture, an installed
`window.fetch` interceptor for actual UI-triggered denial bodies, and a
same-origin browser `fetch` for direct response-body capture. Raw bodies and
HARs were written only to private evidence, mode `600`.

Commands were of the form:

```text
agent-browser --session rt-rec open http://127.0.0.1:18081/clinic/login
agent-browser --session rt-rec fill <user-field> "$REC_USER"
agent-browser --session rt-rec fill <password-field> "$REC_PASSWORD"
agent-browser --session rt-rec click <submit>
agent-browser --session rt-rec network har start <private-har>
agent-browser --session rt-rec open http://127.0.0.1:18081/clinic/patients/WS-SIT-1
agent-browser --session rt-rec network har stop <private-har>
agent-browser --session rt-rec eval --stdin   # same-origin fetch + UI fetch interceptor
```

Equivalent sessions were used for the DOC unknown-alias case (`rt-doc`) and the
guest case (`rt-anon`). Credential values, cookies and session identifiers are
not reproduced here.

### 11.2 Results

| Case | Observed UI | Native request | Denial body | Evidence |
| --- | --- | --- | --- | --- |
| REC, allowlisted alias `WS-SIT-1` | “ไม่มีสิทธิ์เข้าถึงหน้านี้” preserved | `GET clinic_ui.api.patients.view?workspace_patient_id=WS-SIT-1` → 403 | 403, `application/json`, 21 bytes, exactly `{"code": "FORBIDDEN"}`, `no-store` | `RT-REC-DENIAL` |
| DOC, unknown alias `WS-UNKNOWN` / `WS-RETEST-UNKNOWN` | “ไม่มีสิทธิ์เข้าถึงหน้านี้” preserved | `GET clinic_ui.api.patients.view?workspace_patient_id=WS-UNKNOWN` → 403 | 403, `application/json`, 21 bytes, exactly `{"code": "FORBIDDEN"}`, `no-store` | `RT-UNKNOWN-DENIAL` |
| Guest | Login page renders; deep link redirects to `/clinic/login?reason=expired` | `session.bootstrap` → 401; no patient view from UI | Direct view 403, 21 bytes, exactly `{"code": "FORBIDDEN"}`, `no-store` | `RT-ANON` |

Safety assertions passed for every captured body: no traceback text, no server
path, no message/exception details, no patient alias/display/native/contact or
clinical data, and no credential/cookie/CSRF text. The actual UI-triggered REC
body matched the direct-fetch body byte-for-byte. The guest deep link returned
the SPA shell with 200 and then redirected through the bootstrap 401; the API
view itself returned the safe 403 envelope.

### 11.3 Proxy fallback audit

All retest HAR entries were `GET` requests to native pages, static assets,
`clinic_ui.api.session.bootstrap`, or `clinic_ui.api.patients.view`. Across the
three HARs (14 entries total) there were **zero** requests to `/api/read`,
`/api/live`, `/api/write`, `/api/auth` or `/api/files`. No proxy fallback was
observed. Evidence: `RT-PROXY-AUDIT`.

### 11.4 Status

| Variant / condition | Status | Reason |
| --- | --- | --- |
| `SIT-INT-02-U-error-body` | **PASS** (scoped) | REC, DOC unknown-alias and guest denial bodies all returned the code-only safe envelope with no traceback, server path, message detail, patient data or credential data |
| `SIT-PAT-04-U-REC` | **BLOCKED** (policy-dependent) | REC denial behavior is observed and preserved, but the baseline REC patient-read policy mapping remains unresolved; this retest is not a policy pass |
| `SIT-PAT-04-U-cross-patient-DOC` | **BLOCKED** | The unknown alias is not an existing patient, so this does not test existing-patient cross-patient isolation |
| `SIT-AUTH-03-U-anon` | **PASS** | Guest login renders and the protected deep link redirects to safe sign-in with no protected rows |

`PASS` is claimed only for the error-body condition actually exercised here and
for the guest denial variant. The deploy fixes the specific §10 traceback
finding for these denial paths. It does not convert the policy-dependent REC
variant or the cross-patient variant into passes.

### 11.5 Limitations

- No second allowlisted patient exists; existing-patient cross-patient access
  remains untested.
- REC patient-read policy remains unresolved.
- Only patient-view GET denial paths were exercised; malformed DTO, extra-field,
  log-capture and other error surfaces were not.
- No screenshots were captured or published. No credential, cookie, session
  identifier, traceback text or patient field value is in this report.

**Retest conclusion:** the deployed safe error envelope removes the previously
observed detailed denial tracebacks for REC, DOC unknown-alias and guest
patient-view requests, while preserving the existing permission-denied UI and
guest redirect. No proxy fallback was observed. Historical §10 remains the
pre-envelope record; this section is the post-envelope retest.

## 12. Post-deployment addendum — native patient CREATE/EDIT/VIEW UI SIT (2026-11-14)

**Status:** partial. DOC created a new fictional synthetic Patient through the
native React UI, then edited and re-viewed it through the same UI. The create,
update and acknowledgement receipts are terminal `COMMITTED` in the private
write fence, the authoritative `Patient` row matches the UI-submitted values,
and no Customer, Contact or User record matching the synthetic identity was
observed in the post-run read. A scoped responsive **FAIL** was observed: the
created patient's profile page overflows horizontally at 390 px because the
long opaque workspace alias does not wrap. This section is
not a full `SIT-PAT-02`, `SIT-PAT-03` or `SIT-PAT-04` pass, and it is not a
release or production-readiness claim.

**Date:** 2026-11-14 (run window ends 11:47 UTC)  
**Target:** native React UI `http://127.0.0.1:18081/clinic` on the isolated
`marley-sit` Compose project only. The retained REC capture loaded
`/assets/clinic_ui/ui/assets/index-C98Dorft.js` and
`/assets/clinic_ui/ui/assets/index-JQTv9oLP.css`; those are the observed served
assets for this run. The older `index-C2fPQs0M.js` / `index-P6wyMZyJ.css` names
in the deployment log are not used as this section's provenance.  
**Actors:** existing synthetic `DOC`, `NUR`, `REC` only  
**Record under test:** one newly created fictional patient, referred to here
only by private label `WS-UI-1`; actual opaque alias and all field values are in
private evidence only  
**Private evidence alias:** `PW-20261114`  
**Private evidence:** `~/.local/state/marley-sit/evidence/patient-write-sit/`
(directory `700`, files `600`); manifest `evidence-manifest.json`

### 12.1 Safety and one-shot execution

The run used only synthetic identities from the private credentials file and
the newly deployed, user-approved create/edit controls. It did not create a
table or column, alter permissions or accounts, delete a Patient or resource,
change site configuration, restart/rebuild/redeploy anything, touch the existing
`127.0.0.1:18080` lab, or use a proxy fallback in the captured edit and REC
navigation. The existing fixture was not submitted for edit; its profile
exposes the edit control, but it was not activated and the retained fence and
captured HAR contain no fixture-targeted update.

No save or acknowledgement result was uncertain. The create and update fence
keys each show a terminal `COMMITTED` receipt and an `ack:` receipt, and the
retained database state agrees with the UI. The browser write lock
(`clinic.patient-write.pending`) was read as absent after both flows, but that
lock check is **observation-only**: no lock artifact was retained. Had any
save/ack been ambiguous, no further write would have been issued and the lock
would have been preserved.

Two UI save activations during the edit produced **no request**: the retained
fence snapshot immediately before the explicit submit still had four events and
no update event, and the later committed update appears only once. The null lock
and the unchanged `Patient` row for those activations are **observation-only**
(no retained artifact). This is recorded as an automation activation limitation,
not as a retried uncertain mutation and not as a product save-button defect.

### 12.2 Exact UI steps

1. Signed in as DOC at `/clinic/login` with the private synthetic credentials;
   landed on `/clinic`.
2. Opened `ผู้ป่วย` (`/clinic/patients`). The deployed page showed the
   `เพิ่มผู้ป่วยจำลอง` (add synthetic patient) control and the one allow-listed
   fixture row.
3. Activated `เพิ่มผู้ป่วยจำลอง`; filled first name, last name, sex and date of
   birth with new fictional values (private label `WS-UI-1`), then activated
   `บันทึก` once.
4. The form closed; the page showed `บันทึกข้อมูลผู้ป่วยแล้ว`. The browser write
   lock was read as absent (**observation-only**, no retained artifact). The
   directory still showed only the one allow-listed fixture, so the newly
   created record was **not** discoverable there (directory row observation).
5. Opened the created record through the native UI route
   `/clinic/patients/<server-returned-alias>`. The alias was obtained read-only
   from the committed create receipt in the private fence, not from the browser
   lock or a client-supplied identifier.
6. The profile rendered the created synthetic identity, the opaque workspace
   alias, DOB, sex and status, plus `แก้ไขผู้ป่วยจำลอง` (edit synthetic patient).
7. Activated the edit control. The form was prefilled with the created values.
   Changed only the last name. The first save activations produced no dispatch
   (see §12.1); one explicit form submit then closed the form.
8. Reloaded the profile. The page rendered the updated display name, same alias,
   DOB, sex and status. The edit control was still presented (observation-only,
   no retained capture).
9. Re-ran the profile at 390×844, 834×1112 and 1440×900; the 390 px width
   overflowed (see §12.4; **observation-only**, no retained bounds artifact).
   The anonymous/guest check in §12.5 was read-only and is a narrow supplemental
   observation.

### 12.3 Native endpoint statuses

| Endpoint / case | Observed | Evidence |
| --- | --- | --- |
| `POST clinic_ui.api.patients.create` (DOC, UI create) | Status code not directly captured. Client accepted the response and acknowledgement, the form showed the saved status, the write lock was read as cleared (**observation-only**), and the create key reached a terminal `COMMITTED` receipt in the fence | `PW-CREATE` (observation-only), `PW-FENCE` |
| `POST clinic_ui.api.patients.update` (DOC, UI edit) | **200**; only native update, then ack, then profile view in the capture | `PW-HAR-EDIT` |
| `POST clinic_ui.api.patients.ack` (DOC, UI edit) | **200**; ack receipt appended to the fence | `PW-HAR-EDIT`, `PW-BACKEND` |
| `GET clinic_ui.api.patients.view` (DOC, created alias) | **200**; exact keys `workspace_patient_id`, `display_name`, `first_name`, `last_name`, `sex`, `dob`, `status`, `modified`; `Cache-Control: no-store` | `PW-VIEW-API`, `PW-VIEW-PAYLOAD` |
| Same view (NUR) | **403**, `no-store`, no field body | `PW-VIEW-API` |
| Same view (REC) | **403**, `no-store`, safe code envelope | `PW-VIEW-API`, `PW-HAR-REC` |
| Same view (guest) | **403**, `no-store`; UI deep link redirected to `/clinic/login?reason=expired` (guest redirect is **observation-only**; the native 403 is captured) | `PW-VIEW-API`, `PW-GUEST` (observation-only) |
| `GET clinic_ui.api.directory.synthetic_patients` (DOC after create) | **200**, one item — the pre-existing allow-listed fixture; the new patient was not listed (**observation-only**, no retained directory body) | `PW-DIRECTORY` (observation-only) |
| Directory (REC) | **200**; zero items and no add-patient control observed (**observation-only** for the UI; the GET status is captured) | `PW-HAR-REC`, `PW-REC` (observation-only) |

**Proxy fallback audit:** the captured edit HAR (3 entries) contains only native
`patients.update`, `patients.ack` and `patients.view`; the captured REC HAR (10
entries) contains only native bootstrap, directory and a denied native view.
Both have **zero** requests to `/api/read`, `/api/live`, `/api/write`,
`/api/auth` or `/api/files`. Evidence: `PW-HAR-EDIT`, `PW-HAR-REC`. This
conclusion is scoped to those two captures. The create HAR is a zero-entry
capture, so **no proxy-fallback conclusion is made for the create flow**.

The create HAR capture was started on the wrong browser session and recorded
zero entries. No status code is claimed for create; the claim rests on the
client success path, the committed fence receipts and the database row.

### 12.4 Independent backend and side-effect checks (read-only)

| Check | Result | Evidence |
| --- | --- | --- |
| New `Patient` exists | Yes; one new row, synthetic identity `WS-UI-1`; native docname equals display name | `PW-BACKEND` |
| Persisted fields | The stored `first_name`, `last_name`, `sex`, `dob` and `status` equal the retained view payload, and the captured update request carried the edited values. That the edit differed from creation only in the last name is **observation-only** (neither the create inputs nor a pre-edit row were retained) | `PW-BACKEND`, `PW-VIEW-PAYLOAD`, `PW-HAR-EDIT` |
| No contact/customer/user linkage on the new row | `customer` null, `user_id` empty, `email`/`mobile`/`phone` null, `invite_user` `0` | `PW-BACKEND`, `PW-SIDE-EFFECTS` |
| Customer/Contact/User side effect | Post-run observation only: Customer 1, Contact 3, User 5, and no Customer, Contact or User matched the synthetic identity. **No pre-run totals were captured**, so this is not an unchanged-totals assertion | `PW-SIDE-EFFECTS` (post-run observation) |
| Alias storage | The opaque alias appears only in the private fence receipt. The retained pre-run snapshot has 887 tables and no `clinic_ui`-named table. The post-run 887/no-`clinic_ui` result was observed but **no post-schema snapshot was retained**, so no unchanged-schema claim is made | `PW-PRESTATE` (pre); post observation-only |
| Write fence | 8 events: four `DISPATCHED` and four `COMMITTED` (create and update, each with actor and site-latch records); the site-latch `COMMITTED` events carry the `ack:` receipts and the actor `COMMITTED` events the alias receipt; no `UNKNOWN` state observed. Source: the post-edit fence dump | `PW-BACKEND` |
| Pre-edit fence snapshot | The separately retained earlier snapshot has exactly four events and no update event | `PW-FENCE` |
| Client lock | `clinic.patient-write.pending` was read as absent after both flows; **observation-only**, no retained artifact | `PW-LOCK` (observation-only) |
| View payload matches DB | All six source fields equal the independent `Patient` read; `workspace_patient_id` equals the alias; no `native_patient_id` or contact keys | `PW-VIEW-PAYLOAD`, `PW-BACKEND` |
| Existing fixture untouched | Its `customer` link and `invite_user` remain populated; no retained fence receipt and no captured request targets it. The fixture's `modified` timestamp was not captured before the run, so no pre-timestamp comparison is claimed | `PW-PRESTATE`, `PW-BACKEND` |
| Responsive — created profile | At 390×844 `document.scrollWidth` was read as **545** against a 390 px viewport; the profile identity block and unwrapped opaque alias were 512 px wide. Directory at 390 px did not overflow. At 834 and 1440 px the profile fit. **Observation-only**; no bounds artifact was retained. Supplementary check, not a full layout pass | `PW-RESPONSIVE` (observation-only; scoped **FAIL**) |

**Field-safety limit:** the new native docname again equals its display name, so
absence of that string from UI text is not proof that a native identifier was
not rendered. The API key set is the reliable assertion: no native identifier
and no contact field is returned.

### 12.5 Variant statuses (scoped, not full catalogue passes)

| Variant | Actor | Actual | Status |
| --- | --- | --- | --- |
| `SIT-PAT-02-H-DOC` | DOC | New fictional patient created once through the UI; form confirmed save; DB row, alias receipt and ack all match. The catalogue variant names REC/authorized ADM and also requires duplicate-candidate handling; neither was run | **BLOCKED** (partial) |
| `SIT-PAT-02-U-*` | — | Missing/invalid fields, duplicate, permission denial and double-click were not executed | **NOT RUN** |
| `SIT-PAT-03-H-DOC` | DOC | Last-name edit saved once through the UI; update and ack both 200; DB and reloaded profile agree; version advanced. Stale-version, invalid-field and revoked-permission variants were not run | **BLOCKED** (partial) |
| `SIT-PAT-03-U-*` | — | Stale version, invalid field and revoked permission were not executed | **NOT RUN** |
| `SIT-PAT-04-H-DOC` | DOC | Created profile renders identity, alias, DOB, sex and status (UI content observation-only). Visits, history, appointments and linked photos remain absent | **BLOCKED** (partial) |
| `SIT-PAT-04-U-REC` | REC | Directory row count 0 observed (observation-only); direct profile denied by UI and native 403 with captured safe body | **BLOCKED** (policy-dependent) |
| `SIT-PAT-04-U-cross-patient-DOC` | DOC/NUR | The only created patient is resolvable by its creator; NUR got a captured 403 on the DOC-created alias. This is alias/actor binding, not an existing-patient cross-patient isolation test | **BLOCKED** |
| `SIT-AUTH-03-U-anon` | guest | Deep link redirected to safe sign-in; native view 403; no data. Narrow supplemental observation-only check, not a full authentication suite | **PASS** (supplemental) |
| `SIT-UX-02-profile-responsive` | DOC | Profile overflows horizontally at 390 px because the opaque alias does not wrap; 834 and 1440 fit. Narrow supplemental observation-only check | **FAIL** (scope-limited, supplemental) |
| `SIT-INT-06-H` | DOC/NUR/REC | Create/update allowed for DOC, view allowed for DOC; NUR/REC create/update not attempted; NUR/REC view of the created alias denied. REC mutation rejection was **not tested**. Other operations not run | **BLOCKED** (partial) |

**Tally for this addendum:** 10 variants — **1 PASS, 1 FAIL, 6 BLOCKED**
(partial/policy-dependent), **2 NOT RUN**. No full patient create/edit/view
catalogue family is claimed passed.

### 12.6 Findings and limitations

- **Finding (UX/data discoverability):** the patient directory lists only the
  static allow-list. A newly created patient has no directory row and the create
  flow offers no link to the new record, so view/edit required the native route
  with the server-returned alias. This blocks an ordinary user from completing
  create → view/edit without the alias.
- **Finding (responsiveness):** the created profile overflows at 390 px; the
  long opaque alias is unbreakable. The prior responsive pass used the short
  `WS-SIT-1` alias and does not cover this new alias format.
- **Finding (actor binding):** the alias is actor-bound. NUR cannot view the
  patient created by DOC; no shared clinician visibility of locally created
  records was observed.
- Create/update are limited to DOC/NUR capabilities on this deployment; REC
  bootstrap advertised no write capability and the REC capture contained no
  write request. REC mutation rejection itself was **not tested**.
- Not executed: duplicate create, invalid/missing fields, stale `base_modified`,
  revoked permission, NUR create/edit, double-click, create/ack failure
  injection, REC create/update rejection, log-capture checks and any
  clinical/visit/photo content.
- **Evidence provenance:** the report uses only the aliases mapped in the
  private manifest. Checks that were observed in the session but have no
  retained artifact are labelled **observation-only**: the browser lock reads,
  the two non-dispatch save activations, the DOC directory row, the guest
  redirect, and the responsive bounds. The create HAR and one edit HAR are
  zero-entry captures and are not used as request-status evidence.
- No screenshots were captured or published. No credential, cookie, CSRF token,
  session identifier, patient field value or full alias is in this report.

**Addendum conclusion:** the deployed controls allowed one DOC native-UI create
and one DOC native-UI edit of a new fictional synthetic patient, with a terminal
committed receipt and acknowledgement for each, DB-verified values, and no
Customer/Contact/User record matching the synthetic identity in the post-run
read. No new table was present in the retained pre-run snapshot or the post-run
observation. The captured edit and REC traffic show no proxy fallback; the
create flow has no retained request capture. REC advertised no write capability
and issued no captured write, but REC mutation rejection was not tested. Guest
access and responsiveness were narrow supplemental observations, and the 390 px
profile overflow remains a supplemental **FAIL**. The result is a partial SIT
outcome with one patient-directory discoverability gap and unresolved
cross-actor visibility; full SIT-PAT-02/03/04 and all unhappy-path variants
remain blocked or not run.

## 13. Deployed-fix UI retest — directory, cross-clinician read-only, mobile (2026-11-14)

**Status:** partial retest of the deployed fixes. All checks listed in this
section passed against the running `marley-sit` build. This is not a full SIT
pass: only directory visibility, profile linking, read-only presentation for
NUR, REC/guest denial, directory DTO field safety, proxy absence, one profile's
responsive bounds and fence immutability were exercised. Historical §12 results
are preserved; its responsive **FAIL** and directory discoverability gap remain
the pre-fix record and are not rewritten.

**Date:** 2026-11-14  
**Target:** `http://127.0.0.1:18081` only; `marley-sit` Compose project; nine
containers up; loopback publication only  
**Served assets observed:** `/assets/clinic_ui/ui/assets/index-BvD2VLj_.js`,
`/assets/clinic_ui/ui/assets/index-BAFLUY3s.css`  
**Build observed:** the redeployed directory/cross-clinician/mobile-fix build
recorded in the deployment log; this retest did not deploy or restart anything  
**Actors:** existing private synthetic `DOC`, `NUR`, `REC` and an anonymous
session only  
**Private evidence alias:** `RT2-20261114`  
**Private evidence:** `~/.local/state/marley-sit/evidence/ui-retest-20261114/`
(directory `700`, files `600`); manifest `evidence-manifest.json`

### 13.1 Safety and method

The retest used browser sign-in and read-only navigation and data requests. It
did not create, edit or delete a patient or any other record, change a permission,
account or site configuration, unlock or re-key a fence, restart or redeploy a
container, or touch the existing `127.0.0.1:18080` lab. No write control was
activated. Browser HARs and same-origin `GET` response bodies were written only
to private evidence. Credentials, cookies, CSRF tokens, patient names, the
created patient's alias and all field values are absent from this section.

Baseline fence state was read before any browser navigation and read again after
the last one. Both reads returned the same 8 events, the same last sequence `8`
and the same row digest; no fence write occurred. The post-run read reported
`Patient` count `2`; no patient mutation was issued.

### 13.2 Exact commands and observed statuses

Credential values and the created alias are omitted; `<alias>` denotes the
private alias read from the fence receipt and stored privately as `RT2-ALIAS`.

| Command (abridged) | Observed | Evidence |
| --- | --- | --- |
| `docker ps --filter label=com.docker.compose.project=marley-sit --format ...` | exit 0; 9 containers up; only `127.0.0.1:18081` published (observation-only, output not retained) | — |
| Backend read-only Python: open the private fence SQLite store `mode=ro`, `SELECT * FROM events ORDER BY rowid` | exit 0; 8 events, last sequence 8, four `DISPATCHED` + four `COMMITTED` | `RT2-BASELINE` |
| `agent-browser --session rt2-doc open …/clinic/login`; snapshot; fill the two credential fields; `click` submit | exit 0; landed on `/clinic` | `RT2-DOC-HAR` session |
| `agent-browser --session rt2-doc eval` click nav link `ผู้ป่วย` | exit 0; `/clinic/patients`; two rows: allow-listed fixture and created patient | `RT2-DOC-HAR` |
| `agent-browser --session rt2-doc eval` click the anchor whose href matches the created-patient route | exit 0; URL became `/clinic/patients/<alias>`; created identity rendered; `แก้ไขผู้ป่วยจำลอง` button present | `RT2-DOC-HAR` |
| `agent-browser --session rt2-doc network har start/stop` around directory and profile | exit 0; `GET directory.synthetic_patients` 200; `GET patients.view?workspace_patient_id=<alias>` 200; zero proxy-like paths | `RT2-DOC-HAR` |
| Same-origin `GET` directory as DOC / NUR via private synthetic login | 200 / 200; two items each; `Cache-Control: no-store` | `RT2-API` |
| Directory DTO key audit | Response contains only `items`; each item has only `display_name` and `workspace_patient_id`; **no `native_patient_id` token or value** | `RT2-API` |
| Same-origin `GET patients.view` as DOC | 200; keys `display_name`, `dob`, `editable`, `first_name`, `last_name`, `modified`, `sex`, `status`, `workspace_patient_id`; `editable: true`; `no-store` | `RT2-API` |
| `agent-browser --session rt2-nur open …/clinic/login`; login; eval-click `ผู้ป่วย` | exit 0; `/clinic/patients`; two rows; created patient shown | `RT2-NUR-HAR` |
| eval-click the created-patient anchor as NUR | exit 0; URL `/clinic/patients/<alias>`; no edit button; explanatory copy `ไม่สามารถแก้ไขข้อมูลรายการนี้ได้` rendered | `RT2-NUR-HAR` |
| Same-origin `GET patients.view` as NUR | 200; same field keys; `editable: false`; `no-store` | `RT2-API` |
| DOC eval-click fixture anchor `href="/clinic/patients/WS-SIT-1"` | exit 0; `/clinic/patients/WS-SIT-1`; fixture profile rendered | `RT2-FIXTURE-HAR` |
| `agent-browser --session rt2-rec open …/clinic/login`; login; eval-click `ผู้ป่วย` | exit 0; `/clinic/patients`; zero rows; no add-patient control | `RT2-REC-HAR` |
| `agent-browser --session rt2-rec open …/clinic/patients/<alias>` | exit 0; UI rendered `ไม่มีสิทธิ์เข้าถึงหน้านี้`; no identity text | `RT2-REC-HAR` |
| `agent-browser --session rt2-guest open …/clinic/patients/<alias>` | exit 0; redirected to `/clinic/login?reason=expired`; bootstrap 401 | `RT2-GUEST-HAR` |
| Same-origin `GET` as REC | directory 200 with `items: []`; view 403 with 21-byte `{"code": "FORBIDDEN"}`; `no-store` | `RT2-API` |
| Same-origin `GET` as guest | directory 403; view 403 with the same 21-byte code envelope; `no-store` | `RT2-API` |
| `agent-browser … set viewport 390 844` / `834 1112` / `1440 900`; open `<alias>`; measure `document.documentElement.scrollWidth`, `innerWidth`, overflow elements, edit-button bounds | scroll width equalled viewport width at all three widths (`390`, `834`, `1440`); zero elements past the viewport; edit button inside the viewport at each width | `RT2-RESP-390`, `RT2-RESP-834`, `RT2-RESP-1440` |
| HAR audit over `doc-directory`, `nur-directory`, `rec`, `guest`, `doc-fixture` | zero requests to `/api/read`, `/api/live`, `/api/write`, `/api/auth` or `/api/files` | `RT2-DOC-HAR`, `RT2-NUR-HAR`, `RT2-REC-HAR`, `RT2-GUEST-HAR`, `RT2-FIXTURE-HAR` |
| Backend read-only fence read after all navigation | exit 0; still 8 events, last sequence 8, identical row digest; `Patient` count 2 | `RT2-AFTER` |

Exact browser statuses are not surfaced for client-side route changes; HTTP
statuses above come from HAR entries for the native requests and from the
same-origin `GET` captures. Directory/response bodies are private evidence only.

### 13.3 Check results

| Check | Result | Basis |
| --- | --- | --- |
| DOC directory displays the created patient | **PASS** | Two rows rendered, including the created patient |
| NUR directory displays the created patient | **PASS** | Two rows rendered, including the created patient |
| Created-patient link opens the correct profile | **PASS** | DOC and NUR landed on `/clinic/patients/<alias>`; profile identity matched |
| Fixture link opens the correct profile | **PASS** | DOC landed on `/clinic/patients/WS-SIT-1` |
| DOC edit control enabled | **PASS** | `แก้ไขผู้ป่วยจำลอง` rendered; view payload `editable: true` |
| NUR read-only with explanatory copy | **PASS** | No edit control; `ไม่สามารถแก้ไขข้อมูลรายการนี้ได้`; view payload `editable: false` |
| REC denied under current policy | **PASS** | Empty directory, no create control, UI permission denial, view 403 safe envelope |
| Guest denied | **PASS** | Deep link redirected to sign-in; bootstrap 401; directory/view 403 |
| No native IDs in directory network response | **PASS** | Item keys only `display_name`, `workspace_patient_id`; no `native_patient_id` token |
| No proxy fallback in captured sessions | **PASS** | Zero `/api/read`, `/api/live`, `/api/write`, `/api/auth`, `/api/files` requests across five HARs |
| Created-profile responsive at 390/834/1440 | **PASS** (scope-limited) | Measured scroll width equals viewport and zero overflow elements at all three widths; edit control within bounds |
| Fence sequence unchanged | **PASS** | 8 events, last sequence 8, identical row digest before and after; no write issued |

**Result for this retest:** 12 checks — **12 PASS**, no FAIL and no BLOCKED
within the stated scope. Overall SIT status remains **partial**: this section
does not exercise create/edit mutations, duplicate or invalid-input handling,
stale versions, revoked permissions, NUR mutation rejection, clinical data,
other roles or other workflows, and no full `SIT-PAT-02/03/04` pass is claimed.

### 13.4 Relationship to prior findings

- §12's responsive **FAIL** used the pre-fix build and is retained as the
  historical record. On the build observed here the created profile no longer
  overflows at 390 px, and its measured scroll width equals the viewport.
- §12's directory discoverability gap is likewise historical: the created
  patient now appears in both DOC and NUR directories and its row link opens the
  correct profile.
- §12's actor-bound visibility limitation is superseded for reads on this build:
  NUR can open the DOC-created patient read-only with explicit explanatory copy.
  Update authorization was not tested by this retest.

### 13.5 Limitations

- Read-only only. No create, edit, delete, permission change, fence action or
  deployment was performed.
- NUR's bootstrap still advertises the two patient-write capabilities, but the
  created record is returned `editable: false` and the UI offers no edit control.
  Whether a NUR update request is refused was **not tested** here.
- REC patient-read policy remains policy-dependent; this retest records the
  current deployed behavior only.
- Directory body safety was checked on the current response; HAR entries did not
  retain response bodies, so bodies were captured by separate same-origin `GET`
  requests.
- Responsive checking used measured `scrollWidth`, viewport bounds and edit
  control position on the created profile only, not a visual or multi-page
  layout review.
- Only one created patient exists; no cross-patient isolation or listing
  pagination was exercised.
- No screenshots were captured or published. No credential, cookie, session
  token, patient field value or full alias appears in this section.

**Section conclusion:** on the redeployed build observed here, DOC and NUR
directories list the existing created patient and link to the correct profile;
DOC sees the edit control and `editable: true`; NUR sees a read-only profile with
explanatory copy and `editable: false`; REC and guest are denied under current
policy; the directory DTO exposes only display name and workspace alias; the
captured sessions show no proxy fallback; the created profile fits 390, 834 and
1440 px; and the write fence is byte-identical before and after. These are
scoped retest results, not a full SIT pass.

## 14. Native read-only appointment list SIT (2026-11-14)

**Status:** partial. On the running isolated build, DOC `/clinic/appointments`
and REC `/clinic/reception` render the single configured synthetic appointment
with date, time, status and workspace patient reference matching an independent
read-only backend read. NUR UI denial was observed on both routes
(**observation-only**; the HAR retains no DOM or final URL) and the native
appointment endpoint returns a captured 403. Guest redirect was observed
(**observation-only**) while the guest bootstrap 401 and native 403 are
captured. Captured native traffic shows no proxy fallback, and the list fits
phone, tablet and desktop widths. Practitioner assignment/filtering, booking,
check-in and patient-preview navigation are **not implemented**, so the full
`SIT-REC-02` variant remains **BLOCKED (partial)**. This is not a full SIT pass.

**Date:** 2026-11-14  
**Target:** `http://127.0.0.1:18081` only; `marley-sit` Compose project; nine
containers up; loopback publication only  
**Served assets observed:** `/assets/clinic_ui/ui/assets/index-CIho-KfF.js`,
`/assets/clinic_ui/ui/assets/index-BAFLUY3s.css`  
**Actors:** existing private synthetic `DOC`, `REC`, `NUR` and an anonymous
session only  
**Configured data:** exactly one allow-listed fictional appointment; no
practitioner assigned (`practitioner` null); booking/check-in not implemented  
**Private evidence alias:** `APT-20261114`  
**Private evidence:** `~/.local/state/marley-sit/evidence/appointment-ui-sit-20261114/`
(directory `700`, files `600`); manifest `evidence-manifest.json`

### 14.1 Safety and method

The captured appointment-read phase issued only read-only browser navigation and
`GET` requests. The execution as a whole was **not GET-only**: each actor
session submitted a credential login `POST` to the Frappe login method. No
appointment, patient or other business record was created, edited or deleted; no
permission, role or site configuration was changed; no container was deployed,
restarted or reconfigured; no fence action or unlock occurred; and the existing
`127.0.0.1:18080` lab was not touched. No appointment or patient write control
was activated, and no intentional appointment/patient mutation was issued.

Baseline and post-run reads returned the same single appointment row, the same
`Patient` count (`2`) and the same fence state (8 events, last sequence `8`,
identical row digest). No visible flow error appeared in DOC or REC; the NUR and
guest denials rendered as expected (both **observation-only**, see below).

**Retained-evidence scope:** the NUR and guest HARs contain request records only
— no DOM snapshot and no final URL. Their denial text and the guest redirect are
therefore **observation-only**, not established by a retained artifact. The same
HARs do retain the absence of any appointment request, and the guest HAR retains
a bootstrap response status of 401. The direct native `GET` results for NUR and
guest (403) are retained separately.

### 14.2 Exact commands and observed statuses

The appointment and patient workspace references are omitted; values are
matched against private backend evidence only.

| Command (abridged) | Observed | Evidence |
| --- | --- | --- |
| `docker ps --filter label=com.docker.compose.project=marley-sit --format ...` | exit 0; 9 containers up; only `127.0.0.1:18081` published (observation-only) | — |
| Backend read-only Python: `frappe.get_all('Patient Appointment', fields=[...])`, site-config appointment allow-list, fence SQLite `mode=ro` | exit 0; one appointment, one allow-list entry; date, time, status and patient reference captured; `practitioner` null; fence 8 events, last sequence 8 | `APT-BACKEND-BEFORE` |
| `agent-browser --session ap-doc open …/clinic/login`; set viewport 1440×900; snapshot; fill credentials; click submit (login `POST`) | exit 0; landed on `/clinic` | `APT-DOC-HAR` session |
| `agent-browser --session ap-doc network har start`; `open …/clinic/appointments`; `get url`; `har stop` | exit 0; `/clinic/appointments`; native `GET clinic_ui.api.appointments.synthetic_appointments` **200**; zero proxy-like requests | `APT-DOC-HAR` |
| `agent-browser --session ap-doc eval` extract table headers and body cells | one row; headers date, time, patient workspace reference, appointment reference, source status; values matched API/DB | `APT-DOC-ROWS` |
| `agent-browser --session ap-rec open …/clinic/login`; login; HAR start; `open …/clinic/reception`; HAR stop | exit 0; `/clinic/reception`; native appointment GET **200**; zero proxy-like requests | `APT-REC-HAR` |
| `agent-browser --session ap-rec eval` extract reception appointment row | one row; same date, time, patient reference, appointment reference and status string as DOC | `APT-REC-ROWS` |
| `agent-browser --session ap-nur open …/clinic/login`; login `POST`; HAR start; `open …/clinic/appointments`; then `open …/clinic/reception` | exit 0; UI rendered `ไม่มีสิทธิ์เข้าถึงหน้านี้` on both routes (**observation-only**; no DOM/URL retained); no appointment row or reference text; HAR contains **no appointment request** | `APT-NUR-HAR` |
| `agent-browser --session ap-guest network har start`; `open …/clinic/appointments`; then `open …/clinic/reception` | exit 0; redirect to `/clinic/login?reason=expired` observed (**observation-only**); no appointment content; HAR retains bootstrap **401** and **no appointment request** | `APT-GUEST-HAR` |
| Same-origin `GET clinic_ui.api.appointments.synthetic_appointments` as DOC / REC | **200 / 200**; one item each; `Cache-Control: no-store`; message key `items`; item keys exactly `appointment_date`, `appointment_id`, `appointment_time`, `status`, `workspace_patient_id` | `APT-API` |
| Same-origin `GET` as NUR / guest | **403 / 403**; 21-byte `{"code": "FORBIDDEN"}`; `no-store` | `APT-API` |
| Value comparison: API vs independent DB vs DOC/REC UI | Date, status and patient reference equal; time equal after zero-padding normalization; appointment reference equals the configured workspace reference; no native ID or practitioner key in the API | `APT-VALUES` |
| `agent-browser … set viewport 390×844`, `834×1112`, `1440×900`; open `/clinic/appointments`; measure document and table-wrapper widths and cell bounds | DOC: document scroll width equals viewport (`390`, `834`, `1440`); wrapper scroll width equals client width; all five cells inside viewport | `APT-RESP-DOC-390`, `APT-RESP-DOC-834`, `APT-RESP-DOC-1440` |
| Same measurement on REC `/clinic/reception` at 390×844 and 1440×900 | document scroll width equals viewport; no horizontal wrapper overflow; all cells inside | `APT-RESP-REC-390`, `APT-RESP-REC-1440` |
| HAR audit over DOC, REC, NUR and guest captures | zero requests to `/api/read`, `/api/live`, `/api/write`, `/api/auth` or `/api/files` | `APT-DOC-HAR`, `APT-REC-HAR`, `APT-NUR-HAR`, `APT-GUEST-HAR` |
| Backend read-only read after all navigation | exit 0; same one appointment row; `Patient` count 2; fence 8 events, last sequence 8, identical row digest | `APT-BACKEND-AFTER` |

**Time presentation:** the raw DB value is `9:30:00`; the API and both UIs render
`09:30:00`. The difference is zero-padding only, not a different time. Date and
status render byte-equal to the backend.

### 14.3 Results and scope

| Check / variant | Result | Scope |
| --- | --- | --- |
| DOC `/clinic/appointments` read-only list | **PASS** (scope-limited) | One row; date, time, status, patient and appointment references matched backend/API. No practitioner filtering, booking, check-in or patient-preview link |
| REC `/clinic/reception` appointment list | **PASS** (scope-limited) | Same single row and values as DOC; read-only notice rendered |
| NUR denial on appointments and reception | **PASS** (mixed evidence) | Direct native `GET` 403 is retained. The UI permission-denied state is **observation-only**; the HAR retains no DOM/URL, only the absence of an appointment request |
| Guest denial | **PASS** (mixed evidence) | Guest bootstrap 401 and direct native `GET` 403 are retained; no appointment request appears. The sign-in redirect is **observation-only** (no DOM/URL retained) |
| Native-only traffic / no proxy fallback | **PASS** | Captured sessions show the native appointment method where a read occurred; zero legacy proxy paths in all four HARs |
| Phone / tablet / desktop layout | **PASS** (scope-limited) | DOC 390/834/1440 and REC 390/1440: document width equals viewport; no table-wrapper overflow; cells inside viewport |
| Field safety | **PASS** | Item keys are exactly the five public fields; no native appointment/patient ID and no practitioner field |
| Data preservation / fence | **PASS** | Appointment row, `Patient` count and fence unchanged; no intentional appointment/patient mutation was issued |
| `SIT-REC-02` full variant | **BLOCKED** (partial) | Requires viewing by practitioner/time/status and opening a patient preview. Practitioner assignment/filter and preview navigation are not implemented; only the read-only list subset was exercised |
| `SIT-INT-06-H` appointment portion | **BLOCKED** (partial) | Only the appointment read operation was tested; other operations and role-change races were not |
| Booking / check-in / cancellation variants | **NOT RUN** | No booking, check-in or cancellation UI is implemented; no such write was attempted |

**Result for this section:** 8 concrete checks passed within their stated scope;
two of them (NUR and guest denial) combine a retained native 403 with
**observation-only** UI state. The full `SIT-REC-02` variant remains **BLOCKED
(partial)** and booking/check-in variants are **NOT RUN**. Overall SIT status
remains partial; no full appointment, reception or end-to-end workflow pass is
claimed.

### 14.4 Findings and limitations

- The read-only list exposes no practitioner value and no patient-preview link;
  the visible notice states that booking and check-in are unavailable. This is
  consistent with the unimplemented practitioner/booking scope, not a hidden
  failure.
- NUR receives a permission-denied UI state before any appointment request, but
  that UI state is **observation-only** (no DOM/URL retained); the HAR retains no
  appointment request and the direct native `GET` is a retained 403. Guest
  redirect is likewise **observation-only**; the guest HAR retains bootstrap 401
  and no appointment request, and the direct `GET` is 403.
- Execution was **not GET-only**: each authenticated session submitted a login
  `POST`. The GET-only statement applies to the captured appointment-read phase.
  No intentional appointment or patient mutation was issued.
- The appointment time is normalized to `09:30:00` in the API/UI while the raw
  DB field is `9:30:00`. Semantically equal; no timezone conversion was applied.
- HAR entries do not retain response bodies; API bodies were captured separately
  by same-origin `GET`. No screenshot was captured or published.
- The list is not date-scoped: the configured appointment is dated `2026-11-20`,
  which is not the SIT date, so the "today" condition of `SIT-REC-02` was not
  verified.
- Only one configured appointment and one allow-listed patient exist. Empty,
  cancelled/no-show, out-of-scope, stale, paginated and multi-row variants were
  not exercised.
- No credential, cookie, session token, patient name, workspace patient or
  appointment reference, native ID or field value appears in this section.

**Section conclusion:** the deployed native read-only appointment slice renders
correct date, time, status and patient/appointment references for DOC and REC,
uses no proxy fallback, fits the tested phone/tablet/desktop widths, and leaves
the appointment row, patients and write fence unchanged. The native endpoint
returns a captured 403 for NUR and guest; their UI denial text and the guest
redirect were observed but are **observation-only**, not retained artifacts.
Practitioner and booking/check-in scope is absent, so the appointment reception
SIT variant is partial, not passed.

## 15. Native appointment-write UI SIT (2026-11-15)

**Status:** **PASS for the scoped booking/reschedule/check-in variants; partial,
not a full catalogue pass.** The first two attempts failed closed (403, then a
diagnosed 409 revalidation defect) and are kept below as history. After operator
reconciliations and the typed-revalidation fix, the final run completed one
booking, one reschedule and one same-day check-in through the native UI with four
committed receipts, four committed acknowledgement barriers, `tabVersion` +2,
zero side-effect documents and an untouched fixture. Cancellation and no-show are
**not in scope**, the doctor-role booking surface is **absent** in this build
(recorded gap), and one phone-width overflow was measured and left unfixed.

**History:** attempt 1 stopped on the first write (403); attempt 2 stopped on the
first write again with a diagnosed post-insert revalidation defect (409); the
final run is §15.6.

**Date:** 2026-11-15
**Target:** `http://127.0.0.1:18081` only; `marley-sit` Compose project; loopback
publication only
**Deployed image:** `marley-sit/backend:16.6.1` = `sha256:5b5a3b4a…` (backend,
frontend, websocket and both queue workers after the pre-step recreate)
**Actors:** existing private synthetic `REC` only for the write attempt; the
`DOC` session was **not** used to write
**Served assets observed:** `/assets/clinic_ui/ui/assets/index-BO2XFM0k.js`,
`/assets/clinic_ui/ui/assets/index-BT7iXnX7.css`
**Site date:** `2026-11-15` before and after the attempt (no rollover)
**Private evidence alias:** `AWS-20261115`
**Private evidence:** `~/.local/state/marley-sit/evidence/appointment-write-sit-20261115/`
(directory `700`, files `600`); manifest `evidence-manifest.json`, 27 files

### 15.1 Safety, pre-conditions and the stop condition

Pre-conditions were verified before any write: the Compose project resolved to
`marley-sit` with only `127.0.0.1:18081` published; both queue workers were
still running an older image and were recreated onto the deployed image
(`5b5a3b4a…`) **before** the write; the scheduler container was left untouched
and still reports `Scheduler is disabled for site marley-sit.local`; the fence
read through `sqlite3` in `mode=ro` showed **8 events, last sequence 8, zero
unresolved**, with a single anchored deployment at epoch `1`.

One booking was attempted: one form fill, **one submit, no retry**. The response
was a fixed 403 denial. Per the agreed stop protocol all further writes were
abandoned immediately — no retry, no unlock, no re-key, no reconciliation, no
record deletion, no second actor write, no deploy and no commit. The client-side
appointment write lock, which the transport sets **before** dispatch and clears
only after a successful acknowledgement, was **preserved** and not cleared; the
booking control stays disabled as a result.

Post-attempt read-only verification: `Patient Appointment` count `1`, the
existing fixture `HLC-APP-2026-00001` unchanged in `status` (`Scheduled`) and
`modified` (`2026-11-14 20:41:35.544020`), `Patient` count `2`, `tabVersion`
total `5` (unchanged), and unchanged counts for `Event` (`0`), `Fee Validity`
(`0`), `Customer` (`1`), `Contact` (`3`), `Address` (`0`), `Sales Invoice` (`0`),
`Service Request` (`0`) and `Patient Insurance Policy` (`0`). No Event, Fee
Validity, Customer, Contact, Address, Invoice, Service Request or insurance row
was created by this run. The appointment naming series still stands at `1`, so
no document number was consumed.

The fence moved from 8 to **10 events** with **two unresolved keys**: sequence 9
is the reception actor's `book` claim in state `DISPATCHED` with no receipt, and
sequence 10 is that operation's acknowledgement barrier in state `DISPATCHED`
(recorded under the barrier actor). There is no `COMMITTED` receipt and no
`COMPLETED` barrier, so the unresolved check reports both keys and blocks the
site, and every later appointment
write on the site fails closed. Because the fence is not ready, the advertised
capability matrix now shows `write.appointment.book`,
`write.appointment.reschedule` and `write.appointment.checkin` as `read_only`
for DOC, REC **and** NUR, and the reception UI renders
"การจองไม่เปิดใช้งานสำหรับบัญชีนี้" ("booking is not enabled for this account").
This is the expected consequence of the unresolved claim, not a second fault.

### 15.2 Exact commands and observed statuses

The workspace patient alias, the appointment alias and all native identifiers
are omitted; values are compared against private evidence only.

| Command (abridged) | Observed | Evidence |
| --- | --- | --- |
| `docker compose -p marley-sit -f deploy/local-sit/compose.yaml ps` / `config` | exit 0; project `marley-sit`; one published port, host IP `127.0.0.1`, `18081` | — |
| `docker compose … exec -T scheduler bench --site marley-sit.local scheduler status` | exit 0; `Scheduler is disabled for site marley-sit.local` | — |
| `docker compose … exec -T redis-queue redis-cli` `LLEN`/`SCARD` | exit 0; queues `default`/`short`/`long` = 0, `rq:started` = 0 before the recreate | — |
| `docker compose … up -d --no-deps --force-recreate queue-short queue-long` | exit 0; both containers recreated and started; container image now `sha256:5b5a3b4a…`, equal to `marley-sit/backend:16.6.1` | `post-recreate-workers.txt` |
| Backend read-only Python: site config keys, `nowdate()`, appointment/patient reads, baseline counts, fence `sqlite3 mode=ro` | exit 0; site date `2026-11-15`; one appointment; fence 8 events, 0 unresolved | `preflight-before.json` |
| `agent-browser --session aws-rec open …/clinic/login`; fill credentials; submit; `get url` | exit 0; landed on `/clinic` (**login POST observation-only**: the HAR starts after authentication) | — |
| `agent-browser … network har start`; `open …/clinic/reception`; `wait --load networkidle` | exit 0; reception form and list rendered; capability `write.appointment.book` advertised enabled; patient choice restricted to the single allow-listed candidate | `rec-book-403.har` |
| Open the booking form; select the allow-listed patient; set date `2026-11-20`, time `11:00`; snapshot to confirm `20 / 11 / 2026 11:00` before submit | exit 0; form values confirmed in the accessibility snapshot | — |
| **One** click on confirm (`POST /api/method/clinic_ui.api.appointments.book`) | **403**, 21-byte body `{"code": "FORBIDDEN"}`, `Cache-Control: no-store`; **no retry** | `rec-book-403.har`, `rec-book-result.json` |
| Read the live DOM for status/alert text after the submit | two alerts: the lock notice and the refusal notice; no success text; no alias shown | `rec-book-result.json` |
| `agent-browser … network har stop` | exit 0; 10 entries retained | `rec-book-403.har` |
| HAR audit | 5 clinic API requests, **zero** requests to `/api/read`, `/api/live`, `/api/write`, `/api/auth`, `/api/files`; POST body keys exactly `appointment_date`, `appointment_time`, `operation_key`, `workspace_patient_id`; no response bodies retained | `rec-book-403.har` |
| Backend read-only Python re-run (same script as preflight) | exit 0; appointment count, patient count, fixture `status`/`modified`, `tabVersion` total and all side-effect counts unchanged; fence 10 events, 1 unresolved | `verify-after-403.json`, `verify-summary.json` |
| Fence `sqlite3 mode=ro` over sequences 9-10 | `DISPATCHED` claim (reception actor, kind `book`, no receipt, no reason) and `DISPATCHED` barrier; no terminal event | `fence-new-events.txt` |
| Backend read-only gate probes per actor: deployment approval, boundary snapshot, side-effect settings, patient resolution, appointment-type mode, create permission, site date; repeated after `reset_native_read_snapshot()` | all values favourable for `REC` and `DOC` before **and** after the snapshot reset | `gate-probe.json`, `reset-probe.txt` |
| Server-side capability computation for DOC/REC/NUR after the stop | all three roles `read_only` for book/reschedule/check-in (fence not ready); NUR had no appointment write capability at any point | `capability-matrix.json` |
| `agent-browser … set viewport 390 844`; reception and appointment detail measured | reception: document scroll width `390` = viewport, no horizontal overflow; detail: `390`, card right edge `374`, widest row `353`; **booking form not measurable** because the preserved lock disables the open-form control | `rec-reception-locked-390.png` |

### 15.3 Results and scope

| Check / variant | Result | Scope |
| --- | --- | --- |
| Pre-step: workers on the deployed image, scheduler paused, fence ready | **PASS** | Workers equal `sha256:5b5a3b4a…`; scheduler reports disabled; fence 8 events, 0 unresolved before the write |
| Booking through the native UI (reception) | **FAIL (stopped)** | One submit, HTTP 403, no record, no receipt, no ack |
| Reschedule through the native UI | **NOT RUN** | Blocked by the stop protocol and by the unresolved fence |
| Same-day booking and check-in (doctor) | **NOT RUN** | Blocked by the stop protocol and by the unresolved fence |
| Receipt discovery / alias recording for the new booking | **NOT RUN** | No receipt exists to discover |
| `tabVersion` +2 after reschedule and check-in | **NOT RUN** | Expected deltas did not arise; total stayed `5` |
| Fence growth to ~24 events with four receipts and matching barriers | **NOT RUN** | Only the two events of the failed claim exist; the fence is unresolved |
| Cancellation / no-show | **NOT IN SCOPE** | Not requested, not implemented on this surface |
| `SIT-REC-04` (booking/reschedule/check-in scopes) | **BLOCKED (partial)** | Only a single booking attempt was executed, and it failed |
| Native-only traffic / no proxy fallback | **PASS** (scope-limited) | The retained HAR shows the native method calls and no legacy proxy path |
| Phone-width layout | **PASS** (scope-limited) | Reception and detail at 390 px; the booking form could not be measured because the write lock keeps it closed |
| Data preservation | **PASS** | Appointment and patient rows, fixture version and all side-effect counts unchanged |
| Capability matrix, `NUR` appointment writes | **PASS** (observation at stop state) | `read_only` for book/reschedule/check-in |
| Full appointment-write catalogue | **NOT PASSED** | Not executed |

### 15.4 Findings, limitations and blockers

- **Blocker.** The first and only booking write was refused with a fixed 403
  while the write fence still recorded a claimed operation with no terminal
  event. Read-only replays of every gate the endpoint consults — deployment
  marker, boundary snapshot, appointment side-effect settings, patient
  resolution, appointment-type booking mode, create permission and site date —
  evaluate favourably for the reception and doctor roles, before and after the
  same snapshot reset the endpoint performs inside the fence. The failing check
  is therefore request-scoped and could not be reproduced read-only; no
  traceback was retained (`Error Log` is empty and the container log carries no
  request error). The exact refusing line is **not established**.
- **Consequence.** The site-wide fence is unresolved and every appointment write
  now fails closed, including the reschedule, same-day booking and check-in
  steps of this run. Operator reconciliation of the fence is required before any
  further attempt; this run deliberately did not unlock, re-key, reconcile or
  delete anything.
- **Lock preservation.** The browser-side appointment write lock is set before
  dispatch by design and is cleared only after an acknowledgement. It remains
  set, so the reception booking control is disabled and no retry can be issued
  from the same session.
- **Evidence mix.** The HAR retains request records only — no response bodies,
  no DOM snapshot and no final URL. Response bodies quoted above came from
  separate same-origin reads and from the private JSON captures. The login
  `POST` and the pre-request navigation are **observation-only** (the HAR starts
  after authentication); the post-submit alert text, the disabled booking
  control and the header notice that company context is unavailable were read
  from the live DOM and one **private** screenshot, not from retained HAR
  artifacts.
- **Secret and data hygiene.** No credential, cookie, session token, CSRF token,
  personal name, appointment alias or native identifier appears in this section.
  The retained HAR carries no response bodies; the evidence directory and its
  files stay at `700`/`600`.
- **Environment notes.** A shell notice on the reception page reported that
  company context is unavailable; the same page still rendered the appointment
  list and the booking control, and the `Company` read check passed for both
  roles, so this is recorded as an observation, not a diagnosed fault. The
  interface reverts to the Thai locale on reload; label text differs from the
  English rendering used in §14 while the underlying controls are the same.
- **Unchanged scope.** Cancellation, no-show, practitioner assignment, budget
  exhaustion, stale-version, conflict and role-change races were not exercised.
  The stale-version state was never intentionally triggered.

**Section conclusion:** the deployed native appointment-write slice accepted the
form, dispatched exactly one booking request and refused it with a fixed 403
without creating a record, then left the write fence unresolved. The site is now
closed for appointment writes until an operator reconciles the fence. The
booking variant is a failed single attempt, reschedule and check-in were not
run, cancellation and no-show are out of scope, and the full catalogue is not
passed. No data was changed, nothing was deleted, and no credential or native
identifier was published.

### 15.5 Resume attempt after the operator reconciliation (2026-11-15, evening)

**Status:** **BLOCKED again, on the same first write.** After the approved
operator reconciliation of the fence and a rebuilt image, the reception booking
was submitted once and failed closed with HTTP **409** and a durable
`UNKNOWN` fence terminal whose recorded reason is *"inserted Patient Appointment
failed closed-world revalidation"*. No record was created. The write path itself
is now diagnosed: the post-insert revalidation compares **typed** date/time
values read back from the database against **formatted string** expectations, so
every booking fails it regardless of input. Reschedule, the doctor-role same-day
booking and check-in were **not run**; the fence is left unresolved again and the
site is closed for appointment writes.

**Approved operator actions carried into this run:** fence reconciliation (no
action taken by this task; re-verified afterwards) and the redeploy of
`marley-sit/backend:16.6.1` = `sha256:f09a2522…`.

**Re-verified before writing (read-only):**

| Check | Observed |
| --- | --- |
| Container images | backend, websocket, `queue-short`, `queue-long` = `sha256:f09a2522…`; scheduler still on the previous image and still reports `Scheduler is disabled for site marley-sit.local` |
| Fence store | `sqlite3 mode=ro`: 8 events, last sequence 8, **0 unresolved keys**; store file `sha256:fd605449…`; anchor epoch 1 |
| Capability matrix (server-side) | DOC and REC: `write.appointment.book`, `…reschedule`, `…checkin` = `enabled`; NUR: all `read_only`; `read.appointment` `read_only` for all three |
| Baseline data | site date `2026-11-15`; 1 appointment (`HLC-APP-2026-00001`, `Scheduled`, `modified 2026-11-14 20:41:35.544020`); 2 Patients; `tabVersion` 5; Contact 3 / Customer 1 / Event, Fee Validity, Sales Invoice, Service Request, Patient Insurance Policy, Address 0 |

**Frontend deviation (recorded, then fixed with approval).** The frontend
container had been *restarted* rather than *recreated*, so it still ran the
previous image `sha256:5b5a3b4a…`. Inside each container
`sites/assets/clinic_ui/ui` resolves to that image's own
`apps/clinic_ui/clinic_ui/public/ui`, so nginx served the older bundle while the
shell rendered by the new backend referenced the new one: the shell's
`index-B_am-lmn.js` and `index-cT_EZzUp.css` returned **404**, the previous
hashes returned 200, and the browser rendered no interactive content. With
explicit approval, one frontend-only recreate onto the already-deployed image was
performed (`docker compose … up -d --no-deps --force-recreate frontend`, exit 0,
no build, no code change). Afterwards both referenced assets return **200** and
the served bundle hash equals the backend's own build
(`58cb7dd8…`); the SPA loads, the reception page renders and the synthetic
reception login succeeds.

**The one write and its outcome.**

| Step (abridged) | Observed | Evidence |
| --- | --- | --- |
| `agent-browser --session s2-rec …` open `/clinic/login`, fill credentials, submit | exit 0; landed on `/clinic`; reception nav rendered | — |
| `network har start`; open `/clinic/reception`; snapshot | exit 0; booking card and list rendered; capability advertised enabled; single allow-listed patient candidate | `rec-book-A-attempt2.har` |
| Open booking form; select patient; set date `2026-11-21`, time `10:00`; confirm values in the accessibility snapshot before submitting | exit 0; `21 / 11 / 2026`, `10:00`, patient selected | — |
| **One** confirm click (`POST …appointments.book`) | **409**; UI shows the lock notice and the unconfirmed-result notice; **no retry** | `rec-book-A-attempt2.har`, `rec-book-A-attempt2-state.txt` |
| Fence `sqlite3 mode=ro` | 11 events: seq 9 `book` claim `DISPATCHED`, seq 10 acknowledgement barrier `DISPATCHED`, seq 11 `UNKNOWN` with reason *inserted Patient Appointment failed closed-world revalidation* | `fence-events-attempt2.txt` |
| Read-only data re-check | appointments 1, Patients 2, `tabVersion` 5, naming series still at 1, fixture `status`/`modified` unchanged, all side-effect counts unchanged | `verify-after-attempt2.json` |
| HAR audit | native method calls only; no `/api/read`, `/api/live`, `/api/write`, `/api/auth`, `/api/files`; POST body keys exactly the four public fields | `rec-book-A-attempt2.har` |

**Diagnosis of the revalidation failure (read-only, no insert issued).** A
calibration probe applied the contract's own `inserted_problem` to the **existing
fixture** with its own values. It returns `unexpected appointment_datetime`,
because a document read back from the database carries typed values —
`appointment_date` is a `date`, `appointment_time` a `timedelta`,
`appointment_datetime` and `appointment_end_datetime` `datetime` — while the
contract's expected snapshot holds formatted strings (`"… 09:30:00"`). A second
latent mismatch is visible in the same read: the string form of a `timedelta`
loses the zero padding (`9:30:00` against the normalized `09:30:00`). The same
probe shows every other reviewed field matches (status, title, end datetime,
check-in flag, naming series, patient name and sex, empty optional fields), so
the failure is deterministic for any booking and independent of the values
chosen here. The earlier naming-series defect is genuinely fixed: the prepared
document's series equals the pinned series and the fixture's stored series is
the same.

**Consequence and stop.** The operation ended in a durable `UNKNOWN` terminal
with its barrier still open, so the site-wide fence is unresolved again (two
unresolved keys) and every appointment write fails closed. The browser write lock
is preserved, no acknowledgement was attempted, no unlock/re-key/reconciliation
was performed by this task, and no retry was issued. Reschedule, the doctor-role
same-day booking and check-in remain **NOT RUN**; the 390 px booking-form
measurement remains **NOT RUN** for the same reason. A code-level fix to the
revalidation (compare typed values, or normalise both sides) plus operator
reconciliation is required before another attempt.

**Evidence mix for this sub-section.** Request records come from the retained
HAR; the 409 status, the fixed response envelope and the fence events are
retained in private JSON/text captures; the two UI notices were read from the
live DOM and are **observation-only**. No screenshot of the failed state was
taken in this attempt. The login `POST` is **observation-only** (the HAR starts
after authentication). No credential, session token, CSRF token, personal name,
appointment alias or native identifier appears in this sub-section.

### 15.6 Final run after the typed-comparator fix (2026-11-15, 21:19-21:41 site time)

**Status:** **PASS for the scoped booking, reschedule and check-in variants** —
one booking, one reschedule and one check-in completed through the native UI,
each with a committed receipt and a committed acknowledgement barrier, and the
reviewed data, version and fence assertions hold. The reception variant
`SIT-REC-04` is **partial**, not a full catalogue pass: cancellation and no-show
are out of scope, the doctor-role booking surface **does not exist** in this
build (recorded gap, see below), and only one synthetic patient and one
department/type configuration were exercised. One **responsive regression** was
found and is recorded without a code change.

**Approved operator actions logged for this run (none performed by this task
except where stated):**

| # | Action | Who | Evidence |
| --- | --- | --- | --- |
| 1 | Fence reconciliation #1 after the 403 attempt | operator (approved) | re-verified by this task: 8 events, 0 unresolved |
| 2 | Fence reconciliation #2 + typed-revalidation fix deploy (`sha256:d1e360a2…`) after the 409 attempt | operator (approved) | re-verified by this task: store `sha256:3e12df2c…`, 8 events, 0 unresolved |
| 3 | Frontend recreate onto the already-deployed image (previously restarted, not recreated) | **this task, explicitly approved** | `resume-frontend-recreate.log`; served bundle `sha256:58cb7dd8…` equals the backend build |
| 4 | Actor deviation: same-day booking B and its check-in performed by **REC** instead of DOC, because the deployed UI exposes the booking form only to REC sessions | operator (approved) | `doc-booking-surface-probe.json` |

**Pre-write re-verification (read-only):** containers on `sha256:d1e360a2…`;
frontend serving the image's own bundle; fence 8 events, last sequence 8, **0
unresolved**, store `sha256:3e12df2c…`; scheduler `disabled`; capabilities DOC
and REC `book`/`reschedule`/`checkin` = `enabled`, NUR `read_only`; site date
`2026-11-15`; baseline 1 appointment, 2 Patients, `tabVersion` 5, no side-effect
rows.

**Executed (one submit per operation, no retries):**

| Step | Actor | Observed | Evidence |
| --- | --- | --- | --- |
| Book future appointment A (2026-11-21 10:00) | REC | `POST …book` **200** + `POST …ack` **200**; success notice with the workspace reference; new row appeared in the list and in the detail read (receipt discovery) | `rec-har.har`, `rec-bookA-result.json` |
| Reschedule A → 2026-11-22 14:30 | REC | `POST …reschedule` **200** + `POST …ack` **200**; detail now `2026-11-22 14:30:00`, version advanced, success notice | `rec-har.har`, `rec-reschedule-result.json` |
| Book same-day appointment B (2026-11-15 22:00) | REC | `POST …book` **200** + `POST …ack` **200**; row created with native status `Open` (same-day controller output) | `rec-bookB-checkin.har`, `rec-bookB-result.json` |
| Check in B when the server detail reported `can_checkin` | REC | `POST …checkin` **200** (acknowledgement included); row status became `Checked In`; queue position assigned by the controller | `rec-bookB-checkin.har`, `rec-checkin-result.json` |

The booking form's check-in control renders only when the versioned detail
returns `can_checkin` true; it was absent for the fixture and for the future
appointment and appeared for the same-day booking, which is the server-flag
behaviour the variant requires. The typed comparator was exercised by
appointment A on all three canonical forms (date, time, datetime) and by B on
same-day status derivation.

**Independent backend verification after the run (read-only):**

| Assertion | Result |
| --- | --- |
| Appointment rows | 3 rows: the pre-existing fixture unchanged; A = `Scheduled`, `2026-11-22`, `14:30:00`, department/company/type as configured, owner the reception identity; B = `Checked In`, `2026-11-15`, `22:00:00`, queue position `1`, `appointment_based_on_check_in` true |
| Fixture `HLC-APP-2026-00001` | `status` `Scheduled`, `modified` `2026-11-14 20:41:35.544020` — unchanged |
| `Patient` count | 2 (unchanged) |
| `tabVersion` | 5 → **7** (+2; booking inserts add none, reschedule and check-in add one each) |
| Side effects | `Event` 0, `Fee Validity` 0, `Customer` 1, `Contact` 3, `Address` 0, `Sales Invoice` 0, `Service Request` 0, `Patient Insurance Policy` 0 — all unchanged |
| Write fence | 8 → **24 events**; four operations each with a `COMMITTED` receipt and a `COMMITTED` acknowledgement barrier; **0 unresolved keys**; kinds `book` 8, `reschedule` 4, `checkin` 4 (claim + barrier for each) |
| Capabilities after the writes | DOC and REC still `book`/`reschedule`/`checkin` = `enabled`; NUR still `read_only`; `read.appointment` `read_only` |
| HAR audit | no `/api/read`, `/api/live`, `/api/write`, `/api/auth`, `/api/files`; no native document name in any request URL or body; write methods captured are exactly 2 × `book`, 1 × `reschedule`, 1 × `checkin` plus their 4 `ack` calls |
| Scheduler | still disabled throughout |

**Responsive spot-check at 390 × 844.**

| Surface | Result |
| --- | --- |
| Reception booking form | **PASS** — document scroll width 375 within a 390 viewport, no horizontal overflow; patient select and both inputs end at 338 px |
| Appointment detail, short fixture reference | **PASS** (measured earlier: 390 = viewport) |
| Appointment detail, booking-created reference | **FAIL** — document scroll width **552** against a **390** viewport, **162 px of horizontal overflow**, caused by `.appointment-patient-id` and its inner `span` measuring **503 px** with no wrapping. Private screenshot retained; no code was changed |

**Variant outcomes.**

| Variant / step | Outcome |
| --- | --- |
| `SIT-REC-04` booking (reception, future and same-day) | **PASS** (scope-limited) |
| `SIT-REC-04` reschedule | **PASS** (scope-limited) |
| `SIT-REC-04` check-in (`can_checkin` server flag) | **PASS** (scope-limited) |
| `SIT-REC-04` cancellation / no-show | **NOT IN SCOPE** |
| Doctor-role booking and check-in | **BLOCKED (gap)** — no booking surface for DOC in this build; check-in additionally requires the booking creator, so a DOC-owned booking cannot be produced through the UI |
| Full appointment-write catalogue | **NOT PASSED** — partial coverage only |
| Phone layout (booking form, detail) | **PARTIAL** — form passes; detail fails on long references |

**Parked suggestion for the UI owner (not implemented).** Wrap long references
on the detail card, e.g. in `app/src/features/appointments/appointments.css`:
`.appointment-patient-id span { overflow-wrap: anywhere; }` (optionally
`min-width: 0` on the paragraph). This is a one-line change and was deliberately
**not** made here.

**Limitations.** A and B were both booked by the reception identity, so the
doctor-role write path is verified only through read-only capability checks and
the shared endpoints' permission gates, not end to end. The fixture, patient and
appointment-type configuration are single-valued, so no multi-patient,
multi-department, practitioner-assigned, stale-version, conflict, budget-
exhaustion or role-change-race path was exercised. Execution was not GET-only:
the four approved writes and their acknowledgements are the only mutating
requests, each submitted once. Response bodies are not retained in the HARs; the
status texts quoted above were read from the live DOM or from private JSON
captures, and the overflow screenshot is private. No credential, session token,
CSRF token, personal name, appointment alias or native document name appears in
this sub-section.

**Section conclusion (final).** With the typed revalidation fix deployed and the
fence reconciled, the scoped appointment-write variants pass: booking, reschedule
and check-in each commit exactly one receipt plus one acknowledgement barrier,
`tabVersion` advances by exactly two, the fixture, patient count and every
side-effect document stay untouched, and the site ends with no unresolved fence
keys and unchanged capabilities. Remaining gaps are the absent doctor-role
booking surface, the out-of-scope cancellation and no-show paths, and one
measured phone-width overflow on long appointment references.

## 16. UI parity wave — redeploy + read-only verification (2026-11-16)

**Scope.** The 2026-11-15 UI parity wave ("Clinic Application Design" port, all
37 designed surfaces for 7 roles — see
`docs/superpowers/plans/2026-11-15-ui-parity.md`, spec
`docs/superpowers/specs/2026-11-15-ui-parity-design.md` and gap audit
`docs/superpowers/specs/2026-11-15-ui-parity-gap-audit.md`) was completed after
this report's appointment-write sections. This addendum records the parity
redeploy and its **read-only** verification. Nothing in this wave changed any
native contract: patient/appointment/photo/stock native flows keep their
verified behavior (§§10–15), and every fixture-driven surface renders the
designed components over client-side Demo/Sample fixtures with truthful
"not connected" states wherever no Marley contract exists — appointment
cancel/no-show (§6) and clinical, pharmacy, cashier, management and admin
writes remain non-wired.

**Local verification before redeploy.** App suite `npx vitest run` = 103 files /
1216 tests passed; backend `python -m unittest discover -s tests -t .` = 864
tests OK; `tsc --noEmit` clean; repo-wide `git diff --check` clean. Two dead
artefacts from the wave folded: the retired `/clinic/finance` FinancePage and
the task-1 SurfaceShell placeholder (both removed with zero live references);
`app/e2e-marley/roles.ts`, `role-matrix.spec.ts` and
`app/e2e-live/photos-browse.spec.ts` were re-baselined at source level to the
current routes/nav canon (not executed — see limitations). The e2e write-guard
audit found only sign-in/out in the specs; save controls are asserted disabled,
never clicked.

**Redeploy (isolated `marley-sit`, approved pattern).** Preflight 22 PASS /
0 REFUSE (port 18081 only). `build:native` + `build backend` + force-recreate
of backend, websocket, queue-short, queue-long, then frontend in the same
block → image `sha256:489e4907…`; served bundle sha equals image equals host
build (js `ba32d5c9…`, css `9bbb0d68…`, asset refs all 200). Scheduler left
enabled; `verify.py` exit 0 — **26/26** (DOC: exactly the five approved writes
enabled, `write.appointment.cancel` read_only; capabilities unchanged).
Fence unchanged: 24 events, 0 unresolved; appointment budget unchanged
(books 2/2, reschedule 1/1, check-in 1/1); Patient 2, Patient Appointment 3,
`tabVersion` 7; every tracked side-effect table stayed at 0 rows created in
the run window.

**Read-only browser matrix (real identities DOC/REC/NUR only).** With HAR
capture per session: the only non-GET in all three sessions is
`POST /api/method/login`; **zero** non-GET requests to `/clinic*` or
`clinic_ui.api.*`. DOC-01 My Day, DOC-02 visit workspace (authored-encounter
demo shows the sample-no-write banner), DOC-03 procedures and DOC-04 labs
render with Demo/Sample labels and truthful zero-write copy; REC reception,
patient directory, appointments and checkout/CAS-01 render; NUR home/tasks
render. PHA-02…07, CAS-02/03, MGR-01/02 and ADM-01…05 show the designed
permission-denied panel for these roles per the design matrix. Legacy routes
all fold correctly: `/clinic/finance` → cashier (CAS/REC) or management (MGR);
`/clinic/lab/photos` → forwarded to role home for DOC/NUR, denied panel for
other roles; `/clinic/lab/photos/:id` → live-photos route. Phone-width (390px)
spot checks: booking form, appointment detail, DOC-01, DOC-04 all measure
scrollWidth = viewport (the §15.6 appointment-detail overflow is fixed in this
wave); the cashier dense table passes 834 as a wrapper-scroll design.
Locale toggle persists across reload (device-scope `clinic.locale`).

**Limitations / deviations recorded.** (1) PHA/CAS/MGR/ADM have no synthetic
identities — their surfaces are browser-unverifiable and were verified by
suite-level structure/role-guard tests only (§6 limitation unchanged). (2) NUR
patient-write capabilities `write.patient.create/update = enabled` are the
pre-existing DOC/NUR baseline; no NUR appointment write exists and none was
performed. (3) Reception queue and home work-items render an explicit
fail-closed "not supported / upstream unavailable" panel in native mode when
the live work-items contract returns no data — truthful, not a regression.
(4) The re-baselined e2e specs were not executed in this wave; their behavior
is proven by the in-browser matrix above.

**Family statuses unchanged.** Verified families (patient create/edit/view,
appointment list, appointment booking/reschedule/check-in) keep their PASS
scopes from §§10–15.6. All previously blocked families (appointment
cancel/no-show, clinical write loop, labs, pharmacy/cashier/finance/management/
admin writes) remain blocked with no backend contract — the parity UI renders
their designed surfaces over clearly labeled Demo/Sample fixtures and never
transport-writes. No SIT-family status upgrades are claimed by this addendum.

*Private evidence: `~/.local/state/marley-sit/evidence/parity-deploy-20261116/`
(dir 700, files 600, manifest sha256-verified); no credential, token or
synthetic personal data appears in this section.*
