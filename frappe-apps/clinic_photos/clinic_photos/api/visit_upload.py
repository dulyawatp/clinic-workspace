"""Authenticated, read-only reconciliation for one visit capture request."""

from clinic_photos.access import frappe_context
from clinic_photos.api import photo_save
from clinic_photos.api.visit_photos import _valid_dispatch_extra
from clinic_photos.publication import journal, validation
from clinic_photos.publication.contracts import VISIT_ORIGINAL_CREATE, VisitBinding
from clinic_photos.visit.runtime import enabled_for_site

SAFE_DENIAL = "Visit photo request unavailable"
SAFE_VALIDATION = "Invalid visit photo request"


def _stage_visit_upload(payload, *, on_created):
    from clinic_photos.visit.intake import stage_visit_upload

    return stage_visit_upload(payload, on_created=on_created)


def _enqueue_visit(operation_id):
    from clinic_photos.publication.launcher import enqueue_visit_submission

    enqueue_visit_submission(operation_id)


def capture(payload=None, **extra):
    """Stage one private visit capture; notification runs only after commit."""
    frappe = photo_save._frappe()
    photo_save.require_post()
    photo_save.require_native_session()
    actor = getattr(getattr(frappe, "session", None), "user", None)
    site = getattr(frappe.local, "site", None)
    if (not isinstance(actor, str) or not actor or actor == "Guest" or
        not enabled_for_site(site, getattr(frappe, "conf", None))):
        raise frappe.PermissionError(SAFE_DENIAL)
    if (type(payload) is not dict or
        not _valid_dispatch_extra(extra, "visit_upload.capture") or
        payload.get("slot") not in ("Before", "After")):
        raise frappe.ValidationError(SAFE_VALIDATION)
    fields = {"appointment", "slot", "body_area", "viewpoint", "request_key", "image_base64"}
    if payload["slot"] == "After":
        fields.add("encounter")
    if set(payload) != fields:
        raise frappe.ValidationError(SAFE_VALIDATION)
    photo_save.require_production_workspace(
        frozenset({"DOC"}) if payload["slot"] == "After" else frozenset({"DOC", "REC", "ADM"})
    )

    def register_notification(submitted):
        attempted = False

        def notify():
            nonlocal attempted
            if attempted:
                return
            attempted = True
            try:
                _enqueue_visit(submitted.id)
            except Exception:
                # The committed submission remains pending; this callback never retries.
                pass

        frappe.db.after_commit.add(notify)

    try:
        _stage_visit_upload(payload, on_created=register_notification)
    except validation.PublicationValidationError:
        raise frappe.ValidationError(SAFE_VALIDATION) from None
    except Exception:
        # Escaping the request is required to roll back a potentially inserted row.
        raise frappe.PermissionError(SAFE_DENIAL) from None
    return {"state": "pending"}


def _read_pair(appointment):
    from clinic_photos.visit.pair import read_pair

    return read_pair(appointment)


def capture_status(payload=None, **extra):
    """Return only bounded status for the current actor's exact request tuple."""
    frappe = photo_save._frappe()
    photo_save.require_post()
    photo_save.require_native_session()
    actor = getattr(getattr(frappe, "session", None), "user", None)
    site = getattr(frappe.local, "site", None)
    if (not isinstance(actor, str) or not actor or actor == "Guest" or
        not enabled_for_site(site, getattr(frappe, "conf", None))):
        raise frappe.PermissionError(SAFE_DENIAL)
    if (type(payload) is not dict or set(payload) != {"appointment", "slot", "request_key"}
        or not _valid_dispatch_extra(extra, "visit_upload.capture_status")):
        raise frappe.ValidationError(SAFE_VALIDATION)
    try:
        appointment_name = validation.require_reference(payload["appointment"])
        request_key = validation.require_request_key(payload["request_key"])
        if (not all(char.isprintable() for char in appointment_name) or
            payload["slot"] not in ("Before", "After")):
            raise ValueError
    except Exception:
        raise frappe.ValidationError(SAFE_VALIDATION) from None
    photo_save.require_production_workspace(
        frozenset({"DOC"}) if payload["slot"] == "After" else frozenset({"DOC", "REC", "ADM"})
    )

    try:
        if (type(getattr(frappe.db, "transaction_writes", None)) is not int or
            frappe.db.transaction_writes != 0):
            raise ValueError
        appointment = frappe.get_doc("Patient Appointment", appointment_name)
        if (appointment.name != appointment_name or not appointment.patient or not appointment.creation or
            not frappe.has_permission("Patient Appointment", "read", doc=appointment)):
            raise ValueError
        patient = frappe.get_doc("Patient", appointment.patient)
        if (patient.name != appointment.patient or not patient.creation or
            not frappe.has_permission("Patient", "read", doc=patient)):
            raise ValueError
    except Exception:
        raise frappe.PermissionError(SAFE_DENIAL) from None

    try:
        identity = journal.identity_digest(site, actor, VISIT_ORIGINAL_CREATE, request_key)
        row = frappe_context.get_save_operation_by_identity(identity)
        if row is None:
            return {"state": "not_found"}
        operation = journal.load_verified_operation(row.get("name"))
        if (operation.id != row.get("name") or operation.actor != actor or operation.site != site or
            operation.kind != VISIT_ORIGINAL_CREATE or operation.request_key != request_key):
            return {"state": "not_found"}
        binding = operation.request.visit_binding
        if (type(binding) is not VisitBinding or binding.appointment != appointment.name or
            binding.patient != patient.name or binding.slot != payload["slot"] or
            binding.appointment_creation != str(appointment.creation) or
            binding.patient_creation != str(patient.creation)):
            return {"state": "not_found"}
        if operation.state == "SUBMITTED":
            return {"state": "pending"}
        if operation.state == "ABORTED":
            return {"state": "rejected"}
        if operation.state != "PUBLISHED" or not operation.published:
            return {"state": "unknown"}
        from clinic_photos.visit.pair import _read_slot_structure

        linked = _read_slot_structure(appointment, patient, binding.slot)
        if not linked:
            return {"state": "unknown"}
        record, photo, file = linked
        if (record.operation != operation.id or operation.published.domain_doctype != "Clinic Photo" or
            photo.name != operation.published.domain_name or
            file.name != operation.published.file_name):
            return {"state": "unknown"}
        return {"state": "saved"}
    except Exception:
        return {"state": "unknown"}


def _register_site_contract():
    try:
        import frappe
    except ModuleNotFoundError:
        return
    global capture, capture_status
    capture = frappe.whitelist(methods=["POST"])(capture)
    capture_status = frappe.whitelist(methods=["POST"])(capture_status)


_register_site_contract()
