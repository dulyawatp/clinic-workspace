"""Focused unit tests for the exact-object first writer and receipt bridge.

These tests exercise the real own code end to end: the real durable journal, the real
exclusion/owner protocol over real OS advisory locks on a per-test sites volume, the
real runtime adapter, the real ``ProtectedFileMixin`` over a narrow native File
stand-in, and ``publication.writer``. Only the framework import boundary is faked: an
in-memory ``frappe`` module that provides the exact call shapes this app uses.

The stand-in follows the pinned Frappe 16.53 order that this task was told to use
(``document-insert-save-1.txt``/``file-lifecycle-1..4.txt``):

* ``Document.insert``: ``check_permission("create")`` → ``before_insert`` →
  ``set_new_name``/``validate``/``_validate`` → ``db_insert`` → ``after_insert``;
* ``File.before_insert``: folder/privacy/name/attachment/extension/private-access
  validations → ``save_file`` → ``validate_duplicate_entry``;
* ``File.save_file``: content type → ``check_max_file_size`` → native MD5 →
  duplicate lookup → suffix naming → ``before_write_file`` → ``write_file`` hook →
  ``save_file_on_filesystem``.

Not proven here: installed Frappe/MariaDB behaviour, the real MRO, native EXIF/hook
resolution against a live site, filesystem behaviour under power loss, the coordinator
transaction, the disposable-site/synthetic-mode gate (Task 8), and any multi-host
guarantee. A green local run is *not* native insertion acceptance.

Fixture bytes are created only through the writer inside an owned scratch sites volume;
no existing protected original is seeded, mutated or deleted.
"""

import base64
import contextlib
import copy
import datetime
import hashlib
import os
import stat
import sys
import tempfile
import types
import unittest
import uuid
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.access import frappe_context, lifecycle, storage
from clinic_photos.access.file_mixin import ProtectedFileMixin
from clinic_photos.publication import contracts, exclusion, journal, writer

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
SYNTHETIC_PATIENT = "SYNTHETIC-PATIENT-1"
DRAFT = "DRAFT-1"
REQUEST_KEY = "request-key-0001"
SYNTHETIC_REQUEST_KEY = "request-key-0002"

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
EXPECTED_SHA256 = hashlib.sha256(JPEG_BYTES).hexdigest()
EXPECTED_SIZE = len(JPEG_BYTES)


# ---------------------------------------------------------------------------
# Narrow framework boundary: committed/transactional rows, no publication logic
# ---------------------------------------------------------------------------
class _FakeCallbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)

    @property
    def _functions(self):
        return self.callbacks

    def run(self):
        while self.callbacks:
            self.callbacks.pop(0)()

    def reset(self):
        self.callbacks = []


class _FakeOperationDocument(dict):
    def __init__(self, values, db):
        super().__init__(values)
        self.name = ""
        self._db = db

    def insert(self, **_kwargs):
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
    """In-memory rows with an explicit committed/transactional split."""

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
        self.held_locks = set()
        self.get_all_calls = []

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
        else:
            row[field] = value
        return row

    # -- reads -----------------------------------------------------------
    def _select(self, doctype, filters):
        rows = list(self.docs.get(doctype, {}).values())
        if not filters:
            return rows
        for fieldname, expected in filters.items():
            if isinstance(expected, tuple) and len(expected) == 2:
                operator, operand = expected
                if operator == "!=":
                    rows = [row for row in rows if row.get(fieldname) != operand]
                    continue
                raise AssertionError(f"unsupported fake filter operator: {operator!r}")
            rows = [row for row in rows if row.get(fieldname) == expected]
        return rows

    def exists(self, doctype, name=None, filters=None, **_kwargs):
        if name is None:
            name = filters
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
        if isinstance(filters, str):
            row = self.docs.get(doctype, {}).get(filters)
            rows = [row] if row else []
        else:
            rows = self._select(doctype, filters)
        if not rows:
            return None
        row = rows[0]
        if fieldname == "*" and as_dict:
            return dict(row)
        fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
        if as_dict:
            return {field: row.get(field) for field in fields}
        values = [row.get(field) for field in fields]
        return values if isinstance(fieldname, (list, tuple)) else values[0]

    def get_all(
        self,
        doctype,
        filters=None,
        fields=None,
        pluck=None,
        limit=None,
        limit_start=0,
        limit_page_length=None,
        order_by=None,
        **_kwargs,
    ):
        self.get_all_calls.append(
            {
                "doctype": doctype,
                "limit": limit,
                "limit_start": limit_start,
                "limit_page_length": limit_page_length,
                "order_by": order_by,
            }
        )
        rows = self._select(doctype, filters)
        if order_by:
            fieldname = str(order_by).split()[0]
            reverse = "desc" in str(order_by).lower()
            rows.sort(key=lambda row: str(row.get(fieldname) or ""), reverse=reverse)
        if limit_start:
            rows = rows[limit_start:]
        if limit_page_length is not None:
            rows = rows[:limit_page_length]
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
        self.local.db = self.db
        self.sites_path = sites_path
        self.site = site
        self.form_dict = {}
        self.permissions = {}
        self.hook_methods = {}
        self.hook_resolution_error = None
        utils = types.ModuleType("frappe.utils")
        utils.get_hook_method = self._hook_method
        self.utils = utils
        self.request = None
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})
        self.MaxFileSizeReachedError = type("MaxFileSizeReachedError", (Exception,), {})
        self.FileTypeNotAllowed = type("FileTypeNotAllowed", (Exception,), {})
        self.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        self.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})

    def get_site_path(self, *parts):
        return os.path.join(self.sites_path, self.site, *parts)

    def get_doc(self, values):
        return _FakeOperationDocument(values, self.db)

    def has_permission(self, doctype, permission_type="read", doc=None):
        name = getattr(doc, "name", doc)
        return bool(self.permissions.get((doctype, permission_type, name), False))

    def call_hook_method(self, hook, **_kwargs):
        raise AssertionError(f"native write hook must never be invoked: {hook}")

    def _hook_method(self, hook, *_args, **_kwargs):
        # Pinned 16.53 declares the helper at ``frappe.utils.get_hook_method`` only.
        # This fake exposes exactly that namespace and no package-level alias, so a
        # wrong namespace cannot resolve here.
        if self.hook_resolution_error is not None:
            raise self.hook_resolution_error
        return self.hook_methods.get(hook)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    def whitelist(self, *_args, **_kwargs):
        def decorate(function):
            return function

        return decorate


@contextlib.contextmanager
def fake_runtime(*, docs=None, user=ACTOR, site=SITE, sites_path=None):
    module = _FakeFrappe(docs=docs, user=user, site=site, sites_path=sites_path)
    previous = sys.modules.get("frappe")
    previous_utils = sys.modules.get("frappe.utils")
    sys.modules["frappe"] = module
    sys.modules["frappe.utils"] = module.utils
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous
        if previous_utils is None:
            sys.modules.pop("frappe.utils", None)
        else:
            sys.modules["frappe.utils"] = previous_utils


def seed_actor(fake, *, user=ACTOR, roles=("Physician",), enabled=1):
    fake.db.committed.setdefault("User", {})[user] = {"name": user, "enabled": enabled}
    fake.db.docs.setdefault("User", {})[user] = {"name": user, "enabled": enabled}
    for index, role in enumerate(roles):
        name = f"HR-{user}-{index}"
        row = {"name": name, "parent": user, "parenttype": "User", "role": role}
        fake.db.committed.setdefault("Has Role", {})[name] = dict(row)
        fake.db.docs.setdefault("Has Role", {})[name] = dict(row)


def seed_patient(fake, patient=SYNTHETIC_PATIENT):
    fake.db.committed.setdefault("Patient", {})[patient] = {"name": patient}
    fake.db.docs.setdefault("Patient", {})[patient] = {"name": patient}


def sites_volume(root):
    """Real per-test sites volume with the fixed site tree the adapter resolves."""
    sites_path = os.path.realpath(root)
    for relative in ("private/files", "public/files"):
        os.makedirs(os.path.join(sites_path, SITE, relative), exist_ok=True)
    return sites_path


def private_root(sites_path):
    return os.path.join(sites_path, SITE, "private", "files")


def target_path(sites_path, key):
    return os.path.join(private_root(sites_path), key[len("/private/files/") :])


def synthetic_request():
    return contracts.validate_request(
        {
            "kind": "synthetic_original_create",
            "request_key": SYNTHETIC_REQUEST_KEY,
            "synthetic_patient": SYNTHETIC_PATIENT,
            "image_base64": JPEG_2X2_BASE64,
        }
    )


ANNOTATION_ENVELOPE = {"schema_version": 1, "coordinate_space": "normalized"}
ANNOTATION_SHAPE = {
    "id": "shape-1",
    "type": "rectangle",
    "color": "#1a2B3c",
    "x": 0.25,
    "y": 0.5,
    "width": 0.5,
    "height": 0.25,
    "stroke_width": 0.02,
}


def annotation_request(**overrides):
    payload = {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": DRAFT,
        "expected_version": 0,
        "original": "PHOTO-1",
        "annotation": {**ANNOTATION_ENVELOPE, "shapes": [dict(ANNOTATION_SHAPE)]},
    }
    payload.update(overrides)
    return contracts.validate_request(payload)


def seed_draft(fake, *, draft=DRAFT, author=ACTOR, photo=""):
    row = {"name": draft, "author": author, "photo": photo}
    fake.db.committed.setdefault("Clinic Photo Draft", {})[draft] = dict(row)
    fake.db.docs.setdefault("Clinic Photo Draft", {})[draft] = dict(row)


