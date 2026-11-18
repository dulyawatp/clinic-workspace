"""Endpoint tests for the converted Clinical Procedure write family.

The fake gateway proves the converted development lane: gate ordering, the strict
body, the designed pre-mutation refusals (role, boundary/install drift, unknown
target, draft/completed/cancelled target, price list, per-row batch/stock), the
native lifecycle and the closed-world side-effect check.

It also proves the absence of the historical fence machinery: no deployment
marker, no write budget, no receipt append, no acknowledgement, no automatic
retry, and an unreadable historical ledger never blocks a converted write. A
historical sealed alias still resolves read-only through its committed creation
receipt, and a newly created procedure is addressed by its native docname.
"""

import unittest
from unittest import mock

from clinic_ui.core import config, patient_write, procedure_write
from clinic_ui.endpoints import procedures_write, session

from .fake_gateway import (
    Denied,
    FakeGateway,
    FakeProcedureReceipt,
    NativePermissionDenied,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "doctor@example.test"
CSRF = "csrf-token-value"
KEY = "proc-complete-key-0001-01"
CREATE_KEY = "proc-create-key-0001-01"
WORKSPACE = "WS-PROC-1"
NATIVE = "HLC-CPR-1"
SERVICE_REQUEST = "HLC-SR-1"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_native_procedure_write_enabled=1,
        clinic_ui_native_procedure_write_site=SITE,
        clinic_ui_synthetic_procedures=[
            {
                "workspace_procedure_id": WORKSPACE,
                "native_procedure_id": NATIVE,
            }
        ],
    )
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


