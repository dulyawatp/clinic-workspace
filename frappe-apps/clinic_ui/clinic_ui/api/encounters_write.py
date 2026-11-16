"""Designed Patient Encounter draft, complete and amendment POST methods.

``POST /api/method/clinic_ui.api.encounters_write.save_draft``,
``.../complete``, ``.../amend`` and ``.../ack`` are the default-off encounter
family. Frappe performs the POST CSRF check before any method runs, and the
endpoint repeats an explicit same-session token check that does not honour
``ignore_csrf`` or an allowed referrer. This module exposes no generic status
setter and accepts no docstatus from the client.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import encounters_write as encounter_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["POST"])
def save_draft(**form):
    return encounter_endpoint.save_draft(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def complete(**form):
    return encounter_endpoint.complete(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def amend(**form):
    return encounter_endpoint.amend(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def ack(**form):
    return encounter_endpoint.acknowledge(Gateway(), _body(form), _csrf_token(form))
