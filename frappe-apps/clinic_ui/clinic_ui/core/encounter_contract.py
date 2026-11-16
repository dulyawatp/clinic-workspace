"""Closed-world contract for the pinned Patient Encounter write path.

The design rulings this module encodes (they are not re-decided here):

* a draft holds ``symptoms``, the diagnosis child table, the drug, lab and
  procedure prescription child tables; clinical notes are separate
  ``Clinical Note`` documents; ``physical_examination`` and ``clinical_notes``
  on the encounter are never write targets; vitals go to ``Vital Signs``,
* completion is DOC-only and the closed-world re-read EXPECTS the native
  cascade: appointment ``Closed``, encounter submitted with status
  ``Completed`` and exactly one submitted ``Service Request`` per procedure and
  lab prescription row (drug rows do not cascade; ordered is not dispensed),
* an amendment is a NEW ``Clinical Note`` addendum that references the
  finalized encounter; the original note and the encounter are never rewritten.

Installed field names are not hardcoded here. Each logical field lists the
installed candidates; the adapter resolves them from the site's own metadata
preflight (``frappe.get_meta``) and a missing piece refuses the write
pre-fence instead of guessing.

Every designed refusal is a pre-fence decision: role, boundary, install
preflight, target eligibility, required-before-complete fields and budget.
"""

from __future__ import annotations

import re

ENCOUNTER_DOCTYPE = "Patient Encounter"
CLINICAL_NOTE_DOCTYPE = "Clinical Note"
VITAL_SIGNS_DOCTYPE = "Vital Signs"
SERVICE_REQUEST_DOCTYPE = "Service Request"

ENCOUNTER_DRAFT_KIND = "encounter_draft"
ENCOUNTER_COMPLETE_KIND = "encounter_complete"
ENCOUNTER_AMEND_KIND = "encounter_amend"
ENCOUNTER_KINDS = (
    ENCOUNTER_DRAFT_KIND,
    ENCOUNTER_COMPLETE_KIND,
    ENCOUNTER_AMEND_KIND,
)

DRAFT_DOCSTATUS = 0
FINAL_DOCSTATUS = 1
COMPLETED_STATUS = "Completed"
CLOSED_APPOINTMENT_STATUS = "Closed"
# Completion may close an appointment only from these two native statuses; any
# other status means the cascade this contract re-reads cannot be expected.
CASCADE_SOURCE_STATUSES = ("Open", "Checked In")

MAX_ROWS = 20
MAX_TEXT_LENGTH = 2000
MAX_CODE_LENGTH = 140
MAX_NOTE_LENGTH = 5000
MAX_ADDENDUM_LENGTH = 5000

# Installed-field preflight. Logical name -> installed candidates, per DocType.
# A DocType that is absent, or a logical field with no installed candidate,
# refuses the family with a recorded reason (design ruling 8).
REQUIRED_FIELDS = {
    ENCOUNTER_DOCTYPE: {
        "patient": ("patient",),
        "practitioner": ("practitioner",),
        "encounter_date": ("encounter_date",),
        "appointment": ("appointment",),
        "symptoms": ("symptoms",),
        "diagnosis": ("diagnosis",),
        "drug_prescription": ("drug_prescription",),
        "lab_test_prescription": ("lab_test_prescription",),
        "procedure_prescription": ("procedure_prescription",),
    },
    CLINICAL_NOTE_DOCTYPE: {
        "patient": ("patient",),
        "practitioner": ("practitioner",),
        "note": ("note",),
        "reference_doctype": ("reference_doctype",),
        "reference_docname": ("reference_docname",),
    },
    VITAL_SIGNS_DOCTYPE: {
        "patient": ("patient",),
    },
    SERVICE_REQUEST_DOCTYPE: {
        "patient": ("patient",),
        "status": ("status",),
    },
    "Complaint Detail": {
        "complaint": ("complaint",),
    },
    "Patient Encounter Diagnosis": {
        "diagnosis": ("diagnosis",),
    },
    "Drug Prescription": {
        "drug_code": ("drug_code", "medication"),
        "dosage": ("dosage",),
        "period": ("period",),
    },
    "Lab Prescription": {
        "lab_test_code": ("lab_test_code", "lab_test_name", "test_name"),
    },
    "Procedure Prescription": {
        "procedure": ("procedure", "procedure_name"),
    },
}

