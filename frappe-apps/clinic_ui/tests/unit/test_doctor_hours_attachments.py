"""Scoped attachment projection without loading practitioner children."""
import types
import unittest
from unittest import mock


class LazyPractitioner:
    name = "practitioner"
    user_id = "doctor@example.test"
    status = "Active"

    def __init__(self):
        self.check_permission = mock.Mock()

    @property
    def practitioner_schedules(self):
        raise AssertionError("Lazy child table must not be loaded")


def field(fieldtype, options=None):
    return types.SimpleNamespace(fieldtype=fieldtype, options=options, permlevel=0)


class AttachmentTests(unittest.TestCase):
    def setUp(self):
        from clinic_ui.endpoints.doctor_hours import _scoped_attachment_rows
        self.read = _scoped_attachment_rows
        self.gateway = mock.Mock()
        self.frappe = self.gateway.frappe
        self.frappe.PermissionError = PermissionError
        self.frappe.session.user = "doctor@example.test"
        self.practitioner = LazyPractitioner()
        self.frappe.get_lazy_doc.return_value = self.practitioner
        self.parent = mock.Mock()
        self.child = mock.Mock()
        self.child.istable = 1
        self.parent.get_field.side_effect = {
            "user_id": field("Link", "User"), "status": field("Select"),
            "practitioner_schedules": field("Table", "Practitioner Service Unit Schedule"),
        }.get
        self.child.get_field.side_effect = {
            "schedule": field("Link", "Practitioner Schedule"),
            "service_unit": field("Link", "Healthcare Service Unit"),
        }.get
        for meta in (self.parent, self.child):
            meta.get_permlevel_access.return_value = [0]
            meta.get_masked_fields.return_value = []
        self.frappe.get_meta.side_effect = lambda doctype: {
            "Healthcare Practitioner": self.parent,
            "Practitioner Service Unit Schedule": self.child,
        }[doctype]
        self.row = {
            "name": "attachment", "parent": "practitioner",
            "parenttype": "Healthcare Practitioner", "parentfield": "practitioner_schedules",
            "idx": 1, "schedule": "schedule", "service_unit": "unit",
        }
        self.frappe.db.get_values.return_value = [self.row]

    def call(self):
        return self.read(self.gateway, "practitioner", "doctor@example.test")

    def test_lazy_practitioner_and_exact_bounded_query(self):
        self.assertEqual(self.call(), [self.row])
        self.frappe.get_lazy_doc.assert_called_once_with(
            "Healthcare Practitioner", "practitioner", for_update=True)
        self.practitioner.check_permission.assert_called_once_with("read")
        self.frappe.db.get_values.assert_called_once_with(
            "Practitioner Service Unit Schedule",
            {"parent": "practitioner", "parenttype": "Healthcare Practitioner",
             "parentfield": "practitioner_schedules"},
            ["name", "parent", "parenttype", "parentfield", "idx", "schedule", "service_unit"],
            as_dict=True, for_update=True, limit=33, order_by="idx asc")
        self.frappe.get_doc.assert_not_called()
        self.frappe.db.get_value.assert_not_called()

    def test_identity_permission_and_metadata_fail_closed(self):
        for attribute, value in (("name", "other"), ("user_id", "other"),
                                 ("status", "Disabled")):
            with self.subTest(attribute=attribute):
                setattr(self.practitioner, attribute, value)
                with self.assertRaises(PermissionError):
                    self.call()
                delattr(self.practitioner, attribute)
        self.frappe.session.user = "other"
        with self.assertRaises(PermissionError):
            self.call()
        self.frappe.session.user = "doctor@example.test"
        self.practitioner.check_permission.side_effect = PermissionError
        with self.assertRaises(PermissionError):
            self.call()
        self.practitioner.check_permission.side_effect = None
        self.child.istable = 0
        with self.assertRaises(PermissionError):
            self.call()
        self.child.istable = 1
        self.child.get_masked_fields.return_value = [types.SimpleNamespace(fieldname="schedule")]
        with self.assertRaises(PermissionError):
            self.call()
        self.frappe.db.get_values.assert_not_called()

    def test_bad_rows_and_overflow_fail_closed(self):
        for key, value in (("name", ""), ("parent", "other"), ("parenttype", "Other"),
                           ("parentfield", "other"), ("idx", True), ("idx", 0),
                           ("schedule", "")):
            with self.subTest(key=key, value=value):
                original = self.row[key]
                self.row[key] = value
                with self.assertRaises(PermissionError):
                    self.call()
                self.row[key] = original
        self.frappe.db.get_values.return_value = [self.row, dict(self.row)]
        with self.assertRaises(PermissionError):
            self.call()
        self.frappe.db.get_values.return_value = [dict(self.row, name=str(i)) for i in range(33)]
        with self.assertRaises(PermissionError):
            self.call()

    def test_masked_unit_is_not_queried_and_messages_are_stripped(self):
        self.child.get_masked_fields.return_value = [types.SimpleNamespace(fieldname="service_unit")]
        self.frappe.db.get_values.return_value = [{k: v for k, v in self.row.items()
                                                   if k != "service_unit"}]
        self.assertNotIn("service_unit", self.call()[0])
        self.assertNotIn("service_unit", self.frappe.db.get_values.call_args.args[2])
        self.gateway.strip_message_channels.assert_called_once_with()
