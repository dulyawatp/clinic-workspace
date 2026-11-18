"""Endpoint tests for the converted native appointment write lane.

The development lane relies on Frappe/Marley permissions, validation and
transactions only: no operation fence, no write budget, no acknowledgement and
no deployment marker. These tests assert the native gate order, permission
checks, CSRF, strict bodies, transaction cleanup and the agreed
``{"status", "appointment"}`` result. They also assert that an unresolved
historical fence can never block a converted write, while a historical sealed
alias still resolves read-only.
"""

import unittest

from clinic_ui.core import (
    appointment_write,
    config,
    patient_alias,
    patient_write,
    write_fence,
)
from clinic_ui.core.appointment_write import build_alias
from clinic_ui.endpoints import appointments_write

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


class UnrelatedPermissionError(Exception):
    """Same class *name* as the framework denial, unrelated type and module."""


UnrelatedPermissionError.__name__ = "PermissionError"
BOOK_KEY = "appt-book-key-0001-012345"
PATIENT_KEY = "pat-create-key-0001-0123"
MODIFIED = "2026-11-14 09:30:00.123456"
TODAY = "2026-11-14"

NATIVE_APPT = "NATIVE-APPT-10"
NATIVE_TODAY = "NATIVE-APPT-TODAY"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_patients=[
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
            {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
        ],
        clinic_ui_synthetic_appointments=[
            {"workspace_appointment_id": "APPT-1", "native_appointment_id": "NATIVE-APPT-1"},
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
    fake.appointment_detail_map["NATIVE-APPT-NEW"] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open",
        "modified": MODIFIED,
    }
    fake.appointment_detail_map[NATIVE_APPT] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open",
        "modified": MODIFIED,
    }
    fake.appointment_detail_map[NATIVE_TODAY] = {
        "patient": "PAT-2",
        "appointment_date": TODAY,
        "appointment_time": "08:00:00",
        "status": "Open",
        "modified": MODIFIED,
    }
    fake.appointment_view_detail = {
        key: dict(value) for key, value in fake.appointment_detail_map.items()
    }
    fake.appointment_locked[NATIVE_APPT] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "status": "Open",
    }
    fake.appointment_locked[NATIVE_TODAY] = {
        "patient": "PAT-2",
        "appointment_date": TODAY,
        "status": "Open",
        "appointment_time": "08:00:00",
    }
    _install_modified(fake)
    return fake


def _install_modified(fake):
    """Serve the per-record versions from the same fixture map."""

    fake.appointment_modified = {
        "NATIVE-APPT-NEW": MODIFIED,
        NATIVE_APPT: MODIFIED,
        NATIVE_TODAY: MODIFIED,
    }


def alias_for(key):
    return build_alias(SECRET.encode(), SITE, ACTOR, key)


def sealed_patient_alias(operation_key=PATIENT_KEY):
    return patient_alias.build_alias(SECRET.encode(), SITE, ACTOR, operation_key)


def book_body(**overrides):
    values = {
        "workspace_patient_id": "WS-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30",
    }
    values.update(overrides)
    return values


def reschedule_body(**overrides):
    values = {
        "workspace_appointment_id": NATIVE_APPT,
        "base_modified": MODIFIED,
        "appointment_date": "2026-11-21",
        "appointment_time": "10:15",
    }
    values.update(overrides)
    return values


def checkin_body(**overrides):
    values = {
        "workspace_appointment_id": NATIVE_TODAY,
        "base_modified": MODIFIED,
    }
    values.update(overrides)
    return values


