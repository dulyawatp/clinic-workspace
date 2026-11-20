import unittest
from unittest.mock import patch
from datetime import date
from decimal import Decimal

from clinic_ui.runtime.visit_invoice_preview import preview_visit_invoice, prepare_visit_invoice


VISIT = dict(appointment_id="APT-1", practitioner="DR-1", patient="PAT-1",
             customer="CUS-1", company="Clinic", currency="USD",
             service="CONSULT", price=Decimal("100"))
ACCOUNTING = dict(company="Clinic", currency="USD", customer="CUS-1",
                  income_account="Income", receivable_account="Receivable",
                  cost_center="Center", item="CONSULT", uom="Visit",
                  company_currency="USD", party_account_currency="USD",
                  posting_date=date(2026, 11, 20), selling_price_list="Standard",
                  unit_rate=Decimal("100"))
VAT_ROW = dict(charge_type="On Net Total", account_head="VAT Payable",
               rate=Decimal("7"), included_in_print_rate=0, description="VAT 7%")
VAT_TERMS = dict(ACCOUNTING, vat_mode="vat_7", tax_template="VAT 7",
                 tax_rows=(VAT_ROW,), tax_account="VAT Payable", tax_rate=Decimal("7"))
NO_VAT_TERMS = dict(ACCOUNTING, vat_mode="no_vat", tax_template=None,
                    tax_rows=(), tax_account=None, tax_rate=None)


class FakeDocument:
    def __init__(self, values, outcome):
        self.__dict__.update(values)
        self.items = [type("Row", (), {**row, "precision": lambda self, field: 2})()
                      for row in values["items"]]
        self.taxes = []
        self.outcome = outcome

    def calculate_taxes_and_totals(self):
        self.__dict__.update(self.outcome)
        row = self.items[0]
        row.amount = row.base_amount = self.total
        row.net_amount = row.base_net_amount = self.net_total
        row.distributed_discount_amount = self.discount_amount
        for key, value in self.outcome.get("row_changes", {}).items():
            setattr(self.items[0], key, value)

    def precision(self, field):
        return 2


class FakeFrappe:
    def __init__(self, outcome):
        self.outcome = outcome
        self.document = None

    def get_doc(self, values):
        self.document = FakeDocument(values, self.outcome)
        return self.document


class NativeShapeDocument(FakeDocument):
    def __init__(self, values, outcome):
        super().__init__(values, outcome)
        self.outstanding_amount = None

    def calculate_taxes_and_totals(self):
        # Pinned controller uses float operands in these branches.
        assert self.posting_date == date(2026, 11, 20)
        assert self.party_account_currency == "USD"
        gross = float(self.items[0].rate) * float(self.items[0].qty)
        if self.additional_discount_percentage:
            self.discount_amount = gross * self.additional_discount_percentage / 100.0
        if self.discount_amount:
            self.base_discount_amount = self.discount_amount * float(self.conversion_rate)
        else:
            # Native leaves supplied zero untouched in the no-discount branch.
            self.base_discount_amount = 0.0
        total = gross - self.discount_amount
        self.total = self.base_total = gross
        self.net_total = self.grand_total = total
        self.base_net_total = self.base_grand_total = total
        self.total_taxes_and_charges = self.base_total_taxes_and_charges = 0.0
        row = self.items[0]
        row.amount = row.base_amount = gross
        row.net_amount = row.base_net_amount = total
        row.distributed_discount_amount = self.discount_amount
        self.rounded_total = self.base_rounded_total = total
        self.rounding_adjustment = self.base_rounding_adjustment = 0.0


class NativeShapeFrappe:
    def get_doc(self, values):
        self.document = NativeShapeDocument(values, {})
        return self.document


class Gateway:
    def __init__(self, frappe):
        self.frappe = frappe


