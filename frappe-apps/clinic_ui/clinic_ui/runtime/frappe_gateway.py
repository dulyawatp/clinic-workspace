"""The single Frappe seam for the read-only clinic endpoints.

Everything here is resolved from the current request and the current site; no
value is accepted from the client. Verified against the Frappe ``version-16``
source (not against an installed lab build; see the README blockers):

- ``frappe.sessions.get_csrf_token()`` lazily generates the per-session token
  stored in ``frappe.local.session.data.csrf_token``.
- ``frappe.session.sid`` / ``frappe.session.user`` are the request session.
- ``frappe.has_permission(doctype, ptype, doc, user)`` accepts a document name
  and loads it lazily for the permission check.
- ``frappe.local.response_headers`` is merged into the HTTP response, so
  ``Cache-Control: no-store`` can be set for both the success and the denial
  path.

The site configuration is read from that site's own ``site_config.json`` so a
``common_site_config`` value can never enable the feature implicitly.
"""

import hmac
import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as _date

from clinic_ui.core import (
    admin_contract,
    appointment_contract,
    appointment_write,
    config,
    deployment_marker,
    encounter_contract,
    encounter_write,
    fence_store,
    patient_write,
    procedure_contract,
    procedure_write,
    stock_contract,
    write_fence,
)
from clinic_ui.runtime.traceback import UNSAFE_V1_KEYS, WRITE_ERROR_CODES

PATIENT_DOCTYPE = "Patient"
APPOINTMENT_DOCTYPE = "Patient Appointment"
DISPLAY_NAME_FIELD = "patient_name"
# The only Patient Appointment DocFields a synthetic appointment row may read.
# Each carries a permlevel and can be masked; nothing is fetched before both
# checks pass for every field.
APPOINTMENT_DOCFIELD_FIELDS = (
    "patient",
    "appointment_date",
    "appointment_time",
    "status",
)
# Cancel/no-show decision fields. They are reviewed contract fields at
# permlevel 0 and are read only after the same document and field-level checks
# as the list rows, so the pre-fence decision never depends on configuration.
APPOINTMENT_ACTION_TEXT_FIELDS = (
    "notes",
    "practitioner",
    "event",
    "ref_sales_invoice",
    "insurance_coverage",
    "service_request",
    "mode_of_payment",
)
APPOINTMENT_ACTION_RAW_FIELDS = ("invoiced", "paid_amount")
APPOINTMENT_ACTION_FIELDS = (
    APPOINTMENT_ACTION_TEXT_FIELDS + APPOINTMENT_ACTION_RAW_FIELDS
)
# DocFields that carry a permlevel and can be masked. Verified against Frappe
# v16: ``Meta._fields`` is ``{field.fieldname: field for field in self.fields}``
# (``frappe/model/meta.py:514-515``), so it contains DocFields only.
PATIENT_DOCFIELD_FIELDS = (
    "patient_name",
    "first_name",
    "last_name",
    "sex",
    "dob",
    "status",
)
# ``modified`` is a standard ``default_fields`` column, not a DocField
# (``frappe/model/__init__.py:84-93``). It has no permlevel/mask entry, so it is
# read only after the document read permission succeeds, not through
# ``Meta.get_field``.
PATIENT_STANDARD_FIELDS = ("modified",)
# Fields that, when populated on a constructed Patient, make the native
# controller create or update a Customer, Contact, Address or Website User.
# Verified against Frappe healthcare v16 Patient: ``customer``/customer fields
# drive ``create_customer``/``update_linked_customer``; email/mobile/phone drive
# ``set_contact``/``create_website_user``; any ``address_line1`` triggers
# ``make_address``; ``user_id`` + email + invite_user drives website-user
# creation. ``invite_user`` itself must stay exactly 0.
PATIENT_CREATE_TRIGGER_FIELDS = (
    "customer",
    "customer_name",
    "email",
    "mobile",
    "phone",
    "user_id",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "pincode",
    "country",
)
# Exactly the fields a detail read may load. Every other Patient column stays
# unread, so no identifier, contact value or clinical field can reach the DTO.
PATIENT_VIEW_FIELDS = PATIENT_DOCFIELD_FIELDS + PATIENT_STANDARD_FIELDS
APPOINTMENT_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
# The site timestamp recorded verbatim in an appended cancel/no-show note,
# exactly as ``frappe.utils.now()`` returns it.
NOTE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
NOTE_EVENT_DOCTYPE = "Event"

# Pinned native contract (installed healthcare v16.6.1,
# `healthcare/healthcare/doctype/patient_appointment/patient_appointment.py`):
#
# * ``check_in_appointment(appointment_id, practitioner=None, service_unit=None)``
#   is a module-level whitelisted function that saves the document and refuses
#   only ``Cancelled``, ``Closed`` and ``Checked Out``. The adapter narrows that
#   to Open/Confirmed on the site's own current date and calls the real symbol.
# * ``PatientAppointment.validate`` derives status by date:
#   future -> ``Scheduled`` (or an explicit ``Confirmed``), today -> ``Open``
#   (or ``Confirmed``), past -> ``No Show``; terminal statuses are preserved.
# * ``after_insert``/``on_update`` side effects are gated by Healthcare Settings
#   and by target fields. Every switch and every trigger field below must be
#   clear before a booking is prepared, and the target is rechecked under the
#   row lock before any save.
APPOINTMENT_CHECKIN_STATUSES = appointment_contract.CHECKIN_STATUSES
APPOINTMENT_RESCHEDULE_STATUSES = appointment_contract.RESCHEDULE_STATUSES
APPOINTMENT_CHECKED_IN_STATUS = "Checked In"
# Healthcare Settings switches that make the controller message patients,
# invoice, or manage free follow-up validity. ``default_google_calendar`` is a
# link default that would create calendar events for any practitioner booking.
APPOINTMENT_UNSAFE_SETTINGS = (
    "show_payment_popup",
    "send_appointment_confirmation",
    "send_appointment_reminder",
    "enable_free_follow_ups",
    "apply_free_follow_ups_on_encounters",
)
APPOINTMENT_UNSAFE_SETTING_LINKS = ("default_google_calendar",)
# The controller's own same-day check-in entry point (pinned module path).
NATIVE_CHECKIN_MODULE = appointment_contract.CHECKIN_MODULE
NATIVE_CHECKIN_NAME = appointment_contract.CHECKIN_NAME
APPOINTMENT_CONTRACT_FIELDS = tuple(appointment_contract.APPOINTMENT_FIELD_CLASSES)
APPOINTMENT_EXPECTED_KEYS = (
    "patient",
    "company",
    "appointment_type",
    "department",
    "appointment_for",
    "duration",
    "appointment_date",
    "appointment_time",
)


@dataclass(frozen=True)
class AppointmentLockedState:
    """Freshly locked and verified target values passed back to the endpoint."""

    doc: object
    patient: str | None
    company: str | None
    department: str | None
    appointment_type: str | None
    appointment_for: str | None
    duration: object
    status: str | None
    appointment_date: str | None
    appointment_time: str | None
    modified: str | None
    patient_name: str | None = None
    patient_sex: str | None = None
    notes: str | None = None
    practitioner: str | None = None
    event: str | None = None
    invoiced: object = None
    ref_sales_invoice: str | None = None
    paid_amount: object = None
    insurance_coverage: str | None = None
    service_request: str | None = None
    mode_of_payment: str | None = None
NO_STORE_HEADER = "Cache-Control"
NO_STORE_VALUE = "no-store"
DENIAL_MESSAGE = "Not permitted"
ANONYMOUS_STATUS = 401

ENCOUNTER_DOCTYPE = encounter_contract.ENCOUNTER_DOCTYPE
CLINICAL_NOTE_DOCTYPE = encounter_contract.CLINICAL_NOTE_DOCTYPE
VITAL_SIGNS_DOCTYPE = encounter_contract.VITAL_SIGNS_DOCTYPE
SERVICE_REQUEST_DOCTYPE = encounter_contract.SERVICE_REQUEST_DOCTYPE
ENCOUNTER_READ_FIELDS = ("patient", "docstatus", "status", "appointment")
ENCOUNTER_CHILD_TABLES = {
    "complaint": "Complaint Detail",
    "diagnosis": "Patient Encounter Diagnosis",
    "drug_prescription": "Drug Prescription",
    "lab_test_prescription": "Lab Prescription",
    "procedure_prescription": "Procedure Prescription",
}


@dataclass(frozen=True)
class EncounterLockedState:
    """Freshly locked and verified encounter values passed to the endpoint."""

    doc: object
    patient: str | None
    docstatus: object
    status: str | None
    appointment: str | None
    appointment_status: str | None
    modified: str | None
    complaint: bool
    diagnosis_count: int
    medication_codes: tuple
    lab_count: int
    procedure_count: int
    clinical_note: str | None
    clinical_note_modified: str | None
    assessment: str | None = None
    plan: str | None = None
    followup: str | None = None

    def as_dict(self):
        return {
            "patient": self.patient,
            "docstatus": self.docstatus,
            "status": self.status,
            "appointment": self.appointment,
            "appointment_status": self.appointment_status,
            "modified": self.modified,
            "complaint": self.complaint,
            "diagnosis_count": self.diagnosis_count,
            "medication_codes": self.medication_codes,
            "lab_count": self.lab_count,
            "procedure_count": self.procedure_count,
            "clinical_note": self.clinical_note,
            "clinical_note_modified": self.clinical_note_modified,
            "assessment": self.assessment,
            "plan": self.plan,
            "followup": self.followup,
            "allergy_reviewed": False,
        }


@dataclass(frozen=True)
class ProcedureLockedState:
    """Freshly locked and verified procedure values passed to the endpoint."""

    doc: object
    patient: str | None
    docstatus: object
    status: str | None
    consume_stock: bool
    warehouse: str | None
    company: str | None
    price_list: str | None
    customer: str | None
    procedure_template: str | None
    post_op_checklist: str | None
    service_request: str | None
    appointment: str | None
    modified: str | None
    items: tuple
    allow_negative_stock: bool
    today: str | None

    def as_dict(self):
        return {
            "patient": self.patient,
            "docstatus": self.docstatus,
            "status": self.status,
            "consume_stock": self.consume_stock,
            "warehouse": self.warehouse,
            "company": self.company,
            "price_list": self.price_list,
            "customer": self.customer,
            "procedure_template": self.procedure_template,
            "post_op_checklist": self.post_op_checklist,
            "service_request": self.service_request,
            "appointment": self.appointment,
            "modified": self.modified,
            "items": self.items,
            "allow_negative_stock": self.allow_negative_stock,
            "today": self.today,
        }


