"""Standard-library tests for protected storage identity and lifecycle guards.

These tests never import Frappe. The feature-record lookup is injected, so the
path/canonicalisation/classification contract is exercised in isolation. They do
not prove installed Frappe file naming, dedup behaviour or route handling.
"""

import unittest

from clinic_photos.access.draft_identity import (
    DraftIdentityError,
    assert_draft_update_allowed,
)
from clinic_photos.access.lifecycle import (
    PUBLICATION_OPERATIONS,
    LifecycleError,
    PublicationContext,
    PublicationContextError,
    assert_original_deletable,
    assert_original_record_immutable,
    assert_pending_file_cleanup,
    assert_pending_file_content_allowed,
    assert_version_deletable,
    assert_version_record_immutable,
    normalize_link,
    normalize_version_number,
    require_version_insert,
)
from clinic_photos.access.policy import Actor
from clinic_photos.access.storage import (
    InvalidStoragePath,
    ProtectedAccessDenied,
    ProtectionRef,
    canonical_private_key,
    classify_file,
    is_reserved_key,
    require_protected_read,
    reserved_namespace_name,
)


def actor_for(role, user="clinician@example.test", enabled=True):
    return Actor(
        user=user,
        enabled=enabled,
        clinician=role in {"Physician", "Nursing User"},
    )


ORIGINAL_KEY = "/private/files/clinic-photo-11111111-1111-1111-1111-111111111111.png"
ANNOTATION_KEY = "/private/files/clinic-annotation-22222222-2222-2222-2222-222222222222.json"


def lookup_for(mapping, by_name=None):
    """Build an injectable reference lookup recording ``(key, name)`` calls."""
    calls = []

    def lookup(storage_key, file_name=""):
        calls.append((storage_key, file_name))
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

    lookup.calls = calls
    return lookup


def photo_ref(name="PHOTO-1", committed=True, key=ORIGINAL_KEY):
    return ProtectionRef(
        kind="original",
        author="",
        committed=committed,
        doctype="Clinic Photo",
        name=name,
        draft="",
        storage_key=key,
    )


def version_ref(
    draft="DRAFT-1",
    committed=False,
    author="author@example.test",
    key=ANNOTATION_KEY,
    name=None,
):
    return ProtectionRef(
        kind="annotation",
        author=author,
        committed=committed,
        doctype="Clinic Photo Annotation Version",
        name=name or f"{draft}-v{1 if not committed else 2}",
        draft=draft,
        storage_key=key,
    )


class FakeFileDoc:
    def __init__(self, file_url=None, **fields):
        self.file_url = file_url
        for key, value in fields.items():
            setattr(self, key, value)


