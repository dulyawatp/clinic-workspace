import unittest
import base64
from datetime import datetime, timezone

from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from clinic_ui.core import production_patient_ref as refs


class ProductionPatientRefTests(unittest.TestCase):
    def setUp(self):
        self.key = bytes(range(64))
        self.site = "clinic.example"
        self.creation = datetime(2026, 11, 22, 13, 14, 15, 123456)

    def test_stable_opaque_round_trip(self):
        name = "patient-secret-marker-123"
        ref = refs.encode(self.key, self.site, name, self.creation)
        self.assertEqual(ref, refs.encode(self.key, self.site, name, self.creation))
        self.assertNotIn(name, ref)
        self.assertEqual(refs.decode(self.key, self.site, ref), (name, self.creation))
        self.assertNotEqual(ref, refs.encode(self.key, self.site, name + "x", self.creation))
        other_creation = self.creation.replace(microsecond=123457)
        other = refs.encode(self.key, self.site, name, other_creation)
        self.assertNotEqual(ref, other)
        self.assertEqual(refs.decode(self.key, self.site, other), (name, other_creation))

    def test_unicode_maximum_and_cap(self):
        name = "😀" * 140
        ref = refs.encode(self.key, self.site, name, self.creation)
        self.assertEqual(len(ref), refs.REF_MAX_LENGTH)
        self.assertEqual(refs.decode(self.key, self.site, ref), (name, self.creation))
        with self.assertRaises(ValueError):
            refs.encode(self.key, self.site, name + "😀", self.creation)

    def test_rejects_invalid_refs_uniformly(self):
        ref = refs.encode(self.key, self.site, "patient-123", self.creation)
        altered = ref[:-1] + ("A" if ref[-1] != "A" else "B")
        short_ref = refs.encode(self.key, self.site, "x", self.creation)
        alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        noncanonical = short_ref[:-1] + alphabet[alphabet.index(short_ref[-1]) ^ 1]
        for value in (
            "patient-123", "", ref + "=", ref + "/", ref + "A",
            ref + "x" * refs.REF_MAX_LENGTH, altered, "cpr2_" + ref[5:],
            "../" + ref, ref + "\n", noncanonical, None,
        ):
            with self.subTest(value=value):
                self.assertIsNone(refs.decode(self.key, self.site, value))
        self.assertIsNone(refs.decode(self.key, "other.example", ref))
        self.assertIsNone(refs.decode(bytes(reversed(self.key)), self.site, ref))
        self.assertIsNone(refs.decode(self.key, self.site + "/", ref))

    def test_rejects_invalid_inputs_without_echoing_them(self):
        for name in ("", " ", "a\nb", "\ud800", "x" * 141):
            with self.subTest(name=repr(name)):
                with self.assertRaises(ValueError) as error:
                    refs.encode(self.key, self.site, name, self.creation)
                self.assertEqual(str(error.exception), "invalid patient reference input")
        for key, site in ((b"short", self.site), (self.key, ""), (self.key, "\ud800")):
            with self.subTest(site=repr(site)):
                with self.assertRaises(ValueError):
                    refs.encode(key, site, "patient", self.creation)
                with self.assertRaises(ValueError):
                    refs.decode(key, site, "invalid")

    def test_creation_requires_naive_datetime(self):
        for creation in (None, "2026-11-22T13:14:15.123456", self.creation.replace(tzinfo=timezone.utc)):
            with self.subTest(creation=creation):
                with self.assertRaisesRegex(ValueError, "^invalid patient reference input$"):
                    refs.encode(self.key, self.site, "patient", creation)
        creation = datetime(1, 1, 1)
        ref = refs.encode(self.key, self.site, "patient", creation)
        self.assertEqual(refs.decode(self.key, self.site, ref), ("patient", creation))

    def test_rejects_authenticated_bad_generation_and_wrong_purpose(self):
        cipher = AESSIV(self.key)
        for plain, purpose in (
            (b"2026-02-30T13:14:15.123456patient", b"clinic_ui.production_patient_ref.cpr1"),
            (b"2026-11-22 13:14:15.123456patient", b"clinic_ui.production_patient_ref.cpr1"),
            (b"2026-11-22T13:14:15.123456patient", b"another-purpose"),
        ):
            ciphertext = cipher.encrypt(plain, [purpose, self.site.encode()])
            ref = refs.PREFIX + base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode()
            self.assertIsNone(refs.decode(self.key, self.site, ref))


if __name__ == "__main__":
    unittest.main()
