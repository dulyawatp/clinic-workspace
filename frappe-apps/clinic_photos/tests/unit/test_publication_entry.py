"""Public-seam tests for the default-off durable photo-save entry.

The tests drive ``clinic_photos.api.photo_save`` through the narrow fake Frappe
module only at the framework boundary. They prove local gate/auth/validation refusal
behaviour: POST-only dispatch, the native cookie session plus explicit CSRF token,
the current enabled clinician requirement, the server-owned publication gate and the
exact synthetic site/mode restriction. Accepted staging, status and transaction
ownership are covered by ``test_async_publication_submission.py``. These tests do
**not** prove installed Frappe HTTP, transaction isolation or native permissions;
those remain separate native gates.
"""

import base64
import contextlib
import types
import unittest
from dataclasses import FrozenInstanceError
from unittest import mock

from fake_frappe import fake_frappe

from clinic_photos.api import photo_save
from clinic_photos.publication import contracts


AUTHOR = "clinician@example.test"

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)


def docs(user=AUTHOR, enabled=1, role="Physician"):
    return {
        "User": {user: {"name": user, "enabled": enabled}},
        "Has Role": {
            f"has-role-{user}": {
                "name": f"has-role-{user}",
                "parent": user,
                "parenttype": "User",
                "role": role,
            }
        },
    }


@contextlib.contextmanager
def runtime(user=AUTHOR, enabled=1, role="Physician", method="POST"):
    with fake_frappe(docs=docs(user, enabled, role), user=user) as frappe:
        frappe.conf = {}
        frappe.session.sid = "sid-entry-test"
        frappe.session.data = types.SimpleNamespace(csrf_token="csrf-entry-test")
        headers = {"X-Frappe-CSRF-Token": "csrf-entry-test"}
        frappe.local.request = types.SimpleNamespace(
            method=method,
            cookies={"sid": "sid-entry-test"},
            headers=headers,
        )

        def get_request_header(name, default=None):
            return headers.get(name, default)

        frappe.get_request_header = get_request_header
        yield frappe


def annotation_payload():
    return {
        "kind": "annotation_append",
        "request_key": "request-key-0001",
        "draft": "DRAFT-1",
        "expected_version": 1,
        "original": "PHOTO-1",
        "annotation": {
            "schema_version": 1,
            "coordinate_space": "normalized",
            "shapes": [],
        },
    }


def synthetic_payload():
    return {
        "kind": "synthetic_original_create",
        "request_key": "request-key-0002",
        "synthetic_patient": "SYNTHETIC-PATIENT-1",
        "image_base64": PNG_1X1_BASE64,
    }


def copy_docs(frappe):
    return {
        doctype: {name: dict(row) for name, row in rows.items()}
        for doctype, rows in frappe.db.docs.items()
    }


class EntryGateTests(unittest.TestCase):
    def test_disabled_entry_never_commits(self):
        with runtime() as frappe:
            before = copy_docs(frappe)
            with mock.patch.object(frappe.db, "commit") as commit:
                with self.assertRaises(frappe.PermissionError) as caught:
                    photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DISABLED)
            commit.assert_not_called()
            self.assertEqual(copy_docs(frappe), before)

    def test_get_is_denied_before_the_gate(self):
        with runtime(method="GET") as frappe:
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

    def test_guest_is_denied(self):
        with runtime(user="Guest") as frappe:
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

    def test_administrator_is_not_an_ordinary_clinician(self):
        with runtime(user="Administrator") as frappe:
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

    def test_non_clinician_is_denied(self):
        with runtime(role="Accounts User") as frappe:
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)

    def test_disabled_clinician_is_denied(self):
        with runtime(enabled=0) as frappe:
            with self.assertRaises(frappe.PermissionError) as caught:
                photo_save.save(annotation_payload())

            self.assertEqual(str(caught.exception), photo_save.SAFE_DENIAL)


