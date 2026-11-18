"""Endpoint tests for ``clinic_ui.api.session.bootstrap`` against a fake gateway.

The fake gateway records ordering and never touches Frappe, a site database or
the network. These tests prove the gate, DTO and no-leak decisions only.
"""

import json
import logging
import unittest

from clinic_ui.core import policy
from clinic_ui.core.config import APPROVED_WRITE_SITE
from clinic_ui.core.epoch import session_epoch
from clinic_ui.endpoints import session as session_endpoint

from .fake_gateway import SECRET, Denied, FakeGateway, session_view, settings

SID = "sid-abc123"
TOKEN = "csrf-token-value"


def gateway(**overrides):
    values = {
        "site": "marley.test",
        "settings": settings(),
        "current_session": session_view(sid=SID),
        "csrf_token": TOKEN,
    }
    values.update(overrides)
    return FakeGateway(**values)


class BootstrapSuccessTests(unittest.TestCase):
    def test_returns_live_bootstrap_for_a_mapped_role(self):
        fake = gateway()
        payload = session_endpoint.bootstrap(fake)

        self.assertEqual(payload["mode"], "live")
        self.assertEqual(
            payload["user"],
            {
                "id": "nurse@example.test",
                "displayName": "Nurse Example",
                "roles": ["NUR"],
            },
        )
        self.assertEqual(payload["csrfToken"], TOKEN)
        self.assertEqual(
            payload["sessionEpoch"], session_epoch(SECRET.encode(), "marley.test", SID)
        )
        self.assertEqual(set(payload["capabilities"].values()), {"read_only"})

    def test_marks_the_response_no_store_even_on_success(self):
        fake = gateway()
        session_endpoint.bootstrap(fake)
        self.assertEqual(fake.no_store_calls, 1)

    def test_does_not_read_any_patient_record(self):
        fake = gateway()
        session_endpoint.bootstrap(fake)
        self.assertEqual(fake.patient_reads, [])

    def test_epoch_is_stable_for_the_same_session_and_differs_for_another(self):
        first = session_endpoint.bootstrap(gateway())
        second = session_endpoint.bootstrap(gateway())
        other = session_endpoint.bootstrap(
            gateway(current_session=session_view(sid="sid-other"))
        )
        self.assertEqual(first["sessionEpoch"], second["sessionEpoch"])
        self.assertNotEqual(first["sessionEpoch"], other["sessionEpoch"])

    def test_payload_contains_no_session_cookie_or_secret(self):
        fake = gateway()
        payload = session_endpoint.bootstrap(fake)
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn(SID, serialized)
        self.assertNotIn(SECRET, serialized)
        for key in payload:
            self.assertNotIn("sid", key.lower())
            self.assertNotIn("cookie", key.lower())
            self.assertNotIn("secret", key.lower())


class DenialAssertions(unittest.TestCase):
    def assertDenied(self, fake):
        with self.assertRaises(Denied):
            session_endpoint.bootstrap(fake)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)


