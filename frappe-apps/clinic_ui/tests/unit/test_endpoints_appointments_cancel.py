"""Endpoint tests for the converted appointment cancel and manual no-show lane.

The converted lane relies on Frappe/Marley permissions, validation and native
transactions: no operation fence, no write budget, no acknowledgement and no
deployment marker. These tests assert gate ordering, strict bodies, designed
pre-mutation refusals, native permission checks, transaction cleanup and the
agreed ``{"status", "appointment"}`` result, and that an unresolved historical
ledger can never block a converted transition.
"""

import unittest

from clinic_ui.core import appointment_write, config, patient_write
from clinic_ui.core.appointment_write import build_alias
from clinic_ui.endpoints import appointments_cancel_write

from .fake_gateway import (
    Denied,
    FakeGateway,
    NativePermissionDenied,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "reception@example.test"
CSRF = "csrf-token-value"
BOOK_KEY = "appt-book-key-0001-012345"
MODIFIED = "2026-11-14 09:30:00.123456"
TODAY = "2026-11-14"
NATIVE_TARGET = "NATIVE-APPT-1"
REASON = "Patient called to cancel the visit."


class UnrelatedPermissionError(Exception):
    """Same class *name* as the framework denial, unrelated type and module."""


UnrelatedPermissionError.__name__ = "PermissionError"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_patients=[
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
            {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
        ],
        clinic_ui_synthetic_appointments=[
            {
                "workspace_appointment_id": "APPT-1",
                "native_appointment_id": NATIVE_TARGET,
            },
        ],
        clinic_ui_native_appointment_write_enabled=1,
        clinic_ui_native_appointment_write_site=SITE,
        clinic_ui_appointment_medical_department="MEDDEP-1",
        clinic_ui_appointment_company="COMP-1",
        clinic_ui_appointment_type="APPT-TYPE-1",
        clinic_ui_appointment_checkin_patient="WS-2",
    )
    values.update(overrides)
    return values


def action_detail(**overrides):
    values = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Scheduled",
        "notes": None,
        "practitioner": None,
        "event": None,
        "invoiced": 0,
        "ref_sales_invoice": None,
        "paid_amount": 0,
        "insurance_coverage": None,
        "service_request": None,
        "mode_of_payment": None,
    }
    values.update(overrides)
    return values


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(user=ACTOR, roles=("Clinic Reception",)),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = FakeGateway(
        **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    fake.site_today_value = TODAY
    fake.appointment_action_details[NATIVE_TARGET] = action_detail()
    fake.appointment_locked[NATIVE_TARGET] = {"status": "Scheduled"}
    fake.appointment_detail_map[NATIVE_TARGET] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Cancelled",
        "modified": MODIFIED,
    }
    return fake


def alias_for(key):
    return build_alias(SECRET.encode(), SITE, ACTOR, key)


def body(**overrides):
    values = {
        "patient": "WS-1",
        "appointment_id": NATIVE_TARGET,
        "reason": REASON,
    }
    values.update(overrides)
    return values


def booked_target(fake, native_id=NATIVE_TARGET, actor=ACTOR):
    fake.appointment_receipts[(actor, "book", BOOK_KEY)] = (
        appointment_write.encode_receipt(
            kind="book", alias=alias_for(BOOK_KEY), native_appointment_id=native_id
        )
    )


