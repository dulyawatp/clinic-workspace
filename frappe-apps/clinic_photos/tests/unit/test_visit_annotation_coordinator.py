import importlib
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest

from clinic_photos.publication import visit_transaction
from clinic_photos.access import frappe_context
from test_visit_transaction_guard import env
from test_visit_annotation_staging import intent


def test_coordinator_available():
    assert importlib.util.find_spec("clinic_photos.publication.visit_annotation_coordinator") is not None


def test_real_native_file_cannot_publish_without_completion(env):
    _, owner, _ = env
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        guard._native_file = object()
        guard._native_settled = True
        with pytest.raises(visit_transaction.TransactionDenied):
            guard._publish_ready()


@pytest.fixture
def lane(env, monkeypatch):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    from clinic_photos.publication import visit_annotation_prepare, visit_annotation_domain
    from clinic_photos.publication import visit_annotation_publish, visit_file_scope
    item = intent()
    item = replace(item, binding=replace(item.binding, site="clinic", actor="doctor"))
    object.__setattr__(owner, "_intent", item)
    object.__setattr__(owner, "_operation_id", "operation")
    events, pending, persisted = [], [], []
    data = item.canonical_visit_annotation.encode()
    key = "/private/files/clinic-visit-annotation-test.json"
    import hashlib
    values = {"reservation_key": key, "expected_sha256": hashlib.sha256(data).hexdigest(),
              "expected_size": len(data), "generation": 1}
    object.__setattr__(owner, "_visit_prepared_values", values)
    def prepare(owner):
        events.append("prepare")
        object.__setattr__(owner, "_visit_prepare_phase", "armed")
        def committed():
            object.__setattr__(owner, "_visit_prepare_phase", "committed")
        def rolled_back():
            object.__setattr__(owner, "_visit_prepare_phase", "burned")
        handle = visit_annotation_prepare._PreparationWitness(owner, committed, rolled_back)
        object.__setattr__(owner, "_visit_prepare_handle", handle)
        db.after_commit.add(handle.committed)
        db.after_rollback.add(handle.rolled_back)
        return handle
    monkeypatch.setattr(visit_annotation_prepare, "prepare", prepare)
    original_commit, original_rollback = db.commit, db.rollback
    def commit():
        events.append("commit")
        original_commit()
        persisted.extend(pending)
        pending.clear()
    def rollback():
        events.append("rollback")
        original_rollback()
        pending.clear()
    db.commit, db.rollback = commit, rollback
    db.sql = lambda query, args: (events.append("release") or [(1,)])
    class File:
        def __init__(self, values):
            self.__dict__.update(values)
            self.flags = SimpleNamespace()
        def insert(self):
            events.append("file")
            pending.append("file")
            self.name = "file-1"
            active_guard._native_file = self
            active_guard._native_queue = db.after_rollback._functions
            active_guard._native_settled = True
            active_guard._file_lock = frappe_context._lock_name(key)
    frappe.get_doc = lambda values: File(values)
    state = SimpleNamespace(fail=None, proof=None, completion=None)
    @contextmanager
    def scope(guard, handle, doc):
        nonlocal active_guard
        active_guard = guard
        yield object()
        events.append("pins_close")
        if state.fail == "pins":
            raise RuntimeError("pin cleanup failed")
    active_guard = None
    monkeypatch.setattr(visit_file_scope, "_file_scope", scope)
    def completed(guard):
        events.append("proof")
        if state.proof is None:
            state.proof = SimpleNamespace(file=guard._native_file, name="file-1", operation="operation",
                                          intent=item, device=1, inode=2)
        return state.proof
    monkeypatch.setattr(visit_file_scope, "_completed_file", completed)
    def domain(guard):
        events.append("domain")
        pending.append("domain")
        if state.fail == "domain":
            raise ValueError("domain failed")
    monkeypatch.setattr(visit_annotation_domain, "_stage", domain)
    def journal(guard):
        events.append("journal")
        pending.append("journal")
        result = object.__new__(visit_annotation_publish._Completion)
        for k, v in dict(guard=guard, intent=item, operation="operation", file="file-1",
                         version=item.managed_draft + "-v1", creation_identity="1:2").items():
            object.__setattr__(result, k, v)
        return result
    monkeypatch.setattr(visit_annotation_publish, "_stage", journal)
    yield owner, db, events, pending, persisted, state


