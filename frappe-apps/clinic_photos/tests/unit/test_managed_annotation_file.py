"""No-site checks for reserved drawing File identities and byte denial."""

import types

import pytest

from clinic_photos.access import frappe_context, storage
from fake_frappe import fake_frappe
from test_file_policy import base_docs, add_file_row, actor_for, ANNOTATION_KEY, VERSION


PREFIX = "clinic-visit-annotation-"
DRAFT = "clinic-visit-annotation-draft-abc"


def test_reserved_key_case_and_encoded_alias():
    for basename in (PREFIX + "abc.json", PREFIX.upper() + "abc.json"):
        key = "/private/files/" + basename
        assert storage.is_reserved_key(key)
        assert storage.is_reserved_key(storage.canonical_private_key(key.replace("clinic", "%63linic")))


def test_authoritative_version_reference_denies_bytes_before_pending(monkeypatch):
    docs = base_docs(version_file="FILE-1")
    docs["Clinic Photo Annotation Version"][VERSION]["draft"] = DRAFT
    with fake_frappe(docs, user="Administrator") as frappe:
        add_file_row(frappe, file_url=ANNOTATION_KEY)
        doc = types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)
        monkeypatch.setattr(frappe_context, "_pending_content_matches", lambda *args: True)
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_persisted_attachment_and_alias_are_managed():
    docs = base_docs()
    with fake_frappe(docs) as frappe:
        row = add_file_row(frappe, file_url=ANNOTATION_KEY)
        row["attached_to_doctype"] = "Clinic Photo Draft"
        row["attached_to_name"] = DRAFT
        doc = types.SimpleNamespace(name="FILE-1", file_url="/private/files/other.json")
        assert frappe_context.is_managed_annotation_file(doc)


def test_persisted_version_attachment_is_managed():
    docs = base_docs()
    docs["Clinic Photo Annotation Version"][VERSION]["draft"] = DRAFT
    with fake_frappe(docs) as frappe:
        row = add_file_row(frappe, file_url=ANNOTATION_KEY)
        row["attached_to_doctype"] = "Clinic Photo Annotation Version"
        row["attached_to_name"] = VERSION
        doc = types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_orphan_and_persisted_managed_keys_deny_bytes():
    key = "/private/files/" + PREFIX.upper() + "abc.json"
    with fake_frappe(base_docs()) as frappe:
        add_file_row(frappe, file_url=key)
        for doc in (types.SimpleNamespace(file_url=key),
                    types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)):
            assert frappe_context.is_managed_annotation_file(doc)
            with pytest.raises(frappe.PermissionError):
                frappe_context.require_protected_read(doc)


def test_orphan_key_denies_write():
    with fake_frappe(base_docs()) as frappe:
        doc = types.SimpleNamespace(file_url="/private/files/" + PREFIX + "abc.json")
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_content_write(doc, "save_file")


def test_legacy_reference_is_not_managed():
    with fake_frappe(base_docs(version_file="FILE-1")) as frappe:
        add_file_row(frappe, file_url=ANNOTATION_KEY)
        doc = types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)
        assert not frappe_context.is_managed_annotation_file(doc)


@pytest.mark.parametrize("draft", ["", "DRAFT-1"])
def test_reserved_version_reference_with_stale_draft_denies(draft):
    docs = base_docs(version_file="FILE-1")
    row = docs["Clinic Photo Annotation Version"].pop(VERSION)
    row.update(name=DRAFT + "-v1", draft=draft)
    docs["Clinic Photo Annotation Version"][row["name"]] = row
    with fake_frappe(docs) as frappe:
        add_file_row(frappe, file_url=ANNOTATION_KEY)
        doc = types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_missing_reserved_version_attachment_denies():
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        row.update(attached_to_doctype="Clinic Photo Annotation Version",
                   attached_to_name=DRAFT + "-v1")
        doc = types.SimpleNamespace(name="FILE-1", file_url=row["file_url"])
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_missing_legacy_version_attachment_remains_unrelated():
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        row.update(attached_to_doctype="Clinic Photo Annotation Version",
                   attached_to_name="MISSING-LEGACY")
        doc = types.SimpleNamespace(name="FILE-1", file_url=row["file_url"])
        assert not frappe_context.is_managed_annotation_file(doc)
        frappe_context.require_protected_read(doc)


