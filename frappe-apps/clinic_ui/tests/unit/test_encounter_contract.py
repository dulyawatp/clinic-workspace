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
                "appointment_type",
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
                "reference_doc",
                "reference_name",
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
            "Patient Encounter Symptom": ("complaint",),
            "Patient Encounter Diagnosis": ("diagnosis",),
            "Lab Prescription": (lab_field,),
            "Procedure Prescription": ("procedure",),
        }.items()
    }
    fields["Drug Prescription"] = {drug_field, "dosage", "period"}
    return fields


def installed_shapes(
    *,
    symptom_type="Table MultiSelect",
    symptom_child="Patient Encounter Symptom",
    note_pair=("reference_doc", "reference_name"),
):
    """The installed healthcare v16.6.1 metadata, fieldname -> (type, options).

    This is the shape ``frappe.get_meta(doctype).fields`` carries: scalar fields,
    Table fields and default columns (``modified`` has no DocField row, so its
    shape is unknown).
    """

    return {
        "Patient Encounter": {
            "patient": ("Link", "Patient"),
            "practitioner": ("Link", "Healthcare Practitioner"),
            "encounter_date": ("Date", None),
            "appointment": ("Link", "Patient Appointment"),
            "appointment_type": ("Link", "Appointment Type"),
            "symptoms": (symptom_type, symptom_child),
            "diagnosis": ("Table MultiSelect", "Patient Encounter Diagnosis"),
            "drug_prescription": ("Table", "Drug Prescription"),
            "lab_test_prescription": ("Table", "Lab Prescription"),
            "procedure_prescription": ("Table", "Procedure Prescription"),
            "status": ("Select", "\nOpen\nOrdered\nCompleted\nCancelled"),
            "modified": (None, None),
        },
        "Clinical Note": {
            "patient": ("Link", "Patient"),
            "practitioner": ("Link", "Healthcare Practitioner"),
            "note": ("Text Editor", None),
            note_pair[0]: ("Link", "DocType"),
            note_pair[1]: ("Dynamic Link", note_pair[0]),
        },
        "Vital Signs": {
            "patient": ("Link", "Patient"),
            "temperature": ("Float", None),
            "pulse": ("Int", None),
            "respiratory_rate": ("Int", None),
            "bp_systolic": ("Int", None),
            "bp_diastolic": ("Int", None),
            "height": ("Float", None),
            "weight": ("Float", None),
        },
        "Service Request": {
            "patient": ("Link", "Patient"),
            "status": ("Select", "\nDraft\nActive\nCompleted"),
        },
        "Patient Encounter Symptom": {"complaint": ("Link", "Complaint")},
        "Patient Encounter Diagnosis": {"diagnosis": ("Link", "Diagnosis")},
        "Drug Prescription": {
            "drug_code": ("Link", "Item"),
            "dosage": ("Link", "Prescription Dosage"),
            "period": ("Link", "Prescription Duration"),
            "dosage_form": ("Link", "Dosage Form"),
        },
        "Lab Prescription": {"lab_test_code": ("Link", "Lab Test Template")},
        "Procedure Prescription": {
            "procedure": ("Link", "Clinical Procedure Template")
        },
    }


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


