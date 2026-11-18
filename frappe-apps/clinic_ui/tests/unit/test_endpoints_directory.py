"""Endpoint tests for ``clinic_ui.api.directory.synthetic_patients``.

The endpoint takes no client input: the allowlist, the native authority check
and the current session all come from server-owned state. Tests use a fake
gateway only.
"""

import inspect
import unittest
from unittest import mock

from clinic_ui.core import config, dto, patient_alias, patient_write
from clinic_ui.endpoints import aliases as aliases_endpoint
from clinic_ui.endpoints import directory as directory_endpoint

from .fake_gateway import (
    Denied,
    FakeCreateReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

ALLOWLIST = [
    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
    {"workspace_patient_id": "WS-3", "native_patient_id": "PAT-3"},
]

AUTHORITY = {"PAT-1": "Patient One", "PAT-2": "Patient Two", "PAT-3": "Patient Three"}


def gateway(**overrides):
    values = {
        "site": "marley.test",
        "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
        "current_session": session_view(roles=("Nurse",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_authority = dict(AUTHORITY)
    return fake


DEV_SITE = config.APPROVED_WRITE_SITE


def dev_gateway(**overrides):
    """A gateway inside the converted development scope (approved site only)."""

    values = {
        "site": DEV_SITE,
        "settings": settings(
            clinic_ui_synthetic_site=DEV_SITE,
            clinic_ui_synthetic_patients=ALLOWLIST,
            clinic_ui_native_patient_write_site=DEV_SITE,
        ),
        "current_session": session_view(roles=("Nurse",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_authority = dict(AUTHORITY)
    return fake


class DirectorySuccessTests(unittest.TestCase):
    def test_returns_authorized_synthetic_patients_only(self):
        fake = gateway()
        fake.patient_authority["PAT-2"] = None

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "workspace_patient_id": "WS-1",
                        "display_name": "Patient One",
                    },
                    {
                        "workspace_patient_id": "WS-3",
                        "display_name": "Patient Three",
                    },
                ]
            },
        )
        self.assertEqual(fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"])

    def test_configured_native_ids_not_workspace_ids_are_checked(self):
        fake = gateway()
        directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"])

    def test_marks_the_response_no_store(self):
        fake = gateway()
        directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.no_store_calls, 1)

    def test_empty_allowlist_returns_no_items(self):
        fake = gateway(settings=settings(clinic_ui_synthetic_patients=[]))
        self.assertEqual(directory_endpoint.synthetic_patients(fake), {"items": []})
        self.assertEqual(fake.patient_reads, [])

    def test_malformed_display_name_is_omitted_without_failing_the_read(self):
        fake = gateway()
        fake.patient_authority["PAT-2"] = "bad\x00name"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-3"],
        )

    def test_reception_and_doctor_roles_are_accepted(self):
        for roles in (("Clinic Reception",), ("Physician",)):
            with self.subTest(roles=roles):
                fake = gateway(current_session=session_view(roles=roles))
                payload = directory_endpoint.synthetic_patients(fake)
                self.assertEqual(len(payload["items"]), 3)

    def test_no_client_input_parameter_exists(self):
        parameters = list(
            inspect.signature(directory_endpoint.synthetic_patients).parameters
        )
        self.assertEqual(parameters, ["gateway"])


class DirectoryDenialTests(unittest.TestCase):
    def assertDenied(self, fake):
        with self.assertRaises(Denied):
            directory_endpoint.synthetic_patients(fake)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)

    def test_unconfigured_site_is_denied_before_any_patient_read(self):
        fake = gateway(settings={})
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])

    def test_malformed_allowlist_fails_closed_before_any_patient_read(self):
        fake = gateway(
            settings=settings(
                clinic_ui_synthetic_patients=[
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-2"},
                ]
            )
        )
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])

    def test_guest_administrator_and_missing_sessions_are_denied(self):
        for view in (
            None,
            session_view(sid="Guest"),
            session_view(user="Guest"),
            session_view(user="Administrator"),
            session_view(enabled=False),
        ):
            with self.subTest(view=view):
                self.assertDenied(gateway(current_session=view))

    def test_unmapped_and_ambiguous_users_are_denied(self):
        for view in (
            session_view(roles=("Accounts User",)),
            session_view(roles=("Physician", "Nurse")),
        ):
            with self.subTest(view=view):
                self.assertDenied(gateway(current_session=view))

    def test_native_authority_failure_fails_closed_without_partial_items(self):
        fake = gateway()
        fake.patient_read_error = RuntimeError("native authority unavailable")
        with self.assertRaises(RuntimeError):
            directory_endpoint.synthetic_patients(fake)
        self.assertEqual(fake.denials, [])

    def test_role_outside_the_first_slice_is_denied_by_configuration(self):
        fake = gateway(settings=settings(clinic_ui_role_map={"PHA": ["Pharmacist"]}))
        self.assertDenied(fake)
        self.assertEqual(fake.patient_reads, [])


