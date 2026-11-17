#!/usr/bin/env python3
"""Focused regression tests for the verifier's medication deferral.

These exercise only the pure functions in ``deploy/local-sit/bin/verify.py``.
No Docker, network, site or credential access happens here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

VERIFY_PATH = Path(__file__).resolve().parents[1] / "bin" / "verify.py"

MEDICATION = "write.medication.order"


def _load_verify():
    spec = importlib.util.spec_from_file_location("local_sit_verify", VERIFY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = _load_verify()


def _gates(**opened):
    """Return a gate map with every family closed except ``opened``."""

    gates = {family: 0 for family in verify.GATE_CONFIG_KEYS}
    gates.update(opened)
    return gates


def _capabilities(gates, role):
    """Return a capability map that matches ``gates`` once deferral applies."""

    expected_ids = list(verify.WRITE_CAPABILITY_GATES) + sorted(
        verify.PERMANENT_READ_ONLY
    )
    capabilities = {name: "read_only" for name in expected_ids}
    for name in verify.expected_enabled_writes(gates, role):
        capabilities[name] = "enabled"
    return capabilities, expected_ids


class MedicationDeferralTests(unittest.TestCase):
    def test_encounter_gate_does_not_enable_medication_for_doc(self):
        enabled = verify.expected_enabled_writes(_gates(encounter=1), "DOC")

        self.assertNotIn(MEDICATION, enabled)
        self.assertIn("write.encounter.draft", enabled)
        self.assertIn("write.lab.order", enabled)

    def test_encounter_gate_does_not_enable_medication_for_nurse(self):
        enabled = verify.expected_enabled_writes(_gates(encounter=1), "NUR")

        self.assertNotIn(MEDICATION, enabled)
        self.assertIn("write.encounter.draft", enabled)

    def test_enabled_medication_is_rejected_even_with_encounter_gate_open(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")
        capabilities[MEDICATION] = "enabled"

        problems = verify.capability_problems(
            capabilities, expected_ids, gates, "DOC"
        )

        self.assertIn(f"deferred writes enabled: ['{MEDICATION}']", problems)

    def test_deferred_states_report_observed_values(self):
        observed = verify.deferred_capability_states(
            {
                MEDICATION: "enabled",
                "write.encounter.draft": "enabled",
            }
        )

        self.assertEqual(observed, {MEDICATION: "enabled"})
        self.assertEqual(
            verify.deferred_capability_states({MEDICATION: "read_only"}),
            {MEDICATION: "read_only"},
        )
        self.assertEqual(
            verify.deferred_capability_states({}), {MEDICATION: None}
        )

    def test_deferred_capability_stays_in_the_gated_capability_set(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")

        self.assertIn(MEDICATION, verify.WRITE_CAPABILITY_GATES)
        self.assertIn(MEDICATION, expected_ids)
        self.assertIn(MEDICATION, capabilities)
        self.assertEqual(capabilities[MEDICATION], "read_only")

    def test_matching_map_with_encounter_gate_open_passes(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")

        self.assertEqual(
            verify.capability_problems(capabilities, expected_ids, gates, "DOC"), []
        )

    def test_medication_capability_is_still_part_of_the_contract(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")
        capabilities.pop(MEDICATION)

        problems = verify.capability_problems(
            capabilities, expected_ids, gates, "DOC"
        )

        self.assertIn(MEDICATION, verify.WRITE_CAPABILITY_GATES)
        self.assertTrue(
            any("missing capabilities" in problem for problem in problems),
            f"expected a missing-capability problem, got {problems}",
        )

    def test_other_capability_checks_are_retained(self):
        gates = _gates(patient=1, appointment=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")

        ungated = dict(capabilities, **{"write.stock.adjust": "enabled"})
        self.assertTrue(
            any(
                "outside their gate" in problem
                for problem in verify.capability_problems(
                    ungated, expected_ids, gates, "DOC"
                )
            )
        )

        regressed = dict(capabilities, **{"write.appointment.checkin": "read_only"})
        self.assertTrue(
            any(
                "approved writes regressed" in problem
                for problem in verify.capability_problems(
                    regressed, expected_ids, gates, "DOC"
                )
            )
        )

    def test_selfcheck_still_passes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = verify.selfcheck()

        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
