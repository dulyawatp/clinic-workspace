"""Adapter tests for ``clinic_ui.runtime.frappe_gateway``.

A minimal fake Frappe module is injected, so these tests exercise the real
adapter code paths without an installed Frappe, a site or the network. They
prove the dispatch mechanics (401 status, no-store header, generic denial,
site-config source and per-patient authority) — not real permission semantics.
"""

import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from clinic_ui.runtime import frappe_gateway


class FakePermissionError(Exception):
    pass


class FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []

    def get_value(self, doctype, name, fieldname=None):
        self.calls.append((doctype, name, fieldname))
        key = (doctype, name, fieldname)
        return self.values.get(key)


class FakeFrappe(types.SimpleNamespace):
    def __init__(self, site="marley.test", site_path=None, session=None):
        super().__init__()
        self.local = types.SimpleNamespace(
            site=site,
            session={},
            response={},
            response_headers={},
        )
        self.session = session or types.SimpleNamespace(user="Guest", sid="Guest")
        self.db = FakeDB()
        self.PermissionError = FakePermissionError
        self.permission_calls = []
        self._site_path = site_path
        self.roles = ()

    def get_site_path(self, *parts):
        if self._site_path is None:
            raise FileNotFoundError("no site path")
        return str(Path(self._site_path).joinpath(*parts))

    def get_roles(self, user=None):
        return list(self.roles)

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        self.permission_calls.append((doctype, ptype, doc, user))
        return bool(self.db.values.get(("__perm__", doc, ptype), False))


class GatewayTestCase(unittest.TestCase):
    def gateway(self, stub):
        return frappe_gateway.Gateway(frappe_module=stub)


class AnonymousResponseAdapterTests(GatewayTestCase):
    def test_respond_anonymous_sets_401_and_returns_a_fixed_code(self):
        stub = FakeFrappe()
        gateway = self.gateway(stub)

        payload = gateway.respond_anonymous()

        self.assertEqual(payload, {"code": "AUTH_EXPIRED"})
        self.assertEqual(stub.local.response["http_status_code"], 401)
        self.assertEqual(list(stub.local.response), ["http_status_code"])

    def test_mark_no_store_sets_the_header(self):
        stub = FakeFrappe()
        self.gateway(stub).mark_no_store()
        self.assertEqual(stub.local.response_headers["Cache-Control"], "no-store")

    def test_deny_raises_a_generic_permission_error(self):
        stub = FakeFrappe()
        with self.assertRaises(FakePermissionError) as raised:
            self.gateway(stub).deny()
        self.assertEqual(str(raised.exception), "Not permitted")

    def test_guest_session_is_reported_without_identity_or_db_reads(self):
        stub = FakeFrappe()
        view = self.gateway(stub).current_session
        self.assertEqual(view.user, "Guest")
        self.assertEqual(view.full_name, "")
        self.assertFalse(view.enabled)
        self.assertEqual(view.roles, ())
        self.assertEqual(stub.db.calls, [])


class SessionAdapterTests(GatewayTestCase):
    def test_live_session_is_mapped(self):
        stub = FakeFrappe(
            session=types.SimpleNamespace(user="nurse@example.test", sid="sid-abc123")
        )
        stub.db.values[("User", "nurse@example.test", "full_name")] = "Nurse Example"
        stub.db.values[("User", "nurse@example.test", "enabled")] = 1
        stub.roles = ("Nurse", "All")

        view = self.gateway(stub).current_session

        self.assertEqual(view.sid, "sid-abc123")
        self.assertEqual(view.user, "nurse@example.test")
        self.assertEqual(view.full_name, "Nurse Example")
        self.assertTrue(view.enabled)
        self.assertEqual(view.roles, ("Nurse", "All"))

    def test_missing_local_session_is_none(self):
        stub = FakeFrappe()
        stub.local.session = None
        self.assertIsNone(self.gateway(stub).current_session)


class SiteConfigSourceTests(GatewayTestCase):
    def test_values_come_from_the_sites_own_site_config(self):
        with TemporaryDirectory() as directory:
            Path(directory, "site_config.json").write_text(
                json.dumps({"clinic_ui_enabled": 1}), encoding="utf-8"
            )
            stub = FakeFrappe(site_path=directory)
            gateway = self.gateway(stub)
            self.assertEqual(gateway.setting("clinic_ui_enabled"), 1)
            self.assertIsNone(gateway.setting("absent_key"))

    def test_unreadable_configuration_yields_no_settings(self):
        stub = FakeFrappe(site_path=None)
        self.assertIsNone(self.gateway(stub).setting("clinic_ui_enabled"))


class CsrfAdapterTests(GatewayTestCase):
    def install_sessions(self, function):
        sessions = types.ModuleType("frappe.sessions")
        sessions.get_csrf_token = function
        stub = FakeFrappe()
        stub.sessions = sessions
        previous = sys.modules.get("frappe.sessions")
        previous_parent = sys.modules.get("frappe")
        sys.modules["frappe"] = stub
        sys.modules["frappe.sessions"] = sessions
        return stub, previous, previous_parent

    def restore(self, previous, previous_parent):
        for name, value in (
            ("frappe.sessions", previous),
            ("frappe", previous_parent),
        ):
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def test_token_is_returned(self):
        stub, previous, previous_parent = self.install_sessions(
            lambda: "csrf-token-value"
        )
        try:
            self.assertEqual(self.gateway(stub).csrf_token(), "csrf-token-value")
        finally:
            self.restore(previous, previous_parent)

    def test_absent_or_blank_token_fails_closed(self):
        for function in (lambda: None, lambda: "   ", lambda: 5):
            stub, previous, previous_parent = self.install_sessions(function)
            try:
                with self.subTest(function=function):
                    self.assertIsNone(self.gateway(stub).csrf_token())
            finally:
                self.restore(previous, previous_parent)

    def test_generation_failure_fails_closed(self):
        def boom():
            raise RuntimeError("session store unavailable")

        stub, previous, previous_parent = self.install_sessions(boom)
        try:
            self.assertIsNone(self.gateway(stub).csrf_token())
        finally:
            self.restore(previous, previous_parent)


class NativePatientAuthorityTests(GatewayTestCase):
    def stub_with_patient(self, permitted, display_name):
        stub = FakeFrappe(
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            )
        )
        stub.db.values[("__perm__", "PAT-1", "read")] = permitted
        stub.db.values[("Patient", "PAT-1", "patient_name")] = display_name
        return stub

    def test_authorized_patient_returns_display_name(self):
        stub = self.stub_with_patient(True, "Patient One")
        gateway = self.gateway(stub)
        self.assertEqual(gateway.native_patient_display_name("PAT-1"), "Patient One")
        self.assertEqual(
            stub.permission_calls, [("Patient", "read", "PAT-1", "doctor@example.test")]
        )
        self.assertEqual(
            stub.db.calls, [("Patient", "PAT-1", "patient_name")]
        )

    def test_denied_patient_returns_none_without_reading_the_record(self):
        stub = self.stub_with_patient(False, "Patient One")
        gateway = self.gateway(stub)
        self.assertIsNone(gateway.native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_authority_failure_fails_closed(self):
        stub = self.stub_with_patient(True, "Patient One")

        def boom(*args, **kwargs):
            raise RuntimeError("permission engine unavailable")

        stub.has_permission = boom
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))


if __name__ == "__main__":
    unittest.main()
