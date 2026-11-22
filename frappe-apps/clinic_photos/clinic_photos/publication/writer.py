"""Exact-object exclusive first writer and provisional receipt bridge.

One protected object, one first write, and the bytes are created **only** inside the
bound native first-save specialization. The writer never touches the filesystem before
``File.insert()``:

1. it verifies this attempt's still-live exclusion owner, the persisted ``PREPARED``
   evidence and the exact unsaved ``File`` object;
2. it registers a server-owned *intent* (operation evidence, the exact object
   reference, key, SHA-256/size, actor, site and publication context) — no bytes;
3. it enters the bound publication context and calls ordinary ``File.insert()`` with
   no ``ignore_permissions``, so native create permission, ``before_insert``
   validations and every later native veto run in the pinned 16.53 order;
4. native ``File.before_insert`` → ``save_file`` reaches the mixin specialization,
   which resolves the effective write/security hooks (refusing an unknown or
   registered ``write_file``/``before_write_file``), applies the native maximum-file-
   size check, computes native-compatible MD5 metadata, and then performs the confined
   directory-relative exclusive no-follow creation with full byte and directory
   durability before minting a provisional receipt;
5. after the complete native insert/hook sequence returns, the receipt is bound to the
   persisted ``File`` identity only after a fresh persisted row, the actual bytes and
   the authoritative references are verified. Binding is consumed exactly once.

Everything — including pre-write refusals and post-hook binding — runs inside
``owned_publication_transaction(owner)``, so a refusal latches the execution-wide
publication abort; a caller that catches it cannot continue under a fresh owner in the
same execution.

No EXIF strip, hash-suffix naming, URL rewrite, dedup retarget, overwrite/copy-existing
flag, native write fallback or client capability exists on this path. Native MD5
``content_hash`` is compatibility metadata, never receipt authority.

Not proven here: installed Frappe/MariaDB behaviour, the real MRO/effective hook map,
filesystem behaviour under power loss, the caller's transaction and the
disposable-site/synthetic-mode gate (Task 8). Those remain native activation evidence.
"""

import hashlib
import mimetypes
import os
import stat
from dataclasses import dataclass, replace

from clinic_photos.access import frappe_context, storage
from clinic_photos.publication import exclusion, journal
from clinic_photos.publication.contracts import (
    CreationReceipt,
    PreparedOperation,
    PublishedOutcome,
)

SAFE_WRITER_DENIED = "Protected first object cannot be written for this operation"
SAFE_WRITER_EXISTS = "Protected first object already exists"
SAFE_WRITER_UNCERTAIN = "Protected first write outcome is uncertain"
SAFE_WRITER_RECEIPT = "Protected publication receipt is not valid"

FILE_MODE = 0o600
READ_CHUNK = 1024 * 1024

# Bounded alias scan: complete canonical comparison over a finite page budget. A
# budget that runs out without a short page is *uncertainty*, never a partial accept.
FILE_SCAN_PAGE_SIZE = 500
FILE_SCAN_MAX_PAGES = 40

_DIRECTORY_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CREATE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_READ_FLAGS = (
    os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)
_INTENT_ATTR = "clinic_photos_first_write_intents"
_ISSUED_ATTR = "clinic_photos_issued_persisted_objects"


class FirstObjectError(Exception):
    """Fixed-message protected-writer refusal."""


class FirstObjectDenied(FirstObjectError):
    pass


class FirstObjectExists(FirstObjectError):
    pass


class FirstObjectUncertain(FirstObjectError):
    pass


class ReceiptInvalid(FirstObjectError):
    pass


class _AliasScanBudgetExceeded(Exception):
    """The bounded alias scan could not prove the complete picture."""


