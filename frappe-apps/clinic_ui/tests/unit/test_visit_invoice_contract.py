import unittest
from decimal import Decimal

from clinic_ui.core.visit_invoice_contract import parse_discount


class VisitInvoiceDiscountTests(unittest.TestCase):
    def test_no_discount_and_valid_modes(self):
        self.assertIsNone(parse_discount({}))
        self.assertEqual(parse_discount({"discount": {"mode": "amount", "value": "12.50"}}),
                         {"mode": "amount", "value": Decimal("12.50")})
        self.assertEqual(parse_discount({"discount": {"mode": "percent", "value": "15"}}),
                         {"mode": "percent", "value": Decimal("15")})

    def test_invalid_discount_rejected(self):
        for discount in (
            {"mode": "amount", "value": "NaN"}, {"mode": "amount", "value": "Infinity"},
            {"mode": "amount", "value": "0.001"}, {"mode": "amount", "value": "-1"},
            {"mode": "percent", "value": "100.01"}, {"mode": "percent", "value": "0"},
            {"mode": "amount", "value": "1", "percent": "10"},
            {"mode": "amount", "value": 1}, {"mode": "other", "value": "1"},
        ):
            with self.subTest(discount=discount), self.assertRaises(ValueError):
                parse_discount({"discount": discount})

    def test_rejects_non_fixed_point_and_oversized_values(self):
        for value in ("1e2", "1_000", " 1", "1 ", "1e999999999",
                      "1000001", "12345678", "١", "１"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_discount({"discount": {"mode": "amount", "value": value}})

    def test_existing_monetary_limit_is_inclusive(self):
        self.assertEqual(parse_discount({"discount": {"mode": "amount", "value": "1000000.00"}}),
                         {"mode": "amount", "value": Decimal("1000000.00")})
