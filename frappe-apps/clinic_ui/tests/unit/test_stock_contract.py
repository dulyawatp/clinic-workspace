"""Contract tests for the stock receive/transfer/adjust write family.

The contract decides every designed refusal before the fence: unknown
masters, missing or expired batches, insufficient stock (named line), missing
target warehouse, over-receipt against a referenced purchase order and the
restricted reconciliation account. It also carries the count-based EXPECTED
side-effect list the closed-world re-read compares against.
"""

import unittest

from clinic_ui.core import config, stock_contract, write_budgets

SITE = config.APPROVED_WRITE_SITE
SECRET = b"0123456789abcdef0123456789abcdef"
ACTOR = "pharmacist@example.test"
KEY = "stock-receive-key-0001"


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


def receive_payload(**overrides):
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


def transfer_payload(**overrides):
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


def adjust_payload(**overrides):
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


def pinned_snapshot():
    return {
        "versions": {
            "frappe": "16.54.0",
            "erpnext": "16.52.1",
            "healthcare": "16.6.1",
        },
        "source_sha256": dict(stock_contract.REVIEWED_SOURCE_SHA256),
        "controller_mro": dict(stock_contract.REVIEWED_MRO),
        "submittable": {
            "Purchase Receipt": 1,
            "Stock Entry": 1,
            "Stock Reconciliation": 1,
        },
        "enabled_server_scripts": 0,
        "doc_events": {
            doctype: () for doctype in stock_contract.GUARDED_DOCTYPES
        },
        "wildcard_doc_events": {
            event: tuple(handlers)
            for event, handlers in stock_contract.REVIEWED_WILDCARD_EVENTS.items()
        },
        "scheduler_stock_jobs": (),
        "class_overrides": {
            doctype: () for doctype in stock_contract.GUARDED_DOCTYPES
        },
        "fence_kinds": tuple(stock_contract.STOCK_KINDS),
    }


def texts(problems):
    return " | ".join(problem[2] for problem in problems)


class KindTests(unittest.TestCase):
    def test_the_family_owns_three_budgeted_kinds(self):
        self.assertEqual(
            stock_contract.STOCK_KINDS,
            ("stock_receive", "stock_transfer", "stock_adjust"),
        )
        for kind in stock_contract.STOCK_KINDS:
            with self.subTest(kind=kind):
                self.assertEqual(write_budgets.budget_for(kind), 1)
                self.assertEqual(stock_contract.budget(kind), 1)

    def test_an_unknown_kind_fails_closed(self):
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.budget("stock_issue")
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.require_kind(None)


class BoundaryTests(unittest.TestCase):
    def test_the_pinned_snapshot_passes(self):
        self.assertIsNone(stock_contract.boundary_problem(pinned_snapshot()))

    def test_every_reviewed_dimension_is_pinned(self):
        drifts = {
            "versions": lambda snap: snap["versions"].update({"erpnext": "16.53.0"}),
            "source": lambda snap: snap["source_sha256"].update(
                {"stock_entry": "0" * 64}
            ),
            "mro": lambda snap: snap["controller_mro"].update(
                {"Stock Entry": ("frappe.model.document.Document",)}
            ),
            "submittable": lambda snap: snap["submittable"].update(
                {"Purchase Receipt": 0}
            ),
            "server scripts": lambda snap: snap.update(
                {"enabled_server_scripts": 1}
            ),
            "doc events": lambda snap: snap["doc_events"].update(
                {"Stock Entry": ("app.hooks.patch",)}
            ),
            "wildcard": lambda snap: snap["wildcard_doc_events"].update(
                {"validate": ("app.hooks.check",)}
            ),
            "scheduler": lambda snap: snap.update(
                {"scheduler_stock_jobs": ("app.tasks.expire",)}
            ),
            "overrides": lambda snap: snap["class_overrides"].update(
                {"Stock Entry": ("app.overrides.Entry",)}
            ),
            "fence kinds": lambda snap: snap.update({"fence_kinds": ()}),
        }
        for label, mutate in drifts.items():
            with self.subTest(label=label):
                snapshot = pinned_snapshot()
                mutate(snapshot)
                self.assertIsNotNone(stock_contract.boundary_problem(snapshot))

    def test_an_unreadable_snapshot_fails_closed(self):
        for snapshot in (None, {}, {"versions": {}}):
            with self.subTest(snapshot=snapshot):
                self.assertIsNotNone(stock_contract.boundary_problem(snapshot))


