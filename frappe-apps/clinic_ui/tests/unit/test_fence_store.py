"""Tests for the explicit local write-fence store anchor."""

import os
import tempfile
import unittest
from pathlib import Path

from clinic_ui.core import fence_store, write_fence


class FenceStoreTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        self.directory = self.root / fence_store.STORE_DIRNAME
        self.anchor = self.root / fence_store.ANCHOR_FILENAME

    def test_initialize_then_open(self):
        info = fence_store.initialize_store(self.directory, self.anchor)
        self.assertEqual(info.epoch, 1)
        self.assertEqual(info.sequence, 0)
        self.assertEqual(self.anchor.stat().st_mode & 0o777, 0o600)

        ledger = fence_store.open_store(self.directory, self.anchor)
        self.assertEqual(ledger.info().deployment_id, info.deployment_id)

    def test_second_initialize_is_refused(self):
        fence_store.initialize_store(self.directory, self.anchor)
        with self.assertRaises(write_fence.StoreUnavailable):
            fence_store.initialize_store(self.directory, self.anchor)

    def test_missing_anchor_fails_closed(self):
        fence_store.initialize_store(self.directory, self.anchor)
        self.anchor.unlink()
        with self.assertRaises(write_fence.StoreNotInitialized):
            fence_store.open_store(self.directory, self.anchor)

    def test_missing_store_fails_closed(self):
        info = fence_store.initialize_store(self.directory, self.anchor)
        # Remove the store but keep a syntactically valid anchor.
        for name in (write_fence.STORE_FILENAME, write_fence.LOCK_FILENAME):
            (self.directory / name).unlink()
        with self.assertRaises(write_fence.StoreNotInitialized):
            fence_store.open_store(self.directory, self.anchor)
        self.assertEqual(info.epoch, 1)

    def test_anchor_must_be_private(self):
        fence_store.initialize_store(self.directory, self.anchor)
        os.chmod(self.anchor, 0o644)
        with self.assertRaises(write_fence.StorePermissionError):
            fence_store.open_store(self.directory, self.anchor)

    def test_anchor_must_be_strict_json(self):
        fence_store.initialize_store(self.directory, self.anchor)
        original = self.anchor.read_text(encoding="utf-8")
        for replacement in (
            "not json",
            '{"deployment_id":"x"}',
            '{"deployment_id":"%s","epoch":1,"sequence":0,"extra":1}'
            % ("a" * 32),
            '{"deployment_id":"%s","epoch":"1","sequence":0}' % ("a" * 32),
        ):
            with self.subTest(replacement=replacement):
                self.anchor.write_text(replacement, encoding="utf-8")
                os.chmod(self.anchor, 0o600)
                with self.assertRaises(write_fence.StoreCorrupt):
                    fence_store.open_store(self.directory, self.anchor)
        self.anchor.write_text(original, encoding="utf-8")
        os.chmod(self.anchor, 0o600)

    def test_anchor_must_not_be_a_symlink(self):
        fence_store.initialize_store(self.directory, self.anchor)
        target = self.root / "elsewhere.json"
        target.write_text(self.anchor.read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o600)
        self.anchor.unlink()
        self.anchor.symlink_to(target)
        with self.assertRaises(write_fence.StoreNotInitialized):
            fence_store.open_store(self.directory, self.anchor)


if __name__ == "__main__":
    unittest.main()
