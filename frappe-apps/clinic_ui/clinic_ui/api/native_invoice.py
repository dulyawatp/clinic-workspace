"""Native, unsaved Sales Invoice preview."""

import frappe

from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def preview(doc):
    return Gateway().preview_native_invoice(doc)
