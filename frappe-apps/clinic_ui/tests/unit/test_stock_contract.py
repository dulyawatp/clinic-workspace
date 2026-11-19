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
        "company": "SIT Synthetic Company USD",
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
        "purpose": "Stock Reconciliation",
        "company": "SIT Synthetic Company USD",
        "item_code": "ITEM-1",
        "warehouse": "WH-1",
        "qty": 3.0,
        "valuation_rate": 12.5,
        "expense_account": "Temporary Opening - SITCO",
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
            doctype: {
                event: tuple(handlers)
                for event, handlers in stock_contract.REVIEWED_DOC_EVENTS[
                    doctype
                ].items()
            }
            for doctype in stock_contract.GUARDED_DOCTYPES
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
            "doc events": lambda snap: snap["doc_events"]["Stock Entry"].update(
                {"before_save": ("app.hooks.patch",)}
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

    def test_erpnext_builtin_doc_events_are_reviewed(self):
        entry = (
            "erpnext.stock.doctype.material_request.material_request."
            "update_completed_and_requested_qty"
        )
        self.assertEqual(
            stock_contract.REVIEWED_DOC_EVENTS["Stock Entry"],
            {"on_cancel": (entry,), "on_submit": (entry,)},
        )
        self.assertIsNone(stock_contract.boundary_problem(pinned_snapshot()))

    def test_foreign_doc_events_still_fail_closed(self):
        snapshot = pinned_snapshot()
        snapshot["doc_events"]["Stock Entry"]["on_submit"] = (
            "erpnext.stock.doctype.material_request.material_request."
            "update_completed_and_requested_qty",
            "clinic_ui.hooks.sneaky",
        )
        self.assertEqual(
            stock_contract.boundary_problem(snapshot),
            "unreviewed doc_events for Stock Entry",
        )
        snapshot = pinned_snapshot()
        snapshot["doc_events"]["Stock Entry"]["before_save"] = (
            "app.hooks.patch",
        )
        self.assertEqual(
            stock_contract.boundary_problem(snapshot),
            "unreviewed doc_events for Stock Entry",
        )

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

    def test_transfer_carries_the_native_material_transfer_fields(self):
        payload = stock_contract.validate_transfer(transfer_payload())

        self.assertEqual(
            payload,
            {
                "kind": "stock_transfer",
                "operation_key": None,
                "company": "SIT Synthetic Company USD",
                "item_code": "ITEM-1",
                "qty": 4.0,
                "source_warehouse": "WH-1",
                "target_warehouse": "WH-2",
                "batch_id": "BATCH-1",
            },
        )

    def test_transfer_requires_the_document_identity_fields(self):
        for missing in (
            "company",
            "item_code",
            "qty",
            "source_warehouse",
            "target_warehouse",
        ):
            with self.subTest(missing=missing):
                case = {
                    key: value
                    for key, value in transfer_payload().items()
                    if key != missing
                }
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_transfer(case)

    def test_transfer_leaves_the_batch_and_the_operation_key_optional(self):
        body = transfer_payload()
        for key in ("batch_id",):
            body.pop(key)
        payload = stock_contract.validate_transfer(body)
        self.assertIsNone(payload["batch_id"])
        self.assertIsNone(payload["operation_key"])

        blank = stock_contract.validate_transfer(transfer_payload(batch_id=""))
        self.assertIsNone(blank["batch_id"])

        with_key = stock_contract.validate_transfer(
            transfer_payload(operation_key=KEY)
        )
        self.assertEqual(with_key["operation_key"], KEY)

    def test_transfer_rejects_unknown_and_malformed_input(self):
        for drift in (
            {"reason": "Restock the front shelf"},
            {"operation_key": "short"},
            {"target_warehouse": ""},
            {"source_warehouse": " "},
            {"company": ""},
            {"qty": 0},
            {"qty": -1},
            {"qty": "4"},
        ):
            with self.subTest(drift=drift):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_transfer(transfer_payload(**drift))

    def test_adjust_requires_only_the_document_identity_fields(self):
        payload = stock_contract.validate_adjust(adjust_payload(qty=0))
        self.assertEqual(payload["qty"], 0.0)
        self.assertEqual(payload["purpose"], "Stock Reconciliation")
        self.assertEqual(payload["valuation_rate"], 12.5)
        for missing in ("purpose", "company", "item_code", "warehouse"):
            with self.subTest(missing=missing):
                case = {
                    key: value
                    for key, value in adjust_payload().items()
                    if key != missing
                }
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_adjust(case)

    def test_adjust_leaves_optional_native_fields_to_frappe(self):
        body = adjust_payload()
        for key in ("qty", "valuation_rate", "expense_account", "batch_id"):
            body.pop(key)
        payload = stock_contract.validate_adjust(body)
        self.assertIsNone(payload["qty"])
        self.assertIsNone(payload["valuation_rate"])
        self.assertIsNone(payload["expense_account"])
        self.assertIsNone(payload["batch_id"])

        nulls = stock_contract.validate_adjust(
            adjust_payload(qty=None, valuation_rate=None, expense_account=None)
        )
        self.assertIsNone(nulls["qty"])
        self.assertIsNone(nulls["valuation_rate"])
        self.assertIsNone(nulls["expense_account"])

        self.assertIsNone(
            stock_contract.validate_adjust(
                adjust_payload(expense_account="")
            )["expense_account"]
        )

    def test_adjust_accepts_each_native_purpose(self):
        for purpose in (
            "Stock Reconciliation",
            "Opening Stock",
            "Adjustment Entry",
        ):
            with self.subTest(purpose=purpose):
                payload = stock_contract.validate_adjust(
                    adjust_payload(purpose=purpose)
                )
                self.assertEqual(payload["purpose"], purpose)

    def test_adjust_rejects_unknown_and_malformed_input(self):
        for drift in (
            {"reason": "Count discrepancy"},
            {"purpose": ""},
            {"qty": -1},
            {"valuation_rate": -1},
            {"qty": "three"},
            {"valuation_rate": "twelve"},
            {"company": ""},
        ):
            with self.subTest(drift=drift):
                with self.assertRaises(stock_contract.StockContractError):
                    stock_contract.validate_adjust(adjust_payload(**drift))


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


class ExpectedSideEffectTests(unittest.TestCase):
    def test_receive_expects_one_entry_and_a_positive_bin_delta(self):
        expected = stock_contract.expected_receive(detail(), receive_payload())
        self.assertEqual(expected["sle_rows"], 1)
        self.assertEqual(expected["bin_mode"], "delta")
        self.assertEqual(expected["bins"], {"WH-1": 4.0})
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

    def test_the_prepared_receive_body_pins_the_server_company_and_posting(self):
        self.assertIsNone(
            stock_contract.prepared_problem("stock_receive", self.prepared_receive())
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

    def test_the_prepared_body_checks_its_own_route(self):
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_receive", self.prepared_receive(supplier="")
            )
        )
        self.assertIsNotNone(
            stock_contract.prepared_problem("stock_receive", None)
        )

    def test_the_prepared_body_does_not_accept_zero(self):
        self.assertIsNotNone(
            stock_contract.prepared_problem(
                "stock_receive", self.prepared_receive(qty=0.0)
            )
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
