"""Explicit, site-owned configuration gate.

Every value is read from the site's own ``site_config.json`` through a caller
supplied getter. Nothing is defaulted, inferred or repaired: a missing,
malformed or ambiguous value makes the whole feature unavailable
(``load_settings`` returns ``None``) so the endpoints fail closed.

Keys
----
``clinic_ui_enabled``
    Truthy flag. Only ``True``/``1`` or ``"1"``/``"true"``/``"yes"`` enable it.
``clinic_ui_synthetic_site``
    The one site name this feature is allowed to run on. It must equal the
    current site exactly.
``clinic_ui_epoch_secret``
    At least 32 characters of server-owned secret entropy. Used only to derive
    the opaque session epoch; never returned, logged or stored elsewhere.
``clinic_ui_role_map``
    Mapping of clinic role code (``DOC``/``REC``/``NUR``) to a non-empty list of
    native Frappe role names. Codes outside the first slice, duplicate role
    names and a role name shared by two codes are refused.
``clinic_ui_synthetic_patients``
    Server-owned allowlist of ``{"workspace_patient_id", "native_patient_id"}``
    objects. Order is preserved. Duplicates and extra keys are refused.
"""

from dataclasses import dataclass
from typing import Any

from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.core.text import is_valid_identifier, normalize_identifier

ENABLED_KEY = "clinic_ui_enabled"
SITE_KEY = "clinic_ui_synthetic_site"
EPOCH_SECRET_KEY = "clinic_ui_epoch_secret"
ROLE_MAP_KEY = "clinic_ui_role_map"
ALLOWLIST_KEY = "clinic_ui_synthetic_patients"

MIN_EPOCH_SECRET_LENGTH = 32
MAX_ROLE_NAME_LENGTH = 140
MAX_ALLOWLIST_ENTRIES = 1000

TRUTHY = frozenset({"1", "true", "yes"})

ALLOWLIST_KEYS = frozenset({"workspace_patient_id", "native_patient_id"})


@dataclass(frozen=True)
class Settings:
    role_map: tuple
    allowlist: tuple
    epoch_secret: bytes

    def as_role_map(self):
        return {code: tuple(roles) for code, roles in self.role_map}


def _enabled(raw: Any) -> bool:
    if raw is True:
        return True
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw == 1
    if isinstance(raw, str):
        return raw.strip().lower() in TRUTHY
    return False


def _role_map(raw: Any):
    if not isinstance(raw, dict) or not raw:
        return None

    parsed = {}
    seen_role_names = set()
    for code, roles in raw.items():
        if code not in SUPPORTED_ROLE_CODES:
            return None
        if not isinstance(roles, (list, tuple)) or not roles:
            return None
        names = []
        for role in roles:
            if not isinstance(role, str):
                return None
            name = role.strip()
            if not is_valid_identifier(name) or len(name) > MAX_ROLE_NAME_LENGTH:
                return None
            if name in names or name in seen_role_names:
                return None
            names.append(name)
            seen_role_names.add(name)
        parsed[code] = tuple(names)

    return tuple(sorted(parsed.items()))


def _allowlist(raw: Any):
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) > MAX_ALLOWLIST_ENTRIES:
        return None

    entries = []
    workspace_ids = set()
    native_ids = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != ALLOWLIST_KEYS:
            return None
        workspace_id = normalize_identifier(entry["workspace_patient_id"])
        native_id = normalize_identifier(entry["native_patient_id"])
        if not is_valid_identifier(workspace_id) or not is_valid_identifier(native_id):
            return None
        if workspace_id in workspace_ids or native_id in native_ids:
            return None
        workspace_ids.add(workspace_id)
        native_ids.add(native_id)
        entries.append((workspace_id, native_id))

    return tuple(entries)


def load_settings(getter, site):
    """Return validated settings, or ``None`` when the feature is unavailable."""

    if not callable(getter):
        return None
    if not isinstance(site, str) or not site.strip():
        return None

    if not _enabled(getter(ENABLED_KEY)):
        return None

    configured_site = getter(SITE_KEY)
    if not isinstance(configured_site, str) or not configured_site.strip():
        return None
    if configured_site.strip() != site:
        return None

    secret = getter(EPOCH_SECRET_KEY)
    if not isinstance(secret, str) or len(secret) < MIN_EPOCH_SECRET_LENGTH:
        return None

    role_map = _role_map(getter(ROLE_MAP_KEY))
    if role_map is None:
        return None

    allowlist = _allowlist(getter(ALLOWLIST_KEY))
    if allowlist is None:
        return None

    return Settings(
        role_map=role_map,
        allowlist=allowlist,
        epoch_secret=secret.encode("utf-8"),
    )
