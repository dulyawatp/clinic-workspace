"""Closed-world contract for the stock receive / transfer / adjust family.

Design rulings encoded here (they are not re-decided):

* receive creates the batch master where the item needs one and then submits a
  ``Purchase Receipt`` with ``set_posting_time`` pinned; the batch creation and
  the receipt submit are ONE receive operation (one fence claim, budget
  ``stock_receive`` 1). A purchase-order reference is optional and, when given,
  the receipt may not exceed its pending quantity,
* transfer submits a ``Stock Entry`` of type ``Material Transfer`` with per-row
  ``s_warehouse``/``t_warehouse`` and the batch on batch-managed items,
* adjust is a RESTRICTED ``Stock Reconciliation`` (ADM only) with a required
  reason; the reason and the approver identity live in the fence receipt, not
  in a DocType field,
* quantities are never written to the ledger, a Bin or a Batch directly: every
  movement goes through the installed submittable document,
* the closed-world re-read EXPECTS a count-based side-effect list: one Stock
  Ledger Entry per posted row, the Bin movement at each named warehouse and the
  reviewed GL row count (0 or two per entry),
* every designed refusal is a pre-fence decision. Insufficient stock and
  insufficient batch stock use the installed validation's own titles
  (``Insufficient Stock`` / ``Insufficient Stock for Batch``) with the named
  line; negative stock policy (D-09) and FEFO (D-10) stay open and unwired.

Installed field names are resolved from the site's own metadata; a missing
DocType or field refuses the write pre-fence (design ruling 8).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from clinic_ui.core import procedure_contract, write_budgets
from clinic_ui.core.text import is_valid_text

RECEIVE_KIND = "stock_receive"
TRANSFER_KIND = "stock_transfer"
ADJUST_KIND = "stock_adjust"
STOCK_KINDS = (RECEIVE_KIND, TRANSFER_KIND, ADJUST_KIND)

PURCHASE_RECEIPT_DOCTYPE = "Purchase Receipt"
PURCHASE_RECEIPT_ITEM_DOCTYPE = "Purchase Receipt Item"
STOCK_ENTRY_DOCTYPE = "Stock Entry"
STOCK_ENTRY_DETAIL_DOCTYPE = "Stock Entry Detail"
STOCK_RECONCILIATION_DOCTYPE = "Stock Reconciliation"
STOCK_RECONCILIATION_ITEM_DOCTYPE = "Stock Reconciliation Item"
BATCH_DOCTYPE = "Batch"
ITEM_DOCTYPE = "Item"
BIN_DOCTYPE = "Bin"

GUARDED_DOCTYPES = (
    PURCHASE_RECEIPT_DOCTYPE,
    STOCK_ENTRY_DOCTYPE,
    STOCK_RECONCILIATION_DOCTYPE,
    BATCH_DOCTYPE,
)

MATERIAL_TRANSFER_TYPE = "Material Transfer"
FINAL_DOCSTATUS = 1

# Reviewed executable boundary: the four controllers this family drives or
# creates, pinned by version, source sha256 (erpnext v16.52.1 image) and MRO,
# plus the site-global hooks already reviewed for the appointment family (the
# same pinned stack). Our own fence kinds are part of the pin.
REVIEWED_VERSIONS = {
    "frappe": "16.54.0",
    "erpnext": "16.52.1",
    "healthcare": "16.6.1",
}

REVIEWED_SOURCE_SHA256 = {
    "purchase_receipt": (
        "d8b926f62edac07ef08c8a3e0af15bbf7a85fea649f9f2ad94d394dbfcf58170"
    ),
    "stock_entry": (
        "8a01e23850d6f9f4aad86a6c923f3681b7ceb59ee01c64684db33da921b4ffc3"
    ),
    "stock_reconciliation": (
        "b907634b217e3d2aef055fb7bb07c9ca61e26ff708b35337bdef7bd573538e7e"
    ),
    "batch": "a5d4f8321e42387cf305bca91659c8b2eb4df49f7c5a5bb40ea40abe00a0ee04",
}

REVIEWED_MRO = {
    PURCHASE_RECEIPT_DOCTYPE: (
        "erpnext.stock.doctype.purchase_receipt.purchase_receipt.PurchaseReceipt",
        "erpnext.controllers.buying_controller.BuyingController",
        "erpnext.controllers.subcontracting_controller.SubcontractingController",
        "erpnext.controllers.stock_controller.StockController",
        "erpnext.controllers.accounts_controller.AccountsController",
        "erpnext.utilities.transaction_base.TransactionBase",
        "erpnext.controllers.status_updater.StatusUpdater",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
    STOCK_ENTRY_DOCTYPE: (
        "erpnext.stock.doctype.stock_entry.stock_entry.StockEntry",
        "erpnext.controllers.stock_controller.StockController",
        "erpnext.controllers.accounts_controller.AccountsController",
        "erpnext.utilities.transaction_base.TransactionBase",
        "erpnext.controllers.status_updater.StatusUpdater",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "erpnext.controllers.subcontracting_inward_controller."
        "SubcontractingInwardController",
        "builtins.object",
    ),
    STOCK_RECONCILIATION_DOCTYPE: (
        "erpnext.stock.doctype.stock_reconciliation.stock_reconciliation."
        "StockReconciliation",
        "erpnext.controllers.stock_controller.StockController",
        "erpnext.controllers.accounts_controller.AccountsController",
        "erpnext.utilities.transaction_base.TransactionBase",
        "erpnext.controllers.status_updater.StatusUpdater",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
    BATCH_DOCTYPE: (
        "erpnext.stock.doctype.batch.batch.Batch",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
}

# Site-global wildcard hooks: identical to the reviewed appointment pin on the
# same stack, so they are read from that module instead of a second list.
REVIEWED_WILDCARD_EVENTS = procedure_contract.REVIEWED_WILDCARD_EVENTS

# Reviewed per-doctype doc_events: only erpnext's own Stock Entry hooks exist on
# this stack (material-request progress); the other guarded doctypes carry none.
# Our app registers no doc_events here. Anything else fails closed.
REVIEWED_DOC_EVENTS = {
    doctype: procedure_contract.REVIEWED_DOC_EVENTS.get(doctype, {})
    for doctype in GUARDED_DOCTYPES
}

# A scheduler job whose dotted path names one of these modules would post stock
# outside this contract; none may exist for the family.
SCHEDULER_STOCK_TOKENS = (
    "purchase_receipt",
    "stock_entry",
    "stock_reconciliation",
)

INSUFFICIENT_STOCK_MESSAGE = "Insufficient Stock"
INSUFFICIENT_BATCH_MESSAGE = "Insufficient Stock for Batch"
OVERRECEIPT_MESSAGE = (
    "Over-receipt: quantity exceeds the purchase order pending quantity"
)

HTTP_REFUSED = 417
GL_ROWS_PER_ENTRY = 2

MAX_TEXT_LENGTH = 140
MAX_REASON_LENGTH = 280
MAX_ROWS = 20

# Installed-field preflight: logical name -> installed candidates per DocType.
INSTALLED_CANDIDATES = {
    PURCHASE_RECEIPT_DOCTYPE: {
        "supplier": ("supplier",),
        "company": ("company",),
        "posting_date": ("posting_date",),
        "set_posting_time": ("set_posting_time",),
        "items": ("items",),
        "docstatus": ("docstatus",),
    },
    PURCHASE_RECEIPT_ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "qty": ("qty",),
        "warehouse": ("warehouse",),
        "uom": ("uom",),
        "batch_no": ("batch_no",),
        "use_serial_batch_fields": ("use_serial_batch_fields",),
    },
    STOCK_ENTRY_DOCTYPE: {
        "stock_entry_type": ("stock_entry_type",),
        "company": ("company",),
        "posting_date": ("posting_date",),
        "set_posting_time": ("set_posting_time",),
        "items": ("items",),
        "docstatus": ("docstatus",),
    },
    STOCK_ENTRY_DETAIL_DOCTYPE: {
        "s_warehouse": ("s_warehouse",),
        "t_warehouse": ("t_warehouse",),
        "item_code": ("item_code",),
        "qty": ("qty",),
        "uom": ("uom",),
        "batch_no": ("batch_no",),
        "use_serial_batch_fields": ("use_serial_batch_fields",),
    },
    STOCK_RECONCILIATION_DOCTYPE: {
        "company": ("company",),
        "posting_date": ("posting_date",),
        "set_posting_time": ("set_posting_time",),
        "items": ("items",),
        "expense_account": ("expense_account",),
        "docstatus": ("docstatus",),
    },
    STOCK_RECONCILIATION_ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "warehouse": ("warehouse",),
        "qty": ("qty",),
        "batch_no": ("batch_no",),
        "use_serial_batch_fields": ("use_serial_batch_fields",),
    },
    BATCH_DOCTYPE: {
        "batch_id": ("batch_id",),
        "item": ("item",),
        "expiry_date": ("expiry_date",),
        "batch_qty": ("batch_qty",),
    },
    ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "has_batch_no": ("has_batch_no",),
        "has_expiry_date": ("has_expiry_date",),
        "is_stock_item": ("is_stock_item",),
    },
    BIN_DOCTYPE: {
        "warehouse": ("warehouse",),
        "item_code": ("item_code",),
        "actual_qty": ("actual_qty",),
    },
}

OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RESERVED_OPERATION_KEY_PREFIXES = (
    "clinic-ui-",
    "wpa1_",
    "wsa1_",
    "wse1_",
    "wsp1_",
)
MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_NAME_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024

RECEIPT_CODEC = "stock"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {
        "codec",
        "version",
        "kind",
        "reference",
        "item_code",
        "warehouse",
        "qty",
        "reason",
    }
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

RECEIVE_KEYS = frozenset(
    {
        "operation_key",
        "item_code",
        "qty",
        "warehouse",
        "supplier",
        "batch_id",
        "batch_expiry",
        "purchase_order",
    }
)
TRANSFER_KEYS = frozenset(
    {
        "operation_key",
        "item_code",
        "qty",
        "source_warehouse",
        "target_warehouse",
        "batch_id",
    }
)
ADJUST_KEYS = frozenset(
    {
        "operation_key",
        "item_code",
        "qty",
        "warehouse",
        "reason",
        "batch_id",
    }
)


class StockContractError(ValueError):
    """A stock write input or codec value is outside the accepted shape."""


class StockTargetNotEligible(Exception):
    """The target changed between the pre-fence read and the held fence."""


class StockBudgetExhausted(Exception):
    """The durable local test budget for this stock kind is used up."""


@dataclass(frozen=True)
class StockReceipt:
    """Decoded terminal receipt for one committed stock movement."""

    kind: str
    reference: str
    item_code: str
    warehouse: str
    qty: float
    reason: str


def require_kind(kind):
    if not isinstance(kind, str) or kind not in STOCK_KINDS:
        raise StockContractError("kind is not a stock kind")
    return kind


def budget(kind):
    return write_budgets.budget_for(require_kind(kind))


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete."""

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    for doctype, fields in INSTALLED_CANDIDATES.items():
        installed = meta_fields.get(doctype)
        if not isinstance(installed, (set, frozenset, tuple, list)):
            return f"missing DocType {doctype}"
        installed = set(installed)
        for logical, candidates in fields.items():
            if not any(candidate in installed for candidate in candidates):
                return f"missing field {doctype}.{logical}"
    return None


