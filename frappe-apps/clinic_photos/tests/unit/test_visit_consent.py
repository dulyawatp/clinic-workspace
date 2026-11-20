import importlib
import sys
import types
import unittest
from unittest.mock import patch


class ValidationError(Exception):
    pass


class Document:
    def get(self, key):
        return getattr(self, key, None)

    def is_new(self):
        return self.previous is None

    def get_doc_before_save(self):
        return self.previous


frappe = types.ModuleType("frappe")
frappe.ValidationError = ValidationError
frappe.PermissionError = ValidationError
frappe.throw = lambda message, *args: (_ for _ in ()).throw(ValidationError(message))
frappe.session = types.SimpleNamespace(user="doctor")
frappe.utils = types.SimpleNamespace(now_datetime=lambda: "server-time")
model = types.ModuleType("frappe.model")
document = types.ModuleType("frappe.model.document")
document.Document = Document

with patch.dict(sys.modules, {"frappe": frappe, "frappe.model": model, "frappe.model.document": document}):
    consent_module = importlib.import_module("clinic_photos.clinic_photos.doctype.clinic_visit_photo_consent.clinic_visit_photo_consent")


class DB:
    def __init__(self, appointment=None, consent=None):
        self.appointment = appointment or {"patient": "P", "status": "Open", "position_in_queue": 0}
        self.consent = consent
        self.current = None

    def get_value(self, doctype, filters, fields, **kwargs):
        if doctype == "Patient Appointment":
            return self.appointment if filters == "A" else None
        if isinstance(filters, str) and doctype == "Clinic Visit Photo Consent":
            return self.current
        if self.consent and filters == {"appointment": "A"}:
            return {"name": "C"}
        return None


def consent(**values):
    doc = consent_module.ClinicVisitPhotoConsent()
    doc.previous = None
    for key, value in {"appointment": "A", "patient": "P", "evidence": "signed", "revoked": 0, **values}.items():
        setattr(doc, key, value)
    return doc


class ConsentTests(unittest.TestCase):
    def setUp(self):
        frappe.db = DB()
        frappe.has_permission = lambda *args, **kwargs: True
        self.doc = consent()

    def test_grant_derives_audit_and_rejects_wrong_patient_or_client_observation(self):
        self.doc.validate()
        self.assertEqual((self.doc.granted_by, self.doc.granted_at), ("doctor", "server-time"))
        for values in ({"patient": "other"}, {"evidence": " "}, {"checked_in_at": "forged"}):
            with self.assertRaises(ValidationError):
                consent(**values).validate()

    def test_revoke_is_one_way_and_grant_fields_immutable(self):
        self.doc.validate()
        prior = types.SimpleNamespace(**vars(self.doc))
        self.doc.previous = prior
        self.doc.name = "C"
        frappe.db.current = vars(prior)
        self.doc.revoked = 1
        self.doc.validate()
        self.assertEqual((self.doc.revoked_by, self.doc.revoked_at), ("doctor", "server-time"))
        self.doc.previous = types.SimpleNamespace(**vars(self.doc))
        frappe.db.current = vars(self.doc.previous)
        self.doc.revoked = 0
        with self.assertRaises(ValidationError):
            self.doc.validate()
        self.doc.revoked = 1
        self.doc.evidence = "rewritten"
        with self.assertRaises(ValidationError):
            self.doc.validate()

    def test_grant_never_infers_observation_from_current_appointment(self):
        for status, queue in (("Checked In", 1), ("Checked In", 0), ("Closed", 2)):
            frappe.db.appointment.update(status=status, position_in_queue=queue)
            doc = consent()
            doc.validate()
            self.assertIsNone(doc.get("checked_in_at"))

    def test_grant_denied_without_native_appointment_or_patient_read(self):
        for denied in ("Patient Appointment", "Patient"):
            frappe.has_permission = lambda doctype, *args, **kwargs: doctype != denied
            with self.assertRaises(ValidationError):
                consent().validate()

    def test_client_cannot_set_observation_on_existing_consent(self):
        self.doc.validate()
        self.doc.previous = types.SimpleNamespace(**vars(self.doc))
        self.doc.name = "C"
        frappe.db.current = vars(self.doc.previous)
        self.doc.checked_in_at = "forged"
        with self.assertRaises(ValidationError):
            self.doc.validate()

    def test_stale_consent_cannot_erase_concurrent_observation(self):
        self.doc.validate()
        self.doc.name = "C"
        self.doc.previous = types.SimpleNamespace(**vars(self.doc))
        frappe.db.current = {**vars(self.doc.previous), "checked_in_at": "other-transaction"}
        with self.assertRaises(ValidationError):
            self.doc.validate()
