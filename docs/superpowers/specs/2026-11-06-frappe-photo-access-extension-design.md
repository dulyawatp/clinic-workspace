# Native-first Frappe photo access extension

**Status:** User-approved enforcement design; concrete implementation/acceptance gates remain. No extension installed or live activation approved.  
**Authority:** Approved phase policy in `2026-11-06-frappe-private-photo-drafts-design.md` and the explicit amendment in the original functional requirements.  
**Evidence:** `docs/assessments/2026-11-06-frappe-native-draft-probe.md`.

## Goal

Use Frappe's native document, permission and private-file facilities for manually saved annotation drafts. All current authenticated clinicians may read all patients/saved drafts in this phase; only the author may change their draft. Protect immutable originals and committed annotation versions without a core patch, separate object store or privileged browser credentials.

## Approach and limits

Keep native role-read/owner-write draft permissions. Add a small feature app using the installed `extend_doctype_class` mechanism for File methods and normal lifecycle/permission hooks. Do not replace Frappe's streaming or nginx delivery. The extension restricts native authorization; it never treats a feature capability as a substitute for document authorization.

**Installed hook contract clarification:** Observed Frappe 16.53.0 dispatch supplies `ptype`, and treats any falsy `has_permission` hook result as a veto. Internal policy may use `None` to mean “no feature objection,” but the native adapter must translate that exact decision to `True` so the remaining native hooks and role/user-permission checks continue. `True` at this boundary is not a grant or a substitute for those checks. Internal `False` and unexpected decisions remain denied. Evidence: `.superpowers/sdd/2026-11-06-frappe-photo-access-extension/disposable/native/diagnosis/` (installed source and observed decision chain). Native continuation and downstream denials require installed verification; local mocks do not establish them.

Native permissions alone were tested and leave revoked owners and explicit File shares able to read bytes. A proxy-only check misses direct Frappe URLs. Runtime monkeypatches/core patches are rejected. File method extension is the preferred supported seam, but source readers bypassing File methods must also be constrained or proven unreachable for protected files before activation.

The bounded source inventory is complete; candidate behavior remains untested. Reachability of the File mixin and lack of a whitelist on `get_content` do not establish blanket safety. Any additional discovered exposure blocks activation until resolved. The decisions below are requirements for a concrete pre-code plan, not reasons to continue open-ended source research.

## Protected identity

The server records draft/author identity, patient/photo identity, immutable original hash/dimensions and version File references. Author identity comes from the authenticated session, not payload. Each save has its own immutable annotation version; versions conflict within a draft, not across every draft for a photo.

Classify protected storage by server-maintained File references and canonical storage identity, covering every File row referencing those same bytes. Optional `fid`, a filename, a supplied draft ID or current attachment alone cannot decide whether protection applies. Native storage deduplication must not create a less-protected alias. Metadata lookup for this protection remains internal; patient-scoped duplicate responses never disclose cross-patient matches. New protected files must be registered as protected before they are reachable; partial save/orphan handling cannot create an unguarded interval.

## Native and extension responsibilities

| Boundary | Enforcement |
|---|---|
| Draft record | Native clinician read and owner-only write; explicit server author check on version publish. Preserve author identity and original references. |
| File metadata/list access | Restrictive feature checks in addition to native permissions; no anonymous/non-clinician metadata disclosure for protected objects. |
| File download | Extend `is_downloadable` to require current enabled clinician identity for protected bytes, then retain native download authorization. Owner/share branches cannot override the clinician restriction. |
| File content | Extend `get_content` to require both current enabled clinician identity and applicable native File/document read authorization before any early content return or disk read. Native `get_content` has no authorization check; calling `super()` alone is insufficient. Define legitimate insertion contexts explicitly; unspecified background reads are denied. ZIP and permitted export callers must not bypass this restriction. |
| Lifecycle | Reject rewriting originals/committed versions, unauthorized draft linkage, ownership transfer, unauthorized deletion, private-to-public conversion and aliases that weaken policy. Other clinicians' read permission never authorizes these mutations. |
| Derivatives | Do not allow native thumbnail/optimization paths to mutate protected originals or emit unprotected derivatives. Initially deny these operations for protected objects unless private derived delivery is explicitly verified. |
| Direct-storage helpers | For each inventoried external entry point, either prove inaccessible to protected objects or add a narrow supported entry-point restriction. Do not globally replace raw filesystem helpers or silently disable unrelated finance operations. |
| Sessions and background work | Recheck current enabled user/clinician status; stale roles in a long-lived session must not permit access. Deny unspecified background readers by default. No client-controlled service-mode or `ignore_permissions` bypass. |
| Delivery cache | Feature responses must use no-store behavior appropriate for shared workstations; verify accelerated delivery and both routes. Clear application-held drafts/photos on logout. Already downloaded/exported copies cannot be recalled. |

