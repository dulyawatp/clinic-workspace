import frappe
from frappe.model.document import Document

from clinic_photos.access import visit_observation


class ClinicVisitPhotoConsent(Document):
    def validate(self):
        if not self.get("appointment") or not self.get("patient"):
            frappe.throw("Consent requires an appointment and patient.")
        if not frappe.has_permission("Patient Appointment", "read", doc=self.get("appointment")) or not frappe.has_permission("Patient", "read", doc=self.get("patient")):
            frappe.throw("Permission required for appointment and patient.", frappe.PermissionError)
        appointment = frappe.db.get_value(
            "Patient Appointment", self.get("appointment"),
            ["patient", "status", "position_in_queue"], as_dict=True, for_update=True,
        )
        if not appointment or not self.get("patient") or appointment["patient"] != self.get("patient"):
            frappe.throw("Consent must match the appointment patient.")
        if self.is_new():
            if not isinstance(self.get("evidence"), str) or not self.get("evidence").strip():
                frappe.throw("Consent evidence is required.")
            if any(self.get(field) for field in ("granted_by", "granted_at", "revoked", "revoked_by", "revoked_at", "checked_in_at")):
                frappe.throw("Consent audit fields are server controlled.")
            self.granted_by = frappe.session.user
            self.granted_at = frappe.utils.now_datetime()
            return

        previous = self.get_doc_before_save()
        fields = ("appointment", "patient", "evidence", "granted_by", "granted_at")
        stored_fields = (*fields, "revoked", "revoked_by", "revoked_at", "checked_in_at")
        current = frappe.db.get_value(
            "Clinic Visit Photo Consent", self.name, list(stored_fields),
            as_dict=True, for_update=True,
        )
        if not current or any(current.get(field) != getattr(previous, field, None) for field in stored_fields):
            frappe.throw("Consent changed concurrently; reload before saving.")
        if any(self.get(field) != getattr(previous, field, None) for field in fields):
            frappe.throw("Consent grant evidence is immutable.")
        if getattr(previous, "revoked", 0):
            if not self.get("revoked") or any(self.get(field) != getattr(previous, field, None) for field in ("revoked_by", "revoked_at")):
                frappe.throw("Consent revocation is immutable.")
        elif self.get("revoked"):
            if self.get("revoked_by") or self.get("revoked_at"):
                frappe.throw("Consent revocation audit is server controlled.")
            self.revoked_by = frappe.session.user
            self.revoked_at = frappe.utils.now_datetime()
        elif self.get("revoked_by") or self.get("revoked_at"):
            frappe.throw("Consent revocation audit is server controlled.")
        before = getattr(previous, "checked_in_at", None)
        if self.get("checked_in_at") != before:
            if before or self.get("revoked") or not visit_observation.is_observing(self):
                frappe.throw("Check-in observation is server controlled.")

    def on_trash(self):
        frappe.throw("Consent records cannot be deleted.")
