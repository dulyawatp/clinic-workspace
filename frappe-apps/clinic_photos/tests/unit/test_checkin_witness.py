import types
import unittest

from tests.unit.test_visit_consent import frappe, consent, DB, consent_module

witness = consent_module.checkin_witness


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.doc = consent()
        self.doc.validate = lambda: None
        self.doc.granted_at = "2026-11-20 09:00:00"
        self.doc.save = lambda: setattr(self.doc, "saved", True)
        frappe.db = DB(consent=True)
        frappe.has_permission = lambda *args, **kwargs: True
        frappe.get_doc = lambda *args: self.doc
        frappe.utils.get_datetime = lambda value: value

    def test_matching_consent_records_new_witness(self):
        witness.observe("A", "P", "2026-11-20 10:00:00", "doctor")
        self.assertEqual(self.doc.checkin_witness_at, "2026-11-20 10:00:00")
        self.assertTrue(self.doc.saved)

    def test_witness_save_passes_real_controller_guard(self):
        doc = consent()
        frappe.db = DB(consent=True)
        frappe.has_permission = lambda *args, **kwargs: True
        doc.validate()
        doc.granted_at = "2026-11-20 09:00:00"
        doc.previous = types.SimpleNamespace(**vars(doc))
        doc.name = "C"
        frappe.db.current = vars(doc.previous)
        doc.save = doc.validate
        frappe.get_doc = lambda *args: doc
        witness.observe("A", "P", "2026-11-20 10:00:00", "doctor")
        self.assertEqual(doc.checkin_witness_at, "2026-11-20 10:00:00")

    def test_denied_revoked_late_or_wrong_patient_never_writes(self):
        for mode in ("denied", "revoked", "late", "patient", "actor"):
            self.setUp()
            if mode == "denied":
                frappe.has_permission = lambda *args, **kwargs: False
            elif mode == "revoked":
                self.doc.revoked = 1
            elif mode == "late":
                self.doc.granted_at = "2026-11-20 11:00:00"
            elif mode == "patient":
                self.doc.patient = "Q"
            witness.observe("A", "P", "2026-11-20 10:00:00", "other" if mode == "actor" else "doctor")
            self.assertFalse(getattr(self.doc, "saved", False))
