"""Native practitioner draft fields and validation."""

from clinic_ui.core.doctor_hours import _check_fields


_PRACTITIONER = {
    "first_name": ("Data", None), "last_name": ("Data", None),
    "practitioner_name": ("Data", None), "department": ("Link", "Medical Department"),
    "status": ("Select", None), "user_id": ("Link", "User"),
    "show_in_portal": ("Check", None),
    "practitioner_schedules": ("Table", "Practitioner Service Unit Schedule"),
}
_DEPARTMENT = {"department": ("Data", None)}


def check_field_authority(frappe, write=False):
    _check_fields(frappe, "Healthcare Practitioner", _PRACTITIONER, "read")
    _check_fields(frappe, "Medical Department", _DEPARTMENT, "read")
    if write:
        _check_fields(frappe, "Healthcare Practitioner", _PRACTITIONER, "write")


def eligible_practitioner(doc):
    return doc.status == "Disabled" and not doc.user_id and not doc.show_in_portal


def validate_input(body):
    if not isinstance(body, dict) or set(body) != {"firstName", "lastName", "department"}:
        raise ValueError("Invalid profile")
    values = []
    for key in ("firstName", "lastName", "department"):
        value = body[key]
        if (not isinstance(value, str) or (key != "lastName" and not value.strip())
                or len(value.strip()) > 140
                or any(ord(char) < 32 or ord(char) == 127 for char in value)):
            raise ValueError("Invalid profile")
        values.append(value.strip())
    if len(" ".join(value for value in values[:2] if value)) > 140:
        raise ValueError("Invalid profile")
    return values


def present(doc):
    return {"name": doc.name, "displayName": doc.practitioner_name, "version": str(doc.modified)}
