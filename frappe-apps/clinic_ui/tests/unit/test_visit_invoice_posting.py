import copy
import unittest
from types import SimpleNamespace

from clinic_ui.runtime.visit_invoice_posting import (
    PostingPreDispatch, PostingPostDispatch,
    check_posting_permissions, post_visit_invoice,
)
from clinic_ui.runtime.frappe_gateway import Gateway


class PermissionFake:
    def __init__(self, denied=()):
        self.denied = denied
        self.calls = []
        self.session = SimpleNamespace(user="doctor")

    def has_permission(self, doctype, ptype, doc, user):
        self.calls.append((doctype, ptype, doc, user))
        return ptype not in self.denied


class PostingPermissionTest(unittest.TestCase):
    def test_gateway_calls_native_lifecycle_without_flags(self):
        calls = []
        doc = SimpleNamespace(insert=lambda: calls.append("insert"),
                              submit=lambda: calls.append("submit"))
        gateway = Gateway()
        gateway.insert_visit_invoice(doc)
        gateway.submit_visit_invoice(doc)
        self.assertEqual(calls, ["insert", "submit"])

    def test_checks_populated_record_for_create_and_submit(self):
        doc = SimpleNamespace(doctype="Sales Invoice", docstatus=0, customer="C", flags={})
        frappe = PermissionFake()
        check_posting_permissions(SimpleNamespace(frappe=frappe), doc)
        self.assertEqual(frappe.calls, [
            ("Sales Invoice", "create", doc, "doctor"),
            ("Sales Invoice", "submit", doc, "doctor"),
        ])

    def test_denial_precedes_mutation(self):
        for denied in ("create", "submit"):
            with self.subTest(denied=denied):
                doc = SimpleNamespace(doctype="Sales Invoice", docstatus=0, customer="C", flags={})
                with self.assertRaises(PostingPreDispatch):
                    check_posting_permissions(SimpleNamespace(frappe=PermissionFake((denied,))), doc)

    def test_rejects_permission_bypass_flags_and_guest(self):
        for flags, user in (({"ignore_permissions": True}, "doctor"), ({}, "Guest")):
            with self.subTest(flags=flags, user=user):
                doc = SimpleNamespace(doctype="Sales Invoice", docstatus=0, flags=flags)
                frappe = PermissionFake()
                frappe.session.user = user
                with self.assertRaises(PostingPreDispatch):
                    check_posting_permissions(SimpleNamespace(frappe=frappe), doc)
                self.assertEqual(frappe.calls, [])

    def test_accepts_docstatus_int_subclass(self):
        class DocStatus(int):
            pass
        doc = SimpleNamespace(doctype="Sales Invoice", docstatus=DocStatus(0), flags={})
        check_posting_permissions(SimpleNamespace(frappe=PermissionFake()), doc)


class PostingLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.visit = dict(appointment_id="A", patient="P", customer="C", company="Co",
                          currency="USD", service="S", price="100.00")
        self.accounting = dict(uom="Each", income_account="Income", receivable_account="AR",
                               cost_center="CC", posting_date="2026-11-20", selling_price_list="Standard",
                               vat_mode="no_vat", tax_template=None, tax_rows=(),
                               tax_account=None, tax_rate=None)
        self.totals = dict(currency="USD", gross="100.00", discount="10.00",
                           net="90.00", vat="0.00", total="90.00")
        self.discount = dict(mode="amount", value="10.00")
        self.store = {}
        self.events = []
        self.mutate = lambda store: None
        self.gl = [
            ("Sales Invoice", "INV", "Co", "2026-11-20", "AR", "USD", "Customer", "C", 90, 0, 90, 0, 0, "Sales Invoice", "INV", None),
            ("Sales Invoice", "INV", "Co", "2026-11-20", "Income", "USD", None, None, 0, 90, 0, 90, 0, None, None, "CC"),
        ]
        self.stock = []
        owner = self

        class Doc(SimpleNamespace):
            def insert(self):
                owner.events.append("insert")
                self.name = "INV"
                owner.store["invoice"] = copy.deepcopy(self)

            def submit(self):
                owner.events.append("submit")
                self.docstatus = 1
                owner.store["invoice"] = copy.deepcopy(self)
                owner.store["appointment"] = SimpleNamespace(name="A", invoiced=1, ref_sales_invoice="INV")
                owner.mutate(owner.store)

        self.doc = Doc(
            doctype="Sales Invoice", docstatus=0, flags={}, name=None,
            customer="C", patient="P", company="Co", currency="USD",
            posting_date="2026-11-20", set_posting_time=0, selling_price_list="Standard",
            price_list_currency="USD", plc_conversion_rate=1,
            party_account_currency="USD", debit_to="AR", conversion_rate=1,
            is_pos=0, is_return=0, update_stock=0, taxes_and_charges=None, taxes=[], advances=[], payments=[],
            total=100, base_total=100, net_total=90, base_net_total=90,
            grand_total=90, base_grand_total=90, discount_amount=10,
            base_discount_amount=10, outstanding_amount=90, rounded_total=90,
            base_rounded_total=90, rounding_adjustment=0, base_rounding_adjustment=0,
            total_taxes_and_charges=0, base_total_taxes_and_charges=0,
            total_advance=0, paid_amount=0, base_paid_amount=0, write_off_amount=0,
            base_write_off_amount=0, change_amount=0, base_change_amount=0,
            loyalty_points=0, loyalty_amount=0, redeem_loyalty_points=0,
            allocate_advances_automatically=0, write_off_outstanding_amount_automatically=0,
            apply_discount_on="Net Total", additional_discount_percentage=0,
            items=[SimpleNamespace(item_code="S", qty=1, uom="Each", stock_uom="Each",
                    income_account="Income", cost_center="CC", conversion_factor=1, reference_dt="Patient Appointment",
                    reference_dn="A", rate=100, price_list_rate=100, amount=100,
                    base_amount=100, net_amount=90, base_net_amount=90,
                    distributed_discount_amount=10)])

        class Frappe(PermissionFake):
            def has_permission(self, doctype, ptype, doc, user):
                owner.events.append((doctype, ptype))
                return super().has_permission(doctype, ptype, doc, user)

            def get_doc(self, doctype, name):
                owner.events.append(("read", doctype))
                return copy.deepcopy(owner.store["invoice" if doctype == "Sales Invoice" else "appointment"])

            def get_meta(self, doctype):
                kinds = {
                    "voucher_type": ("Link", "DocType"), "voucher_no": ("Dynamic Link", "voucher_type"),
                    "company": ("Link", "Company"), "posting_date": ("Date", None),
                    "account": ("Link", "Account"), "account_currency": ("Link", "Currency"),
                    "cost_center": ("Link", "Cost Center"),
                    "party_type": ("Link", "DocType"), "party": ("Dynamic Link", "party_type"),
                    "debit": ("Currency", "Company:company:default_currency"),
                    "credit": ("Currency", "Company:company:default_currency"),
                    "debit_in_account_currency": ("Currency", "account_currency"),
                    "credit_in_account_currency": ("Currency", "account_currency"),
                    "is_cancelled": ("Check", None),
                    "against_voucher_type": ("Link", "DocType"),
                    "against_voucher": ("Dynamic Link", "against_voucher_type"),
                }
                def field(name):
                    kind, options = kinds.get(name, ("Data", None))
                    return SimpleNamespace(fieldname=name, fieldtype=kind, options=options, permlevel=0)
                return SimpleNamespace(get_field=field,
                    get_permlevel_access=lambda permission, parenttype=None, user=None:
                        [0] if parenttype == "Sales Invoice" else [],
                    get_masked_fields=lambda parenttype=None: [])

        self.frappe = Frappe()
        def sql(query, values):
            self.events.append(("sql", query, values))
            if "tabGL Entry" in query:
                return self.gl[:4]
            if "tabStock Ledger Entry" in query:
                return self.stock[:1]
            raise AssertionError(query)
        self.frappe.db = SimpleNamespace(sql=sql)
        def insert_invoice(doc):
            self.events.append("gateway insert")
            doc.insert()
        def submit_invoice(doc):
            self.events.append("gateway submit")
            doc.submit()
        self.gateway = SimpleNamespace(
            frappe=self.frappe, _fields_readable=lambda meta, fields: True,
            insert_visit_invoice=insert_invoice, submit_visit_invoice=submit_invoice,
        )

    def post(self):
        return post_visit_invoice(self.gateway, self.doc, self.visit, self.accounting,
                                  self.totals, self.discount)

    def test_native_lifecycle_verifies_ledger_before_return(self):
        self.assertEqual(self.post(), "INV")
        self.assertIn("insert", self.events)
        self.assertIn("submit", self.events)
        self.assertIn("gateway insert", self.events)
        self.assertIn("gateway submit", self.events)
        self.assertGreater(self.events.index("submit"), self.events.index("insert"))
        self.assertGreater(self.events.count(("read", "Sales Invoice")), 1)
        queries = [e for e in self.events if isinstance(e, tuple) and e[0] == "sql"]
        self.assertEqual(len(queries), 2)
        self.assertEqual(queries[0][2], ("Sales Invoice", "INV"))
        self.assertEqual(queries[1][2], ("Sales Invoice", "INV"))
        self.assertIn("WHERE voucher_type = %s AND voucher_no = %s LIMIT 4", queries[0][1])
        self.assertIn("WHERE voucher_type = %s AND voucher_no = %s LIMIT 1", queries[1][1])

    def test_submit_denied_before_insert(self):
        self.frappe.denied = ("submit",)
        with self.assertRaises(PostingPreDispatch):
            self.post()
        self.assertNotIn("insert", self.events)

    def test_persisted_drift_requires_caller_rollback(self):
        for field, value in (("outstanding_amount", 80), ("patient", "other")):
            with self.subTest(field=field):
                self.setUp()
                self.mutate = lambda store: setattr(store["invoice"], field, value)
                with self.assertRaises(PostingPostDispatch):
                    self.post()
                self.assertIn("submit", self.events)

    def test_bad_appointment_marker_requires_caller_rollback(self):
        self.mutate = lambda store: setattr(store["appointment"], "ref_sales_invoice", "wrong")
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_wrong_child_reference_requires_caller_rollback(self):
        self.mutate = lambda store: setattr(store["invoice"].items[0], "reference_dn", "wrong")
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_saved_draft_submit_denial_requires_caller_rollback(self):
        original = self.frappe.has_permission
        calls = []

        def permission(doctype, ptype, doc, user):
            if ptype == "submit":
                calls.append(doc)
                if len(calls) == 2:
                    return False
            return original(doctype, ptype, doc, user)

        self.frappe.has_permission = permission
        with self.assertRaises(PostingPostDispatch):
            self.post()
        self.assertIn("insert", self.events)
        self.assertNotIn("submit", self.events)

    def test_saved_draft_write_denial_requires_rollback_before_submit(self):
        self.frappe.denied = ("write",)
        with self.assertRaises(PostingPostDispatch):
            self.post()
        self.assertIn("insert", self.events)
        self.assertIn(("Sales Invoice", "write"), self.events)
        self.assertNotIn("gateway submit", self.events)

    def test_saved_draft_write_permission_checked_on_loaded_record(self):
        self.post()
        checks = [doc for doctype, permission, doc, user in self.frappe.calls
                  if doctype == "Sales Invoice" and permission == "write"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0].name, "INV")
        self.assertIsNot(checks[0], self.doc)

    def test_unexpected_stock_adjustment_requires_caller_rollback(self):
        self.mutate = lambda store: setattr(store["invoice"], "update_stock", 1)
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_ledger_drift_requires_rollback(self):
        cases = [
            [], [self.gl[0]], self.gl + [self.gl[0]],
            [self.gl[0][:12] + (1,) + self.gl[0][13:], self.gl[1]],
            [self.gl[0][:8] + (89,) + self.gl[0][9:], self.gl[1]],
            [self.gl[0][:7] + ("wrong",) + self.gl[0][8:], self.gl[1]],
            [self.gl[0][:14] + ("OTHER",) + self.gl[0][15:], self.gl[1]],
        ]
        for rows in cases:
            with self.subTest(rows=rows):
                self.setUp()
                self.gl = rows
                with self.assertRaises(PostingPostDispatch):
                    self.post()

    def test_stock_ledger_presence_requires_rollback(self):
        self.stock = [("SLE-1",)]
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_sql_errors_require_rollback(self):
        def fail(*args):
            raise RuntimeError("unavailable")
        self.frappe.db.sql = fail
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_parent_mask_rejects_child_fields_before_insert(self):
        original = self.frappe.get_meta
        def meta(doctype):
            result = original(doctype)
            if doctype == "Sales Invoice Item":
                result.get_masked_fields = lambda parenttype=None: [SimpleNamespace(fieldname="rate")]
            return result
        self.frappe.get_meta = meta
        with self.assertRaises(PostingPreDispatch):
            self.post()
        self.assertNotIn("insert", self.events)

    def test_posting_and_price_list_drift_rejected(self):
        for field, value in (("posting_date", "2026-11-21"), ("set_posting_time", 1),
                             ("selling_price_list", "other"), ("plc_conversion_rate", 2)):
            with self.subTest(field=field):
                self.setUp()
                self.mutate = lambda store: setattr(store["invoice"], field, value)
                with self.assertRaises(PostingPostDispatch):
                    self.post()

    def enable_vat(self):
        self.accounting.update(vat_mode="vat_7", tax_template="VAT",
            tax_rows=({"charge_type": "On Net Total", "account_head": "Tax",
                       "rate": 7, "included_in_print_rate": 0, "description": "VAT 7%"},),
            tax_account="Tax", tax_rate=7)
        self.totals.update(net="90.00", vat="6.30", total="96.30")
        self.doc.taxes_and_charges = "VAT"
        self.doc.taxes = [SimpleNamespace(charge_type="On Net Total", account_head="Tax",
            rate=7, included_in_print_rate=0, cost_center="CC", description="VAT 7%",
            tax_amount=6.3, base_tax_amount=6.3, tax_amount_after_discount_amount=6.3,
            base_tax_amount_after_discount_amount=6.3, total=96.3, base_total=96.3)]
        for field in ("grand_total", "base_grand_total", "outstanding_amount",
                      "rounded_total", "base_rounded_total"):
            setattr(self.doc, field, 96.3)
        self.doc.total_taxes_and_charges = self.doc.base_total_taxes_and_charges = 6.3
        self.gl = [
            ("Sales Invoice", "INV", "Co", "2026-11-20", "AR", "USD", "Customer", "C", 96.3, 0, 96.3, 0, 0, "Sales Invoice", "INV", None),
            ("Sales Invoice", "INV", "Co", "2026-11-20", "Income", "USD", None, None, 0, 90, 0, 90, 0, None, None, "CC"),
            ("Sales Invoice", "INV", "Co", "2026-11-20", "Tax", "USD", None, None, 0, 6.3, 0, 6.3, 0, None, None, "CC"),
        ]

    def test_vat_invoice_posts_separate_tax_ledger(self):
        self.enable_vat()
        self.assertEqual(self.post(), "INV")

    def test_missing_mode_or_amounts_rejected_before_insert(self):
        for mapping, key in (("accounting", "vat_mode"), ("totals", "net"), ("totals", "vat")):
            with self.subTest(key=key):
                self.setUp()
                del (self.accounting if mapping == "accounting" else self.totals)[key]
                with self.assertRaises(PostingPreDispatch):
                    self.post()
                self.assertNotIn("insert", self.events)

    def test_totals_must_be_fixed_point_strings(self):
        for value in (90, "90", "90.0", "090.00"):
            with self.subTest(value=value):
                self.setUp()
                self.totals["net"] = value
                with self.assertRaises(PostingPreDispatch):
                    self.post()

    def test_vat_persisted_tax_drift_requires_rollback(self):
        for field, value in (("account_head", "other"), ("rate", 6),
                             ("description", "changed"),
                             ("included_in_print_rate", 1), ("included_in_paid_amount", 1),
                             ("add_deduct_tax", "Deduct"), ("tax_amount_after_discount_amount", 5)):
            with self.subTest(field=field):
                self.setUp()
                self.enable_vat()
                self.mutate = lambda store: setattr(store["invoice"].taxes[0], field, value)
                with self.assertRaises(PostingPostDispatch):
                    self.post()

    def test_item_tax_template_drift_requires_rollback(self):
        self.enable_vat()
        self.mutate = lambda store: setattr(store["invoice"].items[0], "item_tax_template", "other")
        with self.assertRaises(PostingPostDispatch):
            self.post()

    def test_vat_extra_or_wrong_ledger_requires_rollback(self):
        for change in ("extra", "cost_center", "party"):
            with self.subTest(change=change):
                self.setUp()
                self.enable_vat()
                if change == "extra":
                    self.gl.append(self.gl[-1])
                elif change == "cost_center":
                    self.gl[-1] = self.gl[-1][:-1] + ("other",)
                else:
                    self.gl[-1] = self.gl[-1][:6] + ("Customer", "C") + self.gl[-1][8:]
                with self.assertRaises(PostingPostDispatch):
                    self.post()


if __name__ == "__main__":
    unittest.main()
