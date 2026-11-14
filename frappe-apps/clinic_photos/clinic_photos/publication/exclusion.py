"""Cross-process operation/target exclusion for protected photo publication.

The single-host shared-sites-volume protocol is one pair of stable OS advisory locks
per publication attempt, acquired in a fixed order:

1. the **operation** lock, derived from the server-owned opaque operation ID;
2. the **target** lock, derived from the server-derived target identity: the draft for
   an annotation append, or the synthetic patient-creation scope.

Lock objects live in a fixed, server-derived directory on the sites volume. They are
opened with ``O_NOFOLLOW`` and verified from the descriptor itself (regular file,
single link, current user, no group/other access) before ``flock(LOCK_EX | LOCK_NB)``.
A busy object refuses immediately. There is no timeout, lease, Redis key, queue state,
process status or HTTP marker that can authorize a takeover, because none of those
prove the current writer stopped. Objects are never unlinked: unlinking would let the
next attempt lock a fresh inode while the previous writer still holds the old one.

Authority is not a Python-side claim. ``LockedOwner`` is created only by a private
factory inside this module, is registered by exact object identity together with both
held objects, the process ID and the immutable initial bindings, and refuses to be
constructed, copied, deep-copied or pickled. Every authorization re-verifies, before it
answers: registry identity, PID, lifecycle, each descriptor's device/inode/type/link
count/owner/mode, the current path entry and the current stable directory tree, and an
independent lock-availability probe on each object.

Supported trusted protocol: code using this module controls these descriptors
exclusively. It performs no external ``close``/``LOCK_UN``/``dup2`` of them, no
concurrent release while a protected mutation is running, and no concurrent maintenance
of the stable lock paths. This is not a security boundary against arbitrary Python
introspection or same-uid filesystem interference.

The probe is an *additional loss detector*, never an ownership proof: a fresh
description is opened without create/truncate/no-follow, its inode must match, and a
non-blocking exclusive lock is attempted. Success means the object was lockable, so the
probe is closed and the owner is poisoned and refused - the probe lock is never adopted.
Busy proves only that *some* holder exists. Any other error is uncertainty. Portable
``flock`` cannot attribute a conflicting lock to a token, so an external unlock followed
by a competitor, or a close/reopen onto the same inode, is outside what this protocol can
detect. If that stronger guarantee is ever required, this protocol must not be claimed to
provide it.

A ``fork()`` without ``exec()`` child is handled once by a fork hook: inherited owners are
invalidated, inherited descriptors are closed without ``LOCK_UN``, and the registries and
local attempts are cleared, so the child can neither authorize nor release its parent's
locks and its own inherited cleanup is harmless. General multithreaded-fork safety is not
established.

Deliberately absent, and not claimed:

* no preparation, key reservation, byte write, commit or publication happens here;
* the scope admits ``SUBMITTED`` only, before ``yield``; a ``PREPARED`` operation is
  refused at entry and again at the locked read, and that known refusal permanently
  spends the token's authority (it stays unpoisoned, because a definite state is not
  uncertainty, but every later authorization refuses). An unrecognised state is
  malformed: refused before any lock at entry, and poisoned at the locked read. There is
  no public continuation opt-out for a *fresh* attempt; only the exact live owner that
  itself committed this attempt's preparation may continue over its own ``PREPARED``
  evidence (``LockedOwner.continue_after_own_preparation``), and that continuation is
  not durable evidence and cannot be transferred to another token. Durable
  owner/generation evidence belongs to the preparation record, and native unique
  constraints remain an additional check, never the lock;
* ``attempt_generation`` is a bounded, process-local ordering counter; it is not durable
  evidence and never substitutes for the persisted generation of a later task.
"""

import errno
import fcntl
import hashlib
import os
import re
import stat
import sys
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass

from clinic_photos.access import frappe_context
from clinic_photos.publication.contracts import PreparedOperation

LOCK_ROOT_NAME = ".clinic-photos-exclusion"
LOCK_FILE_MODE = 0o600
LOCK_DIRECTORY_MODE = 0o700

SITE_DOMAIN = b"clinic-photos-exclusion-site-v1"
OPERATION_DOMAIN = b"clinic-photos-exclusion-operation-v1"
TARGET_DOMAIN = b"clinic-photos-exclusion-target-v1"

TARGET_PREFIXES = ("draft", "patient-creation")
MAX_TARGET_IDENTITY_LENGTH = 512

