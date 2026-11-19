"""Contract tests for the purchasing (PO draft/submit + receipt) family.

The contract decides every designed refusal before the fence: role is decided by
the endpoint, but unknown supplier/item/warehouse, a line without item, quantity,
unit or rate, an empty itemization, a non-draft submit target, a receipt against
a non-submitted PO, over-receipt against the pending quantity and the batch rules
are all decided here. The EXPECTED side-effect list is count-based and closed:
a draft adds nothing beyond the document, a submit changes status/docstatus only,
and a PO-referenced receipt adds Stock Ledger rows, a Bin delta, a reviewed GL
row count and the PO received-quantity/status delta.
"""

import unittest

from clinic_ui.core import config, purchase_contract, write_budgets

SITE = config.APPROVED_WRITE_SITE
SECRET = b"0123456789abcdef0123456789abcdef"
ACTOR = "pharmacist@example.test"
KEY = "purchase-order-key-0001"


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


def draft_payload(**overrides):
    values = {
        "kind": purchase_contract.DRAFT_KIND,
        "operation_key": KEY,
        "supplier": "SUP-1",
        "items": [{"item_code": "ITEM-1", "qty": 5.0, "rate": 2.5}],
    }
    values.update(overrides)
    return values


def submit_payload(**overrides):
    values = {
        "kind": purchase_contract.SUBMIT_KIND,
        "operation_key": KEY,
        "purchase_order": "PO-1",
    }
    values.update(overrides)
    return values


def receipt_payload(**overrides):
    values = {
        "kind": purchase_contract.RECEIPT_KIND,
        "operation_key": KEY,
        "purchase_order": "PO-1",
        "item_code": "ITEM-1",
        "qty": 2.0,
        "warehouse": "WH-1",
        "batch_id": "BATCH-1",
    }
    values.update(overrides)
    return values


class KindAndBudgetTests(unittest.TestCase):
    def test_the_family_has_three_registry_kinds(self):
        self.assertEqual(
            purchase_contract.PURCHASE_KINDS,
            (
                "purchase_order_draft",
                "purchase_order_submit",
                "purchase_receipt",
            ),
        )

    def test_each_kind_reads_the_approved_budget(self):
        for kind in purchase_contract.PURCHASE_KINDS:
            self.assertEqual(purchase_contract.budget(kind), 1)
            self.assertEqual(write_budgets.budget_for(kind), 1)

    def test_an_unknown_kind_is_a_contract_error(self):
        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.budget("purchase_receipt_extra")


