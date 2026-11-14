# Native photo insertion ordering — review draft

**Status:** User-approved design; implementation plan pending review. Explicit approval includes the feature-specific schema/lookup expansion and fail-closed ordinary-upload availability tradeoff. Approval authorizes planning only—not code changes, schema migrations, native/HTTP retries or live activation. Builder specification questions are resolved; runtime guarantees remain unverified. Existing approved policy remains authoritative.

**Builder-review decisions:** User selected a new authenticated coordinator method, disabled by default and exercised only on the isolated synthetic site, owning both commits. User also selected conservative recovery: unresolved prepared saves do not resume automatically; include read-only evidence inspection and an operator runbook. These settle design scope only. No implementation or live activation approval is implied.

## Intent

Resolve the installed Frappe File insertion mismatch without core patches, privileged File insertion, weaker access checks or a test-only shortcut presented as production safety.

The current fixture assumes metadata-only `File.insert()` followed by the first content write. Installed native `before_insert` instead invokes `save_file(content=get_content())` before persisting the File row. Current native matrix stopped at its first case; no passing matrix or HTTP acceptance is implied.

## Approaches considered

1. **Persist File metadata before bytes.** Retains the earlier ordering assumption, but no suitable supported metadata-only path is established. The native copy-existing flag is not authorization to register a nonexistent blob or skip required lifecycle behavior.
2. **Register protected feature/key, then guarded content-bearing native insertion, then finalize linkage.** User selected review of this approach. It may fit native insertion, but feasibility depends on canonical naming, byte fidelity, deduplication, receipt finalization and rollback contracts.
3. **Pause and dispose the synthetic stack.** Remains available if no safe supported design can be established.

The choice to review approach 2 is not approval to implement it.

## Proposed ordering, subject to source reconciliation

1. A server-owned transaction owner validates actor/draft/version authority and acquires authoritative key/alias locks.
2. Authoritative pending feature/key identity is registered before any protected byte access or write. A reserved filename alone is insufficient.
3. The exact unsaved File object enters a bound native insertion/write operation. Native permission checks and feature guards remain active.
4. A successful first write produces one receipt bound to that object, authorization context, canonical identity and actual resulting bytes.
5. After native File persistence, the receipt binds once to the persisted File identity. Linkage finalization verifies and consumes that receipt; an insert return value alone is not publication success.
6. Any failed or unknown write/persistence/linkage outcome poisons publication and requires reconciliation. No unproven retry, replacement or key reuse is permitted.

These steps describe required properties, not an established supported implementation.

The concrete proposal below uses exactly two committed phases. The advisory alternative of creating bytes before native insertion and committing a separate receipt is superseded: it violates the intended insertion scope and does not eliminate the crash gap.

## Installed native contracts now established by source

Source evidence is pinned to Frappe 16.53.0 in
`.superpowers/sdd/2026-11-06-frappe-photo-access-extension/disposable/native/ordering-review/source/`.
These are source observations, not runtime acceptance:

- `Document.insert` checks create permission, calls `before_insert`, then assigns the File name, validates, persists the row and calls `after_insert`. The pre-write object has no final native File name.
- Ordinary local File insertion reads in-memory content when supplied, otherwise reads disk; it has no deferred-content insertion branch. The copy-existing path still requires an existing blob at validation and is not a first-write shortcut.
- Native insertion calls `save_file` with `overwrite=False`. Native defaults may suffix-rename the filename, rewrite the URL, reuse a matching MD5/private-file record and repoint linkage again during duplicate validation. Simply supplying content does not preserve the approved key/alias contract.
- Native JPEG handling may strip EXIF metadata when the system setting is enabled. A native content hash is MD5, not the required actual-byte SHA-256.
- Native disk writing uses `wb+`, not exclusive creation. A reserved path and an in-process token alone do not prove that an existing blob cannot be overwritten.
- Native rollback compensation is callback-driven. Callback exceptions can prevent later callbacks; savepoint rollback does not run them; commit drops rollback callbacks before executing pre-commit callbacks. Native compensation alone therefore does not establish failure/crash safety.

The revised path remains **not implementation-ready** until it specifies how to prevent these native key/content/deduplication effects and safely own physical writes and compensation. Forcing overwrite flags, changing site-wide EXIF settings, weakening identity checks or trusting native rollback callbacks alone is not an approved solution.

## Expanded review direction selected by user

