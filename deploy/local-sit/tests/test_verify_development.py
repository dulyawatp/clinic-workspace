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
    "write.procedure.complete",
    "write.procedure.create",
    "write.stock.consume",
    "write.purchase_order.draft",
}

PROCEDURE = {
    "write.procedure.complete",
    "write.procedure.create",
    "write.stock.consume",
}

# The purchase lane converted only its draft. Submit and the PO-referenced
# receipt stay on the historical fence: an unresolved ledger keeps them off.
PURCHASE = {"write.purchase_order.draft"}
FENCED = {"write.purchase_order.submit", "write.stock.receive"}

# The procedure lane completes and creates without consuming stock. Stock
# consumption keeps its own review and stays read_only even when the procedure
# flag is on, whatever the strict verifier's legacy family classification says.
PROCEDURE_WRITES = {
    "write.procedure.complete",
    "write.procedure.create",
}

CONSUMING = {"write.stock.consume"}

# Capabilities of the lanes that are not converted here. The development check
# must never claim them as ready, whatever their observed state.
UNRELATED = {
    "write.stock.receive",
    "write.stock.transfer",
    "write.stock.adjust",
    "write.purchase_order.draft",
    "write.purchase_order.submit",
    "write.invoice.submit",
    "write.payment.receive",
    "write.admin.user",
    "write.admin.master",
}


def _gates(**opened):
    gates = {family: 0 for family in strict.GATE_CONFIG_KEYS}
    gates.update(opened)
    return gates


def _capabilities(gates, role="DOC"):
    """Model the site bootstrap: expected writes minus the never-ready set.

    The fence-gated purchase writes are modelled as disabled, which is the
    state an unresolved ledger produces.
    """

    capabilities = {name: "read_only" for name in strict.WRITE_CAPABILITY_GATES}
    expected = (
        strict.expected_enabled_writes(gates, role)
        - development.NEVER_ENABLED
        - development.FENCED_WRITES
    )
    for name in expected:
        capabilities[name] = "enabled"
    return capabilities


