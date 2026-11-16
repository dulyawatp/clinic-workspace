"""Explicitly initialized write-fence store and anchor paths.

The fence reuses :mod:`clinic_ui.core.write_fence`; this module adds only the
operator-initialized store anchor that ``Ledger.open`` requires. It never
creates, repairs or migrates a store from a request path. Missing, malformed or
non-private anchor/store state raises ``StoreUnavailable`` so writes fail closed.

Local-only exception: the anchor lives next to the store under the site's
private directory rather than in an independently backed-up system. That is
sufficient for same-process restart locking, but it does **not** detect a
coordinated restore of the site private files. Restore/recovery gates are
explicitly waived for this synthetic local experiment by the task owner; this
module makes no production or restore-safety claim.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from clinic_ui.core import write_fence

STORE_DIRNAME = "clinic_ui_native_write_fence"
ANCHOR_FILENAME = "clinic_ui_native_write_fence.anchor.json"
ANCHOR_KEYS = frozenset({"deployment_id", "epoch", "sequence"})
MAX_ANCHOR_BYTES = 512

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _write_anchor(anchor_path, info):
    path = Path(anchor_path)
    parent = path.parent
    if not parent.is_dir():
        raise write_fence.StoreUnavailable("write fence anchor directory is missing")
    payload = json.dumps(
        {
            "deployment_id": info.deployment_id,
            "epoch": info.epoch,
            "sequence": info.sequence,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
            0o600,
        )
    except OSError as exc:
        raise write_fence.StoreUnavailable(
            "write fence anchor already exists or cannot be created"
        ) from exc
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("write fence anchor write made no progress")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise write_fence.StoreUnavailable(
            "write fence anchor could not be written"
        ) from exc
    finally:
        os.close(descriptor)
    try:
        directory_descriptor = os.open(
            parent, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise write_fence.StoreUnavailable(
            "write fence anchor directory could not be synced"
        ) from exc


def _read_anchor(anchor_path):
    path = Path(anchor_path)
    try:
        descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError as exc:
        raise write_fence.StoreNotInitialized(
            "write fence anchor is missing"
        ) from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise write_fence.StoreUnavailable("write fence anchor is not a file")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise write_fence.StorePermissionError(
                "write fence anchor is accessible to others"
            )
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and details.st_uid != geteuid():
            raise write_fence.StorePermissionError(
                "write fence anchor is owned by another user"
            )
        chunks = []
        remaining = MAX_ANCHOR_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except OSError as exc:
        raise write_fence.StoreUnavailable(
            "write fence anchor could not be read"
        ) from exc
    finally:
        os.close(descriptor)
    if len(raw) > MAX_ANCHOR_BYTES:
        raise write_fence.StoreCorrupt("write fence anchor is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise write_fence.StoreCorrupt("write fence anchor is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != ANCHOR_KEYS:
        raise write_fence.StoreCorrupt("write fence anchor is malformed")
    if not isinstance(payload["deployment_id"], str):
        raise write_fence.StoreCorrupt("write fence anchor is malformed")
    if not isinstance(payload["epoch"], int) or isinstance(payload["epoch"], bool):
        raise write_fence.StoreCorrupt("write fence anchor is malformed")
    if not isinstance(payload["sequence"], int) or isinstance(
        payload["sequence"], bool
    ):
        raise write_fence.StoreCorrupt("write fence anchor is malformed")
    try:
        return write_fence.Anchor(**payload)
    except write_fence.InvalidOperation as exc:
        raise write_fence.StoreCorrupt("write fence anchor is malformed") from exc


def initialize_store(directory, anchor_path):
    """Create a new fence store and its anchor; never reuses an existing one."""

    info = write_fence.initialize(directory)
    _write_anchor(anchor_path, info)
    return info


def open_store(directory, anchor_path):
    """Open the anchored store, or raise ``StoreUnavailable``."""

    anchor = _read_anchor(anchor_path)
    return write_fence.Ledger.open(directory, anchor=anchor)


def read_anchor(anchor_path):
    """Return the validated anchor without opening the store."""

    return _read_anchor(anchor_path)
