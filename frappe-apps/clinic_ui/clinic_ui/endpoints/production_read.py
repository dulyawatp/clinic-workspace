"""Production-only enrollment gate for explicitly selected read endpoints."""

import base64
import binascii
import hmac
import os

from clinic_ui.core import config, policy, production_enrollment
from clinic_ui.core.epoch import session_epoch
from clinic_ui.endpoints import common

ENABLED = "clinic_ui_production_read_enabled"


def require_browser_session(gateway, view):
    """Bind explicit workspace context to the actual native session cookie."""
    try:
        request = gateway.frappe.local.request
        authorization = request.headers.get("Authorization")
        cookie = request.cookies.get("sid")
    except AttributeError:
        gateway.deny()
    if (
        authorization not in (None, "")
        or not isinstance(cookie, str)
        or not cookie
        or cookie == "Guest"
        or cookie != view.sid
    ):
        gateway.deny()


def patient_ref_key(gateway):
    raw = gateway.setting("clinic_ui_production_patient_ref_key")
    try:
        if not isinstance(raw, str) or len(raw) != 86:
            return None
        key = base64.b64decode(raw + "==", altchars=b"-_", validate=True)
        if len(key) != 64 or base64.urlsafe_b64encode(key).rstrip(b"=").decode("ascii") != raw:
            return None
        return key
    except (ValueError, binascii.Error):
        return None


def authorize_if_selected(gateway, allowed_codes, *, begun=False, bootstrap=False, workspace=None):
    """Return production settings and role, or None for the legacy lane."""
    enabled = gateway.setting(ENABLED)
    if (enabled is None or enabled is False) and "CLINIC_UI_PRODUCTION_MARKER_PATH" not in os.environ:
        return None
    if not begun:
        common.begin(gateway)
    if enabled is not True:
        gateway.deny()
    view = gateway.current_session
    common.require_authorized_session(gateway, view)
    try:
        frappe = gateway.frappe
        site = frappe.local.site
        private_path = os.path.abspath(frappe.get_site_path("private"))
        valid = site == gateway.site and production_enrollment.verify_production_enrollment(
            site,
            private_path,
            os.environ.get("CLINIC_UI_PRODUCTION_MARKER_PATH"),
            gateway.setting("clinic_ui_production_site"),
            gateway.setting("clinic_ui_production_marker_sha256"),
        )
        roles = config._role_map(gateway.setting("clinic_ui_production_role_map"))
        secret = gateway.setting("clinic_ui_production_epoch_secret")
        automatic_roles = {"all", "guest", "desk user", "administrator"}
        if (
            not valid
            or roles is None
            or any(name.casefold() in automatic_roles for _, names in roles for name in names)
            or patient_ref_key(gateway) is None
            or not isinstance(secret, str)
            or len(secret) < config.MIN_EPOCH_SECRET_LENGTH
        ):
            gateway.deny()
        settings = config.Settings(role_map=roles, allowlist=(), epoch_secret=secret.encode("utf-8"))
    except (AttributeError, OSError, TypeError, ValueError):
        gateway.deny()
    try:
        fresh_roles = gateway.fresh_user_roles(view.user)
    except Exception:
        fresh_roles = None
    if not isinstance(fresh_roles, tuple):
        gateway.deny()
    eligible = policy._matching_codes(set(fresh_roles), settings.as_role_map())
    if not eligible:
        gateway.deny()
    if bootstrap:
        request = getattr(gateway.frappe.local, "request", None)
        headers = getattr(request, "headers", {})
        selected = headers.get("X-Clinic-Workspace")
        supplied_epoch = headers.get("X-Clinic-Session-Epoch")
        if workspace is not None or selected is not None or supplied_epoch is not None:
            require_browser_session(gateway, view)
        if selected is not None or supplied_epoch is not None:
            if (
                selected not in eligible
                or not isinstance(supplied_epoch, str)
                or not hmac.compare_digest(
                    supplied_epoch.encode("utf-8"),
                    session_epoch(settings.epoch_secret, gateway.site, view.sid).encode("ascii"),
                )
            ):
                gateway.deny()
        if workspace is None:
            role = eligible[0] if len(eligible) == 1 else None
        elif isinstance(workspace, str) and workspace in eligible:
            role = workspace
        else:
            gateway.deny()
        return settings, view, role, eligible

    try:
        headers = gateway.frappe.local.request.headers
        selected = headers.get("X-Clinic-Workspace")
        supplied_epoch = headers.get("X-Clinic-Session-Epoch")
    except AttributeError:
        gateway.deny()
    if selected is None and supplied_epoch is None and len(eligible) == 1:
        role = eligible[0]
    elif (
        isinstance(selected, str)
        and selected in eligible
        and isinstance(supplied_epoch, str)
        and hmac.compare_digest(
            supplied_epoch.encode("utf-8"),
            session_epoch(settings.epoch_secret, gateway.site, view.sid).encode("ascii"),
        )
    ):
        require_browser_session(gateway, view)
        role = selected
    else:
        gateway.deny()
    if role not in allowed_codes:
        gateway.deny()
    return settings, view, role
