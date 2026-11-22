"""Private write scope for photo-owned bookkeeping documents."""

from contextlib import contextmanager
from contextvars import ContextVar

_writing = ContextVar("clinic_visit_bookkeeping", default=None)
_INSERT_TYPES = frozenset({
    "Clinic Visit Photo State",
    "Clinic Visit Photo Consent Audit",
    "Clinic Visit Checkin Witness",
})


@contextmanager
def internal_write(doc):
    previous = getattr(doc.flags, "ignore_permissions", None)
    token = _writing.set(doc)
    try:
        yield
    finally:
        doc.flags.ignore_permissions = previous
        _writing.reset(token)


def require_internal_write(doc):
    import frappe

    if _writing.get() is not doc:
        frappe.throw("Visit photo bookkeeping is server-owned", frappe.PermissionError)


def insert(values):
    import frappe

    if not isinstance(values, dict) or values.get("doctype") not in _INSERT_TYPES:
        frappe.throw("Invalid bookkeeping type", frappe.PermissionError)
    doc = frappe.get_doc(values)
    if doc.doctype != values["doctype"]:
        frappe.throw("Invalid bookkeeping type", frappe.PermissionError)
    with internal_write(doc):
        return doc.insert(ignore_permissions=True)


def save(doc):
    import frappe

    if doc.doctype != "Clinic Visit Photo State":
        frappe.throw("Invalid bookkeeping type", frappe.PermissionError)
    with internal_write(doc):
        return doc.save(ignore_permissions=True)
