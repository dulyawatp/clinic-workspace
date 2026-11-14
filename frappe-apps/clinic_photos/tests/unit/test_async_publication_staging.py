"""Focused unit tests for the bounded, filesystem-only publication staging module.

These tests exercise the real module against the real OS filesystem inside a per-test
``tempfile.TemporaryDirectory``. Only the framework boundary is faked: a narrow
in-memory ``frappe`` module that answers ``get_site_path`` (the fixture in
``fake_frappe``). No Frappe import, bench, database, native image stack beyond the
installed Pillow inspection used by ``publication.validation``, network, subprocess or
deployment effect is involved.

Proven here: bounded image validation through the existing ``validation`` seams, a
server-generated random basename, confinement to the site's private staging directory
(never HTTP-exposed ``public/files``), restrictive directory/file modes, exclusive
no-follow creation without overwrite or retry, file+directory durability calls including
an unconditional fsync of the private parent on the create path (so a staging directory
created by a peer that never synced it is still durable before metadata is committed),
residue retention on failure, refusal when the process is not bound to a site or when the
site root is reached through a symlink, resolution of a relative
``./<site>/private/...`` result against the working directory with the same binding and
refusals for traversal, another site, an extra component or a symlinked site, refusal
instead of degraded opens when the host lacks the required
``O_NOFOLLOW``/``O_DIRECTORY``/``O_CLOEXEC``/``O_NONBLOCK`` flags, refusal of a FIFO at the
key without blocking, sha256/size/format integrity checks on read, and refusal of
arbitrary keys, traversal, symlinks, replaced objects, hard links, loosened modes,
truncated/appended/corrupted bytes, missing objects and a mismatch between the bound site
and the resolved staging path.

Not proven here: installed Frappe site resolution, MariaDB/transactional behaviour,
power-loss durability, multi-host or network filesystem semantics, EXIF handling,
backup/restore, retention/purge, and any HTTP route. A green run is not native
acceptance and does not authorize staging in a live site.
"""

import base64
import contextlib
import hashlib
import os
import re
import stat
import struct
import sys
import tempfile
import threading
import unittest
import zlib
from pathlib import Path
from unittest import mock

from fake_frappe import fake_frappe


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.publication import staging, validation

JPEG_2X2_BASE64 = (
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRof"
    "Hh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/2wBDAQkJCQwLDBgNDRgyIRwh"
    "MjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjIyMjL/wA"
    "ARCAACAAIDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QA"
    "tRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2"
    "JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eX"
    "qDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2u"
    "Hi4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8"
    "QAtREAAgECBAQDBAcFBAQAAQJ3AAECAxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYn"
    "LRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eH"
    "l6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2N"
    "na4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwDgqKKK8M/VD//Z"
)
JPEG_BYTES = base64.b64decode(JPEG_2X2_BASE64)

KEY_PATTERN = re.compile(r"[0-9a-f]{32}\.(?:jpg|png)")


def png_bytes(width=2, height=2):
    """Build a deterministic, valid 2x2 RGB PNG without any encoder dependency."""
    def chunk(kind, payload):
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


PNG_BYTES = png_bytes()


def tree(root):
    entries = []
    for base, directories, files in os.walk(root):
        for name in directories + files:
            entries.append(os.path.relpath(os.path.join(base, name), root))
    return sorted(entries)


@contextlib.contextmanager
def working_directory(path):
    """Run a block with ``path`` as the process working directory, then restore it."""
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