SUBMITTED_STATE = "SUBMITTED"
PREPARED_STATE = "PREPARED"
DURABLE_STATES = frozenset(
    {"SUBMITTED", "PREPARED", "PUBLISHED", "ABORTED", "QUARANTINED"}
)

LOCAL_ATTEMPT_LIMIT = 1024

SAFE_EXCLUSION_DENIED = "Publication exclusion is unavailable for this operation"
SAFE_EXCLUSION_BUSY = "Publication exclusion is held by another writer"
SAFE_EXCLUSION_LOST = "Publication exclusion ownership could not be confirmed"
SAFE_EXCLUSION_POISONED = "Publication ownership is no longer valid"
SAFE_EXCLUSION_NO_RETRY = "Publication may not restart from this operation state"

_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_BUSY_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK})

# Authority registries, keyed by exact object identity. ``_HELD_LOCKS`` and
# ``_HELD_PATHS`` are populated at acquisition, before any owner exists, so a child or a
# failed mint can never leave an untracked descriptor behind.
_OWNERS: dict = {}
_HELD_LOCKS: dict = {}
_HELD_PATHS: dict = {}
_ACTIVE_OWNERS: dict = {}
_LOCAL_ATTEMPTS: "OrderedDict[str, int]" = OrderedDict()
_FORK_HOOK_INSTALLED = False


class ExclusionError(Exception):
    """Fixed-message exclusion refusal; never carries identifiers or paths."""


class ExclusionDenied(ExclusionError):
    pass


class ExclusionNoRetry(ExclusionDenied):
    """A definite durable state that may not become a new publication attempt."""


class ExclusionBusy(ExclusionError):
    """Another writer holds one of the objects; takeover is never attempted."""


class ExclusionLost(ExclusionError):
    """Ownership could not be confirmed; the token is unusable."""


class ExclusionPoisoned(ExclusionError):
    """The owner observed uncertainty and can never authorize again."""


# ---------------------------------------------------------------------------
# Name derivation and input validation
# ---------------------------------------------------------------------------
def _framed(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _digest(domain: bytes, value: str) -> str:
    digest = hashlib.sha256()
    digest.update(_framed(domain))
    digest.update(_framed(value.encode("utf-8")))
    return digest.hexdigest()


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)


def _require_operation_id(value) -> str:
    if not isinstance(value, str) or _OPERATION_ID.fullmatch(value) is None:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return value


