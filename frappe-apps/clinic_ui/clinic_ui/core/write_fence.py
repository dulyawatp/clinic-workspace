"""Fail-closed write fence for future synthetic Patient and appointment writes.

Standalone core only: nothing here is wired to an endpoint, no capability is
enabled and no write runs against any site. The module is the durable,
interprocess fence a later server-side patient or appointment write path must
acquire before its native transaction and release only after the terminal
outcome is persisted.

Operation kinds
---------------
``create`` and ``update`` remain the patient kinds and keep their existing
receipt enumeration. ``book``, ``reschedule`` and ``checkin`` are additive
appointment kinds on the same store: same schema, same site-wide barrier and
same single sequence, but a separate immutable key per kind and a separate
bounded receipt enumeration. A patient receipt page never contains an
appointment receipt and vice versa; the receipts themselves are interpreted
only by their own codec.

Caller contract
---------------
``Ledger.fence`` yields one :class:`Claim`. The caller must run the entire
native write transaction *inside* the ``with`` block and call
``claim.commit(receipt)`` only after that native transaction has committed.
The interprocess lock stays held until the block exits, including after
``claim.commit``; post-commit work inside the block is therefore serialized.
This core does not authenticate an actor, bind a site, resolve a session,
check a role or capability, or expose any HTTP surface. The caller must derive
``actor`` and ``kind`` server-side and keep the write capability disabled
until the site-side and recovery work in the assessment is implemented and
reviewed. Nothing here is a production guarantee.

Storage
-------
One private directory (mode ``0700``) holds two files, both mode ``0600``::

    write-fence.sqlite3   append-only SQLite ledger, ``synchronous=FULL``
    write-fence.lock      advisory ``flock`` target

``initialize`` is explicit and separate. Opening a store never creates,
repairs, migrates, truncates or deletes a file: a missing, incomplete,
non-private, replaced or inconsistent store fails closed
(``StoreNotInitialized`` / ``StorePermissionError`` / ``StoreReplaced`` /
``StoreCorrupt``). Initialization requires the directory ``fsync`` to succeed;
failure returns ``StoreUnavailable`` and leaves the files for inspection.

Ledger model
------------
``meta`` carries one persistent deployment UUID, a recovery epoch and a
monotonically increasing sequence. ``events`` is append-only (triggers reject
``UPDATE``/``DELETE``) and each row is a state transition for the immutable
key ``(deployment_id, actor, kind, operation_key)``: exactly one ``DISPATCHED``
claim followed by at most one terminal ``COMMITTED`` (with a native receipt)
or ``UNKNOWN`` (with a reason). Sequence values must be contiguous from 1 and
match ``meta.sequence``; a terminal event must carry the same payload digest
and a higher sequence than its dispatch. On every verification the two tables,
their columns/types/primary key/unique index and all three triggers must match
the app's own DDL exactly after normalizing only whitespace, case and spacing
around punctuation; a same-name no-op trigger or an altered CHECK constraint
fails closed. Additional tables, explicit indexes, views or triggers are
refused. There is no per-key update, no delete and no second dispatch.

Identity pinning
----------------
``Ledger.open`` records the device/inode identity of the directory, ledger and
lock file. Every use re-opens the directory with ``O_NOFOLLOW``/``O_DIRECTORY``,
compares its identity, then stats both files through that descriptor; the lock
is opened with ``O_NOFOLLOW`` and its descriptor identity is compared before
``flock`` and the store is validated again after the lock is acquired. A
changed identity, a symlink or a missing file raises ``StoreReplaced`` instead
of using it.

Adversarial limits (same UID)
-----------------------------
The store protects against crashes, duplicate dispatch and accidental
replacement. It cannot defend against a hostile process running as the same
user (or root): such a process can replace files inside the tiny window between
a check and SQLite's own path-based open, write the database directly, patch
memory, or replace the anchor. Inode pinning narrows that window and rejects
the deterministic cases; it is not a security boundary against a compromised
host. Keep the directory private and the host trusted.

Trusted-host assumption
-----------------------
Schema verification and inode pinning assume the host, the store directory, its
ancestors, both store files and the anchor are not replaced or modified while a
``Ledger`` is in use. A fresh ``Ledger.open`` during active use is safe only
under that assumption: if the lock inode is swapped between opens, two
processes can hold advisory locks on different inodes and split the fence.
Run one long-lived ``Ledger`` per store, keep the files private, and drain
writers before maintenance, backup or restore. This module makes no universal
race-immunity claim.

Restore threat model (coordination only; protocol not implemented)
------------------------------------------------------------------
The ledger is private and meant to live outside the site database and site
Docker volumes, with its own backup and retention. On its own it can detect
local damage (bad schema, sequence gap, digest mismatch, missing dispatch,
orphan terminal, non-private mode, replaced inode) and, when given an
``Anchor`` read from an *independent* durable record, a store that is older
than that record. It cannot detect a coordinated rollback where the site
database, this ledger and the anchor are all restored to a consistent older
point, and the restore/quarantine protocol (epoch advance, reconciliation,
``COMMITTED_BUT_MISSING``, operator unlock) is deliberately not implemented in
this slice. Until that protocol exists, a restore must drain writers, take
this store's ``write-fence.lock``, and then reconcile against the external
anchor, and no write may run against a restored store. The anchor is never
updated by this module: it freezes unless an independent operator record is
advanced, and a stale anchor allows forward writes while weakening rollback
detection. Absence of an event here is never evidence that a native operation
did not commit, and there is no automatic recovery of ``DISPATCHED``/
``UNKNOWN`` keys.

Durability limits
-----------------
``synchronous=FULL`` plus a successful SQLite commit is the durable terminal
boundary on a filesystem that honours ``fsync``. Network filesystems,
copy-on-write snapshots, containers or virtual disks that ignore flushes, and
power loss can still lose the store or a committed row; the independent anchor
and its backup are what bound that loss. Directory fsync is required during
initialization and any failure is surfaced instead of ignored.

The verifier scans the small first-slice ledger on every fence; revisit before
any large-scale use.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import sqlite3
import stat
import uuid
from collections.abc import Mapping
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

try:  # POSIX only; the store is fail-closed where flock is unavailable.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX
    fcntl = None

STORE_FILENAME = "write-fence.sqlite3"
LOCK_FILENAME = "write-fence.lock"
# Independent durable latch file. It is checked before any new claim and does
# not depend on the SQLite connection or the interprocess lock, so it still
# blocks new work if the database append cannot be persisted.
LATCH_FILENAME = "write-fence.manual-investigation.latch"
SCHEMA_VERSION = 1

DISPATCHED = "DISPATCHED"
COMMITTED = "COMMITTED"
UNKNOWN = "UNKNOWN"
STATES = (DISPATCHED, COMMITTED, UNKNOWN)
PATIENT_OPERATION_KINDS = frozenset({"create", "update"})
# Appointment kinds are additive to the same ledger. They share the site-wide
# barrier and the single sequence, but each kind is a separate immutable key,
# so a patient key and an appointment key with the same text never collide. The
# two wave kinds carry their registry names (``appointment_cancel`` and
# ``appointment_no_show``) because they are also budget keys.
APPOINTMENT_OPERATION_KINDS = frozenset(
    {
        "book",
        "reschedule",
        "checkin",
        "appointment_cancel",
        "appointment_no_show",
    }
)
OPERATION_KINDS = PATIENT_OPERATION_KINDS | APPOINTMENT_OPERATION_KINDS

# Reserved identity for the durable site-wide manual-investigation latch. It is
# not a client operation key: no client value can equal it because the endpoint
# only accepts keys matching the strict operation-key pattern and this key is
# additionally prefixed with ``clinic-ui-``. Once its terminal ``UNKNOWN`` is
# appended, ``_has_unresolved`` blocks every actor and new key until an operator
# inspects the deployment. Nothing in this module clears it.
LATCH_ACTOR = "clinic-ui-site-latch"
LATCH_KIND = "create"
LATCH_OPERATION_KEY = "clinic-ui-manual-investigation-latch"
BARRIER_PREFIX = "clinic-ui-write-barrier-"

MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_ACTOR_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024
MAX_REASON_LENGTH = 512
# Read-only discovery bound. This is a hard cap on one enumeration query; it is
# not a client input and can never be widened by a caller.
MAX_RECEIPT_ENUMERATION = 1000
_DIGEST_LENGTH = 64
_DEPLOYMENT_LENGTH = 32
_HEX = frozenset("0123456789abcdef")

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

_SCHEMA = (
    """
    CREATE TABLE meta (
        deployment_id TEXT NOT NULL PRIMARY KEY,
        epoch INTEGER NOT NULL,
        sequence INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        CHECK (length(deployment_id) = 32),
        CHECK (epoch >= 1),
        CHECK (sequence >= 0),
        CHECK (schema_version = 1)
    )
    """,
    """
    CREATE TABLE events (
        sequence INTEGER NOT NULL PRIMARY KEY,
        deployment_id TEXT NOT NULL,
        actor TEXT NOT NULL,
        kind TEXT NOT NULL,
        operation_key TEXT NOT NULL,
        payload_digest TEXT NOT NULL,
        state TEXT NOT NULL,
        receipt TEXT,
        reason TEXT,
        recorded_at TEXT NOT NULL,
        UNIQUE (deployment_id, actor, kind, operation_key, state),
        CHECK (sequence >= 1),
        CHECK (state IN ('DISPATCHED', 'COMMITTED', 'UNKNOWN')),
        CHECK (length(payload_digest) = 64),
        CHECK (state != 'COMMITTED' OR (receipt IS NOT NULL AND length(receipt) > 0)),
        CHECK (state != 'UNKNOWN' OR (reason IS NOT NULL AND length(reason) > 0)),
        CHECK (state = 'UNKNOWN' OR reason IS NULL)
    )
    """,
    """
    CREATE TRIGGER events_append_only_update BEFORE UPDATE ON events
    BEGIN
        SELECT RAISE(ABORT, 'write fence events are append-only');
    END
    """,
    """
    CREATE TRIGGER events_append_only_delete BEFORE DELETE ON events
    BEGIN
        SELECT RAISE(ABORT, 'write fence events are append-only');
    END
    """,
    """
    CREATE TRIGGER events_terminal_after_dispatch BEFORE INSERT ON events
    WHEN NEW.state != 'DISPATCHED'
    BEGIN
        SELECT RAISE(ABORT, 'terminal event without a dispatched claim')
        WHERE NOT EXISTS (
            SELECT 1 FROM events
            WHERE deployment_id = NEW.deployment_id
              AND actor = NEW.actor
              AND kind = NEW.kind
              AND operation_key = NEW.operation_key
              AND state = 'DISPATCHED'
        );
    END
    """,
)

_TABLE_SQL = {"meta": _SCHEMA[0], "events": _SCHEMA[1]}
_TRIGGER_SQL = {
    "events_append_only_update": _SCHEMA[2],
    "events_append_only_delete": _SCHEMA[3],
    "events_terminal_after_dispatch": _SCHEMA[4],
}
_TABLE_COLUMNS = {
    "meta": (
        ("deployment_id", "TEXT", 1, 1),
        ("epoch", "INTEGER", 1, 0),
        ("sequence", "INTEGER", 1, 0),
        ("schema_version", "INTEGER", 1, 0),
    ),
    "events": (
        ("sequence", "INTEGER", 1, 1),
        ("deployment_id", "TEXT", 1, 0),
        ("actor", "TEXT", 1, 0),
        ("kind", "TEXT", 1, 0),
        ("operation_key", "TEXT", 1, 0),
        ("payload_digest", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("receipt", "TEXT", 0, 0),
        ("reason", "TEXT", 0, 0),
        ("recorded_at", "TEXT", 1, 0),
    ),
}
_UNIQUE_INDEX_COLUMNS = {
    "meta": ("deployment_id",),
    "events": ("deployment_id", "actor", "kind", "operation_key", "state"),
}
_UNIQUE_INDEX_ORIGIN = {"meta": "pk", "events": "u"}

__all__ = [
    "Anchor",
    "APPOINTMENT_OPERATION_KINDS",
    "Claim",
    "COMMITTED",
    "CommittedAppointmentReceipt",
    "CommittedCreateReceipt",
    "DISPATCHED",
    "EnumerationOverflow",
    "FenceError",
    "FenceReleased",
    "InvalidOperation",
    "Ledger",
    "LedgerInfo",
    "LATCH_ACTOR",
    "LATCH_KIND",
    "LATCH_OPERATION_KEY",
    "LockUnavailable",
    "MAX_RECEIPT_ENUMERATION",
    "OperationConflict",
    "OperationLocked",
    "OperationRecord",
    "PATIENT_OPERATION_KINDS",
    "RestoredStoreDetected",
    "STATES",
    "SiteLocked",
    "StoreCorrupt",
    "StoreNotInitialized",
    "StorePermissionError",
    "StoreReplaced",
    "StoreUnavailable",
    "TerminalAppendFailed",
    "UNKNOWN",
    "UnterminatedFence",
    "barrier_key",
    "initialize",
    "payload_digest",
]


class FenceError(Exception):
    """Base class for every write-fence failure."""


class EnumerationOverflow(FenceError):
    """A bounded read-only enumeration found more rows than its limit.

    Callers must not treat a truncated receipt page as a complete set: an alias
    or collision beyond the bound could otherwise be missed.
    """


class StoreUnavailable(FenceError):
    """The ledger cannot be used; the caller must fail closed."""


class StoreNotInitialized(StoreUnavailable):
    """No complete initialized store exists at the given directory."""


class StorePermissionError(StoreUnavailable):
    """The store directory or file is not private to the current user."""


class StoreCorrupt(StoreUnavailable):
    """The store violates its schema, sequence or event invariants."""


class StoreReplaced(StoreCorrupt):
    """A pinned directory, ledger or lock inode changed or became a symlink."""


class RestoredStoreDetected(StoreUnavailable):
    """The store is older than the independent anchor; writes are refused."""


class LockUnavailable(FenceError):
    """Another process holds the fence."""


class OperationConflict(FenceError):
    """The key is already bound to a different payload digest."""


class OperationLocked(FenceError):
    """The key is DISPATCHED or UNKNOWN and must not be replayed."""

    def __init__(self, state, sequence):
        super().__init__(f"operation is {state}; no replay without reconciliation")
        self.state = state
        self.sequence = sequence


class SiteLocked(FenceError):
    """Some key in this deployment is unresolved; no new key may be accepted.

    A single ``DISPATCHED`` or ``UNKNOWN`` operation key blocks every new
    operation key for every actor until an operator reconciles it. There is no
    automatic unlock, no timeout and no actor-scoped exception.
    """


class InvalidOperation(FenceError):
    """An input or anchor value is outside the accepted shape."""


class TerminalAppendFailed(FenceError):
    """The terminal record was not durably appended; the outcome is UNKNOWN."""


class FenceReleased(FenceError):
    """The claim was already completed or its fence was released."""


class UnterminatedFence(FenceError):
    """The fenced body exited without a terminal record."""


@dataclass(frozen=True)
class Anchor:
    """Independent, externally persisted expectation about the store.

    ``deployment_id`` and ``epoch`` must match exactly and the store sequence
    must be at least ``sequence``. Read this from a durable record outside the
    site backup; an in-store value is not an anchor. This module never advances
    the anchor, so it freezes unless an independent operator record updates it.
    """

    deployment_id: str
    epoch: int
    sequence: int


@dataclass(frozen=True)
class LedgerInfo:
    deployment_id: str
    epoch: int
    sequence: int


@dataclass(frozen=True)
class OperationRecord:
    """Durable view of one operation key; never a permission to write."""

    state: str
    sequence: int
    payload_digest: str
    receipt: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class CommittedCreateReceipt:
    """One read-only committed create receipt discovered in the fence.

    The actor is the original creator recorded by the fence. The receipt is the
    opaque terminal value; callers must decode and re-verify it before use.
    """

    actor: str
    operation_key: str
    receipt: str


@dataclass(frozen=True)
class CommittedAppointmentReceipt:
    """One read-only committed appointment receipt discovered in the fence.

    The kind is the appointment operation kind (``book``, ``reschedule`` or
    ``checkin``). The receipt is opaque to this module: only the separate
    appointment codec may interpret it, and the caller must re-verify it before
    use. Patient receipts are never returned here and appointment receipts are
    never returned by :meth:`Ledger.committed_create_receipts`.
    """

    kind: str
    actor: str
    operation_key: str
    receipt: str


@dataclass(frozen=True)
class _Pinned:
    directory: tuple
    database: tuple
    lock: tuple


def payload_digest(payload: Mapping) -> str:
    """Return a canonical SHA-256 digest of a whitelisted payload mapping."""

    if not isinstance(payload, Mapping):
        raise InvalidOperation("payload must be a mapping")
    try:
        canonical = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise InvalidOperation("payload is not canonically serializable") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def initialize(directory) -> LedgerInfo:
    """Create a new private store. Never repairs, reuses or deletes one."""

    target = _directory_path(directory)
    if target.exists() and not target.is_dir():
        raise StoreUnavailable("write fence path is not a directory")
    try:
        target.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise StoreUnavailable("could not create the write fence directory") from exc
    expected_directory = _directory_identity(target)
    directory_fd = _open_verified_directory(target, expected_directory)

    database_path = target / STORE_FILENAME
    lock_path = target / LOCK_FILENAME
    try:
        existing = _inspect_file(directory_fd, STORE_FILENAME)
        if existing is not None or _inspect_file(directory_fd, LOCK_FILENAME) is not None:
            raise StoreUnavailable("write fence store already exists")
        _create_private_file(directory_fd, STORE_FILENAME)
        _create_private_file(directory_fd, LOCK_FILENAME)
        _fsync_directory(directory_fd)
        database = _inspect_file(directory_fd, STORE_FILENAME)
        lock = _inspect_file(directory_fd, LOCK_FILENAME)
        if database is None or lock is None:
            raise StoreUnavailable("write fence files disappeared during creation")
        _require_private_details(database, "ledger file")
        _require_private_details(lock, "lock file")

        deployment_id = uuid.uuid4().hex
        with closing(_connect(database_path)) as connection:
            _require_pinned_file(directory_fd, STORE_FILENAME, _identity(database))
            with _transaction(connection):
                for statement in _SCHEMA:
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO meta (deployment_id, epoch, sequence,"
                    " schema_version) VALUES (?, 1, 0, ?)",
                    (deployment_id, SCHEMA_VERSION),
                )
            _require_pinned_file(directory_fd, STORE_FILENAME, _identity(database))
            _verify(connection)
        _fsync_directory(directory_fd)
    except sqlite3.DatabaseError as exc:
        raise StoreUnavailable("could not initialize the write fence store") from exc
    finally:
        os.close(directory_fd)

    return LedgerInfo(deployment_id=deployment_id, epoch=1, sequence=0)


class Ledger:
    """An opened, anchored store. Use :func:`initialize` once, then ``open``."""

    def __init__(self, directory, anchor, pinned):
        self._directory = directory
        self._anchor = anchor
        self._pinned = pinned

    @classmethod
    def open(cls, directory, *, anchor) -> "Ledger":
        target = _directory_path(directory)
        anchor = _require_anchor(anchor)
        if not target.is_dir():
            raise StoreNotInitialized("write fence store does not exist")
        _require_private_directory(target)

        expected_directory = _directory_identity(target)
        directory_fd = _open_verified_directory(target, expected_directory)
        try:
            database = _inspect_file(directory_fd, STORE_FILENAME)
            lock = _inspect_file(directory_fd, LOCK_FILENAME)
            if database is None or lock is None:
                raise StoreNotInitialized("write fence store is incomplete")
            if not stat.S_ISREG(database.st_mode) or not stat.S_ISREG(lock.st_mode):
                raise StoreReplaced("write fence store contains a non-regular file")
            _require_private_details(database, "ledger file")
            _require_private_details(lock, "lock file")
            pinned = _Pinned(
                directory=expected_directory,
                database=_identity(database),
                lock=_identity(lock),
            )
            try:
                with closing(_connect(target / STORE_FILENAME)) as connection:
                    _require_pinned_file(
                        directory_fd, STORE_FILENAME, pinned.database
                    )
                    info = _verify(connection, quick_check=True)
            except sqlite3.DatabaseError as exc:
                raise StoreCorrupt(
                    "write fence database failed integrity checks"
                ) from exc
        finally:
            os.close(directory_fd)

        _check_anchor(anchor, info)
        return cls(target, anchor, pinned)

    @property
    def directory(self):
        return self._directory

    def info(self) -> LedgerInfo:
        directory_fd = self._open_pinned_directory()
        try:
            with self._connection(directory_fd) as connection:
                return _verify(connection)
        finally:
            os.close(directory_fd)

    def lookup(self, *, actor, kind, operation_key) -> OperationRecord | None:
        """Read the latest durable state for one key; never unlocks or retries."""

        actor = _require_actor(actor)
        kind = _require_kind(kind)
        operation_key = _require_operation_key(operation_key)
        directory_fd = self._open_pinned_directory()
        try:
            with self._connection(directory_fd) as connection:
                row = _latest(connection, actor, kind, operation_key)
        finally:
            os.close(directory_fd)
        if row is None:
            return None
        return OperationRecord(
            state=row["state"],
            sequence=row["sequence"],
            payload_digest=row["payload_digest"],
            receipt=row["receipt"],
            reason=row["reason"],
        )

    def committed_create_receipts(self, *, limit, operation_key=None):
        """Enumerate terminal ``COMMITTED`` create receipts, bounded and read-only.

        Only terminal committed creates owned by a real actor are returned. The
        reserved latch actor (manual latch and every write barrier) is excluded,
        as is the reserved latch operation key and every non-terminal state. An
        optional operation key narrows the read to one key so a caller can verify
        one alias without scanning the whole store.

        ``limit`` is a hard server-side bound; a non-positive, non-integer or
        over-limit value is refused. The query asks for one row beyond the bound
        so an over-full page raises :class:`EnumerationOverflow` instead of
        silently truncating. The method appends nothing, acquires no write claim
        and never changes the ledger sequence. A missing, replaced or corrupt
        store raises ``StoreUnavailable``.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_RECEIPT_ENUMERATION
        ):
            raise InvalidOperation("enumeration limit is invalid")
        if operation_key is not None:
            operation_key = _require_operation_key(operation_key)

        sql = (
            "SELECT e.actor, e.operation_key, e.receipt FROM events AS e"
            " WHERE e.deployment_id = (SELECT deployment_id FROM meta)"
            " AND e.kind = 'create'"
            " AND e.state = 'COMMITTED'"
            " AND e.actor != ?"
            " AND e.operation_key != ?"
            " AND e.sequence = ("
            "   SELECT MAX(e2.sequence) FROM events AS e2"
            "   WHERE e2.deployment_id = e.deployment_id"
            "     AND e2.actor = e.actor"
            "     AND e2.kind = e.kind"
            "     AND e2.operation_key = e.operation_key"
            " )"
        )
        parameters = [LATCH_ACTOR, LATCH_OPERATION_KEY]
        if operation_key is not None:
            sql += " AND e.operation_key = ?"
            parameters.append(operation_key)
        sql += " ORDER BY e.sequence LIMIT ?"
        parameters.append(limit + 1)

        directory_fd = self._open_pinned_directory()
        try:
            with self._connection(directory_fd) as connection:
                try:
                    rows = connection.execute(sql, parameters).fetchall()
                except sqlite3.DatabaseError as exc:
                    raise StoreUnavailable(
                        "could not read committed create receipts"
                    ) from exc
        finally:
            os.close(directory_fd)
        if len(rows) > limit:
            raise EnumerationOverflow("committed create receipts exceed the bound")
        return [
            CommittedCreateReceipt(
                actor=row["actor"],
                operation_key=row["operation_key"],
                receipt=row["receipt"],
            )
            for row in rows
        ]

    def committed_appointment_receipts(self, *, limit, kind=None, operation_key=None):
        """Enumerate terminal ``COMMITTED`` appointment receipts, bounded.

        This is a separate, read-only namespace from
        :meth:`committed_create_receipts`: only the appointment kinds (``book``,
        ``reschedule``, ``checkin``) are selected, so a patient create receipt
        is never returned here and an appointment receipt is never returned
        there. The reserved latch actor (manual latch and every write barrier)
        and the reserved latch key are excluded, as is every non-terminal state
        and every patient kind. ``kind`` optionally narrows the read to one
        appointment kind and ``operation_key`` to one key, so a caller can
        verify a single alias without scanning the whole store.

        ``limit`` is a hard server-side bound; a non-positive, non-integer or
        over-limit value is refused. The query asks for one row beyond the
        bound so an over-full page raises :class:`EnumerationOverflow` instead
        of silently truncating. The method appends nothing, acquires no write
        claim and never changes the ledger sequence. A missing, replaced or
        corrupt store raises ``StoreUnavailable``.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_RECEIPT_ENUMERATION
        ):
            raise InvalidOperation("enumeration limit is invalid")
        if kind is not None:
            kind = _require_kind(kind)
            if kind not in APPOINTMENT_OPERATION_KINDS:
                raise InvalidOperation("kind is not an appointment kind")
        if operation_key is not None:
            operation_key = _require_operation_key(operation_key)
        kinds = tuple(sorted(APPOINTMENT_OPERATION_KINDS))
        sql = (
            "SELECT e.kind, e.actor, e.operation_key, e.receipt FROM events AS e"
            " WHERE e.deployment_id = (SELECT deployment_id FROM meta)"
            f" AND e.kind IN ({', '.join('?' for _ in kinds)})"
            " AND e.state = 'COMMITTED'"
            " AND e.actor != ?"
            " AND e.operation_key != ?"
            " AND e.sequence = ("
            "   SELECT MAX(e2.sequence) FROM events AS e2"
            "   WHERE e2.deployment_id = e.deployment_id"
            "     AND e2.actor = e.actor"
            "     AND e2.kind = e.kind"
            "     AND e2.operation_key = e.operation_key"
            " )"
        )
        parameters = [*kinds, LATCH_ACTOR, LATCH_OPERATION_KEY]
        if kind is not None:
            sql += " AND e.kind = ?"
            parameters.append(kind)
        if operation_key is not None:
            sql += " AND e.operation_key = ?"
            parameters.append(operation_key)
        sql += " ORDER BY e.sequence LIMIT ?"
        parameters.append(limit + 1)

        directory_fd = self._open_pinned_directory()
        try:
            with self._connection(directory_fd) as connection:
                try:
                    rows = connection.execute(sql, parameters).fetchall()
                except sqlite3.DatabaseError as exc:
                    raise StoreUnavailable(
                        "could not read committed appointment receipts"
                    ) from exc
        finally:
            os.close(directory_fd)
        if len(rows) > limit:
            raise EnumerationOverflow(
                "committed appointment receipts exceed the bound"
            )
        return [
            CommittedAppointmentReceipt(
                kind=row["kind"],
                actor=row["actor"],
                operation_key=row["operation_key"],
                receipt=row["receipt"],
            )
            for row in rows
        ]

    def ready(self):
        """Return whether this deployment may currently admit a new operation.

        This is an advisory, non-mutating readiness check for bootstrap. It
        takes the same interprocess lock as a claim, then checks the independent
        manual-investigation latch and the latest state of every key, including
        every write barrier. A latch, a dispatched key, an unknown terminal or a
        committed-but-unacknowledged barrier returns ``False``.

        It appends nothing and reserves nothing. A claim still re-checks both
        conditions under the lock, so a readiness ``True`` can never authorize a
        write by itself.
        """

        try:
            lock_fd, directory_fd = self._acquire_lock()
        except LockUnavailable:
            return False
        try:
            try:
                self._require_no_latch(directory_fd)
            except SiteLocked:
                return False
            with self._connection(directory_fd) as connection:
                return not _has_unresolved(connection)
        finally:
            _release_lock(lock_fd)
            os.close(directory_fd)

    def acknowledge_barrier(self, *, actor, kind, operation_key, ack_receipt):
        """Resolve the write barrier after an explicit client acknowledgement.

        The original operation must already be ``COMMITTED`` with a durable
        receipt, and the barrier must still be ``DISPATCHED``. This appends the
        single ``COMMITTED`` terminal for the barrier so the site-wide
        unresolved check admits a new operation. A repeat acknowledgement is
        idempotent only when the recorded ack receipt matches.
        """

        actor = _require_actor(actor)
        kind = _require_kind(kind)
        operation_key = _require_operation_key(operation_key)
        ack_receipt = _require_receipt(ack_receipt)
        barrier = barrier_key(actor, kind, operation_key)
        lock_fd, directory_fd = self._acquire_lock()
        try:
            with self._connection(directory_fd) as connection:
                operation = _latest(connection, actor, kind, operation_key)
                if (
                    operation is None
                    or operation["state"] != COMMITTED
                    or not operation["receipt"]
                ):
                    return "invalid"
                existing = _latest(
                    connection, LATCH_ACTOR, kind, barrier
                )
                if existing is None:
                    return "invalid"
                if existing["state"] == COMMITTED:
                    return (
                        "already"
                        if existing["receipt"] == ack_receipt
                        else "invalid"
                    )
                if existing["state"] != DISPATCHED:
                    return "invalid"
                _append(
                    connection,
                    self,
                    actor=LATCH_ACTOR,
                    kind=kind,
                    operation_key=barrier,
                    payload_digest=existing["payload_digest"],
                    state=COMMITTED,
                    receipt=ack_receipt,
                )
                return "acked"
        except sqlite3.DatabaseError as exc:
            raise StoreUnavailable(
                "could not record the success acknowledgement"
            ) from exc
        finally:
            _release_lock(lock_fd)
            os.close(directory_fd)

    def latch(self, reason):
        """Create the durable site-wide manual-investigation latch.

        Two independent durable records are attempted: a private latch file in
        the store directory (checked before every new claim and independent of
        SQLite/lock availability) and a reserved ``DISPATCHED``/``UNKNOWN``
        event pair under the same interprocess lock. Either record is enough to
        block every actor and new key. The call is idempotent.

        Returns ``True`` when the deployment is latched. Raises only when
        neither record can be persisted; the caller must then fail loudly.
        """

        reason = _require_reason(reason)
        file_created, file_existed, file_error = self._try_latch_file(reason)
        try:
            lock_fd, directory_fd = self._acquire_lock()
        except LockUnavailable:
            if file_created or file_existed:
                return file_created
            raise
        try:
            event_latched, event_appended, event_error = self._try_latch_events(
                directory_fd,
                operation_key=LATCH_OPERATION_KEY,
                seed=LATCH_OPERATION_KEY,
                reason=reason,
            )
        finally:
            _release_lock(lock_fd)
            os.close(directory_fd)
        if file_created or event_appended:
            return True
        if file_existed or event_latched:
            return False
        if file_error is not None:
            raise file_error
        raise StoreUnavailable(
            "could not record the manual investigation latch"
        ) from event_error

    def latch_operation_uncertain(self, *, actor, kind, operation_key, reason):
        """Fallback site latch derived from one operation identity.

        This is used when the primary reserved latch cannot be persisted. It
        writes a second reserved ``DISPATCHED``/``UNKNOWN`` event pair, keyed by
        a hash of the actor/kind/operation key, under the same lock. The
        original operation's ``COMMITTED`` receipt and history are untouched,
        but ``_has_unresolved`` now sees the fallback latch and blocks every new
        key for every actor until an operator inspects the deployment.
        """

        actor = _require_actor(actor)
        kind = _require_kind(kind)
        operation_key = _require_operation_key(operation_key)
        reason = _require_reason(reason)
        seed = "|".join((actor, kind, operation_key))
        latch_key = _fallback_latch_key(seed)
        file_created, file_existed, file_error = self._try_latch_file(reason)
        try:
            lock_fd, directory_fd = self._acquire_lock()
        except LockUnavailable:
            if file_created or file_existed:
                return file_created
            raise
        try:
            event_latched, event_appended, event_error = self._try_latch_events(
                directory_fd,
                operation_key=latch_key,
                seed=seed,
                reason=reason,
            )
        finally:
            _release_lock(lock_fd)
            os.close(directory_fd)
        if file_created or event_appended:
            return True
        if file_existed or event_latched:
            return False
        if file_error is not None:
            raise file_error
        raise StoreUnavailable(
            "could not record the operation uncertainty latch"
        ) from event_error

    @contextmanager
    def fence(self, *, actor, kind, operation_key, payload_digest):
        """Hold the interprocess fence across claim, native work and terminal.

        Yields a fresh :class:`Claim`. The body must run the native transaction
        and call ``claim.commit`` after it commits, or ``claim.unknown`` when
        the outcome cannot be established. An exception in the body leaves the
        key ``DISPATCHED`` for reconciliation; nothing is auto-marked. The lock
        is released only when the body exits, even after ``claim.commit``.
        """

        actor = _require_actor(actor)
        kind = _require_kind(kind)
        operation_key = _require_operation_key(operation_key)
        digest = _require_digest(payload_digest)
        lock_fd, directory_fd = self._acquire_lock()
        try:
            self._require_no_latch(directory_fd)
            with self._connection(directory_fd) as connection:
                claim = self._claim(connection, actor, kind, operation_key, digest)
                claim._attach(directory_fd)
                try:
                    yield claim
                except BaseException:
                    raise
                else:
                    if claim.terminal_sequence is None:
                        raise UnterminatedFence(
                            "fenced body exited without a terminal record"
                        )
                finally:
                    claim._release()
        finally:
            _release_lock(lock_fd)
            os.close(directory_fd)

    # -- internals -----------------------------------------------------

    def _try_latch_file(self, reason):
        """Write the independent latch file.

        Returns ``(created, existed, error)`` so an already-present latch is not
        mistaken for a persistence failure.
        """

        directory_fd = self._open_pinned_directory()
        try:
            _require_pinned_store(self._pinned, directory_fd)
            try:
                created, existed = self._write_latch_file(directory_fd, reason)
                return created, existed, None
            except StoreUnavailable as exc:
                return False, False, exc
        finally:
            os.close(directory_fd)

    def _write_latch_file(self, directory_fd, reason):
        try:
            descriptor = os.open(
                LATCH_FILENAME,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            return False, True
        except OSError as exc:
            raise StoreUnavailable(
                "could not create the manual investigation latch"
            ) from exc
        try:
            os.fchmod(descriptor, 0o600)
            payload = reason.encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("manual investigation latch made no progress")
                offset += written
            os.fsync(descriptor)
        except OSError as exc:
            raise StoreUnavailable(
                "could not write the manual investigation latch"
            ) from exc
        finally:
            os.close(descriptor)
        try:
            os.fsync(directory_fd)
        except OSError:
            pass
        return True, False

    def _try_latch_events(self, directory_fd, *, operation_key, seed, reason):
        """Append the reserved latch event pair.

        Returns ``(latched, appended, error)``: ``latched`` means a terminal
        latch already exists or was appended, ``appended`` means this call wrote
        it.
        """

        try:
            with self._connection(directory_fd) as connection:
                existing = _latest(
                    connection, LATCH_ACTOR, LATCH_KIND, operation_key
                )
                appended = existing is None
                if appended:
                    _append_latch_events(
                        connection,
                        self,
                        operation_key=operation_key,
                        seed=seed,
                        reason=reason,
                    )
            return True, appended, None
        except (sqlite3.DatabaseError, StoreUnavailable) as exc:
            return False, False, exc

    def _require_no_latch(self, directory_fd):
        try:
            os.stat(
                LATCH_FILENAME, dir_fd=directory_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            return
        except OSError as exc:
            raise StoreUnavailable(
                "could not check the manual investigation latch"
            ) from exc
        raise SiteLocked(
            "a manual investigation latch blocks every new operation"
        )

    def _open_pinned_directory(self):
        return _open_verified_directory(
            self._directory, self._pinned.directory
        )

    @contextmanager
    def _connection(self, directory_fd):
        _require_pinned_store(self._pinned, directory_fd)
        database_fd = _require_pinned_descriptor(
            directory_fd, STORE_FILENAME, self._pinned.database
        )
        try:
            try:
                connection = _connect(self._directory / STORE_FILENAME)
            except sqlite3.DatabaseError as exc:
                raise StoreCorrupt("write fence database could not be opened") from exc
            try:
                _require_pinned_store(self._pinned, directory_fd)
                try:
                    info = _verify(connection)
                except sqlite3.DatabaseError as exc:
                    raise StoreCorrupt("write fence database is inconsistent") from exc
                _check_anchor(self._anchor, info)
                yield connection
            finally:
                connection.close()
        finally:
            os.close(database_fd)

    def _claim(self, connection, actor, kind, operation_key, digest):
        existing = _latest(connection, actor, kind, operation_key)
        if existing is not None:
            if existing["payload_digest"] != digest:
                raise OperationConflict(
                    "operation key is already bound to a different payload"
                )
            raise OperationLocked(existing["state"], existing["sequence"])
        if _has_unresolved(connection):
            raise SiteLocked(
                "an unresolved operation key blocks every new operation"
            )
        try:
            sequence = _append(
                connection,
                self,
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=digest,
                state=DISPATCHED,
            )
            barrier = barrier_key(actor, kind, operation_key)
            _append(
                connection,
                self,
                actor=LATCH_ACTOR,
                kind=kind,
                operation_key=barrier,
                payload_digest=barrier_digest(actor, kind, operation_key),
                state=DISPATCHED,
            )
        except sqlite3.DatabaseError as exc:
            raise StoreUnavailable(
                "could not durably record the dispatched claim"
            ) from exc
        return Claim(
            ledger=self,
            actor=actor,
            kind=kind,
            operation_key=operation_key,
            payload_digest=digest,
            dispatch_sequence=sequence,
            connection=connection,
        )

    def _acquire_lock(self):
        if fcntl is None:
            raise StoreUnavailable("filesystem locking is unavailable")
        directory_fd = self._open_pinned_directory()
        try:
            _require_pinned_store(self._pinned, directory_fd)
            try:
                descriptor = os.open(
                    LOCK_FILENAME,
                    os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC,
                    dir_fd=directory_fd,
                )
            except OSError as exc:
                raise StoreReplaced(
                    "write fence lock could not be opened safely"
                ) from exc
            try:
                details = os.fstat(descriptor)
                if _identity(details) != self._pinned.lock:
                    raise StoreReplaced("write fence lock inode changed")
                _require_private_details(details, "lock file")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    if exc.errno in (errno.EACCES, errno.EAGAIN):
                        raise LockUnavailable(
                            "write fence is held by another process"
                        ) from exc
                    raise StoreUnavailable(
                        "could not acquire the write fence lock"
                    ) from exc
                _require_pinned_store(self._pinned, directory_fd)
            except BaseException:
                os.close(descriptor)
                raise
            return descriptor, directory_fd
        except BaseException:
            os.close(directory_fd)
            raise


class Claim:
    """One durable ``DISPATCHED`` key, usable only inside its fence.

    Identity and completion state are private and exposed through read-only
    properties, so caller code cannot mutate the key or digest that the
    terminal record will be bound to.
    """

    __slots__ = (
        "_actor",
        "_kind",
        "_operation_key",
        "_payload_digest",
        "_dispatch_sequence",
        "_terminal_sequence",
        "_state",
        "_receipt",
        "_reason",
        "_ledger",
        "_connection",
        "_directory_fd",
        "_open",
    )

    def __init__(
        self,
        *,
        ledger,
        actor,
        kind,
        operation_key,
        payload_digest,
        dispatch_sequence,
        connection,
    ):
        self._ledger = ledger
        self._actor = actor
        self._kind = kind
        self._operation_key = operation_key
        self._payload_digest = payload_digest
        self._dispatch_sequence = dispatch_sequence
        self._terminal_sequence = None
        self._state = DISPATCHED
        self._receipt = None
        self._reason = None
        self._connection = connection
        self._directory_fd = None
        self._open = True

    @property
    def actor(self):
        return self._actor

    @property
    def kind(self):
        return self._kind

    @property
    def operation_key(self):
        return self._operation_key

    @property
    def payload_digest(self):
        return self._payload_digest

    @property
    def state(self):
        return self._state

    @property
    def dispatch_sequence(self):
        return self._dispatch_sequence

    @property
    def terminal_sequence(self):
        return self._terminal_sequence

    @property
    def receipt(self):
        return self._receipt

    @property
    def reason(self):
        return self._reason

    def commit(self, receipt):
        """Append the durable ``COMMITTED`` terminal; raises until it is durable.

        A failure marks the local outcome ``UNKNOWN`` even when the commit may
        have applied; the caller must not retry either way.
        """

        self._require_open()
        receipt = _require_receipt(receipt)
        sequence = self._terminal(COMMITTED, receipt=receipt, reason=None)
        self._state = COMMITTED
        self._receipt = receipt
        self._terminal_sequence = sequence

    def unknown(self, reason):
        """Append the durable ``UNKNOWN`` terminal; the key stays locked."""

        self._require_open()
        reason = _require_reason(reason)
        sequence = self._terminal(UNKNOWN, receipt=None, reason=reason)
        self._state = UNKNOWN
        self._reason = reason
        self._terminal_sequence = sequence

    def _terminal(self, state, *, receipt, reason):
        self._require_open()
        try:
            if self._directory_fd is not None:
                _require_pinned_store(self._ledger._pinned, self._directory_fd)
            sequence = _append(
                self._connection,
                self._ledger,
                actor=self._actor,
                kind=self._kind,
                operation_key=self._operation_key,
                payload_digest=self._payload_digest,
                state=state,
                receipt=receipt,
                reason=reason,
            )
        except Exception as exc:
            self._state = UNKNOWN
            self._release()
            if isinstance(exc, FenceError):
                raise
            raise TerminalAppendFailed(
                "terminal record was not durably appended; outcome is UNKNOWN"
            ) from exc
        self._release()
        return sequence

    def _attach(self, directory_fd):
        self._directory_fd = directory_fd

    def _require_open(self):
        if not self._open:
            raise FenceReleased("the write fence for this claim is released")

    def _release(self):
        self._open = False
        self._connection = None
        self._directory_fd = None


# -- ledger append and verification -----------------------------------


def _append(
    connection,
    ledger,
    *,
    actor,
    kind,
    operation_key,
    payload_digest,
    state,
    receipt=None,
    reason=None,
):
    meta = connection.execute(
        "SELECT deployment_id, epoch, sequence FROM meta"
    ).fetchone()
    if meta is None:
        raise StoreCorrupt("write fence meta is missing")
    _check_anchor(
        ledger._anchor,
        LedgerInfo(meta["deployment_id"], meta["epoch"], meta["sequence"]),
    )
    sequence = meta["sequence"] + 1
    recorded_at = datetime.now(timezone.utc).isoformat(timespec="microseconds")

    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT INTO events (sequence, deployment_id, actor, kind,"
            " operation_key, payload_digest, state, receipt, reason,"
            " recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sequence,
                meta["deployment_id"],
                actor,
                kind,
                operation_key,
                payload_digest,
                state,
                receipt,
                reason,
                recorded_at,
            ),
        )
        updated = connection.execute(
            "UPDATE meta SET sequence = ? WHERE deployment_id = ?"
            " AND sequence = ?",
            (sequence, meta["deployment_id"], meta["sequence"]),
        )
        if updated.rowcount != 1:
            raise StoreCorrupt("write fence sequence update did not apply")
        connection.execute("COMMIT")
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise
    return sequence


def _canonical_sql(sql):
    if not isinstance(sql, str):
        return None
    normalized = " ".join(sql.split()).casefold()
    return re.sub(r"\s*([(),;])\s*", r"\1", normalized)


def _verify_schema(connection):
    tables = {}
    triggers = {}
    for row in connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master"
        " WHERE name NOT LIKE 'sqlite_%'"
    ):
        kind = row["type"]
        if kind == "table":
            tables[row["name"]] = row
        elif kind == "trigger":
            triggers[row["name"]] = row
        elif kind == "index":
            if row["sql"] is not None:
                raise StoreCorrupt("write fence has an unexpected explicit index")
        else:
            raise StoreCorrupt("write fence has an unexpected schema object")

    if set(tables) != set(_TABLE_SQL):
        raise StoreCorrupt("write fence tables do not match the required schema")
    if set(triggers) != set(_TRIGGER_SQL):
        raise StoreCorrupt("write fence triggers do not match the required schema")

    for name, expected in _TABLE_SQL.items():
        row = tables[name]
        if row["tbl_name"] != name:
            raise StoreCorrupt("write fence table ownership is invalid")
        if _canonical_sql(row["sql"]) != _canonical_sql(expected):
            raise StoreCorrupt("write fence table definition differs from schema")
        columns = tuple(
            (column["name"], column["type"], column["notnull"], column["pk"])
            for column in connection.execute(f'PRAGMA table_info("{name}")')
        )
        if columns != _TABLE_COLUMNS[name]:
            raise StoreCorrupt("write fence table columns differ from schema")
        if any(
            column["dflt_value"] is not None
            for column in connection.execute(f'PRAGMA table_info("{name}")')
        ):
            raise StoreCorrupt("write fence table has an unexpected column default")

        indexes = connection.execute(f'PRAGMA index_list("{name}")').fetchall()
        if any(not index["unique"] for index in indexes):
            raise StoreCorrupt("write fence table has an unexpected non-unique index")
        unique = [index for index in indexes if index["unique"]]
        if len(unique) != 1:
            raise StoreCorrupt("write fence table lacks its unique index")
        index = unique[0]
        if index["origin"] != _UNIQUE_INDEX_ORIGIN[name]:
            raise StoreCorrupt("write fence unique index origin is invalid")
        index_columns = tuple(
            column["name"]
            for column in connection.execute(
                f'PRAGMA index_info("{index["name"]}")'
            )
        )
        if index_columns != _UNIQUE_INDEX_COLUMNS[name]:
            raise StoreCorrupt("write fence unique index columns are invalid")

    for name, expected in _TRIGGER_SQL.items():
        row = triggers[name]
        if row["tbl_name"] != "events":
            raise StoreCorrupt("write fence trigger is attached to another table")
        if _canonical_sql(row["sql"]) != _canonical_sql(expected):
            raise StoreCorrupt("write fence trigger definition differs from schema")


def _verify(connection, quick_check=False) -> LedgerInfo:
    if quick_check:
        row = connection.execute("PRAGMA quick_check").fetchone()
        if row is None or row[0] != "ok":
            raise StoreCorrupt("write fence database failed quick_check")

    mode = connection.execute("PRAGMA journal_mode").fetchone()
    if mode is None or mode[0].lower() != "delete":
        raise StoreCorrupt("write fence journal mode is not the private default")

    _verify_schema(connection)

    meta_rows = connection.execute(
        "SELECT deployment_id, epoch, sequence, schema_version FROM meta"
    ).fetchall()
    if len(meta_rows) != 1:
        raise StoreCorrupt("write fence meta is missing or duplicated")
    meta = meta_rows[0]
    deployment_id = meta["deployment_id"]
    epoch = meta["epoch"]
    sequence = meta["sequence"]
    if not _is_hex(deployment_id, _DEPLOYMENT_LENGTH):
        raise StoreCorrupt("write fence deployment id is invalid")
    if not _is_count(epoch, minimum=1):
        raise StoreCorrupt("write fence epoch is invalid")
    if not _is_count(sequence):
        raise StoreCorrupt("write fence sequence is invalid")
    if meta["schema_version"] != SCHEMA_VERSION:
        raise StoreCorrupt("write fence schema version is unsupported")

    rows = connection.execute(
        "SELECT sequence, deployment_id, actor, kind, operation_key,"
        " payload_digest, state, receipt, reason FROM events ORDER BY sequence"
    ).fetchall()
    if [row["sequence"] for row in rows] != list(range(1, len(rows) + 1)):
        raise StoreCorrupt("write fence event sequence is not contiguous")
    if len(rows) != sequence:
        raise StoreCorrupt("write fence sequence does not match its events")

    dispatched = {}
    terminal = {}
    for row in rows:
        if row["deployment_id"] != deployment_id:
            raise StoreCorrupt("write fence event belongs to another deployment")
        for field in ("actor", "kind", "operation_key"):
            if not isinstance(row[field], str) or not row[field]:
                raise StoreCorrupt("write fence event key is invalid")
        if not _is_hex(row["payload_digest"], _DIGEST_LENGTH):
            raise StoreCorrupt("write fence payload digest is invalid")
        state = row["state"]
        if state not in STATES:
            raise StoreCorrupt("write fence event state is invalid")
        if state == COMMITTED:
            receipt = row["receipt"]
            if not isinstance(receipt, str) or not receipt:
                raise StoreCorrupt("committed event has no receipt")
            if row["reason"] is not None:
                raise StoreCorrupt("committed event has a reason")
        elif row["receipt"] is not None:
            raise StoreCorrupt("non-committed event has a receipt")
        if state == UNKNOWN:
            if not isinstance(row["reason"], str) or not row["reason"]:
                raise StoreCorrupt("unknown event has no reason")
        elif row["reason"] is not None:
            raise StoreCorrupt("non-unknown event has a reason")

        identity = (row["actor"], row["kind"], row["operation_key"])
        if state == DISPATCHED:
            if identity in dispatched:
                raise StoreCorrupt("operation key was dispatched twice")
            dispatched[identity] = (row["sequence"], row["payload_digest"])
        else:
            if identity in terminal:
                raise StoreCorrupt("operation key has two terminal records")
            if identity not in dispatched:
                raise StoreCorrupt("terminal event precedes its dispatch")
            dispatch_sequence, dispatch_digest = dispatched[identity]
            if row["sequence"] <= dispatch_sequence:
                raise StoreCorrupt("terminal event precedes its dispatch")
            if row["payload_digest"] != dispatch_digest:
                raise StoreCorrupt("terminal digest does not match its dispatch")
            terminal[identity] = row["sequence"]

    return LedgerInfo(deployment_id=deployment_id, epoch=epoch, sequence=sequence)


def _check_anchor(anchor, info):
    if info.deployment_id != anchor.deployment_id:
        raise RestoredStoreDetected("deployment id does not match the anchor")
    if info.epoch != anchor.epoch:
        raise RestoredStoreDetected("recovery epoch does not match the anchor")
    if info.sequence < anchor.sequence:
        raise RestoredStoreDetected("store sequence is older than the anchor")


def barrier_key(actor, kind, operation_key):
    """Derive the reserved site-wide write barrier key for one operation."""

    actor = _require_actor(actor)
    kind = _require_kind(kind)
    operation_key = _require_operation_key(operation_key)
    seed = "|".join((actor, kind, operation_key))
    tag = hashlib.sha256(
        b"clinic_ui_write_barrier|" + seed.encode("utf-8")
    ).hexdigest()[:32]
    return BARRIER_PREFIX + tag


def barrier_digest(actor, kind, operation_key):
    seed = "|".join((actor, kind, operation_key))
    return hashlib.sha256(
        b"clinic_ui_write_barrier_digest|" + seed.encode("utf-8")
    ).hexdigest()


def _fallback_latch_key(seed):
    tag = hashlib.sha256(
        b"clinic_ui_fallback_latch|" + seed.encode("utf-8")
    ).hexdigest()[:32]
    return "clinic-ui-op-latch-" + tag


def _append_latch_events(connection, ledger, *, operation_key, seed, reason):
    digest = hashlib.sha256(
        b"clinic_ui_manual_investigation|" + seed.encode("utf-8")
    ).hexdigest()
    _append(
        connection,
        ledger,
        actor=LATCH_ACTOR,
        kind=LATCH_KIND,
        operation_key=operation_key,
        payload_digest=digest,
        state=DISPATCHED,
    )
    _append(
        connection,
        ledger,
        actor=LATCH_ACTOR,
        kind=LATCH_KIND,
        operation_key=operation_key,
        payload_digest=digest,
        state=UNKNOWN,
        reason=reason,
    )


def _latest(connection, actor, kind, operation_key):
    return connection.execute(
        "SELECT sequence, payload_digest, state, receipt, reason FROM events"
        " WHERE deployment_id = (SELECT deployment_id FROM meta)"
        " AND actor = ? AND kind = ? AND operation_key = ?"
        " ORDER BY sequence DESC LIMIT 1",
        (actor, kind, operation_key),
    ).fetchone()


def _has_unresolved(connection):
    """True when any key's latest event is not ``COMMITTED``.

    The check is deployment-wide on purpose: one dispatched or unknown key
    blocks every other key and every other actor, not only the original one.
    """

    return (
        connection.execute(
            "SELECT 1 FROM events AS e"
            " WHERE e.deployment_id = (SELECT deployment_id FROM meta)"
            " AND e.state != 'COMMITTED'"
            " AND e.sequence = ("
            "   SELECT MAX(e2.sequence) FROM events AS e2"
            "   WHERE e2.deployment_id = e.deployment_id"
            "     AND e2.actor = e.actor"
            "     AND e2.kind = e.kind"
            "     AND e2.operation_key = e.operation_key"
            " )"
            " LIMIT 1",
        ).fetchone()
        is not None
    )


# -- input and filesystem validation ----------------------------------


def _directory_path(directory):
    if isinstance(directory, str):
        if not directory.strip():
            raise InvalidOperation("store directory must be a non-empty path")
        return Path(directory)
    if isinstance(directory, os.PathLike):
        return Path(directory)
    raise InvalidOperation("store directory must be a path")


def _require_anchor(anchor):
    if not isinstance(anchor, Anchor):
        raise InvalidOperation("anchor must be an Anchor")
    if not _is_hex(anchor.deployment_id, _DEPLOYMENT_LENGTH):
        raise InvalidOperation("anchor deployment id must be 32 hex characters")
    if not _is_count(anchor.epoch, minimum=1):
        raise InvalidOperation("anchor epoch must be a positive integer")
    if not _is_count(anchor.sequence):
        raise InvalidOperation("anchor sequence must be a non-negative integer")
    return anchor


def _require_actor(actor):
    if not isinstance(actor, str):
        raise InvalidOperation("actor is required")
    if actor != actor.strip() or not 1 <= len(actor) <= MAX_ACTOR_LENGTH:
        raise InvalidOperation("actor is invalid")
    _require_printable(actor, "actor")
    return actor


def _require_kind(kind):
    if kind not in OPERATION_KINDS:
        raise InvalidOperation("kind is not a supported operation kind")
    return kind


def _require_operation_key(operation_key):
    if not isinstance(operation_key, str):
        raise InvalidOperation("operation key is required")
    if not MIN_OPERATION_KEY_LENGTH <= len(operation_key) <= MAX_OPERATION_KEY_LENGTH:
        raise InvalidOperation("operation key length is invalid")
    _require_printable(operation_key, "operation key")
    return operation_key


def _require_digest(digest):
    if not _is_hex(digest, _DIGEST_LENGTH):
        raise InvalidOperation("payload digest must be 64 lowercase hex characters")
    return digest


def _require_receipt(receipt):
    if not isinstance(receipt, str) or not receipt:
        raise InvalidOperation("a native receipt is required")
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise InvalidOperation("native receipt is too long")
    _require_printable(receipt, "native receipt")
    return receipt


def _require_reason(reason):
    if not isinstance(reason, str) or not reason:
        raise InvalidOperation("an unknown reason is required")
    if len(reason) > MAX_REASON_LENGTH:
        raise InvalidOperation("unknown reason is too long")
    _require_printable(reason, "unknown reason")
    return reason


def _require_printable(value, field):
    for character in value:
        if ord(character) < 0x20 or ord(character) == 0x7F:
            raise InvalidOperation(f"{field} contains a control character")


def _is_hex(value, length):
    return (
        isinstance(value, str)
        and len(value) == length
        and all(character in _HEX for character in value)
    )


def _is_count(value, minimum=0):
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value >= minimum
    )


def _identity(details):
    return (details.st_dev, details.st_ino)


def _directory_identity(path):
    try:
        details = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise StoreReplaced("write fence directory is unavailable") from exc
    if not stat.S_ISDIR(details.st_mode):
        raise StoreReplaced("write fence directory is not a directory")
    return _identity(details)


def _open_verified_directory(path, expected_identity):
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW | _O_CLOEXEC,
        )
    except OSError as exc:
        raise StoreReplaced("write fence directory could not be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode):
            raise StoreReplaced("write fence directory is not a directory")
        if _identity(details) != expected_identity:
            raise StoreReplaced("write fence directory inode changed")
        _require_private_details(details, "directory")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _inspect_file(directory_fd, name):
    try:
        return os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _require_pinned_file(directory_fd, name, pinned_identity):
    details = _inspect_file(directory_fd, name)
    if details is None:
        raise StoreReplaced("write fence file disappeared")
    if not stat.S_ISREG(details.st_mode):
        raise StoreReplaced("write fence file is not a regular file")
    if _identity(details) != pinned_identity:
        raise StoreReplaced("write fence file inode changed")
    _require_private_details(details, "file")
    return details


def _require_pinned_descriptor(directory_fd, name, pinned_identity):
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise StoreReplaced("write fence file could not be opened safely") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise StoreReplaced("write fence file is not a regular file")
        if _identity(details) != pinned_identity:
            raise StoreReplaced("write fence file inode changed")
        _require_private_details(details, "file")
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _require_pinned_store(pinned, directory_fd):
    _require_pinned_file(directory_fd, STORE_FILENAME, pinned.database)
    _require_pinned_file(directory_fd, LOCK_FILENAME, pinned.lock)
    details = os.fstat(directory_fd)
    if _identity(details) != pinned.directory:
        raise StoreReplaced("write fence directory inode changed")
    _require_private_details(details, "directory")


def _require_private_directory(path):
    if path.is_symlink() or not path.is_dir():
        raise StorePermissionError("write fence store is not a private directory")
    _require_private_details(os.lstat(path), "directory")


def _require_private_details(details, label):
    if stat.S_IMODE(details.st_mode) & 0o077:
        raise StorePermissionError(f"write fence {label} is accessible to others")
    geteuid = getattr(os, "geteuid", None)
    if geteuid is not None and details.st_uid != geteuid():
        raise StorePermissionError(f"write fence {label} is owned by another user")


def _create_private_file(directory_fd, name):
    try:
        descriptor = os.open(
            name,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | _O_NOFOLLOW | _O_CLOEXEC,
            0o600,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise StoreUnavailable("could not create the write fence file") from exc
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _fsync_directory(directory_fd):
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise StoreUnavailable("could not fsync the write fence directory") from exc


def _connect(path):
    uri = f"file:{quote(os.fspath(path), safe='/')}?mode=rw"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=1.0)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA busy_timeout=1000")
        synchronous = connection.execute("PRAGMA synchronous").fetchone()
        busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()
        if synchronous is None or synchronous[0] != 2:
            raise StoreCorrupt("write fence connection is not synchronous=FULL")
        if busy_timeout is None or busy_timeout[0] != 1000:
            raise StoreCorrupt("write fence connection has no busy timeout")
    except BaseException:
        connection.close()
        raise
    return connection


def _release_lock(descriptor):
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        os.close(descriptor)


@contextmanager
def _transaction(connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.DatabaseError:
            pass
        raise
    else:
        connection.execute("COMMIT")
