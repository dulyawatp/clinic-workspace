"""Unit tests for the external raw-reader guard and the response no-store hook.

These tests drive the real adapter logic through ``tests.unit.fake_frappe``: a
deliberately narrow in-memory stand-in for only the attributes the adapter calls. A
fake ERPNext module records whether the raw reader was reached. No installed Frappe
MRO, DocPerm, nginx, cache or HTTP behaviour is claimed here; Task 4 owns that.

Two boundaries are under test:

* ``convert_mt940_to_csv`` must refuse protected and reserved-orphan bytes **before**
  the installed ERPNext raw reader is imported/called, and delegate unrelated inputs
  unchanged (same arguments, same exception object).
* ``after_request`` must apply ``Cache-Control: no-store``, ``Pragma: no-cache`` and
  ``Expires: 0`` only to responses whose server-owned per-request marker was set,
  replacing contradictory freshness headers and preserving everything else. The hook
  performs no storage I/O and makes no authorization decision.
"""
import contextlib
import importlib
import json
import sys
import types
import unittest

from clinic_photos.access import frappe_context
from fake_frappe import fake_frappe


AUTHOR = "author@example.test"

ORIGINAL_KEY = "/private/files/clinic-photo-11111111-1111-1111-1111-111111111111.png"
ANNOTATION_KEY = "/private/files/clinic-annotation-22222222-2222-2222-2222-222222222222.json"
# Registered original whose canonical key is not in the reserved namespace. Its
# basename and File docname are used to prove that filename input is only a lookup
# key resolved to authoritative identity, never classification evidence by itself.
LEGACY_KEY = "/private/files/legacy-original.png"
ORPHAN_KEY = "/private/files/clinic-photo-99999999-9999-9999-9999-999999999999.png"

UNRELATED_PRIVATE = "/private/files/statement-2026-07.mt940"
UNRELATED_PUBLIC = "/files/statement-2026-07.mt940"
UNRELATED_BASENAME = "statement-2026-07.mt940"

PHOTO = "PHOTO-1"
LEGACY_PHOTO = "PHOTO-LEGACY"
VERSION = "VERSION-1"
DRAFT = "DRAFT-1"
FILE_ORIGINAL = "FILE-ORIGINAL"
FILE_ANNOTATION = "FILE-ANNOTATION"
FILE_LEGACY = "FILE-LEGACY"

RAW_READER_MODULE = (
    "erpnext.accounts.doctype.bank_statement_import.bank_statement_import"
)
RAW_READER_PATH = RAW_READER_MODULE + ".convert_mt940_to_csv"
WRAPPER_PATH = "clinic_photos.access.entrypoints.convert_mt940_to_csv"
NATIVE_ZIP_MODULE = "frappe.core.api.file"
NATIVE_ZIP_PATH = NATIVE_ZIP_MODULE + ".zip_files"
ZIP_WRAPPER_PATH = "clinic_photos.access.entrypoints.zip_files"


def file_like(name, key):
    """Minimal File-document stand-in the adapter reads attributes from."""
    return types.SimpleNamespace(
        name=name,
        file_url=key,
        file_name=key.rsplit("/", 1)[-1],
        content_hash="hash-" + name.lower(),
        file_size=11,
        is_private=1,
        owner=AUTHOR,
    )


