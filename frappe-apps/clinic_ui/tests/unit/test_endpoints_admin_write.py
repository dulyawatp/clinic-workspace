"""Endpoint tests for the admin users + masters write family.

The fake gateway proves gate ordering, the strict bodies, every pre-fence
refusal (role, allowlist violation, duplicate identifier, missing email,
missing installed role, budget), the fence-before-native ordering, the
closed-world re-read, the exactly-once receipt and the one-shot ack. Credentials
never enter a receipt or a response.
"""

import unittest

from clinic_ui.core import admin_contract, config, patient_write
from clinic_ui.endpoints import admin_write, session

from .fake_gateway import (
    Denied,
    FakeAdminReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "sit.adm@example.invalid"
CSRF = "csrf-token-value"
USER_KEY = "admin-user-key-0001-01"
MASTER_KEY = "admin-master-key-0001-01"

ROLE_MAP = {
    "DOC": ["Physician"],
    "NUR": ["Nursing User"],
    "REC": ["Clinic Reception"],
    "PHA": ["Stock Manager"],
    "CAS": ["Accounts User"],
    "MGR": ["Clinic Lab Manager"],
    "ADM": ["System Manager"],
}


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_role_map=ROLE_MAP,
        clinic_ui_native_admin_write_enabled=1,
        clinic_ui_native_admin_write_site=SITE,
    )
    values.update(overrides)
    return values


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(
            user=ACTOR, roles=("System Manager",)
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
    fake.created_admin_user = "sit.pha@example.invalid"
    return fake


def user_body(**overrides):
    values = {
        "operation_key": USER_KEY,
        "email": "sit.pha@example.invalid",
        "full_name": "SIT Pharmacist",
        "clinic_role_code": "PHA",
        "password": "caller-supplied-secret-value",
    }
    values.update(overrides)
    return values


def master_body(master_type="warehouse", payload=None, **overrides):
    values = {
        "operation_key": MASTER_KEY,
        "master_type": master_type,
        "payload": payload or {"warehouse_name": "SIT Pharmacy"},
    }
    values.update(overrides)
    return values


def committed_user(fake, key=USER_KEY):
    fake.admin_receipt_records.append(
        FakeAdminReceipt(
            "admin_user",
            ACTOR,
            key,
            admin_contract.encode_receipt(
                kind="admin_user", identifier="existing@example.invalid"
            ),
        )
    )


def committed_master(fake, key=MASTER_KEY):
    fake.admin_receipt_records.append(
        FakeAdminReceipt(
            "admin_master",
            ACTOR,
            key,
            admin_contract.encode_receipt(
                kind="admin_master",
                master_type="warehouse",
                identifier="SIT Warehouse",
            ),
        )
    )


class CreateUserTests(unittest.TestCase):
    def test_creates_one_user_and_leaves_the_ack_to_its_endpoint(self):
        fake = gateway()

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(
            payload["user"]["user_id"], "sit.pha@example.invalid"
        )
        self.assertEqual(payload["user"]["roles"], ["Stock Manager"])
        self.assertTrue(admin_contract.is_valid_ack_token(payload["ackToken"]))
        self.assertNotIn("caller-supplied-secret", repr(payload))
        self.assertEqual(fake.admin_fence_calls[0][1], "admin_user")
        self.assertEqual(len(fake.admin_user_create_calls), 1)
        created = fake.admin_user_create_calls[0]
        self.assertEqual(created["roles"], ["Stock Manager"])
        self.assertEqual(created["password"], "caller-supplied-secret-value")
        self.assertEqual(fake.commit_calls, 1)
        receipt = fake.admin_fence_claim.committed[0]
        self.assertNotIn("caller-supplied-secret", receipt)
        decoded = admin_contract.decode_receipt(receipt)
        self.assertEqual(decoded.identifier, "sit.pha@example.invalid")
        self.assertEqual(fake.admin_ack_calls, [])

    def test_a_practitioner_link_is_part_of_the_same_mutation(self):
        fake = gateway()
        fake.admin_practitioners.add("HLC-PRACT-1")

        admin_write.create_user(
            fake, user_body(practitioner="HLC-PRACT-1"), CSRF
        )

        self.assertEqual(len(fake.admin_user_create_calls), 1)
        self.assertEqual(
            fake.admin_user_create_calls[0]["practitioner"], "HLC-PRACT-1"
        )

    def test_a_forbidden_role_is_refused_before_the_fence(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_role_map={
                    **ROLE_MAP,
                    "PHA": ["System Manager"],
                    "ADM": ["Clinic Lab Administrator"],
                }
            ),
            current_session=session_view(
                user=ACTOR, roles=("Clinic Lab Administrator",)
            ),
        )

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_healthcare_administrator_is_refused_before_the_fence(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_role_map={
                    **ROLE_MAP,
                    "PHA": ["Healthcare Administrator"],
                }
            )
        )

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_an_unmapped_code_is_refused_before_the_fence(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_role_map={
                    code: roles for code, roles in ROLE_MAP.items() if code != "CAS"
                }
            )
        )

        payload = admin_write.create_user(
            fake, user_body(clinic_role_code="CAS"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_duplicate_user_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_users.add("sit.pha@example.invalid")

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_missing_practitioner_is_refused_before_the_fence(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(practitioner="HLC-MISSING"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_missing_installed_role_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_missing_roles = ["Stock Manager"]

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_missing_email_and_username_is_a_validation_refusal(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(email=None), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_username_only_create_is_accepted(self):
        fake = gateway()
        fake.created_admin_user = "sit.pha"

        payload = admin_write.create_user(
            fake, user_body(email=None, username="sit.pha"), CSRF
        )

        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["user"]["user_id"], "sit.pha")

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            {**user_body(), "docstatus": 1},
            user_body(password=None),
            user_body(password="short"),
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = admin_write.create_user(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.admin_fence_calls, [])

    def test_the_shared_gate_refuses_before_validation(self):
        fake = gateway(
            current_session=session_view(
                user="sit.doc@example.invalid", roles=("Physician",)
            )
        )
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_fence_calls, [])

        fake = gateway()
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), None)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_the_gate_flag_and_install_preflight_are_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_admin_write_enabled=0)
        )
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_fence_calls, [])

        other = gateway()
        other.admin_install_problem = "missing DocType User"
        with self.assertRaises(Denied):
            admin_write.create_user(other, user_body(), CSRF)
        self.assertEqual(other.admin_fence_calls, [])

    def test_native_authority_is_required(self):
        fake = gateway()
        fake.admin_can_write = False

        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_an_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        for index in range(4):
            committed_user(fake, f"admin-user-key-{index:04d}-01")

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_user_create_calls, [])


