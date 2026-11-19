"""Focused adapter tests for the converted invoice lane.

A minimal fake Frappe module exercises the real gateway methods without an
installed site. These tests cover what the endpoint fake cannot: native
create/submit authority, loaded-document permission checks, native ERPNext
pricing integration, posted-document verification and bounded discovery.
"""

import ast
import inspect
import math
import sys
import types
import unittest
from unittest import mock
from pathlib import Path

from clinic_ui.core import finance_contract
from clinic_ui.runtime import frappe_gateway


def frappe_legacy_round(value, precision=2):
    multiplier = 10**precision
    number = round(float(value) * multiplier, 8)
    floor_number = math.floor(number)
    if number - floor_number == 0.5:
        number = floor_number + 1
    else:
        number = round(number)
    return number / multiplier


class Ctx(dict):
    """Minimal ``frappe._dict`` stand-in: mapping plus attribute access."""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self[name] = value


class FakeField:
    def __init__(self, permlevel=0, fieldname=None):
        self.permlevel = permlevel
        self.fieldname = fieldname


class FakeMeta:
    def __init__(self, blocked=(), masked=()):
        self.blocked = set(blocked)
        self.masked = set(masked)
        self.fields = []
        self.default_fields = ()

    def get_permlevel_access(self, permission_type="read", parenttype=None, *, user=None):
        return [0]

    def get_field(self, fieldname):
        if fieldname in self.blocked:
            return None
        return FakeField(0, fieldname)

    def get_masked_fields(self, parenttype=None):
        return [FakeField(0, name) for name in self.masked]


class FakeDB:
    def __init__(self):
        self.existing = set()
        self.values = {}
        self.get_single_calls = []
        self.exists_calls = []

    def exists(self, doctype, name):
        self.exists_calls.append((doctype, name))
        return (doctype, name) in self.existing

    def get_value(self, doctype, name, fieldname=None, as_dict=False, **kwargs):
        if isinstance(fieldname, (list, tuple)):
            row = {
                field: self.values.get((doctype, name, field))
                for field in fieldname
            }
            return row if as_dict else tuple(row[field] for field in fieldname)
        return self.values.get((doctype, name, fieldname))

    def get_all(self, doctype, filters=None, fields=None, **kwargs):
        return []

    def get_single_value(self, doctype, fieldname):
        self.get_single_calls.append((doctype, fieldname))
        return None


class FakeFrappe(types.SimpleNamespace):
    def __init__(self):
        super().__init__()
        self.local = types.SimpleNamespace(site="marley-sit.local")
        self.session = types.SimpleNamespace(user="cashier@example.test")
        self.db = FakeDB()
        self.metas = {}
        self.denied = set()
        self.list_rows = {}
        self.list_calls = []
        self.doc_calls = []
        self._dict = Ctx
        self.utils = types.SimpleNamespace(
            nowdate=lambda: "2026-11-16",
            flt=frappe_legacy_round,
        )
        self.defaults = types.SimpleNamespace(
            get_user_default=self._company_default
        )
        self._company = None

    def _company_default(self, key):
        return self._company if key == "Company" else None

    def get_meta(self, doctype):
        return self.metas.setdefault(doctype, FakeMeta())

    def get_doc(self, doctype, name=None, **kwargs):
        self.doc_calls.append((doctype, name))
        attributes = {"doctype": doctype, "name": name}
        for (row_doctype, row_name, field), value in self.db.values.items():
            if row_doctype == doctype and row_name == name:
                attributes[field] = value
        return types.SimpleNamespace(**attributes)

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        target = getattr(doc, "name", doc) if doc is not None else None
        if target is None:
            return (doctype, ptype, None) not in self.denied
        return (doctype, ptype, target) not in self.denied

    def get_list(
        self,
        doctype,
        filters=None,
        fields=None,
        order_by=None,
        limit_page_length=None,
        **kwargs,
    ):
        self.list_calls.append(
            (doctype, dict(filters or {}), tuple(fields or ()), limit_page_length)
        )
        return list(self.list_rows.get(doctype, []))


def priced(args, **overrides):
    result = {
        "rate": 10.0,
        "price_list_rate": 10.0,
        "qty": args["qty"],
        "conversion_factor": 1.0,
        "uom": args["uom"],
        "stock_uom": args["stock_uom"],
        "currency": args["currency"],
        "discount_amount": 0.0,
        "discount_percentage": 0.0,
        "income_account": "Sales - SITCO",
        "cost_center": "Main - SITCO",
    }
    result.update(overrides)
    return result


