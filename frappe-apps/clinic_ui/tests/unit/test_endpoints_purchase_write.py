"""Endpoint tests for the purchasing (PO draft/submit + receipt) family.

The fake gateway proves gate ordering, the strict bodies, every pre-fence
refusal (role, boundary/install drift, unknown supplier/item/warehouse, an
itemization gap, a non-draft submit target, an unknown or non-submitted PO,
over-receipt with a named line, batch gaps, native authority drift, budget), the
fence-before-native ordering, the closed-world side-effect check, the receipt
and the one-shot ack.
"""

import unittest
from unittest import mock

from clinic_ui.core import config, patient_write, purchase_contract, write_fence
from clinic_ui.endpoints import purchase_write, session

from .fake_gateway import (
    Denied,
    FakeGateway,
    FakePurchaseReceipt,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
CSRF = "csrf-token-value"
KEY = "purchase-write-key-0001"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_native_purchase_write_enabled=1,
        clinic_ui_native_purchase_write_site=SITE,
        clinic_ui_role_map={
            "PHA": ["Stock User"],
            "MGR": ["Purchase Manager"],
            "REC": ["Clinic Reception"],
        },
    )
    values.update(overrides)
    return values


ROLES = {
    "PHA": ("Stock User",),
    "MGR": ("Purchase Manager",),
    "REC": ("Clinic Reception",),
}


def item(**overrides):
    values = {
        "item_code": "ITEM-1",
        "exists": True,
        "has_batch_no": True,
        "has_expiry_date": True,
        "is_stock_item": True,
        "stock_uom": "Unit",
    }
    values.update(overrides)
    return values


def draft_detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "currency": "USD",
        "supplier_exists": True,
        "items": {"ITEM-1": item()},
    }
    values.update(overrides)
    return values


def submit_detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "currency": "USD",
        "supplier_exists": True,
        "purchase_order": {
            "exists": True,
            "docstatus": 0,
            "supplier": "SUP-1",
            "status": "Draft",
        },
        "lines": [
            {
                "item_code": "ITEM-1",
                "qty": 5.0,
                "rate": 2.5,
                "uom": "Unit",
                "po_item": "POI-1",
            }
        ],
    }
    values.update(overrides)
    return values


def receipt_detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "currency": "USD",
        "supplier": "SUP-1",
        "supplier_exists": True,
        "item": item(),
        "warehouse": {"exists": True, "company": "SIT Synthetic Company USD"},
        "purchase_order": {
            "exists": True,
            "docstatus": 1,
            "status": "To Receive and Bill",
        },
        "po_pending_qty": 5.0,
        "po_item": "POI-1",
        "batch": {
            "batch_id": "BATCH-1",
            "exists": True,
            "item": "ITEM-1",
            "expiry_date": "2027-01-01",
            "batch_qty": 0.0,
        },
    }
    values.update(overrides)
    return values


