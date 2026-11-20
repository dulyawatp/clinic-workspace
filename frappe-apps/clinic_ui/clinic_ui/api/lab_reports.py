"""Private appointment reports using native File storage and permissions.

Returned private download URLs contain the exact File fid. Stock Frappe's
separate URL-only download behavior is unchanged.
"""

import frappe

from clinic_ui.endpoints import lab_reports
from clinic_ui.runtime.frappe_gateway import Gateway


def _call(method, *args):
    gateway = Gateway()
    gateway.mark_no_store()
    try:
        return method(gateway, *args)
    except ValueError:
        frappe.throw("Invalid lab report request", frappe.ValidationError)


@frappe.whitelist(methods=["GET"])
def list_for_appointment(appointment_ref=None, offset=0):
    return _call(lab_reports.list_for_appointment, appointment_ref, offset)


@frappe.whitelist(methods=["GET"])
def list_for_encounter(encounter_id=None, offset=0):
    return _call(lab_reports.list_for_encounter, encounter_id, offset)


@frappe.whitelist(methods=["POST"])
def upload(appointment_ref=None):
    gateway = Gateway()
    gateway.mark_no_store()
    request = frappe.request
    if (frappe.session.user == "Guest" or request.mimetype != "multipart/form-data"
            or request.args or set(request.form) != {"appointment_ref"}
            or len(request.form.getlist("appointment_ref")) != 1
            or set(request.files) != {"file"} or len(request.files.getlist("file")) != 1):
        frappe.throw("Invalid lab report request", frappe.ValidationError)
    from frappe.core.api.file import get_max_file_size

    file = request.files["file"]
    maximum = get_max_file_size()
    content = file.stream.read(maximum + 1)
    if len(content) > maximum:
        frappe.throw("File exceeds maximum size", frappe.ValidationError)
    try:
        return lab_reports.upload(gateway, appointment_ref, file.filename, content)
    except ValueError:
        frappe.throw("Invalid lab report request", frappe.ValidationError)
