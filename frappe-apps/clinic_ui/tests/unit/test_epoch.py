"""Pure tests for the opaque session epoch derivation.

The epoch is an HMAC over server identity, never a credential and never
reversible. Tests prove determinism, separation and fail-closed inputs.
"""

import unittest

from clinic_ui.core.epoch import session_epoch

SECRET = b"0123456789abcdef0123456789abcdef"
OTHER_SECRET = b"fedcba9876543210fedcba9876543210"


class SessionEpochTests(unittest.TestCase):
    def test_epoch_is_deterministic_per_site_and_session(self):
        first = session_epoch(SECRET, "marley.test", "sid-abc123")
        second = session_epoch(SECRET, "marley.test", "sid-abc123")
        self.assertEqual(first, second)

    def test_epoch_differs_per_session_site_and_secret(self):
        base = session_epoch(SECRET, "marley.test", "sid-abc123")
        self.assertNotEqual(base, session_epoch(SECRET, "marley.test", "sid-def456"))
        self.assertNotEqual(base, session_epoch(SECRET, "other.test", "sid-abc123"))
        self.assertNotEqual(base, session_epoch(OTHER_SECRET, "marley.test", "sid-abc123"))

    def test_epoch_is_hex_and_fits_the_client_dto_limit(self):
        epoch = session_epoch(SECRET, "marley.test", "sid-abc123")
        self.assertEqual(len(epoch), 64)
        self.assertLessEqual(len(epoch), 128)
        int(epoch, 16)

    def test_epoch_does_not_embed_the_raw_session_id(self):
        epoch = session_epoch(SECRET, "marley.test", "sid-abc123")
        self.assertNotIn("sid-abc123", epoch)
        self.assertNotIn("abc123", epoch)

    def test_absent_inputs_fail_closed(self):
        for secret, site, sid in (
            (b"", "marley.test", "sid-abc123"),
            (SECRET, "", "sid-abc123"),
            (SECRET, "marley.test", ""),
            (None, "marley.test", "sid-abc123"),
            (SECRET, None, "sid-abc123"),
            (SECRET, "marley.test", None),
            ("not-bytes", "marley.test", "sid-abc123"),
        ):
            with self.subTest(secret=secret, site=site, sid=sid):
                with self.assertRaises(ValueError):
                    session_epoch(secret, site, sid)


if __name__ == "__main__":
    unittest.main()
