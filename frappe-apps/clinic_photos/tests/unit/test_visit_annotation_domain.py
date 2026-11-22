import importlib
import hashlib
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_file_scope, visit_transaction
from test_visit_transaction_guard import env
from test_visit_transaction_file_lock import setup_scope
from test_visit_annotation_staging import intent


def test_domain_stage_module_exists():
    assert importlib.util.find_spec("clinic_photos.publication.visit_annotation_domain") is not None


@pytest.fixture
def stage_env(env, monkeypatch):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        item = intent()
        # Use the authenticated fixture actor/site for this private lane.
        from dataclasses import replace
        item = replace(item, binding=replace(item.binding, actor="doctor", site="clinic"))
        object.__setattr__(owner, "_intent", item)
        data = item.canonical_visit_annotation.encode()
        reservation = entry[3]._replace(intent=item, digest=hashlib.sha256(data).hexdigest(), size=len(data))
        owner._visit_prepared_values.update(expected_sha256=reservation.digest, expected_size=reservation.size)
        entry = (entry[0], guard, entry[2], reservation, entry[4])
        visit_file_scope._ACTIVE.set(entry)
        file_doc = entry[2]
        file_doc.name = "FILE-1"
        file_doc.content = data
        file_doc.file_size = reservation.size
        file_doc.content_hash = hashlib.md5(data, usedforsecurity=False).hexdigest()
        guard._native_file = file_doc
        guard._native_settled = True
        guard._native_queue = db.after_rollback._functions
        guard._native_callback = object()
        guard._native_queue.append(guard._native_callback)
        monkeypatch.setattr(visit_transaction._VisitTransaction, "_is_native_file_rollback",
                            staticmethod(lambda callback, doc: callback is guard._native_callback and doc is file_doc))
        guard._file_lock = frappe_context._lock_name(reservation.key)
        rows = {}
        file_row = dict(name=file_doc.name, file_url=reservation.key,
                        file_name=file_doc.file_name, owner="doctor", is_private=1,
                        file_size=reservation.size, content_hash=file_doc.content_hash,
                        attached_to_doctype=None, attached_to_name=None, attached_to_field=None)
        rows[("File", file_doc.name)] = file_row
        def get_value(doctype, name, fields, **kw):
            assert kw == {"as_dict": True, "for_update": True}
            return ({field: rows[(doctype, name)].get(field) for field in fields}
                    if (doctype, name) in rows else None)
        db.get_value = get_value
        db._cursor = SimpleNamespace(rowcount=0)
        def sql(query, params):
            assert query.startswith("UPDATE `tabClinic Photo Draft`")
            number, pointer, name, old_number, old_pointer = params
            row = rows[("Clinic Photo Draft", name)]
            if row["current_version_number"] == old_number and row["current_version"] == old_pointer:
                row.update(current_version_number=number, current_version=pointer)
                db._cursor.rowcount = 1
            else:
                db._cursor.rowcount = 0
        db.sql = sql
        class Doc:
            def __init__(self, values):
                self.values = values
            def db_insert(self, *, ignore_if_duplicate):
                assert ignore_if_duplicate is False
                key = (self.values["doctype"], self.values["name"])
                if key in rows:
                    raise ValueError("duplicate")
                rows[key] = dict(self.values)
        frappe.get_doc = lambda values: Doc(values)
        mod = importlib.import_module("clinic_photos.publication.visit_annotation_domain")
        @dataclass(frozen=True)
        class Receipt:
            operation: str
            reservation: object
            file_doc: object
            name: str
            evidence: object
        receipt = Receipt(owner.operation_id, reservation, file_doc, file_doc.name,
                          SimpleNamespace(size=reservation.size, sha256=reservation.digest))
        monkeypatch.setattr(visit_file_scope, "_completed_file", lambda actual: receipt
                            if actual is guard else None, raising=False)
        try:
            yield mod, guard, entry, receipt, rows, item
        finally:
            visit_file_scope._ACTIVE.reset(token)


def test_stage_creates_exact_version_and_advances_head(stage_env):
    mod, guard, entry, receipt, rows, item = stage_env
    name = mod._stage(guard)
    draft = rows[("Clinic Photo Draft", item.managed_draft)]
    version = rows[("Clinic Photo Annotation Version", name)]
    assert draft["current_version_number"] == 1 and draft["current_version"] == name
    assert version["draft"] == item.managed_draft and version["state"] == "committed"
    assert version["annotation_file"] == entry[2].name and version["storage_key"] == entry[3].key
    assert not guard._aborted


def test_stale_head_refused_before_domain_writes(stage_env):
    mod, guard, entry, receipt, rows, item = stage_env
    rows[("Clinic Photo Draft", item.managed_draft)] = dict(
        name=item.managed_draft, author="doctor", owner="doctor", photo=item.binding.original_photo,
        current_version_number=1, current_version="old")
    before = dict(rows)
    with pytest.raises(mod.DomainDenied):
        mod._stage(guard)
    assert rows == before and guard.publication_aborted()


def test_wrong_file_or_duplicate_refused(stage_env):
    mod, guard, entry, receipt, rows, item = stage_env
    rows[("File", entry[2].name)]["file_url"] = "/private/files/wrong"
    with pytest.raises(mod.DomainDenied):
        mod._stage(guard)
    assert not any(key[0] == "Clinic Photo Draft" for key in rows)


def test_duplicate_version_never_overwritten(stage_env):
    mod, guard, entry, receipt, rows, item = stage_env
    version_name = item.managed_draft + "-v1"
    rows[("Clinic Photo Annotation Version", version_name)] = {"name": version_name}
    with pytest.raises(mod.DomainDenied):
        mod._stage(guard)
    assert rows[("Clinic Photo Annotation Version", version_name)] == {"name": version_name}


def test_existing_zero_head_and_single_use(stage_env):
    mod, guard, entry, receipt, rows, item = stage_env
    rows[("Clinic Photo Draft", item.managed_draft)] = dict(
        name=item.managed_draft, author="doctor", owner="doctor", photo=item.binding.original_photo,
        current_version_number=0, current_version="")
    assert mod._stage(guard) == item.managed_draft + "-v1"
    with pytest.raises(mod.DomainDenied):
        mod._stage(guard)


def test_missing_bridge_proof_rejected(stage_env, monkeypatch):
    mod, guard, entry, receipt, rows, item = stage_env
    def refuse(guard):
        raise ValueError("missing proof")
    monkeypatch.setattr(visit_file_scope, "_completed_file", refuse)
    with pytest.raises(mod.DomainDenied):
        mod._stage(guard)
    assert set(rows) == {("File", entry[2].name)}
