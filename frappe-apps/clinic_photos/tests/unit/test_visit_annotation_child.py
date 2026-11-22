import copy
import os
import pickle
import sys
from collections import deque
from types import SimpleNamespace

import pytest

from clinic_photos.publication import visit_annotation_child as child


class Hooks:
    def __init__(self):
        self._functions = deque()


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(child, "_attempted", False)
    monkeypatch.setattr(child.process, "SITES_PATH", str(tmp_path))
    (tmp_path / "clinic").mkdir()
    local = SimpleNamespace()
    db = SimpleNamespace(_conn=object(), transaction_writes=0)
    for name in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
        setattr(db, name, Hooks())
    events = []
    frappe = SimpleNamespace(local=local, session=SimpleNamespace(user=None))
    frappe.get_site_path = lambda: os.path.join(local.sites_path, local.site)
    db.sql = lambda query, values=(), **kwargs: [("Physician",)] if "tabHas Role" in query else [1]
    db.get_value = lambda doctype, name, field, **kwargs: {"name": name, "enabled": 1}

    def init(*, site, sites_path):
        events.append("init")
        local.site = site
        local.sites_path = sites_path

    def connect(*, set_admin_as_user):
        assert set_admin_as_user is False
        events.append("connect")
        local.db = db
        frappe.db = db
        frappe.conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "clinic",
                       "clinic_visit_photo_annotations_enabled": True}

    def set_user(user):
        events.append("set_user")
        frappe.session.user = user

    frappe.init, frappe.connect, frappe.set_user = init, connect, set_user
    frappe.destroy = lambda: events.append("destroy")
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    row = {"name": "a" * 32, "actor": "doctor"}
    monkeypatch.setattr(child.frappe_context, "get_save_operation", lambda name: row.copy())

    def verify(projection, *, site, actor):
        events.append("verify")
        assert frappe.session.user is None
        assert site == "clinic" and actor == row["actor"]
        return SimpleNamespace()

    monkeypatch.setattr(child.codec, "load_submitted", verify)
    return frappe, db, events, row


def test_bootstrap_authenticates_before_actor_and_one_shot(env):
    frappe, db, events, row = env
    with child._bootstrap(row["name"], site="clinic") as proof:
        assert events == ["init", "connect", "verify", "set_user"]
        for copier in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError):
                copier(proof)
        child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])
        with pytest.raises(child.ChildDenied):
            child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])
    assert events[-1] == "destroy"
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass


@pytest.mark.parametrize("change", ["site", "actor", "db", "connection", "operation", "request"])
def test_identity_change_burns_proof(env, change):
    frappe, db, events, row = env
    with child._bootstrap(row["name"], site="clinic") as proof:
        site, actor, operation = "clinic", "doctor", row["name"]
        if change == "site":
            frappe.local.site = "other"
        elif change == "actor":
            frappe.session.user = "other"
        elif change == "db":
            frappe.local.db = object()
        elif change == "connection":
            db._conn = object()
        elif change == "operation":
            operation = "b" * 32
        else:
            frappe.local.request = object()
        with pytest.raises(child.ChildDenied):
            child._consume(proof, site=site, actor=actor, operation_id=operation)
        with pytest.raises(child.ChildDenied):
            child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])
    assert events[-1] == "destroy"


def test_inherited_context_refused_before_init_without_destroy(env):
    frappe, db, events, row = env
    frappe.local.site = "already-bound"
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass
    assert events == []


def test_failed_authentication_destroys_without_adopting_actor(env, monkeypatch):
    frappe, db, events, row = env
    def fail(*args, **kwargs):
        raise ValueError("bad projection")
    monkeypatch.setattr(child.codec, "load_submitted", fail)
    with pytest.raises(ValueError):
        with child._bootstrap(row["name"], site="clinic"):
            pass
    assert events == ["init", "connect", "destroy"]


@pytest.mark.parametrize("change", ["flag", "site_flag", "annotation_flag", "dirty", "wrong_row", "no_connection"])
def test_invalid_bootstrap_frame_or_row_refuses_and_cleans(env, monkeypatch, change):
    frappe, db, events, row = env
    if change in ("flag", "site_flag", "annotation_flag", "dirty", "no_connection"):
        original = frappe.connect
        def connect(**kwargs):
            original(**kwargs)
            if change == "flag":
                frappe.conf["clinic_visit_photos_enabled"] = False
            elif change == "site_flag":
                frappe.conf["clinic_visit_photos_site"] = "other"
            elif change == "annotation_flag":
                frappe.conf["clinic_visit_photo_annotations_enabled"] = False
            elif change == "dirty":
                db.before_commit._functions.append(lambda: None)
            else:
                db._conn = None
        frappe.connect = connect
    else:
        row["name"] = "b" * 32
    with pytest.raises(child.ChildDenied):
        with child._bootstrap("a" * 32, site="clinic"):
            pass
    assert "set_user" not in events
    assert events[-1] == "destroy"


