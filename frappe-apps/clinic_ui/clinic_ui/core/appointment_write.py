"""Framework-independent codecs for local synthetic appointment writes.

Nothing here imports Frappe, resolves a session, reads a record or performs a
write. The endpoint layer supplies already-authorized session facts and the
adapter performs the native booking, reschedule or check-in; this module only
defines the durable identifiers and the acknowledgement binding that surround
that native call.

Three kinds are accepted: ``book``, ``reschedule`` and ``checkin``. They are
additive fence kinds (see :mod:`clinic_ui.core.write_fence`) and deliberately
disjoint from the patient ``create``/``update`` kinds and their codecs:

* the alias namespace is ``wsa1_<32 hex actor tag>_<16 hex key tag>_<key>``,
  distinct from the patient ``wpa1_`` wrapper. An alias is built from the
  ``book`` operation key and is stable across the later reschedule and check-in
  of the same appointment, so it never embeds a kind or a native docname;
* a receipt carries ``"codec": "appointment"`` and only appointment receipt
  keys, so :func:`clinic_ui.core.patient_write.decode_create_receipt` refuses
  it and this decoder refuses every patient receipt;
* the acknowledgement token binds site, actor, kind, operation key and the
  durable receipt under an appointment-specific HMAC prefix, so a patient
  token can never acknowledge an appointment (and the patient codec already
  refuses these kinds); every identity component is length-prefixed before
  HMAC so a ``|`` inside a site or actor cannot collide with a different
  split of the same string;
* the operation digest binds kind, key and server-normalized fields under a
  separate prefix.

A native appointment identifier is stored in the external fence receipt only.
Like the patient path, it is never placed in the alias and never returned to
the client: the client receives the workspace alias and an acknowledgement
token.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Mapping

from clinic_ui.core import patient_write, write_budgets
from clinic_ui.core.text import is_valid_text

APPOINTMENT_KINDS = frozenset(
    {
        "book",
        "reschedule",
        "checkin",
        "appointment_cancel",
        "appointment_no_show",
    }
)

ALIAS_PREFIX = "wsa1_"
ACTOR_TAG_LENGTH = 32
KEY_TAG_LENGTH = 16
ALIAS_MAX_LENGTH = 192
ALIAS_RE = re.compile(
    r"^wsa1_([0-9a-f]{32})_([0-9a-f]{16})_([A-Za-z0-9_-]{16,128})$"
)
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
# Client operation keys may never enter the fence-internal latch namespace, the
# patient alias namespace or this module's alias namespace: each is a reserved
# identifier space that a later read or recovery step must not confuse with a
# key.
RESERVED_OPERATION_KEY_PREFIXES = ("clinic-ui-", "wpa1_", "wsa1_")

MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_APPOINTMENT_ID_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024
MAX_WORKSPACE_ID_LENGTH = 192
MAX_MODIFIED_LENGTH = 64
# A cancel/no-show reason is free text, but it is copied verbatim into the
# appointment's ``notes`` field, so it stays a single bounded line.
MAX_REASON_LENGTH = 200

# The approved local test budget. It is a deployment-wide property derived from
# the durable fence receipts, not a client input and not an in-memory counter:
# two bookings, one reschedule of a booked record and one check-in of the
# dedicated same-day record. The separate same-day fixture needs its own
# booking, so the two creates cover the booking and that fixture. The two wave
# kinds read their limits from the shared registry so the numbers cannot drift.
BUDGETS = {
    "book": 2,
    "reschedule": 1,
    "checkin": 1,
    "appointment_cancel": write_budgets.budget_for("appointment_cancel"),
    "appointment_no_show": write_budgets.budget_for("appointment_no_show"),
}


RECEIPT_CODEC = "appointment"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {"codec", "version", "kind", "alias", "native_appointment_id"}
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

_FIELD_TYPES = (str, int, bool, type(None))


class AppointmentWriteError(ValueError):
    """An appointment write input or codec value is outside the accepted shape."""


# The appointment path reuses the patient write side-effect signal so a single
# exception type marks every constructed document that would trigger a
# forbidden linked-record, calendar or billing effect.
UnsafeSideEffect = patient_write.UnsafeSideEffect


class AppointmentTargetNotEligible(Exception):
    """The requested native record is not the approved writable target.

    Raised before any native mutation when the target is the read-only static
    fixture, the wrong dedicated record, or a status/date outside the approved
    transition. The deployment is not latched: nothing was dispatched.
    """


class AppointmentBudgetExhausted(Exception):
    """The durable local test budget for this appointment kind is used up."""


@dataclass(frozen=True)
class AppointmentReceipt:
    """Decoded terminal receipt for one committed appointment operation."""

    kind: str
    alias: str
    native_appointment_id: str


def require_kind(kind):
    """Return the appointment kind or raise ``AppointmentWriteError``."""
    if not isinstance(kind, str) or kind not in APPOINTMENT_KINDS:
        raise AppointmentWriteError("kind is not an appointment kind")
    return kind


def budget(kind):
    """Return the approved durable operation budget for one appointment kind."""
    return BUDGETS[require_kind(kind)]


def require_operation_key(value):
    """Return a strict, non-reserved appointment operation key."""
    if not isinstance(value, str):
        raise AppointmentWriteError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise AppointmentWriteError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise AppointmentWriteError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise AppointmentWriteError("operation_key is reserved")
    return value


def _require_secret(secret, field="secret"):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise AppointmentWriteError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise AppointmentWriteError("alias or acknowledgement context is invalid")
    return site.strip(), actor.strip()


def _framed(prefix, *values):
    """Return an unambiguous HMAC message for the given components.

    Each component is encoded as ``|<decimal byte length>:<utf-8 bytes>``.
    A plain ``|`` join would let ``site="a|b", actor="c"`` and
    ``site="a", actor="b|c"`` produce the same message; the length prefix makes
    every component self-delimiting so no re-split can collide.
    """
    message = bytearray(prefix)
    for value in values:
        data = value.encode("utf-8")
        message += b"|" + str(len(data)).encode("ascii") + b":" + data
    return bytes(message)


def actor_tag(secret, site, actor):
    """Return the stable 32-hex actor/site tag used in an appointment alias."""
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    message = _framed(b"clinic_ui_appointment_alias_actor", site, actor)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:ACTOR_TAG_LENGTH]


def _key_tag(secret, site, actor, operation_key):
    message = _framed(
        b"clinic_ui_appointment_alias_key", site, actor, operation_key
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:KEY_TAG_LENGTH]


def build_alias(secret, site, actor, operation_key):
    """Return the server-derived workspace alias for one booking operation key.

    The alias is bound to site, actor and the ``book`` operation key only, so
    it stays valid for the reschedule and check-in of the same appointment. A
    native appointment identifier is never part of it.
    """
    operation_key = require_operation_key(operation_key)
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    alias = (
        f"{ALIAS_PREFIX}{actor_tag(secret, site, actor)}_"
        f"{_key_tag(secret, site, actor, operation_key)}_{operation_key}"
    )
    if len(alias) > ALIAS_MAX_LENGTH:
        raise AppointmentWriteError("alias is too long")
    return alias


def parse_alias(secret, site, actor, alias):
    """Return the booking operation key, or ``None`` when the alias is not ours.

    A wrong actor tag, a wrong site, a malformed value, a patient alias or a
    native docname returns ``None``. A valid tag alone is never eligibility: the
    caller must still load the committed ``book`` receipt for the returned key.
    """
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


def encode_receipt(*, kind, alias, native_appointment_id):
    """Encode the minimal terminal receipt stored in the external fence.

    The encoding is appointment-specific. ``patient_write.decode_create_receipt``
    refuses it, so patient receipt enumeration cannot interpret it as a Patient.
    """
    kind = require_kind(kind)
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise AppointmentWriteError("invalid receipt alias")
    if not is_valid_text(native_appointment_id, MAX_APPOINTMENT_ID_LENGTH):
        raise AppointmentWriteError("invalid receipt native id")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "alias": alias,
            "native_appointment_id": native_appointment_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise AppointmentWriteError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return an :class:`AppointmentReceipt` or raise ``AppointmentWriteError``.

    Every patient receipt, every other codec and every receipt with missing,
    extra or non-string fields is refused.
    """
    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise AppointmentWriteError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise AppointmentWriteError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise AppointmentWriteError("invalid appointment receipt")
    version = payload["version"]
    if (
        payload["codec"] != RECEIPT_CODEC
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != RECEIPT_VERSION
    ):
        raise AppointmentWriteError("invalid appointment receipt")
    try:
        kind = require_kind(payload["kind"])
    except AppointmentWriteError as exc:
        raise AppointmentWriteError("invalid appointment receipt") from exc
    alias = payload["alias"]
    native_appointment_id = payload["native_appointment_id"]
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise AppointmentWriteError("invalid appointment receipt")
    if not is_valid_text(native_appointment_id, MAX_APPOINTMENT_ID_LENGTH):
        raise AppointmentWriteError("invalid appointment receipt")
    return AppointmentReceipt(
        kind=kind, alias=alias, native_appointment_id=native_appointment_id
    )