def resolve_field(meta_fields, doctype, logical, candidates=None):
    if candidates is None:
        candidates = (INSTALLED_CANDIDATES.get(doctype) or {}).get(logical)
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
    source = snapshot.get("source_sha256")
    if not isinstance(source, dict):
        return "source hashes unreadable"
    for name, value in REVIEWED_SOURCE_SHA256.items():
        if source.get(name) != value:
            return f"source drift for {name}"
    mro = snapshot.get("controller_mro")
    if not isinstance(mro, dict):
        return "controller MRO unreadable"
    for doctype, reviewed in REVIEWED_MRO.items():
        if tuple(mro.get(doctype) or ()) != reviewed:
            return f"controller MRO drift for {doctype}"
    submittable = snapshot.get("submittable")
    if not isinstance(submittable, dict):
        return "submittable flags unreadable"
    for doctype in (
        PURCHASE_RECEIPT_DOCTYPE,
        STOCK_ENTRY_DOCTYPE,
        STOCK_RECONCILIATION_DOCTYPE,
    ):
        if submittable.get(doctype) != 1:
            return f"{doctype} is not submittable"
    if snapshot.get("enabled_server_scripts") != 0:
        return "server scripts are enabled"
    problem = procedure_contract.doc_events_problem(
        snapshot.get("doc_events"), REVIEWED_DOC_EVENTS
    )
    if problem:
        return problem
    wildcard = snapshot.get("wildcard_doc_events")
    if not isinstance(wildcard, dict):
        return "wildcard doc_events unreadable"
    for event, reviewed in REVIEWED_WILDCARD_EVENTS.items():
        if tuple(wildcard.get(event) or ()) != reviewed:
            return f"wildcard doc_events drift for {event}"
    if tuple(snapshot.get("scheduler_stock_jobs") or ()):
        return "a scheduler job touches the stock family"
    overrides = snapshot.get("class_overrides")
    if not isinstance(overrides, dict):
        return "class overrides unreadable"
    for doctype in GUARDED_DOCTYPES:
        if tuple(overrides.get(doctype) or ()):
            return f"{doctype} class is overridden or extended"
    if tuple(snapshot.get("fence_kinds") or ()) != STOCK_KINDS:
        return "fence kinds are not the reviewed stock set"
    return None


