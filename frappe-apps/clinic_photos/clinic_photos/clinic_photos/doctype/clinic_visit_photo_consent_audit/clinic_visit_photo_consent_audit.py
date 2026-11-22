import frappe
from frappe.model.document import Document

from clinic_photos.visit.bookkeeping import require_internal_write


class ClinicVisitPhotoConsentAudit(Document):
    def validate(self):
        require_internal_write(self)
        if not self.is_new():
            frappe.throw("Consent audit is immutable", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Consent audit is immutable", frappe.PermissionError)
