import unittest
from unittest import mock

from clinic_ui.core import finance_contract
from clinic_ui.endpoints import finance_write
from clinic_ui.runtime import sit_payment_slot

from .fake_gateway import Denied, SECRET
from .test_endpoints_finance_write import CSRF, KEY, gateway


class SharedAckTests(unittest.TestCase):
    def setUp(self):
        self.fake = gateway()
        self.fake._settings["clinic_ui_payment_deployment_profile"] = "shared_slot_v1"
        self.identity = mock.patch.object(
            sit_payment_slot, "shared_sit_payment_identity",
            return_value=sit_payment_slot.DEPLOYMENT_ID,
        )
        self.identity.start()
        self.addCleanup(self.identity.stop)
        self.body = {"kind": "payment_receive", "operation_key": KEY, "ack_token": "a" * 64}

    def test_slot_success_and_invalid_never_use_legacy(self):
        self.fake.acknowledge_finance_write = mock.Mock(side_effect=AssertionError("legacy fence"))
        self.fake.write_deployment_approved = mock.Mock(side_effect=AssertionError("legacy marker"))
        with mock.patch.object(sit_payment_slot, "verify_slot_ack", return_value="acked") as verify:
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"acknowledged": True})
            verify.assert_called_once_with(
                self.fake, sit_payment_slot.DEPLOYMENT_ID, mock.ANY,
                self.fake.current_session.user, KEY, self.body["ack_token"],
            )
            verify.return_value = "invalid"
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"code": "OPERATION_CONFLICT"})
            verify.side_effect = sit_payment_slot.SlotUnavailable()
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"code": "WRITE_LOCKED"})

    def test_not_owned_requires_independent_legacy_authorization(self):
        with mock.patch.object(sit_payment_slot, "verify_slot_ack", return_value="not_owned"):
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"acknowledged": True})
            self.assertEqual(len(self.fake.finance_ack_calls), 1)
            self.fake.write_deployment_approved = mock.Mock(return_value=False)
            with self.assertRaises(Denied):
                finance_write.acknowledge(self.fake, self.body, CSRF)

    def test_blocked_and_invoice_use_only_existing_legacy_path(self):
        with mock.patch.object(sit_payment_slot, "verify_slot_ack") as verify:
            self.fake._settings["clinic_ui_payment_deployment_profile"] = None
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"acknowledged": True})
            self.fake._settings["clinic_ui_payment_deployment_profile"] = "shared_slot_v1"
            invoice = dict(self.body, kind="invoice_submit")
            self.assertEqual(finance_write.acknowledge(self.fake, invoice, CSRF), {"acknowledged": True})
            verify.assert_not_called()

    def test_auth_and_csrf_apply_before_any_slot_read(self):
        with mock.patch.object(sit_payment_slot, "verify_slot_ack") as verify:
            with self.assertRaises(Denied):
                finance_write.acknowledge(self.fake, self.body, "bad")
            with self.assertRaises(Denied):
                finance_write.acknowledge(self.fake, {"bad": True}, "bad")
            verify.assert_not_called()

    def test_shared_malformed_body_authorized_without_legacy_fence(self):
        self.fake.write_deployment_approved = mock.Mock(side_effect=AssertionError("legacy marker"))
        with mock.patch.object(sit_payment_slot, "verify_slot_ack") as verify:
            self.assertEqual(finance_write.acknowledge(self.fake, {"bad": True}, CSRF), {"code": "VALIDATION"})
            verify.assert_not_called()

    def test_real_receipt_binding_repeated_delivery_and_uncommitted(self):
        receipt = finance_contract.encode_receipt(
            kind=finance_contract.PAYMENT_KIND, reference="PE-1", invoice="INV-1",
            customer="CUST-1", amount=20, mode_of_payment="Cash", status="Paid",
        )
        actor = self.fake.current_session.user
        token = finance_contract.ack_token(
            SECRET.encode(), self.fake.site, actor, finance_contract.PAYMENT_KIND, KEY, receipt,
        )
        self.body["ack_token"] = token
        row = {"state": "spent", "actor": actor, "operation_key": KEY, "receipt": receipt}
        with mock.patch.object(sit_payment_slot, "read_slot", return_value=row):
            for _ in range(2):
                self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"acknowledged": True})
            self.assertEqual(
                finance_write.acknowledge(self.fake, dict(self.body, ack_token="0" * 64), CSRF),
                {"code": "OPERATION_CONFLICT"},
            )
            row["actor"] = "other@example.test"
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"code": "OPERATION_CONFLICT"})
        self.fake.finance_ack_status = "invalid"
        row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        with mock.patch.object(sit_payment_slot, "read_slot", return_value=row):
            self.assertEqual(finance_write.acknowledge(self.fake, self.body, CSRF), {"code": "OPERATION_CONFLICT"})
