# React UI–Marley native integration: SIT test cases

**Status: DRAFT — scenarios defined; SIT not executed.**  
**Target:** Frappe-hosted React clinic UI and Marley backend in the approved lab.  
**Purpose:** establish requirement-to-test traceability for the seven clinic roles before system integration testing (SIT). A listed case is not evidence that its feature exists, is enabled, or passed.

## 1. Sources and interpretation

1. `OpenDesign Requirements v3/Clinic_Functional_Requirements_v3.md` — primary requirements, workflows WF-01–07, forms F01–F16, localization and acceptance criteria. V3 additions supersede conflicting v2 text; unmodified v2 requirements remain applicable.
2. `OpenDesign Requirements v3/Clinic_Requirements_v3_Change_Log.md` and `Clinic_Screen_Manifest_v3.json` — changes and screen inventory. The manifest maps 37 surfaces and 97 legacy screens; it is not proof of implemented backend contracts.
3. `OpenDesign Requirements v3/Clinic_Backend_Contract_Verification_v3.md` — backend verification gates, not a verified backend contract.
4. `docs/superpowers/specs/2026-11-14-marley-integrated-lab-design.md` and `docs/superpowers/plans/2026-11-14-marley-native-first-slice.md` — native integration and lab safety boundaries.
5. `app/e2e-marley/SPEC.md` — previous proxy-based, read-only role suite. Its 52 passing cases do not establish native or write-workflow coverage.
6. `docs/assessments/2026-11-14-encounter-reconciliation-evidence.md` and `docs/2026-11-08-photo-publication-recovery-runbook.md` — unresolved outcome and photo-publication safety rules.

**Precedence:** the v3 phase amendment permits clinician-wide reads of saved photos and annotation drafts for DOC/NUR; only the draft author may edit their draft. REC photo read is deferred, even where the screen manifest lists REC. Do not infer access from navigation or from a screen's presence.

## 2. Safety, prerequisites and execution rules

- **No execution authorized by this document.** Approve the test plan, data, target, access, test window and each applicable write/permission-change category separately before execution. Production is out of scope.
- Use only explicitly identified synthetic accounts and allow-listed fictional records. Keep identifiers, credentials, cookies, screenshots, backups and evidence in approved private storage; never put them in this document or public logs.
- The new clinic endpoints must enforce the synthetic allow-list and current-user native authorization. Existing direct Marley APIs are broader by explicit lab decision: do not claim that the clinic allow-list restricts those APIs, and do not query real patient records.
- A dedicated isolated disposable environment and independently verified recovery/backup plan are prerequisites for fault injection, concurrency, financial/stock operations, authorization changes and destructive scenarios. Do not alter real patient data, delete records, clear queues, change accounts/permissions, or enable write gates without separate approval.
- **Unknown means locked:** do not automatically retry or re-key an uncertain mutation, unlock an intent, edit an operation state, or infer non-commit from a timeout, current read, ordinary HTTP status, operator assertion, or restored backup. Preserve evidence and escalate.
- Clinical writes and native registration remain blocked until their specific backend contract, operation-bound receipt, transaction/fencing and legacy-intent continuity gates are proven. Photo publication remains separately gated. Cases describe intended acceptance, not permission to bypass these gates.
- Direct HTTP is approved **only for the lab**; use synthetic accounts and recognize network exposure. Do not infer production suitability.
- Do not invent status transitions or policy. Before a status test, record **display status → source DocType → source value → verified transition/method → actor → failure state**. If any is unverified, mark the case `BLOCKED`, not pass.
- Check prerequisites per case: implemented native endpoint and permission, approved synthetic input and expected backend source, safe evidence method, applicable write gate, and independent verification of the outcome. For timing/failure cases use an approved isolated fault harness, never an uncontrolled live disruption.
- Saved-photo checks require immutable original bytes, clinician-wide authorized reads, separately versioned annotations, manual save and no fabricated success. Do not access another patient's unapproved data.
- **Expected outcome for unsupported functionality now:** fail closed and report `BLOCKED/NOT IMPLEMENTED`; a disabled button or absence of an error is not a successful business transaction.

## 3. Case format and results

Each row below is a **test case family**, with separately recorded happy (`-H`) and unhappy (`-U`) executions; where multiple unhappy conditions are listed, record a separate result for each condition and applicable role. Use IDs such as `SIT-AUTH-01-H-DOC` and `SIT-AUTH-01-U-invalid-DOC`. Parameterize actor, synthetic fixture and input values in a private execution sheet. Do not claim all variants tested from one result.

**Common steps:** (1) establish the stated role and prerequisite with an isolated synthetic session; (2) perform the indicated action through the native React UI; (3) observe UI and response; (4) independently verify the authorized Marley record, linkage, version and any side effect using approved read-only evidence; (5) record the result. For denial/failure cases verify no unintended backend change, no sensitive disclosure, and no automatic repeat. Apply only the verified contract; an unknown expected result blocks the case.

