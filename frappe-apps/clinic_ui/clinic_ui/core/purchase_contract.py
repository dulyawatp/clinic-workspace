"""Closed-world contract for the purchasing (PO draft/submit + receipt) family.

Design rulings encoded here (they are not re-decided):

* a draft purchase order is ``docstatus`` 0 and carries at least one itemized
  line with an item, a positive quantity, the item's stock unit and an explicit
  rate; the supplier, company, currency and the line units come from installed
  records, never from the client,
* submitting a draft moves it to ``docstatus`` 1 with the installed status
  ``To Receive and Bill``; the submit body carries no submit flag, because a
  reorder opens a draft and never auto-submits (R3:718),
* a PO-referenced receipt is a *distinct* fence kind (``purchase_receipt``): a
  submitted ``Purchase Receipt`` whose line names the purchase order and its
  line, with the supplier resolved from the order. Partial receipts reduce the
  pending balance; an over-receipt is refused pre-fence with the named line,
* the receipt pins ``set_posting_time`` and the server-owned posting date, and a
  batch-managed item creates its batch master in the same single operation,
* closing a purchase order (CSV:117) is designed but stays unwired this wave:
  there is no close kind and no close budget line. The receipt-in-progress
  refusal it needs is expressed here as a pre-fence decision for anything that
  would close the order,
* the closed-world re-read EXPECTS a count-based side-effect list: a draft adds
  nothing beyond the document, a submit changes status/docstatus only, and a
  receipt adds one Stock Ledger Entry per posted row, the Bin movement at the
  named warehouse, a reviewed GL row count and the PO received-quantity/status
  delta. Nothing beyond that list is accepted,
* every designed refusal is a pre-fence decision.

Installed field names are resolved from the site's own metadata; a missing
DocType or field refuses the write pre-fence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from clinic_ui.core import procedure_contract, write_budgets
from clinic_ui.core.text import is_valid_text

DRAFT_KIND = "purchase_order_draft"
SUBMIT_KIND = "purchase_order_submit"
RECEIPT_KIND = "purchase_receipt"
PURCHASE_KINDS = (DRAFT_KIND, SUBMIT_KIND, RECEIPT_KIND)

PURCHASE_ORDER_DOCTYPE = "Purchase Order"
PURCHASE_ORDER_ITEM_DOCTYPE = "Purchase Order Item"
PURCHASE_RECEIPT_DOCTYPE = "Purchase Receipt"
PURCHASE_RECEIPT_ITEM_DOCTYPE = "Purchase Receipt Item"
SUPPLIER_DOCTYPE = "Supplier"
BATCH_DOCTYPE = "Batch"
ITEM_DOCTYPE = "Item"
BIN_DOCTYPE = "Bin"

GUARDED_DOCTYPES = (
    PURCHASE_ORDER_DOCTYPE,
    PURCHASE_RECEIPT_DOCTYPE,
    BATCH_DOCTYPE,
)

DRAFT_DOCSTATUS = 0
SUBMITTED_DOCSTATUS = 1
# The installed status a submitted order carries until something is received
# and the status a fully received order carries until it is billed.
PO_OPEN_STATUS = "To Receive and Bill"
PO_BILL_STATUS = "To Bill"

# Reviewed executable boundary: the two buying controllers this family drives or
# creates, pinned by version, source sha256 (erpnext v16.52.1 image) and MRO,
# plus the site-global hooks already reviewed for the appointment family (the
# same pinned stack). Our own fence kinds are part of the pin.
REVIEWED_VERSIONS = {
    "frappe": "16.54.0",
    "erpnext": "16.52.1",
    "healthcare": "16.6.1",
}

REVIEWED_SOURCE_SHA256 = {
    "purchase_order": (
        "417c023169a2f65dfdee383e360395afa2e3fae38df434a69a495aef09aedb15"
    ),
    "purchase_receipt": (
        "d8b926f62edac07ef08c8a3e0af15bbf7a85fea649f9f2ad94d394dbfcf58170"
    ),
    "batch": "a5d4f8321e42387cf305bca91659c8b2eb4df49f7c5a5bb40ea40abe00a0ee04",
}

REVIEWED_MRO = {
    PURCHASE_ORDER_DOCTYPE: (
        "erpnext.buying.doctype.purchase_order.purchase_order.PurchaseOrder",
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

# A scheduler job whose dotted path names one of these modules would post
# purchasing documents outside this contract; none may exist for the family.
SCHEDULER_PURCHASE_TOKENS = (
    "purchase_order",
    "purchase_receipt",
)

OVERRECEIPT_MESSAGE = (
    "Over-receipt: quantity exceeds the purchase order pending quantity"
)
RECEIPT_IN_PROGRESS_MESSAGE = (
    "The purchase order cannot close while a receipt is in progress"
)

HTTP_REFUSED = 417
GL_ROWS_PER_ENTRY = 2

MAX_TEXT_LENGTH = 140
MAX_REASON_LENGTH = 280
MAX_ROWS = 20
MAX_QTY = 1_000_000.0
MAX_RATE = 1_000_000.0

# Installed-field preflight: logical name -> installed candidates per DocType.
INSTALLED_CANDIDATES = {
    PURCHASE_ORDER_DOCTYPE: {
        "supplier": ("supplier",),
        "company": ("company",),
        "currency": ("currency",),
        "conversion_rate": ("conversion_rate",),
        "transaction_date": ("transaction_date",),
        "items": ("items",),
        "docstatus": ("docstatus",),
        "status": ("status",),
    },
    PURCHASE_ORDER_ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "qty": ("qty",),
        "rate": ("rate",),
        "uom": ("uom",),
        "stock_uom": ("stock_uom",),
        "conversion_factor": ("conversion_factor",),
        "schedule_date": ("schedule_date",),
        "received_qty": ("received_qty",),
        "purchase_order": ("purchase_order",),
    },
    PURCHASE_RECEIPT_DOCTYPE: {
        "supplier": ("supplier",),
        "company": ("company",),
        "currency": ("currency",),
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
        "purchase_order": ("purchase_order",),
        "purchase_order_item": ("purchase_order_item",),
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
        "stock_uom": ("stock_uom",),
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
    "wpo1_",
)
MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_NAME_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024

RECEIPT_CODEC = "purchase"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {
        "codec",
        "version",
        "kind",
        "reference",
        "purchase_order",
        "item_code",
        "warehouse",
        "qty",
    }
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

DRAFT_KEYS = frozenset({"operation_key", "supplier", "items"})
DRAFT_LINE_KEYS = frozenset({"item_code", "qty", "rate"})
SUBMIT_KEYS = frozenset({"operation_key", "purchase_order"})
RECEIPT_BODY_KEYS = frozenset(
    {
        "operation_key",
        "purchase_order",
        "item_code",
        "qty",
        "warehouse",
        "batch_id",
        "batch_expiry",
    }
)


class PurchaseContractError(ValueError):
    """A purchasing write input or codec value is outside the accepted shape."""


class PurchaseTargetNotEligible(Exception):
    """The target changed between the pre-fence read and the held fence."""


class PurchaseBudgetExhausted(Exception):
    """The durable local test budget for this purchasing kind is used up."""


@dataclass(frozen=True)
class PurchaseReceipt:
    """Decoded terminal receipt for one committed purchasing operation."""

    kind: str
    reference: str
    purchase_order: str
    item_code: str
    warehouse: str
    qty: float


def require_kind(kind):
    if not isinstance(kind, str) or kind not in PURCHASE_KINDS:
        raise PurchaseContractError("kind is not a purchasing kind")
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
    for doctype in (PURCHASE_ORDER_DOCTYPE, PURCHASE_RECEIPT_DOCTYPE):
        if submittable.get(doctype) != 1:
            return f"{doctype} is not submittable"
    if snapshot.get("enabled_server_scripts") != 0:
        return "server scripts are enabled"
    doc_events = snapshot.get("doc_events")
    if not isinstance(doc_events, dict):
        return "doc_events unreadable"
    for doctype in GUARDED_DOCTYPES:
        if tuple(doc_events.get(doctype) or ()):
            return f"unreviewed doc_events for {doctype}"
    wildcard = snapshot.get("wildcard_doc_events")
    if not isinstance(wildcard, dict):
        return "wildcard doc_events unreadable"
    for event, reviewed in REVIEWED_WILDCARD_EVENTS.items():
        if tuple(wildcard.get(event) or ()) != reviewed:
            return f"wildcard doc_events drift for {event}"
    if tuple(snapshot.get("scheduler_purchase_jobs") or ()):
        return "a scheduler job touches the purchasing family"
    overrides = snapshot.get("class_overrides")
    if not isinstance(overrides, dict):
        return "class overrides unreadable"
    for doctype in GUARDED_DOCTYPES:
        if tuple(overrides.get(doctype) or ()):
            return f"{doctype} class is overridden or extended"
    if tuple(snapshot.get("fence_kinds") or ()) != PURCHASE_KINDS:
        return "fence kinds are not the reviewed purchasing set"
    return None


# -- strict request bodies -------------------------------------------------


def require_operation_key(value):
    if not isinstance(value, str):
        raise PurchaseContractError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise PurchaseContractError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise PurchaseContractError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise PurchaseContractError("operation_key is reserved")
    return value


def _require_body(body, allowed):
    if not isinstance(body, dict):
        raise PurchaseContractError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise PurchaseContractError("body keys must be strings")
    if set(body) - allowed:
        raise PurchaseContractError("unknown field")
    return body


def _require_text(value, field, maximum=MAX_TEXT_LENGTH):
    if not is_valid_text(value, maximum):
        raise PurchaseContractError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise PurchaseContractError(f"{field} is invalid")
    return stripped


def _optional_text(value, field, maximum=MAX_TEXT_LENGTH):
    if value is None:
        return None
    return _require_text(value, field, maximum)


def _require_number(value, field, *, minimum, maximum, exclusive_minimum):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PurchaseContractError(f"{field} is invalid")
    if value != value or value in (float("inf"), float("-inf")):
        raise PurchaseContractError(f"{field} is invalid")
    number = float(value)
    if exclusive_minimum and number <= minimum:
        raise PurchaseContractError(f"{field} is invalid")
    if not exclusive_minimum and number < minimum:
        raise PurchaseContractError(f"{field} is invalid")
    if number > maximum:
        raise PurchaseContractError(f"{field} is invalid")
    return number


def validate_draft(body):
    body = _require_body(body, DRAFT_KEYS)
    for required in ("operation_key", "supplier", "items"):
        if required not in body:
            raise PurchaseContractError(f"missing field {required}")
    raw_items = body["items"]
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise PurchaseContractError("items must be a non-empty list")
    if len(raw_items) > MAX_ROWS:
        raise PurchaseContractError("too many itemized lines")
    lines = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise PurchaseContractError("an itemized line must be a mapping")
        for key in raw:
            if not isinstance(key, str):
                raise PurchaseContractError("line keys must be strings")
        if set(raw) != DRAFT_LINE_KEYS:
            raise PurchaseContractError("an itemized line is invalid")
        lines.append(
            {
                "item_code": _require_text(raw["item_code"], "item_code"),
                "qty": _require_number(
                    raw["qty"],
                    "qty",
                    minimum=0.0,
                    maximum=MAX_QTY,
                    exclusive_minimum=True,
                ),
                "rate": _require_number(
                    raw["rate"],
                    "rate",
                    minimum=0.0,
                    maximum=MAX_RATE,
                    exclusive_minimum=True,
                ),
            }
        )
    return {
        "kind": DRAFT_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "supplier": _require_text(body["supplier"], "supplier"),
        "items": lines,
    }


def validate_submit(body):
    body = _require_body(body, SUBMIT_KEYS)
    for required in ("operation_key", "purchase_order"):
        if required not in body:
            raise PurchaseContractError(f"missing field {required}")
    return {
        "kind": SUBMIT_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "purchase_order": _require_text(body["purchase_order"], "purchase_order"),
    }


def validate_receipt(body):
    body = _require_body(body, RECEIPT_BODY_KEYS)
    for required in (
        "operation_key",
        "purchase_order",
        "item_code",
        "qty",
        "warehouse",
    ):
        if required not in body:
            raise PurchaseContractError(f"missing field {required}")
    return {
        "kind": RECEIPT_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "purchase_order": _require_text(body["purchase_order"], "purchase_order"),
        "item_code": _require_text(body["item_code"], "item_code"),
        "qty": _require_number(
            body["qty"],
            "qty",
            minimum=0.0,
            maximum=MAX_QTY,
            exclusive_minimum=True,
        ),
        "warehouse": _require_text(body["warehouse"], "warehouse"),
        "batch_id": _optional_text(body.get("batch_id"), "batch_id"),
        "batch_expiry": _optional_text(body.get("batch_expiry"), "batch_expiry"),
    }


# -- pre-fence decisions ---------------------------------------------------


def _money_problems(detail):
    problems = []
    if not _text(detail.get("company"), MAX_TEXT_LENGTH):
        problems.append(
            (HTTP_REFUSED, "company", "the site company is not readable")
        )
    if not _text(detail.get("currency"), MAX_TEXT_LENGTH):
        problems.append(
            (HTTP_REFUSED, "currency", "the site currency is not readable")
        )
    return problems


def _item_problem(detail, code, label):
    item = (detail.get("items") or {}).get(code)
    if not isinstance(item, dict) or not item.get("exists"):
        return (HTTP_REFUSED, label, f"item {code} does not exist")
    if not item.get("is_stock_item"):
        return (HTTP_REFUSED, label, f"item {code} is not a stock item")
    return None


def draft_problems(detail, payload):
    """Every pre-fence draft decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    problems = []
    if not detail.get("supplier_exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "supplier",
                f"supplier {payload['supplier']} does not exist",
            )
        )
    problems.extend(_money_problems(detail))
    lines = payload.get("items") or ()
    if not lines:
        problems.append(
            (HTTP_REFUSED, "items", "at least one itemized line is required")
        )
    for index, line in enumerate(lines, start=1):
        problem = _item_problem(
            detail, line["item_code"], f"items[{index}].item_code"
        )
        if problem:
            problems.append(problem)
            continue
        item = (detail.get("items") or {}).get(line["item_code"]) or {}
        if not _text(item.get("stock_uom"), MAX_TEXT_LENGTH):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"items[{index}].uom",
                    f"item {line['item_code']} has no stock unit",
                )
            )
        if not _positive(line.get("qty")):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"items[{index}].qty",
                    "the ordered quantity must be positive",
                )
            )
        if not _positive(line.get("rate")):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"items[{index}].rate",
                    "the ordered rate must be positive",
                )
            )
    return tuple(problems)


