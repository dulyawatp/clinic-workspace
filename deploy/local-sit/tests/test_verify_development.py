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
    "write.invoice.submit",
    "write.stock.adjust",
    "write.admin.user",
}

PROCEDURE = {
    "write.procedure.complete",
    "write.procedure.create",
    "write.stock.consume",
}

# The purchase lane converted only its draft. Submit and the PO-referenced
# receipt stay on the historical fence: an unresolved ledger keeps them off.
PURCHASE = {"write.purchase_order.draft"}
FENCED = {
    "write.purchase_order.submit",
    "write.stock.receive",
    "write.stock.transfer",
    "write.payment.receive",
    "write.admin.master",
}

# The stock lane converted only its adjustment. Receive and transfer stay
# fenced, and consumption is never consuming-ready here.
STOCK = {"write.stock.adjust"}
STOCK_FENCED = {"write.stock.receive", "write.stock.transfer"}

# The finance lane converted only its invoice. Payment receive stays fenced and
# read_only while the ledger carries unresolved keys.
FINANCE = {"write.invoice.submit"}
FINANCE_FENCED = {"write.payment.receive"}

# The admin lane converted only its user write. The master write stays fenced
# and is asserted disabled while the ledger carries unresolved keys.
ADMIN = {"write.admin.user"}
ADMIN_FENCED = {"write.admin.master"}

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
    "write.purchase_order.draft",
    "write.purchase_order.submit",
    "write.payment.receive",
    "write.admin.master",
}


def _gates(**opened):
    gates = {family: 0 for family in strict.GATE_CONFIG_KEYS}
    gates.update(opened)
    return gates


def _capabilities(gates, role="DOC", adjust_authority=None, purchase_authority=None):
    """Model the site bootstrap: expected writes minus the never-ready set.

    The fence-gated writes are modelled as disabled, which is the state an
    unresolved ledger produces. ``write.stock.adjust`` and
    ``write.purchase_order.draft`` follow the backend rule rather than the
    strict role map: an admitted role, the family flag and the caller's own
    probed native document authority.
    """

    capabilities = {name: "read_only" for name in strict.WRITE_CAPABILITY_GATES}
    expected = strict.expected_enabled_writes(gates, role) - {
        development.ADJUST_CAPABILITY,
        development.PURCHASE_DRAFT_CAPABILITY,
    }
    if (
        role in development.ADJUST_ROLE_SET
        and gates.get("stock") in (1, True, "1")
        and adjust_authority is True
    ):
        expected.add(development.ADJUST_CAPABILITY)
    if (
        role in development.PURCHASE_DRAFT_ROLE_SET
        and gates.get("purchase") in (1, True, "1")
        and purchase_authority is True
    ):
        expected.add(development.PURCHASE_DRAFT_CAPABILITY)
    expected = expected - development.NEVER_ENABLED - development.FENCED_WRITES
    for name in expected:
        capabilities[name] = "enabled"
    return capabilities


