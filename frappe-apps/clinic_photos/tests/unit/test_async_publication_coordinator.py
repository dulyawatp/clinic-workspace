"""Focused tests for the two-phase publication coordinator (Task 8).

These tests exercise the real own code: the real durable journal, the real exclusion
owner over real OS advisory locks in a per-test sites volume, the real publication
contracts/validation, the real runtime adapter and File mixin over a narrow native
``File`` stand-in, the real DocType controllers, and ``publication.coordinator``.

Only the framework import boundary is faked: an in-memory ``frappe`` module with an
explicit committed/transactional split, so "the coordinator committed exactly two
transactions" and "a later read sees only durable state" are observable. This is a
local fixture, not a Frappe/MariaDB simulation: installed transactions, DocPerms,
hooks, the real MRO, MariaDB isolation and the native image library remain pending
native evidence (see ``NativeCoordinatorChecksPending``).

Bytes for the synthetic original and the annotation payload are produced only by the
real protected writer inside an owned scratch sites volume; no protected original is
seeded, mutated or deleted, and no generic operation/domain metadata is pre-seeded as
a stand-in for a real publication.
"""

import base64
import contextlib
import copy
import hashlib
import json
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

from clinic_photos.access import storage
from clinic_photos.access.file_mixin import ProtectedFileMixin
from clinic_photos.publication import contracts, exclusion, journal, process, writer

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
OTHER_ACTOR = "other@example.test"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
PATIENT = "SYNTHETIC-PATIENT-1"
REQUEST_KEY = "request-key-0001"
SYNTHETIC_REQUEST_KEY = "request-key-0002"
GATE = process.GATE_CONFIG_KEY
SYNTHETIC_GATE = "clinic_photos_synthetic_mode"

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
JPEG_SHA256 = hashlib.sha256(JPEG_BYTES).hexdigest()

ENVELOPE = {"schema_version": 1, "coordinate_space": "normalized"}
SHAPE = {
    "id": "shape-1",
    "type": "rectangle",
    "color": "#1a2B3c",
    "x": 0.25,
    "y": 0.5,
    "width": 0.5,
    "height": 0.25,
    "stroke_width": 0.02,
}

VERSION_DOCTYPE = "Clinic Photo Annotation Version"
PHOTO_DOCTYPE = "Clinic Photo"
DRAFT_DOCTYPE = "Clinic Photo Draft"


# ---------------------------------------------------------------------------
# Framework boundary: committed/transactional rows plus the called call shapes
# ---------------------------------------------------------------------------
class _FakeCallbacks:
    def __init__(self):
        self.callbacks = []

    @property
    def _functions(self):
        """Installed ``CallbackManager`` exposes its pending callables here."""
        return self.callbacks

    def add(self, callback):
        self.callbacks.append(callback)

    def run(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def reset(self):
        self.callbacks = []


_UNSET = object()


class _FakeDB:
    """In-memory rows with an explicit committed/transactional split."""

    def __init__(self):
        self.committed = {}
        self.docs = {}
        self.before_commit = _FakeCallbacks()
        self.after_commit = _FakeCallbacks()
        self.before_rollback = _FakeCallbacks()
        self.after_rollback = _FakeCallbacks()
        self.commit_calls = 0
        self.rollback_calls = 0
        self.commit_snapshots = []
        self.commit_failure = None
        # Commit index -> error raised *after* the commit was durably applied. This is
        # the ambiguous "committed but the acknowledgement was lost" case, which is a
        # different outcome from a commit that never applied.
        self.commit_failure_after_apply = {}
        self.transaction_writes = 0
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

    def update_row(self, doctype, name, **values):
        row = self.docs.setdefault(doctype, {}).setdefault(name, {"name": name})
        row.update(values)
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
        self.commit_calls += 1
        if self.commit_failure is not None:
            raise self.commit_failure
        self.before_rollback.reset()
        self.after_rollback.reset()
        self.before_commit.run()
        self.committed = copy.deepcopy(self.docs)
        self.commit_snapshots.append(copy.deepcopy(self.committed))
        self.after_commit.run()
        lost = self.commit_failure_after_apply.get(self.commit_calls)
        if lost is not None:
            raise lost

    def rollback(self):
        self.rollback_calls += 1
        self.before_commit.reset()
        self.after_commit.reset()
        self.before_rollback.run()
        self.docs = copy.deepcopy(self.committed)
        self.after_rollback.run()


# ---------------------------------------------------------------------------
# Native document shapes: the real DocType controllers run, ordering is pinned
# ---------------------------------------------------------------------------
class _DocumentBase(dict):
    doctype = ""
    autoname_format = "hash"

    _ATTRIBUTES = frozenset({"name", "flags", "doctype", "autoname_format"})

    def __init__(self, values=None, *, db=None, name=None, new=True):
        super().__init__({key: value for key, value in (values or {}).items()})
        object.__setattr__(self, "name", name or "")
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_new", new)
        object.__setattr__(self, "flags", types.SimpleNamespace(ignore_permissions=False))
        object.__setattr__(
            self,
            "_previous",
            copy.deepcopy(self._db.docs.get(self.doctype, {}).get(name))
            if not new and name
            else None,
        )
        object.__setattr__(self, "_insert_calls", [])
        object.__setattr__(self, "_save_calls", [])

    def __setattr__(self, key, value):
        """Document fields are the row; only infrastructure stays an attribute."""
        if key.startswith("_") or key in self._ATTRIBUTES:
            object.__setattr__(self, key, value)
            return
        self[key] = value

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key) from None

    # -- dict/document surface used by the controllers --------------------
    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return copy.deepcopy(self._previous)

    def check_permission(self, permtype="read", permlevel=None):
        if getattr(self.flags, "ignore_permissions", False):
            return
        import frappe

        if not frappe.has_permission(self.doctype, permtype, doc=self.name or ""):
            raise frappe.PermissionError(f"{permtype} denied")

    # -- native ordering ---------------------------------------------------
    def insert(self, ignore_permissions=None, **_kwargs):
        self._insert_calls.append(ignore_permissions)
        if ignore_permissions is not None:
            self.flags.ignore_permissions = bool(ignore_permissions)
        self.check_permission("create")
        self.before_insert()
        self.set_new_name()
        self.validate()
        self._db_insert()
        self.after_insert()
        return self

    def save(self, ignore_permissions=None, **_kwargs):
        self._save_calls.append(ignore_permissions)
        if ignore_permissions is not None:
            self.flags.ignore_permissions = bool(ignore_permissions)
        self.check_permission("write")
        self._previous = copy.deepcopy(self._db.docs.get(self.doctype, {}).get(self.name))
        self.validate()
        self._db_update()
        return self

    def set_new_name(self):
        if self.name:
            return
        autoname = getattr(self, "autoname", None)
        if callable(autoname):
            autoname()
            return
        if self.autoname_format == "hash":
            self.name = uuid.uuid4().hex
            return
        if self.autoname_format.startswith("format:"):
            template = self.autoname_format[len("format:") :]
            self.name = template.format(**{key: self.get(key) for key in self})
            return
        raise AssertionError(f"unsupported fake autoname {self.autoname_format!r}")

    def _db_insert(self):
        self._new = False
        values = {
            key: value
            for key, value in self.items()
            if key not in ("doctype", "name")
        }
        self._db.add(self.doctype, self.name, **values)

    def _db_update(self):
        row = self._db.docs.setdefault(self.doctype, {}).setdefault(
            self.name, {"name": self.name}
        )
        row.update(
            {
                key: value
                for key, value in self.items()
                if key not in ("doctype", "name")
            }
        )

    # -- default native hooks (the controllers override the real ones) -----
    def before_insert(self):
        pass

    def validate(self):
        pass

    def after_insert(self):
        pass


def _controller_class(module_path, class_name):
    import importlib

    return getattr(importlib.import_module(module_path), class_name)


OPERATION_MODULE = (
    "clinic_photos.clinic_photos.doctype."
    "clinic_photo_save_operation.clinic_photo_save_operation"
)
VERSION_MODULE = (
    "clinic_photos.clinic_photos.doctype."
    "clinic_photo_annotation_version.clinic_photo_annotation_version"
)
PHOTO_MODULE = (
    "clinic_photos.clinic_photos.doctype.clinic_photo.clinic_photo"
)
DRAFT_MODULE = (
    "clinic_photos.clinic_photos.doctype.clinic_photo_draft.clinic_photo_draft"
)

_DOCUMENT_SPECS = {
    journal.OPERATION_DOCTYPE: (
        OPERATION_MODULE,
        "ClinicPhotoSaveOperation",
        "hash",
    ),
    VERSION_DOCTYPE: (
        VERSION_MODULE,
        "ClinicPhotoAnnotationVersion",
        "format:{draft}-v{version_number}",
    ),
    PHOTO_DOCTYPE: (PHOTO_MODULE, "ClinicPhoto", "hash"),
    DRAFT_DOCTYPE: (DRAFT_MODULE, "ClinicPhotoDraft", "hash"),
}

_DOCUMENT_CLASSES = {}


def _document_class(doctype):
    """The real controller class, bound to the fake document ordering."""
    if doctype in _DOCUMENT_CLASSES:
        return _DOCUMENT_CLASSES[doctype]
    try:
        module_path, class_name, autoname_format = _DOCUMENT_SPECS[doctype]
    except KeyError:
        raise AssertionError(f"unsupported fake doctype {doctype!r}") from None
    base = _controller_class(module_path, class_name)

    class _Bound(base):
        pass

    _Bound.doctype = doctype
    _Bound.autoname_format = autoname_format
    _Bound.__name__ = f"Fake{class_name}"
    _DOCUMENT_CLASSES[doctype] = _Bound
    return _Bound


class _NativeFile:
    """Native ``File`` stand-in following the pinned 16.53 insert order (shape only).

    ``ProtectedFileMixin`` is real, so ``require_file_insert``, the first-save window
    and the persisted-identity check all run against this object.
    """

    sequence = 0
    doctype = "File"

    def __init__(
        self,
        *,
        file_url=None,
        file_name=None,
        content=None,
        owner=ACTOR,
        is_private=1,
        new=True,
        name=None,
        create_permitted=True,
        fail_in=None,
        db=None,
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
        self.fail_in = fail_in
        self._db = db
        self.flags = types.SimpleNamespace(in_insert=False)
        self.calls = []
        self.insert_kwargs = []
        self.observations = {}

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
            self.observations["failed_in"] = "validate"
            raise self._failure("validate")
        self._db_insert()
        self.after_insert()
        if self.fail_in == "after_insert":
            self.observations["failed_in"] = "after_insert"
            raise self._failure("after_insert")
        return self

    def _failure(self, stage):
        import frappe

        return frappe.ValidationError(f"native {stage} veto")

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
        self._db.add(
            "File",
            self.name,
            file_url=self.file_url,
            file_name=self.file_name,
            is_private=self.is_private,
            owner=self.owner,
            content_hash=self.content_hash,
            file_size=self.file_size,
            attached_to_doctype=self.attached_to_doctype,
            attached_to_name=self.attached_to_name,
        )

    def save(self, ignore_permissions=None, **_kwargs):
        """Native update order: permission/validation, then the persisted row.

        ``ProtectedFileMixin.validate`` is real, so the File update guard runs against
        this object's persisted row exactly as installed Frappe would run it.
        """
        self.calls.append("save")
        if ignore_permissions is not None:
            self.flags.ignore_permissions = bool(ignore_permissions)
        self._previous = copy.deepcopy(self._db.docs.get("File", {}).get(self.name))
        self.validate()
        if self.fail_in == "validate":
            self.observations["failed_in"] = "validate"
            raise self._failure("validate")
        self._db_update()
        return self

    def _db_update(self):
        row = self._db.docs.setdefault("File", {}).setdefault(
            self.name, {"name": self.name}
        )
        row.update(
            {
                "file_url": self.file_url,
                "file_name": self.file_name,
                "is_private": self.is_private,
                "owner": self.owner,
                "content_hash": self.content_hash,
                "file_size": self.file_size,
                "attached_to_doctype": self.attached_to_doctype,
                "attached_to_name": self.attached_to_name,
            }
        )
        return row

    def set_folder_name(self):
        pass

    def set_is_private(self):
        if isinstance(self.file_url, str) and self.file_url.startswith(
            storage.PRIVATE_URL_PREFIX
        ):
            self.is_private = 1

    def set_file_name(self):
        if not self.file_name and isinstance(self.file_url, str):
            self.file_name = self.file_url.rsplit("/", 1)[-1]

    def validate_attachment_limit(self):
        pass

    def set_file_type(self):
        self.file_type = "Image"

    def validate_file_extension(self):
        pass

    def validate_private_file_access(self):
        pass

    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        raise AssertionError("the writer must own the first byte write")

    def check_max_file_size(self):
        import frappe

        size = len(self._content or b"")
        if size > 8 * 1024 * 1024:
            raise frappe.MaxFileSizeReachedError("File size exceeded")
        return size

    def get_content(self, encodings=None):
        self.calls.append("get_content")
        return self.content

    def get_full_path(self):
        import frappe

        if isinstance(self.file_url, str) and self.file_url.startswith(
            storage.PRIVATE_URL_PREFIX
        ):
            relative = self.file_url[len(storage.PRIVATE_URL_PREFIX) :]
            return frappe.get_site_path("private", "files", relative)
        if isinstance(self.file_url, str) and self.file_url.startswith("/files/"):
            relative = self.file_url[len("/files/") :]
            return frappe.get_site_path("public", "files", relative)
        return None

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


class _FakeFrappe(types.ModuleType):
    def __init__(self, *, user=ACTOR, site=SITE, sites_path=None, conf=None):
        super().__init__("frappe")
        self.db = _FakeDB()
        self.session = types.SimpleNamespace(user=user)
        self.local = types.SimpleNamespace(site=site, sites_path=sites_path)
        self.local.db = self.db
        self.sites_path = sites_path
        self.site = site
        self.conf = dict(conf if conf is not None else {GATE: True, SYNTHETIC_GATE: True})
        self.form_dict = {}
        self.permissions = set()
        self.hook_methods = {}
        self.request = None
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})
        self.MaxFileSizeReachedError = type("MaxFileSizeReachedError", (Exception,), {})
        self.FileTypeNotAllowed = type("FileTypeNotAllowed", (Exception,), {})
        self.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        self.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
        utils = types.ModuleType("frappe.utils")
        utils.get_hook_method = self._hook_method
        self.utils = utils

    def get_site_path(self, *parts):
        return os.path.join(self.sites_path, self.site, *parts)

    def init(self, site="", sites_path=".", **_kwargs):
        # The fake keeps its own temporary volume; the call shape is recorded so a
        # test can assert the fresh bootstrap without leaving the sandbox.
        self.init_calls = getattr(self, "init_calls", [])
        self.init_calls.append((site, sites_path))
        self.local.site = site
        self.session.user = "Guest"

    def connect(self, *, set_admin_as_user=True, **_kwargs):
        self.connect_calls = getattr(self, "connect_calls", [])
        self.connect_calls.append(set_admin_as_user)

    def set_user(self, username):
        self.set_user_calls = getattr(self, "set_user_calls", [])
        self.set_user_calls.append(username)
        self.session.user = username

    def destroy(self):
        self.destroyed = True
        self.local.site = ""

    def get_doc(self, values, name=None):
        import frappe

        if isinstance(values, str):
            doctype = values
            row = copy.deepcopy(self.db.docs.get(doctype, {}).get(name))
            if row is None:
                raise frappe.ValidationError("missing document")
            document = _document_class(doctype)(row, db=self.db, name=name, new=False)
            return document
        doctype = values["doctype"]
        if doctype == "File":
            # Native ``Document.is_new()`` is driven by ``__islocal``: a dict-built
            # document is *not* new unless the caller marks it local. Modelling that
            # here keeps "this is a new native document" observable instead of assumed.
            document = ExactFile(
                file_url=values.get("file_url"),
                file_name=values.get("file_name"),
                content=values.get("content"),
                owner=values.get("owner", ACTOR),
                is_private=int(values.get("is_private", 1)),
                new=bool(values.get("__islocal")),
                db=self.db,
            )
            return document
        return _document_class(doctype)(
            {key: value for key, value in values.items() if key != "doctype"},
            db=self.db,
        )

    def has_permission(self, doctype, permission_type="read", doc=None, **_kwargs):
        name = getattr(doc, "name", doc) or ""
        return (doctype, permission_type) in self.permissions or (
            doctype,
            permission_type,
            name,
        ) in self.permissions

    def call_hook_method(self, hook, **_kwargs):
        raise AssertionError(f"native write hook must never be invoked: {hook}")

    def _hook_method(self, hook, *_args, **_kwargs):
        return self.hook_methods.get(hook)

    def throw(self, message, exc=None):
        raise (exc or self.ValidationError)(message)

    def whitelist(self, *_args, **_kwargs):
        def decorate(function):
            return function

        return decorate