def submit_problems(detail, payload):
    """Every pre-fence submit decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    order = detail.get("purchase_order")
    if not isinstance(order, dict) or not order.get("exists"):
        return (
            (
                HTTP_REFUSED,
                "purchase_order",
                f"purchase order {payload['purchase_order']} does not exist",
            ),
        )
    problems = []
    if order.get("docstatus") != DRAFT_DOCSTATUS:
        problems.append(
            (
                HTTP_REFUSED,
                "purchase_order",
                f"purchase order {payload['purchase_order']} is not a draft",
            )
        )
    if not detail.get("supplier_exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "supplier",
                "the purchase order supplier does not exist",
            )
        )
    problems.extend(_money_problems(detail))
    lines = detail.get("lines") or ()
    if not lines:
        problems.append(
            (HTTP_REFUSED, "items", "the draft has no itemized line")
        )
    for index, line in enumerate(lines, start=1):
        label = f"lines[{index}]"
        if not _text(line.get("item_code"), MAX_TEXT_LENGTH):
            problems.append(
                (HTTP_REFUSED, label, "the draft line has no item")
            )
        if not _positive(line.get("qty")):
            problems.append(
                (HTTP_REFUSED, label, "the draft line has no quantity")
            )
        if not _positive(line.get("rate")):
            problems.append((HTTP_REFUSED, label, "the draft line has no rate"))
        if not _text(line.get("uom"), MAX_TEXT_LENGTH):
            problems.append((HTTP_REFUSED, label, "the draft line has no unit"))
    return tuple(problems)


def _batch_problems(detail, payload):
    """Batch decisions for a batch-managed item, with a named line each."""

    item = detail.get("item") or {}
    code = payload["item_code"]
    batch_id = payload.get("batch_id")
    problems = []
    if not item.get("has_batch_no"):
        if batch_id:
            problems.append(
                (HTTP_REFUSED, "batch_no", f"item {code} does not use batches")
            )
        return problems
    if not batch_id:
        problems.append(
            (HTTP_REFUSED, "batch_no", f"batch number is required for {code}")
        )
        return problems

    batch = detail.get("batch")
    if not isinstance(batch, dict) or not batch.get("exists"):
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


def receipt_problems(detail, payload):
    """Every pre-fence PO-referenced receipt decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    order = detail.get("purchase_order")
    if not isinstance(order, dict) or not order.get("exists"):
        return (
            (
                HTTP_REFUSED,
                "purchase_order",
                f"purchase order {payload['purchase_order']} does not exist",
            ),
        )
    problems = []
    if order.get("docstatus") != SUBMITTED_DOCSTATUS:
        problems.append(
            (
                HTTP_REFUSED,
                "purchase_order",
                f"purchase order {payload['purchase_order']} is not submitted",
            )
        )
    if not detail.get("supplier_exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "supplier",
                "the purchase order supplier does not exist",
            )
        )
    problems.extend(_money_problems(detail))

    item = detail.get("item")
    code = payload["item_code"]
    if not isinstance(item, dict) or not item.get("exists"):
        problems.append(
            (HTTP_REFUSED, "item_code", f"item {code} does not exist")
        )
    else:
        if not item.get("is_stock_item"):
            problems.append(
                (HTTP_REFUSED, "item_code", f"item {code} is not a stock item")
            )
        if not _text(item.get("stock_uom"), MAX_TEXT_LENGTH):
            problems.append(
                (HTTP_REFUSED, "item_code", f"item {code} has no stock unit")
            )
    warehouse = detail.get("warehouse")
    if not isinstance(warehouse, dict) or not warehouse.get("exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "warehouse",
                f"warehouse {payload['warehouse']} does not exist",
            )
        )
    problems.extend(_batch_problems(detail, payload))

    pending = detail.get("po_pending_qty")
    if not detail.get("po_item") or not _number(pending):
        problems.append(
            (
                HTTP_REFUSED,
                "purchase_order",
                f"purchase order {payload['purchase_order']} has no pending "
                f"quantity for {code}",
            )
        )
    elif float(pending) <= 0:
        problems.append(
            (
                HTTP_REFUSED,
                "qty",
                f"purchase order {payload['purchase_order']} is fully received "
                f"for {code}",
            )
        )
    elif payload["qty"] > float(pending) + 1e-9:
        problems.append((HTTP_REFUSED, "qty", OVERRECEIPT_MESSAGE))
    return tuple(problems)


