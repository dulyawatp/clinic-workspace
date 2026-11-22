import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from clinic_ui.endpoints import linked_encounter


class ProductionLinkedEndpointSaveTest(unittest.TestCase):
    def setUp(self):
        self.gateway = Mock()
        self.gateway.setting.return_value = True
        self.gateway.frappe.db.sql.return_value = [{"isolation": "REPEATABLE-READ", "snapshot": 0}]
        self.actor = SimpleNamespace(user="doctor")
        self.payload = {"appointment_ref": "APT-1", "expected_version": None, "clinical": {"symptoms": ["Pain"]}}
        self.appointment = SimpleNamespace(name="APT-1")
        self.saved = object()
        self.context = {"appointment": "APT-1", "patient": "PAT-1", "practitioner": "DOC-1", "status": "Checked In", "_alternative": False}
        self.projected = {"context": {"patient": "opaque"}}
        self.patches = [
            patch("clinic_ui.endpoints.linked_encounter.production_read.authorize_if_selected", return_value=(None, self.actor, "DOC")),
            patch("clinic_ui.runtime.production_linked.lock_current_linked", return_value=(self.appointment, None)),
            patch("clinic_ui.runtime.production_linked.read_context", return_value=(self.context, "opaque")),
            patch("clinic_ui.runtime.production_linked_save.save_locked_draft", return_value=self.saved),
            patch("clinic_ui.runtime.production_linked.project_saved_draft", return_value=self.projected),
        ]
        self.auth, self.lock, self.read, self.save, self.project = [p.start() for p in self.patches]
        self.addCleanup(lambda: [p.stop() for p in reversed(self.patches)])
        self.gateway.frappe.get_doc.return_value = self.appointment

    def test_saves_once_and_projects_actual_saved_document_after_revalidation(self):
        self.assertIs(linked_encounter.save_linked(self.gateway, self.payload), self.projected)
        self.auth.assert_called_once_with(self.gateway, frozenset({"DOC"}))
        self.lock.assert_called_once_with(self.gateway, "APT-1")
        self.save.assert_called_once_with(self.gateway, self.appointment, None, self.context, None, self.payload["clinical"])
        self.assertEqual(self.read.call_count, 2)
        self.gateway.frappe.get_doc.assert_called_once_with("Patient Appointment", "APT-1", for_update=True)
        self.project.assert_called_once_with(self.gateway, self.context, "opaque", self.saved)

    def test_unsupported_payload_never_mutates(self):
        for clinical in ({"note": {}}, {"medications": {}}, {"symptoms": [1]}):
            with self.subTest(clinical=clinical):
                with self.assertRaises(linked_encounter.LinkedEncounterError):
                    linked_encounter.save_linked(self.gateway, {**self.payload, "clinical": clinical})
        self.save.assert_not_called()

    def test_first_save_projects_current_closed_status(self):
        current = {**self.context, "status": "Closed"}
        self.read.side_effect = [(self.context, "opaque"), (current, "opaque")]
        self.assertIs(linked_encounter.save_linked(self.gateway, self.payload), self.projected)
        self.save.assert_called_once()
        self.project.assert_called_once_with(self.gateway, current, "opaque", self.saved)

    def test_changed_binding_reference_or_invalid_status_transition_aborts(self):
        cases = (
            (self.context, {**self.context, "practitioner": "DOC-2"}, "opaque"),
            (self.context, self.context, "other"),
            ({**self.context, "status": "Closed"}, self.context, "opaque"),
            (self.context, {**self.context, "status": "Cancelled"}, "opaque"),
        )
        for initial, current, patient_ref in cases:
            with self.subTest(initial=initial, current=current, patient_ref=patient_ref):
                self.read.side_effect = [(initial, "opaque"), (current, patient_ref)]
                with self.assertRaises(linked_encounter.LinkedEncounterError):
                    linked_encounter.save_linked(self.gateway, self.payload)
        self.assertEqual(self.save.call_count, len(cases))
        self.project.assert_not_called()

    def test_disabled_flag_or_bad_isolation_never_saves(self):
        for flag, profile in [(1, {"isolation": "REPEATABLE-READ", "snapshot": 0}), (True, {"isolation": "READ-COMMITTED", "snapshot": 0}), (True, {"isolation": "REPEATABLE-READ", "snapshot": 1})]:
            with self.subTest(flag=flag, profile=profile):
                self.gateway.setting.return_value = flag
                self.gateway.deny.side_effect = PermissionError()
                self.gateway.frappe.db.sql.return_value = [profile]
                with self.assertRaises((PermissionError, linked_encounter.LinkedEncounterError)):
                    linked_encounter.save_linked(self.gateway, self.payload)
        self.save.assert_not_called()

    def test_witness_revalidation_failure_never_projects_or_replays(self):
        self.read.side_effect = [(self.context, "opaque"), linked_encounter.LinkedEncounterError("linked visit unavailable")]
        with self.assertRaises(linked_encounter.LinkedEncounterError):
            linked_encounter.save_linked(self.gateway, self.payload)
        self.save.assert_called_once()
        self.project.assert_not_called()

    def test_unknown_save_result_is_not_replayed(self):
        self.save.side_effect = RuntimeError("unknown result")
        with self.assertRaisesRegex(RuntimeError, "unknown result"):
            linked_encounter.save_linked(self.gateway, self.payload)
        self.save.assert_called_once()
        self.project.assert_not_called()
