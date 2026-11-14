"""Pure DTO tests: exact wire shapes, no extra fields, no secrets.

The client contract is mirrored from ``app/src/domain/session.ts`` and
``app/src/features/patients/live-directory.ts``. Frappe wraps the returned
mapping as ``{"message": ...}``; these tests assert the mapping itself.
"""

import unittest

from clinic_ui.core import dto

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
        for role in ("PHA", "MGR", "ADM", "", "doc"):
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
    def test_directory_shape_is_exact(self):
        payload = dto.build_directory(
            [
                dto.patient_item("WS-1", "PAT-1", "Patient One"),
                dto.patient_item("WS-2", "PAT-2", "Patient Two"),
            ]
        )
        self.assertEqual(set(payload), {"items"})
        self.assertEqual(
            payload["items"],
            [
                {
                    "workspace_patient_id": "WS-1",
                    "native_patient_id": "PAT-1",
                    "display_name": "Patient One",
                },
                {
                    "workspace_patient_id": "WS-2",
                    "native_patient_id": "PAT-2",
                    "display_name": "Patient Two",
                },
            ],
        )

    def test_empty_directory_is_valid(self):
        self.assertEqual(dto.build_directory([]), {"items": []})

    def test_patient_item_refuses_malformed_values(self):
        cases = (
            ("", "PAT-1", "Patient One"),
            ("WS-1", "", "Patient One"),
            ("WS-1", "PAT-1", ""),
            ("WS-1", "PAT-1", "bad\x01name"),
            ("W" * 257, "PAT-1", "Patient One"),
            ("WS-1", "PAT-1", "n" * 257),
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    dto.patient_item(*case)

    def test_directory_refuses_item_count_over_the_client_limit(self):
        items = [
            dto.patient_item(f"WS-{i}", f"PAT-{i}", f"Patient {i}")
            for i in range(dto.MAX_DIRECTORY_ITEMS + 1)
        ]
        with self.assertRaises(ValueError):
            dto.build_directory(items)

    def test_directory_refuses_duplicate_workspace_ids(self):
        with self.assertRaises(ValueError):
            dto.build_directory(
                [
                    dto.patient_item("WS-1", "PAT-1", "Patient One"),
                    dto.patient_item("WS-1", "PAT-2", "Patient Two"),
                ]
            )


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
