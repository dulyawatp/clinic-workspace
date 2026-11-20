import unittest
from types import SimpleNamespace
from decimal import Decimal
from datetime import date

from clinic_ui.runtime import visit_invoice_accounting
from clinic_ui.runtime.visit_invoice_accounting import (
    VAT_MODE_ADD,
    VAT_MODE_NONE,
    VAT_RATE,
    VAT_TEMPLATE_KEY,
    _unsupported_switches_disabled,
    resolve_visit_invoice_terms,
)


def _key(filters):
    return tuple(sorted(
        (name, tuple(value) if isinstance(value, (list, tuple)) else value)
        for name, value in filters.items()
    ))


class FakeDB:
    def __init__(self):
        self.rows = {}
        self.hits = set()
        self.error = False
        self.calls = []

    def sql(self, query, params):
        return self.rows.get(params, [("0",)])

    def get_value(self, doctype, filters, field, ignore, order_by):
        if self.error:
            raise RuntimeError("query failed")
        assert field == "name" and ignore is False and order_by is None
        self.calls.append((doctype, _key(filters)))
        return "hidden" if (doctype, _key(filters)) in self.hits else None


class FakeFrappe:
    def __init__(self):
        self.db = FakeDB()
        self.bad_field = None
        self.denied = set()
        self.unreadable = set()
        self.child_masked = set()
        self.child_denied_levels = set()
        self.child_parenttypes = []
        self.config = {}
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
                                   options=options, fieldname=name,
                                   permlevel=1 if name in self.child_denied_levels else 0)
        def levels(permission, parenttype=None, user=None):
            self.child_parenttypes.append(parenttype)
            return [0] if parenttype == "Sales Taxes and Charges Template" and doctype not in self.unreadable else []
        return SimpleNamespace(get_field=field, doctype=doctype, get_permlevel_access=levels,
            get_masked_fields=lambda parenttype=None: [
                SimpleNamespace(fieldname=name) for name in self.child_masked]
                if parenttype == "Sales Taxes and Charges Template" else [])

    def has_permission(self, doctype, ptype, doc, user):
        return (doctype, doc) not in self.denied and ptype == "read" and user == "doctor"

    def get_doc(self, doctype, name):
        return SimpleNamespace(name=name, **self.docs[(doctype, name)])

    def new_doc(self, *args, **kwargs):
        raise AssertionError("native write/construction must not happen")


