import frappe
from frappe.model.document import Document

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
        require_version_registration(self)

    def validate(self):
        if self.is_new():
            return

        require_version_update(self, self.get_doc_before_save())

    def on_trash(self):
        require_version_delete(self)
