"""Endpoint tests for the finance (invoice submit + payment receive) family.

The fake gateway proves gate ordering, the strict bodies, every pre-fence
refusal (wrong role, boundary/install drift, unknown customer, missing or
non-selling price list, past due date, missing accounts, item without a selling
price, duplicate charge, discount not enabled, draft/cancelled/paid invoice,
over-allocation, allocation sum, refused payment mode, mode without a company
account, repeat payment, card without a reference, native authority drift and
budget), the fence-before-native ordering, the closed-world side-effect check,
the receipt and the one-shot ack.

The discount path is exercised both ways: refused pre-fence when the site flag
is off (the recorded fallback cut) and carried on the child row when the flag
and the installed child are both present.
"""

import unittest
from unittest import mock

from clinic_ui.core import config, finance_contract, patient_write, write_fence
from clinic_ui.endpoints import finance_write, session

from .fake_gateway import (
    Denied,
    FakeFinanceReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
CSRF = "csrf-token-value"
KEY = "finance-write-key-0001"

ROLES = {
    "CAS": ("Accounts User",),
    "MGR": ("Accounts Manager",),
    "PHA": ("Stock User",),
}


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_native_finance_write_enabled=1,
        clinic_ui_native_finance_write_site=SITE,
        clinic_ui_role_map={
            "CAS": ["Accounts User"],
            "MGR": ["Accounts Manager"],
            "PHA": ["Stock User"],
        },
    )
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


