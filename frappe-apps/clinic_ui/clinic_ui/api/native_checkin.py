"""Authenticated native appointment check-in."""

import frappe

from clinic_ui.api.patients import _body
from clinic_ui.endpoints import native_checkin
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def checkin(**form):
    try:
        return native_checkin.checkin(Gateway(), _body(form))
    except ValueError as exc:
        frappe.throw(str(exc), frappe.ValidationError)
