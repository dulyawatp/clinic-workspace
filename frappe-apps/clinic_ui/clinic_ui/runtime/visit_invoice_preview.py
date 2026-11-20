"""In-memory native preview; not an authorization or persistence boundary."""

from datetime import date
from decimal import Decimal, InvalidOperation
import json

from clinic_ui.runtime.visit_invoice import resolve_visit_invoice
from clinic_ui.runtime.visit_invoice_accounting import MAX_TAX_DESCRIPTION_LENGTH, resolve_visit_invoice_terms


def item_tax_rate_ok(value, mode, account):
    """Accept only serialized native tax rates, never an Item Tax override."""
    if value is None or value == "":
        return True
    if type(value) is not str:
        return False
    class ObjectPairs(list):
        pass
    try:
        pairs = json.loads(value, object_pairs_hook=ObjectPairs, parse_float=Decimal)
    except (ValueError, TypeError):
        return False
    if not isinstance(pairs, ObjectPairs):
        return False
    if not pairs:
        return True
    return (mode == "vat_7" and len(pairs) == 1 and isinstance(pairs[0], tuple)
            and pairs[0][0] == account and type(pairs[0][1]) in (int, Decimal)
            and pairs[0][1] == 7)


def _money(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result != result.quantize(Decimal("0.01")):
            raise ValueError("unrepresentable money")
        return result
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("unrepresentable money") from exc


def preview_visit_invoice(frappe, visit, discount, terms):
    """Calculate only; callers must re-resolve terms and authorize separately."""
    keys = ("appointment_id", "practitioner", "patient", "customer",
            "company", "currency", "service")
    term_keys = ("company", "currency", "customer", "income_account", "receivable_account",
                 "cost_center", "item", "uom", "company_currency", "party_account_currency",
                 "selling_price_list")
    if (not isinstance(terms, dict)
            or any(not isinstance(terms.get(k), str) or not terms[k].strip() for k in term_keys)
            or type(terms.get("posting_date")) is not date
            or any(not isinstance(visit.get(k), str) or not visit[k] for k in keys)
            or terms["company_currency"] != visit["currency"]
            or terms["party_account_currency"] != visit["currency"]
            or any(terms[k] != visit[k] for k in ("company", "currency", "customer"))
            or terms["item"] != visit["service"]
            or not isinstance(terms.get("unit_rate"), Decimal)
            or terms["unit_rate"] != visit["price"]):
        raise ValueError("unsupported preview terms")
    if any(key not in terms for key in ("vat_mode", "tax_template", "tax_rows", "tax_account", "tax_rate")):
        raise ValueError("unsupported tax terms")
    mode = terms.get("vat_mode")
    if mode == "no_vat":
        if (terms.get("tax_template") is not None or terms.get("tax_rows") != ()
                or terms.get("tax_account") is not None or terms.get("tax_rate") is not None):
            raise ValueError("unsupported tax terms")
    elif mode == "vat_7":
        account, template = terms.get("tax_account"), terms.get("tax_template")
        rows = terms.get("tax_rows")
        description = rows[0].get("description") if type(rows) is tuple and len(rows) == 1 and type(rows[0]) is dict else None
        expected = {"charge_type": "On Net Total", "account_head": account,
                    "rate": Decimal("7"), "included_in_print_rate": 0, "description": description}
        if (type(template) is not str or not template.strip() or template != template.strip()
                or type(account) is not str or not account.strip() or account != account.strip()
                or account in (terms["income_account"], terms["receivable_account"])
                or type(terms.get("tax_rate")) is not Decimal or terms["tax_rate"] != Decimal("7")
                or type(rows) is not tuple or len(rows) != 1 or type(rows[0]) is not dict
                or type(description) is not str or not description.strip()
                or len(description) > MAX_TAX_DESCRIPTION_LENGTH
                or rows[0] != expected or type(rows[0].get("rate")) is not Decimal
                or type(rows[0].get("included_in_print_rate")) is not int):
            raise ValueError("unsupported tax terms")
    else:
        raise ValueError("unsupported tax terms")
    price = _money(visit["price"])
    if price <= 0:
        raise ValueError("free service")
    values = {
        "doctype": "Sales Invoice", "docstatus": 0, "customer": visit["customer"],
        "patient": visit["patient"], "company": visit["company"],
        "currency": visit["currency"], "conversion_rate": 1.0, "is_pos": 0,
        "debit_to": terms["receivable_account"],
        "selling_price_list": terms["selling_price_list"],
        "price_list_currency": visit["currency"], "plc_conversion_rate": 1.0,
        "update_stock": 0,
        "is_return": 0, "allocate_advances_automatically": 0,
        "write_off_outstanding_amount_automatically": 0,
        "party_account_currency": terms["party_account_currency"],
        "posting_date": terms["posting_date"], "set_posting_time": 0,
        "total_advance": 0.0, "write_off_amount": 0.0,
        "base_write_off_amount": 0.0, "paid_amount": 0.0,
        "base_paid_amount": 0.0, "change_amount": 0.0, "base_change_amount": 0.0,
        "loyalty_points": 0.0, "loyalty_amount": 0.0, "redeem_loyalty_points": 0,
        "advances": [], "payments": [],
        "discount_amount": 0.0, "additional_discount_percentage": 0.0,
        "taxes_and_charges": terms.get("tax_template"),
        "taxes": ([{**terms["tax_rows"][0], "rate": float(terms["tax_rate"])}]
                  if mode == "vat_7" else []),
        "items": [{"item_code": visit["service"], "qty": 1.0, "rate": float(price),
                   "price_list_rate": float(price), "distributed_discount_amount": 0.0,
                   "income_account": terms["income_account"], "cost_center": terms["cost_center"],
                   "uom": terms["uom"], "stock_uom": terms["uom"], "conversion_factor": 1.0,
                   "reference_dt": "Patient Appointment",
                   "reference_dn": visit["appointment_id"]}],
    }
    requested = None
    if discount is not None:
        if (not isinstance(discount, dict) or set(discount) != {"mode", "value"}
                or discount["mode"] not in ("amount", "percent")
                or not isinstance(discount["value"], Decimal)
                or not discount["value"].is_finite() or discount["value"] <= 0
                or discount["value"] > (100 if discount["mode"] == "percent" else price)):
            raise ValueError("invalid discount")
        requested = discount["value"]
        values["apply_discount_on"] = "Net Total"
        values["discount_amount" if discount["mode"] == "amount"
               else "additional_discount_percentage"] = float(requested)
    doc = frappe.get_doc(values)
    header_money = ("total", "net_total", "grand_total", "discount_amount", "base_total",
                    "base_net_total", "base_grand_total", "base_discount_amount", "rounded_total",
                    "base_rounded_total", "rounding_adjustment", "base_rounding_adjustment",
                    "outstanding_amount", "total_taxes_and_charges", "base_total_taxes_and_charges",
                    "total_advance", "write_off_amount", "base_write_off_amount",
                    "paid_amount", "base_paid_amount", "change_amount", "base_change_amount",
                    "loyalty_amount")
    row_money = ("rate", "price_list_rate", "amount", "base_amount", "net_amount",
                 "base_net_amount", "distributed_discount_amount")
    tax_money = ("tax_amount", "base_tax_amount", "tax_amount_after_discount_amount",
                 "base_tax_amount_after_discount_amount", "total", "base_total")
    def precision_ok():
        return (all(type(doc.precision(field)) is int and doc.precision(field) == 2
                    for field in header_money)
                and len(doc.items) == 1
                and all(type(doc.items[0].precision(field)) is int
                         and doc.items[0].precision(field) == 2 for field in row_money)
                and all(type(row.precision(field)) is int and row.precision(field) == 2
                        for row in doc.taxes for field in tax_money))
    def tax_identity_ok():
        if (doc.taxes_and_charges or None) != terms.get("tax_template"):
            return False
        if mode != "vat_7":
            return not doc.taxes
        if len(doc.taxes) != 1:
            return False
        row = doc.taxes[0]
        return (row.charge_type == "On Net Total" and row.account_head == terms["tax_account"]
                and row.description == terms["tax_rows"][0]["description"]
                and not isinstance(row.rate, bool) and Decimal(str(row.rate)) == Decimal("7")
                and type(row.included_in_print_rate) is int and row.included_in_print_rate == 0
                and not getattr(row, "included_in_paid_amount", 0)
                and not getattr(row, "add_deduct_tax", None) in ("Deduct",))
    if not precision_ok():
        raise ValueError("unsupported native precision")
    if (doc.doctype != "Sales Invoice" or isinstance(doc.docstatus, bool)
            or not isinstance(doc.docstatus, int) or doc.docstatus != 0 or not tax_identity_ok()
            or not item_tax_rate_ok(getattr(doc.items[0], "item_tax_rate", None), "no_vat", None)):
        raise ValueError("native tax drift")
    doc.calculate_taxes_and_totals()
    gross, cut, total = (_money(getattr(doc, field)) for field in
                         ("total", "discount_amount", "grand_total"))
    rounded, base_rounded = (_money(getattr(doc, field)) for field in
                             ("rounded_total", "base_rounded_total"))
    net = _money(doc.net_total)
    tax = _money(doc.total_taxes_and_charges)
    if (not precision_ok() or not tax_identity_ok()
             or doc.doctype != "Sales Invoice" or isinstance(doc.docstatus, bool)
             or not isinstance(doc.docstatus, int) or doc.docstatus != 0
            or doc.customer != visit["customer"] or doc.patient != visit["patient"]
            or doc.company != visit["company"] or doc.currency != visit["currency"]
            or doc.party_account_currency != visit["currency"]
            or str(doc.posting_date) != str(terms["posting_date"])
            or doc.set_posting_time != 0
            or doc.debit_to != terms["receivable_account"]
            or doc.selling_price_list != terms["selling_price_list"]
            or doc.price_list_currency != visit["currency"] or doc.plc_conversion_rate != 1
            or doc.conversion_rate != 1 or doc.is_pos != 0 or doc.update_stock != 0
            or doc.is_return != 0 or doc.allocate_advances_automatically != 0
            or doc.write_off_outstanding_amount_automatically != 0
            or doc.advances or doc.payments
            or any(_money(getattr(doc, field)) != 0 for field in
                   ("total_advance", "write_off_amount", "base_write_off_amount",
                    "paid_amount", "base_paid_amount", "change_amount",
                    "base_change_amount", "loyalty_points", "loyalty_amount",
                    "rounding_adjustment", "base_rounding_adjustment"))
            or doc.redeem_loyalty_points != 0
            or doc.items[0].item_code != visit["service"]
            or doc.items[0].income_account != terms["income_account"]
            or doc.items[0].cost_center != terms["cost_center"]
            or doc.items[0].uom != terms["uom"] or doc.items[0].stock_uom != terms["uom"]
            or doc.items[0].conversion_factor != 1
            or doc.items[0].reference_dt != "Patient Appointment"
            or doc.items[0].reference_dn != visit["appointment_id"]
            or doc.items[0].qty != 1 or _money(doc.items[0].rate) != price
            or _money(doc.items[0].price_list_rate) != price
            or any(getattr(doc.items[0], field, None) for field in
                   ("pricing_rules", "item_tax_template", "discount_percentage", "discount_amount",
                    "margin_rate_or_amount", "discount_account", "insurance_coverage",
                    "coverage_percentage", "patient_insurance_policy", "insurance_payor",
                     "coverage_rate", "coverage_qty", "insurance_coverage_amount"))
              or not item_tax_rate_ok(getattr(doc.items[0], "item_tax_rate", None), mode, terms["tax_account"])
            or any(getattr(doc, field, None) for field in
                   ("additional_discount_account", "is_cash_or_non_trade_discount",
                    "insurance_coverage", "coverage_percentage"))
             or gross != price or total <= 0 or net <= 0 or cut < 0 or gross - cut != net
             or total != net + tax or tax < 0
             or _money(doc.base_total) != gross or _money(doc.base_net_total) != net
            or _money(doc.base_grand_total) != total
            or _money(doc.base_discount_amount) != cut
            or _money(doc.items[0].amount) != gross
            or _money(doc.items[0].base_amount) != gross
             or _money(doc.items[0].net_amount) != net
             or _money(doc.items[0].base_net_amount) != net
            or _money(doc.items[0].distributed_discount_amount) != cut
            or (rounded or total) != total
            or (base_rounded or total) != total
            or (doc.outstanding_amount is not None and _money(doc.outstanding_amount) != total)
             or _money(doc.base_total_taxes_and_charges) != tax
             or (mode != "vat_7" and tax != 0)
             or (mode == "vat_7" and (
                 tax <= 0 or abs(tax - net * Decimal("0.07")) > Decimal("0.005")
                 or any(_money(getattr(doc.taxes[0], field)) != tax for field in tax_money[:4])
                 or any(_money(getattr(doc.taxes[0], field)) != total for field in tax_money[4:])))
             or (discount is None and cut != 0)
             or ((discount is None or discount["mode"] == "amount")
                 and Decimal(str(doc.additional_discount_percentage)) != 0)
            or (discount is not None and (cut <= 0 or doc.apply_discount_on != "Net Total"))
            or (discount is not None and discount["mode"] == "amount" and cut != requested)
            or (discount is not None and discount["mode"] == "percent"
                and (Decimal(str(doc.additional_discount_percentage)) != requested
                     or abs(cut - gross * requested / 100) > Decimal("0.005")))):
        raise ValueError("native preview drift")
    totals = {"currency": visit["currency"], "gross": f"{gross:.2f}",
              "discount": f"{cut:.2f}", "net": f"{net:.2f}",
              "vat": f"{tax:.2f}", "total": f"{total:.2f}"}
    return doc, totals


def prepare_visit_invoice(gateway, appointment_id, discount, vat_mode):
    """Compose an unsaved draft with required explicit ``vat_mode``.

    Explicit mode returns terms with vat_mode, tax_template, tax_rows,
    tax_account and tax_rate; totals include currency, gross, discount, net,
    vat and total.
    Authorization and No VAT eligibility remain caller responsibilities.
    """
    try:
        visit = resolve_visit_invoice(gateway, appointment_id)
        if visit is None:
            return None
        accounting = resolve_visit_invoice_terms(gateway, visit, vat_mode)
        if not isinstance(accounting, dict) or accounting.get("vat_mode") != vat_mode:
            return None
        doc, totals = preview_visit_invoice(gateway.frappe, visit, discount, accounting)
        return visit, accounting, doc, totals
    except Exception:
        return None