def gateway(role="CAS", **overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(
            user="cashier@example.test", roles=ROLES[role]
        ),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = FakeGateway(
        **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    fake.finance_invoice_detail = invoice_detail()
    fake.finance_payment_detail = payment_detail()
    fake.finance_baseline_value = {
        "gl_rows": 4,
        "ple_rows": 2,
        "sle_rows": 7,
        "invoice_outstanding": 20.0,
        "invoice_status": "Unpaid",
    }
    return fake


def invoice_body(**overrides):
    values = {
        "operation_key": KEY,
        "customer": "CUST-1",
        "selling_price_list": "Standard Selling",
        "due_date": "2026-11-30",
        "items": [{"item_code": "ITEM-1", "qty": 2}],
    }
    values.update(overrides)
    return values


def payment_body(**overrides):
    values = {
        "operation_key": KEY,
        "invoice": "SINV-1",
        "mode_of_payment": "Cash",
        "amount": 20.0,
        "allocations": [{"invoice": "SINV-1", "amount": 20.0}],
    }
    values.update(overrides)
    return values


def committed(fake, kind="payment_receive", key=KEY):
    values = {
        "kind": kind,
        "reference": "PE-EXISTING" if kind == "payment_receive" else "SINV-1",
        "invoice": "SINV-1",
        "customer": "CUST-1",
        "amount": 20.0,
        "mode_of_payment": "Cash",
        "status": "Paid",
    }
    fake.finance_receipt_records.append(
        FakeFinanceReceipt(
            kind,
            fake.current_session.user,
            key,
            finance_contract.encode_receipt(**values),
        )
    )


class PreviewTests(unittest.TestCase):
    def test_the_preview_returns_server_totals_and_writes_nothing(self):
        fake = gateway()

        payload = finance_write.save_invoice_draft(fake, invoice_body(), CSRF)

        self.assertEqual(payload["status"], "preview")
        self.assertEqual(payload["customer"], "CUST-1")
        self.assertEqual(payload["currency"], "USD")
        self.assertEqual(payload["net_total"], 20.0)
        self.assertEqual(payload["total"], 20.0)
        self.assertEqual(payload["total_taxes"], 0.0)
        self.assertEqual(payload["lines"][0]["rate"], 10.0)
        self.assertEqual(fake.finance_fence_calls, [])
        self.assertEqual(fake.finance_invoice_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_the_preview_names_the_discount_path_state(self):
        fake = gateway()
        payload = finance_write.save_invoice_draft(fake, invoice_body(), CSRF)
        self.assertFalse(payload["discount_available"])

        fake = gateway(
            settings=write_settings(clinic_ui_invoice_discount_enabled=1)
        )
        payload = finance_write.save_invoice_draft(
            fake, invoice_body(discount=2), CSRF
        )
        self.assertTrue(payload["discount_available"])
        self.assertEqual(payload["discount"], 2.0)
        self.assertEqual(payload["total"], 18.0)

    def test_the_preview_refuses_an_unknown_customer(self):
        fake = gateway()
        fake.finance_invoice_detail = invoice_detail(customer_exists=False)

        payload = finance_write.save_invoice_draft(fake, invoice_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(payload["problem"], "customer")
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.finance_invoice_calls, [])

    def test_the_preview_body_is_strict(self):
        fake = gateway()

        payload = finance_write.save_invoice_draft(
            fake, invoice_body(currency="THB"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)

    def test_the_preview_needs_the_finance_gate(self):
        fake = gateway()

        with self.assertRaises(Denied):
            finance_write.save_invoice_draft(fake, invoice_body(), None)

    def test_the_preview_needs_native_invoice_authority(self):
        fake = gateway()
        fake.finance_create_authority = {"Sales Invoice": False}

        with self.assertRaises(Denied):
            finance_write.save_invoice_draft(fake, invoice_body(), CSRF)


class InvoiceSubmitTests(unittest.TestCase):
    def test_one_invoice_is_created_and_submitted_with_server_state(self):
        fake = gateway()

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["invoice"], "SINV-1")
        self.assertEqual(payload["total"], 20.0)
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(fake.finance_fence_calls[0][1], "invoice_submit")
        self.assertEqual(len(fake.finance_invoice_calls), 1)
        self.assertEqual(fake.commit_calls, 1)

    def test_the_prepared_invoice_pins_the_server_state(self):
        fake = gateway()

        finance_write.submit_invoice(fake, invoice_body(), CSRF)

        prepared = fake.finance_invoice_calls[0]
        self.assertEqual(prepared["company"], "SIT Synthetic Company USD")
        self.assertEqual(prepared["currency"], "USD")
        self.assertEqual(prepared["selling_price_list"], "Standard Selling")
        self.assertEqual(prepared["due_date"], "2026-11-30")
        self.assertEqual(prepared["posting_date"], "2026-11-16")
        self.assertEqual(prepared["update_stock"], 0)
        self.assertIs(type(prepared["update_stock"]), int)
        self.assertEqual(prepared["debit_to"], "Accounts Receivable - SITCO")
        self.assertEqual(prepared["taxes"], [])
        self.assertEqual(prepared["deductions"], [])
        line = prepared["items"][0]
        self.assertEqual(line["rate"], 10.0)
        self.assertEqual(line["amount"], 20.0)
        self.assertEqual(line["income_account"], "Sales - SITCO")
        self.assertEqual(line["cost_center"], "Main - SITCO")
        self.assertNotIn("grand_total", prepared)
        self.assertNotIn("docstatus", prepared)

    def test_the_posted_total_is_the_server_total_not_the_client_arithmetic(self):
        fake = gateway()

        finance_write.submit_invoice(
            fake, invoice_body(items=[{"item_code": "ITEM-1", "qty": 3}]), CSRF
        )

        expected = fake.finance_facts_calls[0][1]
        self.assertEqual(expected["grand_total"], 30.0)
        self.assertEqual(expected["debit_total"], 30.0)
        self.assertEqual(expected["credit_total"], 30.0)
        prepared = fake.finance_invoice_calls[0]
        self.assertNotIn("grand_total", prepared)
        self.assertNotIn("net_total", prepared)
        self.assertEqual(prepared["items"][0]["amount"], 30.0)

    def test_an_unknown_customer_never_reaches_the_fence(self):
        fake = gateway()
        fake.finance_invoice_detail = invoice_detail(customer_exists=False)

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])
        self.assertEqual(fake.finance_invoice_calls, [])

    def test_a_body_rate_is_refused_before_validation_accepts_it(self):
        fake = gateway()

        payload = finance_write.submit_invoice(
            fake, invoice_body(items=[{"item_code": "ITEM-1", "qty": 1, "rate": 1}]),
            CSRF,
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.finance_fence_calls, [])

    def test_update_stock_can_never_be_flipped_from_the_body(self):
        fake = gateway()

        payload = finance_write.submit_invoice(
            fake, invoice_body(update_stock=1), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.finance_fence_calls, [])

    def test_a_discount_without_the_permission_flag_is_refused_pre_fence(self):
        fake = gateway()

        payload = finance_write.submit_invoice(
            fake, invoice_body(discount=2), CSRF
        )

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(payload["problem"], "discount")
        self.assertEqual(fake.finance_fence_calls, [])
        self.assertEqual(fake.finance_invoice_calls, [])

    def test_a_discount_rides_the_deductions_child_when_available(self):
        fake = gateway(
            settings=write_settings(clinic_ui_invoice_discount_enabled=1)
        )

        payload = finance_write.submit_invoice(
            fake, invoice_body(discount=2), CSRF
        )

        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["total"], 18.0)
        prepared = fake.finance_invoice_calls[0]
        self.assertEqual(prepared["discount"], 2.0)
        self.assertEqual(len(prepared["deductions"]), 1)
        self.assertEqual(prepared["deductions"][0]["amount"], 2.0)
        self.assertNotIn("discount_amount", prepared)

    def test_a_duplicate_charge_is_refused_pre_fence(self):
        fake = gateway()

        payload = finance_write.submit_invoice(
            fake,
            invoice_body(
                items=[
                    {"item_code": "ITEM-1", "qty": 1},
                    {"item_code": "ITEM-1", "qty": 2},
                ]
            ),
            CSRF,
        )

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_the_baseline_is_read_before_the_fence(self):
        fake = gateway()

        finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(fake.finance_baseline_calls, [("invoice_submit", None)])
        self.assertEqual(
            fake.finance_facts_calls[0][2], fake.finance_baseline_value
        )


