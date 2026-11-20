import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clinic_ui.endpoints import linked_encounter
from clinic_ui.runtime.frappe_gateway import Gateway as NativeGateway


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

    def linked_project(self, context, encounter):
        return {"context": context, "encounter": encounter, "note": self.note}

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
        self.assertEqual(linked_encounter.open_for_appointment(self.gateway, "A1")["note"]["id"], "N1")

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
        if (self.doctype, operation) in self.model.denied:
            raise PermissionError("native denial")

    def set(self, field, value):
        setattr(self, field, value)

    def append(self, field, value):
        getattr(self, field).append(value)

    def insert(self):
        self.check_permission("create")
        self.name = "E1" if self.doctype == "Patient Encounter" else "N1"
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
    def __init__(self):
        self.session = SimpleNamespace(user="doctor")
        self.denied = set()
        self.fail_submit = False
        self.docs = {
            ("Patient Appointment", "A1"): dict(name="A1", patient="P1", practitioner="DR1",
                                                status="Open", procedure_template=None, therapy_type=None,
                                                template_dt=None),
            ("Patient", "P1"): dict(name="P1"),
        }

    def get_doc(self, doctype, name):
        return ModelDoc(self, doctype, **copy.deepcopy(self.docs[(doctype, name)]))

    def new_doc(self, doctype):
        return ModelDoc(self, doctype)

    def get_meta(self, doctype):
        return SimpleNamespace(
            get_permlevel_access=lambda *a, **kw: [0],
            get_masked_fields=lambda: [],
            get_field=lambda field: SimpleNamespace(permlevel=0, options={
                "symptoms": "Patient Encounter Symptom",
                "diagnosis": "Patient Encounter Diagnosis",
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

    def test_reception_appointment_save_note_update_reopen_and_submit(self):
        opened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertIsNone(opened["encounter"])
        self.assertEqual(len(self.frappe.docs), 2)
        first = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": None,
            "clinical": {"symptoms": ["Pain"], "diagnoses": ["D1"],
                         "note": {"id": None, "modified": None, "text": "Assessment"}}})
        self.assertEqual(first["context"]["status"], "Closed")
        self.assertEqual(first["note"]["id"], "N1")
        reopened = self.request(linked_encounter.open_for_appointment, "A1")
        self.assertEqual(reopened["encounter"]["id"], "E1")
        second = self.request(linked_encounter.save_linked, {
            "appointment_ref": "A1", "expected_version": "1",
            "clinical": {"note": {"id": "N1", "modified": "1", "text": "Updated"}}})
        self.assertEqual(second["note"]["modified"], "2")
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
        self.assertEqual(len(self.frappe.docs), 2)

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
