"""Resolve the built SPA shell assets from the Vite build manifest.

The native build (``pnpm --filter @clinic/app build:native``) writes a content
hashed bundle into ``<app>/public/ui``. The shell page may not hardcode a hash
and must not guess one, so the Vite manifest is the single source of truth for
which files the shell references.

A manifest is build output, so shape alone does not prove a deploy is complete.
Every referenced file must also:

* resolve inside the build root after following symlinks (a link out of the
  build root is refused);
* be a regular file, not a directory or a dangling link.

This module is pure apart from those reads. It imports no Frappe code, so the
shell's asset mapping is testable without a site.

Failure is always closed (``AssetManifestError``): a missing, unreadable,
malformed or non-UTF-8 manifest, or any referenced file that is not deployed
under the build root, yields no assets rather than a partial shell.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

#: Segments (under the app package) that hold the native build output. Frappe
#: serves ``<app>/public`` at ``/assets/<app>``, so this becomes
#: ``/assets/clinic_ui/ui/...``.
SHELL_STATIC_SEGMENTS = ("public", "ui")

#: Manifest location, relative to the build root. Vite emits this file only when
#: ``build.manifest`` is enabled.
SHELL_MANIFEST_SEGMENTS = (".vite", "manifest.json")

#: The one manifest entry the shell needs: the built ``index.html``.
SHELL_ENTRY = "index.html"

#: Must match ``NATIVE_ASSET_BASE`` in ``app/vite.native.ts``.
ASSET_URL_PREFIX = "/assets/clinic_ui/ui/"

# A manifest value may only name a plain relative file path ending in the
# expected extension. No scheme, query, fragment, backslash, absolute path or
# ``..`` segment can survive this pattern.
_SAFE_RELATIVE_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.(?:js|css)$")


class AssetManifestError(Exception):
    """The built shell assets cannot be resolved from this build root."""


@dataclass(frozen=True)
class ShellAssets:
    """Absolute URLs the shell template must reference, in load order."""

    scripts: tuple[str, ...]
    styles: tuple[str, ...]


def shell_assets(build_root: str | Path) -> ShellAssets:
    """Return the shell scripts and styles deployed under ``build_root``."""
    root = Path(build_root)
    manifest = _load_manifest(root.joinpath(*SHELL_MANIFEST_SEGMENTS))
    entry = manifest.get(SHELL_ENTRY)
    if not isinstance(entry, dict):
        raise AssetManifestError("manifest has no shell entry object")

    root_real = _real_path(root)
    script = _deployed_file(root, root_real, entry.get("file"), "entry file", ".js")

    css = entry.get("css", [])
    if not isinstance(css, list):
        raise AssetManifestError("entry css is not a list")

    styles: list[str] = []
    for value in css:
        style = _deployed_file(root, root_real, value, "entry style", ".css")
        if style not in styles:
            styles.append(style)

    return ShellAssets(
        scripts=(ASSET_URL_PREFIX + script,),
        styles=tuple(ASSET_URL_PREFIX + style for style in styles),
    )


def _load_manifest(path: Path) -> dict:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise AssetManifestError("shell manifest is unreadable") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AssetManifestError("shell manifest is not valid UTF-8") from exc
    try:
        manifest = json.loads(text)
    except ValueError as exc:
        raise AssetManifestError("shell manifest is not valid JSON") from exc
    if not isinstance(manifest, dict):
        raise AssetManifestError("shell manifest is not an object")
    return manifest


def _assert_safe(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SAFE_RELATIVE_PATH.match(value):
        # The value is deliberately not echoed: a malformed manifest must not
        # leak build or filesystem detail into a response or a log line.
        raise AssetManifestError(f"{label} is not a safe relative asset path")
    if any(segment in ("", ".", "..") for segment in value.split("/")):
        raise AssetManifestError(f"{label} is not a safe relative asset path")
    return value


def _real_path(path: Path) -> str:
    try:
        return os.path.realpath(path)
    except OSError as exc:  # pragma: no cover - realpath rarely fails
        raise AssetManifestError("build root cannot be resolved") from exc


def _deployed_file(
    root: Path, root_real: str, value: object, label: str, suffix: str
) -> str:
    """Return ``value`` only if it is a regular file deployed under ``root``."""
    relative = _assert_safe(value, label)
    if not relative.endswith(suffix):
        raise AssetManifestError(f"{label} is not a {suffix} file")

    try:
        resolved = os.path.realpath(root / relative)
        contained = os.path.commonpath((root_real, resolved)) == root_real
        is_file = os.path.isfile(resolved)
    except (OSError, ValueError) as exc:
        raise AssetManifestError(f"{label} cannot be resolved") from exc

    if not contained or not is_file:
        raise AssetManifestError(f"{label} is not deployed under the build root")
    return relative