class InstallTests(unittest.TestCase):
    def installed(self):
        return {
            doctype: set(fields)
            for doctype, fields in stock_contract.INSTALLED_CANDIDATES.items()
        }

    def test_a_complete_install_passes(self):
        self.assertIsNone(stock_contract.install_problem(self.installed()))

    def test_a_missing_doctype_or_field_is_named(self):
        missing_doctype = self.installed()
        missing_doctype.pop("Stock Reconciliation")
        self.assertIn(
            "Stock Reconciliation",
            stock_contract.install_problem(missing_doctype),
        )

        missing_field = self.installed()
        missing_field["Stock Entry Detail"].discard("t_warehouse")
        self.assertIn(
            "Stock Entry Detail.t_warehouse",
            stock_contract.install_problem(missing_field),
        )


class ValidationTests(unittest.TestCase):
    def test_receive_accepts_the_designed_shape(self):
        self.assertEqual(
            stock_contract.validate_receive(receive_payload())["qty"], 4.0
        )

    def test_receive_is_strict(self):
        cases = (
            {},
            {**receive_payload(), "supplier": ""},
            {**receive_payload(), "qty": 0},
            {**receive_payload(), "qty": -1},
            {**receive_payload(), "qty": "4"},
            {**receive_payload(), "docstatus": 1},
            {**receive_payload(), "operation_key": "short"},
        )
        for case in cases:
            with self.subTest(case=case):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_receive(case)

    def test_transfer_requires_a_distinct_named_target(self):
        self.assertEqual(
            stock_contract.validate_transfer(transfer_payload())[
                "target_warehouse"
            ],
            "WH-2",
        )
        for case in (
            {k: v for k, v in transfer_payload().items() if k != "target_warehouse"},
            {**transfer_payload(), "target_warehouse": ""},
        ):
            with self.subTest(case=case):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_transfer(case)

    def test_adjust_requires_a_reason_and_allows_zero(self):
        payload = stock_contract.validate_adjust(adjust_payload(qty=0))
        self.assertEqual(payload["qty"], 0.0)
        self.assertEqual(payload["reason"], "Count discrepancy")
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.validate_adjust(adjust_payload(reason=""))
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.validate_adjust(adjust_payload(reason="   "))
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.validate_adjust(adjust_payload(qty=-1))
        with self.assertRaises(stock_contract.StockContractError):
            stock_contract.validate_adjust(adjust_payload(reason="x" * 281))


class ReceiveProblemTests(unittest.TestCase):
    def test_a_valid_receive_has_no_problem(self):
        self.assertEqual(
            stock_contract.receive_problems(detail(), receive_payload()), ()
        )

    def test_unknown_masters_are_named_lines(self):
        cases = (
            (
                detail(item=item(exists=False)),
                "does not exist",
            ),
            (detail(item=item(is_stock_item=False)), "not a stock item"),
            (detail(supplier_exists=False), "SUP-1"),
            (detail(warehouse={"exists": False}), "WH-1"),
        )
        for values, expected in cases:
            with self.subTest(expected=expected):
                problems = stock_contract.receive_problems(
                    values, receive_payload()
                )
                self.assertTrue(problems)
                self.assertEqual(problems[0][0], stock_contract.HTTP_REFUSED)
                self.assertIn(expected, texts(problems))

    def test_a_batch_item_needs_a_valid_batch(self):
        missing = stock_contract.receive_problems(
            detail(batch=None),
            {
                key: value
                for key, value in receive_payload().items()
                if key != "batch_id"
            },
        )
        self.assertIn("batch", texts(missing))

        foreign = stock_contract.receive_problems(
            detail(batch={**detail()["batch"], "item": "ITEM-9"}),
            receive_payload(),
        )
        self.assertIn("BATCH-1", texts(foreign))

        expired = stock_contract.receive_problems(
            detail(batch={**detail()["batch"], "expiry_date": "2026-01-01"}),
            receive_payload(),
        )
        self.assertIn("expired", texts(expired))

    def test_a_new_batch_needs_an_expiry_for_an_expiry_item(self):
        problems = stock_contract.receive_problems(
            detail(batch=None),
            receive_payload(batch_id="BATCH-NEW"),
        )
        self.assertIn("expiry", texts(problems))

        ok = stock_contract.receive_problems(
            detail(batch=None),
            receive_payload(batch_id="BATCH-NEW", batch_expiry="2027-06-30"),
        )
        self.assertEqual(ok, ())

    def test_over_receipt_against_a_reference_is_refused(self):
        problems = stock_contract.receive_problems(
            detail(po_pending_qty=2.0),
            receive_payload(purchase_order="PO-1", qty=4.0),
        )
        self.assertIn(stock_contract.OVERRECEIPT_MESSAGE, texts(problems))

        unknown_po = stock_contract.receive_problems(
            detail(po_pending_qty=None),
            receive_payload(purchase_order="PO-9"),
        )
        self.assertIn("PO-9", texts(unknown_po))

    def test_without_a_reference_no_pending_quantity_is_required(self):
        self.assertEqual(
            stock_contract.receive_problems(
                detail(po_pending_qty=None), receive_payload()
            ),
            (),
        )


