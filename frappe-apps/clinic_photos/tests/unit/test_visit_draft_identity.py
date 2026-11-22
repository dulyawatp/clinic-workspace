import importlib
import re
import sys
import types

import pytest

from clinic_photos.access import draft_identity


PREFIX = "clinic-visit-annotation-draft-"
RESERVED = PREFIX + "a" * 64


def test_visit_identity_is_exact_deterministic_and_unambiguous():
    derive = draft_identity.visit_draft_name
    args = ("site", "appointment", "before", "photo", "author")
    name = derive(*args)
    assert re.fullmatch(PREFIX + r"[0-9a-f]{64}", name)
    assert name == derive(*args)
    for index in range(len(args)):
        changed = list(args)
        changed[index] += "x"
        assert name != derive(*changed)
    assert derive("ab", "c", "before", "photo", "author") != derive(
        "a", "bc", "before", "photo", "author"
    )
    assert derive(" site", *args[1:]) != name
    for index in range(len(args)):
        changed = list(args)
        changed[index] = ""
        with pytest.raises(ValueError):
            derive(*changed)


@pytest.fixture
def controllers(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    frappe.session = types.SimpleNamespace(user="author")

    def throw(message, exception):
        raise exception(message)

    frappe.throw = throw
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = type("Document", (), {})
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.model", model)
    monkeypatch.setitem(sys.modules, "frappe.model.document", document)
    names = (
        "clinic_photos.clinic_photos.doctype.clinic_photo_draft.clinic_photo_draft",
        "clinic_photos.clinic_photos.doctype.clinic_photo_annotation_version.clinic_photo_annotation_version",
    )
    for name in names:
        monkeypatch.delitem(sys.modules, name, raising=False)
    modules = [importlib.import_module(name) for name in names]
    for module in modules:
        monkeypatch.setattr(module, "frappe", frappe)
    yield frappe, modules[0].ClinicPhotoDraft, modules[1].ClinicPhotoAnnotationVersion
    for name in names:
        sys.modules.pop(name, None)


def test_reserved_draft_native_creation_update_and_rename_are_closed(controllers):
    frappe, draft_class, _ = controllers
    draft = draft_class()
    draft.name = RESERVED
    draft.get = lambda field: None
    draft.is_new = lambda: True
    with pytest.raises(frappe.PermissionError):
        draft.before_insert()
    with pytest.raises(frappe.PermissionError):
        draft.validate()
    draft.is_new = lambda: False
    with pytest.raises(frappe.PermissionError):
        draft.validate()
    with pytest.raises(frappe.PermissionError):
        draft.on_trash()
    for old, new in ((RESERVED, "legacy"), ("legacy", RESERVED)):
        with pytest.raises(frappe.PermissionError):
            draft.before_rename(old, new, False)
    draft.before_rename("legacy", "other", False)


def test_reserved_version_native_creation_update_and_rename_are_closed(controllers):
    frappe, _, version_class = controllers
    version = version_class()
    version.name = RESERVED + "-v1"
    version.draft = RESERVED
    version.is_new = lambda: True
    with pytest.raises(frappe.PermissionError):
        version.before_insert()
    with pytest.raises(frappe.PermissionError):
        version.validate()
    version.is_new = lambda: False
    with pytest.raises(frappe.PermissionError):
        version.validate()
    with pytest.raises(frappe.PermissionError):
        version.on_trash()
    for old, new in ((version.name, "legacy-v1"), ("legacy-v1", version.name)):
        with pytest.raises(frappe.PermissionError):
            version.before_rename(old, new, False)
    version.name = "legacy-v1"
    version.draft = "legacy"
    version.before_rename("legacy-v1", "other-v1", False)


def test_reserved_prefix_is_guarded_even_when_malformed(controllers):
    frappe, draft_class, version_class = controllers
    draft = draft_class()
    version = version_class()
    for obj in (draft, version):
        with pytest.raises(frappe.PermissionError):
            obj.before_rename("legacy", PREFIX + "malformed", False)


def test_mixed_case_reserved_names_cannot_be_created_or_renamed(controllers):
    frappe, draft_class, version_class = controllers
    mixed = "Clinic-Visit-Annotation-Draft-" + "a" * 64
    for cls, suffix in ((draft_class, ""), (version_class, "-v1")):
        document = cls()
        document.name = mixed + suffix
        document.draft = "legacy"
        document.get = lambda field: None
        document.is_new = lambda: True
        with pytest.raises(frappe.PermissionError):
            document.before_insert()
        with pytest.raises(frappe.PermissionError):
            document.validate()
        for old, new in ((document.name, "legacy"), ("legacy", document.name)):
            with pytest.raises(frappe.PermissionError):
                document.before_rename(old, new, False)


def test_version_rename_rejects_reserved_draft_link_with_legacy_names(controllers):
    frappe, _, version_class = controllers
    version = version_class()
    version.name = "legacy-v1"
    version.draft = RESERVED
    with pytest.raises(frappe.PermissionError):
        version.before_rename("legacy-v1", "other-v1", False)
