"""Contract tests for the Clinical Procedure completion family.

These pin the closed-world decisions that must all happen *before* the fence
claim: the installed preflight, the pinned boundary (driven method name + source
sha), the exactly-once target rules, the mandatory price list, per-row batch and
stock validity, the EXPECTED side-effect list and the codecs.
"""

import unittest

from clinic_ui.core import config, procedure_contract as contract
from clinic_ui.core import procedure_write, write_fence


def meta_fields(**overrides):
    fields = {
        doctype: set(names) for doctype, names in contract.REQUIRED_FIELDS.items()
    }
    for doctype, names in overrides.items():
        fields[doctype] = names if isinstance(names, set) else set(names)
    return fields


def snapshot(**overrides):
    values = {
        "versions": dict(contract.REVIEWED_VERSIONS),
        "source_sha256": {
            "clinical_procedure": contract.PROCEDURE_SOURCE_SHA256,
            "service_request": contract.SERVICE_REQUEST_SOURCE_SHA256,
        },
        "complete_method": contract.COMPLETE_METHOD,
        "complete_callable": 1,
        "procedure_submittable": 1,
        "stock_entry_submittable": 1,
        "enabled_server_scripts": 0,
        "doc_events": {
            doctype: {
                event: tuple(handlers)
                for event, handlers in contract.REVIEWED_DOC_EVENTS[
                    doctype
                ].items()
            }
            for doctype in (
                contract.PROCEDURE_DOCTYPE,
                contract.STOCK_ENTRY_DOCTYPE,
            )
        },
        "wildcard_doc_events": {
            event: tuple(handlers)
            for event, handlers in contract.REVIEWED_WILDCARD_EVENTS.items()
        },
        "scheduler_procedure_jobs": (),
        "class_overrides": {contract.PROCEDURE_DOCTYPE: ()},
    }
    values.update(overrides)
    return values


def row(**overrides):
    values = {
        "item_code": "ITEM-1",
        "qty": 1.0,
        "uom": "Unit",
        "batch_no": "BATCH-1",
        "batch_item": "ITEM-1",
        "batch_qty": 10.0,
        "batch_expiry": "2027-01-01",
        "available_qty": 10.0,
    }
    values.update(overrides)
    return values


def detail(**overrides):
    values = {
        "patient": "PAT-1",
        "docstatus": contract.FINAL_DOCSTATUS,
        "status": "In Progress",
        "consume_stock": True,
        "warehouse": "WH-1",
        "company": "COMP-1",
        "price_list": "Standard Selling",
        "customer": "CUST-1",
        "procedure_template": "TPL-1",
        "post_op_checklist": None,
        "service_request": None,
        "appointment": None,
        "modified": "2026-11-16 09:30:00.123456",
        "items": (row(),),
        "allow_negative_stock": False,
        "today": "2026-11-16",
    }
    values.update(overrides)
    return values


def facts(**overrides):
    values = {
        "stock_entry": "NATIVE-SE-1",
        "stock_entry_docstatus": 1,
        "stock_entry_type": contract.MATERIAL_ISSUE_TYPE,
        "stock_entry_company": "COMP-1",
        "procedure_company": "COMP-1",
        "status": contract.COMPLETED_STATUS,
        "actual_end_datetime": "2026-11-16 10:00:00",
        "stock_ledger_rows": 1,
        "gl_rows": 2,
        "bin_delta": -1.0,
        "bin_warehouse": "WH-1",
        "nursing_task_rows": 0,
    }
    values.update(overrides)
    return values


class InstallPreflightTests(unittest.TestCase):
    def test_a_complete_install_has_no_problem(self):
        self.assertIsNone(contract.install_problem(meta_fields()))

    def test_an_absent_doctype_is_a_problem(self):
        fields = meta_fields()
        del fields[contract.ITEM_DOCTYPE]
        self.assertEqual(
            contract.install_problem(fields),
            f"missing DocType {contract.ITEM_DOCTYPE}",
        )

    def test_a_missing_logical_field_is_a_problem(self):
        fields = meta_fields()
        fields[contract.PROCEDURE_DOCTYPE] = {"patient"}
        self.assertEqual(
            contract.install_problem(fields),
            f"missing field {contract.PROCEDURE_DOCTYPE}.procedure_template",
        )

    def test_unreadable_metadata_is_a_problem(self):
        self.assertEqual(
            contract.install_problem(None), "installed metadata unreadable"
        )

    def test_resolve_field_returns_the_installed_name(self):
        fields = meta_fields()
        self.assertEqual(
            contract.resolve_field(fields, contract.ITEM_DOCTYPE, "batch_no"),
            "batch_no",
        )
        self.assertIsNone(
            contract.resolve_field(fields, contract.ITEM_DOCTYPE, "unknown")
        )