def _require_target_identity(value) -> str:
    """A server-derived ``<scope>:<reference>`` value; never used as a path."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_TARGET_IDENTITY_LENGTH
        or value.strip() != value
        or _has_control_characters(value)
    ):
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    prefix, separator, remainder = value.partition(":")
    if not separator or prefix not in TARGET_PREFIXES:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if not remainder or remainder.strip() != remainder:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return value


def _site_binding() -> tuple[str, str]:
    """Server-derived ``(site, real sites volume path)``; never client input."""
    site, sites_path = frappe_context.current_site_context()
    if (
        not isinstance(site, str)
        or not site
        or site.strip() != site
        or "/" in site
        or "\\" in site
        or _has_control_characters(site)
    ):
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if (
        not isinstance(sites_path, str)
        or not sites_path
        or not os.path.isabs(sites_path)
    ):
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    try:
        resolved = os.path.realpath(sites_path)
    except OSError:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None
    if not os.path.isdir(resolved):
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return site, resolved


def _site_directory_name(site: str) -> str:
    return _digest(SITE_DOMAIN, site)


def _lock_paths(
    site_directory, operation_id: str, target_identity: str
) -> tuple[str, str]:
    """Fixed object names for one attempt; hashed, never raw identifiers."""
    return (
        os.path.join(str(site_directory), _digest(OPERATION_DOMAIN, operation_id)),
        os.path.join(str(site_directory), _digest(TARGET_DOMAIN, target_identity)),
    )


@dataclass(frozen=True)
class _StableTree:
    """Server-derived directory chain captured at acquisition time."""

    sites_path: str
    root_path: str
    root_device: int
    root_inode: int
    site_path: str
    site_device: int
    site_inode: int


def _directory_identity(path: str) -> tuple[int, int]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if info.st_uid != os.geteuid():
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    finally:
        os.close(descriptor)
    if os.path.realpath(path) != path:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return info.st_dev, info.st_ino


def _create_verified_directory(path: str) -> None:
    try:
        os.mkdir(path, LOCK_DIRECTORY_MODE)
    except FileExistsError:
        pass
    except OSError:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None
    _directory_identity(path)


def _site_lock_directory(sites_real: str, site: str) -> _StableTree:
    """Create (lazily) and verify the fixed lock directories for this site."""
    root = os.path.join(sites_real, LOCK_ROOT_NAME)
    _create_verified_directory(root)
    root_device, root_inode = _directory_identity(root)
    site_directory = os.path.join(root, _site_directory_name(site))
    _create_verified_directory(site_directory)
    site_device, site_inode = _directory_identity(site_directory)
    return _StableTree(
        sites_path=sites_real,
        root_path=root,
        root_device=root_device,
        root_inode=root_inode,
        site_path=site_directory,
        site_device=site_device,
        site_inode=site_inode,
    )


def _verify_directory_entry(path: str, device: int, inode: int) -> None:
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    if (
        not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (device, inode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or os.path.realpath(path) != path
    ):
        raise ExclusionLost(SAFE_EXCLUSION_LOST)


def _verify_stable_tree(tree: _StableTree) -> None:
    """Re-check the directory chain, not only the leaf descriptors."""
    try:
        sites = os.stat(tree.sites_path)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    if not stat.S_ISDIR(sites.st_mode) or os.path.realpath(tree.sites_path) != (
        tree.sites_path
    ):
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    if os.path.dirname(tree.root_path) != tree.sites_path:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    _verify_directory_entry(tree.root_path, tree.root_device, tree.root_inode)
    if os.path.dirname(tree.site_path) != tree.root_path:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    _verify_directory_entry(tree.site_path, tree.site_device, tree.site_inode)


# ---------------------------------------------------------------------------
# OS advisory lock objects
# ---------------------------------------------------------------------------
class _HeldLock:
    __slots__ = ("descriptor", "path", "device", "inode")

    def __init__(self, descriptor: int, path: str, device: int, inode: int):
        self.descriptor = descriptor
        self.path = path
        self.device = device
        self.inode = inode


def _acquire(path: str) -> _HeldLock:
    """Open, verify and non-blockingly lock one stable object; never unlink it."""
    if path in _HELD_PATHS:
        raise ExclusionBusy(SAFE_EXCLUSION_BUSY)
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        descriptor = os.open(path, flags, LOCK_FILE_MODE)
    except OSError:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None

    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077
        ):
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in _BUSY_ERRNOS:
                raise ExclusionBusy(SAFE_EXCLUSION_BUSY) from None
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None
    except BaseException:
        os.close(descriptor)
        raise

    lock = _HeldLock(descriptor, path, info.st_dev, info.st_ino)
    _HELD_LOCKS[id(lock)] = lock
    _HELD_PATHS[path] = lock
    return lock


def _release(lock: _HeldLock) -> None:
    """Drop one object only if this module still owns that exact descriptor.

    A lock removed from the registry (an inherited child, or a failed mint) is not
    touched at all, so child cleanup can neither unlock nor close a descriptor the
    parent still uses. When the descriptor's identity no longer matches, the number may
    already have been reused and is deliberately left open; only a matching descriptor
    is unlocked and closed. Objects are never unlinked.
    """
    if _HELD_LOCKS.get(id(lock)) is not lock:
        return
    _HELD_LOCKS.pop(id(lock), None)
    _HELD_PATHS.pop(lock.path, None)

    try:
        info = os.fstat(lock.descriptor)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    if (info.st_dev, info.st_ino) != (lock.device, lock.inode):
        # Possible descriptor reuse: never close a number this module no longer owns.
        raise ExclusionLost(SAFE_EXCLUSION_LOST)

    entry_mismatch = False
    try:
        entry = os.stat(lock.path, follow_symlinks=False)
        if (
            not stat.S_ISREG(entry.st_mode)
            or entry.st_nlink != 1
            or (entry.st_dev, entry.st_ino) != (lock.device, lock.inode)
        ):
            entry_mismatch = True
    except OSError:
        entry_mismatch = True

    try:
        fcntl.flock(lock.descriptor, fcntl.LOCK_UN)
    except OSError:
        entry_mismatch = True
    finally:
        try:
            os.close(lock.descriptor)
        except OSError:
            pass
    if entry_mismatch:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)


# ---------------------------------------------------------------------------
# Fork handling
# ---------------------------------------------------------------------------
def _after_fork_in_child() -> None:
    """Invalidate inherited authority in the child; never touch the parent's locks.

    Descriptors are closed without ``LOCK_UN`` and without any database, path or
    application-lock work. Clearing the registries makes inherited owners unusable and
    inherited scope cleanup a no-op.
    """
    for owner in list(_OWNERS.values()):
        owner._invalidate_inherited()
    for lock in list(_HELD_LOCKS.values()):
        try:
            os.close(lock.descriptor)
        except OSError:
            pass
    _OWNERS.clear()
    _ACTIVE_OWNERS.clear()
    _HELD_LOCKS.clear()
    _HELD_PATHS.clear()
    _LOCAL_ATTEMPTS.clear()


def _ensure_fork_hook() -> None:
    global _FORK_HOOK_INSTALLED
    if _FORK_HOOK_INSTALLED or not hasattr(os, "register_at_fork"):
        return
    os.register_at_fork(after_in_child=_after_fork_in_child)
    _FORK_HOOK_INSTALLED = True


# ---------------------------------------------------------------------------
# Persisted bindings
# ---------------------------------------------------------------------------
def _row_value(row, fieldname):
    getter = getattr(row, "get", None)
    if callable(getter):
        return getter(fieldname)
    return getattr(row, fieldname, None)


def _row_text(row, fieldname) -> str:
    value = _row_value(row, fieldname)
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return value


def _optional_row_text(row, fieldname):
    value = _row_value(row, fieldname)
    if value in (None, ""):
        return None
    if not isinstance(value, str) or value.strip() != value:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return value


def _read_operation(operation_id: str, *, for_update: bool):
    try:
        return frappe_context.get_save_operation(operation_id, for_update=for_update)
    except ExclusionError:
        raise
    except Exception:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None


BINDING_FIELDS = ("site", "actor", "kind", "draft", "original", "synthetic_patient")

_REQUEST_KINDS = ("annotation_append", "synthetic_original_create")


def _initial_bindings(row) -> dict:
    """Immutable server-derived bindings of one persisted operation row."""
    bindings = {
        "operation_id": _row_text(row, "name"),
        "site": _row_text(row, "site"),
        "actor": _row_text(row, "actor"),
        "kind": _row_text(row, "kind"),
        "draft": _optional_row_text(row, "draft"),
        "original": _optional_row_text(row, "original"),
        "synthetic_patient": _optional_row_text(row, "synthetic_patient"),
        "state": _row_text(row, "state"),
    }
    if bindings["kind"] not in _REQUEST_KINDS:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if bindings["state"] not in DURABLE_STATES:
        # An unrecognised state is malformed, never a definite refusal.
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return bindings


def _target_from_row(row) -> str:
    """Server-derived target identity of one persisted operation row."""
    kind = _row_value(row, "kind")
    if kind == "annotation_append":
        return "draft:" + _row_text(row, "draft")
    if kind == "synthetic_original_create":
        return "patient-creation:" + _row_text(row, "synthetic_patient")
    raise ExclusionDenied(SAFE_EXCLUSION_DENIED)


def target_identity_for_operation(operation_id: str) -> str:
    """Derive the target identity a coordinator must present for this operation.

    This is an unlocked read used only to build the exclusion request; the exact same
    derivation is repeated under the operation lock and a mismatch is denied.
    """
    operation_id = _require_operation_id(operation_id)
    row = _read_operation(operation_id, for_update=False)
    if row is None:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    return _target_from_row(row)


# ---------------------------------------------------------------------------
# Owner token
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class LockedState:
    """Snapshot re-read while both locks are held.

    ``state`` comes from the locked row. The identity fields are the immutable initial
    bindings captured at entry and re-verified under the locks. ``attempt_generation``
    is this process's local attempt counter: ordering aid only, never durable evidence.
    """

    operation_id: str
    site: str
    actor: str
    kind: str
    target_identity: str
    draft: str | None
    original: str | None
    synthetic_patient: str | None
    state: str
    attempt_generation: int

    @property
    def fresh_attempt_permitted(self) -> bool:
        """Only an unstarted operation may begin a publication attempt."""
        return self.state == SUBMITTED_STATE


class LockedOwner:
    """Server-owned publication token; valid only while this scope holds the locks.

    Instances exist only through this module's private factory. Construction, copying,
    deep-copying and pickling all refuse. Every authorization re-verifies the exact
    registry identity, PID, lifecycle, both held objects, the descriptor and path
    identities and the stable directory tree, then probes both objects for conflicting
    holders.
    """

    __slots__ = (
        "_operation_id",
        "_site",
        "_actor",
        "_kind",
        "_target_identity",
        "_draft",
        "_original",
        "_synthetic_patient",
        "_attempt_generation",
        "_pid",
        "_tree",
        "_operation_lock",
        "_target_lock",
        "_poisoned",
        "_refused",
        "_closed",
        "_releasing",
        "_prepared_continuation",
        "_preparation_commit",
    )

    def __init__(self, *args, **kwargs):
        raise TypeError("LockedOwner is created only by hold_exclusion")

    def __copy__(self):
        raise TypeError("LockedOwner cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("LockedOwner cannot be copied")

    def __reduce__(self):
        raise TypeError("LockedOwner cannot be pickled")

    def __reduce_ex__(self, protocol):
        raise TypeError("LockedOwner cannot be pickled")

    # -- identity ---------------------------------------------------------
    @property
    def operation_id(self) -> str:
        return self._operation_id

    @property
    def site(self) -> str:
        return self._site

    @property
    def actor(self) -> str:
        return self._actor

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def target_identity(self) -> str:
        return self._target_identity

    @property
    def draft(self) -> str | None:
        return self._draft

    @property
    def original(self) -> str | None:
        return self._original

    @property
    def synthetic_patient(self) -> str | None:
        return self._synthetic_patient

    @property
    def attempt_generation(self) -> int:
        return self._attempt_generation

    @property
    def poisoned(self) -> bool:
        return self._poisoned

    # -- lifecycle --------------------------------------------------------
    def _invalidate(self) -> None:
        self._releasing = True
        _OWNERS.pop(id(self), None)
        self._closed = True

    def _invalidate_inherited(self) -> None:
        self._poisoned = True
        self._closed = True
        self._releasing = True

    # -- authorization ----------------------------------------------------
    def poison(self, reason: str = "owner_uncertainty") -> None:
        """Latch irreversible uncertainty; the reason is never disclosed."""
        self._poisoned = True

    # -- transaction-owner signalling -------------------------------------
    # A live token is also the narrow transaction-owner object a publication caller
    # registers with ``owned_publication_transaction``: an uncertain or spent token
    # reports the transaction as abortive, and a protected failure latches it. Neither
    # method can ever authorize.
    def publication_aborted(self) -> bool:
        return bool(
            self._poisoned or self._refused or self._closed or self._releasing
        )

    def abort_publication(self, reason: str) -> None:
        self.poison(reason)

    def require_bindings(self, *, actor, site, target_identity) -> "LockedOwner":
        """The caller must agree with the server-bound identity of this attempt."""
        _authorize(self)
        if (actor, site, target_identity) != (
            self._actor,
            self._site,
            self._target_identity,
        ):
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        return self

    def require_attempt_generation(self, generation) -> "LockedOwner":
        """Compare against this process's local attempt counter, not durable state."""
        _authorize(self)
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation != self._attempt_generation
        ):
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        return self

    def advance_attempt_generation(self) -> int:
        """Allocate the next local attempt generation for this process."""
        _authorize(self)
        self._attempt_generation += 1
        _record_local_attempt(self._operation_id, self._attempt_generation)
        return self._attempt_generation

    def require_continued(self) -> None:
        """Refuse unless this exact token already continued over its preparation.

        Only :meth:`continue_after_own_preparation` sets that state, and the
        coordinator calls it after its own preparation commit. Publication helpers use
        this check instead of continuing themselves, so a staged-but-uncommitted
        ``PREPARED`` row can never be turned into a durable outcome.
        """
        _authorize(self)
        if self._prepared_continuation is not True:
            raise ExclusionNoRetry(SAFE_EXCLUSION_NO_RETRY)

    def arm_preparation_commit(self, prepared) -> None:
        """Observe this attempt's preparation commit through the native boundary.

        Called by :func:`journal.prepare` after it verified and issued the exact
        ``PREPARED`` evidence. The only thing that records commit success is the
        one-shot ``after_commit`` callback of the very transaction that staged the row:
        a staged row, a rolled-back transaction and a caller-supplied flag can never
        produce the marker. The native callback boundary itself is a captured
        assumption (``frappe.db.after_commit`` is a ``CallbackManager`` whose ``run()``
        executes only after the SQL ``COMMIT`` succeeded); if this execution exposes no
        such boundary the continuation is refused instead of assumed.
        """
        _authorize(self)
        from clinic_photos.publication import journal

        if not journal._is_issued_preparation(prepared):
            # Not the exact value this process issued from a verified PREPARED row.
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if (
            self._preparation_commit is not None
            or self._prepared_continuation
            or prepared.id != self._operation_id
            or prepared.generation < 1
            or prepared.state != PREPARED_STATE
        ):
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

        import frappe

        manager = getattr(getattr(frappe, "db", None), "after_commit", None)
        register = getattr(manager, "add", None)
        if not callable(register):
            # No observable commit boundary: the continuation cannot be proved.
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

        owner = self
        marker = (id(prepared), prepared.generation, prepared.key)

        def _preparation_committed() -> None:
            # Runs only after this transaction's SQL COMMIT returned. It records the
            # marker on the exact token; it can never authorize anything by itself.
            if _OWNERS.get(id(owner)) is owner:
                owner._preparation_commit = marker

        try:
            register(_preparation_committed)
        except BaseException:
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None

    def continue_after_own_preparation(self, prepared) -> "LockedState":
        """Continue *this* live attempt over the preparation it just committed.

        This is the only continuation in the protocol, and it is deliberately narrow:
        the exact same registered token that holds both locks must still own them, the
        presented value must be the exact handle this process issued for this
        operation's verified ``PREPARED`` row (a field-equal dataclass a caller
        constructs is not), and the native transaction that staged that row must have
        reported its commit through the owner's one-shot marker. The locked row is then
        re-read ``for_update`` and every binding is re-compared. A fresh token never
        inherits this state: entry and the ordinary locked re-read still refuse
        ``PREPARED``, so a duplicate launch or a later attempt cannot resume it.

        The accepted flag and marker are execution-local ordering evidence, never
        durable authority, and the marker is spent here so a second continuation
        refuses. A failure after this point leaves the committed ``PREPARED`` floor and
        the owner poison in place.
        """
        _authorize(self)
        from clinic_photos.publication import journal

        if type(prepared) is not PreparedOperation:
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if not journal._is_issued_preparation(prepared):
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if self._prepared_continuation:
            # One continuation per token, ever.
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

        marker = self._preparation_commit
        if not isinstance(marker, tuple) or marker != (
            id(prepared),
            prepared.generation,
            prepared.key,
        ):
            # The preparation transaction never reported a commit for this evidence.
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

        expected = (
            prepared.id,
            prepared.actor,
            prepared.site,
            prepared.kind,
            prepared.target_identity,
            prepared.draft,
            prepared.synthetic_patient,
        )
        actual = (
            self._operation_id,
            self._actor,
            self._site,
            self._kind,
            self._target_identity,
            self._draft,
            self._synthetic_patient,
        )
        if expected != actual:
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        if prepared.generation < 1 or prepared.state != PREPARED_STATE:
            self._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

        self._preparation_commit = None
        self._prepared_continuation = True
        try:
            state = _bind_locked_state(self)
            row = _read_operation(self._operation_id, for_update=True)
            if row is None:
                self._poisoned = True
                raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
            generation = _row_value(row, "generation")
            if (
                isinstance(generation, bool)
                or not isinstance(generation, int)
                or generation != prepared.generation
            ):
                self._poisoned = True
                raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
            if (_row_value(row, "reservation_key") or "") != prepared.key:
                self._poisoned = True
                raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
        except ExclusionError:
            self._prepared_continuation = False
            raise
        if state.state != PREPARED_STATE:
            self._prepared_continuation = False
            self._refused = True
            raise ExclusionNoRetry(SAFE_EXCLUSION_NO_RETRY)
        return state


