"""Closed-world contract for the finance (invoice submit + payment receive) family.

Design rulings encoded here (they are not re-decided):

* an invoice is created and submitted as **one** fenced mutation unit
  (``invoice_submit``): the installed ``Sales Invoice`` is inserted, then
  submitted, so it lands ``docstatus`` 1 with the installed status ``Unpaid``.
  The approved write budget has exactly one invoice unit, so a separate draft
  write would consume it and no submitted invoice could ever exist; the
  "save draft" surface is therefore the server-side preview the UI shows before
  the single submit,
* ``update_stock`` is pinned to the integer ``0`` and anything else is refused
  (fortune rule: the stock was already issued by the procedure/pharmacy path,
  so ``1`` would deduct twice),
* ``due_date`` is required and explicit — no ``+7 days`` is ever invented — and
  the company, currency, the selling price list, the receivable/income accounts
  and the cost center are resolved from installed records, never from the
  client. Rates come from the installed selling price list, so the client's own
  arithmetic is never truth; the posted totals are re-read and compared,
* taxes stay empty: the sit has the doctype but no template and no policy
  record (D-11 open), so nothing is invented. A discount rides the child
  ``deductions`` line only when the installed metadata proves that child **and**
  the site-config permission flag is on (CSV:123's CAS + permission condition,
  qualifier folded to configuration); otherwise the discount is refused
  pre-fence and the invoice lands without a discount path. No rate is ever
  invented,
* a customer must already exist (CSV:121: the UI never creates a ``Customer``),
  and the same charge may not appear twice on one invoice (CSV:122),
* a payment is a ``Payment Entry`` of type ``Receive`` whose ``references[]``
  allocation sums to the paid amount (CSV:128). Modes are ``Cash`` and
  ``Credit Card`` only (R3:472 overrides SPEC's ``Bank Transfer``); the other
  modes are refused by name. A draft/cancelled/paid invoice, an over-allocation,
  a repeat payment and a mode without a company account are all refused
  pre-fence,
* refund is **cut** (D-13 open, no SPEC section): there is no refund kind and
  ``write.payment.refund`` stays read-only. Void/amend is **cut** too: no clean
  installed lifecycle path was reviewed, so ``write.invoice.void_amend`` stays
  read-only and no control is wired,
* the closed-world re-read EXPECTS a count-based list: an invoice adds one GL
  row per line plus the debtor row (Debtors Dr / Sales Cr), no Stock Ledger
  Entry (``update_stock`` 0) and one Payment Ledger row; a payment adds two GL
  rows, one Payment Ledger row, the invoice's outstanding delta and the
  ``Unpaid`` → ``Paid`` status when it is fully allocated. Nothing beyond that
  list is accepted,
* every designed refusal is a pre-fence decision.

Installed field names are resolved from the site's own metadata; a missing
DocType or required field refuses the write pre-fence. The two controllers are
driven only through their public insert/submit lifecycle, so the reviewed
boundary is the pinned versions and controller source hashes, the controller
MRO, the submittable flags, the reviewed class overrides and doc events, and
our own fence kinds.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass

from clinic_ui.core import procedure_contract, write_budgets
from clinic_ui.core.text import is_valid_text

INVOICE_KIND = "invoice_submit"
PAYMENT_KIND = "payment_receive"
FINANCE_KINDS = (INVOICE_KIND, PAYMENT_KIND)

INVOICE_DOCTYPE = "Sales Invoice"
INVOICE_ITEM_DOCTYPE = "Sales Invoice Item"
DEDUCTION_DOCTYPE = "Sales Invoice Deduction"
PAYMENT_DOCTYPE = "Payment Entry"
PAYMENT_REFERENCE_DOCTYPE = "Payment Entry Reference"
CUSTOMER_DOCTYPE = "Customer"
COMPANY_DOCTYPE = "Company"
ACCOUNT_DOCTYPE = "Account"
ITEM_DOCTYPE = "Item"
ITEM_DEFAULT_DOCTYPE = "Item Default"
PRICE_LIST_DOCTYPE = "Price List"
MODE_OF_PAYMENT_DOCTYPE = "Mode of Payment"
MODE_OF_PAYMENT_ACCOUNT_DOCTYPE = "Mode of Payment Account"
GL_ENTRY_DOCTYPE = "GL Entry"
PAYMENT_LEDGER_DOCTYPE = "Payment Ledger Entry"
STOCK_LEDGER_DOCTYPE = "Stock Ledger Entry"

GUARDED_DOCTYPES = (INVOICE_DOCTYPE, PAYMENT_DOCTYPE)

DRAFT_DOCSTATUS = 0
SUBMITTED_DOCSTATUS = 1
CANCELLED_DOCSTATUS = 2

INVOICE_UNPAID_STATUS = "Unpaid"
INVOICE_PAID_STATUS = "Paid"
INVOICE_CANCELLED_STATUS = "Cancelled"
INVOICE_DRAFT_STATUS = "Draft"

# R3:472 overrides SPEC's Bank Transfer: these two modes and no other.
ACCEPTED_PAYMENT_MODES = ("Cash", "Credit Card")
CARD_PAYMENT_MODE = "Credit Card"
REFUSED_PAYMENT_MODES = ("Bank Transfer",)

# The fortune rule: a submitted invoice must never move stock.
PINNED_UPDATE_STOCK = 0

# Reviewed executable boundary: the two accounting controllers this family
# drives, pinned by version (the same image as the other families), source
# sha256 and MRO. The two hashes were derived from the erpnext v16.52.1 tag and
# cross-validated by re-deriving the already-reviewed stock/purchase pins
# (purchase_receipt, stock_entry, purchase_order, batch) from the same tag
# byte-identically; the on-site preflight re-hashes the installed files and
# fails closed on drift.
REVIEWED_VERSIONS = {
    "frappe": "16.54.0",
    "erpnext": "16.52.1",
    "healthcare": "16.6.1",
}

REVIEWED_SOURCE_SHA256 = {
    "sales_invoice": (
        "d429600bec1abb62bd45c28a5a198e01d3ccb72841340f7db4b3693e2bc4343f"
    ),
    "payment_entry": (
        "f844d725fa548b4f126503c0c8f231d1040440152819925f2dd7a8656a194c2f"
    ),
}

# The installed controller hierarchies (erpnext+healthcare class objects as the
# collector snapshots them, not bare erpnext source): healthcare extends Sales
# Invoice with ExtendedSalesInvoice/SalesInvoiceMixin, and erpnext inserts
# StatusUpdater between TransactionBase and Document.
REVIEWED_MRO = {
    INVOICE_DOCTYPE: (
        "erpnext.accounts.doctype.sales_invoice.sales_invoice."
        "ExtendedSalesInvoice",
        "healthcare.healthcare.custom_doctype.sales_invoice.SalesInvoiceMixin",
        "erpnext.accounts.doctype.sales_invoice.sales_invoice.SalesInvoice",
        "erpnext.controllers.selling_controller.SellingController",
        "erpnext.controllers.stock_controller.StockController",
        "erpnext.controllers.accounts_controller.AccountsController",
        "erpnext.utilities.transaction_base.TransactionBase",
        "erpnext.controllers.status_updater.StatusUpdater",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
    PAYMENT_DOCTYPE: (
        "erpnext.accounts.doctype.payment_entry.payment_entry.PaymentEntry",
        "erpnext.controllers.accounts_controller.AccountsController",
        "erpnext.utilities.transaction_base.TransactionBase",
        "erpnext.controllers.status_updater.StatusUpdater",
        "frappe.model.document.Document",
        "frappe.model.document._DocumentBase",
        "builtins.object",
    ),
}

# Reviewed per-doctype class overrides: ``override_doctype_class`` plus
# ``extend_doctype_class`` as the collector aggregates them. The installed
# healthcare app mixes its SalesInvoiceMixin into the invoice controller (the
# same extension the pinned MRO above records); Payment Entry has none. The
# comparison is closed-world: any added, removed or foreign override fails
# closed.
REVIEWED_CLASS_OVERRIDES = {
    INVOICE_DOCTYPE: (
        "healthcare.healthcare.custom_doctype.sales_invoice.SalesInvoiceMixin",
    ),
    PAYMENT_DOCTYPE: (),
}

# Site-global wildcard hooks: identical to the reviewed appointment pin on the
# same stack, so they are read from that module instead of a second list.
REVIEWED_WILDCARD_EVENTS = procedure_contract.REVIEWED_WILDCARD_EVENTS

# Reviewed per-doctype doc_events: the exact event->handler set the pinned stack
# registers on the two accounting controllers. These are erpnext's regional
# hooks and healthcare's invoice/payment hooks -- reviewed-builtin, not ours.
# Our app registers no doc_events on either doctype; anything else fails closed.
REVIEWED_DOC_EVENTS = {
    INVOICE_DOCTYPE: {
        "on_cancel": (
            "erpnext.regional.italy.utils.sales_invoice_on_cancel",
            "healthcare.healthcare.utils.manage_invoice_submit_cancel",
        ),
        "on_submit": (
            "erpnext.regional.italy.utils.sales_invoice_on_submit",
            "healthcare.healthcare.utils.manage_invoice_submit_cancel",
        ),
        "on_trash": ("erpnext.regional.check_deletion_permission",),
        "validate": ("healthcare.healthcare.utils.manage_invoice_validate",),
    },
    PAYMENT_DOCTYPE: {
        "on_cancel": (
            "healthcare.healthcare.custom_doctype.payment_entry."
            "manage_payment_entry_submit_cancel",
        ),
        "on_submit": (
            "healthcare.healthcare.custom_doctype.payment_entry."
            "manage_payment_entry_submit_cancel",
        ),
        "on_trash": ("erpnext.regional.check_deletion_permission",),
        "validate": (
            "healthcare.healthcare.doctype.insurance_claim.insurance_claim."
            "validate_payment_entry_and_set_claim_fields",
        ),
    },
}

# A scheduler job whose dotted path names one of these modules would post
# accounting documents outside this contract; none may exist for the family.
SCHEDULER_FINANCE_TOKENS = ("sales_invoice", "payment_entry")

# Installed-field preflight: logical name -> installed candidates per DocType.
INSTALLED_CANDIDATES = {
    INVOICE_DOCTYPE: {
        "customer": ("customer",),
        "company": ("company",),
        "currency": ("currency",),
        "conversion_rate": ("conversion_rate",),
        "posting_date": ("posting_date",),
        "due_date": ("due_date",),
        "selling_price_list": ("selling_price_list",),
        "update_stock": ("update_stock",),
        "debit_to": ("debit_to",),
        "cost_center": ("cost_center",),
        "taxes": ("taxes",),
        "items": ("items",),
        "docstatus": ("docstatus",),
        "status": ("status",),
        "grand_total": ("grand_total",),
        "net_total": ("net_total",),
        "outstanding_amount": ("outstanding_amount",),
    },
    INVOICE_ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "qty": ("qty",),
        "rate": ("rate",),
        "amount": ("amount",),
        "uom": ("uom",),
        "stock_uom": ("stock_uom",),
        "income_account": ("income_account",),
        "cost_center": ("cost_center",),
        "price_list_rate": ("price_list_rate",),
    },
    DEDUCTION_DOCTYPE: {
        # Optional: only present on installations that carry the child.
        "account": ("account",),
        "cost_center": ("cost_center",),
        "amount": ("amount",),
    },
    PAYMENT_DOCTYPE: {
        "payment_type": ("payment_type",),
        "party_type": ("party_type",),
        "party": ("party",),
        "company": ("company",),
        "currency": ("currency",),
        "paid_from": ("paid_from",),
        "paid_to": ("paid_to",),
        "paid_amount": ("paid_amount",),
        "received_amount": ("received_amount",),
        "source_exchange_rate": ("source_exchange_rate",),
        "target_exchange_rate": ("target_exchange_rate",),
        "posting_date": ("posting_date",),
        "mode_of_payment": ("mode_of_payment",),
        "reference_no": ("reference_no",),
        "reference_date": ("reference_date",),
        "references": ("references",),
        "docstatus": ("docstatus",),
        "status": ("status",),
    },
    PAYMENT_REFERENCE_DOCTYPE: {
        "reference_doctype": ("reference_doctype",),
        "reference_name": ("reference_name",),
        "allocated_amount": ("allocated_amount",),
        "outstanding_amount": ("outstanding_amount",),
        "total_amount": ("total_amount",),
    },
    CUSTOMER_DOCTYPE: {
        "customer_name": ("customer_name",),
        "disabled": ("disabled",),
    },
    COMPANY_DOCTYPE: {
        "default_currency": ("default_currency",),
        "default_receivable_account": ("default_receivable_account",),
        "default_income_account": ("default_income_account",),
        "cost_center": ("cost_center",),
    },
    ACCOUNT_DOCTYPE: {
        "company": ("company",),
        "is_group": ("is_group",),
        "account_type": ("account_type",),
    },
    ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "is_sales_item": ("is_sales_item",),
        "stock_uom": ("stock_uom",),
    },
    ITEM_DEFAULT_DOCTYPE: {
        "parent": ("parent",),
        "company": ("company",),
        "income_account": ("income_account",),
        "cost_center": ("cost_center",),
    },
    PRICE_LIST_DOCTYPE: {
        "selling": ("selling",),
        "enabled": ("enabled",),
        "currency": ("currency",),
    },
    MODE_OF_PAYMENT_DOCTYPE: {
        "enabled": ("enabled",),
        "accounts": ("accounts",),
    },
    MODE_OF_PAYMENT_ACCOUNT_DOCTYPE: {
        "company": ("company",),
        "default_account": ("default_account",),
    },
    GL_ENTRY_DOCTYPE: {
        "voucher_type": ("voucher_type",),
        "voucher_no": ("voucher_no",),
        "account": ("account",),
        "debit": ("debit",),
        "credit": ("credit",),
        "is_cancelled": ("is_cancelled",),
    },
    PAYMENT_LEDGER_DOCTYPE: {
        "voucher_type": ("voucher_type",),
        "voucher_no": ("voucher_no",),
        "amount": ("amount",),
        "account_type": ("account_type",),
        "delinked": ("delinked",),
    },
    STOCK_LEDGER_DOCTYPE: {
        "voucher_type": ("voucher_type",),
        "voucher_no": ("voucher_no",),
    },
}

# The discount child is optional for the family: a missing child cuts only the
# discount sub-capability (recorded fallback), never the invoice write.
OPTIONAL_DOCTYPES = (DEDUCTION_DOCTYPE,)
DISCOUNT_CHILD_FIELDS = ("account", "cost_center", "amount")
# The child only rides the installed parent link: the gateway resolves the
# Sales Invoice table fieldname and its child target, and the discount path
# refuses unless both match.
DEDUCTION_LINK_FIELD = "deductions"
DEDUCTION_LINK_KEY = "deduction_parent_link"
DEDUCTION_TABLE_TYPES = ("Table", "Table MultiSelect")

DISCOUNT_NOT_ENABLED_MESSAGE = "discounts are not enabled on this deployment"
DISCOUNT_CHILD_MISSING_MESSAGE = (
    "the installed Sales Invoice has no deductions child"
)

HTTP_REFUSED = 417
# Reviewed GL/Payment-Ledger shape of the two operations.
GL_ROWS_PER_INVOICE = 1
GL_ROWS_PER_INVOICE_LINE = 1
GL_ROWS_PER_PAYMENT = 2
PAYMENT_LEDGER_ROWS_PER_ALLOCATION = 1

MAX_TEXT_LENGTH = 140
MAX_ROWS = 20
MAX_QTY = 1_000_000.0
MAX_AMOUNT = 1_000_000.0
MONEY_TOLERANCE = 0.005

OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
RESERVED_OPERATION_KEY_PREFIXES = (
    "clinic-ui-",
    "wpa1_",
    "wsa1_",
    "wse1_",
    "wsp1_",
    "wpo1_",
    "wfi1_",
)
MIN_OPERATION_KEY_LENGTH = 16
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_NAME_LENGTH = 140
MAX_RECEIPT_LENGTH = 1024
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

RECEIPT_CODEC = "finance"
RECEIPT_VERSION = 1
RECEIPT_KEYS = frozenset(
    {
        "codec",
        "version",
        "kind",
        "reference",
        "invoice",
        "customer",
        "amount",
        "mode_of_payment",
        "status",
    }
)

ACK_KEYS = frozenset({"operation_key", "kind", "ack_token"})
ACK_TOKEN_LENGTH = 64
ACK_TOKEN_RE = re.compile(r"^[0-9a-f]{64}$")

INVOICE_KEYS = frozenset(
    {
        "operation_key",
        "customer",
        "selling_price_list",
        "due_date",
        "items",
        "discount",
    }
)
INVOICE_LINE_KEYS = frozenset({"item_code", "qty"})
PAYMENT_KEYS = frozenset(
    {
        "operation_key",
        "invoice",
        "mode_of_payment",
        "amount",
        "allocations",
        "reference_no",
    }
)
ALLOCATION_KEYS = frozenset({"invoice", "amount"})

CARD_FIELDS = ("card_number", "cvv", "pan", "card_cvv", "card_expiry")
CLIENT_TOTAL_FIELDS = ("grand_total", "net_total", "total_taxes_and_charges")


class FinanceContractError(ValueError):
    """A finance write input or codec value is outside the accepted shape."""


class FinanceTargetNotEligible(Exception):
    """The target changed between the pre-fence read and the held fence."""


class FinanceBudgetExhausted(Exception):
    """The durable local test budget for this finance kind is used up."""


@dataclass(frozen=True)
class FinanceReceipt:
    """Decoded terminal receipt for one committed finance operation."""

    kind: str
    reference: str
    invoice: str
    customer: str
    amount: float
    mode_of_payment: str
    status: str


def require_kind(kind):
    if not isinstance(kind, str) or kind not in FINANCE_KINDS:
        raise FinanceContractError("kind is not a finance kind")
    return kind


def budget(kind):
    return write_budgets.budget_for(require_kind(kind))


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete."""

    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    for doctype, fields in INSTALLED_CANDIDATES.items():
        if doctype in OPTIONAL_DOCTYPES:
            continue
        installed = meta_fields.get(doctype)
        if not isinstance(installed, (set, frozenset, tuple, list)):
            return f"missing DocType {doctype}"
        installed = set(installed)
        for logical, candidates in fields.items():
            if not any(candidate in installed for candidate in candidates):
                return f"missing field {doctype}.{logical}"
    return None