@contextlib.contextmanager
def fake_runtime(*, sites_path, user=ACTOR, site=SITE, conf=None):
    module = _FakeFrappe(user=user, site=site, sites_path=sites_path, conf=conf)
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = _DocumentBase
    model.document = document
    module.model = model
    previous = sys.modules.get("frappe")
    previous_utils = sys.modules.get("frappe.utils")
    previous_model = sys.modules.get("frappe.model")
    previous_document = sys.modules.get("frappe.model.document")
    sys.modules["frappe"] = module
    sys.modules["frappe.utils"] = module.utils
    sys.modules["frappe.model"] = model
    sys.modules["frappe.model.document"] = document
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
        if previous_model is None:
            sys.modules.pop("frappe.model", None)
        else:
            sys.modules["frappe.model"] = previous_model
        if previous_document is None:
            sys.modules.pop("frappe.model.document", None)
        else:
            sys.modules["frappe.model.document"] = previous_document


# ---------------------------------------------------------------------------
# Fixtures (server-derived metadata rows only; bytes come from the writer)
# ---------------------------------------------------------------------------
def seed_actor(fake, *, user=ACTOR, roles=("Physician",), enabled=1):
    for target in (fake.db.committed, fake.db.docs):
        target.setdefault("User", {})[user] = {"name": user, "enabled": enabled}
        for index, role in enumerate(roles):
            name = f"HR-{user}-{index}"
            target.setdefault("Has Role", {})[name] = {
                "name": name,
                "parent": user,
                "parenttype": "User",
                "role": role,
            }


def seed_patient(fake, patient=PATIENT):
    for target in (fake.db.committed, fake.db.docs):
        target.setdefault("Patient", {})[patient] = {"name": patient}


def seed_annotation_target(fake, *, version=0, author=ACTOR, photo=PHOTO):
    """A committed draft/photo/patient fixture for the annotation vertical slice."""
    for target in (fake.db.committed, fake.db.docs):
        target.setdefault(DRAFT_DOCTYPE, {})[DRAFT] = {
            "name": DRAFT,
            "author": author,
            "owner": author,
            "photo": photo,
            "current_version_number": version,
            "current_version": None,
        }
        target.setdefault(PHOTO_DOCTYPE, {})[PHOTO] = {
            "name": PHOTO,
            "patient_reference": PATIENT,
            "original_file": "FILE-ORIGINAL",
            "storage_key": "/private/files/clinic-photo-original.jpg",
            "original_sha256": "a" * 64,
            "original_width": 2,
            "original_height": 2,
        }


def allow_permissions(fake, *, draft_write=True, photo_read=True, patient_read=True):
    permissions = {
        (DRAFT_DOCTYPE, "read"),
        (PHOTO_DOCTYPE, "read") if photo_read else (PHOTO_DOCTYPE, "read-denied"),
        ("Patient", "read") if patient_read else ("Patient", "read-denied"),
    }
    if draft_write:
        permissions.add((DRAFT_DOCTYPE, "write"))
    fake.permissions.update(permissions)


def sites_volume(root):
    sites_path = os.path.realpath(root)
    for relative in ("private/files", "public/files"):
        os.makedirs(os.path.join(sites_path, SITE, relative), exist_ok=True)
    return sites_path


def private_root(sites_path):
    return os.path.join(sites_path, SITE, "private", "files")


def target_path(sites_path, key):
    return os.path.join(
        private_root(sites_path), key[len(storage.PRIVATE_URL_PREFIX) :]
    )


def annotation_request(**overrides):
    payload = {
        "kind": "annotation_append",
        "request_key": REQUEST_KEY,
        "draft": DRAFT,
        "expected_version": 0,
        "original": PHOTO,
        "annotation": {**ENVELOPE, "shapes": [dict(SHAPE)]},
    }
    payload.update(overrides)
    return contracts.validate_request(payload)


def synthetic_request(**overrides):
    payload = {
        "kind": "synthetic_original_create",
        "request_key": SYNTHETIC_REQUEST_KEY,
        "synthetic_patient": PATIENT,
        "image_base64": JPEG_2X2_BASE64,
    }
    payload.update(overrides)
    return contracts.validate_request(payload)


def stage_committed(fake, request):
    """Stage one SUBMITTED row in its own framework transaction, then commit it."""
    operation = journal.stage_submission(request, actor=ACTOR, site=SITE)
    fake.db.commit()
    return operation


def publish(fake, operation):
    from clinic_photos.publication import coordinator

    with coordinator.child_process_publication():
        return coordinator.publish_submission(operation)


def operation_rows(fake, *, committed=False):
    source = fake.db.committed if committed else fake.db.docs
    return source.get(journal.OPERATION_DOCTYPE, {})


def file_rows(fake, *, committed=False):
    source = fake.db.committed if committed else fake.db.docs
    return source.get("File", {})


