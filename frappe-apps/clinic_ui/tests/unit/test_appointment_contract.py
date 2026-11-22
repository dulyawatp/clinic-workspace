"""Closed-world contract tests for the pinned Patient Appointment boundary.

These tests are pure: they exercise the reviewed field classification, fetch
expectations, shared target eligibility and the executable-boundary snapshot
comparison without a Frappe site.
"""

import copy
import unittest
from datetime import date, datetime, time, timedelta

from clinic_ui.core import appointment_contract as contract


def expected_booking(**overrides):
    values = {
        "patient": "PAT-1",
        "company": "COMP-1",
        "appointment_type": "APPT-TYPE-1",
        "department": "MEDDEP-1",
        "appointment_for": "Department",
        "duration": 15,
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "patient_name": "Patient One",
        "patient_sex": "Female",
        "inpatient_record": None,
        "emergency_record": None,
    }
    values.update(overrides)
    return values


def inserted_booking(**overrides):
    values = {
        **expected_booking(),
        "name": "HLC-APP-2026-00002",
        "naming_series": "HLC-APP-.YYYY.-",
        "status": "Scheduled",
        "title": "Patient One at MEDDEP-1",
        "patient_age": None,
        "appointment_datetime": "2026-11-20 09:30:00",
        "appointment_end_datetime": "2026-11-20 09:45:00",
        "appointment_based_on_check_in": True,
        "position_in_queue": None,
        "invoiced": 0,
        "reminded": 0,
        "add_video_conferencing": 0,
        "notes": None,
        "referring_practitioner": None,
        "source": None,
    }
    values.update(overrides)
    return values


def typed_booking(**overrides):
    """An inserted row as Frappe reads it back: Date, Time and Datetime types."""

    values = inserted_booking(
        appointment_date=date(2026, 11, 20),
        appointment_time=timedelta(hours=9, minutes=30),
        appointment_datetime=datetime(2026, 11, 20, 9, 30),
        appointment_end_datetime=datetime(2026, 11, 20, 9, 45),
    )
    values.update(overrides)
    return values


class FieldClassificationTests(unittest.TestCase):
    def test_every_reviewed_field_has_exactly_one_class(self):
        self.assertEqual(
            set(contract.APPOINTMENT_FIELD_CLASSES),
            set(contract.APPOINTMENT_FIELD_METADATA),
        )
        for name, field_class in contract.APPOINTMENT_FIELD_CLASSES.items():
            with self.subTest(field=name):
                self.assertIn(
                    field_class,
                    {
                        contract.IDENTITY,
                        contract.DESTINATION,
                        contract.DERIVED,
                        contract.DEFAULT_INERT,
                        contract.EMPTY,
                        contract.LAYOUT,
                    },
                )

    @staticmethod
    def field_dict(name, metadata):
        return {
            "fieldname": name,
            "fieldtype": metadata[0],
            "options": metadata[1],
            "fetch_from": metadata[2],
            "fetch_if_empty": bool(metadata[3]),
            "default": metadata[4],
            "permlevel": metadata[5],
            "mask": metadata[6],
            "reqd": metadata[7],
            "read_only": metadata[8],
            "hidden": metadata[9],
            "set_only_once": metadata[10],
        }

    def test_reviewed_metadata_is_accepted(self):
        fields = [
            self.field_dict(name, metadata)
            for name, metadata in contract.APPOINTMENT_FIELD_METADATA.items()
        ]
        self.assertIsNone(contract.metadata_problem(fields))

    def test_changed_default_fetch_permlevel_or_mask_is_rejected(self):
        changed = {
            "default": self.field_dict(
                "invoiced",
                contract.APPOINTMENT_FIELD_METADATA["invoiced"][:4]
                + ("1",)
                + contract.APPOINTMENT_FIELD_METADATA["invoiced"][5:],
            ),
            "fetch_from": self.field_dict(
                "duration",
                contract.APPOINTMENT_FIELD_METADATA["duration"][:2]
                + ("patient.patient_name",)
                + contract.APPOINTMENT_FIELD_METADATA["duration"][3:],
            ),
            "permlevel": self.field_dict(
                "insurance_policy",
                contract.APPOINTMENT_FIELD_METADATA["insurance_policy"][:5]
                + (1,)
                + contract.APPOINTMENT_FIELD_METADATA["insurance_policy"][6:],
            ),
            "mask": self.field_dict(
                "notes",
                contract.APPOINTMENT_FIELD_METADATA["notes"][:6]
                + (1,)
                + contract.APPOINTMENT_FIELD_METADATA["notes"][7:],
            ),
            "read_only": self.field_dict(
                "notes",
                contract.APPOINTMENT_FIELD_METADATA["notes"][:8]
                + (1,)
                + contract.APPOINTMENT_FIELD_METADATA["notes"][9:],
            ),
        }
        for name, field in changed.items():
            with self.subTest(case=name):
                self.assertIsNotNone(contract.metadata_problem([field]))

    def test_unreviewed_custom_or_removed_field_is_rejected(self):
        self.assertIsNotNone(
            contract.metadata_problem(
                [{"fieldname": "custom_clinic_flag", "fieldtype": "Data"}]
            )
        )
        self.assertIsNotNone(contract.metadata_problem([]))