class BenchSites:
    """A bench-like ``sites`` directory that holds one bound site."""

    def __init__(self, bound_site="bound-site.local", create_private=True):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._temporary.name)
        self.sites = os.path.join(self.root, "sites")
        self.bound_site = bound_site
        self.site = os.path.join(self.sites, bound_site)
        os.makedirs(self.site)
        if create_private:
            os.makedirs(os.path.join(self.site, "private"))
        self._context = fake_frappe(site_path=self.site)
        self.frappe = self._context.__enter__()
        self.frappe.local.site = bound_site

    @property
    def staging_dir(self):
        return os.path.join(self.site, "private", "clinic_photo_staging")

    def relative_site_path(self, *parts):
        """The ``./<site>/...`` shape an HTTP process may get from ``get_site_path``."""
        return os.path.join(os.curdir, self.bound_site, *parts)

    def files(self):
        if not os.path.isdir(self.staging_dir):
            return []
        return sorted(
            name for name in os.listdir(self.staging_dir)
            if os.path.isfile(os.path.join(self.staging_dir, name))
            and not os.path.islink(os.path.join(self.staging_dir, name))
        )

    def close(self):
        self._context.__exit__(None, None, None)
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class StagingSite:
    """Per-test site: a real temporary directory with a real ``private`` child."""

    def __init__(self, site_name=None, create_private=True):
        self._temporary = tempfile.TemporaryDirectory()
        self.site = os.path.realpath(self._temporary.name)
        if create_private:
            os.makedirs(os.path.join(self.site, "private"))
        self._context = fake_frappe(site_path=self.site)
        self.frappe = self._context.__enter__()
        # Always bound: an unbound process must fail closed, so the fixture never
        # leaves ``local.site`` unset by accident.
        self.frappe.local.site = (
            site_name if site_name is not None else os.path.basename(self.site)
        )

    @property
    def staging_dir(self):
        return os.path.join(self.site, "private", "clinic_photo_staging")

    def staged_path(self, key):
        return os.path.join(self.staging_dir, key)

    def files(self):
        if not os.path.isdir(self.staging_dir):
            return []
        return sorted(
            name for name in os.listdir(self.staging_dir)
            if os.path.isfile(os.path.join(self.staging_dir, name))
            and not os.path.islink(os.path.join(self.staging_dir, name))
        )

    def close(self):
        self._context.__exit__(None, None, None)
        self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()


class StageImageTestCase(unittest.TestCase):
    """Exact bytes, random basename, private confinement and restrictive modes."""

    def test_stage_returns_bounded_metadata_for_the_exact_bytes(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)

            self.assertIsInstance(staged, staging.StagedImage)
            self.assertEqual(staged.sha256, hashlib.sha256(JPEG_BYTES).hexdigest())
            self.assertEqual(staged.size, len(JPEG_BYTES))
            self.assertEqual(staged.format, "JPEG")
            self.assertRegex(staged.key, KEY_PATTERN)
            self.assertTrue(staged.key.endswith(".jpg"))

    def test_staged_file_is_a_private_regular_file_with_exact_bytes(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)

            path = site.staged_path(staged.key)
            self.assertTrue(os.path.isfile(path))
            self.assertFalse(os.path.islink(path))
            info = os.lstat(path)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
            self.assertEqual(info.st_uid, os.geteuid())
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(info.st_size, len(JPEG_BYTES))

            directory = os.lstat(site.staging_dir)
            self.assertTrue(stat.S_ISDIR(directory.st_mode))
            self.assertEqual(stat.S_IMODE(directory.st_mode), 0o700)

            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), JPEG_BYTES)

    def test_key_is_a_random_basename_never_a_url_or_path(self):
        with StagingSite() as site:
            first = staging.stage_image(JPEG_BYTES)
            second = staging.stage_image(JPEG_BYTES)

            self.assertNotEqual(first.key, second.key)
            for staged in (first, second):
                self.assertRegex(staged.key, KEY_PATTERN)
                self.assertNotIn("/", staged.key)
                self.assertNotIn("\\", staged.key)
                self.assertNotIn("..", staged.key)
                self.assertEqual(os.path.basename(staged.key), staged.key)
                self.assertFalse(staged.key.startswith("/"))
            self.assertEqual(len(site.files()), 2)
            self.assertNotIn(
                "/public/",
                site.staged_path(first.key).replace(os.sep, "/"),
            )

    def test_staging_writes_nothing_into_public_files_or_the_database(self):
        with StagingSite() as site:
            before = tree(site.site)
            staging.stage_image(JPEG_BYTES)
            after = tree(site.site)

            added = [entry for entry in after if entry not in before]
            self.assertEqual(
                added,
                [
                    os.path.join("private", "clinic_photo_staging"),
                    os.path.join("private", "clinic_photo_staging", site.files()[0]),
                ],
            )
            self.assertFalse(os.path.exists(os.path.join(site.site, "public")))
            self.assertFalse(
                os.path.exists(os.path.join(site.site, "private", "files"))
            )
            self.assertEqual(site.frappe.db.docs, {})
            self.assertEqual(site.frappe.db.event_log, [])

    def test_png_is_identified_and_named_by_its_validated_format(self):
        with StagingSite() as site:
            staged = staging.stage_image(PNG_BYTES)

            self.assertEqual(staged.format, "PNG")
            self.assertEqual(staged.size, len(PNG_BYTES))
            self.assertEqual(staged.sha256, hashlib.sha256(PNG_BYTES).hexdigest())
            self.assertTrue(staged.key.endswith(".png"))
            self.assertEqual(staging.read_staged_image(staged), PNG_BYTES)

    def test_frappe_is_resolved_at_call_time_not_at_import_time(self):
        self.assertFalse(hasattr(staging, "frappe"))

    def test_file_and_directory_durability_are_both_requested(self):
        calls = []

        def record_fsync(descriptor):
            calls.append(descriptor)
            os.fsync(descriptor)

        with StagingSite() as site:
            with mock.patch.object(staging, "_fsync_file", side_effect=record_fsync) as file_sync:
                with mock.patch.object(
                    staging, "_fsync_directory", side_effect=record_fsync
                ) as directory_sync:
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(file_sync.call_count, 1)
            # One sync records the new staging directory in its private parent, one sync
            # covers the staging directory itself after the object is written.
            self.assertEqual(directory_sync.call_count, 2)
            self.assertEqual(len(calls), 3)