def receipt_in_progress_problem(detail):
    """The designed close-while-receipting refusal (close itself is unwired).

    Closing a purchase order is designed (CSV:117) but has no budget line in
    this wave, so no close kind exists. The refusal it needs is still a
    contract decision: an order with a receipt in progress may not close.
    """

    if not isinstance(detail, dict):
        return (HTTP_REFUSED, "detail", "target state is unreadable")
    if detail.get("receipt_in_progress") is True:
        return (HTTP_REFUSED, "purchase_order", RECEIPT_IN_PROGRESS_MESSAGE)
    return None


# -- EXPECTED side effects -------------------------------------------------


def expected_draft(detail, payload):
    return {
        "doctype": PURCHASE_ORDER_DOCTYPE,
        "kind": DRAFT_KIND,
        "docstatus": DRAFT_DOCSTATUS,
        "sle_rows": 0,
        "gl_rows_max": 0,
        "bins": {},
    }


def expected_submit(detail, payload):
    return {
        "doctype": PURCHASE_ORDER_DOCTYPE,
        "kind": SUBMIT_KIND,
        "docstatus": SUBMITTED_DOCSTATUS,
        "status": PO_OPEN_STATUS,
        "sle_rows": 0,
        "gl_rows_max": 0,
        "bins": {},
    }


