"""Authenticated visit-photo history and consent endpoints."""

import hashlib

from clinic_photos.api import photo_save
from clinic_photos.access.storage import is_canonical_visit_key
from clinic_photos.publication import validation
from clinic_photos.visit.runtime import enabled_for_site

SAFE_DENIAL = "Visit photo request unavailable"
SAFE_VALIDATION = "Invalid visit photo request"


def require_visit_request(allowed_codes=frozenset({"DOC"})):
    """Require the independent site gate and authenticated native POST session."""
    frappe = photo_save._frappe()
    photo_save.require_post()
    photo_save.require_native_session()
    photo_save.require_production_workspace(allowed_codes)
    user = getattr(getattr(frappe, "session", None), "user", None)
    conf = getattr(frappe, "conf", None)
    if (not isinstance(user, str) or not user or user == "Guest" or
        not enabled_for_site(getattr(frappe.local, "site", None), conf)):
        raise frappe.PermissionError(SAFE_DENIAL)


def _valid_dispatch_extra(extra, method):
    if not extra:
        return True
    expected = f"clinic_photos.api.{method}"
    form = getattr(photo_save._frappe().local, "form_dict", None)
    return (set(extra) == {"cmd"} and extra["cmd"] == expected and
            callable(getattr(form, "get", None)) and form.get("cmd") == expected)


def _payload(payload, keys, extra, method):
    frappe = photo_save._frappe()
    if type(payload) is not dict or set(payload) != keys or not _valid_dispatch_extra(extra, method):
        raise frappe.ValidationError(SAFE_VALIDATION)
    try:
        appointment = validation.require_reference(payload["appointment"])
    except Exception:
        raise frappe.ValidationError(SAFE_VALIDATION) from None
    if not all(char.isprintable() for char in appointment):
        raise frappe.ValidationError(SAFE_VALIDATION)
    return appointment


def _read_pair(appointment):
    from clinic_photos.visit.pair import read_pair

    return read_pair(appointment)


def _change_consent(*args, **kwargs):
    from clinic_photos.visit.state import change_consent

    return change_consent(*args, **kwargs)


def read_pair(payload=None, **extra):
    require_visit_request()
    appointment = _payload(payload, {"appointment"}, extra, "visit_photos.read_pair")
    try:
        result = _read_pair(appointment)
        response = {"appointment": appointment}
        for slot in ("before", "after"):
            item = result[slot]
            response[slot] = None if item is None else {
                field: item[field] for field in ("photo", "body_area", "viewpoint")
            }
        frappe = photo_save._frappe()
        if (enabled_for_site(getattr(frappe.local, "site", None), getattr(frappe, "conf", None)) and
                frappe.conf.get("clinic_visit_photo_annotations_enabled") is True):
            response["drawings_enabled"] = True
        return response
    except Exception:
        raise photo_save._frappe().PermissionError(SAFE_DENIAL) from None


def grant_consent(payload=None, **extra):
    require_visit_request(frozenset({"DOC", "REC", "ADM"}))
    appointment = _payload(payload, {"appointment", "attested", "evidence"}, extra, "visit_photos.grant_consent")
    evidence = payload["evidence"]
    if (payload["attested"] is not True or not isinstance(evidence, str) or
        not 3 <= len(evidence) <= 140 or evidence != evidence.strip() or
        not all(char.isprintable() for char in evidence)):
        raise photo_save._frappe().ValidationError(SAFE_VALIDATION)
    try:
        _change_consent(appointment, True, evidence, attested=True)
    except Exception:
        raise photo_save._frappe().PermissionError(SAFE_DENIAL) from None
    return {"ok": True}


def revoke_consent(payload=None, **extra):
    require_visit_request(frozenset({"DOC", "REC", "ADM"}))
    appointment = _payload(payload, {"appointment"}, extra, "visit_photos.revoke_consent")
    try:
        _change_consent(appointment, False)
    except Exception:
        raise photo_save._frappe().PermissionError(SAFE_DENIAL) from None
    return {"ok": True}


def read_image(payload=None, **extra):
    from clinic_photos.access.entrypoints import mark_protected_request

    mark_protected_request()
    require_visit_request()
    appointment = _payload(payload, {"appointment", "slot"}, extra, "visit_photos.read_image")
    if payload["slot"] not in ("Before", "After"):
        raise photo_save._frappe().ValidationError(SAFE_VALIDATION)
    frappe = photo_save._frappe()
    try:
        from werkzeug.wrappers import Response

        result = _read_pair(appointment)
        item = result[payload["slot"].lower()]
        if result["appointment"] != appointment or not item:
            raise ValueError
        photo = frappe.get_doc("Clinic Photo", item["photo"])
        file = frappe.get_doc("File", item["file"])
        key = photo.storage_key
        if (photo.name != item["photo"] or photo.original_file != item["file"] or
            file.name != item["file"] or file.is_private != 1 or
            file.attached_to_doctype != "Clinic Photo" or
            file.attached_to_name != photo.name or file.file_url != key or
            not is_canonical_visit_key(key) or type(file.file_size) is not int):
            raise ValueError
        validation.check_decoded_image_size(file.file_size)
        data = validation.validate_image_bytes(file.get_content())
        if len(data) != file.file_size or hashlib.sha256(data).hexdigest() != photo.original_sha256:
            raise ValueError
        if data.startswith(b"\x89PNG\r\n\x1a\n") and key.endswith(".png"):
            mime = "image/png"
        elif data.startswith(b"\xff\xd8\xff") and key.endswith(".jpg"):
            mime = "image/jpeg"
        else:
            raise ValueError
        response = Response(data, content_type=mime)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception:
        raise frappe.PermissionError(SAFE_DENIAL) from None


def read_own_before(payload=None, **extra):
    from clinic_photos.access.entrypoints import mark_protected_request

    mark_protected_request()
    require_visit_request(frozenset({"REC"}))
    appointment = _payload(payload, {"appointment"}, extra, "visit_photos.read_own_before")
    frappe = photo_save._frappe()
    try:
        from werkzeug.wrappers import Response
        from clinic_photos.visit import reception_read

        data, mime = reception_read.read_own_before(appointment)
        response = Response(data, content_type=mime)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response
    except Exception:
        raise frappe.PermissionError(SAFE_DENIAL) from None


def _register_site_contract():
    try:
        import frappe
    except ModuleNotFoundError:
        return
    global read_pair, grant_consent, revoke_consent, read_image, read_own_before
    read_pair = frappe.whitelist(methods=["POST"])(read_pair)
    grant_consent = frappe.whitelist(methods=["POST"])(grant_consent)
    revoke_consent = frappe.whitelist(methods=["POST"])(revoke_consent)
    read_image = frappe.whitelist(methods=["POST"])(read_image)
    read_own_before = frappe.whitelist(methods=["POST"])(read_own_before)


_register_site_contract()
