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
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date as _date, datetime as _datetime
from clinic_ui.endpoints.linked_encounter import LinkedEncounterError

from clinic_ui.core import (
    admin_contract,
    appointment_contract,
    appointment_write,
    config,
    deployment_marker,
    encounter_contract,
    encounter_write,
    fence_store,
    finance_contract,
    patient_write,
    policy,
    procedure_contract,
    procedure_write,
    purchase_contract,
    stock_contract,
    write_fence,
)
from clinic_ui.core.text import is_valid_text, normalize_identifier
from clinic_ui.runtime.traceback import UNSAFE_V1_KEYS, WRITE_ERROR_CODES


PATIENT_DOCTYPE = "Patient"
APPOINTMENT_DOCTYPE = "Patient Appointment"
_ENCOUNTER_CHILD_FIELDS = (
    "symptoms", "diagnosis", "codification_table", "drug_prescription",
    "lab_test_prescription", "procedure_prescription", "therapies",
)
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
# The linked booking values the encounter path revalidates on the locked row:
# ``Patient Encounter.appointment_type`` is installed required and derived, and
# the patient/status pair is the completion cascade source.
APPOINTMENT_ENCOUNTER_FIELDS = ("patient", "status", "appointment_type")
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
# The only Clinical Procedure values a read surface may return. ``status`` is a
# DocField with a permlevel and can be masked; ``name`` and ``docstatus`` are
# standard columns without a permlevel or mask entry, read only after the
# document and field-level checks. A linked identifier is never fetched here.
PROCEDURE_STATUS_FIELD = "status"
PROCEDURE_STANDARD_NAME_FIELD = "name"
PROCEDURE_DOCSTATUS_VALUES = (0, 1, 2)
# The only Purchase Order values a converted draft result or read surface may
# return. ``status`` and ``supplier`` are DocFields with a permlevel and can be
# masked; ``name`` and ``docstatus`` are standard columns without a permlevel or
# mask entry, read only after the document and field-level checks. No item,
# quantity or amount is fetched here.
PURCHASE_ORDER_STATUS_FIELD = "status"
PURCHASE_ORDER_SUPPLIER_FIELD = "supplier"
PURCHASE_STANDARD_NAME_FIELD = "name"
PURCHASE_DOCSTATUS_VALUES = (0, 1, 2)
APPOINTMENT_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")
# The site timestamp recorded verbatim in an appended cancel/no-show note,
# exactly as ``frappe.utils.now()`` returns it: whole seconds, or the seconds
# plus six fractional digits Frappe 16 renders (``%Y-%m-%d %H:%M:%S.%f``).
NOTE_TIMESTAMP = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{6})?$"
)
NOTE_TIMESTAMP_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")
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
    "complaint": "Patient Encounter Symptom",
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
    medication_count: int
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
            "medication_count": self.medication_count,
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


@dataclass(frozen=True)
class ServiceRequestLockedState:
    """Freshly locked Service Request values passed to the creation endpoint."""

    doc: object
    exists: bool
    docstatus: object
    template_dt: str | None
    template_dn: str | None
    patient: str | None
    procedure_template: str | None
    procedure_exists: bool
    consume_stock: bool

    def as_dict(self):
        return {
            "exists": self.exists,
            "docstatus": self.docstatus,
            "template_dt": self.template_dt,
            "template_dn": self.template_dn,
            "patient": self.patient,
            "procedure_template": self.procedure_template,
            "procedure_exists": self.procedure_exists,
            "consume_stock": self.consume_stock,
        }


class EncounterWriteLease:
    """A held encounter fence lease that claims only when dispatched.

    ``Lease.dispatch`` is the one call that appends ``DISPATCHED``, so every
    authorization, configuration and target decision taken between the lease and
    the dispatch writes nothing when it refuses.
    """

    __slots__ = ("_lease",)

    def __init__(self, lease):
        self._lease = lease

    def dispatch(self):
        """Claim the operation key under the held lock, or map the refusal."""

        try:
            return self._lease.dispatch()
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


class LinkedFieldAccessError(LinkedEncounterError):
    """A requested linked-workflow field is not readable or writable."""


