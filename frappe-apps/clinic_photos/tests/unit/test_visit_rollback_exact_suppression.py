import pytest

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_file_rollback_admission import File, NativeFile


def test_mutated_exact_callback_suppressed_only_during_guarded_rollback(
        env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, other = File(), File()
    native_calls, policy_calls = [], []
    monkeypatch.setattr(NativeFile, "on_rollback",
                        lambda self: native_calls.append(self), raising=False)
    monkeypatch.setattr(file_mixin, "require_file_rollback",
                        lambda self: policy_calls.append(self) or True)

    def native_insert(self):
        frappe.db.after_rollback.add(self.on_rollback)
        raise RuntimeError("native failed")

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    native_rollback = db.rollback
    def rollback():
        other.on_rollback()
        native_rollback()
    monkeypatch.setattr(db, "rollback", rollback)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="native failed"):
                doc.insert()
            doc.file_url = "/private/files/unrelated"
            doc.owner = "other"
            guard._rollback()
            assert native_calls == [other]
            assert policy_calls == [other]
            assert db.rollbacks == 1
            doc.on_rollback()
            assert native_calls == [other, doc]
            assert policy_calls == [other, doc]


def test_unscoped_direct_callback_keeps_existing_policy(monkeypatch):
    doc = File()
    native_calls = []
    monkeypatch.setattr(NativeFile, "on_rollback",
                        lambda self: native_calls.append(self), raising=False)
    monkeypatch.setattr(file_mixin, "require_file_rollback", lambda self: True)
    doc.on_rollback()
    assert native_calls == [doc]


def test_rollback_exception_clears_suppression(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    native_calls = []
    monkeypatch.setattr(NativeFile, "on_rollback",
                        lambda self: native_calls.append(self), raising=False)
    monkeypatch.setattr(file_mixin, "require_file_rollback", lambda self: True)
    def native_insert(self):
        frappe.db.after_rollback.add(self.on_rollback)
        raise RuntimeError("native failed")
    def rollback():
        raise RuntimeError("rollback failed")
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    monkeypatch.setattr(db, "rollback", rollback)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="native failed"):
                doc.insert()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            doc.on_rollback()
            assert native_calls == [doc]