def expected_receipt(detail, payload):
    pending = detail.get("po_pending_qty")
    remaining = float(pending) - float(payload["qty"])
    return {
        "doctype": PURCHASE_RECEIPT_DOCTYPE,
        "kind": RECEIPT_KIND,
        "docstatus": SUBMITTED_DOCSTATUS,
        "purchase_order": payload["purchase_order"],
        "item_code": payload["item_code"],
        "sle_rows": 1,
        "bins": {payload["warehouse"]: float(payload["qty"])},
        "gl_rows_max": GL_ROWS_PER_ENTRY,
        "po_received_delta": float(payload["qty"]),
        "po_status": PO_BILL_STATUS if remaining <= 1e-9 else PO_OPEN_STATUS,
    }


def prepared_problem(kind, prepared):
    """Closed-world check of the mutation body a gateway will execute.

    The prepared body is the server's own resolved document payload: it must
    carry the company and currency resolved from installed state (never client
    input), the supplier, the server-owned date and the kind's own route fields.
    A missing piece is refused before the fence, so a gateway drift can never
    turn into a post-dispatch KeyError.
    """

    if kind not in PURCHASE_KINDS:
        return "unknown purchasing kind"
    if not isinstance(prepared, dict):
        return "prepared body unreadable"
    if not _text(prepared.get("company"), MAX_TEXT_LENGTH):
        return "prepared body has no company"
    if not _text(prepared.get("currency"), MAX_TEXT_LENGTH):
        return "prepared body has no currency"

    if kind == DRAFT_KIND:
        if not _text(prepared.get("supplier"), MAX_TEXT_LENGTH):
            return "prepared body has no supplier"
        if not _text(prepared.get("transaction_date"), 40):
            return "prepared body has no transaction date"
        lines = prepared.get("items")
        if not isinstance(lines, (list, tuple)) or not lines:
            return "prepared body has no itemized line"
        for line in lines:
            if not isinstance(line, dict):
                return "prepared line is unreadable"
            if not _text(line.get("item_code"), MAX_TEXT_LENGTH):
                return "prepared line has no item"
            if not _text(line.get("uom"), MAX_TEXT_LENGTH):
                return "prepared line has no unit"
            if not _text(line.get("schedule_date"), 40):
                return "prepared line has no schedule date"
            if not _positive(line.get("qty")):
                return "prepared line quantity is invalid"
            if not _positive(line.get("rate")):
                return "prepared line rate is invalid"
        return None

    if kind == SUBMIT_KIND:
        if not _text(prepared.get("purchase_order"), MAX_NAME_LENGTH):
            return "prepared body has no purchase order"
        if prepared.get("docstatus") != DRAFT_DOCSTATUS:
            return "prepared draft is not unsubmitted"
        return None

    if not _text(prepared.get("purchase_order"), MAX_NAME_LENGTH):
        return "prepared body has no purchase order"
    if not _text(prepared.get("supplier"), MAX_TEXT_LENGTH):
        return "prepared body has no supplier"
    if not _text(prepared.get("item_code"), MAX_TEXT_LENGTH):
        return "prepared body has no item"
    if not _text(prepared.get("uom"), MAX_TEXT_LENGTH):
        return "prepared body has no unit"
    if not _text(prepared.get("warehouse"), MAX_TEXT_LENGTH):
        return "prepared body has no warehouse"
    if not _text(prepared.get("posting_date"), 40):
        return "prepared body has no posting date"
    if prepared.get("set_posting_time") is not True:
        return "prepared body does not pin the posting time"
    if not _positive(prepared.get("qty")):
        return "prepared quantity is invalid"
    batch_no = prepared.get("batch_no")
    if batch_no is not None and not _text(batch_no, MAX_TEXT_LENGTH):
        return "prepared batch is invalid"
    return None


