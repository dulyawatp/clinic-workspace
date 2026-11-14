"""Standard-library tests for the framework-independent photo access policy.

These tests intentionally do not import Frappe. They verify the pure policy
decisions and the explicit native DocType permission/schema metadata only.
They do not claim an installed Frappe site, effective runtime permissions or
HTTP behavior.
"""

import json
import types
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from clinic_photos.access.draft_identity import (
    SERVER_OWNED_DRAFT_FIELDS,
    DraftIdentityError,
    assert_draft_update_allowed,
    initialize_new_draft,
)
from clinic_photos.access.policy import (
    Actor,
    ProtectedRecord,
    may_edit_draft,
    may_read,
)

APP_ROOT = Path(__file__).resolve().parents[2]
DOCTYPES = APP_ROOT / "clinic_photos" / "clinic_photos" / "doctype"

# Candidate role contract from the approved plan. The installed mapping is
# NOT activated by these tests; only the candidate meaning is documented here.
CANDIDATE_CLINICIAN_ROLES = {
    "Physician": True,
    "Nursing User": True,
    "Healthcare Administrator": False,
    "System Manager": False,
    "Guest": False,
}

PERMISSION_FLAGS = (
    "read",
    "write",
    "create",
    "delete",
    "submit",
    "cancel",
    "amend",
    "report",
    "export",
    "import",
    "share",
    "print",
    "email",
)


def actor_for(role, user="clinician@example.test", enabled=True):
    """Build an actor from a candidate role name, never from Frappe."""
    return Actor(user=user, enabled=enabled, clinician=CANDIDATE_CLINICIAN_ROLES[role])


def load_doctype(doctype_snake):
    path = DOCTYPES / doctype_snake / f"{doctype_snake}.json"
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def permission_rows(document):
    return document["permissions"]


def permission_row(document, role, if_owner=0):
    matches = [
        row
        for row in permission_rows(document)
        if row["role"] == role and row.get("if_owner", 0) == if_owner
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one permission row for role={role!r} "
            f"if_owner={if_owner}, found {len(matches)}"
        )
    return matches[0]


class ActorValueTests(unittest.TestCase):
    def test_actor_and_protected_record_are_frozen_values(self):
        actor = Actor(user="clinician@example.test", enabled=True, clinician=True)
        record = ProtectedRecord(author="clinician@example.test", committed=False)

        with self.assertRaises(FrozenInstanceError):
            actor.enabled = False
        with self.assertRaises(FrozenInstanceError):
            record.committed = True

        self.assertEqual(actor.user, "clinician@example.test")
        self.assertTrue(actor.clinician)
        self.assertFalse(record.committed)