def gateway(role="PHA", **overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(
            user="purchasing@example.test", roles=ROLES[role]
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
    fake.site_today_value = "2026-11-16"
    fake.purchase_draft_detail = draft_detail()
    fake.purchase_submit_detail = submit_detail()
    fake.purchase_receipt_detail = receipt_detail()
    fake.purchase_baseline_value = {
        "stock_ledger_rows": 5,
        "gl_rows": 10,
        "bins": {"WH-1": 10.0},
        "po_received": 0.0,
    }
    return fake


def draft_body(**overrides):
    values = {
        "operation_key": KEY,
        "supplier": "SUP-1",
        "items": [{"item_code": "ITEM-1", "qty": 5, "rate": 2.5}],
    }
    values.update(overrides)
    return values


def submit_body(**overrides):
    values = {"operation_key": KEY, "purchase_order": "PO-1"}
    values.update(overrides)
    return values


def receipt_body(**overrides):
    values = {
        "operation_key": KEY,
        "purchase_order": "PO-1",
        "item_code": "ITEM-1",
        "qty": 2.0,
        "warehouse": "WH-1",
        "batch_id": "BATCH-1",
    }
    values.update(overrides)
    return values


def committed(fake, kind="purchase_receipt", key=KEY):
    values = {
        "kind": kind,
        "reference": "PO-EXISTING",
        "purchase_order": "PO-EXISTING",
    }
    if kind == "purchase_receipt":
        values.update(
            {
                "reference": "PR-EXISTING",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "qty": 1.0,
            }
        )
    fake.purchase_receipt_records.append(
        FakePurchaseReceipt(
            kind,
            fake.current_session.user,
            key,
            purchase_contract.encode_receipt(**values),
        )
    )


class DraftTests(unittest.TestCase):
    def test_a_draft_is_inserted_without_submitting(self):
        fake = gateway()

        payload = purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "drafted")
        self.assertEqual(payload["purchase_order"], "PO-DRAFT-1")
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(fake.purchase_fence_calls[0][1], "purchase_order_draft")
        self.assertEqual(len(fake.purchase_draft_calls), 1)
        self.assertEqual(fake.purchase_submit_order_calls, [])
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.purchase_ack_calls, [])

    def test_the_prepared_draft_carries_server_money_and_units(self):
        fake = gateway()

        purchase_write.save_po_draft(fake, draft_body(), CSRF)

        prepared = fake.purchase_draft_calls[0]
        self.assertEqual(prepared["company"], "SIT Synthetic Company USD")
        self.assertEqual(prepared["currency"], "USD")
        self.assertEqual(prepared["supplier"], "SUP-1")
        self.assertEqual(prepared["transaction_date"], "2026-11-16")
        self.assertNotIn("docstatus", prepared)
        line = prepared["items"][0]
        self.assertEqual(line["uom"], "Unit")
        self.assertEqual(line["stock_uom"], "Unit")
        self.assertEqual(line["rate"], 2.5)
        self.assertEqual(line["schedule_date"], "2026-11-16")

    def test_the_baseline_is_read_before_the_fence(self):
        fake = gateway()

        purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(len(fake.purchase_baseline_calls), 1)
        kind, warehouses, item_code, order = fake.purchase_baseline_calls[0]
        self.assertEqual(kind, "purchase_order_draft")
        self.assertEqual(tuple(warehouses), ())
        self.assertEqual(item_code, "ITEM-1")
        self.assertIsNone(order)
        self.assertEqual(fake.purchase_facts_calls[0][2], fake.purchase_baseline_value)

    def test_an_unknown_supplier_is_refused_before_the_fence(self):
        fake = gateway()
        fake.purchase_draft_detail = draft_detail(supplier_exists=False)

        payload = purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.purchase_fence_calls, [])
        self.assertEqual(fake.purchase_draft_calls, [])

    def test_an_item_without_a_unit_is_refused_before_the_fence(self):
        fake = gateway()
        fake.purchase_draft_detail = draft_detail(
            items={"ITEM-1": item(stock_uom="")}
        )

        payload = purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_the_draft_body_is_strict(self):
        for candidate in (
            {},
            draft_body(items=[]),
            draft_body(items=[{"item_code": "ITEM-1", "qty": 5}]),
            draft_body(currency="THB"),
            draft_body(docstatus=1),
            draft_body(operation_key="short"),
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = purchase_write.save_po_draft(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 422)
                self.assertEqual(fake.purchase_fence_calls, [])


class SubmitTests(unittest.TestCase):
    def test_a_draft_is_submitted_once_with_the_server_status(self):
        fake = gateway()

        payload = purchase_write.submit_po(fake, submit_body(), CSRF)

        self.assertEqual(payload["status"], "submitted")
        self.assertEqual(payload["purchase_order"], "PO-SUBMITTED-1")
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            fake.purchase_fence_calls[0][1], "purchase_order_submit"
        )
        self.assertEqual(len(fake.purchase_submit_order_calls), 1)
        prepared = fake.purchase_submit_order_calls[0]
        self.assertEqual(prepared["purchase_order"], "PO-1")
        self.assertEqual(prepared["docstatus"], 0)
        self.assertEqual(prepared["company"], "SIT Synthetic Company USD")
        self.assertEqual(prepared["currency"], "USD")
        expected = fake.purchase_facts_calls[0][1]
        self.assertEqual(expected["status"], "To Receive and Bill")
        self.assertEqual(expected["docstatus"], 1)
        self.assertEqual(fake.commit_calls, 1)

    def test_a_non_draft_target_is_refused_before_the_fence(self):
        fake = gateway()
        fake.purchase_submit_detail = submit_detail(
            purchase_order={
                "exists": True,
                "docstatus": 1,
                "supplier": "SUP-1",
                "status": "To Receive and Bill",
            }
        )

        payload = purchase_write.submit_po(fake, submit_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.purchase_fence_calls, [])
        self.assertEqual(fake.purchase_submit_order_calls, [])

    def test_the_submit_body_carries_no_submit_flag(self):
        for candidate in (
            submit_body(docstatus=1),
            submit_body(submit=True),
        ):
            fake = gateway()
            payload = purchase_write.submit_po(fake, candidate, CSRF)
            self.assertEqual(payload, {"code": "VALIDATION"})
            self.assertEqual(fake.purchase_fence_calls, [])

        fake = gateway()
        purchase_write.submit_po(fake, submit_body(), CSRF)
        self.assertNotIn("submit", fake.purchase_submit_order_calls[0])