For each execution capture: ID/variant, requirement, actor(s), prerequisites and test-data alias, steps/input, expected UI **and** backend effect, actual observations, status (`NOT RUN`, `PASS`, `FAIL`, `BLOCKED`, `NOT APPLICABLE`), private evidence reference, defect/blocker and date. Separate fixture/unit/proxy evidence from native browser SIT. A `PASS` requires both the stated UI and backend assertions, not just an HTTP success.

**Baseline:** all cases below are `NOT RUN` for this SIT catalogue. Native lab smoke has demonstrated only limited DOC/REC/NUR session/directory API behavior, DOC browser directory rendering and one denied-role API boundary; it is not complete SIT. Native unsupported routes, write gates and the local undeployed logout-feedback fix must be recorded per execution.

## 4. Roles and access test dimensions

| Code | Role | Main required scope | Restrictions / evidence |
|---|---|---|---|
| REC | Reception | Registration, appointments, check-in, queue, follow-up; delegated checkout only if approved | No full clinical note by default; photo reads deferred |
| DOC | Doctor | Day/visit, canonical clinical workspace, photos, orders, review and completion | Clinical note and order actions require verified native permission |
| NUR | Nurse/treatment assistant | Assigned tasks, vitals, authorized photos, prep/lab handoff and aftercare | Optional organizational role; no author-only draft edit by another user |
| PHA | Pharmacy | Items, batches, stock, receiving, transfer and purchasing | No unnecessary clinical notes |
| CAS | Cashier | Billing queue, invoice, payment and receipt | No unnecessary clinical detail or card secrets |
| MGR | Manager | Verified operational, stock and financial dashboards/reports | Not automatically an administrator; approval actions only if authorized |
| ADM | Administrator | Setup, users/roles, schedules, masters, audit and integration readiness | No implicit clinical-data access or privilege escalation |

Run shared authentication, session, localization, authorization and navigation cases for **each of the seven**. For role-dependent behavior record both permitted and explicitly denied actors. The native first slice currently maps only DOC/REC/NUR; the four remaining roles and role grants require verified implementation, not an assumed pass. Multi-role users receive only the approved union of permissions. Where v3, manifest and actual permissions differ, raise a decision and block the affected assertion rather than granting access to make a test pass.

## 5. Shared application, security and patient cases

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-AUTH-01 | APP-01; each role | Sign in with approved role → correct identity, role, native session and role home; no password retained in UI | Invalid credentials, disabled/unmapped/ambiguous role or unavailable service → safe denial/error; no protected data; distinguish authentication from authorization |
| SIT-AUTH-02 | APP-01; each role | Sign out once → session revoked, protected cache cleared, later protected request denied | Lost/rejected response or failed reconciliation → protected UI hidden until independently checked, failure shown safely, no second POST; never reuse prior uncertain logout |
| SIT-AUTH-03 | APP-01/04; each role | Valid session resumes and deep link opens authorized page | Guest, expiry or revoked session → safe sign-in/re-auth; 403 remains distinct from 401; no stale protected rows |
| SIT-AUTH-04 | APP-01; each role | Valid same-origin token/session permits an authorized operation after approval | Missing, mismatched, stale or cross-session CSRF; cross-origin/null origin → reject before mutation; no credential leakage |
| SIT-AUTH-05 | APP-02; all roles | Switch identity or refresh role → current role/capabilities and fresh data only | Old delayed response, role revocation or logout race → cannot restore prior identity, token or cached content; no cross-user error message |
| SIT-AUTH-06 | APP-02; seven roles | Each authorized navigation item opens its permitted surface | Direct deep link/API request by unauthorized or unmapped role → server denies; hidden navigation alone is insufficient; verify multi-role union without escalation |
| SIT-AUTH-07 | APP-02/04; all | Server-provided capability enables only verified approved actions | Missing, malformed, disabled or unknown capability → fail closed, no legacy proxy fallback in native mode |
| SIT-AUTH-08 | APP-04; all | Recover from loading/empty/offline states with safe read-only retry | Backend unavailable, malformed/partial response or throttling → truthful error; no synthetic success, sensitive stack trace or mutation retry |
| SIT-APP-01 | APP-02; all | Shell/profile display correct user, company, role and verified profile data | Missing profile/company or unsupported endpoint → explicit unavailable state, no fabricated data or cross-role disclosure |
| SIT-APP-02 | APP-03; authorized roles | Search allowed synthetic patient/appointment, paginate, open matching record | No match, malformed query, unauthorized record or stale response → empty/denied safely; no unauthorized identifying/clinical details |
| SIT-APP-03 | APP-04; authorized roles | Print/export permitted synthetic record with correct scoped content | Unauthorized export, unavailable printer/backend or unverified content → denied/clear error; no unrelated data or partial false success |
| SIT-APP-04 | APP-02/04; all | Open eligible notification and follow authorized link | Wrong role, expired session, stale or unauthorized target → no disclosure/action; no fabricated notifications |
| SIT-PAT-01 | PAT-01; permitted roles | Find allow-listed synthetic patient by supported name/mobile/ID, paginate and open correct record | No match, out-of-scope patient, invalid search or permission denial → no record leak; directory and API enforce allowed scope |
| SIT-PAT-02 | PAT-02/F01; REC/authorized ADM | Create fictional patient once, verify fields, linked backend ID and duplicate-candidate handling | Missing/invalid fields, duplicate, permission denial or double-click → validation/controlled conflict, no duplicate; **blocked until native registration safety gates proven** |
| SIT-PAT-03 | PAT-02/F01; authorized editor | Edit allowed demographics/consent fields, save manually, reload and verify authoritative record | Stale version, invalid field or revoked permission → no silent overwrite; preserve unsaved input as policy permits |
| SIT-PAT-04 | PAT-03; authorized roles | Open patient profile with authorized visits, history, appointments and linked photos | Cross-patient/direct-link or insufficient permission → denied/filtered; no clinical history leaked to REC/CAS/PHA |
| SIT-PAT-05 | PAT-01/03; permitted roles | Rapidly switch synthetic patients and revisit → matching identity and latest data only | Delayed response or cache from previous patient → no wrong patient, stale image or data displayed |

