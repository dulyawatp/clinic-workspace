"""Native draft doctor hours; caller owns authorization and transaction."""

import math
import re
from datetime import time, timedelta
from decimal import Decimal


_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
_FIELDS = {"day", "from_time", "to_time", "duration", "maximum_appointments"}
_TIME = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")

# Field type and table/link targets are part of the authority boundary. A site
# customization that changes their meaning must not silently widen this lane.
_PRACTITIONER = {
    "status": ("Select", None), "user_id": ("Link", "User"),
    "practitioner_schedules": ("Table", "Practitioner Service Unit Schedule"),
}
_UNIT = {
    "company": ("Link", "Company"), "is_group": ("Check", None),
    "allow_appointments": ("Check", None),
}
_SCHEDULE = {
    "schedule_name": ("Data", None), "disabled": ("Check", None),
    "allow_video_conferencing": ("Check", None),
    "time_slots": ("Table", "Healthcare Schedule Time Slot"),
}
_ATTACHMENT = {"schedule": ("Link", "Practitioner Schedule"),
               "service_unit": ("Link", "Healthcare Service Unit")}
_SLOT = {
    "day": ("Select", None), "from_time": ("Time", None), "to_time": ("Time", None),
    "duration": ("Float", None), "maximum_appointments": ("Int", None),
}


def _check_fields(frappe, doctype, fields, access, parenttype=None):
    """Check actual DocFields using native Meta and child parent context."""
    try:
        meta = frappe.get_meta(doctype)
        context = {"parenttype": parenttype} if parenttype else {}
        levels = set(meta.get_permlevel_access(access, user=frappe.session.user, **context))
        masks = meta.get_masked_fields(**context)
        if masks is None:
            raise ValueError("Missing field masks")
        masked = {getattr(field, "fieldname", None) for field in masks}
        for name, (fieldtype, options) in fields.items():
            field = meta.get_field(name)
            if (field is None or field.fieldtype != fieldtype
                    or (options is not None and field.options != options)
                    or field.permlevel not in levels or name in masked):
                raise ValueError("Field authority unavailable")
    except Exception as exc:
        raise frappe.PermissionError("Field authority unavailable") from exc


def check_field_authority(frappe, write=False):
    """Fail closed before querying or writing any scoped native fields."""
    for doctype, fields, parent in (
        ("Healthcare Practitioner", _PRACTITIONER, None),
        ("Healthcare Service Unit", _UNIT, None),
        ("Practitioner Schedule", _SCHEDULE, None),
        ("Practitioner Service Unit Schedule", _ATTACHMENT, "Healthcare Practitioner"),
        ("Healthcare Schedule Time Slot", _SLOT, "Practitioner Schedule"),
    ):
        _check_fields(frappe, doctype, fields, "read", parent)
    if write:
        for doctype, fields, parent in (
            ("Healthcare Practitioner", {"practitioner_schedules": _PRACTITIONER["practitioner_schedules"]}, None),
            ("Practitioner Schedule", _SCHEDULE, None),
            ("Practitioner Service Unit Schedule", _ATTACHMENT, "Healthcare Practitioner"),
            ("Healthcare Schedule Time Slot", _SLOT, "Practitioner Schedule"),
        ):
            _check_fields(frappe, doctype, fields, "write", parent)


def _seconds(value):
    if isinstance(value, timedelta):
        return Decimal(str(value.total_seconds()))
    if isinstance(value, time):
        return Decimal(value.hour * 3600 + value.minute * 60 + value.second) + Decimal(value.microsecond) / 1000000
    if isinstance(value, str):
        parts = value.split(":")
        if len(parts) in (2, 3):
            return Decimal(parts[0]) * 3600 + Decimal(parts[1]) * 60 + (Decimal(parts[2]) if len(parts) == 3 else 0)
    raise ValueError("Unexpected stored time")


