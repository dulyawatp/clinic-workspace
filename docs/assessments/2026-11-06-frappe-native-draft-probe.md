# Native Frappe draft permission probe

**Environment:** authorized disposable `marley.local`, Frappe 16.53.0.  
**Scope:** synthetic objects only; no patient records, credentials, existing private-file bytes or production permission changes. This is not signed acceptance or live activation.

## Method

The user approved three synthetic users, one temporary custom draft DocType, two draft records and two private JSON files, with scoped cleanup. Two users had Physician; the third had no clinician role. Each draft referenced one private JSON File attached to that draft. Setup used Administrator for schema/users and privileged File insertion, not for the tested actors' permission evaluations. This does not prove normal-user upload/create behavior.

Commands ran via SSH with the approved identity and strict host-key verification, then `docker exec -i -w /home/frappe/frappe-bench/sites marley-health-backend-1 /home/frappe/frappe-bench/env/bin/python -`. Tests invoked `frappe.has_permission` on the draft and File, and `File.is_downloadable()` for bytes authorization. They did not make HTTP requests or attempt actual unauthorized mutations.

## Initial setup defect and recovery

The first run omitted disabled permission fields. Installed `core/doctype/docperm/docperm.json` defaults read/write/create/delete/share to 1, so those rows accidentally granted broader rights. Its cross-author results are invalid evidence for owner-only configuration. Installed source inspection exited 0 and established the defaults before correcting the probe.

The initial command exited 1 during cleanup: File deletion was blocked by the draft's Link field, and one synthetic User deletion hit a database conflict. Draft deletion subsequently removed the attached Files. A scoped cleanup command exited 0, confirmed both exact File records absent, removed the one remaining synthetic User, and confirmed all three users and temporary DocType metadata absent. This verification did not check initial-run physical file removal. Audit/log/schema traces may remain.

## Corrected configuration

Every standard permission flag was explicitly initialized to 0. Persisted rows were read back:

| Role | If owner | Read | Create | Write | Delete | Share |
|---|---:|---:|---:|---:|---:|---:|
| Physician | 0 | 1 | 1 | 0 | 0 | 0 |
| Physician | 1 | 1 | 0 | 1 | 1 | 0 |

Author deletion was a probe setting, not a decision to enable a product delete operation. Original immutability and annotation-version lifecycle remain separately gated.

## Corrected results

The corrected run exited **0**:

| Actor | Draft read | Draft write/delete | Draft share | File downloadable | File write/delete |
|---|---|---|---|---|---|
| Author, Physician | Allow | Allow | Deny | Allow | Allow |
| Other Physician | Allow | Deny | Deny | Allow | Deny |
| Non-clinician | Deny | Deny | Deny | Deny | Deny |
| Guest | Deny | Deny | Deny | Deny | Deny |

Two residual bypass conditions were demonstrated by the installed byte-permission function:

1. Removing all clinician roles from the File owner still left `is_downloadable()` **true**.
2. Administrator explicitly sharing the second private File with the non-clinician made `is_downloadable()` **true**, despite that user lacking draft access.

The second test establishes behavior for an Administrator-created share, not that ordinary clinicians can create that share. Native draft sharing was denied to both Physician users.

Corrected cleanup unlinked only the synthetic drafts' annotation fields, removed the exact Files, drafts and users, and removed temporary DocType metadata. Output confirmed all deletions and **0 synthetic physical files remaining** for that run. No core patch, persistent permission change or production draft model was installed. Removing custom DocType metadata is not a guarantee of a physical SQL table drop; retained schema/audit traces were within the disclosed probe limitations.

## Conclusion and next gate

Native role-read/owner-write permissions are a viable foundation for the tested Physician configuration. They are not sufficient by themselves for the approved role-revocation policy on private bytes. A minimal verified safeguard is still required for revoked owners and inappropriate File shares, covering all direct download paths; a proxy-only check is insufficient. Do not weaken the policy or patch Frappe core to make this probe pass.

Before activation: verify DOC/NUR installed role mappings, fresh HTTP sessions and both byte routes, actual mutation denials, attachment/relink/replacement paths, shared byte references, concurrent saves, cleanup/recovery, and backup/restore. Any extension must be designed and reviewed against the installed permission path before deployment. Live reads/writes remain blocked.

## Follow-up: supported extension seam (read-only)

Three additional source/metadata inspection commands exited 0, without installing or modifying an app:

- Installed `frappe/model/document.py:125–167` applies any controller override, verifies it inherits the native class, then invokes `_get_extended_class`. Lines 206–235 load `extend_doctype_class` mixins in reverse hook order and place them before the base class in method resolution order. Thus the installed standard File controller supports a native class-extension seam; a core patch is not needed to extend methods.
- Installed-site hooks report no File entry for either `extend_doctype_class` or `override_doctype_class`. The resolved File class MRO is native File → Document → _DocumentBase → object. This is the present inventory, not proof of future app composition.
- Installed `frappe/core/doctype/file/file.py:715–749` implements `get_content` without a permission check in that function, including an early return for supplied in-memory content. The inspected function has no whitelist decorator. Its existence alone does not establish remote exposure; callers and dispatch must be traced. Overriding only `is_downloadable` would not constrain an internal caller using `get_content` directly.

Context7 documentation lookup resolved Frappe's version-16 branch and returned its migration note requiring controller overrides to inherit the native class. Installed source, not that generic note, establishes the extension seam above.

**Candidate, not deployed:** a small Frappe feature app can use the supported File mixin seam to restrict protected-byte download/content methods while preserving native checks, plus native draft ownership and lifecycle validation. This is not yet an approved complete security design: protected-byte identity/alias rules, full reader coverage, legitimate background execution, fresh-role checks and actual HTTP tests must be specified before implementation. A method extension cannot secure readers that bypass the File object entirely.