## 6. Photo, annotation and privacy cases

All create/upload/save cases are blocked until the relevant publication and recovery gates are approved and verified. DOC/NUR reads are clinician-wide per phase amendment; another author's draft is readable but not editable. REC access remains deferred.

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-PHOTO-01 | PAT-04; DOC/NUR | Read saved photos for an allow-listed synthetic patient, including one outside an assigned care relationship → correct patient, visit, date and body area | REC/other unauthorized role, out-of-scope patient or direct bytes URL → denied; no existence leak |
| SIT-PHOTO-02 | PAT-04/F03; authorized actor | With approved consent and write gates, capture/upload and link original to correct synthetic patient/visit → private original and metadata persisted | Missing consent, invalid type/size, wrong patient/visit or revoked permission → no unauthorized attachment/public bytes |
| SIT-PHOTO-03 | PAT-05; DOC/NUR reader | Compare compatible before/after photos by visit/body area; side-by-side and slider preserve originals | Incompatible/missing images, wrong patient or no permission → safe refusal; no cross-patient mix |
| SIT-PHOTO-04 | PAT-05; authorized author | Draw agreed shapes/text/freehand, undo/redo and manually save a new independent annotation version → original bytes unchanged and audit/version correct | Other author attempts edit; malformed shape, stale version or save failure → deny/conflict, no original overwrite or false saved indication |
| SIT-PHOTO-05 | PAT-05; DOC/NUR | Reload/view saved annotation draft across clinician sessions → correct version visible to authorized clinician | Another author attempts to alter it or stale concurrent saves collide → preserve independent versions, report conflict and do not silently overwrite |
| SIT-PHOTO-06 | PAT-04/05; authorized actor | Fetch private photo via approved route → correct bytes, type and no-store behavior | Corrupt digest, path traversal, public file, cross-patient link or inaccessible original → deny safely without exposing bytes or paths |
| SIT-PHOTO-07 | PAT-04/05; authorized actor | Confirmed photo publication → exactly one saved result with verified original and annotation linkage | Response lost, `PREPARED`, `ABORTED`, `QUARANTINED` or otherwise unknown → retain lock/evidence, never replay/re-key or infer saved from a later read |
| SIT-PHOTO-08 | PAT-04; authorized actor | Switch TH/EN during an approved upload without losing selected file/metadata | Cancel, network failure or session expiry during upload → no false completion; preserve/lock according to operation outcome |
| SIT-PHOTO-09 | F03/WF-01; authorized DOC/NUR | Capture an approved intake photo before check-in without an encounter; later link the same original to the correct encounter without reupload or duplication | Encounter creation/link fails → original remains safely recoverable in patient/intake context; no wrong link or duplicate; REC access remains deferred |