# ---------------------------------------------------------------------------
# Canonical private path
# ---------------------------------------------------------------------------
class CanonicalPrivateKeyTests(unittest.TestCase):
    def test_private_path_is_returned_unchanged(self):
        self.assertEqual(canonical_private_key(ORIGINAL_KEY), ORIGINAL_KEY)
        self.assertEqual(canonical_private_key(ANNOTATION_KEY), ANNOTATION_KEY)

    def test_percent_encoding_is_decoded_once(self):
        self.assertEqual(
            canonical_private_key("/private/files/a%20b.png"),
            "/private/files/a b.png",
        )
        self.assertEqual(
            canonical_private_key("/private/files/clinic%2Dphoto-1.png"),
            "/private/files/clinic-photo-1.png",
        )

    def test_non_private_and_non_site_relative_urls_are_rejected(self):
        for value in (
            "",
            "x.png",
            "/files/x.png",
            "/public/files/x.png",
            "/private/other/x.png",
            "/private/files",
            "https://site.test/private/files/x.png",
            "//site.test/private/files/x.png",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidStoragePath):
                    canonical_private_key(value)

    def test_traversal_and_ambiguous_segments_are_rejected(self):
        for value in (
            "/private/files/../x.png",
            "/private/files/a/../../x.png",
            "/private/files/./x.png",
            "/private/files/%2e%2e/x.png",
            "/private/files/a/%2E%2E/b.png",
            "/private/files/a//b.png",
            "/private/files/",
            "/private/files/a%252e%252e.png",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidStoragePath):
                    canonical_private_key(value)

    def test_encoded_separators_null_and_controls_are_rejected(self):
        for value in (
            "/private/files/a%2Fb.png",
            "/private/files/a%2fb.png",
            "/private/files/a%5Cb.png",
            "/private/files/a%00b.png",
            "/private/files/a\\b.png",
            "/private/files/a\x00b.png",
            "/private/files/a\nb.png",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidStoragePath):
                    canonical_private_key(value)

    def test_query_fragment_and_surrounding_whitespace_are_rejected(self):
        for value in (
            "/private/files/a.png?x=1",
            "/private/files/a.png#frag",
            " /private/files/a.png",
            "/private/files/a.png ",
        ):
            with self.subTest(value=value):
                with self.assertRaises(InvalidStoragePath):
                    canonical_private_key(value)

    def test_non_string_input_is_rejected(self):
        for value in (None, b"/private/files/a.png", 1):
            with self.subTest(value=value):
                with self.assertRaises(InvalidStoragePath):
                    canonical_private_key(value)

    def test_reserved_namespace_uses_the_basename_prefix(self):
        self.assertTrue(is_reserved_key(ORIGINAL_KEY))
        self.assertTrue(is_reserved_key(ANNOTATION_KEY))
        self.assertTrue(is_reserved_key("/private/files/sub/clinic-photo-1.png"))
        self.assertFalse(is_reserved_key("/private/files/myclinic-photo-1.png"))
        self.assertFalse(is_reserved_key("/private/files/clinic-photo.png"))
        self.assertFalse(is_reserved_key("/private/files/scan.png"))

    def test_bare_filenames_are_checked_for_the_reserved_prefix(self):
        # Frappe may assign file_url after insert checks; the generated
        # file_name is the fail-closed signal in that interval.
        self.assertTrue(reserved_namespace_name("clinic-photo-1.png"))
        self.assertTrue(reserved_namespace_name("clinic-annotation-1.json"))
        self.assertFalse(reserved_namespace_name("myclinic-photo-1.png"))
        self.assertFalse(reserved_namespace_name("clinic-photo.png"))
        self.assertFalse(reserved_namespace_name(""))
        self.assertFalse(reserved_namespace_name(None))


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------
class ClassifyFileTests(unittest.TestCase):
    def test_registered_original_is_protected_and_immutable(self):
        lookup = lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        record = classify_file(FakeFileDoc(ORIGINAL_KEY), lookup=lookup)

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "original")
        self.assertTrue(record.registered)
        self.assertTrue(record.committed)
        self.assertFalse(record.inconsistent)
        self.assertEqual(record.storage_key, ORIGINAL_KEY)
        self.assertEqual(lookup.calls, [(ORIGINAL_KEY, "")])

    def test_registered_committed_annotation_is_protected(self):
        lookup = lookup_for({ANNOTATION_KEY: (version_ref(committed=True),)})
        record = classify_file({"file_url": ANNOTATION_KEY}, lookup=lookup)

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "annotation")
        self.assertTrue(record.committed)
        self.assertEqual(record.author, "author@example.test")
        self.assertEqual(record.draft, "DRAFT-1")

    def test_pending_registration_is_protected_but_not_committed(self):
        lookup = lookup_for({ANNOTATION_KEY: (version_ref(committed=False),)})
        record = classify_file(FakeFileDoc(ANNOTATION_KEY), lookup=lookup)

        self.assertIsNotNone(record)
        self.assertTrue(record.registered)
        self.assertFalse(record.committed)

    def test_mixed_references_are_protected_inconsistent_and_immutable(self):
        lookup = lookup_for(
            {ORIGINAL_KEY: (photo_ref(), version_ref(committed=False, key=ORIGINAL_KEY))}
        )
        record = classify_file(FakeFileDoc(ORIGINAL_KEY), lookup=lookup)

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "mixed")
        # Any immutable reference governs the shared bytes.
        self.assertTrue(record.committed)
        self.assertTrue(record.inconsistent)
        self.assertEqual(record.author, "")

    def test_shared_url_alias_rows_classify_identically(self):
        lookup = lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        primary = classify_file(FakeFileDoc(ORIGINAL_KEY, name="FILE-1"), lookup=lookup)
        alias = classify_file(FakeFileDoc(ORIGINAL_KEY, name="FILE-2"), lookup=lookup)

        self.assertIsNotNone(primary)
        self.assertEqual(primary, alias)

    def test_persisted_protected_identity_survives_a_mutated_caller_url(self):
        lookup = lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        record = classify_file(
            FakeFileDoc("/private/files/unrelated.png", name="FILE-1"),
            lookup=lookup,
            persisted={"file_url": ORIGINAL_KEY},
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "original")
        self.assertEqual(record.storage_key, ORIGINAL_KEY)
        self.assertTrue(record.inconsistent)

    def test_authoritative_file_reference_protects_a_changed_url(self):
        by_name = {"FILE-REF": (photo_ref(name="PHOTO-9"),)}
        record = classify_file(
            FakeFileDoc("/private/files/scan.png", name="FILE-REF"),
            lookup=lookup_for({}, by_name=by_name),
        )

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "original")
        self.assertTrue(record.inconsistent)

    def test_agreed_caller_and_persisted_identity_is_consistent(self):
        lookup = lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        record = classify_file(
            FakeFileDoc(ORIGINAL_KEY, name="FILE-1"),
            lookup=lookup,
            persisted={"file_url": ORIGINAL_KEY},
        )

        self.assertIsNotNone(record)
        self.assertFalse(record.inconsistent)
        self.assertEqual(record.storage_key, ORIGINAL_KEY)

    def test_hash_equal_different_path_file_is_unrelated(self):
        lookup = lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        record = classify_file(
            FakeFileDoc(
                "/private/files/unrelated.png",
                name="FILE-3",
                content_hash="a" * 64,
            ),
            lookup=lookup,
        )

        self.assertIsNone(record)
        self.assertFalse(
            any("a" * 64 in repr(call) for call in lookup.calls)
        )

    def test_unregistered_reserved_path_is_denied_not_ignored(self):
        lookup = lookup_for({})
        record = classify_file(FakeFileDoc(original_key_for("9999")), lookup=lookup)

        self.assertIsNotNone(record)
        self.assertEqual(record.kind, "unregistered")
        self.assertFalse(record.registered)
        self.assertFalse(record.committed)

    def test_invalid_private_path_fails_closed(self):
        lookup = lookup_for({})
        record = classify_file(
            FakeFileDoc("/private/files/../clinic-photo-9999.png"), lookup=lookup
        )

        self.assertIsNotNone(record)
        self.assertFalse(record.registered)
        self.assertEqual(record.kind, "invalid")

    def test_unrelated_private_and_public_paths_are_not_classified(self):
        lookup = lookup_for({})
        for url in (
            "/private/files/scan.png",
            "/files/clinic-photo-public.png",
            "/private/files/clinic-photo.png",
            "",
            None,
        ):
            with self.subTest(url=url):
                self.assertIsNone(classify_file(FakeFileDoc(url), lookup=lookup))


