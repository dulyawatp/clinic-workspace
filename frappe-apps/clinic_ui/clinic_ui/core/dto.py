"""Wire DTOs for the read-only clinic endpoints.

Frappe wraps the returned mapping as ``{"message": ...}``. The shapes here are
mirrored from ``app/src/domain/session.ts`` and
``app/src/features/patients/live-directory.ts``; no client-only field is added
and no server secret, session identifier or record field is included.

Bootstrap::

    {
      "mode": "live",
      "sessionEpoch": "<64 hex chars>",
      "user": {"id": "...", "displayName": "...", "roles": ["DOC"|"REC"|"NUR"]},
      "capabilities": {"<every capability>": "read_only" | "enabled"},
      "csrfToken": "<frappe session csrf token>"
    }

Directory::

    {"items": [
      {"workspace_patient_id": "...", "display_name": "..."}
    ]}

Write success::

    {"status": "created" | "updated",
     "ackToken": "<64 lowercase hex>",
     "patient": {"workspace_patient_id": "...", "display_name": "...",
                 "first_name": "...", "last_name": ..., "sex": ...,
                 "dob": ..., "status": ..., "modified": "..."}},

Acknowledgement success::

    {"acknowledged": true}
"""

import re
from datetime import date

from clinic_ui.core.capabilities import (
    read_only_capabilities,
    validate_capabilities,
)
from clinic_ui.core.patient_write import is_valid_ack_token, parse_modified
from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.core.text import MAX_TEXT_LENGTH, is_valid_text

MAX_EPOCH_LENGTH = 128
MAX_DIRECTORY_ITEMS = 1000
MAX_APPOINTMENT_ITEMS = 1000
MAX_CSRF_TOKEN_LENGTH = MAX_TEXT_LENGTH

# Appointment rows carry exactly the five fields the existing live contract
# already publishes. The appointment id is the server-owned workspace
# reference, never the native Patient Appointment docname.
APPOINTMENT_ITEM_KEYS = frozenset(
    {
        "workspace_patient_id",
        "appointment_id",
        "appointment_date",
        "appointment_time",
        "status",
    }
)
APPOINTMENT_DETAIL_KEYS = APPOINTMENT_ITEM_KEYS | {"modified"}
APPOINTMENT_READ_KEYS = APPOINTMENT_DETAIL_KEYS | {
    "can_reschedule",
    "can_checkin",
}
APPOINTMENT_WRITE_STATUSES = (
    "booked",
    "rescheduled",
    "checked_in",
    "cancelled",
    "no_show",
)
# Encounter write results carry the workspace encounter alias, never a native
# Patient Encounter docname. The nested detail is exactly these keys.
ENCOUNTER_DETAIL_KEYS = frozenset(
    {
        "encounter_id",
        "workspace_patient_id",
        "state",
        "status",
        "modified",
    }
)
ENCOUNTER_WRITE_STATUSES = ("draft_saved", "completed", "amended")
ENCOUNTER_STATES = ("draft", "completed")
APPOINTMENT_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
APPOINTMENT_STATUS = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,63}$")

# Exactly the fields a detail read may carry. The native field name is mapped
# to display_name; no identifier, contact value or clinical field is included.
# `modified` is the authoritative optimistic-concurrency token a later edit
# submits as `base_modified`; it is read only after the same field-level
# permission checks as every other field.
PATIENT_DETAIL_FIELDS = (
    "first_name",
    "last_name",
    "sex",
    "dob",
    "status",
    "modified",
)
PATIENT_DETAIL_NATIVE_KEYS = frozenset({"patient_name", *PATIENT_DETAIL_FIELDS})

# Explicit anonymous/expired response. The client treats 401 as "sign in", not as
# an error, so the body is one fixed code with no identity, role or session data.
ANONYMOUS_CODE = "AUTH_EXPIRED"

__all__ = [
    "ANONYMOUS_CODE",
    "MAX_APPOINTMENT_ITEMS",
    "MAX_CSRF_TOKEN_LENGTH",
    "MAX_DIRECTORY_ITEMS",
    "MAX_EPOCH_LENGTH",
    "PATIENT_DETAIL_FIELDS",
    "appointment_detail",
    "appointment_item",
    "appointment_read",
    "appointment_write_result",
    "build_anonymous",
    "build_appointments",
    "build_bootstrap",
    "build_directory",
    "encounter_detail",
    "encounter_write_result",
    "is_valid_text",
    "patient_detail",
    "patient_item",
    "patient_write_result",
]


def build_anonymous():
    """Fixed anonymous/expired body; sent with HTTP 401 by the adapter."""

    return {"code": ANONYMOUS_CODE}