# -- strict request bodies -------------------------------------------------


def require_operation_key(value):
    if not isinstance(value, str):
        raise StockContractError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise StockContractError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise StockContractError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise StockContractError("operation_key is reserved")
    return value


def _require_body(body, allowed):
    if not isinstance(body, dict):
        raise StockContractError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise StockContractError("body keys must be strings")
    if set(body) - allowed:
        raise StockContractError("unknown field")
    return body


def _require_text(value, field, maximum=MAX_TEXT_LENGTH):
    if not is_valid_text(value, maximum):
        raise StockContractError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise StockContractError(f"{field} is invalid")
    return stripped


def _optional_text(value, field, maximum=MAX_TEXT_LENGTH):
    if value is None:
        return None
    return _require_text(value, field, maximum)


def _require_qty(value, *, positive):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise StockContractError("qty is invalid")
    if value != value or value in (float("inf"), float("-inf")):
        raise StockContractError("qty is invalid")
    if positive and float(value) <= 0:
        raise StockContractError("qty is invalid")
    if not positive and float(value) < 0:
        raise StockContractError("qty is invalid")
    return float(value)


def validate_receive(body):
    body = _require_body(body, RECEIVE_KEYS)
    for required in (
        "operation_key",
        "item_code",
        "qty",
        "warehouse",
        "supplier",
    ):
        if required not in body:
            raise StockContractError(f"missing field {required}")
    return {
        "kind": RECEIVE_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "item_code": _require_text(body["item_code"], "item_code"),
        "qty": _require_qty(body["qty"], positive=True),
        "warehouse": _require_text(body["warehouse"], "warehouse"),
        "supplier": _require_text(body["supplier"], "supplier"),
        "batch_id": _optional_text(body.get("batch_id"), "batch_id"),
        "batch_expiry": _optional_text(body.get("batch_expiry"), "batch_expiry"),
        "purchase_order": _optional_text(
            body.get("purchase_order"), "purchase_order"
        ),
    }


