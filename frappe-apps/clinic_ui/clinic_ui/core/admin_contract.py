"""Closed-world contract for the admin users + masters family.

The design rulings this module encodes (they are not re-decided here):

* ``create_user`` is ONE mutation per budget unit: the ``User`` row, its
  ``User Role`` rows and the optional ``Healthcare Practitioner`` link are
  created together. The client supplies the password; the gateway never
  generates a secret and no credential is logged or placed in a receipt.
* the privilege boundary is the assignable-role allowlist. Only the clinic
  role-code mapped native roles, the built-in module roles and the five clinic
  ``Role`` records may be assigned. ``System Manager`` and ``Healthcare
  Administrator`` may NEVER be assigned through the contract; both are refused
  pre-fence with a named line.
* ``edit_master`` is one mutation per DocType variant (warehouse, supplier,
  item + price, price list, mode of payment + account, procedure template, and
  the two prerequisites the install lacks: UOM and Supplier Group).
  Each variant accepts exactly its designed payload keys, and duplicates are
  refused pre-fence.
* the master prerequisites (a resolvable company, an installed UOM row for the
  item unit, an installed Supplier Group row for the supplier) are decided
  before the fence claim, never after it.
* company and currency are read from the site's own records by the gateway;
  nothing here hardcodes a company or a currency.

Nothing in this module imports Frappe, reads a record or performs a write.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from clinic_ui.core import write_budgets
from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.core.text import is_valid_text

ADMIN_USER_KIND = "admin_user"
ADMIN_MASTER_KIND = "admin_master"
ADMIN_KINDS = (ADMIN_USER_KIND, ADMIN_MASTER_KIND)

USER_DOCTYPE = "User"
ROLE_DOCTYPE = "Role"
PRACTITIONER_DOCTYPE = "Healthcare Practitioner"
WAREHOUSE_DOCTYPE = "Warehouse"
SUPPLIER_DOCTYPE = "Supplier"
UOM_DOCTYPE = "UOM"
SUPPLIER_GROUP_DOCTYPE = "Supplier Group"
ITEM_DOCTYPE = "Item"
ITEM_PRICE_DOCTYPE = "Item Price"
PRICE_LIST_DOCTYPE = "Price List"
MODE_OF_PAYMENT_DOCTYPE = "Mode of Payment"
PROCEDURE_TEMPLATE_DOCTYPE = "Clinical Procedure Template"

# Design Amendment A1: ``uom`` and ``supplier_group`` are contract masters
# because they are prerequisites of the approved item and supplier rows, and the
# install has none. Order: uom -> supplier_group -> price list -> item ->
# procedure template (warehouse, supplier and mode of payment are
# order-independent, but supplier_group must precede supplier).
# Design Amendment A1.1 adds ``practitioner``: one Healthcare Practitioner for
# the SIT doctor user, order-independent relative to that chain.
MASTER_TYPES = (
    "warehouse",
    "supplier",
    "item",
    "price_list",
    "mode_of_payment",
    "procedure_template",
    "supplier_group",
    "uom",
    "practitioner",
)

MASTER_DOCTYPES = {
    "warehouse": WAREHOUSE_DOCTYPE,
    "supplier": SUPPLIER_DOCTYPE,
    "item": ITEM_DOCTYPE,
    "price_list": PRICE_LIST_DOCTYPE,
    "mode_of_payment": MODE_OF_PAYMENT_DOCTYPE,
    "procedure_template": PROCEDURE_TEMPLATE_DOCTYPE,
    "supplier_group": SUPPLIER_GROUP_DOCTYPE,
    "uom": UOM_DOCTYPE,
    "practitioner": PRACTITIONER_DOCTYPE,
}

# The first supplier group the install/contract supplies. The supplier row needs
# a group; the server resolves it (never the client) and refuses pre-fence when
# no group exists.
DEFAULT_SUPPLIER_GROUP = "All Supplier Groups"

# The privilege boundary. A role is assignable only when it is one of the
# built-in module roles, one of the five clinic Role records, or a native role
# that the site's role map maps a clinic code to and that is also in this set.
ASSIGNABLE_ROLES = frozenset(
    {
        "Physician",
        "Nursing User",
        "Stock Manager",
        "Stock User",
        "Accounts User",
        "Purchase User",
        "Sales User",
        "Item Manager",
        "Clinic Lab Reception",
        "Clinic Lab Pharmacy",
        "Clinic Lab Cashier",
        "Clinic Lab Manager",
        "Clinic Lab Administrator",
    }
)
# Never assignable through the contract, whatever a site role map says. The
# acting ADM synthetic user carries System Manager natively from site config;
# that is not a contract grant.
FORBIDDEN_ASSIGN_ROLES = frozenset({"System Manager", "Healthcare Administrator"})

# Every master variant accepts exactly these payload keys. No invented field.
MASTER_PAYLOAD_KEYS = {
    "warehouse": frozenset({"warehouse_name"}),
    "supplier": frozenset({"supplier_name"}),
    "item": frozenset(
        {
            "item_code",
            "item_name",
            "item_group",
            "stock_uom",
            "price_list",
            "rate",
        }
    ),
    "price_list": frozenset({"price_list_name"}),
    "mode_of_payment": frozenset({"mode_of_payment", "account"}),
    "procedure_template": frozenset({"template_name", "item_code", "rate"}),
    "supplier_group": frozenset({"supplier_group_name"}),
    "uom": frozenset({"uom_name"}),
    # Amendment A1.1. ``user`` is the installed ``user_id`` link; it is optional
    # and may be sent empty. Every other key is required text.
    "practitioner": frozenset({"practitioner_name", "user"}),
}

# Installed-field preflight. Logical name -> installed candidates, per DocType.
# A DocType that is absent, or a logical field with no installed candidate,
# refuses the family with a recorded reason before the fence.
REQUIRED_FIELDS = {
    USER_DOCTYPE: {
        "email": ("email",),
        "full_name": ("full_name", "first_name"),
        "enabled": ("enabled",),
        "roles": ("roles",),
    },
    ROLE_DOCTYPE: {"role_name": ("role_name", "name")},
    PRACTITIONER_DOCTYPE: {
        # Amendment A1.1: the practitioner master op writes these installed
        # fields. ``practitioner_name`` is the contract payload name; the
        # installed required ``first_name`` carries the same text, and the
        # installed ``status`` default (Active) satisfies its own requirement.
        "practitioner_name": ("practitioner_name",),
        "first_name": ("first_name",),
        "status": ("status",),
        "user_id": ("user_id",),
    },
    WAREHOUSE_DOCTYPE: {
        "warehouse_name": ("warehouse_name",),
        "company": ("company",),
    },
    SUPPLIER_DOCTYPE: {
        "supplier_name": ("supplier_name",),
        "supplier_group": ("supplier_group",),
    },
    ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "item_name": ("item_name",),
        "item_group": ("item_group",),
        "stock_uom": ("stock_uom",),
    },
    ITEM_PRICE_DOCTYPE: {
        "item_code": ("item_code",),
        "price_list": ("price_list",),
        "rate": ("rate",),
        "currency": ("currency",),
    },
    PRICE_LIST_DOCTYPE: {
        "price_list_name": ("price_list_name",),
        "currency": ("currency",),
        "selling": ("selling",),
    },
    MODE_OF_PAYMENT_DOCTYPE: {
        "mode_of_payment": ("mode_of_payment",),
        "accounts": ("accounts",),
    },
    PROCEDURE_TEMPLATE_DOCTYPE: {
        "template": ("template",),
        "item_code": ("item_code",),
        "rate": ("rate",),
        "company": ("company",),
    },
    SUPPLIER_GROUP_DOCTYPE: {
        "supplier_group_name": ("supplier_group_name",),
    },
    UOM_DOCTYPE: {
        "uom_name": ("uom_name",),
    },
}

MAX_TEXT_LENGTH = 140
MAX_NAME_LENGTH = 140
MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128
MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RESERVED_OPERATION_KEY_PREFIXES = (
    "clinic-ui-",
    "wpa1_",
    "wsa1_",
    "wse1_",
    "wsp1_",
)

RECEIPT_CODEC = "admin"
RECEIPT_VERSION = 1
MAX_RECEIPT_LENGTH = 1024
RECEIPT_KEYS = frozenset(
    {"codec", "version", "kind", "master_type", "identifier"}
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

BUDGETS = {kind: write_budgets.budget_for(kind) for kind in ADMIN_KINDS}


class AdminContractError(ValueError):
    """An admin write input or codec value is outside the accepted shape."""


class AdminTargetNotEligible(Exception):
    """The requested target is a duplicate or otherwise not writable."""


class AdminBudgetExhausted(Exception):
    """The durable local test budget for this admin kind is used up."""


@dataclass(frozen=True)
class AdminReceipt:
    """Decoded terminal receipt for one committed admin operation.

    A user receipt carries only the created user name; a master receipt carries
    the variant and the created row name. No credential is ever present.
    """

    kind: str
    master_type: str
    identifier: str


# -- installed preflight ---------------------------------------------------


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete."""

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


