"""Focused boundaries for PREPARED key reservation and native File rollback.

These tests exercise real own code end to end: the real durable journal, the real
exclusion/owner protocol with real OS advisory locks on a per-test sites volume, the
real runtime adapter, and the real File mixin over a narrow native stand-in. Only the
framework import boundary is faked: an in-memory ``frappe`` module that provides the
exact call shapes this app uses (``db.get_value`` with ``as_dict``/``for_update``,
``db.get_all``, ``db.set_value``, ``db.get_doc(...).insert()``, callbacks, and a
committed/transactional row split).

Not proven here: installed Frappe/MariaDB behaviour, the request transaction, the
coordinator's own commits, DocPerm, MariaDB unique constraints, installed effective
hook maps, module-level filesystem helpers outside the mixin, and any multi-host
guarantee. The fake never stands in for a protected writer, coordinator, launcher or
inspection surface; no File row or protected byte is created by these tests outside
the simulated transaction. The alias/veto cases build an already-committed PREPARED
reservation snapshot through the real derivation helpers instead of calling
``journal.prepare`` in the fixture, so they stay runnable against pre-change access
code.
"""

import base64
import contextlib
import copy
import hashlib
import os
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.access import frappe_context
from clinic_photos.access import lifecycle
from clinic_photos.access import storage
from clinic_photos.access.file_mixin import ProtectedFileMixin
from clinic_photos.publication import contracts, exclusion, journal

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-1"
REQUEST_KEY = "request-key-0001"
SYNTHETIC_REQUEST_KEY = "request-key-0002"
ANNOTATION_KEY = "/private/files/clinic-annotation-22222222-2222-2222-2222-222222222222.json"
NONRESERVED_KEY = "/private/files/legacy-annotation.json"
UNRELATED_KEY = "/private/files/scan.png"

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)
PNG_1X1_BYTES = base64.b64decode(PNG_1X1_BASE64)


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
    return {
        "schema_version": 1,
        "coordinate_space": "normalized",
        "shapes": [shape],
    }


def annotation_request():
    return contracts.validate_request(
        {
            "kind": "annotation_append",
            "request_key": REQUEST_KEY,
            "draft": DRAFT,
            "expected_version": 0,
            "original": PHOTO,
            "annotation": annotation(),
        }
    )


def synthetic_request():
    return contracts.validate_request(
        {
            "kind": "synthetic_original_create",
            "request_key": SYNTHETIC_REQUEST_KEY,
            "synthetic_patient": SYNTHETIC_PATIENT,
            "image_base64": PNG_1X1_BASE64,
        }
    )


# ---------------------------------------------------------------------------
# Narrow framework boundary: committed/transactional rows, no publication logic
# ---------------------------------------------------------------------------
class _FakeCallbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)

    def run(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def reset(self):
        self.callbacks = []


class _FakeOperationDocument(dict):
    """Only the document surface the staging repository calls."""

    def __init__(self, values, db):
        super().__init__(values)
        self.name = ""
        self._db = db

    def insert(self, **kwargs):
        self._db.insert_attempts += 1
        self.name = uuid.uuid4().hex
        self["name"] = self.name
        values = {
            key: value
            for key, value in self.items()
            if key not in ("doctype", "name")
        }
        self._db.add(journal.OPERATION_DOCTYPE, self.name, **values)
        return self


_UNSET = object()


class _FakeDB:
    """In-memory rows with an explicit committed/transactional split.

    ``commit`` promotes the transactional view; ``rollback`` discards it. Both refuse
    unless a test explicitly enables transaction control, so any repository helper
    that tries to commit or roll back is caught instead of silently succeeding.
    """

    def __init__(self, docs=None):
        self.committed = copy.deepcopy(docs or {})
        self.docs = copy.deepcopy(self.committed)
        self.before_commit = _FakeCallbacks()
        self.after_commit = _FakeCallbacks()
        self.before_rollback = _FakeCallbacks()
        self.after_rollback = _FakeCallbacks()
        self.allow_transaction_control = False
        self.commit_calls = 0
        self.rollback_calls = 0
        self.insert_attempts = 0
        self.set_value_calls = []
        self.get_value_calls = []
        self.held_locks = set()

    # -- writes ----------------------------------------------------------
    def add(self, doctype, name, **values):
        self.docs.setdefault(doctype, {})[name] = dict(values, name=name)
        return self.docs[doctype][name]

    def set_value(self, doctype, name, field=_UNSET, value=_UNSET):
        if field is _UNSET:
            raise AssertionError("set_value requires a field or a field mapping")
        row = self.docs.setdefault(doctype, {}).setdefault(name, {"name": name})
        if isinstance(field, dict):
            row.update(field)
            self.set_value_calls.append((doctype, name, dict(field)))
        else:
            row[field] = value
            self.set_value_calls.append((doctype, name, {field: value}))
        return row

    # -- reads -----------------------------------------------------------
    def _select(self, doctype, filters):
        rows = list(self.docs.get(doctype, {}).values())
        if not filters:
            return rows
        for fieldname, expected in filters.items():
            rows = [row for row in rows if row.get(fieldname) == expected]
        return rows

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        as_dict=False,
        for_update=False,
        **_kwargs,
    ):
        self.get_value_calls.append(
            {"doctype": doctype, "filters": filters, "for_update": bool(for_update)}
        )
        if isinstance(filters, str):
            row = self.docs.get(doctype, {}).get(filters)
            rows = [row] if row else []
        else:
            rows = self._select(doctype, filters)
        if not rows:
            return None
        row = rows[0]
        fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
        if as_dict:
            return {field: row.get(field) for field in fields}
        values = [row.get(field) for field in fields]
        return values if isinstance(fieldname, (list, tuple)) else values[0]

    def get_all(self, doctype, filters=None, fields=None, pluck=None, limit=None, **_kwargs):
        rows = self._select(doctype, filters)
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        if fields:
            return [{field: row.get(field) for field in fields} for row in rows]
        return [dict(row) for row in rows]

    def sql(self, query, values=(), **_kwargs):
        normalized = " ".join(str(query).split()).lower()
        if "get_lock" in normalized:
            name, _timeout = values
            self.held_locks.add(name)
            return [(1,)]
        if "release_lock" in normalized:
            name = values[0]
            if name not in self.held_locks:
                return [(None,)]
            self.held_locks.discard(name)
            return [(1,)]
        raise AssertionError(f"unexpected fake SQL: {query!r}")

    # -- transaction ------------------------------------------------------
    def commit(self):
        if not self.allow_transaction_control:
            raise AssertionError("repository helpers must never commit")
        self.commit_calls += 1
        self.before_rollback.reset()
        self.after_rollback.reset()
        self.before_commit.run()
        self.committed = copy.deepcopy(self.docs)
        self.after_commit.run()

    def rollback(self):
        if not self.allow_transaction_control:
            raise AssertionError("repository helpers must never roll back")
        self.rollback_calls += 1
        self.before_commit.reset()
        self.after_commit.reset()
        self.before_rollback.run()
        self.docs = copy.deepcopy(self.committed)
        self.after_rollback.run()

    def snapshot(self):
        return copy.deepcopy(self.committed)


