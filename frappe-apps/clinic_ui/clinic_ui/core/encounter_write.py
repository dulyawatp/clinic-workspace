"""Framework-independent codecs for the Patient Encounter write family.

Nothing here imports Frappe, resolves a session, reads a record or performs a
write. It defines the durable identifiers, the strict request bodies and the
acknowledgement binding that surround the native encounter, Clinical Note,
Vital Signs and Service Request operations.

Three kinds share one encounter alias namespace (``wse1_``), distinct from the
patient ``wpa1_`` and appointment ``wsa1_`` aliases:

* ``encounter_draft`` creates (or updates) the draft encounter, its child rows,
  the separate Clinical Note documents and the Vital Signs record,
* ``encounter_complete`` submits the draft and re-reads the native cascade,
* ``encounter_amend`` adds a new Clinical Note addendum to the finalized
  encounter; it never rewrites the original.

The alias is derived from the creating draft key and stays valid for the later
complete and amend of the same encounter, so it never embeds a kind or a native
docname. Receipts carry ``"codec": "encounter"`` and are interpreted only by
this module.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Mapping

from clinic_ui.core import encounter_contract, patient_write, write_budgets
from clinic_ui.core.text import is_valid_text

ALIAS_PREFIX = "wse1_"
ACTOR_TAG_LENGTH = 32
KEY_TAG_LENGTH = 16
ALIAS_MAX_LENGTH = 192
ALIAS_RE = re.compile(
    r"^wse1_([0-9a-f]{32})_([0-9a-f]{16})_([A-Za-z0-9_-]{16,128})$"
)
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RESERVED_OPERATION_KEY_PREFIXES = ("clinic-ui-", "wpa1_", "wsa1_", "wse1_")

MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_ENCOUNTER_ID_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024
MAX_WORKSPACE_ID_LENGTH = 192
MAX_MODIFIED_LENGTH = 64

BUDGETS = {
    encounter_contract.ENCOUNTER_DRAFT_KIND: write_budgets.budget_for(
        encounter_contract.ENCOUNTER_DRAFT_KIND
    ),
    encounter_contract.ENCOUNTER_COMPLETE_KIND: write_budgets.budget_for(
        encounter_contract.ENCOUNTER_COMPLETE_KIND
    ),
    encounter_contract.ENCOUNTER_AMEND_KIND: write_budgets.budget_for(
        encounter_contract.ENCOUNTER_AMEND_KIND
    ),
}

RECEIPT_CODEC = "encounter"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {"codec", "version", "kind", "alias", "native_encounter_id"}
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

_FIELD_TYPES = (str, int, bool, type(None))


class EncounterWriteError(ValueError):
    """An encounter write input or codec value is outside the accepted shape."""


class EncounterTargetNotEligible(Exception):
    """The requested native encounter is not the approved writable target."""


class EncounterBudgetExhausted(Exception):
    """The durable local test budget for this encounter kind is used up."""


@dataclass(frozen=True)
class EncounterReceipt:
    """Decoded terminal receipt for one committed encounter operation."""

    kind: str
    alias: str
    native_encounter_id: str


def require_kind(kind):
    if not isinstance(kind, str) or kind not in encounter_contract.ENCOUNTER_KINDS:
        raise EncounterWriteError("kind is not an encounter kind")
    return kind


def budget(kind):
    return BUDGETS[require_kind(kind)]


def require_operation_key(value):
    if not isinstance(value, str):
        raise EncounterWriteError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise EncounterWriteError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise EncounterWriteError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise EncounterWriteError("operation_key is reserved")
    return value


def _require_secret(secret, field="secret"):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise EncounterWriteError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise EncounterWriteError("alias or acknowledgement context is invalid")
    return site.strip(), actor.strip()


def _framed(prefix, *values):
    message = bytearray(prefix)
    for value in values:
        data = value.encode("utf-8")
        message += b"|" + str(len(data)).encode("ascii") + b":" + data
    return bytes(message)


def actor_tag(secret, site, actor):
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    message = _framed(b"clinic_ui_encounter_alias_actor", site, actor)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:ACTOR_TAG_LENGTH]


def _key_tag(secret, site, actor, operation_key):
    message = _framed(b"clinic_ui_encounter_alias_key", site, actor, operation_key)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:KEY_TAG_LENGTH]


def build_alias(secret, site, actor, operation_key):
    """Return the server-derived encounter alias for one draft operation key."""

    operation_key = require_operation_key(operation_key)
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    alias = (
        f"{ALIAS_PREFIX}{actor_tag(secret, site, actor)}_"
        f"{_key_tag(secret, site, actor, operation_key)}_{operation_key}"
    )
    if len(alias) > ALIAS_MAX_LENGTH:
        raise EncounterWriteError("alias is too long")
    return alias


def parse_alias(secret, site, actor, alias):
    """Return the creating operation key, or ``None`` when the alias is not ours."""

    if not isinstance(alias, str) or len(alias) > ALIAS_MAX_LENGTH:
        return None
    match = ALIAS_RE.fullmatch(alias)
    if match is None:
        return None
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    operation_key = match.group(3)
    if not hmac.compare_digest(actor_tag(secret, site, actor), match.group(1)):
        return None
    if not hmac.compare_digest(
        _key_tag(secret, site, actor, operation_key), match.group(2)
    ):
        return None
    return operation_key


def encode_receipt(*, kind, alias, native_encounter_id):
    kind = require_kind(kind)
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise EncounterWriteError("invalid receipt alias")
    if not is_valid_text(native_encounter_id, MAX_ENCOUNTER_ID_LENGTH):
        raise EncounterWriteError("invalid receipt native id")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "alias": alias,
            "native_encounter_id": native_encounter_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise EncounterWriteError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return an :class:`EncounterReceipt` or raise ``EncounterWriteError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise EncounterWriteError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise EncounterWriteError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise EncounterWriteError("invalid encounter receipt")
    version = payload["version"]
    if (
        payload["codec"] != RECEIPT_CODEC
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != RECEIPT_VERSION
    ):
        raise EncounterWriteError("invalid encounter receipt")
    try:
        kind = require_kind(payload["kind"])
    except EncounterWriteError as exc:
        raise EncounterWriteError("invalid encounter receipt") from exc
    alias = payload["alias"]
    native_encounter_id = payload["native_encounter_id"]
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise EncounterWriteError("invalid encounter receipt")
    if not is_valid_text(native_encounter_id, MAX_ENCOUNTER_ID_LENGTH):
        raise EncounterWriteError("invalid encounter receipt")
    return EncounterReceipt(
        kind=kind, alias=alias, native_encounter_id=native_encounter_id
    )


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise EncounterWriteError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_encounter_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, Mapping):
        raise EncounterWriteError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise EncounterWriteError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise EncounterWriteError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise EncounterWriteError("ack token is invalid")
    return {
        "operation_key": require_operation_key(body["operation_key"]),
        "kind": require_kind(body["kind"]),
        "ack_token": body["ack_token"],
    }


def _canonical_fields(fields):
    """Return a canonical, JSON-safe copy of the server-normalized fields.

    The encounter payload carries child-row lists and a note mapping, so unlike
    the appointment codec this accepts nested lists/dicts. Only JSON scalars are
    allowed: floats, sets, bytes and arbitrary objects are refused.
    """

    if not isinstance(fields, Mapping):
        raise EncounterWriteError("fields must be a mapping")

    def canonical(value, depth):
        if depth > 4:
            raise EncounterWriteError("field nesting is too deep")
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, (list, tuple)):
            return [canonical(item, depth + 1) for item in value]
        if isinstance(value, Mapping):
            copy = {}
            for key, item in value.items():
                if not isinstance(key, str) or not key:
                    raise EncounterWriteError(
                        "field names must be non-empty strings"
                    )
                copy[key] = canonical(item, depth + 1)
            return copy
        raise EncounterWriteError("field values must be JSON scalars")

    result = {}
    for key, value in fields.items():
        if not isinstance(key, str) or not key:
            raise EncounterWriteError("field names must be non-empty strings")
        result[key] = canonical(value, 0)
    return result


def operation_digest(secret, *, kind, operation_key, fields):
    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    try:
        canonical = json.dumps(
            {
                "fields": _canonical_fields(fields),
                "kind": kind,
                "operation_key": operation_key,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EncounterWriteError("operation payload is not canonical") from exc
    return hmac.new(
        secret,
        b"clinic_ui_encounter_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# -- strict request bodies -------------------------------------------------
#
# The client never sends native identifiers, docstatus, status, the native
# cascade or a clinical-note name. Logical row fields are validated here; the
# adapter maps them onto the installed field names resolved by the preflight.

DRAFT_KEYS = frozenset(
    {
        "patient",
        "operation_key",
        "encounter",
        "base_modified",
        "appointment",
        "complaint",
        "diagnoses",
        "medications",
        "lab_tests",
        "procedures",
        "vitals",
        "note",
        "allergy_reviewed",
    }
)
COMPLETE_KEYS = frozenset({"encounter", "operation_key", "allergy_reviewed"})
AMEND_KEYS = frozenset({"encounter", "operation_key", "addendum"})

DIAGNOSIS_KEYS = frozenset({"diagnosis"})
MEDICATION_KEYS = frozenset({"drug_code", "dosage", "period"})
LAB_KEYS = frozenset({"lab_test_code"})
PROCEDURE_KEYS = frozenset({"procedure"})
NOTE_KEYS = frozenset({"assessment", "plan", "rationale", "followup"})
VITAL_KEYS = frozenset(encounter_contract.VITAL_FIELDS)


def _require_body(body):
    if not isinstance(body, Mapping):
        raise EncounterWriteError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise EncounterWriteError("body keys must be strings")
    return body


def _require_mapping(body, keys):
    body = _require_body(body)
    if set(body) - keys:
        raise EncounterWriteError("unknown field")
    return body


def _require_text(value, field, max_length):
    if not is_valid_text(value, max_length):
        raise EncounterWriteError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise EncounterWriteError(f"{field} is invalid")
    return stripped


def _optional_text(value, field, max_length):
    if value is None:
        return None
    if not isinstance(value, str) or len(value) > max_length:
        raise EncounterWriteError(f"{field} is invalid")
    return value


def _workspace_patient(value):
    return _require_text(value, "patient", MAX_WORKSPACE_ID_LENGTH)


def _encounter_alias(value):
    text = _require_text(value, "encounter", ALIAS_MAX_LENGTH)
    if ALIAS_RE.fullmatch(text) is None:
        raise EncounterWriteError("encounter is invalid")
    return text


def _base_modified(value):
    text = _require_text(value, "base_modified", MAX_MODIFIED_LENGTH)
    if patient_write.parse_modified(text) is None:
        raise EncounterWriteError("base_modified is invalid")
    return text


def _rows(value, field, keys, maximum=encounter_contract.MAX_ROWS):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise EncounterWriteError(f"{field} is invalid")
    rows = []
    for row in value:
        if not isinstance(row, Mapping) or not row:
            raise EncounterWriteError(f"{field} is invalid")
        if set(row) - keys:
            raise EncounterWriteError(f"{field} is invalid")
        rows.append(dict(row))
    return rows


def _diagnoses(value):
    rows = []
    for row in _rows(value, "diagnoses", DIAGNOSIS_KEYS):
        rows.append(
            {"diagnosis": _require_text(
                row.get("diagnosis"),
                "diagnosis",
                encounter_contract.MAX_CODE_LENGTH,
            )}
        )
    return rows


def _medications(value):
    rows = []
    for row in _rows(value, "medications", MEDICATION_KEYS):
        # ``drug_code`` is required for every medication row. The adapter maps
        # the logical name to the installed Drug Prescription field set.
        rows.append(
            {
                "drug_code": _require_text(
                    row.get("drug_code"),
                    "drug_code",
                    encounter_contract.MAX_CODE_LENGTH,
                ),
                "dosage": _optional_text(
                    row.get("dosage"),
                    "dosage",
                    encounter_contract.MAX_CODE_LENGTH,
                ),
                "period": _optional_text(
                    row.get("period"),
                    "period",
                    encounter_contract.MAX_CODE_LENGTH,
                ),
            }
        )
    return rows


def _lab_tests(value):
    return [
        {
            "lab_test_code": _require_text(
                row.get("lab_test_code"),
                "lab_test_code",
                encounter_contract.MAX_CODE_LENGTH,
            )
        }
        for row in _rows(value, "lab_tests", LAB_KEYS)
    ]


def _procedures(value):
    return [
        {
            "procedure": _require_text(
                row.get("procedure"),
                "procedure",
                encounter_contract.MAX_CODE_LENGTH,
            )
        }
        for row in _rows(value, "procedures", PROCEDURE_KEYS)
    ]


def _vitals(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EncounterWriteError("vitals is invalid")
    if set(value) - VITAL_KEYS:
        raise EncounterWriteError("vitals is invalid")
    vitals = {}
    for key, raw in value.items():
        if raw is None:
            continue
        text = str(raw) if isinstance(raw, int) and not isinstance(raw, bool) else raw
        if not encounter_contract.valid_numeric(text):
            raise EncounterWriteError("vitals is invalid")
        vitals[key] = text
    return vitals


def _note(value):
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise EncounterWriteError("note is invalid")
    if set(value) - NOTE_KEYS:
        raise EncounterWriteError("note is invalid")
    note = {}
    for key, raw in value.items():
        text = _optional_text(raw, key, encounter_contract.MAX_NOTE_LENGTH)
        if text is not None and text.strip():
            note[key] = text.strip()
    return note


def _allergy_reviewed(value):
    if value is None:
        return False
    if not isinstance(value, bool):
        raise EncounterWriteError("allergy_reviewed is invalid")
    return value


def validate_save_draft(body):
    """Return the normalized draft payload or raise ``EncounterWriteError``."""

    body = _require_mapping(body, DRAFT_KEYS)
    for required in ("patient", "operation_key"):
        if required not in body:
            raise EncounterWriteError(f"missing field {required}")
    encounter = body.get("encounter")
    base_modified = body.get("base_modified")
    if encounter is None:
        if base_modified is not None:
            raise EncounterWriteError("base_modified requires an encounter")
        if body.get("appointment") is None:
            raise EncounterWriteError("a new draft requires an appointment")
    else:
        encounter = _encounter_alias(encounter)
        if base_modified is None:
            raise EncounterWriteError("an update requires base_modified")
        base_modified = _base_modified(base_modified)

    complaint = _optional_text(
        body.get("complaint"), "complaint", encounter_contract.MAX_TEXT_LENGTH
    )
    appointment = body.get("appointment")
    if appointment is not None:
        appointment = _require_text(appointment, "appointment", MAX_WORKSPACE_ID_LENGTH)

    payload = {
        "kind": encounter_contract.ENCOUNTER_DRAFT_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "workspace_patient_id": _workspace_patient(body["patient"]),
        "encounter": encounter,
        "base_modified": base_modified,
        "appointment": appointment,
        "allergy_reviewed": _allergy_reviewed(body.get("allergy_reviewed")),
        "fields": {
            "complaint": complaint,
            "diagnoses": _diagnoses(body.get("diagnoses")),
            "medications": _medications(body.get("medications")),
            "lab_tests": _lab_tests(body.get("lab_tests")),
            "procedures": _procedures(body.get("procedures")),
            "vitals": _vitals(body.get("vitals")),
            "note": _note(body.get("note")),
        },
    }
    return payload


def validate_complete(body):
    """Return the normalized completion payload or raise ``EncounterWriteError``."""

    body = _require_mapping(body, COMPLETE_KEYS)
    for required in ("encounter", "operation_key"):
        if required not in body:
            raise EncounterWriteError(f"missing field {required}")
    return {
        "kind": encounter_contract.ENCOUNTER_COMPLETE_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "encounter": _encounter_alias(body["encounter"]),
        "allergy_reviewed": _allergy_reviewed(body.get("allergy_reviewed")),
    }


def validate_amend(body):
    """Return the normalized amendment payload or raise ``EncounterWriteError``."""

    body = _require_mapping(body, AMEND_KEYS)
    for required in ("encounter", "operation_key", "addendum"):
        if required not in body:
            raise EncounterWriteError(f"missing field {required}")
    addendum = _require_text(
        body["addendum"], "addendum", encounter_contract.MAX_ADDENDUM_LENGTH
    )
    return {
        "kind": encounter_contract.ENCOUNTER_AMEND_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "encounter": _encounter_alias(body["encounter"]),
        "addendum": addendum,
    }
