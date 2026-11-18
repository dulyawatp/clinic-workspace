"""Endpoint tests for the Clinical Procedure completion write family.

The fake gateway proves gate ordering, the strict body, every pre-fence refusal
(role, boundary/install drift, unknown target, draft/completed/cancelled target,
price list, per-row batch/stock, budget), the fence-before-native ordering, the
closed-world side-effect check, the exactly-once receipt and the one-shot ack.
"""

import unittest
from unittest import mock

from clinic_ui.core import config, patient_write, write_fence
from clinic_ui.core import procedure_write
from clinic_ui.endpoints import procedures_write, session

from .fake_gateway import (
    Denied,
    FakeGateway,
    FakeProcedureReceipt,
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


def committed(fake, key=KEY):
    fake.procedure_receipt_records.append(
        FakeProcedureReceipt(
            "procedure_complete",
            ACTOR,
            key,
            procedure_write.encode_receipt(
                kind="procedure_complete",
                alias=procedure_write.build_alias(
                    SECRET.encode(), SITE, ACTOR, key
                ),
                native_procedure_id=NATIVE,
                stock_entry="NATIVE-SE-1",
            ),
        )
    )


class CompleteTests(unittest.TestCase):
    def test_completes_once_and_leaves_the_ack_to_its_own_endpoint(self):
        fake = gateway()

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], WORKSPACE)
        self.assertEqual(payload["procedure"]["status"], "Completed")
        self.assertTrue(payload["stockEntryCreated"])
        self.assertNotIn("HLC-CPR", repr(payload))
        self.assertNotIn("NATIVE-SE", repr(payload))
        self.assertEqual(fake.procedure_fence_calls[0][1], "procedure_complete")
        self.assertEqual(len(fake.complete_procedure_calls), 1)
        self.assertEqual(fake.commit_calls, 1)
        receipt = fake.procedure_fence_claim.committed[0]
        decoded = procedure_write.decode_receipt(receipt)
        self.assertEqual(decoded.stock_entry, "NATIVE-SE-1")
        self.assertEqual(decoded.native_procedure_id, NATIVE)
        self.assertEqual(fake.procedure_ack_calls, [])

    def test_the_baseline_is_read_before_the_fence_and_passed_to_the_reread(self):
        fake = gateway()

        procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(len(fake.stock_baseline_calls), 1)
        self.assertEqual(fake.stock_baseline_calls[0][1], "WH-1")
        self.assertEqual(
            fake.procedure_facts_calls[0][2], fake.stock_baseline
        )

    def test_a_completed_procedure_is_refused_pre_fence_exactly_once(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(status="Completed")

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.complete_procedure_calls, [])

    def test_a_cancelled_procedure_posts_no_stock(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(docstatus=2)

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_draft_procedure_is_refused(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            docstatus=0, status="Draft"
        )

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_an_unknown_workspace_procedure_is_refused(self):
        fake = gateway()

        payload = procedures_write.complete(
            fake, body(procedure="WS-PROC-UNKNOWN"), CSRF
        )

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.procedure_action_detail_calls, [])

    def test_the_missing_price_list_is_a_named_417_refusal(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(price_list=None)

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.write_responses[-1][0], 417)
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_an_invalid_batch_or_insufficient_stock_is_refused(self):
        cases = (
            ("expired", procedure_detail(items=(row(batch_expiry="2026-01-01"),))),
            ("batch stock", procedure_detail(items=(row(batch_qty=0.0),))),
            (
                "warehouse stock",
                procedure_detail(items=(row(batch_no=None, available_qty=0.0),)),
            ),
        )
        for label, detail in cases:
            with self.subTest(label=label):
                fake = gateway()
                fake.procedure_details[NATIVE] = detail
                payload = procedures_write.complete(fake, body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.write_responses[-1][0], 417)
                self.assertEqual(fake.procedure_fence_calls, [])

    def test_an_unlisted_nursing_checklist_is_refused_pre_fence(self):
        fake = gateway()
        fake.procedure_details[NATIVE] = procedure_detail(
            post_op_checklist="POST-OP-1"
        )

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])

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
                self.assertEqual(fake.procedure_fence_calls, [])

    def test_the_shared_gate_refuses_before_validation(self):
        fake = gateway(current_session=session_view(user="nurse@example.test", roles=("Nurse",)))
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

        fake = gateway()
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), None)
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway()
        fake.procedure_boundary_problem = "source drift for clinical_procedure"
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

        other = gateway()
        other.procedure_install_problem = "missing field Clinical Procedure.price_list"
        with self.assertRaises(Denied):
            procedures_write.complete(other, body(), CSRF)
        self.assertEqual(other.procedure_fence_calls, [])

    def test_a_target_without_native_write_authority_is_denied(self):
        fake = gateway()
        fake.procedure_can_write = False

        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_the_gate_flag_and_allowlist_are_required(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_procedure_write_enabled=0
            )
        )
        with self.assertRaises(Denied):
            procedures_write.complete(fake, body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_an_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        committed(fake, "proc-complete-key-0000-01")

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.complete_procedure_calls, [])


