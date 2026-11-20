"""Private preview binding. No posting, authorization bypass or endpoint."""

import json
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from clinic_ui.runtime.visit_invoice_preview import prepare_visit_invoice


VAT_MODES = ("vat_7", "no_vat")
_VAT_FIELDS = ("vat_mode", "tax_template", "tax_rows", "tax_account", "tax_rate")
_TAX_ROW_KEYS = {"charge_type", "account_head", "rate", "included_in_print_rate", "description"}
_TOTALS = ("currency", "gross", "discount", "net", "vat", "total")


@dataclass(frozen=True)
class BoundVisitInvoice:
    doc: object
    _claims_json: str
    _visit: object
    _accounting: object
    _totals: object
    _discount: object

    def claims(self):
        # Reproject immutable facts; mutations to returned dictionaries do not persist.
        return json.loads(self._claims_json)


def _identity(gateway):
    session = gateway.frappe.session
    actor, sid = session.user, session.sid
    if (type(actor) is not str or not actor.strip() or actor != actor.strip()
            or actor == "Guest" or type(sid) is not str or not sid.strip()
            or sid != sid.strip()):
        raise ValueError("invalid session")
    return actor, sid


def _scalar(value):
    if type(value) is str and value:
        return value
    if type(value) is int or type(value) is bool or value is None:
        return value
    if type(value) is date:
        return value.isoformat()
    if type(value) is Decimal and value.is_finite():
        return format(value, "f")
    raise ValueError("unsupported accounting term")


def _key(value):
    if type(value) is not str:
        raise ValueError("invalid fact key")
    return value


def _freeze(value):
    """Copy trusted facts into immutable tagged tuples, keeping their native type."""
    if type(value) is dict:
        if len(value) > 128:
            raise ValueError("mapping too large")
        return ("mapping", tuple(sorted((_key(key), _freeze(item)) for key, item in value.items())))
    if type(value) in (list, tuple):
        if len(value) > 128:
            raise ValueError("sequence too long")
        return ("list" if type(value) is list else "tuple", tuple(_freeze(item) for item in value))
    if type(value) is Decimal:
        return ("decimal", _scalar(value))
    if type(value) is date:
        return ("date", value.isoformat())
    return ("scalar", _scalar(value))


def _thaw(frozen):
    """Rebuild a frozen fact tree while preserving native types."""
    if type(frozen) is not tuple or len(frozen) != 2 or type(frozen[0]) is not str:
        raise ValueError("invalid frozen fact")
    tag, value = frozen
    if tag == "mapping":
        return {key: _thaw(item) for key, item in value}
    if tag == "tuple":
        return tuple(_thaw(item) for item in value)
    if tag == "list":
        return [_thaw(item) for item in value]
    if tag == "decimal":
        result = Decimal(value)
        if not result.is_finite():
            raise ValueError("invalid frozen decimal")
        return result
    if tag == "date":
        return date.fromisoformat(value)
    if tag == "scalar":
        return value
    raise ValueError("invalid frozen fact")


def _vat_facts(accounting):
    """Bind the actual vetted mode with its template, account, rate and rows."""
    mode = accounting.get("vat_mode")
    if type(mode) is not str or mode not in VAT_MODES:
        raise ValueError("invalid vat mode")
    template, account, rate = (accounting.get(field) for field in
                              ("tax_template", "tax_account", "tax_rate"))
    rows = accounting.get("tax_rows")
    if type(rows) not in (list, tuple):
        raise ValueError("invalid tax rows")
    if mode == "no_vat":
        if template is not None or account is not None or rate is not None or len(rows) != 0:
            raise ValueError("invalid no-vat terms")
        return {"mode": mode, "template": None, "account": None, "rate": None, "rows": []}
    for value in (template, account):
        if type(value) is not str or not value.strip() or value != value.strip():
            raise ValueError("invalid tax identity")
    if type(rate) is not Decimal or not rate.is_finite() or rate <= 0:
        raise ValueError("invalid tax rate")
    if len(rows) != 1 or type(rows[0]) is not dict or set(rows[0]) != _TAX_ROW_KEYS:
        raise ValueError("invalid tax rows")
    row = rows[0]
    if (row["charge_type"] != "On Net Total"
            or type(row["description"]) is not str or not row["description"].strip()
            or len(row["description"]) > 512
            or type(row["account_head"]) is not str or row["account_head"] != account
            or type(row["rate"]) is not Decimal or not row["rate"].is_finite()
            or row["rate"] != rate
            or type(row["included_in_print_rate"]) is not int
            or row["included_in_print_rate"] != 0):
        raise ValueError("invalid tax row")
    return {"mode": mode, "template": template, "account": account, "rate": _scalar(rate),
            "rows": [{"charge_type": row["charge_type"], "account_head": account,
                      "rate": _scalar(rate),
                      "included_in_print_rate": row["included_in_print_rate"],
                      "description": row["description"]}]}


