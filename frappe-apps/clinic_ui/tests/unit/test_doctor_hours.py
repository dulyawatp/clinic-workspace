"""Signed-in doctor's recurring hours; no site or writes."""
import types
import unittest
from datetime import time, timedelta
from unittest import mock


class Denied(Exception):
    pass


def doc(**values):
    return types.SimpleNamespace(check_permission=mock.Mock(), **values)


class DoctorHoursTests(unittest.TestCase):
    def setUp(self):
        from clinic_ui.endpoints import doctor_hours
        self.endpoint = doctor_hours
        self.gateway = mock.Mock()
        self.gateway.deny.side_effect = Denied
        self.gateway.frappe.PermissionError = Denied
        request = self.gateway.frappe.local.request
        request.headers = {"X-Clinic-Workspace": "DOC", "X-Clinic-Session-Epoch": "epoch"}
        self.view = types.SimpleNamespace(user="doctor@example.test", sid="sid")
        self.auth = mock.patch.object(doctor_hours.production_read, "authorize_if_selected",
                                      return_value=(None, self.view, "DOC"))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.fields = mock.patch.object(doctor_hours, "_check_fields")
        self.fields.start()
        self.addCleanup(self.fields.stop)
        self.unit_fields = mock.patch.object(doctor_hours, "_check_native_fields")
        self.unit_fields.start()
        self.addCleanup(self.unit_fields.stop)
        self.practitioner = doc(name="own", user_id=self.view.user, status="Active",
                                practitioner_schedules=[doc(schedule="schedule", service_unit="unit")])
        self.schedule = doc(name="schedule", disabled=0, time_slots=[
            doc(day="Monday", from_time="09:00:00", to_time="10:00:00")])
        self.unit = doc(name="unit")
        self.gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="own")]
        self.gateway.frappe.get_doc.side_effect = lambda doctype, name: {
            ("Healthcare Practitioner", "own"): self.practitioner,
            ("Practitioner Schedule", "schedule"): self.schedule,
            ("Healthcare Service Unit", "unit"): self.unit,
        }[(doctype, name)]

    def test_own_enabled_schedule_only_and_no_writes(self):
        result = self.endpoint.get(self.gateway)
        self.assertEqual(result, {"state": "ready", "schedules": [{
            "slots": [{"day": "Monday", "from_time": "09:00:00", "to_time": "10:00:00"}],
            "serviceUnit": "unit"}]})
        self.gateway.frappe.get_list.assert_called_once_with(
            "Healthcare Practitioner", filters={"user_id": self.view.user, "status": "Active"},
            fields=["name"], limit_page_length=2)
        self.gateway.frappe.get_all.assert_not_called()
        self.gateway.frappe.db.set_value.assert_not_called()
        self.gateway.frappe.new_doc.assert_not_called()

    def test_empty_only_when_readable_practitioner_has_no_enabled_schedules(self):
        self.practitioner.practitioner_schedules = []
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.practitioner.practitioner_schedules = [doc(schedule="schedule", service_unit=None)]
        self.schedule.disabled = 1
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})

    def test_ambiguous_or_unreadable_is_unavailable(self):
        for rows in ([], [doc(name="own"), doc(name="other")]):
            self.gateway.frappe.get_list.return_value = rows
            self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.gateway.frappe.get_list.return_value = [doc(name="own")]
        self.schedule.check_permission.side_effect = Denied
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_unreadable_unit_is_omitted(self):
        self.unit.check_permission.side_effect = Denied
        result = self.endpoint.get(self.gateway)
        self.assertEqual(result["state"], "ready")
        self.assertNotIn("serviceUnit", result["schedules"][0])

    def test_missing_explicit_workspace_or_epoch_is_denied(self):
        headers = self.gateway.frappe.local.request.headers
        for key, value in (("X-Clinic-Workspace", "MGR"), ("X-Clinic-Session-Epoch", "")):
            old = headers[key]
            headers[key] = value
            with self.assertRaises(Denied):
                self.endpoint.get(self.gateway)
            headers[key] = old

    def test_authorization_failure_is_not_translated_to_empty(self):
        with mock.patch.object(self.endpoint.production_read, "authorize_if_selected",
                               side_effect=Denied):
            with self.assertRaises(Denied):
                self.endpoint.get(self.gateway)
        self.gateway.frappe.get_list.assert_not_called()

    def test_unreadable_child_field_is_unavailable_not_empty(self):
        with mock.patch.object(self.endpoint, "_check_fields", side_effect=Denied):
            self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.gateway.frappe.get_list.assert_not_called()

    def test_native_times_are_zero_padded_and_preserve_seconds(self):
        self.schedule.time_slots[0].from_time = timedelta(hours=9, seconds=7)
        self.schedule.time_slots[0].to_time = time(10, 2, 3)
        slot = self.endpoint.get(self.gateway)["schedules"][0]["slots"][0]
        self.assertEqual(slot["from_time"], "09:00:07")
        self.assertEqual(slot["to_time"], "10:02:03")

    def test_invalid_native_time_is_unavailable(self):
        for value in (timedelta(days=1), timedelta(seconds=-1),
                      timedelta(microseconds=1), time(9, microsecond=1),
                      "25:00:00", "09:00:00.1"):
            with self.subTest(value=value):
                self.schedule.time_slots[0].from_time = value
                self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_no_slots_is_empty_and_empty_schedule_is_omitted(self):
        self.schedule.time_slots = []
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.practitioner.practitioner_schedules.append(doc(schedule="other", service_unit=None))
        original = self.gateway.frappe.get_doc.side_effect
        other = doc(name="other", disabled=0, time_slots=[
            doc(day="Tuesday", from_time="11:00", to_time="12:00")])
        self.gateway.frappe.get_doc.side_effect = lambda doctype, name: (
            other if (doctype, name) == ("Practitioner Schedule", "other")
            else original(doctype, name))
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "ready", "schedules": [{
            "slots": [{"day": "Tuesday", "from_time": "11:00", "to_time": "12:00"}]}]})

    def test_translated_exceptions_strip_native_messages_but_gate_denial_does_not(self):
        self.schedule.check_permission.side_effect = Denied
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.gateway.strip_message_channels.assert_called_once_with()
        self.gateway.strip_message_channels.reset_mock()
        self.schedule.check_permission.side_effect = None
        self.unit.check_permission.side_effect = Denied
        self.endpoint.get(self.gateway)
        self.gateway.strip_message_channels.assert_called_once_with()
        self.gateway.strip_message_channels.reset_mock()
        with mock.patch.object(self.endpoint.production_read, "authorize_if_selected",
                               side_effect=Denied):
            with self.assertRaises(Denied):
                self.endpoint.get(self.gateway)
        self.gateway.strip_message_channels.assert_not_called()

    def test_schedule_field_authority_is_checked_only_when_needed(self):
        self.fields.stop()
        def check(frappe, doctype, fields, access, parent=None):
            if doctype in ("Practitioner Schedule", "Healthcare Schedule Time Slot"):
                raise Denied()
        self.endpoint._check_native_fields.side_effect = check
        self.practitioner.practitioner_schedules = []
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.practitioner.practitioner_schedules = [doc(schedule="schedule", service_unit=None)]
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_disabled_schedule_does_not_require_slot_field_authority(self):
        self.fields.stop()
        def check(frappe, doctype, fields, access, parent=None):
            if doctype == "Healthcare Schedule Time Slot":
                raise Denied()
        self.endpoint._check_native_fields.side_effect = check
        self.schedule.disabled = 1
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.schedule.disabled = 0
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_real_gate_rejects_stale_epoch_and_mismatched_sid(self):
        from clinic_ui.core.epoch import session_epoch
        from clinic_ui.core import production_enrollment
        from .fake_gateway import Denied as GatewayDenied, SECRET, session_view
        from .test_production_read_adapter import gateway

        self.auth.stop()
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(user="doctor@example.test", roles=("Physician",))
        fake.frappe.local.request.cookies = {"sid": fake.current_session.sid}
        headers = fake.frappe.local.request.headers
        headers.update({"X-Clinic-Workspace": "DOC", "X-Clinic-Session-Epoch": "stale"})
        with mock.patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            with self.assertRaises(GatewayDenied):
                self.endpoint.get(fake)
            headers["X-Clinic-Session-Epoch"] = session_epoch(
                SECRET.encode(), fake.site, fake.current_session.sid)
            fake.frappe.local.request.cookies["sid"] = "another-session"
            with self.assertRaises(GatewayDenied):
                self.endpoint.get(fake)
            fake.frappe.local.request.cookies["sid"] = fake.current_session.sid
            fake.frappe.get_list = mock.Mock(return_value=[])
            self.assertEqual(self.endpoint.get(fake), {"state": "unavailable"})
            fake.frappe.get_list.assert_called_once()
