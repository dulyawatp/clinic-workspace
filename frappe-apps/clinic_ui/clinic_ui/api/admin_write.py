"""Designed admin users + masters POST methods (default off).

``POST /api/method/clinic_ui.api.admin_write.create_user`` and
``.../edit_master`` are the admin family, with ``.../ack`` for the one-shot
barrier. Frappe performs the POST CSRF check before any method runs, and the
endpoint repeats an explicit same-session token check that does not honour
``ignore_csrf`` or an allowed referrer. The client supplies the user password;
the gateway never generates a secret, and no credential is receipted or
returned.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import admin_write as admin_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def create_user(**form):
    return admin_endpoint.create_user(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def edit_master(**form):
    return admin_endpoint.edit_master(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return admin_endpoint.acknowledge(Gateway(), _body(form), _csrf_token(form))