# -- privilege boundary ----------------------------------------------------


def _mapped_roles(role_map, clinic_role_code):
    if not isinstance(role_map, dict):
        return None
    mapped = role_map.get(clinic_role_code)
    if not isinstance(mapped, (list, tuple)) or not mapped:
        return None
    roles = []
    for role in mapped:
        if not isinstance(role, str):
            return None
        name = role.strip()
        if not name or name in roles:
            return None
        roles.append(name)
    return tuple(roles)


def assignable_roles(role_map, clinic_role_code):
    """Return the native roles a clinic code maps to, or ``None``.

    The returned roles are only a proposal: :func:`role_problem` decides
    whether every one of them is inside the allowlist.
    """

    if clinic_role_code not in SUPPORTED_ROLE_CODES:
        return None
    return _mapped_roles(role_map, clinic_role_code)


def role_problem(role_map, clinic_role_code):
    """Return a named pre-fence role problem, or ``None``.

    The privilege boundary is decided here: every mapped native role must be in
    the assignable allowlist. System Manager and Healthcare Administrator are
    never assignable, even when a site role map maps a clinic code to them.
    """

    if clinic_role_code not in SUPPORTED_ROLE_CODES:
        return f"{clinic_role_code} is not a supported clinic role code"
    roles = assignable_roles(role_map, clinic_role_code)
    if not roles:
        return f"{clinic_role_code} has no configured native role"
    for role in roles:
        if role in FORBIDDEN_ASSIGN_ROLES:
            return f"role {role} for {clinic_role_code} may not be assigned"
        if role not in ASSIGNABLE_ROLES:
            return f"role {role} for {clinic_role_code} is not assignable"
    return None


