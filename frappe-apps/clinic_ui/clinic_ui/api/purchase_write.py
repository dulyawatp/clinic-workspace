"""Designed purchasing POST methods (default off).

``POST /api/method/clinic_ui.api.purchase_write.save_po_draft``,
``.../submit_po`` and ``.../receive`` are the purchasing family, with
``.../ack`` for the one-shot barrier. Frappe performs the POST CSRF check before
any method runs, and the endpoint repeats an explicit same-session token check
that does not honour ``ignore_csrf`` or an allowed referrer. Every document is
created or submitted by the installed buying controllers; no ledger row is
written here.

``save_po_draft`` is the converted draft lane: it inserts one draft purchase
order, never submits it, posts no stock or ledger row, and returns the native
docname with its status and ``docstatus``. ``submit_po`` and ``receive`` keep
the historical fence, deployment marker and acknowledgement.

Draft itemized lines carry only ``item_code``, ``qty`` and ``rate``. The
installed controller resolves the line-facing values the client never sends
(``item_name`` and the default target warehouse) during its own insert
lifecycle; this lane adds no custom derivation and guesses no warehouse.

``GET .../view`` returns one purchase order's ``purchase_order_id``, ``status``
and ``docstatus``; ``GET .../drafts`` lists the bounded, permission-filtered
draft orders for one readable Supplier. Both are read-only: they resolve no
write authority, read no item, quantity or amount, and do not depend on the
purchasing write flag.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import purchase_write as purchase_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def view(purchase_order_id=None):
    return purchase_endpoint.view(Gateway(), purchase_order_id)


@frappe.whitelist(methods=["GET"])
def drafts(supplier=None):
    return purchase_endpoint.drafts(Gateway(), supplier)


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
