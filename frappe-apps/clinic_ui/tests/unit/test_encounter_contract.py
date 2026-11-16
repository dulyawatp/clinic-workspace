"""Contract tests for the Patient Encounter write family.

The installed field set is supplied as a fixture (as ``frappe.db.get_meta``
would return it) so the preflight is exercised without a live site. Nothing
here performs I/O; these are pure closed-world decisions.
"""

import unittest

from clinic_ui.core import encounter_contract as contract
from clinic_ui.core import encounter_write


def installed_fields(*, drug_field="drug_code", lab_field="lab_test_code"):
    """A complete installed metadata fixture with selectable alternatives."""

    fields = {
        doctype: set(values)
        for doctype, values in {
            "Patient Encounter": (
                "patient",
                "practitioner",
                "encounter_date",
                "appointment",
                "symptoms",
                "diagnosis",
                "drug_prescription",
                "lab_test_prescription",
                "procedure_prescription",
                "status",
                "modified",
            ),
            "Clinical Note": (
                "patient",
                "practitioner",
                "note",
                "reference_doctype",
                "reference_docname",
            ),
            "Vital Signs": (
                "patient",
                "temperature",
                "pulse",
                "respiratory_rate",
                "bp_systolic",
                "bp_diastolic",
                "height",
                "weight",
            ),
            "Service Request": ("patient", "status"),
            "Complaint Detail": ("complaint",),
            "Patient Encounter Diagnosis": ("diagnosis",),
            "Lab Prescription": (lab_field,),
            "Procedure Prescription": ("procedure",),
        }.items()
    }
    fields["Drug Prescription"] = {drug_field, "dosage", "period"}
    return fields


class InstallPreflightTests(unittest.TestCase):
    def test_complete_installed_field_set_passes(self):
        self.assertIsNone(contract.install_problem(installed_fields()))

    def test_the_installed_alternative_names_are_accepted(self):
        fields = installed_fields(drug_field="medication", lab_field="lab_test_name")
        self.assertIsNone(contract.install_problem(fields))
        self.assertEqual(
            contract.resolve_field(fields, "Drug Prescription", "drug_code"),
            "medication",
        )
        self.assertEqual(
            contract.resolve_field(fields, "Lab Prescription", "lab_test_code"),
            "lab_test_name",
        )

    def test_a_missing_field_cuts_the_family_with_its_name(self):
        fields = installed_fields()
        fields["Drug Prescription"] = {"dosage", "period"}
        self.assertEqual(
            contract.install_problem(fields),
            "missing field Drug Prescription.drug_code",
        )

    def test_a_missing_doctype_is_reported(self):
        fields = installed_fields()
        del fields["Vital Signs"]
        self.assertEqual(
            contract.install_problem(fields), "missing DocType Vital Signs"
        )


class BoundaryTests(unittest.TestCase):
    def snapshot(self, **overrides):
        values = {
            "versions": {
                "frappe": "16.54.0",
                "erpnext": "16.52.1",
                "healthcare": "16.6.1",
            },
            "encounter_submittable": 1,
            "enabled_server_scripts": 0,
            "doc_events": {
                "Patient Encounter": (),
                "Clinical Note": (),
                "Vital Signs": (),
            },
        }
        values.update(overrides)
        return values

    def test_pinned_boundary_passes(self):
        self.assertIsNone(contract.boundary_problem(self.snapshot()))

    def test_version_drift_fails_closed(self):
        snapshot = self.snapshot()
        snapshot["versions"]["healthcare"] = "16.6.2"
        self.assertEqual(
            contract.boundary_problem(snapshot),
            "versions drift for healthcare",
        )

    def test_wildcard_doc_events_fail_closed(self):
        snapshot = self.snapshot()
        snapshot["doc_events"]["Vital Signs"] = ("some.hook",)
        self.assertEqual(
            contract.boundary_problem(snapshot),
            "unreviewed doc_events for Vital Signs",
        )


class CompletionTests(unittest.TestCase):
    def detail(self, **overrides):
        values = {
            "complaint": True,
            "diagnosis_count": 1,
            "assessment": "Acne",
            "plan": "Topical",
            "followup": "Two weeks",
            "allergy_reviewed": True,
        }
        values.update(overrides)
        return values

    def test_a_complete_draft_has_no_problem(self):
        self.assertEqual(contract.completion_problems(self.detail()), ())

    def test_each_missing_field_has_its_own_problem_text(self):
        cases = (
            ("complaint", "chief complaint is required before completion"),
            ("diagnosis", "at least one diagnosis is required before completion"),
            ("assessment", "assessment is required before completion"),
            ("plan", "plan is required before completion"),
            ("followup", "follow-up is required before completion"),
            (
                "allergy_reviewed",
                "allergy review acknowledgement is required before completion",
            ),
        )
        for field, message in cases:
            with self.subTest(field=field):
                values = self.detail()
                if field == "diagnosis":
                    values["diagnosis_count"] = 0
                else:
                    values[field] = False if field == "allergy_reviewed" else ""
                problems = contract.completion_problems(values)
                self.assertEqual(problems, ((field, message),))


