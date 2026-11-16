"""Contract tests for the finance (invoice submit + payment receive) family.

The contract is the closed-world decoder for both operations: strict bodies,
the installed-field preflight (with the discount child optional), the pinned
boundary, every pre-fence decision with its named line, the EXPECTED
side-effect lists, the prepared-body guard and the receipt/ack codec.

Design cuts asserted here: no tax row is ever produced, no client total reaches
the prepared body, the discount path exists only when the installed child and
the site-config permission flag are both present, and no refund kind exists.
"""

import unittest

from clinic_ui.core import finance_contract, write_budgets

PINNED = {
    "versions": {
        "frappe": "16.54.0",
        "erpnext": "16.52.1",
        "healthcare": "16.6.1",
    },
    "source_sha256": dict(finance_contract.REVIEWED_SOURCE_SHA256),
    "controller_mro": {
        doctype: tuple(expected)
        for doctype, expected in finance_contract.REVIEWED_MRO.items()
    },
    "submittable": {
        finance_contract.INVOICE_DOCTYPE: 1,
        finance_contract.PAYMENT_DOCTYPE: 1,
    },
    "enabled_server_scripts": 0,
    "doc_events": {
        doctype: {
            event: tuple(handlers)
            for event, handlers in finance_contract.REVIEWED_DOC_EVENTS[
                doctype
            ].items()
        }
        for doctype in finance_contract.GUARDED_DOCTYPES
    },
    "wildcard_doc_events": {
        event: tuple(handlers)
        for event, handlers in finance_contract.REVIEWED_WILDCARD_EVENTS.items()
    },
    "scheduler_finance_jobs": (),
    "class_overrides": {
        doctype: ()
        for doctype in finance_contract.GUARDED_DOCTYPES
    },
    "fence_kinds": finance_contract.FINANCE_KINDS,
}


def meta_fields(**overrides):
    fields = {
        doctype: set(candidates)
        for doctype, candidates in finance_contract.INSTALLED_CANDIDATES.items()
    }
    fields[finance_contract.DEDUCTION_LINK_KEY] = True
    fields.update(overrides)
    return fields


def invoice_body(**overrides):
    values = {
        "operation_key": "finance-write-key-0001",
        "customer": "CUST-1",
        "selling_price_list": "Standard Selling",
        "due_date": "2026-11-30",
        "items": [{"item_code": "ITEM-1", "qty": 2.0}],
    }
    values.update(overrides)
    return values


def payment_body(**overrides):
    values = {
        "operation_key": "finance-write-key-0001",
        "invoice": "SINV-1",
        "mode_of_payment": "Cash",
        "amount": 20.0,
        "allocations": [{"invoice": "SINV-1", "amount": 20.0}],
    }
    values.update(overrides)
    return values


def invoice_detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "currency": "USD",
        "customer_exists": True,
        "price_list": {"exists": True, "selling": True, "enabled": True},
        "prices": {"ITEM-1": 10.0},
        "items": {
            "ITEM-1": {
                "exists": True,
                "is_sales_item": True,
                "stock_uom": "Unit",
            }
        },
        "receivable_account": "Accounts Receivable - SITCO",
        "income_accounts": {"ITEM-1": "Sales - SITCO"},
        "cost_center": "Main - SITCO",
        "discount_available": False,
    }
    values.update(overrides)
    return values


def payment_detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "currency": "USD",
        "invoice": {
            "exists": True,
            "docstatus": 1,
            "status": "Unpaid",
            "outstanding_amount": 20.0,
            "grand_total": 20.0,
            "customer": "CUST-1",
        },
        "mode": {
            "exists": True,
            "account": "Cash - SITCO",
            "accepted": True,
        },
        "payment_exists": False,
        "receivable_account": "Accounts Receivable - SITCO",
    }
    values.update(overrides)
    return values


class InstallPreflightTests(unittest.TestCase):
    def test_a_complete_install_has_no_problem(self):
        self.assertIsNone(finance_contract.install_problem(meta_fields()))

    def test_the_discount_child_is_optional_for_the_family(self):
        fields = meta_fields(**{finance_contract.DEDUCTION_DOCTYPE: set()})
        self.assertIsNone(finance_contract.install_problem(fields))

    def test_a_missing_required_field_is_named(self):
        fields = meta_fields()
        fields[finance_contract.PAYMENT_DOCTYPE] = set(
            finance_contract.INSTALLED_CANDIDATES[
                finance_contract.PAYMENT_DOCTYPE
            ]
        ) - {"references"}
        self.assertEqual(
            finance_contract.install_problem(fields),
            "missing field Payment Entry.references",
        )

    def test_unreadable_metadata_fails_closed(self):
        self.assertEqual(
            finance_contract.install_problem(None),
            "installed metadata unreadable",
        )

    def test_the_discount_path_needs_the_flag_and_the_installed_child(self):
        fields = meta_fields()
        self.assertIsNotNone(
            finance_contract.discount_problem(fields, enabled=False)
        )
        fields[finance_contract.DEDUCTION_DOCTYPE] = set()
        self.assertIsNotNone(
            finance_contract.discount_problem(fields, enabled=True)
        )

    def test_the_discount_path_is_available_with_flag_and_child(self):
        fields = meta_fields()
        for field in finance_contract.DISCOUNT_CHILD_FIELDS:
            fields[finance_contract.DEDUCTION_DOCTYPE].add(field)
        self.assertIsNone(
            finance_contract.discount_problem(fields, enabled=True)
        )

    def test_the_discount_path_needs_the_parent_link_fieldname(self):
        fields = meta_fields()
        fields[finance_contract.DEDUCTION_LINK_KEY] = False
        self.assertIsNotNone(
            finance_contract.discount_problem(fields, enabled=True)
        )
        fields.pop(finance_contract.DEDUCTION_LINK_KEY)
        self.assertIsNotNone(
            finance_contract.discount_problem(fields, enabled=True)
        )


