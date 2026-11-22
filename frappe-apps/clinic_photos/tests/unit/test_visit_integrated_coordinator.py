"""Transaction integration with real coordinator, File mixin, filesystem and codecs.

Frappe's native Document and DB are fixture adapters. Authorization resolution
and preparation/continuation are stubbed; domain, journal and readback are real.
"""
import copy
import hashlib
import json
import os
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import staging, visit_transaction, writer
from test_visit_annotation_staging import intent
from test_visit_transaction_guard import env
from test_visit_native_insert_gate import File, NativeFile


@pytest.fixture
def integrated(env, tmp_path, monkeypatch):
    frappe, owner, db = env
    frappe.PermissionError = PermissionError
    from clinic_photos.publication import visit_annotation_prepare as prepare
    from clinic_photos.publication import visit_annotation_journal_codec as codec

    item = intent()
    item = replace(item, binding=replace(item.binding, site="clinic", actor="doctor"))
    operation = "1" * 32
    data = item.canonical_visit_annotation.encode()
    staged = staging.StagedVisitAnnotation("a" * 32 + ".json", hashlib.sha256(data).hexdigest(),
                                           len(data), staging.VISIT_ANNOTATION_JSON)
    projection = {**codec._metadata_values(item, staged), "name": operation}
    material = json.dumps(["clinic-visit-annotation-prepared-v1", "clinic", "doctor",
                           operation, item.target_identity, item.intent_digest, 1],
                          ensure_ascii=True, separators=(",", ":")).encode()
    key = "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json"
    values = {"state": "PREPARED", "reservation_key": key, "generation": 1,
              "expected_sha256": staged.sha256, "expected_size": staged.size}
    object.__setattr__(owner, "_intent", item)
    object.__setattr__(owner, "_operation_id", operation)
    object.__setattr__(owner, "_projection", projection)
    committed = {("Clinic Photo Save Operation", operation): dict(projection)}
    working = copy.deepcopy(committed)
    events = []
    control = SimpleNamespace(fail_journal=False)
    db._cursor = SimpleNamespace(rowcount=0)

    def commit():
        db.before_rollback.clear()
        db.after_rollback.clear()
        db.before_commit.run()
        committed.clear()
        committed.update(copy.deepcopy(working))
        events.append(("commit", committed[("Clinic Photo Save Operation", operation)]["state"]))
        db.after_commit.run()

    def rollback():
        db.before_commit.clear()
        db.after_commit.clear()
        db.before_rollback.run()
        working.clear()
        working.update(copy.deepcopy(committed))
        events.append(("rollback",))
        db.after_rollback.run()

    db.commit, db.rollback = commit, rollback

    def get_value(doctype, name, fields, **kwargs):
        assert kwargs == {"as_dict": True, "for_update": True}
        row = working.get((doctype, name))
        return None if row is None else {field: row.get(field) for field in fields}
    db.get_value = get_value

    def set_value(doctype, name, changes, *, update_modified):
        assert not update_modified
        if changes.get("state") == "PUBLISHED" and control.fail_journal:
            events.append(("journal_failure",))
            raise RuntimeError("injected journal failure")
        working[(doctype, name)].update(changes)
    db.set_value = set_value
    monkeypatch.setattr(frappe_context, "get_save_operation",
                        lambda name, for_update: copy.deepcopy(working.get(
                            ("Clinic Photo Save Operation", name))))
    monkeypatch.setattr(staging, "read_staged_visit_annotation", lambda _: data)

    def prepare_attempt(actual):
        assert actual is owner
        working[("Clinic Photo Save Operation", operation)].update(values)
        object.__setattr__(owner, "_visit_prepared_values", dict(values))
        object.__setattr__(owner, "_visit_prepare_phase", "armed")
        def committed_callback():
            object.__setattr__(owner, "_visit_prepare_phase", "committed")
        def rollback_callback():
            object.__setattr__(owner, "_visit_prepare_phase", "burned")
        witness = prepare._PreparationWitness(owner, committed_callback, rollback_callback)
        object.__setattr__(owner, "_visit_prepare_handle", witness)
        db.after_commit.add(witness.committed)
        db.after_rollback.add(witness.rolled_back)
        return witness
    monkeypatch.setattr(prepare, "prepare", prepare_attempt)
    def continuation(actual, witness):
        assert actual is owner and witness is owner._visit_prepare_handle
        assert owner._visit_prepare_phase == "committed"
        return prepare._ContinuedReservation(item, operation, 1, key, staged.sha256, staged.size)
    monkeypatch.setattr(prepare, "continue_after_commit", continuation)

    def sql(query, params=None):
        text = query.lower()
        if "get_lock" in text or "release_lock" in text:
            events.append(("lock" if "get_lock" in text else "unlock",))
            return [(1,)]
        if "innodb_snapshot_isolation" in text:
            return [(0,)]
        if "select name, file_url" in text:
            rows = sorted((name, row["file_url"]) for (doctype, name), row in working.items()
                          if doctype == "File")
            if len(params) == 2:
                rows = [row for row in rows if row[0] > params[0]]
            return rows[:params[-1]]
        if "from `tabfile`" in text and "where name" in text:
            row = working.get(("File", params[0]))
            fields = ("name", *frappe_context.FILE_IDENTITY_FIELDS)
            return [] if row is None else [tuple(row.get(field) for field in fields)]
        if text.startswith("update `tabclinic photo save operation`"):
            if control.fail_journal:
                events.append(("journal_failure",))
                raise RuntimeError("injected journal failure")
            state, file, doctype, name, version, identity, operation_id, old_state = params
            row = working[("Clinic Photo Save Operation", operation_id)]
            db._cursor.rowcount = int(row["state"] == old_state)
            if db._cursor.rowcount:
                row.update(state=state, published_file=file, published_domain_doctype=doctype,
                           published_domain_name=name, published_version=version,
                           creation_identity=identity)
            return []
        if text.startswith("update `tabclinic photo draft`"):
            number, pointer, draft_name, old, old_pointer = params
            row = working[("Clinic Photo Draft", draft_name)]
            db._cursor.rowcount = int(row["current_version_number"] == old and
                                      row["current_version"] == old_pointer)
            if db._cursor.rowcount:
                row.update(current_version_number=number, current_version=pointer)
            return []
        raise AssertionError(query)
    db.sql = sql
    monkeypatch.setitem(sys.modules, "frappe.utils", SimpleNamespace(get_hook_method=lambda _: None))
    monkeypatch.setattr(writer, "_open_visit_files_directory",
                        lambda _: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    for attr in ("set_folder_name", "set_is_private", "set_file_name", "validate_attachment_limit",
                 "set_file_type", "validate_file_extension", "validate_private_file_access",
                 "validate_file_path"):
        monkeypatch.setattr(File, attr, lambda self: None, raising=False)
    monkeypatch.setattr(File, "check_max_file_size", lambda self: len(self.content), raising=False)

    def native_insert(self):
        events.append(("file_insert",))
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        self.flags.in_insert = False
        self.db_insert(ignore_if_duplicate=False)
        self.flags.in_insert = True
        self.clear_cache()
        self.notify_update()
        assert list(db.after_rollback._functions) == [self.on_rollback]
        assert len(db.after_commit._functions) == 1
        self.flags.in_insert = False
        return self
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)
    def native_clear(self):
        events.append(("native_cache",))
        db.after_commit.add(lambda: None)
    def native_notify(self):
        events.append(("native_notify",))
        db.after_commit.add(lambda: None)
    monkeypatch.setattr(NativeFile, "clear_cache", native_clear, raising=False)
    monkeypatch.setattr(NativeFile, "notify_update", native_notify, raising=False)
    def insert_file(self, *args, **kwargs):
        working[("File", self.name)] = {field: getattr(self, field, None)
                                       for field in ("name", *frappe_context.FILE_IDENTITY_FIELDS)}
    monkeypatch.setattr(NativeFile, "db_insert", insert_file, raising=False)

    class Doc:
        def __init__(self, values):
            self.values = values
        def db_insert(self, *, ignore_if_duplicate):
            assert ignore_if_duplicate is False
            row_key = (self.values["doctype"], self.values["name"])
            if row_key in working:
                raise ValueError("duplicate")
            working[row_key] = dict(self.values)
            events.append(("domain_insert", row_key[0]))
    def get_doc(values):
        if values["doctype"] != "File":
            return Doc(values)
        doc = File()
        doc.__dict__.update(values)
        doc.is_folder = doc.is_remote_file = False
        return doc
    frappe.get_doc = get_doc
    return SimpleNamespace(owner=owner, db=db, item=item, key=key, data=data, committed=committed,
                           working=working, events=events, control=control, path=tmp_path / key.rsplit("/", 1)[-1])