class CancelTests(unittest.TestCase):
    def test_cancels_a_native_target(self):
        fake = gateway()
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(set(payload), {"status", "appointment"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["appointment"]["appointment_id"], NATIVE_TARGET)
        self.assertEqual(payload["appointment"]["status"], "Cancelled")
        self.assertEqual(len(fake.cancel_calls), 1)
        state, reason, actor = fake.cancel_calls[0]
        self.assertEqual(reason, REASON)
        self.assertEqual(actor, ACTOR)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.appointment_ack_calls, [])
        self.assertEqual(fake.appointment_receipt_calls, [])

    def test_cancels_a_historical_booking_alias_read_only(self):
        alias = alias_for(BOOK_KEY)
        fake = gateway()
        booked_target(fake)

        payload = appointments_cancel_write.cancel_appointment(
            fake, body(appointment_id=alias), CSRF
        )

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["appointment"]["appointment_id"], alias)
        self.assertEqual(len(fake.cancel_calls), 1)

    def test_cancels_a_native_patient_outside_the_static_allowlist(self):
        fake = gateway(patient_read_authority={"PAT-NEW"})
        fake.appointment_action_details[NATIVE_TARGET]["patient"] = "PAT-NEW"
        fake.appointment_locked[NATIVE_TARGET]["patient"] = "PAT-NEW"
        fake.appointment_detail_map[NATIVE_TARGET]["patient"] = "PAT-NEW"

        payload = appointments_cancel_write.cancel_appointment(
            fake, body(patient="PAT-NEW"), CSRF
        )

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "PAT-NEW")
        expected = fake.appointment_lock_calls[-1][2]
        self.assertEqual(expected["patient"], "PAT-NEW")
        self.assertNotIn("patients", expected)

    def test_a_historical_receipt_failure_is_a_denial_not_an_error(self):
        alias = alias_for(BOOK_KEY)
        for error in (
            patient_write.StoreUnavailable(),
            patient_write.WriteUncertain(),
            patient_write.WriteLocked(),
        ):
            with self.subTest(error=type(error).__name__):
                fake = gateway()
                fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = error
                with self.assertRaises(Denied):
                    appointments_cancel_write.cancel_appointment(
                        fake, body(appointment_id=alias), CSRF
                    )
                self.assertEqual(fake.appointment_lock_calls, [])
                self.assertEqual(fake.cancel_calls, [])
                self.assertEqual(fake.commit_calls, 0)
                self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_target_never_consults_the_historical_ledger(self):
        fake = gateway()
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = (
            patient_write.StoreUnavailable()
        )
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(fake.appointment_receipt_calls, [])

    def test_a_patient_read_denial_under_the_row_lock_denies_and_rolls_back(self):
        fake = gateway()
        # The pre-lock check, then the authoritative in-lock one.
        fake.patient_read_sequence = [True, False]
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.cancel_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.write_responses, [])

    def test_wrong_role_is_refused_before_any_native_call(self):
        fake = gateway(current_session=session_view(user=ACTOR, roles=("Nurse",)))
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.cancel_calls, [])

    def test_missing_or_invalid_reason_is_a_validation_refusal(self):
        for reason in (None, "", "  ", "x" * 201, 7):
            with self.subTest(reason=reason):
                fake = gateway()
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(reason=reason), CSRF
                )
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[0][0], 422)
                self.assertEqual(fake.cancel_calls, [])
                self.assertEqual(fake.appointment_lock_calls, [])

    def test_unknown_body_fields_are_refused(self):
        for extra in ({"status": "Cancelled"}, {"native_appointment_id": "X"}):
            with self.subTest(extra=extra):
                fake = gateway()
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(**extra), CSRF
                )
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.cancel_calls, [])

    def test_terminal_target_status_is_refused_before_the_lock(self):
        for status in ("Cancelled", "Closed", "Checked Out", "No Show"):
            with self.subTest(status=status):
                fake = gateway()
                fake.appointment_action_details[NATIVE_TARGET] = action_detail(
                    status=status
                )
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(), CSRF
                )
                self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
                self.assertEqual(fake.write_responses[0][0], 409)
                self.assertEqual(fake.appointment_lock_calls, [])
                self.assertEqual(fake.cancel_calls, [])

    def test_linked_invoice_target_is_refused_before_the_lock(self):
        for overrides in (
            {"invoiced": 1},
            {"ref_sales_invoice": "SINV-1"},
            {"paid_amount": 500},
            {"insurance_coverage": "COV-1"},
            {"service_request": "SR-1"},
        ):
            with self.subTest(overrides=overrides):
                fake = gateway()
                fake.appointment_action_details[NATIVE_TARGET] = action_detail(
                    **overrides
                )
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(), CSRF
                )
                self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
                self.assertEqual(fake.cancel_calls, [])

    def test_patient_mismatch_is_refused(self):
        fake = gateway()
        fake.appointment_action_details[NATIVE_TARGET] = action_detail(
            patient="PAT-2"
        )
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.cancel_calls, [])

    def test_a_static_target_is_never_writable(self):
        fake = gateway()
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(
                fake, body(appointment_id="APPT-1"), CSRF
            )
        self.assertEqual(fake.cancel_calls, [])

    def test_a_foreign_alias_without_the_actors_receipt_is_refused(self):
        foreign = build_alias(SECRET.encode(), SITE, "other@example.test", BOOK_KEY)
        fake = gateway()
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(
                fake, body(appointment_id=foreign), CSRF
            )
        self.assertEqual(fake.cancel_calls, [])

    def test_an_unknown_patient_is_denied(self):
        fake = gateway(patient_can_read=False)
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(
                fake, body(patient="PAT-9"), CSRF
            )
        self.assertEqual(fake.cancel_calls, [])

    def test_native_read_and_write_authority_are_required(self):
        for override in (
            {"appointment_can_write": False},
            {"patient_can_read": False},
        ):
            with self.subTest(override=override):
                fake = gateway(**override)
                with self.assertRaises(Denied):
                    appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
                self.assertEqual(fake.cancel_calls, [])

    def test_an_unsafe_boundary_is_denied(self):
        fake = gateway(appointment_boundary_problem="drift")
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.cancel_calls, [])

    def test_an_unreadable_historical_ledger_never_blocks_a_native_cancel(self):
        fake = gateway()
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = (
            patient_write.StoreUnavailable()
        )
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(len(fake.cancel_calls), 1)

    def test_a_native_permission_denial_before_mutation_is_a_403_without_commit(
        self,
    ):
        fake = gateway()
        fake.appointment_lock_error = NativePermissionDenied("denied")
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.cancel_calls, [])
        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_an_unrelated_permission_named_failure_at_commit_is_uncertain(self):
        fake = gateway()

        def boom():
            raise UnrelatedPermissionError("denied")

        fake.commit = boom
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_failure_after_dispatch_is_uncertain(self):
        fake = gateway()
        fake.cancel_error = RuntimeError("native failure")
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_commit_failure_is_uncertain(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        fake = gateway()
        fake.cancel_error = RuntimeError("native failure")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(appointments_cancel_write.RollbackFailed):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.write_responses, [])

    def test_missing_post_commit_detail_is_uncertain_without_a_latch(self):
        fake = gateway()
        fake.appointment_detail_map.clear()
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.strip_calls, 1)


