"""Permission-bound native recurring hours for the signed-in doctor."""
from datetime import time, timedelta
import re

from clinic_ui.core.doctor_hours import _check_fields as _check_native_fields
from clinic_ui.endpoints import production_read
from clinic_ui.endpoints import doctor_hours_self_read


def _check_fields(frappe):
    for doctype, fields, parent in (
        ("Healthcare Practitioner", {
            "user_id": ("Link", "User"), "status": ("Select", None),
            "practitioner_schedules": ("Table", "Practitioner Service Unit Schedule"),
        }, None),
    ):
        _check_native_fields(frappe, doctype, fields, "read", parent)


def _scoped_attachment_rows(gateway, practitioner_name, user):
    """Lock and validate only attachments belonging to an authorized practitioner."""
    frappe = gateway.frappe
    try:
        if (not isinstance(practitioner_name, str) or not practitioner_name
                or not isinstance(user, str) or not user or frappe.session.user != user):
            raise ValueError("Invalid practitioner scope")
        _check_fields(frappe)
        practitioner = frappe.get_lazy_doc(
            "Healthcare Practitioner", practitioner_name, for_update=True)
        practitioner.check_permission("read")
        if (practitioner.name != practitioner_name or practitioner.user_id != user
                or practitioner.status != "Active"):
            raise ValueError("Invalid practitioner")
        if frappe.get_meta("Practitioner Service Unit Schedule").istable != 1:
            raise ValueError("Invalid attachment table")
        _check_native_fields(frappe, "Practitioner Service Unit Schedule", {
            "schedule": ("Link", "Practitioner Schedule"),
        }, "read", "Healthcare Practitioner")
        fields = ["name", "parent", "parenttype", "parentfield", "idx", "schedule"]
        try:
            _check_native_fields(frappe, "Practitioner Service Unit Schedule", {
                "service_unit": ("Link", "Healthcare Service Unit"),
            }, "read", "Healthcare Practitioner")
            fields.append("service_unit")
        except Exception:
            gateway.strip_message_channels()
        rows = frappe.db.get_values(
            "Practitioner Service Unit Schedule",
            {"parent": practitioner_name, "parenttype": "Healthcare Practitioner",
             "parentfield": "practitioner_schedules"},
            fields, as_dict=True, for_update=True, limit=33, order_by="idx asc",
        )
        if len(rows) > 32:
            raise ValueError("Too many attachments")
        names = set()
        previous_idx = 0
        result = []
        for row in rows:
            name, idx, schedule = row["name"], row["idx"], row["schedule"]
            if (not isinstance(name, str) or not name or name in names
                    or row["parent"] != practitioner_name
                    or row["parenttype"] != "Healthcare Practitioner"
                    or row["parentfield"] != "practitioner_schedules"
                    or type(idx) is not int or idx < 1 or idx < previous_idx
                    or not isinstance(schedule, str) or not schedule):
                raise ValueError("Invalid attachment")
            names.add(name)
            previous_idx = idx
            result.append({field: row[field] for field in fields})
        return result
    except Exception as exc:
        raise frappe.PermissionError("Attachment rows unavailable") from exc


