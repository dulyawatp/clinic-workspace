"""Offline-only visit invoice preview orchestration; no native adapter is wired."""

from decimal import Decimal
import sys

from clinic_ui.core import visit_invoice_contract, visit_invoice_token
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import common
from clinic_ui.runtime import visit_invoice as runtime


VAT_MODES = ("vat_7", "no_vat")
_TOTALS = ("currency", "gross", "discount", "net", "vat", "total")


def _vat_mode(body):
    """Accept only the literal modes the browser chose; never infer or default."""
    vat_mode = body.get("vat_mode")
    return vat_mode if type(vat_mode) is str and vat_mode in VAT_MODES else None


def _no_vat_allowed(gateway, appointment, vat_mode):
    """No VAT needs positive server authority on every request that carries it."""
    if vat_mode != "no_vat":
        return True
    try:
        return gateway.visit_invoice_no_vat_allowed(appointment) is True
    except Exception:
        return False


def _checked_totals(claims, vat_mode):
    """Re-derive preview totals from the bound claims instead of trusting them."""
    vat = claims.get("vat")
    totals = claims.get("totals")
    if type(vat) is not dict or vat.get("mode") != vat_mode or type(totals) is not dict:
        raise ValueError("preview binding mismatch")
    if set(totals) != set(_TOTALS):
        raise ValueError("invalid preview totals")
    gross, cut, net, vat_amount, total = (
        Decimal(totals[field]) for field in ("gross", "discount", "net", "vat", "total"))
    if (gross <= 0 or cut < 0 or net <= 0 or vat_amount < 0 or total <= 0
            or gross - cut != net or net + vat_amount != total
            or (vat_mode == "no_vat" and vat_amount != 0)
            or (claims["discount"]["mode"] == "none" and cut != 0)
            or (claims["discount"]["mode"] != "none" and cut <= 0)):
        raise ValueError("invalid preview totals")
    return totals


def preview(gateway, body, csrf_token=None):
    common.begin(gateway)
    _settings, view, role = common.authorize(gateway, frozenset({"DOC"}))
    if (role != "DOC" or runtime.visit_invoice_write_enabled(gateway, view.user) is not True
            or not gateway.csrf_token_valid(csrf_token)):
        gateway.deny()
    if (not isinstance(body, dict) or not {"appointment_id", "vat_mode"} <= set(body)
            or not set(body) <= {"appointment_id", "discount", "vat_mode"}):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    appointment, vat_mode = body["appointment_id"], _vat_mode(body)
    if (not is_valid_text(appointment, 140) or appointment != appointment.strip()
            or vat_mode is None):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        discount = visit_invoice_contract.parse_discount(body)
    except ValueError:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        allowed = (gateway.visit_invoice_native_create_submit_allowed() is True
                   and (discount is None or gateway.visit_invoice_discount_allowed() is True))
    except Exception:
        allowed = False
    if not allowed or not _no_vat_allowed(gateway, appointment, vat_mode):
        gateway.deny()
    try:
        result = gateway.build_visit_invoice_preview(appointment, discount, vat_mode)
        if type(result) is not dict or set(result) != {"totals", "claims"}:
            raise ValueError("invalid preview")
        totals, claims = result["totals"], result["claims"]
        expected_discount = ({"mode": "none", "value": "0"} if discount is None
                             else {"mode": discount["mode"], "value": str(discount["value"])})
        if (type(claims) is not dict or claims.get("appointment") != appointment
                or claims.get("actor") != view.user or claims.get("discount") != expected_discount
                or claims.get("totals") != totals):
            raise ValueError("preview binding mismatch")
        totals = _checked_totals(claims, vat_mode)
        token = visit_invoice_token.issue(
            gateway.visit_invoice_token_key(), claims, now=gateway.visit_invoice_clock()
        )
        return {"currency": totals["currency"], "gross": totals["gross"],
                "discount": totals["discount"], "net": totals["net"],
                "vat": totals["vat"], "total": totals["total"], "preview_token": token}
    except Exception:
        gateway.deny()


class KnownMutationFailure(Exception):
    """Native worker confirms failure before commit; transaction needs rollback."""


