"""Focused offline tests for metadata-only image publication.

The durable photo-save journal must stage the accepted image bytes into the bound
site's private staging area (``publication.staging``: exclusive no-follow create,
file and directory fsync, read-back verification) *before* it inserts the SUBMITTED
row, and the row itself must hold a metadata reference only: staged key, SHA-256,
size and format. No image bytes, no base64 and no path-like reference are persisted.

Proven here against a narrow in-memory Frappe stub plus a real per-test temporary
site tree:

* the persisted row carries the staged metadata and no image bytes at any point;
* the object is staged, verified and fsynced before the row insert is attempted;
* a staging refusal or an insert failure persists no row and no bytes;
* a same-tuple replay stays idempotent and stages exactly one object;
* a changed payload on the same tuple is a conflict that cannot replace the row;
* legacy image-bearing rows (``image_base64`` populated) and image-reference-less
  rows fail closed and are never automatically processed;
* a tampered, replaced or truncated staged object, or tampered staged metadata,
  fails closed;
* annotation submissions stage nothing and keep their existing behaviour.

Not proven here: installed Frappe/MariaDB behaviour, the real DocType controller
under a site, transaction/commit semantics, native permission decisions, power-loss
durability, retention/purge of staging residue, multi-host filesystems or any HTTP
route. A green run is not native acceptance, and no migration is executed here.
"""

import base64
import contextlib
import hashlib
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.publication import contracts, journal, staging

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
REQUEST_KEY = "request-key-0002"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-1"

# Known-good 1x1 PNG fixture from the contract tests.
PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)
PNG_1X1_BASE64_PADDED = base64.b64encode(PNG_1X1_BYTES).decode("ascii")

ENVELOPE = {"schema_version": 1, "coordinate_space": "normalized"}

STAGED_FIELDS = ("staged_key", "staged_sha256", "staged_size", "staged_format")


def annotation(**values):
    shape = {
        "id": "shape-1",
        "type": "rectangle",
        "color": "#1a2B3c",
        "x": 0.25,
        "y": 0.5,
        "width": 0.5,
        "height": 0.25,
        "stroke_width": 0.02,
    }
    shape.update(values)
    return {**ENVELOPE, "shapes": [shape]}


def annotation_payload(**overrides):
    payload = {
        "kind": "annotation_append",
        "request_key": "request-key-0001",
        "draft": "DRAFT-1",
        "expected_version": 0,
        "original": "PHOTO-1",
        "annotation": annotation(),
    }
    payload.update(overrides)
    return payload


def synthetic_payload(**overrides):
    payload = {
        "kind": "synthetic_original_create",
        "request_key": REQUEST_KEY,
        "synthetic_patient": SYNTHETIC_PATIENT,
        "image_base64": PNG_1X1_BASE64,
    }
    payload.update(overrides)
    return payload


class FakeOperationDocument(dict):
    """Only the document surface the journal's insert path calls."""

    def __init__(self, values, db):
        super().__init__(values)
        self.name = values.get("name") or ""
        self._db = db

    def insert(self, **_kwargs):
        self._db.events.append("insert")
        if self._db.insert_exception is not None:
            raise self._db.insert_exception("forced insert failure")
        if not self.name:
            self.name = uuid.uuid4().hex
        self["name"] = self.name
        self._db.add(
            journal.OPERATION_DOCTYPE,
            self.name,
            **{
                key: value
                for key, value in self.items()
                if key not in ("doctype", "name")
            },
        )
        return self


class FakeDB:
    def __init__(self):
        self.docs = {}
        self.events = []
        self.insert_exception = None

    def add(self, doctype, name, **values):
        self.docs.setdefault(doctype, {})[name] = dict(values, name=name)
        return self.docs[doctype][name]

    def set_value(self, doctype, name, **values):
        row = self.docs.setdefault(doctype, {}).setdefault(name, {"name": name})
        row.update(values)
        return row

    def exists(self, doctype, name):
        return name in self.docs.get(doctype, {})

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        as_dict=False,
        for_update=False,
        **_kwargs,
    ):
        rows = list(self.docs.get(doctype, {}).values())
        if isinstance(filters, str):
            rows = [row for row in rows if row.get("name") == filters]
        elif filters:
            rows = [
                row
                for row in rows
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if not rows:
            return None
        row = rows[0]
        fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
        if as_dict:
            return {field: row.get(field) for field in fields}
        values = [row.get(field) for field in fields]
        return values if isinstance(fieldname, (list, tuple)) else values[0]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, limit=None, **_kw):
        rows = list(self.docs.get(doctype, {}).values())
        if filters:
            rows = [
                row
                for row in rows
                if all(row.get(key) == value for key, value in filters.items())
            ]
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        if fields:
            return [{field: row.get(field) for field in fields} for row in rows]
        return [dict(row) for row in rows]

    def commit(self):
        raise AssertionError("journal must never commit")

    def rollback(self):
        raise AssertionError("journal must never roll back")


