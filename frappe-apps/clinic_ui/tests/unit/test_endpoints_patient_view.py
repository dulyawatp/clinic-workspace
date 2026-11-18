"""Endpoint tests for ``clinic_ui.api.patients.view``.

The view resolves a server-owned workspace alias to a native Patient and reads
only the allowlisted display fields for which the *current* user passes a
native read check. Tests use a fake gateway only: gate order, alias resolution
and fail-closed behaviour are asserted, not real Frappe permission semantics.
"""

import inspect
import unittest
from unittest import mock

from clinic_ui.core import config, patient_alias, patient_write
from clinic_ui.endpoints import aliases as aliases_endpoint
from clinic_ui.endpoints import patients as patients_endpoint

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
]

DETAIL = {
    "patient_name": "Patient One",
    "first_name": "Patient",
    "last_name": "One",
    "sex": "Female",
    "dob": "1990-01-02",
    "status": "Active",
    "modified": "2026-11-14T09:30:00.123456",
}


def gateway(**overrides):
    values = {
        "site": "marley.test",
        "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
        "current_session": session_view(roles=("Physician",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
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
        "current_session": session_view(roles=("Physician",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
    return fake


class PatientViewSuccessTests(unittest.TestCase):
    def test_returns_only_allowlisted_fields_for_an_authorized_alias(self):
        fake = gateway()

        payload = patients_endpoint.view(fake, "WS-1")

        self.assertEqual(
            payload,
            {
                "workspace_patient_id": "WS-1",
                "display_name": "Patient One",
                "first_name": "Patient",
                "last_name": "One",
                "sex": "Female",
                "dob": "1990-01-02",
                "status": "Active",
                "modified": "2026-11-14T09:30:00.123456",
                "editable": True,
            },
        )
        self.assertEqual(fake.patient_views, ["PAT-1"])

    def test_payload_never_carries_the_native_docname(self):
        fake = gateway()
        payload = patients_endpoint.view(fake, "WS-1")
        self.assertNotIn("native_patient_id", payload)
        self.assertNotIn("PAT-1", payload.values())

    def test_nullable_fields_are_preserved_as_null(self):
        fake = gateway()
        fake.patient_view_detail["PAT-1"] = {
            "patient_name": "Patient One",
            "first_name": "Patient",
            "last_name": None,
            "sex": None,
            "dob": None,
            "status": "Active",
            "modified": "2026-11-14T09:30:00.123456",
        }
        payload = patients_endpoint.view(fake, "WS-1")
        self.assertIsNone(payload["last_name"])
        self.assertIsNone(payload["sex"])
        self.assertIsNone(payload["dob"])
        self.assertEqual(
            set(payload),
            {
                "workspace_patient_id",
                "display_name",
                "first_name",
                "last_name",
                "sex",
                "dob",
                "status",
                "modified",
                "editable",
            },
        )

    def test_marks_the_response_no_store(self):
        fake = gateway()
        patients_endpoint.view(fake, "WS-1")
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)

    def test_nurse_and_reception_roles_pass_the_clinic_gate(self):
        for roles in (("Nurse",), ("Clinic Reception",)):
            with self.subTest(roles=roles):
                fake = gateway(current_session=session_view(roles=roles))
                payload = patients_endpoint.view(fake, "WS-1")
                self.assertEqual(payload["display_name"], "Patient One")
                self.assertEqual(fake.patient_views, ["PAT-1"])

    def test_signature_takes_the_alias_and_nothing_else(self):
        parameters = list(inspect.signature(patients_endpoint.view).parameters)
        self.assertEqual(parameters, ["gateway", "workspace_patient_id"])


class PatientViewDenialTests(unittest.TestCase):
    def assertDenied(self, fake, workspace_patient_id="WS-1", native_reads=()):
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, workspace_patient_id)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)
        self.assertEqual(fake.patient_views, list(native_reads))

    def test_unconfigured_site_is_denied_before_any_native_read(self):
        self.assertDenied(gateway(settings={}))

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

    def test_unknown_identifier_is_denied_without_a_native_read(self):
        # Outside the converted development scope a non-alias value is never
        # offered to the native permission check.
        self.assertDenied(gateway(), "WS-UNKNOWN")

    def test_a_native_docname_without_read_authority_is_denied(self):
        fake = dev_gateway()
        fake.patient_can_read = False
        self.assertDenied(fake, "PAT-1")

    def test_malformed_alias_is_denied_without_a_native_read(self):
        for alias in (None, "", "   ", 5, ["WS-1"], "WS-1\x00", "W" * 257):
            with self.subTest(alias=alias):
                self.assertDenied(gateway(), alias)

    def test_native_permission_denial_fails_closed(self):
        for roles in (("Physician",), ("Clinic Reception",)):
            with self.subTest(roles=roles):
                fake = gateway(current_session=session_view(roles=roles))
                fake.patient_view_detail = {}
                self.assertDenied(fake, native_reads=["PAT-1"])

    def test_malformed_native_detail_fails_closed(self):
        for detail in (
            {"patient_name": "", "first_name": "P", "sex": "Female"},
            {"first_name": "P", "sex": "Female"},
            {
                "patient_name": "Patient One",
                "first_name": "bad\x00name",
                "sex": "Female",
            },
            {
                "patient_name": "Patient One",
                "first_name": "P",
                "sex": "Female",
                "extra": "leak",
            },
            {**DETAIL, "dob": "1990-02-30"},
            {**DETAIL, "dob": "not-a-date"},
        ):
            with self.subTest(detail=detail):
                fake = gateway()
                fake.patient_view_detail["PAT-1"] = dict(detail)
                self.assertDenied(fake, native_reads=["PAT-1"])

    def test_native_failure_fails_closed(self):
        fake = gateway()
        fake.patient_view_error = RuntimeError("native read unavailable")
        with self.assertRaises(RuntimeError):
            patients_endpoint.view(fake, "WS-1")
        self.assertEqual(fake.denials, [])
        self.assertGreaterEqual(fake.traceback_suppressions, 1)


class NativeDocnameReadTests(unittest.TestCase):
    """Converted creates return a native docname; the read path must accept it."""

    def test_a_readable_native_docname_returns_canonical_detail(self):
        fake = dev_gateway()
        payload = patients_endpoint.view(fake, "PAT-1")
        self.assertEqual(payload["workspace_patient_id"], "PAT-1")
        self.assertEqual(payload["display_name"], "Patient One")
        self.assertTrue(payload["editable"])
        self.assertEqual(fake.patient_read_calls, ["PAT-1"])
        self.assertEqual(fake.patient_views, ["PAT-1"])

    def test_a_native_docname_does_not_widen_editability(self):
        fake = dev_gateway()
        fake.write_authority = set()
        self.assertFalse(patients_endpoint.view(fake, "PAT-1")["editable"])

    def test_a_non_clinical_role_never_reaches_the_native_check(self):
        fake = dev_gateway(current_session=session_view(roles=("Accounts User",)))
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, "PAT-1")
        self.assertEqual(fake.patient_read_calls, [])

    def test_a_native_docname_is_refused_outside_the_approved_site(self):
        # Legacy site behavior is unchanged: only configured aliases resolve.
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
                with self.assertRaises(Denied):
                    patients_endpoint.view(fake, "PAT-1")
                self.assertEqual(fake.patient_read_calls, [])

    def test_the_static_alias_still_resolves_outside_the_approved_site(self):
        fake = gateway(
            site="marley.local",
            settings=settings(
                clinic_ui_synthetic_site="marley.local",
                clinic_ui_synthetic_patients=ALLOWLIST,
            ),
        )
        payload = patients_endpoint.view(fake, "WS-1")
        self.assertEqual(payload["workspace_patient_id"], "WS-1")
        self.assertEqual(payload["display_name"], "Patient One")

    def test_a_native_docname_stays_readable_when_development_writes_are_off(self):
        site = config.APPROVED_WRITE_SITE
        fake = dev_gateway(
            settings=settings(
                clinic_ui_synthetic_site=site,
                clinic_ui_synthetic_patients=ALLOWLIST,
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=site,
            )
        )

        payload = patients_endpoint.view(fake, "PAT-1")

        self.assertEqual(payload["workspace_patient_id"], "PAT-1")
        self.assertEqual(payload["display_name"], "Patient One")
        # Read authority is still required; the flag only disabled writes.
        fake = dev_gateway(
            settings=settings(
                clinic_ui_synthetic_site=site,
                clinic_ui_synthetic_patients=ALLOWLIST,
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=site,
            )
        )
        fake.patient_can_read = False
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, "PAT-1")
        self.assertEqual(fake.patient_views, [])

