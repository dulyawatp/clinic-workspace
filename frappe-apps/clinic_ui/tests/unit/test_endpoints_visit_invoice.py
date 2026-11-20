import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import contextmanager

from clinic_ui.core import visit_invoice_token
from clinic_ui.endpoints import visit_invoice


KEY = b"k" * 32
NOW = 1000
TAX_ROW = {"charge_type": "On Net Total", "account_head": "VAT-LIAB",
           "rate": "7", "included_in_print_rate": 0, "description": "Visit VAT"}


class Denied(Exception):
    pass


class FakeGateway:
    def __init__(self):
        self.csrf = True
        self.native = True
        self.discount_allowed = True
        self.no_vat_allowed = True
        self.no_vat_error = False
        self.no_vat_calls = []
        self.calls = []
        self.claims = None
        self.events = []
        self.locked = True
        self.prepare_error = False
        self.post_error = None
        self.bad_facts = False
        self.rollback_error = False
        self.commit_error = False
        self.result_error = False
        self.pending_mutation = False

    def suppress_traceback(self):
        pass

    def mark_no_store(self):
        pass

    def deny(self):
        raise Denied()

    def csrf_token_valid(self, token):
        return self.csrf and token == "csrf"

    def visit_invoice_native_create_submit_allowed(self):
        return self.native

    def visit_invoice_no_vat_allowed(self, appointment_id):
        self.no_vat_calls.append(appointment_id)
        if self.no_vat_error:
            raise RuntimeError("private authority details")
        return self.no_vat_allowed

    def visit_invoice_discount_allowed(self):
        return self.discount_allowed

    def totals(self, discount, vat_mode):
        gross = Decimal("100.00")
        cut = Decimal("0") if discount is None else discount["value"]
        net = gross - cut
        vat = (net * Decimal("7") / 100).quantize(Decimal("0.01")) if vat_mode == "vat_7" else Decimal("0.00")
        return {"currency": "USD", "gross": f"{gross:.2f}", "discount": f"{cut:.2f}",
                "net": f"{net:.2f}", "vat": f"{vat:.2f}", "total": f"{net + vat:.2f}"}

    def claims_for(self, appointment_id, discount, vat_mode):
        return {
            "actor": "doctor@example.test", "session": "private-session",
            "date": "2026-11-20", "appointment": appointment_id, "encounter": None,
            "discount": ({"mode": "none", "value": "0"} if discount is None else
                         {"mode": discount["mode"], "value": str(discount["value"])}),
            "vat": ({"mode": "vat_7", "template": "VAT 7", "account": "VAT-LIAB",
                     "rate": "7", "rows": [dict(TAX_ROW)]} if vat_mode == "vat_7" else
                    {"mode": "no_vat", "template": None, "account": None,
                     "rate": None, "rows": []}),
            "terms": {"patient": "private-patient", "customer": "customer",
                      "practitioner": "doctor", "company": "company",
                      "service": "service", "rate": "100.00"},
            "totals": self.totals(discount, vat_mode),
        }

    def build_visit_invoice_preview(self, appointment_id, discount, vat_mode):
        self.calls.append((appointment_id, discount, vat_mode))
        self.claims = self.claims_for(appointment_id, discount, vat_mode)
        return {"totals": dict(self.claims["totals"]), "claims": self.claims}

    def visit_invoice_token_key(self):
        return KEY

    def visit_invoice_clock(self):
        return NOW

    def respond_write(self, status, body):
        return status, body

    @contextmanager
    def visit_invoice_serialized(self, appointment_id):
        self.events.append("enter")
        try:
            yield self.locked
        finally:
            self.events.append("exit")

    def prepare_visit_invoice_submission(self, appointment_id, discount, vat_mode):
        self.events.append("prepare")
        if self.prepare_error:
            raise ValueError("unavailable")
        return {**self.build_visit_invoice_preview(appointment_id, discount, vat_mode),
                "draft": object()}

    def post_visit_invoice_and_verify_facts(self, prepared):
        self.events.append("post")
        self.pending_mutation = True
        if self.post_error:
            raise self.post_error
        return {"invoice_id": "INV-1", "docstatus": 1,
                "persisted_facts_verified": not self.bad_facts}

    def rollback_visit_invoice(self):
        self.events.append("rollback")
        if self.rollback_error:
            raise RuntimeError("rollback failed")
        self.pending_mutation = False

    def commit_visit_invoice(self):
        self.events.append("commit")
        if self.commit_error:
            raise RuntimeError("commit ambiguous")

    def view_submitted_visit_invoice(self, appointment_id, invoice_id):
        self.events.append("view")
        if self.result_error:
            raise RuntimeError("unreadable")
        return {"invoice_id": invoice_id}


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeGateway()
        self.authorize = patch(
            "clinic_ui.endpoints.visit_invoice.common.authorize",
            return_value=(None, SimpleNamespace(user="doctor@example.test"), "DOC"),
        )
        self.authorize.start()
        self.addCleanup(self.authorize.stop)

    def enabled(self):
        return patch("clinic_ui.runtime.visit_invoice.visit_invoice_write_enabled", return_value=True)

    def test_default_gate_denies_before_authorization_or_adapter(self):
        with self.assertRaises(Denied):
            visit_invoice.preview(self.gateway, {"appointment_id": "APT-1", "vat_mode": "vat_7"}, "csrf")
        self.assertEqual(self.gateway.calls, [])

    def test_valid_preview_modes_and_no_discount_issue_real_opaque_token(self):
        for discount, expected in ((None, "0.00"),
                                   ({"mode": "amount", "value": "12.50"}, "12.50"),
                                   ({"mode": "percent", "value": "15"}, "15.00")):
            with self.subTest(discount=discount), self.enabled():
                body = {"appointment_id": "APT-1", "vat_mode": "vat_7"}
                if discount is not None:
                    body["discount"] = discount
                result = visit_invoice.preview(self.gateway, body, "csrf")
                self.assertEqual(set(result), {"currency", "gross", "discount", "net", "vat",
                                               "total", "preview_token"})
                self.assertEqual(result["discount"], expected)
                self.assertNotIn("preview_token", str(self.gateway.claims["terms"]))
                self.assertTrue(visit_invoice_token.verify(KEY, self.gateway.claims,
                                                           result["preview_token"], now=NOW))
                self.assertNotIn("private", str(result))

    def test_preview_amounts_show_net_vat_and_payable(self):
        with self.enabled():
            result = visit_invoice.preview(
                self.gateway,
                {"appointment_id": "APT-1", "vat_mode": "vat_7",
                 "discount": {"mode": "percent", "value": "15"}},
                "csrf",
            )
            self.assertEqual((result["net"], result["vat"], result["total"]),
                             ("85.00", "5.95", "90.95"))
            no_vat = visit_invoice.preview(
                self.gateway,
                {"appointment_id": "APT-1", "vat_mode": "no_vat",
                 "discount": {"mode": "percent", "value": "15"}},
                "csrf",
            )
            self.assertEqual((no_vat["net"], no_vat["vat"], no_vat["total"]),
                             ("85.00", "0.00", "85.00"))
        self.assertEqual(self.gateway.calls,
                         [("APT-1", {"mode": "percent", "value": Decimal("15")}, "vat_7"),
                          ("APT-1", {"mode": "percent", "value": Decimal("15")}, "no_vat")])

    def test_vat_7_never_consults_no_vat_authority(self):
        with self.enabled():
            visit_invoice.preview(self.gateway, {"appointment_id": "APT-1", "vat_mode": "vat_7"}, "csrf")
        self.assertEqual(self.gateway.no_vat_calls, [])

    def test_no_vat_requires_positive_server_authority_before_preview(self):
        for case in ("denied", "error"):
            with self.subTest(case=case), self.enabled():
                gateway = FakeGateway()
                if case == "denied":
                    gateway.no_vat_allowed = False
                else:
                    gateway.no_vat_error = True
                with self.assertRaises(Denied):
                    visit_invoice.preview(gateway, {"appointment_id": "APT-1", "vat_mode": "no_vat"}, "csrf")
                self.assertEqual(gateway.calls, [])
                self.assertEqual(gateway.no_vat_calls, ["APT-1"])

    def test_no_vat_preview_binds_authorized_zero_tax_mode(self):
        with self.enabled():
            result = visit_invoice.preview(
                self.gateway, {"appointment_id": "APT-1", "vat_mode": "no_vat"}, "csrf")
        self.assertEqual(result["vat"], "0.00")
        self.assertEqual(result["total"], "100.00")
        self.assertEqual(self.gateway.claims["vat"]["mode"], "no_vat")
        self.assertTrue(visit_invoice_token.verify(KEY, self.gateway.claims,
                                                   result["preview_token"], now=NOW))

    def test_missing_or_invalid_vat_mode_normalized(self):
        for body in ({"appointment_id": "APT-1"},
                     {"appointment_id": "APT-1", "vat_mode": None},
                     {"appointment_id": "APT-1", "vat_mode": ""},
                     {"appointment_id": "APT-1", "vat_mode": "vat"},
                     {"appointment_id": "APT-1", "vat_mode": "vat_6"},
                     {"appointment_id": "APT-1", "vat_mode": "VAT_7"},
                     {"appointment_id": "APT-1", "vat_mode": " vat_7"},
                     {"appointment_id": "APT-1", "vat_mode": 7}):
            with self.subTest(body=body), self.enabled():
                self.assertEqual(visit_invoice.preview(self.gateway, body, "csrf"),
                                 (422, {"code": "VALIDATION"}))
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(self.gateway.no_vat_calls, [])

    def test_invalid_request_normalized(self):
        for body in (None, {}, {"appointment_id": " APT-1", "vat_mode": "vat_7"},
                     {"appointment_id": "x", "vat_mode": "vat_7", "extra": 1},
                     {"appointment_id": "x", "vat_mode": "vat_7",
                      "discount": {"mode": "amount", "value": "-1"}}):
            with self.subTest(body=body), self.enabled():
                self.assertEqual(visit_invoice.preview(self.gateway, body, "csrf"),
                                 (422, {"code": "VALIDATION"}))
        self.assertEqual(self.gateway.calls, [])

    def test_csrf_native_and_discount_denials(self):
        for attr, body in (("csrf", {"appointment_id": "x", "vat_mode": "vat_7"}),
                           ("native", {"appointment_id": "x", "vat_mode": "vat_7"}),
                           ("discount_allowed", {"appointment_id": "x", "vat_mode": "vat_7",
                                                 "discount": {"mode": "amount", "value": "1"}})):
            gateway = FakeGateway()
            setattr(gateway, attr, False)
            with self.subTest(attr=attr), self.enabled(), self.assertRaises(Denied):
                visit_invoice.preview(gateway, body, "csrf")
            self.assertEqual(gateway.calls, [])

    def test_invalid_adapter_data_denied(self):
        self.gateway.build_visit_invoice_preview = lambda *_: {"claims": {"secret": "x"}}
        with self.enabled(), self.assertRaises(Denied):
            visit_invoice.preview(self.gateway, {"appointment_id": "x", "vat_mode": "vat_7"}, "csrf")

    def test_authorization_denial_propagates(self):
        with self.enabled(), patch("clinic_ui.endpoints.visit_invoice.common.authorize", side_effect=Denied):
            with self.assertRaises(Denied):
                visit_invoice.preview(self.gateway, {"appointment_id": "x", "vat_mode": "vat_7"}, "csrf")