def _time(value):
    if isinstance(value, timedelta):
        if value.days != 0 or value.microseconds:
            raise ValueError("Invalid native time")
        hours, remainder = divmod(value.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if isinstance(value, time):
        if value.microsecond or value.tzinfo is not None:
            raise ValueError("Invalid native time")
        return value.strftime("%H:%M:%S")
    if isinstance(value, str) and re.fullmatch(
        r"(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9])?", value
    ):
        return value
    raise ValueError("Invalid native time")


def get(gateway):
    production = production_read.authorize_if_selected(gateway, ("DOC",))
    if production is None:
        gateway.deny()
    _, view, role = production
    headers = gateway.frappe.local.request.headers
    if (view.user in ("Guest", "Administrator") or role != "DOC"
            or headers.get("X-Clinic-Workspace") != "DOC"
            or not headers.get("X-Clinic-Session-Epoch")):
        gateway.deny()

    frappe = gateway.frappe
    try:
        _check_fields(frappe)
        scoped = (gateway.setting("clinic_ui_doctor_hours_self_read_enabled") is True
                  and gateway.setting("clinic_ui_doctor_hours_self_read_site") == gateway.site)
        if scoped:
            if (frappe.session.user != view.user
                    or frappe.db.get_value("User", view.user, "enabled") != 1):
                return {"state": "unavailable"}
            identities = frappe.db.get_values(
                "Healthcare Practitioner", {"user_id": view.user, "status": "Active"},
                ["name"], limit=2, as_dict=True, for_update=True,
            )
            if len(identities) != 1 or not isinstance(identities[0]["name"], str) or not identities[0]["name"]:
                return {"state": "unavailable"}
        rows = frappe.get_list(
            "Healthcare Practitioner", filters={"user_id": view.user, "status": "Active"},
            fields=["name"], limit_page_length=2,
        )
        if len(rows) != 1 or (scoped and rows[0].name != identities[0]["name"]):
            return {"state": "unavailable"}
        if scoped:
            attachments = _scoped_attachment_rows(gateway, rows[0].name, view.user)
            ranges = doctor_hours_self_read.read_many_schedule_ranges(
                frappe, [row["schedule"] for row in attachments], _time)
            if sum(len(ranges[row["schedule"]]) for row in attachments) > 512:
                return {"state": "unavailable"}
            schedules = []
            for attachment in attachments:
                slots = ranges[attachment["schedule"]]
                if not slots:
                    continue
                item = {"slots": slots}
                try:
                    if attachment.get("service_unit"):
                        unit = frappe.get_doc("Healthcare Service Unit", attachment["service_unit"])
                        unit.check_permission("read")
                        if unit.name == attachment["service_unit"]:
                            item["serviceUnit"] = unit.name
                except Exception:
                    gateway.strip_message_channels()
                schedules.append(item)
            return {"state": "ready", "schedules": schedules} if schedules else {"state": "empty"}
        practitioner = frappe.get_doc("Healthcare Practitioner", rows[0].name)
        practitioner.check_permission("read")
        if (practitioner.name != rows[0].name or practitioner.user_id != view.user
                or practitioner.status != "Active"):
            return {"state": "unavailable"}
        schedules = []
        for attachment in practitioner.practitioner_schedules:
            _check_native_fields(frappe, "Practitioner Service Unit Schedule", {
                "schedule": ("Link", "Practitioner Schedule"),
            }, "read", "Healthcare Practitioner")
            _check_native_fields(frappe, "Practitioner Schedule", {
                "disabled": ("Check", None),
            }, "read")
            if not attachment.schedule:
                return {"state": "unavailable"}
            schedule = frappe.get_doc("Practitioner Schedule", attachment.schedule)
            schedule.check_permission("read")
            if schedule.name != attachment.schedule or schedule.disabled not in (0, 1):
                return {"state": "unavailable"}
            if schedule.disabled:
                continue
            _check_native_fields(frappe, "Practitioner Schedule", {
                "time_slots": ("Table", "Healthcare Schedule Time Slot"),
            }, "read")
            _check_native_fields(frappe, "Healthcare Schedule Time Slot", {
                "day": ("Select", None), "from_time": ("Time", None),
                "to_time": ("Time", None),
            }, "read", "Practitioner Schedule")
            slots = []
            for slot in schedule.time_slots:
                if slot.day not in ("Sunday", "Monday", "Tuesday", "Wednesday",
                                     "Thursday", "Friday", "Saturday"):
                    return {"state": "unavailable"}
                slots.append({"day": slot.day, "from_time": _time(slot.from_time),
                              "to_time": _time(slot.to_time)})
            if not slots:
                continue
            item = {"slots": slots}
            try:
                _check_native_fields(frappe, "Practitioner Service Unit Schedule", {
                    "service_unit": ("Link", "Healthcare Service Unit"),
                }, "read", "Healthcare Practitioner")
                if attachment.service_unit:
                    unit = frappe.get_doc("Healthcare Service Unit", attachment.service_unit)
                    unit.check_permission("read")
                    if unit.name == attachment.service_unit:
                        item["serviceUnit"] = unit.name
            except Exception:
                gateway.strip_message_channels()
            schedules.append(item)
        return {"state": "ready", "schedules": schedules} if schedules else {"state": "empty"}
    except Exception:
        gateway.strip_message_channels()
        return {"state": "unavailable"}