## 7. Reception and appointment cases

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-REC-01 | REC-01; REC | Open home and authorized task tiles; navigate to corresponding real work | Unauthorized checkout/photo/clinical detail or unavailable task → hidden/denied or explicitly unavailable, no sensitive leakage |
| SIT-REC-02 | REC-02; REC | View today's synthetic appointments by practitioner/time/status and open patient preview | Empty list, cancelled/no-show, out-of-scope patient or unverified queue source → truthful empty/denied/unavailable |
| SIT-REC-03 | REC-02; REC | Check in an eligible appointment once through verified server method → one correct arrival/queue transition | Double-click, already checked in, stale/cancelled booking or unauthorized actor → no duplicate; actual backend state remains authoritative |
| SIT-REC-04 | REC-03; REC/DOC as authorized | Open day/week calendar, filter doctor/date/type and see verified availability | Full day, practitioner leave/break or backend unavailable → blocked/unavailable slots, no fictitious availability |
| SIT-REC-05 | REC-04/F02; authorized actor | Create appointment for synthetic patient with required doctor, time, type and company; verify one linked backend record | Missing fields, overlapping slot, unauthorized doctor or double submit → validation/conflict, no duplicate |
| SIT-REC-06 | REC-04/F02; authorized actor | Reschedule/edit an eligible booking and reload → one updated booking with correct audit/status | Stale version, concurrent booking, cancellation conflict or permission revoked → controlled conflict; no overwrite/duplicate |
| SIT-REC-07 | REC-04; authorized actor | Cancel an eligible appointment with required reason → verified transition and reason | Missing reason, completed/stale booking or unauthorized cancellation → reject without state change |
| SIT-REC-08 | REC-05; REC/delegated CAS | Handoff completed visit to billing and book follow-up when authorized | Incomplete consultation, unpaid bill or unauthorized payment action → accurate pending state and no premature charge/completion |

## 8. Clinical, nursing and lab cases

`Review Details` backed by `encounter_comment` is **not** a formal Clinical Note. Formal note/signature and exam fields require a separately verified contract and permission. Do not treat draft save as signature or completion.

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-DOC-01 | DOC-01; DOC | Open authorized My Day/next visit → correct patient, appointment, clinician and existing encounter | Unassigned/out-of-scope patient, no appointment or duplicate launch → no unauthorized/new duplicate encounter |
| SIT-DOC-02 | DOC-02/F05–F08; DOC | Open one canonical visit workspace; navigate overview, history, examination, note, photos, lab, medications/procedures and review | Missing source/permission or unsupported section → truthful unavailable; no second editable copy of history/exam |
| SIT-DOC-03 | DOC-02; DOC | Edit current visit history/examination in the verified authoritative location, save draft manually and reopen — **BLOCKED, not passed:** encounter writes are deferred pending a reviewed appointment-provenance and authorization contract | Invalid units, stale data or prior-history edit attempt → validation/conflict; prior history remains read-only and no unverified display field written |
| SIT-DOC-04 | DOC-02; DOC | Manually save Review Details to verified encounter field; reload same synthetic encounter → exact persisted content — **BLOCKED, not passed:** encounter writes are deferred pending a reviewed appointment-provenance and authorization contract | Concurrent stale save or post-dispatch uncertain error → no overwrite/retry; lock and safe evidence retained |
| SIT-DOC-05 | DOC-02; DOC | Save a draft, review required data and complete/sign only through approved native workflow; verify linked documents and side effects — **BLOCKED, not passed:** encounter writes are deferred pending a reviewed appointment-provenance and authorization contract | Missing required fields, unauthorized signature or backend failure → no false signed/completed status or duplicate side effect |
| SIT-DOC-06 | DOC-03/F07; DOC/authorized staff | Record procedure, actual item/warehouse/batch use and complete once → one linked verified stock movement | Expired/insufficient stock, missing warehouse, invalid units or double submit → no unauthorized/double consumption |
| SIT-DOC-07 | DOC-04/F08; authorized actors | Create native lab order and view verified result linked to correct visit — **BLOCKED, not passed:** lab ordering is deferred pending a reviewed lab/native contract | Wrong patient/order, missing required data, unauthorized reviewer or unsupported result → deny/clear error, no incorrect link |
| SIT-LAB-01 | DOC-04/F08; DOC/NUR as authorized | Track external lab ordered → sent → received with private report → doctor review, reviewer and audit — **BLOCKED, not passed:** lab ordering is deferred and lab review is cut (read-only) pending reviewed lab contracts | Missing reference/report, invalid transition, wrong patient or NUR attempting doctor-only review → reject; original report retained |
| SIT-NUR-01 | NUR-01; NUR | View assigned patient/tasks and doctor/urgency appropriate to current permissions | Unassigned patient, no tasks or permission revoked → no unrelated clinical disclosure |
| SIT-NUR-02 | NUR-02/F05; NUR | Enter vitals with patient, measurement date/time, values and units; save and reopen | Missing/invalid values, units or wrong patient → validation/denial and no wrong-patient observation |
| SIT-NUR-03 | NUR-02; NUR/authorized DOC | Complete permitted preparation, lab dispatch/upload and aftercare linked to correct visit — **BLOCKED, not passed:** lab ordering is deferred pending a reviewed lab/native contract | Incomplete checklist, unavailable permission or invalid lab transition → no unsupported status/unauthorized completion |
| SIT-CLIN-01 | WF-01/02; clinical actors | Navigate between sections and return to same verified encounter without losing manually saved data — **BLOCKED, not passed:** encounter writes are deferred pending a reviewed appointment-provenance and authorization contract | Network loss, refresh, wrong identity or ambiguous save → safe recovery/locked state; no duplicate encounter |
| SIT-DOC-08 | DOC-02/F06; DOC | Create and reload an authorized medication order with required dose/duration and conditional route/frequency; distinguish prescribed from dispensed, consumed and invoiced — **BLOCKED, not passed:** medication ordering is deferred pending a reviewed clinical/native/authorization contract | Missing/invalid dose or duration, unauthorized actor or failed save → validation/denial with no unintended dispensing, consumption or invoice |
| SIT-DOC-09 | AT-25; authorized DOC | Amend a finalized note only through verified amendment workflow; retain original and audit history — **BLOCKED, not passed:** encounter writes are deferred pending a reviewed appointment-provenance and authorization contract | Unauthorized direct overwrite or unsupported amendment → deny without changing the signed original |
| SIT-CLIN-02 | F07/F13; DOC/CAS | Complete an approved treatment consuming stock once, then invoice the linked service → source ledger still reflects exactly one consumption | Repeat completion/invoice or invoice failure → no second deduction or fabricated accounting record |
| SIT-LAB-02 | F08/WF-06; DOC/NUR | Record a verified returned/rejected specimen or report and follow the authorized correction/handoff — **BLOCKED, not passed:** lab review is cut (read-only) pending a reviewed lab review contract | Invalid transition, incorrect patient or unauthorized review → refuse with original evidence retained |

