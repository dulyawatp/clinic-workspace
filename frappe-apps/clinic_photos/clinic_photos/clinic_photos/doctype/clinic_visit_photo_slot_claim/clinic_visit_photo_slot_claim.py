import frappe
from frappe.model.document import Document

from clinic_photos.publication.visit_claim import require_bound_insert


class ClinicVisitPhotoSlotClaim(Document):
    def before_insert(self):
        require_bound_insert(self)

    def validate(self):
        if self.is_new():
            require_bound_insert(self)
            return
        frappe.throw("Visit photo claims are immutable.", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Visit photo claims are immutable.", frappe.PermissionError)
