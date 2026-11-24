"""Read-only reuse GET contract."""

import unittest
from types import SimpleNamespace
from unittest import mock

from clinic_ui.endpoints import owner_doctor_finish as finish


class Denied(Exception):
    pass


class ReuseGetTests(unittest.TestCase):
    def setUp(self):
        self.gateway = mock.Mock()
        self.gateway.frappe.PermissionError = Denied
        self.gateway.setting.side_effect = lambda key: {
            "clinic_ui_owner_doctor_finish_reuse_enabled": True,
            "clinic_ui_owner_doctor_finish_reuse_site": "site",
            "clinic_ui_owner_doctor_finish_reuse_hooks_reviewed_site": "site",
        }.get(key)
        self.gateway.site = "site"
        self.gateway.deny.side_effect = Denied
        self.gateway.respond_write.side_effect = lambda status, body: (status, body)
        self.gate = mock.patch.object(finish, "_gate", return_value=("company", "Doctor", "owner"))
        self.gate.start()
        self.addCleanup(self.gate.stop)
        self.doc = SimpleNamespace(name="doc", modified="p1", status="Disabled", user_id=None,
                                   show_in_portal=0, practitioner_schedules=[], first_name="A", last_name="",
                                   check_permission=mock.Mock())
        self.unit = SimpleNamespace(name="unit", company="company", is_group=0, allow_appointments=1,
                                    check_permission=mock.Mock())
        self.schedule = SimpleNamespace(name="schedule", disabled=0, allow_video_conferencing=0,
                                        time_slots=[SimpleNamespace(day="Monday", from_time="09:00:00",
                                            to_time="10:00:00", duration=30, maximum_appointments=1)],
                                        check_permission=mock.Mock())
        self.gateway.frappe.db.exists.return_value = False
        self.gateway.frappe.get_doc.side_effect = lambda kind, name, **kw: {
            "Healthcare Practitioner": self.doc, "Healthcare Service Unit": self.unit,
            "Practitioner Schedule": self.schedule}[kind]
        self.gateway.frappe.get_list.side_effect = lambda kind, **kw: [SimpleNamespace(name={
            "Healthcare Service Unit": "unit", "Practitioner Schedule": "schedule"}[kind])]
        self.fields = mock.patch.object(finish.doctor_hours, "check_field_authority")
        self.extra_fields = mock.patch.object(finish.doctor_hours, "_check_fields")
        self.fields.start()
        self.extra_fields.start()
        self.addCleanup(self.fields.stop)
        self.addCleanup(self.extra_fields.stop)

    def test_options_filters_and_returns_contract(self):
        result = finish.reuse_options(self.gateway, "doc")
        self.assertEqual(result, {"practitioner": "doc", "practitionerVersion": "p1",
                                  "schedules": [{"name": "schedule"}], "serviceUnits": [{"name": "unit"}]})
        self.assertEqual(self.gateway.frappe.get_list.call_count, 2)
        self.gateway.frappe.get_all.assert_not_called()
        self.gateway.frappe.new_doc.assert_not_called()

    def test_attached_draft_yields_empty_lists(self):
        self.doc.practitioner_schedules = [object()]
        result = finish.reuse_options(self.gateway, "doc")
        self.assertEqual(result, {"practitioner": "doc", "practitionerVersion": "p1",
                                  "schedules": [], "serviceUnits": []})
        self.gateway.frappe.get_list.assert_not_called()

    def test_unsupported_schedule_yields_empty_lists(self):
        self.schedule.time_slots[0].duration = 0
        self.assertEqual(finish.reuse_options(self.gateway, "doc"),
                         {"practitioner": "doc", "practitionerVersion": "p1",
                          "schedules": [], "serviceUnits": []})

    def test_options_skips_individually_denied_source(self):
        self.schedule.check_permission.side_effect = Denied
        self.assertEqual(finish.reuse_options(self.gateway, "doc")["schedules"], [])

    def test_missing_reuse_opt_in_returns_empty_choices(self):
        self.gateway.setting.return_value = None
        self.gateway.setting.side_effect = None
        result = finish.reuse_options(self.gateway, "doc")
        self.assertEqual((result["schedules"], result["serviceUnits"]), ([], []))
        self.gateway.frappe.get_list.assert_not_called()

    def test_over_cap_fails_closed_and_queries_are_bounded_ordered(self):
        self.gateway.frappe.get_list.side_effect = None
        self.gateway.frappe.get_list.return_value = [SimpleNamespace(name=str(n)) for n in range(101)]
        result = finish.reuse_options(self.gateway, "doc")
        self.assertEqual((result["schedules"], result["serviceUnits"]), ([], []))
        for call in self.gateway.frappe.get_list.call_args_list:
            self.assertEqual(call.kwargs["limit_page_length"], 101)
            self.assertEqual(call.kwargs["order_by"], "name asc")

    def test_denied_candidate_does_not_hide_other_eligible_choices(self):
        denied = SimpleNamespace(name="denied", check_permission=mock.Mock(side_effect=Denied))
        self.gateway.frappe.get_list.side_effect = lambda kind, **kw: (
            [SimpleNamespace(name="denied"), SimpleNamespace(name="schedule")]
            if kind == "Practitioner Schedule" else [SimpleNamespace(name="unit")])
        original = self.gateway.frappe.get_doc.side_effect
        self.gateway.frappe.get_doc.side_effect = lambda kind, name, **kw: (
            denied if name == "denied" else original(kind, name, **kw))
        result = finish.reuse_options(self.gateway, "doc")
        self.assertEqual(result["schedules"], [{"name": "schedule"}])

    def test_denied_unit_does_not_hide_other_unit(self):
        denied = SimpleNamespace(name="denied", check_permission=mock.Mock(side_effect=Denied))
        self.gateway.frappe.get_list.side_effect = lambda kind, **kw: (
            [SimpleNamespace(name="denied"), SimpleNamespace(name="unit")]
            if kind == "Healthcare Service Unit" else [SimpleNamespace(name="schedule")])
        original = self.gateway.frappe.get_doc.side_effect
        self.gateway.frappe.get_doc.side_effect = lambda kind, name, **kw: (
            denied if name == "denied" else original(kind, name, **kw))
        self.assertEqual(finish.reuse_options(self.gateway, "doc")["serviceUnits"], [{"name": "unit"}])

    def test_denied_candidate_messages_are_cleared_without_hiding_other_choices(self):
        original = self.gateway.frappe.get_doc.side_effect
        for kind in ("Healthcare Service Unit", "Practitioner Schedule"):
            with self.subTest(kind=kind):
                response = {}
                self.gateway.frappe.local.response = response
                def denied_permission(_):
                    response["_server_messages"] = "sensitive denied record"
                    raise Denied
                denied = SimpleNamespace(name="denied", check_permission=mock.Mock(side_effect=denied_permission))
                self.gateway.frappe.get_doc.side_effect = lambda dt, name, **kw: (
                    denied if name == "denied" else original(dt, name, **kw))
                self.gateway.frappe.get_list.side_effect = lambda dt, **kw: (
                    [SimpleNamespace(name="denied"), SimpleNamespace(name="unit" if dt == "Healthcare Service Unit" else "schedule")]
                    if dt == kind else [SimpleNamespace(name="unit" if dt == "Healthcare Service Unit" else "schedule")])
                self.gateway.strip_message_channels.side_effect = lambda: response.clear()
                result = finish.reuse_options(self.gateway, "doc")
                self.assertEqual(result["schedules"], [{"name": "schedule"}])
                self.assertEqual(result["serviceUnits"], [{"name": "unit"}])
                self.assertNotIn("_server_messages", response)
                self.gateway.strip_message_channels.assert_called()
                self.gateway.frappe.get_doc.side_effect = original
                self.gateway.strip_message_channels.reset_mock()

    def test_unsupported_candidate_clears_native_messages(self):
        response = {}
        self.gateway.frappe.local.response = response
        self.gateway.strip_message_channels.side_effect = lambda: response.clear()
        def unsupported(_):
            response["_server_messages"] = "sensitive source"
            raise ValueError("unsupported")
        with mock.patch.object(finish.owner_doctor_reuse, "normalized_slots", side_effect=unsupported):
            self.assertEqual(finish.reuse_options(self.gateway, "doc")["schedules"], [])
        self.assertNotIn("_server_messages", response)

    def test_review_delegates_with_reuse_gate(self):
        envelope = {"mode": "reuse", "slotDigest": "a" * 64}
        with mock.patch.object(finish.owner_doctor_reuse, "prepare_review", return_value=envelope) as review:
            self.assertEqual(finish.reuse_review(self.gateway, "doc", "schedule", "unit"), envelope)
        review.assert_called_once_with(self.gateway.frappe, "doc", "schedule", "unit", "company")
        finish._gate.assert_called_once_with(self.gateway, reuse=True)

    def test_invalid_input_and_unsupported_review(self):
        self.assertEqual(finish.reuse_options(self.gateway, ""), (422, {"code": "VALIDATION"}))
        self.assertEqual(finish.reuse_review(self.gateway, "doc", "", "unit"),
                         (422, {"code": "VALIDATION"}))
        with mock.patch.object(finish.owner_doctor_reuse, "prepare_review", side_effect=ValueError):
            self.assertEqual(finish.reuse_review(self.gateway, "doc", "schedule", "unit"),
                             (422, {"code": "VALIDATION"}))

    def test_denied_review_is_not_validation(self):
        with mock.patch.object(finish.owner_doctor_reuse, "prepare_review", side_effect=Denied):
            with self.assertRaises(Denied):
                finish.reuse_review(self.gateway, "doc", "schedule", "unit")

    def test_target_write_denial_blocks_choices_and_review(self):
        def permission(access):
            if access == "write":
                raise Denied
        self.doc.check_permission.side_effect = permission
        with self.assertRaises(Denied):
            finish.reuse_options(self.gateway, "doc")
        with mock.patch.object(finish.owner_doctor_reuse, "prepare_review", return_value={}):
            with self.assertRaises(Denied):
                finish.reuse_review(self.gateway, "doc", "schedule", "unit")


