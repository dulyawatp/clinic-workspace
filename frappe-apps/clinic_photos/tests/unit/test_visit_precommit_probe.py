"""Offline checks for the test-only precommit boundary probe."""

import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).with_name("visit_precommit_probe.py")
SPEC = importlib.util.spec_from_file_location("visit_precommit_probe", PATH)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class PrecommitProbeTest(unittest.TestCase):
    def test_real_boundary_follows_publish_and_arm(self):
        path = pathlib.Path(__file__).parents[2] / "clinic_photos/publication/coordinator.py"
        result = probe.find_boundary(path.read_text())
        self.assertGreater(result["commit_line"], result["publish_line"])
        self.assertGreater(result["commit_line"], result["arm_line"])

    def test_missing_arm_is_refused(self):
        path = pathlib.Path(__file__).parents[2] / "clinic_photos/publication/coordinator.py"
        source = path.read_text().replace("writer.arm_visit_final_commit(", "writer.other_arm(")
        with self.assertRaises(ValueError):
            probe.find_boundary(source)


if __name__ == "__main__":
    unittest.main()