class BoundaryTests(unittest.TestCase):
    def test_the_pinned_snapshot_passes(self):
        self.assertIsNone(finance_contract.boundary_problem(PINNED))

    def test_erpnext_and_healthcare_builtin_doc_events_are_reviewed(self):
        invoice = finance_contract.REVIEWED_DOC_EVENTS[
            finance_contract.INVOICE_DOCTYPE
        ]
        self.assertEqual(
            sorted(invoice["on_submit"]),
            [
                "erpnext.regional.italy.utils.sales_invoice_on_submit",
                "healthcare.healthcare.utils.manage_invoice_submit_cancel",
            ],
        )
        self.assertIsNone(finance_contract.boundary_problem(PINNED))

    def test_foreign_doc_events_still_fail_closed(self):
        drift = dict(PINNED)
        drift["doc_events"] = {
            **PINNED["doc_events"],
            finance_contract.INVOICE_DOCTYPE: {
                **PINNED["doc_events"][finance_contract.INVOICE_DOCTYPE],
                "before_save": ("app.hooks.patch",),
            },
        }
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "document event drift for Sales Invoice",
        )

    def test_wildcard_event_drift_is_named(self):
        drift = dict(PINNED)
        drift["wildcard_doc_events"] = dict(
            PINNED["wildcard_doc_events"], validate=("app.hooks.check",)
        )
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "wildcard event drift for validate",
        )

    def test_the_installed_controller_hierarchies_are_pinned(self):
        invoice = finance_contract.REVIEWED_MRO[finance_contract.INVOICE_DOCTYPE]
        self.assertEqual(
            invoice[:3],
            (
                "erpnext.accounts.doctype.sales_invoice.sales_invoice."
                "ExtendedSalesInvoice",
                "healthcare.healthcare.custom_doctype.sales_invoice."
                "SalesInvoiceMixin",
                "erpnext.accounts.doctype.sales_invoice.sales_invoice."
                "SalesInvoice",
            ),
        )
        self.assertIn(
            "erpnext.controllers.status_updater.StatusUpdater", invoice
        )
        self.assertIn(
            "erpnext.controllers.status_updater.StatusUpdater",
            finance_contract.REVIEWED_MRO[finance_contract.PAYMENT_DOCTYPE],
        )

    def test_version_drift_is_named(self):
        drift = dict(PINNED)
        drift["versions"] = dict(PINNED["versions"], erpnext="16.53.0")
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "versions drift for erpnext",
        )

    def test_the_two_controller_sources_are_pinned(self):
        self.assertEqual(
            sorted(finance_contract.REVIEWED_SOURCE_SHA256),
            ["payment_entry", "sales_invoice"],
        )
        for name, value in finance_contract.REVIEWED_SOURCE_SHA256.items():
            self.assertEqual(len(value), 64, name)

    def test_source_hash_drift_is_named(self):
        drift = dict(PINNED)
        drift["source_sha256"] = dict(
            PINNED["source_sha256"], sales_invoice="0" * 64
        )
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "source drift for sales_invoice",
        )

    def test_unreadable_source_hashes_fail_closed(self):
        drift = dict(PINNED)
        drift["source_sha256"] = None
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "source hashes unreadable",
        )

    def test_controller_mro_drift_is_named(self):
        drift = dict(PINNED)
        drift["controller_mro"] = dict(
            PINNED["controller_mro"],
            **{finance_contract.INVOICE_DOCTYPE: ("builtins.object",)},
        )
        self.assertIn(
            "MRO drift", finance_contract.boundary_problem(drift)
        )

    def test_a_class_override_fails_closed(self):
        drift = dict(PINNED)
        drift["class_overrides"] = dict(
            PINNED["class_overrides"],
            **{finance_contract.PAYMENT_DOCTYPE: ("app.payments.Payment",)},
        )
        self.assertIn(
            "override drift", finance_contract.boundary_problem(drift)
        )

    def test_a_scheduler_job_on_the_family_is_refused(self):
        drift = dict(PINNED)
        drift["scheduler_finance_jobs"] = (
            "erpnext.accounts.doctype.sales_invoice.sales_invoice.post",
        )
        self.assertIn(
            "scheduler", finance_contract.boundary_problem(drift)
        )

    def test_fence_kind_drift_is_refused(self):
        drift = dict(PINNED)
        drift["fence_kinds"] = ("invoice_submit",)
        self.assertEqual(
            finance_contract.boundary_problem(drift),
            "fence kinds are not the reviewed finance set",
        )

    def test_a_non_submittable_controller_is_refused(self):
        drift = dict(PINNED)
        drift["submittable"] = {
            finance_contract.INVOICE_DOCTYPE: 1,
            finance_contract.PAYMENT_DOCTYPE: 0,
        }
        self.assertIn(
            "not submittable", finance_contract.boundary_problem(drift)
        )


