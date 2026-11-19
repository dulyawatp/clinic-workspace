"""Contract tests for the admin users + masters family.

These pin the closed-world decisions that must happen *before* the fence claim:
the installed-field preflight, the role allowlist (the privilege boundary), the
strict request bodies for both operation groups, duplicate identifiers, the
per-DocType master payload variants and the codecs. Credentials never appear in
a receipt, a digest or an error message.
"""

import unittest

from clinic_ui.core import admin_contract as contract
from clinic_ui.core import write_budgets


def meta_fields(**overrides):
    fields = {
        doctype: set(names) for doctype, names in contract.REQUIRED_FIELDS.items()
    }
    for doctype, names in overrides.items():
        fields[doctype] = names if isinstance(names, set) else set(names)
    return fields


def user_body(**overrides):
    values = {
        "operation_key": "admin-user-key-0001-01",
        "email": "sit.pha@example.invalid",
        "full_name": "SIT Pharmacist",
        "clinic_role_code": "PHA",
        "password": "caller-supplied-secret-value",
    }
    values.update(overrides)
    return values


def master_body(master_type="warehouse", payload=None, **overrides):
    values = {
        "operation_key": "admin-master-key-0001-01",
        "master_type": master_type,
        "payload": payload if payload is not None else warehouse_payload(),
    }
    values.update(overrides)
    return values


def warehouse_payload(**overrides):
    values = {"warehouse_name": "SIT Pharmacy"}
    values.update(overrides)
    return values


def supplier_payload(**overrides):
    values = {"supplier_name": "SIT Supplier"}
    values.update(overrides)
    return values


def item_payload(**overrides):
    values = {
        "item_code": "SIT-ITEM-1",
        "item_name": "SIT Item One",
        "item_group": "All Item Groups",
        "stock_uom": "Nos",
        "price_list": "Standard Selling",
        "price_list_rate": 12.5,
    }
    values.update(overrides)
    return values


def price_list_payload(**overrides):
    values = {"price_list_name": "Standard Selling"}
    values.update(overrides)
    return values


def payment_payload(**overrides):
    values = {
        "mode_of_payment": "Cash",
        "account": "Cash - SITCO",
    }
    values.update(overrides)
    return values


def template_payload(**overrides):
    values = {
        "template_name": "SIT Dressing",
        "item_code": "SIT-ITEM-1",
        "rate": 25.0,
    }
    values.update(overrides)
    return values


def uom_payload(**overrides):
    values = {"uom_name": "Nos"}
    values.update(overrides)
    return values


def supplier_group_payload(**overrides):
    values = {"supplier_group_name": "SIT Suppliers"}
    values.update(overrides)
    return values


def practitioner_payload(**overrides):
    values = {"practitioner_name": "SIT Doctor", "user": ""}
    values.update(overrides)
    return values


def prerequisites(**overrides):
    values = {
        "company": "SIT Synthetic Company",
        "uom_rows": 1,
        "supplier_group": "All Supplier Groups",
        "supplier_group_rows": 1,
        # Probe-verified: Healthcare Practitioner has no mandatory Link fields on
        # this stack (Amendment A1.1), so the map is empty.
        "practitioner_links": {},
    }
    values.update(overrides)
    return values


ROLE_MAP = {
    "DOC": ("Physician",),
    "NUR": ("Nursing User",),
    "PHA": ("Stock Manager",),
    "CAS": ("Accounts User",),
    "MGR": ("Clinic Lab Manager",),
    "ADM": ("System Manager",),
}


class InstallPreflightTests(unittest.TestCase):
    def test_installed_field_set_resolves(self):
        self.assertIsNone(contract.install_problem(meta_fields()))

    def test_a_missing_doctype_is_a_named_problem(self):
        fields = meta_fields()
        fields.pop("Warehouse")
        self.assertIn("Warehouse", contract.install_problem(fields))

    def test_a_missing_logical_field_is_a_named_problem(self):
        fields = meta_fields(**{"Item Price": {"item_code", "price_list"}})
        problem = contract.install_problem(fields)
        self.assertIsNotNone(problem)
        self.assertIn("Item Price", problem)

    def test_unreadable_metadata_fails_closed(self):
        self.assertIsNotNone(contract.install_problem(None))


