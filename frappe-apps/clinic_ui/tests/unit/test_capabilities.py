"""Capability-map tests and contract parity with the client contract file.

The server must publish every capability name the React client knows, all as
``read_only``. Parity is checked against ``contracts/capabilities.json`` when
that repository file is reachable; the check is skipped when the app is copied
outside the repository.
"""

import json
import unittest
from pathlib import Path

from clinic_ui.core.capabilities import (
    CAPABILITY_NAMES,
    READ_ONLY_STATE,
    read_only_capabilities,
)


def contract_path():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "capabilities.json"
        if candidate.is_file():
            return candidate
    return None


class CapabilityTests(unittest.TestCase):
    def test_read_only_map_covers_every_capability(self):
        capabilities = read_only_capabilities()
        self.assertEqual(set(capabilities), set(CAPABILITY_NAMES))
        self.assertEqual(set(capabilities.values()), {READ_ONLY_STATE})
        self.assertEqual(READ_ONLY_STATE, "read_only")

    def test_capability_names_are_unique_and_namespaced(self):
        self.assertEqual(len(CAPABILITY_NAMES), len(set(CAPABILITY_NAMES)))
        for name in CAPABILITY_NAMES:
            with self.subTest(name=name):
                self.assertRegex(name, r"^(write|read)\.[a-z.]+")

    def test_no_write_capability_is_enabled(self):
        capabilities = read_only_capabilities()
        self.assertFalse(
            [name for name, state in capabilities.items() if state != "read_only"]
        )

    def test_returned_map_is_a_fresh_mapping(self):
        first = read_only_capabilities()
        first["read.patient"] = "enabled"
        self.assertEqual(read_only_capabilities()["read.patient"], "read_only")


class ContractParityTests(unittest.TestCase):
    def test_names_match_the_client_contract(self):
        path = contract_path()
        if path is None:
            self.skipTest("contracts/capabilities.json is not reachable")
        names = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(CAPABILITY_NAMES), list(names))


if __name__ == "__main__":
    unittest.main()
