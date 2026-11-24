"""Explicitly opted-in Owner doctor Finish methods."""

import frappe

from clinic_ui.api.patients import _body, _csrf_token
from clinic_ui.endpoints import owner_doctor_finish
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def options(practitioner):
    return owner_doctor_finish.options(Gateway(), practitioner)


@frappe.whitelist(methods=["GET"])
def reuse_options(practitioner):
    return owner_doctor_finish.reuse_options(Gateway(), practitioner)


@frappe.whitelist(methods=["GET"])
def reuse_review(practitioner, schedule, service_unit):
    return owner_doctor_finish.reuse_review(Gateway(), practitioner, schedule, service_unit)


@frappe.whitelist(methods=["POST"])
def finish(**form):
    return owner_doctor_finish.finish(Gateway(), _body(form), _csrf_token(form))
