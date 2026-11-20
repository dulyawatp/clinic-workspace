"""Native appointment hook; no public API or permission bypass."""

from contextvars import ContextVar

import frappe

_observing = ContextVar("clinic_photo_observing", default=None)


def checked_in(status, queue):
    return status == "Checked In" and isinstance(queue, int) and not isinstance(queue, bool) and queue > 0


def is_observing(doc):
    return _observing.get() is doc


def appointment_updated(doc, method=None):
    previous = doc.get_doc_before_save()
    if (
        not previous
        or previous.status == "Checked In"
        or previous.name != doc.name
        or previous.patient != doc.patient
        or previous.position_in_queue not in (None, 0)
        or not checked_in(doc.status, doc.position_in_queue)
    ):
        return
    row = frappe.db.get_value("Clinic Visit Photo Consent", {"appointment": doc.name}, ["name"], as_dict=True, for_update=True)
    if not row:
        return
    consent = frappe.get_doc("Clinic Visit Photo Consent", row["name"])
    if consent.get("patient") != doc.patient or consent.get("revoked") or consent.get("checked_in_at"):
        return
    if not frappe.has_permission("Clinic Visit Photo Consent", "write", doc=consent):
        return
    if not frappe.has_permission("Patient Appointment", "read", doc=doc.name) or not frappe.has_permission("Patient", "read", doc=doc.patient):
        return
    token = _observing.set(consent)
    try:
        consent.checked_in_at = frappe.utils.now_datetime()
        consent.save()
    finally:
        _observing.reset(token)