class LaneTests(unittest.TestCase):
    def test_converted_set_is_the_patient_appointment_procedure_purchase_stock_finance_and_admin_lanes(self):
        self.assertEqual(set(development.CONVERTED), CONVERTED)
        self.assertFalse(CONVERTED & strict.DEFERRED_WRITE_CAPABILITIES)
        self.assertEqual(
            development.CONVERTED_FAMILIES,
            (
                "patient",
                "appointment",
                "procedure",
                "purchase",
                "stock",
                "finance",
                "admin",
            ),
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
            tuple(report),
            (
                "patient",
                "appointment",
                "procedure",
                "purchase",
                "stock",
                "finance",
                "admin",
            ),
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
        capabilities = _capabilities(gates, role="MGR", purchase_authority=True)

        self.assertEqual(capabilities["write.purchase_order.draft"], "enabled")
        self.assertEqual(capabilities["write.purchase_order.submit"], "read_only")
        self.assertEqual(capabilities["write.stock.receive"], "read_only")
        self.assertEqual(capabilities["write.stock.consume"], "read_only")
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="MGR", purchase_authority=True
            ),
            [],
        )

    def test_mgr_draft_must_stay_disabled_without_native_create_authority(self):
        # MGR is in the purchasing role set and the gate is open, but the draft
        # is decided by its own native Purchase Order create right.
        gates = _gates(patient=1, appointment=1, procedure=1, purchase=1)
        capabilities = _capabilities(gates, role="MGR", purchase_authority=False)

        self.assertEqual(capabilities["write.purchase_order.draft"], "read_only")
        self.assertEqual(
            development.purchase_draft_problems(capabilities, gates, "MGR", False),
            [],
        )
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="MGR", purchase_authority=False
            ),
            [],
        )

    def test_pha_draft_read_only_without_native_create_authority(self):
        # The live false failure: PHA had the purchase flag and the role but no
        # native Purchase Order create right, so the draft must stay read_only.
        gates = _gates(purchase=1)
        capabilities = _capabilities(gates, role="PHA", purchase_authority=False)

        self.assertEqual(capabilities["write.purchase_order.draft"], "read_only")
        self.assertEqual(
            development.purchase_draft_problems(capabilities, gates, "PHA", False),
            [],
        )
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="PHA", purchase_authority=False
            ),
            [],
        )

    def test_pha_draft_enabled_without_authority_is_reported(self):
        gates = _gates(purchase=1)
        capabilities = dict(
            _capabilities(gates, role="PHA", purchase_authority=False),
            **{"write.purchase_order.draft": "enabled"},
        )

        problems = development.purchase_draft_problems(capabilities, gates, "PHA", False)
        self.assertTrue(any("write.purchase_order.draft" in p for p in problems))
        self.assertTrue(
            any(
                "enabled while closed" in p and "write.purchase_order.draft" in p
                for p in development.lane_problems(
                    capabilities, gates, role="PHA", purchase_authority=False
                )
            )
        )

    def test_pha_draft_enabled_with_authority_and_gate_is_accepted(self):
        gates = _gates(purchase=1)
        capabilities = _capabilities(gates, role="PHA", purchase_authority=True)

        self.assertEqual(capabilities["write.purchase_order.draft"], "enabled")
        self.assertEqual(
            development.purchase_draft_problems(capabilities, gates, "PHA", True), []
        )
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="PHA", purchase_authority=True
            ),
            [],
        )

    def test_unreadable_purchase_authority_is_a_problem_not_a_pass(self):
        gates = _gates(purchase=1)
        capabilities = _capabilities(gates, role="PHA")

        self.assertEqual(
            development.purchase_draft_problems(capabilities, gates, "PHA", None),
            ["native Purchase Order create authority unreadable"],
        )

    def test_mgr_draft_must_stay_disabled_when_the_purchase_gate_is_off(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = _capabilities(gates, role="MGR", purchase_authority=True)

        self.assertEqual(capabilities["write.purchase_order.draft"], "read_only")
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="MGR", purchase_authority=True
            ),
            [],
        )

    def test_mgr_draft_enabled_while_the_purchase_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1)
        capabilities = dict(
            _capabilities(gates, role="MGR", purchase_authority=True),
            **{"write.purchase_order.draft": "enabled"},
        )
        problems = development.lane_problems(
            capabilities, gates, role="MGR", purchase_authority=True
        )

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

    def test_expected_purchase_draft_requires_the_native_create_authority(self):
        gates = _gates(purchase=1)

        self.assertIn(
            "write.purchase_order.draft",
            development.expected_converted(
                gates, "MGR", purchase_authority=True
            ),
        )
        self.assertNotIn(
            "write.purchase_order.draft",
            development.expected_converted(
                gates, "MGR", purchase_authority=False
            ),
        )
        self.assertNotIn(
            "write.purchase_order.draft",
            development.expected_converted(gates, "MGR", purchase_authority=None),
        )

    def test_expected_purchase_draft_requires_the_open_purchase_gate(self):
        gates = _gates(patient=1, appointment=1)

        self.assertNotIn(
            "write.purchase_order.draft",
            development.expected_converted(
                gates, "MGR", purchase_authority=True
            ),
        )

    def test_expected_purchase_draft_requires_an_admitted_role(self):
        gates = _gates(purchase=1)

        self.assertNotIn(
            "write.purchase_order.draft",
            development.expected_converted(
                gates, "DOC", purchase_authority=True
            ),
        )
        self.assertIn(
            "write.purchase_order.draft",
            development.expected_converted(
                gates, "PHA", purchase_authority=True
            ),
        )
        self.assertEqual(
            tuple(development.PURCHASE_DRAFT_ROLE_SET), ("PHA", "MGR")
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
        report = development.lane_report(
            _capabilities(gates, role="MGR", purchase_authority=True),
            gates,
            role="MGR",
            purchase_authority=True,
        )

        self.assertIn("purchase", report)
        self.assertEqual(
            report["purchase"]["write.purchase_order.draft"],
            {"expected": True, "observed": "enabled"},
        )
        self.assertEqual(
            report["purchase"]["write.purchase_order.submit"],
            {"expected": False, "observed": "read_only"},
        )


class FinanceLaneTests(unittest.TestCase):
    def test_invoice_is_the_only_converted_finance_write(self):
        finance = {
            name
            for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
            if family == "finance"
        }

        self.assertEqual(finance & set(development.CONVERTED), FINANCE)
        self.assertEqual(finance - set(development.CONVERTED), FINANCE_FENCED)
        self.assertTrue(FINANCE_FENCED <= set(development.FENCED_WRITES))
        self.assertEqual(
            strict.WRITE_CAPABILITY_GATES["write.invoice.submit"], ("finance", ("CAS",))
        )
        self.assertEqual(
            strict.WRITE_CAPABILITY_GATES["write.payment.receive"],
            ("finance", ("CAS",)),
        )

    def test_cas_invoice_enabled_when_the_finance_gate_is_on(self):
        gates = _gates(finance=1)
        capabilities = _capabilities(gates, role="CAS")

        self.assertEqual(capabilities["write.invoice.submit"], "enabled")
        self.assertEqual(capabilities["write.payment.receive"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="CAS"), []
        )
        self.assertEqual(development.fenced_write_problems(capabilities, 2), [])

    def test_cas_invoice_must_stay_disabled_when_the_finance_gate_is_off(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = _capabilities(gates, role="CAS")

        self.assertEqual(capabilities["write.invoice.submit"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="CAS"), []
        )

    def test_cas_invoice_enabled_while_the_finance_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(
            _capabilities(gates, role="CAS"),
            **{"write.invoice.submit": "enabled"},
        )
        problems = development.lane_problems(capabilities, gates, role="CAS")

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.invoice.submit" in p
                for p in problems
            ),
            problems,
        )

    def test_cas_invoice_disabled_with_the_finance_gate_on_is_reported(self):
        gates = _gates(finance=1)
        capabilities = dict(
            _capabilities(gates, role="CAS"),
            **{"write.invoice.submit": "read_only"},
        )
        problems = development.lane_problems(capabilities, gates, role="CAS")

        self.assertTrue(
            any("not enabled" in p and "write.invoice.submit" in p for p in problems),
            problems,
        )

    def test_payment_receive_enabled_with_an_unresolved_ledger_is_reported(self):
        gates = _gates(finance=1)
        capabilities = dict(
            _capabilities(gates, role="CAS"),
            **{"write.payment.receive": "enabled"},
        )
        problems = development.fenced_write_problems(capabilities, 2)

        self.assertTrue(
            any("write.payment.receive" in p for p in problems), problems
        )

    def test_payment_receive_stays_read_only_with_an_unresolved_ledger(self):
        gates = _gates(finance=1)
        capabilities = _capabilities(gates, role="CAS")

        self.assertEqual(capabilities["write.payment.receive"], "read_only")
        self.assertEqual(development.fenced_write_problems(capabilities, 2), [])

    def test_unpublished_payment_receive_with_an_unresolved_ledger_is_reported(self):
        gates = _gates(finance=1)
        capabilities = {
            name: state
            for name, state in _capabilities(gates, role="CAS").items()
            if name != "write.payment.receive"
        }
        problems = development.fenced_write_problems(capabilities, 2)

        self.assertTrue(
            any(
                "not published" in p and "write.payment.receive" in p
                for p in problems
            ),
            problems,
        )

    def test_resolved_ledger_makes_no_payment_readiness_claim(self):
        gates = _gates(finance=1)
        capabilities = dict(
            _capabilities(gates, role="CAS"),
            **{"write.payment.receive": "enabled"},
        )

        self.assertEqual(development.fenced_write_problems(capabilities, 0), [])

    def test_doc_bootstrap_does_not_expect_the_invoice_lane(self):
        gates = _gates(patient=1, appointment=1, procedure=1, finance=1)
        capabilities = _capabilities(gates)

        self.assertEqual(capabilities["write.invoice.submit"], "read_only")
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_doc_invoice_enabled_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1, finance=1)
        capabilities = dict(
            _capabilities(gates), **{"write.invoice.submit": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.invoice.submit" in p
                for p in problems
            ),
            problems,
        )

    def test_cas_lane_report_covers_the_finance_family(self):
        gates = _gates(finance=1)
        report = development.lane_report(
            _capabilities(gates, role="CAS"), gates, role="CAS"
        )

        self.assertIn("finance", report)
        self.assertEqual(
            report["finance"]["write.invoice.submit"],
            {"expected": True, "observed": "enabled"},
        )
        self.assertEqual(
            report["finance"]["write.payment.receive"],
            {"expected": False, "observed": "read_only"},
        )