def procedure_detail(**overrides):
    values = {
        "patient": "PAT-1",
        "docstatus": 1,
        "status": "In Progress",
        "consume_stock": False,
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


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(user=ACTOR, roles=("Physician",)),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = FakeGateway(
        **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    fake.site_today_value = "2026-11-16"
    fake.procedure_details[NATIVE] = procedure_detail()
    fake.service_request_states[SERVICE_REQUEST] = service_request_state()
    fake.stock_baseline = {
        "stock_ledger_rows": 5,
        "gl_rows": 10,
        "bins": {"ITEM-1": 10.0},
    }
    # The converted lane only completes non-consuming procedures and drives no
    # Stock Entry at all.
    fake.created_stock_entry = None
    fake.procedure_facts = non_consuming_facts()
    return fake


def body(**overrides):
    values = {"procedure": WORKSPACE, "operation_key": KEY}
    values.update(overrides)
    return values


def service_request_state(**overrides):
    """The server-read creation decisions for one submitted request."""

    values = {
        "exists": True,
        "docstatus": 1,
        "template_dt": "Clinical Procedure Template",
        "template_dn": "TPL-1",
        "patient": "PAT-1",
        "procedure_template": "TPL-1",
        "procedure_exists": False,
        "consume_stock": False,
        "service_request": SERVICE_REQUEST,
    }
    values.update(overrides)
    return values


def create_body(**overrides):
    values = {"service_request": SERVICE_REQUEST, "operation_key": CREATE_KEY}
    values.update(overrides)
    return values


def historical_create_receipt(fake, key=CREATE_KEY, native="HLC-CPR-0"):
    fake.procedure_receipt_records.append(
        FakeProcedureReceipt(
            "procedure_create",
            ACTOR,
            key,
            procedure_write.encode_receipt(
                kind="procedure_create",
                alias=procedure_write.build_alias(
                    SECRET.encode(), SITE, ACTOR, key
                ),
                native_procedure_id=native,
                service_request="HLC-SR-0",
            ),
        )
    )


def item_detail(*rows, **overrides):
    """A non-consuming procedure that still carries item rows."""

    return procedure_detail(items=tuple(rows), **overrides)


def non_consuming_facts(**overrides):
    """Closed-world facts for a procedure that consumes no stock."""

    values = {
        "stock_entry": None,
        "stock_entry_docstatus": None,
        "stock_entry_type": None,
        "stock_entry_company": None,
        "procedure_company": "COMP-1",
        "status": "Completed",
        "actual_end_datetime": "2026-11-16 10:00:00",
        "stock_ledger_rows": 0,
        "gl_rows": 0,
        "bin_delta": 0.0,
        "bin_warehouse": "WH-1",
        "nursing_task_rows": 0,
    }
    values.update(overrides)
    return values


class CompleteTests(unittest.TestCase):
    def test_completes_through_the_native_lifecycle_without_fence_or_ack(self):
        fake = gateway()
        fake.procedure_read_status[NATIVE] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], WORKSPACE)
        self.assertEqual(payload["procedure"]["state"], "completed")
        self.assertEqual(payload["procedure"]["status"], "Completed")
        self.assertIs(payload["stockEntryCreated"], False)
        self.assertNotIn("ackToken", payload)
        self.assertNotIn("NATIVE-SE", repr(payload))
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.procedure_receipt_calls, [])
        self.assertEqual(fake.procedure_ack_calls, [])
        self.assertEqual(len(fake.complete_procedure_calls), 1)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.rollback_calls, 0)
        self.assertEqual(fake.procedure_lock_calls, [NATIVE])

    def test_the_baseline_is_read_before_dispatch_and_passed_to_the_reread(self):
        fake = gateway()

        procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(len(fake.stock_baseline_calls), 1)
        self.assertEqual(fake.stock_baseline_calls[0][1], "WH-1")
        self.assertEqual(fake.procedure_facts_calls[0][2], fake.stock_baseline)

    def test_the_read_back_uses_the_native_docname_and_echoes_the_workspace_id(
        self,
    ):
        fake = gateway()
        fake.procedure_read_status[NATIVE] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.procedure_read_detail_calls, [NATIVE])
        self.assertEqual(payload["procedure"]["procedure_id"], WORKSPACE)

    def test_the_read_back_uses_the_native_docname_for_a_historical_alias(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        historical_create_receipt(fake)
        alias = procedure_write.build_alias(
            SECRET.encode(), SITE, ACTOR, CREATE_KEY
        )
        fake.procedure_details["HLC-CPR-0"] = procedure_detail()
        fake.procedure_read_status["HLC-CPR-0"] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(fake, body(procedure=alias), CSRF)

        self.assertEqual(fake.procedure_read_detail_calls, ["HLC-CPR-0"])
        self.assertEqual(payload["procedure"]["procedure_id"], alias)

    def test_a_stock_consuming_procedure_is_refused_before_the_lock(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            consume_stock=True, items=(row(),)
        )

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.write_responses[-1][0], 409)
        self.assertEqual(fake.stock_baseline_calls, [])
        self.assertEqual(fake.procedure_lock_calls, [])
        self.assertEqual(fake.complete_procedure_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 0)

    def test_a_missing_or_ambiguous_consume_flag_fails_closed(self):
        for label, mutate in (
            ("missing", lambda detail: detail.pop("consume_stock")),
            ("null", lambda detail: detail.update(consume_stock=None)),
            ("text", lambda detail: detail.update(consume_stock="false")),
        ):
            with self.subTest(label=label):
                fake = gateway()
                detail = procedure_detail()
                mutate(detail)
                fake.procedure_details[NATIVE] = detail

                payload = procedures_write.complete(fake, body(), CSRF)

                self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
                self.assertEqual(fake.procedure_lock_calls, [])
                self.assertEqual(fake.complete_procedure_calls, [])

    def test_a_consume_flag_that_turned_on_under_the_lock_is_refused(self):
        fake = gateway()
        fake.procedure_locked[NATIVE] = {"consume_stock": True}

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.complete_procedure_calls, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_non_consuming_completion_reports_a_false_stock_entry_flag(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            consume_stock=False, items=()
        )
        fake.created_stock_entry = None
        fake.procedure_facts = non_consuming_facts()

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertIs(payload["stockEntryCreated"], False)
        self.assertEqual(fake.procedure_read_detail_calls, [NATIVE])

    def test_a_non_consuming_completion_without_a_warehouse_is_not_uncertain(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            consume_stock=False, items=(), warehouse=None
        )
        fake.created_stock_entry = None
        fake.procedure_facts = non_consuming_facts(bin_warehouse=None)

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertIs(payload["stockEntryCreated"], False)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.rollback_calls, 0)

    def test_an_unresolved_historical_ledger_never_blocks_the_write(self):
        fake = gateway()
        fake.procedure_receipt_list_error = RuntimeError("ledger unavailable")
        fake.procedure_fence_error = RuntimeError("fence unavailable")

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(fake.procedure_receipt_calls, [])
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.commit_calls, 1)

    def test_a_historical_created_alias_still_resolves_read_only(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        historical_create_receipt(fake)
        alias = procedure_write.build_alias(
            SECRET.encode(), SITE, ACTOR, CREATE_KEY
        )
        fake.procedure_details["HLC-CPR-0"] = procedure_detail()
        fake.procedure_read_status["HLC-CPR-0"] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(
            fake, body(procedure=alias), CSRF
        )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], alias)
        self.assertEqual(fake.procedure_receipt_calls, [])
        self.assertEqual(fake.procedure_ack_calls, [])
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_native_docname_is_a_target_under_native_authority(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        fake.procedure_can_read = True
        fake.procedure_read_status[NATIVE] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(fake, body(procedure=NATIVE), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], NATIVE)

    def test_a_native_docname_without_read_authority_is_denied(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        fake.procedure_can_read = False

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(procedure=NATIVE), CSRF)
        self.assertEqual(fake.procedure_lock_calls, [])
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_an_alias_shaped_value_is_never_reinterpreted_as_a_docname(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        fake.procedure_can_read = True
        alias = "wsp1_" + "0" * 32 + "_" + "0" * 16 + "_" + "a" * 16

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(procedure=alias), CSRF)
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_a_malformed_alias_namespace_value_never_falls_back_to_native(self):
        for value in (
            "wsp1_truncated",
            "wsp1_" + "z" * 32 + "_" + "0" * 16 + "_" + "a" * 16,
            "wsp1_" + "0" * 32 + "_" + "0" * 16 + "_" + "a" * 8,
        ):
            with self.subTest(value=value):
                fake = gateway(
                    settings=write_settings(clinic_ui_synthetic_procedures=[])
                )
                fake.procedure_can_read = True
                fake.procedure_can_write = True

                with self.assertRaises(Denied):
                    procedures_write.complete(fake, body(procedure=value), CSRF)
                self.assertEqual(fake.procedure_action_detail_calls, [])
                self.assertEqual(fake.procedure_lock_calls, [])
                self.assertEqual(fake.complete_procedure_calls, [])

    def test_an_unknown_workspace_procedure_is_denied(self):
        fake = gateway()

        with self.assertRaises(Denied):
            procedures_write.complete(
                fake, body(procedure="WS-PROC-UNKNOWN"), CSRF
            )

        self.assertEqual(fake.complete_procedure_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_an_ineligible_target_is_a_designed_conflict(self):
        cases = (
            ("completed", procedure_detail(status="Completed")),
            ("cancelled", procedure_detail(docstatus=2)),
            ("draft", procedure_detail(docstatus=0, status="Draft")),
        )
        for label, detail in cases:
            with self.subTest(label=label):
                fake = gateway()
                fake.procedure_details[NATIVE] = detail

                payload = procedures_write.complete(fake, body(), CSRF)

                self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
                self.assertEqual(fake.write_responses[-1][0], 409)
                self.assertEqual(fake.complete_procedure_calls, [])
                self.assertEqual(fake.commit_calls, 0)

    def test_the_missing_price_list_is_a_designed_refusal(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = item_detail(row(), price_list=None)

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_an_invalid_batch_or_insufficient_stock_is_refused(self):
        cases = (
            ("expired", item_detail(row(batch_expiry="2026-01-01"))),
            ("batch stock", item_detail(row(batch_qty=0.0))),
            (
                "warehouse stock",
                item_detail(row(batch_no=None, available_qty=0.0)),
            ),
        )
        for label, detail in cases:
            with self.subTest(label=label):
                fake = gateway()
                fake.procedure_details[NATIVE] = detail
                payload = procedures_write.complete(fake, body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 417)
                self.assertEqual(fake.complete_procedure_calls, [])

    def test_an_unlisted_nursing_checklist_is_refused(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            post_op_checklist="POST-OP-1"
        )

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_the_body_is_strict(self):
        for candidate in (
            {},
            {"procedure": WORKSPACE},
            {"operation_key": KEY},
            {**body(), "docstatus": 1},
        ):
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = procedures_write.complete(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.complete_procedure_calls, [])

    def test_the_shared_gate_refuses_before_validation(self):
        fake = gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nurse",)
            )
        )
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)

        fake = gateway()
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), None)

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway(procedure_boundary_problem="source drift")
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)

        other = gateway(
            procedure_install_problem="missing field Clinical Procedure.price_list"
        )
        with self.assertRaises(Denied):
            procedures_write.complete(other, body(), CSRF)

    def test_the_historical_deployment_marker_no_longer_gates_the_write(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")

    def test_a_target_without_native_write_authority_is_denied(self):
        fake = gateway()
        fake.procedure_can_write = False

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_an_unreadable_target_is_denied(self):
        fake = gateway()
        fake.procedure_details.clear()

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_the_gate_flag_is_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_procedure_write_enabled=0)
        )
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)

    def test_a_native_failure_rolls_back_and_reports_an_uncertain_outcome(self):
        fake = gateway()
        fake.complete_procedure_error = patient_write.WriteUncertain()

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.write_responses[-1][0], 409)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(len(fake.complete_procedure_calls), 1)

    def test_an_unexpected_native_error_is_uncertain_without_a_retry(self):
        fake = gateway()
        fake.complete_procedure_error = RuntimeError("native exploded")

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(len(fake.complete_procedure_calls), 1)

    def test_a_side_effect_mismatch_rolls_back_as_uncertain(self):
        fake = gateway()
        fake.procedure_facts = non_consuming_facts(stock_ledger_rows=1)

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_commit_failure_is_uncertain(self):
        fake = gateway()

        def failing_commit():
            raise RuntimeError("native commit failed")

        with mock.patch.object(fake, "commit", failing_commit):
            payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_unreadable_read_back_after_commit_is_uncertain(self):
        fake = gateway()
        fake.procedure_read_detail = False

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 1)

    def test_a_masked_or_blank_status_read_back_after_commit_is_uncertain(self):
        for status in ("", "   ", None):
            with self.subTest(status=status):
                fake = gateway()
                fake.procedure_read_status[NATIVE] = {"status": status}

                payload = procedures_write.complete(fake, body(), CSRF)

                self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
                self.assertEqual(fake.commit_calls, 1)
                self.assertEqual(fake.write_responses[-1][0], 409)

    def test_a_read_back_that_is_not_completed_is_uncertain(self):
        fake = gateway()
        fake.procedure_read_status[NATIVE] = {"status": "In Progress"}

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})

    def test_a_lock_permission_denial_is_a_definite_denial(self):
        fake = gateway()
        fake.procedure_lock_error = NativePermissionDenied("not permitted")

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_a_lock_failure_is_uncertain(self):
        fake = gateway()
        fake.procedure_lock_error = patient_write.WriteUncertain()

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        fake = gateway()
        fake.complete_procedure_error = patient_write.WriteUncertain()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(procedures_write.RollbackFailed):
                procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)


