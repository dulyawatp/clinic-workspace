"""Standard-library HTTP acceptance support for protected photo delivery.

This module never imports Frappe and performs no I/O at import time. It validates an
explicitly approved disposable target and an access-restricted runtime manifest, then
issues exactly the recorded read-only GET cases in that manifest. Missing or unsafe
configuration raises :class:`ConfigError` before any transport is constructed.

Boundaries kept deliberately narrow:

* HTTPS only, except an explicitly declared SSH-loopback tunnel contract; the
  evaluation HTTP target is always refused.
* Redirects are never followed, ambient proxies are ignored and no cookie jar is
  installed. A session cookie is attached only for the manifest actor that names it;
  Guest sends none and never inherits another actor's session.
* Every body is read under a conservative cap. Allowed bodies must match the
  manifest's exact size and SHA-256 (or an exact restricted local fixture), so a
  truncated protected body cannot be accepted.
* ZIP archives are inspected in memory. Expected members are matched by bytes/hash;
  an archive hash is never compared because ZIP timestamps are variable. Denied and
  mixed archives are checked for protected member names, protected bytes and
  protected hashes.
* Every response must carry the exact cache trio: ``Cache-Control: no-store``,
  ``Pragma: no-cache`` and ``Expires: 0``.

The module does not implement mutation, raw-bank/finance POST, lifecycle, nginx/TLS,
concurrency or cleanup cases. Those remain pending separately provisioned installed
cases; see :data:`PENDING_INSTALLED_CASES`.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import io
import json
import os
import pathlib
import posixpath
import re
import stat
import urllib.error
import urllib.parse
import urllib.request
import zipfile

BASE_URL_ENV = "CLINIC_PHOTO_TEST_BASE_URL"
MANIFEST_ENV = "CLINIC_PHOTO_TEST_MANIFEST"
SCHEMA = "clinic_photos.http_manifest/v1"

CACHE_CONTROL = "no-store"
PRAGMA = "no-cache"
EXPIRES = "0"

DEFAULT_TIMEOUT_SECONDS = 15
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ZIP_MEMBERS = 32
MAX_ZIP_UNCOMPRESSED_BYTES = 16 * 1024 * 1024
MAX_URL_LENGTH = 4096

# Recorded from the Task 2/3 installed-source excerpts. The manifest cannot name a
# different path for these kinds; direct/internal paths come from recorded File rows
# and the internal nginx location respectively.
ROUTE_CONTRACT = {
    "direct_private": {
        "method": "GET",
        "path": None,
        "allowed_query": ("fid",),
    },
    "native_download": {
        # apps/frappe/frappe/handler.py:258-274 (installed excerpt). There is no
        # frappe.core.api.file.download_file route and no fid argument.
        "method": "GET",
        "path": "/api/method/frappe.handler.download_file",
        "allowed_query": ("file_url",),
    },
    "native_zip": {
        "method": "GET",
        "path": "/api/method/frappe.core.api.file.zip_files",
        "allowed_query": (),
    },
    "internal_protected": {
        "method": "GET",
        "path_prefix": "/protected/",
        "allowed_query": (),
    },
}

REQUIRED_ACTORS = {
    "author": "clinician-author",
    "other_clinician": "clinician-other",
    "nonclinician": "nonclinician",
    "guest": "guest",
    "revoked_owner": "revoked-owner-existing-session",
    "disabled": "disabled-user",
    "shared_nonclinician": "explicit-file-share-nonclinician",
}

REQUIRED_ASSETS = ("original", "annotation", "unrelated")
REQUIRED_FILES = ("original", "annotation", "alias", "unrelated")

REQUIRED_FILE_SPEC = {
    "original": {"asset": "original", "same_path_as": None},
    "annotation": {"asset": "annotation", "same_path_as": None},
    # The only intentional duplicate storage identity: a distinct File ID that
    # shares the original's canonical storage key and expected bytes.
    "alias": {"asset": "original", "same_path_as": "original"},
    "unrelated": {"asset": "unrelated", "same_path_as": None},
}

REQUIRED_ROUTE_SPEC = {
    "direct_original": {
        "kind": "direct_private",
        "file": "original",
        "fid_file": None,
    },
    "direct_alias": {
        "kind": "direct_private",
        "file": "alias",
        "fid_file": "alias",
    },
    "download_original": {
        "kind": "native_download",
        "file": "original",
        "file_url_file": "original",
    },
    "download_annotation": {
        "kind": "native_download",
        "file": "annotation",
        "file_url_file": "annotation",
    },
    "zip_protected": {
        "kind": "native_zip",
        "files": ("original", "annotation"),
    },
    "zip_mixed": {
        "kind": "native_zip",
        "files": ("original", "unrelated"),
    },
    "internal_protected": {
        "kind": "internal_protected",
        "file": "original",
    },
}

REQUIRED_ROUTES = tuple(REQUIRED_ROUTE_SPEC)

# Each case is bound to its actor, named route, body mode, exact allowed/forbidden
# protected assets and evidence-backed status policy. Labels alone are not coverage.
REQUIRED_CASE_SPEC = {
    "author.direct.original.allowed": {
        "actor": "author",
        "route": "direct_original",
        "body": "allowed_bytes",
        "asset": "original",
        "assets": (),
        "forbidden": (),
        "content_type": "image/png",
        "status_kind": "allowed",
    },
    "author.direct.alias.allowed": {
        "actor": "author",
        "route": "direct_alias",
        "body": "allowed_bytes",
        "asset": "original",
        "assets": (),
        "forbidden": (),
        "content_type": "image/png",
        "status_kind": "allowed",
    },
    "author.download.original.allowed": {
        "actor": "author",
        "route": "download_original",
        "body": "allowed_bytes",
        "asset": "original",
        "assets": (),
        "forbidden": (),
        "content_type": "image/png",
        "status_kind": "allowed",
    },
    "author.download.annotation.allowed": {
        "actor": "author",
        "route": "download_annotation",
        "body": "allowed_bytes",
        "asset": "annotation",
        "assets": (),
        "forbidden": (),
        "content_type": "application/json",
        "status_kind": "allowed",
    },
    "author.zip.protected.allowed": {
        "actor": "author",
        "route": "zip_protected",
        "body": "zip_allowed",
        "asset": None,
        "assets": ("original", "annotation"),
        "forbidden": (),
        "content_type": "application/zip",
        "status_kind": "allowed",
    },
    "other_clinician.direct.original.allowed": {
        "actor": "other_clinician",
        "route": "direct_original",
        "body": "allowed_bytes",
        "asset": "original",
        "assets": (),
        "forbidden": (),
        "content_type": "image/png",
        "status_kind": "allowed",
    },
    "other_clinician.download.annotation.allowed": {
        "actor": "other_clinician",
        "route": "download_annotation",
        "body": "allowed_bytes",
        "asset": "annotation",
        "assets": (),
        "forbidden": (),
        "content_type": "application/json",
        "status_kind": "allowed",
    },
    "other_clinician.zip.protected.allowed": {
        "actor": "other_clinician",
        "route": "zip_protected",
        "body": "zip_allowed",
        "asset": None,
        "assets": ("original", "annotation"),
        "forbidden": (),
        "content_type": "application/zip",
        "status_kind": "allowed",
    },
    "nonclinician.direct.original.denied": {
        "actor": "nonclinician",
        "route": "direct_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "nonclinician.download.original.denied": {
        "actor": "nonclinician",
        "route": "download_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "guest.direct.original.denied": {
        "actor": "guest",
        "route": "direct_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "guest.download.original.denied": {
        "actor": "guest",
        "route": "download_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "guest.zip.protected.denied": {
        "actor": "guest",
        "route": "zip_protected",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "revoked_owner.direct.original.denied": {
        "actor": "revoked_owner",
        "route": "direct_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "revoked_owner.download.annotation.denied": {
        "actor": "revoked_owner",
        "route": "download_annotation",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "disabled.direct.original.denied": {
        "actor": "disabled",
        "route": "direct_original",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "disabled.zip.protected.denied": {
        "actor": "disabled",
        "route": "zip_protected",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "shared_nonclinician.direct.alias.denied": {
        "actor": "shared_nonclinician",
        "route": "direct_alias",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "shared_nonclinician.download.annotation.denied": {
        "actor": "shared_nonclinician",
        "route": "download_annotation",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "auth-denied",
    },
    "shared_nonclinician.zip.mixed.denied_protected": {
        "actor": "shared_nonclinician",
        "route": "zip_mixed",
        "body": "zip_mixed",
        "asset": None,
        "assets": ("unrelated",),
        "forbidden": ("original",),
        "content_type": "application/zip",
        "status_kind": "allowed",
    },
    "guest.internal_protected.denied": {
        "actor": "guest",
        "route": "internal_protected",
        "body": "denied",
        "asset": None,
        "assets": (),
        "forbidden": ("original", "annotation"),
        "content_type": None,
        "status_kind": "internal-not-found",
    },
}

REQUIRED_CASE_IDS = tuple(REQUIRED_CASE_SPEC)

PENDING_INSTALLED_CASES = (
    "non-author draft save/update denial and allowed author append",
    "protected File relink/reown/delete/replace/publicize denial",
    "owner-change, privacy-flip, URL-move and encoded-URL mutation denial",
    "native thumbnail/optimization denial for protected objects",
    "raw Bank Statement Import protected-input rejection before raw read",
    "finance/mutation POST requests (not authorized by this harness)",
    "metadata/list reads for protected and unrelated rows",
    "concurrency, retry, crash and restart reconciliation",
    "authorized frontend nginx accelerated-delivery and TLS verification",
    "cleanup/disposal verification for exact synthetic objects",
    "installed MRO/method resolution and both clinician role results",
)

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ConfigError(Exception):
    """Prerequisites are missing or unsafe; nothing has been sent."""


class ProbeError(Exception):
    """A response did not satisfy the declared read-only acceptance case."""


class HttpResult:
    """One measured HTTP response; no URL is retained for evidence output."""

    __slots__ = ("status", "headers", "body")

    def __init__(self, status, headers, body):
        self.status = int(status)
        self.headers = headers
        self.body = body


class Config:
    """Validated manifest plus the external environment used for session headers."""

    __slots__ = ("base_url", "manifest", "manifest_path", "_environ", "timeout")

    def __init__(self, base_url, manifest, manifest_path, environ):
        self.base_url = base_url
        self.manifest = manifest
        self.manifest_path = manifest_path
        self._environ = dict(environ)
        self.timeout = DEFAULT_TIMEOUT_SECONDS

    def __repr__(self):
        return f"Config(base_url={self.base_url!r}, manifest={self.manifest_path.name!r})"

    def session_header(self, actor_name):
        actor = self.manifest["actors"][actor_name]
        if actor["kind"] == "guest":
            return None
        name = actor["session_env"]
        value = self._environ.get(name)
        if not value:
            raise ConfigError(f"{name} is required for actor {actor_name!r}")
        if not _valid_header_value(value):
            raise ConfigError(
                f"{name} contains characters that are invalid in a header value"
            )
        return value


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect; urllib turns the 3xx into an HTTPError."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def build_opener():
    """No ambient proxy, no cookie processor, no redirect following."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}), NoRedirectHandler()
    )


