import unittest
from unittest.mock import patch

from clinic_ui.runtime.visit_invoice_serialization import (
    SerializationError,
    VisitInvoiceLane,
)


class DB:
    db_type = "mariadb"
    _disable_transaction_control = 0

    def __init__(self):
        self._conn = Connection()
        self.events = []
        self.row = (("APT", 0, None),)
        self.fail = None

    def sql(self, query, values=None):
        self.events.append(("sql", query, values))
        if self.fail == "sql":
            raise RuntimeError("contended")
        return self.row

    def rollback(self):
        self.events.append(("rollback",))
        if self.fail == "rollback":
            raise RuntimeError("rollback failed")

    def commit(self):
        self.events.append(("commit",))
        if self.fail == "commit":
            raise RuntimeError("commit failed")


class Connection:
    open = True

    def thread_id(self):
        return 123

    def get_autocommit(self):
        return False


class SerializationTest(unittest.TestCase):
    def setUp(self):
        self.db = DB()
        self.driver = patch("clinic_ui.runtime.visit_invoice_serialization._supported_connection", return_value=True)
        self.driver.start()
        self.addCleanup(self.driver.stop)

    def lane(self, appointment="APT"):
        return VisitInvoiceLane(self.db, appointment)

    def test_exact_parameterized_nowait_and_denial_rollback(self):
        with self.lane() as entered:
            self.assertIs(entered, True)
            self.assertEqual(self.db.events[0], ("rollback",))
            _, sql, values = self.db.events[1]
            self.assertIn("FOR UPDATE NOWAIT", sql)
            self.assertIn("`tabPatient Appointment`", sql)
            self.assertEqual(values, ("APT", "APT"))
        self.assertEqual(self.db.events[-1], ("rollback",))

    def test_commit_is_explicit_and_no_exit_rollback(self):
        lane = self.lane()
        with lane:
            lane.commit()
        self.assertEqual([event[0] for event in self.db.events], ["rollback", "sql", "commit"])

    def test_bad_rows_and_contention_rollback(self):
        for row in [(), (("other", 0, None),), (("APT", 1, None),),
                    (("APT", 0, "SI"),), (("APT", False, None),)]:
            with self.subTest(row=row):
                self.db = DB()
                self.db.row = row
                with self.assertRaises(SerializationError):
                    with self.lane():
                        self.fail("must not enter")
                self.assertEqual(self.db.events[-1], ("rollback",))
        self.db = DB()
        self.db.fail = "sql"
        with self.assertRaises(SerializationError):
            with self.lane():
                self.fail("must not enter")
        self.assertEqual(self.db.events[-1], ("rollback",))

    def test_disabled_control_refuses(self):
        self.db._disable_transaction_control = 1
        with self.assertRaises(SerializationError):
            with self.lane():
                self.fail("must not enter")
        self.assertEqual(self.db.events, [])

    def test_autocommit_and_unsupported_driver_refused(self):
        self.db._conn.get_autocommit = lambda: True
        with self.assertRaises(SerializationError):
            with self.lane():
                self.fail("must not enter")
        self.assertEqual(self.db.events, [])
        self.db._conn.get_autocommit = lambda: False
        with patch("clinic_ui.runtime.visit_invoice_serialization._supported_connection", return_value=False):
            with self.assertRaises(SerializationError):
                with self.lane():
                    self.fail("must not enter")

    def test_disabled_control_after_entry_cannot_commit_or_fake_rollback(self):
        lane = self.lane()
        with self.assertRaises(SerializationError):
            with lane:
                self.db._disable_transaction_control = 1
                lane.commit()
        self.assertEqual([event[0] for event in self.db.events], ["rollback", "sql"])

    def test_connection_drift_refuses_commit_and_cleanup(self):
        with self.assertRaises(SerializationError):
            with self.lane() as entered:
                self.assertTrue(entered)
                self.db._conn = object()
        self.assertNotIn(("commit",), self.db.events)
        self.assertEqual(self.db.events[-1][0], "sql")

    def test_commit_error_propagates_and_rolls_back(self):
        lane = self.lane()
        with self.assertRaises(SerializationError):
            with lane:
                self.db.fail = "commit"
                lane.commit()
        self.assertEqual(self.db.events[-1], ("rollback",))

    def test_rollback_error_propagates(self):
        with self.assertRaises(SerializationError):
            with self.lane():
                self.db.fail = "rollback"


if __name__ == "__main__":
    unittest.main()
