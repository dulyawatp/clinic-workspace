import copy
import unittest

from clinic_ui.core.visit_invoice_token import issue, verify


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
            "terms": {"patient": "P-1", "customer": "C-1", "practitioner": "DR-1",
                      "company": "CO-1", "service": "S-1", "rate": "100.00", "account": "A"},
            "totals": {"currency": "USD", "gross": "100.00", "discount": "12.50", "total": "87.50"},
        }

    def test_roundtrip_and_opaque_wire(self):
        token = issue(self.key, self.claims, now=1000)
        self.assertRegex(token, r"^v1\.1300\.[0-9a-f]{64}$")
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
                                    ("discount", "value", "10.00"), ("totals", "total", "90.00")):
            changed = copy.deepcopy(self.claims)
            changed[section][key] = value
            self.assertFalse(verify(self.key, changed, token, now=1000), key)
        self.assertFalse(verify(b"x" * 32, self.claims, token, now=1000))

    def test_invalid_wire_and_clock(self):
        token = issue(self.key, self.claims, now=1000)
        for bad in (token.upper(), token + ".", token.replace(".1300.", ".01300."),
                    token.replace("v1.", "v2."), "v1.1301." + "0" * 64, "v1.-1." + "0" * 64):
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
        token = issue(self.key, self.claims, now=1000)
        self.assertTrue(verify(self.key, self.claims, token, now=1000))
