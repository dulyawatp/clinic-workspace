"""Read-only native accounting context; not a preview-terms authorization.

The VAT branch resolves exactly one explicitly configured, non-default,
same-company ERPNext ``Sales Taxes and Charges Template`` holding one exclusive
7% ``On Net Total`` row pointing at a verified same-company tax liability
account. No VAT eligibility and no doctor authorization are decided here; both
belong to the separate endpoint lane.
"""

from datetime import date
from decimal import Decimal

VAT_MODE_ADD = "vat_7"
VAT_MODE_NONE = "no_vat"
VAT_MODES = (VAT_MODE_ADD, VAT_MODE_NONE)
# Server-only configuration naming the one approved VAT template. It is never
# supplied by a caller, and a missing, malformed or unknown value disables the
# VAT branch instead of falling back to a native default.
VAT_TEMPLATE_KEY = "clinic_ui_visit_invoice_vat_template"
VAT_RATE = Decimal("7")
MAX_TAX_DESCRIPTION_LENGTH = 512
_MAX_NAME_LENGTH = 140

_SWITCHES = {
    "Healthcare Settings": (
        "enable_free_follow_ups",
        "show_payment_popup",
        "create_lab_test_on_si_submit",
        "create_observation_on_si_submit",
    ),
    "Selling Settings": ("enable_discount_accounting",),
}

_TAX_TEMPLATE_FIELDS = {
    "company": ("Link", "Company"), "is_default": ("Check", None),
    "disabled": ("Check", None), "taxes": ("Table", "Sales Taxes and Charges"),
}
_TAX_ROW_FIELDS = {
    "charge_type": ("Select", None), "account_head": ("Link", "Account"),
    "rate": ("Float", None), "included_in_print_rate": ("Check", None),
    "description": ("Small Text", None),
}

_FIELDS = {
    "Company": {
        "default_currency": ("Link", "Currency"),
        "default_receivable_account": ("Link", "Account"),
        "default_income_account": ("Link", "Account"),
        "cost_center": ("Link", "Cost Center"),
    },
    "Customer": {
        "customer_group": ("Link", "Customer Group"), "disabled": ("Check", None),
        "default_currency": ("Link", "Currency"), "default_price_list": ("Link", "Price List"),
    },
    "Customer Group": {"default_price_list": ("Link", "Price List")},
    "Healthcare Practitioner": {},
    "Selling Settings": {"selling_price_list": ("Link", "Price List")},
    "Price List": {
        "enabled": ("Check", None), "selling": ("Check", None), "currency": ("Link", "Currency"),
    },
    "Account": {
        "company": ("Link", "Company"), "account_currency": ("Link", "Currency"),
        "disabled": ("Check", None), "is_group": ("Check", None),
        "root_type": ("Select", None), "account_type": ("Select", None),
    },
    "Cost Center": {
        "company": ("Link", "Company"), "disabled": ("Check", None), "is_group": ("Check", None),
    },
    "Item": {
        "disabled": ("Check", None), "is_sales_item": ("Check", None),
        "is_stock_item": ("Check", None), "is_fixed_asset": ("Check", None),
        "stock_uom": ("Link", "UOM"), "variant_of": ("Link", "Item"),
    },
    "UOM": {"enabled": ("Check", None)},
    "Sales Taxes and Charges Template": _TAX_TEMPLATE_FIELDS,
}
_INTEGRITY_FIELDS = {
    "Party Account": {"company": ("Link", "Company"), "account": ("Link", "Account")},
    "GL Entry": {
        "company": ("Link", "Company"), "party_type": ("Link", "DocType"),
        "party": ("Dynamic Link", "party_type"),
    },
    "Sales Taxes and Charges Template": _TAX_TEMPLATE_FIELDS,
    "Sales Taxes and Charges": _TAX_ROW_FIELDS,
    "Tax Rule": {"company": ("Link", "Company"), "item_group": ("Link", "Item Group")},
    "Pricing Rule": {"disable": ("Check", None), "selling": ("Check", None), "company": ("Link", "Company")},
    "Promotional Scheme": {"disable": ("Check", None), "selling": ("Check", None),
                           "company": ("Link", "Company")},
    "Item Price": {"item_code": ("Link", "Item"), "price_list": ("Link", "Price List"),
                   "currency": ("Link", "Currency"), "price_list_rate": ("Currency", "currency")},
    "Item Tax": {"item_tax_template": ("Link", "Item Tax Template"), "valid_from": ("Date", None)},
}


def _schema(meta, fields):
    for name, (kind, options) in fields.items():
        field = meta.get_field(name)
        if field is None or field.fieldtype != kind:
            return False
        if options is not None and field.options != options:
            return False
    return True