# Vitals live on their own DocType. Only these installed columns are written.
VITAL_FIELDS = {
    "temperature": ("temperature",),
    "pulse": ("pulse",),
    "respiratory_rate": ("respiratory_rate",),
    "bp_systolic": ("bp_systolic",),
    "bp_diastolic": ("bp_diastolic",),
    "height": ("height",),
    "weight": ("weight",),
}

# The required-before-complete set (CSV:70), in the order the operator sees it,
# with the per-field problem text. The values are read from the persisted
# encounter, its child rows and its linked Clinical Note, never from the client.
COMPLETION_REQUIREMENTS = (
    ("complaint", "chief complaint is required before completion"),
    ("diagnosis", "at least one diagnosis is required before completion"),
    ("assessment", "assessment is required before completion"),
    ("plan", "plan is required before completion"),
    ("followup", "follow-up is required before completion"),
    (
        "allergy_reviewed",
        "allergy review acknowledgement is required before completion",
    ),
)

# Healthcare Settings switches that make the native cascade send messages or
# create billing/free-follow-up effects when the appointment is closed. The
# family gate checks exactly this enumerated set; nothing else is consulted.
ENCOUNTER_UNSAFE_SETTINGS = (
    "show_payment_popup",
    "send_appointment_confirmation",
    "send_appointment_reminder",
    "enable_free_follow_ups",
    "apply_free_follow_ups_on_encounters",
)
ENCOUNTER_UNSAFE_SETTING_LINKS = ("default_google_calendar",)

# The reviewed executable boundary: versions pinned with the appointment family
# (installed healthcare v16.6.1) and no wildcard handler on the encounter path.
REVIEWED_VERSIONS = {
    "frappe": "16.54.0",
    "erpnext": "16.52.1",
    "healthcare": "16.6.1",
}

# Amendment addendum marker. The addendum is a new Clinical Note whose note
# starts with this pinned line; the original note text is never changed.
ADDENDUM_PREFIX = "[clinic-ui amendment]"
MAX_ADDENDUM_LINE_LENGTH = MAX_ADDENDUM_LENGTH + 200

# Clinical Note has one text field. The designed note sections are stored as
# pinned, single-line labelled paragraphs so completion can re-read them without
# inventing a field. Parsing is strict: an unlabelled line belongs to the
# previous section; unknown labels are ignored.
NOTE_LABELS = {
    "assessment": "Assessment",
    "plan": "Plan",
    "rationale": "Rationale",
    "followup": "Follow-up",
}
_NOTE_LINE_RE = re.compile(
    r"^(Assessment|Plan|Rationale|Follow-up): ?(.*)$"
)

_NUMERIC_RE = re.compile(r"^-?\d{1,4}(\.\d{1,2})?$")


