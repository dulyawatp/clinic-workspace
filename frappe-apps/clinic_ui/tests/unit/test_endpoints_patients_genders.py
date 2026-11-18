"""Endpoint tests for ``clinic_ui.api.patients.genders``.

The gender options are the site's own ``Gender`` master names readable by the
*current* user. Tests use a fake gateway only: gate order, the approved native
site scope and fail-closed behaviour are asserted, not real Frappe permission
semantics. No option is ever invented and no write surface is touched.
"""

import inspect
import unittest

from clinic_ui.core import config
from clinic_ui.endpoints import patients as patients_endpoint

from .fake_gateway import Denied, FakeGateway, session_view, settings

DEV_SITE = config.APPROVED_WRITE_SITE


def dev_gateway(**overrides):
    values = {
        "site": DEV_SITE,
        "settings": settings(clinic_ui_synthetic_site=DEV_SITE),
        "current_session": session_view(roles=("Physician",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.gender_options = ["Female", "Male", "Other"]
    return fake


class GenderOptionsSuccessTests(unittest.TestCase):
    def test_returns_the_site_names_unchanged(self):
        fake = dev_gateway()

        payload = patients_endpoint.genders(fake)

        self.assertEqual(payload, {"items": ["Female", "Male", "Other"]})
        self.assertEqual(fake.gender_option_calls, [patients_endpoint.MAX_GENDER_OPTIONS])

    def test_marks_the_response_no_store(self):
        fake = dev_gateway()
        patients_endpoint.genders(fake)
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)

    def test_a_single_master_name_is_returned_as_a_list(self):
        fake = dev_gateway()
        fake.gender_options = ["Female"]
        self.assertEqual(
            patients_endpoint.genders(fake), {"items": ["Female"]}
        )

    def test_an_empty_master_yields_no_options(self):
        fake = dev_gateway()
        fake.gender_options = []
        self.assertEqual(patients_endpoint.genders(fake), {"items": []})

    def test_every_patient_read_role_passes_the_clinic_gate(self):
        for roles in (("Physician",), ("Nurse",), ("Clinic Reception",)):
            with self.subTest(roles=roles):
                fake = dev_gateway(current_session=session_view(roles=roles))
                self.assertEqual(
                    patients_endpoint.genders(fake)["items"],
                    ["Female", "Male", "Other"],
                )

    def test_signature_takes_the_gateway_and_nothing_else(self):
        parameters = list(inspect.signature(patients_endpoint.genders).parameters)
        self.assertEqual(parameters, ["gateway"])

    def test_the_read_never_touches_the_patient_write_surface(self):
        fake = dev_gateway()
        patients_endpoint.genders(fake)
        self.assertEqual(fake.prepare_calls, [])
        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.update_calls, [])
        self.assertEqual(fake.write_responses, [])


class GenderOptionsDenialTests(unittest.TestCase):
    def assertDenied(self, fake, option_calls=()):
        with self.assertRaises(Denied):
            patients_endpoint.genders(fake)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)
        self.assertEqual(fake.gender_option_calls, list(option_calls))

    def test_unconfigured_site_is_denied_before_any_master_read(self):
        self.assertDenied(dev_gateway(settings={}))

    def test_guest_administrator_and_missing_sessions_are_denied(self):
        for view in (
            None,
            session_view(sid="Guest"),
            session_view(user="Guest"),
            session_view(user="Administrator"),
            session_view(enabled=False),
        ):
            with self.subTest(view=view):
                self.assertDenied(dev_gateway(current_session=view))

    def test_unmapped_and_ambiguous_users_are_denied(self):
        for view in (
            session_view(roles=("Accounts User",)),
            session_view(roles=("Physician", "Nurse")),
        ):
            with self.subTest(view=view):
                self.assertDenied(dev_gateway(current_session=view))

    def test_a_site_outside_the_native_scope_is_denied_before_any_read(self):
        fake = dev_gateway(
            site="marley.test",
            settings=settings(clinic_ui_synthetic_site="marley.test"),
        )
        self.assertDenied(fake)

    def test_a_refused_or_failed_master_read_is_denied(self):
        fake = dev_gateway()
        fake.gender_options = None
        self.assertDenied(fake, option_calls=[patients_endpoint.MAX_GENDER_OPTIONS])

    def test_a_malformed_master_response_is_denied(self):
        for value in ("Female", {"items": ["Female"]}, 5):
            with self.subTest(value=value):
                fake = dev_gateway()
                fake.gender_options = value
                self.assertDenied(
                    fake, option_calls=[patients_endpoint.MAX_GENDER_OPTIONS]
                )


if __name__ == "__main__":
    unittest.main()
