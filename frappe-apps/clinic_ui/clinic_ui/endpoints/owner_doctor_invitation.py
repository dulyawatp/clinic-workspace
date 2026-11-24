"""Separate, opt-in native doctor invitation.

Enable only after reviewing hooks, mail configuration and Communication retention:
clinic_ui_owner_doctor_invitation_enabled, _site, _hooks_reviewed_site, _role.
The latter three settings use the same prefix. Retain the marker permanently.
"""

import hashlib

from clinic_ui.core import owner_staff_contract
from clinic_ui.endpoints import production_read


_PURPOSE = "Clinic UI doctor invitation"


def _gate(gateway, csrf=None, write=False):
    prefix = "clinic_ui_owner_doctor_invitation"
    if (gateway.setting(prefix + "_enabled") is not True
            or gateway.setting(prefix + "_site") != gateway.site
            or gateway.setting(prefix + "_hooks_reviewed_site") != gateway.site):
        gateway.deny()
    role = gateway.setting(prefix + "_role")
    production = production_read.authorize_if_selected(gateway, owner_staff_contract.OWNER_ROLE_CODES)
    if production is None or not isinstance(role, str) or not role or role != role.strip():
        gateway.deny()
    settings, view, workspace = production
    headers = gateway.frappe.local.request.headers
    role_map = settings.as_role_map()
    fresh = gateway.fresh_user_roles(view.user)
    if (view.user in ("Guest", "Administrator") or workspace != "MGR"
            or headers.get("X-Clinic-Workspace") != "MGR"
            or not headers.get("X-Clinic-Session-Epoch")
            or not isinstance(fresh, tuple) or "System Manager" not in fresh
            or role not in role_map.get("DOC", ())
            or any(role in roles for code, roles in role_map.items() if code != "DOC")
            or (write and not gateway.csrf_token_valid(csrf))):
        gateway.deny()
    return role


def _valid(value):
    return isinstance(value, str) and bool(value) and value == value.strip() and len(value) <= 140


def _target(gateway, practitioner, user_id, role, lock=False):
    frappe = gateway.frappe
    for doctype, fields, parent in (
        ("User", ("email", "enabled", "user_type", "roles"), None),
        ("Healthcare Practitioner", ("user_id", "status"), None),
        ("Has Role", ("role",), "User"),
    ):
        if not gateway._fields_readable(frappe.get_meta(doctype), fields, parenttype=parent):
            raise ValueError("Fields unavailable")
    user = frappe.get_doc("User", user_id, for_update=lock)
    user.check_permission("read")
    user.check_permission("write")
    doctor = frappe.get_doc("Healthcare Practitioner", practitioner, for_update=lock)
    doctor.check_permission("read")
    roles = {row.role for row in user.roles}
    if (user.name != user_id or user.email != user_id or user.enabled != 1
            or user.user_type != "System User" or roles != {role}
            or doctor.user_id != user_id or doctor.status != "Active"
            or frappe.db.count("Healthcare Practitioner", {"user_id": user_id}) != 1):
        raise ValueError("Unsupported target")
    return user


def _marker_name(user_id):
    return "clinic-doctor-invite-" + hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def _lookup(frappe, user, lock=False):
    marker_name = _marker_name(user.name)
    if not frappe.db.exists("Communication", marker_name):
        return {"status": "not_queued"}
    marker = frappe.get_doc("Communication", marker_name, for_update=lock)
    marker.check_permission("read")
    if (marker.reference_doctype != "User" or marker.reference_name != user.name
            or marker.subject != _PURPOSE or marker.content):
        return {"status": "unknown"}
    rows = frappe.get_all("Email Queue", filters={"communication": marker_name},
                          fields=["name"], limit_page_length=2)
    if len(rows) != 1:
        return {"status": "unknown"}
    queue = frappe.get_doc("Email Queue", rows[0].name, for_update=lock)
    if (queue.communication != marker_name or queue.reference_doctype != "User"
            or queue.reference_name != user.name or len(queue.recipients) != 1
            or queue.recipients[0].recipient != user.email):
        return {"status": "unknown"}
    state = {"Not Sent": "queued", "Sending": "queued", "Sent": "sent",
             "Error": "failed"}.get(queue.status, "unknown")
    return {"status": state, "queueId": queue.name}


def status(gateway, practitioner, user_id):
    role = _gate(gateway)
    if (practitioner is not None and not _valid(practitioner)) or not _valid(user_id):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        gateway.owner_doctor_invitation_native()
        if practitioner is None:
            rows = gateway.frappe.get_list(
                "Healthcare Practitioner", filters={"user_id": user_id, "status": "Active"},
                fields=["name"], limit_page_length=2)
            if len(rows) != 1:
                return {"status": "unavailable"}
            practitioner = rows[0].name
            try:
                user = _target(gateway, practitioner, user_id, role)
            except Exception as error:
                unavailable = (ValueError, gateway.frappe.PermissionError,
                               gateway.frappe.DoesNotExistError)
                if isinstance(error, unavailable):
                    gateway.strip_message_channels()
                    return {"status": "unavailable"}
                raise
            result = _lookup(gateway.frappe, user)
            return {**result, "practitioner": practitioner}
        return _lookup(gateway.frappe, _target(gateway, practitioner, user_id, role))
    except Exception:
        gateway.strip_message_channels()
        return {"status": "unknown"}


def send(gateway, body, csrf):
    role = _gate(gateway, csrf, write=True)
    if (not isinstance(body, dict) or set(body) not in
            ({"practitioner", "userId"}, {"practitioner", "userId", "previousQueueId"})
            or any(not _valid(value) for value in body.values())):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    frappe = gateway.frappe
    try:
        gateway.owner_doctor_invitation_native()
        user = _target(gateway, body["practitioner"], body["userId"], role, lock=True)
        current = _lookup(frappe, user, lock=True)
        if "previousQueueId" in body:
            if current.get("queueId") != body["previousQueueId"] or current["status"] != "failed":
                gateway.rollback()
                return current
            # Retry the same locked native message without generating another link.
            gateway.owner_doctor_invitation_retry(current["queueId"])
            result = _lookup(frappe, user, lock=True)
            if result != {"status": "queued", "queueId": current["queueId"]}:
                raise ValueError("Native queue retry not confirmed")
            gateway.commit()
            gateway.strip_message_channels()
            return result
        if current["status"] != "not_queued":
            gateway.rollback()
            return current
        meta = frappe.get_meta("User")
        levels = meta.get_permlevel_access("write", user=frappe.session.user)
        if any(meta.get_field(field) is None or meta.get_field(field).permlevel not in levels
               for field in ("reset_password_key", "last_reset_password_key_generated_on")):
            raise ValueError("Fields unavailable")
        marker = frappe.get_doc({"doctype": "Communication", "subject": _PURPOSE,
                                 "communication_type": "Automated Message",
                                 "communication_medium": "Email", "sent_or_received": "Sent",
                                 "reference_doctype": "User", "reference_name": user.name})
        marker.check_permission("create")
        queue = gateway.owner_doctor_invitation_enqueue(marker, _marker_name(user.name), user)
        if not queue or not queue.name:
            raise ValueError("Native queue unavailable")
        result = _lookup(frappe, user, lock=True)
        if result != {"status": "queued", "queueId": queue.name}:
            raise ValueError("Native queue correlation failed")
        gateway.commit()
        gateway.strip_message_channels()
        return result
    except Exception:
        gateway.rollback()
        gateway.strip_message_channels()
        return {"status": "unknown"}