class AdminLaneTests(unittest.TestCase):
    def test_user_is_the_only_converted_admin_write(self):
        admin = {
            name
            for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
            if family == "admin"
        }

        self.assertEqual(admin & set(development.CONVERTED), ADMIN)
        self.assertEqual(admin - set(development.CONVERTED), ADMIN_FENCED)
        self.assertTrue(ADMIN_FENCED <= set(development.FENCED_WRITES))
        self.assertEqual(
            strict.WRITE_CAPABILITY_GATES["write.admin.user"], ("admin", ("ADM",))
        )

    def test_adm_user_enabled_when_the_admin_gate_is_on(self):
        gates = _gates(admin=1)
        capabilities = _capabilities(gates, role="ADM")

        self.assertEqual(capabilities["write.admin.user"], "enabled")
        self.assertEqual(capabilities["write.admin.master"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="ADM"), []
        )

    def test_adm_user_must_stay_disabled_when_the_admin_gate_is_off(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = _capabilities(gates, role="ADM")

        self.assertEqual(capabilities["write.admin.user"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, role="ADM"), []
        )

    def test_adm_user_enabled_while_the_admin_gate_is_off_is_reported(self):
        gates = _gates(patient=1, appointment=1)
        capabilities = dict(
            _capabilities(gates, role="ADM"),
            **{"write.admin.user": "enabled"},
        )
        problems = development.lane_problems(capabilities, gates, role="ADM")

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.admin.user" in p
                for p in problems
            ),
            problems,
        )

    def test_adm_user_disabled_with_the_admin_gate_on_is_reported(self):
        gates = _gates(admin=1)
        capabilities = dict(
            _capabilities(gates, role="ADM"),
            **{"write.admin.user": "read_only"},
        )
        problems = development.lane_problems(capabilities, gates, role="ADM")

        self.assertTrue(
            any("not enabled" in p and "write.admin.user" in p for p in problems),
            problems,
        )

    def test_admin_master_enabled_with_an_unresolved_ledger_is_reported(self):
        gates = _gates(admin=1)
        capabilities = dict(
            _capabilities(gates, role="ADM"),
            **{"write.admin.master": "enabled"},
        )
        problems = development.fenced_write_problems(capabilities, 2)

        self.assertTrue(
            any("write.admin.master" in p for p in problems), problems
        )

    def test_admin_master_stays_disabled_with_an_unresolved_ledger(self):
        gates = _gates(admin=1)
        capabilities = _capabilities(gates, role="ADM")

        self.assertEqual(capabilities["write.admin.master"], "read_only")
        self.assertEqual(development.fenced_write_problems(capabilities, 2), [])

    def test_contract_adm_must_not_advertise_the_user_lane(self):
        # The contract ADM user carries the clinic role only: it does not hold
        # native User create, so an enabled lane there would be advertised from
        # the site flag rather than from authority.
        gates = _gates(admin=1)
        capabilities = _capabilities(gates, role="ADM")
        problems = development.contract_admin_problems(capabilities, 2)

        self.assertTrue(
            any(
                "User create authority" in p and "write.admin.user" in p
                for p in problems
            ),
            problems,
        )

    def test_contract_adm_read_only_admin_writes_pass(self):
        gates = _gates(admin=1)
        capabilities = {name: "read_only" for name in strict.WRITE_CAPABILITY_GATES}

        self.assertEqual(development.contract_admin_problems(capabilities, 2), [])

    def test_contract_adm_enabled_master_with_an_unresolved_ledger_is_reported(self):
        capabilities = {
            name: "read_only" for name in strict.WRITE_CAPABILITY_GATES
        }
        capabilities["write.admin.master"] = "enabled"
        problems = development.contract_admin_problems(capabilities, 2)

        self.assertTrue(
            any("write.admin.master" in p for p in problems), problems
        )

    def test_contract_adm_unreadable_capabilities_are_reported(self):
        self.assertEqual(
            development.contract_admin_problems(None, 2),
            ["capabilities are not a map"],
        )

    def test_doc_bootstrap_does_not_expect_the_admin_lane(self):
        gates = _gates(patient=1, appointment=1, procedure=1, admin=1)
        capabilities = _capabilities(gates)

        self.assertEqual(capabilities["write.admin.user"], "read_only")
        self.assertEqual(capabilities["write.admin.master"], "read_only")
        self.assertEqual(development.lane_problems(capabilities, gates), [])

    def test_doc_admin_user_enabled_is_reported(self):
        gates = _gates(patient=1, appointment=1, procedure=1, admin=1)
        capabilities = dict(
            _capabilities(gates), **{"write.admin.user": "enabled"}
        )
        problems = development.lane_problems(capabilities, gates)

        self.assertTrue(
            any(
                "enabled while closed" in p and "write.admin.user" in p
                for p in problems
            ),
            problems,
        )

    def test_adm_lane_report_covers_the_admin_family(self):
        gates = _gates(admin=1)
        report = development.lane_report(
            _capabilities(gates, role="ADM"), gates, role="ADM"
        )

        self.assertIn("admin", report)
        self.assertEqual(
            report["admin"]["write.admin.user"],
            {"expected": True, "observed": "enabled"},
        )
        self.assertEqual(
            report["admin"]["write.admin.master"],
            {"expected": False, "observed": "read_only"},
        )


