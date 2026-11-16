"""Closed-world contract for the pinned Clinical Procedure completion path.

The design rulings this module encodes (they are not re-decided here):

* completion drives the installed ``ClinicalProcedure.complete_procedure``
  document method by name, pinned with the sha256 of the installed source file,
  and never re-implements it. That method creates and submits the Material-Issue
  ``Stock Entry`` itself, then sets ``status`` ``Completed`` and
  ``actual_end_datetime``,
* the closed-world re-read EXPECTS exactly: the submitted Material-Issue Stock
  Entry the method returned, the procedure status/``actual_end_datetime`` pair,
  one Stock Ledger Entry per consumed item row, the matching negative Bin
  movement, the Stock Ledger GL rows those entries produce (count based: the two
  perpetual-inventory rows per entry, or none when the company does not post
  them) and no nursing task rows,
* ``price_list`` is mandatory whenever the procedure carries items (SPEC:197 and
  the installed method throw ``"Price List is mandatory"``); the contract decides
  it before the fence so a missing master never latches the site,
* completion is exactly-once: a submitted-then-completed procedure, a cancelled
  procedure and a draft procedure are all refused before the fence claim. A
  second completion would create a second Stock Entry (SPEC:221), so the server
  refuses it and the client locks its button.

Installed field names are not hardcoded here. Each logical field lists the
installed candidates; the adapter resolves them from the site's own metadata
preflight (``frappe.get_meta``) and a missing piece refuses the write pre-fence
instead of guessing.

Every designed refusal is a pre-fence decision: role, boundary, install
preflight, target eligibility, price list, per-row batch/stock validity and
budget.
"""

from __future__ import annotations

PROCEDURE_DOCTYPE = "Clinical Procedure"
ITEM_DOCTYPE = "Clinical Procedure Item"
STOCK_ENTRY_DOCTYPE = "Stock Entry"
PROCEDURE_TEMPLATE_DOCTYPE = "Clinical Procedure Template"

PROCEDURE_COMPLETE_KIND = "procedure_complete"
PROCEDURE_KINDS = (PROCEDURE_COMPLETE_KIND,)

DRAFT_DOCSTATUS = 0
FINAL_DOCSTATUS = 1
CANCELLED_DOCSTATUS = 2
COMPLETED_STATUS = "Completed"
CANCELLED_STATUS = "Cancelled"
MATERIAL_ISSUE_TYPE = "Material Issue"

# The installed document method this family drives, pinned by name and by the
# sha256 of the installed source file (healthcare v16.6.1). The method is called
# on the locked document; the stock posting is its own, never ours.
COMPLETE_METHOD = "complete_procedure"
PROCEDURE_SOURCE_PATH = (
    "healthcare",
    "doctype",
    "clinical_procedure",
    "clinical_procedure.py",
)
PROCEDURE_SOURCE_SHA256 = (
    "1eb0883901c63b6a0eface06971f9ed3a11415f79ac826767d894653bb9b272c"
)

# The reviewed executable boundary: the versions pinned with the appointment and
# encounter families (installed healthcare v16.6.1) plus the sha of the file that
# defines the driven method. No wildcard handler and no class override may sit on
# the procedure or Stock Entry path.
REVIEWED_VERSIONS = {
    "frappe": "16.54.0",
    "erpnext": "16.52.1",
    "healthcare": "16.6.1",
}

# The ``doc_events["*"]`` tuples are site-global hooks: they fire for every
# doctype, including the Stock Entry the driven method submits. They were
# recorded verbatim with the appointment family from the same pinned stack, so
# this family compares them against that reviewed set instead of inventing a
# second list. Any change there is unreviewed drift for both families.
REVIEWED_WILDCARD_EVENTS = {
    "before_insert": (),
    "before_validate": (),
    "before_save": (),
    "after_insert": (),
    "on_update": (
        "frappe.desk.notifications.clear_doctype_notifications",
        "frappe.workflow.doctype.workflow_action.workflow_action."
        "process_workflow_actions",
        "frappe.core.doctype.file.utils.attach_files_to_document",
        "frappe.automation.doctype.assignment_rule.assignment_rule.apply",
        "frappe.automation.doctype.assignment_rule.assignment_rule.update_due_date",
        "frappe.core.doctype.user_type.user_type."
        "apply_permissions_for_non_standard_user_type",
        "frappe.core.doctype.permission_log.permission_log.make_perm_log",
        "frappe.search.sqlite_search.update_doc_index",
    ),
    "validate": (
        "erpnext.support.doctype.service_level_agreement.service_level_agreement"
        ".apply",
        "erpnext.setup.doctype.transaction_deletion_record."
        "transaction_deletion_record.check_for_running_deletion_job",
    ),
    "on_change": (
        "frappe.automation.doctype.milestone_tracker.milestone_tracker."
        "evaluate_milestone",
    ),
}
# A scheduled job whose dotted path names the procedure doctype would run outside
# this contract; none may exist for the family.
SCHEDULER_PROCEDURE_TOKEN = "clinical_procedure"

