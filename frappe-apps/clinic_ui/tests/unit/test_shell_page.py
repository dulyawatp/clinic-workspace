"""Tests for ``clinic_ui/www/clinic.py``: the website shell context.

A fake ``frappe`` module is injected into ``sys.modules`` for the duration of
each test and removed afterwards, so the real shell module can be executed and
its ``get_context`` contract observed without an installed site. What is proven
here is the offline contract only: cache headers, fail-closed behaviour, the
exact context keys and the absence of any per-user value.
"""

import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
SHELL_MODULE_PATH = APP_ROOT / "clinic_ui" / "www" / "clinic.py"
SHELL_TEMPLATE_PATH = APP_ROOT / "clinic_ui" / "www" / "clinic.html"

NO_STORE = "no-store,no-cache,must-revalidate,max-age=0"

MANIFEST = {
    "index.html": {
        "file": "assets/index-BXY6xa-J.js",
        "name": "index",
        "src": "index.html",
        "isEntry": True,
        "css": ["assets/index-P6wyMZyJ.css"],
    }
}

EXPECTED_CONTEXT_KEYS = {
    "title",
    "no_cache",
    "sitemap",
    "ui_available",
    "ui_scripts",
    "ui_styles",
}


class FakeFrappe(types.SimpleNamespace):
    """Just the surface the shell module touches."""

    def __init__(self, app_path):
        super().__init__()
        self.local = types.SimpleNamespace(response_headers={})
        self._app_path = Path(app_path)

    def get_app_path(self, app_name, *joins):
        return str(self._app_path.joinpath(*joins))


def load_shell_module(frappe_stub):
    with mock.patch.dict(sys.modules, {"frappe": frappe_stub}):
        spec = importlib.util.spec_from_file_location(
            "clinic_ui_shell_page_under_test", SHELL_MODULE_PATH
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class ShellContextTestCase(unittest.TestCase):
    BUILT_FILES = ("assets/index-BXY6xa-J.js", "assets/index-P6wyMZyJ.css")

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.app_path = Path(self._tmp.name)
        self.build_root = self.app_path / "public" / "ui"
        (self.build_root / ".vite").mkdir(parents=True)
        self.frappe = FakeFrappe(self.app_path)
        self.module = load_shell_module(self.frappe)

    def write_manifest(self, payload=MANIFEST, files=BUILT_FILES):
        path = self.build_root / ".vite" / "manifest.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        for relative in files:
            built = self.build_root / relative
            built.parent.mkdir(parents=True, exist_ok=True)
            built.write_text("built", encoding="utf-8")

    def context(self):
        context = {}
        self.module.get_context(context)
        return context


class CacheContractTests(ShellContextTestCase):
    def test_module_disables_the_website_page_cache(self):
        self.assertIs(self.module.no_cache, True)

    def test_module_keeps_the_shell_out_of_the_sitemap(self):
        self.assertEqual(self.module.sitemap, 0)

    def test_get_context_sets_no_store_for_every_outcome(self):
        for payload in (MANIFEST, None):
            with self.subTest(manifest=payload):
                self.frappe.local.response_headers = {}
                if payload is not None:
                    self.write_manifest(payload)
                self.context()
                self.assertEqual(
                    self.frappe.local.response_headers.get("Cache-Control"), NO_STORE
                )

    def test_context_marks_itself_uncached(self):
        self.write_manifest(MANIFEST)

        self.assertIs(self.context()["no_cache"], True)


class ManifestContextTests(ShellContextTestCase):
    def test_hashed_build_files_are_added_to_the_context(self):
        self.write_manifest(MANIFEST)

        context = self.context()

        self.assertIs(context["ui_available"], True)
        self.assertEqual(
            context["ui_scripts"], ["/assets/clinic_ui/ui/assets/index-BXY6xa-J.js"]
        )
        self.assertEqual(
            context["ui_styles"], ["/assets/clinic_ui/ui/assets/index-P6wyMZyJ.css"]
        )

    def test_successful_shell_keeps_the_default_status(self):
        self.write_manifest(MANIFEST)

        self.assertNotIn("http_status_code", self.context())

    def test_context_exposes_only_the_shell_keys(self):
        self.write_manifest(MANIFEST)

        context = self.context()

        self.assertEqual(set(context), EXPECTED_CONTEXT_KEYS)
        for forbidden in (
            "user",
            "fullname",
            "session",
            "csrf_token",
            "sid",
            "roles",
            "capabilities",
            "route",
            "path",
        ):
            self.assertNotIn(forbidden, context)

    def test_title_is_static_and_never_interpolated(self):
        self.write_manifest(MANIFEST)

        self.assertEqual(self.context()["title"], "Clinic")


class FailClosedTests(ShellContextTestCase):
    def test_missing_manifest_returns_503_without_any_asset(self):
        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["ui_scripts"], [])
        self.assertEqual(context["ui_styles"], [])
        self.assertEqual(context["http_status_code"], 503)

    def test_broken_manifest_returns_503_without_any_asset(self):
        (self.app_path / "public" / "ui" / ".vite" / "manifest.json").write_text(
            "{not json", encoding="utf-8"
        )

        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["http_status_code"], 503)

    def test_manifest_without_the_shell_entry_returns_503(self):
        self.write_manifest({"src/main.tsx": MANIFEST["index.html"]})

        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["http_status_code"], 503)

    def test_missing_entry_script_returns_503_without_any_asset(self):
        # The manifest names a script the build did not deploy: a shell that
        # points at it would render an empty page, so the page must fail instead.
        self.write_manifest(MANIFEST, files=("assets/index-P6wyMZyJ.css",))

        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["ui_scripts"], [])
        self.assertEqual(context["ui_styles"], [])
        self.assertEqual(context["http_status_code"], 503)

    def test_missing_stylesheet_returns_503_without_any_asset(self):
        self.write_manifest(MANIFEST, files=("assets/index-BXY6xa-J.js",))

        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["http_status_code"], 503)

    def test_invalid_utf8_manifest_returns_503(self):
        path = self.build_root / ".vite" / "manifest.json"
        path.write_bytes(b'{"index.html": {"file": "assets/index-\xff.js"}}')

        context = self.context()

        self.assertIs(context["ui_available"], False)
        self.assertEqual(context["http_status_code"], 503)


