"""Bounded practitioner-first Finish; enabled source schedules are unsupported.

Opt-in requires exact site and reviewed hooks, company, and one native DOC role:
clinic_ui_owner_doctor_finish_enabled, _site, _hooks_reviewed_site,
_company and _role (all prefixed clinic_ui_owner_doctor_finish).
"""

import hashlib
import hmac
import json
from decimal import Decimal

from clinic_ui.core import doctor_hours, owner_doctor_profile, owner_staff_contract
from clinic_ui.endpoints import owner_doctor_reuse, production_read


def _reuse_qualified(gateway):
    """Separate site attestation: installed practitioner and User Permission hooks
    do not commit early or write source schedules or reverse links."""
    prefix = "clinic_ui_owner_doctor_finish_reuse"
    return (gateway.setting(prefix + "_enabled") is True
            and gateway.setting(prefix + "_site") == gateway.site
            and gateway.setting(prefix + "_hooks_reviewed_site") == gateway.site)


def _gate(gateway, csrf=None, write=False, reuse=False, allow_unqualified=False):
    prefix = "clinic_ui_owner_doctor_finish"
    if (gateway.setting(prefix + "_enabled") is not True
            or gateway.setting(prefix + "_site") != gateway.site
            or gateway.setting(prefix + "_hooks_reviewed_site") != gateway.site):
        gateway.deny()
    if reuse and not allow_unqualified and not _reuse_qualified(gateway):
        gateway.deny()
    company = gateway.setting(prefix + "_company")
    role = gateway.setting(prefix + "_role")
    if any(not isinstance(v, str) or not v or v != v.strip() or len(v) > 140
           for v in (company, role)):
        gateway.deny()
    production = production_read.authorize_if_selected(gateway, owner_staff_contract.OWNER_ROLE_CODES)
    if production is None:
        gateway.deny()
    settings, view, workspace = production
    headers = gateway.frappe.local.request.headers
    fresh = gateway.fresh_user_roles(view.user)
    if (view.user in ("Administrator", "Guest") or workspace != "MGR"
            or headers.get("X-Clinic-Workspace") != "MGR"
            or not headers.get("X-Clinic-Session-Epoch")
            or not isinstance(fresh, tuple) or "System Manager" not in fresh
            or (write and not gateway.csrf_token_valid(csrf))):
        gateway.deny()
    role_map = settings.as_role_map()
    if (role not in role_map.get("DOC", ())
            or any(role in roles for code, roles in role_map.items() if code != "DOC")
            or role.casefold() in ("all", "guest", "desk user")
            or any(word in role.casefold() for word in ("admin", "manager", "system"))):
        gateway.deny()
    try:
        native_role = gateway.frappe.get_doc("Role", role)
        native_role.check_permission("read")
        if (not gateway._fields_readable(gateway.frappe.get_meta("Role"), ("disabled", "desk_access"))
                or native_role.disabled != 0 or native_role.desk_access != 1):
            gateway.deny()
        for doctype in ("DocPerm", "Custom DocPerm"):
            for target in ("User", "Role", "Role Profile", "User Permission", "DocPerm",
                           "Custom DocPerm", "Server Script", "System Settings", "Customize Form",
                           "Property Setter", "Custom Field", "Client Script", "Web Form"):
                for permission in ("write", "create", "delete", "share", "submit", "cancel", "amend"):
                    if gateway.frappe.db.exists(doctype, {"role": role, "parent": target, permission: 1}):
                        gateway.deny()
        if not gateway.owner_staff_create_authorized(view.user):
            gateway.deny()
        permissions = (("Healthcare Practitioner", "read"), ("Healthcare Practitioner", "write"),
                       ("Practitioner Schedule", "read"))
        if not reuse:
            permissions += (("Practitioner Schedule", "create"),)
        permissions += (("Healthcare Service Unit", "read"), ("Practitioner Availability", "read"),
                        ("User Permission", "read"), ("User Permission", "create"))
        for doctype, access in permissions:
            if not gateway.frappe.has_permission(doctype, access, user=view.user):
                gateway.deny()
        doctor_hours.check_field_authority(gateway.frappe, write=not reuse)
        if reuse:
            doctor_hours._check_fields(
                gateway.frappe, "Healthcare Practitioner",
                {"practitioner_schedules": doctor_hours._PRACTITIONER["practitioner_schedules"]}, "write",
            )
            doctor_hours._check_fields(
                gateway.frappe, "Practitioner Service Unit Schedule",
                doctor_hours._ATTACHMENT, "write", "Healthcare Practitioner",
            )
        owner_doctor_profile.check_field_authority(gateway.frappe)
        doctor_hours._check_fields(gateway.frappe, "Healthcare Practitioner",
                                   {"status": doctor_hours._PRACTITIONER["status"],
                                    "user_id": doctor_hours._PRACTITIONER["user_id"]}, "write")
        if not gateway._fields_readable(gateway.frappe.get_meta("User Permission"),
                                        ("user", "allow", "for_value", "apply_to_all_doctypes", "is_default")):
            gateway.deny()
        doctor_hours._check_fields(
            gateway.frappe, "User Permission",
            {"apply_to_all_doctypes": ("Check", None), "is_default": ("Check", None)}, "read",
        )
        availability = gateway.frappe.get_meta("Practitioner Availability")
        if not availability.is_submittable:
            gateway.deny()
        doctor_hours._check_fields(
            gateway.frappe, "Practitioner Availability",
            {"scope_type": ("Link", "DocType"), "scope": ("Dynamic Link", "scope_type"),
             "status": ("Select", None)}, "read",
        )
    except Exception:
        gateway.deny()
    return company, role, view.user


