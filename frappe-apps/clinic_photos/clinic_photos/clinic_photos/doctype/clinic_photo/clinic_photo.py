import frappe
from frappe.model.document import Document

from clinic_photos.access.frappe_context import (
    require_original_delete,
    require_original_registration,
    require_original_update,
)


class ClinicPhoto(Document):
    """Protected clinical photo record.

    Registration requires a canonical storage identity and rejects pre-existing
    non-canonical aliases. Original references are immutable after registration;
    only the one-time empty-to-set link/hash/dimension assignment inside the server
    commit context is allowed. Ordinary sessions cannot delete originals.
    """

    def before_insert(self):
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
