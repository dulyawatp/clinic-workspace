"""Endpoint tests for the converted native Patient create/edit lane.

The development lane relies on Frappe/Marley permissions and validation only:
no operation fence, no acknowledgement and no deployment marker. These tests
assert the native gate order, permission checks, CSRF, strict body validation,
transaction cleanup and the agreed ``{"status", "patient"}`` result. They also
assert that an unresolved historical fence can never block a converted write.
"""

import unittest

from clinic_ui.core import config, patient_alias, patient_write, write_fence
from clinic_ui.endpoints import patients_write

from .fake_gateway import (
    Denied,
    FakeGateway,
    NativePermissionDenied,
    SECRET,
    detail,
    session_view,
    settings,
)

KEY = "op-0123456789abcdef"
ACTOR = "nurse@example.test"
SITE = config.APPROVED_WRITE_SITE
CSRF = "csrf-token-value"


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": settings(
            clinic_ui_synthetic_site=SITE,
            clinic_ui_native_patient_write_site=SITE,
        ),
        "current_session": session_view(user=ACTOR),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.patient_view_detail["PAT-NEW"] = detail()
    fake.patient_view_detail["PAT-1"] = detail()
    fake.patient_modified["PAT-1"] = "2026-11-14T09:30:00.123456"
    return fake


def create_body(**overrides):
    values = {
        "first_name": "Patient",
        "last_name": "One",
        "sex": "Female",
        "dob": "1990-01-02",
    }
    values.update(overrides)
    return values


def update_body(**overrides):
    values = {
        "workspace_patient_id": "WS-1",
        "base_modified": "2026-11-14T09:30:00.123456",
        "first_name": "Patient",
    }
    values.update(overrides)
    return values


def sealed_alias(operation_key=KEY):
    return patient_alias.build_alias(SECRET.encode(), SITE, ACTOR, operation_key)


def create(fake, body):
    return patients_write.create(fake, body, CSRF)


def update(fake, body):
    return patients_write.update(fake, body, CSRF)


