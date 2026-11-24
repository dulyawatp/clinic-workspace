"""Read-only preparation of an explicitly selected native schedule."""

import hashlib
import hmac
import json
import math
import re
from decimal import Decimal

from clinic_ui.core import doctor_hours, owner_doctor_profile


_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.0+)?)?$")


def _time(value):
    if isinstance(value, str) and not _TIME.fullmatch(value):
        raise ValueError("Unsupported time")
    seconds = doctor_hours._seconds(value)
    if not seconds.is_finite() or seconds != seconds.to_integral_value() or not 0 <= seconds < 86400:
        raise ValueError("Unsupported time")
    hours, remainder = divmod(int(seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    return "%02d:%02d:%02d" % (hours, minutes, seconds)


def normalized_slots(schedule):
    """Preserve native order; reject unsupported shapes without draft authoring rules."""
    rows = schedule.time_slots
    if not isinstance(rows, (list, tuple)) or not 1 <= len(rows) <= 512:
        raise ValueError("Unsupported slot count")
    result = []
    for row in rows:
        start, end = _time(row.from_time), _time(row.to_time)
        if (row.day not in doctor_hours._DAYS or start >= end
                or isinstance(row.duration, bool)):
            raise ValueError("Unsupported slot")
        try:
            duration = float(row.duration)
            if not math.isfinite(duration) or duration <= 0 or Decimal(str(duration)) != Decimal(str(row.duration)):
                raise ValueError("Unsupported duration")
        except (TypeError, OverflowError, ArithmeticError) as exc:
            raise ValueError("Unsupported duration") from exc
        maximum = row.maximum_appointments
        if isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 0:
            raise ValueError("Unsupported capacity")
        result.append({"day": row.day, "from_time": start, "to_time": end,
                       "duration": duration, "maximum_appointments": maximum})
    return result


def digest_slots(schedule, service_unit, slots):
    canonical = json.dumps(
        {"schedule": schedule, "serviceUnit": service_unit, "slots": slots},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(canonical).hexdigest()


def prepare_review(frappe, practitioner, schedule, service_unit, company, lock=False,
                   practitioner_version=None, schedule_version=None, slot_digest=None):
    """Read and optionally lock the selected documents, returning a review envelope."""
    doctor_hours.check_field_authority(frappe)
    doctor_hours._check_fields(
        frappe, "Healthcare Practitioner",
        {key: owner_doctor_profile._PRACTITIONER[key] for key in ("first_name", "last_name", "show_in_portal")},
        "read",
    )
    doc = frappe.get_doc("Healthcare Practitioner", practitioner, for_update=lock)
    doc.check_permission("read")
    if (not owner_doctor_profile.eligible_practitioner(doc) or doc.practitioner_schedules
            or not isinstance(doc.first_name, str) or not doc.first_name.strip()
            or not isinstance(doc.last_name or "", str)):
        raise ValueError("Unsupported practitioner")
    if frappe.db.exists("Practitioner Availability", {
        "scope": doc.name, "status": "Active", "docstatus": 1,
    }):
        raise ValueError("Practitioner availability override")
    unit = frappe.get_doc("Healthcare Service Unit", service_unit, for_update=lock)
    unit.check_permission("read")
    if not doctor_hours.eligible_unit(unit, company):
        raise ValueError("Unsupported unit")
    source = frappe.get_doc("Practitioner Schedule", schedule, for_update=lock)
    source.check_permission("read")
    if source.disabled != 0 or source.allow_video_conferencing != 0:
        raise ValueError("Unsupported schedule")
    slots = normalized_slots(source)
    digest = digest_slots(source.name, unit.name, slots)
    if ((practitioner_version is not None and str(doc.modified) != practitioner_version)
            or (schedule_version is not None and str(source.modified) != schedule_version)
            or (slot_digest is not None and (not isinstance(slot_digest, str)
                or not hmac.compare_digest(digest, slot_digest)))):
        raise ValueError("Stale review")
    return {"mode": "reuse", "practitioner": doc.name, "practitionerVersion": str(doc.modified),
            "schedule": source.name, "scheduleVersion": str(source.modified), "serviceUnit": unit.name,
            "slots": slots, "firstName": doc.first_name, "lastName": doc.last_name or "",
            "slotDigest": digest}
