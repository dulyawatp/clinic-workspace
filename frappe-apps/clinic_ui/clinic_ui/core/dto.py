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
      "capabilities": {"<every capability>": "read_only"},
      "csrfToken": "<frappe session csrf token>"
    }

Directory::

    {"items": [
      {"workspace_patient_id": "...", "native_patient_id": "...", "display_name": "..."}
    ]}
"""

from clinic_ui.core.capabilities import read_only_capabilities
from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.core.text import MAX_TEXT_LENGTH, is_valid_text

MAX_EPOCH_LENGTH = 128
MAX_DIRECTORY_ITEMS = 1000
MAX_CSRF_TOKEN_LENGTH = MAX_TEXT_LENGTH

# Explicit anonymous/expired response. The client treats 401 as "sign in", not as
# an error, so the body is one fixed code with no identity, role or session data.
ANONYMOUS_CODE = "AUTH_EXPIRED"

__all__ = [
    "ANONYMOUS_CODE",
    "MAX_CSRF_TOKEN_LENGTH",
    "MAX_DIRECTORY_ITEMS",
    "MAX_EPOCH_LENGTH",
    "build_anonymous",
    "build_bootstrap",
    "build_directory",
    "is_valid_text",
    "patient_item",
]


def build_anonymous():
    """Fixed anonymous/expired body; sent with HTTP 401 by the adapter."""

    return {"code": ANONYMOUS_CODE}


def build_bootstrap(*, session_epoch, user_id, display_name, role, csrf_token):
    """Build the bootstrap payload or raise ``ValueError``.

    The caller has already authorized the request; this function refuses any
    value that would produce a malformed or over-long client DTO.
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

    return {
        "mode": "live",
        "sessionEpoch": session_epoch,
        "user": {
            "id": user_id,
            "displayName": display_name,
            "roles": [role],
        },
        "capabilities": read_only_capabilities(),
        "csrfToken": csrf_token,
    }


def patient_item(workspace_patient_id, native_patient_id, display_name):
    """Build one directory item or raise ``ValueError``."""

    for value in (workspace_patient_id, native_patient_id, display_name):
        if not is_valid_text(value):
            raise ValueError("invalid patient item")

    return {
        "workspace_patient_id": workspace_patient_id,
        "native_patient_id": native_patient_id,
        "display_name": display_name,
    }


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
            "native_patient_id",
            "display_name",
        }:
            raise ValueError("invalid directory item")
        workspace_id = item["workspace_patient_id"]
        if workspace_id in seen:
            raise ValueError("duplicate directory item")
        seen.add(workspace_id)
        normalized.append(dict(item))

    return {"items": normalized}
