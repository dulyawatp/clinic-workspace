import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class VisitStateTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.whitelist = lambda *a, **kw: lambda fn: fn
        self.frappe.session = types.SimpleNamespace(user="staff@example.test")
        self.frappe.get_roles = lambda user: ["Physician"]
        self.frappe.has_permission = lambda doctype, ptype, *, doc=None, throw=False: True
        self.frappe.get_doc = lambda dt, name, **kw: (
            types.SimpleNamespace(name="A", patient="P", creation="old")
            if dt == "Patient Appointment" else types.SimpleNamespace(name="P", creation="old")
        )
        self.frappe.PermissionError = PermissionError
        self.frappe.ValidationError = ValueError
        self.frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
        self.frappe.db = types.SimpleNamespace(
            get_value=lambda *a, **kw: 1, get_all=lambda *a, **kw: ["Physician"],
            sql=lambda *a, **kw: [(role,) for role in self.frappe.get_roles(self.frappe.session.user)],
            savepoint=lambda name: None, rollback=lambda **kw: None,
        )
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("clinic_photos.visit.state", None)
        self.state = importlib.import_module("clinic_photos.visit.state")

    def test_consent_requires_evidence_and_rejects_forged_audit_fields(self):
        with self.assertRaises(ValueError):
            self.state.change_consent("A", True, "")
        with self.assertRaises(TypeError):
            self.state.change_consent("A", True, "signed", actor="forged")
        with self.assertRaises(TypeError):
            self.state.change_consent("A", True, "signed", attested=True, timestamp="forged")

    def test_read_only_actor_cannot_change_consent(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        self.frappe.get_doc = lambda *a, **kw: doc
        self.frappe.has_permission = lambda doctype, ptype, **kw: ptype != "write"
        with self.assertRaises(PermissionError):
            self.state.lock_visit("A")

    def test_cross_appointment_state_is_rejected(self):
        appointment = types.SimpleNamespace(name="A", patient="P", creation="old")
        self.frappe.get_doc = lambda dt, *a, **kw: (
            appointment if dt == "Patient Appointment" else
            types.SimpleNamespace(name="P", creation="old") if dt == "Patient" else
            types.SimpleNamespace(appointment="B", patient="P",
                                  appointment_snapshot="B", patient_snapshot="P")
        )
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.db = types.SimpleNamespace(
            get_value=lambda *a, **kw: "B",
        )
        with self.assertRaises(ValueError):
            self.state.lock_visit("A")

    def test_closed_appointment_does_not_imply_witness(self):
        self.frappe.db = types.SimpleNamespace(get_value=lambda *a, **kw: None)
        self.assertFalse(self.state.has_checkin_witness("A", "P"))

    def test_state_lookup_uses_unique_link_and_locking_read(self):
        appointment = types.SimpleNamespace(name="A", patient="P", creation="old")
        state = types.SimpleNamespace(name="old-name", appointment="A", patient="P",
                                      appointment_snapshot="A", patient_snapshot="P",
                                      appointment_creation="old", patient_creation="old")
        self.frappe.get_doc = lambda dt, name, **kw: (
            appointment if dt == "Patient Appointment" else
            types.SimpleNamespace(name="P", creation="old") if dt == "Patient" else state
        )
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.db.get_value = Mock(return_value="old-name")
        self.assertEqual(self.state.lock_visit("A"), (appointment, state))
        self.frappe.db.get_value.assert_called_once_with(
            self.state.VISIT, {"appointment": "A"}, "name", for_update=True
        )

    def test_renamed_patient_does_not_inherit_state(self):
        appointment = types.SimpleNamespace(name="A", patient="P2", creation="old")
        state = types.SimpleNamespace(name="A", appointment="A", patient="P2",
                                      appointment_snapshot="A", patient_snapshot="P1")
        self.frappe.get_doc = lambda dt, name, **kw: (
            appointment if dt == "Patient Appointment" else
            types.SimpleNamespace(name="P2", creation="old") if dt == "Patient" else state
        )
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.db.get_value = Mock(return_value="A")
        with self.assertRaises(ValueError):
            self.state.lock_visit("A")

    def test_renamed_appointment_does_not_transfer_state(self):
        appointment = types.SimpleNamespace(name="B", patient="P", creation="old")
        state = types.SimpleNamespace(name="A", appointment="B", patient="P",
                                      appointment_snapshot="A", patient_snapshot="P")
        self.frappe.get_doc = lambda dt, name, **kw: (
            appointment if dt == "Patient Appointment" else
            types.SimpleNamespace(name="P", creation="old") if dt == "Patient" else state
        )
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.db.get_value = Mock(return_value="A")
        with self.assertRaises(ValueError):
            self.state.lock_visit("B")

    def test_recreated_same_name_appointment_or_patient_cannot_inherit_state(self):
        for new_appointment, new_patient in ((True, False), (False, True)):
            appointment = types.SimpleNamespace(
                name="A", patient="P", creation="2026-02" if new_appointment else "2026-01"
            )
            patient = types.SimpleNamespace(
                name="P", creation="2026-02" if new_patient else "2026-01"
            )
            state = types.SimpleNamespace(
                name="A", appointment="A", patient="P",
                appointment_snapshot="A", patient_snapshot="P",
                appointment_creation="2026-01", patient_creation="2026-01",
            )
            self.frappe.get_doc = lambda dt, name, **kw: {
                "Patient Appointment": appointment, "Patient": patient,
                self.state.VISIT: state,
            }[dt]
            self.frappe.has_permission = lambda *a, **kw: True
            self.frappe.db.get_value = Mock(return_value="A")
            with self.subTest(appointment=new_appointment), self.assertRaises(ValueError):
                self.state.lock_visit("A")

    def test_recreated_same_name_cannot_inherit_witness(self):
        for new_appointment in (True, False):
            self.frappe.get_doc = lambda dt, name, **kw: (
                types.SimpleNamespace(name="A", patient="P",
                                      creation="new" if new_appointment else "old")
                if dt == "Patient Appointment" else
                types.SimpleNamespace(name="P", creation="old" if new_appointment else "new")
            )
            self.frappe.has_permission = lambda *a, **kw: True
            self.frappe.db.get_value = Mock(return_value=types.SimpleNamespace(
                patient="P", appointment_snapshot="A", patient_snapshot="P",
                appointment_creation="old", patient_creation="old",
            ))
            with self.subTest(appointment=new_appointment), self.assertRaises(ValueError):
                self.state.has_checkin_witness("A", "P")

    def test_witness_lookup_uses_locking_read_and_checks_snapshot(self):
        self.frappe.db.get_value = Mock(return_value=types.SimpleNamespace(
            patient="P", appointment_snapshot="A", patient_snapshot="P",
            appointment_creation="old", patient_creation="old",
        ))
        self.assertTrue(self.state.has_checkin_witness("A", "P"))
        self.assertTrue(self.frappe.db.get_value.call_args.kwargs["for_update"])
        self.assertEqual(self.frappe.db.get_value.call_args.args[1], {"appointment": "A"})

    def test_renamed_witness_is_rejected(self):
        self.frappe.get_doc = lambda dt, name, **kw: (
            types.SimpleNamespace(name="A", patient="P2", creation="old")
            if dt == "Patient Appointment" else types.SimpleNamespace(name="P2", creation="old")
        )
        self.frappe.db.get_value = Mock(return_value=types.SimpleNamespace(
            patient="P2", appointment_snapshot="A", patient_snapshot="P1"
        ))
        with self.assertRaises(ValueError):
            self.state.has_checkin_witness("A", "P2")

    def test_consent_rejects_sample_evidence(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        self.frappe.get_doc = lambda *a, **kw: doc
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.db = types.SimpleNamespace(
            get_value=lambda doctype, *a, **kw: 1 if doctype == "User" else None,
            get_all=lambda *a, **kw: ["Physician"],
        )
        self.frappe.get_roles = lambda *a: ["Physician"]
        for reference in (
            "sample consent", "SAMPLE-CONSENT-1041",
            "demo/consent/SAMPLE-CONSENT-1041.pdf",
            "PC-FORM-2.1/1041", "PC-FORM-2.0/1043",
            "PC-FORM-2.1/1044", "PC-FORM-2.1/1048",
            "PC-FORM-2.1/GUARDIAN-01",
        ):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.state.change_consent("A", True, reference, attested=True)

    def test_grant_requires_explicit_boolean_attestation(self):
        with self.assertRaises(ValueError):
            self.state.change_consent("A", True, "signed-form/42")
        for attested in (False, None, 1, "true"):
            with self.subTest(attested=attested), self.assertRaises(ValueError):
                self.state.change_consent("A", True, "signed-form/42", attested=attested)

    def test_grant_rejects_invalid_references(self):
        for reference in ("", "  ", "...", "x" * 141, "signed\nform"):
            with self.subTest(reference=reference), self.assertRaises(ValueError):
                self.state.change_consent("A", True, reference, attested=True)

    def test_requires_appointment_read_as_well_as_write(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        self.frappe.get_doc = lambda *a, **kw: doc
        self.frappe.has_permission = lambda doctype, ptype, **kw: not (
            doctype == "Patient Appointment" and ptype == "read"
        )
        with self.assertRaises(PermissionError):
            self.state.lock_visit("A")

    def test_grant_and_revoke_append_server_owned_audit(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(appointment="A", patient="P", consent_granted=0)
        self.frappe.get_roles = lambda user: ["Physician"]
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert", return_value=types.SimpleNamespace(name="G1")) as insert, \
             patch.object(self.state.bookkeeping, "save") as save:
            self.state.change_consent("A", True, "signed-form/42", attested=True)
            self.assertEqual(visit.consent_granted, 1)
            self.state.change_consent("A", False)
            self.assertEqual(visit.consent_granted, 0)
        events = [call.args[0] for call in insert.call_args_list]
        self.assertEqual([e["action"] for e in events], ["Granted", "Revoked"])
        self.assertEqual([e["actor"] for e in events], ["staff@example.test"] * 2)
        self.assertEqual([(e["appointment"], e["patient"]) for e in events],
                         [("A", "P"), ("A", "P")])
        self.assertEqual(events[0]["evidence"], "signed-form/42")
        self.assertEqual([e["attested"] for e in events], [1, 0])
        self.assertEqual(save.call_count, 2)

    def test_active_grant_tracks_exact_audit_and_changes_after_regrant(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(appointment="A", patient="P", consent_granted=0)
        audits = [types.SimpleNamespace(name=name) for name in ("G1", "R1", "G2")]
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert", side_effect=audits), \
             patch.object(self.state.bookkeeping, "save"):
            self.state.change_consent("A", True, "signed-form/42", attested=True)
            self.assertEqual(visit.active_grant_audit, "G1")
            self.state.change_consent("A", False)
            self.assertIsNone(visit.active_grant_audit)
            self.state.change_consent("A", True, "signed-form/43", attested=True)
            self.assertEqual(visit.active_grant_audit, "G2")

    def test_grant_cannot_activate_without_persisted_audit_identity(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(appointment="A", patient="P", consent_granted=0)
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert", return_value=types.SimpleNamespace(name="")), \
             patch.object(self.state.bookkeeping, "save") as save:
            with self.assertRaises(ValueError):
                self.state.change_consent("A", True, "signed-form/42", attested=True)
        self.assertEqual(visit.consent_granted, 0)
        save.assert_not_called()

    def test_consent_audit_failure_does_not_change_existing_state(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(appointment="A", patient="P", consent_granted=1)
        self.frappe.get_roles = lambda user: ["Physician"]
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert", side_effect=RuntimeError("audit")), \
             patch.object(self.state.bookkeeping, "save") as save:
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.state.change_consent("A", False)
        self.assertEqual(visit.consent_granted, 1)
        save.assert_not_called()

    def test_actor_without_allowed_native_role_cannot_revoke(self):
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(appointment="A", patient="P", consent_granted=1)
        self.frappe.get_roles = lambda user: ["Nursing User"]
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert"), \
             patch.object(self.state.bookkeeping, "save"):
            with self.assertRaises(PermissionError):
                self.state.change_consent("A", False)

    def test_consent_denies_disabled_user_even_if_cached_role_permits(self):
        self.frappe.db.get_value = Mock(return_value=0)
        self.frappe.db.sql = Mock(return_value=[("Physician",)])
        doc = types.SimpleNamespace(name="A", patient="P")
        visit = types.SimpleNamespace(consent_granted=1)
        with patch.object(self.state, "lock_visit", return_value=(doc, visit)), \
             patch.object(self.state.bookkeeping, "insert") as insert, \
             patch.object(self.state.bookkeeping, "save"):
            with self.assertRaises(PermissionError):
                self.state.change_consent("A", False)
            insert.assert_not_called()

    def test_consent_denies_removed_role_even_if_session_cache_stale(self):
        self.frappe.db.sql = Mock(return_value=[])
        doc = types.SimpleNamespace(name="A", patient="P")
        with patch.object(self.state, "lock_visit", return_value=(doc, None)) as lock, \
             patch.object(self.state.bookkeeping, "insert") as insert:
            with self.assertRaises(PermissionError):
                self.state.change_consent("A", False)
            lock.assert_not_called()
            insert.assert_not_called()

    def test_audit_failure_rolls_back_new_state_to_savepoint(self):
        doc = types.SimpleNamespace(name="A", patient="P", creation="old")
        rollback = unittest.mock.Mock()
        self.frappe.db.rollback = rollback
        with patch.object(self.state, "lock_visit", return_value=(doc, None)), \
             patch.object(self.state.bookkeeping, "insert", side_effect=[object(), RuntimeError("audit")]):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.state.change_consent("A", False)
        rollback.assert_called_once_with(save_point="clinic_visit_consent")

    def test_new_state_records_current_native_creation_values(self):
        doc = types.SimpleNamespace(name="A", patient="P", creation="appointment-created")
        patient = types.SimpleNamespace(name="P", creation="patient-created")
        self.frappe.get_doc = Mock(return_value=patient)
        visit = types.SimpleNamespace(consent_granted=0)
        with patch.object(self.state, "lock_visit", return_value=(doc, None)), \
             patch.object(self.state.bookkeeping, "insert", side_effect=[visit, types.SimpleNamespace(name="G1")]) as insert, \
             patch.object(self.state.bookkeeping, "save"):
            self.state.change_consent("A", True, "signed-form/42", attested=True)
        self.assertEqual(insert.call_args_list[0].args[0]["appointment_creation"], "appointment-created")
        self.assertEqual(insert.call_args_list[0].args[0]["patient_creation"], "patient-created")
        self.frappe.get_doc.assert_called_once_with("Patient", "P", for_update=True)


class CheckInTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.local = types.SimpleNamespace(site="site.test")
        self.frappe.conf = {"clinic_visit_photos_enabled": True,
                            "clinic_visit_photos_site": "site.test"}
        self.frappe.whitelist = lambda *a, **kw: lambda fn: fn
        self.frappe.ValidationError = ValueError
        self.frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        sys.modules.pop("clinic_photos.visit.checkin", None)
        self.checkin = importlib.import_module("clinic_photos.visit.checkin")

    def test_successful_fresh_transition_records_witness_once(self):
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = lambda *a, **kw: result
        with patch.object(self.checkin, "_native", return_value=result) as native, \
             patch.object(self.checkin, "_record_witness") as record:
            self.assertIs(self.checkin.check_in_appointment("A", "D", "S"), result)
        native.assert_called_once_with("A", "D", "S")
        record.assert_called_once_with("A", "P")

    def test_disabled_or_wrong_site_is_native_only(self):
        for config in ({}, {"clinic_visit_photos_enabled": True,
                            "clinic_visit_photos_site": "other.test"},
                       {"clinic_visit_photos_enabled": 1,
                        "clinic_visit_photos_site": "site.test"}):
            self.frappe.conf = config
            result = object()
            self.frappe.get_doc = Mock(side_effect=AssertionError("unexpected read"))
            with self.subTest(config=config), \
                 patch.object(self.checkin, "_native", return_value=result) as native, \
                 patch.object(self.checkin, "_record_witness") as record:
                self.assertIs(self.checkin.check_in_appointment("A", "D", "S"), result)
                native.assert_called_once_with("A", "D", "S")
                record.assert_not_called()
                self.frappe.get_doc.assert_not_called()

    def test_canonical_native_name_used_for_post_read(self):
        before = types.SimpleNamespace(name="APPT-A", patient="P", status="Open")
        result = types.SimpleNamespace(name="APPT-A", patient="P", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = Mock(return_value=result)
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin, "_record_witness") as record:
            self.assertIs(self.checkin.check_in_appointment("appt-a"), result)
        self.frappe.get_doc.assert_called_once_with("Patient Appointment", "APPT-A", for_update=True)
        record.assert_called_once_with("APPT-A", "P")

    def test_existing_witness_lookup_is_locking_and_link_scoped(self):
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None))
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.get_doc = lambda dt, name, **kw: (
            types.SimpleNamespace(name="A", patient="P", creation="old")
            if dt == "Patient Appointment" else types.SimpleNamespace(name="P", creation="old")
        )
        with patch.object(self.checkin.bookkeeping, "insert"):
            self.checkin._record_witness("A", "P")
        self.assertTrue(self.frappe.db.get_value.call_args.kwargs["for_update"])
        self.assertEqual(self.frappe.db.get_value.call_args.args[1], {"appointment": "A"})

    def test_witness_records_locked_native_creation_values(self):
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None))
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.has_permission = lambda *a, **kw: True
        self.frappe.get_doc = Mock(side_effect=lambda dt, name, **kw: (
            types.SimpleNamespace(name="A", patient="P", creation="appointment-created")
            if dt == "Patient Appointment" else types.SimpleNamespace(name="P", creation="patient-created")
        ))
        with patch.object(self.checkin.bookkeeping, "insert") as insert:
            self.checkin._record_witness("A", "P")
        self.assertEqual(insert.call_args.args[0]["appointment_creation"], "appointment-created")
        self.assertEqual(insert.call_args.args[0]["patient_creation"], "patient-created")
        self.assertEqual([call.kwargs for call in self.frappe.get_doc.call_args_list],
                         [{"for_update": True}, {"for_update": True}])

    def test_stale_existing_witness_does_not_block_native_checkin(self):
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.has_permission = lambda *a, **kw: True
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", creation="new", status="Checked In",
                                       get_doc_before_save=lambda: before)
        patient = types.SimpleNamespace(name="P", creation="new")
        self.frappe.get_doc = lambda dt, *a, **kw: patient if dt == "Patient" else result
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=types.SimpleNamespace(
            patient="P", appointment_snapshot="A", patient_snapshot="P",
            appointment_creation="old", patient_creation="old",
        )))
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin.bookkeeping, "insert") as insert:
            self.assertIs(self.checkin.check_in_appointment("A"), result)
        insert.assert_not_called()

    def test_new_witness_insert_failure_propagates_from_native_checkin(self):
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.has_permission = lambda *a, **kw: True
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", creation="new", status="Checked In",
                                       get_doc_before_save=lambda: before)
        patient = types.SimpleNamespace(name="P", creation="new")
        self.frappe.get_doc = lambda dt, *a, **kw: patient if dt == "Patient" else result
        self.frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None))
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin.bookkeeping, "insert", side_effect=RuntimeError("insert failed")):
            with self.assertRaisesRegex(RuntimeError, "insert failed"):
                self.checkin.check_in_appointment("A")

    def test_replay_does_not_create_witness(self):
        before = types.SimpleNamespace(name="A", patient="P", status="Checked In")
        result = types.SimpleNamespace(name="A", patient="P", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = lambda *a, **kw: result
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin, "_record_witness") as record:
            self.checkin.check_in_appointment("A")
        record.assert_not_called()

    def test_patient_disagreement_fails_without_witness(self):
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = lambda *a, **kw: types.SimpleNamespace(
            name="A", patient="other", status="Checked In"
        )
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin, "_record_witness") as record:
            with self.assertRaises(ValueError):
                self.checkin.check_in_appointment("A")
        record.assert_not_called()

    def test_native_failure_does_not_create_witness(self):
        with patch.object(self.checkin, "_native", side_effect=RuntimeError("native")), \
             patch.object(self.checkin, "_record_witness") as record:
            with self.assertRaisesRegex(RuntimeError, "native"):
                self.checkin.check_in_appointment("A")
        record.assert_not_called()
    def test_witness_failure_propagates(self):
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = lambda *a, **kw: result
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin, "_record_witness", side_effect=RuntimeError("audit")):
            with self.assertRaisesRegex(RuntimeError, "audit"):
                self.checkin.check_in_appointment("A")

    def test_closed_before_save_preserves_native_result_without_witness(self):
        for status in ("Closed", "Cancelled", "Checked Out", "Checked In", ""):
            before = types.SimpleNamespace(name="A", patient="P", status=status)
            result = types.SimpleNamespace(name="A", patient="P", status="Checked In",
                                           get_doc_before_save=lambda: before)
            self.frappe.get_doc = lambda *a, **kw: result
            with self.subTest(status=status), \
                 patch.object(self.checkin, "_native", return_value=result), \
                 patch.object(self.checkin, "_record_witness") as record:
                self.assertIs(self.checkin.check_in_appointment("A"), result)
                record.assert_not_called()

    def test_native_checkin_without_photo_read_permission_creates_no_witness(self):
        self.frappe.db = types.SimpleNamespace(get_value=Mock())
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.PermissionError = PermissionError
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", creation="old", status="Checked In",
                                       get_doc_before_save=lambda: before)
        self.frappe.get_doc = lambda *a, **kw: result
        for denied in ("Patient Appointment", "Patient"):
            self.frappe.has_permission = lambda dt, *a, **kw: dt != denied
            with self.subTest(denied=denied), \
                 patch.object(self.checkin, "_native", return_value=result), \
                 patch.object(self.checkin.bookkeeping, "insert") as insert:
                self.assertIs(self.checkin.check_in_appointment("A"), result)
                insert.assert_not_called()
                self.frappe.db.get_value.assert_not_called()

    def test_native_checkin_survives_patient_document_read_denial(self):
        self.frappe.db = types.SimpleNamespace(get_value=Mock())
        self.frappe.session = types.SimpleNamespace(user="staff")
        self.frappe.PermissionError = PermissionError
        before = types.SimpleNamespace(name="A", patient="P", status="Open")
        result = types.SimpleNamespace(name="A", patient="P", creation="old", status="Checked In",
                                       get_doc_before_save=lambda: before)
        patient = types.SimpleNamespace(name="P", creation="old")
        self.frappe.get_doc = lambda dt, *a, **kw: patient if dt == "Patient" else result
        self.frappe.has_permission = lambda dt, *a, **kw: not (
            dt == "Patient" and kw.get("doc") is patient
        )
        with patch.object(self.checkin, "_native", return_value=result), \
             patch.object(self.checkin.bookkeeping, "insert") as insert:
            self.assertIs(self.checkin.check_in_appointment("A"), result)
        insert.assert_not_called()
        self.frappe.db.get_value.assert_not_called()


