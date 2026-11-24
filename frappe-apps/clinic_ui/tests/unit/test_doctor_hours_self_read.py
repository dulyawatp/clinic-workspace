"""Metadata-only checks for the scoped doctor-hours projection."""
import types
import unittest
from datetime import timedelta
from unittest import mock


def field(fieldtype, options=None, mask=0, permlevel=0):
    return types.SimpleNamespace(fieldtype=fieldtype, options=options,
                                 mask=mask, permlevel=permlevel)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.parent = mock.Mock()
        self.child = mock.Mock()
        self.child.istable = 1
        self.parent_fields = {
            "disabled": field("Check"),
            "time_slots": field("Table", "Healthcare Schedule Time Slot"),
        }
        self.child_fields = {
            "day": field("Select"),
            "from_time": field("Time"),
            "to_time": field("Time"),
        }
        self.parent.get_field.side_effect = self.parent_fields.get
        self.child.get_field.side_effect = self.child_fields.get
        self.parent.get_masked_fields.return_value = []
        self.child.get_masked_fields.return_value = []
        self.parent.get_permlevel_access.return_value = []
        self.child.get_permlevel_access.return_value = []
        self.frappe = mock.Mock()
        self.frappe.PermissionError = PermissionError
        self.frappe.session.user = "doctor@example.test"
        self.frappe.get_meta.side_effect = lambda doctype: {
            "Practitioner Schedule": self.parent,
            "Healthcare Schedule Time Slot": self.child,
        }[doctype]

    def check(self, include_slots=True):
        from clinic_ui.endpoints.doctor_hours_self_read import check_schedule_projection_metadata
        check_schedule_projection_metadata(self.frappe, include_slots=include_slots)

    def test_public_default_schema_does_not_grant_or_require_role_permission(self):
        self.check()
        self.parent.get_permlevel_access.assert_not_called()
        self.child.get_permlevel_access.assert_not_called()
        self.child.get_masked_fields.assert_called_once_with(parenttype="Practitioner Schedule")

    def test_wrong_type_target_and_non_child_rejected(self):
        for value in (field("Data"), field("Table", "Other")):
            with self.subTest(value=value):
                self.parent_fields["time_slots"] = value
                with self.assertRaises(PermissionError):
                    self.check()
        self.parent_fields["time_slots"] = field("Table", "Healthcare Schedule Time Slot")
        self.child.istable = 0
        with self.assertRaises(PermissionError):
            self.check()

    def test_parent_and_slot_masks_fail_closed(self):
        self.parent.get_masked_fields.return_value = [types.SimpleNamespace(fieldname="disabled")]
        with self.assertRaises(PermissionError):
            self.check()
        self.parent.get_masked_fields.return_value = []
        self.child_fields["day"].mask = 1
        with self.assertRaises(PermissionError):
            self.check()
        self.child_fields["day"].mask = 0
        self.child.get_masked_fields.return_value = [types.SimpleNamespace(fieldname="from_time")]
        with self.assertRaises(PermissionError):
            self.check()
        self.child.get_masked_fields.return_value = None
        with self.assertRaises(PermissionError):
            self.check()

    def test_positive_permlevel_requires_ordinary_authority(self):
        self.child_fields["day"].permlevel = 1
        with self.assertRaises(PermissionError):
            self.check()
        self.child.get_permlevel_access.return_value = [1]
        with self.assertRaises(PermissionError):
            self.check()

    def test_disabled_only_does_not_inspect_slot_metadata(self):
        self.check(False)
        self.frappe.get_meta.assert_called_once_with("Practitioner Schedule")

    def test_missing_mask_metadata_fails_closed(self):
        del self.parent_fields["disabled"].mask
        with self.assertRaises(PermissionError):
            self.check(False)

    def read_ranges(self):
        from clinic_ui.endpoints.doctor_hours_self_read import read_schedule_ranges
        return read_schedule_ranges(self.frappe, "schedule-1", lambda value: value)

    def setup_schedule(self):
        self.frappe.db.get_value.return_value = {"name": "schedule-1", "disabled": 0}
        self.frappe.db.get_values.return_value = [{
            "name": "slot-1", "parent": "schedule-1", "parenttype": "Practitioner Schedule",
            "parentfield": "time_slots", "idx": 1, "day": "Monday",
            "from_time": "10:00:00", "to_time": "12:00:00",
        }]

    def test_range_query_exact_and_returns_projection_only(self):
        self.setup_schedule()
        self.assertEqual(self.read_ranges(), [{
            "day": "Monday", "from_time": "10:00:00", "to_time": "12:00:00",
        }])
        self.frappe.db.get_value.assert_called_once_with(
            "Practitioner Schedule", {"name": "schedule-1"}, ["name", "disabled"], as_dict=True,
            for_update=True)
        self.frappe.db.get_values.assert_called_once_with(
            "Healthcare Schedule Time Slot",
            {"parent": "schedule-1", "parenttype": "Practitioner Schedule",
             "parentfield": "time_slots"},
            ["name", "parent", "parenttype", "parentfield", "idx",
             "day", "from_time", "to_time"],
            order_by="idx asc", limit=513, as_dict=True, for_update=True)

    def test_disabled_schedule_skips_child(self):
        self.setup_schedule()
        self.frappe.db.get_value.return_value["disabled"] = 1
        self.assertEqual(self.read_ranges(), [])
        self.frappe.db.get_values.assert_not_called()
        self.child.get_masked_fields.assert_not_called()

    def test_invalid_parent_and_rows_fail_closed(self):
        self.setup_schedule()
        for parent in (None, {"name": "other", "disabled": 0},
                       {"name": "schedule-1", "disabled": 2}):
            self.frappe.db.get_value.return_value = parent
            with self.subTest(parent=parent), self.assertRaises(PermissionError):
                self.read_ranges()
        self.frappe.db.get_values.assert_not_called()
        self.setup_schedule()
        row = self.frappe.db.get_values.return_value[0]
        for key, value in (("parent", "other"), ("parenttype", "Other"),
                           ("parentfield", "other"), ("name", ""), ("idx", 0),
                           ("day", "Funday")):
            original = row[key]
            row[key] = value
            with self.subTest(key=key), self.assertRaises(PermissionError):
                self.read_ranges()
            row[key] = original

    def test_mask_and_oversize_fail_closed(self):
        self.setup_schedule()
        self.child_fields["day"].mask = 1
        with self.assertRaises(PermissionError):
            self.read_ranges()
        self.frappe.db.get_values.assert_not_called()
        self.child_fields["day"].mask = 0
        self.frappe.db.get_values.return_value *= 513
        with self.assertRaises(PermissionError):
            self.read_ranges()

    def test_existing_formatter_shapes_and_rejects_native_times(self):
        from clinic_ui.endpoints.doctor_hours import _time
        from clinic_ui.endpoints.doctor_hours_self_read import read_schedule_ranges
        self.setup_schedule()
        row = self.frappe.db.get_values.return_value[0]
        row["from_time"] = timedelta(hours=9, seconds=7)
        self.assertEqual(read_schedule_ranges(self.frappe, "schedule-1", _time)[0]["from_time"],
                         "09:00:07")
        row["from_time"] = timedelta(microseconds=1)
        with self.assertRaises(PermissionError):
            read_schedule_ranges(self.frappe, "schedule-1", _time)

    def test_many_exact_doctype_named_schedule_and_parent_first(self):
        from clinic_ui.endpoints.doctor_hours_self_read import read_many_schedule_ranges
        self.setup_schedule()
        def parent(doctype, filters, fields, **kwargs):
            return {"name": filters["name"], "disabled": 0}
        self.frappe.db.get_value.side_effect = parent
        self.frappe.db.get_values.return_value = []
        self.assertEqual(read_many_schedule_ranges(
            self.frappe, ["Practitioner Schedule"], str), {"Practitioner Schedule": []})
        self.frappe.db.get_value.assert_called_once_with(
            "Practitioner Schedule", {"name": "Practitioner Schedule"},
            ["name", "disabled"], as_dict=True, for_update=True)

    def test_many_orphan_last_prevents_all_slot_queries(self):
        from clinic_ui.endpoints.doctor_hours_self_read import read_many_schedule_ranges
        self.setup_schedule()
        self.frappe.db.get_value.side_effect = lambda doctype, filters, fields, **kwargs: (
            None if filters["name"] == "z-orphan" else {"name": filters["name"], "disabled": 0})
        with self.assertRaises(PermissionError):
            read_many_schedule_ranges(self.frappe, ["z-orphan", "a-valid"], str)
        self.frappe.db.get_values.assert_not_called()

    def test_many_combined_budget_fails_closed(self):
        from clinic_ui.endpoints.doctor_hours_self_read import read_many_schedule_ranges
        self.setup_schedule()
        self.frappe.db.get_value.side_effect = lambda doctype, filters, fields, **kwargs: {
            "name": filters["name"], "disabled": 0}
        def rows(doctype, filters, fields, **kwargs):
            return [{"name": filters["parent"], "parent": filters["parent"],
                     "parenttype": "Practitioner Schedule", "parentfield": "time_slots",
                     "idx": 1, "day": "Monday", "from_time": "10:00", "to_time": "11:00"}]
        self.frappe.db.get_values.side_effect = rows
        with self.assertRaises(PermissionError):
            read_many_schedule_ranges(self.frappe, ["b", "a"], str, max_slots=1)
        self.assertEqual([call.kwargs["limit"] for call in self.frappe.db.get_values.call_args_list],
                         [2, 1])


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        from clinic_ui.endpoints import doctor_hours
        self.endpoint = doctor_hours
        self.gateway = mock.Mock()
        self.gateway.site = "site.local"
        self.gateway.setting.side_effect = lambda name: {
            "clinic_ui_doctor_hours_self_read_enabled": True,
            "clinic_ui_doctor_hours_self_read_site": "site.local",
        }.get(name)
        self.gateway.frappe.local.request.headers = {
            "X-Clinic-Workspace": "DOC", "X-Clinic-Session-Epoch": "epoch",
        }
        self.user = "doctor@example.test"
        self.gateway.frappe.session.user = self.user
        self.view = types.SimpleNamespace(user=self.user, sid="sid")
        self.auth = mock.patch.object(doctor_hours.production_read, "authorize_if_selected",
                                      return_value=(None, self.view, "DOC"))
        self.auth.start()
        self.addCleanup(self.auth.stop)
        self.fields = mock.patch.object(doctor_hours, "_check_fields")
        self.fields.start()
        self.addCleanup(self.fields.stop)
        self.native_fields = mock.patch.object(doctor_hours, "_check_native_fields")
        self.native_fields.start()
        self.addCleanup(self.native_fields.stop)
        self.ranges = mock.patch("clinic_ui.endpoints.doctor_hours_self_read.read_many_schedule_ranges",
                                 return_value={"shared": [{"day": "Monday", "from_time": "10:00:00",
                                                            "to_time": "12:00:00"}]})
        self.range_mock = self.ranges.start()
        self.addCleanup(self.ranges.stop)
        self.attachment = types.SimpleNamespace(name="link", schedule="shared", service_unit=None)
        self.practitioner = types.SimpleNamespace(
            name="practitioner", user_id=self.user, status="Active",
            practitioner_schedules=[self.attachment], check_permission=mock.Mock())
        self.gateway.frappe.get_list.return_value = [types.SimpleNamespace(name="practitioner")]
        self.identities = [{"name": "practitioner"}]
        self.gateway.frappe.get_doc.return_value = self.practitioner
        self.gateway.frappe.get_lazy_doc.return_value = self.practitioner
        self.gateway.frappe.get_meta.return_value.istable = 1
        self.link = {
            "name": "link", "parent": "practitioner", "parenttype": "Healthcare Practitioner",
            "parentfield": "practitioner_schedules", "schedule": "shared", "idx": 1,
            "service_unit": None,
        }
        self.links = [self.link]
        self.gateway.frappe.db.get_values.side_effect = lambda doctype, *args, **kwargs: (
            self.identities if doctype == "Healthcare Practitioner" else self.links)
        self.gateway.frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: (
            1 if doctype == "User" else self.link)

    def test_scoped_read_and_live_detachment(self):
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "ready", "schedules": [{
            "slots": [{"day": "Monday", "from_time": "10:00:00", "to_time": "12:00:00"}]}]})
        self.gateway.frappe.db.get_values.assert_any_call(
            "Healthcare Practitioner", {"user_id": self.user, "status": "Active"},
            ["name"], limit=2, as_dict=True, for_update=True)
        self.gateway.frappe.get_doc.assert_not_called()
        self.gateway.frappe.get_lazy_doc.assert_called_once_with(
            "Healthcare Practitioner", "practitioner", for_update=True)
        self.links.clear()
        self.range_mock.reset_mock()
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.range_mock.assert_called_once_with(self.gateway.frappe, [], self.endpoint._time)

    def test_hidden_duplicate_and_bad_link_never_read_schedule(self):
        self.identities.append({"name": "hidden"})
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.range_mock.assert_not_called()
        self.identities.pop()
        self.link["parent"] = "other"
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.range_mock.assert_not_called()

    def test_off_and_wrong_site_use_ordinary_path(self):
        for enabled, site in ((False, "site.local"), (1, "site.local"), (True, "other")):
            self.gateway.setting.side_effect = lambda name: {
                "clinic_ui_doctor_hours_self_read_enabled": enabled,
                "clinic_ui_doctor_hours_self_read_site": site,
            }.get(name)
            self.practitioner.practitioner_schedules = []
            self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.gateway.frappe.db.get_values.assert_not_called()
        self.range_mock.assert_not_called()

    def test_scoped_rejects_disabled_or_mismatched_native_actor(self):
        self.gateway.frappe.session.user = "other"
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.gateway.frappe.session.user = self.user
        self.gateway.frappe.db.get_value.side_effect = lambda *args, **kwargs: 0
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.gateway.frappe.db.get_values.assert_not_called()

    def test_shared_schedule_for_two_users_then_detachment(self):
        other = "other@example.test"
        other_doc = types.SimpleNamespace(
            name="other-practitioner", user_id=other, status="Active", check_permission=mock.Mock())
        other_link = {**self.link, "name": "other-link", "parent": "other-practitioner"}
        docs = {self.user: self.practitioner, other: other_doc}
        attachments = {self.user: [self.link], other: [other_link]}
        self.gateway.frappe.get_list.side_effect = lambda *args, **kwargs: [
            types.SimpleNamespace(name=docs[self.view.user].name)]
        self.gateway.frappe.db.get_values.side_effect = lambda doctype, *args, **kwargs: (
            [{"name": docs[self.view.user].name}] if doctype == "Healthcare Practitioner"
            else attachments[self.view.user])
        self.gateway.frappe.get_lazy_doc.side_effect = lambda doctype, name, **kwargs: docs[self.view.user]
        self.assertEqual(self.endpoint.get(self.gateway)["state"], "ready")
        self.view.user = other
        self.gateway.frappe.session.user = other
        self.assertEqual(self.endpoint.get(self.gateway)["state"], "ready")
        attachments[other].clear()
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "empty"})
        self.view.user = self.user
        self.gateway.frappe.session.user = self.user
        self.assertEqual(self.endpoint.get(self.gateway)["state"], "ready")

    def test_repeated_schedule_output_exceeding_budget_is_unavailable(self):
        self.links.append({**self.link, "name": "link-2", "idx": 2})
        self.range_mock.return_value = {"shared": [{"day": "Monday"}] * 257}
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_orphan_last_causes_no_slot_reads(self):
        from clinic_ui.endpoints import doctor_hours_self_read
        self.ranges.stop()
        self.link["schedule"] = "a-valid"
        self.links.append({**self.link, "name": "link-2", "idx": 2, "schedule": "z-orphan"})
        def get_value(doctype, filters, *args, **kwargs):
            if doctype == "User":
                return 1
            return None if filters["name"] == "z-orphan" else {
                "name": filters["name"], "disabled": 0}
        self.gateway.frappe.db.get_value.side_effect = get_value
        with mock.patch.object(doctor_hours_self_read, "check_schedule_projection_metadata"):
            self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})
        self.assertFalse(any(call.args[0] == "Healthcare Schedule Time Slot"
                             for call in self.gateway.frappe.db.get_values.call_args_list))

    def test_projection_failure_returns_unavailable(self):
        self.range_mock.side_effect = PermissionError("masked")
        self.assertEqual(self.endpoint.get(self.gateway), {"state": "unavailable"})

    def test_real_gate_rejects_stale_sid_revoked_and_disabled(self):
        from clinic_ui.core.epoch import session_epoch
        from clinic_ui.core import production_enrollment
        from .fake_gateway import Denied, SECRET, session_view
        from .test_production_read_adapter import gateway

        self.auth.stop()
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(user=self.user, roles=("Physician",))
        fake.frappe.local.request.cookies = {"sid": fake.current_session.sid}
        headers = fake.frappe.local.request.headers
        headers.update({"X-Clinic-Workspace": "DOC", "X-Clinic-Session-Epoch": "stale"})
        with mock.patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            with self.assertRaises(Denied):
                self.endpoint.get(fake)
            headers["X-Clinic-Session-Epoch"] = session_epoch(
                SECRET.encode(), fake.site, fake.current_session.sid)
            fake.frappe.local.request.cookies["sid"] = "other"
            with self.assertRaises(Denied):
                self.endpoint.get(fake)
            fake.frappe.local.request.cookies["sid"] = fake.current_session.sid
            fake.fresh_user_roles = lambda user: ()
            with self.assertRaises(Denied):
                self.endpoint.get(fake)
            fake.current_session = session_view(user=self.user, roles=("Physician",), enabled=False)
            with self.assertRaises(Denied):
                self.endpoint.get(fake)
