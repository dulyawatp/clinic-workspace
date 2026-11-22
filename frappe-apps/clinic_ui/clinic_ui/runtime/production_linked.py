"""Current, permission-filtered identity for production linked visits."""

from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
from clinic_ui.runtime.production_read_gateway import exact_readable, _projected_fields_readable


def _identity(value):
    return is_valid_text(value, 140) and value == value.strip()


def lock_current_linked(gateway, native_appointment_id):
    """Lock current linked encounter before its exact appointment.

    Locks remain owned by the surrounding native request transaction.
    Callers must revalidate authority and witness using these locked documents.
    """
    if not _identity(native_appointment_id):
        raise LinkedEncounterError("linked visit unavailable")
    frappe = gateway.frappe
    rows = frappe.db.sql(
        "select name from `tabPatient Encounter` where appointment = %s and docstatus != 2 order by name limit 2 for update",
        (native_appointment_id,),
    )
    if not isinstance(rows, (list, tuple)) or len(rows) > 1:
        raise LinkedEncounterError("ambiguous linked encounter")
    encounter = None
    if rows:
        row = rows[0]
        if not isinstance(row, (list, tuple)) or len(row) != 1 or not _identity(row[0]):
            raise LinkedEncounterError("linked visit unavailable")
        encounter = frappe.get_doc("Patient Encounter", row[0], for_update=True)
        encounter.check_permission("read")
        if (
            encounter.name != row[0]
            or encounter.appointment != native_appointment_id
            or not encounter.creation
            or encounter.docstatus not in (0, 1)
            or not _identity(encounter.patient)
            or not _identity(encounter.practitioner)
        ):
            raise LinkedEncounterError("linked visit unavailable")
    appointment = frappe.get_doc(
        "Patient Appointment", native_appointment_id, for_update=True
    )
    appointment.check_permission("read")
    if (
        appointment.name != native_appointment_id
        or not _identity(appointment.patient)
        or not _identity(appointment.practitioner)
        or (encounter is not None and (
            encounter.patient != appointment.patient
            or encounter.practitioner != appointment.practitioner
        ))
    ):
        raise LinkedEncounterError("linked visit unavailable")
    return appointment, encounter


def project_saved_draft(gateway, context, patient_ref, encounter_doc):
    """Project the saved current document without a snapshot encounter reload."""
    try:
        if (
            not _identity(patient_ref)
            or not _identity(encounter_doc.name)
            or encounter_doc.appointment != context["appointment"]
            or encounter_doc.patient != context["patient"]
            or encounter_doc.practitioner != context["practitioner"]
            or encounter_doc.docstatus != 0
            or not encounter_doc.modified
        ):
            raise ValueError()
        encounter = {
            "id": encounter_doc.name,
            "modified": str(encounter_doc.modified),
            "docstatus": 0,
        }
        result = gateway._linked_project_doc(context, encounter, encounter_doc)
        if not isinstance(result, dict) or not isinstance(result.get("context"), dict) or result["context"].get("patient") != context["patient"]:
            raise ValueError()
        result["context"]["patient"] = patient_ref
        return result
    except Exception:
        raise LinkedEncounterError("linked visit unavailable") from None


def read_context(gateway, native_appointment_id, actor, *, locked_appointment=None):
    """Return internal context and opaque patient reference; never publish context."""
    try:
        frappe = gateway.frappe
        if (
            not _identity(native_appointment_id)
            or not _identity(actor)
            or actor != frappe.session.user
            or not exact_readable(gateway, "Patient Appointment", native_appointment_id)
        ):
            raise ValueError()
        if locked_appointment is None:
            context = gateway.linked_context(native_appointment_id)
        else:
            if locked_appointment.name != native_appointment_id:
                raise ValueError()
            context = gateway._linked_context_from_doc(locked_appointment)
        if (
            not isinstance(context, dict)
            or context.get("appointment") != native_appointment_id
            or not _identity(context.get("patient"))
            or not _identity(context.get("practitioner"))
            or context.get("status") not in ("Checked In", "Closed")
            or context.get("_alternative") is not False
        ):
            raise ValueError()
        patient_ref = gateway._production_encounter_patient_ref(context["patient"])
        if not isinstance(patient_ref, str) or not patient_ref:
            raise ValueError()
        if locked_appointment is not None:
            from clinic_ui.core import production_patient_ref
            from clinic_ui.endpoints import production_read

            patient = frappe.get_doc("Patient", context["patient"], for_update=True)
            patient.check_permission("read")
            if patient.name != context["patient"] or not patient.creation:
                raise ValueError()
            current_ref = production_patient_ref.encode(
                production_read.patient_ref_key(gateway), gateway.site,
                patient.name, patient.creation,
            )
            if current_ref != patient_ref:
                raise ValueError()
        if not _projected_fields_readable(
            gateway, "Healthcare Practitioner", ("user_id", "status")
        ):
            raise ValueError()
        rows = frappe.get_list(
            "Healthcare Practitioner",
            filters={"user_id": actor, "status": "Active"},
            fields=["name", "user_id", "status"],
            limit_page_length=2,
        )
        if (
            not isinstance(rows, (list, tuple))
            or len(rows) != 1
            or not isinstance(rows[0], dict)
            or rows[0].get("name") != context["practitioner"]
            or rows[0].get("user_id") != actor
            or rows[0].get("status") != "Active"
        ):
            raise ValueError()
        if locked_appointment is not None:
            practitioner = frappe.get_doc(
                "Healthcare Practitioner", context["practitioner"], for_update=True
            )
            practitioner.check_permission("read")
            if (
                practitioner.name != context["practitioner"]
                or practitioner.user_id != actor
                or practitioner.status != "Active"
            ):
                raise ValueError()
        from clinic_photos.visit.state import has_checkin_witness
        if has_checkin_witness(native_appointment_id, context["patient"]) is not True:
            raise ValueError()
        return context, patient_ref
    except Exception:
        raise LinkedEncounterError("linked visit unavailable") from None