class BookkeepingSchemaTests(unittest.TestCase):
    def test_no_direct_doctype_permissions(self):
        root = Path(__file__).resolve().parents[2] / "clinic_photos/clinic_photos/doctype"
        for name in ("clinic_visit_photo_state", "clinic_visit_photo_consent_audit",
                     "clinic_visit_checkin_witness"):
            with self.subTest(name=name):
                schema = json.loads((root / name / f"{name}.json").read_text())
                self.assertEqual(schema["permissions"], [])
                self.assertEqual(schema["allow_rename"], 0)

    def test_audit_identifiers_are_data_snapshots(self):
        root = Path(__file__).resolve().parents[2] / "clinic_photos/clinic_photos/doctype"
        path = root / "clinic_visit_photo_consent_audit/clinic_visit_photo_consent_audit.json"
        fields = {f["fieldname"]: f["fieldtype"] for f in json.loads(path.read_text())["fields"]}
        self.assertEqual([fields[f] for f in ("appointment", "patient", "actor")],
                         ["Data", "Data", "Data"])

    def test_witness_actor_is_data_snapshot(self):
        root = Path(__file__).resolve().parents[2] / "clinic_photos/clinic_photos/doctype"
        path = root / "clinic_visit_checkin_witness/clinic_visit_checkin_witness.json"
        fields = {f["fieldname"]: f["fieldtype"] for f in json.loads(path.read_text())["fields"]}
        self.assertEqual(fields["actor"], "Data")


class BookkeepingBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.frappe = types.ModuleType("frappe")
        self.frappe.PermissionError = PermissionError
        self.frappe.throw = lambda message, exc=ValueError: (_ for _ in ()).throw(exc(message))
        self.modules = patch.dict(sys.modules, {"frappe": self.frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        from clinic_photos.visit import bookkeeping
        self.bookkeeping = bookkeeping

    def test_rejects_non_bookkeeping_insert(self):
        with self.assertRaises(PermissionError):
            self.bookkeeping.insert({"doctype": "Patient"})

    def test_save_rejects_immutable_audit(self):
        doc = types.SimpleNamespace(doctype="Clinic Visit Photo Consent Audit")
        with self.assertRaises(PermissionError):
            self.bookkeeping.save(doc)

    def test_scope_is_exact_object_and_ignore_permission_restored(self):
        other = types.SimpleNamespace(doctype="Clinic Visit Photo State")
        doc = types.SimpleNamespace(doctype="Clinic Visit Photo State",
                                    flags=types.SimpleNamespace(ignore_permissions=False))
        def save(**kwargs):
            doc.flags.ignore_permissions = True
            self.bookkeeping.require_internal_write(doc)
            with self.assertRaises(PermissionError):
                self.bookkeeping.require_internal_write(other)
            return doc
        doc.save = save
        self.assertIs(self.bookkeeping.save(doc), doc)
        self.assertFalse(doc.flags.ignore_permissions)
        with self.assertRaises(PermissionError):
            self.bookkeeping.require_internal_write(doc)

    def test_restores_permission_flag_and_scope_when_save_raises(self):
        doc = types.SimpleNamespace(doctype="Clinic Visit Photo State",
                                    flags=types.SimpleNamespace(ignore_permissions=False))
        def save(**kwargs):
            doc.flags.ignore_permissions = True
            raise RuntimeError("save failed")
        doc.save = save
        with self.assertRaisesRegex(RuntimeError, "save failed"):
            self.bookkeeping.save(doc)
        self.assertFalse(doc.flags.ignore_permissions)
        with self.assertRaises(PermissionError):
            self.bookkeeping.require_internal_write(doc)


if __name__ == "__main__":
    unittest.main()
