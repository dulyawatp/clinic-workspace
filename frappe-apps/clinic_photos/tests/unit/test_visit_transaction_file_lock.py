import hashlib
from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_file_scope, visit_transaction
from test_visit_transaction_guard import env


def setup_scope(guard, owner):
    from clinic_photos.publication import visit_annotation_prepare
    data = b"content"
    intent = object()
    object.__setattr__(owner, "_operation_id", "operation")
    object.__setattr__(owner, "_intent", intent)
    reservation = visit_annotation_prepare._ContinuedReservation(
        intent, "operation", 1, "/private/files/clinic-visit-annotation-test.json",
        hashlib.sha256(data).hexdigest(), len(data))
    object.__setattr__(owner, "_visit_prepared_values", {
        "reservation_key": reservation.key, "expected_sha256": reservation.digest,
        "expected_size": reservation.size})
    file_doc = SimpleNamespace(
        content=data, is_new=lambda: True, name=None, file_url=reservation.key,
        file_name=reservation.key.rsplit("/", 1)[-1], is_private=1, owner="doctor")
    entry = (object.__new__(visit_file_scope._FileScope), guard, file_doc, reservation, [False, False])
    return entry, visit_file_scope._ACTIVE.set(entry)


@pytest.mark.parametrize("outcome", ["commit", "rollback", "uncertain", "foreign"])
def test_file_lock_owned_until_confirmed_outcome(env, outcome):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    events = []
    db.sql = lambda query, args: (events.append((query, args)) or [(1,)])
    commit, rollback = db.commit, db.rollback
    def logged_commit():
        events.append("commit")
        commit()
    def logged_rollback():
        events.append("rollback")
        rollback()
    db.commit, db.rollback = logged_commit, logged_rollback
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        guard._ack_callback = guard._after_commit
        db.after_commit.add(guard._ack_callback)
        entry, token = setup_scope(guard, owner)
        try:
            queues = [tuple(guard._queue(n)) for n in
                      ("before_commit", "after_commit", "before_rollback", "after_rollback")]
            guard._acquire_visit_file_lock(entry[0])
            assert queues == [tuple(guard._queue(n)) for n in
                              ("before_commit", "after_commit", "before_rollback", "after_rollback")]
            assert events == [("select get_lock(%s, %s)",
                               (frappe_context._lock_name(entry[3].key),
                                frappe_context.LOCK_TIMEOUT_SECONDS))]
            if outcome == "commit":
                guard._publish_ready()
                guard._commit_publish()
            elif outcome == "rollback":
                guard._rollback()
            elif outcome == "uncertain":
                db.fail_commit = True
                guard._publish_ready()
                with pytest.raises(visit_transaction.TransactionUncertain):
                    guard._commit_publish()
            else:
                db._conn = object()
                with pytest.raises(visit_transaction.TransactionUncertain):
                    guard._rollback()
            if outcome in ("commit", "rollback"):
                assert events[1] == outcome
                assert events[-1] == ("select release_lock(%s)", (frappe_context._lock_name(entry[3].key),))
                assert len(events) == 3
            else:
                assert len(events) == (2 if outcome == "uncertain" else 1)
        finally:
            visit_file_scope._ACTIVE.reset(token)


@pytest.mark.parametrize("result", [[(0,)], [(None,)], [], [(True,)]])
def test_unconfirmed_release_is_not_retried(env, result):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    calls = []
    def sql(query, args):
        calls.append(query)
        return [(1,)] if "get_lock" in query else result
    db.sql = sql
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        try:
            guard._acquire_visit_file_lock(entry[0])
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert calls == ["select get_lock(%s, %s)", "select release_lock(%s)"]
        finally:
            visit_file_scope._ACTIVE.reset(token)


def test_wrong_scope_denied_without_sql(env):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    db.sql = lambda *args: pytest.fail("unexpected SQL")
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        try:
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._acquire_visit_file_lock(object())
        finally:
            visit_file_scope._ACTIVE.reset(token)


def test_ambiguous_acquisition_retained_through_rollback(env):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    calls = []
    def sql(query, args):
        calls.append(query)
        raise RuntimeError("unknown acquisition outcome")
    db.sql = sql
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        try:
            with pytest.raises(visit_transaction.TransactionDenied):
                guard._acquire_visit_file_lock(entry[0])
            guard._rollback()
            assert calls == ["select get_lock(%s, %s)"]
        finally:
            visit_file_scope._ACTIVE.reset(token)


def test_ambiguous_rollback_never_releases(env):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    calls = []
    db.sql = lambda query, args: (calls.append(query) or [(1,)])
    db.rollback = lambda: (_ for _ in ()).throw(RuntimeError("unknown rollback outcome"))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        try:
            guard._acquire_visit_file_lock(entry[0])
            with pytest.raises(visit_transaction.TransactionUncertain):
                guard._rollback()
            assert calls == ["select get_lock(%s, %s)"]
        finally:
            visit_file_scope._ACTIVE.reset(token)
