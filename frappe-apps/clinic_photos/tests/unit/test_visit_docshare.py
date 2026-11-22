"""DocShare visit restrictions; Frappe database and hooks are faked."""
import sys
import types
from unittest.mock import patch

import pytest

from clinic_photos import hooks


@pytest.fixture(autouse=True)
def stub_protection_lookup(monkeypatch):
    from clinic_photos.access import frappe_context
    monkeypatch.setattr(frappe_context, "lookup_protection", lambda *args: ())


def test_docshare_hook_registration():
    assert hooks.doc_events["DocShare"]["validate"] == "clinic_photos.access.share.validate_docshare"


def test_managed_share_denied_unrelated_allowed_and_revocation_allowed():
    from clinic_photos.access import share
    db = types.SimpleNamespace(
        get_value=lambda dt, name, fields, **kw: (
            {"file_url": "/private/files/clinic-visit-photo-fake", "attached_to_doctype": "", "attached_to_name": ""}
            if dt == "File" and name == "managed" else None),
        get_all=lambda *args, **kwargs: [],
    )
    frappe = types.SimpleNamespace(db=db, PermissionError=PermissionError)
    class Document(types.SimpleNamespace):
        def as_dict(self):
            return {k: v for k, v in vars(self).items() if k != "previous"}
        def get_doc_before_save(self):
            return getattr(self, "previous", None)
    doc = Document(share_doctype="File", share_name="managed", user="u",
                   everyone=0, read=1, write=0, submit=0, share=0)
    with patch.dict(sys.modules, {"frappe": frappe}):
        with pytest.raises(PermissionError):
            share.validate_docshare(doc)
        doc.share_name = "ordinary"
        share.validate_docshare(doc)
        doc.share_name = "managed"
        old = Document(share_doctype="File", share_name="managed", user="u",
                       everyone=0, read=1, write=0, submit=0, share=0)
        doc.previous = old
        doc.read = 0
        share.validate_docshare(doc)
        doc.read = 1
        with pytest.raises(PermissionError):
            share.validate_docshare(doc)
        doc.read = 0
        doc.user = "another"
        with pytest.raises(PermissionError):
            share.validate_docshare(doc)


def test_alias_slot_linked_file_and_preactivation_check():
    from clinic_photos.access import share

    files = {
        "alias": {"file_url": "/private/files/%2563linic-visit-photo-malformed"},
        "slot-file": {"file_url": "/private/files/renamed"},
        "legacy": {"file_url": "/private/files/ordinary"},
    }
    photos = {"photo": {"storage_key": "/private/files/renamed", "original_file": "slot-file"}}
    def get_value(doctype, name, fields, **kwargs):
        if doctype == "File":
            row = files.get(name, {})
            return row.get(fields) if isinstance(fields, str) else row
        return photos.get(name, {})
    rows = []
    def get_all(doctype, **kwargs):
        if doctype == "DocShare":
            return rows
        if doctype == "Clinic Photo":
            return [key for key, row in photos.items()
                    if row["original_file"] == kwargs["filters"]["original_file"]]
        if doctype == "Clinic Visit Photo Slot":
            return ["slot"] if kwargs["filters"]["photo"] == "photo" else []
        raise AssertionError(doctype)
    frappe = types.SimpleNamespace(db=types.SimpleNamespace(get_value=get_value, get_all=get_all),
                                   PermissionError=PermissionError)
    with patch.dict(sys.modules, {"frappe": frappe}):
        assert share._managed_target("File", "alias")
        assert share._managed_target("File", "slot-file")
        assert share._managed_target("Clinic Photo", "photo")
        assert not share._managed_target("File", "legacy")
        rows.append({"share_doctype": "File", "share_name": "legacy"})
        share.assert_no_managed_shares()
        rows.append({"share_doctype": "Clinic Photo", "share_name": "photo"})
        with pytest.raises(PermissionError, match="activation blocked"):
            share.assert_no_managed_shares()