def discount_problem(meta_fields, *, enabled):
    """Return why the discount path is unavailable, or ``None`` when available.

    The path needs two independent things: the site-config permission flag
    (CSV:123's qualifier, folded to configuration) and the installed child with
    its reviewed fields. Either one missing cuts only the discount
    sub-capability; the invoice write itself is unaffected.
    """

    if enabled is not True:
        return DISCOUNT_NOT_ENABLED_MESSAGE
    if not isinstance(meta_fields, dict):
        return "installed metadata unreadable"
    if meta_fields.get(DEDUCTION_LINK_KEY) is not True:
        return DISCOUNT_CHILD_MISSING_MESSAGE
    installed = meta_fields.get(DEDUCTION_DOCTYPE)
    if not isinstance(installed, (set, frozenset, tuple, list)) or not installed:
        return DISCOUNT_CHILD_MISSING_MESSAGE
    installed = set(installed)
    for field in DISCOUNT_CHILD_FIELDS:
        if field not in installed:
            return DISCOUNT_CHILD_MISSING_MESSAGE
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
    for doctype, expected in REVIEWED_MRO.items():
        if tuple(mro.get(doctype) or ()) != expected:
            return f"controller MRO drift for {doctype}"
    submittable = snapshot.get("submittable")
    if not isinstance(submittable, dict):
        return "submittable flags unreadable"
    for doctype in GUARDED_DOCTYPES:
        if submittable.get(doctype) != 1:
            return f"{doctype} is not submittable"
    if snapshot.get("enabled_server_scripts") != 0:
        return "an enabled Server Script exists"
    events = snapshot.get("doc_events")
    if not isinstance(events, dict):
        return "document events unreadable"
    for doctype in GUARDED_DOCTYPES:
        actual = events.get(doctype) or {}
        if not isinstance(actual, dict):
            return "document events unreadable"
        if {
            event: tuple(handlers or ())
            for event, handlers in actual.items()
        } != REVIEWED_DOC_EVENTS[doctype]:
            return f"document event drift for {doctype}"
    wildcard = snapshot.get("wildcard_doc_events")
    if not isinstance(wildcard, dict):
        return "wildcard events unreadable"
    for event, reviewed in REVIEWED_WILDCARD_EVENTS.items():
        if tuple(wildcard.get(event) or ()) != reviewed:
            return f"wildcard event drift for {event}"
    if tuple(snapshot.get("scheduler_finance_jobs") or ()):
        return "a scheduler job posts finance documents"
    overrides = snapshot.get("class_overrides")
    if not isinstance(overrides, dict):
        return "class overrides unreadable"
    for doctype in GUARDED_DOCTYPES:
        if (
            tuple(overrides.get(doctype) or ())
            != REVIEWED_CLASS_OVERRIDES[doctype]
        ):
            return f"class override drift for {doctype}"
    if tuple(snapshot.get("fence_kinds") or ()) != FINANCE_KINDS:
        return "fence kinds are not the reviewed finance set"
    return None