class PatientViewSealedAliasTests(unittest.TestCase):
    def gateway(self, **overrides):
        values = {
            "site": "marley.test",
            "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
            "current_session": session_view(roles=("Physician",)),
        }
        values.update(overrides)
        fake = FakeGateway(**values)
        fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
        alias = patient_alias.build_alias(
            SECRET.encode(), "marley.test", "nurse@example.test", "op-0123456789abcdef"
        )
        fake.create_receipts[
            ("nurse@example.test", "op-0123456789abcdef")
        ] = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id="PAT-1"
        )
        return fake, alias

    def test_committed_create_receipt_makes_the_sealed_alias_readable(self):
        fake, alias = self.gateway()
        payload = patients_endpoint.view(fake, alias)
        self.assertEqual(payload["workspace_patient_id"], alias)
        self.assertEqual(payload["modified"], DETAIL["modified"])
        self.assertTrue(payload["editable"])
        self.assertEqual(fake.patient_views, ["PAT-1"])

    def test_missing_create_receipt_is_denied(self):
        fake, alias = self.gateway()
        fake.create_receipts.clear()
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])

    def test_uncertain_create_receipt_is_denied_for_read(self):
        fake, alias = self.gateway()
        fake.create_receipts[
            ("nurse@example.test", "op-0123456789abcdef")
        ] = patient_write.WriteLocked()
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])