class InstalledShapeTests(unittest.TestCase):
    """The installed metadata is typed: a name-only set cannot prove a table."""

    def test_the_installed_shape_passes(self):
        self.assertIsNone(contract.install_problem(installed_shapes()))

    def test_the_installed_appointment_type_is_required(self):
        self.assertIn(
            "appointment_type",
            contract.REQUIRED_FIELDS[contract.ENCOUNTER_DOCTYPE],
        )

    def test_a_scalar_column_with_the_table_name_is_refused(self):
        self.assertEqual(
            contract.install_problem(installed_shapes(symptom_type="Data")),
            "field Patient Encounter.symptoms is not a table field",
        )

    def test_the_symptom_child_doctype_is_pinned(self):
        self.assertEqual(
            contract.install_problem(
                installed_shapes(symptom_child="Complaint Detail")
            ),
            "field Patient Encounter.symptoms child table is not "
            "Patient Encounter Symptom",
        )

    def test_the_legacy_complaint_child_alone_is_refused(self):
        fields = installed_fields()
        del fields[contract.SYMPTOM_CHILD_DOCTYPE]
        fields["Complaint Detail"] = {"complaint"}
        self.assertEqual(
            contract.install_problem(fields),
            "missing DocType Patient Encounter Symptom",
        )

    def test_a_mixed_reference_pair_is_refused(self):
        """Only a matched pair links a note; a half-legacy mix links nothing."""

        shapes = installed_shapes()
        note = shapes["Clinical Note"]
        del note["reference_name"]
        note["reference_docname"] = ("Dynamic Link", "reference_doc")

        self.assertEqual(
            contract.install_problem(shapes),
            "missing field Clinical Note.reference_name",
        )
        self.assertEqual(contract.note_reference_fields(shapes), (None, None))

    def test_a_reference_doc_that_is_not_a_doctype_link_is_refused(self):
        shapes = installed_shapes()
        shapes["Clinical Note"]["reference_doc"] = ("Data", None)

        self.assertEqual(
            contract.install_problem(shapes),
            "field Clinical Note.reference_doc is not a DocType link",
        )
        self.assertEqual(contract.note_reference_fields(shapes), (None, None))

    def test_a_reference_name_that_points_at_another_field_is_refused(self):
        shapes = installed_shapes()
        shapes["Clinical Note"]["reference_name"] = (
            "Dynamic Link",
            "reference_doctype",
        )

        self.assertEqual(
            contract.install_problem(shapes),
            "field Clinical Note.reference_name is not a dynamic link to "
            "reference_doc",
        )

    def test_a_reference_name_that_is_not_a_dynamic_link_is_refused(self):
        shapes = installed_shapes()
        shapes["Clinical Note"]["reference_name"] = ("Data", None)

        self.assertEqual(
            contract.install_problem(shapes),
            "field Clinical Note.reference_name is not a dynamic link to "
            "reference_doc",
        )

    def test_the_note_reference_pair_is_the_installed_one(self):
        self.assertEqual(
            contract.note_reference_fields(installed_shapes()),
            ("reference_doc", "reference_name"),
        )

    def test_the_legacy_note_reference_pair_still_resolves(self):
        shapes = installed_shapes(
            note_pair=("reference_doctype", "reference_docname")
        )
        self.assertEqual(
            contract.note_reference_fields(shapes),
            ("reference_doctype", "reference_docname"),
        )


class MedicationSupportTests(unittest.TestCase):
    """Medication rows stay outside the reviewed contract.

    ``Drug Prescription.dosage_form`` is installed required and the payload
    cannot supply it, so a medication-bearing draft would fail after dispatch and
    latch the site. It is refused before the fence instead.
    """

    def test_a_medication_bearing_draft_is_refused(self):
        self.assertEqual(
            contract.medication_problem(
                {"medications": [{"drug_code": "DRUG-1"}]}
            ),
            "medication rows are outside the reviewed encounter contract",
        )

    def test_an_empty_medication_section_is_admitted(self):
        for fields in (
            {},
            {"medications": []},
            {"medications": ()},
            {"medications": None},
        ):
            with self.subTest(fields=fields):
                self.assertIsNone(contract.medication_problem(fields))

    def test_a_non_sequence_medication_value_is_refused(self):
        self.assertEqual(
            contract.medication_problem({"medications": {"drug_code": "D-1"}}),
            "medication rows are outside the reviewed encounter contract",
        )


class PersistedMedicationTests(unittest.TestCase):
    """A target that already holds medication rows is refused on the target.

    The reviewed contract cannot write a medication row and must not delete the
    rows a native user left behind, so the count — not the codes — decides. A
    blank drug code still counts.
    """

    def test_a_target_holding_medication_rows_is_refused(self):
        self.assertEqual(
            contract.persisted_medication_problem({"medication_count": 1}),
            "target encounter has existing medication rows",
        )

    def test_blank_drug_codes_still_count(self):
        self.assertEqual(
            contract.persisted_medication_problem(
                {"medication_count": 2, "medication_codes": ("", None)}
            ),
            "target encounter has existing medication rows",
        )

    def test_a_clean_target_passes(self):
        self.assertIsNone(
            contract.persisted_medication_problem({"medication_count": 0})
        )

    def test_an_unreadable_count_fails_closed(self):
        for value in (None, "1", -1, True, 1.0):
            with self.subTest(value=value):
                self.assertEqual(
                    contract.persisted_medication_problem(
                        {"medication_count": value}
                    ),
                    "medication row count is unreadable",
                )


class CreateTargetTests(unittest.TestCase):
    def test_a_create_needs_the_linked_appointments_type(self):
        self.assertEqual(
            contract.create_target_problem(
                {"appointment": "APPT-1", "appointment_type": None}
            ),
            "linked appointment has no appointment type",
        )
        self.assertEqual(
            contract.create_target_problem(
                {"appointment": None, "appointment_type": "APPT-TYPE-1"}
            ),
            "draft create has no linked appointment",
        )

    def test_a_derived_appointment_type_passes(self):
        self.assertIsNone(
            contract.create_target_problem(
                {"appointment": "APPT-1", "appointment_type": "APPT-TYPE-1"}
            )
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