class BodyValidationTests(unittest.TestCase):
    def test_a_draft_body_needs_a_supplier_and_one_line(self):
        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.validate_draft(
                {"operation_key": KEY, "supplier": "SUP-1", "items": []}
            )
        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.validate_draft(
                {"operation_key": KEY, "items": [{"item_code": "I", "qty": 1, "rate": 1}]}
            )

    def test_a_draft_line_carries_only_item_quantity_and_rate(self):
        payload = purchase_contract.validate_draft(
            {
                "operation_key": KEY,
                "supplier": "SUP-1",
                "items": [{"item_code": "ITEM-1", "qty": 5, "rate": 2.5}],
            }
        )
        self.assertEqual(
            payload["items"], [{"item_code": "ITEM-1", "qty": 5.0, "rate": 2.5}]
        )
        self.assertEqual(payload["kind"], purchase_contract.DRAFT_KIND)

    def test_a_draft_line_may_not_carry_a_docstatus_or_money_field(self):
        for extra in ({"docstatus": 1}, {"currency": "THB"}, {"price_list": "X"}):
            line = {"item_code": "ITEM-1", "qty": 5, "rate": 2.5, **extra}
            with self.assertRaises(purchase_contract.PurchaseContractError):
                purchase_contract.validate_draft(
                    {
                        "operation_key": KEY,
                        "supplier": "SUP-1",
                        "items": [line],
                    }
                )

    def test_quantities_and_rates_are_positive_numbers(self):
        for line in (
            {"item_code": "ITEM-1", "qty": 0, "rate": 1},
            {"item_code": "ITEM-1", "qty": -1, "rate": 1},
            {"item_code": "ITEM-1", "qty": 1, "rate": 0},
            {"item_code": "ITEM-1", "qty": 1, "rate": -2},
            {"item_code": "ITEM-1", "qty": "1", "rate": 1},
            {"item_code": "ITEM-1", "qty": 1, "rate": "2"},
        ):
            with self.assertRaises(purchase_contract.PurchaseContractError):
                purchase_contract.validate_draft(
                    {
                        "operation_key": KEY,
                        "supplier": "SUP-1",
                        "items": [line],
                    }
                )

    def test_more_than_the_reviewed_row_count_is_refused(self):
        lines = [
            {"item_code": f"ITEM-{index}", "qty": 1, "rate": 1}
            for index in range(purchase_contract.MAX_ROWS + 1)
        ]
        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.validate_draft(
                {"operation_key": KEY, "supplier": "SUP-1", "items": lines}
            )

    def test_the_submit_and_receipt_bodies_are_strict(self):
        submit = purchase_contract.validate_submit(
            {"operation_key": KEY, "purchase_order": "PO-1"}
        )
        self.assertEqual(submit["purchase_order"], "PO-1")
        self.assertEqual(submit["kind"], purchase_contract.SUBMIT_KIND)

        receipt = purchase_contract.validate_receipt(
            {
                "operation_key": KEY,
                "purchase_order": "PO-1",
                "item_code": "ITEM-1",
                "qty": 2,
                "warehouse": "WH-1",
            }
        )
        self.assertEqual(receipt["kind"], purchase_contract.RECEIPT_KIND)
        self.assertIsNone(receipt["batch_id"])

        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.validate_submit(
                {"operation_key": KEY, "purchase_order": "PO-1", "submit": 1}
            )
        with self.assertRaises(purchase_contract.PurchaseContractError):
            purchase_contract.validate_receipt(
                {
                    "operation_key": KEY,
                    "purchase_order": "PO-1",
                    "item_code": "ITEM-1",
                    "qty": 2,
                }
            )


