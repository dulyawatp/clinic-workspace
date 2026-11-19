"""Non-installed Frappe fakes: these tests do not prove site configuration."""
import unittest
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from clinic_ui.runtime.visit_invoice_native import visit_invoice_evidence
from clinic_ui.runtime.visit_invoice import resolve_visit_invoice
from clinic_ui.runtime.frappe_gateway import Gateway as NativeGateway


SCHEMA = {
    "Patient Appointment": {
        "appointment_for": ("Select", None), "appointment_date": ("Date", None),
        "status": ("Select", None), "practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "invoiced": ("Check", None), "ref_sales_invoice": ("Link", "Sales Invoice"),
        "paid_amount": ("Currency", None), "mode_of_payment": ("Link", "Mode of Payment"),
        "inpatient_record": ("Link", "Inpatient Record"),
        "appointment_type": ("Link", "Appointment Type"),
        "template_dt": ("Link", "DocType"), "template_dn": ("Dynamic Link", "template_dt"),
        "procedure_template": ("Link", "Clinical Procedure Template"),
        "procedure_prescription": ("Link", "Procedure Prescription"),
        "therapy_type": ("Link", "Therapy Type"), "therapy_plan": ("Link", "Therapy Plan"),
        "service_request": ("Link", "Service Request"),
        "insurance_payor": ("Link", "Insurance Payor"),
        "insurance_policy": ("Link", "Patient Insurance Policy"),
        "insurance_coverage": ("Link", "Patient Insurance Coverage"),
    },
    "Healthcare Practitioner": {
        "user_id": ("Link", "User"), "status": ("Select", None),
        "enable_free_follow_ups": ("Check", None),
        "op_consulting_charge_item": ("Link", "Item"), "op_consulting_charge": ("Currency", "Currency"),
    },
    "Patient": {"customer": ("Link", "Customer")},
    "Customer": {}, "Company": {"default_currency": ("Link", "Currency")},
    "Item": {"disabled": ("Check", None), "is_sales_item": ("Check", None)},
    "Sales Invoice Item": {"reference_dt": ("Link", "DocType"), "reference_dn": ("Dynamic Link", "reference_dt")},
    "Sales Invoice": {"patient": ("Link", "Patient")},
    "Patient Encounter": {
        "appointment": ("Link", "Patient Appointment"), "practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "status": ("Select", None), "invoiced": ("Check", None),
    },
    "Healthcare Settings": {"enable_free_follow_ups": ("Check", None)},
}


class Meta:
    def __init__(self, fields):
        self.fields = {key: SimpleNamespace(fieldtype=kind, options=options, permlevel=0)
                       for key, (kind, options) in fields.items()}

    def get_field(self, name):
        return self.fields.get(name)


class DB:
    def __init__(self):
        self.hits = set()
        self.calls = []
        self.broken = set()
        self.encounters = []
        self.encounter_invoice = False
        self.fail_encounter_invoice = False
        self.fail_second_encounter = False
        self.setting_rows = [("0",)]

    def exists(self, doctype, filters):
        self.calls.append((doctype, filters))
        return doctype in self.hits

    def get_value(self, doctype, filters, fieldname, *, ignore, order_by):
        self.calls.append((doctype, filters))
        if ignore is not False or order_by is not None or fieldname != "name":
            raise AssertionError("history lookup must be strict and bounded")
        if doctype in self.broken:
            raise RuntimeError("missing table or column")
        if doctype == "Patient Encounter":
            if "name" in filters and self.fail_second_encounter:
                raise RuntimeError("second encounter lookup failed")
            excluded = filters.get("name", [None, None])[1]
            return next((name for name in self.encounters if name != excluded), None)
        if doctype == "Sales Invoice Item" and filters.get("reference_dt") == "Patient Encounter":
            if self.fail_encounter_invoice:
                raise RuntimeError("encounter invoice lookup failed")
            return "hidden" if self.encounter_invoice else None
        return "hidden" if doctype in self.hits else None

    def get_single_value(self, doctype, fieldname):
        if doctype != "Healthcare Settings" or fieldname != "enable_free_follow_ups":
            raise AssertionError("unexpected setting")
        if isinstance(self.setting_rows, Exception):
            raise self.setting_rows
        try:
            return int(self.setting_rows[0][0])
        except (IndexError, TypeError, ValueError):
            return 0

    def sql(self, query, values):
        if (query != "SELECT value FROM tabSingles WHERE doctype=%s AND field=%s LIMIT 2"
                or values != ("Healthcare Settings", "enable_free_follow_ups")):
            raise AssertionError("unexpected setting query")
        if isinstance(self.setting_rows, Exception):
            raise self.setting_rows
        return self.setting_rows


