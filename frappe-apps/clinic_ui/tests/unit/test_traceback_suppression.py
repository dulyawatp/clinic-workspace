"""Traceback suppression for clinic API responses.

Frappe v16 includes the full exception in the JSON error body when
``frappe.utils.response.is_traceback_allowed()`` is true
(``frappe/utils/response.py:68-79``, ``_make_logs_v1`` and ``report_error``).
That condition holds on a site with ``allow_error_traceback`` for a System User,
or on a dev server. The clinic API must return only the status, the generic
exception type and a generic message.

The ``before_request`` hook runs for every request, including a guest that
Frappe refuses before a non-guest whitelisted method is called
(``frappe/app.py:219-220``). Tests inject a fake ``frappe`` module: no site,
database or HTTP layer is claimed.
"""

import sys
import types
import unittest

from clinic_ui.runtime import traceback as traceback_module

CLINIC_PATH = "/api/method/clinic_ui.api.patients.view"


class FakeFlags(types.SimpleNamespace):
    pass


class RaisingFlags:
    def __setattr__(self, name, value):
        raise RuntimeError("flags unavailable")


class FakeResponse:
    def __init__(self, body, status=403, mimetype="application/json"):
        self.status_code = status
        self.mimetype = mimetype
        self._body = body

    def get_data(self, as_text=False):
        if as_text:
            return self._body
        return self._body.encode("utf-8")

    def set_data(self, value):
        self._body = value


def install_fake_frappe(path=CLINIC_PATH, flags=None, cmd=None):
    local = types.SimpleNamespace(
        request=types.SimpleNamespace(path=path),
        flags=FakeFlags() if flags is None else flags,
    )
    module = types.SimpleNamespace(local=local)
    if cmd is not None:
        module.form_dict = types.SimpleNamespace(cmd=cmd)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    return local, previous


def restore_frappe(previous):
    if previous is None:
        sys.modules.pop("frappe", None)
    else:
        sys.modules["frappe"] = previous


class TracebackSuppressionTests(unittest.TestCase):
    def test_clinic_api_path_disables_error_tracebacks(self):
        local, previous = install_fake_frappe()
        try:
            traceback_module.suppress_api_tracebacks()
        finally:
            restore_frappe(previous)
        self.assertTrue(local.flags.disable_traceback)

    def test_every_clinic_api_method_is_covered(self):
        for path in (
            "/api/method/clinic_ui.api.session.bootstrap",
            "/api/method/clinic_ui.api.directory.synthetic_patients",
            CLINIC_PATH,
        ):
            with self.subTest(path=path):
                local, previous = install_fake_frappe(path)
                try:
                    traceback_module.suppress_api_tracebacks()
                finally:
                    restore_frappe(previous)
                self.assertTrue(local.flags.disable_traceback)

    def test_other_paths_are_not_modified(self):
        for path in (
            "/api/method/frappe.client.get_list",
            "/api/resource/Patient",
            "/clinic/patients/WS-1",
            "/api/method/clinic_uix.evil",
        ):
            with self.subTest(path=path):
                local, previous = install_fake_frappe(path)
                try:
                    traceback_module.suppress_api_tracebacks()
                finally:
                    restore_frappe(previous)
                self.assertFalse(getattr(local.flags, "disable_traceback", False))

    def test_supported_api_versions_are_covered(self):
        for path in (
            "/api/v1/method/clinic_ui.api.patients.view",
            "/api/v2/method/clinic_ui.api.directory.synthetic_patients",
        ):
            with self.subTest(path=path):
                local, previous = install_fake_frappe(path)
                try:
                    traceback_module.suppress_api_tracebacks()
                finally:
                    restore_frappe(previous)
                self.assertTrue(local.flags.disable_traceback)

    def test_legacy_cmd_dispatch_is_covered(self):
        local, previous = install_fake_frappe(
            "/api", cmd="clinic_ui.api.patients.view"
        )
        try:
            traceback_module.suppress_api_tracebacks()
        finally:
            restore_frappe(previous)
        self.assertTrue(local.flags.disable_traceback)

    def test_other_cmd_dispatch_is_not_modified(self):
        local, previous = install_fake_frappe("/api", cmd="frappe.client.get_list")
        try:
            traceback_module.suppress_api_tracebacks()
        finally:
            restore_frappe(previous)
        self.assertFalse(getattr(local.flags, "disable_traceback", False))

    def test_missing_request_or_flags_is_safe(self):
        for local in (
            types.SimpleNamespace(),
            types.SimpleNamespace(request=None, flags=FakeFlags()),
            types.SimpleNamespace(
                request=types.SimpleNamespace(path=None), flags=FakeFlags()
            ),
        ):
            with self.subTest(local=local):
                previous = sys.modules.get("frappe")
                sys.modules["frappe"] = types.SimpleNamespace(local=local)
                try:
                    traceback_module.suppress_api_tracebacks()
                finally:
                    restore_frappe(previous)
                self.assertFalse(
                    getattr(getattr(local, "flags", None), "disable_traceback", False)
                )

    def test_flag_write_failure_is_safe(self):
        local = types.SimpleNamespace(
            request=types.SimpleNamespace(path=CLINIC_PATH),
            flags=RaisingFlags(),
        )
        previous = sys.modules.get("frappe")
        sys.modules["frappe"] = types.SimpleNamespace(local=local)
        try:
            traceback_module.suppress_api_tracebacks()
        finally:
            restore_frappe(previous)