class RoleAllowlistTests(unittest.TestCase):
    def test_a_mapped_assignable_role_is_accepted(self):
        self.assertIsNone(contract.role_problem(ROLE_MAP, "PHA"))
        self.assertEqual(
            contract.assignable_roles(ROLE_MAP, "PHA"), ("Stock Manager",)
        )

    def test_builtin_module_and_clinic_roles_are_assignable(self):
        for role in (
            "Physician",
            "Nursing User",
            "Stock Manager",
            "Stock User",
            "Accounts User",
            "Purchase User",
            "Sales User",
            "Item Manager",
            "Clinic Lab Reception",
            "Clinic Lab Pharmacy",
            "Clinic Lab Cashier",
            "Clinic Lab Manager",
            "Clinic Lab Administrator",
        ):
            with self.subTest(role=role):
                self.assertIn(role, contract.ASSIGNABLE_ROLES)

    def test_system_manager_is_never_assignable(self):
        self.assertIsNotNone(contract.role_problem(ROLE_MAP, "ADM"))
        self.assertIn("ADM", contract.role_problem(ROLE_MAP, "ADM"))

    def test_healthcare_administrator_is_never_assignable(self):
        role_map = {"REC": ("Healthcare Administrator",)}
        self.assertIsNotNone(contract.role_problem(role_map, "REC"))

    def test_an_unmapped_code_is_refused(self):
        role_map = {"PHA": ("Stock Manager",)}
        self.assertIsNotNone(contract.role_problem(role_map, "CAS"))

    def test_an_unsupported_code_is_refused(self):
        self.assertIsNotNone(contract.role_problem(ROLE_MAP, "OWNER"))

    def test_a_map_with_two_roles_keeps_the_order(self):
        role_map = {"PHA": ("Stock Manager", "Stock User")}
        self.assertEqual(
            contract.assignable_roles(role_map, "PHA"),
            ("Stock Manager", "Stock User"),
        )


class CreateUserBodyTests(unittest.TestCase):
    def test_a_valid_body_normalises(self):
        payload = contract.validate_create_user(user_body())
        self.assertEqual(payload["kind"], contract.ADMIN_USER_KIND)
        self.assertEqual(payload["clinic_role_code"], "PHA")
        self.assertEqual(payload["email"], "sit.pha@example.invalid")
        self.assertIsNone(payload["username"])
        self.assertIsNone(payload["practitioner"])

    def test_a_body_without_the_email_key_is_refused(self):
        body = user_body()
        del body["email"]

        with self.assertRaises(contract.AdminContractError):
            contract.validate_create_user(body)

    def test_the_username_format_stays_bounded(self):
        for candidate in ("a", "bad user", "-lead", "name!", "x" * 141):
            with self.subTest(candidate=candidate):
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_create_user(user_body(username=candidate))
        payload = contract.validate_create_user(user_body(username="Sit.PHA-1"))
        self.assertEqual(payload["username"], "sit.pha-1")

    def test_a_real_email_is_required_and_username_alone_is_refused(self):
        with self.assertRaises(contract.AdminContractError):
            contract.validate_create_user(
                user_body(email=None, username="sit.pha")
            )

    def test_the_email_and_username_are_case_normalised(self):
        payload = contract.validate_create_user(
            user_body(email="Sit.PHA@Example.INVALID", username="Sit.PHA")
        )
        self.assertEqual(payload["email"], "sit.pha@example.invalid")
        self.assertEqual(payload["username"], "sit.pha")

    def test_a_username_equal_to_the_email_is_kept_optional(self):
        payload = contract.validate_create_user(
            user_body(username="sit.pha@example.invalid")
        )
        self.assertEqual(payload["username"], "sit.pha@example.invalid")

    def test_an_email_is_required(self):
        with self.assertRaises(contract.AdminContractError):
            contract.validate_create_user(user_body(email=None))

    def test_the_password_is_required_and_never_normalised_away(self):
        with self.assertRaises(contract.AdminContractError):
            contract.validate_create_user(user_body(password=None))
        payload = contract.validate_create_user(user_body())
        self.assertEqual(payload["password"], "caller-supplied-secret-value")

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            user_body(extra="nope"),
            user_body(clinic_role_code="OWNER"),
            user_body(full_name=""),
            user_body(operation_key="short"),
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_create_user(candidate)

    def test_the_practitioner_link_is_optional_and_normalised(self):
        payload = contract.validate_create_user(
            user_body(practitioner="HLC-PRACT-1")
        )
        self.assertEqual(payload["practitioner"], "HLC-PRACT-1")


