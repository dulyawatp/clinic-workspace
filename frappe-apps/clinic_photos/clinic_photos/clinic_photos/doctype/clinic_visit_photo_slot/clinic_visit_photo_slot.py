import frappe
from frappe.model.document import Document

from clinic_photos.visit.bookkeeping import require_internal_write


class ClinicVisitPhotoSlot(Document):
    def validate(self):
        require_internal_write(self)
        if not self.is_new():
            frappe.throw("Visit photo slot is immutable", frappe.PermissionError)
        from clinic_photos.visit.capture import slot_key
        if self.slot_key != slot_key(self.appointment, self.slot):
            frappe.throw("Invalid visit photo slot", frappe.ValidationError)

    def on_trash(self):
        frappe.throw("Visit photo slot is immutable", frappe.PermissionError)