class TransferProblemTests(unittest.TestCase):
    def test_a_valid_transfer_has_no_problem(self):
        self.assertEqual(
            stock_contract.transfer_problems(detail(), transfer_payload()), ()
        )

    def test_insufficient_stock_names_the_line(self):
        problems = stock_contract.transfer_problems(
            detail(available_qty=1.0), transfer_payload()
        )
        self.assertIn(stock_contract.INSUFFICIENT_STOCK_MESSAGE, texts(problems))
        self.assertIn("ITEM-1", texts(problems))
        self.assertIn("WH-1", texts(problems))

    def test_insufficient_batch_stock_uses_the_batch_message(self):
        problems = stock_contract.transfer_problems(
            detail(batch={**detail()["batch"], "batch_qty": 1.0}),
            transfer_payload(),
        )
        self.assertIn(
            stock_contract.INSUFFICIENT_BATCH_MESSAGE, texts(problems)
        )
        self.assertIn("BATCH-1", texts(problems))

    def test_a_missing_or_equal_target_is_refused(self):
        missing = stock_contract.transfer_problems(
            detail(target_warehouse={"exists": False}), transfer_payload()
        )
        self.assertIn("WH-2", texts(missing))

        same = stock_contract.transfer_problems(
            detail(), transfer_payload(target_warehouse="WH-1")
        )
        self.assertIn("differ", texts(same))

    def test_an_expired_batch_never_transfers(self):
        problems = stock_contract.transfer_problems(
            detail(batch={**detail()["batch"], "expiry_date": "2026-01-01"}),
            transfer_payload(),
        )
        self.assertIn("expired", texts(problems))


class AdjustProblemTests(unittest.TestCase):
    def test_a_valid_adjust_has_no_problem(self):
        self.assertEqual(
            stock_contract.adjust_problems(detail(), adjust_payload()), ()
        )

    def test_a_company_without_the_adjustment_account_is_refused(self):
        problems = stock_contract.adjust_problems(
            detail(stock_adjustment_account=None), adjust_payload()
        )
        self.assertIn("stock adjustment account", texts(problems))

    def test_a_batch_item_needs_its_batch(self):
        problems = stock_contract.adjust_problems(
            detail(batch=None),
            {
                key: value
                for key, value in adjust_payload().items()
                if key != "batch_id"
            },
        )
        self.assertIn("batch", texts(problems))


