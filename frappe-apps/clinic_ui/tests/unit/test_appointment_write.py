"""Unit tests for the appointment write codecs.

Everything here is framework-independent: no site, Docker, MariaDB or native
record is touched. The two properties under test are namespace separation from
the patient codecs and the binding of every acknowledgement to actor, site,
kind, operation key and the durable receipt.
"""

import unittest

from clinic_ui.core import appointment_write, patient_alias, patient_write
from clinic_ui.core.appointment_write import (
    AppointmentWriteError,
    ack_token,
    build_alias,
    decode_receipt,
    encode_receipt,
    operation_digest,
    parse_alias,
    validate_ack,
)

SECRET = b"appointment-secret-that-is-at-least-32-bytes"
SITE = "marley-sit.local"
ACTOR = "doc@example.test"
KEY = "appt-op-key-0001-0123456789"
NATIVE_ID = "APPT-0001"


class KindTests(unittest.TestCase):
    def test_accepts_only_the_three_appointment_kinds(self):
        for kind in ("book", "reschedule", "checkin"):
            with self.subTest(kind=kind):
                self.assertEqual(appointment_write.require_kind(kind), kind)
        for kind in ("create", "update", "cancel", "Book", "", None, 1):
            with self.subTest(kind=kind):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.require_kind(kind)

    def test_appointment_kinds_do_not_overlap_patient_kinds(self):
        self.assertEqual(
            appointment_write.APPOINTMENT_KINDS,
            frozenset({"book", "reschedule", "checkin"}),
        )
        self.assertEqual(
            set(appointment_write.APPOINTMENT_KINDS) & patient_write.RECEIPT_KINDS,
            set(),
        )


class OperationKeyTests(unittest.TestCase):
    def test_accepts_a_strict_operation_key(self):
        self.assertEqual(appointment_write.require_operation_key(KEY), KEY)

    def test_rejects_short_long_and_non_ascii_keys(self):
        for key in (
            "short",
            "k" * 129,
            "ключ-0123456789",
            "",
            None,
            "k" * 20 + "\n",
        ):
            with self.subTest(key=key):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.require_operation_key(key)

    def test_rejects_reserved_fence_and_alias_namespaces(self):
        for key in (
            "clinic-ui-manual-investigation-latch",
            "wpa1_0123456789abcdef0123456789abcdef_0123456789abcdef_appt-op-key-000",
            "wsa1_0123456789abcdef0123456789abcdef_0123456789abcdef_appt-op-key-000",
        ):
            with self.subTest(key=key[:16]):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.require_operation_key(key)


