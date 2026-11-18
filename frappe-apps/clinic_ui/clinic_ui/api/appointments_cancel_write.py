"""Designed appointment cancel and manual no-show POST methods.

``POST /api/method/clinic_ui.api.appointments_cancel_write.cancel`` and
``.../no_show`` are the converted development transition lane. They rely on
Frappe/Marley permissions, validation and transactions, with no operation fence,
acknowledgement or deployment marker. Frappe performs the POST CSRF check before
any method runs, and the endpoint repeats an explicit same-session token check
that does not honour ``ignore_csrf`` or an allowed referrer. This module exposes
no generic status setter and accepts no status from the client.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import appointments_cancel_write as cancel_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def cancel(**form):
    return cancel_endpoint.cancel_appointment(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def no_show(**form):
    return cancel_endpoint.record_no_show(
        Gateway(), _body(form), _csrf_token(form)
    )
