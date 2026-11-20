"""Source-only tests for the unwired, locked settlement fact reader."""

import sys
import types
import unittest
from unittest import mock

from clinic_ui.core import patient_write
from clinic_ui.runtime.frappe_gateway import Gateway


class Field:
    def __init__(self, name):
        self.name = name

    def __eq__(self, other):
        return (self.name, other)


class Document:
    def __init__(self, **values):
        self.__dict__.update(values)

    def precision(self, name):
        return self.precisions[name]

    def append(self, field, value):
        return Document(precisions={"allocated_amount": 2, "outstanding_amount": 2})


class SettlementFactsTests(unittest.TestCase):
    def setUp(self):
        self.invoice = Document(
            name="SINV-1", company="Company", currency="THB",
            party_account_currency="THB", customer="Customer", debit_to="Debtors",
            outstanding_amount=20, docstatus=1, conversion_rate=1,
            precisions={"outstanding_amount": 2},
        )
        self.payment = Document(precisions={"paid_amount": 2, "received_amount": 2})
        self.rows = [{"voucher_type": "Sales Invoice", "voucher_no": "SINV-1",
                      "account": "Debtors", "party_type": "Customer", "party": "Customer",
                      "currency": "THB", "outstanding": 20,
                      "outstanding_in_account_currency": 20}]
        self.query = mock.Mock()
        self.query.get_voucher_outstandings.side_effect = lambda **kwargs: self.rows
        module = types.ModuleType("erpnext.accounts.utils")
        module.QueryPaymentLedger = lambda: self.query
        self.modules = mock.patch.dict(sys.modules, {"erpnext.accounts.utils": module})
        self.modules.start()
        self.addCleanup(self.modules.stop)
        self.frappe = mock.Mock()
        self.frappe._dict = lambda **kwargs: kwargs
        self.frappe.qb.DocType.return_value = types.SimpleNamespace(
            account=Field("account"), party_type=Field("party_type"), party=Field("party"),
            company=Field("company"), account_currency=Field("account_currency"),
        )
        self.frappe.get_doc.side_effect = lambda *args: (
            self.invoice if args[0] == "Sales Invoice" else self.payment
        )
        self.frappe.db.get_value.side_effect = lambda doctype, name, field: {
            ("Company", "Company", "default_currency"): "THB",
            ("Account", "Debtors", "account_currency"): "THB",
            ("Account", "Cash", "account_currency"): "THB",
            ("Account", "Debtors", "company"): "Company",
            ("Account", "Cash", "company"): "Company",
            ("Account", "Debtors", "account_type"): "Receivable",
            ("Account", "Debtors", "is_group"): 0,
            ("Account", "Debtors", "disabled"): 0,
        }.get((doctype, name, field))
        self.frappe.utils.flt.side_effect = lambda value, precision: round(float(value), precision)
        self.gateway = Gateway(self.frappe)

    def facts(self, amount=20, allocation=20):
        return self.gateway.payment_settlement_facts(
            "SINV-1", "Cash", amount, allocation, "Cash"
        )

    def test_current_balance_and_exact_native_filters(self):
        facts = self.facts()
        self.assertEqual(facts["outstanding"], 20)
        self.assertEqual(facts["currency"], "THB")
        self.query.get_voucher_outstandings.assert_called_once_with(
            vouchers=[{"voucher_type": "Sales Invoice", "voucher_no": "SINV-1"}],
            common_filter=[("company", "Company"), ("account_currency", "THB"),
                           ("account", "Debtors"), ("party_type", "Customer"),
                           ("party", "Customer")],
        )

    def test_missing_or_stale_ledger_refused(self):
        for rows in ([], [{"voucher_type": "Sales Invoice", "voucher_no": "SINV-1",
                          "account": "Debtors", "party_type": "Customer", "party": "Customer",
                          "currency": "THB", "outstanding": 19,
                          "outstanding_in_account_currency": 19}]):
            with self.subTest(rows=rows):
                self.rows = rows
                self.assertIsNone(self.facts())

    def test_currency_mismatch_refused(self):
        self.invoice.party_account_currency = "USD"
        self.assertIsNone(self.facts())

    def test_non_receivable_or_disabled_debtor_refused(self):
        original = self.frappe.db.get_value.side_effect
        for field, value in (("account_type", "Bank"), ("disabled", 1), ("is_group", 1)):
            with self.subTest(field=field):
                self.frappe.db.get_value.side_effect = (
                    lambda doctype, name, key, field=field, value=value:
                    value if (doctype, name, key) == ("Account", "Debtors", field)
                    else original(doctype, name, key)
                )
                self.assertIsNone(self.facts())

    def test_precision_and_stale_amount_refused_without_rounding(self):
        self.assertIsNone(self.facts(20.001, 20.001))
        self.assertIsNone(self.facts(19.99, 19.99))

    def test_missing_native_precision_refuses(self):
        self.payment.precisions["received_amount"] = None
        self.assertIsNone(self.facts())

    def test_mode_account_must_match_company_and_currency(self):
        original = self.frappe.db.get_value.side_effect
        self.frappe.db.get_value.side_effect = lambda doctype, name, field: (
            "USD" if (doctype, name, field) == ("Account", "Cash", "account_currency")
            else original(doctype, name, field)
        )
        self.assertIsNone(self.facts())

    def test_native_precision_does_not_round_ledger_or_request_into_match(self):
        self.rows[0]["outstanding_in_account_currency"] = 20.004
        self.assertIsNone(self.facts())
        self.assertIsNone(self.facts(20.004, 20.004))

    def test_raw_invoice_residual_is_not_rounded_into_match(self):
        self.invoice.outstanding_amount = 20.004
        self.assertIsNone(self.facts())

    def test_baseline_company_amount_or_currency_mismatch_refused(self):
        self.rows[0]["outstanding"] = 19.99
        self.assertIsNone(self.facts())
        self.rows[0]["outstanding"] = 20
        self.rows[0]["currency"] = "USD"
        self.assertIsNone(self.facts())

    def test_baseline_missing_company_amount_or_duplicate_rows_refused(self):
        row = dict(self.rows[0])
        del self.rows[0]["outstanding"]
        self.assertIsNone(self.facts())
        self.rows = [row, dict(row)]
        self.assertIsNone(self.facts())

    def test_production_gate_remains_closed(self):
        self.assertFalse(self.gateway.native_finance_settlement_verified())

    def test_mode_requires_enabled_native_matching_cash_or_bank_account(self):
        self.frappe.db.exists.return_value = True
        def get_value(doctype, name, field, **kwargs):
            if doctype == "Mode of Payment":
                return {"enabled": 1, "type": "Bank"}
            if doctype == "Mode of Payment Account":
                return "Cash"
            if doctype == "Account":
                return {"account_type": "Cash", "is_group": 0, "disabled": 0,
                        "company": "Company"}
            return None
        self.frappe.db.get_value.side_effect = get_value
        self.assertFalse(self.gateway._finance_mode_of_payment("Cash", "Company")["accepted"])

    def test_compatible_cash_and_card_modes_are_accepted(self):
        self.frappe.db.exists.return_value = True
        for mode, kind in (("Cash", "Cash"), ("Credit Card", "Bank")):
            with self.subTest(mode=mode):
                def get_value(doctype, name, field, **kwargs):
                    if doctype == "Mode of Payment":
                        return {"enabled": 1, "type": kind}
                    if doctype == "Mode of Payment Account":
                        return "Account"
                    if doctype == "Account":
                        return {"account_type": kind, "is_group": 0, "disabled": 0,
                                "company": "Company"}
                    return None
                self.frappe.db.get_value.side_effect = get_value
                self.assertTrue(self.gateway._finance_mode_of_payment(mode, "Company")["accepted"])