class PersistedObject:
    """Opaque post-hook binding of a provisional receipt to the persisted File.

    Instances exist only through :func:`bind_persisted_object`; construction, copying
    and pickling refuse, and setting attributes after construction refuses too, so a
    caller cannot forge evidence, clone a bound object or rewrite its identity.
    """

    __slots__ = ("_receipt", "_file_name", "_references", "_operation_id", "_used")

    def __init__(self, *args, **kwargs):
        raise TypeError("PersistedObject is issued only by bind_persisted_object")

    def __setattr__(self, name, value):
        if name not in PersistedObject.__slots__ or hasattr(self, name):
            raise TypeError("PersistedObject is immutable")
        object.__setattr__(self, name, value)

    def __copy__(self):
        raise TypeError("PersistedObject cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("PersistedObject cannot be copied")

    def __reduce__(self):
        raise TypeError("PersistedObject cannot be pickled")

    def __reduce_ex__(self, protocol):
        raise TypeError("PersistedObject cannot be pickled")

    @property
    def receipt(self) -> CreationReceipt:
        return self._receipt

    @property
    def file_name(self) -> str:
        return self._file_name

    @property
    def references(self) -> tuple:
        return self._references


# ---------------------------------------------------------------------------
# Execution-local intent evidence
# ---------------------------------------------------------------------------
def _registry(attribute: str) -> dict:
    import frappe

    registry = getattr(frappe.local, attribute, None)
    if registry is None:
        registry = {}
        setattr(frappe.local, attribute, registry)
    return registry


def _issued() -> dict:
    return _registry(_ISSUED_ATTR)


def _intents() -> dict:
    return _registry(_INTENT_ATTR)


def _poison(operation_id: str, reason: str) -> None:
    """Latch uncertainty on the exact live owner; never raise a second error."""
    try:
        exclusion.require_locked_owner(operation_id).poison(reason)
    except BaseException:
        return


def _deny(operation_id: str, reason: str, error: FirstObjectError) -> None:
    _poison(operation_id, reason)
    raise error


def _refuse(operation_id: str, reason: str) -> None:
    _deny(operation_id, reason, FirstObjectDenied(SAFE_WRITER_DENIED))


def _refuse_receipt(operation_id: str, reason: str) -> None:
    _deny(operation_id, reason, ReceiptInvalid(SAFE_WRITER_RECEIPT))


def _refuse_uncertain(operation_id: str, reason: str) -> None:
    _deny(operation_id, reason, FirstObjectUncertain(SAFE_WRITER_UNCERTAIN))


def _live_owner(operation: PreparedOperation) -> "exclusion.LockedOwner":
    """This attempt's still-live owner token, re-authorized and identity-bound.

    A stale, foreign or missing owner is not resumable in this execution. The existing
    execution-wide abort latch is set before the refusal propagates, so a caller that
    catches it cannot continue bind/consume under another owner.
    """
    try:
        owner = exclusion.require_locked_owner(operation.id)
        return owner.require_bindings(
            actor=operation.actor,
            site=operation.site,
            target_identity=operation.target_identity,
        )
    except exclusion.ExclusionError:
        frappe_context._abort_publication("writer_owner_unavailable")
        raise


# ---------------------------------------------------------------------------
# Server-bound evidence
# ---------------------------------------------------------------------------
def _require_persisted_prepared(operation: PreparedOperation) -> PreparedOperation:
    """Re-read the durable PREPARED row and require exactly the presented evidence.

    ``journal._prepared_operation`` is this package's single authoritative verifier for
    a persisted PREPARED row (identity, payload digest, derived key, exact byte
    evidence); reusing it keeps one derivation instead of a second, weaker one.
    """
    row = frappe_context.get_save_operation(operation.id, for_update=True)
    if row is None:
        _refuse(operation.id, "prepared_row_missing")
    try:
        verified = journal._prepared_operation(row)
    except journal.JournalError:
        _refuse(operation.id, "prepared_row_inconsistent")
    if verified != operation:
        _refuse(operation.id, "prepared_evidence_mismatch")
    return verified


def _require_exact_file(file_doc, operation: PreparedOperation) -> bytes:
    """The exact unsaved File object for this key, privacy, owner and byte evidence."""
    import frappe

    is_new = getattr(file_doc, "is_new", None)
    if not callable(is_new) or not is_new():
        _refuse(operation.id, "file_not_new")
    if getattr(file_doc, "name", ""):
        _refuse(operation.id, "file_already_named")
    if operation.visit_binding is not None and any(
        getattr(file_doc, field, None) not in (None, "")
        for field in ("attached_to_doctype", "attached_to_name", "attached_to_field")
    ):
        _refuse(operation.id, "visit_file_preattached")

    raw = storage.file_url_of(file_doc)
    try:
        key = storage.canonical_private_key(raw)
    except storage.InvalidStoragePath:
        _refuse(operation.id, "file_key_not_canonical")
    if raw != key or key != operation.key or not storage.is_reserved_key(key):
        _refuse(operation.id, "file_key_not_reserved")
    if (getattr(file_doc, "file_name", "") or "") != key.rsplit("/", 1)[-1]:
        _refuse(operation.id, "file_name_not_canonical")
    if not int(getattr(file_doc, "is_private", 0) or 0):
        _refuse(operation.id, "file_not_private")
    if (getattr(file_doc, "owner", "") or "") != operation.actor:
        _refuse(operation.id, "file_owner_mismatch")
    if (frappe.session.user or "") != operation.actor:
        _refuse(operation.id, "session_actor_mismatch")
    if (getattr(frappe.local, "site", "") or "") != operation.site:
        _refuse(operation.id, "session_site_mismatch")

    content = getattr(file_doc, "content", None)
    if not isinstance(content, bytes) or not content:
        _refuse(operation.id, "file_bytes_missing")
    if len(content) != operation.expected_size:
        _refuse(operation.id, "file_size_mismatch")
    if hashlib.sha256(content).hexdigest() != operation.expected_sha256:
        _refuse(operation.id, "file_hash_mismatch")
    return content


def _confined_root() -> str:
    import frappe

    path = frappe.get_site_path("private", "files")
    if not isinstance(path, str) or not path:
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    root = os.path.realpath(path)
    if not os.path.isdir(root):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    return root


def _relative_basename(key: str) -> str:
    """Flat canonical basename of a reserved key; never a path or an escape."""
    if not isinstance(key, str) or not key.startswith(storage.PRIVATE_URL_PREFIX):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    relative = key[len(storage.PRIVATE_URL_PREFIX) :]
    if not relative or "/" in relative or "\\" in relative or relative in (".", ".."):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    if os.sep in relative or (os.altsep and os.altsep in relative):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    return relative


def _open_visit_files_directory(owner):
    """Open existing active-site private/files without following directory links."""
    import frappe
    from clinic_photos.publication import staging

    descriptors = []
    try:
        if not all(getattr(os, flag, 0) for flag in ("O_DIRECTORY", "O_NOFOLLOW")):
            raise ValueError
        exclusion.require_visit_annotation_owner(owner)
        site, sites_real = exclusion._site_binding()
        if (site != owner.site or getattr(frappe.local, "site", None) != site
                or sites_real != owner._tree.sites_path
                or site in (".", "..")):
            raise ValueError
        path = frappe.get_site_path("private", "files")
        if not isinstance(path, str) or not path:
            raise ValueError
        path = staging._absolute_staging_path(path)
        if path != os.path.join(sites_real, site, "private", "files"):
            raise ValueError
        sites_fd = os.open(sites_real, _DIRECTORY_FLAGS)
        descriptors.append(sites_fd)
        site_fd = os.open(site, _DIRECTORY_FLAGS, dir_fd=sites_fd)
        descriptors.append(site_fd)
        private_fd = os.open("private", _DIRECTORY_FLAGS, dir_fd=site_fd)
        descriptors.append(private_fd)
        files_fd = os.open("files", _DIRECTORY_FLAGS, dir_fd=private_fd)
        descriptors.append(files_fd)
        for descriptor, name, parent in (
            (sites_fd, sites_real, None), (site_fd, site, sites_fd),
            (private_fd, "private", site_fd),
            (files_fd, "files", private_fd),
        ):
            opened = os.fstat(descriptor)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (not stat.S_ISDIR(opened.st_mode) or not stat.S_ISDIR(entry.st_mode)
                    or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino)):
                raise ValueError
        exclusion.require_visit_annotation_owner(owner)
        if (exclusion._site_binding() != (site, sites_real)
                or getattr(frappe.local, "site", None) != site):
            raise ValueError
        descriptors.pop()
        return files_fd
    except Exception:
        raise FirstObjectDenied(SAFE_WRITER_DENIED) from None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Bounded, complete canonical alias scan
# ---------------------------------------------------------------------------
def _file_url_rows() -> list:
    """Every File row's ``(name, file_url)`` in a bounded page budget.

    Pages are read with the documented ``limit_start``/``limit_page_length``/
    ``order_by`` API and compared canonically, so percent-encoded or otherwise
    variant spellings of the same key are still found. Budget exhaustion raises rather
    than returning a partial view.
    """
    import frappe

    page_size = int(FILE_SCAN_PAGE_SIZE)
    max_pages = int(FILE_SCAN_MAX_PAGES)
    if page_size <= 0 or max_pages <= 0:
        raise _AliasScanBudgetExceeded()
    rows = []
    for index in range(max_pages):
        batch = (
            frappe.db.get_all(
                "File",
                fields=["name", "file_url"],
                limit_start=index * page_size,
                limit_page_length=page_size,
                order_by="name asc",
            )
            or []
        )
        rows.extend(batch)
        if len(batch) < page_size:
            return rows
    raise _AliasScanBudgetExceeded()


def _alias_holders(storage_key: str) -> set:
    """Names of File rows whose canonical key equals ``storage_key``."""
    holders = set()
    for row in _file_url_rows():
        try:
            candidate = storage.canonical_private_key(row.get("file_url") or "")
        except storage.InvalidStoragePath:
            continue
        if candidate == storage_key:
            holders.add(row.get("name") or "")
    return holders


def _require_no_alias(operation: PreparedOperation, *, error) -> None:
    try:
        holders = _alias_holders(operation.key)
    except _AliasScanBudgetExceeded:
        _deny(operation.id, "alias_scan_budget", error(SAFE_WRITER_UNCERTAIN))
    if holders:
        _refuse(operation.id, "key_already_registered")


# ---------------------------------------------------------------------------
# Confined exclusive creation and verification (injectable OS seams)
# ---------------------------------------------------------------------------
def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError("short write")
        view = view[written:]


