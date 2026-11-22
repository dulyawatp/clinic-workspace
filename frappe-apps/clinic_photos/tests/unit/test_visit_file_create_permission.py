import pytest

from clinic_photos.access import file_mixin, frappe_context, permissions, storage
from clinic_photos.access.policy import Actor
from clinic_photos.publication import visit_native_save, visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile


@pytest.fixture
def permission_env(env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    prepared = dict(owner._visit_prepared_values, state="PREPARED", generation=1)
    object.__setattr__(owner, "_visit_prepared_values", prepared)
    projection = {"doctype": "Clinic Photo Save Operation", "name": owner.operation_id,
                  "state": "SUBMITTED"}
    object.__setattr__(owner, "_projection", projection)
    row = {**projection, **prepared}
    monkeypatch.setattr(frappe_context, "get_save_operation", lambda *a, **kw: row.copy())
    monkeypatch.setattr(frappe_context, "lookup_protection",
                        lambda key, name: [storage.ProtectionRef(
                            "reservation", storage_key=doc.file_url)] if key == doc.file_url else [])
    monkeypatch.setattr(frappe_context, "current_actor",
                        lambda user=None: Actor(user or frappe.session.user, True, True))
    return frappe, owner, handle, doc, row


def test_native_create_checks_defer_twice_only_for_exact_attempt(permission_env, monkeypatch, visit_file_scope):
    _, owner, handle, doc, _ = permission_env
    decisions = []
    def check(self, kind):
        decisions.append(permissions.file_has_permission(self, ptype=kind))
        if not decisions[-1]:
            raise PermissionError
    monkeypatch.setattr(File, "check_permission", check, raising=False)
    def native(self):
        self.check_permission("create")
        self.before_insert()
        raise RuntimeError("stop before insertion")
    monkeypatch.setattr(NativeFile, "insert", native)
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes",
                        lambda self: (_ for _ in ()).throw(RuntimeError("stop before insertion")))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop before insertion"):
                doc.insert()
            assert decisions == [True, True]


@pytest.mark.parametrize("change", ["direct", "substitute", "foreign", "unregistered", "row", "read",
                                    "missing_record", "inconsistent", "sealed", "settled", "unsafe_mode"])
def test_no_broad_permission(permission_env, monkeypatch, visit_file_scope, change):
    _, owner, handle, doc, row = permission_env
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            entry = visit_file_scope._ACTIVE.get()
            if change != "direct":
                visit_file_scope._before_native_insert(doc, (), {})
            attempt = [entry, False, False, None]
            token = file_mixin._VISIT_INSERT.set(attempt) if change != "direct" else None
            try:
                if change == "substitute":
                    doc = File()
                elif change == "unregistered":
                    monkeypatch.setattr(frappe_context, "lookup_protection", lambda *a: [])
                elif change == "row":
                    row["state"] = "PUBLISHED"
                elif change == "missing_record":
                    monkeypatch.setattr(frappe_context, "classify_file", lambda *a, **kw: None)
                elif change == "inconsistent":
                    from clinic_photos.access.policy import ProtectedRecord
                    monkeypatch.setattr(frappe_context, "classify_file", lambda *a, **kw: ProtectedRecord(
                        author="", committed=False, kind="pending", storage_key=doc.file_url,
                        registered=True, inconsistent=True))
                elif change == "sealed":
                    attempt[1] = True
                elif change == "settled":
                    guard._native_settled = True
                elif change == "unsafe_mode":
                    doc.flags.ignore_permissions = True
                assert not permissions.file_has_permission(
                    doc, user="foreign" if change == "foreign" else None,
                    ptype="read" if change == "read" else "create")
            finally:
                if token is not None:
                    file_mixin._VISIT_INSERT.reset(token)
                guard.abort_publication("test")


def test_native_permission_denial_prevents_bytes(permission_env, monkeypatch, visit_file_scope):
    _, owner, handle, doc, _ = permission_env
    calls = []
    def native_check(self, kind):
        assert permissions.file_has_permission(self, ptype=kind)
        raise PermissionError("native deny")
    monkeypatch.setattr(File, "check_permission", native_check, raising=False)
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.check_permission("create"))
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes", lambda self: calls.append(True))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="native deny"):
                doc.insert()
            assert not calls
