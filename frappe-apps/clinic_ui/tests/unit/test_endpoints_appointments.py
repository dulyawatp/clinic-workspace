"""Endpoint tests for ``clinic_ui.api.appointments.synthetic_appointments``.

The endpoint takes no client input. The site-owned appointment allowlist is the
only source; each native read is a current-user per-document and field-level
check in the gateway. Tests use a fake gateway only, so they prove gate order,
alias mapping and fail-closed behaviour, not real Frappe permission semantics.
"""

import inspect
import unittest
from decimal import Decimal
from unittest.mock import patch

from clinic_ui.core import appointment_write, config, patient_write
from clinic_ui.core.appointment_write import build_alias
from clinic_ui.endpoints import appointments as appointments_endpoint

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
BOOK_KEY = "appt-book-key-0001-012345"
OTHER_KEY = "appt-book-key-0002-012345"

PATIENTS = [
    {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
    {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
]
APPOINTMENTS = [
    {
        "workspace_appointment_id": "APPT-1",
        "native_appointment_id": "NATIVE-APPT-1",
    },
    {
        "workspace_appointment_id": "APPT-2",
        "native_appointment_id": "NATIVE-APPT-2",
    },
]
DETAILS = {
    "NATIVE-APPT-1": {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Open",
    },
    "NATIVE-APPT-2": {
        "patient": "PAT-2",
        "appointment_date": "2026-11-20",
        "appointment_time": "10:00:00",
        "status": "Checked In",
    },
}


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": settings(
            clinic_ui_synthetic_site=SITE,
            clinic_ui_synthetic_patients=PATIENTS,
            clinic_ui_synthetic_appointments=APPOINTMENTS,
            clinic_ui_native_appointment_write_enabled=1,
            clinic_ui_native_appointment_write_site=SITE,
            clinic_ui_appointment_medical_department="MEDDEP-1",
            clinic_ui_appointment_company="COMP-1",
            clinic_ui_appointment_type="APPT-TYPE-1",
            clinic_ui_appointment_checkin_patient="WS-2",
        ),
        "current_session": session_view(roles=("Clinic Reception",)),
    }
    values.update(overrides)
    fake = FakeGateway(**values)
    fake.appointment_view_detail = {
        native: dict(detail) for native, detail in DETAILS.items()
    }
    return fake


