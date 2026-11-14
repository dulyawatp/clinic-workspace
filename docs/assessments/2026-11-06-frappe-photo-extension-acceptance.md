# Frappe photo extension acceptance — preparation only

**Status:** Not executed. Local candidate reviewed; 325 standard-library tests independently passed before HTTP-harness preparation. This is not installed-site, concurrency, HTTP or production acceptance.

## Authorization boundary

Existing approval covers local source/test preparation only. Before any remote installation, obtain approval for the exact isolated deployment, site name, container/image changes, installation/migration/restart effects, synthetic records/users/sessions, test endpoints, failure injection, and cleanup/disposal procedure. A generic “continue” is not this approval.

Prefer a new disposable deployment with the recorded Frappe 16.53.0 / ERPNext 16.52.1 / Healthcare 16.6.1 versions and synthetic data only. Do not install by editing the existing `marley-health-backend-1` running container, modify `marley.local`, clone patient data, or restart shared services under the earlier read-only inspection/probe authorization. The isolated target and provisioning method are **not yet selected**.

No dependency installation, core patch, monkeypatch, commit, role activation, frontend persistence switch or real publication endpoint is authorized. Protected publication remains unsupported without a real transaction-owning caller; test-only owners do not establish that caller's correctness.

## Evidence budget and owners

| Claim | Minimum evidence | Owner |
|---|---|---|
| Local guards and harness logic | Scoped review plus one final standard-library run on reconciled source | Orchestrator |
| Installed File extension/native dispatch | Effective MRO, real permission checks, exact override/method contracts | Authorized native-test executor |
| Each native lifecycle case reaches its intended guard | One isolated native execution per manifest case, actual method identity/nonce, raw output/exit and state/byte assertions | Authorized native-test executor |
| HTTP access and cache policy | Provisioned synthetic principal/state evidence plus actual direct/API/ZIP responses, hashes/member inspection and denial-body/header assertions | Authorized HTTP-test executor |
| Native transaction/concurrency/disposal safety | Two-connection races, exact lock ownership/results, failed callbacks/commit/rollback/compensation and teardown observations | Authorized native-test executor |
| Cleanup | Exact residue inventory and verified isolated-site/private-file disposal; matching operator attestation | Authorized deployment owner |

No evidence lane may substitute fake adapters for installed behavior. Reuse valid local results; rerun affected checks if source changes. Failed required cases, missing identities, skips, missing raw logs or unconfirmed disposal block acceptance.

## Pre-execution checklist

1. Record explicit approval and deployment identifier. Verify target isolation and versions without printing credentials. Ensure there are no real patient records or production integrations. Record how the deployment and its private storage will be removed.
2. Install the app only through the approved reproducible deployment method. Record artifact digest, commands, exit statuses, migration effects and effective File MRO; no ad-hoc running-container edits.
3. Verify native CLI selection and **full output compatibility**: exact selected module/class/method, one test per execution, BEGIN/END identity/nonce and residue markers, terminal summary. Option availability alone is insufficient. Keep verification evidence tied to the runner's command-contract digest.
4. Provision only exact synthetic patients/photos/annotation versions and principal states. Cover Physician and Nursing User candidate roles; role names are not an approved live DOC/NUR mapping. Other clinician, non-clinician, Guest, disabled user, revoked owner with existing session and explicit shared non-clinician need distinct evidence. Test infrastructure setup does not grant ordinary Administrator clinician access.
5. Keep session values in access-restricted, untracked files/environment, never logs or committed fixtures. Require approved HTTPS or explicit SSH-local transport. Never send credentials over `http://10.0.100.10:8080`. Disable cross-origin redirects and unintended ambient credentials/proxy use in the client.
6. Provision the HTTP manifest only after contracts/target are verified. The manifest is operator-supplied test configuration, not proof the deployment is disposable or principals hold the claimed roles.

## Acceptance groups

### A. Isolated native policy matrix

Use `clinic_photos/tests/case_manifest.json` and its isolated runner. Run each required case in a fresh native execution; do not clear the production abort latch. The test-only owner must be active where a case intends to reach lifecycle policy; no-owner cases remain separate. Do not accept skips or assertions that merely hit an already-aborted gate.

The strict aggregator requires raw execution evidence and matching residue-disposal confirmation. Disposal is an operator attestation plus independently recorded deployment/storage evidence, not a claim the harness automatically erased protected fixtures.

### B. HTTP reads, denials and cache delivery

The local HTTP harness under preparation supports only its explicitly implemented read/download assertions. Validate its schema before use. Missing prerequisites must fail rather than skip. Do not interpret sample/dry-run output as an actual request.

Record success body SHA-256 and size, or exact ZIP member bytes; archive timestamps make whole-ZIP hashes unsuitable. Verify protected original/annotation content cannot appear in denied responses or denied ZIP members. Check `Cache-Control: no-store`, `Pragma: no-cache`, `Expires: 0`, including protected entries skipped by native ZIP permission prefilter and mixed allowed/denied archives. Include direct private URLs, recorded native download and ZIP routes, `fid`, aliases and direct `/protected/` denial.

