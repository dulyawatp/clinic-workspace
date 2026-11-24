"""Default-off production Owner access to native practitioner drafts."""

from uuid import uuid4

from clinic_ui.core import owner_doctor_profile, owner_staff_contract
from clinic_ui.endpoints import production_read


def _gate(gateway, csrf=None, write=False):
    site = gateway.setting("clinic_ui_owner_doctor_profile_site")
    if (gateway.setting("clinic_ui_owner_doctor_profile_enabled") is not True
            or not isinstance(site, str) or not site or site != gateway.site):
        gateway.deny()
    production = production_read.authorize_if_selected(gateway, owner_staff_contract.OWNER_ROLE_CODES)
    if production is None:
        gateway.deny()
    _, view, role = production
    headers = gateway.frappe.local.request.headers
    if (view.user in ("Administrator", "Guest") or role != "MGR"
            or headers.get("X-Clinic-Workspace") != "MGR"
            or not headers.get("X-Clinic-Session-Epoch")):
        gateway.deny()
    roles = gateway.fresh_user_roles(view.user)
    if not isinstance(roles, tuple) or "System Manager" not in roles:
        gateway.deny()
    if write and not gateway.csrf_token_valid(csrf):
        gateway.deny()


def _authority(gateway, write=False):
    frappe = gateway.frappe
    try:
        owner_doctor_profile.check_field_authority(frappe, write)
    except frappe.PermissionError:
        gateway.deny()
    if (not frappe.has_permission("Healthcare Practitioner", "read")
            or not frappe.has_permission("Medical Department", "read")
            or (write and not frappe.has_permission("Healthcare Practitioner", "create"))):
        gateway.deny()


def _can_create(frappe):
    if not frappe.has_permission("Healthcare Practitioner", "create"):
        return False
    try:
        owner_doctor_profile.check_field_authority(frappe, write=True)
    except frappe.PermissionError:
        return False
    return True


def _readable_names(frappe, doctype, filters=None):
    start = 0
    while True:
        rows = frappe.get_list(doctype, filters=filters, fields=["name"],
                               start=start, page_length=100, order_by="name asc")
        yield from rows
        if len(rows) < 100:
            return
        start += len(rows)


def lookup(gateway):
    _gate(gateway)
    _authority(gateway)
    frappe = gateway.frappe
    departments = []
    for row in _readable_names(frappe, "Medical Department"):
        doc = frappe.get_doc("Medical Department", row.name)
        if frappe.has_permission("Medical Department", "read", doc=doc):
            departments.append({"name": doc.name, "displayName": doc.department})
    practitioners = []
    for row in _readable_names(frappe, "Healthcare Practitioner", {"status": "Disabled"}):
        doc = frappe.get_doc("Healthcare Practitioner", row.name)
        if (frappe.has_permission("Healthcare Practitioner", "read", doc=doc)
                and owner_doctor_profile.eligible_practitioner(doc)):
            practitioners.append(owner_doctor_profile.present(doc))
    return {"departments": departments, "practitioners": practitioners,
            "canCreate": _can_create(frappe)}


def create(gateway, body, csrf):
    _gate(gateway, csrf, write=True)
    _authority(gateway, write=True)
    frappe = gateway.frappe
    try:
        first, last, department = owner_doctor_profile.validate_input(body)
    except ValueError:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        dept = frappe.get_doc("Medical Department", department)
        if not frappe.has_permission("Medical Department", "read", doc=dept):
            gateway.deny()
        display_name = f"{first} {last}" if last else first
        native_name = f"clinic-ui-draft-{uuid4()}"
        if frappe.db.exists("Healthcare Practitioner", native_name):
            return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
        doc = gateway.owner_doctor_create_draft({
            "doctype": "Healthcare Practitioner", "first_name": first, "last_name": last,
            "practitioner_name": display_name, "department": department,
            "status": "Disabled", "user_id": "", "show_in_portal": 0,
            "practitioner_schedules": [],
        }, native_name)
        saved = frappe.get_doc("Healthcare Practitioner", doc.name)
        saved.check_permission("read")
        if (saved.name != native_name or not owner_doctor_profile.eligible_practitioner(saved)
                or saved.practitioner_schedules or saved.first_name != first
                or (saved.last_name or "") != last or saved.department != department
                or saved.practitioner_name != display_name):
            raise ValueError("Native readback mismatch")
        return owner_doctor_profile.present(saved)
    except Exception as exc:
        gateway.rollback()
        gateway.strip_message_channels()
        if isinstance(exc, frappe.PermissionError):
            gateway.deny()
        return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
