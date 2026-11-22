"""Production-only enrollment gate for explicitly selected read endpoints."""

import base64
import binascii
import os

from clinic_ui.core import config, production_enrollment
from clinic_ui.endpoints import common

ENABLED = "clinic_ui_production_read_enabled"


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


def authorize_if_selected(gateway, allowed_codes, *, begun=False):
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
    role = common.role_or_deny(gateway, view, settings, allowed_codes)
    return settings, view, role
