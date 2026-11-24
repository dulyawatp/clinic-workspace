"""Explicitly opted-in production Owner draft-hours methods."""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import owner_doctor_hours
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def lookup():
    return owner_doctor_hours.lookup(Gateway())


@frappe.whitelist(methods=["POST"])
def stage(**form):
    return owner_doctor_hours.stage(Gateway(), _body(form), _csrf_token(form))