class ReceiptTests(unittest.TestCase):
    def test_a_po_referenced_receipt_is_submitted_once(self):
        fake = gateway()

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload["status"], "received")
        self.assertEqual(
            payload["movement"],
            {
                "kind": "purchase_receipt",
                "purchase_order": "PO-1",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "qty": 2.0,
                "batch": "BATCH-1",
            },
        )
        self.assertNotIn("PR-1", repr(payload))
        self.assertEqual(fake.purchase_fence_calls[0][1], "purchase_receipt")
        self.assertEqual(len(fake.purchase_receipt_calls), 1)
        prepared = fake.purchase_receipt_calls[0]
        self.assertEqual(prepared["purchase_order"], "PO-1")
        self.assertEqual(prepared["po_item"], "POI-1")
        self.assertEqual(prepared["supplier"], "SUP-1")
        self.assertTrue(prepared["set_posting_time"])
        self.assertEqual(prepared["company"], "SIT Synthetic Company USD")
        self.assertEqual(fake.commit_calls, 1)

    def test_a_new_batch_is_created_in_the_same_operation(self):
        fake = gateway()
        fake.purchase_receipt_detail = receipt_detail(
            batch={"batch_id": "NEW", "exists": False, "item": "ITEM-1"}
        )

        body = receipt_body(batch_id="NEW", batch_expiry="2027-05-01")
        payload = purchase_write.receive(fake, body, CSRF)

        self.assertEqual(payload["status"], "received")
        prepared = fake.purchase_receipt_calls[0]
        self.assertTrue(prepared["create_batch"])
        self.assertEqual(prepared["batch_expiry"], "2027-05-01")
        self.assertEqual(fake.purchase_fence_calls[0][1], "purchase_receipt")

    def test_the_receipt_baseline_covers_the_po_line(self):
        fake = gateway()

        purchase_write.receive(fake, receipt_body(), CSRF)

        kind, warehouses, item_code, order = fake.purchase_baseline_calls[0]
        self.assertEqual(kind, "purchase_receipt")
        self.assertEqual(tuple(warehouses), ("WH-1",))
        self.assertEqual(item_code, "ITEM-1")
        self.assertEqual(order, "PO-1")

    def test_an_over_receipt_names_the_line_and_never_claims(self):
        fake = gateway()
        fake.purchase_receipt_detail = receipt_detail(po_pending_qty=1.0)

        payload = purchase_write.receive(fake, receipt_body(qty=2.0), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.purchase_fence_calls, [])
        self.assertEqual(fake.purchase_receipt_calls, [])

    def test_the_receipt_requires_the_po_reference(self):
        for detail in (
            {"exists": False, "docstatus": None},
            {"exists": True, "docstatus": 0, "status": "Draft"},
        ):
            fake = gateway()
            fake.purchase_receipt_detail = receipt_detail(
                purchase_order=detail, po_pending_qty=None, po_item=None
            )
            payload = purchase_write.receive(fake, receipt_body(), CSRF)
            self.assertEqual(payload, {"code": "VALIDATION"})
            self.assertEqual(fake.purchase_fence_calls, [])

    def test_the_receipt_body_is_strict(self):
        for candidate in (
            receipt_body(purchase_order=""),
            receipt_body(warehouse=""),
            receipt_body(qty=0),
            receipt_body(supplier="SUP-9"),
            receipt_body(docstatus=1),
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = purchase_write.receive(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 422)
                self.assertEqual(fake.purchase_fence_calls, [])


class GateTests(unittest.TestCase):
    def test_the_role_gate_is_shared_by_all_three_operations(self):
        for operation, body in (
            (purchase_write.save_po_draft, draft_body()),
            (purchase_write.submit_po, submit_body()),
            (purchase_write.receive, receipt_body()),
        ):
            with self.subTest(operation=operation.__name__):
                fake = gateway(role="REC")
                with self.assertRaises(Denied):
                    operation(fake, body, CSRF)
                self.assertEqual(fake.purchase_fence_calls, [])

    def test_a_manager_may_draft_submit_and_receive(self):
        for operation, body in (
            (purchase_write.save_po_draft, draft_body()),
            (purchase_write.submit_po, submit_body()),
            (purchase_write.receive, receipt_body()),
        ):
            with self.subTest(operation=operation.__name__):
                fake = gateway(role="MGR")
                payload = operation(fake, body, CSRF)
                self.assertNotIn("code", payload)

    def test_the_shared_gate_refuses_before_validation(self):
        fake = gateway()
        with self.assertRaises(Denied):
            purchase_write.save_po_draft(fake, draft_body(), None)
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_the_purchase_flag_is_required(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_purchase_write_enabled=0
            )
        )
        with self.assertRaises(Denied):
            purchase_write.save_po_draft(fake, draft_body(), CSRF)
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_the_stock_flag_alone_does_not_open_purchasing(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_stock_write_enabled=1,
                clinic_ui_native_stock_write_site=SITE,
                clinic_ui_native_purchase_write_enabled=0,
            )
        )
        with self.assertRaises(Denied):
            purchase_write.receive(fake, receipt_body(), CSRF)
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway()
        fake.purchase_boundary_problem = "versions drift for erpnext"
        with self.assertRaises(Denied):
            purchase_write.save_po_draft(fake, draft_body(), CSRF)
        self.assertEqual(fake.purchase_fence_calls, [])

        other = gateway()
        other.purchase_install_problem = (
            "missing field Purchase Order.currency"
        )
        with self.assertRaises(Denied):
            purchase_write.save_po_draft(other, draft_body(), CSRF)
        self.assertEqual(other.purchase_fence_calls, [])

    def test_the_native_create_authority_is_required(self):
        fake = gateway()
        fake.purchase_create_authority = {"Purchase Order": False}
        with self.assertRaises(Denied):
            purchase_write.save_po_draft(fake, draft_body(), CSRF)
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_a_new_batch_needs_the_batch_create_authority(self):
        fake = gateway()
        fake.purchase_receipt_detail = receipt_detail(
            batch={"batch_id": "NEW", "exists": False, "item": "ITEM-1"}
        )
        fake.purchase_create_authority = {"Purchase Receipt": True, "Batch": False}

        with self.assertRaises(Denied):
            purchase_write.receive(
                fake, receipt_body(batch_id="NEW", batch_expiry="2027-05-01"), CSRF
            )
        self.assertEqual(fake.purchase_fence_calls, [])

    def test_an_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        committed(fake, "purchase_receipt", "purchase-write-key-0000")

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.purchase_fence_calls, [])
        self.assertEqual(fake.purchase_receipt_calls, [])

    def test_each_kind_has_its_own_budget(self):
        fake = gateway()
        committed(fake, "purchase_order_draft", "purchase-write-key-0000")

        self.assertEqual(
            purchase_write.save_po_draft(fake, draft_body(), CSRF),
            {"code": "WRITE_LOCKED"},
        )
        self.assertEqual(
            purchase_write.submit_po(fake, submit_body(), CSRF)["status"],
            "submitted",
        )