class BoundaryTests(unittest.TestCase):
    def test_a_pinned_snapshot_has_no_problem(self):
        self.assertIsNone(contract.boundary_problem(snapshot()))

    def test_version_drift_is_refused(self):
        values = snapshot()
        values["versions"] = dict(values["versions"], healthcare="16.5.0")
        self.assertEqual(
            contract.boundary_problem(values), "versions drift for healthcare"
        )

    def test_source_drift_is_refused(self):
        values = snapshot()
        values["source_sha256"] = {"clinical_procedure": "0" * 64}
        self.assertEqual(
            contract.boundary_problem(values),
            "source drift for clinical_procedure",
        )

    def test_a_renamed_or_missing_method_is_refused(self):
        values = snapshot(complete_method="finish_procedure")
        self.assertEqual(contract.boundary_problem(values), "driven method drift")
        values = snapshot(complete_callable=0)
        self.assertEqual(
            contract.boundary_problem(values), "driven method is not callable"
        )

    def test_erpnext_builtin_doc_events_are_reviewed(self):
        entry = (
            "erpnext.stock.doctype.material_request.material_request."
            "update_completed_and_requested_qty"
        )
        self.assertEqual(
            contract.REVIEWED_DOC_EVENTS[contract.STOCK_ENTRY_DOCTYPE],
            {"on_cancel": (entry,), "on_submit": (entry,)},
        )
        self.assertIsNone(contract.boundary_problem(snapshot()))

    def test_unreviewed_doc_events_and_overrides_are_refused(self):
        values = snapshot()
        values["doc_events"] = {
            contract.PROCEDURE_DOCTYPE: {
                "after_save": ("healthcare.hooks.after_save",)
            },
            contract.STOCK_ENTRY_DOCTYPE: {},
        }
        self.assertEqual(
            contract.boundary_problem(values),
            f"unreviewed doc_events for {contract.PROCEDURE_DOCTYPE}",
        )
        values = snapshot()
        values["doc_events"][contract.STOCK_ENTRY_DOCTYPE]["on_submit"] = (
            "erpnext.stock.doctype.material_request.material_request."
            "update_completed_and_requested_qty",
            "clinic_ui.hooks.sneaky",
        )
        self.assertEqual(
            contract.boundary_problem(values),
            f"unreviewed doc_events for {contract.STOCK_ENTRY_DOCTYPE}",
        )
        values = snapshot(
            wildcard_doc_events={
                **snapshot()["wildcard_doc_events"],
                "after_insert": ("pkg.unreviewed",),
            }
        )
        self.assertEqual(
            contract.boundary_problem(values),
            "wildcard doc_events drift for after_insert",
        )
        values = snapshot(scheduler_procedure_jobs=("pkg.job",))
        self.assertEqual(
            contract.boundary_problem(values),
            "a scheduler job touches the procedure family",
        )
        values = snapshot(
            class_overrides={contract.PROCEDURE_DOCTYPE: ("pkg.Custom",)}
        )
        self.assertEqual(
            contract.boundary_problem(values),
            "Clinical Procedure class is overridden or extended",
        )

    def test_submittability_server_scripts_and_snapshot_shape_are_checked(self):
        self.assertEqual(
            contract.boundary_problem(snapshot(procedure_submittable=0)),
            "Clinical Procedure is not submittable",
        )
        self.assertEqual(
            contract.boundary_problem(snapshot(stock_entry_submittable=0)),
            "Stock Entry is not submittable",
        )
        self.assertEqual(
            contract.boundary_problem(snapshot(enabled_server_scripts=1)),
            "server scripts are enabled",
        )
        self.assertEqual(
            contract.boundary_problem(None), "boundary snapshot unreadable"
        )


