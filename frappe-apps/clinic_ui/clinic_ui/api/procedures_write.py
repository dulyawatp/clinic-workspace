"""Designed Clinical Procedure completion POST methods (default off).

``POST /api/method/clinic_ui.api.procedures_write.complete`` and ``.../ack`` are
the procedure family. Frappe performs the POST CSRF check before any method runs,
and the endpoint repeats an explicit same-session token check that does not honour
``ignore_csrf`` or an allowed referrer. This module exposes no generic status
setter, accepts no docstatus, status, price list or item row from the client and
never returns a native docname.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import procedures_write as procedure_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def complete(**form):
    return procedure_endpoint.complete(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return procedure_endpoint.acknowledge(Gateway(), _body(form), _csrf_token(form))