class CompositionTest(unittest.TestCase):
    def test_explicit_mode_resolves_terms_and_rejects_invalid_mode(self):
        gateway = Gateway(NativeShapeFrappe())
        with patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice", return_value=VISIT), \
             patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice_terms",
                   return_value=NO_VAT_TERMS) as resolver:
            result = prepare_visit_invoice(gateway, "APT-1", None, "no_vat")
            self.assertEqual(result[3]["vat"], "0.00")
            resolver.assert_called_once_with(gateway, VISIT, "no_vat")
        with patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice", return_value=VISIT), \
             patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice_terms",
                   return_value=None) as resolver:
            self.assertIsNone(prepare_visit_invoice(gateway, "APT-1", None, None))
            resolver.assert_called_once_with(gateway, VISIT, None)
        with patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice", return_value=VISIT), \
             patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice_terms",
                   return_value=NO_VAT_TERMS):
            self.assertIsNone(prepare_visit_invoice(gateway, "APT-1", None, "vat_7"))

    def prepare(self, accounting=NO_VAT_TERMS, frappe=None):
        frappe = frappe or NativeShapeFrappe()
        with patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice", return_value=VISIT), \
             patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice_terms", return_value=accounting):
            return frappe, prepare_visit_invoice(Gateway(frappe), "APT-1", None, "no_vat")

    def test_returns_same_checked_document_and_terms(self):
        frappe, result = self.prepare()
        visit, accounting, doc, totals = result
        self.assertIs(doc, frappe.document)
        self.assertEqual((visit, accounting), (VISIT, NO_VAT_TERMS))
        self.assertEqual((doc.debit_to, doc.selling_price_list), ("Receivable", "Standard"))
        self.assertEqual((doc.items[0].income_account, doc.items[0].cost_center,
                          doc.items[0].uom, doc.items[0].conversion_factor),
                         ("Income", "Center", "Visit", 1))
        self.assertEqual(totals["total"], "100.00")

    def test_unsupported_accounting_or_mismatched_terms_do_not_construct(self):
        for accounting in (None, dict(ACCOUNTING, unit_rate=Decimal("99")),
                           dict(ACCOUNTING, item="OTHER"), dict(ACCOUNTING, currency="EUR")):
            with self.subTest(accounting=accounting):
                frappe, result = self.prepare(accounting)
                self.assertIsNone(result)
                self.assertFalse(hasattr(frappe, "document"))

    def test_reject_precision_or_projected_account_drift(self):
        class DriftDocument(NativeShapeDocument):
            def precision(self, field):
                return 3
        class DriftFrappe(NativeShapeFrappe):
            def get_doc(self, values):
                self.document = DriftDocument(values, {})
                return self.document
        self.assertIsNone(self.prepare(frappe=DriftFrappe())[1])
        class AccountDocument(NativeShapeDocument):
            def calculate_taxes_and_totals(self):
                super().calculate_taxes_and_totals()
                self.items[0].income_account = "OTHER"
        class AccountFrappe(NativeShapeFrappe):
            def get_doc(self, values):
                self.document = AccountDocument(values, {})
                return self.document
        self.assertIsNone(self.prepare(frappe=AccountFrappe())[1])

        class ListDocument(NativeShapeDocument):
            def calculate_taxes_and_totals(self):
                super().calculate_taxes_and_totals()
                self.selling_price_list = "OTHER"
        class ListFrappe(NativeShapeFrappe):
            def get_doc(self, values):
                self.document = ListDocument(values, {})
                return self.document
        self.assertIsNone(self.prepare(frappe=ListFrappe())[1])

    def test_composes_real_visit_resolver_with_gateway_evidence(self):
        gateway = Gateway(NativeShapeFrappe())
        evidence = dict(VISIT, metadata_verified=True, record_fields_readable=True,
                        billing_history_complete=True, prior_billing=False,
                        actor_practitioner="DR-1", status="Open", docstatus=0,
                        invoiced=0, ref_sales_invoice=None)
        gateway.visit_invoice_evidence = lambda appointment_id: evidence
        with patch("clinic_ui.runtime.visit_invoice_preview.resolve_visit_invoice_terms",
                   return_value=NO_VAT_TERMS) as accounting_resolver:
            result = prepare_visit_invoice(gateway, "APT-1", None, "no_vat")
        self.assertIsNotNone(result)
        accounting_resolver.assert_called_once_with(gateway, VISIT, "no_vat")
        self.assertIsNone(prepare_visit_invoice(gateway, "OTHER", None, "no_vat"))

    def test_mode_argument_is_required(self):
        with self.assertRaises(TypeError):
            prepare_visit_invoice(Gateway(NativeShapeFrappe()), "APT-1", None)