class CrossClinicianReadTests(unittest.TestCase):
    OPERATION_KEY = "op-0123456789abcdef"

    def created(
        self,
        creator="sit.doc@example.test",
        native_patient_id="PAT-2",
        *,
        alias=None,
    ):
        alias = alias or patient_alias.build_alias(
            SECRET.encode(), "marley.test", creator, self.OPERATION_KEY
        )
        receipt = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id=native_patient_id
        )
        return FakeCreateReceipt(creator, self.OPERATION_KEY, receipt), alias

    def gateway(self, **overrides):
        values = {
            "site": "marley.test",
            "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
            "current_session": session_view(
                user="nurse@example.test", roles=("Nurse",)
            ),
        }
        values.update(overrides)
        fake = FakeGateway(**values)
        fake.patient_view_detail = {"PAT-2": dict(DETAIL)}
        return fake

    def test_doc_and_nur_can_read_another_clinicians_created_patient(self):
        for user, roles in (
            ("doc2@example.test", ("Physician",)),
            ("nurse@example.test", ("Nurse",)),
        ):
            with self.subTest(roles=roles):
                fake = self.gateway(
                    current_session=session_view(user=user, roles=roles)
                )
                record, alias = self.created()
                fake.created_receipts = [record]
                payload = patients_endpoint.view(fake, alias)
                self.assertEqual(payload["workspace_patient_id"], alias)
                self.assertEqual(payload["display_name"], "Patient One")

    def test_cross_actor_read_is_bounded_to_the_aliased_operation_key(self):
        fake = self.gateway()
        record, alias = self.created()
        other, _ = self.created(creator="other@example.test", native_patient_id="PAT-3")
        fake.created_receipts = [other, record]
        patients_endpoint.view(fake, alias)
        self.assertEqual(
            fake.receipt_list_calls,
            [(aliases_endpoint.MAX_CREATED_RECEIPTS, self.OPERATION_KEY)],
        )

    def test_reception_is_refused_cross_actor_read(self):
        fake = self.gateway(
            current_session=session_view(
                user="sit.rec@example.test", roles=("Clinic Reception",)
            )
        )
        record, alias = self.created()
        fake.created_receipts = [record]
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])

    def test_cross_actor_native_read_denial_fails_closed(self):
        fake = self.gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.patient_view_detail = {}
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, ["PAT-2"])

    def test_receipt_alias_must_match_creator_site_and_key(self):
        fake = self.gateway()
        good, alias = self.created()
        fake.created_receipts = [
            FakeCreateReceipt("mallory@example.test", good.operation_key, good.receipt)
        ]
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])

    def test_duplicate_matching_receipts_are_ambiguous_and_denied(self):
        fake = self.gateway()
        record, alias = self.created()
        fake.created_receipts = [record, record]
        with self.assertRaises(Denied):
            patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])

    def test_overflowed_enumeration_page_is_denied(self):
        fake = self.gateway()
        record, alias = self.created()
        other, _ = self.created(creator="other@example.test", native_patient_id="PAT-3")
        fake.created_receipts = [record, other]
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 1):
            with self.assertRaises(Denied):
                patients_endpoint.view(fake, alias)
        self.assertEqual(fake.patient_views, [])

    def test_exact_enumeration_bound_is_not_overflow_and_is_readable(self):
        fake = self.gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        with mock.patch.object(aliases_endpoint, "MAX_CREATED_RECEIPTS", 1):
            payload = patients_endpoint.view(fake, alias)
        self.assertEqual(payload["workspace_patient_id"], alias)

    def test_missing_or_corrupt_fence_denies_the_cross_actor_read(self):
        for error in (
            patient_write.StoreUnavailable(),
            patient_write.WriteUncertain(),
        ):
            with self.subTest(error=type(error).__name__):
                fake = self.gateway()
                record, alias = self.created()
                fake.created_receipts = [record]
                fake.receipt_list_error = error
                with self.assertRaises(Denied):
                    patients_endpoint.view(fake, alias)
                self.assertEqual(fake.patient_views, [])

    def test_cross_actor_payload_withholds_the_native_id(self):
        fake = self.gateway()
        record, alias = self.created()
        fake.created_receipts = [record]
        payload = patients_endpoint.view(fake, alias)
        self.assertNotIn("native_patient_id", payload)
        self.assertNotIn("PAT-2", payload.values())


