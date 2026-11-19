"""Strict request policy for the disposable visit-invoice ingress."""
import json
import re
from urllib.parse import parse_qsl

PREFIX = "/api/method/"
READS = {
    "clinic_ui.api.session.bootstrap": None,
    "clinic_ui.api.directory.synthetic_patients": None,
    "clinic_ui.api.appointments.synthetic_appointments": None,
    "clinic_ui.api.patients.view": "workspace_patient_id",
    "clinic_ui.api.appointments.view": "workspace_appointment_id",
    "clinic_ui.api.visit_invoice.view": "appointment_id",
}


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 1024


def allows(method, target, body=b"", content_type=""):
    path, sep, query = target.partition("?")
    if not path.startswith("/") or any(c in path for c in "%#\\") or ".." in path:
        return False
    if method == "GET" and not body:
        if target == "/clinic/login?reason=expired":
            return True
        if not sep and (re.fullmatch(r"/clinic(?:/[A-Za-z0-9_/-]*)?", path) or
                        re.fullmatch(r"/assets/clinic_ui/ui/assets/[A-Za-z0-9_.-]+", path)):
            return True
        name = path.removeprefix(PREFIX) if path.startswith(PREFIX) else ""
        if name not in READS:
            return False
        key = READS[name]
        if key is None:
            return not sep
        # Keys must be literal; only values may use URLSearchParams encoding.
        if not sep or not query.startswith(key + "=") or "&" in query or "#" in query:
            return False
        try:
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, encoding="utf-8", errors="strict")
            return len(pairs) == 1 and pairs[0][0] == key and text(pairs[0][1])
        except ValueError:
            return False
    if method != "POST" or sep or content_type.lower() != "application/json":
        return False
    name = path.removeprefix(PREFIX) if path.startswith(PREFIX) else ""
    if name == "logout":
        return body == b""
    if name not in {"login", "clinic_ui.api.visit_invoice.preview", "clinic_ui.api.visit_invoice.submit"}:
        return False
    try:
        data = json.loads(body, object_pairs_hook=unique_pairs)
    except (ValueError, UnicodeDecodeError, RecursionError):
        return False
    if not isinstance(data, dict):
        return False
    if name == "login":
        return set(data) == {"usr", "pwd"} and all(text(v) for v in data.values())
    required = {"appointment_id"} | ({"preview_token"} if name.endswith(".submit") else set())
    if not required <= set(data) or set(data) - required - {"discount"}:
        return False
    if not all(text(data[k]) for k in required):
        return False
    if "discount" not in data:
        return True
    discount = data["discount"]
    return (isinstance(discount, dict) and set(discount) == {"mode", "value"} and
            discount["mode"] in ("amount", "percent") and text(discount["value"]))
