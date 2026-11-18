"""Synthetic appointment read, detail and converted write methods.

``GET /api/method/clinic_ui.api.appointments.synthetic_appointments`` keeps the
five-field list contract and adds the historical and native discovery sources.
``GET .../view`` returns the separate versioned detail contract.

``POST .../book``, ``.../reschedule`` and ``.../checkin`` are the converted
development write lane: they rely on Frappe/Marley permissions, validation and
transactions, with no operation fence, acknowledgement or deployment marker.
Frappe performs the POST CSRF check before any write method runs, and the
endpoint repeats an explicit same-session token check that does not honour
``ignore_csrf`` or an allowed referrer. This module never sets ``ignore_csrf``
and exposes no cancellation, acknowledgement or generic status setter.

Frappe wraps every returned mapping as ``{"message": ...}``.
"""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import appointments as appointments_endpoint
from clinic_ui.endpoints import appointments_write as appointments_write_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def synthetic_appointments():
    return appointments_endpoint.synthetic_appointments(Gateway())


@frappe.whitelist(methods=["GET"])
def view(workspace_appointment_id=None):
    return appointments_endpoint.appointment_detail(Gateway(), workspace_appointment_id)


@frappe.whitelist(methods=["POST"])
def book(**form):
    return appointments_write_endpoint.book(Gateway(), _body(form), _csrf_token(form))


@frappe.whitelist(methods=["POST"])
def reschedule(**form):
    return appointments_write_endpoint.reschedule(
        Gateway(), _body(form), _csrf_token(form)
    )


@frappe.whitelist(methods=["POST"])
def checkin(**form):
    return appointments_write_endpoint.checkin(
        Gateway(), _body(form), _csrf_token(form)
    )