class PreFenceDecisionTests(unittest.TestCase):
    def test_a_draft_refuses_an_unknown_supplier(self):
        problems = purchase_contract.draft_problems(
            draft_detail(supplier_exists=False), draft_payload()
        )
        self.assertEqual(problems[0][0], purchase_contract.HTTP_REFUSED)
        self.assertEqual(problems[0][1], "supplier")

    def test_a_draft_refuses_an_unknown_item_with_a_named_line(self):
        problems = purchase_contract.draft_problems(
            draft_detail(items={}), draft_payload()
        )
        self.assertEqual(problems[0][1], "items[1].item_code")

    def test_a_draft_refuses_an_item_without_a_unit(self):
        problems = purchase_contract.draft_problems(
            draft_detail(items={"ITEM-1": item(stock_uom="")}), draft_payload()
        )
        self.assertEqual(problems[0][1], "items[1].uom")

    def test_a_draft_refuses_a_non_stock_item(self):
        problems = purchase_contract.draft_problems(
            draft_detail(items={"ITEM-1": item(is_stock_item=False)}),
            draft_payload(),
        )
        self.assertEqual(problems[0][1], "items[1].item_code")

    def test_a_draft_refuses_a_company_or_currency_that_is_not_readable(self):
        for detail in (
            draft_detail(company=""),
            draft_detail(currency=""),
        ):
            problems = purchase_contract.draft_problems(detail, draft_payload())
            self.assertEqual(problems[0][0], purchase_contract.HTTP_REFUSED)

    def test_a_submit_refuses_a_target_that_is_not_a_draft(self):
        detail = submit_detail(
            purchase_order={
                "exists": True,
                "docstatus": 1,
                "supplier": "SUP-1",
                "status": "To Receive and Bill",
            }
        )
        problems = purchase_contract.submit_problems(detail, submit_payload())
        self.assertEqual(problems[0][1], "purchase_order")

    def test_a_submit_refuses_an_unknown_target(self):
        detail = submit_detail(
            purchase_order={"exists": False, "docstatus": None, "supplier": None}
        )
        problems = purchase_contract.submit_problems(detail, submit_payload())
        self.assertEqual(problems[0][0], purchase_contract.HTTP_REFUSED)

    def test_a_submit_refuses_a_draft_without_complete_lines(self):
        for line in (
            {"item_code": "ITEM-1", "qty": 0, "rate": 2.5, "uom": "Unit"},
            {"item_code": "ITEM-1", "qty": 5, "rate": 2.5, "uom": ""},
            {"item_code": "ITEM-1", "qty": 5, "rate": 0, "uom": "Unit"},
        ):
            problems = purchase_contract.submit_problems(
                submit_detail(lines=[line]), submit_payload()
            )
            self.assertEqual(problems[0][1], "lines[1]")

    def test_a_submit_refuses_a_draft_with_no_lines(self):
        problems = purchase_contract.submit_problems(
            submit_detail(lines=[]), submit_payload()
        )
        self.assertEqual(problems[0][1], "items")

    def test_a_receipt_requires_a_submitted_purchase_order(self):
        for docstatus in (0, 2, None):
            problems = purchase_contract.receipt_problems(
                receipt_detail(
                    purchase_order={"exists": True, "docstatus": docstatus}
                ),
                receipt_payload(),
            )
            self.assertEqual(problems[0][1], "purchase_order")

    def test_a_receipt_refuses_an_unknown_item_or_warehouse(self):
        problems = purchase_contract.receipt_problems(
            receipt_detail(item={"exists": False}), receipt_payload()
        )
        self.assertEqual(problems[0][1], "item_code")
        problems = purchase_contract.receipt_problems(
            receipt_detail(warehouse={"exists": False}), receipt_payload()
        )
        self.assertEqual(problems[0][1], "warehouse")

    def test_a_receipt_refuses_an_over_receipt_with_a_named_line(self):
        problems = purchase_contract.receipt_problems(
            receipt_detail(po_pending_qty=1.0), receipt_payload(qty=2.0)
        )
        self.assertEqual(problems[0][0], purchase_contract.HTTP_REFUSED)
        self.assertEqual(problems[0][1], "qty")
        self.assertIn(purchase_contract.OVERRECEIPT_MESSAGE, problems[0][2])

    def test_a_receipt_refuses_a_missing_po_line(self):
        problems = purchase_contract.receipt_problems(
            receipt_detail(po_pending_qty=None, po_item=None), receipt_payload()
        )
        self.assertEqual(problems[0][1], "purchase_order")

    def test_a_receipt_refuses_batch_gaps_before_the_fence(self):
        problems = purchase_contract.receipt_problems(
            receipt_detail(batch=None), receipt_payload(batch_id=None)
        )
        self.assertEqual(problems[0][1], "batch_no")
        problems = purchase_contract.receipt_problems(
            receipt_detail(
                batch={
                    "batch_id": "NEW",
                    "exists": False,
                    "item": "ITEM-1",
                }
            ),
            receipt_payload(batch_id="NEW", batch_expiry=None),
        )
        self.assertEqual(problems[0][1], "batch_expiry")
        problems = purchase_contract.receipt_problems(
            receipt_detail(
                batch={
                    "batch_id": "NEW",
                    "exists": False,
                    "item": "ITEM-1",
                }
            ),
            receipt_payload(batch_id="NEW", batch_expiry="2020-01-01"),
        )
        self.assertEqual(problems[0][1], "batch_expiry")


