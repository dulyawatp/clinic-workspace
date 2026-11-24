"""Default-off production Owner access to draft practitioner hours.

Site settings: clinic_ui_owner_doctor_hours_enabled must be JSON true;
clinic_ui_owner_doctor_hours_company must name the authorized native company.
"""

from clinic_ui.core import doctor_hours, owner_staff_contract
from clinic_ui.endpoints import production_read


def _gate(gateway, csrf=None, write=False):
    if gateway.setting("clinic_ui_owner_doctor_hours_enabled") is not True:
        gateway.deny()
    company = gateway.setting("clinic_ui_owner_doctor_hours_company")
    if not isinstance(company, str) or not company.strip() or len(company) > 140:
        gateway.deny()
    production = production_read.authorize_if_selected(gateway, owner_staff_contract.OWNER_ROLE_CODES)
    if production is None:
        gateway.deny()
    _, view, role = production
    headers = gateway.frappe.local.request.headers
    if (
        view.user in ("Administrator", "Guest")
        or role != "MGR"
        or headers.get("X-Clinic-Workspace") != "MGR"
        or not headers.get("X-Clinic-Session-Epoch")
    ):
        gateway.deny()
    roles = gateway.fresh_user_roles(view.user)
    if not isinstance(roles, tuple) or "System Manager" not in roles:
        gateway.deny()
    if write and not gateway.csrf_token_valid(csrf):
        gateway.deny()
    return company


def lookup(gateway):
    company = _gate(gateway)
    frappe = gateway.frappe
    try:
        doctor_hours.check_field_authority(frappe)
    except frappe.PermissionError:
        gateway.deny()
    if not all(frappe.has_permission(doctype, "read") for doctype in (
        "Healthcare Practitioner", "Healthcare Service Unit",
    )):
        gateway.deny()
    practitioners = []
    for row in frappe.get_list("Healthcare Practitioner", filters={"status": "Disabled"},
                               fields=["name"], page_length=100, order_by="name asc"):
        doc = frappe.get_doc("Healthcare Practitioner", row.name)
        if (frappe.has_permission("Healthcare Practitioner", "read", doc=doc)
                and frappe.has_permission("Healthcare Practitioner", "write", doc=doc)
                and doctor_hours.eligible_practitioner(doc)):
            practitioners.append({"name": doc.name, "version": str(doc.modified)})
    units = []
    for row in frappe.get_list("Healthcare Service Unit",
                               filters={"company": company, "is_group": 0, "allow_appointments": 1},
                               fields=["name"], page_length=100, order_by="name asc"):
        doc = frappe.get_doc("Healthcare Service Unit", row.name)
        if (frappe.has_permission("Healthcare Service Unit", "read", doc=doc)
                and doctor_hours.eligible_unit(doc, company)):
            units.append({"name": doc.name})
    return {"practitioners": practitioners, "serviceUnits": units}


def stage(gateway, body, csrf):
    company = _gate(gateway, csrf, write=True)
    keys = {"practitioner", "version", "serviceUnit", "slots"}
    if (not isinstance(body, dict) or set(body) != keys
            or any(not isinstance(body[key], str) or not body[key] or len(body[key]) > 140
                   for key in ("practitioner", "version", "serviceUnit"))):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        doctor_hours.validate_slots(body["slots"])
    except (ValueError, OverflowError):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        return doctor_hours.stage_hours(
            gateway, body["practitioner"], body["version"],
            body["serviceUnit"], company, body["slots"],
        )
    except Exception as exc:
        # Required even when translating: the normal POST commit must not
        # persist a schedule whose practitioner save failed.
        gateway.rollback()
        gateway.strip_message_channels()
        if isinstance(exc, doctor_hours.StalePractitioner):
            return gateway.respond_write(409, {"code": "STALE_VERSION"})
        if isinstance(exc, doctor_hours.IneligibleServiceUnit):
            return gateway.respond_write(422, {"code": "VALIDATION"})
        if isinstance(exc, gateway.frappe.PermissionError):
            gateway.deny()
        return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
