import frappe
from frappe.model.document import Document

from clinic_photos.access.draft_identity import is_reserved_visit_identity
from clinic_photos.access.frappe_context import (
    require_version_delete,
    require_version_registration,
    require_version_update,
)


class ClinicPhotoAnnotationVersion(Document):
    """Server-created immutable annotation version for a draft.

    Registration is pending-only and context-bound; ``annotation_file`` may be
    empty so that a pending version can register its storage key before the bytes
    exist. Commit requires the matching persisted File/key. Once committed neither
    identity nor state can change, and the record cannot be deleted.
    """

    def before_insert(self):
        self._deny_reserved_identity()
        require_version_registration(self)

    def validate(self):
        self._deny_reserved_identity()
        if self.is_new():
            return

        require_version_update(self, self.get_doc_before_save())

    def on_trash(self):
        self._deny_reserved_identity()
        require_version_delete(self)

    def before_rename(self, old, new, merge=False):
        self._deny_reserved_identity()
        if is_reserved_visit_identity(old) or is_reserved_visit_identity(new):
            frappe.throw("Version identity is server-owned.", frappe.PermissionError)

    def _deny_reserved_identity(self):
        if is_reserved_visit_identity(getattr(self, "name", None)) or is_reserved_visit_identity(
            getattr(self, "draft", None)
        ):
            frappe.throw("Version identity is server-owned.", frappe.PermissionError)