class CreateEndpointTests(unittest.TestCase):
    def test_created_result_returns_the_native_docname(self):
        fake = gateway()
        payload = create(fake, create_body())

        self.assertEqual(payload["status"], "created")
        self.assertEqual(set(payload), {"status", "patient"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["patient"]["workspace_patient_id"], "PAT-NEW")
        self.assertEqual(payload["patient"]["display_name"], "Patient One")
        self.assertEqual(
            set(payload["patient"]),
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
        self.assertNotIn("native_patient_id", payload["patient"])
        self.assertEqual(
            fake.create_calls,
            [
                {
                    "first_name": "Patient",
                    "last_name": "One",
                    "sex": "Female",
                    "dob": "1990-01-02",
                }
            ],
        )
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.write_responses[0][0], 200)

    def test_the_converted_lane_never_touches_the_operation_fence(self):
        fake = gateway()
        # A latched, unresolved or corrupt historical fence must not matter.
        fake.deployment_approved = False
        fake.fence_ready = False
        fake.fence_error = patient_write.StoreUnavailable()
        fake.create_receipts[(ACTOR, KEY)] = patient_write.WriteLocked()

        payload = create(fake, create_body())

        self.assertEqual(payload["status"], "created")
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.receipt_list_calls, [])
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.ack_calls, [])

    def test_write_gate_is_default_off(self):
        fake = gateway(
            settings=settings(
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=SITE,
            )
        )
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

    def test_another_site_is_refused(self):
        fake = gateway(
            site="marley.local",
            settings=settings(
                clinic_ui_synthetic_site="marley.local",
                clinic_ui_native_patient_write_site=SITE,
            ),
        )
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

    def test_missing_or_wrong_csrf_is_denied_before_dispatch(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                with self.assertRaises(Denied):
                    patients_write.create(fake, create_body(), supplied)
                self.assertEqual(fake.create_calls, [])

    def test_native_create_authority_is_required(self):
        fake = gateway()
        fake.can_create = False
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

    def test_a_live_gender_is_required(self):
        fake = gateway()
        fake.genders = {"Male"}
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[0][0], 422)
        self.assertEqual(fake.create_calls, [])

    def test_strict_body_rejections_do_not_dispatch(self):
        for body in (
            create_body(status="Disabled"),
            create_body(patient_name="Injected"),
            create_body(invite_user=1),
            create_body(email="x@example.test"),
            create_body(operation_key="short"),
            create_body(first_name=""),
            create_body(sex=""),
            create_body(dob="2999-01-01"),
        ):
            with self.subTest(body=body):
                fake = gateway()
                payload = create(fake, body)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.commit_calls, 0)
                self.assertEqual(fake.create_calls, [])

    def test_a_legacy_operation_key_is_accepted_and_ignored(self):
        fake = gateway()
        payload = create(fake, create_body(operation_key=KEY))
        self.assertEqual(payload["status"], "created")
        self.assertEqual(fake.commit_calls, 1)

    def test_an_invalid_supplied_sex_is_refused_before_dispatch(self):
        fake = gateway()
        fake.genders = {"Female"}
        payload = create(fake, create_body(sex="Other"))
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.rollback_calls, 0)

    def test_unsafe_healthcare_settings_fail_closed(self):
        fake = gateway()
        fake.side_effects_unsafe = True
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

    def test_unsafe_default_side_effect_denies_before_insert(self):
        fake = gateway()
        fake.default_side_effect = "customer"
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

    def test_native_failure_after_dispatch_is_uncertain(self):
        fake = gateway()
        fake.create_error = RuntimeError("native failure")
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_commit_failure_is_uncertain(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_failed_rollback_propagates(self):
        fake = gateway()

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.create_error = RuntimeError("native failure")
        fake.rollback = rollback_failure
        with self.assertRaises(patients_write.RollbackFailed):
            create(fake, create_body())
        self.assertEqual(fake.write_responses, [])

    def test_missing_post_commit_detail_is_uncertain_without_a_latch(self):
        fake = gateway()
        fake.patient_view_detail.clear()
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        create(fake, create_body())
        self.assertEqual(fake.strip_calls, 1)


class UpdateEndpointTests(unittest.TestCase):
    def test_updated_result_keeps_a_static_alias(self):
        fake = gateway()
        payload = update(fake, update_body())

        self.assertEqual(payload["status"], "updated")
        self.assertEqual(set(payload), {"status", "patient"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["patient"]["workspace_patient_id"], "WS-1")
        self.assertEqual(payload["patient"]["modified"], detail()["modified"])
        self.assertEqual(
            fake.update_calls,
            [
                (
                    "PAT-1",
                    {"first_name": "Patient"},
                    "2026-11-14T09:30:00.123456",
                )
            ],
        )
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.fence_calls, [])

    def test_updated_result_accepts_a_native_docname(self):
        fake = gateway()
        payload = update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["patient"]["workspace_patient_id"], "PAT-1")
        self.assertEqual(fake.update_calls[0][0], "PAT-1")

    def test_a_native_docname_needs_native_write_authority(self):
        fake = gateway()
        fake.write_authority = set()
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(fake.update_calls, [])

    def test_stale_base_is_refused_before_dispatch(self):
        fake = gateway()
        payload = update(
            fake, update_body(base_modified="2026-11-14T09:30:01.000000")
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.update_calls, [])

    def test_invalid_update_does_not_dispatch(self):
        for body in (
            update_body(status="Disabled"),
            update_body(patient_name="Injected"),
            update_body(first_name=""),
            update_body(base_modified="not-a-date"),
            update_body(operation_key="short"),
        ):
            with self.subTest(body=body):
                fake = gateway()
                payload = update(fake, body)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.update_calls, [])

    def test_partial_update_accepts_clearable_fields(self):
        fake = gateway()
        payload = update(fake, update_body(last_name=None, dob=None))
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(
            fake.update_calls[0][1],
            {"first_name": "Patient", "last_name": None, "dob": None},
        )

    def test_native_stale_version_is_reported_before_any_save(self):
        fake = gateway()
        fake.update_error = patient_write.StaleVersion()
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_native_update_failure_is_uncertain(self):
        fake = gateway()
        fake.update_error = RuntimeError("native failure")
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_failed_rollback_propagates(self):
        fake = gateway()
        fake.update_error = RuntimeError("native failure")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(patients_write.RollbackFailed):
            update(fake, update_body())
        self.assertEqual(fake.write_responses, [])

    def test_a_historical_sealed_alias_still_resolves_read_only(self):
        alias = sealed_alias()
        fake = gateway()
        fake.create_receipts[(ACTOR, KEY)] = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id="PAT-NEW"
        )
        fake.patient_modified["PAT-NEW"] = detail()["modified"]

        payload = update(fake, update_body(workspace_patient_id=alias))

        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["patient"]["workspace_patient_id"], alias)
        self.assertEqual(fake.update_calls[0][0], "PAT-NEW")

    def test_an_unresolvable_historical_alias_is_denied_not_locked(self):
        for stored in (None, patient_write.WriteLocked()):
            with self.subTest(stored=stored):
                fake = gateway()
                if stored is not None:
                    fake.create_receipts[(ACTOR, KEY)] = stored
                with self.assertRaises(Denied):
                    update(fake, update_body(workspace_patient_id=sealed_alias()))
                self.assertEqual(fake.update_calls, [])
                self.assertEqual(fake.write_responses, [])

    def test_a_broken_historical_fence_never_blocks_a_native_update(self):
        fake = gateway()
        fake.create_receipts[(ACTOR, KEY)] = patient_write.StoreUnavailable()

        payload = update(fake, update_body(workspace_patient_id="PAT-1"))

        self.assertEqual(payload["status"], "updated")
        self.assertEqual(fake.update_calls[0][0], "PAT-1")
        # A native docname never consults the historical ledger at all.
        self.assertEqual(fake.receipt_list_calls, [])
        self.assertEqual(fake.fence_calls, [])

    def test_native_write_authority_precedes_the_update(self):
        fake = gateway()
        fake.can_write = False
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.update_calls, [])

    def test_an_invalid_supplied_sex_is_refused_before_mutation(self):
        fake = gateway()
        fake.genders = {"Female"}

        payload = update(fake, update_body(sex="Other"))

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.update_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 0)

    def test_a_live_gender_is_accepted_on_update(self):
        fake = gateway()
        fake.genders = {"Female"}
        payload = update(fake, update_body(sex="Female"))
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(fake.update_calls[0][1], {"first_name": "Patient", "sex": "Female"})

    def test_a_partial_update_without_sex_does_not_require_a_gender(self):
        fake = gateway()
        fake.genders = set()
        payload = update(fake, update_body())
        self.assertEqual(payload["status"], "updated")

    def test_native_write_authority_precedes_the_gender_check(self):
        fake = gateway()
        fake.can_write = False
        fake.genders = {"Female"}
        with self.assertRaises(Denied):
            update(fake, update_body(sex="Other"))
        self.assertEqual(fake.update_calls, [])
        self.assertEqual(fake.write_responses, [])

    def test_unsafe_healthcare_settings_and_targets_fail_closed(self):
        fake = gateway()
        fake.side_effects_unsafe = True
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.update_calls, [])

        fake = gateway()
        fake.target_unsafe.add("PAT-1")
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.update_calls, [])

    def test_missing_post_commit_detail_is_uncertain(self):
        fake = gateway()
        fake.patient_view_detail.clear()
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.latch_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        update(fake, update_body())
        self.assertEqual(fake.strip_calls, 1)


