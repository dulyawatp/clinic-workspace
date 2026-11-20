import unittest
from unittest import mock

from clinic_ui.core import finance_contract, patient_write
from clinic_ui.endpoints import finance_write
from clinic_ui.runtime import sit_payment_slot

from .test_endpoints_finance_write import gateway, payment_body, payment_detail


class SharedPaymentTransactionTests(unittest.TestCase):
    def setUp(self):
        self.gateway = gateway()
        self.payload = finance_contract.validate_payment(payment_body())
        self.events = []
        self.actor = "cashier@example.test"
        self.deployment = sit_payment_slot.DEPLOYMENT_ID
        self.detail = payment_detail()
        self.read_detail = mock.Mock(side_effect=lambda: self.record("detail", self.detail))
        self.reauthorize = mock.Mock(side_effect=lambda: self.record("auth", self.actor))
        self.lock = mock.patch.object(sit_payment_slot, "lock_slot", side_effect=lambda *args: self.record("slot"))
        self.consume = mock.patch.object(sit_payment_slot, "consume_slot", side_effect=lambda *args: self.record("consume"))
        self.lock_mock = self.lock.start()
        self.consume_mock = self.consume.start()
        self.addCleanup(self.lock.stop)
        self.addCleanup(self.consume.stop)
        self.gateway.finance_payment_lease = mock.Mock(side_effect=AssertionError("legacy fence"))
        self.gateway.list_committed_finance_receipts = mock.Mock(side_effect=AssertionError("legacy budget"))
        for name, event in (
            ("finance_payment_lock_profile", "profile"),
            ("reset_native_read_snapshot", "reset"),
            ("lock_finance_invoice", "invoice"),
            ("lock_finance_payment_ledger_ranges", "ledger"),
            ("native_finance_can_write", "write"),
            ("native_finance_can_submit", "submit"),
            ("native_finance_can_read_invoice", "read"),
            ("payment_settlement_facts", "facts"),
            ("create_native_payment_entry", "post"),
            ("verify_full_payment_effects", "verify"),
            ("commit", "commit"),
            ("rollback", "rollback"),
        ):
            original = getattr(self.gateway, name)
            setattr(self.gateway, name, mock.Mock(side_effect=self.wrap(event, original)))

    def record(self, event, value=None):
        self.events.append(event)
        return value

    def wrap(self, event, original):
        def wrapped(*args, **kwargs):
            self.events.append(event)
            return original(*args, **kwargs)
        return wrapped

    def run_transaction(self):
        return finance_write._post_shared_sit_payment(
            self.gateway, self.payload, self.actor, self.deployment,
            self.reauthorize, read_detail=self.read_detail,
        )

    def test_atomic_order_and_receipt(self):
        reference, receipt, expected = self.run_transaction()
        self.assertEqual(reference, "PE-1")
        self.assertEqual(finance_contract.decode_receipt(receipt).reference, reference)
        self.assertEqual(expected["outstanding_after"], 0)
        self.assertEqual(self.events, [
            "profile", "reset", "slot", "invoice", "ledger", "auth",
            "write", "submit", "read", "detail", "facts", "post", "verify",
            "consume", "commit",
        ])
        self.consume_mock.assert_called_once_with(
            self.gateway, self.deployment, self.actor, self.payload["operation_key"], receipt
        )

    def test_denied_authority_and_spent_slot_never_post(self):
        for condition in ("actor", "permission", "spent"):
            with self.subTest(condition=condition):
                self.doCleanups()
                self.setUp()
                if condition == "actor":
                    self.reauthorize.return_value = "other"
                    self.reauthorize.side_effect = None
                    error = patient_write.WriteUncertain
                elif condition == "permission":
                    self.gateway.native_finance_can_submit.side_effect = None
                    self.gateway.native_finance_can_submit.return_value = False
                    error = finance_contract.FinanceTargetNotEligible
                else:
                    self.lock_mock.side_effect = sit_payment_slot.SlotUnavailable()
                    error = sit_payment_slot.SlotUnavailable
                with self.assertRaises(error):
                    self.run_transaction()
                self.assertNotIn("post", self.events)
                self.assertIn("rollback", self.events)

    def test_effect_or_consume_failure_rolls_back(self):
        for condition in ("verify", "consume"):
            with self.subTest(condition=condition):
                self.doCleanups()
                self.setUp()
                if condition == "verify":
                    self.gateway.verify_full_payment_effects.side_effect = lambda *args: False
                    error = patient_write.WriteUncertain
                else:
                    self.consume_mock.side_effect = sit_payment_slot.SlotUnavailable()
                    error = sit_payment_slot.SlotUnavailable
                with self.assertRaises(error):
                    self.run_transaction()
                self.assertNotIn("commit", self.events)
                self.assertIn("rollback", self.events)

    def test_commit_exception_never_returns_success(self):
        self.gateway.commit.side_effect = RuntimeError("commit response lost")
        with self.assertRaisesRegex(RuntimeError, "commit response lost"):
            self.run_transaction()
        self.assertIn("consume", self.events)
        self.assertIn("rollback", self.events)

    def test_rollback_failure_propagates(self):
        self.lock_mock.side_effect = sit_payment_slot.SlotUnavailable()
        self.gateway.rollback.side_effect = RuntimeError("rollback failed")
        with self.assertRaises(finance_write.RollbackFailed):
            self.run_transaction()