def ack_token(secret, site, actor, kind, operation_key, receipt):
    """Return a keyed token binding site, actor, kind, key and the receipt.

    The token is what the client returns to acknowledge a confirmed outcome. It
    never exposes the native appointment identifier or any patient data.
    """
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise AppointmentWriteError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_appointment_ack",
        site,
        actor,
        kind,
        operation_key,
        receipt,
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    """Validate the explicit appointment acknowledgement body."""
    if not isinstance(body, Mapping):
        raise AppointmentWriteError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise AppointmentWriteError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise AppointmentWriteError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise AppointmentWriteError("ack token is invalid")
    return {
        "operation_key": require_operation_key(body["operation_key"]),
        "kind": require_kind(body["kind"]),
        "ack_token": body["ack_token"],
    }


def _canonical_fields(fields):
    if not isinstance(fields, Mapping):
        raise AppointmentWriteError("fields must be a mapping")
    canonical = {}
    for key, value in fields.items():
        if not isinstance(key, str) or not key:
            raise AppointmentWriteError("field names must be non-empty strings")
        if isinstance(value, float) or not isinstance(value, _FIELD_TYPES):
            raise AppointmentWriteError("field values must be scalar")
        canonical[key] = value
    return canonical


def operation_digest(secret, *, kind, operation_key, fields):
    """Return a 64-hex keyed digest binding kind, key and normalized fields.

    ``fields`` is the server-normalized, already-whitelisted field mapping the
    endpoint built; this function only canonicalizes it. The appointment prefix
    keeps the digest distinct from the patient operation digest.
    """
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
        raise AppointmentWriteError("operation payload is not canonical") from exc
    return hmac.new(
        secret,
        b"clinic_ui_appointment_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# -- strict request bodies -------------------------------------------------
#
# Each body is exactly one operation. Server-owned values (native patient id,
# department, appointment type, status, practitioner, company) are never
# accepted from the client: the endpoint derives them from validated site
# configuration and the synthetic allowlist.

BOOK_KEYS = frozenset(
    {"operation_key", "workspace_patient_id", "appointment_date", "appointment_time"}
)
RESCHEDULE_KEYS = frozenset(
    {
        "operation_key",
        "workspace_appointment_id",
        "base_modified",
        "appointment_date",
        "appointment_time",
    }
)
CHECKIN_KEYS = frozenset(
    {"operation_key", "workspace_appointment_id", "base_modified"}
)

APPOINTMENT_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(?::[0-5]\d)?$")


def _require_body(body):
    if not isinstance(body, Mapping):
        raise AppointmentWriteError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise AppointmentWriteError("body keys must be strings")
    return body


def _require_mapping(body, keys):
    body = _require_body(body)
    if set(body) - keys:
        raise AppointmentWriteError("unknown field")
    return body


def _require_text(value, field, max_length):
    if not is_valid_text(value, max_length):
        raise AppointmentWriteError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise AppointmentWriteError(f"{field} is invalid")
    return stripped


def _appointment_date(value):
    text = _require_text(value, "appointment_date", 10)
    if len(text) != 10:
        raise AppointmentWriteError("appointment_date is invalid")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise AppointmentWriteError("appointment_date is invalid") from exc
    if parsed.isoformat() != text:
        raise AppointmentWriteError("appointment_date is invalid")
    return text


def _appointment_time(value):
    text = _require_text(value, "appointment_time", 8)
    if APPOINTMENT_TIME_RE.fullmatch(text) is None:
        raise AppointmentWriteError("appointment_time is invalid")
    # Frappe's Time column carries seconds; normalize so the request digest and
    # the native comparison cannot differ only by ``HH:MM`` vs ``HH:MM:SS``.
    if len(text) == 5:
        text = text + ":00"
    return text


def _workspace_id(value):
    text = _require_text(value, "workspace_appointment_id", MAX_WORKSPACE_ID_LENGTH)
    # The converted lane addresses a new appointment by its native docname and a
    # historical one by its sealed alias. Resolution, not this shape check,
    # decides authority: the static read allowlist and its fixtures are refused
    # in the endpoint before any native call.
    return text


def is_alias_shaped(value):
    """True when the value has the sealed appointment-alias shape.

    Used only to keep an alias-shaped value from being reinterpreted as a
    native ``Patient Appointment`` docname. Shape alone is never authority.
    """

    return isinstance(value, str) and ALIAS_RE.fullmatch(value) is not None


def _base_modified(value):
    text = _require_text(value, "base_modified", MAX_MODIFIED_LENGTH)
    if patient_write.parse_modified(text) is None:
        raise AppointmentWriteError("base_modified is invalid")
    return text


def validate_book(body):
    """Return the normalized booking payload or raise ``AppointmentWriteError``.

    ``operation_key`` is accepted for transport compatibility with the
    historical fenced lane but carries no authority and is not required: the
    converted lane uses native permissions, validation and transactions.
    """

    body = _require_mapping(body, BOOK_KEYS)
    required = frozenset({"workspace_patient_id", "appointment_date", "appointment_time"})
    if required - set(body):
        raise AppointmentWriteError("missing field")
    payload = {
        "kind": "book",
        "workspace_patient_id": _require_text(
            body["workspace_patient_id"], "workspace_patient_id", MAX_WORKSPACE_ID_LENGTH
        ),
        "fields": {
            "appointment_date": _appointment_date(body["appointment_date"]),
            "appointment_time": _appointment_time(body["appointment_time"]),
        },
    }
    if "operation_key" in body:
        payload["operation_key"] = require_operation_key(body["operation_key"])
    return payload


def validate_reschedule(body):
    """Return the normalized reschedule payload (date and time only)."""

    body = _require_mapping(body, RESCHEDULE_KEYS)
    required = frozenset(
        {
            "workspace_appointment_id",
            "base_modified",
            "appointment_date",
            "appointment_time",
        }
    )
    if required - set(body):
        raise AppointmentWriteError("missing field")
    payload = {
        "kind": "reschedule",
        "workspace_appointment_id": _workspace_id(body["workspace_appointment_id"]),
        "base_modified": _base_modified(body["base_modified"]),
        "fields": {
            "appointment_date": _appointment_date(body["appointment_date"]),
            "appointment_time": _appointment_time(body["appointment_time"]),
        },
    }
    if "operation_key" in body:
        payload["operation_key"] = require_operation_key(body["operation_key"])
    return payload


def validate_checkin(body):
    """Return the normalized same-day check-in payload (no mutable fields)."""

    body = _require_mapping(body, CHECKIN_KEYS)
    required = frozenset({"workspace_appointment_id", "base_modified"})
    if required - set(body):
        raise AppointmentWriteError("missing field")
    payload = {
        "kind": "checkin",
        "workspace_appointment_id": _workspace_id(body["workspace_appointment_id"]),
        "base_modified": _base_modified(body["base_modified"]),
        "fields": {},
    }
    if "operation_key" in body:
        payload["operation_key"] = require_operation_key(body["operation_key"])
    return payload


# -- cancel and manual no-show bodies --------------------------------------
#
# Both transitions name the target through a historical sealed alias or the
# native docname returned by booking, and carry the operator's reason. The
# patient is the patient identifier the caller names; the endpoint proves it
# resolves to the same native patient as the target before any native write.
# Status, notes preimage, native identifiers and timestamps are server-owned and
# refused from the client.

CANCEL_KEYS = frozenset({"patient", "appointment_id", "operation_key", "reason"})


def _reason(value):
    text = _require_text(value, "reason", MAX_REASON_LENGTH)
    if len(text) > MAX_REASON_LENGTH:
        raise AppointmentWriteError("reason is invalid")
    return text


def _cancel_payload(body, kind):
    body = _require_mapping(body, CANCEL_KEYS)
    required = frozenset({"patient", "appointment_id", "reason"})
    if required - set(body):
        raise AppointmentWriteError("missing field")
    payload = {
        "kind": kind,
        "workspace_patient_id": _require_text(
            body["patient"], "patient", MAX_WORKSPACE_ID_LENGTH
        ),
        "workspace_appointment_id": _workspace_id(body["appointment_id"]),
        "reason": _reason(body["reason"]),
    }
    if "operation_key" in body:
        payload["operation_key"] = require_operation_key(body["operation_key"])
    return payload


def validate_cancel(body):
    """Return the normalized cancel payload or raise ``AppointmentWriteError``."""

    return _cancel_payload(body, "appointment_cancel")


def validate_no_show(body):
    """Return the normalized manual no-show payload."""

    return _cancel_payload(body, "appointment_no_show")

