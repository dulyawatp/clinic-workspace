"""Designed finance POST methods (default off).

``POST /api/method/clinic_ui.api.finance_write.save_invoice_draft`` returns the
server-resolved invoice preview (it writes nothing),
``.../submit_invoice`` creates and submits one sales invoice and
``.../receive_payment`` posts one Payment Entry, with ``.../ack`` for the
one-shot barrier. Frappe performs the POST CSRF check before any method runs,
and the endpoint repeats an explicit same-session token check that does not
honour ``ignore_csrf`` or an allowed referrer. Every document is created or
submitted by the installed accounting controllers; no ledger row is written
here, and no card data is accepted.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import finance_write as finance_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def save_invoice_draft(**form):
    return finance_endpoint.save_invoice_draft(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def submit_invoice(**form):
    return finance_endpoint.submit_invoice(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def receive_payment(**form):
    return finance_endpoint.receive_payment(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return finance_endpoint.acknowledge(Gateway(), _body(form), _csrf_token(form))