class RequestContractTests(unittest.TestCase):
    def test_annotation_append_accepts_exactly_its_kind_fields(self):
        request = contracts.validate_request(annotation_payload())

        self.assertIsInstance(request, contracts.SaveRequest)
        self.assertEqual(request.kind, "annotation_append")
        self.assertEqual(request.request_key, "request-key-0001")
        self.assertEqual(request.draft, "DRAFT-1")
        self.assertEqual(request.expected_version, 1)
        self.assertEqual(request.original, "PHOTO-1")
        self.assertEqual(
            request.annotation,
            '{"coordinate_space":"normalized","schema_version":1,"shapes":[]}',
        )
        self.assertIsNone(request.synthetic_patient)
        self.assertIsNone(request.image_bytes)

    def test_annotation_append_requires_every_kind_field(self):
        for fieldname in ("kind", "request_key", "draft", "expected_version", "original", "annotation"):
            with self.subTest(fieldname=fieldname):
                payload = annotation_payload()
                payload.pop(fieldname)
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_annotation_append_rejects_synthetic_fields(self):
        for fieldname, value in (
            ("synthetic_patient", "SYNTHETIC-PATIENT-1"),
            ("image_base64", PNG_1X1_BASE64),
        ):
            with self.subTest(fieldname=fieldname):
                payload = annotation_payload()
                payload[fieldname] = value
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_synthetic_original_accepts_exactly_its_kind_fields(self):
        request = contracts.validate_request(synthetic_payload())

        self.assertEqual(request.kind, "synthetic_original_create")
        self.assertEqual(request.request_key, "request-key-0002")
        self.assertEqual(request.synthetic_patient, "SYNTHETIC-PATIENT-1")
        self.assertEqual(request.image_bytes, PNG_1X1_BYTES)
        self.assertIsNone(request.draft)
        self.assertIsNone(request.expected_version)
        self.assertIsNone(request.original)
        self.assertIsNone(request.annotation)

    def test_synthetic_original_requires_every_kind_field(self):
        for fieldname in ("kind", "request_key", "synthetic_patient", "image_base64"):
            with self.subTest(fieldname=fieldname):
                payload = synthetic_payload()
                payload.pop(fieldname)
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_synthetic_original_rejects_annotation_fields(self):
        for fieldname, value in (
            ("draft", "DRAFT-1"),
            ("expected_version", 1),
            ("original", "PHOTO-1"),
            ("annotation", {"shapes": []}),
        ):
            with self.subTest(fieldname=fieldname):
                payload = synthetic_payload()
                payload[fieldname] = value
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_client_authority_fields_are_rejected(self):
        for fieldname in (
            "owner",
            "generation",
            "path",
            "storage_key",
            "file_url",
            "ignore_permissions",
            "permissions",
            "permission_flags",
            "synthetic",
            "synthetic_mode",
            "site",
            "actor",
            "user",
            "is_private",
        ):
            with self.subTest(fieldname=fieldname):
                payload = annotation_payload()
                payload[fieldname] = "client-supplied"
                with self.assertRaises(contracts.PublicationValidationError) as caught:
                    contracts.validate_request(payload)

                self.assertEqual(
                    str(caught.exception), contracts.SAFE_VALIDATION
                )

    def test_unknown_kind_and_non_mapping_are_rejected(self):
        for payload in (
            {},
            {**annotation_payload(), "kind": "original_ingest"},
            [],
            "not-a-payload",
            None,
        ):
            with self.subTest(payload=payload):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_expected_version_accepts_zero_and_rejects_booleans_and_negatives(self):
        for value in (True, False, -1, 1.5, "1"):
            with self.subTest(value=value):
                payload = annotation_payload()
                payload["expected_version"] = value
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

        fresh = annotation_payload()
        fresh["expected_version"] = 0
        request = contracts.validate_request(fresh)
        self.assertEqual(request.expected_version, 0)

        operation = contracts.PreparedOperation(
            id="OP-1",
            site="clinic-photo-accept.local",
            actor=AUTHOR,
            kind="annotation_append",
            payload_digest="a" * 64,
            key="/private/files/clinic-annotation-1.json",
            generation=1,
            draft="DRAFT-1",
            expected_version=0,
            synthetic_patient=None,
            target_identity="draft:DRAFT-1",
            expected_sha256="b" * 64,
            expected_size=1,
            state="PREPARED",
        )
        self.assertEqual(operation.expected_version, 0)

    def test_annotation_must_be_a_json_object(self):
        for value in ([], "shapes", {"ratio": float("nan")}, {1: "not-a-string-key"}):
            with self.subTest(value=value):
                payload = annotation_payload()
                payload["annotation"] = value
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_image_base64_must_be_strict_standard_base64(self):
        for value in (
            bytearray(b"bytes"),
            memoryview(b"bytes"),
            "",
            None,
            "data:image/png;base64," + PNG_1X1_BASE64,
            PNG_1X1_BASE64[:8] + "\n" + PNG_1X1_BASE64[8:],
            "not base64!",
        ):
            with self.subTest(value=value):
                payload = synthetic_payload()
                payload["image_base64"] = value
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.validate_request(payload)

    def test_request_is_immutable(self):
        request = contracts.validate_request(annotation_payload())

        with self.assertRaises(FrozenInstanceError):
            request.draft = "DRAFT-2"

    def test_shared_records_validate_states_and_receipt_identity(self):
        outcome = contracts.SaveOutcome("saved", "OP-1", 1, "annotation.json")
        self.assertEqual(outcome.status, "saved")
        for status in ("receipted", "", "SAVED"):
            with self.subTest(status=status):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.SaveOutcome(status, "OP-1", 1, "annotation.json")

        operation = contracts.PreparedOperation(
            id="OP-1",
            site="clinic-photo-accept.local",
            actor=AUTHOR,
            kind="annotation_append",
            payload_digest="a" * 64,
            key="/private/files/clinic-annotation-1.json",
            generation=1,
            draft="DRAFT-1",
            expected_version=1,
            synthetic_patient=None,
            target_identity="draft:DRAFT-1",
            expected_sha256="b" * 64,
            expected_size=1,
            state="PREPARED",
        )
        self.assertEqual(operation.state, "PREPARED")
        for state in ("RECEIPTED", "prepared", ""):
            with self.subTest(state=state):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.PreparedOperation(
                        id="OP-1",
                        site="clinic-photo-accept.local",
                        actor=AUTHOR,
                        kind="annotation_append",
                        payload_digest="a" * 64,
                        key="/private/files/clinic-annotation-1.json",
                        generation=1,
                        draft="DRAFT-1",
                        expected_version=1,
                        synthetic_patient=None,
                        target_identity="draft:DRAFT-1",
                        expected_sha256="b" * 64,
                        expected_size=1,
                        state=state,
                    )

        receipt = contracts.CreationReceipt(
            operation_id="OP-1",
            generation=1,
            key="/private/files/clinic-annotation-1.json",
            device=1,
            inode=2,
            size=3,
            sha256="a" * 64,
        )
        self.assertEqual(receipt.size, 3)
        for sha256 in ("A" * 64, "a" * 63, "g" * 64, ""):
            with self.subTest(sha256=sha256):
                with self.assertRaises(contracts.PublicationValidationError):
                    contracts.CreationReceipt(
                        operation_id="OP-1",
                        generation=1,
                        key="/private/files/clinic-annotation-1.json",
                        device=1,
                        inode=2,
                        size=3,
                        sha256=sha256,
                    )