class BookingValueTests(unittest.TestCase):
    @staticmethod
    def prepared(**overrides):
        values = expected_booking()
        values.pop("patient_name", None)
        values.pop("patient_sex", None)
        values.update(overrides)
        return values

    def test_prepared_values_allow_only_the_server_identity_set(self):
        # A freshly built document has no derived value yet.
        self.assertIsNone(contract.prepared_problem(self.prepared()))
        # The two fetch fields may already equal the locked source values.
        self.assertIsNone(
            contract.prepared_problem(
                expected_booking(), expected_booking()
            )
        )

    def test_prepared_values_reject_pre_populated_derived_fields(self):
        for field in (
            "status",
            "title",
            "appointment_datetime",
            "appointment_end_datetime",
            "appointment_based_on_check_in",
            "position_in_queue",
            "naming_series",
        ):
            with self.subTest(field=field):
                value = "X"
                if field == "position_in_queue":
                    value = 1
                elif field == "appointment_based_on_check_in":
                    value = True
                self.assertIsNotNone(
                    contract.prepared_problem(self.prepared(**{field: value}))
                )
        self.assertIsNotNone(
            contract.prepared_problem(
                self.prepared(patient_name="Other Patient"),
                expected_booking(),
            )
        )

    def test_prepared_values_allow_only_the_pinned_naming_series_default(self):
        # frappe.new_doc applies the DocType Select static default, so the
        # pinned series is already populated before the contract check runs.
        self.assertIsNone(
            contract.prepared_problem(
                self.prepared(naming_series=contract.PINNED_NAMING_SERIES)
            )
        )
        self.assertIsNone(
            contract.prepared_problem(
                self.prepared(naming_series=contract.PINNED_NAMING_SERIES),
                expected_booking(),
            )
        )
        for value in ("HLC-APP-.YY.-", "OTHER-SERIES-", "HLC-APP-.YYYY."):
            with self.subTest(value=value):
                self.assertIsNotNone(
                    contract.prepared_problem(self.prepared(naming_series=value))
                )

    def test_prepared_values_reject_unsupported_populated_links_and_defaults(self):
        for field in (
            "referring_practitioner",
            "source",
            "notes",
            "inpatient_record",
            "emergency_record",
            "procedure_prescription",
            "service_request",
            "insurance_policy",
            "practitioner",
            "service_unit",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(
                    contract.prepared_problem(expected_booking(**{field: "X-1"}))
                )
        self.assertIsNotNone(
            contract.prepared_problem(expected_booking(invoiced=1))
        )
        self.assertIsNotNone(
            contract.prepared_problem(expected_booking(patient_for="Department"))
        )

    def test_inserted_values_allow_only_reviewed_controller_deltas(self):
        self.assertIsNone(contract.inserted_problem(
            inserted_booking(), expected_booking(), today="2026-11-14"
        ))

    def test_inserted_values_reject_unreviewed_deltas_and_fetch_drift(self):
        cases = {
            "name": {"patient_name": "Other Patient"},
            "sex": {"patient_sex": "Male"},
            "title": {"title": "Other title"},
            "status": {"status": "Confirmed"},
            "datetime": {"appointment_datetime": "2026-11-20 10:00:00"},
            "end": {"appointment_end_datetime": "2026-11-20 09:30:00"},
            "checkin": {"appointment_based_on_check_in": False},
            "queue": {"position_in_queue": 3},
            "age": {"patient_age": "42"},
            "source": {"source": "WEB"},
            "notes": {"notes": "free text"},
            "invoice": {"invoiced": 1},
            "unknown": {"custom_clinic_flag": "x"},
        }
        for name, override in cases.items():
            with self.subTest(case=name):
                self.assertIsNotNone(
                    contract.inserted_problem(
                        inserted_booking(**override),
                        expected_booking(),
                        today="2026-11-14",
                    )
                )


class TypedReadBackTests(unittest.TestCase):
    """The DB read-back carries typed Date/Time/Datetime values, not strings."""

    def test_typed_read_back_matches_the_formatted_expectation(self):
        self.assertIsNone(
            contract.inserted_problem(
                typed_booking(), expected_booking(), today="2026-11-14"
            )
        )

    def test_every_supported_time_shape_is_canonicalized(self):
        for value in (
            "09:30:00",
            "9:30:00",
            "09:30",
            "9:30",
            timedelta(hours=9, minutes=30),
            time(9, 30),
        ):
            with self.subTest(value=value):
                self.assertIsNone(
                    contract.inserted_problem(
                        typed_booking(appointment_time=value),
                        expected_booking(),
                        today="2026-11-14",
                    )
                )

    def test_typed_read_back_still_rejects_wrong_values(self):
        cases = (
            ("appointment_date", date(2026, 11, 21), "unexpected appointment_date"),
            (
                "appointment_time",
                timedelta(hours=9, minutes=45),
                "unexpected appointment_time",
            ),
            (
                "appointment_datetime",
                datetime(2026, 11, 20, 10, 0),
                "unexpected appointment_datetime",
            ),
            (
                "appointment_end_datetime",
                datetime(2026, 11, 20, 10, 0),
                "unexpected appointment_end_datetime",
            ),
            (
                "appointment_time",
                timedelta(days=1),
                "unexpected appointment_time",
            ),
        )
        for field, value, problem_text in cases:
            with self.subTest(field=field, value=value):
                self.assertEqual(
                    contract.inserted_problem(
                        typed_booking(**{field: value}),
                        expected_booking(),
                        today="2026-11-14",
                    ),
                    problem_text,
                )

    def test_typed_target_values_are_accepted(self):
        values = typed_booking(status="Open", appointment_date=date(2026, 11, 14))
        values["appointment_datetime"] = datetime(2026, 11, 14, 9, 30)
        values["appointment_end_datetime"] = datetime(2026, 11, 14, 9, 45)
        self.assertIsNone(contract.target_problem(values))


class ConfigIdentityTests(unittest.TestCase):
    identity = {
        "patients": ("PAT-1", "PAT-2"),
        "company": "COMP-1",
        "appointment_type": "APPT-TYPE-1",
        "department": "MEDDEP-1",
    }

    def test_the_configured_identity_is_accepted(self):
        self.assertIsNone(
            contract.identity_config_problem(self.prepare(), self.identity)
        )
        self.assertIsNone(
            contract.identity_config_problem(
                self.prepare(patient="PAT-2"),
                {**self.identity, "patient": "PAT-2"},
            )
        )

    @staticmethod
    def prepare(**overrides):
        values = expected_booking(**overrides)
        values.pop("patient_name", None)
        values.pop("patient_sex", None)
        return values

    def test_any_identity_drift_from_config_is_refused(self):
        for override in (
            {"patient": "PAT-9"},
            {"company": "COMP-9"},
            {"appointment_type": "APPT-TYPE-9"},
            {"department": "MEDDEP-9"},
            {"appointment_for": "Practitioner"},
        ):
            with self.subTest(override=override):
                self.assertIsNotNone(
                    contract.identity_config_problem(
                        self.prepare(**override), self.identity
                    )
                )
        self.assertIsNotNone(
            contract.identity_config_problem(
                self.prepare(), {**self.identity, "patient": "PAT-2"}
            )
        )

    def test_a_missing_expectation_fails_closed(self):
        self.assertIsNotNone(
            contract.identity_config_problem(self.prepare(), None)
        )


class TargetValueTests(unittest.TestCase):
    def target(self, **overrides):
        values = inserted_booking(status="Open", appointment_date="2026-11-14")
        values["appointment_datetime"] = "2026-11-14 09:30:00"
        values["appointment_end_datetime"] = "2026-11-14 09:45:00"
        values.update(overrides)
        return values

    def test_reviewed_target_values_are_accepted(self):
        self.assertIsNone(contract.target_problem(self.target()))

    def test_populated_empty_fields_are_rejected(self):
        for field in (
            "referring_practitioner",
            "source",
            "notes",
            "inpatient_record",
            "emergency_record",
            "ref_sales_invoice",
            "procedure_prescription",
            "service_request",
            "insurance_policy",
            "insurance_coverage",
            "event",
            "practitioner",
            "service_unit",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(
                    contract.target_problem(self.target(**{field: "X-1"}))
                )

    def test_unreviewed_derived_values_and_defaults_are_rejected(self):
        for override in (
            {"patient_age": "42"},
            {"invoiced": 1},
            {"reminded": 1},
            {"add_video_conferencing": 1},
            {"appointment_for": "Practitioner"},
            {"duration": 0},
            {"unknown_field": "x"},
        ):
            with self.subTest(override=override):
                self.assertIsNotNone(contract.target_problem(self.target(**override)))


class SourceValueTests(unittest.TestCase):
    def sources(self, **overrides):
        values = {
            "patient_name": "Patient One",
            "patient_sex": "Female",
            "inpatient_record": None,
            "emergency_record": None,
            "duration": 15,
            "allow_booking_for": "Department",
        }
        values.update(overrides)
        return values

    def test_reviewed_sources_are_accepted(self):
        self.assertIsNone(contract.source_problem(self.sources()))

    def test_source_fetch_and_unreviewed_links_must_be_safe(self):
        for override in (
            {"patient_name": None},
            {"patient_sex": None},
            {"inpatient_record": "IP-1"},
            {"emergency_record": "ER-1"},
            {"duration": 0},
            {"duration": "15"},
            {"allow_booking_for": "Practitioner"},
            {"allow_booking_for": "Service Unit"},
        ):
            with self.subTest(override=override):
                self.assertIsNotNone(
                    contract.source_problem(self.sources(**override))
                )


class TargetEligibilityTests(unittest.TestCase):
    def test_reschedule_is_for_non_dedicated_non_terminal_records(self):
        base = {
            "appointment_date": "2026-11-20",
            "today": "2026-11-14",
            "patient": "PAT-1",
            "dedicated_patient": "PAT-2",
            "has_receipt": False,
        }
        for status in ("Scheduled", "Open", "Confirmed"):
            with self.subTest(status=status):
                self.assertTrue(
                    contract.reschedule_target_eligible(status=status, **base)
                )
        self.assertFalse(
            contract.reschedule_target_eligible(status="Checked In", **base)
        )
        self.assertFalse(
            contract.reschedule_target_eligible(status="Open", **{**base, "has_receipt": True})
        )
        self.assertFalse(
            contract.reschedule_target_eligible(
                status="Open",
                **{
                    **base,
                    "appointment_date": "2026-11-14",
                    "patient": "PAT-2",
                },
            )
        )

    def test_checkin_is_the_dedicated_same_day_record(self):
        base = {
            "status": "Open",
            "appointment_date": "2026-11-14",
            "today": "2026-11-14",
            "patient": "PAT-2",
            "dedicated_patient": "PAT-2",
            "has_receipt": False,
        }
        self.assertTrue(contract.checkin_target_eligible(**base))
        for override in (
            {"status": "Checked In"},
            {"status": "Scheduled"},
            {"appointment_date": "2026-11-20"},
            {"today": "2026-11-15"},
            {"patient": "PAT-1"},
            {"dedicated_patient": "PAT-1"},
            {"has_receipt": True},
        ):
            with self.subTest(override=override):
                self.assertFalse(
                    contract.checkin_target_eligible(**{**base, **override})
                )


class BoundarySnapshotTests(unittest.TestCase):
    def snapshot(self):
        return copy.deepcopy(contract.reviewed_boundary_snapshot())

    def test_the_reviewed_snapshot_is_accepted(self):
        self.assertIsNone(contract.boundary_problem(self.snapshot()))

    def test_wildcard_on_change_pins_the_live_milestone_evaluator(self):
        snapshot = self.snapshot()

        self.assertIn("on_change", contract.WILDCARD_TRIGGER_EVENTS)
        self.assertEqual(
            snapshot["wildcard_on_change"],
            (
                "frappe.automation.doctype.milestone_tracker."
                "milestone_tracker.evaluate_milestone",
            ),
        )
        self.assertEqual(snapshot["enabled_milestone_trackers"], 0)

    def test_version_and_source_drift_is_rejected(self):
        for mutate in (
            lambda s: s["versions"].update({"frappe": "16.99.0"}),
            lambda s: s["versions"].update({"healthcare": "16.7.0"}),
            lambda s: s["source_sha256"].update({"patient_appointment": "0" * 64}),
            lambda s: s["source_sha256"].pop("fee_validity"),
        ):
            with self.subTest(mutate=mutate):
                snapshot = self.snapshot()
                mutate(snapshot)
                self.assertIsNotNone(contract.boundary_problem(snapshot))

    def test_checkin_profiles_accept_only_exact_installed_photo_override(self):
        target = "clinic_photos.visit.checkin.check_in_appointment"
        for installed, overrides, allowed in (
            (False, (), True),
            (True, (target,), True),
            (True, (), False),
            (False, (target,), False),
            (True, ("other.target",), False),
            (True, (target, target), False),
            (True, (target, "other.target"), False),
            (False, None, False),
            (True, target, False),
            (None, (), False),
        ):
            with self.subTest(installed=installed, overrides=overrides):
                snapshot = self.snapshot()
                snapshot["clinic_photos_installed"] = installed
                snapshot["checkin_whitelisted_override"] = overrides
                self.assertEqual(contract.boundary_problem(snapshot) is None, allowed)
                if allowed:
                    snapshot["enabled_server_scripts"] = 1
                    self.assertIsNotNone(contract.boundary_problem(snapshot))

    def test_hook_override_server_script_and_metadata_drift_is_rejected(self):
        mutators = {
            "patient doc event": lambda s: s.update(
                {"doc_events_patient_appointment": ("x.y",)}
            ),
            "wildcard update": lambda s: s.update(
                {"wildcard_on_update": s["wildcard_on_update"] + ("x.y",)}
            ),
            "wildcard validate": lambda s: s.update(
                {"wildcard_validate": s["wildcard_validate"] + ("x.y",)}
            ),
            "wildcard on_change": lambda s: s.update(
                {"wildcard_on_change": s["wildcard_on_change"] + ("x.y",)}
            ),
            "milestone tracker": lambda s: s.update(
                {"enabled_milestone_trackers": 1}
            ),
            "override class": lambda s: s.update(
                {"override_doctype_class": ("x.Y",)}
            ),
            "whitelist override": lambda s: s.update(
                {"checkin_whitelisted_override": ("x.y",)}
            ),
            "scheduler all": lambda s: s.update(
                {"scheduler_all": s["scheduler_all"] + ("x.y",)}
            ),
            "scheduler daily": lambda s: s.update(
                {"scheduler_daily": s["scheduler_daily"][:-1]}
            ),
            "server script": lambda s: s.update({"enabled_server_scripts": 1}),
            "appointment metadata": lambda s: s["appointment_metadata"].update(
                {
                    "notes": s["appointment_metadata"]["notes"][:4]
                    + ("x",)
                    + s["appointment_metadata"]["notes"][5:]
                }
            ),
            "patient metadata": lambda s: s["patient_metadata"].update(
                {
                    "sex": s["patient_metadata"]["sex"][:5]
                    + (1,)
                    + s["patient_metadata"]["sex"][6:]
                }
            ),
            "type metadata": lambda s: s["appointment_type_metadata"].update(
                {
                    "allow_booking_for": s["appointment_type_metadata"][
                        "allow_booking_for"
                    ][:1]
                    + ("Practitioner\nDepartment",)
                    + s["appointment_type_metadata"]["allow_booking_for"][2:]
                }
            ),
            "checkin module": lambda s: s.update({"checkin_module": "x.y"}),
            "class module": lambda s: s.update({"patient_appointment_class": "x.Y"}),
            "cancel module": lambda s: s.update({"cancel_module": "x.y"}),
            "cancel name": lambda s: s.update({"cancel_name": "x_y"}),
            "cancel whitelist override": lambda s: s.update(
                {"cancel_whitelisted_override": ("x.y",)}
            ),
        }
        for name, mutate in mutators.items():
            with self.subTest(case=name):
                snapshot = self.snapshot()
                mutate(snapshot)
                self.assertIsNotNone(contract.boundary_problem(snapshot))


class CancelNoShowContractTests(unittest.TestCase):
    """The designed cancel/no-show transition contract, pure and closed-world."""

    def target(self, **overrides):
        values = {
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

    def test_pinned_transition_names_and_statuses(self):
        self.assertEqual(contract.CANCEL_NAME, "cancel_appointment")
        self.assertEqual(contract.CANCEL_MODULE, contract.CHECKIN_MODULE)
        self.assertEqual(contract.CANCELLED_STATUS, "Cancelled")
        self.assertEqual(contract.NO_SHOW_STATUS, "No Show")
        self.assertEqual(
            contract.CANCELLABLE_STATUSES,
            ("Scheduled", "Open", "Confirmed", "Checked In"),
        )
        self.assertEqual(
            contract.NO_SHOW_STATUSES, ("Scheduled", "Open", "Confirmed")
        )

    def test_every_cancellable_status_is_a_designed_non_terminal_target(self):
        # Checked In preserves a terminal badge in ``set_status`` but SPEC:118-122
        # and the SIT target (Checked In -> Cancelled) admit it as a cancel
        # source. The terminal *destinations* stay refused.
        for status in ("Cancelled", "Closed", "No Show", "Checked Out"):
            with self.subTest(status=status):
                self.assertNotIn(status, contract.CANCELLABLE_STATUSES)
        for status in contract.NO_SHOW_STATUSES:
            with self.subTest(status=status):
                self.assertNotIn(status, contract.TERMINAL_STATUSES)

    def test_cancel_accepts_every_non_terminal_status(self):
        for status in ("Scheduled", "Open", "Confirmed", "Checked In"):
            with self.subTest(status=status):
                self.assertIsNone(
                    contract.cancel_target_problem(self.target(status=status))
                )

    def test_cancel_refuses_terminal_and_unknown_statuses(self):
        for status in ("Closed", "Cancelled", "No Show", "Checked Out", "", None):
            with self.subTest(status=status):
                self.assertIsNotNone(
                    contract.cancel_target_problem(self.target(status=status))
                )

    def test_cancel_refuses_unreviewed_linked_record_branches(self):
        for field in (
            "invoiced",
            "ref_sales_invoice",
            "paid_amount",
            "insurance_coverage",
            "service_request",
            "mode_of_payment",
        ):
            with self.subTest(field=field):
                self.assertIsNotNone(
                    contract.cancel_target_problem(self.target(**{field: "X" if field
                        in ("ref_sales_invoice", "insurance_coverage",
                            "service_request", "mode_of_payment") else 1}))
                )

    def test_cancel_refuses_an_over_long_notes_preimage(self):
        self.assertIsNotNone(
            contract.cancel_target_problem(
                self.target(notes="x" * (contract.MAX_NOTES_LENGTH + 1))
            )
        )

    def test_no_show_accepts_only_booked_non_terminal_statuses(self):
        for status in ("Scheduled", "Open", "Confirmed"):
            with self.subTest(status=status):
                self.assertIsNone(
                    contract.no_show_target_problem(self.target(status=status))
                )
        for status in (
            "Checked In",
            "Checked Out",
            "Closed",
            "Cancelled",
            "No Show",
            "",
            None,
        ):
            with self.subTest(status=status):
                self.assertIsNotNone(
                    contract.no_show_target_problem(self.target(status=status))
                )

    def test_note_line_is_deterministic_and_actor_bound(self):
        line = contract.note_line(
            contract.NO_SHOW_KIND,
            actor="reception@example.test",
            reason="Patient did not arrive.",
            recorded_at="2026-11-14 09:30:00",
        )
        self.assertEqual(
            line,
            "[2026-11-14 09:30:00] appointment_no_show by reception@example.test: "
            "Patient did not arrive.",
        )
        self.assertNotEqual(
            line,
            contract.note_line(
                contract.NO_SHOW_KIND,
                actor="other@example.test",
                reason="Patient did not arrive.",
                recorded_at="2026-11-14 09:30:00",
            ),
        )

    def test_note_residue_is_appended_verbatim(self):
        line = contract.note_line(
            contract.CANCEL_KIND,
            actor="reception@example.test",
            reason="Cancelled at the desk.",
            recorded_at="2026-11-14 09:30:00",
        )
        self.assertEqual(contract.note_postimage(None, line), line)
        self.assertEqual(contract.note_postimage("", line), line)
        self.assertEqual(
            contract.note_postimage("Existing note.", line),
            "Existing note.\n" + line,
        )

    def test_cancel_event_is_expected_only_for_the_practitioner_branch(self):
        self.assertFalse(contract.cancel_event_expected(self.target()))
        self.assertFalse(
            contract.cancel_event_expected(self.target(event="EVENT-1"))
        )
        self.assertTrue(
            contract.cancel_event_expected(
                self.target(event="EVENT-1", practitioner="PRAC-1")
            )
        )
        self.assertTrue(
            contract.cancel_event_expected(self.target(practitioner="PRAC-1"))
        )

    def test_post_condition_reads_the_returned_state(self):
        line = contract.note_line(
            contract.CANCEL_KIND,
            actor="reception@example.test",
            reason="Cancelled at the desk.",
            recorded_at="2026-11-14 09:30:00",
        )
        postimage = contract.note_postimage(None, line)
        self.assertIsNone(
            contract.cancel_postcondition_problem(
                self.target(status="Cancelled", notes=postimage), postimage
            )
        )
        self.assertIsNotNone(
            contract.cancel_postcondition_problem(
                self.target(status="Cancelled", notes=None), postimage
            )
        )
        self.assertIsNotNone(
            contract.cancel_postcondition_problem(
                self.target(status="Open", notes=postimage), postimage
            )
        )
        self.assertIsNone(
            contract.no_show_postcondition_problem(
                self.target(status="No Show", notes=postimage), postimage
            )
        )
        self.assertIsNotNone(
            contract.no_show_postcondition_problem(
                self.target(status="Scheduled", notes=postimage), postimage
            )
        )


if __name__ == "__main__":
    unittest.main()