def _fsync_file(descriptor: int) -> None:
    os.fsync(descriptor)


def _fsync_directory(descriptor: int) -> None:
    os.fsync(descriptor)


def _read_all(descriptor: int, expected_size: int) -> bytes:
    chunks = []
    remaining = expected_size
    while remaining > 0:
        chunk = os.read(descriptor, min(READ_CHUNK, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _require_created_object(info) -> None:
    """Identity of the object this writer just created: regular, single-link, ours."""
    if not stat.S_ISREG(info.st_mode):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if info.st_nlink != 1:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if info.st_uid != os.geteuid():
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)


def _require_matching_entry(descriptor_directory, name, device, inode, size) -> None:
    """The directory entry still names exactly the created object, not a replacement."""
    entry = os.stat(name, dir_fd=descriptor_directory, follow_symlinks=False)
    if not stat.S_ISREG(entry.st_mode):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if (entry.st_dev, entry.st_ino) != (device, inode):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if entry.st_nlink != 1:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if int(entry.st_size) != int(size):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)


def _content_hash(data: bytes) -> str:
    """Native-compatible ``get_content_hash`` value (MD5); metadata only."""
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def _create_exact_object(operation, data) -> CreationReceipt:
    """Create the exact object once at the canonical key without overwriting anything.

    Directory-relative, exclusive and no-follow: an existing path, a symlink, a
    non-canonical or nested basename and a hard-link ambiguity all refuse. Nothing is
    unlinked, truncated or retried on failure; the residue stays as protected evidence
    and the owner is poisoned.
    """
    def fail(kind, reason):
        if kind == "exists":
            _deny(operation.id, reason, FirstObjectExists(SAFE_WRITER_EXISTS))
        elif kind == "uncertain":
            _refuse_uncertain(operation.id, reason)
        else:
            _refuse(operation.id, reason)

    info = _create_confined_bytes(operation.key, data, fail)
    return CreationReceipt(
        operation_id=operation.id,
        generation=operation.generation,
        key=operation.key,
        device=info.st_dev,
        inode=info.st_ino,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _create_confined_bytes(key, data, fail):
    """Private fd mechanics; caller supplies authority and failure handling."""
    callback = fail

    def fail(kind, reason):
        callback(kind, reason)
        raise FirstObjectDenied(SAFE_WRITER_DENIED)

    try:
        root = _confined_root()
        name = _relative_basename(key)
        directory = os.open(root, _DIRECTORY_FLAGS)
    except FirstObjectError:
        fail("denied", "confinement_unavailable")
    except OSError:
        fail("denied", "confinement_unavailable")

    try:
        return _create_bytes_at_directory(directory, name, data, fail)
    finally:
        try:
            os.close(directory)
        except OSError:
            pass


def _create_bytes_at_directory(directory, name, data, fail):
    """Use an already-open directory; its lifetime remains with the caller."""
    callback = fail

    def fail(kind, reason):
        callback(kind, reason)
        raise FirstObjectDenied(SAFE_WRITER_DENIED)

    try:
        if not isinstance(name, str):
            raise FirstObjectDenied(SAFE_WRITER_DENIED)
        _relative_basename(storage.PRIVATE_URL_PREFIX + name)
    except FirstObjectError:
        fail("denied", "confinement_unavailable")

    if not stat.S_ISDIR(os.fstat(directory).st_mode):
        fail("denied", "confinement_unavailable")

    try:
        descriptor = os.open(name, _CREATE_FLAGS, FILE_MODE, dir_fd=directory)
    except FileExistsError:
        fail("exists", "target_exists")
    except OSError:
        fail("denied", "create_refused")

    try:
        try:
            _require_created_object(os.fstat(descriptor))
        except FirstObjectUncertain:
            fail("uncertain", "created_identity_unexpected")

        try:
            _write_all(descriptor, data)
        except OSError:
            fail("uncertain", "write_incomplete")

        try:
            _fsync_file(descriptor)
        except OSError:
            fail("uncertain", "file_not_durable")

        info = os.fstat(descriptor)
        try:
            _require_created_object(info)
        except FirstObjectUncertain:
            fail("uncertain", "object_identity_changed")
    finally:
        try:
            os.close(descriptor)
        except OSError:
            fail("uncertain", "descriptor_close_failed")

    # Verify the actual object, never an unchecked pathname: a fresh confined
    # no-follow descriptor must resolve to the same inode and the same bytes.
    try:
        reader = os.open(name, _READ_FLAGS, dir_fd=directory)
    except OSError:
        fail("uncertain", "readback_failed")
    try:
        read_info = os.fstat(reader)
        if (read_info.st_dev, read_info.st_ino) != (info.st_dev, info.st_ino):
            fail("uncertain", "object_identity_changed")
        if int(read_info.st_size) != len(data):
            fail("uncertain", "object_size_changed")
        try:
            written = _read_all(reader, len(data))
        except OSError:
            fail("uncertain", "readback_failed")
        if written != data:
            fail("uncertain", "readback_mismatch")
    finally:
        try:
            os.close(reader)
        except OSError:
            fail("uncertain", "descriptor_close_failed")

    try:
        _require_matching_entry(directory, name, info.st_dev, info.st_ino, len(data))
    except (OSError, FirstObjectUncertain):
        fail("uncertain", "object_identity_changed")

    try:
        _fsync_directory(directory)
    except OSError:
        fail("uncertain", "directory_not_durable")

    return info


def _read_exact_target(receipt: CreationReceipt) -> bytes:
    """Actual object bytes, re-read from a confined no-follow descriptor and hashed."""
    try:
        root = _confined_root()
        name = _relative_basename(receipt.key)
        directory = os.open(root, _DIRECTORY_FLAGS)
    except (FirstObjectError, OSError):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None

    try:
        try:
            descriptor = os.open(name, _READ_FLAGS, dir_fd=directory)
        except OSError:
            raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
            if info.st_nlink != 1:
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
            if info.st_uid != os.geteuid():
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
            if (info.st_dev, info.st_ino) != (receipt.device, receipt.inode):
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
            if int(info.st_size) != int(receipt.size):
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
            data = _read_all(descriptor, receipt.size)
        finally:
            try:
                os.close(descriptor)
            except OSError:
                raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None
        _require_matching_entry(directory, name, receipt.device, receipt.inode, receipt.size)
    except OSError:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None
    finally:
        try:
            os.close(directory)
        except OSError:
            pass

    if len(data) != receipt.size:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    if hashlib.sha256(data).hexdigest() != receipt.sha256:
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    return data


def _target_matches_receipt(receipt: CreationReceipt) -> bool:
    try:
        root = _confined_root()
        name = _relative_basename(receipt.key)
        directory = os.open(root, _DIRECTORY_FLAGS)
    except (FirstObjectError, OSError):
        return False
    try:
        entry = os.stat(name, dir_fd=directory, follow_symlinks=False)
    except OSError:
        return False
    finally:
        try:
            os.close(directory)
        except OSError:
            return False
    if not stat.S_ISREG(entry.st_mode):
        return False
    if entry.st_nlink != 1:
        return False
    if (entry.st_dev, entry.st_ino) != (receipt.device, receipt.inode):
        return False
    return int(entry.st_size) == int(receipt.size)


# ---------------------------------------------------------------------------
# Intent registry
# ---------------------------------------------------------------------------
def _context_pair(context) -> tuple:
    return (
        getattr(context, "actor", None),
        getattr(context, "draft", "") or "",
        getattr(context, "operation", None),
        getattr(context, "storage_key", None),
        getattr(context, "synthetic_patient", None),
        getattr(context, "operation_id", None),
        getattr(context, "visit_operation", False),
    )


def _expected_context(operation: PreparedOperation) -> tuple:
    # Only the synthetic kind carries a server-derived operation identity in its
    # publication context; a draft-bound context must carry none (annotation_append).
    return (
        operation.actor,
        operation.draft or "",
        "pending_insert",
        operation.key,
        operation.synthetic_patient,
        operation.id if operation.synthetic_patient or operation.visit_binding else None,
        operation.visit_binding is not None,
    )


def _register_intent(operation, file_doc, data) -> dict:
    entry = {
        "operation": operation,
        "document": file_doc,
        "operation_id": operation.id,
        "key": operation.key,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "generation": operation.generation,
        "actor": operation.actor,
        "site": operation.site,
        "context": _expected_context(operation),
        "receipt": None,
        "bound": None,
        "file_name": None,
        "linkage": None,
        "published": None,
        "used": False,
    }
    _intents()[operation.key] = entry
    return entry


def _arm_visit_transaction(operation, entry):
    """Deny commits until future exact slot/PUBLISHED handshake exists."""
    import frappe

    lifetime = {"active": True, "db": frappe.local.db, "finalized": None,
                "commit_verified": False, "commit_acknowledged": False}
    entry["visit_lifetime"] = lifetime
    db = frappe.local.db

    def invalidate():
        lifetime["active"] = False

    def acknowledge_commit():
        if lifetime["commit_verified"]:
            lifetime["commit_acknowledged"] = True
        lifetime["active"] = False

    def refuse_early_commit():
        # CallbackManager pops before invoking; re-arm before raising so retrying
        # commit cannot pass. Frappe clears rollback hooks before before_commit.
        try:
            pending = getattr(db.before_commit, "_functions", None)
            if pending is None:
                raise FirstObjectDenied(SAFE_WRITER_DENIED)
            allowed = tuple(
                item.get("before_commit_callback")
                for item in frappe_context._lock_registry().values()
                if item.get("before_commit_registered")
            )
            if any(not any(callback is known for known in allowed) for callback in pending):
                raise FirstObjectDenied(SAFE_WRITER_DENIED)
            bound = lifetime["finalized"]
            if bound is None:
                raise FirstObjectDenied(SAFE_WRITER_DENIED)
            _verify_staged_visit_final(operation, bound)
            lifetime["commit_verified"] = True
        except BaseException:
            lifetime["active"] = False
            db.before_commit.add(refuse_early_commit)
            _poison(operation.id, "visit_commit_not_finalized")
            raise

    try:
        pending = getattr(db.before_commit, "_functions", None)
        if pending is None or pending:
            _refuse(operation.id, "visit_precommit_callbacks_not_audited")
        db.before_commit.add(refuse_early_commit)
        db.after_commit.add(acknowledge_commit)
        db.before_rollback.add(invalidate)
    except Exception:
        lifetime["active"] = False
        _refuse(operation.id, "visit_transaction_hooks_unavailable")


def _require_visit_lifetime(operation, owner, entry):
    import frappe

    if ((frappe.session.user or "") != operation.actor or
        (getattr(frappe.local, "site", "") or "") != operation.site or
        frappe_context.current_publication_owner() is not owner or
        not entry or not entry.get("visit_lifetime", {}).get("active") or
        entry["visit_lifetime"].get("db") is not getattr(frappe.local, "db", None)):
        _refuse_receipt(operation.id, "visit_transaction_mismatch")
    owner.require_continued()


def finish_visit_transaction(operation, owner, action):
    """Finish the exact owning transaction; any finish error is uncertain."""
    import frappe

    entry = _intent_for_key(operation.key)
    _require_visit_lifetime(operation, owner, entry)
    if action not in ("commit", "rollback"):
        _refuse(operation.id, "visit_invalid_finish")
    lifetime = entry["visit_lifetime"]
    try:
        getattr(lifetime["db"], action)()
        if action == "commit" and not (
            lifetime["commit_verified"] and lifetime["commit_acknowledged"]
        ):
            raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    except BaseException:
        lifetime["active"] = False
        owner.poison("visit_transaction_finish_uncertain")
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None


def rollback_visit_transaction(operation, owner, db):
    """One cleanup rollback on the pinned connection, even after owner poison."""
    import frappe

    attempted = getattr(frappe.local, "_clinic_visit_rollbacks", None)
    if attempted is None:
        attempted = set()
        frappe.local._clinic_visit_rollbacks = attempted
    token = (id(owner), id(db), operation.id)
    entry = _intent_for_key(operation.key)
    if (token in attempted or frappe_context.current_publication_owner() is not owner or
        getattr(frappe.local, "db", None) is not db or
        (frappe.session.user or "") != operation.actor or
        (getattr(frappe.local, "site", "") or "") != operation.site or
        (entry is not None and (entry.get("operation") != operation or
         entry.get("visit_lifetime", {}).get("db") is not db))):
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN)
    attempted.add(token)
    if entry and entry.get("visit_lifetime"):
        entry["visit_lifetime"]["active"] = False
    try:
        db.rollback()
    except BaseException:
        owner.poison("visit_transaction_rollback_uncertain")
        raise FirstObjectUncertain(SAFE_WRITER_UNCERTAIN) from None