**Medication ordering is deferred (`SIT-DOC-08` remains `BLOCKED`).** The encounter draft contract refuses any nonempty medications section before the fence until a reviewed medication contract, native lifecycle and permission set exist (`docs/superpowers/specs/2026-11-18-medication-deferral-design.md`). The native surface shows medication ordering as unavailable, and the local SIT verifier asserts, for the one authenticated DOC bootstrap it reads, that `write.medication.order` stays `read_only` even when the encounter family gate is open; NUR and the other role maps still need separate authorization-safe verification. This is a safety boundary, not a pass: record `BLOCKED` for every `SIT-DOC-08` variant until the contract is reviewed and separately approved, and preserve any prior evidence rather than re-classifying it.

**Encounter and lab writes are deferred or cut (`SIT-DOC-03`, `SIT-DOC-04`, `SIT-DOC-05`, `SIT-DOC-07`, `SIT-DOC-09`, `SIT-LAB-01`, `SIT-LAB-02`, `SIT-NUR-03` and `SIT-CLIN-01` are `BLOCKED`, not passed).** In the pinned native contract a new encounter draft save closes its appointment, whereas the completion contract requires the appointment to remain open or checked in, and note authorization and other cascade risks are unreviewed. New clinic UI draft, completion, amendment and lab-ordering mutations are therefore refused before fence dispatch; an exact authorized replay of an existing committed receipt remains available for acknowledgement recovery. Lab review is cut rather than deferred (`docs/superpowers/specs/2026-11-18-encounter-deferral-design.md`). This endpoint boundary does not restrict Frappe's separate resource API or the proxy. The native surface presents these writes as unavailable, and the local SIT verifier asserts, for the one authenticated DOC bootstrap it reads, that `write.encounter.draft`, `write.encounter.complete`, `write.encounter.amend`, `write.lab.order` and `write.medication.order` stay `read_only` even when the encounter family gate is open, and that the ungated `write.lab.review` stays `read_only`. NUR and the other role maps are not logged in by the verifier; they need separate authorization-safe verification rather than extra live logins. This is a safety boundary, not a pass: record `BLOCKED` for every affected variant until the contracts are reviewed and separately approved, and preserve any prior evidence rather than re-classifying it.

## 9. Pharmacy, stock and purchasing cases

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-PHA-01 | PHA-01/02; PHA | Open stock home and search item details, UOM, tracking and warehouse from verified Marley data | Unavailable source, unauthorized master edit or service item as stock → truthful state, no fabricated balances |
| SIT-PHA-02 | PHA-03; PHA/MGR | Filter positive on-hand batches by warehouse and expiry in 7/30/90 days → quantities and dates match source | Expired/zero balance, wrong warehouse or duplicate bundle → excluded/flagged correctly; no double count |
| SIT-PHA-03 | PHA-04; PHA/MGR | View warehouse/batch balance and chronological ledger linked to source transactions; authorized export | Unauthorized export or missing source → deny/unavailable; immutable ledger is not editable |
| SIT-PHA-04 | PHA-05/F10; PHA | Receive approved synthetic PO items with supplier, UOM, warehouse, batch and expiry → one submitted receipt and correct balance | Invalid quantity/UOM, expired batch, duplicate submit or permission denial → reject without extra receipt/movement |
| SIT-PHA-05 | PHA-06; PHA/authorized DOC | Transfer/issue permitted stock with before/after balances and transaction linkage | Insufficient stock, same/invalid warehouse, unauthorized issue or double action → no duplicate/negative movement beyond approved policy |
| SIT-PHA-06 | PHA-06; explicitly authorized ADM | Run approved reconciliation through verified backend workflow and audit | Unverified reconciliation contract or unauthorized actor → blocked, no direct ledger/status edits |
| SIT-PHA-07 | PHA-07/F12; PHA | Select low-stock item → draft PO with correct supplier/item/quantity, never auto-submit | Missing supplier, invalid quantity, stale stock or no permission → validation/conflict; no submitted PO |
| SIT-PHA-08 | PHA-07; PHA/MGR | Partially receive PO, reopen progress → outstanding quantity equals actual submitted receipts | Over-receipt, duplicate receipt or failed submission → correct unchanged outstanding balance and no phantom stock |