class TargetTests(unittest.TestCase):
    def test_a_submitted_in_progress_procedure_is_completable(self):
        self.assertIsNone(contract.target_problem(detail()))

    def test_the_installed_source_pins_are_the_expected_shas(self):
        self.assertEqual(len(contract.PROCEDURE_SOURCE_SHA256), 64)
        self.assertEqual(len(contract.SERVICE_REQUEST_SOURCE_SHA256), 64)
        self.assertEqual(contract.COMPLETE_METHOD, "complete_procedure")

    def test_service_request_source_drift_is_refused(self):
        values = snapshot()
        values["source_sha256"] = dict(
            values["source_sha256"], service_request="0" * 64
        )
        self.assertEqual(
            contract.boundary_problem(values),
            "source drift for service_request",
        )

    def test_a_draft_procedure_is_refused(self):
        self.assertEqual(
            contract.target_problem(detail(docstatus=0, status="Draft")),
            "target procedure is not submitted",
        )

    def test_a_completed_procedure_is_refused_exactly_once(self):
        self.assertEqual(
            contract.target_problem(detail(status="Completed")),
            "target procedure is already completed",
        )

    def test_a_cancelled_procedure_posts_no_stock(self):
        self.assertEqual(
            contract.target_problem(
                detail(docstatus=contract.CANCELLED_DOCSTATUS)
            ),
            "target procedure is cancelled",
        )
        self.assertEqual(
            contract.target_problem(detail(status="Cancelled")),
            "target procedure is cancelled",
        )

    def test_a_procedure_without_a_patient_is_refused(self):
        self.assertEqual(
            contract.target_problem(detail(patient=None)),
            "target procedure has no patient",
        )


class CompletionProblemTests(unittest.TestCase):
    def test_a_valid_procedure_has_no_problem(self):
        self.assertEqual(contract.completion_problems(detail()), ())

    def test_a_missing_price_list_is_the_installed_message(self):
        problems = contract.completion_problems(detail(price_list=None))
        self.assertEqual(
            problems[0],
            (417, "price_list", contract.PRICE_LIST_MESSAGE),
        )
        self.assertEqual(contract.PRICE_LIST_MESSAGE, "Price List is mandatory")

    def test_a_missing_warehouse_company_or_customer_is_named(self):
        problems = contract.completion_problems(
            detail(warehouse=None, company=None, customer=None)
        )
        names = [problem[1] for problem in problems]
        self.assertEqual(names, ["warehouse", "company", "customer"])

    def test_an_invalid_row_is_named_per_line(self):
        problems = contract.completion_problems(
            detail(items=(row(item_code=None),))
        )
        self.assertEqual(problems[0][1], "items[0].item_code")
        problems = contract.completion_problems(detail(items=(row(qty=0),)))
        self.assertEqual(problems[0][1], "items[0].qty")
        problems = contract.completion_problems(detail(items=(row(uom=None),)))
        self.assertEqual(problems[0][1], "items[0].uom")

    def test_a_batch_must_exist_belong_and_not_be_expired(self):
        problems = contract.completion_problems(
            detail(items=(row(batch_item="OTHER-ITEM"),))
        )
        self.assertIn("does not belong", problems[0][2])
        problems = contract.completion_problems(
            detail(items=(row(batch_expiry="2026-01-01"),))
        )
        self.assertIn("is expired", problems[0][2])
        problems = contract.completion_problems(
            detail(items=(row(batch_qty=0.0),))
        )
        self.assertIn("insufficient stock", problems[0][2])

    def test_insufficient_stock_is_named_with_the_item(self):
        problems = contract.completion_problems(
            detail(items=(row(batch_no=None, available_qty=0.0),))
        )
        self.assertEqual(problems[0][1], "items[0].qty")
        self.assertIn("ITEM-1", problems[0][2])
        self.assertIn("insufficient stock", problems[0][2])

    def test_allow_negative_stock_skips_the_quantity_check(self):
        self.assertEqual(
            contract.completion_problems(
                detail(
                    allow_negative_stock=True,
                    items=(row(batch_no=None, available_qty=0.0),),
                )
            ),
            (),
        )

    def test_a_post_operative_checklist_is_refused(self):
        problems = contract.completion_problems(
            detail(post_op_checklist="POST-OP-1")
        )
        self.assertEqual(problems[0][1], "nursing_tasks")

    def test_a_non_consuming_procedure_needs_no_price_list_or_customer(self):
        self.assertEqual(
            contract.completion_problems(
                detail(
                    consume_stock=False,
                    price_list=None,
                    customer=None,
                    items=(),
                )
            ),
            (),
        )