def _intent_for_document(file_doc):
    """The intent registered for exactly this object reference (never ``id()`` only)."""
    for entry in _intents().values():
        if entry.get("document") is file_doc:
            return entry
    return None


def _intent_for_key(storage_key: str):
    return _intents().get(storage_key)


def owns_first_object(file_doc) -> bool:
    """Whether this exact object has an unspent server-owned first-write intent."""
    entry = _intent_for_document(file_doc)
    return entry is not None and not entry["used"]


def first_write_intent_authorized(file_doc, storage_key: str, context) -> bool:
    """Minimal first-content gate: exact object, same owner/context, exact bytes."""
    import frappe

    if file_doc is None or context is None:
        return False
    entry = _intent_for_document(file_doc)
    if entry is None or entry["used"]:
        return False
    if entry["receipt"] is not None:
        # The one first write for this key already happened.
        return False
    operation = entry["operation"]
    if entry["key"] != storage_key:
        return False
    if entry["context"] != _context_pair(context):
        return False
    expected_operation_id = operation.id if operation.synthetic_patient or operation.visit_binding else None
    if getattr(context, "operation_id", None) != expected_operation_id:
        return False
    if (frappe.session.user or "") != operation.actor:
        return False
    if (getattr(frappe.local, "site", "") or "") != operation.site:
        return False
    if not int(getattr(file_doc, "is_private", 0) or 0):
        return False
    is_new = getattr(file_doc, "is_new", None)
    if not callable(is_new) or not is_new() or getattr(file_doc, "name", ""):
        return False
    content = getattr(file_doc, "content", None)
    if not isinstance(content, bytes) or len(content) != operation.expected_size:
        return False
    if hashlib.sha256(content).hexdigest() != operation.expected_sha256:
        return False
    try:
        _live_owner(operation)
    except exclusion.ExclusionError:
        return False
    return True


