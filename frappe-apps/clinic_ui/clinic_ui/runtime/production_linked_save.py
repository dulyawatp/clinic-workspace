"""Save a narrowly scoped clinical draft using already locked native documents."""

from clinic_ui.core.text import is_valid_identifier
from clinic_ui.endpoints.linked_encounter import LinkedEncounterError


_MAPPING = (
    ("name", "appointment"),
    ("patient", "patient"),
    ("practitioner", "practitioner"),
    ("department", "medical_department"),
    ("patient_sex", "patient_sex"),
    ("invoiced", "invoiced"),
    ("company", "company"),
    ("appointment_type", "appointment_type"),
    ("insurance_policy", "insurance_policy"),
    ("insurance_coverage", "insurance_coverage"),
)
_SOURCE_FIELDS = tuple(source for source, _ in _MAPPING) + (
    "modified", "creation", "docstatus", "status", "procedure_template",
    "therapy_type", "template_dt",
)


def save_locked_draft(gateway, appointment_doc, encounter_doc, context,
                      expected_version, clinical):
    if (not isinstance(clinical, dict)
            or not set(clinical) <= {"symptoms", "diagnoses"}):
        raise LinkedEncounterError("invalid clinical payload")
    for values in clinical.values():
        if (not isinstance(values, list)
                or any(not isinstance(value, str) or not is_valid_identifier(value)
                       for value in values)):
            raise LinkedEncounterError("invalid clinical rows")
    if (not isinstance(context, dict)
            or context.get("_alternative") is not False
            or any(getattr(appointment_doc, field, None) != context.get(key)
                   for field, key in (("name", "appointment"), ("patient", "patient"),
                                      ("practitioner", "practitioner"), ("status", "status")))
            or not appointment_doc.creation or not appointment_doc.modified
            or appointment_doc.docstatus not in (0, 1)
            or getattr(appointment_doc, "procedure_template", None)
            or getattr(appointment_doc, "therapy_type", None)
            or getattr(appointment_doc, "template_dt", None) in
            ("Therapy Type", "Clinical Procedure Template", "Observation Template")):
        raise LinkedEncounterError("linked visit unavailable")
    appointment_doc.check_permission("read")
    gateway._linked_fields("Patient Appointment", (
        "patient", "practitioner", "department", "patient_sex", "invoiced",
        "company", "appointment_type", "insurance_policy", "insurance_coverage",
        "status", "procedure_template", "therapy_type", "template_dt",
    ), "read")
    if encounter_doc is None:
        if expected_version is not None or appointment_doc.status != "Checked In":
            raise LinkedEncounterError("stale encounter")
        snapshot = gateway.frappe.get_doc("Patient Appointment", appointment_doc.name)
        snapshot.check_permission("read")
        try:
            locked_preimage = appointment_doc.as_dict()
            snapshot_preimage = snapshot.as_dict()
            if (not isinstance(locked_preimage, dict) or not locked_preimage
                    or not isinstance(snapshot_preimage, dict)
                    or locked_preimage != snapshot_preimage):
                raise ValueError()
        except Exception:
            raise LinkedEncounterError("stale appointment") from None
        if any(getattr(snapshot, field, None) != getattr(appointment_doc, field, None)
               for field in _SOURCE_FIELDS):
            raise LinkedEncounterError("stale appointment")
        doc = gateway._linked_map(context)
        if (getattr(doc, "docstatus", None) != 0
                or any(getattr(doc, target, None) != getattr(appointment_doc, source, None)
                       for source, target in _MAPPING)):
            raise LinkedEncounterError("mapped identity mismatch")
        doc.check_permission("create")
    else:
        doc = encounter_doc
        if (appointment_doc.status not in ("Checked In", "Closed")
                or not doc.creation or not doc.name or doc.docstatus != 0
                or not isinstance(expected_version, str)
                or not expected_version or str(doc.modified) != expected_version
                or any(getattr(doc, field, None) != context[key]
                       for field, key in (("appointment", "appointment"), ("patient", "patient"),
                                          ("practitioner", "practitioner")))):
            raise LinkedEncounterError("stale encounter")
        doc.check_permission("read")
        doc.check_permission("write")
    for key, table, child, field in (
        ("symptoms", "symptoms", "Patient Encounter Symptom", "complaint"),
        ("diagnoses", "diagnosis", "Patient Encounter Diagnosis", "diagnosis"),
    ):
        if key in clinical:
            read_meta = gateway._linked_fields("Patient Encounter", (table,), "read")
            gateway._linked_fields(child, (field,), "read", read_meta)
            meta = gateway._linked_fields("Patient Encounter", (table,), "write")
            gateway._linked_fields(child, (field,), "write", meta)
            doc.set(table, [])
            for value in clinical[key]:
                doc.append(table, {field: value})
    gateway._linked_fields("Patient Encounter", ("submit_orders_on_save",), "write")
    doc.submit_orders_on_save = 0
    old_name = doc.name
    gateway._persist_linked_draft(doc, encounter_doc is None)
    if (not doc.name or not doc.modified or doc.docstatus != 0
            or (encounter_doc is not None and
                (doc.name != old_name or str(doc.modified) == expected_version))
            or any(getattr(doc, field, None) != context[key]
                   for field, key in (("appointment", "appointment"), ("patient", "patient"),
                                      ("practitioner", "practitioner")))):
        raise LinkedEncounterError("invalid saved encounter")
    return doc
