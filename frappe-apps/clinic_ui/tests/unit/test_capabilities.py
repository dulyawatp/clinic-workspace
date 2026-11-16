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
    ENABLED_STATE,
    READ_ONLY_STATE,
    bootstrap_capabilities,
    read_only_capabilities,
    validate_capabilities,
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


class BootstrapCapabilityTests(unittest.TestCase):
    def test_defaults_to_every_capability_read_only(self):
        capabilities = bootstrap_capabilities()
        self.assertEqual(set(capabilities), set(CAPABILITY_NAMES))
        self.assertEqual(set(capabilities.values()), {READ_ONLY_STATE})

    def test_enables_only_the_requested_patient_writes(self):
        both = bootstrap_capabilities(
            write_patient_create=True, write_patient_update=True
        )
        self.assertEqual(both["write.patient.create"], ENABLED_STATE)
        self.assertEqual(both["write.patient.update"], ENABLED_STATE)
        self.assertEqual(
            {name for name, state in both.items() if state != READ_ONLY_STATE},
            {"write.patient.create", "write.patient.update"},
        )

        create_only = bootstrap_capabilities(write_patient_create=True)
        self.assertEqual(create_only["write.patient.create"], ENABLED_STATE)
        self.assertEqual(create_only["write.patient.update"], READ_ONLY_STATE)

        update_only = bootstrap_capabilities(write_patient_update=True)
        self.assertEqual(update_only["write.patient.create"], READ_ONLY_STATE)
        self.assertEqual(update_only["write.patient.update"], ENABLED_STATE)

    def test_enabled_state_name_is_stable(self):
        self.assertEqual(ENABLED_STATE, "enabled")

    def test_enables_only_the_requested_appointment_writes(self):
        capabilities = bootstrap_capabilities(
            write_appointment_book=True,
            write_appointment_reschedule=True,
            write_appointment_checkin=True,
        )
        self.assertEqual(
            {
                name
                for name, state in capabilities.items()
                if state == ENABLED_STATE
            },
            {
                "write.appointment.book",
                "write.appointment.reschedule",
                "write.appointment.checkin",
            },
        )
        self.assertEqual(
            capabilities["write.appointment.cancel"], READ_ONLY_STATE
        )
        self.assertEqual(capabilities["write.patient.create"], READ_ONLY_STATE)

    def test_appointment_writes_are_independently_requested(self):
        book_only = bootstrap_capabilities(write_appointment_book=True)
        self.assertEqual(book_only["write.appointment.book"], ENABLED_STATE)
        self.assertEqual(
            book_only["write.appointment.reschedule"], READ_ONLY_STATE
        )
        self.assertEqual(
            book_only["write.appointment.checkin"], READ_ONLY_STATE
        )

    def test_bootstrap_map_is_a_fresh_mapping(self):
        first = bootstrap_capabilities(write_patient_create=True)
        first["read.patient"] = ENABLED_STATE
        self.assertEqual(read_only_capabilities()["read.patient"], READ_ONLY_STATE)
        self.assertEqual(
            bootstrap_capabilities()["read.patient"], READ_ONLY_STATE
        )

    def test_validate_accepts_only_the_exact_shape_and_states(self):
        valid = read_only_capabilities()
        self.assertEqual(validate_capabilities(valid), valid)

        invalid_maps = (
            None,
            [],
            {"write.patient.create": READ_ONLY_STATE},
            {**valid, "unknown.capability": READ_ONLY_STATE},
            {**valid, "read.patient": "enabledish"},
            {**valid, "read.patient": ENABLED_STATE},
            {**valid, "write.appointment.cancel": ENABLED_STATE},
            {**valid, "write.admin.master": ENABLED_STATE},
        )
        for candidate in invalid_maps:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    validate_capabilities(candidate)

    def test_validate_refuses_enabled_on_any_other_capability(self):
        valid = read_only_capabilities()
        allowed = {
            "write.patient.create",
            "write.patient.update",
            "write.appointment.book",
            "write.appointment.reschedule",
            "write.appointment.checkin",
        }
        for name in CAPABILITY_NAMES:
            if name in allowed:
                continue
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_capabilities({**valid, name: ENABLED_STATE})


class ContractParityTests(unittest.TestCase):
    def test_names_match_the_client_contract(self):
        path = contract_path()
        if path is None:
            self.skipTest("contracts/capabilities.json is not reachable")
        names = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(CAPABILITY_NAMES), list(names))


if __name__ == "__main__":
    unittest.main()
