"""Deployment-bound private marker for the local synthetic write path.

The marker is the enforceable second factor for enabling writes. It lives
outside ``site_config.json`` under the site's private directory, so a copied
configuration file, a matching hostname or a second site created from the same
bench cannot by itself turn writes on.

The marker binds three facts together:

* the exact approved site name (``marley-sit.local``),
* the canonical private-directory path of this deployment,
* the ``deployment_id`` of the initialized write-fence store, and
* the marker file's own private ownership and permissions.

Only the operator initialization command creates it, and only after the fence
store exists. A missing, malformed, world-readable, foreign-owned, symlinked,
wrong-site or wrong-deployment marker denies writes.

Threat model (local-only experiment, explicitly not production):

* A person who can write the site's private directory and the fence store can
  also forge this marker. That is the same trust boundary as the site database
  and is out of scope for a local experiment.
* A person who can copy only ``site_config.json`` and the site name cannot
  create the marker with the right ``deployment_id`` unless they also copy or
  initialize the fence store, which is an explicit operator action. The marker
  also binds the canonical private-directory path, so a copied site directory
  at a different location is refused even if the store and marker were copied
  with it.
* A backup/restore is not independently anchored; restore detection remains
  waived for this local-only experiment.
"""

from __future__ import annotations

import json
import os
import stat

from clinic_ui.core.text import is_valid_text

MARKER_DIRNAME = "clinic_ui_native_write_deployment"
MARKER_VERSION = 1
MARKER_KEYS = frozenset({"version", "site", "deployment_id", "private_path"})
MAX_MARKER_BYTES = 512
MAX_SITE_LENGTH = 140

_O_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class MarkerError(Exception):
    """The deployment marker is missing, malformed or not trustworthy."""


def _require_site(site):
    if not is_valid_text(site, MAX_SITE_LENGTH):
        raise MarkerError("site name is invalid")
    if "/" in site or "\\" in site or site in (".", ".."):
        raise MarkerError("site name is invalid")
    return site


def _require_deployment_id(deployment_id):
    if not isinstance(deployment_id, str) or not deployment_id:
        raise MarkerError("deployment id is invalid")
    if len(deployment_id) > 128:
        raise MarkerError("deployment id is invalid")
    return deployment_id


def marker_directory(private_dir):
    return os.path.join(os.fspath(private_dir), MARKER_DIRNAME)


def marker_path(private_dir, site):
    return os.path.join(marker_directory(private_dir), _require_site(site) + ".json")


def write_marker(private_dir, site, deployment_id):
    """Create the private marker once, bound to the fence deployment id.

    Never overwrites an existing marker: re-initialization must be an explicit
    operator decision, not a side effect of a retry.
    """

    site = _require_site(site)
    deployment_id = _require_deployment_id(deployment_id)
    directory = marker_directory(private_dir)
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        details = os.stat(directory, follow_symlinks=False)
        if not stat.S_ISDIR(details.st_mode):
            raise MarkerError("deployment marker directory is not a directory")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise MarkerError("deployment marker directory is not private")
    except MarkerError:
        raise
    except OSError as exc:
        raise MarkerError("deployment marker directory is unavailable") from exc

    payload = json.dumps(
        {
            "version": MARKER_VERSION,
            "site": site,
            "deployment_id": deployment_id,
            "private_path": os.path.realpath(private_dir),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    path = marker_path(private_dir, site)
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW | _O_CLOEXEC,
            0o600,
        )
    except OSError as exc:
        raise MarkerError(
            "deployment marker already exists or cannot be created"
        ) from exc
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError("deployment marker write made no progress")
            offset += written
        os.fsync(descriptor)
    except OSError as exc:
        raise MarkerError("deployment marker could not be written") from exc
    finally:
        os.close(descriptor)


def read_marker(private_dir, site, expected_deployment_id):
    """True only for a private marker bound to this site and deployment id."""

    site = _require_site(site)
    expected_deployment_id = _require_deployment_id(expected_deployment_id)
    path = marker_path(private_dir, site)
    try:
        descriptor = os.open(path, os.O_RDONLY | _O_NOFOLLOW | _O_CLOEXEC)
    except OSError as exc:
        raise MarkerError("deployment marker is missing") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise MarkerError("deployment marker is not a file")
        if stat.S_IMODE(details.st_mode) & 0o077:
            raise MarkerError("deployment marker is accessible to others")
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and details.st_uid != geteuid():
            raise MarkerError("deployment marker is owned by another user")
        chunks = []
        remaining = MAX_MARKER_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
    except MarkerError:
        raise
    except OSError as exc:
        raise MarkerError("deployment marker could not be read") from exc
    finally:
        os.close(descriptor)

    if len(raw) > MAX_MARKER_BYTES:
        raise MarkerError("deployment marker is too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise MarkerError("deployment marker is malformed") from exc
    if not isinstance(payload, dict) or set(payload) != MARKER_KEYS:
        raise MarkerError("deployment marker is malformed")
    if payload["version"] != MARKER_VERSION:
        raise MarkerError("deployment marker version is unsupported")
    if payload["site"] != site:
        raise MarkerError("deployment marker site does not match")
    if payload["deployment_id"] != expected_deployment_id:
        raise MarkerError("deployment marker is not bound to this fence")
    if payload["private_path"] != os.path.realpath(private_dir):
        raise MarkerError("deployment marker is not bound to this path")
    return True
