"""Witness only fresh transitions made by the native HTTP check-in dispatch."""

import frappe

from clinic_photos.visit import bookkeeping
from clinic_photos.visit.identity import current_identity
from clinic_photos.visit.runtime import enabled_for_site


def _native(appointment_id, practitioner, service_unit):
    from healthcare.healthcare.doctype.patient_appointment.patient_appointment import (
        check_in_appointment,
    )

    return check_in_appointment(appointment_id, practitioner, service_unit)


def _record_witness(appointment, patient):
    doc = frappe.get_doc("Patient Appointment", appointment, for_update=True)
    if doc.name != appointment or doc.patient != patient:
        frappe.throw("Inconsistent visit witness", frappe.ValidationError)
    if not frappe.has_permission("Patient Appointment", "read", doc=doc, throw=False):
        return
    identity = current_identity(doc, skip_denied=True)
    if identity is None:
        return
    appointment_creation, patient_creation = identity
    existing = frappe.db.get_value(
        "Clinic Visit Checkin Witness", {"appointment": appointment},
        ["patient", "appointment_snapshot", "patient_snapshot",
         "appointment_creation", "patient_creation"],
        as_dict=True, for_update=True,
    )
    if existing:
        # Never replace an existing witness, including a stale one. Capture
        # checks its identity separately and fails closed on any mismatch.
        return
    bookkeeping.insert({
        "doctype": "Clinic Visit Checkin Witness",
        "appointment": appointment,
        "patient": patient,
        "appointment_snapshot": appointment,
        "patient_snapshot": patient,
        "appointment_creation": appointment_creation,
        "patient_creation": patient_creation,
        "actor": frappe.session.user,
    })


@frappe.whitelist()
def check_in_appointment(appointment_id, practitioner=None, service_unit=None):
    result = _native(appointment_id, practitioner, service_unit)
    if not enabled_for_site(
        getattr(getattr(frappe, "local", None), "site", None),
        getattr(frappe, "conf", None),
    ):
        return result
    before = result.get_doc_before_save()
    canonical = result.name
    after = frappe.get_doc("Patient Appointment", canonical, for_update=True)
    if (not result.patient or not canonical or after.name != canonical or
        after.patient != result.patient or
        result.status != "Checked In" or after.status != "Checked In"):
        frappe.throw("Inconsistent native check-in", frappe.ValidationError)
    if not before or before.status in (None, "", "Cancelled", "Closed", "Checked Out", "Checked In"):
        return result
    if before.name != canonical or before.patient != result.patient:
        frappe.throw("Inconsistent native check-in", frappe.ValidationError)
    _record_witness(canonical, result.patient)
    return result
