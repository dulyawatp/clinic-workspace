"""Unwired one-shot bootstrap for a visit drawing child; never publishes."""

import os
import threading
from contextlib import contextmanager

from clinic_photos.access import frappe_context
from clinic_photos.publication import process
from clinic_photos.publication import visit_annotation_journal_codec as codec

_import_pid = os.getpid()
_attempted = False
_active = None


class ChildDenied(RuntimeError):
    pass


class _ChildProof:
    __slots__ = ()

    def __copy__(self):
        raise TypeError("child proof cannot be copied")

    def __deepcopy__(self, memo):
        raise TypeError("child proof cannot be copied")

    def __reduce__(self):
        raise TypeError("child proof cannot be serialized")


def _frame_clean(frappe):
    return (getattr(frappe.local, "request", None) is None
            and getattr(frappe.local, "job", None) is None
            and type(getattr(frappe.local.db, "transaction_writes", None)) is int
            and process._context_is_clean(frappe))


def _volume(frappe, site):
    sites = os.path.realpath(process.SITES_PATH)
    configured = getattr(frappe.local, "sites_path", None)
    if not isinstance(configured, str) or os.path.realpath(configured) != sites:
        raise ChildDenied("visit drawing child unavailable")
    root = os.path.join(sites, site)
    native = frappe.get_site_path()
    if (not isinstance(native, str) or not os.path.isdir(sites)
            or not os.path.isdir(root) or os.path.realpath(root) != root
            or os.path.realpath(native) != root):
        raise ChildDenied("visit drawing child unavailable")
    return sites, root


def _physician(db, actor):
    if not isinstance(actor, str) or not actor or actor in ("Guest", "Administrator"):
        raise ChildDenied("visit drawing child unavailable")
    user = db.get_value("User", actor, ["name", "enabled"], as_dict=True)
    roles = db.sql(
        """select role from `tabHas Role`
           where parent = %s and parenttype = 'User' order by name""",
        (actor,),
    )
    if not user or user.get("name") != actor or user.get("enabled") != 1 or ("Physician",) not in roles:
        raise ChildDenied("visit drawing child unavailable")


@contextmanager
def _bootstrap(operation_id, *, site):
    """Authenticate one submitted row in a fresh native frame; no publisher callback."""
    global _attempted, _active
    if (_attempted or os.getpid() != _import_pid
            or threading.current_thread() is not threading.main_thread()
            or not process._valid_operation_id(operation_id)
            or not process._valid_visit_site(site)):
        raise ChildDenied("visit drawing child unavailable")
    _attempted = True
    import frappe

    if any(getattr(frappe.local, field, None) is not None
           for field in ("site", "db", "request", "job")):
        raise ChildDenied("visit drawing child unavailable")
    initialized = False
    proof = None
    try:
        initialized = True
        frappe.init(site=site, sites_path=process.SITES_PATH)
        frappe.connect(set_admin_as_user=False)
        db = getattr(frappe.local, "db", None)
        if (getattr(frappe.local, "site", None) != site
                or not process._visit_gate_enabled(site, getattr(frappe, "conf", None))
                or frappe.conf.get("clinic_visit_photo_annotations_enabled") is not True
                or not _frame_clean(frappe)):
            raise ChildDenied("visit drawing child unavailable")
        volume = _volume(frappe, site)
        initial_connection = getattr(db, "_conn", None)
        result = db.sql("select 1", pluck=True)
        connection = getattr(db, "_conn", None)
        if (result != [1] or connection is None or frappe.local.db is not db
                or (initial_connection is not None and initial_connection is not connection)
                or not _frame_clean(frappe)):
            raise ChildDenied("visit drawing child unavailable")
        row = frappe_context.get_save_operation(operation_id)
        if not isinstance(row, dict) or row.get("name") != operation_id:
            raise ChildDenied("visit drawing child unavailable")
        row = dict(row)
        row.setdefault("doctype", "Clinic Photo Save Operation")
        actor = row.get("actor")
        codec.load_submitted(row, site=site, actor=actor)
        _physician(db, actor)
        if (frappe.local.db is not db or db._conn is not connection
                or frappe.local.site != site or _volume(frappe, site) != volume
                or not _frame_clean(frappe)):
            raise ChildDenied("visit drawing child unavailable")
        frappe.set_user(actor)
        if (getattr(frappe.session, "user", None) != actor
                or frappe.local.db is not db or db._conn is not connection
                or frappe.local.site != site or _volume(frappe, site) != volume
                or not _frame_clean(frappe)):
            raise ChildDenied("visit drawing child unavailable")
        proof = _ChildProof()
        _active = (proof, {
            "pid": os.getpid(), "thread": threading.current_thread(),
            "site": site, "actor": actor, "operation": operation_id,
            "db": db, "connection": connection, "volume": volume,
            "consumed": False,
        })
        yield proof
    finally:
        _active = None
        if initialized:
            try:
                frappe.destroy()
            except Exception:
                pass


def _consume(proof, *, site, actor, operation_id):
    """Future coordinator's one-use seam; arguments only narrow the bound identity."""
    import frappe

    if type(proof) is not _ChildProof or _active is None or _active[0] is not proof:
        raise ChildDenied("visit drawing child unavailable")
    state = _active[1]
    available = not state["consumed"]
    state["consumed"] = True
    if (not available or state["pid"] != os.getpid()
            or state["thread"] is not threading.current_thread()
            or (site, actor, operation_id) != (state["site"], state["actor"], state["operation"])
            or getattr(frappe.local, "site", None) != state["site"]
            or getattr(frappe.session, "user", None) != state["actor"]
            or getattr(frappe.local, "db", None) is not state["db"]
            or getattr(state["db"], "_conn", None) is not state["connection"]
            or _volume(frappe, state["site"]) != state["volume"]
            or not _frame_clean(frappe)):
        raise ChildDenied("visit drawing child unavailable")