class LaneTests(unittest.TestCase):
    def test_converted_set_is_the_patient_appointment_procedure_and_purchase_lanes(self):
        self.assertEqual(set(development.CONVERTED), CONVERTED)
        self.assertFalse(CONVERTED & strict.DEFERRED_WRITE_CAPABILITIES)
        self.assertEqual(
            development.CONVERTED_FAMILIES,
            ("patient", "appointment", "procedure", "purchase"),
        )
        self.assertEqual(set(development.FENCED_WRITES), FENCED)
        self.assertFalse(CONVERTED & FENCED)

    def test_procedure_family_classification_does_not_open_stock_consume(self):
        # The strict verifier still classifies write.stock.consume under the
        # procedure family; this lane is non-consuming, so it stays read_only.
        self.assertEqual(
            strict.WRITE_CAPABILITY_GATES["write.stock.consume"][0], "procedure"
        )
        self.assertEqual(set(development.NEVER_ENABLED), CONSUMING)
        self.assertTrue(CONSUMING <= CONVERTED)
        self.assertFalse(CONSUMING & UNRELATED)
        self.assertEqual(set(development.CONVERTED), CONVERTED)

    def test_open_gates_pass(self):
        gates = _gates(patient=1, appointment=1, procedure=1)

        self.assertEqual(
            development.lane_problems(_capabilities(gates), gates), []
        )

    def test_closed_gate_is_reported(self):
        gates = _gates(patient=1, procedure=1)
        problems = development.lane_problems(_capabilities(gates), gates)

        self.assertTrue(any("do not open converted lanes" in p for p in problems))

    def test_disabled_converted_write_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(
            _capabilities(gates), **{"write.appointment.checkin": "read_only"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(any("not enabled" in p for p in problems))

    def test_procedure_gate_off_requires_the_lane_to_stay_disabled(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = _capabilities(gates)

        self.assertEqual(
            {name: capabilities[name] for name in sorted(PROCEDURE)},
            {name: "read_only" for name in sorted(PROCEDURE)},
        )
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_procedure_enabled_while_its_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(
            _capabilities(gates), **{"write.procedure.complete": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any("enabled while closed" in p for p in problems), problems
        )
        self.assertTrue(
            any("write.procedure.complete" in p for p in problems), problems
        )

    def test_stock_consume_enabled_while_the_procedure_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(_capabilities(gates), **{"write.stock.consume": "enabled"})
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any("non-consuming" in p and "write.stock.consume" in p for p in problems),
            problems,
        )

    def test_open_procedure_gate_requires_only_create_and_complete(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = _capabilities(gates)

        self.assertEqual(
            {name for name in PROCEDURE if capabilities[name] == "enabled"},
            PROCEDURE_WRITES,
        )
        self.assertEqual(capabilities["write.stock.consume"], "read_only")
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_stock_consume_enabled_with_the_procedure_gate_open_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(_capabilities(gates), **{"write.stock.consume": "enabled"})
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any("non-consuming" in p and "write.stock.consume" in p for p in problems),
            problems,
        )
        self.assertFalse(
            any("not enabled" in p and "write.stock.consume" in p for p in problems),
            problems,
        )

    def test_procedure_create_and_complete_missing_with_the_gate_open_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(
            _capabilities(gates), **{"write.procedure.create": "read_only"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any("not enabled" in p and "write.procedure.create" in p for p in problems),
            problems,
        )

    def test_unrelated_families_are_not_claimed_ready(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = _capabilities(gates)

        for name in sorted(UNRELATED | strict.DEFERRED_WRITE_CAPABILITIES):
            self.assertEqual(capabilities[name], "read_only", name)
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_unrelated_family_readiness_is_not_asserted(self):
        # Open stock, purchase, finance and admin gates for a role the
        # development check does not cover: the procedure lane still passes and
        # no unrelated readiness is claimed either way.
        gates = _gates(
            patient=1,
            appointment=1,
            procedure=1,
            stock=1,
            purchase=1,
            finance=1,
            admin=1,
        )
        capabilities = _capabilities(gates)

        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_deferred_write_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(
            _capabilities(gates), **{"write.medication.order": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(any("deferred capabilities enabled" in p for p in problems))

    def test_missing_procedure_capability_is_reported_when_the_gate_is_off(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = {
            name: state
            for name, state in _capabilities(gates).items()
            if name != "write.procedure.create"
        }
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any("converted capabilities missing" in p and "write.procedure.create" in p for p in problems),
            problems,
        )

    def test_lane_report_shows_the_procedure_lane_disabled_when_off(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = _capabilities(gates)
        report = development.lane_report(capabilities, gates)

        self.assertEqual(
            tuple(report), ("patient", "appointment", "procedure", "purchase")
        )
        for name in PROCEDURE:
            self.assertEqual(
                report["procedure"][name], {"expected": False, "observed": "read_only"}
            )
        self.assertTrue(all(entry["expected"] for entry in report["patient"].values()))

    def test_lane_report_shows_the_procedure_lane_enabled_when_on(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        report = development.lane_report(_capabilities(gates), gates)

        for name in PROCEDURE_WRITES:
            self.assertEqual(
                report["procedure"][name], {"expected": True, "observed": "enabled"}
            )
        self.assertEqual(
            report["procedure"]["write.stock.consume"],
            {"expected": False, "observed": "read_only"},
        )


class PurchaseLaneTests(unittest.TestCase):
    def test_draft_is_the_only_converted_purchase_write(self):
        purchase = {
            name
            for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
            if family == "purchase"
        }

        self.assertEqual(purchase & set(development.CONVERTED), PURCHASE)
        self.assertEqual(
            purchase - set(development.CONVERTED), {"write.purchase_order.submit"}
        )
        self.assertTrue(FENCED <= set(strict.WRITE_CAPABILITY_GATES))

    def test_mgr_draft_enabled_when_the_purchase_gate_is_on(self):
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        capabilities = _capabilities(gates, role="MGR")

        self.assertEqual(capabilities["write.purchase_order.draft"], "enabled")
        self.assertEqual(capabilities["write.purchase_order.submit"], "read_only")
        self.assertEqual(capabilities["write.stock.receive"], "read_only")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="MGR"), []
        )

    def test_mgr_draft_must_stay_disabled_when_the_purchase_gate_is_off(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = _capabilities(gates, role="MGR")

        self.assertEqual(capabilities["write.purchase_order.draft"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="MGR"), []
        )

    def test_mgr_draft_enabled_while_the_purchase_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(
            _capabilities(gates, role="MGR"),
            **{"write.purchase_order.draft": "enabled"},
        )
        problems = development.lane_problems(capabilities, gates, role="MGR")

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.purchase_order.draft" in p
                for p in problems
            ),
            problems,
        )

    def test_mgr_does_not_inherit_the_patient_or_appointment_lanes(self):
        # MGR is not admitted by the patient or appointment policy sets; a
        # closed-family complaint here would be a false claim about this role.
        gates = _gates(purchase=1)
        capabilities = _capabilities(gates, role="MGR")
        problems = development.lane_problems(capabilities, gates, role="MGR")

        self.assertFalse(
            any("do not open converted lanes" in p for p in problems), problems
        )

    def test_doc_bootstrap_does_not_expect_the_purchase_draft(self):
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        capabilities = _capabilities(gates)

        self.assertEqual(capabilities["write.purchase_order.draft"], "read_only")
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_doc_purchase_draft_enabled_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        capabilities = dict(
            _capabilities(gates), **{"write.purchase_order.draft": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.purchase_order.draft" in p
                for p in problems
            ),
            problems,
        )

    def test_mgr_lane_report_covers_the_purchase_family(self):
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        report = development.lane_report(_capabilities(gates, role="MGR"), gates, role="MGR")

        self.assertIn("purchase", report)
        self.assertEqual(
            report["purchase"]["write.purchase_order.draft"],
            {"expected": True, "observed": "enabled"},
        )
        self.assertEqual(
            report["purchase"]["write.purchase_order.submit"],
            {"expected": False, "observed": "read_only"},
        )


class FencedWriteTests(unittest.TestCase):
    def test_unresolved_ledger_keeps_submit_and_receipt_disabled(self):
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        capabilities = _capabilities(gates, role="MGR")

        self.assertEqual(development.fenced_write_problems(capabilities, 2), [])

    def test_enabled_submit_with_an_unresolved_ledger_is_reported(self):
        gates = _gates(purchase=1)
        capabilities = dict(
            _capabilities(gates, role="MGR"),
            **{"write.purchase_order.submit": "enabled"},
        )
        problems = development.fenced_write_problems(capabilities, 2)

        self.assertTrue(
            any("write.purchase_order.submit" in p for p in problems), problems
        )

    def test_enabled_receipt_with_an_unresolved_ledger_is_reported(self):
        gates = _gates(purchase=1)
        capabilities = dict(
            _capabilities(gates, role="MGR"),
            **{"write.stock.receive": "enabled"},
        )
        problems = development.fenced_write_problems(capabilities, 2)

        self.assertTrue(
            any("write.stock.receive" in p for p in problems), problems
        )

    def test_resolved_ledger_makes_no_readiness_claim(self):
        gates = _gates(purchase=1)
        capabilities = dict(
            _capabilities(gates, role="MGR"),
            **{
                "write.purchase_order.submit": "enabled",
                "write.stock.receive": "enabled",
            },
        )

        self.assertEqual(development.fenced_write_problems(capabilities, 0), [])

    def test_unreadable_unresolved_count_is_reported(self):
        self.assertEqual(
            development.fenced_write_problems({}, None),
            ["historical fence unresolved count unreadable"],
        )


class CredentialTests(unittest.TestCase):
    def test_absent_mgr_credential_reports_inability(self):
        credentials = {
            "users": [
                {"clinic_code": "DOC", "email": "doc@example.invalid", "password": "x"},
                {"clinic_code": "MGR", "email": "mgr@example.invalid"},
            ]
        }

        self.assertIsNone(development.credential_for(credentials, "MGR"))

    def test_complete_mgr_credential_is_returned(self):
        mgr = {
            "clinic_code": "MGR",
            "email": "mgr@example.invalid",
            "password": "x",
        }
        credentials = {"users": [{"clinic_code": "DOC"}, mgr]}

        self.assertIs(development.credential_for(credentials, "MGR"), mgr)


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
