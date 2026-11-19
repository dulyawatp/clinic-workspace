"""Unwired native lifecycle and bounded voucher integrity verification."""

from decimal import Decimal, InvalidOperation


class PostingPreDispatch(Exception):
    """No write was dispatched; caller retains transaction ownership."""


class PostingPostDispatch(Exception):
    """A write may have occurred; caller must roll back and handle uncertainty."""


def check_posting_permissions(gateway, doc):
    """Check native create and submit permissions on the populated draft."""
    try:
        frappe = gateway.frappe
        user = frappe.session.user
        if (not isinstance(user, str) or not user or user == "Guest"
                or doc.doctype != "Sales Invoice"
                or isinstance(doc.docstatus, bool)
                or not isinstance(doc.docstatus, int) or doc.docstatus != 0
                or any(doc.flags.get(key) for key in (
                    "ignore_permissions", "ignore_mandatory", "ignore_links"))):
            raise PostingPreDispatch("posting preflight refused")
        for permission in ("create", "submit"):
            if not frappe.has_permission("Sales Invoice", ptype=permission, doc=doc, user=user):
                raise PostingPreDispatch("posting preflight refused")
    except PostingPreDispatch:
        raise
    except Exception as exc:
        raise PostingPreDispatch("posting preflight unavailable") from exc


_INVOICE_FIELDS = (
    "customer", "patient", "company", "currency", "party_account_currency", "debit_to",
    "conversion_rate", "is_pos", "is_return", "update_stock", "items", "taxes", "advances",
    "payments", "total", "base_total", "net_total", "base_net_total", "grand_total",
    "base_grand_total", "discount_amount", "base_discount_amount", "outstanding_amount",
    "rounded_total", "base_rounded_total", "rounding_adjustment", "base_rounding_adjustment",
    "total_taxes_and_charges", "base_total_taxes_and_charges", "total_advance",
    "paid_amount", "base_paid_amount", "write_off_amount", "base_write_off_amount",
    "change_amount", "base_change_amount", "loyalty_points", "loyalty_amount",
    "redeem_loyalty_points", "allocate_advances_automatically",
    "write_off_outstanding_amount_automatically", "apply_discount_on",
    "additional_discount_percentage", "posting_date", "set_posting_time", "selling_price_list",
    "price_list_currency", "plc_conversion_rate",
)
_ITEM_FIELDS = (
    "item_code", "qty", "uom", "stock_uom", "income_account", "cost_center",
    "reference_dt", "reference_dn", "rate", "price_list_rate", "amount", "base_amount",
    "net_amount", "base_net_amount", "distributed_discount_amount", "conversion_factor",
)


def _number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("missing numeric value")
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError("non-finite numeric value")
        return result
    except InvalidOperation as exc:
        raise ValueError("invalid numeric value") from exc


def _read(gateway, doctype, name, fields):
    frappe = gateway.frappe
    meta = frappe.get_meta(doctype)
    if (any(meta.get_field(field) is None for field in fields)
            or not gateway._fields_readable(meta, fields)
            or not frappe.has_permission(doctype, ptype="read", doc=name, user=frappe.session.user)):
        raise ValueError("read authorization unavailable")
    doc = frappe.get_doc(doctype, name)
    if doc.name != name:
        raise ValueError("record identity drift")
    return doc


def _item_fields_readable(frappe):
    meta = frappe.get_meta("Sales Invoice Item")
    allowed = set(meta.get_permlevel_access("read", parenttype="Sales Invoice", user=frappe.session.user))
    masked = {field.fieldname for field in meta.get_masked_fields(parenttype="Sales Invoice")}
    for name in _ITEM_FIELDS:
        field = meta.get_field(name)
        if field is None or field.permlevel not in allowed or name in masked:
            return False
    return True


_GL_FIELDS = {
    "voucher_type": ("Link", "DocType"), "voucher_no": ("Dynamic Link", "voucher_type"),
    "company": ("Link", "Company"), "posting_date": ("Date", None),
    "account": ("Link", "Account"), "account_currency": ("Link", "Currency"),
    "party_type": ("Link", "DocType"), "party": ("Dynamic Link", "party_type"),
    "debit": ("Currency", "Company:company:default_currency"),
    "credit": ("Currency", "Company:company:default_currency"),
    "debit_in_account_currency": ("Currency", "account_currency"),
    "credit_in_account_currency": ("Currency", "account_currency"),
    "is_cancelled": ("Check", None),
    "against_voucher_type": ("Link", "DocType"),
    "against_voucher": ("Dynamic Link", "against_voucher_type"),
}
_SLE_FIELDS = {key: _GL_FIELDS[key] for key in ("voucher_type", "voucher_no")}


