import frappe
from frappe.model.document import Document

from clinic_photos.visit.bookkeeping import require_internal_write


class ClinicVisitPhotoState(Document):
    def validate(self):
        require_internal_write(self)
        if not self.is_new():
            before = self.get_doc_before_save()
            if not before or (before.appointment, before.patient,
                              before.appointment_snapshot, before.patient_snapshot,
                              before.appointment_creation, before.patient_creation) != (
                self.appointment, self.patient, self.appointment_snapshot, self.patient_snapshot,
                self.appointment_creation, self.patient_creation
            ):
                frappe.throw("Visit identity is immutable", frappe.ValidationError)

    def on_trash(self):
        frappe.throw("Visit state cannot be deleted", frappe.PermissionError)