class ExpectedSideEffectTests(unittest.TestCase):
    def test_receive_expects_one_entry_and_a_positive_bin_delta(self):
        expected = stock_contract.expected_receive(detail(), receive_payload())
        self.assertEqual(expected["sle_rows"], 1)
        self.assertEqual(expected["bin_mode"], "delta")
        self.assertEqual(expected["bins"], {"WH-1": 4.0})
        self.assertEqual(expected["gl_rows_max"], 2)

    def test_transfer_expects_two_entries_and_mirrored_bin_deltas(self):
        expected = stock_contract.expected_transfer(detail(), transfer_payload())
        self.assertEqual(expected["sle_rows"], 2)
        self.assertEqual(expected["bin_mode"], "delta")
        self.assertEqual(expected["bins"], {"WH-1": -4.0, "WH-2": 4.0})
        self.assertEqual(expected["gl_rows_max"], 4)

    def test_adjust_sets_the_counted_bin(self):
        expected = stock_contract.expected_adjust(detail(), adjust_payload())
        self.assertEqual(expected["sle_rows"], 1)
        self.assertEqual(expected["bin_mode"], "set")
        self.assertEqual(expected["bins"], {"WH-1": 3.0})
        self.assertEqual(expected["gl_rows_max"], 2)

    def facts(self, **overrides):
        values = {
            "docstatus": 1,
            "doctype": "Purchase Receipt",
            "sle_rows": 1,
            "gl_rows": 2,
            "bins": {"WH-1": 4.0},
        }
        values.update(overrides)
        return values

    def test_a_matching_reread_passes(self):
        expected = stock_contract.expected_receive(detail(), receive_payload())
        self.assertIsNone(
            stock_contract.side_effect_problem(self.facts(), expected)
        )

    def test_no_gl_rows_is_a_reviewed_outcome(self):
        expected = stock_contract.expected_receive(detail(), receive_payload())
        self.assertIsNone(
            stock_contract.side_effect_problem(self.facts(gl_rows=0), expected)
        )

    def test_every_drift_is_refused(self):
        expected = stock_contract.expected_receive(detail(), receive_payload())
        drifts = (
            {"docstatus": 0},
            {"doctype": "Stock Entry"},
            {"sle_rows": 2},
            {"gl_rows": 6},
            {"bins": {"WH-1": 9.0}},
            {"bins": {}},
        )
        for drift in drifts:
            with self.subTest(drift=drift):
                self.assertIsNotNone(
                    stock_contract.side_effect_problem(
                        self.facts(**drift), expected
                    )
                )

    def test_transfer_checks_both_sides(self):
        expected = stock_contract.expected_transfer(detail(), transfer_payload())
        facts = self.facts(
            doctype="Stock Entry",
            sle_rows=2,
            gl_rows=0,
            bins={"WH-1": -4.0, "WH-2": 4.0},
        )
        self.assertIsNone(stock_contract.side_effect_problem(facts, expected))
        self.assertIsNotNone(
            stock_contract.side_effect_problem(
                {**facts, "bins": {"WH-1": -4.0, "WH-2": 1.0}}, expected
            )
        )

    def test_adjust_compares_the_counted_quantity(self):
        expected = stock_contract.expected_adjust(detail(), adjust_payload())
        facts = self.facts(
            doctype="Stock Reconciliation",
            sle_rows=1,
            gl_rows=2,
            bins={"WH-1": 3.0},
        )
        self.assertIsNone(stock_contract.side_effect_problem(facts, expected))
        self.assertIsNotNone(
            stock_contract.side_effect_problem(
                {**facts, "bins": {"WH-1": 4.0}}, expected
            )
        )


class PreparedBodyTests(unittest.TestCase):
    def prepared_receive(self, **overrides):
        values = {
            "item_code": "ITEM-1",
            "qty": 4.0,
            "uom": "Unit",
            "warehouse": "WH-1",
            "supplier": "SUP-1",
            "purchase_order": None,
            "po_item": None,
            "batch_no": "BATCH-1",
            "batch_expiry": None,
            "create_batch": False,
            "posting_date": "2026-11-16",
            "set_posting_time": True,
            "company": "SIT Synthetic Company USD",
        }
        values.update(overrides)
        return values

    def prepared_transfer(self, **overrides):
        values = {
            "item_code": "ITEM-1",
            "qty": 4.0,
            "uom": "Unit",
            "source_warehouse": "WH-1",
            "target_warehouse": "WH-2",
            "batch_no": "BATCH-1",
            "create_batch": False,
            "posting_date": "2026-11-16",
            "set_posting_time": True,
            "company": "SIT Synthetic Company USD",
        }
        values.update(overrides)
        return values

    def prepared_adjust(self, **overrides):
        values = {
            "item_code": "ITEM-1",
            "qty": 3.0,
            "uom": "Unit",
            "warehouse": "WH-1",
            "batch_no": "BATCH-1",
            "create_batch": False,
            "reason": "Count discrepancy",
            "expense_account": "Stock Adjustment - SITCO",
            "posting_date": "2026-11-16",
            "set_posting_time": True,
            "company": "SIT Synthetic Company USD",
        }
        values.update(overrides)
        return values

    def test_each_prepared_body_pins_the_server_company_and_posting(self):
        for kind, prepared in (
            ("stock_receive", self.prepared_receive()),
            ("stock_transfer", self.prepared_transfer()),
            ("stock_adjust", self.prepared_adjust()),
        ):
            with self.subTest(kind=kind):
                self.assertIsNone(
                    stock_contract.prepared_problem(kind, prepared)
                )

    def test_a_missing_company_or_unpinned_posting_is_a_problem(self):
        for drift in (
            {"company": None},
            {"company": ""},
            {"posting_date": None},
            {"set_posting_time": False},
            {"uom": ""},
        ):
            with self.subTest(drift=drift):
                self.assertIsNotNone(
                    stock_contract.prepared_problem(
                        "stock_receive", self.prepared_receive(**drift)
                    )
                )

    def test_each_kind_checks_its_own_route(self):
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_transfer",
                self.prepared_transfer(target_warehouse="WH-1"),
            )
        )
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_adjust", self.prepared_adjust(reason="")
            )
        )
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_adjust", self.prepared_adjust(expense_account=None)
            )
        )
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_receive", self.prepared_receive(supplier="")
            )
        )
        self.assertIsNotNone(
            stock_contract.prepared_problem("stock_receive", None)
        )

    def test_adjust_accepts_zero_but_receive_and_transfer_do_not(self):
        self.assertIsNone(
            stock_contract.prepared_problem(
                "stock_adjust", self.prepared_adjust(qty=0.0)
            )
        )
        for kind, prepared in (
            ("stock_receive", self.prepared_receive(qty=0.0)),
            ("stock_transfer", self.prepared_transfer(qty=0.0)),
        ):
            with self.subTest(kind=kind):
                self.assertIsNotNone(
                    stock_contract.prepared_problem(kind, prepared)
                )