class StockLaneTests(unittest.TestCase):
    def test_adjust_is_the_only_converted_stock_write(self):
        stock = {
            name
            for name, (family, _roles) in strict.WRITE_CAPABILITY_GATES.items()
            if family == "stock"
        }

        self.assertEqual(stock & set(development.CONVERTED), STOCK)
        self.assertEqual(stock - set(development.CONVERTED), STOCK_FENCED)
        self.assertTrue(STOCK_FENCED <= set(development.FENCED_WRITES))
        self.assertEqual(development.ADJUST_CAPABILITY, "write.stock.adjust")
        self.assertEqual(tuple(development.ADJUST_ROLE_SET), ("PHA", "ADM"))
        # The strict role map admits the adjustment for ADM only; this check
        # decides it from the probed authority instead.
        self.assertEqual(
            strict.WRITE_CAPABILITY_GATES["write.stock.adjust"],
            ("stock", ("ADM",)),
        )

    def test_expected_adjust_requires_the_native_authority(self):
        gates = _gates(stock=1)

        self.assertIn(
            "write.stock.adjust",
            development.expected_converted(gates, "PHA", True),
        )
        self.assertNotIn(
            "write.stock.adjust",
            development.expected_converted(gates, "PHA", False),
        )
        self.assertNotIn(
            "write.stock.adjust",
            development.expected_converted(gates, "PHA", None),
        )

    def test_expected_adjust_requires_the_open_stock_gate(self):
        gates = _gates(patient=1, appointment=1)

        self.assertNotIn(
            "write.stock.adjust",
            development.expected_converted(gates, "PHA", True),
        )

    def test_expected_adjust_requires_an_admitted_role(self):
        gates = _gates(stock=1)

        self.assertNotIn(
            "write.stock.adjust",
            development.expected_converted(gates, "DOC", True),
        )

    def test_unreadable_authority_is_a_problem_not_a_pass(self):
        gates = _gates(stock=1)
        capabilities = _capabilities(gates, role="PHA")

        self.assertEqual(
            development.adjust_lane_problems(capabilities, gates, "PHA", None),
            ["native Stock Reconciliation authority unreadable"],
        )

    def test_pha_adjust_enabled_with_authority_and_gate_is_accepted(self):
        gates = _gates(stock=1)
        capabilities = _capabilities(gates, role="PHA", adjust_authority=True)

        self.assertEqual(capabilities["write.stock.adjust"], "enabled")
        self.assertEqual(
            development.adjust_lane_problems(capabilities, gates, "PHA", True), []
        )
        self.assertEqual(
            development.lane_problems(
                capabilities, gates, role="PHA", adjust_authority=True
            ),
            [],
        )

    def test_pha_adjust_enabled_without_authority_is_reported(self):
        # The flag and the role are not enough: no native reconciliation
        # authority means the advertised enabled state is a defect.
        gates = _gates(stock=1)
        capabilities = dict(
            _capabilities(gates, role="PHA", adjust_authority=False),
            **{"write.stock.adjust": "enabled"},
        )

        problems = development.adjust_lane_problems(capabilities, gates, "PHA", False)
        self.assertTrue(any("write.stock.adjust" in p for p in problems), problems)
        self.assertTrue(
            any(
                "enabled while closed" in p and "write.stock.adjust" in p
                for p in development.lane_problems(
                    capabilities, gates, role="PHA", adjust_authority=False
                )
            )
        )

    def test_pha_adjust_read_only_with_authority_and_gate_open_is_reported(self):
        gates = _gates(stock=1)
        capabilities = _capabilities(gates, role="PHA", adjust_authority=True)
        capabilities["write.stock.adjust"] = "read_only"

        problems = development.adjust_lane_problems(capabilities, gates, "PHA", True)
        self.assertTrue(any("expected 'enabled'" in p for p in problems), problems)
        self.assertTrue(
            any(
                "not enabled" in p and "write.stock.adjust" in p
                for p in development.lane_problems(
                    capabilities, gates, role="PHA", adjust_authority=True
                )
            )
        )

    def test_adm_adjust_without_authority_stays_read_only(self):
        gates = _gates(stock=1)
        capabilities = _capabilities(gates, role="ADM", adjust_authority=False)

        self.assertEqual(capabilities["write.stock.adjust"], "read_only")
        self.assertEqual(
            development.adjust_lane_problems(capabilities, gates, "ADM", False), []
        )

    def test_adm_adjust_enabled_without_authority_is_reported(self):
        gates = _gates(stock=1)
        capabilities = dict(
            _capabilities(gates, role="ADM", adjust_authority=False),
            **{"write.stock.adjust": "enabled"},
        )

        problems = development.adjust_lane_problems(capabilities, gates, "ADM", False)
        self.assertTrue(any("write.stock.adjust" in p for p in problems), problems)

    def test_doc_is_never_expected_to_adjust(self):
        gates = _gates(patient=1, appointment=1, stock=1)
        capabilities = _capabilities(gates, adjust_authority=True)

        self.assertEqual(capabilities["write.stock.adjust"], "read_only")
        self.assertEqual(
            development.lane_problems(capabilities, gates, adjust_authority=True), []
        )

    def test_fenced_stock_writes_stay_disabled_with_an_unresolved_ledger(self):
        gates = _gates(stock=1)
        capabilities = _capabilities(gates, role="PHA", adjust_authority=True)

        for name in sorted(STOCK_FENCED):
            self.assertEqual(capabilities[name], "read_only", name)
        self.assertEqual(capabilities["write.stock.consume"], "read_only")
        self.assertEqual(development.fenced_write_problems(capabilities, 2), [])

    def test_stock_lane_report_covers_the_stock_family(self):
        gates = _gates(stock=1)
        report = development.lane_report(
            _capabilities(gates, role="PHA", adjust_authority=True),
            gates,
            role="PHA",
            adjust_authority=True,
        )

        self.assertIn("stock", report)
        self.assertEqual(
            report["stock"]["write.stock.adjust"],
            {"expected": True, "observed": "enabled"},
        )
        for name in sorted(STOCK_FENCED):
            self.assertEqual(
                report["stock"][name], {"expected": False, "observed": "read_only"}
            )