class MayReadTests(unittest.TestCase):
    def test_enabled_clinician_with_native_read_may_read(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                self.assertTrue(may_read(actor_for(role), native_read=True))

    def test_enabled_non_clinician_with_native_read_may_not_read(self):
        for role in ("Healthcare Administrator", "System Manager"):
            with self.subTest(role=role):
                self.assertFalse(may_read(actor_for(role), native_read=True))

    def test_guest_session_with_native_read_may_not_read(self):
        actor = Actor(user="Guest", enabled=True, clinician=False)
        self.assertFalse(may_read(actor, native_read=True))

    def test_disabled_clinician_may_not_read(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                self.assertFalse(
                    may_read(actor_for(role, enabled=False), native_read=True)
                )

    def test_missing_native_read_denies_clinician(self):
        self.assertFalse(may_read(actor_for("Physician"), native_read=False))

    def test_guest_identity_never_reads_even_if_flags_are_wrong(self):
        actor = Actor(user="Guest", enabled=True, clinician=True)
        self.assertFalse(may_read(actor, native_read=True))

    def test_administrator_identity_never_qualifies_as_clinician(self):
        actor = Actor(user="Administrator", enabled=True, clinician=True)
        self.assertFalse(may_read(actor, native_read=True))

    def test_blank_identity_never_reads(self):
        actor = Actor(user="", enabled=True, clinician=True)
        self.assertFalse(may_read(actor, native_read=True))

    def test_disabled_non_clinician_may_not_read(self):
        actor = actor_for("Healthcare Administrator", enabled=False)
        self.assertFalse(may_read(actor, native_read=True))


class MayEditDraftTests(unittest.TestCase):
    def test_enabled_clinician_author_with_native_write_may_edit(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                actor = actor_for(role, user="author@example.test")
                self.assertTrue(
                    may_edit_draft(actor, "author@example.test", native_write=True)
                )

    def test_cross_author_edit_is_denied_for_both_clinician_roles(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                actor = actor_for(role, user="other@example.test")
                self.assertFalse(
                    may_edit_draft(actor, "author@example.test", native_write=True)
                )

    def test_author_without_native_write_may_not_edit(self):
        actor = actor_for("Physician", user="author@example.test")
        self.assertFalse(
            may_edit_draft(actor, "author@example.test", native_write=False)
        )

    def test_disabled_clinician_author_may_not_edit(self):
        actor = actor_for("Nursing User", user="author@example.test", enabled=False)
        self.assertFalse(
            may_edit_draft(actor, "author@example.test", native_write=True)
        )

    def test_non_clinician_author_may_not_edit(self):
        for role in ("Healthcare Administrator", "System Manager"):
            with self.subTest(role=role):
                actor = actor_for(role, user="author@example.test")
                self.assertFalse(
                    may_edit_draft(actor, "author@example.test", native_write=True)
                )

    def test_guest_identity_never_edits_even_if_flags_are_wrong(self):
        actor = Actor(user="Guest", enabled=True, clinician=True)
        self.assertFalse(may_edit_draft(actor, "Guest", native_write=True))

    def test_administrator_identity_never_edits_as_clinician_author(self):
        actor = Actor(user="Administrator", enabled=True, clinician=True)
        self.assertFalse(
            may_edit_draft(actor, "Administrator", native_write=True)
        )

    def test_blank_author_or_blank_actor_is_denied(self):
        actor = Actor(user="", enabled=True, clinician=True)
        self.assertFalse(may_edit_draft(actor, "", native_write=True))
        clinician = actor_for("Physician")
        self.assertFalse(may_edit_draft(clinician, "", native_write=True))


class DraftIdentityTests(unittest.TestCase):
    """Behavioral tests for the pure guards the draft controller delegates to.

    These are local stand-ins for controller decisions, not evidence of native
    Frappe insert/update enforcement.
    """

    session_user = "author@example.test"

    def test_server_owned_draft_fields_are_the_identity_and_version_references(self):
        self.assertEqual(
            set(SERVER_OWNED_DRAFT_FIELDS),
            {"author", "owner", "photo", "current_version_number", "current_version"},
        )

    def test_new_draft_binds_author_and_owner_to_the_session_user(self):
        prepared = initialize_new_draft(
            {
                "author": None,
                "owner": None,
                "photo": "PHOTO-1",
                "current_version_number": 0,
                "current_version": None,
            },
            self.session_user,
        )
        self.assertEqual(prepared["author"], self.session_user)
        self.assertEqual(prepared["owner"], self.session_user)
        self.assertEqual(prepared["photo"], "PHOTO-1")

    def test_new_draft_rewrites_blank_author_and_owner_to_the_session_user(self):
        prepared = initialize_new_draft(
            {"author": "", "owner": ""}, self.session_user
        )
        self.assertEqual(prepared["author"], self.session_user)
        self.assertEqual(prepared["owner"], self.session_user)

    def test_new_draft_rejects_a_forged_author(self):
        with self.assertRaises(DraftIdentityError):
            initialize_new_draft(
                {"author": "someone-else@example.test"}, self.session_user
            )

    def test_new_draft_rejects_a_forged_owner(self):
        with self.assertRaises(DraftIdentityError):
            initialize_new_draft(
                {"owner": "someone-else@example.test"}, self.session_user
            )

    def test_new_draft_initializes_version_pointers(self):
        prepared = initialize_new_draft(
            {"current_version_number": 7, "current_version": "DRAFT-1-v7"},
            self.session_user,
        )
        self.assertEqual(prepared["current_version_number"], 0)
        self.assertIsNone(prepared["current_version"])

    def test_new_draft_rejects_non_clinical_sessions(self):
        for session_user in (None, "", "Guest", "Administrator"):
            with self.subTest(session_user=session_user):
                with self.assertRaises(DraftIdentityError):
                    initialize_new_draft({}, session_user)

    def test_new_draft_does_not_mutate_the_input_mapping(self):
        values = {"author": None, "owner": None, "photo": "PHOTO-1"}
        original = dict(values)

        initialize_new_draft(values, self.session_user)

        self.assertEqual(values, original)

    def test_unchanged_server_owned_fields_are_allowed(self):
        state = {
            "author": self.session_user,
            "owner": self.session_user,
            "photo": "PHOTO-1",
            "current_version_number": 0,
            "current_version": None,
        }
        self.assertIsNone(
            assert_draft_update_allowed(dict(state), dict(state))
        )

    def test_update_rejects_changes_to_each_server_owned_field(self):
        previous = {
            "author": self.session_user,
            "owner": self.session_user,
            "photo": "PHOTO-1",
            "current_version_number": 0,
            "current_version": None,
        }
        changes = {
            "author": "someone-else@example.test",
            "owner": "someone-else@example.test",
            "photo": "PHOTO-2",
            "current_version_number": 1,
            "current_version": "DRAFT-1-v1",
        }
        for fieldname, forged_value in changes.items():
            current = dict(previous)
            current[fieldname] = forged_value
            with self.subTest(fieldname=fieldname):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(dict(previous), current)

    def test_update_fails_closed_without_persisted_state(self):
        current = {
            "author": self.session_user,
            "owner": self.session_user,
            "photo": "PHOTO-1",
            "current_version_number": 0,
            "current_version": None,
        }
        for previous in (None, {}):
            with self.subTest(previous=previous):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(previous, current)


class DraftPointerCommitBindingTests(unittest.TestCase):
    """The publication pointer advance is bound to the exact draft/actor/version."""

    DRAFT = "DRAFT-1"
    KEY = "/private/files/clinic-annotation-00000000000000000000000000000000.json"

    def state(self, **overrides):
        state = {
            "author": self.session_user,
            "owner": self.session_user,
            "photo": "PHOTO-1",
            "current_version_number": 0,
            "current_version": None,
        }
        state.update(overrides)
        return state

    def context(self, **overrides):
        values = {
            "operation": "commit",
            "draft": self.DRAFT,
            "actor": self.session_user,
            "storage_key": self.KEY,
        }
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def setUp(self):
        self.session_user = "author@example.test"
        self.advanced = self.state(current_version_number=1, current_version="DRAFT-1-v1")

    def test_commit_context_allows_exactly_one_advance(self):
        self.assertIsNone(
            assert_draft_update_allowed(
                self.state(),
                self.advanced,
                draft_name=self.DRAFT,
                context=self.context(),
            )
        )

    def test_commit_context_from_another_actor_is_refused(self):
        with self.assertRaises(DraftIdentityError):
            assert_draft_update_allowed(
                self.state(),
                self.advanced,
                draft_name=self.DRAFT,
                context=self.context(actor="someone-else@example.test"),
            )

    def test_non_commit_or_unbound_context_is_refused(self):
        for label, context in (
            ("pending_insert", self.context(operation="pending_insert")),
            ("no_context", None),
            ("other_draft", self.context(draft="DRAFT-9")),
            ("no_draft_on_context", self.context(draft="")),
        ):
            with self.subTest(case=label):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(
                        self.state(),
                        self.advanced,
                        draft_name=self.DRAFT,
                        context=context,
                    )

    def test_skipped_or_unnamed_advances_are_refused(self):
        cases = {
            "skip_two": (self.state(), self.state(current_version_number=2, current_version="DRAFT-1-v2")),
            "number_without_name": (
                self.state(),
                self.state(current_version_number=1, current_version=None),
            ),
            "name_without_number": (
                self.state(),
                self.state(current_version_number=0, current_version="DRAFT-1-v1"),
            ),
            "other_draft_name": (
                self.state(),
                self.state(current_version_number=1, current_version="DRAFT-9-v1"),
            ),
            "wrong_series_name": (
                self.state(),
                self.state(current_version_number=1, current_version="DRAFT-1-v2"),
            ),
        }
        for label, (previous, current) in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(
                        previous,
                        current,
                        draft_name=self.DRAFT,
                        context=self.context(),
                    )

    def test_commit_context_does_not_widen_author_or_photo(self):
        for fieldname, forged in (
            ("author", "someone-else@example.test"),
            ("owner", "someone-else@example.test"),
            ("photo", "PHOTO-2"),
        ):
            with self.subTest(fieldname=fieldname):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(
                        self.state(),
                        self.advanced | {fieldname: forged},
                        draft_name=self.DRAFT,
                        context=self.context(),
                    )

    def test_missing_persisted_author_fails_closed_inside_a_commit_context(self):
        previous = self.state(author=None)
        with self.assertRaises(DraftIdentityError):
            assert_draft_update_allowed(
                previous,
                self.advanced,
                draft_name=self.DRAFT,
                context=self.context(),
            )

    def test_no_context_still_refuses_every_ordinary_pointer_change(self):
        with self.assertRaises(DraftIdentityError):
            assert_draft_update_allowed(
                self.state(), self.advanced, draft_name=self.DRAFT
            )


class SchemaPermissionTests(unittest.TestCase):
    def test_every_permission_row_lists_all_flags_explicitly(self):
        for doctype in (
            "clinic_photo",
            "clinic_photo_draft",
            "clinic_photo_annotation_version",
        ):
            document = load_doctype(doctype)
            with self.subTest(doctype=document["name"]):
                self.assertTrue(permission_rows(document))
                for row in permission_rows(document):
                    for flag in PERMISSION_FLAGS:
                        with self.subTest(role=row["role"], flag=flag, doctype=doctype):
                            self.assertIn(flag, row)
                            self.assertIn(row[flag], (0, 1))

    def test_permissions_name_only_candidate_clinician_roles(self):
        for doctype in (
            "clinic_photo",
            "clinic_photo_draft",
            "clinic_photo_annotation_version",
        ):
            document = load_doctype(doctype)
            roles = {row["role"] for row in permission_rows(document)}
            with self.subTest(doctype=document["name"]):
                self.assertLessEqual(roles, {"Physician", "Nursing User"})

    def test_photo_and_version_are_not_client_creatable_or_writable(self):
        for doctype in ("clinic_photo", "clinic_photo_annotation_version"):
            document = load_doctype(doctype)
            for role in ("Physician", "Nursing User"):
                row = permission_row(document, role)
                with self.subTest(doctype=doctype, role=role):
                    self.assertEqual(row["read"], 1)
                    for flag in (
                        "write",
                        "create",
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
                        self.assertEqual(row[flag], 0)

    def test_general_clinicians_read_and_create_drafts_only(self):
        document = load_doctype("clinic_photo_draft")
        for role in ("Physician", "Nursing User"):
            row = permission_row(document, role)
            with self.subTest(role=role):
                self.assertEqual(row["read"], 1)
                self.assertEqual(row["create"], 1)
                for flag in (
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
                    self.assertEqual(row[flag], 0)

    def test_owner_rows_only_add_draft_write_for_the_author(self):
        document = load_doctype("clinic_photo_draft")
        for role in ("Physician", "Nursing User"):
            row = permission_row(document, role, if_owner=1)
            with self.subTest(role=role):
                self.assertEqual(row["read"], 1)
                self.assertEqual(row["write"], 1)
                for flag in (
                    "create",
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
                    self.assertEqual(row[flag], 0)

    def test_no_if_owner_rows_on_photo_or_version(self):
        for doctype in ("clinic_photo", "clinic_photo_annotation_version"):
            document = load_doctype(doctype)
            with self.subTest(doctype=doctype):
                self.assertFalse(
                    [row for row in permission_rows(document) if row.get("if_owner")]
                )

    def test_server_owned_storage_fields_are_read_only(self):
        expected = {
            "clinic_photo": (
                "original_file",
                "storage_key",
                "original_sha256",
                "original_width",
                "original_height",
            ),
            "clinic_photo_draft": (
                "author",
                "photo",
                "current_version_number",
                "current_version",
            ),
            "clinic_photo_annotation_version": (
                "draft",
                "version_number",
                "annotation_file",
                "storage_key",
                "state",
            ),
        }
        for doctype, fieldnames in expected.items():
            document = load_doctype(doctype)
            fields = {field["fieldname"]: field for field in document["fields"]}
            for fieldname in fieldnames:
                with self.subTest(doctype=doctype, field=fieldname):
                    self.assertIn(fieldname, fields)
                    self.assertEqual(fields[fieldname].get("read_only"), 1)

    def test_annotation_versions_are_unique_per_draft_and_version_number(self):
        document = load_doctype("clinic_photo_annotation_version")
        self.assertEqual(document["autoname"], "format:{draft}-v{version_number}")

    def test_format_autoname_pairs_with_old_style_naming_rule(self):
        for doctype in (
            "clinic_photo",
            "clinic_photo_draft",
            "clinic_photo_annotation_version",
        ):
            document = load_doctype(doctype)
            with self.subTest(doctype=doctype):
                self.assertIn("naming_rule", document)
                if document["autoname"].startswith("format:"):
                    self.assertEqual(
                        document["naming_rule"], "Expression (old style)"
                    )

    def test_renaming_is_disabled_on_all_doctypes(self):
        for doctype in (
            "clinic_photo",
            "clinic_photo_draft",
            "clinic_photo_annotation_version",
        ):
            document = load_doctype(doctype)
            with self.subTest(doctype=doctype):
                self.assertIn("allow_rename", document)
                self.assertEqual(document["allow_rename"], 0)

    def test_annotation_version_state_options_are_exactly_pending_committed(self):
        document = load_doctype("clinic_photo_annotation_version")
        fields = {field["fieldname"]: field for field in document["fields"]}
        self.assertEqual(fields["state"]["options"], "pending\ncommitted")

    def test_every_permission_row_uses_permlevel_zero(self):
        for doctype in (
            "clinic_photo",
            "clinic_photo_draft",
            "clinic_photo_annotation_version",
        ):
            document = load_doctype(doctype)
            for row in permission_rows(document):
                with self.subTest(doctype=doctype, role=row["role"]):
                    self.assertIn("permlevel", row)
                    self.assertEqual(row["permlevel"], 0)

    def test_draft_author_field_metadata_requests_create_only_assignment(self):
        # Metadata only: actual binding/rejection is enforced by the draft
        # controller guards, covered behaviorally in DraftIdentityTests.
        document = load_doctype("clinic_photo_draft")
        fields = {field["fieldname"]: field for field in document["fields"]}
        self.assertEqual(fields["author"].get("read_only"), 1)
        self.assertEqual(fields["author"].get("set_only_once"), 1)


if __name__ == "__main__":
    unittest.main()