class EditMasterBodyTests(unittest.TestCase):
    PAIRS = (
        ("warehouse", warehouse_payload),
        ("supplier", supplier_payload),
        ("item", item_payload),
        ("price_list", price_list_payload),
        ("mode_of_payment", payment_payload),
        ("procedure_template", template_payload),
        ("uom", uom_payload),
        ("supplier_group", supplier_group_payload),
    )

    def test_every_designed_variant_normalises(self):
        for master_type, builder in self.PAIRS:
            with self.subTest(master_type=master_type):
                payload = contract.validate_edit_master(
                    master_body(master_type, builder())
                )
                self.assertEqual(payload["kind"], contract.ADMIN_MASTER_KIND)
                self.assertEqual(payload["master_type"], master_type)

    def test_an_unknown_master_type_is_refused(self):
        with self.assertRaises(contract.AdminContractError):
            contract.validate_edit_master(
                master_body("custom_doctype", warehouse_payload())
            )

    def test_unknown_payload_fields_are_refused(self):
        for master_type, builder in self.PAIRS:
            with self.subTest(master_type=master_type):
                payload = builder()
                payload["docstatus"] = 1
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_edit_master(master_body(master_type, payload))

    def test_missing_required_fields_are_refused(self):
        cases = (
            ("warehouse", {}),
            ("supplier", {"supplier_name": ""}),
            ("item", {**item_payload(), "price_list": ""}),
            ("price_list", {}),
            ("mode_of_payment", {"mode_of_payment": "Cash"}),
            ("procedure_template", {"template_name": "T"}),
            ("uom", {"uom_name": ""}),
            ("supplier_group", {}),
        )
        for master_type, payload in cases:
            with self.subTest(master_type=master_type):
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_edit_master(master_body(master_type, payload))

    def test_a_non_positive_rate_is_refused(self):
        cases = (
            ("item", item_payload(price_list_rate=0)),
            ("procedure_template", template_payload(rate=0)),
        )
        for master_type, payload in cases:
            with self.subTest(master_type=master_type):
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_edit_master(
                        master_body(master_type, payload)
                    )

    def test_the_natural_identifier_is_per_variant(self):
        self.assertEqual(
            contract.identifier_for("warehouse", {"warehouse_name": "WH-1"}),
            "WH-1",
        )
        self.assertEqual(
            contract.identifier_for("supplier", {"supplier_name": "SUP-1"}),
            "SUP-1",
        )
        self.assertEqual(
            contract.identifier_for("item", {"item_code": "ITEM-1"}), "ITEM-1"
        )
        self.assertEqual(
            contract.identifier_for(
                "price_list", {"price_list_name": "Standard Selling"}
            ),
            "Standard Selling",
        )
        self.assertEqual(
            contract.identifier_for(
                "mode_of_payment", {"mode_of_payment": "Cash"}
            ),
            "Cash",
        )
        self.assertEqual(
            contract.identifier_for(
                "procedure_template", {"template_name": "Dressing"}
            ),
            "Dressing",
        )
        self.assertEqual(
            contract.identifier_for("uom", {"uom_name": "Nos"}), "Nos"
        )
        self.assertEqual(
            contract.identifier_for(
                "supplier_group", {"supplier_group_name": "SIT Suppliers"}
            ),
            "SIT Suppliers",
        )