# The installed refusal text this family reproduces (SPEC:197). It is not echoed
# to the client: the response is the fixed safe envelope, and this constant is
# what the contract and the tests name.
PRICE_LIST_MESSAGE = "Price List is mandatory"

MAX_ROWS = 20
MAX_TEXT_LENGTH = 140
# A Material-Issue Stock Entry posts one GL row pair per Stock Ledger Entry under
# perpetual inventory and none otherwise; nothing else is a reviewed outcome.
GL_ROWS_PER_ENTRY = 2

HTTP_REFUSED = 417

# Installed-field preflight. Logical name -> installed candidates, per DocType.
# A DocType that is absent, or a logical field with no installed candidate,
# refuses the family with a recorded reason (design ruling 8).
REQUIRED_FIELDS = {
    PROCEDURE_DOCTYPE: {
        "patient": ("patient",),
        "procedure_template": ("procedure_template",),
        "company": ("company",),
        "warehouse": ("warehouse",),
        "price_list": ("price_list",),
        "consume_stock": ("consume_stock",),
        "items": ("items",),
        "status": ("status",),
        "actual_end_datetime": ("actual_end_datetime",),
        "appointment": ("appointment",),
        "service_request": ("service_request",),
    },
    ITEM_DOCTYPE: {
        "item_code": ("item_code",),
        "qty": ("qty",),
        "uom": ("uom",),
        "batch_no": ("batch_no",),
        "invoice_separately_as_consumables": (
            "invoice_separately_as_consumables",
        ),
    },
    STOCK_ENTRY_DOCTYPE: {
        "stock_entry_type": ("stock_entry_type",),
        "docstatus": ("docstatus",),
    },
    PROCEDURE_TEMPLATE_DOCTYPE: {
        "post_op_nursing_checklist_template": (
            "post_op_nursing_checklist_template",
        ),
    },
}


class ProcedureContractError(ValueError):
    """A procedure contract input or value is outside the accepted shape."""


