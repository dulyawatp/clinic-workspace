import copy
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
TAX_ROW = {"charge_type": "On Net Total", "account_head": "VAT-LIAB",
           "rate": Decimal("7"), "included_in_print_rate": 0, "description": " VAT 7% "}
ACCOUNTING = dict(company="CLINIC", currency="USD", customer="CUS",
                  income_account="INCOME", receivable_account="RECEIVABLE", cost_center="CENTER",
                  item="CONSULT", uom="VISIT", company_currency="USD",
                  party_account_currency="USD", posting_date=date(2026, 11, 20),
                  selling_price_list="LIST", unit_rate=Decimal("100.00"),
                  vat_mode="vat_7", tax_template="VAT 7", tax_rows=(TAX_ROW,),
                  tax_account="VAT-LIAB", tax_rate=Decimal("7"))
NO_VAT_ACCOUNTING = dict(ACCOUNTING, vat_mode="no_vat", tax_template=None, tax_rows=(),
                         tax_account=None, tax_rate=None)
TOTALS = dict(currency="USD", gross="100.00", discount="0.00", net="100.00",
              vat="7.00", total="107.00")
NO_VAT_TOTALS = dict(currency="USD", gross="100.00", discount="0.00", net="100.00",
                     vat="0.00", total="100.00")


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

    def build(self, result=None, discount=None, vat_mode="vat_7"):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                   return_value=self.result if result is None else result) as prepare:
            result = self.gateway.build_visit_invoice_preview("APT", discount, vat_mode)
        self.preparation = prepare
        return result

    def test_preview_and_bound_internal_facts(self):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                   return_value=self.result) as prepare:
            bound = prepare_bound_visit_invoice(self.gateway, "APT", None, "vat_7")
        self.assertEqual(prepare.call_args.args[2:], (None, "vat_7"))
        self.assertIs(bound.doc, self.doc)
        claims = bound.claims()
        self.assertEqual((claims["actor"], claims["session"], claims["encounter"]),
                         ("doctor@example.org", "sid-1", "ENC"))
        self.assertEqual(claims["terms"]["income_account"], "INCOME")
        self.assertEqual(claims["terms"]["rate"], "100.00")
        self.assertEqual(claims["vat"], {"mode": "vat_7", "template": "VAT 7",
                                         "account": "VAT-LIAB", "rate": "7",
                                         "rows": [{"charge_type": "On Net Total",
                                                   "account_head": "VAT-LIAB", "rate": "7",
                                                   "included_in_print_rate": 0,
                                                   "description": " VAT 7% "}]})
        self.assertEqual(claims["totals"], TOTALS)
        self.assertEqual(self.build(), {"totals": TOTALS, "claims": claims})
        claims["terms"]["income_account"] = "MUTATED"
        claims["vat"]["rows"][0]["account_head"] = "MUTATED"
        self.assertEqual(bound.claims()["terms"]["income_account"], "INCOME")
        self.assertEqual(bound.claims()["vat"]["rows"][0]["account_head"], "VAT-LIAB")

    def test_vat_facts_are_absent_from_plain_terms(self):
        claims = self.build()["claims"]
        for field in ("vat_mode", "tax_template", "tax_rows", "tax_account", "tax_rate"):
            self.assertNotIn(field, claims["terms"])

    def test_no_vat_mode_binds_zero_tax_and_no_rows(self):
        claims = self.build((VISIT, NO_VAT_ACCOUNTING, self.doc, NO_VAT_TOTALS),
                            vat_mode="no_vat")["claims"]
        self.assertEqual(claims["vat"], {"mode": "no_vat", "template": None, "account": None,
                                         "rate": None, "rows": []})
        self.assertEqual(claims["totals"]["vat"], "0.00")

    def test_required_literal_vat_mode(self):
        for vat_mode in (None, "", "vat", "vat_6", "VAT_7", " no_vat", 7, True):
            with self.subTest(vat_mode=vat_mode):
                self.assertIsNone(self.build(vat_mode=vat_mode))
                with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                           return_value=self.result) as prepare:
                    self.assertIsNone(
                        self.gateway.prepare_visit_invoice_submission("APT", None, vat_mode))
                prepare.assert_not_called()

    def test_vat_mode_and_tax_fact_drift_refused(self):
        for accounting, totals, vat_mode in (
            (dict(ACCOUNTING, vat_mode="no_vat",
                  tax_template=None, tax_rows=(), tax_account=None, tax_rate=None),
             TOTALS, "vat_7"),
            (NO_VAT_ACCOUNTING, TOTALS, "no_vat"),
            (dict(ACCOUNTING, tax_account="OTHER"), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rate=Decimal("6")), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rows=()), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rows=(dict(TAX_ROW, rate=Decimal("6")),)), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rows=(dict(TAX_ROW, included_in_print_rate=1),)), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rows=(dict(TAX_ROW, charge_type="Actual"),)), TOTALS, "vat_7"),
            (dict(ACCOUNTING, tax_rows=(TAX_ROW, TAX_ROW)), TOTALS, "vat_7"),
            (ACCOUNTING, dict(TOTALS, net="100.01"), "vat_7"),
            (ACCOUNTING, {"currency": "USD", "gross": "100.00", "discount": "0.00",
                          "total": "107.00"}, "vat_7"),
        ):
            with self.subTest(accounting=accounting.get("tax_template"), vat_mode=vat_mode):
                self.assertIsNone(self.build((VISIT, accounting, self.doc, totals),
                                             vat_mode=vat_mode))

    def test_tax_description_required_and_bound(self):
        claims = self.build()["claims"]
        token = issue(self.gateway.visit_invoice_token_key(), claims, now=100)
        self.assertEqual(claims["vat"]["rows"][0]["description"], " VAT 7% ")
        for description in (None, "", "   ", "x" * 513):
            row = dict(TAX_ROW)
            if description is None:
                del row["description"]
            else:
                row["description"] = description
            with self.subTest(description=description):
                self.assertIsNone(self.build((VISIT, dict(ACCOUNTING, tax_rows=(row,)),
                                              self.doc, TOTALS)))
        changed = self.build((VISIT, dict(ACCOUNTING, tax_rows=(
            dict(TAX_ROW, description="Other description"),)), self.doc, TOTALS))["claims"]
        self.assertFalse(verify(self.gateway.visit_invoice_token_key(), changed, token, now=101))

    def test_actor_session_rate_and_tax_facts_are_token_bound(self):
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
        for accounting in (dict(ACCOUNTING, tax_template="VAT 6"),
                           dict(ACCOUNTING, tax_account="OTHER",
                                tax_rows=(dict(TAX_ROW, account_head="OTHER"),)),
                           dict(ACCOUNTING, tax_rate=Decimal("6"),
                                tax_rows=(dict(TAX_ROW, rate=Decimal("6")),))):
            changed = self.build((VISIT, accounting, self.doc, TOTALS))["claims"]
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
            self.assertIsNone(self.gateway.build_visit_invoice_preview("APT", None, "vat_7"))

    def test_submission_preserves_private_typed_facts_and_posts_only_on_dispatch(self):
        visit, accounting, totals = copy.deepcopy(VISIT), copy.deepcopy(ACCOUNTING), dict(TOTALS)
        discount = {"mode": "amount", "value": Decimal("1.00")}
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice",
                   return_value=(visit, accounting, self.doc, totals)):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", discount, "vat_7")
        visit["price"] = Decimal("999")
        accounting["posting_date"] = date(2000, 1, 1)
        accounting["tax_rows"][0]["rate"] = Decimal("6")
        accounting["tax_account"] = "MUTATED"
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
        passed_accounting = args[3]
        self.assertEqual(passed_accounting["posting_date"], date(2026, 11, 20))
        self.assertEqual(passed_accounting["tax_rate"], Decimal("7"))
        self.assertEqual(passed_accounting["tax_rows"][0]["rate"], Decimal("7"))
        self.assertEqual(passed_accounting["tax_account"], "VAT-LIAB")
        self.assertIsInstance(passed_accounting["tax_rows"][0]["rate"], Decimal)

    def test_submission_refuses_changed_wrapper_claims_or_vat_mode(self):
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", None, "vat_7")
        prepared["totals"]["total"] = "1.00"
        with self.assertRaises(ValueError):
            self.gateway.post_visit_invoice_and_verify_facts(prepared)
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", None, "vat_7")
        prepared["claims"] = {**prepared["claims"],
                              "vat": {**prepared["claims"]["vat"], "mode": "no_vat"}}
        with self.assertRaises(ValueError):
            self.gateway.post_visit_invoice_and_verify_facts(prepared)
        with patch("clinic_ui.runtime.visit_invoice_binding.prepare_visit_invoice", return_value=self.result):
            prepared = self.gateway.prepare_visit_invoice_submission("APT", None, "vat_7")
        self.session.sid = "other"
        with self.assertRaises(ValueError):
            self.gateway.post_visit_invoice_and_verify_facts(prepared)


if __name__ == "__main__":
    unittest.main()