def validate_transfer(body):
    body = _require_body(body, TRANSFER_KEYS)
    for required in (
        "operation_key",
        "item_code",
        "qty",
        "source_warehouse",
        "target_warehouse",
    ):
        if required not in body:
            raise StockContractError(f"missing field {required}")
    return {
        "kind": TRANSFER_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "item_code": _require_text(body["item_code"], "item_code"),
        "qty": _require_qty(body["qty"], positive=True),
        "source_warehouse": _require_text(
            body["source_warehouse"], "source_warehouse"
        ),
        "target_warehouse": _require_text(
            body["target_warehouse"], "target_warehouse"
        ),
        "batch_id": _optional_text(body.get("batch_id"), "batch_id"),
    }


def validate_adjust(body):
    body = _require_body(body, ADJUST_KEYS)
    for required in ("operation_key", "item_code", "qty", "warehouse", "reason"):
        if required not in body:
            raise StockContractError(f"missing field {required}")
    return {
        "kind": ADJUST_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "item_code": _require_text(body["item_code"], "item_code"),
        "qty": _require_qty(body["qty"], positive=False),
        "warehouse": _require_text(body["warehouse"], "warehouse"),
        "reason": _require_text(body["reason"], "reason", MAX_REASON_LENGTH),
        "batch_id": _optional_text(body.get("batch_id"), "batch_id"),
    }