The source-reconciled recommendation is to consider an **extension-owned protected first-content writer**, invoked through supported File extension methods while preserving native document permissions, validation and delivery. This is more than guarding native write defaults and requires explicit design approval. No custom delivery service or core patch is proposed.

Review it with the parent durable-save owner, rather than claiming a synthetic owner establishes production safety. The design must resolve:

- Exact protected first-write scope, exclusive confined creation, byte/hash verification, no native transformation/deduplication/key rewrite, and compatibility with installed security/write hooks.
- A receipt bridging the exact unsaved File object to one persisted native identity, consumed once by authorized linkage finalization.
- A database-backed ownership/outcome record committed before publication, surviving the publication transaction's rollback and callback-reset/failure windows.
- Known-commit, known-rollback and unknown outcomes, with idempotency and reauthorization on retries/reconciliation; no deletion or reuse on ambiguous ownership/commit.
- Original ingest, explicit same-patient original reuse and manual annotation-version append as distinct flows. Version conflict remains scoped to the draft, never globally to a photo.
- Backup of database, private files and any required recovery metadata to a separate device; destination, encryption, retention and tested restore remain release decisions, not newly approved infrastructure.

The existing parent save plan's `(user, operation, requestKey)` ownership, disabled-write defaults, clinician-wide reads and author-only editing remain requirements. This review does not activate an endpoint, frontend persistence or a live role mapping.

## Selected durable-intent direction — concrete design approval pending

One operation coordinator must own authorization, intent, transaction boundaries, the protected writer, receipt binding and outcome reconciliation. The parent plan currently names these requirements but supplies no working durable-owner protocol.

### Intent journal alternatives

**A. Database-backed phased intent — selected by the user for this design.** Commit the operation intent and authoritative pending registration before the publication transaction writes bytes. Publication atomically persists File/version/draft linkage and committed operation outcome. A failed publication transaction leaves the earlier intent available for recovery. Use a clean coordinator-owned transaction boundary; never commit unrelated request writes. The clean-boundary integration remains to be specified and runtime-verified; independent connection isolation is not established by source inspection.

This is explicitly non-atomic preparation plus publication. Row locks end at phase commit, so durable execution ownership/fencing and verified cross-process exclusion must bridge the handoff. A lease expiry alone cannot authorize a second writer while the first may still run.

### Coordinator boundary proposal

Use the dedicated default-off authenticated method selected by the user for the design, with sequential transactions on the existing request-local database and the clinician preserved. Concrete implementation remains subject to approval of this document. It may not be called inside an arbitrary existing request transaction. Before any preparation commit, the entry path must establish that authentication, middleware and earlier hooks have left no unrelated pending writes or callbacks. An unverifiable clean boundary blocks publication; do not commit or roll back somebody else's work to manufacture one.

Do not introduce a separate DB instance or swap `local.db` as a workaround. Native hooks resolve request-local state, and separate callback queues do not establish safe integration. The later framework request-finalization commit must have no pending feature mutation, publication callback or unvalidated result capable of changing the settled outcome. Any error after a known publication commit cannot undo it; replay must resolve the durable result rather than append another version.

### Exclusion proposal and deployment limit

For the current single-host shared-sites-volume deployment only, propose stable server-owned OS advisory lock objects, acquired in order operation then draft and held across phase commits, physical writes and outcome resolution. Lock objects are never unlinked/recreated during ordinary cleanup. Unique operation and canonical-key reservations provide database constraints; persisted generation tokens are rechecked before protected mutations but do not replace exclusion.

All publication, reconciliation, cancellation and draft mutation paths must follow compatible locking. No detached writer, alternate host or background process may bypass this protocol. Recovery must acquire the same locks and establish that the prior writer and any delegated children no longer retain write capability. Lease expiry alone never authorizes takeover. Uncertain DB or lock ownership stops mutations and leaves durable reconciliation state; a lost process cannot promise to hold locks indefinitely.

Advisory locks constrain cooperating writers only. Exclusive file creation prevents initial replacement, not later mutation. Complete protected-namespace writer coverage, deployment permissions and actual cross-process lock behavior are mandatory verification gates. If the deployment cannot establish them, refuse protected publication rather than claim fencing makes arbitrary writers safe.

**B. Restricted filesystem write-ahead journal — considered, not selected.** Fsync intent and its directory before protected creation, with crash-safe record updates and ownership. This avoids DB rollback erasing intent but introduces another consistency, locking and backup domain. Do not build it alongside the selected database journal.

