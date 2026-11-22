"""Private visit upload intake; no HTTP entry point."""

from clinic_photos.publication import journal, validation
from clinic_photos.publication.contracts import (
    PublicationValidationError, SaveRequest, VisitBinding, VISIT_ORIGINAL_CREATE,
)
from clinic_photos.visit import actor, capture, state
from clinic_photos.visit.runtime import enabled_for_site

_FIELDS = {"appointment", "slot", "body_area", "viewpoint", "request_key", "image_base64"}


def stage_visit_upload(payload, *, on_created=None):
    """Stage a verified visit request in the caller's transaction, never commit."""
    import frappe

    if type(payload) is not dict or payload.get("slot") not in ("Before", "After"):
        raise PublicationValidationError("Invalid visit photo request")
    fields = _FIELDS | ({"encounter"} if payload["slot"] == "After" else set())
    if set(payload) != fields:
        raise PublicationValidationError("Invalid visit photo request")
    for field in ("appointment", "body_area", "viewpoint"):
        value = validation.require_reference(payload[field])
        if not value.isprintable():
            raise PublicationValidationError("Invalid visit photo request")
    validation.require_request_key(payload["request_key"])
    if payload["slot"] == "After":
        encounter_name = validation.require_reference(payload["encounter"])
        if not encounter_name.isprintable():
            raise PublicationValidationError("Invalid visit photo request")
    else:
        encounter_name = None
    image = validation.decode_image_base64(payload["image_base64"])
    validation.validate_image_bytes(image)

    site = getattr(frappe.local, "site", None)
    if not enabled_for_site(site, getattr(frappe, "conf", None)):
        raise frappe.PermissionError("Visit photo capture is not permitted")
    user = actor.require_visit_actor(doctor=payload["slot"] == "After")
    if user != getattr(frappe.session, "user", None):
        raise frappe.PermissionError("Visit photo capture is not permitted")

    encounter = None
    if encounter_name:
        encounter = frappe.get_doc("Patient Encounter", encounter_name, for_update=True)
        if encounter.name != encounter_name or not encounter.creation:
            raise frappe.PermissionError("Visit photo capture is not permitted")
    appointment, visit = state.lock_visit(payload["appointment"])
    if (appointment.name != payload["appointment"] or not appointment.patient or
        not appointment.creation or not visit or visit.consent_granted != 1 or
        visit.appointment_creation != str(appointment.creation) or
        not visit.patient_creation or not visit.active_grant_audit):
        raise frappe.PermissionError("Visit photo capture is not permitted")

    binding = VisitBinding(
        appointment=appointment.name, patient=appointment.patient, slot=payload["slot"],
        encounter=encounter.name if encounter else None,
        body_area=payload["body_area"], viewpoint=payload["viewpoint"],
        appointment_creation=str(appointment.creation),
        patient_creation=visit.patient_creation,
        encounter_creation=str(encounter.creation) if encounter else None,
        grant_audit=visit.active_grant_audit,
    )
    request = SaveRequest(
        kind=VISIT_ORIGINAL_CREATE, request_key=payload["request_key"],
        image_bytes=image, visit_binding=binding,
    )
    # A filled slot is always a refusal, including a replay; use status/readback.
    capture.check_slot_availability(binding)
    if on_created is None:
        return journal.stage_submission(request, actor=user, site=site)
    return journal.stage_submission(request, actor=user, site=site, on_created=on_created)
