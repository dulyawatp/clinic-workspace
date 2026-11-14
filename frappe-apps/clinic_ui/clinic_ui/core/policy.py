"""Framework-independent session and role policy.

The policy can only narrow access. It never grants authority that the native
Frappe permission system denied, and it never reads a record itself: the
caller supplies the already-resolved session facts.

Session identities that must never act as ordinary clinic principals are
``Guest``, ``Administrator`` and the empty identity. Frappe's whitelist also
refuses ``Guest`` before a non-guest method runs; the check here is defence in
depth so the policy is safe on its own.
"""

from dataclasses import dataclass

from clinic_ui.core.text import is_valid_text

# Clinic role codes supported by the first slice. A configured role map may only
# use these codes; anything else is refused as an invalid configuration.
SUPPORTED_ROLE_CODES = frozenset({"DOC", "REC", "NUR"})

# The synthetic directory slice is readable by these roles only.
DIRECTORY_ROLE_CODES = frozenset({"DOC", "REC", "NUR"})

NON_CLINICAL_IDENTITIES = frozenset({"", "Guest", "Administrator"})

GUEST_SID = "Guest"


@dataclass(frozen=True)
class SessionView:
    """Facts about the current authenticated request, resolved by the adapter."""

    sid: str
    user: str
    full_name: str
    enabled: bool
    roles: tuple


def _is_denied_identity(user):
    if not isinstance(user, str):
        return True
    stripped = user.strip()
    if not stripped:
        return True
    if stripped in NON_CLINICAL_IDENTITIES:
        return True
    return stripped.lower() == "guest"


def is_anonymous(view):
    """True when the request carries no live user identity.

    Anonymous/expired is a sign-in state, not an authorization decision: a
    missing or guest session id, a blank user and ``Guest`` are anonymous. An
    ``Administrator`` or a disabled user is *authenticated* and is therefore an
    authorization denial, never an anonymous response.
    """

    if view is None:
        return True
    if not is_valid_text(view.sid) or view.sid.strip() == GUEST_SID:
        return True
    if not isinstance(view.user, str) or not view.user.strip():
        return True
    return view.user.strip().lower() == "guest"


def is_valid_session(view):
    """True only for a live, enabled, non-guest, non-administrator session."""

    if view is None:
        return False
    if not is_valid_text(view.sid) or view.sid.strip() == GUEST_SID:
        return False
    if _is_denied_identity(view.user):
        return False
    return view.enabled is True


def resolve_role(view, role_map):
    """Resolve exactly one clinic role code, or ``None``.

    ``None`` covers an invalid session, no mapped role and an ambiguous mapping
    (two clinic codes sharing a native role, or a user carrying roles of two
    clinic codes). Ambiguity is never resolved by precedence.
    """

    if not is_valid_session(view):
        return None
    if not isinstance(role_map, dict) or not role_map:
        return None

    native_roles = set(role for role in view.roles if isinstance(role, str))
    matches = [
        code
        for code, mapped in role_map.items()
        if code in SUPPORTED_ROLE_CODES and native_roles.intersection(mapped)
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def authorize(view, role_map, allowed_codes):
    """Return the single role code when it is also allowed for this surface."""

    role = resolve_role(view, role_map)
    if role is None or role not in allowed_codes:
        return None
    return role