class ExpectedSideEffectTests(unittest.TestCase):
    def test_a_draft_expects_nothing_beyond_the_document(self):
        expected = purchase_contract.expected_draft(
            draft_detail(), draft_payload()
        )
        self.assertEqual(expected["docstatus"], 0)
        self.assertEqual(expected["sle_rows"], 0)
        self.assertEqual(expected["gl_rows_max"], 0)
        self.assertEqual(expected["doctype"], "Purchase Order")

    def test_a_submit_expects_status_and_docstatus_only(self):
        expected = purchase_contract.expected_submit(
            submit_detail(), submit_payload()
        )
        self.assertEqual(expected["docstatus"], 1)
        self.assertEqual(expected["status"], "To Receive and Bill")
        self.assertEqual(expected["sle_rows"], 0)

    def test_a_receipt_expects_ledger_bin_gl_and_the_po_delta(self):
        expected = purchase_contract.expected_receipt(
            receipt_detail(), receipt_payload()
        )
        self.assertEqual(expected["doctype"], "Purchase Receipt")
        self.assertEqual(expected["sle_rows"], 1)
        self.assertEqual(expected["bins"], {"WH-1": 2.0})
        self.assertEqual(expected["gl_rows_max"], 2)
        self.assertEqual(expected["po_received_delta"], 2.0)
        self.assertEqual(expected["po_status"], "To Receive and Bill")

    def test_a_full_receipt_expects_the_billing_status(self):
        expected = purchase_contract.expected_receipt(
            receipt_detail(po_pending_qty=2.0), receipt_payload(qty=2.0)
        )
        self.assertEqual(expected["po_status"], "To Bill")
    def test_the_side_effect_check_is_closed_world(self):
        expected = purchase_contract.expected_receipt(
            receipt_detail(), receipt_payload()
        )
        facts = {
            "docstatus": 1,
            "doctype": "Purchase Receipt",
            "sle_rows": 1,
            "gl_rows": 2,
            "bins": {"WH-1": 2.0},
            "po_received_delta": 2.0,
            "po_status": "To Receive and Bill",
        }
        self.assertIsNone(
            purchase_contract.side_effect_problem(facts, expected)
        )
        for broken in (
            {**facts, "sle_rows": 2},
            {**facts, "gl_rows": 1},
            {**facts, "bins": {"WH-1": 1.0}},
            {**facts, "po_received_delta": 1.0},
            {**facts, "po_status": "Completed"},
            {**facts, "docstatus": 0},
            {**facts, "doctype": "Purchase Order"},
        ):
            self.assertIsNotNone(
                purchase_contract.side_effect_problem(broken, expected)
            )


class PreparedBodyTests(unittest.TestCase):
    def test_a_prepared_draft_carries_server_money_and_units(self):
        prepared = {
            "kind": purchase_contract.DRAFT_KIND,
            "supplier": "SUP-1",
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
            "conversion_rate": 1.0,
            "transaction_date": "2026-11-16",
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 5.0,
                    "rate": 2.5,
                    "uom": "Unit",
                    "stock_uom": "Unit",
                    "conversion_factor": 1.0,
                    "schedule_date": "2026-11-16",
                }
            ],
        }
        self.assertIsNone(
            purchase_contract.prepared_problem(
                purchase_contract.DRAFT_KIND, prepared
            )
        )
        for field in ("company", "currency", "supplier", "transaction_date"):
            broken = {**prepared, field: ""}
            self.assertIsNotNone(
                purchase_contract.prepared_problem(
                    purchase_contract.DRAFT_KIND, broken
                )
            )
        broken = {**prepared, "items": [{**prepared["items"][0], "uom": ""}]}
        self.assertIsNotNone(
            purchase_contract.prepared_problem(
                purchase_contract.DRAFT_KIND, broken
            )
        )

    def test_a_prepared_submit_carries_the_draft_target(self):
        prepared = {
            "kind": purchase_contract.SUBMIT_KIND,
            "purchase_order": "PO-1",
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
            "docstatus": 0,
        }
        self.assertIsNone(
            purchase_contract.prepared_problem(
                purchase_contract.SUBMIT_KIND, prepared
            )
        )
        for broken in (
            {**prepared, "purchase_order": ""},
            {**prepared, "currency": ""},
            {**prepared, "docstatus": 1},
        ):
            self.assertIsNotNone(
                purchase_contract.prepared_problem(
                    purchase_contract.SUBMIT_KIND, broken
                )
            )

    def test_a_prepared_receipt_pins_posting_and_the_po_reference(self):
        prepared = {
            "kind": purchase_contract.RECEIPT_KIND,
            "purchase_order": "PO-1",
            "po_item": "POI-1",
            "supplier": "SUP-1",
            "item_code": "ITEM-1",
            "qty": 2.0,
            "uom": "Unit",
            "warehouse": "WH-1",
            "batch_no": "BATCH-1",
            "batch_expiry": None,
            "create_batch": False,
            "posting_date": "2026-11-16",
            "set_posting_time": True,
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
        }
        self.assertIsNone(
            purchase_contract.prepared_problem(
                purchase_contract.RECEIPT_KIND, prepared
            )
        )
        for broken in (
            {**prepared, "purchase_order": ""},
            {**prepared, "set_posting_time": False},
            {**prepared, "posting_date": ""},
            {**prepared, "company": ""},
            {**prepared, "qty": 0.0},
        ):
            self.assertIsNotNone(
                purchase_contract.prepared_problem(
                    purchase_contract.RECEIPT_KIND, broken
                )
            )