class CreateTests(unittest.TestCase):
    """The creation path from a submitted Service Request."""

    def test_creates_one_procedure_and_returns_its_native_docname(self):
        fake = gateway()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(
            payload["procedure"]["procedure_id"], fake.created_procedure_id
        )
        self.assertEqual(payload["procedure"]["state"], "pending")
        self.assertEqual(payload["procedure"]["status"], "Pending")
        self.assertNotIn("ackToken", payload)
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.procedure_receipt_calls, [])
        self.assertEqual(fake.procedure_ack_calls, [])
        self.assertEqual(len(fake.create_procedure_calls), 1)
        self.assertEqual(fake.create_procedure_calls[0].patient, "PAT-1")
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.rollback_calls, 0)

    def test_the_created_native_docname_completes_the_same_procedure(self):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        fake.procedure_can_read = True
        created = procedures_write.create(fake, create_body(), CSRF)
        procedure_id = created["procedure"]["procedure_id"]
        fake.procedure_details[procedure_id] = procedure_detail(
            service_request=SERVICE_REQUEST
        )
        fake.procedure_read_status[procedure_id] = {
            "status": "Completed",
            "docstatus": 1,
        }

        payload = procedures_write.complete(
            fake,
            {
                "procedure": procedure_id,
                "operation_key": "proc-complete-key-0002-01",
            },
            CSRF,
        )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], procedure_id)
        self.assertEqual(
            fake.complete_procedure_calls[0].as_dict()["service_request"],
            SERVICE_REQUEST,
        )

    def test_a_created_non_consuming_procedure_completes_with_the_full_shape(
        self,
    ):
        fake = gateway(settings=write_settings(clinic_ui_synthetic_procedures=[]))
        fake.procedure_can_read = True
        created = procedures_write.create(fake, create_body(), CSRF)
        procedure_id = created["procedure"]["procedure_id"]
        fake.procedure_details[procedure_id] = procedure_detail(
            service_request=SERVICE_REQUEST, consume_stock=False, items=()
        )
        fake.procedure_read_status[procedure_id] = {
            "status": "Completed",
            "docstatus": 1,
        }
        fake.created_stock_entry = None
        fake.procedure_facts = non_consuming_facts()

        payload = procedures_write.complete(
            fake,
            {
                "procedure": procedure_id,
                "operation_key": "proc-complete-key-0003-01",
            },
            CSRF,
        )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], procedure_id)
        self.assertEqual(payload["procedure"]["state"], "completed")
        self.assertEqual(payload["procedure"]["status"], "Completed")
        self.assertIs(payload["stockEntryCreated"], False)
        self.assertEqual(fake.procedure_read_detail_calls[-1], procedure_id)

    def test_a_missing_service_request_is_refused(self):
        fake = gateway()
        fake.service_request_states.clear()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.create_procedure_calls, [])

    def test_an_unsubmitted_service_request_is_refused(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            docstatus=0
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_non_procedure_request_is_refused(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            template_dt="Lab Test Template", procedure_template=None
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.create_procedure_calls, [])

    def test_an_existing_procedure_for_the_request_is_refused_exactly_once(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            procedure_exists=True
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_stock_consuming_template_is_refused(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            consume_stock=True
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_caller_without_create_authority_is_denied(self):
        fake = gateway()
        fake.procedure_can_create = False

        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_nurse_is_denied_before_validation(self):
        fake = gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nursing User",)
            )
        )

        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

    def test_the_body_is_strict(self):
        cases = (
            {},
            {"operation_key": KEY},
            {"service_request": SERVICE_REQUEST},
            {**create_body(), "procedure": "WS-PROC-1"},
            {**create_body(), "docstatus": 1},
            {"service_request": " " + SERVICE_REQUEST, "operation_key": KEY},
            {"service_request": SERVICE_REQUEST, "operation_key": "short"},
        )
        for candidate in cases:
            with self.subTest(candidate=candidate):
                fake = gateway()
                payload = procedures_write.create(fake, candidate, CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.create_procedure_calls, [])

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway(procedure_boundary_problem="versions drift")
        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

        fake = gateway(procedure_install_problem="missing field")
        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

    def test_the_gate_flag_and_site_are_required(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_procedure_write_enabled=0)
        )
        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

    def test_the_historical_deployment_marker_no_longer_gates_creation(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload["status"], "created")

    def test_a_facts_mismatch_rolls_back_as_uncertain(self):
        fake = gateway()
        fake.procedure_create_facts = {"docstatus": 0}

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_target_that_became_ineligible_under_the_lock_is_a_conflict(self):
        fake = gateway()
        fake.service_request_lock_error = (
            procedure_write.ProcedureTargetNotEligible()
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_native_creation_failure_is_uncertain_without_retry(self):
        fake = gateway()
        fake.create_procedure_error = patient_write.WriteUncertain()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(len(fake.create_procedure_calls), 1)

    def test_a_native_commit_failure_is_uncertain(self):
        fake = gateway()

        def failing_commit():
            raise RuntimeError("native commit failed")

        with mock.patch.object(fake, "commit", failing_commit):
            payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_UNCERTAIN"})
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_rollback_failure_escapes_on_the_creation_path(self):
        fake = gateway()
        fake.create_procedure_error = patient_write.WriteUncertain()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(procedures_write.RollbackFailed):
                procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)


