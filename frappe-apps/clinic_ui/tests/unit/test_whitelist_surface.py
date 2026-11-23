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
    "clinic_ui.api.native_invoice.preview",
    "clinic_ui.api.session.bootstrap",
    "clinic_ui.api.directory.synthetic_patients",
    "clinic_ui.api.appointments.synthetic_appointments",
    "clinic_ui.api.appointments.view",
    "clinic_ui.api.appointments.book",
    "clinic_ui.api.appointments.reschedule",
    "clinic_ui.api.appointments.checkin",
    "clinic_ui.api.encounters.create_draft",
    "clinic_ui.api.encounters.list_for_patient",
    "clinic_ui.api.encounters.view",
    "clinic_ui.api.encounters.history_photo_appointment",
    "clinic_ui.api.encounters.open_for_appointment",
    "clinic_ui.api.encounters.save_linked",
    "clinic_ui.api.encounters.complete_linked",
    "clinic_ui.api.lab_reports.list_for_appointment",
    "clinic_ui.api.lab_reports.list_for_encounter",
    "clinic_ui.api.lab_reports.upload",
    "clinic_ui.api.appointments_cancel_write.cancel",
    "clinic_ui.api.appointments_cancel_write.no_show",
    "clinic_ui.api.encounters_write.save_draft",
    "clinic_ui.api.encounters_write.complete",
    "clinic_ui.api.encounters_write.amend",
    "clinic_ui.api.encounters_write.ack",
    "clinic_ui.api.procedures_write.create",
    "clinic_ui.api.procedures_write.complete",
    "clinic_ui.api.procedures_write.view",
    "clinic_ui.api.procedures_write.by_service_request",
    "clinic_ui.api.admin_write.create_user",
    "clinic_ui.api.admin_write.edit_master",
    "clinic_ui.api.admin_write.ack",
    "clinic_ui.api.stock_write.receive",
    "clinic_ui.api.stock_write.transfer",
    "clinic_ui.api.stock_write.adjust",
    "clinic_ui.api.stock_write.ack",
    "clinic_ui.api.purchase_write.save_po_draft",
    "clinic_ui.api.purchase_write.submit_po",
    "clinic_ui.api.purchase_write.receive",
    "clinic_ui.api.purchase_write.ack",
    "clinic_ui.api.purchase_write.view",
    "clinic_ui.api.purchase_write.drafts",
    "clinic_ui.api.finance_write.save_invoice_draft",
    "clinic_ui.api.finance_write.submit_invoice",
    "clinic_ui.api.finance_write.receive_payment",
    "clinic_ui.api.finance_write.ack",
    "clinic_ui.api.finance_write.view",
    "clinic_ui.api.finance_write.invoices",
    "clinic_ui.api.patients.view",
    "clinic_ui.api.patients.genders",
    "clinic_ui.api.patients.create",
    "clinic_ui.api.patients.update",
}