class SyntheticModeGateTests(unittest.TestCase):
    def test_synthetic_kind_requires_exact_site_and_exact_true_mode(self):
        cases = (
            (None, False),
            (None, True),
            ("other.local", True),
            ("clinic-photo-accept.local", False),
            ("clinic-photo-accept.local", None),
            ("clinic-photo-accept.local", 1),
            ("clinic-photo-accept.local", "true"),
        )
        for site, mode in cases:
            with self.subTest(site=site, mode=mode):
                with runtime() as frappe:
                    frappe.conf[photo_save.GATE_CONFIG_KEY] = True
                    if site is not None:
                        frappe.local.site = site
                    if mode is not None:
                        frappe.conf["clinic_photos_synthetic_mode"] = mode

                    with mock.patch.object(frappe.db, "commit") as commit:
                        with mock.patch.object(frappe.db, "rollback") as rollback:
                            with self.assertRaises(frappe.PermissionError) as caught:
                                photo_save.save(synthetic_payload())

                    self.assertEqual(
                        str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED
                    )
                    commit.assert_not_called()
                    rollback.assert_not_called()

    def test_annotation_append_is_not_subject_to_synthetic_mode_gate(self):
        with runtime() as frappe:
            frappe.conf[photo_save.GATE_CONFIG_KEY] = True
            frappe.local.site = "live.example"
            frappe.conf["clinic_photos_synthetic_mode"] = False
            payload = annotation_payload()
            payload["owner"] = "client-supplied-owner"

            with self.assertRaises(frappe.ValidationError) as caught:
                photo_save.save(payload)

            self.assertEqual(str(caught.exception), photo_save.SAFE_VALIDATION)
            self.assertNotEqual(
                str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED
            )

    def test_client_synthetic_mode_and_site_fields_are_rejected(self):
        with runtime() as frappe:
            frappe.conf[photo_save.GATE_CONFIG_KEY] = True
            frappe.local.site = "clinic-photo-accept.local"
            frappe.conf["clinic_photos_synthetic_mode"] = True

            for fieldname, value in (
                ("synthetic_mode", True),
                ("site", "clinic-photo-accept.local"),
            ):
                with self.subTest(fieldname=fieldname):
                    payload = synthetic_payload()
                    payload[fieldname] = value
                    with self.assertRaises(frappe.ValidationError) as caught:
                        photo_save.save(payload)

                    self.assertEqual(
                        str(caught.exception), photo_save.SAFE_VALIDATION
                    )