class NativeAuthorityProbeTests(unittest.TestCase):
    def test_probe_script_reads_each_document_right_for_each_identity(self):
        script = development.native_authority_probe_script(
            ["pha@example.invalid", "mgr@example.invalid", "adm@example.invalid"]
        )

        self.assertIn("'Stock Reconciliation'", script)
        self.assertIn("'Purchase Order'", script)
        self.assertIn("('create', 'submit')", script)
        self.assertIn("('create',)", script)
        self.assertIn("'pha@example.invalid'", script)
        self.assertIn("'mgr@example.invalid'", script)
        self.assertIn("'adm@example.invalid'", script)
        self.assertNotIn("password", script)

    def test_stock_authority_requires_both_rights(self):
        observed = {
            "both@example.invalid": {
                "stock_reconciliation": {"create": True, "submit": True}
            },
            "create@example.invalid": {
                "stock_reconciliation": {"create": True, "submit": False}
            },
            "submit@example.invalid": {
                "stock_reconciliation": {"create": False, "submit": True}
            },
            "none@example.invalid": {
                "stock_reconciliation": {"create": False, "submit": False}
            },
        }

        self.assertIs(
            development.stock_adjust_authority(observed, "both@example.invalid"),
            True,
        )
        self.assertIs(
            development.stock_adjust_authority(observed, "create@example.invalid"),
            False,
        )
        self.assertIs(
            development.stock_adjust_authority(observed, "submit@example.invalid"),
            False,
        )
        self.assertIs(
            development.stock_adjust_authority(observed, "none@example.invalid"),
            False,
        )

    def test_purchase_draft_authority_reads_the_create_right_only(self):
        observed = {
            "create@example.invalid": {"purchase_order": {"create": True}},
            "none@example.invalid": {"purchase_order": {"create": False}},
        }

        self.assertIs(
            development.purchase_draft_authority(observed, "create@example.invalid"),
            True,
        )
        self.assertIs(
            development.purchase_draft_authority(observed, "none@example.invalid"),
            False,
        )

    def test_unreadable_probe_is_unknown(self):
        for observed in (
            None,
            {},
            {"missing@example.invalid": None},
            {"bad@example.invalid": {"stock_reconciliation": {"create": True}}},
            {
                "bad@example.invalid": {
                    "stock_reconciliation": {"create": 1, "submit": 1}
                }
            },
            {"bad@example.invalid": {"purchase_order": {}}},
            {"bad@example.invalid": {"purchase_order": {"create": "yes"}}},
            {"bad@example.invalid": {"purchase_order": None}},
        ):
            with self.subTest(observed=observed):
                self.assertIsNone(
                    development.stock_adjust_authority(
                        observed, "bad@example.invalid"
                    )
                )
                self.assertIsNone(
                    development.purchase_draft_authority(
                        observed, "bad@example.invalid"
                    )
                )

    def test_empty_probe_targets_no_identity(self):
        self.assertIn(
            "emails = []", development.native_authority_probe_script([])
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

    def test_missing_pha_credential_fails_instead_of_passing_unverified(self):
        credentials = {
            "users": [
                {"clinic_code": "DOC", "email": "doc@example.invalid", "password": "x"},
                {"clinic_code": "PHA", "email": "pha@example.invalid"},
            ]
        }
        problem = development.pha_credential_problem(credentials)

        self.assertIsNotNone(problem)
        self.assertIn("PHA", problem)
        self.assertIn("cannot be verified", problem)

    def test_complete_pha_credential_raises_no_problem(self):
        credentials = {
            "users": [
                {
                    "clinic_code": "PHA",
                    "email": "pha@example.invalid",
                    "password": "x",
                    "roles": ["Stock Manager"],
                }
            ]
        }

        self.assertIsNone(development.pha_credential_problem(credentials))

    def test_missing_cas_credential_fails_instead_of_passing_unverified(self):
        credentials = {
            "users": [
                {"clinic_code": "DOC", "email": "doc@example.invalid", "password": "x"},
                {"clinic_code": "CAS", "email": "cas@example.invalid"},
            ]
        }
        problem = development.cas_credential_problem(credentials)

        self.assertIsNotNone(problem)
        self.assertIn("CAS", problem)
        self.assertIn("cannot be verified", problem)

    def test_complete_cas_credential_raises_no_problem(self):
        credentials = {
            "users": [
                {
                    "clinic_code": "CAS",
                    "email": "cas@example.invalid",
                    "password": "x",
                    "roles": ["Accounts User"],
                }
            ]
        }

        self.assertIsNone(development.cas_credential_problem(credentials))

    def test_missing_adm_credential_fails_instead_of_passing_unverified(self):
        credentials = {
            "users": [
                {"clinic_code": "DOC", "email": "doc@example.invalid", "password": "x"},
                {"clinic_code": "ADM", "email": "adm@example.invalid"},
            ]
        }
        problem = development.adm_credential_problem(credentials)

        self.assertIsNotNone(problem)
        self.assertIn("adm_bootstrap", problem)
        self.assertIn("cannot be verified", problem)

    def test_complete_adm_bootstrap_credential_raises_no_problem(self):
        credentials = {
            "users": [
                {"clinic_code": "DOC", "email": "doc@example.invalid", "password": "x"},
                {
                    "clinic_code": "ADM",
                    "email": "adm@example.invalid",
                    "password": "x",
                },
            ],
            "adm_bootstrap": {
                "email": "bootstrap@example.invalid",
                "password": "x",
                "roles": ["System Manager", "Item Manager"],
            },
        }

        self.assertIsNone(development.adm_credential_problem(credentials))

    def test_contract_adm_user_does_not_substitute_for_the_bootstrap(self):
        # A complete contract ADM credential (clinic role only, no native User
        # create) must not be accepted as the enabled-lane identity.
        credentials = {
            "users": [
                {
                    "clinic_code": "ADM",
                    "email": "adm@example.invalid",
                    "password": "x",
                    "roles": ["Item Manager"],
                }
            ]
        }

        self.assertIsNone(development.adm_bootstrap_credential(credentials))
        self.assertIsNotNone(development.adm_credential_problem(credentials))

    def test_adm_bootstrap_credential_is_selected_over_the_contract_user(self):
        bootstrap = {
            "email": "bootstrap@example.invalid",
            "password": "x",
            "roles": ["System Manager", "Item Manager"],
        }
        credentials = {
            "users": [
                {"clinic_code": "ADM", "email": "adm@example.invalid", "password": "x"}
            ],
            "adm_bootstrap": bootstrap,
        }

        self.assertIs(development.adm_bootstrap_credential(credentials), bootstrap)
        self.assertEqual(
            development.credential_for(credentials, "ADM")["email"],
            "adm@example.invalid",
        )

    def test_incomplete_adm_bootstrap_is_absent(self):
        for entry in (
            {"email": "bootstrap@example.invalid"},
            {"password": "x"},
            {"email": "", "password": "x"},
            "not-a-map",
        ):
            with self.subTest(entry=entry):
                self.assertIsNone(
                    development.adm_bootstrap_credential({"adm_bootstrap": entry})
                )


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