def build_bootstrap(
    *, session_epoch, user_id, display_name, role, csrf_token, capabilities=None
):
    """Build the bootstrap payload or raise ``ValueError``.

    The caller has already authorized the request and decided the capability
    states. This function refuses any value that would produce a malformed or
    over-long client DTO, and validates the capability map it publishes.
    """

    if not is_valid_text(session_epoch, MAX_EPOCH_LENGTH):
        raise ValueError("invalid session epoch")
    if not is_valid_text(user_id):
        raise ValueError("invalid user id")
    if not is_valid_text(display_name):
        raise ValueError("invalid display name")
    if not isinstance(role, str) or role not in SUPPORTED_ROLE_CODES:
        raise ValueError("invalid role code")
    if not is_valid_text(csrf_token, MAX_CSRF_TOKEN_LENGTH):
        raise ValueError("invalid csrf token")
    if capabilities is None:
        capabilities = read_only_capabilities()
    else:
        capabilities = validate_capabilities(capabilities)

    return {
        "mode": "live",
        "sessionEpoch": session_epoch,
        "user": {
            "id": user_id,
            "displayName": display_name,
            "roles": [role],
        },
        "capabilities": capabilities,
        "csrfToken": csrf_token,
    }


def patient_item(workspace_patient_id, display_name):
    """Build one directory item or raise ``ValueError``.

    The native docname is deliberately absent. The server resolves it
    internally for the per-record native read check, but the client only
    receives the workspace alias and the display name.
    """

    for value in (workspace_patient_id, display_name):
        if not is_valid_text(value):
            raise ValueError("invalid patient item")

    return {
        "workspace_patient_id": workspace_patient_id,
        "display_name": display_name,
    }


def _is_calendar_date(value):
    """True only for a real ISO ``YYYY-MM-DD`` calendar date."""

    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


def patient_detail(workspace_patient_id, detail, *, editable=False):
    """Build one patient detail payload or raise ``ValueError``.

    ``detail`` is the gateway's native mapping. It must carry exactly the
    allowlisted native keys; the native docname is never accepted or returned.
    Optional values may be ``None`` but never another type, and a birth date
    must be a real calendar date before the client formats it.

    ``editable`` is the server's write-readiness decision for the current actor.
    It defaults to ``False`` and is only ever a boolean; the endpoint is the sole
    place that may compute ``True``, after its own write-resolution and native
    authority checks.
    """

    if not is_valid_text(workspace_patient_id):
        raise ValueError("invalid workspace patient id")
    if not isinstance(detail, dict) or set(detail) != PATIENT_DETAIL_NATIVE_KEYS:
        raise ValueError("invalid patient detail")
    if not isinstance(editable, bool):
        raise ValueError("invalid editable flag")

    display_name = detail["patient_name"]
    if not is_valid_text(display_name):
        raise ValueError("invalid display name")

    payload = {
        "workspace_patient_id": workspace_patient_id,
        "display_name": display_name,
    }
    for field in PATIENT_DETAIL_FIELDS:
        value = detail[field]
        if value is not None and not is_valid_text(value):
            raise ValueError("invalid patient field")
        if field == "dob" and value is not None and not _is_calendar_date(value):
            raise ValueError("invalid date of birth")
        if field == "modified" and (
            not is_valid_text(value, 64) or parse_modified(value) is None
        ):
            raise ValueError("invalid modified token")
        payload[field] = value
    payload["editable"] = editable
    return payload


def patient_write_result(status, workspace_patient_id, detail, ack_token):
    """Build the create/edit success payload or raise ``ValueError``.

    Success status is exactly ``created`` or ``updated``. The nested patient is
    the same allowlisted detail shape as the view, now including the
    authoritative ``modified`` token. ``ackToken`` is the opaque acknowledgement
    token the client must return before another write is admitted.
    """

    if status not in ("created", "updated"):
        raise ValueError("invalid write status")
    if not is_valid_ack_token(ack_token):
        raise ValueError("invalid acknowledgement token")
    patient = patient_detail(workspace_patient_id, detail)
    return {"status": status, "patient": patient, "ackToken": ack_token}


def build_directory(items):
    """Build the directory payload or raise ``ValueError``."""

    if not isinstance(items, (list, tuple)):
        raise ValueError("invalid directory items")
    if len(items) > MAX_DIRECTORY_ITEMS:
        raise ValueError("too many directory items")

    seen = set()
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) != {
            "workspace_patient_id",
            "display_name",
        }:
            raise ValueError("invalid directory item")
        workspace_id = item["workspace_patient_id"]
        if workspace_id in seen:
            raise ValueError("duplicate directory item")
        seen.add(workspace_id)
        normalized.append(dict(item))

    return {"items": normalized}


def appointment_item(
    workspace_patient_id,
    appointment_id,
    appointment_date,
    appointment_time,
    status,
):
    """Build one appointment row or raise ``ValueError``.

    Only the five existing live-contract fields are accepted. ``appointment_id``
    is the configured workspace appointment reference; the native appointment
    docname and native patient id never appear here.
    """

    if not is_valid_text(workspace_patient_id):
        raise ValueError("invalid workspace patient id")
    if not is_valid_text(appointment_id):
        raise ValueError("invalid appointment id")
    if not _is_calendar_date(appointment_date):
        raise ValueError("invalid appointment date")
    if not isinstance(appointment_time, str) or not APPOINTMENT_TIME.fullmatch(
        appointment_time
    ):
        raise ValueError("invalid appointment time")
    if not isinstance(status, str) or not APPOINTMENT_STATUS.fullmatch(status):
        raise ValueError("invalid appointment status")

    return {
        "workspace_patient_id": workspace_patient_id,
        "appointment_id": appointment_id,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "status": status,
    }