An intent stored only inside the publication transaction is rejected: rollback would erase the evidence needed to reconcile physical bytes.

### Corrected source constraints and remaining runtime gates

The corrected `remaining-seams/durable-save-seams.md` supersedes earlier hook claims. `out = out or hook(...)` skips subsequent callable evaluation after the first truthy return. Static AST inventory found no target write-hook registrations in four installed `hooks.py` files; it does **not** prove the effective merged runtime hook map is empty. Protected writes must refuse an unknown or incompatible effective hook configuration rather than silently bypass it.

Native hooks can mutate attachment/privacy fields after `after_insert`. Receipt bridging there is provisional: the coordinator must re-read persisted identity and references after complete insertion and linkage hooks, before committing publication.

Independent DB objects have separate connections and callback queues by source, but cross-connection commit isolation remains untested. `frappe.connect()` replaces request-local DB state and sets Administrator; it is not an approved connection-swap shortcut. Transaction ownership must preserve the authenticated actor and native permission context throughout.

The deployment uses an ext4-backed sites volume, containing private files. Directory-relative exclusive/no-follow operations and fsync are available by platform inspection; race safety, confinement and crash durability remain untested. These are implementation verification gates, not established guarantees.

### Operation and outcome contract

- Proposed idempotency identity: site, authenticated actor, operation kind and request key. Bind target draft and immutable semantic payload digest; reject a mismatched reuse. Replays require current-access reauthorization and do not append another version.
- Server derives patient/author/next version/storage keys and File identity. Proposed inputs include draft identity, expected draft version, canonical annotation document and verified original identity; no client transaction-owner or permission flags.
- Under a fixed lock order, prepare durable pending intent before bytes. Reacquire/verify execution authority and draft version for publication; a feature/key reservation is not a saved draft.
- The real owner governs exact-object first write, post-insert receipt binding and one-use linkage. Known publication commit must include coherent operation outcome and domain records; a successful File insert or SQL call alone is insufficient.
- Saved is returned only after known commit. Lost connection or ambiguous commit/callback result is **unknown**, not failure with permission to create another version. Reconciliation compares durable operation/domain records and actual bytes; inconsistent evidence remains quarantined.
- Known rollback preserves the prepared intent. A crash after exclusive file creation but before recorded creation identity leaves quarantine, not guessed deletion authority. No automatic immutable deletion or silent key reuse.
- Native callbacks may assist, but are not the durable ownership/outcome record. Their loss or failure cannot establish compensation or authorize retry.

### Concrete two-phase protocol proposed for approval

#### Named records and explicit schema amendment

Propose one feature-specific **Clinic Photo Save Operation** DocType as the durable intent/outcome record. This is a new schema element requiring approval of this design, not an existing capability or a general-purpose file registry. Existing `Clinic Photo` and `Clinic Photo Annotation Version` remain the domain records; the latter already has `pending/committed` states. The current `Clinic Photo` schema has no publication-state field, so it must not be used to expose a half-prepared original as a saved photo.

The operation record contains: site-bound operation ID; actor; kind; request key and semantic payload digest; patient reference; optional draft/original references; expected draft version where applicable; intended canonical key; generation; expected hash/size and image dimensions where applicable; state (`PREPARED`, `PUBLISHED`, `ABORTED`, `QUARANTINED`); and, upon publication, File/domain links plus stable creation/receipt evidence. Enforce operation-tuple and canonical-key uniqueness. Do not store arbitrary file paths or client capabilities as authority. Ordinary document APIs cannot list/read/mutate operation records; the disabled coordinator and restricted inspection surface mediate their use.

The committed PREPARED operation is the **authoritative pending key registration**. Extend feature protection lookup to consult this feature-specific record in addition to existing domain references, so protection survives rollback before any File/version row exists. Pending/unresolved keys deny ordinary byte access. Publication creates/commits domain records and PUBLISHED together; do not create a colliding draft/version placeholder in preparation. This expressly amends the extension's earlier feature-record-only lookup scope for durable save; it does not authorize a general storage registry, role grants or File permission bypasses.

#### Operation kinds

