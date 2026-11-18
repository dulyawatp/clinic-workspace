"""Closed-world contract for the pinned Patient Appointment write path.

The installed healthcare v16.6.1 controller is the executable boundary. This
module pins the reviewed app versions, controller sources, hooks, overrides,
server-script absence and effective field metadata. Any unreviewed drift makes
``boundary_problem`` non-``None`` so every appointment write and action flag
fails closed.

Field classification is exhaustive: every effective Patient Appointment
DocField is one of ``IDENTITY`` (server-owned booking fields), ``DESTINATION``
(the only reschedule inputs), ``DERIVED`` (reviewed controller output),
``DEFAULT_INERT`` (reviewed zero defaults), ``EMPTY`` (must stay unset) or
``LAYOUT`` (no stored value). An unreviewed populated field, link, custom field
or property-setter change is refused rather than denylisted.

The metadata tuples are
``(fieldtype, options, fetch_from, fetch_if_empty, default, permlevel, mask,
reqd, read_only, hidden, set_only_once)``.
"""

from __future__ import annotations

import copy
import re
from datetime import date, datetime, time, timedelta

IDENTITY = "IDENTITY"
DESTINATION = "DESTINATION"
DERIVED = "DERIVED"
DEFAULT_INERT = "DEFAULT_INERT"
EMPTY = "EMPTY"
LAYOUT = "LAYOUT"

APPOINTMENT_DOCTYPE = "Patient Appointment"
APPOINTMENT_CLASS_PATH = (
    "healthcare.healthcare.doctype.patient_appointment.patient_appointment."
    "PatientAppointment"
)
CHECKIN_MODULE = (
    "healthcare.healthcare.doctype.patient_appointment.patient_appointment"
)
CHECKIN_NAME = "check_in_appointment"
PINNED_NAMING_SERIES = "HLC-APP-.YYYY.-"
RESCHEDULE_STATUSES = ("Scheduled", "Open", "Confirmed")
CHECKIN_STATUSES = ("Open", "Confirmed")

# The cancel transition (design ruling 4). The installed controller is
# healthcare v16.6.1; ``cancel_appointment(appointment_id)`` is the reviewed
# module-level entry point at patient_appointment.py:652 that handles the
# linked records (insurance coverage, Service Request, Sales Invoice, fee
# validity, calendar Event). It does not set the status itself: the installed
# ``update_status`` wrapper sets ``Cancelled`` and then calls it. This contract
# drives the pinned ``cancel_appointment`` side-effect handler and writes the
# terminal status through the controller's own ``save()`` path, whose
# ``set_status`` preserves terminal values (patient_appointment.py:133-150).
# Name, module and source sha are pinned in the boundary snapshot below.
CANCEL_KIND = "appointment_cancel"
NO_SHOW_KIND = "appointment_no_show"
CANCEL_MODULE = CHECKIN_MODULE
CANCEL_NAME = "cancel_appointment"
CANCELLED_STATUS = "Cancelled"
NO_SHOW_STATUS = "No Show"
# Terminal statuses exactly as the installed ``set_status`` treats them
# (patient_appointment.py:137): the controller early-returns for these.
TERMINAL_STATUSES = (
    "Checked In",
    "Checked Out",
    "Closed",
    "Cancelled",
    "No Show",
)
# SPEC:118-122 ("any non-terminal appointment") plus the SIT cancel target
# (Checked In -> Cancelled). A cancel never matches a status badge: the status
# is written only by the designed transition and read back as a post-condition.
CANCELLABLE_STATUSES = ("Scheduled", "Open", "Confirmed", "Checked In")
# Manual no-show (CSV:58) applies to a booked, not-yet-seen appointment.
NO_SHOW_STATUSES = ("Scheduled", "Open", "Confirmed")
# Branches of ``cancel_appointment`` whose outcome this contract does not
# deep-verify. A target that populates any of them is refused PRE-fence (the
# linked-invoice ruling: refuse when linked-invoice handling fails closed),
# leaving only the fee-validity and calendar branches, which are attested from
# the returned state.
CANCEL_REFUSED_LINKS = (
    "invoiced",
    "ref_sales_invoice",
    "paid_amount",
    "insurance_coverage",
    "service_request",
    "mode_of_payment",
)
NOTE_LABEL_CANCEL = CANCEL_KIND
NOTE_LABEL_NO_SHOW = NO_SHOW_KIND
MAX_REASON_LENGTH = 200
MAX_NOTES_LENGTH = 1000
_NOTE_LABELS = {CANCEL_KIND: NOTE_LABEL_CANCEL, NO_SHOW_KIND: NOTE_LABEL_NO_SHOW}

# Patient fields read to verify the controller's fetch_from values.
PATIENT_SOURCE_FIELDS = (
    "patient_name",
    "sex",
    "inpatient_record",
    "emergency_record",
)
# Appointment Type fields that decide the booking mode and duration.
APPOINTMENT_TYPE_SOURCE_FIELDS = (
    "appointment_type",
    "default_duration",
    "allow_booking_for",
)