class CreatedPatientDirectoryTests(unittest.TestCase):
    def created(
        self,
        actor="sit.doc@example.test",
        operation_key="op-0123456789abcdef",
        native_patient_id="PAT-NEW",
        *,
        alias=None,
        site="marley.test",
    ):
        alias = alias or patient_alias.build_alias(
            SECRET.encode(), site, actor, operation_key
        )
        receipt = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id=native_patient_id
        )
        return FakeCreateReceipt(actor, operation_key, receipt), alias

    def test_merges_committed_created_patients_after_the_static_allowlist(self):
        fake = gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.patient_authority["PAT-NEW"] = "Created Patient"

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", alias],
        )
        self.assertEqual(payload["items"][3]["display_name"], "Created Patient")
        self.assertEqual(
            set(payload["items"][3]), {"workspace_patient_id", "display_name"}
        )
        limit, operation_key = fake.receipt_list_calls[0]
        self.assertIsNone(operation_key)
        self.assertGreater(limit, 0)

    def test_native_read_denial_omits_the_created_patient(self):
        fake = gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        # No display authority entry for PAT-NEW.
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )
        self.assertIn("PAT-NEW", fake.patient_reads)

    def test_missing_or_corrupt_fence_keeps_the_static_directory(self):
        for error in (
            patient_write.StoreUnavailable(),
            patient_write.WriteUncertain(),
        ):
            with self.subTest(error=type(error).__name__):
                fake = gateway()
                fake.receipt_list_error = error
                payload = directory_endpoint.synthetic_patients(fake)
                self.assertEqual(
                    [item["workspace_patient_id"] for item in payload["items"]],
                    ["WS-1", "WS-2", "WS-3"],
                )
                self.assertEqual(
                    fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"]
                )

    def test_receipt_alias_must_match_creator_site_and_operation_key(self):
        fake = gateway()
        good, alias = self.created()
        # Same stored alias, but the fence row names a different creator.
        fake.created_receipts = [
            FakeCreateReceipt("mallory@example.test", good.operation_key, good.receipt)
        ]
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )
        self.assertNotIn(alias, [item["workspace_patient_id"] for item in payload["items"]])

    def test_malformed_receipt_is_omitted(self):
        fake = gateway()
        fake.created_receipts = [
            FakeCreateReceipt("sit.doc@example.test", "op-0123456789abcdef", "{")
        ]
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )

    def test_duplicate_alias_is_ambiguous_and_omitted(self):
        fake = gateway()
        record, alias = self.created()
        fake.created_receipts = [record, record]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )
        self.assertNotIn(alias, [item["workspace_patient_id"] for item in payload["items"]])

    def test_created_alias_colliding_with_static_alias_is_skipped(self):
        fake = gateway()
        record, alias = self.created(alias="WS-1")
        fake.created_receipts = [record]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )

    def test_static_and_historical_items_keep_their_alias(self):
        fake = gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        payload = directory_endpoint.synthetic_patients(fake)
        for item in payload["items"]:
            self.assertEqual(
                set(item), {"workspace_patient_id", "display_name"}
            )
        self.assertNotIn("PAT-NEW", [item["workspace_patient_id"] for item in payload["items"]])

    def test_exact_combined_item_limit_includes_the_created_slice(self):
        fake = gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        with mock.patch.object(dto, "MAX_DIRECTORY_ITEMS", 4):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", alias],
        )

    def test_combined_overflow_omits_the_whole_created_slice(self):
        fake = gateway()
        first, first_alias = self.created(operation_key="op-aaaaaaaaaaaaaaaa")
        second, second_alias = self.created(
            operation_key="op-bbbbbbbbbbbbbbbb", native_patient_id="PAT-NEW-2"
        )
        fake.created_receipts = [first, second]
        fake.patient_authority["PAT-NEW"] = "Created One"
        fake.patient_authority["PAT-NEW-2"] = "Created Two"
        with mock.patch.object(dto, "MAX_DIRECTORY_ITEMS", 4):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )
        self.assertNotIn(first_alias, [
            item["workspace_patient_id"] for item in payload["items"]
        ])
        self.assertNotIn(second_alias, [
            item["workspace_patient_id"] for item in payload["items"]
        ])

    def test_enumeration_overflow_omits_created_even_when_room_remains(self):
        fake = gateway()
        records = [
            self.created(operation_key=f"op-{number:016d}", native_patient_id=f"PAT-{number}")[0]
            for number in (1, 2, 3)
        ]
        fake.created_receipts = records
        for number in (1, 2, 3):
            fake.patient_authority[f"PAT-{number}"] = f"Created {number}"
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 2):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )
        # No created native field is even read when the page overflows.
        self.assertEqual(fake.patient_reads, ["PAT-1", "PAT-2", "PAT-3"])

    def test_collision_past_the_enumeration_limit_is_not_partially_trusted(self):
        fake = gateway()
        first, first_alias = self.created(operation_key="op-aaaaaaaaaaaaaaaa")
        second, second_alias = self.created(
            operation_key="op-bbbbbbbbbbbbbbbb", native_patient_id="PAT-NEW-2"
        )
        duplicate = FakeCreateReceipt(
            first.actor, "op-cccccccccccccccc", first.receipt
        )
        fake.created_receipts = [first, second, duplicate]
        fake.patient_authority["PAT-NEW"] = "Created One"
        fake.patient_authority["PAT-NEW-2"] = "Created Two"
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 2):
            payload = directory_endpoint.synthetic_patients(fake)
        ids = [item["workspace_patient_id"] for item in payload["items"]]
        self.assertEqual(ids, ["WS-1", "WS-2", "WS-3"])
        self.assertNotIn(first_alias, ids)
        self.assertNotIn(second_alias, ids)

    def test_malformed_records_do_not_consume_a_created_slot(self):
        fake = gateway()
        malformed = FakeCreateReceipt(
            "sit.doc@example.test", "op-0000000000000001", "{"
        )
        valid, alias = self.created(operation_key="op-0000000000000002")
        fake.created_receipts = [malformed, valid]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 2):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", alias],
        )

    def test_denied_records_do_not_consume_a_created_slot(self):
        fake = gateway()
        denied, denied_alias = self.created(
            operation_key="op-0000000000000001", native_patient_id="PAT-DENIED"
        )
        visible, visible_alias = self.created(
            operation_key="op-0000000000000002", native_patient_id="PAT-VISIBLE"
        )
        fake.created_receipts = [denied, visible]
        fake.patient_authority["PAT-VISIBLE"] = "Visible Patient"
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 2):
            payload = directory_endpoint.synthetic_patients(fake)
        ids = [item["workspace_patient_id"] for item in payload["items"]]
        self.assertEqual(ids, ["WS-1", "WS-2", "WS-3", visible_alias])
        self.assertNotIn(denied_alias, ids)

    def test_exact_enumeration_bound_is_not_overflow(self):
        fake = gateway()
        first, first_alias = self.created(operation_key="op-0000000000000001")
        second, second_alias = self.created(
            operation_key="op-0000000000000002", native_patient_id="PAT-NEW-2"
        )
        fake.created_receipts = [first, second]
        fake.patient_authority["PAT-NEW"] = "Created One"
        fake.patient_authority["PAT-NEW-2"] = "Created Two"
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 2):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", first_alias, second_alias],
        )


