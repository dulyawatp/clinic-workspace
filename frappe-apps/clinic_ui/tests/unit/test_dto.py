"""Pure DTO tests: exact wire shapes, no extra fields, no secrets.

The client contract is mirrored from ``app/src/domain/session.ts`` and
``app/src/features/patients/live-directory.ts``. Frappe wraps the returned
mapping as ``{"message": ...}``; these tests assert the mapping itself.
"""

import unittest

from clinic_ui.core import dto
from clinic_ui.core.capabilities import (
    bootstrap_capabilities,
    read_only_capabilities,
)

EPOCH = "a" * 64


class BootstrapDTOTests(unittest.TestCase):
    def test_bootstrap_shape_is_exact(self):
        payload = dto.build_bootstrap(
            session_epoch=EPOCH,
            user_id="clinician@example.test",
            display_name="Clinician Example",
            role="DOC",
            csrf_token="csrf-token-value",
        )
        self.assertEqual(
            set(payload),
            {"mode", "sessionEpoch", "user", "capabilities", "csrfToken"},
        )
        self.assertEqual(payload["mode"], "live")
        self.assertEqual(payload["sessionEpoch"], EPOCH)
        self.assertEqual(
            payload["user"],
            {
                "id": "clinician@example.test",
                "displayName": "Clinician Example",
                "roles": ["DOC"],
            },
        )
        self.assertEqual(payload["csrfToken"], "csrf-token-value")

    def test_capabilities_are_present_and_never_enabled(self):
        payload = dto.build_bootstrap(
            session_epoch=EPOCH,
            user_id="clinician@example.test",
            display_name="Clinician Example",
            role="DOC",
            csrf_token="csrf-token-value",
        )
        capabilities = payload["capabilities"]
        self.assertTrue(capabilities)
        self.assertEqual(set(capabilities.values()), {"read_only"})

    def test_bootstrap_uses_an_explicit_capability_map(self):
        capabilities = bootstrap_capabilities(
            write_patient_create=True, write_patient_update=True
        )
        payload = dto.build_bootstrap(
            session_epoch=EPOCH,
            user_id="clinician@example.test",
            display_name="Clinician Example",
            role="DOC",
            csrf_token="csrf-token-value",
            capabilities=capabilities,
        )
        self.assertEqual(payload["capabilities"], capabilities)
        self.assertEqual(payload["capabilities"]["write.patient.create"], "enabled")
        self.assertEqual(payload["capabilities"]["write.patient.update"], "enabled")

    def test_bootstrap_refuses_an_invalid_capability_map(self):
        cases = (
            [],
            {"write.patient.create": "enabled"},
            {"read.patient": "not-a-state"},
        )
        cases = cases + (
            {**read_only_capabilities(), "read.patient": "enabled"},
            {**read_only_capabilities(), "write.photo.upload": "enabled"},
        )
        for capabilities in cases:
            with self.subTest(capabilities=capabilities):
                with self.assertRaises(ValueError):
                    dto.build_bootstrap(
                        session_epoch=EPOCH,
                        user_id="clinician@example.test",
                        display_name="Clinician Example",
                        role="DOC",
                        csrf_token="csrf-token-value",
                        capabilities=capabilities,
                    )

    def test_single_role_only(self):
        with self.assertRaises(ValueError):
            dto.build_bootstrap(
                session_epoch=EPOCH,
                user_id="clinician@example.test",
                display_name="Clinician Example",
                role=["DOC", "REC"],
                csrf_token="csrf-token-value",
            )

    def test_unsupported_role_code_is_refused(self):
        for role in ("XYZ", "PHA-LIMITED", "", "doc"):
            with self.subTest(role=role):
                with self.assertRaises(ValueError):
                    dto.build_bootstrap(
                        session_epoch=EPOCH,
                        user_id="clinician@example.test",
                        display_name="Clinician Example",
                        role=role,
                        csrf_token="csrf-token-value",
                    )

    def test_missing_or_oversized_fields_are_refused(self):
        cases = {
            "blank epoch": {"session_epoch": ""},
            "oversized epoch": {"session_epoch": "a" * 129},
            "blank user": {"user_id": "  "},
            "control char user": {"user_id": "user\x00id"},
            "blank display name": {"display_name": "   "},
            "oversized display name": {"display_name": "n" * 257},
            "blank csrf token": {"csrf_token": ""},
            "missing csrf token": {"csrf_token": None},
        }
        for name, overrides in cases.items():
            values = {
                "session_epoch": EPOCH,
                "user_id": "clinician@example.test",
                "display_name": "Clinician Example",
                "role": "DOC",
                "csrf_token": "csrf-token-value",
            }
            values.update(overrides)
            with self.subTest(case=name):
                with self.assertRaises(ValueError):
                    dto.build_bootstrap(**values)