class PaymentReceiveTests(unittest.TestCase):
    def test_one_payment_is_posted_with_a_matching_allocation(self):
        fake = gateway()

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload["status"], "received")
        self.assertEqual(payload["payment"], "PE-1")
        self.assertEqual(payload["invoice"], "SINV-1")
        self.assertEqual(payload["outstanding"], 0.0)
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(fake.finance_fence_calls[0][1], "payment_receive")
        prepared = fake.finance_payment_calls[0]
        self.assertEqual(prepared["payment_type"], "Receive")
        self.assertEqual(prepared["party_type"], "Customer")
        self.assertEqual(prepared["party"], "CUST-1")
        self.assertEqual(prepared["paid_from"], "Accounts Receivable - SITCO")
        self.assertEqual(prepared["paid_to"], "Cash - SITCO")
        self.assertEqual(prepared["paid_amount"], 20.0)
        self.assertEqual(prepared["received_amount"], 20.0)
        self.assertEqual(len(prepared["references"]), 1)
        self.assertEqual(
            prepared["references"][0]["reference_name"], "SINV-1"
        )
        self.assertEqual(
            prepared["references"][0]["allocated_amount"], 20.0
        )
        self.assertEqual(fake.commit_calls, 1)

    def test_no_card_field_is_ever_prepared(self):
        fake = gateway()

        finance_write.receive_payment(fake, payment_body(), CSRF)

        prepared = fake.finance_payment_calls[0]
        for key in ("card_number", "cvv", "pan"):
            self.assertNotIn(key, prepared)
        self.assertNotIn("4111", repr(prepared))

    def test_a_card_body_with_card_data_is_refused(self):
        fake = gateway()

        payload = finance_write.receive_payment(
            fake, payment_body(card_number="4111111111111111"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.finance_payment_calls, [])

    def test_bank_transfer_is_refused_pre_fence_by_name(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            mode={"exists": True, "account": "Bank - SITCO", "accepted": False}
        )

        payload = finance_write.receive_payment(
            fake, payment_body(mode_of_payment="Bank Transfer"), CSRF
        )

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(payload["problem"], "mode_of_payment")
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.finance_fence_calls, [])
        self.assertEqual(fake.finance_payment_calls, [])

    def test_a_draft_invoice_is_refused_pre_fence(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 0,
                "status": "Draft",
                "outstanding_amount": 20.0,
                "grand_total": 20.0,
                "customer": "CUST-1",
            }
        )

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_a_cancelled_invoice_is_refused_pre_fence(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 2,
                "status": "Cancelled",
                "outstanding_amount": 20.0,
                "grand_total": 20.0,
                "customer": "CUST-1",
            }
        )

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_an_over_allocation_is_refused_pre_fence(self):
        fake = gateway()

        payload = finance_write.receive_payment(
            fake,
            payment_body(
                amount=25.0,
                allocations=[{"invoice": "SINV-1", "amount": 25.0}],
            ),
            CSRF,
        )

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(payload["problem"], "amount")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_an_allocation_sum_mismatch_is_refused_pre_fence(self):
        fake = gateway()

        payload = finance_write.receive_payment(
            fake,
            payment_body(
                amount=20.0,
                allocations=[{"invoice": "SINV-1", "amount": 15.0}],
            ),
            CSRF,
        )

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(payload["problem"], "allocations")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_a_repeat_payment_is_refused_pre_fence(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(payment_exists=True)

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_a_mode_without_an_account_is_refused_pre_fence(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            mode={"exists": True, "account": "", "accepted": True}
        )

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

    def test_a_card_payment_needs_a_terminal_reference(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            mode={"exists": True, "account": "Card - SITCO", "accepted": True}
        )

        payload = finance_write.receive_payment(
            fake, payment_body(mode_of_payment="Credit Card"), CSRF
        )
        self.assertEqual(payload["code"], "VALIDATION")
        self.assertEqual(fake.finance_fence_calls, [])

        payload = finance_write.receive_payment(
            fake,
            payment_body(mode_of_payment="Credit Card", reference_no="TERM-001"),
            CSRF,
        )
        self.assertEqual(payload["status"], "received")
        self.assertEqual(
            fake.finance_payment_calls[0]["reference_no"], "TERM-001"
        )

    def test_a_partial_payment_reports_the_server_balance(self):
        fake = gateway()
        fake.finance_payment_detail = payment_detail(
            invoice={
                "exists": True,
                "docstatus": 1,
                "status": "Unpaid",
                "outstanding_amount": 50.0,
                "grand_total": 50.0,
                "customer": "CUST-1",
            }
        )

        payload = finance_write.receive_payment(
            fake,
            payment_body(amount=20.0),
            CSRF,
        )

        self.assertEqual(payload["outstanding"], 30.0)
        expected = fake.finance_facts_calls[0][1]
        self.assertIsNone(expected["status"])
        self.assertEqual(expected["unchanged_status"], "Unpaid")

    def test_the_payment_body_is_strict(self):
        for candidate in (
            payment_body(amount=0),
            payment_body(allocations=[]),
            payment_body(docstatus=1),
            payment_body(supplier="SUP-1"),
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = finance_write.receive_payment(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 422)
                self.assertEqual(fake.finance_fence_calls, [])


class GateTests(unittest.TestCase):
    def test_the_role_gate_is_shared_by_all_three_operations(self):
        for operation, body in (
            (finance_write.save_invoice_draft, invoice_body()),
            (finance_write.submit_invoice, invoice_body()),
            (finance_write.receive_payment, payment_body()),
        ):
            with self.subTest(operation=operation.__name__):
                fake = gateway(role="PHA")
                with self.assertRaises(Denied):
                    operation(fake, body, CSRF)
                self.assertEqual(fake.finance_fence_calls, [])
                self.assertEqual(fake.finance_invoice_calls, [])

    def test_a_manager_is_not_a_cashier(self):
        fake = gateway(role="MGR")

        with self.assertRaises(Denied):
            finance_write.submit_invoice(fake, invoice_body(), CSRF)

    def test_the_finance_flag_is_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_finance_write_enabled=0)
        )

        with self.assertRaises(Denied):
            finance_write.submit_invoice(fake, invoice_body(), CSRF)
        self.assertEqual(fake.finance_fence_calls, [])

    def test_the_purchasing_flag_alone_does_not_open_finance(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_purchase_write_enabled=1,
                clinic_ui_native_purchase_write_site=SITE,
                clinic_ui_native_finance_write_enabled=0,
            )
        )

        with self.assertRaises(Denied):
            finance_write.receive_payment(fake, payment_body(), CSRF)

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway()
        fake.finance_boundary_problem = "versions drift for erpnext"
        with self.assertRaises(Denied):
            finance_write.submit_invoice(fake, invoice_body(), CSRF)
        self.assertEqual(fake.finance_fence_calls, [])

        other = gateway()
        other.finance_install_problem = "missing field Sales Invoice.debit_to"
        with self.assertRaises(Denied):
            finance_write.submit_invoice(other, invoice_body(), CSRF)
        self.assertEqual(other.finance_fence_calls, [])

    def test_the_native_authority_is_required_per_document(self):
        fake = gateway()
        fake.finance_create_authority = {"Sales Invoice": True, "Payment Entry": False}
        self.assertEqual(
            finance_write.submit_invoice(fake, invoice_body(), CSRF)["status"],
            "submitted",
        )
        with self.assertRaises(Denied):
            finance_write.receive_payment(fake, payment_body(), CSRF)

    def test_an_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        committed(fake, "invoice_submit", "finance-write-key-0000")

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.finance_fence_calls, [])
        self.assertEqual(fake.finance_invoice_calls, [])

    def test_each_kind_has_its_own_budget(self):
        fake = gateway()
        committed(fake, "invoice_submit", "finance-write-key-0000")

        self.assertEqual(
            finance_write.submit_invoice(fake, invoice_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(
            finance_write.receive_payment(fake, payment_body(), CSRF)["status"],
            "received",
        )


class FenceAndUncertaintyTests(unittest.TestCase):
    def test_a_concurrent_change_rolls_back_without_committing(self):
        fake = gateway()
        fake.finance_detail_sequence = [
            payment_detail(),
            payment_detail(payment_exists=True),
        ]

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.finance_payment_calls, [])
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_side_effect_mismatch_rolls_back(self):
        fake = gateway()
        fake.finance_facts = {
            "doctype": "Sales Invoice",
            "docstatus": 1,
            "status": "Unpaid",
            "gl_rows": 0,
        }

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.finance_mutation_error = patient_write.WriteUncertain()

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.finance_fence_claim.unknowns, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_an_unexpected_native_error_marks_unknown_and_rolls_back(self):
        fake = gateway()
        fake.finance_mutation_error = RuntimeError("native exploded")

        payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.finance_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_the_ack_endpoint_is_the_only_acknowledgement(self):
        fake = gateway()

        finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(fake.finance_ack_calls, [])
        self.assertEqual(len(fake.finance_fence_claim.committed), 1)


class ReceiptEncodeBeforeCommitTests(unittest.TestCase):
    """The terminal receipt is encoded before the one native commit.

    Encoding after the commit would land an invoice or a payment whose only
    durable record is ``UNKNOWN``; encoding first makes a codec refusal
    deterministic while the native transaction is still uncommitted, so the
    rollback discards it and no ``COMMITTED`` terminal is appended.
    """

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal(self):
        fake = gateway()
        real_encode = finance_contract.encode_receipt

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            self.assertEqual(len(fake.finance_invoice_calls), 1)
            return real_encode(**kwargs)

        claim = fake.finance_fence_claim
        real_claim_commit = claim.commit

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            return real_claim_commit(receipt)

        claim.commit = recording_claim_commit

        with mock.patch.object(
            finance_contract, "encode_receipt", recording_encode
        ):
            payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(claim.committed), 1)
        # The wire body is what the ordering change must not move.
        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["invoice"], "SINV-1")
        self.assertEqual(payload["total"], 20.0)
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        fake = gateway()

        with mock.patch.object(
            finance_contract,
            "encode_receipt",
            side_effect=finance_contract.FinanceContractError(
                "invalid receipt reference"
            ),
        ):
            payload = finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.finance_payment_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertTrue(fake.finance_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.finance_receipt_records, [])

    def test_the_ack_token_is_bound_to_the_receipt_that_was_stored(self):
        fake = gateway()

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        (stored,) = fake.finance_fence_claim.committed
        self.assertEqual(
            payload["ackToken"],
            finance_contract.ack_token(
                SECRET.encode(),
                SITE,
                fake.current_session.user,
                "invoice_submit",
                KEY,
                stored,
            ),
        )

    def test_a_native_commit_failure_ends_unknown_without_a_terminal(self):
        fake = gateway()

        def failing_commit():
            fake.call_order.append("commit")
            raise RuntimeError("native commit failed")

        fake.commit = failing_commit

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.call_order.count("commit"), 1)
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertTrue(fake.finance_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.finance_receipt_records, [])

    def test_a_terminal_claim_failure_after_the_commit_stays_uncertain(self):
        """The native write is durable, so the answer cannot be a retry."""

        fake = gateway()

        def failing_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            raise RuntimeError("terminal append failed")

        fake.finance_fence_claim.commit = failing_claim_commit

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertTrue(fake.finance_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.finance_receipt_records, [])


