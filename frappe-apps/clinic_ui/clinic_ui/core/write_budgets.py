"""The durable write budget registry for the full write-contract wave.

One unit is one SIT write operation that claimed the operation fence. Budgets
are a deployment property derived from the durable fence receipts, not client
input and not an in-memory counter. Refused writes never claim the fence, so
they never consume a unit.

The numbers are the user-approved final set for the isolated sit. Changing a
number here changes the approved write volume for every family that reads it.
Design Amendment A1 (2026-11-16, budget v3) is the approved set: ``admin_master``
grew to 14 for the two new master kinds (``uom``, ``supplier_group``) and the
procedure family gained the one ``procedure_create`` unit.
"""

BUDGETS = {
    "appointment_cancel": {"budget": 2, "family": "appointment"},
    "appointment_no_show": {"budget": 1, "family": "appointment"},
    "encounter_draft": {"budget": 2, "family": "encounter"},
    "encounter_complete": {"budget": 1, "family": "encounter"},
    "encounter_amend": {"budget": 1, "family": "encounter"},
    "procedure_create": {"budget": 1, "family": "procedure"},
    "procedure_complete": {"budget": 1, "family": "procedure"},
    "lab_order": {"budget": 1, "family": "lab"},
    "stock_receive": {"budget": 1, "family": "stock"},
    "stock_transfer": {"budget": 1, "family": "stock"},
    "stock_adjust": {"budget": 1, "family": "stock"},
    "purchase_order_draft": {"budget": 1, "family": "purchase"},
    "purchase_order_submit": {"budget": 1, "family": "purchase"},
    "purchase_receipt": {"budget": 1, "family": "purchase"},
    "invoice_submit": {"budget": 1, "family": "invoice"},
    "payment_receive": {"budget": 1, "family": "payment"},
    "admin_user": {"budget": 4, "family": "admin"},
    "admin_master": {"budget": 14, "family": "admin"},
}

ALL_KINDS = tuple(BUDGETS)


def budget_for(kind):
    """Return the approved durable budget for one write kind.

    An unknown or malformed kind fails closed with ``ValueError`` rather than
    defaulting to any allowance.
    """

    if not isinstance(kind, str) or kind not in BUDGETS:
        raise ValueError("unknown write kind")
    return BUDGETS[kind]["budget"]
