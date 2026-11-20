import types
import unittest
from pathlib import Path

from clinic_ui.endpoints import native_checkin


class Doc:
    def __init__(self, **overrides):
        self.__dict__.update(name="A", patient="P", practitioner="D",
                             service_unit="S", status="Open",
                             position_in_queue=0, modified="v1", **overrides)
        self.denied = set()

    def check_permission(self, permission):
        if permission in self.denied:
            raise PermissionError(permission)


class Gateway:
    def __init__(self):
        self.doc = Doc()
        self.patient = Doc()
        self.frappe = types.SimpleNamespace(get_doc=self.get_doc)
        self.calls = 0
        self.fixtures = ()
        self.resolve = "A"
        self.reads = []
        self.events = []

    def get_doc(self, doctype, name, **kwargs):
        self.reads.append((doctype, name, kwargs))
        if doctype == "Patient":
            return self.patient
        return self.doc

    def linked_resolve(self, ref):
        return self.resolve

    def checkin_fixture_ids(self):
        return self.fixtures

    def _linked_fields(self, *args):
        pass

    def schedule_checkin_observation(self, appointment, patient):
        self.events.append((appointment, patient))

    def _native_check_in_function(self):
        def call(name):
            self.calls += 1
            self.doc.status = "Checked In"
            self.doc.position_in_queue = 1
        return call


class NativeCheckinTests(unittest.TestCase):
    def setUp(self):
        self.gateway = Gateway()

    def action(self, ref="A", version="v1"):
        return native_checkin.checkin(self.gateway, {"appointment_ref": ref, "expected_version": version})

    def test_native_action_and_no_replay(self):
        self.assertEqual(self.action()["status"], "Checked In")
        self.assertEqual(self.gateway.calls, 1)
        self.assertEqual(self.gateway.events, [("A", "P")])
        self.assertEqual(self.gateway.reads.count(("Patient Appointment", "A", {"for_update": True})), 2)
        with self.assertRaises(ValueError):
            self.action()
        self.assertEqual(self.gateway.calls, 1)

    def test_rejects_fixture_stale_and_denied_permissions(self):
        self.gateway.fixtures = ("A",)
        with self.assertRaises(ValueError):
            self.action()
        self.gateway.fixtures = ()
        with self.assertRaises(ValueError):
            self.action(version="stale")
        for doc, permission in ((self.gateway.doc, "read"), (self.gateway.doc, "write"), (self.gateway.patient, "read")):
            doc.denied.add(permission)
            with self.assertRaises(PermissionError):
                self.action()
            doc.denied.clear()
        self.assertEqual(self.gateway.calls, 0)

    def test_sealed_alias_resolves_to_real_appointment(self):
        self.assertEqual(self.action(ref="sealed-alias")["appointment"], "A")
        self.assertEqual(self.gateway.calls, 1)

    def test_rejects_fabricated_or_changed_native_result(self):
        for status, queue, patient in (("Open", 1, "P"), ("Checked In", 0, "P"), ("Checked In", 1, "Q")):
            self.gateway = Gateway()
            def native(name):
                self.gateway.doc.status = status
                self.gateway.doc.position_in_queue = queue
                self.gateway.doc.patient = patient
            self.gateway._native_check_in_function = lambda: native
            with self.assertRaises(ValueError):
                self.action()
            self.assertEqual(self.gateway.events, [])

    def test_rejects_already_checked_in_even_with_positive_queue(self):
        self.gateway.doc.status = "Checked In"
        self.gateway.doc.position_in_queue = 1
        with self.assertRaises(ValueError):
            self.action()
        self.assertEqual(self.gateway.calls, 0)

    def test_api_is_post_only_without_photo_or_stock_rpc_override(self):
        root = Path(__file__).resolve().parents[2] / "clinic_ui"
        api = (root / "api" / "native_checkin.py").read_text()
        action = (root / "endpoints" / "native_checkin.py").read_text()
        self.assertIn('@frappe.whitelist(methods=["POST"])', api)
        self.assertNotIn("clinic_photos", api + action)
        self.assertNotIn("ignore_permissions", api + action)
        self.assertNotIn(".commit(", api + action)