class SideEffectTests(unittest.TestCase):
    def expected(self, **overrides):
        return contract.expected_side_effects(detail(**overrides))

    def test_the_expected_list_is_count_based(self):
        expected = self.expected(items=(row(), row(item_code="ITEM-2")))
        self.assertEqual(
            expected,
            {
                "consumes_stock": True,
                "item_rows": 2,
                "total_qty": 2.0,
                "warehouse": "WH-1",
            },
        )

    def test_a_non_consuming_procedure_expects_no_stock(self):
        expected = self.expected(consume_stock=False)
        self.assertEqual(expected["item_rows"], 0)
        self.assertFalse(expected["consumes_stock"])

    def test_a_pinned_postimage_has_no_problem(self):
        self.assertIsNone(
            contract.side_effect_problem(facts(), self.expected())
        )

    def test_a_missing_or_draft_stock_entry_is_refused(self):
        self.assertEqual(
            contract.side_effect_problem(
                facts(stock_entry=None), self.expected()
            ),
            "the driven method returned no Stock Entry",
        )
        self.assertEqual(
            contract.side_effect_problem(
                facts(stock_entry_docstatus=0), self.expected()
            ),
            "the Stock Entry is not submitted",
        )
        self.assertEqual(
            contract.side_effect_problem(
                facts(stock_entry_type="Material Receipt"), self.expected()
            ),
            "the Stock Entry is not a Material Issue",
        )

    def test_status_and_actual_end_are_required(self):
        self.assertEqual(
            contract.side_effect_problem(
                facts(status="In Progress"), self.expected()
            ),
            "procedure status is not Completed",
        )
        self.assertEqual(
            contract.side_effect_problem(
                facts(actual_end_datetime=None), self.expected()
            ),
            "procedure actual end is not recorded",
        )

    def test_ledger_bin_and_gl_deltas_must_match(self):
        self.assertEqual(
            contract.side_effect_problem(
                facts(stock_ledger_rows=2), self.expected()
            ),
            "stock ledger row count differs from the item rows",
        )
        self.assertEqual(
            contract.side_effect_problem(
                facts(bin_delta=0.0), self.expected()
            ),
            "bin movement differs from the consumed quantity",
        )
        self.assertEqual(
            contract.side_effect_problem(facts(gl_rows=3), self.expected()),
            "gl row count is outside the reviewed outcomes",
        )
        self.assertIsNone(
            contract.side_effect_problem(facts(gl_rows=0), self.expected())
        )

    def test_nursing_tasks_are_not_an_expected_effect(self):
        self.assertEqual(
            contract.side_effect_problem(
                facts(nursing_task_rows=1), self.expected()
            ),
            "nursing tasks were created",
        )

    def test_a_stock_entry_for_a_non_consuming_procedure_is_refused(self):
        self.assertEqual(
            contract.side_effect_problem(
                facts(), self.expected(consume_stock=False)
            ),
            "a Stock Entry was created for a non-consuming procedure",
        )


