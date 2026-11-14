"""Tests for ``clinic_ui.core.assets``: the Vite manifest -> shell asset mapping.

The shell has to name content-hashed build files, and it must only name files
that are actually deployed. A manifest is build output, so shape validation is
not enough: each referenced file must exist under the build root, resolve inside
it (symlinks included) and be a regular file. Anything else fails closed.

These tests use temporary build roots only: no network, no Frappe, no committed
bundle.
"""

import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from clinic_ui.core import assets

ENTRY = {
    "file": "assets/index-BXY6xa-J.js",
    "name": "index",
    "src": "index.html",
    "isEntry": True,
    "css": ["assets/index-P6wyMZyJ.css"],
}


class BuildRoot:
    """A temporary native build root: ``<root>/.vite/manifest.json`` + files."""

    def __init__(self, parent):
        self.root = Path(parent)
        self.manifest = self.root / ".vite" / "manifest.json"
        self.manifest.parent.mkdir(parents=True, exist_ok=True)

    def write_manifest(self, payload):
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")

    def write_bytes(self, payload):
        self.manifest.write_bytes(payload)

    def write_file(self, relative, content="built"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class ShellAssetsTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.build = BuildRoot(self._tmp.name)

    def read(self, payload, files=("assets/index-BXY6xa-J.js", "assets/index-P6wyMZyJ.css")):
        self.build.write_manifest(payload)
        for relative in files:
            self.build.write_file(relative)
        return assets.shell_assets(self.build.root)

    def assert_fails(self, payload, files=("assets/index-BXY6xa-J.js", "assets/index-P6wyMZyJ.css")):
        with self.assertRaises(assets.AssetManifestError):
            self.read(payload, files)


class ResolvedAssetTests(ShellAssetsTests):
    def test_entry_script_and_styles_are_published_under_the_app_asset_prefix(self):
        resolved = self.read({"index.html": ENTRY})

        self.assertEqual(
            resolved.scripts, ("/assets/clinic_ui/ui/assets/index-BXY6xa-J.js",)
        )
        self.assertEqual(
            resolved.styles, ("/assets/clinic_ui/ui/assets/index-P6wyMZyJ.css",)
        )

    def test_entry_without_css_has_no_styles(self):
        entry = {key: value for key, value in ENTRY.items() if key != "css"}

        resolved = self.read({"index.html": entry}, files=("assets/index-BXY6xa-J.js",))

        self.assertEqual(resolved.styles, ())
        self.assertEqual(len(resolved.scripts), 1)

    def test_resolution_is_deterministic_for_the_same_build(self):
        payload = {"index.html": ENTRY}

        first = self.read(payload)
        second = self.read(payload)

        self.assertEqual(first, second)
        self.assertEqual(first.scripts, second.scripts)

    def test_a_new_build_hash_changes_the_url(self):
        resolved = self.read(
            {"index.html": dict(ENTRY, file="assets/index-zzz999.js")},
            files=("assets/index-zzz999.js", "assets/index-P6wyMZyJ.css"),
        )

        self.assertEqual(
            resolved.scripts, ("/assets/clinic_ui/ui/assets/index-zzz999.js",)
        )

    def test_css_order_is_preserved_and_duplicates_are_dropped(self):
        entry = dict(ENTRY, css=["assets/b.css", "assets/a.css", "assets/b.css"])

        resolved = self.read(
            {"index.html": entry},
            files=("assets/index-BXY6xa-J.js", "assets/a.css", "assets/b.css"),
        )

        self.assertEqual(
            resolved.styles,
            (
                "/assets/clinic_ui/ui/assets/b.css",
                "/assets/clinic_ui/ui/assets/a.css",
            ),
        )

    def test_extra_manifest_entries_are_ignored(self):
        payload = {
            "index.html": ENTRY,
            "src/main.tsx": {"file": "assets/main-unused.js", "isEntry": False},
        }

        resolved = self.read(payload)

        self.assertEqual(
            resolved.scripts, ("/assets/clinic_ui/ui/assets/index-BXY6xa-J.js",)
        )

    def test_shell_assets_are_immutable(self):
        resolved = self.read({"index.html": ENTRY})

        with self.assertRaises(Exception):
            resolved.scripts = ()


class MissingFileTests(ShellAssetsTests):
    """A manifest that names files the build did not produce must fail closed."""

    def test_missing_entry_script_fails_closed(self):
        self.assert_fails(
            {"index.html": ENTRY}, files=("assets/index-P6wyMZyJ.css",)
        )

    def test_missing_stylesheet_fails_closed(self):
        self.assert_fails({"index.html": ENTRY}, files=("assets/index-BXY6xa-J.js",))

    def test_no_deployed_files_at_all_fails_closed(self):
        self.assert_fails({"index.html": ENTRY}, files=())

    def test_directory_instead_of_file_fails_closed(self):
        self.build.write_manifest({"index.html": ENTRY})
        (self.build.root / "assets" / "index-BXY6xa-J.js").mkdir(parents=True)
        self.build.write_file("assets/index-P6wyMZyJ.css")

        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_missing_build_root_fails_closed(self):
        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(Path(self._tmp.name) / "not-built")


class ContainmentTests(ShellAssetsTests):
    """The build root is the boundary: nothing outside it may be referenced."""

    def test_symlink_escaping_the_build_root_fails_closed(self):
        outside = Path(self._tmp.name).parent / "clinic-outside-asset.js"
        outside.write_text("outside", encoding="utf-8")
        self.addCleanup(outside.unlink)
        self.build.write_manifest({"index.html": ENTRY})
        target = self.build.root / "assets" / "index-BXY6xa-J.js"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(outside)
        self.build.write_file("assets/index-P6wyMZyJ.css")

        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_symlinked_directory_escaping_the_build_root_fails_closed(self):
        outside = Path(self._tmp.name).parent / "clinic-outside-dir"
        outside.mkdir(exist_ok=True)
        (outside / "index-BXY6xa-J.js").write_text("outside", encoding="utf-8")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        self.build.write_manifest({"index.html": ENTRY})
        (self.build.root / "assets").symlink_to(outside, target_is_directory=True)
        self.build.write_file("assets/index-P6wyMZyJ.css")

        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_symlink_inside_the_build_root_is_allowed(self):
        target = self.build.write_file("assets/real-entry.js")
        link = self.build.root / "assets" / "index-BXY6xa-J.js"
        link.symlink_to(target)
        self.build.write_file("assets/index-P6wyMZyJ.css")
        self.build.write_manifest({"index.html": ENTRY})

        resolved = assets.shell_assets(self.build.root)

        self.assertEqual(
            resolved.scripts, ("/assets/clinic_ui/ui/assets/index-BXY6xa-J.js",)
        )


class ManifestReadTests(ShellAssetsTests):
    def test_missing_manifest_file_fails_closed(self):
        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_invalid_utf8_manifest_fails_closed(self):
        self.build.write_bytes(b'{"index.html": {"file": "assets/index-\xff\xfe.js"}}')
        self.build.write_file("assets/index-BXY6xa-J.js")

        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_invalid_json_fails_closed(self):
        self.build.write_bytes(b"{not json")

        with self.assertRaises(assets.AssetManifestError):
            assets.shell_assets(self.build.root)

    def test_manifest_that_is_not_an_object_fails_closed(self):
        self.assert_fails([ENTRY])

    def test_missing_entry_fails_closed(self):
        self.assert_fails({"src/main.tsx": ENTRY})

    def test_entry_that_is_not_an_object_fails_closed(self):
        self.assert_fails({"index.html": ENTRY["file"]})

    def test_entry_without_a_file_fails_closed(self):
        entry = {key: value for key, value in ENTRY.items() if key != "file"}

        self.assert_fails({"index.html": entry})

    def test_non_javascript_entry_fails_closed(self):
        self.assert_fails({"index.html": dict(ENTRY, file="assets/index-BXY6xa-J.css")})

    def test_css_that_is_not_a_list_fails_closed(self):
        self.assert_fails({"index.html": dict(ENTRY, css="assets/index.css")})

    def test_css_entry_that_is_not_a_string_fails_closed(self):
        self.assert_fails({"index.html": dict(ENTRY, css=[1])})

    def test_traversal_and_absolute_paths_fail_closed(self):
        unsafe = (
            "../outside.js",
            "/etc/passwd.js",
            "assets/../../secret.js",
            "assets//double.js",
            "assets\\windows.js",
            "",
            "assets/",
            "assets/index.js?query=1",
            "https://cdn.example.test/index.js",
        )
        for value in unsafe:
            with self.subTest(file=value):
                self.assert_fails({"index.html": dict(ENTRY, file=value)})

    def test_unsafe_css_path_fails_closed(self):
        self.assert_fails({"index.html": dict(ENTRY, css=["../escape.css"])})

    def test_error_messages_do_not_echo_manifest_contents(self):
        secretish = "secret-token-value"

        with self.assertRaises(assets.AssetManifestError) as caught:
            self.read({"index.html": dict(ENTRY, file=f"assets/{secretish}/x.js?{secretish}")})

        self.assertNotIn(secretish, str(caught.exception))

    def test_error_messages_do_not_echo_filesystem_paths(self):
        with self.assertRaises(assets.AssetManifestError) as caught:
            assets.shell_assets(self.build.root)

        self.assertNotIn(str(self._tmp.name), str(caught.exception))


class ManifestLocationTests(unittest.TestCase):
    def test_manifest_lives_under_public_ui_so_frappe_serves_the_build(self):
        self.assertEqual(assets.SHELL_STATIC_SEGMENTS, ("public", "ui"))
        self.assertEqual(assets.SHELL_MANIFEST_SEGMENTS, (".vite", "manifest.json"))

    def test_asset_url_prefix_matches_the_vite_base(self):
        self.assertEqual(assets.ASSET_URL_PREFIX, "/assets/clinic_ui/ui/")


if __name__ == "__main__":
    unittest.main()
