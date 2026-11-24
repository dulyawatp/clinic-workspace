"""Production Owner native staff methods."""

import frappe

from clinic_ui.endpoints import owner_staff
from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def list_staff(start=0, page_size=25):
    return owner_staff.list_staff(Gateway(), start, page_size)


@frappe.whitelist(methods=["GET"])
def role_options(user_id=None):
    return owner_staff.role_options(Gateway(), user_id)


@frappe.whitelist(methods=["POST"])
def update_roles(**form):
    return owner_staff.update_roles(Gateway(), _body(form), _csrf_token(form))
@frappe.whitelist(methods=["GET"])
def create_options():
    return owner_staff.create_options(Gateway())
@frappe.whitelist(methods=["POST"])
def create_staff(**form):
    return owner_staff.create_staff(Gateway(), _body(form), _csrf_token(form))