class BookingTests(unittest.TestCase):
    def test_books_with_server_owned_targets_and_returns_the_native_docname(self):
        fake = gateway()
        _install_modified(fake)
        payload = appointments_write.book(fake, book_body(), CSRF)

        self.assertEqual(payload["status"], "booked")
        self.assertEqual(set(payload), {"status", "appointment"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["appointment"]["appointment_id"], "NATIVE-APPT-NEW")
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "WS-1")
        self.assertEqual(payload["appointment"]["modified"], MODIFIED)
        self.assertEqual(
            set(payload["appointment"]),
            {
                "workspace_patient_id",
                "appointment_id",
                "appointment_date",
                "appointment_time",
                "status",
                "modified",
            },
        )
        self.assertEqual(
            fake.prepare_appointment_calls,
            [
                {
                    "patient": "PAT-1",
                    "company": "COMP-1",
                    "department": "MEDDEP-1",
                    "appointment_type": "APPT-TYPE-1",
                    "duration": 15,
                    "appointment_date": "2026-11-20",
                    "appointment_time": "09:30:00",
                }
            ],
        )
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(
            fake.appointment_source_calls[0], ("PAT-1", "APPT-TYPE-1", True)
        )

    def test_the_converted_lane_never_touches_the_operation_fence(self):
        fake = gateway()
        _install_modified(fake)
        fake.deployment_approved = False
        fake.fence_ready = False
        fake.fence_error = patient_write.StoreUnavailable()
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()

        payload = appointments_write.book(fake, book_body(), CSRF)

        self.assertEqual(payload["status"], "booked")
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.appointment_receipt_calls, [])
        self.assertEqual(fake.appointment_ack_calls, [])
        self.assertEqual(fake.latch_calls, [])

    def test_books_for_a_native_patient_docname(self):
        fake = gateway()
        payload = appointments_write.book(
            fake, book_body(workspace_patient_id="PAT-1"), CSRF
        )
        self.assertEqual(payload["status"], "booked")
        # An allowlisted patient keeps its stable workspace alias in the result.
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "WS-1")
        self.assertEqual(fake.prepare_appointment_calls[0]["patient"], "PAT-1")

    def test_books_for_a_readable_native_patient_outside_the_allowlist(self):
        fake = gateway(patient_read_authority={"PAT-NEW"})
        fake.appointment_detail_map["NATIVE-APPT-NEW"]["patient"] = "PAT-NEW"
        payload = appointments_write.book(
            fake, book_body(workspace_patient_id="PAT-NEW"), CSRF
        )
        self.assertEqual(payload["status"], "booked")
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "PAT-NEW")
        self.assertEqual(fake.prepare_appointment_calls[0]["patient"], "PAT-NEW")

    def test_a_historical_patient_alias_still_books_read_only(self):
        alias = sealed_patient_alias()
        fake = gateway()
        fake.create_receipts[(ACTOR, PATIENT_KEY)] = patient_write.encode_receipt(
            kind="create", alias=alias, native_patient_id="PAT-1"
        )

        payload = appointments_write.book(
            fake, book_body(workspace_patient_id=alias), CSRF
        )

        self.assertEqual(payload["status"], "booked")
        # The allowlisted patient is published under its stable workspace alias.
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "WS-1")
        self.assertEqual(fake.prepare_appointment_calls[0]["patient"], "PAT-1")
        self.assertEqual(fake.created_receipts, [])

    def test_a_patient_the_user_cannot_read_is_denied_before_any_write(self):
        fake = gateway(patient_can_read=False)
        with self.assertRaises(Denied):
            appointments_write.book(
                fake, book_body(workspace_patient_id="PAT-UNKNOWN"), CSRF
            )
        self.assertEqual(fake.prepare_appointment_calls, [])
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_native_create_and_patient_read_authority_are_required(self):
        for override in (
            {"patient_can_read": False},
            {"appointment_can_create": False},
        ):
            with self.subTest(override=override):
                fake = gateway(**override)
                _install_modified(fake)
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), CSRF)
                self.assertEqual(fake.prepare_appointment_calls, [])
                self.assertEqual(fake.insert_appointment_calls, [])

    def test_linked_doctype_read_denial_denies_before_any_write(self):
        for doctype in (
            "Company",
            "Appointment Type",
            "Medical Department",
            "Gender",
        ):
            with self.subTest(doctype=doctype):
                fake = gateway(doctype_reads={doctype: False})
                _install_modified(fake)
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), CSRF)
                self.assertEqual(fake.insert_appointment_calls, [])

    def test_boundary_drift_and_unsafe_settings_deny_before_any_write(self):
        fake = gateway(appointment_boundary_problem="healthcare source drift")
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_source_calls, [])

        fake = gateway(appointment_side_effects_unsafe=True)
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_an_unreviewed_appointment_type_mode_denies(self):
        fake = gateway(
            appointment_type_contract={
                "appointment_type": "APPT-TYPE-1",
                "default_duration": 15,
                "allow_booking_for": "Practitioner",
            }
        )
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_a_past_destination_is_a_validation_refusal(self):
        fake = gateway()
        _install_modified(fake)
        payload = appointments_write.book(
            fake, book_body(appointment_date="2026-11-13"), CSRF
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[0][0], 422)
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_strict_body_rejections_do_not_dispatch(self):
        for body in (
            book_body(status="Open"),
            book_body(native_appointment_id="NATIVE-1"),
            book_body(operation_key="short"),
            book_body(appointment_date="20-11-2026"),
            book_body(appointment_time="9:30"),
            book_body(workspace_patient_id=""),
            book_body(workspace_patient_id=" WS-1"),
        ):
            with self.subTest(body=body):
                fake = gateway()
                _install_modified(fake)
                payload = appointments_write.book(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[0][0], 422)
                self.assertEqual(fake.insert_appointment_calls, [])
                self.assertEqual(fake.commit_calls, 0)

    def test_a_legacy_operation_key_is_accepted_and_ignored(self):
        fake = gateway()
        _install_modified(fake)
        payload = appointments_write.book(
            fake, book_body(operation_key=BOOK_KEY), CSRF
        )
        self.assertEqual(payload["status"], "booked")
        self.assertEqual(fake.commit_calls, 1)

    def test_post_insert_revalidation_failure_is_uncertain(self):
        fake = gateway(
            appointment_insert_verify_error=patient_write.WriteUncertain()
        )
        _install_modified(fake)
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_commit_failure_is_uncertain(self):
        fake = gateway()
        _install_modified(fake)

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_unrelated_permission_named_failure_at_commit_is_uncertain(self):
        fake = gateway()

        def boom():
            raise UnrelatedPermissionError("denied")

        fake.commit = boom
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_permission_failure_during_the_insert_is_uncertain(self):
        fake = gateway(
            insert_appointment_error=NativePermissionDenied("denied")
        )
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_failed_rollback_propagates(self):
        fake = gateway(insert_appointment_error=RuntimeError("native failure"))

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(appointments_write.RollbackFailed):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.write_responses, [])

    def test_missing_post_commit_detail_is_uncertain_without_a_latch(self):
        fake = gateway()
        _install_modified(fake)
        fake.appointment_detail_map.clear()
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_success_strips_message_channels(self):
        fake = gateway()
        _install_modified(fake)
        appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.strip_calls, 1)


