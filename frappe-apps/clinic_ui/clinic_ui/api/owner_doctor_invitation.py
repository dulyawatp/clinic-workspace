"""Explicitly opted-in Owner doctor invitation methods."""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import owner_doctor_invitation
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def status(practitioner=None, user_id=None):
    return owner_doctor_invitation.status(Gateway(), practitioner, user_id)


@frappe.whitelist(methods=["POST"])
def send(**form):
    return owner_doctor_invitation.send(Gateway(), _body(form), _csrf_token(form))
