import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clinic_ui.endpoints import linked_encounter
from clinic_ui.runtime.frappe_gateway import (
    Gateway as NativeGateway, LinkedFieldAccessError, LinkedNoteMismatch,
)


class Gateway:
    def __init__(self):
        self.encounter = None
        self.note = None
        self.calls = []
        self.fixture = set()

    def linked_resolve(self, ref):
        if ref in self.fixture:
            raise ValueError("fixture")
        return ref

    def linked_context(self, ref):
        return {"appointment": ref, "patient": "P1", "practitioner": "DR1", "status": "Closed" if self.encounter else "Open"}

    def linked_find(self, context):
        return self.encounter

    def linked_project(self, context, encounter, note_offset=0):
        return {"context": context, "encounter": encounter,
                "notes": {"state": "available", "items": [] if self.note is None else [self.note], "next_offset": None}}

    def linked_save(self, context, encounter, clinical):
        self.calls.append(("save", clinical))
        self.encounter = {"id": "E1", "modified": "v1", "docstatus": 0}
        if clinical.get("note"):
            self.note = {"id": "N1", "modified": "n1", "note": clinical["note"]["text"]}
        return self.encounter

    def linked_complete(self, context, encounter):
        self.calls.append(("submit", encounter["id"]))
        encounter = dict(encounter, modified="v2", docstatus=1)
        self.encounter = encounter
        return encounter


class LinkedEncounterTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()

    def test_open_does_not_write_and_first_save_reopens(self):
        result = linked_encounter.open_for_appointment(self.gateway, "A1")
        self.assertIsNone(result["encounter"])
        self.assertEqual(self.gateway.calls, [])
        saved = linked_encounter.save_linked(self.gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": {"note": {"text": "hello", "id": None, "modified": None}}})
        self.assertEqual(saved["encounter"]["id"], "E1")
        self.assertEqual(linked_encounter.open_for_appointment(self.gateway, "A1")["notes"]["items"][0]["id"], "N1")

    def test_fixture_alias_collision_rejected(self):
        self.gateway.fixture.add("A1")
        with self.assertRaises(ValueError):
            linked_encounter.open_for_appointment(self.gateway, "A1")
        self.assertEqual(self.gateway.calls, [])

    def test_rejects_extra_fields_and_stale_submit(self):
        with self.assertRaises(ValueError):
            linked_encounter.save_linked(self.gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": {"patient": "other"}})
        self.gateway.encounter = {"id": "E1", "modified": "v1", "docstatus": 0}
        with self.assertRaises(ValueError):
            linked_encounter.complete_linked(self.gateway, {"appointment_ref": "A1", "encounter_id": "E1", "expected_version": "old"})
        self.assertEqual(self.gateway.calls, [])

    def test_submit_existing_draft_once(self):
        self.gateway.encounter = {"id": "E1", "modified": "v1", "docstatus": 0}
        payload = {"appointment_ref": "A1", "encounter_id": "E1", "expected_version": "v1"}
        result = linked_encounter.complete_linked(self.gateway, payload)
        self.assertEqual(result["encounter"]["docstatus"], 1)
        with self.assertRaises(ValueError):
            linked_encounter.complete_linked(self.gateway, payload)
        self.assertEqual(len(self.gateway.calls), 1)

    def test_native_resolution_never_reinterprets_fixture_collision(self):
        gateway = NativeGateway(SimpleNamespace())
        gateway.setting = lambda key: [{"workspace_appointment_id": "A1", "native_appointment_id": "A2"}]
        with self.assertRaises(ValueError):
            gateway.linked_resolve("A1")
        self.assertEqual(gateway.linked_resolve("A3"), "A3")

    def test_sealed_alias_is_cross_actor_identity_only_and_ambiguous_refuses(self):
        from clinic_ui.core import appointment_write, config
        alias = appointment_write.build_alias(b"x" * 32, "site", "reception", "appt-op-key-0002-0123456789")
        gateway = NativeGateway(SimpleNamespace(local=SimpleNamespace(site="site")))
        gateway.setting = lambda key: "x" * 32 if key == config.EPOCH_SECRET_KEY else []
        with patch("clinic_ui.endpoints.appointments.discover_booked") as discover:
            discover.return_value = [SimpleNamespace(workspace_appointment_id=alias, native_appointment_id="A1", creator="reception")]
            self.assertEqual(gateway.linked_resolve(alias), "A1")
            discover.return_value *= 2
            with self.assertRaises(ValueError):
                gateway.linked_resolve(alias)
        with self.assertRaises(ValueError):
            gateway.linked_resolve("wsa1_broken")

    def test_permission_failure_propagates_without_writes(self):
        gateway = Gateway()
        gateway.linked_context = Mock(side_effect=PermissionError("denied"))
        with self.assertRaises(PermissionError):
            linked_encounter.save_linked(gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": {}})
        self.assertEqual(gateway.calls, [])

    def test_note_identity_and_version_are_mandatory_together(self):
        for note in ({"id": "N1", "modified": None, "text": "x"},
                     {"id": None, "modified": "v1", "text": "x"},
                     {"id": None, "modified": None, "text": ""}):
            with self.subTest(note=note), self.assertRaises(ValueError):
                linked_encounter.save_linked(self.gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": {"note": note}})
        self.assertEqual(self.gateway.calls, [])

    def test_malformed_nested_values_rejected(self):
        for clinical in (None, [], {"symptoms": "pain"}, {"diagnoses": [None]},
                         {"note": {"id": "N1", "modified": "  ", "text": "x"}},
                         {"note": {"id": None, "modified": None, "text": "x", "extra": 1}}):
            with self.subTest(clinical=clinical), self.assertRaises(ValueError):
                linked_encounter.save_linked(self.gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": clinical})

    def test_missing_ambiguous_and_failure_never_report_success(self):
        for error in (ValueError("missing"), ValueError("ambiguous"), RuntimeError("native failure")):
            gateway = Gateway()
            gateway.linked_find = Mock(side_effect=error)
            with self.subTest(error=error), self.assertRaises(type(error)):
                linked_encounter.save_linked(gateway, {"appointment_ref": "A1", "expected_version": None, "clinical": {}})
            self.assertEqual(gateway.calls, [])

    def test_submit_failure_propagates(self):
        gateway = Gateway()
        gateway.encounter = {"id": "E1", "modified": "v1", "docstatus": 0}
        gateway.linked_complete = Mock(side_effect=RuntimeError("submit failed"))
        with self.assertRaisesRegex(RuntimeError, "submit failed"):
            linked_encounter.complete_linked(gateway, {"appointment_ref": "A1", "encounter_id": "E1", "expected_version": "v1"})


class NativeAdapterTests(unittest.TestCase):
    def test_existing_draft_preserves_orders_and_uses_native_save(self):
        doc = SimpleNamespace(name="E1", modified="v1", docstatus=0,
                              drug_prescription=[{"drug_code": "RX"}],
                              lab_test_prescription=[{"lab_test_code": "LAB"}],
                              procedure_prescription=[], therapies=[],
                              submit_orders_on_save=1)
        doc.check_permission = Mock()
        doc.save = Mock()
        gateway = NativeGateway(SimpleNamespace())
        gateway._linked_doc = Mock(return_value=doc)
        gateway._linked_fields = Mock()
        result = gateway.linked_save({"appointment": "A1"}, {"id": "E1", "modified": "v1"}, {})
        self.assertEqual(doc.drug_prescription, [{"drug_code": "RX"}])
        self.assertEqual(doc.lab_test_prescription, [{"lab_test_code": "LAB"}])
        self.assertEqual(doc.submit_orders_on_save, 0)
        doc.check_permission.assert_called_once_with("write")
        doc.save.assert_called_once_with()
        self.assertEqual(result["id"], "E1")

    def test_submit_disables_draft_order_dispatch_and_propagates_failure(self):
        doc = SimpleNamespace(modified="v1", docstatus=0, submit_orders_on_save=1)
        doc.submit = Mock(side_effect=RuntimeError("native failure"))
        gateway = NativeGateway(SimpleNamespace())
        gateway._linked_doc = Mock(return_value=doc)
        gateway._linked_fields = Mock()
        with self.assertRaisesRegex(RuntimeError, "native failure"):
            gateway.linked_complete({"appointment": "A1"}, {"id": "E1", "modified": "v1"})
        self.assertEqual(doc.submit_orders_on_save, 0)
        doc.submit.assert_called_once_with()

    def test_ambiguous_linked_encounters_refuse(self):
        frappe = SimpleNamespace(get_all=Mock(return_value=[{"name": "E1"}, {"name": "E2"}]))
        gateway = NativeGateway(frappe)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            gateway.linked_find({"appointment": "A1"})

    def test_unsaved_open_checks_native_mapper_authority(self):
        frappe = SimpleNamespace(get_all=Mock(return_value=[]))
        gateway = NativeGateway(frappe)
        gateway._linked_map = Mock(side_effect=PermissionError("no create"))
        with self.assertRaisesRegex(PermissionError, "no create"):
            gateway.linked_find({"appointment": "A1", "patient": "P1", "practitioner": "DR1",
                                 "status": "Open", "_alternative": False})


class ModelDoc:
    def __init__(self, model, doctype, **fields):
        self.model = model
        self.doctype = doctype
        self.__dict__.update(fields)

    def check_permission(self, operation):
        if ((self.doctype, operation) in self.model.denied
                or (self.doctype, operation, getattr(self, "name", None)) in self.model.denied):
            raise PermissionError("native denial")

    def set(self, field, value):
        setattr(self, field, value)

    def append(self, field, value):
        if field == "drug_prescription":
            value = dict(value, name=f"DRUG-{len(getattr(self, field)) + 1}")
        getattr(self, field).append(value)

    def insert(self):
        self.check_permission("create")
        self.name = "E1" if self.doctype == "Patient Encounter" else f"N{1 + sum(1 for kind, _ in self.model.docs if kind == 'Clinical Note')}"
        self.docstatus = 0
        self.modified = "1"
        self._persist()

    def save(self):
        self.check_permission("write")
        self.modified = str(int(self.modified) + 1)
        self._persist()

    def submit(self):
        self.check_permission("submit")
        self.docstatus = 1
        self.modified = str(int(self.modified) + 1)
        self._persist()
        if self.model.fail_submit:
            raise RuntimeError("native submit failure")

    def _persist(self):
        fields = {key: copy.deepcopy(value) for key, value in self.__dict__.items()
                  if key not in ("model", "doctype")}
        self.model.docs[(self.doctype, self.name)] = fields
        if self.doctype == "Patient Encounter":
            self.model.docs[("Patient Appointment", self.appointment)]["status"] = "Closed"


class ModelFrappe:
    PermissionError = PermissionError
    DoesNotExistError = KeyError

    def __init__(self):
        self.session = SimpleNamespace(user="doctor")
        self.denied = set()
        self.fail_submit = False
        self.docs = {
            ("Patient Appointment", "A1"): dict(name="A1", patient="P1", practitioner="DR1",
                                                status="Open", procedure_template=None, therapy_type=None,
                                                template_dt=None),
            ("Patient", "P1"): dict(name="P1"),
            ("Item", "ITEM-1"): dict(name="ITEM-1"),
            ("Prescription Dosage", "DOS-1"): dict(name="DOS-1"),
            ("Prescription Dosage", "DOS-2"): dict(name="DOS-2"),
            ("Prescription Duration", "PER-1"): dict(name="PER-1"),
            ("Dosage Form", "FORM-1"): dict(name="FORM-1"),
            ("Medication Request", "REQ-1"): dict(name="REQ-1"),
            ("UOM", "UOM-1"): dict(name="UOM-1"),
            ("Code Value", "CV-1"): dict(name="CV-1"),
        }

    def get_doc(self, doctype, name):
        return ModelDoc(self, doctype, **copy.deepcopy(self.docs[(doctype, name)]))

    def new_doc(self, doctype):
        return ModelDoc(self, doctype)

    def get_meta(self, doctype):
        return SimpleNamespace(
            fields=[SimpleNamespace(fieldname=name, fieldtype={
                "drug_code": "Link", "dosage": "Link", "period": "Link", "dosage_form": "Link",
                "strength_uom": "Link", "intent": "Link", "priority": "Link",
                "comment": "Small Text", "interval": "Int", "interval_uom": "Select",
                "strength": "Float", "dosage_by_interval": "Check", "number_of_repeats_allowed": "Float",
                "update_schedule": "Check",
                }.get(name, "Data"), options={
                    "drug_code": "Item", "dosage": "Prescription Dosage",
                    "period": "Prescription Duration", "dosage_form": "Dosage Form",
                    "strength_uom": "UOM", "intent": "Code Value", "priority": "Code Value",
                }.get(name)) for name in (
                "drug_code", "drug_name", "dosage", "period", "dosage_form",
                "medication", "medication_request", "update_schedule",
                "comment", "interval", "interval_uom", "strength", "strength_uom",
                "dosage_by_interval", "number_of_repeats_allowed", "intent", "priority",
            )],
            get_permlevel_access=lambda *a, **kw: [0],
            get_masked_fields=lambda: [],
            get_field=lambda field: SimpleNamespace(permlevel=0, options={
                "symptoms": "Patient Encounter Symptom",
                "diagnosis": "Patient Encounter Diagnosis",
                "drug_prescription": "Drug Prescription",
            }.get(field)),
        )

    def get_all(self, doctype, filters, fields, limit_page_length):
        result = []
        for (kind, name), values in self.docs.items():
            if kind != doctype:
                continue
            if all((value != 2 if isinstance(condition, list) else value == condition)
                   for key, condition in filters.items() for value in [values.get(key)]):
                result.append({"name": name})
        return result[:limit_page_length]

    def get_list(self, doctype, filters, fields, page_length, start=0, order_by=None):
        if (doctype, "read") in self.denied:
            raise PermissionError("native denial")
        return self.get_all(doctype, filters, fields, start + page_length)[start:]

    def request(self, function, *args):
        before = copy.deepcopy(self.docs)
        try:
            return function(*args)
        except Exception:
            self.docs = before
            raise


class NativeModelFlowTests(unittest.TestCase):
    def setUp(self):
        self.frappe = ModelFrappe()
        self.gateway = NativeGateway(self.frappe)
        self.gateway.setting = lambda key: None

        def mapper(context):
            ModelDoc(self.frappe, "Patient Encounter").check_permission("create")
            return ModelDoc(self.frappe, "Patient Encounter", appointment=context["appointment"],
                            patient=context["patient"], practitioner=context["practitioner"],
                            symptoms=[], diagnosis=[], drug_prescription=[], lab_test_prescription=[],
                            procedure_prescription=[], therapies=[], submit_orders_on_save=0)

        self.gateway._linked_map = mapper

    def request(self, method, *args):
        return self.frappe.request(method, self.gateway, *args)

    def test_medications_add_edit_and_preserve_native_context(self):
        medication = {"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}
        result = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [medication], "edit": []}}})
        row = result["medications"]["items"][0]
        self.assertEqual(row["id"], "DRUG-1")
        self.assertEqual(row["drug_code"], "ITEM-1")
        self.assertTrue(row["supported"])
        self.assertEqual(self.frappe.docs[("Patient Encounter", "E1")]["submit_orders_on_save"], 0)
        self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["medication"] = "HIDDEN-MED"
        edit = {"id": "DRUG-1", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"}
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {
                "appointment_ref": "A1", "expected_version": "old",
                "clinical": {"medications": {"add": [], "edit": [edit]}}})
        updated = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "1",
            "clinical": {"medications": {"add": [], "edit": [edit]}}})
        self.assertEqual(updated["medications"]["items"][0]["dosage"], "DOS-2")
        self.assertEqual(self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["medication"], "HIDDEN-MED")
        self.assertNotIn("HIDDEN-MED", repr(updated))
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "2", "clinical": {}})
        self.assertEqual(len(self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"]), 1)

    def test_medication_edit_refuses_issued_or_forged_or_item_override(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        before = copy.deepcopy(self.frappe.docs)
        for edit in (
            {"id": "OTHER", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"},
            {"id": "DRUG-1", "drug_code": "ITEM-1", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"},
        ):
            with self.subTest(edit=edit), self.assertRaises(ValueError):
                self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": "1",
                    "clinical": {"medications": {"add": [], "edit": [edit]}}})
            self.assertEqual(self.frappe.docs, before)
        self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["medication_request"] = "REQ-1"
        projected = self.request(linked_encounter.open_for_appointment, "A1")["medications"]["items"][0]
        self.assertEqual(projected["medication_request"], "REQ-1")
        self.assertIs(projected["supported"], False)
        edit = {"id": "DRUG-1", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"}
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": "1",
                "clinical": {"medications": {"add": [], "edit": [edit]}}})

    def test_medication_bad_schema_must_not_accept_links(self):
        original = self.frappe.get_meta
        def meta(doctype):
            result = original(doctype)
            if doctype == "Drug Prescription":
                result.fields[0].options = "OTHER"
            return result
        self.frappe.get_meta = meta
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
                "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})

    def test_medication_preserves_unsupported_desk_rows_and_rejects_edits(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        row = self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]
        row["interval"] = 6
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertIs(result["medications"]["items"][0]["supported"], False)
        self.assertEqual(result["medications"]["items"][0]["details"]["interval"], 6)
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": "1",
                "clinical": {"medications": {"add": [], "edit": [{"id": "DRUG-1", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"}]}}})
        self.assertEqual(self.frappe.docs, before)
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": "1", "clinical": {}})
        self.assertEqual(self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["interval"], 6)

    def test_medication_link_denial_rolls_back_and_no_extra_links_accepted(self):
        self.frappe.denied.add(("Item", "read", "ITEM-1"))
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaises(PermissionError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
                "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        self.assertEqual(self.frappe.docs, before)
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
                "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1", "medication": "HIDDEN"}], "edit": []}}})

    def test_medication_edit_requires_persisted_draft(self):
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
                "clinical": {"medications": {"add": [], "edit": [{"id": "DRUG-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}]}}})
        self.assertNotIn(("Patient Encounter", "E1"), self.frappe.docs)

    def test_medication_hidden_field_returns_unavailable_without_values(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        original = self.gateway._linked_fields
        def fields(doctype, *args, **kwargs):
            if doctype == "Drug Prescription":
                raise LinkedFieldAccessError("masked")
            return original(doctype, *args, **kwargs)
        self.gateway._linked_fields = fields
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(result["medications"], {"state": "unavailable", "items": [], "complete": False})
        self.assertNotIn("ITEM-1", repr(result["medications"]))

    def test_denied_medication_master_hides_section_and_blocks_edit(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"symptoms": ["Pain"], "note": {"id": None, "modified": None, "text": "note"},
                         "medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        self.frappe.denied.add(("Item", "read", "ITEM-1"))
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(result["medications"], {"state": "unavailable", "items": [], "complete": False})
        self.assertEqual(result["encounter"]["symptoms"], ["Pain"])
        self.assertEqual(result["notes"]["items"][0]["text"], "note")
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaises(ValueError):
            self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": "1",
                "clinical": {"medications": {"add": [], "edit": [{"id": "DRUG-1", "dosage": "DOS-2", "period": "PER-1", "dosage_form": "FORM-1"}]}}})
        self.assertEqual(self.frappe.docs, before)

    def test_denied_request_and_detail_links_hide_medications(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        row = self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]
        for field, value, doctype in (("medication_request", "REQ-1", "Medication Request"),
                                      ("strength_uom", "UOM-1", "UOM"),
                                      ("intent", "CV-1", "Code Value"),
                                      ("priority", "CV-1", "Code Value")):
            row[field] = value
            self.frappe.denied.add((doctype, "read", value))
            result = self.request(linked_encounter.open_for_appointment, "A1")
            with self.subTest(field=field):
                self.assertEqual(result["medications"]["state"], "unavailable")
                self.assertNotIn(value, repr(result["medications"]))
            self.frappe.denied.clear()
            row[field] = None

    def test_denied_standard_links_hide_medications(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        for doctype, name in (("Prescription Dosage", "DOS-1"), ("Prescription Duration", "PER-1"), ("Dosage Form", "FORM-1")):
            self.frappe.denied.add((doctype, "read", name))
            with self.subTest(doctype=doctype):
                result = self.request(linked_encounter.open_for_appointment, "A1")
                self.assertEqual(result["medications"]["state"], "unavailable")
                self.assertNotIn(name, repr(result["medications"]))
            self.frappe.denied.clear()

    def test_derived_drug_name_is_not_projected(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["drug_name"] = "SECRET NAME"
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertNotIn("SECRET NAME", repr(result))
        self.assertNotIn("drug_name", result["medications"]["items"][0]["details"])

    def test_stale_detail_link_schema_makes_medications_unavailable(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        original = self.frappe.get_meta
        def meta(doctype):
            result = original(doctype)
            if doctype == "Drug Prescription":
                for field in result.fields:
                    if field.fieldname == "strength_uom":
                        field.options = "OTHER"
            return result
        self.frappe.get_meta = meta
        self.assertEqual(self.request(linked_encounter.open_for_appointment, "A1")["medications"]["state"], "unavailable")

    def test_malformed_prescription_metadata_is_unavailable(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        original = self.frappe.get_meta
        def meta(doctype):
            result = original(doctype)
            if doctype == "Drug Prescription":
                result.fields = None
            return result
        self.frappe.get_meta = meta
        self.assertEqual(self.request(linked_encounter.open_for_appointment, "A1")["medications"]["state"], "unavailable")

    def test_unexpected_reference_failure_propagates(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        original = self.frappe.get_doc
        def get_doc(doctype, name):
            if doctype == "Item":
                raise RuntimeError("unexpected native failure")
            return original(doctype, name)
        self.frappe.get_doc = get_doc
        with self.assertRaisesRegex(RuntimeError, "unexpected native failure"):
            self.request(linked_encounter.open_for_appointment, "A1")

    def test_duplicate_references_checked_once_per_projection(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        rows = self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"]
        rows.append(dict(rows[0], name="DRUG-2"))
        self.frappe.get_doc = Mock(wraps=self.frappe.get_doc)
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(len(result["medications"]["items"]), 2)
        self.assertEqual(self.frappe.get_doc.call_args_list.count(unittest.mock.call("Item", "ITEM-1")), 1)

    def test_missing_request_is_unavailable_not_unissued(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"][0]["medication_request"] = "MISSING"
        self.assertEqual(self.request(linked_encounter.open_for_appointment, "A1")["medications"],
                         {"state": "unavailable", "items": [], "complete": False})

    def test_medication_projection_signals_incomplete_at_limit(self):
        self.request(linked_encounter.save_linked, {"appointment_ref": "A1", "expected_version": None,
            "clinical": {"medications": {"add": [{"drug_code": "ITEM-1", "dosage": "DOS-1", "period": "PER-1", "dosage_form": "FORM-1"}], "edit": []}}})
        rows = self.frappe.docs[("Patient Encounter", "E1")]["drug_prescription"]
        rows.extend(dict(rows[0], name=f"DRUG-{index}") for index in range(2, 102))
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(len(result["medications"]["items"]), 100)
        self.assertIs(result["medications"]["complete"], False)

    def test_reception_appointment_save_note_update_reopen_and_submit(self):
        opened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertIsNone(opened["encounter"])
        self.assertNotIn(("Patient Encounter", "E1"), self.frappe.docs)
        first = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"symptoms": ["Pain"], "diagnoses": ["D1"],
                         "note": {"id": None, "modified": None, "text": "Assessment"}}})
        self.assertEqual(first["context"]["status"], "Closed")
        self.assertEqual(first["notes"]["items"][0]["id"], "N1")
        reopened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(reopened["encounter"]["id"], "E1")
        second = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "1",
            "clinical": {"note": {"id": "N1", "modified": "1", "text": "Updated"}}})
        self.assertEqual(second["notes"]["items"][0]["modified"], "2")
        done = self.request(linked_encounter.complete_linked, {
            "appointment_ref": "A1", "encounter_id": "E1", "expected_version": "2"})
        self.assertEqual(done["encounter"]["docstatus"], 1)
        self.assertEqual(self.frappe.docs[("Clinical Note", "N1")]["note"], "Updated")

    def test_note_denial_and_submit_failure_roll_back_prior_writes(self):
        payload = {"appointment_ref": "A1", "expected_version": None,
                   "clinical": {"note": {"id": None, "modified": None, "text": "hello"}}}
        before = copy.deepcopy(self.frappe.docs)
        self.frappe.denied.add(("Clinical Note", "create"))
        with self.assertRaises(PermissionError):
            self.request(linked_encounter.save_linked, payload)
        self.assertEqual(self.frappe.docs, before)
        self.frappe.denied.clear()
        self.request(linked_encounter.save_linked, payload)
        before = copy.deepcopy(self.frappe.docs)
        self.frappe.fail_submit = True
        with self.assertRaises(RuntimeError):
            self.request(linked_encounter.complete_linked, {
                "appointment_ref": "A1", "encounter_id": "E1", "expected_version": "1"})
        self.assertEqual(self.frappe.docs, before)

    def test_patient_and_appointment_document_denial(self):
        for doctype in ("Patient", "Patient Appointment"):
            self.frappe.denied.add((doctype, "read"))
            with self.subTest(doctype=doctype), self.assertRaises(PermissionError):
                self.request(linked_encounter.open_for_appointment, "A1")
            self.frappe.denied.clear()
        self.assertNotIn(("Patient Encounter", "E1"), self.frappe.docs)

    def test_desk_alternatives_cannot_create_encounter(self):
        for value in ("Therapy Type", "Clinical Procedure Template", "Observation Template"):
            self.frappe.docs[("Patient Appointment", "A1")]["template_dt"] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "not eligible"):
                self.request(linked_encounter.open_for_appointment, "A1")

    def test_desk_statuses_and_closed_without_draft(self):
        for status in ("Open", "Checked In", "Confirmed", "Scheduled"):
            self.frappe.docs[("Patient Appointment", "A1")]["status"] = status
            with self.subTest(status=status):
                self.assertIsNone(self.request(linked_encounter.open_for_appointment, "A1")["encounter"])
        for status in ("Closed", "Cancelled", "No Show", "Checked Out"):
            self.frappe.docs[("Patient Appointment", "A1")]["status"] = status
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.request(linked_encounter.open_for_appointment, "A1")

    def test_stale_note_rolls_back_encounter_save(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "hello"}}})
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaisesRegex(ValueError, "stale note"):
            self.request(linked_encounter.save_linked, {
                "appointment_ref": "A1", "expected_version": "1",
                "clinical": {"note": {"id": "N1", "modified": "old", "text": "changed"}}})
        self.assertEqual(self.frappe.docs, before)

    def test_multiple_notes_open_edit_one_and_create_another(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "one"}}})
        original = copy.deepcopy(self.frappe.docs[("Clinical Note", "N1")])
        self.frappe.docs[("Clinical Note", "N2")] = dict(original, name="N2", note="two")
        opened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual([n["id"] for n in opened["notes"]["items"]], ["N1", "N2"])
        updated = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "1",
            "clinical": {"note": {"id": "N1", "modified": "1", "text": "edited"}}})
        self.assertEqual(self.frappe.docs[("Clinical Note", "N2")]["note"], "two")
        self.assertEqual(updated["notes"]["items"][0]["text"], "edited")
        result = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "2",
            "clinical": {"note": {"id": None, "modified": None, "text": "three"}}})
        self.assertEqual([n["id"] for n in result["notes"]["items"]], ["N1", "N2", "N3"])
        self.assertEqual(self.frappe.docs[("Clinical Note", "N2")]["note"], "two")

    def test_note_pagination_and_denied_document(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "one"}}})
        original = self.frappe.docs[("Clinical Note", "N1")]
        for index in range(2, 24):
            name = f"N{index}"
            self.frappe.docs[("Clinical Note", name)] = dict(original, name=name)
        first = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(len(first["notes"]["items"]), 20)
        self.assertEqual(first["notes"]["next_offset"], 20)
        second = self.request(linked_encounter.open_for_appointment, "A1", 20)
        self.assertEqual(len(second["notes"]["items"]), 3)
        self.assertIsNone(second["notes"]["next_offset"])
        self.frappe.denied.add(("Clinical Note", "read", "N1"))
        denied = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(denied["notes"], {"state": "unavailable", "items": [], "next_offset": None})

    def test_denied_lookahead_does_not_disclose_continuation(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "one"}}})
        original = self.frappe.docs[("Clinical Note", "N1")]
        for index in range(2, 22):
            name = f"N{index}"
            self.frappe.docs[("Clinical Note", name)] = dict(original, name=name)
        self.frappe.denied.add(("Clinical Note", "read", "N21"))
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(len(result["notes"]["items"]), 20)
        self.assertIsNone(result["notes"]["next_offset"])
        self.assertNotIn("N21", [note["id"] for note in result["notes"]["items"]])

    def test_lookahead_missing_or_mismatched_hides_continuation_but_other_errors_propagate(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "one"}}})
        original_note = self.frappe.docs[("Clinical Note", "N1")]
        for index in range(2, 22):
            name = f"N{index}"
            self.frappe.docs[("Clinical Note", name)] = dict(original_note, name=name)
        original = self.gateway._linked_note
        for error in (KeyError("missing"), LinkedNoteMismatch("mismatch"),
                      LinkedFieldAccessError("masked")):
            def read(context, encounter_id, note_id, failure=error):
                if note_id == "N21":
                    raise failure
                return original(context, encounter_id, note_id)
            self.gateway._linked_note = read
            with self.subTest(error=error):
                result = self.request(linked_encounter.open_for_appointment, "A1")
                self.assertEqual(len(result["notes"]["items"]), 20)
                self.assertIsNone(result["notes"]["next_offset"])
        def broken(context, encounter_id, note_id):
            if note_id == "N21":
                raise RuntimeError("unexpected")
            return original(context, encounter_id, note_id)
        self.gateway._linked_note = broken
        with self.assertRaisesRegex(RuntimeError, "unexpected"):
            self.request(linked_encounter.open_for_appointment, "A1")

    def test_unexpected_note_list_failure_propagates(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None, "clinical": {}})
        for error in (RuntimeError("database error"), ValueError("unexpected native error")):
            self.frappe.get_list = Mock(side_effect=error)
            with self.subTest(error=error), self.assertRaises(type(error)):
                self.request(linked_encounter.open_for_appointment, "A1")

    def test_masked_note_fields_return_unavailable(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None, "clinical": {}})
        original = self.gateway._linked_fields
        def fields(doctype, *args, **kwargs):
            if doctype == "Clinical Note":
                raise LinkedFieldAccessError("masked")
            return original(doctype, *args, **kwargs)
        self.gateway._linked_fields = fields
        result = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(result["notes"], {"state": "unavailable", "items": [], "next_offset": None})

    def test_denied_note_read_does_not_block_encounter_read_or_submit(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "secret"}}})
        self.frappe.denied.add(("Clinical Note", "read"))
        opened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(opened["notes"], {"state": "unavailable", "items": [], "next_offset": None})
        done = self.request(linked_encounter.complete_linked, {
            "appointment_ref": "A1", "encounter_id": "E1", "expected_version": "1"})
        self.assertEqual(done["encounter"]["docstatus"], 1)
        self.assertEqual(done["notes"]["state"], "unavailable")

    def test_denied_explicit_note_edit_rolls_back_encounter(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "one"}}})
        self.frappe.denied.add(("Clinical Note", "write"))
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaises(PermissionError):
            self.request(linked_encounter.save_linked, {
                "appointment_ref": "A1", "expected_version": "1",
                "clinical": {"note": {"id": "N1", "modified": "1", "text": "changed"}}})
        self.assertEqual(self.frappe.docs, before)

    def test_note_create_with_read_denied_and_plain_save_no_duplicate(self):
        self.frappe.denied.add(("Clinical Note", "read"))
        result = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "new"}}})
        self.assertEqual(result["notes"]["state"], "unavailable")
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "1", "clinical": {}})
        self.assertEqual([name for kind, name in self.frappe.docs if kind == "Clinical Note"], ["N1"])

    def test_note_from_other_context_cannot_be_updated(self):
        self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"note": {"id": None, "modified": None, "text": "original"}}})
        self.frappe.docs[("Clinical Note", "N1")]["patient"] = "OTHER"
        before = copy.deepcopy(self.frappe.docs)
        with self.assertRaisesRegex(ValueError, "note identity mismatch"):
            self.request(linked_encounter.save_linked, {
                "appointment_ref": "A1", "expected_version": "1",
                "clinical": {"note": {"id": "N1", "modified": "1", "text": "changed"}}})
        self.assertEqual(self.frappe.docs, before)
