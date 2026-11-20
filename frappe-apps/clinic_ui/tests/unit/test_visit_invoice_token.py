import copy
import unittest

from clinic_ui.core.visit_invoice_token import issue, verify


VAT_ROW = {"charge_type": "On Net Total", "account_head": "VAT-LIAB",
           "rate": "7", "included_in_print_rate": 0, "description": " VAT 7% "}


class VisitInvoiceTokenTest(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.claims = {
            "actor": "doctor@example.org",
            "session": "secret-session-id",
            "date": "2026-11-19",
            "appointment": "APT-1",
            "encounter": None,
            "discount": {"mode": "amount", "value": "12.50"},
            "vat": {"mode": "vat_7", "template": "VAT 7", "account": "VAT-LIAB",
                    "rate": "7", "rows": [dict(VAT_ROW)]},
            "terms": {"patient": "P-1", "customer": "C-1", "practitioner": "DR-1",
                      "company": "CO-1", "service": "S-1", "rate": "100.00", "account": "A"},
            "totals": {"currency": "USD", "gross": "100.00", "discount": "12.50",
                       "net": "87.50", "vat": "6.13", "total": "93.63"},
        }

    def test_roundtrip_and_opaque_wire(self):
        token = issue(self.key, self.claims, now=1000)
        self.assertRegex(token, r"^v2\.1300\.[0-9a-f]{64}$")
        self.assertNotIn("secret-session-id", token)
        self.assertTrue(verify(self.key, self.claims, token, now=1000))
        self.assertTrue(verify(self.key, self.claims, token, now=1299))
        self.assertFalse(verify(self.key, self.claims, token, now=1300))
        self.assertFalse(verify(self.key, self.claims, token, now=999))

    def test_claim_tampering(self):
        token = issue(self.key, self.claims, now=1000)
        for key, value in (("actor", "other"), ("session", "other"), ("date", "2026-11-20"),
                           ("appointment", "APT-2"), ("encounter", "ENC-1")):
            changed = copy.deepcopy(self.claims)
            changed[key] = value
            self.assertFalse(verify(self.key, changed, token, now=1000), key)
        for section, key, value in (("terms", "rate", "99.00"), ("terms", "account", "B"),
                                    ("discount", "value", "10.00"), ("totals", "total", "93.64")):
            changed = copy.deepcopy(self.claims)
            changed[section][key] = value
            self.assertFalse(verify(self.key, changed, token, now=1000), key)
        self.assertFalse(verify(b"x" * 32, self.claims, token, now=1000))

    def test_vat_mode_and_tax_facts_are_token_bound(self):
        token = issue(self.key, self.claims, now=1000)
        no_vat = copy.deepcopy(self.claims)
        no_vat["vat"] = {"mode": "no_vat", "template": None, "account": None,
                         "rate": None, "rows": []}
        no_vat["totals"] = dict(self.claims["totals"], vat="0.00", total="87.50")
        self.assertFalse(verify(self.key, no_vat, token, now=1000))
        for section, key, value in (
            ("vat", "template", "VAT 6"),
            ("vat", "account", "OTHER-LIAB"),
            ("vat", "rate", "6"),
            ("totals", "net", "87.51"),
            ("totals", "vat", "6.12"),
        ):
            changed = copy.deepcopy(self.claims)
            changed[section][key] = value
            self.assertFalse(verify(self.key, changed, token, now=1000), key)
        changed = copy.deepcopy(self.claims)
        changed["vat"]["rows"][0]["account_head"] = "OTHER-LIAB"
        self.assertFalse(verify(self.key, changed, token, now=1000))
        changed = copy.deepcopy(self.claims)
        changed["vat"]["rows"][0]["included_in_print_rate"] = 1
        self.assertFalse(verify(self.key, changed, token, now=1000))
        changed = copy.deepcopy(self.claims)
        changed["vat"]["rows"][0]["description"] = "Other description"
        self.assertFalse(verify(self.key, changed, token, now=1000))

    def test_tax_description_required_nonblank_and_bounded(self):
        for description in (None, "", "   ", "x" * 513):
            changed = copy.deepcopy(self.claims)
            if description is None:
                del changed["vat"]["rows"][0]["description"]
            else:
                changed["vat"]["rows"][0]["description"] = description
            with self.subTest(description=description), self.assertRaises(ValueError):
                issue(self.key, changed, now=1000)

    def test_pre_vat_tokens_and_claims_are_rejected(self):
        old = copy.deepcopy(self.claims)
        del old["vat"]
        old["totals"] = {"currency": "USD", "gross": "100.00", "discount": "12.50",
                         "total": "87.50"}
        with self.assertRaises(ValueError):
            issue(self.key, old, now=1000)
        self.assertFalse(verify(self.key, old, "v2.1300." + "0" * 64, now=1000))
        token = issue(self.key, self.claims, now=1000)
        for legacy in ("v1.1300." + "0" * 64, token.replace("v2.", "v1.")):
            self.assertFalse(verify(self.key, self.claims, legacy, now=1000), legacy)

    def test_no_vat_requires_zero_tax_and_no_rows(self):
        claims = copy.deepcopy(self.claims)
        claims["vat"] = {"mode": "no_vat", "template": None, "account": None,
                         "rate": None, "rows": []}
        claims["totals"] = dict(claims["totals"], vat="0.00", total="87.50")
        self.assertTrue(verify(self.key, claims, issue(self.key, claims, now=1000), now=1000))
        for vat in ({"mode": "no_vat", "template": None, "account": None, "rate": None,
                     "rows": [dict(VAT_ROW)]},
                    {"mode": "no_vat", "template": "VAT 7", "account": None,
                     "rate": None, "rows": []},
                    {"mode": "no_vat", "template": None, "account": "VAT-LIAB",
                     "rate": None, "rows": []},
                    {"mode": "no_vat", "template": None, "account": None,
                     "rate": "7", "rows": []}):
            changed = copy.deepcopy(claims)
            changed["vat"] = vat
            with self.subTest(vat=vat), self.assertRaises(ValueError):
                issue(self.key, changed, now=1000)
        taxed = copy.deepcopy(claims)
        taxed["totals"] = dict(claims["totals"], vat="6.13", total="93.63")
        with self.assertRaises(ValueError):
            issue(self.key, taxed, now=1000)

    def test_invalid_vat_and_totals_shapes(self):
        invalid = []
        for field in ("mode", "template", "account", "rate", "rows"):
            changed = copy.deepcopy(self.claims)
            del changed["vat"][field]
            invalid.append(changed)
        for field in ("currency", "gross", "discount", "net", "vat", "total"):
            changed = copy.deepcopy(self.claims)
            del changed["totals"][field]
            invalid.append(changed)
        for section, key, value in (
            ("vat", "mode", "vat_6"),
            ("vat", "rows", []),
            ("vat", "rows", [dict(VAT_ROW), dict(VAT_ROW)]),
            ("vat", "rate", "1e1"),
            ("vat", "rate", "0"),
            ("vat", "account", ""),
            ("totals", "vat", "06.13"),
            ("totals", "total", "93.64"),
        ):
            changed = copy.deepcopy(self.claims)
            changed[section][key] = value
            invalid.append(changed)
        for claims in invalid:
            with self.subTest(claims=claims["vat"]), self.assertRaises(ValueError):
                issue(self.key, claims, now=1000)

    def test_invalid_wire_and_clock(self):
        token = issue(self.key, self.claims, now=1000)
        for bad in (token.upper(), token + ".", token.replace(".1300.", ".01300."),
                    token.replace("v2.", "v3."), "v2.1301." + "0" * 64, "v2.-1." + "0" * 64):
            self.assertFalse(verify(self.key, self.claims, bad, now=1000), bad)
        for now in (True, 1.0, -1):
            with self.assertRaises(ValueError):
                issue(self.key, self.claims, now=now)

    def test_invalid_claims_and_key_fail_closed(self):
        token = issue(self.key, self.claims, now=1000)
        for key in (b"", b"x" * 31, None, "x" * 32):
            with self.assertRaises(ValueError):
                issue(key, self.claims, now=1000)
            self.assertFalse(verify(key, self.claims, token, now=1000))
        invalid = []
        for field in self.claims:
            value = copy.deepcopy(self.claims)
            del value[field]
            invalid.append(value)
        for field in ("patient", "customer", "practitioner", "company", "service", "rate"):
            value = copy.deepcopy(self.claims)
            del value["terms"][field]
            invalid.append(value)
        for section, key, value in (("terms", "rate", float("nan")), ("totals", "total", "087.50"),
                                    ("discount", "value", "1e2"), ("terms", "rate", 1.5)):
            changed = copy.deepcopy(self.claims)
            changed[section][key] = value
            invalid.append(changed)
        for claims in invalid:
            with self.assertRaises(ValueError):
                issue(self.key, claims, now=1000)
            self.assertFalse(verify(self.key, claims, token, now=1000))

    def test_percent_mode_matches_native_preview(self):
        self.claims["discount"] = {"mode": "percent", "value": "15"}
        self.claims["totals"] = {"currency": "USD", "gross": "100.00", "discount": "15.00",
                                 "net": "85.00", "vat": "5.95", "total": "90.95"}
        token = issue(self.key, self.claims, now=1000)
        self.assertTrue(verify(self.key, self.claims, token, now=1000))


if __name__ == "__main__":
    unittest.main()
