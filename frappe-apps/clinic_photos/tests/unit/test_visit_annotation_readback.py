from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_file_scope, visit_native_save
from test_visit_annotation_domain import stage_env
from test_visit_annotation_publish import ready
from test_visit_transaction_guard import env


@pytest.fixture
def committed(ready, monkeypatch):
    from clinic_photos.publication import visit_annotation_publish
    guard, journal_row, rows, item, writes = ready
    completion = visit_annotation_publish._stage(guard)
    entry = visit_file_scope._ACTIVE.get()
    old_proof = visit_file_scope._completed_file(guard)
    proof = object.__new__(visit_file_scope._CompletedFile)
    for field in ("operation", "intent", "reservation", "file", "name", "device", "inode", "size", "sha256"):
        object.__setattr__(proof, field, getattr(old_proof, field))
    receipt = SimpleNamespace(device=proof.device, inode=proof.inode, size=proof.size, sha256=proof.sha256)
    attempt = [entry, None, None, (entry, proof.file, proof.reservation, receipt, (proof.name,))]
    entry[0]._completed = proof
    entry[4].append((proof, attempt))
    guard._phase = "done"
    guard._visit_completion = completion
    guard._visit_publish_acknowledged = True
    guard._file_lock = frappe_context._lock_name(proof.reservation.key)
    guard._file_lock_uncertain = False
    calls = []
    monkeypatch.setattr(visit_native_save, "_verify_persisted_bytes",
                        lambda e, r: calls.append((e, r)))
    def sql(query, params=()):
        if "@@session.innodb_snapshot_isolation" in query:
            return [(0,)]
        assert "LOCK IN SHARE MODE" in query
        return [(proof.name, proof.reservation.key)]
    guard._db.sql = sql
    writes.clear()
    return guard, completion, proof, journal_row, rows, calls, writes


def test_immediate_readback_succeeds_without_writes(committed):
    from clinic_photos.publication import visit_annotation_readback as readback
    guard, completion, proof, _, _, calls, writes = committed
    assert readback._verify(guard, completion, proof) is True
    assert calls and calls[0][0] is visit_file_scope._ACTIVE.get()
    assert not writes


@pytest.mark.parametrize("target,field", [
    ("journal", "payload_digest"), ("journal", "published_file"),
    ("file", "file_url"), ("draft", "current_version"),
    ("version", "annotation_file"),
])
def test_altered_committed_rows_unknown(committed, target, field):
    from clinic_photos.publication import visit_annotation_readback as readback
    guard, completion, proof, row, rows, calls, writes = committed
    selected = {"journal": row, "file": rows[("File", proof.name)],
                "draft": rows[("Clinic Photo Draft", completion.intent.managed_draft)],
                "version": rows[("Clinic Photo Annotation Version", completion.version)]}[target]
    selected[field] = "wrong"
    with pytest.raises(readback.ReadbackUnknown):
        readback._verify(guard, completion, proof)
    assert not writes


@pytest.mark.parametrize("change", ["phase", "ack", "completion", "lock", "connection", "proof"])
def test_missing_handoff_or_scope_unknown(committed, change):
    from clinic_photos.publication import visit_annotation_readback as readback
    guard, completion, proof, _, _, _, writes = committed
    if change == "phase":
        guard._phase = "publish"
    elif change == "ack":
        guard._visit_publish_acknowledged = False
    elif change == "completion":
        guard._visit_completion = object()
    elif change == "lock":
        guard._file_lock = None
    elif change == "connection":
        guard._db._conn = object()
    else:
        proof = object()
    with pytest.raises(readback.ReadbackUnknown):
        readback._verify(guard, completion, proof)
    assert not writes


def test_replaced_bytes_or_alias_unknown(committed, monkeypatch):
    from clinic_photos.publication import visit_annotation_readback as readback
    guard, completion, proof, _, _, _, _ = committed
    def refuse(*args):
        raise ValueError("changed bytes")
    monkeypatch.setattr(visit_native_save, "_verify_persisted_bytes", refuse)
    with pytest.raises(readback.ReadbackUnknown):
        readback._verify(guard, completion, proof)


def test_alias_added_after_commit_unknown(committed):
    from clinic_photos.publication import visit_annotation_readback as readback
    guard, completion, proof, _, _, _, writes = committed
    guard._db.sql = lambda query, params=(): ([(0,)] if "@@" in query else
                                               [(proof.name, proof.reservation.key),
                                                ("other", proof.reservation.key)])
    with pytest.raises(readback.ReadbackUnknown):
        readback._verify(guard, completion, proof)
    assert not writes