class BudgetTests(unittest.TestCase):
    def test_both_kinds_carry_their_registry_budget(self):
        self.assertEqual(
            finance_contract.budget("invoice_submit"),
            write_budgets.budget_for("invoice_submit"),
        )
        self.assertEqual(
            finance_contract.budget("payment_receive"),
            write_budgets.budget_for("payment_receive"),
        )

    def test_no_refund_kind_exists(self):
        self.assertEqual(
            set(finance_contract.FINANCE_KINDS),
            {"invoice_submit", "payment_receive"},
        )
        with self.assertRaises(finance_contract.FinanceContractError):
            finance_contract.require_kind("payment_refund")


class InvoiceBodyTests(unittest.TestCase):
    def test_a_valid_body_is_normalized(self):
        payload = finance_contract.validate_invoice(invoice_body())
        self.assertEqual(payload["kind"], "invoice_submit")
        self.assertEqual(payload["discount"], 0.0)
        self.assertEqual(payload["items"], [{"item_code": "ITEM-1", "qty": 2.0}])

    def test_client_money_and_lifecycle_fields_are_refused(self):
        for candidate in (
            invoice_body(docstatus=1),
            invoice_body(rate=10.0),
            invoice_body(grand_total=20.0),
            invoice_body(currency="THB"),
            invoice_body(company="Other"),
            invoice_body(taxes=[{"account_head": "VAT"}]),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.validate_invoice(candidate)

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            invoice_body(items=[]),
            invoice_body(items=[{"item_code": "ITEM-1", "qty": 0}]),
            invoice_body(items=[{"item_code": "ITEM-1", "qty": 1, "rate": 5}]),
            invoice_body(due_date="30/11/2026"),
            invoice_body(due_date=""),
            invoice_body(discount=-1),
            invoice_body(operation_key="short"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.validate_invoice(candidate)


class PaymentBodyTests(unittest.TestCase):
    def test_a_valid_body_is_normalized(self):
        payload = finance_contract.validate_payment(payment_body())
        self.assertEqual(payload["kind"], "payment_receive")
        self.assertEqual(payload["amount"], 20.0)
        self.assertEqual(len(payload["allocations"]), 1)

    def test_card_data_has_no_field_at_all(self):
        for candidate in (
            payment_body(card_number="4111111111111111"),
            payment_body(cvv="123"),
            payment_body(pan="4111"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.validate_payment(candidate)

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            payment_body(amount=0),
            payment_body(amount=-5),
            payment_body(allocations=[]),
            payment_body(allocations=[{"invoice": "SINV-9", "amount": 20.0}]),
            payment_body(allocations=[{"invoice": "SINV-1"}]),
            payment_body(docstatus=1),
            payment_body(party="CUST-1"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.validate_payment(candidate)


class InvoiceProblemTests(unittest.TestCase):
    def problems(self, detail=None, body=None):
        return finance_contract.invoice_problems(
            invoice_detail() if detail is None else detail,
            finance_contract.validate_invoice(body or invoice_body()),
        )

    def test_a_clean_invoice_has_no_problem(self):
        self.assertEqual(self.problems(), ())

    def test_an_unknown_customer_is_named(self):
        problems = self.problems(invoice_detail(customer_exists=False))
        self.assertEqual(problems[0][0], 417)
        self.assertIn("customer CUST-1 does not exist", problems[0][2])

    def test_a_missing_or_non_selling_price_list_is_named(self):
        for price_list, expected in (
            ({"exists": False}, "does not exist"),
            ({"exists": True, "selling": False, "enabled": True}, "not a selling"),
            ({"exists": True, "selling": True, "enabled": False}, "not enabled"),
        ):
            with self.subTest(price_list=price_list):
                problems = self.problems(invoice_detail(price_list=price_list))
                self.assertIn(expected, problems[0][2])

    def test_a_past_due_date_is_refused(self):
        problems = self.problems(body=invoice_body(due_date="2026-11-15"))
        self.assertIn("before the posting date", problems[0][2])

    def test_a_missing_receivable_account_is_named(self):
        problems = self.problems(invoice_detail(receivable_account=""))
        self.assertIn("Accounts Receivable", problems[0][2])

    def test_an_item_without_a_selling_price_is_named(self):
        problems = self.problems(invoice_detail(prices={}))
        self.assertIn("no selling price", problems[0][2])

    def test_a_non_sales_item_is_refused(self):
        problems = self.problems(
            invoice_detail(
                items={
                    "ITEM-1": {
                        "exists": True,
                        "is_sales_item": False,
                        "stock_uom": "Unit",
                    }
                }
            )
        )
        self.assertIn("not a sales item", problems[0][2])

    def test_an_unknown_item_is_named(self):
        problems = self.problems(
            invoice_detail(items={"ITEM-1": {"exists": False}})
        )
        self.assertIn("item ITEM-1 does not exist", problems[0][2])

    def test_duplicate_charges_are_refused(self):
        body = invoice_body(
            items=[
                {"item_code": "ITEM-1", "qty": 1.0},
                {"item_code": "ITEM-1", "qty": 1.0},
            ]
        )
        problems = self.problems(body=body)
        self.assertIn("duplicate charge", problems[0][2])

    def test_a_discount_is_refused_when_the_path_is_cut(self):
        problems = self.problems(body=invoice_body(discount=2.0))
        self.assertIn("not enabled", problems[0][2])

    def test_a_discount_larger_than_the_subtotal_is_refused(self):
        detail = invoice_detail(discount_available=True)
        problems = self.problems(detail=detail, body=invoice_body(discount=21.0))
        self.assertIn("exceeds the subtotal", problems[0][2])

    def test_a_discount_on_an_available_path_passes(self):
        detail = invoice_detail(discount_available=True)
        self.assertEqual(
            self.problems(detail=detail, body=invoice_body(discount=2.0)), ()
        )

    def test_a_missing_company_or_currency_is_named(self):
        problems = self.problems(invoice_detail(currency=""))
        self.assertIn("currency", problems[0][2])


class PaymentProblemTests(unittest.TestCase):
    def problems(self, detail=None, body=None):
        return finance_contract.payment_problems(
            payment_detail() if detail is None else detail,
            finance_contract.validate_payment(body or payment_body()),
        )

    def test_a_clean_payment_has_no_problem(self):
        self.assertEqual(self.problems(), ())

    def test_a_draft_invoice_is_refused(self):
        detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 0,
                "status": "Draft",
                "outstanding_amount": 20.0,
                "grand_total": 20.0,
                "customer": "CUST-1",
            }
        )
        problems = self.problems(detail)
        self.assertIn("not submitted", problems[0][2])

    def test_a_cancelled_invoice_is_refused(self):
        detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 2,
                "status": "Cancelled",
                "outstanding_amount": 20.0,
                "grand_total": 20.0,
                "customer": "CUST-1",
            }
        )
        problems = self.problems(detail)
        self.assertIn("cancelled", problems[0][2])

    def test_an_already_paid_invoice_refuses_a_repeat_payment(self):
        detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 1,
                "status": "Paid",
                "outstanding_amount": 0.0,
                "grand_total": 20.0,
                "customer": "CUST-1",
            }
        )
        problems = self.problems(detail)
        self.assertIn("already paid", problems[0][2])

    def test_a_recorded_payment_for_the_invoice_is_refused(self):
        problems = self.problems(payment_detail(payment_exists=True))
        self.assertIn("already recorded", problems[0][2])

    def test_an_over_allocation_is_refused(self):
        body = payment_body(
            amount=25.0, allocations=[{"invoice": "SINV-1", "amount": 25.0}]
        )
        problems = self.problems(body=body)
        self.assertIn("exceeds the invoice outstanding", problems[0][2])

    def test_an_allocation_sum_that_differs_from_the_paid_amount_is_refused(self):
        body = payment_body(
            amount=20.0, allocations=[{"invoice": "SINV-1", "amount": 15.0}]
        )
        problems = self.problems(body=body)
        self.assertIn("do not sum", problems[0][2])

    def test_bank_transfer_is_refused_by_name(self):
        detail = payment_detail(
            mode={"exists": True, "account": "Bank - SITCO", "accepted": False}
        )
        problems = self.problems(
            detail=detail, body=payment_body(mode_of_payment="Bank Transfer")
        )
        self.assertIn("Bank Transfer", problems[0][2])
        self.assertIn("not an accepted mode", problems[0][2])

    def test_an_unknown_mode_of_payment_is_named(self):
        detail = payment_detail(mode={"exists": False, "accepted": False})
        problems = self.problems(
            detail=detail, body=payment_body(mode_of_payment="Cash")
        )
        self.assertIn("does not exist", problems[0][2])

    def test_a_mode_without_a_company_account_is_named(self):
        detail = payment_detail(
            mode={"exists": True, "account": "", "accepted": True}
        )
        problems = self.problems(detail)
        self.assertIn("no account for the company", problems[0][2])

    def test_a_card_payment_requires_a_terminal_reference(self):
        detail = payment_detail(
            mode={"exists": True, "account": "Card - SITCO", "accepted": True}
        )
        problems = self.problems(
            detail=detail, body=payment_body(mode_of_payment="Credit Card")
        )
        self.assertIn("terminal reference", problems[0][2])

    def test_a_card_payment_with_a_reference_passes(self):
        detail = payment_detail(
            mode={"exists": True, "account": "Card - SITCO", "accepted": True}
        )
        problems = self.problems(
            detail=detail,
            body=payment_body(
                mode_of_payment="Credit Card", reference_no="TERM-001"
            ),
        )
        self.assertEqual(problems, ())


