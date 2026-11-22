from dataclasses import replace

import pytest

from clinic_photos.publication import staging, validation
from clinic_photos.publication import visit_annotation_journal_codec as codec
from test_visit_annotation_staging import intent, site


def test_metadata_roundtrip_is_allowlisted_and_private(site):
    item = intent()
    staged = staging.stage_visit_annotation(item)
    values = codec.submitted_values(item, staged)
    assert set(values) == {
        "doctype", "site", "actor", "kind", "request_key", "identity_digest",
        "payload_digest", "state", "draft", "expected_version", "original",
        "visit_binding_json", "staged_key", "staged_sha256", "staged_size",
        "staged_format",
    }
    assert item.canonical_visit_annotation not in repr(values)
    assert codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor) == item


@pytest.mark.parametrize("field,value", [
    ("annotation_json", "{}"), ("synthetic_patient", "patient"),
    ("image_base64", "data"), ("reservation_key", "key"),
    ("expected_sha256", "hash"), ("expected_size", 1), ("generation", 1),
    ("published_file", "file"), ("published_domain_doctype", "X"),
    ("published_domain_name", "X"), ("published_version", 1),
    ("creation_identity", "X"),
])
def test_foreign_evidence_rejected(site, field, value):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    values[field] = value
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor)


@pytest.mark.parametrize("field", [
    "identity_digest", "payload_digest", "draft", "original",
    "visit_binding_json", "staged_sha256", "site", "actor", "request_key",
])
def test_metadata_tamper_rejected(site, field):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    values[field] += "x"
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor)


def test_swapped_valid_stage_rejected(site):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    drawing = {"schema_version": 2, "coordinate_space": "normalized", "shapes": [
        {"id": "x", "type": "text", "color": "#123456", "x": 0, "y": 0,
         "text": "x", "font_size": 0.1},
    ]}
    other = replace(item, canonical_visit_annotation=validation.canonical_visit_annotation(drawing))
    staged = staging.stage_visit_annotation(other)
    for field in ("key", "sha256", "size", "format"):
        values["staged_" + field] = getattr(staged, field)
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor)


def test_encode_refuses_other_site_even_with_valid_stage(site):
    item = intent()
    staged = staging.stage_visit_annotation(item)
    wrong_site = replace(item, binding=replace(item.binding, site="other.local"))
    with pytest.raises(codec.JournalIntegrityError):
        codec.submitted_values(wrong_site, staged)


@pytest.mark.parametrize("field,value", [
    ("doctype", "Other"), ("unknown", "foreign"), ("unexpected_clinical_data", ""),
])
def test_foreign_projection_rejected(site, field, value):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    values[field] = value
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor)


@pytest.mark.parametrize("field,value", [("kind", "other"), ("state", "PREPARED")])
def test_foreign_kind_or_state_rejected_before_read(site, monkeypatch, field, value):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    values[field] = value
    monkeypatch.setattr(staging, "read_staged_visit_annotation", lambda _: pytest.fail("unexpected read"))
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor)


def test_decoder_reads_once_and_accepts_native_envelope(site, monkeypatch):
    item = intent()
    values = codec.submitted_values(item, staging.stage_visit_annotation(item))
    values.update(name="op", owner="owner", creation="date", modified="date",
                  modified_by="owner", docstatus=0, idx=0)
    original = staging.read_staged_visit_annotation
    reads = []
    def read_once(staged):
        reads.append(staged)
        return original(staged)
    monkeypatch.setattr(staging, "read_staged_visit_annotation", read_once)
    assert codec.load_submitted(values, site=item.binding.site, actor=item.binding.actor) == item
    assert len(reads) == 1


def test_decoder_refuses_non_mapping(site):
    with pytest.raises(codec.JournalIntegrityError):
        codec.load_submitted(object(), site="bound-site.local", actor="doctor")