class CloseWhileReceiptingTests(unittest.TestCase):
    """Closing a PO is designed (CSV:117) but unwired this wave."""

    def test_a_receipt_in_progress_blocks_a_close_pre_fence(self):
        problem = purchase_contract.receipt_in_progress_problem(
            {"receipt_in_progress": True}
        )
        self.assertEqual(problem[0], purchase_contract.HTTP_REFUSED)
        self.assertIn(
            purchase_contract.RECEIPT_IN_PROGRESS_MESSAGE, problem[2]
        )
        self.assertIsNone(
            purchase_contract.receipt_in_progress_problem(
                {"receipt_in_progress": False}
            )
        )

    def test_there_is_no_close_kind_in_this_wave(self):
        self.assertNotIn("purchase_order_close", purchase_contract.PURCHASE_KINDS)
        self.assertNotIn("purchase_order_close", write_budgets.ALL_KINDS)


class ReceiptCodecTests(unittest.TestCase):
    def test_a_receipt_round_trips(self):
        encoded = purchase_contract.encode_receipt(
            kind=purchase_contract.RECEIPT_KIND,
            reference="PR-1",
            purchase_order="PO-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=2.0,
        )
        decoded = purchase_contract.decode_receipt(encoded)
        self.assertEqual(decoded.kind, purchase_contract.RECEIPT_KIND)
        self.assertEqual(decoded.reference, "PR-1")
        self.assertEqual(decoded.purchase_order, "PO-1")
        self.assertEqual(decoded.qty, 2.0)

    def test_a_draft_receipt_carries_the_order_name(self):
        encoded = purchase_contract.encode_receipt(
            kind=purchase_contract.DRAFT_KIND,
            reference="PO-1",
            purchase_order="PO-1",
        )
        decoded = purchase_contract.decode_receipt(encoded)
        self.assertEqual(decoded.reference, "PO-1")
        self.assertEqual(decoded.item_code, "")

    def test_a_malformed_receipt_is_refused(self):
        encoded = purchase_contract.encode_receipt(
            kind=purchase_contract.RECEIPT_KIND,
            reference="PR-1",
            purchase_order="PO-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=2.0,
        )
        for broken in (
            "",
            "{}",
            encoded.replace("purchase_receipt", "stock_receive"),
            encoded.replace('"qty":2.0', '"qty":"2"'),
        ):
            with self.assertRaises(purchase_contract.PurchaseContractError):
                purchase_contract.decode_receipt(broken)


class DigestAndAckTests(unittest.TestCase):
    def test_the_digest_is_deterministic_and_kind_bound(self):
        fields = {"supplier": "SUP-1", "items": "[ITEM-1:5.000000@2.500000]"}
        first = purchase_contract.operation_digest(
            SECRET,
            kind=purchase_contract.DRAFT_KIND,
            operation_key=KEY,
            fields=fields,
        )
        second = purchase_contract.operation_digest(
            SECRET,
            kind=purchase_contract.DRAFT_KIND,
            operation_key=KEY,
            fields=fields,
        )
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        other = purchase_contract.operation_digest(
            SECRET,
            kind=purchase_contract.SUBMIT_KIND,
            operation_key=KEY,
            fields=fields,
        )
        self.assertNotEqual(first, other)

    def test_the_ack_token_binds_site_actor_kind_key_and_receipt(self):
        receipt = purchase_contract.encode_receipt(
            kind=purchase_contract.RECEIPT_KIND,
            reference="PR-1",
            purchase_order="PO-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=2.0,
        )
        token = purchase_contract.ack_token(
            SECRET,
            SITE,
            ACTOR,
            purchase_contract.RECEIPT_KIND,
            KEY,
            receipt,
        )
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertTrue(purchase_contract.is_valid_ack_token(token))
        other = purchase_contract.ack_token(
            SECRET,
            SITE,
            "manager@example.test",
            purchase_contract.RECEIPT_KIND,
            KEY,
            receipt,
        )
        self.assertNotEqual(token, other)

    def test_the_ack_body_is_strict(self):
        token = "a" * 64
        payload = purchase_contract.validate_ack(
            {
                "operation_key": KEY,
                "kind": purchase_contract.RECEIPT_KIND,
                "ack_token": token,
            }
        )
        self.assertEqual(payload["ack_token"], token)
        for body in (
            {"operation_key": KEY, "ack_token": token},
            {
                "operation_key": KEY,
                "kind": purchase_contract.RECEIPT_KIND,
                "ack_token": "short",
            },
            {
                "operation_key": KEY,
                "kind": "stock_receive",
                "ack_token": token,
            },
        ):
            with self.assertRaises(purchase_contract.PurchaseContractError):
                purchase_contract.validate_ack(body)