class Frappe:
    def __init__(self):
        self.session = SimpleNamespace(user="doctor@example.test")
        self.utils = SimpleNamespace(today=lambda: "2026-11-20")
        self.meta = {doctype: Meta(fields) for doctype, fields in SCHEMA.items()}
        self.db = DB()
        self.allowed = True
        self.denied = set()
        self.identities = ["PRAC-1"]
        self.docs = {
            ("Patient Appointment", "APT-1"): SimpleNamespace(
                name="APT-1", appointment_for="Practitioner", appointment_date=date(2026, 11, 20),
                status="Open", docstatus=0, practitioner="PRAC-1", patient="PAT-1",
                company="CO-1", invoiced=0, ref_sales_invoice=None, paid_amount=0,
                mode_of_payment=None, inpatient_record=None, appointment_type="CONSULT",
                template_dt=None, template_dn=None, procedure_template=None,
                procedure_prescription=None, therapy_type=None, therapy_plan=None,
                service_request=None, insurance_payor=None, insurance_policy=None,
                insurance_coverage=None),
            ("Healthcare Practitioner", "PRAC-1"): SimpleNamespace(
                name="PRAC-1", user_id="doctor@example.test", status="Active",
                enable_free_follow_ups=0,
                op_consulting_charge_item="SVC-1", op_consulting_charge=100),
            ("Patient", "PAT-1"): SimpleNamespace(name="PAT-1", customer="CUS-1"),
            ("Customer", "CUS-1"): SimpleNamespace(name="CUS-1"),
            ("Company", "CO-1"): SimpleNamespace(name="CO-1", default_currency="USD"),
            ("Item", "SVC-1"): SimpleNamespace(name="SVC-1", disabled=0, is_sales_item=1),
        }

    def get_meta(self, doctype):
        return self.meta[doctype]

    def has_permission(self, doctype, ptype, doc, user):
        return self.allowed and (doctype, doc) not in self.denied and (doctype, doc) in self.docs

    def get_doc(self, doctype, name):
        return self.docs[(doctype, name)]

    def get_all(self, doctype, filters, pluck, limit_page_length):
        return self.identities[:limit_page_length]


class Gateway:
    def __init__(self):
        self.frappe = Frappe()
        self.unreadable = set()

    def _fields_readable(self, meta, fields):
        return all(meta.get_field(field) is not None and field not in self.unreadable for field in fields)

    def visit_invoice_evidence(self, appointment_id):
        return visit_invoice_evidence(self, appointment_id)


