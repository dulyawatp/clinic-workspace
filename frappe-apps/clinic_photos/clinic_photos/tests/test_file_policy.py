"""Focused Frappe integration tests for protected File boundaries.

NOT RUN in Task 2: this host has no installed site or bench. Do not run this module
as one suite; select one case per native execution through the declared runner
(``clinic_photos/tests/run_isolated_cases.py``) once a separately authorized
disposable site exists:

    python3 -m clinic_photos.tests.run_isolated_cases --dry-run
    # execution stays blocked until the bench CLI contract is verified (see runner)

Why one execution per case: a protected denial or failure latches this app's abort
for the whole execution and there is no supported reset inside one. A single
``bench run-tests --app clinic_photos`` execution therefore cannot cover more than
the first aborting case; the second would be denied by the latch, not by the guard
under test. This module therefore declares one case per test method, and
``case_manifest.json`` records for every case whether it aborts, whether a fresh
TEST-ONLY owner is active around the intended guard call, and which guard it must
reach. The runner selects ``--case Class --test bare_method`` in each native execution.

A stale execution (abort already latched, or an owner/context still registered at
case start) is reported as a FAILURE, never as a skip: acceptance requires every
manifest case to pass in its own execution.

The factory follows the approved pending-before-bytes order: register the feature
record/storage key, insert File metadata, consume first content inside the bound
server context, then commit the linkage. ``ignore_permissions`` is used only to
bypass native DocPerm on feature records whose create flags are deliberately 0;
the feature guards still run and still require the publication context. No File
insert or content write uses that flag.

Owner boundary: protected publication requires a registered transaction-owning
caller and this app ships none. ``_PendingOwner`` below only makes the bound
contexts enterable in this suite; it is **not** the durable-publication caller,
proves nothing about it, and must be replaced by the real owner when that caller
exists (it latches an abort exactly like the adapter and never authorizes on its
own). A fresh owner instance is created per case, so no case inherits another
case's aborted state, and no case clears a latched abort.
"""

import contextlib
import functools
import os
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from clinic_photos.access import frappe_context
from clinic_photos.access.file_mixin import ProtectedFileMixin
from clinic_photos.access.frappe_context import (
    current_actor,
    current_publication,
    lookup_protection,
    owned_publication_transaction,
)
from clinic_photos.access.storage import (
    canonical_private_key,
    classify_file,
    is_reserved_key,
)
from clinic_photos.tests.run_isolated_cases import (
    BEGIN_MARKER,
    CASE_NONCE_ENV,
    END_MARKER,
    RESIDUE_MARKER,
    emit_marker,
)

ABORT_ATTR = "clinic_photos_publication_aborted"


# ---------------------------------------------------------------------------
# Runner provenance: emitted by the selected test itself, never by the runner
# ---------------------------------------------------------------------------
def _case_nonce() -> str:
    """Per-invocation nonce supplied by the isolated runner (empty when manual)."""
    return os.environ.get(CASE_NONCE_ENV, "")


def _emit(marker: str, payload: dict) -> None:
    """Delegate to the runner's original-stdout channel (see ``emit_marker``)."""
    emit_marker(marker, payload)