class ReceiptTests(unittest.TestCase):
    def test_a_receipt_round_trips_with_its_reason(self):
        receipt = stock_contract.encode_receipt(
            kind="stock_adjust",
            reference="SR-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=3.0,
            reason="Count discrepancy",
        )
        decoded = stock_contract.decode_receipt(receipt)
        self.assertEqual(decoded.kind, "stock_adjust")
        self.assertEqual(decoded.reference, "SR-1")
        self.assertEqual(decoded.reason, "Count discrepancy")

    def test_an_unknown_or_tampered_receipt_is_refused(self):
        receipt = stock_contract.encode_receipt(
            kind="stock_receive",
            reference="PR-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=4.0,
        )
        self.assertNotIn("native", receipt)
        for candidate in (None, "", "{}", receipt[:-1]):
            with self.subTest(candidate=candidate):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.decode_receipt(candidate)

    def test_the_operation_digest_is_stable_and_bound(self):
        fields = {"item_code": "ITEM-1", "qty": "4.000000", "warehouse": "WH-1"}
        first = stock_contract.operation_digest(
            SECRET,
            kind="stock_receive",
            operation_key=KEY,
            fields=fields,
        )
        second = stock_contract.operation_digest(
            SECRET,
            kind="stock_receive",
            operation_key=KEY,
            fields=dict(reversed(list(fields.items()))),
        )
        self.assertEqual(first, second)
        other = stock_contract.operation_digest(
            SECRET,
            kind="stock_receive",
            operation_key=KEY,
            fields={**fields, "warehouse": "WH-2"},
        )
        self.assertNotEqual(first, other)

    def test_the_ack_token_binds_the_receipt(self):
        receipt = stock_contract.encode_receipt(
            kind="stock_transfer",
            reference="SE-1",
            item_code="ITEM-1",
            warehouse="WH-1",
            qty=4.0,
        )
        token = stock_contract.ack_token(
            SECRET, SITE, ACTOR, "stock_transfer", KEY, receipt
        )
        self.assertRegex(token, r"^[0-9a-f]{64}$")
        self.assertFalse(
            stock_contract.is_valid_ack_token(token.lower()[:-1])
        )
        self.assertTrue(stock_contract.is_valid_ack_token(token))

    def test_an_ack_body_is_strict(self):
        body = {"operation_key": KEY, "kind": "stock_receive", "ack_token": "a" * 64}
        self.assertEqual(
            stock_contract.validate_ack(body)["kind"], "stock_receive"
        )
        for candidate in (
            {**body, "extra": 1},
            {**body, "kind": "stock_issue"},
            {**body, "ack_token": "short"},
        ):
            with self.subTest(candidate=candidate):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_ack(candidate)


if __name__ == "__main__":
    unittest.main()