class BudgetAndCodecTests(unittest.TestCase):
    secret = "0123456789abcdef0123456789abcdef".encode()

    def test_budget_is_the_approved_single_unit(self):
        self.assertEqual(procedure_write.budget("procedure_complete"), 1)

    def test_alias_round_trip_is_actor_bound(self):
        alias = procedure_write.build_alias(
            self.secret, "marley.test", "doc@test", "proc-key-0001-abcdef"
        )
        self.assertTrue(alias.startswith("wsp1_"))
        self.assertEqual(
            procedure_write.parse_alias(
                self.secret, "marley.test", "doc@test", alias
            ),
            "proc-key-0001-abcdef",
        )
        self.assertIsNone(
            procedure_write.parse_alias(
                self.secret, "marley.test", "other@test", alias
            )
        )

    def test_receipt_round_trip_carries_the_stock_entry(self):
        receipt = procedure_write.encode_receipt(
            kind="procedure_complete",
            alias="wsp1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16,
            native_procedure_id="HLC-CPR-1",
            stock_entry="MAT-STE-1",
        )
        decoded = procedure_write.decode_receipt(receipt)
        self.assertEqual(decoded.native_procedure_id, "HLC-CPR-1")
        self.assertEqual(decoded.stock_entry, "MAT-STE-1")
        self.assertEqual(decoded.kind, "procedure_complete")

    def test_a_receipt_with_a_foreign_key_set_is_refused(self):
        with self.assertRaises(procedure_write.ProcedureWriteError):
            procedure_write.decode_receipt('{"codec": "procedure"}')

    def test_the_body_is_exactly_the_workspace_id_and_key(self):
        payload = procedure_write.validate_complete(
            {"procedure": "WS-PROC-1", "operation_key": "proc-key-0001-abcdef"}
        )
        self.assertEqual(payload["procedure"], "WS-PROC-1")
        for extra in ({"docstatus": 1}, {"status": "Completed"}, {"qty": 1}):
            with self.subTest(extra=extra):
                with self.assertRaises(procedure_write.ProcedureWriteError):
                    procedure_write.validate_complete(
                        {
                            "procedure": "WS-PROC-1",
                            "operation_key": "proc-key-0001-abcdef",
                            **extra,
                        }
                    )

    def test_a_reserved_operation_key_is_refused(self):
        for key in ("clinic-ui-aaaaaaaaaaaa", "wse1_" + "a" * 20):
            with self.subTest(key=key):
                with self.assertRaises(procedure_write.ProcedureWriteError):
                    procedure_write.validate_complete(
                        {"procedure": "WS-PROC-1", "operation_key": key}
                    )

    def test_ack_token_binds_the_receipt(self):
        receipt = procedure_write.encode_receipt(
            kind="procedure_complete",
            alias="wsp1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16,
            native_procedure_id="HLC-CPR-1",
            stock_entry="MAT-STE-1",
        )
        token = procedure_write.ack_token(
            self.secret,
            "marley.test",
            "doc@test",
            "procedure_complete",
            "proc-key-0001-abcdef",
            receipt,
        )
        self.assertTrue(procedure_write.is_valid_ack_token(token))
        self.assertEqual(
            procedure_write.validate_ack(
                {
                    "operation_key": "proc-key-0001-abcdef",
                    "kind": "procedure_complete",
                    "ack_token": token,
                }
            )["ack_token"],
            token,
        )

    # -- Amendment A1: the creation kind ---------------------------------

    def test_the_creation_budget_is_the_approved_single_unit(self):
        self.assertEqual(procedure_write.budget("procedure_create"), 1)
        self.assertEqual(
            set(procedure_write.BUDGETS),
            {"procedure_complete", "procedure_create"},
        )

    def test_a_creation_receipt_carries_the_service_request(self):
        receipt = procedure_write.encode_receipt(
            kind="procedure_create",
            alias="wsp1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16,
            native_procedure_id="HLC-CPR-1",
            service_request="HLC-SR-1",
        )
        decoded = procedure_write.decode_receipt(receipt)
        self.assertEqual(decoded.kind, "procedure_create")
        self.assertEqual(decoded.service_request, "HLC-SR-1")
        self.assertEqual(decoded.stock_entry, "")

    def test_a_completion_receipt_carries_no_service_request(self):
        receipt = procedure_write.encode_receipt(
            kind="procedure_complete",
            alias="wsp1_" + "a" * 32 + "_" + "b" * 16 + "_" + "c" * 16,
            native_procedure_id="HLC-CPR-1",
            stock_entry="MAT-STE-1",
        )
        self.assertEqual(
            procedure_write.decode_receipt(receipt).service_request, ""
        )

    def test_the_creation_body_is_exactly_the_request_and_key(self):
        payload = procedure_write.validate_create(
            {"service_request": "HLC-SR-1", "operation_key": "proc-key-0001-abcdef"}
        )
        self.assertEqual(payload["kind"], "procedure_create")
        self.assertEqual(payload["service_request"], "HLC-SR-1")
        for extra in (
            {"procedure_template": "TPL-1"},
            {"patient": "PAT-1"},
            {"docstatus": 1},
        ):
            with self.subTest(extra=extra):
                with self.assertRaises(procedure_write.ProcedureWriteError):
                    procedure_write.validate_create(
                        {
                            "service_request": "HLC-SR-1",
                            "operation_key": "proc-key-0001-abcdef",
                            **extra,
                        }
                    )

    def test_a_creation_body_without_the_request_is_refused(self):
        with self.assertRaises(procedure_write.ProcedureWriteError):
            procedure_write.validate_create(
                {"operation_key": "proc-key-0001-abcdef"}
            )

    def test_both_kinds_are_procedure_kinds(self):
        self.assertEqual(
            contract.PROCEDURE_KINDS,
            ("procedure_complete", "procedure_create"),
        )
        self.assertIn("procedure_create", write_fence.PROCEDURE_OPERATION_KINDS)


