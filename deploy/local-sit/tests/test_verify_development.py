#!/usr/bin/env python3
"""Focused tests for ``bin/verify-development.py``.

These exercise only the pure functions and argument parsing. No Docker,
network, site or credential access happens here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "bin" / "verify-development.py"


def _load_development():
    spec = importlib.util.spec_from_file_location("local_sit_development", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


development = _load_development()
strict = development.strict

CONVERTED = {
    "write.patient.create",
    "write.patient.update",
    "write.appointment.book",
    "write.appointment.reschedule",
    "write.appointment.cancel",
    "write.appointment.no_show",
    "write.appointment.checkin",
}


def _gates(**opened):
    gates = {family: 0 for family in strict.GATE_CONFIG_KEYS}
    gates.update(opened)
    return gates


def _capabilities(gates, role="DOC"):
    capabilities = {name: "read_only" for name in strict.WRITE_CAPABILITY_GATES}
    for name in strict.expected_enabled_writes(gates, role):
        capabilities[name] = "enabled"
    return capabilities


class LaneTests(unittest.TestCase):
    def test_converted_set_is_the_patient_and_appointment_lanes(self):
        self.assertEqual(set(development.CONVERTED), CONVERTED)
        self.assertFalse(CONVERTED & strict.DEFERRED_WRITE_CAPABILITIES)

    def test_open_gates_pass(self):
        gates = _gates(patient=1, appointment=1)

        self.assertEqual(
            development.lane_problems(_capabilities(gates), gates), []
        )

    def test_closed_gate_is_reported(self):
        gates = _gates(patient=1)
        problems = development.lane_problems(_capabilities(gates), gates)

        self.assertTrue(any("do not open converted lanes" in p for p in problems))

    def test_disabled_converted_write_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(
            _capabilities(gates), **{"write.appointment.checkin": "read_only"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(any("not enabled" in p for p in problems))

    def test_deferred_write_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(
            _capabilities(gates), **{"write.medication.order": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(any("deferred capabilities enabled" in p for p in problems))


class FenceTests(unittest.TestCase):
    def test_exact_counts_pass(self):
        ledger = {"events": 102, "unresolved": 2}

        self.assertEqual(development.fence_problems(ledger, 102, 2), [])

    def test_event_drift_is_reported(self):
        problems = development.fence_problems(
            {"events": 100, "unresolved": 2}, 102, 2
        )

        self.assertTrue(any("fence events" in p for p in problems))

    def test_unresolved_drift_is_reported(self):
        problems = development.fence_problems(
            {"events": 102, "unresolved": 0}, 102, 2
        )

        self.assertTrue(any("unresolved keys" in p for p in problems))

    def test_unreadable_ledger_is_reported(self):
        self.assertEqual(
            development.fence_problems(None, 102, 2),
            ["historical fence ledger unreadable"],
        )


class DirectoryTests(unittest.TestCase):
    def test_extra_synthetic_patients_are_allowed(self):
        items = [
            {"workspace_patient_id": "WS-SIT-1", "display_name": "SITSYNTH Alpha"},
            {"workspace_patient_id": "wpa1_dev", "display_name": "UISYNTH Dev"},
        ]

        self.assertEqual(development.directory_problems(items), [])

    def test_missing_seeded_patient_is_reported(self):
        problems = development.directory_problems([])

        self.assertTrue(any("appears 0 times" in p for p in problems))

    def test_leaked_native_identifier_is_reported(self):
        items = [
            {
                "workspace_patient_id": "WS-SIT-1",
                "display_name": "SITSYNTH Alpha",
                "native_patient_id": "PAT-0001",
            }
        ]
        problems = development.directory_problems(items)

        self.assertTrue(any("unexpected shape" in p for p in problems))


class ArgumentTests(unittest.TestCase):
    def test_both_recorded_counts_are_required(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                development.main(["--expected-fence-events", "102"])


if __name__ == "__main__":
    unittest.main()
