"""Framework-independent and narrow runtime-adapter tests for File protection.

The adapter tests install ``tests.unit.fake_frappe``: a deliberately small
in-memory stand-in implementing only the attributes the runtime adapter calls. They
exercise the real adapter/policy/mixin path (no patched guards) and establish guard
ordering locally. They do NOT prove installed Frappe MRO, DocPerm, MariaDB SQL or
HTTP behaviour; Task 4 owns that.
"""
import contextlib
import sqlite3
import types
import unittest
from unittest import mock
from pathlib import Path

from clinic_photos.access import file_mixin
from clinic_photos.access.file_mixin import ProtectedFileMixin
from clinic_photos.access.lifecycle import PublicationContext
from clinic_photos.access.permissions import (
    PROTECTED_FILE_SQL_CONDITION,
    file_has_permission_for,
    file_metadata_allowed,
    file_query_condition,
    protected_file_condition,
    hex_name_literal,
)
from clinic_photos.access.policy import Actor
from clinic_photos.access.storage import ProtectionRef, classify_file
from fake_frappe import FakePublicationOwner, fake_frappe


APP_ROOT = Path(__file__).resolve().parents[2]

AUTHOR = "author@example.test"
OTHER_AUTHOR = "other@example.test"
DRAFT = "DRAFT-1"
ORIGINAL_KEY = "/private/files/clinic-photo-11111111-1111-1111-1111-111111111111.png"
ANNOTATION_KEY = "/private/files/clinic-annotation-22222222-2222-2222-2222-222222222222.json"
VERSION = "VERSION-1"
PHOTO = "PHOTO-1"


def actor_for(role, user=AUTHOR, enabled=True):
    return Actor(
        user=user,
        enabled=enabled,
        clinician=role in {"Physician", "Nursing User"},
    )


def photo_ref(key=ORIGINAL_KEY, name=PHOTO):
    return ProtectionRef(
        kind="original",
        committed=True,
        doctype="Clinic Photo",
        name=name,
        storage_key=key,
    )


def version_ref(committed=False, key=ANNOTATION_KEY, draft=DRAFT, author=AUTHOR):
    return ProtectionRef(
        kind="annotation",
        author=author,
        committed=committed,
        doctype="Clinic Photo Annotation Version",
        name=VERSION,
        draft=draft,
        storage_key=key,
    )


def lookup_for(mapping, by_name=None):
    def lookup(storage_key, file_name=""):
        found = []
        if storage_key:
            found.extend(mapping.get(storage_key, ()))
        if file_name and by_name:
            found.extend(by_name.get(file_name, ()))
        seen = set()
        unique = []
        for reference in found:
            marker = (reference.doctype, reference.name)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(reference)
        return tuple(unique)

    return lookup


def base_docs(
    *,
    user=AUTHOR,
    roles=("Physician",),
    enabled=1,
    draft_author=AUTHOR,
    photo_file="",
    photo_key=ORIGINAL_KEY,
    version_key=ANNOTATION_KEY,
    version_file="",
    version_state="pending",
    version_author=AUTHOR,
    draft_photo=PHOTO,
):
    docs = {
        "User": {user: {"name": user, "enabled": enabled}},
        "Has Role": {
            f"HR-{index}": {
                "name": f"HR-{index}",
                "parent": user,
                "parenttype": "User",
                "role": role,
            }
            for index, role in enumerate(roles)
        },
        "Clinic Photo Draft": {
            DRAFT: {"name": DRAFT, "author": draft_author, "photo": draft_photo},
        },
        "Clinic Photo": {
            PHOTO: {
                "name": PHOTO,
                "storage_key": photo_key,
                "original_file": photo_file,
            }
        },
        "Clinic Photo Annotation Version": {
            VERSION: {
                "name": VERSION,
                "draft": DRAFT,
                "annotation_file": version_file,
                "storage_key": version_key,
                "state": version_state,
            }
        },
    }
    return docs


def add_file_row(
    frappe,
    name="FILE-1",
    *,
    file_url=ORIGINAL_KEY,
    content_hash="h" * 64,
    file_size=10,
    is_private=1,
    owner=AUTHOR,
    file_name="original.png",
):
    frappe.db.add(
        "File",
        name,
        file_url=file_url,
        content_hash=content_hash,
        file_size=file_size,
        is_private=is_private,
        owner=owner,
        file_name=file_name,
    )
    return frappe.db.docs["File"][name]


def allow_native_read(frappe, file_name="FILE-1", doctype="Clinic Photo"):
    frappe.permissions[("File", "read", file_name)] = True
    frappe.permissions[(doctype, "read", PHOTO if doctype == "Clinic Photo" else VERSION)] = True


class FakeVersion:
    def __init__(self, **values):
        self.values = dict(values)

    def get(self, fieldname):
        return self.values.get(fieldname)


# ---------------------------------------------------------------------------
# Native File stand-in with INSTALLED 16.53 signatures
# ---------------------------------------------------------------------------
class FakeNativeFile:
    def __init__(
        self,
        *,
        name=None,
        file_url=None,
        file_name="",
        is_private=1,
        content=None,
        new=True,
        previous=None,
        content_hash=None,
        file_size=0,
        owner=AUTHOR,
    ):
        self.name = name
        self.file_url = file_url
        self.file_name = file_name
        self.is_private = is_private
        self.content = content
        self.content_hash = content_hash
        self.file_size = file_size
        self.owner = owner
        self._new = new
        self._previous = previous
        self.flags = types.SimpleNamespace(in_insert=False)
        self.calls = []
        self.native_chain = False
        self.fail_after_write = False
        self.fail_after_delete = False
        self.on_native = None
        self.rollback_events = []

    def is_new(self):
        return self._new

    def get_doc_before_save(self):
        return self._previous

    def get_content(self, encodings=None):
        self.calls.append(("get_content", encodings))
        return self.content

    def is_downloadable(self):
        self.calls.append(("is_downloadable",))
        return True

    def before_insert(self):
        self.calls.append(("before_insert",))

    def validate(self):
        self.calls.append(("validate",))

    def save_file(
        self,
        content=None,
        decode=False,
        ignore_existing_file_check=False,
        overwrite=False,
    ):
        self.calls.append(("save_file", content, decode, overwrite))
        if content is not None:
            self.content_hash = "c" * 64
            self.file_size = len(content)
        if self.native_chain:
            # Installed 16.53 order: save_file -> save_file_on_filesystem ->
            # write_file (read-only source excerpt in task-2-native-file-excerpts.txt).
            self.save_file_on_filesystem()
        if self.fail_after_write:
            raise RuntimeError("native write failed after touching the filesystem")
        return {"file_url": self.file_url}

    def write_file(self):
        self.calls.append(("write_file",))
        import frappe

        # Installed native registers its rollback compensation here, after the write.
        frappe.db.after_rollback.add(lambda: self.rollback_events.append("compensation"))
        if self.fail_after_write:
            raise RuntimeError("native write failed after touching the filesystem")
        return "/tmp/fake"

    def save_file_on_filesystem(self):
        self.calls.append(("save_file_on_filesystem",))
        if self.on_native is not None:
            self.on_native(self)
        if self.native_chain:
            self.write_file()
        return {"file_url": self.file_url}

    def handle_is_private_changed(self):
        self.calls.append(("handle_is_private_changed",))

    def set_is_private(self):
        self.calls.append(("set_is_private",))
        if self.file_url:
            self.is_private = int(str(self.file_url).startswith("/private"))

    def make_thumbnail(
        self,
        set_as_thumbnail=True,
        width=300,
        height=300,
        suffix="small",
        crop=False,
    ):
        self.calls.append(("make_thumbnail", suffix))
        return "thumbnail"

    def optimize_file(self):
        self.calls.append(("optimize_file",))

    def delete_file_from_filesystem(self, only_thumbnail=False):
        self.calls.append(("delete_file_from_filesystem", only_thumbnail))
        if self.fail_after_delete:
            raise RuntimeError("native cleanup failed after touching bytes")

    def delete_file_data_content(self, only_thumbnail=False):
        self.calls.append(("delete_file_data_content", only_thumbnail))
        if self.fail_after_delete:
            raise RuntimeError("native cleanup failed after touching bytes")

    def on_trash(self):
        self.calls.append(("on_trash",))
        if self.fail_after_delete:
            raise RuntimeError("native cleanup failed after touching bytes")


class FakeFile(ProtectedFileMixin, FakeNativeFile):
    pass


def protected_doc(frappe, key=ORIGINAL_KEY, **overrides):
    add_file_row(frappe, file_url=key, **overrides)
    allow_native_read(frappe)
    return FakeFile(
        name="FILE-1",
        file_url=key,
        file_name="original.png",
        new=False,
        content_hash="h" * 64,
        file_size=10,
    )


