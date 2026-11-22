import base64
from dataclasses import replace

import pytest

from clinic_photos.publication import contracts
from clinic_photos.publication.validation import PublicationValidationError


IMAGE = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)


def binding(**changes):
    values = dict(
        appointment="APT-1", patient="PAT-1", slot="After", encounter="ENC-1",
        body_area="Arm", viewpoint="Front", appointment_creation="2026-01-01 10:00:00",
        patient_creation="2026-01-01 09:00:00",
        encounter_creation="2026-01-01 11:00:00", grant_audit="AUDIT-1",
    )
    values.update(changes)
    return contracts.VisitBinding(**values)


def request(**changes):
    values = dict(kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-key-0000001",
                  visit_binding=binding(), image_bytes=IMAGE)
    values.update(changes)
    return contracts.SaveRequest(**values)


def test_visit_binding_is_immutable_and_after_requires_exact_encounter():
    value = binding()
    with pytest.raises(AttributeError):
        value.slot = "Before"
    for changes in ({"encounter": None}, {"encounter_creation": None}, {"slot": "other"}):
        with pytest.raises(PublicationValidationError):
            binding(**changes)
    before = binding(slot="Before", encounter=None, encounter_creation=None)
    assert before.slot == "Before"
    with pytest.raises(PublicationValidationError):
        replace(before, encounter="ENC-1")


def test_visit_rejects_mixed_legacy_fields_and_requires_bound_image():
    for field, value in (("draft", "D"), ("expected_version", 0), ("original", "P"),
                         ("annotation", "{}"), ("synthetic_patient", "P"),
                         ("visit_binding", None), ("image_bytes", b"bad")):
        with pytest.raises(PublicationValidationError):
            request(**{field: value})
    with pytest.raises(PublicationValidationError):
        contracts.SaveRequest(kind=contracts.SYNTHETIC_ORIGINAL_CREATE,
                              request_key="visit-key-0000001", synthetic_patient="P",
                              image_bytes=IMAGE, visit_binding=binding())


def test_public_legacy_parser_refuses_visit_variant():
    with pytest.raises(PublicationValidationError):
        contracts.validate_request({"kind": "visit_original_create",
                                    "request_key": "visit-key-0000001",
                                    "synthetic_patient": "P",
                                    "image_base64": base64.b64encode(IMAGE).decode()})


def test_visit_fields_are_bounded_canonical_and_printable():
    for field in ("appointment", "patient", "body_area", "viewpoint",
                  "appointment_creation", "patient_creation", "encounter",
                  "encounter_creation", "grant_audit"):
        for bad in ("", " spaced ", "x" * 141, "line\nbreak"):
            with pytest.raises(PublicationValidationError):
                binding(**{field: bad})