class RescheduleTests(unittest.TestCase):
    def test_reschedules_a_native_docname(self):
        fake = gateway()
        _install_modified(fake)
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)

        self.assertEqual(payload["status"], "rescheduled")
        self.assertEqual(set(payload), {"status", "appointment"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["appointment"]["appointment_id"], NATIVE_APPT)
        self.assertEqual(len(fake.save_reschedule_calls), 1)
        state, fields, today = fake.save_reschedule_calls[0]
        self.assertEqual(
            fields,
            {"appointment_date": "2026-11-21", "appointment_time": "10:15:00"},
        )
        self.assertEqual(today, TODAY)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_reschedules_a_native_patient_outside_the_static_allowlist(self):
        fake = gateway(patient_read_authority={"PAT-NEW"})
        fake.appointment_detail_map[NATIVE_APPT]["patient"] = "PAT-NEW"
        fake.appointment_locked[NATIVE_APPT]["patient"] = "PAT-NEW"

        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)

        self.assertEqual(payload["status"], "rescheduled")
        expected = fake.appointment_lock_calls[-1][2]
        self.assertEqual(expected["patient"], "PAT-NEW")
        self.assertNotIn("patients", expected)
        self.assertEqual(len(fake.save_reschedule_calls), 1)

    def test_a_historical_receipt_failure_is_a_denial_not_an_error(self):
        for error in (
            patient_write.StoreUnavailable(),
            patient_write.WriteUncertain(),
            patient_write.WriteLocked(),
        ):
            with self.subTest(error=type(error).__name__):
                fake = gateway()
                fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = error
                with self.assertRaises(Denied):
                    appointments_write.reschedule(
                        fake,
                        reschedule_body(workspace_appointment_id=alias_for(BOOK_KEY)),
                        CSRF,
                    )
                self.assertEqual(fake.appointment_lock_calls, [])
                self.assertEqual(fake.save_reschedule_calls, [])
                self.assertEqual(fake.commit_calls, 0)
                self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_target_never_consults_the_historical_ledger(self):
        fake = gateway()
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = (
            patient_write.StoreUnavailable()
        )
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()

        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)

        self.assertEqual(payload["status"], "rescheduled")
        self.assertEqual(fake.appointment_receipt_calls, [])

    def test_a_historical_booking_alias_still_reschedules_read_only(self):
        alias = alias_for(BOOK_KEY)
        fake = gateway()
        _install_modified(fake)
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = (
            appointment_write.encode_receipt(
                kind="book", alias=alias, native_appointment_id=NATIVE_APPT
            )
        )

        payload = appointments_write.reschedule(
            fake, reschedule_body(workspace_appointment_id=alias), CSRF
        )

        self.assertEqual(payload["status"], "rescheduled")
        self.assertEqual(payload["appointment"]["appointment_id"], alias)
        self.assertEqual(len(fake.save_reschedule_calls), 1)
        # The historical read never writes the ledger.
        self.assertEqual(fake.appointment_receipt_calls, [])

    def test_the_static_fixture_is_never_writable(self):
        fake = gateway()
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.reschedule(
                fake, reschedule_body(workspace_appointment_id="APPT-1"), CSRF
            )
        self.assertEqual(fake.appointment_lock_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_an_alias_without_a_committed_booking_is_refused(self):
        fake = gateway()
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.reschedule(
                fake,
                reschedule_body(workspace_appointment_id=alias_for(BOOK_KEY)),
                CSRF,
            )
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_an_alias_is_never_reinterpreted_as_a_native_docname(self):
        fake = gateway()
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.reschedule(
                fake,
                reschedule_body(workspace_appointment_id="wsa1_" + "0" * 32 + "_" + "0" * 16 + "_" + BOOK_KEY),
                CSRF,
            )
        self.assertEqual(fake.appointment_lock_calls, [])

    def test_another_actors_booking_alias_is_refused(self):
        other = "doctor@example.test"
        alias = build_alias(SECRET.encode(), SITE, other, BOOK_KEY)
        fake = gateway()
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.reschedule(
                fake, reschedule_body(workspace_appointment_id=alias), CSRF
            )
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_native_write_authority_is_required(self):
        fake = gateway()
        _install_modified(fake)
        fake.appointment_write_authority = set()
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_a_stale_base_version_is_refused_before_the_lock(self):
        fake = gateway()
        _install_modified(fake)
        payload = appointments_write.reschedule(
            fake, reschedule_body(base_modified="2026-11-14 09:30:01.000000"), CSRF
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.appointment_lock_calls, [])

    def test_a_missing_native_version_denies(self):
        fake = gateway()
        fake.appointment_modified = {}
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)

    def test_unsafe_settings_and_target_denial_fail_closed(self):
        fake = gateway(appointment_side_effects_unsafe=True)
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)

        fake = gateway()
        _install_modified(fake)
        fake.appointment_target_problems[NATIVE_APPT] = "unreviewed link"
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_the_designated_checkin_record_cannot_be_rescheduled(self):
        fake = gateway()
        _install_modified(fake)
        fake.appointment_locked[NATIVE_TODAY] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "status": "Open",
        }
        payload = appointments_write.reschedule(
            fake, reschedule_body(workspace_appointment_id=NATIVE_TODAY), CSRF
        )
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.write_responses[0][0], 409)
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_destination_that_went_past_under_the_lock_is_refused(self):
        fake = gateway()
        _install_modified(fake)
        fake.site_today_values = [TODAY, "2026-11-22"]
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_native_stale_version_under_the_lock_is_reported(self):
        fake = gateway()
        _install_modified(fake)
        fake.appointment_lock_error = patient_write.StaleVersion()
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_failure_after_dispatch_is_uncertain(self):
        fake = gateway()
        _install_modified(fake)
        fake.save_reschedule_error = RuntimeError("native failure")
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_unrelated_permission_named_failure_at_commit_is_uncertain(self):
        fake = gateway()

        def boom():
            raise UnrelatedPermissionError("denied")

        fake.commit = boom
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_permission_denial_before_mutation_is_a_403_without_commit(
        self,
    ):
        fake = gateway()
        fake.appointment_lock_error = NativePermissionDenied("denied")
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_a_native_permission_failure_during_the_save_is_uncertain(self):
        fake = gateway()
        fake.save_reschedule_error = NativePermissionDenied("denied")
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.appointment_receipt_records, [])

    def test_failed_rollback_propagates(self):
        fake = gateway()
        _install_modified(fake)
        fake.save_reschedule_error = RuntimeError("native failure")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(appointments_write.RollbackFailed):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)

    def test_strict_body_rejections_do_not_dispatch(self):
        for body in (
            reschedule_body(appointment_date="not-a-date"),
            reschedule_body(base_modified="not-a-date"),
            reschedule_body(status="Open"),
            reschedule_body(workspace_appointment_id=""),
        ):
            with self.subTest(body=body):
                fake = gateway()
                _install_modified(fake)
                payload = appointments_write.reschedule(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.appointment_lock_calls, [])
                self.assertEqual(fake.commit_calls, 0)


