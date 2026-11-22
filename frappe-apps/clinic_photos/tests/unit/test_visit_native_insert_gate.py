from types import SimpleNamespace

import pytest

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope


class NativeFile:
    def insert(self, *args, **kwargs):
        self.native_calls.append((args, kwargs))
        raise PermissionError("native create denied")


class File(file_mixin.ProtectedFileMixin, NativeFile):
    def __init__(self):
        self.name = ""
        self.file_url = "/private/files/clinic-visit-annotation-test.json"
        self.file_name = "clinic-visit-annotation-test.json"
        self.is_private = 1
        self.owner = "doctor"
        self.content = b"data"
        self.attached_to_doctype = self.attached_to_name = self.attached_to_field = None
        self.flags = SimpleNamespace()
        self.native_calls = []

    def is_new(self):
        return True


@pytest.mark.parametrize("args,kwargs,flag", [
    ((True,), {}, None),
    ((), {"ignore_permissions": True}, None),
    ((), {"ignore_if_duplicate": True}, None),
    ((), {}, "ignore_permissions"),
    ((), {}, "ignore_links"),
    ((), {}, "ignore_mandatory"),
    ((), {}, "ignore_duplicate_entry_error"),
    ((), {}, "in_insert"),
    ((), {}, "in_print"),
    ((), {}, "copy_from_existing_file"),
])
def test_unsafe_insert_refused_before_native(env, monkeypatch, visit_file_scope, args, kwargs, flag):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    if flag:
        setattr(doc.flags, flag, True)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert(*args, **kwargs)
            assert guard.publication_aborted()
            assert doc.native_calls == []


def test_clean_insert_preserves_native_create_denial(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native create denied"):
                doc.insert()
            assert doc.native_calls == [((), {})]
            assert guard.publication_aborted()


def test_other_file_insert_refused_while_scope_active(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, other = File(), File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                other.insert()
            assert not other.native_calls
            assert guard.publication_aborted()


def test_unscoped_insert_keeps_native_arguments():
    doc = File()
    with pytest.raises(PermissionError, match="native create denied"):
        doc.insert(ignore_permissions=True)
    assert doc.native_calls == [((), {"ignore_permissions": True})]


def test_decode_refused_before_native(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    doc.decode = True
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert not doc.native_calls
            assert guard.publication_aborted()


def test_identity_failure_after_native_insert_aborts_even_if_caught(
        env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(NativeFile, "insert", lambda self: self)
    def refuse_identity(file_doc):
        raise ValueError("identity unavailable")
    monkeypatch.setattr(visit_native_save, "_verify_post_row", refuse_identity)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(ValueError, match="identity unavailable"):
                doc.insert()
            assert guard.publication_aborted()


def test_unscoped_identity_failure_propagates(monkeypatch):
    doc = File()
    monkeypatch.setattr(NativeFile, "insert", lambda self: self)
    def refuse_identity(file_doc):
        raise ValueError("identity unavailable")
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", refuse_identity)
    with pytest.raises(ValueError, match="identity unavailable"):
        doc.insert()