def build_appointments(items):
    """Build the appointment list payload or raise ``ValueError``."""

    if not isinstance(items, (list, tuple)):
        raise ValueError("invalid appointment items")
    if len(items) > MAX_APPOINTMENT_ITEMS:
        raise ValueError("too many appointment items")

    seen = set()
    normalized = []
    for item in items:
        if not isinstance(item, dict) or set(item) != APPOINTMENT_ITEM_KEYS:
            raise ValueError("invalid appointment item")
        appointment_id = item["appointment_id"]
        if appointment_id in seen:
            raise ValueError("duplicate appointment item")
        seen.add(appointment_id)
        normalized.append(dict(item))

    return {"items": normalized}


def appointment_detail(
    workspace_patient_id,
    appointment_id,
    appointment_date,
    appointment_time,
    status,
    modified,
):
    """Build the versioned appointment detail or raise ``ValueError``.

    This is the separate detail contract: the same five list fields plus the
    authoritative ``modified`` optimistic-concurrency token. It never carries a
    native appointment docname or native patient id.
    """

    item = appointment_item(
        workspace_patient_id=workspace_patient_id,
        appointment_id=appointment_id,
        appointment_date=appointment_date,
        appointment_time=appointment_time,
        status=status,
    )
    if not is_valid_text(modified, 64) or parse_modified(modified) is None:
        raise ValueError("invalid appointment version")
    item["modified"] = modified
    return item


def appointment_read(detail, *, can_reschedule=False, can_checkin=False):
    """Return the detail plus explicit, server-computed action eligibility.

    Both flags default to ``False`` and are only ever booleans; the endpoint is
    the sole place that may compute ``True`` after its own target, permission and
    budget checks. A readable static fixture therefore stays read-only.
    """

    if not isinstance(detail, dict) or set(detail) != APPOINTMENT_DETAIL_KEYS:
        raise ValueError("invalid appointment detail")
    if not isinstance(can_reschedule, bool) or not isinstance(can_checkin, bool):
        raise ValueError("invalid appointment action flag")
    payload = dict(detail)
    payload["can_reschedule"] = can_reschedule
    payload["can_checkin"] = can_checkin
    return payload


def appointment_write_result(status, detail, ack_token):
    """Build the booking/reschedule/check-in/cancel/no-show success payload.

    ``status`` is exactly ``booked``, ``rescheduled``, ``checked_in``,
    ``cancelled`` or ``no_show``. The nested appointment is the versioned detail
    shape and ``ackToken`` is the opaque acknowledgement token the client must
    return before another write is admitted. No native identifier is included.
    """

    if status not in APPOINTMENT_WRITE_STATUSES:
        raise ValueError("invalid appointment write status")
    if not is_valid_ack_token(ack_token):
        raise ValueError("invalid acknowledgement token")
    if not isinstance(detail, dict) or set(detail) != APPOINTMENT_DETAIL_KEYS:
        raise ValueError("invalid appointment detail")
    return {"status": status, "appointment": dict(detail), "ackToken": ack_token}


def encounter_detail(
    *, encounter_id, workspace_patient_id, state, status, modified
):
    """Build the encounter write detail or raise ``ValueError``.

    ``encounter_id`` is the server-derived workspace alias. ``state`` is the
    designed ``draft``/``completed`` phase and ``status`` the native status text
    read back after the operation. No native docname is included.
    """

    if not is_valid_text(encounter_id, 192):
        raise ValueError("invalid encounter id")
    if not is_valid_text(workspace_patient_id, 192):
        raise ValueError("invalid workspace patient id")
    if state not in ENCOUNTER_STATES:
        raise ValueError("invalid encounter state")
    if not is_valid_text(status, 64):
        raise ValueError("invalid encounter status")
    if not is_valid_text(modified, 64) or parse_modified(modified) is None:
        raise ValueError("invalid encounter version")
    return {
        "encounter_id": encounter_id,
        "workspace_patient_id": workspace_patient_id,
        "state": state,
        "status": status,
        "modified": modified,
    }


def encounter_write_result(status, detail, ack_token, *, addendum=None):
    """Build the draft/complete/amend success payload.

    ``addendum`` is the workspace-facing addendum reference for an amendment
    only; it is ``None`` for every other kind and is never a native docname.
    """

    if status not in ENCOUNTER_WRITE_STATUSES:
        raise ValueError("invalid encounter write status")
    if not is_valid_ack_token(ack_token):
        raise ValueError("invalid acknowledgement token")
    if not isinstance(detail, dict) or set(detail) != ENCOUNTER_DETAIL_KEYS:
        raise ValueError("invalid encounter detail")
    payload = {
        "status": status,
        "encounter": dict(detail),
        "ackToken": ack_token,
    }
    if status == "amended":
        if not is_valid_text(addendum, 192):
            raise ValueError("invalid addendum")
        payload["addendum"] = addendum
    elif addendum is not None:
        raise ValueError("unexpected addendum")
    return payload