Infrastructure administrators remain trusted. An Administrator-created share must not enlarge the feature's ordinary audience through HTTP; this does not promise protection against administrators changing code or reading the database/storage directly.

## Acceptance gate and ownership

The implementer owns focused unit and synthetic installed-site tests. The orchestrator reconciles results and runs independent scoped validation once the candidate is settled. Evidence must record exact commands, exit statuses and response/body/byte assertions, not only server permission booleans.

1. Install the candidate only on a separately authorized disposable deployment. Confirm effective File MRO and method resolution. No production installation or shared service changes are implied by this design.
2. With fresh HTTP sessions, test author clinician, other clinician, non-clinician, Guest, revoked owner with an existing session, and explicitly shared non-clinician. Cover direct private URL, API download and ZIP; repeat applicable cases with/without `fid` and shared storage aliases.
3. Actually attempt non-author draft edits and protected File relink/reown/delete/replace/publicize operations. Denial must leave bytes and metadata unchanged. Prove allowed author manual saves append an independent version and preserve originals.
4. Exercise thumbnail/optimization and the identified raw Bank Statement Import path using only suitable synthetic records and inputs, under separately approved write scope. Resolve path exposure without changing unrelated finance permissions. This is a prerequisite, not an assumed closed path.
5. Verify role removal/disabled users, response cache behavior, nginx internal location and no body leakage on denials. Verify raw helper, export and background reader coverage against the bounded register; document any additional discovered reader as a blocker until resolved.
6. Verify original-byte preservation despite native image transformations, canonical SHA-256/dimensions, patient-scoped duplicate reuse, concurrency, save/retry/crash reconciliation and off-device backup/restore under the existing gated plan.

The existing fixture workspace stays fixture-only. No task is complete merely because a mixin exists. Real operations remain disabled until installed contracts, authorization/mutation/byte-route tests, recovery and required approvals pass.

## Remaining implementation decisions

Local role-mapping review found fixture DOC/NUR labels only, not an installed-backend mapping. `proxy/src/server.ts:80–83` explicitly refuses live mode without an upstream auth contract; this gate must remain intact. The native probe covered Physician, not Nursing User. Physician → DOC and Nursing User → NUR are candidate mappings to verify, not activated permissions; Healthcare Administrator and System Manager must not implicitly qualify as clinicians.

Resolve the installed DOC/NUR role mapping, exact feature app location/schema, protected registry/alias transaction boundary, narrow raw-reader restriction, and background execution policy in the implementation plan before coding. These are not invitations to silently weaken the approved access policy. If a supported restriction cannot cover an exposed reader, report the blocker rather than claiming complete protection.

The pre-code plan must name: authoritative feature records/fields and canonical alias lookup (reuse feature records rather than invent a general-purpose registry); registration/publication/cleanup ordering; supported metadata list/read and File mutation enforcement points; exact narrow restriction for the raw Bank Statement Import reader and direct-image/thumbnail operations; and response-header enforcement for both normal and accelerated delivery. The bank import's own document permission and post-read MT940 validation do not authorize reading protected clinical bytes. Distinguish temporary upload cleanup from immutable committed versions. These choices precede implementation; actual route/mutation/cache behavior is then proved by synthetic acceptance tests.
