import unittest
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.runtime.frappe_gateway import Gateway
from clinic_ui.runtime.visit_invoice_serialization import SerializationError


class GatewayLaneTest(unittest.TestCase):
    def test_one_lane_owns_enter_commit_and_exit(self):
        events = []

        class Lane:
            def __init__(self, db, appointment_id):
                events.append(("init", db, appointment_id))

            def __enter__(self):
                events.append("enter")
                return True

            def commit(self):
                events.append("commit")

            def __exit__(self, *args):
                events.append(("exit", args))

        db = object()
        gateway = Gateway(SimpleNamespace(db=db))
        with patch("clinic_ui.runtime.visit_invoice_serialization.VisitInvoiceLane", Lane):
            lane = gateway.visit_invoice_serialized("APT")
            with lane:
                gateway.commit_visit_invoice()
            with self.assertRaises(SerializationError):
                gateway.visit_invoice_serialized("OTHER")
        self.assertEqual(events, [("init", db, "APT"), "enter", "commit",
                                  ("exit", (None, None, None))])

    def test_rollback_same_lane_propagates_failure(self):
        failure = RuntimeError("rollback failed")
        lane = SimpleNamespace(rollback=lambda: (_ for _ in ()).throw(failure))
        gateway = Gateway(SimpleNamespace(db=object()))
        with patch("clinic_ui.runtime.visit_invoice_serialization.VisitInvoiceLane", return_value=lane):
            self.assertIs(gateway.visit_invoice_serialized("APT"), lane)
            with self.assertRaisesRegex(RuntimeError, "rollback failed"):
                gateway.rollback_visit_invoice()

    def test_commit_and_rollback_require_lane(self):
        gateway = Gateway(SimpleNamespace(db=object()))
        with self.assertRaises(SerializationError):
            gateway.commit_visit_invoice()
        with self.assertRaises(SerializationError):
            gateway.rollback_visit_invoice()