class DirectoryCreationDurabilityTestCase(unittest.TestCase):
    """Creating the staging directory is made durable in its private parent.

    The sync cannot be conditional on winning the ``mkdir`` race: a peer may create the
    directory and die before syncing its private parent, and this process would then
    commit metadata for objects inside a directory entry that is not yet durable.
    """

    def test_creating_the_staging_directory_syncs_the_private_parent_first(self):
        observed = []

        def record(descriptor):
            info = os.fstat(descriptor)
            observed.append((info.st_dev, info.st_ino))
            os.fsync(descriptor)

        with StagingSite() as site:
            with mock.patch.object(staging, "_fsync_directory", side_effect=record):
                staging.stage_image(JPEG_BYTES)

            private = os.stat(os.path.join(site.site, "private"))
            staging_directory = os.stat(site.staging_dir)

        private_identity = (private.st_dev, private.st_ino)
        staging_identity = (staging_directory.st_dev, staging_directory.st_ino)
        self.assertTrue(observed, "no directory durability call was made")
        self.assertEqual(observed[0], private_identity)
        self.assertEqual(observed.count(private_identity), 1)
        self.assertIn(staging_identity, observed)

    def test_peer_created_staging_directory_is_still_recorded_in_the_private_parent(self):
        observed = []
        entries_at_parent_sync = []

        with StagingSite() as site:
            # Stand-in for a peer that won ``mkdir`` and never synced the private parent.
            os.makedirs(site.staging_dir, mode=0o700)
            private = os.stat(os.path.join(site.site, "private"))
            staging_directory = os.stat(site.staging_dir)
            private_identity = (private.st_dev, private.st_ino)

            def record(descriptor):
                info = os.fstat(descriptor)
                identity = (info.st_dev, info.st_ino)
                observed.append(identity)
                if identity == private_identity:
                    # The parent is synced before this process creates its object.
                    entries_at_parent_sync.append(sorted(os.listdir(site.staging_dir)))
                os.fsync(descriptor)

            with mock.patch.object(staging, "_fsync_directory", side_effect=record):
                staged = staging.stage_image(JPEG_BYTES)

            self.assertEqual(len(site.files()), 1)
            self.assertEqual(staging.read_staged_image(staged), JPEG_BYTES)

        staging_identity = (staging_directory.st_dev, staging_directory.st_ino)
        self.assertIn(private_identity, observed)
        self.assertEqual(observed.count(private_identity), 1)
        self.assertEqual(entries_at_parent_sync, [[]])
        self.assertLess(observed.index(private_identity), observed.index(staging_identity))

    def test_private_parent_sync_failure_refuses_before_any_object_exists(self):
        with StagingSite() as site:
            os.makedirs(site.staging_dir, mode=0o700)
            with mock.patch.object(
                staging, "_fsync_directory", side_effect=OSError("simulated")
            ):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(site.files(), [])
            self.assertTrue(os.path.isdir(site.staging_dir))