# -- strict request bodies -------------------------------------------------


def require_operation_key(value):
    if not isinstance(value, str):
        raise FinanceContractError("operation_key is invalid")
    if not MIN_OPERATION_KEY_LENGTH <= len(value) <= MAX_OPERATION_KEY_LENGTH:
        raise FinanceContractError("operation_key is invalid")
    if OPERATION_KEY_RE.fullmatch(value) is None:
        raise FinanceContractError("operation_key is invalid")
    if value.startswith(RESERVED_OPERATION_KEY_PREFIXES):
        raise FinanceContractError("operation_key is reserved")
    return value


def _require_body(body, allowed):
    if not isinstance(body, dict):
        raise FinanceContractError("body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise FinanceContractError("body keys must be strings")
    if set(body) - allowed:
        raise FinanceContractError("unknown field")
    return body


def _require_text(value, field, maximum=MAX_TEXT_LENGTH):
    if not is_valid_text(value, maximum):
        raise FinanceContractError(f"{field} is invalid")
    stripped = value.strip()
    if not stripped or stripped != value:
        raise FinanceContractError(f"{field} is invalid")
    return stripped


def _optional_text(value, field, maximum=MAX_TEXT_LENGTH):
    if value is None:
        return None
    return _require_text(value, field, maximum)


def _require_number(value, field, *, maximum, exclusive_minimum=False):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise FinanceContractError(f"{field} is invalid")
    if value != value or value in (float("inf"), float("-inf")):
        raise FinanceContractError(f"{field} is invalid")
    number = float(value)
    if exclusive_minimum:
        if number <= 0:
            raise FinanceContractError(f"{field} is invalid")
    elif number < 0:
        raise FinanceContractError(f"{field} is invalid")
    if number > maximum:
        raise FinanceContractError(f"{field} is invalid")
    return number


def _require_date(value, field):
    text = _require_text(value, field, 40)
    if DATE_RE.fullmatch(text) is None:
        raise FinanceContractError(f"{field} is invalid")
    return text


def validate_invoice(body):
    body = _require_body(body, INVOICE_KEYS)
    for required in (
        "operation_key",
        "customer",
        "selling_price_list",
        "due_date",
        "items",
    ):
        if required not in body:
            raise FinanceContractError(f"missing field {required}")
    raw_items = body["items"]
    if not isinstance(raw_items, (list, tuple)) or not raw_items:
        raise FinanceContractError("items must be a non-empty list")
    if len(raw_items) > MAX_ROWS:
        raise FinanceContractError("too many itemized lines")
    lines = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise FinanceContractError("an itemized line must be a mapping")
        for key in raw:
            if not isinstance(key, str):
                raise FinanceContractError("line keys must be strings")
        if set(raw) != INVOICE_LINE_KEYS:
            raise FinanceContractError("an itemized line is invalid")
        lines.append(
            {
                "item_code": _require_text(raw["item_code"], "item_code"),
                "qty": _require_number(
                    raw["qty"],
                    "qty",
                    maximum=MAX_QTY,
                    exclusive_minimum=True,
                ),
            }
        )
    discount = 0.0
    if "discount" in body:
        discount = _require_number(body["discount"], "discount", maximum=MAX_AMOUNT)
    return {
        "kind": INVOICE_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "customer": _require_text(body["customer"], "customer"),
        "selling_price_list": _require_text(
            body["selling_price_list"], "selling_price_list"
        ),
        "due_date": _require_date(body["due_date"], "due_date"),
        "items": lines,
        "discount": discount,
    }


def validate_payment(body):
    body = _require_body(body, PAYMENT_KEYS)
    for required in (
        "operation_key",
        "invoice",
        "mode_of_payment",
        "amount",
        "allocations",
    ):
        if required not in body:
            raise FinanceContractError(f"missing field {required}")
    invoice = _require_text(body["invoice"], "invoice")
    raw_allocations = body["allocations"]
    if not isinstance(raw_allocations, (list, tuple)) or not raw_allocations:
        raise FinanceContractError("allocations must be a non-empty list")
    if len(raw_allocations) > MAX_ROWS:
        raise FinanceContractError("too many allocations")
    allocations = []
    for raw in raw_allocations:
        if not isinstance(raw, dict):
            raise FinanceContractError("an allocation must be a mapping")
        for key in raw:
            if not isinstance(key, str):
                raise FinanceContractError("allocation keys must be strings")
        if set(raw) != ALLOCATION_KEYS:
            raise FinanceContractError("an allocation is invalid")
        target = _require_text(raw["invoice"], "allocation invoice")
        if target != invoice:
            raise FinanceContractError("an allocation targets another invoice")
        allocations.append(
            {
                "invoice": target,
                "amount": _require_number(
                    raw["amount"],
                    "allocation amount",
                    maximum=MAX_AMOUNT,
                    exclusive_minimum=True,
                ),
            }
        )
    return {
        "kind": PAYMENT_KIND,
        "operation_key": require_operation_key(body["operation_key"]),
        "invoice": invoice,
        "mode_of_payment": _require_text(
            body["mode_of_payment"], "mode_of_payment"
        ),
        "amount": _require_number(
            body["amount"], "amount", maximum=MAX_AMOUNT, exclusive_minimum=True
        ),
        "allocations": allocations,
        "reference_no": _optional_text(body.get("reference_no"), "reference_no"),
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


def _price_list_problem(detail):
    price_list = detail.get("price_list")
    if price_list is None:
        # The converted lane lets the installed pricing controller resolve the
        # price list; per-line native pricing is checked below instead.
        return None
    if not isinstance(price_list, dict) or not price_list.get("exists"):
        return (
            HTTP_REFUSED,
            "selling_price_list",
            "the selling price list does not exist",
        )
    if not price_list.get("selling"):
        return (
            HTTP_REFUSED,
            "selling_price_list",
            "the price list is not a selling price list",
        )
    if not price_list.get("enabled"):
        return (
            HTTP_REFUSED,
            "selling_price_list",
            "the selling price list is not enabled",
        )
    return None


def invoice_problems(detail, payload):
    """Every pre-dispatch invoice decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    problems = []
    if not detail.get("customer_exists"):
        problems.append(
            (
                HTTP_REFUSED,
                "customer",
                f"customer {payload['customer']} does not exist",
            )
        )
    problem = _price_list_problem(detail)
    if problem:
        problems.append(problem)
    price_currency = detail.get("price_currency")
    if "price_currency" in detail:
        if not isinstance(price_currency, str) or not price_currency:
            problems.append(
                (
                    HTTP_REFUSED,
                    "selling_price_list",
                    "the price list currency could not be resolved",
                )
            )
        elif (
            isinstance(detail.get("currency"), str)
            and detail.get("currency")
            and price_currency != detail["currency"]
        ):
            problems.append(
                (
                    HTTP_REFUSED,
                    "selling_price_list",
                    "the price list currency differs from the company currency",
                )
            )
    if detail.get("price_conversion_unverified") is True:
        problems.append(
            (
                HTTP_REFUSED,
                "selling_price_list",
                "the price list conversion rate is not one",
            )
        )
    problems.extend(_money_problems(detail))

    today = detail.get("today")
    if isinstance(today, str) and payload["due_date"] < today:
        problems.append(
            (
                HTTP_REFUSED,
                "due_date",
                "the due date is before the posting date",
            )
        )

    if not _text(detail.get("receivable_account"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "debit_to",
                "the company has no Accounts Receivable account",
            )
        )
    if not _text(detail.get("cost_center"), MAX_TEXT_LENGTH):
        problems.append(
            (HTTP_REFUSED, "cost_center", "the company has no cost center")
        )

    prices = detail.get("prices") or {}
    items = detail.get("items") or {}
    income_accounts = detail.get("income_accounts") or {}
    price_unverified = detail.get("price_unverified") or ()
    seen = set()
    for index, line in enumerate(payload["items"], start=1):
        code = line["item_code"]
        label = f"items[{index}]"
        item = items.get(code)
        if code in price_unverified:
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{label}.rate",
                    "the native price and pricing basis could not be verified",
                )
            )
        if not isinstance(item, dict) or not item.get("exists"):
            problems.append(
                (HTTP_REFUSED, f"{label}.item_code", f"item {code} does not exist")
            )
        elif not item.get("is_sales_item"):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{label}.item_code",
                    f"item {code} is not a sales item",
                )
            )
        if not _positive(prices.get(code)):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{label}.rate",
                    f"item {code} has no selling price in the price list",
                )
            )
        if not _text(income_accounts.get(code), MAX_TEXT_LENGTH):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{label}.income_account",
                    f"item {code} has no income account",
                )
            )
        if code in seen:
            problems.append(
                (
                    HTTP_REFUSED,
                    label,
                    f"duplicate charge: item {code} appears more than once",
                )
            )
        seen.add(code)

    discount = payload.get("discount") or 0.0
    if discount > 0:
        if detail.get("discount_available") is not True:
            problems.append(
                (
                    HTTP_REFUSED,
                    "discount",
                    DISCOUNT_NOT_ENABLED_MESSAGE,
                )
            )
        else:
            totals = invoice_totals(detail, payload)
            if discount >= totals["net_total"]:
                problems.append(
                    (
                        HTTP_REFUSED,
                        "discount",
                        "the discount exceeds the subtotal",
                    )
                )
    return tuple(problems)


def payment_problems(detail, payload):
    """Every pre-fence payment decision, in operator order."""

    if not isinstance(detail, dict):
        return ((HTTP_REFUSED, "detail", "target state is unreadable"),)
    invoice = detail.get("invoice")
    if not isinstance(invoice, dict) or not invoice.get("exists"):
        return (
            (
                HTTP_REFUSED,
                "invoice",
                f"invoice {payload['invoice']} does not exist",
            ),
        )
    problems = []
    status = invoice.get("status")
    if invoice.get("docstatus") == CANCELLED_DOCSTATUS or status == (
        INVOICE_CANCELLED_STATUS
    ):
        problems.append(
            (
                HTTP_REFUSED,
                "invoice",
                f"invoice {payload['invoice']} is cancelled",
            )
        )
    elif invoice.get("docstatus") != SUBMITTED_DOCSTATUS:
        problems.append(
            (
                HTTP_REFUSED,
                "invoice",
                f"invoice {payload['invoice']} is not submitted",
            )
        )
    elif status == INVOICE_PAID_STATUS or not _positive(
        invoice.get("outstanding_amount")
    ):
        problems.append(
            (
                HTTP_REFUSED,
                "invoice",
                f"invoice {payload['invoice']} is already paid",
            )
        )
    problems.extend(_money_problems(detail))
    if not _text(invoice.get("customer"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "party",
                f"invoice {payload['invoice']} has no customer",
            )
        )
    allocated = sum(entry["amount"] for entry in payload["allocations"])
    if abs(allocated - payload["amount"]) > MONEY_TOLERANCE:
        problems.append(
            (
                HTTP_REFUSED,
                "allocations",
                "the allocations do not sum to the paid amount",
            )
        )
    outstanding = invoice.get("outstanding_amount")
    if _positive(outstanding) and payload["amount"] > float(outstanding) + 1e-9:
        problems.append(
            (
                HTTP_REFUSED,
                "amount",
                "the paid amount exceeds the invoice outstanding balance",
            )
        )

    mode = detail.get("mode") or {}
    name = payload["mode_of_payment"]
    if mode.get("exists") is not True:
        problems.append(
            (
                HTTP_REFUSED,
                "mode_of_payment",
                f"mode of payment {name} does not exist",
            )
        )
    elif mode.get("accepted") is not True:
        problems.append(
            (
                HTTP_REFUSED,
                "mode_of_payment",
                f"payment mode {name} is not an accepted mode "
                f"({' or '.join(ACCEPTED_PAYMENT_MODES)} only)",
            )
        )
    elif not _text(mode.get("account"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "mode_of_payment",
                f"mode of payment {name} has no account for the company",
            )
        )
    elif name == CARD_PAYMENT_MODE and not payload.get("reference_no"):
        problems.append(
            (
                HTTP_REFUSED,
                "reference_no",
                "a card payment requires a terminal reference",
            )
        )

    if detail.get("payment_exists") is True:
        problems.append(
            (
                HTTP_REFUSED,
                "invoice",
                f"a payment for invoice {payload['invoice']} is already recorded",
            )
        )
    if not _text(detail.get("receivable_account"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "paid_from",
                "the company has no Accounts Receivable account",
            )
        )
    return tuple(problems)


# -- EXPECTED side effects -------------------------------------------------


def _round_money(detail, value):
    """Round through the installed policy the caller supplied.

    Frappe's configured rounding method is not Python ``round`` (a half-cent
    such as 0.125 becomes 0.13 under the installed legacy policy). The gateway
    passes that policy as ``money_rounder``; a missing callable is the legacy
    pure-Python test fallback only.
    """

    rounder = detail.get("money_rounder") if isinstance(detail, dict) else None
    if callable(rounder):
        try:
            return float(rounder(value))
        except Exception as exc:
            raise FinanceContractError("installed money rounding unavailable") from exc
    return round(float(value), 2)


def invoice_totals(detail, payload):
    """The server-owned totals: rates come from the installed price list."""

    prices = detail.get("prices") or {}
    lines = []
    net_total = 0.0
    for line in payload["items"]:
        rate = _round_money(detail, prices.get(line["item_code"]) or 0.0)
        amount = _round_money(detail, line["qty"] * rate)
        net_total = _round_money(detail, net_total + amount)
        lines.append(
            {
                "item_code": line["item_code"],
                "qty": line["qty"],
                "rate": rate,
                "amount": amount,
            }
        )
    discount = _round_money(detail, payload.get("discount") or 0.0)
    grand_total = _round_money(detail, net_total - discount)
    return {
        "lines": lines,
        "net_total": net_total,
        "discount": discount,
        "grand_total": grand_total,
        "total_taxes": 0.0,
    }


def expected_invoice(detail, payload):
    totals = invoice_totals(detail, payload)
    income_accounts = detail.get("income_accounts") or {}
    credit_by_account = {}
    for line in totals["lines"]:
        account = income_accounts.get(line["item_code"])
        if account:
            credit_by_account[account] = _round_money(
                detail,
                credit_by_account.get(account, 0.0) + line["amount"],
            )
    debit_account = detail.get("receivable_account")
    return {
        "doctype": INVOICE_DOCTYPE,
        "kind": INVOICE_KIND,
        "docstatus": SUBMITTED_DOCSTATUS,
        "status": INVOICE_UNPAID_STATUS,
        "currency": detail.get("currency"),
        "debit_account": debit_account,
        # The installed controller credits the line income accounts resolved
        # from installed records (never the client payload), merging rows that
        # share an account.
        "credit_accounts": sorted(credit_by_account),
        "debit_by_account": (
            {debit_account: totals["grand_total"]} if debit_account else {}
        ),
        "credit_by_account": credit_by_account,
        "net_total": totals["net_total"],
        "grand_total": totals["grand_total"],
        "gl_rows": len(totals["lines"]) * GL_ROWS_PER_INVOICE_LINE
        + GL_ROWS_PER_INVOICE,
        "gl_rows_max": len(totals["lines"]) * GL_ROWS_PER_INVOICE_LINE
        + GL_ROWS_PER_INVOICE,
        "debit_total": totals["grand_total"],
        "credit_total": totals["net_total"],
        "sle_rows": 0,
        "update_stock": PINNED_UPDATE_STOCK,
        "ple_rows": 1,
        "unexpected_gl_rows": 0,
    }


def expected_payment(detail, payload):
    invoice = detail.get("invoice") or {}
    mode = detail.get("mode") or {}
    outstanding = float(invoice.get("outstanding_amount") or 0.0)
    remaining = round(outstanding - payload["amount"], 2)
    full = remaining <= MONEY_TOLERANCE
    return {
        "doctype": PAYMENT_DOCTYPE,
        "kind": PAYMENT_KIND,
        "docstatus": SUBMITTED_DOCSTATUS,
        "gl_rows": GL_ROWS_PER_PAYMENT,
        "ple_rows": len(payload["allocations"])
        * PAYMENT_LEDGER_ROWS_PER_ALLOCATION,
        "unexpected_gl_rows": 0,
        # Receive: the debtor account is credited, the mode account debited.
        # Both are resolved from installed records, never the client payload.
        "paid_from_account": detail.get("receivable_account"),
        "paid_to_account": mode.get("account"),
        "invoice": payload["invoice"],
        "allocated": payload["amount"],
        "outstanding_delta": -payload["amount"],
        "outstanding_after": round(outstanding - payload["amount"], 2),
        "status": INVOICE_PAID_STATUS if full else None,
        "unchanged_status": None if full else invoice.get("status"),
    }


def invoice_gl_sums(gl_rows, expected):
    """Legacy total classifier for expected-account tests/helpers.

    Posted invoice verification uses ``invoice_gl_accounts`` so merged or
    separate account groups are checked by account sum, not by line count.
    """

    receivable = expected.get("debit_account")
    incomes = set(expected.get("credit_accounts") or ())
    debit_total = 0.0
    credit_total = 0.0
    unexpected = 0
    for gl in gl_rows:
        account = gl.get("account")
        if account and account == receivable:
            debit_total += float(gl.get("debit") or 0.0)
        elif account and account in incomes:
            credit_total += float(gl.get("credit") or 0.0)
        else:
            unexpected += 1
    return round(debit_total, 2), round(credit_total, 2), unexpected


def invoice_gl_accounts(gl_rows, expected):
    """Group actual GL rows by reviewed account and side.

    The installed controller merges rows that share an account/cost center, so
    no per-line row count is asserted. Only the reviewed receivable debit
    account and the reviewed income credit accounts are accepted; a wrong-side
    posting or any other account is counted as unexpected.
    """

    debit_account = expected.get("debit_account")
    credit_accounts = set(expected.get("credit_accounts") or ())
    debit_by_account = {}
    credit_by_account = {}
    unexpected = 0
    for gl in gl_rows:
        account = gl.get("account")
        debit = float(gl.get("debit") or 0.0)
        credit = float(gl.get("credit") or 0.0)
        if not account:
            unexpected += 1
        elif account == debit_account:
            if credit:
                unexpected += 1
            debit_by_account[account] = (
                debit_by_account.get(account, 0.0) + debit
            )
        elif account in credit_accounts:
            if debit:
                unexpected += 1
            credit_by_account[account] = (
                credit_by_account.get(account, 0.0) + credit
            )
        else:
            unexpected += 1
    return debit_by_account, credit_by_account, unexpected


def payment_gl_unexpected(gl_rows, expected):
    """Count payment GL rows outside the reviewed mode/receivable accounts."""

    accounts = {
        expected.get("paid_to_account"),
        expected.get("paid_from_account"),
    }
    accounts.discard(None)
    return sum(
        1
        for gl in gl_rows
        if not gl.get("account") or gl.get("account") not in accounts
    )


def prepared_problem(kind, prepared):
    """Closed-world check of the mutation body a gateway will execute.

    The prepared body is the server's own resolved document payload: it carries
    the company, currency, price list and ledger accounts resolved from
    installed state, the server-owned dates and the kind's own route fields. A
    missing piece, a client total, a non-zero ``update_stock``, an invented tax
    row or any card field is refused before the fence, so a gateway drift can
    never turn into a post-dispatch KeyError.
    """

    if kind not in FINANCE_KINDS:
        return "unknown finance kind"
    if not isinstance(prepared, dict):
        return "prepared body unreadable"
    for field in ("company", "currency"):
        if not _text(prepared.get(field), MAX_TEXT_LENGTH):
            return f"prepared body has no {field}"
    if kind == INVOICE_KIND:
        return _prepared_invoice_problem(prepared)
    return _prepared_payment_problem(prepared)


def _prepared_invoice_problem(prepared):
    for field in (
        "customer",
        "selling_price_list",
        "debit_to",
        "cost_center",
    ):
        if not _text(prepared.get(field), MAX_TEXT_LENGTH):
            return f"prepared body has no {field}"
    for field in ("due_date", "posting_date"):
        if not _text(prepared.get(field), 40):
            return f"prepared body has no {field}"
    if not (
        isinstance(prepared.get("update_stock"), int)
        and not isinstance(prepared.get("update_stock"), bool)
        and prepared.get("update_stock") == PINNED_UPDATE_STOCK
    ):
        return "prepared invoice does not pin update_stock 0"
    for field in CLIENT_TOTAL_FIELDS:
        if field in prepared:
            return "prepared invoice carries a computed total"
    taxes = prepared.get("taxes")
    if not isinstance(taxes, (list, tuple)):
        return "prepared invoice has no tax table"
    if taxes:
        return "prepared invoice carries a tax row"
    items = prepared.get("items")
    if not isinstance(items, (list, tuple)) or not items:
        return "prepared invoice has no itemized line"
    for line in items:
        if not isinstance(line, dict):
            return "prepared line is unreadable"
        if not _text(line.get("item_code"), MAX_TEXT_LENGTH):
            return "prepared line has no item"
        if not _positive(line.get("qty")):
            return "prepared line quantity is invalid"
        if not _positive(line.get("rate")):
            return "prepared line rate is invalid"
        if not _text(line.get("income_account"), MAX_TEXT_LENGTH):
            return "prepared line has no income account"
        if not _text(line.get("cost_center"), MAX_TEXT_LENGTH):
            return "prepared line has no cost center"
        amount = line.get("amount")
        if not _number(amount) or abs(
            float(amount) - float(line["qty"]) * float(line["rate"])
        ) > MONEY_TOLERANCE:
            return "prepared line amount differs from quantity times rate"
    if "discount_amount" in prepared or "additional_discount_percentage" in (
        prepared
    ):
        return "a discount may ride only the deductions child"
    discount = prepared.get("discount") or 0.0
    deductions = prepared.get("deductions") or ()
    if discount > 0:
        if not isinstance(deductions, (list, tuple)) or len(deductions) != 1:
            return "a discount needs exactly one deductions child row"
        row = deductions[0]
        if not isinstance(row, dict):
            return "the deductions child is unreadable"
        if not _text(row.get("account"), MAX_TEXT_LENGTH):
            return "the deductions child has no account"
        if not _text(row.get("cost_center"), MAX_TEXT_LENGTH):
            return "the deductions child has no cost center"
        if not _number(row.get("amount")) or abs(
            float(row["amount"]) - float(discount)
        ) > MONEY_TOLERANCE:
            return "the deductions child amount differs from the discount"
    elif deductions:
        return "a deductions child without a discount is refused"
    return None


def _prepared_payment_problem(prepared):
    for key in CARD_FIELDS:
        if key in prepared:
            return "a card field may never be prepared"
    if prepared.get("payment_type") != "Receive":
        return "prepared payment is not a Receive entry"
    if prepared.get("party_type") != "Customer":
        return "prepared payment party type is not Customer"
    for field in ("party", "paid_from", "paid_to", "mode_of_payment"):
        if not _text(prepared.get(field), MAX_TEXT_LENGTH):
            return f"prepared body has no {field}"
    for field in ("posting_date", "reference_date"):
        if not _text(prepared.get(field), 40):
            return f"prepared body has no {field}"
    paid = prepared.get("paid_amount")
    received = prepared.get("received_amount")
    if not _positive(paid):
        return "prepared payment amount is invalid"
    if not _number(received) or abs(float(received) - float(paid)) > (
        MONEY_TOLERANCE
    ):
        return "prepared received amount differs from the paid amount"
    references = prepared.get("references")
    if not isinstance(references, (list, tuple)) or not references:
        return "prepared payment has no allocation"
    total = 0.0
    for row in references:
        if not isinstance(row, dict):
            return "prepared allocation is unreadable"
        if row.get("reference_doctype") != INVOICE_DOCTYPE:
            return "prepared allocation does not target a sales invoice"
        if not _text(row.get("reference_name"), MAX_NAME_LENGTH):
            return "prepared allocation has no invoice"
        if not _positive(row.get("allocated_amount")):
            return "prepared allocation amount is invalid"
        total += float(row["allocated_amount"])
    if abs(total - float(paid)) > MONEY_TOLERANCE:
        return "prepared allocations do not sum to the paid amount"
    return None


def side_effect_problem(facts, expected):
    """Compare the closed-world re-read against the EXPECTED list.

    Nothing beyond the list is accepted: the posted document, the reviewed GL
    account grouping and sums (Debtors Dr / Sales Cr), no unknown account,
    the pinned ``update_stock`` state (SLE is not read for an invoice), the
    reviewed Payment Ledger row count, and — for a payment — the invoice's
    outstanding delta and status transition.
    """

    if not isinstance(facts, dict) or not isinstance(expected, dict):
        return "side-effect facts unreadable"
    if facts.get("doctype") != expected.get("doctype"):
        return "the finance document type differs"
    if facts.get("docstatus") != expected.get("docstatus"):
        return "the finance document has the wrong docstatus"
    gl_rows = facts.get("gl_rows")
    if expected["kind"] != INVOICE_KIND:
        if _count(gl_rows) is False or gl_rows != expected["gl_rows"]:
            return "gl row count differs"
    unexpected = facts.get("unexpected_gl_rows")
    if _count(unexpected) is False or unexpected != expected["unexpected_gl_rows"]:
        return "a gl row on an account outside the reviewed set was posted"
    ple_rows = facts.get("ple_rows")
    if _count(ple_rows) is False or ple_rows != expected["ple_rows"]:
        return "payment ledger row count differs"

    if expected["kind"] == INVOICE_KIND:
        if facts.get("currency") != expected.get("currency"):
            return (
                "the posted invoice currency differs from the server currency"
            )
        if facts.get("status") != expected["status"]:
            return "the invoice status differs"
        if not _money_equal(facts.get("grand_total"), expected["grand_total"]):
            return "the posted total differs from the server total"
        if not _money_equal(facts.get("net_total"), expected["net_total"]):
            return "the posted net total differs from the server total"
        if not _money_equal(facts.get("debit_total"), expected["debit_total"]):
            return "the receivables debit sum differs from the posted total"
        if not _money_equal(
            facts.get("credit_total"), expected["credit_total"]
        ):
            return "the income credit sum differs from the net total"
        if _count(gl_rows) is False or gl_rows > expected.get(
            "gl_rows_max", expected["gl_rows"]
        ):
            return "more gl rows were posted than the reviewed maximum"
        accounts = facts.get("gl_accounts")
        if not isinstance(accounts, dict):
            return "posted gl account grouping is unreadable"
        actual_debit = accounts.get("debit")
        actual_credit = accounts.get("credit")
        if not isinstance(actual_debit, dict) or not isinstance(
            actual_credit, dict
        ):
            return "posted gl account grouping is unreadable"
        for account, amount in (expected.get("debit_by_account") or {}).items():
            if not _money_equal(actual_debit.get(account), amount):
                return "the receivables debit account sum differs"
        for account, amount in (expected.get("credit_by_account") or {}).items():
            if not _money_equal(actual_credit.get(account), amount):
                return "an income credit account sum differs"
        if facts.get("sle_verified") is True:
            if facts.get("sle_rows") != expected["sle_rows"]:
                return "a stock ledger row was posted by an invoice"
        elif "update_stock" in facts:
            if facts.get("update_stock") != expected.get(
                "update_stock", PINNED_UPDATE_STOCK
            ):
                return "the posted invoice update_stock differs"
        elif facts.get("sle_rows") != expected["sle_rows"]:
            return "a stock ledger row was posted by an invoice"
        if not _money_equal(
            facts.get("outstanding_amount"), expected["grand_total"]
        ):
            return "the posted invoice outstanding differs from its total"
        return None

    if not _money_equal(facts.get("allocated"), expected["allocated"]):
        return "the allocated amount differs from the paid amount"
    if not _money_equal(
        facts.get("outstanding_delta"), expected["outstanding_delta"]
    ):
        return "the invoice outstanding delta differs"
    if not _money_equal(
        facts.get("outstanding_after"), expected["outstanding_after"]
    ):
        return "the invoice outstanding after payment differs"
    target = expected.get("status")
    if target is not None:
        if facts.get("invoice_status") != target:
            return "the invoice status after payment differs"
    elif facts.get("invoice_status") != expected.get("unchanged_status"):
        return "a partial payment changed the invoice status"
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
        raise FinanceContractError(f"{field} is invalid")
    return secret


def _require_context(site, actor):
    if not is_valid_text(site, MAX_SITE_LENGTH) or not is_valid_text(
        actor, MAX_ACTOR_LENGTH
    ):
        raise FinanceContractError("receipt context is invalid")
    return site.strip(), actor.strip()


def encode_receipt(
    *,
    kind,
    reference,
    invoice,
    customer,
    amount,
    mode_of_payment="",
    status="",
):
    kind = require_kind(kind)
    for value, maximum, field in (
        (reference, MAX_NAME_LENGTH, "reference"),
        (invoice, MAX_NAME_LENGTH, "invoice"),
        (customer, MAX_TEXT_LENGTH, "customer"),
    ):
        if not is_valid_text(value, maximum):
            raise FinanceContractError(f"invalid receipt {field}")
    if not _positive(amount):
        raise FinanceContractError("invalid receipt amount")
    if mode_of_payment and not is_valid_text(mode_of_payment, MAX_TEXT_LENGTH):
        raise FinanceContractError("invalid receipt mode")
    if status and not is_valid_text(status, MAX_TEXT_LENGTH):
        raise FinanceContractError("invalid receipt status")
    receipt = json.dumps(
        {
            "codec": RECEIPT_CODEC,
            "version": RECEIPT_VERSION,
            "kind": kind,
            "reference": reference,
            "invoice": invoice,
            "customer": customer,
            "amount": float(amount),
            "mode_of_payment": mode_of_payment,
            "status": status,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if len(receipt) > MAX_RECEIPT_LENGTH:
        raise FinanceContractError("receipt is too long")
    return receipt


def decode_receipt(receipt):
    """Return a :class:`FinanceReceipt` or raise ``FinanceContractError``."""

    if (
        not isinstance(receipt, str)
        or not receipt
        or len(receipt) > MAX_RECEIPT_LENGTH
    ):
        raise FinanceContractError("invalid receipt")
    try:
        payload = json.loads(receipt)
    except ValueError as exc:
        raise FinanceContractError("invalid receipt") from exc
    if not isinstance(payload, dict) or set(payload) != RECEIPT_KEYS:
        raise FinanceContractError("invalid finance receipt")
    if payload["codec"] != RECEIPT_CODEC or payload["version"] != RECEIPT_VERSION:
        raise FinanceContractError("invalid finance receipt")
    try:
        kind = require_kind(payload["kind"])
    except FinanceContractError as exc:
        raise FinanceContractError("invalid finance receipt") from exc
    reference = payload["reference"]
    invoice = payload["invoice"]
    customer = payload["customer"]
    amount = payload["amount"]
    mode_of_payment = payload["mode_of_payment"]
    status = payload["status"]
    if not is_valid_text(reference, MAX_NAME_LENGTH):
        raise FinanceContractError("invalid finance receipt")
    if not is_valid_text(invoice, MAX_NAME_LENGTH):
        raise FinanceContractError("invalid finance receipt")
    if not is_valid_text(customer, MAX_TEXT_LENGTH):
        raise FinanceContractError("invalid finance receipt")
    if not _positive(amount):
        raise FinanceContractError("invalid finance receipt")
    if not isinstance(mode_of_payment, str) or (
        mode_of_payment and not is_valid_text(mode_of_payment, MAX_TEXT_LENGTH)
    ):
        raise FinanceContractError("invalid finance receipt")
    if not isinstance(status, str) or (
        status and not is_valid_text(status, MAX_TEXT_LENGTH)
    ):
        raise FinanceContractError("invalid finance receipt")
    if kind == INVOICE_KIND and mode_of_payment:
        raise FinanceContractError("invalid finance receipt")
    if kind == PAYMENT_KIND and not mode_of_payment:
        raise FinanceContractError("invalid finance receipt")
    return FinanceReceipt(
        kind=kind,
        reference=reference,
        invoice=invoice,
        customer=customer,
        amount=float(amount),
        mode_of_payment=mode_of_payment,
        status=status,
    )


def operation_digest(secret, *, kind, operation_key, fields):
    _require_secret(secret, "operation secret")
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not isinstance(fields, dict):
        raise FinanceContractError("fields must be a mapping")
    for name, value in fields.items():
        if not isinstance(name, str) or not name:
            raise FinanceContractError("field names must be non-empty strings")
        if value is not None and not isinstance(value, str):
            raise FinanceContractError("field values must be text")
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
        b"clinic_ui_finance_operation|" + canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def ack_token(secret, site, actor, kind, operation_key, receipt):
    _require_secret(secret, "ack secret")
    site, actor = _require_context(site, actor)
    kind = require_kind(kind)
    operation_key = require_operation_key(operation_key)
    if not is_valid_text(receipt, MAX_RECEIPT_LENGTH):
        raise FinanceContractError("ack receipt is invalid")
    message = _framed(
        b"clinic_ui_finance_ack", site, actor, kind, operation_key, receipt
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def is_valid_ack_token(value):
    return isinstance(value, str) and ACK_TOKEN_RE.fullmatch(value) is not None


def validate_ack(body):
    if not isinstance(body, dict):
        raise FinanceContractError("ack body must be a mapping")
    for key in body:
        if not isinstance(key, str):
            raise FinanceContractError("ack body keys must be strings")
    if set(body) != ACK_KEYS:
        raise FinanceContractError("ack body is invalid")
    if not is_valid_ack_token(body["ack_token"]):
        raise FinanceContractError("ack token is invalid")
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


def _money_equal(left, right):
    if not _number(left) or not _number(right):
        return False
    return abs(float(left) - float(right)) <= MONEY_TOLERANCE