def request(**overrides):
    """The server-read creation decisions for one submitted request."""

    values = {
        "exists": True,
        "docstatus": contract.FINAL_DOCSTATUS,
        "template_dt": contract.PROCEDURE_TEMPLATE_DOCTYPE,
        "template_dn": "TPL-1",
        "patient": "PAT-1",
        "procedure_template": "TPL-1",
        "procedure_exists": False,
        "consume_stock": False,
    }
    values.update(overrides)
    return values


def create_facts(**overrides):
    values = {
        "name": "HLC-CPR-1",
        "patient": "PAT-1",
        "procedure_template": "TPL-1",
        "service_request": "HLC-SR-1",
        "docstatus": contract.FINAL_DOCSTATUS,
        "status": contract.PENDING_STATUS,
    }
    values.update(overrides)
    return values


def create_expected(**overrides):
    values = {
        "procedure_id": "HLC-CPR-1",
        "patient": "PAT-1",
        "procedure_template": "TPL-1",
        "service_request": "HLC-SR-1",
    }
    values.update(overrides)
    return values


class CreationTargetTests(unittest.TestCase):
    """Amendment A1: every creation decision is pre-fence and named."""

    def test_a_submitted_procedure_request_is_creatable(self):
        self.assertIsNone(contract.service_request_problem(request()))

    def test_an_absent_request_is_refused(self):
        self.assertEqual(
            contract.service_request_problem(request(exists=False)),
            "service request not found",
        )

    def test_an_unsubmitted_request_is_refused(self):
        self.assertEqual(
            contract.service_request_problem(request(docstatus=0)),
            "service request is not submitted",
        )

    def test_a_non_procedure_request_is_refused(self):
        self.assertEqual(
            contract.service_request_problem(
                request(
                    template_dt="Lab Test Template", procedure_template=None
                )
            ),
            "service request is not a procedure request",
        )

    def test_a_request_without_patient_or_template_is_refused(self):
        self.assertEqual(
            contract.service_request_problem(request(patient=None)),
            "service request has no patient",
        )
        self.assertEqual(
            contract.service_request_problem(request(procedure_template=None)),
            "service request has no procedure template",
        )

    def test_an_existing_procedure_is_the_exactly_once_refusal(self):
        self.assertEqual(
            contract.service_request_problem(request(procedure_exists=True)),
            "a procedure already exists for the service request",
        )

    def test_a_stock_consuming_template_is_refused(self):
        self.assertEqual(
            contract.service_request_problem(request(consume_stock=True)),
            "the procedure template consumes stock, which this creation path "
            "does not seed",
        )

    def test_unreadable_decisions_are_refused(self):
        self.assertEqual(
            contract.service_request_problem(None),
            "service request state unreadable",
        )

    def test_the_pinned_field_map_mirrors_the_installed_helper(self):
        mapping = dict(contract.CREATE_FIELD_MAP)
        self.assertEqual(mapping["patient_gender"], "patient_sex")
        self.assertEqual(mapping["patient_age_data"], "patient_age")
        self.assertEqual(mapping["occurrence_date"], "start_date")
        self.assertEqual(mapping["occurrence_time"], "start_time")
        self.assertEqual(len(contract.SERVICE_REQUEST_SOURCE_SHA256), 64)

    def test_a_pinned_creation_re_read_has_no_problem(self):
        self.assertIsNone(
            contract.create_facts_problem(create_facts(), create_expected())
        )

    def test_the_creation_re_read_is_closed_world(self):
        self.assertEqual(
            contract.create_facts_problem(
                create_facts(name="HLC-CPR-9"), create_expected()
            ),
            "created procedure differs from the receipt",
        )
        self.assertEqual(
            contract.create_facts_problem(
                create_facts(patient="PAT-2"), create_expected()
            ),
            "created procedure patient differs from the service request",
        )
        self.assertEqual(
            contract.create_facts_problem(
                create_facts(procedure_template="TPL-2"), create_expected()
            ),
            "created procedure template differs from the service request",
        )
        self.assertEqual(
            contract.create_facts_problem(
                create_facts(service_request="HLC-SR-9"), create_expected()
            ),
            "created procedure is not linked to the service request",
        )

    def test_a_created_procedure_must_be_submitted_and_not_terminal(self):
        self.assertEqual(
            contract.create_facts_problem(
                create_facts(docstatus=0), create_expected()
            ),
            "created procedure is not submitted",
        )
        for status in (contract.COMPLETED_STATUS, contract.CANCELLED_STATUS):
            with self.subTest(status=status):
                self.assertEqual(
                    contract.create_facts_problem(
                        create_facts(status=status), create_expected()
                    ),
                    "created procedure is already in a terminal state",
                )

    def test_unreadable_creation_facts_are_refused(self):
        self.assertEqual(
            contract.create_facts_problem(None, create_expected()),
            "post-create procedure facts unreadable",
        )