class AccountingCheckpointTest(unittest.TestCase):
    def test_legacy_public_resolver_is_absent(self):
        self.assertFalse(hasattr(visit_invoice_accounting, "resolve_visit_invoice_accounting"))

    def setUp(self):
        self.frappe = FakeFrappe()
        self.gateway = SimpleNamespace(
            frappe=self.frappe,
            _fields_readable=lambda meta, fields: meta.doctype not in self.frappe.unreadable,
            _configuration=lambda: self.frappe.config,
        )
        self.visit = dict(appointment_id="Visit", company="Clinic", customer="Patient",
                          currency="USD", practitioner="Doctor", service="Consult",
                          patient="P", price=Decimal("100.00"))

    def enable_vat(self):
        self.frappe.config[VAT_TEMPLATE_KEY] = "VAT 7"
        self.frappe.docs[("Sales Taxes and Charges Template", "VAT 7")] = dict(
            company="Clinic", is_default=0, disabled=0,
            taxes=[SimpleNamespace(charge_type="On Net Total", account_head="VAT Payable",
                                   rate=7.0, included_in_print_rate=0, description="VAT 7%")])
        self.frappe.docs[("Account", "VAT Payable")] = dict(
            company="Clinic", account_currency="USD", disabled=0, is_group=0,
            root_type="Liability", account_type="Tax")

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
        terms = resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE)
        self.assertEqual(terms, {
            "company": "Clinic", "currency": "USD", "customer": "Patient",
            "income_account": "Revenue", "receivable_account": "Debtors",
            "cost_center": "Clinic CC", "item": "Consult", "uom": "Unit",
            "company_currency": "USD", "party_account_currency": "USD",
            "posting_date": date(2026, 11, 20),
            "selling_price_list": "Standard Selling", "unit_rate": Decimal("100.00"),
            "vat_mode": VAT_MODE_NONE, "tax_template": None, "tax_rows": (),
            "tax_account": None, "tax_rate": None,
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
                self.frappe.db.hits.add((doctype, _key(filters)))
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.db.hits.clear()
        self.frappe.db.error = True
        self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))

    def test_pricing_and_tax_sources_refuse(self):
        for doctype, filters in [
            ("Sales Taxes and Charges Template", {"company": "Clinic", "is_default": 1}),
            ("Sales Taxes and Charges Template",
             {"company": "Clinic", "is_default": ["is", "not set"]}),
            ("Tax Rule", {}), ("Pricing Rule", {}), ("Promotional Scheme", {}),
            ("Item Price", {"item_code": "Consult"}), ("Item Tax", {}),
        ]:
            with self.subTest(doctype=doctype, filters=filters):
                self.frappe.db.hits.add((doctype, _key(filters)))
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.assertIsNone(resolve_visit_invoice_terms(
                    self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.db.hits.clear()

    def test_permission_settings_and_invalid_records_refuse(self):
        for key in self.frappe.docs:
            with self.subTest(denied=key):
                self.frappe.denied.add(key)
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.denied.clear()
            with self.subTest(unreadable=key):
                self.frappe.unreadable.add(key[0])
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.unreadable.clear()
        self.frappe.db.rows[("Selling Settings", "enable_discount_accounting")] = [("1",)]
        self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
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
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.docs[key][field] = old

    def test_integrity_and_document_metadata_drift_refuse(self):
        for key in (("GL Entry", "party"), ("Party Account", "company"),
                    ("Item", "stock_uom"), ("Account", "root_type"),
                    ("Item Price", "price_list_rate"), ("Item Tax", "item_tax_template"),
                    ("Pricing Rule", "disable"), ("Tax Rule", "company"),
                    ("Sales Taxes and Charges", "account_head"),
                    ("Sales Taxes and Charges", "included_in_print_rate")):
            with self.subTest(field=key):
                self.frappe.bad_field = key
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.bad_field = None

    def test_invalid_rate_and_posting_date_refuse(self):
        for rate in (Decimal("0"), Decimal("1.001"), Decimal("NaN"), "100.00", None):
            with self.subTest(rate=rate):
                self.visit["price"] = rate
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
        self.visit["price"] = Decimal("100.00")
        self.frappe.utils.today = lambda: "bad"
        self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))

    def test_template_inventory_queries_are_pinned(self):
        self.assertIsNotNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
        template = [filters for doctype, filters in self.frappe.db.calls
                    if doctype == "Sales Taxes and Charges Template"]
        self.assertEqual(template, [
            _key({"company": "Clinic", "is_default": 1}),
            _key({"company": "Clinic", "is_default": ["is", "not set"]}),
        ])
        self.assertIn(("Tax Rule", ()), self.frappe.db.calls)
        self.assertIn(("Item Tax", ()), self.frappe.db.calls)