class PrerequisiteTests(unittest.TestCase):
    """Amendment A1: the master prerequisites are decided before the fence."""

    def test_a_resolved_site_has_no_problem_for_every_variant(self):
        for master_type in contract.MASTER_TYPES:
            with self.subTest(master_type=master_type):
                self.assertIsNone(
                    contract.prerequisite_problem(master_type, prerequisites())
                )

    def test_no_company_refuses_every_admin_master_op(self):
        for master_type in contract.MASTER_TYPES:
            with self.subTest(master_type=master_type):
                self.assertEqual(
                    contract.prerequisite_problem(
                        master_type, prerequisites(company=None)
                    ),
                    "no company resolves for the admin surface",
                )

    def test_no_uom_row_refuses_the_item_op(self):
        self.assertEqual(
            contract.prerequisite_problem("item", prerequisites(uom_rows=0)),
            "no UOM rows are installed for the item unit",
        )
        self.assertIsNone(
            contract.prerequisite_problem("uom", prerequisites(uom_rows=0))
        )

    def test_no_supplier_group_row_refuses_the_supplier_op(self):
        self.assertEqual(
            contract.prerequisite_problem(
                "supplier",
                prerequisites(supplier_group=None, supplier_group_rows=0),
            ),
            "no Supplier Group rows are installed for the supplier",
        )
        self.assertIsNone(
            contract.prerequisite_problem(
                "supplier_group",
                prerequisites(supplier_group=None, supplier_group_rows=0),
            )
        )

    def test_unreadable_prerequisites_are_refused(self):
        self.assertEqual(
            contract.prerequisite_problem("warehouse", None),
            "master prerequisites unreadable",
        )
        self.assertEqual(
            contract.prerequisite_problem(
                "warehouse", prerequisites(uom_rows=None)
            ),
            "UOM rows are unreadable",
        )

    def test_the_practitioner_link_prerequisites_are_probe_derived(self):
        self.assertEqual(
            contract.prerequisite_problem("practitioner", prerequisites()),
            None,
        )
        self.assertEqual(
            contract.prerequisite_problem(
                "practitioner",
                prerequisites(practitioner_links={"gender": 0}),
            ),
            "no rows are installed for the mandatory practitioner link gender",
        )
        self.assertEqual(
            contract.prerequisite_problem(
                "practitioner", prerequisites(practitioner_links=None)
            ),
            "practitioner link targets unreadable",
        )
        self.assertEqual(
            contract.prerequisite_problem(
                "practitioner",
                prerequisites(practitioner_links={"gender": None}),
            ),
            "practitioner link rows unreadable for gender",
        )

    def test_the_three_new_kinds_are_installed_masters(self):
        self.assertEqual(contract.MASTER_DOCTYPES["uom"], "UOM")
        self.assertEqual(
            contract.MASTER_DOCTYPES["supplier_group"], "Supplier Group"
        )
        self.assertEqual(
            contract.MASTER_DOCTYPES["practitioner"], "Healthcare Practitioner"
        )
        self.assertEqual(
            contract.MASTER_PAYLOAD_KEYS["uom"], frozenset({"uom_name"})
        )
        self.assertEqual(
            contract.MASTER_PAYLOAD_KEYS["supplier_group"],
            frozenset({"supplier_group_name"}),
        )
        self.assertEqual(
            contract.MASTER_PAYLOAD_KEYS["practitioner"],
            frozenset({"practitioner_name", "user"}),
        )