class CheckinTests(unittest.TestCase):
    def test_checks_in_the_dedicated_same_day_record(self):
        fake = gateway()

        payload = appointments_write.checkin(fake, checkin_body(), CSRF)

        self.assertEqual(payload["status"], "checked_in")
        self.assertEqual(set(payload), {"status", "appointment"})
        self.assertNotIn("ackToken", payload)
        self.assertEqual(payload["appointment"]["appointment_id"], NATIVE_TODAY)
        self.assertEqual(len(fake.checkin_calls), 1)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_static_fixture_is_never_checkable(self):
        fake = gateway()
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.checkin(
                fake, checkin_body(workspace_appointment_id="APPT-1"), CSRF
            )
        self.assertEqual(fake.checkin_calls, [])

    def test_a_record_on_another_date_is_refused(self):
        fake = gateway()
        fake.appointment_detail_map[NATIVE_TODAY]["appointment_date"] = "2026-11-20"
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.checkin_calls, [])

    def test_checkin_does_not_scan_for_cross_record_uniqueness(self):
        fake = gateway()
        fake.readable_appointments = [NATIVE_TODAY, "NATIVE-APPT-TODAY-2"]
        fake.appointment_view_detail["NATIVE-APPT-TODAY-2"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "11:00:00",
            "status": "Open",
            "modified": MODIFIED,
        }

        payload = appointments_write.checkin(fake, checkin_body(), CSRF)

        # The target-scoped native lifecycle decides; no truncated page is used
        # to claim uniqueness across records.
        self.assertEqual(payload["status"], "checked_in")
        self.assertEqual(fake.readable_appointment_calls, [])
        self.assertEqual(len(fake.checkin_calls), 1)

    def test_a_second_checkin_of_the_same_record_is_refused(self):
        fake = gateway()
        fake.appointment_detail_map[NATIVE_TODAY]["status"] = "Checked In"
        fake.appointment_locked[NATIVE_TODAY]["status"] = "Checked In"
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_patient_read_denial_under_the_row_lock_denies_and_rolls_back(self):
        fake = gateway()
        fake.patient_read_sequence = [True, False]
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.write_responses, [])

    def test_a_target_for_another_patient_is_refused(self):
        fake = gateway()
        fake.appointment_detail_map[NATIVE_TODAY]["patient"] = "PAT-1"
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.checkin_calls, [])

    def test_a_date_changed_under_the_lock_is_refused(self):
        fake = gateway()
        fake.appointment_locked[NATIVE_TODAY] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "status": "Open",
        }
        fake.site_today_values = [TODAY, "2026-11-15"]
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.checkin_calls, [])

    def test_a_stale_version_is_refused(self):
        fake = gateway()
        payload = appointments_write.checkin(
            fake, checkin_body(base_modified="2026-11-14 09:30:01.000000"), CSRF
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.checkin_calls, [])

    def test_patient_read_authority_is_required(self):
        fake = gateway(patient_can_read=False)
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(fake.checkin_calls, [])

    def test_a_native_failure_after_checkin_is_uncertain(self):
        fake = gateway()
        fake.checkin_error = RuntimeError("native failure")
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_commit_failure_is_uncertain(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_permission_failure_during_checkin_is_uncertain(self):
        fake = gateway()
        fake.checkin_error = NativePermissionDenied("denied")
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_boundary_drift_denies(self):
        fake = gateway(appointment_boundary_problem="drift")
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)