class SettingsGateTests(unittest.TestCase):
    def settings(self, **overrides):
        values = {
            "clinic_ui_enabled": 1,
            "clinic_ui_synthetic_site": config.APPROVED_WRITE_SITE,
            "clinic_ui_epoch_secret": "0123456789abcdef0123456789abcdef",
            "clinic_ui_role_map": {"DOC": ["Physician"]},
            "clinic_ui_synthetic_patients": [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"}
            ],
            "clinic_ui_native_procedure_write_enabled": 1,
            "clinic_ui_native_procedure_write_site": config.APPROVED_WRITE_SITE,
            "clinic_ui_synthetic_procedures": [
                {
                    "workspace_procedure_id": "WS-PROC-1",
                    "native_procedure_id": "HLC-CPR-1",
                }
            ],
        }
        values.update(overrides)
        return config.load_settings(values.get, config.APPROVED_WRITE_SITE)

    def test_the_gate_opens_only_with_the_flag_site_and_allowlist(self):
        loaded = self.settings()
        self.assertTrue(loaded.procedure_write_enabled)
        self.assertEqual(
            loaded.native_procedure_for("WS-PROC-1"), "HLC-CPR-1"
        )
        self.assertIsNone(loaded.native_procedure_for("WS-PROC-2"))

    def test_the_flag_defaults_off(self):
        loaded = self.settings(clinic_ui_native_procedure_write_enabled=0)
        self.assertFalse(loaded.procedure_write_enabled)

    def test_a_foreign_site_never_opens_the_gate(self):
        loaded = self.settings(clinic_ui_native_procedure_write_site="marley.local")
        self.assertFalse(loaded.procedure_write_enabled)

    def test_a_malformed_allowlist_disables_the_feature(self):
        self.assertIsNone(
            self.settings(
                clinic_ui_synthetic_procedures=[
                    {"workspace_procedure_id": "WS-PROC-1"}
                ]
            )
        )

    def test_a_workspace_id_may_not_equal_a_native_id(self):
        self.assertIsNone(
            self.settings(
                clinic_ui_synthetic_procedures=[
                    {
                        "workspace_procedure_id": "PAT-1",
                        "native_procedure_id": "HLC-CPR-1",
                    }
                ]
            )
        )


if __name__ == "__main__":
    unittest.main()
