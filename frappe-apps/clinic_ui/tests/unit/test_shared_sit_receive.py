import unittest
from unittest import mock

from clinic_ui.core import finance_contract, patient_write
from clinic_ui.endpoints import finance_write
from clinic_ui.runtime import sit_payment_slot

from .fake_gateway import Denied
from .test_endpoints_finance_write import CSRF, gateway, payment_body


class SharedReceiveTests(unittest.TestCase):
    def setUp(self):
        self.gateway = gateway()
        self.gate = mock.patch.object(finance_write, "_shared_sit_payment_gate")
        self.gate_mock = self.gate.start()
        self.addCleanup(self.gate.stop)
        settings = mock.Mock(epoch_secret=b"x" * 32)
        self.gate_mock.return_value = (settings, self.gateway.current_session, "CAS")
        self.identity = mock.patch.object(
            sit_payment_slot, "shared_sit_payment_identity", return_value=sit_payment_slot.DEPLOYMENT_ID
        )
        self.identity_mock = self.identity.start()
        self.addCleanup(self.identity.stop)
        self.gateway.shared_sit_finance_payment_detail = mock.Mock(return_value={"native": True})

    def call(self, body=None):
        return finance_write._receive_shared_sit_payment(
            self.gateway, payment_body() if body is None else body, CSRF
        )

    def test_release_gate_and_validation_refuse_before_post(self):
        with mock.patch.object(finance_write, "_post_shared_sit_payment") as post:
            result = self.call({})
            self.assertEqual(result["code"], finance_write.VALIDATION_CODE)
            self.gateway.finance_settlement_verified = False
            result = self.call()
            self.assertEqual(result["code"], finance_write.LOCKED_CODE)
            post.assert_not_called()

    def test_committed_result_and_callbacks(self):
        receipt = finance_contract.encode_receipt(
            kind=finance_contract.PAYMENT_KIND, reference="PE-1", invoice="SINV-1",
            customer="CUST-1", amount=20, mode_of_payment="Cash", status="Paid",
        )
        def post(gw, payload, actor, deployment, reauthorize, *, read_detail):
            self.assertEqual(deployment, sit_payment_slot.DEPLOYMENT_ID)
            self.assertEqual(reauthorize(), actor)
            self.assertEqual(read_detail(), {"native": True})
            self.gateway.shared_sit_finance_payment_detail.assert_called_once_with(payload)
            return "PE-1", receipt, {"outstanding_after": 0}
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=post):
            result = self.call()
        self.assertEqual(result["status"], "received")
        self.assertEqual(result["payment"], "PE-1")
        self.assertEqual(result["ackToken"], finance_contract.ack_token(
            b"x" * 32, self.gateway.site, self.gateway.current_session.user,
            finance_contract.PAYMENT_KIND, payment_body()["operation_key"], receipt,
        ))
        self.assertEqual(self.gate_mock.call_count, 2)

    def test_reauthorization_rejects_changed_deployment_and_actor(self):
        def post(gw, payload, actor, deployment, reauthorize, *, read_detail):
            self.identity_mock.return_value = None
            reauthorize()
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=post):
            self.assertEqual(self.call()["code"], finance_write.LOCKED_CODE)
        def changed_actor(gw, payload, actor, deployment, reauthorize, *, read_detail):
            self.identity_mock.return_value = deployment
            self.gate_mock.return_value = (mock.Mock(), mock.Mock(user="other"), "CAS")
            self.assertEqual(reauthorize(), "other")
            raise patient_write.WriteUncertain()
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=changed_actor):
            self.assertEqual(self.call()["code"], finance_write.LOCKED_CODE)

    def test_commit_uncertain_is_not_success_and_denial_propagates(self):
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=finance_write.CommitUncertain()):
            self.assertEqual(self.call()["code"], finance_write.UNCERTAIN_CODE)
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=Denied()):
            with self.assertRaises(Denied):
                self.call()
        with mock.patch.object(finance_write, "_post_shared_sit_payment", side_effect=finance_write.RollbackFailed()):
            with self.assertRaises(finance_write.RollbackFailed):
                self.call()