class PaymentEffectsTests(unittest.TestCase):
    facts = SettlementFactsTests.facts

    def setUp(self):
        SettlementFactsTests.setUp(self)
        self.settlement = self.facts()
        self.settlement["reference_no"] = "TERM-1"
        self.settlement["reference_date"] = "2026-11-20"
        self.invoice.status = "Paid"
        self.invoice.outstanding_amount = 0
        self.rows[0]["outstanding_in_account_currency"] = 0
        self.rows[0]["outstanding"] = 0
        self.posted = Document(
            name="PE-1", docstatus=1, payment_type="Receive", party_type="Customer",
            party="Customer", company="Company", mode_of_payment="Cash",
            paid_from="Debtors", paid_to="Cash", paid_from_account_currency="THB",
            paid_to_account_currency="THB", paid_amount=20, received_amount=20,
            base_paid_amount=20, base_received_amount=20,
            unallocated_amount=0, difference_amount=0, source_exchange_rate=1,
            target_exchange_rate=1, references=[
                Document(reference_doctype="Sales Invoice", reference_name="SINV-1",
                         allocated_amount=20)
            ],
            reference_no="TERM-1", reference_date="2026-11-20",
        )
        self.gl = [
            dict(voucher_type="Payment Entry", voucher_no="PE-1", company="Company",
                 account="Cash", account_currency="THB", debit=20, credit=0,
                 debit_in_account_currency=20, credit_in_account_currency=0,
                 party_type=None, party=None, against_voucher_type=None,
                 against_voucher=None, is_cancelled=0),
            dict(voucher_type="Payment Entry", voucher_no="PE-1", company="Company",
                 account="Debtors", account_currency="THB", debit=0, credit=20,
                 debit_in_account_currency=0, credit_in_account_currency=20,
                 party_type="Customer", party="Customer",
                 against_voucher_type="Sales Invoice", against_voucher="SINV-1",
                 is_cancelled=0),
        ]
        self.ple = [
            dict(voucher_type="Payment Entry", voucher_no="PE-1", company="Company",
                 account="Debtors", account_type="Receivable", account_currency="THB",
                 party_type="Customer", party="Customer", against_voucher_type="Sales Invoice",
                 against_voucher_no="SINV-1", amount=-20, amount_in_account_currency=-20,
                 delinked=0),
        ]
        self.frappe.get_doc.side_effect = lambda *args: (
            self.invoice if args[0] == "Sales Invoice" else self.posted
        )
        self.frappe.db.get_all.side_effect = lambda doctype, **kwargs: (
            self.gl if doctype == "GL Entry" else self.ple
        )

    def verify(self):
        return self.gateway.verify_full_payment_effects("PE-1", self.settlement)

    def test_valid_scoped_full_payment(self):
        self.assertTrue(self.verify())
        calls = self.frappe.db.get_all.call_args_list
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertEqual(call.kwargs["filters"], {
                "voucher_type": "Payment Entry", "voucher_no": "PE-1"
            })

    def test_wrong_reference_or_allocation_rejected(self):
        for field, value in (("reference_name", "SINV-2"), ("allocated_amount", 19)):
            with self.subTest(field=field):
                row = self.posted.references[0]
                original = getattr(row, field)
                setattr(row, field, value)
                self.assertFalse(self.verify())
                setattr(row, field, original)

    def test_wrong_voucher_or_account_rejected(self):
        for row, field, value in ((self.gl[0], "voucher_no", "PE-2"),
                                  (self.gl[1], "account", "Other"),
                                  (self.ple[0], "against_voucher_no", "SINV-2")):
            with self.subTest(field=field):
                original = row[field]
                row[field] = value
                self.assertFalse(self.verify())
                row[field] = original

    def test_nonzero_invoice_or_ledger_and_missing_ledger_rejected(self):
        self.invoice.outstanding_amount = 0.001
        self.assertFalse(self.verify())

        self.invoice.outstanding_amount = 0
        self.rows[0]["outstanding_in_account_currency"] = 0.001
        self.assertFalse(self.verify())
        self.rows = []
        self.assertFalse(self.verify())

    def test_company_currency_residual_rejected_even_if_account_zero(self):
        self.rows[0]["outstanding"] = 0.01
        self.assertFalse(self.verify())

    def test_post_missing_company_amount_or_duplicate_rows_refused(self):
        row = dict(self.rows[0])
        del self.rows[0]["outstanding"]
        self.assertFalse(self.verify())
        self.rows = [row, dict(row)]
        self.assertFalse(self.verify())

    def test_post_query_scopes_company_and_currency(self):
        self.assertTrue(self.verify())
        self.assertEqual(
            self.query.get_voucher_outstandings.call_args.kwargs["common_filter"],
            [("company", "Company"), ("account_currency", "THB"),
             ("account", "Debtors"), ("party_type", "Customer"), ("party", "Customer")],
        )

    def test_payment_fields_must_match_settlement(self):
        for field, value in (
            ("mode_of_payment", "Credit Card"), ("company", "Other"),
            ("paid_to_account_currency", "USD"), ("paid_amount", 19),
            ("unallocated_amount", 1), ("difference_amount", 1),
            ("reference_no", "OTHER"), ("reference_date", "2026-11-21"),
        ):
            with self.subTest(field=field):
                original = getattr(self.posted, field)
                setattr(self.posted, field, value)
                self.assertFalse(self.verify())
                setattr(self.posted, field, original)
        self.posted.references.append(self.posted.references[0])
        self.assertFalse(self.verify())

    def test_scoped_ledger_amounts_signs_and_presence(self):
        for row, field, value in (
            (self.gl[0], "debit", 19), (self.gl[1], "credit", 19),
            (self.ple[0], "amount", 20), (self.ple[0], "amount_in_account_currency", -19),
        ):
            with self.subTest(field=field):
                original = row[field]
                row[field] = value
                self.assertFalse(self.verify())
                row[field] = original
        self.ple = []
        self.assertFalse(self.verify())