class PractitionerMasterContractTests(unittest.TestCase):
    """Amendment A1.1: payload, identifier and facts for the practitioner kind."""

    def test_an_empty_user_link_normalises_to_none(self):
        payload = contract.validate_edit_master(
            master_body("practitioner", practitioner_payload())
        )["payload"]
        self.assertIsNone(payload["user"])

    def test_a_named_user_link_is_kept(self):
        payload = contract.validate_edit_master(
            master_body(
                "practitioner",
                practitioner_payload(user="sit.doc@example.invalid"),
            )
        )["payload"]
        self.assertEqual(payload["user"], "sit.doc@example.invalid")

    def test_the_payload_shape_is_closed_world(self):
        cases = (
            {"practitioner_name": "SIT Doctor"},
            {"user": ""},
            {"practitioner_name": "SIT Doctor", "user": "", "designation": "x"},
            {"practitioner_name": "", "user": ""},
            {"practitioner_name": "SIT Doctor", "user": 7},
        )
        for payload in cases:
            with self.subTest(payload=payload):
                with self.assertRaises(contract.AdminContractError):
                    contract.validate_edit_master(
                        master_body("practitioner", payload)
                    )

    def test_the_identifier_is_the_supplied_name(self):
        self.assertEqual(
            contract.identifier_for("practitioner", practitioner_payload()),
            "SIT Doctor",
        )

    def test_the_facts_compare_the_name_and_the_user_link(self):
        payload = practitioner_payload(user="sit.doc@example.invalid")
        expected = {"identifier": "HLC-PRAC-2026-00001", "payload": payload}
        self.assertIsNone(
            contract.master_facts_problem(
                "practitioner",
                {
                    "name": "HLC-PRAC-2026-00001",
                    "practitioner_name": "SIT Doctor",
                    "user_id": "sit.doc@example.invalid",
                },
                expected,
            )
        )
        self.assertEqual(
            contract.master_facts_problem(
                "practitioner",
                {
                    "name": "HLC-PRAC-2026-00001",
                    "practitioner_name": "Someone Else",
                    "user_id": "sit.doc@example.invalid",
                },
                expected,
            ),
            "created practitioner differs from the request",
        )
        self.assertEqual(
            contract.master_facts_problem(
                "practitioner",
                {
                    "name": "HLC-PRAC-2026-00001",
                    "practitioner_name": "SIT Doctor",
                    "user_id": None,
                },
                expected,
            ),
            "created practitioner user differs from the request",
        )

    def test_the_practitioner_mandatory_fields_are_the_installed_ones(self):
        self.assertEqual(
            contract.REQUIRED_FIELDS[contract.PRACTITIONER_DOCTYPE],
            {
                "practitioner_name": ("practitioner_name",),
                "first_name": ("first_name",),
                "status": ("status",),
                "user_id": ("user_id",),
            },
        )

    def test_the_new_master_facts_are_their_names(self):
        for master_type, payload in (
            ("uom", uom_payload()),
            ("supplier_group", supplier_group_payload()),
        ):
            with self.subTest(master_type=master_type):
                identifier = contract.identifier_for(master_type, payload)
                self.assertIsNone(
                    contract.master_facts_problem(
                        master_type,
                        {"name": identifier},
                        {"identifier": identifier, "payload": payload},
                    )
                )
                self.assertEqual(
                    contract.master_facts_problem(
                        master_type,
                        {"name": "SOMETHING-ELSE"},
                        {"identifier": identifier, "payload": payload},
                    ),
                    "created master differs from the request",
                )


class BudgetTests(unittest.TestCase):
    def test_the_family_budgets_are_consumed_from_the_registry(self):
        self.assertEqual(contract.budget("admin_user"), 4)
        self.assertEqual(contract.budget("admin_master"), 15)
        self.assertEqual(
            contract.ADMIN_KINDS, ("admin_user", "admin_master")
        )
        for kind in contract.ADMIN_KINDS:
            self.assertEqual(
                contract.budget(kind), write_budgets.budget_for(kind)
            )

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(contract.AdminContractError):
            contract.budget("admin_delete")