@pytest.mark.parametrize("fail", [False, True])
def test_real_file_domain_journal_readback_transaction(integrated, fail):
    from clinic_photos.publication import visit_annotation_coordinator
    case = integrated
    case.control.fail_journal = fail
    if fail:
        with pytest.raises(Exception):
            visit_annotation_coordinator._publish_verified(case.owner)
        assert ("journal_failure",) in case.events
        assert case.events.count(("commit", "PREPARED")) == 1
        assert case.events.count(("file_insert",)) == 1
        assert case.committed[("Clinic Photo Save Operation", "1" * 32)]["state"] == "PREPARED"
        assert not any(doctype in ("File", "Clinic Photo Draft", "Clinic Photo Annotation Version")
                       for doctype, _ in case.committed)
        assert case.working == case.committed
        assert case.events.count(("rollback",)) == 1
    else:
        result = visit_annotation_coordinator._publish_verified(case.owner)
        assert ("native_cache",) not in case.events and ("native_notify",) not in case.events
        assert result.file == "FILE-1"
        assert case.events.count(("file_insert",)) == 1
        assert [event for event in case.events if event[0] == "commit"] == [
            ("commit", "PREPARED"), ("commit", "PUBLISHED")]
        assert case.path.read_bytes() == case.data
        assert case.committed[("Clinic Photo Annotation Version", result.version)]["annotation_file"] == result.file


def test_legacy_post_save_delegates(integrated):
    doc = File()
    doc.clear_cache()
    doc.notify_update()
    assert integrated.events[-2:] == [("native_cache",), ("native_notify",)]


@pytest.mark.parametrize("method", ["clear_cache", "notify_update"])
def test_active_scope_without_current_attempt_denied(env, monkeypatch, method):
    from test_visit_file_scope import setup_scope
    from clinic_photos.publication import visit_file_scope
    env[0].PermissionError = PermissionError
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            with visit_file_scope._file_scope(guard, handle, doc):
                getattr(doc, method)()
        assert guard.publication_aborted()