## Route reachability and bounded reader register (read-only)

Two further source-extraction commands exited 0. `frappe/__init__.py:1596` imports `get_doc` from the document module. In `model/document.py:347–348`, keyword construction delegates to `get_doc_from_dict`; lines 369–374 obtain the controller and instantiate it. `get_controller:110–122` caches the result of `import_controller`, whose installed extension behavior is described above. Thus `find_file_by_url:473–474` constructs through the extensible controller and dynamically invokes instance `is_downloadable()`. Combined with the previously traced routes, source supports mixin reachability for both named download paths. No candidate mixin has been installed or executed, so HTTP behavior remains unproved.

| Source path / reader | Observed path | Remaining verification |
|---|---|---|
| `/private/files/` and `handler.download_file` | Both use `find_file_by_url` and dynamic `is_downloadable`; the latter subsequently uses `get_content` | Real HTTP denial before response/body or accelerated delivery; fresh-role policy and aliases |
| `file.py:999–1014`, `File.zip_files` | Calls module `has_permission` directly, then instance `get_content`; does not call `is_downloadable` | `get_content` restriction and archive callers/dispatch; do not treat download-only restriction as sufficient |
| `utils/pdf.py:353–370`, `_get_base64_image` | Uses `find_file_by_url` before `get_content` | Print/export entry points, execution identity and generated-PDF privacy; existing exported copies cannot be recalled |
| `utils/csvutils.py:46–68` | Loads an attached File then calls `get_content` | Calling import workflows and current-user checks; not proof of remote content exposure |
| `file.py:549–590`, `make_thumbnail` | Calls `get_local_image` rather than `get_content`; constructs output via `get_site_path("public", thumbnail_url...)` | Trace actual path resolution, caller authorization and derivative privacy before allowing clinical previews; source alone does not establish a public leak |
| `file.py:957–987`, whitelisted `optimize_file` | Reads via `get_content`, then `save_file(..., overwrite=True)` | Actual dispatch and lifecycle rejection for immutable clinical originals/committed annotation versions |
| `file.py:101–141,804–874`, insert/save lifecycle | Reads content internally; save may strip JPEG EXIF under system settings and deduplicates by content hash plus private/public status, not patient | Protected-file setup/background execution must not become an access bypass. Verify exact original-byte preservation, canonical SHA-256 separately from native hash, and alias-safe patient-scoped duplicate behavior |
| `utils/file_manager.py:331–387`, `get_file/get_file_path` | Reads filesystem bytes directly without File method dispatch | Inventory whitelisted callers and constrain applicable paths; a File mixin alone does not intercept this helper |

The bounded reference search covered `core/doctype/file`, `utils`, `handler.py` and `desk/form` only; it is not a complete reader inventory across Frappe, ERPNext, Healthcare or custom apps. Background/export and direct-storage paths remain explicit blockers, not assumed protected paths. Native storage deduplication is not permission to return cross-patient matches to the UI.

### Final bounded caller pass

Three further source-only commands exited 0. An AST search of non-test Python across installed Frappe, ERPNext and Healthcare found seven enclosing-function references to the selected names `get_file`, `get_local_image`, `zip_files`, `make_thumbnail`, `optimize_file` (cap 60, not reached). This is a selected-name search, not proof of all readers; nested functions can appear twice and indirect calls are outside its coverage.

- `core/api/file.py:130–135` exposes whitelisted ZIP delivery through native `File.zip_files`; the instance `get_content` extension is relevant even though the ZIP code calls module-level permissions first.
- `handler.py:293–351` checks document access and method whitelisting before controller RPC execution. Since inspected `File.get_content` is not whitelisted, do not describe it as a directly exposed RPC. `optimize_file` is whitelisted, so lifecycle immutability still needs enforcement and an actual request test.
- `core/doctype/file/utils.py:85–114` opens local image paths directly. Private image paths use the site's private location. The thumbnail method constructs a separate output location through `get_site_path("public", ...)`; actual derived-path confinement and caller reachability must be tested, not inferred as a confirmed leak.
- ERPNext Chart of Accounts Importer `get_coa:182–210` and `import_coa:85–112` call a **local** `get_file:115–131`, not the raw filesystem helper. That local helper uses `find_file_by_url` and restricts extensions to CSV/Excel. These references are not evidence of a direct filesystem bypass.
- ERPNext Bank Statement Import `convert_mt940_to_csv:170–233` imports the raw helper from `frappe.utils.file_manager` (line 19). It checks write access to a Bank Statement Import document before reading the supplied path, then checks MT940 format. This is a confirmed File-method-bypassing read path in source, not a demonstrated clinical-content disclosure. Its document access, input/path restriction and output behavior need targeted tests; do not execute this write-producing function against real data during inspection.

This concludes the bounded source inventory for the proposed design. Remaining uncertainty is moved to explicit synthetic acceptance cases rather than claimed resolved by more source search.

Planning-specific seam confirmation (read-only command, exit 0): installed `app.py:181–186` calls `after_request` hooks with request/response; lines 169–173 catch/log their exceptions, so header hooks are not authorization boundaries. `init_request:214–220` parses form data, creates the HTTP request/session, then invokes `before_request`. Installed `utils/data.py:1750–1798` rejects sources not beginning `/files` and sources containing `..` before local-image reading. Thus its private-URL rejection is an explicit acceptance case, not a reason to monkeypatch that helper. Selected references found this helper exposed through safe-exec utilities; custom script/template configurations remain outside the inspected source-only inventory. Context7 confirmed supported whitelisted-method override signatures and that query permission conditions constrain `get_list`, not privileged `get_all`.
