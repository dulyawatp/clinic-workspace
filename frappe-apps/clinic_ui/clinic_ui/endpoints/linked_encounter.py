"""Appointment-linked encounter workflow."""

from clinic_ui.core.text import is_valid_identifier


class LinkedEncounterError(ValueError):
    """Invalid input or an invalid native linked-workflow state."""


def _ref(value):
    if not isinstance(value, str) or not is_valid_identifier(value):
        raise LinkedEncounterError("invalid appointment reference")
    return value


def _context(gateway, ref):
    return gateway.linked_context(gateway.linked_resolve(_ref(ref)))


def open_for_appointment(gateway, appointment_ref, note_offset=0):
    if isinstance(note_offset, bool) or not isinstance(note_offset, (int, str)):
        raise LinkedEncounterError("invalid note offset")
    if isinstance(note_offset, str):
        if len(note_offset) > 18 or not note_offset.isascii() or not note_offset.isdecimal():
            raise LinkedEncounterError("invalid note offset")
        note_offset = int(note_offset)
    if note_offset < 0:
        raise LinkedEncounterError("invalid note offset")
    context = _context(gateway, appointment_ref)
    return gateway.linked_project(context, gateway.linked_find(context), note_offset)


def save_linked(gateway, payload):
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "expected_version", "clinical"}:
        raise LinkedEncounterError("invalid request")
    clinical = payload["clinical"]
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
