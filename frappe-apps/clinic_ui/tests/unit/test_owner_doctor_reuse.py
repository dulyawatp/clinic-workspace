"""Finish reuse transaction checks without a Frappe site."""

import unittest
from types import SimpleNamespace
from unittest import mock

from clinic_ui.endpoints import owner_doctor_finish as finish
from clinic_ui.runtime.frappe_gateway import Gateway


class PermissionDenied(Exception):
    pass


class DuplicateEntry(Exception):
    pass


class MissingDocument(Exception):
    pass


class ReuseFinishTests(unittest.TestCase):
    def setUp(self):
        self.gateway = mock.Mock()
        self.gateway.frappe.PermissionError = PermissionDenied
        self.gateway.frappe.DuplicateEntryError = DuplicateEntry
        self.gateway.frappe.DoesNotExistError = MissingDocument
        self.gateway.deny.side_effect = PermissionDenied
        self.gateway.respond_write.side_effect = lambda status, body: (status, body)
        self.gateway.owner_doctor_finish_email_valid.return_value = True
        self.gateway.owner_doctor_finish_native_user_supported.return_value = True
        self.gateway.frappe.db.exists.return_value = False
        self.gateway.frappe.db.sql.return_value = []
        meta = self.gateway.frappe.get_meta.return_value
        meta.get_permlevel_access.return_value = [0]
        meta.get_field.return_value.permlevel = 0
        self.doc = SimpleNamespace(name="doc", modified="p1", first_name="A", last_name="", status="Disabled",
                                   user_id=None, show_in_portal=0, practitioner_schedules=[],
                                   check_permission=mock.Mock())
        self.source = SimpleNamespace(name="schedule", modified="s1", disabled=0, allow_video_conferencing=0,
                                      check_permission=mock.Mock(), save=mock.Mock(), insert=mock.Mock())
        self.permission = SimpleNamespace(user="doctor@example.test", allow="Healthcare Practitioner",
                                          for_value="doc", apply_to_all_doctypes=1, is_default=0,
                                          check_permission=mock.Mock())
        self.user = mock.Mock()
        self.saved_user = SimpleNamespace(enabled=1, user_type="System User", email="doctor@example.test")
        self.gateway.owner_role_readback.return_value = (self.saved_user, ["Doctor"])
        self.gateway.frappe.get_list.return_value = [SimpleNamespace(name="permission")]
        self.gateway.frappe.get_doc.side_effect = self.get_doc
        self.body = {"mode": "reuse", "practitioner": "doc", "practitionerVersion": "p1",
                     "schedule": "schedule", "scheduleVersion": "s1", "serviceUnit": "unit",
                     "email": "doctor@example.test", "slotDigest": "a" * 64}
        self.slots = [{"day": "Monday", "from_time": "09:00:00", "to_time": "10:00:00",
                       "duration": 30.0, "maximum_appointments": 2}]
        self.review = {"slots": self.slots}
        self.gate = mock.patch.object(finish, "_gate", return_value=("company", "Doctor", "owner"))
        self.prepare = mock.patch.object(finish.owner_doctor_reuse, "prepare_review", return_value=self.review)
        self.normalized = mock.patch.object(finish.owner_doctor_reuse, "normalized_slots", return_value=self.slots)
        self.gate.start()
        self.prepare.start()
        self.normalized.start()
        self.addCleanup(self.gate.stop)
        self.addCleanup(self.prepare.stop)
        self.addCleanup(self.normalized.stop)
        self.gateway.owner_doctor_finish_link.side_effect = self.link

    def get_doc(self, kind, *args, **kwargs):
        if isinstance(kind, dict):
            return self.user
        return {"Healthcare Practitioner": self.doc, "Practitioner Schedule": self.source,
                "User Permission": self.permission}[kind]

    def link(self, doc, email, schedule, unit):
        doc.user_id = email
        doc.status = "Active"
        doc.practitioner_schedules.append(SimpleNamespace(schedule=schedule, service_unit=unit))

    def test_reuse_links_reviewed_source_without_publishing(self):
        result = finish.finish(self.gateway, self.body, "csrf")
        self.assertEqual(result, {"practitioner": "doc", "userId": "doctor@example.test",
                                  "schedule": "schedule", "serviceUnit": "unit",
                                  "slots": self.slots, "enabled": True})
        finish._gate.assert_called_once_with(self.gateway, "csrf", write=True, reuse=True)
        finish.owner_doctor_reuse.prepare_review.assert_called_once_with(
            self.gateway.frappe, "doc", "schedule", "unit", "company", lock=True,
            practitioner_version="p1", schedule_version="s1", slot_digest="a" * 64)
        self.doc.check_permission.assert_any_call("write")
        self.gateway.owner_doctor_finish_link.assert_called_once_with(
            self.doc, "doctor@example.test", "schedule", "unit")
        self.gateway.owner_doctor_finish_publish.assert_not_called()
        self.source.save.assert_not_called()
        self.source.insert.assert_not_called()
        self.gateway.frappe.new_doc.assert_not_called()
        self.gateway.commit.assert_called_once()

    def test_stale_review_rejected_before_user_insert(self):
        finish.owner_doctor_reuse.prepare_review.side_effect = ValueError("stale or attached")
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "STALE_VERSION"}))
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()
        self.gateway.owner_doctor_finish_link.assert_not_called()

    def test_missing_review_document_is_stale_before_insert(self):
        finish.owner_doctor_reuse.prepare_review.side_effect = MissingDocument("missing")
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "STALE_VERSION"}))
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()

    def test_missing_target_second_read_is_stale_before_insert(self):
        self.gateway.frappe.get_doc.side_effect = MissingDocument("missing")
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "STALE_VERSION"}))
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()

    def test_revoked_review_permission_is_denied_not_stale(self):
        finish.owner_doctor_reuse.prepare_review.side_effect = PermissionDenied("revoked")
        with self.assertRaises(PermissionDenied):
            finish.finish(self.gateway, self.body, "csrf")
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()

    def test_real_review_rejects_existing_attachment_before_insert(self):
        self.prepare.stop()
        self.doc.practitioner_schedules.append(SimpleNamespace(schedule="other", service_unit="unit"))
        with mock.patch.object(finish.doctor_hours, "check_field_authority"), \
                mock.patch.object(finish.doctor_hours, "_check_fields"):
            self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "STALE_VERSION"}))
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()
        self.gateway.owner_doctor_finish_link.assert_not_called()

    def test_changed_second_practitioner_read_rejected_before_insert(self):
        self.doc.modified = "p2"
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "STALE_VERSION"}))
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()

    def test_revoked_practitioner_write_denied_before_insert(self):
        def check(access):
            if access == "write":
                raise PermissionDenied
        self.doc.check_permission.side_effect = check
        with self.assertRaises(PermissionDenied):
            finish.finish(self.gateway, self.body, "csrf")
        self.gateway.owner_doctor_finish_insert_user.assert_not_called()

    def test_source_change_after_link_is_uncertain_without_retry(self):
        self.source.modified = "changed"
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "WRITE_UNCERTAIN"}))
        self.gateway.owner_doctor_finish_insert_user.assert_called_once()
        self.gateway.owner_doctor_finish_publish.assert_not_called()
        self.gateway.commit.assert_not_called()

    def test_native_role_mismatch_is_uncertain(self):
        self.gateway.owner_role_readback.return_value = (self.saved_user, ["Wrong"])
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "WRITE_UNCERTAIN"}))
        self.gateway.commit.assert_not_called()

    def test_commit_failure_is_uncertain_without_replay(self):
        self.gateway.commit.side_effect = RuntimeError("commit result unknown")
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "WRITE_UNCERTAIN"}))
        self.gateway.owner_doctor_finish_insert_user.assert_called_once()
        self.gateway.owner_doctor_finish_link.assert_called_once()

    def test_changed_native_slots_after_link_is_uncertain(self):
        finish.owner_doctor_reuse.normalized_slots.return_value = []
        self.assertEqual(finish.finish(self.gateway, self.body, "csrf"), (409, {"code": "WRITE_UNCERTAIN"}))
        self.gateway.commit.assert_not_called()

    def test_legacy_copy_still_uses_existing_draft_path(self):
        body = {k: v for k, v in self.body.items() if k not in ("mode", "serviceUnit")}
        with mock.patch.object(finish, "_draft", side_effect=ValueError):
            self.assertEqual(finish.finish(self.gateway, body, "csrf"), (409, {"code": "STALE_VERSION"}))
        finish._gate.assert_called_once_with(self.gateway, "csrf", write=True)
        finish.owner_doctor_reuse.prepare_review.assert_not_called()

    def test_legacy_copy_publishes_new_schedule(self):
        body = {k: v for k, v in self.body.items() if k not in ("mode", "serviceUnit")}
        self.doc.modified = "p1"
        unit = SimpleNamespace(name="unit")
        self.doc.practitioner_schedules = [SimpleNamespace(schedule="schedule", service_unit="unit")]
        body["slotDigest"] = finish._slot_digest("schedule", "unit", self.slots)
        def publish(doc, email, slots):
            doc.user_id, doc.status = email, "Active"
            return self.source
        self.gateway.owner_doctor_finish_publish.side_effect = publish
        with mock.patch.object(finish, "_draft", return_value=(self.doc, self.source, unit, self.slots)), \
                mock.patch.object(finish, "_slots", return_value=self.slots):
            result = finish.finish(self.gateway, body, "csrf")
        self.assertEqual(result["schedule"], "schedule")
        self.gateway.owner_doctor_finish_publish.assert_called_once_with(self.doc, "doctor@example.test", self.slots)
        self.gateway.owner_doctor_finish_link.assert_not_called()

    def test_gateway_appends_one_child_and_saves_only_practitioner(self):
        practitioner = mock.Mock()
        Gateway.owner_doctor_finish_link(None, practitioner, "doctor@example.test", "schedule", "unit")
        practitioner.append.assert_called_once_with(
            "practitioner_schedules", {"schedule": "schedule", "service_unit": "unit"})
        self.assertEqual((practitioner.user_id, practitioner.status), ("doctor@example.test", "Active"))
        practitioner.save.assert_called_once_with()
