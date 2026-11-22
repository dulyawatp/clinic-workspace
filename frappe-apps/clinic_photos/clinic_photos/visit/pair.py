"""Private read-only visit pair projection; not a public or byte-access API."""

from contextvars import ContextVar

import frappe

from clinic_photos.access.storage import is_canonical_visit_key
from clinic_photos.publication import journal
from clinic_photos.publication.contracts import VisitBinding, VISIT_ORIGINAL_CREATE
from clinic_photos.visit.capture import SLOT, slot_key

_FILE_READ_SCOPE = ContextVar("clinic_visit_file_read", default=None)
_PHOTO_READ_SCOPE = ContextVar("clinic_visit_photo_read", default=None)


def _photo_read_in_scope(photo, user):
    scope = _PHOTO_READ_SCOPE.get()
    if scope is None:
        return False
    expected, key, file_name, photo_name, patient, actor, site, indirect = scope
    return (
        (photo is expected or indirect) and getattr(photo, "name", None) == photo_name
        and getattr(photo, "storage_key", None) == key
        and getattr(photo, "original_file", None) == file_name
        and getattr(photo, "patient_reference", None) == patient
        and is_canonical_visit_key(key)
        and frappe.local.site == site and frappe.session.user == actor
        and (user is None or user == actor)
    )


def _file_read_in_scope(file, user):
    scope = _FILE_READ_SCOPE.get()
    if scope is None:
        return False
    expected, photo, key, file_name, photo_name, actor, site = scope
    return (
        file is expected and file.name == file_name and file.file_url == key
        and file.is_private == 1 and file.attached_to_doctype == "Clinic Photo"
        and file.attached_to_name == photo_name and photo.name == photo_name
        and photo.original_file == file_name and photo.storage_key == key
        and is_canonical_visit_key(key)
        and frappe.local.site == site and frappe.session.user == actor
        and (user is None or user == actor)
    )


def _deny():
    frappe.throw("Visit photo history is not available", frappe.PermissionError)


def _read_slot_structure(appointment, patient, which, *, for_update=False):
    """Validate persisted linkage without invoking document permission hooks."""
    lock = {"for_update": True} if for_update else {}
    name = frappe.db.get_value(
        SLOT, {"appointment": appointment.name, "slot": which}, "name", **lock
    )
    if not name:
        return None
    record = frappe.get_doc(SLOT, name, **lock)
    if (name != slot_key(appointment.name, which) or record.name != name or
        record.slot_key != name or record.appointment != appointment.name or
        record.patient != patient.name or record.slot != which or
        record.appointment_creation != str(appointment.creation) or
        record.patient_creation != str(patient.creation)):
        _deny()

    operation = journal.load_verified_operation(record.operation)
    if (operation.id != record.operation or operation.state != "PUBLISHED" or
        operation.kind != VISIT_ORIGINAL_CREATE or operation.site != frappe.local.site or
        not operation.published):
        _deny()
    binding = operation.request.visit_binding
    if (type(binding) is not VisitBinding or binding.appointment != record.appointment or
        binding.patient != record.patient or binding.slot != record.slot or
        binding.appointment_creation != record.appointment_creation or
        binding.patient_creation != record.patient_creation or
        binding.body_area != record.body_area or binding.viewpoint != record.viewpoint):
        _deny()
    published = operation.published
    if published.domain_doctype != "Clinic Photo" or published.domain_name != record.photo:
        _deny()
    photo = frappe.get_doc("Clinic Photo", record.photo, **lock)
    if (photo.name != record.photo or photo.patient_reference != patient.name or
        photo.original_file != published.file_name or
        photo.storage_key != published.reservation.key or
        photo.original_sha256 != published.reservation.expected_sha256):
        _deny()
    file = frappe.get_doc("File", published.file_name, **lock)
    if (file.name != published.file_name or file.is_private != 1 or
        file.file_url != photo.storage_key or file.attached_to_doctype != "Clinic Photo" or
        file.attached_to_name != photo.name):
        _deny()
    return record, photo, file


def _require_native_read_for_verified_slot(patient_name, photo, file, actor, site):
    """Check native access to a structurally verified published Photo/File pair."""
    key, file_name, photo_name = file.file_url, file.name, photo.name
    if (_FILE_READ_SCOPE.get() is not None or _PHOTO_READ_SCOPE.get() is not None
        or frappe.session.user != actor or
        frappe.local.site != site):
        _deny()
    photo_scope = (photo, key, file_name, photo_name, patient_name, actor, site)
    photo_token = _PHOTO_READ_SCOPE.set((*photo_scope, False))
    try:
        if not frappe.has_permission("Clinic Photo", "read", doc=photo):
            _deny()
        _PHOTO_READ_SCOPE.reset(photo_token)
        photo_token = _PHOTO_READ_SCOPE.set((*photo_scope, True))
        token = _FILE_READ_SCOPE.set(
            (file, photo, key, file_name, photo_name, actor, site)
        )
        try:
            if not frappe.has_permission("File", "read", doc=file):
                _deny()
        finally:
            _FILE_READ_SCOPE.reset(token)
    finally:
        _PHOTO_READ_SCOPE.reset(photo_token)


def _read_slot(appointment, patient, which, actor, site):
    linked = _read_slot_structure(appointment, patient, which)
    if not linked:
        return None
    record, photo, file = linked
    _require_native_read_for_verified_slot(patient.name, photo, file, actor, site)
    return {"photo": photo.name, "file": file.name,
            "body_area": record.body_area, "viewpoint": record.viewpoint}


def read_pair(appointment_name):
    """Return coherent published metadata only; no consent or write authority.

    Caller must provide an ordinary Frappe read transaction. This private
    interface returns no storage paths, URLs, staging information or bytes.
    """
    writes = getattr(frappe.db, "transaction_writes", None)
    if type(writes) is not int or writes != 0:
        _deny()
    if (not isinstance(appointment_name, str) or not appointment_name or
        appointment_name.strip() != appointment_name):
        _deny()
    actor, site = frappe.session.user, frappe.local.site
    if not actor or not site:
        _deny()
    appointment = frappe.get_doc("Patient Appointment", appointment_name)
    if (appointment.name != appointment_name or not appointment.patient or
        not appointment.creation or
        not frappe.has_permission("Patient Appointment", "read", doc=appointment)):
        _deny()
    patient = frappe.get_doc("Patient", appointment.patient)
    if (patient.name != appointment.patient or not patient.creation or
        not frappe.has_permission("Patient", "read", doc=patient)):
        _deny()
    before = _read_slot(appointment, patient, "Before", actor, site)
    after = _read_slot(appointment, patient, "After", actor, site)
    if after and (not before or before["body_area"] != after["body_area"] or
                  before["viewpoint"] != after["viewpoint"]):
        _deny()
    return {"appointment": appointment.name, "patient": patient.name,
            "before": before, "after": after}
