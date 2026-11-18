"""Endpoint tests for the stock receive/transfer/adjust family.

The fake gateway proves gate ordering, the strict bodies, every pre-fence
refusal (role, boundary/install drift, unknown masters, missing/expired batch,
insufficient stock with a named line, over-receipt, missing target warehouse,
missing adjustment account, budget), the fence-before-native ordering, the
closed-world side-effect check, the receipt and the one-shot ack.
"""

import unittest
from unittest import mock

from clinic_ui.core import config, patient_write, stock_contract, write_fence
from clinic_ui.endpoints import session, stock_write

from .fake_gateway import (
    Denied,
    FakeGateway,
    FakeStockReceipt,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
CSRF = "csrf-token-value"
KEY = "stock-write-key-0001"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_native_stock_write_enabled=1,
        clinic_ui_native_stock_write_site=SITE,
        clinic_ui_role_map={"PHA": ["Stock User"], "ADM": ["Accounts User"]},
    )
    values.update(overrides)
    return values


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


def detail(**overrides):
    values = {
        "today": "2026-11-16",
        "company": "SIT Synthetic Company USD",
        "stock_adjustment_account": "Stock Adjustment - SITCO",
        "item": item(),
        "warehouse": {"exists": True, "company": "SIT Synthetic Company USD"},
        "target_warehouse": {
            "exists": True,
            "company": "SIT Synthetic Company USD",
        },
        "supplier_exists": True,
        "batch": {
            "batch_id": "BATCH-1",
            "exists": True,
            "item": "ITEM-1",
            "expiry_date": "2027-01-01",
            "batch_qty": 10.0,
        },
        "available_qty": 10.0,
        "po_pending_qty": None,
    }
    values.update(overrides)
    return values