class EncounterContractError(ValueError):
    """An encounter contract input or value is outside the accepted shape."""


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete.

    ``meta_fields`` maps a DocType name to the set of installed field names
    (``frappe.get_meta(doctype).get_valid_columns()`` in the adapter). An
    absent DocType or a logical field with no installed candidate is a
    pre-fence refusal, not a guess.
    """

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    for doctype, fields in REQUIRED_FIELDS.items():
        installed = meta_fields.get(doctype)
        if not isinstance(installed, (set, frozenset, tuple, list)):
            return f"missing DocType {doctype}"
        installed = set(installed)
        for logical, candidates in fields.items():
            if not any(candidate in installed for candidate in candidates):
                return f"missing field {doctype}.{logical}"
    for logical, candidates in VITAL_FIELDS.items():
        installed = meta_fields.get(VITAL_SIGNS_DOCTYPE)
        if not isinstance(installed, (set, frozenset, tuple, list)):
            return f"missing DocType {VITAL_SIGNS_DOCTYPE}"
        if not any(candidate in set(installed) for candidate in candidates):
            return f"missing field {VITAL_SIGNS_DOCTYPE}.{logical}"
    return None


def resolve_field(meta_fields, doctype, logical, candidates=None):
    """Return the installed field name for a logical field, or ``None``."""

    if candidates is None:
        candidates = (REQUIRED_FIELDS.get(doctype) or {}).get(logical)
    if not candidates:
        return None
    installed = meta_fields.get(doctype) if isinstance(meta_fields, dict) else None
    if not isinstance(installed, (set, frozenset, tuple, list)):
        return None
    installed = set(installed)
    for candidate in candidates:
        if candidate in installed:
            return candidate
    return None


def boundary_problem(snapshot):
    """Return the first unreviewed boundary drift, or ``None`` when pinned."""

    if not isinstance(snapshot, dict):
        return "boundary snapshot unreadable"
    versions = snapshot.get("versions")
    if not isinstance(versions, dict):
        return "versions unreadable"
    for name, value in REVIEWED_VERSIONS.items():
        if versions.get(name) != value:
            return f"versions drift for {name}"
    if snapshot.get("encounter_submittable") != 1:
        return "Patient Encounter is not submittable"
    if snapshot.get("enabled_server_scripts") != 0:
        return "server scripts are enabled"
    doc_events = snapshot.get("doc_events")
    if not isinstance(doc_events, dict):
        return "doc_events unreadable"
    for doctype in (ENCOUNTER_DOCTYPE, CLINICAL_NOTE_DOCTYPE, VITAL_SIGNS_DOCTYPE):
        if tuple(doc_events.get(doctype) or ()):
            return f"unreviewed doc_events for {doctype}"
    return None


def draft_target_problem(values):
    """Refuse a draft-update target before any fence claim."""

    docstatus = values.get("docstatus")
    if docstatus != DRAFT_DOCSTATUS:
        return "target encounter is not a draft"
    if not values.get("appointment"):
        return "target encounter has no linked appointment"
    return None


def complete_target_problem(values):
    """Refuse a completion target before any fence claim.

    The linked appointment is the cascade source: a missing link, a missing
    appointment row or a status outside ``Open``/``Checked In`` is refused here
    so the closed-world re-read can EXPECT ``Closed``.
    """

    docstatus = values.get("docstatus")
    if docstatus != DRAFT_DOCSTATUS:
        return "target encounter is not a draft"
    if not values.get("appointment"):
        return "target encounter has no linked appointment"
    appointment_status = values.get("appointment_status")
    if appointment_status not in CASCADE_SOURCE_STATUSES:
        return "linked appointment is not open or checked in"
    return None


def amend_target_problem(values):
    """Refuse an amendment target before any fence claim."""

    if values.get("docstatus") != FINAL_DOCSTATUS:
        return "target encounter is not finalized"
    if values.get("status") != COMPLETED_STATUS:
        return "target encounter is not completed"
    if not values.get("clinical_note"):
        return "target encounter has no linked clinical note"
    return None


def completion_problems(values):
    """Return every required-before-complete problem with its field text.

    ``values`` is the persisted detail: ``complaint`` (bool), ``diagnosis_count``
    (int), ``assessment``/``plan``/``followup`` (text) and ``allergy_reviewed``
    (bool). Each tuple is ``(field, message)`` in the designed order.
    """

    problems = []
    if not values.get("complaint"):
        problems.append(COMPLETION_REQUIREMENTS[0])
    if not _positive_count(values.get("diagnosis_count")):
        problems.append(COMPLETION_REQUIREMENTS[1])
    for index, field in ((2, "assessment"), (3, "plan"), (4, "followup")):
        text = values.get(field)
        if not isinstance(text, str) or not text.strip():
            problems.append(COMPLETION_REQUIREMENTS[index])
    if values.get("allergy_reviewed") is not True:
        problems.append(COMPLETION_REQUIREMENTS[5])
    return tuple(problems)


def expected_service_requests(values):
    """One submitted Service Request per procedure and lab prescription row."""

    procedures = values.get("procedure_count")
    labs = values.get("lab_count")
    if not _non_negative_count(procedures) or not _non_negative_count(labs):
        return None
    return procedures + labs


def cascade_problem(values, expected_service_requests):
    """Check the closed-world state after submit.

    EXPECTED side effects: encounter submitted and ``Completed``, the linked
    appointment ``Closed`` and exactly one submitted Service Request per
    procedure/lab row. Drug rows never cascade.
    """

    if values.get("docstatus") != FINAL_DOCSTATUS:
        return "encounter was not submitted"
    if values.get("status") != COMPLETED_STATUS:
        return "encounter status is not Completed"
    if values.get("appointment_status") != CLOSED_APPOINTMENT_STATUS:
        return "linked appointment was not closed"
    submitted = values.get("submitted_service_requests")
    if not _non_negative_count(submitted):
        return "service request count is unreadable"
    if expected_service_requests is None:
        return "expected service request count is unreadable"
    if submitted != expected_service_requests:
        return "service request cascade count differs"
    return None


def amend_postcondition_problem(
    *,
    encounter_modified,
    expected_encounter_modified,
    clinical_note_modified,
    original_note_modified,
    addendum_created,
    expected_note,
):
    """Check the amendment never rewrote the finalized record.

    The addendum must exist, the original Clinical Note's ``modified`` must be
    byte-identical to the locked preimage and the encounter's own ``modified``
    must be unchanged.
    """

    if addendum_created is not True:
        return "addendum was not created"
    if encounter_modified != expected_encounter_modified:
        return "encounter was rewritten by the amendment"
    if clinical_note_modified != original_note_modified:
        return "original clinical note was rewritten"
    if not expected_note:
        return "addendum note is empty"
    return None


def build_note_text(note):
    """Return the pinned labelled note text for one Clinical Note record."""

    if not isinstance(note, dict):
        return ""
    lines = []
    for key in ("assessment", "plan", "rationale", "followup"):
        value = note.get(key)
        if isinstance(value, str) and value.strip():
            lines.append(f"{NOTE_LABELS[key]}: {' '.join(value.splitlines())}")
    return "\n".join(lines)


def parse_note_text(text):
    """Return the designed note sections from the pinned labelled text."""

    sections = {"assessment": "", "plan": "", "rationale": "", "followup": ""}
    if not isinstance(text, str):
        return sections
    current = None
    by_label = {label: key for key, label in NOTE_LABELS.items()}
    for line in text.splitlines():
        match = _NOTE_LINE_RE.match(line)
        if match is not None:
            current = by_label[match.group(1)]
            sections[current] = match.group(2).strip()
            continue
        if current is not None and line.strip():
            sections[current] = (sections[current] + " " + line.strip()).strip()
    return sections


def addendum_note(actor, text):
    """Return the pinned addendum note body for one amendment."""

    if not isinstance(text, str) or not text.strip():
        raise EncounterContractError("addendum is invalid")
    line = f"{ADDENDUM_PREFIX} {actor}: {text.strip()}"
    if len(line) > MAX_ADDENDUM_LINE_LENGTH:
        raise EncounterContractError("addendum is too long")
    return line


def section_problem(role_code, fields):
    """NUR writes vitals only; notes, diagnoses, orders and complaint are DOC.

    The role code gate already admitted DOC or NUR. This is the per-section rule
    (CSV:69/73/86), decided before the fence so a refusal never latches.
    """

    if role_code == "DOC":
        return None
    if role_code != "NUR":
        return "role may not write the encounter"
    for name in (
        "complaint",
        "diagnoses",
        "medications",
        "lab_tests",
        "procedures",
        "note",
    ):
        if fields.get(name):
            return f"nurse may not write {name}"
    return None


def valid_numeric(value):
    """True for a bounded decimal string as the vitals columns accept it."""

    return isinstance(value, str) and _NUMERIC_RE.fullmatch(value) is not None


def _positive_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _non_negative_count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