## 10. Cashier and manager cases

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-CAS-01 | CAS-01; CAS/delegated REC | View eligible completed synthetic visits and existing charges with minimum billing identity | Unfinished visit, unauthorized actor or unrelated clinical history → excluded/denied or safely marked pending |
| SIT-CAS-02 | CAS-02/F13; CAS | Preview and submit invoice with verified customer, services, price, tax and permitted discount → one linked invoice | Duplicate charge, invalid discount, stale price or missing customer → controlled validation/conflict, no duplicate invoice |
| SIT-CAS-03 | CAS-03/F14; CAS | Record authorized full/partial cash or card-method payment through verified Payment Entry → correct outstanding balance and receipt | Overpayment/invalid amount, unsupported partial payment, duplicate click or unauthorized role → no duplicate entry; never accept/store PAN or CVV |
| SIT-CAS-04 | CAS-02/03; authorized approver | Perform approved void/amend/refund workflow with reason and audit | Unauthorized user, invalid state or unverified refund method → denied; original accounting evidence not erased |
| SIT-MGR-01 | MGR-01; MGR | Filter dashboard and drill down to synthetic source records; invoiced, collected and outstanding reconcile independently | Empty/missing source or unsupported metric → accurate empty/unavailable, never fabricated totals or treat invoiced as collected |
| SIT-MGR-02 | MGR-02; MGR | View appointments, workload, treatment, finance, inventory and purchasing reports with dates/denominators and permitted export | Unauthorized source, inconsistent KPI or invalid date range → deny/error; no overly broad patient/financial data |
| SIT-MGR-03 | MGR-01/02; MGR | Check near-expiry and inventory-value metrics against verified positive stock and valuation policy | Missing valuation policy, zero quantity or duplicate source → unavailable/excluded, not invented value |

## 11. Administrator and configuration cases

Account/role/permission changes require separate approval and isolated synthetic test principals; these cases are not authority to change lab accounts.

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-ADM-01 | ADM-01; ADM | See verified setup/readiness warnings and links for unconfigured services | Missing health source or failure → unknown/degraded, never a fake healthy state |
| SIT-ADM-02 | ADM-02; authorized ADM | In approved isolated environment create/edit synthetic user role/profile and practitioner link, then verify effective access | Unauthorized change, invalid link or privilege escalation attempt → denied; existing unrelated permissions unchanged |
| SIT-ADM-03 | ADM-03/F15; ADM | Configure practitioner working hours, break, leave, room/service and appointment duration from verified schema | Overlapping schedules, leave or conflicting booking → server rejects invalid slot; no saved overlap |
| SIT-ADM-04 | ADM-03/04; ADM | Configure authorized synthetic services, procedure/lab templates, warehouses, price lists and payment modes; reopen source values | Invalid schema/required field, unauthorized actor or unsupported setting → validation/deny; no fabricated master record |
| SIT-ADM-05 | ADM-05; ADM/authorized MGR | View verified integration metadata, redacted errors, audit and backup/readiness status | Missing evidence, inaccessible audit or sensitive content → truthful unavailable/redacted; no patient secrets or false health |
| SIT-ADM-06 | ADM-02/04; ADM | Change approved default locale or role configuration and verify it applies only to intended scope | Ambiguous/shared role, denied change or stale configuration → fail closed; no unrelated access granted |

## 12. Cross-cutting localization, quality and recovery

