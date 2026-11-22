import pytest
from types import SimpleNamespace

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_native_save, visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile


def test_native_order_permission_opens_exact_one_shot_window(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    events = []
    monkeypatch.setattr(File, "check_permission", lambda self, kind: events.append(kind), raising=False)
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda self: None)

    def before(self):
        events.append(file_mixin._visit_save_window(self))
        visit_file_scope._ACTIVE.get()[0]._claim(self)
        return SimpleNamespace(size=4)

    def insert(self):
        self.before_insert()
        return self

    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes", before)
    monkeypatch.setattr(visit_native_save, "_verify_post_row", lambda self: None)
    monkeypatch.setattr(visit_file_scope, "_install_completed", lambda *args: None)
    monkeypatch.setattr(NativeFile, "insert", insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            assert doc.insert() is doc
            assert events == ["create", True]
            assert file_mixin._visit_save_window(doc) is False
            assert scope is visit_file_scope._ACTIVE.get()[0]


def test_permission_denial_does_not_enter_window(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    calls = []
    def deny(self, kind):
        raise PermissionError("native denied")
    monkeypatch.setattr(File, "check_permission", deny, raising=False)
    monkeypatch.setattr(NativeFile, "before_insert", lambda self: calls.append(True), raising=False)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native denied"):
                doc.insert()
            assert calls == []
            assert not file_mixin._visit_save_window(doc)
            assert guard.publication_aborted()


def test_direct_before_insert_does_not_gain_window(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(file_mixin, "require_file_insert", lambda self: (_ for _ in ()).throw(PermissionError()))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises((PermissionError, visit_file_scope.FileScopeDenied)):
                doc.before_insert()
            assert not file_mixin._visit_save_window(doc)
            guard.abort_publication("test")


def test_second_before_insert_refused(env, monkeypatch, visit_file_scope):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes",
                        lambda self: SimpleNamespace(size=4))
    def insert(self):
        self.before_insert()
        self.before_insert()
    monkeypatch.setattr(NativeFile, "insert", insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
            assert not file_mixin._visit_save_window(doc)


def test_window_clears_on_native_hook_exception(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    def before(self):
        assert file_mixin._visit_save_window(self)
        raise ValueError("hook failed")
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes", before)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(ValueError, match="hook failed"):
                doc.insert()
            assert not file_mixin._visit_save_window(doc)
            assert guard.publication_aborted()


def test_ambient_begin_does_not_authorize_direct_hook(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            visit_file_scope._before_native_insert(doc, (), {})
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.before_insert()
            assert guard.publication_aborted()


def test_substituted_file_is_refused_inside_window(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, other = File(), File()
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    def before(self):
        file_mixin._visit_save_window(other)
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes", before)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
            assert not file_mixin._visit_save_window(doc)


def test_permission_cannot_enable_bypass_flags(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    calls = []
    monkeypatch.setattr(File, "check_permission",
                        lambda self, kind: setattr(self.flags, "ignore_permissions", True),
                        raising=False)
    monkeypatch.setattr(NativeFile, "before_insert", lambda self: calls.append(True), raising=False)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert not calls
            assert guard.publication_aborted()


def test_direct_content_and_save_keep_existing_guards(env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    def deny(*args):
        raise PermissionError("existing guard")
    monkeypatch.setattr(file_mixin, "require_protected_read", deny)
    monkeypatch.setattr(file_mixin, "require_content_write", deny)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="existing guard"):
                doc.get_content()
            with pytest.raises(PermissionError, match="existing guard"):
                doc.save_file(content=b"data")
            guard.abort_publication("test")