class ReservedLatchActorTests(unittest.TestCase):
    """The historical fence's reserved identity stays reserved.

    No fence state is consulted; only the identity is refused, so the converted
    lane cannot be driven by the synthetic latch principal.
    """

    def test_the_reserved_latch_identity_cannot_write(self):
        fake = gateway(
            current_session=session_view(
                user=write_fence.LATCH_ACTOR, roles=("Nurse",)
            )
        )
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.create_calls, [])

        fake = gateway(
            current_session=session_view(
                user=write_fence.LATCH_ACTOR, roles=("Nurse",)
            )
        )
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.update_calls, [])


class PreMutationPermissionDenialTests(unittest.TestCase):
    """Only a definite pre-dispatch refusal becomes 403.

    ``prepare_native_patient_create`` performs no native mutation, so the
    adapter's own permission type there is a definite refusal. Everything from
    ``native_patient_update`` (including ``doc.save`` and its hooks), from the
    insert, or from the commit stays ``WRITE_UNCERTAIN``: a rollback cannot
    establish that no external side effect happened.
    """

    def test_create_preparation_denial_is_a_403_before_any_insert(self):
        fake = gateway()
        fake.prepare_error = NativePermissionDenied("denied")

        with self.assertRaises(Denied):
            create(fake, create_body())

        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 0)
        self.assertEqual(fake.write_responses, [])

    def test_create_preparation_failure_other_than_denial_is_not_a_write(self):
        fake = gateway()
        fake.prepare_error = RuntimeError("preparation failed")

        with self.assertRaises(RuntimeError):
            create(fake, create_body())

        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 0)
        self.assertEqual(fake.write_responses, [])

    def test_update_denial_is_uncertain_because_save_may_have_started(self):
        fake = gateway()
        fake.update_error = NativePermissionDenied("denied")

        payload = update(fake, update_body())

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.strip_calls, 0)

    def test_a_class_that_merely_looks_like_permission_error_is_not_a_denial(self):
        lookalike = type("PermissionError", (Exception,), {})
        fake = gateway()
        fake.prepare_error = lookalike("denied")

        with self.assertRaises(lookalike):
            create(fake, create_body())

        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.write_responses, [])

    def test_a_gateway_without_the_identity_check_does_not_claim_a_denial(self):
        fake = gateway()
        fake.is_native_permission_denial = None
        fake.prepare_error = NativePermissionDenied("denied")

        with self.assertRaises(NativePermissionDenied):
            create(fake, create_body())

        self.assertEqual(fake.create_calls, [])
        self.assertEqual(fake.write_responses, [])

    def test_a_non_permission_error_during_update_stays_uncertain(self):
        fake = gateway()
        fake.update_error = RuntimeError("native failure")

        payload = update(fake, update_body())

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_denial_during_insert_stays_uncertain_after_the_mutation(self):
        fake = gateway()
        fake.create_error = NativePermissionDenied("denied")

        payload = create(fake, create_body())

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_denial_during_commit_stays_uncertain(self):
        fake = gateway()

        def boom():
            raise NativePermissionDenied("commit refused")

        fake.commit = boom

        payload = create(fake, create_body())

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)


