"""Appointment-linked encounter workflow."""

from clinic_ui.core.text import is_valid_identifier


def _ref(value):
    if not isinstance(value, str) or not is_valid_identifier(value):
        raise ValueError("invalid appointment reference")
    return value


def _context(gateway, ref):
    return gateway.linked_context(gateway.linked_resolve(_ref(ref)))


def open_for_appointment(gateway, appointment_ref):
    context = _context(gateway, appointment_ref)
    return gateway.linked_project(context, gateway.linked_find(context))


def save_linked(gateway, payload):
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "expected_version", "clinical"}:
        raise ValueError("invalid request")
    clinical = payload["clinical"]
    if not isinstance(clinical, dict) or not set(clinical) <= {"symptoms", "diagnoses", "note"}:
        raise ValueError("invalid clinical payload")
    for field in ("symptoms", "diagnoses"):
        if field in clinical and (not isinstance(clinical[field], list) or any(not isinstance(v, str) or not is_valid_identifier(v) for v in clinical[field])):
            raise ValueError("invalid clinical rows")
    if "note" in clinical:
        note = clinical["note"]
        if not isinstance(note, dict) or set(note) != {"id", "modified", "text"} or not isinstance(note["text"], str) or not note["text"].strip():
            raise ValueError("invalid note")
        if (note["id"] is None) != (note["modified"] is None):
            raise ValueError("invalid note version")
        if note["id"] is not None and (not is_valid_identifier(note["id"]) or not isinstance(note["modified"], str) or not note["modified"].strip()):
            raise ValueError("invalid note version")
    context = _context(gateway, payload["appointment_ref"])
    encounter = gateway.linked_find(context)
    version = payload["expected_version"]
    if encounter is None:
        if version is not None:
            raise ValueError("stale encounter")
    elif encounter["docstatus"] != 0 or not isinstance(version, str) or version != encounter["modified"]:
        raise ValueError("stale encounter")
    encounter = gateway.linked_save(context, encounter, clinical)
    return gateway.linked_project(gateway.linked_context(context["appointment"]), encounter)


def complete_linked(gateway, payload):
    if not isinstance(payload, dict) or set(payload) != {"appointment_ref", "encounter_id", "expected_version"}:
        raise ValueError("invalid request")
    if not isinstance(payload["encounter_id"], str) or not is_valid_identifier(payload["encounter_id"]) or not isinstance(payload["expected_version"], str) or not payload["expected_version"].strip():
        raise ValueError("invalid encounter identity")
    context = _context(gateway, payload["appointment_ref"])
    encounter = gateway.linked_find(context)
    if encounter is None or encounter["docstatus"] != 0 or encounter["id"] != payload["encounter_id"] or encounter["modified"] != payload["expected_version"]:
        raise ValueError("stale encounter")
    encounter = gateway.linked_complete(context, encounter)
    return gateway.linked_project(gateway.linked_context(context["appointment"]), encounter)
