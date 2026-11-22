"""Private visit and slot checks; not a publication entry point."""

import hashlib
import json

import frappe

from clinic_photos.publication.contracts import VisitBinding
from clinic_photos.visit import actor, bookkeeping, state

SLOT = "Clinic Visit Photo Slot"


class VisitEligibilityRejected(frappe.PermissionError):
    """Verified visit identity has a definitely revoked or replaced grant."""


def _deny():
    frappe.throw("Visit photo capture is not permitted", frappe.PermissionError)


def check_visit_authority(binding):
    """Check visit, encounter and grant under locks in caller's transaction.

    This is deliberately incomplete: caller must also lock and validate both
    slot rows, check pairing and vacancy, and reauthorize at publication before
    any bytes or records are written. No caller may treat success as permission
    to publish. No commit, staging, enqueue, or File mutation occurs here.
    """
    if type(binding) is not VisitBinding:
        _deny()
    _check_visit_authority(
        binding.appointment, binding.patient, binding.slot, binding.encounter,
        binding.encounter_creation, binding.appointment_creation,
        binding.patient_creation, binding.grant_audit,
    )


def _check_visit_authority(appointment_name, patient, slot, encounter_name,
                           encounter_creation, appointment_creation, patient_creation,
                           grant_audit):
    """Shared authority checks using identities derived from native visit records."""
    actor.require_visit_actor(doctor=slot == "After")

    encounter = None
    if slot == "After":
        encounter = frappe.get_doc("Patient Encounter", encounter_name, for_update=True)
        if (encounter.name != encounter_name or
            str(encounter.creation) != encounter_creation or
            encounter.docstatus != 0 or
            encounter.appointment != appointment_name or
            encounter.patient != patient or
            not frappe.has_permission("Patient Encounter", "read", doc=encounter) or
            not frappe.has_permission("Patient Encounter", "write", doc=encounter)):
            _deny()

    appointment, visit = state.lock_visit(appointment_name)
    if (appointment.name != appointment_name or appointment.patient != patient or
        not visit or visit.appointment_creation != appointment_creation or
        visit.patient_creation != patient_creation):
        _deny()
    if encounter and (
        not appointment.practitioner or not encounter.practitioner or
        encounter.practitioner != appointment.practitioner
    ):
        _deny()
    if encounter and not state.has_checkin_witness(appointment_name, patient):
        _deny()

    audit = frappe.get_doc("Clinic Visit Photo Consent Audit", grant_audit, for_update=True)
    if (audit.name != grant_audit or audit.appointment != appointment_name or
        audit.patient != patient or audit.action != "Granted" or audit.attested != 1):
        _deny()
    if not frappe.has_permission("File", "create"):
        _deny()
    active = getattr(visit, "active_grant_audit", None)
    revoked = type(visit.consent_granted) is int and visit.consent_granted == 0 and active in (None, "")
    replaced = (
        type(visit.consent_granted) is int and visit.consent_granted == 1 and isinstance(active, str) and active
        and active == active.strip() and active.isprintable() and len(active) <= 140
        and active != grant_audit
    )
    if replaced:
        current_audit = frappe.get_doc("Clinic Visit Photo Consent Audit", active, for_update=True)
        if (current_audit.name != active or current_audit.appointment != appointment_name or
            current_audit.patient != patient or current_audit.action != "Granted" or
            current_audit.attested != 1):
            _deny()
    if revoked or replaced:
        raise VisitEligibilityRejected("Visit photo capture is not permitted")
    if visit.consent_granted != 1 or active != grant_audit:
        _deny()


