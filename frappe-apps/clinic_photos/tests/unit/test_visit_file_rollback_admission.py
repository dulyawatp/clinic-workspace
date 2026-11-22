"""Unwired admission of the exact scoped native File rollback callback."""

from types import SimpleNamespace

import pytest

from clinic_photos.access import file_mixin, frappe_context
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


class OverridingFile(File):
    def on_rollback(self):
        return None


class AttributeFile(File):
    def __init__(self):
        super().__init__()
        self.on_rollback = lambda: None


class DuckCallback:
    """Callable-looking queue entry that is not a bound method."""

    def __init__(self, file_doc):
        self.__self__ = file_doc
        self.__func__ = file_mixin.ProtectedFileMixin.on_rollback

    def __call__(self):
        return None


def _scope(env, monkeypatch):
    owner, handle, _ = setup_scope(env, monkeypatch)
    return owner, handle


def test_pre_registration_failure_admits_unchanged_baseline(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native create denied"):
                doc.insert()
            assert guard._native_file is doc
            assert guard._native_settled
            assert guard._native_callback is None
            assert list(db.after_rollback._functions) == []
            guard._rollback()
            assert db.rollbacks == 1


def test_post_registration_failure_admits_exact_native_callback(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, compensation = File(), []
    monkeypatch.setattr(file_mixin, "require_file_rollback",
                        lambda file_doc: compensation.append(file_doc) and False)

    def native(self):
        frappe.db.after_rollback.add(self.on_rollback)
        raise RuntimeError("native insert failed after save")

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="after save"):
                doc.insert()
            captured = guard._native_callback
            assert captured is db.after_rollback._functions[-1]
            assert captured.__self__ is doc
            assert captured.__func__ is file_mixin.ProtectedFileMixin.on_rollback
            guard._rollback()
            assert db.rollbacks == 1
            assert compensation == []


def test_poisoned_failure_before_registration_still_settles(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"

        def native(self):
            guard.abort_publication("publication boundary aborted")
            monkeypatch.setattr(frappe_context, "publication_transaction_live", lambda: False)
            raise PermissionError("native create denied")

        monkeypatch.setattr(NativeFile, "insert", native)
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native create denied"):
                doc.insert()
            assert guard._native_settled
            assert guard._native_callback is None
            guard._rollback()
            assert db.rollbacks == 1


def test_poisoned_failure_after_registration_still_settles(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, compensation = File(), []
    monkeypatch.setattr(file_mixin, "require_file_rollback",
                        lambda file_doc: compensation.append(file_doc) and False)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"

        def native(self):
            frappe.db.after_rollback.add(self.on_rollback)
            guard.abort_publication("publication boundary aborted")
            monkeypatch.setattr(frappe_context, "publication_transaction_live", lambda: False)
            raise RuntimeError("after save")

        monkeypatch.setattr(NativeFile, "insert", native)
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="after save"):
                doc.insert()
            assert guard._native_settled
            assert guard._native_callback is db.after_rollback._functions[-1]
            guard._rollback()
            assert db.rollbacks == 1
            assert compensation == []


def test_successful_insert_requires_expected_callback(env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(NativeFile, "insert", lambda self: self)
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)
    monkeypatch.setattr(visit_native_save, "_verify_post_row", lambda file_doc: None)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
            assert not guard._native_settled
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0


def test_successful_insert_admits_exact_native_callback(env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, compensation = File(), []
    monkeypatch.setattr(file_mixin, "require_file_rollback",
                        lambda file_doc: compensation.append(file_doc) and False)
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)
    monkeypatch.setattr(visit_native_save, "_verify_post_row", lambda file_doc: None)
    monkeypatch.setattr(visit_file_scope, "_install_completed", lambda *args: None)

    def native(self):
        frappe.db.after_rollback.add(self.on_rollback)
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            doc.insert()
            assert guard._native_callback is db.after_rollback._functions[-1]
            assert guard._permitted is False
            assert guard._phase == "publish"
            assert guard._verified is False and guard._acknowledged is False
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._commit_publish()
            assert db.commits == 0
            guard.abort_publication("publication failed after insert")
        guard._rollback()
        assert db.rollbacks == 1
        assert compensation == []


def test_foreign_callback_preserves_native_error(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, alien = File(), []
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.db.after_rollback.add(lambda: alien.append("alien"))
        raise PermissionError("native create denied")

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native create denied"):
                doc.insert()
            assert guard.publication_aborted()
            assert not guard._native_settled
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0
            assert alien == []
            assert len(db.after_rollback._functions) == 1


def test_foreign_callback_on_success_refuses(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, alien = File(), []
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.db.after_rollback.add(lambda: alien.append("alien"))
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
            assert not guard._native_settled
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0
            assert alien == []


def test_duck_typed_callback_refuses(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.db.after_rollback.add(DuckCallback(self))
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert not guard._native_settled
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0


def test_other_document_callback_refuses(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, other = File(), File()
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.db.after_rollback.add(other.on_rollback)
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0


def test_extra_appended_callback_refuses(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, alien = File(), []
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.db.after_rollback.add(self.on_rollback)
        frappe.db.after_rollback.add(lambda: alien.append("alien"))
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0
            assert alien == []


def test_foreign_initial_queue_refused(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc, baseline_calls = File(), []
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def baseline():
        baseline_calls.append(True)

    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        db.after_rollback.add(baseline)
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert doc.native_calls == []
            assert guard.publication_aborted()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0
            assert baseline_calls == []


def test_overridden_on_rollback_refused_before_native(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = OverridingFile()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert doc.native_calls == []
            assert guard.publication_aborted()


def test_non_method_on_rollback_refused_before_native(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = AttributeFile()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert doc.native_calls == []
            assert guard.publication_aborted()


def test_success_settlement_requires_captured_scope(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            token = visit_file_scope._before_native_insert(doc, (), {})
            assert scope._claim(doc) == b"data"
        assert not guard.publication_aborted()
        with pytest.raises(visit_file_scope.FileScopeDenied):
            visit_file_scope._native_insert_persisted(doc, token)
        assert guard.publication_aborted()


def test_success_settlement_rejects_mismatched_document(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            token = visit_file_scope._before_native_insert(doc, (), {})
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_file_scope._native_insert_persisted(File(), token)
            assert guard.publication_aborted()


def test_publish_ready_refuses_pending_native_insert(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        guard._begin_native_file_insert(doc)
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._publish_ready()
        assert guard.publication_aborted()
        with pytest.raises(visit_transaction.TransactionUncertain):
            guard._rollback()
        assert db.rollbacks == 0


def test_unsettled_native_insert_blocks_rollback(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        guard._begin_native_file_insert(doc)
        guard.abort_publication("test")
        with pytest.raises(visit_transaction.TransactionUncertain):
            guard._rollback()
        assert db.rollbacks == 0


def test_second_native_insert_is_refused(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        guard._begin_native_file_insert(doc)
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._begin_native_file_insert(File())
        assert guard.publication_aborted()


def test_replaced_rollback_queue_refused(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle = _scope(env, monkeypatch)
    doc = File()
    monkeypatch.setattr(file_mixin, "require_file_persisted_identity", lambda file_doc: None)

    def native(self):
        frappe.local.db.after_rollback = type(db.after_rollback)()
        return self

    monkeypatch.setattr(NativeFile, "insert", native)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert db.rollbacks == 0
