"""Endpoint tests for ``clinic_ui.api.session.bootstrap`` against a fake gateway.

The fake gateway records ordering and never touches Frappe, a site database or
the network. These tests prove the gate, DTO and no-leak decisions only.
"""

import json
import logging
import unittest

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