def _case(method):
    """Emit verifiable BEGIN/END evidence for exactly this selected test.

    The identity is unittest's own ``self.id()`` (module.Class.method), so a runner
    cannot relabel an arbitrary pass as the requested case, and the nonce binds the
    evidence to one runner invocation. Running the module by hand produces no
    accepted provenance, which is intentional: acceptance requires the runner.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        nonce = _case_nonce()
        identity = self.id()
        if not nonce:
            self.fail(
                "case evidence requires the isolated runner to supply "
                f"{CASE_NONCE_ENV}"
            )
        _emit(BEGIN_MARKER, {"id": identity, "nonce": nonce})
        try:
            method(self, *args, **kwargs)
        except BaseException:
            _emit(END_MARKER, {"id": identity, "nonce": nonce, "status": "fail"})
            raise
        _emit(END_MARKER, {"id": identity, "nonce": nonce, "status": "pass"})

    return wrapper


class _PendingOwner:
    """Test-only stand-in for the missing parent transaction owner.

    Fresh instance per case; it records the abort reasons it is notified with, which
    is how a case proves it reached the intended lifecycle boundary rather than the
    generic owner gate.
    """

    def __init__(self):
        self.aborted = False
        self.reasons = []

    def publication_aborted(self):
        return self.aborted

    def abort_publication(self, reason):
        self.reasons.append(reason)
        self.aborted = True


@contextlib.contextmanager
def _publication(owner, **kwargs):
    with owned_publication_transaction(owner):
        with frappe_context.bound_publication(**kwargs):
            yield


PHYSICIAN = "clinic-photo-test-physician@example.test"
NURSING = "clinic-photo-test-nursing@example.test"
OUTSIDER = "clinic-photo-test-outsider@example.test"
PHYSICIAN_ROLE = "Physician"
NURSING_ROLE = "Nursing User"
OUTSIDER_ROLE = "Healthcare Administrator"


def _keys(suffix):
    return (
        canonical_private_key(f"/private/files/clinic-photo-{suffix}.png"),
        canonical_private_key(f"/private/files/clinic-annotation-{suffix}.json"),
    )


def _ensure_user(email, role):
    if frappe.db.exists("User", email):
        return
    frappe.get_doc(
        {
            "doctype": "User",
            "email": email,
            "first_name": "Clinic Photo Test",
            "send_welcome_email": 0,
            "roles": [{"role": role}],
        }
    ).insert(ignore_permissions=True)


class ProtectedFileBoundaryTests(FrappeTestCase):
    """Synthetic records only; never real clinical or finance data."""

    # -- execution / fixture lifecycle -----------------------------------
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if getattr(frappe.local, ABORT_ATTR, False):
            raise AssertionError(
                "stale execution: publication abort already latched before setup"
            )
        for role in (PHYSICIAN_ROLE, NURSING_ROLE, OUTSIDER_ROLE):
            if not frappe.db.exists("Role", role):
                # Environment prerequisite, not a tested behaviour. The runner turns
                # this skip into an acceptance FAILURE; nothing here is claimed as
                # covered.
                raise unittest.SkipTest(f"candidate role {role!r} is absent")

        for email, role in (
            (PHYSICIAN, PHYSICIAN_ROLE),
            (NURSING, NURSING_ROLE),
            (OUTSIDER, OUTSIDER_ROLE),
        ):
            _ensure_user(email, role)

        cls.created = []
        cls.storage_keys = []
        cls.sets = {}
        cls.unrelated_file = None
        # Setup-only TEST-ONLY owner. Fixtures are built with it *before* the case
        # owner exists, so the case owner wraps only the intended guard call.
        cls.setup_owner = _PendingOwner()

    def setUp(self):
        super().setUp()
        # Incompatible stale execution is a FAILURE: the runner must start a fresh
        # native execution per case, and a latched abort cannot be cleared here.
        if getattr(frappe.local, ABORT_ATTR, False):
            raise AssertionError(
                "stale execution: publication abort is already latched; select this "
                "case in a fresh native execution"
            )
        if frappe_context.current_publication_owner() is not None:
            raise AssertionError(
                "stale execution: a publication owner is still registered"
            )
        if current_publication() is not None:
            raise AssertionError(
                "stale execution: a publication context is still bound"
            )
        # Fresh TEST-ONLY owner for this case only.
        self.owner = _PendingOwner()

    @classmethod
    def tearDownClass(cls):
        # Ordinary deletion of these synthetic protected rows is deliberately NOT
        # attempted. A protected delete needs a live transaction owner and the abort
        # latch is sticky and never reset here, so a delete at this point would be
        # either refused (leaving the same residue) or act without an owner. Instead
        # the execution reports exactly what it created; the isolated-case runner
        # refuses acceptance until an authorized disposal of that residue is
        # confirmed. Nothing is rolled back or reset by this harness.
        identity = f"{__name__}.{cls.__name__}"
        payload = {
            "id": identity,
            "nonce": _case_nonce(),
            "rows": [list(row) for row in getattr(cls, "created", [])],
            "storage_keys": list(getattr(cls, "storage_keys", [])),
        }
        try:
            _emit(RESIDUE_MARKER, payload)
        finally:
            try:
                cls._remove_non_protected_identities()
            finally:
                # Superclass teardown always runs, even if residue reporting or the
                # non-protected cleanup raised.
                super().tearDownClass()

    @classmethod
    def _remove_non_protected_identities(cls):
        """Remove only harness-owned Users; protected rows are left to disposal."""
        frappe.set_user("Administrator")
        for email in (PHYSICIAN, NURSING, OUTSIDER):
            if frappe.db.exists("User", email):
                frappe.delete_doc("User", email, ignore_permissions=True, force=True)

    @classmethod
    def _track(cls, doctype, name):
        cls.created.append((doctype, name))
        return name

    # -- lazy fixtures (built before the case owner exists) ---------------
    _SET_SUFFIX = {PHYSICIAN: "physician", NURSING: "nursing"}

    @classmethod
    def _ensure_set(cls, author):
        if author in cls.sets:
            return cls.sets[author]
        cls.sets[author] = cls._create_set(author, cls._SET_SUFFIX[author])
        return cls.sets[author]

    @classmethod
    def _ensure_both_sets(cls):
        return [cls._ensure_set(PHYSICIAN), cls._ensure_set(NURSING)]

    @classmethod
    def _ensure_unrelated(cls):
        if cls.unrelated_file is None:
            cls._create_unrelated_file()
        return cls.unrelated_file

    @classmethod
    def _create_set(cls, author, suffix):
        # Only the setup owner is active here; the per-case owner is created later by
        # ``setUp``. The original registration that precedes the first insert is
        # therefore already owner-scoped.
        with owned_publication_transaction(cls.setup_owner):
            return cls._create_set_in_owner_scope(author, suffix)

    @classmethod
    def _create_set_in_owner_scope(cls, author, suffix):
        original_key, annotation_key = _keys(suffix)

        frappe.set_user(author)

        # 1. Register the original storage identity. The File reference is linked
        #    only after the bytes exist, inside the server commit context.
        photo = frappe.get_doc(
            {
                "doctype": "Clinic Photo",
                "patient_reference": "SYNTHETIC-PATIENT",
                "storage_key": original_key,
            }
        )
        photo.insert(ignore_permissions=True)
        cls._track("Clinic Photo", photo.name)

        # 2. The author creates the draft (native clinician create permission).
        draft = frappe.get_doc({"doctype": "Clinic Photo Draft", "photo": photo.name})
        draft.insert()
        cls._track("Clinic Photo Draft", draft.name)

        # 3. Register the pending annotation storage identity before its bytes.
        with _publication(
            cls.setup_owner,
            actor=author,
            draft=draft.name,
            operation="pending_insert",
            storage_key=annotation_key,
        ):
            version = frappe.get_doc(
                {
                    "doctype": "Clinic Photo Annotation Version",
                    "draft": draft.name,
                    "version_number": 1,
                    "storage_key": annotation_key,
                    "state": "pending",
                }
            )
            version.insert(ignore_permissions=True)
        cls._track("Clinic Photo Annotation Version", version.name)

        # 4. Insert protected File metadata, then consume first content, then
        #    persist the resulting hash/url — all inside the same bound context.
        original_file = cls._insert_pending_file(
            author, draft.name, original_key, b"original-bytes"
        )
        annotation_file = cls._insert_pending_file(
            author, draft.name, annotation_key, b"annotation-bytes"
        )

        # 5. Link the immutable original references exactly once in commit context.
        with _publication(
            cls.setup_owner,
            actor=author,
            draft=draft.name,
            operation="commit",
            storage_key=original_key,
        ):
            photo.original_file = original_file.name
            photo.original_sha256 = "0" * 64
            photo.original_width = 1
            photo.original_height = 1
            photo.save(ignore_permissions=True)

        # 6. Commit the annotation version with its matching persisted File.
        with _publication(
            cls.setup_owner,
            actor=author,
            draft=draft.name,
            operation="commit",
            storage_key=annotation_key,
        ):
            version.annotation_file = annotation_file.name
            version.state = "committed"
            version.save(ignore_permissions=True)

        return {
            "author": author,
            "photo": photo.name,
            "draft": draft.name,
            "version": version.name,
            "original_file": original_file.name,
            "annotation_file": annotation_file.name,
        }

    @classmethod
    def _insert_pending_file(cls, author, draft, storage_key, content):
        # Registration precedes the bytes. File metadata is inserted without
        # content; first content is then consumed by the same bound context.
        # ``overwrite=True`` preserves the registered canonical key (native
        # save_file otherwise appends a content-hash suffix), and
        # ``flags.new_file`` is the installed native flag that skips reading the
        # not-yet-written original. Neither flag bypasses the feature guards.
        with _publication(
            cls.setup_owner,
            actor=author,
            draft=draft,
            operation="pending_insert",
            storage_key=storage_key,
        ):
            file_doc = frappe.get_doc(
                {
                    "doctype": "File",
                    "file_name": storage_key.rsplit("/", 1)[-1],
                    "file_url": storage_key,
                    "is_private": 1,
                }
            )
            file_doc.insert()
            file_doc.flags.new_file = True
            file_doc.save_file(content=content, overwrite=True)
            file_doc.save()
        cls._track("File", file_doc.name)
        cls.storage_keys.append(storage_key)
        return file_doc

    @classmethod
    def _create_unrelated_file(cls):
        frappe.set_user(PHYSICIAN)
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "plain-synthetic-notes.txt",
                "is_private": 1,
                "content": b"unrelated-synthetic-bytes",
            }
        )
        file_doc.insert()
        cls._track("File", file_doc.name)
        cls.unrelated_file = file_doc.name

    # -- case helpers ----------------------------------------------------
    def _owner_scope(self):
        """The per-case TEST-ONLY owner around one intended guard call."""
        return owned_publication_transaction(self.owner)

    def _file(self, name):
        return frappe.get_doc("File", name)

    def _assert_denied(self, callable_, *args, **kwargs):
        with self.assertRaises(frappe.PermissionError):
            callable_(*args, **kwargs)

    def _assert_boundary(self, reason, exception, callable_, *args, **kwargs):
        """Denial comes from the intended boundary, not the generic owner gate.

        The owner is live and registered, and it is notified with exactly the abort
        reason of the guard under test (``None`` = the guard denies before entering a
        boundary, so the owner must not be notified and nothing is latched).
        """
        with self.assertRaises(exception):
            callable_(*args, **kwargs)
        self.assertEqual(self.owner.reasons, [] if reason is None else [reason])
        if reason is None:
            self.assertIsNone(
                getattr(frappe.local, ABORT_ATTR, None),
                "a pre-boundary denial must not latch the publication abort",
            )
            self.assertFalse(self.owner.aborted)
        else:
            self.assertIs(getattr(frappe.local, ABORT_ATTR, None), True)
            self.assertTrue(self.owner.aborted)

    def _assert_owner_gate(self, exception, callable_, *args, **kwargs):
        """Separate no-owner case: the gate denies before any lifecycle guard."""
        self.assertIsNone(frappe_context.current_publication_owner())
        with self.assertRaises(exception) as caught:
            callable_(*args, **kwargs)
        self.assertIs(getattr(frappe.local, ABORT_ATTR, None), True)
        return caught.exception

    def _file_row(self, name):
        return frappe.db.get_value(
            "File", name, ["file_url", "is_private", "owner", "content_hash"]
        )

    def _assert_bytes_unchanged(self, values):
        self.assertEqual(
            self._file(values["original_file"]).get_content(encodings=[]),
            b"original-bytes",
        )
        self.assertEqual(
            self._file(values["annotation_file"]).get_content(encodings=[]),
            b"annotation-bytes",
        )

    # -- identity / MRO --------------------------------------------------
    @_case
    def test_reserved_test_keys_stay_in_the_orphan_namespace(self):
        for values in self._ensure_both_sets():
            for fieldname in ("original_file", "annotation_file"):
                self.assertTrue(is_reserved_key(self._file(values[fieldname]).file_url))

    @_case
    def test_installed_file_class_uses_the_protected_mixin(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(PHYSICIAN)
        self.assertIsInstance(self._file(values["original_file"]), ProtectedFileMixin)

    @_case
    def test_administrator_is_not_an_ordinary_clinician(self):
        frappe.set_user("Administrator")
        self.assertFalse(current_actor().clinician)

    # -- read ------------------------------------------------------------
    @_case
    def test_both_clinician_roles_read_both_protected_files(self):
        sets = [
            (PHYSICIAN, self._ensure_set(PHYSICIAN)),
            (NURSING, self._ensure_set(NURSING)),
        ]
        for reader in (PHYSICIAN, NURSING):
            frappe.set_user(reader)
            for author, values in sets:
                with self.subTest(reader=reader, author=author):
                    self.assertEqual(
                        self._file(values["original_file"]).get_content(
                            encodings=[]
                        ),
                        b"original-bytes",
                    )
                    self.assertEqual(
                        self._file(values["annotation_file"]).get_content(
                            encodings=[]
                        ),
                        b"annotation-bytes",
                    )

    @_case
    def test_non_clinician_cannot_download_or_read_protected_content(self):
        sets = self._ensure_both_sets()
        frappe.set_user(OUTSIDER)
        for values in sets:
            for name in (values["original_file"], values["annotation_file"]):
                with self.subTest(name=name):
                    file_doc = self._file(name)
                    self._assert_denied(file_doc.get_content)
                    self._assert_denied(file_doc.is_downloadable)

    @_case
    def test_explicit_file_share_does_not_admit_a_non_clinician(self):
        values = self._ensure_set(PHYSICIAN)
        name = values["original_file"]
        frappe.set_user("Administrator")
        frappe.share.add("File", name, OUTSIDER, read=1)
        try:
            frappe.set_user(OUTSIDER)
            self._assert_denied(self._file(name).get_content)
        finally:
            frappe.set_user("Administrator")
            frappe.share.remove("File", name, OUTSIDER)

    @_case
    def test_disabled_clinician_cannot_read_with_an_existing_session(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(PHYSICIAN)
        frappe.db.set_value("User", PHYSICIAN, "enabled", 0)
        try:
            self._assert_denied(self._file(values["original_file"]).get_content)
        finally:
            frappe.db.set_value("User", PHYSICIAN, "enabled", 1)

    @_case
    def test_non_author_clinician_reads_protected_content(self):
        """Non-author clinician read is admitted (the mutation denials are separate)."""
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(NURSING)
        self.assertEqual(
            self._file(values["original_file"]).get_content(encodings=[]),
            b"original-bytes",
        )
        self.assertEqual(
            self._file(values["annotation_file"]).get_content(encodings=[]),
            b"annotation-bytes",
        )

    # -- metadata --------------------------------------------------------
    @_case
    def test_non_clinician_list_hides_protected_rows_but_not_unrelated_rows(self):
        sets = self._ensure_both_sets()
        unrelated = self._ensure_unrelated()
        frappe.set_user(OUTSIDER)
        names = {row.name for row in frappe.get_list("File", fields=["name"])}
        for values in sets:
            self.assertNotIn(values["original_file"], names)
            self.assertNotIn(values["annotation_file"], names)
        self.assertIn(unrelated, names)

        frappe.set_user(PHYSICIAN)
        names = {row.name for row in frappe.get_list("File", fields=["name"])}
        self.assertIn(sets[0]["original_file"], names)

    @_case
    def test_direct_document_read_is_denied_for_non_clinicians(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(OUTSIDER)
        self._assert_denied(frappe.get_doc, "File", values["original_file"])

    # -- lifecycle: one intended guard per case, fresh owner --------------
    def _mutate_non_author(self, mutation):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["original_file"])
        frappe.set_user(NURSING)
        file_doc = self._file(values["original_file"])
        for fieldname, value in mutation.items():
            setattr(file_doc, fieldname, value)
        return values, before, file_doc

    @_case
    def test_non_author_cannot_change_file_owner(self):
        values, before, file_doc = self._mutate_non_author({"owner": NURSING})
        with self._owner_scope():
            self._assert_boundary(
                "file_finalize",
                frappe.PermissionError,
                file_doc.save,
                ignore_permissions=True,
            )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_non_author_cannot_flip_file_privacy(self):
        values, before, file_doc = self._mutate_non_author({"is_private": 0})
        with self._owner_scope():
            self._assert_boundary(
                "file_finalize",
                frappe.PermissionError,
                file_doc.save,
                ignore_permissions=True,
            )
        self.assertEqual(self._file_row(values["original_file"]), before)

    @_case
    def test_non_author_cannot_move_file_url(self):
        values, before, file_doc = self._mutate_non_author(
            {"file_url": "/private/files/scan-moved.png"}
        )
        with self._owner_scope():
            self._assert_boundary(
                "file_finalize",
                frappe.PermissionError,
                file_doc.save,
                ignore_permissions=True,
            )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_non_author_encoded_url_spelling_denied(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(NURSING)
        file_doc = self._file(values["original_file"])
        # Derive the alternate spelling from the actual stored URL, not from the File
        # docname, and prove it is the SAME storage identity before mutating.
        original_url = file_doc.file_url
        self.assertEqual(canonical_private_key(original_url), original_url)
        encoded = original_url.replace("/clinic-photo-", "/%63linic-photo-", 1)
        self.assertNotEqual(encoded, original_url)
        self.assertEqual(canonical_private_key(encoded), original_url)

        before = self._file_row(values["original_file"])
        self.assertEqual(before[0], original_url)
        file_doc.file_url = encoded
        with self._owner_scope():
            # Denied by the canonical-spelling preflight, before any boundary, so the
            # owner is not notified and nothing is latched.
            self._assert_boundary(
                None,
                frappe.PermissionError,
                file_doc.save,
                ignore_permissions=True,
            )
        after = self._file_row(values["original_file"])
        self.assertEqual(after, before)
        self.assertEqual(after[0], original_url)

    @_case
    def test_author_cannot_replace_protected_content(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["original_file"])
        frappe.set_user(PHYSICIAN)
        file_doc = self._file(values["original_file"])
        with self._owner_scope():
            self._assert_boundary(
                "content_write",
                frappe.PermissionError,
                file_doc.save_file,
                content=b"replacement",
            )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_derivative_thumbnail_denied(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["original_file"])
        frappe.set_user(PHYSICIAN)
        file_doc = self._file(values["original_file"])
        with self._owner_scope():
            self._assert_boundary(
                None, frappe.PermissionError, file_doc.make_thumbnail
            )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_derivative_optimize_denied(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["original_file"])
        frappe.set_user(PHYSICIAN)
        file_doc = self._file(values["original_file"])
        with self._owner_scope():
            self._assert_boundary(
                None, frappe.PermissionError, file_doc.optimize_file
            )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_author_cannot_delete_committed_version(self):
        values = self._ensure_set(PHYSICIAN)
        before = frappe.db.get_value(
            "Clinic Photo Annotation Version",
            values["version"],
            ["state", "annotation_file", "storage_key"],
        )
        frappe.set_user(PHYSICIAN)
        with self._owner_scope():
            self._assert_boundary(
                "protected_cleanup",
                frappe.PermissionError,
                frappe.delete_doc,
                "Clinic Photo Annotation Version",
                values["version"],
                ignore_permissions=True,
            )
        self.assertEqual(
            frappe.db.get_value(
                "Clinic Photo Annotation Version",
                values["version"],
                ["state", "annotation_file", "storage_key"],
            ),
            before,
        )

    @_case
    def test_non_author_cannot_delete_committed_version(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(NURSING)
        with self._owner_scope():
            self._assert_boundary(
                "protected_cleanup",
                frappe.PermissionError,
                frappe.delete_doc,
                "Clinic Photo Annotation Version",
                values["version"],
                ignore_permissions=True,
            )
        self.assertTrue(
            frappe.db.exists("Clinic Photo Annotation Version", values["version"])
        )

    @_case
    def test_author_cannot_delete_committed_annotation_file(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["annotation_file"])
        frappe.set_user(PHYSICIAN)
        with self._owner_scope():
            self._assert_boundary(
                "protected_cleanup",
                frappe.PermissionError,
                frappe.delete_doc,
                "File",
                values["annotation_file"],
                ignore_permissions=True,
            )
        self.assertEqual(self._file_row(values["annotation_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_committed_original_reference_change_denied(self):
        values = self._ensure_set(PHYSICIAN)
        before = frappe.db.get_value(
            "Clinic Photo",
            values["photo"],
            ["original_file", "original_sha256", "storage_key"],
        )
        frappe.set_user(PHYSICIAN)
        photo = frappe.get_doc("Clinic Photo", values["photo"])
        photo.original_sha256 = "f" * 64
        with self._owner_scope():
            # Owner is live, so the immutable-reference guard is reached and maps to
            # native ValidationError (the no-owner case below asserts the gate denial).
            self._assert_boundary(
                "original_finalize",
                frappe.ValidationError,
                photo.save,
                ignore_permissions=True,
            )
        self.assertEqual(
            frappe.db.get_value(
                "Clinic Photo",
                values["photo"],
                ["original_file", "original_sha256", "storage_key"],
            ),
            before,
        )

    @_case
    def test_original_record_delete_denied(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(PHYSICIAN)
        with self._owner_scope():
            self._assert_boundary(
                "protected_cleanup",
                frappe.PermissionError,
                frappe.delete_doc,
                "Clinic Photo",
                values["photo"],
                ignore_permissions=True,
            )
        self.assertTrue(frappe.db.exists("Clinic Photo", values["photo"]))

    # -- separate no-owner cases: the gate denies first -------------------
    @_case
    def test_original_registration_without_owner_denied(self):
        photo = frappe.get_doc(
            {
                "doctype": "Clinic Photo",
                "patient_reference": "SYNTHETIC-PATIENT-NO-OWNER",
                "storage_key": canonical_private_key(
                    "/private/files/clinic-photo-no-owner.png"
                ),
            }
        )
        frappe.set_user(PHYSICIAN)
        self._assert_owner_gate(
            frappe.PermissionError, photo.insert, ignore_permissions=True
        )
        self.assertFalse(frappe.db.exists("Clinic Photo", photo.name))

    @_case
    def test_reserved_file_insert_without_owner_denied(self):
        frappe.set_user(PHYSICIAN)
        key = canonical_private_key("/private/files/clinic-photo-without-context.png")
        file_doc = frappe.get_doc(
            {
                "doctype": "File",
                "file_name": "clinic-photo-without-context.png",
                "file_url": key,
                "is_private": 1,
            }
        )
        self._assert_owner_gate(frappe.PermissionError, file_doc.insert)
        self.assertFalse(frappe.db.exists("File", file_doc.name))

    @_case
    def test_content_write_without_owner_denied(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["original_file"])
        frappe.set_user(PHYSICIAN)
        file_doc = self._file(values["original_file"])
        self._assert_owner_gate(
            frappe.PermissionError, file_doc.save_file, content=b"overwrite"
        )
        self.assertEqual(self._file_row(values["original_file"]), before)
        self._assert_bytes_unchanged(values)

    @_case
    def test_version_delete_without_owner_denied(self):
        values = self._ensure_set(PHYSICIAN)
        frappe.set_user(PHYSICIAN)
        self._assert_owner_gate(
            frappe.PermissionError,
            frappe.delete_doc,
            "Clinic Photo Annotation Version",
            values["version"],
            ignore_permissions=True,
        )
        self.assertTrue(
            frappe.db.exists("Clinic Photo Annotation Version", values["version"])
        )

    @_case
    def test_annotation_file_delete_without_owner_denied(self):
        values = self._ensure_set(PHYSICIAN)
        before = self._file_row(values["annotation_file"])
        frappe.set_user(PHYSICIAN)
        self._assert_owner_gate(
            frappe.PermissionError,
            frappe.delete_doc,
            "File",
            values["annotation_file"],
            ignore_permissions=True,
        )
        self.assertEqual(self._file_row(values["annotation_file"]), before)

    @_case
    def test_original_update_without_owner_denied_by_gate_not_validation(self):
        """The owner gate precedes the immutable-reference guard on this path."""
        values = self._ensure_set(PHYSICIAN)
        before = frappe.db.get_value(
            "Clinic Photo",
            values["photo"],
            ["original_file", "original_sha256", "storage_key"],
        )
        frappe.set_user(PHYSICIAN)
        photo = frappe.get_doc("Clinic Photo", values["photo"])
        photo.original_sha256 = "e" * 64
        error = self._assert_owner_gate(
            frappe.PermissionError, photo.save, ignore_permissions=True
        )
        self.assertNotIsInstance(error, frappe.ValidationError)
        self.assertEqual(
            frappe.db.get_value(
                "Clinic Photo",
                values["photo"],
                ["original_file", "original_sha256", "storage_key"],
            ),
            before,
        )

    # -- unrelated native lifecycle --------------------------------------
    @_case
    def test_unregistered_reserved_metadata_is_not_listed_for_clinicians(self):
        unrelated = self._ensure_unrelated()
        frappe.set_user(PHYSICIAN)
        self.assertIsNone(
            classify_file(
                frappe.get_doc("File", unrelated),
                lookup=lookup_protection,
            )
        )

    @_case
    def test_unrelated_file_lifecycle_is_unchanged(self):
        unrelated = self._ensure_unrelated()
        frappe.set_user(PHYSICIAN)
        file_doc = self._file(unrelated)
        self.assertIsNone(classify_file(file_doc, lookup=lookup_protection))
        self.assertEqual(
            file_doc.get_content(encodings=[]), b"unrelated-synthetic-bytes"
        )
        file_doc.save()

    # -- server context --------------------------------------------------
    @_case
    def test_publication_context_is_request_local_and_not_client_settable(self):
        self.assertIsNone(
            getattr(frappe.local, "clinic_photos_publication_context", None)
        )
        self.assertIsNone(frappe.form_dict.get("clinic_photo_publication"))


if __name__ == "__main__":
    unittest.main()
