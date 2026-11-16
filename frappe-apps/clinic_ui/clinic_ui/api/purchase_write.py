"""Designed purchasing POST methods (default off).

``POST /api/method/clinic_ui.api.purchase_write.save_po_draft``,
``.../submit_po`` and ``.../receive`` are the purchasing family, with
``.../ack`` for the one-shot barrier. Frappe performs the POST CSRF check before
any method runs, and the endpoint repeats an explicit same-session token check
that does not honour ``ignore_csrf`` or an allowed referrer. Every document is
created or submitted by the installed buying controllers; no ledger row is
written here.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import purchase_write as purchase_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def save_po_draft(**form):
    return purchase_endpoint.save_po_draft(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def submit_po(**form):
    return purchase_endpoint.submit_po(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def receive(**form):
    return purchase_endpoint.receive(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return purchase_endpoint.acknowledge(
        Gateway(), _body(form), _csrf_token(form)
    )
