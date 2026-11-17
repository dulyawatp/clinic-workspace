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
# The installed complaint child of ``Patient Encounter.symptoms``. The older
# ``Complaint Detail`` DocType is not installed with healthcare v16.6.1.
SYMPTOM_CHILD_DOCTYPE = "Patient Encounter Symptom"

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
        "appointment_type": ("appointment_type",),
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
        # The reference pair is a matched set, checked as a unit by
        # ``note_reference_problem``: resolving the two names independently would
        # admit a half-installed or half-legacy mix that links nothing.
    },
    VITAL_SIGNS_DOCTYPE: {
        "patient": ("patient",),
    },
    SERVICE_REQUEST_DOCTYPE: {
        "patient": ("patient",),
        "status": ("status",),
    },
    SYMPTOM_CHILD_DOCTYPE: {
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

# The installed child-table shape of the encounter's logical sections. A scalar
# column carrying the same name would satisfy a name-only preflight and then fail
# at insert, so the child DocType is pinned in the preflight as well.
TABLE_FIELD_TYPES = ("Table", "Table MultiSelect")
TABLE_FIELDS = {
    ENCOUNTER_DOCTYPE: {
        "symptoms": SYMPTOM_CHILD_DOCTYPE,
        "diagnosis": "Patient Encounter Diagnosis",
        "drug_prescription": "Drug Prescription",
        "lab_test_prescription": "Lab Prescription",
        "procedure_prescription": "Procedure Prescription",
    },
}

# The Clinical Note reference pair, newest first. A note links to the encounter
# through a ``Link`` to ``DocType`` plus a ``Dynamic Link`` that points back at
# that very field. The pair is installed as a unit; the two names are never
# resolved independently.
NOTE_REFERENCE_PAIRS = (
    ("reference_doc", "reference_name"),
    ("reference_doctype", "reference_docname"),
)
NOTE_REFERENCE_DOCTYPE_FIELD_TYPE = "Link"
NOTE_REFERENCE_DOCTYPE_OPTIONS = "DocType"
NOTE_REFERENCE_NAME_FIELD_TYPE = "Dynamic Link"

# Medication rows are outside the reviewed contract. The installed
# ``Drug Prescription.dosage_form`` is required and the payload cannot supply it,
# so a medication-bearing draft would fail after dispatch. It is refused before
# the fence until a reviewed medication contract exists.
MEDICATION_UNSUPPORTED = "medication rows are outside the reviewed encounter contract"

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


def _installed_names(meta_fields, doctype):
    """The installed field names for one DocType, or ``None`` when unknown."""

    if not isinstance(meta_fields, dict):
        return None
    installed = meta_fields.get(doctype)
    if not isinstance(installed, (set, frozenset, tuple, list, dict)):
        return None
    return set(installed)


def _field_shape(installed, fieldname):
    """``(fieldtype, options)`` for one installed field, or ``None``.

    ``None`` also covers the name-only metadata form and frappe's default
    columns, which carry no ``DocField`` row: the shape simply cannot be proven
    there, and the name check is what applies.
    """

    if not isinstance(installed, dict):
        return None
    shape = installed.get(fieldname)
    if isinstance(shape, (tuple, list)) and len(shape) == 2:
        return (shape[0], shape[1])
    return None


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete.

    ``meta_fields`` maps a DocType name to what the adapter resolved from the
    site's own metadata: a mapping of installed fieldname to
    ``(fieldtype, options)`` (``frappe.get_meta(doctype).fields``, which includes
    Table fields; ``get_valid_columns()`` does not). A plain set/list of names is
    still accepted, but then only the names are proven — the table shape check
    below needs the typed form. An absent DocType or a logical field with no
    installed candidate is a pre-fence refusal, not a guess.
    """

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    for doctype, fields in REQUIRED_FIELDS.items():
        installed = meta_fields.get(doctype)
        if not isinstance(installed, (set, frozenset, tuple, list, dict)):
            return f"missing DocType {doctype}"
        installed = set(installed)
        for logical, candidates in fields.items():
            if not any(candidate in installed for candidate in candidates):
                return f"missing field {doctype}.{logical}"
    problem = note_reference_problem(meta_fields)
    if problem is not None:
        return problem
    problem = table_shape_problem(meta_fields)
    if problem is not None:
        return problem
    for logical, candidates in VITAL_FIELDS.items():
        installed = meta_fields.get(VITAL_SIGNS_DOCTYPE)
        if not isinstance(installed, (set, frozenset, tuple, list, dict)):
            return f"missing DocType {VITAL_SIGNS_DOCTYPE}"
        if not any(candidate in set(installed) for candidate in candidates):
            return f"missing field {VITAL_SIGNS_DOCTYPE}.{logical}"
    return None


def table_shape_problem(meta_fields):
    """Refuse a required child table that is not the installed table field.

    Only the typed metadata form carries the fieldtype and options; a name-only
    set cannot prove the shape and is left to the name check in
    :func:`install_problem`.
    """

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    for doctype, fields in TABLE_FIELDS.items():
        installed = meta_fields.get(doctype)
        if not isinstance(installed, dict):
            continue
        for logical, child in fields.items():
            candidates = (REQUIRED_FIELDS.get(doctype) or {}).get(logical) or ()
            name = next(
                (candidate for candidate in candidates if candidate in installed),
                None,
            )
            if name is None:
                continue
            shape = installed.get(name)
            if not isinstance(shape, (tuple, list)) or len(shape) != 2:
                continue
            fieldtype, options = shape
            if fieldtype not in TABLE_FIELD_TYPES:
                return f"field {doctype}.{logical} is not a table field"
            if options != child:
                return (
                    f"field {doctype}.{logical} child table is not {child}"
                )
    return None


def resolve_field(meta_fields, doctype, logical, candidates=None):
    """Return the installed field name for a logical field, or ``None``."""

    if candidates is None:
        candidates = (REQUIRED_FIELDS.get(doctype) or {}).get(logical)
    if not candidates:
        return None
    installed = meta_fields.get(doctype) if isinstance(meta_fields, dict) else None
    if not isinstance(installed, (set, frozenset, tuple, list, dict)):
        return None
    installed = set(installed)
    for candidate in candidates:
        if candidate in installed:
            return candidate
    return None


def note_reference_problem(meta_fields):
    """Refuse a Clinical Note that has no readable reference pair.

    The pair must be one of the reviewed matched pairs *and*, when the typed
    metadata is available, must be shaped as installed: a ``Link`` to ``DocType``
    plus a ``Dynamic Link`` whose options is that same field. A mixed pair, a
    scalar field carrying the name, or a dynamic link pointing at a different
    field would write a link nobody can read back.
    """

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    installed = _installed_names(meta_fields, CLINICAL_NOTE_DOCTYPE)
    if installed is None:
        return f"missing DocType {CLINICAL_NOTE_DOCTYPE}"
    pair = _note_reference_pair(meta_fields)
    if pair is None:
        for doctype_field, name_field in NOTE_REFERENCE_PAIRS:
            if doctype_field in installed or name_field in installed:
                missing = (
                    doctype_field if doctype_field not in installed else name_field
                )
                return f"missing field {CLINICAL_NOTE_DOCTYPE}.{missing}"
        return f"missing field {CLINICAL_NOTE_DOCTYPE}.{NOTE_REFERENCE_PAIRS[0][0]}"
    doctype_field, name_field = pair
    raw = meta_fields.get(CLINICAL_NOTE_DOCTYPE)
    doctype_shape = _field_shape(raw, doctype_field)
    if doctype_shape is not None:
        fieldtype, options = doctype_shape
        if (
            fieldtype != NOTE_REFERENCE_DOCTYPE_FIELD_TYPE
            or options != NOTE_REFERENCE_DOCTYPE_OPTIONS
        ):
            return (
                f"field {CLINICAL_NOTE_DOCTYPE}.{doctype_field} "
                "is not a DocType link"
            )
    name_shape = _field_shape(raw, name_field)
    if name_shape is not None:
        fieldtype, options = name_shape
        if (
            fieldtype != NOTE_REFERENCE_NAME_FIELD_TYPE
            or options != doctype_field
        ):
            return (
                f"field {CLINICAL_NOTE_DOCTYPE}.{name_field} "
                f"is not a dynamic link to {doctype_field}"
            )
    return None


def _note_reference_pair(meta_fields):
    """The first reviewed pair whose two names are both installed."""

    installed = _installed_names(meta_fields, CLINICAL_NOTE_DOCTYPE)
    if installed is None:
        return None
    for doctype_field, name_field in NOTE_REFERENCE_PAIRS:
        if doctype_field in installed and name_field in installed:
            return (doctype_field, name_field)
    return None


def note_reference_fields(meta_fields):
    """Return the installed ``(reference doctype, reference name)`` pair.

    ``(None, None)`` when the pair is absent, mixed or wrongly shaped, so the
    caller writes no link and reads no note instead of guessing.
    """

    if note_reference_problem(meta_fields) is not None:
        return (None, None)
    pair = _note_reference_pair(meta_fields)
    if pair is None:
        return (None, None)
    return pair


def medication_problem(fields):
    """Refuse any medication-bearing draft before the fence.

    ``validate_save_draft`` has already normalised the section, so a mapping or a
    subclass cannot slip through: anything that is not an empty sequence is a
    medication request.
    """

    medications = fields.get("medications")
    if medications is None:
        return None
    if isinstance(medications, (list, tuple)) and not medications:
        return None
    return MEDICATION_UNSUPPORTED


def persisted_medication_problem(values):
    """Refuse a target that already holds medication rows.

    The incoming payload is not the only way medication rows exist: a native user
    can add them directly to the draft. The reviewed contract cannot rewrite such
    a row (the installed ``dosage_form`` is required and unsupplied) and must not
    delete what a human entered, so the row *count* — blank drug codes included —
    refuses the target before dispatch. An unreadable count fails closed.
    """

    count = values.get("medication_count")
    if not _non_negative_count(count):
        return "medication row count is unreadable"
    if count:
        return "target encounter has existing medication rows"
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


def create_target_problem(values):
    """Refuse a draft create before any fence claim.

    The installed ``Patient Encounter.appointment_type`` is required and is never
    a client value: it is derived from the linked booking. A missing link or a
    booking without an appointment type is refused here so the insert cannot fail
    inside the fence and latch the site.
    """

    if not values.get("appointment"):
        return "draft create has no linked appointment"
    appointment_type = values.get("appointment_type")
    if not isinstance(appointment_type, str) or not appointment_type.strip():
        return "linked appointment has no appointment type"
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
