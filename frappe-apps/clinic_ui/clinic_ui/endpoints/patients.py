"""``GET /api/method/clinic_ui.api.patients.view``.

Reads the allowlisted display fields of one record. The client supplies a
workspace identifier: a configured static alias, a historical sealed alias, or
a native ``Patient`` docname returned by the converted create lane. The native
docname is accepted only after the current user's own native Patient read
permission check.

Gate order is the shared one: no-store, session validity, site configuration,
clinic role, then the native read permission check for the *current* user on
the exact Patient. A refused native check, a missing or malformed identifier and
a malformed record all end as the framework's generic 403; nothing partial is
returned. REC holds no Patient permission on the isolated site, so a REC view
is denied even though the clinic role gate admits the role code.

The detail includes the authoritative ``modified`` token so an edit can submit
``base_modified``. It is read only after the same field-level read permission
checks as every other detail field.

A DOC or NUR caller may read another clinician's historical created Patient, but
only when a terminal committed create receipt re-derives to the exact alias and
the current user separately passes the native read check. The response carries
``editable``: ``False`` by default and for every non-creator created alias, and
``True`` only when the current actor's own actor-bound write resolution and the
native Patient write authority both permit the edit. This flag never grants or
widens write authority; it is a display decision computed after the read.
"""

from clinic_ui.core import dto, patient_alias, patient_write
from clinic_ui.core.policy import PATIENT_VIEW_ROLE_CODES, PATIENT_WRITE_ROLE_CODES
from clinic_ui.core.text import (
    is_valid_identifier,
    is_valid_text,
    normalize_identifier,
)
from clinic_ui.endpoints import aliases, common


def _is_editable(gateway, settings, actor, role, alias, resolved):
    """Compute the display-only edit flag; any uncertainty stays ``False``."""

    if role not in PATIENT_WRITE_ROLE_CODES:
        return False
    if resolved.source == aliases.SOURCE_CREATED and resolved.creator != actor:
        return False
    try:
        write_native_patient_id = aliases.resolve_write(
            gateway, settings, actor, alias
        )
        if write_native_patient_id != resolved.native_patient_id:
            return False
        return gateway.native_patient_can_write(write_native_patient_id) is True
    except (
        patient_write.WriteLocked,
        patient_write.WriteConflict,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_alias.AliasError,
    ):
        return False


def view(gateway, workspace_patient_id):
    settings, session, role = common.authorize(gateway, PATIENT_VIEW_ROLE_CODES)

    alias = normalize_identifier(workspace_patient_id)
    if not is_valid_identifier(alias) or not is_valid_text(alias, 256):
        gateway.deny()

    try:
        resolved = aliases.resolve_read(
            gateway, settings, session.user, role, alias
        )
    except (
        patient_write.WriteLocked,
        patient_write.WriteConflict,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        gateway.deny()

    detail = gateway.native_patient_view(resolved.native_patient_id)
    if detail is None:
        gateway.deny()

    editable = _is_editable(
        gateway, settings, session.user, role, alias, resolved
    )

    try:
        return dto.patient_detail(alias, detail, editable=editable)
    except ValueError:
        gateway.deny()
