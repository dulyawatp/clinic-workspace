"""Narrow, uploader-only proof for one published Before original."""

import hashlib
import re
from datetime import datetime

import frappe

from clinic_photos.access.storage import is_canonical_visit_key
from clinic_photos.publication import journal, validation
from clinic_photos.publication.contracts import VisitBinding, VISIT_ORIGINAL_CREATE
from clinic_photos.visit import capture
from clinic_photos.visit.runtime import enabled_for_site


def _require(condition):
    if not condition:
        raise ValueError("Visit photo request unavailable")


def _readable(doctype, name, user):
    rows = frappe.get_list(doctype, filters={"name": name}, fields=["name"], limit_page_length=2)
    _require(isinstance(rows, (list, tuple)) and len(rows) == 1 and
             isinstance(rows[0], dict) and set(rows[0]) == {"name"} and rows[0]["name"] == name)
    doc = frappe.get_doc(doctype, name)
    _require(doc.name == name and frappe.has_permission(doctype, ptype="read", doc=doc, user=user) is True)
    return doc


def _generation(value):
    if type(value) is datetime and value.tzinfo is None:
        value = str(value)
    _require(type(value) is str and
             re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", value))
    datetime.fromisoformat(value)
    return value


def _proof(appointment_name, user, site):
    _require(frappe.session.user == user and frappe.local.site == site and
             enabled_for_site(site, frappe.conf) is True and
             type(getattr(frappe.db, "transaction_writes", None)) is int and
             frappe.db.transaction_writes == 0 and user not in ("Guest", "Administrator"))
    account = frappe.db.get_value("User", user, ["name", "enabled", "user_type"], as_dict=True)
    _require(account and account.get("name") == user and account.get("enabled") == 1 and
             account.get("user_type") == "System User")
    roles = frappe.db.sql(
        "select role from `tabHas Role` where parent = %s and parenttype = 'User' order by name",
        (user,),
    )
    _require(any(row == ("Receptionist",) for row in roles) and
             not any(row in (("System Manager",), ("Administrator",)) for row in roles))
    appointment = _readable("Patient Appointment", appointment_name, user)
    _require(bool(appointment.patient) and bool(appointment.creation))
    patient = _readable("Patient", appointment.patient, user)
    _require(bool(patient.creation))
    name = capture.slot_key(appointment.name, "Before")
    slot = frappe.get_doc(capture.SLOT, name)
    _require(slot.name == name and slot.slot_key == name and
             slot.appointment == appointment.name and slot.patient == patient.name and
             slot.slot == "Before" and slot.appointment_creation == str(appointment.creation) and
             slot.patient_creation == str(patient.creation) and
             all(type(getattr(slot, field, None)) is str and getattr(slot, field)
                 for field in ("operation", "photo", "photo_creation", "file_name", "file_creation")))
    _generation(slot.photo_creation)
    _generation(slot.file_creation)
    row = frappe.db.get_value(journal.OPERATION_DOCTYPE, slot.operation,
                              ["name", "actor", "site", "kind", "state"], as_dict=True)
    _require(row and row.get("name") == slot.operation and row.get("actor") == user and
             row.get("site") == site and row.get("kind") == VISIT_ORIGINAL_CREATE and
             row.get("state") == "PUBLISHED")
    operation = journal.load_verified_operation(slot.operation)
    _require(operation.id == slot.operation and operation.actor == user and operation.site == site and
             operation.kind == VISIT_ORIGINAL_CREATE and operation.state == "PUBLISHED" and operation.published)
    binding = operation.request.visit_binding
    _require(type(binding) is VisitBinding and binding.appointment == slot.appointment and
             binding.patient == slot.patient and binding.slot == "Before" and
             binding.appointment_creation == slot.appointment_creation and
             binding.patient_creation == slot.patient_creation and binding.body_area == slot.body_area and
             binding.viewpoint == slot.viewpoint)
    published = operation.published
    reservation = published.reservation
    _require(published.domain_doctype == "Clinic Photo" and published.domain_name == slot.photo and
             published.file_name == slot.file_name and reservation.visit_binding == binding and
             reservation.actor == user and reservation.site == site and
             is_canonical_visit_key(reservation.key) and
             type(reservation.expected_size) is int and
             type(published.creation_identity) is str and bool(published.creation_identity))
    validation.check_decoded_image_size(reservation.expected_size)
    photo = frappe.get_doc("Clinic Photo", slot.photo)
    file = frappe.get_doc("File", slot.file_name)
    _require(photo.name == slot.photo and photo.patient_reference == patient.name and
             photo.original_file == slot.file_name and photo.storage_key == reservation.key and
             photo.original_sha256 == reservation.expected_sha256 and
             _generation(photo.creation) == slot.photo_creation and
             file.name == slot.file_name and _generation(file.creation) == slot.file_creation and
             file.file_url == reservation.key and file.is_private == 1 and
             file.file_name == reservation.key.rsplit("/", 1)[-1] and file.owner == user and
             file.file_size == reservation.expected_size and
             file.attached_to_doctype == "Clinic Photo" and file.attached_to_name == photo.name and
             file.attached_to_field in (None, ""))
    return (user, site, appointment.name, patient.name, slot.name, slot.operation, slot.photo,
            slot.photo_creation, slot.file_name, slot.file_creation, binding, reservation.key,
            reservation.expected_size, reservation.expected_sha256, published.creation_identity)


def read_own_before(appointment):
    from clinic_photos.visit.annotation_read import read_verified_original_bytes

    user, site = frappe.session.user, frappe.local.site
    _require(type(user) is str and bool(user) and type(site) is str and bool(site))
    proof = _proof(appointment, user, site)
    data = read_verified_original_bytes(site, proof[11], proof[12], proof[14], proof[13])
    _require(type(data) is bytes and len(data) == proof[12] and
             hashlib.sha256(data).hexdigest() == proof[13])
    validation.validate_image_bytes(data)
    if data.startswith(b"\x89PNG\r\n\x1a\n") and proof[11].endswith(".png"):
        mime = "image/png"
    elif data.startswith(b"\xff\xd8\xff") and proof[11].endswith(".jpg"):
        mime = "image/jpeg"
    else:
        raise ValueError("Visit photo request unavailable")
    _require(_proof(appointment, user, site) == proof)
    return data, mime
