"""Endpoint tests for the admin users + masters write family.

The fake gateway proves gate ordering, the strict bodies, every pre-fence
refusal (role, allowlist violation, duplicate identifier, missing email,
missing installed role, budget), the fence-before-native ordering, the
closed-world re-read, the exactly-once receipt and the one-shot ack. Credentials
never enter a receipt or a response.
"""

import unittest
from unittest import mock

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
    """The converted user lane: native authority, no fence/budget/ack."""

    def test_creates_one_native_user_without_a_fence_receipt_or_ack(self):
        fake = gateway()

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(
            payload["user"]["user_id"], "sit.pha@example.invalid"
        )
        self.assertEqual(payload["user"]["roles"], ["Stock Manager"])
        self.assertNotIn("ackToken", payload)
        self.assertNotIn("caller-supplied-secret", repr(payload))
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_fence_claim.committed, [])
        self.assertEqual(fake.admin_fence_claim.unknowns, [])
        self.assertEqual(fake.admin_ack_calls, [])
        self.assertEqual(fake.admin_receipt_calls, [])
        # No practitioner means no User Permission preflight.
        self.assertEqual(fake.admin_user_permission_calls, [])
        self.assertEqual(len(fake.admin_user_create_calls), 1)
        created = fake.admin_user_create_calls[0]
        self.assertEqual(created["roles"], ["Stock Manager"])
        self.assertEqual(created["password"], "caller-supplied-secret-value")
        self.assertNotIn("operation_key", created)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.rollback_calls, 0)
        # The closed-world re-read before the commit is retained.
        self.assertEqual(
            fake.admin_user_facts_calls, ["sit.pha@example.invalid"]
        )

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
        self.assertEqual(len(fake.admin_user_permission_calls), 1)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_unresolved_historical_receipts_neither_block_nor_change(self):
        fake = gateway()
        for index in range(6):
            committed_user(fake, f"admin-user-key-{index:04d}-01")
        before = list(fake.admin_receipt_records)

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(fake.admin_fence_calls, [])
        # The historical ledger is never read or written by this lane.
        self.assertEqual(fake.admin_receipt_calls, [])
        self.assertEqual(fake.admin_receipt_records, before)
        self.assertEqual(fake.admin_fence_claim.committed, [])

    def test_the_deployment_marker_and_fence_store_are_not_required(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(len(fake.admin_user_create_calls), 1)

    def test_the_request_operation_key_is_optional_and_not_a_fence(self):
        body = user_body()
        body.pop("operation_key")

        fake = gateway()
        payload = admin_write.create_user(fake, body, CSRF)
        self.assertEqual(payload["status"], "created")

        # The same key for two different users is not de-duplicated by this
        # lane: native duplicate checks decide, never a fabricated receipt.
        fake = gateway()
        first = admin_write.create_user(fake, user_body(), CSRF)
        second = admin_write.create_user(
            fake, user_body(email="sit.nur@example.invalid"), CSRF
        )
        self.assertEqual(first["status"], "created")
        self.assertEqual(second["status"], "created")
        self.assertEqual(len(fake.admin_user_create_calls), 2)
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_forbidden_role_is_refused_before_any_mutation(self):
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
        self.assertEqual(fake.admin_user_create_calls, [])
        self.assertEqual(fake.admin_fence_calls, [])

    def test_healthcare_administrator_is_refused_before_any_mutation(self):
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
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_an_unmapped_code_is_refused_before_any_mutation(self):
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
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_duplicate_user_is_refused_before_any_mutation(self):
        fake = gateway()
        fake.admin_users.add("sit.pha@example.invalid")

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_missing_practitioner_is_refused_before_any_mutation(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(practitioner="HLC-MISSING"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_missing_installed_role_is_refused_before_any_mutation(self):
        fake = gateway()
        fake.admin_missing_roles = ["Stock Manager"]

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_missing_email_is_a_validation_refusal(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(email=None), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_username_only_create_is_refused_before_any_mutation(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(email=None, username="sit.pha"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_distinct_username_is_passed_to_the_native_controller(self):
        fake = gateway()

        payload = admin_write.create_user(
            fake, user_body(username="sit.pha"), CSRF
        )

        self.assertEqual(payload["status"], "created")
        created = fake.admin_user_create_calls[0]
        self.assertEqual(created["email"], "sit.pha@example.invalid")
        self.assertEqual(created["username"], "sit.pha")

    def test_duplicate_identifiers_are_compared_after_normalisation(self):
        fake = gateway()
        fake.admin_users.add("sit.pha@example.invalid")

        payload = admin_write.create_user(
            fake, user_body(email="SIT.PHA@example.invalid"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_a_duplicate_username_uses_the_installed_username_field(self):
        fake = gateway()
        fake.admin_usernames.add("sit.pha")

        payload = admin_write.create_user(
            fake, user_body(username="sit.pha"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_an_already_linked_practitioner_is_refused_without_disclosure(self):
        fake = gateway()
        fake.admin_practitioners.add("HLC-PRACT-1")
        fake.admin_practitioner_linked.add("HLC-PRACT-1")

        payload = admin_write.create_user(
            fake, user_body(practitioner="HLC-PRACT-1"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.admin_user_create_calls, [])
        # The refusal names no user and no link target.
        self.assertNotIn("sit.", repr(payload))

    def test_practitioner_write_authority_is_required_before_the_mutation(self):
        fake = gateway()
        fake.admin_practitioners.add("HLC-PRACT-1")
        fake.admin_practitioner_can_write["HLC-PRACT-1"] = False

        with self.assertRaises(Denied):
            admin_write.create_user(
                fake, user_body(practitioner="HLC-PRACT-1"), CSRF
            )
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_user_permission_create_authority_is_required_for_a_link(self):
        fake = gateway()
        fake.admin_practitioners.add("HLC-PRACT-1")
        fake.admin_can_create_user_permission = False

        with self.assertRaises(Denied):
            admin_write.create_user(
                fake, user_body(practitioner="HLC-PRACT-1"), CSRF
            )
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_an_installed_email_policy_refusal_is_deterministic(self):
        fake = gateway()
        fake.admin_email_problem = "email is invalid"

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 422)
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            {**user_body(), "docstatus": 1},
            user_body(password=None),
            user_body(password="short"),
            user_body(operation_key="short"),
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = admin_write.create_user(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.admin_user_create_calls, [])

    def test_the_gate_refuses_before_validation(self):
        fake = gateway(
            current_session=session_view(
                user="sit.doc@example.invalid", roles=("Physician",)
            )
        )
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_user_create_calls, [])

        fake = gateway()
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), None)
        self.assertEqual(fake.admin_user_create_calls, [])

    def test_the_gate_flag_and_install_preflight_are_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_admin_write_enabled=0)
        )
        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_user_create_calls, [])

        other = gateway()
        other.admin_install_problem = "missing DocType User"
        with self.assertRaises(Denied):
            admin_write.create_user(other, user_body(), CSRF)
        self.assertEqual(other.admin_user_create_calls, [])

    def test_native_authority_is_required(self):
        fake = gateway()
        fake.admin_can_write = False

        with self.assertRaises(Denied):
            admin_write.create_user(fake, user_body(), CSRF)
        self.assertEqual(fake.admin_user_create_calls, [])


class CreateUserUncertainTests(unittest.TestCase):
    """A lost response is uncertain: roll back, report, never retry."""

    def test_a_native_failure_is_uncertain_and_rolled_back(self):
        fake = gateway()
        fake.admin_user_create_error = patient_write.WriteUncertain()

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[-1][0], 409)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_fence_claim.unknowns, [])

    def test_an_unexpected_native_error_is_uncertain_and_rolled_back(self):
        fake = gateway()
        fake.admin_user_create_error = RuntimeError("native exploded")

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_claim.unknowns, [])

    def test_a_facts_mismatch_is_uncertain_and_never_commits(self):
        fake = gateway()
        fake.admin_user_facts = {
            "user": "sit.pha@example.invalid",
            "enabled": 1,
            "roles": ["Stock User"],
            "practitioner": None,
        }

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_claim.committed, [])

    def test_a_commit_failure_is_uncertain_and_rolled_back(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_claim.committed, [])

    def test_a_failed_rollback_escapes_to_frappe_cleanup(self):
        fake = gateway()
        fake.admin_user_create_error = RuntimeError("native failed")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(admin_write.RollbackFailed):
            admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(fake.write_responses, [])

    def test_a_success_response_that_cannot_be_built_is_uncertain(self):
        fake = gateway()

        def no_channels():
            raise RuntimeError("channels unavailable")

        fake.strip_message_channels = no_channels

        payload = admin_write.create_user(fake, user_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.admin_fence_claim.committed, [])


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
        fake.admin_masters.add(("uom", "Nos"))
        payload = {
            "item_code": "SIT-ITEM-1",
            "item_name": "SIT Item One",
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "price_list": "Standard Selling",
            "price_list_rate": 12.5,
        }

        result = admin_write.edit_master(
            fake, master_body("item", payload), CSRF
        )

        self.assertEqual(result["status"], "created")
        self.assertEqual(fake.admin_master_create_calls[0][0], "item")
        self.assertEqual(
            fake.admin_master_create_calls[0][1]["price_list_rate"], 12.5
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
            ("supplier_group", {"supplier_group_name": "SIT Suppliers"}),
            ("uom", {"uom_name": "Nos"}),
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

    def test_a_new_kind_receipts_its_own_variant(self):
        fake = gateway()

        for master_type, payload, identifier in (
            ("uom", {"uom_name": "Nos"}, "Nos"),
            (
                "supplier_group",
                {"supplier_group_name": "SIT Suppliers"},
                "SIT Suppliers",
            ),
        ):
            with self.subTest(master_type=master_type):
                fake = gateway()
                result = admin_write.edit_master(
                    fake, master_body(master_type, payload), CSRF
                )
                self.assertEqual(result["status"], "created")
                self.assertEqual(result["master"]["master_type"], master_type)
                self.assertEqual(result["master"]["master_id"], identifier)
                receipt = admin_contract.decode_receipt(
                    fake.admin_fence_claim.committed[0]
                )
                self.assertEqual(receipt.kind, "admin_master")
                self.assertEqual(receipt.master_type, master_type)
                self.assertEqual(receipt.identifier, identifier)

    def test_a_missing_company_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_prerequisites = dict(
            fake.admin_prerequisites, company=None
        )

        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_every_master_op_is_refused_when_no_company_resolves(self):
        cases = (
            ("warehouse", {"warehouse_name": "SIT Pharmacy"}),
            ("uom", {"uom_name": "Nos"}),
            ("supplier_group", {"supplier_group_name": "SIT Suppliers"}),
        )
        for master_type, body in cases:
            with self.subTest(master_type=master_type):
                fake = gateway()
                fake.admin_prerequisites = dict(
                    fake.admin_prerequisites, company=None
                )
                payload = admin_write.edit_master(
                    fake, master_body(master_type, body), CSRF
                )
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.admin_fence_calls, [])

    def test_an_item_op_is_refused_when_no_uom_row_exists(self):
        fake = gateway()
        fake.admin_prerequisites = dict(
            fake.admin_prerequisites, uom_rows=0
        )
        payload = {
            "item_code": "SIT-ITEM-1",
            "item_name": "SIT Item One",
            "item_group": "All Item Groups",
            "stock_uom": "Nos",
            "price_list": "Standard Selling",
            "price_list_rate": 12.5,
        }

        result = admin_write.edit_master(
            fake, master_body("item", payload), CSRF
        )

        self.assertEqual(result, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_an_item_op_is_refused_when_its_unit_is_not_installed(self):
        fake = gateway()

        payload = {
            "item_code": "SIT-ITEM-1",
            "item_name": "SIT Item One",
            "item_group": "All Item Groups",
            "stock_uom": "Bottle",
            "price_list": "Standard Selling",
            "price_list_rate": 12.5,
        }

        result = admin_write.edit_master(
            fake, master_body("item", payload), CSRF
        )

        self.assertEqual(result, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_a_supplier_op_is_refused_when_no_supplier_group_exists(self):
        fake = gateway()
        fake.admin_prerequisites = dict(
            fake.admin_prerequisites,
            supplier_group=None,
            supplier_group_rows=0,
        )

        payload = admin_write.edit_master(
            fake, master_body("supplier", {"supplier_name": "SIT Supplier"}), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_the_prerequisites_are_read_before_the_duplicate_checks(self):
        fake = gateway()
        fake.admin_masters.add(("warehouse", "SIT Pharmacy"))

        admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(
            fake.admin_prerequisites_calls[0],
            fake.admin_prerequisites,
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
            "price_list_rate": 12.5,
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
        for index in range(15):
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

    def test_the_master_receipt_is_encoded_before_the_commit_and_the_claim(self):
        fake = gateway()
        real_encode = admin_contract.encode_receipt
        real_claim_commit = fake.admin_fence_claim.commit

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            return real_claim_commit(receipt)

        with mock.patch.object(
            admin_contract, "encode_receipt", recording_encode
        ), mock.patch.object(
            fake.admin_fence_claim, "commit", recording_claim_commit
        ):
            payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(fake.admin_fence_claim.committed), 1)

    def test_a_master_receipt_encode_failure_never_reaches_the_commit(self):
        fake = gateway()

        with mock.patch.object(
            admin_contract,
            "encode_receipt",
            side_effect=admin_contract.AdminContractError(
                "invalid receipt identifier"
            ),
        ):
            payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.admin_master_create_calls), 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_claim.committed, [])
        self.assertEqual(len(fake.admin_fence_claim.unknowns), 1)

    def test_the_committed_master_receipt_binds_the_ack_without_a_re_encode(self):
        fake = gateway()
        real_encode = admin_contract.encode_receipt
        encodes = []

        def counting_encode(**kwargs):
            encodes.append(dict(kwargs))
            return real_encode(**kwargs)

        with mock.patch.object(
            admin_contract, "encode_receipt", counting_encode
        ):
            payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(len(encodes), 1)
        receipt = fake.admin_fence_claim.committed[0]
        self.assertEqual(
            payload["ackToken"],
            admin_contract.ack_token(
                SECRET.encode(),
                SITE,
                ACTOR,
                admin_contract.ADMIN_MASTER_KIND,
                MASTER_KEY,
                receipt,
            ),
        )

    def test_a_master_commit_failure_is_uncertain_and_locked(self):
        fake = gateway()

        def boom():
            raise RuntimeError("commit outcome unknown")

        fake.commit = boom
        payload = admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.admin_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.admin_fence_claim.committed, [])

    def test_a_failed_master_rollback_escapes_to_frappe_cleanup(self):
        fake = gateway()
        fake.admin_master_create_error = RuntimeError("native failed")

        def rollback_failure():
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_failure
        with self.assertRaises(admin_write.RollbackFailed):
            admin_write.edit_master(fake, master_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(len(fake.admin_fence_claim.unknowns), 1)


class PractitionerMasterTests(unittest.TestCase):
    """Amendment A1.1: the practitioner master op through the contract."""

    def body(self, **overrides):
        payload = {"practitioner_name": "SIT Doctor", "user": ""}
        payload.update(overrides.pop("payload", {}))
        return master_body("practitioner", payload, **overrides)

    def test_creates_one_practitioner_and_receipts_the_supplied_name(self):
        fake = gateway()

        payload = admin_write.edit_master(fake, self.body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["master"]["master_type"], "practitioner")
        self.assertEqual(payload["master"]["master_id"], "HLC-PRAC-2026-00001")
        self.assertEqual(fake.admin_fence_calls[0][1], "admin_master")
        self.assertEqual(len(fake.admin_master_create_calls), 1)
        master_type, sent = fake.admin_master_create_calls[0]
        self.assertEqual(master_type, "practitioner")
        self.assertEqual(
            sent, {"practitioner_name": "SIT Doctor", "user": None}
        )
        receipt = admin_contract.decode_receipt(
            fake.admin_fence_claim.committed[0]
        )
        self.assertEqual(receipt.master_type, "practitioner")
        # The installed row is autonamed; the receipt carries the created name.
        self.assertEqual(receipt.identifier, "HLC-PRAC-2026-00001")

    def test_a_linked_user_is_validated_and_linked(self):
        fake = gateway()
        fake.admin_users.add("sit.doc@example.invalid")
        fake.admin_enabled_users.add("sit.doc@example.invalid")

        payload = admin_write.edit_master(
            fake, self.body(payload={"user": "sit.doc@example.invalid"}), CSRF
        )

        self.assertEqual(payload["status"], "created")
        self.assertEqual(
            fake.admin_master_create_calls[0][1]["user"],
            "sit.doc@example.invalid",
        )
        self.assertEqual(
            set(fake.admin_practitioner_for_user_calls),
            {"sit.doc@example.invalid"},
        )

    def test_an_unknown_user_is_refused_before_the_fence(self):
        fake = gateway()

        payload = admin_write.edit_master(
            fake, self.body(payload={"user": "sit.doc@example.invalid"}), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_a_disabled_user_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_users.add("sit.doc@example.invalid")

        payload = admin_write.edit_master(
            fake, self.body(payload={"user": "sit.doc@example.invalid"}), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_a_second_practitioner_for_the_same_user_is_refused_before_the_fence(
        self,
    ):
        fake = gateway()
        fake.admin_enabled_users.add("sit.doc@example.invalid")
        fake.admin_practitioner_users["sit.doc@example.invalid"] = (
            "HLC-PRAC-2026-00001"
        )

        payload = admin_write.edit_master(
            fake, self.body(payload={"user": "sit.doc@example.invalid"}), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])

    def test_a_mandatory_link_with_zero_rows_is_refused_before_the_fence(self):
        fake = gateway()
        fake.admin_prerequisites = dict(
            fake.admin_prerequisites, practitioner_links={"gender": 0}
        )

        payload = admin_write.edit_master(fake, self.body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.admin_fence_calls, [])

    def test_the_practitioner_payload_shape_is_strict(self):
        cases = (
            {"practitioner_name": "SIT Doctor"},
            {"practitioner_name": "SIT Doctor", "user": "", "extra": "x"},
            {"practitioner_name": "", "user": ""},
            {"practitioner_name": "SIT Doctor", "user": 7},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                fake = gateway()
                result = admin_write.edit_master(
                    fake, master_body("practitioner", payload), CSRF
                )
                self.assertEqual(result, {"code": "VALIDATION"})
                self.assertEqual(fake.admin_fence_calls, [])

    def test_the_practitioner_op_needs_the_native_create_authority(self):
        fake = gateway()
        fake.admin_can_create = {"Healthcare Practitioner": False}

        with self.assertRaises(Denied):
            admin_write.edit_master(fake, self.body(), CSRF)
        self.assertEqual(fake.admin_fence_calls, [])
        self.assertEqual(fake.admin_master_create_calls, [])


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

    def test_the_converted_user_flag_needs_no_deployment_marker_or_fence(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False

        capabilities = session.bootstrap(fake)["capabilities"]

        # The user lane is native; the master lane and its ack stay closed.
        self.assertEqual(capabilities["write.admin.user"], "enabled")
        self.assertEqual(capabilities["write.admin.master"], "read_only")

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