def test_proof_expires_on_scope_exit(env):
    frappe, db, events, row = env
    with child._bootstrap(row["name"], site="clinic") as proof:
        pass
    with pytest.raises(child.ChildDenied):
        child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])


@pytest.mark.parametrize("change", ["fork", "thread"])
def test_fork_and_non_main_thread_refuse_before_init(env, monkeypatch, change):
    frappe, db, events, row = env
    if change == "fork":
        monkeypatch.setattr(child, "_import_pid", -1)
    else:
        monkeypatch.setattr(child.threading, "main_thread", lambda: object())
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass
    assert events == []


@pytest.mark.parametrize("actor,enabled,roles", [
    ("Guest", 1, [("Physician",)]), ("Administrator", 1, [("Physician",)]),
    ("doctor", 0, [("Physician",)]), ("doctor", 1, [("Nursing User",)]),
])
def test_non_physician_never_adopted(env, actor, enabled, roles):
    frappe, db, events, row = env
    row["actor"] = actor
    db.get_value = lambda doctype, name, field, **kwargs: {"name": name, "enabled": enabled}
    db.sql = lambda query, values=(), **kwargs: roles if "tabHas Role" in query else [1]
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass
    assert "set_user" not in events


def test_lazy_connection_is_established_through_read(env):
    frappe, db, events, row = env
    db._conn = None
    def sql(query, values=(), **kwargs):
        if query == "select 1":
            db._conn = object()
            return [1]
        return [("Physician",)]
    db.sql = sql
    with child._bootstrap(row["name"], site="clinic") as proof:
        child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])


@pytest.mark.parametrize("when", ["entry", "auth", "adoption", "consume"])
def test_wrong_site_root_refuses(env, monkeypatch, when):
    frappe, db, events, row = env
    if when == "entry":
        frappe.get_site_path = lambda: "/other/clinic"
    elif when == "auth":
        monkeypatch.setattr(child.codec, "load_submitted", lambda *a, **kw: setattr(frappe, "get_site_path", lambda: "/other/clinic"))
    elif when == "adoption":
        original = frappe.set_user
        def set_user(user):
            original(user)
            frappe.get_site_path = lambda: "/other/clinic"
        frappe.set_user = set_user
    if when == "consume":
        with child._bootstrap(row["name"], site="clinic") as proof:
            frappe.get_site_path = lambda: "/other/clinic"
            with pytest.raises(child.ChildDenied):
                child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])
    else:
        with pytest.raises(child.ChildDenied):
            with child._bootstrap(row["name"], site="clinic"):
                pass


def test_proof_identity_is_not_mutable(env):
    frappe, db, events, row = env
    with child._bootstrap(row["name"], site="clinic") as proof:
        for field in ("_actor", "_site", "_operation", "_consumed", "_live"):
            with pytest.raises((AttributeError, TypeError)):
                setattr(proof, field, "other")


def test_frappe_db_proxy_and_lazy_wrapper(env):
    frappe, db, events, row = env
    original = frappe.connect
    class Proxy:
        def __getattr__(self, key):
            return getattr(frappe.local.db, key)
    def connect(**kwargs):
        original(**kwargs)
        frappe.db = Proxy()
        db._conn = None
    def sql(query, values=(), **kwargs):
        if query == "select 1":
            db._conn = object()
            return [1]
        return [("Physician",)]
    frappe.connect = connect
    db.sql = sql
    with child._bootstrap(row["name"], site="clinic") as proof:
        child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])


@pytest.mark.parametrize("change", ["wrapper", "connection"])
def test_database_drift_during_auth_refuses_before_actor(env, monkeypatch, change):
    frappe, db, events, row = env
    def drift(*args, **kwargs):
        if change == "wrapper":
            frappe.local.db = SimpleNamespace()
        else:
            db._conn = object()
    monkeypatch.setattr(child.codec, "load_submitted", drift)
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass
    assert "set_user" not in events


def test_annotation_flag_checked_before_staged_read(env, monkeypatch):
    frappe, db, events, row = env
    original = frappe.connect
    def connect(**kwargs):
        original(**kwargs)
        frappe.conf["clinic_visit_photo_annotations_enabled"] = 1
    frappe.connect = connect
    monkeypatch.setattr(child.codec, "load_submitted", lambda *a, **kw: pytest.fail("staged read"))
    with pytest.raises(child.ChildDenied):
        with child._bootstrap(row["name"], site="clinic"):
            pass


def test_lazy_native_tuple_rows_use_pluck(env):
    frappe, db, events, row = env
    db._conn = None
    def sql(query, values=(), *, pluck=False):
        if query == "select 1":
            db._conn = object()
            native_rows = ((1,),)
            return [item[0] for item in native_rows] if pluck else native_rows
        return [("Physician",)]
    db.sql = sql
    with child._bootstrap(row["name"], site="clinic") as proof:
        child._consume(proof, site="clinic", actor="doctor", operation_id=row["name"])