class BoundaryAndInstallTests(unittest.TestCase):
    def test_the_installed_field_preflight_names_the_first_gap(self):
        meta = {
            doctype: {candidates[0] for candidates in fields.values()}
            for doctype, fields in purchase_contract.INSTALLED_CANDIDATES.items()
        }
        self.assertIsNone(purchase_contract.install_problem(meta))
        broken = dict(meta)
        broken[purchase_contract.PURCHASE_ORDER_DOCTYPE] = {
            "supplier",
            "company",
        }
        self.assertIn(
            "Purchase Order",
            purchase_contract.install_problem(broken),
        )

    def test_the_draft_preflight_ignores_receipt_only_fields(self):
        # ERPNext 16.52.1: Purchase Order Item has no purchase_order and no
        # received_qty column; a healthy install must still open the draft.
        meta = {
            doctype: {candidates[0] for candidates in fields.values()}
            for doctype, fields in purchase_contract.INSTALLED_CANDIDATES.items()
        }
        for doctype, fields in (
            purchase_contract.DRAFT_INSTALLED_CANDIDATES.items()
        ):
            meta.setdefault(doctype, set())
            meta[doctype] |= {
                candidates[0] for candidates in fields.values()
            }
        meta[purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE] = {
            candidates[0]
            for candidates in (
                purchase_contract.DRAFT_INSTALLED_CANDIDATES[
                    purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE
                ].values()
            )
        }

        self.assertEqual(
            purchase_contract.install_problem(meta),
            "missing field Purchase Order Item.received_qty",
        )
        self.assertIsNone(purchase_contract.draft_install_problem(meta))

        only_order = dict(meta)
        only_order[purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE] = (
            meta[purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE]
            | {"received_qty"}
        )
        self.assertEqual(
            purchase_contract.install_problem(only_order),
            "missing field Purchase Order Item.purchase_order",
        )

    def test_the_draft_preflight_still_names_a_missing_needed_field(self):
        meta = {
            doctype: {candidates[0] for candidates in fields.values()}
            for doctype, fields in (
                purchase_contract.DRAFT_INSTALLED_CANDIDATES.items()
            )
        }
        self.assertIsNone(purchase_contract.draft_install_problem(meta))

        meta[purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE].discard("rate")
        self.assertEqual(
            purchase_contract.draft_install_problem(meta),
            "missing field Purchase Order Item.rate",
        )

        missing = {
            doctype: {candidates[0] for candidates in fields.values()}
            for doctype, fields in (
                purchase_contract.DRAFT_INSTALLED_CANDIDATES.items()
            )
        }
        missing.pop("Company")
        self.assertEqual(
            purchase_contract.draft_install_problem(missing),
            "missing DocType Company",
        )

    def test_the_boundary_pin_covers_the_purchase_order_controller(self):
        self.assertEqual(
            purchase_contract.REVIEWED_SOURCE_SHA256["purchase_order"],
            "417c023169a2f65dfdee383e360395afa2e3fae38df434a69a495aef09aedb15",
        )
        self.assertEqual(
            purchase_contract.REVIEWED_SOURCE_SHA256["purchase_receipt"],
            "d8b926f62edac07ef08c8a3e0af15bbf7a85fea649f9f2ad94d394dbfcf58170",
        )


if __name__ == "__main__":
    unittest.main()