@pytest.mark.parametrize("draft", ["", "DRAFT-1"])
def test_version_attachment_alias_resolves_reserved_persisted_name(monkeypatch, draft):
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        row.update(attached_to_doctype="Clinic Photo Annotation Version",
                   attached_to_name="database-alias")
        doc = types.SimpleNamespace(name="FILE-1", file_url=row["file_url"])
        get_value = frappe.db.get_value

        def alias_lookup(doctype, name, fields, **kwargs):
            if doctype == "Clinic Photo Annotation Version" and name == "database-alias":
                if fields == "draft":
                    return draft
                return {"name": DRAFT + "-v1", "draft": draft}
            return get_value(doctype, name, fields, **kwargs)

        monkeypatch.setattr(frappe.db, "get_value", alias_lookup)
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_draft_attachment_alias_resolves_reserved_persisted_name(monkeypatch):
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        row.update(attached_to_doctype="Clinic Photo Draft", attached_to_name="draft-alias")
        doc = types.SimpleNamespace(name="FILE-1", file_url=row["file_url"])
        get_value = frappe.db.get_value
        def alias_lookup(doctype, name, fields, **kwargs):
            if doctype == "Clinic Photo Draft" and name == "draft-alias":
                return DRAFT if fields == "name" else {"name": DRAFT}
            return get_value(doctype, name, fields, **kwargs)
        monkeypatch.setattr(frappe.db, "get_value", alias_lookup)
        assert frappe_context.is_managed_annotation_file(doc)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)


def test_file_alias_uses_persisted_name_once_for_byte_guard(monkeypatch):
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        doc = types.SimpleNamespace(name="file-alias", file_url=row["file_url"])
        get_value = frappe.db.get_value
        lookups, reads, classifications = [], [], []
        def alias_lookup(doctype, name, fields, **kwargs):
            if doctype == "File":
                reads.append(name)
                if name == "file-alias":
                    return {**row, "name": "FILE-1"}
            return get_value(doctype, name, fields, **kwargs)
        def protection(key, name):
            lookups.append(name)
            return ([{"doctype": "Clinic Photo Annotation Version", "name": "version",
                      "draft": DRAFT, "kind": "annotation", "storage_key": key}]
                    if name == "FILE-1" else [])
        classify = storage.classify_file
        def counted(*args, **kwargs):
            classifications.append(1)
            return classify(*args, **kwargs)
        monkeypatch.setattr(frappe.db, "get_value", alias_lookup)
        monkeypatch.setattr(frappe_context, "lookup_protection", protection)
        monkeypatch.setattr(storage, "classify_file", counted)
        with pytest.raises(frappe.PermissionError):
            frappe_context.require_protected_read(doc)
        assert reads == ["file-alias"]
        assert lookups == ["FILE-1"]
        assert len(classifications) == 1


def test_classify_file_accepts_preloaded_persisted_identity(monkeypatch):
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        doc = types.SimpleNamespace(name="file-alias", file_url=row["file_url"])
        monkeypatch.setattr(frappe_context, "_file_row",
                            lambda doc: pytest.fail("unexpected File read"))
        seen = []
        monkeypatch.setattr(frappe_context, "lookup_protection",
                            lambda key, name: seen.append(name) or [])
        assert frappe_context.classify_file(doc, persisted={**row, "name": "FILE-1"}) is None
        assert seen == ["FILE-1"]


def test_unrelated_read_queries_once_and_deduplicates_attachments(monkeypatch):
    with fake_frappe(base_docs()) as frappe:
        row = add_file_row(frappe, file_url="/private/files/unrelated.json")
        row.update(attached_to_doctype="Clinic Photo Annotation Version",
                   attached_to_name="MISSING-LEGACY")
        doc = types.SimpleNamespace(name="FILE-1", file_url=row["file_url"],
                                    attached_to_doctype=row["attached_to_doctype"],
                                    attached_to_name=row["attached_to_name"])
        values, searches = [], []
        get_value, get_all = frappe.db.get_value, frappe.db.get_all
        def counted_value(doctype, *args, **kwargs):
            values.append(doctype)
            return get_value(doctype, *args, **kwargs)
        def counted_all(doctype, *args, **kwargs):
            searches.append(doctype)
            return get_all(doctype, *args, **kwargs)
        monkeypatch.setattr(frappe.db, "get_value", counted_value)
        monkeypatch.setattr(frappe.db, "get_all", counted_all)
        frappe_context.require_protected_read(doc)
        assert values.count("File") == 1
        assert values.count("Clinic Photo Annotation Version") == 1
        assert len(searches) == 5


def test_storage_default_authorizer_receives_classified_legacy_record(monkeypatch):
    with fake_frappe(base_docs(version_file="FILE-1")) as frappe:
        add_file_row(frappe, file_url=ANNOTATION_KEY)
        doc = types.SimpleNamespace(name="FILE-1", file_url=ANNOTATION_KEY)
        seen = []
        monkeypatch.setattr(frappe_context, "current_actor", lambda: actor_for("Physician"))
        monkeypatch.setattr(frappe_context, "authorize_protected_read",
                            lambda record, file_doc: seen.append((record, file_doc)) or True)
        storage.require_protected_read(doc)
        assert len(seen) == 1
        assert seen[0][0].kind == "annotation"
        assert seen[0][1] is doc
