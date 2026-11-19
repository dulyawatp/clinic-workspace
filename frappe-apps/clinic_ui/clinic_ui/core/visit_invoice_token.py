"""Internal preview binding; callers must independently re-resolve every claim."""

import datetime
import hashlib
import hmac
import json
import re


_PURPOSE = b"clinic-ui:doctor-visit-invoice-preview:v1\0"
_WIRE = re.compile(r"v1\.([1-9][0-9]{0,15})\.([0-9a-f]{64})", re.ASCII)
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,6})?", re.ASCII)
_MONEY = re.compile(r"(?:0|[1-9][0-9]{0,15})\.[0-9]{2}", re.ASCII)


def _text(value):
    if type(value) is not str or not value or len(value) > 512:
        raise ValueError("invalid string")
    value.encode("utf-8")


def _canonical(value, depth=0):
    if depth > 8:
        raise ValueError("claims too deep")
    if value is None or type(value) is bool:
        return
    if type(value) is str:
        _text(value)
    elif type(value) is int:
        if abs(value) > 10**16:
            raise ValueError("integer out of range")
    elif type(value) is list:
        if len(value) > 128:
            raise ValueError("list too long")
        for item in value:
            _canonical(item, depth + 1)
    elif type(value) is dict:
        if len(value) > 128:
            raise ValueError("mapping too large")
        for key, item in value.items():
            _text(key)
            _canonical(item, depth + 1)
    else:
        raise ValueError("invalid claim type")


def _payload(key, claims, now):
    if type(key) is not bytes or len(key) < 32:
        raise ValueError("key must be at least 32 bytes")
    if type(now) is not int or not 0 <= now <= 10**15:
        raise ValueError("invalid clock")
    if type(claims) is not dict or set(claims) != {
        "actor", "session", "date", "appointment", "encounter", "discount", "terms", "totals"
    }:
        raise ValueError("invalid claims")
    _canonical(claims)
    for field in ("actor", "session", "date", "appointment"):
        _text(claims[field])
    if claims["encounter"] is not None:
        _text(claims["encounter"])
    try:
        if datetime.date.fromisoformat(claims["date"]).isoformat() != claims["date"]:
            raise ValueError("noncanonical date")
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid date") from exc
    discount = claims["discount"]
    if type(discount) is not dict or set(discount) != {"mode", "value"}:
        raise ValueError("invalid discount")
    if discount["mode"] not in ("none", "amount", "percent"):
        raise ValueError("invalid discount mode")
    if type(discount["value"]) is not str or not _DECIMAL.fullmatch(discount["value"]):
        raise ValueError("invalid discount value")
    if discount["mode"] == "none" and discount["value"] != "0":
        raise ValueError("invalid no-discount value")
    if type(claims["terms"]) is not dict or not claims["terms"]:
        raise ValueError("missing accounting terms")
    for field in ("patient", "customer", "practitioner", "company", "service"):
        _text(claims["terms"].get(field))
    rate = claims["terms"].get("rate")
    if type(rate) is not str or not _MONEY.fullmatch(rate):
        raise ValueError("invalid rate")
    totals = claims["totals"]
    if type(totals) is not dict or set(totals) != {"currency", "gross", "discount", "total"}:
        raise ValueError("invalid totals")
    if type(totals["currency"]) is not str or not re.fullmatch(r"[A-Z]{3}", totals["currency"], re.ASCII):
        raise ValueError("invalid currency")
    for field in ("gross", "discount", "total"):
        if type(totals[field]) is not str or not _MONEY.fullmatch(totals[field]):
            raise ValueError("invalid money")
    encoded = json.dumps(claims, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    if len(encoded) > 32768:
        raise ValueError("claims too large")
    return encoded


def _mac(key, expiry, payload):
    return hmac.new(key, _PURPOSE + str(expiry).encode("ascii") + b"\0" + payload, hashlib.sha256).hexdigest()


def issue(key, claims, *, now):
    """Issue token using server key, trusted claims and integer Unix time."""
    payload = _payload(key, claims, now)
    expiry = now + 300
    return f"v1.{expiry}.{_mac(key, expiry, payload)}"


def verify(key, claims, token, *, now):
    """Verify against freshly re-resolved claims; malformed inputs fail closed."""
    try:
        payload = _payload(key, claims, now)
        if type(token) is not str or not (match := _WIRE.fullmatch(token)):
            return False
        expiry = int(match.group(1))
        if not 0 < expiry - now <= 300:
            return False
        return hmac.compare_digest(match.group(2), _mac(key, expiry, payload))
    except (ValueError, TypeError, UnicodeError):
        return False
