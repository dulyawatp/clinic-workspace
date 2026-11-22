import importlib

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_file_scope, visit_transaction
from test_visit_transaction_guard import env
from test_visit_transaction_file_lock import setup_scope


@pytest.fixture
def scoped(env):
    frappe, owner, db = env
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        entry, token = setup_scope(guard, owner)
        guard._file_lock = frappe_context._lock_name(entry[3].key)
        try:
            yield frappe, guard, db, entry
        finally:
            visit_file_scope._ACTIVE.reset(token)


def module():
    return importlib.import_module("clinic_photos.publication.visit_file_aliases")


def test_current_locking_keyset_scan_and_stale_snapshot(scoped, monkeypatch):
    _, _, db, entry = scoped
    aliases = module()
    monkeypatch.setattr(aliases, "_PAGE_SIZE", 2)
    calls = []
    current = [("a", None), ("b", ""), ("c", "/public/files/other")]
    def sql(query, params=()):
        calls.append((query, params))
        if "@@session.innodb_snapshot_isolation" in query:
            return [(0,)]
        assert "LOCK IN SHARE MODE" in query and "ORDER BY name" in query
        assert "LIMIT %s" in query
        if len(params) == 1:
            assert "WHERE" not in query
            return current[:params[0]]
        assert "name > %s" in query
        return [row for row in current if row[0] > params[0]][:params[1]]
    db.sql = sql
    aliases._verify_no_holders(entry[2])
    assert [args for query, args in calls[1:]] == [(2,), ("b", 2)]
    # A stale non-locking snapshot would be empty, but current read sees this alias.
    current.append(("d", entry[3].key.replace("clinic-", "%63linic-")))
    with pytest.raises(aliases.AliasProofDenied):
        aliases._verify_no_holders(entry[2])


def test_post_requires_exact_singleton(scoped):
    _, _, db, entry = scoped
    aliases = module()
    entry[2].name = "FILE-1"
    db.sql = lambda query, params=(): ([(0,)] if "@@" in query else [("FILE-1", entry[3].key)])
    aliases._verify_single_holder(entry[2])
    db.sql = lambda query, params=(): ([(0,)] if "@@" in query else [("other", entry[3].key)])
    with pytest.raises(aliases.AliasProofDenied):
        aliases._verify_single_holder(entry[2])


@pytest.mark.parametrize("result", [[(1,)], [(None,)], [], [("0",)], [(False,)]])
def test_snapshot_isolation_must_be_explicit_integer_zero(scoped, result):
    _, _, db, entry = scoped
    db.sql = lambda *args: result
    with pytest.raises(module().AliasProofDenied):
        module()._verify_no_holders(entry[2])


@pytest.mark.parametrize("rows", [[("a", 123)], [(None, "x")], [("a",)], [("a", "x"), ("a", "y")]])
def test_malformed_rows_denied(scoped, rows):
    _, _, db, entry = scoped
    db.sql = lambda query, params=(): [(0,)] if "@@" in query else rows
    with pytest.raises(module().AliasProofDenied):
        module()._verify_no_holders(entry[2])


def test_budget_and_connection_swap_fail_closed(scoped, monkeypatch):
    _, guard, db, entry = scoped
    aliases = module()
    monkeypatch.setattr(aliases, "_PAGE_SIZE", 1)
    monkeypatch.setattr(aliases, "_MAX_PAGES", 2)
    db.sql = lambda query, params=(): ([(0,)] if "@@" in query else
                                      [((params[0] if len(params) == 2 else "") + "a", "")])
    with pytest.raises(aliases.AliasProofDenied):
        aliases._verify_no_holders(entry[2])
    guard._aborted = False
    def swap(query, params=()):
        if "@@" in query:
            return [(0,)]
        db._conn = object()
        return []
    db.sql = swap
    with pytest.raises(aliases.AliasProofDenied):
        aliases._verify_no_holders(entry[2])


@pytest.mark.parametrize("change", ["lock", "uncertain", "reservation", "file"])
def test_wrong_lock_or_scope_denied_before_sql(scoped, change):
    _, guard, db, entry = scoped
    aliases = module()
    db.sql = lambda *args: pytest.fail("unexpected SQL")
    file_doc = entry[2]
    if change == "lock":
        guard._file_lock = "another lock"
    elif change == "uncertain":
        guard._file_lock_uncertain = True
    elif change == "reservation":
        guard._exclusion_owner._visit_prepared_values["reservation_key"] = "/private/files/other"
    else:
        file_doc = object()
    with pytest.raises(aliases.AliasProofDenied):
        aliases._verify_no_holders(file_doc)