def original_key_for(suffix):
    return f"/private/files/clinic-photo-{suffix}.png"


# ---------------------------------------------------------------------------
# Read authorisation
# ---------------------------------------------------------------------------
class RequireProtectedReadTests(unittest.TestCase):
    def read(self, file_doc, actor, native_read, mapping, persisted=None):
        return require_protected_read(
            file_doc,
            actor=actor,
            native_read=native_read,
            lookup=lookup_for(mapping),
            persisted=persisted,
        )

    def test_unrelated_file_is_not_restricted(self):
        self.read(
            FakeFileDoc("/private/files/scan.png"),
            actor=actor_for("Guest", user="Guest"),
            native_read=False,
            mapping={},
        )

    def test_enabled_clinician_with_native_read_is_allowed(self):
        for role in ("Physician", "Nursing User"):
            with self.subTest(role=role):
                self.read(
                    FakeFileDoc(ORIGINAL_KEY),
                    actor=actor_for(role),
                    native_read=True,
                    mapping={ORIGINAL_KEY: (photo_ref(),)},
                )

    def test_non_clinician_and_guest_are_denied(self):
        for role in ("Healthcare Administrator", "System Manager", "Guest"):
            with self.subTest(role=role):
                with self.assertRaises(ProtectedAccessDenied):
                    self.read(
                        FakeFileDoc(ORIGINAL_KEY),
                        actor=actor_for(role, user=f"{role}@example.test"),
                        native_read=True,
                        mapping={ORIGINAL_KEY: (photo_ref(),)},
                    )

    def test_revoked_or_disabled_clinician_is_denied(self):
        with self.assertRaises(ProtectedAccessDenied):
            self.read(
                FakeFileDoc(ORIGINAL_KEY),
                actor=actor_for("Physician", enabled=False),
                native_read=True,
                mapping={ORIGINAL_KEY: (photo_ref(),)},
            )

    def test_native_read_denial_is_respected(self):
        with self.assertRaises(ProtectedAccessDenied):
            self.read(
                FakeFileDoc(ORIGINAL_KEY),
                actor=actor_for("Physician"),
                native_read=False,
                mapping={ORIGINAL_KEY: (photo_ref(),)},
            )

    def test_unregistered_reserved_file_is_denied_even_for_clinicians(self):
        with self.assertRaises(ProtectedAccessDenied):
            self.read(
                FakeFileDoc(original_key_for("7777")),
                actor=actor_for("Physician"),
                native_read=True,
                mapping={},
            )

    def test_inconsistent_persisted_identity_is_denied_before_content(self):
        with self.assertRaises(ProtectedAccessDenied):
            self.read(
                FakeFileDoc("/private/files/unrelated.png", name="FILE-1"),
                actor=actor_for("Physician"),
                native_read=True,
                mapping={ORIGINAL_KEY: (photo_ref(),)},
                persisted={"file_url": ORIGINAL_KEY},
            )

    def test_pending_registration_is_readable_but_not_immutable(self):
        self.read(
            FakeFileDoc(ANNOTATION_KEY),
            actor=actor_for("Nursing User"),
            native_read=True,
            mapping={ANNOTATION_KEY: (version_ref(committed=False),)},
        )

    def test_administrator_session_is_denied_ordinary_clinical_read(self):
        with self.assertRaises(ProtectedAccessDenied):
            self.read(
                FakeFileDoc(ORIGINAL_KEY),
                actor=Actor(user="Administrator", enabled=True, clinician=True),
                native_read=True,
                mapping={ORIGINAL_KEY: (photo_ref(),)},
            )