Separately exercise the approved frontend/nginx route and accelerated response; application response headers alone do not prove nginx delivery or cache behavior. A TLS/tunnel configuration assertion alone is not transport acceptance.

### C. Native method and mutation closure — not implied by HTTP-read support

Verify bank-import override POST-only dispatch, protected-path rejection before raw read, approved conservative refusal of ambiguous protected/unrelated matches, and unchanged unambiguous unrelated handling. Finance-method calls and synthetic import setup require explicit write-producing test approval; do not invoke real imports/accounts.

Verify thumbnail/optimization denial, private-image helper rejection, actual mutation RPCs, metadata queries, share/owner/relink/publicize/delete/replace denials and unchanged byte/state assertions. An HTTP-read harness does not establish these claims.

### D. Transaction failure, concurrency and residue

Observe real MariaDB advisory ownership and documented return shapes, timeout/nesting behavior, query SQL-mode/UTF-8 HEX behavior, alias/registration races and scan costs. Test callback exceptions before lock callbacks, cleared rollback compensation, partial physical writes/deletes and connection teardown. No delayed filesystem compensation may act after a key is unlocked.

Native source inspection found ordinary request closing callbacks and job cleanup call `destroy()`, but earlier callbacks can prevent reaching it. `persistent_connections` was absent and unreferenced in inspected Frappe Python; that is **not** a disposal guarantee. Connection close is not byte/transaction reconciliation.

Reserved residue must remain denied or immutable. A real transaction-owning caller and durable idempotency/recovery behavior belong to the parent save plan; do not activate publication because a stand-in owner passes isolated cases.

## Stop conditions and cleanup

Stop on wrong target, real-data exposure, missing native contracts, unexpected side effects, source/runtime mismatch, ambiguous cleanup scope or any failed required assertion. Preserve exact evidence without session secrets; do not broaden deletes or retry an uncertain publication.

Dispose only the approved isolated deployment and its private storage, or use an explicitly approved exact-object cleanup procedure. Record database/schema/audit and physical-file residue honestly. No production backup/restore acceptance is implied by disposing synthetic fixtures; off-device backup destination/encryption/retention/restore procedure remain unresolved parent gates.

## Results

- Local HTTP-support preparation: **VALIDATED LOCALLY ONLY**. Separate Standards PASS and Spec finding closure followed by independent **401 tests / exit 0** on the reviewed source. Missing configuration refuses actual-suite execution (exit 1, 0 cases), configuration-only `--check` (exit 3), and no-flag CLI (exit 2). Production bytes unchanged. Exact commands/logs and review verdicts: `.superpowers/sdd/2026-11-06-frappe-photo-access-extension/task-4-response-slice-review-results.md`. No actual request was sent.
- Guest ZIP and internal `/protected/` denial status/exact cache-header compatibility: **UNPROVED ACCEPTANCE BLOCKERS**; fake responses do not establish runtime compatibility.
- Installation/migration: **AUTHORIZED AND PERFORMED ON ISOLATED SYNTHETIC TARGET ONLY**, as detailed below; no production installation authorized.
- Update after explicit scope approval: isolated `clinic-photos-accept` / `clinic-photo-accept.local` on `10.0.100.10` was provisioned, with separate DB/storage/networks and loopback8082. One pinned `flit_core` wheel was separately approved. This supersedes the earlier installation authorization status for this synthetic target only; production activation remains unauthorized. Evidence: `disposable/readiness-report.md` and corrected `logs/provisioned/verify-subchecks.log` under the evidence directory above. Original failed readiness subcheck is retained, not treated as success.
- Native runner gate: **NOT VERIFIED**. Initial candidate selected zero cases; repaired separate class/method selector executed one passing case, but Frappe buffering decorated markers and discarded teardown residue. `--debug` was rejected because it also enables pdb. User approved test-only original-stream marker repair; no full matrix or HTTP case has executed.
- Subsequent runner gate: **VERIFIED FOR ONE CASE**, with standalone original-stream markers, actual identity/nonce/residue, one-test summary and terminal success. Independent local suite after marker repair: **417 tests / exit 0**. Matrix collection then stopped at first case (**0 passed, 1 failed, 30 unrun**) on native `ptype` versus adapter `permission_type` mismatch; local repair pending. Earlier runner-gate failures remain historical evidence, not current gate status. HTTP remains unrun.
- Disposal evidence: **PENDING**. Final acceptance must bind full run/nonces, rows and storage keys to the exact stack/site and observed removal of its database/private-file volumes. A matching row digest plus operator label alone, or empty residue, is insufficient. No manual immutable deletion or abort reset is authorized.
- Native matrix and CLI contract: **NOT RUN / UNVERIFIED**.
- HTTP/nginx/accelerated delivery: **NOT RUN**.
- Failure/concurrency/disposal/cleanup: **NOT RUN**.
- Real publication owner/durable recovery: **NOT IMPLEMENTED / NOT ACCEPTED**.
- Live capability or role activation: **DISABLED / NOT AUTHORIZED**.
