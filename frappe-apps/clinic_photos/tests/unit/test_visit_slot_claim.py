import importlib
import json
import pathlib
import sys
import types
import unittest
from unittest.mock import patch

APP = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(APP))


class Denied(Exception):
    pass


class Duplicate(Exception):
    pass


class FakeDoc(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None


class ClaimTests(unittest.TestCase):
    def setUp(self):
        global visit_claim
        if importlib.util.find_spec("clinic_photos.publication.visit_claim") is None:
            self.fail("visit claim implementation missing")
        visit_claim = importlib.import_module("clinic_photos.publication.visit_claim")
        self.rows = {}
        self.permission = True
        self.create_allowed = True
        self.mutate = False
        self.frappe = types.ModuleType("frappe")
        self.frappe.session = types.SimpleNamespace(user="doctor")
        self.frappe.local = types.SimpleNamespace(site="test.local")
        self.frappe.conf = {"clinic_ui_role_map": {"DOC": ["Physician"], "REC": ["Reception"]}}
        self.frappe.PermissionError = Denied
        self.frappe.DuplicateEntryError = Duplicate
        self.frappe.UniqueValidationError = Duplicate
        self.frappe.throw = lambda msg, exc=Denied: (_ for _ in ()).throw(exc(msg))
        self.frappe.has_permission = lambda *args, **kwargs: self.permission
        self.frappe.db = types.SimpleNamespace(get_value=self.get_value)
        self.frappe.get_all = lambda *args, **kwargs: ["Physician"]
        self.frappe.get_doc = self.get_doc
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def get_value(self, doctype, filters, fieldname, **kwargs):
        if doctype == "User":
            return 1
        if doctype == "Clinic Visit Photo Consent":
            return "CONSENT-1"
        raise AssertionError(doctype)

    def get_doc(self, doctype, name=None):
        if isinstance(doctype, dict):
            doc = FakeDoc(doctype)

            def insert():
                if not self.create_allowed:
                    raise Denied()
                if self.mutate:
                    doc["patient"] = "OTHER"
                visit_claim.require_bound_insert(doc)
                if doc["visit_slot_key"] in self.rows:
                    raise Duplicate()
                self.rows[doc["visit_slot_key"]] = dict(doc)
                return doc

            doc.insert = insert
            return doc
        if doctype == "Patient Appointment":
            return FakeDoc(name=name, patient="PATIENT-1")
        if doctype == "Patient":
            return FakeDoc(name=name)
        if doctype == "Clinic Visit Photo Consent":
            return FakeDoc(name=name, appointment="APPT-1", patient="PATIENT-1",
                           revoked=0, granted_by="doctor", granted_at="now", evidence="signed")
        raise AssertionError(doctype)

    def claim(self, key="request-1", slot="Before"):
        return visit_claim.claim("APPT-1", slot, key, b"image")

    def test_native_insert_is_bound_and_unique(self):
        self.claim()
        self.assertEqual(len(self.rows), 1)
        with self.assertRaises(Duplicate):
            self.claim("request-2")
        self.assertEqual(len(self.rows), 1)

    def test_naked_insert_and_native_permission_denial(self):
        with self.assertRaises(Denied):
            visit_claim.require_bound_insert(FakeDoc())
        self.permission = False
        with self.assertRaises(Denied):
            self.claim()
        self.permission = True
        self.create_allowed = False
        with self.assertRaises(Denied):
            self.claim()
        self.assertFalse(self.rows)

    def test_after_fails_closed(self):
        with self.assertRaises(Denied):
            self.claim(slot="After")
        self.assertFalse(self.rows)

    def test_tampered_row_refuses(self):
        self.mutate = True
        with self.assertRaises(Denied):
            self.claim()
        self.assertFalse(self.rows)

    def test_controller_denies_unbound_and_updates(self):
        module = types.ModuleType("frappe.model.document")
        module.Document = type("Document", (), {})
        model = types.ModuleType("frappe.model")
        with patch.dict(sys.modules, {"frappe.model": model, "frappe.model.document": module}):
            controller = importlib.import_module(
                "clinic_photos.clinic_photos.doctype.clinic_visit_photo_slot_claim.clinic_visit_photo_slot_claim"
            )
            doc = controller.ClinicVisitPhotoSlotClaim()
            doc.is_new = lambda: False
            with self.assertRaises(Denied):
                doc.before_insert()
            with self.assertRaises(Denied):
                doc.validate()
            with self.assertRaises(Denied):
                doc.on_trash()

    def test_metadata_has_create_only_and_unique_slot(self):
        path = APP / "clinic_photos/clinic_photos/doctype/clinic_visit_photo_slot_claim/clinic_visit_photo_slot_claim.json"
        metadata = json.loads(path.read_text())
        self.assertEqual({p["role"] for p in metadata["permissions"]}, {"Physician", "Healthcare Administrator"})
        for permission in metadata["permissions"]:
            self.assertEqual(permission.get("create"), 1)
            self.assertFalse(any(permission.get(k) for k in ("read", "write", "delete", "report", "export")))
        self.assertEqual(next(f for f in metadata["fields"] if f["fieldname"] == "visit_slot_key")["unique"], 1)