def _check_meta(frappe, doctype, fields):
    meta = frappe.get_meta(doctype)
    for name, (kind, options) in fields.items():
        field = meta.get_field(name)
        if field is None or field.fieldtype != kind or (options is not None and field.options != options):
            raise ValueError("ledger metadata mismatch")


def _check_ledger(frappe, name, visit, accounting, totals):
    _check_meta(frappe, "GL Entry", _GL_FIELDS)
    _check_meta(frappe, "Stock Ledger Entry", _SLE_FIELDS)
    rows = frappe.db.sql(
        "SELECT voucher_type, voucher_no, company, posting_date, account, account_currency, "
        "party_type, party, debit, credit, debit_in_account_currency, "
        "credit_in_account_currency, is_cancelled, against_voucher_type, against_voucher FROM `tabGL Entry` "
        "WHERE voucher_type = %s AND voucher_no = %s LIMIT 3",
        ("Sales Invoice", name),
    )
    if len(rows) != 2:
        raise ValueError("unexpected ledger row count")
    total = _number(totals["total"])
    expected = {
        accounting["receivable_account"]: ("Customer", visit["customer"], total, Decimal(0)),
        accounting["income_account"]: (None, None, Decimal(0), total),
    }
    if len(expected) != 2:
        raise ValueError("ledger accounts overlap")
    seen = set()
    for row in rows:
        if len(row) != 15:
            raise ValueError("ledger projection mismatch")
        (voucher_type, voucher_no, company, posting_date, account, currency, party_type, party,
         debit, credit, debit_currency, credit_currency, cancelled, against_type, against) = row
        if (voucher_type != "Sales Invoice" or voucher_no != name or company != visit["company"]
                or str(posting_date) != str(accounting["posting_date"]) or currency != visit["currency"]
                or account not in expected or account in seen or _number(cancelled) != 0):
            raise ValueError("ledger identity drift")
        expected_type, expected_party, expected_debit, expected_credit = expected[account]
        if ((party_type or None) != expected_type or (party or None) != expected_party
                or ((against_type or None), (against or None)) !=
                   (("Sales Invoice", name) if account == accounting["receivable_account"] else (None, None))
                or _number(debit) != expected_debit or _number(credit) != expected_credit
                or _number(debit_currency) != expected_debit or _number(credit_currency) != expected_credit):
            raise ValueError("ledger amount or party drift")
        seen.add(account)
    stock = frappe.db.sql(
        "SELECT name FROM `tabStock Ledger Entry` "
        "WHERE voucher_type = %s AND voucher_no = %s LIMIT 1", ("Sales Invoice", name),
    )
    if stock:
        raise ValueError("unexpected stock ledger entry")