- **`annotation_append`:** requires existing published original and author-owned draft, expected draft version and validated annotation payload. Patient/author/original binding is server-derived. Apply draft conflict checks before bytes; the publication transaction creates the next immutable annotation version and advances the draft.
- **`synthetic_original_create`:** isolated-test-only kind, additionally gated by verified disposable-site identity and explicit synthetic-test mode. Requires synthetic patient reference and actual image bytes; derives hash/dimensions/key and creates the original `Clinic Photo` plus File in publication. It has no existing draft, expected draft version or original-reference prerequisite. Preparation reserves identity in the operation record. Use operation exclusion and patient-scoped synthetic-creation exclusion in a fixed order; there is no nonexistent draft lock to acquire. Idempotency uses the same actor/kind/request-key rule. This kind is unavailable on live sites and is not a general ingest endpoint.

The preparation/publication checklist below describes `annotation_append`; apply the stated synthetic-original variant rather than inventing placeholder draft/original identities. Both kinds use the same exact-object protected writer, receipt protocol and outcome rules.

**Preparation transaction:** under operation/draft exclusion, check current enabled clinician, native permissions and draft authorship; validate expected draft version and original reference; bind the semantic payload digest to unique `(site, actor, operation, requestKey)`. Allocate a fresh non-reusable canonical key and generation, and commit operation **PREPARED** plus authoritative pending registration. Store expected byte length/hash, intended key, schema/target identity and actor; not the annotation contents in diagnostic logs. No bytes or File row exist yet. Constraints enforce operation uniqueness, canonical-key uniqueness and committed draft/version uniqueness. Competing prepared requests may exist, but each must recheck the current draft version before writing.

**Publication transaction:** reacquire database row locks while retaining cross-phase exclusion; reauthorize actor, author, expected version, generation and complete key/reference state. Bind exact unsaved File object and invoke ordinary `File.insert()` with native permission checks. Only its server-bound first-save specialization may create bytes. Bridge the successful creation receipt provisionally after native File persistence. After complete insertion and domain-linkage hooks, re-read File/domain identity, canonical references and actual byte evidence; consume the receipt once. Commit File, immutable annotation version, draft current-version advancement and operation **PUBLISHED** together. Do not commit from a File hook, writer or receipt helper.

Return saved only after known commit and validated durable outcome. Request teardown must not contain a deferred feature mutation needed to finish publication. A durable PUBLISHED replay returns the same version after present-access checks; payload mismatch rejects, version mismatch conflicts before a write, and a PREPARED retry never blindly reruns insertion.

The first slice proposes an authenticated coordinator method behind a default-off server gate, tested only on the approved synthetic site after separate implementation approval. It owns the two request-local commits; a bench harness alone cannot establish this entry contract. No live route exposure, frontend write activation or production mid-request commit is authorized. Clean-boundary failure refuses the method before preparation.

**Receipt binding:** capture operation/generation, actor, exact unsaved object identity, canonical key, owner/privacy/attachment target, actual SHA-256/size and filesystem creation identity. The in-memory object identity is not serialized as durable authority. After insertion, bind once to native File name and verify both the persisted row and object; later hook mutation invalidates linkage. Persist the stable creation and domain evidence atomically with PUBLISHED. Reject receipt replay, substitution, relinking, changed bytes or unexpected aliases. The receipt is not an externally usable capability.

**Crash gap:** no independent RECEIPTED commit or second DB connection is proposed. If bytes were created but publication did not verifiably commit, the PREPARED record survives but may lack durable creation identity. Matching path or content does not repair that ownership gap. Block reuse/resumption and quarantine for separately authorized manual reconciliation. This intentionally trades automatic recovery for safety.

### Protected File specialization

Use the supported File mixin, not a core patch. `before_insert` establishes a server-only scope for the exact registered object around native delegation. Preserve native create authorization, in-memory content access guards, applicable native size/content/security validation, document validation, attachment lifecycle and delivery. Resolve the effective write/security hooks before permitting this mode; unknown, changed or incompatible hooks block it. Do not silently skip a security obligation to preserve bytes.

For this protected first insertion only, specialize `save_file` to avoid native EXIF changes, MD5 reuse, suffix naming and URL rewriting. Keep canonical private identity and compute native compatibility metadata separately from required SHA-256. Specialize duplicate validation to verify identity without native URL repointing. Proved-unrelated Files continue through native behavior, subject to the classification checks below. Existing committed protected Files remain immutable; request flags cannot activate first-write mode.

The physical writer opens the trusted private-files directory through a confined directory descriptor, rejects path separators/noncanonical basenames, and uses exclusive/no-follow creation. Retain the opened descriptor and creation identity; handle partial writes, fsync bytes and parent directory before issuing a success receipt. Verify identity, size and hash against the actual object, not an unchecked pathname. Existing target, symlink, confinement failure or short/failed write poisons the operation; never truncate or replace. Exact platform implementation is a builder choice subject to race/crash tests, not a proven guarantee from syscall availability.

