import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clinic_ui.runtime.frappe_gateway import Gateway


class Denied(Exception):
    pass


class Missing(Exception):
    pass


class Meta:
    def __init__(self, fields, masked=()):
        self.fields = fields
        self.masked = masked
        self.levels = {}
        self.accessible = [0]

    def get_field(self, name):
        return SimpleNamespace(permlevel=self.levels.get(name, 0), options=self.fields[name]) if name in self.fields else None

    def get_permlevel_access(self, ptype, user=None):
        return self.accessible

    def get_masked_fields(self):
        return None if self.masked is None else [SimpleNamespace(fieldname=name) for name in self.masked]


class Frappe:
    PermissionError = Denied
    DoesNotExistError = Missing

    def __init__(self):
        self.session = SimpleNamespace(user="doctor")
        self.local = SimpleNamespace(site="any-site")
        self.allowed = True
        self.patient_allowed = True
        self.denied_docs = set()
        self.query_denied_docs = set()
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
        self.db = SimpleNamespace(get_value=lambda doctype, name, field: "Patient One",
                                  set_value=Mock(), sql=Mock(), commit=Mock(), rollback=Mock())
        self.insert = Mock()
        self.save = Mock()
        self.delete_doc = Mock()
        self.document_writes = []
        self.queries = []

    def get_meta(self, doctype):
        return self.meta[doctype]

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        if doctype == "Patient":
            return self.patient_allowed
        name = getattr(doc, "name", doc)
        return self.allowed and name not in self.denied_docs

    def get_list(self, doctype, **kwargs):
        self.queries.append((doctype, kwargs))
        if not self.allowed:
            raise Denied()
        rows = [row for row in self.rows if row["patient"] == kwargs["filters"]["patient"]
                and row["name"] not in self.query_denied_docs]
        rows.sort(key=lambda row: (row["encounter_date"], row["name"]), reverse=True)
        start = kwargs["start"]
        return [{field: row[field] for field in kwargs["fields"]}
                for row in rows[start:start + kwargs["page_length"]]]

    def get_doc(self, doctype, name):
        row = next((r for r in self.rows if r["name"] == name), None)
        if row is None:
            raise Missing()
        save, insert, delete = Mock(), Mock(), Mock()
        self.document_writes.extend((save, insert, delete))
        return SimpleNamespace(**{**row, "clinical_note": "secret"}, symptoms=[SimpleNamespace(complaint="Pain", note="secret")],
                               diagnosis=[SimpleNamespace(diagnosis="D1", note="secret")],
                               save=save, insert=insert, delete=delete,
                               )

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
            "id": "ENC-24", "patientId": "PAT-1", "patientName": "Patient One",
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

    def test_offset_boundary_never_returns_an_unusable_continuation(self):
        with patch.object(self.frappe, "get_list", return_value=self.frappe.rows[:21]):
            self.assertEqual(self.gateway.list_patient_encounters("PAT-1", 1000000)["nextOffset"], None)
            self.assertEqual(self.gateway.list_patient_encounters("PAT-1", 999980)["nextOffset"], 1000000)
            self.assertEqual(self.gateway.list_patient_encounters("PAT-1", 999999)["nextOffset"], None)

    def test_document_permissions_order_projection_and_no_writes(self):
        self.frappe.rows[0]["clinical_note"] = "secret"
        self.frappe.rows[1]["encounter_date"] = "2027-01-01"
        self.frappe.denied_docs.add("ENC-24")
        result = self.gateway.list_patient_encounters("PAT-1", 0)
        ids = [item["id"] for item in result["items"]]
        self.assertEqual(ids[:3], ["ENC-01", "ENC-23", "ENC-22"])
        self.assertNotIn("ENC-24", ids)
        self.assertNotIn("secret", str(result))
        self.assertEqual(self.frappe.queries[0][1]["fields"],
                         ["name", "patient", "practitioner", "encounter_date", "docstatus"])
        with self.assertRaises(Denied):
            self.gateway.view_patient_encounter("ENC-24")
        self.gateway.view_patient_encounter("ENC-00")
        for call in (self.frappe.insert, self.frappe.save, self.frappe.delete_doc,
                     self.frappe.db.set_value, self.frappe.db.sql,
                     self.frappe.db.commit, self.frappe.db.rollback,
                     *self.frappe.document_writes):
            call.assert_not_called()

    def test_document_denial_does_not_leak_and_sparse_page_continues(self):
        self.frappe.denied_docs = {f"ENC-{n:02}" for n in range(5, 25)}
        first = self.gateway.list_patient_encounters("PAT-1", 0)
        self.assertEqual(first, {"items": [], "nextOffset": 20})
        second = self.gateway.list_patient_encounters("PAT-1", first["nextOffset"])
        self.assertEqual([item["id"] for item in second["items"]],
                         ["ENC-04", "ENC-03", "ENC-02", "ENC-01", "ENC-00"])
        self.assertIsNone(second["nextOffset"])

    def test_candidate_missing_or_denied_is_skipped_unexpected_error_propagates(self):
        original = self.frappe.get_doc
        def load(doctype, name):
            if name == "ENC-24":
                raise Missing()
            if name == "ENC-23":
                raise Denied()
            return original(doctype, name)
        with patch.object(self.frappe, "get_doc", side_effect=load):
            result = self.gateway.list_patient_encounters("PAT-1", 0)
        self.assertEqual(result["items"][0]["id"], "ENC-22")
        self.assertEqual(result["nextOffset"], 20)
        with patch.object(self.frappe, "get_doc", side_effect=RuntimeError("unexpected")):
            with self.assertRaisesRegex(RuntimeError, "unexpected"):
                self.gateway.list_patient_encounters("PAT-1", 0)

    def test_query_filter_and_document_hook_are_independent(self):
        self.frappe.query_denied_docs.add("ENC-24")
        original = self.frappe.has_permission
        def check(doctype, ptype="read", doc=None, user=None):
            if getattr(doc, "name", None) == "ENC-23":
                raise Denied()
            return original(doctype, ptype=ptype, doc=doc, user=user)
        with patch.object(self.frappe, "has_permission", side_effect=check):
            result = self.gateway.list_patient_encounters("PAT-1", 0)
        self.assertEqual(result["items"][0]["id"], "ENC-22")
        self.assertNotIn("ENC-23", str(result))
        self.assertNotIn("ENC-24", str(result))
        def broken(doctype, ptype="read", doc=None, user=None):
            if getattr(doc, "name", None) == "ENC-23":
                raise RuntimeError("hook failed")
            return original(doctype, ptype=ptype, doc=doc, user=user)
        with patch.object(self.frappe, "has_permission", side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, "hook failed"):
                self.gateway.list_patient_encounters("PAT-1", 0)

    def test_actual_document_patient_and_fields_override_candidate(self):
        original = self.frappe.get_doc
        def load(doctype, name):
            doc = original(doctype, name)
            if name == "ENC-24":
                doc.patient = "PAT-OTHER"
            if name == "ENC-23":
                doc.practitioner = "ACTUAL-DOCTOR"
            return doc
        with patch.object(self.frappe, "get_doc", side_effect=load):
            result = self.gateway.list_patient_encounters("PAT-1", 0)
        self.assertEqual(result["items"][0]["id"], "ENC-23")
        self.assertEqual(result["items"][0]["practitioner"], "ACTUAL-DOCTOR")
        self.assertNotIn("ENC-24", str(result))

    def test_order_is_stable_across_pages_with_equal_dates(self):
        first = self.gateway.list_patient_encounters("PAT-1", 0)
        second = self.gateway.list_patient_encounters("PAT-1", first["nextOffset"])
        ids = [item["id"] for item in first["items"] + second["items"]]
        self.assertEqual(ids, [f"ENC-{n:02}" for n in reversed(range(25))])
        self.assertIsNone(second["nextOffset"])

    def test_missing_and_denied_detail_are_distinct(self):
        with self.assertRaises(Missing):
            self.gateway.view_patient_encounter("missing")
        self.frappe.denied_docs.add("ENC-00")
        with self.assertRaises(Denied):
            self.gateway.view_patient_encounter("ENC-00")

    def test_child_sections_fail_closed_on_restricted_masked_or_unknown_metadata(self):
        parent = self.frappe.meta["Patient Encounter"]
        child = self.frappe.meta["Patient Encounter Symptom"]
        for target, attr, value in (
            (parent, "masked", ("symptoms",)),
            (parent, "levels", {"symptoms": 1}),
            (child, "masked", ("complaint",)),
            (child, "levels", {"complaint": 1}),
            (parent, "masked", None),
            (child, "masked", None),
            (child, "fields", {}),
        ):
            with self.subTest(target=target, attr=attr, value=value):
                original = getattr(target, attr)
                setattr(target, attr, value)
                self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["symptoms"])
                setattr(target, attr, original)
        with patch.object(parent, "get_masked_fields", return_value=[SimpleNamespace()]):
            self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["symptoms"])
        diagnosis = self.frappe.meta["Patient Encounter Diagnosis"]
        diagnosis.levels = {"diagnosis": 1}
        self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["diagnoses"])
        diagnosis.levels = {}
        diagnosis.masked = ("diagnosis",)
        self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["diagnoses"])
        diagnosis.masked = ()
        with patch.object(diagnosis, "get_masked_fields", side_effect=RuntimeError):
            self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["diagnoses"])
        parent.fields["diagnosis"] = "Wrong Child"
        self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["diagnoses"])

    def test_malformed_child_mask_fieldnames_fail_closed(self):
        child = self.frappe.meta["Patient Encounter Symptom"]
        for entry in (SimpleNamespace(), SimpleNamespace(fieldname=None),
                      SimpleNamespace(fieldname=""), SimpleNamespace(fieldname=7)):
            with self.subTest(entry=entry), patch.object(
                child, "get_masked_fields", return_value=[entry]
            ):
                self.assertIsNone(self.gateway.view_patient_encounter("ENC-00")["symptoms"])
