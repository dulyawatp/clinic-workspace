app_name = "clinic_ui"
app_title = "Clinic UI"
app_publisher = "Clinic OpenDesign"
app_description = "Native session, synthetic directory, and default-off local patient writes"
app_email = ""
app_license = "MIT"
app_version = "0.0.1"

# Intentionally minimal. This app declares no doctype, no doc_event, no
# permission_query_conditions, no override_whitelisted_methods. It adds read
# methods, including the Owner staff list, and default-off POST writes under
# `clinic_ui.api`, one website route family and one narrow `before_request`
# hook, so `/api`, `/app` and `/desk` keep their native behaviour. Adding any
# other hook here is a review-visible change.
#
# `before_request` only sets Frappe's documented `disable_traceback` flag for
# clinic API paths. Frappe otherwise includes the full formatted traceback in
# JSON error bodies when `allow_error_traceback` is enabled for a System User
# (`frappe/utils/response.py:68-79`, `_make_logs_v1`, `report_error`). The
# `after_request` sanitiser then replaces a clinic API JSON error body with a
# fixed status-derived code; it strips `exc`, `_exc_source`, `_debug_messages`,
# `_server_messages`, `_error_message`, `exception` and v2 `messages`. A
# request-local, server-set 409 write code (`WRITE_LOCKED`,
# `OPERATION_CONFLICT`, `STALE_VERSION`) is preserved from an allowlist. This
# covers Frappe's dev-server branch, where the flag is ignored, and message
# channels that are serialized even when tracebacks are disabled. Both hooks cover `/api/method/`, `/api/v1/method/`,
# `/api/v2/method/` and legacy `cmd=clinic_ui.` dispatch. Failures before the
# request is initialised (site resolution, database connection) remain
# framework-owned and are not claimed.
before_request = ["clinic_ui.runtime.traceback.suppress_api_tracebacks"]
after_request = ["clinic_ui.runtime.traceback.sanitize_api_error_response"]

# `website_route_rules` is the documented Frappe v16 website hook: the rules are
# compiled by `frappe.website.path_resolver.resolve_from_map` through Werkzeug
# and only run after `/api`, `/app`, `/desk`, `/assets` and static files have
# been routed. No global `website_path_resolver` is registered: that hook would
# replace the resolver for the whole site instead of adding two routes.
#
# Both rules render one template, `clinic_ui/www/clinic.html` with its
# `clinic_ui/www/clinic.py` controller. The nested rule captures the rest of the
# path so a hard reload of a client route (`/clinic/patients/PT-1/photos`)
# reaches the same no-cache shell; the SPA router reads `window.location`.
website_route_rules = [
    {"from_route": "/clinic", "to_route": "clinic"},
    {"from_route": "/clinic/<path:app_path>", "to_route": "clinic"},
]

# Framework adapters are imported only by the whitelisted entry modules and the
# website controller; the policy, DTO, asset and configuration layers import no
# Frappe code and are tested without an installed site.