class ExpectedTotalsTests(unittest.TestCase):
    def test_the_invoice_total_is_computed_from_server_prices(self):
        payload = finance_contract.validate_invoice(
            invoice_body(items=[{"item_code": "ITEM-1", "qty": 3.0}])
        )
        totals = finance_contract.invoice_totals(invoice_detail(), payload)
        self.assertEqual(totals["net_total"], 30.0)
        self.assertEqual(totals["grand_total"], 30.0)
        self.assertEqual(totals["total_taxes"], 0.0)

    def test_the_discount_reduces_only_the_grand_total(self):
        payload = finance_contract.validate_invoice(invoice_body(discount=5.0))
        totals = finance_contract.invoice_totals(
            invoice_detail(discount_available=True), payload
        )
        self.assertEqual(totals["net_total"], 20.0)
        self.assertEqual(totals["discount"], 5.0)
        self.assertEqual(totals["grand_total"], 15.0)

    def test_the_invoice_expected_list_is_count_based(self):
        payload = finance_contract.validate_invoice(invoice_body())
        expected = finance_contract.expected_invoice(invoice_detail(), payload)
        self.assertEqual(expected["doctype"], "Sales Invoice")
        self.assertEqual(expected["docstatus"], 1)
        self.assertEqual(expected["status"], "Unpaid")
        self.assertEqual(expected["gl_rows"], 2)
        self.assertEqual(expected["debit_total"], 20.0)
        self.assertEqual(expected["credit_total"], 20.0)
        self.assertEqual(expected["sle_rows"], 0)
        self.assertEqual(expected["ple_rows"], 1)

    def test_the_payment_expected_list_is_count_based(self):
        payload = finance_contract.validate_payment(payment_body())
        expected = finance_contract.expected_payment(payment_detail(), payload)
        self.assertEqual(expected["doctype"], "Payment Entry")
        self.assertEqual(expected["gl_rows"], 2)
        self.assertEqual(expected["ple_rows"], 1)
        self.assertEqual(expected["outstanding_delta"], -20.0)
        self.assertEqual(expected["status"], "Paid")

    def test_the_discount_expected_shape_is_verified_only_with_the_path(self):
        # The child's GL behaviour is only proven on a site that installs the
        # reviewed child under the parent link; otherwise this expectation is
        # unverified, so skip with the installed reason instead of inventing a
        # shape for a path the deployment does not carry.
        reason = finance_contract.discount_problem(
            meta_fields(), enabled=True
        )
        if reason is not None:
            self.skipTest(reason)
        payload = finance_contract.validate_invoice(
            invoice_body(discount=5.0)
        )
        expected = finance_contract.expected_invoice(
            invoice_detail(discount_available=True), payload
        )
        self.assertEqual(expected["gl_rows"], len(payload["items"]) + 1)
        self.assertEqual(expected["credit_total"], expected["net_total"])

    def test_a_partial_payment_keeps_the_observed_status(self):
        detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 1,
                "status": "Unpaid",
                "outstanding_amount": 50.0,
                "grand_total": 50.0,
                "customer": "CUST-1",
            }
        )
        payload = finance_contract.validate_payment(payment_body(amount=20.0))
        expected = finance_contract.expected_payment(detail, payload)
        self.assertIsNone(expected["status"])
        self.assertEqual(expected["unchanged_status"], "Unpaid")