def _claim_totals(totals):
    """Project exact currency, gross, discount, net, VAT and payable facts."""
    if type(totals) is not dict or set(totals) != set(_TOTALS):
        raise ValueError("invalid totals")
    result = {field: _scalar(totals[field]) for field in _TOTALS}
    gross, cut, net, vat, total = (Decimal(result[field]) for field in _TOTALS[1:])
    if not all(value.is_finite() for value in (gross, cut, net, vat, total)) or gross - cut != net or net + vat != total:
        raise ValueError("inconsistent totals")
    return result


def prepare_bound_visit_invoice(gateway, appointment_id, discount, vat_mode):
    """Resolve current facts and retain checked draft for a future posting adapter."""
    if type(vat_mode) is not str or vat_mode not in VAT_MODES:
        return None
    try:
        actor, sid = _identity(gateway)
        prepared = prepare_visit_invoice(gateway, appointment_id, discount, vat_mode)
        if prepared is None or _identity(gateway) != (actor, sid):
            return None
        visit, accounting, doc, totals = prepared
        if (visit["appointment_id"] != appointment_id
                or accounting.get("vat_mode") != vat_mode):
            return None
        vat = _vat_facts(accounting)
        claim_totals = _claim_totals(totals)
        if vat["mode"] == "no_vat" and claim_totals["vat"] != "0.00":
            return None
        terms = {key: _scalar(value) for key, value in accounting.items()
                 if key not in _VAT_FIELDS}
        for key in ("patient", "customer", "practitioner", "company", "service"):
            terms[key] = _scalar(visit[key])
        terms["rate"] = format(visit["price"], ".2f")
        claims = {
            "actor": actor, "session": sid, "date": accounting["posting_date"].isoformat(),
            "appointment": appointment_id, "encounter": visit.get("encounter_id"),
            "discount": ({"mode": "none", "value": "0"} if discount is None else
                         {"mode": discount["mode"], "value": str(discount["value"])}),
            "vat": vat, "terms": terms, "totals": claim_totals,
        }
        return BoundVisitInvoice(
            doc, json.dumps(claims, sort_keys=True, separators=(",", ":")),
            _freeze(visit), _freeze(accounting), _freeze(totals),
            None if discount is None else _freeze(discount),
        )
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        return None


def build_visit_invoice_preview(gateway, appointment_id, discount, vat_mode):
    bound = prepare_bound_visit_invoice(gateway, appointment_id, discount, vat_mode)
    if bound is None:
        return None
    claims = bound.claims()
    return {"totals": dict(claims["totals"]), "claims": claims}


def prepare_visit_invoice_submission(gateway, appointment_id, discount, vat_mode):
    bound = prepare_bound_visit_invoice(gateway, appointment_id, discount, vat_mode)
    if bound is None:
        return None
    claims = bound.claims()
    return {"draft": bound, "totals": dict(claims["totals"]), "claims": claims}


def post_visit_invoice_and_verify_facts(gateway, prepared):
    from clinic_ui.core.text import is_valid_text
    from clinic_ui.runtime.visit_invoice_posting import post_visit_invoice

    if type(prepared) is not dict or set(prepared) != {"draft", "totals", "claims"}:
        raise ValueError("invalid prepared invoice")
    bound = prepared["draft"]
    if type(bound) is not BoundVisitInvoice:
        raise ValueError("invalid bound invoice")
    claims = bound.claims()
    if (prepared["claims"] != claims or prepared["totals"] != claims["totals"]
            or _identity(gateway) != (claims["actor"], claims["session"])):
        raise ValueError("prepared invoice context changed")
    visit = _thaw(bound._visit)
    accounting = _thaw(bound._accounting)
    if (type(visit) is not dict or type(accounting) is not dict
            or accounting.get("vat_mode") != claims["vat"]["mode"]):
        raise ValueError("prepared invoice context changed")
    invoice_id = post_visit_invoice(
        gateway, bound.doc, visit, accounting, _thaw(bound._totals),
        None if bound._discount is None else _thaw(bound._discount),
        encounter_id=visit.get("encounter_id"),
    )
    if not is_valid_text(invoice_id, 140) or invoice_id != invoice_id.strip():
        raise ValueError("invalid posted invoice")
    return {"invoice_id": invoice_id, "docstatus": 1, "persisted_facts_verified": True}


def native_create_submit_allowed(gateway):
    try:
        actor, _ = _identity(gateway)
        return all(gateway.frappe.has_permission("Sales Invoice", ptype=permission, user=actor) is True
                   for permission in ("create", "submit"))
    except Exception:
        return False


def discount_allowed(gateway):
    try:
        actor, _ = _identity(gateway)
        configured = gateway._configuration().get("clinic_ui_visit_invoice_discount_user")
        return (actor != "Administrator" and type(configured) is str and configured == actor)
    except Exception:
        return False


def token_key(gateway):
    secret = gateway._configuration().get("clinic_ui_visit_invoice_preview_secret")
    if type(secret) is not str:
        raise ValueError("missing preview secret")
    key = secret.encode("utf-8")
    if len(key) < 32:
        raise ValueError("invalid preview secret")
    return key


def clock():
    return int(time.time())
