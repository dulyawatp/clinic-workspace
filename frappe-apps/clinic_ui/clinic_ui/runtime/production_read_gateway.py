"""Permission-filtered exact membership for production read endpoints."""

from datetime import datetime


def production_checkin_ready(gateway, appointment):
    """Probe current native authority and the reviewed Photos boundary."""
    try:
        from clinic_photos.visit.runtime import enabled_for_site
        from clinic_ui.core import appointment_contract

        frappe = gateway.frappe
        doc = frappe.get_doc("Patient Appointment", appointment)
        return (
            getattr(doc, "name", None) == appointment
            and frappe.has_permission(
                "Patient Appointment", ptype="write", doc=doc, user=frappe.session.user
            ) is True
            and enabled_for_site(gateway.site, frappe.conf) is True
            and appointment_contract.checkin_profile(gateway._checkin_profile_snapshot()) == "photo"
            and gateway.native_appointment_boundary_problem() is None
            and gateway.healthcare_appointment_side_effects_unsafe() is False
        )
    except Exception:
        return False


def patient_creation(gateway, name):
    """Read creation only after caller checked current Patient authority."""
    try:
        value = gateway.frappe.db.get_value("Patient", name, "creation")
        return value if isinstance(value, datetime) and value.tzinfo is None else None
    except Exception:
        return None


def exact_readable(gateway, doctype, name):
    """Fail closed unless one permission-filtered row exactly matches."""
    try:
        rows = gateway.frappe.get_list(
            doctype,
            filters={"name": name},
            fields=["name"],
            limit_page_length=2,
        )
        return (
            isinstance(rows, (list, tuple))
            and len(rows) == 1
            and isinstance(rows[0], dict)
            and set(rows[0]) == {"name"}
            and rows[0]["name"] == name
        )
    except Exception:
        return False


def _projected_fields_readable(gateway, doctype, fields):
    try:
        meta = gateway.frappe.get_meta(doctype)
        masked = meta.get_masked_fields()
        if not isinstance(masked, (list, tuple)) or any(
            not isinstance(getattr(item, "fieldname", None), str)
            or not item.fieldname.strip() for item in masked
        ):
            return False
        return gateway._fields_readable(meta, fields)
    except Exception:
        return False


def history_photo_appointment(gateway, encounter_id, patient_ref):
    """Project only a freshly authorized encounter's linked appointment."""
    from clinic_ui.core import production_patient_ref
    from clinic_ui.core.policy import PATIENT_VIEW_ROLE_CODES
    from clinic_ui.core.text import is_valid_text
    from clinic_ui.endpoints import production_read

    gateway.mark_no_store()
    gateway.suppress_traceback()
    if production_read.authorize_if_selected(gateway, PATIENT_VIEW_ROLE_CODES) is None:
        gateway.deny()
    if not is_valid_text(encounter_id, 140) or encounter_id != encounter_id.strip():
        gateway.deny()
    decoded = production_patient_ref.decode(
        production_read.patient_ref_key(gateway), gateway.site, patient_ref
    )
    if decoded is None:
        gateway.deny()
    patient, _ = decoded
    if (
        gateway._production_encounter_patient_ref(patient) != patient_ref
        or not _projected_fields_readable(gateway, "Patient", ("patient_name",))
    ):
        gateway.deny()
    if not exact_readable(gateway, "Patient Encounter", encounter_id):
        gateway.deny()
    frappe = gateway.frappe
    doc = frappe.get_doc("Patient Encounter", encounter_id)
    if (
        not frappe.has_permission("Patient Encounter", ptype="read", doc=doc, user=frappe.session.user)
        or not _projected_fields_readable(gateway, "Patient Encounter", ("patient", "appointment"))
        or gateway._row_value(doc, "name") != encounter_id
        or gateway._row_value(doc, "patient") != patient
    ):
        gateway.deny()
    appointment = gateway._row_value(doc, "appointment")
    if appointment is None or appointment == "":
        return {"appointment": None}
    if (
        not is_valid_text(appointment, 140)
        or appointment != appointment.strip()
        or not exact_readable(gateway, "Patient Appointment", appointment)
        or not _projected_fields_readable(gateway, "Patient Appointment", ("patient",))
    ):
        gateway.deny()
    row = gateway.native_appointment_view(appointment)
    if not isinstance(row, dict) or row.get("patient") != patient:
        gateway.deny()
    return {"appointment": appointment}
