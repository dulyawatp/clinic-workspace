"""Endpoint tests for the designed Patient Encounter write family.

The fake gateway proves gate ordering, strict bodies, the per-section role rule,
the pre-fence refusals (including the required-before-complete set and the
completion cascade eligibility), the fence-before-native ordering, the
closed-world cascade check and the amendment's never-rewrite guarantee.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path

from clinic_ui.core import (
    appointment_write,
    config,
    fence_store,
    patient_write,
    write_fence,
)
from clinic_ui.core import encounter_write
from clinic_ui.core.appointment_write import build_alias as build_appointment_alias
from clinic_ui.endpoints import encounters_write
from clinic_ui.runtime import frappe_gateway

from .fake_gateway import (
    Denied,
    FakeAppointmentReceipt,
    FakeEncounterOperation,
    FakeEncounterReceipt,
    FakeGateway,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "doctor@example.test"
CSRF = "csrf-token-value"
BOOK_KEY = "appt-book-key-0001-012345"
DRAFT_KEY = "enc-draft-key-0001-0123"
DRAFT_UPDATE_KEY = "enc-draft-key-0002-0123"
COMPLETE_KEY = "enc-complete-key-0001-01"
AMEND_KEY = "enc-amend-key-0001-0123"
MODIFIED = "2026-11-14 09:30:00.123456"
NOTE_MODIFIED = "2026-11-14 08:00:00.000000"
NATIVE_APPT = "NATIVE-APPT-1"
NATIVE_ENCOUNTER = "NATIVE-ENC-1"
NATIVE_NOTE = "NATIVE-NOTE-1"


def write_settings(**overrides):
    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_patients=[
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
        ],
        clinic_ui_native_appointment_write_enabled=1,
        clinic_ui_native_appointment_write_site=SITE,
        clinic_ui_appointment_medical_department="MEDDEP-1",
        clinic_ui_appointment_company="COMP-1",
        clinic_ui_appointment_type="APPT-TYPE-1",
        clinic_ui_appointment_checkin_patient="WS-1",
        clinic_ui_native_encounter_write_enabled=1,
        clinic_ui_native_encounter_write_site=SITE,
        clinic_ui_encounter_practitioner="PRAC-1",
        clinic_ui_encounter_company="COMP-1",
    )
    values.update(overrides)
    return values


def encounter_detail(**overrides):
    values = {
        "patient": "PAT-1",
        "docstatus": 0,
        "status": "Draft",
        "appointment": NATIVE_APPT,
        "appointment_status": "Open",
        "modified": MODIFIED,
        "complaint": True,
        "diagnosis_count": 1,
        "medication_codes": ("DRUG-1",),
        "lab_count": 1,
        "procedure_count": 1,
        "medication_count": 0,
        "clinical_note": NATIVE_NOTE,
        "clinical_note_modified": NOTE_MODIFIED,
        "assessment": "Acne",
        "plan": "Topical retinoid",
        "followup": "Two weeks",
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
    fake.site_today_value = "2026-11-14"
    fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail()
    fake.encounter_detail_map[NATIVE_ENCOUNTER] = {
        "patient": "PAT-1",
        "docstatus": 1,
        "status": "Completed",
        "modified": MODIFIED,
    }
    fake.appointment_action_details[NATIVE_APPT] = {
        "patient": "PAT-1",
        "appointment_date": "2026-11-14",
        "appointment_time": "09:30:00",
        "status": "Open",
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
    return fake


class RealLedgerLease:
    """Record the dispatch attempt, then defer to the production adapter."""

    def __init__(self, gateway, lease):
        self.gateway = gateway
        self.lease = lease

    def dispatch(self):
        self.gateway.call_order.append("encounter_dispatch")
        self.gateway.encounter_lease_dispatches.append(
            self.gateway.encounter_lease_calls[-1]
        )
        return self.lease.dispatch()


class RealLedgerLeaseGateway(FakeGateway):
    """Fake native site, real temporary fence ledger.

    ``FakeLease`` cannot show a durable ``DISPATCHED`` key, so these tests use the
    production ledger in a temporary directory. Only the fence is real; every
    native call is still the in-memory stub.
    """

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self._store = store

    @contextlib.contextmanager
    def encounter_write_lease(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("encounter_lease")
        self.encounter_lease_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        if self.on_encounter_lease_entered is not None:
            self.on_encounter_lease_entered(self)
        # Mirrors ``frappe_gateway.Gateway.encounter_write_lease`` mapping.
        try:
            with self._store.lease(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as lease:
                try:
                    yield RealLedgerLease(
                        self, frappe_gateway.EncounterWriteLease(lease)
                    )
                finally:
                    # Marks the end of the lease body: the fence is still held
                    # here, so any cleanup must already have been attempted.
                    self.call_order.append("encounter_lease_exit")
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

    def lookup_encounter_receipt(self, actor, kind, operation_key):
        record = self._store.lookup(
            actor=actor, kind=kind, operation_key=operation_key
        )
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def lookup_encounter_operation(self, actor, kind, operation_key):
        # Mirrors ``frappe_gateway.Gateway.lookup_encounter_operation``.
        try:
            return self._store.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc


class LeaseLedgerIntegrationTests(unittest.TestCase):
    """A refusal before ``dispatch()`` must not burn the operation key.

    The lease appends nothing until ``dispatch()``, so an authorization,
    configuration or native-data refusal taken under the held lease leaves no
    dispatched key, no event and no site latch, and every later operation still
    runs.
    """

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        root = Path(self._temp.name)
        self.store = fence_store.initialize_store(
            root / fence_store.STORE_DIRNAME, root / fence_store.ANCHOR_FILENAME
        )
        self.ledger = fence_store.open_store(
            root / fence_store.STORE_DIRNAME, root / fence_store.ANCHOR_FILENAME
        )

    def integration_gateway(self):
        fake = RealLedgerLeaseGateway(
            self.ledger,
            site=SITE,
            settings=write_settings(),
            current_session=session_view(user=ACTOR, roles=("Physician",)),
        )
        fake.site_today_value = "2026-11-14"
        # One lab row and one procedure row cascade to two Service Requests.
        fake.submitted_service_requests = 2
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail()
        fake.encounter_detail_map[NATIVE_ENCOUNTER] = {
            "patient": "PAT-1",
            "docstatus": 1,
            "status": "Completed",
            "modified": MODIFIED,
        }
        fake.appointment_action_details[NATIVE_APPT] = {
            "patient": "PAT-1",
            "appointment_date": "2026-11-14",
            "appointment_time": "09:30:00",
            "status": "Open",
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
        booked(fake)
        return fake

    def assert_no_key(self, kind, operation_key):
        self.assertIsNone(
            self.ledger.lookup(
                actor=ACTOR, kind=kind, operation_key=operation_key
            )
        )

    def seed_committed_draft(self):
        """One committed draft plus its acknowledgement, as production leaves it.

        ``claim.commit`` only resolves the operation key: the write barrier stays
        ``DISPATCHED`` until the client acknowledges, and an unresolved barrier
        blocks every new claim. The seed is therefore committed *and*
        acknowledged, or the test would prove the ack design instead of the
        refusal design.
        """

        with self.ledger.fence(
            actor=ACTOR,
            kind="encounter_draft",
            operation_key=DRAFT_KEY,
            payload_digest="ab" * 32,
        ) as claim:
            claim.commit(
                encounter_write.encode_receipt(
                    kind="encounter_draft",
                    alias=encounter_alias(),
                    native_encounter_id=NATIVE_ENCOUNTER,
                )
            )
        self.assertEqual(
            self.ledger.acknowledge_barrier(
                actor=ACTOR,
                kind="encounter_draft",
                operation_key=DRAFT_KEY,
                ack_receipt="ack:seeded",
            ),
            "acked",
        )

    def test_a_booking_type_race_leaves_no_key_and_the_next_operation_runs(self):
        fake = self.integration_gateway()
        booked(fake)
        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Open",
            "appointment_type": "APPT-TYPE-2",
        }

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assert_no_key("encounter_draft", DRAFT_KEY)

        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Open",
            "appointment_type": "APPT-TYPE-1",
        }
        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(len(fake.create_encounter_calls), 1)
        receipt = self.ledger.lookup(
            actor=ACTOR,
            kind="encounter_draft",
            operation_key="enc-draft-key-0009-0123",
        )
        self.assertEqual(receipt.state, write_fence.COMMITTED)

    def test_a_booking_status_race_leaves_no_key_and_the_next_operation_runs(self):
        fake = self.integration_gateway()
        booked(fake)
        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Closed",
            "appointment_type": "APPT-TYPE-1",
        }

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.rollback_calls, 1)
        self.assert_no_key("encounter_draft", DRAFT_KEY)

        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Checked In",
            "appointment_type": "APPT-TYPE-1",
        }
        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload["status"], "draft_saved")

    def test_a_persisted_medication_race_leaves_no_key_and_the_next_op_runs(self):
        fake = self.integration_gateway()
        self.seed_committed_draft()
        fake.encounter_locked[NATIVE_ENCOUNTER] = {"medication_count": 1}
        sequence = self.ledger.info().sequence

        payload = encounters_write._save_draft_undeferred(fake, update_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.save_encounter_calls, [])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.rollback_calls, 1)
        # A refusal before dispatch appends nothing at all: no claim, no barrier.
        self.assertEqual(self.ledger.info().sequence, sequence)
        self.assert_no_key("encounter_draft", DRAFT_UPDATE_KEY)

        fake.encounter_locked.pop(NATIVE_ENCOUNTER)
        payload = encounters_write._save_draft_undeferred(
            fake, update_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(len(fake.save_encounter_calls), 1)

    def test_an_amend_race_leaves_no_key_and_the_next_operation_runs(self):
        fake = self.integration_gateway()
        self.seed_committed_draft()
        # The finalized, completed target the pre-fence read saw is no longer
        # one: another native write changed it before this operation locked it.
        fake.encounter_locked[NATIVE_ENCOUNTER] = {
            "docstatus": 0,
            "status": "Draft",
        }

        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.addendum_calls, [])
        self.assert_no_key("encounter_amend", AMEND_KEY)

        fake.encounter_locked.pop(NATIVE_ENCOUNTER)
        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload["status"], "draft_saved")

    def test_a_completion_race_leaves_no_key_and_the_next_operation_runs(self):
        fake = self.integration_gateway()
        self.seed_committed_draft()
        fake.encounter_locked[NATIVE_ENCOUNTER] = {"medication_count": 1}

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assert_no_key("encounter_complete", COMPLETE_KEY)

        fake.encounter_locked.pop(NATIVE_ENCOUNTER)
        payload = encounters_write._complete_undeferred(
            fake, complete_body(operation_key="enc-complete-key-0009-01"), CSRF
        )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(len(fake.submit_encounter_calls), 1)

        # After the acknowledgement the site admits a further, unrelated key.
        self.assertEqual(
            self.ledger.acknowledge_barrier(
                actor=ACTOR,
                kind="encounter_complete",
                operation_key="enc-complete-key-0009-01",
                ack_receipt="ack:completed",
            ),
            "acked",
        )
        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload["status"], "draft_saved")


    def test_the_lease_is_held_across_the_locks_the_dispatch_and_the_commit(self):
        fake = self.integration_gateway()
        booked(fake)

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        order = fake.call_order
        self.assertLess(order.index("encounter_lease"), order.index("snapshot_reset"))
        self.assertLess(order.index("snapshot_reset"), order.index("appointment_lock"))
        self.assertLess(
            order.index("appointment_lock"), order.index("encounter_dispatch")
        )
        self.assertLess(order.index("encounter_dispatch"), order.index("encounter_create"))
        self.assertLess(order.index("encounter_create"), order.index("commit"))
        # Nothing drops the row locks between the lease decision and the native
        # commit, so the decision that authorised the write is still the deciding
        # one when the write lands.
        self.assertEqual(fake.rollback_calls, 0)

    def test_a_duplicate_key_with_a_different_payload_refuses_at_dispatch(self):
        fake = self.integration_gateway()
        booked(fake)
        self.seed_committed_draft()

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(len(fake.encounter_lease_dispatches), 1)
        self.assertEqual(fake.rollback_calls, 1)
        record = self.ledger.lookup(
            actor=ACTOR, kind="encounter_draft", operation_key=DRAFT_KEY
        )
        self.assertEqual(record.state, write_fence.COMMITTED)

        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload["status"], "draft_saved")

    def test_a_duplicate_key_with_the_same_payload_never_writes_twice(self):
        fake = self.integration_gateway()
        booked(fake)

        first = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(first["status"], "draft_saved")

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.create_encounter_calls), 1)
        record = self.ledger.lookup(
            actor=ACTOR, kind="encounter_draft", operation_key=DRAFT_KEY
        )
        self.assertEqual(record.state, write_fence.COMMITTED)

    def test_a_role_change_after_the_lease_is_taken_refuses_before_dispatch(self):
        fake = self.integration_gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw,
            "current_session",
            session_view(user=ACTOR, roles=("Accounts User",)),
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_a_settings_change_after_the_lease_is_taken_refuses_before_dispatch(self):
        fake = self.integration_gateway()
        booked(fake)

        def disable_encounter_write(gw):
            gw._settings["clinic_ui_native_encounter_write_enabled"] = 0

        fake.on_encounter_lease_entered = disable_encounter_write

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_a_deployment_change_after_the_lease_is_taken_refuses_before_dispatch(self):
        fake = self.integration_gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "deployment_approved", False
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_a_csrf_change_after_the_lease_is_taken_refuses_before_dispatch(self):
        fake = self.integration_gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "_session_csrf_token", "rotated-token"
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_an_uncertain_outcome_after_dispatch_keeps_the_key_and_blocks_later_ops(
        self,
    ):
        fake = self.integration_gateway()
        booked(fake)
        fake.create_encounter_error = RuntimeError("native write failed")

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.rollback_calls, 1)
        record = self.ledger.lookup(
            actor=ACTOR, kind="encounter_draft", operation_key=DRAFT_KEY
        )
        self.assertEqual(record.state, write_fence.UNKNOWN)

        # The dispatched key is never retried: the unresolved barrier blocks the
        # next operation until an operator reconciles it.
        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.create_encounter_calls), 1)


    # -- fresh-role authority guard (deny only) --------------------------

    def test_a_cached_allow_with_a_revoked_db_role_refuses_before_dispatch(self):
        """The pinned ``get_roles`` answer can outlive the Has Role row."""

        fake = self.integration_gateway()
        fake.fresh_roles = ()

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.fresh_role_calls, [ACTOR])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 0)
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_a_db_grant_never_promotes_the_cached_gated_role(self):
        """Cached NUR, DB grants Physician only: the fresh read refuses."""

        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.fresh_roles = ("Physician",)

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(
                fake,
                {
                    "patient": "WS-1",
                    "operation_key": DRAFT_UPDATE_KEY,
                    "encounter": encounter_alias(),
                    "base_modified": MODIFIED,
                    "vitals": {"temperature": "36.8"},
                },
                CSRF,
            )

        self.assertEqual(fake.fresh_role_calls, ["nurse@example.test"])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.save_encounter_calls, [])

    def test_a_db_role_that_agrees_with_the_cached_gate_still_writes(self):
        fake = self.integration_gateway()
        fake.fresh_roles = ("Physician",)

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(fake.fresh_role_calls, [ACTOR])
        self.assertEqual(len(fake.encounter_lease_dispatches), 1)
        self.assertEqual(len(fake.create_encounter_calls), 1)

    def test_two_fresh_clinic_codes_are_ambiguous_and_refuse(self):
        fake = self.integration_gateway()
        fake.fresh_roles = ("Physician", "Nurse")

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_an_unreadable_fresh_role_read_refuses_before_dispatch(self):
        for failed in ("none", "error"):
            with self.subTest(failure=failed):
                fake = self.integration_gateway()
                if failed == "none":
                    fake.fresh_roles_unreadable = True
                else:
                    fake.fresh_roles_error = RuntimeError("has role unreadable")

                with self.assertRaises(Denied):
                    encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

                self.assertEqual(fake.fresh_role_calls, [ACTOR])
                self.assertEqual(fake.encounter_lease_dispatches, [])
                self.assertEqual(fake.create_encounter_calls, [])
                self.assert_no_key("encounter_draft", DRAFT_KEY)

    def test_a_gated_role_mapped_to_an_automatic_role_fails_closed(self):
        """``Desk User`` is a framework default, never a Has Role row."""

        fake = self.integration_gateway()
        fake.current_session = session_view(user=ACTOR, roles=("Desk User",))
        fake._settings["clinic_ui_role_map"] = {"DOC": ["Desk User"]}
        fake.fresh_roles = ()

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_the_fresh_read_follows_the_snapshot_restart_and_precedes_dispatch(self):
        fake = self.integration_gateway()
        fake.fresh_roles = ("Physician",)

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        order = fake.call_order
        self.assertLess(order.index("snapshot_reset"), order.index("fresh_roles_read"))
        self.assertLess(order.index("fresh_roles_read"), order.index("encounter_dispatch"))
        # The read is the requesting actor's, never a target's owner.
        self.assertEqual(fake.fresh_role_calls, [ACTOR])

    def test_a_cross_actor_nurse_write_is_guarded_by_the_session_actor(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.fresh_roles = ()

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(
                fake,
                {
                    "patient": "WS-1",
                    "operation_key": DRAFT_UPDATE_KEY,
                    "encounter": encounter_alias(),
                    "base_modified": MODIFIED,
                    "vitals": {"temperature": "36.8"},
                },
                CSRF,
            )

        self.assertEqual(fake.fresh_role_calls, ["nurse@example.test"])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.save_encounter_calls, [])

    def test_completion_rechecks_the_fresh_role_before_dispatch(self):
        fake = self.integration_gateway()
        self.seed_committed_draft()
        fake.fresh_roles = ()

        with self.assertRaises(Denied):
            encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(fake.fresh_role_calls, [ACTOR])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assert_no_key("encounter_complete", COMPLETE_KEY)

    def test_amendment_rechecks_the_fresh_role_before_dispatch(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            docstatus=1, status="Completed"
        )
        fake.fresh_roles = ()

        with self.assertRaises(Denied):
            encounters_write._amend_undeferred(fake, amend_body(), CSRF)

        self.assertEqual(fake.fresh_role_calls, [ACTOR])
        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.addendum_calls, [])

    def test_the_gate_is_reread_from_a_restarted_snapshot_before_the_dispatch(self):
        fake = gateway()
        booked(fake)

        encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        order = fake.call_order
        gate_reads = [index for index, name in enumerate(order) if name == "gate_read"]
        self.assertGreater(len(gate_reads), 1)
        self.assertLess(order.index("encounter_lease"), order.index("snapshot_reset"))
        self.assertLess(order.index("snapshot_reset"), gate_reads[-1])
        self.assertLess(gate_reads[-1], order.index("encounter_dispatch"))

    def test_a_settings_change_is_seen_by_the_gate_after_the_snapshot_restart(self):
        """A stale read snapshot must not re-authorize a now-disabled write."""

        fake = gateway()
        booked(fake)
        fake.stale_read_snapshot = True
        fake.on_encounter_lease_entered = lambda gw: gw._settings.update(
            {"clinic_ui_native_encounter_write_enabled": 0}
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_create_authority_revoked_at_the_lease_is_refused_before_dispatch(self):
        fake = gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "encounter_can_create", False
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_booking_write_authority_revoked_at_the_lease_refuses_before_dispatch(
        self,
    ):
        fake = gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "appointment_can_write", False
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_write_authority_revoked_at_the_lease_is_refused_before_dispatch(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "encounter_can_write", False
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, update_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.save_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_clinical_side_effect_settings_flipped_at_the_lease_refuse_before_dispatch(
        self,
    ):
        fake = gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "encounter_side_effects_unsafe", True
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_target_patient_rebound_under_the_lock_blocks_completion(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)

        def rebind(gw):
            gw.encounter_locked[NATIVE_ENCOUNTER] = {"patient": "PAT-2"}
            gw.locked_appointment = {
                "patient": "PAT-2",
                "status": "Open",
                "appointment_type": "APPT-TYPE-1",
            }

        fake.on_encounter_lease_entered = rebind

        with self.assertRaises(Denied):
            encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_patient_read_authority_revoked_at_the_lease_blocks_completion(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "patient_can_read", False
        )

        with self.assertRaises(Denied):
            encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_target_patient_rebound_under_the_lock_blocks_a_amendment(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            docstatus=1, status="Completed"
        )
        fake.on_encounter_lease_entered = lambda gw: gw.encounter_locked.update(
            {NATIVE_ENCOUNTER: {"patient": "PAT-2"}}
        )

        with self.assertRaises(Denied):
            encounters_write._amend_undeferred(fake, amend_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.addendum_calls, [])
        self.assertEqual(fake.rollback_calls, 1)


    def test_an_interrupt_after_dispatch_rolls_back_under_the_lease_and_keeps_the_key(
        self,
    ):
        fake = self.integration_gateway()
        booked(fake)
        fake.create_encounter_error = KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        # The native cleanup is attempted while the lease is still held, so the
        # transaction cannot outlive the fence release.
        self.assertEqual(fake.rollback_calls, 1)
        order = fake.call_order
        self.assertLess(order.index("rollback"), order.index("encounter_lease_exit"))
        # An interrupt is not an established outcome: the key stays DISPATCHED and
        # is neither unlocked, retried nor rewritten as a known terminal state.
        record = self.ledger.lookup(
            actor=ACTOR, kind="encounter_draft", operation_key=DRAFT_KEY
        )
        self.assertEqual(record.state, write_fence.DISPATCHED)
        self.assertEqual(len(fake.create_encounter_calls), 1)

        payload = encounters_write._save_draft_undeferred(
            fake, draft_body(operation_key="enc-draft-key-0009-0123"), CSRF
        )
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(len(fake.create_encounter_calls), 1)


    def test_patient_read_revoked_at_the_lease_blocks_a_create(self):
        fake = gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw, "patient_can_read", False
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_an_epoch_secret_rotation_at_the_lease_is_refused_before_dispatch(self):
        """The ack token must come from the configuration this gate validated.

        A secret rotated under the lease would make the response token
        unverifiable by the production acknowledgement path, leaving the barrier
        ``DISPATCHED`` and the site locked, so the rotation is refused instead.
        """

        fake = gateway()
        booked(fake)
        fake.on_encounter_lease_entered = lambda gw: gw._settings.update(
            {"clinic_ui_epoch_secret": "f" * 32}
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_an_epoch_secret_rotation_at_the_lease_blocks_completion(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.on_encounter_lease_entered = lambda gw: gw._settings.update(
            {"clinic_ui_epoch_secret": "f" * 32}
        )

        with self.assertRaises(Denied):
            encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_a_nurse_who_becomes_a_doctor_loses_the_cross_actor_privilege(self):
        """The nurse cross-actor target right must not survive a role change."""

        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.on_encounter_lease_entered = lambda gw: setattr(
            gw,
            "current_session",
            session_view(user="nurse@example.test", roles=("Physician",)),
        )

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(
                fake,
                {
                    "patient": "WS-1",
                    "operation_key": DRAFT_UPDATE_KEY,
                    "encounter": encounter_alias(),
                    "base_modified": MODIFIED,
                    "vitals": {"temperature": "36.8"},
                },
                CSRF,
            )

        self.assertEqual(fake.encounter_lease_dispatches, [])
        self.assertEqual(fake.save_encounter_calls, [])

    def test_completion_locks_the_booking_without_requiring_appointment_write(self):
        """HEAD completes with encounter rights only: the lock must need read."""

        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.submitted_service_requests = 2

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(fake.read_locked_appointment_calls, [NATIVE_APPT])
        self.assertEqual(fake.locked_appointment_calls, [])

    def test_a_committed_draft_replays_from_the_real_ledger(self):
        """The durable receipt, not a re-run, is what answers a replay.

        The first request writes through the production ledger; the replay goes
        through the deferred public entry and must return the same
        acknowledgement token while appending nothing: same sequence, no new
        lease, no second native write.
        """

        fake = self.integration_gateway()
        body = draft_body()
        first = encounters_write._save_draft_undeferred(fake, body, CSRF)
        sequence = self.ledger.info().sequence
        leases = list(fake.encounter_lease_calls)
        creates = list(fake.create_encounter_calls)

        payload = encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(payload["ackToken"], first["ackToken"])
        self.assertEqual(self.ledger.info().sequence, sequence)
        self.assertEqual(fake.encounter_lease_calls, leases)
        self.assertEqual(fake.create_encounter_calls, creates)

    def test_an_unresolved_operation_answers_locked_and_appends_nothing(self):
        """A durable ``DISPATCHED`` key keeps its ``WRITE_LOCKED`` meaning.

        The eager fence answered ``409 WRITE_LOCKED`` for a key whose outcome is
        not established. The deferral must not flatten that into a generic
        denial, and must not latch, unlock, retry or append either.
        """

        fake = self.integration_gateway()
        body = draft_body()
        digest = digest_for(
            fake, "encounter_draft", validated("encounter_draft", body)
        )
        with self.assertRaises(write_fence.UnterminatedFence):
            with self.ledger.fence(
                actor=ACTOR,
                kind="encounter_draft",
                operation_key=DRAFT_KEY,
                payload_digest=digest,
            ):
                pass
        sequence = self.ledger.info().sequence

        payload = encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(self.ledger.info().sequence, sequence)
        self.assertEqual(
            self.ledger.lookup(
                actor=ACTOR, kind="encounter_draft", operation_key=DRAFT_KEY
            ).state,
            write_fence.DISPATCHED,
        )
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])


def appointment_alias(key=BOOK_KEY):
    return build_appointment_alias(SECRET.encode(), SITE, ACTOR, key)


def encounter_alias(key=DRAFT_KEY):
    return encounter_write.build_alias(SECRET.encode(), SITE, ACTOR, key)


def booked(fake):
    fake.appointment_receipt_records.append(
        FakeAppointmentReceipt(
            "book",
            ACTOR,
            BOOK_KEY,
            appointment_write.encode_receipt(
                kind="book",
                alias=appointment_alias(),
                native_appointment_id=NATIVE_APPT,
            ),
        )
    )


def committed_encounter(fake, kind, key, native_id=NATIVE_ENCOUNTER):
    fake.encounter_receipt_records.append(
        FakeEncounterReceipt(
            kind,
            ACTOR,
            key,
            encounter_write.encode_receipt(
                kind=kind,
                alias=encounter_alias(key),
                native_encounter_id=native_id,
            ),
        )
    )


def draft_body(**overrides):
    values = {
        "patient": "WS-1",
        "operation_key": DRAFT_KEY,
        "appointment": appointment_alias(),
        "complaint": "Itchy rash",
        "diagnoses": [{"diagnosis": "L20.9"}],
        "medications": [],
        "lab_tests": [{"lab_test_code": "CBC"}],
        "procedures": [{"procedure": "PROC-1"}],
        "vitals": {"temperature": "37.1", "pulse": "72"},
        "note": {
            "assessment": "Atopic dermatitis",
            "plan": "Emollient",
            "followup": "Two weeks",
        },
        "allergy_reviewed": True,
    }
    values.update(overrides)
    return values


def update_body(**overrides):
    values = draft_body(
        encounter=encounter_alias(),
        base_modified=MODIFIED,
        operation_key=DRAFT_UPDATE_KEY,
        appointment=None,
        complaint="Worse rash",
    )
    values.update(overrides)
    return values


def complete_body(**overrides):
    values = {
        "encounter": encounter_alias(),
        "operation_key": COMPLETE_KEY,
        "allergy_reviewed": True,
    }
    values.update(overrides)
    return values


def amend_body(**overrides):
    values = {
        "encounter": encounter_alias(),
        "operation_key": AMEND_KEY,
        "addendum": "Severity increased since the photo.",
    }
    values.update(overrides)
    return values


VALIDATORS = {
    "encounter_draft": encounter_write.validate_save_draft,
    "encounter_complete": encounter_write.validate_complete,
    "encounter_amend": encounter_write.validate_amend,
}


def validated(kind, body):
    """The normalised payload the endpoint derives its digest from."""

    return VALIDATORS[kind](body)


def digest_for(fake, kind, payload):
    loaded = config.load_settings(fake.setting, fake.site)
    return encounter_write.operation_digest(
        loaded.epoch_secret,
        kind=kind,
        operation_key=payload["operation_key"],
        fields=encounters_write._digest_fields(payload),
    )


class DraftTests(unittest.TestCase):
    def test_creates_a_draft_and_acknowledges_once(self):
        fake = gateway()
        booked(fake)

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(payload["encounter"]["encounter_id"], encounter_alias())
        self.assertEqual(payload["encounter"]["state"], "draft")
        self.assertNotIn("NATIVE-ENC", repr(payload))
        self.assertEqual(fake.encounter_lease_calls[0][1], "encounter_draft")
        self.assertEqual(len(fake.create_encounter_calls), 1)
        created = fake.create_encounter_calls[0]
        self.assertEqual(created["_native_patient_id"], "PAT-1")
        self.assertEqual(created["_native_appointment_id"], NATIVE_APPT)
        self.assertEqual(created["fields"]["medications"], [])
        self.assertEqual(fake.locked_appointment_calls, [NATIVE_APPT])
        self.assertEqual(fake.commit_calls, 1)
        receipt = fake.encounter_lease_claim.committed[0]
        self.assertEqual(
            encounter_write.decode_receipt(receipt).kind, "encounter_draft"
        )

    def test_the_native_commit_precedes_the_terminal_claim(self):
        fake = gateway()
        booked(fake)

        encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        order = fake.call_order
        self.assertLess(order.index("encounter_create"), order.index("commit"))
        self.assertLess(order.index("commit"), order.index("encounter_claim_commit"))

    def test_medication_rows_require_drug_code_before_the_fence(self):
        fake = gateway()
        booked(fake)
        payload = encounters_write._save_draft_undeferred(
            fake,
            draft_body(medications=[{"dosage": "1 tab"}]),
            CSRF,
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_update_requires_the_actors_committed_draft(self):
        fake = gateway()
        payload = encounters_write._save_draft_undeferred(fake, update_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_a_stale_base_modified_is_refused_inside_the_fence_without_saving(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write._save_draft_undeferred(
            fake, update_body(base_modified="2026-11-14 07:00:00.000000"), CSRF
        )
        self.assertEqual(payload, {"code": "STALE_VERSION"})
        self.assertEqual(fake.save_encounter_calls, [])

    def test_a_draft_can_be_updated_and_keeps_the_same_alias(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write._save_draft_undeferred(fake, update_body(), CSRF)
        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(payload["encounter"]["encounter_id"], encounter_alias())
        self.assertEqual(len(fake.save_encounter_calls), 1)

    def test_a_medication_bearing_draft_is_refused_before_the_fence(self):
        """Drug Prescription.dosage_form is installed required and unsupplied.

        Until a reviewed medication contract exists the row set is refused before
        the fence, so nothing is dispatched and no site latch is possible.
        """

        fake = gateway()
        booked(fake)

        payload = encounters_write._save_draft_undeferred(
            fake,
            draft_body(
                medications=[
                    {"drug_code": "DRUG-1", "dosage": "1 tab", "period": "7 days"}
                ]
            ),
            CSRF,
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_medication_bearing_update_is_refused_before_the_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)

        payload = encounters_write._save_draft_undeferred(
            fake, update_body(medications=[{"drug_code": "DRUG-1"}]), CSRF
        )

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.save_encounter_calls, [])

    def test_a_mid_flight_appointment_type_change_refuses_the_write(self):
        """The type is derived pre-fence and revalidated on the locked row."""

        fake = gateway()
        booked(fake)
        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Open",
            "appointment_type": "APPT-TYPE-2",
        }

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.locked_appointment_calls, [NATIVE_APPT])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_mid_flight_patient_change_on_the_booking_refuses_the_write(self):
        fake = gateway()
        booked(fake)
        fake.locked_appointment = {
            "patient": "PAT-2",
            "status": "Open",
            "appointment_type": "APPT-TYPE-1",
        }

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_mid_flight_status_change_on_the_booking_is_refused(self):
        fake = gateway()
        booked(fake)
        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Closed",
            "appointment_type": "APPT-TYPE-1",
        }

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.create_encounter_calls, [])

    def test_an_unreadable_locked_booking_refuses_the_write(self):
        fake = gateway()
        booked(fake)
        fake.locked_appointment = None

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(fake.create_encounter_calls, [])

    def test_an_update_carrying_persisted_medication_rows_is_refused(self):
        """A native user may have left medication rows on the draft.

        The contract cannot rewrite or delete them, so the target is refused
        before the fence instead of silently dropping the rows.
        """

        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            medication_count=1
        )

        payload = encounters_write._save_draft_undeferred(fake, update_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.save_encounter_calls, [])

    def test_medication_rows_appearing_after_the_pre_fence_read_are_refused(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_locked[NATIVE_ENCOUNTER] = {"medication_count": 1}

        payload = encounters_write._save_draft_undeferred(fake, update_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.save_encounter_calls, [])

    def test_a_note_request_needs_clinical_note_create_authority(self):
        """DOC holds no Clinical Note create right on the installed site.

        The note is a separate document inserted after the encounter write, so a
        refused insert would leave the operation UNKNOWN and latch the site. The
        refusal must therefore happen before the fence.
        """

        fake = gateway()
        booked(fake)
        fake.clinical_note_can_create = False

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_an_update_with_a_note_is_refused_the_same_way(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.clinical_note_can_create = False

        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, update_body(), CSRF)
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_a_draft_without_a_note_still_saves(self):
        fake = gateway()
        booked(fake)
        fake.clinical_note_can_create = False

        payload = encounters_write._save_draft_undeferred(fake, draft_body(note=None), CSRF)

        self.assertEqual(payload["status"], "draft_saved")

    def test_a_create_derives_the_linked_appointment_type(self):
        fake = gateway()
        booked(fake)

        encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(
            fake.create_encounter_calls[0]["_appointment_type"], "APPT-TYPE-1"
        )

    def test_a_missing_linked_appointment_type_is_refused_before_the_fence(self):
        fake = gateway()
        booked(fake)
        fake.encounter_appointment_type = None

        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.create_encounter_calls, [])

    def test_a_draft_needs_an_open_linked_appointment(self):
        fake = gateway()
        booked(fake)
        fake.appointment_action_details[NATIVE_APPT]["status"] = "Scheduled"
        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_install_preflight_and_boundary_fail_closed_before_validation(self):
        fake = gateway()
        booked(fake)
        fake.encounter_install_problem = "missing field Drug Prescription.drug_code"
        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(fake.encounter_lease_calls, [])

        other = gateway()
        booked(other)
        other.encounter_boundary_problem = "versions drift for healthcare"
        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(other, draft_body(), CSRF)
        self.assertEqual(other.encounter_lease_calls, [])

    def test_exhausted_draft_budget_is_refused_before_the_fence(self):
        fake = gateway()
        booked(fake)
        committed_encounter(fake, "encounter_draft", "enc-draft-key-0000-0001")
        committed_encounter(fake, "encounter_draft", "enc-draft-key-0000-0002")
        payload = encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_an_unreviewed_role_is_denied(self):
        fake = gateway(
            current_session=session_view(user="cashier@example.test", roles=("Accounts User",))
        )
        with self.assertRaises(Denied):
            encounters_write._save_draft_undeferred(fake, draft_body(), CSRF)
        self.assertEqual(fake.encounter_lease_calls, [])


class NurseSectionTests(unittest.TestCase):
    def nurse_gateway(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        return fake

    def test_nurse_may_save_vitals_only(self):
        fake = self.nurse_gateway()
        payload = encounters_write._save_draft_undeferred(
            fake,
            {
                "patient": "WS-1",
                "operation_key": DRAFT_UPDATE_KEY,
                "encounter": encounter_alias(),
                "base_modified": MODIFIED,
                "vitals": {"temperature": "36.8"},
            },
            CSRF,
        )
        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(len(fake.save_encounter_calls), 1)

    def test_nurse_may_not_write_notes_diagnoses_or_orders(self):
        for field, value in (
            ("note", {"assessment": "A"}),
            ("diagnoses", [{"diagnosis": "L20.9"}]),
            ("medications", [{"drug_code": "DRUG-1"}]),
            ("lab_tests", [{"lab_test_code": "CBC"}]),
            ("procedures", [{"procedure": "PROC-1"}]),
            ("complaint", "Rash"),
        ):
            with self.subTest(field=field):
                fake = self.nurse_gateway()
                with self.assertRaises(Denied):
                    encounters_write._save_draft_undeferred(
                        fake,
                        update_body(**{field: value}),
                        CSRF,
                    )
                self.assertEqual(fake.encounter_lease_calls, [])


class CompleteTests(unittest.TestCase):
    def ready(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        return fake

    def test_completes_and_verifies_the_expected_cascade(self):
        fake = self.ready()
        fake.submitted_service_requests = 2
        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["encounter"]["state"], "completed")
        self.assertEqual(len(fake.submit_encounter_calls), 1)
        self.assertEqual(fake.encounter_lease_calls[0][1], "encounter_complete")
        self.assertEqual(fake.commit_calls, 1)
        order = fake.call_order
        self.assertLess(order.index("encounter_lease"), order.index("snapshot_reset"))
        self.assertLess(order.index("encounter_lock"), order.index("appointment_lock"))
        self.assertLess(
            order.index("appointment_lock"), order.index("encounter_dispatch")
        )
        self.assertLess(order.index("encounter_dispatch"), order.index("encounter_submit"))
        self.assertLess(order.index("encounter_submit"), order.index("commit"))

    def test_a_missing_appointment_linkage_is_refused_pre_fence(self):
        fake = self.ready()
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            appointment=None, appointment_status=None
        )
        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_an_appointment_outside_open_or_checked_in_is_refused_pre_fence(self):
        for status in ("Scheduled", "Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                fake = self.ready()
                fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
                    appointment_status=status
                )
                payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
                self.assertEqual(payload, {"code": "WRITE_LOCKED"})
                self.assertEqual(fake.encounter_lease_calls, [])
                self.assertEqual(fake.submit_encounter_calls, [])

    def test_a_draft_carrying_persisted_medication_rows_cannot_complete(self):
        fake = self.ready()
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            medication_count=1
        )

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_medication_rows_appearing_after_the_pre_fence_read_block_completion(self):
        fake = self.ready()
        fake.encounter_locked[NATIVE_ENCOUNTER] = {"medication_count": 1}

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_an_encounter_unlinked_after_the_pre_fence_read_is_refused(self):
        fake = self.ready()
        fake.encounter_locked[NATIVE_ENCOUNTER] = {"appointment": None}

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.locked_appointment_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_a_booking_change_under_the_lock_blocks_completion(self):
        fake = self.ready()
        fake.locked_appointment = {
            "patient": "PAT-1",
            "status": "Closed",
            "appointment_type": "APPT-TYPE-1",
        }

        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.submit_encounter_calls, [])

    def test_the_required_before_complete_set_is_enforced_pre_fence(self):
        for field, value in (
            ("complaint", False),
            ("diagnosis_count", 0),
            ("assessment", ""),
            ("plan", ""),
            ("followup", ""),
        ):
            with self.subTest(field=field):
                fake = self.ready()
                fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
                    **{field: value}
                )
                payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
                self.assertEqual(payload, {"code": "VALIDATION"})
                self.assertEqual(fake.encounter_lease_calls, [])

    def test_the_allergy_acknowledgement_is_required(self):
        fake = self.ready()
        payload = encounters_write._complete_undeferred(
            fake, complete_body(allergy_reviewed=False), CSRF
        )
        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_a_cascade_mismatch_rolls_back_and_does_not_commit(self):
        fake = self.ready()
        fake.submitted_service_requests = 1
        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_claim.committed, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_a_nurse_never_completes(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        with self.assertRaises(Denied):
            encounters_write._complete_undeferred(fake, complete_body(), CSRF)
        self.assertEqual(fake.encounter_lease_calls, [])

    def test_complete_budget_is_single(self):
        fake = self.ready()
        committed_encounter(fake, "encounter_complete", "enc-complete-key-0000-01")
        payload = encounters_write._complete_undeferred(fake, complete_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])


class AmendTests(unittest.TestCase):
    def finalized(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            docstatus=1, status="Completed"
        )
        return fake

    def test_creates_an_addendum_and_never_rewrites_the_original(self):
        fake = self.finalized()
        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)
        self.assertEqual(payload["status"], "amended")
        self.assertTrue(payload["addendum"].startswith("addendum_"))
        state, note_text, actor = fake.addendum_calls[0]
        self.assertIn("[clinic-ui amendment]", note_text)
        self.assertEqual(actor, ACTOR)
        # The preimage of the original note is what the gateway re-verifies.
        self.assertEqual(
            fake.amend_facts_calls[0][4], NOTE_MODIFIED
        )
        self.assertEqual(fake.encounter_lease_calls[0][1], "encounter_amend")
        self.assertEqual(fake.commit_calls, 1)
        order = fake.call_order
        self.assertLess(order.index("encounter_lease"), order.index("snapshot_reset"))
        self.assertLess(
            order.index("encounter_lock"), order.index("encounter_dispatch")
        )
        self.assertLess(
            order.index("encounter_dispatch"), order.index("encounter_addendum")
        )
        self.assertLess(order.index("encounter_addendum"), order.index("commit"))

    def test_the_never_rewrite_preimage_comes_from_the_locked_row(self):
        """A stale pre-fence read must not turn an ordinary edit into UNKNOWN."""

        fake = self.finalized()
        newer = "2026-11-14 11:00:00.000000"
        newer_note_modified = "2026-11-14 10:30:00.000000"
        fake.encounter_locked[NATIVE_ENCOUNTER] = {
            "modified": newer,
            "clinical_note": "NATIVE-NOTE-2",
            "clinical_note_modified": newer_note_modified,
        }
        fake.amend_facts = {"encounter_modified": newer}

        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)

        self.assertEqual(payload["status"], "amended")
        facts_call = fake.amend_facts_calls[0]
        self.assertEqual(facts_call[3], "NATIVE-NOTE-2")
        self.assertEqual(facts_call[4], newer_note_modified)

    def test_a_draft_is_not_amendable_pre_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.addendum_calls, [])

    def test_a_rewritten_original_note_is_detected_and_not_committed(self):
        fake = self.finalized()
        fake.amend_facts = {
            "clinical_note_modified": "2026-11-14 09:59:00.000000"
        }
        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_claim.committed, [])
        self.assertEqual(fake.rollback_calls, 1)

    def test_amend_budget_is_single(self):
        fake = self.finalized()
        committed_encounter(fake, "encounter_amend", "enc-amend-key-0000-0001")
        payload = encounters_write._amend_undeferred(fake, amend_body(), CSRF)
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(fake.encounter_lease_calls, [])


class DeferredWriteTests(unittest.TestCase):
    """New encounter mutations are refused before any fence or native write.

    The capability map publishes the encounter draft, completion, amendment and
    lab-ordering ids ``read_only``; these tests prove a direct caller of the API
    gets the same answer. Every case passes the read gate, the explicit
    same-session CSRF check and the enabled encounter deployment gate first, so
    the refusal is the deferral and not a gate artefact.
    """

    def assert_refused_without_dispatch(self, fake, call):
        with self.assertRaises(Denied):
            call()
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.save_encounter_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assertEqual(fake.addendum_calls, [])
        self.assertEqual(fake.encounter_lease_claim.committed, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_new_draft_is_refused_before_the_fence(self):
        fake = gateway()
        booked(fake)

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.save_draft(fake, draft_body(), CSRF)
        )

        # The explicit same-session CSRF check still runs ahead of the refusal.
        self.assertEqual(fake.csrf_supplied, [CSRF])

    def test_a_draft_update_is_refused_before_the_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.save_draft(fake, update_body(), CSRF)
        )

    def test_a_completion_is_refused_before_the_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.complete(fake, complete_body(), CSRF)
        )

    def test_an_amendment_is_refused_before_the_fence(self):
        fake = gateway()
        committed_encounter(fake, "encounter_draft", DRAFT_KEY)
        fake.encounter_details[NATIVE_ENCOUNTER] = encounter_detail(
            docstatus=1, status="Completed"
        )

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.amend(fake, amend_body(), CSRF)
        )

    def test_a_lab_bearing_draft_is_refused_before_the_fence(self):
        """Lab rows ride the draft payload and never reach the fence."""

        fake = gateway()
        booked(fake)

        self.assert_refused_without_dispatch(
            fake,
            lambda: encounters_write.save_draft(
                fake, draft_body(lab_tests=[{"lab_test_code": "CBC"}]), CSRF
            ),
        )

    def test_a_nurse_draft_is_refused_before_the_fence(self):
        fake = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        booked(fake)

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.save_draft(fake, draft_body(), CSRF)
        )

    def test_a_bad_csrf_token_refuses_without_dispatching(self):
        fake = gateway()
        booked(fake)

        self.assert_refused_without_dispatch(
            fake, lambda: encounters_write.save_draft(fake, draft_body(), "wrong")
        )


class CommittedReplayTests(unittest.TestCase):
    """An exact replay of the caller's own committed operation still answers.

    Nothing new dispatches, mutates, latches, unlocks or retries. A terminal
    ``COMMITTED`` record for this actor, kind, operation key, payload digest and
    alias rebuilds the same body and the same acknowledgement token; this actor's
    own ``DISPATCHED``/``UNKNOWN`` key keeps the eager fence's
    ``409 WRITE_LOCKED`` answer; every other key is refused.
    """

    def committed(self, fake, kind, body, *, state="COMMITTED", digest=None,
                  actor=ACTOR, alias=None, native_id=NATIVE_ENCOUNTER):
        payload = validated(kind, body)
        key = payload["operation_key"]
        stored_alias = alias
        if stored_alias is None:
            stored_alias = payload["encounter"] or encounter_alias(key)
        if digest is None:
            digest = digest_for(fake, kind, payload)
        receipt = encounter_write.encode_receipt(
            kind=kind, alias=stored_alias, native_encounter_id=native_id
        )
        fake.encounter_operations[(actor, kind, key)] = FakeEncounterOperation(
            state, digest, receipt
        )
        return receipt

    def expected_ack(self, fake, kind, body, receipt):
        payload = validated(kind, body)
        loaded = config.load_settings(fake.setting, fake.site)
        return encounter_write.ack_token(
            loaded.epoch_secret,
            fake.site,
            ACTOR,
            kind,
            payload["operation_key"],
            receipt,
        )

    def assert_no_dispatch(self, fake):
        self.assertEqual(fake.encounter_lease_calls, [])
        self.assertEqual(fake.create_encounter_calls, [])
        self.assertEqual(fake.save_encounter_calls, [])
        self.assertEqual(fake.submit_encounter_calls, [])
        self.assertEqual(fake.addendum_calls, [])
        self.assertEqual(fake.commit_calls, 0)

    def test_a_committed_draft_create_replays_its_own_ack_token(self):
        fake = gateway()
        body = draft_body()
        receipt = self.committed(
            fake, "encounter_draft", body, actor=ACTOR
        )

        payload = encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(
            payload["ackToken"], self.expected_ack(fake, "encounter_draft", body, receipt)
        )
        self.assertEqual(payload["encounter"]["encounter_id"], encounter_alias())
        self.assertNotIn("NATIVE-ENC", repr(payload))
        self.assert_no_dispatch(fake)
        self.assertEqual(fake.encounter_ack_calls, [])

    def test_a_committed_draft_update_replays(self):
        fake = gateway()
        body = update_body()
        receipt = self.committed(fake, "encounter_draft", body)

        payload = encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(payload["status"], "draft_saved")
        self.assertEqual(
            payload["ackToken"], self.expected_ack(fake, "encounter_draft", body, receipt)
        )
        self.assert_no_dispatch(fake)

    def test_a_committed_completion_replays(self):
        fake = gateway()
        body = complete_body()
        receipt = self.committed(fake, "encounter_complete", body)

        payload = encounters_write.complete(fake, body, CSRF)

        self.assertEqual(payload["status"], "completed")
        self.assertEqual(
            payload["ackToken"],
            self.expected_ack(fake, "encounter_complete", body, receipt),
        )
        self.assert_no_dispatch(fake)

    def test_a_committed_amendment_replays_its_addendum_reference(self):
        fake = gateway()
        body = amend_body()
        receipt = self.committed(fake, "encounter_amend", body)

        payload = encounters_write.amend(fake, body, CSRF)

        self.assertEqual(payload["status"], "amended")
        self.assertEqual(payload["addendum"], f"addendum_{AMEND_KEY}")
        self.assertEqual(
            payload["ackToken"],
            self.expected_ack(fake, "encounter_amend", body, receipt),
        )
        self.assert_no_dispatch(fake)

    def test_the_replayed_token_is_the_one_acknowledge_accepts(self):
        fake = gateway()
        body = complete_body()
        receipt = self.committed(fake, "encounter_complete", body)

        payload = encounters_write.complete(fake, body, CSRF)
        token = payload["ackToken"]

        self.assertTrue(encounter_write.is_valid_ack_token(token))
        self.assertEqual(
            token,
            encounter_write.ack_token(
                config.load_settings(fake.setting, fake.site).epoch_secret,
                fake.site,
                ACTOR,
                "encounter_complete",
                COMPLETE_KEY,
                receipt,
            ),
        )
        self.assertEqual(
            encounters_write.acknowledge(
                fake,
                {
                    "operation_key": COMPLETE_KEY,
                    "kind": "encounter_complete",
                    "ack_token": token,
                },
                CSRF,
            ),
            {"acknowledged": True},
        )

    def test_a_different_payload_on_the_same_key_is_refused(self):
        fake = gateway()
        self.committed(fake, "encounter_draft", draft_body(), digest="ab" * 32)

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, draft_body(complaint="Other"), CSRF)

        self.assert_no_dispatch(fake)

    def test_another_actors_key_is_refused(self):
        fake = gateway()
        body = draft_body()
        self.committed(
            fake, "encounter_draft", body, actor="other@example.test"
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_a_receipt_for_another_alias_is_refused(self):
        fake = gateway()
        body = update_body()
        self.committed(
            fake, "encounter_draft", body, alias=encounter_alias(DRAFT_UPDATE_KEY)
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_an_unreadable_store_is_refused(self):
        fake = gateway()
        body = draft_body()
        fake.encounter_operations[(ACTOR, "encounter_draft", DRAFT_KEY)] = (
            patient_write.StoreUnavailable()
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_a_malformed_replay_body_is_refused_before_any_lookup(self):
        fake = gateway()

        payload = encounters_write.save_draft(fake, {"patient": "WS-1"}, CSRF)

        self.assertEqual(payload, {"code": "VALIDATION"})
        self.assertEqual(fake.encounter_operation_calls, [])
        self.assert_no_dispatch(fake)


    def test_a_dispatched_replay_answers_locked_without_latching(self):
        """The eager fence answered ``WRITE_LOCKED``; so does a replay.

        The key is the caller's own and its outcome is not established, so the
        refusal keeps that meaning instead of collapsing into a generic denial.
        Nothing is latched, unlocked, retried or appended.
        """

        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body, state="DISPATCHED")

        payload = encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_an_unknown_replay_answers_locked_without_latching(self):
        fake = gateway()
        body = complete_body()
        self.committed(fake, "encounter_complete", body, state="UNKNOWN")

        payload = encounters_write.complete(fake, body, CSRF)

        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_a_replay_is_refused_when_the_fresh_role_disagrees(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        # The stored Has Role rows no longer resolve to the gated role.
        fake.fresh_roles = ("Nurse",)

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(fake.fresh_role_calls, [ACTOR])
        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])

    def test_a_replay_is_refused_when_the_fresh_role_read_fails(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.fresh_roles_unreadable = True

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_the_replay_authority_reread_follows_the_snapshot_reset(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)

        encounters_write.save_draft(fake, body, CSRF)

        order = fake.call_order
        self.assertLess(
            order.index("config_refresh"), order.index("snapshot_reset")
        )
        self.assertLess(
            order.index("snapshot_reset"), order.index("fresh_roles_read")
        )

    def test_a_replay_is_refused_when_the_native_detail_is_missing(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.native_encounter_detail = lambda native_id: None

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])

    def test_a_replay_needs_the_native_patient_read(self):
        """The replayed body carries the patient, so its read must be authorised.

        A revoked Patient read is a refusal before any answer: no latch, no
        dispatch, no event, no commit and no ledger mutation.
        """

        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        ledger_before = dict(fake.encounter_operations)
        fake.patient_can_read = False

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assertEqual(fake.patient_read_calls, ["PAT-1"])
        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])
        self.assertEqual(fake.operation_latch_calls, [])
        self.assertEqual(fake.encounter_ack_calls, [])
        self.assertEqual(fake.encounter_operations, ledger_before)


    def test_a_replay_with_no_native_patient_id_refuses_without_a_role_check(self):
        """A blank id must not devolve into the role-level ``doc=None`` answer."""

        for patient in (None, "", "   "):
            with self.subTest(patient=patient):
                fake = gateway()
                body = draft_body()
                self.committed(fake, "encounter_draft", body)
                fake.encounter_detail_map[NATIVE_ENCOUNTER] = {
                    "patient": patient,
                    "docstatus": 0,
                    "status": "Draft",
                    "modified": MODIFIED,
                }

                with self.assertRaises(Denied):
                    encounters_write.save_draft(fake, body, CSRF)

                self.assertEqual(fake.patient_read_calls, [])
                self.assert_no_dispatch(fake)
                self.assertEqual(fake.latch_calls, [])
                self.assertEqual(fake.operation_latch_calls, [])
                self.assertEqual(fake.encounter_ack_calls, [])

    def test_a_replay_uses_the_refreshed_patient_allowlist(self):
        """The response is built from the re-read settings, not the stale ones.

        The patient is dropped from the allowlist between the two gate reads.
        ``_success`` maps the native patient through the allowlist, so a replay
        that kept the first read would answer with a patient the site no longer
        maps; it must refuse instead.
        """

        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.on_snapshot_reset = lambda gw: gw._settings.update(
            clinic_ui_synthetic_patients=[]
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)
        self.assertEqual(fake.latch_calls, [])

    def test_a_replay_is_refused_when_the_session_user_changes(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.on_snapshot_reset = lambda gw: setattr(
            gw,
            "current_session",
            session_view(user="other@example.test", roles=("Physician",)),
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_a_replay_is_refused_when_the_gated_role_changes(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.on_snapshot_reset = lambda gw: setattr(
            gw,
            "current_session",
            session_view(user=ACTOR, roles=("Nurse",)),
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)

    def test_a_replay_is_refused_when_the_epoch_secret_changes(self):
        fake = gateway()
        body = draft_body()
        self.committed(fake, "encounter_draft", body)
        fake.on_snapshot_reset = lambda gw: gw._settings.update(
            clinic_ui_epoch_secret="f" * 32
        )

        with self.assertRaises(Denied):
            encounters_write.save_draft(fake, body, CSRF)

        self.assert_no_dispatch(fake)


class AckTests(unittest.TestCase):
    def test_the_acknowledgement_binds_the_encounter_kind(self):
        fake = gateway()
        payload = encounters_write.acknowledge(
            fake,
            {
                "operation_key": COMPLETE_KEY,
                "kind": "encounter_complete",
                "ack_token": "a" * 64,
            },
            CSRF,
        )
        self.assertEqual(payload, {"acknowledged": True})
        self.assertEqual(fake.encounter_ack_calls[0][1], "encounter_complete")


if __name__ == "__main__":
    unittest.main()