def _valid_header_value(value):
    return isinstance(value, str) and bool(value) and not any(
        character in value for character in ("\r", "\n", "\x00")
    )


def _header_value(headers, name):
    if headers is None:
        return None
    lowered = name.lower()
    keys = headers.keys()
    for key in keys:
        if str(key).lower() == lowered:
            return headers.get(key)
    return None


def _header_items(headers):
    if headers is None:
        return []
    return [(str(key), str(value)) for key, value in headers.items()]


def _header_values(headers, name):
    """Return every value for a header; duplicates must not be silently merged."""
    if headers is None:
        return []
    get_all = getattr(headers, "get_all", None)
    if get_all is not None:
        return [value for value in (get_all(name) or [])]
    value = _header_value(headers, name)
    return [] if value is None else [value]


def redact(text, environ=None):
    """Replace every session environment value with a stable marker."""
    environ = os.environ if environ is None else environ
    result = str(text)
    secrets = [
        str(value)
        for key, value in environ.items()
        if key.startswith("CLINIC_PHOTO_TEST_SESSION_") and value
    ]
    for secret in sorted(secrets, key=len, reverse=True):
        result = result.replace(secret, "[redacted]")
    return result


def _manifest_problem(problems, where, message):
    problems.append(f"{where}: {message}")


def _check_keys(value, allowed, where, problems, required=()):
    if not isinstance(value, dict):
        _manifest_problem(problems, where, "must be an object")
        return False
    for key in value:
        if key not in allowed:
            _manifest_problem(problems, where, f"unknown key {key!r}")
    for key in required:
        if key not in value:
            _manifest_problem(problems, where, f"missing key {key!r}")
    return True


def _safe_relative_path(value):
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    path = pathlib.PurePosixPath(value)
    return bool(path.parts) and all(part not in ("", ".", "..") for part in path.parts)