# ---------------------------------------------------------------------------
# Native File stand-in: pinned 16.53 order, shape only, real mixin guards run
# ---------------------------------------------------------------------------
class _NativeFile:
    sequence = 0

    def __init__(
        self,
        *,
        file_url,
        file_name,
        content,
        owner=ACTOR,
        is_private=1,
        new=True,
        name=None,
        create_permitted=True,
        max_file_size=None,
        fail_in=None,
        observations=None,
    ):
        self.name = name
        self.file_url = file_url
        self.file_name = file_name
        self.content = content
        self._content = content
        self.content_hash = ""
        self.content_type = None
        self.file_size = 0
        self.file_type = None
        self.owner = owner
        self.is_private = is_private
        self.folder = None
        self.attached_to_doctype = None
        self.attached_to_name = None
        self._new = new
        self.create_permitted = create_permitted
        self.max_file_size = max_file_size
        self.fail_in = fail_in
        self.flags = types.SimpleNamespace(in_insert=False)
        self.calls = []
        self.insert_kwargs = []
        self.db_inserts = []
        self.observations = observations if observations is not None else {}

    # -- native entry points ---------------------------------------------
    def check_permission(self, permtype="read", permlevel=None):
        import frappe

        if getattr(self.flags, "ignore_permissions", False):
            return
        if permtype == "create" and not self.create_permitted:
            self.observations["create_denied"] = True
            raise frappe.PermissionError("create denied")

    def insert(
        self,
        ignore_permissions=None,
        ignore_links=None,
        ignore_if_duplicate=False,
        ignore_mandatory=None,
        set_name=None,
        set_child_names=True,
    ):
        self.calls.append("insert")
        self.insert_kwargs.append({"ignore_permissions": ignore_permissions})
        if ignore_permissions is not None:
            self.flags.ignore_permissions = bool(ignore_permissions)
        self.check_permission("create")
        self.before_insert()
        self.set_new_name()
        self.flags.in_insert = True
        try:
            self.validate()
        finally:
            self.flags.in_insert = False
        if self.fail_in == "validate":
            raise self._failure("validate")
        self._db_insert()
        self.after_insert()
        if self.fail_in == "after_insert":
            raise self._failure("after_insert")
        return self

    def _failure(self, stage):
        import frappe

        exc = frappe.ValidationError(f"native {stage} veto")
        self.observations["failed_in"] = stage
        return exc

    def before_insert(self):
        self.calls.append("before_insert")
        self.set_folder_name()
        self.set_is_private()
        self.set_file_name()
        self.validate_attachment_limit()
        self.set_file_type()
        self.validate_file_extension()
        self.validate_private_file_access()
        self.observations["bytes_before_save_file"] = (
            os.path.exists(self.get_full_path()) if self.file_url else None
        )
        self.save_file(content=self.get_content())
        self.validate_duplicate_entry()

    def set_new_name(self):
        if not self.name:
            type(self).sequence += 1
            self.name = f"FILE-{type(self).sequence}"

    def validate(self):
        self.calls.append("validate")

    def after_insert(self):
        self.calls.append("after_insert")

    def validate_duplicate_entry(self):
        self.calls.append("validate_duplicate_entry")

    def _db_insert(self):
        self._new = False
        import frappe

        frappe.db.add(
            "File",
            self.name,
            file_url=self.file_url,
            file_name=self.file_name,
            is_private=self.is_private,
            owner=self.owner,
            content_hash=self.content_hash,
            file_size=self.file_size,
            attached_to_doctype=None,
            attached_to_name=None,
            attached_to_field=None,
        )
        self.db_inserts.append(self.name)

    # -- native File.before_insert helpers -------------------------------
    def set_folder_name(self):
        self.calls.append("set_folder_name")

    def set_is_private(self):
        self.calls.append("set_is_private")

    def set_file_name(self):
        self.calls.append("set_file_name")

    def validate_attachment_limit(self):
        self.calls.append("validate_attachment_limit")

    def set_file_type(self):
        self.calls.append("set_file_type")

    def validate_file_extension(self):
        self.calls.append("validate_file_extension")

    def validate_private_file_access(self):
        self.calls.append("validate_private_file_access")

    # -- native byte path (must not run for a protected intent object) ----
    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        self.calls.append("save_file")
        if content is not None:
            self.content = content
            self._content = content
        if not self._content:
            return None
        self.content_type = "image/jpeg"
        self.file_size = self.check_max_file_size()
        self.content_hash = hashlib.md5(self._content).hexdigest()
        self.set_url_for_native_write()
        return self.save_file_on_filesystem()

    def set_url_for_native_write(self):
        prefix = "/private/files/" if self.is_private else "/files/"
        self.file_url = prefix + self.file_name

    def save_file_on_filesystem(self):
        self.calls.append("save_file_on_filesystem")
        path = self.write_file()
        return {"file_name": os.path.basename(path), "file_url": self.file_url}

    def write_file(self):
        self.calls.append("write_file")
        path = self.get_full_path()
        with open(path, "wb+") as handle:
            handle.write(self._content)
            os.fsync(handle.fileno())
        return path

    def check_max_file_size(self):
        import frappe

        size = len(self._content or b"")
        if self.max_file_size is not None and size > self.max_file_size:
            raise frappe.MaxFileSizeReachedError("File size exceeded")
        return size

    def get_content(self, encodings=None):
        self.calls.append("get_content")
        return self.content

    def get_full_path(self):
        import frappe

        if isinstance(self.file_url, str) and self.file_url.startswith(
            "/private/files/"
        ):
            relative = self.file_url[len("/private/files/") :]
            return frappe.get_site_path("private", "files", relative)
        if isinstance(self.file_url, str) and self.file_url.startswith("/files/"):
            relative = self.file_url[len("/files/") :]
            return frappe.get_site_path("public", "files", relative)
        return None

    # -- native Document shape used by the guards -------------------------
    def is_new(self):
        return self._new

    def get(self, fieldname, default=None):
        return getattr(self, fieldname, default)

    def get_doc_before_save(self):
        return None

    def run_method(self, method, *args, **kwargs):
        return getattr(self, method)(*args, **kwargs)

    def on_rollback(self):
        self.calls.append("on_rollback")

    def _delete_file_on_disk(self):
        self.calls.append("_delete_file_on_disk")
        raise AssertionError("native deletion must not run for a protected object")


class ExactFile(ProtectedFileMixin, _NativeFile):
    pass


class NativeReturnBindFile(ExactFile):
    """Exact object whose native ``after_insert`` return attempts a bind first."""

    bind_hook = None

    def after_insert(self):
        super().after_insert()
        hook = type(self).bind_hook
        if hook is not None:
            hook(self)


def exact_file(prepared, *, content=JPEG_BYTES, **overrides):
    basename = prepared.key.rsplit("/", 1)[-1]
    values = dict(file_url=prepared.key, file_name=basename, content=content)
    values.update(overrides)
    return ExactFile(**values)


class _Owner:
    def __init__(self):
        self.aborted = False
        self.reasons = []

    def publication_aborted(self):
        return self.aborted

    def abort_publication(self, reason):
        self.reasons.append(reason)
        self.aborted = True


def abort_latched(fake):
    return bool(
        getattr(fake.local, frappe_context.PUBLICATION_ABORTED_ATTR, None)
    )


def assert_aborted(testcase, fake):
    testcase.assertTrue(abort_latched(fake), "execution abort must be latched")
    testcase.assertFalse(frappe_context.publication_transaction_live())


def operation_rows(fake):
    return fake.db.docs.get(journal.OPERATION_DOCTYPE, {})


def stage_visit_fixture(request):
    """Stage visit in writer-only fixture; domain authority has separate tests."""
    from unittest.mock import patch
    from clinic_photos.visit import capture

    with patch.object(capture, "check_visit_authority"):
        return journal.stage_submission(request, actor=ACTOR, site=SITE)


@contextlib.contextmanager
def prepared_in_owner_scope(sites_path, *, request=None):
    """Yield ``(fake, owner, prepared)`` with a real PREPARED row in the same scope."""
    from unittest.mock import patch

    request = request or synthetic_request()
    with fake_runtime(sites_path=sites_path) as fake:
        seed_actor(fake)
        seed_patient(fake)
        # Writer fixture models journal/File ownership, not visit domain rows.
        # Visit authority is exercised separately by visit capture/journal tests.
        if request.kind == contracts.VISIT_ORIGINAL_CREATE:
            from clinic_photos.visit import capture
            authority = lambda: patch.object(capture, "check_visit_authority")
        else:
            authority = contextlib.nullcontext
        with authority():
            submission = journal.stage_submission(request, actor=ACTOR, site=SITE)
        target = exclusion.target_identity_for_operation(submission.id)
        with exclusion.hold_exclusion(submission.id, target) as owner:
            with authority():
                prepared = journal.prepare(submission)
            yield fake, owner, prepared


@contextlib.contextmanager
def synthetic_publication(prepared):
    """Server-bound non-draft publication context for this exact PREPARED operation."""
    with frappe_context.bound_publication(
        actor=ACTOR,
        operation="pending_insert",
        operation_id=prepared.id,
        storage_key=prepared.key,
        synthetic_patient=SYNTHETIC_PATIENT,
    ) as context:
        yield context