class SiteBindingTestCase(unittest.TestCase):
    """The bound site is required and must be the site root that is actually opened."""

    def test_unbound_site_is_refused_for_staging(self):
        with StagingSite() as site:
            del site.frappe.local.site
            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_empty_bound_site_is_refused(self):
        with StagingSite() as site:
            site.frappe.local.site = ""
            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_non_string_bound_site_is_refused(self):
        for value in (None, 0, b"site", object()):
            with self.subTest(kind=type(value).__name__):
                with StagingSite() as site:
                    site.frappe.local.site = value
                    with self.assertRaises(staging.StagingRefused):
                        staging.stage_image(JPEG_BYTES)
                    self.assertFalse(os.path.exists(site.staging_dir))

    def test_unbound_site_is_refused_for_reading(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            del site.frappe.local.site
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)
            self.assertEqual(len(site.files()), 1)

    def test_symlinked_site_root_is_refused_even_when_the_bound_name_matches(self):
        with StagingSite(create_private=False) as site:
            real = os.path.join(site.site, "real-site")
            os.makedirs(os.path.join(real, "private"))
            link = os.path.join(site.site, "bound-site")
            os.symlink(real, link)
            site.frappe.get_site_path = lambda *parts: os.path.join(link, *parts)
            site.frappe.local.site = "bound-site"

            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)

            self.assertFalse(
                os.path.exists(os.path.join(real, "private", "clinic_photo_staging"))
            )
            self.assertEqual(os.listdir(os.path.join(real, "private")), [])

    def test_read_through_a_symlinked_site_root_is_refused(self):
        with StagingSite(create_private=False) as site:
            real = os.path.join(site.site, "real-site")
            os.makedirs(os.path.join(real, "private"))
            link = os.path.join(site.site, "bound-site")
            os.symlink(real, link)
            site.frappe.get_site_path = lambda *parts: os.path.join(real, *parts)
            site.frappe.local.site = "real-site"
            staged = staging.stage_image(JPEG_BYTES)

            site.frappe.get_site_path = lambda *parts: os.path.join(link, *parts)
            site.frappe.local.site = "bound-site"
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

            staged_dir = os.path.join(real, "private", "clinic_photo_staging")
            self.assertEqual(os.listdir(staged_dir), [staged.key])


