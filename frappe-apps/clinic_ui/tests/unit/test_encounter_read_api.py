import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.runtime.frappe_gateway import Gateway


class Denied(Exception):
    pass


class Meta:
    def __init__(self, fields, masked=()):
        self.fields = fields
        self.masked = masked

    def get_field(self, name):
        return SimpleNamespace(permlevel=0, options=self.fields[name]) if name in self.fields else None

    def get_permlevel_access(self, ptype, user=None):
        return [0]

    def get_masked_fields(self):
        return [SimpleNamespace(fieldname=name) for name in self.masked]


class Frappe:
    PermissionError = Denied

    def __init__(self):
        self.session = SimpleNamespace(user="doctor")
        self.local = SimpleNamespace(site="any-site")
        self.allowed = True
        self.patient_allowed = True
        self.rows = [
            {"name": f"ENC-{n:02}", "patient": "PAT-1", "practitioner": "DR-1",
             "encounter_date": "2026-01-01", "docstatus": 0}
            for n in range(25)
        ]
        self.meta = {
            "Patient Encounter": Meta({"patient": None, "practitioner": None, "encounter_date": None,
                                       "symptoms": "Patient Encounter Symptom",
                                       "diagnosis": "Patient Encounter Diagnosis"}),
            "Patient Encounter Symptom": Meta({"complaint": None}),
            "Patient Encounter Diagnosis": Meta({"diagnosis": None}),
            "Patient": Meta({"patient_name": None}),
        }
        self.db = SimpleNamespace(get_value=lambda doctype, name, field: "Patient One")
        self.queries = []

    def get_meta(self, doctype):
        return self.meta[doctype]

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        return self.patient_allowed if doctype == "Patient" else self.allowed

    def get_list(self, doctype, **kwargs):
        self.queries.append((doctype, kwargs))
        if not self.allowed:
            raise Denied()
        rows = [row for row in self.rows if row["patient"] == kwargs["filters"]["patient"]]
        start = kwargs["start"]
        return rows[start:start + kwargs["page_length"]]

    def get_doc(self, doctype, name):
        row = next((r for r in self.rows if r["name"] == name), None)
        if row is None:
            raise Denied()
        return SimpleNamespace(**row, symptoms=[SimpleNamespace(complaint="Pain", note="secret")],
                               diagnosis=[SimpleNamespace(diagnosis="D1", note="secret")],
                               clinical_note="secret")

    def throw(self, message, exc):
        raise exc(message)


class EncounterReadTests(unittest.TestCase):
    def setUp(self):
        self.frappe = Frappe()
        self.gateway = Gateway(self.frappe)
        self.gateway._site_config = {}

    def test_native_patient_without_pilot_configuration_and_bounded_pagination(self):
        result = self.gateway.list_patient_encounters("PAT-1", 0)
        self.assertEqual(len(result["items"]), 20)
        self.assertEqual(result["nextOffset"], 20)
        self.assertEqual(result["items"][0], {
            "id": "ENC-00", "patientId": "PAT-1", "patientName": "Patient One",
            "practitioner": "DR-1", "encounterDate": "2026-01-01", "docstatus": 0,
        })
        self.assertEqual(self.frappe.queries[0][1]["page_length"], 21)
        self.assertEqual(self.frappe.queries[0][1]["order_by"], "encounter_date desc, name desc")
        self.assertEqual(self.gateway.list_patient_encounters("PAT-1", 20)["nextOffset"], None)

    def test_patient_denial_and_reserved_alias_never_fall_back(self):
        self.frappe.patient_allowed = False
        with self.assertRaises(Denied):
            self.gateway.list_patient_encounters("PAT-1", 0)
        self.frappe.patient_allowed = True
        with patch.object(self.gateway, "_resolve_encounter_alias", side_effect=Denied) as resolve:
            with self.assertRaises(Denied):
                self.gateway.list_patient_encounters("wpa1_invalid", 0)
            resolve.assert_not_called()
            valid_alias = "wpa1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16
            with self.assertRaises(Denied):
                self.gateway.list_patient_encounters(valid_alias, 0)
            resolve.assert_called_once_with(valid_alias)
        self.assertEqual(self.frappe.queries, [])

    def test_configured_alias_resolves_before_patient_check_without_fallback(self):
        self.gateway._site_config = {
            "clinic_ui_synthetic_patients": [
                {"workspace_patient_id": "PAT-ALIAS", "native_patient_id": "PAT-1"}
            ]
        }
        with patch.object(self.gateway, "_resolve_encounter_alias", return_value="PAT-1") as resolve:
            result = self.gateway.list_patient_encounters("PAT-ALIAS", 0)
            self.assertEqual(result["items"][0]["patientId"], "PAT-1")
            resolve.assert_called_once_with("PAT-ALIAS")
        with patch.object(self.gateway, "_resolve_encounter_alias", side_effect=Denied):
            with self.assertRaises(Denied):
                self.gateway.list_patient_encounters("PAT-ALIAS", 0)

    def test_unrelated_patient_has_no_rows(self):
        result = self.gateway.list_patient_encounters("PAT-OTHER", 0)
        self.assertEqual(result, {"items": [], "nextOffset": None})

    def test_unreadable_display_fields_deny_instead_of_exposing_values(self):
        self.frappe.meta["Patient Encounter"].masked = ("practitioner",)
        with self.assertRaises(Denied):
            self.gateway.list_patient_encounters("PAT-1", 0)
        with self.assertRaises(Denied):
            self.gateway.view_patient_encounter("ENC-00")
        self.assertEqual(self.frappe.queries, [])

    def test_detail_checks_both_documents_and_projects_only_supported_fields(self):
        result = self.gateway.view_patient_encounter("ENC-00")
        self.assertEqual(result["symptoms"], ["Pain"])
        self.assertEqual(result["diagnoses"], ["D1"])
        self.assertNotIn("clinical_note", result)
        self.assertNotIn("note", str(result))
        self.frappe.patient_allowed = False
        with self.assertRaises(Denied):
            self.gateway.view_patient_encounter("ENC-00")
        self.frappe.patient_allowed = True
        self.frappe.allowed = False
        with self.assertRaises(Denied):
            self.gateway.view_patient_encounter("ENC-00")

    def test_unavailable_child_fields_are_null_and_empty_is_empty(self):
        self.frappe.meta["Patient Encounter Symptom"].masked = ("complaint",)
        self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["symptoms"])
        self.frappe.meta["Patient Encounter Symptom"].masked = ()
        original = self.frappe.get_doc
        def empty(doctype, name):
            doc = original(doctype, name)
            doc.symptoms = []
            return doc
        self.frappe.get_doc = empty
        self.assertEqual(self.gateway.view_patient_encounter("ENC-00")["symptoms"], [])

    def test_invalid_identifiers_and_offsets_are_rejected(self):
        for patient in ("", " ", "\n", None):
            with self.subTest(patient=patient), self.assertRaises(Denied):
                self.gateway.list_patient_encounters(patient, 0)
        for offset in (-1, "x", 1.5, True, "9" * 5000, "١"):
            with self.subTest(offset=offset), self.assertRaises(Denied):
                self.gateway.list_patient_encounters("PAT-1", offset)
        for encounter in ("", "\n", None):
            with self.subTest(encounter=encounter), self.assertRaises(Denied):
                self.gateway.view_patient_encounter(encounter)