class WriteGateTests(unittest.TestCase):
    def test_appointment_writes_are_default_off(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_appointment_write_enabled=0)
        )
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_missing_or_wrong_csrf_is_denied_before_dispatch(self):
        for supplied in (None, "", "wrong-token"):
            with self.subTest(supplied=supplied):
                fake = gateway()
                _install_modified(fake)
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), supplied)
                self.assertEqual(fake.insert_appointment_calls, [])

    def test_a_role_outside_reception_and_doctor_is_denied(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Nurse",))
        )
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_another_site_and_a_mismatched_write_site_are_refused(self):
        other = gateway(
            site="marley.local",
            settings=write_settings(clinic_ui_synthetic_site="marley.local"),
        )
        _install_modified(other)
        with self.assertRaises(Denied):
            appointments_write.book(other, book_body(), CSRF)
        self.assertEqual(other.insert_appointment_calls, [])

        mismatched = gateway(
            settings=write_settings(
                clinic_ui_native_appointment_write_site="marley.local"
            )
        )
        _install_modified(mismatched)
        with self.assertRaises(Denied):
            appointments_write.book(mismatched, book_body(), CSRF)
        self.assertEqual(mismatched.insert_appointment_calls, [])

    def test_the_reserved_latch_identity_cannot_write(self):
        fake = gateway(
            current_session=session_view(
                user=write_fence.LATCH_ACTOR, roles=("Clinic Reception",)
            )
        )
        _install_modified(fake)
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])


class HistoricalIsolationTests(unittest.TestCase):
    def test_an_unreadable_historical_ledger_never_blocks_a_native_write(self):
        fake = gateway()
        _install_modified(fake)
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()

        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)

        self.assertEqual(payload["status"], "rescheduled")
        self.assertEqual(len(fake.save_reschedule_calls), 1)


if __name__ == "__main__":
    unittest.main()
