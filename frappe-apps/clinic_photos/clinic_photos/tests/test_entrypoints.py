"""Focused Frappe integration tests for the external raw-reader and cache closure.

NOT RUN locally: this host has no installed site, bench or ERPNext. Task 4 owns
execution on an explicitly authorized disposable site. Running these cases does not
require installing this app anywhere; it requires that the app is installed there.

What these cases add beyond the pure unit tests:

* installed hook resolution for both overrides (bank POST-only contract; ZIP native
  whitelist contract compared against the original decorated endpoint);
* guard ordering against the *installed* ERPNext callable: a protected input is
  refused before the Bank Statement Import document is loaded or any bytes are read;
* header behaviour on a real Werkzeug response, including an accelerated
  (``X-Accel-Redirect``) delivery response.

Deliberate limits:

* no finance method runs against real data and no Chart of Accounts importer is
  touched; the delegation case uses a nonexistent import document so the native
  function fails before any file read or write;
* ZIP marker propagation, Guest dispatch prefilter, GET/POST method rejection and
  real request routing are **not** exercised locally; they belong to the Task 4 HTTP
  harness. `frappe.is_whitelisted` returns `None` on success (installed
  ``__init__.py:479-497`` has no return value), so success is asserted as `None`,
  never as truthy;
* actual HTTP routing (direct ``/private/files/`` URL, API download, ZIP, nginx
  ``/protected/`` internal location) belongs to the Task 4 HTTP harness, which
  requires the authorized base URL and manifest;
* Frappe swallows ``after_request`` exceptions, so no case here claims the header
  hook is an authorization boundary.
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.wrappers import Response

from clinic_photos.access.entrypoints import (
    NATIVE_ZIP_MODULE,
    NATIVE_ZIP_PATH,
    PROTECTED_REQUEST_ATTR,
    RAW_READER_MODULE,
    RAW_READER_PATH,
    after_request,
    convert_mt940_to_csv,
)

PROTECTED_SAMPLE = (
    "/private/files/clinic-photo-77777777-7777-7777-7777-777777777777.png"
)
UNRELATED_SAMPLE = "/private/files/statement-2026-07.mt940"


def mark():
    setattr(frappe.local, PROTECTED_REQUEST_ATTR, True)


def clear_mark():
    try:
        delattr(frappe.local, PROTECTED_REQUEST_ATTR)
    except AttributeError:
        pass


class EntryPointRegistrationTests(FrappeTestCase):
    def test_installed_override_resolves_to_the_clinic_photos_wrapper(self):
        self.assertEqual(
            frappe.override_whitelisted_method(RAW_READER_PATH),
            "clinic_photos.access.entrypoints.convert_mt940_to_csv",
        )

    def test_installed_override_keeps_the_post_only_whitelist_contract(self):
        method = frappe.get_attr(
            "clinic_photos.access.entrypoints.convert_mt940_to_csv"
        )
        # Installed is_whitelisted returns None on success; it raises otherwise.
        self.assertIsNone(frappe.is_whitelisted(method))
        allowed = frappe.allowed_http_methods_for_whitelisted_func[method]
        self.assertIn("POST", allowed)
        self.assertNotIn("GET", allowed)
        self.assertEqual(method.__name__, "convert_mt940_to_csv")

    def test_zip_override_resolves_to_the_clinic_photos_wrapper(self):
        self.assertEqual(
            frappe.override_whitelisted_method(NATIVE_ZIP_PATH),
            "clinic_photos.access.entrypoints.zip_files",
        )

    def test_zip_override_keeps_the_native_whitelist_contract(self):
        overridden = frappe.get_attr("clinic_photos.access.entrypoints.zip_files")
        original = frappe.get_attr(NATIVE_ZIP_PATH)
        self.assertIsNone(frappe.is_whitelisted(overridden))
        self.assertEqual(
            set(frappe.allowed_http_methods_for_whitelisted_func[overridden]),
            set(frappe.allowed_http_methods_for_whitelisted_func[original]),
        )
        self.assertEqual(overridden.__name__, "zip_files")

    def test_raw_reader_module_is_installed_and_unchanged(self):
        module = frappe.get_module(RAW_READER_MODULE)
        self.assertTrue(callable(module.convert_mt940_to_csv))

    def test_zip_endpoint_module_is_installed_and_unchanged(self):
        module = frappe.get_module(NATIVE_ZIP_MODULE)
        self.assertTrue(callable(module.zip_files))


class RawReaderDenialTests(FrappeTestCase):
    def setUp(self):
        clear_mark()
        # This test creates no records: a reserved orphan path needs no fixture.
        self.reserved_path = PROTECTED_SAMPLE

    def tearDown(self):
        clear_mark()

    def test_protected_input_is_rejected_before_native_document_or_bytes(self):
        with self.assertRaises(frappe.PermissionError):
            convert_mt940_to_csv("SYNTHETIC-NONEXISTENT", self.reserved_path)
        self.assertTrue(getattr(frappe.local, PROTECTED_REQUEST_ATTR, False))

    def test_unrelated_input_reaches_the_native_document_check_first(self):
        # A missing import document proves delegation to the native callable: the
        # wrapper's protected-input guard did not fire and no file is read.
        with self.assertRaises(frappe.DoesNotExistError):
            convert_mt940_to_csv("SYNTHETIC-NONEXISTENT", UNRELATED_SAMPLE)
        self.assertFalse(getattr(frappe.local, PROTECTED_REQUEST_ATTR, False))


class ResponseHeaderTests(FrappeTestCase):
    def setUp(self):
        clear_mark()

    def tearDown(self):
        clear_mark()

    def _response(self, accelerated=False):
        response = Response()
        response.headers["Cache-Control"] = (
            "private,max-age=3600,stale-while-revalidate=86400"
        )
        response.headers["Expires"] = "Wed, 21 Oct 2026 07:28:00 GMT"
        if accelerated:
            response.headers["X-Accel-Redirect"] = "/protected/private/files/x.png"
        return response

    def assert_no_store(self, response):
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")

    def test_streamed_protected_response_is_no_store(self):
        mark()
        response = self._response()
        after_request(frappe.local.request, response)
        self.assert_no_store(response)

    def test_accelerated_protected_response_is_no_store(self):
        mark()
        response = self._response(accelerated=True)
        after_request(frappe.local.request, response)
        self.assert_no_store(response)
        self.assertEqual(
            response.headers["X-Accel-Redirect"],
            "/protected/private/files/x.png",
        )

    def test_denied_response_is_no_store(self):
        mark()
        response = self._response()
        response.status_code = 403
        after_request(frappe.local.request, response)
        self.assert_no_store(response)

    def test_unrelated_response_is_untouched(self):
        response = self._response()
        before = dict(response.headers)
        after_request(frappe.local.request, response)
        self.assertEqual(dict(response.headers), before)


if __name__ == "__main__":
    unittest.main()
