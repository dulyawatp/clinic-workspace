"""Endpoint tests for the local synthetic Patient create/edit methods."""

import unittest
from unittest import mock

from clinic_ui.core import config, patient_alias, patient_write
from clinic_ui.endpoints import patients_write

from .fake_gateway import Denied, FakeGateway, SECRET, detail, session_view, settings

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
        "operation_key": KEY,
        "first_name": "Patient",
        "last_name": "One",
        "sex": "Female",
        "dob": "1990-01-02",
    }
    values.update(overrides)
    return values


def update_body(**overrides):
    values = {
        "operation_key": KEY,
        "workspace_patient_id": "WS-1",
        "base_modified": "2026-11-14T09:30:00.123456",
        "first_name": "Patient",
    }
    values.update(overrides)
    return values


def sealed_alias(operation_key=KEY):
    return patient_alias.build_alias(
        SECRET.encode(), SITE, ACTOR, operation_key
    )


def ack_body(**overrides):
    values = {
        "operation_key": KEY,
        "kind": "create",
        "ack_token": "a" * 64,
    }
    values.update(overrides)
    return values


def create(fake, body):
    return patients_write.create(fake, body, CSRF)


def update(fake, body):
    return patients_write.update(fake, body, CSRF)


class CreateEndpointTests(unittest.TestCase):
    def test_created_status_and_patient_shape(self):
        fake = gateway()
        payload = create(fake, create_body())

        self.assertEqual(payload["status"], "created")
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
        self.assertFalse(payload["patient"]["editable"])
        self.assertEqual(
            payload["patient"]["workspace_patient_id"],
            patient_alias.build_alias(SECRET.encode(), SITE, ACTOR, KEY),
        )
        self.assertNotIn("native_patient_id", payload["patient"])
        self.assertEqual(fake.create_calls, [{
            "first_name": "Patient",
            "last_name": "One",
            "sex": "Female",
            "dob": "1990-01-02",
        }])
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(fake.fence_calls), 1)
        self.assertEqual(len(fake.fence_claim.committed), 1)
        self.assertEqual(fake.write_responses[0][0], 200)
        receipt = patient_write.encode_receipt(
            kind="create", alias=sealed_alias(), native_patient_id="PAT-NEW"
        )
        self.assertEqual(
            payload["ackToken"],
            patient_write.ack_token(
                SECRET.encode(), SITE, ACTOR, "create", KEY, receipt
            ),
        )

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
        self.assertEqual(fake.fence_calls, [])

    def test_native_create_authority_precedes_the_fence(self):
        fake = gateway()
        fake.can_create = False
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.create_calls, [])

    def test_live_gender_is_required_before_the_fence(self):
        fake = gateway()
        fake.genders = {"Male"}
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[0][0], 422)
        self.assertEqual(fake.fence_calls, [])

    def test_strict_body_rejections_do_not_dispatch(self):
        fake = gateway()
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
                self.assertEqual(fake.fence_calls, [])
                self.assertEqual(fake.commit_calls, 0)

    def test_lock_states_map_to_409(self):
        cases = (
            (patient_write.WriteLocked(), "WRITE_LOCKED"),
            (patient_write.WriteConflict(), "OPERATION_CONFLICT"),
            (patient_write.StoreUnavailable(), "WRITE_LOCKED"),
        )
        for error, code in cases:
            with self.subTest(error=error):
                fake = gateway()
                fake.fence_error = error
                payload = create(fake, create_body())
                self.assertEqual(payload, {"code": code})
                self.assertEqual(fake.write_responses[0][0], 409)
                self.assertEqual(fake.commit_calls, 0)

    def test_native_failure_after_dispatch_is_uncertain_and_locked(self):
        fake = gateway()
        fake.create_error = RuntimeError("native failure")
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_commit_failure_is_uncertain_and_locked(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal_claim(self):
        """The receipt is validated while the native write is uncommitted."""

        fake = gateway()
        real_encode = patient_write.encode_receipt
        real_claim_commit = fake.fence_claim.commit

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            return real_claim_commit(receipt)

        with mock.patch.object(
            patient_write, "encode_receipt", recording_encode
        ), mock.patch.object(
            fake.fence_claim, "commit", recording_claim_commit
        ):
            payload = create(fake, create_body())

        self.assertEqual(payload["status"], "created")
        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(fake.fence_claim.committed), 1)

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        """A rejected receipt is deterministic and lands before the commit."""

        fake = gateway()

        with mock.patch.object(
            patient_write,
            "encode_receipt",
            side_effect=patient_write.WriteInputError("invalid receipt alias"),
        ):
            payload = create(fake, create_body())

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.create_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.fence_claim.committed, [])
        self.assertEqual(len(fake.fence_claim.unknowns), 1)

    def test_missing_or_wrong_csrf_is_denied_before_dispatch(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                with self.assertRaises(Denied):
                    patients_write.create(fake, create_body(), supplied)
                self.assertEqual(fake.fence_calls, [])
                self.assertEqual(fake.create_calls, [])

    def test_deployment_marker_gate_is_enforced(self):
        fake = gateway()
        fake.deployment_approved = False
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.fence_calls, [])

    def test_unsafe_healthcare_settings_fail_closed_before_dispatch(self):
        fake = gateway()
        fake.side_effects_unsafe = True
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.create_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        create(fake, create_body())
        self.assertEqual(fake.strip_calls, 1)

    def test_failed_rollback_propagates_and_keeps_unknown_lock(self):
        fake = gateway()

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.create_error = RuntimeError("native failure")
        fake.rollback = rollback_failure
        with self.assertRaises(patients_write.RollbackFailed):
            create(fake, create_body())
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.write_responses, [])

    def test_unsafe_default_side_effect_denies_before_dispatch(self):
        fake = gateway()
        fake.default_side_effect = "customer"
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.create_calls, [])

    def test_missing_post_commit_view_is_locked_and_latched(self):
        fake = gateway()
        fake.patient_view_detail.clear()
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(len(fake.latch_calls), 1)
        self.assertEqual(fake.operation_latch_calls, [])

    def test_latch_failure_falls_back_to_the_operation_unknown(self):
        fake = gateway()
        fake.patient_view_detail.clear()
        fake.latch_error = RuntimeError("latch store unavailable")
        payload = create(fake, create_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.operation_latch_calls), 1)
        self.assertEqual(
            fake.operation_latch_calls[0][:3],
            (ACTOR, "create", KEY),
        )

    def test_both_latch_failures_raise_without_returning_a_normal_error(self):
        fake = gateway()
        fake.patient_view_detail.clear()
        fake.latch_error = RuntimeError("latch store unavailable")
        fake.operation_latch_error = RuntimeError("operation store unavailable")
        with self.assertRaises(patients_write.ManualInvestigationRequired):
            create(fake, create_body())
        self.assertEqual(fake.write_responses, [])


