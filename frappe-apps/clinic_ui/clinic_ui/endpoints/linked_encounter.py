"""Appointment-linked encounter workflow."""

from clinic_ui.core.text import is_valid_identifier
from clinic_ui.endpoints import production_read


class LinkedEncounterError(ValueError):
    """Invalid input or an invalid native linked-workflow state."""


def _ref(value):
    if not isinstance(value, str) or not is_valid_identifier(value):
        raise LinkedEncounterError("invalid appointment reference")
    return value


def _context(gateway, ref):
    return gateway.linked_context(gateway.linked_resolve(_ref(ref)))


def _deny_production_until_linked_read_is_ready(gateway):
    if production_read.authorize_if_selected(gateway, frozenset({"DOC"})) is not None:
        gateway.deny()


def open_for_appointment(gateway, appointment_ref, note_offset=0):
    production = production_read.authorize_if_selected(gateway, frozenset({"DOC"}))
    if isinstance(note_offset, bool) or not isinstance(note_offset, (int, str)):
        raise LinkedEncounterError("invalid note offset")
    if isinstance(note_offset, str):
        if len(note_offset) > 18 or not note_offset.isascii() or not note_offset.isdecimal():
            raise LinkedEncounterError("invalid note offset")
        note_offset = int(note_offset)
    if note_offset < 0:
        raise LinkedEncounterError("invalid note offset")
    if production is None:
        context = _context(gateway, appointment_ref)
        return gateway.linked_project(context, gateway.linked_find(context), note_offset)
    from clinic_ui.runtime.production_linked import read_context

    context, patient_ref = read_context(gateway, appointment_ref, production[1].user)
    encounter = gateway.linked_find(context)
    if context["status"] == "Closed" and (
        not isinstance(encounter, dict) or encounter.get("docstatus") != 0
    ):
        raise LinkedEncounterError("linked visit unavailable")
    result = gateway.linked_project(context, encounter, note_offset)
    if (
        not isinstance(result, dict)
        or not isinstance(result.get("context"), dict)
        or result["context"].get("patient") != context["patient"]
    ):
        raise LinkedEncounterError("linked visit unavailable")
    result["context"]["patient"] = patient_ref
    return result