def protected_docs():
    return {
        "User": {AUTHOR: {"enabled": 1}},
        "Has Role": {
            "HAS-ROLE-1": {
                "parent": AUTHOR,
                "parenttype": "User",
                "role": "Physician",
            }
        },
        "File": {
            FILE_ORIGINAL: {
                "name": FILE_ORIGINAL,
                "file_url": ORIGINAL_KEY,
                "file_name": ORIGINAL_KEY.rsplit("/", 1)[-1],
                "content_hash": "hash-original",
                "file_size": 11,
                "is_private": 1,
                "owner": AUTHOR,
            },
            FILE_ANNOTATION: {
                "name": FILE_ANNOTATION,
                "file_url": ANNOTATION_KEY,
                "file_name": ANNOTATION_KEY.rsplit("/", 1)[-1],
                "content_hash": "hash-annotation",
                "file_size": 12,
                "is_private": 1,
                "owner": AUTHOR,
            },
            FILE_LEGACY: {
                "name": FILE_LEGACY,
                "file_url": LEGACY_KEY,
                "file_name": LEGACY_KEY.rsplit("/", 1)[-1],
                "content_hash": "hash-legacy",
                "file_size": 13,
                "is_private": 1,
                "owner": AUTHOR,
            },
            "FILE-UNRELATED": {
                "name": "FILE-UNRELATED",
                "file_url": "/private/files/unrelated-statement.png",
                "file_name": "unrelated-statement.png",
                # Same content hash as the protected original: a hash-equal file at
                # a different path is still unrelated and must delegate.
                "content_hash": "hash-original",
                "file_size": 11,
                "is_private": 1,
                "owner": AUTHOR,
            },
            # Duplicate basename, one protected and one unrelated: native's single OR
            # query picks an arbitrary first row; the approved policy refuses this
            # ambiguous match.
            "FILE-DUP-UNRELATED": {
                "name": "FILE-DUP-UNRELATED",
                "file_url": "/private/files/dup-shared.mt940",
                "file_name": "dup-shared.mt940",
                "content_hash": "hash-dup-unrelated",
                "file_size": 21,
                "is_private": 1,
                "owner": AUTHOR,
            },
            "FILE-DUP-PROTECTED": {
                "name": "FILE-DUP-PROTECTED",
                "file_url": ORIGINAL_KEY,
                "file_name": "dup-shared.mt940",
                "content_hash": "hash-dup-protected",
                "file_size": 22,
                "is_private": 1,
                "owner": AUTHOR,
            },
            # Conflicting match kinds for one supplied value: the docname match is
            # unrelated while the filename match is protected.
            "conflict-target": {
                "name": "conflict-target",
                "file_url": "/private/files/conflict-unrelated.mt940",
                "file_name": "conflict-other.mt940",
                "content_hash": "hash-conflict-doc",
                "file_size": 23,
                "is_private": 1,
                "owner": AUTHOR,
            },
            "FILE-CONFLICT-NAME": {
                "name": "FILE-CONFLICT-NAME",
                "file_url": ORIGINAL_KEY,
                "file_name": "conflict-target",
                "content_hash": "hash-conflict-name",
                "file_size": 24,
                "is_private": 1,
                "owner": AUTHOR,
            },
            # Supplied path text is unrelated, but a File row's filename equals that
            # path while its target is protected.
            "FILE-CONFLICT-PATH": {
                "name": "FILE-CONFLICT-PATH",
                "file_url": ORIGINAL_KEY,
                "file_name": "/private/files/conflict-path.mt940",
                "content_hash": "hash-conflict-path",
                "file_size": 25,
                "is_private": 1,
                "owner": AUTHOR,
            },
            "FILE-DUP-UNRELATED-A": {
                "name": "FILE-DUP-UNRELATED-A",
                "file_url": "/private/files/dup-unrelated-a.mt940",
                "file_name": "dup-unrelated.mt940",
                "content_hash": "hash-u-a",
                "file_size": 26,
                "is_private": 1,
                "owner": AUTHOR,
            },
            "FILE-DUP-UNRELATED-B": {
                "name": "FILE-DUP-UNRELATED-B",
                "file_url": "/private/files/dup-unrelated-b.mt940",
                "file_name": "dup-unrelated.mt940",
                "content_hash": "hash-u-b",
                "file_size": 27,
                "is_private": 1,
                "owner": AUTHOR,
            },
        },
        "Clinic Photo": {
            PHOTO: {
                "name": PHOTO,
                "storage_key": ORIGINAL_KEY,
                "original_file": FILE_ORIGINAL,
                "patient": "PATIENT-1",
            },
            LEGACY_PHOTO: {
                "name": LEGACY_PHOTO,
                "storage_key": LEGACY_KEY,
                "original_file": FILE_LEGACY,
                "patient": "PATIENT-1",
            },
        },
        "Clinic Photo Annotation Version": {
            VERSION: {
                "name": VERSION,
                "storage_key": ANNOTATION_KEY,
                "annotation_file": FILE_ANNOTATION,
                "draft": DRAFT,
                "state": "committed",
            }
        },
        "Clinic Photo Draft": {
            DRAFT: {
                "name": DRAFT,
                "author": AUTHOR,
                "photo": PHOTO,
            }
        },
    }


