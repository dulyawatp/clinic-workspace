from decimal import Decimal, InvalidOperation
import re


def parse_discount(body):
    if not isinstance(body, dict):
        raise ValueError("Invalid request")
    if "discount" not in body or body["discount"] is None:
        return None
    discount = body["discount"]
    if not isinstance(discount, dict) or set(discount) != {"mode", "value"}:
        raise ValueError("Invalid discount")
    mode, value = discount["mode"], discount["value"]
    if (mode not in ("amount", "percent") or not isinstance(value, str)
            or re.fullmatch(r"[0-9]{1,7}(?:\.[0-9]{1,2})?", value) is None):
        raise ValueError("Invalid discount")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("Invalid discount") from exc
    if (not number.is_finite() or number <= 0 or number > 1_000_000
            or (mode == "percent" and number > 100)):
        raise ValueError("Invalid discount")
    return {"mode": mode, "value": number}