class _FakeFrappe(types.ModuleType):
    def __init__(self, *, docs=None, user=ACTOR, site=SITE, sites_path=None):
        super().__init__("frappe")
        self.db = _FakeDB(docs)
        self.session = types.SimpleNamespace(user=user)
        self.local = types.SimpleNamespace(site=site, sites_path=sites_path)
        self.sites_path = sites_path
        self.site = site
        self.form_dict = {}
        self.permissions = {}
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})
        self.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        self.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})

    def get_site_path(self, *parts):
        return os.path.join(self.sites_path, self.site, *parts)

    def get_doc(self, values):
        return _FakeOperationDocument(values, self.db)

    def has_permission(self, doctype, permission_type="read", doc=None):
        name = getattr(doc, "name", doc)
        return bool(self.permissions.get((doctype, permission_type, name), False))

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)


@contextlib.contextmanager
def fake_runtime(*, docs=None, user=ACTOR, site=SITE, sites_path=None):
    module = _FakeFrappe(docs=docs, user=user, site=site, sites_path=sites_path)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


def _seed_actor(fake, *, user=ACTOR, roles=("Physician",), enabled=1):
    fake.db.committed.setdefault("User", {})[user] = {"name": user, "enabled": enabled}
    fake.db.docs.setdefault("User", {})[user] = {"name": user, "enabled": enabled}
    for index, role in enumerate(roles):
        name = f"HR-{user}-{index}"
        row = {"name": name, "parent": user, "parenttype": "User", "role": role}
        fake.db.committed.setdefault("Has Role", {})[name] = dict(row)
        fake.db.docs.setdefault("Has Role", {})[name] = dict(row)


def _sites_volume(root):
    """Real per-test sites volume with the fixed site tree the adapter resolves."""
    sites_path = os.path.realpath(root)
    for relative in ("private/files", "public/files"):
        os.makedirs(os.path.join(sites_path, SITE, relative), exist_ok=True)
    return sites_path


def _stage_submitted(fake, request):
    operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
    fake.db.allow_transaction_control = True
    fake.db.commit()
    fake.db.allow_transaction_control = False
    return operation


def _prepare(fake, operation):
    target = exclusion.target_identity_for_operation(operation.id)
    with exclusion.hold_exclusion(operation.id, target):
        return journal.prepare(operation)


def _operation_rows(fake):
    return fake.db.docs.get(journal.OPERATION_DOCTYPE, {})


def _committed_operation_rows(fake):
    return fake.db.committed.get(journal.OPERATION_DOCTYPE, {})