class RelativeSitePathTestCase(unittest.TestCase):
    """A relative ``get_site_path`` result is resolved against the trusted working dir.

    An HTTP process can see ``./<site>/private/clinic_photo_staging`` with the bench
    ``sites`` directory as the working directory; that is legitimate and must stage into
    the same place as the absolute form, with every other check unchanged.
    """

    def test_relative_site_path_is_resolved_against_the_working_directory(self):
        with BenchSites() as bench:
            bench.frappe.get_site_path = bench.relative_site_path

            with working_directory(bench.sites):
                staged = staging.stage_image(JPEG_BYTES)
                self.assertEqual(staged.format, "JPEG")
                self.assertEqual(
                    staging.read_staged_image(staged), JPEG_BYTES
                )

            expected = os.path.join(bench.staging_dir, staged.key)
            self.assertTrue(os.path.isfile(expected))
            self.assertEqual(bench.files(), [staged.key])
            self.assertEqual(
                stat.S_IMODE(os.lstat(bench.staging_dir).st_mode), 0o700
            )
            self.assertEqual(stat.S_IMODE(os.lstat(expected).st_mode), 0o600)
            with open(expected, "rb") as handle:
                self.assertEqual(handle.read(), JPEG_BYTES)

    def test_relative_site_path_without_the_dot_prefix_is_supported(self):
        with BenchSites() as bench:
            bench.frappe.get_site_path = lambda *parts: os.path.join(
                bench.bound_site, *parts
            )

            with working_directory(bench.sites):
                staged = staging.stage_image(JPEG_BYTES)

            self.assertEqual(bench.files(), [staged.key])

    def test_relative_site_path_is_refused_outside_the_working_directory(self):
        with BenchSites(create_private=False) as bench:
            # A sibling tree that a traversal would reach, with a matching site name.
            outside = os.path.join(bench.root, "outside", bench.bound_site)
            os.makedirs(os.path.join(outside, "private"))
            bench.frappe.get_site_path = lambda *parts: os.path.join(
                os.curdir, os.pardir, "outside", bench.bound_site, *parts
            )

            with working_directory(bench.sites):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertFalse(
                os.path.exists(os.path.join(outside, "private", "clinic_photo_staging"))
            )
            self.assertEqual(os.listdir(os.path.join(outside, "private")), [])

    def test_relative_site_path_that_climbs_out_of_the_anchor_is_refused(self):
        with BenchSites(create_private=False) as bench:
            # A working directory inside the bound site: ``../private/...`` lands on the
            # real bound site, yet it is not the ``./<site>/...`` shape Frappe produces.
            inside = os.path.join(bench.site, "work")
            os.makedirs(inside)
            bench.frappe.get_site_path = lambda *parts: os.path.join(
                os.pardir, *parts
            )

            with working_directory(inside):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertFalse(os.path.exists(bench.staging_dir))

    def test_relative_site_path_naming_another_site_is_refused(self):
        with BenchSites(create_private=False) as bench:
            other = "other-site.local"
            os.makedirs(os.path.join(bench.sites, other, "private"))
            bench.frappe.get_site_path = lambda *parts: os.path.join(
                os.curdir, other, *parts
            )

            with working_directory(bench.sites):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertFalse(
                os.path.exists(
                    os.path.join(bench.sites, other, "private", "clinic_photo_staging")
                )
            )

    def test_relative_site_path_with_extra_components_is_refused(self):
        with BenchSites(create_private=False) as bench:
            nested = os.path.join(bench.sites, "wrapper", bench.bound_site)
            os.makedirs(os.path.join(nested, "private"))
            bench.frappe.get_site_path = lambda *parts: os.path.join(
                os.curdir, "wrapper", bench.bound_site, *parts
            )

            with working_directory(bench.sites):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertFalse(
                os.path.exists(os.path.join(nested, "private", "clinic_photo_staging"))
            )

    def test_relative_site_path_through_a_symlinked_site_is_refused(self):
        with BenchSites(create_private=False) as bench:
            outside = os.path.join(bench.root, "outside-site")
            os.makedirs(os.path.join(outside, "private"))
            os.rmdir(bench.site)  # empty: the fixture created it without ``private``
            os.symlink(outside, bench.site)
            bench.frappe.get_site_path = bench.relative_site_path

            with working_directory(bench.sites):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(os.listdir(os.path.join(outside, "private")), [])

    def test_relative_site_path_with_a_missing_private_directory_is_refused(self):
        with BenchSites(create_private=False) as bench:
            bench.frappe.get_site_path = bench.relative_site_path

            with working_directory(bench.sites):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertFalse(os.path.exists(bench.staging_dir))

    def test_relative_and_absolute_forms_address_the_same_staged_object(self):
        with BenchSites() as bench:
            bench.frappe.get_site_path = bench.relative_site_path
            with working_directory(bench.sites):
                staged = staging.stage_image(JPEG_BYTES)

            bench.frappe.get_site_path = lambda *parts: os.path.join(bench.site, *parts)
            self.assertEqual(staging.read_staged_image(staged), JPEG_BYTES)