# ---------------------------------------------------------------------------
# Native first-save specialization (the only place bytes are created)
# ---------------------------------------------------------------------------
def _require_native_write_configuration(operation: PreparedOperation) -> None:
    """Refuse an unknown or registered native write/security hook configuration.

    Resolution uses the pinned 16.53 helper at its real namespace,
    ``frappe.utils.get_hook_method`` (native ``file.py`` imports it from there), for
    ``before_write_file``/``write_file``. A registered writer hook or a resolution
    failure blocks the protected mode instead of being silently bypassed; an empty
    hook map is the compatible configuration. The import stays lazy so importing this
    module never touches the framework.
    """
    try:
        from frappe.utils import get_hook_method

        before_hook = get_hook_method("before_write_file")
        write_hook = get_hook_method("write_file")
    except BaseException:
        _refuse(operation.id, "native_hook_resolution_failed")
    if before_hook or write_hook:
        _refuse(operation.id, "native_write_hook_registered")


def save_first_object(
    file_doc,
    *,
    content=None,
    decode=False,
    ignore_existing_file_check=False,
    overwrite=False,
):
    """Protected first-save body, called from the mixin inside native ``save_file``.

    Native create permission and the ``before_insert`` validations have already run.
    This body resolves the effective write hooks, applies the native maximum-file-size
    check, writes native-compatible metadata and then performs the confined exact-object
    creation. It never falls back to native writing.
    """
    entry = _intent_for_document(file_doc)
    if entry is None:
        # No server-owned intent exists for this object, so there is no operation
        # authority to refuse against. Abort the execution rather than leave a direct
        # caller free to resume under a fresh owner.
        frappe_context._abort_publication("first_save_without_intent")
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    operation = entry["operation"]
    operation_id = operation.id

    try:
        owner = _live_owner(operation)
        if not frappe_context.publication_transaction_live():
            # Direct seam use outside the writer's transaction-owning caller: no
            # supported owner transaction exists, so refuse and stop this execution.
            frappe_context._abort_publication("first_save_without_transaction")
            _refuse(operation_id, "first_save_without_transaction")
        # The same owner scope the public writer uses, so every refusal below latches
        # the execution abort even when this seam is called directly.
        with frappe_context.owned_publication_transaction(owner):
            if entry["used"] or entry["receipt"] is not None:
                _refuse(operation_id, "first_write_already_used")
            if decode or overwrite or ignore_existing_file_check:
                _refuse(operation_id, "forced_write_mode")

            _require_persisted_prepared(operation)
            data = _require_exact_file(file_doc, operation)
            if entry["context"] != _context_pair(
                frappe_context.current_publication()
            ):
                _refuse(operation_id, "context_changed")
            if content is not None:
                if not isinstance(content, bytes) or content != data:
                    _refuse(operation_id, "content_substituted")

            _require_native_write_configuration(operation)

            file_doc.content = data
            file_doc._content = data
            file_doc.content_type = mimetypes.guess_type(file_doc.file_name)[0]
            file_doc.file_size = file_doc.check_max_file_size()
            file_doc.content_hash = _content_hash(data)

            receipt = _create_exact_object(operation, data)
            entry["receipt"] = receipt
            file_doc.file_url = operation.key
            file_doc.is_private = 1
            return {
                "file_name": operation.key.rsplit("/", 1)[-1],
                "file_url": operation.key,
            }
    except BaseException:
        _poison(operation_id, "first_save_refused")
        raise


def verify_exclusive_first_object(file_doc) -> bool:
    """Whether native duplicate validation must not retarget this exact object."""
    entry = _intent_for_document(file_doc)
    if entry is None or entry["used"]:
        return False
    operation = entry["operation"]
    receipt = entry.get("receipt")
    if not isinstance(receipt, CreationReceipt):
        _refuse_receipt(operation.id, "duplicate_before_first_write")
    _require_no_alias(operation, error=FirstObjectUncertain)
    if not _target_matches_receipt(receipt):
        _refuse_uncertain(operation.id, "target_changed")
    return True


# ---------------------------------------------------------------------------
# Public writer seams
# ---------------------------------------------------------------------------
def write_first_object(operation: PreparedOperation, file_doc) -> PersistedObject:
    """Write exactly one protected object for this PREPARED operation.

    Bytes are created only by the native first-save specialization during the ordinary
    ``File.insert()`` this function performs. The returned ``PersistedObject`` is the
    post-hook binding of the provisional receipt to the persisted File identity.
    """
    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    owner = _live_owner(operation)
    if operation.visit_binding is not None:
        owner.require_continued()
    with frappe_context.owned_publication_transaction(owner):
        _require_persisted_prepared(operation)
        _require_exact_file(file_doc, operation)
        _require_no_alias(operation, error=FirstObjectDenied)
        entry = _register_intent(operation, file_doc, file_doc.content)
        if operation.visit_binding is not None:
            _arm_visit_transaction(operation, entry)

        try:
            with frappe_context.bound_publication(
                actor=operation.actor,
                draft=operation.draft or "",
                operation="pending_insert",
                operation_id=(
                    operation.id if operation.synthetic_patient or operation.visit_binding else None
                ),
                storage_key=operation.key,
                synthetic_patient=operation.synthetic_patient,
                visit_operation=operation.visit_binding is not None,
            ):
                file_doc.insert()
        except BaseException:
            _poison(operation.id, "native_insert_failed")
            # A veto after the first-save window leaves the residue and the abort
            # latch, never a usable receipt.
            entry["receipt"] = None
            raise

        receipt = entry.get("receipt")
        if not isinstance(receipt, CreationReceipt):
            _refuse_receipt(operation.id, "native_first_save_not_reached")
        return bind_persisted_object(operation, receipt, file_doc)


# ---------------------------------------------------------------------------
# Provisional receipt -> persisted identity bridge
# ---------------------------------------------------------------------------
def _require_persisted_file(operation, receipt: CreationReceipt, file_name: str):
    import frappe

    row = frappe.db.get_value(
        "File",
        file_name,
        ["name", "file_url", "file_name", "is_private", "owner", "file_size"],
        as_dict=True,
    )
    if not row or (row.get("name") or "") != file_name:
        _refuse_receipt(operation.id, "persisted_file_missing")

    raw = row.get("file_url") or ""
    try:
        key = storage.canonical_private_key(raw)
    except storage.InvalidStoragePath:
        _refuse_receipt(operation.id, "persisted_key_invalid")
    if raw != key or key != receipt.key:
        _refuse_receipt(operation.id, "persisted_key_mismatch")
    if (row.get("file_name") or "") != key.rsplit("/", 1)[-1]:
        _refuse_receipt(operation.id, "persisted_file_name_mismatch")
    if not int(row.get("is_private") or 0):
        _refuse_receipt(operation.id, "persisted_not_private")
    if (row.get("owner") or "") != operation.actor:
        _refuse_receipt(operation.id, "persisted_owner_mismatch")
    try:
        persisted_size = int(row.get("file_size") or 0)
    except (TypeError, ValueError):
        _refuse_receipt(operation.id, "persisted_size_invalid")
    if persisted_size != receipt.size:
        _refuse_receipt(operation.id, "persisted_size_mismatch")

    try:
        holders = _alias_holders(receipt.key)
    except _AliasScanBudgetExceeded:
        _refuse_receipt(operation.id, "alias_scan_budget")
    if holders != {file_name}:
        # Complete canonical comparison over the bounded scan: this persisted row is
        # the only holder of the key, no variant spelling shares it.
        _refuse_receipt(operation.id, "persisted_alias")
    return row