def _record_local_attempt(operation_id: str, generation: int) -> None:
    _LOCAL_ATTEMPTS.pop(operation_id, None)
    _LOCAL_ATTEMPTS[operation_id] = generation
    while len(_LOCAL_ATTEMPTS) > LOCAL_ATTEMPT_LIMIT:
        _LOCAL_ATTEMPTS.popitem(last=False)


def _next_local_attempt(operation_id: str) -> int:
    generation = _LOCAL_ATTEMPTS.get(operation_id, 0) + 1
    _record_local_attempt(operation_id, generation)
    return generation


def _mint_owner(
    *,
    operation_id: str,
    bindings: dict,
    target_identity: str,
    tree: _StableTree,
    operation_lock: _HeldLock,
    target_lock: _HeldLock,
) -> LockedOwner:
    """Private process-bound factory; the only way a usable token exists."""
    owner = object.__new__(LockedOwner)
    owner._operation_id = operation_id
    owner._site = bindings["site"]
    owner._actor = bindings["actor"]
    owner._kind = bindings["kind"]
    owner._target_identity = target_identity
    owner._draft = bindings["draft"]
    owner._original = bindings["original"]
    owner._synthetic_patient = bindings["synthetic_patient"]
    owner._attempt_generation = _next_local_attempt(operation_id)
    owner._pid = os.getpid()
    owner._tree = tree
    owner._operation_lock = operation_lock
    owner._target_lock = target_lock
    owner._poisoned = False
    owner._refused = False
    owner._closed = False
    owner._releasing = False
    owner._prepared_continuation = False
    owner._preparation_commit = None
    _OWNERS[id(owner)] = owner
    return owner


