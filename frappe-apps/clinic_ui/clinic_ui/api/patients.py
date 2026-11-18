"""Patient read and local synthetic write methods.

``GET /api/method/clinic_ui.api.patients.view`` reads one record: a static
workspace alias, a historical sealed alias or a native ``Patient`` docname the
current user may read.

``POST /api/method/clinic_ui.api.patients.create`` and ``...update`` are the
converted development write lane. They rely on Frappe/Marley permissions,
validation and transactions; there is no operation fence, acknowledgement or
deployment marker. Frappe performs the POST CSRF check before the method runs,
and the endpoint repeats an explicit same-session token check that does not
honour ``ignore_csrf`` or an allowed referrer. This module never sets
``ignore_csrf``.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.endpoints import patients as patients_endpoint
from clinic_ui.endpoints import patients_write as patients_write_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway

# Transport keys consumed by Frappe itself; they are not Patient fields.
_TRANSPORT_KEYS = frozenset({"csrf_token", "cmd"})


def _body(form):
    return {key: value for key, value in form.items() if key not in _TRANSPORT_KEYS}


def _csrf_token(form):
    token = form.get("csrf_token")
    if isinstance(token, str) and token:
        return token
    try:
        header = frappe.get_request_header("X-Frappe-CSRF-Token")
    except Exception:
        header = None
    if isinstance(header, str) and header:
        return header
    # Frappe's own CSRF check pops a body-only ``csrf_token`` from form_dict
    # before dispatch. Read the cached parsed JSON so the explicit check can
    # still verify a body-supplied token. Any failure leaves it missing, which
    # the endpoint rejects.
    try:
        data = frappe.request.get_json(silent=True)
    except Exception:
        data = None
    if isinstance(data, dict):
        cached = data.get("csrf_token")
        if isinstance(cached, str) and cached:
            return cached
    return None


@frappe.whitelist(methods=["GET"])
def view(workspace_patient_id=None):
    return patients_endpoint.view(Gateway(), workspace_patient_id)


@frappe.whitelist(methods=["POST"])
def create(**form):
    return patients_write_endpoint.create(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def update(**form):
    return patients_write_endpoint.update(
        Gateway(), _body(form), _csrf_token(form)
    )