class PrivateVisitCoordinatorTests(unittest.TestCase):
    def test_visit_abort_rechecks_full_submitted_identity_and_advanced_state(self):
        from dataclasses import replace
        from unittest.mock import patch
        from clinic_photos.access import frappe_context

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-rejected-002", visit_binding=binding, image_bytes=JPEG_BYTES)
        fresh = contracts.SubmittedOperation(
            id="a" * 32, site=SITE, actor=ACTOR, kind=request.kind,
            request_key=request.request_key, payload_digest="a" * 64, request=request,
        )
        verified = types.SimpleNamespace(
            state="SUBMITTED", kind=request.kind, payload_digest=fresh.payload_digest,
            reservation_key=None, id=fresh.id,
        )
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            with patch.object(frappe_context, "get_save_operation", return_value={}) as read, \
                 patch.object(journal, "_verified_operation", return_value=verified), \
                 patch.object(journal, "_submitted_operation",
                              return_value=replace(fresh, actor="other")):
                with self.assertRaises(journal.JournalIntegrityError):
                    journal.abort(fresh)
                read.assert_called_once_with(fresh.id, for_update=True)
                verified.state = "PREPARED"
                with self.assertRaises(journal.JournalStateError):
                    journal.abort(fresh)
                self.assertNotIn("ABORTED", repr(fake.db.docs))

    def test_definite_visit_revocation_aborts_only_locked_submission(self):
        from unittest.mock import Mock, patch
        from clinic_photos.publication import coordinator

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-rejected-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        fresh = contracts.SubmittedOperation(
            id="a" * 32, site=SITE, actor=ACTOR, kind=request.kind,
            request_key=request.request_key, payload_digest="a" * 64, request=request,
        )
        owner = Mock()
        @contextlib.contextmanager
        def hold(*args):
            yield owner
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            fake.db.transaction_writes = 0
            from clinic_photos.visit import capture
            with patch.object(exclusion, "target_identity_for_operation", return_value=contracts.visit_target_identity(binding)), \
                 patch.object(exclusion, "hold_exclusion", side_effect=hold), \
                 patch.object(journal, "load_submission", return_value=fresh) as load, \
                 patch.object(capture, "check_slot_availability", side_effect=capture.VisitEligibilityRejected("revoked")) as check, \
                 patch.object(journal, "abort") as abort, \
                 patch.object(coordinator, "_commit") as commit, \
                 patch.object(coordinator, "_rollback") as rollback, \
                 patch.object(journal, "prepare") as prepare:
                result = coordinator._execute_private_visit(fresh)
                self.assertEqual(result.status, "rejected")
                load.assert_called_once_with(fresh.id, for_update=True)
                abort.assert_called_once_with(fresh)
                commit.assert_called_once_with()
                prepare.assert_not_called()
                abort.reset_mock()
                commit.reset_mock()
                check.side_effect = RuntimeError("database uncertain")
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._execute_private_visit(fresh)
                abort.assert_not_called()
                commit.assert_not_called()
                check.side_effect = capture.VisitEligibilityRejected("revoked")
                commit.side_effect = RuntimeError("commit uncertain")
                rollback.reset_mock()
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._execute_private_visit(fresh)
                commit.assert_called_once()
                rollback.assert_not_called()
                owner.poison.assert_any_call("visit_abort_commit_uncertain")

    def test_visit_entry_requires_visit_child_scope_and_gate(self):
        from unittest.mock import patch
        from clinic_photos.publication import coordinator
        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-entry-key-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        submitted = contracts.SubmittedOperation(
            id="a" * 32, site=SITE, actor=ACTOR, kind=request.kind,
            request_key=request.request_key, payload_digest="a" * 64, request=request,
        )
        outcome = contracts.SaveOutcome("saved", submitted.id)
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            with patch.object(process, "_visit_gate_enabled", return_value=True), \
                 patch.object(journal, "load_submission", return_value=submitted), \
                 patch.object(coordinator, "_execute_private_visit", return_value=outcome) as execute:
                with self.assertRaises(coordinator.CoordinatorUnavailable):
                    coordinator.publish_visit_submission(submitted)
                with coordinator.child_process_publication():
                    with self.assertRaises(coordinator.CoordinatorUnavailable):
                        coordinator.publish_visit_submission(submitted)
                with coordinator.child_process_publication(mode="visit"):
                    self.assertEqual(coordinator.publish_visit_submission(submitted), outcome)
                    execute.assert_called_once_with(submitted)
                    with self.assertRaises(coordinator.CoordinatorDenied):
                        coordinator.publish_submission(submitted)
                    fake.session.user = "other"
                    with self.assertRaises(coordinator.CoordinatorUnavailable):
                        coordinator.publish_visit_submission(submitted)
                    fake.session.user = ACTOR
                    with patch.object(process, "_visit_gate_enabled", return_value=False):
                        with self.assertRaises(coordinator.CoordinatorUnavailable):
                            coordinator.publish_visit_submission(submitted)

    def test_private_visit_readback_requires_clean_exact_committed_evidence(self):
        from dataclasses import replace
        from unittest.mock import patch
        from clinic_photos.publication import coordinator

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-readback-0001", visit_binding=binding, image_bytes=JPEG_BYTES)
        fresh = contracts.SubmittedOperation(
            id="a" * 32, site=SITE, actor=ACTOR, kind=request.kind,
            request_key=request.request_key, payload_digest="a" * 64, request=request,
        )
        prepared = contracts.PreparedOperation(
            id=fresh.id, site=SITE, actor=ACTOR, kind=fresh.kind, payload_digest=fresh.payload_digest,
            key="/private/files/clinic-photo-test.jpg", generation=1, draft=None,
            expected_version=None, synthetic_patient=None,
            target_identity=contracts.visit_target_identity(binding), expected_sha256=JPEG_SHA256,
            expected_size=len(JPEG_BYTES), state="PREPARED", visit_binding=binding,
        )
        outcome = contracts.PublishedOutcome(reservation=replace(prepared, state="PUBLISHED"),
            file_name="FILE", domain_doctype="Clinic Photo", domain_name="PHOTO",
            version=None, creation_identity="1:2")
        verified = types.SimpleNamespace(
            id=fresh.id, request_key=fresh.request_key, state="PUBLISHED", kind=fresh.kind, actor=ACTOR, site=SITE,
            request=request, payload_digest=fresh.payload_digest, published=outcome,
        )
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            fake.db.transaction_writes = 0
            fake.db.add("Clinic Photo", "PHOTO", storage_key=prepared.key, patient_reference="PAT",
                        original_file="FILE", original_sha256=JPEG_SHA256)
            file = fake.db.add("File", "FILE", file_url=prepared.key,
                               file_name=prepared.key.rsplit("/", 1)[-1], is_private=1, owner=ACTOR,
                               file_size=len(JPEG_BYTES), attached_to_doctype="Clinic Photo",
                               attached_to_name="PHOTO", attached_to_field=None)
            with patch.object(journal, "load_verified_operation", return_value=verified) as load, \
                 patch.object(writer, "verify_published_object") as verify:
                result = coordinator._readback_visit(fresh, prepared, outcome)
                self.assertEqual(result.status, "saved")
                load.assert_called_with(fresh.id)
                verify.assert_called_once()
                file["attached_to_name"] = "OTHER"
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._readback_visit(fresh, prepared, outcome)
                file["attached_to_name"] = "PHOTO"
                fake.db.transaction_writes = 1
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._readback_visit(fresh, prepared, outcome)

    def test_private_visit_attempt_prepares_continues_publishes_and_reads_back(self):
        from unittest.mock import Mock, patch
        from clinic_photos.publication import coordinator

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        request = contracts.SaveRequest(kind=contracts.VISIT_ORIGINAL_CREATE,
            request_key="visit-composition-001", visit_binding=binding, image_bytes=JPEG_BYTES)
        fresh = contracts.SubmittedOperation(
            id="a" * 32, site=SITE, actor=ACTOR, kind=request.kind,
            request_key=request.request_key, payload_digest="a" * 64, request=request,
        )
        prepared = contracts.PreparedOperation(
            id=fresh.id, site=SITE, actor=ACTOR, kind=fresh.kind, payload_digest=fresh.payload_digest,
            key="/private/files/clinic-photo-test.jpg", generation=1, draft=None,
            expected_version=None, synthetic_patient=None,
            target_identity=contracts.visit_target_identity(binding), expected_sha256=JPEG_SHA256,
            expected_size=len(JPEG_BYTES), state="PREPARED", visit_binding=binding,
        )
        events = []
        owner, published = Mock(), object()
        owner.continue_after_own_preparation.side_effect = lambda p: events.append("continue")
        @contextlib.contextmanager
        def hold(*args):
            events.append("hold")
            yield owner
        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            fake.db.transaction_writes = 0
            from clinic_photos.visit import capture
            with patch.object(exclusion, "target_identity_for_operation", return_value=prepared.target_identity), \
                 patch.object(exclusion, "hold_exclusion", side_effect=hold), \
                 patch.object(journal, "load_submission", return_value=fresh), \
                 patch.object(capture, "check_slot_availability", side_effect=lambda b: events.append("auth")), \
                 patch.object(journal, "prepare", side_effect=lambda f: events.append("prepare") or prepared), \
                 patch.object(coordinator, "_commit", side_effect=lambda: events.append("prepare-commit")) as commit, \
                 patch.object(coordinator, "_publish_prepared_visit",
                              side_effect=lambda *args: events.append("publish") or published) as publish_visit, \
                 patch.object(coordinator, "_readback_visit",
                              side_effect=lambda *args: events.append("readback") or "verified") as readback:
                self.assertEqual(coordinator._execute_private_visit(fresh), "verified")
                self.assertEqual(events, ["hold", "auth", "prepare", "prepare-commit", "continue",
                                          "publish", "readback"])
                readback.side_effect = coordinator.CoordinatorUnknown("unverified")
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._execute_private_visit(fresh)
                publish_visit.reset_mock()
                commit.side_effect = RuntimeError("uncertain preparation commit")
                with self.assertRaises(coordinator.CoordinatorUnknown):
                    coordinator._execute_private_visit(fresh)
                publish_visit.assert_not_called()

    def test_visit_failure_rolls_back_before_commit_and_never_retries_commit(self):
        from unittest.mock import Mock, patch
        from clinic_photos.publication import coordinator

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        prepared = contracts.PreparedOperation(
            id="operation", site=SITE, actor=ACTOR, kind=contracts.VISIT_ORIGINAL_CREATE,
            payload_digest="a" * 64, key="/private/files/clinic-photo-test.jpg", generation=1,
            draft=None, expected_version=None, synthetic_patient=None,
            target_identity=contracts.visit_target_identity(binding), expected_sha256=JPEG_SHA256,
            expected_size=len(JPEG_BYTES), state="PREPARED", visit_binding=binding,
        )
        for failure in ("eligibility", "save", "slot", "commit"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as root, \
                 fake_runtime(sites_path=sites_volume(root)) as fake:
                from clinic_photos.visit import capture
                owner, file_doc, photo = Mock(), Mock(), Mock()
                file_doc.flags = types.SimpleNamespace(ignore_permissions=None)
                if failure == "save":
                    def fail_save(**kwargs):
                        file_doc.flags.ignore_permissions = kwargs["ignore_permissions"]
                        raise RuntimeError("native save veto")
                    file_doc.save.side_effect = fail_save
                photo.name = "PHOTO"
                bound = types.SimpleNamespace(file_name="FILE")
                @contextlib.contextmanager
                def scope(*_args, **_kwargs):
                    yield
                def finish(_prepared, _owner, action):
                    if failure == "commit" and action == "commit":
                        raise RuntimeError("unknown commit")
                with patch.object(coordinator.frappe_context, "owned_publication_transaction", scope), \
                     patch.object(coordinator.frappe_context, "visit_photo_insertion", scope), \
                     patch.object(coordinator.frappe_context, "visit_file_attachment", scope), \
                     patch.object(writer, "_live_owner", return_value=owner), \
                     patch.object(writer, "_require_persisted_prepared"), \
                     patch.object(capture, "check_slot_availability",
                                  side_effect=RuntimeError() if failure == "eligibility" else None), \
                     patch.object(writer, "rollback_visit_transaction") as rollback, \
                     patch.object(coordinator, "_exact_file_document", return_value=file_doc), \
                     patch.object(fake, "get_doc", return_value=photo), \
                     patch.object(writer, "write_first_object", return_value=bound) as write, \
                     patch.object(writer, "domain_linkage"), \
                     patch.object(writer, "consume_persisted_object", return_value="FILE"), \
                     patch.object(capture, "insert_prepared_slot_link",
                                  side_effect=RuntimeError() if failure == "slot" else None), \
                     patch.object(journal, "publish"), \
                     patch.object(writer, "arm_visit_final_commit"), \
                     patch.object(writer, "finish_visit_transaction", side_effect=finish) as ended:
                    with self.assertRaises(coordinator.CoordinatorUnknown):
                        coordinator._publish_prepared_visit(owner, prepared, JPEG_BYTES)
                    owner.poison.assert_called()
                    if failure == "eligibility":
                        rollback.assert_called_once_with(prepared, owner, fake.db)
                        write.assert_not_called()
                        ended.assert_not_called()
                    elif failure in ("save", "slot"):
                        rollback.assert_called_once_with(prepared, owner, fake.db)
                        ended.assert_not_called()
                        self.assertIsNone(file_doc.flags.ignore_permissions)
                    else:
                        ended.assert_called_once_with(prepared, owner, "commit")
                        rollback.assert_not_called()

    def test_visit_sequence_keeps_commit_inside_owner_and_uses_exact_objects(self):
        from unittest.mock import Mock, patch
        from clinic_photos.publication import coordinator

        binding = contracts.VisitBinding(
            appointment="APT", patient="PAT", slot="Before", encounter=None,
            body_area="Arm", viewpoint="Front", appointment_creation="ac",
            patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
        )
        prepared = contracts.PreparedOperation(
            id="operation", site=SITE, actor=ACTOR, kind=contracts.VISIT_ORIGINAL_CREATE,
            payload_digest="a" * 64, key="/private/files/clinic-photo-test.jpg",
            generation=1, draft=None, expected_version=None, synthetic_patient=None,
            target_identity=contracts.visit_target_identity(binding),
            expected_sha256=JPEG_SHA256, expected_size=len(JPEG_BYTES),
            state="PREPARED", visit_binding=binding,
        )
        events = []
        owner = Mock()
        owner.require_continued.side_effect = lambda: events.append("continued")
        file_doc = Mock()
        file_doc.flags = types.SimpleNamespace(ignore_permissions=None)
        bound = types.SimpleNamespace(file_name="FILE")
        photo = Mock()
        photo.name = "PHOTO"
        photo.insert.side_effect = lambda **kw: events.append(("insert", kw))
        file_doc.save.side_effect = lambda **kw: events.append(("save", kw))
        linkage = object()
        outcome = object()

        @contextlib.contextmanager
        def scope(name):
            events.append("enter-" + name)
            yield
            events.append("exit-" + name)

        with tempfile.TemporaryDirectory() as root, fake_runtime(sites_path=sites_volume(root)) as fake:
            from clinic_photos.visit import capture
            with patch.object(coordinator.frappe_context, "owned_publication_transaction",
                              side_effect=lambda token: scope("owner")), \
                 patch.object(coordinator.frappe_context, "visit_photo_insertion",
                              side_effect=lambda *args: scope("photo")), \
                 patch.object(coordinator.frappe_context, "visit_file_attachment",
                              side_effect=lambda *args: scope("attachment")), \
                 patch.object(capture, "check_slot_availability",
                              side_effect=lambda arg: events.append("availability")), \
                 patch.object(writer, "_live_owner", return_value=owner), \
                 patch.object(writer, "_require_persisted_prepared"), \
                 patch.object(coordinator, "_exact_file_document", return_value=file_doc), \
                 patch.object(fake, "get_doc", return_value=photo) as get_doc, \
                 patch.object(writer, "write_first_object",
                              side_effect=lambda *args: events.append("file") or bound), \
                 patch.object(writer, "domain_linkage", return_value=linkage), \
                 patch.object(writer, "consume_persisted_object",
                              side_effect=lambda *args, **kw: events.append("consume") or "FILE"), \
                 patch.object(capture, "insert_prepared_slot_link",
                              side_effect=lambda *args: events.append("slot")), \
                 patch.object(journal, "publish",
                              side_effect=lambda *args: events.append("publish") or outcome), \
                 patch.object(writer, "arm_visit_final_commit",
                              side_effect=lambda *args: events.append("arm")), \
                 patch.object(writer, "finish_visit_transaction",
                              side_effect=lambda *args: events.append(args[2])):
                result = coordinator._publish_prepared_visit(owner, prepared, JPEG_BYTES)
        self.assertIs(result, outcome)
        self.assertEqual(events, [
            "continued", "enter-owner", "availability", "file", "enter-photo",
            ("insert", {"ignore_permissions": True}), "exit-photo", "enter-attachment",
            ("save", {"ignore_permissions": True}), "exit-attachment", "consume", "slot", "publish", "arm",
            "commit", "exit-owner",
        ])
        values = get_doc.call_args.args[0]
        self.assertEqual((values["patient_reference"], values["original_file"],
                          values["original_width"], values["original_height"]), ("PAT", "FILE", 2, 2))
        self.assertEqual((file_doc.attached_to_doctype, file_doc.attached_to_name),
                         ("Clinic Photo", "PHOTO"))


# ---------------------------------------------------------------------------
# The named vertical slice: preparation precedes bytes, publication is atomic
# ---------------------------------------------------------------------------
class PublicationAtomicityTests(unittest.TestCase):
    def annotation_environment(self, root, **overrides):
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake, version=overrides.get("version", 0))
        allow_permissions(fake, **overrides.get("permissions", {}))
        return fake, sites_path

    def test_exact_file_document_is_a_new_native_document(self):
        """The attempt's File must be a *new* native document, not a fetched one.

        Native probe: ``frappe.get_doc({...})`` without ``__islocal`` yields
        ``File.is_new() is False``, so the writer's ``_require_exact_file`` guard
        refuses it (``file_not_new``) and the reservation is never spent.
        """
        with tempfile.TemporaryDirectory() as root:
            self.annotation_environment(root)
            from clinic_photos.publication import coordinator

            prepared = types.SimpleNamespace(
                key="/private/files/clinic-photo-regression-key",
                actor=ACTOR,
            )
            data = b"exact-attempt-bytes"

            file_doc = coordinator._exact_file_document(prepared, data)

            self.assertTrue(file_doc.is_new())
            self.assertFalse(getattr(file_doc, "name", ""))
            self.assertEqual(file_doc.file_url, prepared.key)
            self.assertEqual(file_doc.file_name, "clinic-photo-regression-key")
            self.assertEqual(int(file_doc.is_private), 1)
            self.assertEqual(file_doc.owner, ACTOR)
            self.assertEqual(file_doc.content, data)

    def test_prepare_precedes_bytes_and_publish_is_atomic(self):
        with tempfile.TemporaryDirectory() as root:
            fake, sites_path = self.annotation_environment(root)
            request = annotation_request()
            operation = stage_committed(fake, request)

            # The framework committed SUBMITTED; nothing protected exists yet.
            self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "SUBMITTED")
            self.assertEqual(file_rows(fake, committed=True), {})

            # Observe the private volume at the PREPARED commit boundary itself,
            # without leaving a callback in the entry frame.
            volume_at_prepare = []
            original_commit = fake.db.commit

            def observing_commit():
                if fake.db.commit_calls == 1:
                    volume_at_prepare.append(
                        ("before", sorted(os.listdir(private_root(sites_path))))
                    )
                original_commit()
                if fake.db.commit_calls == 2:
                    volume_at_prepare.append(
                        ("after", sorted(os.listdir(private_root(sites_path))))
                    )

            fake.db.commit = observing_commit
            self.addCleanup(setattr, fake.db, "commit", original_commit)

            outcome = publish(fake, operation)
            fake.db.commit = original_commit

            # Exactly two coordinator-owned commits, no rollback, and the first one
            # carries the reservation *before* any protected byte exists.
            self.assertEqual(fake.db.commit_calls, 3)  # SUBMITTED stage + PREPARED + PUBLISHED
            self.assertEqual(fake.db.rollback_calls, 0)
            prepared_snapshot, published_snapshot = fake.db.commit_snapshots[1:]

            prepared_row = prepared_snapshot[journal.OPERATION_DOCTYPE][operation.id]
            self.assertEqual(prepared_row["state"], "PREPARED")
            self.assertTrue(storage.is_reserved_key(prepared_row["reservation_key"]))
            self.assertEqual(prepared_row["generation"], journal.PREPARED_GENERATION)
            self.assertNotIn("File", prepared_snapshot)
            self.assertNotIn(VERSION_DOCTYPE, prepared_snapshot)
            self.assertEqual(
                prepared_snapshot[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 0
            )
            self.assertIsNone(prepared_snapshot[DRAFT_DOCTYPE][DRAFT]["current_version"])
            # No protected byte exists at the committed PREPARED boundary.
            self.assertEqual(
                volume_at_prepare,
                [("before", []), ("after", [])],
            )

            key = prepared_row["reservation_key"]
            payload_bytes = request.annotation.encode("utf-8")
            self.assertEqual(
                prepared_row["expected_sha256"], hashlib.sha256(payload_bytes).hexdigest()
            )
            self.assertEqual(prepared_row["expected_size"], len(payload_bytes))

            # The atomic publication commit carries File + version + draft + PUBLISHED.
            self.assertEqual(published_snapshot[journal.OPERATION_DOCTYPE][operation.id]["state"], "PUBLISHED")
            published_file = published_snapshot["File"]
            self.assertEqual(len(published_file), 1)
            file_name, file_row = next(iter(published_file.items()))
            self.assertEqual(file_row["file_url"], key)
            self.assertEqual(int(file_row["file_size"]), len(payload_bytes))
            self.assertEqual(file_row["owner"], ACTOR)

            version_name = f"{DRAFT}-v1"
            version_row = published_snapshot[VERSION_DOCTYPE][version_name]
            self.assertEqual(version_row["draft"], DRAFT)
            self.assertEqual(version_row["version_number"], 1)
            self.assertEqual(version_row["annotation_file"], file_name)
            self.assertEqual(version_row["storage_key"], key)
            self.assertEqual(version_row["state"], "committed")

            draft_row = published_snapshot[DRAFT_DOCTYPE][DRAFT]
            self.assertEqual(draft_row["current_version_number"], 1)
            self.assertEqual(draft_row["current_version"], version_name)
            self.assertEqual(draft_row["author"], ACTOR)

            # Actual bytes: the exact canonical annotation text, once.
            path = target_path(sites_path, key)
            with open(path, "rb") as handle:
                self.assertEqual(handle.read(), payload_bytes)
            info = os.stat(path, follow_symlinks=False)
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(info.st_nlink, 1)
            self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
            self.assertEqual(os.listdir(private_root(sites_path)), [key.rsplit("/", 1)[-1]])

            self.assertEqual(outcome.status, "saved")
            self.assertEqual(outcome.operation_id, operation.id)
            self.assertEqual(outcome.version, 1)
            self.assertEqual(outcome.file_name, file_name)

    def test_published_annotation_file_is_attached_to_its_new_version(self):
        """Native private File read needs the attachment; it names this version only."""
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path = self.annotation_environment(root)
            operation = stage_committed(fake, annotation_request())

            outcome = publish(fake, operation)

            self.assertEqual(outcome.status, "saved")
            published_snapshot = fake.db.commit_snapshots[-1]
            file_name, file_row = next(iter(published_snapshot["File"].items()))
            version_name = published_snapshot[DRAFT_DOCTYPE][DRAFT]["current_version"]
            self.assertEqual(version_name, f"{DRAFT}-v1")
            self.assertEqual(file_row["attached_to_doctype"], VERSION_DOCTYPE)
            self.assertEqual(file_row["attached_to_name"], version_name)
            # The attachment names the immutable version that references these bytes.
            self.assertEqual(
                published_snapshot[VERSION_DOCTYPE][version_name]["annotation_file"],
                file_name,
            )
            self.assertEqual(outcome.file_name, file_name)

    def test_published_original_file_is_attached_to_its_new_photo(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                seed_actor(fake)
                seed_patient(fake)
                allow_permissions(fake)
                operation = stage_committed(fake, synthetic_request())

                outcome = publish(fake, operation)

                self.assertEqual(outcome.status, "saved")
                published_snapshot = fake.db.commit_snapshots[-1]
                photo_name, photo_row = next(iter(published_snapshot[PHOTO_DOCTYPE].items()))
                file_name, file_row = next(iter(published_snapshot["File"].items()))
                self.assertEqual(file_row["attached_to_doctype"], PHOTO_DOCTYPE)
                self.assertEqual(file_row["attached_to_name"], photo_name)
                self.assertEqual(photo_row["original_file"], file_name)
                self.assertEqual(outcome.file_name, file_name)

    def test_sequential_publications_never_rebind_an_already_published_file(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path = self.annotation_environment(root)
            first = publish(fake, stage_committed(fake, annotation_request()))
            self.assertEqual(first.status, "saved")
            first_file, _row = next(iter(fake.db.commit_snapshots[-1]["File"].items()))

            second = publish(
                fake,
                stage_committed(
                    fake,
                    annotation_request(request_key="request-key-0003", expected_version=1),
                ),
            )
            self.assertEqual(second.status, "saved")

            files = fake.db.docs["File"]
            # The earlier publication keeps its own attachment: nothing was rebound.
            self.assertEqual(files[first_file]["attached_to_name"], f"{DRAFT}-v1")
            self.assertEqual(files[first_file]["attached_to_doctype"], VERSION_DOCTYPE)
            later = {name: row for name, row in files.items() if name != first_file}
            self.assertEqual(len(later), 1)
            second_name, second_row = next(iter(later.items()))
            self.assertEqual(second_name, second.file_name)
            self.assertEqual(second_row["attached_to_name"], f"{DRAFT}-v2")

    def test_synthetic_original_publishes_without_draft_or_version_placeholders(self):
        with tempfile.TemporaryDirectory() as root:
            sites_path = sites_volume(root)
            with fake_runtime(sites_path=sites_path) as fake:
                seed_actor(fake)
                seed_patient(fake)
                allow_permissions(fake)
                operation = stage_committed(fake, synthetic_request())

                outcome = publish(fake, operation)

                self.assertEqual(outcome.status, "saved")
                self.assertIsNone(outcome.version)
                published_snapshot = fake.db.commit_snapshots[-1]
                key = published_snapshot[journal.OPERATION_DOCTYPE][operation.id][
                    "reservation_key"
                ]
                self.assertEqual(
                    published_snapshot[journal.OPERATION_DOCTYPE][operation.id]["state"],
                    "PUBLISHED",
                )
                self.assertNotIn(VERSION_DOCTYPE, published_snapshot)
                self.assertNotIn(DRAFT_DOCTYPE, published_snapshot)

                photos = published_snapshot[PHOTO_DOCTYPE]
                self.assertEqual(len(photos), 1)
                photo_name, photo_row = next(iter(photos.items()))
                self.assertEqual(photo_row["patient_reference"], PATIENT)
                self.assertEqual(photo_row["storage_key"], key)
                self.assertEqual(photo_row["original_sha256"], JPEG_SHA256)
                self.assertEqual((photo_row["original_width"], photo_row["original_height"]), (2, 2))

                file_name, file_row = next(iter(published_snapshot["File"].items()))
                self.assertEqual(photo_row["original_file"], file_name)
                self.assertEqual(file_row["file_url"], key)
                self.assertEqual(int(file_row["file_size"]), len(JPEG_BYTES))
                self.assertEqual(outcome.file_name, file_name)

                with open(target_path(sites_path, key), "rb") as handle:
                    self.assertEqual(handle.read(), JPEG_BYTES)

    def test_fresh_version_zero_and_next_version_are_server_derived(self):
        with tempfile.TemporaryDirectory() as root:
            fake, _sites_path = self.annotation_environment(root)
            operation = stage_committed(fake, annotation_request(expected_version=0))

            first = publish(fake, operation)

            self.assertEqual((first.status, first.version), ("saved", 1))
            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 1
            )
            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version"],
                f"{DRAFT}-v1",
            )

            # A second, independent request against the advanced draft publishes 2.
            second_request = annotation_request(
                request_key="request-key-0003", expected_version=1
            )
            second_operation = stage_committed(fake, second_request)
            second = publish(fake, second_operation)

            self.assertEqual((second.status, second.version), ("saved", 2))
            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version"],
                f"{DRAFT}-v2",
            )
            self.assertNotEqual(first.file_name, second.file_name)