def _reference_markers(operation, file_name, linkage=None):
    """Authoritative references of this exact object: own reservation plus linkage.

    ``linkage`` is a server-derived :class:`DomainLinkage` for the one domain record
    this publication creates; it is re-verified against the persisted row before it is
    accepted, so the receipt update can never be widened to unrelated references.
    """
    references = frappe_context.lookup_protection(operation.key, file_name) or ()
    markers = tuple(
        sorted(
            (
                getattr(reference, "kind", ""),
                getattr(reference, "doctype", ""),
                getattr(reference, "name", ""),
                getattr(reference, "storage_key", ""),
            )
            for reference in references
        )
    )
    expected = [("reservation", journal.OPERATION_DOCTYPE, operation.id, operation.key)]
    if linkage is not None:
        expected.append(linkage.marker)
    if markers != tuple(sorted(expected)):
        _refuse_receipt(operation.id, "reference_binding_changed")
    return markers


@dataclass(frozen=True)
class DomainLinkage:
    """The one server-derived domain reference this publication created.

    Instances exist only through :func:`domain_linkage`, which verifies the persisted
    domain row against the operation's own draft/patient/key/byte evidence. The value
    is a marker pair, never an arbitrary caller-supplied reference list.
    """

    doctype: str
    kind: str
    name: str
    storage_key: str

    @property
    def marker(self) -> tuple:
        return (self.kind, self.doctype, self.name, self.storage_key)


_LINKAGE_KINDS = {
    "Clinic Photo": "original",
    "Clinic Photo Annotation Version": "annotation",
}

_LINKAGE_FIELDS = {
    "Clinic Photo": (
        "name",
        "storage_key",
        "original_file",
        "original_sha256",
        "patient_reference",
    ),
    "Clinic Photo Annotation Version": (
        "name",
        "draft",
        "version_number",
        "annotation_file",
        "storage_key",
        "state",
    ),
}


def domain_linkage(
    operation: PreparedOperation, doctype: str, name: str, file_name: str
) -> DomainLinkage:
    """Verify and return the expected domain reference for this exact publication.

    ``doctype`` is limited to the two approved domain records and the persisted row
    must agree with the operation: the same canonical key, this operation's draft for a
    version, the committed state and the exact persisted File name, or the synthetic
    patient/byte evidence for an original. Any disagreement refuses and poisons.
    """
    import frappe

    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    if operation.visit_binding is not None:
        owner = _live_owner(operation)
        owner.require_continued()
        _require_persisted_prepared(operation)
        if doctype != "Clinic Photo":
            _refuse(operation.id, "visit_linkage_doctype")
    kind = _LINKAGE_KINDS.get(doctype)
    if kind is None or not isinstance(name, str) or not name or name.strip() != name:
        _refuse(operation.id, "linkage_doctype_or_name")
    if not isinstance(file_name, str) or not file_name:
        _refuse(operation.id, "linkage_file_missing")

    row = (
        frappe.db.get_value(
            doctype, name, list(_LINKAGE_FIELDS[doctype]), as_dict=True, for_update=True
        )
        or None
    )
    if row is None or (row.get("name") or "") != name:
        _refuse(operation.id, "linkage_row_missing")
    storage_key = row.get("storage_key") or ""
    try:
        canonical = storage.canonical_private_key(storage_key)
    except storage.InvalidStoragePath:
        _refuse(operation.id, "linkage_key_not_canonical")
    if canonical != storage_key or storage_key != operation.key:
        _refuse(operation.id, "linkage_key_mismatch")

    if kind == "annotation":
        if (
            (row.get("draft") or "") != operation.draft
            or row.get("state") != "committed"
            or (row.get("annotation_file") or "") != file_name
        ):
            _refuse(operation.id, "linkage_version_mismatch")
    else:
        if (
            (row.get("original_file") or "") != file_name
            or (row.get("original_sha256") or "") != operation.expected_sha256
            or (row.get("patient_reference") or "") != (
                operation.visit_binding.patient if operation.visit_binding else operation.synthetic_patient
            )
        ):
            _refuse(operation.id, "linkage_original_mismatch")
    return DomainLinkage(
        doctype=doctype, kind=kind, name=name, storage_key=operation.key
    )


def bind_persisted_object(
    operation: PreparedOperation, receipt: CreationReceipt, file_doc
) -> PersistedObject:
    """Bind the provisional receipt to the persisted File identity, once.

    Runs only after the complete native insert/hook sequence returned, inside this
    attempt's live owner transaction, so a refusal latches the execution abort.
    """
    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    owner = _live_owner(operation)
    with frappe_context.owned_publication_transaction(owner):
        return _bind_locked(operation, receipt, file_doc)


def _bind_locked(operation, receipt, file_doc) -> PersistedObject:
    if not isinstance(receipt, CreationReceipt):
        _refuse(operation.id, "receipt_type")

    entry = _intent_for_key(operation.key)
    if entry is None or entry.get("operation_id") != operation.id:
        _refuse_receipt(operation.id, "receipt_not_issued")
    if entry.get("document") is not file_doc:
        _refuse_receipt(operation.id, "receipt_object_substituted")
    if entry.get("bound") is not None or entry.get("used"):
        _refuse_receipt(operation.id, "receipt_already_bound")
    if entry.get("receipt") is not receipt:
        # Issuance identity, not an equal field tuple.
        _refuse_receipt(operation.id, "receipt_substituted")

    file_name = storage.file_name_of(file_doc)
    if not file_name:
        _refuse_receipt(operation.id, "persisted_name_missing")

    _require_persisted_prepared(operation)
    _require_persisted_file(operation, receipt, file_name)
    try:
        _read_exact_target(receipt)
    except FirstObjectUncertain:
        _refuse_uncertain(operation.id, "persisted_bytes_changed")
    references = _reference_markers(operation, file_name)

    bound = object.__new__(PersistedObject)
    object.__setattr__(bound, "_receipt", receipt)
    object.__setattr__(bound, "_file_name", file_name)
    object.__setattr__(bound, "_references", references)
    object.__setattr__(bound, "_operation_id", operation.id)
    object.__setattr__(bound, "_used", False)
    _issued()[id(bound)] = bound
    entry["file_name"] = file_name
    entry["bound"] = bound
    return bound