| ID | Requirement / actor | Happy path: action → expected | Unhappy path: action → expected |
|---|---|---|---|
| SIT-I18N-01 | L10N-01–15; all | Thai is default; switch to English and back, persist verified preference across authorized session | Unsupported/missing translation or session change → safe fallback; no clinical free-text translation or silent calendar conversion |
| SIT-I18N-02 | V3-AT-11; REC/NUR/DOC | Switch TH/EN mid-queue and during approved draft/upload → labels/status translate, input and IDs remain | Backend error or language switch during uncertain operation → retain safe state; no duplicate request or fabricated result |
| SIT-UX-01 | NFR/AT; all | Exercise keyboard, focus, labels, error announcements and reduced-motion behavior on key workflows | Validation error, modal close or session expiry → focus and accessible error remain usable; no keyboard trap |
| SIT-UX-02 | V3-AT-13; REC/PHA | At 390, 834 and 1440 px complete approved key navigation and form flows with no clipped mandatory content | Long labels, zoom, empty/error state or overflow → all required controls remain accessible |
| SIT-UX-03 | APP-04; all | Edit a supported draft and intentionally navigate after saving → correct persisted state | Navigate/back/close with dirty input or stale edit → explicit keep/discard/conflict handling; no silent loss |
| SIT-INT-01 | Native integration; all | Navigate `/clinic` and permitted deep links; same-origin native assets and API resolve | Guest route, missing/malformed asset manifest or unsupported endpoint → safe sign-in/503/unavailable; `/api`, `/desk` and existing Marley routes unaffected |
| SIT-INT-02 | Data security; all | Only minimum permitted synthetic fields returned, private responses appropriately no-store | Malformed DTO, extra sensitive fields, logs/errors containing credentials or cross-patient data → fail/deny and report without copying secrets |
| SIT-INT-03 | Write safety; authorized actors | Same operation key and same payload resolve to one verified backend outcome where protocol supports it | Different payload with same key, concurrent keys, double submit or lock conflict → reject/serialize; no duplicate document or side effect |
| SIT-INT-04 | Write recovery; authorized actors | Recover an independently proven terminal operation from a durable operation receipt after reload/restart | Timeout, dropped response, post-dispatch 4xx/5xx/redirect, crash after commit or status-store failure → unknown lock persists; no automatic retry or unlock |
| SIT-INT-05 | Restore safety; operator | In an approved isolated restore rehearsal, verify originals, records, operation receipts and consistent recovery | Missing/replaced legacy intent DB or restored older state → absence is not proof of non-commit; writes remain blocked pending independent fencing/outcome proof |
| SIT-INT-06 | Authorization; all | Verify native server denies unauthorized role on each applicable patient/photo/appointment/clinical/finance/stock/admin operation | User changes role/session while request is pending → no stale authorization, unintended mutation or data leakage |
| SIT-INT-07 | Audit/privacy; authorized actors | Verify authorized operation produces appropriate linked audit evidence without revealing secrets | Failure/denial/logging → no credentials, cookie, clinical free text or unauthorized data in error, telemetry or test report |
| SIT-INT-08 | End-to-end handoff WF-01 | With separately approved gates, follow synthetic patient from registration → appointment → check-in → clinical work → authorized billing/payment and follow-up; verify each linked Marley document and actor | Missing/denied/failed intermediate step or unknown outcome → stop at correct boundary, no later fabricated completion or duplicate transaction |
| SIT-INT-09 | Optional V3-AT-14–16 (P1) | If approved and implemented, verify Kanban/request inbox, token/wait analytics, value-at-risk and service revenue against same verified source records | Absent contract/data → marked unavailable; no duplicate booking, invented wait time or fabricated valuation |
| SIT-INT-10 | Encounter reconciliation; authorized operator | In an isolated proof harness accept a terminal receipt only when operation, site, epoch, actor, target, kind, payload and base version match | Alter each binding independently or use unauthorized resolver → reject, preserve lock and audit; no mutation |
| SIT-INT-11 | Encounter reconciliation/fencing | Prove a terminal no-commit fence blocks an independently delayed original request | Race resolution against execution or a second operator; fail audit/CAS persistence → at most one valid outcome; keep unknown locked when proof fails |
| SIT-INT-12 | Encounter reconciliation | Resolve a proven operation without changing subsequent shared edits | Concurrent later edit, stale base version or unproven receipt → preserve later edits, deny unsafe resolution, no retry |
| SIT-REC-09 | REC-01/02, V3-AT-01/02 | Reconcile date-filtered reception KPI counts to source records and execute an approved no-show transition | Missing source, invalid transition or unauthorized actor → unavailable/deny; no invented counts or status |
| SIT-PAT-06 | PAT-03, V3-AT-04 | Show latest authorized vital with source, measurement date and units | No vital, stale source or no permission → display missing/denied, never an invented zero |
| SIT-CAS-05 | CAS-03; CAS | Reconcile same-day cash/card summaries and drill down to posted synthetic payment entries | Missing/unauthorized entry or unposted payment → exclude/deny; do not confuse invoice with collection |
| SIT-I18N-03 | L10N-01–15; all | Verify supported Thai/English Unicode search, print language and per-user preference across approved devices | Missing translation or different user → safe fallback and no preference or patient-data leakage |
| SIT-FORM-01 | F04/F09/F11/F16; authorized roles | Verify check-in/checkout, inventory/batch, stock movement and shared form states against each authoritative field/transition | For each required/conditional field, denial, loading, validation, unsaved, error and stale state → correct controlled response; no unintended write |
| SIT-WF-01 | WF-04/F07/F10–F12; PHA/DOC | In approved isolation, complete low-stock review → supplier/PO → partial receipt and batch → balance → room transfer → procedure consumption, reconciling the ledger and remaining PO | Invalid/duplicate receipt, transfer or interrupted step → stop, preserve outstanding quantity and do not duplicate stock |
| SIT-WF-02 | WF-07/L10N; authorized clinician | Log in in Thai → enter Thai note text → switch to English → continue → Save Draft → log out/in → verify language preference → reopen note with unchanged clinical content | Expiry, unavailable translation or uncertain draft save → safe recovery/fallback, no lost content falsely reported as saved, no duplicated operation |
| SIT-INT-13 | Reconciliation atomicity; isolated proof harness | Demonstrate mutation and its committed operation receipt share one native transaction, including absent-record races serialized through the same fence | Crash/failure at each boundary or competing absent-record launch → no mutation without matching durable receipt or duplicate launch; unproven outcome stays locked |
| SIT-INT-14 | Reconciliation fencing/restore; isolated proof harness | Demonstrate hooks/background effects stay within the proven transaction/fence, and an independently anchored restore detector recognizes rollback | Deferred effect escapes fencing or epoch is restored with the same database → do not infer safety from that epoch; keep affected writes blocked |

