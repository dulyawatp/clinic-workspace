import pytest

from clinic_photos.publication import visit_native_save, visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


@pytest.mark.parametrize("change", ["none", "name", "file", "url", "content"])
def test_receipt_binding_survives_naming_and_refuses_retarget(
        tmp_path, env, monkeypatch, visit_file_scope, change):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))

    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        receipt = visit_native_save._verify_named_receipt(self)
        assert receipt.size == 4
        if change == "none":
            assert visit_native_save._verify_named_receipt(self) is receipt
        else:
            candidate = self
            if change == "name":
                self.name = "FILE-2"
            elif change == "file":
                candidate = File()
                candidate.name = "FILE-1"
                candidate.flags.in_insert = True
            elif change == "url":
                self.file_url = "/private/files/other.json"
            else:
                self.content = b"evil"
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_named_receipt(candidate)
        raise RuntimeError("stop before row insertion")

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop before"):
                doc.insert()
            assert guard.publication_aborted()


def test_named_receipt_requires_in_insert_flag(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            visit_native_save._verify_named_receipt(self)
        raise RuntimeError("stop")
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop"):
                doc.insert()


def test_named_file_without_created_receipt_refuses(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    def native_insert(self):
        self.name = "FILE-1"
        self.flags.in_insert = True
        visit_native_save._verify_named_receipt(self)
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()


def test_unnamed_admission_still_rejects_named_file(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    doc.name = "FILE-1"
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            with visit_file_scope._file_scope(guard, handle, doc):
                pass
