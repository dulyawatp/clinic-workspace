"""Visit feature activation predicate."""

import os


def enabled_for_site(site, conf) -> bool:
    """Require an explicit boolean gate and exact server-configured site."""
    if not isinstance(site, str) or not site or site.strip() != site:
        return False
    if not callable(getattr(conf, "get", None)):
        return False
    configured = conf.get("clinic_visit_photos_site")
    if not (
        conf.get("clinic_visit_photos_enabled") is True
        and isinstance(configured, str)
        and configured == site
    ):
        return False
    enabled = conf.get("clinic_ui_production_read_enabled")
    if (enabled is None or enabled is False) and "CLINIC_UI_PRODUCTION_MARKER_PATH" not in os.environ:
        return True
    if enabled is not True:
        return False
    try:
        import frappe
        from clinic_ui.runtime.production_site import enrolled

        return enrolled(frappe, site, conf) is True
    except (ImportError, AttributeError, OSError, TypeError, ValueError):
        return False