# -- pre-fence decisions ---------------------------------------------------


def _item_problems(detail, payload):
    item = detail.get("item")
    code = payload["item_code"]
    if not isinstance(item, dict) or not item.get("exists"):
        return [(HTTP_REFUSED, "item_code", f"item {code} does not exist")]
    if not item.get("is_stock_item"):
        return [(HTTP_REFUSED, "item_code", f"item {code} is not a stock item")]
    if not _text(item.get("stock_uom"), MAX_TEXT_LENGTH):
        return [(HTTP_REFUSED, "item_code", f"item {code} has no stock unit")]
    return []


def _warehouse_problem(detail, key, label):
    warehouse = detail.get(key)
    if not isinstance(warehouse, dict) or not warehouse.get("exists"):
        return (HTTP_REFUSED, label, f"warehouse {label} does not exist")
    return None


def _batch_problems(
    detail, payload, *, allow_create, require_stock
):
    """Batch decisions for a batch-managed item, with a named line each."""

    item = detail.get("item") or {}
    code = payload["item_code"]
    batch_id = payload.get("batch_id")
    problems = []
    if not item.get("has_batch_no"):
        if batch_id:
            problems.append(
                (
                    HTTP_REFUSED,
                    "batch_no",
                    f"item {code} does not use batches",
                )
            )
        return problems
    if not batch_id:
        problems.append(
            (
                HTTP_REFUSED,
                "batch_no",
                f"batch number is required for {code}",
            )
        )
        return problems

    batch = detail.get("batch")
    if not isinstance(batch, dict) or not batch.get("exists"):
        if not allow_create:
            problems.append(
                (
                    HTTP_REFUSED,
                    "batch_no",
                    f"batch {batch_id} for {code} does not exist",
                )
            )
            return problems
        expiry = payload.get("batch_expiry")
        if item.get("has_expiry_date") and not expiry:
            problems.append(
                (
                    HTTP_REFUSED,
                    "batch_expiry",
                    f"expiry date is required for a new batch of {code}",
                )
            )
            return problems
        if expiry and isinstance(detail.get("today"), str):
            if expiry < detail["today"]:
                problems.append(
                    (
                        HTTP_REFUSED,
                        "batch_expiry",
                        f"batch {batch_id} for {code} is expired",
                    )
                )
        return problems

    if batch.get("item") != code:
        problems.append(
            (
                HTTP_REFUSED,
                "batch_no",
                f"batch {batch_id} does not belong to {code}",
            )
        )
        return problems
    expiry = batch.get("expiry_date")
    today = detail.get("today")
    if isinstance(expiry, str) and expiry and isinstance(today, str):
        if expiry < today:
            problems.append(
                (
                    HTTP_REFUSED,
                    "batch_no",
                    f"batch {batch_id} for {code} is expired",
                )
            )
            return problems
    if require_stock and not _at_least(batch.get("batch_qty"), payload["qty"]):
        problems.append(
            (
                HTTP_REFUSED,
                "qty",
                f"{INSUFFICIENT_BATCH_MESSAGE}: batch {batch_id} for {code}",
            )
        )
    return problems


