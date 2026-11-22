import hashlib
import json

import pytest

from clinic_photos.publication import staging
from clinic_photos.publication import visit_annotation_journal_codec as codec
from test_visit_annotation_staging import intent, site


def published():
    item = intent()
    staged = staging.stage_visit_annotation(item)
    row = codec.submitted_values(item, staged)
    operation = "operation-1"
    material = json.dumps(
        ["clinic-visit-annotation-prepared-v1", item.binding.site, item.binding.actor,
         operation, item.target_identity, item.intent_digest, 1],
        ensure_ascii=True, separators=(",", ":"),
    ).encode()
    row.update(
        name=operation, state="PUBLISHED", generation=1,
        reservation_key="/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json",
        expected_sha256=staged.sha256, expected_size=staged.size,
        published_file="file-1", published_domain_doctype="Clinic Photo Annotation Version",
        published_domain_name=f"{item.managed_draft}-v{item.expected_version + 1}",
        published_version=item.expected_version + 1, creation_identity="12:34",
    )
    return item, row


def decode(item, row, content=None):
    return codec.load_published(
        row, intent=item, operation="operation-1", published_file="file-1",
        creation_identity="12:34",
        committed_bytes=item.canonical_visit_annotation.encode() if content is None else content,
    )


def test_published_readback_without_staging(site, monkeypatch):
    item, row = published()
    monkeypatch.setattr(staging, "read_staged_visit_annotation", lambda _: pytest.fail("staging read"))
    assert decode(item, row) == item


@pytest.mark.parametrize("field,value", [
    ("name", "other"), ("site", "other"), ("actor", "other"), ("request_key", "other"),
    ("kind", "other"), ("state", "SUBMITTED"), ("draft", "other"), ("original", "other"),
    ("expected_version", True), ("visit_binding_json", "{}"), ("identity_digest", "other"),
    ("payload_digest", "other"), ("staged_key", "../x"), ("staged_sha256", "other"),
    ("staged_size", True), ("staged_format", "other"), ("generation", True),
    ("reservation_key", "other"), ("expected_sha256", "other"), ("expected_size", True),
    ("published_file", "other"), ("published_domain_doctype", "other"),
    ("published_domain_name", "other"), ("published_version", True),
    ("creation_identity", "12:35"), ("annotation_json", ""), ("unknown", ""),
])
def test_published_tampering_rejected(site, field, value):
    item, row = published()
    row[field] = value
    with pytest.raises(codec.JournalIntegrityError):
        decode(item, row)


@pytest.mark.parametrize("content", [b"{}", b"", b"\xff", "not bytes"])
def test_published_bytes_must_match(site, content):
    item, row = published()
    with pytest.raises(codec.JournalIntegrityError):
        decode(item, row, content)