def _check_invoice(doc, visit, accounting, totals, discount, status):
    if (isinstance(doc.docstatus, bool) or not isinstance(doc.docstatus, int)
            or doc.docstatus != status or doc.doctype != "Sales Invoice"):
        raise ValueError("invoice status drift")
    for field, expected in {
        "customer": visit["customer"], "patient": visit["patient"], "company": visit["company"],
        "currency": visit["currency"], "party_account_currency": visit["currency"],
        "debit_to": accounting["receivable_account"],
        "selling_price_list": accounting["selling_price_list"],
        "price_list_currency": visit["currency"],
    }.items():
        if getattr(doc, field) != expected:
            raise ValueError("invoice identity drift")
    if str(doc.posting_date) != str(accounting["posting_date"]):
        raise ValueError("posting date drift")
    gross, cut, total = (_number(totals[k]) for k in ("gross", "discount", "total"))
    if (totals["currency"] != visit["currency"] or gross != _number(visit["price"])
            or gross <= 0 or cut < 0 or total <= 0 or gross - cut != total
            or any(value != value.quantize(Decimal("0.01")) for value in (gross, cut, total))):
        raise ValueError("invalid totals")
    for field, expected in {
        "conversion_rate": 1, "plc_conversion_rate": 1, "set_posting_time": 0,
        "total": gross, "base_total": gross, "net_total": total,
        "base_net_total": total, "grand_total": total, "base_grand_total": total,
        "discount_amount": cut, "base_discount_amount": cut, "outstanding_amount": total,
        **{field: 0 for field in (
            "is_pos", "is_return", "update_stock", "rounding_adjustment", "base_rounding_adjustment",
            "total_taxes_and_charges", "base_total_taxes_and_charges", "total_advance",
            "paid_amount", "base_paid_amount", "write_off_amount", "base_write_off_amount",
            "change_amount", "base_change_amount", "loyalty_points", "loyalty_amount",
            "redeem_loyalty_points", "allocate_advances_automatically",
            "write_off_outstanding_amount_automatically")},
    }.items():
        if _number(getattr(doc, field)) != expected:
            raise ValueError("invoice amount drift")
    if any(_number(getattr(doc, field)) not in (0, total)
           for field in ("rounded_total", "base_rounded_total")):
        raise ValueError("rounding drift")
    if discount is None:
        if cut != 0 or _number(doc.additional_discount_percentage) != 0:
            raise ValueError("discount drift")
    elif (set(discount) != {"mode", "value"} or doc.apply_discount_on != "Net Total" or cut <= 0
          or discount["mode"] not in ("amount", "percent") or _number(discount["value"]) <= 0
          or (discount["mode"] == "amount" and
              (_number(discount["value"]) != cut or _number(doc.additional_discount_percentage) != 0))
          or (discount["mode"] == "percent" and
              (_number(doc.additional_discount_percentage) != _number(discount["value"])
               or abs(cut - gross * _number(discount["value"]) / 100) > Decimal("0.005")))):
        raise ValueError("discount drift")
    if doc.taxes or doc.advances or doc.payments or len(doc.items) != 1:
        raise ValueError("unexpected invoice rows")
    if any(getattr(doc, field, None) for field in (
            "additional_discount_account", "is_cash_or_non_trade_discount",
            "insurance_coverage", "coverage_percentage")):
        raise ValueError("unsupported invoice adjustment")
    item = doc.items[0]
    if any(getattr(item, field, None) for field in (
            "pricing_rules", "discount_percentage", "discount_amount", "margin_rate_or_amount",
            "discount_account", "insurance_coverage", "coverage_percentage",
            "patient_insurance_policy", "insurance_payor", "coverage_rate", "coverage_qty",
            "insurance_coverage_amount")):
        raise ValueError("unsupported item adjustment")
    for field, expected in {
        "item_code": visit["service"], "uom": accounting["uom"], "stock_uom": accounting["uom"],
        "income_account": accounting["income_account"], "cost_center": accounting["cost_center"],
        "reference_dt": "Patient Appointment", "reference_dn": visit["appointment_id"],
    }.items():
        if getattr(item, field) != expected:
            raise ValueError("invoice item drift")
    for field, expected in {
        "qty": 1, "conversion_factor": 1, "rate": gross, "price_list_rate": gross, "amount": gross,
        "base_amount": gross, "net_amount": total, "base_net_amount": total,
        "distributed_discount_amount": cut,
    }.items():
        if _number(getattr(item, field)) != expected:
            raise ValueError("invoice item amount drift")


def post_visit_invoice(gateway, doc, visit, accounting, totals, discount, *, encounter_id=None):
    """Dispatch native lifecycle and verify persisted voucher before returning.

    Caller owns locking, complete trusted input validation, rollback and commit.
    This does not commit; caller owns the transaction and final integration.
    """
    try:
        check_posting_permissions(gateway, doc)
        if getattr(doc, "name", None):
            raise ValueError("draft already named")
        _check_invoice(doc, visit, accounting, totals, discount, 0)
        if not _item_fields_readable(gateway.frappe):
            raise ValueError("item field authorization unavailable")
    except PostingPreDispatch:
        raise
    except Exception as exc:
        raise PostingPreDispatch("posting preflight refused") from exc
    try:
        gateway.insert_visit_invoice(doc)
        name = doc.name
        if not isinstance(name, str) or not name:
            raise ValueError("missing saved invoice name")
        draft = _read(gateway, "Sales Invoice", name, _INVOICE_FIELDS)
        _check_invoice(draft, visit, accounting, totals, discount, 0)
        if (any(draft.flags.get(key) for key in ("ignore_permissions", "ignore_mandatory", "ignore_links"))
                or not gateway.frappe.has_permission(
                    "Sales Invoice", ptype="write", doc=draft, user=gateway.frappe.session.user)
                or not gateway.frappe.has_permission(
                    "Sales Invoice", ptype="submit", doc=draft, user=gateway.frappe.session.user)):
            raise ValueError("saved invoice write or submit denied")
        gateway.submit_visit_invoice(draft)
        invoice = _read(gateway, "Sales Invoice", name, _INVOICE_FIELDS)
        _check_invoice(invoice, visit, accounting, totals, discount, 1)
        appointment = _read(gateway, "Patient Appointment", visit["appointment_id"],
                            ("invoiced", "ref_sales_invoice"))
        if _number(appointment.invoiced) != 1 or appointment.ref_sales_invoice != name:
            raise ValueError("appointment marker drift")
        if encounter_id is not None:
            encounter = _read(gateway, "Patient Encounter", encounter_id, ("appointment", "invoiced"))
            if encounter.appointment != visit["appointment_id"] or _number(encounter.invoiced) != 1:
                raise ValueError("encounter marker drift")
        _check_ledger(gateway.frappe, name, visit, accounting, totals)
    except Exception as exc:
        raise PostingPostDispatch("posting requires rollback") from exc
    return name