def gateway(role="PHA", **overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(
            user="pharmacist@example.test",
            roles=("Stock User",) if role == "PHA" else ("Accounts User",),
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
    fake.stock_receive_detail = detail()
    fake.stock_transfer_detail = detail()
    fake.stock_adjust_detail = detail()
    fake.stock_baseline_value = {
        "stock_ledger_rows": 5,
        "gl_rows": 10,
        "bins": {"WH-1": 10.0, "WH-2": 0.0},
    }
    return fake


def receive_body(**overrides):
    values = {
        "operation_key": KEY,
        "item_code": "ITEM-1",
        "qty": 4.0,
        "warehouse": "WH-1",
        "supplier": "SUP-1",
        "batch_id": "BATCH-1",
    }
    values.update(overrides)
    return values


def transfer_body(**overrides):
    values = {
        "operation_key": KEY,
        "item_code": "ITEM-1",
        "qty": 4.0,
        "source_warehouse": "WH-1",
        "target_warehouse": "WH-2",
        "batch_id": "BATCH-1",
    }
    values.update(overrides)
    return values


def adjust_body(**overrides):
    values = {
        "operation_key": KEY,
        "item_code": "ITEM-1",
        "qty": 3.0,
        "warehouse": "WH-1",
        "reason": "Count discrepancy",
        "batch_id": "BATCH-1",
    }
    values.update(overrides)
    return values


def committed(fake, kind="stock_receive", key=KEY):
    fake.stock_receipt_records.append(
        FakeStockReceipt(
            kind,
            fake.current_session.user,
            key,
            stock_contract.encode_receipt(
                kind=kind,
                reference="PR-EXISTING",
                item_code="ITEM-1",
                warehouse="WH-1",
                qty=1.0,
            ),
        )
    )


class ReceiveTests(unittest.TestCase):
    def test_a_receipt_is_submitted_once_and_hidden_from_the_response(self):
        fake = gateway()

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload["status"], "received")
        self.assertEqual(
            payload["movement"],
            {
                "kind": "stock_receive",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "qty": 4.0,
                "batch": "BATCH-1",
            },
        )
        self.assertRegex(payload["ackToken"], r"^[0-9a-f]{64}$")
        self.assertNotIn("PR-1", repr(payload))
        self.assertEqual(fake.stock_fence_calls[0][1], "stock_receive")
        self.assertEqual(len(fake.stock_receipt_calls), 1)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.stock_ack_calls, [])

    def test_the_baseline_is_read_before_the_fence(self):
        fake = gateway()

        stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(len(fake.stock_baseline_calls), 1)
        kind, warehouses, item_code = fake.stock_baseline_calls[0]
        self.assertEqual(kind, "stock_receive")
        self.assertEqual(tuple(warehouses), ("WH-1",))
        self.assertEqual(item_code, "ITEM-1")
        self.assertEqual(fake.stock_facts_calls[0][2], fake.stock_baseline_value)

    def test_the_mutation_payload_pins_the_posting_date_and_batch(self):
        fake = gateway()

        stock_write.receive(fake, receive_body(), CSRF)

        prepared = fake.stock_receipt_calls[0]
        self.assertEqual(prepared["item_code"], "ITEM-1")
        self.assertEqual(prepared["warehouse"], "WH-1")
        self.assertEqual(prepared["batch_no"], "BATCH-1")
        self.assertFalse(prepared["create_batch"])
        self.assertEqual(prepared["posting_date"], "2026-11-16")
        self.assertTrue(prepared["set_posting_time"])

    def test_a_new_batch_master_precedes_the_receipt(self):
        fake = gateway()
        fake.stock_receive_detail = detail(batch=None)

        stock_write.receive(
            fake,
            receive_body(batch_id="BATCH-NEW", batch_expiry="2027-06-30"),
            CSRF,
        )

        prepared = fake.stock_receipt_calls[0]
        self.assertTrue(prepared["create_batch"])
        self.assertEqual(prepared["batch_no"], "BATCH-NEW")
        self.assertEqual(prepared["batch_expiry"], "2027-06-30")

    def test_unknown_masters_are_refused_pre_fence(self):
        for label, values in (
            ("item", detail(item=item(exists=False))),
            ("supplier", detail(supplier_exists=False)),
            ("warehouse", detail(warehouse={"exists": False})),
        ):
            with self.subTest(label=label):
                fake = gateway()
                fake.stock_receive_detail = values
                payload = stock_write.receive(fake, receive_body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 417)
                self.assertEqual(fake.stock_fence_calls, [])
                self.assertEqual(fake.stock_receipt_calls, [])

    def test_a_missing_or_expired_batch_is_refused(self):
        for label, values in (
            ("expired", detail(batch={**detail()["batch"], "expiry_date": "2026-01-01"})),
            ("foreign", detail(batch={**detail()["batch"], "item": "ITEM-9"})),
        ):
            with self.subTest(label=label):
                fake = gateway()
                fake.stock_receive_detail = values
                payload = stock_write.receive(fake, receive_body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.stock_fence_calls, [])

    def test_over_receipt_against_a_reference_is_refused(self):
        fake = gateway()
        fake.stock_receive_detail = detail(po_pending_qty=2.0)

        payload = stock_write.receive(
            fake, receive_body(purchase_order="PO-1"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.stock_fence_calls, [])

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            {**receive_body(), "qty": 0},
            {**receive_body(), "docstatus": 1},
            {**receive_body(), "operation_key": "short"},
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = stock_write.receive(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 422)
                self.assertEqual(fake.stock_fence_calls, [])

    def test_the_shared_gate_refuses_before_validation(self):
        fake = gateway(role="ADM")
        with self.assertRaises(Denied):
            stock_write.receive(fake, receive_body(), CSRF)
        self.assertEqual(fake.stock_fence_calls, [])

        fake = gateway()
        with self.assertRaises(Denied):
            stock_write.receive(fake, receive_body(), None)
        self.assertEqual(fake.stock_fence_calls, [])

    def test_the_gate_flag_is_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_stock_write_enabled=0)
        )
        with self.assertRaises(Denied):
            stock_write.receive(fake, receive_body(), CSRF)
        self.assertEqual(fake.stock_fence_calls, [])

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway()
        fake.stock_boundary_problem = "versions drift for erpnext"
        with self.assertRaises(Denied):
            stock_write.receive(fake, receive_body(), CSRF)
        self.assertEqual(fake.stock_fence_calls, [])

        other = gateway()
        other.stock_install_problem = "missing field Stock Entry.set_posting_time"
        with self.assertRaises(Denied):
            stock_write.receive(other, receive_body(), CSRF)
        self.assertEqual(other.stock_fence_calls, [])

    def test_the_native_create_authority_is_required(self):
        fake = gateway()
        fake.stock_create_authority = {"Purchase Receipt": False}
        with self.assertRaises(Denied):
            stock_write.receive(fake, receive_body(), CSRF)
        self.assertEqual(fake.stock_fence_calls, [])

    def test_an_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        committed(fake, "stock_receive", "stock-receive-key-0000")

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_fence_calls, [])
        self.assertEqual(fake.stock_receipt_calls, [])