class EditableFlagTests(unittest.TestCase):
    OPERATION_KEY = "op-0123456789abcdef"

    def gateway(self, **overrides):
        values = {
            "site": "marley.test",
            "settings": settings(clinic_ui_synthetic_patients=ALLOWLIST),
            "current_session": session_view(roles=("Physician",)),
        }
        values.update(overrides)
        fake = FakeGateway(**values)
        fake.patient_view_detail = {"PAT-1": dict(DETAIL), "PAT-2": dict(DETAIL)}
        return fake

    def created(self, creator="sit.doc@example.test", native_patient_id="PAT-2"):
        alias = patient_alias.build_alias(
            SECRET.encode(), "marley.test", creator, self.OPERATION_KEY
        )
        receipt = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id=native_patient_id
        )
        return FakeCreateReceipt(creator, self.OPERATION_KEY, receipt), alias

    def test_static_alias_is_editable_only_with_native_write_authority(self):
        fake = self.gateway()
        self.assertTrue(patients_endpoint.view(fake, "WS-1")["editable"])

        fake = self.gateway()
        fake.can_write = False
        self.assertFalse(patients_endpoint.view(fake, "WS-1")["editable"])

    def test_reception_never_receives_an_editable_static_patient(self):
        fake = self.gateway(
            current_session=session_view(roles=("Clinic Reception",))
        )
        self.assertFalse(patients_endpoint.view(fake, "WS-1")["editable"])

    def test_creator_created_alias_is_editable(self):
        fake = self.gateway(
            current_session=session_view(
                user="sit.doc@example.test", roles=("Physician",)
            )
        )
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.create_receipts[(record.actor, record.operation_key)] = record.receipt
        self.assertTrue(patients_endpoint.view(fake, alias)["editable"])

    def test_non_creator_created_alias_is_read_only(self):
        fake = self.gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nurse",)
            )
        )
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.write_authority = {"PAT-2"}
        self.assertFalse(patients_endpoint.view(fake, alias)["editable"])

    def test_creator_alias_defaults_to_false_without_native_write_authority(self):
        fake = self.gateway(
            current_session=session_view(
                user="sit.doc@example.test", roles=("Physician",)
            )
        )
        record, alias = self.created()
        fake.created_receipts = [record]
        fake.create_receipts[(record.actor, record.operation_key)] = record.receipt
        fake.can_write = False
        self.assertFalse(patients_endpoint.view(fake, alias)["editable"])


if __name__ == "__main__":
    unittest.main()