class FakeFrappe(types.ModuleType):
    def __init__(self, root):
        super().__init__("frappe")
        self.root = root
        self.db = FakeDB()
        self.session = types.SimpleNamespace(user=ACTOR)
        self.local = types.SimpleNamespace(site=SITE)
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})
        self.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        self.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        self.db.add("User", ACTOR, enabled=1)
        self.db.add(
            "Has Role",
            f"{ACTOR}:Physician",
            parent=ACTOR,
            parenttype="User",
            role="Physician",
        )

    def get_site_path(self, *parts):
        return os.path.join(self.root, SITE, *parts)

    def get_doc(self, values):
        return FakeOperationDocument(values, self.db)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)


@contextlib.contextmanager
def runtime(root):
    module = FakeFrappe(root)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


def operation_rows(fake):
    return fake.db.docs.get(journal.OPERATION_DOCTYPE, {})


def staging_directory(root):
    return os.path.join(root, SITE, "private", staging.STAGING_DIRECTORY)


def staged_objects(root):
    directory = staging_directory(root)
    if not os.path.isdir(directory):
        return []
    return sorted(os.listdir(directory))


def stored_row(fake, operation_id):
    return operation_rows(fake)[operation_id]


class MetadataOnlyPublicationTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = os.path.realpath(self._temp.name)
        os.makedirs(os.path.join(self.root, SITE, "private"), exist_ok=True)
        self._fake_context = runtime(self.root)
        self.fake = self._fake_context.__enter__()
        self.addCleanup(self._fake_context.__exit__, None, None, None)

    # -- helpers ---------------------------------------------------------
    def assert_no_image_bytes(self, row):
        """No stored value is, or contains, the accepted image or its base64."""
        self.assertNotIn("image_base64", row)
        for fieldname, value in row.items():
            if isinstance(value, (bytes, bytearray)):
                self.fail(f"{fieldname} stores raw image bytes")
            if not isinstance(value, str):
                continue
            with self.subTest(field=fieldname):
                self.assertNotIn(PNG_1X1_BASE64, value)
                self.assertNotIn(PNG_1X1_BASE64_PADDED, value)
                self.assertNotIn(PNG_1X1_BASE64.rstrip("="), value)

    def staged_object_path(self, row):
        return os.path.join(
            staging_directory(self.root), row["staged_key"]
        )

    # -- persistence shape ----------------------------------------------
    def test_staged_metadata_is_persisted_and_no_image_bytes_are_stored(self):
        request = contracts.validate_request(synthetic_payload())

        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        row = stored_row(self.fake, operation.id)

        self.assertEqual(row["staged_sha256"], hashlib.sha256(PNG_1X1_BYTES).hexdigest())
        self.assertEqual(row["staged_size"], len(PNG_1X1_BYTES))
        self.assertEqual(row["staged_format"], "PNG")
        self.assertRegex(row["staged_key"], r"^[0-9a-f]{32}\.png$")
        self.assert_no_image_bytes(row)

        stored = staged_objects(self.root)
        self.assertEqual(stored, [row["staged_key"]])
        self.assertNotIn("File", self.fake.db.docs)

    def test_staged_object_authenticates_the_exact_accepted_bytes(self):
        request = contracts.validate_request(synthetic_payload())
        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        row = stored_row(self.fake, operation.id)

        with open(self.staged_object_path(row), "rb") as handle:
            self.assertEqual(handle.read(), PNG_1X1_BYTES)

        loaded = journal.load_submission(operation.id)
        self.assertEqual(loaded.request.image_bytes, PNG_1X1_BYTES)
        self.assertEqual(loaded, operation)

    def test_annotation_submission_stages_nothing(self):
        request = contracts.validate_request(annotation_payload())

        with mock.patch.object(staging, "stage_image") as stage:
            operation = journal.stage_submission(request, actor=ACTOR, site=SITE)

        stage.assert_not_called()
        row = stored_row(self.fake, operation.id)
        for fieldname in STAGED_FIELDS:
            self.assertIn(row.get(fieldname), (None, ""))
        self.assertNotIn("image_base64", row)
        self.assertEqual(journal.load_submission(operation.id).request, request)
        self.assertEqual(staged_objects(self.root), [])

    # -- ordering and durability ----------------------------------------
    def test_object_is_staged_and_fsynced_before_the_row_insert(self):
        events = self.fake.db.events

        def record_file_sync(_descriptor):
            events.append("file_fsync")

        def record_directory_sync(_descriptor):
            events.append("dir_fsync")

        with mock.patch.object(staging, "_fsync_file", record_file_sync), \
                mock.patch.object(staging, "_fsync_directory", record_directory_sync):
            journal.stage_submission(
                contracts.validate_request(synthetic_payload()), actor=ACTOR, site=SITE
            )

        self.assertEqual(events[-1], "insert")
        self.assertIn("file_fsync", events[:-1])
        # The staging directory entry is synced after the object is written, before
        # any metadata is committed.
        self.assertEqual(events[-2], "dir_fsync")
        self.assertGreater(events.index("file_fsync"), events.index("dir_fsync"))

    # -- failures and interruption --------------------------------------
    def test_staging_refusal_persists_no_row_and_discloses_nothing(self):
        request = contracts.validate_request(synthetic_payload())

        with mock.patch.object(
            staging,
            "stage_image",
            side_effect=staging.StagingRefused(staging.SAFE_STAGING_REFUSED),
        ):
            with self.assertRaises(journal.JournalError) as caught:
                journal.stage_submission(request, actor=ACTOR, site=SITE)

        self.assertEqual(operation_rows(self.fake), {})
        self.assertEqual(staged_objects(self.root), [])
        self.assertNotIn(PNG_1X1_BASE64, str(caught.exception))
        self.assertFalse(hasattr(caught.exception, "operation_id"))

    def test_insert_failure_after_staging_persists_no_row(self):
        request = contracts.validate_request(synthetic_payload())
        self.fake.db.insert_exception = RuntimeError

        with self.assertRaises(RuntimeError):
            journal.stage_submission(request, actor=ACTOR, site=SITE)

        self.assertEqual(operation_rows(self.fake), {})
        # Staging residue is evidence and is never unlinked by this module.
        self.assertEqual(len(staged_objects(self.root)), 1)

    # -- idempotency and request identity -------------------------------
    def test_replay_is_idempotent_and_stages_exactly_one_object(self):
        request = contracts.validate_request(synthetic_payload())

        first = journal.stage_submission(request, actor=ACTOR, site=SITE)
        second = journal.stage_submission(request, actor=ACTOR, site=SITE)

        self.assertEqual(first.id, second.id)
        self.assertEqual(first, second)
        self.assertEqual(len(operation_rows(self.fake)), 1)
        self.assertEqual(len(staged_objects(self.root)), 1)

    def test_changed_image_on_the_same_tuple_is_a_conflict(self):
        first = journal.stage_submission(
            contracts.validate_request(synthetic_payload()), actor=ACTOR, site=SITE
        )

        other = base64.b64encode(PNG_1X1_BYTES + b"\x00").decode("ascii")
        with self.assertRaises(journal.JournalConflict) as caught:
            journal.stage_submission(
                contracts.validate_request(synthetic_payload(image_base64=other)),
                actor=ACTOR,
                site=SITE,
            )

        self.assertEqual(caught.exception.operation_id, first.id)
        self.assertEqual(len(operation_rows(self.fake)), 1)
        self.assertEqual(len(staged_objects(self.root)), 1)

    def test_request_identity_survives_a_fresh_process(self):
        request = contracts.validate_request(synthetic_payload())
        staged = journal.stage_submission(request, actor=ACTOR, site=SITE)
        digest = staged.payload_digest
        row = dict(stored_row(self.fake, staged.id))

        # A new process binds the same site tree and re-reads the persisted row.
        with runtime(self.root) as fake:
            fake.db.add(
                journal.OPERATION_DOCTYPE,
                row["name"],
                **{key: value for key, value in row.items() if key != "name"},
            )
            loaded = journal.load_submission(staged.id)

        self.assertEqual(loaded, staged)
        self.assertEqual(loaded.payload_digest, digest)
        self.assertEqual(loaded.request.image_bytes, PNG_1X1_BYTES)
        self.assertEqual(loaded.request.request_key, REQUEST_KEY)
    # -- legacy and unreferenced rows fail closed -----------------------
    def _seed_row(self, request, **overrides):
        row = {
            "site": SITE,
            "actor": ACTOR,
            "kind": request.kind,
            "request_key": request.request_key,
            "identity_digest": journal.identity_digest(
                SITE, ACTOR, request.kind, request.request_key
            ),
            "payload_digest": journal.payload_digest(request),
            "state": "SUBMITTED",
            "draft": request.draft,
            "expected_version": request.expected_version,
            "original": request.original,
            "synthetic_patient": request.synthetic_patient,
            "annotation_json": request.annotation,
            "reservation_key": None,
        }
        row.update(overrides)
        name = uuid.uuid4().hex
        self.fake.db.add(journal.OPERATION_DOCTYPE, name, **row)
        return name

    def test_legacy_image_bearing_row_fails_closed(self):
        request = contracts.validate_request(synthetic_payload())
        name = self._seed_row(request, image_base64=PNG_1X1_BASE64)

        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(name)
        with self.assertRaises(journal.JournalIntegrityError):
            journal.abort(journal.SubmittedOperation(
                id=name,
                site=SITE,
                actor=ACTOR,
                kind=request.kind,
                request_key=request.request_key,
                payload_digest=journal.payload_digest(request),
                request=request,
            ))
        # The stored legacy row is not rewritten, migrated or deleted.
        self.assertEqual(
            stored_row(self.fake, name)["image_base64"], PNG_1X1_BASE64
        )

    def test_legacy_row_cannot_be_resurrected_by_a_replay(self):
        request = contracts.validate_request(synthetic_payload())
        self._seed_row(request, image_base64=PNG_1X1_BASE64)

        with self.assertRaises(journal.JournalIntegrityError):
            journal.stage_submission(request, actor=ACTOR, site=SITE)

        self.assertEqual(len(operation_rows(self.fake)), 1)
        self.assertEqual(staged_objects(self.root), [])

    def test_image_reference_less_row_fails_closed(self):
        request = contracts.validate_request(synthetic_payload())
        name = self._seed_row(request)

        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(name)

    # -- integrity of the staged object and its metadata ----------------
    def test_tampered_staged_bytes_fail_closed(self):
        operation = journal.stage_submission(
            contracts.validate_request(synthetic_payload()), actor=ACTOR, site=SITE
        )
        row = stored_row(self.fake, operation.id)
        path = self.staged_object_path(row)

        mutations = {
            "appended": lambda data: data + b"\x00",
            "truncated": lambda data: data[:-1],
            "corrupted": lambda data: b"\x89PNG\r\n\x1a\n" + b"broken" * 4,
        }
        for label, mutation in mutations.items():
            with self.subTest(mutation=label):
                with open(path, "wb") as handle:
                    handle.write(mutation(PNG_1X1_BYTES))
                with self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(operation.id)

    def test_tampered_staged_metadata_fails_closed(self):
        request = contracts.validate_request(synthetic_payload())
        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        row = stored_row(self.fake, operation.id)

        for fieldname, value in (
            ("staged_sha256", "0" * 64),
            ("staged_size", len(PNG_1X1_BYTES) + 1),
            ("staged_format", "JPEG"),
            ("staged_key", f"{'0' * 32}.jpg"),
            ("staged_key", "../../etc/passwd"),
        ):
            with self.subTest(field=fieldname, value=value):
                original = row[fieldname]
                row[fieldname] = value
                with self.assertRaises(journal.JournalIntegrityError):
                    journal.load_submission(operation.id)
                row[fieldname] = original

    def test_missing_staged_object_fails_closed(self):
        operation = journal.stage_submission(
            contracts.validate_request(synthetic_payload()), actor=ACTOR, site=SITE
        )
        row = stored_row(self.fake, operation.id)
        os.unlink(self.staged_object_path(row))

        with self.assertRaises(journal.JournalIntegrityError):
            journal.load_submission(operation.id)


if __name__ == "__main__":
    unittest.main()
