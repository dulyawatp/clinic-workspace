"""Recurring hours for the signed-in doctor."""
import frappe

from clinic_ui.endpoints import doctor_hours
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def get():
    return doctor_hours.get(Gateway())