class ShellTemplateTests(unittest.TestCase):
    def setUp(self):
        self.template = SHELL_TEMPLATE_PATH.read_text(encoding="utf-8")

    def test_template_exists_next_to_the_module(self):
        self.assertTrue(SHELL_TEMPLATE_PATH.is_file())
        self.assertTrue(SHELL_MODULE_PATH.is_file())

    def test_template_renders_the_hashed_files_it_is_given(self):
        self.assertIn("{{ href }}", self.template)
        self.assertIn("{{ src }}", self.template)
        self.assertIn("ui_styles", self.template)
        self.assertIn("ui_scripts", self.template)

    def test_template_hardcodes_no_build_hash_and_no_other_asset_root(self):
        self.assertNotIn("index-", self.template)
        self.assertNotIn("/assets/", self.template)

    def test_template_does_not_extend_the_frappe_website_layout(self):
        self.assertNotIn("{% extends", self.template)
        self.assertNotIn("{%- extends", self.template)
        self.assertNotIn("base.html", self.template)

    def test_template_carries_no_user_or_session_value(self):
        for forbidden in (
            "{{ user",
            "{{ fullname",
            "{{ frappe",
            "csrf",
            "session",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.template)

    def test_template_has_a_single_root_mount_and_no_desk_bundle(self):
        self.assertEqual(self.template.count('id="root"'), 1)
        self.assertNotIn("frappe-web", self.template)
        self.assertNotIn("desk", self.template)

    def test_template_guards_the_assets_with_the_availability_flag(self):
        self.assertIn("ui_available", self.template)


try:  # Jinja is a Frappe dependency, not a test dependency.
    import jinja2
except ImportError:  # pragma: no cover - exercised only without jinja2 installed
    jinja2 = None


@unittest.skipIf(jinja2 is None, "jinja2 is not installed")
class ShellTemplateRenderTests(ShellContextTestCase):
    """Render the real template with the real controller output.

    ``StrictUndefined`` is used so a template variable that the controller stops
    providing fails here instead of rendering as an empty string.
    """

    def render(self):
        template = jinja2.Template(
            SHELL_TEMPLATE_PATH.read_text(encoding="utf-8"),
            undefined=jinja2.StrictUndefined,
        )
        return template.render(**self.context())

    def test_rendered_shell_references_the_resolved_files(self):
        self.write_manifest(MANIFEST)

        html = self.render()

        self.assertIn(
            '<script type="module" crossorigin '
            'src="/assets/clinic_ui/ui/assets/index-BXY6xa-J.js"></script>',
            html,
        )
        self.assertIn(
            '<link rel="stylesheet" crossorigin '
            'href="/assets/clinic_ui/ui/assets/index-P6wyMZyJ.css" />',
            html,
        )
        self.assertEqual(html.count('id="root"'), 1)

    def test_rendered_shell_needs_no_base_tag(self):
        self.write_manifest(MANIFEST)

        html = self.render()

        self.assertNotIn("<base", html)
        self.assertIn('src="/assets/clinic_ui/ui/', html)

    def test_rendered_failure_page_references_no_asset(self):
        html = self.render()

        self.assertNotIn("/assets/", html)
        self.assertNotIn("<script", html)
        self.assertIn("unavailable", html)


if __name__ == "__main__":
    unittest.main()
