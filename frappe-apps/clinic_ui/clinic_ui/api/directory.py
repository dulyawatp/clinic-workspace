"""``GET /api/method/clinic_ui.api.directory.synthetic_patients``.

Frappe wraps the returned mapping as ``{"message": {"items": [...]}}``. The
method accepts no client input: the allowlist is server-owned site
configuration and each entry is checked against the current user's native read
permission before its display name is read.
"""

import frappe

from clinic_ui.endpoints import directory as directory_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(methods=["GET"])
def synthetic_patients():
    return directory_endpoint.synthetic_patients(Gateway())
