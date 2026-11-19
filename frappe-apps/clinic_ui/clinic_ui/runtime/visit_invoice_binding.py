"""Private preview binding. No posting, authorization bypass or endpoint."""

import json
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from clinic_ui.runtime.visit_invoice_preview import prepare_visit_invoice


@dataclass(frozen=True)
class BoundVisitInvoice:
    doc: object
    _claims_json: str
    _visit: tuple
    _accounting: tuple
    _totals: tuple
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


def _freeze(facts):
    for key, value in facts.items():
        if type(key) is not str:
            raise ValueError("invalid fact key")
        _scalar(value)
    return tuple(sorted(facts.items()))


def prepare_bound_visit_invoice(gateway, appointment_id, discount):
    """Resolve current facts and retain checked draft for a future posting adapter."""
    try:
        actor, sid = _identity(gateway)
        prepared = prepare_visit_invoice(gateway, appointment_id, discount)
        if prepared is None or _identity(gateway) != (actor, sid):
            return None
        visit, accounting, doc, totals = prepared
        if visit["appointment_id"] != appointment_id:
            return None
        terms = {key: _scalar(value) for key, value in accounting.items()}
        for key in ("patient", "customer", "practitioner", "company", "service"):
            terms[key] = _scalar(visit[key])
        terms["rate"] = format(visit["price"], ".2f")
        claims = {
            "actor": actor, "session": sid, "date": accounting["posting_date"].isoformat(),
            "appointment": appointment_id, "encounter": visit.get("encounter_id"),
            "discount": ({"mode": "none", "value": "0"} if discount is None else
                         {"mode": discount["mode"], "value": str(discount["value"])}),
            "terms": terms, "totals": dict(totals),
        }
        return BoundVisitInvoice(
            doc, json.dumps(claims, sort_keys=True, separators=(",", ":")),
            _freeze(visit), _freeze(accounting), _freeze(totals),
            None if discount is None else _freeze(discount),
        )
    except (AttributeError, KeyError, TypeError, ValueError, ArithmeticError):
        return None


def build_visit_invoice_preview(gateway, appointment_id, discount):
    bound = prepare_bound_visit_invoice(gateway, appointment_id, discount)
    if bound is None:
        return None
    claims = bound.claims()
    return {"totals": dict(claims["totals"]), "claims": claims}


def prepare_visit_invoice_submission(gateway, appointment_id, discount):
    bound = prepare_bound_visit_invoice(gateway, appointment_id, discount)
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
    visit = dict(bound._visit)
    invoice_id = post_visit_invoice(
        gateway, bound.doc, visit, dict(bound._accounting), dict(bound._totals),
        None if bound._discount is None else dict(bound._discount),
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