def _poison(owner) -> None:
    try:
        owner._poisoned = True
    except BaseException:
        # An object that cannot even accept the mark is still never authorized again.
        pass


def _authorize(owner) -> None:
    """Refuse unless this exact token still provably owns both locks right now."""
    if type(owner) is not LockedOwner:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    try:
        closed = owner._closed
        releasing = owner._releasing
        poisoned = owner._poisoned
        refused = owner._refused
        registered = _OWNERS.get(id(owner)) is owner
        pid = owner._pid
    except AttributeError:
        # A reconstructed shell that never came through the factory owns nothing.
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED) from None
    if closed or releasing:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    if poisoned:
        raise ExclusionPoisoned(SAFE_EXCLUSION_POISONED)
    if refused:
        # A definite durable state permanently spent this token's authority.
        raise ExclusionNoRetry(SAFE_EXCLUSION_NO_RETRY)
    if not registered or pid != os.getpid():
        _poison(owner)
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    try:
        _verify_stable_tree(owner._tree)
        for lock in (owner._operation_lock, owner._target_lock):
            _verify_lock(lock)
    except ExclusionError:
        _poison(owner)
        raise
    except BaseException:
        _poison(owner)
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None


def _verify_lock(lock: _HeldLock) -> None:
    if _HELD_LOCKS.get(id(lock)) is not lock or _HELD_PATHS.get(lock.path) is not lock:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    try:
        info = os.fstat(lock.descriptor)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    if not _matching_object(info, lock):
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    try:
        entry = os.stat(lock.path, follow_symlinks=False)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    if not _matching_object(entry, lock) or os.path.realpath(lock.path) != lock.path:
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    _probe_object_available(lock)


