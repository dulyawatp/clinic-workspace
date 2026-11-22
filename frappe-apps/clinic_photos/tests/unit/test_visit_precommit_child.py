"""DB-free tests for operation-scoped test-only tracing."""

import importlib.util
import pathlib
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace

PATH = pathlib.Path(__file__).with_name("visit_precommit_child.py")
SPEC = importlib.util.spec_from_file_location("visit_precommit_child", PATH)
child = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(child)

OP = "a" * 32
SITE = "marley-photo-acceptance.local"


def fake_publish(owner, prepared, data):
    marker = "before"
    marker = "commit"  # trace target
    return marker


TARGET = fake_publish.__code__.co_firstlineno + 2


class ChildTraceTest(unittest.TestCase):
    def run_trace(self, prepared, directory, timeout=0.3, repeat=False):
        def run():
            result = fake_publish(None, prepared, b"")
            if repeat:
                fake_publish(None, prepared, b"")
            return result
        return child.run_with_barrier(fake_publish, TARGET, OP, SITE, directory, timeout, run)

    def test_matching_operation_waits_for_release_once(self):
        with tempfile.TemporaryDirectory() as directory:
            def release():
                self.assertTrue(child.wait_for_path(pathlib.Path(directory) / "ready", 1))
                (pathlib.Path(directory) / "release").touch(mode=0o600)
            thread = threading.Thread(target=release)
            thread.start()
            try:
                self.assertEqual(self.run_trace(SimpleNamespace(id=OP, site=SITE, state="PREPARED"),
                                                directory), "commit")
            finally:
                thread.join(2)
            self.assertFalse(thread.is_alive())

    def test_mismatched_operation_refuses_before_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(child.BarrierRefused):
                self.run_trace(SimpleNamespace(id="b" * 32, site=SITE, state="PREPARED"), directory)
            self.assertFalse((pathlib.Path(directory) / "ready").exists())

    def test_timeout_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(child.BarrierRefused):
                self.run_trace(SimpleNamespace(id=OP, site=SITE, state="PREPARED"), directory, 0.03)

    def test_prior_trace_restored_after_timeout(self):
        def previous(frame, event, arg):
            return previous
        original = sys.gettrace()
        sys.settrace(previous)
        try:
            with tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(child.BarrierRefused):
                    self.run_trace(SimpleNamespace(id=OP, site=SITE, state="PREPARED"), directory, 0.03)
                self.assertIs(sys.gettrace(), previous)
        finally:
            sys.settrace(original)

    def test_no_boundary_hit_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(child.BarrierRefused):
                child.run_with_barrier(fake_publish, TARGET, OP, SITE, directory, 0.1, lambda: None)

    def test_second_hit_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            def release():
                child.wait_for_path(pathlib.Path(directory) / "ready", 1)
                (pathlib.Path(directory) / "release").touch(mode=0o600)
            thread = threading.Thread(target=release)
            thread.start()
            try:
                with self.assertRaises(child.BarrierRefused):
                    self.run_trace(SimpleNamespace(id=OP, site=SITE, state="PREPARED"), directory,
                                   repeat=True)
            finally:
                thread.join(2)

    def test_invalid_site_refuses_without_runner(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(child.BarrierRefused):
                child.run_with_barrier(fake_publish, TARGET, OP, "other.local", directory, 0.1,
                                       lambda: self.fail("runner called"))


if __name__ == "__main__":
    unittest.main()