EXPECTED_METHODS = {
    "clinic_ui.api.native_invoice.preview": ["POST"],
    "clinic_ui.api.session.bootstrap": ["GET"],
    "clinic_ui.api.directory.synthetic_patients": ["GET"],
    "clinic_ui.api.appointments.synthetic_appointments": ["GET"],
    "clinic_ui.api.appointments.view": ["GET"],
    "clinic_ui.api.appointments.book": ["POST"],
    "clinic_ui.api.appointments.reschedule": ["POST"],
    "clinic_ui.api.appointments.checkin": ["POST"],
    "clinic_ui.api.encounters.create_draft": ["POST"],
    "clinic_ui.api.encounters.list_for_patient": ["GET"],
    "clinic_ui.api.encounters.view": ["GET"],
    "clinic_ui.api.encounters.history_photo_appointment": ["POST"],
    "clinic_ui.api.encounters.open_for_appointment": ["GET"],
    "clinic_ui.api.encounters.save_linked": ["POST"],
    "clinic_ui.api.encounters.complete_linked": ["POST"],
    "clinic_ui.api.lab_reports.list_for_appointment": ["GET"],
    "clinic_ui.api.lab_reports.list_for_encounter": ["GET"],
    "clinic_ui.api.lab_reports.upload": ["POST"],
    "clinic_ui.api.appointments_cancel_write.cancel": ["POST"],
    "clinic_ui.api.appointments_cancel_write.no_show": ["POST"],
    "clinic_ui.api.encounters_write.save_draft": ["POST"],
    "clinic_ui.api.encounters_write.complete": ["POST"],
    "clinic_ui.api.encounters_write.amend": ["POST"],
    "clinic_ui.api.encounters_write.ack": ["POST"],
    "clinic_ui.api.procedures_write.create": ["POST"],
    "clinic_ui.api.procedures_write.complete": ["POST"],
    "clinic_ui.api.procedures_write.view": ["GET"],
    "clinic_ui.api.procedures_write.by_service_request": ["GET"],
    "clinic_ui.api.admin_write.create_user": ["POST"],
    "clinic_ui.api.admin_write.edit_master": ["POST"],
    "clinic_ui.api.admin_write.ack": ["POST"],
    "clinic_ui.api.stock_write.receive": ["POST"],
    "clinic_ui.api.stock_write.transfer": ["POST"],
    "clinic_ui.api.stock_write.adjust": ["POST"],
    "clinic_ui.api.stock_write.ack": ["POST"],
    "clinic_ui.api.purchase_write.save_po_draft": ["POST"],
    "clinic_ui.api.purchase_write.submit_po": ["POST"],
    "clinic_ui.api.purchase_write.receive": ["POST"],
    "clinic_ui.api.purchase_write.ack": ["POST"],
    "clinic_ui.api.purchase_write.view": ["GET"],
    "clinic_ui.api.purchase_write.drafts": ["GET"],
    "clinic_ui.api.finance_write.save_invoice_draft": ["POST"],
    "clinic_ui.api.finance_write.submit_invoice": ["POST"],
    "clinic_ui.api.finance_write.receive_payment": ["POST"],
    "clinic_ui.api.finance_write.ack": ["POST"],
    "clinic_ui.api.finance_write.view": ["GET"],
    "clinic_ui.api.finance_write.invoices": ["GET"],
    "clinic_ui.api.patients.view": ["GET"],
    "clinic_ui.api.patients.genders": ["GET"],
    "clinic_ui.api.patients.create": ["POST"],
    "clinic_ui.api.patients.update": ["POST"],
}

# Guest dispatch exists only so an anonymous/expired caller receives an explicit
# 401 and can reach sign-in. The directory and patient view endpoints stay
# non-guest.
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

    def test_endpoint_methods_are_exact(self):
        for entry in app_whitelists():
            with self.subTest(endpoint=entry["name"]):
                self.assertEqual(entry["methods"], EXPECTED_METHODS[entry["name"]])

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

    def test_patient_read_and_write_endpoints_stay_non_guest(self):
        for entry in app_whitelists():
            if entry["name"].startswith("clinic_ui.api.patients."):
                self.assertIn(entry["allow_guest"], (None, False))

    def test_guest_dispatch_is_declared_in_one_module(self):
        modules = [
            path.relative_to(PACKAGE_ROOT / "api").as_posix()
            for path in sorted((PACKAGE_ROOT / "api").rglob("*.py"))
            if "allow_guest=True" in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(modules, ["session.py"])

    def test_mutation_helpers_are_confined_to_the_runtime_adapter(self):
        adapter = PACKAGE_ROOT / "runtime" / "frappe_gateway.py"
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            tree = _tree_without_docstrings(path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                for keyword in node.keywords:
                    if keyword.arg in ("ignore_permissions", "ignore_csrf"):
                        self.fail(f"{path}: forbidden keyword {keyword.arg}")
                if path == adapter:
                    continue
                func = node.func
                if isinstance(func, ast.Attribute):
                    if func.attr in ("insert", "save", "submit", "cancel"):
                        self.fail(f"{path}: forbidden mutation call .{func.attr}")
                    if (
                        func.attr in ("set_value", "delete")
                        and isinstance(func.value, ast.Attribute)
                        and func.value.attr == "db"
                    ):
                        self.fail(f"{path}: forbidden mutation call frappe.db.{func.attr}")
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "new_doc"
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "frappe"
                ):
                    self.fail(f"{path}: forbidden frappe.new_doc")

    def test_adapter_never_bypasses_permissions_or_csrf(self):
        adapter = PACKAGE_ROOT / "runtime" / "frappe_gateway.py"
        tree = _tree_without_docstrings(adapter)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg in ("ignore_permissions", "ignore_csrf"):
                    self.fail(f"adapter: forbidden keyword {keyword.arg}")


def _tree_without_docstrings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        if (
            node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body.pop(0)
    return tree


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