def _submit_in_lane(gateway, appointment, discount, vat_mode, token, actor, dispatched):
    if not _no_vat_allowed(gateway, appointment, vat_mode):
        return "denied", None
    try:
        prepared = gateway.prepare_visit_invoice_submission(appointment, discount, vat_mode)
        if type(prepared) is not dict or set(prepared) != {"draft", "totals", "claims"}:
            return "denied", None
        claims, totals = prepared["claims"], prepared["totals"]
        expected = ({"mode": "none", "value": "0"} if discount is None
                    else {"mode": discount["mode"], "value": str(discount["value"])})
        if (prepared["draft"] is None or type(claims) is not dict
                or claims.get("appointment") != appointment or claims.get("actor") != actor
                or claims.get("discount") != expected or claims.get("totals") != totals
                or not visit_invoice_token.verify(gateway.visit_invoice_token_key(), claims, token,
                                                  now=gateway.visit_invoice_clock())):
            return "denied", None
        if _checked_totals(claims, vat_mode) != totals:
            return "denied", None
    except Exception:
        return "denied", None

    dispatched[0] = True
    try:
        posted = gateway.post_visit_invoice_and_verify_facts(prepared)
    except KnownMutationFailure:
        gateway.rollback_visit_invoice()
        return "denied", None
    except Exception:
        gateway.rollback_visit_invoice()
        return "uncertain", None
    if (type(posted) is not dict
            or set(posted) != {"invoice_id", "docstatus", "persisted_facts_verified"}
            or type(posted["docstatus"]) is not int or posted["docstatus"] != 1
            or posted["persisted_facts_verified"] is not True
            or not is_valid_text(posted["invoice_id"], 140)
            or posted["invoice_id"] != posted["invoice_id"].strip()):
        gateway.rollback_visit_invoice()
        return "denied", None
    try:
        gateway.commit_visit_invoice()
    except Exception:
        gateway.rollback_visit_invoice()
        return "uncertain", None
    try:
        result = gateway.view_submitted_visit_invoice(appointment, posted["invoice_id"])
        if type(result) is not dict or result != {"invoice_id": posted["invoice_id"]}:
            return "uncertain", None
        return "success", result
    except Exception:
        return "uncertain", None


def submit(gateway, body, csrf_token=None):
    """Submit only inside an adapter-provided serialized native transaction."""
    common.begin(gateway)
    _settings, view, role = common.authorize(gateway, frozenset({"DOC"}))
    if (role != "DOC" or runtime.visit_invoice_write_enabled(gateway, view.user) is not True
            or not gateway.csrf_token_valid(csrf_token)):
        gateway.deny()
    if (not isinstance(body, dict) or not {"appointment_id", "preview_token", "vat_mode"} <= set(body)
            or not set(body) <= {"appointment_id", "discount", "preview_token", "vat_mode"}):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    appointment, token = body["appointment_id"], body["preview_token"]
    vat_mode = _vat_mode(body)
    if (not is_valid_text(appointment, 140) or appointment != appointment.strip()
            or vat_mode is None
            or type(token) is not str or not token or len(token) > 100):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        discount = visit_invoice_contract.parse_discount(body)
    except ValueError:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        allowed = (gateway.visit_invoice_native_create_submit_allowed() is True
                   and (discount is None or gateway.visit_invoice_discount_allowed() is True))
    except Exception:
        allowed = False
    if not allowed:
        gateway.deny()
    lane_factory = getattr(gateway, "visit_invoice_serialized", None)
    if not callable(lane_factory):
        gateway.deny()
    lane = lane_factory(appointment)
    entered = lane.__enter__()
    dispatched = [False]
    try:
        outcome, result = (_submit_in_lane(gateway, appointment, discount, vat_mode, token,
                                           view.user, dispatched)
                           if entered is True else ("denied", None))
    except BaseException:
        lane.__exit__(*sys.exc_info())
        raise
    lane.__exit__(None, None, None)
    if outcome == "uncertain":
        return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})
    if outcome == "denied":
        gateway.deny()
    return result


def view(gateway, appointment_id=None):
    """Return only an authorized linked invoice reference, or unavailable."""
    _settings, _view, role = common.authorize(gateway, frozenset({"DOC"}))
    if role != "DOC":
        gateway.deny()
    if (not is_valid_text(appointment_id, 140)
            or appointment_id != appointment_id.strip()):
        gateway.deny()
    try:
        result = gateway.view_visit_invoice(appointment_id)
        if (type(result) is dict and set(result) == {"invoice_id"}
                and is_valid_text(result["invoice_id"], 140)
                and result["invoice_id"] == result["invoice_id"].strip()):
            return {"invoice_id": result["invoice_id"]}
    except Exception:
        pass
    return {"code": "UNAVAILABLE"}