# -- strict request bodies -------------------------------------------------

USER_BODY_KEYS = frozenset(
    {
        "operation_key",
        "email",
        "username",
        "full_name",
        "clinic_role_code",
        "practitioner",
        "password",
    }
)
USER_REQUIRED_KEYS = frozenset(
    {"operation_key", "full_name", "clinic_role_code", "password"}
)
MASTER_BODY_KEYS = frozenset({"operation_key", "master_type", "payload"})


def require_kind(kind):
    if not isinstance(kind, str) or kind not in ADMIN_KINDS:
        raise AdminContractError("kind is not an admin kind")
    return kind


def budget(kind):
    return BUDGETS[require_kind(kind)]


def require_operation_key(value):
    if not isinstance(value, str):
        raise AdminContractError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise AdminContractError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise AdminContractError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise AdminContractError("operation_key is reserved")
    return value


def _require_body(body, allowed):
    if not isinstance(body, dict):
        raise AdminContractError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise AdminContractError("body keys must be strings")
    unknown = set(body) - allowed
    if unknown:
        raise AdminContractError("unknown field")
    return body


def _require_text(value, field, max_length, *, required=True):
    if value is None and not required:
        return None
    if not is_valid_text(value, max_length):
        raise AdminContractError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise AdminContractError(f"{field} is invalid")
    return stripped


def _optional_text(value, field, max_length):
    if value is None:
        return None
    return _require_text(value, field, max_length)


def _looks_like_email(value):
    return (
        is_valid_text(value, MAX_TEXT_LENGTH)
        and "@" in value
        and " " not in value
        and value.count("@") == 1
    )


def _looks_like_username(value):
    return OPERATION_KEY_RE.fullmatch(value) is not None or re.fullmatch(
        r"^[A-Za-z0-9][A-Za-z0-9._@-]{1,139}$", value
    ) is not None


