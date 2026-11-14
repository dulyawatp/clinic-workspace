app_name = "clinic_ui"
app_title = "Clinic UI"
app_publisher = "Clinic OpenDesign"
app_description = "Read-only native session and synthetic directory contracts"
app_email = ""
app_license = "MIT"
app_version = "0.0.1"

# Intentionally minimal. This app declares no doctype, no doc_event, no
# permission_query_conditions, no override_whitelisted_methods and no request
# hook. It adds two GET-only whitelisted methods under `clinic_ui.api` and one
# website route family, so `/api`, `/app` and `/desk` keep their native
# behaviour. Adding any other hook here is a review-visible change.
#
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
