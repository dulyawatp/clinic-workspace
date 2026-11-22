import hashlib
import json
from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import staging, visit_file_scope
from clinic_photos.publication import visit_annotation_journal_codec as codec
from test_visit_annotation_domain import stage_env
from test_visit_transaction_guard import env


@pytest.fixture
def ready(stage_env, monkeypatch):
    _, guard, entry, _, rows, item = stage_env
    owner = guard._exclusion_owner
    data = item.canonical_visit_annotation.encode()
    material = json.dumps(
        ["clinic-visit-annotation-prepared-v1", item.binding.site, item.binding.actor,
         owner.operation_id, item.target_identity, item.intent_digest, 1],
        ensure_ascii=True, separators=(",", ":"),
    ).encode()
    key = "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json"
    reservation = entry[3]._replace(key=key)
    entry = (entry[0], guard, entry[2], reservation, entry[4])
    visit_file_scope._ACTIVE.set(entry)
    doc = entry[2]
    doc.file_url, doc.file_name = key, key.rsplit("/", 1)[-1]
    rows[("File", doc.name)].update(file_url=key, file_name=doc.file_name)
    staged = staging.StagedVisitAnnotation("a" * 32 + ".json", reservation.digest,
                                           reservation.size, staging.VISIT_ANNOTATION_JSON)
    projection = codec._metadata_values(item, staged)
    projection["name"] = owner.operation_id
    object.__setattr__(owner, "_projection", projection)
    values = dict(state="PREPARED", reservation_key=key, generation=1,
                  expected_sha256=reservation.digest, expected_size=reservation.size)
    object.__setattr__(owner, "_visit_prepared_values", values)
    row = {**projection, **values}
    version = item.managed_draft + "-v1"
    rows[("Clinic Photo Draft", item.managed_draft)] = dict(
        name=item.managed_draft, author=guard._actor, owner=guard._actor,
        photo=item.binding.original_photo, current_version_number=1, current_version=version)
    rows[("Clinic Photo Annotation Version", version)] = dict(
        name=version, draft=item.managed_draft, version_number=1,
        annotation_file=doc.name, storage_key=key, state="committed")
    guard._domain_stage_started = True
    proof = SimpleNamespace(operation=owner.operation_id, intent=item, reservation=reservation,
                            file=doc, name=doc.name, device=12, inode=34,
                            size=reservation.size, sha256=reservation.digest)
    monkeypatch.setattr(visit_file_scope, "_completed_file", lambda g: proof if g is guard else None)
    monkeypatch.setattr(staging, "read_staged_visit_annotation", lambda _: data)
    monkeypatch.setattr(frappe_context, "get_save_operation",
                        lambda operation, for_update: dict(row) if operation == owner.operation_id and for_update else None)
    writes = []
    def set_value(doctype, name, values, update_modified):
        guard._db.after_commit.add(lambda: None)
        guard._db.after_rollback.add(lambda: None)
        writes.append((doctype, name, dict(values), update_modified))
        row.update(values)
    guard._db.set_value = set_value
    def sql(query, args):
        assert query.startswith("UPDATE `tabClinic Photo Save Operation` SET ")
        assert "WHERE `name` = %s AND `state` = %s" in query
        state, file, doctype, version, number, identity, name, old_state = args
        guard._db._cursor.rowcount = int(name == owner.operation_id and row["state"] == old_state)
        if guard._db._cursor.rowcount:
            values = dict(state=state, published_file=file, published_domain_doctype=doctype,
                          published_domain_name=version, published_version=number,
                          creation_identity=identity)
            writes.append(("Clinic Photo Save Operation", name, values, False))
            row.update(values)
    guard._db.sql = sql
    return guard, row, rows, item, writes


def test_stage_published_and_verify_locked_readback(ready):
    from clinic_photos.publication import visit_annotation_publish as publish
    guard, row, _, item, writes = ready
    before = (tuple(guard._queue("after_commit")), tuple(guard._queue("after_rollback")))
    completion = publish._stage(guard)
    assert before == (tuple(guard._queue("after_commit")), tuple(guard._queue("after_rollback")))
    assert completion.guard is guard
    assert completion.intent is item
    assert row["state"] == "PUBLISHED"
    assert len(writes) == 1
    assert set(writes[0][2]) == {
        "state", "published_file", "published_domain_doctype", "published_domain_name",
        "published_version", "creation_identity",
    }


def test_completion_cannot_be_caller_constructed(env):
    env[0].PermissionError = type("PermissionError", (Exception,), {})
    from clinic_photos.publication import visit_annotation_publish as publish
    with pytest.raises(publish.PublishDenied):
        publish._Completion(None, None, None, None, None, None)


@pytest.mark.parametrize("target,field", [
    ("draft", "current_version"), ("draft", "current_version_number"),
    ("version", "annotation_file"), ("version", "storage_key"), ("file", "file_url"),
    ("journal", "payload_digest"), ("journal", "name"), ("journal", "published_file"),
])
def test_mismatch_aborts_before_journal_write(ready, target, field):
    from clinic_photos.publication import visit_annotation_publish as publish
    guard, row, rows, item, writes = ready
    selected = {"draft": rows[("Clinic Photo Draft", item.managed_draft)],
                "version": rows[("Clinic Photo Annotation Version", item.managed_draft + "-v1")],
                "file": rows[("File", "FILE-1")], "journal": row}[target]
    selected[field] = "wrong"
    with pytest.raises(publish.PublishDenied):
        publish._stage(guard)
    assert not writes
    assert guard.publication_aborted()


def test_malformed_post_write_aborts(ready, monkeypatch):
    from clinic_photos.publication import visit_annotation_publish as publish
    guard, row, _, _, writes = ready
    sql = guard._db.sql
    def corrupt(*args):
        sql(*args)
        row["published_version"] = 99
    monkeypatch.setattr(guard._db, "sql", corrupt)
    with pytest.raises(publish.PublishDenied):
        publish._stage(guard)
    assert len(writes) == 1
    assert guard.publication_aborted()


def test_conditional_update_must_match_one_row(ready):
    from clinic_photos.publication import visit_annotation_publish as publish
    guard, _, _, _, writes = ready
    guard._db.sql = lambda *args: setattr(guard._db._cursor, "rowcount", 0)
    with pytest.raises(publish.PublishDenied):
        publish._stage(guard)
    assert not writes
    assert guard.publication_aborted()
