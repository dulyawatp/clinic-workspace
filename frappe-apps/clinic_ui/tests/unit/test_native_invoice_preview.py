import unittest
from types import SimpleNamespace

from clinic_ui.runtime.frappe_gateway import Gateway


class Invoice:
    def __init__(self, denied=None):
        self.events = []
        self.denied = denied
        self.values = {"doctype": "Sales Invoice", "docstatus": 0}
        self.meta = SimpleNamespace(fields=[
            SimpleNamespace(fieldname="customer", fieldtype="Link", options="Customer"),
            SimpleNamespace(fieldname="additional_discount_percentage", fieldtype="Percent", options=None),
            SimpleNamespace(fieldname="grand_total", fieldtype="Currency", options=None),
            SimpleNamespace(fieldname="items", fieldtype="Table", options="Sales Invoice Item"),
        ])

    def check_permission(self, action):
        self.events.append(action)
        if action == self.denied:
            raise PermissionError(action)

    def set(self, key, value):
        self.values[key] = value

    def validate_higher_perm_levels(self):
        self.events.append("validate_fields")

    def set_missing_values(self):
        self.events.append("missing")

    def calculate_taxes_and_totals(self):
        self.events.append("calculate")
        self.values["grand_total"] = 105

    def apply_fieldlevel_read_permissions(self):
        self.events.append("mask")
        self.values.pop("customer", None)

    def as_dict(self):
        return self.values.copy()


class Frappe:
    def __init__(self, denied=None):
        self.invoice = Invoice(denied)

    def new_doc(self, doctype):
        assert doctype == "Sales Invoice"
        return self.invoice

    def get_meta(self, doctype):
        assert doctype == "Sales Invoice Item"
        return SimpleNamespace(fields=[
            SimpleNamespace(fieldname="item_code", fieldtype="Link", options="Item"),
            SimpleNamespace(fieldname="qty", fieldtype="Float", options=None),
        ])


class NativeInvoicePreviewTests(unittest.TestCase):
    def test_preview_is_new_masked_native_invoice(self):
        frappe = Frappe()
        result = Gateway(frappe).preview_native_invoice({
            "doctype": "Sales Invoice", "name": "existing", "docstatus": 1,
            "flags": {"ignore_permissions": 1}, "customer": "C-1",
            "additional_discount_percentage": 5,
            "items": [{"doctype": "User", "name": "row", "item_code": "I-1",
                       "qty": 2, "flags": {"ignore_permissions": 1}}],
        })
        self.assertEqual(result["items"], [{"item_code": "I-1", "qty": 2}])
        self.assertEqual(result["grand_total"], 105)
        self.assertEqual(result["docstatus"], 0)
        self.assertNotIn("name", result)
        self.assertNotIn("customer", result)
        self.assertEqual(result["additional_discount_percentage"], 5)
        self.assertEqual(frappe.invoice.events,
                         ["create", "create", "validate_fields", "read", "missing", "calculate", "read", "mask"])

    def test_create_or_read_denial_stops_calculation(self):
        for denied in ("create", "read"):
            frappe = Frappe(denied)
            with self.subTest(denied=denied), self.assertRaises(PermissionError):
                Gateway(frappe).preview_native_invoice({"customer": "C-1"})
            self.assertNotIn("calculate", frappe.invoice.events)

    def test_rejects_non_invoice_and_malformed_children(self):
        for data in ({"doctype": "User"}, {"items": [{"qty": {"x": 1}}]},
                     {"items": {"doctype": "User"}}):
            with self.subTest(data=data), self.assertRaises(ValueError):
                Gateway(Frappe()).preview_native_invoice(data)


if __name__ == "__main__":
    unittest.main()