class RawReaderProbe:
    """Stand-in for the installed ERPNext callable, recording exact arguments."""

    def __init__(self, result="converted.csv", error=None):
        self.calls = []
        self.result = result
        self.error = error

    def __call__(self, data_import, mt940_file_path):
        self.calls.append((data_import, mt940_file_path))
        if self.error is not None:
            raise self.error
        return self.result


@contextlib.contextmanager
def raw_reader_module(probe):
    """Install a fake ERPNext module so no real finance code is imported."""
    module = types.ModuleType(RAW_READER_MODULE)
    module.convert_mt940_to_csv = probe
    previous = sys.modules.get(RAW_READER_MODULE)
    sys.modules[RAW_READER_MODULE] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop(RAW_READER_MODULE, None)
        else:
            sys.modules[RAW_READER_MODULE] = previous


class NativeZipProbe:
    """Local emulation of the installed ``File.zip_files`` loop for tests.

    Installed order is authoritative (task-3-native-excerpts.txt, file.py:1000-1014):
    ``get_doc`` → folder skip → module-level ``has_permission`` skip **before**
    ``get_content``. Only an entry that passes the prefilter reaches the real read
    guard. This is a narrow fake native endpoint, not the installed implementation.
    """

    def __init__(self, fake):
        self.fake = fake
        self.calls = []
        self.shipped = []
        self.guard_reached = []
        self.seen = []

    def __call__(self, files):
        self.calls.append(files)
        parsed = json.loads(files) if isinstance(files, str) else files
        shipped = []
        for name in parsed:
            self.seen.append(name)
            row = self.fake.db.docs.get("File", {}).get(name)
            if row is None:
                raise self.fake.DoesNotExistError(name)
            doc = file_like(name, row.get("file_url") or "")
            if row.get("is_folder"):
                continue
            if not self.fake.has_permission("File", "read", doc=doc):
                # Native prefilter skip: get_content is never called for this entry.
                continue
            self.guard_reached.append(name)
            frappe_context.require_protected_read(doc)
            shipped.append(name)
        self.shipped.append(list(shipped))
        return {"file_names": shipped}


@contextlib.contextmanager
def native_zip_module(probe):
    """Install a fake ``frappe.core.api.file`` module around the wrapper call."""
    module = types.ModuleType(NATIVE_ZIP_MODULE)
    module.zip_files = probe
    previous = sys.modules.get(NATIVE_ZIP_MODULE)
    sys.modules[NATIVE_ZIP_MODULE] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop(NATIVE_ZIP_MODULE, None)
        else:
            sys.modules[NATIVE_ZIP_MODULE] = previous


class HeaderStore(dict):
    """Case-insensitive, single-value header mapping like Werkzeug ``Headers``.

    Only the operations the production hook uses are implemented; values replace
    prior headers irrespective of case, matching Werkzeug's ``__setitem__``.
    """

    def __init__(self, values=None):
        super().__init__()
        for key, value in (values or {}).items():
            self[key] = value

    def _find(self, key):
        for existing in dict.keys(self):
            if existing.lower() == str(key).lower():
                return existing
        return None

    def __setitem__(self, key, value):
        existing = self._find(key)
        if existing is not None:
            dict.__delitem__(self, existing)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        existing = self._find(key)
        if existing is None:
            raise KeyError(key)
        dict.__delitem__(self, existing)

    def __contains__(self, key):
        return self._find(key) is not None

    def get(self, key, default=None):
        existing = self._find(key)
        if existing is None:
            return default
        return dict.__getitem__(self, existing)


class FakeResponse:
    def __init__(self, headers=None, status_code=200):
        self.headers = HeaderStore(headers)
        self.status_code = status_code


def denial_headers():
    return {
        "Cache-Control": "private,max-age=3600,stale-while-revalidate=86400",
        "Expires": "Wed, 21 Oct 2026 07:28:00 GMT",
        "age": "12",
        "Surrogate-Control": "max-age=3600",
        "Content-Type": "image/png",
    }


