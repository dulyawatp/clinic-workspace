import unittest
from unittest import mock

from clinic_ui.endpoints import finance_write
from clinic_ui.runtime import frappe_gateway, sit_payment_slot

from .fake_gateway import Denied
from .test_endpoints_finance_write import CSRF, gateway, write_settings


class PaymentLaneTests(unittest.TestCase):
    def test_selector_fails_closed_on_shared_site(self):
        fake = gateway()
        with mock.patch.object(sit_payment_slot, "shared_sit_payment_identity") as proof:
            for profile in (None, "", "shared_slot_v2", True):
                fake.setting = mock.Mock(return_value=profile)
                self.assertEqual(sit_payment_slot.payment_lane(fake), "blocked")
            fake.setting = mock.Mock(side_effect=OSError())
            self.assertEqual(sit_payment_slot.payment_lane(fake), "blocked")
            fake.setting = mock.Mock(return_value="shared_slot_v1")
            proof.return_value = None
            self.assertEqual(sit_payment_slot.payment_lane(fake), "blocked")
            proof.side_effect = OSError()
            self.assertEqual(sit_payment_slot.payment_lane(fake), "blocked")
            proof.side_effect = None
            proof.return_value = sit_payment_slot.DEPLOYMENT_ID
            self.assertEqual(sit_payment_slot.payment_lane(fake), "shared_slot_v1")
            fake.setting.return_value = "legacy"
            self.assertEqual(sit_payment_slot.payment_lane(fake), "legacy")

    def test_other_or_hostile_hostname_never_selects_slot(self):
        fake = gateway(site="marley-sit.local.attacker")
        fake.setting = mock.Mock(side_effect=AssertionError("unexpected config read"))
        self.assertEqual(sit_payment_slot.payment_lane(fake), "legacy")

    def test_shared_authorization_rechecks_selector_and_existing_guards(self):
        fake = gateway()
        fake.write_deployment_approved = mock.Mock(side_effect=AssertionError("legacy marker"))
        with mock.patch.object(sit_payment_slot, "payment_lane", return_value="shared_slot_v1") as lane:
            result = finance_write._shared_sit_payment_gate(fake, CSRF)
            self.assertEqual(result[1].user, fake.current_session.user)
            self.assertEqual(fake.config_refresh_calls, 1)
            lane.assert_called_once_with(fake)
            with self.assertRaises(Denied):
                finance_write._shared_sit_payment_gate(fake, "invalid")
            for name in ("native_finance_boundary_problem", "native_finance_payment_install_problem"):
                with self.subTest(name=name), mock.patch.object(fake, name, return_value="invalid"):
                    with self.assertRaises(Denied):
                        finance_write._shared_sit_payment_gate(fake, CSRF)
            lane.return_value = "blocked"
            with self.assertRaises(Denied):
                finance_write._shared_sit_payment_gate(fake, CSRF)

    def test_shared_authorization_denies_wrong_role(self):
        fake = gateway(role="PHA")
        with mock.patch.object(sit_payment_slot, "payment_lane", return_value="shared_slot_v1"):
            with self.assertRaises(Denied):
                finance_write._shared_sit_payment_gate(fake, CSRF)

    def test_shared_authorization_denies_disabled_finance(self):
        fake = gateway(settings=write_settings(clinic_ui_native_finance_write_enabled=0))
        with mock.patch.object(sit_payment_slot, "payment_lane", return_value="shared_slot_v1"):
            with self.assertRaises(Denied):
                finance_write._shared_sit_payment_gate(fake, CSRF)

    def test_shared_authorization_uses_profile_and_proof(self):
        fake = gateway()
        original_setting = fake.setting
        fake.setting = lambda key: (
            "shared_slot_v1" if key == sit_payment_slot.PROFILE_SETTING
            else original_setting(key)
        )
        with mock.patch.object(sit_payment_slot, "shared_sit_payment_identity") as proof:
            proof.return_value = sit_payment_slot.DEPLOYMENT_ID
            finance_write._shared_sit_payment_gate(fake, CSRF)
            proof.return_value = None
            with self.assertRaises(Denied):
                finance_write._shared_sit_payment_gate(fake, CSRF)

    def test_slot_detail_never_reads_legacy_payment_history(self):
        gw = frappe_gateway.Gateway()
        gw._purchase_company_currency = mock.Mock(return_value=("Company", "USD"))
        gw._finance_company_defaults = mock.Mock(return_value={"default_currency": "USD"})
        gw._finance_invoice_state = mock.Mock(return_value={"debit_to": "Debtors"})
        gw._finance_mode_of_payment = mock.Mock(return_value={"accepted": True})
        gw.site_today = mock.Mock(return_value="2026-11-21")
        gw._finance_payment_recorded = mock.Mock(return_value=True)
        payload = {"invoice": "SINV-1", "mode_of_payment": "Cash"}
        self.assertFalse(gw.shared_sit_finance_payment_detail(payload)["payment_exists"])
        gw._finance_payment_recorded.assert_not_called()
        self.assertTrue(gw.native_finance_payment_detail(payload)["payment_exists"])
        gw._finance_payment_recorded.assert_called_once_with("SINV-1")
