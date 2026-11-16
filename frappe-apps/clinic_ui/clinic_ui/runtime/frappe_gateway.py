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
    appointment_contract,
    appointment_write,
    config,
    deployment_marker,
    fence_store,
    patient_write,
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
NO_STORE_HEADER = "Cache-Control"
NO_STORE_VALUE = "no-store"
DENIAL_MESSAGE = "Not permitted"
ANONYMOUS_STATUS = 401


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
        self, native_appointment_id, *, base_modified, expected=None
    ):
        """Lock and freshly re-verify one writable target before any save.

        Order: the SQLite fence is already held, then this method restarts the
        native transaction snapshot, locks the appointment row with
        ``FOR UPDATE``, checks the pinned boundary, closed-world fields, links
        and settings, then locks the Patient and Appointment Type rows in that
        deterministic order and verifies the exact fetched values. Every value
        used after the lock is the currently committed value.
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

        if not patient_write.modified_matches(
            self._text_value(getattr(doc, "modified", None)), base_modified
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
