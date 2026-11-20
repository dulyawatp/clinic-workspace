import types
import unittest

from clinic_ui.endpoints import checkin_observer


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.callbacks = []
        self.jobs = []
        self.frappe = types.SimpleNamespace(
            db=types.SimpleNamespace(after_commit=types.SimpleNamespace(add=self.callbacks.append)),
            utils=types.SimpleNamespace(now_datetime=lambda: "2026-11-20 10:00:00.000001"),
            local=types.SimpleNamespace(flags=types.SimpleNamespace(in_migrate=False)),
            session=types.SimpleNamespace(user="actor"),
            get_hooks=lambda name: ["clinic_photos.private.checkin.observe"],
            enqueue=lambda method, **kwargs: self.jobs.append((method, kwargs)),
        )

    def test_enqueue_only_after_commit_and_rollback_has_no_job(self):
        checkin_observer.schedule(self.frappe, "A", "P")
        self.assertFalse(self.jobs)
        self.callbacks.pop()()
        self.assertEqual(self.jobs[0][0], "clinic_photos.private.checkin.observe")
        self.assertEqual(self.jobs[0][1]["appointment"], "A")
        self.assertEqual(self.jobs[0][1]["actor"], "actor")
        checkin_observer.schedule(self.frappe, "A", "P")
        self.callbacks.clear()
        self.assertEqual(len(self.jobs), 1)

    def test_missing_hook_or_unavailable_queue_is_safe(self):
        self.frappe.get_hooks = lambda name: []
        checkin_observer.schedule(self.frappe, "A", "P")
        self.callbacks.pop()()
        self.assertFalse(self.jobs)
        self.frappe.get_hooks = lambda name: ["observer"]
        self.frappe.enqueue = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("redis"))
        checkin_observer.schedule(self.frappe, "A", "P")
        self.callbacks.pop()()
