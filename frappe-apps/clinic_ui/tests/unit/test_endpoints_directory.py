"""Endpoint tests for ``clinic_ui.api.directory.synthetic_patients``.

The endpoint takes no client input: the allowlist, the native authority check
and the current session all come from server-owned state. Tests use a fake
gateway only.
"""

import inspect
import unittest

from clinic_ui.endpoints import directory as directory_endpoint

from .fake_gateway import Denied, FakeGateway, session_view, settings

ALLOWLIST = [
    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
    {"workspace_patient_id": "WS-3", "native_patient_id": "PAT-3"},
]

AUTHORITY = {"PAT-1": "Patient One", "PAT-2": "Patient Two", "PAT-3": "Patient Three"}


def gateway(**overrides):
    values = {
        "site": "marley.test",
        "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
        "current_session": session_view(roles=("Nurse",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_authority = dict(AUTHORITY)
    return fake


class DirectorySuccessTests(unittest.TestCase):
    def test_returns_authorized_synthetic_patients_only(self):
        fake = gateway()
        fake.patient_authority["PAT-2"] = None

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "workspace_patient_id": "WS-1",
                        "native_patient_id": "PAT-1",
                        "display_name": "Patient One",
                    },
                    {
                        "workspace_patient_id": "WS-3",
                        "native_patient_id": "PAT-3",
                        "display_name": "Patient Three",
                    },
                ]
            },
        )
        self.assertEqual(fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"])

    def test_configured_native_ids_not_workspace_ids_are_checked(self):
        fake = gateway()
        directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"])

    def test_marks_the_response_no_store(self):
        fake = gateway()
        directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.no_store_calls, 1)

    def test_empty_allowlist_returns_no_items(self):
        fake = gateway(settings=settings(clinic_ui_synthetic_patients=[]))
        self.assertEqual(directory_endpoint.synthetic_patients(fake), {"items": []})
        self.assertEqual(fake.patient_reads, [])

    def test_malformed_display_name_is_omitted_without_failing_the_read(self):
        fake = gateway()
        fake.patient_authority["PAT-2"] = "bad\x00name"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["native_patient_id"] for item in payload["items"]],
            ["PAT-1", "PAT-3"],
        )

    def test_reception_and_doctor_roles_are_accepted(self):
        for roles in (("Clinic Reception",), ("Physician",)):
            with self.subTest(roles=roles):
                fake = gateway(current_session=session_view(roles=roles))
                payload = directory_endpoint.synthetic_patients(fake)
                self.assertEqual(len(payload["items"]), 3)

    def test_no_client_input_parameter_exists(self):
        parameters = list(
            inspect.signature(directory_endpoint.synthetic_patients).parameters
        )
        self.assertEqual(parameters, ["gateway"])


class DirectoryDenialTests(unittest.TestCase):
    def assertDenied(self, fake):
        with self.assertRaises(Denied):
            directory_endpoint.synthetic_patients(fake)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)

    def test_unconfigured_site_is_denied_before_any_patient_read(self):
        fake = gateway(settings={})
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])

    def test_malformed_allowlist_fails_closed_before_any_patient_read(self):
        fake = gateway(
            settings=settings(
                clinic_ui_synthetic_patients=[
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-2"},
                ]
            )
        )
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])

    def test_guest_administrator_and_missing_sessions_are_denied(self):
        for view in (
            None,
            session_view(sid="Guest"),
            session_view(user="Guest"),
            session_view(user="Administrator"),
            session_view(enabled=False),
        ):
            with self.subTest(view=view):
                self.assertDenied(gateway(current_session=view))

    def test_unmapped_and_ambiguous_users_are_denied(self):
        for view in (
            session_view(roles=("Accounts User",)),
            session_view(roles=("Physician", "Nurse")),
        ):
            with self.subTest(view=view):
                self.assertDenied(gateway(current_session=view))

    def test_native_authority_failure_fails_closed_without_partial_items(self):
        fake = gateway()
        fake.patient_read_error = RuntimeError("native authority unavailable")
        with self.assertRaises(RuntimeError):
            directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.denials, [])

    def test_role_outside_the_first_slice_is_denied_by_configuration(self):
        fake = gateway(settings=settings(clinic_ui_role_map={"PHA": ["Pharmacist"]}))
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])


if __name__ == "__main__":
    unittest.main()