# ---------------------------------------------------------------------------
# Lifecycle value helpers and record guards
# ---------------------------------------------------------------------------
class NormalizationTests(unittest.TestCase):
    def test_absent_links_normalise_to_empty_strings(self):
        self.assertEqual(normalize_link(None), "")
        self.assertEqual(normalize_link(""), "")
        self.assertEqual(normalize_link(" PHOTO-1 "), "PHOTO-1")

    def test_valid_equivalent_version_numbers_normalise(self):
        self.assertEqual(normalize_version_number(0), 0)
        self.assertEqual(normalize_version_number("0"), 0)
        self.assertEqual(normalize_version_number(3), 3)
        self.assertEqual(normalize_version_number("003"), 3)
        self.assertEqual(normalize_version_number(None), 0)

    def test_invalid_version_numbers_are_rejected(self):
        for value in (-1, "-1", 3.5, "3.5", "abc", "", True, False, "1e3"):
            with self.subTest(value=value):
                with self.assertRaises(LifecycleError):
                    normalize_version_number(value)


class DraftPointerNormalizationTests(unittest.TestCase):
    BASE = {
        "author": "author@example.test",
        "owner": "author@example.test",
        "photo": "PHOTO-1",
        "current_version_number": 0,
        "current_version": None,
    }

    def test_absent_and_empty_links_are_equivalent(self):
        assert_draft_update_allowed(
            dict(self.BASE), dict(self.BASE, current_version="")
        )

    def test_equivalent_version_numbers_are_equivalent(self):
        previous = dict(self.BASE, current_version_number=3)
        assert_draft_update_allowed(
            previous, dict(previous, current_version_number="003")
        )

    def test_invalid_version_pointer_values_are_rejected(self):
        previous = dict(self.BASE)
        for value in (-1, "-1", 3.5, "3.5", True):
            with self.subTest(value=value):
                with self.assertRaises(DraftIdentityError):
                    assert_draft_update_allowed(
                        previous, dict(previous, current_version_number=value)
                    )


PHOTO_STATE = {
    "original_file": "FILE-1",
    "storage_key": ORIGINAL_KEY,
    "original_sha256": "a" * 64,
    "original_width": 100,
    "original_height": 200,
}


