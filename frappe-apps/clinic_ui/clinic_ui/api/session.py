"""``GET /api/method/clinic_ui.api.session.bootstrap``.

Guest dispatch is intentional and narrow: without ``allow_guest`` Frappe raises
its own 403 for a missing or expired session, but the client needs an explicit
401 to reach sign-in. The endpoint therefore admits the dispatch and returns a
fixed ``{"code": "AUTH_EXPIRED"}`` body with HTTP 401 for anonymous/expired
callers. Authenticated authorization denials remain framework 403s.

Frappe wraps the returned mapping as ``{"message": ...}``. The optional
``workspace`` query selects an assigned clinic role; every response is no-store.
"""

import frappe

from clinic_ui.endpoints import session as session_endpoint
from clinic_ui.runtime.frappe_gateway import Gateway


@frappe.whitelist(allow_guest=True, methods=["GET"])
def bootstrap(workspace=None):
    return session_endpoint.bootstrap(Gateway(), workspace=workspace)