def outcome(discount="0", total="100"):
    return dict(total=100, net_total=total, grand_total=total,
                base_total=100, base_net_total=total, base_grand_total=total,
                discount_amount=discount, total_taxes_and_charges=0,
                base_total_taxes_and_charges=0, base_discount_amount=discount,
                rounded_total=total, base_rounded_total=total,
                rounding_adjustment=0, base_rounding_adjustment=0,
                outstanding_amount=total)


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.terms = NO_VAT_TERMS

    def preview(self, discount, values):
        frappe = FakeFrappe(values)
        _, result = preview_visit_invoice(frappe, VISIT, discount, self.terms)
        return frappe.document, result

    def test_native_docstatus_subclass_and_bool_no_vat(self):
        class DocStatus(int):
            pass
        class StatusFrappe(NativeShapeFrappe):
            def get_doc(self, values):
                doc = super().get_doc(values)
                doc.docstatus = self.status
                return doc
        frappe = StatusFrappe()
        frappe.status = DocStatus(0)
        preview_visit_invoice(frappe, VISIT, None, NO_VAT_TERMS)
        frappe.status = False
        with self.assertRaises(ValueError):
            preview_visit_invoice(frappe, VISIT, None, NO_VAT_TERMS)

    def test_native_amount_percent_and_no_discount(self):
        for discount, values, expected in [
            (None, outcome(), ("100.00", "0.00", "100.00")),
            ({"mode": "amount", "value": Decimal("12.50")},
             outcome("12.50", "87.50"), ("100.00", "12.50", "87.50")),
            ({"mode": "percent", "value": Decimal("15")},
             outcome("15", "85"), ("100.00", "15.00", "85.00")),
        ]:
            with self.subTest(discount=discount):
                doc, result = self.preview(discount, values)
                self.assertEqual((result["gross"], result["discount"], result["total"]), expected)
                self.assertEqual(doc.items[0].reference_dn, "APT-1")
                if discount:
                    self.assertEqual(doc.apply_discount_on, "Net Total")

    def test_explicit_vat_native_outcomes(self):
        class VatDocument(NativeShapeDocument):
            def __init__(self, values, outcome):
                super().__init__(values, outcome)
                self.taxes = [type("Tax", (), {**row, "precision": lambda self, field: 2})()
                              for row in values["taxes"]]

            def calculate_taxes_and_totals(self):
                super().calculate_taxes_and_totals()
                self.net_total = self.base_net_total = round(self.net_total, 2)
                self.items[0].net_amount = self.items[0].base_net_amount = self.net_total
                tax = round(self.net_total * 0.07, 2) if self.taxes else 0
                self.total_taxes_and_charges = self.base_total_taxes_and_charges = tax
                self.grand_total = self.base_grand_total = round(self.net_total + tax, 2)
                self.rounded_total = self.base_rounded_total = self.grand_total
                if self.taxes:
                    row = self.taxes[0]
                    row.tax_amount = row.base_tax_amount = tax
                    row.tax_amount_after_discount_amount = row.base_tax_amount_after_discount_amount = tax
                    row.total = row.base_total = self.grand_total

        class VatFrappe:
            def __init__(self, status=0):
                self.status = status
            def get_doc(self, values):
                self.document = VatDocument(values, {})
                self.document.docstatus = self.status
                return self.document

        for terms, discount, expected in (
            (VAT_TERMS, None, ("100.00", "7.00", "107.00")),
            (VAT_TERMS, {"mode": "percent", "value": Decimal("15")}, ("85.00", "5.95", "90.95")),
            (VAT_TERMS, {"mode": "amount", "value": Decimal("10")}, ("90.00", "6.30", "96.30")),
            (NO_VAT_TERMS, {"mode": "percent", "value": Decimal("15")}, ("85.00", "0.00", "85.00")),
        ):
            with self.subTest(terms=terms, discount=discount):
                frappe = VatFrappe()
                doc, totals = preview_visit_invoice(frappe, VISIT, discount, terms)
                self.assertEqual((totals["net"], totals["vat"], totals["total"]), expected)
                self.assertEqual(doc.taxes_and_charges, terms["tax_template"])
                self.assertEqual(len(doc.taxes), len(terms["tax_rows"]))
                if doc.taxes:
                    self.assertEqual(doc.taxes[0].description, "VAT 7%")
        class DocStatus(int):
            pass
        for terms in (VAT_TERMS, NO_VAT_TERMS):
            with self.subTest(mode=terms["vat_mode"]):
                preview_visit_invoice(VatFrappe(DocStatus(0)), VISIT, None, terms)
                with self.assertRaises(ValueError):
                    preview_visit_invoice(VatFrappe(False), VISIT, None, terms)
        discount = {"mode": "amount", "value": Decimal("99.99")}
        with self.assertRaises(ValueError):
            preview_visit_invoice(VatFrappe(), VISIT, discount, VAT_TERMS)
        self.assertEqual(preview_visit_invoice(VatFrappe(), VISIT, discount, NO_VAT_TERMS)[1]["total"], "0.01")

    def test_reject_partial_and_invalid_tax_terms(self):
        missing = dict(NO_VAT_TERMS)
        del missing["tax_account"]
        for terms in (ACCOUNTING, dict(ACCOUNTING, tax_template="VAT 7"),
                      missing,
                      dict(NO_VAT_TERMS, tax_rows=(VAT_ROW,)),
                      dict(VAT_TERMS, tax_rows=(dict(VAT_ROW, rate=Decimal("6")),)),
                      dict(VAT_TERMS, tax_rows=(dict(VAT_ROW, included_in_print_rate=1),)),
                      dict(VAT_TERMS, tax_rows=(dict(VAT_ROW, description="  "),)),
                      dict(VAT_TERMS, tax_rows=(VAT_ROW, VAT_ROW)),
                      dict(VAT_TERMS, tax_account="Income")):
            with self.subTest(terms=terms), self.assertRaises(ValueError):
                preview_visit_invoice(FakeFrappe(outcome()), VISIT, None, terms)

    def test_reject_tax_drift_before_and_after_calculation(self):
        class TaxDocument(FakeDocument):
            def __init__(self, values, changes):
                super().__init__(values, outcome())
                row = type("Tax", (), {**values["taxes"][0],
                                       "precision": lambda self, field: 2})()
                self.taxes = [row]
                self.changes = changes
                if "pre" in changes:
                    setattr(row, *changes["pre"])
                if "pre_item" in changes:
                    self.items[0].item_tax_rate = changes["pre_item"]

            def calculate_taxes_and_totals(self):
                super().calculate_taxes_and_totals()
                self.grand_total = self.base_grand_total = 107
                self.rounded_total = self.base_rounded_total = 107
                self.outstanding_amount = 107
                self.total_taxes_and_charges = self.base_total_taxes_and_charges = 7
                row = self.taxes[0]
                row.tax_amount = row.base_tax_amount = 7
                row.tax_amount_after_discount_amount = row.base_tax_amount_after_discount_amount = 7
                row.total = row.base_total = 107
                if "post" in self.changes:
                    setattr(row, *self.changes["post"])
                if "header" in self.changes:
                    setattr(self, *self.changes["header"])
                if "post_item" in self.changes:
                    self.items[0].item_tax_rate = self.changes["post_item"]

        class TaxFrappe:
            def __init__(self, changes):
                self.changes = changes

            def get_doc(self, values):
                return TaxDocument(values, self.changes)

        preview_visit_invoice(TaxFrappe({}), VISIT, None, VAT_TERMS)
        preview_visit_invoice(TaxFrappe({"post_item": '{"VAT Payable": 7}'}), VISIT, None, VAT_TERMS)
        for value in ('{"VAT Payable": 6}', '{"Other": 7}', '{"VAT Payable": true}',
                      '{"VAT Payable": 7, "Other": 7}', '{"VAT Payable": 7, "VAT Payable": 7}',
                      '{"VAT Payable": "7"}', '{bad}', [], {"VAT Payable": 7}):
            with self.subTest(item_tax_rate=value), self.assertRaises(ValueError):
                preview_visit_invoice(TaxFrappe({"post_item": value}), VISIT, None, VAT_TERMS)
        with self.assertRaises(ValueError):
            preview_visit_invoice(TaxFrappe({"pre_item": '{"VAT Payable": 7}'}), VISIT, None, VAT_TERMS)
        for changes in (
            {"pre": ("rate", 6)}, {"pre": ("included_in_print_rate", 1)},
            {"pre": ("description", "changed")}, {"post": ("description", "changed")},
            {"post": ("account_head", "OTHER")}, {"post": ("rate", 6)},
            {"post": ("charge_type", "Actual")}, {"post": ("included_in_print_rate", 1)},
            {"post": ("tax_amount", 6)}, {"post": ("base_tax_amount_after_discount_amount", 6)},
            {"post": ("total", 106)}, {"header": ("taxes_and_charges", "OTHER")},
            {"header": ("base_grand_total", 106)}, {"header": ("rounding_adjustment", 0.01)},
            {"post": ("precision", lambda field: 3)},
            {"header": ("docstatus", 1)},
            {"header": ("doctype", "OTHER")},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                preview_visit_invoice(TaxFrappe(changes), VISIT, None, VAT_TERMS)

    def test_native_float_operand_shapes(self):
        for discount, expected in (
            (None, "100.00"),
            ({"mode": "amount", "value": Decimal("12.50")}, "87.50"),
            ({"mode": "percent", "value": Decimal("15")}, "85.00"),
        ):
            with self.subTest(discount=discount):
                frappe = NativeShapeFrappe()
                _, result = preview_visit_invoice(frappe, VISIT, discount, self.terms)
                self.assertEqual(result["total"], expected)
                doc = frappe.document
                for value in (doc.items[0].rate, doc.items[0].price_list_rate,
                              doc.items[0].qty, doc.conversion_rate,
                              doc.additional_discount_percentage, doc.discount_amount):
                    self.assertIs(type(value), float)

    def test_unset_outstanding_and_automatic_posting_time(self):
        for discount, expected in (
            (None, "100.00"),
            ({"mode": "amount", "value": Decimal("12.50")}, "87.50"),
            ({"mode": "percent", "value": Decimal("15")}, "85.00"),
        ):
            with self.subTest(discount=discount):
                frappe = NativeShapeFrappe()
                doc, totals = preview_visit_invoice(frappe, VISIT, discount, self.terms)
                self.assertIsNone(doc.outstanding_amount)
                self.assertEqual(doc.set_posting_time, 0)
                self.assertEqual(totals["total"], expected)

    def test_reject_native_drift_and_zero(self):
        discount = {"mode": "percent", "value": Decimal("15")}
        for values in (outcome("0", "100"), outcome("100", "0"),
                       outcome("15", "84"), dict(outcome(), total=101),
                       dict(outcome(), total_taxes_and_charges=1)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.preview(discount, values)

    def test_reject_unsupported_settings_and_currency(self):
        for terms in (dict(NO_VAT_TERMS, company_currency="EUR"),
                      dict(NO_VAT_TERMS, party_account_currency="EUR"),
                      dict(NO_VAT_TERMS, posting_date=None)):
            with self.subTest(terms=terms):
                with self.assertRaises(ValueError):
                    preview_visit_invoice(FakeFrappe(outcome()), VISIT, None, terms)

    def test_reject_shifted_percent_and_pricing(self):
        discount = {"mode": "percent", "value": Decimal("15")}
        for values in (dict(outcome("15", "85"), additional_discount_percentage=12),
                       dict(outcome("15", "85"), row_changes={"pricing_rules": "RULE"}),
                       outcome("14", "86")):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    self.preview(discount, values)

    def test_reject_changed_visit_reference_and_rate(self):
        for changes in ({"reference_dn": "OTHER"}, {"rate": 99}, {"item_tax_rate": '{"VAT Payable": 0}'}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    self.preview(None, dict(outcome(), row_changes=changes))
        self.preview(None, dict(outcome(), row_changes={"item_tax_rate": "{}"}))

    def test_reject_payable_row_and_adjustment_drift(self):
        discount = {"mode": "amount", "value": Decimal("12.50")}
        for changes in (
            {"rounded_total": 88, "base_rounded_total": 88, "outstanding_amount": 88},
            {"outstanding_amount": 88},
            {"rounding_adjustment": 0.50},
            {"row_changes": {"net_amount": 88}},
            {"row_changes": {"distributed_discount_amount": 11}},
            {"row_changes": {"insurance_coverage": 10}},
            {"total_advance": 1},
            {"set_posting_time": 1},
            {"is_return": 1},
            {"allocate_advances_automatically": 1},
            {"write_off_outstanding_amount_automatically": 1},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.preview(discount, dict(outcome("12.50", "87.50"), **changes))


if __name__ == "__main__":
    unittest.main()