class BootstrapTests(unittest.TestCase):
    def test_the_surface_is_published_without_the_stock_capability(self):
        fake = gateway()
        payload = session.bootstrap(fake)
        capabilities = payload["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "enabled")
        self.assertEqual(capabilities["write.procedure.create"], "enabled")
        # The converted lane refuses consuming procedures, so the stock
        # consumption capability must never be advertised by this gate.
        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_the_historical_fence_does_not_gate_the_capabilities(self):
        fake = gateway()
        fake.deployment_approved = False
        fake.fence_ready = False

        capabilities = session.bootstrap(fake)["capabilities"]

        self.assertEqual(capabilities["write.procedure.complete"], "enabled")
        self.assertEqual(capabilities["write.procedure.create"], "enabled")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_the_flags_stay_read_only_when_the_gate_is_closed(self):
        fake = gateway(
            settings=write_settings(clinic_ui_native_procedure_write_enabled=0)
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "read_only")
        self.assertEqual(capabilities["write.procedure.create"], "read_only")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_a_stock_consuming_gate_flag_never_publishes_stock_consume(self):
        fake = gateway()
        fake.procedure_can_write = True

        capabilities = session.bootstrap(fake)["capabilities"]

        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_a_nurse_never_receives_the_procedure_capability(self):
        fake = gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nurse",)
            )
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "read_only")
        self.assertEqual(capabilities["write.procedure.create"], "read_only")


if __name__ == "__main__":
    unittest.main()
