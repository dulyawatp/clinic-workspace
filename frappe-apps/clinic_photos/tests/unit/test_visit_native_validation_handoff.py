import sys
from types import SimpleNamespace

import pytest

from clinic_photos.publication import visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile


def test_authorized_window_runs_pinned_prefix_then_checks_exact_bytes(
        env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    doc.is_folder = False
    doc.is_remote_file = False
    calls = []
    names = ("set_folder_name", "set_is_private", "set_file_name",
             "validate_attachment_limit", "set_file_type",
             "validate_file_extension", "validate_private_file_access")
    for name in names:
        monkeypatch.setattr(doc, name, lambda name=name: calls.append(name), raising=False)
    monkeypatch.setitem(sys.modules, "frappe.utils",
                        SimpleNamespace(get_hook_method=lambda name: None))
    monkeypatch.setattr(doc, "check_max_file_size", lambda: calls.append("size") or 4,
                        raising=False)
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)

    def before(self):
        assert visit_native_save._validated_content(self) == b"data"
        raise RuntimeError("stop before any byte creation")

    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes", before)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop before"):
                doc.insert()
            assert calls == list(names) + ["size"]
            assert guard.publication_aborted()


@pytest.mark.parametrize("mode", ["remote", "folder", "changed_content"])
def test_refuses_unsafe_file_after_native_validation(
        env, monkeypatch, visit_file_scope, mode):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    doc.is_folder = mode == "folder"
    doc.is_remote_file = mode == "remote"
    names = ("set_folder_name", "set_is_private", "set_file_name",
             "validate_attachment_limit", "set_file_type",
             "validate_file_extension", "validate_private_file_access")
    for name in names:
        monkeypatch.setattr(doc, name, lambda: None, raising=False)
    if mode == "changed_content":
        monkeypatch.setattr(doc, "set_file_name",
                            lambda: setattr(doc, "content", b"evil"))
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    monkeypatch.setattr(NativeFile, "before_insert",
                        lambda self: visit_native_save._validated_content(self), raising=False)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()


def test_direct_call_never_opens_validation_or_handoff(env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._validated_content(doc)
            assert guard.publication_aborted()


@pytest.mark.parametrize("failure", ["write_hook", "size"])
def test_save_preparation_refuses_hooks_and_size(
        env, monkeypatch, visit_file_scope, failure):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    doc.is_folder = doc.is_remote_file = False
    for name in ("set_folder_name", "set_is_private", "set_file_name",
                 "validate_attachment_limit", "set_file_type",
                 "validate_file_extension", "validate_private_file_access"):
        monkeypatch.setattr(doc, name, lambda: None, raising=False)
    monkeypatch.setitem(sys.modules, "frappe.utils", SimpleNamespace(
        get_hook_method=lambda name: object() if failure == "write_hook" else None))
    monkeypatch.setattr(doc, "check_max_file_size", lambda: 3, raising=False)
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    monkeypatch.setattr(NativeFile, "before_insert",
                        lambda self: visit_native_save._validated_content(self), raising=False)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