class CreateUserFenceTests(unittest.TestCase):
    def test_a_duplicate_inside_the_fence_rolls_back(self):
        fake = gateway()
        fake.admin_user_exists_sequence = [False, True]

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.admin_user_create_calls, [])
        self.assertEqual(fake.admin_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_facts_mismatch_rolls_back(self):
        fake = gateway()
        fake.admin_user_facts = {
            "user": "sit.pha@example.invalid",
            "enabled": 1,
            "roles": ["Stock User"],
            "practitioner": None,
        }

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.admin_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.admin_user_create_error = patient_write.WriteUncertain()

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_an_unexpected_native_error_marks_unknown_and_rolls_back(self):
        fake = gateway()
        fake.admin_user_create_error = RuntimeError("native exploded")

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.admin_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)


class EditMasterTests(unittest.TestCase):
    def test_creates_one_warehouse_and_receipts_the_variant(self):
        fake = gateway()

        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["master"]["master_type"], "warehouse")
        self.assertEqual(payload["master"]["master_id"], "SIT Pharmacy")
        self.assertTrue(admin_contract.is_valid_ack_token(payload["ackToken"]))
        self.assertEqual(fake.admin_fence_calls[0][1], "admin_master")
        self.assertEqual(len(fake.admin_master_create_calls), 1)
        receipt = admin_contract.decode_receipt(
            fake.admin_fence_claim.committed[0]
        )
        self.assertEqual(receipt.master_type, "warehouse")
        self.assertEqual(receipt.identifier, "SIT Pharmacy")
        self.assertEqual(fake.admin_ack_calls, [])

    def test_an_item_payload_carries_its_price(self):
        fake = gateway()
        payload = {
            "item_code": "SIT-ITEM-1",
            "item_name": "SIT Item One",
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "price_list": "Standard Selling",
            "rate": 12.5,
        }

        result = admin_write.edit_master(
            fake, master_body("item", payload), CSRF
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(fake.admin_master_create_calls[0][0], "item")
        self.assertEqual(
            fake.admin_master_create_calls[0][1]["rate"], 12.5
        )

    def test_every_variant_is_accepted(self):
        cases = (
            ("supplier", {"supplier_name": "SIT Supplier"}),
            ("price_list", {"price_list_name": "Standard Selling"}),
            (
                "mode_of_payment",
                {"mode_of_payment": "Cash", "account": "Cash - SITCO"},
            ),
            (
                "procedure_template",
                {
                    "template_name": "SIT Dressing",
                    "item_code": "SIT-ITEM-1",
                    "rate": 25.0,
                },
            ),
        )
        for master_type, payload in cases:
            with self.subTest(master_type=master_type):
                fake = gateway()
                result = admin_write.edit_master(
                    fake, master_body(master_type, payload), CSRF
                )
                self.assertEqual(result["status"], "created")
                self.assertEqual(
                    fake.admin_master_create_calls[0][0], master_type
                )

    def test_a_duplicate_master_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_masters.add(("warehouse", "SIT Pharmacy"))

        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_duplicate_item_price_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_item_prices.add(("SIT-ITEM-1", "Standard Selling"))
        payload = {
            "item_code": "SIT-ITEM-1",
            "item_name": "SIT Item One",
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "price_list": "Standard Selling",
            "rate": 12.5,
        }

        result = admin_write.edit_master(
            fake, master_body("item", payload), CSRF
        )

        self.assertEqual(result, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_an_unknown_type_or_bad_payload_is_validation(self):
        cases = (
            master_body("custom_doctype", {"warehouse_name": "WH"}),
            master_body("warehouse", {"warehouse_name": "WH", "company": "X"}),
            master_body("warehouse", {"warehouse_name": ""}),
            {**master_body(), "payload": "nope"},
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = admin_write.edit_master(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.admin_fence_calls, [])

    def test_an_exhausted_master_budget_is_refused_before_the_fence(self):
        fake = gateway()
        for index in range(12):
            committed_master(fake, f"admin-master-key-{index:04d}-01")

        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_native_create_authority_is_required_for_the_variant(self):
        fake = gateway()
        fake.admin_can_create = {"Warehouse": False}

        with self.assertRaises(Denied):
            admin_write.edit_master(fake, master_body(), CSRF)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_facts_mismatch_rolls_back(self):
        fake = gateway()
        fake.admin_master_facts = {
            "name": "SIT Pharmacy",
            "company": None,
        }

        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.admin_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)


class AckTests(unittest.TestCase):
    def ack_body(self, **overrides):
        values = {
            "operation_key": USER_KEY,
            "kind": "admin_user",
            "ack_token": "a" * 64,
        }
        values.update(overrides)
        return values

    def test_a_valid_ack_is_recorded_once(self):
        fake = gateway()

        payload = admin_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.admin_ack_calls), 1)

    def test_an_invalid_ack_is_a_conflict(self):
        fake = gateway()
        fake.admin_ack_status = "invalid"

        payload = admin_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})

    def test_a_malformed_ack_body_is_validation(self):
        fake = gateway()

        payload = admin_write.acknowledge(
            fake, self.ack_body(ack_token="short"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_ack_calls, [])


class BootstrapTests(unittest.TestCase):
    def test_the_pair_is_published_only_when_the_gate_is_open(self):
        fake = gateway()
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.admin.user"], "enabled")
        self.assertEqual(capabilities["write.admin.master"], "enabled")

    def test_the_flags_stay_read_only_when_the_gate_is_closed(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_admin_write_enabled=0)
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.admin.user"], "read_only")
        self.assertEqual(capabilities["write.admin.master"], "read_only")

    def test_a_non_admin_never_receives_the_admin_capabilities(self):
        fake = gateway(
            current_session=session_view(
                user="sit.doc@example.invalid", roles=("Physician",)
            )
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.admin.user"], "read_only")
        self.assertEqual(capabilities["write.admin.master"], "read_only")


if __name__ == "__main__":
    unittest.main()