class RollbackFailureTests(unittest.TestCase):
    """A failed rollback must escape the endpoint, never become a normal 409.

    A normal response would let Frappe's end-of-request commit for an unsafe
    POST persist the partial native write, so the failure is raised to Frappe
    cleanup instead.
    """

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        fake = gateway()
        fake.finance_mutation_error = patient_write.WriteUncertain()

        def failing_rollback():
            fake.rollback_calls += 1
            raise RuntimeError("native rollback failed")

        fake.rollback = failing_rollback

        with self.assertRaises(finance_write.RollbackFailed):
            finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.finance_fence_claim.committed, [])

    def test_an_encoder_refusal_with_a_failed_rollback_escapes_from_the_generic_branch(
        self,
    ):
        """The unexpected-failure branch marks ``UNKNOWN`` first, then rolls back.

        Encoding is patched to raise a plain error (not a contract or fence
        type), so the operation falls into the generic handler: the marker is
        attempted before the rollback, and a failed rollback must still escape
        rather than answer with a normal 409.
        """

        fake = gateway()
        order = []
        claim = fake.finance_fence_claim
        real_unknown = claim.unknown

        def recording_unknown(reason):
            order.append("marker")
            return real_unknown(reason)

        def failing_rollback():
            order.append("rollback")
            fake.rollback_calls += 1
            raise RuntimeError("native rollback failed")

        claim.unknown = recording_unknown
        fake.rollback = failing_rollback

        with mock.patch.object(
            finance_contract,
            "encode_receipt",
            side_effect=RuntimeError("receipt encoder failed"),
        ):
            with self.assertRaises(finance_write.RollbackFailed) as caught:
                finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertEqual(order, ["marker", "rollback"])
        self.assertEqual(len(claim.unknowns), 1)
        self.assertEqual(claim.committed, [])
        self.assertEqual(fake.write_responses, [])
        self.assertIs(type(caught.exception), finance_write.RollbackFailed)
        self.assertNotIsInstance(
            caught.exception,
            (write_fence.FenceError, patient_write.WriteUncertain),
        )

    def test_a_named_refusal_with_a_failed_rollback_escapes_without_a_marker(self):
        """A named dispatch refusal rolls back only; no ``UNKNOWN`` is written."""

        fake = gateway()
        fake.finance_detail_sequence = [
            payment_detail(),
            payment_detail(payment_exists=True),
        ]

        def failing_rollback():
            fake.rollback_calls += 1
            raise RuntimeError("native rollback failed")

        fake.rollback = failing_rollback

        with self.assertRaises(finance_write.RollbackFailed) as caught:
            finance_write.receive_payment(fake, payment_body(), CSRF)

        self.assertEqual(fake.finance_fence_claim.unknowns, [])
        self.assertEqual(fake.finance_fence_claim.committed, [])
        self.assertEqual(fake.write_responses, [])
        self.assertIs(type(caught.exception), finance_write.RollbackFailed)
        self.assertNotIsInstance(
            caught.exception,
            (write_fence.FenceError, patient_write.WriteUncertain),
        )