class TargetTests(unittest.TestCase):
    def test_complete_refuses_a_missing_appointment_link(self):
        self.assertEqual(
            contract.complete_target_problem(
                {"docstatus": 0, "appointment": None, "appointment_status": None}
            ),
            "target encounter has no linked appointment",
        )

    def test_complete_refuses_an_appointment_outside_open_or_checked_in(self):
        for status in (None, "Scheduled", "Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                self.assertEqual(
                    contract.complete_target_problem(
                        {
                            "docstatus": 0,
                            "appointment": "APPT-1",
                            "appointment_status": status,
                        }
                    ),
                    "linked appointment is not open or checked in",
                )

    def test_complete_accepts_open_and_checked_in(self):
        for status in contract.CASCADE_SOURCE_STATUSES:
            with self.subTest(status=status):
                self.assertIsNone(
                    contract.complete_target_problem(
                        {
                            "docstatus": 0,
                            "appointment": "APPT-1",
                            "appointment_status": status,
                        }
                    )
                )

    def test_amend_requires_a_completed_finalized_target(self):
        self.assertEqual(
            contract.amend_target_problem({"docstatus": 0, "status": "Draft"}),
            "target encounter is not finalized",
        )
        self.assertEqual(
            contract.amend_target_problem(
                {"docstatus": 1, "status": "Draft", "clinical_note": "N-1"}
            ),
            "target encounter is not completed",
        )
        self.assertIsNone(
            contract.amend_target_problem(
                {"docstatus": 1, "status": "Completed", "clinical_note": "N-1"}
            )
        )


class CascadeTests(unittest.TestCase):
    def test_service_request_expectation_is_one_per_procedure_and_lab_row(self):
        self.assertEqual(
            contract.expected_service_requests(
                {"lab_count": 2, "procedure_count": 1}
            ),
            3,
        )

    def test_cascade_problem_covers_every_expected_side_effect(self):
        values = {
            "docstatus": 1,
            "status": "Completed",
            "appointment_status": "Closed",
            "submitted_service_requests": 2,
        }
        self.assertIsNone(contract.cascade_problem(values, 2))
        self.assertEqual(
            contract.cascade_problem({**values, "docstatus": 0}, 2),
            "encounter was not submitted",
        )
        self.assertEqual(
            contract.cascade_problem({**values, "status": "Draft"}, 2),
            "encounter status is not Completed",
        )
        self.assertEqual(
            contract.cascade_problem({**values, "appointment_status": "Open"}, 2),
            "linked appointment was not closed",
        )
        self.assertEqual(
            contract.cascade_problem(
                {**values, "submitted_service_requests": 1}, 2
            ),
            "service request cascade count differs",
        )


class AmendmentTests(unittest.TestCase):
    def test_amendment_must_not_rewrite_the_finalized_records(self):
        values = {
            "encounter_modified": "2026-11-14 09:00:00",
            "expected_encounter_modified": "2026-11-14 09:00:00",
            "clinical_note_modified": "2026-11-14 08:00:00",
            "original_note_modified": "2026-11-14 08:00:00",
            "addendum_created": True,
            "expected_note": "[clinic-ui amendment] doc: text",
        }
        self.assertIsNone(contract.amend_postcondition_problem(**values))
        self.assertEqual(
            contract.amend_postcondition_problem(
                **{**values, "encounter_modified": "2026-11-14 10:00:00"}
            ),
            "encounter was rewritten by the amendment",
        )
        self.assertEqual(
            contract.amend_postcondition_problem(
                **{**values, "clinical_note_modified": "2026-11-14 10:00:00"}
            ),
            "original clinical note was rewritten",
        )
        self.assertEqual(
            contract.amend_postcondition_problem(
                **{**values, "addendum_created": False}
            ),
            "addendum was not created",
        )


class NoteTextTests(unittest.TestCase):
    def test_note_sections_round_trip_without_inventing_a_field(self):
        note = {
            "assessment": "Acne vulgaris",
            "plan": "Topical retinoid",
            "rationale": "First line",
            "followup": "Two weeks",
        }
        text = contract.build_note_text(note)
        self.assertIn("Assessment: Acne vulgaris", text)
        self.assertEqual(contract.parse_note_text(text), note)

    def test_empty_sections_are_omitted_and_parsed_as_empty(self):
        text = contract.build_note_text({"assessment": "A"})
        self.assertEqual(text, "Assessment: A")
        self.assertEqual(contract.parse_note_text(text)["plan"], "")


class SectionRoleTests(unittest.TestCase):
    def test_doc_writes_any_section(self):
        self.assertIsNone(
            contract.section_problem("DOC", {"note": {"assessment": "A"}})
        )

    def test_nurse_writes_vitals_only(self):
        self.assertIsNone(contract.section_problem("NUR", {"vitals": {"pulse": "80"}}))
        for field in (
            "complaint",
            "diagnoses",
            "medications",
            "lab_tests",
            "procedures",
            "note",
        ):
            with self.subTest(field=field):
                self.assertEqual(
                    contract.section_problem("NUR", {field: [{"x": "y"}]}),
                    f"nurse may not write {field}",
                )


class BudgetAndCodecTests(unittest.TestCase):
    def test_budgets_follow_the_approved_registry(self):
        self.assertEqual(encounter_write.budget("encounter_draft"), 2)
        self.assertEqual(encounter_write.budget("encounter_complete"), 1)
        self.assertEqual(encounter_write.budget("encounter_amend"), 1)

    def test_receipt_and_alias_are_encounter_only(self):
        secret = b"0123456789abcdef0123456789abcdef"
        alias = encounter_write.build_alias(secret, "marley.test", "doc@test", "key-0001-abcdefgh")
        self.assertTrue(alias.startswith("wse1_"))
        self.assertEqual(
            encounter_write.parse_alias(secret, "marley.test", "doc@test", alias),
            "key-0001-abcdefgh",
        )
        receipt = encounter_write.encode_receipt(
            kind="encounter_draft",
            alias=alias,
            native_encounter_id="ENC-1",
        )
        decoded = encounter_write.decode_receipt(receipt)
        self.assertEqual(decoded.native_encounter_id, "ENC-1")


if __name__ == "__main__":
    unittest.main()