def make_gateway():
    frappe = FakeFrappe()
    frappe._company = "SIT Synthetic Company USD"
    frappe.db.existing.update(
        {
            ("Customer", "CUST-1"),
            ("Item", "ITEM-1"),
            ("Price List", "Standard Selling"),
            ("Company", "SIT Synthetic Company USD"),
        }
    )
    frappe.db.values.update(
        {
            ("Company", "SIT Synthetic Company USD", "default_currency"): "USD",
            (
                "Company",
                "SIT Synthetic Company USD",
                "default_receivable_account",
            ): "Debtors - SITCO",
            (
                "Company",
                "SIT Synthetic Company USD",
                "default_income_account",
            ): "Sales - SITCO",
            ("Company", "SIT Synthetic Company USD", "cost_center"): "Main - SITCO",
            ("Item", "ITEM-1", "is_sales_item"): 1,
            ("Item", "ITEM-1", "stock_uom"): "Unit",
        }
    )
    gateway = frappe_gateway.Gateway(frappe)
    gateway._finance_fields_cache = {finance_contract.DEDUCTION_LINK_KEY: False}
    calls = []
    gateway._pricing_calls = calls

    def price_list_currency(price_list, company, transaction_date):
        return "USD", 1.0

    def get_item_details(args):
        calls.append(dict(args))
        return priced(args)

    gateway._finance_price_list_currency = price_list_currency
    gateway._finance_get_item_details = get_item_details
    return types.SimpleNamespace(frappe=frappe, gateway=gateway, calls=calls)


def payload():
    return {
        "customer": "CUST-1",
        "selling_price_list": "Standard Selling",
        "due_date": "2026-11-30",
        "items": [{"item_code": "ITEM-1", "qty": 2}],
    }


class AuthorityTests(unittest.TestCase):
    def test_submit_authority_uses_the_native_submit_ptype(self):
        class Recording(FakeFrappe):
            def __init__(self):
                super().__init__()
                self.calls = []

            def has_permission(self, doctype, ptype="read", doc=None, user=None):
                self.calls.append((doctype, ptype, doc))
                return True

        frappe = Recording()
        gateway = frappe_gateway.Gateway(frappe)

        self.assertTrue(gateway.native_finance_can_submit("Sales Invoice"))
        self.assertIn(("Sales Invoice", "submit", None), frappe.calls)

    def test_create_authority_stays_a_create_check(self):
        class Recording(FakeFrappe):
            def __init__(self):
                super().__init__()
                self.calls = []

            def has_permission(self, doctype, ptype="read", doc=None, user=None):
                self.calls.append((doctype, ptype, doc))
                return True

        frappe = Recording()
        gateway = frappe_gateway.Gateway(frappe)

        self.assertTrue(gateway.native_finance_can_write("Sales Invoice"))
        self.assertIn(("Sales Invoice", "create", None), frappe.calls)


class MetaCollectorTests(unittest.TestCase):
    def test_collector_includes_true_meta_table_fields_and_defaults(self):
        stub = make_gateway()
        gateway = frappe_gateway.Gateway(stub.frappe)
        meta = FakeMeta()
        meta.fields = [
            FakeField(0, "taxes"),
            FakeField(0, "items"),
            FakeField(0, "customer"),
        ]
        meta.default_fields = ("name", "docstatus")
        stub.frappe.metas["Sales Invoice"] = meta

        fields = gateway._finance_meta_fields()

        self.assertIn("taxes", fields["Sales Invoice"])
        self.assertIn("items", fields["Sales Invoice"])
        self.assertIn("name", fields["Sales Invoice"])
        self.assertIn("docstatus", fields["Sales Invoice"])