def _read(gateway, doctype, name, user):
    if not isinstance(name, str) or not name.strip():
        return None
    frappe = gateway.frappe
    fields = _FIELDS[doctype]
    meta = frappe.get_meta(doctype)
    if not _schema(meta, fields) or not gateway._fields_readable(meta, fields):
        return None
    if not frappe.has_permission(doctype, ptype="read", doc=name, user=user):
        return None
    doc = frappe.get_doc(doctype, name)
    return doc if doc.name == name else None


def _check(value, expected):
    return type(value) is int and value == expected


def _unsupported_switches_disabled(gateway):
    """Check raw server-side settings, without Frappe's Check coercion."""
    try:
        frappe = gateway.frappe
        for doctype, fields in _SWITCHES.items():
            meta = frappe.get_meta(doctype)
            for name in fields:
                field = meta.get_field(name)
                if field is None or field.fieldtype != "Check":
                    return False
                rows = frappe.db.sql(
                    "SELECT value FROM tabSingles WHERE doctype=%s AND field=%s LIMIT 2",
                    (doctype, name),
                )
                if len(rows) != 1 or len(rows[0]) != 1 or rows[0][0] != "0":
                    return False
        return True
    except Exception:
        return False


def _configured_vat_template(gateway):
    """Read the one server-only approved VAT template name, or nothing."""
    name = gateway._configuration().get(VAT_TEMPLATE_KEY)
    if (type(name) is not str or not name or name != name.strip()
            or len(name) > _MAX_NAME_LENGTH or not name.isprintable()):
        return None
    return name


def _resolve_vat_tax(gateway, visit, terms):
    """Resolve the approved template's single exclusive 7% tax row and account.

    The template must be the exact configured, non-default, enabled,
    same-company record. Its one row must charge 7% on the net total without
    being included in the printed rate, and its ledger account must be an
    enabled same-company leaf Tax liability in the invoice currency.
    """
    frappe = gateway.frappe
    user = frappe.session.user
    name = _configured_vat_template(gateway)
    if name is None:
        return None
    row_meta = frappe.get_meta("Sales Taxes and Charges")
    parenttype = "Sales Taxes and Charges Template"
    allowed = set(row_meta.get_permlevel_access("read", parenttype=parenttype, user=user))
    masked = {field.fieldname for field in row_meta.get_masked_fields(parenttype=parenttype)}
    if (not _schema(row_meta, _TAX_ROW_FIELDS)
            or any(row_meta.get_field(field).permlevel not in allowed or field in masked
                   for field in _TAX_ROW_FIELDS)):
        return None
    template = _read(gateway, "Sales Taxes and Charges Template", name, user)
    if template is None:
        return None
    rows = template.taxes
    if (template.company != visit["company"] or not _check(template.is_default, 0)
            or not _check(template.disabled, 0)
            or not isinstance(rows, (list, tuple)) or len(rows) != 1):
        return None
    row = rows[0]
    rate = row.rate
    if (row.charge_type != "On Net Total" or not _check(row.included_in_print_rate, 0)
            or type(rate) not in (int, float) or Decimal(str(rate)) != VAT_RATE
            or type(row.description) is not str or not row.description.strip()
            or len(row.description) > MAX_TAX_DESCRIPTION_LENGTH
            or type(row.account_head) is not str or not row.account_head.strip()):
        return None
    account = _read(gateway, "Account", row.account_head, user)
    if (account is None or account.company != visit["company"]
            or account.account_currency != visit["currency"]
            or not _check(account.disabled, 0) or not _check(account.is_group, 0)
            or account.root_type != "Liability" or account.account_type != "Tax"
            or row.account_head in (terms["receivable_account"], terms["income_account"])):
        return None
    return name, {"charge_type": "On Net Total", "account_head": row.account_head,
                   "rate": VAT_RATE, "included_in_print_rate": 0, "description": row.description}


def resolve_visit_invoice_terms(gateway, visit, vat_mode):
    """Resolve the accounting context for one required, explicit VAT mode.

    ``vat_7`` resolves the approved template and its single tax row; ``no_vat``
    carries no template and no rows. Nothing here establishes No VAT
    eligibility or doctor authorization, and no default template is ever used.
    """
    if not isinstance(vat_mode, str) or vat_mode not in VAT_MODES:
        return None
    try:
        terms = _resolve_visit_invoice_accounting(gateway, visit)
        if terms is None:
            return None
        tax = None
        if vat_mode == VAT_MODE_ADD:
            tax = _resolve_vat_tax(gateway, visit, terms)
            if tax is None:
                return None
        result = dict(terms)
        if tax is None:
            result.update({"vat_mode": VAT_MODE_NONE, "tax_template": None,
                           "tax_rows": (), "tax_account": None, "tax_rate": None})
        else:
            template, row = tax
            result.update({"vat_mode": VAT_MODE_ADD, "tax_template": template,
                           "tax_rows": (row,), "tax_account": row["account_head"],
                           "tax_rate": VAT_RATE})
        return result
    except Exception:
        return None


