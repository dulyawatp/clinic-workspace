import frappe
from frappe.model.document import Document

from clinic_photos.access.frappe_context import (
    current_publication,
    require_original_delete,
    require_original_registration,
    require_original_update,
    require_visit_photo_insertion,
)


class ClinicPhoto(Document):
    """Protected clinical photo record.

    Registration requires a canonical storage identity and rejects pre-existing
    non-canonical aliases. Original references are immutable after registration;
    only the one-time empty-to-set link/hash/dimension assignment inside the server
    commit context is allowed. Ordinary sessions cannot delete originals.
    """

    def before_insert(self):
        context = current_publication()
        if context is not None and getattr(context, "visit_operation", False):
            require_visit_photo_insertion(self)
        # Pass the stored field untouched: stripping here would validate a different
        # value than the one that ends up persisted.
        storage_key = self.get("storage_key")
        if not storage_key:
            frappe.throw(
                "Clinic Photo requires a storage identity.",
                frappe.ValidationError,
            )
        require_original_registration(storage_key)

    def validate(self):
        if self.is_new():
            return

        require_original_update(self, self.get_doc_before_save())

    def on_trash(self):
        require_original_delete(self)
