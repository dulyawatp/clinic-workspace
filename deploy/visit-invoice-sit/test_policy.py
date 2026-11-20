import unittest

from policy import allows
from gateway import request_length
from email.message import Message
from pathlib import Path
import re


class PolicyTests(unittest.TestCase):
    def test_login_expired_is_only_allowed_shell_query(self):
        self.assertTrue(allows("GET", "/clinic/login?reason=expired"))
        for query in ("reason=expired&cmd=logout", "reason=other", "reason=%65xpired", "cmd=logout"):
            self.assertFalse(allows("GET", "/clinic/login?" + query))

    def test_compose_disables_inherited_builds_and_background_jobs(self):
        compose = (Path(__file__).parent / "compose.override.yaml").read_text()
        self.assertRegex(compose, r'(?ms)^  builder:\n.*?profiles: \["image-build-only"\].*?^    build:')
        for service in ("backend", "frontend", "configurator", "create-site", "provision",
                        "websocket", "queue-short", "queue-long", "scheduler"):
            match = re.search(rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-z][a-z-]*:|^volumes:)", compose)
            self.assertIsNotNone(match, service)
            self.assertIn("build: !reset null", match.group(1), service)
            self.assertIn("${CLINIC_VISIT_INVOICE_IMAGE:?", match.group(1), service)
        for service in ("queue-short", "queue-long", "scheduler"):
            self.assertRegex(compose, rf"(?ms)^  {service}:\n(?:(?!^  [a-z][a-z-]*:).)*profiles: \[\"disabled-background\"\]")

    def test_base_required_variables_are_documented_as_private(self):
        directory = Path(__file__).parent
        base = (directory.parent / "local-sit" / "compose.yaml").read_text()
        runbook = (directory / "README.md").read_text()
        required = set(re.findall(r"\$\{([A-Z_]+):\?", base))
        self.assertTrue({"SIT_EPOCH_SECRET", "SIT_DOC_PASSWORD", "SIT_NUR_PASSWORD", "SIT_REC_PASSWORD"} <= required)
        for variable in required:
            self.assertIn(f"`{variable}`", runbook)
    def test_ingress_rejects_ambiguous_framing(self):
        headers = Message()
        headers.add_header("Content-Length", "1")
        headers.add_header("Content-Length", "1")
        self.assertIsNone(request_length(headers))
        headers = Message()
        headers.add_header("Transfer-Encoding", "chunked")
        self.assertIsNone(request_length(headers))
        headers = Message()
        self.assertEqual(request_length(headers), 0)
    def test_explicit_reads_and_auth(self):
        self.assertTrue(allows("GET", "/clinic/login"))
        self.assertTrue(allows("GET", "/assets/clinic_ui/ui/assets/main.js"))
        self.assertTrue(allows("GET", "/api/method/clinic_ui.api.session.bootstrap"))

    def test_rejects_dispatch_bypasses_and_unknown_routes(self):
        for method, target in [
            ("GET", "/api/method/frappe.client.get"),
            ("POST", "/api/method/clinic_ui.api.session.bootstrap"),
            ("GET", "/clinic?cmd=frappe.client.delete"),
            ("GET", "/api/method/clinic_ui.api.session.bootstrap?%63md=frappe.client.delete"),
            ("GET", "/api/method%2fclinic_ui.api.session.bootstrap"),
            ("GET", "/clinic/../api/method/frappe.client.get"),
            ("GET", "/app"),
            ("GET", "/assets/clinic_ui/ui/assets/../../private/file"),
        ]:
            with self.subTest(method=method, target=target):
                self.assertFalse(allows(method, target))

    def test_invoice_and_auth_schema(self):
        prefix = "/api/method/"
        self.assertTrue(allows("POST", prefix + "login", b'{"usr":"u","pwd":"p"}', "application/json"))
        self.assertTrue(allows("POST", prefix + "logout", b"", "application/json"))
        self.assertTrue(allows("GET", prefix + "clinic_ui.api.visit_invoice.view?appointment_id=APPT%2F1"))
        self.assertTrue(allows("POST", prefix + "clinic_ui.api.visit_invoice.submit",
                               b'{"appointment_id":"A","vat_mode":"vat_7","preview_token":"t"}', "application/json"))
        for target, body in [
            (prefix + "login", b'{"usr":"u","pwd":"p","cmd":"frappe.client.delete"}'),
            (prefix + "login", b'{"usr":"u","usr":"v","pwd":"p"}'),
            (prefix + "clinic_ui.api.visit_invoice.preview", b'{"appointment_id":"A","discount":{"cmd":"x"}}'),
            (prefix + "clinic_ui.api.visit_invoice.submit", b'{"appointment_id":"A","preview_token":"t","cmd":"x"}'),
        ]:
            self.assertFalse(allows("POST", target, body, "application/json"))
        self.assertFalse(allows("GET", prefix + "clinic_ui.api.visit_invoice.view?appointment_id=A&cmd=x"))
        self.assertFalse(allows("GET", prefix + "clinic_ui.api.visit_invoice.view?%61ppointment_id=A"))
        self.assertFalse(allows("POST", prefix + "login", b"usr=u&pwd=p", "application/x-www-form-urlencoded"))

    def test_invoice_vat_mode_is_required_exact_string(self):
        prefix = "/api/method/"
        for name in ("preview", "submit"):
            target = prefix + "clinic_ui.api.visit_invoice." + name
            token = b',"preview_token":"t"' if name == "submit" else b""
            for mode in (b'"vat_7"', b'"no_vat"'):
                body = b'{"appointment_id":"A","vat_mode":' + mode + token + b"}"
                with self.subTest(name=name, mode=mode):
                    self.assertTrue(allows("POST", target, body, "application/json"))
            for mode in (b'"VAT_7"', b'"vat 7"', b'"VAT7"', b'""', b'"7"', b'"vat_0"',
                         b"null", b"true", b"7", b'["vat_7"]', b'{"mode":"vat_7"}'):
                body = b'{"appointment_id":"A","vat_mode":' + mode + token + b"}"
                with self.subTest(name=name, mode=mode):
                    self.assertFalse(allows("POST", target, body, "application/json"))
            for body in [
                b'{"appointment_id":"A"' + token + b"}",
                b'{"appointment_id":"A","vat_mode":"vat_7","vat_mode":"no_vat"' + token + b"}",
                b'{"appointment_id":"A","vat_mode":"vat_7","preview_token":"t"}' if name == "preview" else
                b'{"appointment_id":"A","vat_mode":"vat_7"}',
                b'{"appointment_id":"A","vat_mode":"vat_7","cmd":"x"' + token + b"}",
                b'{"appointment_id":"A","vat_mode":"vat_7","preview_tokn":"t"' + token + b"}",
                b'{"appointment_id":"A","vat_mode":"vat_7","discount":"10"' + token + b"}",
            ]:
                with self.subTest(name=name, body=body):
                    self.assertFalse(allows("POST", target, body, "application/json"))
            body = b'{"appointment_id":"A","vat_mode":"vat_7","discount":{"mode":"percent","value":"15"}' + token + b"}"
            with self.subTest(name=name, discount=True):
                self.assertTrue(allows("POST", target, body, "application/json"))
            body = b'{"appointment_id":"A","vat_mode":"vat_7","discount":{"mode":"percent","value":"15","cmd":"x"}' + token + b"}"
            with self.subTest(name=name, discount_unknown=True):
                self.assertFalse(allows("POST", target, body, "application/json"))
            body = b'{"appointment_id":"A","vat_mode":"vat_7","discount":{"mode":"amount","value":"10"}}'
            with self.subTest(name=name, discount_only_preview=name == "preview"):
                self.assertIs(allows("POST", target, body, "application/json"), name == "preview")


if __name__ == "__main__":
    unittest.main()