def consume_persisted_object(
    operation: PreparedOperation, bound, *, domain_linkage=None
) -> str:
    """Consume the bound receipt exactly once and return the persisted File name."""
    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    owner = _live_owner(operation)
    with frappe_context.owned_publication_transaction(owner):
        if operation.visit_binding is not None:
            _require_visit_lifetime(operation, owner, _intent_for_key(operation.key))
        return _consume_locked(operation, bound, domain_linkage)


def _consume_locked(operation, bound, linkage=None) -> str:
    if type(bound) is not PersistedObject or _issued().get(id(bound)) is not bound:
        _refuse_receipt(operation.id, "unissued_binding")

    receipt = bound._receipt
    if bound._operation_id != operation.id or receipt.key != operation.key:
        _refuse_receipt(operation.id, "binding_reused")
    entry = _intent_for_key(operation.key)
    if entry is None or entry.get("operation_id") != operation.id:
        _refuse_receipt(operation.id, "binding_unknown")
    if entry.get("bound") is not bound:
        _refuse_receipt(operation.id, "binding_substituted")
    if entry.get("receipt") is not receipt:
        _refuse_receipt(operation.id, "receipt_substituted")
    if bound._used or entry.get("used"):
        _refuse_receipt(operation.id, "receipt_replay")
    if operation.visit_binding is not None and type(linkage) is not DomainLinkage:
        _refuse_receipt(operation.id, "visit_linkage_missing")

    _require_persisted_prepared(operation)
    _require_persisted_file(operation, receipt, bound._file_name)
    try:
        _read_exact_target(receipt)
    except FirstObjectUncertain:
        _refuse_uncertain(operation.id, "persisted_bytes_changed")
    if linkage is not None:
        _require_domain_linkage(operation, linkage, bound._file_name)
    if operation.visit_binding is not None:
        _require_visit_attachment(operation, bound._file_name, linkage)
    actual = _reference_markers(operation, bound._file_name, linkage)
    expected = tuple(
        sorted(
            bound._references
            + ((linkage.marker,) if linkage is not None else ())
        )
    )
    if actual != expected:
        _refuse_receipt(operation.id, "reference_binding_changed")

    object.__setattr__(bound, "_used", True)
    entry["used"] = True
    entry["linkage"] = linkage
    return bound._file_name


def _require_visit_attachment(operation, file_name, linkage):
    import frappe

    if type(linkage) is not DomainLinkage or linkage.doctype != "Clinic Photo":
        _refuse_receipt(operation.id, "visit_linkage_missing")
    row = frappe.db.get_value(
        "File", file_name, ["name", "attached_to_doctype", "attached_to_name"],
        as_dict=True, for_update=True,
    )
    if (not row or row.get("name") != file_name or
        row.get("attached_to_doctype") != "Clinic Photo" or
        row.get("attached_to_name") != linkage.name):
        _refuse_receipt(operation.id, "visit_file_attachment_mismatch")


def require_visit_consumed_object(operation: PreparedOperation, bound):
    """Reverify and return exact issued evidence for a future same-transaction slot."""
    if not isinstance(operation, PreparedOperation) or operation.visit_binding is None:
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    owner = _live_owner(operation)
    owner.require_continued()
    with frappe_context.owned_publication_transaction(owner):
        _require_visit_lifetime(operation, owner, _intent_for_key(operation.key))
        if type(bound) is not PersistedObject or _issued().get(id(bound)) is not bound:
            _refuse_receipt(operation.id, "unissued_binding")
        entry = _intent_for_key(operation.key)
        if (entry is None or entry.get("operation") != operation or
            entry.get("bound") is not bound or entry.get("receipt") is not bound._receipt or
            not bound._used or not entry.get("used")):
            _refuse_receipt(operation.id, "visit_binding_mismatch")
        linkage = entry.get("linkage")
        _require_persisted_prepared(operation)
        _require_persisted_file(operation, bound._receipt, bound._file_name)
        try:
            _read_exact_target(bound._receipt)
        except FirstObjectUncertain:
            _refuse_uncertain(operation.id, "persisted_bytes_changed")
        _require_domain_linkage(operation, linkage, bound._file_name)
        _require_visit_attachment(operation, bound._file_name, linkage)
        _reference_markers(operation, bound._file_name, linkage)
        return bound, linkage


def _verify_staged_visit_final(operation, bound):
    """Recheck exact consumed receipt, staged PUBLISHED outcome and slot."""
    import frappe

    owner = _live_owner(operation)
    entry = _intent_for_key(operation.key)
    _require_visit_lifetime(operation, owner, entry)
    if (type(bound) is not PersistedObject or _issued().get(id(bound)) is not bound or
        entry.get("operation") != operation or entry.get("bound") is not bound or
        entry.get("receipt") is not bound._receipt or not entry.get("used") or
        not bound._used or entry.get("published") is not True):
        _refuse_receipt(operation.id, "visit_final_binding_mismatch")
    linkage = entry.get("linkage")
    if type(linkage) is not DomainLinkage or linkage.doctype != "Clinic Photo":
        _refuse_receipt(operation.id, "visit_final_linkage_missing")
    try:
        verified = journal.load_verified_operation(operation.id, for_update=True)
    except journal.JournalError:
        _refuse_receipt(operation.id, "visit_final_journal_invalid")
    published = verified.published
    if (verified.state != "PUBLISHED" or not published or
        published.reservation != replace(operation, state="PUBLISHED") or
        published.file_name != bound._file_name or published.domain_doctype != linkage.doctype or
        published.domain_name != linkage.name or
        published.creation_identity != f"{bound._receipt.device}:{bound._receipt.inode}"):
        _refuse_receipt(operation.id, "visit_final_journal_mismatch")
    _require_persisted_file(operation, bound._receipt, bound._file_name)
    try:
        _read_exact_target(bound._receipt)
    except FirstObjectUncertain:
        _refuse_uncertain(operation.id, "persisted_bytes_changed")
    row = frappe.db.get_value(
        "Clinic Photo", linkage.name, list(_LINKAGE_FIELDS["Clinic Photo"]),
        as_dict=True, for_update=True,
    )
    if (not row or row.get("name") != linkage.name or
        row.get("storage_key") != operation.key or row.get("original_file") != bound._file_name or
        row.get("original_sha256") != operation.expected_sha256 or
        row.get("patient_reference") != operation.visit_binding.patient):
        _refuse_receipt(operation.id, "visit_final_photo_mismatch")
    _require_visit_attachment(operation, bound._file_name, linkage)
    _reference_markers(operation, bound._file_name, linkage)


def arm_visit_final_commit(operation: PreparedOperation, bound):
    """Authorize only this issued receipt after exact PUBLISHED+slot staging."""
    if not isinstance(operation, PreparedOperation) or operation.visit_binding is None:
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    _verify_staged_visit_final(operation, bound)
    entry = _intent_for_key(operation.key)
    entry["visit_lifetime"]["finalized"] = bound