def _canonical_route_path(value, root):
    """Validate one recorded storage/internal path before any authenticated request.

    Encoding is refused outright rather than decoded loosely: the recorded route
    contract carries canonical paths, and percent-encoded separators, traversal,
    controls or double encoding would otherwise make the client's URL ambiguous.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("path must be a non-empty string")
    if not root.endswith("/"):
        root += "/"
    if not value.startswith(root):
        raise ValueError("path is outside the allowed root")
    if "%" in value:
        raise ValueError("percent-encoded paths are not accepted in recorded routes")
    if "\\" in value:
        raise ValueError("backslash separators are not accepted")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError("control characters are not accepted in paths")
    if any(character.isspace() for character in value):
        raise ValueError("whitespace is not accepted in paths")
    if "?" in value or "#" in value:
        raise ValueError("query or fragment text is not accepted in paths")
    if value.endswith("/"):
        raise ValueError("path must name a file, not a directory")
    parts = value.split("/")
    if parts[0] != "":
        raise ValueError("path must be absolute")
    if any(part in ("", ".", "..") for part in parts[1:]):
        raise ValueError("path has an empty, dot or traversal segment")
    if posixpath.normpath(value) != value:
        raise ValueError("path is not canonically normalized")
    return value


def _target_problems(target, problems, require_approval):
    if not _check_keys(
        target,
        {
            "base_url",
            "approved_target",
            "disposable",
            "approval_reference",
            "transport",
            "tunnel_contract",
        },
        "target",
        problems,
        required=(
            "base_url",
            "approved_target",
            "disposable",
            "approval_reference",
            "transport",
        ),
    ):
        return
    base_url = target.get("base_url")
    approved = target.get("approved_target")
    transport = target.get("transport")
    disposable = target.get("disposable")
    approval_reference = target.get("approval_reference")
    tunnel = target.get("tunnel_contract")

    if not isinstance(base_url, str) or not base_url:
        _manifest_problem(problems, "target", "base_url must be a non-empty string")
        return
    if base_url != approved:
        _manifest_problem(
            problems, "target", "base_url must equal approved_target exactly"
        )
    if not isinstance(disposable, bool):
        _manifest_problem(problems, "target", "disposable must be a boolean")
    if not isinstance(approval_reference, str) or not approval_reference.strip():
        _manifest_problem(
            problems, "target", "approval_reference must be a non-empty string"
        )
    if transport not in ("https", "ssh-tunnel"):
        _manifest_problem(problems, "target", "transport must be https or ssh-tunnel")
        return

    parsed = urllib.parse.urlsplit(base_url)
    if parsed.username or parsed.password:
        _manifest_problem(problems, "target", "credentials in the target are refused")
    if parsed.query or parsed.fragment:
        _manifest_problem(problems, "target", "target must not carry query or fragment")
    if parsed.path not in ("", "/"):
        _manifest_problem(problems, "target", "target must not carry a path prefix")
    if base_url.endswith("/"):
        _manifest_problem(problems, "target", "target must not end with a slash")
    if not parsed.hostname:
        _manifest_problem(problems, "target", "target must name a host")
        return
    try:
        port = parsed.port
    except ValueError:
        _manifest_problem(problems, "target", "target port is not a valid integer")
        port = None
    if parsed.hostname == "10.0.100.10" or port == 8080:
        _manifest_problem(
            problems,
            "target",
            "the evaluation HTTP target and port 8080 are always refused",
        )
    if parsed.scheme == "https":
        if transport != "https":
            _manifest_problem(
                problems, "target", "https requires transport 'https'"
            )
        if tunnel is not None:
            _manifest_problem(
                problems, "target", "https must not declare a tunnel contract"
            )
    elif parsed.scheme == "http":
        if transport != "ssh-tunnel":
            _manifest_problem(
                problems,
                "target",
                "plain HTTP is refused unless an explicit ssh-tunnel contract is used",
            )
        else:
            _tunnel_problems(parsed, tunnel, problems)
    else:
        _manifest_problem(problems, "target", "target scheme must be https")

    if require_approval:
        if disposable is not True:
            _manifest_problem(
                problems, "target", "disposable must be true for a runtime manifest"
            )
        if isinstance(approval_reference, str):
            marker = approval_reference.upper()
            if any(
                token in marker
                for token in ("REPLACE", "PLACEHOLDER", "PENDING", "APPROVAL-REQUIRED")
            ):
                _manifest_problem(
                    problems,
                    "target",
                    "approval_reference still carries a placeholder; explicit approval is required",
                )


def _tunnel_problems(parsed, tunnel, problems):
    if not isinstance(tunnel, dict):
        _manifest_problem(
            problems, "target", "ssh-tunnel transport requires a tunnel contract"
        )
        return
    _check_keys(
        tunnel,
        {"local_host", "local_port", "remote_approval_reference"},
        "target.tunnel_contract",
        problems,
        required=("local_host", "local_port", "remote_approval_reference"),
    )
    local_host = tunnel.get("local_host")
    local_port = tunnel.get("local_port")
    reference = tunnel.get("remote_approval_reference")
    if local_host not in ("127.0.0.1", "::1", "localhost"):
        _manifest_problem(
            problems, "target", "tunnel local_host must be a loopback address"
        )
    if parsed.hostname != local_host and not (
        local_host == "localhost" and parsed.hostname in ("127.0.0.1", "::1")
    ):
        _manifest_problem(
            problems, "target", "target host must match the tunnel local_host"
        )
    if not isinstance(local_port, int) or not 1 <= local_port <= 65535:
        _manifest_problem(problems, "target", "tunnel local_port must be 1-65535")
    elif parsed.port != local_port:
        _manifest_problem(
            problems, "target", "target port must match the tunnel local_port"
        )
    if not isinstance(reference, str) or not reference.strip():
        _manifest_problem(
            problems,
            "target",
            "tunnel contract needs the recorded remote approval reference",
        )


def _records_problems(records, problems):
    if not _check_keys(
        records,
        {"photo", "version", "draft"},
        "records",
        problems,
        required=("photo", "version", "draft"),
    ):
        return
    seen = set()
    for name in ("photo", "version", "draft"):
        value = records[name]
        if not isinstance(value, str) or not value:
            _manifest_problem(problems, f"records.{name}", "must be a non-empty string")
        elif value in seen:
            _manifest_problem(problems, "records", "identifiers must be distinct")
        else:
            seen.add(value)


def _assets_problems(assets, problems):
    if not _check_keys(
        assets, set(REQUIRED_ASSETS), "assets", problems, required=REQUIRED_ASSETS
    ):
        return
    for name, asset in assets.items():
        where = f"assets.{name}"
        if not _check_keys(
            asset,
            {"file_name", "sha256", "size", "content_type", "body_fragments", "fixture"},
            where,
            problems,
            required=("file_name", "sha256", "size"),
        ):
            continue
        if not isinstance(asset["file_name"], str) or not asset["file_name"]:
            _manifest_problem(problems, where, "file_name must be a non-empty string")
        if not isinstance(asset["sha256"], str) or not _HEX64.match(asset["sha256"]):
            _manifest_problem(problems, where, "sha256 must be 64 lowercase hex digits")
        size = asset["size"]
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            _manifest_problem(problems, where, "size must be a positive integer")
        elif size > MAX_BODY_BYTES:
            _manifest_problem(
                problems, where, "size exceeds the conservative read cap"
            )
        content_type = asset.get("content_type")
        if content_type is not None and (
            not isinstance(content_type, str) or not content_type
        ):
            _manifest_problem(problems, where, "content_type must be a string")
        fragments = asset.get("body_fragments")
        if name in ("original", "annotation"):
            if not isinstance(fragments, list) or not fragments:
                _manifest_problem(
                    problems,
                    where,
                    "protected assets need non-empty body_fragments for denial checks",
                )
        if fragments is not None:
            if not isinstance(fragments, list):
                _manifest_problem(problems, where, "body_fragments must be a list")
            else:
                for fragment in fragments:
                    if not isinstance(fragment, str) or len(fragment) < 8:
                        _manifest_problem(
                            problems,
                            where,
                            "each body fragment must be a string of at least 8 characters",
                        )
        fixture = asset.get("fixture")
        if fixture is not None and not _safe_relative_path(fixture):
            _manifest_problem(
                problems, where, "fixture must be a safe relative path"
            )
    digests = [
        asset.get("sha256")
        for asset in assets.values()
        if isinstance(asset, dict) and isinstance(asset.get("sha256"), str)
    ]
    if len(set(digests)) != len(digests):
        _manifest_problem(
            problems,
            "assets",
            "distinct expected assets must not share a hash; a hash-equal"
            " different-path substitute is not a shared-storage alias",
        )


def _files_problems(files, assets, problems):
    if not _check_keys(
        files, set(REQUIRED_FILES), "files", problems, required=REQUIRED_FILES
    ):
        return
    seen_fids = set()
    seen_paths = {}
    for name, spec in REQUIRED_FILE_SPEC.items():
        entry = files.get(name)
        where = f"files.{name}"
        if not _check_keys(
            entry, {"fid", "path", "asset"}, where, problems, required=("fid", "path", "asset")
        ):
            continue
        fid = entry["fid"]
        path = entry["path"]
        asset = entry["asset"]
        if not isinstance(fid, str) or not fid:
            _manifest_problem(problems, where, "fid must be a non-empty string")
        elif fid in seen_fids:
            _manifest_problem(problems, where, "duplicate fid")
        else:
            seen_fids.add(fid)
        try:
            _canonical_route_path(path, "/private/files/")
        except ValueError as error:
            _manifest_problem(problems, where, str(error))
            continue
        if not isinstance(asset, str):
            _manifest_problem(problems, where, "asset must be a string")
            continue
        if asset != spec["asset"]:
            _manifest_problem(
                problems,
                where,
                f"asset must be {spec['asset']!r} for this File role",
            )
        if asset not in assets:
            _manifest_problem(problems, where, "asset reference is not declared")
        if spec["same_path_as"] is not None:
            target = files.get(spec["same_path_as"])
            target_path = target.get("path") if isinstance(target, dict) else None
            if target_path != path:
                _manifest_problem(
                    problems,
                    where,
                    "shared-storage alias must reuse the canonical original path",
                )
            target_fid = target.get("fid") if isinstance(target, dict) else None
            if target_fid == fid:
                _manifest_problem(
                    problems,
                    where,
                    "shared-storage alias must have a distinct File ID",
                )
        elif path in seen_paths:
            _manifest_problem(
                problems,
                where,
                "unintended duplicate storage path; only the declared alias may share"
                " the original path",
            )
        seen_paths.setdefault(path, name)


def _actors_problems(actors, problems):
    if not _check_keys(
        actors, set(REQUIRED_ACTORS), "actors", problems, required=REQUIRED_ACTORS
    ):
        return
    for name, expected_kind in REQUIRED_ACTORS.items():
        actor = actors.get(name)
        where = f"actors.{name}"
        if not _check_keys(
            actor, {"kind", "session_env"}, where, problems, required=("kind", "session_env")
        ):
            continue
        if actor["kind"] != expected_kind:
            _manifest_problem(problems, where, f"kind must be {expected_kind!r}")
        session_env = actor["session_env"]
        if expected_kind == "guest":
            if session_env is not None:
                _manifest_problem(
                    problems, where, "Guest must not reference a session"
                )
            continue
        if not isinstance(session_env, str) or not session_env:
            _manifest_problem(problems, where, "a session env reference is required")
        elif not session_env.startswith("CLINIC_PHOTO_TEST_SESSION_"):
            _manifest_problem(
                problems,
                where,
                "session reference must name a CLINIC_PHOTO_TEST_SESSION_* env variable",
            )


def _route_problems(route, name, files, problems):
    where = f"routes.{name}"
    if name not in REQUIRED_ROUTE_SPEC:
        _manifest_problem(problems, where, "unexpected route name")
        return
    spec = REQUIRED_ROUTE_SPEC[name]
    kind = route.get("kind") if isinstance(route, dict) else None
    if not isinstance(kind, str) or kind not in ROUTE_CONTRACT:
        _manifest_problem(problems, where, "unknown or missing route kind")
        return
    if kind != spec["kind"]:
        _manifest_problem(
            problems, where, f"route kind must be {spec['kind']!r}"
        )
    contract = ROUTE_CONTRACT[kind]
    allowed = {"kind"}
    if kind in ("direct_private", "native_download"):
        allowed.update({"file", "query"})
    elif kind == "native_zip":
        allowed.update({"files"})
    else:
        allowed.update({"path"})
    if not _check_keys(route, allowed, where, problems, required=("kind",)):
        return

    if kind in ("direct_private", "native_download"):
        file_key = route.get("file")
        if file_key != spec["file"]:
            _manifest_problem(
                problems, where, f"route file must be {spec['file']!r}"
            )
        if not isinstance(file_key, str) or file_key not in files:
            _manifest_problem(problems, where, "file reference is not declared")
            return
        for referenced in (spec.get("file_url_file"), spec.get("fid_file")):
            if referenced is not None and referenced not in files:
                _manifest_problem(
                    problems, where, "route references a File that is not declared"
                )
                return
        query = route.get("query", {})
        if not isinstance(query, dict):
            _manifest_problem(problems, where, "query must be an object")
            return
        for key in query:
            if key not in contract["allowed_query"]:
                _manifest_problem(problems, where, f"query key {key!r} is not recorded")
        for key, value in query.items():
            if not isinstance(value, str) or not value:
                _manifest_problem(problems, where, "query values must be non-empty strings")
            elif "\r" in value or "\n" in value:
                _manifest_problem(problems, where, "query values must not contain controls")
        if kind == "native_download":
            expected_query = {
                "file_url": files[spec["file_url_file"]]["path"]
            }
        elif spec.get("fid_file"):
            expected_query = {
                "fid": files[spec["fid_file"]]["fid"]
            }
        else:
            expected_query = {}
        if query != expected_query:
            _manifest_problem(
                problems,
                where,
                "route query does not match the recorded contract for this route",
            )
    elif kind == "native_zip":
        file_keys = route.get("files")
        if not isinstance(file_keys, list) or not file_keys:
            _manifest_problem(problems, where, "files must be a non-empty list")
            return
        if any(not isinstance(file_key, str) for file_key in file_keys):
            _manifest_problem(problems, where, "files entries must be strings")
            return
        if len(set(file_keys)) != len(file_keys):
            _manifest_problem(problems, where, "files contains duplicates")
        if tuple(file_keys) != spec["files"]:
            _manifest_problem(
                problems,
                where,
                "native ZIP file order/list does not match the recorded route",
            )
        for file_key in file_keys:
            if not isinstance(file_key, str) or file_key not in files:
                _manifest_problem(problems, where, f"unknown file {file_key!r}")
        if route.get("query"):
            _manifest_problem(problems, where, "native ZIP does not take a query")
    else:
        path = route.get("path")
        if not isinstance(path, str):
            _manifest_problem(
                problems, where, "path must be an internal /protected/ location"
            )
            return
        try:
            _canonical_route_path(path, contract["path_prefix"])
        except ValueError as error:
            _manifest_problem(problems, where, str(error))
            return
        original = files.get(spec["file"])
        original_path = original.get("path") if isinstance(original, dict) else None
        if original_path is not None and path != "/protected" + original_path:
            _manifest_problem(
                problems, where, "internal path must name the original protected File"
            )


def _expect_problems(expect, case_id, assets, problems):
    where = f"cases[{case_id}].expect"
    spec = REQUIRED_CASE_SPEC.get(case_id)
    if spec is None:
        return
    if not _check_keys(
        expect,
        {"status", "body", "asset", "assets", "forbidden_assets", "content_type"},
        where,
        problems,
        required=("status", "body"),
    ):
        return
    status = expect["status"]
    if not isinstance(status, int) or isinstance(status, bool) or not 100 <= status <= 599:
        _manifest_problem(problems, where, "status must be an integer HTTP status")
    elif 300 <= status <= 399:
        _manifest_problem(
            problems,
            where,
            "redirects are not denial evidence; the client never follows them",
        )
    status_kind = spec["status_kind"]
    if status_kind == "allowed" and status != 200:
        _manifest_problem(
            problems, where, "allowed cases must expect exactly status 200"
        )
    elif status_kind == "auth-denied" and status not in (401, 403):
        _manifest_problem(
            problems,
            where,
            "authorization denials must expect 401 or 403, never 404/500 or another status",
        )
    elif status_kind == "internal-not-found" and status != 404:
        _manifest_problem(
            problems,
            where,
            "the internal-location contract must expect exactly 404",
        )

    body = expect["body"]
    if body != spec["body"]:
        _manifest_problem(
            problems, where, f"body mode must be {spec['body']!r} for this case"
        )
        return
    if spec["asset"] is None:
        if "asset" in expect:
            _manifest_problem(problems, where, "this case must not declare an asset")
    elif expect.get("asset") != spec["asset"]:
        _manifest_problem(
            problems, where, f"asset must be {spec['asset']!r} for this case"
        )
    expected_assets = list(spec["assets"])
    if expected_assets:
        _asset_list_problems(expect.get("assets"), assets, where, problems, "assets")
        if list(expect.get("assets") or ()) != expected_assets:
            _manifest_problem(
                problems,
                where,
                "allowed asset list does not match the required protected members",
            )
    elif "assets" in expect:
        _manifest_problem(problems, where, "this case must not declare allowed assets")
    expected_forbidden = list(spec["forbidden"])
    if expected_forbidden:
        _asset_list_problems(
            expect.get("forbidden_assets"),
            assets,
            where,
            problems,
            "forbidden_assets",
        )
        if list(expect.get("forbidden_assets") or ()) != expected_forbidden:
            _manifest_problem(
                problems,
                where,
                "forbidden asset list does not match the required protected assets",
            )
    elif "forbidden_assets" in expect:
        _manifest_problem(
            problems, where, "this case must not declare forbidden assets"
        )
    content_type = expect.get("content_type")
    if spec["content_type"] is None:
        if content_type is not None:
            _manifest_problem(
                problems, where, "this case must not constrain content_type"
            )
    elif content_type != spec["content_type"]:
        _manifest_problem(
            problems,
            where,
            f"content_type must be {spec['content_type']!r} for this case",
        )


def _asset_list_problems(values, assets, where, problems, label):
    if not isinstance(values, list) or not values:
        _manifest_problem(problems, where, f"{label} must be a non-empty list")
        return
    if len(set(values)) != len(values):
        _manifest_problem(problems, where, f"{label} contains duplicates")
    for value in values:
        if not isinstance(value, str) or value not in assets:
            _manifest_problem(problems, where, f"{label} names unknown asset {value!r}")


def _cases_problems(cases, assets, actors, routes, problems):
    if not isinstance(cases, list):
        _manifest_problem(problems, "cases", "must be a list")
        return
    seen = set()
    for index, case in enumerate(cases):
        where = f"cases[{index}]"
        if not _check_keys(
            case, {"id", "actor", "route", "expect"}, where, problems, required=("id", "actor", "route", "expect")
        ):
            continue
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id:
            _manifest_problem(problems, where, "id must be a non-empty string")
            continue
        if case_id in seen:
            _manifest_problem(problems, where, f"duplicate case id {case_id!r}")
        seen.add(case_id)
        if not isinstance(case["actor"], str) or case["actor"] not in actors:
            _manifest_problem(problems, where, "actor is not declared")
        if not isinstance(case["route"], str) or case["route"] not in routes:
            _manifest_problem(problems, where, "route is not declared")
        spec = REQUIRED_CASE_SPEC.get(case_id)
        if spec is not None:
            if case["actor"] != spec["actor"]:
                _manifest_problem(
                    problems,
                    where,
                    f"actor must be {spec['actor']!r} for this required case",
                )
            if case["route"] != spec["route"]:
                _manifest_problem(
                    problems,
                    where,
                    f"route must be {spec['route']!r} for this required case",
                )
        _expect_problems(case["expect"], case_id, assets, problems)
    missing = [case_id for case_id in REQUIRED_CASE_IDS if case_id not in seen]
    extra = [case_id for case_id in seen if case_id not in REQUIRED_CASE_IDS]
    for case_id in missing:
        _manifest_problem(problems, "cases", f"missing required case {case_id!r}")
    for case_id in extra:
        _manifest_problem(problems, "cases", f"unexpected case {case_id!r}")


def validate_manifest(manifest, require_approval=False):
    """Return a list of structural/safety problems; empty means usable."""
    problems = []
    if not isinstance(manifest, dict):
        return ["manifest must be an object"]
    if manifest.get("schema") != SCHEMA:
        _manifest_problem(problems, "manifest", f"schema must be {SCHEMA!r}")
    _check_keys(
        manifest,
        {"schema", "note", "target", "provisioner_attestation", "records", "assets", "files", "actors", "routes", "cases"},
        "manifest",
        problems,
        required=("schema", "target", "provisioner_attestation", "records", "assets", "files", "actors", "routes", "cases"),
    )

    target = manifest.get("target")
    if isinstance(target, dict):
        _target_problems(target, problems, require_approval)
    else:
        _manifest_problem(problems, "target", "must be an object")

    attestation = manifest.get("provisioner_attestation")
    if not isinstance(attestation, dict):
        _manifest_problem(problems, "provisioner_attestation", "must be an object")
    else:
        if not attestation.get("attested_by"):
            _manifest_problem(problems, "provisioner_attestation", "attested_by is required")
        if attestation.get("not_proof") is not True:
            _manifest_problem(
                problems,
                "provisioner_attestation",
                "must state not_proof: true; attestation is not evidence of native state",
            )
        if not attestation.get("statement"):
            _manifest_problem(problems, "provisioner_attestation", "statement is required")

    records = manifest.get("records")
    if isinstance(records, dict):
        _records_problems(records, problems)
    else:
        _manifest_problem(problems, "records", "must be an object")

    assets = manifest.get("assets")
    if isinstance(assets, dict):
        _assets_problems(assets, problems)
    else:
        _manifest_problem(problems, "assets", "must be an object")
        assets = {}

    files = manifest.get("files")
    if isinstance(files, dict):
        _files_problems(files, assets, problems)
    else:
        _manifest_problem(problems, "files", "must be an object")
        files = {}

    actors = manifest.get("actors")
    if isinstance(actors, dict):
        _actors_problems(actors, problems)
    else:
        _manifest_problem(problems, "actors", "must be an object")
        actors = {}

    routes = manifest.get("routes")
    if not isinstance(routes, dict):
        _manifest_problem(problems, "routes", "must be an object")
        routes = {}
    else:
        for name in REQUIRED_ROUTES:
            if name not in routes:
                _manifest_problem(problems, "routes", f"missing required route {name!r}")
        for name in routes:
            if name not in REQUIRED_ROUTE_SPEC:
                _manifest_problem(problems, "routes", f"unexpected route {name!r}")
        for name, route in routes.items():
            _route_problems(route, name, files, problems)

    _cases_problems(manifest.get("cases"), assets, actors, routes, problems)
    return problems


def _read_manifest(path):
    if path.name.lower().endswith(".sample.json"):
        raise ConfigError(
            "the schema sample is not a runtime manifest; provision a restricted copy"
        )
    try:
        info = path.lstat()
    except OSError as error:
        raise ConfigError(f"cannot stat {MANIFEST_ENV}: {error.strerror or error}") from None
    if stat.S_ISLNK(info.st_mode):
        raise ConfigError(f"{MANIFEST_ENV} must not be a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise ConfigError(f"{MANIFEST_ENV} must be a regular file")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(
            f"{MANIFEST_ENV} must be access-restricted (for example chmod 600)"
        )
    if hasattr(os, "geteuid") and info.st_uid != os.geteuid():
        raise ConfigError(f"{MANIFEST_ENV} must be owned by the current user")
    if info.st_size > MAX_MANIFEST_BYTES:
        raise ConfigError(f"{MANIFEST_ENV} is unexpectedly large")
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise ConfigError(f"cannot read {MANIFEST_ENV}: {error.strerror or error}") from None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ConfigError(f"{MANIFEST_ENV} is not valid UTF-8 JSON") from None


def load_config(environ=None):
    """Validate environment + manifest and return a transport-free config."""
    environ = dict(os.environ if environ is None else environ)
    missing = [
        name
        for name in (BASE_URL_ENV, MANIFEST_ENV)
        if not environ.get(name)
    ]
    if missing:
        raise ConfigError(
            "required environment is missing: " + ", ".join(missing)
        )
    base_url = environ[BASE_URL_ENV]
    manifest_path = pathlib.Path(environ[MANIFEST_ENV])
    manifest = _read_manifest(manifest_path)
    problems = validate_manifest(manifest, require_approval=True)
    if problems:
        raise ConfigError("manifest is not usable: " + "; ".join(problems))

    target = manifest["target"]
    if base_url != target["base_url"]:
        raise ConfigError(
            f"{BASE_URL_ENV} must equal the exact approved target in the manifest"
        )

    for name, actor in manifest["actors"].items():
        if actor["kind"] == "guest":
            continue
        env_name = actor["session_env"]
        value = environ.get(env_name)
        if not value:
            raise ConfigError(f"{env_name} is required for actor {name!r}")
        if not _valid_header_value(value):
            raise ConfigError(
                f"{env_name} contains characters that are invalid in a header value"
            )
    return Config(base_url, manifest, manifest_path, environ)


def _case(manifest, case_id):
    for case in manifest["cases"]:
        if case["id"] == case_id:
            return case
    raise ConfigError(f"case {case_id!r} is not declared")


def _route_url(config, route):
    kind = route["kind"]
    contract = ROUTE_CONTRACT[kind]
    files = config.manifest["files"]
    if kind == "direct_private":
        try:
            path = _canonical_route_path(
                files[route["file"]]["path"], "/private/files/"
            )
        except ValueError:
            raise ConfigError(
                "recorded direct-private path is not canonical"
            ) from None
        query = dict(route.get("query") or {})
    elif kind == "native_download":
        path = contract["path"]
        query = dict(route["query"])
        try:
            _canonical_route_path(query["file_url"], "/private/files/")
        except (KeyError, ValueError):
            raise ConfigError(
                "recorded download file_url is not canonical"
            ) from None
    elif kind == "native_zip":
        path = contract["path"]
        query = {
            "files": json.dumps(
                [files[file_key]["fid"] for file_key in route["files"]],
                separators=(",", ":"),
            )
        }
    elif kind == "internal_protected":
        try:
            path = _canonical_route_path(route["path"], "/protected/")
        except ValueError:
            raise ConfigError(
                "recorded internal path is not canonical"
            ) from None
        query = {}
    else:  # validated already
        raise ConfigError(f"unsupported route kind {kind!r}")
    url = config.base_url + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    if len(url) > MAX_URL_LENGTH:
        raise ConfigError("recorded route URL exceeds the conservative length cap")
    return url


def build_request(config, case_id):
    """Build exactly one read-only GET request for a declared case."""
    case = _case(config.manifest, case_id)
    route = config.manifest["routes"][case["route"]]
    url = _route_url(config, route)
    headers = {
        "Accept": "*/*",
        "Connection": "close",
        "User-Agent": "clinic-photos-http-acceptance/1.0",
    }
    cookie = config.session_header(case["actor"])
    if cookie is not None:
        headers["Cookie"] = cookie
    return urllib.request.Request(url, headers=headers, method="GET")


def read_capped(response, max_bytes=None):
    """Read at most the cap plus one byte; reject over-read and truncation."""
    limit = MAX_BODY_BYTES if max_bytes is None else max_bytes
    data = response.read(limit + 1)
    if len(data) > limit:
        raise ProbeError("response body exceeds the conservative read cap")
    declared = _header_value(getattr(response, "headers", None), "Content-Length")
    if declared is not None:
        try:
            expected = int(declared)
        except (TypeError, ValueError):
            raise ProbeError("response Content-Length is not an integer") from None
        if expected < 0:
            raise ProbeError("response Content-Length is negative")
        if expected > limit:
            raise ProbeError("declared response body exceeds the read cap")
        if len(data) != expected:
            raise ProbeError("response body is truncated or longer than declared")
    return data


def _close(response):
    closer = getattr(response, "close", None)
    if closer is not None:
        closer()


def execute_case(config, case_id, opener=None):
    """Send one declared read-only GET and verify it; raise ProbeError on mismatch."""
    case = _case(config.manifest, case_id)
    request = build_request(config, case_id)
    transport = opener if opener is not None else build_opener()
    response = None
    try:
        response = transport.open(request, timeout=config.timeout)
    except urllib.error.HTTPError as error:
        response = error
    except urllib.error.URLError as error:
        raise ProbeError(
            f"{case_id}: request failed ({type(error).__name__})"
        ) from None
    except http.client.HTTPException as error:
        # A parser fault (for example BadStatusLine) carries unparsed server text,
        # which may reflect the request Cookie. Report only its type, never the
        # message, and suppress the chained exception so no traceback prints it.
        raise ProbeError(
            f"{case_id}: request failed ({type(error).__name__})"
        ) from None
    except OSError as error:
        raise ProbeError(
            f"{case_id}: request failed ({type(error).__name__})"
        ) from None
    try:
        status = response.getcode()
        headers = getattr(response, "headers", None)
        body = read_capped(response, max_bytes=MAX_BODY_BYTES)
    except ProbeError:
        raise
    except http.client.HTTPException as error:
        raise ProbeError(
            f"{case_id}: response read failed ({type(error).__name__})"
        ) from None
    except OSError as error:
        raise ProbeError(
            f"{case_id}: response read failed ({type(error).__name__})"
        ) from None
    finally:
        _close(response)
    return verify_response(
        config.manifest,
        case,
        HttpResult(status=status, headers=headers, body=body),
        manifest_dir=config.manifest_path.parent,
    )


def _asset_bytes(asset, manifest_dir):
    fixture = asset.get("fixture")
    if not fixture:
        return None
    if manifest_dir is None:
        raise ProbeError("a fixture reference requires the manifest directory")
    root = pathlib.Path(manifest_dir).resolve()
    path = (root / fixture).resolve()
    if root not in path.parents:
        raise ProbeError("fixture path escapes the manifest directory")
    try:
        info = path.lstat()
    except OSError:
        raise ProbeError("declared expected-bytes fixture is missing") from None
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise ProbeError("expected-bytes fixture must be a restricted regular file")
    if info.st_size > MAX_BODY_BYTES:
        raise ProbeError("expected-bytes fixture exceeds the read cap")
    data = path.read_bytes()
    if len(data) != asset["size"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
        raise ProbeError("expected-bytes fixture does not match its recorded hash/size")
    return data


def _asset_problems(asset, body, manifest_dir, label):
    problems = []
    if len(body) != asset["size"]:
        problems.append(f"{label}: size {len(body)} != expected {asset['size']}")
    digest = hashlib.sha256(body).hexdigest()
    if digest != asset["sha256"]:
        problems.append(f"{label}: SHA-256 does not match expected bytes")
    expected = _asset_bytes(asset, manifest_dir)
    if expected is not None and body != expected:
        problems.append(f"{label}: bytes differ from the exact expected fixture")
    return problems


def _cache_trio_problems(headers):
    expected = (
        ("Cache-Control", CACHE_CONTROL),
        ("Pragma", PRAGMA),
        ("Expires", EXPIRES),
    )
    problems = []
    for name, value in expected:
        values = _header_values(headers, name)
        if len(values) != 1:
            problems.append(f"{name} must appear exactly once with {value!r}")
        elif values[0] != value:
            problems.append(f"{name} must be exactly {value!r}")
    return problems


def _content_type_problems(headers, expect):
    expected = expect.get("content_type")
    if expected is None:
        return []
    values = _header_values(headers, "Content-Type")
    if len(values) != 1:
        return ["Content-Type must appear exactly once"]
    actual = values[0]
    if not str(actual).split(";", 1)[0].strip().lower() == expected.lower():
        # Never echo an untrusted header value: a server may reflect Cookie/session
        # data into Content-Type, and unittest would print it in the traceback.
        return ["Content-Type does not match the recorded value"]
    return []


def _forbidden_needles(manifest, assets, manifest_dir=None):
    needles = set()
    for asset_name in assets:
        asset = manifest["assets"][asset_name]
        for fragment in asset.get("body_fragments") or []:
            needles.add(fragment.encode("utf-8"))
        needles.add(asset["file_name"].encode("utf-8"))
        needles.add(asset["sha256"].encode("ascii"))
        fixture = _asset_bytes(asset, manifest_dir) if asset.get("fixture") else None
        if fixture:
            needles.add(fixture)
    for entry in manifest["files"].values():
        if entry["asset"] in assets:
            needles.add(entry["fid"].encode("utf-8"))
            needles.add(entry["path"].encode("utf-8"))
    return [needle for needle in needles if needle]


def _is_zip_payload(body):
    if not body:
        return False
    try:
        return zipfile.is_zipfile(io.BytesIO(body))
    except (OSError, ValueError):
        return False


def _forbidden_header_problems(manifest, forbidden, headers, manifest_dir):
    """Protected bytes or identifiers must never appear in any response header."""
    needles = _forbidden_needles(manifest, forbidden, manifest_dir)
    header_text = " ".join(
        f"{name}: {value}" for name, value in _header_items(headers)
    ).encode("utf-8", "replace")
    for needle in needles:
        if needle in header_text:
            return ["response headers contain a protected identifier or bytes"]
    return []


def _denied_problems(manifest, expect, result, manifest_dir):
    problems = []
    forbidden = expect["forbidden_assets"]
    needles = _forbidden_needles(manifest, forbidden, manifest_dir)
    forbidden_hashes = {
        manifest["assets"][name]["sha256"] for name in forbidden
    }
    if hashlib.sha256(result.body).hexdigest() in forbidden_hashes:
        problems.append("denial body equals protected expected bytes")
    content_type = _header_value(result.headers, "Content-Type") or ""
    zip_declared = "zip" in str(content_type).lower()
    if _is_zip_payload(result.body) or zip_declared or b"PK\x03\x04" in result.body:
        # zipfile.is_zipfile scans for the central directory and accepts prefixed
        # archives; never rely on a leading local-header signature or a raw scan.
        problems.extend(
            _zip_forbidden_problems(
                result.body, manifest, forbidden, manifest_dir, scan_raw=False
            )
        )
    # A recognized archive does not exempt the rest of the response: a preamble,
    # trailer or archive comment carrying protected bytes still leaks them.
    for needle in needles:
        if needle in result.body:
            problems.append("denial body contains a protected fragment")
            break
    problems.extend(
        _forbidden_header_problems(manifest, forbidden, result.headers, manifest_dir)
    )
    if _header_value(result.headers, "X-Accel-Redirect") is not None:
        problems.append("a denial must not hand back an internal protected redirect")
    return problems


def _zip_members(body):
    try:
        archive = zipfile.ZipFile(io.BytesIO(body))
    except (zipfile.BadZipFile, OSError):
        raise ProbeError("expected ZIP response is not a readable archive") from None
    try:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_MEMBERS:
            raise ProbeError("ZIP has more members than the conservative cap")
        names = []
        members = {}
        total = 0
        for info in infos:
            name = info.filename
            if name.startswith("/") or "\\" in name:
                raise ProbeError("ZIP member name is absolute or ambiguous")
            parts = pathlib.PurePosixPath(name).parts
            if not parts or any(part in ("", ".", "..") for part in parts):
                raise ProbeError("ZIP member name is unsafe")
            if info.flag_bits & 0x1:
                raise ProbeError("encrypted ZIP members are not accepted")
            if name in members:
                raise ProbeError("ZIP has duplicate member names")
            total += info.file_size
            if total > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ProbeError("ZIP uncompressed size exceeds the conservative cap")
            with archive.open(info) as handle:
                data = handle.read(MAX_ZIP_UNCOMPRESSED_BYTES + 1)
            if len(data) != info.file_size:
                raise ProbeError("ZIP member is truncated or larger than declared")
            names.append(name)
            members[name] = data
        return names, members, archive.comment
    except (zipfile.BadZipFile, OSError, EOFError):
        raise ProbeError("ZIP archive could not be inspected safely") from None
    finally:
        archive.close()


def _zip_forbidden_problems(body, manifest, forbidden, manifest_dir, scan_raw=True):
    try:
        _, members, comment = _zip_members(body)
    except ProbeError as error:
        return [str(error)]
    problems = []
    forbidden_names = {manifest["assets"][name]["file_name"] for name in forbidden}
    forbidden_hashes = {manifest["assets"][name]["sha256"] for name in forbidden}
    needles = _forbidden_needles(manifest, forbidden, manifest_dir)
    for name, data in members.items():
        if name in forbidden_names:
            problems.append("ZIP contains a protected member name")
        if hashlib.sha256(data).hexdigest() in forbidden_hashes:
            problems.append("ZIP contains protected bytes under another name")
        if any(needle in data for needle in needles):
            problems.append("ZIP member contains a protected fragment")
        if any(needle in name.encode("utf-8", "replace") for needle in needles):
            problems.append("ZIP member name contains protected identifiers")
    if comment and any(needle in comment for needle in needles):
        problems.append("ZIP archive comment contains a protected fragment")
    if scan_raw and any(needle in body for needle in needles):
        # Archive-level leakage outside the member stream: a preamble, trailer or
        # stored comment carrying protected bytes instead of a member payload.
        problems.append("ZIP archive-level bytes contain a protected fragment")
    return problems


def _zip_allowed_problems(body, manifest, expect, manifest_dir):
    names, members, _comment = _zip_members(body)
    expected_names = [manifest["assets"][name]["file_name"] for name in expect["assets"]]
    problems = []
    if set(names) != set(expected_names):
        problems.append("ZIP member set does not match the expected protected members")
    for asset_name in expect["assets"]:
        asset = manifest["assets"][asset_name]
        data = members.get(asset["file_name"])
        if data is None:
            problems.append(f"expected ZIP member {asset['file_name']!r} is missing")
            continue
        problems.extend(_asset_problems(asset, data, manifest_dir, asset_name))
    return names, problems


def _zip_mixed_problems(body, manifest, expect, headers, manifest_dir):
    names, members, _comment = _zip_members(body)
    allowed_names = [manifest["assets"][name]["file_name"] for name in expect["assets"]]
    problems = []
    if set(names) != set(allowed_names):
        problems.append("mixed ZIP must contain exactly the allowed members")
    for asset_name in expect["assets"]:
        asset = manifest["assets"][asset_name]
        data = members.get(asset["file_name"])
        if data is None:
            problems.append(f"expected allowed member {asset['file_name']!r} is missing")
        else:
            problems.extend(_asset_problems(asset, data, manifest_dir, asset_name))
    problems.extend(
        _zip_forbidden_problems(body, manifest, expect["forbidden_assets"], manifest_dir)
    )
    problems.extend(
        _forbidden_header_problems(
            manifest, expect["forbidden_assets"], headers, manifest_dir
        )
    )
    return names, problems


def verify_response(manifest, case, result, manifest_dir=None):
    """Verify one measured response against its declared case; raise ProbeError."""
    expect = case["expect"]
    problems = []
    if result.status != expect["status"]:
        problems.append(
            f"status {result.status} != expected {expect['status']}"
        )
    problems.extend(_cache_trio_problems(result.headers))
    problems.extend(_content_type_problems(result.headers, expect))
    zip_members = None
    try:
        body_kind = expect["body"]
        if body_kind == "allowed_bytes":
            asset = manifest["assets"][expect["asset"]]
            problems.extend(_asset_problems(asset, result.body, manifest_dir, expect["asset"]))
        elif body_kind == "denied":
            problems.extend(_denied_problems(manifest, expect, result, manifest_dir))
        elif body_kind == "zip_allowed":
            zip_members, zip_problems = _zip_allowed_problems(
                result.body, manifest, expect, manifest_dir
            )
            problems.extend(zip_problems)
        elif body_kind == "zip_mixed":
            zip_members, zip_problems = _zip_mixed_problems(
                result.body, manifest, expect, result.headers, manifest_dir
            )
            problems.extend(zip_problems)
        else:
            problems.append("unknown body expectation")
    except ProbeError as error:
        problems.append(str(error))
    if problems:
        raise ProbeError(f"{case['id']}: " + "; ".join(problems))
    return {
        "case": case["id"],
        "actor": case["actor"],
        "route": case["route"],
        "status": result.status,
        "cache_control": _header_value(result.headers, "Cache-Control"),
        "pragma": _header_value(result.headers, "Pragma"),
        "expires": _header_value(result.headers, "Expires"),
        "body_sha256": hashlib.sha256(result.body).hexdigest(),
        "body_size": len(result.body),
        "body_kind": expect["body"],
        "zip_members": len(zip_members) if zip_members is not None else None,
    }


def main(argv=None, environ=None):
    """Configuration check only; this helper never sends a request."""
    parser = argparse.ArgumentParser(
        description="Validate the protected-photo HTTP acceptance configuration."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="required: validate prerequisites and exit without sending a request",
    )
    arguments = parser.parse_args(argv)
    if not arguments.check:
        print(
            "this helper only validates configuration and never sends a request;"
            " pass --check"
        )
        return 2
    try:
        config = load_config(environ)
    except ConfigError as error:
        print("configuration error: " + redact(str(error), environ))
        return 3
    print(
        "configuration ok: manifest="
        + str(config.manifest_path)
        + " target="
        + config.base_url
        + " cases="
        + str(len(config.manifest["cases"]))
    )
    print("no request was sent; actual read-only cases require the authorized run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
