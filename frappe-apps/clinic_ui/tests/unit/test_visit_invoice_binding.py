import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.core.visit_invoice_token import issue, verify
from clinic_ui.runtime.frappe_gateway import Gateway
from clinic_ui.runtime.visit_invoice_binding import prepare_bound_visit_invoice


VISIT = dict(appointment_id="APT", practitioner="DR", patient="PAT", customer="CUS",
             company="CLINIC", currency="USD", service="CONSULT", price=Decimal("100.00"),
             encounter_id="ENC")
ACCOUNTING = dict(company="CLINIC", currency="USD", customer="CUS",
                  income_account="INCOME", receivable_account="RECEIVABLE", cost_center="CENTER",
                  item="CONSULT", uom="VISIT", company_currency="USD",
                  party_account_currency="USD", posting_date=date(2026, 11, 20),
                  selling_price_list="LIST", unit_rate=Decimal("100.00"))
TOTALS = dict(currency="USD", gross="100.00", discount="0.00", total="100.00")


class BindingTest(unittest.TestCase):
    def setUp(self):
        self.session = SimpleNamespace(user="doctor@example.org", sid="sid-1")
        self.frappe = SimpleNamespace(session=self.session, has_permission=lambda *a, **k: True)
        self.gateway = Gateway(self.frappe)
        self.gateway._site_config = {
            "clinic_ui_visit_invoice_discount_user": "doctor@example.org",
            "clinic_ui_visit_invoice_preview_secret": "s" * 32,
        }
        self.doc = object()
        self.result = (VISIT, ACCOUNTING, self.doc, TOTALS)

    def build(self, result=None, discount=None):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                   return_value=self.result if result is None else result):
            return self.gateway.build_visit_invoice_preview("APT", discount)

    def test_preview_and_bound_internal_facts(self):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            bound = prepare_bound_visit_invoice(self.gateway, "APT", None)
        self.assertIs(bound.doc, self.doc)
        claims = bound.claims()
        self.assertEqual((claims["actor"], claims["session"], claims["encounter"]),
                         ("doctor@example.org", "sid-1", "ENC"))
        self.assertEqual(claims["terms"]["income_account"], "INCOME")
        self.assertEqual(claims["terms"]["rate"], "100.00")
        self.assertEqual(self.build(), {"totals": TOTALS, "claims": claims})
        claims["terms"]["income_account"] = "MUTATED"
        self.assertEqual(bound.claims()["terms"]["income_account"], "INCOME")

    def test_actor_session_and_rate_are_token_bound(self):
        claims = self.build()["claims"]
        token = issue(self.gateway.visit_invoice_token_key(), claims, now=100)
        for target, value in (("user", "other@example.org"), ("sid", "sid-2")):
            old = getattr(self.session, target)
            setattr(self.session, target, value)
            self.assertFalse(verify(self.gateway.visit_invoice_token_key(),
                                    self.build()["claims"], token, now=101))
            setattr(self.session, target, old)
        visit = dict(VISIT, price=Decimal("101.00"))
        accounts = dict(ACCOUNTING, unit_rate=Decimal("101.00"))
        changed = self.build((visit, accounts, self.doc, TOTALS))["claims"]
        self.assertFalse(verify(self.gateway.visit_invoice_token_key(), changed, token, now=101))

    def test_permissions_discount_secret_and_session_fail_closed(self):
        self.assertTrue(self.gateway.visit_invoice_native_create_submit_allowed())
        self.assertTrue(self.gateway.visit_invoice_discount_allowed())
        self.assertEqual(self.gateway.visit_invoice_token_key(), b"s" * 32)
        self.frappe.has_permission = lambda *a, **kw: kw["ptype"] == "create"
        self.assertFalse(self.gateway.visit_invoice_native_create_submit_allowed())
        self.gateway._site_config["clinic_ui_visit_invoice_preview_secret"] = "short"
        with self.assertRaises(ValueError):
            self.gateway.visit_invoice_token_key()
        self.session.user = "Administrator"
        self.assertFalse(self.gateway.visit_invoice_discount_allowed())
        self.session.sid = ""
        self.assertIsNone(self.build())

    def test_absent_config_and_unavailable_preparation_refuse(self):
        self.gateway._site_config = {}
        self.assertFalse(self.gateway.visit_invoice_discount_allowed())
        with self.assertRaises(ValueError):
            self.gateway.visit_invoice_token_key()
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=None):
            self.assertIsNone(self.gateway.build_visit_invoice_preview("APT", None))

    def test_submission_preserves_private_typed_facts_and_posts_only_on_dispatch(self):
        visit, accounting, totals = dict(VISIT), dict(ACCOUNTING), dict(TOTALS)
        discount = {"mode": "amount", "value": Decimal("1.00")}
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                   return_value=(visit, accounting, self.doc, totals)):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", discount)
        visit["price"] = Decimal("999")
        accounting["posting_date"] = date(2000, 1, 1)
        discount["value"] = Decimal("999")
        with patch("clinic_ui.runtime.visit_invoice_posting.post_visit_invoice",
                   return_value="INV-1") as post:
            result = self.gateway.post_visit_invoice_and_verify_facts(prepared)
        self.assertEqual(result, {"invoice_id": "INV-1", "docstatus": 1,
                                  "persisted_facts_verified": True})
        args, kwargs = post.call_args
        self.assertEqual(args, (self.gateway, self.doc, VISIT, ACCOUNTING, TOTALS,
                                {"mode": "amount", "value": Decimal("1.00")}))
        self.assertEqual(kwargs, {"encounter_id": "ENC"})

    def test_submission_refuses_changed_wrapper_or_session(self):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", None)
        prepared["totals"]["total"] = "1.00"
        with self.assertRaises(ValueError):
            self.gateway.post_visit_invoice_and_verify_facts(prepared)
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", None)
        self.session.sid = "other"
        with self.assertRaises(ValueError):
            self.gateway.post_visit_invoice_and_verify_facts(prepared)


if __name__ == "__main__":
    unittest.main()