class SubmitTests(PreviewTests):
    def body(self, discount=None, vat_mode="vat_7"):
        payload = {"appointment_id": "APT-1", "vat_mode": vat_mode}
        if discount is not None:
            payload["discount"] = discount
        token = visit_invoice.preview(self.gateway, payload, "csrf")["preview_token"]
        return {**payload, "preview_token": token}

    def test_submit_success_revalidates_then_commits_and_reads(self):
        for discount in (None, {"mode": "amount", "value": "12.50"},
                         {"mode": "percent", "value": "15"}):
            with self.subTest(discount=discount), self.enabled():
                self.gateway = FakeGateway()
                body = self.body(discount)
                self.assertEqual(visit_invoice.submit(self.gateway, body, "csrf"),
                                 {"invoice_id": "INV-1"})
                self.assertEqual(self.gateway.events, ["enter", "prepare", "post", "commit", "view", "exit"])

    def test_submit_no_vat_requires_authority_again_inside_lane(self):
        with self.enabled():
            body = self.body(vat_mode="no_vat")
            self.gateway.no_vat_allowed = False
            with self.assertRaises(Denied):
                visit_invoice.submit(self.gateway, body, "csrf")
            self.assertEqual(self.gateway.no_vat_calls, ["APT-1", "APT-1"])
            self.assertEqual(self.gateway.events, ["enter", "exit"])
            self.assertNotIn("post", self.gateway.events)
            self.assertEqual(self.gateway.no_vat_calls, ["APT-1", "APT-1"])

    def test_submit_no_vat_authority_error_inside_lane_denies(self):
        with self.enabled():
            body = self.body(vat_mode="no_vat")
            self.gateway.no_vat_error = True
            with self.assertRaises(Denied):
                visit_invoice.submit(self.gateway, body, "csrf")
            self.assertEqual(self.gateway.events, ["enter", "exit"])

    def test_submit_missing_or_invalid_vat_mode_normalized(self):
        with self.enabled():
            body = self.body()
            for payload in ({k: v for k, v in body.items() if k != "vat_mode"},
                            {**body, "vat_mode": "vat_6"},
                            {**body, "vat_mode": None}):
                with self.subTest(payload=payload.get("vat_mode")):
                    self.assertEqual(visit_invoice.submit(self.gateway, payload, "csrf"),
                                     (422, {"code": "VALIDATION"}))
            self.assertEqual(self.gateway.events, [])

    def test_stale_expired_tampered_or_duplicate_denied_before_post(self):
        for case in ("stale", "expired", "tampered", "duplicate"):
            with self.subTest(case=case), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if case == "tampered":
                    body["preview_token"] += "x"
                if case == "expired":
                    self.gateway.visit_invoice_clock = lambda: NOW + 300
                if case == "duplicate":
                    self.gateway.prepare_error = True
                if case == "stale":
                    original = self.gateway.prepare_visit_invoice_submission
                    def changed(*args):
                        result = original(*args)
                        result["claims"] = {**result["claims"],
                                            "terms": {**result["claims"]["terms"], "rate": "101.00"}}
                        return result
                    self.gateway.prepare_visit_invoice_submission = changed
                with self.assertRaises(Denied):
                    visit_invoice.submit(self.gateway, body, "csrf")
                self.assertNotIn("post", self.gateway.events)

    def test_changed_mode_or_tax_facts_denied_before_post(self):
        cases = {
            "mode": lambda claims: claims.update(
                {"vat": {**claims["vat"], "mode": "no_vat"}}),
            "template": lambda claims: claims["vat"].update({"template": "VAT 6"}),
            "account": lambda claims: claims["vat"].update({"account": "OTHER"}),
            "rate": lambda claims: claims["vat"].update({"rate": "6"}),
            "row": lambda claims: claims["vat"]["rows"][0].update({"account_head": "OTHER"}),
            "vat_total": lambda claims: claims["totals"].update({"vat": "6.00"}),
            "net": lambda claims: claims["totals"].update({"net": "99.00"}),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                original = self.gateway.prepare_visit_invoice_submission

                def changed(*args):
                    result = original(*args)
                    mutate(result["claims"])
                    return result

                self.gateway.prepare_visit_invoice_submission = changed
                with self.assertRaises(Denied):
                    visit_invoice.submit(self.gateway, body, "csrf")
                self.assertNotIn("post", self.gateway.events)

    def test_missing_or_false_lane_denies_before_prepare(self):
        for missing in (True, False):
            with self.subTest(missing=missing), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if missing:
                    self.gateway.visit_invoice_serialized = None
                else:
                    self.gateway.locked = False
                with self.assertRaises(Denied):
                    visit_invoice.submit(self.gateway, body, "csrf")
                self.assertNotIn("prepare", self.gateway.events)

    def test_known_failure_and_bad_facts_rollback(self):
        for failure in ("known", "facts"):
            with self.subTest(failure=failure), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if failure == "known":
                    self.gateway.post_error = visit_invoice.KnownMutationFailure()
                else:
                    self.gateway.bad_facts = True
                with self.assertRaises(Denied):
                    visit_invoice.submit(self.gateway, body, "csrf")
                self.assertIn("rollback", self.gateway.events)
                self.assertNotIn("commit", self.gateway.events)

    def test_rollback_failure_propagates(self):
        with self.enabled():
            body = self.body()
            self.gateway.bad_facts = True
            self.gateway.rollback_error = True
            with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                visit_invoice.submit(self.gateway, body, "csrf")
            self.assertNotIn("commit", self.gateway.events)

    def test_ambiguous_post_commit_roll_back_but_view_does_not(self):
        for failure in ("post", "commit", "view"):
            with self.subTest(failure=failure), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if failure == "post":
                    self.gateway.post_error = RuntimeError("unknown")
                elif failure == "commit":
                    self.gateway.commit_error = True
                else:
                    self.gateway.result_error = True
                self.assertEqual(visit_invoice.submit(self.gateway, body, "csrf"),
                                 (409, {"code": "WRITE_UNCERTAIN"}))
                if failure in ("post", "commit"):
                    self.assertIn("rollback", self.gateway.events)
                    self.assertLess(self.gateway.events.index("rollback"), self.gateway.events.index("exit"))
                    self.assertFalse(self.gateway.pending_mutation)
                else:
                    self.assertNotIn("rollback", self.gateway.events)

    def test_ambiguous_failure_rollback_error_propagates(self):
        for failure in ("post", "commit"):
            with self.subTest(failure=failure), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if failure == "post":
                    self.gateway.post_error = RuntimeError("ambiguous mutation")
                else:
                    self.gateway.commit_error = True
                self.gateway.rollback_error = True
                with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                    visit_invoice.submit(self.gateway, body, "csrf")
                self.assertLess(self.gateway.events.index("rollback"), self.gateway.events.index("exit"))

    def test_submit_strict_payload_and_permission(self):
        with self.enabled():
            body = self.body()
            self.assertEqual(visit_invoice.submit(self.gateway, {**body, "extra": 1}, "csrf"),
                             (422, {"code": "VALIDATION"}))
            self.gateway.native = False
            with self.assertRaises(Denied):
                visit_invoice.submit(self.gateway, body, "csrf")
            self.assertEqual(self.gateway.events, [])

    def test_submit_default_gate_csrf_and_discount_authorization(self):
        with self.enabled():
            body = self.body({"mode": "percent", "value": "15"})
        with self.assertRaises(Denied):
            visit_invoice.submit(self.gateway, body, "csrf")
        for attr in ("csrf", "discount_allowed"):
            with self.subTest(attr=attr), self.enabled():
                setattr(self.gateway, attr, False)
                with self.assertRaises(Denied):
                    visit_invoice.submit(self.gateway, body, "csrf")
                setattr(self.gateway, attr, True)
        self.assertEqual(self.gateway.events, [])

    def test_wrong_postcommit_view_and_lane_exit_failure_are_uncertain(self):
        for case in ("wrong_view", "exit"):
            with self.subTest(case=case), self.enabled():
                self.gateway = FakeGateway()
                body = self.body()
                if case == "wrong_view":
                    self.gateway.view_submitted_visit_invoice = lambda *_: {"invoice_id": "OTHER"}
                else:
                    @contextmanager
                    def broken_exit(_appointment):
                        yield True
                        raise RuntimeError("private release error")
                    self.gateway.visit_invoice_serialized = broken_exit
                if case == "exit":
                    with self.assertRaisesRegex(RuntimeError, "private release error"):
                        visit_invoice.submit(self.gateway, body, "csrf")
                else:
                    self.assertEqual(visit_invoice.submit(self.gateway, body, "csrf"),
                                     (409, {"code": "WRITE_UNCERTAIN"}))
                self.assertNotIn("rollback", self.gateway.events)

    def test_lane_enter_cleanup_failure_escapes(self):
        @contextmanager
        def broken_enter(_appointment):
            raise RuntimeError("cleanup failed")
            yield
        self.gateway.visit_invoice_serialized = broken_enter
        with self.enabled():
            body = self.body()
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                visit_invoice.submit(self.gateway, body, "csrf")

    def test_cleanup_failure_with_pending_mutation_never_returns_normally(self):
        @contextmanager
        def broken_exit(_appointment):
            try:
                yield True
            finally:
                raise RuntimeError("cleanup failed")
        self.gateway.visit_invoice_serialized = broken_exit
        self.gateway.bad_facts = True
        self.gateway.rollback_error = True
        with self.enabled():
            body = self.body()
            with self.assertRaisesRegex(RuntimeError, "cleanup failed"):
                visit_invoice.submit(self.gateway, body, "csrf")
        self.assertTrue(self.gateway.pending_mutation)

    def test_native_authority_error_is_generic_denial(self):
        def fail():
            raise RuntimeError("private native details")
        self.gateway.visit_invoice_native_create_submit_allowed = fail
        with self.enabled(), self.assertRaises(Denied):
            visit_invoice.preview(self.gateway, {"appointment_id": "x", "vat_mode": "vat_7"}, "csrf")

    def test_mismatched_actor_or_totals_are_denied(self):
        original = self.gateway.build_visit_invoice_preview
        for field in ("actor", "totals"):
            def altered(*args):
                result = original(*args)
                result["claims"] = {**result["claims"], field: "private mismatch"}
                return result
            self.gateway.build_visit_invoice_preview = altered
            with self.subTest(field=field), self.enabled(), self.assertRaises(Denied):
                visit_invoice.preview(self.gateway, {"appointment_id": "x", "vat_mode": "vat_7"}, "csrf")


class ViewTests(PreviewTests):
    def test_view_works_with_gate_off_and_exact_projection(self):
        self.gateway.view_visit_invoice = lambda appointment: {"invoice_id": "INV-1"}
        self.assertEqual(visit_invoice.view(self.gateway, "APT-1"), {"invoice_id": "INV-1"})

    def test_view_unavailable_on_absent_unreadable_or_invalid_result(self):
        for result in (None, {}, {"invoice_id": "INV-1", "private": "secret"}, {"invoice_id": " x "}):
            with self.subTest(result=result):
                self.gateway.view_visit_invoice = lambda _appointment: result
                self.assertEqual(visit_invoice.view(self.gateway, "APT-1"), {"code": "UNAVAILABLE"})
        def fail(_appointment):
            raise RuntimeError("private details")
        self.gateway.view_visit_invoice = fail
        self.assertEqual(visit_invoice.view(self.gateway, "APT-1"), {"code": "UNAVAILABLE"})

    def test_view_rejects_bad_id_and_unauthorized(self):
        self.gateway.view_visit_invoice = lambda appointment: {"invoice_id": "INV-1"}
        for appointment in (None, "", " APT", "a" * 141):
            with self.subTest(appointment=appointment), self.assertRaises(Denied):
                visit_invoice.view(self.gateway, appointment)
        with patch("clinic_ui.endpoints.visit_invoice.common.authorize", side_effect=Denied):
            with self.assertRaises(Denied):
                visit_invoice.view(self.gateway, "APT-1")