class AppointmentSuccessTests(unittest.TestCase):
    def test_visit_invoice_reference_requires_exact_owned_native_context(self):
        fake = gateway(current_session=session_view(roles=("Physician",)))
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1", "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00", "status": "Open",
            "modified": "2026-11-14 09:30:00.123456",
        }
        evidence = {
            "metadata_verified": True, "record_fields_readable": True,
            "billing_history_complete": True, "prior_billing": False,
            "appointment_id": "NATIVE-APPT-3", "practitioner": "DOC-1",
            "actor_practitioner": "DOC-1", "status": "Open", "docstatus": 0,
            "encounter_id": None, "invoiced": 0, "ref_sales_invoice": None,
            "patient": "PAT-1", "customer": "CUST-1", "company": "COMP-1",
            "currency": "THB", "service": "ITEM-1", "price": Decimal("100"),
        }
        fake.visit_invoice_evidence = lambda _: evidence
        with patch("clinic_ui.endpoints.appointments.lookup_visit_invoice", create=True, return_value=None):
            payload = appointments_endpoint.appointment_detail(fake, "NATIVE-APPT-3")
            self.assertEqual(payload["visit_invoice_appointment_id"], "NATIVE-APPT-3")
            evidence["actor_practitioner"] = "DOC-2"
            payload = appointments_endpoint.appointment_detail(fake, "NATIVE-APPT-3")
            self.assertNotIn("visit_invoice_appointment_id", payload)
            evidence["actor_practitioner"] = "DOC-1"
            evidence["metadata_verified"] = False
            payload = appointments_endpoint.appointment_detail(fake, "NATIVE-APPT-3")
            self.assertNotIn("visit_invoice_appointment_id", payload)

    def test_visit_invoice_reference_allows_verified_submitted_read_only_context(self):
        fake = gateway(current_session=session_view(roles=("Physician",)))
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1", "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00", "status": "Closed",
            "modified": "2026-11-14 09:30:00.123456",
        }
        with patch("clinic_ui.endpoints.appointments.lookup_visit_invoice", create=True, return_value={"invoice_id": "INV-1"}):
            payload = appointments_endpoint.appointment_detail(fake, "NATIVE-APPT-3")
        self.assertEqual(payload["visit_invoice_appointment_id"], "NATIVE-APPT-3")

    def test_visit_invoice_reference_never_exposes_static_alias_or_native_mapping(self):
        fake = gateway(current_session=session_view(roles=("Physician",)))
        fake.appointment_detail_map["NATIVE-APPT-1"] = {
            **DETAILS["NATIVE-APPT-1"], "modified": "2026-11-14 09:30:00.123456",
        }
        with patch("clinic_ui.endpoints.appointments.lookup_visit_invoice", create=True, return_value={"invoice_id": "INV-1"}):
            for target in ("APPT-1", "NATIVE-APPT-1"):
                with self.subTest(target=target):
                    payload = appointments_endpoint.appointment_detail(fake, target)
                    self.assertNotIn("visit_invoice_appointment_id", payload)

    def test_visit_invoice_reference_omitted_for_booked_alias_and_reception(self):
        fake = gateway(current_session=session_view(user=ACTOR, roles=("Physician",)))
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake.appointment_receipt_records.append(FakeAppointmentReceipt(
            "book", ACTOR, BOOK_KEY,
            appointment_write.encode_receipt(
                kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
            ),
        ))
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1", "appointment_date": "2026-11-20",
            "appointment_time": "09:30:00", "status": "Open",
            "modified": "2026-11-14 09:30:00.123456",
        }
        with patch("clinic_ui.endpoints.appointments.lookup_visit_invoice", create=True, return_value={"invoice_id": "INV-1"}):
            self.assertNotIn("visit_invoice_appointment_id",
                             appointments_endpoint.appointment_detail(fake, alias))
            fake.current_session = session_view(user=ACTOR, roles=("Clinic Reception",))
            self.assertNotIn("visit_invoice_appointment_id",
                             appointments_endpoint.appointment_detail(fake, "NATIVE-APPT-3"))

    def test_returns_the_five_field_rows_for_reception(self):
        fake = gateway()
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "workspace_patient_id": "WS-1",
                        "appointment_id": "APPT-1",
                        "appointment_date": "2026-11-20",
                        "appointment_time": "09:30:00",
                        "status": "Open",
                    },
                    {
                        "workspace_patient_id": "WS-2",
                        "appointment_id": "APPT-2",
                        "appointment_date": "2026-11-20",
                        "appointment_time": "10:00:00",
                        "status": "Checked In",
                    },
                ]
            },
        )
        self.assertEqual(
            fake.appointment_views, ["NATIVE-APPT-1", "NATIVE-APPT-2"]
        )

    def test_doctor_can_read_the_same_list(self):
        fake = gateway(current_session=session_view(roles=("Physician",)))
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(len(payload["items"]), 2)

    def test_native_patient_and_appointment_ids_never_leave_the_server(self):
        payload = appointments_endpoint.synthetic_appointments(gateway())
        text = repr(payload)
        for native in ("PAT-1", "PAT-2", "NATIVE-APPT-1", "NATIVE-APPT-2"):
            self.assertNotIn(native, text)

    def test_an_appointment_whose_patient_the_user_cannot_read_is_omitted(self):
        fake = gateway()
        fake.patient_read_authority = {"PAT-2"}
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(
            [item["appointment_id"] for item in payload["items"]],
            ["APPT-2"],
        )
        self.assertEqual(fake.patient_read_calls[0], "PAT-1")

    def test_an_unreadable_booked_or_native_patient_is_omitted_not_a_denial(self):
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        fake.patient_read_authority = {"PAT-1", "PAT-2"}
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book",
                ACTOR,
                BOOK_KEY,
                appointment_write.encode_receipt(
                    kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
                ),
            )
        )
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "REAL-PATIENT-9",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
        }
        fake.readable_appointments = ["NATIVE-APPT-4"]
        fake.appointment_view_detail["NATIVE-APPT-4"] = {
            "patient": "REAL-PATIENT-8",
            "appointment_date": "2026-11-21",
            "appointment_time": "12:00:00",
            "status": "Open",
        }

        payload = appointments_endpoint.synthetic_appointments(fake)

        self.assertEqual(
            [item["appointment_id"] for item in payload["items"]],
            ["APPT-1", "APPT-2"],
        )

    def test_a_readable_native_patient_is_published_by_its_docname(self):
        fake = gateway()
        fake.patient_read_authority = {"PAT-1", "PAT-2", "REAL-PATIENT-9"}
        fake.appointment_view_detail["NATIVE-APPT-2"] = {
            **DETAILS["NATIVE-APPT-2"],
            "patient": "REAL-PATIENT-9",
        }
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(
            [item["workspace_patient_id"] for item in payload["items"]],
            ["WS-1", "REAL-PATIENT-9"],
        )

    def test_a_blank_or_unreadable_patient_is_omitted(self):
        for patient in ("", None):
            with self.subTest(patient=patient):
                fake = gateway()
                fake.appointment_view_detail["NATIVE-APPT-2"] = {
                    **DETAILS["NATIVE-APPT-2"],
                    "patient": patient,
                }
                payload = appointments_endpoint.synthetic_appointments(fake)
                self.assertEqual(
                    [item["appointment_id"] for item in payload["items"]],
                    ["APPT-1"],
                )

    def test_empty_appointment_allowlist_returns_no_items_without_reads(self):
        fake = gateway(
            settings=settings(
                clinic_ui_synthetic_site=SITE,
                clinic_ui_synthetic_patients=PATIENTS,
                clinic_ui_synthetic_appointments=[],
            )
        )
        self.assertEqual(
            appointments_endpoint.synthetic_appointments(fake), {"items": []}
        )
        self.assertEqual(fake.appointment_views, [])

    def test_marks_the_response_no_store_and_suppresses_tracebacks(self):
        fake = gateway()
        appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)

    def test_signature_takes_the_gateway_and_nothing_else(self):
        parameters = list(
            inspect.signature(
                appointments_endpoint.synthetic_appointments
            ).parameters
        )
        self.assertEqual(parameters, ["gateway"])