Native protected rollback/delete behavior must not independently delete or restore a blob using mutable File fields or MD5 aliases. Specialize that path to preserve/quarantine residue and record abort evidence; callbacks are not cleanup authority. No automatic protected unlink is in this proposal. Ordinary immutable-deletion restrictions remain. Synthetic-stack teardown remains a separate approved operation, not a publication cleanup API.

**Later ordinary uploads are part of the protection boundary.** Native hash lookup and duplicate validation may retarget an initially unrelated upload to a protected destination. Suppressing deduplication only in protected first insertion is insufficient; omitting MD5 is not a proven fix because native code can regenerate it. Use two distinct checks: (1) the File validation boundary reclassifies the destination after native retargeting and before `db_insert`; (2) a supported File `insert` wrapper checks persisted identity/references after the complete native insert/hook sequence returns, before returning control or permitting publication commit. Check 2 is necessarily post-row-insertion, within the uncommitted transaction: violations poison the operation and require rollback, not a claim that no row was ever inserted. Neither check may defer essential validation to request teardown. Same-patient reuse requires an explicit feature operation, not hash equality. Rejection must also prevent native rollback callbacks/helpers from deleting, moving or modifying the referenced original. Matching hashes alone do not prove disclosure.

At both checks, any new alias to a registered protected key (reserved **or nonreserved**) is rejected unless the server-bound explicit reuse operation authorizes that exact relationship. The protection lookup uses operation/domain identity, not filename prefix alone. No current first-slice operation authorizes an ordinary-upload alias. If classification of an apparently unrelated upload fails, reject that File insertion too; do not delegate on unknown. This can temporarily block ordinary Desk uploads when protection lookup is unavailable and is an intentional availability tradeoff requiring design approval. Proved-unrelated uploads retain native behavior. These are proposed implementation obligations; their enforcement is unproved until installed tests pass.

**Source-review refinement, not runtime closure:** the dedup-retarget/new-file rollback deletion dispatch reaches the existing mixin deletion guards, including failed documents classified by their current key. However, other native `on_rollback` branches directly rewrite original content or move paths without that dispatch; the current mixin does not guard this boundary. The proposed nonreserved-alias rejection and Administrator-independent rollback/deletion boundaries below address these gaps at design level; implementation and runtime proof remain outstanding. Exact-stack disposal authority is separate and must not be inferred from Administrator status.

Protected rollback should preserve residue and mark the operation unresolved/quarantined without invoking native destructive compensation. Avoid deliberately raising a denial from a routine rollback callback and thereby preventing later callbacks, but do not suppress unexpected failure or claim recovery succeeded. Native File bookkeeping flags may be finalized only without clearing the publication owner's poisoned state, one-use authority or abort latch. Resolve identity from authoritative key/reference evidence even if the failed File was never persisted. Any reachable direct module-level filesystem helper outside the mixin needs its own verified protection boundary; unsupported closure is a design blocker, not permission to monkeypatch core.

**Concrete rollback boundary:** override the supported File `on_rollback` method. Before delegating, classify the current URL/full filesystem path and both `original_path` endpoints, as well as any previously bound protected identity. Map filesystem paths against trusted site roots, not URL string heuristics. Protected, reserved, inconsistent, previously poisoned or unclassifiable destinations skip native compensation entirely for every user. Retain in-memory poison/abort evidence even if mutable File fields change; do not clear it with native rollback flags. Only destinations proved unrelated may delegate to native compensation. Classification failure must not enable mutation or deliberately abort the remaining callback queue. This does not promise generic exception isolation for other native callbacks.

The same rule overrides current Administrator cleanup shortcuts for ordinary protected rollback and deletion dispatch: actor identity never substitutes for operation-bound authority. Protected `_delete_file_on_disk` and downstream deletion guards must refuse native destructive work, including for Administrator, outside separately authorized disposal. The existing synthetic-stack teardown removes stack-owned resources externally and is not an in-app Administrator bypass. These are specified boundary changes to implement and test after approval, not unresolved permission choices.