def save_linked(gateway, payload):
    production = production_read.authorize_if_selected(gateway, frozenset({"DOC"}))
    if production is not None and gateway.setting("clinic_ui_production_linked_draft_enabled") is not True:
        gateway.deny()
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "expected_version", "clinical"}:
        raise LinkedEncounterError("invalid request")
    clinical = payload["clinical"]
    if production is not None:
        appointment_ref = _ref(payload["appointment_ref"])
        if appointment_ref != appointment_ref.strip():
            raise LinkedEncounterError("invalid appointment reference")
        if not isinstance(clinical, dict) or not set(clinical) <= {"symptoms", "diagnoses"}:
            raise LinkedEncounterError("invalid clinical payload")
        for values in clinical.values():
            if not isinstance(values, list) or any(
                not isinstance(value, str) or not is_valid_identifier(value) for value in values
            ):
                raise LinkedEncounterError("invalid clinical rows")
        profile = gateway.frappe.db.sql(
            "SELECT @@session.tx_isolation AS isolation, "
            "@@innodb_snapshot_isolation AS snapshot",
            as_dict=True,
        )
        if (
            not isinstance(profile, (list, tuple)) or len(profile) != 1
            or not isinstance(profile[0], dict)
            or profile[0].get("isolation") != "REPEATABLE-READ"
            or type(profile[0].get("snapshot")) not in (int, float)
            or profile[0]["snapshot"] != 0
        ):
            raise LinkedEncounterError("unsupported transaction profile")
        from clinic_ui.runtime.production_linked import lock_current_linked, project_saved_draft, read_context
        from clinic_ui.runtime.production_linked_save import save_locked_draft

        appointment, encounter = lock_current_linked(gateway, appointment_ref)
        context, patient_ref = read_context(
            gateway, appointment_ref, production[1].user, locked_appointment=appointment
        )
        saved = save_locked_draft(
            gateway, appointment, encounter, context, payload["expected_version"], clinical
        )
        current = gateway.frappe.get_doc("Patient Appointment", appointment_ref, for_update=True)
        current_context, current_patient_ref = read_context(
            gateway, appointment_ref, production[1].user, locked_appointment=current
        )
        if (
            (context.get("status"), current_context.get("status")) not in (
                ("Checked In", "Checked In"), ("Checked In", "Closed"), ("Closed", "Closed")
            )
            or {key: value for key, value in current_context.items() if key != "status"}
            != {key: value for key, value in context.items() if key != "status"}
            or current_patient_ref != patient_ref
        ):
            raise LinkedEncounterError("linked visit unavailable")
        return project_saved_draft(gateway, current_context, current_patient_ref, saved)
    if not isinstance(clinical, dict) or not set(clinical) <= {"symptoms", "diagnoses", "note", "medications"}:
        raise LinkedEncounterError("invalid clinical payload")
    if "medications" in clinical:
        operations = clinical["medications"]
        if not isinstance(operations, dict) or set(operations) != {"add", "edit"}:
            raise LinkedEncounterError("invalid medication operations")
        for action, keys in (
            ("add", {"drug_code", "dosage", "period", "dosage_form"}),
            ("edit", {"id", "dosage", "period", "dosage_form"}),
        ):
            rows = operations[action]
            if not isinstance(rows, list) or len(rows) > 100:
                raise LinkedEncounterError("invalid medication operations")
            for row in rows:
                if not isinstance(row, dict) or set(row) != keys or any(
                    not is_valid_identifier(value) or value != value.strip()
                    for value in row.values()
                ):
                    raise LinkedEncounterError("invalid medication row")
        ids = [row["id"] for row in operations["edit"]]
        if len(ids) != len(set(ids)):
            raise LinkedEncounterError("repeated medication edit")
    for field in ("symptoms", "diagnoses"):
        if field in clinical and (not isinstance(clinical[field], list) or any(not isinstance(v, str) or not is_valid_identifier(v) for v in clinical[field])):
            raise LinkedEncounterError("invalid clinical rows")
    if "note" in clinical:
        note = clinical["note"]
        if not isinstance(note, dict) or set(note) != {"id", "modified", "text"} or not isinstance(note["text"], str) or not note["text"].strip():
            raise LinkedEncounterError("invalid note")
        if (note["id"] is None) != (note["modified"] is None):
            raise LinkedEncounterError("invalid note version")
        if note["id"] is not None and (not is_valid_identifier(note["id"]) or not isinstance(note["modified"], str) or not note["modified"].strip()):
            raise LinkedEncounterError("invalid note version")
    context = _context(gateway, payload["appointment_ref"])
    encounter = gateway.linked_find(context)
    version = payload["expected_version"]
    if encounter is None:
        if version is not None:
            raise LinkedEncounterError("stale encounter")
    elif encounter["docstatus"] != 0 or not isinstance(version, str) or version != encounter["modified"]:
        raise LinkedEncounterError("stale encounter")
    encounter = gateway.linked_save(context, encounter, clinical)
    return gateway.linked_project(gateway.linked_context(context["appointment"]), encounter)


def complete_linked(gateway, payload):
    _deny_production_until_linked_read_is_ready(gateway)
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "encounter_id", "expected_version"}:
        raise LinkedEncounterError("invalid request")
    if not isinstance(payload["encounter_id"], str) or not is_valid_identifier(payload["encounter_id"]) or not isinstance(payload["expected_version"], str) or not payload["expected_version"].strip():
        raise LinkedEncounterError("invalid encounter identity")
    context = _context(gateway, payload["appointment_ref"])
    encounter = gateway.linked_find(context)
    if encounter is None or encounter["docstatus"] != 0 or encounter["id"] != payload["encounter_id"] or encounter["modified"] != payload["expected_version"]:
        raise LinkedEncounterError("stale encounter")
    encounter = gateway.linked_complete(context, encounter)
    return gateway.linked_project(gateway.linked_context(context["appointment"]), encounter)