class AppointmentDenialTests(unittest.TestCase):
    def assertDenied(self, fake, reads=()):
        with self.assertRaises(Denied):
            appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(len(fake.denials), 1)
        self.assertEqual(fake.no_store_calls, 1)
        self.assertGreaterEqual(fake.traceback_suppressions, 1)
        self.assertEqual(fake.appointment_views, list(reads))

    def test_unconfigured_site_is_denied_before_any_native_read(self):
        self.assertDenied(gateway(settings={}))

    def test_roles_outside_doc_and_rec_are_denied_before_any_native_read(self):
        for roles in (
            ("Nurse",),
            ("Pharmacist",),
            ("Accounts User",),
            ("Physician", "Clinic Reception"),
        ):
            with self.subTest(roles=roles):
                self.assertDenied(
                    gateway(current_session=session_view(roles=roles))
                )

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

    def test_native_read_failure_fails_the_whole_response(self):
        fake = gateway()
        fake.appointment_view_detail.pop("NATIVE-APPT-2")
        self.assertDenied(fake, reads=["NATIVE-APPT-1", "NATIVE-APPT-2"])

    def test_malformed_native_detail_fails_the_whole_response(self):
        for detail in (
            {**DETAILS["NATIVE-APPT-1"], "appointment_date": "2026-02-30"},
            {**DETAILS["NATIVE-APPT-1"], "appointment_time": "25:00"},
            {**DETAILS["NATIVE-APPT-1"], "status": "<bad>"},
            {"patient": "PAT-1"},
        ):
            with self.subTest(detail=detail):
                fake = gateway()
                fake.appointment_view_detail["NATIVE-APPT-1"] = dict(detail)
                self.assertDenied(fake, reads=["NATIVE-APPT-1"])

    def test_a_later_native_failure_returns_no_partial_list(self):
        fake = gateway()
        fake.appointment_view_detail.pop("NATIVE-APPT-2")
        with self.assertRaises(Denied):
            appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(len(fake.denials), 1)