def _assert_readback(frappe, practitioner_name, schedule_name, unit_name, slots):
    # get_doc reloads from the database; for_update avoids a stale snapshot.
    schedule = frappe.get_doc("Practitioner Schedule", schedule_name, for_update=True)
    practitioner = frappe.get_doc("Healthcare Practitioner", practitioner_name, for_update=True)
    schedule.check_permission("read")
    practitioner.check_permission("read")
    if (schedule.name != schedule_name or schedule.schedule_name != schedule_name
            or schedule.disabled != 1 or schedule.allow_video_conferencing != 0
            or practitioner.status != "Disabled" or practitioner.user_id
            or len(practitioner.practitioner_schedules) != 1
            or practitioner.practitioner_schedules[0].schedule != schedule_name
            or practitioner.practitioner_schedules[0].service_unit != unit_name
            or len(schedule.time_slots) != len(slots)):
        raise ValueError("Native readback mismatch")
    for actual, expected in zip(schedule.time_slots, slots):
        if (actual.day != expected["day"]
                or _seconds(actual.from_time) != _seconds(expected["from_time"])
                or _seconds(actual.to_time) != _seconds(expected["to_time"])
                or Decimal(str(actual.duration)) != Decimal(str(expected["duration"]))
                or actual.maximum_appointments != expected["maximum_appointments"]):
            raise ValueError("Native slot readback mismatch")
    return practitioner


def _time(value):
    match = _TIME.fullmatch(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("Invalid time")
    return int(match[1]) * 60 + int(match[2])


def validate_slots(slots):
    """Validate bounded, weekday-ordered, nonoverlapping native slot inputs."""
    if not isinstance(slots, list) or not 1 <= len(slots) <= 56:
        raise ValueError("Invalid slots")
    previous_day, previous_end = -1, 0
    for slot in slots:
        if not isinstance(slot, dict) or set(slot) != _FIELDS:
            raise ValueError("Invalid slot fields")
        if not isinstance(slot["day"], str) or slot["day"] not in _DAYS:
            raise ValueError("Invalid weekday")
        day = _DAYS.index(slot["day"])
        start, end = _time(slot["from_time"]), _time(slot["to_time"])
        duration, maximum = slot["duration"], slot["maximum_appointments"]
        if (
            day < previous_day or (day == previous_day and start < previous_end)
            or start >= end
            or isinstance(duration, bool) or not isinstance(duration, (int, float))
            or not math.isfinite(duration) or duration <= 0
            or Decimal(str(duration)).as_tuple().exponent < -9
            or isinstance(maximum, bool) or not isinstance(maximum, int)
            or maximum <= 0 or maximum * duration > end - start
        ):
            raise ValueError("Invalid slot interval or capacity")
        previous_day, previous_end = day, end


def eligible_practitioner(doc):
    return doc.status == "Disabled" and not doc.user_id and not doc.practitioner_schedules


def eligible_unit(doc, company):
    return not doc.is_group and bool(doc.allow_appointments) and doc.company == company


def stage_hours(gateway, practitioner_name, expected_modified, service_unit_name, company, slots):
    """Insert disabled schedule and attach it; exceptions must cause rollback."""
    frappe = gateway.frappe
    validate_slots(slots)
    check_field_authority(frappe, write=True)
    practitioner = frappe.get_doc("Healthcare Practitioner", practitioner_name, for_update=True)
    practitioner.check_permission("read")
    practitioner.check_permission("write")
    if str(practitioner.modified) != expected_modified or not eligible_practitioner(practitioner):
        raise StalePractitioner()
    unit = frappe.get_doc("Healthcare Service Unit", service_unit_name, for_update=True)
    unit.check_permission("read")
    if not eligible_unit(unit, company):
        raise IneligibleServiceUnit()
    if not frappe.has_permission("Practitioner Schedule", "create"):
        raise frappe.PermissionError
    schedule = gateway.owner_doctor_stage_schedule(practitioner, unit, slots)
    practitioner = _assert_readback(frappe, practitioner.name, schedule.name, unit.name, slots)
    return {"practitioner": practitioner.name, "version": str(practitioner.modified), "schedule": schedule.name}


class StalePractitioner(Exception):
    pass


class IneligibleServiceUnit(Exception):
    pass