class UpdateEndpointTests(unittest.TestCase):
    def test_updated_status_uses_allowlist_alias(self):
        fake = gateway()
        payload = update(fake, update_body())

        self.assertEqual(payload["status"], "updated")
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
        self.assertEqual(fake.fence_calls[0][1], "update")
        receipt = patient_write.encode_receipt(
            kind="update", alias="WS-1", native_patient_id="PAT-1"
        )
        self.assertEqual(
            payload["ackToken"],
            patient_write.ack_token(
                SECRET.encode(), SITE, ACTOR, "update", KEY, receipt
            ),
        )

    def test_stale_base_is_refused_before_dispatch(self):
        fake = gateway()
        payload = update(
            fake, update_body(base_modified="2026-11-14T09:30:01.000000")
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.update_calls, [])

    def test_native_write_authority_precedes_the_fence(self):
        fake = gateway()
        fake.can_write = False
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.fence_calls, [])

    def test_sealed_alias_requires_a_committed_create_receipt(self):
        alias = sealed_alias()
        fake = gateway()
        fake.create_receipts[(ACTOR, KEY)] = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id="PAT-NEW"
        )
        fake.patient_modified["PAT-NEW"] = detail()["modified"]
        payload = update(fake, update_body(workspace_patient_id=alias))
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(payload["patient"]["workspace_patient_id"], alias)

    def test_sealed_alias_without_receipt_is_denied(self):
        fake = gateway()
        with self.assertRaises(Denied):
            update(fake, update_body(workspace_patient_id=sealed_alias()))
        self.assertEqual(fake.fence_calls, [])

    def test_sealed_alias_uncertain_create_is_locked(self):
        fake = gateway()
        fake.create_receipts[(ACTOR, KEY)] = patient_write.WriteLocked()
        payload = update(fake, update_body(workspace_patient_id=sealed_alias()))
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.fence_calls, [])

    def test_partial_update_accepts_clearable_fields(self):
        fake = gateway()
        payload = update(fake, update_body(last_name=None, dob=None))
        self.assertEqual(payload["status"], "updated")
        self.assertEqual(
            fake.update_calls[0][1],
            {"first_name": "Patient", "last_name": None, "dob": None},
        )

    def test_invalid_update_does_not_dispatch(self):
        fake = gateway()
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
                self.assertEqual(fake.fence_calls, [])
                self.assertEqual(fake.update_calls, [])

    def test_stale_version_during_native_update_is_uncertain(self):
        fake = gateway()
        fake.update_error = patient_write.StaleVersion()
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_native_update_failure_is_uncertain(self):
        fake = gateway()
        fake.update_error = RuntimeError("native failure")
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_the_update_receipt_is_encoded_before_the_commit_and_the_claim(self):
        fake = gateway()
        real_encode = patient_write.encode_receipt
        real_claim_commit = fake.fence_claim.commit

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            return real_claim_commit(receipt)

        with mock.patch.object(
            patient_write, "encode_receipt", recording_encode
        ), mock.patch.object(
            fake.fence_claim, "commit", recording_claim_commit
        ):
            payload = update(fake, update_body())

        self.assertEqual(payload["status"], "updated")
        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(fake.fence_claim.committed), 1)

    def test_an_update_receipt_encode_failure_never_commits(self):
        fake = gateway()

        with mock.patch.object(
            patient_write,
            "encode_receipt",
            side_effect=patient_write.WriteInputError("invalid receipt alias"),
        ):
            payload = update(fake, update_body())

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.update_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.fence_claim.committed, [])
        self.assertEqual(len(fake.fence_claim.unknowns), 1)

    def test_an_update_commit_failure_is_uncertain_and_locked(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = update(fake, update_body())
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.fence_claim.committed, [])

    def test_missing_or_wrong_csrf_is_denied_before_dispatch(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                with self.assertRaises(Denied):
                    patients_write.update(fake, update_body(), supplied)
                self.assertEqual(fake.fence_calls, [])
                self.assertEqual(fake.update_calls, [])

    def test_deployment_marker_gate_is_enforced(self):
        fake = gateway()
        fake.deployment_approved = False
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.fence_calls, [])

    def test_unsafe_healthcare_settings_fail_closed_before_dispatch(self):
        fake = gateway()
        fake.side_effects_unsafe = True
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.fence_calls, [])

    def test_unsafe_target_contact_or_customer_state_fails_closed(self):
        fake = gateway()
        fake.target_unsafe.add("PAT-1")
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.update_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        update(fake, update_body())
        self.assertEqual(fake.strip_calls, 1)

    def test_failed_rollback_propagates_and_keeps_unknown_lock(self):
        fake = gateway()
        fake.update_error = RuntimeError("native failure")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(patients_write.RollbackFailed):
            update(fake, update_body())
        self.assertEqual(len(fake.fence_claim.unknowns), 1)
        self.assertEqual(fake.write_responses, [])

    def test_signature_takes_body_and_csrf(self):
        import inspect

        self.assertEqual(
            list(inspect.signature(patients_write.create).parameters),
            ["gateway", "body", "csrf_token"],
        )
        self.assertEqual(
            list(inspect.signature(patients_write.update).parameters),
            ["gateway", "body", "csrf_token"],
        )
        self.assertEqual(
            list(inspect.signature(patients_write.ack).parameters),
            ["gateway", "body", "csrf_token"],
        )


