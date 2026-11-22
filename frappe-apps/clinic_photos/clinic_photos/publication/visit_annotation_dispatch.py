"""Private one-way visit handoff; a job never establishes saved status."""

from clinic_photos.access import frappe_context
from clinic_photos.publication import launcher, process, visit_annotation_exec
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication.visit_annotation_intent import VISIT_ANNOTATION_APPEND

_JOB = "clinic_photos.publication.visit_annotation_dispatch._run"


def _site(frappe):
    site = launcher._visit_server_site(frappe)
    if frappe.conf.get("clinic_visit_photo_annotations_enabled") is not True:
        raise launcher.LauncherUnavailable()
    return site


def _enqueue(operation_id, site):
    """After-commit notification only; failure leaves original key unresolved."""
    try:
        if not launcher._valid_operation_id(operation_id):
            return
        frappe = launcher._frappe()
        if _site(frappe) != site:
            return
        frappe.enqueue(_JOB, queue=launcher.QUEUE, timeout=launcher.LAUNCHER_JOB_TIMEOUT_SECONDS,
                       operation_id=operation_id)
    except Exception:
        return


def _run(operation_id):
    """One worker attempt only, from a clean frame and exact SUBMITTED row."""
    if not launcher._valid_operation_id(operation_id):
        return None
    try:
        frappe = launcher._frappe()
        site = _site(frappe)
        db = getattr(frappe.local, "db", None)
        if (db is None or type(getattr(db, "transaction_writes", None)) is not int or
                not process._context_is_clean(frappe)):
            return None
        connection = getattr(db, "_conn", None)
        row = frappe_context.get_save_operation(operation_id)
        if not isinstance(row, dict):
            return None
        row = dict(row)
        row.setdefault("doctype", codec.journal.OPERATION_DOCTYPE)
        if (row.get("name") != operation_id or row.get("kind") != VISIT_ANNOTATION_APPEND or
                row.get("state") != codec.journal.SUBMITTED_STATE or row.get("site") != site):
            return None
        intent = codec.load_submitted(row, site=site, actor=row.get("actor"))
        if (intent.binding.site != site or intent.binding.actor != row.get("actor") or
                getattr(frappe.local, "db", None) is not db or
                getattr(db, "_conn", None) is None or
                (connection is not None and db._conn is not connection) or
                _site(frappe) != site or not process._context_is_clean(frappe)):
            return None
        visit_annotation_exec._launch(operation_id)
    except Exception:
        return None
    return None