# Behavior-changing DocType metadata that a client-side field list cannot see.
APPOINTMENT_DOCTYPE_BEHAVIOR = {
    "autoname": "naming_series:",
    "naming_rule": 'By "Naming Series" field',
    "is_virtual": 0,
    "issingle": 0,
    "is_submittable": 0,
    "track_changes": 1,
    "allow_rename": 0,
    "allow_copy": 0,
    "is_tree": 0,
    "editable_grid": 0,
    "quick_entry": 0,
    "allow_import": 1,
    "allow_auto_repeat": 0,
    "image_field": "",
    "title_field": "title",
    "search_fields": (
        "patient, practitioner, department, appointment_date, appointment_time"
    ),
    "default_print_format": "",
    "is_calendar_and_gantt": 0,
    "max_attachments": 0,
}

# Every wildcard doc_event that can run during insert/save or field change.
# Pinning all of them prevents a newly added global hook from escaping review.
WILDCARD_TRIGGER_EVENTS = (
    "before_insert",
    "before_validate",
    "validate",
    "before_save",
    "after_insert",
    "on_update",
    "on_change",
)

APPOINTMENT_FIELD_METADATA = {
    "inpatient_record": ('Link', 'Inpatient Record', 'patient.inpatient_record', 0, '', 0, 0, 0, 1, 0, 0),
    "patient": ('Link', 'Patient', '', 0, '', 0, 0, 1, 0, 0, 1),
    "appointment_type": ('Link', 'Appointment Type', '', 0, '', 0, 0, 1, 0, 0, 1),
    "duration": ('Int', '', 'appointment_type.default_duration', 0, '', 0, 0, 0, 0, 0, 1),
    "column_break_1": ('Column Break', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "status": ('Select', '\nScheduled\nOpen\nConfirmed\nChecked In\nChecked Out\nClosed\nCancelled\nNo Show', '', 0, '', 0, 0, 0, 1, 0, 0),
    "procedure_template": ('Link', 'Clinical Procedure Template', '', 0, '', 0, 0, 0, 0, 1, 1),
    "get_procedure_from_encounter": ('Button', '', '', 0, '', 0, 0, 0, 0, 1, 0),
    "procedure_prescription": ('Link', 'Procedure Prescription', '', 0, '', 0, 0, 0, 1, 1, 0),
    "service_unit": ('Link', 'Healthcare Service Unit', '', 0, '', 0, 0, 0, 1, 0, 0),
    "section_break_12": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "practitioner": ('Link', 'Healthcare Practitioner', '', 0, '', 0, 0, 0, 0, 0, 0),
    "department": ('Link', 'Medical Department', 'practitioner.department', 1, '', 0, 0, 0, 0, 0, 1),
    "column_break_17": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "appointment_date": ('Date', '', '', 0, '', 0, 0, 1, 1, 0, 0),
    "appointment_time": ('Time', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "section_break_16": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "patient_name": ('Data', '', 'patient.patient_name', 0, '', 0, 0, 0, 1, 0, 0),
    "patient_sex": ('Link', 'Gender', 'patient.sex', 0, '', 0, 0, 0, 1, 0, 0),
    "patient_age": ('Data', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "appointment_datetime": ('Datetime', '', '', 0, '', 0, 0, 0, 1, 1, 0),
    "mode_of_payment": ('Link', 'Mode of Payment', '', 0, '', 0, 0, 0, 0, 0, 0),
    "paid_amount": ('Currency', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "column_break_2": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "invoiced": ('Check', '', '', 0, '0', 0, 0, 0, 1, 0, 0),
    "company": ('Link', 'Company', '', 0, '', 0, 0, 1, 0, 0, 1),
    "section_break_3": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "notes": ('Small Text', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "referring_practitioner": ('Link', 'Healthcare Practitioner', '', 0, '', 0, 0, 0, 0, 0, 0),
    "reminded": ('Check', '', '', 0, '0', 0, 0, 0, 0, 1, 0),
    "therapy_type": ('Link', 'Therapy Type', '', 0, '', 0, 0, 0, 0, 1, 1),
    "get_prescribed_therapies": ('Button', '', '', 0, '', 0, 0, 0, 0, 1, 0),
    "therapy_plan": ('Link', 'Therapy Plan', '', 0, '', 0, 0, 0, 0, 1, 1),
    "ref_sales_invoice": ('Link', 'Sales Invoice', '', 0, '', 0, 0, 0, 1, 0, 0),
    "naming_series": ('Select', 'HLC-APP-.YYYY.-', '', 0, '', 0, 0, 0, 0, 0, 1),
    "billing_item": ('Link', 'Item', '', 0, '', 0, 0, 0, 1, 0, 0),
    "title": ('Data', '', '', 0, '', 0, 0, 0, 1, 1, 0),
    "practitioner_name": ('Data', '', 'practitioner.practitioner_name', 0, '', 0, 0, 0, 1, 0, 0),
    "source": ('Link', 'UTM Source', '', 0, '', 0, 0, 0, 0, 0, 0),
    "insurance_section": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "column_break_49": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "insurance_payor": ('Link', 'Insurance Payor', 'insurance_policy.insurance_payor', 0, '', 0, 0, 0, 1, 0, 0),
    "insurance_policy": ('Link', 'Patient Insurance Policy', '', 0, '', 0, 0, 0, 0, 0, 0),
    "insurance_coverage": ('Link', 'Patient Insurance Coverage', '', 0, '', 0, 0, 0, 1, 0, 0),
    "coverage_status": ('Data', '', 'insurance_coverage.status', 1, '', 0, 0, 0, 1, 0, 0),
    "google_meet_link": ('Data', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "add_video_conferencing": ('Check', '', '', 0, '0', 0, 0, 0, 0, 0, 0),
    "event": ('Link', 'Event', '', 0, '', 0, 0, 0, 1, 0, 0),
    "appointment_based_on_check_in": ('Check', '', '', 0, '0', 0, 0, 0, 1, 0, 0),
    "position_in_queue": ('Int', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "appointment_for": ('Select', '\nPractitioner\nDepartment\nService Unit', 'appointment_type.allow_booking_for', 0, '', 0, 0, 1, 1, 0, 1),
    "section_patient_details": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "column_break_4pp7": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "service_request": ('Link', 'Service Request', '', 0, '', 0, 0, 0, 1, 0, 0),
    "reference_doctype": ('Link', 'DocType', '', 0, '', 0, 0, 0, 1, 0, 0),
    "reference_docname": ('Dynamic Link', 'reference_doctype', '', 0, '', 0, 0, 0, 1, 0, 0),
    "column_break_wicc": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "appointment_notes_section": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "source_section": ('Section Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "column_break_xmkt": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "column_break_gdcb": ('Column Break', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "template_dt": ('Link', 'DocType', '', 0, '', 0, 0, 0, 1, 0, 0),
    "template_dn": ('Dynamic Link', 'template_dt', '', 0, '', 0, 0, 0, 1, 0, 0),
    "get_orders": ('Button', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "appointment_end_datetime": ('Datetime', '', '', 0, '', 0, 0, 0, 1, 1, 0),
    "emergency_record": ('Link', 'Emergency Record', 'patient.emergency_record', 0, '', 0, 0, 0, 1, 0, 0),
}

PATIENT_FIELD_METADATA = {
    "inpatient_record": ('Link', 'Inpatient Record', '', 0, '', 0, 0, 0, 1, 0, 0),
    "emergency_record": ('Link', 'Emergency Record', '', 0, '', 0, 0, 0, 1, 0, 0),
    "patient_name": ('Data', '', '', 0, '', 0, 0, 0, 1, 0, 0),
    "sex": ('Link', 'Gender', '', 0, '', 0, 0, 1, 0, 0, 0),
}

APPOINTMENT_TYPE_FIELD_METADATA = {
    "appointment_type": ('Data', '', '', 0, '', 0, 0, 1, 0, 0, 0),
    "default_duration": ('Int', '', '', 0, '', 0, 0, 0, 0, 0, 0),
    "allow_booking_for": ('Select', 'Practitioner\nDepartment\nService Unit', '', 0, 'Practitioner', 0, 0, 1, 0, 0, 0),
}

APPOINTMENT_FIELD_CLASSES = {
    "inpatient_record": EMPTY,
    "patient": IDENTITY,
    "appointment_type": IDENTITY,
    "duration": IDENTITY,
    "column_break_1": LAYOUT,
    "status": DERIVED,
    "procedure_template": EMPTY,
    "get_procedure_from_encounter": LAYOUT,
    "procedure_prescription": EMPTY,
    "service_unit": EMPTY,
    "section_break_12": LAYOUT,
    "practitioner": EMPTY,
    "department": IDENTITY,
    "column_break_17": LAYOUT,
    "appointment_date": DESTINATION,
    "appointment_time": DESTINATION,
    "section_break_16": LAYOUT,
    "patient_name": DERIVED,
    "patient_sex": DERIVED,
    "patient_age": DERIVED,
    "appointment_datetime": DERIVED,
    "mode_of_payment": EMPTY,
    "paid_amount": EMPTY,
    "column_break_2": LAYOUT,
    "invoiced": DEFAULT_INERT,
    "company": IDENTITY,
    "section_break_3": LAYOUT,
    "notes": EMPTY,
    "referring_practitioner": EMPTY,
    "reminded": DEFAULT_INERT,
    "therapy_type": EMPTY,
    "get_prescribed_therapies": EMPTY,
    "therapy_plan": EMPTY,
    "ref_sales_invoice": EMPTY,
    "naming_series": DERIVED,
    "billing_item": EMPTY,
    "title": DERIVED,
    "practitioner_name": EMPTY,
    "source": EMPTY,
    "insurance_section": LAYOUT,
    "column_break_49": LAYOUT,
    "insurance_payor": EMPTY,
    "insurance_policy": EMPTY,
    "insurance_coverage": EMPTY,
    "coverage_status": EMPTY,
    "google_meet_link": EMPTY,
    "add_video_conferencing": DEFAULT_INERT,
    "event": EMPTY,
    "appointment_based_on_check_in": DERIVED,
    "position_in_queue": DERIVED,
    "appointment_for": IDENTITY,
    "section_patient_details": LAYOUT,
    "column_break_4pp7": LAYOUT,
    "service_request": EMPTY,
    "reference_doctype": EMPTY,
    "reference_docname": EMPTY,
    "column_break_wicc": LAYOUT,
    "appointment_notes_section": LAYOUT,
    "source_section": LAYOUT,
    "column_break_xmkt": LAYOUT,
    "column_break_gdcb": LAYOUT,
    "template_dt": EMPTY,
    "template_dn": EMPTY,
    "get_orders": LAYOUT,
    "appointment_end_datetime": DERIVED,
    "emergency_record": EMPTY,
}

IGNORED_STANDARD_FIELDS = frozenset({
    "name",
    "owner",
    "creation",
    "modified",
    "modified_by",
    "docstatus",
    "idx",
    "parent",
    "parentfield",
    "parenttype",
    "_user_tags",
    "_comments",
    "_assign",
    "_liked_by",
})

_EMPTY_VALUE = (None, "", 0, False)

# Reviewed executable boundary. The wildcard hooks run on every save or field
# change; the healthcare scheduler entries and controller source hashes pin the
# reviewed v16.6.1 behavior. The site currently has no Server Script rows and no
# enabled Milestone Tracker for Patient Appointment, so the wildcard on_change
# evaluator runs with an empty tracker set.
_REVIEWED_BOUNDARY = {
    "versions": {"frappe": "16.54.0", "erpnext": "16.52.1", "healthcare": "16.6.1"},
    "source_sha256": {
        "patient_appointment": "3006502909c92ed326ae24bf62bef032a7175e3a1b4c68178e94c5153a391767",
        "fee_validity": "7655a389d4813dbcaac6257d44d439238c5e4b90f6f0f8b62a69d12db48b885b",
    },
    "controller_mro": (
        "healthcare.healthcare.doctype.patient_appointment.patient_appointment.PatientAppointment",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
    "extend_doctype_class": (),
    "appointment_behavior": APPOINTMENT_DOCTYPE_BEHAVIOR,
    "enabled_notifications": 0,
    "enabled_webhooks": 0,
    "active_workflows": 0,
    "active_assignment_rules": 0,
    "enabled_automations": 0,
    "enabled_milestone_trackers": 0,
    "patient_appointment_class": CHECKIN_MODULE,
    "checkin_module": CHECKIN_MODULE,
    "checkin_name": CHECKIN_NAME,
    "cancel_module": CANCEL_MODULE,
    "cancel_name": CANCEL_NAME,
    "cancel_whitelisted_override": (),
    "doc_events_patient_appointment": (),
    "wildcard_before_insert": (),
    "wildcard_before_validate": (),
    "wildcard_before_save": (),
    "wildcard_after_insert": (),
    "wildcard_on_update": (
        "frappe.desk.notifications.clear_doctype_notifications",
        "frappe.workflow.doctype.workflow_action.workflow_action.process_workflow_actions",
        "frappe.core.doctype.file.utils.attach_files_to_document",
        "frappe.automation.doctype.assignment_rule.assignment_rule.apply",
        "frappe.automation.doctype.assignment_rule.assignment_rule.update_due_date",
        "frappe.core.doctype.user_type.user_type.apply_permissions_for_non_standard_user_type",
        "frappe.core.doctype.permission_log.permission_log.make_perm_log",
        "frappe.search.sqlite_search.update_doc_index",
    ),
    "wildcard_validate": (
        "erpnext.support.doctype.service_level_agreement.service_level_agreement.apply",
        "erpnext.setup.doctype.transaction_deletion_record.transaction_deletion_record.check_for_running_deletion_job",
    ),
    "wildcard_on_change": (
        "frappe.automation.doctype.milestone_tracker.milestone_tracker."
        "evaluate_milestone",
    ),
    "override_doctype_class": (),
    "checkin_whitelisted_override": (),
    "scheduler_all": (
        "frappe.email.queue.flush",
        "frappe.email.queue.retry_sending_emails",
        "frappe.monitor.flush",
        "frappe.integrations.doctype.google_calendar.google_calendar.sync",
        "healthcare.healthcare.doctype.patient_appointment.patient_appointment.send_appointment_reminder",
    ),
    "scheduler_daily": (
        "frappe.desk.doctype.event.event.send_event_digest",
        "frappe.email.doctype.notification.notification.trigger_daily_alerts",
        "frappe.desk.form.document_follow.send_daily_updates",
        "healthcare.healthcare.doctype.patient_appointment.patient_appointment.update_appointment_status",
        "healthcare.healthcare.doctype.fee_validity.fee_validity.update_validity_status",
        "healthcare.healthcare.doctype.inpatient_record.inpatient_record.add_occupied_service_unit_in_ip_to_billables",
    ),
    "enabled_server_scripts": 0,
    "appointment_metadata": APPOINTMENT_FIELD_METADATA,
    "patient_metadata": PATIENT_FIELD_METADATA,
    "appointment_type_metadata": APPOINTMENT_TYPE_FIELD_METADATA,
}


def reviewed_boundary_snapshot():
    """A fresh copy of the reviewed boundary for runtime comparison and tests."""

    return copy.deepcopy(_REVIEWED_BOUNDARY)


def _value(source, field):
    if source is None:
        return None
    if hasattr(source, "get"):
        return source.get(field)
    return getattr(source, field, None)


# Frappe reads a Date column back as ``datetime.date``, a Time column as
# ``datetime.timedelta`` (whose ``str`` drops the zero padding, e.g.
# ``9:30:00``) and a Datetime column as ``datetime.datetime``. Expectations are
# built as the site's text format, so every comparison canonicalizes both sides.
_SITE_DATE_FORMAT = "%Y-%m-%d"
_SITE_TIME_FORMAT = "%H:%M:%S"
_SITE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
_DATETIME_TEXT_FORMATS = (
    _SITE_DATETIME_FORMAT,
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
)
_TIME_TEXT = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")
_TYPED_FIELDTYPES = ("Date", "Time", "Datetime")


def _canonical_date(value):
    if isinstance(value, datetime):
        return value.date().strftime(_SITE_DATE_FORMAT)
    if isinstance(value, date):
        return value.strftime(_SITE_DATE_FORMAT)
    if isinstance(value, str):
        try:
            return datetime.strptime(value.strip(), _SITE_DATE_FORMAT).strftime(
                _SITE_DATE_FORMAT
            )
        except ValueError:
            return None
    return None


def _canonical_time(value):
    if isinstance(value, timedelta):
        total_seconds = int(value.total_seconds())
        if total_seconds < 0 or total_seconds >= 24 * 60 * 60:
            return None
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.strftime(_SITE_TIME_FORMAT)
    if isinstance(value, str):
        match = _TIME_TEXT.fullmatch(value.strip())
        if match is None:
            return None
        hours, minutes, seconds = match.groups()
        if int(hours) > 23 or int(minutes) > 59 or int(seconds or 0) > 59:
            return None
        return f"{int(hours):02d}:{minutes}:{seconds or '00'}"
    return None


def _canonical_datetime(value):
    if isinstance(value, datetime):
        return value.strftime(_SITE_DATETIME_FORMAT)
    if isinstance(value, str):
        text = value.strip()
        for text_format in _DATETIME_TEXT_FORMATS:
            try:
                return datetime.strptime(text, text_format).strftime(
                    _SITE_DATETIME_FORMAT
                )
            except ValueError:
                continue
    return None


def canonical_value(field, value):
    """Canonical comparable form for a typed read-back or expected string.

    Only Date, Time and Datetime fields are rewritten; every other field keeps
    its own value so an exact-type mismatch is still refused.
    """

    fieldtype = APPOINTMENT_FIELD_METADATA.get(field, ("",))[0]
    if fieldtype == "Date":
        return _canonical_date(value)
    if fieldtype == "Time":
        return _canonical_time(value)
    if fieldtype == "Datetime":
        return _canonical_datetime(value)
    return value


def values_match(field, actual, expected):
    """Compare a native value with its expectation under the field's type."""

    fieldtype = APPOINTMENT_FIELD_METADATA.get(field, ("",))[0]
    if fieldtype not in _TYPED_FIELDTYPES:
        return actual == expected
    canonical = canonical_value(field, actual)
    return canonical is not None and canonical == canonical_value(field, expected)


def _field_value(field, attributes):
    if hasattr(field, "get"):
        values = [field.get(name) for name in attributes]
    else:
        values = [getattr(field, name, None) for name in attributes]
    fieldtype, options, fetch_from, fetch_if_empty, default, permlevel, mask,         reqd, read_only, hidden, set_only_once = values
    default_value = "" if default in (None, "") else str(default)
    return (
        fieldtype or "",
        options or "",
        fetch_from or "",
        1 if fetch_if_empty else 0,
        default_value,
        int(permlevel or 0),
        1 if mask else 0,
        1 if reqd else 0,
        1 if read_only else 0,
        1 if hidden else 0,
        1 if set_only_once else 0,
    )


def normalize_meta_fields(fields):
    """Normalize effective DocFields to the reviewed attribute mapping."""

    normalized = {}
    for field in fields or ():
        name = _value(field, "fieldname")
        if not isinstance(name, str) or not name:
            continue
        normalized[name] = _field_value(
            field,
            (
                "fieldtype",
                "options",
                "fetch_from",
                "fetch_if_empty",
                "default",
                "permlevel",
                "mask",
                "reqd",
                "read_only",
                "hidden",
                "set_only_once",
            ),
        )
    return normalized


def _metadata_difference(actual, expected):
    actual = normalize_meta_fields(actual) if not isinstance(actual, dict) else actual
    missing = sorted(set(expected) - set(actual))
    if missing:
        return f"missing reviewed field {missing[0]}"
    extra = sorted(set(actual) - set(expected))
    if extra:
        return f"unreviewed field {extra[0]}"
    for name in expected:
        if tuple(actual[name]) != tuple(expected[name]):
            return f"field metadata changed for {name}"
    return None


def metadata_problem(fields):
    """Return a reason when effective metadata is not the reviewed inventory."""

    return _metadata_difference(fields, APPOINTMENT_FIELD_METADATA)


def _identity_problem(values, expected=None, ignore=frozenset()):
    for name, kind in APPOINTMENT_FIELD_CLASSES.items():
        if name in ignore:
            continue
        value = _value(values, name)
        if kind == LAYOUT:
            continue
        if kind in (IDENTITY, DESTINATION):
            if value in (None, ""):
                return f"missing {name}"
            if expected is not None and expected.get(name) is not None:
                if not values_match(name, value, expected[name]):
                    return f"unexpected {name}"
            continue
        if kind == DEFAULT_INERT:
            if value not in _EMPTY_VALUE:
                return f"default field {name} is populated"
            continue
        if kind == EMPTY:
            if value not in _EMPTY_VALUE:
                return f"unsupported field {name} is populated"
            continue
        # DERIVED values are reviewed controller output; inserted values are
        # checked individually by inserted_problem.
    for name, value in (values.items() if hasattr(values, "items") else ()):
        if name in IGNORED_STANDARD_FIELDS or name in APPOINTMENT_FIELD_CLASSES:
            continue
        if value not in _EMPTY_VALUE:
            return f"unreviewed field {name} is populated"
    return None


def prepared_problem(values, expected=None):
    """Check the constructed booking document before insert.

    Derived fields must still be empty (or, for the two fetch fields only,
    already equal the locked source values). This prevents a DocField default
    or an injected property setter from smuggling a derived value into insert.
    The one exception is ``naming_series``: ``frappe.new_doc`` pre-fills it
    from the reviewed Select static default, so exactly the pinned series is
    accepted and every other value is still refused.
    """

    problem = _identity_problem(values, expected)
    if problem is not None:
        return problem
    for name, kind in APPOINTMENT_FIELD_CLASSES.items():
        if kind != DERIVED:
            continue
        value = _value(values, name)
        if value in _EMPTY_VALUE:
            continue
        if name == "naming_series" and str(value) == PINNED_NAMING_SERIES:
            continue
        if (
            name in ("patient_name", "patient_sex")
            and expected is not None
            and str(value) == str(expected.get(name))
        ):
            continue
        return f"prepared derived field {name} is populated"
    return None


def identity_config_problem(values, expected):
    """Enforce the configured synthetic patient/company/type/department.

    ``expected`` may carry ``patient`` (exact) or ``patients`` (approved set),
    plus the exact company, appointment type and medical department. This is
    executed on the locked native row and on non-mutating detail reads.
    """

    if not isinstance(expected, dict):
        return "expected identity unavailable"
    patient = _value(values, "patient")
    exact = expected.get("patient")
    if exact is not None and patient != exact:
        return "target patient is not the configured record"
    patients = expected.get("patients")
    if patients is not None and patient not in set(patients):
        return "target patient is not an approved synthetic patient"
    for field in ("company", "appointment_type", "department"):
        wanted = expected.get(field)
        if wanted is not None and _value(values, field) != wanted:
            return f"target {field} is not configured"
    if _value(values, "appointment_for") != "Department":
        return "target appointment_for is not Department"
    return None


def _parse_date(value):
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def _parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%H:%M:%S")
    except ValueError:
        return None


def expected_derived(expected, today):
    """Return the reviewed controller-derived values for a booking."""

    date = _parse_date(expected.get("appointment_date"))
    time = _parse_time(expected.get("appointment_time"))
    duration = expected.get("duration")
    if date is None or time is None or isinstance(duration, bool) or not isinstance(
        duration, int
    ) or duration <= 0:
        return None
    today_date = _parse_date(today)
    if today_date is None:
        return None
    if date > today_date:
        status = "Scheduled"
    elif date == today_date:
        status = "Open"
    else:
        status = "No Show"
    start = datetime.combine(date, time.time())
    end = start + timedelta(minutes=duration)
    patient_name = expected.get("patient_name") or expected.get("patient")
    return {
        "status": status,
        "title": f"{patient_name} at {expected.get('department')}",
        "appointment_datetime": start.strftime("%Y-%m-%d %H:%M:%S"),
        "appointment_end_datetime": end.strftime("%Y-%m-%d %H:%M:%S"),
        "appointment_based_on_check_in": True,
    }


def inserted_problem(values, expected, *, today):
    """Check a freshly inserted booking, allowing only reviewed deltas."""

    identity_problem = _identity_problem(values, expected)
    if identity_problem is not None:
        return identity_problem
    derived = expected_derived(expected, today)
    if derived is None:
        return "booking expectations are malformed"
    if _value(values, "naming_series") != PINNED_NAMING_SERIES:
        return "unexpected naming series"
    if _value(values, "patient_name") != expected.get("patient_name"):
        return "patient_name fetch mismatch"
    if _value(values, "patient_sex") != expected.get("patient_sex"):
        return "patient_sex fetch mismatch"
    if _value(values, "patient_age") not in _EMPTY_VALUE:
        return "unexpected patient_age"
    for name, expected_value in derived.items():
        if not values_match(name, _value(values, name), expected_value):
            return f"unexpected {name}"
    if _value(values, "position_in_queue") not in _EMPTY_VALUE:
        return "unexpected position_in_queue"
    return None


def target_problem(values):
    """Check a locked or read-only target before it may be mutated."""

    return _target_problem(values)


def transition_problem(values):
    """Check a target after the cancel/no-show transition wrote its note.

    ``notes`` is the only field this contract's transitions populate, so it is
    the single reviewed exception to the closed-world EMPTY classification. The
    post-condition already compares it verbatim against the pinned postimage.
    """

    return _target_problem(values, ignore=frozenset({"notes"}))


def _target_problem(values, ignore=frozenset()):
    problem = _identity_problem(values, ignore=ignore)
    if problem is not None:
        return problem
    if _value(values, "appointment_for") != "Department":
        return "unsupported appointment_for"
    duration = _value(values, "duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return "invalid duration"
    if not _value(values, "patient_name"):
        return "missing patient_name"
    if not _value(values, "patient_sex"):
        return "missing patient_sex"
    if _value(values, "patient_age") not in _EMPTY_VALUE:
        return "unexpected patient_age"
    if _value(values, "naming_series") != PINNED_NAMING_SERIES:
        return "unexpected naming series"
    for name in ("appointment_datetime", "appointment_end_datetime", "title"):
        if not _value(values, name):
            return f"missing {name}"
    checkin_flag = _value(values, "appointment_based_on_check_in")
    if checkin_flag not in (0, 1, None, False, True, ""):
        return "unexpected appointment_based_on_check_in"
    queue = _value(values, "position_in_queue")
    if queue not in (None, "", 0) and (
        isinstance(queue, bool) or not isinstance(queue, int) or queue < 1
    ):
        return "unexpected position_in_queue"
    return None


def source_problem(sources):
    """Check the locked Patient and Appointment Type fetch sources."""

    if not isinstance(sources, dict):
        return "sources unreadable"
    name = sources.get("patient_name")
    if not isinstance(name, str) or not name.strip():
        return "patient_name is unavailable"
    sex = sources.get("patient_sex")
    if not isinstance(sex, str) or not sex.strip():
        return "patient_sex is unavailable"
    for field in ("inpatient_record", "emergency_record"):
        if sources.get(field) not in _EMPTY_VALUE:
            return f"unsupported patient {field}"
    duration = sources.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
        return "appointment type duration is invalid"
    if sources.get("allow_booking_for") != "Department":
        return "appointment type is not a department booking mode"
    return None


def reschedule_target_eligible(
    *, status, appointment_date, patient, today, dedicated_patient, has_receipt
):
    """Shared non-terminal, non-dedicated same-day reschedule eligibility."""

    if status not in RESCHEDULE_STATUSES:
        return False
    if has_receipt:
        return False
    if appointment_date is None or today is None or appointment_date < today:
        return False
    return not (patient == dedicated_patient and appointment_date == today)


def checkin_target_eligible(
    *,
    status,
    appointment_date,
    today,
    patient,
    dedicated_patient,
    has_receipt,
):
    """Target-scoped dedicated same-day check-in eligibility.

    There is deliberately no cross-record uniqueness condition: the installed
    ``check_in_appointment`` entry point has no such rule, and a bounded page
    cannot establish one. A second check-in of the *same* record is refused by
    the status lifecycle (``Checked In`` is not an eligible status), which the
    adapter rechecks under the row lock.
    """

    if status not in CHECKIN_STATUSES:
        return False
    if has_receipt:
        return False
    if appointment_date is None or today is None:
        return False
    return appointment_date == today and patient == dedicated_patient


def _populated(value):
    return value not in _EMPTY_VALUE


def _notes_preimage_problem(values):
    notes = _value(values, "notes")
    if notes is None:
        return None
    if not isinstance(notes, str):
        return "notes is unreadable"
    if len(notes) > MAX_NOTES_LENGTH:
        return "notes is too long"
    return None


def cancel_target_problem(values):
    """Refuse a cancel target before any fence claim.

    The status must be non-terminal (``Cancelled`` itself, ``Closed``,
    ``Checked Out`` and ``No Show`` are refused). Any populated branch that the
    pinned ``cancel_appointment`` handles through a linked document is refused
    here as well: its outcome is not deep-verified by this contract, and a
    cancel must never latch the site on an unreviewed side effect.
    """

    status = _value(values, "status")
    if status not in CANCELLABLE_STATUSES:
        return "target appointment is not cancellable"
    for field in CANCEL_REFUSED_LINKS:
        if _populated(_value(values, field)):
            return f"linked {field} handling is not reviewed"
    return _notes_preimage_problem(values)


def no_show_target_problem(values):
    """Refuse a manual no-show target before any fence claim."""

    status = _value(values, "status")
    if status not in NO_SHOW_STATUSES:
        return "target appointment is not no-show eligible"
    return _notes_preimage_problem(values)


def note_line(kind, *, actor, reason, recorded_at):
    """Return the pinned, verbatim note line for one transition.

    The appointment has no dedicated reason field (the only available field is
    ``notes``), so the pinned format records the transition, the acting user,
    the reason and the site's current timestamp. It is compared verbatim after
    the native save.
    """

    label = _NOTE_LABELS[kind]
    return f"[{recorded_at}] {label} by {actor}: {reason}"


def note_postimage(preimage, line):
    """Append one pinned line to the exact locked ``notes`` preimage."""

    text = preimage if isinstance(preimage, str) else ""
    return f"{text}\n{line}" if text else line


def cancel_event_expected(values):
    """True only for the branch where the pinned method cancels the event.

    ``cancel_appointment`` returns before every side effect when the target has
    no practitioner (patient_appointment.py:655-656); a department booking has
    no practitioner, so its calendar event is attested as untouched rather than
    deep-verified.
    """

    return bool(_value(values, "practitioner")) or False


def cancel_postcondition_problem(values, expected_notes):
    """Check the returned state after the pinned cancel transition."""

    if _value(values, "status") != CANCELLED_STATUS:
        return "status is not Cancelled"
    if _value(values, "notes") != expected_notes:
        return "cancel note was not recorded verbatim"
    return None


def no_show_postcondition_problem(values, expected_notes):
    """Check the returned state after the native no-show save."""

    if _value(values, "status") != NO_SHOW_STATUS:
        return "status is not No Show"
    if _value(values, "notes") != expected_notes:
        return "no-show note was not recorded verbatim"
    return None


def _normalized_sequence(value):
    return tuple(value or ())


def boundary_problem(snapshot):
    """Return the first unreviewed boundary drift, or ``None`` when pinned."""

    if not isinstance(snapshot, dict):
        return "boundary snapshot unreadable"
    expected = _REVIEWED_BOUNDARY
    for key in ("versions", "source_sha256"):
        actual = snapshot.get(key)
        if not isinstance(actual, dict):
            return f"{key} unreadable"
        for name, value in expected[key].items():
            if actual.get(name) != value:
                return f"{key} drift for {name}"
    for key in (
        "patient_appointment_class",
        "checkin_module",
        "checkin_name",
        "cancel_module",
        "cancel_name",
        "enabled_server_scripts",
        "enabled_notifications",
        "enabled_webhooks",
        "active_workflows",
        "active_assignment_rules",
        "enabled_automations",
        "enabled_milestone_trackers",
    ):
        if snapshot.get(key) != expected[key]:
            return f"{key} drift"
    for key in (
        "doc_events_patient_appointment",
        "wildcard_before_insert",
        "wildcard_before_validate",
        "wildcard_before_save",
        "wildcard_after_insert",
        "wildcard_on_update",
        "wildcard_validate",
        "wildcard_on_change",
        "override_doctype_class",
        "checkin_whitelisted_override",
        "cancel_whitelisted_override",
        "scheduler_all",
        "scheduler_daily",
        "extend_doctype_class",
        "controller_mro",
    ):
        if _normalized_sequence(snapshot.get(key)) != _normalized_sequence(
            expected[key]
        ):
            return f"{key} drift"
    if snapshot.get("appointment_behavior") != expected["appointment_behavior"]:
        return "appointment_behavior drift"
    for key in (
        "appointment_metadata",
        "patient_metadata",
        "appointment_type_metadata",
    ):
        metadata = snapshot.get(key)
        if not isinstance(metadata, dict):
            return f"{key} unreadable"
        difference = _metadata_difference(metadata, expected[key])
        if difference is not None:
            return f"{key}: {difference}"
    return None
