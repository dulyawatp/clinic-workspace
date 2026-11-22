"""Transaction-bound, read-only authority for a visit annotation write."""

import frappe

from clinic_photos.publication.validation import require_reference
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationBinding
from clinic_photos.visit import actor, pair, runtime, state


def _deny():
    frappe.throw("Visit annotation is not permitted", frappe.PermissionError)


def resolve_write_binding(appointment, encounter, slot, *, expected=None):
    """Resolve fresh evidence under locks; caller owns transaction and outcome."""
    return _resolve_write_binding(appointment, encounter, slot, expected=expected, for_update=True)


def preview_write_binding(appointment, encounter, slot, *, expected=None):
    """Advisory read only; submission must revalidate under writer locks."""
    return _resolve_write_binding(appointment, encounter, slot, expected=expected, for_update=False)


def _resolve_write_binding(appointment, encounter, slot, *, expected, for_update):
    site = frappe.local.site
    conf = frappe.local.conf
    if not runtime.enabled_for_site(site, conf) or conf.get("clinic_visit_photo_annotations_enabled") is not True:
        _deny()
    for value in (appointment, encounter):
        require_reference(value)
        if not value.isprintable():
            _deny()
    if slot not in ("Before", "After"):
        _deny()
    user = actor.require_visit_actor(doctor=True) if for_update else actor.require_visit_actor(doctor=True, for_update=False)
    linked = frappe.db.sql(
        """select name from `tabPatient Encounter`
           where appointment = %s and docstatus != 2
           order by name limit 2""" + (" for update" if for_update else ""),
        (appointment,),
    )
    if len(linked) != 1 or linked[0][0] != encounter:
        _deny()
    doc = frappe.get_doc("Patient Encounter", encounter, for_update=for_update)
    if (doc.name != encounter or not doc.creation or doc.docstatus != 0 or
        doc.appointment != appointment or not doc.patient or
        not frappe.has_permission("Patient Encounter", "read", doc=doc) or
        not frappe.has_permission("Patient Encounter", "write", doc=doc)):
        _deny()
    appt, visit = state.lock_visit(appointment) if for_update else state.read_visit(appointment)
    if (not visit or appt.patient != doc.patient or not appt.practitioner or
        not doc.practitioner or appt.practitioner != doc.practitioner):
        _deny()
    grant = getattr(visit, "active_grant_audit", None)
    if type(visit.consent_granted) is not int or visit.consent_granted != 1:
        _deny()
    require_reference(grant)
    audit = frappe.get_doc("Clinic Visit Photo Consent Audit", grant, for_update=for_update)
    if (audit.name != grant or audit.appointment != appointment or
        audit.patient != appt.patient or audit.action != "Granted" or audit.attested != 1):
        _deny()
    patient = frappe.get_doc("Patient", appt.patient, for_update=for_update)
    if patient.name != appt.patient or str(patient.creation) != visit.patient_creation:
        _deny()
    result = pair._read_slot_structure(appt, patient, slot, for_update=for_update)
    if not result:
        _deny()
    record, photo, file = result
    pair._require_native_read_for_verified_slot(patient.name, photo, file, user, site)
    if not frappe.has_permission("File", "create"):
        _deny()
    binding = VisitAnnotationBinding(
        site=site, actor=user, appointment=appointment, patient=patient.name,
        slot=slot, encounter=encounter, appointment_creation=str(appt.creation),
        patient_creation=str(patient.creation), encounter_creation=str(doc.creation),
        grant_audit=grant, published_slot=record.name, original_photo=photo.name,
        original_file=file.name, original_operation=record.operation,
    )
    if expected is not None and (type(expected) is not VisitAnnotationBinding or binding != expected):
        _deny()
    return binding