class TransferTests(unittest.TestCase):
    def test_a_material_transfer_is_submitted_once(self):
        fake = gateway()

        payload = stock_write.transfer(fake, transfer_body(), CSRF)

        self.assertEqual(payload["status"], "transferred")
        self.assertEqual(
            payload["movement"],
            {
                "kind": "stock_transfer",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "target_warehouse": "WH-2",
                "qty": 4.0,
                "batch": "BATCH-1",
            },
        )
        self.assertEqual(fake.stock_fence_calls[0][1], "stock_transfer")
        self.assertEqual(len(fake.stock_transfer_calls), 1)
        prepared = fake.stock_transfer_calls[0]
        self.assertEqual(prepared["source_warehouse"], "WH-1")
        self.assertEqual(prepared["target_warehouse"], "WH-2")
        self.assertEqual(prepared["batch_no"], "BATCH-1")
        self.assertEqual(prepared["posting_date"], "2026-11-16")
        self.assertTrue(prepared["set_posting_time"])

    def test_insufficient_stock_names_the_line_and_never_claims(self):
        fake = gateway()
        fake.stock_transfer_detail = detail(available_qty=1.0)

        payload = stock_write.transfer(fake, transfer_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.stock_fence_calls, [])
        self.assertEqual(fake.stock_transfer_calls, [])

    def test_a_missing_or_equal_target_is_refused(self):
        for label, body, values in (
            (
                "missing",
                transfer_body(),
                detail(target_warehouse={"exists": False}),
            ),
            ("same", transfer_body(target_warehouse="WH-1"), detail()),
        ):
            with self.subTest(label=label):
                fake = gateway()
                fake.stock_transfer_detail = values
                payload = stock_write.transfer(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.stock_fence_calls, [])

    def test_the_second_transfer_is_refused_by_the_budget(self):
        fake = gateway()
        committed(fake, "stock_transfer", "stock-transfer-key-0000")

        payload = stock_write.transfer(fake, transfer_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_fence_calls, [])


class AdjustTests(unittest.TestCase):
    def test_a_reconciliation_with_a_reason_is_submitted_once(self):
        fake = gateway(role="ADM")

        payload = stock_write.adjust(fake, adjust_body(), CSRF)

        self.assertEqual(payload["status"], "adjusted")
        self.assertEqual(
            payload["movement"],
            {
                "kind": "stock_adjust",
                "item_code": "ITEM-1",
                "warehouse": "WH-1",
                "qty": 3.0,
                "reason": "Count discrepancy",
            },
        )
        prepared = fake.stock_reconciliation_calls[0]
        self.assertEqual(prepared["qty"], 3.0)
        self.assertEqual(prepared["reason"], "Count discrepancy")
        self.assertEqual(
            prepared["expense_account"], "Stock Adjustment - SITCO"
        )
        self.assertEqual(fake.stock_fence_calls[0][1], "stock_adjust")

    def test_only_the_adjustment_role_may_reconcile(self):
        fake = gateway(role="PHA")
        with self.assertRaises(Denied):
            stock_write.adjust(fake, adjust_body(), CSRF)
        self.assertEqual(fake.stock_fence_calls, [])

    def test_a_missing_reason_is_validation(self):
        fake = gateway(role="ADM")
        payload = stock_write.adjust(fake, adjust_body(reason=" "), CSRF)
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.stock_fence_calls, [])

    def test_a_missing_adjustment_account_is_refused_pre_fence(self):
        fake = gateway(role="ADM")
        fake.stock_adjust_detail = detail(stock_adjustment_account=None)

        payload = stock_write.adjust(fake, adjust_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.stock_fence_calls, [])

    def test_zero_quantity_writes_the_count_off(self):
        fake = gateway(role="ADM")

        payload = stock_write.adjust(fake, adjust_body(qty=0), CSRF)

        self.assertEqual(payload["movement"]["qty"], 0.0)