# ---------------------------------------------------------------------------
# PREPARED reservation
# ---------------------------------------------------------------------------
class PreparedReservationTests(unittest.TestCase):
    def test_prepared_reservation_survives_publication_rollback(self):
        request = annotation_request()
        payload_bytes = request.annotation.encode("utf-8")

        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                operation = _stage_submitted(fake, request)
                commits_before = fake.db.commit_calls

                prepared = _prepare(fake, operation)

                # The owner derives canonical identity and byte evidence; the helper
                # itself stages one durable row and never commits or rolls back.
                self.assertIsInstance(prepared, contracts.PreparedOperation)
                self.assertEqual(prepared.id, operation.id)
                self.assertEqual(prepared.site, SITE)
                self.assertEqual(prepared.actor, ACTOR)
                self.assertEqual(prepared.kind, "annotation_append")
                self.assertEqual(prepared.payload_digest, operation.payload_digest)
                self.assertEqual(prepared.draft, DRAFT)
                self.assertEqual(prepared.expected_version, 0)
                self.assertIsNone(prepared.synthetic_patient)
                self.assertEqual(prepared.target_identity, f"draft:{DRAFT}")
                self.assertEqual(prepared.state, "PREPARED")
                self.assertGreaterEqual(prepared.generation, 1)
                self.assertEqual(
                    prepared.key,
                    storage.canonical_private_key(prepared.key),
                )
                self.assertTrue(storage.is_reserved_key(prepared.key))
                self.assertEqual(
                    prepared.expected_sha256,
                    hashlib.sha256(payload_bytes).hexdigest(),
                )
                self.assertEqual(prepared.expected_size, len(payload_bytes))

                self.assertEqual(fake.db.commit_calls, commits_before)
                self.assertEqual(fake.db.rollback_calls, 0)

                # The key is protected before any File or domain row exists.
                self.assertNotIn("File", fake.db.docs)
                self.assertNotIn("Clinic Photo", fake.db.docs)
                self.assertNotIn("Clinic Photo Annotation Version", fake.db.docs)
                row = _operation_rows(fake)[operation.id]
                self.assertEqual(row["state"], "PREPARED")
                self.assertEqual(row["reservation_key"], prepared.key)
                self.assertEqual(row["generation"], prepared.generation)
                self.assertEqual(row["expected_sha256"], prepared.expected_sha256)
                self.assertEqual(row["expected_size"], prepared.expected_size)

                reservations = frappe_context.lookup_protection(prepared.key, "")
                self.assertTrue(reservations)
                self.assertTrue(
                    all(reference.kind == "reservation" for reference in reservations)
                )
                record = storage.classify_file({"file_url": prepared.key})
                self.assertIsNotNone(record)
                self.assertTrue(record.registered)
                self.assertFalse(record.inconsistent)

            # The PREPARED row is only durable once the coordinator commits its own
            # earlier transaction; the helper itself never did.
            self.assertEqual(
                _committed_operation_rows(fake)[operation.id]["state"], "SUBMITTED"
            )
            fake.db.allow_transaction_control = True
            fake.db.commit()
            fake.db.allow_transaction_control = False
            self.assertEqual(
                _committed_operation_rows(fake)[operation.id]["state"], "PREPARED"
            )

            # The later publication transaction is discarded; the reservation stays.
            with fake_runtime(docs=fake.db.snapshot(), sites_path=sites_path) as reader:
                committed = _committed_operation_rows(reader)[operation.id]
                self.assertEqual(committed["state"], "PREPARED")
                self.assertEqual(committed["reservation_key"], prepared.key)
                self.assertEqual(committed["generation"], prepared.generation)

                reader.db.allow_transaction_control = True
                reader.db.add("File", "FILE-PARTIAL", file_url=prepared.key)
                reader.db.add(
                    "Clinic Photo Annotation Version",
                    "VERSION-PARTIAL",
                    draft=DRAFT,
                    storage_key=prepared.key,
                    annotation_file="FILE-PARTIAL",
                    state="pending",
                )
                reader.db.rollback()
                reader.db.allow_transaction_control = False

                self.assertNotIn("File", reader.db.docs)
                self.assertNotIn("Clinic Photo Annotation Version", reader.db.docs)
                surviving = _operation_rows(reader)[operation.id]
                self.assertEqual(surviving["state"], "PREPARED")
                self.assertEqual(surviving["reservation_key"], prepared.key)

                references = frappe_context.lookup_protection(prepared.key, "")
                self.assertTrue(references)
                record = storage.classify_file({"file_url": prepared.key})
                self.assertTrue(record.registered)
                self.assertFalse(record.inconsistent)
                self.assertEqual(record.storage_key, prepared.key)
                # No authoritative domain reference exists yet: ordinary byte access
                # stays denied rather than resolving to a published record.
                self.assertEqual(record.references, ())
                self.assertFalse(
                    frappe_context.authorize_protected_read(record, {"file_url": prepared.key})
                )

    def test_submitted_operation_alone_does_not_reserve_a_key(self):
        request = annotation_request()

        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                operation = _stage_submitted(fake, request)
                before_prepare = fake.db.snapshot()

                prepared = _prepare(fake, operation)

            with fake_runtime(docs=before_prepare, sites_path=sites_path) as reader:
                row = _committed_operation_rows(reader)[operation.id]
                self.assertEqual(row["state"], "SUBMITTED")
                self.assertIn(row.get("reservation_key"), (None, ""))
                self.assertIn(row.get("generation"), (None, ""))
                self.assertIn(row.get("expected_sha256"), (None, ""))
                self.assertIn(row.get("expected_size"), (None, ""))
                self.assertNotIn("File", reader.db.docs)

                # The key a later PREPARED attempt derives is not reserved by a mere
                # SUBMITTED acceptance.
                self.assertEqual(frappe_context.lookup_protection(prepared.key, ""), ())
                record = storage.classify_file({"file_url": prepared.key})
                self.assertIsNotNone(record)
                self.assertFalse(record.registered)

    def test_prepare_requires_the_live_server_bound_owner(self):
        request = annotation_request()

        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                operation = _stage_submitted(fake, request)
                commits_before = fake.db.commit_calls
                rollbacks_before = fake.db.rollback_calls

                with self.assertRaises(exclusion.ExclusionDenied):
                    journal.prepare(operation)
                self.assertEqual(
                    _operation_rows(fake)[operation.id]["state"], "SUBMITTED"
                )
                self.assertIn(
                    _operation_rows(fake)[operation.id].get("reservation_key"),
                    (None, ""),
                )

                target = exclusion.target_identity_for_operation(operation.id)
                with exclusion.hold_exclusion(operation.id, target) as owner:
                    # A submission whose server-bound identity disagrees with the
                    # locked row is refused instead of being trusted.
                    forged = contracts.SubmittedOperation(
                        id=operation.id,
                        site=SITE,
                        actor="other@example.test",
                        kind=operation.kind,
                        request_key=operation.request_key,
                        payload_digest=operation.payload_digest,
                        request=operation.request,
                    )
                    with self.assertRaises(exclusion.ExclusionDenied):
                        journal.prepare(forged)
                    self.assertEqual(
                        _operation_rows(fake)[operation.id]["state"], "SUBMITTED"
                    )
                    self.assertTrue(owner.poisoned)

            self.assertEqual(fake.db.commit_calls, commits_before)
            self.assertEqual(fake.db.rollback_calls, rollbacks_before)

    def test_prepare_refuses_a_second_attempt_and_spends_the_token(self):
        request = synthetic_request()

        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                operation = _stage_submitted(fake, request)
                commits_before = fake.db.commit_calls

                target = exclusion.target_identity_for_operation(operation.id)
                with exclusion.hold_exclusion(operation.id, target) as owner:
                    prepared = journal.prepare(operation)
                    self.assertEqual(prepared.synthetic_patient, SYNTHETIC_PATIENT)
                    self.assertIsNone(prepared.draft)
                    self.assertIsNone(prepared.expected_version)
                    self.assertEqual(
                        prepared.target_identity,
                        f"patient-creation:{SYNTHETIC_PATIENT}",
                    )
                    self.assertEqual(prepared.expected_size, len(PNG_1X1_BYTES))
                    self.assertEqual(
                        prepared.expected_sha256,
                        hashlib.sha256(PNG_1X1_BYTES).hexdigest(),
                    )

                    with self.assertRaises(exclusion.ExclusionNoRetry):
                        journal.prepare(operation)
                    self.assertFalse(owner.poisoned)
                    with self.assertRaises(exclusion.ExclusionNoRetry):
                        journal.prepare(operation)

            self.assertEqual(fake.db.commit_calls, commits_before)
            self.assertEqual(fake.db.rollback_calls, 0)