class FenceAndUncertaintyTests(unittest.TestCase):
    def test_a_concurrent_change_rolls_back_without_committing(self):
        fake = gateway()
        fake.purchase_detail_sequence = [
            receipt_detail(),
            receipt_detail(item=item(exists=False)),
        ]

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.purchase_receipt_calls, [])
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_side_effect_mismatch_rolls_back(self):
        fake = gateway()
        fake.purchase_facts = {
            "docstatus": 1,
            "doctype": "Purchase Receipt",
            "sle_rows": 0,
        }

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.purchase_mutation_error = patient_write.WriteUncertain()

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.purchase_fence_claim.unknowns, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_an_unexpected_native_error_marks_unknown_and_rolls_back(self):
        fake = gateway()
        fake.purchase_mutation_error = RuntimeError("native exploded")

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.purchase_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_the_ack_endpoint_is_the_only_acknowledgement(self):
        fake = gateway()

        purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(fake.purchase_ack_calls, [])
        self.assertEqual(len(fake.purchase_fence_claim.committed), 1)


class ReceiptEncodeBeforeCommitTests(unittest.TestCase):
    """The terminal receipt is encoded before the one native commit.

    Encoding after the commit would land a purchase order or receipt whose only
    durable record is ``UNKNOWN``; encoding first makes a codec refusal
    deterministic while the native transaction is still uncommitted, so the
    rollback discards it and no ``COMMITTED`` terminal is appended.
    """

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal(self):
        fake = gateway()
        real_encode = purchase_contract.encode_receipt

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            self.assertEqual(len(fake.purchase_receipt_calls), 1)
            return real_encode(**kwargs)

        claim = fake.purchase_fence_claim
        real_claim_commit = claim.commit

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            return real_claim_commit(receipt)

        claim.commit = recording_claim_commit

        with mock.patch.object(
            purchase_contract, "encode_receipt", recording_encode
        ):
            payload = purchase_write.receive(fake, receipt_body(), CSRF)

        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(claim.committed), 1)
        # The wire body is what the ordering change must not move.
        self.assertEqual(payload["status"], "received")
        self.assertEqual(
            payload["movement"],
            {
                "kind": "purchase_receipt",
                "purchase_order": "PO-1",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "qty": 2.0,
                "batch": "BATCH-1",
            },
        )
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        fake = gateway()

        with mock.patch.object(
            purchase_contract,
            "encode_receipt",
            side_effect=purchase_contract.PurchaseContractError(
                "invalid receipt reference"
            ),
        ):
            payload = purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.purchase_draft_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertTrue(fake.purchase_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.purchase_receipt_records, [])

    def test_the_ack_token_is_bound_to_the_receipt_that_was_stored(self):
        fake = gateway()

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        (stored,) = fake.purchase_fence_claim.committed
        self.assertEqual(
            payload["ackToken"],
            purchase_contract.ack_token(
                SECRET.encode(),
                SITE,
                fake.current_session.user,
                "purchase_receipt",
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

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.call_order.count("commit"), 1)
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertTrue(fake.purchase_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.purchase_receipt_records, [])

    def test_a_terminal_claim_failure_after_the_commit_stays_uncertain(self):
        """The native write is durable, so the answer cannot be a retry."""

        fake = gateway()

        def failing_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            raise RuntimeError("terminal append failed")

        fake.purchase_fence_claim.commit = failing_claim_commit

        payload = purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertTrue(fake.purchase_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.purchase_receipt_records, [])


class RollbackFailureTests(unittest.TestCase):
    """A failed rollback must escape the endpoint, never become a normal 409.

    A normal response would let Frappe's end-of-request commit for an unsafe
    POST persist the partial native write, so the failure is raised to Frappe
    cleanup instead.
    """

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        fake = gateway()
        fake.purchase_mutation_error = patient_write.WriteUncertain()

        def failing_rollback():
            fake.rollback_calls += 1
            raise RuntimeError("native rollback failed")

        fake.rollback = failing_rollback

        with self.assertRaises(purchase_write.RollbackFailed):
            purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.purchase_fence_claim.committed, [])

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
        claim = fake.purchase_fence_claim
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
            purchase_contract,
            "encode_receipt",
            side_effect=RuntimeError("receipt encoder failed"),
        ):
            with self.assertRaises(purchase_write.RollbackFailed) as caught:
                purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertEqual(order, ["marker", "rollback"])
        self.assertEqual(len(claim.unknowns), 1)
        self.assertEqual(claim.committed, [])
        self.assertEqual(fake.write_responses, [])
        self.assertIs(type(caught.exception), purchase_write.RollbackFailed)
        self.assertNotIsInstance(
            caught.exception,
            (write_fence.FenceError, patient_write.WriteUncertain),
        )

    def test_a_named_refusal_with_a_failed_rollback_escapes_without_a_marker(self):
        """A named dispatch refusal rolls back only; no ``UNKNOWN`` is written."""

        fake = gateway()
        fake.purchase_detail_sequence = [
            receipt_detail(),
            receipt_detail(item=item(exists=False)),
        ]

        def failing_rollback():
            fake.rollback_calls += 1
            raise RuntimeError("native rollback failed")

        fake.rollback = failing_rollback

        with self.assertRaises(purchase_write.RollbackFailed) as caught:
            purchase_write.receive(fake, receipt_body(), CSRF)

        self.assertEqual(fake.purchase_fence_claim.unknowns, [])
        self.assertEqual(fake.purchase_fence_claim.committed, [])
        self.assertEqual(fake.purchase_receipt_calls, [])
        self.assertEqual(fake.write_responses, [])
        self.assertIs(type(caught.exception), purchase_write.RollbackFailed)
        self.assertNotIsInstance(
            caught.exception,
            (write_fence.FenceError, patient_write.WriteUncertain),
        )