## 13. Traceability and completion gates

- **Surface coverage:** APP-01–04, PAT-01–05, REC-01–05, DOC-01–04, NUR-01–02, PHA-01–07, CAS-01–03, MGR-01–02 and ADM-01–05 are represented above. Validate the complete v3 screen manifest and inherited v2 requirements against this catalogue during review; an unlisted inherited behavior is a coverage gap, not implicitly passed.
- **Forms and journey coverage:** F01–F16 and WF-01–07 are referenced by families. Before execution, expand each field/required/conditional/validation rule and each workflow transition from the authoritative specifications into independently reportable variants. Record unverified backend mappings as blockers.
- **Acceptance coverage:** map every AT-01–25 and V3-AT-01–13 to one or more specific executed variants in the execution sheet. P1 AT-26 and V3-AT-14–16 are separately reported; P2 out-of-scope items are not release-required. No release claim until the mapping has no unexplained omissions.
- **Role coverage:** seven distinct role identities and required positive/negative actor variants; NUR is organizationally optional but its scenarios still require a recorded test or an approved applicability decision.
- **Integration coverage:** native browser + Marley evidence for each applicable case; fixture-only, proxy-only, unit-test and API-only observations remain separate.
- **Exit criteria:** approved requirements/contracts and safe test data; all release-required variants passed or formally dispositioned; no open critical safety/security defect; unknown writes locked; permissions and audit verified; backup/restore and deployment procedures verified as separately approved. No complete-integration or production-readiness claim while major native workflows or contracts are blocked.

### Open decisions and dependencies

1. Apply the v3 phase amendment over conflicting screen-manifest/photo role entries; obtain approval before any future REC photo access.
2. Verify each native DocType, field, method, status mapping, role permission and side effect; do not assume a designable field exists.
3. Complete native role mapping and workflow implementation for PHA/CAS/MGR/ADM and unsupported DOC/REC/NUR screens.
4. Prove native registration and clinical/photo write safety, legacy-lock continuity and operation-specific terminality before enabling mutations.
5. Approve safe isolated data and operator-controlled fault-injection/permission-change procedures; do not use existing production or real-patient data.
6. Restore-test backups on an isolated target and resolve credential/session exposure separately.
7. Reconcile the catalogue with every individual requirement, acceptance criterion, form field and workflow transition before declaring coverage complete.

### Independent review and OpenDesign evidence

Oracle reviewed the initial catalogue and found incorrect form references and missing medication, pre-check-in photos, finalized-note amendment, cross-workflow stock and reconciliation proof scenarios. Those specific issues are addressed above; **the independent review did not certify complete coverage**. Full requirement-to-variant, actor, field, transition and 97-legacy-mapping traceability remains outstanding.

The linked **Clinic Application Design** project and its v3 handoff provide design references, not evidence of native Marley behavior. Relevant project artifacts include `handoff/clinic-v3-acceptance.json` (acceptance proof flags and V3-AT-12/13/16), `handoff/clinic-v3-final-review.md` §4.2 and §7 (responsive claims and limits), `handoff/clinic-surface-manifest.json` (surface/role/state inventory), `handoff/clinic-actions-permissions.csv` (designed actions and role dependencies), and `handoff/clinic-backend-integration-audit.md` (prototype/backend gaps). Prototype code in `assets/js/app.js`, `clinical.js`, `operations.js` and `core-lab-v3.js` is supplementary design evidence only.

The comparison identified gaps or incomplete design evidence for unknown-outcome locks, offline/conflict states, refund approval, notification targeting and responsive layouts. Client-side visit completion, invoice/payment and stock behavior must not be assumed to match Marley transactions. The photo-role manifest conflict is overridden by the v3 phase amendment. Before SIT, compare approved rendered screens and states to the React UI and resolve mismatches; do not infer a backend contract, authorization, transaction, successful acceptance or actual responsive behavior from the prototype. The acceptance handoff marks acceptance items unproven; prior audit statements about unwired prototype code may be stale and require revalidation.
