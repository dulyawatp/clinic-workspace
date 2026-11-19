import unittest
from decimal import Decimal

from clinic_ui.runtime.visit_invoice import resolve_visit_invoice, visit_invoice_write_enabled


class Gateway:
    def __init__(self):
        self.evidence = {
            "metadata_verified": True, "record_fields_readable": True,
            "billing_history_complete": True, "prior_billing": False,
            "appointment_id": "APT-1", "practitioner": "PRAC-1",
            "actor_practitioner": "PRAC-1", "status": "Open", "docstatus": 0,
            "invoiced": 0, "ref_sales_invoice": None,
            "patient": "PAT-1", "customer": "CUS-1", "company": "CO-1",
            "currency": "USD", "service": "SVC-1", "price": Decimal("100.00"),
        }

    def visit_invoice_evidence(self, appointment_id):
        return self.evidence


class VisitInvoiceRuntimeTests(unittest.TestCase):
    def test_write_gate_exact_site_actor_config_and_key(self):
        class Gate:
            site = "clinic-visit-invoice.local"
            config = {"clinic_ui_visit_invoice_write_enabled": 1,
                      "clinic_ui_visit_invoice_write_user": "doctor@example.test",
                      "use_mysqlclient": 0}
            def _configuration(self):
                return self.config
            def visit_invoice_token_key(self):
                return b"k" * 32
        gate = Gate()
        actor = "doctor@example.test"
        self.assertTrue(visit_invoice_write_enabled(gate, actor))
        for key, values in {
            "clinic_ui_visit_invoice_write_enabled": [None, True, "true", 0],
            "clinic_ui_visit_invoice_write_user": [None, "Administrator", actor.upper()],
            "use_mysqlclient": [None, False, 1, "false"],
        }.items():
            original = gate.config[key]
            for value in values:
                with self.subTest(key=key, value=value):
                    gate.config[key] = value
                    self.assertFalse(visit_invoice_write_enabled(gate, actor))
            gate.config[key] = original
        self.assertFalse(visit_invoice_write_enabled(gate, "Administrator"))
        gate.site = "other.local"
        self.assertFalse(visit_invoice_write_enabled(gate, actor))
        gate.site = "clinic-visit-invoice.local"
        gate.visit_invoice_token_key = lambda: b"short"
        self.assertFalse(visit_invoice_write_enabled(gate, actor))

    def test_closed_requires_valid_encounter_and_returns_it(self):
        gateway = Gateway()
        gateway.evidence["status"] = "Closed"
        self.assertIsNone(resolve_visit_invoice(gateway, "APT-1"))
        gateway.evidence["encounter_id"] = "ENC-1"
        self.assertEqual(resolve_visit_invoice(gateway, "APT-1")["encounter_id"], "ENC-1")
        gateway.evidence["status"] = "Open"
        self.assertIsNone(resolve_visit_invoice(gateway, "APT-1"))

    def test_verified_inputs_are_minimal(self):
        result = resolve_visit_invoice(Gateway(), "APT-1")
        self.assertEqual(result["appointment_id"], "APT-1")
        self.assertEqual(result["price"], Decimal("100.00"))
        self.assertNotIn("actor_practitioner", result)

    def test_fail_closed(self):
        for key, value in (
            ("metadata_verified", False), ("record_fields_readable", False),
            ("billing_history_complete", False), ("prior_billing", True),
            ("appointment_id", "APT-2"), ("actor_practitioner", "PRAC-2"),
            ("status", "Cancelled"), ("status", "Unknown"), ("docstatus", 2),
            ("invoiced", 1), ("ref_sales_invoice", "INV-CANCELLED"),
            ("patient", None), ("customer", None), ("company", None),
            ("currency", None), ("service", None), ("price", Decimal("NaN")),
        ):
            gateway = Gateway()
            gateway.evidence[key] = value
            with self.subTest(key=key, value=value):
                self.assertIsNone(resolve_visit_invoice(gateway, "APT-1"))

    def test_unreadable_and_gate_are_closed(self):
        class Unreadable:
            def visit_invoice_evidence(self, appointment_id):
                raise PermissionError()
        self.assertIsNone(resolve_visit_invoice(Unreadable(), "APT-1"))
        self.assertFalse(visit_invoice_write_enabled())

    def test_missing_invoice_reference_is_not_proven_empty(self):
        gateway = Gateway()
        del gateway.evidence["ref_sales_invoice"]
        self.assertIsNone(resolve_visit_invoice(gateway, "APT-1"))

    def test_native_zero_fields_require_actual_integers(self):
        for key in ("docstatus", "invoiced"):
            for value in (False, 0.0, Decimal("0")):
                gateway = Gateway()
                gateway.evidence[key] = value
                with self.subTest(key=key, value=repr(value)):
                    self.assertIsNone(resolve_visit_invoice(gateway, "APT-1"))