class PricingContextTests(unittest.TestCase):
    def _module(self, function):
        root = types.ModuleType("erpnext")
        stock = types.ModuleType("erpnext.stock")
        details = types.ModuleType("erpnext.stock.get_item_details")
        details.get_price_list_currency_and_exchange_rate = function
        details.get_item_details = function
        root.stock = stock
        stock.get_item_details = details
        return {
            "erpnext": root,
            "erpnext.stock": stock,
            "erpnext.stock.get_item_details": details,
        }

    def test_price_list_currency_uses_frappe_dict_context(self):
        stub = make_gateway()
        seen = {}

        def native(ctx):
            if not isinstance(ctx, Ctx):
                raise AttributeError("ctx must be frappe._dict")
            seen.update(ctx)
            return Ctx(price_list_currency="USD", plc_conversion_rate=1.0)

        with mock.patch.dict(sys.modules, self._module(native)):
            currency, rate = (
                frappe_gateway.Gateway._finance_price_list_currency(
                    stub.gateway,
                    "Standard Selling",
                    "SIT Synthetic Company USD",
                    "2026-11-16",
                )
            )

        self.assertEqual((currency, rate), ("USD", 1.0))
        self.assertEqual(seen["price_list"], "Standard Selling")

    def test_get_item_details_receives_frappe_dict_context(self):
        stub = make_gateway()
        seen = {}

        def native(ctx):
            if not isinstance(ctx, Ctx):
                raise AttributeError("ctx must be frappe._dict")
            seen.update(ctx)
            return Ctx(rate=10.0, price_list_rate=10.0)

        with mock.patch.dict(sys.modules, self._module(native)):
            result = frappe_gateway.Gateway._finance_get_item_details(
                stub.gateway,
                {"item_code": "ITEM-1", "qty": 2, "uom": "Unit"},
            )

        self.assertEqual(result["rate"], 10.0)
        self.assertEqual(seen["item_code"], "ITEM-1")