class OriginalImmutabilityTests(unittest.TestCase):
    def test_unchanged_original_record_is_allowed(self):
        assert_original_record_immutable(
            previous=dict(PHOTO_STATE), current=dict(PHOTO_STATE)
        )

    def test_each_server_owned_original_field_is_immutable(self):
        for fieldname in PHOTO_STATE:
            with self.subTest(fieldname=fieldname):
                current = dict(PHOTO_STATE)
                current[fieldname] = "changed"
                with self.assertRaises(LifecycleError):
                    assert_original_record_immutable(
                        previous=dict(PHOTO_STATE), current=current
                    )

    def test_absent_link_matches_empty_link(self):
        previous = dict(PHOTO_STATE, original_file=None)
        current = dict(PHOTO_STATE, original_file="")
        assert_original_record_immutable(previous=previous, current=current)

    def test_missing_persisted_state_fails_closed(self):
        with self.assertRaises(LifecycleError):
            assert_original_record_immutable(previous=None, current=dict(PHOTO_STATE))
        with self.assertRaises(LifecycleError):
            assert_original_record_immutable(previous=dict(PHOTO_STATE), current=None)

    def test_initial_original_link_registration_requires_the_commit_context(self):
        previous = dict(PHOTO_STATE, original_file="", original_sha256="")
        current = dict(PHOTO_STATE)
        context = PublicationContext(
            actor="author@example.test",
            draft="DRAFT-1",
            operation="commit",
            storage_key=ORIGINAL_KEY,
        )

        with self.assertRaises(LifecycleError):
            assert_original_record_immutable(previous=previous, current=current)
        with self.assertRaises(LifecycleError):
            assert_original_record_immutable(
                previous=previous,
                current=current,
                context=PublicationContext(
                    actor="author@example.test",
                    draft="DRAFT-1",
                    operation="commit",
                    storage_key=original_key_for("4444"),
                ),
            )

        assert_original_record_immutable(
            previous=previous, current=current, context=context
        )

    def test_storage_key_never_changes(self):
        with self.assertRaises(LifecycleError):
            assert_original_record_immutable(
                previous=dict(PHOTO_STATE),
                current=dict(PHOTO_STATE, storage_key=original_key_for("3333")),
            )

    def test_original_deletion_is_always_rejected(self):
        with self.assertRaises(LifecycleError):
            assert_original_deletable(previous=dict(PHOTO_STATE))


VERSION_STATE = {
    "draft": "DRAFT-1",
    "version_number": 2,
    "annotation_file": "FILE-2",
    "storage_key": ANNOTATION_KEY,
    "state": "pending",
}