def test_atomic_order_and_internal_acknowledgment(lane):
    owner, db, events, pending, persisted, state = lane
    coordinator = importlib.import_module("clinic_photos.publication.visit_annotation_coordinator")
    with coordinator._run(owner) as outcome:
        assert outcome.guard._visit_completion is outcome.completion
        assert outcome.guard._visit_publish_acknowledged is True
        assert outcome.guard._file_lock == frappe_context._lock_name(outcome.guard._exclusion_owner._visit_prepared_values["reservation_key"])
        assert "release" not in events
    assert persisted == ["file", "domain", "journal"]
    assert events.index("file") < events.index("domain") < events.index("journal")
    assert events.count("commit") == 2 and "rollback" not in events


def test_phase_two_failure_rolls_back_together(lane):
    owner, db, events, pending, persisted, state = lane
    state.fail = "domain"
    coordinator = importlib.import_module("clinic_photos.publication.visit_annotation_coordinator")
    with pytest.raises(Exception):
        with coordinator._run(owner):
            pass
    assert persisted == [] and pending == [] and events[-2:] == ["rollback", "release"]


def test_commit_uncertainty_never_retries_or_rolls_back(lane):
    owner, db, events, pending, persisted, state = lane
    coordinator = importlib.import_module("clinic_photos.publication.visit_annotation_coordinator")
    original = db.commit
    def commit():
        if db.commits == 1:
            events.append("uncertain")
            raise RuntimeError("lost commit response")
        original()
    db.commit = commit
    with pytest.raises(visit_transaction.TransactionUncertain):
        with coordinator._run(owner):
            pass
    assert events.count("uncertain") == 1 and "rollback" not in events and "release" not in events


def test_verified_readback_before_single_release_and_pin_close(lane, monkeypatch):
    from clinic_photos.publication import visit_annotation_readback
    owner, db, events, pending, persisted, state = lane
    coordinator = importlib.import_module("clinic_photos.publication.visit_annotation_coordinator")
    def verify(guard, completion, proof):
        assert guard._phase == "done" and guard._file_lock is not None
        assert guard._visit_completion is completion and guard._completion_file is proof
        events.append("readback")
        return True
    monkeypatch.setattr(visit_annotation_readback, "_verify", verify)
    result = coordinator._publish_verified(owner)
    assert result.operation == "operation" and result.file == "file-1"
    assert events[-3:] == ["readback", "release", "pins_close"]
    assert events.count("release") == 1 and "rollback" not in events


@pytest.mark.parametrize("failure", ["readback", "unconfirmed", "connection", "release", "pins"])
def test_postcommit_failure_is_unknown_never_rollback_or_retry(lane, monkeypatch, failure):
    from clinic_photos.publication import visit_annotation_readback
    owner, db, events, pending, persisted, state = lane
    coordinator = importlib.import_module("clinic_photos.publication.visit_annotation_coordinator")
    def verify(guard, completion, proof):
        events.append("readback")
        if failure == "readback":
            raise ValueError("readback failed")
        if failure == "connection":
            db._conn = object()
        return failure != "unconfirmed"
    monkeypatch.setattr(visit_annotation_readback, "_verify", verify)
    if failure == "release":
        db.sql = lambda query, args: (events.append("release") or [(0,)])
    if failure == "pins":
        state.fail = "pins"
    with pytest.raises(visit_annotation_readback.ReadbackUnknown):
        coordinator._publish_verified(owner)
    assert events.count("commit") == 2 and "rollback" not in events
    assert events.count("readback") == 1
    assert events.count("release") == (0 if failure in ("readback", "unconfirmed", "connection") else 1)