def _password(value):
    if not isinstance(value, str):
        raise AdminContractError("password is invalid")
    if not MIN_PASSWORD_LENGTH <= len(value) <= MAX_PASSWORD_LENGTH:
        raise AdminContractError("password is invalid")
    if value.strip() != value or any(char.isspace() for char in value):
        raise AdminContractError("password is invalid")
    return value


def validate_create_user(body):
    """Return the normalized create-user payload or raise ``AdminContractError``."""

    body = _require_body(body, USER_BODY_KEYS)
    for required in USER_REQUIRED_KEYS:
        if required not in body:
            raise AdminContractError(f"missing field {required}")

    clinic_role_code = _require_text(
        body["clinic_role_code"], "clinic_role_code", MAX_NAME_LENGTH
    )
    if clinic_role_code not in SUPPORTED_ROLE_CODES:
        raise AdminContractError("clinic_role_code is not supported")

    email = _optional_text(body.get("email"), "email", MAX_TEXT_LENGTH)
    username = _optional_text(body.get("username"), "username", MAX_NAME_LENGTH)
    if email is None and username is None:
        raise AdminContractError("email or username is required")
    if email is not None and not _looks_like_email(email):
        raise AdminContractError("email is invalid")
    if username is not None and not _looks_like_username(username):
        raise AdminContractError("username is invalid")

    return {
        "kind": ADMIN_USER_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "email": email,
        "username": username,
        "full_name": _require_text(
            body["full_name"], "full_name", MAX_TEXT_LENGTH
        ),
        "clinic_role_code": clinic_role_code,
        "practitioner": _optional_text(
            body.get("practitioner"), "practitioner", MAX_NAME_LENGTH
        ),
        "password": _password(body["password"]),
    }


def master_type_problem(master_type):
    if master_type not in MASTER_TYPES:
        return "master_type is not supported"
    return None


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
    )


def _positive_rate(value):
    if not _number(value):
        raise AdminContractError("rate is invalid")
    if float(value) <= 0:
        raise AdminContractError("rate must be positive")
    return float(value)


def _normalise_master_payload(master_type, payload):
    if not isinstance(payload, dict):
        raise AdminContractError("payload must be a mapping")
    expected = MASTER_PAYLOAD_KEYS[master_type]
    if set(payload) != set(expected):
        raise AdminContractError("payload shape is invalid")

    normalized = {}
    for key in sorted(expected):
        value = payload[key]
        if key == "rate":
            normalized[key] = _positive_rate(value)
        elif master_type == "practitioner" and key == "user":
            # Amendment A1.1: the user link is optional. An empty string is the
            # explicit "no link" the form sends, never a missing key.
            if value is None or (isinstance(value, str) and not value.strip()):
                normalized[key] = None
            else:
                normalized[key] = _require_text(
                    value, key, MAX_NAME_LENGTH
                )
        else:
            normalized[key] = _require_text(value, key, MAX_TEXT_LENGTH)
    return normalized


def validate_edit_master(body):
    """Return the normalized edit-master payload or raise ``AdminContractError``."""

    body = _require_body(body, MASTER_BODY_KEYS)
    for required in ("operation_key", "master_type", "payload"):
        if required not in body:
            raise AdminContractError(f"missing field {required}")
    master_type = body["master_type"]
    if master_type_problem(master_type) is not None:
        raise AdminContractError("master_type is not supported")
    return {
        "kind": ADMIN_MASTER_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "master_type": master_type,
        "payload": _normalise_master_payload(master_type, body["payload"]),
    }


def identifier_for(master_type, payload):
    """Return the natural duplicate key for one master variant."""

    if master_type == "warehouse":
        return payload["warehouse_name"]
    if master_type == "supplier":
        return payload["supplier_name"]
    if master_type == "item":
        return payload["item_code"]
    if master_type == "price_list":
        return payload["price_list_name"]
    if master_type == "mode_of_payment":
        return payload["mode_of_payment"]
    if master_type == "procedure_template":
        return payload["template_name"]
    if master_type == "supplier_group":
        return payload["supplier_group_name"]
    if master_type == "uom":
        return payload["uom_name"]
    if master_type == "practitioner":
        # The installed docname is autonamed (HLC-PRAC-.YYYY.-), so the receipt
        # identifier is the supplied practitioner name. Exactly-once for the
        # variant is per linked user, decided by the endpoint before the fence.
        return payload["practitioner_name"]
    raise AdminContractError("master_type is not supported")