No journal write is required inside `after_rollback`, where the DB may be broken. The earlier committed PREPARED record is the durable unresolved floor. The coordinator or later reconciler may record QUARANTINED only after reliable DB access/outcome resolution; failure to do so leaves an unresolved operation, never a successful cleanup. In-memory poison and diagnostics assist but do not replace that durable intent. Lock acquisition/release and final outcome handling must belong to coordinator control flow, not solely callback ordering.

The four-app static caller inventory found supported deletion routes using `delete_doc("File")` and guarded instance dispatch, not direct helper calls. Native module-level deletion helpers perform filesystem removal without protected identity checks. Therefore scope is a verified installed-app/hook/writer configuration: any reachable unguarded helper caller or untrusted extension blocks publication/activation until a supported enforcement boundary exists. Static inventory is not a runtime reachability or arbitrary-plugin safety proof. No core patch or monkeypatch is proposed to expand that scope.

### Failure and reconciliation states

| Observation under exclusion | Outcome |
|---|---|
| Preparation fails before known commit | No publication permitted; resolve uncertain preparation before reusing the request key |
| PREPARED, known rollback, proven no creation attempt/object, no domain publication | Record ABORTED; keep allocated key non-reusable |
| PREPARED with bytes, missing creation proof, uncertain prior writer, or inconsistent linkage | Record QUARANTINED when DB certainty permits; otherwise report unknown and retain PREPARED as unresolved |
| Commit response lost | Stop mutations; acquire fresh reliable read state and inspect operation/domain/bytes, never repeat the write |
| Matching PUBLISHED and coherent version/File/hash evidence | Resolve to published after current-access reauthorization |
| Terminal DB state conflicts with physical/domain evidence | Quarantine operationally; do not overwrite history or claim saved |

Unknown is an externally reported lack of certainty, not a fabricated durable transition when the DB is unavailable. Quarantine means block normal reads/publication/reuse and destructive automated cleanup; it need not move the file. An explicit reconciler has narrow server authority to inspect operation-bound evidence and change recovery state, not blanket File permissions. No recovery endpoint or deletion capability is activated by design approval. Revoked authors cannot resume publication; orphan/failed bytes remain protected from all ordinary reads.

The selected first slice includes read-only, operation-bound evidence inspection and a documented operator runbook, not ad hoc DB surgery or an automated resume/purge endpoint. The implementation plan must name the authorized inspection role and audit boundary before implementing that surface. An unresolved PREPARED request returns pending/unknown and never promises another write; request-key replay guarantees duplicate-success protection, not automatic recovery. Manual state-changing reconciliation needs separately authorized authority and proven evidence. A retention/purge policy, tested recovery and consistent off-device restore are live-activation blockers: indefinite orphan PHI retention is not approved merely because automatic deletion is prohibited. Synthetic residue remains governed by exact-stack disposal approval.

**Orchestrator's proposed inspection boundary:** require a current authenticated clinician who is also explicitly configured as a recovery operator; the operator allowlist defaults empty and grants no clinician role. Keep inspection disabled with the coordinator gate. Return operation-bound status, identity/hash/size consistency and audit facts only, not image/annotation contents, credentials, raw exception dumps or arbitrary filesystem reads. Audit actor, operation and inspection outcome. Existing clinician/native document-read requirements still apply. This is a proposed authorization restriction for user review, not an activated role mapping or a grant to Administrator/non-clinicians.

### Domain boundaries and first implementation candidate

Original ingest, explicit same-patient original reuse and annotation append remain separate operations. Ingest verifies actual image bytes/hash/dimensions and patient/encounter authority. Reuse never discloses cross-patient matches or merges drafts. Annotation append writes a separate immutable JSON version against an existing verified original; it never rewrites that original.

The smallest proposed later implementation slice is annotation append against a preverified synthetic original, with a real coordinator/journal, replay handling and draft-scoped conflict. This is not approved for implementation and does not activate original ingest or UI persistence. Authorization, crash/unknown outcome, receipts, alias safety and consistent backup/restore remain release gates.

**Orchestrator's acceptance-scope clarification:** JSON append alone does not validate image fidelity. Include a bounded synthetic image-bytes insertion through the same protected writer/coordinator to establish the original fixture and exercise unchanged JPEG bytes, dimensions, key preservation and later-upload alias rejection. This does not activate a general ingest endpoint, patient deduplication workflow or UI. Do not seed a supposedly protected original by privileged direct filesystem/DB writes. General original-ingest behavior remains outside the first feature slice; the synthetic writer contract is part of its test setup and evidence.

## Invariants that may not change