class Gateway:
    """Adapter used by the whitelisted entry points."""

    def __init__(self, frappe_module=None):
        self._frappe = frappe_module
        self._site_config = None
        self._write_ledger = None

    @property
    def frappe(self):
        if self._frappe is None:
            import frappe

            self._frappe = frappe
        return self._frappe

    @property
    def site(self):
        return getattr(self.frappe.local, "site", None)

    def _configuration(self):
        if self._site_config is None:
            try:
                path = self.frappe.get_site_path("site_config.json")
                with open(path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except Exception:
                loaded = {}
            self._site_config = loaded if isinstance(loaded, dict) else {}
        return self._site_config

    def setting(self, key):
        return self._configuration().get(key)

    @property
    def current_session(self):
        from clinic_ui.core.policy import SessionView

        try:
            frappe = self.frappe
            local = getattr(frappe, "local", None)
            if local is None or getattr(local, "session", None) is None:
                return None
            user = frappe.session.user
            sid = frappe.session.sid
            if not isinstance(user, str) or user == "Guest":
                return SessionView(
                    sid=sid if isinstance(sid, str) else "",
                    user=user if isinstance(user, str) else "",
                    full_name="",
                    enabled=False,
                    roles=(),
                )
            record_full_name = frappe.db.get_value("User", user, "full_name")
            record_enabled = frappe.db.get_value("User", user, "enabled")
            roles = frappe.get_roles(user)
        except Exception:
            return None

        return SessionView(
            sid=sid if isinstance(sid, str) else "",
            user=user,
            full_name=(
                record_full_name
                if isinstance(record_full_name, str) and record_full_name.strip()
                else user
            ),
            enabled=bool(record_enabled),
            roles=tuple(role for role in (roles or ()) if isinstance(role, str)),
        )

    def csrf_token(self):
        try:
            from frappe.sessions import get_csrf_token

            token = get_csrf_token()
        except Exception:
            return None
        if not isinstance(token, str) or not token.strip():
            return None
        return token

    def _saved_csrf_token(self):
        """Read the initialized session CSRF token without framework bypasses.

        Frappe v16 stores it at ``frappe.local.session.data.csrf_token``
        (``frappe/sessions.py:202-206``); ``frappe.session.data`` exposes that
        mapping. The framework's ``ignore_csrf`` and allowed-referrer branches
        are deliberately not consulted here.
        """

        session = self.frappe.session
        data = getattr(session, "data", None)
        token = None
        if data is not None and hasattr(data, "get"):
            token = data.get("csrf_token")
        if token is None:
            token = getattr(session, "csrf_token", None)
        return token

    def csrf_token_valid(self, supplied=None):
        """Explicit same-session CSRF check, independent of framework config.

        Both the initialized session token and the supplied token must be
        non-empty strings and equal. A missing token, ``frappe.conf.ignore_csrf``
        and an allowed referrer do not bypass this check.
        """

        try:
            saved = self._saved_csrf_token()
            if not isinstance(saved, str) or not saved:
                return False
            if not isinstance(supplied, str) or not supplied:
                return False
            return hmac.compare_digest(saved, supplied)
        except Exception:
            return False

    def mark_no_store(self):
        headers = getattr(self.frappe.local, "response_headers", None)
        if headers is None:
            headers = {}
            self.frappe.local.response_headers = headers
        headers[NO_STORE_HEADER] = NO_STORE_VALUE

    def suppress_traceback(self):
        """Disable traceback and message-channel leakage for this error response.

        ``frappe.utils.response.is_traceback_allowed`` honours
        ``frappe.local.flags.disable_traceback``. Message channels are
        serialized even when tracebacks are disabled, so a caught
        ``frappe.throw`` message, ``_server_messages`` and ``_error_message``
        are cleared here before the generic denial is raised. The flag changes
        no status and no data; a missing container or framework failure is
        ignored so suppression can never become a new error path.
        """

        try:
            local = self.frappe.local
            flags = getattr(local, "flags", None)
            if flags is not None:
                flags.disable_traceback = True
                flags.error_message = None
            if hasattr(local, "message_log"):
                local.message_log = []
            response = getattr(local, "response", None)
            if isinstance(response, dict):
                for key in UNSAFE_V1_KEYS:
                    response.pop(key, None)
                response.pop("messages", None)
        except Exception:
            return

    def deny(self):
        self.suppress_traceback()
        raise self.frappe.PermissionError(DENIAL_MESSAGE)

    def respond_anonymous(self):
        """Return the fixed anonymous body with an explicit 401 status.

        Frappe reads ``frappe.local.response["http_status_code"]`` for the status
        and removes it from the JSON body, so the client receives
        ``{"message": {"code": "AUTH_EXPIRED"}}`` with HTTP 401. No identity,
        role, epoch, CSRF token or configuration value is included.
        """

        from clinic_ui.core import dto

        self.frappe.local.response["http_status_code"] = ANONYMOUS_STATUS
        return dto.build_anonymous()

    def native_patient_display_name(self, native_patient_id):
        """Current-user native read authority plus the display-only field.

        Returns ``None`` for a missing record, a denied user, a malformed value
        or any adapter failure; the caller then omits the entry. The document
        read is followed by the same field-level checks as every other Patient
        read: ``patient_name`` must be at an accessible read permlevel and must
        not be masked for the current user. No field value is fetched before
        both checks pass. Record fields other than the display name are never
        read into the response.
        """

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                PATIENT_DOCTYPE, ptype="read", doc=native_patient_id, user=user
            ):
                return None
            meta = frappe.get_meta(PATIENT_DOCTYPE)
            accessible_permlevels = meta.get_permlevel_access("read", user=user)
            field_definition = meta.get_field(DISPLAY_NAME_FIELD)
            if (
                field_definition is None
                or field_definition.permlevel not in accessible_permlevels
            ):
                return None
            masked_fields = meta.get_masked_fields() or ()
            masked_names = {
                masked.fieldname
                for masked in masked_fields
                if getattr(masked, "fieldname", None)
            }
            if DISPLAY_NAME_FIELD in masked_names:
                return None
            display_name = frappe.db.get_value(
                PATIENT_DOCTYPE, native_patient_id, DISPLAY_NAME_FIELD
            )
        except Exception:
            return None

        if not isinstance(display_name, str) or not display_name.strip():
            return None
        return display_name.strip()

    @staticmethod
    def _text_value(value):
        """Return a JSON-safe scalar: text as-is, date/datetime as ISO, else None.

        A blank optional field is reported as ``None`` rather than as an empty
        value, so an unset name part or gender does not refuse the whole read.
        """

        if value is None:
            return None
        if isinstance(value, str):
            return value if value.strip() else None
        isoformat = getattr(value, "isoformat", None)
        if callable(isoformat):
            try:
                return isoformat()
            except Exception:
                return None
        return None

    @staticmethod
    def _time_value(value):
        """Return a wall-clock time as ``HH:MM:SS`` or ``None``.

        Frappe may return a Time column as ``datetime.time``, a string or a
        ``datetime.timedelta`` depending on the driver and conversion. A
        malformed or out-of-range value is ``None`` so the caller fails closed.
        """

        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            return text if APPOINTMENT_TIME_PATTERN.fullmatch(text) else None
        total_seconds = getattr(value, "total_seconds", None)
        if callable(total_seconds):
            try:
                seconds = int(total_seconds())
            except Exception:
                return None
            if seconds < 0 or seconds >= 24 * 60 * 60:
                return None
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        text = Gateway._text_value(value)
        if not isinstance(text, str) or not APPOINTMENT_TIME_PATTERN.fullmatch(text):
            return None
        return text

    def native_appointment_view(self, native_appointment_id):
        """Allowlisted appointment fields under document and field-level rights.

        The document read permission is checked first. Every allowlisted field
        must then exist at a readable permlevel and must not be masked for the
        current user. Only after all checks pass are the four internal values
        fetched. ``None`` covers a denied document, a restricted or masked
        field, a malformed record and any adapter failure.

        The native patient id and appointment docname are returned to the
        endpoint only; the endpoint maps the patient through the explicit
        synthetic patient allowlist and never publishes either native value.
        """

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                APPOINTMENT_DOCTYPE,
                ptype="read",
                doc=native_appointment_id,
                user=user,
            ):
                return None
            meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
            accessible_permlevels = meta.get_permlevel_access("read", user=user)
            for field in APPOINTMENT_DOCFIELD_FIELDS:
                field_definition = meta.get_field(field)
                if (
                    field_definition is None
                    or field_definition.permlevel not in accessible_permlevels
                ):
                    return None
            masked_fields = meta.get_masked_fields() or ()
            masked_names = {
                masked.fieldname
                for masked in masked_fields
                if getattr(masked, "fieldname", None)
            }
            if any(field in masked_names for field in APPOINTMENT_DOCFIELD_FIELDS):
                return None
            detail = {}
            for field in APPOINTMENT_DOCFIELD_FIELDS:
                value = frappe.db.get_value(
                    APPOINTMENT_DOCTYPE, native_appointment_id, field
                )
                if field == "appointment_time":
                    value = self._time_value(value)
                else:
                    value = self._text_value(value)
                if not isinstance(value, str) or not value.strip():
                    return None
                detail[field] = value.strip()
        except Exception:
            return None
        return detail

    def native_appointment_detail(self, native_appointment_id):
        """The five list fields plus the authoritative ``modified`` version.

        It reuses the verified document and field-level read checks of
        :meth:`native_appointment_view`; ``modified`` is a standard column with
        no DocField metadata, so it is read only after those checks succeed.
        """

        detail = self.native_appointment_view(native_appointment_id)
        if detail is None:
            return None
        try:
            modified = self._text_value(
                self.frappe.db.get_value(
                    APPOINTMENT_DOCTYPE, native_appointment_id, "modified"
                )
            )
        except Exception:
            return None
        if not isinstance(modified, str) or not modified.strip():
            return None
        detail["modified"] = modified.strip()
        return detail

    def site_today(self):
        """Return the site's current local date as strict ISO ``YYYY-MM-DD``.

        This is the pinned ``frappe.utils.nowdate()`` source, not the browser's
        date and not a client value. A malformed or unreadable value returns
        ``None`` so the caller fails closed.
        """

        try:
            value = self.frappe.utils.nowdate()
        except Exception:
            return None
        if not isinstance(value, str) or len(value) != 10:
            return None
        try:
            parsed = _date.fromisoformat(value)
        except ValueError:
            return None
        if parsed.isoformat() != value:
            return None
        return value

    def native_patient_can_read(self, native_patient_id):
        """The current user's own document-level Patient read authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    PATIENT_DOCTYPE,
                    ptype="read",
                    doc=native_patient_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_doctype_can_read(self, doctype, doc=None):
        """Current-user native read authority for one linked DocType/document."""

        try:
            if not isinstance(doctype, str) or not doctype.strip():
                return False
            if doc is not None and (
                not isinstance(doc, str) or not doc.strip()
            ):
                return False
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="read",
                    doc=doc,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_appointment_can_create(self):
        """Role-level native Patient Appointment create authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    APPOINTMENT_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_appointment_can_write(self, native_appointment_id=None):
        """Role-level or exact-document native Patient Appointment write authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    APPOINTMENT_DOCTYPE,
                    ptype="write",
                    doc=native_appointment_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def healthcare_appointment_side_effects_unsafe(self):
        """True when any installed controller side-effect switch is active.

        Every switch is read by its pinned Healthcare Settings field name. A
        truthy confirmation/reminder/payment/free-follow-up switch, a configured
        default calendar, or an unreadable settings row fails closed. The
        adapter never changes a setting.
        """

        try:
            for field in APPOINTMENT_UNSAFE_SETTINGS:
                if self.frappe.db.get_single_value("Healthcare Settings", field):
                    return True
            for field in APPOINTMENT_UNSAFE_SETTING_LINKS:
                if self.frappe.db.get_single_value("Healthcare Settings", field) not in (
                    None,
                    "",
                ):
                    return True
            return False
        except Exception:
            return True

    def _fields_readable(self, meta, fields):
        """True when every named DocField is readable for the current user.

        Mirrors the verified v16 API: ``get_permlevel_access("read")`` for the
        field's permlevel and ``get_masked_fields()`` for masking. A missing
        field definition or an unreadable mask list fails closed.
        """

        try:
            user = self.frappe.session.user
            accessible = set(meta.get_permlevel_access("read", user=user))
            masked = {
                getattr(masked, "fieldname", None)
                for masked in (meta.get_masked_fields() or ())
            }
            for field in fields:
                if field in masked:
                    return False
                definition = meta.get_field(field)
                if definition is None or definition.permlevel not in accessible:
                    return False
            return True
        except Exception:
            return False

    def reset_native_read_snapshot(self):
        """Restart the native transaction so later locks read current rows."""

        self.frappe.db.rollback()

    def native_appointment_boundary_problem(self):
        """Return pinned-boundary drift, or ``None`` when fully reviewed.

        Tests supply ``frappe.appointment_boundary_snapshot``; production
        collects versions, controller hashes, hooks, overrides, server scripts
        and effective DocFields. Any failure is a non-empty problem so callers
        fail closed.
        """

        try:
            snapshot = getattr(
                self.frappe, "appointment_boundary_snapshot", None
            )
            if snapshot is None:
                snapshot = self._collect_appointment_boundary_snapshot()
            return appointment_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_appointment_boundary_snapshot(self):
        """Collect the executable and effective-metadata boundary from Frappe."""

        import hashlib
        import importlib

        frappe = self.frappe

        def version(module_name, module=None):
            if module is not None:
                return getattr(module, "__version__", None)
            return getattr(importlib.import_module(module_name), "__version__", None)

        def source_hash(*parts):
            path = frappe.get_app_path("healthcare", *parts)
            with open(path, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()

        hooks = frappe.get_hooks() or {}
        doc_events = hooks.get("doc_events") or {}
        wildcard = doc_events.get("*") or {}
        overrides = hooks.get("override_doctype_class") or {}
        whitelisted = hooks.get("override_whitelisted_methods") or {}
        scheduler = hooks.get("scheduler_events") or {}
        server_scripts = frappe.get_all(
            "Server Script", filters={"disabled": 0}, limit=1, pluck="name"
        ) or ()

        def matching(doctype, filters):
            return frappe.get_all(
                doctype, filters=filters, limit=1, pluck="name"
            ) or ()

        controller = frappe.get_attr(
            appointment_contract.APPOINTMENT_CLASS_PATH
        )
        extend = hooks.get("extend_doctype_class") or {}
        meta = frappe.get_meta(appointment_contract.APPOINTMENT_DOCTYPE)
        behavior = {}
        for name in appointment_contract.APPOINTMENT_DOCTYPE_BEHAVIOR:
            value = meta.get(name)
            behavior[name] = "" if value is None else value
        return {
            "versions": {
                "frappe": version("frappe", frappe),
                "erpnext": version("erpnext"),
                "healthcare": version("healthcare"),
            },
            "source_sha256": {
                "patient_appointment": source_hash(
                    "healthcare",
                    "doctype",
                    "patient_appointment",
                    "patient_appointment.py",
                ),
                "fee_validity": source_hash(
                    "healthcare", "doctype", "fee_validity", "fee_validity.py"
                ),
            },
            "controller_mro": tuple(
                f"{item.__module__}.{item.__name__}" for item in controller.__mro__
            ),
            "extend_doctype_class": tuple(
                extend.get(appointment_contract.APPOINTMENT_DOCTYPE) or ()
            ),
            "appointment_behavior": behavior,
            "enabled_notifications": len(
                matching(
                    "Notification",
                    {
                        "enabled": 1,
                        "document_type": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            "enabled_webhooks": len(
                matching(
                    "Webhook",
                    {
                        "enabled": 1,
                        "webhook_doctype": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            "active_workflows": len(
                matching(
                    "Workflow",
                    {
                        "is_active": 1,
                        "document_type": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            "active_assignment_rules": len(
                matching(
                    "Assignment Rule",
                    {
                        "disabled": 0,
                        "document_type": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            "enabled_automations": len(
                matching(
                    "Automation Flow",
                    {
                        "enabled": 1,
                        "document_type": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            # Milestone Tracker has no `enabled` column: `disabled = 0` is the
            # live filter the wildcard on_change evaluator itself uses.
            "enabled_milestone_trackers": len(
                matching(
                    "Milestone Tracker",
                    {
                        "disabled": 0,
                        "document_type": appointment_contract.APPOINTMENT_DOCTYPE,
                    },
                )
            ),
            "patient_appointment_class": getattr(
                controller, "__module__", None
            ),
            "checkin_module": getattr(
                frappe.get_attr(
                    f"{appointment_contract.CHECKIN_MODULE}."
                    f"{appointment_contract.CHECKIN_NAME}"
                ),
                "__module__",
                None,
            ),
            "checkin_name": getattr(
                frappe.get_attr(
                    f"{appointment_contract.CHECKIN_MODULE}."
                    f"{appointment_contract.CHECKIN_NAME}"
                ),
                "__name__",
                None,
            ),
            "cancel_module": getattr(
                frappe.get_attr(
                    f"{appointment_contract.CANCEL_MODULE}."
                    f"{appointment_contract.CANCEL_NAME}"
                ),
                "__module__",
                None,
            ),
            "cancel_name": getattr(
                frappe.get_attr(
                    f"{appointment_contract.CANCEL_MODULE}."
                    f"{appointment_contract.CANCEL_NAME}"
                ),
                "__name__",
                None,
            ),
            "doc_events_patient_appointment": tuple(
                doc_events.get(appointment_contract.APPOINTMENT_DOCTYPE) or ()
            ),
            "wildcard_before_insert": tuple(
                wildcard.get("before_insert") or ()
            ),
            "wildcard_before_validate": tuple(
                wildcard.get("before_validate") or ()
            ),
            "wildcard_before_save": tuple(wildcard.get("before_save") or ()),
            "wildcard_after_insert": tuple(
                wildcard.get("after_insert") or ()
            ),
            "wildcard_on_update": tuple(wildcard.get("on_update") or ()),
            "wildcard_validate": tuple(wildcard.get("validate") or ()),
            "wildcard_on_change": tuple(wildcard.get("on_change") or ()),
            "override_doctype_class": tuple(
                overrides.get(appointment_contract.APPOINTMENT_DOCTYPE) or ()
            ),
            "checkin_whitelisted_override": tuple(
                whitelisted.get(
                    f"{appointment_contract.CHECKIN_MODULE}."
                    f"{appointment_contract.CHECKIN_NAME}"
                )
                or ()
            ),
            "cancel_whitelisted_override": tuple(
                whitelisted.get(
                    f"{appointment_contract.CANCEL_MODULE}."
                    f"{appointment_contract.CANCEL_NAME}"
                )
                or ()
            ),
            "scheduler_all": tuple(scheduler.get("all") or ()),
            "scheduler_daily": tuple(scheduler.get("daily") or ()),
            "enabled_server_scripts": len(server_scripts),
            "appointment_metadata": appointment_contract.normalize_meta_fields(
                frappe.get_meta(appointment_contract.APPOINTMENT_DOCTYPE).fields
            ),
            "patient_metadata": {
                name: value
                for name, value in appointment_contract.normalize_meta_fields(
                    frappe.get_meta(PATIENT_DOCTYPE).fields
                ).items()
                if name in appointment_contract.PATIENT_SOURCE_FIELDS
            },
            "appointment_type_metadata": {
                name: value
                for name, value in appointment_contract.normalize_meta_fields(
                    frappe.get_meta("Appointment Type").fields
                ).items()
                if name
                in appointment_contract.APPOINTMENT_TYPE_SOURCE_FIELDS
            },
        }

    def native_appointment_type_contract(self, appointment_type):
        """Return the reviewed Appointment Type mode and duration, or ``None``."""

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                "Appointment Type", ptype="read", doc=appointment_type, user=user
            ):
                return None
            meta = frappe.get_meta("Appointment Type")
            if not self._fields_readable(
                meta, appointment_contract.APPOINTMENT_TYPE_SOURCE_FIELDS
            ):
                return None
            row = frappe.db.get_value(
                "Appointment Type",
                appointment_type,
                list(appointment_contract.APPOINTMENT_TYPE_SOURCE_FIELDS),
                as_dict=True,
                cache=False,
            )
        except Exception:
            return None
        if not row:
            return None
        duration = self._row_value(row, "default_duration")
        mode = self._text_value(self._row_value(row, "allow_booking_for"))
        if isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0:
            return None
        return {
            "appointment_type": self._text_value(
                self._row_value(row, "appointment_type")
            ),
            "default_duration": duration,
            "allow_booking_for": mode,
        }

    def native_appointment_type_duration(self, appointment_type):
        """Reviewed positive default duration, or ``None``."""

        contract = self.native_appointment_type_contract(appointment_type)
        return None if contract is None else contract["default_duration"]

    def _appointment_source_row(self, patient, appointment_type, *, for_update):
        """Read and normalize the locked Patient and Appointment Type sources."""

        frappe = self.frappe
        user = frappe.session.user
        if not frappe.has_permission(
            PATIENT_DOCTYPE, ptype="read", doc=patient, user=user
        ):
            return None
        if not frappe.has_permission(
            "Appointment Type", ptype="read", doc=appointment_type, user=user
        ):
            return None
        if not self._fields_readable(
            frappe.get_meta(PATIENT_DOCTYPE),
            appointment_contract.PATIENT_SOURCE_FIELDS,
        ):
            return None
        if not self._fields_readable(
            frappe.get_meta("Appointment Type"),
            appointment_contract.APPOINTMENT_TYPE_SOURCE_FIELDS,
        ):
            return None
        # Deterministic dependency lock order: Patient, then Appointment Type.
        patient_row = frappe.db.get_value(
            PATIENT_DOCTYPE,
            patient,
            list(appointment_contract.PATIENT_SOURCE_FIELDS),
            as_dict=True,
            cache=False,
            for_update=for_update,
        )
        type_row = frappe.db.get_value(
            "Appointment Type",
            appointment_type,
            list(appointment_contract.APPOINTMENT_TYPE_SOURCE_FIELDS),
            as_dict=True,
            cache=False,
            for_update=for_update,
        )
        if not patient_row or not type_row:
            return None
        sex = self._text_value(self._row_value(patient_row, "sex"))
        if sex and not frappe.has_permission(
            "Gender", ptype="read", doc=sex, user=user
        ):
            return None
        return {
            "patient_name": self._text_value(
                self._row_value(patient_row, "patient_name")
            ),
            "patient_sex": sex,
            "inpatient_record": self._text_value(
                self._row_value(patient_row, "inpatient_record")
            ),
            "emergency_record": self._text_value(
                self._row_value(patient_row, "emergency_record")
            ),
            "duration": self._row_value(type_row, "default_duration"),
            "allow_booking_for": self._text_value(
                self._row_value(type_row, "allow_booking_for")
            ),
        }

    def native_appointment_sources(self, patient, appointment_type, *, lock=True):
        """Lock and verify the exact Patient/Appointment Type fetch sources."""

        try:
            sources = self._appointment_source_row(
                patient, appointment_type, for_update=lock
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        if sources is None:
            raise patient_write.WriteUncertain()
        problem = appointment_contract.source_problem(sources)
        if problem is not None:
            raise patient_write.UnsafeSideEffect(problem)
        return sources

    def prepare_native_appointment_create(self, fields, *, expected=None):
        """Build and vet one department booking before any insert."""

        frappe = self.frappe
        doc = frappe.new_doc(APPOINTMENT_DOCTYPE)
        doc.patient = fields["patient"]
        doc.company = fields["company"]
        doc.appointment_type = fields["appointment_type"]
        doc.department = fields["department"]
        doc.appointment_for = "Department"
        doc.duration = fields["duration"]
        doc.appointment_date = fields["appointment_date"]
        doc.appointment_time = fields["appointment_time"]
        self._require_prepared_appointment(doc, expected or fields)
        return doc

    @staticmethod
    def _require_prepared_appointment(doc, expected):
        problem = appointment_contract.prepared_problem(doc, expected)
        if problem is not None:
            raise patient_write.UnsafeSideEffect(problem)

    def insert_native_appointment(self, doc, *, expected=None, today=None):
        """Insert once with the exact frozen expectation checked first."""

        self._require_prepared_appointment(doc, expected or {})
        doc.insert()
        native_appointment_id = getattr(doc, "name", None)
        if (
            not isinstance(native_appointment_id, str)
            or not native_appointment_id.strip()
        ):
            raise patient_write.WriteUncertain()
        return native_appointment_id

    def verify_native_appointment_insert(
        self, native_appointment_id, expected, sources, today
    ):
        """Revalidate the inserted booking, allowing only reviewed deltas.

        ``expected`` must be the snapshot frozen before ``doc.insert()``; the
        prepared document itself is never used here because insert mutates it.
        """

        try:
            doc = self.frappe.get_doc(
                APPOINTMENT_DOCTYPE, native_appointment_id, for_update=True
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        if doc is None or getattr(doc, "name", None) != native_appointment_id:
            raise patient_write.WriteUncertain()
        frozen = {
            key: expected.get(key) for key in APPOINTMENT_EXPECTED_KEYS
        }
        frozen.update(sources or {})
        problem = appointment_contract.inserted_problem(doc, frozen, today=today)
        if problem is not None:
            raise patient_write.WriteUncertain()
        return doc

    def _appointment_target_row(self, native_appointment_id):
        frappe = self.frappe
        user = frappe.session.user
        if not frappe.has_permission(
            APPOINTMENT_DOCTYPE,
            ptype="write",
            doc=native_appointment_id,
            user=user,
        ):
            return None
        meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
        if not self._fields_readable(meta, APPOINTMENT_CONTRACT_FIELDS):
            return None
        try:
            doc = frappe.get_doc(APPOINTMENT_DOCTYPE, native_appointment_id)
        except Exception:
            return None
        return doc if doc is not None else None

    def native_appointment_target_problem(
        self, native_appointment_id, *, expected=None
    ):
        """Non-mutating closed-world target check used by detail action flags."""

        try:
            row = self._appointment_target_row(native_appointment_id)
        except Exception:
            return "target unreadable"
        if row is None:
            return "target unreadable"
        problem = appointment_contract.target_problem(row)
        if problem is not None:
            return problem
        problem = appointment_contract.identity_config_problem(row, expected)
        if problem is not None:
            return problem
        try:
            self._require_row_links_readable(row)
        except Exception:
            return "target links unreadable"
        try:
            sources = self.native_appointment_sources(
                self._text_value(self._row_value(row, "patient")),
                self._text_value(self._row_value(row, "appointment_type")),
                lock=False,
            )
        except Exception:
            return "target sources unreadable"
        if (
            self._text_value(self._row_value(row, "patient_name"))
            != sources["patient_name"]
            or self._text_value(self._row_value(row, "patient_sex"))
            != sources["patient_sex"]
            or self._row_value(row, "duration") != sources["duration"]
            or self._text_value(self._row_value(row, "appointment_for"))
            != sources["allow_booking_for"]
        ):
            return "target fetch values drifted"
        return None

    def native_appointment_modified(self, native_appointment_id):
        """Current authoritative version under native write authority."""

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                APPOINTMENT_DOCTYPE,
                ptype="write",
                doc=native_appointment_id,
                user=user,
            ):
                return None
            return self._text_value(
                frappe.db.get_value(
                    APPOINTMENT_DOCTYPE,
                    native_appointment_id,
                    "modified",
                    cache=False,
                )
            )
        except Exception:
            return None

    @staticmethod
    def _row_value(row, field):
        if hasattr(row, "get"):
            return row.get(field)
        return getattr(row, field, None)

    def _require_row_links_readable(self, row):
        """Require read authority for every populated reviewed link value."""

        frappe = self.frappe
        user = frappe.session.user
        meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
        for field in APPOINTMENT_CONTRACT_FIELDS:
            value = self._row_value(row, field)
            if not value:
                continue
            definition = meta.get_field(field)
            if definition is None:
                raise frappe.PermissionError(DENIAL_MESSAGE)
            if definition.fieldtype == "Dynamic Link":
                raise frappe.PermissionError(DENIAL_MESSAGE)
            if definition.fieldtype != "Link":
                continue
            if not frappe.has_permission(
                definition.options, ptype="read", doc=value, user=user
            ):
                raise frappe.PermissionError(DENIAL_MESSAGE)
        patient = self._row_value(row, "patient")
        if not self.native_patient_can_read(patient):
            raise frappe.PermissionError(DENIAL_MESSAGE)

    def _require_appointment_links_readable(self, doc):
        self._require_row_links_readable(doc)

    def lock_appointment_target(
        self, native_appointment_id, *, base_modified=None, expected=None
    ):
        """Lock and freshly re-verify one writable target before any save.

        Order: the SQLite fence is already held, then this method restarts the
        native transaction snapshot, locks the appointment row with
        ``FOR UPDATE``, checks the pinned boundary, closed-world fields, links
        and settings, then locks the Patient and Appointment Type rows in that
        deterministic order and verifies the exact fetched values. Every value
        used after the lock is the currently committed value.

        ``base_modified`` is the client's optimistic-concurrency token for the
        reschedule and check-in paths. Cancel and manual no-show have no client
        version input (their body names a target and a reason only), so they
        pass ``None``: every decision they make is re-read under this lock and
        the ``notes`` preimage is taken from the locked row, so there is no
        stale value to overwrite.
        """

        frappe = self.frappe
        user = frappe.session.user
        if self.native_appointment_boundary_problem() is not None:
            raise patient_write.WriteUncertain()
        # No native write has happened yet; restarting the transaction removes
        # any stale consistent-read snapshot taken by earlier request checks.
        frappe.db.rollback()

        doc = frappe.get_doc(APPOINTMENT_DOCTYPE, native_appointment_id, for_update=True)
        if doc is None:
            raise patient_write.WriteUncertain()
        if not frappe.has_permission(
            APPOINTMENT_DOCTYPE, ptype="write", doc=native_appointment_id, user=user
        ):
            raise frappe.PermissionError(DENIAL_MESSAGE)

        meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
        if not self._fields_readable(meta, APPOINTMENT_CONTRACT_FIELDS):
            raise frappe.PermissionError(DENIAL_MESSAGE)
        problem = appointment_contract.target_problem(doc)
        if problem is not None:
            raise patient_write.UnsafeSideEffect(problem)
        problem = appointment_contract.identity_config_problem(doc, expected)
        if problem is not None:
            raise patient_write.UnsafeSideEffect(problem)
        self._require_row_links_readable(doc)
        if self.healthcare_appointment_side_effects_unsafe() is not False:
            raise patient_write.UnsafeSideEffect("appointment settings are unsafe")

        sources = self.native_appointment_sources(
            self._text_value(getattr(doc, "patient", None)),
            self._text_value(getattr(doc, "appointment_type", None)),
            lock=True,
        )
        for field, source_field in (
            ("patient_name", "patient_name"),
            ("patient_sex", "patient_sex"),
        ):
            if self._text_value(getattr(doc, field, None)) != sources[source_field]:
                raise patient_write.UnsafeSideEffect(f"{field} fetch mismatch")
        if getattr(doc, "duration", None) != sources["duration"]:
            raise patient_write.UnsafeSideEffect("duration fetch mismatch")
        if self._text_value(getattr(doc, "appointment_for", None)) != sources[
            "allow_booking_for"
        ]:
            raise patient_write.UnsafeSideEffect("appointment_for fetch mismatch")

        if (
            base_modified is not None
            and not patient_write.modified_matches(
                self._text_value(getattr(doc, "modified", None)), base_modified
            )
        ):
            raise patient_write.StaleVersion()

        return AppointmentLockedState(
            doc=doc,
            patient=self._text_value(getattr(doc, "patient", None)),
            company=self._text_value(getattr(doc, "company", None)),
            department=self._text_value(getattr(doc, "department", None)),
            appointment_type=self._text_value(
                getattr(doc, "appointment_type", None)
            ),
            appointment_for=self._text_value(
                getattr(doc, "appointment_for", None)
            ),
            duration=getattr(doc, "duration", None),
            status=self._text_value(getattr(doc, "status", None)),
            appointment_date=self._text_value(
                getattr(doc, "appointment_date", None)
            ),
            appointment_time=self._time_value(
                getattr(doc, "appointment_time", None)
            ),
            modified=self._text_value(getattr(doc, "modified", None)),
            patient_name=self._text_value(
                getattr(doc, "patient_name", None)
            ),
            patient_sex=self._text_value(getattr(doc, "patient_sex", None)),
            notes=self._text_value(getattr(doc, "notes", None)),
            practitioner=self._text_value(getattr(doc, "practitioner", None)),
            event=self._text_value(getattr(doc, "event", None)),
            invoiced=getattr(doc, "invoiced", None),
            ref_sales_invoice=self._text_value(
                getattr(doc, "ref_sales_invoice", None)
            ),
            paid_amount=getattr(doc, "paid_amount", None),
            insurance_coverage=self._text_value(
                getattr(doc, "insurance_coverage", None)
            ),
            service_request=self._text_value(
                getattr(doc, "service_request", None)
            ),
            mode_of_payment=self._text_value(
                getattr(doc, "mode_of_payment", None)
            ),
        )

    def save_appointment_reschedule(self, state, fields, today=None):
        """Apply date/time to the locked document and verify the controller result.

        ``today`` is read after the appointment row lock by the caller so a
        midnight boundary cannot be crossed between preflight and save. The
        controller derives status/datetimes/title; only those reviewed deltas
        are accepted.
        """

        doc = state.doc
        if state.status not in APPOINTMENT_RESCHEDULE_STATUSES:
            raise appointment_write.AppointmentTargetNotEligible()

        doc.appointment_date = fields["appointment_date"]
        doc.appointment_time = fields["appointment_time"]
        doc.save()

        if today is None:
            today = self.site_today()
        if today is None:
            raise patient_write.WriteUncertain()
        saved_date = self._text_value(getattr(doc, "appointment_date", None))
        saved_time = self._time_value(getattr(doc, "appointment_time", None))
        saved_status = self._text_value(getattr(doc, "status", None))
        if saved_date != fields["appointment_date"] or saved_time != fields[
            "appointment_time"
        ]:
            raise patient_write.WriteUncertain()
        if saved_status not in APPOINTMENT_RESCHEDULE_STATUSES:
            raise patient_write.WriteUncertain()
        if saved_date > today and saved_status not in ("Scheduled", "Confirmed"):
            raise patient_write.WriteUncertain()
        if saved_date == today and saved_status not in ("Open", "Confirmed"):
            raise patient_write.WriteUncertain()
        problem = appointment_contract.target_problem(doc)
        if problem is not None:
            raise patient_write.WriteUncertain()
        derived = appointment_contract.expected_derived(
            {
                "patient": state.patient,
                "department": state.department,
                "patient_name": state.patient_name,
                "duration": state.duration,
                "appointment_date": fields["appointment_date"],
                "appointment_time": fields["appointment_time"],
            },
            today,
        )
        if derived is None:
            raise patient_write.WriteUncertain()
        for field in (
            "appointment_datetime",
            "appointment_end_datetime",
            "title",
        ):
            # The controller may leave a Datetime as a typed value in memory;
            # compare it in the same site format the expectation builder emits.
            value = appointment_contract.canonical_value(
                field, getattr(doc, field, None)
            )
            if value != derived[field]:
                raise patient_write.WriteUncertain()
        for field, expected in (
            ("patient_name", state.patient_name),
            ("patient_sex", state.patient_sex),
        ):
            if self._text_value(getattr(doc, field, None)) != expected:
                raise patient_write.WriteUncertain()
        for field, expected in (
            ("patient", state.patient),
            ("company", state.company),
            ("department", state.department),
            ("appointment_type", state.appointment_type),
            ("appointment_for", state.appointment_for),
        ):
            if self._text_value(getattr(doc, field, None)) != expected:
                raise patient_write.WriteUncertain()
        if getattr(doc, "duration", None) != state.duration:
            raise patient_write.WriteUncertain()

    @staticmethod
    def _native_check_in_function():
        """Import the pinned module-level check-in entry point or fail closed."""

        try:
            module = __import__(
                NATIVE_CHECKIN_MODULE, fromlist=[NATIVE_CHECKIN_NAME]
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        function = getattr(module, NATIVE_CHECKIN_NAME, None)
        if not callable(function):
            raise patient_write.WriteUncertain()
        return function

    def native_appointment_checkin(self, state, today):
        """Invoke the pinned native check-in inside the locked transaction.

        The real module-level ``check_in_appointment`` is called by name; no
        document method is assumed. Immediately before the call the locked
        status and the site's current date are revalidated. The resulting saved
        status is read back with a locking read.
        """

        if today is None:
            raise patient_write.WriteUncertain()
        if state.status not in APPOINTMENT_CHECKIN_STATUSES:
            raise appointment_write.AppointmentTargetNotEligible()
        if state.appointment_date != today:
            raise appointment_write.AppointmentTargetNotEligible()

        check_in = self._native_check_in_function()
        try:
            check_in(state.doc.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

        try:
            doc = self.frappe.get_doc(
                APPOINTMENT_DOCTYPE, state.doc.name, for_update=True
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        if doc is None:
            raise patient_write.WriteUncertain()
        if self._text_value(getattr(doc, "status", None)) != (
            APPOINTMENT_CHECKED_IN_STATUS
        ):
            raise patient_write.WriteUncertain()
        if appointment_contract.target_problem(doc) is not None:
            raise patient_write.WriteUncertain()
        for field, expected in (
            ("patient", state.patient),
            ("company", state.company),
            ("department", state.department),
            ("appointment_type", state.appointment_type),
            ("appointment_for", state.appointment_for),
            ("appointment_date", today),
            ("appointment_time", state.appointment_time),
            ("patient_name", state.patient_name),
            ("patient_sex", state.patient_sex),
        ):
            value = self._time_value(getattr(doc, field, None)) if (
                field == "appointment_time"
            ) else self._text_value(getattr(doc, field, None))
            if value != expected:
                raise patient_write.WriteUncertain()
        if getattr(doc, "duration", None) != state.duration:
            raise patient_write.WriteUncertain()
        position = getattr(doc, "position_in_queue", None)
        if isinstance(position, bool) or not isinstance(position, int) or position < 1:
            raise patient_write.WriteUncertain()

    def native_appointment_action_detail(self, native_appointment_id):
        """Read the cancel/no-show decision fields under the same read checks.

        The list fields come from :meth:`native_appointment_view` (document
        read, permlevel and masking checks). The remaining decision fields are
        reviewed permlevel-0 DocFields, checked with the same helper before any
        value is fetched. ``None`` fails the caller closed.
        """

        detail = self.native_appointment_view(native_appointment_id)
        if detail is None:
            return None
        try:
            frappe = self.frappe
            meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
            if not self._fields_readable(meta, APPOINTMENT_ACTION_FIELDS):
                return None
            for field in APPOINTMENT_ACTION_TEXT_FIELDS:
                detail[field] = self._text_value(
                    frappe.db.get_value(
                        APPOINTMENT_DOCTYPE, native_appointment_id, field
                    )
                )
            for field in APPOINTMENT_ACTION_RAW_FIELDS:
                detail[field] = frappe.db.get_value(
                    APPOINTMENT_DOCTYPE, native_appointment_id, field
                )
        except Exception:
            return None
        return detail

    @staticmethod
    def _native_cancel_function():
        """Import the pinned module-level ``cancel_appointment`` or fail closed."""

        try:
            module = __import__(
                appointment_contract.CANCEL_MODULE,
                fromlist=[appointment_contract.CANCEL_NAME],
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        function = getattr(module, appointment_contract.CANCEL_NAME, None)
        if not callable(function):
            raise patient_write.WriteUncertain()
        return function

    def _site_now(self):
        """Return the site timestamp used verbatim in the appended note line."""

        try:
            value = self.frappe.utils.now()
        except Exception:
            raise patient_write.WriteUncertain() from None
        if not isinstance(value, str) or NOTE_TIMESTAMP.fullmatch(value) is None:
            raise patient_write.WriteUncertain()
        return value

    def _append_note(self, state, kind, reason, actor):
        """Return the contract-pinned ``notes`` postimage for one transition."""

        line = appointment_contract.note_line(
            kind, actor=actor, reason=reason, recorded_at=self._site_now()
        )
        return appointment_contract.note_postimage(state.notes, line)

    def _verify_transition(self, state, postimage, postcondition):
        """Re-read the saved row and assert the closed-world post-conditions."""

        try:
            doc = self.frappe.get_doc(
                APPOINTMENT_DOCTYPE, state.doc.name, for_update=True
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        if doc is None:
            raise patient_write.WriteUncertain()
        if postcondition(doc, postimage) is not None:
            raise patient_write.WriteUncertain()
        if appointment_contract.transition_problem(doc) is not None:
            raise patient_write.WriteUncertain()
        for field, expected in (
            ("patient", state.patient),
            ("company", state.company),
            ("department", state.department),
            ("appointment_type", state.appointment_type),
            ("appointment_for", state.appointment_for),
            ("appointment_date", state.appointment_date),
            ("appointment_time", state.appointment_time),
            ("patient_name", state.patient_name),
            ("patient_sex", state.patient_sex),
        ):
            value = (
                self._time_value(getattr(doc, field, None))
                if field == "appointment_time"
                else self._text_value(getattr(doc, field, None))
            )
            if value != expected:
                raise patient_write.WriteUncertain()
        if getattr(doc, "duration", None) != state.duration:
            raise patient_write.WriteUncertain()
        return doc

    def cancel_native_appointment(self, state, reason, actor):
        """Drive the pinned cancel transition inside the locked transaction.

        The reviewed rule (design ruling 4) is: call the installed
        ``cancel_appointment(appointment_id)`` handler, then write the terminal
        ``Cancelled`` status and the reason line through the controller's own
        ``save()``, whose ``set_status`` preserves terminal values. The returned
        state is read back: status, verbatim note and every reviewed identity
        field unchanged. The fee-validity and calendar branches are attested
        from that state, not deep-verified; the linked-record branches are
        refused pre-fence by the contract.
        """

        problem = appointment_contract.cancel_target_problem(state)
        if problem is not None or appointment_contract.transition_problem(state.doc) is not None:
            raise appointment_write.AppointmentTargetNotEligible()

        postimage = self._append_note(
            state, appointment_contract.CANCEL_KIND, reason, actor
        )
        cancel = self._native_cancel_function()
        try:
            cancel(state.doc.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

        state.doc.status = appointment_contract.CANCELLED_STATUS
        state.doc.notes = postimage
        try:
            state.doc.save()
        except Exception:
            raise patient_write.WriteUncertain() from None

        doc = self._verify_transition(
            state, postimage, appointment_contract.cancel_postcondition_problem
        )

        # The pinned handler cancels the linked calendar Event only on the
        # practitioner branch; on that branch the Event status is verified.
        if appointment_contract.cancel_event_expected(state) and state.event:
            try:
                event_status = self.frappe.db.get_value(
                    NOTE_EVENT_DOCTYPE, state.event, "status"
                )
            except Exception:
                raise patient_write.WriteUncertain() from None
            if event_status != appointment_contract.CANCELLED_STATUS:
                raise patient_write.WriteUncertain()
        return doc

    def no_show_native_appointment(self, state, reason, actor):
        """Write the installed terminal ``No Show`` status and the reason line.

        The installed ``set_status`` treats ``No Show`` as terminal and tolerates
        it on save (patient_appointment.py:133-150), so this is the designed
        transition rather than a bare status write: the controller's own
        validate/on_update path runs, and the returned state is checked for the
        exact status, the verbatim note and every reviewed identity field.
        """

        problem = appointment_contract.no_show_target_problem(state)
        if problem is not None or appointment_contract.transition_problem(state.doc) is not None:
            raise appointment_write.AppointmentTargetNotEligible()

        postimage = self._append_note(
            state, appointment_contract.NO_SHOW_KIND, reason, actor
        )
        state.doc.status = appointment_contract.NO_SHOW_STATUS
        state.doc.notes = postimage
        try:
            state.doc.save()
        except Exception:
            raise patient_write.WriteUncertain() from None

        return self._verify_transition(
            state, postimage, appointment_contract.no_show_postcondition_problem
        )

    def native_patient_view(self, native_patient_id):
        """Allowlisted detail fields under document and field-level read rights.

        Both checks are verified Frappe v16 APIs:

        - ``frappe.has_permission(doctype, "read", doc=name, user=user)`` for the
          document (``frappe/permissions.py:81``).
        - ``frappe.get_meta(doctype).get_permlevel_access("read", user=user)``
          for the readable permlevels (``frappe/model/meta.py:745-753``), then
          each field's ``permlevel``.
        - ``frappe.get_meta(doctype).get_masked_fields()`` for the current
          user's masked fields (``frappe/model/meta.py:195-214``). Masking is a
          separate permission axis from permlevel: a masked field is refused
          even when its permlevel is readable.

        A field above the accessible level, a masked allowed field, an
        unresolvable mask list or a missing record keeps the whole read closed,
        and no field is fetched before every check passes.
        """

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                PATIENT_DOCTYPE, ptype="read", doc=native_patient_id, user=user
            ):
                return None
            meta = frappe.get_meta(PATIENT_DOCTYPE)
            accessible_permlevels = meta.get_permlevel_access("read", user=user)
            for field in PATIENT_DOCFIELD_FIELDS:
                field_definition = meta.get_field(field)
                if (
                    field_definition is None
                    or field_definition.permlevel not in accessible_permlevels
                ):
                    return None
            masked_fields = meta.get_masked_fields() or ()
            masked_names = {
                masked.fieldname
                for masked in masked_fields
                if getattr(masked, "fieldname", None)
            }
            if any(field in masked_names for field in PATIENT_DOCFIELD_FIELDS):
                return None
            # ``modified`` is a standard column with no DocField metadata. It is
            # safe here because the document-level read permission above already
            # succeeded; do not call ``get_field`` for it.
            detail = {}
            for field in PATIENT_VIEW_FIELDS:
                detail[field] = self._text_value(
                    frappe.db.get_value(PATIENT_DOCTYPE, native_patient_id, field)
                )
        except Exception:
            return None
        return detail

    # -- local synthetic write path -------------------------------------

    def write_deployment_approved(self):
        """Enforceable write gate: exact approved site plus private marker.

        The marker must be private, sit outside ``site_config.json`` and be
        bound to the initialized fence store's ``deployment_id``. A copied
        config, matching hostname or a second site on the same bench cannot
        satisfy it. Any read or parse failure denies.
        """

        try:
            if self.site != config.APPROVED_WRITE_SITE:
                return False
            private = self.frappe.get_site_path("private")
            anchor_path = os.path.join(private, fence_store.ANCHOR_FILENAME)
            anchor = fence_store.read_anchor(anchor_path)
            return deployment_marker.read_marker(
                private, self.site, anchor.deployment_id
            )
        except Exception:
            return False

    def write_fence_ready(self):
        """True only when the fence store opens, verifies and is ready.

        This is the bootstrap capability check, not a write. It opens the
        anchored store and then runs the ledger's synchronized readiness check:
        no manual-investigation latch and no unresolved key or barrier. A
        missing, uninitialized, replaced or corrupt store, a held lock, a latch,
        a dispatched key, a committed-but-unacknowledged barrier, an unknown
        terminal or any read error returns ``False``.
        """

        try:
            return self._ledger().ready() is True
        except Exception:
            return False

    def healthcare_settings_side_effects_unsafe(self):
        """True when installed Healthcare Settings would cause unwanted effects.

        Frappe healthcare v16 ``Patient.after_insert`` disables the patient when
        ``collect_registration_fee`` is set and otherwise calls
        ``send_registration_sms``; that helper sends only when
        ``send_registration_msg`` is set. ``Patient.on_update`` creates or
        updates a Customer when ``link_customer_to_patient`` is set. All three
        are unsafe for this synthetic local path, so any truthy value fails
        closed. A read failure is also unsafe.
        """

        try:
            for field in (
                "collect_registration_fee",
                "send_registration_msg",
                "link_customer_to_patient",
            ):
                if self.frappe.db.get_single_value("Healthcare Settings", field):
                    return True
            return False
        except Exception:
            return True

    def native_patient_target_unsafe(self, native_patient_id):
        """True when the target record carries a contact/customer/user value.

        ``Patient.on_update`` can update a Customer, a Contact, or create a
        Website User from these fields. This path must never trigger those, so
        any populated value fails closed, as does a missing row or read error.
        """

        try:
            row = self.frappe.db.get_value(
                PATIENT_DOCTYPE,
                native_patient_id,
                ["invite_user", "email", "mobile", "phone", "customer", "user_id"],
                as_dict=True,
            )
            if row is None:
                return True
            for field in (
                "invite_user",
                "email",
                "mobile",
                "phone",
                "customer",
                "user_id",
            ):
                value = row.get(field) if hasattr(row, "get") else getattr(
                    row, field, None
                )
                if value:
                    return True
            return False
        except Exception:
            return True

    def respond_write(self, status, payload):
        """Return a write payload with an explicit non-200 status when needed.

        The error sanitizer replaces every clinic error body with a fixed code.
        For a server-set 409, record the safe write code in a request-local
        flag so the sanitizer can preserve ``WRITE_LOCKED`` /
        ``OPERATION_CONFLICT`` / ``STALE_VERSION`` without trusting the body.
        """

        if status != 200:
            try:
                self.frappe.local.response["http_status_code"] = status
            except Exception:
                pass
        if (
            status == 409
            and isinstance(payload, dict)
            and payload.get("code") in WRITE_ERROR_CODES
        ):
            try:
                self.frappe.local.flags.clinic_ui_write_error_code = payload["code"]
            except Exception:
                pass
        return payload

    def strip_message_channels(self):
        """Remove Frappe message channels from an otherwise successful write.

        The native controller or a helper can queue ``frappe.msgprint`` /
        message-log entries even when the write succeeds. Those channels are
        serialized independently of traceback settings, so clear them before
        returning success. Failure to clear is swallowed: the read surfaces
        already suppress on error, and this must not become a new failure path.
        """

        try:
            local = self.frappe.local
            if hasattr(local, "message_log"):
                local.message_log = []
            response = getattr(local, "response", None)
            if isinstance(response, dict):
                for key in UNSAFE_V1_KEYS:
                    response.pop(key, None)
                response.pop("messages", None)
                response.pop("errors", None)
            flags = getattr(local, "flags", None)
            if flags is not None:
                flags.error_message = None
        except Exception:
            return

    def native_patient_can_create(self):
        """Role-level native create authority, checked before any fence access."""

        try:
            return bool(
                self.frappe.has_permission(
                    PATIENT_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_patient_can_write(self, native_patient_id=None):
        """Role-level or exact-document native write authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    PATIENT_DOCTYPE,
                    ptype="write",
                    doc=native_patient_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_gender_exists(self, sex):
        """True only for a live Gender readable by the current user."""

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission("Gender", ptype="read", user=user):
                return False
            return bool(frappe.db.exists("Gender", sex))
        except Exception:
            return False

    def native_patient_modified(self, native_patient_id):
        """Current authoritative modified value under native write authority."""

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                PATIENT_DOCTYPE, ptype="write", doc=native_patient_id, user=user
            ):
                return None
            return self._text_value(
                frappe.db.get_value(PATIENT_DOCTYPE, native_patient_id, "modified")
            )
        except Exception:
            return None

    def native_patient_create(self, fields):
        """Prepare, inspect and insert one Patient through the native controller."""

        doc = self.prepare_native_patient_create(fields)
        return self.insert_native_patient(doc)

    def prepare_native_patient_create(self, fields):
        """Build and vet a Patient before any insert or fence dispatch.

        Frappe applies field defaults when the document is constructed and the
        controller's ``before_insert`` adds selling/customer defaults. This
        method applies ``before_insert``, then rejects any populated
        Customer/Contact/Address/email/phone/user/invite trigger field. The
        returned document is only inserted after the endpoint's authorization,
        settings and target checks and the operation fence are in place;
        ``insert_native_patient`` re-applies defaults and re-checks immediately
        before the insert.

        No ``ignore_permissions`` is used anywhere. Only the strict fields plus
        server-owned ``invite_user = 0`` are set by this path.
        """

        frappe = self.frappe
        doc = frappe.new_doc(PATIENT_DOCTYPE)
        doc.first_name = fields["first_name"]
        if fields.get("last_name") is not None:
            doc.last_name = fields["last_name"]
        doc.sex = fields["sex"]
        if fields.get("dob") is not None:
            doc.dob = fields["dob"]
        doc.invite_user = 0

        before_insert = getattr(doc, "before_insert", None)
        if callable(before_insert):
            before_insert()
        self._require_clean_new_patient(doc)
        return doc

    @staticmethod
    def _require_clean_new_patient(doc):
        """Reject any default-injected side-effect trigger before insert."""

        for field in PATIENT_CREATE_TRIGGER_FIELDS:
            if getattr(doc, field, None):
                raise patient_write.UnsafeSideEffect(field)
        if getattr(doc, "invite_user", None):
            raise patient_write.UnsafeSideEffect("invite_user")

    def insert_native_patient(self, doc):
        """Re-apply defaults, re-vet, then insert the Patient once."""

        before_insert = getattr(doc, "before_insert", None)
        if callable(before_insert):
            before_insert()
        self._require_clean_new_patient(doc)
        doc.insert()
        native_patient_id = getattr(doc, "name", None)
        if not isinstance(native_patient_id, str) or not native_patient_id.strip():
            raise patient_write.WriteUncertain()
        return native_patient_id

    def native_patient_update(self, native_patient_id, fields, base_modified):
        """Optimistic-concurrency update through the native controller.

        The Patient row is locked and its ``modified`` value is compared to
        ``base_modified`` before any field is applied. ``doc.save()`` is called
        without ``ignore_permissions``.
        """

        frappe = self.frappe
        user = frappe.session.user
        if not frappe.has_permission(
            PATIENT_DOCTYPE, ptype="write", doc=native_patient_id, user=user
        ):
            raise frappe.PermissionError(DENIAL_MESSAGE)

        rows = frappe.db.sql(
            "SELECT modified FROM `tabPatient` WHERE name=%s FOR UPDATE",
            (native_patient_id,),
            as_dict=True,
        )
        if not rows:
            raise patient_write.WriteUncertain()
        row = rows[0]
        current = row.get("modified") if hasattr(row, "get") else row["modified"]
        if not patient_write.modified_matches(current, base_modified):
            raise patient_write.StaleVersion()

        doc = frappe.get_doc(PATIENT_DOCTYPE, native_patient_id)
        if not frappe.has_permission(
            PATIENT_DOCTYPE, ptype="write", doc=native_patient_id, user=user
        ):
            raise frappe.PermissionError(DENIAL_MESSAGE)

        for field, value in fields.items():
            setattr(doc, field, value)
        doc.invite_user = 0
        doc.save()
        return native_patient_id

    def commit(self):
        """The one explicit site DB commit for a fenced native write."""

        self.frappe.db.commit()

    def rollback(self):
        """Discard an uncommitted failed native write before the request ends.

        Frappe commits unsafe requests at request end. A caught post-dispatch
        failure must not let that implicit commit persist a partial Patient,
        Customer, Contact or other side effect. The operation key still stays
        locked/uncertain; this only removes the partial transaction.
        """

        self.frappe.db.rollback()

    def _ledger(self):
        if self._write_ledger is not None:
            return self._write_ledger
        try:
            private = self.frappe.get_site_path("private")
            directory = os.path.join(private, fence_store.STORE_DIRNAME)
            anchor_path = os.path.join(private, fence_store.ANCHOR_FILENAME)
            self._write_ledger = fence_store.open_store(directory, anchor_path)
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except Exception as exc:
            raise patient_write.StoreUnavailable() from exc
        return self._write_ledger

    def lookup_create_receipt(self, actor, operation_key):
        """Return a committed create receipt, ``None``, or raise a lock state."""

        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind="create", operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def list_committed_create_receipts(self, limit, operation_key=None):
        """Read-only bounded enumeration of committed create receipts.

        The enumeration never claims, appends, acknowledges or latches. A
        missing, replaced or corrupt fence raises ``StoreUnavailable`` so the
        caller can keep the static directory; an inconsistent ledger raises
        ``WriteUncertain`` so a read fails closed instead of guessing.
        """

        ledger = self._ledger()
        try:
            return ledger.committed_create_receipts(
                limit=limit, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_write(self, secret, actor, kind, operation_key, ack_token):
        """Verify a success acknowledgement and resolve the write barrier.

        The token must bind this actor, kind, operation key and the durable
        receipt already stored for the committed operation. A mismatch or a
        non-committed operation returns ``invalid`` without changing anything.
        """

        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = patient_write.ack_token(
            secret,
            self.site,
            actor,
            kind,
            operation_key,
            record.receipt,
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def latch_manual_investigation(self, reason):
        """Durably block the whole deployment pending manual investigation."""

        return self._ledger().latch(reason)

    def latch_operation_uncertain(self, actor, kind, operation_key, reason):
        """Fallback latch: mark the exact operation key ``UNKNOWN`` durably."""

        return self._ledger().latch_operation_uncertain(
            actor=actor,
            kind=kind,
            operation_key=operation_key,
            reason=reason,
        )

    @contextmanager
    def patient_write_fence(self, actor, kind, operation_key, payload_digest):
        """Hold the persistent fence across the native transaction and commit."""

        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    # -- appointment fence, receipts and acknowledgement -----------------

    def lookup_appointment_receipt(self, actor, kind, operation_key):
        """Return a committed appointment receipt, ``None``, or raise a lock.

        Only the three appointment kinds are accepted; a patient kind is a
        caller error and fails closed as uncertain.
        """

        if kind not in write_fence.APPOINTMENT_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def list_committed_appointment_receipts(
        self, limit, kind=None, operation_key=None
    ):
        """Read-only bounded enumeration of committed appointment receipts."""

        ledger = self._ledger()
        try:
            return ledger.committed_appointment_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise appointment_write.AppointmentBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_appointment_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        """Verify an appointment acknowledgement and resolve the barrier.

        The token must bind this actor, site, appointment kind, operation key and
        the durable receipt already stored for the committed operation. A
        patient kind, a mismatch or a non-committed operation is ``invalid``
        without changing anything.
        """

        if kind not in write_fence.APPOINTMENT_OPERATION_KINDS:
            return "invalid"
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = appointment_write.ack_token(
            secret,
            self.site,
            actor,
            kind,
            operation_key,
            record.receipt,
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    @contextmanager
    def appointment_write_fence(
        self, actor, kind, operation_key, payload_digest
    ):
        """Hold the same site-wide fence for one appointment operation."""

        if kind not in write_fence.APPOINTMENT_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    # -- encounter write path --------------------------------------------

    def native_encounter_boundary_problem(self):
        """Return pinned-boundary drift for the encounter path, or ``None``.

        Tests supply ``frappe.encounter_boundary_snapshot``; production collects
        the pinned versions, the submittable flag, wildcard ``doc_events`` on the
        three written doctypes and the enabled Server Script count. Any failure
        is a non-empty problem so callers fail closed.
        """

        try:
            snapshot = getattr(self.frappe, "encounter_boundary_snapshot", None)
            if snapshot is None:
                snapshot = self._collect_encounter_boundary_snapshot()
            return encounter_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_encounter_boundary_snapshot(self):
        import importlib

        frappe = self.frappe

        def version(module_name, module=None):
            if module is not None:
                return getattr(module, "__version__", None)
            return getattr(importlib.import_module(module_name), "__version__", None)

        meta = frappe.get_meta(ENCOUNTER_DOCTYPE)
        hooks = frappe.get_hooks() or {}
        doc_events = hooks.get("doc_events") or {}
        server_scripts = frappe.get_all(
            "Server Script", filters={"disabled": 0}, limit=1, pluck="name"
        ) or ()
        return {
            "versions": {
                "frappe": version("frappe", frappe),
                "erpnext": version("erpnext"),
                "healthcare": version("healthcare"),
            },
            "encounter_submittable": 1 if meta.get("is_submittable") else 0,
            "enabled_server_scripts": len(server_scripts),
            "doc_events": {
                doctype: tuple(doc_events.get(doctype) or ())
                for doctype in (
                    ENCOUNTER_DOCTYPE,
                    CLINICAL_NOTE_DOCTYPE,
                    VITAL_SIGNS_DOCTYPE,
                )
            },
        }

    def _encounter_meta_fields(self):
        """Resolve the installed field names through the metadata cache."""

        cached = getattr(self, "_encounter_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        doctypes = set(encounter_contract.REQUIRED_FIELDS) | {VITAL_SIGNS_DOCTYPE}
        for doctype in doctypes:
            meta = get_meta(doctype)
            fields[doctype] = set(meta.get_valid_columns())
        self._encounter_fields_cache = fields
        return fields

    def native_encounter_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return encounter_contract.install_problem(self._encounter_meta_fields())
        except Exception:
            return "installed metadata unreadable"

    def _encounter_field(self, doctype, logical, candidates=None):
        return encounter_contract.resolve_field(
            self._encounter_meta_fields(), doctype, logical, candidates
        )

    def native_encounter_can_create(self):
        try:
            return bool(
                self.frappe.has_permission(
                    ENCOUNTER_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_encounter_can_write(self, native_encounter_id=None):
        try:
            return bool(
                self.frappe.has_permission(
                    ENCOUNTER_DOCTYPE,
                    ptype="write",
                    doc=native_encounter_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_clinical_note_can_create(self):
        try:
            return bool(
                self.frappe.has_permission(
                    CLINICAL_NOTE_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def healthcare_encounter_side_effects_unsafe(self):
        """True when Healthcare Settings would add effects to the cascade.

        The family gate checks exactly the enumerated keys: appointment
        confirmation/reminder messages, the payment popup, free follow-up
        validity (which an encounter submit can trigger) and a default calendar.
        A read failure is unsafe.
        """

        try:
            for field in encounter_contract.ENCOUNTER_UNSAFE_SETTINGS:
                if self.frappe.db.get_single_value("Healthcare Settings", field):
                    return True
            for field in encounter_contract.ENCOUNTER_UNSAFE_SETTING_LINKS:
                if self.frappe.db.get_single_value("Healthcare Settings", field):
                    return True
            return False
        except Exception:
            return True

    def native_submitted_service_request_count(self):
        """Baseline count used to prove the exactly-once cascade delta."""

        try:
            return int(
                self.frappe.db.count(SERVICE_REQUEST_DOCTYPE, {"docstatus": 1})
            )
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_encounter_cascade_detail(self, native_encounter_id, baseline):
        """Closed-world re-read after submit: statuses plus the SR delta."""

        try:
            doc = self.frappe.get_doc(
                ENCOUNTER_DOCTYPE, native_encounter_id, for_update=True
            )
            appointment_status = None
            appointment = self._text_value(getattr(doc, "appointment", None))
            if appointment:
                appointment_status = self._text_value(
                    self.frappe.db.get_value(
                        APPOINTMENT_DOCTYPE, appointment, "status"
                    )
                )
            submitted = int(
                self.frappe.db.count(SERVICE_REQUEST_DOCTYPE, {"docstatus": 1})
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        return {
            "docstatus": getattr(doc, "docstatus", None),
            "status": self._text_value(getattr(doc, "status", None)),
            "appointment_status": appointment_status,
            "submitted_service_requests": submitted - baseline,
        }

    def native_encounter_detail(self, native_encounter_id):
        """The success detail fields under the same read authority."""

        if self.native_encounter_can_write(native_encounter_id) is not True:
            return None
        try:
            row = self.frappe.db.get_value(
                ENCOUNTER_DOCTYPE,
                native_encounter_id,
                ["patient", "docstatus", "status", "modified"],
                as_dict=True,
            )
        except Exception:
            return None
        if row is None:
            return None
        get = row.get if hasattr(row, "get") else lambda key, default=None: getattr(
            row, key, default
        )
        return {
            "patient": self._text_value(get("patient")),
            "docstatus": get("docstatus"),
            "status": self._text_value(get("status")) or "Draft",
            "modified": self._text_value(get("modified")),
        }

    def _encounter_state_values(self, doc):
        """Build the contract detail from a locked encounter document."""

        native = doc.name
        appointment = self._text_value(getattr(doc, "appointment", None))
        appointment_status = None
        if appointment:
            appointment_status = self._text_value(
                self.frappe.db.get_value(APPOINTMENT_DOCTYPE, appointment, "status")
            )
        complaint_field = self._encounter_field("Complaint Detail", "complaint")
        complaint = False
        if complaint_field:
            complaint = bool(
                self.frappe.db.count(
                    "Complaint Detail",
                    {"parent": native, complaint_field: ("!=", "")},
                )
            )
        diagnosis_count = int(
            self.frappe.db.count(
                "Patient Encounter Diagnosis", {"parent": native}
            )
        )
        lab_count = int(
            self.frappe.db.count("Lab Prescription", {"parent": native})
        )
        procedure_count = int(
            self.frappe.db.count("Procedure Prescription", {"parent": native})
        )
        drug_code_field = self._encounter_field("Drug Prescription", "drug_code")
        medication_codes = ()
        if drug_code_field:
            medication_codes = tuple(
                self.frappe.get_all(
                    "Drug Prescription",
                    filters={"parent": native},
                    pluck=drug_code_field,
                )
                or ()
            )
        clinical_note = None
        clinical_note_modified = None
        assessment = plan = followup = None
        notes = self.frappe.get_all(
            CLINICAL_NOTE_DOCTYPE,
            filters={
                "reference_doctype": ENCOUNTER_DOCTYPE,
                "reference_docname": native,
            },
            fields=["name", "modified", "note"],
            order_by="creation desc",
            limit=1,
        ) or ()
        if notes:
            row = notes[0]
            note = row.get("note") if hasattr(row, "get") else None
            clinical_note = self._text_value(
                row.get("name") if hasattr(row, "get") else None
            )
            clinical_note_modified = self._text_value(
                str(row.get("modified")) if hasattr(row, "get") else None
            )
            sections = encounter_contract.parse_note_text(note)
            assessment = sections["assessment"] or None
            plan = sections["plan"] or None
            followup = sections["followup"] or None
        return {
            "patient": self._text_value(getattr(doc, "patient", None)),
            "docstatus": getattr(doc, "docstatus", None),
            "status": self._text_value(getattr(doc, "status", None)),
            "appointment": appointment,
            "appointment_status": appointment_status,
            "modified": self._text_value(getattr(doc, "modified", None)),
            "complaint": complaint,
            "diagnosis_count": diagnosis_count,
            "medication_codes": medication_codes,
            "lab_count": lab_count,
            "procedure_count": procedure_count,
            "clinical_note": clinical_note,
            "clinical_note_modified": clinical_note_modified,
            "assessment": assessment,
            "plan": plan,
            "followup": followup,
        }

    def lock_encounter_target(self, native_encounter_id, base_modified=None):
        """Lock the encounter row and read the contract decision fields."""

        try:
            doc = self.frappe.get_doc(
                ENCOUNTER_DOCTYPE, native_encounter_id, for_update=True
            )
            values = self._encounter_state_values(doc)
        except Exception:
            raise patient_write.WriteUncertain() from None
        return EncounterLockedState(doc=doc, **values)

    def native_encounter_action_detail(self, native_encounter_id):
        """Read the pre-fence decision fields under native authority."""

        if self.native_encounter_can_write(native_encounter_id) is not True:
            return None
        try:
            doc = self.frappe.get_doc(ENCOUNTER_DOCTYPE, native_encounter_id)
            return self._encounter_state_values(doc)
        except Exception:
            return None

    def _write_encounter_children(self, doc, payload):
        """Replace the reviewed child rows with the normalized payload."""

        fields = payload["fields"]
        complaint_field = self._encounter_field("Complaint Detail", "complaint")
        if fields.get("complaint") is not None and complaint_field:
            doc.set("symptoms", [])
            doc.append("symptoms", {complaint_field: fields["complaint"]})
        diagnosis_field = self._encounter_field(
            "Patient Encounter Diagnosis", "diagnosis"
        )
        if fields.get("diagnoses") and diagnosis_field:
            doc.set("diagnosis", [])
            for row in fields["diagnoses"]:
                doc.append("diagnosis", {diagnosis_field: row["diagnosis"]})
        drug_code = self._encounter_field("Drug Prescription", "drug_code")
        if fields.get("medications") and drug_code:
            doc.set("drug_prescription", [])
            for row in fields["medications"]:
                values = {drug_code: row["drug_code"]}
                for logical in ("dosage", "period"):
                    installed = self._encounter_field("Drug Prescription", logical)
                    if installed and row.get(logical):
                        values[installed] = row[logical]
                doc.append("drug_prescription", values)
        lab_code = self._encounter_field("Lab Prescription", "lab_test_code")
        if fields.get("lab_tests") and lab_code:
            doc.set("lab_test_prescription", [])
            for row in fields["lab_tests"]:
                doc.append("lab_test_prescription", {lab_code: row["lab_test_code"]})
        procedure = self._encounter_field(
            "Procedure Prescription", "procedure"
        )
        if fields.get("procedures") and procedure:
            doc.set("procedure_prescription", [])
            for row in fields["procedures"]:
                doc.append("procedure_prescription", {procedure: row["procedure"]})

    def _write_encounter_vitals(self, native_encounter_id, patient, vitals):
        if not vitals:
            return
        values = {"patient": patient}
        for logical, raw in vitals.items():
            installed = self._encounter_field(VITAL_SIGNS_DOCTYPE, logical)
            if installed:
                values[installed] = raw
        try:
            doc = self.frappe.new_doc(VITAL_SIGNS_DOCTYPE)
            for key, value in values.items():
                setattr(doc, key, value)
            doc.insert()
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _write_encounter_note(self, native_encounter_id, patient, note):
        text = encounter_contract.build_note_text(note)
        if not text:
            return
        practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
        try:
            doc = self.frappe.new_doc(CLINICAL_NOTE_DOCTYPE)
            doc.patient = patient
            doc.practitioner = practitioner
            doc.note = text
            doc.reference_doctype = ENCOUNTER_DOCTYPE
            doc.reference_docname = native_encounter_id
            doc.insert()
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_encounter_draft(self, payload):
        """Insert the draft encounter (docstatus 0) and its separate documents."""

        patient = payload["_native_patient_id"]
        appointment = payload["_native_appointment_id"]
        try:
            doc = self.frappe.new_doc(ENCOUNTER_DOCTYPE)
            doc.patient = patient
            doc.practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
            doc.company = self.setting(config.ENCOUNTER_COMPANY_KEY)
            doc.appointment = appointment
            doc.encounter_date = self.frappe.utils.nowdate()
            doc.encounter_time = self.frappe.utils.nowtime()
            self._write_encounter_children(doc, payload)
            doc.insert()
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None
        self._write_encounter_vitals(doc.name, patient, payload["fields"]["vitals"])
        self._write_encounter_note(doc.name, patient, payload["fields"]["note"])
        return doc.name

    def save_native_encounter_draft(self, state, payload):
        """Update the locked draft, its child rows and its separate documents."""

        doc = state.doc
        if getattr(doc, "docstatus", None) != encounter_contract.DRAFT_DOCSTATUS:
            raise encounter_write.EncounterTargetNotEligible()
        try:
            self._write_encounter_children(doc, payload)
            doc.save()
        except encounter_write.EncounterTargetNotEligible:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None
        self._write_encounter_vitals(
            doc.name, state.patient, payload["fields"]["vitals"]
        )
        self._write_encounter_note(
            doc.name, state.patient, payload["fields"]["note"]
        )
        return doc.name

    def submit_native_encounter(self, state):
        """Submit the locked draft through the native controller."""

        if getattr(state.doc, "docstatus", None) != encounter_contract.DRAFT_DOCSTATUS:
            raise encounter_write.EncounterTargetNotEligible()
        try:
            state.doc.submit()
        except encounter_write.EncounterTargetNotEligible:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_clinical_note_addendum(self, state, note_text, actor):
        """Insert a NEW Clinical Note addendum; the original stays untouched."""

        if not isinstance(state.clinical_note, str) or not state.clinical_note:
            raise encounter_write.EncounterTargetNotEligible()
        try:
            doc = self.frappe.new_doc(CLINICAL_NOTE_DOCTYPE)
            doc.patient = state.patient
            doc.practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
            doc.note = note_text
            doc.reference_doctype = ENCOUNTER_DOCTYPE
            doc.reference_docname = state.doc.name
            doc.insert()
        except encounter_write.EncounterTargetNotEligible:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None
        return doc.name

    def native_encounter_amend_facts(
        self,
        native_encounter_id,
        addendum_id,
        expected_note,
        original_note,
        original_note_modified,
    ):
        """Re-read the addendum and the untouched original after the insert."""

        try:
            addendum_note = self.frappe.db.get_value(
                CLINICAL_NOTE_DOCTYPE, addendum_id, "note"
            )
            encounter_modified = self.frappe.db.get_value(
                ENCOUNTER_DOCTYPE, native_encounter_id, "modified"
            )
            clinical_note_modified = self.frappe.db.get_value(
                CLINICAL_NOTE_DOCTYPE, original_note, "modified"
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        return {
            "addendum_created": addendum_id is not None,
            "addendum_note": self._text_value(addendum_note),
            "encounter_modified": self._text_value(encounter_modified),
            "clinical_note_modified": self._text_value(
                str(clinical_note_modified) if clinical_note_modified else None
            ),
            "original_note_modified": self._text_value(
                str(original_note_modified) if original_note_modified else None
            ),
        }

    # -- encounter fence, receipts and acknowledgement -------------------

    def lookup_encounter_receipt(self, actor, kind, operation_key):
        if kind not in write_fence.ENCOUNTER_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            record = ledger.lookup(actor=actor, kind=kind, operation_key=operation_key)
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def list_committed_encounter_receipts(
        self, limit, kind=None, operation_key=None
    ):
        ledger = self._ledger()
        try:
            return ledger.committed_encounter_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise encounter_write.EncounterBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_encounter_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.ENCOUNTER_OPERATION_KINDS:
            return "invalid"
        ledger = self._ledger()
        try:
            record = ledger.lookup(actor=actor, kind=kind, operation_key=operation_key)
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = encounter_write.ack_token(
            secret, self.site, actor, kind, operation_key, record.receipt
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    @contextmanager
    def encounter_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.ENCOUNTER_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def receipt_native_encounter_id(self, receipt):
        """Decode the just-committed receipt back to the native encounter id."""

        return encounter_write.decode_receipt(receipt).native_encounter_id

    # -- procedure write path --------------------------------------------

    def native_procedure_boundary_problem(self):
        """Return pinned-boundary drift for the procedure path, or ``None``.

        Tests supply ``frappe.procedure_boundary_snapshot``; production collects
        the pinned versions, the sha256 of the installed file that defines the
        driven method, the method name and callability, both submittable flags,
        the wildcard/class overrides on the path and the enabled Server Script
        count. Any failure is a non-empty problem so callers fail closed.
        """

        try:
            snapshot = getattr(self.frappe, "procedure_boundary_snapshot", None)
            if snapshot is None:
                snapshot = self._collect_procedure_boundary_snapshot()
            return procedure_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_procedure_boundary_snapshot(self):
        import hashlib
        import importlib

        frappe = self.frappe

        def version(module_name, module=None):
            if module is not None:
                return getattr(module, "__version__", None)
            return getattr(importlib.import_module(module_name), "__version__", None)

        path = frappe.get_app_path(
            "healthcare", *procedure_contract.PROCEDURE_SOURCE_PATH
        )
        with open(path, "rb") as handle:
            source_sha = hashlib.sha256(handle.read()).hexdigest()

        controller = frappe.get_doc(
            {"doctype": procedure_contract.PROCEDURE_DOCTYPE}
        )
        method = getattr(controller, procedure_contract.COMPLETE_METHOD, None)

        hooks = frappe.get_hooks() or {}
        doc_events = hooks.get("doc_events") or {}
        wildcard = doc_events.get("*") or {}
        overrides = hooks.get("override_doctype_class") or {}
        extends = hooks.get("extend_doctype_class") or {}
        server_scripts = frappe.get_all(
            "Server Script", filters={"disabled": 0}, limit=1, pluck="name"
        ) or ()
        return {
            "versions": {
                "frappe": version("frappe", frappe),
                "erpnext": version("erpnext"),
                "healthcare": version("healthcare"),
            },
            "source_sha256": {"clinical_procedure": source_sha},
            "complete_method": procedure_contract.COMPLETE_METHOD,
            "complete_callable": 1 if callable(method) else 0,
            "procedure_submittable": (
                1
                if frappe.get_meta(procedure_contract.PROCEDURE_DOCTYPE).get(
                    "is_submittable"
                )
                else 0
            ),
            "stock_entry_submittable": (
                1
                if frappe.get_meta(procedure_contract.STOCK_ENTRY_DOCTYPE).get(
                    "is_submittable"
                )
                else 0
            ),
            "enabled_server_scripts": len(server_scripts),
            "doc_events": {
                doctype: tuple(doc_events.get(doctype) or ())
                for doctype in (
                    procedure_contract.PROCEDURE_DOCTYPE,
                    procedure_contract.STOCK_ENTRY_DOCTYPE,
                )
            },
            "wildcard_doc_events": {
                event: tuple(wildcard.get(event) or ())
                for event in procedure_contract.REVIEWED_WILDCARD_EVENTS
            },
            "scheduler_procedure_jobs": tuple(
                path
                for handlers in (hooks.get("scheduler_events") or {}).values()
                for path in (handlers or ())
                if procedure_contract.SCHEDULER_PROCEDURE_TOKEN in str(path)
            ),
            "class_overrides": {
                procedure_contract.PROCEDURE_DOCTYPE: tuple(
                    overrides.get(procedure_contract.PROCEDURE_DOCTYPE) or ()
                )
                + tuple(extends.get(procedure_contract.PROCEDURE_DOCTYPE) or ())
            },
        }

    def _procedure_meta_fields(self):
        """Resolve the installed field names through the metadata cache."""

        cached = getattr(self, "_procedure_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in procedure_contract.REQUIRED_FIELDS:
            fields[doctype] = set(get_meta(doctype).get_valid_columns())
        self._procedure_fields_cache = fields
        return fields

    def native_procedure_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return procedure_contract.install_problem(
                self._procedure_meta_fields()
            )
        except Exception:
            return "installed metadata unreadable"

    def _procedure_field(self, doctype, logical, candidates=None):
        return procedure_contract.resolve_field(
            self._procedure_meta_fields(), doctype, logical, candidates
        )

    def native_procedure_can_write(self, native_procedure_id=None):
        try:
            return bool(
                self.frappe.has_permission(
                    procedure_contract.PROCEDURE_DOCTYPE,
                    ptype="write",
                    doc=native_procedure_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_procedure_action_detail(self, native_procedure_id):
        """Read the pre-fence decision fields under native authority."""

        if self.native_procedure_can_write(native_procedure_id) is not True:
            return None
        try:
            doc = self.frappe.get_doc(
                procedure_contract.PROCEDURE_DOCTYPE, native_procedure_id
            )
            return self._procedure_state_values(doc)
        except Exception:
            return None

    def _procedure_rows(self, native_procedure_id, warehouse):
        """Per-row batch and stock facts read from the site's own masters."""

        item_doctype = procedure_contract.ITEM_DOCTYPE
        batch_field = self._procedure_field(item_doctype, "batch_no")
        fields = ["item_code", "qty", "uom"]
        if batch_field and batch_field not in fields:
            fields.append(batch_field)
        rows = self.frappe.get_all(
            item_doctype,
            filters={
                "parent": native_procedure_id,
                "parenttype": procedure_contract.PROCEDURE_DOCTYPE,
            },
            fields=fields,
            order_by="idx asc",
            limit=procedure_contract.MAX_ROWS + 1,
        ) or ()
        details = []
        for row in rows:
            get = (
                row.get
                if hasattr(row, "get")
                else (lambda key, default=None: default)
            )
            batch_no = self._text_value(get(batch_field)) if batch_field else None
            batch_item = batch_qty = batch_expiry = None
            if batch_no:
                batch = self.frappe.db.get_value(
                    "Batch",
                    batch_no,
                    ["item", "batch_qty", "expiry_date"],
                    as_dict=True,
                )
                if batch is not None:
                    batch_get = (
                        batch.get
                        if hasattr(batch, "get")
                        else (lambda key, default=None: default)
                    )
                    batch_item = self._text_value(batch_get("item"))
                    batch_qty = batch_get("batch_qty")
                    batch_expiry = self._text_value(batch_get("expiry_date"))
            available = None
            if warehouse:
                available = self.frappe.db.get_value(
                    "Bin",
                    {"item_code": get("item_code"), "warehouse": warehouse},
                    "actual_qty",
                )
            details.append(
                {
                    "item_code": self._text_value(get("item_code")),
                    "qty": get("qty"),
                    "uom": self._text_value(get("uom")),
                    "batch_no": batch_no,
                    "batch_item": batch_item,
                    "batch_qty": batch_qty,
                    "batch_expiry": batch_expiry,
                    "available_qty": available,
                }
            )
        return details

    def _procedure_state_values(self, doc):
        """Build the contract detail from a locked procedure document."""

        native = doc.name
        warehouse = self._text_value(getattr(doc, "warehouse", None))
        try:
            allow_negative = bool(
                self.frappe.db.get_single_value(
                    "Stock Settings", "allow_negative_stock"
                )
            )
        except Exception:
            allow_negative = False
        rows = self._procedure_rows(native, warehouse)
        patient = self._text_value(getattr(doc, "patient", None))
        customer = None
        if patient:
            customer = self._text_value(
                self.frappe.db.get_value("Patient", patient, "customer")
            )
        template = self._text_value(getattr(doc, "procedure_template", None))
        post_op_checklist = None
        if template:
            post_op_checklist = self._text_value(
                self.frappe.db.get_value(
                    procedure_contract.PROCEDURE_TEMPLATE_DOCTYPE,
                    template,
                    "post_op_nursing_checklist_template",
                )
            )
        return {
            "patient": patient,
            "docstatus": getattr(doc, "docstatus", None),
            "status": self._text_value(getattr(doc, "status", None)),
            "consume_stock": bool(getattr(doc, "consume_stock", 0)),
            "warehouse": warehouse,
            "company": self._text_value(getattr(doc, "company", None)),
            "price_list": self._text_value(getattr(doc, "price_list", None)),
            "customer": customer,
            "procedure_template": template,
            "post_op_checklist": post_op_checklist,
            "service_request": self._text_value(
                getattr(doc, "service_request", None)
            ),
            "appointment": self._text_value(getattr(doc, "appointment", None)),
            "modified": self._text_value(getattr(doc, "modified", None)),
            "items": tuple(rows),
            "allow_negative_stock": allow_negative,
            "today": self.site_today(),
        }

    def lock_procedure_target(self, native_procedure_id):
        """Lock the procedure row and read the contract decision fields."""

        try:
            doc = self.frappe.get_doc(
                procedure_contract.PROCEDURE_DOCTYPE,
                native_procedure_id,
                for_update=True,
            )
            values = self._procedure_state_values(doc)
        except Exception:
            raise patient_write.WriteUncertain() from None
        return ProcedureLockedState(doc=doc, **values)

    def native_stock_ledger_baseline(self, items, warehouse):
        """Pre-fence Stock Ledger Entry and Bin baselines for the delta re-read."""

        try:
            bins = {}
            for item in items or ():
                code = item.get("item_code") if hasattr(item, "get") else None
                if not code:
                    continue
                bins[code] = self.frappe.db.get_value(
                    "Bin",
                    {"item_code": code, "warehouse": warehouse},
                    "actual_qty",
                )
            return {
                "stock_ledger_rows": int(
                    self.frappe.db.count(
                        "Stock Ledger Entry",
                        {"voucher_type": procedure_contract.STOCK_ENTRY_DOCTYPE},
                    )
                ),
                "gl_rows": int(
                    self.frappe.db.count(
                        "GL Entry",
                        {"voucher_type": procedure_contract.STOCK_ENTRY_DOCTYPE},
                    )
                ),
                "bins": bins,
            }
        except Exception:
            raise patient_write.WriteUncertain() from None

    def complete_native_procedure(self, state):
        """Drive the pinned installed ``complete_procedure`` document method.

        The method is called on the locked document; it creates and submits the
        Material-Issue Stock Entry itself and sets the completion fields. Its
        return value (the Stock Entry name) is returned verbatim, and a raised
        error is an uncertain outcome: the stock may already have moved.
        """

        method = getattr(state.doc, procedure_contract.COMPLETE_METHOD, None)
        if not callable(method):
            raise patient_write.WriteUncertain()
        try:
            return self._text_value(method())
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_procedure_complete_facts(
        self, native_procedure_id, stock_entry, baseline
    ):
        """Closed-world re-read after the driven method returns.

        EXPECTED side effects only: the returned Stock Entry (submitted material
        issue for the same company), the procedure status/actual end pair, the
        Stock Ledger Entry row delta, the summed Bin delta at the procedure
        warehouse, the GL row delta and the nursing task row count.
        """

        if not isinstance(baseline, dict):
            raise patient_write.WriteUncertain()
        try:
            row = self.frappe.db.get_value(
                procedure_contract.PROCEDURE_DOCTYPE,
                native_procedure_id,
                ["status", "actual_end_datetime", "company", "warehouse"],
                as_dict=True,
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        if row is None:
            raise patient_write.WriteUncertain()
        get = (
            row.get
            if hasattr(row, "get")
            else (lambda key, default=None: default)
        )
        warehouse = self._text_value(get("warehouse"))

        entry_docstatus = entry_type = entry_company = None
        sle_delta = 0
        gl_delta = 0
        bin_delta = 0.0
        try:
            if stock_entry:
                entry = self.frappe.db.get_value(
                    procedure_contract.STOCK_ENTRY_DOCTYPE,
                    stock_entry,
                    ["docstatus", "stock_entry_type", "company"],
                    as_dict=True,
                )
                if entry is None:
                    raise patient_write.WriteUncertain()
                entry_get = (
                    entry.get
                    if hasattr(entry, "get")
                    else (lambda key, default=None: default)
                )
                entry_docstatus = entry_get("docstatus")
                entry_type = self._text_value(entry_get("stock_entry_type"))
                entry_company = self._text_value(entry_get("company"))
                sle_delta = int(
                    self.frappe.db.count(
                        "Stock Ledger Entry",
                        {"voucher_type": procedure_contract.STOCK_ENTRY_DOCTYPE},
                    )
                ) - int(baseline.get("stock_ledger_rows") or 0)
                gl_delta = int(
                    self.frappe.db.count(
                        "GL Entry",
                        {"voucher_type": procedure_contract.STOCK_ENTRY_DOCTYPE},
                    )
                ) - int(baseline.get("gl_rows") or 0)
                bins = baseline.get("bins") or {}
                for code, before in bins.items():
                    after = self.frappe.db.get_value(
                        "Bin",
                        {"item_code": code, "warehouse": warehouse},
                        "actual_qty",
                    )
                    if isinstance(after, (int, float)):
                        bin_delta += float(after) - float(before or 0)
            nursing = int(
                self.frappe.db.count(
                    "Nursing Task",
                    {
                        "reference_doctype": procedure_contract.PROCEDURE_DOCTYPE,
                        "reference_name": native_procedure_id,
                    },
                )
            )
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None
        return {
            "stock_entry": stock_entry,
            "stock_entry_docstatus": entry_docstatus,
            "stock_entry_type": entry_type,
            "stock_entry_company": entry_company,
            "procedure_company": self._text_value(get("company")),
            "status": self._text_value(get("status")),
            "actual_end_datetime": self._text_value(get("actual_end_datetime")),
            "stock_ledger_rows": sle_delta,
            "gl_rows": gl_delta,
            "bin_delta": bin_delta,
            "bin_warehouse": warehouse,
            "nursing_task_rows": nursing,
        }

    # -- procedure fence, receipts and acknowledgement -------------------

    def lookup_procedure_receipt(self, actor, kind, operation_key):
        if kind not in write_fence.PROCEDURE_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def list_committed_procedure_receipts(
        self, limit, kind=None, operation_key=None
    ):
        ledger = self._ledger()
        try:
            return ledger.committed_procedure_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise procedure_write.ProcedureBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_procedure_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.PROCEDURE_OPERATION_KINDS:
            return "invalid"
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = procedure_write.ack_token(
            secret, self.site, actor, kind, operation_key, record.receipt
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    @contextmanager
    def procedure_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.PROCEDURE_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def receipt_stock_entry(self, receipt):
        """Decode the just-committed receipt back to the created Stock Entry."""

        return procedure_write.decode_receipt(receipt).stock_entry

    # -- admin user/master surface --------------------------------------

    def _admin_meta_fields(self):
        """Resolve the installed field names through the metadata cache."""

        cached = getattr(self, "_admin_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in admin_contract.REQUIRED_FIELDS:
            fields[doctype] = set(get_meta(doctype).get_valid_columns())
        self._admin_fields_cache = fields
        return fields

    def native_admin_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return admin_contract.install_problem(self._admin_meta_fields())
        except Exception:
            return "installed metadata unreadable"

    def native_admin_can_write(self):
        """The acting user's native User-create authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    admin_contract.USER_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_admin_can_create(self, doctype):
        """The acting user's native create authority for one target DocType."""

        try:
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_admin_missing_roles(self, roles):
        """Return the assignable roles that are not installed on the site."""

        missing = []
        for role in roles or ():
            try:
                if not self.frappe.db.exists(admin_contract.ROLE_DOCTYPE, role):
                    missing.append(role)
            except Exception:
                missing.append(role)
        return missing

    def native_admin_user_exists(self, identifier):
        try:
            return bool(
                self.frappe.db.exists(admin_contract.USER_DOCTYPE, identifier)
            )
        except Exception:
            return True

    def native_admin_practitioner_exists(self, name):
        try:
            return bool(
                self.frappe.db.exists(admin_contract.PRACTITIONER_DOCTYPE, name)
            )
        except Exception:
            return False

    def native_admin_master_exists(self, master_type, identifier):
        doctype = admin_contract.MASTER_DOCTYPES.get(master_type)
        if doctype is None:
            return True
        try:
            return bool(self.frappe.db.exists(doctype, identifier))
        except Exception:
            return True

    def native_admin_item_price_exists(self, item_code, price_list):
        try:
            return bool(
                self.frappe.db.exists(
                    admin_contract.ITEM_PRICE_DOCTYPE,
                    {"item_code": item_code, "price_list": price_list},
                )
            )
        except Exception:
            return True

    def _admin_company(self):
        company = None
        try:
            company = self._text_value(
                self.frappe.db.get_single_value(
                    "Global Defaults", "default_company"
                )
            )
        except Exception:
            company = None
        if not company:
            try:
                company = self._text_value(
                    self.frappe.defaults.get_default("company")
                )
            except Exception:
                company = None
        if not company:
            raise patient_write.WriteUncertain()
        return company

    def _admin_currency(self, company):
        currency = None
        try:
            currency = self._text_value(
                self.frappe.db.get_value(
                    "Company", company, "default_currency"
                )
            )
        except Exception:
            currency = None
        if not currency:
            raise patient_write.WriteUncertain()
        return currency

    def create_native_admin_user(self, payload):
        """Create the User, its roles and the optional practitioner link once.

        The password is caller-supplied and the welcome mail is suppressed, as
        on the installed site. The credentials never leave this mutation: the
        caller receives the created user name only.
        """

        try:
            doc = {
                "doctype": admin_contract.USER_DOCTYPE,
                "email": payload["email"] or payload["username"],
                "first_name": payload["full_name"],
                "full_name": payload["full_name"],
                "enabled": 1,
                "user_type": "System User",
                "send_welcome_email": 0,
                "new_password": payload["password"],
                "roles": [
                    {"role": role} for role in payload.get("roles") or ()
                ],
            }
            if payload.get("username"):
                doc["username"] = payload["username"]
            user = self.frappe.get_doc(doc)
            user.flags.no_welcome_mail = True
            user.insert()
            practitioner = payload.get("practitioner")
            if practitioner:
                self.frappe.db.set_value(
                    admin_contract.PRACTITIONER_DOCTYPE,
                    practitioner,
                    "user_id",
                    user.name,
                )
            return self._text_value(user.name)
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_admin_master(self, master_type, payload):
        """Create one master row (with its bundled price/account child).

        Company and currency are read from the site's own records; none is
        accepted from the client.
        """

        try:
            company = self._admin_company()
            currency = self._admin_currency(company)
            if master_type == "warehouse":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.WAREHOUSE_DOCTYPE,
                        "warehouse_name": payload["warehouse_name"],
                        "company": company,
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "supplier":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.SUPPLIER_DOCTYPE,
                        "supplier_name": payload["supplier_name"],
                        "supplier_group": "All Supplier Groups",
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "item":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.ITEM_DOCTYPE,
                        "item_code": payload["item_code"],
                        "item_name": payload["item_name"],
                        "item_group": payload["item_group"],
                        "stock_uom": payload["stock_uom"],
                        "is_stock_item": 1,
                    }
                )
                doc.insert()
                price = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.ITEM_PRICE_DOCTYPE,
                        "item_code": doc.name,
                        "price_list": payload["price_list"],
                        "rate": payload["rate"],
                        "currency": currency,
                        "selling": 1,
                    }
                )
                price.insert()
                return self._text_value(doc.name)
            if master_type == "price_list":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.PRICE_LIST_DOCTYPE,
                        "price_list_name": payload["price_list_name"],
                        "currency": currency,
                        "selling": 1,
                        "enabled": 1,
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "mode_of_payment":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.MODE_OF_PAYMENT_DOCTYPE,
                        "mode_of_payment": payload["mode_of_payment"],
                        "enabled": 1,
                        "accounts": [
                            {
                                "company": company,
                                "default_account": payload["account"],
                            }
                        ],
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "procedure_template":
                item = self.frappe.db.get_value(
                    admin_contract.ITEM_DOCTYPE,
                    payload["item_code"],
                    ["item_group", "stock_uom"],
                    as_dict=True,
                )
                if item is None:
                    raise patient_write.WriteUncertain()
                item_get = (
                    item.get
                    if hasattr(item, "get")
                    else (lambda key, default=None: default)
                )
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.PROCEDURE_TEMPLATE_DOCTYPE,
                        "template": payload["template_name"],
                        "item_code": payload["item_code"],
                        "item_group": self._text_value(item_get("item_group")),
                        "uom": self._text_value(item_get("stock_uom")),
                        "rate": payload["rate"],
                        "company": company,
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            raise patient_write.WriteUncertain()
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_admin_user_facts(self, user):
        """Closed-world re-read after one user mutation."""

        try:
            row = self.frappe.db.get_value(
                admin_contract.USER_DOCTYPE,
                user,
                ["name", "enabled"],
                as_dict=True,
            )
            if row is None:
                raise patient_write.WriteUncertain()
            get = (
                row.get
                if hasattr(row, "get")
                else (lambda key, default=None: default)
            )
            roles = self.frappe.get_all(
                "User Role",
                filters={"parent": user},
                pluck="role",
            ) or ()
            practitioner = self.frappe.db.get_value(
                admin_contract.PRACTITIONER_DOCTYPE,
                {"user_id": user},
                "name",
            )
            return {
                "user": self._text_value(get("name")),
                "enabled": get("enabled"),
                "roles": list(roles),
                "practitioner": (
                    self._text_value(practitioner) or None
                ),
            }
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_admin_master_facts(self, master_type, name):
        """Closed-world re-read after one master mutation."""

        try:
            if master_type == "warehouse":
                row = self.frappe.db.get_value(
                    admin_contract.WAREHOUSE_DOCTYPE,
                    name,
                    ["name", "company"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ("company",))
            if master_type == "supplier":
                row = self.frappe.db.get_value(
                    admin_contract.SUPPLIER_DOCTYPE,
                    name,
                    ["name", "supplier_name"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ("supplier_name",))
            if master_type == "item":
                row = self.frappe.db.get_value(
                    admin_contract.ITEM_DOCTYPE,
                    name,
                    ["name", "item_code"],
                    as_dict=True,
                )
                facts = self._admin_facts_row(row, ("item_code",))
                prices = self.frappe.get_all(
                    admin_contract.ITEM_PRICE_DOCTYPE,
                    filters={"item_code": name},
                    fields=["price_list", "rate"],
                    order_by="creation desc",
                    limit=1,
                ) or ()
                price = prices[0] if prices else None
                price_get = (
                    price.get
                    if hasattr(price, "get")
                    else (lambda key, default=None: default)
                )
                facts["price_list"] = self._text_value(
                    price_get("price_list")
                )
                facts["rate"] = price_get("rate")
                return facts
            if master_type == "price_list":
                row = self.frappe.db.get_value(
                    admin_contract.PRICE_LIST_DOCTYPE,
                    name,
                    ["name", "currency", "selling"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ("currency", "selling"))
            if master_type == "mode_of_payment":
                row = self.frappe.db.get_value(
                    admin_contract.MODE_OF_PAYMENT_DOCTYPE,
                    name,
                    ["name"],
                    as_dict=True,
                )
                facts = self._admin_facts_row(row, ())
                accounts = self.frappe.get_all(
                    "Mode of Payment Account",
                    filters={"parent": name},
                    fields=["default_account"],
                    order_by="idx asc",
                    limit=1,
                ) or ()
                account = accounts[0] if accounts else None
                account_get = (
                    account.get
                    if hasattr(account, "get")
                    else (lambda key, default=None: default)
                )
                facts["account"] = self._text_value(
                    account_get("default_account")
                )
                return facts
            if master_type == "procedure_template":
                row = self.frappe.db.get_value(
                    admin_contract.PROCEDURE_TEMPLATE_DOCTYPE,
                    name,
                    ["name", "template", "item_code"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ("template", "item_code"))
            raise patient_write.WriteUncertain()
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _admin_facts_row(self, row, fields):
        if row is None:
            raise patient_write.WriteUncertain()
        get = (
            row.get
            if hasattr(row, "get")
            else (lambda key, default=None: default)
        )
        facts = {"name": self._text_value(get("name"))}
        for field in fields:
            facts[field] = self._text_value(get(field))
        return facts

    # -- admin fence, receipts and acknowledgement -----------------------

    def lookup_admin_receipt(self, actor, kind, operation_key):
        if kind not in write_fence.ADMIN_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if record is None:
            return None
        if record.state == write_fence.COMMITTED:
            return record.receipt
        raise patient_write.WriteLocked()

    def list_committed_admin_receipts(self, limit, kind=None, operation_key=None):
        ledger = self._ledger()
        try:
            return ledger.committed_admin_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise admin_contract.AdminBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_admin_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.ADMIN_OPERATION_KINDS:
            return "invalid"
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = admin_contract.ack_token(
            secret, self.site, actor, kind, operation_key, record.receipt
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    @contextmanager
    def admin_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.ADMIN_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    # -- stock receive/transfer/adjust surface ---------------------------

    def _stock_meta_fields(self):
        """Resolve the installed field names through the metadata cache."""

        cached = getattr(self, "_stock_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in stock_contract.INSTALLED_CANDIDATES:
            fields[doctype] = set(get_meta(doctype).get_valid_columns())
        self._stock_fields_cache = fields
        return fields

    def native_stock_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return stock_contract.install_problem(self._stock_meta_fields())
        except Exception:
            return "installed metadata unreadable"

    def native_stock_boundary_problem(self):
        """Return pinned-boundary drift for the stock family, or ``None``.

        Tests supply ``frappe.stock_boundary_snapshot``; production collects the
        pinned versions, the sha256 of the controllers this family drives or
        creates, their MRO, the submittable flags, the wildcard/class overrides
        on the path, the enabled Server Script count and our fence kinds. Any
        failure is a non-empty problem so callers fail closed.
        """

        try:
            snapshot = getattr(self.frappe, "stock_boundary_snapshot", None)
            if snapshot is None:
                snapshot = self._collect_stock_boundary_snapshot()
            return stock_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_stock_boundary_snapshot(self):
        import hashlib
        import importlib

        frappe = self.frappe

        def version(module_name, module=None):
            if module is not None:
                return getattr(module, "__version__", None)
            try:
                return getattr(
                    importlib.import_module(module_name), "__version__", None
                )
            except Exception:
                return None

        sources = {
            "purchase_receipt": (
                "stock",
                "doctype",
                "purchase_receipt",
                "purchase_receipt.py",
            ),
            "stock_entry": (
                "stock",
                "doctype",
                "stock_entry",
                "stock_entry.py",
            ),
            "stock_reconciliation": (
                "stock",
                "doctype",
                "stock_reconciliation",
                "stock_reconciliation.py",
            ),
            "batch": ("stock", "doctype", "batch", "batch.py"),
        }
        source_sha256 = {}
        for key, parts in sources.items():
            path = frappe.get_app_path("erpnext", *parts)
            with open(path, "rb") as handle:
                source_sha256[key] = hashlib.sha256(handle.read()).hexdigest()

        mro = {}
        for doctype in stock_contract.GUARDED_DOCTYPES:
            controller = type(frappe.get_doc({"doctype": doctype}))
            mro[doctype] = tuple(
                f"{base.__module__}.{base.__name__}" for base in controller.__mro__
            )

        hooks = frappe.get_hooks() or {}
        doc_events = hooks.get("doc_events") or {}
        wildcard = doc_events.get("*") or {}
        overrides = hooks.get("override_doctype_class") or {}
        extends = hooks.get("extend_doctype_class") or {}
        server_scripts = frappe.get_all(
            "Server Script", filters={"disabled": 0}, limit=1, pluck="name"
        ) or ()
        return {
            "versions": {
                "frappe": version("frappe", frappe),
                "erpnext": version("erpnext"),
                "healthcare": version("healthcare"),
            },
            "source_sha256": source_sha256,
            "controller_mro": mro,
            "submittable": {
                doctype: (
                    1 if frappe.get_meta(doctype).get("is_submittable") else 0
                )
                for doctype in (
                    stock_contract.PURCHASE_RECEIPT_DOCTYPE,
                    stock_contract.STOCK_ENTRY_DOCTYPE,
                    stock_contract.STOCK_RECONCILIATION_DOCTYPE,
                )
            },
            "enabled_server_scripts": len(server_scripts),
            "doc_events": {
                doctype: tuple(doc_events.get(doctype) or ())
                for doctype in stock_contract.GUARDED_DOCTYPES
            },
            "wildcard_doc_events": {
                event: tuple(wildcard.get(event) or ())
                for event in stock_contract.REVIEWED_WILDCARD_EVENTS
            },
            "scheduler_stock_jobs": tuple(
                path
                for handlers in (hooks.get("scheduler_events") or {}).values()
                for path in (handlers or ())
                if any(
                    token in str(path)
                    for token in stock_contract.SCHEDULER_STOCK_TOKENS
                )
            ),
            "class_overrides": {
                doctype: tuple(overrides.get(doctype) or ())
                + tuple(extends.get(doctype) or ())
                for doctype in stock_contract.GUARDED_DOCTYPES
            },
            "fence_kinds": tuple(stock_contract.STOCK_KINDS),
        }

    def native_stock_can_create(self, doctype):
        """The acting user's native create authority on one stock document."""

        try:
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def _stock_item(self, item_code):
        row = self.frappe.db.get_value(
            stock_contract.ITEM_DOCTYPE,
            item_code,
            ["has_batch_no", "has_expiry_date", "is_stock_item", "stock_uom"],
            as_dict=True,
        )
        if row is None:
            return {"exists": False}
        get = getattr(row, "get", lambda key, default=None: default)
        return {
            "exists": True,
            "item_code": item_code,
            "has_batch_no": bool(get("has_batch_no")),
            "has_expiry_date": bool(get("has_expiry_date")),
            "is_stock_item": bool(get("is_stock_item")),
            "stock_uom": self._text_value(get("stock_uom")),
        }

    def _stock_warehouse(self, warehouse):
        company = self.frappe.db.get_value("Warehouse", warehouse, "company")
        return {
            "exists": bool(company),
            "company": self._text_value(company),
        }

    def _stock_batch(self, batch_id):
        if not batch_id:
            return None
        row = self.frappe.db.get_value(
            stock_contract.BATCH_DOCTYPE,
            batch_id,
            ["item", "expiry_date", "batch_qty"],
            as_dict=True,
        )
        if row is None:
            return {"batch_id": batch_id, "exists": False}
        get = getattr(row, "get", lambda key, default=None: default)
        return {
            "batch_id": batch_id,
            "exists": True,
            "item": self._text_value(get("item")),
            "expiry_date": self._text_value(get("expiry_date")),
            "batch_qty": get("batch_qty"),
        }

    def _stock_bin_qty(self, item_code, warehouse):
        if not warehouse:
            return None
        return self.frappe.db.get_value(
            stock_contract.BIN_DOCTYPE,
            {"item_code": item_code, "warehouse": warehouse},
            "actual_qty",
        )

    def _stock_purchase_order(self, purchase_order, item_code):
        if not purchase_order:
            return {"po_pending_qty": None, "po_item": None}
        docstatus = self.frappe.db.get_value(
            "Purchase Order", purchase_order, "docstatus"
        )
        if docstatus != 1:
            return {"po_pending_qty": None, "po_item": None}
        row = self.frappe.db.get_value(
            "Purchase Order Item",
            {"parent": purchase_order, "item_code": item_code},
            ["name", "qty", "received_qty"],
            as_dict=True,
        )
        if row is None:
            return {"po_pending_qty": None, "po_item": None}
        get = getattr(row, "get", lambda key, default=None: default)
        ordered = get("qty")
        received = get("received_qty") or 0
        if not isinstance(ordered, (int, float)):
            return {"po_pending_qty": None, "po_item": None}
        return {
            "po_pending_qty": float(ordered) - float(received),
            "po_item": self._text_value(get("name")),
        }

    def native_stock_receive_detail(self, payload):
        """Read the pre-fence receive decision fields under native authority."""

        try:
            item = self._stock_item(payload["item_code"])
            warehouse = self._stock_warehouse(payload["warehouse"])
            order = self._stock_purchase_order(
                payload.get("purchase_order"), payload["item_code"]
            )
            return {
                "today": self.site_today(),
                "company": warehouse.get("company"),
                "item": item,
                "warehouse": warehouse,
                "supplier_exists": bool(
                    self.frappe.db.exists("Supplier", payload["supplier"])
                ),
                "batch": self._stock_batch(payload.get("batch_id")),
                "available_qty": self._stock_bin_qty(
                    payload["item_code"], payload["warehouse"]
                ),
                "po_item": order["po_item"],
                "po_pending_qty": order["po_pending_qty"],
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def native_stock_transfer_detail(self, payload):
        """Read the pre-fence transfer decision fields."""

        try:
            warehouse = self._stock_warehouse(payload["source_warehouse"])
            return {
                "today": self.site_today(),
                "company": warehouse.get("company"),
                "item": self._stock_item(payload["item_code"]),
                "warehouse": warehouse,
                "target_warehouse": self._stock_warehouse(
                    payload["target_warehouse"]
                ),
                "batch": self._stock_batch(payload.get("batch_id")),
                "available_qty": self._stock_bin_qty(
                    payload["item_code"], payload["source_warehouse"]
                ),
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def native_stock_adjust_detail(self, payload):
        """Read the pre-fence reconciliation decision fields."""

        try:
            warehouse = self._stock_warehouse(payload["warehouse"])
            company = warehouse.get("company")
            account = None
            if company:
                account = self.frappe.db.get_value(
                    "Company", company, "stock_adjustment_account"
                )
            return {
                "today": self.site_today(),
                "company": company,
                "stock_adjustment_account": self._text_value(account),
                "item": self._stock_item(payload["item_code"]),
                "warehouse": warehouse,
                "batch": self._stock_batch(payload.get("batch_id")),
                "available_qty": self._stock_bin_qty(
                    payload["item_code"], payload["warehouse"]
                ),
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def _stock_document_counts(self):
        vouchers = (
            stock_contract.PURCHASE_RECEIPT_DOCTYPE,
            stock_contract.STOCK_ENTRY_DOCTYPE,
            stock_contract.STOCK_RECONCILIATION_DOCTYPE,
        )
        return {
            "stock_ledger_rows": int(
                self.frappe.db.count(
                    "Stock Ledger Entry",
                    filters={"voucher_type": ("in", list(vouchers))},
                )
            ),
            "gl_rows": int(
                self.frappe.db.count(
                    "GL Entry",
                    filters={"voucher_type": ("in", list(vouchers))},
                )
            ),
        }

    def native_stock_baseline(self, kind, warehouses, item_code):
        """Pre-fence Stock Ledger Entry, GL and Bin baselines."""

        if kind not in stock_contract.STOCK_KINDS:
            raise patient_write.WriteUncertain()
        try:
            counts = self._stock_document_counts()
            bins = {
                warehouse: self._stock_bin_qty(item_code, warehouse)
                for warehouse in warehouses
            }
            return {**counts, "bins": bins}
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _stock_now_time(self):
        try:
            from frappe.utils import nowtime

            return nowtime()
        except Exception:
            return None

    def _stock_create_batch(self, prepared):
        """Create the batch master that must exist before the receipt posts."""

        document = {
            "doctype": stock_contract.BATCH_DOCTYPE,
            "item": prepared["item_code"],
            "batch_id": prepared["batch_no"],
        }
        if prepared.get("batch_expiry"):
            document["expiry_date"] = prepared["batch_expiry"]
        self.frappe.get_doc(document).insert()

    def create_native_stock_receipt(self, prepared):
        """One receive unit: batch master (where required), then the receipt."""

        try:
            if prepared.get("create_batch"):
                self._stock_create_batch(prepared)
            row = {
                "item_code": prepared["item_code"],
                "qty": prepared["qty"],
                "uom": prepared["uom"],
                "warehouse": prepared["warehouse"],
            }
            if prepared.get("batch_no"):
                row["batch_no"] = prepared["batch_no"]
                row["use_serial_batch_fields"] = 1
            if prepared.get("purchase_order"):
                item_row = {"purchase_order": prepared["purchase_order"]}
                if prepared.get("po_item"):
                    item_row["purchase_order_item"] = prepared["po_item"]
                row.update(item_row)
            document = {
                "doctype": stock_contract.PURCHASE_RECEIPT_DOCTYPE,
                "supplier": prepared["supplier"],
                "company": prepared["company"],
                "posting_date": prepared["posting_date"],
                "posting_time": self._stock_now_time(),
                "set_posting_time": 1,
                "items": [row],
            }
            receipt = self.frappe.get_doc(document)
            receipt.insert()
            receipt.submit()
            return self._text_value(receipt.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_stock_transfer(self, prepared):
        """One Material Transfer Stock Entry between the two warehouses."""

        try:
            row = {
                "item_code": prepared["item_code"],
                "qty": prepared["qty"],
                "uom": prepared["uom"],
                "s_warehouse": prepared["source_warehouse"],
                "t_warehouse": prepared["target_warehouse"],
            }
            if prepared.get("batch_no"):
                row["batch_no"] = prepared["batch_no"]
                row["use_serial_batch_fields"] = 1
            document = {
                "doctype": stock_contract.STOCK_ENTRY_DOCTYPE,
                "stock_entry_type": stock_contract.MATERIAL_TRANSFER_TYPE,
                "company": prepared["company"],
                "posting_date": prepared["posting_date"],
                "posting_time": self._stock_now_time(),
                "set_posting_time": 1,
                "items": [row],
            }
            entry = self.frappe.get_doc(document)
            entry.insert()
            entry.submit()
            return self._text_value(entry.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_stock_reconciliation(self, prepared):
        """One restricted Stock Reconciliation with the counted quantity."""

        try:
            row = {
                "item_code": prepared["item_code"],
                "qty": prepared["qty"],
                "warehouse": prepared["warehouse"],
            }
            if prepared.get("batch_no"):
                row["batch_no"] = prepared["batch_no"]
                row["use_serial_batch_fields"] = 1
            document = {
                "doctype": stock_contract.STOCK_RECONCILIATION_DOCTYPE,
                "company": prepared["company"],
                "posting_date": prepared["posting_date"],
                "posting_time": self._stock_now_time(),
                "set_posting_time": 1,
                "expense_account": prepared["expense_account"],
                "items": [row],
            }
            reconciliation = self.frappe.get_doc(document)
            reconciliation.insert()
            reconciliation.submit()
            return self._text_value(reconciliation.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _stock_docstatus(self, reference, expected):
        doctype = expected.get("doctype")
        if not isinstance(doctype, str) or not reference:
            raise patient_write.WriteUncertain()
        docstatus = self.frappe.db.get_value(doctype, reference, "docstatus")
        if docstatus is None:
            raise patient_write.WriteUncertain()
        return docstatus

    def _stock_side_effect_facts(self, reference, expected, baseline):
        """Closed-world re-read: document state, SLE/GL deltas and Bin movement."""

        if not isinstance(baseline, dict) or not isinstance(expected, dict):
            raise patient_write.WriteUncertain()
        try:
            counts = self._stock_document_counts()
            bins = {}
            for warehouse in expected["bins"]:
                before = (baseline.get("bins") or {}).get(warehouse)
                after = self._stock_bin_qty(
                    expected.get("item_code"), warehouse
                )
                after_value = (
                    float(after) if isinstance(after, (int, float)) else 0.0
                )
                if expected.get("bin_mode") == "set":
                    bins[warehouse] = after_value
                else:
                    before_value = (
                        float(before)
                        if isinstance(before, (int, float))
                        else 0.0
                    )
                    bins[warehouse] = after_value - before_value
            return {
                "docstatus": self._stock_docstatus(reference, expected),
                "doctype": expected["doctype"],
                "sle_rows": counts["stock_ledger_rows"]
                - int(baseline.get("stock_ledger_rows") or 0),
                "gl_rows": counts["gl_rows"]
                - int(baseline.get("gl_rows") or 0),
                "bins": bins,
            }
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_stock_receipt_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(reference, expected, baseline)

    def native_stock_transfer_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(reference, expected, baseline)

    def native_stock_adjust_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(reference, expected, baseline)

    # -- stock fence, receipts and acknowledgement -----------------------

    def list_committed_stock_receipts(
        self, limit, kind=None, operation_key=None
    ):
        ledger = self._ledger()
        try:
            return ledger.committed_stock_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise stock_contract.StockBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_stock_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.STOCK_OPERATION_KINDS:
            return "invalid"
        ledger = self._ledger()
        try:
            record = ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        if (
            record is None
            or record.state != write_fence.COMMITTED
            or not record.receipt
        ):
            return "invalid"
        expected = stock_contract.ack_token(
            secret, self.site, actor, kind, operation_key, record.receipt
        )
        if not hmac.compare_digest(expected, ack_token):
            return "invalid"
        try:
            return ledger.acknowledge_barrier(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                ack_receipt="ack:" + expected,
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    @contextmanager
    def stock_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.STOCK_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.fence(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as claim:
                yield claim
        except write_fence.OperationLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.SiteLocked as exc:
            raise patient_write.WriteLocked() from exc
        except write_fence.OperationConflict as exc:
            raise patient_write.WriteConflict() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
