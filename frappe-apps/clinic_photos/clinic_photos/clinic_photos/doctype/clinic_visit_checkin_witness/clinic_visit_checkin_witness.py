import frappe
from frappe.model.document import Document

from clinic_photos.visit.bookkeeping import require_internal_write


class ClinicVisitCheckinWitness(Document):
    def validate(self):
        require_internal_write(self)
        if not self.is_new():
            frappe.throw("Check-in witness is immutable", frappe.PermissionError)

    def on_trash(self):
        frappe.throw("Check-in witness is immutable", frappe.PermissionError)