class AliasTests(unittest.TestCase):
    def test_alias_is_deterministic_and_round_trips(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        self.assertEqual(alias, build_alias(SECRET, SITE, ACTOR, KEY))
        self.assertTrue(alias.startswith(appointment_write.ALIAS_PREFIX))
        self.assertNotIn(NATIVE_ID, alias)
        self.assertEqual(parse_alias(SECRET, SITE, ACTOR, alias), KEY)

    def test_alias_namespace_is_distinct_from_the_patient_alias(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        self.assertFalse(alias.startswith(patient_alias.PREFIX))
        self.assertIsNone(patient_alias.alias_operation_key(alias))
        self.assertIsNone(
            parse_alias(
                SECRET,
                SITE,
                ACTOR,
                "wpa1_0123456789abcdef0123456789abcdef_0123456789abcdef_" + KEY,
            )
        )

    def test_alias_is_bound_to_site_and_actor(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        self.assertIsNone(parse_alias(SECRET, "other-sit.local", ACTOR, alias))
        self.assertIsNone(parse_alias(SECRET, SITE, "other@example.test", alias))
        self.assertIsNone(parse_alias(b"x" * 32, SITE, ACTOR, alias))

    def test_malformed_alias_is_refused(self):
        padded = build_alias(SECRET, SITE, ACTOR, KEY) + "x"
        for alias in (None, "", NATIVE_ID, "wsa1_" + "0" * 32, padded):
            with self.subTest(alias=str(alias)[:16]):
                self.assertIsNone(parse_alias(SECRET, SITE, ACTOR, alias))

    def test_invalid_alias_inputs_are_refused(self):
        for secret, site, actor, key in (
            (b"short", SITE, ACTOR, KEY),
            (SECRET, "", ACTOR, KEY),
            (SECRET, "   ", ACTOR, KEY),
            (SECRET, SITE, "doc\x00example.test", KEY),
            (SECRET, SITE, "a" * 141, KEY),
            (SECRET, SITE, ACTOR, "short"),
        ):
            with self.subTest(site=str(site)[:8], actor=str(actor)[:8]):
                with self.assertRaises(AppointmentWriteError):
                    build_alias(secret, site, actor, key)

    def test_delimiter_inside_site_or_actor_cannot_collide(self):
        first = ("marley|sit.local", "doc@example.test")
        second = ("marley", "sit.local|doc@example.test")
        first_alias = build_alias(SECRET, *first, KEY)
        second_alias = build_alias(SECRET, *second, KEY)
        self.assertNotEqual(
            appointment_write.actor_tag(SECRET, *first),
            appointment_write.actor_tag(SECRET, *second),
        )
        self.assertNotEqual(first_alias, second_alias)
        self.assertIsNone(parse_alias(SECRET, *second, first_alias))
        self.assertIsNone(parse_alias(SECRET, *first, second_alias))


class ReceiptCodecTests(unittest.TestCase):
    def test_round_trips_each_appointment_kind(self):
        for kind in ("book", "reschedule", "checkin"):
            with self.subTest(kind=kind):
                alias = build_alias(SECRET, SITE, ACTOR, KEY)
                receipt = encode_receipt(
                    kind=kind, alias=alias, native_appointment_id=NATIVE_ID
                )
                decoded = decode_receipt(receipt)
                self.assertEqual(
                    (decoded.kind, decoded.alias, decoded.native_appointment_id),
                    (kind, alias, NATIVE_ID),
                )

    def test_patient_codec_cannot_read_an_appointment_receipt(self):
        receipt = encode_receipt(
            kind="book", alias=build_alias(SECRET, SITE, ACTOR, KEY),
            native_appointment_id=NATIVE_ID,
        )
        with self.assertRaises(patient_write.WriteInputError):
            patient_write.decode_create_receipt(receipt)

    def test_appointment_codec_cannot_read_a_patient_receipt(self):
        receipt = patient_write.encode_receipt(
            kind="create",
            alias="wpa1_0123456789abcdef0123456789abcdef_0123456789abcdef_" + KEY,
            native_patient_id="PAT-0001",
        )
        with self.assertRaises(AppointmentWriteError):
            decode_receipt(receipt)

    def test_unknown_kind_is_refused_by_both_codecs(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        for kind in ("create", "cancel", "", None):
            with self.subTest(kind=kind):
                with self.assertRaises(AppointmentWriteError):
                    encode_receipt(
                        kind=kind, alias=alias, native_appointment_id=NATIVE_ID
                    )

    def test_tampered_receipts_are_refused(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        receipt = encode_receipt(
            kind="checkin", alias=alias, native_appointment_id=NATIVE_ID
        )
        for bad in (
            None,
            "",
            "not json",
            receipt + "trailing",
            receipt.replace('"alias"', '"native_patient_id"', 1),
            receipt.replace('"appointment"', '"patient"'),
            receipt.replace('"version":1', '"version":true'),
        ):
            with self.subTest(receipt=str(bad)[:24]):
                with self.assertRaises(AppointmentWriteError):
                    decode_receipt(bad)

    def test_receipt_is_bounded_and_never_carries_a_control_character(self):
        alias = build_alias(SECRET, SITE, ACTOR, KEY)
        for native_id in ("", "x" * 141, "APPT\n0001", None):
            with self.subTest(native_id=str(native_id)[:12]):
                with self.assertRaises(AppointmentWriteError):
                    encode_receipt(
                        kind="book", alias=alias, native_appointment_id=native_id
                    )
        for bad_alias in ("", "x" * 193, "wsa1_\t", None):
            with self.subTest(alias=str(bad_alias)[:12]):
                with self.assertRaises(AppointmentWriteError):
                    encode_receipt(
                        kind="book", alias=bad_alias, native_appointment_id=NATIVE_ID
                    )


class AckTokenTests(unittest.TestCase):
    def token(self, **override):
        arguments = dict(
            site=SITE,
            actor=ACTOR,
            kind="book",
            operation_key=KEY,
            receipt="appointment-receipt",
        )
        arguments.update(override)
        return ack_token(SECRET, **arguments)

    def test_token_is_deterministic_and_hex(self):
        self.assertEqual(self.token(), self.token())
        self.assertRegex(self.token(), r"^[0-9a-f]{64}$")
        self.assertNotIn("appointment-receipt", self.token())

    def test_token_is_bound_to_every_identity_field(self):
        reference = self.token()
        for override in (
            {"site": "other-sit.local"},
            {"actor": "other@example.test"},
            {"kind": "reschedule"},
            {"kind": "checkin"},
            {"operation_key": "appt-op-key-0002-0123456789"},
            {"receipt": "other-receipt"},
        ):
            with self.subTest(override=override):
                self.assertNotEqual(self.token(**override), reference)

    def test_delimiter_inside_site_or_actor_cannot_collide(self):
        first = self.token(site="marley|sit.local", actor="doc@example.test")
        second = self.token(site="marley", actor="sit.local|doc@example.test")
        self.assertNotEqual(first, second)
        # Same split, but the actor carries the delimiter in the other position.
        third = self.token(site="marley|sit.local|x", actor="y")
        fourth = self.token(site="marley", actor="sit.local|x|y")
        self.assertNotEqual(third, fourth)

    def test_token_rejects_patient_kinds_and_malformed_input(self):
        for override in (
            {"kind": "create"},
            {"kind": "update"},
            {"operation_key": "short"},
            {"receipt": ""},
            {"site": ""},
        ):
            with self.subTest(override=override):
                with self.assertRaises(AppointmentWriteError):
                    self.token(**override)
        with self.assertRaises(AppointmentWriteError):
            ack_token(b"short", SITE, ACTOR, "book", KEY, "appointment-receipt")

    def test_patient_ack_token_rejects_appointment_kinds(self):
        for kind in ("book", "reschedule", "checkin"):
            with self.subTest(kind=kind):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.ack_token(
                        SECRET, SITE, ACTOR, kind, KEY, "appointment-receipt"
                    )

    def test_validate_ack_accepts_one_strict_body(self):
        body = {"operation_key": KEY, "kind": "checkin", "ack_token": self.token()}
        self.assertEqual(
            validate_ack(body),
            {"operation_key": KEY, "kind": "checkin", "ack_token": self.token()},
        )

    def test_validate_ack_refuses_extra_missing_or_wrong_fields(self):
        token = self.token(kind="checkin")
        for body in (
            {},
            {"operation_key": KEY, "kind": "checkin"},
            {"operation_key": KEY, "kind": "checkin", "ack_token": token, "extra": 1},
            {"operation_key": KEY, "kind": "create", "ack_token": token},
            {"operation_key": "short", "kind": "checkin", "ack_token": token},
            {"operation_key": KEY, "kind": "checkin", "ack_token": token.upper()},
            {"operation_key": KEY, "kind": "checkin", "ack_token": "short"},
            ["not", "a", "mapping"],
        ):
            with self.subTest(body=str(body)[:24]):
                with self.assertRaises(AppointmentWriteError):
                    validate_ack(body)


class BudgetTests(unittest.TestCase):
    def test_budgets_are_the_approved_durable_limits(self):
        self.assertEqual(
            appointment_write.BUDGETS,
            {"book": 2, "reschedule": 1, "checkin": 1},
        )
        for kind, limit in appointment_write.BUDGETS.items():
            with self.subTest(kind=kind):
                self.assertEqual(appointment_write.budget(kind), limit)

    def test_patient_and_unknown_kinds_have_no_budget(self):
        for kind in ("create", "update", "", None, 1):
            with self.subTest(kind=kind):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.budget(kind)


BOOK_BODY = {
    "operation_key": KEY,
    "workspace_patient_id": "WS-1",
    "appointment_date": "2026-11-20",
    "appointment_time": "09:30",
}


RESCHEDULE_BODY = {
    "operation_key": KEY,
    "workspace_appointment_id": "wsa1_" + "a" * 32 + "_" + "b" * 16 + "_" + KEY,
    "base_modified": "2026-11-14 09:30:00.123456",
    "appointment_date": "2026-11-21",
    "appointment_time": "10:15:00",
}


CHECKIN_BODY = {
    "operation_key": KEY,
    "workspace_appointment_id": "wsa1_" + "a" * 32 + "_" + "b" * 16 + "_" + KEY,
    "base_modified": "2026-11-14 09:30:00.123456",
}


class BookBodyTests(unittest.TestCase):
    def test_accepts_the_strict_booking_surface(self):
        self.assertEqual(
            appointment_write.validate_book(dict(BOOK_BODY)),
            {
                "kind": "book",
                "operation_key": KEY,
                "workspace_patient_id": "WS-1",
                "fields": {
                    "appointment_date": "2026-11-20",
                    "appointment_time": "09:30:00",
                },
            },
        )

    def test_time_is_normalized_to_seconds(self):
        payload = appointment_write.validate_book(
            dict(BOOK_BODY, appointment_time="23:05:09")
        )
        self.assertEqual(payload["fields"]["appointment_time"], "23:05:09")

    def test_rejects_server_owned_and_unknown_fields(self):
        for key in (
            "patient",
            "native_patient_id",
            "native_appointment_id",
            "department",
            "appointment_type",
            "company",
            "practitioner",
            "status",
            "naming_series",
            "modified",
        ):
            with self.subTest(key=key):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_book(dict(BOOK_BODY, **{key: "x"}))

    def test_rejects_missing_or_malformed_values(self):
        for body in (
            {},
            dict(BOOK_BODY, operation_key="short"),
            dict(BOOK_BODY, workspace_patient_id=""),
            dict(BOOK_BODY, appointment_date="2026-02-30"),
            dict(BOOK_BODY, appointment_date="20-11-2026"),
            dict(BOOK_BODY, appointment_time="24:00"),
            dict(BOOK_BODY, appointment_time="09:3"),
            {key: value for key, value in BOOK_BODY.items() if key != "appointment_date"},
            ["not", "a", "mapping"],
        ):
            with self.subTest(body=str(body)[:32]):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_book(body)


class RescheduleBodyTests(unittest.TestCase):
    def test_accepts_only_date_and_time_changes(self):
        payload = appointment_write.validate_reschedule(dict(RESCHEDULE_BODY))
        self.assertEqual(payload["kind"], "reschedule")
        self.assertEqual(payload["workspace_appointment_id"], RESCHEDULE_BODY["workspace_appointment_id"])
        self.assertEqual(payload["base_modified"], RESCHEDULE_BODY["base_modified"])
        self.assertEqual(
            payload["fields"],
            {"appointment_date": "2026-11-21", "appointment_time": "10:15:00"},
        )

    def test_requires_both_date_and_time(self):
        for key in ("appointment_date", "appointment_time"):
            with self.subTest(key=key):
                body = {k: v for k, v in RESCHEDULE_BODY.items() if k != key}
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_reschedule(body)

    def test_rejects_status_patient_and_unknown_fields(self):
        for key in (
            "status",
            "patient",
            "workspace_patient_id",
            "department",
            "appointment_type",
            "practitioner",
            "base_modifiedx",
        ):
            with self.subTest(key=key):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_reschedule(
                        dict(RESCHEDULE_BODY, **{key: "x"})
                    )

    def test_rejects_malformed_base_version_and_alias(self):
        for override in (
            {"base_modified": None},
            {"base_modified": "  "},
            {"base_modified": "yesterday"},
            {"workspace_appointment_id": ""},
            {"workspace_appointment_id": "NATIVE-APPT-1"},
        ):
            with self.subTest(override=override):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_reschedule(
                        dict(RESCHEDULE_BODY, **override)
                    )


class CheckinBodyTests(unittest.TestCase):
    def test_accepts_key_alias_and_version_only(self):
        self.assertEqual(
            appointment_write.validate_checkin(dict(CHECKIN_BODY)),
            {
                "kind": "checkin",
                "operation_key": KEY,
                "workspace_appointment_id": CHECKIN_BODY["workspace_appointment_id"],
                "base_modified": CHECKIN_BODY["base_modified"],
                "fields": {},
            },
        )

    def test_rejects_date_time_status_and_unknown_fields(self):
        for key in (
            "appointment_date",
            "appointment_time",
            "status",
            "patient",
            "native_appointment_id",
            "practitioner",
        ):
            with self.subTest(key=key):
                with self.assertRaises(AppointmentWriteError):
                    appointment_write.validate_checkin(
                        dict(CHECKIN_BODY, **{key: "x"})
                    )


class OperationDigestTests(unittest.TestCase):
    def digest(self, **override):
        arguments = dict(
            kind="book",
            operation_key=KEY,
            fields={"patient_alias": "alias", "date": "2026-11-20"},
        )
        arguments.update(override)
        return operation_digest(SECRET, **arguments)

    def test_digest_is_canonical_and_stable(self):
        first = self.digest(fields={"b": 2, "a": 1})
        second = self.digest(fields={"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertRegex(first, r"^[0-9a-f]{64}$")

    def test_digest_is_bound_to_kind_key_and_fields(self):
        reference = self.digest()
        for override in (
            {"kind": "reschedule"},
            {"operation_key": "appt-op-key-0002-0123456789"},
            {"fields": {"patient_alias": "alias", "date": "2026-11-21"}},
        ):
            with self.subTest(override=override):
                self.assertNotEqual(self.digest(**override), reference)

    def test_digest_refuses_invalid_input(self):
        for override in (
            {"kind": "create"},
            {"operation_key": "short"},
            {"fields": None},
            {"fields": {"nested": {"a": 1}}},
            {"fields": {1: "x"}},
        ):
            with self.subTest(override=override):
                with self.assertRaises(AppointmentWriteError):
                    self.digest(**override)
        with self.assertRaises(AppointmentWriteError):
            operation_digest(b"short", kind="book", operation_key=KEY, fields={})


if __name__ == "__main__":
    unittest.main()
