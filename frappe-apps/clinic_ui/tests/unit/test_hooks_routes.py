"""Route-surface tests for the ``clinic_ui`` hooks module.

``hooks.py`` is data only, so it is loaded from its file path without Frappe.
These tests pin the *shape* of the website route rules that host the SPA shell:

* exactly two rules, both from ``/clinic`` into the one ``clinic`` template;
* the nested rule captures the remaining path so client-side routes survive a
  hard reload;
* no global ``website_path_resolver`` hook exists (that would replace Frappe's
  documented resolver instead of extending it);
* no rule can capture ``/api``, ``/desk``, ``/app``, ``/assets``, ``/login`` or
  any other existing site route.

The matcher below mirrors the Werkzeug rule semantics Frappe relies on in
``frappe.website.router.evaluate_dynamic_routes`` for the two rule shapes used
here (literal segment and ``<path:name>``). It refuses any other converter, so a
rule change cannot silently pass.
"""

import importlib.util
import re
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
HOOKS_PATH = APP_ROOT / "clinic_ui" / "hooks.py"

EXPECTED_RULES = [
    {"from_route": "/clinic", "to_route": "clinic"},
    {"from_route": "/clinic/<path:app_path>", "to_route": "clinic"},
]

# Routes that must keep their native Frappe/ERPNext behaviour.
UNTOUCHED_PATHS = (
    "/",
    "/api",
    "/api/method/clinic_ui.api.session.bootstrap",
    "/api/resource/Patient",
    "/app",
    "/app/patient",
    "/desk",
    "/desk/patient",
    "/assets/clinic_ui/ui/assets/index-abc123.js",
    "/login",
    "/clinicphotos",
    "/clinic_photos",
    "/about",
)


def load_hooks():
    spec = importlib.util.spec_from_file_location("clinic_ui_hooks_under_test", HOOKS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _compile(from_route):
    """Return a regex for a Werkzeug rule using only the supported shapes."""
    parts = []
    for segment in from_route.strip("/").split("/"):
        if segment.startswith("<") and segment.endswith(">"):
            converter, _, _name = segment[1:-1].partition(":")
            if converter == "path":
                parts.append(r"(?P<path>.+)")
            elif converter == "":
                parts.append(r"(?P<segment>[^/]+)")
            else:
                raise AssertionError(f"unsupported rule converter: {segment}")
        else:
            parts.append(re.escape(segment))
    return re.compile("^/" + "/".join(parts) + "$")


def matches(from_route, path):
    return _compile(from_route).match(path) is not None


class WebsiteRouteRuleShapeTests(unittest.TestCase):
    def setUp(self):
        self.hooks = load_hooks()

    def test_rules_are_exactly_the_two_shell_rules(self):
        self.assertEqual(self.hooks.website_route_rules, EXPECTED_RULES)

    def test_every_rule_has_only_from_and_to_route(self):
        for rule in self.hooks.website_route_rules:
            with self.subTest(rule=rule):
                self.assertEqual(set(rule), {"from_route", "to_route"})

    def test_each_rule_targets_the_single_shell_template(self):
        targets = {rule["to_route"] for rule in self.hooks.website_route_rules}
        self.assertEqual(targets, {"clinic"})

    def test_exact_rule_precedes_the_nested_catch_all(self):
        routes = [rule["from_route"] for rule in self.hooks.website_route_rules]
        self.assertLess(routes.index("/clinic"), routes.index("/clinic/<path:app_path>"))

    def test_root_and_nested_routes_reach_the_shell(self):
        cases = {
            "/clinic": "/clinic",
            "/clinic/login": "/clinic/<path:app_path>",
            "/clinic/lab/photos/abc": "/clinic/<path:app_path>",
            "/clinic/patients/PT-1/photos/compare": "/clinic/<path:app_path>",
        }
        for path, rule in cases.items():
            with self.subTest(path=path):
                self.assertTrue(matches(rule, path))

    def test_existing_paths_are_not_captured_by_any_rule(self):
        for path in UNTOUCHED_PATHS:
            with self.subTest(path=path):
                captured = [rule for rule in EXPECTED_RULES if matches(rule["from_route"], path)]
                self.assertEqual(captured, [])

    def test_no_global_path_resolver_or_redirect_is_declared(self):
        self.assertFalse(hasattr(self.hooks, "website_path_resolver"))
        self.assertFalse(hasattr(self.hooks, "website_redirects"))

    def test_no_request_or_permission_hook_is_added(self):
        # The read-only slice stays read-only: the SPA shell must not need a
        # request hook, an auth hook, a permission override or a doc event.
        for hook in (
            "before_request",
            "after_request",
            "auth_hooks",
            "override_whitelisted_methods",
            "permission_query_conditions",
            "has_permission",
            "doc_events",
            "page_renderer",
            "website_path_resolver",
        ):
            with self.subTest(hook=hook):
                self.assertFalse(hasattr(self.hooks, hook))

    def test_app_identity_is_unchanged(self):
        self.assertEqual(self.hooks.app_name, "clinic_ui")
        self.assertEqual(self.hooks.app_version, "0.0.1")


if __name__ == "__main__":
    unittest.main()