class PreparedBodyTests(unittest.TestCase):
    def prepared_invoice(self, **overrides):
        values = {
            "kind": "invoice_submit",
            "customer": "CUST-1",
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
            "conversion_rate": 1.0,
            "selling_price_list": "Standard Selling",
            "due_date": "2026-11-30",
            "posting_date": "2026-11-16",
            "update_stock": 0,
            "debit_to": "Accounts Receivable - SITCO",
            "cost_center": "Main - SITCO",
            "taxes": [],
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 2.0,
                    "rate": 10.0,
                    "amount": 20.0,
                    "income_account": "Sales - SITCO",
                    "cost_center": "Main - SITCO",
                    "price_list_rate": 10.0,
                }
            ],
        }
        values.update(overrides)
        return values

    def prepared_payment(self, **overrides):
        values = {
            "kind": "payment_receive",
            "payment_type": "Receive",
            "party_type": "Customer",
            "party": "CUST-1",
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
            "paid_from": "Accounts Receivable - SITCO",
            "paid_to": "Cash - SITCO",
            "paid_amount": 20.0,
            "received_amount": 20.0,
            "source_exchange_rate": 1.0,
            "target_exchange_rate": 1.0,
            "posting_date": "2026-11-16",
            "mode_of_payment": "Cash",
            "reference_date": "2026-11-16",
            "references": [
                {
                    "reference_doctype": "Sales Invoice",
                    "reference_name": "SINV-1",
                    "allocated_amount": 20.0,
                    "outstanding_amount": 20.0,
                    "total_amount": 20.0,
                }
            ],
        }
        values.update(overrides)
        return values

    def test_a_clean_invoice_body_has_no_problem(self):
        self.assertIsNone(
            finance_contract.prepared_problem(
                "invoice_submit", self.prepared_invoice()
            )
        )

    def test_update_stock_zero_is_mandatory(self):
        for value in (1, 0.0, False, None, "0"):
            with self.subTest(value=value):
                problem = finance_contract.prepared_problem(
                    "invoice_submit", self.prepared_invoice(update_stock=value)
                )
                self.assertIn("update_stock", problem)

    def test_a_client_total_may_not_reach_the_prepared_body(self):
        for key in ("grand_total", "net_total", "total_taxes_and_charges"):
            with self.subTest(key=key):
                problem = finance_contract.prepared_problem(
                    "invoice_submit", self.prepared_invoice(**{key: 20.0})
                )
                self.assertIn("computed total", problem)

    def test_taxes_stay_empty(self):
        problem = finance_contract.prepared_problem(
            "invoice_submit",
            self.prepared_invoice(
                taxes=[{"account_head": "VAT - SITCO", "rate": 7.0}]
            ),
        )
        self.assertIn("tax", problem)

    def test_an_invoice_line_needs_an_income_account(self):
        prepared = self.prepared_invoice()
        prepared["items"][0].pop("income_account")
        self.assertIn(
            "income account",
            finance_contract.prepared_problem("invoice_submit", prepared),
        )

    def test_a_line_amount_must_equal_quantity_times_rate(self):
        prepared = self.prepared_invoice()
        prepared["items"][0]["amount"] = 19.0
        self.assertIn(
            "line amount",
            finance_contract.prepared_problem("invoice_submit", prepared),
        )

    def test_the_discount_rides_the_deductions_child_only(self):
        prepared = self.prepared_invoice(
            discount=2.0,
            discount_amount=2.0,
            deductions=[
                {
                    "account": "Sales - SITCO",
                    "cost_center": "Main - SITCO",
                    "amount": 2.0,
                }
            ],
        )
        self.assertIn(
            "discount",
            finance_contract.prepared_problem("invoice_submit", prepared),
        )
        prepared.pop("discount_amount")
        self.assertIsNone(
            finance_contract.prepared_problem("invoice_submit", prepared)
        )

    def test_the_deductions_child_needs_a_cost_center(self):
        prepared = self.prepared_invoice(
            discount=2.0,
            deductions=[{"account": "Sales - SITCO", "amount": 2.0}],
        )
        self.assertIn(
            "cost center",
            finance_contract.prepared_problem("invoice_submit", prepared),
        )

    def test_a_clean_payment_body_has_no_problem(self):
        self.assertIsNone(
            finance_contract.prepared_problem(
                "payment_receive", self.prepared_payment()
            )
        )

    def test_the_payment_must_be_a_receive(self):
        problem = finance_contract.prepared_problem(
            "payment_receive", self.prepared_payment(payment_type="Pay")
        )
        self.assertIn("Receive", problem)

    def test_the_allocations_must_sum_to_the_paid_amount(self):
        prepared = self.prepared_payment()
        prepared["references"][0]["allocated_amount"] = 15.0
        self.assertIn(
            "allocations",
            finance_contract.prepared_problem("payment_receive", prepared),
        )

    def test_the_payment_needs_both_ledger_accounts(self):
        self.assertIn(
            "paid_from",
            finance_contract.prepared_problem(
                "payment_receive", self.prepared_payment(paid_from="")
            ),
        )
        self.assertIn(
            "paid_to",
            finance_contract.prepared_problem(
                "payment_receive", self.prepared_payment(paid_to="")
            ),
        )

    def test_no_card_field_can_appear(self):
        for key in ("card_number", "cvv", "pan"):
            with self.subTest(key=key):
                self.assertIn(
                    "card",
                    finance_contract.prepared_problem(
                        "payment_receive", self.prepared_payment(**{key: "x"})
                    ),
                )


