import sys
import contextvars
import os
import threading
from collections import deque
from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import exclusion, visit_transaction
from test_visit_annotation_prepare import prepared_env
from test_visit_annotation_staging import site


class Hooks:
    def __init__(self):
        self._functions = deque()

    def add(self, callback):
        self._functions.append(callback)

    def run(self):
        while self._functions:
            self._functions.popleft()()

    def clear(self):
        self._functions.clear()


class Database:
    def __init__(self):
        self._conn = object()
        self._cursor = SimpleNamespace(rowcount=0)
        self.transaction_writes = 0
        for name in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            setattr(self, name, Hooks())
        self.fail_commit = False
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.before_rollback.clear()
        self.after_rollback.clear()
        self.before_commit.run()
        self.commits += 1
        if self.fail_commit:
            raise RuntimeError("uncertain commit")
        self.after_commit.run()

    def rollback(self):
        self.before_commit.clear()
        self.after_commit.clear()
        self.before_rollback.run()
        self.rollbacks += 1
        self.after_rollback.run()


class LocalProxy:
    def __init__(self, local):
        object.__setattr__(self, "_local", local)

    def __getattr__(self, name):
        return getattr(self._local.db, name)


@pytest.fixture
def env(monkeypatch):
    db = Database()
    owner = object.__new__(exclusion._VisitAnnotationOwner)
    object.__setattr__(owner, "_site", "clinic")
    object.__setattr__(owner, "_actor", "doctor")
    local = SimpleNamespace(db=db, site="clinic")
    frappe = SimpleNamespace(local=local, session=SimpleNamespace(user="doctor"), db=LocalProxy(local))
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setattr(exclusion._VisitAnnotationOwner, "require_live", lambda self: self)
    monkeypatch.setitem(exclusion._OWNERS, id(owner), owner)
    token = exclusion._VISIT_OWNER_CONTEXT.set(owner)
    object.__setattr__(owner, "_pid", os.getpid())
    object.__setattr__(owner, "_thread", threading.current_thread())
    object.__setattr__(owner, "_token", token)
    try:
        yield frappe, owner, db
    finally:
        exclusion._VISIT_OWNER_CONTEXT.reset(owner._token)


def test_dirty_context_refused(env):
    frappe, owner, db = env
    db.before_commit.add(lambda: None)
    with pytest.raises(visit_transaction.TransactionDenied):
        with visit_transaction._visit_transaction(owner):
            pass


@pytest.mark.parametrize("change", ["connection", "db", "site", "actor", "owner", "pid", "exclusion"])
def test_identity_swap_aborts(env, monkeypatch, change):
    frappe, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        assert frappe_context.current_publication_owner() is guard
        if change == "connection":
            db._conn = object()
        elif change == "db":
            frappe.local.db = Database()
        elif change == "site":
            frappe.local.site = "other"
        elif change == "actor":
            frappe.session.user = "other"
        elif change == "owner":
            setattr(frappe.local, frappe_context.TRANSACTION_OWNER_ATTR, object())
        elif change == "pid":
            monkeypatch.setattr(visit_transaction.os, "getpid", lambda: -1)
        else:
            monkeypatch.setattr(exclusion._VisitAnnotationOwner, "require_live",
                                lambda self: (_ for _ in ()).throw(ValueError()))
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._publish_ready()
        assert guard.publication_aborted()


def test_early_commit_veto_and_sticky_abort(env):
    _, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        with pytest.raises(visit_transaction.TransactionDenied):
            db.commit()
        with pytest.raises(visit_transaction.TransactionDenied):
            db.commit()
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._publish_ready()
        assert guard.publication_aborted()
        assert db.commits == 0
        guard._rollback()
        assert db.rollbacks == 1


def test_unknown_prepare_callbacks_denied(env):
    _, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        db.after_commit.add(lambda: None)
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._prepare_ready(object())


def test_forged_exclusion_owner_denied(env):
    with pytest.raises(visit_transaction.TransactionDenied):
        with visit_transaction._visit_transaction(object()):
            pass


def test_rollback_in_original_scope_once(env):
    _, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        guard.abort_publication("test")
        guard._rollback()
        with pytest.raises(visit_transaction.TransactionUncertain):
            guard._rollback()
        assert db.rollbacks == 1


def test_rollback_rejects_copied_context_even_after_poison(env):
    _, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        guard.abort_publication("test")
        copied = contextvars.copy_context()
        with pytest.raises(visit_transaction.TransactionUncertain):
            copied.run(guard._rollback)
        assert db.rollbacks == 0
        guard._rollback()
        assert db.rollbacks == 1


