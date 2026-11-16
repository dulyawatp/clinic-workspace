"""The durable write budget registry for the full write-contract wave.

The numbers are the user-approved final set. They are a deployment property of
the isolated SIT, not client input and not an in-memory counter: one unit is
one contract write operation that claimed the fence.
"""

import unittest

from clinic_ui.core import write_budgets

# kind -> (approved budget, family)
EXPECTED = {
    "appointment_cancel": (2, "appointment"),
    "appointment_no_show": (1, "appointment"),
    "encounter_draft": (2, "encounter"),
    "encounter_complete": (1, "encounter"),
    "encounter_amend": (1, "encounter"),
    "procedure_create": (1, "procedure"),
    "procedure_complete": (1, "procedure"),
    "lab_order": (1, "lab"),
    "stock_receive": (1, "stock"),
    "stock_transfer": (1, "stock"),
    "stock_adjust": (1, "stock"),
    "purchase_order_draft": (1, "purchase"),
    "purchase_order_submit": (1, "purchase"),
    "purchase_receipt": (1, "purchase"),
    "invoice_submit": (1, "invoice"),
    "payment_receive": (1, "payment"),
    "admin_user": (4, "admin"),
    "admin_master": (15, "admin"),
}


class WriteBudgetRegistryTests(unittest.TestCase):
    def test_registry_contains_exactly_the_approved_kinds(self):
        self.assertEqual(len(EXPECTED), 18)
        self.assertEqual(write_budgets.ALL_KINDS, tuple(EXPECTED))
        self.assertEqual(tuple(write_budgets.BUDGETS), tuple(EXPECTED))

    def test_every_kind_resolves_to_its_approved_budget(self):
        for kind, (budget, _family) in EXPECTED.items():
            with self.subTest(kind=kind):
                self.assertEqual(write_budgets.budget_for(kind), budget)
                self.assertEqual(write_budgets.BUDGETS[kind]["budget"], budget)
                self.assertEqual(
                    write_budgets.BUDGETS[kind]["family"], EXPECTED[kind][1]
                )

    def test_registry_entries_only_carry_budget_and_family(self):
        for kind, entry in write_budgets.BUDGETS.items():
            with self.subTest(kind=kind):
                self.assertEqual(set(entry), {"budget", "family"})
                self.assertIsInstance(entry["budget"], int)
                self.assertGreater(entry["budget"], 0)
                self.assertIsInstance(entry["family"], str)

    def test_unknown_or_malformed_kind_is_refused(self):
        for kind in ("", "book", "appointment_cancel ", "unknown", None, 1, []):
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    write_budgets.budget_for(kind)


if __name__ == "__main__":
    unittest.main()
