import unittest
import importlib
import sys
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.endpoints import lab_reports


class Doc(SimpleNamespace):
    def check_permission(self, action):
        if action in getattr(self, "denied", ()):
            raise PermissionError(action)


class Gateway:
    def __init__(self):
        self.appointment = Doc(name="A1", patient="P1")
        self.patient = Doc(name="P1")
        self.encounter = Doc(name="E1", appointment="A1", patient="P1")
        self.files = []
        self.frappe = self

    def linked_resolve(self, ref):
        if ref != "A1":
            raise ValueError("invalid reference")
        return ref

    def _linked_fields(self, *args):
        pass

    def get_doc(self, doctype, name):
        if doctype == "Patient Appointment" and name == "A1":
            return self.appointment
        if doctype == "Patient" and name == "P1":
            return self.patient
        if doctype == "Patient Encounter" and name == "E1":
            return self.encounter
        if doctype == "File":
            return next(f for f in self.files if f.name == name)
        raise ValueError("missing")

    def get_list(self, doctype, **kwargs):
        start = kwargs["start"]
        return [{"name": f.name} for f in self.files[start:start + kwargs["page_length"]]]


class LabReportsTests(unittest.TestCase):
    def setUp(self):
        self.g = Gateway()

    def file(self, name="F1"):
        return Doc(name=name, file_name="report.pdf", file_size=5, owner="doctor",
                   creation="today", is_private=1, is_folder=0, folder="Home/Lab Reports",
                   attached_to_doctype="Patient Appointment", attached_to_name="A1",
                   unique_url=f"/private/files/report.pdf?fid={name}")

    def test_both_lists_return_same_file_identity(self):
        self.g.files.append(self.file())
        a = lab_reports.list_for_appointment(self.g, "A1", 0)
        e = lab_reports.list_for_encounter(self.g, "E1", 0)
        self.assertEqual(a, e)
        self.assertEqual(a["items"][0]["id"], "F1")
        self.assertEqual(a["items"][0]["downloadUrl"], "/private/files/report.pdf?fid=F1")

    def test_cross_patient_and_denied_file_are_rejected(self):
        self.g.encounter.patient = "P2"
        with self.assertRaises(ValueError):
            lab_reports.list_for_encounter(self.g, "E1")
        self.g.files.append(self.file())
        self.g.files[0].denied = ("read",)
        with self.assertRaises(PermissionError):
            lab_reports.list_for_appointment(self.g, "A1")

    def test_lookahead_is_authorized_and_bad_url_refused(self):
        self.g.files = [self.file(f"F{i}") for i in range(21)]
        self.g.files[-1].denied = ("read",)
        with self.assertRaises(PermissionError):
            lab_reports.list_for_appointment(self.g, "A1")
        self.g.files = [self.file()]
        self.g.files[0].unique_url = "//evil.test/private/files/report.pdf?fid=F1"
        with self.assertRaises(ValueError):
            lab_reports.list_for_appointment(self.g, "A1")

    def test_upload_validation_refuses_untrusted_fields_and_payload(self):
        with self.assertRaises(ValueError):
            lab_reports.validate_upload("x.pdf", b"not a pdf")
        with self.assertRaises(ValueError):
            lab_reports.validate_upload("../x.pdf", b"%PDF-1.7")
        self.assertEqual(lab_reports.validate_upload("x.pdf", b"%PDF-1.7"), None)

    def test_missing_unauthorized_fixture_and_offset_are_refused(self):
        for reference in ("bad", "", None):
            with self.assertRaises(ValueError):
                lab_reports.list_for_appointment(self.g, reference)
        self.g.patient.denied = ("read",)
        with self.assertRaises(PermissionError):
            lab_reports.list_for_appointment(self.g, "A1")
        self.g.patient.denied = ()
        for offset in (-1, True, "1e2", 1000001):
            with self.assertRaises(ValueError):
                lab_reports.list_for_appointment(self.g, "A1", offset)

    def test_upload_inserts_one_private_appointment_file(self):
        gateway = self.g
        original = gateway.get_doc

        def get_doc(doctype, name=None):
            if isinstance(doctype, dict):
                self.assertEqual(doctype["attached_to_doctype"], "Patient Appointment")
                self.assertEqual(doctype["attached_to_name"], "A1")
                self.assertEqual(doctype["folder"], "Home/Lab Reports")
                self.assertEqual(doctype["is_private"], 1)
                doc = self.file()
                doc.insert = lambda: gateway.files.append(doc)
                return doc
            if doctype == "File" and name == "Home/Lab Reports":
                return Doc(is_folder=1)
            return original(doctype, name)

        gateway.get_doc = get_doc
        result = lab_reports.upload(gateway, "A1", "report.pdf", b"%PDF-1.7")
        self.assertEqual(result["id"], "F1")
        self.assertEqual(len(gateway.files), 1)
        self.assertEqual(lab_reports.list_for_encounter(gateway, "E1")["items"][0], result)
        gateway.appointment.denied = ("write",)
        with self.assertRaises(PermissionError):
            lab_reports.upload(gateway, "A1", "report.pdf", b"%PDF-1.7")
        self.assertEqual(len(gateway.files), 1)


class UploadApiTests(unittest.TestCase):
    def test_rejects_override_before_file_processing_and_sets_no_store(self):
        class Fields(dict):
            def getlist(self, key):
                return [self[key]] if key in self else []

        class Invalid(Exception):
            pass

        def throw(message, error):
            raise error(message)

        frappe = SimpleNamespace(
            whitelist=lambda **kw: lambda fn: fn,
            session=SimpleNamespace(user="doctor"),
            request=SimpleNamespace(mimetype="multipart/form-data", args={},
                                    form=Fields(appointment_ref="A1", folder="Home"),
                                    files=Fields(file=object())),
            ValidationError=Invalid, throw=throw,
        )
        gateway = SimpleNamespace(mark_no_store=lambda: marked.append(True))
        marked = []
        with patch.dict(sys.modules, {"frappe": frappe}):
            api = importlib.import_module("clinic_ui.api.lab_reports")
            with patch.object(api, "Gateway", return_value=gateway):
                with self.assertRaises(Invalid):
                    api.upload("A1")
        self.assertEqual(marked, [True])