class ReuseGateTests(unittest.TestCase):
    def test_reuse_needs_schedule_read_but_not_create_or_write(self):
        gateway = mock.Mock()
        gateway.site = "site"
        gateway.deny.side_effect = Denied
        gateway.setting.side_effect = lambda key: {
            "clinic_ui_owner_doctor_finish_enabled": True,
            "clinic_ui_owner_doctor_finish_site": "site",
            "clinic_ui_owner_doctor_finish_hooks_reviewed_site": "site",
            "clinic_ui_owner_doctor_finish_company": "company",
            "clinic_ui_owner_doctor_finish_role": "Doctor",
            "clinic_ui_owner_doctor_finish_reuse_enabled": True,
            "clinic_ui_owner_doctor_finish_reuse_site": "site",
            "clinic_ui_owner_doctor_finish_reuse_hooks_reviewed_site": "site",
        }.get(key)
        gateway.fresh_user_roles.return_value = ("System Manager",)
        gateway.owner_staff_create_authorized.return_value = True
        gateway._fields_readable.return_value = True
        gateway.frappe.local.request.headers = {"X-Clinic-Workspace": "MGR", "X-Clinic-Session-Epoch": "epoch"}
        gateway.frappe.db.exists.return_value = False
        gateway.frappe.get_doc.return_value = SimpleNamespace(disabled=0, desk_access=1, check_permission=mock.Mock())
        gateway.frappe.has_permission.side_effect = lambda doctype, access, **kw: not (
            doctype == "Practitioner Schedule" and access in ("create", "write"))
        settings = mock.Mock()
        settings.as_role_map.return_value = {"DOC": ("Doctor",)}
        view = SimpleNamespace(user="owner")
        with mock.patch.object(finish.production_read, "authorize_if_selected",
                               return_value=(settings, view, "MGR")), \
                mock.patch.object(finish.doctor_hours, "check_field_authority") as fields, \
                mock.patch.object(finish.doctor_hours, "_check_fields"), \
                mock.patch.object(finish.owner_doctor_profile, "check_field_authority"):
            self.assertEqual(finish._gate(gateway, reuse=True), ("company", "Doctor", "owner"))
            fields.assert_called_once_with(gateway.frappe, write=False)
            original_setting = gateway.setting.side_effect
            for key, replacement in (("clinic_ui_owner_doctor_finish_reuse_enabled", False),
                                     ("clinic_ui_owner_doctor_finish_reuse_site", "other"),
                                     ("clinic_ui_owner_doctor_finish_reuse_hooks_reviewed_site", None)):
                gateway.setting.side_effect = lambda name, k=key, v=replacement: v if name == k else original_setting(name)
                with self.subTest(key=key), self.assertRaises(Denied):
                    finish._gate(gateway, reuse=True)
                self.assertEqual(finish._gate(gateway, reuse=True, allow_unqualified=True),
                                 ("company", "Doctor", "owner"))
            gateway.setting.side_effect = original_setting
            with self.assertRaises(Denied):
                finish._gate(gateway)
            gateway.setting.side_effect = lambda name: (None if "_reuse_" in name else original_setting(name))
            gateway.frappe.has_permission.side_effect = None
            gateway.frappe.has_permission.return_value = True
            self.assertEqual(finish._gate(gateway), ("company", "Doctor", "owner"))

    def test_unqualified_reuse_denied_even_when_legacy_attestation_is_valid(self):
        gateway = mock.Mock()
        gateway.site = "site"
        gateway.deny.side_effect = Denied
        gateway.setting.side_effect = lambda key: {
            "clinic_ui_owner_doctor_finish_enabled": True,
            "clinic_ui_owner_doctor_finish_site": "site",
            "clinic_ui_owner_doctor_finish_hooks_reviewed_site": "site",
            "clinic_ui_owner_doctor_finish_company": "company",
            "clinic_ui_owner_doctor_finish_role": "Doctor",
        }.get(key)
        with self.assertRaises(Denied):
            finish._gate(gateway, reuse=True)


if __name__ == "__main__":
    unittest.main()
