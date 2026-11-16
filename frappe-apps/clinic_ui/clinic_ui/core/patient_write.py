"""Framework-independent validation for the local synthetic Patient write path.

Nothing here imports Frappe, resolves a session, reads a record or performs a
write. The endpoint layer supplies already-authorized session facts and the
adapter performs the native ``Patient`` insert/save.

The accepted surface is deliberately tiny: ``first_name``, ``last_name``,
``sex``, ``dob`` plus a transport operation key, and for update only, the
workspace alias and ``base_modified``. Every other key is refused, including
``status``, ``patient_name``, ``customer``, ``user_id``, ``invite_user``,
``email``, ``mobile``, ``phone`` and ``naming_series``.

The operation digest binds a key to the normalized payload. It is a keyed
HMAC over the canonical mapping so the digest recorded outside the site is not
a plain demographic hash.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from datetime import date, datetime, timezone
from typing import Mapping

from clinic_ui.core.text import is_valid_text

MAX_NAME_LENGTH = 140
MAX_SEX_LENGTH = 140
MAX_ALIAS_LENGTH = 256
MAX_PATIENT_ID_LENGTH = 140
MAX_MODIFIED_LENGTH = 64
MAX_OPERATION_KEY_LENGTH = 128
MAX_RECEIPT_LENGTH = 1024

MIN_OPERATION_KEY_LENGTH = 16
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")

CREATE_KEYS = frozenset(
    {"operation_key", "first_name", "last_name", "sex", "dob"}
)
UPDATE_KEYS = frozenset(
    {
        "operation_key",
        "workspace_patient_id",
        "base_modified",
        "first_name",
        "last_name",
        "sex",
        "dob",
    }
)
EDITABLE_FIELDS = frozenset({"first_name", "last_name", "sex", "dob"})

RECEIPT_KEYS = frozenset(
    {"version", "kind", "alias", "native_patient_id"}
)
RECEIPT_VERSION = 1
RECEIPT_KINDS = frozenset({"create", "update"})

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")



class WriteInputError(ValueError):
    """The request body is outside the strict write surface."""


class WriteLocked(Exception):
    """The operation key is dispatched, unknown or already committed."""


class WriteConflict(Exception):
    """The operation key is bound to a different payload."""


class StoreUnavailable(Exception):
    """The persistent operation fence cannot be used; writes fail closed."""


class StaleVersion(Exception):
    """The supplied ``base_modified`` no longer matches the native record."""


class WriteUncertain(Exception):
    """A post-dispatch outcome could not be established; keep the key locked."""


class UnsafeSideEffect(Exception):
    """The constructed native Patient would trigger a forbidden side effect.

    Raised before any insert. The caller fails closed; no native mutation and no
    operation fence dispatch may occur.
    """


def _require_mapping(body):
    if not isinstance(body, Mapping):
        raise WriteInputError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise WriteInputError("body keys must be strings")
    return body


def _require_text(value, *, field, max_length):
    if not is_valid_text(value, max_length):
        raise WriteInputError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped:
        raise WriteInputError(f"{field} is invalid")
    return stripped


def _optional_text(value, *, field, max_length):
    if value is None:
        return None
    if not isinstance(value, str):
        raise WriteInputError(f"{field} is invalid")
    if not value.strip():
        return None
    return _require_text(value, field=field, max_length=max_length)


def _operation_key(value):
    if not isinstance(value, str):
        raise WriteInputError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise WriteInputError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise WriteInputError("operation_key is invalid")
    # Reserved namespace for fence-internal latch keys. A client key in this
    # namespace could collide with the site-wide manual-investigation latch.
    if value.startswith("clinic-ui-"):
        raise WriteInputError("operation_key is reserved")
    return value


def _date_of_birth(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise WriteInputError("dob is invalid")
    text = value.strip()
    if not text:
        return None
    if text != value or len(text) != 10:
        raise WriteInputError("dob is invalid")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise WriteInputError("dob is invalid") from exc
    if parsed.isoformat() != text:
        raise WriteInputError("dob is invalid")
    if parsed > date.today():
        raise WriteInputError("dob is in the future")
    return text


def parse_modified(value):
    """Parse a Frappe ``modified`` value in ISO ``T`` or SQL space form.

    Returns a naive UTC ``datetime`` or ``None``. Both the view DTO and the
    native row are normalized through this function before comparison.
    """

    if isinstance(value, datetime):
        parsed = value
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > MAX_MODIFIED_LENGTH:
        return None
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def modified_matches(left, right):
    """True only when both values parse to the same instant."""

    parsed_left = parse_modified(left)
    parsed_right = parse_modified(right)
    if parsed_left is None or parsed_right is None:
        return False
    return parsed_left == parsed_right


def _base_modified(value):
    if not isinstance(value, str):
        raise WriteInputError("base_modified is invalid")
    parsed = parse_modified(value)
    if parsed is None:
        raise WriteInputError("base_modified is invalid")
    return value.strip()


def validate_create(body):
    """Return the normalized create payload or raise ``WriteInputError``."""

    body = _require_mapping(body)
    unknown = set(body) - CREATE_KEYS
    if unknown:
        raise WriteInputError("unknown create field")
    required = frozenset({"operation_key", "first_name", "sex"})
    if required - set(body):
        raise WriteInputError("missing create field")

    fields = {
        "first_name": _require_text(
            body["first_name"], field="first_name", max_length=MAX_NAME_LENGTH
        ),
        "last_name": _optional_text(
            body.get("last_name"), field="last_name", max_length=MAX_NAME_LENGTH
        ),
        "sex": _require_text(body["sex"], field="sex", max_length=MAX_SEX_LENGTH),
        "dob": _date_of_birth(body.get("dob")),
    }
    return {
        "kind": "create",
        "operation_key": _operation_key(body["operation_key"]),
        "fields": fields,
    }


def validate_update(body):
    """Return the normalized update payload or raise ``WriteInputError``."""

    body = _require_mapping(body)
    unknown = set(body) - UPDATE_KEYS
    if unknown:
        raise WriteInputError("unknown update field")
    for required in ("operation_key", "workspace_patient_id", "base_modified"):
        if required not in body:
            raise WriteInputError(f"missing update field {required}")

    supplied = {field for field in EDITABLE_FIELDS if field in body}
    if not supplied:
        raise WriteInputError("no editable field supplied")

    fields = {}
    if "first_name" in supplied:
        fields["first_name"] = _require_text(
            body["first_name"], field="first_name", max_length=MAX_NAME_LENGTH
        )
    if "last_name" in supplied:
        fields["last_name"] = _optional_text(
            body["last_name"], field="last_name", max_length=MAX_NAME_LENGTH
        )
    if "sex" in supplied:
        fields["sex"] = _require_text(
            body["sex"], field="sex", max_length=MAX_SEX_LENGTH
        )
    if "dob" in supplied:
        fields["dob"] = _date_of_birth(body["dob"])

    return {
        "kind": "update",
        "operation_key": _operation_key(body["operation_key"]),
        "workspace_patient_id": _require_text(
            body["workspace_patient_id"],
            field="workspace_patient_id",
            max_length=MAX_ALIAS_LENGTH,
        ),
        "base_modified": _base_modified(body["base_modified"]),
        "fields": fields,
    }


def canonical_payload(payload):
    """Return the canonical mapping used for the keyed operation digest."""

    if not isinstance(payload, Mapping):
        raise WriteInputError("payload must be a mapping")
    if payload.get("kind") == "create":
        keys = ("kind", "operation_key", "fields")
    elif payload.get("kind") == "update":
        keys = (
            "kind",
            "operation_key",
            "workspace_patient_id",
            "base_modified",
            "fields",
        )
    else:
        raise WriteInputError("unknown operation kind")
    return {key: payload[key] for key in keys}


def operation_digest(secret, payload):
    """Return a 64-character keyed digest of the canonical operation payload."""

    if not isinstance(secret, bytes) or len(secret) < 32:
        raise WriteInputError("operation secret is invalid")
    try:
        canonical = json.dumps(
            canonical_payload(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise WriteInputError("operation payload is not canonical") from exc
    return hmac.new(
        secret,
        b"clinic_ui_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def validate_ack(body):
    """Validate the explicit success-acknowledgement body."""

    body = _require_mapping(body)
    if set(body) != ACK_KEYS:
        raise WriteInputError("ack body is invalid")
    kind = body["kind"]
    if kind not in RECEIPT_KINDS:
        raise WriteInputError("ack kind is invalid")
    token = body["ack_token"]
    if not isinstance(token, str) or ACK_TOKEN_RE.fullmatch(token) is None:
        raise WriteInputError("ack token is invalid")
    return {
        "operation_key": _operation_key(body["operation_key"]),
        "kind": kind,
        "ack_token": token,
    }


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def ack_token(secret, site, actor, kind, operation_key, receipt):
    """Return a keyed token binding actor, kind, key and the durable receipt.

    The token is what the client returns to acknowledge confirmed success. It
    never exposes the native receipt or patient name.
    """

    if not isinstance(secret, bytes) or len(secret) < 32:
        raise WriteInputError("ack secret is invalid")
    if not is_valid_text(site, 140) or not is_valid_text(actor, 140):
        raise WriteInputError("ack context is invalid")
    if kind not in RECEIPT_KINDS:
        raise WriteInputError("ack kind is invalid")
    operation_key = _operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise WriteInputError("ack receipt is invalid")
    message = (
        b"clinic_ui_ack|"
        + site.strip().encode("utf-8")
        + b"|"
        + actor.strip().encode("utf-8")
        + b"|"
        + kind.encode("utf-8")
        + b"|"
        + operation_key.encode("utf-8")
        + b"|"
        + receipt.encode("utf-8")
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def encode_receipt(*, kind, alias, native_patient_id):
    """Encode the minimal terminal receipt stored in the external fence."""

    if kind not in RECEIPT_KINDS:
        raise WriteInputError("invalid receipt kind")
    if not is_valid_text(alias, MAX_ALIAS_LENGTH):
        raise WriteInputError("invalid receipt alias")
    if not is_valid_text(native_patient_id, MAX_PATIENT_ID_LENGTH):
        raise WriteInputError("invalid receipt native id")
    receipt = json.dumps(
        {
            "version": RECEIPT_VERSION,
            "kind": kind,
            "alias": alias,
            "native_patient_id": native_patient_id,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise WriteInputError("receipt is too long")
    return receipt


def decode_create_receipt(receipt):
    """Return ``(alias, native_patient_id)`` from a committed create receipt."""

    if not isinstance(receipt, str) or not receipt or len(receipt) > MAX_RECEIPT_LENGTH:
        raise WriteInputError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise WriteInputError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise WriteInputError("invalid receipt")
    if payload["version"] != RECEIPT_VERSION or payload["kind"] != "create":
        raise WriteInputError("invalid receipt")
    alias = payload["alias"]
    native_patient_id = payload["native_patient_id"]
    if not is_valid_text(alias, MAX_ALIAS_LENGTH):
        raise WriteInputError("invalid receipt")
    if not is_valid_text(native_patient_id, MAX_PATIENT_ID_LENGTH):
        raise WriteInputError("invalid receipt")
    return alias, native_patient_id