# ---------------------------------------------------------------------------
# Definite phase-one rejection: ABORTED, no key, no bytes
# ---------------------------------------------------------------------------
class DefiniteRejectionTests(unittest.TestCase):
    def run_rejected(self, request, *, prepare_fixture, mutate=None):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-reject-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            prepare_fixture(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, request)
            if mutate is not None:
                mutate(fake)
            before = {
                doctype: copy.deepcopy(fake.db.committed.get(doctype, {}))
                for doctype in ("File", VERSION_DOCTYPE, PHOTO_DOCTYPE)
            }
            outcome = publish(fake, operation)

            self.assertEqual(outcome.operation_id, operation.id)
            row = operation_rows(fake, committed=True)[operation.id]
            self.assertEqual(row["state"], "ABORTED")
            self.assertIn(row.get("reservation_key"), (None, ""))
            self.assertIn(row.get("generation"), (None, ""))
            self.assertIn(row.get("expected_sha256"), (None, ""))
            self.assertIn(row.get("expected_size"), (None, 0))
            after = {
                doctype: fake.db.committed.get(doctype, {})
                for doctype in ("File", VERSION_DOCTYPE, PHOTO_DOCTYPE)
            }
            self.assertEqual(after, before)
            self.assertEqual(os.listdir(private_root(sites_path)), [])
            return fake, outcome

    def seed_draft_target(self, fake):
        seed_annotation_target(fake)

    def test_revoked_author_is_aborted_as_rejected_without_a_key(self):
        def revoke(fake):
            fake.db.update_row(DRAFT_DOCTYPE, DRAFT, author=OTHER_ACTOR, owner=OTHER_ACTOR)

        fake, outcome = self.run_rejected(
            annotation_request(), prepare_fixture=self.seed_draft_target, mutate=revoke
        )
        self.assertEqual(outcome.status, "rejected")

    def test_disabled_or_non_clinician_actor_is_aborted_without_a_key(self):
        def disable(fake):
            fake.db.update_row("User", ACTOR, enabled=0)

        _fake, outcome = self.run_rejected(
            annotation_request(), prepare_fixture=self.seed_draft_target, mutate=disable
        )
        self.assertEqual(outcome.status, "rejected")

    def test_native_draft_write_permission_veto_wins_before_bytes(self):
        def deny(fake):
            fake.permissions.discard((DRAFT_DOCTYPE, "write"))

        _fake, outcome = self.run_rejected(
            annotation_request(), prepare_fixture=self.seed_draft_target, mutate=deny
        )
        self.assertEqual(outcome.status, "rejected")

    def test_original_substitution_is_rejected_before_bytes(self):
        def substitute(fake):
            fake.db.update_row(PHOTO_DOCTYPE, PHOTO, patient_reference=PATIENT)
            fake.db.update_row(DRAFT_DOCTYPE, DRAFT, photo="PHOTO-OTHER")

        _fake, outcome = self.run_rejected(
            annotation_request(),
            prepare_fixture=self.seed_draft_target,
            mutate=substitute,
        )
        self.assertEqual(outcome.status, "rejected")

    def test_stale_expected_version_is_a_conflict_before_bytes(self):
        def advance(fake):
            fake.db.update_row(DRAFT_DOCTYPE, DRAFT, current_version_number=2)

        _fake, outcome = self.run_rejected(
            annotation_request(expected_version=0),
            prepare_fixture=self.seed_draft_target,
            mutate=advance,
        )
        self.assertEqual(outcome.status, "conflict")

    def test_synthetic_gates_hard_refuse_outside_the_disposable_site_mode(self):
        scenarios = {
            "synthetic_mode_off": {"conf": {GATE: True, SYNTHETIC_GATE: False}},
            "synthetic_mode_missing": {"conf": {GATE: True}},
            "synthetic_mode_not_true": {"conf": {GATE: True, SYNTHETIC_GATE: 1}},
        }
        for label, overrides in scenarios.items():
            with self.subTest(case=label):
                root = tempfile.mkdtemp(prefix="clinic-coordinator-synthetic-")
                self.addCleanup(
                    lambda root=root: __import__("shutil").rmtree(root, ignore_errors=True)
                )
                sites_path = sites_volume(root)
                with fake_runtime(sites_path=sites_path, **overrides) as fake:
                    seed_actor(fake)
                    seed_patient(fake)
                    allow_permissions(fake)
                    operation = stage_committed(fake, synthetic_request())

                    outcome = publish(fake, operation)

                    self.assertEqual(outcome.status, "rejected")
                    row = operation_rows(fake, committed=True)[operation.id]
                    self.assertEqual(row["state"], "ABORTED")
                    self.assertIn(row.get("reservation_key"), (None, ""))
                    self.assertNotIn("File", fake.db.committed)
                    self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_missing_patient_permission_is_rejected_for_the_synthetic_kind(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-patient-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            allow_permissions(fake, patient_read=False)
            operation = stage_committed(fake, synthetic_request())

            outcome = publish(fake, operation)

            self.assertEqual(outcome.status, "rejected")
            row = operation_rows(fake, committed=True)[operation.id]
            self.assertEqual(row["state"], "ABORTED")
            self.assertIn(row.get("reservation_key"), (None, ""))
            self.assertNotIn("File", fake.db.committed)
            self.assertEqual(os.listdir(private_root(sites_path)), [])


# ---------------------------------------------------------------------------
# Phase-two failures: PREPARED stays the floor, nothing is saved, no retry
# ---------------------------------------------------------------------------
class PublicationFailureTests(unittest.TestCase):
    def run_failure(self, request, *, prepare_fixture, before_phase_two=None, writer_hook=None, commit_failure=False, lost_after_apply_at=None):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-failure-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        prepare_fixture(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, request)

        if before_phase_two is not None:
            # Runs exactly after the coordinator's PREPARED commit, without leaving a
            # callback in the entry frame.
            original_commit = fake.db.commit

            def mutating_commit():
                original_commit()
                if fake.db.commit_calls == 2:
                    before_phase_two(fake)

            fake.db.commit = mutating_commit
            self.addCleanup(setattr, fake.db, "commit", original_commit)

        if writer_hook is not None:
            original = writer.write_first_object
            writer.write_first_object = writer_hook(original)
            self.addCleanup(setattr, writer, "write_first_object", original)

        if commit_failure:
            original_commit = fake.db.commit
            calls = {"n": 0}

            def failing_commit():
                calls["n"] += 1
                if calls["n"] == 2:
                    fake.db.commit_calls += 1
                    raise ConnectionError("simulated lost commit acknowledgement")
                return original_commit()

            fake.db.commit = failing_commit
            self.addCleanup(setattr, fake.db, "commit", original_commit)

        if lost_after_apply_at is not None:
            fake.db.commit_failure_after_apply[lost_after_apply_at] = ConnectionError(
                "simulated commit applied without a returned acknowledgement"
            )

        outcome = publish(fake, operation)
        return fake, sites_path, operation, outcome

    def test_revocation_between_phases_is_unknown_and_never_saved(self):
        def revoke(fake):
            fake.db.update_row("User", ACTOR, enabled=0)

        fake, sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            before_phase_two=revoke,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])
        self.assertEqual(fake.db.rollback_calls, 1)

    def test_stale_version_between_phases_is_unknown_before_bytes(self):
        def advance(fake):
            fake.db.update_row(DRAFT_DOCTYPE, DRAFT, current_version_number=2)

        fake, sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            before_phase_two=advance,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_original_substitution_between_phases_is_unknown_before_bytes(self):
        def substitute(fake):
            fake.db.update_row(PHOTO_DOCTYPE, PHOTO, patient_reference=PATIENT)
            fake.db.update_row(DRAFT_DOCTYPE, DRAFT, photo="PHOTO-OTHER")

        fake, sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            before_phase_two=substitute,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_native_file_create_veto_is_unknown_and_leaves_no_bytes(self):
        def deny_create(original):
            def wrapper(operation, file_doc):
                file_doc.create_permitted = False
                return original(operation, file_doc)

            return wrapper

        fake, sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            writer_hook=deny_create,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_native_veto_after_bytes_preserves_residue_and_prepared_floor(self):
        def fail_validate(original):
            def wrapper(operation, file_doc):
                file_doc.fail_in = "validate"
                return original(operation, file_doc)

            return wrapper

        fake, sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            writer_hook=fail_validate,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertEqual(fake.db.rollback_calls, 1)

        key = operation_rows(fake, committed=True)[operation.id]["reservation_key"]
        path = target_path(sites_path, key)
        self.assertTrue(os.path.exists(path))
        with open(path, "rb") as handle:
            self.assertEqual(handle.read(), annotation_request().annotation.encode("utf-8"))

    def test_linkage_verification_failure_is_unknown_and_never_saves(self):
        """A genuine linkage/reference mismatch, not an unissued-binding type refusal."""
        root = tempfile.mkdtemp(prefix="clinic-coordinator-linkage-real-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, annotation_request())

        from clinic_photos.publication import coordinator

        original_link = coordinator._link_annotation

        def mutate_version_row(prepared, fresh, file_name):
            linkage, version = original_link(prepared, fresh, file_name)
            # The version row the linkage points at no longer names this object's
            # canonical key: the domain-linkage seam itself must refuse.
            fake.db.update_row(
                VERSION_DOCTYPE,
                linkage.name,
                storage_key="/private/files/clinic-annotation-other.json",
            )
            return linkage, version

        coordinator._link_annotation = mutate_version_row
        self.addCleanup(setattr, coordinator, "_link_annotation", original_link)
        try:
            outcome = publish(fake, operation)
        finally:
            coordinator._link_annotation = original_link

        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        # Only the current transaction was discarded: no committed File or version.
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(fake.db.rollback_calls, 1)
        # The bytes created before the refusal remain as protected residue.
        key = operation_rows(fake, committed=True)[operation.id]["reservation_key"]
        self.assertTrue(os.path.exists(target_path(sites_path, key)))

    def test_attachment_metadata_veto_is_unknown_and_keeps_the_prepared_floor(self):
        """A refused native File update after bytes/domain rolls back the transaction."""
        root = tempfile.mkdtemp(prefix="clinic-coordinator-attach-veto-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, annotation_request())

        original_save = ExactFile.save

        def vetoing_save(self, ignore_permissions=None, **kwargs):
            # Native File update veto: the metadata finalization raises before the
            # attachment is persisted.
            self.fail_in = "validate"
            return original_save(self, ignore_permissions=ignore_permissions, **kwargs)

        ExactFile.save = vetoing_save
        self.addCleanup(setattr, ExactFile, "save", original_save)
        try:
            outcome = publish(fake, operation)
        finally:
            ExactFile.save = original_save

        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        # No committed File, version or attachment survives; the attempt rolled back.
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertEqual(fake.db.rollback_calls, 1)
        key = operation_rows(fake, committed=True)[operation.id]["reservation_key"]
        self.assertTrue(os.path.exists(target_path(sites_path, key)))

    def test_partially_persisted_attachment_is_refused_and_rolled_back(self):
        """A File row that does not carry the exact attachment is never published."""
        root = tempfile.mkdtemp(prefix="clinic-coordinator-attach-partial-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, annotation_request())

        original_save = ExactFile.save

        def partial_save(self, ignore_permissions=None, **kwargs):
            result = original_save(self, ignore_permissions=ignore_permissions, **kwargs)
            # A silently incomplete metadata write: the doctype is stored, the record
            # name is lost, so the readback cannot confirm the exact domain record.
            fake.db.update_row("File", self.name, attached_to_name=None)
            return result

        ExactFile.save = partial_save
        self.addCleanup(setattr, ExactFile, "save", original_save)
        try:
            outcome = publish(fake, operation)
        finally:
            ExactFile.save = original_save

        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertEqual(fake.db.rollback_calls, 1)
        key = operation_rows(fake, committed=True)[operation.id]["reservation_key"]
        self.assertTrue(os.path.exists(target_path(sites_path, key)))

    def test_lost_publication_commit_acknowledgement_is_unknown_not_saved(self):
        fake, _sites_path, operation, outcome = self.run_failure(
            annotation_request(),
            prepare_fixture=seed_annotation_target,
            commit_failure=True,
        )
        self.assertEqual(outcome.status, "unknown")
        # The PREPARED transaction committed; the publication transaction did not.
        self.assertEqual(operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED")
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)

    def test_lost_preparation_commit_acknowledgement_is_unknown_not_saved(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-lost-prepare-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, annotation_request())

            original_commit = fake.db.commit
            calls = {"n": 0}

            def failing_commit():
                calls["n"] += 1
                fake.db.commit_calls += 1
                raise ConnectionError("simulated lost commit acknowledgement")

            fake.db.commit = failing_commit
            try:
                outcome = publish(fake, operation)
            finally:
                fake.db.commit = original_commit

            self.assertEqual(outcome.status, "unknown")
            self.assertEqual(calls["n"], 1)
            self.assertEqual(fake.db.commit_calls, 2)
            self.assertNotIn("File", fake.db.committed)
            self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
            self.assertEqual(os.listdir(private_root(sites_path)), [])
            # A blind retry inside the same execution must not resume this attempt.
            with self.assertRaises(exclusion.ExclusionError):
                exclusion.reread_locked_operation(exclusion.require_locked_owner(operation.id))


# ---------------------------------------------------------------------------
# PREPARED is terminal for this execution: no second write, version or repair
# ---------------------------------------------------------------------------
class PreparedRetryTests(unittest.TestCase):
    def test_prepared_retry_never_reenters_writer_or_appends_a_version(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-retry-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, annotation_request())

            original = writer.write_first_object
            calls = {"n": 0}

            def counting(operation_arg, file_doc):
                calls["n"] += 1
                return original(operation_arg, file_doc)

            writer.write_first_object = counting
            from clinic_photos.publication import coordinator

            original_link = coordinator._link_annotation

            def failing_link(*_args, **_kwargs):
                raise RuntimeError("simulated domain linkage failure")

            coordinator._link_annotation = failing_link
            try:
                first = publish(fake, operation)
            finally:
                coordinator._link_annotation = original_link
                writer.write_first_object = original
            self.assertEqual(first.status, "unknown")
            self.assertEqual(calls["n"], 1)

            committed_before = copy.deepcopy(fake.db.committed)
            bytes_before = sorted(os.listdir(private_root(sites_path)))
            commits_before = fake.db.commit_calls

            def forbidden(*_args, **_kwargs):
                raise AssertionError("a PREPARED retry must never re-enter the writer")

            writer.write_first_object = forbidden
            try:
                second = publish(fake, operation)
            finally:
                writer.write_first_object = original

            self.assertEqual(second.status, "unknown")
            self.assertEqual(fake.db.committed, committed_before)
            self.assertEqual(sorted(os.listdir(private_root(sites_path))), bytes_before)
            self.assertEqual(fake.db.commit_calls, commits_before)
            self.assertEqual(fake.db.rollback_calls, 1)
            versions = fake.db.committed.get(VERSION_DOCTYPE, {})
            self.assertEqual(versions, {})
            key = committed_before[journal.OPERATION_DOCTYPE][operation.id]["reservation_key"]
            self.assertTrue(storage.is_reserved_key(key))
            self.assertEqual(
                fake.db.committed[journal.OPERATION_DOCTYPE][operation.id]["reservation_key"],
                key,
            )


# ---------------------------------------------------------------------------
# Entry authority: only the fixed child may publish
# ---------------------------------------------------------------------------
class EntryAuthorityTests(unittest.TestCase):
    def test_direct_request_or_job_call_cannot_publish(self):
        from clinic_photos.publication import coordinator

        root = tempfile.mkdtemp(prefix="clinic-coordinator-entry-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, annotation_request())
            before = copy.deepcopy(fake.db.committed)
            commits = fake.db.commit_calls

            with self.assertRaises(coordinator.CoordinatorUnavailable):
                coordinator.publish_submission(operation)

            self.assertEqual(fake.db.committed, before)
            self.assertEqual(fake.db.commit_calls, commits)
            self.assertEqual(os.listdir(private_root(sites_path)), [])
            # The refusal cannot burn the operation: it is still runnable.
            self.assertEqual(
                fake.db.committed[journal.OPERATION_DOCTYPE][operation.id]["state"],
                "SUBMITTED",
            )

    def test_site_allowlist_and_enabled_gate_are_rechecked(self):
        from clinic_photos.publication import coordinator

        for label, site, conf in (
            ("other_site", "other.example", {GATE: True}),
            ("gate_off", SITE, {GATE: False}),
            ("gate_not_true", SITE, {GATE: 1}),
        ):
            with self.subTest(case=label):
                root = tempfile.mkdtemp(prefix="clinic-coordinator-gate-")
                self.addCleanup(
                    lambda root=root: __import__("shutil").rmtree(root, ignore_errors=True)
                )
                sites_path = sites_volume(root)
                with fake_runtime(sites_path=sites_path, site=SITE, conf=conf) as fake:
                    seed_actor(fake)
                    seed_patient(fake)
                    seed_annotation_target(fake)
                    allow_permissions(fake)
                    operation = stage_committed(fake, annotation_request())
                    if label == "other_site":
                        fake.local.site = site
                    with coordinator.child_process_publication():
                        with self.assertRaises(coordinator.CoordinatorUnavailable):
                            coordinator.publish_submission(operation)
                    self.assertEqual(
                        fake.db.committed[journal.OPERATION_DOCTYPE][operation.id]["state"],
                        "SUBMITTED",
                    )

    def test_process_child_entry_runs_publication_under_the_child_marker(self):
        from clinic_photos.publication import coordinator

        root = tempfile.mkdtemp(prefix="clinic-coordinator-process-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        request = annotation_request()
        record = {
            "name": "a" * 32,
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
            "synthetic_patient": None,
            "annotation_json": request.annotation,
            "image_base64": None,
            "reservation_key": None,
        }

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            fake.db.add(
                journal.OPERATION_DOCTYPE,
                record["name"],
                **{key: value for key, value in record.items() if key != "name"},
            )
            fake.db.committed = copy.deepcopy(fake.db.docs)

            recorded = {}
            original_publish = coordinator.publish_submission

            def recording_publish(submission):
                import frappe

                token = getattr(frappe.local, coordinator.CHILD_MARKER_ATTR, None)
                recorded["token"] = token
                recorded["registered"] = (
                    coordinator._ENTRY_TOKENS.get(id(token)) is token
                    if token is not None
                    else False
                )
                recorded["submission"] = submission
                return contracts.SaveOutcome("saved", submission.id)

            coordinator.publish_submission = recording_publish
            try:
                code = process.run(record["name"], site=SITE)
            finally:
                coordinator.publish_submission = original_publish

            self.assertEqual(code, process.EXIT_COMPLETED)
            # The scope hands the coordinator a process-bound capability, not a flag.
            self.assertIs(type(recorded.get("token")), coordinator._ChildEntry)
            self.assertTrue(recorded.get("registered"))
            self.assertEqual(recorded["submission"].id, record["name"])

    def test_coordinator_import_is_inert(self):
        import subprocess

        script = (
            "import sys; sys.modules['frappe'] = None; "
            "import clinic_photos.publication.coordinator as coordinator; "
            "print(coordinator.__name__)"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(APP_ROOT),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "clinic_photos.publication.coordinator")
        self.assertEqual(completed.stderr, "")


# ---------------------------------------------------------------------------
# Owner continuation and the narrow linkage/receipt extensions
# ---------------------------------------------------------------------------
class OwnerContinuationTests(unittest.TestCase):
    def test_same_live_owner_continues_prepared_but_a_new_attempt_never_does(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-owner-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)

            with exclusion.hold_exclusion(submission.id, target) as owner:
                prepared = journal.prepare(submission)
                fake.db.commit()
                # The same live owner continues over its own committed evidence.
                state = owner.continue_after_own_preparation(prepared)
                self.assertEqual(state.state, "PREPARED")
                self.assertEqual(state.operation_id, submission.id)
                self.assertEqual(state.target_identity, target)
                # A mismatched evidence value is still refused and poisons.
                other = contracts.PreparedOperation(
                    id=prepared.id,
                    site=prepared.site,
                    actor=OTHER_ACTOR,
                    kind=prepared.kind,
                    payload_digest=prepared.payload_digest,
                    key=prepared.key,
                    generation=prepared.generation,
                    draft=prepared.draft,
                    expected_version=prepared.expected_version,
                    synthetic_patient=prepared.synthetic_patient,
                    target_identity=prepared.target_identity,
                    expected_sha256=prepared.expected_sha256,
                    expected_size=prepared.expected_size,
                    state=prepared.state,
                )
                with self.assertRaises(exclusion.ExclusionDenied):
                    owner.continue_after_own_preparation(other)
                self.assertTrue(owner.poisoned)

            # A new attempt over the committed PREPARED row is refused at entry.
            with self.assertRaises(exclusion.ExclusionNoRetry):
                with exclusion.hold_exclusion(submission.id, target):
                    self.fail("PREPARED must never re-enter a fresh attempt")

    def test_fresh_attempt_read_spends_the_token_before_any_continuation(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-owner-read-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)
            with exclusion.hold_exclusion(submission.id, target) as owner:
                prepared = journal.prepare(submission)
                fake.db.commit()
                # The fresh-attempt reader refuses PREPARED and permanently spends
                # this token, so a later continuation cannot re-open the attempt.
                with self.assertRaises(exclusion.ExclusionNoRetry):
                    exclusion.reread_locked_operation(owner)
                with self.assertRaises(exclusion.ExclusionNoRetry):
                    owner.continue_after_own_preparation(prepared)

    def test_continuation_requires_matching_generation_and_state(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-generation-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)
            with exclusion.hold_exclusion(submission.id, target) as owner:
                prepared = journal.prepare(submission)
                fake.db.commit()
                tampered = contracts.PreparedOperation(
                    id=prepared.id,
                    site=prepared.site,
                    actor=prepared.actor,
                    kind=prepared.kind,
                    payload_digest=prepared.payload_digest,
                    key=prepared.key,
                    generation=prepared.generation + 1,
                    draft=prepared.draft,
                    expected_version=prepared.expected_version,
                    synthetic_patient=prepared.synthetic_patient,
                    target_identity=prepared.target_identity,
                    expected_sha256=prepared.expected_sha256,
                    expected_size=prepared.expected_size,
                    state=prepared.state,
                )
                with self.assertRaises(exclusion.ExclusionDenied):
                    owner.continue_after_own_preparation(tampered)
                self.assertTrue(owner.poisoned)


class JournalPublishTests(unittest.TestCase):
    def test_publish_stages_published_only_under_the_same_owner(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-publish-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)
            with exclusion.hold_exclusion(submission.id, target):
                prepared = journal.prepare(submission)
                # No publication before this attempt's own preparation commit: the
                # locked read refuses the fresh-attempt continuation.
                with self.assertRaises(exclusion.ExclusionError):
                    journal.publish(prepared, None, "unused", None)
                self.assertEqual(
                    operation_rows(fake)[submission.id]["state"], "PREPARED"
                )
                self.assertEqual(fake.db.commit_calls, 0)

    def prepared_scope(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        submission = journal.stage_submission(
            annotation_request(), actor=ACTOR, site=SITE
        )
        target = exclusion.target_identity_for_operation(submission.id)
        owner = self.enterContext(exclusion.hold_exclusion(submission.id, target))
        prepared = journal.prepare(submission)
        fake.db.commit()
        owner.continue_after_own_preparation(prepared)
        return fake, prepared, owner

    def test_publish_refuses_a_field_equal_receipt(self):
        fake, prepared, _owner = self.prepared_scope("clinic-coordinator-receipt-")
        forged = contracts.CreationReceipt(
            operation_id=prepared.id,
            generation=prepared.generation,
            key=prepared.key,
            device=1,
            inode=1,
            size=prepared.expected_size,
            sha256=prepared.expected_sha256,
        )
        with self.assertRaises(journal.JournalIntegrityError):
            journal.publish(prepared, forged, "FILE-1", 1)
        row = operation_rows(fake)[prepared.id]
        self.assertEqual(row["state"], "PREPARED")
        self.assertIn(row.get("published_file"), (None, ""))

    def test_publish_refuses_a_written_but_unconsumed_object(self):
        fake, prepared, _owner = self.prepared_scope("clinic-coordinator-unconsumed-")
        document = ExactFile(
            file_url=prepared.key,
            file_name=prepared.key.rsplit("/", 1)[-1],
            content=annotation_request().annotation.encode("utf-8"),
            db=fake.db,
        )
        bound = writer.write_first_object(prepared, document)
        with self.assertRaises(journal.JournalIntegrityError):
            journal.publish(prepared, bound, bound.file_name, 1)
        row = operation_rows(fake)[prepared.id]
        self.assertEqual(row["state"], "PREPARED")
        self.assertIn(row.get("published_file"), (None, ""))


class LinkageExtensionTests(unittest.TestCase):
    def linkage_environment(self, prefix):
        """A bound first object plus the committed domain row it links to."""
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        submission = journal.stage_submission(
            annotation_request(), actor=ACTOR, site=SITE
        )
        target = exclusion.target_identity_for_operation(submission.id)
        owner = self.enterContext(exclusion.hold_exclusion(submission.id, target))
        prepared = journal.prepare(submission)
        fake.db.commit()
        owner.continue_after_own_preparation(prepared)
        basename = prepared.key.rsplit("/", 1)[-1]
        document = ExactFile(
            file_url=prepared.key,
            file_name=basename,
            content=annotation_request().annotation.encode("utf-8"),
            db=fake.db,
        )
        bound = writer.write_first_object(prepared, document)
        fake.db.add(
            VERSION_DOCTYPE,
            f"{DRAFT}-v1",
            draft=DRAFT,
            version_number=1,
            annotation_file=document.name,
            storage_key=prepared.key,
            state="committed",
        )
        linkage = writer.domain_linkage(
            prepared, VERSION_DOCTYPE, f"{DRAFT}-v1", document.name
        )
        return fake, prepared, bound, document, linkage

    def test_consume_accepts_only_the_exact_expected_domain_linkage(self):
        """The receipt update must not accept arbitrary or unrelated references."""
        with self.subTest(case="missing_linkage"):
            _fake, prepared, bound, _document, _linkage = self.linkage_environment(
                "clinic-coordinator-linkage-"
            )
            with self.assertRaises(writer.FirstObjectError):
                writer.consume_persisted_object(prepared, bound)

        # An unrelated extra reference is refused as well.
        with self.subTest(case="unrelated_reference"):
            fake, prepared, bound, _document, linkage = self.linkage_environment(
                "clinic-coordinator-linkage-extra-"
            )
            fake.db.add(
                VERSION_DOCTYPE,
                f"{DRAFT}-v9",
                draft=DRAFT,
                version_number=9,
                storage_key=prepared.key,
                state="pending",
            )
            with self.assertRaises(writer.FirstObjectError):
                writer.consume_persisted_object(
                    prepared, bound, domain_linkage=linkage
                )

    def test_domain_linkage_rejects_wrong_doctype_key_or_draft(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-linkage-kind-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)

        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)
            with exclusion.hold_exclusion(submission.id, target) as owner:
                prepared = journal.prepare(submission)
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                with self.assertRaises(writer.FirstObjectError):
                    writer.domain_linkage(prepared, PHOTO_DOCTYPE, PHOTO, "FILE-1")
                with self.assertRaises(writer.FirstObjectError):
                    writer.domain_linkage(prepared, "File", "FILE-1", "FILE-1")
                fake.db.add(
                    VERSION_DOCTYPE,
                    f"{DRAFT}-v1",
                    draft="OTHER-DRAFT",
                    version_number=1,
                    annotation_file="FILE-1",
                    storage_key=prepared.key,
                    state="committed",
                )
                with self.assertRaises(writer.FirstObjectError):
                    writer.domain_linkage(
                        prepared, VERSION_DOCTYPE, f"{DRAFT}-v1", "FILE-1"
                    )


class PublicationOutcomeEvidenceTests(unittest.TestCase):
    """Durable File/domain/version/creation evidence persisted with PUBLISHED."""

    def test_published_row_carries_file_domain_version_and_creation_identity(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-outcome-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, annotation_request())

            outcome = publish(fake, operation)
            self.assertEqual(outcome.status, "saved")

            row = operation_rows(fake, committed=True)[operation.id]
            key = row["reservation_key"]
            self.assertEqual(row["published_file"], outcome.file_name)
            self.assertEqual(row["published_domain_doctype"], VERSION_DOCTYPE)
            self.assertEqual(row["published_domain_name"], f"{DRAFT}-v1")
            self.assertEqual(row["published_version"], 1)
            identity = row["creation_identity"]
            self.assertRegex(identity, r"^[0-9]+:[0-9]+$")
            info = os.stat(target_path(sites_path, key), follow_symlinks=False)
            self.assertEqual(identity, f"{info.st_dev}:{info.st_ino}")

    def test_synthetic_published_row_carries_original_domain_without_a_version(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-outcome-syn-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, synthetic_request())

            outcome = publish(fake, operation)
            self.assertEqual(outcome.status, "saved")

            row = operation_rows(fake, committed=True)[operation.id]
            self.assertEqual(row["published_file"], outcome.file_name)
            self.assertEqual(row["published_domain_doctype"], PHOTO_DOCTYPE)
            photo = fake.db.committed[PHOTO_DOCTYPE][row["published_domain_name"]]
            self.assertEqual(photo["original_file"], outcome.file_name)
            self.assertIn(row.get("published_version"), (None, 0, ""))
            self.assertRegex(row["creation_identity"], r"^[0-9]+:[0-9]+$")

    def test_synthetic_publication_stages_a_not_null_version_value(self):
        """The installed ``published_version`` column is ``NOT NULL DEFAULT 0``.

        A strict-schema-aware ``set_value`` refuses ``None`` exactly as the live
        integer column does (``MySQLdb.IntegrityError``), so the synthetic kind's
        absent version is observable: the staged row must carry ``0`` rather than
        ``None``, and the published outcome still reports no version.
        """
        root = tempfile.mkdtemp(prefix="clinic-coordinator-outcome-notnull-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            allow_permissions(fake)
            lax_set_value = fake.db.set_value

            class NotNullRefused(Exception):
                """Stand-in for the live driver's ``IntegrityError``."""

            def strict_set_value(doctype, name, field=_UNSET, value=_UNSET):
                values = dict(field) if isinstance(field, dict) else {field: value}
                if (
                    doctype == journal.OPERATION_DOCTYPE
                    and values.get("published_version", 0) is None
                ):
                    raise NotNullRefused("Column 'published_version' cannot be null")
                return lax_set_value(doctype, name, field, value)

            fake.db.set_value = strict_set_value
            operation = stage_committed(fake, synthetic_request())

            outcome = publish(fake, operation)

            self.assertEqual(outcome.status, "saved")
            self.assertIsNone(outcome.version)
            row = operation_rows(fake, committed=True)[operation.id]
            self.assertEqual(row["published_version"], 0)
            self.assertIsNone(journal.load_published(operation.id).version)

    def test_load_published_refuses_missing_or_tampered_outcome_fields(self):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-outcome-tamper-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            operation = stage_committed(fake, annotation_request())
            self.assertEqual(publish(fake, operation).status, "saved")

            published = journal.load_published(operation.id)
            self.assertEqual(published.file_name, fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]["published_file"])

            tampering = {
                "missing_file": {"published_file": None},
                "wrong_domain_doctype": {"published_domain_doctype": PHOTO_DOCTYPE},
                "wrong_domain_name": {"published_domain_name": f"{DRAFT}-v9"},
                "wrong_version": {"published_version": 9},
                "malformed_identity": {"creation_identity": "not-a-device-inode"},
                "empty_identity": {"creation_identity": None},
            }
            for label, changes in tampering.items():
                with self.subTest(case=label):
                    for doctype, name, values in (
                        (journal.OPERATION_DOCTYPE, operation.id, changes),
                    ):
                        original = {
                            field: fake.db.docs[doctype][name].get(field)
                            for field in changes
                        }
                        fake.db.docs[doctype][name].update(values)
                    try:
                        with self.assertRaises(journal.JournalError):
                            journal.load_published(operation.id)
                    finally:
                        fake.db.docs[doctype][name].update(original)

    def test_publication_without_a_consumed_object_is_refused(self):
        """A field-equal CreationReceipt is not this publication's consumed evidence."""
        root = tempfile.mkdtemp(prefix="clinic-coordinator-consumed-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        with fake_runtime(sites_path=sites_path) as fake:
            seed_actor(fake)
            seed_patient(fake)
            seed_annotation_target(fake)
            allow_permissions(fake)
            submission = journal.stage_submission(
                annotation_request(), actor=ACTOR, site=SITE
            )
            target = exclusion.target_identity_for_operation(submission.id)
            with exclusion.hold_exclusion(submission.id, target) as owner:
                prepared = journal.prepare(submission)
                fake.db.commit()
                owner.continue_after_own_preparation(prepared)
                forged = contracts.CreationReceipt(
                    operation_id=prepared.id,
                    generation=prepared.generation,
                    key=prepared.key,
                    device=0,
                    inode=0,
                    size=prepared.expected_size,
                    sha256=prepared.expected_sha256,
                )
                with self.assertRaises((journal.JournalError, writer.FirstObjectError)):
                    journal.publish(prepared, forged, "FILE-NEVER-WRITTEN", 1)

                row = operation_rows(fake)[submission.id]
                self.assertEqual(row["state"], "PREPARED")
                self.assertIn(row.get("published_file"), (None, ""))
                self.assertNotIn("File", fake.db.docs)


class PublicationCommitBoundaryTests(unittest.TestCase):
    """Commit-applied vs commit-not-applied for both coordinator commits."""

    def environment(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        return fake, sites_path, stage_committed(fake, annotation_request())

    def test_preparation_commit_applied_then_raised_keeps_the_committed_floor(self):
        fake, sites_path, operation = self.environment("clinic-coordinator-amb-prep-")
        fake.db.commit_failure_after_apply[2] = ConnectionError(
            "simulated commit applied without an acknowledgement"
        )

        outcome = publish(fake, operation)

        self.assertEqual(outcome.status, "unknown")
        # As actually observed: the row advanced, so the floor is durable.
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])
        self.assertEqual(fake.db.rollback_calls, 0)

    def test_preparation_commit_not_applied_and_raised_leaves_no_prepared_row(self):
        fake, sites_path, operation = self.environment("clinic-coordinator-noc-prep-")
        original_commit = fake.db.commit
        calls = {"n": 0}

        def failing_commit():
            calls["n"] += 1
            if calls["n"] == 1:
                fake.db.commit_calls += 1
                raise ConnectionError("simulated failed commit")
            return original_commit()

        fake.db.commit = failing_commit
        self.addCleanup(setattr, fake.db, "commit", original_commit)

        outcome = publish(fake, operation)

        self.assertEqual(outcome.status, "unknown")
        # Nothing was applied: the durable row is still SUBMITTED, not PREPARED.
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "SUBMITTED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_publication_commit_applied_then_raised_is_unknown_with_committed_bytes(self):
        fake, sites_path, operation = self.environment("clinic-coordinator-amb-pub-")
        fake.db.commit_failure_after_apply[3] = ConnectionError(
            "simulated publication commit applied without an acknowledgement"
        )

        outcome = publish(fake, operation)

        self.assertEqual(outcome.status, "unknown")
        # The publication commit did apply: the durable outcome is PUBLISHED with bytes,
        # and the coordinator never rewrites, unlinks or retries it.
        row = operation_rows(fake, committed=True)[operation.id]
        self.assertEqual(row["state"], "PUBLISHED")
        self.assertEqual(row["published_version"], 1)
        self.assertEqual(len(fake.db.committed["File"]), 1)
        self.assertEqual(
            sorted(os.listdir(private_root(sites_path))),
            [row["reservation_key"].rsplit("/", 1)[-1]],
        )
        self.assertEqual(fake.db.rollback_calls, 0)
        self.assertEqual(fake.db.commit_calls, 3)


class PhaseAuthorityTests(unittest.TestCase):
    """Between-phase native permission revocation and post-byte hook vetoes."""

    def revoke_between_phases(self, mutate, request=None, prepare_fixture=None):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-phase2-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        (prepare_fixture or seed_annotation_target)(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, request or annotation_request())

        first_commit = fake.db

        def once():
            once.calls += 1
            if once.calls == 1:
                mutate(fake)

        once.calls = 0
        original_commit = fake.db.commit

        def mutating_commit():
            original_commit()
            if first_commit.commit_calls == 2:
                once()

        fake.db.commit = mutating_commit
        self.addCleanup(setattr, fake.db, "commit", original_commit)

        outcome = publish(fake, operation)
        return fake, sites_path, operation, outcome

    def test_native_draft_write_revocation_between_phases_writes_no_bytes(self):
        fake, sites_path, operation, outcome = self.revoke_between_phases(
            lambda fake: fake.permissions.discard((DRAFT_DOCTYPE, "write"))
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_native_photo_read_revocation_between_phases_writes_no_bytes(self):
        fake, sites_path, operation, outcome = self.revoke_between_phases(
            lambda fake: fake.permissions.discard((PHOTO_DOCTYPE, "read"))
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_native_patient_read_revocation_between_phases_writes_no_bytes(self):
        fake, sites_path, operation, outcome = self.revoke_between_phases(
            lambda fake: fake.permissions.discard(("Patient", "read")),
            request=synthetic_request(),
            prepare_fixture=seed_patient,
        )
        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(PHOTO_DOCTYPE, fake.db.committed)
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_after_insert_veto_after_bytes_preserves_residue_and_floor(self):
        def fail_after_insert(original):
            def wrapper(operation, file_doc):
                file_doc.fail_in = "after_insert"
                return original(operation, file_doc)

            return wrapper

        root = tempfile.mkdtemp(prefix="clinic-coordinator-after-insert-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, annotation_request())

        original = writer.write_first_object
        writer.write_first_object = fail_after_insert(original)
        self.addCleanup(setattr, writer, "write_first_object", original)
        try:
            outcome = publish(fake, operation)
        finally:
            writer.write_first_object = original

        self.assertEqual(outcome.status, "unknown")
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "PREPARED"
        )
        self.assertNotIn("File", fake.db.committed)
        self.assertNotIn(VERSION_DOCTYPE, fake.db.committed)
        self.assertEqual(fake.db.rollback_calls, 1)
        key = operation_rows(fake, committed=True)[operation.id]["reservation_key"]
        self.assertTrue(os.path.exists(target_path(sites_path, key)))


class PublicationReadbackTests(unittest.TestCase):
    """`saved` is withheld when the committed evidence is not coherent."""

    def publish_with_post_commit_tamper(self, tamper):
        root = tempfile.mkdtemp(prefix="clinic-coordinator-readback-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        operation = stage_committed(fake, annotation_request())

        original_commit = fake.db.commit

        def tampering_commit():
            original_commit()
            if fake.db.commit_calls == 3:
                tamper(fake, sites_path, operation)

        fake.db.commit = tampering_commit
        self.addCleanup(setattr, fake.db, "commit", original_commit)
        return publish(fake, operation), fake, sites_path, operation

    def test_appended_bytes_withhold_saved(self):
        def tamper(fake, sites_path, operation):
            key = fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]["reservation_key"]
            with open(target_path(sites_path, key), "ab") as handle:
                handle.write(b" ")

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_truncated_bytes_withhold_saved(self):
        def tamper(fake, sites_path, operation):
            key = fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]["reservation_key"]
            with open(target_path(sites_path, key), "r+b") as handle:
                handle.truncate(4)

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_widened_mode_withholds_saved(self):
        def tamper(fake, sites_path, operation):
            key = fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]["reservation_key"]
            os.chmod(target_path(sites_path, key), 0o640)

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_tampered_persisted_outcome_withholds_saved(self):
        def tamper(fake, _sites_path, operation):
            fake.db.docs[journal.OPERATION_DOCTYPE][operation.id]["published_file"] = (
                "FILE-OTHER"
            )

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_truncated_file_row_withholds_saved(self):
        def tamper(fake, _sites_path, _operation):
            name = next(iter(fake.db.docs["File"]))
            fake.db.docs["File"][name]["file_size"] = 1

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_corrupt_version_row_withholds_saved(self):
        def tamper(fake, _sites_path, _operation):
            fake.db.docs[VERSION_DOCTYPE][f"{DRAFT}-v1"]["annotation_file"] = "FILE-OTHER"

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")

    def test_corrupt_draft_pointer_withholds_saved(self):
        def tamper(fake, _sites_path, _operation):
            fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"] = 0

        outcome, _fake, _sites_path, _operation = self.publish_with_post_commit_tamper(
            tamper
        )
        self.assertEqual(outcome.status, "unknown")


class PreparedContinuationBindingTests(unittest.TestCase):
    """Continuation is bound to this attempt's issued preparation and its commit."""

    def prepared_environment(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        submission = journal.stage_submission(
            annotation_request(), actor=ACTOR, site=SITE
        )
        target = exclusion.target_identity_for_operation(submission.id)
        return fake, sites_path, submission, target

    def test_field_equal_forged_preparation_handle_cannot_continue(self):
        fake, _sites_path, submission, target = self.prepared_environment(
            "clinic-coordinator-forged-handle-"
        )
        with exclusion.hold_exclusion(submission.id, target) as owner:
            prepared = journal.prepare(submission)
            fake.db.commit()
            forged = contracts.PreparedOperation(
                id=prepared.id,
                site=prepared.site,
                actor=prepared.actor,
                kind=prepared.kind,
                payload_digest=prepared.payload_digest,
                key=prepared.key,
                generation=prepared.generation,
                draft=prepared.draft,
                expected_version=prepared.expected_version,
                synthetic_patient=prepared.synthetic_patient,
                target_identity=prepared.target_identity,
                expected_sha256=prepared.expected_sha256,
                expected_size=prepared.expected_size,
                state=prepared.state,
            )
            self.assertEqual(forged, prepared)
            with self.assertRaises(exclusion.ExclusionError):
                owner.continue_after_own_preparation(forged)
            self.assertTrue(owner.poisoned)

    def test_staged_uncommitted_preparation_cannot_continue_or_publish(self):
        fake, _sites_path, submission, target = self.prepared_environment(
            "clinic-coordinator-staged-continue-"
        )
        with exclusion.hold_exclusion(submission.id, target) as owner:
            prepared = journal.prepare(submission)
            # The row is only staged in this transaction: no commit was observed.
            with self.assertRaises(exclusion.ExclusionError):
                owner.continue_after_own_preparation(prepared)
            self.assertTrue(owner.poisoned)
            with self.assertRaises(exclusion.ExclusionError):
                journal.publish(prepared, None, "unused", None)


class EntryCapabilityTests(unittest.TestCase):
    """Child entry is a process-bound capability, not an ambient boolean."""

    def environment(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        return fake, sites_path, stage_committed(fake, annotation_request())

    def test_ambient_marker_boolean_is_not_a_child_capability(self):
        from clinic_photos.publication import coordinator

        fake, sites_path, operation = self.environment("clinic-coordinator-marker-")
        import frappe

        object.__setattr__(frappe.local, coordinator.CHILD_MARKER_ATTR, True)
        try:
            with self.assertRaises(coordinator.CoordinatorUnavailable):
                coordinator.publish_submission(operation)
        finally:
            object.__delattr__(frappe.local, coordinator.CHILD_MARKER_ATTR)

        self.assertEqual(fake.db.commit_calls, 1)  # only the SUBMITTED stage
        self.assertEqual(
            operation_rows(fake, committed=True)[operation.id]["state"], "SUBMITTED"
        )
        self.assertEqual(os.listdir(private_root(sites_path)), [])

    def test_entry_rechecks_the_execution_frame_before_staging(self):
        from clinic_photos.publication import coordinator

        scenarios = {
            "request": lambda frappe: object.__setattr__(frappe.local, "request", object()),
            "job": lambda frappe: object.__setattr__(frappe.local, "job", object()),
            "pending_callback": lambda frappe: frappe.db.after_commit.add(lambda: None),
            "uncommitted_write": lambda frappe: setattr(
                frappe.db, "transaction_writes", 1
            ),
        }
        for label, dirty in scenarios.items():
            with self.subTest(case=label):
                fake, sites_path, operation = self.environment(
                    f"clinic-coordinator-frame-{label}-"
                )
                import frappe

                dirty(frappe)
                try:
                    with coordinator.child_process_publication():
                        with self.assertRaises(coordinator.CoordinatorUnavailable):
                            coordinator.publish_submission(operation)
                finally:
                    for attribute in ("request", "job"):
                        if hasattr(frappe.local, attribute):
                            object.__delattr__(frappe.local, attribute)
                    frappe.db.after_commit.reset()
                    frappe.db.transaction_writes = 0

                self.assertEqual(
                    operation_rows(fake, committed=True)[operation.id]["state"],
                    "SUBMITTED",
                )
                self.assertEqual(os.listdir(private_root(sites_path)), [])


class DraftPointerAdvanceTests(unittest.TestCase):
    """The publication +1 goes through the real controller and needs real evidence."""

    def assert_refused_with(self, error, expected_reason):
        """The controller refusal must carry the specific evidence reason."""
        cause = error.__context__
        self.assertIsNotNone(cause, f"refusal lost its reason: {error!r}")
        self.assertIn(expected_reason, str(cause))

    def prepared_owner(self, prefix):
        root = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        sites_path = sites_volume(root)
        fake = self.enterContext(fake_runtime(sites_path=sites_path))
        seed_actor(fake)
        seed_patient(fake)
        seed_annotation_target(fake)
        allow_permissions(fake)
        submission = journal.stage_submission(
            annotation_request(), actor=ACTOR, site=SITE
        )
        target = exclusion.target_identity_for_operation(submission.id)
        return fake, sites_path, submission, target

    def test_controller_refuses_a_noncommitted_version_or_a_foreign_file(self):
        from clinic_photos.access import frappe_context

        scenarios = {
            "pending_version_row": {
                "version_state": "pending",
                "expect": "requires its committed version record",
            },
            "foreign_file_key": {
                "foreign_file": True,
                "expect": "file does not match the operation key",
            },
        }
        for label, options in scenarios.items():
            with self.subTest(case=label):
                fake, _sites_path, submission, target = self.prepared_owner(
                    f"clinic-coordinator-draft-evidence-{label}-"
                )
                with exclusion.hold_exclusion(submission.id, target) as owner:
                    prepared = journal.prepare(submission)
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    with frappe_context.owned_publication_transaction(owner):
                        document = ExactFile(
                            file_url=prepared.key,
                            file_name=prepared.key.rsplit("/", 1)[-1],
                            content=annotation_request().annotation.encode("utf-8"),
                            db=fake.db,
                        )
                        bound = writer.write_first_object(prepared, document)
                        if options.get("foreign_file"):
                            fake.db.update_row(
                                "File",
                                bound.file_name,
                                file_url="/private/files/clinic-annotation-foreign.json",
                            )
                        fake.db.add(
                            VERSION_DOCTYPE,
                            f"{DRAFT}-v1",
                            draft=DRAFT,
                            version_number=1,
                            annotation_file=bound.file_name,
                            storage_key=prepared.key,
                            state=options.get("version_state", "committed"),
                        )
                        with frappe_context.bound_publication(
                            actor=ACTOR,
                            draft=DRAFT,
                            operation="commit",
                            storage_key=prepared.key,
                        ):
                            import frappe

                            draft = frappe.get_doc(DRAFT_DOCTYPE, DRAFT)
                            draft.current_version_number = 1
                            draft.current_version = f"{DRAFT}-v1"
                            try:
                                draft.save(ignore_permissions=True)
                            except BaseException as error:
                                self.assertEqual(
                                    type(error).__name__, "PermissionError", repr(error)
                                )
                                self.assert_refused_with(
                                    error, options["expect"]
                                )
                            else:
                                self.fail(f"{label} must not advance the draft pointer")
                    self.assertEqual(
                        fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 0
                    )

    def test_controller_refuses_a_pointer_advance_without_a_committed_version_row(self):
        from clinic_photos.access import frappe_context

        fake, _sites_path, submission, target = self.prepared_owner(
            "clinic-coordinator-draft-no-version-"
        )
        with exclusion.hold_exclusion(submission.id, target) as owner:
            prepared = journal.prepare(submission)
            fake.db.commit()
            owner.continue_after_own_preparation(prepared)
            with frappe_context.owned_publication_transaction(owner):
                with frappe_context.bound_publication(
                    actor=ACTOR,
                    draft=DRAFT,
                    operation="commit",
                    storage_key=prepared.key,
                ):
                    import frappe

                    draft = frappe.get_doc(DRAFT_DOCTYPE, DRAFT)
                    draft.current_version_number = 1
                    draft.current_version = f"{DRAFT}-v1"
                    try:
                        draft.save(ignore_permissions=True)
                    except BaseException as error:
                        self.assertEqual(
                            type(error).__name__, "PermissionError", repr(error)
                        )
                        self.assertEqual(str(error), "Draft identity is server-owned.")
                        self.assert_refused_with(
                            error, "requires its committed version record"
                        )
                    else:
                        self.fail(
                            "the controller must refuse an advance without a "
                            "committed version record"
                        )

            # Nothing advanced: the persisted pointer is unchanged.
            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 0
            )

    def test_controller_refuses_wrong_context_and_skipped_advances(self):
        from clinic_photos.access import frappe_context

        cases = {
            "other_draft_context": "other_context",
            "skipped_two_versions": "skip_two",
        }
        for label, case in cases.items():
            with self.subTest(case=label):
                fake, _sites_path, submission, target = self.prepared_owner(
                    f"clinic-coordinator-draft-refusals-{label}-"
                )
                with exclusion.hold_exclusion(submission.id, target) as owner:
                    prepared = journal.prepare(submission)
                    fake.db.commit()
                    owner.continue_after_own_preparation(prepared)
                    with frappe_context.owned_publication_transaction(owner):
                        writer.write_first_object(
                            prepared,
                            ExactFile(
                                file_url=prepared.key,
                                file_name=prepared.key.rsplit("/", 1)[-1],
                                content=annotation_request().annotation.encode("utf-8"),
                                db=fake.db,
                            ),
                        )
                        context_draft = (
                            "DRAFT-2" if case == "other_context" else DRAFT
                        )
                        next_version = 2 if case == "skip_two" else 1
                        with frappe_context.bound_publication(
                            actor=ACTOR,
                            draft=context_draft,
                            operation="commit",
                            storage_key=prepared.key,
                        ):
                            import frappe

                            draft = frappe.get_doc(DRAFT_DOCTYPE, DRAFT)
                            draft.current_version_number = next_version
                            draft.current_version = f"{DRAFT}-v{next_version}"
                            try:
                                draft.save(ignore_permissions=True)
                            except BaseException as error:
                                self.assertEqual(
                                    type(error).__name__, "PermissionError", repr(error)
                                )
                                if case == "other_context":
                                    # The identity guard refuses a context bound to
                                    # another draft before the evidence lookup runs.
                                    self.assert_refused_with(
                                        error,
                                        "is server-owned and cannot change",
                                    )
                            else:
                                self.fail(
                                    f"{label} must not advance the draft pointer"
                                )
                    self.assertEqual(
                        fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 0
                    )

    def test_controller_allows_the_advance_with_committed_version_file_and_key(self):
        from clinic_photos.access import frappe_context

        fake, _sites_path, submission, target = self.prepared_owner(
            "clinic-coordinator-draft-real-"
        )
        with exclusion.hold_exclusion(submission.id, target) as owner:
            prepared = journal.prepare(submission)
            fake.db.commit()
            owner.continue_after_own_preparation(prepared)
            basename = prepared.key.rsplit("/", 1)[-1]
            with frappe_context.owned_publication_transaction(owner):
                document = ExactFile(
                    file_url=prepared.key,
                    file_name=basename,
                    content=annotation_request().annotation.encode("utf-8"),
                    db=fake.db,
                )
                bound = writer.write_first_object(prepared, document)
                with frappe_context.bound_publication(
                    actor=ACTOR,
                    draft=DRAFT,
                    operation="pending_insert",
                    storage_key=prepared.key,
                ):
                    import frappe

                    version = frappe.get_doc(
                        {
                            "doctype": VERSION_DOCTYPE,
                            "draft": DRAFT,
                            "version_number": 1,
                            "annotation_file": "",
                            "storage_key": prepared.key,
                            "state": "pending",
                        }
                    )
                    version.insert(ignore_permissions=True)
                with frappe_context.bound_publication(
                    actor=ACTOR,
                    draft=DRAFT,
                    operation="commit",
                    storage_key=prepared.key,
                ):
                    version.state = "committed"
                    version.annotation_file = bound.file_name
                    version.save(ignore_permissions=True)

                    draft = frappe.get_doc(DRAFT_DOCTYPE, DRAFT)
                    draft.current_version_number = 1
                    draft.current_version = version.name
                    draft.save(ignore_permissions=True)

            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version_number"], 1
            )
            self.assertEqual(
                fake.db.docs[DRAFT_DOCTYPE][DRAFT]["current_version"], f"{DRAFT}-v1"
            )


class ImageDimensionEvidenceTests(unittest.TestCase):
    """`image_dimensions` is the persisted original evidence, so it is tested directly."""

    def test_valid_2x2_jpeg_returns_its_real_dimensions(self):
        from clinic_photos.publication import validation

        self.assertEqual(validation.image_dimensions(JPEG_BYTES), (2, 2))

    def test_unreadable_images_are_rejected(self):
        from clinic_photos.publication import validation

        for label, data in (
            ("not_an_image", b"not an image"),
            ("empty", b""),
            ("truncated_jpeg", JPEG_BYTES[:20]),
        ):
            with self.subTest(case=label):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.image_dimensions(data)

    def test_bounds_are_enforced_for_the_declared_axes_and_pixels(self):
        from clinic_photos.publication import validation

        validation.check_image_bounds(1, 1)
        for width, height in (
            (0, 1),
            (1, 0),
            (-1, 1),
            (True, 1),
            (validation.MAX_IMAGE_AXIS + 1, 1),
            (1, validation.MAX_IMAGE_AXIS + 1),
            (validation.MAX_IMAGE_PIXELS // 2 + 1, 2),
        ):
            with self.subTest(width=width, height=height):
                with self.assertRaises(validation.PublicationValidationError):
                    validation.check_image_bounds(width, height)


class NativeCoordinatorChecksPending(unittest.TestCase):
    @unittest.skip(
        "pending native window: the installed Frappe/MariaDB two-phase publication "
        "through python -m clinic_photos.publication.process must be observed with real "
        "transactions, native File permissions, hooks and MariaDB isolation"
    )
    def test_native_two_phase_publication_through_the_real_process(self):
        self.fail("native two-phase publication has not run")

    @unittest.skip(
        "pending native window: a real native File create/validate veto and a lost "
        "publication commit must be observed leaving the committed PREPARED floor"
    )
    def test_native_veto_and_lost_acknowledgement(self):
        self.fail("native veto/lost-acknowledgement check has not run")

    @unittest.skip(
        "pending native permission check: draft author revocation between the two "
        "phases must deny publication through installed permissions"
    )
    def test_native_revocation_between_phases(self):
        self.fail("native revocation check has not run")

    @unittest.skip(
        "pending native permission check: a published private File must be readable by "
        "a non-owner only through its attached_to_doctype/attached_to_name domain "
        "record, and the metadata File.save() on that row must keep the canonical "
        "identity, skip native duplicate retargeting and register no deleting rollback"
    )
    def test_native_attachment_grants_only_the_domain_read(self):
        self.fail("native private File attachment check has not run")


if __name__ == "__main__":
    unittest.main()