class ReceiptTests(unittest.TestCase):
    def test_a_user_receipt_carries_only_the_name(self):
        encoded = contract.encode_receipt(
            kind="admin_user", identifier="sit.pha@example.invalid"
        )
        self.assertNotIn("secret", encoded)
        decoded = contract.decode_receipt(encoded)
        self.assertEqual(decoded.kind, "admin_user")
        self.assertEqual(decoded.identifier, "sit.pha@example.invalid")
        self.assertEqual(decoded.master_type, "")

    def test_a_master_receipt_carries_the_variant_and_name(self):
        encoded = contract.encode_receipt(
            kind="admin_master",
            master_type="warehouse",
            identifier="SIT Pharmacy",
        )
        decoded = contract.decode_receipt(encoded)
        self.assertEqual(decoded.master_type, "warehouse")
        self.assertEqual(decoded.identifier, "SIT Pharmacy")

    def test_a_malformed_receipt_is_refused(self):
        for candidate in (
            "",
            "{}",
            '{"codec":"other"}',
            '{"codec":"admin","version":1,"kind":"admin_master",'
            '"master_type":"custom","identifier":"X"}',
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(contract.AdminContractError):
                    contract.decode_receipt(candidate)


class DigestAndAckTests(unittest.TestCase):
    SECRET = b"0123456789abcdef0123456789abcdef"

    def test_the_digest_binds_kind_key_and_fields(self):
        one = contract.operation_digest(
            self.SECRET,
            kind="admin_user",
            operation_key="admin-user-key-0001-01",
            fields={"identifier": "a@example.invalid"},
        )
        two = contract.operation_digest(
            self.SECRET,
            kind="admin_user",
            operation_key="admin-user-key-0001-01",
            fields={"identifier": "b@example.invalid"},
        )
        self.assertNotEqual(one, two)

    def test_the_ack_token_binds_the_receipt(self):
        receipt = contract.encode_receipt(
            kind="admin_user", identifier="sit.pha@example.invalid"
        )
        token = contract.ack_token(
            self.SECRET,
            "marley-sit.local",
            "adm@example.invalid",
            "admin_user",
            "admin-user-key-0001-01",
            receipt,
        )
        self.assertTrue(contract.is_valid_ack_token(token))
        decoded = contract.validate_ack(
            {
                "operation_key": "admin-user-key-0001-01",
                "kind": "admin_user",
                "ack_token": token,
            }
        )
        self.assertEqual(decoded["kind"], "admin_user")


class FactsTests(unittest.TestCase):
    def test_a_matching_user_re_read_has_no_problem(self):
        expected = {
            "identifier": "sit.pha@example.invalid",
            "roles": ("Stock Manager",),
            "practitioner": None,
        }
        facts = {
            "user": "sit.pha@example.invalid",
            "enabled": 1,
            "roles": ["Stock Manager"],
            "practitioner": None,
        }
        self.assertIsNone(contract.user_facts_problem(facts, expected))

    def test_a_missing_role_or_wrong_practitioner_is_a_problem(self):
        expected = {
            "identifier": "sit.pha@example.invalid",
            "roles": ("Stock Manager",),
            "practitioner": "HLC-PRACT-1",
        }
        self.assertIsNotNone(
            contract.user_facts_problem(
                {
                    "user": "sit.pha@example.invalid",
                    "enabled": 1,
                    "roles": ["Stock User"],
                    "practitioner": "HLC-PRACT-1",
                },
                expected,
            )
        )
        self.assertIsNotNone(
            contract.user_facts_problem(
                {
                    "user": "sit.pha@example.invalid",
                    "enabled": 1,
                    "roles": ["Stock Manager"],
                    "practitioner": None,
                },
                expected,
            )
        )

    def test_a_matching_item_re_read_has_no_problem(self):
        expected = {
            "identifier": "SIT-ITEM-1",
            "payload": item_payload(),
        }
        facts = {
            "name": "SIT-ITEM-1",
            "item_code": "SIT-ITEM-1",
            "price_list": "Standard Selling",
            "price_list_rate": 12.5,
        }
        self.assertIsNone(
            contract.master_facts_problem("item", facts, expected)
        )
        facts["price_list_rate"] = 99.0
        self.assertIsNotNone(
            contract.master_facts_problem("item", facts, expected)
        )

    def test_a_matching_payment_mapping_has_no_problem(self):
        expected = {
            "identifier": "Cash",
            "payload": payment_payload(),
        }
        facts = {"name": "Cash", "account": "Cash - SITCO"}
        self.assertIsNone(
            contract.master_facts_problem("mode_of_payment", facts, expected)
        )
        facts["account"] = "Bank - SITCO"
        self.assertIsNotNone(
            contract.master_facts_problem("mode_of_payment", facts, expected)
        )


if __name__ == "__main__":
    unittest.main()