class NoShowTests(unittest.TestCase):
    def test_records_a_manual_no_show(self):
        fake = gateway()
        payload = appointments_cancel_write.record_no_show(fake, body(), CSRF)

        self.assertEqual(payload["status"], "no_show")
        self.assertEqual(set(payload), {"status", "appointment"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(len(fake.no_show_calls), 1)
        self.assertEqual(fake.cancel_calls, [])
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.appointment_ack_calls, [])

    def test_a_checked_in_target_is_not_no_show_eligible(self):
        fake = gateway()
        fake.appointment_action_details[NATIVE_TARGET] = action_detail(
            status="Checked In"
        )
        payload = appointments_cancel_write.record_no_show(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.no_show_calls, [])

    def test_a_native_failure_is_uncertain(self):
        fake = gateway()
        fake.no_show_error = RuntimeError("native failure")
        payload = appointments_cancel_write.record_no_show(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)


class WriteGateTests(unittest.TestCase):
    def test_transitions_are_default_off(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_appointment_write_enabled=0)
        )
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.cancel_calls, [])

    def test_csrf_is_required(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                with self.assertRaises(Denied):
                    appointments_cancel_write.cancel_appointment(
                        fake, body(), supplied
                    )
                self.assertEqual(fake.cancel_calls, [])

    def test_the_historical_fence_and_deployment_marker_do_not_gate(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False
        fake.fence_error = patient_write.StoreUnavailable()

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_another_site_is_refused(self):
        fake = gateway(
            site="marley.local",
            settings=write_settings(clinic_ui_synthetic_site="marley.local"),
        )
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.cancel_calls, [])


if __name__ == "__main__":
    unittest.main()