# -- pre-fence prerequisites ------------------------------------------------
#
# The install has no UOM and no Supplier Group rows, while the approved item row
# links a UOM and the approved supplier row links a group. Both are contract
# masters now (Amendment A1); the checks below are the designed pre-fence
# refusals so a missing prerequisite can never fail *after* the fence claim.


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def prerequisite_problem(master_type, prerequisites):
    """Return the named pre-fence prerequisite problem, or ``None``.

    ``prerequisites`` is the server-read site state: the resolved company, the
    installed UOM row count, the resolved supplier group, the installed
    supplier-group row count and — Amendment A1.1 — the installed row count of
    every mandatory Link field on Healthcare Practitioner. Nothing here is
    client input.
    """

    if master_type_problem(master_type) is not None:
        return "master_type is not supported"
    if not isinstance(prerequisites, dict):
        return "master prerequisites unreadable"
    if not _present(prerequisites.get("company")):
        return "no company resolves for the admin surface"
    if not _count(prerequisites.get("uom_rows")):
        return "UOM rows are unreadable"
    if not _count(prerequisites.get("supplier_group_rows")):
        return "Supplier Group rows are unreadable"
    if master_type == "item" and prerequisites["uom_rows"] == 0:
        return "no UOM rows are installed for the item unit"
    if master_type == "supplier" and not _present(
        prerequisites.get("supplier_group")
    ):
        return "no Supplier Group rows are installed for the supplier"
    if master_type == "practitioner":
        links = prerequisites.get("practitioner_links")
        if not isinstance(links, dict):
            return "practitioner link targets unreadable"
        for field, rows in sorted(links.items()):
            if not _count(rows):
                return f"practitioner link rows unreadable for {field}"
            if rows == 0:
                return (
                    "no rows are installed for the mandatory practitioner "
                    f"link {field}"
                )
    return None


def user_identifier(payload):
    """The identity used for duplicate checks and the user receipt."""

    return payload["email"] or payload["username"]


# -- post-write closed-world re-read ---------------------------------------


def user_facts_problem(facts, expected):
    """Check the closed-world state after one user mutation."""

    if not isinstance(facts, dict):
        return "post-create user facts unreadable"
    if not isinstance(expected, dict):
        return "expected user facts unreadable"
    if facts.get("user") != expected.get("identifier"):
        return "created user differs from the request"
    if facts.get("enabled") not in (1, True):
        return "created user is disabled"
    roles = facts.get("roles")
    if not isinstance(roles, (list, tuple, set, frozenset)):
        return "created user roles unreadable"
    if set(roles) != set(expected.get("roles") or ()):
        return "created user roles differ from the request"
    if facts.get("practitioner") != expected.get("practitioner"):
        return "created user practitioner link differs from the request"
    return None


def master_facts_problem(master_type, facts, expected):
    """Check the closed-world state after one master mutation."""

    if not isinstance(facts, dict):
        return "post-create master facts unreadable"
    if not isinstance(expected, dict):
        return "expected master facts unreadable"
    payload = expected.get("payload")
    if not isinstance(payload, dict):
        return "expected master payload unreadable"
    if master_type == "practitioner":
        # Amendment A1.1: the row is autonamed, so the closed-world check reads
        # the installed name and compares the supplied name and the user link.
        if not _present(facts.get("name")):
            return "created practitioner has no name"
        if facts.get("practitioner_name") != payload["practitioner_name"]:
            return "created practitioner differs from the request"
        if (facts.get("user_id") or None) != payload["user"]:
            return "created practitioner user differs from the request"
        return None
    if facts.get("name") != expected.get("identifier"):
        return "created master differs from the request"

    if master_type == "warehouse":
        if not _present(facts.get("company")):
            return "created warehouse has no company"
    elif master_type == "supplier":
        if facts.get("name") != payload["supplier_name"]:
            return "created supplier differs from the request"
    elif master_type == "item":
        if facts.get("item_code") != payload["item_code"]:
            return "created item code differs from the request"
        if facts.get("price_list") != payload["price_list"]:
            return "created item price list differs from the request"
        if not _same_number(facts.get("rate"), payload["rate"]):
            return "created item rate differs from the request"
    elif master_type == "price_list":
        if not _present(facts.get("currency")):
            return "created price list has no currency"
        if facts.get("selling") not in (1, True):
            return "created price list is not a selling list"
    elif master_type == "mode_of_payment":
        if facts.get("account") != payload["account"]:
            return "created payment account differs from the request"
    elif master_type == "procedure_template":
        if facts.get("item_code") != payload["item_code"]:
            return "created procedure template item differs from the request"
    elif master_type == "supplier_group":
        if facts.get("name") != payload["supplier_group_name"]:
            return "created supplier group differs from the request"
    elif master_type == "uom":
        if facts.get("name") != payload["uom_name"]:
            return "created UOM differs from the request"
    else:
        return "master_type is not supported"
    return None


