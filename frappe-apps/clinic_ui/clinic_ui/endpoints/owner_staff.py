"""Production-only, permission-filtered Owner staff list."""

from clinic_ui.core import owner_staff_contract as contract
from clinic_ui.endpoints import production_read


def _role_gate(gateway, csrf=None, write=False, create=False):
    production = production_read.authorize_if_selected(gateway, contract.OWNER_ROLE_CODES)
    if production is None:
        gateway.deny()
    settings, view, role = production
    headers = gateway.frappe.local.request.headers
    if headers.get("X-Clinic-Workspace") != "MGR" or not headers.get("X-Clinic-Session-Epoch"):
        gateway.deny()
    fresh = gateway.fresh_user_roles(view.user)
    if role != "MGR" or not isinstance(fresh, tuple) or "System Manager" not in fresh:
        gateway.deny()
    if create and (view.user in ("Administrator", "Guest") or not gateway.owner_staff_create_authorized(view.user)):
        gateway.deny()
    if write and not gateway.csrf_token_valid(csrf):
        gateway.deny()
    choices = gateway.owner_role_choices(settings.as_role_map(), create=create)
    if choices is None:
        gateway.deny()
    return settings, view, choices
def create_options(gateway):
    _, _, choices = _role_gate(gateway, create=True)
    return {"choices": [{"role": name, "label": name} for name in choices]}

def create_staff(gateway, body, csrf):
    _, view, choices = _role_gate(gateway, csrf, write=True, create=True)
    if (
        not isinstance(body, dict)
        or set(body) != {"email", "firstName", "lastName", "roles"}
        or any(not isinstance(body[key], str) or not body[key].strip()
               or len(body[key]) > 140 for key in ("email", "firstName", "lastName"))
        or not isinstance(body["roles"], list) or not body["roles"]
        or len(body["roles"]) > len(choices)
        or any(not isinstance(item, str) or item not in choices for item in body["roles"])
        or len(set(body["roles"])) != len(body["roles"])
    ):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    return gateway.owner_staff_create(body, view.user)


def role_options(gateway, user_id):
    settings, view, choices = _role_gate(gateway)
    if not isinstance(user_id, str) or not user_id or len(user_id) > 140:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        doc, roles = gateway.owner_role_target(user_id, view.user, settings.as_role_map())
        return {
            "userId": doc.name,
            "version": str(doc.modified),
            "roles": sorted(set(roles).intersection(choices)),
            "choices": [{"role": name, "label": name} for name in choices],
        }
    except Exception:
        gateway.deny()


def update_roles(gateway, body, csrf):
    settings, view, choices = _role_gate(gateway, csrf, write=True)
    if (
        not isinstance(body, dict)
        or set(body) != {"userId", "version", "roles"}
        or not isinstance(body["userId"], str)
        or not body["userId"] or len(body["userId"]) > 140
        or not isinstance(body["version"], str)
        or not body["version"] or len(body["version"]) > 64
        or not isinstance(body["roles"], list)
        or len(body["roles"]) > len(choices)
        or any(not isinstance(item, str) or item not in choices for item in body["roles"])
        or len(set(body["roles"])) != len(body["roles"])
    ):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    try:
        doc, before = gateway.owner_role_target(
            body["userId"], view.user, settings.as_role_map(), lock=True
        )
    except Exception:
        gateway.deny()
    if str(doc.modified) != body["version"]:
        return gateway.respond_write(409, {"code": "STALE_VERSION"})
    requested = set(body["roles"])
    before_set = set(before)
    expected = (before_set - set(choices)) | requested
    if not gateway.owner_roles_keep_desk(expected):
        return gateway.respond_write(422, {"code": "VALIDATION"})
    before_type = doc.user_type
    try:
        gateway.owner_role_update(doc, choices, requested, before_set)
        gateway.commit()
        after_doc, after = gateway.owner_role_readback(body["userId"])
        after_set = set(after)
        result = {
            "userId": after_doc.name,
            "version": str(after_doc.modified),
            "beforeRoles": sorted(before_set),
            "afterRoles": sorted(after_set),
            "automaticAdded": sorted(after_set - expected),
            "automaticRemoved": sorted(expected - after_set),
            "beforeUserType": before_type,
            "afterUserType": after_doc.user_type,
        }
        gateway.strip_message_channels()
        return result
    except Exception:
        # A native hook or commit may already have committed. Rollback prevents
        # request-end commit of pending work, but does not establish outcome.
        gateway.rollback()
        return gateway.respond_write(409, {"code": "WRITE_UNCERTAIN"})


def _number(value, maximum):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        number = value
    elif isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 6:
        number = int(value)
    else:
        return None
    return number if 0 <= number <= maximum else None


def list_staff(gateway, start=0, page_size=25):
    production = production_read.authorize_if_selected(gateway, contract.OWNER_ROLE_CODES)
    if production is None:
        gateway.deny()
    settings, view, role = production
    # Require explicit workspace context even when MGR is the sole eligible role.
    try:
        headers = gateway.frappe.local.request.headers
        if headers.get("X-Clinic-Workspace") != "MGR" or not headers.get("X-Clinic-Session-Epoch"):
            gateway.deny()
        fresh = gateway.fresh_user_roles(view.user)
        if role != "MGR" or not isinstance(fresh, tuple) or "System Manager" not in fresh:
            gateway.deny()
    except Exception:
        gateway.deny()
    start = _number(start, contract.MAX_START)
    page_size = _number(page_size, contract.MAX_PAGE_SIZE)
    if start is None or page_size is None or page_size == 0:
        return gateway.respond_write(422, {"code": "VALIDATION"})
    if gateway.native_doctype_can_read("User") is not True:
        gateway.deny()
    result = gateway.owner_staff_list(start, page_size, settings.as_role_map(), view.user)
    if result is None:
        gateway.deny()
    return result
