"""Framework-independent codecs for the Clinical Procedure write family.

Nothing here imports Frappe, resolves a session, reads a record or performs a
write. It defines the sealed identifiers, the strict request bodies and the
receipt/acknowledgement codecs around the two native operations: creating one
Clinical Procedure from a submitted Service Request, and driving the installed
``complete_procedure`` document method. The converted development lane uses the
strict bodies and the historical read-only codecs; it issues no acknowledgement.

The kinds are ``procedure_create`` and ``procedure_complete`` and the alias
prefix is ``wsp1_``, distinct from the patient ``wpa1_``, appointment ``wsa1_``
and encounter ``wse1_`` namespaces. The converted development lane no longer
writes these identifiers: creation returns the native docname of the row it
created, and completion resolves the allowlist, the native docname or a
historical sealed alias through its committed creation receipt. Alias and
receipt codecs remain for that read-only historical resolution and for the
untouched ``wsp1_`` links that already exist.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Mapping

from clinic_ui.core import procedure_contract, write_budgets
from clinic_ui.core.text import is_valid_text

ALIAS_PREFIX = "wsp1_"
ACTOR_TAG_LENGTH = 32
KEY_TAG_LENGTH = 16
ALIAS_MAX_LENGTH = 192
ALIAS_RE = re.compile(
    r"^wsp1_([0-9a-f]{32})_([0-9a-f]{16})_([A-Za-z0-9_-]{16,128})$"
)
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RESERVED_OPERATION_KEY_PREFIXES = ("clinic-ui-", "wpa1_", "wsa1_", "wse1_", "wsp1_")

MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_PROCEDURE_ID_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024
MAX_WORKSPACE_ID_LENGTH = 192
MAX_MODIFIED_LENGTH = 64

BUDGETS = {
    kind: write_budgets.budget_for(kind)
    for kind in procedure_contract.PROCEDURE_KINDS
}

RECEIPT_CODEC = "procedure"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {
        "codec",
        "version",
        "kind",
        "alias",
        "native_procedure_id",
        "stock_entry",
        "service_request",
    }
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")


class ProcedureWriteError(ValueError):
    """A procedure write input or codec value is outside the accepted shape."""


class ProcedureTargetNotEligible(Exception):
    """The requested native procedure is not the approved writable target."""


class ProcedureBudgetExhausted(Exception):
    """The durable local test budget for this procedure kind is used up."""


@dataclass(frozen=True)
class ProcedureReceipt:
    """Decoded terminal receipt for one committed procedure write.

    A completion receipt carries the driven Stock Entry; a creation receipt
    carries the Service Request it was created from and no Stock Entry.
    """

    kind: str
    alias: str
    native_procedure_id: str
    stock_entry: str
    service_request: str


def require_kind(kind):
    if not isinstance(kind, str) or kind not in procedure_contract.PROCEDURE_KINDS:
        raise ProcedureWriteError("kind is not a procedure kind")
    return kind


def budget(kind):
    return BUDGETS[require_kind(kind)]


def require_operation_key(value):
    if not isinstance(value, str):
        raise ProcedureWriteError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise ProcedureWriteError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise ProcedureWriteError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise ProcedureWriteError("operation_key is reserved")
    return value


def _require_secret(secret, field="secret"):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise ProcedureWriteError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise ProcedureWriteError("alias or acknowledgement context is invalid")
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
    message = _framed(b"clinic_ui_procedure_alias_actor", site, actor)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:ACTOR_TAG_LENGTH]


def _key_tag(secret, site, actor, operation_key):
    message = _framed(b"clinic_ui_procedure_alias_key", site, actor, operation_key)
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:KEY_TAG_LENGTH]


def build_alias(secret, site, actor, operation_key):
    """Return the server-derived procedure alias for one operation key."""

    operation_key = require_operation_key(operation_key)
    _require_secret(secret)
    site, actor = _require_context(site, actor)
    alias = (
        f"{ALIAS_PREFIX}{actor_tag(secret, site, actor)}_"
        f"{_key_tag(secret, site, actor, operation_key)}_{operation_key}"
    )
    if len(alias) > ALIAS_MAX_LENGTH:
        raise ProcedureWriteError("alias is too long")
    return alias


def claims_alias_namespace(value):
    """True when the value claims the sealed ``wsp1_`` namespace, valid or not.

    A malformed alias-shaped value is never reinterpreted as a native docname:
    it resolves only through the alias path, so a truncated or corrupted alias
    cannot address an arbitrary native record.
    """

    return isinstance(value, str) and value.startswith(ALIAS_PREFIX)


def parse_alias(secret, site, actor, alias):
    """Return the operation key, or ``None`` when the alias is not ours."""

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


def encode_receipt(
    *, kind, alias, native_procedure_id, stock_entry=None, service_request=None
):
    kind = require_kind(kind)
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise ProcedureWriteError("invalid receipt alias")
    if not is_valid_text(native_procedure_id, MAX_PROCEDURE_ID_LENGTH):
        raise ProcedureWriteError("invalid receipt native id")
    entry = "" if stock_entry is None else stock_entry
    if not isinstance(entry, str) or (
        entry and not is_valid_text(entry, MAX_PROCEDURE_ID_LENGTH)
    ):
        raise ProcedureWriteError("invalid receipt stock entry")
    request = "" if service_request is None else service_request
    if not isinstance(request, str) or (
        request and not is_valid_text(request, MAX_PROCEDURE_ID_LENGTH)
    ):
        raise ProcedureWriteError("invalid receipt service request")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "alias": alias,
            "native_procedure_id": native_procedure_id,
            "stock_entry": entry,
            "service_request": request,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise ProcedureWriteError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return a :class:`ProcedureReceipt` or raise ``ProcedureWriteError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise ProcedureWriteError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise ProcedureWriteError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise ProcedureWriteError("invalid procedure receipt")
    version = payload["version"]
    if (
        payload["codec"] != RECEIPT_CODEC
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != RECEIPT_VERSION
    ):
        raise ProcedureWriteError("invalid procedure receipt")
    try:
        kind = require_kind(payload["kind"])
    except ProcedureWriteError as exc:
        raise ProcedureWriteError("invalid procedure receipt") from exc
    alias = payload["alias"]
    native_procedure_id = payload["native_procedure_id"]
    stock_entry = payload["stock_entry"]
    service_request = payload["service_request"]
    if not is_valid_text(alias, ALIAS_MAX_LENGTH):
        raise ProcedureWriteError("invalid procedure receipt")
    if not is_valid_text(native_procedure_id, MAX_PROCEDURE_ID_LENGTH):
        raise ProcedureWriteError("invalid procedure receipt")
    if not isinstance(stock_entry, str) or (
        stock_entry and not is_valid_text(stock_entry, MAX_PROCEDURE_ID_LENGTH)
    ):
        raise ProcedureWriteError("invalid procedure receipt")
    if not isinstance(service_request, str) or (
        service_request
        and not is_valid_text(service_request, MAX_PROCEDURE_ID_LENGTH)
    ):
        raise ProcedureWriteError("invalid procedure receipt")
    return ProcedureReceipt(
        kind=kind,
        alias=alias,
        native_procedure_id=native_procedure_id,
        stock_entry=stock_entry,
        service_request=service_request,
    )


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise ProcedureWriteError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_procedure_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, Mapping):
        raise ProcedureWriteError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise ProcedureWriteError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise ProcedureWriteError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise ProcedureWriteError("ack token is invalid")
    return {
        "operation_key": require_operation_key(body["operation_key"]),
        "kind": require_kind(body["kind"]),
        "ack_token": body["ack_token"],
    }


def operation_digest(secret, *, kind, operation_key, fields):
    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not isinstance(fields, Mapping):
        raise ProcedureWriteError("fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            raise ProcedureWriteError("field names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise ProcedureWriteError("field values must be text")
    canonical = json.dumps(
        {
            "fields": {name: fields[name] for name in sorted(fields)},
            "kind": kind,
            "operation_key": operation_key,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hmac.new(
        secret,
        b"clinic_ui_procedure_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


# -- strict request body ---------------------------------------------------
#
# The client never sends a native identifier, a docstatus, a status, a price
# list, a warehouse or an item row: completion consumes what the server-side
# procedure already holds.

COMPLETE_KEYS = frozenset({"procedure", "operation_key"})
# The creation body carries the selected Service Request and the operation key.
# Nothing else: the procedure fields, the template, the patient and the
# practitioner all come from the server-read request.
CREATE_KEYS = frozenset({"service_request", "operation_key"})
MAX_SERVICE_REQUEST_LENGTH = 140


def _require_body(body, allowed=COMPLETE_KEYS):
    if not isinstance(body, Mapping):
        raise ProcedureWriteError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise ProcedureWriteError("body keys must be strings")
    if set(body) - allowed:
        raise ProcedureWriteError("unknown field")
    return body


def _require_text(value, field, max_length):
    if not is_valid_text(value, max_length):
        raise ProcedureWriteError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise ProcedureWriteError(f"{field} is invalid")
    return stripped


def _workspace_procedure(value):
    """The server-owned workspace procedure id, never a native docname."""

    return _require_text(value, "procedure", MAX_WORKSPACE_ID_LENGTH)


def validate_complete(body):
    """Return the normalized completion payload or raise ``ProcedureWriteError``."""

    body = _require_body(body)
    for required in ("procedure", "operation_key"):
        if required not in body:
            raise ProcedureWriteError(f"missing field {required}")
    return {
        "kind": procedure_contract.PROCEDURE_COMPLETE_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "procedure": _workspace_procedure(body["procedure"]),
    }


def validate_create(body):
    """Return the normalized creation payload or raise ``ProcedureWriteError``.

    The Service Request is the only record selector the client sends. The
    created Clinical Procedure is never named by the client.
    """

    body = _require_body(body, CREATE_KEYS)
    for required in ("service_request", "operation_key"):
        if required not in body:
            raise ProcedureWriteError(f"missing field {required}")
    return {
        "kind": procedure_contract.PROCEDURE_CREATE_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "service_request": _require_text(
            body["service_request"],
            "service_request",
            MAX_SERVICE_REQUEST_LENGTH,
        ),
    }