class PreparedBodyTests(unittest.TestCase):
    def test_each_mutation_carries_the_server_resolved_company(self):
        fake = gateway()
        stock_write.receive(fake, receive_body(), CSRF)
        self.assertEqual(
            fake.stock_receipt_calls[0]["company"],
            "SIT Synthetic Company USD",
        )

        fake = gateway()
        stock_write.transfer(fake, transfer_body(), CSRF)
        self.assertEqual(
            fake.stock_transfer_calls[0]["company"],
            "SIT Synthetic Company USD",
        )

        fake = gateway(role="ADM")
        stock_write.adjust(fake, adjust_body(), CSRF)
        self.assertEqual(
            fake.stock_reconciliation_calls[0]["company"],
            "SIT Synthetic Company USD",
        )

    def test_a_detail_without_a_company_is_refused_pre_fence(self):
        for label, role, operation, body in (
            ("receive", "PHA", stock_write.receive, receive_body()),
            ("transfer", "PHA", stock_write.transfer, transfer_body()),
            ("adjust", "ADM", stock_write.adjust, adjust_body()),
        ):
            with self.subTest(label=label):
                fake = gateway(role=role)
                fake.stock_receive_detail = detail(company=None)
                fake.stock_transfer_detail = detail(company=None)
                fake.stock_adjust_detail = detail(company=None)
                payload = operation(fake, body, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 417)
                self.assertEqual(fake.stock_fence_calls, [])


class FenceTests(unittest.TestCase):
    def test_a_concurrent_change_rolls_back_without_committing(self):
        fake = gateway()
        fake.stock_detail_sequence = [detail(), detail(item=item(exists=False))]

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_receipt_calls, [])
        self.assertEqual(fake.stock_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_side_effect_mismatch_rolls_back(self):
        fake = gateway()
        fake.stock_facts = {"docstatus": 1, "doctype": "Purchase Receipt", "sle_rows": 0}

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.stock_mutation_error = patient_write.WriteUncertain()

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_fence_claim.unknowns, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_an_unexpected_native_error_marks_unknown_and_rolls_back(self):
        fake = gateway()
        fake.stock_mutation_error = RuntimeError("native exploded")

        payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.stock_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_the_ack_endpoint_is_the_only_acknowledgement(self):
        fake = gateway()

        stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(fake.stock_ack_calls, [])
        self.assertEqual(len(fake.stock_fence_claim.committed), 1)

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal_claim(self):
        """The receipt is validated while the native write is still uncommitted.

        A receipt the fence would reject cannot be an established outcome, so it
        is encoded before the one native commit rather than after it.
        """

        fake = gateway()
        real_encode = stock_contract.encode_receipt
        real_claim_commit = fake.stock_fence_claim.commit

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            real_claim_commit(receipt)

        fake.stock_fence_claim.commit = recording_claim_commit
        with mock.patch.object(stock_contract, "encode_receipt", recording_encode):
            payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload["status"], "received")
        order = fake.call_order
        self.assertLess(order.index("stock_fence"), order.index("encode_receipt"))
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(fake.stock_fence_claim.committed), 1)

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        fake = gateway()

        with mock.patch.object(
            stock_contract,
            "encode_receipt",
            side_effect=stock_contract.StockContractError("receipt is too long"),
        ):
            payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        # The encode refusal lands while the native transaction is still
        # uncommitted: only the rollback attempt is recorded and the key stays
        # locked as UNKNOWN with no COMMITTED terminal.
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.stock_fence_claim.committed, [])
        self.assertTrue(fake.stock_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)

    def test_the_ack_token_binds_the_one_committed_receipt(self):
        """The receipt is encoded once and the committed one is reused verbatim.

        Re-encoding after the commit would validate an already durable receipt a
        second time, so a failure there would be indistinguishable from a failed
        native write.
        """

        fake = gateway()
        encoded = []
        real_encode = stock_contract.encode_receipt

        def counting_encode(**kwargs):
            encoded.append(kwargs)
            return real_encode(**kwargs)

        with mock.patch.object(stock_contract, "encode_receipt", counting_encode):
            payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(len(encoded), 1)
        self.assertEqual(len(fake.stock_fence_claim.committed), 1)
        receipt = fake.stock_fence_claim.committed[0]
        self.assertEqual(encoded[0]["reference"], "PR-1")
        self.assertEqual(
            payload["ackToken"],
            stock_contract.ack_token(
                SECRET.encode(),
                SITE,
                fake.current_session.user,
                "stock_receive",
                KEY,
                receipt,
            ),
        )

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        """A failed rollback must fail loudly, never become a normal 409.

        Returning an envelope would let Frappe's request-end commit persist the
        partial native write; the dedicated exception propagates to cleanup.
        """

        fake = gateway()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(
            stock_contract,
            "encode_receipt",
            side_effect=stock_contract.StockContractError("receipt is too long"),
        ), mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(stock_write.RollbackFailed):
                stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.stock_fence_claim.committed, [])

    def test_a_named_handler_rollback_failure_marks_nothing_and_responds_nothing(
        self,
    ):
        """A named exception path never marks UNKNOWN before its rollback.

        Only the generic handler appends ``UNKNOWN``; the named handlers attempt
        the rollback and re-raise, so the key stays ``DISPATCHED``. The escape
        itself must not be converted by any handler: ``RollbackFailed`` derives
        from ``RuntimeError``, not from ``WriteUncertain`` or ``FenceError``.
        """

        fake = gateway()
        fake.stock_mutation_error = patient_write.WriteUncertain()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(stock_write.RollbackFailed):
                stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(fake.stock_fence_claim.unknowns, [])
        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.stock_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertFalse(
            issubclass(stock_write.RollbackFailed, patient_write.WriteUncertain)
        )
        self.assertFalse(issubclass(stock_write.RollbackFailed, write_fence.FenceError))

    def test_a_native_commit_failure_appends_no_committed_terminal(self):
        fake = gateway()

        def failing_commit():
            fake.call_order.append("commit")
            raise RuntimeError("native commit failed")

        with mock.patch.object(fake, "commit", failing_commit):
            payload = stock_write.receive(fake, receive_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.stock_fence_claim.committed, [])
        self.assertTrue(fake.stock_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)