def test_drawing_share_grants_and_preactivation_scan(monkeypatch):
    from clinic_photos.access import share
    from clinic_photos.access import frappe_context
    from clinic_photos.access.draft_identity import VISIT_DRAFT_PREFIX

    reserved = VISIT_DRAFT_PREFIX + "example"
    files = {
        "drawing": {"name": "drawing", "file_url": "/private/files/clinic-visit-annotation-example"},
        "alias": {"name": "alias", "file_url": "/private/files/%63linic-visit-annotation-example"},
        "attached": {"name": "attached", "file_url": "/private/files/ordinary",
                     "attached_to_doctype": "Clinic Photo Annotation Version",
                     "attached_to_name": "version-alias"},
        "referenced": {"name": "referenced", "file_url": "/private/files/renamed"},
        "legacy": {"name": "legacy", "file_url": "/private/files/ordinary"},
    }
    monkeypatch.setattr(frappe_context, "lookup_protection", lambda key, name: (
        [{"doctype": "Clinic Photo Annotation Version", "name": "version-alias",
          "draft": reserved, "kind": "annotation", "storage_key": key}]
        if name == "referenced" else []
    ))
    versions = {
        "version-alias": {"name": "version-alias", "draft": reserved},
        "legacy-version": {"name": "legacy-version", "draft": "legacy-draft"},
    }
    rows = []
    def get_value(doctype, name, fields, **kwargs):
        if doctype == "File":
            return files.get(name)
        if doctype == "Clinic Photo Annotation Version":
            return versions.get(name)
        return None
    def get_all(doctype, **kwargs):
        if doctype == "DocShare":
            allowed = kwargs["filters"]["share_doctype"][1]
            return [row for row in rows if row["share_doctype"] in allowed]
        return []
    class Document(types.SimpleNamespace):
        def get_doc_before_save(self):
            return getattr(self, "previous", None)
        def as_dict(self):
            return {k: v for k, v in vars(self).items() if k != "previous"}
    frappe = types.SimpleNamespace(db=types.SimpleNamespace(get_value=get_value, get_all=get_all),
                                   PermissionError=PermissionError)
    targets = [
        ("Clinic Photo Draft", reserved),
        ("Clinic Photo Annotation Version", reserved + "-v1"),
        ("Clinic Photo Annotation Version", "version-alias"),
        ("File", "drawing"), ("File", "alias"), ("File", "attached"),
        ("File", "referenced"),
    ]
    with patch.dict(sys.modules, {"frappe": frappe}):
        for doctype, name in targets:
            doc = Document(share_doctype=doctype, share_name=name, user="Administrator",
                           read=1, write=0, submit=0, share=0)
            with pytest.raises(PermissionError):
                share.validate_docshare(doc)
            rows[:] = [{"share_doctype": doctype, "share_name": name}]
            with pytest.raises(PermissionError, match="activation blocked"):
                share.assert_no_managed_shares()
            doc.previous = Document(**doc.as_dict())
            doc.read = 0
            share.validate_docshare(doc)
            doc.share_name = "unrelated"
            with pytest.raises(PermissionError):
                share.validate_docshare(doc)
        for doctype, name in [("Clinic Photo Draft", "legacy-draft"),
                              ("Clinic Photo Annotation Version", "legacy-version"),
                              ("File", "legacy"), ("Other", reserved)]:
            doc = Document(share_doctype=doctype, share_name=name, read=1)
            share.validate_docshare(doc)
            rows[:] = [{"share_doctype": doctype, "share_name": name}]
            share.assert_no_managed_shares()


@pytest.mark.parametrize("doctype", ["Clinic Photo Draft", "File"])
def test_persisted_identity_alias_denied_for_share_and_scan(monkeypatch, doctype):
    from clinic_photos.access import frappe_context, share
    from clinic_photos.access.draft_identity import VISIT_DRAFT_PREFIX

    reserved = VISIT_DRAFT_PREFIX + "persisted"
    rows = [{"share_doctype": doctype, "share_name": "database-alias"}]
    def get_value(dt, name, fields, **kwargs):
        if dt == "Clinic Photo Draft" and name == "database-alias":
            return reserved if fields == "name" else {"name": reserved}
        if dt == "File" and name == "database-alias":
            return {"name": "canonical-file", "file_url": "/private/files/ordinary"}
        return None
    def lookup(key, name):
        if name == "canonical-file":
            return [{"doctype": "Clinic Photo Annotation Version", "name": "version",
                     "draft": reserved, "kind": "annotation", "storage_key": key}]
        return []
    monkeypatch.setattr(frappe_context, "lookup_protection", lookup)
    frappe = types.SimpleNamespace(
        db=types.SimpleNamespace(get_value=get_value,
                                 get_all=lambda dt, **kw: rows if dt == "DocShare" else []),
        PermissionError=PermissionError,
    )
    doc = types.SimpleNamespace(share_doctype=doctype, share_name="database-alias",
                                get_doc_before_save=lambda: None)
    with patch.dict(sys.modules, {"frappe": frappe}):
        with pytest.raises(PermissionError):
            share.validate_docshare(doc)
        with pytest.raises(PermissionError, match="activation blocked"):
            share.assert_no_managed_shares()
