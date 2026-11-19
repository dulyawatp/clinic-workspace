import unittest
from types import SimpleNamespace
from decimal import Decimal
from datetime import date

from clinic_ui.runtime.visit_invoice_accounting import (
    _unsupported_switches_disabled,
    resolve_visit_invoice_accounting,
)


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.hits = set()
        self.error = False

    def sql(self, query, params):
        return self.rows.get(params, [("0",)])

    def get_value(self, doctype, filters, field, ignore, order_by):
        if self.error:
            raise RuntimeError("query failed")
        assert field == "name" and ignore is False and order_by is None
        return "hidden" if (doctype, tuple(sorted(filters.items()))) in self.hits else None


class FakeFrappe:
    def __init__(self):
        self.db = FakeDB()
        self.bad_field = None
        self.denied = set()
        self.unreadable = set()
        self.session = SimpleNamespace(user="doctor")
        self.utils = SimpleNamespace(today=lambda: "2026-11-20")
        self.docs = {
            ("Company", "Clinic"): dict(default_currency="USD", default_receivable_account="Debtors",
                                        default_income_account="Revenue", cost_center="Clinic CC"),
            ("Customer", "Patient"): dict(customer_group="Group", disabled=0, default_currency="USD",
                                           default_price_list=None),
            ("Customer Group", "Group"): dict(default_price_list=None),
            ("Healthcare Practitioner", "Doctor"): {},
            ("Selling Settings", "Selling Settings"): dict(selling_price_list="Standard Selling"),
            ("Price List", "Standard Selling"): dict(enabled=1, selling=1, currency="USD"),
            ("Account", "Debtors"): dict(company="Clinic", account_currency="USD", disabled=0,
                                           is_group=0, root_type="Asset", account_type="Receivable"),
            ("Account", "Revenue"): dict(company="Clinic", account_currency="USD", disabled=0,
                                           is_group=0, root_type="Income", account_type="Income Account"),
            ("Cost Center", "Clinic CC"): dict(company="Clinic", disabled=0, is_group=0),
            ("Item", "Consult"): dict(disabled=0, is_sales_item=1, is_stock_item=0,
                                        is_fixed_asset=0, stock_uom="Unit", variant_of=None),
            ("UOM", "Unit"): dict(enabled=1),
        }

    def get_meta(self, doctype):
        from clinic_ui.runtime.visit_invoice_accounting import _FIELDS, _INTEGRITY_FIELDS
        fields = {**_FIELDS.get(doctype, {}), **_INTEGRITY_FIELDS.get(doctype, {})}
        def field(name):
            kind, options = fields.get(name, ("Check", None))
            return SimpleNamespace(fieldtype="Data" if (doctype, name) == self.bad_field else kind,
                                   options=options)
        return SimpleNamespace(get_field=field, doctype=doctype)

    def has_permission(self, doctype, ptype, doc, user):
        return (doctype, doc) not in self.denied and ptype == "read" and user == "doctor"

    def get_doc(self, doctype, name):
        return SimpleNamespace(name=name, **self.docs[(doctype, name)])

    def new_doc(self, *args, **kwargs):
        raise AssertionError("native write/construction must not happen")


class AccountingCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.frappe = FakeFrappe()
        self.gateway = SimpleNamespace(
            frappe=self.frappe,
            _fields_readable=lambda meta, fields: meta.doctype not in self.frappe.unreadable,
        )
        self.visit = dict(appointment_id="Visit", company="Clinic", customer="Patient",
                          currency="USD", practitioner="Doctor", service="Consult",
                          patient="P", price=Decimal("100.00"))

    def test_raw_switches_require_exactly_one_explicit_zero(self):
        self.assertTrue(_unsupported_switches_disabled(self.gateway))
        for key in (
            ("Healthcare Settings", "enable_free_follow_ups"),
            ("Healthcare Settings", "show_payment_popup"),
            ("Healthcare Settings", "create_lab_test_on_si_submit"),
            ("Healthcare Settings", "create_observation_on_si_submit"),
            ("Selling Settings", "enable_discount_accounting"),
        ):
            for rows in ([], [("1",)], [(0,)], [(None,)], [("0",), ("0",)]):
                with self.subTest(key=key, rows=rows):
                    self.frappe.db.rows[key] = rows
                    self.assertFalse(_unsupported_switches_disabled(self.gateway))
            self.frappe.db.rows.pop(key)
            self.frappe.bad_field = key
            self.assertFalse(_unsupported_switches_disabled(self.gateway))
            self.frappe.bad_field = None

    def test_returns_baseline_without_claiming_complete_preview_terms(self):
        terms = resolve_visit_invoice_accounting(self.gateway, self.visit)
        self.assertEqual(terms, {
            "company": "Clinic", "currency": "USD", "customer": "Patient",
            "income_account": "Revenue", "receivable_account": "Debtors",
            "cost_center": "Clinic CC", "item": "Consult", "uom": "Unit",
            "company_currency": "USD", "party_account_currency": "USD",
            "posting_date": date(2026, 11, 20),
            "selling_price_list": "Standard Selling", "unit_rate": Decimal("100.00"),
        })

    def test_history_and_overrides_fail_closed(self):
        cases = [
            ("GL Entry", dict(party_type="Customer", party="Patient", company="Clinic")),
            ("Party Account", dict(parenttype="Customer", parent="Patient", company="Clinic")),
            ("Party Account", dict(parenttype="Customer Group", parent="Group", company="Clinic")),
            ("Party Account", dict(parenttype="Healthcare Practitioner", parent="Doctor", company="Clinic")),
            ("Party Account", dict(parentfield="income_account", parent="Healthcare Settings", company="Clinic")),
        ]
        for doctype, filters in cases:
            with self.subTest(doctype=doctype, filters=filters):
                self.frappe.db.hits.add((doctype, tuple(sorted(filters.items()))))
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.db.hits.clear()
        self.frappe.db.error = True
        self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))

    def test_pricing_and_tax_sources_refuse(self):
        for doctype, filters in [
            ("Sales Taxes and Charges Template", {}), ("Tax Rule", {}),
            ("Pricing Rule", {}), ("Promotional Scheme", {}),
            ("Item Price", {"item_code": "Consult"}), ("Item Tax", {}),
        ]:
            with self.subTest(doctype=doctype):
                self.frappe.db.hits.add((doctype, tuple(sorted(filters.items()))))
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.db.hits.clear()

    def test_permission_settings_and_invalid_records_refuse(self):
        for key in self.frappe.docs:
            with self.subTest(denied=key):
                self.frappe.denied.add(key)
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.denied.clear()
            with self.subTest(unreadable=key):
                self.frappe.unreadable.add(key[0])
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.unreadable.clear()
        self.frappe.db.rows[("Selling Settings", "enable_discount_accounting")] = [("1",)]
        self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
        self.frappe.db.rows.clear()
        for key, field, bad in [
            (("Company", "Clinic"), "default_income_account", ""),
            (("Company", "Clinic"), "default_receivable_account", "Revenue"),
            (("Company", "Clinic"), "cost_center", None),
            (("Customer", "Patient"), "customer_group", ""),
            (("Customer", "Patient"), "default_currency", "EUR"),
            (("Customer", "Patient"), "default_price_list", "Other"),
            (("Customer Group", "Group"), "default_price_list", "Other"),
            (("Price List", "Standard Selling"), "currency", "EUR"),
            (("Price List", "Standard Selling"), "selling", 0),
            (("Item", "Consult"), "variant_of", "Template"),
            (("Account", "Debtors"), "account_currency", "EUR"),
            (("Account", "Revenue"), "root_type", "Asset"),
            (("Account", "Debtors"), "is_group", 1),
            (("Cost Center", "Clinic CC"), "company", "Other"),
            (("Item", "Consult"), "is_stock_item", 1),
            (("Item", "Consult"), "is_fixed_asset", 1),
            (("UOM", "Unit"), "enabled", 0),
        ]:
            with self.subTest(key=key, field=field):
                old = self.frappe.docs[key][field]
                self.frappe.docs[key][field] = bad
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.docs[key][field] = old

    def test_integrity_and_document_metadata_drift_refuse(self):
        for key in (("GL Entry", "party"), ("Party Account", "company"),
                    ("Item", "stock_uom"), ("Account", "root_type"),
                    ("Item Price", "price_list_rate"), ("Item Tax", "item_tax_template"),
                    ("Pricing Rule", "disable"), ("Tax Rule", "company")):
            with self.subTest(field=key):
                self.frappe.bad_field = key
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
                self.frappe.bad_field = None

    def test_invalid_rate_and_posting_date_refuse(self):
        for rate in (Decimal("0"), Decimal("1.001"), Decimal("NaN"), "100.00", None):
            with self.subTest(rate=rate):
                self.visit["price"] = rate
                self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))
        self.visit["price"] = Decimal("100.00")
        self.frappe.utils.today = lambda: "bad"
        self.assertIsNone(resolve_visit_invoice_accounting(self.gateway, self.visit))


if __name__ == "__main__":
    unittest.main()