class FenceTests(unittest.TestCase):
    def test_a_concurrent_completion_rolls_back_without_committing(self):
        fake = gateway()
        fake.procedure_locked[NATIVE] = {"status": "Completed"}

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.complete_procedure_calls, [])
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_side_effect_mismatch_rolls_back(self):
        fake = gateway()
        fake.procedure_facts = {"stock_ledger_rows": 0}

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_missing_stock_entry_in_the_reread_rolls_back(self):
        fake = gateway()
        fake.procedure_facts = {"stock_entry": None}

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.committed, [])

    def test_a_native_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.complete_procedure_error = patient_write.WriteUncertain()

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.unknowns, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_an_unexpected_native_error_marks_unknown_and_rolls_back(self):
        fake = gateway()
        fake.complete_procedure_error = RuntimeError("native exploded")

        payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.procedure_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)
        self.assertEqual(fake.commit_calls, 0)

    def test_the_ack_endpoint_is_the_only_acknowledgement(self):
        fake = gateway()

        procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.procedure_ack_calls, [])
        self.assertEqual(len(fake.procedure_fence_claim.committed), 1)

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal_claim(self):
        """The receipt is validated while the native write is still uncommitted.

        A receipt the fence would reject cannot be an established outcome, so it
        is encoded before the one native commit rather than after it.
        """

        fake = gateway()
        real_encode = procedure_write.encode_receipt
        real_claim_commit = fake.procedure_fence_claim.commit

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_claim_commit(receipt):
            fake.call_order.append("claim_commit")
            real_claim_commit(receipt)

        fake.procedure_fence_claim.commit = recording_claim_commit
        with mock.patch.object(procedure_write, "encode_receipt", recording_encode):
            payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        order = fake.call_order
        self.assertEqual(len(fake.complete_procedure_calls), 1)
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("claim_commit"))
        self.assertEqual(len(fake.procedure_fence_claim.committed), 1)

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        fake = gateway()

        with mock.patch.object(
            procedure_write,
            "encode_receipt",
            side_effect=procedure_write.ProcedureWriteError("invalid receipt"),
        ):
            payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        # The encode refusal lands while the native transaction is still
        # uncommitted: only the rollback attempt is recorded and the key stays
        # locked as UNKNOWN with no COMMITTED terminal.
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertTrue(fake.procedure_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        """A failed rollback must fail loudly, never become a normal 409.

        Returning an envelope would let Frappe's request-end commit persist the
        partial native write; the dedicated exception propagates to cleanup.
        """

        fake = gateway()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(
            procedure_write,
            "encode_receipt",
            side_effect=procedure_write.ProcedureWriteError("invalid receipt"),
        ), mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(procedures_write.RollbackFailed):
                procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.procedure_fence_claim.committed, [])

    def test_a_named_handler_rollback_failure_marks_nothing_and_responds_nothing(
        self,
    ):
        """A named exception path never marks UNKNOWN before its rollback.

        Only the generic handler appends ``UNKNOWN``; the named handlers attempt
        the rollback and re-raise, so the key stays ``DISPATCHED``. The escape
        itself must not be converted by any handler: ``RollbackFailed`` derives
        from ``RuntimeError``, not from ``WriteUncertain`` or ``FenceError``.
        """

        fake = gateway()
        fake.complete_procedure_error = patient_write.WriteUncertain()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(procedures_write.RollbackFailed):
                procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(fake.procedure_fence_claim.unknowns, [])
        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertFalse(
            issubclass(procedures_write.RollbackFailed, patient_write.WriteUncertain)
        )
        self.assertFalse(
            issubclass(procedures_write.RollbackFailed, write_fence.FenceError)
        )

    def test_a_native_commit_failure_appends_no_committed_terminal(self):
        fake = gateway()

        def failing_commit():
            fake.call_order.append("commit")
            raise RuntimeError("native commit failed")

        with mock.patch.object(fake, "commit", failing_commit):
            payload = procedures_write.complete(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertTrue(fake.procedure_fence_claim.unknowns)
        self.assertEqual(fake.rollback_calls, 1)


class CreateTests(unittest.TestCase):
    """D4: the creation path from a submitted Service Request."""

    def test_creates_one_procedure_and_leaves_the_ack_to_its_endpoint(self):
        fake = gateway()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        self.assertEqual(payload["procedure"]["state"], "pending")
        self.assertEqual(payload["procedure"]["status"], "Pending")
        self.assertTrue(procedure_write.is_valid_ack_token(payload["ackToken"]))
        alias = payload["procedure"]["procedure_id"]
        self.assertEqual(
            alias,
            procedure_write.build_alias(SECRET.encode(), SITE, ACTOR, CREATE_KEY),
        )
        self.assertNotIn("HLC-CPR", repr(payload))
        self.assertNotIn("HLC-SR", repr(payload))
        self.assertEqual(fake.procedure_fence_calls[0][1], "procedure_create")
        self.assertEqual(len(fake.create_procedure_calls), 1)
        self.assertEqual(fake.create_procedure_calls[0].patient, "PAT-1")
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(fake.procedure_ack_calls, [])
        receipt = procedure_write.decode_receipt(
            fake.procedure_fence_claim.committed[0]
        )
        self.assertEqual(receipt.kind, "procedure_create")
        self.assertEqual(receipt.service_request, SERVICE_REQUEST)
        self.assertEqual(receipt.native_procedure_id, fake.created_procedure_id)
        self.assertEqual(receipt.stock_entry, "")

    def test_the_create_path_encodes_the_receipt_before_the_commit(self):
        """The creation path already encodes before the one commit; keep it."""

        fake = gateway()
        real_encode = procedure_write.encode_receipt

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        with mock.patch.object(procedure_write, "encode_receipt", recording_encode):
            payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload["status"], "created")
        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertEqual(len(fake.procedure_fence_claim.committed), 1)

    def test_the_created_alias_completes_through_its_receipt(self):
        fake = gateway(
            settings=write_settings(clinic_ui_synthetic_procedures=[])
        )
        created = procedures_write.create(fake, create_body(), CSRF)
        alias = created["procedure"]["procedure_id"]
        fake.procedure_details[fake.created_procedure_id] = procedure_detail(
            service_request=SERVICE_REQUEST
        )
        fake.procedure_receipt_records.append(
            FakeProcedureReceipt(
                "procedure_create",
                ACTOR,
                CREATE_KEY,
                fake.procedure_fence_claim.committed[0],
            )
        )

        payload = procedures_write.complete(
            fake,
            {
                "procedure": alias,
                "operation_key": "proc-complete-key-0002-01",
            },
            CSRF,
        )

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["procedure"]["procedure_id"], alias)
        self.assertEqual(
            fake.complete_procedure_calls[0].as_dict()["service_request"],
            SERVICE_REQUEST,
        )

    def test_a_rollback_failure_escapes_on_the_creation_path(self):
        """The creation path shares the escape-not-respond rollback rule."""

        fake = gateway()

        def failing_rollback():
            raise RuntimeError("rollback exploded")

        with mock.patch.object(
            procedure_write,
            "encode_receipt",
            side_effect=procedure_write.ProcedureWriteError("invalid receipt"),
        ), mock.patch.object(fake, "rollback", failing_rollback):
            with self.assertRaises(procedures_write.RollbackFailed):
                procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.procedure_fence_claim.committed, [])

    def test_a_missing_service_request_is_refused_before_the_fence(self):
        fake = gateway()
        fake.service_request_states.clear()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.create_procedure_calls, [])

    def test_an_unsubmitted_service_request_is_refused_before_the_fence(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            docstatus=0
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_non_procedure_request_is_refused_before_the_fence(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            template_dt="Lab Test Template", procedure_template=None
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_an_existing_procedure_for_the_request_is_refused_exactly_once(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            procedure_exists=True
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])
        self.assertEqual(fake.create_procedure_calls, [])

    def test_a_stock_consuming_template_is_refused_before_the_fence(self):
        fake = gateway()
        fake.service_request_states[SERVICE_REQUEST] = service_request_state(
            consume_stock=True
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_caller_without_create_authority_is_denied(self):
        fake = gateway()
        fake.procedure_can_create = False

        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_nurse_is_denied_before_validation(self):
        fake = gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nursing User",)
            )
        )

        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)
        self.assertEqual(fake.procedure_fence_calls, [])

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
                self.assertEqual(fake.procedure_fence_calls, [])

    def test_boundary_and_install_drift_fail_closed(self):
        fake = gateway(procedure_boundary_problem="versions drift")
        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

        fake = gateway(procedure_install_problem="missing field")
        with self.assertRaises(Denied):
            procedures_write.create(fake, create_body(), CSRF)

    def test_an_exhausted_creation_budget_is_refused_before_the_fence(self):
        fake = gateway()
        fake.procedure_receipt_records.append(
            FakeProcedureReceipt(
                "procedure_create",
                ACTOR,
                "proc-create-key-0001-01",
                procedure_write.encode_receipt(
                    kind="procedure_create",
                    alias=procedure_write.build_alias(
                        SECRET.encode(), SITE, ACTOR, "proc-create-key-0001-01"
                    ),
                    native_procedure_id="HLC-CPR-0",
                    service_request="HLC-SR-0",
                ),
            )
        )

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_facts_mismatch_rolls_back(self):
        fake = gateway()
        fake.procedure_create_facts = {"docstatus": 0}

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_target_that_became_ineligible_inside_the_fence_rolls_back(self):
        fake = gateway()
        fake.service_request_lock_error = procedure_write.ProcedureTargetNotEligible()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.procedure_fence_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_native_creation_failure_is_uncertain_without_committing(self):
        fake = gateway()
        fake.create_procedure_error = patient_write.WriteUncertain()

        payload = procedures_write.create(fake, create_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.procedure_fence_claim.committed, [])