def _matching_object(info, lock: _HeldLock) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and (info.st_dev, info.st_ino) == (lock.device, lock.inode)
        and info.st_nlink == 1
        and info.st_uid == os.geteuid()
        and not stat.S_IMODE(info.st_mode) & 0o077
    )


def _probe_object_available(lock: _HeldLock) -> None:
    """Additional loss detector: a fresh lockable inode means this token lost it.

    The probe is opened without create/truncate and without following links, its inode
    must match the held object, and its lock is never adopted: success closes the probe
    and refuses. Busy only proves that *some* holder exists.
    """
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock.path, flags)
    except OSError:
        raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or (info.st_dev, info.st_ino) != (
            lock.device,
            lock.inode,
        ):
            raise ExclusionLost(SAFE_EXCLUSION_LOST)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            if error.errno in _BUSY_ERRNOS:
                return
            raise ExclusionLost(SAFE_EXCLUSION_LOST) from None
        raise ExclusionLost(SAFE_EXCLUSION_LOST)
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Locked re-read
# ---------------------------------------------------------------------------
def _bind_locked_state(owner: LockedOwner) -> LockedState:
    """Re-read the durable row under both locks and verify every binding."""
    try:
        row = _read_operation(owner.operation_id, for_update=True)
    except ExclusionError:
        owner._poisoned = True
        raise
    if row is None:
        owner._poisoned = True
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)

    try:
        bindings = _initial_bindings(row)
        target_identity = _target_from_row(row)
    except ExclusionError:
        owner._poisoned = True
        raise

    for fieldname in BINDING_FIELDS:
        if bindings[fieldname] != getattr(owner, "_" + fieldname):
            owner._poisoned = True
            raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if target_identity != owner.target_identity:
        owner._poisoned = True
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if bindings["site"] != _site_binding()[0]:
        owner._poisoned = True
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if bindings["state"] != SUBMITTED_STATE:
        if not (
            bindings["state"] == PREPARED_STATE
            and owner._prepared_continuation
        ):
            # A definite advanced state is a refusal, not uncertainty: the token stays
            # unpoisoned but its authority is spent for the rest of its life. Only the
            # exact live owner that itself committed this attempt's preparation may
            # continue over it, and only for one continuation.
            owner._refused = True
            raise ExclusionNoRetry(SAFE_EXCLUSION_NO_RETRY)

    return LockedState(
        operation_id=owner.operation_id,
        site=owner.site,
        actor=owner.actor,
        kind=owner.kind,
        target_identity=owner.target_identity,
        draft=owner.draft,
        original=owner.original,
        synthetic_patient=owner.synthetic_patient,
        state=bindings["state"],
        attempt_generation=owner.attempt_generation,
    )


