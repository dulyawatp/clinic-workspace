import unittest

from clinic_ui.runtime.frappe_gateway import Gateway


class NativePermissionError(Exception):
    pass


class NativeValidationError(Exception):
    pass


class Draft:
    def __init__(self, failure=None):
        self.doctype = "Patient Encounter"
        self.name = "ENC-1"
        self.docstatus = 0
        self.failure = failure
        self.insert_calls = 0

    def insert(self):
        self.insert_calls += 1
        if self.failure:
            raise self.failure
        return self


class Frappe:
    ValidationError = NativeValidationError

    def __init__(self, failure=None):
        self.doc = Draft(failure)
        self.created = []

    def new_doc(self, doctype):
        self.created.append(doctype)
        return self.doc

    def throw(self, message, exc):
        raise exc(message)


class EncounterEndpointTests(unittest.TestCase):
    def setUp(self):
        self.frappe = Frappe()
        self.gateway = Gateway(frappe_module=self.frappe)
        self.body = {"patient": "PAT-1", "practitioner": "PRA-1", "appointment_type": "TYPE-1"}

    def test_native_create_inserts_once_without_pilot_gate(self):
        result = self.gateway.create_unlinked_encounter_draft(self.body)
        self.assertEqual(result, {"id": "ENC-1", "docstatus": 0})
        self.assertEqual(self.frappe.created, ["Patient Encounter"])
        self.assertEqual(self.frappe.doc.insert_calls, 1)
        self.assertEqual(self.frappe.doc.patient, "PAT-1")
        self.assertEqual(self.frappe.doc.practitioner, "PRA-1")
        self.assertEqual(self.frappe.doc.appointment_type, "TYPE-1")
        self.assertEqual(self.frappe.doc.appointment, "")
        self.assertEqual(self.frappe.doc.submit_orders_on_save, 0)
        for field in (
            "symptoms", "diagnosis", "codification_table", "drug_prescription",
            "lab_test_prescription", "procedure_prescription", "therapies",
        ):
            self.assertEqual(getattr(self.frappe.doc, field), [])

    def test_native_permission_refusal_propagates(self):
        failure = NativePermissionError("native permission denied")
        self.frappe.doc.failure = failure
        with self.assertRaises(NativePermissionError) as raised:
            self.gateway.create_unlinked_encounter_draft(self.body)
        self.assertIs(raised.exception, failure)
        self.assertEqual(self.frappe.doc.insert_calls, 1)

    def test_invalid_body_never_constructs_or_inserts(self):
        for body in (
            None, {}, {**self.body, "appointment": "APT"}, {**self.body, "docstatus": 1},
            {**self.body, "drug_prescription": []}, {**self.body, "patient": ""},
            {**self.body, "patient": "  "}, {**self.body, "patient": 5},
        ):
            with self.subTest(body=body), self.assertRaises(NativeValidationError):
                self.gateway.create_unlinked_encounter_draft(body)
        self.assertEqual(self.frappe.created, [])
