import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


class ProductionLinkedTest(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock()
        self.gateway.frappe.session.user = "doctor@example.com"
        self.gateway.frappe.get_list.side_effect = self.list_rows
        self.gateway._fields_readable.return_value = True
        self.gateway.frappe.get_meta.return_value.get_masked_fields.return_value = []
        self.gateway.linked_context.return_value = {
            "appointment": "APT-1", "patient": "PAT-1",
            "practitioner": "HP-1", "status": "Checked In", "_alternative": False,
        }
        self.gateway._production_encounter_patient_ref.return_value = "opaque"
        self.practitioners = [{"name": "HP-1", "user_id": "doctor@example.com", "status": "Active"}]
        self.witness = Mock(return_value=True)
        state = types.ModuleType("clinic_photos.visit.state")
        state.has_checkin_witness = self.witness
        self.modules = patch.dict(sys.modules, {
            "clinic_photos": types.ModuleType("clinic_photos"),
            "clinic_photos.visit": types.ModuleType("clinic_photos.visit"),
            "clinic_photos.visit.state": state,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def list_rows(self, doctype, **kwargs):
        if doctype == "Patient Appointment":
            return [{"name": "APT-1"}]
        if doctype == "Healthcare Practitioner":
            return self.practitioners
        return []

    def test_checked_in_witnessed_context(self):
        from clinic_ui.runtime.production_linked import read_context
        context, ref = read_context(self.gateway, "APT-1", "doctor@example.com")
        self.assertEqual(context, self.gateway.linked_context.return_value)
        self.assertEqual(ref, "opaque")
        self.witness.assert_called_once_with("APT-1", "PAT-1")

    def test_locked_context_uses_current_document_not_legacy_snapshot(self):
        from clinic_ui.runtime.production_linked import read_context

        locked = Mock()
        locked.name = "APT-1"
        patient = Mock()
        patient.name, patient.creation = "PAT-CURRENT", datetime(2025, 1, 1)
        practitioner = Mock()
        practitioner.name, practitioner.status, practitioner.user_id = "HP-1", "Active", "doctor@example.com"
        self.gateway.frappe.get_doc.side_effect = [patient, practitioner]
        current = dict(self.gateway.linked_context.return_value, patient="PAT-CURRENT")
        self.gateway._linked_context_from_doc.return_value = current
        self.gateway.linked_context.side_effect = AssertionError("stale snapshot")
        with patch("clinic_ui.core.production_patient_ref.encode", return_value="opaque"), \
             patch("clinic_ui.endpoints.production_read.patient_ref_key", return_value=b"key"):
            context, ref = read_context(
                self.gateway, "APT-1", "doctor@example.com", locked_appointment=locked
            )
        self.assertEqual(context, current)
        self.assertEqual(ref, "opaque")
        self.gateway._linked_context_from_doc.assert_called_once_with(locked)
        self.gateway.linked_context.assert_not_called()
        self.witness.assert_called_once_with("APT-1", "PAT-CURRENT")
        self.gateway._production_encounter_patient_ref.assert_called_once_with("PAT-CURRENT")
        self.assertEqual(self.gateway.frappe.get_doc.call_args_list, [
            unittest.mock.call("Patient", "PAT-CURRENT", for_update=True),
            unittest.mock.call("Healthcare Practitioner", "HP-1", for_update=True),
        ])
        patient.check_permission.assert_called_once_with("read")
        practitioner.check_permission.assert_called_once_with("read")

    def test_locked_context_denies_patient_creation_drift_and_current_practitioner_change(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        from clinic_ui.runtime.production_linked import read_context

        locked = Mock(name="appointment")
        locked.name = "APT-1"
        patient = Mock()
        patient.name, patient.creation = "PAT-1", datetime(2025, 1, 1)
        practitioner = Mock()
        practitioner.name, practitioner.status, practitioner.user_id = "HP-1", "Inactive", "doctor@example.com"
        self.gateway._linked_context_from_doc.return_value = self.gateway.linked_context.return_value
        for encoded in ("changed", "opaque"):
            self.gateway.frappe.get_doc.side_effect = [patient, practitioner]
            with patch("clinic_ui.core.production_patient_ref.encode", return_value=encoded), \
                 patch("clinic_ui.endpoints.production_read.patient_ref_key", return_value=b"key"):
                with self.assertRaises(LinkedEncounterError):
                    read_context(self.gateway, "APT-1", "doctor@example.com", locked_appointment=locked)

    def test_locked_context_rejects_mismatched_appointment(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        from clinic_ui.runtime.production_linked import read_context

        locked = Mock()
        locked.name = "OTHER"
        with self.assertRaises(LinkedEncounterError):
            read_context(self.gateway, "APT-1", "doctor@example.com", locked_appointment=locked)
        self.gateway.linked_context.assert_not_called()
        self.witness.assert_not_called()

    def test_refuses_unwitnessed_or_mismatched_context(self):
        from clinic_ui.runtime.production_linked import read_context
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        for mutation in ("witness", "practitioner", "actor", "status", "mask"):
            with self.subTest(mutation=mutation):
                self.setUp()
                actor = "doctor@example.com"
                if mutation == "witness":
                    self.witness.return_value = False
                elif mutation == "practitioner":
                    self.practitioners = []
                elif mutation == "actor":
                    actor = "other@example.com"
                elif mutation == "status":
                    self.gateway.linked_context.return_value["status"] = "Open"
                else:
                    self.gateway._fields_readable.return_value = False
                with self.assertRaises(LinkedEncounterError):
                    read_context(self.gateway, "APT-1", actor)

    def test_current_lock_serializes_appointment_and_bounded_encounter_lookup(self):
        from clinic_ui.runtime.production_linked import lock_current_linked

        appointment = Mock(name="appointment", name_value="unused")
        appointment.name, appointment.patient, appointment.practitioner = "APT-1", "PAT-1", "HP-1"
        encounter = Mock()
        encounter.name, encounter.appointment = "ENC-1", "APT-1"
        encounter.patient, encounter.practitioner = "PAT-1", "HP-1"
        encounter.docstatus, encounter.creation = 0, "created"
        self.gateway.frappe.db.sql.return_value = [("ENC-1",)]
        self.gateway.frappe.get_doc.side_effect = [encounter, appointment]
        locked, current = lock_current_linked(self.gateway, "APT-1")
        self.assertIs(locked, appointment)
        self.assertIs(current, encounter)
        appointment.check_permission.assert_called_once_with("read")
        encounter.check_permission.assert_called_once_with("read")
        self.assertEqual(self.gateway.frappe.mock_calls, [
            unittest.mock.call.db.sql(
                "select name from `tabPatient Encounter` where appointment = %s and docstatus != 2 order by name limit 2 for update",
                ("APT-1",)),
            unittest.mock.call.get_doc("Patient Encounter", "ENC-1", for_update=True),
            unittest.mock.call.get_doc("Patient Appointment", "APT-1", for_update=True),
        ])

    def test_current_lock_empty_scans_before_appointment(self):
        from clinic_ui.runtime.production_linked import lock_current_linked

        appointment = self.gateway.frappe.get_doc.return_value
        appointment.name, appointment.patient, appointment.practitioner = "APT-1", "PAT-1", "HP-1"
        self.gateway.frappe.db.sql.return_value = []
        self.assertEqual(lock_current_linked(self.gateway, "APT-1"), (appointment, None))
        self.assertEqual(self.gateway.frappe.mock_calls, [
            unittest.mock.call.db.sql(
                "select name from `tabPatient Encounter` where appointment = %s and docstatus != 2 order by name limit 2 for update",
                ("APT-1",)),
            unittest.mock.call.get_doc("Patient Appointment", "APT-1", for_update=True),
            unittest.mock.call.get_doc().check_permission("read"),
        ])

    def test_current_lock_rejects_ambiguous_links(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        from clinic_ui.runtime.production_linked import lock_current_linked

        for rows in ([("ENC-1",), ("ENC-2",)], [("ENC-1", "extra")], [(None,)], "bad"):
            with self.subTest(rows=rows):
                self.gateway.frappe.db.sql.return_value = rows
                with self.assertRaises(LinkedEncounterError):
                    lock_current_linked(self.gateway, "APT-1")
        self.gateway.frappe.get_doc.assert_not_called()

    def test_current_lock_rejects_mismatched_locked_identity(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        from clinic_ui.runtime.production_linked import lock_current_linked

        encounter = Mock()
        encounter.name, encounter.appointment = "ENC-1", "APT-1"
        encounter.patient, encounter.practitioner = "PAT-1", "HP-1"
        encounter.docstatus, encounter.creation = 0, "created"
        appointment = Mock()
        appointment.name, appointment.patient, appointment.practitioner = "APT-1", "OTHER", "HP-1"
        self.gateway.frappe.db.sql.return_value = [("ENC-1",)]
        self.gateway.frappe.get_doc.side_effect = [encounter, appointment]
        with self.assertRaises(LinkedEncounterError):
            lock_current_linked(self.gateway, "APT-1")


class ProductionOpenTest(unittest.TestCase):
    def setUp(self):
        from clinic_ui.core import production_enrollment, production_patient_ref
        from .fake_gateway import session_view
        from .test_production_read_adapter import gateway, REF_KEY
        self.gateway = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        self.gateway.current_session = session_view(user="doctor@example.com", roles=("Physician",))
        self.gateway.frappe.session = types.SimpleNamespace(user="doctor@example.com")
        self.context = {"appointment": "APT-1", "patient": "PAT-1",
                        "practitioner": "HP-1", "status": "Checked In", "_alternative": False}
        self.gateway.frappe.get_list = Mock(side_effect=lambda doctype, **kwargs:
            [{"name": "APT-1"}] if doctype == "Patient Appointment" else
            [{"name": "HP-1", "user_id": "doctor@example.com", "status": "Active"}])
        self.gateway.frappe.get_meta = Mock()
        self.gateway.frappe.get_meta.return_value.get_masked_fields.return_value = []
        self.gateway._fields_readable = Mock(return_value=True)
        self.gateway.linked_context = Mock(return_value=self.context)
        self.gateway.linked_resolve = Mock(side_effect=AssertionError("legacy resolution"))
        self.ref = production_patient_ref.encode(REF_KEY, "client.example", "PAT-1", datetime(2025, 1, 1))
        self.gateway._production_encounter_patient_ref = Mock(return_value=self.ref)
        self.gateway.linked_find = Mock(return_value={"id": "ENC-1", "modified": "v1", "docstatus": 0})
        self.gateway.linked_project = Mock(side_effect=lambda context, encounter, offset: {
            "context": {k: v for k, v in context.items() if not k.startswith("_")},
            "encounter": encounter, "notes": {"state": "unavailable"},
            "medications": {"state": "unavailable"},
        })
        self.witness = Mock(return_value=True)
        state = types.ModuleType("clinic_photos.visit.state")
        state.has_checkin_witness = self.witness
        self.stack = [
            patch.object(production_enrollment, "verify_production_enrollment", return_value=True),
            patch.dict(sys.modules, {"clinic_photos": types.ModuleType("clinic_photos"),
                                    "clinic_photos.visit": types.ModuleType("clinic_photos.visit"),
                                    "clinic_photos.visit.state": state}),
        ]
        for item in self.stack:
            item.start()
            self.addCleanup(item.stop)

    def test_production_open_witnessed_uses_opaque_patient(self):
        from clinic_ui.endpoints.linked_encounter import open_for_appointment
        result = open_for_appointment(self.gateway, "APT-1")
        self.assertEqual(result["context"]["patient"], self.ref)
        self.assertNotIn("PAT-1", repr(result))
        self.assertEqual(result["notes"]["state"], "unavailable")
        self.assertEqual(result["medications"]["state"], "unavailable")
        self.gateway.linked_resolve.assert_not_called()
        self.witness.assert_called_once_with("APT-1", "PAT-1")

    def test_closed_requires_existing_draft(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError, open_for_appointment
        self.context["status"] = "Closed"
        result = open_for_appointment(self.gateway, "APT-1")
        self.assertEqual(result["context"]["patient"], self.ref)
        for encounter in (None, {"id": "ENC-1", "modified": "v1", "docstatus": 1}):
            self.gateway.linked_find.return_value = encounter
            with self.assertRaises(LinkedEncounterError):
                open_for_appointment(self.gateway, "APT-1")

    def test_unwitnessed_never_projects_raw_patient(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError, open_for_appointment
        self.witness.return_value = False
        with self.assertRaises(LinkedEncounterError):
            open_for_appointment(self.gateway, "APT-1")
        self.gateway.linked_project.assert_not_called()

    def test_unexpected_projected_patient_is_not_returned(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError, open_for_appointment
        self.gateway.linked_project.return_value = {"context": {"patient": "OTHER"}}
        self.gateway.linked_project.side_effect = None
        with self.assertRaises(LinkedEncounterError):
            open_for_appointment(self.gateway, "APT-1")

    def test_production_writes_remain_denied(self):
        from clinic_ui.endpoints.linked_encounter import complete_linked, save_linked
        from .fake_gateway import Denied
        for action in (save_linked, complete_linked):
            with self.assertRaises(Denied):
                action(self.gateway, {})


class LinkedContextProjectionTest(unittest.TestCase):
    def test_saved_projection_uses_locked_document_and_opaque_patient(self):
        from clinic_ui.runtime.production_linked import project_saved_draft

        gateway = Mock()
        context = {"appointment": "APT-1", "patient": "PAT-1", "practitioner": "HP-1"}
        doc = Mock()
        doc.name, doc.appointment, doc.patient, doc.practitioner = "ENC-1", "APT-1", "PAT-1", "HP-1"
        doc.docstatus, doc.modified = 0, "v1"
        gateway._linked_project_doc.return_value = {
            "context": dict(context), "encounter": {"id": "ENC-1"},
            "notes": {"state": "unavailable"}, "medications": {"state": "unavailable"},
        }
        result = project_saved_draft(gateway, context, "opaque", doc)
        self.assertEqual(result["context"]["patient"], "opaque")
        self.assertNotIn("PAT-1", repr(result))
        gateway._linked_project_doc.assert_called_once_with(
            context, {"id": "ENC-1", "modified": "v1", "docstatus": 0}, doc
        )
        gateway.linked_project.assert_not_called()

    def test_saved_projection_rejects_wrong_identity_and_nondraft(self):
        from clinic_ui.endpoints.linked_encounter import LinkedEncounterError
        from clinic_ui.runtime.production_linked import project_saved_draft

        gateway = Mock()
        context = {"appointment": "APT-1", "patient": "PAT-1", "practitioner": "HP-1"}
        doc = Mock()
        doc.name, doc.appointment, doc.patient, doc.practitioner = "ENC-1", "APT-1", "PAT-1", "HP-1"
        doc.docstatus, doc.modified = 0, "v1"
        for field, value in (("patient", "OTHER"), ("appointment", "OTHER"),
                             ("practitioner", "OTHER"), ("docstatus", 1), ("modified", None)):
            old = getattr(doc, field)
            setattr(doc, field, value)
            with self.subTest(field=field), self.assertRaises(LinkedEncounterError):
                project_saved_draft(gateway, context, "opaque", doc)
            setattr(doc, field, old)
        gateway._linked_project_doc.assert_not_called()

    def test_saved_projection_uses_native_read_checked_document(self):
        from clinic_ui.runtime.frappe_gateway import Gateway
        from clinic_ui.runtime.production_linked import project_saved_draft

        frappe = Mock()
        gateway = Gateway(frappe)
        gateway._linked_fields = Mock()
        gateway._encounter_read_children = Mock(return_value=[])
        gateway._linked_medications = Mock(return_value={"state": "unavailable"})
        gateway._linked_notes = Mock(return_value={"state": "unavailable"})
        context = {"appointment": "APT-1", "patient": "PAT-1", "practitioner": "HP-1"}
        doc = Mock()
        doc.name, doc.appointment, doc.patient, doc.practitioner = "ENC-1", "APT-1", "PAT-1", "HP-1"
        doc.docstatus, doc.modified = 0, "v1"
        result = project_saved_draft(gateway, context, "opaque", doc)
        self.assertEqual(result["encounter"]["modified"], "v1")
        self.assertEqual(result["context"]["patient"], "opaque")
        self.assertEqual(result["notes"]["state"], "unavailable")
        doc.check_permission.assert_called_once_with("read")
        frappe.get_doc.assert_not_called()

    def test_native_projection_uses_same_checks_with_or_without_existing_doc(self):
        from clinic_ui.runtime.frappe_gateway import Gateway

        frappe = Mock()
        gateway = Gateway(frappe)
        gateway._linked_fields = Mock()
        appointment, patient = Mock(), Mock()
        appointment.name, appointment.patient, appointment.practitioner = "APT-1", "PAT-1", "HP-1"
        appointment.status = "Checked In"
        appointment.procedure_template = appointment.therapy_type = appointment.template_dt = None
        frappe.get_doc.side_effect = [appointment, patient, patient]
        legacy = gateway.linked_context("APT-1")
        current = gateway._linked_context_from_doc(appointment)
        self.assertEqual(legacy, current)
        self.assertEqual(appointment.check_permission.call_args_list,
                         [unittest.mock.call("read"), unittest.mock.call("read")])
        self.assertEqual(patient.check_permission.call_args_list,
                         [unittest.mock.call("read"), unittest.mock.call("read")])
        self.assertEqual(frappe.get_doc.call_args_list, [
            unittest.mock.call("Patient Appointment", "APT-1"),
            unittest.mock.call("Patient", "PAT-1"),
            unittest.mock.call("Patient", "PAT-1"),
        ])
        self.assertEqual(gateway._linked_fields.call_count, 2)