def receive_problems(detail, payload):
    """Every pre-fence receive decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    problems = list(_item_problems(detail, payload))
    if problems:
        return tuple(problems)
    if not detail.get("supplier_exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "supplier",
                f"supplier {payload['supplier']} does not exist",
            )
        )
    warehouse_problem = _warehouse_problem(
        detail, "warehouse", payload["warehouse"]
    )
    if warehouse_problem:
        problems.append(warehouse_problem)
    problems.extend(
        _batch_problems(detail, payload, allow_create=True, require_stock=False)
    )
    order = payload.get("purchase_order")
    if order:
        pending = detail.get("po_pending_qty")
        if not _number(pending):
            problems.append(
                (
                    HTTP_REFUSED,
                    "purchase_order",
                    f"purchase order {order} has no pending quantity for "
                    f"{payload['item_code']}",
                )
            )
        elif payload["qty"] > float(pending) + 1e-9:
            problems.append(
                (HTTP_REFUSED, "qty", OVERRECEIPT_MESSAGE)
            )
    return tuple(problems)


def transfer_problems(detail, payload):
    """Every pre-fence transfer decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    problems = list(_item_problems(detail, payload))
    if problems:
        return tuple(problems)
    source_problem = _warehouse_problem(
        detail, "warehouse", payload["source_warehouse"]
    )
    if source_problem:
        problems.append(source_problem)
    target_problem = _warehouse_problem(
        detail, "target_warehouse", payload["target_warehouse"]
    )
    if target_problem:
        problems.append(target_problem)
    if payload["source_warehouse"] == payload["target_warehouse"]:
        problems.append(
            (
                HTTP_REFUSED,
                "target_warehouse",
                "target warehouse must differ from the source warehouse",
            )
        )
    problems.extend(
        _batch_problems(detail, payload, allow_create=False, require_stock=True)
    )
    if not _at_least(detail.get("available_qty"), payload["qty"]):
        problems.append(
            (
                HTTP_REFUSED,
                "qty",
                f"{INSUFFICIENT_STOCK_MESSAGE}: {payload['item_code']} at "
                f"{payload['source_warehouse']}",
            )
        )
    return tuple(problems)