def require_consumed_object(operation: PreparedOperation, bound):
    """Narrow one-shot handoff: this exact bound object was consumed for this write.

    ``journal.publish`` uses this seam, so staging ``PUBLISHED`` intrinsically requires
    the consumed first write instead of relying on coordinator call order. The exact
    issued binding is re-verified against the persisted File row, the actual bytes and
    the one server-issued ``DomainLinkage`` recorded when the receipt was consumed, and
    the handoff is spent here: a second publication of the same object refuses.
    Returns ``(file_name, linkage, receipt)``.
    """
    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    owner = _live_owner(operation)
    with frappe_context.owned_publication_transaction(owner):
        if operation.visit_binding is not None:
            _require_visit_lifetime(operation, owner, _intent_for_key(operation.key))
        return _require_consumed_locked(operation, bound)


def _require_consumed_locked(operation, bound):
    if type(bound) is not PersistedObject or _issued().get(id(bound)) is not bound:
        # A receipt, a dataclass or any field-equal value is not a consumed binding.
        _refuse_receipt(operation.id, "unissued_binding")

    receipt = bound._receipt
    entry = _intent_for_key(operation.key)
    if entry is None or entry.get("operation_id") != operation.id:
        _refuse_receipt(operation.id, "binding_unknown")
    if entry.get("receipt") is not receipt or entry.get("bound") is not bound:
        _refuse_receipt(operation.id, "binding_substituted")
    if not bound._used or not entry.get("used"):
        _refuse_receipt(operation.id, "receipt_not_consumed")
    if entry.get("published") is not None:
        _refuse_receipt(operation.id, "publication_handoff_spent")

    linkage = entry.get("linkage")
    if type(linkage) is not DomainLinkage:
        _refuse_receipt(operation.id, "linkage_missing")

    _require_persisted_prepared(operation)
    _require_persisted_file(operation, receipt, bound._file_name)
    try:
        _read_exact_target(receipt)
    except FirstObjectUncertain:
        _refuse_uncertain(operation.id, "persisted_bytes_changed")
    _require_domain_linkage(operation, linkage, bound._file_name)

    entry["published"] = True
    return bound._file_name, linkage, receipt


def _require_domain_linkage(operation, linkage, file_name) -> None:
    """Re-verify the expected linkage against the persisted row at consume time."""
    if type(linkage) is not DomainLinkage:
        _refuse_receipt(operation.id, "linkage_not_issued")
    verified = domain_linkage(
        operation, linkage.doctype, linkage.name, file_name
    )
    if verified != linkage:
        _refuse_receipt(operation.id, "linkage_substituted")


class _ReadbackRefusal(FirstObjectUncertain):
    """Fixed-message readback refusal carrying this writer's existing poison reason.

    ``reason`` stays an internal diagnostic for the owner-bound wrapper: it is never
    a client value, never serialized and never authorization evidence.
    """

    def __init__(self, reason: str):
        super().__init__(SAFE_WRITER_UNCERTAIN)
        self.reason = reason


def _readback_committed_object(operation, file_name, expected_identity) -> str:
    """Side-effect-free descriptor/hash/identity readback of one committed object.

    Reads, stats, hashes and closes only. It never poisons an owner, latches an abort,
    creates a registry entry, mutates publication state, takes a lock, commits, rolls
    back, repairs, deletes or mints authority; every refusal is a fixed-message error
    raised here. The object is reached through the confined private root and the
    canonical key derived from the operation evidence, never a caller path.
    """
    try:
        root = _confined_root()
        name = _relative_basename(operation.key)
        directory = os.open(root, _DIRECTORY_FLAGS)
    except (FirstObjectError, OSError):
        raise _ReadbackRefusal("published_target_unavailable") from None
    try:
        try:
            descriptor = os.open(name, _READ_FLAGS, dir_fd=directory)
        except OSError:
            raise _ReadbackRefusal("published_target_unavailable") from None
        try:
            try:
                info = os.fstat(descriptor)
            except OSError:
                raise _ReadbackRefusal("published_read_failed") from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise _ReadbackRefusal("published_object_identity")
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise _ReadbackRefusal("published_object_identity")
            if expected_identity is not None and (
                (info.st_dev, info.st_ino) != expected_identity
            ):
                # The committed object is not the object the publication created.
                raise _ReadbackRefusal("published_object_identity")
            if int(info.st_size) != operation.expected_size:
                raise _ReadbackRefusal("published_object_size")
            try:
                data = _read_all(descriptor, operation.expected_size)
            except OSError:
                raise _ReadbackRefusal("published_read_failed") from None
        finally:
            try:
                os.close(descriptor)
            except OSError:
                raise _ReadbackRefusal("descriptor_close_failed") from None
        try:
            _require_matching_entry(
                directory, name, info.st_dev, info.st_ino, operation.expected_size
            )
        except (OSError, FirstObjectUncertain):
            raise _ReadbackRefusal("published_object_identity") from None
    finally:
        try:
            os.close(directory)
        except OSError:
            pass
    if hashlib.sha256(data).hexdigest() != operation.expected_sha256:
        raise _ReadbackRefusal("published_bytes_changed")
    return hashlib.sha256(data).hexdigest()


def verify_committed_object(published: PublishedOutcome) -> str:
    """Ownerless readback of one server-loaded durable publication outcome.

    Accepted evidence is only the committed ``PublishedOutcome`` this package verified
    from a ``PUBLISHED`` row, so the canonical key, expected size, expected SHA-256 and
    the creation identity are all server-derived and always present: nothing about the
    object is supplied by the caller. Returns the SHA-256 only — never a path, byte
    content, File name or domain identifier.

    This is the read-only half of the post-commit verification. It holds no exclusion
    owner and must not: it performs no registry write, publication-state mutation,
    owner poisoning, abort latching, locking, commit/rollback, repair or deletion.
    """
    if not isinstance(published, PublishedOutcome):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    return _readback_committed_object(
        published.reservation,
        published.file_name,
        (published.device, published.inode),
    )


def verify_published_object(
    operation: PreparedOperation,
    file_name: str,
    *,
    creation_identity: str | None = None,
) -> str:
    """Post-commit readback of the exact committed object; returns its SHA-256.

    The coordinator calls this only after a known commit. It requires this attempt's
    live exclusion owner, then delegates to the one shared readback: it re-opens the
    confined canonical key, verifies the object identity/size — including the persisted
    ``dev:inode`` creation identity when one is supplied — and re-hashes the actual
    bytes, so ``saved`` is never claimed from a row alone. It reads nothing from a
    caller path and never writes, unlinks or truncates.

    Failure classification, the per-branch reason and the owner poisoning of Task 8 are
    preserved exactly; a missing owner still latches the execution-wide abort.
    """
    if not isinstance(operation, PreparedOperation):
        raise FirstObjectDenied(SAFE_WRITER_DENIED)
    if not isinstance(file_name, str) or not file_name:
        _refuse_receipt(operation.id, "published_file_missing")
    expected_identity = None
    if creation_identity is not None:
        parts = str(creation_identity).split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            _refuse_receipt(operation.id, "published_identity_malformed")
        expected_identity = (int(parts[0]), int(parts[1]))
    try:
        _live_owner(operation)
    except exclusion.ExclusionError:
        frappe_context._abort_publication("published_verify_without_owner")
        raise
    try:
        return _readback_committed_object(operation, file_name, expected_identity)
    except _ReadbackRefusal as error:
        _refuse_uncertain(operation.id, error.reason)
        raise  # pragma: no cover - _refuse_uncertain always raises