class AdapterTestCase(unittest.TestCase):
    def setUp(self):
        from clinic_photos.access import entrypoints

        self.entrypoints = entrypoints
        self.context = fake_frappe(docs=protected_docs(), user=AUTHOR)
        self.fake = self.context.__enter__()
        self.addCleanup(self.context.__exit__, None, None, None)

    def marked(self):
        return bool(
            getattr(
                self.fake.local, self.entrypoints.PROTECTED_REQUEST_ATTR, False
            )
        )


class BankImportRawReaderGuardTests(AdapterTestCase):
    PROTECTED_INPUTS = (
        ("registered original key", ORIGINAL_KEY),
        ("registered annotation key", ANNOTATION_KEY),
        ("registered non-reserved key", LEGACY_KEY),
        ("reserved orphan key", ORPHAN_KEY),
        (
            "encoded reserved alias",
            ORIGINAL_KEY.replace("clinic-photo-", "clinic%2Dphoto-", 1),
        ),
        (
            "encoded private root alias",
            "/%70rivate/files/" + ORIGINAL_KEY.rsplit("/", 1)[-1],
        ),
        ("protected File docname", FILE_ORIGINAL),
        ("protected annotation File docname", FILE_ANNOTATION),
        ("protected File basename", LEGACY_KEY.rsplit("/", 1)[-1]),
        (
            "traversal private spelling",
            "/private/files/../files/" + ORIGINAL_KEY.rsplit("/", 1)[-1],
        ),
    )

    def assert_rejected_before_raw_reader(self, value):
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            with self.assertRaises(self.fake.PermissionError):
                self.entrypoints.convert_mt940_to_csv("BSI-1", value)
        self.assertEqual(probe.calls, [], "raw reader must not be reached")
        self.assertTrue(self.marked(), "rejection must mark the response")

    def test_protected_inputs_are_rejected_before_the_raw_reader(self):
        for label, value in self.PROTECTED_INPUTS:
            with self.subTest(label):
                self.fake.local = types.SimpleNamespace()
                self.assert_rejected_before_raw_reader(value)

    def test_protected_paths_are_rejected_without_a_finance_document(self):
        # Guard ordering: the missing Bank Statement Import document is never loaded
        # because the protected input is refused first.
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            with self.assertRaises(self.fake.PermissionError):
                self.entrypoints.convert_mt940_to_csv(
                    "DOES-NOT-EXIST", ORIGINAL_KEY
                )
        self.assertEqual(probe.calls, [])

    def test_unrelated_inputs_delegate_with_unchanged_arguments_and_result(self):
        probe = RawReaderProbe(result="/private/files/converted.csv")
        for value in (UNRELATED_PRIVATE, UNRELATED_PUBLIC, UNRELATED_BASENAME):
            with self.subTest(value):
                probe.calls.clear()
                with raw_reader_module(probe):
                    result = self.entrypoints.convert_mt940_to_csv("BSI-1", value)
                self.assertEqual(result, "/private/files/converted.csv")
                self.assertEqual(probe.calls, [("BSI-1", value)])
                self.assertFalse(self.marked())

    def test_unrelated_native_exception_is_delegated_unchanged(self):
        error = RuntimeError("native mt940 failure")
        probe = RawReaderProbe(error=error)
        with raw_reader_module(probe):
            with self.assertRaises(RuntimeError) as raised:
                self.entrypoints.convert_mt940_to_csv("BSI-1", UNRELATED_PRIVATE)
        self.assertIs(raised.exception, error)
        self.assertEqual(probe.calls, [("BSI-1", UNRELATED_PRIVATE)])
        self.assertFalse(self.marked())

    def test_hash_equal_file_at_a_different_path_delegates(self):
        # Protection is storage identity, never a content hash: this File shares the
        # protected original's content hash but has no authoritative reference.
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            result = self.entrypoints.convert_mt940_to_csv(
                "BSI-1", "unrelated-statement.png"
            )
        self.assertEqual(result, "converted.csv")
        self.assertEqual(probe.calls, [("BSI-1", "unrelated-statement.png")])
        self.assertFalse(self.marked())

    def test_mixed_protected_and_unrelated_duplicate_matches_are_refused(self):
        # Approved amendment: native's single OR query would pick an arbitrary first
        # row; this ambiguous match is refused instead.
        self.assert_rejected_before_raw_reader("dup-shared.mt940")

    def test_conflicting_docname_and_filename_match_is_refused(self):
        # One matching row is unrelated by docname, another protected by filename.
        self.assert_rejected_before_raw_reader("conflict-target")

    def test_supplied_path_with_conflicting_row_target_is_refused(self):
        # The supplied path itself is unrelated, but a matching File row points at
        # protected bytes; native may resolve to that row first.
        self.assert_rejected_before_raw_reader("/private/files/conflict-path.mt940")

    def test_unambiguous_unrelated_duplicate_matches_delegate(self):
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            result = self.entrypoints.convert_mt940_to_csv(
                "BSI-1", "dup-unrelated.mt940"
            )
        self.assertEqual(result, "converted.csv")
        self.assertEqual(probe.calls, [("BSI-1", "dup-unrelated.mt940")])
        self.assertFalse(self.marked())

    def test_unambiguous_unrelated_named_row_delegates(self):
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            result = self.entrypoints.convert_mt940_to_csv(
                "BSI-1", "FILE-DUP-UNRELATED"
            )
        self.assertEqual(result, "converted.csv")
        self.assertEqual(probe.calls, [("BSI-1", "FILE-DUP-UNRELATED")])
        self.assertFalse(self.marked())

    def test_unknown_named_input_delegates_without_denial_or_leak(self):
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            result = self.entrypoints.convert_mt940_to_csv(
                "BSI-1", "no-such-file.mt940"
            )
        self.assertEqual(result, "converted.csv")
        self.assertEqual(probe.calls, [("BSI-1", "no-such-file.mt940")])
        self.assertFalse(self.marked())

    def test_non_text_input_is_delegated_unchanged(self):
        probe = RawReaderProbe()
        with raw_reader_module(probe):
            self.entrypoints.convert_mt940_to_csv("BSI-1", None)
        self.assertEqual(probe.calls, [("BSI-1", None)])

    def test_rejection_does_not_change_finance_permissions_or_roles(self):
        with open(self.entrypoints.__file__, encoding="utf-8") as handle:
            module_source = handle.read()
        for forbidden in (
            "add_roles",
            "check_permission",
            "ignore_permissions",
            "get_doc",
            "Chart of Accounts Importer",
        ):
            with self.subTest(forbidden):
                self.assertNotIn(forbidden, module_source)


