"""Framework adapter for host-provisioned production enrollment."""

import os

from clinic_ui.core.production_enrollment import verify_production_enrollment


def enrolled(frappe, site, conf):
    """Verify exact native site and nominal private path without actor authority."""
    try:
        return (
            frappe.local.site == site
            and verify_production_enrollment(
                site,
                os.path.abspath(frappe.get_site_path("private")),
                os.environ.get("CLINIC_UI_PRODUCTION_MARKER_PATH"),
                conf.get("clinic_ui_production_site"),
                conf.get("clinic_ui_production_marker_sha256"),
            )
        )
    except (AttributeError, OSError, TypeError, ValueError):
        return False