def side_effect_problem(facts, expected):
    """Compare the closed-world re-read against the EXPECTED list.

    Nothing beyond the list is accepted: the posted document, one Stock Ledger
    Entry per posted row, the Bin movement at the named warehouse, a reviewed GL
    row count and (for a receipt) the purchase order's received-quantity and
    status delta.
    """

    if not isinstance(facts, dict) or not isinstance(expected, dict):
        return "side-effect facts unreadable"
    if facts.get("docstatus") != expected.get("docstatus"):
        return "the purchasing document has the wrong docstatus"
    if facts.get("doctype") != expected.get("doctype"):
        return "the purchasing document type differs"
    status = expected.get("status")
    if status is not None and facts.get("status") != status:
        return "the purchase order status differs"
    sle_rows = facts.get("sle_rows")
    if _count(sle_rows) is False or sle_rows != expected["sle_rows"]:
        return "stock ledger row count differs"
    gl_rows = facts.get("gl_rows")
    if _count(gl_rows) is False or gl_rows not in (0, expected["gl_rows_max"]):
        return "gl row count is outside the reviewed outcomes"
    bins = facts.get("bins")
    if expected.get("bins"):
        if not isinstance(bins, dict):
            return "bin movement is unreadable"
        for warehouse, value in expected["bins"].items():
            if warehouse not in bins or not _number(bins[warehouse]):
                return f"bin movement for {warehouse} is unreadable"
            if abs(float(bins[warehouse]) - float(value)) > 1e-6:
                return f"bin movement for {warehouse} differs from the quantity"
    elif bins:
        return "an unexpected bin movement was recorded"
    if "po_received_delta" in expected:
        delta = facts.get("po_received_delta")
        if not _number(delta):
            return "the purchase order received quantity is unreadable"
        if abs(float(delta) - float(expected["po_received_delta"])) > 1e-6:
            return "the purchase order received quantity differs"
        if facts.get("po_status") != expected.get("po_status"):
            return "the purchase order status after receipt differs"
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
        raise PurchaseContractError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise PurchaseContractError("receipt context is invalid")
    return site.strip(), actor.strip()