def reread_locked_operation(owner: LockedOwner) -> LockedState:
    """Fresh locked state a coordinator may consume while it still owns the locks.

    The durable row is re-read ``for_update`` and every server-derived binding is
    compared with the token's immutable bindings; a different actor, target, draft,
    original, synthetic patient, kind, site or a vanished/malformed row poisons the
    token permanently. Only ``SUBMITTED`` is accepted: there is no continuation opt-out,
    because durable owner/generation evidence does not exist in this task.
    """
    _authorize(owner)
    return _bind_locked_state(owner)


def require_locked_owner(operation_id: str) -> LockedOwner:
    """The active server-owned token for exactly this operation, or a refusal.

    This is the only way a preparation helper can reach the owner that holds the
    locks: the token is found by exact operation identity in this module's active
    registry and re-authorized (registry identity, PID, lifecycle, both descriptors,
    paths, stable tree and lock availability) before it is returned. ``SUBMITTED``
    evidence is *not* re-read here; the caller must call ``reread_locked_operation``
    inside the same scope before any durable write.
    """
    operation_id = _require_operation_id(operation_id)
    for owner in list(_ACTIVE_OWNERS.values()):
        if owner.operation_id != operation_id:
            continue
        _authorize(owner)
        return owner
    raise ExclusionDenied(SAFE_EXCLUSION_DENIED)