class VersionLifecycleTests(unittest.TestCase):
    def context(self, operation="commit", draft="DRAFT-1", key=ANNOTATION_KEY):
        return PublicationContext(
            actor="author@example.test",
            draft=draft,
            operation=operation,
            storage_key=key,
        )

    def test_unchanged_pending_version_is_allowed(self):
        assert_version_record_immutable(
            previous=dict(VERSION_STATE), current=dict(VERSION_STATE)
        )

    def test_each_identity_field_is_immutable(self):
        for fieldname in ("draft", "version_number", "annotation_file", "storage_key"):
            with self.subTest(fieldname=fieldname):
                current = dict(VERSION_STATE)
                current[fieldname] = "CHANGED-2" if fieldname != "version_number" else 9
                with self.assertRaises(LifecycleError):
                    assert_version_record_immutable(
                        previous=dict(VERSION_STATE), current=current
                    )

    def test_pending_to_committed_requires_a_matching_bound_context(self):
        committed = dict(VERSION_STATE, state="committed")

        with self.assertRaises(LifecycleError):
            assert_version_record_immutable(
                previous=dict(VERSION_STATE), current=committed
            )
        with self.assertRaises(LifecycleError):
            assert_version_record_immutable(
                previous=dict(VERSION_STATE),
                current=committed,
                context=self.context(draft="OTHER-DRAFT"),
            )
        with self.assertRaises(LifecycleError):
            assert_version_record_immutable(
                previous=dict(VERSION_STATE),
                current=committed,
                context=self.context(operation="pending_cleanup"),
            )

        assert_version_record_immutable(
            previous=dict(VERSION_STATE),
            current=committed,
            context=self.context(),
        )

    def test_committed_version_is_immutable(self):
        committed = dict(VERSION_STATE, state="committed")

        for current in (
            dict(committed, state="pending"),
            dict(committed, storage_key=original_key_for("8888")),
        ):
            with self.subTest(current=current):
                with self.assertRaises(LifecycleError):
                    assert_version_record_immutable(
                        previous=committed,
                        current=current,
                        context=self.context(operation="pending_cleanup"),
                    )

        assert_version_record_immutable(previous=committed, current=dict(committed))

    def test_committed_delete_is_rejected(self):
        with self.assertRaises(LifecycleError):
            assert_version_deletable(
                previous=dict(VERSION_STATE, state="committed"),
                context=self.context(operation="pending_cleanup"),
                draft_author="author@example.test",
            )

    def test_pending_delete_requires_the_cleanup_context(self):
        with self.assertRaises(LifecycleError):
            assert_version_deletable(
                previous=dict(VERSION_STATE),
                draft_author="author@example.test",
            )
        with self.assertRaises(LifecycleError):
            assert_version_deletable(
                previous=dict(VERSION_STATE),
                context=self.context(operation="pending_cleanup"),
                draft_author="other@example.test",
            )

        assert_version_deletable(
            previous=dict(VERSION_STATE),
            context=self.context(operation="pending_cleanup"),
            draft_author="author@example.test",
        )

    def test_version_insert_requires_the_bound_pending_context(self):
        with self.assertRaises(LifecycleError):
            require_version_insert(
                dict(VERSION_STATE), draft_author="author@example.test"
            )

        for context in (
            self.context(operation="pending_cleanup"),
            self.context(operation="pending_insert", draft="OTHER-DRAFT"),
            self.context(
                operation="pending_insert", key=original_key_for("5555")
            ),
        ):
            with self.subTest(context=context):
                with self.assertRaises(LifecycleError):
                    require_version_insert(
                        dict(VERSION_STATE),
                        context,
                        draft_author="author@example.test",
                    )

        require_version_insert(
            dict(VERSION_STATE),
            self.context(operation="pending_insert"),
            draft_author="author@example.test",
        )

    def test_version_insert_rejects_committed_or_unknown_state(self):
        for state in ("committed", "archived"):
            with self.subTest(state=state):
                with self.assertRaises(LifecycleError):
                    require_version_insert(
                        dict(VERSION_STATE, state=state),
                        self.context(operation="pending_insert"),
                        draft_author="author@example.test",
                    )

    def test_version_insert_requires_the_drafting_clinician(self):
        with self.assertRaises(LifecycleError):
            require_version_insert(
                dict(VERSION_STATE),
                self.context(operation="pending_insert"),
                draft_author="",
            )
        with self.assertRaises(LifecycleError):
            require_version_insert(
                dict(VERSION_STATE),
                self.context(operation="pending_insert"),
                draft_author="other@example.test",
            )

    def test_version_insert_rejects_missing_identity_and_public_keys(self):
        for record in (
            dict(VERSION_STATE, draft=None),
            dict(VERSION_STATE, storage_key=""),
            dict(VERSION_STATE, storage_key="/private/files/scan.json"),
        ):
            with self.subTest(record=record):
                with self.assertRaises(LifecycleError):
                    require_version_insert(
                        record,
                        self.context(operation="pending_insert"),
                        draft_author="author@example.test",
                    )


class VersionCommitLinkTests(unittest.TestCase):
    def context(self):
        return PublicationContext(
            actor="author@example.test",
            draft="DRAFT-1",
            operation="commit",
            storage_key=ANNOTATION_KEY,
        )

    def test_empty_file_reference_may_be_registered_on_commit(self):
        previous = dict(VERSION_STATE, annotation_file="")
        current = dict(VERSION_STATE, annotation_file="FILE-2", state="committed")

        with self.assertRaises(LifecycleError):
            assert_version_record_immutable(previous, current)

        assert_version_record_immutable(
            previous,
            current,
            self.context(),
            annotation_file_allowed="FILE-2",
        )

    def test_commit_file_reference_must_match_the_adapter_proof(self):
        previous = dict(VERSION_STATE, annotation_file="")
        current = dict(VERSION_STATE, annotation_file="FILE-2", state="committed")

        for allowed in (None, "", "OTHER-FILE"):
            with self.subTest(allowed=allowed):
                with self.assertRaises(LifecycleError):
                    assert_version_record_immutable(
                        previous,
                        current,
                        self.context(),
                        annotation_file_allowed=allowed,
                    )

    def test_pending_registration_may_still_commit_an_already_linked_file(self):
        previous = dict(VERSION_STATE)
        current = dict(VERSION_STATE, state="committed")
        assert_version_record_immutable(previous, current, self.context())

    def test_committed_file_reference_never_changes(self):
        previous = dict(VERSION_STATE, state="committed")
        current = dict(VERSION_STATE, state="committed", annotation_file="OTHER-FILE")
        with self.assertRaises(LifecycleError):
            assert_version_record_immutable(
                previous,
                current,
                self.context(),
                annotation_file_allowed="OTHER-FILE",
            )