def slot_key(appointment, slot):
    """Deterministic opaque unique key for a canonical appointment and slot."""
    if (not isinstance(appointment, str) or not appointment or
        appointment.strip() != appointment or slot not in ("Before", "After")):
        _deny()
    encoded = json.dumps([appointment, slot], ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def _verified_photo(operation_id, photo_name, binding):
    """Require matching published journal and exact native Photo/File linkage."""
    from clinic_photos.publication import journal
    from clinic_photos.visit.pair import _require_native_read_for_verified_slot

    actor, site = frappe.session.user, frappe.local.site
    operation = journal.load_verified_operation(operation_id, for_update=True)
    if (operation.state != "PUBLISHED" or operation.kind != "visit_original_create" or
        operation.site != frappe.local.site or operation.request.visit_binding != binding or
        not operation.published or operation.published.domain_doctype != "Clinic Photo" or
        operation.published.domain_name != photo_name):
        _deny()
    published = operation.published
    photo = frappe.get_doc("Clinic Photo", photo_name, for_update=True)
    if (photo.name != photo_name or photo.patient_reference != binding.patient or
        photo.original_file != published.file_name or
        photo.storage_key != published.reservation.key or
        photo.original_sha256 != published.reservation.expected_sha256):
        _deny()
    file = frappe.get_doc("File", published.file_name, for_update=True)
    if (file.name != published.file_name or file.is_private != 1 or
        file.file_url != photo.storage_key or file.attached_to_doctype != "Clinic Photo" or
        file.attached_to_name != photo.name):
        _deny()
    _require_native_read_for_verified_slot(binding.patient, photo, file, actor, site)


def check_slot_availability(binding):
    """Lock visit then slot rows; reject occupied or inconsistent pair metadata."""
    check_visit_authority(binding)
    current = frappe.db.get_value(
        SLOT, {"slot_key": slot_key(binding.appointment, binding.slot)},
        "name", for_update=True,
    )
    if current:
        _deny()
    other_slot = "After" if binding.slot == "Before" else "Before"
    other = frappe.db.get_value(
        SLOT, {"slot_key": slot_key(binding.appointment, other_slot)},
        "name", for_update=True,
    )
    if not other:
        if binding.slot == "After":
            _deny()
        return
    record = frappe.get_doc(SLOT, other, for_update=True)
    if (record.name != slot_key(binding.appointment, other_slot) or
        record.slot_key != record.name or record.appointment != binding.appointment or
        record.patient != binding.patient or record.slot != other_slot or
        record.appointment_creation != binding.appointment_creation or
        record.patient_creation != binding.patient_creation or
        record.body_area != binding.body_area or record.viewpoint != binding.viewpoint):
        _deny()
    from clinic_photos.publication import journal
    operation = journal.load_verified_operation(record.operation, for_update=True)
    previous = operation.request.visit_binding
    if (type(previous) is not VisitBinding or previous.appointment != record.appointment or
        previous.patient != record.patient or previous.slot != record.slot or
        previous.appointment_creation != record.appointment_creation or
        previous.patient_creation != record.patient_creation or
        previous.body_area != record.body_area or previous.viewpoint != record.viewpoint):
        _deny()
    _verified_photo(record.operation, record.photo, previous)


def insert_prepared_slot_link(prepared, bound):
    """Insert a slot from exact consumed writer evidence while still PREPARED."""
    from clinic_photos.publication import writer

    evidence, linkage = writer.require_visit_consumed_object(prepared, bound)
    if evidence is not bound or linkage.doctype != "Clinic Photo":
        _deny()
    binding = prepared.visit_binding
    check_slot_availability(binding)
    doc = frappe.get_doc({
        "doctype": SLOT, "slot_key": slot_key(binding.appointment, binding.slot),
        "appointment": binding.appointment, "patient": binding.patient, "slot": binding.slot,
        "appointment_creation": binding.appointment_creation,
        "patient_creation": binding.patient_creation, "body_area": binding.body_area,
        "viewpoint": binding.viewpoint, "operation": prepared.id, "photo": linkage.name,
    })
    if doc.doctype != SLOT:
        _deny()
    with bookkeeping.internal_write(doc):
        doc.insert(ignore_permissions=True)
    return doc.name
