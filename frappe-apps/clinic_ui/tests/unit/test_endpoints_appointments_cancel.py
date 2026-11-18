"""Endpoint tests for the designed appointment cancel and manual no-show.

The fake gateway proves gate ordering, strict bodies, pre-fence refusals, the
fence-before-native ordering, the pinned post-condition handling and the
commit/receipt/acknowledgement chain. It does not claim native permission
semantics; the adapter tests cover the locked native calls.

Every designed refusal in steps 3-6 is asserted to happen *before* the fence
claim. A refusal taken after the claim is not free: the claim is already
``DISPATCHED``, so it blocks the whole site and writes no latch.
``FenceLedgerIntegrationTests`` holds the production ledger to show that, and
that a ``COMMITTED`` operation leaves its reserved barrier ``DISPATCHED`` until
the client acknowledges the receipt.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from clinic_ui.core import (
    appointment_write,
    config,
    fence_store,
    patient_write,
    write_fence,
)
from clinic_ui.core.appointment_write import build_alias
from clinic_ui.endpoints import appointments_cancel_write, appointments_write

from .fake_gateway import (
    Denied,
    FakeAppointmentReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "reception@example.test"
CSRF = "csrf-token-value"
BOOK_KEY = "appt-book-key-0001-012345"
CANCEL_KEY = "appt-cancel-key-0001-0123"
NO_SHOW_KEY = "appt-noshow-key-0001-0123"
MODIFIED = "2026-11-14 09:30:00.123456"
TODAY = "2026-11-14"
NATIVE_TARGET = "NATIVE-APPT-1"
REASON = "Patient called to cancel the visit."


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_patients=[
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
            {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
        ],
        clinic_ui_synthetic_appointments=[
            {
                "workspace_appointment_id": "APPT-1",
                "native_appointment_id": NATIVE_TARGET,
            },
        ],
        clinic_ui_native_appointment_write_enabled=1,
        clinic_ui_native_appointment_write_site=SITE,
        clinic_ui_appointment_medical_department="MEDDEP-1",
        clinic_ui_appointment_company="COMP-1",
        clinic_ui_appointment_type="APPT-TYPE-1",
        clinic_ui_appointment_checkin_patient="WS-2",
    )
    values.update(overrides)
    return values


def action_detail(**overrides):
    values = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Scheduled",
        "notes": None,
        "practitioner": None,
        "event": None,
        "invoiced": 0,
        "ref_sales_invoice": None,
        "paid_amount": 0,
        "insurance_coverage": None,
        "service_request": None,
        "mode_of_payment": None,
    }
    values.update(overrides)
    return values


def _fake_gateway(factory, *args, **overrides):
    values = {
        "site": SITE,
        "settings": write_settings(),
        "current_session": session_view(user=ACTOR, roles=("Clinic Reception",)),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = factory(
        *args, **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    fake.site_today_value = TODAY
    fake.appointment_action_details[NATIVE_TARGET] = action_detail()
    fake.appointment_locked[NATIVE_TARGET] = {"status": "Scheduled"}
    fake.appointment_detail_map[NATIVE_TARGET] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-20",
        "appointment_time": "09:30:00",
        "status": "Cancelled",
        "modified": MODIFIED,
    }
    return fake


def gateway(**overrides):
    return _fake_gateway(FakeGateway, **overrides)


def alias_for(key):
    return build_alias(SECRET.encode(), SITE, ACTOR, key)


def committed(fake, kind, key, native_id, actor=ACTOR):
    fake.appointment_receipt_records.append(
        FakeAppointmentReceipt(
            kind,
            actor,
            key,
            appointment_write.encode_receipt(
                kind=kind,
                alias=alias_for(key),
                native_appointment_id=native_id,
            ),
        )
    )


def booked_target(fake, *, key=BOOK_KEY, native_id=NATIVE_TARGET):
    committed(fake, "book", key, native_id)
    return alias_for(key)


def body(**overrides):
    values = {
        "patient": "WS-1",
        "appointment_id": alias_for(BOOK_KEY),
        "operation_key": CANCEL_KEY,
        "reason": REASON,
    }
    values.update(overrides)
    return values


class CancelTests(unittest.TestCase):
    def test_cancels_a_booked_target_and_acknowledges_once(self):
        fake = gateway()
        booked_target(fake)

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload["status"], "cancelled")
        self.assertEqual(
            payload["appointment"]["appointment_id"], alias_for(BOOK_KEY)
        )
        self.assertEqual(payload["appointment"]["status"], "Cancelled")
        self.assertNotIn("native_appointment_id", repr(payload))
        self.assertEqual(fake.appointment_fence_calls[0][1], "appointment_cancel")
        self.assertEqual(fake.appointment_fence_calls[0][0], ACTOR)
        self.assertEqual(len(fake.cancel_calls), 1)
        state, reason, actor = fake.cancel_calls[0]
        self.assertEqual(state.status, "Scheduled")
        self.assertEqual(reason, REASON)
        self.assertEqual(actor, ACTOR)
        self.assertEqual(fake.commit_calls, 1)
        self.assertEqual(len(fake.appointment_fence_claim.committed), 1)

    def test_receipt_and_acknowledgement_bind_the_cancel_kind(self):
        fake = gateway()
        booked_target(fake)
        appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        receipt = fake.appointment_fence_claim.committed[0]
        decoded = appointment_write.decode_receipt(receipt)
        self.assertEqual(decoded.kind, "appointment_cancel")
        self.assertEqual(decoded.alias, alias_for(BOOK_KEY))

        fake.appointment_ack_status = "acked"
        acked = appointments_write.ack(
            fake,
            {
                "operation_key": CANCEL_KEY,
                "kind": "appointment_cancel",
                "ack_token": "a" * 64,
            },
            CSRF,
        )
        self.assertEqual(acked, {"acknowledged": True})
        self.assertEqual(
            fake.appointment_ack_calls[0][1], "appointment_cancel"
        )
        self.assertEqual(fake.appointment_ack_calls[0][2], CANCEL_KEY)

    def test_wrong_role_is_refused_before_the_fence(self):
        fake = gateway(
            current_session=session_view(user="pharmacist@example.test", roles=("Pharmacy",))
        )
        booked_target(fake)
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.cancel_calls, [])

    def test_missing_reason_is_a_validation_refusal_before_the_fence(self):
        fake = gateway()
        booked_target(fake)
        for reason in (None, "", "   "):
            with self.subTest(reason=str(reason)):
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(reason=reason), CSRF
                )
                self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_terminal_target_status_is_refused_before_the_fence(self):
        for status in ("Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                fake = gateway()
                booked_target(fake)
                fake.appointment_action_details[NATIVE_TARGET] = action_detail(
                    status=status
                )
                payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
                self.assertEqual(payload, {"code": "WRITE_LOCKED"})
                self.assertEqual(fake.appointment_fence_calls, [])
                self.assertEqual(fake.appointment_receipt_calls, [])
                self.assertEqual(fake.cancel_calls, [])

    def test_linked_invoice_target_is_refused_before_the_fence(self):
        for overrides in (
            {"invoiced": 1},
            {"ref_sales_invoice": "SINV-1"},
            {"paid_amount": 500},
            {"insurance_coverage": "COV-1"},
            {"service_request": "SR-1"},
        ):
            with self.subTest(overrides=overrides):
                fake = gateway()
                booked_target(fake)
                fake.appointment_action_details[NATIVE_TARGET] = action_detail(
                    **overrides
                )
                payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
                self.assertEqual(payload, {"code": "WRITE_LOCKED"})
                self.assertEqual(fake.appointment_fence_calls, [])
                self.assertEqual(fake.cancel_calls, [])

    def test_patient_mismatch_is_refused_before_the_fence(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_action_details[NATIVE_TARGET] = action_detail(
            patient="PAT-2"
        )
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_static_target_is_never_writable(self):
        fake = gateway()
        booked_target(fake)
        payload = appointments_cancel_write.cancel_appointment(
            fake, body(appointment_id="APPT-1"), CSRF
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_foreign_alias_without_the_actors_receipt_is_refused(self):
        fake = gateway()
        booked_target(fake)
        payload = appointments_cancel_write.cancel_appointment(
            fake,
            body(appointment_id=alias_for("appt-book-key-0009-012345")),
            CSRF,
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_an_unknown_workspace_patient_is_denied(self):
        fake = gateway()
        booked_target(fake)
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(patient="WS-404"), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_exhausted_budget_is_refused_before_the_fence(self):
        fake = gateway()
        booked_target(fake)
        committed(fake, "appointment_cancel", "appt-cancel-key-0000-0001", "A-1")
        committed(fake, "appointment_cancel", "appt-cancel-key-0000-0002", "A-2")

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.cancel_calls, [])

    def test_an_unsafe_boundary_is_denied_before_the_fence(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_boundary_problem = "cancel_name drift"
        with self.assertRaises(Denied):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_a_locked_fence_returns_the_locked_code(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_fence_error = patient_write.WriteLocked()
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})

    def test_a_fence_refusal_of_the_locked_target_writes_no_explicit_latch(self):
        fake = gateway()
        booked_target(fake)
        # The target became terminal between the pre-fence read and the lock.
        fake.appointment_locked[NATIVE_TARGET] = {"status": "Cancelled"}
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        # The in-fence eligibility refusal is post-claim by definition and is
        # reported locked; the designed refusals above are the pre-fence ones.
        # No explicit site latch is written, which does not mean the claim is
        # resolved: the key stays DISPATCHED and blocks the site, and no call in
        # this endpoint resolves it (see FenceLedgerIntegrationTests).
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.cancel_calls, [])

    def test_a_failed_post_condition_latches_instead_of_claiming_success(self):
        fake = gateway()
        booked_target(fake)
        fake.cancel_error = patient_write.WriteUncertain()
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_missing_post_commit_detail_latches_the_deployment(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_detail_map.pop(NATIVE_TARGET)
        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.latch_calls), 1)

    def test_the_receipt_is_encoded_before_the_commit_and_the_terminal_claim(self):
        """The receipt is validated while the native write is still uncommitted."""

        fake = gateway()
        booked_target(fake)
        real_encode = appointment_write.encode_receipt
        committed_at_commit = []

        def recording_encode(**kwargs):
            fake.call_order.append("encode_receipt")
            return real_encode(**kwargs)

        def recording_commit():
            committed_at_commit.append(len(fake.appointment_fence_claim.committed))
            fake.call_order.append("commit")
            fake.commit_calls += 1

        with mock.patch.object(appointment_write, "encode_receipt", recording_encode):
            with mock.patch.object(fake, "commit", recording_commit):
                payload = appointments_cancel_write.cancel_appointment(
                    fake, body(), CSRF
                )

        self.assertEqual(payload["status"], "cancelled")
        order = fake.call_order
        self.assertLess(order.index("encode_receipt"), order.index("commit"))
        self.assertEqual(committed_at_commit, [0])
        self.assertEqual(len(fake.appointment_fence_claim.committed), 1)

    def test_a_receipt_encode_failure_never_reaches_the_native_commit(self):
        """An encoder refusal is deterministic and leaves the native write undone.

        The site commit is never called, the transaction is rolled back and the
        dispatched key has no ``COMMITTED`` receipt.
        """

        fake = gateway()
        booked_target(fake)

        with mock.patch.object(
            appointment_write,
            "encode_receipt",
            side_effect=appointment_write.AppointmentWriteError("invalid alias"),
        ):
            payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_native_commit_failure_appends_no_committed_receipt(self):
        fake = gateway()
        booked_target(fake)

        def failing_commit():
            fake.call_order.append("commit")
            raise RuntimeError("native commit failed")

        with mock.patch.object(fake, "commit", failing_commit):
            payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_rollback_failure_escapes_instead_of_returning_a_response(self):
        """A rollback that fails must never become a normal response.

        The endpoint cannot prove the partial native transaction was discarded,
        so Frappe's implicit end-of-request commit for an unsafe method could
        persist it. The failure escapes to framework cleanup instead; the fence
        key stays locked and no response is written.
        """

        fake = gateway()
        booked_target(fake)
        fake.cancel_error = patient_write.WriteUncertain()

        def rollback_boom():
            fake.rollback_calls += 1
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_boom
        with self.assertRaises(appointments_cancel_write.RollbackFailed):
            appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        # The transition failure was already reasoned about, so no ``UNKNOWN``
        # marker is written here: the key is left ``DISPATCHED`` and blocks the
        # site, and no call in this endpoint resolves it.
        self.assertEqual(fake.appointment_fence_claim.unknowns, [])

    def test_a_receipt_encode_failure_with_a_failed_rollback_escapes(self):
        """The pre-commit refusal still has to escape when rollback fails."""

        fake = gateway()
        booked_target(fake)

        def rollback_boom():
            fake.rollback_calls += 1
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_boom
        with mock.patch.object(
            appointment_write,
            "encode_receipt",
            side_effect=appointment_write.AppointmentWriteError("invalid alias"),
        ):
            with self.assertRaises(appointments_cancel_write.RollbackFailed):
                appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.commit_calls, 0)
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        self.assertEqual(len(fake.appointment_fence_claim.unknowns), 1)


class NoShowTests(unittest.TestCase):
    def test_records_a_manual_no_show_and_acknowledges_once(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_detail_map[NATIVE_TARGET]["status"] = "No Show"

        payload = appointments_cancel_write.record_no_show(
            fake, body(operation_key=NO_SHOW_KEY), CSRF
        )

        self.assertEqual(payload["status"], "no_show")
        self.assertEqual(payload["appointment"]["status"], "No Show")
        self.assertEqual(
            fake.appointment_fence_calls[0][1], "appointment_no_show"
        )
        self.assertEqual(len(fake.no_show_calls), 1)
        _state, reason, actor = fake.no_show_calls[0]
        self.assertEqual(reason, REASON)
        self.assertEqual(actor, ACTOR)
        receipt = fake.appointment_fence_claim.committed[0]
        self.assertEqual(
            appointment_write.decode_receipt(receipt).kind,
            "appointment_no_show",
        )

    def test_no_show_budget_is_a_single_operation(self):
        fake = gateway()
        booked_target(fake)
        committed(fake, "appointment_no_show", "appt-noshow-key-0000-01", "A-1")

        payload = appointments_cancel_write.record_no_show(
            fake, body(operation_key=NO_SHOW_KEY), CSRF
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])
        self.assertEqual(fake.no_show_calls, [])

    def test_a_no_show_rollback_failure_escapes_instead_of_returning_a_response(self):
        """The no-show transition shares the escape-not-respond rollback rule."""

        fake = gateway()
        booked_target(fake)
        fake.no_show_error = patient_write.WriteUncertain()

        def rollback_boom():
            fake.rollback_calls += 1
            raise RuntimeError("rollback failed")

        fake.rollback = rollback_boom
        with self.assertRaises(appointments_cancel_write.RollbackFailed):
            appointments_cancel_write.record_no_show(
                fake, body(operation_key=NO_SHOW_KEY), CSRF
            )

        self.assertEqual(fake.write_responses, [])
        self.assertEqual(fake.appointment_fence_claim.committed, [])
        self.assertEqual(fake.appointment_fence_claim.unknowns, [])

    def test_checked_in_target_is_refused_before_the_fence(self):
        fake = gateway()
        booked_target(fake)
        fake.appointment_action_details[NATIVE_TARGET] = action_detail(
            status="Checked In"
        )
        payload = appointments_cancel_write.record_no_show(
            fake, body(operation_key=NO_SHOW_KEY), CSRF
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.appointment_fence_calls, [])

    def test_wrong_role_and_missing_reason_are_pre_fence(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        booked_target(fake)
        with self.assertRaises(Denied):
            appointments_cancel_write.record_no_show(
                fake, body(operation_key=NO_SHOW_KEY), CSRF
            )
        self.assertEqual(fake.appointment_fence_calls, [])

        allowed = gateway()
        booked_target(allowed)
        payload = appointments_cancel_write.record_no_show(
            allowed, body(operation_key=NO_SHOW_KEY, reason=""), CSRF
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(allowed.appointment_fence_calls, [])


class RealLedgerFenceGateway(FakeGateway):
    """Fake native site, real temporary fence ledger.

    ``FakeClaim`` cannot show a durable ``DISPATCHED`` key, so these tests hold
    the production fence. Only the fence is real; every native call is still the
    in-memory stub.
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self._store = store

    @contextlib.contextmanager
    def appointment_write_fence(
        self, actor, kind, operation_key, payload_digest
    ):
        self.call_order.append("fence")
        self.appointment_fence_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        # Mirrors ``frappe_gateway.Gateway.appointment_write_fence`` mapping.
        try:
            with self._store.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc


class FenceLedgerIntegrationTests(unittest.TestCase):
    """A durable key, not an explicit latch, is what blocks the site.

    The endpoint writes no site-wide latch for an in-fence refusal. These tests
    use the production ledger to show the unresolved key that remains instead.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        fence_store.initialize_store(
            root / fence_store.STORE_DIRNAME, root / fence_store.ANCHOR_FILENAME
        )
        self.ledger = fence_store.open_store(
            root / fence_store.STORE_DIRNAME, root / fence_store.ANCHOR_FILENAME
        )

    def ledger_gateway(self):
        fake = _fake_gateway(RealLedgerFenceGateway, self.ledger)
        booked_target(fake)
        return fake

    def assert_site_blocked(self):
        with self.assertRaises(write_fence.SiteLocked):
            with self.ledger.fence(
                actor=ACTOR,
                kind="appointment_no_show",
                operation_key=NO_SHOW_KEY,
                payload_digest="cd" * 32,
            ) as probe:
                probe.unknown("site must have been blocked")

    def test_an_in_fence_target_refusal_leaves_the_key_dispatched(self):
        fake = self.ledger_gateway()
        # The target turned terminal between the pre-fence read and the lock.
        fake.appointment_locked[NATIVE_TARGET] = {"status": "Cancelled"}

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.write_responses, [(409, {"code": "WRITE_LOCKED"})])
        record = self.ledger.lookup(
            actor=ACTOR, kind="appointment_cancel", operation_key=CANCEL_KEY
        )
        self.assertEqual(record.state, write_fence.DISPATCHED)
        self.assertIsNone(record.receipt)
        self.assert_site_blocked()

    def test_an_in_fence_budget_refusal_leaves_the_key_dispatched(self):
        fake = self.ledger_gateway()
        # The budget fills between the pre-fence read and the re-read in fence.
        with mock.patch.object(
            appointments_cancel_write,
            "_require_budget",
            side_effect=appointment_write.AppointmentBudgetExhausted(),
        ):
            payload = appointments_cancel_write.cancel_appointment(
                fake, body(), CSRF
            )

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.latch_calls, [])
        record = self.ledger.lookup(
            actor=ACTOR, kind="appointment_cancel", operation_key=CANCEL_KEY
        )
        self.assertEqual(record.state, write_fence.DISPATCHED)
        self.assertIsNone(record.receipt)
        self.assert_site_blocked()

    def test_a_receipt_encode_failure_leaves_the_key_unknown(self):
        fake = self.ledger_gateway()

        with mock.patch.object(
            appointment_write,
            "encode_receipt",
            side_effect=appointment_write.AppointmentWriteError("invalid alias"),
        ):
            payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.latch_calls, [])
        record = self.ledger.lookup(
            actor=ACTOR, kind="appointment_cancel", operation_key=CANCEL_KEY
        )
        self.assertEqual(record.state, write_fence.UNKNOWN)
        self.assertIsNone(record.receipt)
        self.assert_site_blocked()

    def test_a_completed_cancel_persists_the_committed_terminal(self):
        fake = self.ledger_gateway()

        payload = appointments_cancel_write.cancel_appointment(fake, body(), CSRF)

        self.assertEqual(payload["status"], "cancelled")
        record = self.ledger.lookup(
            actor=ACTOR, kind="appointment_cancel", operation_key=CANCEL_KEY
        )
        self.assertEqual(record.state, write_fence.COMMITTED)
        self.assertEqual(
            appointment_write.decode_receipt(record.receipt).kind,
            "appointment_cancel",
        )
        # The operation is committed, but its reserved barrier is not: the site
        # stays blocked until the acknowledgement of that receipt.
        self.assert_site_blocked()
        self.assertEqual(
            self.ledger.acknowledge_barrier(
                actor=ACTOR,
                kind="appointment_cancel",
                operation_key=CANCEL_KEY,
                ack_receipt="ack:" + payload["ackToken"],
            ),
            "acked",
        )
        # Only now does the site admit a new operation.
        with self.ledger.fence(
            actor=ACTOR,
            kind="appointment_no_show",
            operation_key=NO_SHOW_KEY,
            payload_digest="cd" * 32,
        ) as admitted:
            admitted.unknown("probe terminated")


if __name__ == "__main__":
    unittest.main()
