"""Private worker for committed clinic_ui native check-in notifications."""

from contextvars import ContextVar

import frappe

_witnessing = ContextVar("clinic_checkin_witness", default=None)


def is_witnessing(doc):
    return _witnessing.get() is doc


def observe(appointment, patient, observed_at, actor):
    if not all(isinstance(value, str) and value for value in (appointment, patient, observed_at, actor)):
        return
    if frappe.session.user != actor:
        return
    appointment_row = frappe.db.get_value(
        "Patient Appointment", appointment, ["patient"], as_dict=True, for_update=True,
    )
    if not appointment_row or appointment_row["patient"] != patient:
        return
    row = frappe.db.get_value(
        "Clinic Visit Photo Consent", {"appointment": appointment}, ["name"],
        as_dict=True, for_update=True,
    )
    if not row:
        return
    doc = frappe.get_doc("Clinic Visit Photo Consent", row["name"])
    if (doc.get("appointment") != appointment or doc.get("patient") != patient
            or doc.get("revoked") or doc.get("checkin_witness_at") or not doc.get("granted_at")):
        return
    if frappe.utils.get_datetime(doc.get("granted_at")) >= frappe.utils.get_datetime(observed_at):
        return
    if not frappe.has_permission("Clinic Visit Photo Consent", "write", doc=doc):
        return
    if not frappe.has_permission("Patient Appointment", "read", doc=appointment) or not frappe.has_permission("Patient", "read", doc=patient):
        return
    token = _witnessing.set(doc)
    try:
        doc.checkin_witness_at = observed_at
        doc.save()
    finally:
        _witnessing.reset(token)
