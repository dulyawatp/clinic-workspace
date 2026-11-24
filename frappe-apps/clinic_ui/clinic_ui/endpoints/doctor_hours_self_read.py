"""Metadata boundary for the scoped recurring-hours projection."""


def check_schedule_projection_metadata(frappe, *, include_slots: bool) -> None:
    """Validate projection fields without granting document access."""
    try:
        definitions = [
            ("Practitioner Schedule", {"disabled": ("Check", None)}, None),
        ]
        if include_slots:
            definitions[0][1]["time_slots"] = ("Table", "Healthcare Schedule Time Slot")
            definitions.append((
                "Healthcare Schedule Time Slot",
                {"day": ("Select", None), "from_time": ("Time", None),
                 "to_time": ("Time", None)},
                "Practitioner Schedule",
            ))
        for doctype, fields, parent in definitions:
            meta = frappe.get_meta(doctype)
            if parent and meta.istable != 1:
                raise ValueError("Expected child table")
            context = {"parenttype": parent} if parent else {}
            masks = meta.get_masked_fields(**context)
            if masks is None:
                raise ValueError("Missing field masks")
            masked = {item.fieldname for item in masks}
            for name, (fieldtype, options) in fields.items():
                field = meta.get_field(name)
                if (field is None or field.fieldtype != fieldtype
                        or (options is not None and field.options != options)
                        or field.mask not in (0, False)
                        or name in masked
                        or type(field.permlevel) is not int
                        or field.permlevel != 0):
                    raise ValueError("Field metadata unavailable")
    except Exception as exc:
        raise frappe.PermissionError("Field metadata unavailable") from exc


def read_schedule_ranges(frappe, schedule: str, format_time) -> list[dict]:
    """Read only recurring ranges for an already-authorized schedule link."""
    return read_many_schedule_ranges(frappe, [schedule], format_time)[schedule]


def read_many_schedule_ranges(frappe, schedules, format_time, *, max_slots=512):
    """Lock all exact parents before reading bounded recurring ranges."""
    try:
        schedules = list(schedules)
        if (type(max_slots) is not int or max_slots < 0
                or any(not isinstance(name, str) or not name for name in schedules)):
            raise ValueError("Invalid schedule scope")
        names = sorted(set(schedules))
        if not names:
            return {}
        check_schedule_projection_metadata(frappe, include_slots=False)
        enabled = {}
        for schedule in names:
            parent = frappe.db.get_value(
                "Practitioner Schedule", {"name": schedule}, ["name", "disabled"],
                as_dict=True, for_update=True,
            )
            if (not parent or parent["name"] != schedule
                    or parent["disabled"] not in (0, 1)):
                raise ValueError("Invalid schedule")
            enabled[schedule] = not parent["disabled"]
        result = {}
        remaining = max_slots
        for schedule in names:
            result[schedule] = []
            if not enabled[schedule]:
                continue
            check_schedule_projection_metadata(frappe, include_slots=True)
            rows = frappe.db.get_values(
                "Healthcare Schedule Time Slot",
                {"parent": schedule, "parenttype": "Practitioner Schedule",
                 "parentfield": "time_slots"},
                ["name", "parent", "parenttype", "parentfield", "idx",
                 "day", "from_time", "to_time"],
                order_by="idx asc", limit=remaining + 1, as_dict=True, for_update=True,
            )
            if len(rows) > remaining:
                raise ValueError("Too many schedule slots")
            remaining -= len(rows)
            row_names = set()
            for row in rows:
                name = row["name"]
                if (not isinstance(name, str) or not name or name in row_names
                        or row["parent"] != schedule
                        or row["parenttype"] != "Practitioner Schedule"
                        or row["parentfield"] != "time_slots"
                        or type(row["idx"]) is not int or row["idx"] < 1
                        or row["day"] not in ("Sunday", "Monday", "Tuesday", "Wednesday",
                                              "Thursday", "Friday", "Saturday")):
                    raise ValueError("Invalid schedule slot")
                row_names.add(name)
                result[schedule].append({
                    "day": row["day"], "from_time": format_time(row["from_time"]),
                    "to_time": format_time(row["to_time"]),
                })
        return result
    except Exception as exc:
        raise frappe.PermissionError("Schedule ranges unavailable") from exc