@pytest.mark.parametrize("name", ["before_rollback", "after_rollback"])
def test_rollback_refuses_foreign_callbacks(env, name):
    _, owner, db = env
    called = []
    with visit_transaction._visit_transaction(owner) as guard:
        guard.abort_publication("test")
        getattr(db, name).add(lambda: called.append(True))
        with pytest.raises(visit_transaction.TransactionUncertain):
            guard._rollback()
        assert not called
        assert db.rollbacks == 0


@pytest.mark.parametrize("change", ["connection", "db", "site", "actor", "owner", "pid"])
def test_rollback_refuses_swapped_context_without_notifying_replacement(env, monkeypatch, change):
    frappe, owner, db = env
    with visit_transaction._visit_transaction(owner) as guard:
        guard.abort_publication("test")
        replacement = SimpleNamespace(abort_publication=lambda reason: pytest.fail("notified replacement"))
        if change == "connection":
            db._conn = object()
        elif change == "db":
            frappe.local.db = Database()
        elif change == "site":
            frappe.local.site = "other"
        elif change == "actor":
            frappe.session.user = "other"
        elif change == "owner":
            setattr(frappe.local, frappe_context.TRANSACTION_OWNER_ATTR, replacement)
        else:
            monkeypatch.setattr(visit_transaction.os, "getpid", lambda: -1)
        with pytest.raises(visit_transaction.TransactionUncertain):
            guard._rollback()
        assert db.rollbacks == 0


def test_combined_preparation_and_native_commit(prepared_env, monkeypatch):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.set_value = env.frappe.db.set_value
    db.get_value = env.frappe.db.get_value
    db.sql = env.frappe.db.sql
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            handle = prepare.prepare(owner)
            guard._prepare_ready(handle)
            guard._commit_prepare()
            assert prepare.continue_after_commit(owner, handle).intent == env.item
            guard._publish_ready()
            guard._commit_publish()
            assert db.commits == 2
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._commit_publish()


def test_prepare_uses_original_database_not_frappe_local_proxy(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.get_value = env.frappe.db.get_value
    original_sql = env.frappe.db.sql
    calls = []
    def sql(query, params):
        calls.append((query, params))
        return original_sql(query, params)
    db.sql = sql
    db.set_value = lambda *args, **kwargs: pytest.fail("cache-triggering set_value")
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    assert env.frappe.db is not db
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            assert guard._db is db and guard._connection is db._conn
            handle = prepare.prepare(owner)
            assert len(calls) == 1 and db._cursor.rowcount == 1
            assert calls[0][1][-2:] == (env.operation, "SUBMITTED")
            guard._prepare_ready(handle)
            guard._commit_prepare()
            assert db.commits == 1


def test_uncertain_commit_is_not_retried(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.set_value = env.frappe.db.set_value
    db.get_value = env.frappe.db.get_value
    db.sql = env.frappe.db.sql
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            handle = prepare.prepare(owner)
            guard._prepare_ready(handle)
            db.fail_commit = True
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._commit_prepare()
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._commit_prepare()
            assert db.commits == 1


def test_preparation_rejects_extra_callback(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.set_value = env.frappe.db.set_value
    db.get_value = env.frappe.db.get_value
    db.sql = env.frappe.db.sql
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            handle = prepare.prepare(owner)
            db.after_commit.add(lambda: None)
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._prepare_ready(handle)
            assert db.commits == 0


def test_prepared_rollback_runs_exact_witness_after_poison(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.set_value = env.frappe.db.set_value
    db.get_value = env.frappe.db.get_value
    db.sql = env.frappe.db.sql
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            handle = prepare.prepare(owner)
            guard._prepare_ready(handle)
            object.__setattr__(owner, "_poisoned", True)
            guard.abort_publication("test")
            guard._rollback()
            assert db.rollbacks == 1
            assert owner._visit_prepare_phase == "burned"


def test_publish_phase_rollback_does_not_expect_consumed_prepare_witness(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare

    env = prepared_env
    db = Database()
    db.set_value = env.frappe.db.set_value
    db.get_value = env.frappe.db.get_value
    db.sql = env.frappe.db.sql
    env.frappe.local.db = db
    env.frappe.db = LocalProxy(env.frappe.local)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with visit_transaction._visit_transaction(owner) as guard:
            handle = prepare.prepare(owner)
            guard._prepare_ready(handle)
            guard._commit_prepare()
            guard.abort_publication("test")
            guard._rollback()
            assert db.rollbacks == 1