def install_problem(meta_fields):
    """Return the first missing installed field, or ``None`` when complete.

    ``meta_fields`` maps a DocType name to the set of installed field names
    (``frappe.get_meta(doctype).get_valid_columns()`` in the adapter). An absent
    DocType or a logical field with no installed candidate is a pre-fence
    refusal, not a guess.
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
    source = snapshot.get("source_sha256")
    if not isinstance(source, dict):
        return "source hashes unreadable"
    if source.get("clinical_procedure") != PROCEDURE_SOURCE_SHA256:
        return "source drift for clinical_procedure"
    if snapshot.get("complete_method") != COMPLETE_METHOD:
        return "driven method drift"
    if snapshot.get("complete_callable") != 1:
        return "driven method is not callable"
    if snapshot.get("procedure_submittable") != 1:
        return "Clinical Procedure is not submittable"
    if snapshot.get("stock_entry_submittable") != 1:
        return "Stock Entry is not submittable"
    if snapshot.get("enabled_server_scripts") != 0:
        return "server scripts are enabled"
    doc_events = snapshot.get("doc_events")
    if not isinstance(doc_events, dict):
        return "doc_events unreadable"
    for doctype in (PROCEDURE_DOCTYPE, STOCK_ENTRY_DOCTYPE):
        if tuple(doc_events.get(doctype) or ()):
            return f"unreviewed doc_events for {doctype}"
    wildcard = snapshot.get("wildcard_doc_events")
    if not isinstance(wildcard, dict):
        return "wildcard doc_events unreadable"
    for event, reviewed in REVIEWED_WILDCARD_EVENTS.items():
        if tuple(wildcard.get(event) or ()) != reviewed:
            return f"wildcard doc_events drift for {event}"
    if tuple(snapshot.get("scheduler_procedure_jobs") or ()):
        return "a scheduler job touches the procedure family"
    overrides = snapshot.get("class_overrides")
    if not isinstance(overrides, dict):
        return "class overrides unreadable"
    if tuple(overrides.get(PROCEDURE_DOCTYPE) or ()):
        return "Clinical Procedure class is overridden or extended"
    return None


def target_problem(values):
    """Refuse a completion target before any fence claim.

    Exactly-once is decided here: a procedure that is not submitted yet (nothing
    to complete), a completed procedure (a second call would submit a second
    Stock Entry, SPEC:221) and a cancelled procedure (CSV:76: no stock) are all
    refused pre-fence.
    """

    if not isinstance(values, dict):
        return "target state unreadable"
    docstatus = values.get("docstatus")
    status = values.get("status")
    if docstatus == CANCELLED_DOCSTATUS or status == CANCELLED_STATUS:
        return "target procedure is cancelled"
    if status == COMPLETED_STATUS:
        return "target procedure is already completed"
    if docstatus != FINAL_DOCSTATUS:
        return "target procedure is not submitted"
    if not values.get("patient"):
        return "target procedure has no patient"
    return None


def completion_problems(values):
    """Return every pre-fence completion problem as ``(status, name, text)``.

    ``values`` is the persisted detail read under native authority: the
    procedure fields plus the per-row batch and stock facts. The order is the
    order the operator meets them: warehouse/company/price list, then one line
    per item row, then the linked customer, then the nursing-checklist gate.
    """

    problems = []
    items = values.get("items") or ()
    if not isinstance(items, (list, tuple)):
        return ((HTTP_REFUSED, "items", "item rows are unreadable"),)
    consumes = bool(items) and bool(values.get("consume_stock"))

    if consumes:
        if not _text(values.get("warehouse"), MAX_TEXT_LENGTH):
            problems.append(
                (HTTP_REFUSED, "warehouse", "warehouse is missing")
            )
        if not _text(values.get("company"), MAX_TEXT_LENGTH):
            problems.append((HTTP_REFUSED, "company", "company is missing"))
    if items and not _text(values.get("price_list"), MAX_TEXT_LENGTH):
        problems.append(
            (HTTP_REFUSED, "price_list", PRICE_LIST_MESSAGE)
        )

    allow_negative = values.get("allow_negative_stock") is True
    today = values.get("today")
    for index, row in enumerate(items):
        if not isinstance(row, dict):
            return ((HTTP_REFUSED, "items", "item rows are unreadable"),)
        prefix = f"items[{index}]"
        code = _text(row.get("item_code"), MAX_TEXT_LENGTH)
        if not code:
            problems.append(
                (HTTP_REFUSED, f"{prefix}.item_code", "item code is missing")
            )
            continue
        qty = row.get("qty")
        if not _positive_number(qty):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{prefix}.qty",
                    f"quantity for {code} must be positive",
                )
            )
            continue
        if not _text(row.get("uom"), MAX_TEXT_LENGTH):
            problems.append(
                (HTTP_REFUSED, f"{prefix}.uom", f"unit for {code} is missing")
            )
        batch_no = _text(row.get("batch_no"), MAX_TEXT_LENGTH)
        if batch_no:
            batch_item = _text(row.get("batch_item"), MAX_TEXT_LENGTH)
            if batch_item and batch_item != code:
                problems.append(
                    (
                        HTTP_REFUSED,
                        f"{prefix}.batch_no",
                        f"batch {batch_no} does not belong to {code}",
                    )
                )
                continue
            expiry = _text(row.get("batch_expiry"), MAX_TEXT_LENGTH)
            if expiry and isinstance(today, str) and expiry < today:
                problems.append(
                    (
                        HTTP_REFUSED,
                        f"{prefix}.batch_no",
                        f"batch {batch_no} for {code} is expired",
                    )
                )
                continue
            if not allow_negative and not _at_least(row.get("batch_qty"), qty):
                problems.append(
                    (
                        HTTP_REFUSED,
                        f"{prefix}.qty",
                        f"batch {batch_no} for {code} has insufficient stock",
                    )
                )
                continue
        elif not allow_negative and not _at_least(
            row.get("available_qty"), qty
        ):
            problems.append(
                (
                    HTTP_REFUSED,
                    f"{prefix}.qty",
                    f"{code} has insufficient stock at the procedure warehouse",
                )
            )
            continue

    if consumes and not _text(values.get("customer"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "customer",
                "patient has no linked customer",
            )
        )

    if _text(values.get("post_op_checklist"), MAX_TEXT_LENGTH):
        problems.append(
            (
                HTTP_REFUSED,
                "nursing_tasks",
                "post-operative nursing checklist would create unlisted rows",
            )
        )
    return tuple(problems)


def expected_side_effects(values):
    """The EXPECTED side-effect list for the closed-world re-read.

    Count based: one Stock Ledger Entry per item row (the Material Issue the
    driven method submits), the matching negative Bin movement at the procedure
    warehouse and one GL row pair per entry. A procedure that consumes nothing
    expects no Stock Entry at all.
    """

    items = values.get("items") or ()
    rows = []
    for row in items:
        if not isinstance(row, dict):
            return None
        rows.append(
            {
                "item_code": _text(row.get("item_code"), MAX_TEXT_LENGTH),
                "qty": row.get("qty"),
            }
        )
    consumes = bool(rows) and bool(values.get("consume_stock"))
    total = 0.0
    for row in rows:
        if not _positive_number(row["qty"]):
            return None
        total += float(row["qty"])
    return {
        "consumes_stock": consumes,
        "item_rows": len(rows) if consumes else 0,
        "total_qty": total if consumes else 0.0,
        "warehouse": _text(values.get("warehouse"), MAX_TEXT_LENGTH),
    }


def side_effect_problem(facts, expected):
    """Check the closed-world state after the driven method returns.

    EXPECTED: the submitted Material-Issue Stock Entry name the method returned,
    the procedure ``Completed`` with ``actual_end_datetime`` set, one Stock
    Ledger Entry per item row, the matching negative Bin movement of the summed
    quantity, a reviewed GL row count and no nursing task rows.
    """

    if not isinstance(facts, dict):
        return "post-completion facts unreadable"
    if not isinstance(expected, dict):
        return "expected side effects unreadable"

    stock_entry = facts.get("stock_entry")
    if expected["consumes_stock"]:
        if not _text(stock_entry, MAX_TEXT_LENGTH):
            return "the driven method returned no Stock Entry"
        if facts.get("stock_entry_docstatus") != FINAL_DOCSTATUS:
            return "the Stock Entry is not submitted"
        if facts.get("stock_entry_type") != MATERIAL_ISSUE_TYPE:
            return "the Stock Entry is not a Material Issue"
        if facts.get("stock_entry_company") != facts.get("procedure_company"):
            return "the Stock Entry company differs from the procedure"
    elif _text(stock_entry, MAX_TEXT_LENGTH):
        return "a Stock Entry was created for a non-consuming procedure"

    if facts.get("status") != COMPLETED_STATUS:
        return "procedure status is not Completed"
    if not _text(facts.get("actual_end_datetime"), 40):
        return "procedure actual end is not recorded"

    sle_rows = facts.get("stock_ledger_rows")
    if not _count(sle_rows):
        return "stock ledger row count is unreadable"
    if sle_rows != expected["item_rows"]:
        return "stock ledger row count differs from the item rows"

    bin_delta = facts.get("bin_delta")
    if not _number(bin_delta):
        return "bin movement is unreadable"
    if abs(float(bin_delta) + float(expected["total_qty"])) > 1e-6:
        return "bin movement differs from the consumed quantity"
    if facts.get("bin_warehouse") != expected["warehouse"]:
        return "bin warehouse differs from the procedure warehouse"

    gl_rows = facts.get("gl_rows")
    if not _count(gl_rows):
        return "gl row count is unreadable"
    if gl_rows not in (0, expected["item_rows"] * GL_ROWS_PER_ENTRY):
        return "gl row count is outside the reviewed outcomes"

    nursing = facts.get("nursing_task_rows")
    if not _count(nursing):
        return "nursing task row count is unreadable"
    if nursing != 0:
        return "nursing tasks were created"
    return None


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


def _positive_number(value):
    return _number(value) and float(value) > 0


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _at_least(available, needed):
    return _number(available) and float(available) >= float(needed)
