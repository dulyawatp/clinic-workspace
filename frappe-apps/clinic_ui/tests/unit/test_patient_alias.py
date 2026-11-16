"""Tests for the opaque, server-derived local Patient alias."""

import unittest

from clinic_ui.core import patient_alias

SECRET = b"0123456789abcdef0123456789abcdef"
OTHER = b"fedcba9876543210fedcba9876543210"
SITE = "marley-sit.local"
ACTOR = "sit.doc@example.invalid"
KEY = "op-0123456789abcdef"


class AliasTests(unittest.TestCase):
    def test_alias_is_stable_and_contains_no_native_id(self):
        first = patient_alias.build_alias(SECRET, SITE, ACTOR, KEY)
        second = patient_alias.build_alias(SECRET, SITE, ACTOR, KEY)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("wpa1_"))
        self.assertEqual(len(first), 5 + 32 + 1 + 16 + 1 + len(KEY))
        self.assertNotIn("PAT-", first)
        self.assertEqual(
            patient_alias.parse_alias(SECRET, SITE, ACTOR, first), KEY
        )

    def test_wrong_actor_site_or_secret_is_refused(self):
        alias = patient_alias.build_alias(SECRET, SITE, ACTOR, KEY)
        self.assertIsNone(
            patient_alias.parse_alias(SECRET, SITE, "other@example.test", alias)
        )
        self.assertIsNone(
            patient_alias.parse_alias(SECRET, "other.local", ACTOR, alias)
        )
        self.assertIsNone(
            patient_alias.parse_alias(OTHER, SITE, ACTOR, alias)
        )

    def test_malformed_or_tampered_alias_is_refused(self):
        alias = patient_alias.build_alias(SECRET, SITE, ACTOR, KEY)
        for value in (
            None,
            5,
            "",
            "PAT-1",
            "wpa1_",
            alias[:-1],
            alias.replace("wpa1_", "wpa2_"),
            alias + "x",
            "wpa1_" + "0" * 32 + "_" + "0" * 16 + "_" + KEY,
        ):
            with self.subTest(value=value):
                self.assertIsNone(
                    patient_alias.parse_alias(SECRET, SITE, ACTOR, value)
                )

    def test_invalid_operation_key_is_refused(self):
        for key in (None, "", "short", "x" * 129, "has space 1234567890", 5):
            with self.subTest(key=key):
                with self.assertRaises(patient_alias.AliasError):
                    patient_alias.build_alias(SECRET, SITE, ACTOR, key)

    def test_invalid_context_is_refused(self):
        for secret, site, actor in (
            ("short", SITE, ACTOR),
            (SECRET, "", ACTOR),
            (SECRET, SITE, ""),
            (SECRET, SITE, "bad\x00actor"),
        ):
            with self.subTest(site=site, actor=actor):
                with self.assertRaises(patient_alias.AliasError):
                    patient_alias.build_alias(secret, site, actor, KEY)


if __name__ == "__main__":
    unittest.main()
