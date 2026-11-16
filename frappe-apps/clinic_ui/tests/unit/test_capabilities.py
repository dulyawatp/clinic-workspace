"""Capability-map tests and contract parity with the client contract file.

The server must publish every capability name the React client knows, all as
``read_only``. Parity is checked against ``contracts/capabilities.json`` when
that repository file is reachable; the check is skipped when the app is copied
outside the repository.
"""

import json
import re
import unittest
from pathlib import Path

from clinic_ui.core.capabilities import (
    CAPABILITY_NAMES,
    ENABLEABLE_NAMES,
    ENABLED_STATE,
    READ_ONLY_STATE,
    bootstrap_capabilities,
    read_only_capabilities,
    validate_capabilities,
)
from clinic_ui.core.policy import SUPPORTED_ROLE_CODES

# The ids added by the full write-contract wave, frozen by the design.
NEW_WRITE_NAMES = (
    "write.appointment.no_show",
    "write.encounter.amend",
    "write.invoice.void_amend",
    "write.stock.consume",
)


def contract_path():
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts" / "capabilities.json"
        if candidate.is_file():
            return candidate
    return None


def _repo_file(*parts):
    for parent in Path(__file__).resolve().parents:
        candidate = parent.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def _quoted(text, marker, terminator):
    """Return the quoted literals between ``marker`` and ``terminator``."""

    start = text.index(marker) + len(marker)
    end = text.index(terminator, start)
    return re.findall(r'"([^"]+)"', text[start:end])


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
            write_appointment_cancel=True,
            write_appointment_no_show=True,
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
                "write.appointment.cancel",
                "write.appointment.no_show",
                "write.appointment.checkin",
            },
        )
        self.assertEqual(
            capabilities["write.patient.create"], READ_ONLY_STATE
        )

    def test_appointment_writes_are_independently_requested(self):
        book_only = bootstrap_capabilities(write_appointment_book=True)
        self.assertEqual(book_only["write.appointment.book"], ENABLED_STATE)
        for name in (
            "write.appointment.reschedule",
            "write.appointment.cancel",
            "write.appointment.no_show",
            "write.appointment.checkin",
        ):
            with self.subTest(name=name):
                self.assertEqual(book_only[name], READ_ONLY_STATE)

    def test_cancel_and_no_show_ride_the_appointment_gate(self):
        for kwargs, name in (
            ({"write_appointment_cancel": True}, "write.appointment.cancel"),
            ({"write_appointment_no_show": True}, "write.appointment.no_show"),
        ):
            with self.subTest(name=name):
                capabilities = bootstrap_capabilities(**kwargs)
                self.assertEqual(capabilities[name], ENABLED_STATE)
                self.assertEqual(
                    capabilities["write.appointment.book"], READ_ONLY_STATE
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
            {**valid, "write.photo.upload": ENABLED_STATE},
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
            "write.appointment.cancel",
            "write.appointment.no_show",
            "write.appointment.checkin",
            "write.encounter.draft",
            "write.encounter.complete",
            "write.procedure.complete",
            "write.medication.order",
            "write.lab.order",
            "write.admin.user",
            "write.admin.master",
            *NEW_WRITE_NAMES,
        }
        for name in CAPABILITY_NAMES:
            if name in allowed:
                continue
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_capabilities({**valid, name: ENABLED_STATE})

    def test_new_write_ids_are_published_read_only(self):
        capabilities = read_only_capabilities()
        for name in NEW_WRITE_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, CAPABILITY_NAMES)
                self.assertEqual(capabilities[name], READ_ONLY_STATE)

    def test_new_write_ids_are_enableable_only_by_deployment_gate(self):
        valid = read_only_capabilities()
        for name in NEW_WRITE_NAMES:
            with self.subTest(name=name):
                self.assertIn(name, ENABLEABLE_NAMES)
                # Bootstrap enables only the reviewed flags it knows; the new
                # ids stay read_only until their own gate is wired.
                self.assertEqual(bootstrap_capabilities()[name], READ_ONLY_STATE)


class ContractParityTests(unittest.TestCase):
    def test_names_match_the_client_contract(self):
        path = contract_path()
        if path is None:
            self.skipTest("contracts/capabilities.json is not reachable")
        names = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(list(CAPABILITY_NAMES), list(names))

    def test_all_client_surfaces_stay_in_lockstep(self):
        """One lockstep check over every published capability surface.

        Drift between the contract JSON, the Python names, the client closed
        union, the client enableable subset and the policy role codes fails
        here. Later tasks extend this test rather than adding new parity ones.
        """

        json_path = contract_path()
        capability_ts = _repo_file("app", "src", "domain", "capability.ts")
        session_ts = _repo_file("app", "src", "transport", "native", "session.ts")
        if json_path is None or capability_ts is None or session_ts is None:
            self.skipTest("client contract surfaces are not reachable")

        names = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(list(CAPABILITY_NAMES), list(names))

        client_source = capability_ts.read_text(encoding="utf-8")
        client_union = _quoted(
            client_source, "export type Capability =", ";"
        )
        self.assertEqual(client_union, list(names))

        client_roles = _quoted(client_source, "export type RoleCode =", ";")
        self.assertEqual(set(client_roles), set(SUPPORTED_ROLE_CODES))

        session_source = session_ts.read_text(encoding="utf-8")
        client_enableable = _quoted(
            session_source, "const NATIVE_ENABLEABLE", "]);"
        )
        self.assertEqual(set(client_enableable), set(ENABLEABLE_NAMES))


if __name__ == "__main__":
    unittest.main()
