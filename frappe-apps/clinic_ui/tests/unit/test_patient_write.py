"""Strict, framework-free tests for the local Patient write contract."""

import unittest
from datetime import date, datetime, timedelta

from clinic_ui.core import patient_write

SECRET = b"0123456789abcdef0123456789abcdef"
KEY = "op-0123456789abcdef"
ALIAS = "wpa1_0123456789abcdef_op-0123456789abcdef"


def create_body(**overrides):
    values = {
        "operation_key": KEY,
        "first_name": "  Patient ",
        "last_name": " One ",
        "sex": "Female",
        "dob": "1990-01-02",
    }
    values.update(overrides)
    return values


def update_body(**overrides):
    values = {
        "operation_key": KEY,
        "workspace_patient_id": ALIAS,
        "base_modified": "2026-11-14T09:30:00.123456",
        "first_name": "Patient",
    }
    values.update(overrides)
    return values


class CreateValidationTests(unittest.TestCase):
    def test_normalizes_strict_fields(self):
        payload = patient_write.validate_create(create_body())
        self.assertEqual(
            payload,
            {
                "kind": "create",
                "operation_key": KEY,
                "fields": {
                    "first_name": "Patient",
                    "last_name": "One",
                    "sex": "Female",
                    "dob": "1990-01-02",
                },
            },
        )

    def test_optional_values_may_be_cleared(self):
        for value in (None, "", "   "):
            with self.subTest(value=value):
                payload = patient_write.validate_create(
                    create_body(last_name=value, dob=value)
                )
                self.assertIsNone(payload["fields"]["last_name"])
                self.assertIsNone(payload["fields"]["dob"])

    def test_unknown_and_privileged_fields_are_refused(self):
        for field in (
            "status",
            "patient_name",
            "customer",
            "user_id",
            "invite_user",
            "email",
            "mobile",
            "phone",
            "naming_series",
            "native_patient_id",
            "workspace_patient_id",
            "base_modified",
            "extra",
        ):
            with self.subTest(field=field):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_create(create_body(**{field: "x"}))

    def test_missing_required_fields_are_refused(self):
        for field in ("operation_key", "first_name", "sex"):
            with self.subTest(field=field):
                body = create_body()
                body.pop(field)
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_create(body)

    def test_optional_fields_may_be_absent(self):
        body = create_body()
        body.pop("last_name")
        body.pop("dob")
        payload = patient_write.validate_create(body)
        self.assertIsNone(payload["fields"]["last_name"])
        self.assertIsNone(payload["fields"]["dob"])

    def test_name_and_sex_shape(self):
        for field in ("first_name", "sex"):
            for value in (None, "", "  ", "bad\x00name", "x" * 141, 5):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(patient_write.WriteInputError):
                        patient_write.validate_create(
                            create_body(**{field: value})
                        )

    def test_dob_must_be_a_real_non_future_date(self):
        for value in (
            "2023-02-29",
            "1990-2-3",
            "1990-01-02T00:00:00",
            " 1990-01-02",
            (date.today() + timedelta(days=1)).isoformat(),
        ):
            with self.subTest(value=value):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_create(create_body(dob=value))

    def test_operation_key_is_strict(self):
        for value in (
            None,
            "",
            "short",
            "x" * 129,
            "has space 1234567890",
            "has/slash1234567890",
            "has\x0012345678901234",
            "clinic-ui-manual-investigation-latch",
        ):
            with self.subTest(value=value):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_create(
                        create_body(operation_key=value)
                    )


class UpdateValidationTests(unittest.TestCase):
    def test_partial_update_keeps_only_supplied_fields(self):
        payload = patient_write.validate_update(
            update_body(last_name=None, dob=None)
        )
        self.assertEqual(
            payload["fields"],
            {"first_name": "Patient", "last_name": None, "dob": None},
        )

    def test_update_requires_at_least_one_editable_field(self):
        body = update_body()
        for field in ("first_name", "last_name", "sex", "dob"):
            body.pop(field, None)
        with self.assertRaises(patient_write.WriteInputError):
            patient_write.validate_update(body)

    def test_missing_identity_or_base_is_refused(self):
        for field in ("operation_key", "workspace_patient_id", "base_modified"):
            with self.subTest(field=field):
                body = update_body()
                body.pop(field)
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_update(body)

    def test_unknown_fields_are_refused(self):
        for field in ("status", "patient_name", "invite_user", "native_patient_id"):
            with self.subTest(field=field):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_update(update_body(**{field: "x"}))

    def test_required_fields_cannot_be_cleared(self):
        for field in ("first_name", "sex"):
            for value in (None, "", "  "):
                with self.subTest(field=field, value=value):
                    with self.assertRaises(patient_write.WriteInputError):
                        patient_write.validate_update(
                            update_body(**{field: value})
                        )

    def test_invalid_base_modified_is_refused(self):
        for value in (None, "", "not a datetime", "x" * 65, 5):
            with self.subTest(value=value):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.validate_update(
                        update_body(base_modified=value)
                    )


class DigestAndReceiptTests(unittest.TestCase):
    def test_digest_is_stable_keyed_and_hex(self):
        payload = patient_write.validate_create(create_body())
        first = patient_write.operation_digest(SECRET, payload)
        second = patient_write.operation_digest(SECRET, payload)
        other = patient_write.operation_digest(SECRET + b"x", payload)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(len(first), 64)
        self.assertTrue(all(character in "0123456789abcdef" for character in first))

    def test_receipt_round_trip_is_strict(self):
        receipt = patient_write.encode_receipt(
            kind="create", alias=ALIAS, native_patient_id="PAT-1"
        )
        self.assertEqual(
            patient_write.decode_create_receipt(receipt), (ALIAS, "PAT-1")
        )
        for bad in (
            "",
            "{}",
            '{"version":1,"kind":"update","alias":"%s","native_patient_id":"PAT-1"}'
            % ALIAS,
            '{"version":1,"kind":"create","alias":"%s"}' % ALIAS,
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(patient_write.WriteInputError):
                    patient_write.decode_create_receipt(bad)

    def test_modified_values_compare_across_supported_forms(self):
        values = (
            "2026-11-14T09:30:00.123456",
            "2026-11-14 09:30:00.123456",
            "2026-11-14T09:30:00.123456Z",
        )
        self.assertTrue(
            patient_write.modified_matches(values[0], values[1])
        )
        self.assertTrue(
            patient_write.modified_matches(values[0], values[2])
        )
        self.assertFalse(
            patient_write.modified_matches(values[0], "2026-11-14T09:30:01")
        )
        self.assertFalse(patient_write.modified_matches(None, values[0]))
        self.assertTrue(
            patient_write.modified_matches(
                datetime(2026, 11, 14, 9, 30, 0, 123456), values[0]
            )
        )


if __name__ == "__main__":
    unittest.main()
