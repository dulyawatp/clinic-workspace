"""Controller for the native SPA shell served at ``/clinic`` and ``/clinic/*``.

The shell is Frappe's server-rendered entry document for the React app. It owns
no data: it names the content-hashed build files (resolved from the Vite
manifest) and provides the ``#root`` mount point. Everything else — session,
routing, data — is fetched by the client from ``/api``.

Caching contract:

* the module disables Frappe's website page cache, so a new build is picked up
  on the next request instead of after ``cache_html``'s window;
* ``get_context`` additionally pins ``Cache-Control: no-store`` on every
  outcome, which is what the browser and any intermediary see;
* the nested route is also marked uncacheable by Frappe itself, because
  ``evaluate_dynamic_routes`` sets ``frappe.local.no_cache`` for any matched
  rule that captures arguments.

A missing or malformed manifest returns ``503`` with no asset reference at all:
a shell that points at files which do not exist is worse than a visible outage.
"""

import frappe

from clinic_ui.core.assets import (
    SHELL_STATIC_SEGMENTS,
    AssetManifestError,
    shell_assets,
)

# Read by Frappe's template renderer: never serve this page from the website
# page cache, and never list it in the sitemap.
no_cache = True
sitemap = 0

#: Same value Frappe's request pipeline uses for uncacheable responses.
NO_STORE = "no-store,no-cache,must-revalidate,max-age=0"


def get_context(context):
    """Populate the shell context from the current native build manifest."""
    # Item assignment keeps this controller compatible with the plain mapping
    # used by the offline tests and with Frappe's attribute-aware `_dict`.
    frappe.local.response_headers["Cache-Control"] = NO_STORE
    context["no_cache"] = True
    context["sitemap"] = 0
    context["title"] = "Clinic"
    context["ui_available"] = False
    context["ui_scripts"] = []
    context["ui_styles"] = []

    build_root = frappe.get_app_path("clinic_ui", *SHELL_STATIC_SEGMENTS)
    try:
        assets = shell_assets(build_root)
    except AssetManifestError:
        context["http_status_code"] = 503
        return

    context["ui_available"] = True
    context["ui_scripts"] = list(assets.scripts)
    context["ui_styles"] = list(assets.styles)