class ResponseCacheHookTests(AdapterTestCase):
    def test_guard_denial_marks_the_request_before_authorization(self):
        # No native File/document permission is granted in this fake, so this is a
        # denial. The marker must already be set for the header hook.
        with self.assertRaises(self.fake.PermissionError):
            frappe_context.require_protected_read(
                file_like(FILE_ORIGINAL, ORIGINAL_KEY)
            )
        self.assertTrue(self.marked())

    def test_direct_protected_url_is_marked_without_authorization(self):
        self.fake.local.request = types.SimpleNamespace(path=ORIGINAL_KEY)
        self.entrypoints.mark_direct_private_request()
        self.assertTrue(self.marked())

    def test_reserved_and_encoded_direct_paths_are_marked(self):
        for path in (
            ORPHAN_KEY,
            "/%70rivate/files/" + ORIGINAL_KEY.rsplit("/", 1)[-1],
        ):
            with self.subTest(path):
                self.fake.local = types.SimpleNamespace(
                    request=types.SimpleNamespace(path=path)
                )
                self.entrypoints.mark_direct_private_request()
                self.assertTrue(self.marked())

    def test_unrelated_direct_private_path_is_not_marked(self):
        self.fake.local.request = types.SimpleNamespace(path=UNRELATED_PRIVATE)
        self.entrypoints.mark_direct_private_request()
        self.assertFalse(self.marked())

    def test_protected_response_replaces_conflicting_freshness_headers(self):
        self.fake.local.request = types.SimpleNamespace(path=ORIGINAL_KEY)
        response = FakeResponse(
            headers=dict(
                denial_headers(),
                **{"X-Accel-Redirect": "/protected/private/files/x.png"},
            )
        )
        self.entrypoints.mark_direct_private_request()
        self.entrypoints.after_request(self.fake.local.request, response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")
        self.assertNotIn("Age", response.headers)
        self.assertNotIn("Surrogate-Control", response.headers)
        self.assertEqual(
            response.headers["X-Accel-Redirect"],
            "/protected/private/files/x.png",
        )
        self.assertEqual(response.headers["Content-Type"], "image/png")

    def test_normal_streamed_protected_response_is_no_store(self):
        request = types.SimpleNamespace(path=UNRELATED_PRIVATE)
        response = FakeResponse(headers=denial_headers())
        self.fake.local.request = request
        self.entrypoints.mark_protected_request()
        self.entrypoints.after_request(request, response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")

    def test_allowed_protected_read_is_also_no_store(self):
        self.fake.permissions[("File", "read", FILE_ORIGINAL)] = True
        self.fake.permissions[("Clinic Photo", "read", PHOTO)] = True
        request = types.SimpleNamespace(
            path="/api/method/frappe.core.api.file.download_file"
        )
        self.fake.local.request = request

        frappe_context.require_protected_read(
            file_like(FILE_ORIGINAL, ORIGINAL_KEY)
        )

        self.assertTrue(self.marked())
        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(request, response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_zip_style_content_denial_receives_no_store(self):
        request = types.SimpleNamespace(path="/api/method/frappe.core.api.file.zip_files")
        self.fake.local.request = request
        with self.assertRaises(self.fake.PermissionError):
            frappe_context.require_protected_read(
                file_like(FILE_ANNOTATION, ANNOTATION_KEY)
            )
        response = FakeResponse(headers={"Cache-Control": "no-cache"}, status_code=403)
        self.entrypoints.after_request(request, response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Expires"], "0")

    def test_unrelated_response_headers_are_unchanged(self):
        request = types.SimpleNamespace(path=UNRELATED_PRIVATE)
        self.fake.local.request = request
        self.entrypoints.mark_direct_private_request()
        headers = denial_headers()
        response = FakeResponse(headers=headers)
        self.entrypoints.after_request(request, response)
        self.assertEqual(dict(response.headers), headers)
        self.assertFalse(self.marked())

    def test_client_parameters_cannot_mark_a_response(self):
        request = types.SimpleNamespace(
            path=UNRELATED_PRIVATE,
            args={"protected": "1", "no_store": "1"},
            headers={"X-Clinic-Protected": "1"},
        )
        self.fake.local.request = request
        self.fake.form_dict = {"protected": 1, "no_store": 1}
        self.entrypoints.mark_direct_private_request()
        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(request, response)
        self.assertEqual(
            response.headers["Cache-Control"],
            "private,max-age=3600,stale-while-revalidate=86400",
        )

    def test_header_hook_performs_no_storage_io_and_no_authorization(self):
        request = types.SimpleNamespace(path=ORIGINAL_KEY)
        self.fake.local.request = request
        self.entrypoints.mark_protected_request()

        class Exploding:
            def __getattr__(self, name):
                raise AssertionError(f"header hook touched storage: {name}")

        self.fake.db = Exploding()

        def explode(*args, **kwargs):
            raise AssertionError("header hook consulted authorization")

        self.fake.has_permission = explode
        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(request, response)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertTrue(self.marked())


class NativeZipPrefilterMarkerTests(AdapterTestCase):
    """The exact ZIP marker requirement, exercised in native prefilter order.

    Native ``File.zip_files`` skips an entry at module-level ``has_permission`` before
    ``get_content``. The wrapper must set the protected-response marker before that
    skip so skipped protected entries and mixed archives still receive the exact trio.
    It must not authorize, deny or stream on its own: only mark, then delegate.
    """

    def setUp(self):
        super().setUp()
        self.fake.DoesNotExistError = type("DoesNotExistError", (Exception,), {})
        self.zip_request = types.SimpleNamespace(
            path="/api/method/frappe.core.api.file.zip_files"
        )
        self.fake.local.request = self.zip_request

    def assert_no_store(self, response):
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertEqual(response.headers["Expires"], "0")

    def test_denied_only_protected_entry_is_marked_before_native_skip(self):
        probe = NativeZipProbe(self.fake)
        payload = json.dumps([FILE_ORIGINAL])
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        self.assertEqual(probe.calls, [payload])
        self.assertEqual(result, {"file_names": []})
        self.assertEqual(probe.shipped, [[]])
        # Native prefilter skipped the protected entry before any content read.
        self.assertEqual(probe.guard_reached, [])
        self.assertTrue(self.marked())

        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(self.zip_request, response)
        self.assert_no_store(response)

    def test_mixed_zip_marks_protected_entries_and_ships_only_allowed_ones(self):
        self.fake.permissions[("File", "read", "FILE-UNRELATED")] = True
        probe = NativeZipProbe(self.fake)
        payload = json.dumps([FILE_ORIGINAL, "FILE-UNRELATED"])
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        self.assertEqual(result, {"file_names": ["FILE-UNRELATED"]})
        self.assertEqual(probe.guard_reached, ["FILE-UNRELATED"])
        self.assertTrue(self.marked())

        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(self.zip_request, response)
        self.assert_no_store(response)

    def test_protected_entry_passing_native_prefilter_is_denied_by_the_guard(self):
        self.fake.permissions[("File", "read", FILE_ORIGINAL)] = True
        probe = NativeZipProbe(self.fake)
        payload = json.dumps([FILE_ORIGINAL])
        with native_zip_module(probe):
            with self.assertRaises(self.fake.PermissionError):
                self.entrypoints.zip_files(payload)

        # Wrapper marked before delegation; the real read guard then denied.
        self.assertTrue(self.marked())
        self.assertEqual(probe.guard_reached, [FILE_ORIGINAL])
        self.assertEqual(probe.shipped, [])

    def test_denied_only_protected_mapping_key_is_marked_before_native_skip(self):
        # Native iterates a parsed mapping's keys, so a JSON object names File docs.
        probe = NativeZipProbe(self.fake)
        payload = json.dumps({FILE_ORIGINAL: True})
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        self.assertEqual(probe.seen, [FILE_ORIGINAL])
        self.assertEqual(result, {"file_names": []})
        self.assertEqual(probe.guard_reached, [])
        self.assertTrue(self.marked())

        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(self.zip_request, response)
        self.assert_no_store(response)

    def test_mixed_mapping_marks_and_ships_only_allowed_keys(self):
        self.fake.permissions[("File", "read", "FILE-UNRELATED")] = True
        probe = NativeZipProbe(self.fake)
        payload = json.dumps({FILE_ORIGINAL: 1, "FILE-UNRELATED": 1})
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        self.assertEqual(result, {"file_names": ["FILE-UNRELATED"]})
        self.assertEqual(probe.seen, [FILE_ORIGINAL, "FILE-UNRELATED"])
        self.assertTrue(self.marked())

        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(self.zip_request, response)
        self.assert_no_store(response)

    def test_parsed_json_string_keeps_native_character_iteration(self):
        # A JSON string parses to str; native iterates its characters.
        probe = NativeZipProbe(self.fake)
        payload = json.dumps(FILE_ORIGINAL)
        with native_zip_module(probe):
            with self.assertRaises(self.fake.DoesNotExistError):
                self.entrypoints.zip_files(payload)

        self.assertEqual(probe.calls, [payload])
        self.assertEqual(probe.seen, [FILE_ORIGINAL[0]])
        self.assertFalse(self.marked())

    def test_noniterable_scalar_is_delegated_unchanged(self):
        probe = NativeZipProbe(self.fake)
        payload = 5
        with native_zip_module(probe):
            with self.assertRaises(TypeError):
                self.entrypoints.zip_files(payload)

        # Same object, no wrapper rejection or replacement; native raises.
        self.assertIs(probe.calls[0], payload)
        self.assertFalse(self.marked())

    def test_one_shot_iterator_is_not_consumed_by_marking(self):
        probe = NativeZipProbe(self.fake)
        payload = iter([FILE_ORIGINAL])
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        # Native sees the iterator's entry, proving marking did not exhaust it.
        self.assertEqual(probe.seen, [FILE_ORIGINAL])
        self.assertIs(probe.calls[0], payload)
        self.assertEqual(result, {"file_names": []})

    def test_unrelated_zip_delegates_unchanged_and_keeps_native_headers(self):
        self.fake.permissions[("File", "read", "FILE-UNRELATED")] = True
        probe = NativeZipProbe(self.fake)
        payload = json.dumps(["FILE-UNRELATED"])
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        self.assertEqual(result, {"file_names": ["FILE-UNRELATED"]})
        self.assertEqual(probe.calls, [payload])
        self.assertFalse(self.marked())

        response = FakeResponse(headers=denial_headers())
        self.entrypoints.after_request(self.zip_request, response)
        self.assertEqual(
            dict(response.headers), denial_headers()
        )

    def test_malformed_files_argument_preserves_native_error_semantics(self):
        probe = NativeZipProbe(self.fake)
        payload = "{not-json"
        with native_zip_module(probe):
            with self.assertRaises(ValueError):
                self.entrypoints.zip_files(payload)

        # Marker parse failure is swallowed; native receives the original value and
        # raises its own error. No protected decision is invented.
        self.assertEqual(probe.calls, [payload])
        self.assertFalse(self.marked())

    def test_unknown_zip_entry_reaches_native_error_without_wrapper_denial(self):
        probe = NativeZipProbe(self.fake)
        payload = json.dumps(["MISSING-FILE"])
        with native_zip_module(probe):
            with self.assertRaises(self.fake.DoesNotExistError):
                self.entrypoints.zip_files(payload)

        self.assertEqual(probe.calls, [payload])
        self.assertFalse(self.marked())

    def test_guest_zip_gets_no_data_before_native_authorization(self):
        self.fake.session.user = "Guest"
        probe = NativeZipProbe(self.fake)
        payload = json.dumps([FILE_ORIGINAL])
        with native_zip_module(probe):
            result = self.entrypoints.zip_files(payload)

        # Guest prefilter semantics stay native: nothing is authorized or streamed by
        # the wrapper, and the native skip yields no file entries.
        self.assertEqual(result, {"file_names": []})
        self.assertEqual(probe.guard_reached, [])
        self.assertNotIn("filecontent", result)


class HookRegistrationTests(unittest.TestCase):
    def setUp(self):
        from clinic_photos import hooks
        from clinic_photos.access import entrypoints

        self.hooks = hooks
        self.entrypoints = entrypoints

    def test_override_and_response_hooks_are_registered(self):
        self.assertEqual(
            self.hooks.override_whitelisted_methods,
            {
                RAW_READER_PATH: WRAPPER_PATH,
                NATIVE_ZIP_PATH: ZIP_WRAPPER_PATH,
            },
        )
        self.assertEqual(
            self.hooks.after_request,
            ["clinic_photos.access.entrypoints.after_request"],
        )
        self.assertEqual(
            self.hooks.before_request,
            ["clinic_photos.access.entrypoints.mark_direct_private_request"],
        )
        self.assertEqual(self.hooks.doc_events, {
            "Patient Appointment": {
                "on_update": "clinic_photos.access.visit_observation.appointment_updated",
            },
        })

    def test_module_import_without_frappe_is_framework_free(self):
        previous = sys.modules.pop("frappe", None)
        try:
            reloaded = importlib.reload(self.entrypoints)
            self.assertTrue(callable(reloaded.convert_mt940_to_csv))
            self.assertFalse(
                hasattr(reloaded.convert_mt940_to_csv, "whitelisted")
            )
        finally:
            if previous is not None:
                sys.modules["frappe"] = previous
            importlib.reload(self.entrypoints)

    def test_site_import_applies_the_installed_post_only_whitelist_contract(self):
        calls = []

        def whitelist(**kwargs):
            calls.append(kwargs)

            def decorate(function):
                function.whitelisted = True
                function.methods = kwargs.get("methods")
                return function

            return decorate

        with fake_frappe(docs={}, user=AUTHOR) as fake:
            fake.whitelist = whitelist
            reloaded = importlib.reload(self.entrypoints)
            self.assertTrue(
                getattr(reloaded.convert_mt940_to_csv, "whitelisted", False)
            )
            self.assertTrue(getattr(reloaded.zip_files, "whitelisted", False))
            # Bank override keeps POST-only; ZIP override keeps native unrestricted
            # defaults and never adds allow_guest.
            self.assertEqual(
                calls, [{"methods": ["POST"]}, {}]
            )
        importlib.reload(self.entrypoints)


if __name__ == "__main__":
    unittest.main()
