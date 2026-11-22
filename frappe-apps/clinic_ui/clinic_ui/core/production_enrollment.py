"""Verify host-provisioned production enrollment without framework dependencies.

The protected server environment supplies ``marker_path``; site configuration
supplies only ``configured_site`` and the lowercase SHA-256 ``expected_digest``
of the exact marker bytes. Native routing supplies ``resolved_site`` and the
nominal absolute ``private_site_path`` before resolving symlinks, in the layout
``<sites>/<resolved_site>/private``. The supported profile rejects symlinked
path components and nonstandard layouts. The UTF-8 JSON marker has exactly:
``version`` (integer 1), ``site`` (exact site string), ``private_path``
(canonical absolute private path), and ``installation_id`` (64 lowercase hex
characters encoding 32 cryptographically random bytes). Randomness must be
ensured by the trusted provisioner; this verifier cannot measure entropy.

Provision outside the sites tree, its volumes and backups, on a read-only
external mount with trusted, non-writable parent ancestry; do not copy the proof
into a clone. The marker must be owned by root or the runtime UID, readable by
the runtime, and have mode 0400, 0440, 0600, or 0640. For root-owned 0440,
the marker's group must be an actual Frappe runtime group.
The marker and site paths, their parent directories, and mount topology must be
controlled by the trusted host throughout verification; path checks cannot
establish mount integrity or prevent a privileged host from replacing mounts.
A restored site at the same path with access to the same external proof is not
distinguishable.
No proof contents are logged. This verifier does not activate any service.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat

_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_KEYS = frozenset({"version", "site", "private_path", "installation_id"})
_MAX_BYTES = 4096


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def verify_production_enrollment(
    resolved_site, private_site_path, marker_path, configured_site, expected_digest
):
    """Return false on any mismatch, inaccessible proof, or malformed input."""
    try:
        if (
            not isinstance(resolved_site, str)
            or not resolved_site
            or len(resolved_site) > 140
            or any(c in resolved_site for c in "/\\\x00")
            or resolved_site in (".", "..")
            or configured_site != resolved_site
            or not isinstance(expected_digest, str)
            or not _HEX_256.fullmatch(expected_digest)
            or not isinstance(private_site_path, str)
            or not os.path.isabs(private_site_path)
            or not isinstance(marker_path, str)
            or not os.path.isabs(marker_path)
            or not hasattr(os, "O_NOFOLLOW")
        ):
            return False
        private_path = os.path.abspath(private_site_path)
        site_dir = os.path.dirname(private_path)
        if (
            private_path != os.path.realpath(private_site_path)
            or os.path.basename(private_path) != "private"
            or os.path.basename(site_dir) != resolved_site
            or not os.path.isdir(private_path)
        ):
            return False
        marker_path = os.path.abspath(marker_path)
        if os.path.realpath(marker_path) != marker_path:
            return False
        sites_dir = os.path.dirname(site_dir)
        if os.path.commonpath((marker_path, sites_dir)) in (marker_path, sites_dir):
            return False
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(marker_path, flags)
        try:
            details = os.fstat(fd)
            if (
                not stat.S_ISREG(details.st_mode)
                or details.st_uid not in (0, os.geteuid())
                or stat.S_IMODE(details.st_mode) not in (0o400, 0o440, 0o600, 0o640)
                or details.st_size > _MAX_BYTES
            ):
                return False
            raw = b""
            while len(raw) <= _MAX_BYTES:
                chunk = os.read(fd, _MAX_BYTES + 1 - len(raw))
                if not chunk:
                    break
                raw += chunk
        finally:
            os.close(fd)
        if len(raw) > _MAX_BYTES or not hmac.compare_digest(
            hashlib.sha256(raw).hexdigest(), expected_digest
        ):
            return False
        payload = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object)
        return (
            isinstance(payload, dict)
            and set(payload) == _KEYS
            and type(payload["version"]) is int
            and payload["version"] == 1
            and payload["site"] == resolved_site
            and payload["private_path"] == private_path
            and isinstance(payload["installation_id"], str)
            and bool(_HEX_256.fullmatch(payload["installation_id"]))
        )
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
        return False