class ReceptionWriteDenialTests(unittest.TestCase):
    """Reception is not a patient-write role even with native permission."""

    def reception(self):
        return gateway(
            current_session=session_view(
                user="reception@example.test",
                full_name="Reception Example",
                roles=("Clinic Reception",),
            )
        )

    def test_reception_create_is_denied_before_dispatch(self):
        fake = self.reception()
        fake.can_create = True
        with self.assertRaises(Denied):
            create(fake, create_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.create_calls, [])

    def test_reception_update_is_denied_before_dispatch(self):
        fake = self.reception()
        fake.can_write = True
        with self.assertRaises(Denied):
            update(fake, update_body())
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(fake.update_calls, [])

    def test_reception_ack_is_denied_before_the_fence(self):
        fake = self.reception()
        with self.assertRaises(Denied):
            patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(fake.ack_calls, [])


class AckEndpointTests(unittest.TestCase):
    def test_valid_ack_releases_the_barrier(self):
        fake = gateway()
        payload = patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(fake.write_responses[0][0], 200)
        self.assertEqual(fake.ack_calls, [(ACTOR, "create", KEY, "a" * 64)])

    def test_already_acknowledged_is_success(self):
        fake = gateway()
        fake.ack_status = "already"
        payload = patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(fake.write_responses[0][0], 200)

    def test_invalid_acknowledgement_is_locked(self):
        fake = gateway()
        fake.ack_status = "invalid"
        payload = patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.write_responses[0][0], 409)

    def test_malformed_ack_body_is_validation_and_never_acknowledges(self):
        cases = (
            {"kind": "delete"},
            {"ack_token": "short"},
            {"ack_token": "A" * 64},
            {"operation_key": "short"},
            {"extra": "field"},
            {"operation_key": "clinic-ui-manual-investigation-latch"},
        )
        for override in cases:
            with self.subTest(override=override):
                fake = gateway()
                body = ack_body(**override)
                payload = patients_write.ack(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[0][0], 422)
                self.assertEqual(fake.ack_calls, [])

    def test_missing_or_wrong_csrf_is_denied_before_ack(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                with self.assertRaises(Denied):
                    patients_write.ack(fake, ack_body(), supplied)
                self.assertEqual(fake.ack_calls, [])

    def test_write_gate_off_or_marker_missing_denies_ack(self):
        disabled = gateway(
            settings=settings(
                clinic_ui_native_patient_write_enabled=0,
                clinic_ui_native_patient_write_site=SITE,
            )
        )
        unmarked = gateway()
        unmarked.deployment_approved = False
        for fake in (disabled, unmarked):
            with self.subTest(site=fake.site):
                with self.assertRaises(Denied):
                    patients_write.ack(fake, ack_body(), CSRF)
                self.assertEqual(fake.ack_calls, [])

    def test_ack_does_not_require_native_patient_write_permission(self):
        fake = gateway()
        fake.can_create = False
        fake.can_write = False
        payload = patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.ack_calls), 1)

    def test_reserved_latch_actor_is_denied(self):
        fake = gateway(current_session=session_view(user="clinic-ui-site-latch"))
        with self.assertRaises(Denied):
            patients_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(fake.ack_calls, [])

    def test_ack_store_failure_is_locked(self):
        for error in (
            patient_write.StoreUnavailable(),
            patient_write.WriteUncertain(),
        ):
            with self.subTest(error=type(error).__name__):
                fake = gateway()
                fake.ack_error = error
                payload = patients_write.ack(fake, ack_body(), CSRF)
                self.assertEqual(payload, {"code": "WRITE_LOCKED"})
                self.assertEqual(fake.write_responses[0][0], 409)


if __name__ == "__main__":
    unittest.main()
