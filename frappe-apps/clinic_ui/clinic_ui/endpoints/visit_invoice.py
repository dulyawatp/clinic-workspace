"""Offline-only visit invoice preview orchestration; no native adapter is wired."""

from decimal import Decimal
import sys

from clinic_ui.core import visit_invoice_contract, visit_invoice_token
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import common
from clinic_ui.runtime import visit_invoice as runtime


def preview(gateway, body, csrf_token=None):
    common.begin(gateway)
    _settings, view, role = common.authorize(gateway, frozenset({"DOC"}))
    if (role != "DOC" or runtime.visit_invoice_write_enabled(gateway, view.user) is not True
            or not gateway.csrf_token_valid(csrf_token)):
        gateway.deny()
    if (not isinstance(body, dict) or not {"appointment_id"} <= set(body)
            or not set(body) <= {"appointment_id", "discount"}):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    appointment = body["appointment_id"]
    if not is_valid_text(appointment, 140) or appointment != appointment.strip():
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
    try:
        result = gateway.build_visit_invoice_preview(appointment, discount)
        if type(result) is not dict or set(result) != {"totals", "claims"}:
            raise ValueError("invalid preview")
        totals, claims = result["totals"], result["claims"]
        expected_discount = ({"mode": "none", "value": "0"} if discount is None
                             else {"mode": discount["mode"], "value": str(discount["value"])})
        if (type(claims) is not dict or claims.get("appointment") != appointment
                or claims.get("actor") != view.user or claims.get("discount") != expected_discount
                or claims.get("totals") != totals):
            raise ValueError("preview binding mismatch")
        token = visit_invoice_token.issue(
            gateway.visit_invoice_token_key(), claims, now=gateway.visit_invoice_clock()
        )
        gross, cut, total = (Decimal(totals[field]) for field in ("gross", "discount", "total"))
        if (gross <= 0 or cut < 0 or total <= 0 or gross - cut != total
                or (discount is None and cut != 0) or (discount is not None and cut <= 0)):
            raise ValueError("invalid preview totals")
        return {**totals, "preview_token": token}
    except Exception:
        gateway.deny()


class KnownMutationFailure(Exception):
    """Native worker confirms failure before commit; transaction needs rollback."""


def _submit_in_lane(gateway, appointment, discount, token, actor, dispatched):
    try:
        prepared = gateway.prepare_visit_invoice_submission(appointment, discount)
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
        gross, cut, total = (Decimal(totals[field]) for field in ("gross", "discount", "total"))
        if (gross <= 0 or cut < 0 or total <= 0 or gross - cut != total
                or (discount is None and cut != 0) or (discount is not None and cut <= 0)):
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
    if (not isinstance(body, dict) or not {"appointment_id", "preview_token"} <= set(body)
            or not set(body) <= {"appointment_id", "discount", "preview_token"}):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    appointment, token = body["appointment_id"], body["preview_token"]
    if (not is_valid_text(appointment, 140) or appointment != appointment.strip()
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
        outcome, result = (_submit_in_lane(gateway, appointment, discount, token, view.user, dispatched)
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