# ---------------------------------------------------------------------------
# Runtime adapter: fresh actor and read authorization
# ---------------------------------------------------------------------------
class RuntimeActorTests(unittest.TestCase):
    def test_roles_and_enabled_status_are_read_freshly(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            self.assertTrue(frappe_context.current_actor().clinician)
            frappe.db.set_value("User", AUTHOR, enabled=0)
            self.assertFalse(frappe_context.current_actor().enabled)
            frappe.db.docs["Has Role"].clear()
            self.assertFalse(frappe_context.current_actor().clinician)

    def test_administrator_and_guest_never_become_clinicians(self):
        from clinic_photos.access import frappe_context

        for user in ("Administrator", "Guest"):
            with self.subTest(user=user):
                with fake_frappe(base_docs(user=user), user=user):
                    actor = frappe_context.current_actor()
                    self.assertFalse(actor.clinician)
                    self.assertFalse(actor.enabled)


class RuntimeReadTests(unittest.TestCase):
    def test_registered_file_requires_native_file_and_feature_read(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            frappe_context.require_protected_read(file_doc)

            for permission in (
                ("File", "read", "FILE-1"),
                ("Clinic Photo", "read", PHOTO),
            ):
                with self.subTest(permission=permission):
                    frappe.permissions[permission] = False
                    with self.assertRaises(frappe.PermissionError):
                        frappe_context.require_protected_read(file_doc)
                    frappe.permissions[permission] = True

    def test_non_clinician_and_disabled_users_are_denied_even_with_native_reads(self):
        from clinic_photos.access import frappe_context

        for docs, user in (
            (base_docs(user=OTHER_AUTHOR, roles=("Healthcare Administrator",)), OTHER_AUTHOR),
            (base_docs(enabled=0), AUTHOR),
        ):
            with self.subTest(user=user):
                with fake_frappe(docs, user=user) as frappe:
                    file_doc = protected_doc(frappe)
                    with self.assertRaises(frappe.PermissionError):
                        frappe_context.require_protected_read(file_doc)

    def test_annotation_file_mutation_is_denied_through_reference_identity(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(
            base_docs(
                photo_file="FILE-1",
                version_file="FILE-ANN",
                version_state="committed",
            )
        ) as frappe:
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                file_name="annotation.json",
            )
            frappe.permissions[("File", "read", "FILE-ANN")] = True
            frappe.permissions[("Clinic Photo Annotation Version", "read", VERSION)] = True
            file_doc = FakeFile(
                name="FILE-ANN",
                file_url="/private/files/unrelated.png",
                file_name="annotation.json",
                new=False,
                content_hash="h" * 64,
                file_size=10,
            )
            file_doc.content = b"annotation-bytes"
            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_protected_read(file_doc)
            self.assertEqual(file_doc.calls, [])

    def test_get_content_runs_before_the_native_in_memory_return(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            file_doc.content = b"protected"
            file_doc.file_url = "/private/files/unrelated.png"
            with self.assertRaises(frappe.PermissionError):
                file_doc.get_content()
            self.assertEqual(file_doc.calls, [])

    def test_unrelated_file_keeps_native_behaviour(self):
        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(file_url="/private/files/scan.png", content=b"plain")
            self.assertEqual(file_doc.get_content(), b"plain")
            self.assertTrue(file_doc.is_downloadable())
            self.assertEqual(
                file_doc.calls,
                [("get_content", None), ("is_downloadable",)],
            )


# ---------------------------------------------------------------------------
# Runtime adapter: side-effect guards use installed signatures
# ---------------------------------------------------------------------------
class NativeWriteGuardTests(unittest.TestCase):
    def test_every_native_write_and_privacy_method_is_denied_first(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            for method in (
                "save_file",
                "write_file",
                "save_file_on_filesystem",
                "handle_is_private_changed",
                "make_thumbnail",
                "optimize_file",
                "delete_file_from_filesystem",
                "delete_file_data_content",
            ):
                with self.subTest(method=method):
                    file_doc = protected_doc(frappe)
                    with self.assertRaises(frappe.PermissionError):
                        getattr(file_doc, method)()
                    self.assertEqual(file_doc.calls, [])

    def test_set_is_private_keeps_the_native_no_argument_signature(self):
        file_doc = FakeFile(file_url=ORIGINAL_KEY, is_private=0)
        # No override: the native no-argument method is inherited and infers
        # privacy from the URL instead of receiving a value.
        file_doc.set_is_private()
        self.assertEqual(file_doc.calls, [("set_is_private",)])
        self.assertEqual(file_doc.is_private, 1)

    def test_unrelated_privacy_and_derivative_methods_delegate(self):
        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(file_url="/private/files/scan.png")
            file_doc.handle_is_private_changed()
            self.assertEqual(file_doc.make_thumbnail(), "thumbnail")
            file_doc.optimize_file()
            self.assertEqual(
                [
                    call[0]
                    for call in file_doc.calls
                ],
                ["handle_is_private_changed", "make_thumbnail", "optimize_file"],
            )


class PendingContentRuntimeTests(unittest.TestCase):
    def pending_file(self, frappe, *, key=ANNOTATION_KEY, name=None, new=True):
        return FakeFile(name=name, file_url=key, file_name="annotation.json", new=new)

    def context(self, frappe, **overrides):
        from clinic_photos.access import frappe_context

        options = dict(
            actor=AUTHOR,
            draft=DRAFT,
            operation="pending_insert",
            storage_key=ANNOTATION_KEY,
        )
        options.update(overrides)
        return _bound(frappe_context, **options)

    def test_bound_clinician_author_may_consume_first_content(self):
        with fake_frappe(base_docs()) as frappe:
            file_doc = self.pending_file(frappe)
            with self.context(frappe):
                result = file_doc.save_file(content=b"bytes")
            self.assertEqual(result, {"file_url": ANNOTATION_KEY})
            self.assertEqual(file_doc.calls[0][0], "save_file")

    def test_context_presence_without_author_or_identity_is_denied(self):
        from clinic_photos.access import frappe_context

        cases = (
            ("no_context", None),
            ("wrong_key", dict(storage_key=ORIGINAL_KEY)),
            ("wrong_draft", dict(draft="other-draft")),
            ("wrong_operation", dict(operation="commit")),
        )
        for label, overrides in cases:
            with self.subTest(label=label):
                with fake_frappe(base_docs()) as frappe:
                    file_doc = self.pending_file(frappe)
                    if overrides is None:
                        with self.assertRaises(frappe.PermissionError):
                            file_doc.save_file(content=b"bytes")
                    else:
                        with self.context(frappe, **overrides):
                            with self.assertRaises(frappe.PermissionError):
                                file_doc.save_file(content=b"bytes")
                    self.assertEqual(file_doc.calls, [])

    def test_different_session_or_author_is_denied(self):
        with fake_frappe(base_docs(draft_author=OTHER_AUTHOR)) as frappe:
            file_doc = self.pending_file(frappe)
            with self.context(frappe):
                with self.assertRaises(frappe.PermissionError):
                    file_doc.save_file(content=b"bytes")
            self.assertEqual(file_doc.calls, [])

    def test_unregistered_reserved_and_existing_bytes_are_denied(self):
        with fake_frappe(base_docs(version_key=ORIGINAL_KEY)) as frappe:
            file_doc = FakeFile(
                file_url="/private/files/clinic-annotation-9999.json",
                file_name="annotation.json",
            )
            with self.context(frappe):
                with self.assertRaises(frappe.PermissionError):
                    file_doc.write_file()
            self.assertEqual(file_doc.calls, [])

        with fake_frappe(base_docs()) as frappe:
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                content_hash="x" * 64,
                file_size=4,
            )
            file_doc = FakeFile(
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                file_name="annotation.json",
                new=False,
                content_hash="x" * 64,
                file_size=4,
            )
            with self.context(frappe):
                with self.assertRaises(frappe.PermissionError):
                    file_doc.save_file(content=b"overwrite")
            self.assertEqual(file_doc.calls, [])

    def test_committed_annotation_reference_denies_content(self):
        with fake_frappe(base_docs(version_state="committed")) as frappe:
            file_doc = self.pending_file(frappe)
            with self.context(frappe):
                with self.assertRaises(frappe.PermissionError):
                    file_doc.save_file(content=b"bytes")
            self.assertEqual(file_doc.calls, [])


# ---------------------------------------------------------------------------
# Runtime adapter: insert/update/delete identity
# ---------------------------------------------------------------------------
class FileLifecycleRuntimeTests(unittest.TestCase):
    def test_same_instance_insert_then_mutated_save_checks_persisted_state(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                name=None,
                file_url=ANNOTATION_KEY,
                file_name="annotation.json",
                new=True,
            )
            file_doc.flags.in_insert = True
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                file_doc.validate()
            self.assertIn(("validate",), file_doc.calls)

            # The same instance is now persisted; the insert flag is cleared.
            file_doc.flags.in_insert = False
            file_doc._new = False
            file_doc.name = "FILE-ANN"
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                content_hash="h" * 64,
            )
            file_doc.file_url = "/private/files/unrelated.png"
            with self.assertRaises(frappe.PermissionError):
                file_doc.validate()

    def test_write_guard_uses_persisted_protection_despite_a_mutated_url(self):
        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            file_doc.file_url = "/private/files/unrelated.png"
            with self.assertRaises(frappe.PermissionError):
                file_doc.write_file()
            self.assertEqual(file_doc.calls, [])

    def test_mutated_save_is_denied_even_while_a_pending_context_remains_bound(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                content_hash="h" * 64,
                file_size=10,
            )
            file_doc = FakeFile(
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                file_name="annotation.json",
                new=False,
                content_hash="h" * 64,
                file_size=10,
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                file_doc.file_url = "/private/files/unrelated.png"
                with self.assertRaises(frappe.PermissionError):
                    file_doc.validate()
            self.assertEqual(file_doc.calls, [])

    def test_reserved_insert_needs_registration_and_the_bound_context(self):
        from clinic_photos.access import frappe_context

        # An unregistered reserved insert is a failed protected registration: it
        # aborts that execution, so the admitted path needs its own execution.
        with fake_frappe(base_docs(version_key=ANNOTATION_KEY)) as frappe:
            file_doc = FakeFile(
                file_url="/private/files/clinic-annotation-9999.json",
                file_name="annotation.json",
            )
            with self.assertRaises(frappe.PermissionError):
                file_doc.before_insert()
            self.assertFalse(frappe_context.publication_transaction_live())

        with fake_frappe(base_docs(version_key=ANNOTATION_KEY)) as frappe:
            registered = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                registered.before_insert()
            self.assertIn(("before_insert",), registered.calls)

    def test_reencoded_protected_url_is_rejected_on_update(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            file_doc.file_url = ORIGINAL_KEY.replace(
                "clinic-photo-", "%63linic-photo-"
            )
            with self.assertRaises(frappe.PermissionError):
                file_doc.validate()
            self.assertEqual(file_doc.calls, [])

    def test_registration_rejects_preexisting_noncanonical_alias(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add(
                "File",
                "ALIAS",
                file_url=ORIGINAL_KEY.replace("clinic-photo-", "%63linic-photo-"),
                file_name="alias.png",
            )
            with self.assertRaises(frappe.ValidationError):
                frappe_context.require_registration_identities_consistent(ORIGINAL_KEY)

    def test_registration_rejects_a_feature_reference_to_another_identity(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-X")) as frappe:
            frappe.db.add(
                "File", "FILE-X", file_url="/private/files/scan.png", file_name="scan.png"
            )
            with self.assertRaises(frappe.ValidationError):
                frappe_context.require_registration_identities_consistent(ORIGINAL_KEY)

    def test_pending_content_rejects_a_reencoded_protected_url(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY.replace(
                    "clinic-annotation-", "%63linic-annotation-"
                ),
                file_name="annotation.json",
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                with self.assertRaises(frappe.PermissionError):
                    file_doc.save_file(content=b"bytes")
            self.assertEqual(file_doc.calls, [])

    def test_registration_accepts_canonical_rows_and_ignores_unrelated_names(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "CANON", file_url=ORIGINAL_KEY, file_name="o.png")
            frappe.db.add(
                "File",
                "UNRELATED",
                file_url="/private/files/scan%20name.png",
                file_name="scan.png",
            )
            frappe_context.require_registration_identities_consistent(ORIGINAL_KEY)

    def test_runtime_create_hook_defers_only_inside_the_bound_context(self):
        from clinic_photos.access import frappe_context, permissions

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            self.assertIs(
                permissions.file_has_permission(file_doc, AUTHOR, "create"),
                False,
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                # Installed has_controller_permissions denies on any falsy return,
                # so a policy deferral must reach it as a truthy "no objection".
                self.assertIs(
                    permissions.file_has_permission(file_doc, AUTHOR, "create"),
                    True,
                )

    def test_native_ptype_create_defers_only_inside_the_bound_context(self):
        from clinic_photos.access import frappe_context, permissions

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            # Installed v16 has_controller_permissions calls the hook as
            # frappe.call(method, doc=doc, ptype=ptype, user=user, debug=debug).
            self.assertIs(
                permissions.file_has_permission(
                    doc=file_doc, ptype="create", user=AUTHOR, debug=False
                ),
                False,
            )
            with _bound(frappe_context, storage_key=ANNOTATION_KEY):
                self.assertIs(
                    permissions.file_has_permission(
                        doc=file_doc, ptype="create", user=AUTHOR, debug=False
                    ),
                    True,
                )
            # A bound context for another key never covers this row.
            with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                self.assertIs(
                    permissions.file_has_permission(
                        doc=file_doc, ptype="create", user=AUTHOR, debug=False
                    ),
                    False,
                )
            # Leaving the exact binding restores the denial.
            self.assertIs(
                permissions.file_has_permission(
                    doc=file_doc, ptype="create", user=AUTHOR, debug=False
                ),
                False,
            )

    def test_native_ptype_read_defers_but_writes_and_missing_ptype_deny(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            self.assertIs(
                permissions.file_has_permission(
                    doc=file_doc, ptype="read", user=AUTHOR, debug=False
                ),
                True,
            )
            self.assertIs(
                permissions.file_has_permission(
                    doc=file_doc, ptype="read", user="Guest", debug=False
                ),
                False,
            )
            for ptype in ("write", "delete", "share", "export", None):
                with self.subTest(ptype=ptype):
                    self.assertIs(
                        permissions.file_has_permission(
                            doc=file_doc, ptype=ptype, user=AUTHOR, debug=False
                        ),
                        False,
                    )

    def test_native_ptype_delete_defers_only_inside_the_cleanup_context(self):
        from clinic_photos.access import frappe_context, permissions

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            self.assertIs(
                permissions.file_has_permission(
                    doc=file_doc, ptype="delete", user=AUTHOR, debug=False
                ),
                False,
            )
            with _bound(
                frappe_context,
                operation="pending_cleanup",
                storage_key=ORIGINAL_KEY,
            ):
                self.assertIs(
                    permissions.file_has_permission(
                        doc=file_doc, ptype="delete", user=AUTHOR, debug=False
                    ),
                    True,
                )

    def test_adapter_continues_the_controller_check_for_unrelated_files(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            unrelated = FakeFile(
                file_url="/private/files/scan.png", file_name="scan.png"
            )
            self.assertIs(
                permissions.file_has_permission(
                    doc=unrelated, ptype="read", user=AUTHOR, debug=False
                ),
                True,
            )
            self.assertIs(
                permissions.file_has_permission(
                    doc=None, ptype="create", user=AUTHOR, debug=False
                ),
                True,
            )

    def test_adapter_denies_unexpected_decisions_and_propagates_exceptions(self):
        from unittest import mock

        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            unrelated = FakeFile(
                file_url="/private/files/scan.png", file_name="scan.png"
            )
            for decision in (True, 1, 0, "read"):
                with self.subTest(decision=decision):
                    with mock.patch.object(
                        permissions,
                        "file_has_permission_for",
                        return_value=decision,
                    ):
                        self.assertIs(
                            permissions.file_has_permission(
                                doc=unrelated,
                                ptype="read",
                                user=AUTHOR,
                                debug=False,
                            ),
                            False,
                        )
            error = RuntimeError("adapter failure must not grant")
            with mock.patch.object(
                permissions, "file_has_permission_for", side_effect=error
            ):
                with self.assertRaises(RuntimeError):
                    permissions.file_has_permission(
                        doc=unrelated, ptype="create", user=AUTHOR, debug=False
                    )

    def test_shared_pending_cleanup_requires_full_binding(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            add_file_row(
                frappe, name="FILE-ANN", file_url=ANNOTATION_KEY, content_hash=""
            )
            file_doc = FakeFile(
                name="FILE-ANN", file_url=ANNOTATION_KEY, new=False
            )
            context = _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_cleanup",
                storage_key=ANNOTATION_KEY,
            )
            with context:
                file_doc.on_trash()
            self.assertIn(("on_trash",), file_doc.calls)

    def test_any_immutable_reference_blocks_cleanup(self):
        from clinic_photos.access import frappe_context

        for docs in (
            base_docs(version_key=ORIGINAL_KEY, photo_file="FILE-1", version_file="FILE-1"),
            base_docs(version_key=ANNOTATION_KEY, version_state="committed"),
        ):
            with self.subTest(docs=list(docs["Clinic Photo Annotation Version"].values())):
                with fake_frappe(docs) as frappe:
                    key = (
                        ORIGINAL_KEY
                        if docs["Clinic Photo Annotation Version"][VERSION]["storage_key"]
                        == ORIGINAL_KEY
                        else ANNOTATION_KEY
                    )
                    add_file_row(frappe, file_url=key)
                    file_doc = FakeFile(name="FILE-1", file_url=key, new=False)
                    with _bound(frappe_context, 
                        actor=AUTHOR,
                        draft=DRAFT,
                        operation="pending_cleanup",
                        storage_key=key,
                    ):
                        with self.assertRaises(frappe.PermissionError):
                            file_doc.on_trash()

    def test_administrator_cannot_dispose_protected_bytes(self):
        from clinic_photos.access import frappe_context
        from clinic_photos.access import storage as storage_module
        from clinic_photos.access.policy import Actor

        # Task 6 boundary: Administrator identity is not protected disposal
        # authority. Exact-stack teardown remains an external operation.
        with fake_frappe(base_docs(photo_file="FILE-1"), user="Administrator") as frappe:
            file_doc = protected_doc(frappe)
            # Even a forged enabled-clinician Administrator principal with native read
            # is denied by the pure policy seam.
            with self.assertRaises(storage_module.ProtectedAccessDenied):
                storage_module.require_protected_read(
                    file_doc,
                    actor=Actor(user="Administrator", enabled=True, clinician=True),
                    native_read=True,
                )
            # Native cleanup/trash refuses protected bytes for Administrator too.
            with self.assertRaises(frappe.PermissionError):
                file_doc.on_trash()
            with self.assertRaises(frappe.PermissionError):
                file_doc._delete_file_on_disk()
            with self.assertRaises(frappe.PermissionError):
                file_doc.delete_file_from_filesystem()
            self.assertEqual(file_doc.calls, [])
            self.assertFalse(frappe_context.publication_transaction_live())


# ---------------------------------------------------------------------------
# Runtime adapter: feature controllers
# ---------------------------------------------------------------------------
class VersionControllerRuntimeTests(unittest.TestCase):
    def test_pending_registration_allows_empty_file_only_in_bound_context(self):
        from clinic_photos.access import frappe_context

        document = FakeVersion(
            draft=DRAFT,
            version_number=1,
            annotation_file="",
            storage_key=ANNOTATION_KEY,
            state="pending",
        )
        with fake_frappe(base_docs()) as frappe:
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                frappe_context.require_version_registration(document)

            with self.assertRaises(frappe.ValidationError):
                with _owned(frappe_context):
                    frappe_context.require_version_registration(document)

    def test_registration_rejects_a_preexisting_noncanonical_alias(self):
        from clinic_photos.access import frappe_context

        document = FakeVersion(
            draft=DRAFT,
            version_number=1,
            annotation_file="",
            storage_key=ANNOTATION_KEY,
            state="pending",
        )
        with fake_frappe(base_docs()) as frappe:
            frappe.db.add(
                "File",
                "ALIAS",
                file_url=ANNOTATION_KEY.replace(
                    "clinic-annotation-", "%63linic-annotation-"
                ),
                file_name="alias.json",
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY,
            ):
                with self.assertRaises(frappe.ValidationError):
                    frappe_context.require_version_registration(document)

    def test_committed_or_unknown_registration_is_denied(self):
        from clinic_photos.access import frappe_context

        for state in ("committed", "archived"):
            with self.subTest(state=state):
                document = FakeVersion(
                    draft=DRAFT,
                    version_number=1,
                    annotation_file="",
                    storage_key=ANNOTATION_KEY,
                    state=state,
                )
                with fake_frappe(base_docs()) as frappe:
                    with _bound(frappe_context, 
                        actor=AUTHOR,
                        draft=DRAFT,
                        operation="pending_insert",
                        storage_key=ANNOTATION_KEY,
                    ):
                        with self.assertRaises(frappe.ValidationError):
                            frappe_context.require_version_registration(document)

    def test_commit_requires_matching_persisted_pending_file(self):
        from clinic_photos.access import frappe_context

        previous = FakeVersion(
            draft=DRAFT,
            version_number=1,
            annotation_file="",
            storage_key=ANNOTATION_KEY,
            state="pending",
        )
        current = FakeVersion(
            draft=DRAFT,
            version_number=1,
            annotation_file="FILE-ANN",
            storage_key=ANNOTATION_KEY,
            state="committed",
        )

        with fake_frappe(base_docs()) as frappe:
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                content_hash="",
                file_size=0,
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="commit",
                storage_key=ANNOTATION_KEY,
            ):
                frappe_context.require_version_update(current, previous)

        with fake_frappe(base_docs()) as frappe:
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ORIGINAL_KEY,
                content_hash="",
                file_size=0,
            )
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="commit",
                storage_key=ANNOTATION_KEY,
            ):
                with self.assertRaises(frappe.ValidationError):
                    frappe_context.require_version_update(current, previous)

    def test_original_link_registration_requires_the_commit_context(self):
        from clinic_photos.access import frappe_context

        previous = FakeVersion()  # unused placeholder, real state below
        previous = types.SimpleNamespace(
            get=lambda fieldname: {
                "storage_key": ORIGINAL_KEY,
                "original_file": "",
                "original_sha256": "",
                "original_width": 0,
                "original_height": 0,
            }[fieldname]
        )
        current = types.SimpleNamespace(
            get=lambda fieldname: {
                "storage_key": ORIGINAL_KEY,
                "original_file": "FILE-1",
                "original_sha256": "a" * 64,
                "original_width": 10,
                "original_height": 20,
            }[fieldname]
        )
        with fake_frappe(base_docs()) as frappe:
            with self.assertRaises(frappe.ValidationError):
                with _owned(frappe_context):
                    frappe_context.require_original_update(current, previous)

        # A fresh execution: the failed finalization above aborted the first one.
        with fake_frappe(base_docs()) as frappe:
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="commit",
                storage_key=ORIGINAL_KEY,
            ):
                frappe_context.require_original_update(current, previous)


# ---------------------------------------------------------------------------
# Metadata policy (pure)
# ---------------------------------------------------------------------------
class QueryHookRuntimeTests(unittest.TestCase):
    def test_administrator_file_list_keeps_baseline_exclusions_only(self):
        from clinic_photos.access import frappe_context, permissions

        with fake_frappe(base_docs(), user="Administrator") as frappe:
            frappe.db.add("User", "other-admin@example.test", enabled=1)
            frappe.db.add("Has Role", "SYSTEM-MANAGER", parent="other-admin@example.test",
                          parenttype="User", role="System Manager")
            frappe.db.add("File", "VISIT", file_url="/private/files/clinic-visit-photo-x.png")
            frappe.db.add("File", "DRAWING", file_url="/private/files/clinic-visit-annotation-x.png")
            expected = permissions.file_query_condition(
                frappe_context.current_actor("Administrator"),
                frappe_context.protected_file_names(),
            )
            self.assertEqual(permissions.file_permission_query_conditions("Administrator"), expected)
            self.assertEqual(permissions.file_permission_query_conditions(), expected)
            for user in ("administrator", "other-admin@example.test", AUTHOR):
                self.assertNotEqual(permissions.file_permission_query_conditions(user), expected)
            self.assertFalse(permissions.file_has_permission(
                types.SimpleNamespace(name="DRAWING", file_url="/private/files/clinic-visit-annotation-x.png"),
                "Administrator", "read",
            ))

    def test_clinician_list_skips_legacy_scan(self):
        from clinic_photos.access import frappe_context, permissions
        with fake_frappe(base_docs()):
            with mock.patch.object(frappe_context, "protected_file_names", side_effect=AssertionError("unused scan")):
                permissions.file_permission_query_conditions(AUTHOR)

    def test_file_read_fetches_persisted_row_once(self):
        from clinic_photos.access import frappe_context, permissions
        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "ORDINARY", file_url="/private/files/ordinary.png")
            doc = types.SimpleNamespace(name="ORDINARY", file_url="/private/files/ordinary.png")
            with mock.patch.object(frappe_context, "_file_row", wraps=frappe_context._file_row) as get_row:
                self.assertTrue(permissions.file_has_permission(doc, AUTHOR, "read"))
            self.assertEqual(get_row.call_count, 1)

    def test_managed_drawing_file_list_uses_bulk_version_evidence(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo Annotation Version", "V-MANAGED",
                          draft="clinic-visit-annotation-draft-123",
                          annotation_file="DRAWING", storage_key="/private/files/shared.png")
            frappe.db.add("File", "DRAWING", file_url="/private/files/shared.png")
            frappe.db.add("File", "ALIAS", file_url="/private/files/%73hared.png")
            frappe.db.add("File", "ATTACHED", file_url="/private/files/unrelated.png",
                          attached_to_doctype="Clinic Photo Annotation Version",
                          attached_to_name="V-MANAGED")
            frappe.db.add("File", "CASE", file_url="/private/files/CLINIC-VISIT-ANNOTATION-x.png")
            frappe.db.add("File", "BAD-ENCODED",
                          file_url="/private/files/../%2543LINIC-VISIT-ANNOTATION-x.png")
            frappe.db.add("File", "LEGACY-NOTES",
                          file_url="/private/files/report-clinic-visit-annotation-notes.txt")
            frappe.db.add("File", "PUBLIC-NOTES",
                          file_url="/files/clinic-visit-annotation-x.png")
            frappe.db.add("File", "EXTERNAL-NOTES",
                          file_url="https://example.test/clinic-visit-annotation-x.png")
            frappe.db.add("File", "PUBLIC-PHOTO",
                          file_url="/files/clinic-visit-photo-x.png")
            condition = permissions.file_permission_query_conditions(AUTHOR)
        for name in ("DRAWING", "ALIAS", "ATTACHED", "CASE", "BAD-ENCODED"):
            self.assertIn(hex_name_literal(name), condition)
        self.assertNotIn(hex_name_literal("LEGACY-NOTES"), condition)
        self.assertNotIn(hex_name_literal("PUBLIC-NOTES"), condition)
        self.assertNotIn(hex_name_literal("EXTERNAL-NOTES"), condition)
        self.assertNotIn(hex_name_literal("PUBLIC-PHOTO"), condition)

    def test_visit_marker_fallback_only_matches_private_leading_basename(self):
        from clinic_photos.access import permissions

        paths = {
            "PUBLIC": "/files/clinic-visit-photo-x.png",
            "REMOTE": "https://example.test/private/files/clinic-visit-photo-x.png",
            "MIDDLE": "/private/files/report-clinic-visit-photo-x.png",
            "ENCODED-MIDDLE": "/private/files/report-%63linic-visit-photo-x.png",
            "PRIVATE": "/private/files/clinic-visit-photo-x.png",
            "UPPERCASE": "/PRIVATE/FILES/CLINIC-VISIT-PHOTO-x.png",
            "ENCODED": "/private/files/%2563linic-visit-photo-x.png",
            "MALFORMED": "/private/files/../clinic-visit-photo-x.png",
            "ENCODED-ROOT": "%2Fprivate%2Ffiles%2Fclinic-visit-photo-x.png",
        }
        hidden = {"PRIVATE", "UPPERCASE", "ENCODED", "MALFORMED", "ENCODED-ROOT"}
        with fake_frappe(base_docs()) as frappe:
            for name, url in paths.items():
                frappe.db.add("File", name, file_url=url)
                frappe.db.add("Clinic Photo", "P-" + name, storage_key=url)
            file_condition = permissions.file_permission_query_conditions(AUTHOR)
            photo_condition = permissions.photo_permission_query_conditions(AUTHOR)
        for name, url in paths.items():
            with self.subTest(name=name):
                self.assertEqual(permissions._contains_visit_marker(url), name in hidden)
                self.assertEqual(hex_name_literal(name) in file_condition, name in hidden)
                self.assertEqual(hex_name_literal("P-" + name) in photo_condition, name in hidden)

        with sqlite3.connect(":memory:") as connection:
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabFile" (name TEXT, file_url TEXT, attached_to_doctype TEXT, attached_to_name TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo" (name TEXT, storage_key TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Annotation Version" (name TEXT, draft TEXT, annotation_file TEXT, storage_key TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Draft" (name TEXT)')
            connection.executemany('INSERT INTO "tabFile" (name, file_url) VALUES (?, ?)', paths.items())
            connection.executemany('INSERT INTO "tabClinic Photo" VALUES (?, ?)',
                                   [("P-" + name, url) for name, url in paths.items()])
            visible_files = {row[0] for row in connection.execute('SELECT name FROM "tabFile" WHERE ' + file_condition)}
            visible_photos = {row[0][2:] for row in connection.execute('SELECT name FROM "tabClinic Photo" WHERE ' + photo_condition)}
        self.assertEqual(visible_files, paths.keys() - hidden)
        self.assertEqual(visible_photos, paths.keys() - hidden)

    def test_managed_draft_attachment_collation_alias_list_direct_parity(self):
        from clinic_photos.access import permissions
        draft = "clinic-visit-annotation-draft-x"
        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo Draft", draft, author=AUTHOR)
            frappe.db.add("File", "DRAFT-ATTACHED", file_url="/private/files/ordinary.png",
                          attached_to_doctype="Clinic Photo Draft",
                          attached_to_name="CLINIC-VISIT-ANNOTATION-DRAFT-X ")
            original = frappe.db.get_value
            # Collation-equivalent spelling lacks the reserved prefix before lookup.
            frappe.db.docs["File"]["DRAFT-ATTACHED"]["attached_to_name"] = "alias"
            def alias_lookup(doctype, name, *args, **kwargs):
                return original(doctype, draft if doctype == "Clinic Photo Draft" and name == "alias" else name, *args, **kwargs)
            with mock.patch.object(frappe.db, "get_value", side_effect=alias_lookup):
                doc = types.SimpleNamespace(name="DRAFT-ATTACHED", file_url="/private/files/ordinary.png")
                self.assertFalse(permissions.file_has_permission(doc, AUTHOR, "read"))
            condition = permissions.file_permission_query_conditions(AUTHOR)
        # SQLite custom collation models a non-prefix MariaDB-equivalent alias.
        with sqlite3.connect(":memory:") as connection:
            def collate(a, b):
                norm = lambda s: draft if s.lower() == "alias" else s.lower()
                return (norm(a) > norm(b)) - (norm(a) < norm(b))
            connection.create_collation("DRAFT_ALIAS", collate)
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabFile" (name TEXT, file_url TEXT, attached_to_doctype TEXT, attached_to_name TEXT COLLATE DRAFT_ALIAS)')
            connection.execute('CREATE TABLE "tabClinic Photo Annotation Version" (name TEXT, draft TEXT, annotation_file TEXT, storage_key TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Draft" (name TEXT COLLATE DRAFT_ALIAS)')
            connection.execute('INSERT INTO "tabFile" VALUES (?, ?, ?, ?)', ("DRAFT-ATTACHED", "/private/files/ordinary.png", "Clinic Photo Draft", "alias"))
            connection.execute('INSERT INTO "tabClinic Photo Draft" VALUES (?)', (draft,))
            self.assertEqual(connection.execute('SELECT name FROM "tabFile" WHERE ' + condition).fetchall(), [])

    def test_unrelated_encoded_paths_do_not_expand_list_sql(self):
        from clinic_photos.access import permissions
        with fake_frappe(base_docs()) as frappe:
            for index in range(300):
                frappe.db.add("File", str(index), file_url=f"/private/files/%61lias-{index}.txt")
            condition = permissions.file_permission_query_conditions(AUTHOR)
        self.assertNotIn("UNION ALL", condition)

    def test_encoded_candidates_are_batched_without_truncation(self):
        from clinic_photos.access import permissions
        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo Annotation Version", "V",
                          draft="clinic-visit-annotation-draft-123",
                          storage_key="/private/files/TARGET.png")
            for index in range(205):
                frappe.db.add("File", str(index), file_url=f"/private/files/%61lias-{index}.txt")
            frappe.db.add("File", "MATCH", file_url="/private/files/%74arget.png")
            calls = []
            def bulk_match(query, values):
                calls.append(values)
                return [(values[i],) for i in range(0, len(values), 2)
                        if values[i + 1].lower() == "/private/files/target.png"]
            with mock.patch.object(frappe.db, "sql", side_effect=bulk_match):
                condition = permissions.file_permission_query_conditions(AUTHOR)
        self.assertEqual([len(values) for values in calls], [200, 200, 12])
        self.assertIn(hex_name_literal("MATCH"), condition)
        self.assertNotIn("UNION ALL", condition)

    def test_managed_attachment_collation_alias_list_direct_parity(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo Annotation Version", "V-MANAGED",
                          draft="clinic-visit-annotation-draft-123")
            frappe.db.add("File", "ATTACHED", file_url="/private/files/ordinary.png",
                          attached_to_doctype="Clinic Photo Annotation Version",
                          attached_to_name="v-managed")
            original_get_value = frappe.db.get_value
            def collation_get_value(doctype, name, *args, **kwargs):
                if doctype == "Clinic Photo Annotation Version" and name == "v-managed":
                    name = "V-MANAGED"
                return original_get_value(doctype, name, *args, **kwargs)
            with mock.patch.object(frappe.db, "get_value", side_effect=collation_get_value):
                doc = types.SimpleNamespace(name="ATTACHED", file_url="/private/files/ordinary.png")
                self.assertFalse(permissions.file_has_permission(doc, AUTHOR, "read"))
            condition = permissions.file_permission_query_conditions(AUTHOR)
        with sqlite3.connect(":memory:") as connection:
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabFile" (name TEXT, file_url TEXT, attached_to_doctype TEXT, attached_to_name TEXT COLLATE NOCASE)')
            connection.execute('CREATE TABLE "tabClinic Photo Annotation Version" (name TEXT COLLATE NOCASE, draft TEXT, annotation_file TEXT, storage_key TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Draft" (name TEXT)')
            connection.execute('INSERT INTO "tabFile" VALUES (?, ?, ?, ?)',
                               ("ATTACHED", "/private/files/ordinary.png", "Clinic Photo Annotation Version", "v-managed"))
            connection.execute('INSERT INTO "tabClinic Photo Annotation Version" VALUES (?, ?, ?, ?)',
                               ("V-MANAGED", "clinic-visit-annotation-draft-123", "", ""))
            self.assertEqual(connection.execute('SELECT name FROM "tabFile" WHERE ' + condition).fetchall(), [])

    def test_encoded_canonical_key_uses_database_collation(self):
        from clinic_photos.access import permissions
        with sqlite3.connect(":memory:") as connection:
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabFile" (name TEXT, file_url TEXT, attached_to_doctype TEXT, attached_to_name TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Annotation Version" (name TEXT, draft TEXT, annotation_file TEXT, storage_key TEXT COLLATE NOCASE)')
            connection.execute('CREATE TABLE "tabClinic Photo Draft" (name TEXT)')
            connection.execute('INSERT INTO "tabFile" VALUES (?, ?, ?, ?)', ("ENCODED", "/private/files/%73hared.png", "", ""))
            connection.execute('INSERT INTO "tabClinic Photo Annotation Version" VALUES (?, ?, ?, ?)', ("V", "clinic-visit-annotation-draft-123", "", "/private/files/SHARED.png"))
            with fake_frappe(base_docs()) as frappe:
                frappe.db.add("Clinic Photo Annotation Version", "V",
                              draft="clinic-visit-annotation-draft-123", storage_key="/private/files/SHARED.png")
                frappe.db.add("File", "ENCODED", file_url="/private/files/%73hared.png")
                with mock.patch.object(frappe.db, "sql", side_effect=lambda query, values: connection.execute(query.replace("%s", "?"), values).fetchall()):
                    condition = permissions.file_permission_query_conditions(AUTHOR)
            self.assertEqual(connection.execute('SELECT name FROM "tabFile" WHERE ' + condition).fetchall(), [])

    def test_managed_drawing_direct_read_is_denied(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo Annotation Version", "V-MANAGED",
                          draft="clinic-visit-annotation-draft-123",
                          annotation_file="DRAWING", storage_key="/private/files/shared.png",
                          state="pending")
            frappe.db.add("File", "DRAWING", file_url="/private/files/shared.png")
            doc = types.SimpleNamespace(name="DRAWING", file_url="/private/files/shared.png")
            self.assertFalse(permissions.file_has_permission(doc, AUTHOR, "read"))

    def _outsider_docs(self):
        docs = base_docs()
        docs["User"][OTHER_AUTHOR] = {"name": OTHER_AUTHOR, "enabled": 1}
        docs["Has Role"]["HR-OUT"] = {
            "name": "HR-OUT",
            "parent": OTHER_AUTHOR,
            "parenttype": "User",
            "role": "Healthcare Administrator",
        }
        return docs

    def test_visit_query_hooks_support_named_parameter_interpolation(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()):
            conditions = (
                ("`tabFile`.`file_url`", permissions.file_permission_query_conditions(AUTHOR)),
                ("`tabClinic Photo`.`storage_key`", permissions.photo_permission_query_conditions(AUTHOR)),
            )
        for field, condition in conditions:
            with self.subTest(field=field):
                query = ("SELECT name WHERE " + condition + " AND name = %(name)s") % {
                    "name": "'ordinary'"
                }
                self.assertIn(
                    f"LOWER(IFNULL({field}, '')) NOT LIKE CONCAT('/private/files/clinic-visit-photo-', CHAR(37))",
                    query,
                )

    def test_non_clinician_hook_excludes_scanned_orphans_only(self):
        from clinic_photos.access import permissions

        with fake_frappe(self._outsider_docs()) as frappe:
            frappe.db.add(
                "File",
                "ORPHAN-ENCODED",
                file_url="/private/files/%63linic-photo-99999999.png",
                file_name="o.png",
            )
            frappe.db.add(
                "File",
                "UNRELATED",
                file_url="/private/files/scan.png",
                file_name="s.png",
            )
            condition = permissions.file_permission_query_conditions(OTHER_AUTHOR)

        self.assertIn(hex_name_literal("ORPHAN-ENCODED"), condition)
        self.assertNotIn(hex_name_literal("UNRELATED"), condition)
        self.assertTrue(condition.startswith("not ("))

    def test_clinician_hook_preserves_legacy_and_hides_visit_namespace(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add(
                "File",
                "ORPHAN",
                file_url="/private/files/clinic-photo-99999999.png",
                file_name="o.png",
            )
            condition = permissions.file_permission_query_conditions(AUTHOR)
            self.assertIn("`tabFile`.`file_url`", condition)
            self.assertIn("/private/files/clinic-visit-photo-", condition)
            self.assertNotIn(hex_name_literal("ORPHAN"), condition)

    def test_visit_list_excludes_encoded_and_malformed_aliases_for_each_actor(self):
        from clinic_photos.access import permissions

        with fake_frappe(self._outsider_docs()) as frappe:
            for name, url in (
                ("VISIT-ENCODED", "/private/files/%63linic-visit-photo-1.png"),
                ("VISIT-BAD", "/private/files/../clinic-visit-photo-2.png"),
                ("VISIT-DOUBLE", "/private/files/%2563linic-visit-photo-3.png"),
                ("LEGACY", "/private/files/clinic-photo-1.png"),
                ("ORDINARY", "/private/files/scan.png"),
            ):
                frappe.db.add("File", name, file_url=url)
            for user in (AUTHOR, OTHER_AUTHOR):
                condition = permissions.file_permission_query_conditions(user)
                for name in ("VISIT-ENCODED", "VISIT-BAD", "VISIT-DOUBLE"):
                    self.assertIn(hex_name_literal(name), condition)
                self.assertNotIn(hex_name_literal("ORDINARY"), condition)

    def test_visit_query_executes_against_file_table_and_preserves_legacy(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "O'BRIEN", file_url="/private/files/%63linic-visit-photo-x")
            condition = permissions.file_permission_query_conditions(AUTHOR)
        with sqlite3.connect(":memory:") as connection:
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabFile" (name TEXT, file_url TEXT, attached_to_doctype TEXT, attached_to_name TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Annotation Version" (name TEXT, draft TEXT, annotation_file TEXT, storage_key TEXT)')
            connection.execute('CREATE TABLE "tabClinic Photo Draft" (name TEXT)')
            connection.executemany('INSERT INTO "tabFile" (name, file_url) VALUES (?, ?)', [
                ("O'BRIEN", "/private/files/%63linic-visit-photo-x"),
                ("NEW", "/private/files/clinic-visit-photo-new.png"),
                ("LEGACY", "/private/files/clinic-photo-old.png"),
                ("ORDINARY", "/private/files/ordinary.png"),
            ])
            rows = connection.execute(
                'SELECT name FROM "tabFile" WHERE ' + condition + " ORDER BY name"
            ).fetchall()
        self.assertEqual(rows, [("LEGACY",), ("ORDINARY",)])

    def test_photo_list_excludes_visit_alias_and_slot_without_key(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo", "P-ALIAS",
                          storage_key="/private/files/%63linic-visit-photo-x")
            frappe.db.add("Clinic Photo", "P-SLOT", storage_key="")
            frappe.db.add("Clinic Visit Photo Slot", "S", photo="P-SLOT")
            condition = permissions.photo_permission_query_conditions(AUTHOR)
        self.assertIn("`tabClinic Photo`.`storage_key`", condition)
        self.assertIn(hex_name_literal("P-ALIAS"), condition)
        self.assertIn(hex_name_literal("P-SLOT"), condition)
        with sqlite3.connect(":memory:") as connection:
            connection.create_function("CONCAT", -1, lambda *values: "".join(values))
            connection.execute('CREATE TABLE "tabClinic Photo" (name TEXT, storage_key TEXT)')
            connection.executemany('INSERT INTO "tabClinic Photo" VALUES (?, ?)', [
                ("P-ALIAS", "/private/files/%63linic-visit-photo-x"),
                ("P-SLOT", ""),
                ("P-NEW", "/private/files/clinic-visit-photo-x"),
                ("P-OLD", "/private/files/clinic-photo-x"),
            ])
            rows = connection.execute(
                'SELECT name FROM "tabClinic Photo" WHERE ' + condition
            ).fetchall()
        self.assertEqual(rows, [("P-OLD",)])

    def test_photo_direct_read_denies_stored_marker_and_slot_when_caller_mutates_key(self):
        from clinic_photos.access import permissions

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("Clinic Photo", "P-STORED",
                          storage_key="/private/files/clinic-visit-photo-x")
            frappe.db.add("Clinic Photo", "P-SLOT", storage_key="")
            frappe.db.add("Clinic Visit Photo Slot", "S", photo="P-SLOT")
            for name in ("P-STORED", "P-SLOT"):
                doc = types.SimpleNamespace(name=name, storage_key="/private/files/ordinary.png")
                self.assertFalse(permissions.photo_has_permission(doc, ptype="read"))
            legacy = types.SimpleNamespace(name=PHOTO, storage_key=ORIGINAL_KEY)
            self.assertTrue(permissions.photo_has_permission(legacy, ptype="read"))


class MetadataPolicyTests(unittest.TestCase):
    def test_unrelated_files_are_left_to_native_permissions(self):
        self.assertTrue(file_metadata_allowed(None, actor_for("System Manager")))

    def test_protected_metadata_requires_a_current_enabled_clinician(self):
        record = classify_file(
            {"file_url": ORIGINAL_KEY}, lookup=lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        )

        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                self.assertTrue(file_metadata_allowed(record, actor_for(role)))
        for role in ("Healthcare Administrator", "System Manager"):
            with self.subTest(role=role):
                self.assertFalse(file_metadata_allowed(record, actor_for(role)))

        self.assertFalse(
            file_metadata_allowed(record, actor_for("Physician", enabled=False))
        )
        self.assertFalse(
            file_metadata_allowed(
                record, Actor(user="Administrator", enabled=True, clinician=True)
            )
        )
        self.assertFalse(
            file_metadata_allowed(
                record, Actor(user="Guest", enabled=True, clinician=True)
            )
        )

    def test_read_is_not_granted_and_never_becomes_write(self):
        record = classify_file(
            {"file_url": ORIGINAL_KEY}, lookup=lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        )
        clinician = actor_for("Physician")

        for permission_type in ("read", "select"):
            with self.subTest(permission_type=permission_type):
                self.assertIsNone(
                    file_has_permission_for(record, clinician, permission_type)
                )
        for permission_type in (
            "write",
            "delete",
            "share",
            "export",
            "print",
            "email",
            "import",
            "report",
            "submit",
            "cancel",
            "amend",
        ):
            with self.subTest(permission_type=permission_type):
                self.assertIs(
                    file_has_permission_for(record, clinician, permission_type),
                    False,
                )

    def test_create_defers_only_inside_the_matching_pending_context(self):
        from clinic_photos.access.storage import classify_file as classify

        record = classify(
            {"file_url": "/private/files/clinic-photo-7777.png"}, lookup=lookup_for({})
        )
        clinician = actor_for("Physician")
        key = "/private/files/clinic-photo-7777.png"

        self.assertIs(file_has_permission_for(record, clinician, "create"), False)
        context = PublicationContext(
            actor=AUTHOR,
            draft=DRAFT,
            operation="pending_insert",
            storage_key=key,
        )
        self.assertIsNone(
            file_has_permission_for(
                record, clinician, "create", context=context, storage_key=key
            )
        )
        self.assertIs(
            file_has_permission_for(
                record,
                clinician,
                "create",
                context=context,
                storage_key="/private/files/clinic-photo-8888.png",
            ),
            False,
        )

    def test_delete_defers_only_inside_the_matching_cleanup_context(self):
        record = classify_file(
            {"file_url": ORIGINAL_KEY}, lookup=lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        )
        clinician = actor_for("Physician")
        context = PublicationContext(
            actor=AUTHOR,
            draft=DRAFT,
            operation="pending_cleanup",
            storage_key=ORIGINAL_KEY,
        )
        self.assertIs(file_has_permission_for(record, clinician, "delete"), False)
        self.assertIsNone(
            file_has_permission_for(
                record,
                clinician,
                "delete",
                context=context,
                storage_key=ORIGINAL_KEY,
            )
        )


class QueryConditionTests(unittest.TestCase):
    def test_clinician_list_is_left_to_native_conditions(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                self.assertEqual(file_query_condition(actor_for(role)), "")

    def test_non_clinician_list_excludes_only_protected_rows(self):
        condition = file_query_condition(actor_for("System Manager"))
        self.assertTrue(condition.startswith("not ("))
        self.assertIn(PROTECTED_FILE_SQL_CONDITION, condition)
        self.assertNotIn("1=1", condition)

    def test_static_condition_covers_references_and_storage_keys(self):
        condition = PROTECTED_FILE_SQL_CONDITION
        for fragment in (
            "`tabFile`.`name` IN (SELECT `original_file` FROM `tabClinic Photo`",
            "`tabFile`.`name` IN (SELECT `annotation_file` FROM `tabClinic Photo Annotation Version`",
            "IFNULL(`tabFile`.`file_url`, '') IN (SELECT `storage_key` FROM `tabClinic Photo`",
            "IFNULL(`tabFile`.`file_url`, '') IN (SELECT `storage_key` FROM `tabClinic Photo Annotation Version`",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, condition)

        # No blanket spelling prefix filter: reserved orphans in any accepted spelling
        # come from the exact scanned name list instead.
        self.assertNotIn("LIKE '%/clinic-photo-%'", condition)
        self.assertNotIn("LIKE '%/clinic-annotation-%'", condition)

    def test_scanned_names_are_exact_hex_exclusions(self):
        names = ["FILE-ORPHAN", "FILE-O'BRIEN", "FILE-back\\slash", "FILE-\u4e2d\u6587"]
        condition = protected_file_condition(names)

        # Mode-independent representation: only quote delimiters and hex digits.
        self.assertIn("HEX(`tabFile`.`name`) IN (", condition)
        for name in names:
            self.assertIn(hex_name_literal(name), condition)
            self.assertIn(name.encode("utf-8").hex().upper(), condition)
        for name in ("FILE-UNRELATED", "FILE-PUBLIC"):
            self.assertNotIn(name, condition)

        literal = hex_name_literal("FILE-O'BRIEN")
        self.assertEqual(literal, "'" + b"FILE-O'BRIEN".hex().upper() + "'")
        self.assertNotIn("'", literal[1:-1])
        self.assertNotIn("\\", literal)

    def test_unrepresentable_file_name_fails_closed(self):
        for value in (None, "", 7):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    hex_name_literal(value)

    def test_extra_native_condition_is_preserved(self):
        condition = file_query_condition(
            actor_for("System Manager"),
            ["FILE-ORPHAN"],
            "`tabFile`.`owner` = 'x'",
        )
        self.assertTrue(condition.startswith("(`tabFile`.`owner` = 'x') and not ("))
        self.assertIn(hex_name_literal("FILE-ORPHAN"), condition)

    def test_condition_does_not_interpolate_the_actor(self):
        actor = actor_for("System Manager", user="o'brien@example.test")
        self.assertNotIn(actor.user, file_query_condition(actor))

    def test_sqlite_fixture_executes_the_list_condition(self):
        """Portable behavioural list test; native MariaDB execution is Task 4."""
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE "tabFile" (name TEXT, file_url TEXT);
            CREATE TABLE "tabClinic Photo" (name TEXT, original_file TEXT, storage_key TEXT);
            CREATE TABLE "tabClinic Photo Annotation Version"
                (name TEXT, annotation_file TEXT, storage_key TEXT);
            INSERT INTO "tabFile" VALUES
                ('FILE-ORIGINAL', '/private/files/clinic-photo-11111111.png'),
                ('FILE-ALIAS-SPELLED', '/private/files/%63linic-photo-11111111.png'),
                ('FILE-ALIAS-DOTSEG', '/private/files/sub/../clinic-photo-11111111.png'),
                ('FILE-ANNOTATION', '/private/files/clinic-annotation-22222222.json'),
                ('FILE-ORPHAN-PLAIN', '/private/files/clinic-photo-99999999.png'),
                ('FILE-ORPHAN-ENCODED', '/private/files/%63linic-annotation-99999999.json'),
                ('FILE-UNRELATED', '/private/files/scan.png'),
                ('FILE-PUBLIC', '/files/scan.png'),
                ('FILE-FOLDER', NULL);
            INSERT INTO "tabClinic Photo" VALUES
                ('PHOTO', 'FILE-ORIGINAL', '/private/files/clinic-photo-11111111.png');
            INSERT INTO "tabClinic Photo Annotation Version" VALUES
                ('VERSION', 'FILE-ANNOTATION', '/private/files/clinic-annotation-22222222.json');
            """
        )

        file_rows = list(
            connection.execute('SELECT name, file_url FROM "tabFile"')
        )
        docs = {
            "File": {
                name: {"name": name, "file_url": url} for name, url in file_rows
            },
            "Clinic Photo": {
                PHOTO: {
                    "name": PHOTO,
                    "original_file": "FILE-ORIGINAL",
                    "storage_key": "/private/files/clinic-photo-11111111.png",
                }
            },
            "Clinic Photo Annotation Version": {
                VERSION: {
                    "name": VERSION,
                    "annotation_file": "FILE-ANNOTATION",
                    "storage_key": "/private/files/clinic-annotation-22222222.json",
                }
            },
        }
        from clinic_photos.access import frappe_context

        with fake_frappe(docs):
            names = frappe_context.protected_file_names()

        self.assertIn("FILE-ALIAS-SPELLED", names)
        self.assertIn("FILE-ORPHAN-ENCODED", names)
        self.assertIn("FILE-ORPHAN-PLAIN", names)
        self.assertNotIn("FILE-UNRELATED", names)
        self.assertNotIn("FILE-PUBLIC", names)
        self.assertNotIn("FILE-FOLDER", names)

        try:
            returned = {
                row[0]
                for row in connection.execute(
                    'SELECT name FROM "tabFile" WHERE 1=1 AND ({0})'.format(
                        file_query_condition(actor_for("System Manager"), names)
                    )
                )
            }
        finally:
            connection.close()

        self.assertEqual(
            returned,
            {"FILE-UNRELATED", "FILE-PUBLIC", "FILE-FOLDER"},
        )

# ---------------------------------------------------------------------------
# Hooks and controller wiring
# ---------------------------------------------------------------------------
class HookRegistrationTests(unittest.TestCase):
    def setUp(self):
        from clinic_photos import hooks

        self.hooks = hooks

    def test_file_extension_and_permission_hooks_are_registered(self):
        self.assertEqual(
            self.hooks.extend_doctype_class,
            {"File": "clinic_photos.access.file_mixin.ProtectedFileMixin"},
        )
        self.assertEqual(
            self.hooks.permission_query_conditions,
            {
                "File": (
                    "clinic_photos.access.permissions."
                    "file_permission_query_conditions"
                ),
                "Clinic Photo": (
                    "clinic_photos.access.permissions."
                    "photo_permission_query_conditions"
                ),
                "Clinic Photo Draft": "clinic_photos.access.permissions.draft_permission_query_conditions",
                "Clinic Photo Annotation Version": "clinic_photos.access.permissions.version_permission_query_conditions",
            },
        )
        self.assertEqual(
            self.hooks.has_permission,
            {"File": "clinic_photos.access.permissions.file_has_permission",
             "Clinic Photo": "clinic_photos.access.permissions.photo_has_permission",
             "Clinic Photo Draft": "clinic_photos.access.permissions.draft_has_permission",
             "Clinic Photo Annotation Version": "clinic_photos.access.permissions.version_has_permission"},
        )

    def test_whitelisted_entry_point_and_response_hooks_are_registered(self):
        # Task 3 adds the bank raw-reader override and the ZIP marker override, plus
        # the header-hook pair; no document-event or role hook is registered by this
        # access foundation.
        from clinic_photos.access.entrypoints import (
            NATIVE_ZIP_PATH,
            RAW_READER_PATH,
        )

        self.assertEqual(
            self.hooks.override_whitelisted_methods,
            {
                RAW_READER_PATH: (
                    "clinic_photos.access.entrypoints.convert_mt940_to_csv"
                ),
                NATIVE_ZIP_PATH: "clinic_photos.access.entrypoints.zip_files",
                "healthcare.healthcare.doctype.patient_appointment.patient_appointment."
                "check_in_appointment": "clinic_photos.visit.checkin.check_in_appointment",
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
            "DocShare": {"validate": "clinic_photos.access.share.validate_docshare"},
        })


class ControllerWiringTests(unittest.TestCase):
    DOCTYPES = APP_ROOT / "clinic_photos" / "clinic_photos" / "doctype"

    def source(self, doctype_snake):
        return (self.DOCTYPES / doctype_snake / f"{doctype_snake}.py").read_text(
            encoding="utf-8"
        )

    def test_original_controller_delegates_to_adapter_guards(self):
        source = self.source("clinic_photo")
        # Registration goes through the owner-gated adapter entry point, which itself
        # delegates to the identity-consistency check.
        self.assertIn("require_original_registration", source)
        self.assertNotIn("require_registration_identities_consistent", source)
        self.assertIn("require_original_update", source)
        self.assertIn("require_original_delete", source)
        self.assertIn("get_doc_before_save", source)

    def test_version_controller_delegates_to_adapter_guards(self):
        source = self.source("clinic_photo_annotation_version")
        self.assertIn("require_version_registration", source)
        self.assertIn("require_version_update", source)
        self.assertIn("require_version_delete", source)
        self.assertIn("get_doc_before_save", source)

    def test_draft_controller_maps_identity_failures_to_native_errors(self):
        source = self.source("clinic_photo_draft")
        self.assertIn("DraftIdentityError", source)
        self.assertIn("frappe.PermissionError", source)


# ---------------------------------------------------------------------------
# Fix round 2: key-wide first content, write chain, locks
# ---------------------------------------------------------------------------
def _bound(frappe_context, *, owner=None, **overrides):
    """Pending publication bound to an explicit transaction-owner stub.

    The app ships no transaction-owning caller, so tests must register a narrow owner
    (``FakePublicationOwner``) to exercise a supported execution context at all.
    """
    options = dict(
        actor=AUTHOR,
        draft=DRAFT,
        operation="pending_insert",
        storage_key=ANNOTATION_KEY,
    )
    options.update(overrides)
    if owner is None:
        owner = frappe_context.current_publication_owner() or FakePublicationOwner()
    return _owned_bound(frappe_context, owner, options)


@contextlib.contextmanager
def _owned_bound(frappe_context, owner, options):
    with frappe_context.owned_publication_transaction(owner):
        with frappe_context.bound_publication(**options):
            yield owner


@contextlib.contextmanager
def _owned(frappe_context, owner=None):
    """Owner-registered execution without entering a publication context."""
    owner = (
        owner
        if owner is not None
        else (frappe_context.current_publication_owner() or FakePublicationOwner())
    )
    with frappe_context.owned_publication_transaction(owner):
        yield owner


class KeyWideFirstContentTests(unittest.TestCase):
    def test_alias_row_cannot_overwrite_initialized_original_bytes(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_file="FILE-ORIGINAL")
        with fake_frappe(docs) as frappe:
            add_file_row(
                frappe,
                name="FILE-ORIGINAL",
                file_url=ORIGINAL_KEY,
                content_hash="a" * 64,
                file_size=9,
            )
            # A fresh dedup alias: empty hash/size, same canonical key, no reference
            # by name. Caller-row inspection alone would call this "uninitialized".
            alias = FakeFile(
                name="FILE-ALIAS",
                file_url=ORIGINAL_KEY,
                file_name="alias.png",
                new=True,
                content_hash=None,
                file_size=0,
            )
            with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                with self.assertRaises(frappe.PermissionError):
                    alias.save_file(content=b"overwrite", overwrite=True)
            self.assertEqual(alias.calls, [])

    def test_existing_confined_target_bytes_deny_first_content(self):
        import tempfile

        from clinic_photos.access import frappe_context

        with tempfile.TemporaryDirectory() as site_path:
            basename = ANNOTATION_KEY.rsplit("/", 1)[-1]
            target = Path(site_path) / "private" / "files" / basename
            target.parent.mkdir(parents=True)
            target.write_bytes(b"already-there")

            with fake_frappe(base_docs(), site_path=site_path) as frappe:
                file_doc = FakeFile(
                    file_url=ANNOTATION_KEY, file_name="annotation.json"
                )
                with _bound(frappe_context):
                    with self.assertRaises(frappe.PermissionError):
                        file_doc.save_file(content=b"new")
                self.assertEqual(file_doc.calls, [])

    def test_original_initialization_requires_the_bound_draft_photo(self):
        from clinic_photos.access import frappe_context

        for draft_photo, expected in ((PHOTO, True), ("PHOTO-OTHER", False)):
            with self.subTest(draft_photo=draft_photo):
                docs = base_docs(photo_file="", draft_photo=draft_photo)
                with fake_frappe(docs) as frappe:
                    file_doc = FakeFile(
                        file_url=ORIGINAL_KEY, file_name="original.png"
                    )
                    with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                        if expected:
                            file_doc.save_file(content=b"original")
                            self.assertEqual(file_doc.calls[0][0], "save_file")
                        else:
                            with self.assertRaises(frappe.PermissionError):
                                file_doc.save_file(content=b"original")
                            self.assertEqual(file_doc.calls, [])

    def test_one_native_chain_is_one_operation_and_a_second_write_is_denied(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            file_doc.native_chain = True
            with _bound(frappe_context):
                file_doc.save_file(content=b"bytes")
                self.assertEqual(
                    [call[0] for call in file_doc.calls],
                    ["save_file", "save_file_on_filesystem", "write_file"],
                )
                # Same document, second chain: consumed already, before any metadata
                # for the key was persisted.
                with self.assertRaises(frappe.PermissionError):
                    file_doc.write_file()
                with self.assertRaises(frappe.PermissionError):
                    file_doc.save_file(content=b"again")
            self.assertEqual(len(file_doc.calls), 3)

    def test_exception_residue_stays_denied_until_authorized_cleanup(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            file_doc.native_chain = True
            file_doc.fail_after_write = True
            with _bound(frappe_context):
                with self.assertRaises(RuntimeError):
                    file_doc.save_file(content=b"partial")
                # Chain depth unwound, but the key stays consumed.
                self.assertNotIn(
                    ANNOTATION_KEY,
                    getattr(frappe.local, "clinic_photos_in_flight_writes", {}),
                )
                with self.assertRaises(frappe.PermissionError):
                    file_doc.write_file()

    def test_consumed_mark_clears_only_after_authorized_cleanup(self):
        from clinic_photos.access import frappe_context

        def write_pending_bytes(frappe):
            add_file_row(
                frappe,
                name="FILE-ANN",
                file_url=ANNOTATION_KEY,
                content_hash="",
                file_size=0,
            )
            file_doc = FakeFile(
                name="FILE-ANN", file_url=ANNOTATION_KEY, new=False
            )
            file_doc.native_chain = True
            with _bound(frappe_context):
                file_doc.save_file(content=b"bytes")
            return file_doc

        # A refused cleanup is a failed protected cleanup: it retains the mark and
        # aborts that execution, so the authorized path needs its own execution.
        with fake_frappe(base_docs()) as frappe:
            file_doc = write_pending_bytes(frappe)
            consumed = getattr(
                frappe.local, "clinic_photos_consumed_first_writes", set()
            )
            self.assertIn(ANNOTATION_KEY, consumed)

            with self.assertRaises(frappe.PermissionError):
                file_doc.on_trash()
            self.assertIn(ANNOTATION_KEY, consumed)
            self.assertFalse(frappe_context.publication_transaction_live())

        with fake_frappe(base_docs()) as frappe:
            file_doc = write_pending_bytes(frappe)
            consumed = getattr(
                frappe.local, "clinic_photos_consumed_first_writes", set()
            )
            self.assertIn(ANNOTATION_KEY, consumed)

            with _bound(frappe_context, operation="pending_cleanup"):
                file_doc.on_trash()
            self.assertNotIn(ANNOTATION_KEY, consumed)


class PendingIdentityBindingTests(unittest.TestCase):
    def pending_doc(self, frappe, **overrides):
        add_file_row(
            frappe,
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            content_hash="",
            file_size=0,
            file_name="annotation.json",
        )
        values = dict(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            is_private=1,
            owner=AUTHOR,
            new=False,
            content_hash="",
            file_size=0,
        )
        values.update(overrides)
        return FakeFile(**values)

    def test_owner_name_privacy_and_url_changes_are_denied_under_context(self):
        from clinic_photos.access import frappe_context

        cases = (
            {"owner": OTHER_AUTHOR},
            {"file_name": "renamed.json"},
            {"is_private": 0},
            {"file_url": "/private/files/clinic-annotation-99999999.json"},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with fake_frappe(base_docs()) as frappe:
                    file_doc = self.pending_doc(frappe)
                    with _bound(frappe_context):
                        for fieldname, value in overrides.items():
                            setattr(file_doc, fieldname, value)
                        with self.assertRaises(frappe.PermissionError):
                            file_doc.validate()
                    self.assertEqual(file_doc.calls, [])

    def test_only_content_hash_and_size_may_change_under_context(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = self.pending_doc(frappe)
            with _bound(frappe_context):
                file_doc.content_hash = "b" * 64
                file_doc.file_size = 12
                file_doc.validate()
            self.assertEqual(file_doc.calls, [("validate",)])


class KeyLockTests(unittest.TestCase):
    def test_one_lock_per_key_is_held_through_commit_and_released_once(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            file_doc.native_chain = True
            with _bound(frappe_context):
                file_doc.save_file(content=b"bytes")
                self.assertEqual(len(frappe.db.lock_calls), 1)
                self.assertEqual(frappe.db.lock_timeouts, [10])
                self.assertIn(frappe.db.lock_calls[0], frappe.db.held_locks)
                self.assertEqual(frappe.db.release_calls, [])

            frappe.db.after_commit.run()
            self.assertEqual(frappe.db.release_calls, frappe.db.lock_calls)
            self.assertEqual(frappe.db.held_locks, set())

            # Idempotent: a second commit run does not release twice.
            frappe.db.after_commit.run()
            self.assertEqual(len(frappe.db.release_calls), 1)

    def test_rollback_releases_the_same_lock_once(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            file_doc = FakeFile(
                file_url=ANNOTATION_KEY, file_name="annotation.json"
            )
            with _bound(frappe_context):
                file_doc.write_file()
            frappe.db.after_rollback.run()
            self.assertEqual(len(frappe.db.release_calls), 1)
            self.assertEqual(frappe.db.held_locks, set())

    def test_lock_timeout_or_uncertain_ownership_denies_before_side_effects(self):
        from clinic_photos.access import frappe_context

        for setup in ("denied", "error"):
            with self.subTest(setup=setup):
                with fake_frappe(base_docs()) as frappe:
                    if setup == "denied":
                        frappe.db.lock_denied = True
                    else:
                        frappe.db.lock_error = RuntimeError("connection lost")
                    file_doc = FakeFile(
                        file_url=ANNOTATION_KEY, file_name="annotation.json"
                    )
                    file_doc.native_chain = True
                    with _bound(frappe_context):
                        with self.assertRaises(frappe.ValidationError):
                            file_doc.save_file(content=b"bytes")
                    self.assertEqual(file_doc.calls, [])
                    self.assertEqual(
                        getattr(
                            frappe.local, "clinic_photos_consumed_first_writes", set()
                        ),
                        set(),
                    )

    def test_identity_and_registration_paths_take_the_same_key_lock(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            file_doc = protected_doc(frappe)
            with _owned(frappe_context):
                file_doc.validate()
            self.assertEqual(len(frappe.db.lock_calls), 1)

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "CANON", file_url=ORIGINAL_KEY, file_name="o.png")
            frappe_context.require_registration_identities_consistent(ORIGINAL_KEY)
            self.assertEqual(len(frappe.db.lock_calls), 1)


class RegistrationCanonicalTests(unittest.TestCase):
    def test_registration_requires_the_stored_key_to_be_canonical(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            with self.assertRaises(frappe.ValidationError):
                frappe_context.require_registration_identities_consistent(
                    ORIGINAL_KEY.replace("clinic-photo-", "%63linic-photo-")
                )
            self.assertEqual(frappe.db.lock_calls, [])

    def test_registration_rejects_a_noncanonical_feature_storage_key(self):
        from clinic_photos.access import frappe_context

        docs = base_docs()
        docs["Clinic Photo"][PHOTO]["storage_key"] = ORIGINAL_KEY.replace(
            "clinic-photo-", "%63linic-photo-"
        )
        with fake_frappe(docs) as frappe:
            frappe.db.add("File", "CANON", file_url=ORIGINAL_KEY, file_name="o.png")
            with self.assertRaises(frappe.ValidationError):
                frappe_context.require_registration_identities_consistent(ORIGINAL_KEY)

    def test_registration_accepts_canonical_key_and_complete_scan(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "CANON", file_url=ORIGINAL_KEY, file_name="o.png")
            frappe.db.add(
                "File",
                "UNRELATED",
                file_url="/private/files/scan%20name.png",
                file_name="scan.png",
            )
            frappe_context.require_registration_identities_consistent(ANNOTATION_KEY)
            self.assertEqual(len(frappe.db.lock_calls), 1)


# ---------------------------------------------------------------------------
# Fix round 3: exact chain token, completed-write receipt, lock lifecycle,
# untouched storage keys and mode-independent query literals
# ---------------------------------------------------------------------------
class WriteChainTokenTests(unittest.TestCase):
    def _doc(self, frappe, **overrides):
        values = dict(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        values.update(overrides)
        add_file_row(
            frappe,
            name=values["name"],
            file_url=values["file_url"],
            content_hash=values["content_hash"],
            file_size=values["file_size"],
            file_name=values["file_name"],
        )
        doc = FakeFile(**values)
        doc.native_chain = True
        return doc

    def test_installed_order_is_one_physical_write_and_clears_the_chain(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
                self.assertEqual(
                    [call[0] for call in doc.calls],
                    ["save_file", "save_file_on_filesystem", "write_file"],
                )
                self.assertEqual(
                    getattr(frappe.local, "clinic_photos_in_flight_writes", {}), {}
                )
            self.assertEqual(len(frappe.db.lock_calls), 1)

    def test_recursive_save_inside_the_outer_call_is_denied(self):
        from clinic_photos.access import frappe_context

        seen = {}

        def hook(doc):
            try:
                doc.save_file(content=b"again", overwrite=True)
            except frappe.PermissionError:
                seen["denied"] = True
            else:
                seen["denied"] = False

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            doc.on_native = hook
            # The nested denial aborts the whole publication: the outer native step
            # that follows is refused as well, so no bytes are written at all.
            with self.assertRaises(frappe.PermissionError):
                with _bound(frappe_context):
                    doc.save_file(content=b"bytes")
            self.assertTrue(seen["denied"])
            self.assertEqual(
                [call[0] for call in doc.calls],
                ["save_file", "save_file_on_filesystem"],
            )

    def test_sibling_object_writing_the_same_key_inside_the_chain_is_denied(self):
        from clinic_photos.access import frappe_context

        seen = {}

        def hook(doc):
            sibling = FakeFile(
                name="FILE-ANN", file_url=ANNOTATION_KEY, new=False
            )
            try:
                sibling.write_file()
            except frappe.PermissionError:
                seen["denied"] = True
            else:
                seen["denied"] = False
                seen["calls"] = sibling.calls

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            doc.on_native = hook
            with self.assertRaises(frappe.PermissionError):
                with _bound(frappe_context):
                    doc.save_file(content=b"bytes")
            self.assertTrue(seen["denied"])
            self.assertNotIn("calls", seen)
            self.assertNotIn(("write_file",), doc.calls)

    def test_changed_context_inside_the_chain_is_denied(self):
        from clinic_photos.access import frappe_context

        seen = {}

        def hook(doc):
            with _bound(frappe_context, 
                actor=AUTHOR,
                draft=DRAFT,
                operation="pending_insert",
                storage_key=ANNOTATION_KEY.replace("22222222", "33333333"),
            ):
                try:
                    doc.write_file()
                except frappe.PermissionError:
                    seen["denied"] = True
                else:
                    seen["denied"] = False

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            doc.on_native = hook
            with self.assertRaises(frappe.PermissionError):
                with _bound(frappe_context):
                    doc.save_file(content=b"bytes")
            self.assertTrue(seen["denied"])
            # No bytes at all: the changed context aborted the publication.
            self.assertEqual(
                [call[0] for call in doc.calls].count("write_file"), 0
            )

    def test_repeated_physical_write_inside_the_chain_is_denied(self):
        from clinic_photos.access import frappe_context

        outcomes = []

        def hook(doc):
            for _ in range(2):
                try:
                    doc.write_file()
                except frappe.PermissionError:
                    outcomes.append("denied")
                else:
                    outcomes.append("allowed")

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            doc.on_native = hook
            with _bound(frappe_context):
                # The hook used the single physical write; the native step that follows
                # must not write a second time.
                with self.assertRaises(frappe.PermissionError):
                    doc.save_file(content=b"bytes")
            self.assertEqual(outcomes, ["allowed", "denied"])

    def test_direct_write_entry_cannot_be_repeated(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._doc(frappe)
            doc.native_chain = False
            with _bound(frappe_context):
                doc.write_file()
                with self.assertRaises(frappe.PermissionError):
                    doc.write_file()


class CompletedWriteReceiptTests(unittest.TestCase):
    def _written(self, frappe):
        add_file_row(
            frappe,
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            content_hash="",
            file_size=0,
            file_name="annotation.json",
        )
        doc = FakeFile(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        doc.native_chain = True
        return doc

    def test_one_metadata_finalization_without_rearming_bytes(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
                doc.validate()
                self.assertEqual(doc.calls[-1], ("validate",))
                # Receipt is one-use and never re-arms a second physical write.
                with self.assertRaises(frappe.PermissionError):
                    doc.validate()
                with self.assertRaises(frappe.PermissionError):
                    doc.save_file(content=b"again")

    def test_forged_result_identity_is_denied(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
                doc.content_hash = "f" * 64
                with self.assertRaises(frappe.PermissionError):
                    doc.validate()

    def test_rebound_object_is_denied(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
                rebound = FakeFile(
                    name="FILE-ANN",
                    file_url=ANNOTATION_KEY,
                    file_name="annotation.json",
                    new=False,
                    content_hash=doc.content_hash,
                    file_size=doc.file_size,
                )
                with self.assertRaises(frappe.PermissionError):
                    rebound.validate()

    def test_changed_context_or_mutated_identity_is_denied(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
                doc.owner = OTHER_AUTHOR
                with self.assertRaises(frappe.PermissionError):
                    doc.validate()

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
            with _bound(frappe_context, draft="DRAFT-2"):
                with self.assertRaises(frappe.PermissionError):
                    doc.validate()

    def test_failed_write_mints_no_receipt(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            doc.fail_after_write = True
            with _bound(frappe_context):
                with self.assertRaises(RuntimeError):
                    doc.save_file(content=b"partial")
                with self.assertRaises(frappe.PermissionError):
                    doc.validate()
            self.assertEqual(
                getattr(frappe.local, "clinic_photos_completed_writes", {}), {}
            )


class LockLifecycleTests(unittest.TestCase):
    def _written(self, frappe):
        add_file_row(
            frappe,
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            content_hash="",
            file_size=0,
        )
        doc = FakeFile(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        doc.native_chain = True
        return doc

    def test_release_runs_after_native_filesystem_compensation(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            log = frappe.db.event_log
            doc.rollback_events = log
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")

            frappe.db.rollback()
            self.assertEqual(log[0], "compensation")
            self.assertIn(("release", frappe.db.lock_calls[0]), log)
            self.assertEqual(frappe.db.held_locks, set())
            self.assertNotIn(
                frappe.db.lock_calls[0],
                getattr(frappe.local, "clinic_photos_key_locks", {}),
            )

    def test_failed_release_keeps_uncertain_ownership_and_denies_reuse(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe_context._acquire_key_lock(ANNOTATION_KEY)
            name = frappe.db.lock_calls[0]
            frappe.db.release_error = RuntimeError("connection lost")

            frappe.db.commit()
            entry = getattr(frappe.local, "clinic_photos_key_locks", {}).get(name)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["state"], "uncertain")

            with self.assertRaises(frappe.ValidationError):
                frappe_context._acquire_key_lock(ANNOTATION_KEY)
            with self.assertRaises(frappe.ValidationError):
                frappe_context._acquire_key_lock(ANNOTATION_KEY)

    def test_guard_failure_after_acquisition_retains_transaction_ownership(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_file="FILE-ORIGINAL")
        with fake_frappe(docs) as frappe:
            add_file_row(
                frappe,
                name="FILE-ORIGINAL",
                file_url=ORIGINAL_KEY,
                content_hash="a" * 64,
                file_size=9,
            )
            doc = FakeFile(file_url=ORIGINAL_KEY, file_name="alias.png")
            doc.native_chain = True
            denied = False
            with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                try:
                    doc.save_file(content=b"overwrite", overwrite=True)
                except frappe.PermissionError:
                    denied = True

            self.assertTrue(denied)
            self.assertEqual(doc.calls, [])
            # The denial must not release a lock that may already guard earlier
            # uncommitted work in this transaction.
            name = frappe.db.lock_calls[0]
            self.assertIn(name, frappe.db.held_locks)
            self.assertEqual(frappe.db.release_calls, [])
            self.assertEqual(
                frappe.local.clinic_photos_key_locks[name]["state"], "held"
            )

            # Only the transaction outcome releases it, once.
            frappe.db.rollback()
            self.assertEqual(frappe.db.held_locks, set())
            self.assertEqual(frappe.db.release_calls, [name])

    def test_before_commit_failure_still_releases_on_the_following_rollback(self):
        from clinic_photos.access import frappe_context

        class Boom(RuntimeError):
            pass

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
            name = frappe.db.lock_calls[0]

            def raiser():
                raise Boom()

            # Registered after the lock's own before_commit callback, i.e. the
            # installed ordering that makes commit() clear the rollback callbacks.
            frappe.db.before_commit.add(raiser)
            with self.assertRaises(Boom):
                frappe.db.commit()
            self.assertIn(name, frappe.db.held_locks)

            frappe.db.rollback()
            self.assertEqual(frappe.db.held_locks, set())
            self.assertIn(name, frappe.db.release_calls)

    def test_commit_releases_once_and_a_second_commit_is_a_no_op(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._written(frappe)
            with _bound(frappe_context):
                doc.save_file(content=b"bytes")
            frappe.db.commit()
            frappe.db.commit()
            self.assertEqual(len(frappe.db.release_calls), 1)


class RawStorageKeyTests(unittest.TestCase):
    def test_registration_rejects_surrounding_whitespace_without_stripping(self):
        from clinic_photos.access import frappe_context

        for raw in (
            f" {ANNOTATION_KEY}",
            f"{ANNOTATION_KEY} ",
            f" {ORIGINAL_KEY} ",
        ):
            with self.subTest(raw=raw):
                with fake_frappe(base_docs()) as frappe:
                    with self.assertRaises(frappe.ValidationError):
                        frappe_context.require_registration_identities_consistent(raw)

    def test_nonreserved_original_key_is_accepted_untouched(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe_context.require_registration_identities_consistent(
                "/private/files/scan.png"
            )

    def test_version_registration_rejects_a_whitespace_storage_key(self):
        from clinic_photos.access import frappe_context

        document = FakeVersion(
            draft=DRAFT,
            version_number=1,
            annotation_file="",
            storage_key=f" {ANNOTATION_KEY}",
            state="pending",
        )
        with fake_frappe(base_docs()) as frappe:
            with _bound(frappe_context):
                with self.assertRaises(frappe.ValidationError):
                    frappe_context.require_version_registration(document)

    def test_photo_controller_passes_the_stored_field_untouched(self):
        source = (
            APP_ROOT
            / "clinic_photos"
            / "clinic_photos"
            / "doctype"
            / "clinic_photo"
            / "clinic_photo.py"
        ).read_text(encoding="utf-8")
        self.assertIn('storage_key = self.get("storage_key")', source)
        self.assertNotIn('self.get("storage_key") or "").strip()', source)


class SqlModeIndependentTests(unittest.TestCase):
    NAMES = (
        "FILE-O'BRIEN",
        "FILE-back\\slash",
        "FILE-中文-照片",
        'FILE-"quoted"',
        "FILE-%63linic-photo-99999999.png",
    )

    def test_constructed_condition_matches_awkward_names_exactly(self):
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE "tabFile" (name TEXT, file_url TEXT);
            CREATE TABLE "tabClinic Photo" (name TEXT, original_file TEXT, storage_key TEXT);
            CREATE TABLE "tabClinic Photo Annotation Version"
                (name TEXT, annotation_file TEXT, storage_key TEXT);
            """
        )
        unrelated = ("FILE-UNRELATED", "FILE-OBRIEN", "FILE-backslash", "FILE-中文")
        connection.executemany(
            'INSERT INTO "tabFile" (name, file_url) VALUES (?, NULL)',
            [(name,) for name in (*self.NAMES, *unrelated)],
        )
        try:
            condition = protected_file_condition(list(self.NAMES))
            returned = {
                row[0]
                for row in connection.execute(
                    'SELECT name FROM "tabFile" WHERE 1=1 AND ({0})'.format(condition)
                )
            }
        finally:
            connection.close()

        self.assertEqual(returned, set(self.NAMES))
        self.assertFalse(set(unrelated) & returned)

    def test_condition_carries_no_backslash_or_name_text(self):
        condition = protected_file_condition(list(self.NAMES))
        self.assertNotIn("\\", condition)
        self.assertNotIn("'BRIEN", condition)
        self.assertNotIn("中文", condition)
        self.assertIn("HEX(`tabFile`.`name`) IN (", condition)


# ---------------------------------------------------------------------------
# Fix round 4: transaction-owned locks, publication poison, reserved first
# content and confirmed release outcomes
# ---------------------------------------------------------------------------
class TransactionOwnershipTests(unittest.TestCase):
    def _denied_doc(self, frappe):
        add_file_row(
            frappe,
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            content_hash="",
            file_size=0,
        )
        doc = FakeFile(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        doc.native_chain = True
        return doc

    def test_registration_lock_is_retained_through_a_caught_denial(self):
        from clinic_photos.access import frappe_context

        # A committed version makes the first-content decision deny.
        with fake_frappe(base_docs(version_state="committed")) as frappe:
            frappe_context.require_registration_identities_consistent(ANNOTATION_KEY)
            name = frappe.db.lock_calls[0]

            doc = self._denied_doc(frappe)
            denied = False
            with _bound(frappe_context):
                try:
                    doc.save_file(content=b"bytes")
                except frappe.PermissionError:
                    denied = True

            self.assertTrue(denied)
            self.assertEqual(doc.calls, [])
            # The registration lock acquired earlier in this transaction is still
            # owned here: the denial did not release or forget it.
            self.assertIn(name, frappe.db.held_locks)
            self.assertEqual(frappe.db.release_calls, [])
            self.assertEqual(
                frappe.local.clinic_photos_key_locks[name]["state"], "held"
            )

            frappe.db.commit()
            self.assertEqual(frappe.db.held_locks, set())
            self.assertEqual(frappe.db.release_calls, [name])
            self.assertNotIn(name, frappe.local.clinic_photos_key_locks)

    def test_denied_first_write_poisons_retry_finalization_and_cleanup(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(version_state="committed")) as frappe:
            doc = self._denied_doc(frappe)
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                with self.assertRaises(frappe.PermissionError):
                    doc.save_file(content=b"bytes")

            # The denying caller was told, and the whole transaction is aborted.
            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["content_write"])
            self.assertFalse(frappe_context.publication_transaction_live())

            # Re-binding a fresh publication context is refused.
            with self.assertRaises(frappe_context.PublicationContextError):
                with _bound(frappe_context):
                    pass

            # Every later protection decision in this transaction denies.
            with self.assertRaises(frappe.PermissionError):
                doc.write_file()
            doc.content_hash = "b" * 64
            doc.file_size = 11
            with self.assertRaises(frappe.PermissionError):
                doc.validate()
            with self.assertRaises(frappe.PermissionError):
                doc.on_trash()

            self.assertEqual(doc.calls, [])
            self.assertIn(
                ANNOTATION_KEY,
                frappe.local.clinic_photos_failed_publications,
            )

    def test_failed_write_residue_denies_retry_and_cleanup_in_the_transaction(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._denied_doc(frappe)
            doc.fail_after_write = True
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                with self.assertRaises(RuntimeError):
                    doc.save_file(content=b"partial")

            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["content_write_failed"])

            # Residue from the failed chain is not reusable in this transaction, for
            # this key or any other.
            with self.assertRaises(frappe.PermissionError):
                doc.write_file()
            with self.assertRaises(frappe.PermissionError):
                doc.on_trash()
            with self.assertRaises(frappe_context.PublicationContextError):
                with _bound(frappe_context, operation="pending_cleanup"):
                    pass

            name = frappe.db.lock_calls[0]
            self.assertIn(name, frappe.db.held_locks)
            self.assertEqual(frappe.db.release_calls, [])

            frappe.db.rollback()
            self.assertEqual(frappe.db.held_locks, set())
            self.assertEqual(frappe.db.release_calls, [name])


class ReleaseConfirmationTests(unittest.TestCase):
    UNCONFIRMED = {
        "another-owner": [(0,)],
        "missing-lock": [(None,)],
        "empty-result": [],
        "missing-value": [()],
        "text-value": [("1",)],
        "boolean-value": [(True,)],
        "malformed-value": [(object(),)],
    }

    def test_only_an_explicit_integer_one_forgets_ownership(self):
        from clinic_photos.access import frappe_context

        for label, result in self.UNCONFIRMED.items():
            with self.subTest(label=label):
                with fake_frappe(base_docs()) as frappe:
                    frappe_context._acquire_key_lock(ANNOTATION_KEY)
                    name = frappe.db.lock_calls[0]
                    frappe.db.release_result = result

                    self.assertFalse(frappe_context._release_key_lock(name))
                    entry = frappe.local.clinic_photos_key_locks.get(name)
                    self.assertIsNotNone(entry)
                    self.assertEqual(entry["state"], "uncertain")
                    with self.assertRaises(frappe.ValidationError):
                        frappe_context._acquire_key_lock(ANNOTATION_KEY)
                    self.assertIn(name, frappe.db.held_locks)

        with fake_frappe(base_docs()) as frappe:
            frappe_context._acquire_key_lock(ANNOTATION_KEY)
            name = frappe.db.lock_calls[0]
            self.assertTrue(frappe_context._release_key_lock(name))
            self.assertNotIn(name, frappe.local.clinic_photos_key_locks)

    def test_a_raised_release_is_not_evidence_the_connection_died(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe_context._acquire_key_lock(ANNOTATION_KEY)
            name = frappe.db.lock_calls[0]
            frappe.db.release_error = RuntimeError("connection lost")

            self.assertFalse(frappe_context._release_key_lock(name))
            entry = frappe.local.clinic_photos_key_locks.get(name)
            self.assertIsNotNone(entry)
            self.assertEqual(entry["state"], "uncertain")
            with self.assertRaises(frappe.ValidationError):
                frappe_context._acquire_key_lock(ANNOTATION_KEY)


class ReservedFirstContentTests(unittest.TestCase):
    LEGACY_KEY = "/private/files/scan.png"

    def test_new_first_content_requires_the_reserved_namespace(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_key=self.LEGACY_KEY, photo_file="")
        with fake_frappe(docs) as frappe:
            # The registered non-reserved original is still protected metadata.
            record = classify_file(
                {"file_url": self.LEGACY_KEY},
                lookup=frappe_context.lookup_protection,
            )
            self.assertTrue(record.registered)
            self.assertFalse(
                frappe_context._first_content_allowed(record, None, [])
            )

            doc = FakeFile(file_url=self.LEGACY_KEY, file_name="scan.png")
            doc.native_chain = True
            with _bound(frappe_context):
                with self.assertRaises(frappe.PermissionError):
                    doc.save_file(content=b"bytes")
            self.assertEqual(doc.calls, [])
            # No bytes were written; the key's lock stays transaction-owned.
            self.assertEqual(len(frappe.db.lock_calls), 1)
            self.assertEqual(frappe.db.release_calls, [])

    def test_reserved_predicate_is_enforced_independently_of_the_context(self):
        import tempfile
        import types

        from clinic_photos.access import frappe_context

        docs = base_docs(photo_key=self.LEGACY_KEY, photo_file="")
        with tempfile.TemporaryDirectory() as site_path:
            with fake_frappe(docs, site_path=site_path) as frappe:
                legacy = classify_file(
                    {"file_url": self.LEGACY_KEY},
                    lookup=frappe_context.lookup_protection,
                )
                reserved = classify_file(
                    {"file_url": ANNOTATION_KEY},
                    lookup=frappe_context.lookup_protection,
                )
                # The public constructor refuses a non-reserved key, so this value
                # object stands in for a context that *does* cover the legacy key: the
                # first-content predicate must still deny it on its own.
                covering = types.SimpleNamespace(
                    actor=AUTHOR,
                    draft=DRAFT,
                    operation="pending_insert",
                    storage_key=self.LEGACY_KEY,
                )
                self.assertFalse(
                    frappe_context._first_content_allowed(legacy, covering, [])
                )

                with _bound(frappe_context):
                    self.assertTrue(
                        frappe_context._first_content_allowed(
                            reserved, frappe_context.current_publication(), []
                        )
                    )

    def test_registered_nonreserved_file_keeps_normal_protection(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_key=self.LEGACY_KEY, photo_file="")
        with fake_frappe(docs) as frappe:
            add_file_row(frappe, name="FILE-SCAN", file_url=self.LEGACY_KEY)
            doc = FakeFile(
                name="FILE-SCAN", file_url=self.LEGACY_KEY, file_name="scan.png",
                new=False,
            )
            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_protected_read(doc)
            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_file_delete(doc, None)


# ---------------------------------------------------------------------------
# Fix round 5: execution-wide publication abort and the transaction-owner boundary
# ---------------------------------------------------------------------------
class TransactionAbortTests(unittest.TestCase):
    def _annotation_doc(self, frappe, **overrides):
        values = dict(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        values.update(overrides)
        add_file_row(
            frappe,
            name=values["name"],
            file_url=values["file_url"],
            content_hash=values["content_hash"],
            file_size=values["file_size"],
            file_name=values["file_name"],
        )
        doc = FakeFile(**values)
        doc.native_chain = True
        return doc

    def test_unowned_execution_denies_every_publication_entry(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(photo_file="FILE-1")) as frappe:
            self.assertFalse(frappe_context.publication_transaction_live())

            # No owner: the publication context itself is unsupported.
            with self.assertRaises(frappe_context.PublicationContextError):
                with frappe_context.bound_publication(
                    actor=AUTHOR,
                    draft=DRAFT,
                    operation="pending_insert",
                    storage_key=ANNOTATION_KEY,
                ):
                    pass

            doc = self._annotation_doc(frappe)
            with self.assertRaises(frappe.PermissionError):
                doc.save_file(content=b"bytes")
            self.assertEqual(doc.calls, [])
            self.assertEqual(frappe.db.lock_calls, [])

            with self.assertRaises(frappe.PermissionError):
                FakeFile(
                    file_url=ANNOTATION_KEY, file_name="annotation.json"
                ).before_insert()

            document = FakeVersion(
                draft=DRAFT,
                version_number=1,
                annotation_file="",
                storage_key=ANNOTATION_KEY,
                state="pending",
            )
            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_version_registration(document)

            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_original_registration(ORIGINAL_KEY)

            # Protected cleanup is denied too.
            with self.assertRaises(frappe.PermissionError):
                doc.on_trash()

            # Unrelated Files and protected reads are untouched by the owner gate.
            unrelated = FakeFile(
                file_url="/files/public.png", file_name="public.png", is_private=0
            )
            unrelated.native_chain = True
            unrelated.save_file(content=b"public")
            self.assertIn(("write_file",), unrelated.calls)

            protected = protected_doc(frappe)
            self.assertIsNone(frappe_context.require_protected_read(protected))

    def test_partial_k_failure_denies_every_j_boundary(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_file="", draft_photo=PHOTO)
        with fake_frappe(docs) as frappe:
            # J: a registered original published successfully in its own transaction.
            j = self._annotation_doc(
                frappe, name="FILE-ORIG", file_url=ORIGINAL_KEY,
                file_name="original.png",
            )
            with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                j.save_file(content=b"original")
            self.assertIn(
                ORIGINAL_KEY, frappe.local.clinic_photos_completed_writes
            )

            # K: the native write fails after touching bytes; the caller catches it.
            k = self._annotation_doc(frappe)
            k.fail_after_write = True
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                with self.assertRaises(RuntimeError):
                    k.save_file(content=b"partial")

            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["content_write_failed"])
            self.assertFalse(frappe_context.publication_transaction_live())

            # J's publication context cannot be re-entered.
            with self.assertRaises(frappe_context.PublicationContextError):
                with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                    pass

            # J's write, registration, receipt finalization and cleanup all deny.
            j2 = FakeFile(file_url=ORIGINAL_KEY, file_name="original.png")
            j2.native_chain = True
            with self.assertRaises(frappe.PermissionError):
                j2.save_file(content=b"again")
            self.assertEqual(j2.calls, [])

            with self.assertRaises(frappe.PermissionError):
                FakeFile(
                    file_url=ORIGINAL_KEY, file_name="original.png"
                ).before_insert()

            j.content_hash = "d" * 64
            j.file_size = 9
            before = list(j.calls)
            with self.assertRaises(frappe.PermissionError):
                j.validate()
            self.assertEqual(j.calls, before)

            with self.assertRaises(frappe.PermissionError):
                j.on_trash()

            # Unrelated Files stay untouched even in the aborted transaction.
            unrelated = FakeFile(
                file_url="/files/public.png", file_name="public.png", is_private=0
            )
            unrelated.native_chain = True
            unrelated.save_file(content=b"public")
            self.assertIn(("write_file",), unrelated.calls)

    def test_failure_at_registration_boundary_aborts_the_whole_transaction(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            document = FakeVersion(
                draft=DRAFT,
                version_number=1,
                annotation_file="",
                storage_key=f" {ANNOTATION_KEY}",
                state="pending",
            )
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                with self.assertRaises(frappe.ValidationError):
                    frappe_context.require_version_registration(document)

            # The failure was a registration validation, not a byte write, and it
            # still aborts publication for the whole execution.
            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["version_registration"])
            self.assertFalse(frappe_context.publication_transaction_live())

            with self.assertRaises(frappe_context.PublicationContextError):
                with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                    pass

            doc = FakeFile(file_url=ORIGINAL_KEY, file_name="original.png")
            doc.native_chain = True
            with self.assertRaises(frappe.PermissionError):
                doc.save_file(content=b"bytes")
            self.assertEqual(doc.calls, [])

    def test_original_registration_failure_aborts_the_transaction(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            frappe.db.add("File", "CANON", file_url=ORIGINAL_KEY, file_name="o.png")
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(frappe.ValidationError):
                    frappe_context.require_original_registration(
                        ORIGINAL_KEY.replace("clinic-photo-", "%63linic-photo-")
                    )

            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["original_registration"])
            with self.assertRaises(frappe.PermissionError):
                frappe_context.require_original_registration(ORIGINAL_KEY)

    def test_owner_signalled_commit_failure_denies_continuation(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            doc = self._annotation_doc(frappe)
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                doc.save_file(content=b"bytes")
                self.assertTrue(frappe_context.publication_transaction_live())

                # The owning caller's commit failed; only it can report that, and this
                # adapter does not pretend to observe it.
                owner.abort_publication("commit failed")

                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe.PermissionError):
                    doc.write_file()

            # Nothing else may publish in the same execution.
            with self.assertRaises(frappe_context.PublicationContextError):
                with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                    pass
            other = FakeFile(file_url=ORIGINAL_KEY, file_name="original.png")
            other.native_chain = True
            with self.assertRaises(frappe.PermissionError):
                other.save_file(content=b"bytes")
            self.assertEqual(other.calls, [])

    def test_aborted_state_survives_leaving_and_reentering_an_owner_scope(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(version_state="committed")) as frappe:
            doc = self._annotation_doc(frappe)
            with _bound(frappe_context):
                with self.assertRaises(frappe.PermissionError):
                    doc.save_file(content=b"bytes")

            self.assertIs(
                getattr(frappe.local, "clinic_photos_publication_aborted"), True
            )

            # Leaving the owner scope and registering a healthy new owner must not
            # clear the aborted state (no lexical exit / savepoint reset).
            fresh = FakePublicationOwner()
            with frappe_context.owned_publication_transaction(fresh):
                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=fresh):
                        pass


# ---------------------------------------------------------------------------
# Fix round 6 (authorized exception to the five-round stop): escaping owner and
# context exceptions latch the execution abort
# ---------------------------------------------------------------------------
class OwnerExceptionLatchTests(unittest.TestCase):
    def _annotation_doc(self, frappe, **overrides):
        values = dict(
            name="FILE-ANN",
            file_url=ANNOTATION_KEY,
            file_name="annotation.json",
            new=False,
            content_hash="",
            file_size=0,
        )
        values.update(overrides)
        add_file_row(
            frappe,
            name=values["name"],
            file_url=values["file_url"],
            content_hash=values["content_hash"],
            file_size=values["file_size"],
            file_name=values["file_name"],
        )
        doc = FakeFile(**values)
        doc.native_chain = True
        return doc

    def test_exception_escaping_owner_scope_latches_for_a_fresh_owner(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            with self.assertRaises(RuntimeError):
                with _owned(frappe_context, owner):
                    raise RuntimeError("caller body failed")

            self.assertTrue(owner.aborted)
            self.assertEqual(owner.reasons, ["owner_scope_exception"])

            # A fresh, healthy owner in the same execution cannot re-open publication.
            fresh = FakePublicationOwner()
            with frappe_context.owned_publication_transaction(fresh):
                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=fresh):
                        pass

            doc = self._annotation_doc(frappe)
            with self.assertRaises(frappe.PermissionError):
                doc.save_file(content=b"bytes")
            self.assertEqual(doc.calls, [])

    def test_nested_same_owner_exception_caught_inside_latches_outer_scope(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(RuntimeError):
                    with _owned(frappe_context, owner):
                        raise RuntimeError("nested scope failed")

                # The outer transaction is still open and its owner is still healthy,
                # yet publication is already aborted.
                self.assertIs(
                    getattr(frappe.local, "clinic_photos_publication_aborted"), True
                )
                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=owner):
                        pass

    def test_status_query_failure_latches_even_when_it_recovers(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            owner.status_error = RuntimeError("status unavailable")
            with frappe_context.owned_publication_transaction(owner):
                self.assertFalse(frappe_context.publication_transaction_live())

                # The owner recovers, but the latched uncertainty stands.
                owner.status_error = None
                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=owner):
                        pass

    def test_owner_reported_abort_is_sticky_after_it_recovers(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            with _bound(frappe_context, owner=owner):
                owner.abort_publication("commit failed")
                self.assertFalse(frappe_context.publication_transaction_live())

                # A transient "healthy again" answer cannot re-open the execution.
                owner.aborted = False
                self.assertFalse(frappe_context.publication_transaction_live())

    def test_notifier_failure_preserves_the_original_error_and_the_abort(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            owner.notify_error = RuntimeError("notifier unavailable")
            with _owned(frappe_context, owner):
                with self.assertRaises(RuntimeError) as caught:
                    with _bound(frappe_context, owner=owner):
                        raise RuntimeError("original failure")
            # The original error, not the notifier error, reaches the caller.
            self.assertEqual(str(caught.exception), "original failure")
            self.assertTrue(owner.aborted)
            self.assertFalse(frappe_context.publication_transaction_live())

    def test_exception_escaping_publication_context_latches(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(RuntimeError):
                    with _bound(frappe_context, owner=owner):
                        raise RuntimeError("publication body failed")

                self.assertFalse(frappe_context.publication_transaction_live())
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=owner):
                        pass


# ---------------------------------------------------------------------------
# Fix round 6: the actual protected cleanup effect is inside the abort boundary
# ---------------------------------------------------------------------------
class ProtectedCleanupBoundaryTests(unittest.TestCase):
    def _doc(self, frappe, name="FILE-ANN", file_url=ANNOTATION_KEY):
        add_file_row(
            frappe, name=name, file_url=file_url, content_hash="", file_size=0
        )
        doc = FakeFile(name=name, file_url=file_url, new=False)
        doc.native_chain = True
        return doc

    def test_partial_cleanup_failure_denies_every_other_boundary(self):
        from clinic_photos.access import frappe_context

        docs = base_docs(photo_file="", draft_photo=PHOTO)
        with fake_frappe(docs) as frappe:
            # J: a published original with an unused metadata receipt.
            j = self._doc(frappe, name="FILE-ORIG", file_url=ORIGINAL_KEY)
            j.file_name = "original.png"
            with _bound(frappe_context, storage_key=ORIGINAL_KEY):
                j.save_file(content=b"original")
            self.assertIn(ORIGINAL_KEY, frappe.local.clinic_photos_completed_writes)

            # K: the protected cleanup partially acts and then raises; the caller
            # catches it while the owning transaction is still healthy.
            k = self._doc(frappe)
            k.fail_after_delete = True
            owner = FakePublicationOwner()
            with _bound(
                frappe_context, owner=owner, operation="pending_cleanup"
            ):
                with self.assertRaises(RuntimeError):
                    k.on_trash()
                self.assertTrue(owner.aborted)
                self.assertEqual(owner.reasons, ["protected_cleanup_effect"])
                self.assertFalse(frappe_context.publication_transaction_live())

                # J: context, write, registration, receipt finalization, cleanup.
                with self.assertRaises(frappe_context.PublicationContextError):
                    with _bound(frappe_context, owner=owner, storage_key=ORIGINAL_KEY):
                        pass

                fresh = FakeFile(file_url=ORIGINAL_KEY, file_name="original.png")
                fresh.native_chain = True
                with self.assertRaises(frappe.PermissionError):
                    fresh.save_file(content=b"again")
                self.assertEqual(fresh.calls, [])

                with self.assertRaises(frappe.PermissionError):
                    FakeFile(
                        file_url=ORIGINAL_KEY, file_name="original.png"
                    ).before_insert()

                before = list(j.calls)
                with self.assertRaises(frappe.PermissionError):
                    j.validate()
                self.assertEqual(j.calls, before)
                self.assertIs(
                    frappe.local.clinic_photos_completed_writes[ORIGINAL_KEY]["used"],
                    False,
                )

                with self.assertRaises(frappe.PermissionError):
                    j.on_trash()

    def test_direct_file_cleanup_methods_latch_on_native_failure(self):
        from clinic_photos.access import frappe_context

        for method in (
            "delete_file_from_filesystem",
            "delete_file_data_content",
            "on_trash",
        ):
            with self.subTest(method=method):
                with fake_frappe(base_docs()) as frappe:
                    doc = self._doc(frappe)
                    doc.fail_after_delete = True
                    owner = FakePublicationOwner()
                    with _bound(
                        frappe_context, owner=owner, operation="pending_cleanup"
                    ):
                        with self.assertRaises(RuntimeError):
                            getattr(doc, method)()
                        self.assertFalse(
                            frappe_context.publication_transaction_live()
                        )

    def test_controller_cleanup_validation_failure_latches(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs(version_state="committed")) as frappe:
            document = FakeVersion(
                draft=DRAFT,
                version_number=1,
                annotation_file="",
                storage_key=ANNOTATION_KEY,
                state="committed",
            )
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(frappe.PermissionError):
                    frappe_context.require_version_delete(document)
                self.assertFalse(frappe_context.publication_transaction_live())

        with fake_frappe(base_docs()) as frappe:
            photo_state = types.SimpleNamespace(
                get=lambda fieldname: {
                    "original_file": "FILE-1",
                    "storage_key": ORIGINAL_KEY,
                    "original_sha256": "a" * 64,
                    "original_width": 10,
                    "original_height": 20,
                }[fieldname]
            )
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(frappe.PermissionError):
                    frappe_context.require_original_delete(photo_state)
                self.assertFalse(frappe_context.publication_transaction_live())

    def test_administrator_cannot_delete_committed_records(self):
        """Actor identity is not protected cleanup authority for records either.

        The File byte path lost its Administrator shortcut in this task; the record
        delete path must behave the same, so the publication boundary and the
        lifecycle assertion run for the Administrator session too.
        """
        from clinic_photos.access import frappe_context

        with fake_frappe(
            base_docs(version_state="committed"), user="Administrator"
        ) as frappe:
            document = FakeVersion(
                draft=DRAFT,
                version_number=1,
                annotation_file="",
                storage_key=ANNOTATION_KEY,
                state="committed",
            )
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(frappe.PermissionError):
                    frappe_context.require_version_delete(document)
                self.assertTrue(owner.aborted)
                self.assertEqual(owner.reasons, ["protected_cleanup"])
            self.assertFalse(frappe_context.publication_transaction_live())

        with fake_frappe(base_docs(), user="Administrator") as frappe:
            photo_state = types.SimpleNamespace(
                get=lambda fieldname: {
                    "original_file": "FILE-1",
                    "storage_key": ORIGINAL_KEY,
                    "original_sha256": "a" * 64,
                    "original_width": 10,
                    "original_height": 20,
                }[fieldname]
            )
            owner = FakePublicationOwner()
            with _owned(frappe_context, owner):
                with self.assertRaises(frappe.PermissionError):
                    frappe_context.require_original_delete(photo_state)
                self.assertTrue(owner.aborted)
                self.assertEqual(owner.reasons, ["protected_cleanup"])

    def test_unrelated_native_cleanup_is_unchanged(self):
        from clinic_photos.access import frappe_context

        with fake_frappe(base_docs()) as frappe:
            unrelated = FakeFile(
                file_url="/files/public.png", file_name="public.png", is_private=0
            )
            unrelated.fail_after_delete = True
            # No owner, not protected: the native failure propagates untouched and the
            # protected publication state is not latched.
            with self.assertRaises(RuntimeError):
                unrelated.on_trash()
            self.assertIsNone(
                getattr(frappe.local, "clinic_photos_publication_aborted", None)
            )
            self.assertEqual(unrelated.calls, [("on_trash",)])
            self.assertIsNone(frappe_context.current_publication_owner())

            deleted = FakeFile(
                file_url="/files/public.png", file_name="public.png", is_private=0
            )
            deleted.on_trash()
            self.assertEqual(deleted.calls, [("on_trash",)])


# ---------------------------------------------------------------------------
# Fix round 6: the site integration factory registers its TEST-ONLY owner before
# the owner-gated original registration
# ---------------------------------------------------------------------------
class IntegrationFixtureOwnerTests(unittest.TestCase):
    SITE_SUITE = APP_ROOT / "clinic_photos" / "tests" / "test_file_policy.py"

    def test_factory_registers_a_test_only_owner_before_original_insert(self):
        source = self.SITE_SUITE.read_text(encoding="utf-8")

        # The owner is a test fixture created in the class setup, never app code, and
        # the factory enters the owner scope before the owner-gated original
        # registration. Fixtures are now built lazily, so ``setUpClass`` defines the
        # setup owner and every fixture helper is called from inside that scope.
        setup = source[source.index("def setUpClass("):source.index("def tearDownClass(")]
        self.assertIn("cls.setup_owner = _PendingOwner()", setup)
        self.assertNotIn("cls._create_set(", setup)

        factory = source[source.index("def _create_set("):source.index("def _insert_pending_file(")]
        self.assertLess(
            factory.index("owned_publication_transaction("),
            factory.index("_create_set_in_owner_scope("),
        )
        in_scope = source[
            source.index("def _create_set_in_owner_scope("):
            source.index("def _insert_pending_file(")
        ]
        self.assertLess(
            in_scope.index('"doctype": "Clinic Photo"'),
            in_scope.index("photo.insert("),
        )
        # Lazy fixtures are always created through the owner-scoped factory: the scope
        # helper is defined once and called exactly once, from inside the owner scope.
        self.assertEqual(source.count("_create_set_in_owner_scope("), 2)
        self.assertIn("cls._create_set(author,", source)

    def test_fixture_does_not_clear_a_latched_abort(self):
        source = self.SITE_SUITE.read_text(encoding="utf-8")
        for forbidden in (
            "_abort_publication",
            "delattr(frappe.local",
            'setattr(frappe.local, "clinic_photos_publication_aborted"',
            "clinic_photos_publication_aborted = False",
            "clinic_photos_publication_aborted, False",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)



if __name__ == "__main__":
    unittest.main()