def _slots(schedule):
    result = []
    for row in schedule.time_slots:
        times = []
        for value in (row.from_time, row.to_time):
            seconds = doctor_hours._seconds(value)
            if seconds != seconds.to_integral_value() or seconds % 60 or not 0 <= seconds < 86400:
                raise ValueError("Unsupported time")
            minutes = int(seconds // 60)
            times.append("%02d:%02d" % divmod(minutes, 60))
        duration = float(row.duration)
        if Decimal(str(duration)) != Decimal(str(row.duration)):
            raise ValueError("Unsupported duration")
        result.append({"day": row.day, "from_time": times[0], "to_time": times[1],
                       "duration": duration, "maximum_appointments": row.maximum_appointments})
    doctor_hours.validate_slots(result)
    return result


def _slot_digest(schedule, unit, slots):
    canonical = json.dumps(
        {"schedule": schedule, "serviceUnit": unit, "slots": slots},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def _draft(frappe, name, company, lock=False):
    practitioner = frappe.get_doc("Healthcare Practitioner", name, for_update=lock)
    practitioner.check_permission("read")
    practitioner.check_permission("write")
    if (not owner_doctor_profile.eligible_practitioner(practitioner)
            or len(practitioner.practitioner_schedules) != 1
            or not practitioner.first_name or not practitioner.first_name.strip()
            or not isinstance(practitioner.last_name or "", str)):
        raise ValueError("Unsupported practitioner")
    # Snapshot check only: native lookup keys on scope, including future overrides.
    # Bypass list filtering so inaccessible overrides cannot be silently adopted.
    # Later authorized native availability changes remain independent booking rules.
    if frappe.db.exists("Practitioner Availability", {
        "scope": practitioner.name, "status": "Active", "docstatus": 1,
    }):
        raise ValueError("Practitioner availability override")
    row = practitioner.practitioner_schedules[0]
    unit = frappe.get_doc("Healthcare Service Unit", row.service_unit, for_update=lock)
    unit.check_permission("read")
    if not doctor_hours.eligible_unit(unit, company):
        raise ValueError("Unsupported unit")
    schedule = frappe.get_doc("Practitioner Schedule", row.schedule, for_update=lock)
    schedule.check_permission("read")
    if schedule.disabled != 1 or schedule.allow_video_conferencing != 0:
        raise ValueError("Unsupported schedule")
    return practitioner, schedule, unit, _slots(schedule)


def options(gateway, practitioner):
    company, _, _ = _gate(gateway)
    if not isinstance(practitioner, str) or not practitioner or len(practitioner) > 140:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        doc, schedule, unit, slots = _draft(gateway.frappe, practitioner, company)
        return {"practitioner": doc.name, "practitionerVersion": str(doc.modified),
                "schedule": schedule.name, "scheduleVersion": str(schedule.modified),
                "serviceUnit": unit.name, "slots": slots,
                "firstName": doc.first_name, "lastName": doc.last_name or "",
                "slotDigest": _slot_digest(schedule.name, unit.name, slots)}
    except gateway.frappe.PermissionError:
        gateway.deny()
    except Exception:
        return gateway.respond_write(422, {"code": "VALIDATION"})


def _valid_reuse_key(value):
    return isinstance(value, str) and bool(value) and len(value) <= 140


def reuse_options(gateway, practitioner):
    company, _, _ = _gate(gateway, reuse=True, allow_unqualified=True)
    if not _valid_reuse_key(practitioner):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    frappe = gateway.frappe
    try:
        doc = frappe.get_doc("Healthcare Practitioner", practitioner)
        doc.check_permission("read")
        doc.check_permission("write")
        result = {"practitioner": doc.name, "practitionerVersion": str(doc.modified),
                  "schedules": [], "serviceUnits": []}
        if (not _reuse_qualified(gateway)
                or not owner_doctor_profile.eligible_practitioner(doc) or doc.practitioner_schedules
                or not isinstance(doc.first_name, str) or not doc.first_name.strip()
                or not isinstance(doc.last_name or "", str)
                or frappe.db.exists("Practitioner Availability", {
                    "scope": doc.name, "status": "Active", "docstatus": 1,
                })):
            return result
        units = frappe.get_list("Healthcare Service Unit",
                                filters={"company": company, "is_group": 0, "allow_appointments": 1},
                                fields=["name"], limit_page_length=101, order_by="name asc")
        schedules = frappe.get_list("Practitioner Schedule",
                                    filters={"disabled": 0, "allow_video_conferencing": 0},
                                    fields=["name"], limit_page_length=101, order_by="name asc")
        if len(units) > 100 or len(schedules) > 100:
            return result
        for row in units:
            try:
                unit = frappe.get_doc("Healthcare Service Unit", row.name)
                unit.check_permission("read")
            except frappe.PermissionError:
                gateway.strip_message_channels()
                continue
            if doctor_hours.eligible_unit(unit, company):
                result["serviceUnits"].append({"name": unit.name})
        for row in schedules:
            try:
                source = frappe.get_doc("Practitioner Schedule", row.name)
                source.check_permission("read")
            except frappe.PermissionError:
                gateway.strip_message_channels()
                continue
            if source.disabled != 0 or source.allow_video_conferencing != 0:
                continue
            try:
                owner_doctor_reuse.normalized_slots(source)
            except (ValueError, TypeError, AttributeError, ArithmeticError):
                gateway.strip_message_channels()
                continue
            result["schedules"].append({"name": source.name})
        if not result["schedules"] or not result["serviceUnits"]:
            result["schedules"], result["serviceUnits"] = [], []
        return result
    except frappe.PermissionError:
        gateway.deny()
    except Exception:
        gateway.strip_message_channels()
        return gateway.respond_write(422, {"code": "VALIDATION"})


def reuse_review(gateway, practitioner, schedule, service_unit):
    company, _, _ = _gate(gateway, reuse=True)
    if not all(_valid_reuse_key(value) for value in (practitioner, schedule, service_unit)):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        result = owner_doctor_reuse.prepare_review(gateway.frappe, practitioner, schedule, service_unit, company)
        doc = gateway.frappe.get_doc("Healthcare Practitioner", practitioner)
        doc.check_permission("write")
        return result
    except gateway.frappe.PermissionError:
        gateway.deny()
    except Exception:
        return gateway.respond_write(422, {"code": "VALIDATION"})


def finish(gateway, body, csrf):
    reuse = isinstance(body, dict) and body.get("mode") == "reuse"
    if reuse:
        company, role, actor = _gate(gateway, csrf, write=True, reuse=True)
    else:
        company, role, actor = _gate(gateway, csrf, write=True)
    keys = {"practitioner", "practitionerVersion", "schedule", "scheduleVersion", "email", "slotDigest"}
    if reuse:
        keys |= {"mode", "serviceUnit"}
    if (not isinstance(body, dict) or set(body) != keys
            or any(not isinstance(body[k], str) or not body[k] or len(body[k]) > 140 for k in keys)
            or len(body["slotDigest"]) != 64
            or any(char not in "0123456789abcdef" for char in body["slotDigest"])):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    email = body["email"].strip().lower()
    if len(email) > 140 or email.count("@") != 1 or "," in email or not gateway.owner_doctor_finish_email_valid(email):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    frappe = gateway.frappe
    dispatched = False
    try:
        if not gateway.owner_doctor_finish_native_user_supported():
            raise frappe.PermissionError
        try:
            if reuse:
                try:
                    review = owner_doctor_reuse.prepare_review(
                        frappe, body["practitioner"], body["schedule"], body["serviceUnit"], company, lock=True,
                        practitioner_version=body["practitionerVersion"], schedule_version=body["scheduleVersion"],
                        slot_digest=body["slotDigest"],
                    )
                except frappe.PermissionError:
                    raise
                except frappe.DoesNotExistError as exc:
                    raise ValueError("Stale review") from exc
                except (TypeError, AttributeError, ArithmeticError) as exc:
                    raise ValueError("Unsupported review") from exc
                try:
                    doc = frappe.get_doc("Healthcare Practitioner", body["practitioner"], for_update=True)
                except frappe.PermissionError:
                    raise
                except frappe.DoesNotExistError as exc:
                    raise ValueError("Stale practitioner") from exc
                doc.check_permission("read")
                doc.check_permission("write")
                if (str(doc.modified) != body["practitionerVersion"]
                        or not owner_doctor_profile.eligible_practitioner(doc)
                        or doc.practitioner_schedules):
                    raise ValueError("Stale practitioner")
                slots = review["slots"]
                unit_name = body["serviceUnit"]
            else:
                doc, source, unit, slots = _draft(frappe, body["practitioner"], company, lock=True)
                unit_name = unit.name
        except (ValueError, OverflowError):
            gateway.rollback()
            return gateway.respond_write(409, {"code": "STALE_VERSION"})
        if not reuse and (str(doc.modified) != body["practitionerVersion"] or source.name != body["schedule"]
                or str(source.modified) != body["scheduleVersion"]
                or not hmac.compare_digest(body["slotDigest"], _slot_digest(source.name, unit.name, slots))):
            gateway.rollback()
            return gateway.respond_write(409, {"code": "STALE_VERSION"})
        if frappe.db.exists("User", email):
            gateway.rollback()
            return gateway.respond_write(409, {"code": "EMAIL_IN_USE"})
        if (frappe.db.exists("Notification Settings", email)
                or frappe.db.sql("select 1 from `tabSessions` where user = %s limit 1", (email,))
                or frappe.db.sql("select 1 from `__Auth` where doctype = 'User' and name = %s limit 1", (email,))
                or frappe.db.exists("Contact", {"email_id": email})
                or frappe.db.exists("Contact", {"user": email})
                or frappe.db.exists("Contact Email", {"email_id": email})):
            gateway.rollback()
            return gateway.respond_write(422, {"code": "VALIDATION"})
        for meta, fields, parent in (
            (frappe.get_meta("User"), ("email", "first_name", "last_name", "enabled", "user_type",
                                       "send_welcome_email", "roles"), None),
            (frappe.get_meta("Has Role"), ("role",), "User"),
        ):
            levels = meta.get_permlevel_access("write", parenttype=parent, user=actor)
            if any(meta.get_field(field) is None or meta.get_field(field).permlevel not in levels for field in fields):
                raise frappe.PermissionError
        user = frappe.get_doc({"doctype": "User", "email": email, "first_name": doc.first_name.strip(),
                               "last_name": (doc.last_name or "").strip(), "enabled": 1,
                               "user_type": "System User", "send_welcome_email": 0,
                               "roles": [{"role": role}]})
        user.check_permission("create")
        dispatched = True
        try:
            gateway.owner_doctor_finish_insert_user(user)
        except frappe.DuplicateEntryError as exc:
            gateway.rollback()
            gateway.strip_message_channels()
            if exc.args[:2] == ("User", email):
                return gateway.respond_write(409, {"code": "EMAIL_IN_USE"})
            return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
        if reuse:
            gateway.owner_doctor_finish_link(doc, email, body["schedule"], unit_name)
            published_name = body["schedule"]
        else:
            published = gateway.owner_doctor_finish_publish(doc, email, slots)
            published_name = published.name
        after = frappe.get_doc("Healthcare Practitioner", doc.name, for_update=True)
        after.check_permission("read")
        actual = frappe.get_doc("Practitioner Schedule", published_name, for_update=True)
        actual.check_permission("read")
        saved_user, roles = gateway.owner_role_readback(email)
        permissions = frappe.get_list("User Permission", filters={"user": email, "allow": "Healthcare Practitioner",
                                                                   "for_value": doc.name}, fields=["name"], page_length=2)
        if (saved_user.enabled != 1 or saved_user.user_type != "System User" or saved_user.email != email
                or set(roles) != {role} or after.user_id != email or after.status != "Active"
                or after.show_in_portal or len(after.practitioner_schedules) != 1
                or after.practitioner_schedules[0].schedule != published_name
                or after.practitioner_schedules[0].service_unit != unit_name
                or actual.disabled != 0 or actual.allow_video_conferencing != 0
                or (reuse and (actual.name != published_name or str(actual.modified) != body["scheduleVersion"]
                               or owner_doctor_reuse.normalized_slots(actual) != slots))
                or (not reuse and _slots(actual) != slots) or len(permissions) != 1):
            raise ValueError("Native readback mismatch")
        permission = frappe.get_doc("User Permission", permissions[0].name)
        permission.check_permission("read")
        if (permission.user != email or permission.allow != "Healthcare Practitioner"
                or permission.for_value != doc.name or permission.apply_to_all_doctypes != 1
                or permission.is_default != 0):
            raise ValueError("Native User Permission mismatch")
        gateway.commit()
        gateway.strip_message_channels()
        return {"practitioner": doc.name, "userId": email, "schedule": published_name,
                "serviceUnit": unit_name, "slots": slots, "enabled": True}
    except frappe.PermissionError:
        gateway.rollback()
        if dispatched:
            gateway.strip_message_channels()
            return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
        gateway.deny()
    except Exception:
        gateway.rollback()
        gateway.strip_message_channels()
        return gateway.respond_write(409 if dispatched else 422,
                                     {"code": "WRITE_UNCERTAIN" if dispatched else "VALIDATION"})