class SideEffectTests(unittest.TestCase):
    def invoice_expected(self):
        payload = finance_contract.validate_invoice(invoice_body())
        return finance_contract.expected_invoice(invoice_detail(), payload)

    def payment_expected(self):
        payload = finance_contract.validate_payment(payment_body())
        return finance_contract.expected_payment(payment_detail(), payload)

    def invoice_facts(self, expected):
        return {
            "doctype": "Sales Invoice",
            "docstatus": expected["docstatus"],
            "status": expected["status"],
            "currency": expected["currency"],
            "grand_total": expected["grand_total"],
            "net_total": expected["net_total"],
            "gl_rows": expected["gl_rows"],
            "debit_total": expected["debit_total"],
            "credit_total": expected["credit_total"],
            "sle_rows": expected["sle_rows"],
            "ple_rows": expected["ple_rows"],
            "unexpected_gl_rows": 0,
            "outstanding_amount": expected["grand_total"],
        }

    def payment_facts(self, expected, status="Paid"):
        return {
            "doctype": "Payment Entry",
            "docstatus": expected["docstatus"],
            "gl_rows": expected["gl_rows"],
            "ple_rows": expected["ple_rows"],
            "unexpected_gl_rows": 0,
            "allocated": expected["allocated"],
            "outstanding_delta": expected["outstanding_delta"],
            "outstanding_after": expected["outstanding_after"],
            "invoice_status": status,
        }

    def test_the_invoice_expected_carries_the_server_resolved_accounts(self):
        detail = invoice_detail()
        expected = self.invoice_expected()
        self.assertEqual(
            expected["debit_account"], detail["receivable_account"]
        )
        self.assertEqual(
            expected["credit_accounts"],
            sorted(set(detail["income_accounts"].values())),
        )

    def test_the_payment_expected_carries_the_server_resolved_accounts(self):
        detail = payment_detail()
        expected = self.payment_expected()
        self.assertEqual(
            expected["paid_from_account"], detail["receivable_account"]
        )
        self.assertEqual(
            expected["paid_to_account"], detail["mode"]["account"]
        )

    def test_invoice_gl_facts_classified_like_the_real_reader_pass(self):
        # Derive the rows the installed controller posts (Debtors Dr / Sales
        # Cr) from the server-resolved accounts, then classify them through the
        # same rule the real gateway's re-read uses. Echoing EXPECTED would hide
        # a key drift exactly like the pre-repair reader did.
        expected = self.invoice_expected()
        gl_rows = [
            {
                "account": expected["debit_account"],
                "debit": expected["debit_total"],
                "credit": 0.0,
            },
            {
                "account": expected["credit_accounts"][0],
                "debit": 0.0,
                "credit": expected["credit_total"],
            },
        ]
        debit_total, credit_total, unexpected = (
            finance_contract.invoice_gl_sums(gl_rows, expected)
        )
        self.assertEqual(debit_total, expected["debit_total"])
        self.assertEqual(credit_total, expected["credit_total"])
        self.assertEqual(unexpected, 0)

    def test_payment_gl_facts_classified_like_the_real_reader_pass(self):
        expected = self.payment_expected()
        gl_rows = [
            {
                "account": expected["paid_to_account"],
                "debit": expected["allocated"],
                "credit": 0.0,
            },
            {
                "account": expected["paid_from_account"],
                "debit": 0.0,
                "credit": expected["allocated"],
            },
        ]
        self.assertEqual(
            finance_contract.payment_gl_unexpected(gl_rows, expected), 0
        )

    def test_a_gl_row_outside_the_expected_accounts_is_unexpected(self):
        expected = self.invoice_expected()
        gl_rows = [
            {
                "account": expected["debit_account"],
                "debit": expected["debit_total"],
                "credit": 0.0,
            },
            {
                "account": "Suspense - SITCO",
                "debit": 0.0,
                "credit": expected["credit_total"],
            },
        ]
        _debit, _credit, unexpected = finance_contract.invoice_gl_sums(
            gl_rows, expected
        )
        self.assertEqual(unexpected, 1)

    def test_the_invoice_expected_list_passes(self):
        expected = self.invoice_expected()
        self.assertIsNone(
            finance_contract.side_effect_problem(
                self.invoice_facts(expected), expected
            )
        )

    def test_a_posted_currency_drift_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["currency"] = "EUR"
        self.assertIn(
            "currency",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_the_payment_expected_list_passes(self):
        expected = self.payment_expected()
        self.assertIsNone(
            finance_contract.side_effect_problem(
                self.payment_facts(expected), expected
            )
        )

    def test_a_wrong_gl_count_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["gl_rows"] = expected["gl_rows"] + 1
        self.assertIn(
            "gl row count",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_an_unexpected_gl_account_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["unexpected_gl_rows"] = 1
        self.assertIn(
            "account outside",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_stock_ledger_movement_on_an_invoice_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["sle_rows"] = 1
        self.assertIn(
            "stock ledger",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_wrong_posted_total_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["grand_total"] = 21.0
        self.assertIn(
            "posted total",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_wrong_debit_or_credit_sum_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["debit_total"] = 0.0
        self.assertIn(
            "debit sum",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_wrong_status_is_refused(self):
        expected = self.invoice_expected()
        facts = self.invoice_facts(expected)
        facts["status"] = "Paid"
        self.assertIn(
            "status", finance_contract.side_effect_problem(facts, expected)
        )

    def test_a_wrong_payment_ledger_count_is_refused(self):
        expected = self.payment_expected()
        facts = self.payment_facts(expected)
        facts["ple_rows"] = 3
        self.assertIn(
            "payment ledger row count",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_wrong_outstanding_delta_is_refused(self):
        expected = self.payment_expected()
        facts = self.payment_facts(expected)
        facts["outstanding_delta"] = -10.0
        self.assertIn(
            "outstanding delta",
            finance_contract.side_effect_problem(facts, expected),
        )

    def test_a_partial_payment_must_keep_the_invoice_status(self):
        detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 1,
                "status": "Unpaid",
                "outstanding_amount": 50.0,
                "grand_total": 50.0,
                "customer": "CUST-1",
            }
        )
        payload = finance_contract.validate_payment(payment_body(amount=20.0))
        expected = finance_contract.expected_payment(detail, payload)
        facts = self.payment_facts(expected, status="Paid")
        self.assertIn(
            "status",
            finance_contract.side_effect_problem(facts, expected),
        )


class ReceiptTests(unittest.TestCase):
    def test_an_invoice_receipt_round_trips(self):
        encoded = finance_contract.encode_receipt(
            kind="invoice_submit",
            reference="SINV-1",
            invoice="SINV-1",
            customer="CUST-1",
            amount=20.0,
            status="Unpaid",
        )
        decoded = finance_contract.decode_receipt(encoded)
        self.assertEqual(decoded.kind, "invoice_submit")
        self.assertEqual(decoded.amount, 20.0)
        self.assertEqual(decoded.status, "Unpaid")

    def test_a_payment_receipt_round_trips(self):
        encoded = finance_contract.encode_receipt(
            kind="payment_receive",
            reference="PE-1",
            invoice="SINV-1",
            customer="CUST-1",
            amount=20.0,
            mode_of_payment="Cash",
            status="Paid",
        )
        decoded = finance_contract.decode_receipt(encoded)
        self.assertEqual(decoded.kind, "payment_receive")
        self.assertEqual(decoded.mode_of_payment, "Cash")
        self.assertEqual(decoded.reference, "PE-1")

    def test_a_tampered_or_unknown_receipt_is_refused(self):
        for value in (
            "",
            "{}",
            '{"codec": "finance"}',
            finance_contract.encode_receipt(
                kind="invoice_submit",
                reference="SINV-1",
                invoice="SINV-1",
                customer="CUST-1",
                amount=20.0,
            ).replace("invoice_submit", "payment_refund"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.decode_receipt(value)


class DigestAndAckTests(unittest.TestCase):
    SECRET = b"0123456789abcdef0123456789abcdef"

    def test_the_digest_is_deterministic_and_field_sensitive(self):
        first = finance_contract.operation_digest(
            self.SECRET,
            kind="invoice_submit",
            operation_key="finance-write-key-0001",
            fields={"customer": "CUST-1"},
        )
        again = finance_contract.operation_digest(
            self.SECRET,
            kind="invoice_submit",
            operation_key="finance-write-key-0001",
            fields={"customer": "CUST-1"},
        )
        other = finance_contract.operation_digest(
            self.SECRET,
            kind="invoice_submit",
            operation_key="finance-write-key-0001",
            fields={"customer": "CUST-2"},
        )
        self.assertEqual(first, again)
        self.assertNotEqual(first, other)

    def test_the_ack_token_binds_the_context(self):
        receipt = finance_contract.encode_receipt(
            kind="payment_receive",
            reference="PE-1",
            invoice="SINV-1",
            customer="CUST-1",
            amount=20.0,
        )
        token = finance_contract.ack_token(
            self.SECRET,
            "marley-sit.local",
            "cashier@example.test",
            "payment_receive",
            "finance-write-key-0001",
            receipt,
        )
        self.assertTrue(finance_contract.is_valid_ack_token(token))
        moved = finance_contract.ack_token(
            self.SECRET,
            "marley-sit.local",
            "other@example.test",
            "payment_receive",
            "finance-write-key-0001",
            receipt,
        )
        self.assertNotEqual(token, moved)

    def test_an_ack_body_is_strict(self):
        valid = {
            "operation_key": "finance-write-key-0001",
            "kind": "payment_receive",
            "ack_token": "a" * 64,
        }
        self.assertEqual(
            finance_contract.validate_ack(valid)["kind"], "payment_receive"
        )
        for candidate in (
            {},
            {**valid, "ack_token": "short"},
            {**valid, "kind": "payment_refund"},
            {**valid, "extra": 1},
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(finance_contract.FinanceContractError):
                    finance_contract.validate_ack(candidate)


if __name__ == "__main__":
    unittest.main()
