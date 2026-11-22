import uuid

import frappe
from frappe.model.document import Document

from clinic_photos.publication import journal
from clinic_photos.publication import visit_annotation_insert_guard
from clinic_photos.publication.visit_annotation_intent import VISIT_ANNOTATION_APPEND


SAFE_SERVER_OWNED = "Durable photo-save operation fields are server-owned."
SAFE_NOT_DELETABLE = "Durable photo-save operations cannot be deleted in-app."


class ClinicPhotoSaveOperation(Document):
    """Server-owned durable photo-save operation.

    The DocType declares no role permissions, so ordinary clinical clients cannot
    create, read, list, export, update or delete it. The user-approved trusted
    Administrator read/list/report/export exception remains native; it does not
    authorize ordinary mutation, publication or in-app deletion.

    Creation is re-authorized here against the live session/site, so an
    Administrator's native permission bypass cannot forge a submission outside the
    repository. Every update is refused, and in-app deletion is refused
    unconditionally; exact-stack disposal remains an infrastructure operation. The
    single SUBMITTED -> PREPARED transition is not a document API: the repository seam
    stages the approved reservation evidence with a no-ORM-trigger update after a
    locked re-read, so this controller stays a wholesale refusal for document saves.

    A submitted image row is metadata only: it names the accepted bytes by staged key,
    SHA-256, size and format, and the bytes themselves stay in the bound site's private
    staging area. ``assert_submission_row`` refuses a row carrying ``image_base64``
    (the legacy image-bearing shape) or a row without a valid staged reference, so such
    a document can never be created through this controller either.

    The name is generated here as 32 lowercase hex characters because Frappe's
    ``UUID`` naming rule emits a hyphenated UUID, which the shared operation-id
    contract does not accept.
    """

    def autoname(self):
        self.name = uuid.uuid4().hex

    def before_insert(self):
        try:
            if (visit_annotation_insert_guard._active()
                    or getattr(self, "kind", None) == VISIT_ANNOTATION_APPEND):
                visit_annotation_insert_guard._require_phase(self, "before_insert")
            elif getattr(self, "kind", None) == journal.VISIT_ORIGINAL_CREATE:
                journal.require_visit_submission_insertion(self)
            else:
                journal.require_service_authorization(
                    frappe.session.user or "",
                    getattr(frappe.local, "site", "") or "",
                )
        except journal.JournalAuthorityDenied:
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)

    def validate(self):
        if self.is_new():
            if (visit_annotation_insert_guard._active()
                    or getattr(self, "kind", None) == VISIT_ANNOTATION_APPEND):
                try:
                    visit_annotation_insert_guard._require_phase(self, "validate")
                except journal.JournalAuthorityDenied:
                    frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)
                return
            journal.assert_submission_row(self)
            return

        frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)

    def on_trash(self):
        frappe.throw(SAFE_NOT_DELETABLE, frappe.PermissionError)
