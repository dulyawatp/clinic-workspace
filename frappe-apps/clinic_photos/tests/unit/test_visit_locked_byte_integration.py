import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_file_aliases, visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


@pytest.mark.parametrize("alias", ["exact", "encoded", None])
def test_current_locking_alias_scan_precedes_any_bytes(
        tmp_path, env, monkeypatch, visit_file_scope, alias):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    events = []
    key = doc.file_url
    current = [] if alias is None else [
        ("OTHER", key if alias == "exact" else key.replace("clinic", "%63linic"))]

    def sql(query, params=None):
        normalized = query.lower()
        if "get_lock" in normalized:
            events.append("acquire")
            return ((1,),)
        if "innodb_snapshot_isolation" in normalized:
            events.append("isolation")
            return ((0,),)
        if "lock in share mode" in normalized:
            events.append("current_scan")
            return current
        if "release_lock" in normalized:
            events.append("release")
            return ((1,),)
        raise AssertionError(query)

    monkeypatch.setattr(db, "sql", sql, raising=False)
    def snapshot():
        events.append("old_snapshot")
        return []
    monkeypatch.setattr(frappe_context, "_all_file_rows", snapshot)
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    original_rollback = db.rollback
    def rollback():
        events.append("rollback")
        original_rollback()
    monkeypatch.setattr(db, "rollback", rollback)
    def insert(self):
        assert frappe_context._all_file_rows() == []
        self.before_insert()
        events.append("after_create")
        raise RuntimeError("stop before row")
    monkeypatch.setattr(NativeFile, "insert", insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            expected = RuntimeError if alias is None else visit_file_aliases.AliasProofDenied
            with pytest.raises(expected):
                doc.insert()
            target = tmp_path / doc.file_name
            assert events[:4] == ["old_snapshot", "acquire", "isolation", "current_scan"]
            assert guard._file_lock is not None
            if alias is None:
                assert target.read_bytes() == b"data"
                assert events[-1] == "after_create"
            else:
                assert not target.exists()
                assert "after_create" not in events
            assert guard.publication_aborted()
            guard._rollback()
            assert events[-2:] == ["rollback", "release"]
            assert events[-1] == "release"
            assert db.rollbacks == 1
            assert guard._file_lock is None