class NativeDiscoveryTests(unittest.TestCase):
    """Newly created patients are discovered through a bounded native list."""

    def created(
        self,
        actor="sit.doc@example.test",
        operation_key="op-0123456789abcdef",
        *,
        site="marley.test",
    ):
        alias = patient_alias.build_alias(
            SECRET.encode(), site, actor, operation_key
        )
        receipt = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id="PAT-NEW"
        )
        return FakeCreateReceipt(actor, operation_key, receipt), alias

    def test_native_list_items_are_appended_after_the_alias_sources(self):
        fake = dev_gateway()
        fake.readable_patients = ["PAT-9"]
        fake.patient_authority["PAT-9"] = "Nine Patient"

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", "PAT-9"],
        )
        self.assertEqual(payload["items"][3]["display_name"], "Nine Patient")

    def test_the_native_list_is_bounded(self):
        fake = dev_gateway()
        fake.readable_patients = ["PAT-9"]
        fake.patient_authority["PAT-9"] = "Nine Patient"
        directory_endpoint.synthetic_patients(fake)
        self.assertEqual(len(fake.readable_list_calls), 1)
        limit = fake.readable_list_calls[0]
        self.assertGreater(limit, 0)
        self.assertLessEqual(limit, directory_endpoint.MAX_NATIVE_PATIENTS)

    def test_a_denied_display_name_is_omitted(self):
        fake = dev_gateway()
        fake.readable_patients = ["PAT-9"]
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )

    def test_a_static_native_id_is_not_listed_twice(self):
        fake = dev_gateway()
        fake.readable_patients = ["PAT-1", "PAT-9"]
        fake.patient_authority["PAT-1"] = "Patient One"
        fake.patient_authority["PAT-9"] = "Nine Patient"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", "PAT-9"],
        )

    def test_a_historically_created_native_id_keeps_its_alias(self):
        fake = dev_gateway()
        record, alias = self.created(site=DEV_SITE)
        fake.created_receipts = [record]
        fake.readable_patients = ["PAT-NEW"]
        fake.patient_authority["PAT-NEW"] = "Created Patient"
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", alias],
        )

    def test_a_missing_native_list_keeps_the_alias_directory(self):
        fake = dev_gateway()
        fake.readable_patients = None
        payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )

    def test_the_native_slice_is_cut_to_the_remaining_directory_room(self):
        fake = dev_gateway()
        fake.readable_patients = ["PAT-9"]
        fake.patient_authority["PAT-9"] = "Nine Patient"
        with mock.patch.object(dto, "MAX_DIRECTORY_ITEMS", 3):
            payload = directory_endpoint.synthetic_patients(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3"],
        )

    def test_the_native_slice_is_disabled_outside_the_approved_site(self):
        for site in ("marley.test", "marley.local"):
            with self.subTest(site=site):
                fake = gateway(
                    site=site,
                    settings=settings(
                        clinic_ui_synthetic_site=site,
                        clinic_ui_synthetic_patients=ALLOWLIST,
                        clinic_ui_native_patient_write_site=site,
                    ),
                )
                fake.readable_patients = ["PAT-9"]
                fake.patient_authority["PAT-9"] = "Nine Patient"

                payload = directory_endpoint.synthetic_patients(fake)

                self.assertEqual(
                    [item["workspace_patient_id"] for item in payload["items"]],
                    ["WS-1", "WS-2", "WS-3"],
                )
                self.assertEqual(fake.readable_list_calls, [])
                self.assertNotIn("PAT-9", fake.patient_reads)

    def test_the_native_slice_still_lists_permitted_records_when_writes_are_off(self):
        fake = dev_gateway(
            settings=settings(
                clinic_ui_synthetic_site=DEV_SITE,
                clinic_ui_synthetic_patients=ALLOWLIST,
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=DEV_SITE,
            )
        )
        fake.readable_patients = ["PAT-9"]
        fake.patient_authority["PAT-9"] = "Nine Patient"

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", "PAT-9"],
        )
        self.assertEqual(len(fake.readable_list_calls), 1)

    def test_the_historical_alias_slice_still_works_on_another_site(self):
        site = "marley.local"
        fake = gateway(
            site=site,
            settings=settings(
                clinic_ui_synthetic_site=site,
                clinic_ui_synthetic_patients=ALLOWLIST,
            ),
        )
        record, alias = self.created(site=site)
        fake.created_receipts = [record]
        fake.readable_patients = ["PAT-9"]
        fake.patient_authority["PAT-9"] = "Nine Patient"
        fake.patient_authority["PAT-NEW"] = "Created Patient"

        payload = directory_endpoint.synthetic_patients(fake)

        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "WS-2", "WS-3", alias],
        )
        self.assertEqual(fake.readable_list_calls, [])


if __name__ == "__main__":
    unittest.main()