class PendingContentTests(unittest.TestCase):
    def record(self, key=ANNOTATION_KEY, committed=False, draft="DRAFT-1"):
        reference = version_ref(draft=draft, committed=committed, key=key)
        return classify_file(FakeFileDoc(key), lookup=lookup_for({key: (reference,)}))

    def context(self, key=ANNOTATION_KEY, draft="DRAFT-1", actor="author@example.test"):
        return PublicationContext(
            actor=actor,
            draft=draft,
            operation="pending_insert",
            storage_key=key,
        )

    def allow(self, record, **overrides):
        options = {
            "actor_user": "author@example.test",
            "draft_author": "author@example.test",
            "actor_enabled": True,
            "actor_clinician": True,
            "has_existing_bytes": False,
        }
        options.update(overrides)
        return assert_pending_file_content_allowed(
            record, self.context(), **options
        )

    def test_matching_bound_clinician_author_may_consume_first_content(self):
        self.allow(self.record())

    def test_original_first_content_requires_its_own_draft_photo(self):
        record = classify_file(
            FakeFileDoc(ORIGINAL_KEY), lookup=lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        )
        options = {
            "actor_user": "author@example.test",
            "draft_author": "author@example.test",
            "actor_enabled": True,
            "actor_clinician": True,
            "has_existing_bytes": False,
        }
        context = PublicationContext(
            actor="author@example.test",
            draft="DRAFT-1",
            operation="pending_insert",
            storage_key=ORIGINAL_KEY,
        )

        with self.assertRaises(LifecycleError):
            assert_pending_file_content_allowed(record, context, **options)
        with self.assertRaises(LifecycleError):
            assert_pending_file_content_allowed(
                record, context, **dict(options, original_photo_allowed=False)
            )
        assert_pending_file_content_allowed(
            record, context, **dict(options, original_photo_allowed=True)
        )

    def test_existing_bytes_committed_or_inconsistent_records_are_denied(self):
        for record, overrides in (
            (self.record(), {"has_existing_bytes": True}),
            (self.record(committed=True), {}),
            (self.record(draft="DRAFT-2"), {}),
            (
                classify_file(
                    FakeFileDoc(original_key_for("9999")), lookup=lookup_for({})
                ),
                {},
            ),
        ):
            with self.subTest(record=getattr(record, "kind", None), overrides=overrides):
                with self.assertRaises(LifecycleError):
                    self.allow(record, **overrides)

    def test_actor_status_op_and_key_must_all_match(self):
        record = self.record()
        for overrides in (
            {"actor_enabled": False},
            {"actor_clinician": False},
            {"actor_user": "other@example.test"},
            {"draft_author": "other@example.test"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(LifecycleError):
                    self.allow(record, **overrides)

        for context in (
            self.context(key="/private/files/clinic-annotation-9999.json"),
            self.context(draft="DRAFT-2"),
            PublicationContext(
                actor="author@example.test",
                draft="DRAFT-1",
                operation="pending_cleanup",
                storage_key=ANNOTATION_KEY,
            ),
        ):
            with self.subTest(context=context):
                with self.assertRaises(LifecycleError):
                    assert_pending_file_content_allowed(
                        record,
                        context,
                        actor_user="author@example.test",
                        draft_author="author@example.test",
                        actor_enabled=True,
                        actor_clinician=True,
                        has_existing_bytes=False,
                    )

    def test_mixed_immutable_and_pending_references_are_denied(self):
        lookup = lookup_for(
            {
                ORIGINAL_KEY: (
                    photo_ref(),
                    version_ref(committed=False, key=ORIGINAL_KEY),
                )
            }
        )
        record = classify_file(FakeFileDoc(ORIGINAL_KEY), lookup=lookup)
        with self.assertRaises(LifecycleError):
            assert_pending_file_content_allowed(
                record,
                PublicationContext(
                    actor="author@example.test",
                    draft="DRAFT-1",
                    operation="pending_insert",
                    storage_key=ORIGINAL_KEY,
                ),
                actor_user="author@example.test",
                draft_author="author@example.test",
                actor_enabled=True,
                actor_clinician=True,
                has_existing_bytes=False,
            )


class PendingCleanupTests(unittest.TestCase):
    def record(self, committed=False, key=ANNOTATION_KEY, draft="DRAFT-1"):
        reference = version_ref(draft=draft, committed=committed, key=key)
        return classify_file(FakeFileDoc(key), lookup=lookup_for({key: (reference,)}))

    def context(self, key=ANNOTATION_KEY, draft="DRAFT-1", actor="author@example.test"):
        return PublicationContext(
            actor=actor,
            draft=draft,
            operation="pending_cleanup",
            storage_key=key,
        )

    def test_pending_author_may_clean_up_matching_shared_bytes(self):
        assert_pending_file_cleanup(
            self.record(),
            self.context(),
            actor_user="author@example.test",
            draft_author="author@example.test",
            actor_enabled=True,
            actor_clinician=True,
        )

    def test_immutable_or_mixed_references_prevent_cleanup(self):
        original = classify_file(
            FakeFileDoc(ORIGINAL_KEY), lookup=lookup_for({ORIGINAL_KEY: (photo_ref(),)})
        )
        mixed = classify_file(
            FakeFileDoc(ORIGINAL_KEY),
            lookup=lookup_for(
                {
                    ORIGINAL_KEY: (
                        photo_ref(),
                        version_ref(committed=False, key=ORIGINAL_KEY),
                    )
                }
            ),
        )
        for record, key in (
            (self.record(committed=True), ANNOTATION_KEY),
            (original, ORIGINAL_KEY),
            (mixed, ORIGINAL_KEY),
        ):
            with self.subTest(kind=record.kind):
                with self.assertRaises(LifecycleError):
                    assert_pending_file_cleanup(
                        record,
                        self.context(key=key),
                        actor_user="author@example.test",
                        draft_author="author@example.test",
                        actor_enabled=True,
                        actor_clinician=True,
                    )

    def test_other_draft_author_key_or_status_prevents_cleanup(self):
        record = self.record()
        cases = (
            (self.context(draft="DRAFT-2"), "author@example.test"),
            (self.context(actor="other@example.test"), "author@example.test"),
            (self.context(), "other@example.test"),
        )
        for context, draft_author in cases:
            with self.subTest(context=context):
                with self.assertRaises(LifecycleError):
                    assert_pending_file_cleanup(
                        record,
                        context,
                        actor_user="author@example.test",
                        draft_author=draft_author,
                        actor_enabled=True,
                        actor_clinician=True,
                    )

        for overrides in ({"actor_enabled": False}, {"actor_clinician": False}):
            with self.subTest(overrides=overrides):
                with self.assertRaises(LifecycleError):
                    assert_pending_file_cleanup(
                        record,
                        self.context(),
                        actor_user="author@example.test",
                        draft_author="author@example.test",
                        actor_enabled=overrides.get("actor_enabled", True),
                        actor_clinician=overrides.get("actor_clinician", True),
                    )


class PublicationContextTests(unittest.TestCase):
    def test_operations_are_explicit(self):
        self.assertEqual(
            PUBLICATION_OPERATIONS,
            frozenset({"pending_insert", "pending_cleanup", "commit"}),
        )

    def test_valid_context_is_a_frozen_value(self):
        context = PublicationContext(
            actor="author@example.test",
            draft="DRAFT-1",
            operation="pending_insert",
            storage_key=ANNOTATION_KEY,
        )
        self.assertEqual(context.operation, "pending_insert")
        with self.assertRaises(Exception):
            context.operation = "commit"

    def test_invalid_context_is_rejected(self):
        for kwargs in (
            dict(actor="", operation="pending_insert"),
            dict(actor="Guest", operation="pending_insert"),
            dict(actor="Administrator", operation="pending_insert"),
            dict(actor="author@example.test", operation="anything"),
            dict(actor="author@example.test", operation="pending_insert", draft=""),
            dict(
                actor="author@example.test",
                operation="pending_insert",
                storage_key="/private/files/scan.png",
            ),
            dict(
                actor="author@example.test",
                operation="pending_insert",
                storage_key="/private/files/../clinic-photo-1.png",
            ),
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(PublicationContextError):
                    PublicationContext(
                        draft=kwargs.pop("draft", "DRAFT-1"),
                        storage_key=kwargs.pop("storage_key", ANNOTATION_KEY),
                        **kwargs,
                    )


if __name__ == "__main__":
    unittest.main()
