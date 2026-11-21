import unittest
from unittest import mock

from clinic_ui.core import write_fence
from clinic_ui.endpoints import session
from clinic_ui.runtime import sit_payment_slot

from .fake_gateway import session_view
from .test_endpoints_finance_write import ROLES, gateway


class SharedBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.fake = gateway()
        self.fake._settings["clinic_ui_payment_deployment_profile"] = "shared_slot_v1"
        self.fake.finance_settlement_verified = True
        for name in ("write_deployment_approved", "write_fence_ready", "list_committed_finance_receipts"):
            setattr(self.fake, name, mock.Mock(side_effect=AssertionError("legacy access")))
        proof = mock.patch.object(
            sit_payment_slot, "shared_sit_payment_identity", return_value=sit_payment_slot.DEPLOYMENT_ID
        )
        self.proof = proof.start()
        self.addCleanup(proof.stop)
        read = mock.patch.object(sit_payment_slot, "read_slot", return_value={"state": "available"})
        self.read = read.start()
        self.addCleanup(read.stop)

    def result(self):
        return session.bootstrap(self.fake)

    def assert_closed(self, invoice="enabled"):
        result = self.result()
        self.assertEqual(result["capabilities"]["write.payment.receive"], "read_only")
        self.assertNotIn("paymentSettlement", result)
        self.assertEqual(result["capabilities"]["write.invoice.submit"], invoice)

    def test_available_slot_advertises_without_fence(self):
        result = self.result()
        self.assertEqual(result["capabilities"]["write.payment.receive"], "enabled")
        self.assertEqual(result["paymentSettlement"], "full_balance_v1")
        self.read.assert_called_once_with(self.fake, sit_payment_slot.DEPLOYMENT_ID)
        self.fake.write_deployment_approved.assert_not_called()
        self.fake.write_fence_ready.assert_not_called()
        self.fake.list_committed_finance_receipts.assert_not_called()

    def test_spent_missing_unreadable_slot_and_release_closed(self):
        self.read.return_value = {"state": "spent"}
        self.assert_closed()
        self.read.side_effect = sit_payment_slot.SlotUnavailable()
        self.assert_closed()
        self.read.side_effect = RuntimeError("database unavailable")
        self.assert_closed()
        self.read.side_effect = None
        self.fake.finance_settlement_verified = False
        self.read.reset_mock()
        self.assert_closed()
        self.read.assert_not_called()

    def test_bad_profile_or_proof_never_falls_back(self):
        self.proof.return_value = None
        self.assert_closed()
        self.fake._settings["clinic_ui_payment_deployment_profile"] = None
        self.assert_closed()
        self.read.assert_not_called()

    def test_wrong_role_reserved_actor_and_install_drift_are_closed(self):
        self.fake.current_session = session_view(user="user@example.test", roles=ROLES["MGR"])
        self.assert_closed(invoice="read_only")
        self.fake.current_session = session_view(user=write_fence.LATCH_ACTOR, roles=ROLES["CAS"])
        self.assert_closed()
        self.fake.current_session = session_view(user="user@example.test", roles=ROLES["CAS"])
        with mock.patch.object(self.fake, "native_finance_payment_install_problem", return_value="invalid"):
            self.assert_closed()
        with mock.patch.object(sit_payment_slot, "payment_lane", side_effect=RuntimeError("unreadable")):
            self.assert_closed()
        self.read.assert_not_called()

    def test_required_native_authority_is_not_bypassed(self):
        for method in ("native_finance_can_write", "native_finance_can_submit"):
            with self.subTest(method=method), mock.patch.object(
                self.fake, method, side_effect=lambda doctype: doctype != "Payment Entry"
            ):
                self.assert_closed()
        self.fake.doctype_reads = {"Sales Invoice": False}
        result = self.result()
        self.assertEqual(result["capabilities"]["write.payment.receive"], "read_only")

    def test_legacy_profile_keeps_existing_path(self):
        fake = gateway()
        result = session.bootstrap(fake)
        self.assertEqual(result["capabilities"]["write.payment.receive"], "enabled")
        self.read.assert_not_called()
