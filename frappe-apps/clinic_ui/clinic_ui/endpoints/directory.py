"""``GET /api/method/clinic_ui.api.directory.synthetic_patients``.

Lists only the server-owned configured allowlist, and only the entries for which
the *current* user passes a native read permission check. The endpoint accepts no
client input: no workspace id, native id, filter, limit or user parameter.

A denied, missing or malformed entry is omitted without description. The native
read check itself belongs to the gateway adapter, which resolves it against the
current session user only.
"""

from clinic_ui.core import dto
from clinic_ui.core.policy import DIRECTORY_ROLE_CODES
from clinic_ui.endpoints import common


def synthetic_patients(gateway):
    settings, _view, _role = common.authorize(gateway, DIRECTORY_ROLE_CODES)

    items = []
    for workspace_patient_id, native_patient_id in settings.allowlist:
        display_name = gateway.native_patient_display_name(native_patient_id)
        if display_name is None:
            continue
        try:
            items.append(
                dto.patient_item(workspace_patient_id, native_patient_id, display_name)
            )
        except ValueError:
            continue

    try:
        return dto.build_directory(items)
    except ValueError:
        gateway.deny()
