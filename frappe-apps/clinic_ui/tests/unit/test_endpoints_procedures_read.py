"""Endpoint tests for the minimal Clinical Procedure read/discovery surface.

These prove the converted-lane inspection contract only: the shared
authenticated/no-store gate with the DOC/NUR role codes, the exact approved
site, the read lane's independence from the procedure write flag, one
permission-checked record read per row, the three-field response and the
refusal of an incomplete or ambiguous discovery page. The fake gateway still
proves gate ordering and payload shape only; it is not a Frappe simulation.
"""

import unittest

from clinic_ui.core import config, procedure_contract, procedure_write
from clinic_ui.endpoints import procedures_write

from .fake_gateway import (
    Denied,
    FakeGateway,
    FakeProcedureReceipt,
    SECRET,
    session_view,
    settings,
)

SITE = config.APPROVED_WRITE_SITE
ACTOR = "doctor@example.test"
CREATE_KEY = "proc-create-key-0001-01"
WORKSPACE = "WS-PROC-1"
NATIVE = "HLC-CPR-1"
NATIVE_2 = "HLC-CPR-2"
SERVICE_REQUEST = "HLC-SR-1"


def read_settings(**overrides):
    """Valid site settings with the procedure write flag left off."""

    values = settings(
        clinic_ui_synthetic_site=SITE,
        clinic_ui_synthetic_procedures=[
            {
                "workspace_procedure_id": WORKSPACE,
                "native_procedure_id": NATIVE,
            }
        ],
    )
    values.update(overrides)
    return values


def gateway(**overrides):
    values = {
        "site": SITE,
        "settings": read_settings(),
        "current_session": session_view(user=ACTOR, roles=("Physician",)),
    }
    values.update(overrides)
    init_keys = {"site", "settings", "current_session", "csrf_token"}
    fake = FakeGateway(
        **{key: value for key, value in values.items() if key in init_keys}
    )
    for key, value in values.items():
        if key not in init_keys:
            setattr(fake, key, value)
    return fake


def historical_create_receipt(fake, key=CREATE_KEY, native="HLC-CPR-0"):
    fake.procedure_receipt_records.append(
        FakeProcedureReceipt(
            "procedure_create",
            ACTOR,
            key,
            procedure_write.encode_receipt(
                kind="procedure_create",
                alias=procedure_write.build_alias(
                    SECRET.encode(), SITE, ACTOR, key
                ),
                native_procedure_id=native,
                service_request="HLC-SR-0",
            ),
        )
    )