class NativeEvidenceTests(unittest.TestCase):
    def test_practitioner_free_followups_must_be_readable_and_exactly_disabled(self):
        gateway = Gateway()
        self.assertIsNotNone(visit_invoice_evidence(gateway, "APT-1"))
        for value in (1, True, False, None, "0"):
            gateway = Gateway()
            gateway.frappe.docs[("Healthcare Practitioner", "PRAC-1")].enable_free_follow_ups = value
            with self.subTest(value=value):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        gateway.unreadable.add("enable_free_follow_ups")
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        del gateway.frappe.meta["Healthcare Practitioner"].fields["enable_free_follow_ups"]
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        gateway.frappe.meta["Healthcare Practitioner"].fields["enable_free_follow_ups"].fieldtype = "Data"
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_free_followups_must_be_observed_disabled(self):
        for rows in ([], [("bad",)], [("1",)], [(0,)], [(False,)],
                     [("0",), ("0",)], RuntimeError("unreadable")):
            gateway = Gateway()
            gateway.frappe.db.setting_rows = rows
            with self.subTest(rows=repr(rows)):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        del gateway.frappe.meta["Healthcare Settings"].fields["enable_free_follow_ups"]
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def closed_gateway(self):
        gateway = Gateway()
        gateway.frappe.docs[("Patient Appointment", "APT-1")].status = "Closed"
        gateway.frappe.db.encounters = ["ENC-1"]
        gateway.frappe.docs[("Patient Encounter", "ENC-1")] = SimpleNamespace(
            name="ENC-1", appointment="APT-1", practitioner="PRAC-1", patient="PAT-1",
            company="CO-1", status="Completed", docstatus=1, invoiced=0)
        return gateway

    def test_closed_visit_resolves_exact_encounter(self):
        gateway = self.closed_gateway()
        self.assertEqual(resolve_visit_invoice(gateway, "APT-1")["encounter_id"], "ENC-1")

    def test_open_with_encounter_or_closed_with_zero_or_multiple_refused(self):
        for status, encounters in (("Open", ["ENC-1"]), ("Closed", []),
                                   ("Closed", ["ENC-1", "ENC-2"])):
            gateway = self.closed_gateway()
            gateway.frappe.docs[("Patient Appointment", "APT-1")].status = status
            gateway.frappe.db.encounters = encounters
            with self.subTest(status=status, encounters=encounters):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_encounter_disagreements_and_inaccessibility_refused(self):
        for field, value in (("appointment", "APT-2"), ("practitioner", "PRAC-2"),
                             ("patient", "PAT-2"), ("company", "CO-2"),
                             ("status", "Cancelled"), ("docstatus", 2), ("docstatus", False),
                             ("invoiced", 1), ("invoiced", False)):
            gateway = self.closed_gateway()
            setattr(gateway.frappe.docs[("Patient Encounter", "ENC-1")], field, value)
            with self.subTest(field=field, value=value):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = self.closed_gateway()
        gateway.frappe.denied.add(("Patient Encounter", "ENC-1"))
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = self.closed_gateway()
        del gateway.frappe.docs[("Patient Encounter", "ENC-1")]
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = self.closed_gateway()
        gateway.unreadable.add("appointment")
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_encounter_history_hits_and_query_failures_refused(self):
        gateway = self.closed_gateway()
        gateway.frappe.db.encounter_invoice = True
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = self.closed_gateway()
        gateway.frappe.db.broken.add("Patient Encounter")
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        for field in ("fail_encounter_invoice", "fail_second_encounter"):
            gateway = self.closed_gateway()
            setattr(gateway.frappe.db, field, True)
            with self.subTest(field=field):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_consultation_discriminators_and_metadata(self):
        fields = ("appointment_type", "template_dt", "template_dn", "procedure_template",
                  "procedure_prescription", "therapy_type", "therapy_plan", "service_request",
                  "insurance_payor", "insurance_policy", "insurance_coverage")
        for field in fields:
            gateway = Gateway()
            gateway.unreadable.add(field)
            with self.subTest(unreadable=field):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
            gateway = Gateway()
            del gateway.frappe.meta["Patient Appointment"].fields[field]
            with self.subTest(missing=field):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        for field in fields[3:]:
            gateway = Gateway()
            setattr(gateway.frappe.docs[("Patient Appointment", "APT-1")], field, "REF-1")
            with self.subTest(marker=field):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
                self.assertEqual(gateway.frappe.db.calls, [])

    def test_appointment_type_template_must_match(self):
        for template_dt, template_dn, expected in (
            (None, None, True), ("Appointment Type", "CONSULT", True),
            ("Appointment Type", "OTHER", False), ("Appointment Type", None, False),
            (None, "CONSULT", False), ("Clinical Procedure Template", "CONSULT", False),
        ):
            gateway = Gateway()
            appointment = gateway.frappe.docs[("Patient Appointment", "APT-1")]
            appointment.template_dt, appointment.template_dn = template_dt, template_dn
            with self.subTest(template_dt=template_dt, template_dn=template_dn):
                self.assertEqual(visit_invoice_evidence(gateway, "APT-1") is not None, expected)

    def test_native_docstatus_subclass_normalized_for_real_gateway(self):
        class DocStatus(int):
            pass

        fake = Gateway()
        appointment = fake.frappe.docs[("Patient Appointment", "APT-1")]
        appointment.docstatus = DocStatus(0)
        native_evidence = visit_invoice_evidence(fake, "APT-1")
        self.assertIsNotNone(native_evidence)
        self.assertIs(type(native_evidence["docstatus"]), int)
        gateway = NativeGateway(fake.frappe)
        gateway._fields_readable = fake._fields_readable
        evidence = gateway.visit_invoice_evidence("APT-1")
        self.assertIs(type(evidence["docstatus"]), int)
        self.assertEqual(evidence["docstatus"], 0)
        self.assertIsNotNone(resolve_visit_invoice(gateway, "APT-1"))
        for status in (True, False, DocStatus(1), 0.0, Decimal("0")):
            appointment.docstatus = status
            with self.subTest(status=repr(status)):
                self.assertIsNone(gateway.visit_invoice_evidence("APT-1"))

    def test_verified_appointment_produces_pure_resolver_inputs(self):
        gateway = Gateway()
        result = resolve_visit_invoice(gateway, "APT-1")
        self.assertEqual(result["price"], Decimal("100"))
        self.assertEqual(result["currency"], "USD")
        self.assertIn(("Sales Invoice Item",
                       {"reference_dt": "Patient Appointment", "reference_dn": "APT-1"}),
                      gateway.frappe.db.calls)

    def test_visit_scope_and_identity_fail_closed(self):
        for field, value in (("appointment_for", "Department"), ("status", "Closed"),
                             ("docstatus", False), ("appointment_date", "2026-11-19"),
                             ("practitioner", None), ("inpatient_record", "INP-1"),
                             ("mode_of_payment", "Cash"), ("paid_amount", 99)):
            gateway = Gateway()
            setattr(gateway.frappe.docs[("Patient Appointment", "APT-1")], field, value)
            with self.subTest(field=field):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
                self.assertFalse(any(doctype.startswith("Sales Invoice")
                                     for doctype, _ in gateway.frappe.db.calls))
        for identities in ([], ["PRAC-2"], ["PRAC-1", "PRAC-2"]):
            gateway = Gateway()
            gateway.frappe.identities = identities
            self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
            self.assertEqual(gateway.frappe.db.calls, [])

    def test_permissions_fields_and_drift_fail_closed(self):
        gateway = Gateway()
        gateway.frappe.allowed = False
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        self.assertEqual(gateway.frappe.db.calls, [])
        for field in ("customer", "user_id", "default_currency", "disabled", "paid_amount"):
            gateway = Gateway()
            gateway.unreadable.add(field)
            self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        gateway.frappe.meta["Sales Invoice Item"].fields["reference_dn"].options = "wrong"
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_history_always_blocks_and_optional_header_is_checked(self):
        for doctype in ("Sales Invoice Item", "Sales Invoice"):
            gateway = Gateway()
            gateway.frappe.meta["Sales Invoice"].fields["appointment"] = SimpleNamespace(
                fieldtype="Link", options="Patient Appointment", permlevel=0)
            gateway.frappe.db.hits.add(doctype)
            self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
        gateway = Gateway()
        gateway.frappe.meta["Sales Invoice"].fields["appointment"] = SimpleNamespace(
            fieldtype="Data", options=None, permlevel=0)
        self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_history_query_errors_do_not_mean_unbilled(self):
        for doctype in ("Sales Invoice Item", "Sales Invoice"):
            gateway = Gateway()
            gateway.frappe.meta["Sales Invoice"].fields["appointment"] = SimpleNamespace(
                fieldtype="Link", options="Patient Appointment", permlevel=0)
            gateway.frappe.db.broken.add(doctype)
            with self.subTest(doctype=doctype):
                self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))

    def test_equal_paid_amount_and_invalid_pricing(self):
        gateway = Gateway()
        gateway.frappe.docs[("Patient Appointment", "APT-1")].paid_amount = "100.00"
        self.assertIsNotNone(visit_invoice_evidence(gateway, "APT-1"))
        for value in (None, 0, -1, "NaN"):
            gateway = Gateway()
            gateway.frappe.docs[("Healthcare Practitioner", "PRAC-1")].op_consulting_charge = value
            self.assertIsNone(visit_invoice_evidence(gateway, "APT-1"))