class ErrorEnvelopeSanitizerTests(unittest.TestCase):
    """Model Frappe v16 serialized error bodies.

    v1 adds ``exc``/``_exc_source``/``_server_messages``/``_error_message``;
    v2 adds ``errors[].type/message/title/exception`` and ``messages``. The
    sanitiser must replace the whole body with a fixed generic envelope, not
    trust a denylist or the incoming parse.
    """

    def setUp(self):
        self.local, self.previous = install_fake_frappe()
        self.local.initialised = True
        self.json = __import__("json")

    def tearDown(self):
        restore_frappe(self.previous)

    def body(self):
        return self.json.loads(self.response.get_data(as_text=True))

    def v1_body(self):
        return self.json.dumps(
            {
                "exc_type": "PermissionError",
                "exception": "frappe.exceptions.PermissionError: Not permitted",
                "exc": "Traceback (most recent call last):\n  File \"apps/frappe/frappe/app.py\"",
                "_exc_source": "clinic_ui (app)",
                "_server_messages": '["sensitive server message"]',
                "_error_message": "sensitive error message",
                "message_log": ["sensitive log"],
                "unknown_field": "leak",
            }
        )

    def test_v1_error_body_becomes_a_fixed_generic_envelope(self):
        self.response = FakeResponse(self.v1_body())
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.response.status_code, 403)
        self.assertEqual(self.body(), {"code": "FORBIDDEN"})
        raw = self.response.get_data(as_text=True)
        for leaked in ("Traceback", "sensitive", "clinic_ui", "PermissionError"):
            self.assertNotIn(leaked, raw)

    def test_v2_merged_messages_and_unknown_keys_are_replaced(self):
        body = self.json.dumps(
            {
                "errors": [
                    {
                        "type": "ValidationError",
                        "message": "sensitive throw message",
                        "title": "sensitive title",
                        "exception": "frappe.exceptions.ValidationError: secret",
                    }
                ],
                "messages": ["sensitive message"],
                "extra": {"nested": "leak"},
            }
        )
        self.response = FakeResponse(body, status=417)
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.body(), {"code": "VALIDATION"})
        self.assertNotIn("sensitive", self.response.get_data(as_text=True))

    def test_status_classification_is_preserved(self):
        cases = {
            400: "VALIDATION",
            401: "AUTH_EXPIRED",
            403: "FORBIDDEN",
            404: "UNSUPPORTED",
            405: "UNSUPPORTED",
            409: "CONFLICT_STALE",
            413: "FILE_TOO_LARGE",
            415: "UNSUPPORTED",
            417: "VALIDATION",
            422: "VALIDATION",
            429: "RATE_LIMITED",
            500: "UPSTREAM_UNAVAILABLE",
            503: "UPSTREAM_UNAVAILABLE",
            418: "UNKNOWN",
        }
        for status, code in cases.items():
            with self.subTest(status=status):
                self.response = FakeResponse("{}", status=status)
                traceback_module.sanitize_api_error_response(response=self.response)
                self.assertEqual(self.response.status_code, status)
                self.assertEqual(self.body(), {"code": code})

    def test_server_set_write_codes_are_preserved_for_409(self):
        for code in ("WRITE_LOCKED", "OPERATION_CONFLICT", "STALE_VERSION"):
            with self.subTest(code=code):
                self.local.flags.clinic_ui_write_error_code = code
                self.response = FakeResponse('{"code": "client-forged"}', status=409)
                traceback_module.sanitize_api_error_response(response=self.response)
                self.assertEqual(self.body(), {"code": code})
                self.assertIsNone(self.local.flags.clinic_ui_write_error_code)

    def test_unknown_or_missing_write_code_falls_back_to_status(self):
        for value in (None, "FORGED", "", 5):
            with self.subTest(value=value):
                if value is not None:
                    self.local.flags.clinic_ui_write_error_code = value
                self.response = FakeResponse("{}", status=409)
                traceback_module.sanitize_api_error_response(response=self.response)
                self.assertEqual(self.body(), {"code": "CONFLICT_STALE"})

    def test_write_code_flag_does_not_change_non_409(self):
        self.local.flags.clinic_ui_write_error_code = "WRITE_LOCKED"
        self.response = FakeResponse("{}", status=422)
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.body(), {"code": "VALIDATION"})

    def test_malformed_non_json_and_empty_bodies_are_replaced(self):
        for body in ("not json", "<html>traceback secret</html>", "", "[]", "null"):
            with self.subTest(body=body):
                self.response = FakeResponse(body, mimetype="text/html")
                traceback_module.sanitize_api_error_response(response=self.response)
                self.assertEqual(self.body(), {"code": "FORBIDDEN"})

    def test_non_clinic_error_is_untouched(self):
        body = self.v1_body()
        self.response = FakeResponse(body)
        sys.modules["frappe"].local.request.path = "/api/method/frappe.client.get_list"
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.response.get_data(as_text=True), body)

    def test_success_response_is_untouched(self):
        body = self.json.dumps({"message": {"items": []}})
        self.response = FakeResponse(body, status=200)
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.response.get_data(as_text=True), body)

    def test_uninitialised_request_is_not_claimed(self):
        body = self.v1_body()
        self.local.initialised = False
        self.response = FakeResponse(body)
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.response.get_data(as_text=True), body)

    def test_missing_or_immutable_response_is_safe(self):
        for response in (None, FakeResponse("raw")):
            if response is not None:
                response.set_data = lambda value: (_ for _ in ()).throw(
                    RuntimeError("immutable")
                )
            with self.subTest(response=response):
                traceback_module.sanitize_api_error_response(response=response)

    def test_cmd_dispatch_error_is_replaced(self):
        self.response = FakeResponse(self.v1_body())
        sys.modules["frappe"].local.request.path = "/api"
        sys.modules["frappe"].form_dict = types.SimpleNamespace(
            cmd="clinic_ui.api.patients.view"
        )
        traceback_module.sanitize_api_error_response(response=self.response)
        self.assertEqual(self.body(), {"code": "FORBIDDEN"})

    def test_hook_entry_point_uses_the_request_argument(self):
        self.response = FakeResponse(self.v1_body())
        traceback_module.sanitize_api_error_response(
            response=self.response,
            request=types.SimpleNamespace(path=CLINIC_PATH),
        )
        self.assertEqual(self.body(), {"code": "FORBIDDEN"})


if __name__ == "__main__":
    unittest.main()
