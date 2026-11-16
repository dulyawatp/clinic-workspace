"""Endpoint tests for local booking, rescheduling and same-day check-in.

The fake gateway proves gate ordering, strict bodies, durable budget/target
decisions, fence-before-native ordering, commit/receipt/acknowledgement
handling and fail-closed denials. It does not claim native permission
semantics; the adapter tests cover the locked native calls.
"""

import unittest

from clinic_ui.core import appointment_write, config, patient_write
from clinic_ui.core.appointment_write import (
    AppointmentBudgetExhausted,
    AppointmentTargetNotEligible,
    build_alias,
)
from clinic_ui.endpoints import appointments_write

from .fake_gateway import (
    Denied,
    FakeAppointmentReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "reception@example.test"
CSRF = "csrf-token-value"
BOOK_KEY = "appt-book-key-0001-012345"
SECOND_BOOK_KEY = "appt-book-key-0002-012345"
RESCHEDULE_KEY = "appt-resched-key-0001-0123"
CHECKIN_KEY = "appt-checkin-key-0001-0012"
MODIFIED = "2026-11-14 09:30:00.123456"
TODAY = "2026-11-14"


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
    fake.appointment_detail_map["NATIVE-APPT-TODAY"] = {
        "patient": "PAT-2",
        "appointment_date": TODAY,
        "appointment_time": "08:00:00",
        "status": "Open",
        "modified": MODIFIED,
    }
    return fake


def alias_for(key):
    return build_alias(SECRET.encode(), SITE, ACTOR, key)


def book_receipt(key, native_id):
    return appointment_write.encode_receipt(
        kind="book", alias=alias_for(key), native_appointment_id=native_id
    )


def committed(fake, kind, key, native_id, actor=ACTOR):
    fake.appointment_receipt_records.append(
        FakeAppointmentReceipt(
            kind,
            actor,
            key,
            appointment_write.encode_receipt(
                kind=kind,
                alias=alias_for(key),
                native_appointment_id=native_id,
            ),
        )
    )


def book_body(**overrides):
    values = {
        "operation_key": BOOK_KEY,
        "workspace_patient_id": "WS-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30",
    }
    values.update(overrides)
    return values


def reschedule_body(**overrides):
    values = {
        "operation_key": RESCHEDULE_KEY,
        "workspace_appointment_id": alias_for(BOOK_KEY),
        "base_modified": MODIFIED,
        "appointment_date": "2026-11-21",
        "appointment_time": "10:15",
    }
    values.update(overrides)
    return values


def checkin_body(**overrides):
    values = {
        "operation_key": CHECKIN_KEY,
        "workspace_appointment_id": alias_for(BOOK_KEY),
        "base_modified": MODIFIED,
    }
    values.update(overrides)
    return values


def ack_body(**overrides):
    values = {
        "operation_key": BOOK_KEY,
        "kind": "book",
        "ack_token": "a" * 64,
    }
    values.update(overrides)
    return values


class BookingTests(unittest.TestCase):
    def test_books_with_server_owned_targets_and_returns_a_versioned_row(self):
        fake = gateway()
        payload = appointments_write.book(fake, book_body(), CSRF)

        self.assertEqual(payload["status"], "booked")
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
        self.assertEqual(payload["appointment"]["appointment_id"], alias_for(BOOK_KEY))
        self.assertEqual(payload["appointment"]["workspace_patient_id"], "WS-1")
        self.assertEqual(payload["appointment"]["modified"], MODIFIED)
        self.assertEqual(fake.prepare_appointment_calls, [{
            "patient": "PAT-1",
            "company": "COMP-1",
            "department": "MEDDEP-1",
            "appointment_type": "APPT-TYPE-1",
            "duration": 15,
            "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00",
        }])
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.appointment_fence_calls[0][1], "book")
        self.assertEqual(len(fake.appointment_fence_claim.committed), 1)
        self.assertEqual(len(payload["ackToken"]), 64)
        text = repr(payload)
        for native in ("PAT-1", "NATIVE-APPT-NEW"):
            self.assertNotIn(native, text)

    def test_booking_checks_every_required_native_read_and_create_authority(self):
        fake = gateway()
        appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.patient_read_calls, ["PAT-1", "PAT-1"])
        self.assertEqual(
            fake.doctype_read_calls,
            [
                ("Company", "COMP-1"),
                ("Appointment Type", "APPT-TYPE-1"),
                ("Medical Department", "MEDDEP-1"),
                ("Gender", None),
                # Repeated after the fence, against current authority.
                ("Company", "COMP-1"),
                ("Appointment Type", "APPT-TYPE-1"),
                ("Medical Department", "MEDDEP-1"),
                ("Gender", None),
            ],
        )

    def test_denials_happen_before_any_fence_or_native_write(self):
        cases = {
            "patient read": {"patient_can_read": False},
            "appointment create": {"appointment_can_create": False},
            "unsafe settings": {"appointment_side_effects_unsafe": True},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                fake = gateway(**override)
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), CSRF)
                self.assertEqual(fake.appointment_fence_calls, [])
                self.assertEqual(fake.prepare_appointment_calls, [])
                self.assertEqual(fake.insert_appointment_calls, [])

    def test_linked_doctype_read_denial_denies(self):
        for doctype in (
            "Company",
            "Appointment Type",
            "Medical Department",
            "Gender",
        ):
            with self.subTest(doctype=doctype):
                fake = gateway(doctype_reads={doctype: False})
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), CSRF)
                self.assertEqual(fake.appointment_fence_calls, [])

    def test_boundary_drift_denies_before_any_read_or_write(self):
        fake = gateway(appointment_boundary_problem="healthcare source drift")
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_source_calls, [])
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_booking_locks_sources_and_revalidates_after_insert(self):
        fake = gateway()
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload["status"], "booked")
        self.assertEqual(
            fake.appointment_source_calls,
            [("PAT-1", "APPT-TYPE-1", True)],
        )
        self.assertEqual(
            fake.appointment_insert_verify_calls,
            [
                (
                    "NATIVE-APPT-NEW",
                    {
                        "patient": "PAT-1",
                        "company": "COMP-1",
                        "department": "MEDDEP-1",
                        "appointment_type": "APPT-TYPE-1",
                        "duration": 15,
                        "appointment_date": "2026-11-20",
                        "appointment_time": "09:30:00",
                        **fake.appointment_sources,
                    },
                    fake.appointment_sources,
                    TODAY,
                )
            ],
        )

    def test_booking_fences_then_resets_the_snapshot_then_locks_dependencies(self):
        fake = gateway()
        appointments_write.book(fake, book_body(), CSRF)
        order = fake.call_order
        self.assertEqual(fake.snapshot_resets, 1)
        self.assertLess(order.index("fence"), order.index("snapshot_reset"))
        self.assertLess(order.index("snapshot_reset"), order.index("sources"))
        self.assertLess(order.index("sources"), order.index("insert"))

    def test_boundary_drift_after_the_fence_denies_before_dependency_locks(self):
        fake = gateway()
        fake.appointment_boundary_problem_sequence = [None, "metadata drift"]
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.snapshot_resets, 1)
        self.assertEqual(fake.appointment_source_calls, [])
        self.assertEqual(fake.insert_appointment_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_config_change_after_the_fence_denies_before_insert(self):
        fake = gateway()
        fake.setting_sequences = {
            "clinic_ui_synthetic_patients": [
                [
                    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
                ],
                [
                    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
                ],
            ]
        }
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.appointment_source_calls, [])
        self.assertEqual(fake.insert_appointment_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_source_fetch_failure_denies_after_the_fence_before_any_write(self):
        fake = gateway(appointment_sources_error=patient_write.UnsafeSideEffect("sex"))
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.prepare_appointment_calls, [])
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.insert_appointment_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_unreviewed_appointment_type_mode_denies(self):
        fake = gateway(
            appointment_type_contract={
                "appointment_type": "APPT-TYPE-1",
                "default_duration": 15,
                "allow_booking_for": "Practitioner",
            }
        )
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_post_insert_revalidation_failure_is_unknown_and_rolls_back(self):
        fake = gateway(
            appointment_insert_verify_error=patient_write.WriteUncertain()
        )
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)
        self.assertEqual(len(fake.appointment_fence_claim.committed), 0)

    def test_a_missing_positive_type_duration_denies(self):
        for duration in (None, 0, -5):
            with self.subTest(duration=duration):
                fake = gateway(appointment_type_duration=duration)
                with self.assertRaises(Denied):
                    appointments_write.book(fake, book_body(), CSRF)
                self.assertEqual(fake.prepare_appointment_calls, [])
                self.assertEqual(fake.appointment_fence_calls, [])

    def test_the_prepared_document_side_effect_guard_denies_before_any_write(self):
        fake = gateway()
        fake.prepare_appointment_error = patient_write.UnsafeSideEffect("practitioner")
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.insert_appointment_calls, [])
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_unallowlisted_patient_is_denied(self):
        fake = gateway()
        with self.assertRaises(Denied):
            appointments_write.book(
                fake, book_body(workspace_patient_id="WS-9"), CSRF
            )
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_past_or_malformed_date_is_refused(self):
        for body in (
            book_body(appointment_date="2026-11-13"),
            book_body(appointment_date="2026-02-30"),
            book_body(appointment_time="25:00"),
        ):
            with self.subTest(body=body):
                fake = gateway()
                payload = appointments_write.book(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.appointment_fence_calls, [])
                self.assertEqual(fake.insert_appointment_calls, [])

    def test_strict_bodies_never_reach_the_fence(self):
        for body in (
            book_body(patient="PAT-1"),
            book_body(department="MEDDEP-1"),
            book_body(company="COMP-1"),
            book_body(duration=15),
            book_body(status="Open"),
            book_body(practitioner="PRACT-1"),
            book_body(native_appointment_id="NATIVE-APPT-1"),
            book_body(operation_key="short"),
        ):
            with self.subTest(body=body):
                fake = gateway()
                payload = appointments_write.book(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.appointment_fence_calls, [])

    def test_an_alias_matching_a_static_workspace_id_is_a_conflict(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_synthetic_appointments=[
                    {
                        "workspace_appointment_id": alias_for(SECOND_BOOK_KEY),
                        "native_appointment_id": "NATIVE-APPT-1",
                    },
                ]
            )
        )
        payload = appointments_write.book(
            fake, book_body(operation_key=SECOND_BOOK_KEY), CSRF
        )
        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_budget_allows_two_bookings_and_refuses_the_third(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-A")
        committed(fake, "book", SECOND_BOOK_KEY, "NATIVE-APPT-B", actor="other@example.test")
        payload = appointments_write.book(
            fake, book_body(operation_key="appt-book-key-0003-012345"), CSRF
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.insert_appointment_calls, [])

    def test_a_receipt_failure_after_commit_keeps_the_key_locked(self):
        fake = gateway()
        fake.created_appointment_id = "X" * 141
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.committed), 0)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)

    def test_commit_failure_marks_unknown_and_rolls_back(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit failed")

        fake.commit = boom
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_rollback_failure_propagates_and_keeps_the_lock(self):
        fake = gateway()

        def commit_boom():
            raise RuntimeError("commit failed")

        def rollback_boom():
            raise RuntimeError("rollback failed")

        fake.commit = commit_boom
        fake.rollback = rollback_boom
        with self.assertRaises(appointments_write.RollbackFailed):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)

    def test_a_post_commit_response_failure_latches_the_deployment(self):
        fake = gateway()
        fake.appointment_detail_map.pop("NATIVE-APPT-NEW")
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.latch_calls, [
            "post-commit booked response construction failed"
        ])

    def test_latch_failure_falls_back_to_the_operation_unknown(self):
        fake = gateway()
        fake.appointment_detail_map.pop("NATIVE-APPT-NEW")

        def latch_boom(reason):
            raise RuntimeError("latch unavailable")

        fake.latch_manual_investigation = latch_boom
        payload = appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.operation_latch_calls), 1)

    def test_both_latch_failures_escape_instead_of_returning_a_response(self):
        fake = gateway()
        fake.appointment_detail_map.pop("NATIVE-APPT-NEW")

        def boom(*args, **kwargs):
            raise RuntimeError("latch unavailable")

        fake.latch_manual_investigation = boom
        fake.latch_operation_uncertain = boom
        with self.assertRaises(appointments_write.ManualInvestigationRequired):
            appointments_write.book(fake, book_body(), CSRF)


