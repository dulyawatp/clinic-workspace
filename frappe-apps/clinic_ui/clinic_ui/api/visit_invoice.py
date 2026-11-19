"""Visit-linked invoice API; write lane remains independently gated."""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import visit_invoice as invoice_endpoint
from clinic_ui.endpoints.visit_invoice import submit as submit_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def view(appointment_id=None):
    return invoice_endpoint.view(Gateway(), appointment_id)


@frappe.whitelist(methods=["POST"])
def preview(**form):
    return invoice_endpoint.preview(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def submit(**form):
    return submit_endpoint(Gateway(), _body(form), _csrf_token(form))
