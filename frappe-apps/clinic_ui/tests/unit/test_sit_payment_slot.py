import os
import tempfile
import unittest
from types import SimpleNamespace

from clinic_ui.core import deployment_marker, finance_contract
from clinic_ui.runtime import sit_payment_slot as slot


class Database:
    def __init__(self, row=None):
        self.row = row
        self.calls = []
        self._cursor = SimpleNamespace(rowcount=0)

    def sql(self, query, values=None, **kwargs):
        self.calls.append((query, values))
        if query.strip().upper() == "SELECT ROW_COUNT()":
            return [(self._cursor.rowcount,)]
        if query.lstrip().upper().startswith("UPDATE"):
            self._cursor.rowcount = 1 if self.row and self.row["state"] == "available" else 0
            if self._cursor.rowcount:
                self.row = dict(self.row, state="spent", actor=values[0], operation_key=values[1], receipt=values[2])
            return []
        return [dict(deployment_id=slot.DEPLOYMENT_ID, **self.row)] if self.row else []


class SlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Database()
        self.gateway = SimpleNamespace(site="marley-sit.local", frappe=SimpleNamespace(
            db=self.db, get_site_path=lambda *parts: os.path.join(self.tmp.name, *parts)))

    def approve(self):
        path = os.path.join(self.tmp.name, "private", slot.PROOF_DIRECTORY)
        os.makedirs(path)
        deployment_marker.write_marker(path, slot.SITE, slot.DEPLOYMENT_ID)

    def test_identity_requires_private_proof_and_exact_site(self):
        self.assertIsNone(slot.shared_sit_payment_identity(self.gateway))
        self.approve()
        self.assertEqual(slot.shared_sit_payment_identity(self.gateway), slot.DEPLOYMENT_ID)
        self.gateway.site = "another.local"
        self.assertIsNone(slot.shared_sit_payment_identity(self.gateway))

    def test_wrong_marker_identity_is_rejected(self):
        path = os.path.join(self.tmp.name, "private", slot.PROOF_DIRECTORY)
        os.makedirs(path)
        deployment_marker.write_marker(path, slot.SITE, "different-deployment")
        self.assertIsNone(slot.shared_sit_payment_identity(self.gateway))

    def test_missing_and_wrong_deployment_fail_without_insert(self):
        self.approve()
        with self.assertRaises(slot.SlotUnavailable):
            slot.read_slot(self.gateway, "different-deployment")
        with self.assertRaises(slot.SlotUnavailable):
            slot.lock_slot(self.gateway, slot.DEPLOYMENT_ID)
        self.assertFalse(any("INSERT" in q.upper() for q, _ in self.db.calls))

    def test_fresh_slot_locks_and_consumes_without_commit(self):
        self.approve()
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        self.assertEqual(slot.read_slot(self.gateway, slot.DEPLOYMENT_ID)["state"], "available")
        self.assertEqual(slot.lock_slot(self.gateway, slot.DEPLOYMENT_ID)["state"], "available")
        self.assertIn("FOR UPDATE", self.db.calls[-1][0])
        slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "operation", "PAY-1")
        self.assertEqual(self.db.row["receipt"], "PAY-1")
        with self.assertRaises(slot.SlotUnavailable):
            slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "operation", "PAY-2")

    def test_spent_slot_is_visible_but_cannot_lock_for_posting(self):
        self.approve()
        self.db.row = {"state": "spent", "actor": "a", "operation_key": "k", "receipt": "PAY-1"}
        self.assertEqual(slot.read_slot(self.gateway, slot.DEPLOYMENT_ID)["receipt"], "PAY-1")
        with self.assertRaises(slot.SlotUnavailable):
            slot.lock_slot(self.gateway, slot.DEPLOYMENT_ID)

    def test_history_reconciliation_fails_closed(self):
        self.assertEqual(slot.initial_state("confirmed_payment"), "spent")
        self.assertEqual(slot.initial_state("confirmed_empty"), "available")
        for history in ("unavailable", "unresolved", None, "unknown"):
            with self.subTest(history=history), self.assertRaises(slot.SlotUnavailable):
                slot.initial_state(history)

    def test_consume_requires_exactly_one_updated_row(self):
        self.approve()
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        sql = self.db.sql

        def no_update(query, values=None, **kwargs):
            result = sql(query, values, **kwargs)
            if query.lstrip().upper().startswith("UPDATE"):
                self.db._cursor.rowcount = 0
            return result

        self.db.sql = no_update
        with self.assertRaises(slot.SlotUnavailable):
            slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "key", "PAY-1")

    def test_available_row_with_any_metadata_is_invalid(self):
        self.approve()
        for field in ("actor", "operation_key", "receipt"):
            with self.subTest(field=field):
                self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
                self.db.row[field] = ""
                for action in (slot.read_slot, slot.lock_slot):
                    with self.assertRaises(slot.SlotUnavailable):
                        action(self.gateway, slot.DEPLOYMENT_ID)
                with self.assertRaises(slot.SlotUnavailable):
                    slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "key", "PAY-1")

    def test_nonexact_returned_deployment_key_is_rejected(self):
        self.approve()
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        sql = self.db.sql
        for wrong in (slot.DEPLOYMENT_ID.upper(), slot.DEPLOYMENT_ID + " "):
            def mismatched(query, values=None, **kwargs):
                rows = sql(query, values, **kwargs)
                if rows:
                    rows[0]["deployment_id"] = wrong
                return rows
            self.db.sql = mismatched
            with self.subTest(wrong=wrong), self.assertRaises(slot.SlotUnavailable):
                slot.lock_slot(self.gateway, slot.DEPLOYMENT_ID)

    def test_update_guards_exact_identity_and_empty_metadata(self):
        self.approve()
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "key", "PAY-1")
        update = next(q for q, _ in self.db.calls if q.startswith("UPDATE"))
        select = next(q for q, _ in self.db.calls if "FOR UPDATE" in q)
        self.assertIn("BINARY deployment_id = BINARY %s", select)
        self.assertIn("BINARY deployment_id = BINARY %s", update)
        for field in ("actor", "operation_key", "receipt"):
            self.assertIn(field + " IS NULL", update)

    def test_spent_legacy_row_accepts_null_metadata(self):
        self.approve()
        self.db.row = {"state": "spent", "actor": None, "operation_key": None, "receipt": None}
        self.assertEqual(slot.read_slot(self.gateway, slot.DEPLOYMENT_ID)["state"], "spent")

    def test_unreadable_row_count_fails_closed(self):
        self.approve()
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        sql = self.db.sql

        def unreadable(query, values=None, **kwargs):
            if query == "SELECT ROW_COUNT()":
                return [("1",)]
            return sql(query, values, **kwargs)

        self.db.sql = unreadable
        with self.assertRaises(slot.SlotUnavailable):
            slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "key", "PAY-1")

    def test_slot_ack_verifies_persisted_receipt_idempotently_without_writes(self):
        self.approve()
        secret = b"x" * 32
        key = "finance-write-key-0001"
        receipt = finance_contract.encode_receipt(
            kind=finance_contract.PAYMENT_KIND, reference="PE-1", invoice="INV-1",
            customer="CUST-1", amount=20, mode_of_payment="Cash", status="Paid",
        )
        self.db.row = {"state": "spent", "actor": "actor", "operation_key": key, "receipt": receipt}
        token = finance_contract.ack_token(secret, self.gateway.site, "actor", finance_contract.PAYMENT_KIND, key, receipt)
        for _ in range(2):
            self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "actor", key, token), "acked")
        self.assertEqual(self.db.row["state"], "spent")
        self.assertTrue(all(q.lstrip().upper().startswith("SELECT") for q, _ in self.db.calls))
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "other", key, token), "invalid")
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "actor", key, "0" * 64), "invalid")
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "actor", key, "é" * 64), "invalid")
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "actor", "another-operation-key", token), "not_owned")
        self.db.row["receipt"] = "malformed"
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, secret, "actor", key, token), "invalid")

    def test_slot_ack_rejects_invoice_receipt(self):
        self.approve()
        key = "finance-write-key-0001"
        receipt = finance_contract.encode_receipt(
            kind=finance_contract.INVOICE_KIND, reference="INV-1", invoice="INV-1",
            customer="CUST-1", amount=20, status="Submitted",
        )
        self.db.row = {"state": "spent", "actor": "actor", "operation_key": key, "receipt": receipt}
        self.assertEqual(slot.verify_slot_ack(self.gateway, slot.DEPLOYMENT_ID, b"x" * 32, "actor", key, "0" * 64), "invalid")

    def test_slot_ack_no_owned_operation_and_unavailable(self):
        self.approve()
        key = "finance-write-key-0001"
        args = (self.gateway, slot.DEPLOYMENT_ID, b"x" * 32, "actor", key, "0" * 64)
        self.db.row = {"state": "available", "actor": None, "operation_key": None, "receipt": None}
        self.assertEqual(slot.verify_slot_ack(*args), "not_owned")
        self.db.row = {"state": "spent", "actor": None, "operation_key": None, "receipt": None}
        self.assertEqual(slot.verify_slot_ack(*args), "not_owned")
        self.gateway.site = "other.local"
        with self.assertRaises(slot.SlotUnavailable):
            slot.verify_slot_ack(*args)
        self.gateway.site = slot.SITE
        self.db.row = None
        with self.assertRaises(slot.SlotUnavailable):
            slot.verify_slot_ack(*args)
        def broken(*args, **kwargs):
            raise RuntimeError("unreadable")
        self.db.sql = broken
        with self.assertRaises(slot.SlotUnavailable):
            slot.verify_slot_ack(*args)