# ---------------------------------------------------------------------------
# Public boundary
# ---------------------------------------------------------------------------
@contextmanager
def hold_exclusion(operation_id: str, target_identity: str):
    """Hold operation then target exclusion for exactly this ``with`` scope.

    The site, actor, kind, draft, original, synthetic patient and target identity are
    derived from the native context and the persisted row, never from the caller.
    ``target_identity`` is only compared with that derivation. Only a ``SUBMITTED``
    operation may enter: ``PREPARED`` and every terminal state refuse before ``yield``,
    so a duplicate child cannot obtain authority over an existing attempt. Busy objects
    refuse at once: a duplicate launch or a timed-out parent cannot acquire authority
    over a surviving writer. Authority is invalidated before release, both objects are
    released in this scope's ``finally`` (never a callback), and a release that cannot
    confirm object identity poisons the token and raises.
    """
    operation_id = _require_operation_id(operation_id)
    requested_target = _require_target_identity(target_identity)
    site, sites_real = _site_binding()

    peek = _read_operation(operation_id, for_update=False)
    if peek is None:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    bindings = _initial_bindings(peek)
    if bindings["operation_id"] != operation_id or bindings["site"] != site:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if _target_from_row(peek) != requested_target:
        raise ExclusionDenied(SAFE_EXCLUSION_DENIED)
    if bindings["state"] != SUBMITTED_STATE:
        raise ExclusionNoRetry(SAFE_EXCLUSION_NO_RETRY)

    _ensure_fork_hook()
    tree = _site_lock_directory(sites_real, site)
    operation_path, target_path = _lock_paths(
        tree.site_path, operation_id, requested_target
    )

    operation_lock = _acquire(operation_path)
    target_lock = None
    owner = None
    try:
        target_lock = _acquire(target_path)
        owner = _mint_owner(
            operation_id=operation_id,
            bindings=bindings,
            target_identity=requested_target,
            tree=tree,
            operation_lock=operation_lock,
            target_lock=target_lock,
        )
        _bind_locked_state(owner)
        _ACTIVE_OWNERS[id(owner)] = owner
        yield owner
    except ExclusionNoRetry:
        # A definite durable state is a refusal, not uncertainty: the token is simply
        # never yielded and no poison is recorded against a state decision.
        raise
    except BaseException:
        if owner is not None:
            owner._poisoned = True
        raise
    finally:
        # Invalidate authority *before* releasing anything: a reference held elsewhere
        # can never authorize between release and scope exit.
        if owner is not None:
            _ACTIVE_OWNERS.pop(id(owner), None)
            owner._invalidate()
        lost = None
        for lock in (target_lock, operation_lock):
            if lock is None:
                continue
            try:
                _release(lock)
            except ExclusionLost as error:
                lost = lost or error
        if owner is not None and lost is not None:
            owner._poisoned = True
        if lost is not None and sys.exc_info()[0] is None:
            raise lost
