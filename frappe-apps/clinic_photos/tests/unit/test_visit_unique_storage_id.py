"""Visit reservation namespace and orphan protection contracts."""

import re
from types import SimpleNamespace

import pytest

from clinic_photos.access import storage
from clinic_photos.publication import contracts, journal


def no_references(_key, _name):
    return ()


def reservation(kind, operation_id="operation-1"):
    request = SimpleNamespace(kind=kind, image_bytes=b"\x89PNG\r\n\x1a\n")
    return journal._reservation_key(
        request, site="site.test", operation_id=operation_id, generation=1, payload="digest"
    )


def test_visit_reservation_is_stable_opaque_and_distinct():
    key = reservation(contracts.VISIT_ORIGINAL_CREATE)
    assert re.fullmatch(r"/private/files/clinic-visit-photo-[0-9a-f]{32}\.png", key)
    assert key == reservation(contracts.VISIT_ORIGINAL_CREATE)
    assert key != reservation(contracts.VISIT_ORIGINAL_CREATE, "operation-2")
    assert storage.is_canonical_visit_key(key)


def test_visit_namespace_is_protected_without_any_registration():
    key = "/private/files/clinic-visit-photo-not-yet-registered.png"
    assert storage.is_canonical_visit_key(key)
    assert storage.is_reserved_key(key)
    record = storage.classify_file({"file_url": key}, lookup=no_references)
    assert record.kind == "unregistered"
    with pytest.raises(storage.ProtectedAccessDenied):
        storage.require_protected_read({"file_url": key}, lookup=no_references)


def test_classifier_rejects_noncanonical_and_unrelated_keys():
    key = "/private/files/clinic-visit-photo-123.png"
    assert not storage.is_canonical_visit_key(key.replace("clinic", "%63linic"))
    assert not storage.is_canonical_visit_key("/files/clinic-visit-photo-123.png")
    assert not storage.is_canonical_visit_key("/private/files/clinic-photo-123.png")
    assert not storage.is_canonical_visit_key(None)


def test_existing_reservation_namespaces_remain_unchanged():
    synthetic = reservation(contracts.SYNTHETIC_ORIGINAL_CREATE)
    assert re.fullmatch(r"/private/files/clinic-photo-[0-9a-f]{32}\.png", synthetic)
    assert storage.is_reserved_key(synthetic)
    assert storage.is_reserved_key("/private/files/clinic-annotation-old.json")
    assert storage.classify_file(
        {"file_url": "/private/files/ordinary.png"}, lookup=no_references
    ) is None