def adjust_problems(detail, payload):
    """Every pre-fence reconciliation decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    problems = list(_item_problems(detail, payload))
    if problems:
        return tuple(problems)
    warehouse_problem = _warehouse_problem(
        detail, "warehouse", payload["warehouse"]
    )
    if warehouse_problem:
        problems.append(warehouse_problem)
    problems.extend(
        _batch_problems(detail, payload, allow_create=False, require_stock=False)
    )
    if not _text(detail.get("stock_adjustment_account"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "expense_account",
                "the company has no stock adjustment account",
            )
        )
    return tuple(problems)


# -- EXPECTED side effects -------------------------------------------------


def expected_receive(detail, payload):
    return {
        "doctype": PURCHASE_RECEIPT_DOCTYPE,
        "kind": RECEIVE_KIND,
        "item_code": payload["item_code"],
        "sle_rows": 1,
        "bin_mode": "delta",
        "bins": {payload["warehouse"]: float(payload["qty"])},
        "gl_rows_max": GL_ROWS_PER_ENTRY,
    }


def expected_transfer(detail, payload):
    return {
        "doctype": STOCK_ENTRY_DOCTYPE,
        "kind": TRANSFER_KIND,
        "item_code": payload["item_code"],
        "sle_rows": 2,
        "bin_mode": "delta",
        "bins": {
            payload["source_warehouse"]: -float(payload["qty"]),
            payload["target_warehouse"]: float(payload["qty"]),
        },
        "gl_rows_max": GL_ROWS_PER_ENTRY * 2,
    }


def expected_adjust(detail, payload):
    return {
        "doctype": STOCK_RECONCILIATION_DOCTYPE,
        "kind": ADJUST_KIND,
        "item_code": payload["item_code"],
        "sle_rows": 1,
        "bin_mode": "set",
        "bins": {payload["warehouse"]: float(payload["qty"])},
        "gl_rows_max": GL_ROWS_PER_ENTRY,
    }


def prepared_problem(kind, prepared):
    """Closed-world check of the mutation body a gateway will execute.

    The prepared body is the server's own resolved document payload: it must
    carry the company resolved from installed state (never client input), the
    server-owned posting date with ``set_posting_time`` pinned, the stock unit
    and the kind's own route fields. A missing piece is refused before the
    fence, so a gateway drift can never turn into a post-dispatch KeyError.
    """

    if kind not in STOCK_KINDS:
        return "unknown stock kind"
    if not isinstance(prepared, dict):
        return "prepared body unreadable"
    if not _text(prepared.get("company"), MAX_TEXT_LENGTH):
        return "prepared body has no company"
    if not _text(prepared.get("posting_date"), 40):
        return "prepared body has no posting date"
    if prepared.get("set_posting_time") is not True:
        return "prepared body does not pin the posting time"
    if not _text(prepared.get("item_code"), MAX_TEXT_LENGTH):
        return "prepared body has no item"
    if not _text(prepared.get("uom"), MAX_TEXT_LENGTH):
        return "prepared body has no unit"
    qty = prepared.get("qty")
    if kind == ADJUST_KIND:
        if not _number(qty) or float(qty) < 0:
            return "prepared quantity is invalid"
    elif not _number(qty) or float(qty) <= 0:
        return "prepared quantity is invalid"
    batch_no = prepared.get("batch_no")
    if batch_no is not None and not _text(batch_no, MAX_TEXT_LENGTH):
        return "prepared batch is invalid"

    if kind == RECEIVE_KIND:
        for field in ("warehouse", "supplier"):
            if not _text(prepared.get(field), MAX_TEXT_LENGTH):
                return f"prepared body has no {field}"
        return None
    if kind == TRANSFER_KIND:
        for field in ("source_warehouse", "target_warehouse"):
            if not _text(prepared.get(field), MAX_TEXT_LENGTH):
                return f"prepared body has no {field}"
        if prepared.get("source_warehouse") == prepared.get("target_warehouse"):
            return "prepared target equals the source warehouse"
        return None
    if not _text(prepared.get("warehouse"), MAX_TEXT_LENGTH):
        return "prepared body has no warehouse"
    if not _text(prepared.get("expense_account"), MAX_TEXT_LENGTH):
        return "prepared body has no stock adjustment account"
    if not _text(prepared.get("reason"), MAX_REASON_LENGTH):
        return "prepared body has no reason"
    return None


def side_effect_problem(facts, expected):
    """Compare the closed-world re-read against the EXPECTED list.

    Nothing beyond the list is accepted: the submitted document, one Stock
    Ledger Entry per posted row, the Bin movement or counted quantity at each
    named warehouse and a reviewed GL row count.
    """

    if not isinstance(facts, dict) or not isinstance(expected, dict):
        return "side-effect facts unreadable"
    if facts.get("docstatus") != FINAL_DOCSTATUS:
        return "the stock document is not submitted"
    if facts.get("doctype") != expected.get("doctype"):
        return "the stock document type differs"
    sle_rows = facts.get("sle_rows")
    if not _count(sle_rows) or sle_rows != expected["sle_rows"]:
        return "stock ledger row count differs"
    gl_rows = facts.get("gl_rows")
    if not _count(gl_rows) or gl_rows not in (0, expected["gl_rows_max"]):
        return "gl row count is outside the reviewed outcomes"
    bins = facts.get("bins")
    if not isinstance(bins, dict):
        return "bin movement is unreadable"
    for warehouse, value in expected["bins"].items():
        if warehouse not in bins or not _number(bins[warehouse]):
            return f"bin movement for {warehouse} is unreadable"
        if abs(float(bins[warehouse]) - float(value)) > 1e-6:
            if expected["bin_mode"] == "set":
                return f"bin quantity for {warehouse} differs from the count"
            return f"bin movement for {warehouse} differs from the quantity"
    return None


# -- receipts, digests and acknowledgement ---------------------------------


def _framed(prefix, *values):
    message = bytearray(prefix)
    for value in values:
        data = value.encode("utf-8")
        message += b"|" + str(len(data)).encode("ascii") + b":" + data
    return bytes(message)


def _require_secret(secret, field="secret"):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise StockContractError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise StockContractError("receipt context is invalid")
    return site.strip(), actor.strip()


def encode_receipt(*, kind, reference, item_code, warehouse, qty, reason=""):
    kind = require_kind(kind)
    for value, maximum, field in (
        (reference, MAX_NAME_LENGTH, "reference"),
        (item_code, MAX_TEXT_LENGTH, "item_code"),
        (warehouse, MAX_TEXT_LENGTH, "warehouse"),
    ):
        if not is_valid_text(value, maximum):
            raise StockContractError(f"invalid receipt {field}")
    if not _number(qty) or float(qty) < 0:
        raise StockContractError("invalid receipt qty")
    if not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH:
        raise StockContractError("invalid receipt reason")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "reference": reference,
            "item_code": item_code,
            "warehouse": warehouse,
            "qty": float(qty),
            "reason": reason,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise StockContractError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return a :class:`StockReceipt` or raise ``StockContractError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise StockContractError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise StockContractError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise StockContractError("invalid stock receipt")
    if payload["codec"] != RECEIPT_CODEC or payload["version"] != RECEIPT_VERSION:
        raise StockContractError("invalid stock receipt")
    try:
        kind = require_kind(payload["kind"])
    except StockContractError as exc:
        raise StockContractError("invalid stock receipt") from exc
    reference = payload["reference"]
    item_code = payload["item_code"]
    warehouse = payload["warehouse"]
    qty = payload["qty"]
    reason = payload["reason"]
    if not is_valid_text(reference, MAX_NAME_LENGTH):
        raise StockContractError("invalid stock receipt")
    if not is_valid_text(item_code, MAX_TEXT_LENGTH):
        raise StockContractError("invalid stock receipt")
    if not is_valid_text(warehouse, MAX_TEXT_LENGTH):
        raise StockContractError("invalid stock receipt")
    if not _number(qty) or float(qty) < 0:
        raise StockContractError("invalid stock receipt")
    if not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH:
        raise StockContractError("invalid stock receipt")
    return StockReceipt(
        kind=kind,
        reference=reference,
        item_code=item_code,
        warehouse=warehouse,
        qty=float(qty),
        reason=reason,
    )


def operation_digest(secret, *, kind, operation_key, fields):
    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not isinstance(fields, dict):
        raise StockContractError("fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            raise StockContractError("field names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise StockContractError("field values must be text")
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
        b"clinic_ui_stock_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise StockContractError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_stock_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, dict):
        raise StockContractError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise StockContractError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise StockContractError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise StockContractError("ack token is invalid")
    return {
        "operation_key": require_operation_key(body["operation_key"]),
        "kind": require_kind(body["kind"]),
        "ack_token": body["ack_token"],
    }


def _text(value, maximum):
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if not stripped or len(stripped) > maximum:
        return ""
    return stripped


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value == value
    )


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _at_least(available, needed):
    return _number(available) and float(available) >= float(needed)