class DetailTests(unittest.TestCase):
    def test_readable_masters_and_native_pricing_resolve_the_line(self):
        stub = make_gateway()

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertTrue(detail["customer_exists"])
        self.assertEqual(detail["prices"]["ITEM-1"], 10.0)
        self.assertEqual(detail["income_accounts"]["ITEM-1"], "Sales - SITCO")
        self.assertEqual(detail["cost_centers"]["ITEM-1"], "Main - SITCO")
        self.assertEqual(detail["price_currency"], "USD")
        self.assertFalse(detail["discount_available"])
        self.assertEqual(detail["price_unverified"], ())
        self.assertEqual(stub.calls[0]["customer"], "CUST-1")
        self.assertEqual(stub.calls[0]["selling_price_list"], "Standard Selling")
        self.assertEqual(stub.calls[0]["transaction_date"], "2026-11-16")
        self.assertEqual(stub.calls[0]["qty"], 2)
        self.assertEqual(stub.calls[0]["uom"], "Unit")

    def test_an_unreadable_customer_is_denied_from_the_loaded_document(self):
        stub = make_gateway()
        stub.frappe.denied.add(("Customer", "read", "CUST-1"))

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertTrue(detail["customer_denied"])

    def test_an_unreadable_item_is_denied_and_pricing_is_not_called(self):
        stub = make_gateway()
        stub.frappe.denied.add(("Item", "read", "ITEM-1"))

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertTrue(detail["items"]["ITEM-1"]["denied"])
        self.assertEqual(stub.calls, [])

    def test_missing_item_price_permission_does_not_block_native_pricing(self):
        stub = make_gateway()
        stub.frappe.denied.update(
            {
                ("Item Price", "read", None),
                ("Price List", "read", "Standard Selling"),
            }
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertEqual(detail["prices"]["ITEM-1"], 10.0)
        self.assertEqual(detail["price_unverified"], ())

    def test_uom_mismatch_refuses_the_price(self):
        stub = make_gateway()
        stub.gateway._finance_get_item_details = lambda args: priced(
            args, uom="Box", stock_uom="Unit"
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])

    def test_quantity_mismatch_refuses_the_price(self):
        stub = make_gateway()
        stub.gateway._finance_get_item_details = lambda args: priced(args, qty=3)

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])

    def test_discount_or_margin_from_pricing_refuses_the_price(self):
        for overrides in (
            {"discount_amount": 1.0},
            {"discount_percentage": 5.0},
            {"rate": 9.0},
        ):
            with self.subTest(overrides=overrides):
                stub = make_gateway()
                stub.gateway._finance_get_item_details = (
                    lambda args, overrides=overrides: priced(args, **overrides)
                )

                detail = stub.gateway.native_finance_invoice_detail(payload())

                self.assertIn("ITEM-1", detail["price_unverified"])

    def test_price_currency_mismatch_refuses_every_line(self):
        stub = make_gateway()
        stub.gateway._finance_price_list_currency = (
            lambda price_list, company, date: ("THB", 1.0)
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])
        self.assertEqual(detail["price_currency"], "THB")

    def test_non_unit_conversion_rate_refuses_every_line(self):
        stub = make_gateway()
        stub.gateway._finance_price_list_currency = (
            lambda price_list, company, date: ("USD", 1.5)
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])

    def test_native_pricing_failure_is_a_refusal_not_a_permission_error(self):
        stub = make_gateway()

        def fail(args):
            raise RuntimeError("pricing unavailable")

        stub.gateway._finance_get_item_details = fail

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])

    def test_zero_native_rate_falls_back_to_the_price_list_rate(self):
        stub = make_gateway()
        stub.gateway._finance_get_item_details = lambda args: priced(
            args, rate=0.0
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertEqual(detail["prices"]["ITEM-1"], 10.0)
        self.assertEqual(detail["price_unverified"], ())

    def test_an_applied_pricing_rule_with_zero_rate_is_unverified(self):
        stub = make_gateway()
        stub.gateway._finance_get_item_details = lambda args: priced(
            args, rate=0.0, pricing_rules='["PR-1"]'
        )

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertIn("ITEM-1", detail["price_unverified"])

    def test_detail_carries_the_installed_money_rounder(self):
        stub = make_gateway()

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertEqual(detail["money_rounder"](0.125), 0.13)

    def test_the_company_is_the_session_default_and_never_a_global_guess(self):
        stub = make_gateway()
        stub.frappe._company = None

        detail = stub.gateway.native_finance_invoice_detail(payload())

        self.assertFalse(detail["company"])
        self.assertEqual(stub.frappe.db.get_single_calls, [])

    def test_there_is_no_ignore_permissions_on_the_lane(self):
        root = Path(inspect.getfile(frappe_gateway)).parent.parent
        endpoint = root / "endpoints" / "finance_write.py"
        self.assertNotIn("ignore_permissions", endpoint.read_text(encoding="utf-8"))
        tree = ast.parse(inspect.getsource(frappe_gateway))
        names = {
            node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        } | {
            keyword.arg
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for keyword in node.keywords
        }
        self.assertNotIn("ignore_permissions", names)


class ReadTests(unittest.TestCase):
    def _invoice(self, **overrides):
        values = {
            "name": "SINV-1",
            "status": "Unpaid",
            "docstatus": 1,
            "currency": "USD",
            "grand_total": 20.0,
            "net_total": 20.0,
            "outstanding_amount": 20.0,
            "update_stock": 0,
        }
        values.update(overrides)
        return {
            ("Sales Invoice", "SINV-1", field): value
            for field, value in values.items()
        }

    def test_view_requires_the_loaded_document_read_permission(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._invoice())
        stub.frappe.denied.add(("Sales Invoice", "read", "SINV-1"))

        self.assertIsNone(
            stub.gateway.native_finance_invoice_view("SINV-1")
        )
        self.assertIn(("Sales Invoice", "SINV-1"), stub.frappe.doc_calls)

    def test_view_requires_a_readable_status_field(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._invoice())
        stub.frappe.metas["Sales Invoice"] = FakeMeta(blocked={"status"})

        self.assertIsNone(
            stub.gateway.native_finance_invoice_view("SINV-1")
        )

    def test_view_returns_only_the_three_fields(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._invoice())

        self.assertEqual(
            stub.gateway.native_finance_invoice_view("SINV-1"),
            {"invoice_id": "SINV-1", "status": "Unpaid", "docstatus": 1},
        )

    def test_discovery_is_bounded_and_permission_filtered(self):
        stub = make_gateway()
        stub.frappe.list_rows["Sales Invoice"] = [{"name": "SINV-1"}]

        self.assertEqual(
            stub.gateway.native_finance_invoice_names("CUST-1", 50), ["SINV-1"]
        )
        self.assertEqual(
            stub.frappe.list_calls,
            [
                (
                    "Sales Invoice",
                    {"customer": "CUST-1"},
                    ("name",),
                    51,
                )
            ],
        )

    def test_an_over_full_page_is_not_truncated(self):
        stub = make_gateway()
        stub.frappe.list_rows["Sales Invoice"] = [
            {"name": "SINV-1"},
            {"name": "SINV-2"},
        ]

        self.assertIsNone(
            stub.gateway.native_finance_invoice_names("CUST-1", 1)
        )


class PostedFactsTests(unittest.TestCase):
    def _expected(self):
        return finance_contract.expected_invoice(
            {
                "currency": "USD",
                "receivable_account": "Debtors - SITCO",
                "income_accounts": {"ITEM-1": "Sales - SITCO"},
                "prices": {"ITEM-1": 10.0},
            },
            {
                "items": [{"item_code": "ITEM-1", "qty": 2}],
                "discount": 0.0,
            },
        )

    def _posted(self, **overrides):
        values = {
            "name": "SINV-1",
            "status": "Unpaid",
            "docstatus": 1,
            "currency": "USD",
            "grand_total": 20.0,
            "net_total": 20.0,
            "outstanding_amount": 20.0,
            "update_stock": 0,
        }
        values.update(overrides)
        return {
            ("Sales Invoice", "SINV-1", field): value
            for field, value in values.items()
        }

    def test_posted_facts_read_the_actual_document_and_ledger(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._posted())
        stub.frappe.list_rows[finance_contract.GL_ENTRY_DOCTYPE] = [
            {"account": "Debtors - SITCO", "debit": 20.0, "credit": 0.0},
            {"account": "Sales - SITCO", "debit": 0.0, "credit": 20.0},
        ]
        stub.frappe.list_rows[finance_contract.PAYMENT_LEDGER_DOCTYPE] = [
            {"name": "PLE-1"}
        ]

        facts = stub.gateway.native_finance_invoice_posted_facts(
            "SINV-1", self._expected()
        )

        self.assertEqual(facts["grand_total"], 20.0)
        self.assertEqual(facts["net_total"], 20.0)
        self.assertEqual(facts["currency"], "USD")
        self.assertEqual(facts["gl_rows"], 2)
        self.assertEqual(facts["debit_total"], 20.0)
        self.assertEqual(facts["credit_total"], 20.0)
        self.assertEqual(facts["ple_rows"], 1)
        self.assertIsNone(facts["sle_rows"])
        self.assertFalse(facts["sle_verified"])
        self.assertEqual(facts["update_stock"], 0)
        self.assertNotIn(
            finance_contract.STOCK_LEDGER_DOCTYPE,
            [call[0] for call in stub.frappe.list_calls],
        )
        self.assertIn(("Sales Invoice", "SINV-1"), stub.frappe.doc_calls)

    def test_posted_facts_refuse_a_denied_document(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._posted())
        stub.frappe.denied.add(("Sales Invoice", "read", "SINV-1"))

        self.assertIsNone(
            stub.gateway.native_finance_invoice_posted_facts(
                "SINV-1", self._expected()
            )
        )

    def test_posted_facts_refuse_an_unreadable_gl_ledger(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._posted())
        stub.frappe.denied.add(
            (finance_contract.GL_ENTRY_DOCTYPE, "read", None)
        )

        self.assertIsNone(
            stub.gateway.native_finance_invoice_posted_facts(
                "SINV-1", self._expected()
            )
        )

    def test_a_nonzero_update_stock_is_reported(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._posted(update_stock=1))
        stub.frappe.list_rows[finance_contract.GL_ENTRY_DOCTYPE] = [
            {"account": "Debtors - SITCO", "debit": 20.0, "credit": 0.0},
            {"account": "Sales - SITCO", "debit": 0.0, "credit": 20.0},
        ]
        stub.frappe.list_rows[finance_contract.PAYMENT_LEDGER_DOCTYPE] = [
            {"name": "PLE-1"}
        ]

        facts = stub.gateway.native_finance_invoice_posted_facts(
            "SINV-1", self._expected()
        )

        self.assertEqual(
            finance_contract.side_effect_problem(facts, self._expected()),
            "the posted invoice update_stock differs",
        )

    def test_posted_facts_refuse_a_masked_total(self):
        stub = make_gateway()
        stub.frappe.db.values.update(self._posted())
        stub.frappe.metas["Sales Invoice"] = FakeMeta(masked={"grand_total"})

        self.assertIsNone(
            stub.gateway.native_finance_invoice_posted_facts(
                "SINV-1", self._expected()
            )
        )

    def test_actual_total_and_currency_mismatches_are_reported(self):
        for overrides, message in (
            ({"grand_total": 19.0}, "the posted total differs"),
            ({"currency": "THB"}, "the posted invoice currency differs"),
            ({"net_total": 18.0}, "the posted net total differs"),
        ):
            with self.subTest(overrides=overrides):
                stub = make_gateway()
                stub.frappe.db.values.update(self._posted(**overrides))
                stub.frappe.list_rows[finance_contract.GL_ENTRY_DOCTYPE] = [
                    {"account": "Debtors - SITCO", "debit": 20.0, "credit": 0.0},
                    {"account": "Sales - SITCO", "debit": 0.0, "credit": 20.0},
                ]
                stub.frappe.list_rows[
                    finance_contract.PAYMENT_LEDGER_DOCTYPE
                ] = [{"name": "PLE-1"}]

                facts = stub.gateway.native_finance_invoice_posted_facts(
                    "SINV-1", self._expected()
                )

                self.assertIn(
                    message,
                    finance_contract.side_effect_problem(
                        facts, self._expected()
                    ),
                )


if __name__ == "__main__":
    unittest.main()