# ---------------------------------------------------------------------------
# Native File alias / rollback boundary
# ---------------------------------------------------------------------------
class _FakeNativeFile:
    """Installed-shape File stand-in: before_insert retarget, validate, db_insert."""

    _sequence = 0

    def __init__(
        self,
        *,
        name=None,
        file_url=None,
        file_name="",
        is_private=1,
        content=b"",
        new=True,
        owner=ACTOR,
        dedup_target=None,
        after_insert_retarget=None,
    ):
        self.name = name
        self.file_url = file_url
        self.file_name = file_name
        self.is_private = is_private
        self.content = content
        self.content_hash = None
        self.file_size = 0
        self.owner = owner
        self._new = new
        self._dedup_target = dedup_target
        self._after_insert_retarget = after_insert_retarget
        self.flags = types.SimpleNamespace(in_insert=False)
        self.calls = []
        self.rollback_events = []
        self.db_inserts = []

    # -- native document lifecycle ---------------------------------------
    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return None

    def before_insert(self):
        self.calls.append("before_insert")
        if self._dedup_target:
            # Native save_file duplicate/retarget path: the new row is repointed at an
            # existing private file identity.
            self.file_url = self._dedup_target
            self.file_name = self._dedup_target.rsplit("/", 1)[-1]
            self.content_hash = "d" * 64
            self.file_size = len(self.content)

    def validate(self):
        self.calls.append("validate")

    def after_insert(self):
        self.calls.append("after_insert")

    def insert(self, ignore_permissions=False, **kwargs):
        self.calls.append("insert")
        self.before_insert()
        self.flags.in_insert = True
        try:
            self.validate()
        finally:
            self.flags.in_insert = False
        self._db_insert()
        if self._after_insert_retarget:
            # A later hook rewrites the destination after validation and row insert.
            self.file_url = self._after_insert_retarget
        self.after_insert()
        return self

    def _db_insert(self):
        if not self.name:
            type(self)._sequence += 1
            self.name = f"FILE-{type(self)._sequence}"
        self._new = False
        import frappe

        frappe.db.add(
            "File",
            self.name,
            file_url=self.file_url,
            file_name=self.file_name,
            is_private=self.is_private,
            owner=self.owner,
            content_hash=self.content_hash or "",
            file_size=self.file_size,
        )
        self.db_inserts.append(self.name)

    # -- native byte/privacy lifecycle -----------------------------------
    def get_content(self, encodings=None):
        self.calls.append("get_content")
        return self.content

    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        self.calls.append("save_file")
        return {"file_url": self.file_url}

    def get_full_path(self):
        import frappe

        for kind, prefix in (("private", "/private/files/"), ("public", "/files/")):
            if isinstance(self.file_url, str) and self.file_url.startswith(prefix):
                relative = self.file_url[len(prefix) :]
                return frappe.get_site_path(kind, "files", *relative.split("/"))
        return None

    def _delete_file_on_disk(self):
        self.calls.append("_delete_file_on_disk")
        path = self.get_full_path()
        self.rollback_events.append(("delete", path))
        if path and os.path.exists(path):
            os.unlink(path)

    def on_rollback(self):
        self.calls.append("on_rollback")
        if self.flags.__dict__.get("new_file"):
            self._delete_file_on_disk()
            return
        if self.flags.__dict__.get("original_content"):
            path = self.get_full_path()
            self.rollback_events.append(("rewrite", path))
            with open(path, "wb") as handle:
                handle.write(self.flags.original_content)
        original_path = self.flags.__dict__.get("original_path")
        if original_path:
            self.rollback_events.append(("move", original_path))
            os.replace(original_path["new"], original_path["old"])

    def delete_file_from_filesystem(self, only_thumbnail=False):
        self.calls.append("delete_file_from_filesystem")
        self._delete_file_on_disk()

    def delete_file_data_content(self, only_thumbnail=False):
        self.calls.append("delete_file_data_content")
        self._delete_file_on_disk()

    def on_trash(self):
        self.calls.append("on_trash")
        self._delete_file_on_disk()


class _FakeFile(ProtectedFileMixin, _FakeNativeFile):
    pass