class AckTests(unittest.TestCase):
    def ack_body(self, **overrides):
        values = {
            "operation_key": KEY,
            "kind": "procedure_complete",
            "ack_token": "a" * 64,
        }
        values.update(overrides)
        return values

    def test_a_valid_ack_is_recorded_once(self):
        fake = gateway()

        payload = procedures_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(len(fake.procedure_ack_calls), 1)

    def test_an_invalid_ack_is_a_conflict(self):
        fake = gateway()
        fake.procedure_ack_status = "invalid"

        payload = procedures_write.acknowledge(fake, self.ack_body(), CSRF)

        self.assertEqual(payload, {"code": "OPERATION_CONFLICT"})

    def test_a_malformed_ack_body_is_validation(self):
        fake = gateway()

        payload = procedures_write.acknowledge(
            fake, self.ack_body(ack_token="short"), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.procedure_ack_calls, [])

    def test_a_creation_ack_is_recorded_once(self):
        fake = gateway()

        payload = procedures_write.acknowledge(
            fake,
            self.ack_body(kind="procedure_create", operation_key=CREATE_KEY),
            CSRF,
        )

        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(
            fake.procedure_ack_calls,
            [(ACTOR, "procedure_create", CREATE_KEY, "a" * 64)],
        )


class BootstrapTests(unittest.TestCase):
    def test_the_pair_is_published_only_when_the_gate_is_open(self):
        fake = gateway()
        payload = session.bootstrap(fake)
        capabilities = payload["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "enabled")
        self.assertEqual(capabilities["write.stock.consume"], "enabled")

    def test_the_flags_stay_read_only_when_the_gate_is_closed(self):
        fake = gateway(
            settings=write_settings(
                clinic_ui_native_procedure_write_enabled=0
            )
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "read_only")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")

    def test_a_nurse_never_receives_the_procedure_capability(self):
        fake = gateway(
            current_session=session_view(
                user="nurse@example.test", roles=("Nurse",)
            )
        )
        capabilities = session.bootstrap(fake)["capabilities"]
        self.assertEqual(capabilities["write.procedure.complete"], "read_only")


if __name__ == "__main__":
    unittest.main()