class ViewTests(unittest.TestCase):
    def test_a_static_workspace_id_reads_the_native_row_and_echoes_the_id(self):
        fake = gateway()

        payload = procedures_write.view(fake, WORKSPACE)

        self.assertEqual(
            payload,
            {
                "procedure": {
                    "procedure_id": WORKSPACE,
                    "status": "Completed",
                    "docstatus": 1,
                }
            },
        )
        self.assertEqual(fake.procedure_view_calls, [NATIVE])

    def test_the_read_gate_sets_no_store_and_suppresses_the_traceback(self):
        fake = gateway()

        procedures_write.view(fake, WORKSPACE)

        self.assertEqual(fake.no_store_calls, 1)
        self.assertEqual(fake.traceback_suppressions, 1)

    def test_a_nurse_may_read_and_reception_may_not(self):
        nurse = gateway(
            current_session=session_view(user="nurse@example.test", roles=("Nurse",))
        )
        self.assertEqual(
            procedures_write.view(nurse, WORKSPACE)["procedure"]["procedure_id"],
            WORKSPACE,
        )

        reception = gateway(
            current_session=session_view(
                user="reception@example.test", roles=("Clinic Reception",)
            )
        )
        with self.assertRaises(Denied):
            procedures_write.view(reception, WORKSPACE)
        self.assertEqual(reception.procedure_view_calls, [])

    def test_the_read_lane_is_independent_of_the_procedure_write_flag(self):
        fake = gateway()
        self.assertIsNone(
            fake._settings.get("clinic_ui_native_procedure_write_enabled")
        )

        payload = procedures_write.view(fake, NATIVE)

        self.assertEqual(payload["procedure"]["procedure_id"], NATIVE)
        self.assertEqual(payload["procedure"]["status"], "Completed")

    def test_a_native_docname_requires_its_own_native_read_permission(self):
        fake = gateway(procedure_can_read=False)
        with self.assertRaises(Denied):
            procedures_write.view(fake, NATIVE_2)
        self.assertEqual(fake.procedure_view_calls, [])

    def test_a_historical_alias_resolves_read_only_through_its_receipt(self):
        fake = gateway(settings=read_settings(clinic_ui_synthetic_procedures=[]))
        historical_create_receipt(fake)
        alias = procedure_write.build_alias(SECRET.encode(), SITE, ACTOR, CREATE_KEY)

        payload = procedures_write.view(fake, alias)

        self.assertEqual(payload["procedure"]["procedure_id"], alias)
        self.assertEqual(fake.procedure_view_calls, ["HLC-CPR-0"])
        self.assertEqual(fake.procedure_ack_calls, [])
        self.assertEqual(fake.procedure_fence_calls, [])

    def test_a_foreign_alias_resolves_nothing_and_never_falls_back_to_native(self):
        fake = gateway(settings=read_settings(clinic_ui_synthetic_procedures=[]))
        foreign = procedure_write.build_alias(
            SECRET.encode(), SITE, "other@example.test", CREATE_KEY
        )

        with self.assertRaises(Denied):
            procedures_write.view(fake, foreign)
        self.assertEqual(fake.procedure_view_calls, [])

    def test_a_malformed_alias_namespace_value_never_falls_back_to_native(self):
        fake = gateway()

        with self.assertRaises(Denied):
            procedures_write.view(fake, "wsp1_truncated")
        self.assertEqual(fake.procedure_view_calls, [])

    def test_an_unknown_or_malformed_id_is_refused_before_any_read(self):
        for value in (None, "", "   ", " X ", 7, "X" * 200):
            with self.subTest(value=value):
                fake = gateway()
                with self.assertRaises(Denied):
                    procedures_write.view(fake, value)
                self.assertEqual(fake.procedure_view_calls, [])

    def test_the_read_requires_the_exact_approved_site(self):
        fake = gateway(
            site="marley.local",
            settings=read_settings(clinic_ui_synthetic_site="marley.local"),
        )

        with self.assertRaises(Denied):
            procedures_write.view(fake, WORKSPACE)
        self.assertEqual(fake.procedure_view_calls, [])

    def test_a_status_or_docstatus_outside_the_shape_fails_closed(self):
        overrides = (
            {"status": ""},
            {"status": "Completed "},
            {"status": None},
            {"docstatus": "1"},
            {"docstatus": True},
            {"docstatus": 7},
            {"docstatus": None},
            {"procedure_id": ""},
        )
        for override in overrides:
            with self.subTest(override=override):
                fake = gateway()
                fake.procedure_view_detail[NATIVE] = override
                with self.assertRaises(Denied):
                    procedures_write.view(fake, WORKSPACE)

    def test_a_raising_or_denied_record_read_fails_closed(self):
        raising = gateway()
        raising.procedure_view_error = RuntimeError("unreadable")
        with self.assertRaises(Denied):
            procedures_write.view(raising, WORKSPACE)

        denied = gateway()
        denied.procedure_view_authority = set()
        with self.assertRaises(Denied):
            procedures_write.view(denied, WORKSPACE)