def _resolve_visit_invoice_accounting(gateway, visit):
    """Resolve account baseline for an already authorized trusted visit.

    Result cannot authorize preview: unsupported settings and precision remain
    unverified. Integrity queries reveal neither matching names nor settings.
    Any same-company default or ambiguous default tax template refuses the
    baseline: native defaults must never supply a tax on their own.
    """
    try:
        frappe = gateway.frappe
        user = frappe.session.user
        if not isinstance(user, str) or not user or user == "Guest" or not isinstance(visit, dict):
            return None
        if any(not isinstance(visit.get(k), str) or not visit[k].strip()
               for k in ("company", "currency", "customer", "practitioner", "service")):
            return None
        company_name, customer_name = visit["company"], visit["customer"]
        company = _read(gateway, "Company", company_name, user)
        customer = _read(gateway, "Customer", customer_name, user)
        practitioner = _read(gateway, "Healthcare Practitioner", visit["practitioner"], user)
        if company is None or customer is None or practitioner is None:
            return None
        if (company.default_currency != visit["currency"] or not _check(customer.disabled, 0)
                or customer.default_currency not in (None, "", visit["currency"])):
            return None
        group = _read(gateway, "Customer Group", customer.customer_group, user)
        settings = _read(gateway, "Selling Settings", "Selling Settings", user)
        if group is None or settings is None:
            return None
        if customer.default_price_list not in (None, "") or group.default_price_list not in (None, ""):
            return None
        price_list = _read(gateway, "Price List", settings.selling_price_list, user)
        if (price_list is None or not _check(price_list.enabled, 1) or not _check(price_list.selling, 1)
                or price_list.currency != visit["currency"]):
            return None
        rate = visit.get("price")
        if (not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0
                or rate != rate.quantize(Decimal("0.01"))):
            return None
        posting_date = date.fromisoformat(frappe.utils.today())
        if not _unsupported_switches_disabled(gateway):
            return None
        if any(not _schema(frappe.get_meta(dt), fields) for dt, fields in _INTEGRITY_FIELDS.items()):
            return None
        if frappe.db.sql(
            "SELECT 1 FROM `tabSales Taxes and Charges Template` "
            "WHERE company=%s AND (is_default IS NULL OR is_default <> 0) LIMIT 1",
            (company_name,),
        ):
            return None
        predicates = (
            ("GL Entry", {"party_type": "Customer", "party": customer_name, "company": company_name}),
            ("Party Account", {"parenttype": "Customer", "parent": customer_name, "company": company_name}),
            ("Party Account", {"parenttype": "Customer Group", "parent": customer.customer_group,
                               "company": company_name}),
            ("Party Account", {"parenttype": "Healthcare Practitioner", "parent": visit["practitioner"],
                               "company": company_name}),
            ("Party Account", {"parentfield": "income_account", "parent": "Healthcare Settings",
                               "company": company_name}),
            ("Tax Rule", {}),
            ("Pricing Rule", {}),
            ("Promotional Scheme", {}),
            ("Item Price", {"item_code": visit["service"]}),
            ("Item Tax", {}),
        )
        for doctype, filters in predicates:
            if frappe.db.get_value(doctype, filters, "name", ignore=False, order_by=None) is not None:
                return None
        receivable_name = company.default_receivable_account
        income_name = company.default_income_account
        if receivable_name == income_name:
            return None
        receivable = _read(gateway, "Account", receivable_name, user)
        income = _read(gateway, "Account", income_name, user)
        cost = _read(gateway, "Cost Center", company.cost_center, user)
        item = _read(gateway, "Item", visit["service"], user)
        if receivable is None or income is None or cost is None or item is None:
            return None
        for account in (receivable, income):
            if (account.company != company_name or account.account_currency != visit["currency"]
                    or not _check(account.disabled, 0) or not _check(account.is_group, 0)):
                return None
        if (receivable.root_type != "Asset" or receivable.account_type != "Receivable"
                or income.root_type != "Income"
                or income.account_type not in ("Income Account", "Direct Income", "Indirect Income")
                or cost.company != company_name or not _check(cost.disabled, 0) or not _check(cost.is_group, 0)
                or not _check(item.disabled, 0) or not _check(item.is_sales_item, 1)
                or not _check(item.is_stock_item, 0) or not _check(item.is_fixed_asset, 0)
                or item.variant_of not in (None, "")):
            return None
        uom = _read(gateway, "UOM", item.stock_uom, user)
        if uom is None or not _check(uom.enabled, 1):
            return None
        return {
            "company": company_name, "currency": visit["currency"], "customer": customer_name,
            "income_account": income_name, "receivable_account": receivable_name,
            "cost_center": company.cost_center, "item": visit["service"], "uom": item.stock_uom,
            "company_currency": visit["currency"], "party_account_currency": receivable.account_currency,
            "posting_date": posting_date, "selling_price_list": settings.selling_price_list,
            "unit_rate": rate,
        }
    except Exception:
        return None