class BookedAppointmentDiscoveryTests(unittest.TestCase):
    """Verified, bounded receipt discovery for newly booked appointments."""

    def receipts(self, fake, actor, entries):
        from clinic_ui.core import appointment_write

        for key, native, alias in entries:
            receipt = appointment_write.encode_receipt(
                kind="book", alias=alias, native_appointment_id=native
            )
            fake.appointment_receipt_records.append(
                FakeAppointmentReceipt("book", actor, key, receipt)
            )

    def test_list_adds_newly_booked_rows_without_native_ids(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        self.receipts(fake, ACTOR, [(BOOK_KEY, "NATIVE-APPT-3", alias)])
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
        }
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(
            [item["appointment_id"] for item in payload["items"]],
            ["APPT-1", "APPT-2", alias],
        )
        self.assertNotIn("NATIVE-APPT-3", repr(payload))
        self.assertNotIn("PAT-1", repr(payload))
        self.assertEqual(fake.appointment_receipt_calls, [(2, "book", None)])

    def test_ambiguous_or_malformed_receipts_fail_the_whole_list(self):
        from clinic_ui.core import appointment_write

        cases = {
            "duplicate alias": [
                (
                    BOOK_KEY,
                    "NATIVE-APPT-3",
                    build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY),
                ),
                (
                    OTHER_KEY,
                    "NATIVE-APPT-4",
                    build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY),
                ),
            ],
            "duplicate native id": [
                (
                    BOOK_KEY,
                    "NATIVE-APPT-3",
                    build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY),
                ),
                (
                    OTHER_KEY,
                    "NATIVE-APPT-3",
                    build_alias(SECRET.encode(), SITE, ACTOR, OTHER_KEY),
                ),
            ],
        }
        for name, entries in cases.items():
            with self.subTest(case=name):
                fake = gateway()
                self.receipts(fake, ACTOR, entries)
                for native in ("NATIVE-APPT-3", "NATIVE-APPT-4"):
                    fake.appointment_view_detail[native] = {
                        "patient": "PAT-1",
                        "appointment_date": "2026-11-21",
                        "appointment_time": "11:00:00",
                        "status": "Open",
                    }
                with self.assertRaises(Denied):
                    appointments_endpoint.synthetic_appointments(fake)

    def test_a_tampered_receipt_is_refused(self):
        fake = gateway()
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book", ACTOR, BOOK_KEY, '{"codec":"patient"}'
            )
        )
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
        }
        with self.assertRaises(Denied):
            appointments_endpoint.synthetic_appointments(fake)

    def test_a_receipt_alias_for_another_actor_is_refused(self):
        fake = gateway()
        alias = build_alias(SECRET.encode(), SITE, "other@example.test", BOOK_KEY)
        self.receipts(fake, ACTOR, [(BOOK_KEY, "NATIVE-APPT-3", alias)])
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
        }
        with self.assertRaises(Denied):
            appointments_endpoint.synthetic_appointments(fake)

    def test_a_booked_record_for_an_unallowlisted_patient_uses_its_docname(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        self.receipts(fake, ACTOR, [(BOOK_KEY, "NATIVE-APPT-3", alias)])
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "REAL-PATIENT-9",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
        }
        payload = appointments_endpoint.synthetic_appointments(fake)
        rows = {item["appointment_id"]: item for item in payload["items"]}
        self.assertEqual(rows[alias]["workspace_patient_id"], "REAL-PATIENT-9")


    def test_an_unavailable_fence_keeps_the_static_read_contract(self):
        fake = gateway()
        fake.appointment_receipt_list_error = patient_write.StoreUnavailable()
        payload = appointments_endpoint.synthetic_appointments(fake)
        self.assertEqual(
            [item["appointment_id"] for item in payload["items"]],
            ["APPT-1", "APPT-2"],
        )

    def test_an_over_full_discovery_page_denies(self):
        fake = gateway()
        for index, key in enumerate(
            (BOOK_KEY, OTHER_KEY, "appt-book-key-0003-012345")
        ):
            alias = build_alias(SECRET.encode(), SITE, ACTOR, key)
            fake.appointment_receipt_records.append(
                FakeAppointmentReceipt(
                    "book",
                    ACTOR,
                    key,
                    appointment_write.encode_receipt(
                        kind="book",
                        alias=alias,
                        native_appointment_id=f"NATIVE-APPT-{index + 3}",
                    ),
                )
            )
            fake.appointment_view_detail[f"NATIVE-APPT-{index + 3}"] = {
                "patient": "PAT-1",
                "appointment_date": "2026-11-21",
                "appointment_time": "11:00:00",
                "status": "Open",
            }
        with self.assertRaises(Denied):
            appointments_endpoint.synthetic_appointments(fake)


