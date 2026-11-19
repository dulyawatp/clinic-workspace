"""Native encounter draft write method."""

import frappe

from clinic_ui.api.patients import _body
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def create_draft(**form):
    return Gateway().create_native_encounter_draft(_body(form))
