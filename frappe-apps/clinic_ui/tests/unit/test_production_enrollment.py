import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from clinic_ui.core.production_enrollment import verify_production_enrollment


class ProductionEnrollmentTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.private = self.root / "sites" / "clinic.example" / "private"
        self.private.mkdir(parents=True)
        self.marker = self.root / "host-proof" / "marker.json"
        self.marker.parent.mkdir()
        self.payload = {
            "version": 1,
            "site": "clinic.example",
            "private_path": str(self.private),
            "installation_id": "a1" * 32,
        }
        self.write()

    def write(self, raw=None):
        self.raw = raw if raw is not None else json.dumps(self.payload).encode()
        self.marker.write_bytes(self.raw)
        self.marker.chmod(0o600)
        self.digest = hashlib.sha256(self.raw).hexdigest()

    def verify(self, **overrides):
        args = dict(
            resolved_site="clinic.example",
            private_site_path=str(self.private),
            marker_path=str(self.marker),
            configured_site="clinic.example",
            expected_digest=self.digest,
        )
        args.update(overrides)
        return verify_production_enrollment(**args)

    def test_valid_external_marker(self):
        self.assertTrue(self.verify())

    def test_symlinked_private_rejected_even_with_marker_in_sites(self):
        target = self.root / "outside" / "foreign" / "private"
        target.mkdir(parents=True)
        self.private.rmdir()
        self.private.symlink_to(target, target_is_directory=True)
        self.payload["private_path"] = str(target)
        self.marker = self.root / "sites" / "marker.json"
        self.write()
        self.assertFalse(self.verify())

    def test_symlinked_site_rejected_even_with_marker_in_sites(self):
        target = self.root / "outside" / "foreign"
        (target / "private").mkdir(parents=True)
        self.private.rmdir()
        self.private.parent.rmdir()
        self.private.parent.symlink_to(target, target_is_directory=True)
        self.payload["private_path"] = str(target / "private")
        self.marker = self.root / "sites" / "marker.json"
        self.write()
        self.assertFalse(self.verify())

    def test_nonstandard_private_layout_rejected(self):
        for private in (
            self.root / "sites" / "clinic.example" / "other",
            self.root / "sites" / "other.example" / "private",
        ):
            with self.subTest(private=private):
                private.mkdir(parents=True)
                self.payload["private_path"] = str(private)
                self.write()
                self.assertFalse(self.verify(private_site_path=str(private)))

    def test_site_and_digest_must_match(self):
        for args in (
            {"resolved_site": "other.example"},
            {"configured_site": "other.example"},
            {"expected_digest": ""},
            {"expected_digest": "0" * 64},
            {"expected_digest": None},
        ):
            with self.subTest(args=args):
                self.assertFalse(self.verify(**args))

    def test_clone_without_independent_marker_and_wrong_private_path(self):
        clone = self.root / "clone" / "sites" / "clinic.example" / "private"
        clone.mkdir(parents=True)
        self.assertFalse(self.verify(private_site_path=str(clone)))
        self.assertFalse(self.verify(marker_path=str(self.root / "missing")))

    def test_unsafe_marker_paths(self):
        linked = self.root / "linked"
        linked.symlink_to(self.marker)
        self.assertFalse(self.verify(marker_path=str(linked)))
        linked_dir = self.root / "linked-dir"
        linked_dir.symlink_to(self.marker.parent, target_is_directory=True)
        self.assertFalse(self.verify(marker_path=str(linked_dir / self.marker.name)))
        copied = self.private / "marker.json"
        copied.write_bytes(self.raw)
        copied.chmod(0o600)
        self.assertFalse(self.verify(marker_path=str(copied)))
        copied = self.root / "sites" / "marker.json"
        copied.write_bytes(self.raw)
        copied.chmod(0o600)
        self.assertFalse(self.verify(marker_path=str(copied)))
        self.assertFalse(self.verify(marker_path="relative"))
        self.assertFalse(self.verify(marker_path=str(self.marker.parent)))

    def test_public_and_oversize_marker_rejected(self):
        self.marker.chmod(0o644)
        self.assertFalse(self.verify())
        self.marker.chmod(0o620)
        self.assertFalse(self.verify())
        self.write(b"x" * 4097)
        self.assertFalse(self.verify())

    def test_only_supported_file_modes_accepted(self):
        for mode in (0o400, 0o440, 0o600, 0o640):
            with self.subTest(mode=oct(mode)):
                self.marker.chmod(mode)
                self.assertTrue(self.verify())
        for mode in (0o000, 0o200, 0o100, 0o500, 0o700, 0o444, 0o660):
            with self.subTest(mode=oct(mode)):
                self.marker.chmod(mode)
                self.assertFalse(self.verify())

    def test_foreign_owned_marker_rejected(self):
        with patch("clinic_ui.core.production_enrollment.os.geteuid", return_value=999999):
            if self.marker.stat().st_uid != 0:
                self.assertFalse(self.verify())

    def test_exact_schema_and_types(self):
        for key, value in (
            ("version", True), ("version", 2), ("site", 1),
            ("private_path", None), ("installation_id", "short"),
            ("installation_id", 123), ("extra", "value"),
        ):
            with self.subTest(key=key, value=value):
                original = dict(self.payload)
                self.payload[key] = value
                self.write()
                self.assertFalse(self.verify())
                self.payload = original
        self.write(json.dumps(self.payload).encode()[:-1] + b', "version": 1}')
        self.assertFalse(self.verify())
        self.write(b"not json")
        self.assertFalse(self.verify())

    def test_invalid_inputs_fail_closed(self):
        for args in (
            {"resolved_site": None}, {"configured_site": True},
            {"private_site_path": None}, {"marker_path": None},
            {"private_site_path": "relative"}, {"expected_digest": 123},
        ):
            with self.subTest(args=args):
                self.assertFalse(self.verify(**args))


if __name__ == "__main__":
    unittest.main()