class WriteGateTests(unittest.TestCase):
    def test_appointment_writes_are_default_off(self):
        fake = gateway(settings=write_settings(
            clinic_ui_native_appointment_write_enabled=0
        ))
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_csrf_and_deployment_marker_are_required(self):
        fake = gateway()
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), None)
        fake = gateway()
        fake.deployment_approved = False
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_role_outside_reception_and_doctor_is_denied(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Nurse",))
        )
        with self.assertRaises(Denied):
            appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])


class RescheduleTests(unittest.TestCase):
    def fake_with_booking(self, **overrides):
        fake = gateway(**overrides)
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-NEW")
        fake.appointment_modified["NATIVE-APPT-NEW"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-NEW"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00",
            "status": "Open",
        }
        fake.appointment_locked["NATIVE-APPT-NEW"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00",
            "status": "Open",
        }
        return fake

    def test_reschedules_only_date_and_time(self):
        fake = self.fake_with_booking()
        payload = appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(payload["status"], "rescheduled")
        self.assertEqual(
            fake.appointment_lock_calls,
            [
                (
                    "NATIVE-APPT-NEW",
                    MODIFIED,
                    {
                        "patient": None,
                        "patients": ("PAT-1", "PAT-2"),
                        "company": "COMP-1",
                        "appointment_type": "APPT-TYPE-1",
                        "department": "MEDDEP-1",
                    },
                )
            ],
        )
        self.assertEqual(
            [
                (state.patient, fields)
                for state, fields, _today in fake.save_reschedule_calls
            ],
            [
                (
                    "PAT-1",
                    {
                        "appointment_date": "2026-11-21",
                        "appointment_time": "10:15:00",
                    },
                )
            ],
        )
        self.assertEqual(fake.appointment_fence_calls[0][1], "reschedule")
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(payload["ackToken"]), 64)

    def test_the_existing_static_fixture_is_never_writable(self):
        fake = gateway()
        fake.appointment_modified["NATIVE-APPT-1"] = MODIFIED
        body = reschedule_body(workspace_appointment_id="APPT-1")
        self.assertEqual(
            appointments_write.reschedule(fake, body, CSRF),
            {"code": "VALIDATION"},
        )
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_an_alias_without_a_committed_booking_receipt_is_refused(self):
        fake = gateway()
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = patient_write.WriteLocked()
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_another_actors_booking_alias_is_refused(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-NEW", actor="other@example.test")
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_stale_version_is_refused_before_dispatch(self):
        fake = self.fake_with_booking()
        fake.appointment_modified["NATIVE-APPT-NEW"] = "2026-11-14 10:30:00.000001"
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "STALE_VERSION"},
        )
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_write_authority_and_target_safety_are_required(self):
        fake = self.fake_with_booking(
            appointment_write_authority=set()
        )
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.appointment_lock_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])

        fake = self.fake_with_booking(
            appointment_target_problems={"NATIVE-APPT-NEW": "notes populated"}
        )
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.appointment_lock_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_the_designated_checkin_record_cannot_be_rescheduled(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-TODAY")
        fake.appointment_modified["NATIVE-APPT-TODAY"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        fake.appointment_locked["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 0)

    def test_a_concurrent_native_writer_under_the_lock_is_refused(self):
        fake = self.fake_with_booking()
        fake.appointment_lock_error = patient_write.StaleVersion()
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)

    def test_boundary_drift_denies_reschedule(self):
        fake = self.fake_with_booking(
            appointment_boundary_problem="override drift"
        )
        with self.assertRaises(Denied):
            appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_destination_that_goes_past_after_midnight_is_refused(self):
        fake = self.fake_with_booking()
        fake.site_today_values = [TODAY, "2026-11-22"]
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_reschedule_uses_the_post_lock_site_date_for_the_dedicated_check(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-TODAY")
        fake.appointment_modified["NATIVE-APPT-TODAY"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": "2026-11-15",
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        fake.appointment_locked["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": "2026-11-15",
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        fake.site_today_values = [TODAY, "2026-11-15"]
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_the_durable_reschedule_budget_is_one(self):
        fake = self.fake_with_booking()
        committed(fake, "reschedule", RESCHEDULE_KEY, "NATIVE-APPT-NEW")
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.appointment_lock_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])

    def test_budget_exhaustion_inside_the_fence_does_not_mutate(self):
        fake = self.fake_with_booking()
        calls = {"count": 0}
        original = fake.list_committed_appointment_receipts

        def race(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] > 1:
                raise AppointmentBudgetExhausted()
            return original(*args, **kwargs)

        fake.list_committed_appointment_receipts = race
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.appointment_lock_calls, [])
        self.assertEqual(fake.save_reschedule_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(calls["count"], 2)


class CheckinTests(unittest.TestCase):
    def fake_with_same_day_booking(self, **overrides):
        fake = gateway(**overrides)
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-TODAY")
        fake.appointment_modified["NATIVE-APPT-TODAY"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        fake.appointment_locked["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        return fake

    def test_checks_in_the_dedicated_same_day_record(self):
        fake = self.fake_with_same_day_booking()
        payload = appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(payload["status"], "checked_in")
        self.assertEqual(
            fake.appointment_lock_calls,
            [
                (
                    "NATIVE-APPT-TODAY",
                    MODIFIED,
                    {
                        "patient": "PAT-2",
                        "patients": ("PAT-1", "PAT-2"),
                        "company": "COMP-1",
                        "appointment_type": "APPT-TYPE-1",
                        "department": "MEDDEP-1",
                    },
                )
            ],
        )
        self.assertEqual(
            [state.patient for state, today in fake.checkin_calls], ["PAT-2"]
        )
        self.assertEqual([today for _state, today in fake.checkin_calls], [TODAY])
        self.assertEqual(fake.appointment_fence_calls[0][1], "checkin")
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(payload["ackToken"]), 64)

    def test_a_static_fixture_is_never_checkable(self):
        fake = gateway()
        fake.appointment_modified["NATIVE-APPT-1"] = MODIFIED
        body = checkin_body(workspace_appointment_id="APPT-1")
        self.assertEqual(
            appointments_write.checkin(fake, body, CSRF), {"code": "VALIDATION"}
        )
        self.assertEqual(fake.checkin_calls, [])

    def test_a_record_on_another_date_is_not_checkable(self):
        fake = self.fake_with_same_day_booking()
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])

    def test_an_ambiguous_same_day_target_is_refused(self):
        fake = self.fake_with_same_day_booking()
        committed(fake, "book", SECOND_BOOK_KEY, "NATIVE-APPT-TODAY-2")
        fake.appointment_view_detail["NATIVE-APPT-TODAY-2"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "09:00:00",
            "status": "Open",
        }
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])

    def test_non_eligible_status_is_refused(self):
        fake = self.fake_with_same_day_booking()
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Checked In",
        }
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])

    def test_the_adapter_target_rejection_maps_to_a_locked_result(self):
        fake = self.fake_with_same_day_booking()
        fake.checkin_error = AppointmentTargetNotEligible()
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 0)

    def test_a_date_changed_under_the_lock_is_refused(self):
        fake = self.fake_with_same_day_booking()
        fake.appointment_locked["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_concurrent_native_writer_under_the_lock_is_refused(self):
        fake = self.fake_with_same_day_booking()
        fake.appointment_lock_error = patient_write.StaleVersion()
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)

    def test_boundary_drift_denies_checkin(self):
        fake = self.fake_with_same_day_booking(
            appointment_boundary_problem="metadata drift"
        )
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_midnight_after_the_lock_refuses_the_same_day_checkin(self):
        fake = self.fake_with_same_day_booking()
        fake.site_today_values = [TODAY, TODAY, "2026-11-15"]
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_target_problem_denies_before_the_fence(self):
        fake = self.fake_with_same_day_booking(
            appointment_target_problems={
                "NATIVE-APPT-TODAY": "notes is populated"
            }
        )
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_the_durable_checkin_budget_is_one(self):
        fake = self.fake_with_same_day_booking()
        committed(fake, "checkin", CHECKIN_KEY, "NATIVE-APPT-TODAY")
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.checkin_calls, [])

    def test_a_stale_version_is_refused(self):
        fake = self.fake_with_same_day_booking()
        fake.appointment_modified["NATIVE-APPT-TODAY"] = "2026-11-14 11:00:00.000001"
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "STALE_VERSION"},
        )
        self.assertEqual(fake.checkin_calls, [])

    def test_commit_failure_after_checkin_marks_unknown_and_rolls_back(self):
        fake = self.fake_with_same_day_booking()

        def boom():
            raise RuntimeError("commit failed")

        fake.commit = boom
        self.assertEqual(
            appointments_write.checkin(fake, checkin_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)


class AcknowledgementTests(unittest.TestCase):
    def test_acknowledges_only_the_appointment_codec(self):
        fake = gateway()
        payload = appointments_write.ack(fake, ack_body(), CSRF)
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(
            fake.appointment_ack_calls,
            [(ACTOR, "book", BOOK_KEY, "a" * 64)],
        )

    def test_invalid_or_locked_acknowledgements_stay_locked(self):
        fake = gateway()
        fake.appointment_ack_status = "invalid"
        self.assertEqual(
            appointments_write.ack(fake, ack_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        fake = gateway()
        fake.appointment_ack_error = patient_write.StoreUnavailable()
        self.assertEqual(
            appointments_write.ack(fake, ack_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )

    def test_ack_body_is_strict(self):
        for body in (
            ack_body(kind="create"),
            ack_body(kind="cancel"),
            ack_body(ack_token="short"),
            ack_body(extra=1),
        ):
            with self.subTest(body=body):
                fake = gateway()
                self.assertEqual(
                    appointments_write.ack(fake, body, CSRF),
                    {"code": "VALIDATION"},
                )
                self.assertEqual(fake.appointment_ack_calls, [])

    def test_ack_obeys_the_write_gate(self):
        fake = gateway()
        with self.assertRaises(Denied):
            appointments_write.ack(fake, ack_body(), None)
        self.assertEqual(fake.appointment_ack_calls, [])


class OrderingAndIsolationTests(unittest.TestCase):
    def test_the_fence_is_acquired_before_the_native_row_lock(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-NEW")
        fake.appointment_modified["NATIVE-APPT-NEW"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-NEW"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00",
            "status": "Open",
        }
        fake.appointment_locked["NATIVE-APPT-NEW"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00",
            "status": "Open",
        }
        order = []
        real_fence = fake.appointment_write_fence
        real_lock = fake.lock_appointment_target
        real_save = fake.save_appointment_reschedule

        def fence(*args):
            order.append("fence")
            return real_fence(*args)

        def lock(*args, **kwargs):
            order.append("native_lock")
            return real_lock(*args, **kwargs)

        def save(*args, **kwargs):
            order.append("native_save")
            return real_save(*args, **kwargs)

        fake.appointment_write_fence = fence
        fake.lock_appointment_target = lock
        fake.save_appointment_reschedule = save

        appointments_write.reschedule(fake, reschedule_body(), CSRF)
        self.assertEqual(order, ["fence", "native_lock", "native_save"])

    def test_checkin_independently_requires_patient_read(self):
        fake = gateway()
        committed(fake, "book", BOOK_KEY, "NATIVE-APPT-TODAY")
        fake.appointment_modified["NATIVE-APPT-TODAY"] = MODIFIED
        fake.appointment_view_detail["NATIVE-APPT-TODAY"] = {
            "patient": "PAT-2",
            "appointment_date": TODAY,
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        fake.patient_can_read = False
        with self.assertRaises(Denied):
            appointments_write.checkin(fake, checkin_body(), CSRF)
        self.assertEqual(fake.checkin_calls, [])
        self.assertEqual(fake.appointment_fence_calls, [])


class CrossKindIsolationTests(unittest.TestCase):
    def test_appointment_writes_never_touch_the_patient_fence(self):
        fake = gateway()
        appointments_write.book(fake, book_body(), CSRF)
        self.assertEqual(fake.fence_calls, [])
        self.assertEqual(len(fake.appointment_fence_calls), 1)
        self.assertEqual(fake.appointment_fence_calls[0][1], "book")

    def test_a_patient_receipt_is_not_a_booking_receipt(self):
        fake = gateway()
        fake.appointment_receipts[(ACTOR, "book", BOOK_KEY)] = (
            patient_write.encode_receipt(
                kind="create",
                alias="WPA-1",
                native_patient_id="PAT-1",
            )
        )
        self.assertEqual(
            appointments_write.reschedule(fake, reschedule_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(fake.save_reschedule_calls, [])


class EndpointShapeTests(unittest.TestCase):
    def test_ack_stays_available_while_cancel_does_not_exist(self):
        self.assertFalse(hasattr(appointments_write, "cancel"))
        self.assertFalse(hasattr(appointments_write, "set_status"))


if __name__ == "__main__":
    unittest.main()
