"""Explicitly opted-in production Owner practitioner draft methods."""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import owner_doctor_profile
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def lookup():
    return owner_doctor_profile.lookup(Gateway())


@frappe.whitelist(methods=["POST"])
def create(**form):
    return owner_doctor_profile.create(Gateway(), _body(form), _csrf_token(form))
