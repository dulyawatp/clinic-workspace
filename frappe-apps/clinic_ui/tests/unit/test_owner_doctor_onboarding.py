"""Local behavioral checks; no Frappe site or mail transport is used."""

import types
import unittest
from unittest import mock

from clinic_ui.endpoints import owner_doctor_finish, owner_doctor_invitation, owner_doctor_profile


class PermissionDenied(Exception):
    pass


class OwnerDoctorOnboardingTests(unittest.TestCase):
    def gateway(self):
        gateway = mock.Mock()
        gateway.site = "test.local"
        gateway.setting.return_value = None
        gateway.deny.side_effect = PermissionDenied
        gateway.respond_write.side_effect = lambda status, body: (status, body)
        gateway.frappe.PermissionError = PermissionDenied
        gateway.frappe.DoesNotExistError = LookupError
        return gateway

    def test_all_onboarding_gates_are_default_off(self):
        for endpoint in (owner_doctor_profile, owner_doctor_finish, owner_doctor_invitation):
            with self.subTest(endpoint=endpoint.__name__):
                gateway = self.gateway()
                with mock.patch.object(endpoint.production_read, "authorize_if_selected") as authorize:
                    with self.assertRaises(PermissionDenied):
                        endpoint._gate(gateway)
                    authorize.assert_not_called()

    def test_finish_rejects_changed_slot_digest_before_writes(self):
        gateway = self.gateway()
        doc = mock.Mock(name="practitioner", modified="p1")
        schedule = mock.Mock(name="schedule", modified="s1")
        schedule.name = "schedule-1"
        unit = types.SimpleNamespace(name="unit-1")
        slots = [{"day": "Monday", "from_time": "09:00", "to_time": "10:00"}]
        body = {
            "practitioner": "doctor-1", "practitionerVersion": "p1",
            "schedule": "schedule-1", "scheduleVersion": "s1",
            "email": "doctor@example.test", "slotDigest": "0" * 64,
        }
        gateway.owner_doctor_finish_email_valid.return_value = True
        gateway.owner_doctor_finish_native_user_supported.return_value = True
        with mock.patch.object(owner_doctor_finish, "_gate", return_value=("company", "Doctor", "owner")), \
                mock.patch.object(owner_doctor_finish, "_draft", return_value=(doc, schedule, unit, slots)) as draft:
            result = owner_doctor_finish.finish(gateway, body, "csrf")

        self.assertEqual(result, (409, {"code": "STALE_VERSION"}))
        draft.assert_called_once_with(gateway.frappe, "doctor-1", "company", lock=True)
        gateway.rollback.assert_called_once_with()
        gateway.commit.assert_not_called()
        gateway.frappe.db.exists.assert_not_called()
        gateway.frappe.get_doc.assert_not_called()
        gateway.frappe.new_doc.assert_not_called()
        gateway.owner_doctor_finish_insert_user.assert_not_called()
        doc.save.assert_not_called()

    def test_existing_invitation_marker_without_queue_is_unknown_and_not_replayed(self):
        gateway = self.gateway()
        user = mock.Mock(name="user")
        user.name = user.email = "doctor@example.test"
        marker = mock.Mock()
        marker.reference_doctype = "User"
        marker.reference_name = user.name
        marker.subject = "Clinic UI doctor invitation"
        marker.content = ""
        gateway.frappe.db.exists.return_value = True
        gateway.frappe.get_doc.return_value = marker
        gateway.frappe.get_all.return_value = []
        body = {"practitioner": "doctor-1", "userId": user.name}
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"), \
                mock.patch.object(owner_doctor_invitation, "_target", return_value=user):
            result = owner_doctor_invitation.send(gateway, body, "csrf")

        self.assertEqual(result, {"status": "unknown"})
        marker_name = owner_doctor_invitation._marker_name(user.name)
        gateway.frappe.get_doc.assert_called_once_with("Communication", marker_name, for_update=True)
        gateway.rollback.assert_called_once_with()
        gateway.commit.assert_not_called()
        marker.insert.assert_not_called()
        user._reset_password.assert_not_called()
        gateway.frappe.sendmail.assert_not_called()
        gateway.owner_doctor_invitation_enqueue.assert_not_called()
        gateway.owner_doctor_invitation_retry.assert_not_called()

    def test_invitation_status_resolves_only_eligible_exact_user(self):
        gateway = self.gateway()
        user_id = "doctor@example.test"
        user = types.SimpleNamespace(name=user_id, email=user_id, enabled=1,
                                     user_type="System User", roles=[types.SimpleNamespace(role="Doctor")],
                                     check_permission=mock.Mock())
        doctor = types.SimpleNamespace(user_id=user_id, status="Active", check_permission=mock.Mock())
        gateway._fields_readable.return_value = True
        gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="doctor-1")]
        gateway.frappe.get_doc.side_effect = lambda doctype, name, **kwargs: {
            "User": user, "Healthcare Practitioner": doctor,
        }[doctype]
        gateway.frappe.db.count.return_value = 1
        gateway.frappe.db.exists.return_value = False
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"):
            result = owner_doctor_invitation.status(gateway, None, user_id)
        self.assertEqual(result, {"status": "not_queued", "practitioner": "doctor-1"})
        gateway.frappe.get_list.assert_called_once_with(
            "Healthcare Practitioner", filters={"user_id": user_id, "status": "Active"},
            fields=["name"], limit_page_length=2)
        user.check_permission.assert_has_calls([mock.call("read"), mock.call("write")])
        doctor.check_permission.assert_called_once_with("read")

    def test_invitation_status_resolution_fails_closed(self):
        for rows, target_error, expected in (
            ([], None, "unavailable"),
            ([types.SimpleNamespace(name="one"), types.SimpleNamespace(name="two")], None, "unavailable"),
            ([types.SimpleNamespace(name="one")], ValueError("Unsupported target"), "unavailable"),
            ([types.SimpleNamespace(name="one")], RuntimeError("database unavailable"), "unknown"),
        ):
            with self.subTest(expected=expected, rows=len(rows)):
                gateway = self.gateway()
                gateway.frappe.get_list.return_value = rows
                with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"), \
                        mock.patch.object(owner_doctor_invitation, "_target", side_effect=target_error):
                    result = owner_doctor_invitation.status(gateway, None, "doctor@example.test")
                self.assertEqual(result, {"status": expected})
                gateway.owner_doctor_invitation_enqueue.assert_not_called()

    def test_invitation_resolution_checks_native_target_guards(self):
        user_id = "doctor@example.test"
        for field, value in (("enabled", 0), ("user_type", "Website User"),
                             ("roles", [types.SimpleNamespace(role="Other")]),
                             ("email", "other@example.test")):
            with self.subTest(field=field):
                gateway = self.gateway()
                user = types.SimpleNamespace(name=user_id, email=user_id, enabled=1,
                                             user_type="System User", roles=[types.SimpleNamespace(role="Doctor")],
                                             check_permission=mock.Mock())
                setattr(user, field, value)
                doctor = types.SimpleNamespace(user_id=user_id, status="Active", check_permission=mock.Mock())
                gateway._fields_readable.return_value = True
                gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="doctor-1")]
                gateway.frappe.get_doc.side_effect = lambda doctype, name, **kwargs: {
                    "User": user, "Healthcare Practitioner": doctor,
                }[doctype]
                gateway.frappe.db.count.return_value = 1
                with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"):
                    self.assertEqual(owner_doctor_invitation.status(gateway, None, user_id),
                                     {"status": "unavailable"})

    def test_invitation_resolution_permission_and_lookup_failures(self):
        gateway = self.gateway()
        gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="doctor-1")]
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"), \
                mock.patch.object(owner_doctor_invitation, "_target", side_effect=PermissionDenied):
            self.assertEqual(owner_doctor_invitation.status(gateway, None, "doctor@example.test"),
                             {"status": "unavailable"})
        gateway.frappe.get_list.side_effect = RuntimeError("database unavailable")
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"):
            self.assertEqual(owner_doctor_invitation.status(gateway, None, "doctor@example.test"),
                             {"status": "unknown"})

    def test_invitation_resolution_rejects_duplicate_linkage(self):
        gateway = self.gateway()
        user_id = "doctor@example.test"
        user = types.SimpleNamespace(name=user_id, email=user_id, enabled=1,
                                     user_type="System User", roles=[types.SimpleNamespace(role="Doctor")],
                                     check_permission=mock.Mock())
        doctor = types.SimpleNamespace(user_id=user_id, status="Active", check_permission=mock.Mock())
        gateway._fields_readable.return_value = True
        gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="doctor-1")]
        gateway.frappe.get_doc.side_effect = lambda doctype, name, **kwargs: {
            "User": user, "Healthcare Practitioner": doctor,
        }[doctype]
        gateway.frappe.db.count.return_value = 2
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"):
            self.assertEqual(owner_doctor_invitation.status(gateway, None, user_id),
                             {"status": "unavailable"})

    def test_explicit_invitation_status_keeps_existing_response(self):
        gateway = self.gateway()
        user = types.SimpleNamespace(name="doctor@example.test")
        with mock.patch.object(owner_doctor_invitation, "_gate", return_value="Doctor"), \
                mock.patch.object(owner_doctor_invitation, "_target", return_value=user), \
                mock.patch.object(owner_doctor_invitation, "_lookup", return_value={"status": "failed", "queueId": "q1"}):
            self.assertEqual(owner_doctor_invitation.status(gateway, "doctor-1", user.name),
                             {"status": "failed", "queueId": "q1"})
        gateway.frappe.get_list.assert_not_called()


if __name__ == "__main__":
    unittest.main()
