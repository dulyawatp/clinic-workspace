"""Static surface tests: the app exposes read-only whitelisted endpoints only.

The package is scanned with ``ast`` so no Frappe import is needed. Any new
whitelisted method, a non-GET method, or a guest-enabled method fails here.
"""

import ast
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = APP_ROOT / "clinic_ui"

EXPECTED_ENDPOINTS = {
    "clinic_ui.api.session.bootstrap",
    "clinic_ui.api.directory.synthetic_patients",
}

# Guest dispatch exists only so an anonymous/expired caller receives an explicit
# 401 and can reach sign-in. The directory endpoint stays non-guest.
EXPECTED_GUEST_ENDPOINTS = {"clinic_ui.api.session.bootstrap"}


def module_name(path):
    relative = path.relative_to(APP_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def whitelisted_functions(tree, path):
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            call = decorator if isinstance(decorator, ast.Call) else None
            target = call.func if call else decorator
            name = target.attr if isinstance(target, ast.Attribute) else None
            if name != "whitelist":
                continue
            methods = None
            allow_guest = None
            if call is not None:
                for keyword in call.keywords:
                    if keyword.arg == "methods":
                        methods = ast.literal_eval(keyword.value)
                    if keyword.arg == "allow_guest":
                        allow_guest = ast.literal_eval(keyword.value)
            found.append(
                {
                    "name": f"{module_name(path)}.{node.name}",
                    "methods": methods,
                    "allow_guest": allow_guest,
                }
            )
    return found


def app_whitelists():
    found = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found.extend(whitelisted_functions(tree, path))
    return found


class WhitelistSurfaceTests(unittest.TestCase):
    def test_exact_read_only_endpoint_set(self):
        found = app_whitelists()
        self.assertEqual({entry["name"] for entry in found}, EXPECTED_ENDPOINTS)

    def test_every_endpoint_is_get_only(self):
        for entry in app_whitelists():
            with self.subTest(endpoint=entry["name"]):
                self.assertEqual(entry["methods"], ["GET"])

    def test_only_bootstrap_dispatches_guests(self):
        guests = {
            entry["name"]
            for entry in app_whitelists()
            if entry["allow_guest"] is True
        }
        self.assertEqual(guests, EXPECTED_GUEST_ENDPOINTS)

    def test_directory_stays_non_guest(self):
        for entry in app_whitelists():
            if entry["name"] == "clinic_ui.api.directory.synthetic_patients":
                self.assertIn(entry["allow_guest"], (None, False))

    def test_guest_dispatch_is_declared_in_one_module(self):
        modules = [
            path.relative_to(PACKAGE_ROOT / "api").as_posix()
            for path in sorted((PACKAGE_ROOT / "api").rglob("*.py"))
            if "allow_guest=True" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(modules, ["session.py"])

    def test_no_mutation_transport_helpers_are_declared(self):
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(PACKAGE_ROOT.rglob("*.py"))
        )
        for forbidden in (
            "frappe.db.set_value",
            "frappe.db.delete",
            "frappe.new_doc",
            ".insert(",
            ".save(",
            ".submit(",
            ".cancel(",
            "methods=[\"POST\"]",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)


class ImportSafetyTests(unittest.TestCase):
    def test_core_and_endpoints_import_without_frappe(self):
        # Imported by the rest of this suite; assert the module really is absent
        # so the framework-independent claim is not accidentally satisfied by an
        # installed Frappe.
        import importlib.util

        self.assertIsNone(importlib.util.find_spec("frappe"))

    def test_policy_and_endpoint_layers_do_not_import_frappe(self):
        # Only ``clinic_ui.runtime.frappe_gateway`` may touch the framework.
        for directory in ("core", "endpoints"):
            for path in sorted((PACKAGE_ROOT / directory).rglob("*.py")):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imported = set()
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        imported.update(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imported.add(node.module or "")
                with self.subTest(module=module_name(path)):
                    self.assertFalse(
                        [name for name in imported if name.split(".")[0] == "frappe"]
                    )


if __name__ == "__main__":
    unittest.main()
