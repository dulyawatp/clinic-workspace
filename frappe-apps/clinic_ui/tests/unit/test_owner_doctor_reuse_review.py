"""Read-only schedule reuse review without a live site."""

import unittest
from types import SimpleNamespace
from unittest import mock

from clinic_ui.endpoints import owner_doctor_reuse


def document(**values):
    return SimpleNamespace(check_permission=mock.Mock(), **values)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.frappe = mock.Mock()
        self.frappe.db.exists.return_value = False
        self.practitioner = document(name="doc", modified="p1", status="Disabled", user_id=None,
                                     show_in_portal=0, practitioner_schedules=[], first_name="A", last_name="")
        self.unit = document(name="unit", company="company", is_group=0, allow_appointments=1)
        self.source = document(name="schedule", modified="s1", disabled=0, allow_video_conferencing=0,
                               time_slots=[SimpleNamespace(day="Tuesday", from_time="09:00:01",
                                   to_time="10:00:00", duration=30, maximum_appointments=20),
                                   SimpleNamespace(day="Monday", from_time="09:00:00",
                                   to_time="10:00:00", duration=30, maximum_appointments=0)])
        docs = {"Healthcare Practitioner": self.practitioner, "Healthcare Service Unit": self.unit,
                "Practitioner Schedule": self.source}
        self.frappe.get_doc.side_effect = lambda kind, name, **kwargs: docs[kind]
        self.authority = mock.patch.object(owner_doctor_reuse.doctor_hours, "check_field_authority")
        self.profile = mock.patch.object(owner_doctor_reuse.doctor_hours, "_check_fields")
        self.authority.start()
        self.profile.start()
        self.addCleanup(self.authority.stop)
        self.addCleanup(self.profile.stop)

    def review(self, **kwargs):
        return owner_doctor_reuse.prepare_review(self.frappe, "doc", "schedule", "unit", "company", **kwargs)

    def test_review_is_normalized_read_only_and_locks_in_order(self):
        result = self.review(lock=True)
        self.assertEqual((result["mode"], result["practitionerVersion"], result["scheduleVersion"]),
                         ("reuse", "p1", "s1"))
        self.assertEqual([row["day"] for row in result["slots"]], ["Tuesday", "Monday"])
        self.assertEqual(result["slots"][0]["from_time"], "09:00:01")
        self.assertEqual(result["slots"][1]["maximum_appointments"], 0)
        self.assertEqual(len(result["slotDigest"]), 64)
        self.assertEqual(self.frappe.get_doc.call_args_list, [
            mock.call("Healthcare Practitioner", "doc", for_update=True),
            mock.call("Healthcare Service Unit", "unit", for_update=True),
            mock.call("Practitioner Schedule", "schedule", for_update=True)])
        for doc in (self.practitioner, self.unit, self.source):
            doc.check_permission.assert_called_once_with("read")
        self.frappe.new_doc.assert_not_called()

    def test_stale_versions_and_digest_rejected(self):
        result = self.review()
        self.source.time_slots[0].maximum_appointments = 21
        self.assertNotEqual(result["slotDigest"], self.review()["slotDigest"])
        self.source.time_slots[0].maximum_appointments = 20
        for kwargs in ({"practitioner_version": "p2"}, {"schedule_version": "s2"},
                       {"slot_digest": "0" * 64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.review(**kwargs)
        self.review(practitioner_version="p1", schedule_version="s1", slot_digest=result["slotDigest"])

    def test_permission_revocation_stops_before_later_reads(self):
        for doc, expected in ((self.practitioner, 1), (self.unit, 2), (self.source, 3)):
            self.frappe.get_doc.reset_mock()
            doc.check_permission.side_effect = PermissionError("revoked")
            with self.subTest(expected=expected), self.assertRaises(PermissionError):
                self.review(lock=True)
            self.assertEqual(self.frappe.get_doc.call_count, expected)
            doc.check_permission.side_effect = None

    def test_native_read_field_checks_are_requested(self):
        self.review()
        owner_doctor_reuse.doctor_hours.check_field_authority.assert_called_once_with(self.frappe)
        call = owner_doctor_reuse.doctor_hours._check_fields.call_args
        self.assertEqual(call.args[1], "Healthcare Practitioner")
        self.assertEqual(set(call.args[2]), {"first_name", "last_name", "show_in_portal"})
        self.assertEqual(call.args[3], "read")

    def test_denied_field_authority_prevents_document_reads(self):
        owner_doctor_reuse.doctor_hours.check_field_authority.side_effect = PermissionError("field denied")
        with self.assertRaises(PermissionError):
            self.review()
        self.frappe.get_doc.assert_not_called()

    def test_ineligible_documents_rejected(self):
        for doc, field, value in ((self.practitioner, "practitioner_schedules", [object()]),
                                  (self.practitioner, "show_in_portal", 1),
                                  (self.unit, "company", "other"), (self.unit, "is_group", 1),
                                  (self.unit, "allow_appointments", 0),
                                  (self.source, "disabled", 1), (self.source, "allow_video_conferencing", 1)):
            original = getattr(doc, field)
            setattr(doc, field, value)
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.review()
            setattr(doc, field, original)

    def test_invalid_native_slots_rejected(self):
        row = self.source.time_slots[0]
        for field, value in (("day", "Noday"), ("from_time", "09:00:00.1"),
                             ("duration", float("nan")), ("duration", 0),
                             ("maximum_appointments", -1), ("maximum_appointments", 1.5)):
            original = getattr(row, field)
            setattr(row, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.review()
            setattr(row, field, original)
        self.source.time_slots = [row] * 513
        with self.assertRaises(ValueError):
            self.review()


if __name__ == "__main__":
    unittest.main()
