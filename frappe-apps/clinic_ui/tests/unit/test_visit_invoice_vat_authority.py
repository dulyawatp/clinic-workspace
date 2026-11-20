"""Server-only No VAT authority over site, actor, classification and visit.

The caller supplies only the appointment identifier. Everything else is server
session state, site configuration or native evidence re-read through the
existing pure resolver. Any fault, ambiguity or missing positive evidence
denies the mode.
"""

import unittest
from decimal import Decimal

from clinic_ui.core.policy import SessionView
from clinic_ui.runtime.visit_invoice_vat_authority import (
    MAX_NO_VAT_APPOINTMENTS,
    no_vat_allowed,
)

SITE = "clinic-visit-invoice.local"
DOCTOR = "doctor@example.test"
APPOINTMENT = "NATIVE-APPT-3"


def session(user=DOCTOR, sid="sid-abc123"):
    return SessionView(
        sid=sid,
        user=user,
        full_name="Doctor Example",
        enabled=True,
        roles=("Physician",),
    )


def evidence(**overrides):
    values = {
        "metadata_verified": True,
        "record_fields_readable": True,
        "billing_history_complete": True,
        "prior_billing": False,
        "appointment_id": APPOINTMENT,
        "practitioner": "DOC-1",
        "actor_practitioner": "DOC-1",
        "status": "Open",
        "docstatus": 0,
        "encounter_id": None,
        "invoiced": 0,
        "ref_sales_invoice": None,
        "patient": "PAT-1",
        "customer": "CUST-1",
        "company": "COMP-1",
        "currency": "THB",
        "service": "ITEM-1",
        "price": Decimal("100"),
    }
    values.update(overrides)
    return values


class Gateway:
    def __init__(self):
        self.site = SITE
        self.view = session()
        self.config = {
            "clinic_ui_visit_invoice_no_vat_user": DOCTOR,
            "clinic_ui_visit_invoice_no_vat_appointments": [APPOINTMENT],
        }
        self.facts = evidence()
        self.configuration_error = None
        self.evidence_error = None

    @property
    def current_session(self):
        return self.view

    def _configuration(self):
        if self.configuration_error is not None:
            raise self.configuration_error
        return self.config

    def visit_invoice_evidence(self, appointment_id):
        if self.evidence_error is not None:
            raise self.evidence_error
        return self.facts


class NoVatAuthorityTests(unittest.TestCase):
    def test_allows_the_exact_doctor_on_the_classified_eligible_appointment(self):
        self.assertIs(no_vat_allowed(Gateway(), APPOINTMENT), True)

    def test_requires_the_exact_synthetic_site(self):
        for site in (
            "marley-sit.local",
            "clinic-visit-invoice.local.evil",
            "CLINIC-VISIT-INVOICE.LOCAL",
            " clinic-visit-invoice.local",
            "clinic-visit-invoice.local ",
            "",
            None,
        ):
            gateway = Gateway()
            gateway.site = site
            with self.subTest(site=repr(site)):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_the_non_guest_non_administrator_session_actor(self):
        for actor in (
            "Administrator",
            "Guest",
            DOCTOR.upper(),
            "other@example.test",
            "",
            None,
        ):
            gateway = Gateway()
            gateway.view = session(user=actor)
            with self.subTest(actor=repr(actor)):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_a_usable_session(self):
        for sid in ("", " ", None):
            gateway = Gateway()
            gateway.view = session(sid=sid)
            with self.subTest(sid=repr(sid)):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)
        gateway = Gateway()
        gateway.view = None
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_the_matching_server_configured_user(self):
        for configured in (
            None,
            "",
            " ",
            DOCTOR.upper(),
            "Administrator",
            "Guest",
            "other@example.test",
            DOCTOR + " ",
            1,
            [DOCTOR],
        ):
            gateway = Gateway()
            gateway.config["clinic_ui_visit_invoice_no_vat_user"] = configured
            with self.subTest(configured=repr(configured)):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_an_explicit_unique_well_formed_classification(self):
        cases = {
            "missing": None,
            "string": APPOINTMENT,
            "empty": [],
            "wildcard": ["*"],
            "parent": ["NATIVE-APPT"],
            "prefix": [APPOINTMENT[:-1]],
            "suffix": [APPOINTMENT + "1"],
            "cased": [APPOINTMENT.lower()],
            "duplicate": [APPOINTMENT, APPOINTMENT],
            "non-string": [1],
            "blank": [" "],
            "padded": [APPOINTMENT + " "],
            "control": ["NATIVE\u0000APPT"],
            "other-only": ["NATIVE-APPT-2"],
            "unbounded": [
                f"NATIVE-APPT-{index}" for index in range(MAX_NO_VAT_APPOINTMENTS + 1)
            ],
        }
        for name, value in cases.items():
            gateway = Gateway()
            if name == "missing":
                del gateway.config["clinic_ui_visit_invoice_no_vat_appointments"]
            else:
                gateway.config["clinic_ui_visit_invoice_no_vat_appointments"] = value
            with self.subTest(case=name):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_the_native_visit_eligibility_preflight(self):
        for key, value in (
            ("metadata_verified", False),
            ("record_fields_readable", False),
            ("billing_history_complete", False),
            ("prior_billing", True),
            ("appointment_id", "NATIVE-APPT-2"),
            ("actor_practitioner", "DOC-2"),
            ("status", "Cancelled"),
            ("docstatus", 2),
            ("invoiced", 1),
            ("ref_sales_invoice", "INV-1"),
            ("patient", None),
            ("price", Decimal("NaN")),
            ("price", Decimal("0")),
        ):
            gateway = Gateway()
            gateway.facts = evidence(**{key: value})
            with self.subTest(key=key, value=repr(value)):
                self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_requires_the_emptiness_proof_for_prior_billing(self):
        gateway = Gateway()
        del gateway.facts["ref_sales_invoice"]
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)

    def test_closed_visit_requires_its_encounter(self):
        gateway = Gateway()
        gateway.facts = evidence(status="Closed")
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)
        gateway.facts = evidence(status="Closed", encounter_id="ENC-1")
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), True)

    def test_fails_closed_on_faults_and_unusable_input(self):
        gateway = Gateway()
        gateway.configuration_error = PermissionError()
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)
        gateway = Gateway()
        gateway.config = None
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)
        gateway = Gateway()
        gateway.evidence_error = PermissionError()
        self.assertIs(no_vat_allowed(gateway, APPOINTMENT), False)
        self.assertIs(no_vat_allowed(None, APPOINTMENT), False)
        self.assertIs(no_vat_allowed(object(), APPOINTMENT), False)
        for appointment_id in (None, "", " ", APPOINTMENT + " ", 1, [APPOINTMENT]):
            gateway = Gateway()
            with self.subTest(appointment_id=repr(appointment_id)):
                self.assertIs(no_vat_allowed(gateway, appointment_id), False)