class DirectoryDTOTests(unittest.TestCase):
    def test_directory_shape_is_exact_and_withholds_native_ids(self):
        payload = dto.build_directory(
            [
                dto.patient_item("WS-1", "Patient One"),
                dto.patient_item("WS-2", "Patient Two"),
            ]
        )
        self.assertEqual(set(payload), {"items"})
        self.assertEqual(
            payload["items"],
            [
                {
                    "workspace_patient_id": "WS-1",
                    "display_name": "Patient One",
                },
                {
                    "workspace_patient_id": "WS-2",
                    "display_name": "Patient Two",
                },
            ],
        )
        for item in payload["items"]:
            self.assertNotIn("native_patient_id", item)

    def test_empty_directory_is_valid(self):
        self.assertEqual(dto.build_directory([]), {"items": []})

    def test_patient_item_refuses_malformed_values(self):
        cases = (
            ("", "Patient One"),
            ("WS-1", ""),
            ("WS-1", "bad\x01name"),
            ("W" * 257, "Patient One"),
            ("WS-1", "n" * 257),
            ("WS-1", None),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    dto.patient_item(*case)

    def test_directory_refuses_item_count_over_the_client_limit(self):
        items = [
            dto.patient_item(f"WS-{i}", f"Patient {i}")
            for i in range(dto.MAX_DIRECTORY_ITEMS + 1)
        ]
        with self.assertRaises(ValueError):
            dto.build_directory(items)

    def test_directory_refuses_duplicate_workspace_ids(self):
        with self.assertRaises(ValueError):
            dto.build_directory(
                [
                    dto.patient_item("WS-1", "Patient One"),
                    dto.patient_item("WS-1", "Patient Two"),
                ]
            )


class PatientDetailDTOTests(unittest.TestCase):
    DETAIL = {
        "patient_name": "Patient One",
        "first_name": "Patient",
        "last_name": "One",
        "sex": "Female",
        "dob": "1990-01-02",
        "status": "Active",
        "modified": "2026-11-14T09:30:00.123456",
    }

    def test_detail_shape_is_exact_and_drops_the_native_field_name(self):
        payload = dto.patient_detail("WS-1", self.DETAIL)
        self.assertEqual(
            payload,
            {
                "workspace_patient_id": "WS-1",
                "display_name": "Patient One",
                "first_name": "Patient",
                "last_name": "One",
                "sex": "Female",
                "dob": "1990-01-02",
                "status": "Active",
                "modified": "2026-11-14T09:30:00.123456",
                "editable": False,
            },
        )
        self.assertNotIn("native_patient_id", payload)
        self.assertNotIn("patient_name", payload)

    def test_editable_is_an_explicit_boolean(self):
        self.assertTrue(dto.patient_detail("WS-1", self.DETAIL, editable=True)["editable"])
        self.assertFalse(
            dto.patient_detail("WS-1", self.DETAIL, editable=False)["editable"]
        )
        for value in (1, 0, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dto.patient_detail("WS-1", self.DETAIL, editable=value)

    def test_optional_fields_may_be_null(self):
        payload = dto.patient_detail(
            "WS-1",
            {
                "patient_name": "Patient One",
                "first_name": "Patient",
                "last_name": None,
                "sex": None,
                "dob": None,
                "status": "Active",
                "modified": "2026-11-14T09:30:00.123456",
            },
        )
        self.assertIsNone(payload["last_name"])
        self.assertIsNone(payload["sex"])
        self.assertIsNone(payload["dob"])

    def test_dob_must_be_a_real_calendar_date(self):
        payload = dto.patient_detail(
            "WS-1", {**self.DETAIL, "dob": "2024-02-29"}
        )
        self.assertEqual(payload["dob"], "2024-02-29")

        for dob in (
            "2023-02-29",
            "1990-02-30",
            "1990-2-3",
            "1990-13-01",
            "0000-00-00",
            "not-a-date",
            "1990-01-02T00:00:00",
            " 1990-01-02",
        ):
            with self.subTest(dob=dob):
                with self.assertRaises(ValueError):
                    dto.patient_detail("WS-1", {**self.DETAIL, "dob": dob})

    def test_detail_refuses_missing_extra_or_malformed_values(self):
        cases = (
            ("WS-1", {"patient_name": "Patient One"}),
            ("WS-1", {**self.DETAIL, "extra": "leak"}),
            ("WS-1", {**self.DETAIL, "patient_name": ""}),
            ("WS-1", {**self.DETAIL, "first_name": "bad\x00name"}),
            ("WS-1", {**self.DETAIL, "sex": 5}),
            ("WS-1", {**self.DETAIL, "dob": "x" * 257}),
            ("", self.DETAIL),
            ("bad\nref", self.DETAIL),
            (None, self.DETAIL),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    dto.patient_detail(*case)


    def test_write_result_status_and_patient_shape(self):
        for status in ("created", "updated"):
            with self.subTest(status=status):
                payload = dto.patient_write_result(
                    status, "WS-1", self.DETAIL
                )
                self.assertEqual(payload["status"], status)
                self.assertEqual(set(payload), {"status", "patient"})
                self.assertEqual(
                    payload["patient"],
                    dto.patient_detail("WS-1", self.DETAIL),
                )
                self.assertEqual(
                    set(payload["patient"]),
                    {
                        "workspace_patient_id",
                        "display_name",
                        "first_name",
                        "last_name",
                        "sex",
                        "dob",
                        "status",
                        "modified",
                        "editable",
                    },
                )
                self.assertFalse(payload["patient"]["editable"])

    def test_write_result_refuses_unknown_status_or_bad_detail(self):
        with self.assertRaises(ValueError):
            dto.patient_write_result("ok", "WS-1", self.DETAIL)
        with self.assertRaises(ValueError):
            dto.patient_write_result(
                "created", "WS-1", {"patient_name": "P"}
            )

    def test_modified_is_required_and_bounded(self):
        for value in (None, "", "x" * 65, "not a datetime"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dto.patient_detail(
                        "WS-1", {**self.DETAIL, "modified": value}
                    )


class AppointmentDTOTests(unittest.TestCase):
    ITEM = {
        "workspace_patient_id": "WS-1",
        "appointment_id": "APPT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open",
    }

    def test_appointment_shape_is_exactly_the_five_contract_fields(self):
        payload = dto.build_appointments([dto.appointment_item(**self.ITEM)])
        self.assertEqual(payload, {"items": [self.ITEM]})
        for item in payload["items"]:
            self.assertNotIn("native_patient_id", item)
            self.assertNotIn("native_appointment_id", item)

    def test_empty_appointment_list_is_valid(self):
        self.assertEqual(dto.build_appointments([]), {"items": []})

    def test_appointment_item_refuses_malformed_values(self):
        cases = (
            {**self.ITEM, "workspace_patient_id": ""},
            {**self.ITEM, "appointment_id": "bad\x01id"},
            {**self.ITEM, "appointment_date": "2026-02-30"},
            {**self.ITEM, "appointment_date": "20-11-2026"},
            {**self.ITEM, "appointment_time": "25:00:00"},
            {**self.ITEM, "appointment_time": "9:30"},
            {**self.ITEM, "status": ""},
            {**self.ITEM, "status": "<bad>"},
        )
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    dto.appointment_item(**item)

    def test_appointment_list_refuses_extra_duplicate_or_oversized_items(self):
        extra = {**self.ITEM, "native_patient_id": "PAT-1"}
        with self.assertRaises(ValueError):
            dto.build_appointments([extra])
        with self.assertRaises(ValueError):
            dto.build_appointments([self.ITEM, self.ITEM])
        with self.assertRaises(ValueError):
            dto.build_appointments(
                [
                    dto.appointment_item(
                        **{**self.ITEM, "appointment_id": f"APPT-{index}"}
                    )
                    for index in range(dto.MAX_APPOINTMENT_ITEMS + 1)
                ]
            )


class AppointmentDetailDTOTests(unittest.TestCase):
    ITEM = {
        "workspace_patient_id": "WS-1",
        "appointment_id": "wsa1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16,
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open",
        "modified": "2026-11-14 09:30:00.123456",
    }

    def test_detail_adds_only_the_version(self):
        self.assertEqual(
            dto.appointment_detail(**self.ITEM),
            self.ITEM,
        )

    def test_read_contract_adds_explicit_action_flags(self):
        payload = dto.appointment_read(
            dto.appointment_detail(**self.ITEM),
            can_reschedule=True,
            can_checkin=False,
        )
        self.assertEqual(
            set(payload),
            {
                "workspace_patient_id",
                "appointment_id",
                "appointment_date",
                "appointment_time",
                "status",
                "modified",
                "can_reschedule",
                "can_checkin",
            },
        )
        self.assertIs(payload["can_reschedule"], True)
        self.assertIs(payload["can_checkin"], False)

    def test_action_flags_must_be_explicit_booleans(self):
        for value in (1, "true", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dto.appointment_read(
                        dto.appointment_detail(**self.ITEM),
                        can_reschedule=value,
                        can_checkin=False,
                    )

    def test_detail_requires_a_valid_modified_token(self):
        for value in (None, "", "not-a-date", "x" * 65):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    dto.appointment_detail(**{**self.ITEM, "modified": value})

    def test_write_result_shape_and_statuses(self):
        detail = dto.appointment_detail(**self.ITEM)
        for status in (
            "booked",
            "rescheduled",
            "checked_in",
            "cancelled",
            "no_show",
        ):
            with self.subTest(status=status):
                payload = dto.appointment_write_result(status, detail)
                self.assertEqual(set(payload), {"status", "appointment"})
                self.assertEqual(payload["status"], status)
                self.assertEqual(payload["appointment"], detail)
                self.assertNotIn("ackToken", payload)
                self.assertNotIn("native_appointment_id", repr(payload))

    def test_write_result_refuses_an_unknown_status_or_detail(self):
        detail = dto.appointment_detail(**self.ITEM)
        for args in (("cancel", detail), ("no-show", detail)):
            with self.subTest(args=str(args)[:24]):
                with self.assertRaises(ValueError):
                    dto.appointment_write_result(*args)
        with self.assertRaises(ValueError):
            dto.appointment_write_result("booked", {"status": "Open"})

    def test_detail_refuses_extra_or_missing_fields(self):
        with self.assertRaises(TypeError):
            dto.appointment_detail(
                **{**self.ITEM, "native_appointment_id": "APPT-1"}
            )
        missing = {key: value for key, value in self.ITEM.items() if key != "modified"}
        with self.assertRaises(TypeError):
            dto.appointment_detail(**missing)


class TextValidationTests(unittest.TestCase):
    def test_valid_text_rules(self):
        self.assertTrue(dto.is_valid_text("Patient One"))
        self.assertTrue(dto.is_valid_text(" พีรพล "))

    def test_invalid_text_rules(self):
        for value in ("", "   ", None, 5, ["a"], "bad\x7fname", "x" * 257):
            with self.subTest(value=value):
                self.assertFalse(dto.is_valid_text(value))


if __name__ == "__main__":
    unittest.main()