class LinkedNoteMismatch(LinkedEncounterError):
    """A note no longer matches the encounter context."""


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

    def refresh_site_config(self):
        """Drop the cached site configuration so the next gate rereads the file.

        Only the encounter write path calls this: it runs a second gate under the
        held lease and must see the file as it is now, not as it was when this
        request first read it. Every other route keeps the single read it already
        makes.
        """

        self._site_config = None

    def setting(self, key):
        return self._configuration().get(key)

    def linked_resolve(self, ref):
        """Resolve identity only; native permissions remain authoritative."""
        from types import SimpleNamespace
        from clinic_ui.core import config, appointment_write
        from clinic_ui.endpoints import appointments

        fixtures = config._appointment_allowlist(self.setting(config.APPOINTMENTS_KEY))
        if fixtures is None or any(alias == ref for alias, _ in fixtures):
            raise LinkedEncounterError("invalid appointment reference")
        if ref.startswith(appointment_write.ALIAS_PREFIX):
            if not appointment_write.is_alias_shaped(ref):
                raise LinkedEncounterError("invalid appointment reference")
            secret = self.setting(config.EPOCH_SECRET_KEY)
            if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
                raise LinkedEncounterError("invalid appointment reference")
            settings = SimpleNamespace(epoch_secret=secret.encode("utf-8"))
            matches = [item for item in appointments.discover_booked(self, settings)
                       if item.workspace_appointment_id == ref]
            if len(matches) != 1:
                raise LinkedEncounterError("invalid appointment reference")
            return matches[0].native_appointment_id
        return ref

    def _linked_fields(self, doctype, fields, ptype="read", parent_meta=None):
        meta = self.frappe.get_meta(doctype)
        authority = parent_meta or meta
        levels = set(authority.get_permlevel_access(ptype, user=self.frappe.session.user))
        masks = meta.get_masked_fields()
        if masks is None:
            raise LinkedFieldAccessError("field access unavailable")
        masked = {getattr(item, "fieldname", None) for item in masks}
        for field in fields:
            definition = meta.get_field(field)
            if definition is None or definition.permlevel not in levels or field in masked:
                raise LinkedFieldAccessError("field access denied")
        return meta

    def linked_context(self, ref):
        doc = self.frappe.get_doc("Patient Appointment", ref)
        doc.check_permission("read")
        self._linked_fields("Patient Appointment", (
            "patient", "practitioner", "status", "procedure_template",
            "therapy_type", "template_dt",
        ))
        if not doc.patient or not doc.practitioner:
            raise LinkedEncounterError("appointment missing clinical identity")
        self.frappe.get_doc("Patient", doc.patient).check_permission("read")
        return {
            "appointment": doc.name,
            "patient": doc.patient,
            "practitioner": doc.practitioner,
            "status": doc.status,
            "_alternative": bool(doc.procedure_template or doc.therapy_type
                                 or doc.template_dt in ("Therapy Type", "Clinical Procedure Template", "Observation Template")),
        }

    def _linked_doc(self, context, encounter_id):
        doc = self.frappe.get_doc("Patient Encounter", encounter_id)
        doc.check_permission("read")
        self._linked_fields("Patient Encounter", ("appointment", "patient", "practitioner"))
        if (doc.appointment != context["appointment"] or doc.patient != context["patient"]
                or doc.practitioner != context["practitioner"]):
            raise LinkedEncounterError("encounter identity mismatch")
        return doc

    def _linked_map(self, context):
        from healthcare.healthcare.doctype.patient_appointment.patient_appointment import make_encounter
        doc = make_encounter(context["appointment"])
        if (doc.appointment != context["appointment"] or doc.patient != context["patient"]
                or doc.practitioner != context["practitioner"]):
            raise LinkedEncounterError("mapped identity mismatch")
        return doc

    def linked_find(self, context):
        rows = self.frappe.get_all("Patient Encounter", filters={
            "appointment": context["appointment"], "docstatus": ["!=", 2],
        }, fields=["name"], limit_page_length=2)
        if len(rows) > 1:
            raise LinkedEncounterError("ambiguous linked encounter")
        if not rows:
            if context["status"] not in ("Open", "Checked In", "Confirmed", "Scheduled") or context["_alternative"]:
                raise LinkedEncounterError("appointment not eligible")
            self._linked_map(context)
            return None
        doc = self._linked_doc(context, rows[0]["name"])
        return {"id": doc.name, "modified": str(doc.modified), "docstatus": int(doc.docstatus)}

    def _linked_note(self, context, encounter_id, note_id):
        note = self.frappe.get_doc("Clinical Note", note_id)
        note.check_permission("read")
        self._linked_fields("Clinical Note", ("patient", "practitioner", "reference_doc", "reference_name", "note"))
        if (note.patient != context["patient"] or note.practitioner != context["practitioner"]
                or note.reference_doc != "Patient Encounter" or note.reference_name != encounter_id):
            raise LinkedNoteMismatch("note identity mismatch")
        return note

    def _linked_notes(self, context, encounter_id, offset):
        unavailable = {"state": "unavailable", "items": [], "next_offset": None}
        try:
            self._linked_fields("Clinical Note", ("patient", "practitioner", "reference_doc", "reference_name", "note"))
            rows = self.frappe.get_list("Clinical Note", filters={
                "reference_doc": "Patient Encounter", "reference_name": encounter_id,
                "patient": context["patient"], "practitioner": context["practitioner"],
            }, fields=["name"], order_by="name asc", start=offset, page_length=21)
            items = []
            for row in rows[:20]:
                note = self._linked_note(context, encounter_id, row["name"])
                items.append({"id": note.name, "modified": str(note.modified), "text": note.note})
            next_offset = None
            if len(rows) > 20:
                try:
                    self._linked_note(context, encounter_id, rows[20]["name"])
                except (self.frappe.PermissionError, self.frappe.DoesNotExistError,
                        LinkedFieldAccessError, LinkedNoteMismatch):
                    pass
                else:
                    next_offset = offset + 20
            return {"state": "available", "items": items,
                    "next_offset": next_offset}
        except (self.frappe.PermissionError, self.frappe.DoesNotExistError,
                LinkedFieldAccessError, LinkedNoteMismatch):
            return unavailable

    def _linked_medication_schema(self, ptype="read"):
        parent = self._linked_fields("Patient Encounter", ("drug_prescription",), ptype)
        table = parent.get_field("drug_prescription")
        if table.options != "Drug Prescription":
            raise LinkedFieldAccessError("medication table unavailable")
        meta = self.frappe.get_meta("Drug Prescription")
        layout = {"Section Break", "Column Break", "Tab Break", "HTML", "Heading"}
        fields = [field.fieldname for field in meta.fields
                  if field.fieldname and field.fieldtype not in layout]
        required = {"drug_code", "dosage", "period", "dosage_form", "medication_request"}
        if not required.issubset(fields):
            raise LinkedFieldAccessError("medication fields unavailable")
        links = {"drug_code": "Item", "dosage": "Prescription Dosage",
                 "period": "Prescription Duration", "dosage_form": "Dosage Form"}
        if any(not any(field.fieldname == key and field.fieldtype == "Link" and field.options == target
                       for field in meta.fields) for key, target in links.items()):
            raise LinkedFieldAccessError("medication link schema unavailable")
        self._linked_fields("Drug Prescription", fields, "read", parent)
        if ptype == "write":
            self._linked_fields("Drug Prescription", ("drug_code", "dosage", "period", "dosage_form"), "write", parent)
        return fields

    def _linked_medication_supported(self, row, fields):
        allowed = {"drug_code", "drug_name", "dosage", "period", "dosage_form",
                   "medication", "medication_request", "update_schedule"}
        return (
            all(isinstance(self._row_value(row, key), str) and self._row_value(row, key).strip()
                for key in ("drug_code", "dosage", "period", "dosage_form"))
            and not self._row_value(row, "medication_request")
            and all(self._row_value(row, key) in (None, "", 0, 0.0, False)
                    for key in fields if key not in allowed)
            and self._row_value(row, "update_schedule") in (None, 1, True)
        )

    def _linked_medications(self, doc):
        unavailable = {"state": "unavailable", "items": [], "complete": False}
        try:
            fields = self._linked_medication_schema()
        except (self.frappe.PermissionError, LinkedFieldAccessError):
            return unavailable
        rows = doc.drug_prescription
        items = []
        for row in rows[:100]:
            items.append({
                "id": self._row_value(row, "name"),
                **{key: self._row_value(row, key) for key in
                   ("drug_code", "dosage", "period", "dosage_form", "medication_request")},
                "details": {key: self._row_value(row, key) for key in (
                    "drug_name", "comment", "interval", "interval_uom", "strength",
                    "strength_uom", "dosage_by_interval", "number_of_repeats_allowed",
                    "intent", "priority", "update_schedule",
                ) if key in fields},
                "supported": self._linked_medication_supported(row, fields),
            })
        return {"state": "available", "items": items, "complete": len(rows) <= 100}

    def linked_project(self, context, encounter, note_offset=0):
        public_context = {key: value for key, value in context.items() if not key.startswith("_")}
        if encounter is None:
            return {"context": public_context, "encounter": None,
                    "medications": {"state": "available", "items": [], "complete": True},
                    "notes": {"state": "available", "items": [], "next_offset": None}}
        doc = self._linked_doc(context, encounter["id"])
        meta = self.frappe.get_meta("Patient Encounter")
        symptoms = self._encounter_read_children(doc, meta, "symptoms", "Patient Encounter Symptom", "complaint")
        diagnoses = self._encounter_read_children(doc, meta, "diagnosis", "Patient Encounter Diagnosis", "diagnosis")
        if symptoms is None or diagnoses is None:
            raise LinkedEncounterError("clinical fields unavailable")
        return {
            "context": public_context,
            "encounter": {**encounter, "symptoms": symptoms, "diagnoses": diagnoses},
            "medications": self._linked_medications(doc),
            "notes": self._linked_notes(context, doc.name, note_offset),
        }

    def linked_save(self, context, encounter, clinical):
        if encounter is None:
            doc = self._linked_map(context)
        else:
            doc = self._linked_doc(context, encounter["id"])
            if str(doc.modified) != encounter["modified"] or int(doc.docstatus) != 0:
                raise LinkedEncounterError("stale encounter")
            doc.check_permission("write")
        for key, table, child, field in (
            ("symptoms", "symptoms", "Patient Encounter Symptom", "complaint"),
            ("diagnoses", "diagnosis", "Patient Encounter Diagnosis", "diagnosis"),
        ):
            if key in clinical:
                meta = self._linked_fields("Patient Encounter", (table,), "write")
                self._linked_fields(child, (field,), "write", meta)
                doc.set(table, [])
                for value in clinical[key]:
                    doc.append(table, {field: value})
        if "medications" in clinical:
            fields = self._linked_medication_schema("write")
            operations = clinical["medications"]
            if encounter is None and operations["edit"]:
                raise LinkedEncounterError("medication edit requires a persisted draft")
            for edit in operations["edit"]:
                matches = [row for row in doc.drug_prescription if self._row_value(row, "name") == edit["id"]]
                if len(matches) != 1 or not self._linked_medication_supported(matches[0], fields):
                    raise LinkedEncounterError("medication row unavailable or unsupported")
                row = matches[0]
                self.frappe.get_doc("Item", self._row_value(row, "drug_code")).check_permission("read")
                for key, doctype in (("dosage", "Prescription Dosage"), ("period", "Prescription Duration"), ("dosage_form", "Dosage Form")):
                    self.frappe.get_doc(doctype, edit[key]).check_permission("read")
                    if isinstance(row, dict):
                        row[key] = edit[key]
                    else:
                        row.set(key, edit[key])
            for add in operations["add"]:
                for key, doctype in (("drug_code", "Item"), ("dosage", "Prescription Dosage"), ("period", "Prescription Duration"), ("dosage_form", "Dosage Form")):
                    self.frappe.get_doc(doctype, add[key]).check_permission("read")
                doc.append("drug_prescription", add)
        self._linked_fields("Patient Encounter", ("submit_orders_on_save",), "write")
        doc.submit_orders_on_save = 0
        if encounter is None:
            doc.insert()
        else:
            doc.save()
        if "note" in clinical:
            incoming = clinical["note"]
            if incoming["id"] is None:
                self._linked_fields("Clinical Note", ("patient", "practitioner", "reference_doc", "reference_name", "note"), "write")
                note = self.frappe.new_doc("Clinical Note")
                note.patient = context["patient"]
                note.practitioner = context["practitioner"]
                note.reference_doc = "Patient Encounter"
                note.reference_name = doc.name
                note.note = incoming["text"]
                note.insert()
            else:
                note = self._linked_note(context, doc.name, incoming["id"])
                if note.name != incoming["id"] or str(note.modified) != incoming["modified"]:
                    raise LinkedEncounterError("stale note")
                note.check_permission("write")
                self._linked_fields("Clinical Note", ("note",), "write")
                note.note = incoming["text"]
                note.save()
        return {"id": doc.name, "modified": str(doc.modified), "docstatus": int(doc.docstatus)}

    def linked_complete(self, context, encounter):
        doc = self._linked_doc(context, encounter["id"])
        if str(doc.modified) != encounter["modified"] or int(doc.docstatus) != 0:
            raise LinkedEncounterError("stale encounter")
        self._linked_fields("Patient Encounter", ("submit_orders_on_save",), "write")
        doc.submit_orders_on_save = 0
        doc.submit()
        return {"id": doc.name, "modified": str(doc.modified), "docstatus": int(doc.docstatus)}

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

    def fresh_user_roles(self, user):
        """Read ``user``'s native roles from the ``Has Role`` rows, uncached.

        ``frappe.get_roles`` memoises its answer in the framework cache
        (``frappe.cache.hget("roles", user, ...)`` in the pinned v16), so a role
        granted or revoked mid-request stays invisible to it. This read goes
        straight to the installed table with the pinned parent/parenttype filters
        and the pinned automatic-role exclusion. It never calls ``get_roles`` and
        never writes or purges a cache, so it can neither mask nor manufacture a
        role.

        ``None`` means the read failed or could not be trusted; callers must
        refuse. An empty tuple means the read succeeded and the user has no
        stored role row.
        """

        if not isinstance(user, str) or not user.strip():
            return None
        try:
            rows = self.frappe.db.get_all(
                "Has Role",
                filters={
                    "parenttype": "User",
                    "parent": user,
                    "role": ["not in", list(policy.AUTOMATIC_FRAMEWORK_ROLES)],
                },
                pluck="role",
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        roles = tuple(row for row in rows if isinstance(row, str) and row)
        if len(roles) != len(rows):
            return None
        return roles

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

    def is_native_permission_denial(self, exc):
        """True only for the installed framework's own ``PermissionError`` type.

        Identity-based on purpose: the endpoint layer never classifies by class
        name, so an unrelated exception that merely happens to be called
        ``PermissionError`` cannot turn an uncertain write into a definite
        denial. When the framework type is unreadable this returns ``False``.
        """

        try:
            return isinstance(exc, self.frappe.PermissionError)
        except Exception:
            return False

    def is_native_validation_error(self, exc):
        """True only for the installed framework's own ``ValidationError`` family.

        The pinned v16 hierarchy makes ``MandatoryError`` and
        ``LinkValidationError`` subclasses of ``frappe.exceptions.
        ValidationError`` (``PermissionError`` is a separate type), so one
        identity check covers all three. Identity-based on purpose: a class that
        merely has a similar name stays an uncertain outcome. When the framework
        type is unreadable this returns ``False``.
        """

        try:
            return isinstance(exc, self.frappe.ValidationError)
        except Exception:
            return False

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

    def list_readable_patients(self, limit):
        """Bounded, permission-aware Patient names for the current user.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used on this discovery path. The caller still
        applies the document and field-level display-name checks per record. Any
        failure returns ``None`` so discovery omits the native slice instead of
        guessing.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        try:
            rows = self.frappe.get_list(
                PATIENT_DOCTYPE,
                fields=["name"],
                order_by="modified desc, name asc",
                limit_page_length=limit,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        names = []
        for row in rows:
            name = (
                row.get("name")
                if hasattr(row, "get")
                else getattr(row, "name", None)
            )
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

    def list_readable_appointments(self, limit):
        """Bounded, permission-aware Patient Appointment names for the user.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used on this discovery path. The caller still
        applies the document and field-level checks per record. Any failure
        returns ``None`` so discovery omits the native slice instead of
        guessing.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        try:
            rows = self.frappe.get_list(
                APPOINTMENT_DOCTYPE,
                fields=["name"],
                order_by="modified desc, name asc",
                limit_page_length=limit,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        names = []
        for row in rows:
            name = (
                row.get("name")
                if hasattr(row, "get")
                else getattr(row, "name", None)
            )
            if isinstance(name, str) and name.strip():
                names.append(name.strip())
        return names

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
        endpoint only. The converted development lane may publish them as the
        record's client-facing identifier, but only behind the current user's
        own native document permissions; this adapter never decides that.
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

    def _encounter_read_denied(self):
        self.frappe.throw("Not permitted", self.frappe.PermissionError)

    def _resolve_encounter_alias(self, ref):
        from clinic_ui.core.policy import PATIENT_VIEW_ROLE_CODES
        from clinic_ui.endpoints import aliases, common

        settings, session, role = common.authorize(self, PATIENT_VIEW_ROLE_CODES)
        resolved = aliases.resolve_read(self, settings, session.user, role, ref)
        return resolved.native_patient_id

    def _encounter_patient_ref(self, ref):
        from clinic_ui.core import config, patient_alias
        from clinic_ui.core.text import is_valid_identifier, normalize_identifier

        ref = normalize_identifier(ref)
        if not is_valid_identifier(ref):
            self._encounter_read_denied()
        if ref.startswith(patient_alias.PREFIX) and patient_alias.ALIAS_RE.fullmatch(ref) is None:
            self._encounter_read_denied()
        configured = self.setting(config.ALLOWLIST_KEY)
        is_static = isinstance(configured, (list, tuple)) and any(
            isinstance(item, dict) and item.get("workspace_patient_id") == ref
            for item in configured
        )
        if is_static or ref.startswith(patient_alias.PREFIX):
            ref = self._resolve_encounter_alias(ref)
        if not ref or not self.native_patient_can_read(ref):
            self._encounter_read_denied()
        return ref

    def _encounter_read_meta(self):
        meta = self.frappe.get_meta(ENCOUNTER_DOCTYPE)
        if not self._fields_readable(meta, ("patient", "practitioner", "encounter_date")):
            self._encounter_read_denied()
        return meta

    def _encounter_summary(self, row):
        patient = self._row_value(row, "patient")
        if not patient or not self.native_patient_can_read(patient):
            self._encounter_read_denied()
        name = self.native_patient_display_name(patient)
        if name is None:
            self._encounter_read_denied()
        return {
            "id": self._row_value(row, "name"),
            "patientId": patient,
            "patientName": name,
            "practitioner": self._text_value(self._row_value(row, "practitioner")),
            "encounterDate": self._text_value(self._row_value(row, "encounter_date")),
            "docstatus": self._row_value(row, "docstatus"),
        }

    def list_patient_encounters(self, patient_ref, offset=0):
        self.mark_no_store()
        self.suppress_traceback()
        patient = self._encounter_patient_ref(patient_ref)
        if isinstance(offset, bool) or not (
            isinstance(offset, int)
            or isinstance(offset, str)
            and len(offset) <= 7
            and offset.isascii()
            and offset.isdecimal()
        ):
            self._encounter_read_denied()
        offset = int(offset)
        if offset < 0 or offset > 1000000:
            self._encounter_read_denied()
        self._encounter_read_meta()
        rows = self.frappe.get_list(
            ENCOUNTER_DOCTYPE,
            filters={"patient": patient},
            fields=["name", "patient", "practitioner", "encounter_date", "docstatus"],
            order_by="encounter_date desc, name desc",
            start=offset,
            page_length=21,
        )
        items = []
        for row in rows[:20]:
            try:
                doc = self.frappe.get_doc(ENCOUNTER_DOCTYPE, self._row_value(row, "name"))
                if not self.frappe.has_permission(
                    ENCOUNTER_DOCTYPE, ptype="read", doc=doc, user=self.frappe.session.user
                ):
                    continue
                if self._row_value(doc, "patient") != patient:
                    continue
            except (self.frappe.PermissionError, self.frappe.DoesNotExistError):
                continue
            items.append(self._encounter_summary(doc))
        return {
            "items": items,
            "nextOffset": offset + 20 if len(rows) > 20 and offset + 20 <= 1000000 else None,
        }

    def _encounter_read_children(self, doc, meta, table, doctype, field):
        try:
            definition = meta.get_field(table)
            parent_masks = meta.get_masked_fields()
            if (
                not definition
                or definition.options != doctype
                or parent_masks is None
                or any(not getattr(item, "fieldname", None) for item in parent_masks)
                or not self._fields_readable(meta, (table,))
            ):
                return None
            child_meta = self.frappe.get_meta(doctype)
            child_field = child_meta.get_field(field)
            accessible = meta.get_permlevel_access("read", user=self.frappe.session.user)
            masked_fields = child_meta.get_masked_fields()
            if masked_fields is None or any(
                not isinstance(getattr(item, "fieldname", None), str)
                or not item.fieldname.strip()
                for item in masked_fields
            ):
                return None
            masked = {item.fieldname for item in masked_fields}
            if not child_field or child_field.permlevel not in accessible or field in masked:
                return None
            rows = getattr(doc, table)
            if not isinstance(rows, (list, tuple)):
                return None
            values = [self._row_value(row, field) for row in rows]
            if any(not isinstance(value, str) for value in values):
                return None
            return values
        except Exception:
            return None

    def view_patient_encounter(self, encounter_id):
        from clinic_ui.core.text import is_valid_identifier, normalize_identifier

        self.mark_no_store()
        self.suppress_traceback()
        encounter_id = normalize_identifier(encounter_id)
        if not is_valid_identifier(encounter_id):
            self._encounter_read_denied()
        doc = self.frappe.get_doc(ENCOUNTER_DOCTYPE, encounter_id)
        if not self.frappe.has_permission(
            ENCOUNTER_DOCTYPE, ptype="read", doc=doc, user=self.frappe.session.user
        ):
            self._encounter_read_denied()
        meta = self._encounter_read_meta()
        result = self._encounter_summary(doc)
        result["symptoms"] = self._encounter_read_children(
            doc, meta, "symptoms", "Patient Encounter Symptom", "complaint"
        )
        result["diagnoses"] = self._encounter_read_children(
            doc, meta, "diagnosis", "Patient Encounter Diagnosis", "diagnosis"
        )
        return result

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
        for shape in NOTE_TIMESTAMP_FORMATS:
            try:
                _datetime.strptime(value, shape)
            except ValueError:
                continue
            return value
        raise patient_write.WriteUncertain()

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

    def native_gender_options(self, limit):
        """Bounded, permission-aware Gender master names for the current user.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used. A caller without role-level Gender read
        authority, an invalid limit, a failed query or an unexpected shape
        returns ``None`` so the endpoint refuses instead of inventing options.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission("Gender", ptype="read", user=user):
                return None
            rows = frappe.get_list(
                "Gender",
                fields=["name"],
                order_by="name asc",
                limit_page_length=limit,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        names = []
        for row in rows:
            name = (
                row.get("name")
                if hasattr(row, "get")
                else getattr(row, "name", None)
            )
            if isinstance(name, str) and is_valid_text(name):
                names.append(normalize_identifier(name))
        return names

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

    def create_unlinked_encounter_draft(self, body):
        """Insert one unlinked draft under Frappe's current user permissions."""
        fields = ("patient", "practitioner", "appointment_type")
        if (
            not isinstance(body, Mapping)
            or set(body) != set(fields)
            or any(not isinstance(body[field], str) or not body[field].strip() for field in fields)
        ):
            self.frappe.throw("Invalid encounter draft", self.frappe.ValidationError)
        doc = self.frappe.new_doc(ENCOUNTER_DOCTYPE)
        for field in fields:
            setattr(doc, field, body[field])
        doc.docstatus = 0
        doc.appointment = ""
        doc.submit_orders_on_save = 0
        for field in _ENCOUNTER_CHILD_FIELDS:
            setattr(doc, field, [])
        doc.insert()
        return {"id": doc.name, "docstatus": doc.docstatus}

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
        """Resolve the installed field names through the metadata cache.

        Meta fieldnames, not ``get_valid_columns()``: the latter excludes Table
        fields in frappe 16.54.0, and ``Patient Encounter.symptoms``,
        ``diagnosis`` and the three prescription tables are all Table fields.
        The installed ``default_fields`` (``name``, ``owner``, ``creation``,
        ``modified``, ``modified_by``, ``docstatus``, ``idx``) are ordinary
        columns, not ``DocField`` rows, so they are unioned in explicitly with an
        unknown shape; only frappe's own default columns are admitted by name.

        The typed shape ``(fieldtype, options)`` is what the closed-world table
        check needs: a scalar column with a table's name would otherwise pass.
        """

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
            default_fields = getattr(meta, "default_fields", None) or ()
            installed = {
                field.fieldname: (
                    getattr(field, "fieldtype", None),
                    getattr(field, "options", None),
                )
                for field in meta.fields
            }
            for name in default_fields:
                if isinstance(name, str):
                    installed.setdefault(name, (None, None))
            fields[doctype] = installed
        self._encounter_fields_cache = fields
        return fields

    def _encounter_note_reference_fields(self):
        """The installed Clinical Note reference pair, or ``(None, None)``."""

        return encounter_contract.note_reference_fields(self._encounter_meta_fields())

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

    def _encounter_note_values(self, native_encounter_id):
        """The newest linked note's identity and sections under read authority.

        Deny-only: the role-level ``Clinical Note`` read and the ``note``
        field's permlevel/mask are checked before any note row is queried, and
        the found row's own document read is checked before its content is
        read. The locating query asks for the name only, so no content column is
        ever fetched before the document authority is established. ``note`` and
        ``modified`` are read together in one ``get_value`` after that check, so
        the text and the never-rewrite preimage come from the same row read and
        cannot skew; a missing, failed or malformed result returns
        ``(None, None, None, None, None)``. No permission is elevated and no
        framework cache is cleared or reimplemented here.
        """

        missing = (None, None, None, None, None)
        reference_doctype_field, reference_name_field = (
            self._encounter_note_reference_fields()
        )
        if not reference_doctype_field or not reference_name_field:
            # An unresolved pair reads no note, so completion stays blocked
            # instead of trusting a note nobody linked.
            return missing
        if self.native_doctype_can_read(CLINICAL_NOTE_DOCTYPE) is not True:
            return missing
        try:
            meta = self.frappe.get_meta(CLINICAL_NOTE_DOCTYPE)
            if not self._fields_readable(meta, ("note",)):
                return missing
        except Exception:
            return missing
        try:
            notes = self.frappe.get_all(
                CLINICAL_NOTE_DOCTYPE,
                filters={
                    reference_doctype_field: ENCOUNTER_DOCTYPE,
                    reference_name_field: native_encounter_id,
                },
                fields=["name"],
                order_by="creation desc",
                limit=1,
            ) or ()
        except Exception:
            return missing
        if not notes:
            return missing
        row = notes[0]
        clinical_note = self._text_value(
            row.get("name") if hasattr(row, "get") else None
        )
        if not clinical_note:
            return missing
        if (
            self.native_doctype_can_read(CLINICAL_NOTE_DOCTYPE, clinical_note)
            is not True
        ):
            return missing
        try:
            content = self.frappe.db.get_value(
                CLINICAL_NOTE_DOCTYPE,
                clinical_note,
                ["note", "modified"],
                as_dict=True,
            )
        except Exception:
            return missing
        if not hasattr(content, "get"):
            return missing
        note = content.get("note")
        persisted_modified = content.get("modified")
        # Keep the persisted ``str()`` representation: the amendment re-reads
        # ``modified`` the same way and compares it byte for byte, so an ISO
        # ``T`` rendering here would refuse a genuine amendment after re-enable.
        clinical_note_modified = self._text_value(
            str(persisted_modified) if persisted_modified else None
        )
        if not clinical_note_modified:
            # The amendment preimage is this ``modified``: a blank one cannot be
            # re-verified, so the note is treated as unreadable.
            return missing
        sections = encounter_contract.parse_note_text(note)
        return (
            clinical_note,
            clinical_note_modified,
            sections["assessment"] or None,
            sections["plan"] or None,
            sections["followup"] or None,
        )

    def _encounter_state_values(self, doc):
        """Build the contract detail from a locked encounter document."""

        native = doc.name
        appointment = self._text_value(getattr(doc, "appointment", None))
        appointment_status = None
        if appointment:
            appointment_status = self._text_value(
                self.frappe.db.get_value(APPOINTMENT_DOCTYPE, appointment, "status")
            )
        complaint_field = self._encounter_field(
            encounter_contract.SYMPTOM_CHILD_DOCTYPE, "complaint"
        )
        complaint = False
        if complaint_field:
            complaint = bool(
                self.frappe.db.count(
                    encounter_contract.SYMPTOM_CHILD_DOCTYPE,
                    {"parent": native, complaint_field: ("!=", "")},
                )
            )
        diagnosis_count = int(
            self.frappe.db.count(
                "Patient Encounter Diagnosis", {"parent": native}
            )
        )
        # The row count, not the codes: a blank drug code is still a medication
        # row the reviewed contract must not rewrite or delete.
        medication_count = int(
            self.frappe.db.count("Drug Prescription", {"parent": native})
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
        clinical_note, clinical_note_modified, assessment, plan, followup = (
            self._encounter_note_values(native)
        )
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
            "medication_count": medication_count,
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
        complaint_field = self._encounter_field(
            encounter_contract.SYMPTOM_CHILD_DOCTYPE, "complaint"
        )
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
        reference_doctype_field, reference_name_field = (
            self._encounter_note_reference_fields()
        )
        if not reference_doctype_field or not reference_name_field:
            raise patient_write.WriteUncertain()
        practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
        try:
            doc = self.frappe.new_doc(CLINICAL_NOTE_DOCTYPE)
            doc.patient = patient
            doc.practitioner = practitioner
            doc.note = text
            setattr(doc, reference_doctype_field, ENCOUNTER_DOCTYPE)
            setattr(doc, reference_name_field, native_encounter_id)
            doc.insert()
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_appointment_encounter_type(self, native_appointment_id):
        """The linked booking's ``appointment_type``, or ``None``.

        ``Patient Encounter.appointment_type`` is installed required and is never
        a client value, so the create decision needs the booking's own type before
        the fence. The read reuses the cancel path's document and field-level
        checks so an unreadable or masked column fails closed.
        """

        detail = self.native_appointment_view(native_appointment_id)
        if detail is None:
            return None
        try:
            meta = self.frappe.get_meta(APPOINTMENT_DOCTYPE)
            if not self._fields_readable(
                meta, APPOINTMENT_ENCOUNTER_FIELDS
            ):
                return None
            value = self._text_value(
                self.frappe.db.get_value(
                    APPOINTMENT_DOCTYPE, native_appointment_id, "appointment_type"
                )
            )
        except Exception:
            return None
        if not isinstance(value, str) or not value.strip():
            return None
        return value.strip()

    def lock_linked_appointment(self, native_appointment_id):
        """Lock the linked booking and return its decision values, or ``None``.

        The fence serialises this contract's own operations only: another native
        writer can edit the booking at any moment, and a plain ``get_value`` is
        not a stable read. ``FOR UPDATE`` holds the row to the end of the
        transaction, so the patient, status and appointment_type returned here
        are the committed values for every decision after this call.

        No snapshot restart happens here on purpose: the fence body already ran
        ``reset_native_read_snapshot()``, and a second rollback would drop row
        locks taken after it. ``None`` covers a missing, denied, restricted or
        unreadable row, so the caller refuses *before* dispatch — an exception
        after dispatch would leave the operation uncertain and latch the site.
        """

        return self._lock_linked_appointment(native_appointment_id, "write")

    def lock_linked_appointment_for_read(self, native_appointment_id):
        """Lock the linked booking for a caller that holds read authority only.

        The completion cascade reads the booking's committed status and never
        writes the booking, so it must not require ``Patient Appointment``
        ``write``: that right was never part of this contract. The row lock, the
        readable-field check and the refusal on a denied read are the same.
        """

        return self._lock_linked_appointment(native_appointment_id, "read")

    def _lock_linked_appointment(self, native_appointment_id, ptype):
        try:
            frappe = self.frappe
            user = frappe.session.user
            doc = frappe.get_doc(
                APPOINTMENT_DOCTYPE, native_appointment_id, for_update=True
            )
            if doc is None:
                return None
            if not frappe.has_permission(
                APPOINTMENT_DOCTYPE,
                ptype=ptype,
                doc=native_appointment_id,
                user=user,
            ):
                return None
            meta = frappe.get_meta(APPOINTMENT_DOCTYPE)
            if not self._fields_readable(meta, APPOINTMENT_ENCOUNTER_FIELDS):
                return None
            values = {
                field: self._text_value(getattr(doc, field, None))
                for field in APPOINTMENT_ENCOUNTER_FIELDS
            }
        except Exception:
            return None
        return values

    def create_native_encounter_draft(self, payload):
        """Insert the draft encounter (docstatus 0) and its separate documents."""

        patient = payload["_native_patient_id"]
        appointment = payload["_native_appointment_id"]
        appointment_type_field = self._encounter_field(
            ENCOUNTER_DOCTYPE, "appointment_type"
        )
        try:
            doc = self.frappe.new_doc(ENCOUNTER_DOCTYPE)
            doc.patient = patient
            doc.practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
            doc.company = self.setting(config.ENCOUNTER_COMPANY_KEY)
            doc.appointment = appointment
            if appointment_type_field:
                # Installed required and derived from the linked booking, which
                # the endpoint already refused to create without.
                setattr(
                    doc,
                    appointment_type_field,
                    payload.get("_appointment_type"),
                )
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
        reference_doctype_field, reference_name_field = (
            self._encounter_note_reference_fields()
        )
        if not reference_doctype_field or not reference_name_field:
            raise patient_write.WriteUncertain()
        try:
            doc = self.frappe.new_doc(CLINICAL_NOTE_DOCTYPE)
            doc.patient = state.patient
            doc.practitioner = self.setting(config.ENCOUNTER_PRACTITIONER_KEY)
            doc.note = note_text
            setattr(doc, reference_doctype_field, ENCOUNTER_DOCTYPE)
            setattr(doc, reference_name_field, state.doc.name)
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

    def lookup_encounter_operation(self, actor, kind, operation_key):
        """Read the latest durable record for one encounter operation.

        Strictly read-only: no claim, no dispatch, no latch, no unlock and no
        retry. The raw state is returned so the caller can refuse a non-terminal
        operation itself instead of turning it into a lock response.
        """

        if kind not in write_fence.ENCOUNTER_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            return ledger.lookup(
                actor=actor, kind=kind, operation_key=operation_key
            )
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

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
    def encounter_write_lease(self, actor, kind, operation_key, payload_digest):
        """Hold the encounter fence lock; the caller decides when to dispatch.

        Nothing is appended until ``dispatch`` is called, so authorization,
        configuration and target eligibility can be refreshed under the held
        lock and refused without burning the key or latching the site.
        """

        if kind not in write_fence.ENCOUNTER_OPERATION_KINDS:
            raise patient_write.WriteUncertain()
        ledger = self._ledger()
        try:
            with ledger.lease(
                actor=actor,
                kind=kind,
                operation_key=operation_key,
                payload_digest=payload_digest,
            ) as lease:
                yield EncounterWriteLease(lease)
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
        request_path = frappe.get_app_path(
            "healthcare", *procedure_contract.SERVICE_REQUEST_SOURCE_PATH
        )
        with open(request_path, "rb") as handle:
            request_sha = hashlib.sha256(handle.read()).hexdigest()

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
            "source_sha256": {
                "clinical_procedure": source_sha,
                "service_request": request_sha,
            },
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
                doctype: {
                    event: tuple(handlers or ())
                    for event, handlers in (
                        doc_events.get(doctype) or {}
                    ).items()
                }
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
        """Resolve the installed field names through the metadata cache.

        Meta fieldnames, not ``get_valid_columns()``: the latter excludes Table
        fields in frappe 16.54.0, and ``Clinical Procedure.items`` is one.

        The installed ``default_fields`` (``name``, ``owner``, ``creation``,
        ``modified``, ``modified_by``, ``docstatus``, ``idx``) are ordinary
        columns, not ``DocField`` rows, so they are unioned in explicitly:
        ``Stock Entry.docstatus`` lives only there and the submittability check
        requires it. The closed-world check stays strict for every other field —
        only frappe's own default columns are admitted by name.
        """

        cached = getattr(self, "_procedure_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in procedure_contract.REQUIRED_FIELDS:
            meta = get_meta(doctype)
            default_fields = getattr(meta, "default_fields", None) or ()
            fields[doctype] = {
                field.fieldname for field in meta.fields
            } | {
                name for name in default_fields if isinstance(name, str)
            }
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

    def native_procedure_can_read(self, native_procedure_id):
        """The current user's own document-level Clinical Procedure read authority."""

        try:
            return bool(
                self.frappe.has_permission(
                    procedure_contract.PROCEDURE_DOCTYPE,
                    ptype="read",
                    doc=native_procedure_id,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def native_procedure_read_detail(self, native_procedure_id):
        """The committed procedure status under the current user's read authority.

        The converted lane reads its own result back after the one commit. This
        is the same three-field, field-level-checked read as
        :meth:`native_procedure_view`: a denied document, a restricted or masked
        ``status`` and a malformed row all return ``None``, so the caller
        reports an uncertain outcome rather than publishing an unmasked status.
        """

        return self.native_procedure_view(native_procedure_id)

    def _procedure_field_readable(self, fieldname):
        """True when one Clinical Procedure DocField is readable and unmasked.

        ``status`` and the ``service_request`` filter are DocFields that carry a
        permlevel and can be masked. A missing, restricted or masked field is
        refused before any value is fetched or any query is built.
        """

        meta = self.frappe.get_meta(procedure_contract.PROCEDURE_DOCTYPE)
        accessible_permlevels = meta.get_permlevel_access(
            "read", user=self.frappe.session.user
        )
        field_definition = meta.get_field(fieldname)
        if (
            field_definition is None
            or field_definition.permlevel not in accessible_permlevels
        ):
            return False
        masked_fields = meta.get_masked_fields() or ()
        return not any(
            getattr(masked, "fieldname", None) == fieldname
            for masked in masked_fields
        )

    def native_procedure_view(self, native_procedure_id):
        """Three-field procedure read under document and field-level rights.

        The document read permission is checked first. ``status`` must then
        exist at a readable permlevel and must not be masked for the current
        user; ``name`` and ``docstatus`` are standard columns without a
        permlevel or mask entry and are read only after that check. No linked
        identifier (patient, practitioner, template, Service Request) is read
        here. ``None`` covers a denied document, a restricted or masked status,
        a malformed row and any adapter failure.
        """

        if (
            not isinstance(native_procedure_id, str)
            or not native_procedure_id.strip()
        ):
            return None
        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                procedure_contract.PROCEDURE_DOCTYPE,
                ptype="read",
                doc=native_procedure_id,
                user=user,
            ):
                return None
            if not self._procedure_field_readable(PROCEDURE_STATUS_FIELD):
                return None
            row = frappe.db.get_value(
                procedure_contract.PROCEDURE_DOCTYPE,
                native_procedure_id,
                [PROCEDURE_STANDARD_NAME_FIELD, PROCEDURE_STATUS_FIELD, "docstatus"],
                as_dict=True,
            )
        except Exception:
            return None
        if row is None:
            return None
        get = (
            row.get
            if hasattr(row, "get")
            else (lambda key, default=None: default)
        )
        name = self._text_value(get(PROCEDURE_STANDARD_NAME_FIELD))
        status = self._text_value(get(PROCEDURE_STATUS_FIELD))
        docstatus = get("docstatus")
        if not name or not status:
            return None
        if (
            not isinstance(docstatus, int)
            or isinstance(docstatus, bool)
            or docstatus not in PROCEDURE_DOCSTATUS_VALUES
        ):
            return None
        return {
            "procedure_id": name,
            "status": status,
            "docstatus": docstatus,
        }

    def native_procedures_for_service_request(self, service_request, limit):
        """Bounded, permission-aware procedure names linked to one request.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used on this discovery path. The installed
        filter column must exist and pass the current user's own field-level read
        and mask check before the query is built. One row beyond the bound is
        requested, so an over-full page is reported as ``None`` rather than
        silently truncated. The caller still applies the document and field-level
        checks per record.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        try:
            filter_field = self._procedure_field(
                procedure_contract.PROCEDURE_DOCTYPE, "service_request"
            )
            if not filter_field:
                return None
            # The filter column is itself a DocField that can carry a permlevel
            # or a mask, so the same field-level read check runs before the
            # query is built. A restricted filter is refused, never dropped.
            if not self._procedure_field_readable(filter_field):
                return None
            rows = self.frappe.get_list(
                procedure_contract.PROCEDURE_DOCTYPE,
                filters={filter_field: service_request},
                fields=[PROCEDURE_STANDARD_NAME_FIELD],
                order_by="modified desc, name asc",
                limit_page_length=limit + 1,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        if len(rows) > limit:
            return None
        names = []
        for row in rows:
            name = (
                row.get(PROCEDURE_STANDARD_NAME_FIELD)
                if hasattr(row, "get")
                else getattr(row, PROCEDURE_STANDARD_NAME_FIELD, None)
            )
            if not isinstance(name, str) or not name.strip():
                return None
            names.append(name.strip())
        return names

    def native_procedure_can_create(self):
        """The acting user's native Clinical Procedure create + submit authority."""

        for ptype in ("create", "submit"):
            try:
                if not self.frappe.has_permission(
                    procedure_contract.PROCEDURE_DOCTYPE,
                    ptype=ptype,
                    user=self.frappe.session.user,
                ):
                    return False
            except Exception:
                return False
        return True

    # -- procedure creation from a submitted Service Request ---------------

    def _service_request_state(self, doc):
        """Build the creation decisions from one Service Request document."""

        native = self._text_value(doc.name)
        template_dn = self._text_value(getattr(doc, "template_dn", None))
        consume_stock = False
        if template_dn:
            try:
                consume_stock = bool(
                    self.frappe.db.get_value(
                        procedure_contract.PROCEDURE_TEMPLATE_DOCTYPE,
                        template_dn,
                        "consume_stock",
                    )
                )
            except Exception:
                raise patient_write.WriteUncertain() from None
        try:
            procedure_exists = bool(
                self.frappe.db.exists(
                    procedure_contract.PROCEDURE_DOCTYPE,
                    {"service_request": native},
                )
            )
        except Exception:
            raise patient_write.WriteUncertain() from None
        return ServiceRequestLockedState(
            doc=doc,
            exists=True,
            docstatus=getattr(doc, "docstatus", None),
            template_dt=self._text_value(getattr(doc, "template_dt", None)),
            template_dn=template_dn,
            patient=self._text_value(getattr(doc, "patient", None)),
            procedure_template=(
                template_dn
                if self._text_value(getattr(doc, "template_dt", None))
                == procedure_contract.PROCEDURE_TEMPLATE_DOCTYPE
                else None
            ),
            procedure_exists=procedure_exists,
            consume_stock=consume_stock,
        )

    def native_service_request_target(self, service_request):
        """Read the pre-fence creation decisions under native read authority.

        ``None`` means the request is unreadable or the caller may not read it;
        the endpoint denies. An absent row is reported as absent so the refusal
        can be a designed one.
        """

        try:
            if not self.frappe.db.exists(
                procedure_contract.SERVICE_REQUEST_DOCTYPE, service_request
            ):
                return {
                    "exists": False,
                    "docstatus": None,
                    "template_dt": None,
                    "template_dn": None,
                    "patient": None,
                    "procedure_template": None,
                    "procedure_exists": False,
                    "consume_stock": False,
                }
        except Exception:
            return None
        try:
            allowed = self.frappe.has_permission(
                procedure_contract.SERVICE_REQUEST_DOCTYPE,
                ptype="read",
                doc=service_request,
                user=self.frappe.session.user,
            )
        except Exception:
            return None
        if allowed is not True:
            return None
        try:
            doc = self.frappe.get_doc(
                procedure_contract.SERVICE_REQUEST_DOCTYPE, service_request
            )
            return self._service_request_state(doc).as_dict()
        except patient_write.WriteUncertain:
            return None
        except Exception:
            return None

    def lock_service_request_target(self, service_request):
        """Lock the Service Request row and re-read the creation decisions."""

        try:
            doc = self.frappe.get_doc(
                procedure_contract.SERVICE_REQUEST_DOCTYPE,
                service_request,
                for_update=True,
            )
            return self._service_request_state(doc)
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_procedure(self, state):
        """Insert and submit the Clinical Procedure for the locked request.

        The field mapping is the pinned ``make_clinical_procedure`` mapping; the
        body carries no client value. Submitting here is what gives completion
        its legal target (``docstatus`` 1, status ``Pending``), and the row is
        linked back to the Service Request it came from.
        """

        values = {}
        for source, target in procedure_contract.CREATE_FIELD_MAP:
            value = getattr(state.doc, source, None)
            if value not in (None, ""):
                values[target] = value
        try:
            doc = self.frappe.get_doc(
                {
                    "doctype": procedure_contract.PROCEDURE_DOCTYPE,
                    "patient": state.patient,
                    "procedure_template": state.template_dn,
                    "service_request": self._text_value(state.doc.name),
                    "company": self._text_value(
                        getattr(state.doc, "company", None)
                    ),
                    "invoiced": (
                        1
                        if self._text_value(
                            getattr(state.doc, "billing_status", None)
                        )
                        == "Invoiced"
                        else 0
                    ),
                    "consume_stock": 1 if state.consume_stock else 0,
                    **values,
                }
            )
            doc.insert()
            doc.submit()
            return self._text_value(doc.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_procedure_create_facts(self, native_procedure_id):
        """Closed-world re-read after one Clinical Procedure creation."""

        try:
            row = self.frappe.db.get_value(
                procedure_contract.PROCEDURE_DOCTYPE,
                native_procedure_id,
                [
                    "name",
                    "patient",
                    "procedure_template",
                    "service_request",
                    "docstatus",
                    "status",
                ],
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
        return {
            "name": self._text_value(get("name")),
            "patient": self._text_value(get("patient")),
            "procedure_template": self._text_value(
                get("procedure_template")
            ),
            "service_request": self._text_value(get("service_request")),
            "docstatus": get("docstatus"),
            "status": self._text_value(get("status")),
        }

    def native_procedure_from_create_receipt(self, actor, operation_key):
        """Resolve the procedure a committed creation receipt names, or ``None``."""

        receipt = self.lookup_procedure_receipt(
            actor,
            procedure_contract.PROCEDURE_CREATE_KIND,
            operation_key,
        )
        if receipt is None:
            return None
        created = procedure_write.decode_receipt(receipt)
        if created.kind != procedure_contract.PROCEDURE_CREATE_KIND:
            return None
        return self._text_value(created.native_procedure_id) or None

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
        """Lock the procedure row and read the contract decision fields.

        The native transaction snapshot is restarted first (no native write has
        happened yet) and the current user's own native write authority is
        re-checked under the lock, so a permission change between the request
        checks and the lock is a definite refusal rather than a write.
        """

        try:
            frappe = self.frappe
            permission_error = frappe.PermissionError
        except Exception:
            raise patient_write.WriteUncertain() from None
        try:
            frappe.db.rollback()
            doc = frappe.get_doc(
                procedure_contract.PROCEDURE_DOCTYPE,
                native_procedure_id,
                for_update=True,
            )
            if not frappe.has_permission(
                procedure_contract.PROCEDURE_DOCTYPE,
                ptype="write",
                doc=native_procedure_id,
                user=frappe.session.user,
            ):
                raise permission_error(DENIAL_MESSAGE)
            values = self._procedure_state_values(doc)
        except permission_error:
            raise
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
        """Resolve the installed field names through the metadata cache.

        ``get_valid_columns()`` returns only the database columns, which excludes
        every Table field: ``User.roles`` is one, so it can never satisfy the
        closed-world preflight (frappe 16.54.0). The installed field set is the
        meta fieldnames, which include Table fields and nothing that is not a
        field.
        """

        cached = getattr(self, "_admin_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in admin_contract.REQUIRED_FIELDS:
            fields[doctype] = {
                field.fieldname for field in get_meta(doctype).fields
            }
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

    def native_admin_username_exists(self, username):
        """Accurate duplicate check against the installed ``username`` field.

        ``User`` is named by email, so the docname lookup above cannot answer
        this. An unreadable check fails closed: a duplicate is refused rather
        than left to fail after the insert.
        """

        if not username:
            return False
        try:
            found = self.frappe.db.get_value(
                admin_contract.USER_DOCTYPE, {"username": username}, "name"
            )
        except Exception:
            return True
        return bool(self._text_value(found))

    def native_admin_email_problem(self, email):
        """Return a deterministic installed email-policy problem, or ``None``.

        Only an installed validator is used, so the refusal is provable before
        the insert. When the install exposes no validator the outcome stays
        unproven and the native controller decides; this method never invents
        a policy of its own.
        """

        try:
            validate = self.frappe.utils.validate_email_address
        except Exception:
            return None
        try:
            accepted = validate(email)
        except Exception:
            return "email is invalid"
        # Installed Frappe: ``validate_email_address(email, throw=False)``
        # returns the email when it is valid and an empty string when it is
        # not. Treat any falsy return as the deterministic refusal.
        return None if accepted else "email is invalid"

    def native_admin_practitioner_is_linked(self, name):
        """Whether a practitioner already points at any user.

        The linked user is never returned or exposed: the caller only needs the
        refusal, so an operator cannot read another user's link through it.
        """

        try:
            linked = self.frappe.db.get_value(
                admin_contract.PRACTITIONER_DOCTYPE, name, "user_id"
            )
        except Exception:
            return True
        return bool(self._text_value(linked))

    def native_admin_can_write_practitioner(self, name):
        """The acting user's native write authority for one practitioner.

        The document is loaded and asked for its own ``write`` permission, so
        the answer is the installed one even if ``has_permission`` with a
        docname is not supported by this Frappe version.
        """

        try:
            doc = self.frappe.get_doc(
                admin_contract.PRACTITIONER_DOCTYPE, name
            )
            doc.check_permission("write")
            return True
        except Exception:
            return False

    def native_admin_can_create_user_permission(self):
        """The acting user's native ``User Permission`` create authority.

        Linking a practitioner runs the installed ``Healthcare Practitioner``
        update hook, which creates a User Permission row for the linked user.
        Without create authority that save fails after the User insert, so the
        endpoint decides this before any mutation.
        """

        try:
            return bool(
                self.frappe.has_permission(
                    admin_contract.USER_PERMISSION_DOCTYPE,
                    ptype="create",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def link_native_admin_practitioner(self, practitioner, user):
        """Link one practitioner through its native load/save lifecycle.

        The acting user's write permission on the row is required, the row is
        loaded and saved so the installed controller validates the link, and an
        existing link is never overwritten. ``db.set_value`` is not used: it
        would bypass both permission and controller hooks.
        """

        try:
            doc = self.frappe.get_doc(
                admin_contract.PRACTITIONER_DOCTYPE, practitioner
            )
            doc.check_permission("write")
            if self._text_value(getattr(doc, "user_id", None)):
                raise patient_write.WriteUncertain()
            doc.user_id = user
            doc.save()
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None
        return practitioner

    def native_admin_practitioner_exists(self, name):
        try:
            return bool(
                self.frappe.db.exists(admin_contract.PRACTITIONER_DOCTYPE, name)
            )
        except Exception:
            return False

    def native_admin_practitioner_for_user(self, user):
        """Return the practitioner row already linked to one user, or ``None``.

        Amendment A1.1: one practitioner per user link, decided before the fence.
        """

        try:
            name = self.frappe.db.get_value(
                admin_contract.PRACTITIONER_DOCTYPE, {"user_id": user}, "name"
            )
        except Exception:
            return None
        return self._text_value(name) or None

    def native_admin_user_enabled(self, user):
        """Return whether the link target user is enabled, read before the fence.

        The installed ``validate_user_id`` refuses a disabled user inside the
        mutation, so an enabled check belongs here instead. Anything unreadable
        fails closed as "not enabled".
        """

        try:
            return bool(
                self.frappe.db.get_value(
                    admin_contract.USER_DOCTYPE, user, "enabled"
                )
            )
        except Exception:
            return False

    def native_admin_mandatory_link_rows(self, doctype):
        """Row counts of every mandatory Link field on one DocType, or ``None``.

        Amendment A1.1: a mandatory Link target with zero rows on site is a
        designed pre-fence refusal, so the counts are read from the installed
        meta (never hardcoded) and returned as ``{fieldname: rows}``.
        """

        try:
            meta = self.frappe.get_meta(doctype)
            fields = getattr(meta, "fields", None) or ()
            links = {}
            for field in fields:
                if getattr(field, "fieldtype", None) != "Link":
                    continue
                if not getattr(field, "reqd", 0):
                    continue
                options = getattr(field, "options", None)
                if not options:
                    continue
                links[str(field.fieldname)] = int(
                    self.frappe.db.count(options)
                )
            return links
        except Exception:
            return None

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

    def native_admin_company(self):
        """Resolve the site's one working company, or ``None``.

        ``frappe.defaults.get_default`` does not exist in frappe 16.54.0; the
        frappe-16 API is ``frappe.db.get_default``. Global Defaults is read
        first, then the database default, then the deterministic fallback: a site
        with exactly one Company row has exactly one answer. Anything else is
        ``None`` and the caller refuses before the fence claim.
        """

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
                company = self._text_value(self.frappe.db.get_default("company"))
            except Exception:
                company = None
        if not company:
            try:
                companies = self.frappe.get_all("Company", pluck="name") or ()
            except Exception:
                companies = ()
            if len(companies) == 1:
                company = self._text_value(companies[0])
            else:
                company = None
        return company

    def native_admin_supplier_group(self):
        """Resolve the supplier group the supplier row links, or ``None``.

        The installed default root is preferred; a site with exactly one group
        has exactly one answer. Ambiguity is never resolved by precedence, and no
        group means the supplier op refuses before the fence claim.
        """

        try:
            if self.frappe.db.exists(
                admin_contract.SUPPLIER_GROUP_DOCTYPE,
                admin_contract.DEFAULT_SUPPLIER_GROUP,
            ):
                return admin_contract.DEFAULT_SUPPLIER_GROUP
            groups = (
                self.frappe.get_all(
                    admin_contract.SUPPLIER_GROUP_DOCTYPE, pluck="name"
                )
                or ()
            )
        except Exception:
            return None
        if len(groups) == 1:
            return self._text_value(groups[0]) or None
        return None

    def native_admin_master_rows(self, master_type):
        """Return the installed row count for one master DocType, or ``None``."""

        doctype = admin_contract.MASTER_DOCTYPES.get(master_type)
        if doctype is None:
            return None
        try:
            return int(self.frappe.db.count(doctype))
        except Exception:
            return None

    def native_admin_prerequisites(self):
        """Read the pre-fence admin prerequisites from the site's own records."""

        return {
            "company": self.native_admin_company(),
            "uom_rows": self.native_admin_master_rows("uom"),
            "supplier_group": self.native_admin_supplier_group(),
            "supplier_group_rows": self.native_admin_master_rows(
                "supplier_group"
            ),
            "practitioner_links": self.native_admin_mandatory_link_rows(
                admin_contract.PRACTITIONER_DOCTYPE
            ),
        }

    def _admin_company(self):
        company = self.native_admin_company()
        if not company:
            # Unreachable through the endpoint: the same resolution is a designed
            # pre-fence refusal there. Closing fail-closed keeps the mutation
            # honest if the site changes under a held claim.
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
                "email": payload["email"],
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
            name = self._text_value(user.name)
            if not name:
                raise patient_write.WriteUncertain()
            practitioner = payload.get("practitioner")
            if practitioner:
                self.link_native_admin_practitioner(practitioner, name)
            return name
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_admin_master(self, master_type, payload):
        """Create one master row (with its bundled price/account child).

        Company, currency and the supplier group are read from the site's own
        records; none is accepted from the client. The prerequisite existence
        checks are decided pre-fence by the endpoint, so a missing company, UOM
        or Supplier Group never fails after the claim.
        """

        try:
            company = self._admin_company()
            if master_type == "uom":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.UOM_DOCTYPE,
                        "uom_name": payload["uom_name"],
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "practitioner":
                # Amendment A1.1. The installed required ``first_name`` carries
                # the supplied practitioner name (the installed
                # ``set_full_name`` derives ``practitioner_name`` from it and the
                # installed ``status`` default is Active). The user link is
                # validated and its uniqueness decided before the fence.
                fields = {
                    "doctype": admin_contract.PRACTITIONER_DOCTYPE,
                    "first_name": payload["practitioner_name"],
                }
                if payload.get("user"):
                    fields["user_id"] = payload["user"]
                doc = self.frappe.get_doc(fields)
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "supplier_group":
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.SUPPLIER_GROUP_DOCTYPE,
                        "supplier_group_name": payload["supplier_group_name"],
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
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
                supplier_group = self.native_admin_supplier_group()
                if not supplier_group:
                    raise patient_write.WriteUncertain()
                doc = self.frappe.get_doc(
                    {
                        "doctype": admin_contract.SUPPLIER_DOCTYPE,
                        "supplier_name": payload["supplier_name"],
                        "supplier_group": supplier_group,
                    }
                )
                doc.insert()
                return self._text_value(doc.name)
            if master_type == "item":
                currency = self._admin_currency(company)
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
                        # Review round 2: installed Item Price has no ``rate``;
                        # it has ``price_list_rate`` and requires ``uom``.
                        "price_list_rate": payload["price_list_rate"],
                        "uom": payload["stock_uom"],
                        "currency": currency,
                        "selling": 1,
                    }
                )
                price.insert()
                return self._text_value(doc.name)
            if master_type == "price_list":
                currency = self._admin_currency(company)
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
                        # Review round 2: the installed template has no
                        # ``company`` and no ``uom``; it requires ``description``
                        # and ``item_group``. The description is server-derived
                        # from the supplied template name. The template links the
                        # existing item (``link_existing_item``), otherwise the
                        # installed after-insert would create a second Item and
                        # its price inside the fence.
                        "description": payload["template_name"],
                        "item_code": payload["item_code"],
                        "item": payload["item_code"],
                        "link_existing_item": 1,
                        "item_group": self._text_value(item_get("item_group")),
                        "rate": payload["rate"],
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
                admin_contract.USER_ROLES_DOCTYPE,
                filters={
                    "parent": user,
                    "parenttype": admin_contract.USER_ROLES_PARENTTYPE,
                    "parentfield": admin_contract.USER_ROLES_PARENTFIELD,
                },
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
                    # Review round 2: the installed rate field is
                    # ``price_list_rate``; ``rate`` does not exist.
                    fields=["price_list", "price_list_rate"],
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
                facts["price_list_rate"] = price_get("price_list_rate")
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
                    ["name", "template", "item_code", "item", "description"],
                    as_dict=True,
                )
                return self._admin_facts_row(
                    row, ("template", "item_code", "item", "description")
                )
            if master_type == "supplier_group":
                row = self.frappe.db.get_value(
                    admin_contract.SUPPLIER_GROUP_DOCTYPE,
                    name,
                    ["name"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ())
            if master_type == "uom":
                row = self.frappe.db.get_value(
                    admin_contract.UOM_DOCTYPE,
                    name,
                    ["name"],
                    as_dict=True,
                )
                return self._admin_facts_row(row, ())
            if master_type == "practitioner":
                row = self.frappe.db.get_value(
                    admin_contract.PRACTITIONER_DOCTYPE,
                    name,
                    ["name", "practitioner_name", "user_id"],
                    as_dict=True,
                )
                return self._admin_facts_row(
                    row, ("practitioner_name", "user_id")
                )
            raise patient_write.WriteUncertain()
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _admin_facts_row(self, row, fields):
        """Type-preserving fact row: only ``name`` is normalised as text.

        Review round 3: routing every fact through ``_text_value`` returned
        ``None`` for non-strings, so a Check (``selling`` on a Price List, stored
        as 0/1) read back as ``None`` and the closed-world compare refused
        *after* the fence claim, latching the site. Non-text installed values are
        therefore returned exactly as the database gives them: ``Check``/``Int``
        as ``0``/``1`` ints, ``Float``/``Currency`` as numbers, ``Data``/``Link``/
        ``Select`` as strings, and ``None`` when the column is null. The contract
        accepts both ``1`` and ``True`` for boolean facts.
        """

        if row is None:
            raise patient_write.WriteUncertain()
        get = (
            row.get
            if hasattr(row, "get")
            else (lambda key, default=None: default)
        )
        facts = {"name": self._text_value(get("name"))}
        for field in fields:
            facts[field] = get(field)
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
                doctype: {
                    event: tuple(handlers or ())
                    for event, handlers in (
                        doc_events.get(doctype) or {}
                    ).items()
                }
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

    def native_stock_can_submit(self, doctype):
        """The acting user's native submit authority on one stock document."""

        try:
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="submit",
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
                "po_item": order["po_item"],
                "po_pending_qty": order["po_pending_qty"],
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

    def create_and_submit_native_stock_reconciliation(self, payload):
        """One reconciliation from the native document fields: insert, submit.

        The body already carries the document's own fields. This adapter adds
        nothing and validates nothing; the installed controller decides. Native
        exceptions are deliberately propagated unchanged so the endpoint can
        tell a refusal from a lost outcome after it rolls the transaction back:
        Frappe's ``ValidationError`` family (including ``MandatoryError`` and
        ``LinkValidationError``) and its own ``PermissionError`` keep their type.
        A rolled-back request may safely answer with a definite refusal because
        ``frappe.db.rollback()`` ends the write transaction and the later
        request-end commit only commits the fresh, empty one.
        """

        row = {
            "item_code": payload["item_code"],
            "warehouse": payload["warehouse"],
        }
        if payload.get("qty") is not None:
            row["qty"] = payload["qty"]
        if payload.get("valuation_rate") is not None:
            row["valuation_rate"] = payload["valuation_rate"]
        if payload.get("batch_id"):
            row["batch_no"] = payload["batch_id"]
            row["use_serial_batch_fields"] = 1
        document = {
            "doctype": stock_contract.STOCK_RECONCILIATION_DOCTYPE,
            "company": payload["company"],
            "purpose": payload["purpose"],
            "items": [row],
        }
        if payload.get("expense_account"):
            document["expense_account"] = payload["expense_account"]
        reconciliation = self.frappe.get_doc(document)
        reconciliation.insert()
        reconciliation.submit()
        return {
            "name": self._text_value(reconciliation.name),
            "docstatus": getattr(reconciliation, "docstatus", None),
        }

    def create_and_submit_native_stock_transfer(self, payload):
        """One Material Transfer from the native document fields: insert, submit.

        The body already carries the document's own fields, so this adapter adds
        only the document type and the native child-row names: ``s_warehouse`` /
        ``t_warehouse`` and ``batch_no``. Frappe fills the unit, description,
        expense account and conversion factor on insert, and its controller
        decides the warehouses, the batch, available stock, the quantity and the
        submit lifecycle. Native exceptions are deliberately propagated
        unchanged so the endpoint can tell a refusal from a lost outcome after
        it rolls the transaction back: Frappe's ``ValidationError`` family and
        its own ``PermissionError`` keep their type.
        """

        row = {
            "item_code": payload["item_code"],
            "qty": payload["qty"],
            "s_warehouse": payload["source_warehouse"],
            "t_warehouse": payload["target_warehouse"],
        }
        if payload.get("batch_id"):
            row["batch_no"] = payload["batch_id"]
            row["use_serial_batch_fields"] = 1
        document = {
            "doctype": stock_contract.STOCK_ENTRY_DOCTYPE,
            "company": payload["company"],
            "stock_entry_type": stock_contract.MATERIAL_TRANSFER_TYPE,
            "items": [row],
        }
        entry = self.frappe.get_doc(document)
        entry.insert()
        entry.submit()
        return {
            "name": self._text_value(entry.name),
            "docstatus": getattr(entry, "docstatus", None),
        }

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

    # -- purchasing (PO draft/submit + receipt) surface -------------------

    def _purchase_meta_fields(self):
        """Resolve the installed field names through the metadata cache.

        Meta fieldnames, not ``get_valid_columns()``: the latter excludes Table
        fields in frappe 16.54.0, and ``Purchase Order.items`` is one. The
        installed ``default_fields`` (``name``, ``owner``, ``creation``,
        ``modified``, ``modified_by``, ``docstatus``, ``idx``) are ordinary
        columns, not ``DocField`` rows, so they are unioned in explicitly. The
        closed-world check stays strict for every other field: only frappe's own
        default columns are admitted by name.
        """

        cached = getattr(self, "_purchase_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in dict.fromkeys(
            (
                *purchase_contract.INSTALLED_CANDIDATES,
                *purchase_contract.DRAFT_INSTALLED_CANDIDATES,
            )
        ):
            meta = get_meta(doctype)
            default_fields = getattr(meta, "default_fields", None) or ()
            fields[doctype] = {
                field.fieldname for field in meta.fields
            } | {
                name for name in default_fields if isinstance(name, str)
            }
        self._purchase_fields_cache = fields
        return fields

    def native_purchase_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return purchase_contract.install_problem(self._purchase_meta_fields())
        except Exception:
            return "installed metadata unreadable"

    def native_purchase_draft_install_problem(self):
        """Preflight only the fields the converted draft lane reads or writes.

        The fenced submit/receipt preflight stays unchanged and remains the
        gate for that lane; this narrower check exists because a healthy
        ERPNext 16.52.1 install has no ``Purchase Order Item.purchase_order``
        column, which would otherwise disable the draft for no reason.
        """

        try:
            return purchase_contract.draft_install_problem(
                self._purchase_meta_fields()
            )
        except Exception:
            return "installed metadata unreadable"

    def native_purchase_boundary_problem(self):
        """Return pinned-boundary drift for the purchasing family, or ``None``.

        Tests supply ``frappe.purchase_boundary_snapshot``; production collects
        the pinned versions, the sha256 of the two buying controllers this
        family drives or creates, their MRO, the submittable flags, the
        wildcard/class overrides on the path, the enabled Server Script count and
        our fence kinds. Any failure is a non-empty problem so callers fail
        closed.
        """

        try:
            snapshot = getattr(self.frappe, "purchase_boundary_snapshot", None)
            if snapshot is None:
                snapshot = self._collect_purchase_boundary_snapshot()
            return purchase_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_purchase_boundary_snapshot(self):
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
            "purchase_order": (
                "buying",
                "doctype",
                "purchase_order",
                "purchase_order.py",
            ),
            "purchase_receipt": (
                "stock",
                "doctype",
                "purchase_receipt",
                "purchase_receipt.py",
            ),
            "batch": ("stock", "doctype", "batch", "batch.py"),
        }
        source_sha256 = {}
        for key, parts in sources.items():
            path = frappe.get_app_path("erpnext", *parts)
            with open(path, "rb") as handle:
                source_sha256[key] = hashlib.sha256(handle.read()).hexdigest()

        mro = {}
        for doctype in purchase_contract.GUARDED_DOCTYPES:
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
                    purchase_contract.PURCHASE_ORDER_DOCTYPE,
                    purchase_contract.PURCHASE_RECEIPT_DOCTYPE,
                )
            },
            "enabled_server_scripts": len(server_scripts),
            "doc_events": {
                doctype: tuple(doc_events.get(doctype) or ())
                for doctype in purchase_contract.GUARDED_DOCTYPES
            },
            "wildcard_doc_events": {
                event: tuple(wildcard.get(event) or ())
                for event in purchase_contract.REVIEWED_WILDCARD_EVENTS
            },
            "scheduler_purchase_jobs": tuple(
                path
                for handlers in (hooks.get("scheduler_events") or {}).values()
                for path in (handlers or ())
                if any(
                    token in str(path)
                    for token in purchase_contract.SCHEDULER_PURCHASE_TOKENS
                )
            ),
            "class_overrides": {
                doctype: tuple(overrides.get(doctype) or ())
                + tuple(extends.get(doctype) or ())
                for doctype in purchase_contract.GUARDED_DOCTYPES
            },
            "fence_kinds": tuple(purchase_contract.PURCHASE_KINDS),
        }

    def native_purchase_can_create(self, doctype):
        """The acting user's native create authority on one buying document."""

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

    def _purchase_company_currency(self):
        """The installed company and its own currency (never client input)."""

        company = None
        try:
            get_default = getattr(self.frappe.defaults, "get_user_default", None)
            if callable(get_default):
                company = get_default("Company")
            if not company:
                company = self.frappe.db.get_single_value(
                    "Global Defaults", "default_company"
                )
        except Exception:
            company = None
        company = self._text_value(company)
        currency = ""
        if company:
            try:
                currency = self._text_value(
                    self.frappe.db.get_value("Company", company, "default_currency")
                )
            except Exception:
                currency = ""
        return company, currency

    def _purchase_currency(self, company):
        if not company:
            return ""
        try:
            return self._text_value(
                self.frappe.db.get_value("Company", company, "default_currency")
            )
        except Exception:
            return ""

    def _purchase_draft_company_currency(self):
        """The company Frappe's own default resolution returns; no extra guess.

        ``frappe.defaults.get_user_default("Company")`` is the framework's
        documented resolution: it merges the user's defaults with the
        ``__default`` site/global values Frappe itself stores, so it is not a
        purely per-user lookup. This lane accepts exactly that resolved value and
        adds no fallback of its own — in particular it never reads a
        ``Global Defaults`` single value directly. When the framework resolves
        nothing, the empty company is reported and the draft is refused rather
        than guessed.
        """

        company = None
        try:
            get_default = getattr(self.frappe.defaults, "get_user_default", None)
            if callable(get_default):
                company = get_default("Company")
        except Exception:
            company = None
        company = self._text_value(company)
        if not company:
            return "", ""
        return company, self._purchase_currency(company)

    def _purchase_order_line(self, purchase_order, item_code):
        """Read the order state and its matching line (pending quantity)."""

        if not purchase_order:
            return {
                "exists": False,
                "docstatus": None,
                "status": None,
                "supplier": "",
                "po_item": None,
                "pending": None,
                "received": 0.0,
            }
        docstatus = self.frappe.db.get_value(
            purchase_contract.PURCHASE_ORDER_DOCTYPE,
            purchase_order,
            "docstatus",
        )
        if docstatus is None:
            return {
                "exists": False,
                "docstatus": None,
                "status": None,
                "supplier": "",
                "po_item": None,
                "pending": None,
                "received": 0.0,
            }
        status = self.frappe.db.get_value(
            purchase_contract.PURCHASE_ORDER_DOCTYPE, purchase_order, "status"
        )
        supplier = self.frappe.db.get_value(
            purchase_contract.PURCHASE_ORDER_DOCTYPE, purchase_order, "supplier"
        )
        rows = self.frappe.db.get_all(
            purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE,
            filters={"parent": purchase_order, "item_code": item_code},
            fields=["name", "qty", "received_qty"],
            order_by="idx asc",
            limit=1,
        ) or ()
        if not rows:
            return {
                "exists": True,
                "docstatus": docstatus,
                "status": self._text_value(status),
                "supplier": self._text_value(supplier),
                "po_item": None,
                "pending": None,
                "received": 0.0,
            }
        row = rows[0]
        get = getattr(row, "get", lambda key, default=None: default)
        ordered = get("qty")
        received = get("received_qty") or 0
        pending = None
        if isinstance(ordered, (int, float)) and not isinstance(ordered, bool):
            pending = float(ordered) - float(received or 0)
        return {
            "exists": True,
            "docstatus": docstatus,
            "status": self._text_value(status),
            "supplier": self._text_value(supplier),
            "po_item": self._text_value(get("name")),
            "pending": pending,
            "received": float(received or 0),
        }

    def _purchase_received(self, purchase_order, item_code):
        return self._purchase_order_line(purchase_order, item_code)["received"]

    def _purchase_readable_item(self, item_code):
        """Item fields only under the caller's own native Item read authority.

        A denied Item reports ``denied`` and loads no field, so an unauthorized
        caller cannot infer stock/batch flags from the preflight. A missing Item
        still reaches the designed "does not exist" refusal.
        """

        readable = (
            self.native_doctype_can_read(
                purchase_contract.ITEM_DOCTYPE, item_code
            )
            is True
        )
        if not readable:
            exists = False
            try:
                exists = bool(
                    self.frappe.db.exists(
                        purchase_contract.ITEM_DOCTYPE, item_code
                    )
                )
            except Exception:
                exists = False
            return {"exists": False, "readable": False, "denied": exists}
        return {**self._stock_item(item_code), "readable": True, "denied": False}

    def native_purchase_order_draft_detail(self, payload):
        """Read the converted draft decision fields under native authority.

        The company and its currency come from Frappe's own
        ``get_user_default("Company")`` resolution (which already includes the
        framework's ``__default`` values); this lane adds no fallback of its own
        and never reads a single default value directly. The supplier, every
        item and every target warehouse are read only through the current
        user's own native read permission; a denied master is reported as
        denied without loading its fields. A warehouse also reports its own
        company and disabled flag so a cross-company or disabled target is
        refused before any mutation. The converted lane never reads the fence or
        a committed receipt.
        """

        try:
            company, currency = self._purchase_draft_company_currency()
            supplier = payload["supplier"]
            supplier_not_readable = (
                self.native_doctype_can_read(
                    purchase_contract.SUPPLIER_DOCTYPE, supplier
                )
                is not True
            )
            supplier_exists = False
            try:
                supplier_exists = bool(
                    self.frappe.db.exists(
                        purchase_contract.SUPPLIER_DOCTYPE, supplier
                    )
                )
            except Exception:
                supplier_exists = False
            items = {}
            for line in payload["items"]:
                code = line["item_code"]
                if code not in items:
                    items[code] = self._purchase_readable_item(code)
            return {
                "today": self.site_today(),
                "company": company,
                "currency": currency,
                "supplier_readable": not supplier_not_readable,
                "supplier_denied": supplier_exists and supplier_not_readable,
                "supplier_exists": supplier_exists
                and not supplier_not_readable,
                "items": items,
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def _purchase_field_readable(self, fieldname):
        """True when one Purchase Order DocField is readable and unmasked."""

        meta = self.frappe.get_meta(purchase_contract.PURCHASE_ORDER_DOCTYPE)
        accessible_permlevels = meta.get_permlevel_access(
            "read", user=self.frappe.session.user
        )
        field_definition = meta.get_field(fieldname)
        if (
            field_definition is None
            or field_definition.permlevel not in accessible_permlevels
        ):
            return False
        masked_fields = meta.get_masked_fields() or ()
        return not any(
            getattr(masked, "fieldname", None) == fieldname
            for masked in masked_fields
        )

    def native_purchase_order_view(self, native_purchase_order_id):
        """Three-field purchase order read under document and field rights.

        The document read permission is checked first. ``status`` must then
        exist at a readable permlevel and must not be masked for the current
        user; ``name`` and ``docstatus`` are standard columns read only after
        that check. No supplier, item, quantity or amount is fetched here.
        ``None`` covers a denied document, a restricted or masked status, a
        malformed row and any adapter failure.
        """

        if (
            not isinstance(native_purchase_order_id, str)
            or not native_purchase_order_id.strip()
        ):
            return None
        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                purchase_contract.PURCHASE_ORDER_DOCTYPE,
                ptype="read",
                doc=native_purchase_order_id,
                user=user,
            ):
                return None
            if not self._purchase_field_readable(PURCHASE_ORDER_STATUS_FIELD):
                return None
            row = frappe.db.get_value(
                purchase_contract.PURCHASE_ORDER_DOCTYPE,
                native_purchase_order_id,
                [
                    PURCHASE_STANDARD_NAME_FIELD,
                    PURCHASE_ORDER_STATUS_FIELD,
                    "docstatus",
                ],
                as_dict=True,
            )
        except Exception:
            return None
        if row is None:
            return None
        get = (
            row.get
            if hasattr(row, "get")
            else (lambda key, default=None: default)
        )
        name = self._text_value(get(PURCHASE_STANDARD_NAME_FIELD))
        status = self._text_value(get(PURCHASE_ORDER_STATUS_FIELD))
        docstatus = get("docstatus")
        if not name or not status:
            return None
        if (
            not isinstance(docstatus, int)
            or isinstance(docstatus, bool)
            or docstatus not in PURCHASE_DOCSTATUS_VALUES
        ):
            return None
        return {
            "purchase_order_id": name,
            "status": status,
            "docstatus": docstatus,
        }

    def native_purchase_order_drafts(self, supplier, limit):
        """Bounded, permission-aware draft names for one readable supplier.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used on this discovery path. The supplier
        filter and ``status`` are DocFields that can carry a permlevel or a
        mask, so both pass the current user's own field-level read check before
        the query is built. One row beyond the bound is requested, so an
        over-full page is reported as ``None`` rather than silently truncated.
        The caller still applies the document and field-level checks per record.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        if not isinstance(supplier, str) or not supplier.strip():
            return None
        try:
            if not self._purchase_field_readable(PURCHASE_ORDER_STATUS_FIELD):
                return None
            if not self._purchase_field_readable(
                PURCHASE_ORDER_SUPPLIER_FIELD
            ):
                return None
            rows = self.frappe.get_list(
                purchase_contract.PURCHASE_ORDER_DOCTYPE,
                filters={
                    PURCHASE_ORDER_SUPPLIER_FIELD: supplier,
                    "docstatus": purchase_contract.DRAFT_DOCSTATUS,
                },
                fields=[PURCHASE_STANDARD_NAME_FIELD],
                order_by="modified desc, name asc",
                limit_page_length=limit + 1,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        if len(rows) > limit:
            return None
        names = []
        for row in rows:
            name = (
                row.get(PURCHASE_STANDARD_NAME_FIELD)
                if hasattr(row, "get")
                else getattr(row, PURCHASE_STANDARD_NAME_FIELD, None)
            )
            name = self._text_value(name)
            if not name:
                return None
            names.append(name)
        return names

    def native_purchase_order_submit_detail(self, payload):
        """Read the pre-fence submit decision fields."""

        try:
            company, currency = self._purchase_company_currency()
            order = self._purchase_order_line(payload["purchase_order"], None)
            lines = []
            if order["exists"]:
                rows = self.frappe.db.get_all(
                    purchase_contract.PURCHASE_ORDER_ITEM_DOCTYPE,
                    filters={"parent": payload["purchase_order"]},
                    fields=[
                        "name",
                        "item_code",
                        "qty",
                        "rate",
                        "uom",
                        "received_qty",
                    ],
                    order_by="idx asc",
                ) or ()
                for row in rows:
                    get = getattr(row, "get", lambda key, default=None: default)
                    lines.append(
                        {
                            "item_code": self._text_value(get("item_code")),
                            "qty": get("qty"),
                            "rate": get("rate"),
                            "uom": self._text_value(get("uom")),
                            "po_item": self._text_value(get("name")),
                        }
                    )
            return {
                "today": self.site_today(),
                "company": company,
                "currency": currency,
                "supplier_exists": bool(
                    order["supplier"]
                    and self.frappe.db.exists("Supplier", order["supplier"])
                ),
                "purchase_order": {
                    "exists": order["exists"],
                    "docstatus": order["docstatus"],
                    "supplier": order["supplier"],
                    "status": order["status"],
                },
                "lines": lines,
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def native_purchase_receipt_detail(self, payload):
        """Read the pre-fence PO-referenced receipt decision fields."""

        try:
            item = self._stock_item(payload["item_code"])
            warehouse = self._stock_warehouse(payload["warehouse"])
            company = warehouse.get("company")
            order = self._purchase_order_line(
                payload["purchase_order"], payload["item_code"]
            )
            return {
                "today": self.site_today(),
                "company": company,
                "currency": self._purchase_currency(company),
                "supplier": order["supplier"],
                "supplier_exists": bool(
                    order["supplier"]
                    and self.frappe.db.exists("Supplier", order["supplier"])
                ),
                "item": item,
                "warehouse": warehouse,
                "purchase_order": {
                    "exists": order["exists"],
                    "docstatus": order["docstatus"],
                    "status": order["status"],
                },
                "po_pending_qty": order["pending"],
                "po_item": order["po_item"],
                "batch": self._stock_batch(payload.get("batch_id")),
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def native_purchase_baseline(
        self, kind, warehouses, item_code, purchase_order=None
    ):
        """Pre-fence Stock Ledger Entry, GL, Bin and PO received baselines."""

        if kind not in purchase_contract.PURCHASE_KINDS:
            raise patient_write.WriteUncertain()
        try:
            counts = self._stock_document_counts()
            bins = {
                warehouse: self._stock_bin_qty(item_code, warehouse)
                for warehouse in warehouses
            }
            received = (
                self._purchase_received(purchase_order, item_code)
                if purchase_order
                else 0.0
            )
            return {**counts, "bins": bins, "po_received": received}
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_purchase_order_draft(self, prepared):
        """One draft purchase order: inserted, never submitted.

        The installed controller resolves the values the client never sends:
        ``insert`` runs the full controller lifecycle, whose
        ``set_missing_values(for_validate=True)`` fills ``item_name`` from the
        Item master and the target ``warehouse`` from the company's default
        warehouse. This lane therefore sends only the item, quantity, rate and
        the server-resolved unit/date; it does not derive those values itself
        and never guesses a warehouse. Permission and validation remain the
        installed controller's.
        """

        try:
            rows = []
            for line in prepared["items"]:
                rows.append(
                    {
                        "item_code": line["item_code"],
                        "qty": line["qty"],
                        "rate": line["rate"],
                        "uom": line["uom"],
                        "stock_uom": line.get("stock_uom") or line["uom"],
                        "conversion_factor": line.get("conversion_factor") or 1.0,
                        "schedule_date": line["schedule_date"],
                    }
                )
            document = {
                "doctype": purchase_contract.PURCHASE_ORDER_DOCTYPE,
                "supplier": prepared["supplier"],
                "company": prepared["company"],
                "currency": prepared["currency"],
                "conversion_rate": prepared.get("conversion_rate") or 1.0,
                "transaction_date": prepared["transaction_date"],
                "items": rows,
            }
            order = self.frappe.get_doc(document)
            order.insert()
            return self._text_value(order.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def submit_native_purchase_order(self, prepared):
        """Submit the existing draft; the body never carries a submit flag."""

        try:
            order = self.frappe.get_doc(
                purchase_contract.PURCHASE_ORDER_DOCTYPE,
                prepared["purchase_order"],
            )
            if order.docstatus != purchase_contract.DRAFT_DOCSTATUS:
                raise patient_write.WriteUncertain()
            order.submit()
            return self._text_value(order.name)
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_purchase_receipt(self, prepared):
        """One PO-referenced receipt unit: batch master (where required), then
        the submitted ``Purchase Receipt`` carrying the order reference."""

        try:
            if prepared.get("create_batch"):
                self._stock_create_batch(prepared)
            row = {
                "item_code": prepared["item_code"],
                "qty": prepared["qty"],
                "uom": prepared["uom"],
                "warehouse": prepared["warehouse"],
                "purchase_order": prepared["purchase_order"],
            }
            if prepared.get("po_item"):
                row["purchase_order_item"] = prepared["po_item"]
            if prepared.get("batch_no"):
                row["batch_no"] = prepared["batch_no"]
                row["use_serial_batch_fields"] = 1
            document = {
                "doctype": purchase_contract.PURCHASE_RECEIPT_DOCTYPE,
                "supplier": prepared["supplier"],
                "company": prepared["company"],
                "currency": prepared["currency"],
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

    def _purchase_side_effect_facts(self, reference, expected, baseline):
        """Closed-world re-read: document state, SLE/GL deltas, Bin movement and
        the purchase order received-quantity/status delta."""

        if not isinstance(baseline, dict) or not isinstance(expected, dict):
            raise patient_write.WriteUncertain()
        doctype = expected.get("doctype")
        if not isinstance(doctype, str) or not reference:
            raise patient_write.WriteUncertain()
        try:
            counts = self._stock_document_counts()
            bins = {}
            for warehouse in expected.get("bins") or {}:
                before = (baseline.get("bins") or {}).get(warehouse)
                after = self._stock_bin_qty(
                    expected.get("item_code"), warehouse
                )
                after_value = (
                    float(after) if isinstance(after, (int, float)) else 0.0
                )
                before_value = (
                    float(before) if isinstance(before, (int, float)) else 0.0
                )
                bins[warehouse] = after_value - before_value
            status = None
            purchase_order = expected.get("purchase_order")
            if doctype == purchase_contract.PURCHASE_ORDER_DOCTYPE:
                status = self.frappe.db.get_value(doctype, reference, "status")
            elif purchase_order:
                status = self.frappe.db.get_value(
                    purchase_contract.PURCHASE_ORDER_DOCTYPE,
                    purchase_order,
                    "status",
                )
            facts = {
                "docstatus": self.frappe.db.get_value(
                    doctype, reference, "docstatus"
                ),
                "doctype": doctype,
                "status": self._text_value(status),
                "sle_rows": counts["stock_ledger_rows"]
                - int(baseline.get("stock_ledger_rows") or 0),
                "gl_rows": counts["gl_rows"]
                - int(baseline.get("gl_rows") or 0),
                "bins": bins,
            }
            if "po_received_delta" in expected:
                before = baseline.get("po_received") or 0.0
                after = self._purchase_received(
                    purchase_order, expected.get("item_code")
                )
                facts["po_received_delta"] = float(after) - float(before)
                facts["po_status"] = self._text_value(status)
            if facts["docstatus"] is None:
                raise patient_write.WriteUncertain()
            return facts
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def native_purchase_order_facts(self, reference, expected, baseline):
        return self._purchase_side_effect_facts(reference, expected, baseline)

    def native_purchase_receipt_facts(self, reference, expected, baseline):
        return self._purchase_side_effect_facts(reference, expected, baseline)

    # -- purchasing fence, receipts and acknowledgement --------------------

    def list_committed_purchase_receipts(
        self, limit, kind=None, operation_key=None
    ):
        ledger = self._ledger()
        try:
            return ledger.committed_purchase_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise purchase_contract.PurchaseBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_purchase_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.PURCHASE_OPERATION_KINDS:
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
        expected = purchase_contract.ack_token(
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
    def purchase_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.PURCHASE_OPERATION_KINDS:
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

    # -- finance (invoice submit + payment receive) ------------------------

    def _finance_meta_fields(self):
        """Resolve installed fields from real DocField metadata plus defaults.

        ``get_valid_columns()`` excludes Table fields (for example
        ``Sales Invoice.taxes``) in frappe 16.54.0, so the collector reads
        ``meta.fields`` and unions Frappe's ordinary default columns. The
        discount child remains optional; the parent ``deductions`` link is
        resolved separately.
        """

        cached = getattr(self, "_finance_fields_cache", None)
        if cached is not None:
            return cached
        frappe = self.frappe
        get_meta = getattr(getattr(frappe, "db", None), "get_meta", None)
        if not callable(get_meta):
            get_meta = frappe.get_meta
        fields = {}
        for doctype in dict.fromkeys(
            (
                *finance_contract.INSTALLED_CANDIDATES,
                *finance_contract.INVOICE_INSTALLED_CANDIDATES,
            )
        ):
            try:
                meta = get_meta(doctype)
            except Exception:
                if doctype in finance_contract.OPTIONAL_DOCTYPES:
                    fields[doctype] = set()
                    continue
                raise
            fieldnames = set()
            for field in getattr(meta, "fields", ()) or ():
                name = getattr(field, "fieldname", None)
                if name is None and hasattr(field, "get"):
                    name = field.get("fieldname")
                if isinstance(name, str) and name:
                    fieldnames.add(name)
            default_fields = getattr(meta, "default_fields", None) or ()
            fieldnames.update(
                name for name in default_fields if isinstance(name, str)
            )
            fields[doctype] = fieldnames
        fields[finance_contract.DEDUCTION_LINK_KEY] = (
            self._finance_deduction_link_ok(get_meta)
        )
        self._finance_fields_cache = fields
        return fields

    @staticmethod
    def _finance_deduction_link_ok(get_meta):
        """True when the parent carries the linked child table we write."""

        try:
            meta = get_meta(finance_contract.INVOICE_DOCTYPE)
        except Exception:
            return False
        for field in getattr(meta, "fields", ()) or ():
            get = getattr(field, "get", lambda key, default=None: default)
            if get("fieldname") != finance_contract.DEDUCTION_LINK_FIELD:
                continue
            if get("fieldtype") not in finance_contract.DEDUCTION_TABLE_TYPES:
                continue
            if get("options") == finance_contract.DEDUCTION_DOCTYPE:
                return True
        return False

    def native_finance_install_problem(self):
        """Preflight the installed DocType/field set; a gap is a refusal."""

        try:
            return finance_contract.install_problem(self._finance_meta_fields())
        except Exception:
            return "installed metadata unreadable"

    def native_finance_invoice_install_problem(self):
        """Preflight only the converted invoice lane's required fields."""

        try:
            return finance_contract.invoice_install_problem(
                self._finance_meta_fields()
            )
        except Exception:
            return "installed metadata unreadable"

    def native_finance_boundary_problem(self):
        """Return pinned-boundary drift for the finance family, or ``None``.

        The two controllers are driven only through their public insert/submit
        lifecycle, so the reviewed boundary is the pinned versions, the
        controller source hashes, the controller MRO, the submittable flags, the
        reviewed class overrides and document events, the wildcard hooks and
        our own fence kinds.
        """

        try:
            snapshot = getattr(self.frappe, "finance_boundary_snapshot", None)
            if snapshot is None:
                snapshot = self._collect_finance_boundary_snapshot()
            return finance_contract.boundary_problem(snapshot)
        except Exception:
            return "boundary unreadable"

    def _collect_finance_boundary_snapshot(self):
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

        mro = {}
        for doctype in finance_contract.GUARDED_DOCTYPES:
            controller = type(frappe.get_doc({"doctype": doctype}))
            mro[doctype] = tuple(
                f"{base.__module__}.{base.__name__}" for base in controller.__mro__
            )

        sources = {
            "sales_invoice": (
                "accounts",
                "doctype",
                "sales_invoice",
                "sales_invoice.py",
            ),
            "payment_entry": (
                "accounts",
                "doctype",
                "payment_entry",
                "payment_entry.py",
            ),
        }
        source_sha256 = {}
        for key, parts in sources.items():
            path = frappe.get_app_path("erpnext", *parts)
            with open(path, "rb") as handle:
                source_sha256[key] = hashlib.sha256(handle.read()).hexdigest()

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
                    finance_contract.INVOICE_DOCTYPE,
                    finance_contract.PAYMENT_DOCTYPE,
                )
            },
            "enabled_server_scripts": len(server_scripts),
            "doc_events": {
                doctype: {
                    event: tuple(handlers or ())
                    for event, handlers in (
                        doc_events.get(doctype) or {}
                    ).items()
                }
                for doctype in finance_contract.GUARDED_DOCTYPES
            },
            "wildcard_doc_events": {
                event: tuple(wildcard.get(event) or ())
                for event in finance_contract.REVIEWED_WILDCARD_EVENTS
            },
            "scheduler_finance_jobs": tuple(
                path
                for handlers in (hooks.get("scheduler_events") or {}).values()
                for path in (handlers or ())
                if any(
                    token in str(path)
                    for token in finance_contract.SCHEDULER_FINANCE_TOKENS
                )
            ),
            "class_overrides": {
                doctype: tuple(overrides.get(doctype) or ())
                + tuple(extends.get(doctype) or ())
                for doctype in finance_contract.GUARDED_DOCTYPES
            },
            "fence_kinds": tuple(finance_contract.FINANCE_KINDS),
        }

    def _finance_money(self, value):
        """Coerce an installed numeric (float or Decimal) or return ``None``."""

        if value is None or isinstance(value, bool):
            return None
        try:
            return float(value)
        except Exception:
            return None

    def native_finance_can_write(self, doctype):
        """The acting user's native create authority on one accounting document."""

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

    def native_finance_can_submit(self, doctype):
        """The acting user's native submit authority on one accounting document."""

        try:
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="submit",
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def _finance_field_readable(self, doctype, fieldname):
        """True when one DocField is readable and unmasked for the session user."""

        meta = self.frappe.get_meta(doctype)
        accessible_permlevels = meta.get_permlevel_access(
            "read", user=self.frappe.session.user
        )
        field_definition = meta.get_field(fieldname)
        if (
            field_definition is None
            or field_definition.permlevel not in accessible_permlevels
        ):
            return False
        masked_fields = meta.get_masked_fields() or ()
        return not any(
            getattr(masked, "fieldname", None) == fieldname
            for masked in masked_fields
        )

    def _finance_can_read_document(self, doctype, name):
        """Check native read permission against the loaded document.

        Passing only a docname can skip row-level user-permission conditions;
        loading the document first and passing the object makes Frappe apply
        the current user's own document and user-permission checks.
        """

        if not isinstance(name, str) or not name.strip():
            return False
        try:
            document = self.frappe.get_doc(doctype, name)
            return bool(
                self.frappe.has_permission(
                    doctype,
                    ptype="read",
                    doc=document,
                    user=self.frappe.session.user,
                )
            )
        except Exception:
            return False

    def _finance_price_list_currency(self, price_list, company, transaction_date):
        """Native price-list currency and conversion rate.

        Uses the installed pricing helper, not a local Price List selector. A
        failure is reported as unreadable; the caller refuses rather than
        guessing a currency.
        """

        try:
            from erpnext.stock.get_item_details import (
                get_price_list_currency_and_exchange_rate,
            )

            make_context = getattr(self.frappe, "_dict", None)
            if not callable(make_context):
                return None, None
            details = get_price_list_currency_and_exchange_rate(
                make_context(
                    {
                        "doctype": finance_contract.INVOICE_DOCTYPE,
                        "price_list": price_list,
                        "company": company,
                        "transaction_date": transaction_date,
                    }
                )
            )
        except Exception:
            return None, None
        if not isinstance(details, dict):
            return None, None
        currency = self._text_value(details.get("price_list_currency"))
        rate = details.get("plc_conversion_rate")
        try:
            rate = float(rate) if rate is not None else 1.0
        except Exception:
            return currency, None
        return currency, rate

    def _finance_money_rounder(self, value):
        """Round with the installed Frappe policy, never Python ``round``."""

        try:
            flt = self.frappe.utils.flt
            return float(flt(value, 2))
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def _finance_get_item_details(self, args):
        """Call the installed native pricing method; never a local selector."""

        try:
            from erpnext.stock.get_item_details import get_item_details

            make_context = getattr(self.frappe, "_dict", None)
            if not callable(make_context):
                raise ValueError("frappe._dict is unavailable")
            details = get_item_details(make_context(args))
        except Exception:
            raise patient_write.StoreUnavailable() from None
        if not isinstance(details, dict):
            raise patient_write.StoreUnavailable()
        return details

    def _finance_verified_item_price(self, args, item, line, currency):
        """One native priced line, verified against the requested basis.

        Native pricing may resolve customer, date, UOM and packing-unit prices.
        This lane only accepts a result whose quantity, stock UOM, conversion
        factor, currency and rate equal the requested stock-UOM line and whose
        rate has no unverified discount or margin applied.
        """

        details = self._finance_get_item_details(args)
        raw_rate = self._finance_money(details.get("rate"))
        price_list_rate = self._finance_money(details.get("price_list_rate"))
        qty = self._finance_money(details.get("qty"))
        conversion_factor = self._finance_money(details.get("conversion_factor"))
        discount_amount = self._finance_money(details.get("discount_amount")) or 0.0
        discount_percentage = (
            self._finance_money(details.get("discount_percentage")) or 0.0
        )
        margin_rate = (
            self._finance_money(details.get("margin_rate_or_amount")) or 0.0
        )
        pricing_rules = details.get("pricing_rules")
        has_pricing_rule = bool(
            pricing_rules and pricing_rules not in ("[]", "null", "None")
        )
        uom = self._text_value(details.get("uom"))
        stock_uom = self._text_value(details.get("stock_uom"))
        priced_currency = self._text_value(details.get("currency"))
        expected_uom = item.get("stock_uom")
        tolerance = finance_contract.MONEY_TOLERANCE

        # The installed method leaves ``rate`` at zero when no pricing rule
        # rewrites it; the resolved price-list rate is then the line rate. Any
        # applied rule, discount or margin must still be refused as unverified.
        rate = raw_rate
        if (
            (rate is None or rate <= 0)
            and not has_pricing_rule
            and abs(discount_amount) <= tolerance
            and abs(discount_percentage) <= tolerance
            and abs(margin_rate) <= tolerance
        ):
            rate = price_list_rate
        if rate is not None:
            rate = self._finance_money_rounder(rate)
        if price_list_rate is not None:
            price_list_rate = self._finance_money_rounder(price_list_rate)
        verified = (
            rate is not None
            and rate > 0
            and price_list_rate is not None
            and price_list_rate > 0
            and abs(rate - price_list_rate) <= tolerance
            and abs(discount_amount) <= tolerance
            and abs(discount_percentage) <= tolerance
            and abs(margin_rate) <= tolerance
            and not has_pricing_rule
            and qty is not None
            and abs(qty - line["qty"]) <= tolerance
            and conversion_factor is not None
            and abs(conversion_factor - 1.0) <= tolerance
            and uom == expected_uom
            and stock_uom == expected_uom
            and (priced_currency is None or priced_currency == currency)
        )
        return {
            "rate": rate,
            "income_account": self._text_value(details.get("income_account")),
            "cost_center": self._text_value(details.get("cost_center")),
            "verified": verified,
        }

    def _finance_item(self, item_code):
        exists = False
        try:
            exists = bool(
                self.frappe.db.exists(finance_contract.ITEM_DOCTYPE, item_code)
            )
        except Exception:
            return {"exists": False, "denied": True}
        if not exists:
            return {"exists": False, "denied": False}
        if not self._finance_can_read_document(
            finance_contract.ITEM_DOCTYPE, item_code
        ):
            return {"exists": False, "denied": True}
        if not all(
            self._finance_field_readable(
                finance_contract.ITEM_DOCTYPE, field
            )
            for field in ("is_sales_item", "stock_uom")
        ):
            return {"exists": False, "denied": True}
        try:
            row = self.frappe.db.get_value(
                finance_contract.ITEM_DOCTYPE,
                item_code,
                ["is_sales_item", "stock_uom"],
                as_dict=True,
            )
        except Exception:
            row = None
        if row is None:
            return {"exists": False, "denied": False}
        get = getattr(row, "get", lambda key, default=None: default)
        return {
            "exists": True,
            "denied": False,
            "is_sales_item": bool(get("is_sales_item")),
            "stock_uom": self._text_value(get("stock_uom")),
        }

    def _finance_company_defaults(self, company):
        if not company:
            return {}
        try:
            row = self.frappe.db.get_value(
                finance_contract.COMPANY_DOCTYPE,
                company,
                [
                    "default_currency",
                    "default_receivable_account",
                    "default_income_account",
                    "cost_center",
                ],
                as_dict=True,
            )
        except Exception:
            row = None
        if row is None:
            return {}
        get = getattr(row, "get", lambda key, default=None: default)
        return {
            "default_currency": self._text_value(get("default_currency")),
            "default_receivable_account": self._text_value(
                get("default_receivable_account")
            ),
            "default_income_account": self._text_value(
                get("default_income_account")
            ),
            "cost_center": self._text_value(get("cost_center")),
        }

    def native_finance_invoice_detail(self, payload, discount_enabled=False):
        """Read the invoice decision fields through native pricing authority.

        Customer, Item and Company are read under the acting user's own
        document permission (loaded-document checks apply user-permission
        conditions). Item pricing is resolved by the installed ERPNext
        ``get_item_details`` method, so Cashier roles do not need Item Price or
        Price List DocPerm and no purchase price is exposed. The native result
        is accepted only when its quantity, stock UOM, conversion factor,
        currency and rate match this stock-UOM line and carry no unverified
        discount or margin. The company is exactly the framework's native
        session default; this lane never guesses a single installed company.
        Discounts remain unavailable.
        """

        try:
            company, currency = self._purchase_draft_company_currency()
            company_denied = bool(company) and not self._finance_can_read_document(
                finance_contract.COMPANY_DOCTYPE, company
            )
            defaults = (
                {} if company_denied else self._finance_company_defaults(company)
            )
            today = self.site_today()
            customer = payload["customer"]
            customer_exists = bool(
                self.frappe.db.exists(finance_contract.CUSTOMER_DOCTYPE, customer)
            )
            price_currency = None
            price_conversion = None
            price_conversion_unverified = False
            if company and currency:
                price_currency, price_conversion = (
                    self._finance_price_list_currency(
                        payload["selling_price_list"], company, today
                    )
                )
                price_conversion_unverified = (
                    price_conversion is None
                    or abs(price_conversion - 1.0)
                    > finance_contract.MONEY_TOLERANCE
                )

            prices = {}
            items = {}
            income_accounts = {}
            cost_centers = {}
            price_unverified = set()
            cost_center = ""
            for line in payload["items"]:
                code = line["item_code"]
                if code in items:
                    continue
                item = self._finance_item(code)
                items[code] = item
                if not item.get("exists") or item.get("denied") is True:
                    continue
                if (
                    not company
                    or not currency
                    or not price_currency
                    or price_currency != currency
                    or price_conversion_unverified
                ):
                    price_unverified.add(code)
                    continue
                args = {
                    "doctype": finance_contract.INVOICE_DOCTYPE,
                    "item_code": code,
                    "company": company,
                    "customer": customer,
                    "currency": currency,
                    "conversion_rate": 1.0,
                    "selling_price_list": payload["selling_price_list"],
                    "price_list": payload["selling_price_list"],
                    "transaction_date": today,
                    "posting_date": today,
                    "qty": line["qty"],
                    "uom": item.get("stock_uom"),
                    "stock_uom": item.get("stock_uom"),
                    "conversion_factor": 1.0,
                    "update_stock": finance_contract.PINNED_UPDATE_STOCK,
                    "is_pos": 0,
                }
                try:
                    priced = self._finance_verified_item_price(
                        args, item, line, currency
                    )
                except Exception:
                    price_unverified.add(code)
                    continue
                prices[code] = priced["rate"]
                income_accounts[code] = priced["income_account"] or ""
                if priced["cost_center"]:
                    cost_centers[code] = priced["cost_center"]
                    if not cost_center:
                        cost_center = priced["cost_center"]
                if priced["verified"] is not True:
                    price_unverified.add(code)

            if not cost_center:
                cost_center = defaults.get("cost_center") or ""
            return {
                "today": today,
                "company": company,
                "currency": currency,
                "company_denied": company_denied,
                "customer_exists": customer_exists,
                "customer_denied": customer_exists
                and not self._finance_can_read_document(
                    finance_contract.CUSTOMER_DOCTYPE, customer
                ),
                "price_currency": price_currency or "",
                "price_conversion_unverified": price_conversion_unverified,
                "prices": prices,
                "price_unverified": tuple(sorted(price_unverified)),
                "price_denied": False,
                "items": items,
                "receivable_account": (
                    defaults.get("default_receivable_account") or ""
                ),
                "income_accounts": income_accounts,
                "cost_centers": cost_centers,
                "cost_center": cost_center,
                "money_rounder": self._finance_money_rounder,
                "discount_available": False,
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def _finance_invoice_state(self, invoice):
        if not invoice:
            return {"exists": False}
        try:
            row = self.frappe.db.get_value(
                finance_contract.INVOICE_DOCTYPE,
                invoice,
                [
                    "docstatus",
                    "status",
                    "outstanding_amount",
                    "grand_total",
                    "customer",
                ],
                as_dict=True,
            )
        except Exception:
            row = None
        if row is None:
            return {"exists": False}
        get = getattr(row, "get", lambda key, default=None: default)
        return {
            "exists": True,
            "docstatus": get("docstatus"),
            "status": self._text_value(get("status")),
            "outstanding_amount": self._finance_money(
                get("outstanding_amount")
            ),
            "grand_total": self._finance_money(get("grand_total")),
            "customer": self._text_value(get("customer")),
        }

    def native_finance_invoice_view(self, native_invoice_id):
        """Three-field invoice read under document and field rights.

        The document read permission is checked first. ``status`` must then
        exist at a readable permlevel and must not be masked for the current
        user; ``name`` and ``docstatus`` are standard columns read only after
        that check. No customer, item, quantity or amount is fetched here.
        ``None`` covers a denied document, a restricted or masked status, a
        malformed row and any adapter failure.
        """

        if (
            not isinstance(native_invoice_id, str)
            or not native_invoice_id.strip()
        ):
            return None
        try:
            frappe = self.frappe
            document = frappe.get_doc(
                finance_contract.INVOICE_DOCTYPE, native_invoice_id
            )
            if not frappe.has_permission(
                finance_contract.INVOICE_DOCTYPE,
                ptype="read",
                doc=document,
                user=frappe.session.user,
            ):
                return None
            if not self._finance_field_readable(
                finance_contract.INVOICE_DOCTYPE, "status"
            ):
                return None
        except Exception:
            return None
        name = self._text_value(getattr(document, "name", None))
        status = self._text_value(getattr(document, "status", None))
        docstatus = getattr(document, "docstatus", None)
        if not name or not status:
            return None
        if (
            not isinstance(docstatus, int)
            or isinstance(docstatus, bool)
            or docstatus not in (0, 1, 2)
        ):
            return None
        return {
            "invoice_id": name,
            "status": status,
            "docstatus": docstatus,
        }

    def native_finance_invoice_names(self, customer, limit):
        """Bounded, permission-aware invoice names for one readable Customer.

        ``frappe.get_list`` applies the framework's own permission filtering and
        permission-query conditions for the session user; ``get_all`` and raw
        SQL are deliberately never used on this discovery path. The customer
        filter and ``status`` are DocFields that can carry a permlevel or a
        mask, so both pass the current user's own field-level read check before
        the query is built. One row beyond the bound is requested, so an
        over-full page is reported as ``None`` rather than silently truncated.
        The caller still applies the document and field-level checks per record.
        """

        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or limit < 1
            or limit > 1000
        ):
            return None
        if not isinstance(customer, str) or not customer.strip():
            return None
        try:
            if not self._finance_field_readable(
                finance_contract.INVOICE_DOCTYPE, "status"
            ):
                return None
            if not self._finance_field_readable(
                finance_contract.INVOICE_DOCTYPE, "customer"
            ):
                return None
            rows = self.frappe.get_list(
                finance_contract.INVOICE_DOCTYPE,
                filters={"customer": customer},
                fields=["name"],
                order_by="modified desc, name asc",
                limit_page_length=limit + 1,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        if len(rows) > limit:
            return None
        names = []
        for row in rows:
            name = (
                row.get("name")
                if hasattr(row, "get")
                else getattr(row, "name", None)
            )
            name = self._text_value(name)
            if not name:
                return None
            names.append(name)
        return names

    def _finance_mode_of_payment(self, name, company):
        if not name:
            return {"exists": False, "account": "", "accepted": False}
        try:
            exists = bool(
                self.frappe.db.exists(
                    finance_contract.MODE_OF_PAYMENT_DOCTYPE, name
                )
            )
        except Exception:
            exists = False
        account = ""
        if exists and company:
            try:
                account = self._text_value(
                    self.frappe.db.get_value(
                        finance_contract.MODE_OF_PAYMENT_ACCOUNT_DOCTYPE,
                        {"parent": name, "company": company},
                        "default_account",
                    )
                )
            except Exception:
                account = ""
        return {
            "exists": exists,
            "account": account,
            "accepted": name in finance_contract.ACCEPTED_PAYMENT_MODES,
        }

    def _finance_payment_recorded(self, invoice):
        """True when a committed payment receipt already names this invoice."""

        try:
            records = self._ledger().committed_finance_receipts(
                limit=write_fence.MAX_RECEIPT_ENUMERATION,
                kind=finance_contract.PAYMENT_KIND,
            )
        except write_fence.EnumerationOverflow as exc:
            raise finance_contract.FinanceBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc
        for record in records:
            try:
                receipt = finance_contract.decode_receipt(record.receipt)
            except finance_contract.FinanceContractError:
                raise patient_write.WriteUncertain() from None
            if receipt.invoice == invoice:
                return True
        return False

    def native_finance_payment_detail(self, payload):
        """Read the pre-fence payment decision fields."""

        try:
            company, _currency = self._purchase_company_currency()
            defaults = self._finance_company_defaults(company)
            return {
                "today": self.site_today(),
                "company": company,
                "currency": self._text_value(
                    defaults.get("default_currency")
                ),
                "invoice": self._finance_invoice_state(payload["invoice"]),
                "mode": self._finance_mode_of_payment(
                    payload["mode_of_payment"], company
                ),
                "payment_exists": self._finance_payment_recorded(
                    payload["invoice"]
                ),
                "receivable_account": (
                    defaults.get("default_receivable_account") or ""
                ),
            }
        except Exception:
            raise patient_write.StoreUnavailable() from None

    def _finance_counts(self):
        vouchers = (
            finance_contract.INVOICE_DOCTYPE,
            finance_contract.PAYMENT_DOCTYPE,
        )
        return {
            "gl_rows": int(
                self.frappe.db.count(
                    finance_contract.GL_ENTRY_DOCTYPE,
                    filters={"voucher_type": ("in", list(vouchers))},
                )
            ),
            "ple_rows": int(
                self.frappe.db.count(
                    finance_contract.PAYMENT_LEDGER_DOCTYPE,
                    filters={"voucher_type": ("in", list(vouchers))},
                )
            ),
            "sle_rows": int(
                self.frappe.db.count(
                    finance_contract.STOCK_LEDGER_DOCTYPE,
                    filters={"voucher_type": ("in", list(vouchers))},
                )
            ),
        }

    def native_finance_baseline(self, kind, invoice=None):
        """Pre-fence GL, Payment Ledger, Stock Ledger and invoice baselines."""

        if kind not in finance_contract.FINANCE_KINDS:
            raise patient_write.WriteUncertain()
        try:
            counts = self._finance_counts()
            state = self._finance_invoice_state(invoice) if invoice else {}
            return {
                **counts,
                "invoice_outstanding": state.get("outstanding_amount"),
                "invoice_status": state.get("status"),
            }
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_and_submit_native_sales_invoice(self, prepared):
        """One invoice unit: insert, then submit the installed document."""

        try:
            rows = []
            for line in prepared["items"]:
                rows.append(
                    {
                        "item_code": line["item_code"],
                        "qty": line["qty"],
                        "rate": line["rate"],
                        "amount": line["amount"],
                        "price_list_rate": line.get("price_list_rate")
                        or line["rate"],
                        "uom": line.get("uom"),
                        "stock_uom": line.get("stock_uom") or line.get("uom"),
                        "income_account": line["income_account"],
                        "cost_center": line["cost_center"],
                    }
                )
            document = {
                "doctype": finance_contract.INVOICE_DOCTYPE,
                "customer": prepared["customer"],
                "company": prepared["company"],
                "currency": prepared["currency"],
                "conversion_rate": prepared.get("conversion_rate") or 1.0,
                "selling_price_list": prepared["selling_price_list"],
                "due_date": prepared["due_date"],
                "posting_date": prepared["posting_date"],
                "update_stock": finance_contract.PINNED_UPDATE_STOCK,
                "debit_to": prepared["debit_to"],
                "cost_center": prepared["cost_center"],
                "taxes": [],
                "items": rows,
            }
            deductions = prepared.get("deductions") or ()
            if deductions:
                document["deductions"] = [
                    dict(row) for row in deductions
                ]
            invoice = self.frappe.get_doc(document)
            invoice.insert()
            invoice.submit()
            return self._text_value(invoice.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def create_native_payment_entry(self, prepared):
        """One ``Payment Entry`` Receive with the server-built allocation."""

        try:
            references = []
            for row in prepared["references"]:
                references.append(
                    {
                        "reference_doctype": row["reference_doctype"],
                        "reference_name": row["reference_name"],
                        "allocated_amount": row["allocated_amount"],
                        "outstanding_amount": row.get("outstanding_amount"),
                        "total_amount": row.get("total_amount"),
                    }
                )
            document = {
                "doctype": finance_contract.PAYMENT_DOCTYPE,
                "payment_type": "Receive",
                "party_type": "Customer",
                "party": prepared["party"],
                "company": prepared["company"],
                "paid_from": prepared["paid_from"],
                "paid_to": prepared["paid_to"],
                "paid_amount": prepared["paid_amount"],
                "received_amount": prepared["received_amount"],
                "source_exchange_rate": prepared.get("source_exchange_rate")
                or 1.0,
                "target_exchange_rate": prepared.get("target_exchange_rate")
                or 1.0,
                "posting_date": prepared["posting_date"],
                "reference_no": prepared.get("reference_no") or "",
                "reference_date": prepared["reference_date"],
                "mode_of_payment": prepared["mode_of_payment"],
                "references": references,
            }
            payment = self.frappe.get_doc(document)
            payment.insert()
            payment.submit()
            return self._text_value(payment.name)
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _finance_gl_rows(self, doctype, reference):
        rows = self.frappe.db.get_all(
            finance_contract.GL_ENTRY_DOCTYPE,
            filters={"voucher_type": doctype, "voucher_no": reference},
            fields=["account", "debit", "credit"],
            limit=200,
        ) or ()
        return [
            {
                "account": self._text_value(
                    getattr(row, "get", lambda key, default=None: default)(
                        "account"
                    )
                ),
                "debit": getattr(row, "get", lambda key, default=None: default)(
                    "debit"
                )
                or 0.0,
                "credit": getattr(row, "get", lambda key, default=None: default)(
                    "credit"
                )
                or 0.0,
            }
            for row in rows
        ]

    def _finance_payment_facts(self, reference, expected, baseline):
        """Closed-world re-read: payment state, GL classification, invoice delta."""

        if not isinstance(baseline, dict) or not isinstance(expected, dict):
            raise patient_write.WriteUncertain()
        try:
            row = self.frappe.db.get_value(
                finance_contract.PAYMENT_DOCTYPE,
                reference,
                ["docstatus", "payment_type", "paid_amount"],
                as_dict=True,
            )
            if row is None:
                raise patient_write.WriteUncertain()
            get = getattr(row, "get", lambda key, default=None: default)
            gl_rows = self._finance_gl_rows(
                finance_contract.PAYMENT_DOCTYPE, reference
            )
            unexpected = finance_contract.payment_gl_unexpected(
                gl_rows, expected
            )
            counts = self._finance_counts()
            invoice_after = self._finance_invoice_state(expected.get("invoice"))
            before_value = self._finance_money(
                baseline.get("invoice_outstanding")
            ) or 0.0
            after_value = self._finance_money(
                invoice_after.get("outstanding_amount")
            ) or 0.0
            return {
                "doctype": finance_contract.PAYMENT_DOCTYPE,
                "docstatus": get("docstatus"),
                "gl_rows": counts["gl_rows"]
                - int(baseline.get("gl_rows") or 0),
                "ple_rows": counts["ple_rows"]
                - int(baseline.get("ple_rows") or 0),
                "unexpected_gl_rows": unexpected,
                "allocated": self._finance_money(get("paid_amount")),
                "outstanding_delta": round(after_value - before_value, 2),
                "outstanding_after": round(after_value, 2),
                "invoice_status": invoice_after.get("status"),
            }
        except patient_write.WriteUncertain:
            raise
        except Exception:
            raise patient_write.WriteUncertain() from None

    def _finance_permission_filtered_rows(self, doctype, filters, fields, limit):
        """Read ledger rows through the user's own native read permission."""

        try:
            if self.frappe.has_permission(
                doctype, ptype="read", user=self.frappe.session.user
            ) is not True:
                return None
            rows = self.frappe.get_list(
                doctype,
                filters=filters,
                fields=fields,
                order_by="name asc",
                limit_page_length=limit,
            )
        except Exception:
            return None
        if not isinstance(rows, (list, tuple)):
            return None
        return list(rows)

    def native_finance_invoice_posted_facts(self, reference, expected):
        """Read the actual submitted invoice and its posted effects.

        Runs before the caller's commit. The document is loaded and re-checked
        under the acting user's own read permission (so user-permission row
        conditions apply), and every field this comparison uses must be at a
        readable, unmasked permlevel. GL, Payment Ledger and Stock Ledger rows
        are read through permission-filtered ``get_list``; a denied or unreadable
        ledger refuses the verification instead of claiming success.
        """

        if not isinstance(reference, str) or not reference.strip():
            return None
        if not isinstance(expected, dict):
            return None
        try:
            frappe = self.frappe
            document = frappe.get_doc(
                finance_contract.INVOICE_DOCTYPE, reference
            )
            if not frappe.has_permission(
                finance_contract.INVOICE_DOCTYPE,
                ptype="read",
                doc=document,
                user=frappe.session.user,
            ):
                return None
            for field in (
                "status",
                "currency",
                "grand_total",
                "net_total",
                "outstanding_amount",
                "update_stock",
            ):
                if not self._finance_field_readable(
                    finance_contract.INVOICE_DOCTYPE, field
                ):
                    return None

            gl_rows = self._finance_permission_filtered_rows(
                finance_contract.GL_ENTRY_DOCTYPE,
                {
                    "voucher_type": finance_contract.INVOICE_DOCTYPE,
                    "voucher_no": reference,
                },
                ["account", "debit", "credit"],
                int(expected.get("gl_rows_max", expected["gl_rows"])) + 1,
            )
            if gl_rows is None:
                return None
            debit_by_account, credit_by_account, unexpected = (
                finance_contract.invoice_gl_accounts(gl_rows, expected)
            )
            debit_total = sum(debit_by_account.values())
            credit_total = sum(credit_by_account.values())
            ple_rows = self._finance_permission_filtered_rows(
                finance_contract.PAYMENT_LEDGER_DOCTYPE,
                {
                    "voucher_type": finance_contract.INVOICE_DOCTYPE,
                    "voucher_no": reference,
                },
                ["name"],
                2,
            )
            if ple_rows is None:
                return None

            return {
                "doctype": finance_contract.INVOICE_DOCTYPE,
                "docstatus": getattr(document, "docstatus", None),
                "status": self._text_value(getattr(document, "status", None)),
                "currency": self._text_value(
                    getattr(document, "currency", None)
                ),
                "grand_total": self._finance_money(
                    getattr(document, "grand_total", None)
                ),
                "net_total": self._finance_money(
                    getattr(document, "net_total", None)
                ),
                "outstanding_amount": self._finance_money(
                    getattr(document, "outstanding_amount", None)
                ),
                "update_stock": getattr(document, "update_stock", None),
                "gl_rows": len(gl_rows),
                "debit_total": round(debit_total, 2),
                "credit_total": round(credit_total, 2),
                "unexpected_gl_rows": unexpected,
                "gl_accounts": {
                    "debit": debit_by_account,
                    "credit": credit_by_account,
                },
                # No Stock Ledger Entry read: Cashier has no native SLE permission
                # and a Sales Invoice is pinned to update_stock=0. The posted
                # document's own update_stock is verified instead.
                "sle_rows": None,
                "sle_verified": False,
                "ple_rows": len(ple_rows),
            }
        except Exception:
            return None

    def native_finance_payment_facts(self, reference, expected, baseline):
        return self._finance_payment_facts(reference, expected, baseline)

    # -- finance fence, receipts and acknowledgement -----------------------

    def list_committed_finance_receipts(
        self, limit, kind=None, operation_key=None
    ):
        ledger = self._ledger()
        try:
            return ledger.committed_finance_receipts(
                limit=limit, kind=kind, operation_key=operation_key
            )
        except write_fence.EnumerationOverflow as exc:
            raise finance_contract.FinanceBudgetExhausted() from exc
        except write_fence.StoreUnavailable as exc:
            raise patient_write.StoreUnavailable() from exc
        except write_fence.FenceError as exc:
            raise patient_write.WriteUncertain() from exc

    def acknowledge_finance_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        if kind not in write_fence.FINANCE_OPERATION_KINDS:
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
        expected = finance_contract.ack_token(
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
    def finance_write_fence(self, actor, kind, operation_key, payload_digest):
        if kind not in write_fence.FINANCE_OPERATION_KINDS:
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
