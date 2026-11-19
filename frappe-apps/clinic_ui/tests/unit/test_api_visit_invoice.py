import importlib
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

from tests.unit.fake_gateway import session_view, settings


class ApiTests(unittest.TestCase):
    def setUp(self):
        frappe = ModuleType("frappe")
        def whitelist(*, methods):
            def decorate(fn):
                fn.methods = methods
                return fn
            return decorate
        frappe.whitelist = whitelist
        frappe.get_request_header = lambda name: "header-token"
        frappe.request = SimpleNamespace(get_json=lambda **kwargs: None)
        self.modules = patch.dict(sys.modules, {"frappe": frappe})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        for name in ("clinic_ui.api.visit_invoice", "clinic_ui.api.patients"):
            sys.modules.pop(name, None)
            self.addCleanup(sys.modules.pop, name, None)
        self.api = importlib.import_module("clinic_ui.api.visit_invoice")

    def test_methods_and_transport(self):
        self.assertEqual(self.api.view.methods, ["GET"])
        self.assertEqual(self.api.preview.methods, ["POST"])
        self.assertEqual(self.api.submit.methods, ["POST"])
        gateway = object()
        with patch.object(self.api, "Gateway", return_value=gateway), \
             patch.object(self.api.invoice_endpoint, "view", return_value={"invoice_id": "INV"}) as view, \
             patch.object(self.api.invoice_endpoint, "preview", return_value={"ok": 1}) as preview, \
             patch.object(self.api, "submit_endpoint", return_value={"ok": 2}) as submit:
            self.assertEqual(self.api.view("APT"), {"invoice_id": "INV"})
            self.assertEqual(self.api.preview(appointment_id="APT", csrf_token="body-token", cmd="ignored"), {"ok": 1})
            self.assertEqual(self.api.submit(appointment_id="APT", preview_token="token"), {"ok": 2})
            view.assert_called_once_with(gateway, "APT")
            preview.assert_called_once_with(gateway, {"appointment_id": "APT"}, "body-token")
            submit.assert_called_once_with(gateway, {"appointment_id": "APT", "preview_token": "token"}, "header-token")

    def test_write_wrappers_remain_hard_off(self):
        class Denied(Exception):
            pass
        class Gateway:
            site = "clinic-visit-invoice.local"
            current_session = session_view(user="doctor@example.test", roles=("Physician",))
            values = settings(clinic_ui_synthetic_site=site)
            build_visit_invoice_preview = Mock()
            prepare_visit_invoice_submission = Mock()
            post_visit_invoice_and_verify_facts = Mock()
            config_reads = 0

            def setting(self, name):
                return self.values.get(name)

            def _configuration(self):
                type(self).config_reads += 1
                return {"use_mysqlclient": 0, "clinic_ui_visit_invoice_write_user": "doctor@example.test"}

            def visit_invoice_token_key(self):
                return b"k" * 32

            def suppress_traceback(self):
                pass
            def mark_no_store(self):
                pass
            def deny(self):
                raise Denied()
        with patch.object(self.api, "Gateway", Gateway):
            for call in (self.api.preview, self.api.submit):
                with self.subTest(call=call.__name__), self.assertRaises(Denied):
                    call(appointment_id="APT", csrf_token="token")
        self.assertEqual(Gateway.config_reads, 2)
        Gateway.build_visit_invoice_preview.assert_not_called()
        Gateway.prepare_visit_invoice_submission.assert_not_called()
        Gateway.post_visit_invoice_and_verify_facts.assert_not_called()
