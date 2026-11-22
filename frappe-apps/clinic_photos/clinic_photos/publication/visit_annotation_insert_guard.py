"""Private, single-document native insertion scope; not an authorization check."""

from contextlib import contextmanager
from contextvars import ContextVar

from clinic_photos.publication import journal
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication.visit_annotation_intent import VISIT_ANNOTATION_APPEND

_scope = ContextVar("clinic_visit_annotation_insert", default=None)


def _deny():
    scope = _scope.get()
    if scope is not None:
        scope["phase"] = "refused"
    raise journal.JournalAuthorityDenied(journal.SAFE_JOURNAL_DENIAL)


def _active():
    return _scope.get() is not None


def _check(doc, values):
    import frappe

    if (getattr(frappe.local, "site", None) != values["site"]
            or getattr(frappe.session, "user", None) != values["actor"]):
        _deny()
    row = doc.as_dict()
    if (not set(row).issubset(codec._ALLOWED_FIELDS | {"__islocal", "__unsaved"})
            or any(type(row.get(key)) is not type(value) or row.get(key) != value
                   for key, value in values.items())
            or any(journal._row_present(row, key) for key in codec._FORBIDDEN_TEXT)
            or any(not journal._row_absent_int(row, key) for key in codec._FORBIDDEN_INT)):
        _deny()


@contextmanager
def _insertion_scope(doc, values):
    """Bind trusted, previously authorized codec metadata to one native object."""
    if _active():
        _deny()
    if (type(values) is not dict or set(values) != codec._METADATA_FIELDS
            or values.get("doctype") != journal.OPERATION_DOCTYPE
            or values.get("kind") != VISIT_ANNOTATION_APPEND
            or values.get("state") != journal.SUBMITTED_STATE
            or not values.get("site") or not values.get("actor")):
        _deny()
    values = dict(values)
    _check(doc, values)
    scope = {"doc": doc, "values": values, "phase": "before_insert"}
    token = _scope.set(scope)
    try:
        yield
        if scope["phase"] != "done":
            _deny()
        _check(doc, values)
    finally:
        scope["phase"] = "refused"
        _scope.reset(token)


def _require_phase(doc, phase):
    scope = _scope.get()
    if scope is None or scope["doc"] is not doc or scope["phase"] != phase:
        _deny()
    _check(doc, scope["values"])
    scope["phase"] = "validate" if phase == "before_insert" else "done"