- Current enabled clinicians may read; only draft authors may edit. Native document permissions remain required; no File `ignore_permissions`, role grants or client-supplied service capabilities.
- Preserve exact original bytes, canonical URL/filename, privacy, owner and feature identity. Native hash fields do not substitute for actual SHA-256 and intrinsic-dimension verification.
- Reject native transformations and implicit cross-record deduplication. Explicit same-patient reuse remains a separate authorized operation with independent drafts.
- One first write, exact object/actor/context/transitions, one receipt. Reject receipt replay, object substitution, relinking and later replacement.
- Lock and ownership guarantees must cover byte writes, persistence, linkage and known completion of commit or rollback compensation. Callback failure or uncertain ownership is not successful cleanup.
- Failed/partial/orphan bytes remain protected and non-recyclable. Registration rollback must not expose them; ordinary immutable deletion and abort-latch reset remain prohibited.
- Concurrency requires cross-process key/alias/reference evidence, not merely request-local state or file existence.

## Scope boundary

Existing extension mechanics and test-only owners do not provide the real durable publisher. This proposal expands the parent durable-save design to include that coordinator, database intent and protected first writer; they do not exist yet. No live publication endpoint or frontend persistence activation is included here. Start any later approved implementation with synthetic annotation append against a verified original; original ingest/reuse and UI activation retain separate acceptance gates.

## Required evidence before implementation approval

- Reconcile full native File/Document ordering, transformations, filename/key rewrites, deduplication, persistence callbacks and physical rollback behavior with current guards/receipts.
- Specify the supported hook/entry path and exact receipt creation, post-persistence binding and consumption points; report any unsatisfied invariant as a blocker.
- Define pending/failed/unknown outcomes without presenting temporary native insertion as durable publication.
- Builder review must distinguish native-source facts from proposed changes and unresolved runtime evidence.
- Present the reconciled design to the user for approval before an implementation plan or code changes.

### Builder-review reconciliation

- Entry and commit scope: user chose the default-off authenticated method on the isolated synthetic target; two request-local commits are proposed within that method, not arbitrary request transactions.
- Retry and quarantine: user chose conservative non-resumption plus read-only inspection/runbook; pending retry is not automatic-save recovery. Retention/purge and tested recovery gate live activation.
- Native dedup metadata: retain needed compatibility metadata only with complete later-upload alias/rollback protection. Neither hash nulling nor protected-insert-only dedup suppression closes the boundary. Unsupported writer/helper configurations block the feature.
- Alias ordering: pre-`db_insert` validation plus post-full-insert verification; later violations require protected rollback. No claim of checking after `after_insert` yet before persistence. Unknown classification rejects ordinary uploads, accepting possible Desk-upload interruption rather than risking a protected target.
- Pending identity: proposed feature-specific Clinic Photo Save Operation is the durable reservation/lookup source, with the explicit field/state/constraint amendment above. Domain records remain unchanged until publication; user approval must cover this schema/lookup expansion.
- Synthetic original: isolated-only operation kind has image/patient inputs, no invented draft/original prerequisite, and the shared writer/receipt/outcome contract.
- Image evidence and fixture provenance: exercise a synthetic original through the same protected coordinator/writer; do not infer image safety from JSON or privileged fixture seeding.
- Remaining runtime work is verification of explicitly specified boundaries, not permission to weaken them. Builder recheck resolved the five material specification gaps; user explicitly approved this design for implementation planning. Implementation authorization remains separate.

Runtime prerequisites must be expressed as fail-closed implementation/activation gates, not falsely claimed complete before code exists: clean entry isolation, effective hooks, full writer coverage, lock semantics, filesystem confinement/durability, native downstream vetoes and complete post-hook validation. If a prerequisite cannot be established without changing this design, stop and return for review. Backup destination/encryption/retention and consistent tested off-device restore remain unresolved release blockers.

## Verification boundaries proposed for approval

Actual installed native hooks and public owner/guard/receipt interfaces: exact bytes/key, first-write/persistence/linkage, replay/substitution refusal, alias races, and failures before/after each transition. Callback failures and process death need separately bounded failure evidence; local mocks prove neither native lifecycle nor durability.

Existing native matrix remains stopped; HTTP remains unrun. Final evidence also requires run/site/rows/storage-key-bound disposal of the approved synthetic stack. The current parser's failed-case residue shortcut and rows-only cleanup record remain open, separate test-evidence gates.