def _present(value):
    return isinstance(value, str) and bool(value.strip())


def _same_number(left, right):
    return _number(left) and _number(right) and abs(float(left) - float(right)) <= 1e-6


# -- codecs ----------------------------------------------------------------


def encode_receipt(*, kind, identifier, master_type=""):
    kind = require_kind(kind)
    if kind == ADMIN_USER_KIND and master_type:
        raise AdminContractError("user receipt carries no master type")
    if kind == ADMIN_MASTER_KIND:
        if master_type_problem(master_type) is not None:
            raise AdminContractError("invalid receipt master type")
    if not is_valid_text(identifier, MAX_NAME_LENGTH):
        raise AdminContractError("invalid receipt identifier")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "master_type": master_type,
            "identifier": identifier,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise AdminContractError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return an :class:`AdminReceipt` or raise ``AdminContractError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise AdminContractError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise AdminContractError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise AdminContractError("invalid admin receipt")
    version = payload["version"]
    if (
        payload["codec"] != RECEIPT_CODEC
        or not isinstance(version, int)
        or isinstance(version, bool)
        or version != RECEIPT_VERSION
    ):
        raise AdminContractError("invalid admin receipt")
    try:
        kind = require_kind(payload["kind"])
    except AdminContractError as exc:
        raise AdminContractError("invalid admin receipt") from exc
    master_type = payload["master_type"]
    if not isinstance(master_type, str):
        raise AdminContractError("invalid admin receipt")
    if kind == ADMIN_USER_KIND and master_type:
        raise AdminContractError("invalid admin receipt")
    if kind == ADMIN_MASTER_KIND and master_type_problem(master_type) is not None:
        raise AdminContractError("invalid admin receipt")
    identifier = payload["identifier"]
    if not is_valid_text(identifier, MAX_NAME_LENGTH):
        raise AdminContractError("invalid admin receipt")
    return AdminReceipt(
        kind=kind, master_type=master_type, identifier=identifier
    )


def _require_secret(secret, field="secret"):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise AdminContractError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_NAME_LENGTH) or not is_valid_text(
        actor, MAX_NAME_LENGTH
    ):
        raise AdminContractError("alias or acknowledgement context is invalid")
    return site.strip(), actor.strip()


def _framed(prefix, *values):
    message = bytearray(prefix)
    for value in values:
        data = value.encode("utf-8")
        message += b"|" + str(len(data)).encode("ascii") + b":" + data
    return bytes(message)


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise AdminContractError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_admin_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, dict):
        raise AdminContractError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise AdminContractError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise AdminContractError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise AdminContractError("ack token is invalid")
    return {
        "operation_key": require_operation_key(body["operation_key"]),
        "kind": require_kind(body["kind"]),
        "ack_token": body["ack_token"],
    }


def operation_digest(secret, *, kind, operation_key, fields):
    """Bind one admin operation to its kind, key and target fields.

    Credentials are never passed here: the caller digests only the stable
    identity of the operation, so no password derivative is stored.
    """

    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not isinstance(fields, dict):
        raise AdminContractError("fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            raise AdminContractError("field names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise AdminContractError("field values must be text")
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
        b"clinic_ui_admin_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