@contextlib.contextmanager
def live_intent_scope(prepared, document, owner):
    """This attempt's live owner and bound context with one real registered intent."""
    options = dict(actor=ACTOR, operation="pending_insert", storage_key=prepared.key)
    if prepared.synthetic_patient:
        options.update(
            operation_id=prepared.id,
            synthetic_patient=prepared.synthetic_patient,
        )
    else:
        options.update(draft=prepared.draft or "")
    with frappe_context.owned_publication_transaction(owner):
        with frappe_context.bound_publication(**options):
            writer._register_intent(prepared, document, document.content)
            yield


# ---------------------------------------------------------------------------
# Exact-object first write (bytes only inside the native save_file window)
# ---------------------------------------------------------------------------
class VisitPreparationOwnerTests(unittest.TestCase):
    def test_visit_finish_requires_native_commit_acknowledgement(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-ack-check-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        for failure in ("disabled", "missing_after", "after_raises"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    with frappe_context.owned_publication_transaction(owner):
                        bound = writer.write_first_object(prepared, exact_file(prepared))
                        writer._intent_for_key(prepared.key)["visit_lifetime"]["finalized"] = bound
                        def commit():
                            if failure != "disabled":
                                fake.db.before_commit.run()
                            if failure == "after_raises":
                                fake.db.after_commit.run()
                                raise RuntimeError("after commit failure")
                        with patch.object(writer, "_verify_staged_visit_final"), \
                             patch.object(fake.db, "commit", side_effect=commit):
                            with self.assertRaises(writer.FirstObjectUncertain):
                                writer.finish_visit_transaction(prepared, owner, "commit")
                        self.assertTrue(owner.poisoned)

    def test_visit_full_rollback_still_runs_for_poisoned_owner(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-rollback-poison", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    writer.write_first_object(prepared, exact_file(prepared))
                    db = fake.local.db
                    owner.poison("linker_failure")
                    writer.rollback_visit_transaction(prepared, owner, db)
                    self.assertEqual(db.rollback_calls, 1)
                    with self.assertRaises(writer.FirstObjectUncertain):
                        writer.rollback_visit_transaction(prepared, owner, db)
                    self.assertEqual(db.rollback_calls, 1)

    def test_visit_cleanup_refuses_foreign_actor_owner_or_connection(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-rollback-ident", visit_binding=binding, image_bytes=JPEG_BYTES)
        for mismatch in ("actor", "owner", "db"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    with frappe_context.owned_publication_transaction(owner):
                        writer.write_first_object(prepared, exact_file(prepared))
                        db = fake.local.db
                        if mismatch == "actor":
                            fake.session.user = "other"
                        if mismatch == "db":
                            fake.local.db = object()
                        patcher = (patch.object(frappe_context, "current_publication_owner", return_value=object())
                                   if mismatch == "owner" else contextlib.nullcontext())
                        with patcher, self.assertRaises(writer.FirstObjectUncertain):
                            writer.rollback_visit_transaction(prepared, owner, db)
                        self.assertEqual(db.rollback_calls, 0)

    def test_visit_attachment_rejects_post_hook_metadata_mutation(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-attachment-002", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    file_doc = exact_file(prepared)
                    bound = writer.write_first_object(prepared, file_doc)
                    photo = types.SimpleNamespace(
                        name="PHOTO", patient_reference="PAT", storage_key=prepared.key,
                        original_file=bound.file_name, original_sha256=prepared.expected_sha256,
                        original_width=2, original_height=2,
                    )
                    with frappe_context.visit_photo_insertion(prepared, bound, file_doc, photo):
                        frappe_context.require_visit_photo_insertion(photo)
                        fake.db.add("Clinic Photo", photo.name, patient_reference="PAT",
                                    storage_key=prepared.key, original_file=bound.file_name,
                                    original_sha256=prepared.expected_sha256,
                                    original_width=2, original_height=2)
                    row = fake.db.docs["File"][file_doc.name]
                    row["category"] = "original"
                    with self.assertRaises(fake.PermissionError):
                        with frappe_context.visit_file_attachment(prepared, bound, file_doc, photo):
                            fake.local._clinic_visit_attachment["used"] = True
                            row.update(attached_to_doctype="Clinic Photo", attached_to_name=photo.name,
                                       category="tampered")

    def test_visit_attachment_allows_only_exact_file_photo_and_metadata(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-attachment-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    file_doc = exact_file(prepared)
                    bound = writer.write_first_object(prepared, file_doc)
                    photo = types.SimpleNamespace(
                        name="PHOTO", patient_reference="PAT", storage_key=prepared.key,
                        original_file=bound.file_name, original_sha256=prepared.expected_sha256,
                        original_width=2, original_height=2,
                    )
                    with frappe_context.visit_photo_insertion(prepared, bound, file_doc, photo):
                        frappe_context.require_visit_photo_insertion(photo)
                        fake.db.add("Clinic Photo", photo.name, patient_reference="PAT",
                                    storage_key=prepared.key, original_file=bound.file_name,
                                    original_sha256=prepared.expected_sha256,
                                    original_width=2, original_height=2)
                    file_doc.attached_to_field = None
                    file_doc.category = "unchanged"
                    file_doc.creation = "2026-11-20 12:34:56.123456"
                    fake.db.docs["File"][file_doc.name]["category"] = "unchanged"
                    def fields():
                        return {key: getattr(file_doc, key, None) for key in (
                            "name", "file_url", "file_name", "owner", "is_private",
                            "file_size", "content_hash", "category", "creation", "attached_to_doctype",
                            "attached_to_name", "attached_to_field",
                        )}
                    file_doc.as_dict = fields
                    before = fields()
                    before["creation"] = datetime.datetime(2026, 11, 20, 12, 34, 56, 123456)
                    with self.assertRaises(fake.PermissionError):
                        with frappe_context.visit_file_attachment(prepared, bound, file_doc, copy.copy(photo)):
                            pass
                    with frappe_context.visit_file_attachment(prepared, bound, file_doc, photo):
                        file_doc.attached_to_doctype = "Clinic Photo"
                        file_doc.attached_to_name = photo.name
                        for field, value in (("category", "changed"), ("attached_to_name", "OTHER"),
                                             ("file_url", "/private/files/other"),
                                             ("is_private", 0), ("file_size", 999),
                                             ("creation", "2026-11-20 12:34:56.123457"),
                                             ("content", b"changed"),
                                             ("attached_to_field", "other")):
                            old = getattr(file_doc, field)
                            setattr(file_doc, field, value)
                            with self.assertRaises(fake.PermissionError):
                                frappe_context.require_visit_file_attachment(file_doc, before)
                            setattr(file_doc, field, old)
                        with self.assertRaises(fake.PermissionError):
                            frappe_context.require_visit_file_attachment(copy.copy(file_doc), before)
                        frappe_context.require_file_update(file_doc, before)
                        fake.db.docs["File"][file_doc.name].update(
                            attached_to_doctype="Clinic Photo", attached_to_name=photo.name,
                            attached_to_field=None,
                        )

    def test_visit_receipt_rejects_concrete_database_rebinding(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-db-rebind-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                fake.local.db = object()
                with patch.object(writer, "_consume_locked") as consume:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.consume_persisted_object(prepared, bound)
                    consume.assert_not_called()

    def test_visit_rejects_unaudited_precommit_callback(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-callback-00001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                writer.write_first_object(prepared, exact_file(prepared))
                called = []
                fake.db.before_commit.add(lambda: called.append(True))
                with self.assertRaises(writer.FirstObjectError):
                    fake.db.commit()
                self.assertEqual(called, [])

    def test_visit_finish_error_invalidates_and_poisons_exact_owner(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-finish-000001", visit_binding=binding, image_bytes=JPEG_BYTES)
        for action in ("commit", "rollback"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    with frappe_context.owned_publication_transaction(owner):
                        writer.write_first_object(prepared, exact_file(prepared))
                        entry = writer._intent_for_key(prepared.key)
                        with patch.object(fake.db, action, side_effect=RuntimeError("unknown")):
                            with self.assertRaises(writer.FirstObjectUncertain):
                                writer.finish_visit_transaction(prepared, owner, action)
                        self.assertFalse(entry["visit_lifetime"]["active"])
                        self.assertTrue(owner.poisoned)

    def test_visit_photo_scope_binds_exact_pending_file_and_photo(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-photo-scope-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    file_doc = exact_file(prepared)
                    bound = writer.write_first_object(prepared, file_doc)
                    photo = types.SimpleNamespace(
                        name="PHOTO", patient_reference="PAT", storage_key=prepared.key,
                        original_file=bound.file_name, original_sha256=prepared.expected_sha256,
                        original_width=2, original_height=2,
                    )
                    with frappe_context.visit_photo_insertion(prepared, bound, file_doc, photo):
                        frappe_context.require_visit_photo_insertion(photo)
                        for field, wrong in (
                            ("patient_reference", "OTHER"), ("storage_key", "OTHER"),
                            ("original_file", "OTHER"), ("original_sha256", "OTHER"),
                            ("original_width", 3), ("original_height", 3),
                        ):
                            original = getattr(photo, field)
                            setattr(photo, field, wrong)
                            with self.assertRaises(fake.PermissionError):
                                frappe_context.require_visit_photo_insertion(photo)
                            setattr(photo, field, original)
                        with self.assertRaises(fake.PermissionError):
                            frappe_context.require_visit_photo_insertion(copy.copy(photo))
                        fake.db.add("Clinic Photo", photo.name, patient_reference="PAT",
                                    storage_key=prepared.key, original_file=bound.file_name,
                                    original_sha256=prepared.expected_sha256,
                                    original_width=2, original_height=2)
                    with self.assertRaises(fake.PermissionError):
                        frappe_context.require_visit_photo_insertion(photo)

    def test_verified_visit_slot_and_published_can_commit_together(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-final-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                from clinic_photos.visit import capture
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    document = exact_file(prepared)
                    document.creation = "2026-11-22 12:00:01.123456"
                    bound = writer.write_first_object(prepared, document)
                    fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                                original_file=document.name, original_sha256=prepared.expected_sha256,
                                patient_reference="PAT", creation="2026-11-22 12:00:00.123456")
                    fake.db.docs["File"][document.name].update(
                        attached_to_doctype="Clinic Photo", attached_to_name="PHOTO",
                        creation=document.creation)
                    linkage = writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", document.name)
                    writer.consume_persisted_object(prepared, bound, domain_linkage=linkage)
                    class FakeSlot:
                        def __init__(self, values):
                            self.values = values
                            self.doctype = values["doctype"]
                            self.name = values["slot_key"]
                            self.flags = types.SimpleNamespace(ignore_permissions=None)

                        def insert(self, **kwargs):
                            self_test.assertEqual(kwargs, {"ignore_permissions": True})
                            values = {k: v for k, v in self.values.items() if k != "doctype"}
                            fake.db.add(self.doctype, self.name, **values)
                            return self

                    self_test = self
                    original_get_doc = fake.get_doc
                    def get_doc(values, *args, **kwargs):
                        if isinstance(values, dict) and values.get("doctype") == "Clinic Visit Photo Slot":
                            return FakeSlot(values)
                        if isinstance(values, str):
                            self.assertEqual(kwargs, {"for_update": True})
                            return types.SimpleNamespace(**fake.db.docs[values][args[0]])
                        return original_get_doc(values)
                    with patch.object(capture, "frappe", fake), \
                         patch.object(capture, "check_slot_availability"), \
                         patch.object(fake, "get_doc", side_effect=get_doc):
                        capture.insert_prepared_slot_link(prepared, bound)
                    journal.publish(prepared, bound, document.name, None)
                    writer.arm_visit_final_commit(prepared, bound)
                    writer.finish_visit_transaction(prepared, owner, "commit")
                    self.assertTrue(writer._intent_for_key(prepared.key)["visit_lifetime"]["commit_acknowledged"])
                    self.assertEqual(fake.db.committed[journal.OPERATION_DOCTYPE][prepared.id]["state"],
                                     "PUBLISHED")
                    slot = fake.db.committed["Clinic Visit Photo Slot"][capture.slot_key("APT", "Before")]
                    self.assertEqual((slot["photo"], slot["file_name"], slot["photo_creation"], slot["file_creation"]),
                                     ("PHOTO", document.name, "2026-11-22 12:00:00.123456",
                                      "2026-11-22 12:00:01.123456"))

    def test_commit_rechecks_slot_after_handshake(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-final-key-0002", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                from clinic_photos.visit.capture import slot_key
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    document = exact_file(prepared)
                    bound = writer.write_first_object(prepared, document)
                    fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                                original_file=document.name, original_sha256=prepared.expected_sha256,
                                patient_reference="PAT")
                    fake.db.docs["File"][document.name].update(
                        attached_to_doctype="Clinic Photo", attached_to_name="PHOTO")
                    linkage = writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", document.name)
                    writer.consume_persisted_object(prepared, bound, domain_linkage=linkage)
                    key = slot_key("APT", "Before")
                    slot = fake.db.add("Clinic Visit Photo Slot", key, slot_key=key, appointment="APT",
                                       patient="PAT", slot="Before", appointment_creation="ac",
                                       patient_creation="pc", body_area="Arm", viewpoint="Front",
                                       operation=prepared.id, photo="PHOTO")
                    journal.publish(prepared, bound, document.name, None)
                    writer.arm_visit_final_commit(prepared, bound)
                    fake.db.before_commit.add(lambda: slot.update(photo="OTHER"))
                    for _ in range(2):
                        with self.assertRaises(Exception):
                            fake.db.commit()
                    fake.db.rollback()
                    self.assertEqual(fake.db.docs[journal.OPERATION_DOCTYPE][prepared.id]["state"],
                                     "PREPARED")
    def test_visit_context_rejects_foreign_registered_owner(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-owner-key-9999", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with patch.object(frappe_context, "current_publication_owner", return_value=object()):
                    with self.assertRaises(lifecycle.PublicationContextError):
                        writer.write_first_object(prepared, exact_file(prepared))
                self.assertNotIn("File", fake.db.docs)

    def test_visit_first_write_rejects_preattached_file(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-attach-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        for field in ("attached_to_doctype", "attached_to_name", "attached_to_field"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    document = exact_file(prepared)
                    setattr(document, field, "OTHER")
                    with self.assertRaises(writer.FirstObjectDenied):
                        writer.write_first_object(prepared, document)
                    self.assertNotIn("File", fake.db.docs)

    def test_visit_receipt_blocks_repeated_early_commit_and_rollback_invalidates(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-txn-key-00001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                for _ in range(2):
                    with self.assertRaises(Exception):
                        fake.db.commit()
                fake.db.rollback()
                with self.assertRaises(Exception):
                    writer.consume_persisted_object(prepared, bound)

    def test_visit_consumption_rechecks_original_session_actor(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-actor-key-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                fake.session.user = "other"
                from unittest.mock import patch
                with patch.object(writer, "_consume_locked", return_value="unexpected") as consume:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.consume_persisted_object(prepared, bound)
                    consume.assert_not_called()

    def test_visit_consumption_rechecks_site_and_current_owner(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-site-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        for mismatch in ("site", "owner"):
            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    bound = writer.write_first_object(prepared, exact_file(prepared))
                    if mismatch == "site":
                        fake.local.site = "other"
                    with patch.object(writer, "_consume_locked", return_value="unexpected") as consume:
                        context = (patch.object(frappe_context, "current_publication_owner", return_value=object())
                                   if mismatch == "owner" else contextlib.nullcontext())
                        with context, self.assertRaises((writer.FirstObjectError, exclusion.ExclusionError)):
                            writer.consume_persisted_object(prepared, bound)
                        consume.assert_not_called()

    def test_visit_after_commit_callback_invalidates_receipt(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-end-key-00001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                fake.db.after_commit.run()
                with patch.object(writer, "_consume_locked", return_value="unexpected") as consume:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.consume_persisted_object(prepared, bound)
                    consume.assert_not_called()

    def test_visit_full_rollback_invalidates_receipt(self):
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-roll-key-00001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                fake.db.rollback()
                with patch.object(writer, "_consume_locked", return_value="unexpected") as consume:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.consume_persisted_object(prepared, bound)
                    consume.assert_not_called()
    def test_visit_context_cannot_authorize_other_operations(self):
        from clinic_photos.access.lifecycle import PublicationContext, PublicationContextError
        with self.assertRaises(PublicationContextError):
            PublicationContext(actor=ACTOR, draft="", operation="commit",
                storage_key="/private/files/clinic-photo-test.png",
                operation_id="op", visit_operation=True)

    def test_visit_writer_denies_uncommitted_preparation(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0005", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                with self.assertRaises(exclusion.ExclusionDenied):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertNotIn("File", fake.db.docs)

    def test_visit_writer_preserves_native_file_create_permission(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0006", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with self.assertRaises(fake.PermissionError):
                    writer.write_first_object(prepared, exact_file(prepared, create_permitted=False))
                self.assertNotIn("File", fake.db.docs)

    def test_visit_writer_rejects_mixed_reservation(self):
        from dataclasses import replace
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0007", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                changed = replace(prepared, visit_binding=replace(binding, patient="OTHER"))
                with self.assertRaises(writer.FirstObjectDenied):
                    writer.write_first_object(changed, exact_file(changed))
                self.assertNotIn("File", fake.db.docs)

    def test_visit_receipt_refuses_consumption_without_exact_photo_linkage(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0002", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                bound = writer.write_first_object(prepared, exact_file(prepared))
                with self.assertRaises(writer.ReceiptInvalid):
                    writer.consume_persisted_object(prepared, bound)

    def test_visit_linkage_requires_matching_patient_and_file(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0003", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                            original_file="FILE", original_sha256=prepared.expected_sha256,
                            patient_reference="PAT")
                result = writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", "FILE")
                self.assertEqual(result.name, "PHOTO")

    def test_visit_linkage_rejects_wrong_photo_patient(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0009", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                            original_file="FILE", original_sha256=prepared.expected_sha256,
                            patient_reference="OTHER")
                with self.assertRaises(writer.FirstObjectDenied):
                    writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", "FILE")

    def test_visit_consumption_returns_exact_server_evidence_with_attached_file(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0004", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                            original_file=document.name, original_sha256=prepared.expected_sha256,
                            patient_reference="PAT")
                fake.db.docs["File"][document.name].update(
                    attached_to_doctype="Clinic Photo", attached_to_name="PHOTO")
                linkage = writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", document.name)
                self.assertEqual(writer.consume_persisted_object(prepared, bound, domain_linkage=linkage),
                                 document.name)
                evidence, evidence_link = writer.require_visit_consumed_object(prepared, bound)
                self.assertIs(evidence, bound)
                self.assertEqual(evidence_link, linkage)
                with self.assertRaises(writer.ReceiptInvalid):
                    writer.require_visit_consumed_object(prepared, object())

    def test_visit_receipt_rejects_file_attached_to_another_photo(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0008", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key,
                            original_file=document.name, original_sha256=prepared.expected_sha256,
                            patient_reference="PAT")
                fake.db.docs["File"][document.name].update(
                    attached_to_doctype="Clinic Photo", attached_to_name="OTHER")
                linkage = writer.domain_linkage(prepared, "Clinic Photo", "PHOTO", document.name)
                with self.assertRaises(writer.ReceiptInvalid):
                    writer.consume_persisted_object(prepared, bound, domain_linkage=linkage)

    def test_visit_writer_creates_exact_native_file_after_durable_continuation(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-write-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                self.assertEqual(bound.file_name, document.name)
                self.assertIsNone(document.insert_kwargs[0]["ignore_permissions"])
                self.assertEqual(bound.receipt.sha256, prepared.expected_sha256)

    def test_prepare_rejects_stray_submitted_evidence_without_update(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        for field, value in (("expected_sha256", "a" * 64), ("expected_size", 1),
                             ("generation", 1), ("reservation_key", "/private/files/unexpected"),
                             ("published_file", "file"), ("published_version", 1),
                             ("published_domain_doctype", "Clinic Photo"),
                             ("published_domain_name", "photo"), ("creation_identity", "1:2")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as root, \
                 fake_runtime(sites_path=sites_volume(root)) as fake:
                seed_actor(fake)
                request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
                    request_key="visit-stray-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
                op = stage_visit_fixture(request)
                target = exclusion.target_identity_for_operation(op.id)
                with exclusion.hold_exclusion(op.id, target):
                    row = fake.db.docs[journal.OPERATION_DOCTYPE][op.id]
                    row[field] = value
                    original = copy.deepcopy(row)
                    with self.assertRaises(journal.JournalIntegrityError):
                        journal.prepare(op)
                    self.assertEqual(row, original)

    def test_visit_continuation_rejects_changed_prepared_evidence_and_poisons_owner(self):
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-cont-key-00001", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                row = fake.db.docs[journal.OPERATION_DOCTYPE][prepared.id]
                row["expected_sha256"] = "0" * 64
                with self.assertRaises(exclusion.ExclusionDenied):
                    owner.continue_after_own_preparation(prepared)
                self.assertTrue(owner.poisoned)

    def test_visit_continuation_compares_complete_reconstructed_value(self):
        from dataclasses import replace
        from unittest.mock import patch
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-cont-key-00002", visit_binding=binding, image_bytes=JPEG_BYTES)
        with tempfile.TemporaryDirectory() as root:
            with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                fake.db.allow_transaction_control = True
                fake.db.commit()
                changed = replace(prepared, visit_binding=replace(binding, body_area="Leg"))
                with patch.object(journal, "_prepared_operation", return_value=changed):
                    with self.assertRaises(exclusion.ExclusionDenied):
                        owner.continue_after_own_preparation(prepared)
                self.assertTrue(owner.poisoned)

    def test_both_slots_prepare_under_live_owner_with_same_opaque_target(self):
        binding = contracts.VisitBinding(
            appointment="PRIVATE-APT", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT-1",
        )
        from dataclasses import replace
        targets = []
        for index, value in enumerate((
            binding, replace(binding, slot="After", encounter="ENC-1", encounter_creation="ec"),
        )):
            request = contracts.SaveRequest(
                kind=contracts.VISIT_ORIGINAL_CREATE, request_key=f"visit-owner-key-{index:04d}",
                visit_binding=value, image_bytes=JPEG_BYTES,
            )
            with tempfile.TemporaryDirectory() as root:
                with prepared_in_owner_scope(sites_volume(root), request=request) as (fake, owner, prepared):
                    self.assertEqual(prepared.visit_binding, value)
                    self.assertEqual(prepared.state, "PREPARED")
                    self.assertEqual(prepared.target_identity, journal.target_identity(request))
                    self.assertNotIn("PRIVATE-APT", prepared.target_identity)
                    self.assertNotIn("PRIVATE-APT", prepared.key)
                    self.assertEqual(owner.target_identity, prepared.target_identity)
                    self.assertEqual(fake.db.committed.get(journal.OPERATION_DOCTYPE, {}), {})
                    fake.db.allow_transaction_control = True
                    fake.db.commit()
                    self.assertEqual(journal.load_verified_operation(prepared.id).request, request)
                    self.assertEqual(owner.continue_after_own_preparation(prepared).state, "PREPARED")
                    with self.assertRaises(exclusion.ExclusionError):
                        owner.continue_after_own_preparation(prepared)
                    targets.append(prepared.target_identity)
        self.assertEqual(targets[0], targets[1])

    def test_opposite_slots_compete_for_same_target_without_takeover(self):
        from dataclasses import replace
        binding = contracts.VisitBinding(
            appointment="PRIVATE-APT", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT-1",
        )
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            seed_actor(fake)
            first = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
                request_key="visit-lock-key-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
            second = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
                request_key="visit-lock-key-0002",
                visit_binding=replace(binding, slot="After", encounter="E", encounter_creation="ec"),
                image_bytes=JPEG_BYTES)
            op1 = stage_visit_fixture(first)
            op2 = stage_visit_fixture(second)
            target = exclusion.target_identity_for_operation(op1.id)
            self.assertEqual(target, exclusion.target_identity_for_operation(op2.id))
            with exclusion.hold_exclusion(op1.id, target):
                with self.assertRaises(exclusion.ExclusionBusy):
                    with exclusion.hold_exclusion(op2.id, target):
                        self.fail("opposite slot acquired held target")

    def test_owner_refuses_tampered_visit_binding_before_preparation(self):
        binding = contracts.VisitBinding(
            appointment="PRIVATE-APT", patient="PAT-1", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT-1",
        )
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            seed_actor(fake)
            request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
                request_key="visit-tamper-key-001", visit_binding=binding, image_bytes=JPEG_BYTES)
            operation = stage_visit_fixture(request)
            target = exclusion.target_identity_for_operation(operation.id)
            with exclusion.hold_exclusion(operation.id, target):
                row = fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]
                row["visit_binding_json"] = row["visit_binding_json"].replace("Arm", "Leg")
                with self.assertRaises(exclusion.ExclusionDenied):
                    journal.prepare(operation)
                self.assertEqual(row["state"], "SUBMITTED")


class FirstWriterExactObjectTests(unittest.TestCase):
    def test_first_writer_preserves_exact_jpeg_and_key_inside_native_window(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, _owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                receipt = bound.receipt

                # Bytes exist only after the native create check and inside the
                # server-bound first-save specialization.
                self.assertFalse(document.observations.get("bytes_before_save_file"))
                self.assertIsNone(document.insert_kwargs[0]["ignore_permissions"])
                self.assertNotIn("save_file", document.calls)
                self.assertNotIn("write_file", document.calls)
                self.assertNotIn("save_file_on_filesystem", document.calls)
                for step in (
                    "set_folder_name",
                    "set_is_private",
                    "set_file_name",
                    "validate_attachment_limit",
                    "set_file_type",
                    "validate_file_extension",
                    "validate_private_file_access",
                    "validate",
                    "after_insert",
                ):
                    self.assertIn(step, document.calls)

                path = target_path(sites_path, prepared.key)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)
                info = os.stat(path, follow_symlinks=False)
                self.assertTrue(stat.S_ISREG(info.st_mode))
                self.assertEqual(info.st_nlink, 1)
                self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
                self.assertEqual(receipt.key, prepared.key)
                self.assertEqual(receipt.sha256, EXPECTED_SHA256)
                self.assertEqual(receipt.size, EXPECTED_SIZE)
                self.assertEqual(
                    (receipt.device, receipt.inode), (info.st_dev, info.st_ino)
                )
                self.assertEqual(receipt.generation, prepared.generation)

                self.assertEqual(document.file_url, prepared.key)
                self.assertEqual(document.file_name, prepared.key.rsplit("/", 1)[-1])
                self.assertTrue(int(document.is_private))
                self.assertEqual(document.owner, ACTOR)
                self.assertEqual(
                    os.listdir(private_root(sites_path)),
                    [prepared.key.rsplit("/", 1)[-1]],
                )

                row = fake.db.docs["File"][document.name]
                self.assertEqual(row["file_url"], prepared.key)
                self.assertEqual(row["owner"], ACTOR)
                self.assertTrue(int(row["is_private"]))
                self.assertEqual(int(row["file_size"]), EXPECTED_SIZE)
                self.assertEqual(
                    row["content_hash"], hashlib.md5(JPEG_BYTES).hexdigest()
                )
                self.assertNotEqual(row["content_hash"], receipt.sha256)

                self.assertNotIn("Clinic Photo Draft", fake.db.docs)
                self.assertNotIn("Clinic Photo", fake.db.docs)
                self.assertNotIn("Clinic Photo Annotation Version", fake.db.docs)
                self.assertEqual(fake.db.commit_calls, 0)
                self.assertEqual(fake.db.rollback_calls, 0)

    def test_annotation_append_publishes_through_the_same_writer(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            request = annotation_request()
            with prepared_in_owner_scope(sites_path, request=request) as (
                fake,
                owner,
                prepared,
            ):
                seed_draft(fake)
                self.assertEqual(prepared.draft, DRAFT)
                self.assertIsNone(prepared.synthetic_patient)
                content = request.annotation.encode("utf-8")
                document = exact_file(prepared, content=content)

                bound = writer.write_first_object(prepared, document)

                path = target_path(sites_path, prepared.key)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), content)
                receipt = bound.receipt
                self.assertEqual(receipt.key, prepared.key)
                self.assertEqual(receipt.size, len(content))
                self.assertEqual(
                    receipt.sha256, hashlib.sha256(content).hexdigest()
                )
                row = fake.db.docs["File"][document.name]
                self.assertEqual(row["file_url"], prepared.key)
                self.assertEqual(row["owner"], ACTOR)
                self.assertTrue(int(row["is_private"]))
                self.assertEqual(int(row["file_size"]), len(content))
                self.assertFalse(owner.poisoned)
                self.assertFalse(abort_latched(fake))

                self.assertEqual(
                    writer.consume_persisted_object(prepared, bound), document.name
                )
                self.assertFalse(owner.poisoned)
                self.assertEqual(fake.db.commit_calls, 0)
                self.assertEqual(fake.db.rollback_calls, 0)

    def test_create_permission_denial_happens_before_any_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared, create_permitted=False)
                with self.assertRaises(fake.PermissionError):
                    writer.write_first_object(prepared, document)

                self.assertTrue(document.observations.get("create_denied"))
                self.assertEqual(os.listdir(private_root(sites_path)), [])
                self.assertNotIn("File", fake.db.docs)
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)

    def test_native_max_file_size_veto_happens_before_any_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared, max_file_size=8)
                with self.assertRaises(fake.MaxFileSizeReachedError):
                    writer.write_first_object(prepared, document)

                self.assertEqual(os.listdir(private_root(sites_path)), [])
                self.assertNotIn("File", fake.db.docs)
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)

    def test_registered_write_hooks_refuse_before_any_bytes(self):
        for hook in ("write_file", "before_write_file"):
            with self.subTest(hook=hook), tempfile.TemporaryDirectory() as root:
                sites_path = sites_volume(root)
                with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                    fake.hook_methods[hook] = lambda *args, **kwargs: None
                    document = exact_file(prepared)
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, document)

                    self.assertEqual(os.listdir(private_root(sites_path)), [])
                    self.assertNotIn("File", fake.db.docs)
                    self.assertTrue(owner.poisoned)
                    assert_aborted(self, fake)

    def test_unresolvable_write_hooks_refuse_before_any_bytes(self):
        scenarios = {
            "resolver_raises": "raise",
            "namespace_missing": "missing",
        }
        for label, mode in scenarios.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root:
                sites_path = sites_volume(root)
                with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                    document = exact_file(prepared)
                    if mode == "raise":
                        fake.hook_resolution_error = AttributeError(
                            "simulated frappe.utils.get_hook_method failure"
                        )
                        with self.assertRaises(writer.FirstObjectError):
                            writer.write_first_object(prepared, document)
                    else:
                        sys.modules.pop("frappe.utils", None)
                        try:
                            with self.assertRaises(writer.FirstObjectError):
                                writer.write_first_object(prepared, document)
                        finally:
                            sys.modules["frappe.utils"] = fake.utils

                    self.assertEqual(os.listdir(private_root(sites_path)), [])
                    self.assertNotIn("File", fake.db.docs)
                    self.assertTrue(owner.poisoned)
                    assert_aborted(self, fake)

    def test_native_validate_veto_after_bytes_preserves_residue_and_latches_abort(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared, fail_in="validate")
                with self.assertRaises(fake.ValidationError):
                    writer.write_first_object(prepared, document)

                path = target_path(sites_path, prepared.key)
                self.assertTrue(os.path.exists(path))
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)
                self.assertNotIn("File", fake.db.docs)
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)

    def test_native_after_insert_veto_after_row_preserves_residue_and_latches_abort(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared, fail_in="after_insert")
                with self.assertRaises(fake.ValidationError):
                    writer.write_first_object(prepared, document)

                self.assertIn("File", fake.db.docs)
                self.assertTrue(os.path.exists(target_path(sites_path, prepared.key)))
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)
                # No usable receipt was produced by the failed attempt.
                self.assertIsNone(
                    writer._intent_for_document(document).get("receipt")
                )


# ---------------------------------------------------------------------------
# Provisional receipt -> persisted identity bridge
# ---------------------------------------------------------------------------
class ReceiptBridgeTests(unittest.TestCase):
    def test_receipt_binds_persisted_identity_once_and_replay_poisons_owner(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                self.assertEqual(bound.file_name, document.name)
                self.assertEqual(bound.receipt.key, prepared.key)

                file_name = writer.consume_persisted_object(prepared, bound)
                self.assertEqual(file_name, document.name)
                self.assertFalse(owner.poisoned)

                with self.assertRaises(writer.FirstObjectError):
                    writer.consume_persisted_object(prepared, bound)
                self.assertTrue(owner.poisoned)
                self.assertEqual(fake.db.commit_calls, 0)

    def test_second_bind_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                with self.assertRaises(writer.FirstObjectError):
                    writer.bind_persisted_object(prepared, bound.receipt, document)
                self.assertTrue(owner.poisoned)

    def test_equal_value_forged_receipt_is_refused_before_bind_once(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, owner, prepared):
                basename = prepared.key.rsplit("/", 1)[-1]
                document = NativeReturnBindFile(
                    file_url=prepared.key,
                    file_name=basename,
                    content=JPEG_BYTES,
                )
                recorded = {}

                def forged_bind(file_doc):
                    entry = writer._intent_for_document(file_doc)
                    issued = entry["receipt"]
                    recorded["bound_before_forged"] = entry["bound"]
                    recorded["issued_receipt_present"] = issued is not None
                    forged = contracts.CreationReceipt(
                        operation_id=issued.operation_id,
                        generation=issued.generation,
                        key=issued.key,
                        device=issued.device,
                        inode=issued.inode,
                        size=issued.size,
                        sha256=issued.sha256,
                    )
                    recorded["forged_equals_issued"] = forged == issued
                    # Bind-once must not mask the issuance identity check: at this
                    # point the intent holds the issued receipt and has no binding.
                    writer.bind_persisted_object(entry["operation"], forged, file_doc)

                NativeReturnBindFile.bind_hook = forged_bind
                try:
                    with self.assertRaises(writer.ReceiptInvalid):
                        writer.write_first_object(prepared, document)
                finally:
                    NativeReturnBindFile.bind_hook = None

                self.assertIsNone(recorded.get("bound_before_forged"))
                self.assertTrue(recorded.get("issued_receipt_present"))
                self.assertTrue(recorded.get("forged_equals_issued"))
                self.assertTrue(owner.poisoned)
                self.assertTrue(os.path.exists(target_path(sites_path, prepared.key)))

    def test_issued_receipt_binds_at_native_return_positive_control(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, _owner, prepared):
                basename = prepared.key.rsplit("/", 1)[-1]
                document = NativeReturnBindFile(
                    file_url=prepared.key,
                    file_name=basename,
                    content=JPEG_BYTES,
                )
                recorded = {}

                def issued_bind(file_doc):
                    entry = writer._intent_for_document(file_doc)
                    bound = writer.bind_persisted_object(
                        entry["operation"], entry["receipt"], file_doc
                    )
                    recorded["bound"] = bound
                    recorded["file_name"] = bound.file_name

                NativeReturnBindFile.bind_hook = issued_bind
                try:
                    # The hook's bind succeeds; the writer's own bind at return is
                    # then the once-guard refusal, which is the expected outcome here.
                    with self.assertRaises(writer.ReceiptInvalid):
                        writer.write_first_object(prepared, document)
                finally:
                    NativeReturnBindFile.bind_hook = None

                bound = recorded.get("bound")
                self.assertIsNotNone(bound)
                self.assertEqual(bound.file_name, recorded.get("file_name"))
                self.assertEqual(bound.file_name, document.name)

    def test_substituted_receipt_and_changed_row_fields_poison_owner(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                fake.db.set_value(
                    "File", document.name, {"owner": "other@example.test"}
                )
                with self.assertRaises(writer.FirstObjectError):
                    writer.consume_persisted_object(prepared, bound)
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)

    def test_copied_or_constructed_persisted_object_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                with self.assertRaises(TypeError):
                    writer.PersistedObject()
                with self.assertRaises(TypeError):
                    copy.copy(bound)
                with self.assertRaises(writer.FirstObjectError):
                    writer.consume_persisted_object(prepared, object())
                self.assertTrue(owner.poisoned)

    def test_target_replaced_between_write_and_bind_refuses(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, owner, prepared):
                document = exact_file(prepared)
                # Replace the object through the writer window: bind must not trust
                # the path after the fact.
                original = writer.bind_persisted_object
                replaced = {}

                def bind_after_replacement(operation, receipt, file_doc):
                    path = target_path(sites_path, prepared.key)
                    os.rename(path, path + ".moved")
                    with open(path, "wb") as handle:
                        handle.write(JPEG_BYTES)
                    replaced["done"] = True
                    return original(operation, receipt, file_doc)

                writer.bind_persisted_object = bind_after_replacement
                try:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, document)
                finally:
                    writer.bind_persisted_object = original

                self.assertTrue(replaced.get("done"))
                self.assertTrue(owner.poisoned)

    def test_content_hash_is_metadata_not_authority(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                fake.db.set_value("File", document.name, {"content_hash": "0" * 32})
                self.assertEqual(
                    writer.consume_persisted_object(prepared, bound), document.name
                )
                self.assertFalse(owner.poisoned)


# ---------------------------------------------------------------------------
# Confinement, durability and refusal paths
# ---------------------------------------------------------------------------
class FirstWriterRefusalTests(unittest.TestCase):
    def test_existing_target_bytes_are_never_truncated(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                path = target_path(sites_path, prepared.key)
                with open(path, "wb") as handle:
                    handle.write(b"existing-protected-bytes")
                with self.assertRaises(writer.FirstObjectError):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertTrue(owner.poisoned)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), b"existing-protected-bytes")
                self.assertNotIn("File", fake.db.docs)
                assert_aborted(self, fake)

    def test_symlink_at_target_refuses_without_following(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                outside = os.path.join(root, "outside.bin")
                with open(outside, "wb") as handle:
                    handle.write(b"outside")
                path = target_path(sites_path, prepared.key)
                os.symlink(outside, path)
                with self.assertRaises(writer.FirstObjectError):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertTrue(owner.poisoned)
                self.assertTrue(os.path.islink(path))
                with open(outside, "rb") as handle:
                    self.assertEqual(handle.read(), b"outside")
                self.assertNotIn("File", fake.db.docs)

    def test_nested_and_separator_basenames_are_refused_by_the_flat_guard(self):
        for key in (
            "/private/files/clinic-photo-nested/clinic-photo-object.jpg",
            "/private/files/../clinic-photo-object.jpg",
            "/private/files/clinic-photo-object.jpg/..",
            "/private/files/clinic\\photo-object.jpg",
        ):
            with self.subTest(key=key):
                with self.assertRaises(writer.FirstObjectDenied):
                    writer._relative_basename(key)
        # The flat canonical key of the real operation is accepted.
        self.assertEqual(
            writer._relative_basename(
                "/private/files/clinic-photo-22222222222222222222222222222222.jpg"
            ),
            "clinic-photo-22222222222222222222222222222222.jpg",
        )

    def test_flat_guard_runs_on_the_real_create_path(self):
        # Reachability control: the direct guard tests above only matter if the create
        # path actually consults it before any byte exists. Later native dedup/bind
        # reads also pass through the guard, so only the first observation proves the
        # create path: removing the create-path guard moves the first call after bytes.
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, _owner, prepared):
                original = writer._relative_basename
                path = target_path(sites_path, prepared.key)
                existed_before_call = []
                seen = []

                def spy(key):
                    existed_before_call.append(os.path.exists(path))
                    seen.append(key)
                    return original(key)

                writer._relative_basename = spy
                try:
                    writer.write_first_object(prepared, exact_file(prepared))
                finally:
                    writer._relative_basename = original
                self.assertTrue(seen)
                self.assertFalse(existed_before_call[0])
                self.assertIn(prepared.key, seen)
                self.assertTrue(os.path.exists(path))

    def test_hard_link_ambiguity_after_write_poisons_owner(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                path = target_path(sites_path, prepared.key)
                os.link(path, os.path.join(root, "second-link.bin"))
                with self.assertRaises(writer.FirstObjectError):
                    writer.consume_persisted_object(prepared, bound)
                self.assertTrue(owner.poisoned)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)

    def test_partial_write_preserves_residue_and_latches_abort(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                original = writer._write_all

                def failing_write(descriptor, data):
                    os.write(descriptor, data[: len(data) // 2])
                    raise OSError("simulated short write")

                writer._write_all = failing_write
                try:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, exact_file(prepared))
                finally:
                    writer._write_all = original

                self.assertTrue(owner.poisoned)
                path = target_path(sites_path, prepared.key)
                with open(path, "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES[: len(JPEG_BYTES) // 2])
                self.assertNotIn("File", fake.db.docs)
                assert_aborted(self, fake)

    def test_file_fsync_failure_preserves_residue_and_latches_abort(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                original = writer._fsync_file

                def failing_fsync(_descriptor):
                    raise OSError("simulated file fsync failure")

                writer._fsync_file = failing_fsync
                try:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, exact_file(prepared))
                finally:
                    writer._fsync_file = original

                self.assertTrue(owner.poisoned)
                with open(target_path(sites_path, prepared.key), "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)
                self.assertNotIn("File", fake.db.docs)
                assert_aborted(self, fake)

    def test_directory_fsync_failure_preserves_residue_and_latches_abort(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                original = writer._fsync_directory

                def failing_fsync(_descriptor):
                    raise OSError("simulated directory fsync failure")

                writer._fsync_directory = failing_fsync
                try:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, exact_file(prepared))
                finally:
                    writer._fsync_directory = original

                self.assertTrue(owner.poisoned)
                with open(target_path(sites_path, prepared.key), "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)
                self.assertNotIn("File", fake.db.docs)
                assert_aborted(self, fake)

    def test_refusal_latches_execution_abort_and_a_fresh_owner_cannot_continue(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                path = target_path(sites_path, prepared.key)
                with open(path, "wb") as handle:
                    handle.write(b"existing")
                with self.assertRaises(writer.FirstObjectError):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)

                # A caught refusal must not let the same execution continue under a
                # fresh owner: the abort latch is sticky.
                fresh = _Owner()
                with self.assertRaises(frappe_context.PublicationContextError):
                    with frappe_context.owned_publication_transaction(fresh):
                        with synthetic_publication(prepared):
                            self.fail("a fresh owner must not bind after an abort")

    def test_unowned_execution_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, _owner, prepared):
                pass
            with fake_runtime(sites_path=sites_path) as fake:
                seed_actor(fake)
                seed_patient(fake)
                with self.assertRaises(exclusion.ExclusionDenied):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_foreign_file_objects_are_refused(self):
        cases = {
            "wrong_key": dict(file_url="/private/files/clinic-photo-other.jpg"),
            "wrong_owner": dict(owner="other@example.test"),
            "public": dict(is_private=0),
            "persisted": dict(new=False, name="FILE-EXISTING"),
            "changed_bytes": dict(content=JPEG_BYTES + b"x"),
            "no_bytes": dict(content=b""),
        }
        for label, overrides in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as root:
                sites_path = sites_volume(root)
                with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, exact_file(prepared, **overrides))
                    self.assertTrue(owner.poisoned)
                    self.assertEqual(os.listdir(private_root(sites_path)), [])
                    self.assertNotIn("File", fake.db.docs)

    def test_changed_durable_evidence_refuses_before_any_bytes(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                fake.db.set_value(
                    journal.OPERATION_DOCTYPE, prepared.id, {"expected_sha256": "0" * 64}
                )
                with self.assertRaises(writer.FirstObjectError):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertTrue(owner.poisoned)
                self.assertEqual(os.listdir(private_root(sites_path)), [])
                self.assertNotIn("File", fake.db.docs)


# ---------------------------------------------------------------------------
# First-content intent gate and ordinary File delegation
# ---------------------------------------------------------------------------
class FirstContentIntentTests(unittest.TestCase):
    def test_first_content_without_writer_intent_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, _owner, prepared):
                # A plain native chain object pointed at the PREPARED key, with the
                # server-bound context but no writer intent: no bytes may be written.
                document = exact_file(prepared)
                with frappe_context.owned_publication_transaction(_Owner()):
                    with synthetic_publication(prepared):
                        with self.assertRaises(fake.PermissionError):
                            document.before_insert()
                self.assertEqual(os.listdir(private_root(sites_path)), [])
                self.assertNotIn("File", fake.db.docs)

    def test_consumed_intent_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                bound = writer.write_first_object(prepared, document)
                writer.consume_persisted_object(prepared, bound)
                with frappe_context.owned_publication_transaction(owner):
                    with self.assertRaises(writer.FirstObjectError):
                        writer.save_first_object(document, content=JPEG_BYTES)
                    # The refusal itself must latch: the scope is still open here.
                    assert_aborted(self, fake)
                self.assertTrue(owner.poisoned)
                fresh = _Owner()
                with self.assertRaises(frappe_context.PublicationContextError):
                    with frappe_context.owned_publication_transaction(fresh):
                        with synthetic_publication(prepared):
                            self.fail("a spent intent must not resume under a fresh owner")

    def test_ordinary_file_insert_delegates_to_native(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                seed_actor(fake)
                public_name = "scan.png"
                document = ExactFile(
                    file_url=f"/files/{public_name}",
                    file_name=public_name,
                    content=b"public-bytes",
                    is_private=0,
                )
                document.insert()
                self.assertIn("save_file", document.calls)
                self.assertIn("write_file", document.calls)
                self.assertTrue(
                    os.path.exists(
                        os.path.join(sites_path, SITE, "public", "files", public_name)
                    )
                )

    def test_alias_variant_spelling_is_detected(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                # Percent-encoded spelling of the same canonical key: an exact-URL
                # filter would miss it, the canonical comparison must not.
                variant = prepared.key.replace("clinic-photo-", "clinic%2Dphoto-")
                fake.db.add("File", "FILE-ALIAS", file_url=variant, file_name="x.jpg")
                with self.assertRaises(writer.FirstObjectError):
                    writer.write_first_object(prepared, exact_file(prepared))
                self.assertTrue(owner.poisoned)

    def test_alias_scan_budget_exhaustion_denies(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                for index in range(3):
                    fake.db.add(
                        "File", f"FILE-{index}", file_url=f"/files/x{index}.png"
                    )
                page = writer.FILE_SCAN_PAGE_SIZE
                pages = writer.FILE_SCAN_MAX_PAGES
                writer.FILE_SCAN_PAGE_SIZE = 1
                writer.FILE_SCAN_MAX_PAGES = 1
                try:
                    with self.assertRaises(writer.FirstObjectError):
                        writer.write_first_object(prepared, exact_file(prepared))
                finally:
                    writer.FILE_SCAN_PAGE_SIZE = page
                    writer.FILE_SCAN_MAX_PAGES = pages
                self.assertTrue(owner.poisoned)
                self.assertEqual(os.listdir(private_root(sites_path)), [])


# ---------------------------------------------------------------------------
# Direct first-save seam: owner transaction and latched refusals
# ---------------------------------------------------------------------------
class DirectSaveSeamTests(unittest.TestCase):
    def test_forced_write_modes_refuse_without_bytes_and_latch(self):
        for mode in ("overwrite", "ignore_existing_file_check", "decode"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                sites_path = sites_volume(root)
                with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                    document = exact_file(prepared)
                    with live_intent_scope(prepared, document, owner):
                        with self.assertRaises(writer.FirstObjectError):
                            writer.save_first_object(document, **{mode: True})
                        # Latched by the direct call itself, not by the owner scope
                        # exit: this assertion runs while the scope is still open.
                        assert_aborted(self, fake)
                    self.assertEqual(os.listdir(private_root(sites_path)), [])
                    self.assertNotIn("File", fake.db.docs)
                    self.assertTrue(owner.poisoned)
                    assert_aborted(self, fake)
                    fresh = _Owner()
                    with self.assertRaises(frappe_context.PublicationContextError):
                        with frappe_context.owned_publication_transaction(fresh):
                            with synthetic_publication(prepared):
                                self.fail(
                                    "a forced-mode refusal must stop a fresh owner"
                                )

    def test_direct_save_without_a_bound_context_refuses_and_latches(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                with frappe_context.owned_publication_transaction(owner):
                    writer._register_intent(prepared, document, document.content)
                    with self.assertRaises(writer.FirstObjectError):
                        writer.save_first_object(document, content=JPEG_BYTES)
                    # Latched by the direct call, while this owner scope is open.
                    assert_aborted(self, fake)
                    self.assertEqual(os.listdir(private_root(sites_path)), [])
                    self.assertNotIn("File", fake.db.docs)
                self.assertTrue(owner.poisoned)
                fresh = _Owner()
                with self.assertRaises(frappe_context.PublicationContextError):
                    with frappe_context.owned_publication_transaction(fresh):
                        with synthetic_publication(prepared):
                            self.fail(
                                "a missing-context refusal must stop a fresh owner"
                            )

    def test_direct_save_outside_any_owner_transaction_is_refused(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, owner, prepared):
                document = exact_file(prepared)
                writer._register_intent(prepared, document, document.content)
                with self.assertRaises(writer.FirstObjectError):
                    writer.save_first_object(document, content=JPEG_BYTES)
                self.assertEqual(os.listdir(private_root(sites_path)), [])
                self.assertNotIn("File", fake.db.docs)
                self.assertTrue(owner.poisoned)
                assert_aborted(self, fake)


# ---------------------------------------------------------------------------
# Server-bound non-draft synthetic patient/target binding
# ---------------------------------------------------------------------------
class NonDraftSyntheticBindingTests(unittest.TestCase):
    def test_synthetic_context_binds_without_a_draft_placeholder(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, _owner, prepared):
                with frappe_context.owned_publication_transaction(_Owner()):
                    with synthetic_publication(prepared) as context:
                        self.assertEqual(context.draft, "")
                        self.assertEqual(context.synthetic_patient, SYNTHETIC_PATIENT)
                        self.assertEqual(context.operation_id, prepared.id)
                        self.assertTrue(
                            lifecycle.context_matches(
                                context,
                                operation="pending_insert",
                                storage_key=prepared.key,
                                synthetic_patient=SYNTHETIC_PATIENT,
                            )
                        )
                        self.assertFalse(
                            lifecycle.context_matches(
                                context,
                                operation="pending_insert",
                                storage_key=prepared.key,
                                synthetic_patient="OTHER-PATIENT",
                            )
                        )
                        self.assertIsNone(
                            frappe_context.require_file_insert(
                                types.SimpleNamespace(
                                    file_url=prepared.key,
                                    file_name=prepared.key.rsplit("/", 1)[-1],
                                    owner=ACTOR,
                                    is_private=1,
                                    name="",
                                )
                            )
                        )
                self.assertNotIn("Clinic Photo Draft", fake.db.docs)

    def test_synthetic_entry_requires_authoritative_prepared_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)

            def bind(prepared, **overrides):
                options = dict(
                    actor=ACTOR,
                    operation="pending_insert",
                    operation_id=prepared.id,
                    storage_key=prepared.key,
                    synthetic_patient=SYNTHETIC_PATIENT,
                )
                options.update(overrides)
                return frappe_context.bound_publication(**options)

            scenarios = {
                "wrong_operation_id": dict(operation_id="0" * 32),
                "missing_operation_id": dict(operation_id=None),
                "other_patient": dict(synthetic_patient=SYNTHETIC_PATIENT + "-X"),
            }
            for label, overrides in scenarios.items():
                with self.subTest(case=label):
                    with prepared_in_owner_scope(sites_path) as (
                        _fake,
                        _owner,
                        prepared,
                    ):
                        with frappe_context.owned_publication_transaction(_Owner()):
                            with self.assertRaises(
                                frappe_context.PublicationContextError
                            ):
                                with bind(prepared, **overrides):
                                    self.fail("entry must refuse")

            # Row-level disagreements refuse at entry too.
            for label, changes in {
                "missing_patient": ("Patient", None),
                "not_prepared": ("row", {"state": "ABORTED"}),
                "other_actor": ("row", {"actor": "other@example.test"}),
                "with_draft": ("row", {"draft": DRAFT}),
            }.items():
                with self.subTest(case=label):
                    with prepared_in_owner_scope(sites_path) as (
                        fresh,
                        _owner2,
                        fresh_prepared,
                    ):
                        if changes[0] == "Patient":
                            fresh.db.docs["Patient"].clear()
                        else:
                            fresh.db.set_value(
                                journal.OPERATION_DOCTYPE,
                                fresh_prepared.id,
                                changes[1],
                            )
                        with frappe_context.owned_publication_transaction(_Owner()):
                            with self.assertRaises(
                                frappe_context.PublicationContextError
                            ):
                                with bind(fresh_prepared):
                                    self.fail("entry must refuse")

    def test_synthetic_gate_denies_when_the_row_changes_after_entry(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (fake, _owner, prepared):
                with frappe_context.owned_publication_transaction(_Owner()):
                    with synthetic_publication(prepared):
                        fake.db.set_value(
                            journal.OPERATION_DOCTYPE,
                            prepared.id,
                            {"state": "ABORTED"},
                        )
                        with self.assertRaises(fake.PermissionError):
                            frappe_context.require_file_insert(
                                types.SimpleNamespace(
                                    file_url=prepared.key,
                                    file_name=prepared.key.rsplit("/", 1)[-1],
                                    owner=ACTOR,
                                    is_private=1,
                                    name="",
                                )
                            )

    def test_synthetic_entry_requires_a_live_owner(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with prepared_in_owner_scope(sites_path) as (_fake, _owner, prepared):
                pass
            with fake_runtime(sites_path=sites_path) as fake:
                seed_actor(fake)
                seed_patient(fake)
                with frappe_context.owned_publication_transaction(_Owner()):
                    with self.assertRaises(frappe_context.PublicationContextError):
                        with frappe_context.bound_publication(
                            actor=ACTOR,
                            operation="pending_insert",
                            operation_id=prepared.id,
                            storage_key=prepared.key,
                            synthetic_patient=SYNTHETIC_PATIENT,
                        ):
                            self.fail("no live owner must refuse the synthetic binding")

    def test_annotation_context_still_requires_a_draft(self):
        with self.assertRaises(lifecycle.PublicationContextError):
            lifecycle.PublicationContext(
                actor=ACTOR,
                draft="",
                operation="pending_insert",
                storage_key="/private/files/clinic-annotation-abc.json",
            )
        with self.assertRaises(lifecycle.PublicationContextError):
            lifecycle.PublicationContext(
                actor=ACTOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key="/private/files/clinic-annotation-abc.json",
                synthetic_patient=SYNTHETIC_PATIENT,
            )
        with self.assertRaises(lifecycle.PublicationContextError):
            lifecycle.PublicationContext(
                actor=ACTOR,
                draft="",
                operation="pending_insert",
                storage_key="/private/files/clinic-annotation-abc.json",
                synthetic_patient=SYNTHETIC_PATIENT,
            )


# ---------------------------------------------------------------------------
# Native acceptance remains pending; local unit proof is not native proof
# ---------------------------------------------------------------------------
class NativeWriterChecksPending(unittest.TestCase):
    @unittest.skip(
        "pending native window: exact-object insertion through the installed File "
        "doctype must be observed with the real MRO, create permission and hook map"
    )
    def test_native_exact_object_insert_through_installed_file(self):
        self.fail("native exact-object insertion has not run")

    @unittest.skip(
        "pending native window: installed File must leave JPEG bytes/dimensions/EXIF, "
        "canonical key, privacy and owner unchanged"
    )
    def test_native_exif_dimensions_and_key_preserved(self):
        self.fail("native byte-preservation check has not run")

    @unittest.skip(
        "pending native window: duplicate validation must not retarget the protected "
        "object and must not repoint an ordinary later upload"
    )
    def test_native_duplicate_validation_no_retarget(self):
        self.fail("native duplicate-retarget check has not run")

    @unittest.skip(
        "pending native window: partial-write/fsync residue and the effective hook map "
        "must be observed on the development stack, not only against a fake boundary"
    )
    def test_native_partial_write_and_hook_residue(self):
        self.fail("native residue check has not run")


if __name__ == "__main__":
    unittest.main()