class AppointmentDetailTests(unittest.TestCase):
    def test_an_unreadable_patient_fails_the_detail_closed(self):
        fake = gateway()
        fake.appointment_detail_map["NATIVE-APPT-1"] = {
            **DETAILS["NATIVE-APPT-1"],
            "modified": "2026-11-14 09:30:00.123456",
        }
        fake.patient_read_authority = set()
        with self.assertRaises(Denied):
            appointments_endpoint.appointment_detail(fake, "APPT-1")

    def test_static_fixture_detail_is_versioned_and_read_only(self):
        fake = gateway()
        fake.appointment_detail_map["NATIVE-APPT-1"] = {
            **DETAILS["NATIVE-APPT-1"],
            "modified": "2026-11-14 09:30:00.123456",
        }
        payload = appointments_endpoint.appointment_detail(fake, "APPT-1")
        self.assertEqual(
            payload,
            {
                "workspace_patient_id": "WS-1",
                "appointment_id": "APPT-1",
                "appointment_date": "2026-11-20",
                "appointment_time": "09:30:00",
                "status": "Open",
                "modified": "2026-11-14 09:30:00.123456",
                "can_reschedule": False,
                "can_checkin": False,
            },
        )
        self.assertNotIn("NATIVE-APPT-1", repr(payload))
        self.assertNotIn("PAT-1", repr(payload))

    def test_booked_detail_exposes_actions_only_to_its_creator(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book",
                ACTOR,
                BOOK_KEY,
                appointment_write.encode_receipt(
                    kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
                ),
            )
        )
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-21",
            "appointment_time": "11:00:00",
            "status": "Open",
            "modified": "2026-11-14 09:30:00.123456",
        }
        own = appointments_endpoint.appointment_detail(fake, alias)
        self.assertTrue(own["can_reschedule"])
        self.assertFalse(own["can_checkin"])
        # The non-mutating target check receives the resolved target's own
        # patient, plus the exact configured company/type/department.
        self.assertIn(
            (
                "NATIVE-APPT-3",
                {
                    "patient": "PAT-1",
                    "company": "COMP-1",
                    "appointment_type": "APPT-TYPE-1",
                    "department": "MEDDEP-1",
                },
            ),
            fake.appointment_target_problem_calls,
        )

        other = gateway(
            current_session=session_view(
                user="other@example.test", roles=("Clinic Reception",)
            )
        )
        other.appointment_receipt_records = fake.appointment_receipt_records
        other.appointment_detail_map = fake.appointment_detail_map
        payload = appointments_endpoint.appointment_detail(other, alias)
        self.assertFalse(payload["can_reschedule"])
        self.assertFalse(payload["can_checkin"])

    def test_a_future_scheduled_booking_is_reschedulable(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book",
                ACTOR,
                BOOK_KEY,
                appointment_write.encode_receipt(
                    kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
                ),
            )
        )
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "11:00:00",
            "status": "Scheduled",
            "modified": "2026-11-14 09:30:00.123456",
        }
        payload = appointments_endpoint.appointment_detail(fake, alias)
        self.assertTrue(payload["can_reschedule"])
        self.assertFalse(payload["can_checkin"])

    def test_action_flags_intersect_every_effective_gate(self):
        cases = {
            "write flag off": {"settings": settings(
                clinic_ui_synthetic_site=SITE,
                clinic_ui_synthetic_patients=PATIENTS,
                clinic_ui_synthetic_appointments=APPOINTMENTS,
                clinic_ui_native_appointment_write_enabled=0,
            )},
            "unsafe controller settings": {"appointment_side_effects_unsafe": True},
            "no write authority": {"appointment_can_write": False},
            "no linked read": {"doctype_reads": {"Company": False}},
            "no readable type duration": {"appointment_type_duration": None},
            "unreviewed type mode": {
                "appointment_type_contract": {
                    "appointment_type": "APPT-TYPE-1",
                    "default_duration": 15,
                    "allow_booking_for": "Practitioner",
                }
            },
            "boundary drift": {"appointment_boundary_problem": "drift"},
            "populated closed-world target": {
                "appointment_target_problems": {"NATIVE-APPT-3": "notes populated"}
            },
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                init = {}
                if "settings" in override:
                    init["settings"] = override["settings"]
                fake = gateway(
                    current_session=session_view(
                        user=ACTOR, roles=("Clinic Reception",)
                    ),
                    **init,
                )
                alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
                fake.appointment_receipt_records.append(
                    FakeAppointmentReceipt(
                        "book",
                        ACTOR,
                        BOOK_KEY,
                        appointment_write.encode_receipt(
                            kind="book",
                            alias=alias,
                            native_appointment_id="NATIVE-APPT-3",
                        ),
                    )
                )
                fake.appointment_detail_map["NATIVE-APPT-3"] = {
                    "patient": "PAT-2",
                    "appointment_date": fake.site_today_value,
                    "appointment_time": "08:00:00",
                    "status": "Open",
                    "modified": "2026-11-14 09:30:00.123456",
                }
                fake.appointment_view_detail["NATIVE-APPT-3"] = {
                    "patient": "PAT-2",
                    "appointment_date": fake.site_today_value,
                    "appointment_time": "08:00:00",
                    "status": "Open",
                }
                for key, value in override.items():
                    if key == "settings":
                        continue
                    setattr(fake, key, value)
                payload = appointments_endpoint.appointment_detail(fake, alias)
                self.assertFalse(payload["can_reschedule"])
                self.assertFalse(payload["can_checkin"])

    def test_action_flags_ignore_the_historical_fence_and_deployment_marker(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        fake.deployment_approved = False
        fake.fence_ready = False
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book",
                ACTOR,
                BOOK_KEY,
                appointment_write.encode_receipt(
                    kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
                ),
            )
        )
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
            "status": "Open",
            "modified": "2026-11-14 09:30:00.123456",
        }
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
            "status": "Open",
        }
        payload = appointments_endpoint.appointment_detail(fake, alias)
        self.assertTrue(payload["can_reschedule"])

    def test_dedicated_same_day_record_is_the_only_checkin_action(self):
        fake = gateway(
            current_session=session_view(user=ACTOR, roles=("Clinic Reception",))
        )
        alias = build_alias(SECRET.encode(), SITE, ACTOR, BOOK_KEY)
        fake.appointment_receipt_records.append(
            FakeAppointmentReceipt(
                "book",
                ACTOR,
                BOOK_KEY,
                appointment_write.encode_receipt(
                    kind="book", alias=alias, native_appointment_id="NATIVE-APPT-3"
                ),
            )
        )
        fake.appointment_detail_map["NATIVE-APPT-3"] = {
            "patient": "PAT-2",
            "appointment_date": fake.site_today_value,
            "appointment_time": "08:00:00",
            "status": "Confirmed",
            "modified": "2026-11-14 09:30:00.123456",
        }
        fake.appointment_view_detail["NATIVE-APPT-3"] = {
            "patient": "PAT-2",
            "appointment_date": fake.site_today_value,
            "appointment_time": "08:00:00",
            "status": "Confirmed",
        }
        payload = appointments_endpoint.appointment_detail(fake, alias)
        self.assertFalse(payload["can_reschedule"])
        self.assertTrue(payload["can_checkin"])

    def test_detail_requires_patient_read_authority(self):
        fake = gateway()
        fake.appointment_detail_map["NATIVE-APPT-1"] = {
            **DETAILS["NATIVE-APPT-1"],
            "modified": "2026-11-14 09:30:00.123456",
        }
        fake.patient_can_read = False
        with self.assertRaises(Denied):
            appointments_endpoint.appointment_detail(fake, "APPT-1")

    def test_unknown_malformed_or_native_alias_is_denied(self):
        for alias in (None, "", "NATIVE-APPT-1", "PAT-1", "wsa1_" + "a" * 32):
            with self.subTest(alias=alias):
                fake = gateway()
                with self.assertRaises(Denied):
                    appointments_endpoint.appointment_detail(fake, alias)

    def test_malformed_native_detail_fails_closed(self):
        fake = gateway()
        fake.appointment_detail_map["NATIVE-APPT-1"] = {
            **DETAILS["NATIVE-APPT-1"],
            "modified": "not-a-date",
        }
        with self.assertRaises(Denied):
            appointments_endpoint.appointment_detail(fake, "APPT-1")


if __name__ == "__main__":
    unittest.main()