class AppointmentCapabilityTests(unittest.TestCase):
    """Bootstrap advertises appointment writes only after the full gate."""

    def appointment_gateway(self, **overrides):
        values = {
            "site": APPROVED_WRITE_SITE,
            "settings": settings(
                clinic_ui_synthetic_site=APPROVED_WRITE_SITE,
                clinic_ui_synthetic_patients=[
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
                ],
                clinic_ui_native_appointment_write_enabled=1,
                clinic_ui_native_appointment_write_site=APPROVED_WRITE_SITE,
                clinic_ui_appointment_medical_department="MEDDEP-1",
                clinic_ui_appointment_company="COMP-1",
                clinic_ui_appointment_type="APPT-TYPE-1",
                clinic_ui_appointment_checkin_patient="WS-2",
            ),
            "current_session": session_view(
                sid=SID, user="reception@example.test", roles=("Clinic Reception",)
            ),
            "csrf_token": TOKEN,
        }
        values.update(overrides)
        return FakeGateway(**values)

    def capabilities(self, fake):
        return session_endpoint.bootstrap(fake)["capabilities"]

    def test_enables_the_five_appointment_writes_only(self):
        capabilities = self.capabilities(self.appointment_gateway())
        for name in (
            "write.appointment.book",
            "write.appointment.reschedule",
            "write.appointment.cancel",
            "write.appointment.no_show",
            "write.appointment.checkin",
        ):
            self.assertEqual(capabilities[name], "enabled")
        self.assertEqual(capabilities["write.patient.create"], "read_only")
        self.assertEqual(capabilities["write.admin.master"], "read_only")

    def test_every_gate_failure_leaves_appointment_writes_read_only(self):
        cases = {
            "flag off": {"settings": settings(
                clinic_ui_synthetic_site=APPROVED_WRITE_SITE,
                clinic_ui_native_appointment_write_enabled=0,
            )},
            "unsafe settings": {"appointment_side_effects_unsafe": True},
            "no create authority": {"appointment_can_create": False},
            "no write authority": {"appointment_can_write": False},
            "no linked read": {"doctype_reads": {"Medical Department": False}},
            "unreviewed type mode": {
                "appointment_type_contract": {
                    "appointment_type": "APPT-TYPE-1",
                    "default_duration": 15,
                    "allow_booking_for": "Practitioner",
                }
            },
            "boundary drift": {"appointment_boundary_problem": "drift"},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                if "settings" in override:
                    fake = self.appointment_gateway(settings=override["settings"])
                else:
                    fake = self.appointment_gateway()
                for key, value in override.items():
                    if key == "settings":
                        continue
                    setattr(fake, key, value)
                capabilities = self.capabilities(fake)
                self.assertEqual(capabilities["write.appointment.book"], "read_only")
                self.assertEqual(
                    capabilities["write.appointment.reschedule"], "read_only"
                )
                self.assertEqual(
                    capabilities["write.appointment.checkin"], "read_only"
                )

    def test_the_historical_fence_does_not_gate_appointment_writes(self):
        fake = self.appointment_gateway()
        fake.deployment_approved = False
        fake.fence_ready = False
        self.assertEqual(
            self.capabilities(fake)["write.appointment.book"], "enabled"
        )

    def test_a_doctor_session_can_receive_the_same_surface(self):
        fake = self.appointment_gateway(
            current_session=session_view(
                sid=SID, user="doctor@example.test", roles=("Physician",)
            )
        )
        self.assertEqual(
            self.capabilities(fake)["write.appointment.book"], "enabled"
        )

    def test_a_nurse_session_never_receives_appointment_writes(self):
        fake = self.appointment_gateway(
            current_session=session_view(sid=SID, roles=("Nurse",))
        )
        capabilities = self.capabilities(fake)
        self.assertEqual(capabilities["write.appointment.book"], "read_only")


class BootstrapDenialTests(DenialAssertions):
    def test_unconfigured_site_is_denied_before_csrf(self):
        fake = gateway(settings={})
        self.assertDenied(fake)
        self.assertEqual(fake.csrf_calls, 0)

    def test_other_site_configuration_is_denied(self):
        self.assertDenied(gateway(settings=settings(clinic_ui_synthetic_site="lab.test")))

    def test_invalid_role_map_is_denied(self):
        self.assertDenied(
            gateway(
                settings=settings(
                    clinic_ui_role_map={"DOC": ["Physician"], "NUR": ["Physician"]}
                )
            )
        )

    def test_absent_session_returns_anonymous_before_csrf(self):
        fake = gateway(current_session=None)
        self.assertEqual(session_endpoint.bootstrap(fake), {"code": "AUTH_EXPIRED"})
        self.assertEqual(fake.csrf_calls, 0)
        self.assertEqual(fake.denials, [])

    def test_authenticated_but_unusable_sessions_are_denied(self):
        for candidate in (
            session_view(user="Administrator"),
            session_view(enabled=False),
        ):
            with self.subTest(view=candidate):
                self.assertDenied(gateway(current_session=candidate))

    def test_unmapped_user_is_denied(self):
        self.assertDenied(gateway(current_session=session_view(roles=("Accounts User",))))

    def test_ambiguous_clinic_roles_are_denied(self):
        self.assertDenied(
            gateway(current_session=session_view(roles=("Physician", "Nurse")))
        )

    def test_missing_csrf_token_fails_closed(self):
        for token in (None, "", "   "):
            with self.subTest(token=token):
                fake = gateway(csrf_token=token)
                self.assertDenied(fake)
                self.assertEqual(fake.csrf_calls, 1)

    def test_absent_epoch_secret_fails_closed(self):
        self.assertDenied(gateway(settings=settings(clinic_ui_epoch_secret="")))

    def test_denial_does_not_read_any_patient_record(self):
        fake = gateway(settings={})
        with self.assertRaises(Denied):
            session_endpoint.bootstrap(fake)
        self.assertEqual(fake.patient_reads, [])


class AnonymousDispatchTests(DenialAssertions):
    """Guest dispatch returns a fixed 401; authenticated denial stays a 403."""

    def assertAnonymous(self, fake):
        payload = session_endpoint.bootstrap(fake)
        self.assertEqual(payload, {"code": "AUTH_EXPIRED"})
        self.assertEqual(fake.anonymous_calls, 1)
        self.assertEqual(fake.denials, [])
        self.assertEqual(fake.no_store_calls, 1)
        self.assertEqual(fake.csrf_calls, 0)
        self.assertEqual(fake.patient_reads, [])
        return payload

    def test_missing_session_returns_anonymous(self):
        self.assertAnonymous(gateway(current_session=None))

    def test_guest_sid_and_guest_user_return_anonymous(self):
        for view in (
            session_view(sid="Guest"),
            session_view(sid=""),
            session_view(user="Guest"),
            session_view(user=" "),
        ):
            with self.subTest(view=view):
                self.assertAnonymous(gateway(current_session=view))

    def test_anonymous_payload_is_fixed_and_carries_no_identity(self):
        payload = self.assertAnonymous(
            gateway(current_session=session_view(user="Guest", roles=("Nurse",)))
        )
        self.assertEqual(list(payload), ["code"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("nurse@example.test", serialized)
        self.assertNotIn("Nurse", serialized)
        for key in ("user", "roles", "sessionEpoch", "capabilities", "csrfToken"):
            self.assertNotIn(key, payload)

    def test_anonymous_does_not_read_the_site_configuration(self):
        fake = gateway(settings={}, current_session=None)
        fake.setting = lambda key: self.fail("configuration read for a guest")
        self.assertAnonymous(fake)

    def test_administrator_denial_stays_forbidden_not_anonymous(self):
        fake = gateway(current_session=session_view(user="Administrator"))
        self.assertDenied(fake)
        self.assertEqual(fake.anonymous_calls, 0)

    def test_disabled_user_denial_stays_forbidden_not_anonymous(self):
        fake = gateway(current_session=session_view(enabled=False))
        self.assertDenied(fake)
        self.assertEqual(fake.anonymous_calls, 0)

    def test_authenticated_configuration_denial_stays_forbidden(self):
        fake = gateway(settings={})
        self.assertDenied(fake)
        self.assertEqual(fake.anonymous_calls, 0)

    def test_authenticated_role_denial_stays_forbidden(self):
        fake = gateway(current_session=session_view(roles=("Accounts User",)))
        self.assertDenied(fake)
        self.assertEqual(fake.anonymous_calls, 0)

    def test_live_session_never_reports_anonymous(self):
        fake = gateway()
        session_endpoint.bootstrap(fake)
        self.assertEqual(fake.anonymous_calls, 0)


class BootstrapWriteCapabilityTests(unittest.TestCase):
    """Patient writes are enabled only through the whole reviewed local gate."""

    def write_gateway(self, **overrides):
        values = {
            "site": APPROVED_WRITE_SITE,
            "settings": settings(
                clinic_ui_synthetic_site=APPROVED_WRITE_SITE,
                clinic_ui_native_patient_write_site=APPROVED_WRITE_SITE,
            ),
            "current_session": session_view(roles=("Nurse",)),
            "csrf_token": TOKEN,
        }
        values.update(overrides)
        return FakeGateway(**values)

    def assert_only_patient_writes_enabled(self, payload):
        capabilities = payload["capabilities"]
        self.assertEqual(capabilities["write.patient.create"], "enabled")
        self.assertEqual(capabilities["write.patient.update"], "enabled")
        self.assertEqual(
            {
                name
                for name, state in capabilities.items()
                if state != "read_only"
            },
            {"write.patient.create", "write.patient.update"},
        )

    def assert_all_read_only(self, payload):
        self.assertEqual(set(payload["capabilities"].values()), {"read_only"})

    def test_bootstrap_uses_the_shared_policy_write_role_codes(self):
        self.assertEqual(
            session_endpoint.PATIENT_WRITE_ROLE_CODES,
            policy.PATIENT_WRITE_ROLE_CODES,
        )

    def test_nurse_on_approved_site_with_all_gates_enables_patient_writes(self):
        payload = session_endpoint.bootstrap(self.write_gateway())
        self.assert_only_patient_writes_enabled(payload)

    def test_physician_role_also_enables_patient_writes(self):
        payload = session_endpoint.bootstrap(
            self.write_gateway(
                current_session=session_view(roles=("Physician",))
            )
        )
        self.assert_only_patient_writes_enabled(payload)

    def test_reception_role_stays_read_only(self):
        payload = session_endpoint.bootstrap(
            self.write_gateway(
                current_session=session_view(roles=("Clinic Reception",))
            )
        )
        self.assert_all_read_only(payload)

    def test_any_other_site_stays_read_only(self):
        payload = session_endpoint.bootstrap(gateway())
        self.assert_all_read_only(payload)

    def test_write_flag_off_stays_read_only(self):
        payload = session_endpoint.bootstrap(
            self.write_gateway(
                settings=settings(
                    clinic_ui_synthetic_site=APPROVED_WRITE_SITE,
                    clinic_ui_native_patient_write_enabled=0,
                    clinic_ui_native_patient_write_site=APPROVED_WRITE_SITE,
                )
            )
        )
        self.assert_all_read_only(payload)

    def test_a_missing_deployment_marker_no_longer_blocks_patient_writes(self):
        # The converted development lane relies on native authority, not the
        # historical fence anchor.
        fake = self.write_gateway()
        fake.deployment_approved = False
        self.assert_only_patient_writes_enabled(
            session_endpoint.bootstrap(fake)
        )

    def test_an_unresolved_fence_no_longer_blocks_patient_writes(self):
        fake = self.write_gateway()
        fake.fence_ready = False
        self.assert_only_patient_writes_enabled(
            session_endpoint.bootstrap(fake)
        )

    def test_unsafe_healthcare_settings_stay_read_only(self):
        fake = self.write_gateway()
        fake.side_effects_unsafe = True
        self.assert_all_read_only(session_endpoint.bootstrap(fake))

    def test_missing_create_permission_disables_only_create(self):
        fake = self.write_gateway()
        fake.can_create = False
        capabilities = session_endpoint.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.patient.create"], "read_only")
        self.assertEqual(capabilities["write.patient.update"], "enabled")

    def test_missing_update_permission_disables_only_update(self):
        fake = self.write_gateway()
        fake.can_write = False
        capabilities = session_endpoint.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.patient.create"], "enabled")
        self.assertEqual(capabilities["write.patient.update"], "read_only")

    def test_capability_check_error_fails_closed(self):
        fake = self.write_gateway()

        def boom():
            raise RuntimeError("gate check failed")

        fake.healthcare_settings_side_effects_unsafe = boom
        self.assert_all_read_only(session_endpoint.bootstrap(fake))

    def test_enabling_writes_does_not_read_any_patient_record(self):
        fake = self.write_gateway()
        session_endpoint.bootstrap(fake)
        self.assertEqual(fake.patient_reads, [])


class NoSecretLoggingTests(unittest.TestCase):
    def test_no_session_id_csrf_or_secret_is_logged(self):
        records = []

        class Recorder(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        handler = Recorder()
        root = logging.getLogger()
        root.addHandler(handler)
        previous = root.level
        root.setLevel(logging.DEBUG)
        try:
            session_endpoint.bootstrap(gateway())
            with self.assertRaises(Denied):
                session_endpoint.bootstrap(gateway(settings={}))
        finally:
            root.removeHandler(handler)
            root.setLevel(previous)

        joined = "\n".join(records)
        self.assertNotIn(SID, joined)
        self.assertNotIn(TOKEN, joined)
        self.assertNotIn(SECRET, joined)


if __name__ == "__main__":
    unittest.main()