class NonBlockingOpenTestCase(unittest.TestCase):
    """A non-regular object at the key is refused without blocking the reader."""

    def test_fifo_at_the_key_is_refused_without_blocking(self):
        with StagingSite() as site:
            os.makedirs(site.staging_dir, mode=0o700)
            key = "e" * 32 + ".jpg"
            os.mkfifo(site.staged_path(key))
            forged = staging.StagedImage(
                key=key,
                sha256=hashlib.sha256(JPEG_BYTES).hexdigest(),
                size=len(JPEG_BYTES),
                format="JPEG",
            )

            outcome = []

            def attempt():
                try:
                    staging.read_staged_image(forged)
                except BaseException as error:  # recorded, never raised, in this thread
                    outcome.append(type(error).__name__)
                else:
                    outcome.append("allowed")

            reader = threading.Thread(target=attempt, daemon=True)
            reader.start()
            reader.join(5)

            self.assertFalse(reader.is_alive(), "reading a FIFO blocked")
            self.assertEqual(outcome, ["StagedImageRefused"])
            self.assertTrue(os.path.exists(site.staged_path(key)))


class OpenFlagRequirementTestCase(unittest.TestCase):
    """Missing open(2) confinement flags fail closed instead of degrading silently."""

    def test_missing_no_follow_flag_fails_closed(self):
        with StagingSite() as site:
            with mock.patch.object(staging.os, "O_NOFOLLOW", 0):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_missing_directory_flag_fails_closed(self):
        with StagingSite() as site:
            with mock.patch.object(staging.os, "O_DIRECTORY", 0):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_missing_cloexec_flag_fails_closed(self):
        with StagingSite() as site:
            with mock.patch.object(staging.os, "O_CLOEXEC", 0):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_missing_non_blocking_flag_fails_closed_on_read(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            with mock.patch.object(staging.os, "O_NONBLOCK", 0):
                with self.assertRaises(staging.StagedImageRefused):
                    staging.read_staged_image(staged)
            self.assertEqual(len(site.files()), 1)


class StageRefusalTestCase(unittest.TestCase):
    """Bounded input, exclusive creation, no retry, no overwrite, residue kept."""

    def test_invalid_image_bytes_are_refused_before_any_write(self):
        invalid = [
            b"",
            b"not an image at all",
            b"GIF89a" + b"\x00" * 64,
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 32,
            b"\xff\xd8\xff" + b"\x00" * 32,
            b"x" * (validation.MAX_IMAGE_BYTES + 1),
        ]
        with StagingSite() as site:
            before = tree(site.site)
            for data in invalid:
                with self.subTest(size=len(data)):
                    with self.assertRaises(staging.StagingRefused):
                        staging.stage_image(data)
            self.assertEqual(tree(site.site), before)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_non_bytes_input_is_refused(self):
        for value in ("aGVsbG8=", bytearray(JPEG_BYTES), memoryview(JPEG_BYTES), None):
            with self.subTest(kind=type(value).__name__):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(value)

    def test_existing_target_is_never_overwritten_or_retried(self):
        fixed = "a" * 32
        with StagingSite() as site:
            os.makedirs(site.staging_dir, mode=0o700)
            target = site.staged_path(f"{fixed}.jpg")
            with open(target, "wb") as handle:
                handle.write(b"existing protected object")

            with mock.patch.object(
                staging.secrets, "token_hex", return_value=fixed
            ) as token:
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(token.call_count, 1)
            with open(target, "rb") as handle:
                self.assertEqual(handle.read(), b"existing protected object")
            self.assertEqual(site.files(), [f"{fixed}.jpg"])

    def test_pre_existing_symlink_at_the_generated_key_is_refused(self):
        fixed = "b" * 32
        with StagingSite() as site:
            os.makedirs(site.staging_dir, mode=0o700)
            outside = os.path.join(site.site, "outside-target.bin")
            with open(outside, "wb") as handle:
                handle.write(b"outside sentinel")
            target = site.staged_path(f"{fixed}.jpg")
            os.symlink(outside, target)

            with mock.patch.object(staging.secrets, "token_hex", return_value=fixed):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertTrue(os.path.islink(target))
            with open(outside, "rb") as handle:
                self.assertEqual(handle.read(), b"outside sentinel")

    def test_directory_at_the_generated_key_is_refused(self):
        fixed = "c" * 32
        with StagingSite() as site:
            os.makedirs(site.staged_path(f"{fixed}.jpg"))
            with mock.patch.object(staging.secrets, "token_hex", return_value=fixed):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)
            self.assertTrue(os.path.isdir(site.staged_path(f"{fixed}.jpg")))

    def test_symlinked_staging_directory_is_refused(self):
        with StagingSite() as site:
            outside = tempfile.mkdtemp(dir=site.site)
            private = os.path.join(site.site, "private")
            os.symlink(outside, os.path.join(private, "clinic_photo_staging"))

            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertEqual(os.listdir(outside), [])

    def test_symlinked_private_directory_is_refused(self):
        with StagingSite(create_private=False) as site:
            outside = tempfile.mkdtemp(dir=site.site)
            os.symlink(outside, os.path.join(site.site, "private"))

            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertEqual(os.listdir(outside), [])

    def test_missing_private_directory_is_refused_not_created(self):
        with StagingSite(create_private=False) as site:
            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_site_binding_mismatch_is_refused(self):
        with StagingSite(site_name="other-site.local") as site:
            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(site.staging_dir))

    def test_inconsistent_site_path_is_refused(self):
        with StagingSite() as site:
            def wrong_path(*parts):
                return os.path.join(site.site, "attacker", *parts)

            site.frappe.get_site_path = wrong_path
            with self.assertRaises(staging.StagingRefused):
                staging.stage_image(JPEG_BYTES)
            self.assertFalse(os.path.exists(os.path.join(site.site, "attacker")))

    def test_file_durability_failure_leaves_residue_and_never_retries(self):
        with StagingSite() as site:
            with mock.patch.object(
                staging, "_fsync_file", side_effect=OSError("simulated")
            ) as file_sync:
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(file_sync.call_count, 1)
            self.assertEqual(len(site.files()), 1)
            residue = os.path.join(site.staging_dir, site.files()[0])
            with open(residue, "rb") as handle:
                self.assertEqual(handle.read(), JPEG_BYTES)
            self.assertEqual(stat.S_IMODE(os.lstat(residue).st_mode), 0o600)

    def test_directory_durability_failure_leaves_residue_and_never_retries(self):
        calls = []

        def fail_the_staging_directory_sync(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError("simulated")
            os.fsync(descriptor)

        with StagingSite() as site:
            with mock.patch.object(
                staging,
                "_fsync_directory",
                side_effect=fail_the_staging_directory_sync,
            ):
                with self.assertRaises(staging.StagingRefused):
                    staging.stage_image(JPEG_BYTES)

            self.assertEqual(len(calls), 2)
            self.assertEqual(len(site.files()), 1)
            residue = os.path.join(site.staging_dir, site.files()[0])
            with open(residue, "rb") as handle:
                self.assertEqual(handle.read(), JPEG_BYTES)


class ReadStagedImageTestCase(unittest.TestCase):
    """Authenticated, bounded reads confined to the bound site's staging directory."""

    def test_roundtrip_returns_the_exact_staged_bytes(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            self.assertEqual(staging.read_staged_image(staged), JPEG_BYTES)
            self.assertEqual(len(site.files()), 1)

    def test_forged_hash_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            forged = staging.StagedImage(
                key=staged.key,
                sha256="0" * 64,
                size=staged.size,
                format=staged.format,
            )
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(forged)

    def test_corrupted_bytes_of_the_same_length_are_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            path = site.staged_path(staged.key)
            with open(path, "r+b") as handle:
                handle.seek(-1, os.SEEK_END)
                original = handle.read(1)
                handle.seek(-1, os.SEEK_END)
                handle.write(bytes([original[0] ^ 0xFF]))

            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_truncated_or_appended_object_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            path = site.staged_path(staged.key)

            with open(path, "r+b") as handle:
                handle.truncate(staged.size - 8)
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

            with open(path, "ab") as handle:
                handle.write(b"trailing")
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_missing_object_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            os.unlink(site.staged_path(staged.key))
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_symlink_replacement_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            path = site.staged_path(staged.key)
            copy = os.path.join(site.site, "copy-of-object.bin")
            os.replace(path, copy)
            os.symlink(copy, path)

            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)
            with open(copy, "rb") as handle:
                self.assertEqual(handle.read(), JPEG_BYTES)

    def test_non_regular_object_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            path = site.staged_path(staged.key)
            os.unlink(path)
            os.makedirs(path)

            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_loosened_permissions_are_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            os.chmod(site.staged_path(staged.key), 0o644)
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_hard_link_ambiguity_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            os.link(site.staged_path(staged.key), site.staged_path("second-link"))
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_arbitrary_file_outside_staging_is_never_read(self):
        with StagingSite() as site:
            key = "d" * 32 + ".png"
            outside_dir = os.path.join(site.site, "private", "files")
            os.makedirs(outside_dir)
            content = PNG_BYTES
            with open(os.path.join(outside_dir, key), "wb") as handle:
                handle.write(content)

            forged = staging.StagedImage(
                key=key,
                sha256=hashlib.sha256(content).hexdigest(),
                size=len(content),
                format="PNG",
            )
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(forged)

    def test_read_under_a_different_site_is_refused(self):
        with StagingSite() as first:
            staged = staging.stage_image(JPEG_BYTES)
            with StagingSite() as second:
                with self.assertRaises(staging.StagedImageRefused):
                    staging.read_staged_image(staged)
                self.assertEqual(second.files(), [])

    def test_read_refuses_when_the_bound_site_name_disagrees(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            site.frappe.local.site = "other-site.local"
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(staged)

    def test_format_mislabelled_metadata_is_refused(self):
        with StagingSite() as site:
            staged = staging.stage_image(JPEG_BYTES)
            mislabelled = staging.StagedImage(
                key=staged.key.replace(".jpg", ".png"),
                sha256=staged.sha256,
                size=staged.size,
                format="PNG",
            )
            with self.assertRaises(staging.StagedImageRefused):
                staging.read_staged_image(mislabelled)


class MetadataValidationTestCase(unittest.TestCase):
    """Metadata that cannot name a staging object is refused at construction."""

    def test_rejects_path_traversal_and_non_basename_keys(self):
        good = hashlib.sha256(JPEG_BYTES).hexdigest()
        for key in (
            "../escape.png",
            "a/b.png",
            "/etc/passwd",
            "..",
            ".",
            ".hidden.png",
            "C:\\escape.png",
            "a" * 31 + ".png",
            "A" * 32 + ".png",
            "a" * 32 + ".gif",
            "a" * 32,
            "",
            None,
            16,
        ):
            with self.subTest(key=key):
                with self.assertRaises(staging.StagedImageRefused):
                    staging.StagedImage(
                        key=key, sha256=good, size=len(JPEG_BYTES), format="PNG"
                    )

    def test_rejects_invalid_hash_size_and_format(self):
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".jpg", sha256="not-a-hash", size=1, format="JPEG"
            )
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".jpg",
                sha256="0" * 64,
                size=0,
                format="JPEG",
            )
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".jpg",
                sha256="0" * 64,
                size=validation.MAX_IMAGE_BYTES + 1,
                format="JPEG",
            )
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".jpg",
                sha256="0" * 64,
                size=True,
                format="JPEG",
            )
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".gif",
                sha256="0" * 64,
                size=1,
                format="GIF",
            )

    def test_extension_and_format_must_agree(self):
        with self.assertRaises(staging.StagedImageRefused):
            staging.StagedImage(
                key="a" * 32 + ".png",
                sha256="0" * 64,
                size=1,
                format="JPEG",
            )
        staged = staging.StagedImage(
            key="a" * 32 + ".png", sha256="0" * 64, size=1, format="PNG"
        )
        self.assertEqual(staged.format, "PNG")

    def test_staged_image_is_immutable_and_slot_based(self):
        staged = staging.StagedImage(
            key="a" * 32 + ".jpg",
            sha256="0" * 64,
            size=1,
            format="JPEG",
        )
        with self.assertRaises(AttributeError):
            staged.key = "b" * 32 + ".jpg"
        self.assertFalse(hasattr(staged, "__dict__"))

    def test_read_refuses_anything_that_is_not_a_staged_image(self):
        with StagingSite():
            for value in (None, "a" * 32 + ".jpg", {"key": "a" * 32 + ".jpg"}):
                with self.subTest(kind=type(value).__name__):
                    with self.assertRaises(staging.StagedImageRefused):
                        staging.read_staged_image(value)


if __name__ == "__main__":
    unittest.main()
