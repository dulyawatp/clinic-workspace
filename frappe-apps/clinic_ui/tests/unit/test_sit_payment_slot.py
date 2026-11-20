import os
import tempfile
import unittest
from types import SimpleNamespace

from clinic_ui.core import deployment_marker
from clinic_ui.runtime import sit_payment_slot as slot


class Database:
    def __init__(self, row=None):
        self.row = row
        self.calls = []
        self._cursor = SimpleNamespace(rowcount=0)

    def sql(self, query, values, **kwargs):
        self.calls.append((query, values))
        if query.lstrip().upper().startswith("UPDATE"):
            self._cursor.rowcount = 1 if self.row and self.row["state"] == "available" else 0
            if self._cursor.rowcount:
                self.row = dict(self.row, state="spent", actor=values[0], operation_key=values[1], receipt=values[2])
            return []
        return [self.row.copy()] if self.row else []


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

        def no_update(query, values, **kwargs):
            result = sql(query, values, **kwargs)
            if query.lstrip().upper().startswith("UPDATE"):
                self.db._cursor.rowcount = 0
            return result

        self.db.sql = no_update
        with self.assertRaises(slot.SlotUnavailable):
            slot.consume_slot(self.gateway, slot.DEPLOYMENT_ID, "actor", "key", "PAY-1")