class VatTermsTest(unittest.TestCase):
    def setUp(self):
        self.frappe = FakeFrappe()
        self.gateway = SimpleNamespace(
            frappe=self.frappe,
            _fields_readable=lambda meta, fields: meta.doctype not in self.frappe.unreadable,
            _configuration=lambda: self.frappe.config,
        )
        self.visit = dict(appointment_id="Visit", company="Clinic", customer="Patient",
                          currency="USD", practitioner="Doctor", service="Consult",
                          patient="P", price=Decimal("100.00"))

    def setUpVat(self):
        self.frappe.config[VAT_TEMPLATE_KEY] = "VAT 7"
        self.frappe.docs[("Sales Taxes and Charges Template", "VAT 7")] = dict(
            company="Clinic", is_default=0, disabled=0,
            taxes=[SimpleNamespace(charge_type="On Net Total", account_head="VAT Payable",
                                   rate=7.0, included_in_print_rate=0, description="VAT 7%")])
        self.frappe.docs[("Account", "VAT Payable")] = dict(
            company="Clinic", account_currency="USD", disabled=0, is_group=0,
            root_type="Liability", account_type="Tax")

    def template(self):
        return self.frappe.docs[("Sales Taxes and Charges Template", "VAT 7")]

    def row(self):
        return self.template()["taxes"][0]

    def terms(self, mode=VAT_MODE_ADD):
        return resolve_visit_invoice_terms(self.gateway, self.visit, mode)

    def test_vat_mode_resolves_one_explicit_template_row_and_account(self):
        self.setUpVat()
        terms = self.terms()
        self.assertEqual(terms, {
            "company": "Clinic", "currency": "USD", "customer": "Patient",
            "income_account": "Revenue", "receivable_account": "Debtors",
            "cost_center": "Clinic CC", "item": "Consult", "uom": "Unit",
            "company_currency": "USD", "party_account_currency": "USD",
            "posting_date": date(2026, 11, 20),
            "selling_price_list": "Standard Selling", "unit_rate": Decimal("100.00"),
            "vat_mode": VAT_MODE_ADD, "tax_template": "VAT 7",
            "tax_rows": ({"charge_type": "On Net Total", "account_head": "VAT Payable",
                          "rate": VAT_RATE, "included_in_print_rate": 0, "description": "VAT 7%"},),
            "tax_account": "VAT Payable", "tax_rate": VAT_RATE,
        })
        self.assertEqual(terms["tax_rate"], Decimal("7"))

    def test_no_vat_mode_carries_no_template_or_rows(self):
        self.setUpVat()
        terms = self.terms(VAT_MODE_NONE)
        self.assertEqual(terms, {
            "company": "Clinic", "currency": "USD", "customer": "Patient",
            "income_account": "Revenue", "receivable_account": "Debtors",
            "cost_center": "Clinic CC", "item": "Consult", "uom": "Unit",
            "company_currency": "USD", "party_account_currency": "USD",
            "posting_date": date(2026, 11, 20),
            "selling_price_list": "Standard Selling", "unit_rate": Decimal("100.00"),
            "vat_mode": VAT_MODE_NONE, "tax_template": None, "tax_rows": (),
            "tax_account": None, "tax_rate": None,
        })

    def test_no_vat_needs_no_server_tax_configuration(self):
        self.assertIsNone(self.frappe.config.get(VAT_TEMPLATE_KEY))
        self.assertIsNotNone(self.terms(VAT_MODE_NONE))
        self.assertIsNone(self.terms(VAT_MODE_ADD))

    def test_mode_is_required_and_exact(self):
        self.setUpVat()
        for mode in (None, "", "vat", "VAT_7", "no-vat", "no_vat ", 7, True,
                     ["vat_7"], Decimal("7")):
            with self.subTest(mode=mode):
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, mode))

    def test_template_configuration_is_server_only_and_strict(self):
        self.setUpVat()
        for value in (None, 7, True, "", "  ", " VAT 7", "VAT 7 ", "x" * 141,
                      "VAT\n7", ["VAT 7"], {"name": "VAT 7"}):
            with self.subTest(value=value):
                self.frappe.config[VAT_TEMPLATE_KEY] = value
                self.assertIsNone(self.terms())
                self.assertIsNotNone(self.terms(VAT_MODE_NONE))
        del self.frappe.config[VAT_TEMPLATE_KEY]
        self.assertIsNone(self.terms())

    def test_configuration_failure_refuses_vat_only(self):
        self.setUpVat()

        def broken():
            raise RuntimeError("config unavailable")

        gateway = SimpleNamespace(
            frappe=self.frappe,
            _fields_readable=lambda meta, fields: True,
            _configuration=broken,
        )
        self.assertIsNone(resolve_visit_invoice_terms(gateway, self.visit, VAT_MODE_ADD))
        self.assertIsNotNone(resolve_visit_invoice_terms(gateway, self.visit, VAT_MODE_NONE))

    def test_template_record_drift_refuses(self):
        self.setUpVat()
        for field, bad in (
            ("company", "Other"), ("is_default", 1), ("is_default", None),
            ("is_default", True), ("disabled", 1), ("taxes", []), ("taxes", None),
            ("taxes", [self.row(), self.row()]),
        ):
            with self.subTest(field=field, bad=bad):
                old = self.template()[field]
                self.template()[field] = bad
                self.assertIsNone(self.terms())
                self.template()[field] = old

    def test_template_read_authorization_refuses(self):
        self.setUpVat()
        self.frappe.denied.add(("Sales Taxes and Charges Template", "VAT 7"))
        self.assertIsNone(self.terms())
        self.frappe.denied.clear()
        self.frappe.unreadable.add("Sales Taxes and Charges Template")
        self.assertIsNone(self.terms())
        self.frappe.unreadable.clear()
        self.frappe.bad_field = ("Sales Taxes and Charges Template", "taxes")
        self.assertIsNone(self.terms())
        self.frappe.bad_field = None

    def test_tax_row_drift_refuses(self):
        self.setUpVat()
        for field, bad in (
            ("charge_type", "On Previous Row Total"), ("charge_type", "Actual"),
            ("charge_type", "On Item Quantity"), ("charge_type", None),
            ("rate", 6.0), ("rate", 7.5), ("rate", 0), ("rate", None),
            ("rate", "7"), ("rate", True), ("rate", Decimal("7")),
            ("included_in_print_rate", 1), ("included_in_print_rate", None),
            ("included_in_print_rate", "0"),
            ("account_head", ""), ("account_head", None), ("account_head", 7),
        ):
            with self.subTest(field=field, bad=bad):
                old = getattr(self.row(), field)
                setattr(self.row(), field, bad)
                self.assertIsNone(self.terms())
                setattr(self.row(), field, old)

    def test_tax_account_drift_refuses(self):
        self.setUpVat()
        account = self.frappe.docs[("Account", "VAT Payable")]
        for field, bad in (
            ("company", "Other"), ("account_currency", "EUR"), ("disabled", 1),
            ("is_group", 1), ("root_type", "Asset"), ("root_type", "Income"),
            ("account_type", "Chargeable"), ("account_type", None),
        ):
            with self.subTest(field=field, bad=bad):
                old = account[field]
                account[field] = bad
                self.assertIsNone(self.terms())
                account[field] = old
        self.row().account_head = "Revenue"
        self.assertIsNone(self.terms())
        self.row().account_head = "Debtors"
        self.assertIsNone(self.terms())
        self.row().account_head = "Missing"
        self.assertIsNone(self.terms())

    def test_tax_row_authorization_refuses_only_the_vat_branch(self):
        self.setUpVat()
        self.frappe.unreadable.add("Sales Taxes and Charges")
        self.assertIsNone(self.terms())
        self.assertIsNotNone(self.terms(VAT_MODE_NONE))
        self.assertIsNotNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))

    def test_template_child_access_uses_parent_levels_and_mask(self):
        self.setUpVat()
        self.assertIsNotNone(self.terms())
        self.assertIn("Sales Taxes and Charges Template", self.frappe.child_parenttypes)
        self.frappe.child_denied_levels.add("description")
        self.assertIsNone(self.terms())
        self.frappe.child_denied_levels.clear()
        self.frappe.child_masked.add("description")
        self.assertIsNone(self.terms())
        self.frappe.child_masked.clear()
        self.frappe.bad_field = ("Sales Taxes and Charges", "description")
        self.assertIsNone(self.terms())

    def test_missing_blank_and_oversized_description_refused(self):
        self.setUpVat()
        for value in (None, "", "  ", "x" * 513):
            with self.subTest(value=value):
                self.row().description = value
                self.assertIsNone(self.terms())
        del self.row().description
        self.assertIsNone(self.terms())

    def test_description_preserved_at_bound(self):
        self.setUpVat()
        self.row().description = " " + "x" * 510 + " "
        self.assertEqual(self.terms()["tax_rows"][0]["description"], self.row().description)

    def test_same_company_default_or_ambiguous_template_refuses_both_modes(self):
        self.setUpVat()
        for filters in ({"company": "Clinic", "is_default": 1},
                        {"company": "Clinic", "is_default": ["is", "not set"]}):
            with self.subTest(filters=filters):
                self.frappe.db.hits.add(
                    ("Sales Taxes and Charges Template", _key(filters)))
                self.assertIsNone(self.terms())
                self.assertIsNone(self.terms(VAT_MODE_NONE))
                self.assertIsNone(resolve_visit_invoice_terms(self.gateway, self.visit, VAT_MODE_NONE))
                self.frappe.db.hits.clear()

    def test_native_field_drift_refuses_both_modes(self):
        self.setUpVat()
        self.frappe.bad_field = ("Sales Taxes and Charges", "rate")
        self.assertIsNone(self.terms())
        self.assertIsNone(self.terms(VAT_MODE_NONE))
        self.frappe.bad_field = None
        self.assertIsNotNone(self.terms())


if __name__ == "__main__":
    unittest.main()
