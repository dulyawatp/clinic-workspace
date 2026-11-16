"""Endpoint tests for the designed Patient Encounter write family.

The fake gateway proves gate ordering, strict bodies, the per-section role rule,
the pre-fence refusals (including the required-before-complete set and the
completion cascade eligibility), the fence-before-native ordering, the
closed-world cascade check and the amendment's never-rewrite guarantee.
"""

import unittest

from clinic_ui.core import appointment_write, config, patient_write
from clinic_ui.core import encounter_write
from clinic_ui.core.appointment_write import build_alias as build_appointment_alias
from clinic_ui.endpoints import encounters_write

from .fake_gateway import (
    Denied,
    FakeAppointmentReceipt,
    FakeEncounterReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "doctor@example.test"
CSRF = "csrf-token-value"
BOOK_KEY = "appt-book-key-0001-012345"
DRAFT_KEY = "enc-draft-key-0001-0123"
DRAFT_UPDATE_KEY = "enc-draft-key-0002-0123"
COMPLETE_KEY = "enc-complete-key-0001-01"
AMEND_KEY = "enc-amend-key-0001-0123"
MODIFIED = "2026-11-14 09:30:00.123456"
NOTE_MODIFIED = "2026-11-14 08:00:00.000000"
NATIVE_APPT = "NATIVE-APPT-1"
NATIVE_ENCOUNTER = "NATIVE-ENC-1"
NATIVE_NOTE = "NATIVE-NOTE-1"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_patients=[
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
        ],
        clinic_ui_native_appointment_write_enabled=1,
        clinic_ui_native_appointment_write_site=SITE,
        clinic_ui_appointment_medical_department="MEDDEP-1",
        clinic_ui_appointment_company="COMP-1",
        clinic_ui_appointment_type="APPT-TYPE-1",
        clinic_ui_appointment_checkin_patient="WS-1",
        clinic_ui_native_encounter_write_enabled=1,
        clinic_ui_native_encounter_write_site=SITE,
        clinic_ui_encounter_practitioner="PRAC-1",
        clinic_ui_encounter_company="COMP-1",
    )
    values.update(overrides)
    return values