class AckTests(unittest.TestCase):
    def ack_body(self, **overrides):
        values = {
            "operation_key": KEY,
            "kind": "stock_receive",
            "ack_token": "a" * 64,
        }
        values.update(overrides)
        return values

    def test_a_valid_ack_is_recorded_once(self):
        fake = gateway()

        payload = stock_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.stock_ack_calls), 1)

    def test_an_invalid_ack_is_a_conflict(self):
        fake = gateway()
        fake.stock_ack_status = "invalid"

        payload = stock_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})

    def test_a_malformed_ack_body_is_validation(self):
        fake = gateway()

        payload = stock_write.acknowledge(
            fake, self.ack_body(ack_token="short"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.stock_ack_calls, [])


class BootstrapTests(unittest.TestCase):
    def test_inventory_role_sees_receive_and_transfer_only(self):
        capabilities = session.bootstrap(gateway())["capabilities"]
        self.assertEqual(capabilities["write.stock.receive"], "enabled")
        self.assertEqual(capabilities["write.stock.transfer"], "enabled")
        self.assertEqual(capabilities["write.stock.adjust"], "read_only")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_admin_role_sees_only_the_adjustment_surface(self):
        capabilities = session.bootstrap(gateway(role="ADM"))["capabilities"]
        self.assertEqual(capabilities["write.stock.adjust"], "enabled")
        self.assertEqual(capabilities["write.stock.receive"], "read_only")
        self.assertEqual(capabilities["write.stock.transfer"], "read_only")

    def test_a_closed_gate_publishes_nothing(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_stock_write_enabled=0)
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        for name in (
            "write.stock.receive",
            "write.stock.transfer",
            "write.stock.adjust",
        ):
            with self.subTest(name=name):
                self.assertEqual(capabilities[name], "read_only")


if __name__ == "__main__":
    unittest.main()
