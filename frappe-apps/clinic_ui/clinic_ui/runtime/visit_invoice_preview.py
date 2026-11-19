"""In-memory native preview; not an authorization or persistence boundary."""

from datetime import date
from decimal import Decimal, InvalidOperation

from clinic_ui.runtime.visit_invoice import resolve_visit_invoice
from clinic_ui.runtime.visit_invoice_accounting import resolve_visit_invoice_accounting


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
    price = _money(visit["price"])
    if price <= 0:
        raise ValueError("free service")
    values = {
        "doctype": "Sales Invoice", "customer": visit["customer"],
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
    def precision_ok():
        return (all(type(doc.precision(field)) is int and doc.precision(field) == 2
                    for field in header_money)
                and len(doc.items) == 1
                and all(type(doc.items[0].precision(field)) is int
                        and doc.items[0].precision(field) == 2 for field in row_money))
    if not precision_ok():
        raise ValueError("unsupported native precision")
    doc.calculate_taxes_and_totals()
    gross, cut, total = (_money(getattr(doc, field)) for field in
                         ("total", "discount_amount", "grand_total"))
    rounded, base_rounded = (_money(getattr(doc, field)) for field in
                             ("rounded_total", "base_rounded_total"))
    if (not precision_ok() or doc.taxes
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
                   ("pricing_rules", "discount_percentage", "discount_amount",
                    "margin_rate_or_amount", "discount_account", "insurance_coverage",
                    "coverage_percentage", "patient_insurance_policy", "insurance_payor",
                    "coverage_rate", "coverage_qty", "insurance_coverage_amount"))
            or any(getattr(doc, field, None) for field in
                   ("additional_discount_account", "is_cash_or_non_trade_discount",
                    "insurance_coverage", "coverage_percentage"))
            or gross != price or total <= 0 or cut < 0 or gross - cut != total
            or _money(doc.net_total) != total
            or _money(doc.base_total) != gross or _money(doc.base_net_total) != total
            or _money(doc.base_grand_total) != total
            or _money(doc.base_discount_amount) != cut
            or _money(doc.items[0].amount) != gross
            or _money(doc.items[0].base_amount) != gross
            or _money(doc.items[0].net_amount) != total
            or _money(doc.items[0].base_net_amount) != total
            or _money(doc.items[0].distributed_discount_amount) != cut
            or (rounded or total) != total
            or (base_rounded or total) != total
            or (doc.outstanding_amount is not None and _money(doc.outstanding_amount) != total)
            or _money(doc.total_taxes_and_charges) != 0
            or _money(doc.base_total_taxes_and_charges) != 0
            or (discount is None and cut != 0)
            or (discount is not None and (cut <= 0 or doc.apply_discount_on != "Net Total"))
            or (discount is not None and discount["mode"] == "amount" and cut != requested)
            or (discount is not None and discount["mode"] == "percent"
                and (Decimal(str(doc.additional_discount_percentage)) != requested
                     or abs(cut - gross * requested / 100) > Decimal("0.005")))):
        raise ValueError("native preview drift")
    return doc, {"currency": visit["currency"], "gross": f"{gross:.2f}",
                 "discount": f"{cut:.2f}", "total": f"{total:.2f}"}


def prepare_visit_invoice(gateway, appointment_id, discount):
    """Compose trusted read-only resolvers with a checked unsaved native draft."""
    try:
        visit = resolve_visit_invoice(gateway, appointment_id)
        if visit is None:
            return None
        accounting = resolve_visit_invoice_accounting(gateway, visit)
        if accounting is None:
            return None
        doc, totals = preview_visit_invoice(gateway.frappe, visit, discount, accounting)
        return visit, accounting, doc, totals
    except Exception:
        return None
