"""Designed stock receive/transfer/adjust POST methods (default off).

``POST /api/method/clinic_ui.api.stock_write.receive``,
``.../transfer`` and ``.../adjust`` are the stock family, with ``.../ack`` for
the one-shot barrier. Frappe performs the POST CSRF check before any method
runs, and the endpoint repeats an explicit same-session token check that does
not honour ``ignore_csrf`` or an allowed referrer. Every movement is posted by
the installed submittable document; no ledger row is written here.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import stock_write as stock_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def receive(**form):
    return stock_endpoint.receive(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def transfer(**form):
    return stock_endpoint.transfer(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def adjust(**form):
    return stock_endpoint.adjust(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return stock_endpoint.acknowledge(Gateway(), _body(form), _csrf_token(form))
