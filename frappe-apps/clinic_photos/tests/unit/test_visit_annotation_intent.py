from dataclasses import FrozenInstanceError, replace
import json

import pytest

from clinic_photos.access.draft_identity import visit_draft_name
from clinic_photos.publication import contracts, validation
from clinic_photos.publication.visit_annotation_intent import (
    VISIT_ANNOTATION_APPEND, VisitAnnotationBinding, VisitAnnotationIntent,
)


def binding(**changes):
    values = dict(site="site", actor="doctor", appointment="apt", patient="patient",
                  slot="Before", encounter="encounter", appointment_creation="ac",
                  patient_creation="pc", encounter_creation="ec", grant_audit="grant",
                  published_slot="slot-record", original_photo="photo",
                  original_file="file", original_operation="operation")
    values.update(changes)
    return VisitAnnotationBinding(**values)


def document(**changes):
    value = {"schema_version": 2, "coordinate_space": "normalized", "shapes": []}
    value.update(changes)
    return value


def intent(**changes):
    values = dict(request_key="0123456789abcdef", expected_version=0, binding=binding(),
                  canonical_visit_annotation=validation.canonical_visit_annotation(document()))
    values.update(changes)
    return VisitAnnotationIntent(**values)


def test_derived_target_and_immutable_contract():
    item = intent()
    expected = visit_draft_name("site", "apt", "Before", "photo", "doctor")
    assert item.managed_draft == expected
    assert item.target_identity == "draft:" + expected
    assert len(item.content_sha256) == len(item.binding_digest) == len(item.intent_digest) == 64
    with pytest.raises(FrozenInstanceError):
        item.expected_version = 2


@pytest.mark.parametrize("field", list(binding().__dict__))
@pytest.mark.parametrize("bad", ["", " padded", "bad\nvalue", None, 1])
def test_binding_rejects_invalid_exact_references(field, bad):
    with pytest.raises(validation.PublicationValidationError):
        binding(**{field: bad})


@pytest.mark.parametrize("slot", ["before", "Other", " After "])
def test_rejects_wrong_slot(slot):
    with pytest.raises(validation.PublicationValidationError):
        binding(slot=slot)


@pytest.mark.parametrize("version", [-1, True, 1.0, "1", None])
def test_rejects_wrong_expected_version(version):
    with pytest.raises(validation.PublicationValidationError):
        intent(expected_version=version)


def test_scope_key_head_and_content_are_digest_bound():
    item = intent()
    variants = [intent(binding=replace(item.binding, **{field: value}))
                for field, value in [("site", "other"), ("actor", "other"),
                                     ("encounter", "other"), ("published_slot", "other"),
                                     ("original_operation", "other")]]
    variants += [intent(request_key="fedcba9876543210"), intent(expected_version=1),
                 intent(canonical_visit_annotation=validation.canonical_visit_annotation(
                     document(shapes=[{"id": "x", "type": "text", "color": "#123456",
                                       "x": 0, "y": 0, "text": "x", "font_size": 0.1}])))]
    assert all(v.intent_digest != item.intent_digest for v in variants)
    assert variants[0].binding_digest != item.binding_digest


def test_canonical_equivalence_and_legacy_isolation():
    raw = document()
    reordered = json.loads(json.dumps(raw, sort_keys=True))
    assert intent(canonical_visit_annotation=validation.canonical_visit_annotation(raw)).intent_digest == \
        intent(canonical_visit_annotation=validation.canonical_visit_annotation(reordered)).intent_digest
    for invalid in [json.dumps(raw), validation.canonical_annotation(document(schema_version=1))]:
        with pytest.raises(validation.PublicationValidationError):
            intent(canonical_visit_annotation=invalid)
    assert VISIT_ANNOTATION_APPEND not in contracts.REQUEST_KINDS
    with pytest.raises(validation.PublicationValidationError):
        contracts.SaveRequest(kind=VISIT_ANNOTATION_APPEND, request_key="0123456789abcdef")


@pytest.mark.parametrize("text", [
    "x" * (validation.MAX_ANNOTATION_BYTES + 1),
    "é" * (validation.MAX_ANNOTATION_BYTES // 2 + 1),
    "\ud800",
])
def test_invalid_utf8_or_oversized_text_rejected_before_json_parse(monkeypatch, text):
    from clinic_photos.publication import visit_annotation_intent

    def unexpected_parse(*args, **kwargs):
        pytest.fail("JSON parser called before size and UTF-8 validation")

    monkeypatch.setattr(visit_annotation_intent.json, "loads", unexpected_parse)
    with pytest.raises(validation.PublicationValidationError):
        intent(canonical_visit_annotation=text)
