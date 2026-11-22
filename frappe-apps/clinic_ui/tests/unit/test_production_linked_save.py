import unittest
from copy import deepcopy
from types import SimpleNamespace

from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
from clinic_ui.runtime.frappe_gateway import Gateway
from clinic_ui.runtime.production_linked_save import save_locked_draft


class Doc(SimpleNamespace):
    def as_dict(self):
        return {key: deepcopy(value) for key, value in vars(self).items()
                if key != "permissions"}

    def check_permission(self, permission):
        self.permissions.append(permission)

    def set(self, field, value):
        setattr(self, field, value)

    def append(self, field, value):
        getattr(self, field).append(value)

    def insert(self):
        self.name, self.modified = "ENC-1", "v1"
        self.operation = "insert"

    def save(self):
        self.modified = "v2"
        self.operation = "save"


class LinkedSaveTests(unittest.TestCase):
    def setUp(self):
        values = dict(name="APT-1", modified="a1", creation="created", docstatus=0, patient="PAT-1",
                      practitioner="DOC-1", status="Checked In",
                      procedure_template=None, therapy_type=None, template_dt=None,
                      department="D", patient_sex="Female", invoiced=0,
                      company="C", appointment_type="T", insurance_policy=None,
                      insurance_coverage=None)
        self.appointment = Doc(**values, permissions=[])
        self.snapshot = Doc(**values, permissions=[])
        self.context = dict(appointment="APT-1", patient="PAT-1",
                            practitioner="DOC-1", status="Checked In",
                            _alternative=False)
        self.mapped = Doc(name=None, modified=None, docstatus=0,
                          appointment="APT-1", patient="PAT-1", practitioner="DOC-1",
                          medical_department="D", patient_sex="Female", invoiced=0,
                          company="C", appointment_type="T", insurance_policy=None,
                          insurance_coverage=None, permissions=[])
        self.gateway = SimpleNamespace(
            frappe=SimpleNamespace(get_doc=lambda *args: self.snapshot),
            _linked_map=lambda context: self.mapped,
            _linked_fields=lambda *args: object(),
            _persist_linked_draft=lambda doc, is_new: Gateway._persist_linked_draft(
                self.gateway, doc, is_new),
        )

    def test_insert_uses_native_mapping(self):
        result = save_locked_draft(self.gateway, self.appointment, None,
                                   self.context, None, {"symptoms": ["Pain"]})
        self.assertIs(result, self.mapped)
        self.assertEqual((result.name, result.modified, result.docstatus),
                         ("ENC-1", "v1", 0))
        self.assertEqual(self.mapped.symptoms, [{"complaint": "Pain"}])
        self.assertEqual(self.mapped.operation, "insert")
        self.assertEqual(self.mapped.submit_orders_on_save, 0)

    def test_rejects_stale_snapshot_and_unsafe_payload(self):
        self.snapshot.modified = "stale"
        with self.assertRaises(LinkedEncounterError):
            save_locked_draft(self.gateway, self.appointment, None,
                              self.context, None, {"symptoms": []})
        with self.assertRaises(LinkedEncounterError):
            save_locked_draft(self.gateway, self.appointment, None,
                              self.context, None, {"note": {}})

    def test_existing_draft_uses_locked_doc_and_checks_version(self):
        doc = self.mapped
        doc.name, doc.modified, doc.creation = "ENC-1", "v1", "created"
        self.appointment.status = self.context["status"] = "Closed"
        with self.assertRaises(LinkedEncounterError):
            save_locked_draft(self.gateway, self.appointment, doc, self.context,
                              "old", {"diagnoses": ["D"]})
        result = save_locked_draft(self.gateway, self.appointment, doc, self.context,
                                   "v1", {"diagnoses": ["D"]})
        self.assertIs(result, doc)
        self.assertEqual((result.name, result.modified, result.docstatus),
                         ("ENC-1", "v2", 0))
        self.assertEqual(doc.diagnosis, [{"diagnosis": "D"}])
        self.assertEqual(doc.permissions, ["read", "write"])
        self.assertEqual(doc.operation, "save")

    def test_mapped_binding_mismatch_and_field_permission_fail_closed(self):
        self.mapped.company = "wrong"
        with self.assertRaises(LinkedEncounterError):
            save_locked_draft(self.gateway, self.appointment, None, self.context,
                              None, {"symptoms": []})
        self.mapped.company = "C"
        def denied(*args):
            raise PermissionError("denied")
        self.gateway._linked_fields = denied
        with self.assertRaises(PermissionError):
            save_locked_draft(self.gateway, self.appointment, None, self.context,
                              None, {"symptoms": []})
        self.assertFalse(hasattr(self.mapped, "operation"))

    def test_rejects_implicit_field_or_child_snapshot_difference_before_mapping(self):
        for field, locked, stale in (
            ("unlisted_field", "current", "old"),
            ("children", [{"value": "current"}], [{"value": "old"}]),
        ):
            with self.subTest(field=field):
                setattr(self.appointment, field, locked)
                setattr(self.snapshot, field, stale)
                calls = []
                self.gateway._linked_map = lambda context: calls.append(context)
                with self.assertRaises(LinkedEncounterError):
                    save_locked_draft(self.gateway, self.appointment, None,
                                      self.context, None, {"symptoms": []})
                self.assertEqual(calls, [])
                self.assertFalse(hasattr(self.mapped, "operation"))
                delattr(self.appointment, field)
                delattr(self.snapshot, field)

    def test_rejects_unavailable_full_preimage(self):
        self.snapshot.as_dict = lambda: None
        with self.assertRaises(LinkedEncounterError):
            save_locked_draft(self.gateway, self.appointment, None,
                              self.context, None, {"symptoms": []})
