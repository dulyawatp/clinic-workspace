"""Native appointment check-in without synthetic write-lane authority."""

from clinic_ui.core.text import is_valid_identifier


def checkin(gateway, payload):
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "expected_version"}:
        raise ValueError("Invalid check-in request")
    ref = payload["appointment_ref"]
    version = payload["expected_version"]
    if not is_valid_identifier(ref) or not isinstance(version, str) or not version.strip():
        raise ValueError("Invalid appointment identity or version")
    name = gateway.linked_resolve(ref)
    if name in gateway.checkin_fixture_ids():
        raise ValueError("Fixture appointment is not eligible")
    doc = gateway.frappe.get_doc("Patient Appointment", name, for_update=True)
    doc.check_permission("read")
    doc.check_permission("write")
    gateway._linked_fields("Patient Appointment", (
        "patient", "practitioner", "service_unit", "status", "position_in_queue",
    ))
    if doc.name != name or not doc.patient or str(doc.modified) != version:
        raise ValueError("Appointment identity or version changed")
    gateway.frappe.get_doc("Patient", doc.patient).check_permission("read")
    if doc.status in ("Cancelled", "Closed", "Checked Out", "Checked In"):
        raise ValueError("Appointment is not eligible for check-in")
    identity = (doc.name, doc.patient, doc.practitioner, doc.service_unit)
    gateway._native_check_in_function()(name)
    result = gateway.frappe.get_doc("Patient Appointment", name, for_update=True)
    if (result.name, result.patient, result.practitioner, result.service_unit) != identity:
        raise ValueError("Appointment identity changed during check-in")
    queue = result.position_in_queue
    if result.status != "Checked In" or isinstance(queue, bool) or not isinstance(queue, int) or queue <= 0:
        raise ValueError("Native check-in was not verified")
    return {"appointment": name, "status": result.status, "modified": str(result.modified)}