class AckTests(unittest.TestCase):
    def ack_body(self, **overrides):
        values = {
            "operation_key": KEY,
            "kind": "purchase_receipt",
            "ack_token": "a" * 64,
        }
        values.update(overrides)
        return values

    def test_a_valid_ack_is_recorded_once(self):
        fake = gateway()

        payload = purchase_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.purchase_ack_calls), 1)

    def test_an_invalid_ack_is_a_conflict(self):
        fake = gateway()
        fake.purchase_ack_status = "invalid"

        payload = purchase_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})

    def test_a_malformed_ack_body_is_validation(self):
        fake = gateway()

        payload = purchase_write.acknowledge(
            fake, self.ack_body(ack_token="short"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.purchase_ack_calls, [])


class BootstrapTests(unittest.TestCase):
    def test_purchasing_roles_see_the_purchase_surfaces(self):
        capabilities = session.bootstrap(gateway())["capabilities"]
        self.assertEqual(
            capabilities["write.purchase_order.draft"], "enabled"
        )
        self.assertEqual(
            capabilities["write.purchase_order.submit"], "enabled"
        )
        self.assertEqual(capabilities["write.stock.receive"], "enabled")

    def test_a_manager_sees_the_same_purchase_surfaces(self):
        capabilities = session.bootstrap(gateway(role="MGR"))["capabilities"]
        self.assertEqual(
            capabilities["write.purchase_order.draft"], "enabled"
        )
        self.assertEqual(
            capabilities["write.purchase_order.submit"], "enabled"
        )

    def test_a_receptionist_sees_nothing(self):
        capabilities = session.bootstrap(gateway(role="REC"))["capabilities"]
        for name in (
            "write.purchase_order.draft",
            "write.purchase_order.submit",
            "write.stock.receive",
        ):
            self.assertEqual(capabilities[name], "read_only")

    def test_a_closed_gate_publishes_nothing(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_purchase_write_enabled=0
            )
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        for name in (
            "write.purchase_order.draft",
            "write.purchase_order.submit",
            "write.stock.receive",
        ):
            self.assertEqual(capabilities[name], "read_only")


class SecretTests(unittest.TestCase):
    def test_the_response_never_echoes_the_secret(self):
        fake = gateway()

        payload = purchase_write.save_po_draft(fake, draft_body(), CSRF)

        self.assertNotIn(SECRET, repr(payload))


if __name__ == "__main__":
    unittest.main()
