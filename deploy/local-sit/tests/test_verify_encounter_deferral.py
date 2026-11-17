#!/usr/bin/env python3
"""Focused regression tests for the verifier's encounter/lab write deferral.

Encounter draft, completion, amendment and lab ordering were deferred on
2026-11-18 (docs/superpowers/specs/2026-11-18-encounter-deferral-design.md).
The verifier must assert each of them stays ``read_only`` even when the
encounter family gate is open, and must keep every other gate check.

These exercise only the pure functions in ``deploy/local-sit/bin/verify.py``.
No Docker, network, site or credential access happens here.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import unittest
from pathlib import Path

VERIFY_PATH = Path(__file__).resolve().parents[1] / "bin" / "verify.py"

DEFERRED = (
    "write.encounter.draft",
    "write.encounter.complete",
    "write.encounter.amend",
    "write.lab.order",
    "write.medication.order",
)


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


class EncounterDeferralTests(unittest.TestCase):
    def test_every_encounter_write_is_deferred(self):
        self.assertEqual(set(DEFERRED), set(verify.DEFERRED_WRITE_CAPABILITIES))

    def test_deferred_set_matches_the_frozen_contract(self):
        contract = json.loads(
            (Path(__file__).resolve().parents[3] / "contracts/capabilities.json").read_text()
        )

        self.assertLessEqual(set(verify.DEFERRED_WRITE_CAPABILITIES), set(contract))
        for name in verify.DEFERRED_WRITE_CAPABILITIES:
            with self.subTest(capability=name):
                self.assertTrue(name.startswith("write."))

    def test_encounter_gate_enables_no_deferred_write(self):
        gates = _gates(encounter=1)

        for role in ("DOC", "NUR"):
            with self.subTest(role=role):
                enabled = verify.expected_enabled_writes(gates, role)
                for name in DEFERRED:
                    self.assertNotIn(name, enabled)

    def test_encounter_gate_still_leaves_unrelated_writes_alone(self):
        gates = _gates(encounter=1, patient=1, appointment=1)
        enabled = verify.expected_enabled_writes(gates, "DOC")

        self.assertLessEqual(
            {
                "write.patient.create",
                "write.patient.update",
                "write.appointment.book",
                "write.appointment.checkin",
            },
            enabled,
        )

    def test_each_enabled_deferred_write_is_rejected_with_the_gate_open(self):
        gates = _gates(patient=1, appointment=1, encounter=1)

        for name in DEFERRED:
            with self.subTest(capability=name):
                capabilities, expected_ids = _capabilities(gates, "DOC")
                capabilities[name] = "enabled"

                problems = verify.capability_problems(
                    capabilities, expected_ids, gates, "DOC"
                )

                self.assertIn(f"deferred writes enabled: ['{name}']", problems)

    def test_deferred_states_report_every_encounter_capability(self):
        observed = verify.deferred_capability_states(
            {name: "enabled" for name in DEFERRED}
            | {"write.patient.create": "enabled"}
        )

        self.assertEqual(observed, {name: "enabled" for name in DEFERRED})
        self.assertEqual(
            verify.deferred_capability_states({}),
            {name: None for name in DEFERRED},
        )

    def test_deferred_capabilities_stay_in_the_gated_capability_set(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")

        for name in DEFERRED:
            with self.subTest(capability=name):
                self.assertIn(name, verify.WRITE_CAPABILITY_GATES)
                self.assertIn(name, expected_ids)
                self.assertEqual(capabilities[name], "read_only")

    def test_matching_map_with_the_encounter_gate_open_passes(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
        capabilities, expected_ids = _capabilities(gates, "DOC")

        self.assertEqual(
            verify.capability_problems(capabilities, expected_ids, gates, "DOC"), []
        )

    def test_other_gate_checks_are_retained_with_the_encounter_gate_open(self):
        gates = _gates(patient=1, appointment=1, encounter=1)
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

        missing = dict(capabilities)
        missing.pop("write.encounter.draft")
        self.assertTrue(
            any(
                "missing capabilities" in problem
                for problem in verify.capability_problems(
                    missing, expected_ids, gates, "DOC"
                )
            )
        )

    def test_other_family_gates_still_enable_their_writes(self):
        gates = _gates(procedure=1, stock=1, purchase=1, finance=1, admin=1)

        self.assertLessEqual(
            {"write.procedure.create", "write.procedure.complete"},
            verify.expected_enabled_writes(gates, "DOC"),
        )
        self.assertLessEqual(
            {"write.stock.receive", "write.stock.transfer"},
            verify.expected_enabled_writes(gates, "PHA"),
        )
        self.assertLessEqual(
            {"write.invoice.submit", "write.payment.receive"},
            verify.expected_enabled_writes(gates, "CAS"),
        )
        self.assertLessEqual(
            {"write.admin.user", "write.admin.master"},
            verify.expected_enabled_writes(gates, "ADM"),
        )

    def test_selfcheck_still_passes(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status = verify.selfcheck()

        self.assertEqual(status, 0)


if __name__ == "__main__":
    unittest.main()