class AckTests(unittest.TestCase):
    def ack_body(self, **overrides):
        values = {
            "operation_key": KEY,
            "kind": "invoice_submit",
            "ack_token": "a" * 64,
        }
        values.update(overrides)
        return values

    def test_a_valid_ack_is_recorded_once(self):
        fake = gateway()

        payload = finance_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.finance_ack_calls), 1)

    def test_an_invalid_ack_is_a_conflict(self):
        fake = gateway()
        fake.finance_ack_status = "invalid"

        payload = finance_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})

    def test_a_refund_kind_can_never_be_acknowledged(self):
        fake = gateway()

        payload = finance_write.acknowledge(
            fake, self.ack_body(kind="payment_refund"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.finance_ack_calls, [])

    def test_a_malformed_ack_body_is_validation(self):
        fake = gateway()

        payload = finance_write.acknowledge(
            fake, self.ack_body(ack_token="short"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.finance_ack_calls, [])


class BootstrapTests(unittest.TestCase):
    def test_a_cashier_sees_the_finance_surfaces(self):
        capabilities = session.bootstrap(gateway())["capabilities"]
        self.assertEqual(capabilities["write.invoice.submit"], "enabled")
        self.assertEqual(capabilities["write.payment.receive"], "enabled")

    def test_void_amend_and_refund_stay_read_only(self):
        capabilities = session.bootstrap(gateway())["capabilities"]
        self.assertEqual(capabilities["write.invoice.void_amend"], "read_only")
        self.assertEqual(capabilities["write.payment.refund"], "read_only")

    def test_another_role_sees_nothing(self):
        capabilities = session.bootstrap(gateway(role="PHA"))["capabilities"]
        self.assertEqual(capabilities["write.invoice.submit"], "read_only")
        self.assertEqual(capabilities["write.payment.receive"], "read_only")

    def test_a_closed_gate_publishes_nothing(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_finance_write_enabled=0)
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.invoice.submit"], "read_only")
        self.assertEqual(capabilities["write.payment.receive"], "read_only")


class SecretTests(unittest.TestCase):
    def test_the_response_never_echoes_the_secret(self):
        fake = gateway()

        payload = finance_write.submit_invoice(fake, invoice_body(), CSRF)

        self.assertNotIn(SECRET, repr(payload))


if __name__ == "__main__":
    unittest.main()