class ByServiceRequestTests(unittest.TestCase):
    def test_items_carry_only_the_three_read_fields(self):
        fake = gateway()
        fake.readable_procedures = {SERVICE_REQUEST: [NATIVE, NATIVE_2]}
        fake.procedure_read_status[NATIVE_2] = {
            "status": "Pending",
            "docstatus": 1,
        }

        payload = procedures_write.by_service_request(fake, SERVICE_REQUEST)

        self.assertEqual(
            payload,
            {
                "items": [
                    {
                        "procedure_id": NATIVE,
                        "status": "Completed",
                        "docstatus": 1,
                    },
                    {
                        "procedure_id": NATIVE_2,
                        "status": "Pending",
                        "docstatus": 1,
                    },
                ]
            },
        )
        self.assertEqual(
            fake.doctype_read_calls,
            [(procedure_contract.SERVICE_REQUEST_DOCTYPE, SERVICE_REQUEST)],
        )
        self.assertEqual(fake.procedure_view_calls, [NATIVE, NATIVE_2])
        self.assertEqual(fake.procedure_list_calls, [(SERVICE_REQUEST, 200)])

    def test_the_empty_page_is_an_empty_list(self):
        fake = gateway()

        self.assertEqual(
            procedures_write.by_service_request(fake, SERVICE_REQUEST), {"items": []}
        )

    def test_the_service_request_read_permission_is_required(self):
        fake = gateway()
        fake.doctype_reads = {"Service Request": False}
        fake.readable_procedures = {SERVICE_REQUEST: [NATIVE]}

        with self.assertRaises(Denied):
            procedures_write.by_service_request(fake, SERVICE_REQUEST)
        self.assertEqual(fake.procedure_list_calls, [])

    def test_an_unreadable_row_refuses_the_whole_list(self):
        fake = gateway()
        fake.readable_procedures = {SERVICE_REQUEST: [NATIVE, NATIVE_2]}
        fake.procedure_view_authority = {NATIVE}

        with self.assertRaises(Denied):
            procedures_write.by_service_request(fake, SERVICE_REQUEST)

    def test_an_over_full_page_is_refused_not_truncated(self):
        fake = gateway()
        fake.readable_procedures = {
            SERVICE_REQUEST: [f"HLC-CPR-{index}" for index in range(201)]
        }

        with self.assertRaises(Denied):
            procedures_write.by_service_request(fake, SERVICE_REQUEST)
        self.assertEqual(fake.procedure_list_calls, [(SERVICE_REQUEST, 200)])

    def test_a_duplicate_row_is_refused_as_ambiguous(self):
        fake = gateway()
        fake.readable_procedures = {SERVICE_REQUEST: [NATIVE, NATIVE]}

        with self.assertRaises(Denied):
            procedures_write.by_service_request(fake, SERVICE_REQUEST)

    def test_a_malformed_row_name_is_refused(self):
        for name in (None, "", " ", " HLC-CPR-1", 7):
            with self.subTest(name=name):
                fake = gateway()
                fake.readable_procedures = {SERVICE_REQUEST: [name]}
                with self.assertRaises(Denied):
                    procedures_write.by_service_request(fake, SERVICE_REQUEST)

    def test_a_failed_or_bounded_list_read_fails_closed(self):
        raising = gateway()
        raising.procedure_list_error = RuntimeError("unreadable")
        with self.assertRaises(Denied):
            procedures_write.by_service_request(raising, SERVICE_REQUEST)

        missing = gateway()
        missing.readable_procedures = None
        with self.assertRaises(Denied):
            procedures_write.by_service_request(missing, SERVICE_REQUEST)

    def test_a_malformed_request_id_is_refused_before_any_read(self):
        for value in (None, "", "  ", " HLC-SR-1", 7, "S" * 200):
            with self.subTest(value=value):
                fake = gateway()
                with self.assertRaises(Denied):
                    procedures_write.by_service_request(fake, value)
                self.assertEqual(fake.doctype_read_calls, [])
                self.assertEqual(fake.procedure_list_calls, [])


if __name__ == "__main__":
    unittest.main()
