import unittest
from types import SimpleNamespace

from clinic_ui.runtime.visit_invoice_lookup import lookup_visit_invoice
from clinic_ui.runtime.frappe_gateway import Gateway


FIELDS = {
    "Patient Appointment": {"practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "invoiced": ("Check", None), "ref_sales_invoice": ("Link", "Sales Invoice")},
    "Healthcare Practitioner": {"user_id": ("Link", "User"), "status": ("Select", None)},
    "Sales Invoice": {"patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "items": ("Table", "Sales Invoice Item")},
    "Sales Invoice Item": {"reference_dt": ("Link", "DocType"),
        "reference_dn": ("Dynamic Link", "reference_dt")},
}


class Meta:
    def __init__(self, fields):
        self.fields = {name: SimpleNamespace(fieldtype=kind, options=options, permlevel=0)
                       for name, (kind, options) in fields.items()}
        self.masked = []
        self.levels = [0]
        self.parent_calls = []

    def get_field(self, name):
        return self.fields.get(name)

    def get_permlevel_access(self, operation, *, parenttype=None, user):
        self.parent_calls.append(("levels", parenttype, user))
        return self.levels

    def get_masked_fields(self, *, parenttype=None):
        self.parent_calls.append(("masks", parenttype))
        return [SimpleNamespace(fieldname=name) for name in self.masked]


class Frappe:
    def __init__(self):
        self.session = SimpleNamespace(user="doctor@example.test")
        self.meta = {name: Meta(fields) for name, fields in FIELDS.items()}
        self.denied = set()
        self.identities = ["DOC-1"]
        item = SimpleNamespace(reference_dt="Patient Appointment", reference_dn="APT-1",
                               parent="INV-1", parenttype="Sales Invoice", parentfield="items")
        self.docs = {
            ("Patient Appointment", "APT-1"): SimpleNamespace(name="APT-1", practitioner="DOC-1",
                patient="PAT-1", company="CO-1", invoiced=1, ref_sales_invoice="INV-1"),
            ("Healthcare Practitioner", "DOC-1"): SimpleNamespace(name="DOC-1",
                user_id="doctor@example.test", status="Active"),
            ("Sales Invoice", "INV-1"): SimpleNamespace(name="INV-1", patient="PAT-1",
                company="CO-1", docstatus=1, items=[item]),
        }

    def get_meta(self, doctype):
        return self.meta[doctype]

    def has_permission(self, doctype, *, ptype, doc, user):
        return ptype == "read" and (doctype, doc) not in self.denied and (doctype, doc) in self.docs

    def get_doc(self, doctype, name):
        return self.docs[(doctype, name)]

    def get_all(self, doctype, *, filters, pluck, limit_page_length):
        if (doctype, filters, pluck, limit_page_length) != (
                "Healthcare Practitioner", {"user_id": self.session.user, "status": "Active"}, "name", 2):
            raise AssertionError("unexpected identity query")
        return self.identities


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.frappe = Frappe()
        self.gateway = Gateway(self.frappe)

    def lookup(self, expected=None):
        return lookup_visit_invoice(self.gateway, "APT-1", expected)

    def test_exact_projection_and_gateway_delegates(self):
        self.assertEqual(self.lookup(), {"invoice_id": "INV-1"})
        self.assertEqual(self.gateway.view_visit_invoice("APT-1"), {"invoice_id": "INV-1"})
        self.assertEqual(self.gateway.view_submitted_visit_invoice("APT-1", "INV-1"), {"invoice_id": "INV-1"})
        self.assertIsNone(self.gateway.view_submitted_visit_invoice("APT-1", "OTHER"))
        self.assertIsNone(self.gateway.view_submitted_visit_invoice("APT-1", None))
        self.assertIn(("levels", "Sales Invoice", "doctor@example.test"),
                      self.frappe.meta["Sales Invoice Item"].parent_calls)
        self.assertIn(("masks", "Sales Invoice"), self.frappe.meta["Sales Invoice Item"].parent_calls)

    def test_identity_and_session_fail_closed(self):
        for user in ("Guest", "Administrator", "", None):
            with self.subTest(user=user):
                self.frappe.session.user = user
                self.assertIsNone(self.lookup())
        self.frappe.session.user = "doctor@example.test"
        for identities in ([], ["DOC-1", "DOC-2"], ["DOC-2"]):
            self.frappe.identities = identities
            self.assertIsNone(self.lookup())
        self.frappe.identities = ["DOC-1"]
        self.frappe.docs[("Healthcare Practitioner", "DOC-1")].status = "Inactive"
        self.assertIsNone(self.lookup())

    def test_document_and_field_permission_denials(self):
        for doctype, name in (("Patient Appointment", "APT-1"),
                              ("Healthcare Practitioner", "DOC-1"), ("Sales Invoice", "INV-1")):
            self.frappe.denied.add((doctype, name))
            self.assertIsNone(self.lookup())
            self.frappe.denied.clear()
        for doctype, field in (("Patient Appointment", "patient"),
                               ("Sales Invoice", "items"), ("Sales Invoice Item", "reference_dn")):
            meta = self.frappe.meta[doctype]
            meta.masked = [field]
            self.assertIsNone(self.lookup())
            meta.masked = []
            meta.fields[field].permlevel = 2
            self.assertIsNone(self.lookup())
            meta.fields[field].permlevel = 0

    def test_invoice_and_visit_mismatch_fail_closed(self):
        appointment = self.frappe.docs[("Patient Appointment", "APT-1")]
        invoice = self.frappe.docs[("Sales Invoice", "INV-1")]
        for obj, field, value in ((appointment, "invoiced", 0), (appointment, "invoiced", True),
                                  (appointment, "ref_sales_invoice", None), (invoice, "patient", "OTHER"),
                                  (invoice, "company", "OTHER"), (invoice, "docstatus", True),
                                  (invoice, "docstatus", 0)):
            old = getattr(obj, field)
            setattr(obj, field, value)
            self.assertIsNone(self.lookup())
            setattr(obj, field, old)
        item = invoice.items[0]
        for field, value in (("reference_dt", "Patient Encounter"), ("reference_dn", "OTHER"),
                             ("parent", "OTHER"), ("parenttype", "OTHER"), ("parentfield", "other")):
            old = getattr(item, field)
            setattr(item, field, value)
            self.assertIsNone(self.lookup())
            setattr(item, field, old)
        invoice.items = [item, item]
        self.assertIsNone(self.lookup())

    def test_submitted_docstatus_int_subclass_is_accepted_but_bool_is_not(self):
        class DocStatus(int):
            pass
        invoice = self.frappe.docs[("Sales Invoice", "INV-1")]
        invoice.docstatus = DocStatus(1)
        self.assertEqual(self.lookup(), {"invoice_id": "INV-1"})
        invoice.docstatus = True
        self.assertIsNone(self.lookup())

    def test_missing_records_or_schema_fail_closed(self):
        del self.frappe.docs[("Sales Invoice", "INV-1")]
        self.assertIsNone(self.lookup())
        self.frappe.meta["Patient Appointment"].fields.pop("patient")
        self.assertIsNone(self.lookup())
