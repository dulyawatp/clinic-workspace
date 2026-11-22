"""Scoped post-naming adapter; no Frappe site required."""

import pytest

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


class _LegacyBindingDenied(PermissionError):
    pass


def test_receipt_bound_named_visit_validate_does_not_require_legacy_intent(
        tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    observed = []
    rows = []
    def create(module, file_doc):
        receipt = module._create_reserved_bytes(file_doc)
        observed.append(receipt)
        return receipt
    configure(doc, tmp_path, monkeypatch,
              create)
    db.visit_rows = rows

    def deny_legacy(*args, **kwargs):
        raise _LegacyBindingDenied("legacy pending_insert unavailable")

    monkeypatch.setattr(file_mixin, "require_file_update", deny_legacy)
    monkeypatch.setattr(NativeFile, "validate", lambda self: observed.append("native validate"),
                        raising=False)

    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        rows.append({"name": self.name, "file_url": self.file_url,
                     "file_name": self.file_name, "owner": self.owner,
                     "is_private": self.is_private, "file_size": self.file_size,
                     "content_hash": self.content_hash, "attached_to_doctype": None,
                     "attached_to_name": None, "attached_to_field": None})
        self.flags.in_insert = False
        return self

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            doc.insert()
            assert observed[-1] == "native validate"


def test_receipt_bound_name_cannot_be_rebound(
        tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    def deny_legacy(*args, **kwargs):
        raise _LegacyBindingDenied("legacy pending_insert unavailable")
    monkeypatch.setattr(file_mixin, "require_file_update", deny_legacy)
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)
    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        self.name = "FILE-2"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            self.validate()
        raise RuntimeError("stop before persistence")
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop before persistence"):
                doc.insert()
