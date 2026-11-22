"""Private transaction-bound visit consent interface, not an HTTP endpoint."""

import frappe

from clinic_photos.visit import actor, bookkeeping
from clinic_photos.visit.identity import current_identity

VISIT = "Clinic Visit Photo State"

# Identifiers explicitly authored by the existing demo consent fixtures. This
# prevents their reuse as evidence, not independent verification of a source.
SAMPLE_EVIDENCE = frozenset({
    "sample consent",
    "pc-form-2.1/1041",
    "pc-form-2.0/1043",
    "pc-form-2.1/1044",
    "pc-form-2.1/1048",
    "pc-form-2.1/guardian-01",
})


def lock_visit(appointment):
    """Lock appointment first, then its state; caller owns transaction outcome."""
    return _visit(appointment, for_update=True)


def read_visit(appointment):
    """Advisory equivalent of lock_visit; no row locks or mutations."""
    return _visit(appointment, for_update=False)


def _visit(appointment, *, for_update):
    if not isinstance(appointment, str) or not appointment.strip():
        frappe.throw("Appointment required", frappe.ValidationError)
    doc = frappe.get_doc("Patient Appointment", appointment, for_update=for_update)
    if doc.name != appointment or not doc.patient:
        frappe.throw("Inconsistent appointment", frappe.ValidationError)
    if not (frappe.has_permission("Patient Appointment", "read", doc=doc) and
            frappe.has_permission("Patient Appointment", "write", doc=doc) and
            frappe.has_permission("Patient", "read", doc=doc.patient)):
        frappe.throw("Not permitted", frappe.PermissionError)
    if for_update:
        appointment_creation, patient_creation = current_identity(doc)
    else:
        if not doc.creation or not frappe.has_permission("Patient", "read", doc=doc.patient, throw=False):
            frappe.throw("Not permitted", frappe.PermissionError)
        patient = frappe.get_doc("Patient", doc.patient, for_update=False)
        if (patient.name != doc.patient or not patient.creation or
                not frappe.has_permission("Patient", "read", doc=patient, throw=False)):
            frappe.throw("Not permitted", frappe.PermissionError)
        appointment_creation, patient_creation = str(doc.creation), str(patient.creation)
    name = frappe.db.get_value(
        VISIT, {"appointment": doc.name}, "name", for_update=for_update
    )
    state = frappe.get_doc(VISIT, name, for_update=for_update) if name else None
    if state and (state.appointment != doc.name or state.patient != doc.patient or
                  state.appointment_snapshot != doc.name or
                  state.patient_snapshot != doc.patient or
                  state.appointment_creation != appointment_creation or
                  state.patient_creation != patient_creation):
        frappe.throw("Inconsistent visit", frappe.ValidationError)
    return doc, state


def change_consent(appointment, granted, evidence=None, *, attested=False):
    """Append audit and update consent atomically inside the caller's transaction."""
    if not isinstance(granted, bool):
        frappe.throw("Invalid consent", frappe.ValidationError)
    if granted:
        if attested is not True:
            frappe.throw("Staff attestation required", frappe.ValidationError)
        if not isinstance(evidence, str):
            frappe.throw("Consent evidence required", frappe.ValidationError)
        evidence = evidence.strip()
        folded = evidence.casefold()
        if (not 3 <= len(evidence) <= 140 or
            not any(char.isalnum() for char in evidence) or
            any(not char.isprintable() for char in evidence) or
            folded in SAMPLE_EVIDENCE or folded.startswith("sample-consent-") or
            folded.startswith("demo/consent/")):
            frappe.throw("Invalid consent evidence", frappe.ValidationError)
    actor.require_visit_actor()
    doc, state = lock_visit(appointment)
    frappe.db.savepoint("clinic_visit_consent")
    try:
        if state is None:
            appointment_creation, patient_creation = current_identity(doc)
            state = bookkeeping.insert({
                "doctype": VISIT, "appointment": doc.name, "patient": doc.patient,
                "appointment_snapshot": doc.name, "patient_snapshot": doc.patient,
                "appointment_creation": appointment_creation,
                "patient_creation": patient_creation,
                "consent_granted": 0,
            })
        audit = bookkeeping.insert({
            "doctype": "Clinic Visit Photo Consent Audit",
            "appointment": doc.name, "patient": doc.patient,
            "action": "Granted" if granted else "Revoked",
            "evidence": evidence if granted else "",
            "attested": int(granted),
            "actor": frappe.session.user,
        })
        if granted and (not isinstance(audit.name, str) or not audit.name or
                        audit.name.strip() != audit.name):
            frappe.throw("Invalid consent audit", frappe.ValidationError)
        state.consent_granted = int(granted)
        state.active_grant_audit = audit.name if granted else None
        bookkeeping.save(state)
    except Exception:
        frappe.db.rollback(save_point="clinic_visit_consent")
        raise
    return state


def has_checkin_witness(appointment, patient):
    doc = frappe.get_doc("Patient Appointment", appointment, for_update=True)
    if (doc.name != appointment or doc.patient != patient or
        not frappe.has_permission("Patient Appointment", "read", doc=doc)):
        frappe.throw("Not permitted", frappe.PermissionError)
    appointment_creation, patient_creation = current_identity(doc)
    row = frappe.db.get_value(
        "Clinic Visit Checkin Witness", {"appointment": appointment},
        ["patient", "appointment_snapshot", "patient_snapshot",
         "appointment_creation", "patient_creation"],
        as_dict=True, for_update=True,
    )
    if not row:
        return False
    if (row.patient != patient or row.appointment_snapshot != appointment or
        row.patient_snapshot != patient or
        row.appointment_creation != appointment_creation or
        row.patient_creation != patient_creation):
        frappe.throw("Inconsistent visit witness", frappe.ValidationError)
    return True
