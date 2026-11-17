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

# Clinic role codes supported by the full write-contract wave. A configured
# role map may only use these codes; anything else is refused as an invalid
# configuration. The write families are gated by the frozensets below; the code
# itself never grants native authority.
SUPPORTED_ROLE_CODES = frozenset({"DOC", "REC", "NUR", "PHA", "CAS", "MGR", "ADM"})

# The synthetic directory slice is readable by these roles only.
DIRECTORY_ROLE_CODES = frozenset({"DOC", "REC", "NUR"})

# The patient detail read admits the same clinic role codes as the directory.
# This gate is not a native grant: REC passes it and is still refused by the
# Patient read permission check, so the native authority stays authoritative.
PATIENT_VIEW_ROLE_CODES = frozenset({"DOC", "REC", "NUR"})

# The read-only synthetic appointment list belongs to the two roles that own
# the appointment surface. The role code gate is not a native grant: the
# current user's document and field-level Patient Appointment read checks still
# run before any field is fetched.
APPOINTMENT_ROLE_CODES = frozenset({"DOC", "REC"})

# Booking, rescheduling and check-in belong to the same two appointment roles.
# The code gate is not a grant: every request still needs the exact native
# Patient Appointment create/write check, the current user's Patient and linked
# DocType reads, and the approved target checks.
APPOINTMENT_WRITE_ROLE_CODES = frozenset({"DOC", "REC"})

# Only these roles may resolve another clinician's created alias for a read, and
# only through a verified committed create receipt plus the current user's
# native Patient read permission. The creator's own actor-bound resolution and
# every write resolution are unchanged.
CROSS_ACTOR_READ_ROLE_CODES = frozenset({"DOC", "NUR"})

# Native create/edit admits only DOC and NUR. This code gate is shared by the
# bootstrap capability and the write/ack endpoints so they cannot drift. The
# role code is not a grant: the exact native Patient create/write check still
# runs before any operation-fence access. REC never reaches that check.
PATIENT_WRITE_ROLE_CODES = frozenset({"DOC", "NUR"})

# Per-family code gates for the full write-contract wave. These narrow; the
# exact native DocType permission check still runs on every write and stays
# authoritative. Qualifier tokens in the requirements fold to these base codes,
# with the qualifier expressed as an extra condition, never a role code.
APPOINTMENT_CANCEL_ROLE_CODES = frozenset({"DOC", "REC"})
APPOINTMENT_NOSHOW_ROLE_CODES = frozenset({"DOC", "REC"})
ENCOUNTER_ROLE_CODES = frozenset({"DOC", "NUR"})
ENCOUNTER_COMPLETE_ROLE_CODES = frozenset({"DOC"})
ENCOUNTER_AMEND_ROLE_CODES = frozenset({"DOC"})
PROCEDURE_ROLE_CODES = frozenset({"DOC"})
STOCK_ROLE_CODES = frozenset({"PHA"})
ADJUST_ROLE_CODES = frozenset({"ADM"})
PO_ROLE_CODES = frozenset({"PHA", "MGR"})
INVOICE_ROLE_CODES = frozenset({"CAS"})
INVOICE_VOID_ROLE_CODES = frozenset({"MGR"})
PAYMENT_ROLE_CODES = frozenset({"CAS"})
ADMIN_ROLE_CODES = frozenset({"ADM"})

NON_CLINICAL_IDENTITIES = frozenset({"", "Guest", "Administrator"})

# Pinned Frappe v16 ``frappe.permissions.AUTOMATIC_ROLES``. ``get_roles`` adds
# them to every answer without any ``Has Role`` row, so a table read can never
# establish one of them as a caller's authoritative role.
AUTOMATIC_FRAMEWORK_ROLES = ("Guest", "All", "Desk User", "Administrator")

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


def _matching_codes(native_roles, role_map):
    """Clinic codes whose mapped native role is present, in map order."""

    return [
        code
        for code, mapped in role_map.items()
        if code in SUPPORTED_ROLE_CODES and native_roles.intersection(mapped)
    ]


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
    matches = _matching_codes(native_roles, role_map)
    if len(matches) != 1:
        return None
    return matches[0]


def fresh_role_problem(fresh_roles, role_map, gated_role):
    """Return why a fresh DB role read does not confirm ``gated_role``.

    ``None`` means the read agrees with the role the outer gate already
    authorised. Every other outcome is a refusal: this check is deny-only and
    never re-resolves a different authority for the request.

    ``fresh_roles`` is the tuple a direct ``Has Role`` read returned, or ``None``
    when the read failed. A role whose native mapping is one of
    :data:`AUTOMATIC_FRAMEWORK_ROLES` cannot be established from the table at
    all, so it refuses too.
    """

    if not isinstance(role_map, dict) or not role_map:
        return "no role map"
    if not isinstance(gated_role, str) or not gated_role:
        return "no gated role"
    mapped = role_map.get(gated_role)
    if not isinstance(mapped, (tuple, list, frozenset, set)):
        return "gated role is not mapped"
    mapped = tuple(role for role in mapped if isinstance(role, str) and role)
    if not mapped:
        return "gated role has no native role"
    if any(role in AUTOMATIC_FRAMEWORK_ROLES for role in mapped):
        return "gated role maps to an automatic framework role"
    if not isinstance(fresh_roles, tuple):
        return "fresh roles unreadable"

    fresh = set(role for role in fresh_roles if isinstance(role, str))
    matches = _matching_codes(fresh, role_map)
    if len(matches) != 1:
        return "fresh roles do not resolve to one clinic role"
    if matches[0] != gated_role:
        return "fresh role disagrees with the gated role"
    return None


def authorize(view, role_map, allowed_codes):
    """Return the single role code when it is also allowed for this surface."""

    role = resolve_role(view, role_map)
    if role is None or role not in allowed_codes:
        return None
    return role