def encounter_detail(**overrides):
    values = {
        "patient": "PAT-1",
        "docstatus": 0,
        "status": "Draft",
        "appointment": NATIVE_APPT,
        "appointment_status": "Open",
        "modified": MODIFIED,
        "complaint": True,
        "diagnosis_count": 1,
        "medication_codes": ("DRUG-1",),
        "lab_count": 1,
        "procedure_count": 1,
        "clinical_note": NATIVE_NOTE,
        "clinical_note_modified": NOTE_MODIFIED,
        "assessment": "Acne",
        "plan": "Topical retinoid",
        "followup": "Two weeks",
    }
    values.update(overrides)
    return values


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(user=ACTOR, roles=("Physician",)),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = FakeGateway(
        **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    fake.site_today_value = "2026-11-14"
    fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail()
    fake.encounter_detail_map[NATIVE_ENCOUNTER] = {
        "patient": "PAT-1",
        "docstatus": 1,
        "status": "Completed",
        "modified": MODIFIED,
    }
    fake.appointment_action_details[NATIVE_APPT] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-14",
        "appointment_time": "09:30:00",
        "status": "Open",
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
    return fake


def appointment_alias(key=BOOK_KEY):
    return build_appointment_alias(SECRET.encode(), SITE, ACTOR, key)


def encounter_alias(key=DRAFT_KEY):
    return encounter_write.build_alias(SECRET.encode(), SITE, ACTOR, key)


def booked(fake):
    fake.appointment_receipt_records.append(
        FakeAppointmentReceipt(
            "book",
            ACTOR,
            BOOK_KEY,
            appointment_write.encode_receipt(
                kind="book",
                alias=appointment_alias(),
                native_appointment_id=NATIVE_APPT,
            ),
        )
    )


def committed_encounter(fake, kind, key, native_id=NATIVE_ENCOUNTER):
    fake.encounter_receipt_records.append(
        FakeEncounterReceipt(
            kind,
            ACTOR,
            key,
            encounter_write.encode_receipt(
                kind=kind,
                alias=encounter_alias(key),
                native_encounter_id=native_id,
            ),
        )
    )


def draft_body(**overrides):
    values = {
        "patient": "WS-1",
        "operation_key": DRAFT_KEY,
        "appointment": appointment_alias(),
        "complaint": "Itchy rash",
        "diagnoses": [{"diagnosis": "L20.9"}],
        "medications": [
            {"drug_code": "DRUG-1", "dosage": "1 tab", "period": "7 days"}
        ],
        "lab_tests": [{"lab_test_code": "CBC"}],
        "procedures": [{"procedure": "PROC-1"}],
        "vitals": {"temperature": "37.1", "pulse": "72"},
        "note": {
            "assessment": "Atopic dermatitis",
            "plan": "Emollient",
            "followup": "Two weeks",
        },
        "allergy_reviewed": True,
    }
    values.update(overrides)
    return values


def update_body(**overrides):
    values = draft_body(
        encounter=encounter_alias(),
        base_modified=MODIFIED,
        operation_key=DRAFT_UPDATE_KEY,
        appointment=None,
        complaint="Worse rash",
    )
    values.update(overrides)
    return values


def complete_body(**overrides):
    values = {
        "encounter": encounter_alias(),
        "operation_key": COMPLETE_KEY,
        "allergy_reviewed": True,
    }
    values.update(overrides)
    return values


def amend_body(**overrides):
    values = {
        "encounter": encounter_alias(),
        "operation_key": AMEND_KEY,
        "addendum": "Severity increased since the photo.",
    }
    values.update(overrides)
    return values


class DraftTests(unittest.TestCase):
    def test_creates_a_draft_and_acknowledges_once(self):
        fake = gateway()
        booked(fake)

        payload = encounters_write.save_draft(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(payload["encounter"]["encounter_id"], encounter_alias())
        self.assertEqual(payload["encounter"]["state"], "draft")
        self.assertNotIn("NATIVE-ENC", repr(payload))
        self.assertEqual(fake.encounter_fence_calls[0][1], "encounter_draft")
        self.assertEqual(len(fake.create_encounter_calls), 1)
        created = fake.create_encounter_calls[0]
        self.assertEqual(created["_native_patient_id"], "PAT-1")
        self.assertEqual(created["_native_appointment_id"], NATIVE_APPT)
        self.assertEqual(
            created["fields"]["medications"][0]["drug_code"], "DRUG-1"
        )
        self.assertEqual(fake.commit_calls, 1)
        receipt = fake.encounter_fence_claim.committed[0]
        self.assertEqual(
            encounter_write.decode_receipt(receipt).kind, "encounter_draft"
        )

    def test_medication_rows_require_drug_code_before_the_fence(self):
        fake = gateway()
        booked(fake)
        payload = encounters_write.save_draft(
            fake,
            draft_body(medications=[{"dosage": "1 tab"}]),
            CSRF,
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_update_requires_the_actors_committed_draft(self):
        fake = gateway()
        payload = encounters_write.save_draft(fake, update_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_a_stale_base_modified_is_refused_inside_the_fence_without_saving(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write.save_draft(
            fake, update_body(base_modified="2026-11-14 07:00:00.000000"), CSRF
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.save_encounter_calls, [])

    def test_a_draft_can_be_updated_and_keeps_the_same_alias(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write.save_draft(fake, update_body(), CSRF)
        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(payload["encounter"]["encounter_id"], encounter_alias())
        self.assertEqual(len(fake.save_encounter_calls), 1)

    def test_a_draft_needs_an_open_linked_appointment(self):
        fake = gateway()
        booked(fake)
        fake.appointment_action_details[NATIVE_APPT]["status"] = "Scheduled"
        payload = encounters_write.save_draft(fake, draft_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_install_preflight_and_boundary_fail_closed_before_validation(self):
        fake = gateway()
        booked(fake)
        fake.encounter_install_problem = "missing field Drug Prescription.drug_code"
        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, draft_body(), CSRF)
        self.assertEqual(fake.encounter_fence_calls, [])

        other = gateway()
        booked(other)
        other.encounter_boundary_problem = "versions drift for healthcare"
        with self.assertRaises(Denied):
            encounters_write.save_draft(other, draft_body(), CSRF)
        self.assertEqual(other.encounter_fence_calls, [])

    def test_exhausted_draft_budget_is_refused_before_the_fence(self):
        fake = gateway()
        booked(fake)
        committed_encounter(fake, "encounter_draft", "enc-draft-key-0000-0001")
        committed_encounter(fake, "encounter_draft", "enc-draft-key-0000-0002")
        payload = encounters_write.save_draft(fake, draft_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_an_unreviewed_role_is_denied(self):
        fake = gateway(
            current_session=session_view(user="cashier@example.test", roles=("Accounts User",))
        )
        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, draft_body(), CSRF)
        self.assertEqual(fake.encounter_fence_calls, [])


class NurseSectionTests(unittest.TestCase):
    def nurse_gateway(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        return fake

    def test_nurse_may_save_vitals_only(self):
        fake = self.nurse_gateway()
        payload = encounters_write.save_draft(
            fake,
            {
                "patient": "WS-1",
                "operation_key": DRAFT_UPDATE_KEY,
                "encounter": encounter_alias(),
                "base_modified": MODIFIED,
                "vitals": {"temperature": "36.8"},
            },
            CSRF,
        )
        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(len(fake.save_encounter_calls), 1)

    def test_nurse_may_not_write_notes_diagnoses_or_orders(self):
        for field, value in (
            ("note", {"assessment": "A"}),
            ("diagnoses", [{"diagnosis": "L20.9"}]),
            ("medications", [{"drug_code": "DRUG-1"}]),
            ("lab_tests", [{"lab_test_code": "CBC"}]),
            ("procedures", [{"procedure": "PROC-1"}]),
            ("complaint", "Rash"),
        ):
            with self.subTest(field=field):
                fake = self.nurse_gateway()
                with self.assertRaises(Denied):
                    encounters_write.save_draft(
                        fake,
                        update_body(**{field: value}),
                        CSRF,
                    )
                self.assertEqual(fake.encounter_fence_calls, [])


class CompleteTests(unittest.TestCase):
    def ready(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        return fake

    def test_completes_and_verifies_the_expected_cascade(self):
        fake = self.ready()
        fake.submitted_service_requests = 2
        payload = encounters_write.complete(fake, complete_body(), CSRF)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["encounter"]["state"], "completed")
        self.assertEqual(len(fake.submit_encounter_calls), 1)
        self.assertEqual(fake.encounter_fence_calls[0][1], "encounter_complete")
        self.assertEqual(fake.commit_calls, 1)

    def test_a_missing_appointment_linkage_is_refused_pre_fence(self):
        fake = self.ready()
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            appointment=None, appointment_status=None
        )
        payload = encounters_write.complete(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_an_appointment_outside_open_or_checked_in_is_refused_pre_fence(self):
        for status in ("Scheduled", "Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                fake = self.ready()
                fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
                    appointment_status=status
                )
                payload = encounters_write.complete(fake, complete_body(), CSRF)
                self.assertEqual(payload, {"code": "WRITE_LOCKED"})
                self.assertEqual(fake.encounter_fence_calls, [])
                self.assertEqual(fake.submit_encounter_calls, [])

    def test_the_required_before_complete_set_is_enforced_pre_fence(self):
        for field, value in (
            ("complaint", False),
            ("diagnosis_count", 0),
            ("assessment", ""),
            ("plan", ""),
            ("followup", ""),
        ):
            with self.subTest(field=field):
                fake = self.ready()
                fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
                    **{field: value}
                )
                payload = encounters_write.complete(fake, complete_body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.encounter_fence_calls, [])

    def test_the_allergy_acknowledgement_is_required(self):
        fake = self.ready()
        payload = encounters_write.complete(
            fake, complete_body(allergy_reviewed=False), CSRF
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_a_cascade_mismatch_rolls_back_and_does_not_commit(self):
        fake = self.ready()
        fake.submitted_service_requests = 1
        payload = encounters_write.complete(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_claim.committed, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_nurse_never_completes(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        with self.assertRaises(Denied):
            encounters_write.complete(fake, complete_body(), CSRF)
        self.assertEqual(fake.encounter_fence_calls, [])

    def test_complete_budget_is_single(self):
        fake = self.ready()
        committed_encounter(fake, "encounter_complete", "enc-complete-key-0000-01")
        payload = encounters_write.complete(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])


class AmendTests(unittest.TestCase):
    def finalized(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            docstatus=1, status="Completed"
        )
        return fake

    def test_creates_an_addendum_and_never_rewrites_the_original(self):
        fake = self.finalized()
        payload = encounters_write.amend(fake, amend_body(), CSRF)
        self.assertEqual(payload["status"], "amended")
        self.assertTrue(payload["addendum"].startswith("addendum_"))
        state, note_text, actor = fake.addendum_calls[0]
        self.assertIn("[clinic-ui amendment]", note_text)
        self.assertEqual(actor, ACTOR)
        # The preimage of the original note is what the gateway re-verifies.
        self.assertEqual(
            fake.amend_facts_calls[0][4], NOTE_MODIFIED
        )
        self.assertEqual(fake.encounter_fence_calls[0][1], "encounter_amend")
        self.assertEqual(fake.commit_calls, 1)

    def test_a_draft_is_not_amendable_pre_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write.amend(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])
        self.assertEqual(fake.addendum_calls, [])

    def test_a_rewritten_original_note_is_detected_and_not_committed(self):
        fake = self.finalized()
        fake.amend_facts = {
            "clinical_note_modified": "2026-11-14 09:59:00.000000"
        }
        payload = encounters_write.amend(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_claim.committed, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_amend_budget_is_single(self):
        fake = self.finalized()
        committed_encounter(fake, "encounter_amend", "enc-amend-key-0000-0001")
        payload = encounters_write.amend(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_fence_calls, [])


class AckTests(unittest.TestCase):
    def test_the_acknowledgement_binds_the_encounter_kind(self):
        fake = gateway()
        payload = encounters_write.acknowledge(
            fake,
            {
                "operation_key": COMPLETE_KEY,
                "kind": "encounter_complete",
                "ack_token": "a" * 64,
            },
            CSRF,
        )
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(fake.encounter_ack_calls[0][1], "encounter_complete")


if __name__ == "__main__":
    unittest.main()