def _write_bytes(sites_path, key, data):
    relative = key[len("/private/files/") :]
    path = os.path.join(sites_path, SITE, "private", "files", *relative.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _write_public(sites_path, name, data):
    path = os.path.join(sites_path, SITE, "public", "files", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)
    return path


class _FakeOwner:
    """Narrow transaction-owner stub with explicit failure signalling."""

    def __init__(self):
        self.aborted = False

    def publication_aborted(self):
        return self.aborted

    def abort_publication(self, reason):
        self.aborted = True


@contextlib.contextmanager
def _bound(
    storage_key=ANNOTATION_KEY,
    *,
    draft=DRAFT,
    operation="pending_insert",
    actor=ACTOR,
):
    owner = _FakeOwner()
    with frappe_context.owned_publication_transaction(owner):
        with frappe_context.bound_publication(
            actor=actor,
            draft=draft,
            operation=operation,
            storage_key=storage_key,
        ):
            yield owner


def _reservation_snapshot(sites_path, request=None):
    """An already-committed PREPARED reservation snapshot; never calls ``prepare``.

    The alias/veto cases must be runnable against pre-change access code, so this
    fixture derives the same server-owned evidence with the real derivation helpers
    and commits the row directly instead of exercising ``journal.prepare``.
    """
    request = request or annotation_request()
    with fake_runtime(sites_path=sites_path) as fake:
        _seed_actor(fake)
        operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
        key = journal._reservation_key(
            request,
            site=SITE,
            operation_id=operation.id,
            generation=journal.PREPARED_GENERATION,
            payload=operation.payload_digest,
        )
        expected_sha256, expected_size = journal._prepared_evidence(request)
        fake.db.set_value(
            journal.OPERATION_DOCTYPE,
            operation.id,
            {
                "state": "PREPARED",
                "reservation_key": key,
                "generation": journal.PREPARED_GENERATION,
                "expected_sha256": expected_sha256,
                "expected_size": expected_size,
            },
        )
        # The coordinator's earlier PREPARED transaction is already committed here.
        fake.db.allow_transaction_control = True
        fake.db.commit()
        fake.db.allow_transaction_control = False
        return operation.id, key, fake.db.snapshot()


class NativeAliasRollbackBoundaryTests(unittest.TestCase):
    def _reservation_fixture(self, sites_path):
        """Committed PREPARED reservation snapshot, built without calling ``prepare``."""
        return _reservation_snapshot(sites_path)

    def test_native_dedup_retarget_before_insert_refused(self):
        """Pre-`db_insert` retarget onto the PREPARED reservation is refused."""
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            _operation_id, reserved_key, committed = self._reservation_fixture(
                sites_path
            )

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                with _bound(storage_key="/private/files/clinic-annotation-other.json"):
                    dedup = _FakeFile(
                        file_url="/private/files/unrelated-upload.png",
                        file_name="upload.png",
                        content=b"retarget-me",
                        dedup_target=reserved_key,
                    )
                    with self.assertRaises(fake.PermissionError):
                        dedup.insert()
                self.assertEqual(dedup.db_inserts, [])
                self.assertNotIn("after_insert", dedup.calls)
                self.assertNotIn("File", fake.db.docs)

    def test_native_dedup_retarget_then_rollback_preserves_original(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            operation_id, reserved_key, committed = self._reservation_fixture(
                sites_path
            )

            # The same retarget after the complete native insert is a post-insert
            #    rejection: the row was staged and the caller must roll it back.
            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                with _bound(storage_key="/private/files/clinic-annotation-other.json"):
                    late = _FakeFile(
                        file_url="/private/files/unrelated-upload.png",
                        file_name="upload.png",
                        content=b"late-retarget",
                        after_insert_retarget=reserved_key,
                    )
                    with self.assertRaises(fake.PermissionError):
                        late.insert()
                self.assertEqual(late.db_inserts, [late.name])
                self.assertIn(late.name, fake.db.docs["File"])

                fake.db.after_rollback.add(
                    lambda: late.rollback_events.append(("sentinel", None))
                )
                fake.db.after_rollback.add(late.on_rollback)
                fake.db.allow_transaction_control = True
                fake.db.rollback()
                fake.db.allow_transaction_control = False

                self.assertNotIn("File", fake.db.docs)
                self.assertIn(("sentinel", None), late.rollback_events)
                self.assertFalse(
                    [event for event in late.rollback_events if event[0] == "delete"]
                )
                self.assertEqual(
                    _operation_rows(fake)[operation_id]["state"], "PREPARED"
                )

    def test_native_rollback_preserves_protected_residue(self):
        """Protected `new_file`/`original_path` compensation is skipped, not applied."""
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            _operation_id, reserved_key, committed = self._reservation_fixture(
                sites_path
            )

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                protected_path = _write_bytes(sites_path, reserved_key, b"residue")
                file_doc = _FakeFile(
                    name="FILE-NEW",
                    file_url=reserved_key,
                    file_name=reserved_key.rsplit("/", 1)[-1],
                    is_private=1,
                )
                file_doc.flags.new_file = True
                file_doc.flags.original_content = b"original-bytes"

                sentinel = []
                fake.db.after_rollback.add(file_doc.on_rollback)
                fake.db.after_rollback.add(lambda: sentinel.append("later"))
                fake.db.allow_transaction_control = True
                fake.db.rollback()
                fake.db.allow_transaction_control = False

                # The guarded callback is registered *before* the sentinel, so this
                # proves the remaining callbacks still ran rather than that an earlier
                # callback already ran.
                self.assertEqual(sentinel, ["later"])
                self.assertTrue(os.path.exists(protected_path))
                with open(protected_path, "rb") as handle:
                    self.assertEqual(handle.read(), b"residue")
                self.assertFalse(file_doc.rollback_events)

                # 4. `original_path` endpoints: neither a protected source nor a
                #    protected target may be moved back, for any user.
                old_path = _write_bytes(sites_path, "/private/files/clinic-photo-old.png", b"old")
                new_path = _write_bytes(sites_path, reserved_key, b"new")
                mover = _FakeFile(
                    name="FILE-MOVE",
                    file_url=reserved_key,
                    file_name=reserved_key.rsplit("/", 1)[-1],
                )
                mover.flags.original_path = {"old": old_path, "new": new_path}
                mover.on_rollback()
                self.assertTrue(os.path.exists(old_path))
                self.assertTrue(os.path.exists(new_path))
                self.assertFalse(mover.rollback_events)

                reverse_old = _write_bytes(sites_path, reserved_key, b"old")
                reverse_new = _write_bytes(sites_path, "/private/files/clinic-photo-new.png", b"new")
                reverse = _FakeFile(
                    name="FILE-MOVE-2",
                    file_url="/private/files/clinic-photo-new.png",
                    file_name="clinic-photo-new.png",
                )
                reverse.flags.original_path = {
                    "old": reverse_old,
                    "new": reverse_new,
                }
                reverse.on_rollback()
                self.assertTrue(os.path.exists(reverse_old))
                self.assertTrue(os.path.exists(reverse_new))
                self.assertFalse(reverse.rollback_events)

                # An unsaved, never-persisted File in the reserved namespace is not
                # deletable through native compensation either.
                unsaved = _FakeFile(
                    file_url=reserved_key,
                    file_name=reserved_key.rsplit("/", 1)[-1],
                )
                unsaved.flags.new_file = True
                unsaved.on_rollback()
                self.assertTrue(os.path.exists(protected_path))
                self.assertFalse(unsaved.rollback_events)

    def test_native_registered_alias_refused(self):
        """A registered non-reserved protected destination cannot be aliased."""
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)

            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                fake.db.add(
                    "Clinic Photo Annotation Version",
                    "VERSION-1",
                    draft=DRAFT,
                    storage_key=NONRESERVED_KEY,
                    annotation_file="FILE-LEGACY",
                    state="pending",
                )
                fake.db.add("File", "FILE-LEGACY", file_url=NONRESERVED_KEY)
                with _bound(storage_key="/private/files/clinic-annotation-other.json"):
                    alias = _FakeFile(
                        file_url=NONRESERVED_KEY,
                        file_name="legacy-annotation.json",
                        content=b"alias",
                    )
                    with self.assertRaises(fake.PermissionError):
                        alias.insert()
                self.assertEqual(alias.db_inserts, [])

    def test_proved_unrelated_file_keeps_native_insert_and_rollback(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)

            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                unrelated = _FakeFile(
                    file_url=UNRELATED_KEY,
                    file_name="scan.png",
                    content=b"unrelated",
                )
                unrelated.insert()
                self.assertEqual(unrelated.calls[:3], ["insert", "before_insert", "validate"])
                self.assertIn("after_insert", unrelated.calls)
                self.assertEqual(unrelated.db_inserts, [unrelated.name])

                path = _write_public(sites_path, "public.png", b"public-bytes")
                rollback_doc = _FakeFile(
                    file_url="/files/public.png",
                    file_name="public.png",
                    new=False,
                )
                rollback_doc.flags.original_content = b"restored"
                rollback_doc.on_rollback()
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), b"restored")
                self.assertEqual(rollback_doc.rollback_events[0][0], "rewrite")

            # An unclassifiable private-looking destination denies an ordinary insert
            # and skips destructive compensation without latching native rollback.
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                unknown = _FakeFile(
                    file_url="/private/files/../escape.png",
                    file_name="escape.png",
                    content=b"unknown",
                )
                with self.assertRaises(fake.PermissionError):
                    unknown.insert()
                self.assertEqual(unknown.db_inserts, [])

                sentinel = []
                fake.db.after_rollback.add(unknown.on_rollback)
                fake.db.after_rollback.add(lambda: sentinel.append("later"))
                fake.db.allow_transaction_control = True
                fake.db.rollback()
                fake.db.allow_transaction_control = False
                # Registered after the guarded callback: the remaining queue still ran.
                self.assertEqual(sentinel, ["later"])
                self.assertFalse(unknown.rollback_events)

    # -- positive bound protected metadata insert ------------------------
    def test_protected_insert_verifies_persisted_identity(self):
        """A correctly bound protected insert is not false-denied.

        This is a metadata-only positive control (one persisted holder with matching
        identity). It is not a first-byte write, receipt or publication proof; the
        exact bytes/receipt belong to Task 7.
        """
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            operation_id, reserved_key, committed = _reservation_snapshot(sites_path)

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                # The drafting clinician is required by the bound insert boundary.
                fake.db.add("Clinic Photo Draft", DRAFT, author=ACTOR)
                with _bound(storage_key=reserved_key):
                    file_doc = _FakeFile(
                        file_url=reserved_key,
                        file_name=reserved_key.rsplit("/", 1)[-1],
                        content=b"metadata-only",
                    )
                    file_doc.insert()

                self.assertEqual(
                    file_doc.calls,
                    ["insert", "before_insert", "validate", "after_insert"],
                )
                self.assertEqual(file_doc.db_inserts, [file_doc.name])
                rows = fake.db.docs["File"]
                self.assertEqual(list(rows), [file_doc.name])
                row = rows[file_doc.name]
                self.assertEqual(storage.canonical_private_key(row["file_url"]), reserved_key)
                self.assertEqual(row["file_name"], reserved_key.rsplit("/", 1)[-1])
                self.assertEqual(row["is_private"], 1)
                self.assertEqual(row["owner"], ACTOR)
                self.assertEqual(
                    _operation_rows(fake)[operation_id]["state"], "PREPARED"
                )

    # -- unknown destination from an out-of-root `original_path` --------
    def test_original_path_outside_trusted_roots_is_unknown(self):
        """`DESTINATION_UNKNOWN` is reachable and skips compensation, retaining poison.

        Every private-looking in-memory or persisted URL already classifies as
        protected, so an `original_path` endpoint outside both trusted site roots is the
        only way to reach the unknown branch.
        """
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            outside = os.path.join(os.path.realpath(root), "outside")
            os.makedirs(outside, exist_ok=True)
            outside_old = os.path.join(outside, "old.png")
            outside_new = os.path.join(outside, "new.png")
            for path, data in ((outside_old, b"old"), (outside_new, b"new")):
                with open(path, "wb") as handle:
                    handle.write(data)

            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                file_doc = _FakeFile(
                    name="FILE-UNKNOWN",
                    file_url=UNRELATED_KEY,
                    file_name="scan.png",
                )
                # Control: without the out-of-root endpoints this destination is
                # provably unrelated, so the classification below is caused by them.
                self.assertEqual(
                    frappe_context.destination_state(file_doc),
                    frappe_context.DESTINATION_UNRELATED,
                )

                file_doc.flags.original_path = {
                    "old": outside_old,
                    "new": outside_new,
                }
                self.assertEqual(
                    frappe_context.destination_state(
                        file_doc, (outside_old, outside_new)
                    ),
                    frappe_context.DESTINATION_UNKNOWN,
                )

                file_doc.on_rollback()

                with open(outside_old, "rb") as handle:
                    self.assertEqual(handle.read(), b"old")
                with open(outside_new, "rb") as handle:
                    self.assertEqual(handle.read(), b"new")
                self.assertFalse(file_doc.rollback_events)
                self.assertTrue(frappe_context.destination_poisoned(file_doc))

    # -- tampered PREPARED evidence and planted derived-key bytes --------
    def test_prepared_evidence_tamper_and_byte_conflict_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            operation_id, _reserved_key, committed = _reservation_snapshot(sites_path)

            with fake_runtime(docs=committed, sites_path=sites_path) as reader:
                row = dict(_committed_operation_rows(reader)[operation_id])
                # Control: the untouched row verifies.
                self.assertIsInstance(
                    journal._prepared_operation(row), contracts.PreparedOperation
                )
                for fieldname, value in (
                    ("expected_sha256", "0" * 64),
                    ("expected_size", 1),
                    (
                        "reservation_key",
                        "/private/files/clinic-annotation-ffffffff-ffff-ffff-ffff-ffffffffffff.json",
                    ),
                    ("generation", journal.PREPARED_GENERATION + 1),
                ):
                    with self.subTest(field=fieldname):
                        tampered = dict(row)
                        tampered[fieldname] = value
                        with self.assertRaises(journal.JournalIntegrityError):
                            journal._prepared_operation(tampered)
                self.assertEqual(
                    _committed_operation_rows(reader)[operation_id], row
                )

            # A planted byte identity at the derived key refuses preparation and leaves
            # the operation SUBMITTED, unpersisted.
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                request = annotation_request()
                operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
                derived = journal._reservation_key(
                    request,
                    site=SITE,
                    operation_id=operation.id,
                    generation=journal.PREPARED_GENERATION,
                    payload=operation.payload_digest,
                )
                _write_bytes(sites_path, derived, b"planted-residue")
                with self.assertRaises(journal.JournalIntegrityError):
                    _prepare(fake, operation)

                row = _operation_rows(fake)[operation.id]
                self.assertEqual(row["state"], "SUBMITTED")
                self.assertIn(row.get("reservation_key"), (None, ""))
                self.assertIn(row.get("generation"), (None, 0, ""))
                self.assertEqual(fake.db.set_value_calls, [])

    # -- live-owner cleanup refuses committed records/bytes --------------
    def test_protected_cleanup_refuses_committed_records_under_live_owner(self):
        """The changed live-owner cleanup path, not the generic no-owner gate."""
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            _operation_id, reserved_key, committed = _reservation_snapshot(sites_path)
            file_name = reserved_key.rsplit("/", 1)[-1]

            # One execution per attempted effect: a protected denial latches the abort
            # for the whole execution, so a second attempt in the same fake would be
            # denied by the latch rather than by the cleanup guard under test. The
            # protected bytes are written fresh per case so one refusal cannot be
            # masked by a previous case's deletion.
            for user in (ACTOR, "NURSING@example.test"):
                for method in (
                    "on_trash",
                    "delete_file_from_filesystem",
                    "delete_file_data_content",
                    "_delete_file_on_disk",
                ):
                    with self.subTest(user=user, method=method):
                        target = _write_bytes(sites_path, reserved_key, b"protected")
                        with fake_runtime(
                            docs=committed, sites_path=sites_path, user=user
                        ) as fake:
                            _seed_actor(fake, user=user)
                            fake.db.add(
                                "Clinic Photo Annotation Version",
                                "VERSION-COMMITTED",
                                draft=DRAFT,
                                storage_key=reserved_key,
                                annotation_file="FILE-PROTECTED",
                                state="committed",
                            )
                            fake.db.add(
                                "File",
                                "FILE-PROTECTED",
                                file_url=reserved_key,
                                file_name=file_name,
                                is_private=1,
                                owner=ACTOR,
                                content_hash="h" * 64,
                                file_size=9,
                            )
                            file_doc = _FakeFile(
                                name="FILE-PROTECTED",
                                file_url=reserved_key,
                                file_name=file_name,
                                new=False,
                            )
                            with _bound(
                                storage_key=reserved_key,
                                operation="pending_cleanup",
                                actor=user,
                            ) as owner:
                                with self.assertRaises(fake.PermissionError):
                                    getattr(file_doc, method)()
                                self.assertTrue(owner.aborted)
                            self.assertEqual(file_doc.calls, [])
                        self.assertTrue(os.path.exists(target))
                        with open(target, "rb") as handle:
                            self.assertEqual(handle.read(), b"protected")

            # Administrator cannot even bind a publication context: an independent,
            # earlier barrier than the cleanup guard.
            target = _write_bytes(sites_path, reserved_key, b"protected")
            with fake_runtime(
                docs=committed, sites_path=sites_path, user="Administrator"
            ) as fake:
                _seed_actor(fake, user="Administrator", roles=())
                with self.assertRaises(lifecycle.PublicationContextError):
                    with _bound(
                        storage_key=reserved_key,
                        operation="pending_cleanup",
                        actor="Administrator",
                    ):
                        self.fail("an Administrator publication context must never bind")
                self.assertTrue(os.path.exists(target))

    # -- explicit no-owner gate case (kept separately) -------------------
    def test_protected_cleanup_without_live_owner_denied_by_gate(self):
        """Separate case: the generic owner gate denies before any lifecycle guard."""
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            _operation_id, reserved_key, committed = _reservation_snapshot(sites_path)
            target = _write_bytes(sites_path, reserved_key, b"protected")

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                fake.db.add(
                    "Clinic Photo Annotation Version",
                    "VERSION-COMMITTED",
                    draft=DRAFT,
                    storage_key=reserved_key,
                    annotation_file="FILE-PROTECTED",
                    state="committed",
                )
                fake.db.add(
                    "File",
                    "FILE-PROTECTED",
                    file_url=reserved_key,
                    file_name=reserved_key.rsplit("/", 1)[-1],
                    is_private=1,
                    owner=ACTOR,
                    content_hash="h" * 64,
                    file_size=9,
                )
                file_doc = _FakeFile(
                    name="FILE-PROTECTED",
                    file_url=reserved_key,
                    file_name=reserved_key.rsplit("/", 1)[-1],
                    new=False,
                )
                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(fake.PermissionError):
                    file_doc.on_trash()
                self.assertTrue(os.path.exists(target))
                self.assertEqual(file_doc.calls, [])

    # -- post-insert verification branches ------------------------------
    def test_post_insert_identity_branches_refuse(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            _operation_id, reserved_key, committed = _reservation_snapshot(sites_path)
            file_name = reserved_key.rsplit("/", 1)[-1]

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                fake.db.add("Clinic Photo Draft", DRAFT, author=ACTOR)

                # (a) The row vanished or was never inserted after the native sequence.
                missing = _FakeFile(
                    name="FILE-ABSENT",
                    file_url=reserved_key,
                    file_name=file_name,
                )
                with _bound(storage_key=reserved_key):
                    with self.assertRaises(fake.PermissionError):
                        frappe_context.require_file_persisted_identity(missing)
                self.assertNotIn("File", fake.db.docs)

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                # (b) Persisted row resolves to a different protected key.
                fake.db.add(
                    "Clinic Photo Annotation Version",
                    "VERSION-OTHER",
                    draft=DRAFT,
                    storage_key=NONRESERVED_KEY,
                    annotation_file="",
                    state="pending",
                )
                fake.db.add(
                    "File",
                    "FILE-OTHER",
                    file_url=NONRESERVED_KEY,
                    file_name="legacy-annotation.json",
                    is_private=1,
                    owner=ACTOR,
                    content_hash="d" * 64,
                    file_size=5,
                )
                mismatched = _FakeFile(
                    name="FILE-OTHER",
                    file_url=reserved_key,
                    file_name=file_name,
                )
                with _bound(storage_key=reserved_key):
                    with self.assertRaises(fake.PermissionError):
                        frappe_context.require_file_persisted_identity(mismatched)

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                # (c) Persisted row disagrees on a non-URL identity field.
                fake.db.add(
                    "File",
                    "FILE-PRIVACY",
                    file_url=reserved_key,
                    file_name=file_name,
                    is_private=0,
                    owner=ACTOR,
                    content_hash="d" * 64,
                    file_size=5,
                )
                privacy = _FakeFile(
                    name="FILE-PRIVACY",
                    file_url=reserved_key,
                    file_name=file_name,
                    is_private=1,
                )
                with _bound(storage_key=reserved_key):
                    with self.assertRaises(fake.PermissionError):
                        frappe_context.require_file_persisted_identity(privacy)

            with fake_runtime(docs=committed, sites_path=sites_path) as fake:
                _seed_actor(fake)
                # (d) A duplicate holder of the same protected identity exists.
                fake.db.add(
                    "File",
                    "FILE-HOLDER",
                    file_url=reserved_key,
                    file_name=file_name,
                    is_private=1,
                    owner=ACTOR,
                    content_hash="d" * 64,
                    file_size=5,
                )
                fake.db.add(
                    "File",
                    "FILE-ALIAS",
                    file_url=reserved_key,
                    file_name=file_name,
                    is_private=1,
                    owner=ACTOR,
                    content_hash="d" * 64,
                    file_size=5,
                )
                duplicate = _FakeFile(
                    name="FILE-HOLDER",
                    file_url=reserved_key,
                    file_name=file_name,
                    is_private=1,
                )
                with _bound(storage_key=reserved_key):
                    with self.assertRaises(fake.PermissionError):
                        frappe_context.require_file_persisted_identity(duplicate)
                # The refusal is a verification only: no holder was removed.
                self.assertIn("FILE-ALIAS", fake.db.docs["File"])
                self.assertIn("FILE-HOLDER", fake.db.docs["File"])

    # -- no public raw evidence staging surface / stale authority --------
    def test_no_owner_stale_or_refused_authority_cannot_prepare(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = _sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                _seed_actor(fake)
                operation = _stage_submitted(fake, annotation_request())
                target = exclusion.target_identity_for_operation(operation.id)

                # No public raw key/generation/hash/size staging seam remains.
                self.assertFalse(
                    hasattr(frappe_context, "mark_save_operation_prepared")
                )

                # (1) No registered owner at all.
                with self.assertRaises(exclusion.ExclusionDenied):
                    journal.prepare(operation)

                # (2) Authority inherited out of a finished scope is stale, not reusable.
                with exclusion.hold_exclusion(operation.id, target):
                    pass
                with self.assertRaises(exclusion.ExclusionDenied):
                    journal.prepare(operation)

                # (3) A refused (already advanced) attempt cannot persist again.
                with exclusion.hold_exclusion(operation.id, target) as owner:
                    prepared = journal.prepare(operation)
                    with self.assertRaises(exclusion.ExclusionNoRetry):
                        journal.prepare(operation)
                    self.assertFalse(owner.poisoned)

                writes = [
                    call
                    for call in fake.db.set_value_calls
                    if call[0] == journal.OPERATION_DOCTYPE
                ]
                self.assertEqual(len(writes), 1)
                row = _operation_rows(fake)[operation.id]
                self.assertEqual(row["state"], "PREPARED")
                self.assertEqual(row["reservation_key"], prepared.key)
                self.assertEqual(fake.db.commit_calls, 1)  # only the staging fixture


if __name__ == "__main__":
    unittest.main()