def encode_receipt(
    *, kind, reference, purchase_order, item_code="", warehouse="", qty=0.0
):
    kind = require_kind(kind)
    for value, maximum, field in (
        (reference, MAX_NAME_LENGTH, "reference"),
        (purchase_order, MAX_NAME_LENGTH, "purchase_order"),
    ):
        if not is_valid_text(value, maximum):
            raise PurchaseContractError(f"invalid receipt {field}")
    if kind == RECEIPT_KIND:
        for value, field in ((item_code, "item_code"), (warehouse, "warehouse")):
            if not is_valid_text(value, MAX_TEXT_LENGTH):
                raise PurchaseContractError(f"invalid receipt {field}")
        if not _number(qty) or float(qty) <= 0:
            raise PurchaseContractError("invalid receipt qty")
    else:
        item_code = ""
        warehouse = ""
        qty = 0.0
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "reference": reference,
            "purchase_order": purchase_order,
            "item_code": item_code,
            "warehouse": warehouse,
            "qty": float(qty),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise PurchaseContractError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return a :class:`PurchaseReceipt` or raise ``PurchaseContractError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise PurchaseContractError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise PurchaseContractError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise PurchaseContractError("invalid purchase receipt")
    if payload["codec"] != RECEIPT_CODEC or payload["version"] != RECEIPT_VERSION:
        raise PurchaseContractError("invalid purchase receipt")
    try:
        kind = require_kind(payload["kind"])
    except PurchaseContractError as exc:
        raise PurchaseContractError("invalid purchase receipt") from exc
    reference = payload["reference"]
    purchase_order = payload["purchase_order"]
    item_code = payload["item_code"]
    warehouse = payload["warehouse"]
    qty = payload["qty"]
    if not is_valid_text(reference, MAX_NAME_LENGTH):
        raise PurchaseContractError("invalid purchase receipt")
    if not is_valid_text(purchase_order, MAX_NAME_LENGTH):
        raise PurchaseContractError("invalid purchase receipt")
    if kind == RECEIPT_KIND:
        if not is_valid_text(item_code, MAX_TEXT_LENGTH):
            raise PurchaseContractError("invalid purchase receipt")
        if not is_valid_text(warehouse, MAX_TEXT_LENGTH):
            raise PurchaseContractError("invalid purchase receipt")
        if not _number(qty) or float(qty) <= 0:
            raise PurchaseContractError("invalid purchase receipt")
    elif item_code != "" or warehouse != "" or float(qty) != 0.0:
        raise PurchaseContractError("invalid purchase receipt")
    return PurchaseReceipt(
        kind=kind,
        reference=reference,
        purchase_order=purchase_order,
        item_code=item_code,
        warehouse=warehouse,
        qty=float(qty),
    )


def operation_digest(secret, *, kind, operation_key, fields):
    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not isinstance(fields, dict):
        raise PurchaseContractError("fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            raise PurchaseContractError("field names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise PurchaseContractError("field values must be text")
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
        b"clinic_ui_purchase_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise PurchaseContractError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_purchase_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, dict):
        raise PurchaseContractError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise PurchaseContractError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise PurchaseContractError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise PurchaseContractError("ack token is invalid")
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


def _positive(value):
    return _number(value) and float(value) > 0


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
