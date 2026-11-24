"""Advisory, read-only visit capture capabilities."""

from clinic_photos.api import photo_save
from clinic_photos.api.visit_photos import _valid_dispatch_extra
from clinic_photos.publication import validation
from clinic_photos.visit import actor, capture, state
from clinic_photos.visit.runtime import enabled_for_site

SAFE_DENIAL = "Visit photo request unavailable"
SAFE_VALIDATION = "Invalid visit photo request"


def _read_pair(appointment):
    from clinic_photos.visit.pair import read_pair

    return read_pair(appointment)


def capture_options(payload=None, **extra):
    """Return advisory booleans; capture still performs its full locked checks."""
    frappe = photo_save._frappe()
    photo_save.require_post()
    photo_save.require_native_session()
    workspace = photo_save.require_production_workspace(frozenset({"DOC", "REC", "ADM"}))
    user = getattr(getattr(frappe, "session", None), "user", None)
    if (not isinstance(user, str) or not user or user == "Guest" or
        not enabled_for_site(getattr(frappe.local, "site", None), getattr(frappe, "conf", None))):
        raise frappe.PermissionError(SAFE_DENIAL)
    if (type(payload) is not dict or not _valid_dispatch_extra(extra, "visit_options.capture_options") or
        set(payload) not in ({"appointment"}, {"appointment", "encounter"})):
        raise frappe.ValidationError(SAFE_VALIDATION)
    try:
        appointment_name = validation.require_reference(payload["appointment"])
        encounter_name = (validation.require_reference(payload["encounter"])
                          if "encounter" in payload else None)
        if (not appointment_name.isprintable() or
            (encounter_name is not None and not encounter_name.isprintable())):
            raise ValueError
    except Exception:
        raise frappe.ValidationError(SAFE_VALIDATION) from None

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
        from clinic_photos.visit.pair import _read_slot_structure
        before_slot = _read_slot_structure(appointment, patient, "Before")
        after_slot = _read_slot_structure(appointment, patient, "After")
        if after_slot and (not before_slot or
            before_slot[0].body_area != after_slot[0].body_area or
            before_slot[0].viewpoint != after_slot[0].viewpoint):
            raise ValueError
        encounter = None
        if encounter_name:
            encounter = frappe.get_doc("Patient Encounter", encounter_name)
            if (encounter.name != encounter_name or encounter.appointment != appointment.name or
                encounter.patient != patient.name or not encounter.creation or
                not frappe.has_permission("Patient Encounter", "read", doc=encounter)):
                raise ValueError
        state_name = frappe.db.get_value(state.VISIT, {"appointment": appointment.name}, "name")
        visit = frappe.get_doc(state.VISIT, state_name) if state_name else None
        if visit and (visit.appointment != appointment.name or visit.patient != patient.name or
            visit.appointment_snapshot != appointment.name or visit.patient_snapshot != patient.name or
            visit.appointment_creation != str(appointment.creation) or
            visit.patient_creation != str(patient.creation)):
            raise ValueError
        active = bool(visit and visit.consent_granted == 1)
        if active:
            grant = visit.active_grant_audit
            audit = frappe.get_doc("Clinic Visit Photo Consent Audit", grant)
            if (not grant or audit.name != grant or audit.appointment != appointment.name or
                audit.patient != patient.name or audit.action != "Granted" or audit.attested != 1):
                raise ValueError
        elif visit and (visit.consent_granted != 0 or visit.active_grant_audit):
            raise ValueError
    except Exception:
        raise frappe.PermissionError(SAFE_DENIAL) from None

    def authorized(slot, selected_encounter):
        try:
            capture._check_visit_authority(
                appointment.name, patient.name, slot,
                selected_encounter.name if selected_encounter else None,
                str(selected_encounter.creation) if selected_encounter else None,
                str(appointment.creation), str(patient.creation), visit.active_grant_audit,
            )
            return True
        except Exception:
            return False

    # After first preserves the native actor -> encounter -> visit lock order.
    after = False
    if (workspace is None or workspace == "DOC") and active and encounter and before_slot and not after_slot:
        try:
            projection = _read_pair(appointment.name)
            after = bool(projection["appointment"] == appointment.name and
                         projection["patient"] == patient.name and projection["before"] and
                         projection["before"]["photo"] == before_slot[1].name and
                         projection["before"]["file"] == before_slot[2].name and
                         not projection["after"] and authorized("After", encounter))
        except Exception:
            after = False
    before = bool(active and not before_slot and not after_slot
                  and authorized("Before", None))
    try:
        actor.require_visit_actor()
        locked_appointment, locked_visit = state.lock_visit(appointment.name)
        may_change = (locked_appointment.name == appointment.name and
                      bool(locked_visit) == bool(visit) and
                      (not visit or (locked_visit.consent_granted == visit.consent_granted and
                       locked_visit.active_grant_audit == visit.active_grant_audit)))
    except Exception:
        may_change = False
    return {
        "has_active_consent": active,
        "can_grant": bool(may_change and not active),
        "can_revoke": bool(may_change and active),
        "can_capture_before": before,
        "can_capture_after": after,
    }


def _register_site_contract():
    try:
        import frappe
    except ModuleNotFoundError:
        return
    global capture_options
    capture_options = frappe.whitelist(methods=["POST"])(capture_options)


_register_site_contract()