class DevelopmentScopeTests(unittest.TestCase):
    """Native identifiers are development behavior for the approved site only."""

    def test_another_site_cannot_write_through_a_native_docname(self):
        site = "marley.local"
        fake = gateway(
            site=site,
            settings=settings(
                clinic_ui_synthetic_site=site,
                clinic_ui_native_patient_write_enabled=1,
                clinic_ui_native_patient_write_site=site,
            ),
        )
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(fake.update_calls, [])

    def test_a_mismatched_configured_write_site_is_refused(self):
        fake = gateway(
            settings=settings(
                clinic_ui_synthetic_site=SITE,
                clinic_ui_native_patient_write_enabled=1,
                clinic_ui_native_patient_write_site="marley.local",
            )
        )
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(fake.update_calls, [])

    def test_a_native_docname_cannot_be_written_when_development_writes_are_off(self):
        fake = gateway(
            settings=settings(
                clinic_ui_synthetic_site=SITE,
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=SITE,
            )
        )
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(fake.update_calls, [])
        self.assertEqual(fake.rollback_calls, 0)

    def test_a_native_docname_never_bypasses_the_native_write_check(self):
        fake = gateway()
        fake.write_authority = set()
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id="PAT-1"))
        self.assertEqual(fake.update_calls, [])


if __name__ == "__main__":
    unittest.main()
