"""Tests that every ``modules.txt`` entry resolves to a real Python package.

Frappe builds ``frappe.local.app_modules`` from ``modules.txt`` by scrubbing each
line and then, in ``frappe.model.sync.sync_for`` (v16.53), importing
``<app>.<module>`` and reading its ``__file__``:

```python
for module_name in frappe.local.app_modules.get(app_name) or []:
    folder = os.path.dirname(frappe.get_module(app_name + "." + module_name).__file__)
```

So ``Clinic UI`` in ``modules.txt`` means the installed app must provide the
package ``clinic_ui.clinic_ui`` with a real ``__init__.py``. A namespace package
is not enough: its ``__file__`` is ``None``. Without the package, install and
migrate fail before any of this app's code runs.

These tests need no Frappe, site or network: they import the app package the same
way the installed app would.
"""

import ast
import importlib
import importlib.util
import unittest
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_NAME = "clinic_ui"
PACKAGE_ROOT = APP_ROOT / PACKAGE_NAME
MODULES_FILE = PACKAGE_ROOT / "modules.txt"


def scrub(module_name):
    """Mirror of ``frappe.scrub``: ``Clinic UI`` -> ``clinic_ui``."""
    return module_name.replace(" ", "_").replace("-", "_").lower()


def declared_modules():
    return [
        line.strip()
        for line in MODULES_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


class ModulesTxtTests(unittest.TestCase):
    def test_modules_file_declares_the_clinic_module(self):
        self.assertEqual(declared_modules(), ["Clinic UI"])

    def test_scrubbed_names_are_unique(self):
        scrubbed = [scrub(name) for name in declared_modules()]
        self.assertEqual(len(scrubbed), len(set(scrubbed)))


class ModulePackageTests(unittest.TestCase):
    def setUp(self):
        self.modules = [scrub(name) for name in declared_modules()]

    def test_every_module_has_a_package_directory(self):
        for module in self.modules:
            with self.subTest(module=module):
                self.assertTrue((PACKAGE_ROOT / module).is_dir())

    def test_every_module_has_an_init_file(self):
        # A namespace package has `__file__ is None`, which is what
        # `sync_for`'s `os.path.dirname(...)` would crash on.
        for module in self.modules:
            with self.subTest(module=module):
                self.assertTrue((PACKAGE_ROOT / module / "__init__.py").is_file())

    def test_every_module_imports_as_a_real_package(self):
        for module in self.modules:
            with self.subTest(module=module):
                imported = importlib.import_module(f"{PACKAGE_NAME}.{module}")
                self.assertIsNotNone(imported.__file__)
                self.assertEqual(
                    Path(imported.__file__).resolve(),
                    (PACKAGE_ROOT / module / "__init__.py").resolve(),
                )

    def test_module_packages_import_no_code(self):
        # The nested package is a Frappe module anchor, not a place for logic:
        # importing it must stay free of framework and side effects.
        for module in self.modules:
            path = PACKAGE_ROOT / module / "__init__.py"
            with self.subTest(module=module):
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                imports = [
                    node
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Import | ast.ImportFrom)
                ]
                self.assertEqual(imports, [])

    def test_frappe_is_not_required_to_import_the_app_package(self):
        # Guards the claim above: this suite never has Frappe installed, so a
        # successful import cannot be hiding behind an installed framework.
        self.assertIsNone(importlib.util.find_spec("frappe"))


if __name__ == "__main__":
    unittest.main()