class LedgerRangeLockTests(unittest.TestCase):
    def setUp(self):
        self.frappe = mock.Mock()
        self.indexes = [
            {"Key_name": name, "Non_unique": 1, "Seq_in_index": sequence,
             "Column_name": column, "Sub_part": None, "Index_type": "BTREE",
             "Ignored": "NO"}
            for name, columns in (
                ("voucher_idx", ("voucher_no", "voucher_type")),
                ("against_idx", ("against_voucher_no", "against_voucher_type")),
            )
            for sequence, column in enumerate(columns, 1)
        ]
        self.ranges = [[], []]
        self.session = {"version": "10.11.8-MariaDB", "isolation": "REPEATABLE-READ",
                        "autocommit": 0}
        self.engine = "InnoDB"
        self.invoice_engine = "InnoDB"
        self.invoice_indexes = [
            {"Key_name": "PRIMARY", "Non_unique": 0, "Seq_in_index": 1,
             "Column_name": "name", "Sub_part": None, "Index_type": "BTREE"}
        ]
        self.calls = []
        def sql(query, values=None, **kwargs):
            self.calls.append((query, values))
            if "@@version" in query:
                return [self.session]
            if "information_schema.TABLES" in query:
                return [{"ENGINE": self.engine if values[0] == "tabPayment Ledger Entry"
                         else self.invoice_engine}]
            if query.startswith("SHOW INDEX"):
                return self.invoice_indexes if "tabSales Invoice" in query else self.indexes
            if "FORCE INDEX" in query:
                return self.ranges[0 if "voucher_idx" in query else 1]
            raise AssertionError(query)
        self.frappe.db.sql.side_effect = sql
        self.gateway = Gateway(self.frappe)

    def test_verified_indexes_and_parameterized_both_ranges(self):
        profile = self.gateway.finance_payment_lock_profile()
        self.gateway.lock_finance_payment_ledger_ranges("SINV-1", profile)
        queries = [(query, values) for query, values in self.calls if "FORCE INDEX" in query]
        self.assertEqual(len(queries), 2)
        for query, values in queries:
            self.assertIn("amount", query)
            self.assertIn("FOR UPDATE", query)
            self.assertEqual(values, ("SINV-1", "Sales Invoice"))
            self.assertNotIn("SINV-1", query)

    def test_unverified_session_or_index_profile_refused(self):
        self.session["isolation"] = "READ-COMMITTED"
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.finance_payment_lock_profile()
        self.session["isolation"] = "REPEATABLE-READ"
        self.indexes[0]["Sub_part"] = 4
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.finance_payment_lock_profile()

    def test_invoice_must_have_innodb_primary_name_index(self):
        self.invoice_engine = "MyISAM"
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.finance_payment_lock_profile()

    def test_index_and_session_variants_fail_closed(self):
        for field, value in (("Non_unique", 0), ("Index_type", "HASH"),
                             ("Ignored", "YES"), ("Column_name", "wrong")):
            with self.subTest(field=field):
                original = self.indexes[0][field]
                self.indexes[0][field] = value
                with self.assertRaises(patient_write.WriteUncertain):
                    self.gateway.finance_payment_lock_profile()
                self.indexes[0][field] = original
        self.session["autocommit"] = 1
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.finance_payment_lock_profile()

    def test_unverified_index_name_rejected_without_query(self):
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.lock_finance_payment_ledger_ranges(
                "SINV-1", ("voucher_idx` FORCE", "against_idx")
            )
        self.assertEqual(self.calls, [])
        self.invoice_engine = "InnoDB"
        self.invoice_indexes[0]["Sub_part"] = 4
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.finance_payment_lock_profile()
    def test_overflow_and_database_error_refuse(self):
        profile = self.gateway.finance_payment_lock_profile()
        self.ranges[0] = [(1,)] * 201
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.lock_finance_payment_ledger_ranges("SINV-1", profile)
        self.frappe.db.sql.side_effect = RuntimeError("deadlock")
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway.lock_finance_payment_ledger_ranges("SINV-1", profile)
