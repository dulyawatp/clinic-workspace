"""Designed Clinical Procedure create, completion and minimal read methods.

``POST /api/method/clinic_ui.api.procedures_write.create`` and ``.../complete``
are the converted development procedure family. They rely on Frappe/Marley
document permissions, validation, controller lifecycle and transactions; there
is no operation fence, deployment marker, write budget or acknowledgement.

Frappe performs the POST CSRF check before any method runs, and each write
endpoint repeats an explicit same-session token check that does not honour
``ignore_csrf`` or an allowed referrer. This module exposes no generic status
setter, accepts no docstatus, status, price list or item row from the client and
never sets ``ignore_csrf``. Creation returns the native docname of the row it
created; completion resolves the workspace id, a historical creation alias or a
native docname the current user may read and write.

``GET .../view`` returns one procedure's ``procedure_id``, ``status`` and
``docstatus``, and ``GET .../by_service_request`` lists the procedures linked to
one Service Request. Both are read-only: they resolve no write authority, read
no linked patient, practitioner or template identifier, and refuse an over-full
or ambiguous discovery page rather than truncating it.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import procedures_write as procedure_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def view(procedure_id=None):
    return procedure_endpoint.view(Gateway(), procedure_id)


@frappe.whitelist(methods=["GET"])
def by_service_request(service_request=None):
    return procedure_endpoint.by_service_request(Gateway(), service_request)


@frappe.whitelist(methods=["POST"])
def create(**form):
    return procedure_endpoint.create(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def complete(**form):
    return procedure_endpoint.complete(Gateway(), _body(form), _csrf_token(form))
