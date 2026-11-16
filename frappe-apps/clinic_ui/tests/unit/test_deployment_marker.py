"""Tests for the deployment-bound private write marker."""

import os
import tempfile
import unittest
from pathlib import Path

from clinic_ui.core import deployment_marker

SITE = "marley-sit.local"
DEPLOYMENT = "deployment-id-1"


class DeploymentMarkerTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.private = Path(self._temp.name) / "private"
        self.private.mkdir(mode=0o700)

    def marker(self):
        return Path(deployment_marker.marker_path(self.private, SITE))

    def test_write_then_read_binds_site_and_deployment(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        self.assertTrue(
            deployment_marker.read_marker(self.private, SITE, DEPLOYMENT)
        )

    def test_second_write_is_refused(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)

    def test_wrong_site_or_deployment_is_refused(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(
                self.private, "other.local", DEPLOYMENT
            )
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(self.private, SITE, "other-deployment")

    def test_marker_copied_to_another_path_is_refused(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        copied_private = Path(self._temp.name) / "copied-private"
        copied_private.mkdir(mode=0o700)
        copied_dir = Path(deployment_marker.marker_directory(copied_private))
        copied_dir.mkdir(mode=0o700)
        copied = Path(deployment_marker.marker_path(copied_private, SITE))
        copied.write_text(self.marker().read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(copied, 0o600)
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(copied_private, SITE, DEPLOYMENT)

    def test_missing_marker_is_refused(self):
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(self.private, SITE, DEPLOYMENT)

    def test_marker_must_be_private_and_owned(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        os.chmod(self.marker(), 0o644)
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(self.private, SITE, DEPLOYMENT)

    def test_marker_must_not_be_a_symlink(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        target = Path(self._temp.name) / "elsewhere.json"
        target.write_text(self.marker().read_text(encoding="utf-8"), encoding="utf-8")
        os.chmod(target, 0o600)
        self.marker().unlink()
        self.marker().symlink_to(target)
        with self.assertRaises(deployment_marker.MarkerError):
            deployment_marker.read_marker(self.private, SITE, DEPLOYMENT)

    def test_malformed_markers_are_refused(self):
        deployment_marker.write_marker(self.private, SITE, DEPLOYMENT)
        for body in (
            "not json",
            "{}",
            '{"version":1,"site":"%s"}' % SITE,
            '{"version":2,"site":"%s","deployment_id":"%s"}'
            % (SITE, DEPLOYMENT),
            '{"version":1,"site":"%s","deployment_id":"%s","extra":1}'
            % (SITE, DEPLOYMENT),
        ):
            with self.subTest(body=body):
                self.marker().write_text(body, encoding="utf-8")
                os.chmod(self.marker(), 0o600)
                with self.assertRaises(deployment_marker.MarkerError):
                    deployment_marker.read_marker(self.private, SITE, DEPLOYMENT)

    def test_site_name_cannot_escape_the_private_directory(self):
        for site in ("../escape", "a/b", "a\\b", "", "bad\x00site"):
            with self.subTest(site=site):
                with self.assertRaises(deployment_marker.MarkerError):
                    deployment_marker.marker_path(self.private, site)


if __name__ == "__main__":
    unittest.main()
