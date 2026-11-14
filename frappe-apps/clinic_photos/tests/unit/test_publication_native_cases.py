"""Local helper/source controls for the authored native publication cases.

These tests are **not** native acceptance and they do not publish anything. They drive
the test-side helpers of ``clinic_photos.tests.test_publication`` at the framework
boundary only:

* the real contract validator builds the requests the cases actually submit (kind
  spelling, canonical annotation envelope, strict image transport, request-key bounds);
* the prerequisite decision refuses before any write, and both gates must be the
  boolean ``True``;
* ``run_child`` delegates exactly the fixed argv/cwd/timeout of the supported child;
* residue recording inventories the operation/domain/File/key an outcome owns.

The native cases themselves remain UNRUN: this environment has no approved site, no
bench interpreter, no installed Task-6 schema and no synthetic fixture. A passing run
here says the authored source and its helpers are coherent; it says nothing about
publication.
"""

import ast
import contextlib
import fcntl
import hashlib
import io
import json
import os
import pathlib
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from clinic_photos.access import frappe_context
from clinic_photos.publication import contracts, inspection, submission
from clinic_photos.tests import revocation_probe as probe
from clinic_photos.tests import run_isolated_cases as runner
from clinic_photos.tests import test_publication as suite

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = APP_ROOT / "clinic_photos" / "tests" / "test_publication.py"
MANIFEST_PATH = APP_ROOT / "clinic_photos" / "tests" / "case_manifest.json"

NONCE = "0123456789abcdef0123456789abcdef"

FIXTURE_CASES = {
    "pub.original.first_jpeg_mro_and_get_content": (
        "PublicationOriginalNativeCases",
        "test_first_jpeg_original_uses_protected_mro_and_get_content",
    ),
    "pub.access.replay_after_newer_version": (
        "PublicationAccessNativeCases",
        "test_replay_after_newer_version_returns_immutable_version",
    ),
    "pub.access.lost_response_replay": (
        "PublicationAccessNativeCases",
        "test_lost_response_replay_resolves_original_tuple",
    ),
    "pub.access.allowlist_cross_actor_audit": (
        "PublicationAccessNativeCases",
        "test_operator_allowlist_cross_actor_audit_is_denied",
    ),
}


def _satisfied():
    return {
        "site": suite.APPROVED_SITE,
        "publication_gate": True,
        "synthetic_mode": True,
        "bench_interpreter": True,
        "actor_exists": True,
        "actor_enabled": True,
        "actor_clinician": True,
        "patient_exists": True,
        "patient_read": True,
        "missing_operation_columns": [],
    }


def _native_body(class_name, method_name):
    """The parsed function node of one authored native case body."""
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for child in node.body:
                if isinstance(child, ast.FunctionDef) and child.name == method_name:
                    return child
    raise AssertionError(f"{class_name}.{method_name} not found")


def _called_attributes(node):
    return {
        call.func.attr
        for call in ast.walk(node)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
    }


def _decorator_names(node):
    return {
        decorator.func.id
        if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Name)
        else getattr(decorator, "id", "")
        for decorator in node.decorator_list
    }


class RequestConstructionTests(unittest.TestCase):
    """The bodies build their requests with the real contract, not a hand shape."""

    def _original_request(self):
        data, width, height = suite.synthetic_jpeg_bytes(NONCE, "orig")
        payload = {
            "kind": contracts.SYNTHETIC_ORIGINAL_CREATE,
            "request_key": suite.request_key(NONCE, "orig"),
            "synthetic_patient": suite.SYNTHETIC_PATIENT,
            "image_base64": __import__("base64").b64encode(data).decode("ascii"),
        }
        return data, width, height, contracts.validate_request(payload)

    def test_synthetic_original_kind_is_the_source_contract_constant(self):
        self.assertEqual(contracts.SYNTHETIC_ORIGINAL_CREATE, "synthetic_original_create")
        data, _, _, request = self._original_request()
        self.assertEqual(request.kind, contracts.SYNTHETIC_ORIGINAL_CREATE)
        self.assertEqual(request.image_bytes, data)
        self.assertEqual(request.synthetic_patient, suite.SYNTHETIC_PATIENT)
        self.assertIsNone(request.draft)

    def test_the_original_payload_is_a_real_jpeg_not_a_relabelled_png(self):
        data, width, height, request = self._original_request()
        # JPEG SOI marker; a PNG signature here would mean the case lies about its format.
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        self.assertNotEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual((width, height), (8, 8))
        self.assertGreater(len(request.image_bytes), 0)

    def test_annotation_request_stores_the_canonical_envelope_text(self):
        request = contracts.validate_request(
            {
                "kind": contracts.ANNOTATION_APPEND,
                "request_key": suite.request_key(NONCE, "v1"),
                "draft": "DRAFT-1",
                "expected_version": 0,
                "original": "PHOTO-1",
                "annotation": suite.synthetic_annotation(NONCE, "v1"),
            }
        )
        parsed = json.loads(request.annotation)
        self.assertEqual(
            set(parsed), {"schema_version", "coordinate_space", "shapes"}
        )
        self.assertEqual(request.annotation, json.dumps(parsed, sort_keys=True, separators=(",", ":")))
        self.assertEqual(request.expected_version, 0)

    def test_the_recon_field_name_annotation_json_is_rejected(self):
        # The contract field is ``annotation``; a body that sent the recon spelling
        # would be refused as an unknown key rather than stored.
        with self.assertRaises(contracts.PublicationValidationError):
            contracts.validate_request(
                {
                    "kind": contracts.ANNOTATION_APPEND,
                    "request_key": suite.request_key(NONCE, "v1"),
                    "draft": "DRAFT-1",
                    "expected_version": 0,
                    "original": "PHOTO-1",
                    "annotation_json": suite.synthetic_annotation(NONCE, "v1"),
                }
            )

    def test_request_keys_are_ascii_bounded_and_nonce_scoped(self):
        for label in ("orig", "base", "lost", "v1", "v2"):
            key = suite.request_key(NONCE, label)
            self.assertRegex(key, r"^[A-Za-z0-9_-]{16,128}$")
            self.assertTrue(key.isascii())
            self.assertIn(NONCE, key)

    def test_two_labels_never_share_image_bytes_or_annotation_identity(self):
        first = suite.synthetic_jpeg_bytes(NONCE, "orig")[0]
        second = suite.synthetic_jpeg_bytes(NONCE, "lost")[0]
        self.assertNotEqual(first, second)
        self.assertNotEqual(
            suite.synthetic_annotation(NONCE, "v1")["shapes"][0]["id"],
            suite.synthetic_annotation(NONCE, "v2")["shapes"][0]["id"],
        )


class PreconditionTests(unittest.TestCase):
    """Refusal is decided before staging, and both gates must be exactly True."""

    def test_a_fully_satisfied_observation_has_no_failures(self):
        self.assertEqual(suite.publication_precondition_failures(_satisfied()), [])

    def test_both_gates_must_be_the_boolean_true(self):
        for key in ("publication_gate", "synthetic_mode"):
            for value in ("1", 1, "true", None, False):
                with self.subTest(field=key, value=value):
                    observed = _satisfied()
                    observed[key] = value
                    self.assertEqual(
                        suite.publication_precondition_failures(observed), [key]
                    )

    def test_every_missing_prerequisite_is_named_in_order(self):
        observed = {
            "site": "other-site.invalid",
            "publication_gate": False,
            "synthetic_mode": False,
            "bench_interpreter": False,
            "actor_exists": False,
            "patient_exists": False,
            "patient_read": False,
            "missing_operation_columns": ["generation"],
        }
        self.assertEqual(
            suite.publication_precondition_failures(observed),
            [
                "approved_site",
                "publication_gate",
                "synthetic_mode",
                "bench_interpreter",
                "clinician_actor",
                "synthetic_patient",
                "patient_read",
                "operation_schema",
            ],
        )

    def test_a_disabled_or_non_clinician_actor_is_refused(self):
        for key in ("actor_enabled", "actor_clinician"):
            with self.subTest(field=key):
                observed = _satisfied()
                observed[key] = False
                self.assertEqual(
                    suite.publication_precondition_failures(observed),
                    ["clinician_actor"],
                )

    def test_missing_operation_columns_are_the_declared_outcome_columns(self):
        installed = [("name",), ("reservation_key",), ("generation",)]
        missing = suite.missing_operation_columns(installed)
        self.assertNotIn("reservation_key", missing)
        self.assertNotIn("generation", missing)
        self.assertEqual(
            missing,
            sorted(
                {
                    "expected_sha256",
                    "expected_size",
                    "published_file",
                    "published_domain_doctype",
                    "published_domain_name",
                    "published_version",
                    "creation_identity",
                }
            ),
        )
        self.assertEqual(
            suite.missing_operation_columns([]), sorted(suite.REQUIRED_OPERATION_COLUMNS)
        )


class ObserverPreconditionTests(unittest.TestCase):
    """The second clinician is explicit, distinct, enabled and not an operator."""

    OBSERVER = "native-probe-observer@example.invalid"

    def _observed(self, **extra):
        observed = {
            "author": suite.SYNTHETIC_CLINICIAN,
            "observer": self.OBSERVER,
            "observer_exists": True,
            "observer_enabled": True,
            "observer_clinician": True,
            "operator_allowlist": None,
        }
        observed.update(extra)
        return observed

    def test_an_absent_or_empty_allowlist_is_valid(self):
        for allowlist in (None, [], ()):
            with self.subTest(allowlist=allowlist):
                self.assertEqual(
                    suite.observer_precondition_failures(
                        self._observed(operator_allowlist=allowlist)
                    ),
                    [],
                )

    def test_a_nonempty_valid_allowlist_excluding_the_observer_is_valid(self):
        self.assertEqual(
            suite.observer_precondition_failures(
                self._observed(operator_allowlist=["operator@example.test"])
            ),
            [],
        )

    def test_a_missing_or_shared_observer_identity_is_refused(self):
        for observer, expected in (
            ("", ["observer_identity"]),
            (suite.SYNTHETIC_CLINICIAN, ["observer_distinct"]),
        ):
            with self.subTest(observer=observer):
                self.assertEqual(
                    suite.observer_precondition_failures(
                        self._observed(observer=observer)
                    ),
                    expected,
                )

    def test_an_unavailable_disabled_or_non_clinician_observer_is_refused(self):
        for field in ("observer_exists", "observer_enabled", "observer_clinician"):
            with self.subTest(field=field):
                self.assertEqual(
                    suite.observer_precondition_failures(
                        self._observed(**{field: False})
                    ),
                    ["observer_actor"],
                )

    def test_a_malformed_allowlist_is_refused_rather_than_masking_the_proof(self):
        for allowlist in (
            "observer@example.test",
            {"observer": True},
            [self.OBSERVER, 5],
            [None],
        ):
            with self.subTest(allowlist=allowlist):
                self.assertEqual(
                    suite.observer_precondition_failures(
                        self._observed(operator_allowlist=allowlist)
                    ),
                    ["operator_allowlist"],
                )

    def test_an_observer_that_is_an_operator_is_refused(self):
        self.assertEqual(
            suite.observer_precondition_failures(
                self._observed(operator_allowlist=[self.OBSERVER])
            ),
            ["observer_is_operator"],
        )


class _SessionFrappe:
    def __init__(self, user):
        self.session = type("Session", (), {"user": user})()
        self.calls = []

    def set_user(self, user):
        self.calls.append(user)
        self.session.user = user


class ActingAsSessionTests(unittest.TestCase):
    """The observer switch always restores the original session, even on failure."""

    class _Self:
        def __init__(self, user="author@example.test"):
            self.frappe = _SessionFrappe(user)
            self.actor = user

    def _bound(self, stub):
        return suite.PublicationNativeCase.acting_as.__get__(stub, type(stub))

    def test_the_session_is_restored_after_the_block(self):
        stub = self._Self()
        with self._bound(stub)("observer@example.test"):
            self.assertEqual(stub.frappe.session.user, "observer@example.test")
        self.assertEqual(stub.frappe.session.user, "author@example.test")
        self.assertEqual(
            stub.frappe.calls, ["observer@example.test", "author@example.test"]
        )

    def test_the_session_is_restored_when_the_block_raises(self):
        stub = self._Self()
        with self.assertRaises(RuntimeError):
            with self._bound(stub)("observer@example.test"):
                raise RuntimeError("denial path failed")
        self.assertEqual(stub.frappe.session.user, "author@example.test")
        self.assertEqual(stub.frappe.calls[-1], "author@example.test")


class _ObserverDb:
    def __init__(self, exists=True, stored=None):
        self._exists = exists
        self._stored = dict(stored or {})

    def exists(self, doctype, name):
        return self._exists and bool(name)

    def get_value(self, doctype, name, fieldname):
        if name in self._stored:
            return self._stored[name]
        return name if self._exists and isinstance(name, str) else None


class _ObserverFrappe:
    """The narrow ``frappe`` surface the observer prerequisite helper reads."""

    def __init__(self, *, exists=True, allowlist=None, stored=None):
        self.conf = {}
        if allowlist is not None:
            self.conf[inspection.RECOVERY_OPERATOR_CONFIG_KEY] = allowlist
        self.session = type("Session", (), {"user": suite.SYNTHETIC_CLINICIAN})()
        self.calls = []
        self.db = _ObserverDb(exists, stored)

    def set_user(self, user):
        self.calls.append(user)
        self.session.user = user


class ObserverPrerequisiteHelperTests(unittest.TestCase):
    """The helper measures the observer read-only and fails before staging."""

    OBSERVER = "native-probe-observer@example.invalid"

    class Harness(suite.PublicationNativeCase):
        def __init__(self, frappe):
            super().__init__("runTest")
            self.frappe = frappe
            self.actor = suite.SYNTHETIC_CLINICIAN

        def runTest(self):
            pass

        def require_bench_environment(self):
            return None

    def _actor(self, frappe, enabled=True, clinician=True):
        return frappe_context.Actor(
            user=frappe.session.user, enabled=enabled, clinician=clinician
        )

    def _patched(self, frappe, **actor):
        return (
            mock.patch.object(suite, "SYNTHETIC_OBSERVER", self.OBSERVER),
            mock.patch.object(
                frappe_context,
                "current_actor",
                side_effect=lambda: self._actor(frappe, **actor),
            ),
        )

    def test_the_helper_returns_the_observer_and_restores_the_author(self):
        frappe = _ObserverFrappe()
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe)
        with observer_patch, actor_patch:
            observer = harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertEqual(observer, self.OBSERVER)
        self.assertEqual(frappe.session.user, suite.SYNTHETIC_CLINICIAN)
        self.assertEqual(frappe.calls, [self.OBSERVER, suite.SYNTHETIC_CLINICIAN])

    def test_a_disabled_or_non_clinician_observer_fails_before_any_write(self):
        for actor in ({"enabled": False}, {"clinician": False}):
            with self.subTest(actor=actor):
                frappe = _ObserverFrappe()
                harness = self.Harness(frappe)
                observer_patch, actor_patch = self._patched(frappe, **actor)
                with observer_patch, actor_patch:
                    with self.assertRaises(AssertionError) as caught:
                        harness.require_observer_prerequisite(
                            suite.SYNTHETIC_CLINICIAN
                        )
                self.assertIn("observer_actor", str(caught.exception))
                self.assertEqual(frappe.session.user, suite.SYNTHETIC_CLINICIAN)

    def test_a_missing_observer_is_refused_without_switching_session(self):
        frappe = _ObserverFrappe(
            exists=False,
            stored={
                suite.SYNTHETIC_CLINICIAN: suite.SYNTHETIC_CLINICIAN,
                self.OBSERVER: self.OBSERVER,
            },
        )
        harness = self.Harness(frappe)
        with mock.patch.object(suite, "SYNTHETIC_OBSERVER", self.OBSERVER):
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertIn("observer_actor", str(caught.exception))
        self.assertEqual(frappe.calls, [])

    def test_an_unset_observer_identity_is_refused(self):
        frappe = _ObserverFrappe()
        harness = self.Harness(frappe)
        with mock.patch.object(suite, "SYNTHETIC_OBSERVER", ""):
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertIn("observer_identity", str(caught.exception))

    def test_a_shared_observer_identity_is_refused(self):
        frappe = _ObserverFrappe()
        harness = self.Harness(frappe)
        with mock.patch.object(
            suite, "SYNTHETIC_OBSERVER", suite.SYNTHETIC_CLINICIAN
        ), mock.patch.object(
            frappe_context,
            "current_actor",
            side_effect=lambda: self._actor(frappe),
        ):
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertIn("observer_distinct", str(caught.exception))

    def test_a_malformed_allowlist_is_refused(self):
        frappe = _ObserverFrappe(allowlist="not-a-list")
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertIn("operator_allowlist", str(caught.exception))

    def test_an_observer_that_is_an_operator_is_refused(self):
        frappe = _ObserverFrappe(allowlist=[self.OBSERVER])
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertIn("observer_is_operator", str(caught.exception))

    def test_a_failure_while_measuring_restores_the_author(self):
        frappe = _ObserverFrappe()
        harness = self.Harness(frappe)
        with mock.patch.object(
            suite, "SYNTHETIC_OBSERVER", self.OBSERVER
        ), mock.patch.object(
            frappe_context, "current_actor", side_effect=RuntimeError("unreadable")
        ):
            with self.assertRaises(RuntimeError):
                harness.require_observer_prerequisite(suite.SYNTHETIC_CLINICIAN)
        self.assertEqual(frappe.session.user, suite.SYNTHETIC_CLINICIAN)


class CanonicalObserverIdentityTests(unittest.TestCase):
    """Stored User.name is authority; a case-variant alias is not a second actor."""

    AUTHOR = suite.SYNTHETIC_CLINICIAN
    OBSERVER = "native-probe-observer@example.invalid"

    class Harness(ObserverPrerequisiteHelperTests.Harness):
        pass

    def _actor(self, frappe, enabled=True, clinician=True):
        return frappe_context.Actor(
            user=frappe.session.user, enabled=enabled, clinician=clinician
        )

    def _patched(self, frappe, observer):
        return (
            mock.patch.object(suite, "SYNTHETIC_OBSERVER", observer),
            mock.patch.object(
                frappe_context,
                "current_actor",
                side_effect=lambda: self._actor(frappe),
            ),
        )

    def test_a_case_variant_observer_of_the_author_is_refused(self):
        variant = self.AUTHOR.upper()
        frappe = _ObserverFrappe(
            stored={self.AUTHOR: self.AUTHOR, variant: self.AUTHOR}
        )
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, variant)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(self.AUTHOR)
        self.assertIn("observer_distinct", str(caught.exception))
        self.assertNotIn(self.AUTHOR, str(caught.exception))
        self.assertNotIn(variant, str(caught.exception))
        self.assertEqual(frappe.session.user, self.AUTHOR)

    def test_a_distinct_alias_returns_the_canonical_observer(self):
        alias = self.OBSERVER.upper()
        frappe = _ObserverFrappe(
            stored={self.OBSERVER: self.OBSERVER, alias: self.OBSERVER}
        )
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, alias)
        with observer_patch, actor_patch:
            observer = harness.require_observer_prerequisite(self.AUTHOR)
        self.assertEqual(observer, self.OBSERVER)
        self.assertEqual(frappe.session.user, self.AUTHOR)
        self.assertEqual(frappe.calls, [self.OBSERVER, self.AUTHOR])

    def test_an_alias_of_an_allowlisted_operator_is_refused(self):
        operator = "operator@example.test"
        alias = operator.upper()
        frappe = _ObserverFrappe(
            allowlist=[operator], stored={operator: operator, alias: operator}
        )
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, alias)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(self.AUTHOR)
        self.assertIn("observer_is_operator", str(caught.exception))

    def test_an_unresolved_observer_identity_is_refused(self):
        frappe = _ObserverFrappe(stored={self.OBSERVER: None})
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, self.OBSERVER)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(self.AUTHOR)
        self.assertIn("observer_identity", str(caught.exception))
        self.assertEqual(frappe.calls, [])

    def test_an_unresolved_author_identity_is_refused_before_writes(self):
        frappe = _ObserverFrappe(
            stored={self.AUTHOR: None, self.OBSERVER: self.OBSERVER}
        )
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, self.OBSERVER)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(self.AUTHOR)
        self.assertIn("author_identity", str(caught.exception))
        self.assertEqual(frappe.calls, [])
        self.assertEqual(harness.actor, self.AUTHOR)

    def test_a_noncanonical_author_is_refused_before_any_session_switch(self):
        alias = self.AUTHOR.upper()
        frappe = _ObserverFrappe(
            stored={
                self.AUTHOR: self.AUTHOR,
                alias: self.AUTHOR,
                self.OBSERVER: self.OBSERVER,
            }
        )
        harness = self.Harness(frappe)
        observer_patch, actor_patch = self._patched(frappe, self.OBSERVER)
        with observer_patch, actor_patch:
            with self.assertRaises(AssertionError) as caught:
                harness.require_observer_prerequisite(alias)
        self.assertIn("author_noncanonical", str(caught.exception))
        self.assertNotIn(alias, str(caught.exception))
        self.assertNotIn(self.AUTHOR, str(caught.exception))
        self.assertEqual(frappe.calls, [])
        self.assertEqual(frappe.session.user, self.AUTHOR)
        self.assertEqual(harness.actor, self.AUTHOR)


class ChildDelegationTests(unittest.TestCase):
    """``run_child`` is the only supported publication entry and its argv is fixed."""

    class _Self:
        def __init__(self):
            self.created = []
            self.storage_keys = []

        def require_bench_environment(self):
            return None

    def test_run_child_uses_the_fixed_argv_cwd_and_timeout(self):
        bound = suite.PublicationNativeCase.run_child.__get__(
            self._Self(), self._Self
        )
        completed = subprocess.CompletedProcess([], 0, "status=completed\n", None)
        with mock.patch.object(subprocess, "run", return_value=completed) as run:
            result = bound(suite.APPROVED_SITE, "0" * 32)
        self.assertIs(result, completed)
        argv, kwargs = run.call_args
        self.assertEqual(
            argv[0],
            [
                sys.executable,
                "-m",
                suite.PROCESS_MODULE,
                "--site",
                suite.APPROVED_SITE,
                "--operation-id",
                "0" * 32,
            ],
        )
        self.assertEqual(kwargs["cwd"], suite.SITES_PATH)
        self.assertEqual(kwargs["timeout"], 180)
        self.assertFalse(kwargs["check"])
        self.assertTrue(kwargs["text"])


class _Reservation:
    key = "/private/files/clinic-photo-example.jpg"


class _Published:
    domain_doctype = "Clinic Photo"
    domain_name = "PHOTO-1"
    file_name = "FILE-1"
    reservation = _Reservation()


class ResidueRecordingTests(unittest.TestCase):
    """Every object an outcome owns is inventoried once, and never deleted."""

    class _Self:
        def __init__(self):
            self.created = []
            self.storage_keys = []

    def test_residue_lists_the_domain_file_and_key_once(self):
        stub = self._Self()
        record = suite.PublicationNativeCase.record_residue.__get__(stub, self._Self)
        record(_Published())
        record(_Published())
        self.assertEqual(stub.created, [("Clinic Photo", "PHOTO-1"), ("File", "FILE-1")])
        self.assertEqual(
            stub.storage_keys, ["/private/files/clinic-photo-example.jpg"]
        )


class NativeCaseSourcePins(unittest.TestCase):
    """The four authored cases stay real, selectable and free of placeholders."""

    def setUp(self):
        self.manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.cases = {case["id"]: case for case in self.manifest["cases"]}

    def test_the_four_cases_are_declared_executable_and_not_blocked(self):
        for case_id in FIXTURE_CASES:
            with self.subTest(case=case_id):
                case = self.cases[case_id]
                self.assertEqual(case["readiness"], "executable")
                self.assertNotIn("blocked_reason", case)

    def test_the_bodies_are_real_and_use_the_shared_fixture_helpers(self):
        for case_id, (class_name, method_name) in FIXTURE_CASES.items():
            with self.subTest(case=case_id):
                node = _native_body(class_name, method_name)
                self.assertIn("_case", _decorator_names(node))
                self.assertNotIn("skip", _decorator_names(node))
                self.assertNotIn("self.fail(", ast.unparse(node))
                called = _called_attributes(node)
                self.assertIn("require_native_prerequisites", called)
                self.assertIn("stage_and_commit", called)
                self.assertIn("publish", called)

    def test_no_body_publishes_in_process_or_touches_the_database_directly(self):
        for case_id, (class_name, method_name) in FIXTURE_CASES.items():
            with self.subTest(case=case_id):
                body = ast.unparse(_native_body(class_name, method_name))
                # No in-process coordinator/journal positive, no connection swap, no
                # privileged seeding, and no retry/delete of uncertain work.
                for forbidden in (
                    "publish_submission",
                    "journal.prepare",
                    "journal.publish",
                    "ignore_permissions",
                    "frappe.local.db",
                    "secondary_connection",
                    "db.rollback",
                    "delete_doc",
                    "unlink",
                    "sleep",
                ):
                    self.assertNotIn(forbidden, body)

    def test_the_cross_actor_case_switches_and_restores_the_session(self):
        node = _native_body(
            "PublicationAccessNativeCases",
            "test_operator_allowlist_cross_actor_audit_is_denied",
        )
        body = ast.unparse(node)
        self.assertIn("require_observer_prerequisite", body)
        self.assertIn("acting_as", body)
        self.assertIn("require_current_clinician", body)
        self.assertIn("inspect_operation", body)
        self.assertIn("InspectionDenied", body)
        self.assertIn("SAFE_INSPECTION_DENIED", body)
        # The denial body must use the real security decision, never a local double.
        for forbidden in (
            "mock",
            "patch",
            "ignore_permissions",
            "db.rollback",
            "delete_doc",
        ):
            self.assertNotIn(forbidden, body)

    def test_the_cross_actor_body_asserts_the_committed_actor_differs(self):
        node = _native_body(
            "PublicationAccessNativeCases",
            "test_operator_allowlist_cross_actor_audit_is_denied",
        )
        body = ast.unparse(node)
        self.assertIn(
            "self.assertNotEqual(before_row.get('actor'), observer)", body
        )

    def test_the_shared_helpers_never_read_after_the_staging_commit(self):
        # The snapshot depends on ordering: stage_and_commit must commit last, and the
        # next DB read must be inside publish(), after the child returned.
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        base = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PublicationNativeCase"
        )
        methods = {
            child.name: child
            for child in base.body
            if isinstance(child, ast.FunctionDef)
        }
        stage = ast.unparse(methods["stage_and_commit"])
        self.assertLess(
            stage.index("stage_submission"), stage.rindex("db.commit()")
        )
        self.assertNotIn("db.count", stage)
        self.assertNotIn("get_doc", stage)
        publish = ast.unparse(methods["publish"])
        self.assertLess(publish.index("run_child"), publish.index("load_published"))



# ---------------------------------------------------------------------------
# Behavioural controls for the fix-1 corrections (written before the repair)
#
# Only the process boundary (``run_child``) and the read/loader boundary
# (``frappe_context.get_save_operation``, ``journal.load_published``, one tiny
# in-memory table) are doubled. The helpers under test are the real ones, and nothing
# here publishes, authorizes or claims native evidence.
# ---------------------------------------------------------------------------
ORIGINAL_KEY = "/private/files/clinic-photo-0123456789abcdef0123456789abcdef.jpg"
FIRST_KEY = "/private/files/clinic-annotation-0123456789abcdef0123456789abcdef.json"
SECOND_KEY = "/private/files/clinic-annotation-fedcba9876543210fedcba9876543210.json"
OPERATION_ID = "0" * 31 + "1"
OTHER_OPERATION_ID = "0" * 31 + "2"


class _File:
    """Minimal stand-in for one loaded File document's cached content."""

    def __init__(self, content):
        self.content = content


class _Table:
    """One tiny in-memory table: equality filters plus ``pluck`` only."""

    def __init__(self, rows=None):
        self.rows = list(rows or ())

    def get_all(self, doctype, filters=None, pluck=None, limit=None, **_kwargs):
        found = [
            row
            for row in self.rows
            if all(row.get(name) == value for name, value in (filters or {}).items())
        ]
        if pluck:
            found = [row.get(pluck) for row in found]
        return found[:limit] if limit else found


class _FakeFrappe:
    """The narrow ``frappe`` surface inventory reads: ``local``, ``conf``, ``db``."""

    def __init__(self, tables=None):
        self.tables = tables or {}
        self.local = type("Local", (), {"site": suite.APPROVED_SITE})()
        self.conf = {suite.PUBLICATION_GATE: True, suite.SYNTHETIC_MODE: True}
        self.db = self

    def get_all(self, doctype, filters=None, pluck=None, limit=None, **kwargs):
        table = self.tables.get(doctype)
        if table is None:
            return []
        return table.get_all(doctype, filters, pluck, limit, **kwargs)


def make_harness(tables=None):
    """A real case instance with only the process and read boundaries doubled.

    The subclass is created inside this function on purpose: a module-level TestCase
    subclass would be picked up by unittest and reported as an extra passing test.
    """

    class Harness(suite.PublicationNativeCase):
        residue_incomplete = False

        def __init__(self):
            super().__init__("runTest")
            self.created = []
            self.storage_keys = []
            self.actor = suite.SYNTHETIC_CLINICIAN
            self.patient = suite.SYNTHETIC_PATIENT
            self.child_result = None
            self.child_error = None
            self.frappe = _FakeFrappe(tables or {})

        def runTest(self):
            pass

        def run_child(self, site, operation_id):
            self.asserted_child = (site, operation_id)
            if self.child_error is not None:
                raise self.child_error
            return self.child_result

    return Harness()


class _StubReservation:
    def __init__(self, key):
        self.key = key


class _StubOutcome:
    def __init__(self, doctype, name, file_name, key):
        self.domain_doctype = doctype
        self.domain_name = name
        self.file_name = file_name
        self.reservation = _StubReservation(key)


def _row(state, key, **extra):
    row = {
        "name": OPERATION_ID,
        "site": suite.APPROVED_SITE,
        "actor": suite.SYNTHETIC_CLINICIAN,
        "kind": contracts.SYNTHETIC_ORIGINAL_CREATE,
        "state": state,
        "reservation_key": key,
        "draft": None,
        "expected_version": None,
        "original": None,
        "synthetic_patient": suite.SYNTHETIC_PATIENT,
    }
    row.update(extra)
    return row


class ResidueInventoryBehaviourTests(unittest.TestCase):
    """Failure residue must be inventoried read-only, and never claimed complete."""

    def test_timed_out_child_still_inventories_the_prepared_key(self):
        harness = make_harness(
            {
                "File": _Table([{"name": "FILE-1", "file_url": ORIGINAL_KEY}]),
                "Clinic Photo": _Table([{"name": "PHOTO-1", "storage_key": ORIGINAL_KEY}]),
                "Clinic Photo Annotation Version": _Table([]),
            }
        )
        harness.child_error = subprocess.TimeoutExpired(cmd="child", timeout=180)
        with mock.patch.object(
            frappe_context, "get_save_operation", return_value=_row("PREPARED", ORIGINAL_KEY)
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                harness.publish(OPERATION_ID)
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertIn(ORIGINAL_KEY, harness.storage_keys)
        self.assertIn(("File", "FILE-1"), harness.created)
        self.assertIn(("Clinic Photo", "PHOTO-1"), harness.created)
        self.assertTrue(type(harness).residue_incomplete)

    def test_unavailable_child_with_partial_rows_reports_them_as_uncertain(self):
        harness = make_harness(
            {
                "File": _Table([{"name": "FILE-1", "file_url": FIRST_KEY}]),
                "Clinic Photo": _Table([]),
                "Clinic Photo Annotation Version": _Table(
                    [{"name": "DRAFT-1-v1", "storage_key": FIRST_KEY}]
                ),
            }
        )
        harness.child_result = subprocess.CompletedProcess(
            [], 3, "status=unavailable operation_id=" + OPERATION_ID + "\n", None
        )
        with mock.patch.object(
            frappe_context,
            "get_save_operation",
            return_value=_row("PREPARED", FIRST_KEY, draft="DRAFT-1", expected_version=0),
        ):
            with self.assertRaises(AssertionError):
                harness.publish(OPERATION_ID)
        self.assertIn(("File", "FILE-1"), harness.created)
        self.assertIn(("Clinic Photo Annotation Version", "DRAFT-1-v1"), harness.created)
        self.assertIn(FIRST_KEY, harness.storage_keys)
        self.assertTrue(type(harness).residue_incomplete)

    def test_completed_child_whose_loader_fails_still_inventories_readable_rows(self):
        from clinic_photos.publication import journal

        harness = make_harness(
            {
                "File": _Table([{"name": "FILE-1", "file_url": ORIGINAL_KEY}]),
                "Clinic Photo": _Table([{"name": "PHOTO-1", "storage_key": ORIGINAL_KEY}]),
            }
        )
        harness.child_result = subprocess.CompletedProcess(
            [], 0, "status=completed operation_id=" + OPERATION_ID + "\n", None
        )
        row = _row(
            "PUBLISHED",
            ORIGINAL_KEY,
            published_file="FILE-1",
            published_domain_doctype="Clinic Photo",
            published_domain_name="PHOTO-1",
        )
        with mock.patch.object(
            frappe_context, "get_save_operation", return_value=row
        ), mock.patch.object(
            journal,
            "load_published",
            side_effect=journal.JournalOperationMissing("unreadable"),
        ):
            with self.assertRaises(journal.JournalOperationMissing):
                harness.publish(OPERATION_ID)
        self.assertIn(("File", "FILE-1"), harness.created)
        self.assertIn(("Clinic Photo", "PHOTO-1"), harness.created)
        self.assertIn(ORIGINAL_KEY, harness.storage_keys)

    def test_unreadable_operation_marks_the_inventory_uncertain(self):
        harness = make_harness()
        harness.child_error = subprocess.TimeoutExpired(cmd="child", timeout=180)
        with mock.patch.object(
            frappe_context, "get_save_operation", side_effect=RuntimeError("unreadable")
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                harness.publish(OPERATION_ID)
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertTrue(type(harness).residue_incomplete)

    def test_inventory_failure_preserves_the_original_failure(self):
        harness = make_harness()
        original = subprocess.TimeoutExpired(cmd="child", timeout=180)
        harness.child_error = original
        with mock.patch.object(
            frappe_context, "get_save_operation", side_effect=RuntimeError("unreadable")
        ):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                harness.publish(OPERATION_ID)
        self.assertIs(caught.exception, original)
        self.assertIn(suite.SAFE_INVENTORY_UNCERTAIN, getattr(original, "__notes__", []))

    def test_later_body_failure_retains_every_earlier_object(self):
        from clinic_photos.publication import journal

        harness = make_harness(
            {
                "File": _Table(
                    [
                        {"name": "FILE-1", "file_url": ORIGINAL_KEY},
                        {"name": "FILE-2", "file_url": SECOND_KEY},
                    ]
                ),
                "Clinic Photo": _Table([{"name": "PHOTO-1", "storage_key": ORIGINAL_KEY}]),
                "Clinic Photo Annotation Version": _Table(
                    [{"name": "DRAFT-1-v2", "storage_key": SECOND_KEY}]
                ),
            }
        )
        first = _StubOutcome("Clinic Photo", "PHOTO-1", "FILE-1", ORIGINAL_KEY)
        harness.child_result = subprocess.CompletedProcess(
            [], 0, "status=completed operation_id=" + OPERATION_ID + "\n", None
        )
        with mock.patch.object(
            frappe_context,
            "get_save_operation",
            return_value=_row("PUBLISHED", ORIGINAL_KEY, published_file="FILE-1"),
        ), mock.patch.object(journal, "load_published", return_value=first):
            harness.publish(OPERATION_ID)

        # A later step fails: the second child's output is malformed after it may
        # already have committed v2.
        harness.child_result = subprocess.CompletedProcess(
            [], 0, "status=bogus operation_id=" + OTHER_OPERATION_ID + "\n", None
        )
        with mock.patch.object(
            frappe_context,
            "get_save_operation",
            return_value=_row("PUBLISHED", SECOND_KEY, published_file="FILE-2"),
        ):
            with self.assertRaises(AssertionError):
                harness.publish(OTHER_OPERATION_ID)

        self.assertIn(("Clinic Photo", "PHOTO-1"), harness.created)
        self.assertIn(("File", "FILE-1"), harness.created)
        self.assertIn(("File", "FILE-2"), harness.created)
        self.assertIn(("Clinic Photo Annotation Version", "DRAFT-1-v2"), harness.created)
        self.assertIn(ORIGINAL_KEY, harness.storage_keys)
        self.assertIn(SECOND_KEY, harness.storage_keys)


class StagingCommitFailureTests(unittest.TestCase):
    """A commit failure still retains the operation identity it already knows."""

    def test_commit_exception_keeps_the_operation_identity(self):
        harness = make_harness()
        harness.frappe = mock.Mock()
        harness.frappe.db.commit.side_effect = RuntimeError("commit failed")
        outcome = contracts.SaveOutcome("pending", OPERATION_ID)
        with mock.patch.object(
            submission, "stage_submission", return_value=outcome
        ), mock.patch.object(frappe_context, "get_save_operation", return_value=None):
            with self.assertRaises(RuntimeError) as caught:
                harness.stage_and_commit(object())
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertIn(
            suite.SAFE_INVENTORY_UNCERTAIN, getattr(caught.exception, "__notes__", [])
        )
        self.assertTrue(type(harness).residue_incomplete)


class NativeContentGuardTests(unittest.TestCase):
    """The pinned native File default is b"", so only truthy content is an injection."""

    def test_the_guard_discriminates_the_native_default_from_truthy_content(self):
        for value, injected in (
            (b"", False),
            (None, False),
            ("", False),
            (b"\xff\xd8\xff", True),
            ("content", True),
        ):
            with self.subTest(value=value):
                self.assertEqual(suite.has_injected_content(_File(value)), injected)

    def test_the_first_jpeg_body_uses_the_no_truthy_content_predicate(self):
        node = _native_body(
            "PublicationOriginalNativeCases",
            "test_first_jpeg_original_uses_protected_mro_and_get_content",
        )
        body = ast.unparse(node)
        self.assertIn("has_injected_content", body)
        self.assertIn("get_content()", body)
        # The identity form ``assertIsNone(getattr(file_doc, "content", None))`` is the
        # defect: the pinned native default is b"", so only truthiness can discriminate.
        for call in ast.walk(node):
            if isinstance(call, ast.Call) and getattr(call.func, "attr", "") == "assertIsNone":
                for argument in call.args:
                    if (
                        isinstance(argument, ast.Call)
                        and getattr(argument.func, "id", "") == "getattr"
                        and any(
                            isinstance(sub, ast.Constant) and sub.value == "content"
                            for sub in argument.args
                        )
                    ):
                        self.fail("content must be checked for truthiness, not identity")


# ---------------------------------------------------------------------------
# Fix-2 controls: the inventory call is guarded, and PUBLISHED certainty must be
# proven by the operation's own exact-key rows.
#
# Written and run BEFORE the fix-2 source change. Every symbol used here already
# exists in fix 1, so each red outcome exercises a real code path rather than a
# missing name. Still local-only: no site, bench, subprocess or native case runs.
# ---------------------------------------------------------------------------
class InventoryGuardBehaviourTests(unittest.TestCase):
    """An inventory failure must never replace the failure it describes."""

    def test_malformed_stored_key_keeps_the_original_failure(self):
        harness = make_harness()
        original = subprocess.TimeoutExpired(cmd="child", timeout=180)
        harness.child_error = original
        # "//[" makes url parsing raise ValueError, which the inventory does not
        # currently expect; it must not become the raised failure.
        with mock.patch.object(
            frappe_context, "get_save_operation", return_value=_row("PREPARED", "//[")
        ):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                harness.publish(OPERATION_ID)
        self.assertIs(caught.exception, original)
        self.assertIn(suite.SAFE_INVENTORY_UNCERTAIN, getattr(original, "__notes__", []))
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertTrue(type(harness).residue_incomplete)

    def test_inventory_exception_in_publish_keeps_the_original_failure(self):
        harness = make_harness()
        original = subprocess.TimeoutExpired(cmd="child", timeout=180)
        harness.child_error = original
        with mock.patch.object(
            harness, "inventory_operation", side_effect=RuntimeError("inventory broke")
        ):
            with self.assertRaises(subprocess.TimeoutExpired) as caught:
                harness.publish(OPERATION_ID)
        self.assertIs(caught.exception, original)
        self.assertIn(suite.SAFE_INVENTORY_UNCERTAIN, getattr(original, "__notes__", []))
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertTrue(type(harness).residue_incomplete)

    def test_inventory_exception_in_commit_branch_keeps_the_original_failure(self):
        harness = make_harness()
        commit_error = RuntimeError("commit failed")
        harness.frappe = mock.Mock()
        harness.frappe.db.commit.side_effect = commit_error
        outcome = contracts.SaveOutcome("pending", OPERATION_ID)
        with mock.patch.object(
            submission, "stage_submission", return_value=outcome
        ), mock.patch.object(
            harness, "inventory_operation", side_effect=RuntimeError("inventory broke")
        ):
            with self.assertRaises(RuntimeError) as caught:
                harness.stage_and_commit(object())
        self.assertIs(caught.exception, commit_error)
        self.assertIn(
            suite.SAFE_INVENTORY_UNCERTAIN, getattr(commit_error, "__notes__", [])
        )
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)
        self.assertTrue(type(harness).residue_incomplete)


class PublishedCertaintyBehaviourTests(unittest.TestCase):
    """PUBLISHED is certain only when its own recorded links are in the exact-key rows."""

    def _published_row(self, **extra):
        row = _row(
            "PUBLISHED",
            ORIGINAL_KEY,
            published_file="FILE-1",
            published_domain_doctype="Clinic Photo",
            published_domain_name="PHOTO-1",
        )
        row.update(extra)
        return row

    def _uncertain(self, tables, row=None):
        harness = make_harness(tables)
        with mock.patch.object(
            frappe_context, "get_save_operation", return_value=row or self._published_row()
        ):
            return harness.inventory_operation(OPERATION_ID), harness

    def test_published_with_zero_exact_key_rows_is_uncertain(self):
        uncertain, harness = self._uncertain(
            {
                "File": _Table([]),
                "Clinic Photo": _Table([]),
                "Clinic Photo Annotation Version": _Table([]),
            }
        )
        self.assertTrue(uncertain)
        self.assertIn((suite.OPERATION_DOCTYPE, OPERATION_ID), harness.created)

    def test_published_with_mismatched_file_name_is_uncertain(self):
        uncertain, _ = self._uncertain(
            {
                "File": _Table([{"name": "FILE-OTHER", "file_url": ORIGINAL_KEY}]),
                "Clinic Photo": _Table([{"name": "PHOTO-1", "storage_key": ORIGINAL_KEY}]),
                "Clinic Photo Annotation Version": _Table([]),
            }
        )
        self.assertTrue(uncertain)

    def test_published_with_mismatched_domain_name_is_uncertain(self):
        uncertain, _ = self._uncertain(
            {
                "File": _Table([{"name": "FILE-1", "file_url": ORIGINAL_KEY}]),
                "Clinic Photo": _Table([{"name": "PHOTO-OTHER", "storage_key": ORIGINAL_KEY}]),
                "Clinic Photo Annotation Version": _Table([]),
            }
        )
        self.assertTrue(uncertain)

    def test_published_with_coherent_exact_rows_stays_certain(self):
        uncertain, harness = self._uncertain(
            {
                "File": _Table([{"name": "FILE-1", "file_url": ORIGINAL_KEY}]),
                "Clinic Photo": _Table([{"name": "PHOTO-1", "storage_key": ORIGINAL_KEY}]),
                "Clinic Photo Annotation Version": _Table([]),
            }
        )
        self.assertFalse(uncertain)
        self.assertIn(("File", "FILE-1"), harness.created)
        self.assertIn(("Clinic Photo", "PHOTO-1"), harness.created)
        self.assertIn(ORIGINAL_KEY, harness.storage_keys)


# ---------------------------------------------------------------------------
# Task 3 behavioural controls: private operator channel, owned probe child and
# complete two-arm orchestration.
#
# Only the process boundary, the wall clock and the native fixture helpers are
# doubled. Nothing here launches a real child, mutates a User, acts as the operator
# or claims native evidence.
# ---------------------------------------------------------------------------
REVOCATION_RUN_NONCE = "0123456789abcdef0123456789abcdef"
REVOCATION_INVOCATION_NONCE = "fedcba9876543210fedcba9876543210"
REVOCATION_ACTION_NONCE = "00112233445566778899aabbccddeeff"
REVOCATION_OPERATION_ID = "abcdefabcdefabcdefabcdefabcdef01"
REVOCATION_SOURCE_DIGEST = "ab" * 32
REVOCATION_VERIFIER = "native-probe-verifier-20261109@example.invalid"
REVOCATION_ROLES = [
    {"role": "Physician", "parenttype": "User", "parentfield": "roles"},
    {"role": "System Manager", "parenttype": "User", "parentfield": "roles"},
]


def _revocation_baseline(seed):
    return {
        "enabled": True,
        "roles": [dict(role) for role in REVOCATION_ROLES],
        "actor_digest": hashlib.sha256(("actor:" + seed).encode()).hexdigest(),
        "operation_digest": hashlib.sha256(("operation:" + seed).encode()).hexdigest(),
        "evidence_digest": hashlib.sha256(("evidence:" + seed).encode()).hexdigest(),
        "counts": {"operations": 1, "files": 1, "photos": 1, "versions": 0},
    }


def _revocation_document(
    mode="baseline", *, baseline=None, request=None, arm="disabled"
):
    return {
        "schema": 1,
        "run_nonce": REVOCATION_RUN_NONCE,
        "invocation_nonce": REVOCATION_INVOCATION_NONCE,
        "site": suite.APPROVED_SITE,
        "source_digest": REVOCATION_SOURCE_DIGEST,
        "operation_id": REVOCATION_OPERATION_ID,
        "actor": suite.SYNTHETIC_CLINICIAN,
        "verifier": REVOCATION_VERIFIER,
        "arm": arm,
        "mode": mode,
        "baseline": baseline,
        "request": request,
    }


def _revocation_response(
    document, *, enabled, clinician, baseline, baseline_digest=None,
    result="observed", clean=True,
):
    digest = baseline_digest
    if digest is None:
        digest = probe.baseline_digest(baseline)
    return {
        "schema": 1,
        "run_nonce": document["run_nonce"],
        "invocation_nonce": document["invocation_nonce"],
        "site_digest": probe.site_digest(document["site"]),
        "source_digest": document["source_digest"],
        "operation_id": document["operation_id"],
        "arm": document["arm"],
        "mode": document["mode"],
        "result": result,
        "enabled": enabled,
        "clinician": clinician,
        "baseline_digest": digest,
        "evidence_digest": baseline["evidence_digest"] if baseline else None,
        "counts": dict(baseline["counts"]) if baseline else None,
        "clean": clean,
    }


def _canonical_private(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _action_binding(
    action="change", *, nonce=REVOCATION_ACTION_NONCE, arm="disabled",
    operation=REVOCATION_OPERATION_ID, baseline=None,
):
    baseline = baseline or _revocation_baseline("base")
    return {
        "schema": 1,
        "run_nonce": REVOCATION_RUN_NONCE,
        "action_nonce": nonce,
        "site": suite.APPROVED_SITE,
        "source_digest": REVOCATION_SOURCE_DIGEST,
        "operation_id": operation,
        "actor": suite.SYNTHETIC_CLINICIAN,
        "arm": arm,
        "action": action,
        "baseline_digest": probe.baseline_digest(baseline),
        "baseline_roles": [dict(role) for role in baseline["roles"]],
    }


def _action_ack(binding, *, status="done", **override):
    ack = {name: value for name, value in binding.items() if name != "baseline_roles"}
    ack["status"] = status
    ack.update(override)
    return ack


@contextlib.contextmanager
def _control_environment(mode=0o700, verifier=REVOCATION_VERIFIER):
    with tempfile.TemporaryDirectory() as base:
        base = os.path.realpath(base)
        control = os.path.join(base, "control")
        os.mkdir(control)
        os.chmod(control, mode)
        with mock.patch.dict(
            os.environ,
            {
                suite.REVOCATION_CONTROL_DIR_ENV: control,
                suite.REVOCATION_VERIFIER_ENV: verifier,
            },
            clear=False,
        ):
            yield control


def _write_private(path, value, mode=0o600):
    data = _canonical_private(value) + b"\n"
    with open(path, "wb") as stream:
        stream.write(data)
    os.chmod(path, mode)


def _write_raw(path, data, mode=0o600):
    with open(path, "wb") as stream:
        stream.write(data)
    os.chmod(path, mode)


class _Clock:
    """Deterministic monotonic clock; the first sleep performs one side effect."""

    def __init__(self, on_sleep=None):
        self.now = 0.0
        self.on_sleep = on_sleep
        self.sleeps = []

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds
        if self.on_sleep is not None:
            self.on_sleep()


@contextlib.contextmanager
def _patched_clock(clock):
    with mock.patch.object(suite.time, "monotonic", clock.monotonic), mock.patch.object(
        suite.time, "sleep", clock.sleep
    ):
        yield


class _FakeProcess:
    """Minimal owned-child stand-in: pipes, one exit and signal accounting."""

    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.pid = 424242
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode
        self.signals = []
        self.wait_calls = []

    def communicate(self, timeout=None):
        return self.stdout.getvalue(), self.stderr.getvalue()

    def send_signal(self, signum):
        self.signals.append(signum)
        if self.returncode is not None:
            return
        self.returncode = -signum

    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        return self.returncode


class _TimeoutProcess(_FakeProcess):
    def communicate(self, timeout=None):
        raise subprocess.TimeoutExpired("child", timeout)


class _SurvivingProcess(_TimeoutProcess):
    def wait(self, timeout=None):
        self.wait_calls.append(timeout)
        raise subprocess.TimeoutExpired("child", timeout)


def _spawn_capture(process, captured):
    def spawn(argv, **kwargs):
        captured.append((argv, kwargs))
        return process

    return spawn


def _valid_probe_line(document, baseline):
    return _canonical_private(
        _revocation_response(
            document,
            enabled=True,
            clinician=True,
            baseline=baseline,
            baseline_digest=probe.baseline_digest(baseline),
        )
    ) + b"\n"


class RevocationProbeTransportTests(unittest.TestCase):
    """Owned child lifecycle, sealed transport and unconditional no-credit parsing."""

    def test_child_timeout_survival_and_forged_response_fail(self):
        document = _revocation_document("baseline")
        baseline = _revocation_baseline("transport")
        line = _valid_probe_line(document, baseline)

        # Positive control: a valid bounded line is parsed and the private baseline
        # descriptor is read only after a proven clean exit.
        process = _FakeProcess(stdout=line, returncode=0)
        captured = []
        read_calls = []
        fd_states = []
        stdin_states = []
        transport_names = []

        def spawn(argv, **kwargs):
            captured.append((argv, kwargs))
            descriptor = kwargs["pass_fds"][0]
            fd_states.append(
                (os.fstat(descriptor).st_size, os.lseek(descriptor, 0, os.SEEK_CUR))
            )
            stdin_fd = kwargs["stdin"]
            info = os.fstat(stdin_fd)
            stdin_states.append(
                (
                    stat.S_ISREG(info.st_mode),
                    stat.S_IMODE(info.st_mode),
                    info.st_uid,
                    os.lseek(stdin_fd, 0, os.SEEK_CUR),
                    fcntl.fcntl(stdin_fd, fcntl.F_GETFL) & os.O_ACCMODE,
                )
            )
            transport_names.extend(
                name
                for name in os.listdir(control)
                if name.startswith(".revocation-input-")
            )
            return process

        with _control_environment() as control, mock.patch.object(
            suite.subprocess, "Popen", side_effect=spawn
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
        ):
            response, private = suite._run_revocation_probe(document)
        self.assertEqual(response.get("result"), "observed")
        self.assertEqual(private, baseline)
        self.assertEqual(len(read_calls), 1)
        argv, kwargs = captured[0]
        self.assertEqual(
            argv,
            [
                sys.executable,
                "-m",
                suite.REVOCATION_PROBE_MODULE,
                "--site",
                suite.APPROVED_SITE,
                "--operation-id",
                REVOCATION_OPERATION_ID,
                "--mode",
                "baseline",
            ],
        )
        self.assertEqual(kwargs["cwd"], suite.SITES_PATH)
        self.assertEqual(kwargs["stdout"], subprocess.PIPE)
        self.assertEqual(kwargs["stderr"], subprocess.PIPE)
        self.assertTrue(kwargs["close_fds"])
        self.assertTrue(kwargs["start_new_session"])
        baseline_fd = kwargs["pass_fds"][0]
        self.assertEqual(kwargs["pass_fds"], (baseline_fd,))
        self.assertEqual(kwargs["env"][suite.REVOCATION_BASELINE_FD_ENV], str(baseline_fd))
        self.assertEqual(
            fd_states, [(0, 0)], "baseline fd must be empty at offset 0 when spawned"
        )
        self.assertEqual(
            stdin_states,
            [(True, 0o600, os.geteuid(), 0, os.O_RDONLY)],
            "sealed stdin must be a read-only 0600 regular file at offset 0",
        )
        self.assertEqual(
            transport_names, [], "the sealed input name must be unlinked before spawn"
        )
        self.assertTrue(
            set(kwargs["env"])
            <= {"PATH", "PYTHONPATH", suite.REVOCATION_BASELINE_FD_ENV},
            "the child environment must hold only the minimal allowed keys",
        )
        self.assertIn(suite.REVOCATION_BASELINE_FD_ENV, kwargs["env"])
        self.assertEqual(
            len([fd for fd in kwargs["pass_fds"] if fd != baseline_fd]),
            0,
            "no inherited descriptor other than the private baseline",
        )

        forged = {
            "no output": b"",
            "multiple lines": line + line,
            "trailing content": line + b"x",
            "oversize": b"{" + b" " * 20000,
        }
        for name, raw in forged.items():
            with self.subTest(forged=name):
                process = _FakeProcess(stdout=raw, returncode=0)
                read_calls = []
                with _control_environment(), mock.patch.object(
                    suite.subprocess, "Popen",
                    side_effect=_spawn_capture(process, []),
                ), mock.patch.object(
                    suite,
                    "_read_private_baseline_descriptor",
                    side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
                ):
                    with self.assertRaises(AssertionError):
                        suite._run_revocation_probe(document)
                self.assertEqual(read_calls, [], "a rejected child result must not be credited")

        duplicate = _valid_probe_line(document, baseline).replace(
            b'"result":"observed"',
            b'"result":"observed","result":"observed"',
            1,
        )
        for name, raw in {"duplicate key": duplicate}.items():
            with self.subTest(forged=name):
                process = _FakeProcess(stdout=raw, returncode=0)
                with _control_environment(), mock.patch.object(
                    suite.subprocess, "Popen",
                    side_effect=_spawn_capture(process, []),
                ):
                    with self.assertRaises(AssertionError):
                        suite._run_revocation_probe(document)

        # A non-zero child exit is never credited even with a valid-looking body.
        process = _FakeProcess(stdout=line, returncode=2)
        read_calls = []
        with _control_environment(), mock.patch.object(
            suite.subprocess, "Popen", side_effect=_spawn_capture(process, [])
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
        ):
            with self.assertRaises(AssertionError):
                suite._run_revocation_probe(document)
        self.assertEqual(read_calls, [])

        # A wrong run binding fails at the shared response binding check; the
        # state/evidence guard negatives live in their own test below.
        wrong = _revocation_document("baseline")
        wrong["run_nonce"] = "11" * 16
        wrong_line = _canonical_private(
            _revocation_response(
                wrong,
                enabled=True,
                clinician=True,
                baseline=baseline,
                baseline_digest=probe.baseline_digest(baseline),
            )
        ) + b"\n"
        process = _FakeProcess(stdout=wrong_line, returncode=0)
        with _control_environment(), mock.patch.object(
            suite.subprocess, "Popen", side_effect=_spawn_capture(process, [])
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: baseline,
        ):
            with self.assertRaises(AssertionError):
                suite._run_revocation_probe(document)

        # Timeout: the process is signalled, the failure is fixed and the private
        # descriptor is never read.
        process = _TimeoutProcess(returncode=None)
        read_calls = []
        with _control_environment(), mock.patch.object(
            suite.subprocess, "Popen", side_effect=_spawn_capture(process, [])
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
        ):
            with self.assertRaises(AssertionError):
                suite._run_revocation_probe(document)
        self.assertEqual(read_calls, [])
        self.assertIn(signal.SIGTERM, process.signals)

        # A child that survives both the terminate and the kill grace is an uncertain
        # failure, never a result.
        process = _SurvivingProcess(
            stdout=b"SENSITIVE-NATIVE-NOISE\n", stderr=b"SENSITIVE-NATIVE-NOISE\n",
            returncode=None,
        )
        read_calls = []
        with _control_environment(), mock.patch.object(
            suite.subprocess, "Popen", side_effect=_spawn_capture(process, [])
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
        ):
            with self.assertRaises(AssertionError) as caught:
                suite._run_revocation_probe(document)
        self.assertEqual(read_calls, [])
        self.assertIn(signal.SIGTERM, process.signals)
        self.assertIn(signal.SIGKILL, process.signals)
        message = str(caught.exception)
        self.assertNotIn("SENSITIVE-NATIVE-NOISE", message)
        self.assertNotIn("Traceback", message)
        self.assertNotIn("SENSITIVE-NATIVE-NOISE", " ".join(
            getattr(caught.exception, "__notes__", [])
        ))

    def test_parent_state_and_evidence_guard_negatives_refuse(self):
        baseline = _revocation_baseline("guards")
        arm_states = {"disabled": (False, True), "role_removed": (True, False)}
        for arm, (enabled, clinician) in arm_states.items():
            for name, mutation in {
                "stale change": {"enabled": True, "clinician": True},
                "wrong clinician": {"enabled": enabled, "clinician": not clinician},
                "non-observed result": {"result": "refused"},
                "clean false": {"clean": False},
            }.items():
                with self.subTest(arm=arm, guard=name):
                    document = _revocation_document(
                        "status_denial", baseline=baseline, arm=arm
                    )
                    response = _revocation_response(
                        document,
                        enabled=enabled,
                        clinician=clinician,
                        baseline=baseline,
                    )
                    response.update(mutation)
                    expected = suite._expected_revocation_bindings(
                        document, document, probe
                    )
                    if "result" in mutation:
                        # Shape is valid for a non-observed result, so the refusal
                        # must come from the state guard rather than the parser.
                        probe.validate_response(response, expected)
                    else:
                        probe.validate_response(response, expected)
                    process = _FakeProcess(
                        stdout=_canonical_private(response) + b"\n", returncode=0
                    )
                    with _control_environment(), mock.patch.object(
                        suite.subprocess, "Popen",
                        side_effect=_spawn_capture(process, []),
                    ):
                        with self.assertRaises(AssertionError):
                            suite._run_revocation_probe(document)

            # Evidence digest mismatch is rejected by the shared binding comparison;
            # counts mismatch is otherwise valid and must fail the explicit guard.
            for name, mutate in {
                "evidence digest": lambda response: response.update(
                    {"evidence_digest": "cd" * 32}
                ),
                "counts": lambda response: response.update(
                    {
                        "counts": {
                            **response["counts"],
                            "files": response["counts"]["files"] + 1,
                        }
                    }
                ),
            }.items():
                with self.subTest(arm=arm, guard=name):
                    document = _revocation_document(
                        "status_denial", baseline=baseline, arm=arm
                    )
                    response = _revocation_response(
                        document,
                        enabled=enabled,
                        clinician=clinician,
                        baseline=baseline,
                    )
                    mutate(response)
                    expected = suite._expected_revocation_bindings(
                        document, document, probe
                    )
                    accepted = True
                    try:
                        probe.validate_response(response, expected)
                    except Exception:
                        accepted = False
                    self.assertEqual(
                        accepted, name != "evidence digest",
                        "only the evidence binding is caught before the explicit guard",
                    )
                    process = _FakeProcess(
                        stdout=_canonical_private(response) + b"\n", returncode=0
                    )
                    with _control_environment(), mock.patch.object(
                        suite.subprocess, "Popen",
                        side_effect=_spawn_capture(process, []),
                    ):
                        with self.assertRaises(AssertionError):
                            suite._run_revocation_probe(document)

        # The private baseline is never read before the state is credited.
        baseline_document = _revocation_document("baseline")
        wrong_state = _revocation_response(
            baseline_document,
            enabled=False,
            clinician=True,
            baseline=baseline,
            baseline_digest=probe.baseline_digest(baseline),
        )
        process = _FakeProcess(
            stdout=_canonical_private(wrong_state) + b"\n", returncode=0
        )
        read_calls = []
        with _control_environment(), mock.patch.object(
            suite.subprocess, "Popen", side_effect=_spawn_capture(process, [])
        ), mock.patch.object(
            suite,
            "_read_private_baseline_descriptor",
            side_effect=lambda descriptor: read_calls.append(descriptor) or baseline,
        ):
            with self.assertRaises(AssertionError):
                suite._run_revocation_probe(baseline_document)
        self.assertEqual(
            read_calls, [], "a refused state must not credit the private baseline"
        )


class RevocationOperatorChannelTests(unittest.TestCase):
    """The external handoff is exact-once, private, bounded and strictly bound."""

    def _await(self, binding, *, ack_writer, clock=None, deadline=5.0):
        logs = []
        clock = clock or _Clock(on_sleep=ack_writer)
        with _patched_clock(clock), mock.patch.object(
            suite, "_emit_revocation_log", side_effect=logs.append
        ):
            suite._await_revocation_action(binding, deadline=deadline)
        return logs, clock

    def test_operator_ack_exact_once_private_and_bounded(self):
        binding = _action_binding()
        with _control_environment() as control:
            ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
            logs, clock = self._await(
                binding, ack_writer=lambda: _write_private(ack_path, _action_ack(binding))
            )
            request_path = os.path.join(
                control, binding["action_nonce"] + ".request.json"
            )
            self.assertTrue(os.path.isfile(request_path))
            self.assertEqual(
                stat.S_IMODE(os.stat(request_path).st_mode), 0o600
            )
            self.assertEqual(
                pathlib.Path(request_path).read_bytes(),
                _canonical_private(binding),
            )
            self.assertEqual(
                logs,
                [
                    "revocation_wait "
                    f"action_nonce={binding['action_nonce']} action=change arm=disabled"
                ],
            )
            self.assertGreaterEqual(len(clock.sleeps), 1)
            self.assertLessEqual(len(clock.sleeps), 2)

            # Exact once: a second call with the same binding refuses because the
            # acknowledgement already exists.
            with _patched_clock(_Clock()):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)

        # A stale acknowledgement that exists before the request is refused first.
        with _control_environment() as control:
            binding = _action_binding(nonce="22" * 16)
            ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
            _write_private(ack_path, _action_ack(binding))
            with _patched_clock(_Clock()):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)
            self.assertFalse(
                os.path.exists(
                    os.path.join(control, binding["action_nonce"] + ".request.json")
                )
            )

        # Bounded forms: oversize, duplicate keys, multiple lines and malformed JSON.
        bounded = {
            "oversize": b"{" + b"x" * suite.REVOCATION_ACK_BYTES,
            "duplicate keys": (
                _canonical_private(_action_ack(_action_binding())).replace(
                    b'"status":"done"',
                    b'"status":"done","status":"done"',
                    1,
                )
            ),
            "multiple lines": (
                _canonical_private(_action_ack(_action_binding())) + b"\n{}\n"
            ),
            "malformed json": b"not-json",
            "empty": b"",
        }
        for name, raw in bounded.items():
            with self.subTest(bounded=name), _control_environment() as control:
                binding = _action_binding(nonce=hashlib.sha256(name.encode()).hexdigest()[:32])
                ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
                with _patched_clock(
                    _Clock(on_sleep=lambda: _write_raw(ack_path, raw))
                ), mock.patch.object(suite, "_emit_revocation_log"):
                    with self.assertRaises(AssertionError):
                        suite._await_revocation_action(binding, deadline=5.0)

        # The deadline is bounded: no acknowledgement, no resubmission, one request.
        with _control_environment() as control:
            binding = _action_binding(nonce="33" * 16)
            with _patched_clock(_Clock()), mock.patch.object(
                suite, "_emit_revocation_log"
            ):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=1.0)
            self.assertEqual(
                len(
                    [
                        name
                        for name in os.listdir(control)
                        if name.endswith(".request.json")
                    ]
                ),
                1,
            )

    def test_action_request_exclusive_create_preserves_foreign_files(self):
        # Losing the O_EXCL race must never delete the other party's file. The path
        # is absent at the pre-check and appears only between check and open.
        with _control_environment() as control:
            binding = _action_binding(nonce="66" * 16)
            request_path = os.path.join(
                control, binding["action_nonce"] + ".request.json"
            )
            real_open = os.open

            def racing_open(path, flags, mode=0o777):
                if path == request_path and flags & os.O_EXCL:
                    _write_raw(request_path, b"foreign-owner")
                return real_open(path, flags, mode)

            with _patched_clock(_Clock()), mock.patch.object(
                suite.os, "open", side_effect=racing_open
            ):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)
            self.assertTrue(
                os.path.exists(request_path), "foreign request file must survive EEXIST"
            )
            self.assertEqual(pathlib.Path(request_path).read_bytes(), b"foreign-owner")

        # An owned write failure cleans up the exact inode this call created.
        with _control_environment() as control:
            binding = _action_binding(nonce="77" * 16)
            request_path = os.path.join(
                control, binding["action_nonce"] + ".request.json"
            )
            with _patched_clock(_Clock()), mock.patch.object(
                suite, "_write_all", side_effect=OSError("short write")
            ):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)
            self.assertFalse(os.path.exists(request_path))

        # If the pathname was replaced after our open, cleanup must refuse to unlink
        # the replacement rather than delete an unowned file.
        with _control_environment() as control:
            binding = _action_binding(nonce="88" * 16)
            request_path = os.path.join(
                control, binding["action_nonce"] + ".request.json"
            )

            def replace_then_fail(descriptor, payload):
                os.unlink(request_path)
                _write_raw(request_path, b"replacement-owner")
                raise OSError("short write")

            with _patched_clock(_Clock()), mock.patch.object(
                suite, "_write_all", side_effect=replace_then_fail
            ):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)
            self.assertTrue(
                os.path.exists(request_path),
                "a replaced pathname must not be unlinked by this call",
            )
            self.assertEqual(
                pathlib.Path(request_path).read_bytes(), b"replacement-owner"
            )

    def test_ack_filesystem_negatives_refuse(self):
        forms = ("0644", "symlink", "directory")
        for form in forms:
            with self.subTest(ack=form), _control_environment() as control:
                binding = _action_binding(
                    nonce=hashlib.sha256(form.encode()).hexdigest()[:32]
                )
                ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
                target = os.path.join(control, "valid-ack.json")

                def publish_ack():
                    if form == "0644":
                        _write_private(
                            ack_path, _action_ack(binding), mode=0o644
                        )
                    elif form == "symlink":
                        _write_private(target, _action_ack(binding))
                        os.symlink(target, ack_path)
                    else:
                        os.mkdir(ack_path)

                with _patched_clock(_Clock(on_sleep=publish_ack)), mock.patch.object(
                    suite, "_emit_revocation_log"
                ):
                    with self.assertRaises(AssertionError):
                        suite._await_revocation_action(binding, deadline=5.0)

    def test_stale_wrong_identity_action_ack_refuses(self):
        # A wrong run/action/site/actor/arm/digest or status must fail before any
        # denial could be credited.
        wrong = {
            "run_nonce": {"run_nonce": "11" * 16},
            "action_nonce": {"action_nonce": "44" * 16},
            "site": {"site": "other-site.invalid"},
            "source_digest": {"source_digest": "cd" * 32},
            "operation_id": {"operation_id": "12" * 16},
            "actor": {"actor": "someone-else@example.invalid"},
            "arm": {"arm": "role_removed"},
            "action": {"action": "restore"},
            "baseline_digest": {"baseline_digest": "ef" * 32},
            "status": {"status": "pending"},
        }
        for name, override in wrong.items():
            with self.subTest(field=name), _control_environment() as control:
                binding = _action_binding(
                    nonce=hashlib.sha256(name.encode()).hexdigest()[:32]
                )
                ack = _action_ack(binding, **override)
                ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
                with _patched_clock(
                    _Clock(on_sleep=lambda: _write_private(ack_path, ack))
                ), mock.patch.object(suite, "_emit_revocation_log"):
                    with self.assertRaises(AssertionError):
                        suite._await_revocation_action(binding, deadline=5.0)

        # A missing key is not a partial match.
        with _control_environment() as control:
            binding = _action_binding(nonce="55" * 16)
            ack = _action_ack(binding)
            ack.pop("source_digest")
            ack_path = os.path.join(control, binding["action_nonce"] + ".ack.json")
            with _patched_clock(
                _Clock(on_sleep=lambda: _write_private(ack_path, ack))
            ), mock.patch.object(suite, "_emit_revocation_log"):
                with self.assertRaises(AssertionError):
                    suite._await_revocation_action(binding, deadline=5.0)


def make_revocation_harness():
    """Real case methods with only the native fixture and probe boundaries doubled.

    The class is created inside the factory so unittest never collects it.
    """

    class Harness(suite.PublicationNativeCase):
        residue_incomplete = False

        def __init__(self):
            super().__init__("runTest")
            self.created = []
            self.storage_keys = []
            self.actor = None
            self.patient = None
            self.prereq_calls = 0
            self.request_labels = []
            self.stage_calls = []
            self.stage_operations = []
            self.publish_calls = []
            self.inventory_calls = []
            self.await_calls = []
            self.probe_calls = []
            self.primary_error = None
            self.restore_action_error = None
            self.change_await_mode = None
            self.restored_enabled = True
            self.restored_clinician = True
            self._operation_counter = 0

        def runTest(self):
            pass

        def require_native_prerequisites(self):
            self.prereq_calls += 1
            self.actor = suite.SYNTHETIC_CLINICIAN
            self.patient = suite.SYNTHETIC_PATIENT
            return self.actor, self.patient

        def original_request(self, label):
            self.request_labels.append(label)
            return "request:" + label, b"bytes"

        def stage_and_commit(self, request):
            self.stage_calls.append(request)
            self._operation_counter += 1
            operation = hashlib.sha256(
                f"operation:{self._operation_counter}".encode()
            ).hexdigest()[:32]
            self.stage_operations.append(operation)
            return operation

        def publish(self, operation_id):
            self.publish_calls.append(operation_id)
            return object()

        def inventory_operation(self, operation_id):
            self.inventory_calls.append(operation_id)
            return False

        def revocation_source_digest(self):
            return REVOCATION_SOURCE_DIGEST

        def run_revocation_probe(self, document):
            self.probe_calls.append(document)
            mode = document["mode"]
            arm = document["arm"]
            if mode == "baseline":
                baseline = _revocation_baseline("arm:" + arm)
                return (
                    _revocation_response(
                        document,
                        enabled=True,
                        clinician=True,
                        baseline=baseline,
                        baseline_digest=probe.baseline_digest(baseline),
                    ),
                    baseline,
                )
            baseline = document["baseline"]
            if mode == "status_denial" and self.primary_error is not None:
                raise self.primary_error
            if mode in ("status_denial", "replay_denial"):
                enabled, clinician = (
                    (False, True) if arm == "disabled" else (True, False)
                )
                return (
                    _revocation_response(
                        document, enabled=enabled, clinician=clinician,
                        baseline=baseline,
                    ),
                    None,
                )
            if mode == "verify":
                return (
                    _revocation_response(
                        document, enabled=True, clinician=True, baseline=baseline
                    ),
                    None,
                )
            if mode == "restored":
                private = _revocation_baseline("arm:" + arm)
                return (
                    _revocation_response(
                        document,
                        enabled=self.restored_enabled,
                        clinician=self.restored_clinician,
                        baseline=private,
                        baseline_digest=probe.baseline_digest(private),
                    ),
                    private,
                )
            raise AssertionError("unexpected revocation probe mode")

        def await_revocation_action(self, binding, *, deadline):
            self.await_calls.append((dict(binding), deadline))
            if binding["action"] == "change" and self.change_await_mode:
                nonce = binding["action_nonce"]
                control = os.environ[suite.REVOCATION_CONTROL_DIR_ENV]
                request_path = os.path.join(control, nonce + ".request.json")
                if self.change_await_mode == "pre_issue":
                    # Drive the real channel into its pre-issuance refusal: the
                    # acknowledgement already exists, so no request is written.
                    _write_private(
                        os.path.join(control, nonce + ".ack.json"),
                        _action_ack(binding),
                    )
                    with mock.patch.object(suite, "_emit_revocation_log"):
                        suite._await_revocation_action(binding, deadline=deadline)
                if self.change_await_mode == "timeout_lost_request":
                    # Drive the real channel through request issuance and a bounded
                    # timeout, then remove the consumed request as an operator may.
                    ticker = _Clock()
                    ticker.now = time.monotonic()
                    ticker.on_sleep = lambda: setattr(ticker, "now", ticker.now + 1000.0)
                    with _patched_clock(ticker), mock.patch.object(
                        suite, "_emit_revocation_log"
                    ):
                        try:
                            suite._await_revocation_action(binding, deadline=deadline)
                        except BaseException as error:
                            os.unlink(request_path)
                            raise error
            if binding["action"] == "restore" and self.restore_action_error is not None:
                raise self.restore_action_error

    return Harness()


class RevocationArmOrchestrationTests(unittest.TestCase):
    """Two complete arms, independent bindings and one reserved restore handoff."""

    def _environment(self):
        return mock.patch.dict(
            os.environ,
            {
                suite.CASE_NONCE_ENV: REVOCATION_RUN_NONCE,
                suite.REVOCATION_VERIFIER_ENV: REVOCATION_VERIFIER,
            },
            clear=False,
        )

    def test_unsafe_control_directory_refuses_before_stage(self):
        cases = {}
        with tempfile.TemporaryDirectory() as base:
            base = os.path.realpath(base)
            good = os.path.join(base, "good")
            os.mkdir(good)
            os.chmod(good, 0o700)
            wide = os.path.join(base, "wide")
            os.mkdir(wide)
            os.chmod(wide, 0o755)
            link = os.path.join(base, "link")
            os.symlink(good, link)
            real_mid = os.path.join(base, "real-mid")
            os.mkdir(real_mid)
            os.chmod(real_mid, 0o700)
            child = os.path.join(real_mid, "child")
            os.mkdir(child)
            os.chmod(child, 0o700)
            mid = os.path.join(base, "mid")
            os.symlink(real_mid, mid)
            missing = os.path.join(base, "missing")
            cases = {
                "missing": missing,
                "relative": "relative/control",
                "mode": wide,
                "symlink": link,
                "intermediate_symlink": os.path.join(mid, "child"),
            }
            harness = make_revocation_harness()
            for name, value in cases.items():
                with self.subTest(control=name), mock.patch.dict(
                    os.environ,
                    {
                        suite.CASE_NONCE_ENV: REVOCATION_RUN_NONCE,
                        suite.REVOCATION_CONTROL_DIR_ENV: value,
                        suite.REVOCATION_VERIFIER_ENV: REVOCATION_VERIFIER,
                    },
                    clear=False,
                ):
                    with self.assertRaises(AssertionError):
                        harness.exercise_revocation_arm("disabled")
            self.assertEqual(harness.stage_calls, [])
            self.assertEqual(harness.prereq_calls, 0)

            # Missing verifier configuration refuses in the same pre-stage window.
            for name, verifier in {"absent": "", "same-as-actor": suite.SYNTHETIC_CLINICIAN}.items():
                with self.subTest(verifier=name), mock.patch.dict(
                    os.environ,
                    {
                        suite.CASE_NONCE_ENV: REVOCATION_RUN_NONCE,
                        suite.REVOCATION_CONTROL_DIR_ENV: good,
                        suite.REVOCATION_VERIFIER_ENV: verifier,
                    },
                    clear=False,
                ):
                    with self.assertRaises(AssertionError):
                        harness.exercise_revocation_arm("disabled")
            self.assertEqual(harness.stage_calls, [])

    def test_lost_change_acknowledgement_still_restores_once(self):
        # The real channel writes the change request, then times out with no ack; the
        # operator consumes/removes the request. Issuance must survive that removal and
        # drive exactly one restore handoff for both arms.
        for arm in ("disabled", "role_removed"):
            with self.subTest(arm=arm), self._environment(), _control_environment():
                harness = make_revocation_harness()
                harness.change_await_mode = "timeout_lost_request"
                with self.assertRaises(AssertionError) as caught:
                    harness.exercise_revocation_arm(arm)
                self.assertEqual(str(caught.exception), suite.SAFE_ACTION_TIMEOUT)
                self.assertTrue(getattr(caught.exception, "issued", False))
                self.assertEqual(
                    [binding["action"] for binding, _ in harness.await_calls],
                    ["change", "restore"],
                )
                self.assertTrue(type(harness).residue_incomplete)
                restored = [
                    document
                    for document in harness.probe_calls
                    if document["mode"] == "restored"
                ]
                self.assertEqual(len(restored), 1)
                self.assertEqual(restored[0]["arm"], arm)
                self.assertNotIn(
                    suite.SAFE_RESTORE_INCOMPLETE,
                    getattr(caught.exception, "__notes__", []),
                )

    def test_pre_issuance_failure_does_not_restore_or_claim_incomplete(self):
        # A refusal before the request exists cannot have changed the actor: no restore
        # handoff, no restored probe and no invented incomplete residue claim.
        for arm in ("disabled", "role_removed"):
            with self.subTest(arm=arm), self._environment(), _control_environment():
                harness = make_revocation_harness()
                harness.change_await_mode = "pre_issue"
                with self.assertRaises(AssertionError) as caught:
                    harness.exercise_revocation_arm(arm)
                self.assertEqual(
                    [binding["action"] for binding, _ in harness.await_calls],
                    ["change"],
                )
                self.assertFalse(type(harness).residue_incomplete)
                self.assertFalse(getattr(caught.exception, "issued", True))
                self.assertEqual(
                    [
                        document["mode"]
                        for document in harness.probe_calls
                        if document["mode"] == "restored"
                    ],
                    [],
                )
                self.assertFalse(
                    any(
                        document["mode"] == "restored"
                        for document in harness.probe_calls
                    )
                )
                self.assertNotIn(
                    suite.SAFE_RESTORE_INCOMPLETE,
                    getattr(caught.exception, "__notes__", []),
                )

    def test_restore_attempt_preserves_primary_failure(self):
        with self._environment(), _control_environment():
            harness = make_revocation_harness()
            primary = AssertionError("primary orchestration failure")
            harness.primary_error = primary
            with self.assertRaises(AssertionError) as caught:
                harness.exercise_revocation_arm("disabled")
            self.assertIs(caught.exception, primary)
            self.assertEqual(
                [binding["action"] for binding, _ in harness.await_calls],
                ["change", "restore"],
            )
            self.assertTrue(type(harness).residue_incomplete)
            self.assertNotIn(
                suite.SAFE_RESTORE_INCOMPLETE, getattr(primary, "__notes__", [])
            )

        # A failed restore handoff never replaces the primary failure; it adds the
        # fixed restoration-incomplete note.
        with self._environment(), _control_environment():
            harness = make_revocation_harness()
            primary = AssertionError("primary orchestration failure")
            harness.primary_error = primary
            harness.restore_action_error = RuntimeError("restore refused")
            with self.assertRaises(AssertionError) as caught:
                harness.exercise_revocation_arm("disabled")
            self.assertIs(caught.exception, primary)
            self.assertIn(
                suite.SAFE_RESTORE_INCOMPLETE, getattr(primary, "__notes__", [])
            )
            self.assertEqual(
                [binding["action"] for binding, _ in harness.await_calls],
                ["change", "restore"],
            )

    def test_restore_ack_without_state_restored_blocks_next_arm(self):
        with self._environment(), _control_environment():
            harness = make_revocation_harness()
            harness.restored_enabled = False
            with self.assertRaises(AssertionError) as caught:
                harness.exercise_revocation_arm("disabled")
            self.assertEqual(str(caught.exception), suite.SAFE_RESTORE_INCOMPLETE)
            self.assertIsNone(caught.exception.__cause__)
            self.assertTrue(type(harness).residue_incomplete)
            self.assertEqual(
                [binding["action"] for binding, _ in harness.await_calls],
                ["change", "restore"],
            )
            # The acknowledgement arrived, but the fresh restored probe did not
            # observe the baseline; no later arm may start.
            self.assertEqual(
                {document["arm"] for document in harness.probe_calls}, {"disabled"}
            )
            self.assertNotIn("role_removed", harness.request_labels)
            # The failed arm stops the sequential case loop, so a second arm never
            # begins in the same case body.
            self.assertNotIn(
                "role_removed",
                [document["arm"] for document in harness.probe_calls],
            )

    def test_both_arms_have_independent_baselines_and_saved_controls(self):
        with self._environment(), _control_environment():
            harness = make_revocation_harness()
            harness._revocation_case_deadline = suite.time.monotonic() + 1200.0
            harness.exercise_revocation_arm("disabled")
            harness.exercise_revocation_arm("role_removed")

        self.assertEqual(harness.prereq_calls, 1)
        self.assertEqual(
            harness.request_labels,
            ["revocation-disabled", "revocation-role_removed"],
        )
        self.assertEqual(len(set(harness.stage_calls)), 2)
        self.assertEqual(harness.publish_calls, harness.stage_operations)
        self.assertEqual(harness.inventory_calls, harness.stage_operations)

        actions = [(binding["arm"], binding["action"]) for binding, _ in harness.await_calls]
        self.assertEqual(
            actions,
            [
                ("disabled", "change"),
                ("disabled", "restore"),
                ("role_removed", "change"),
                ("role_removed", "restore"),
            ],
        )
        nonces = [binding["action_nonce"] for binding, _ in harness.await_calls]
        self.assertEqual(len(nonces), len(set(nonces)))
        self.assertTrue(all(len(nonce) == 32 for nonce in nonces))

        baselines = {}
        for document in harness.probe_calls:
            if document["mode"] == "baseline":
                baselines[document["arm"]] = document
        self.assertEqual(set(baselines), {"disabled", "role_removed"})
        self.assertEqual(
            baselines["disabled"]["operation_id"], harness.stage_operations[0]
        )
        self.assertEqual(
            baselines["role_removed"]["operation_id"], harness.stage_operations[1]
        )
        for arm in ("disabled", "role_removed"):
            with self.subTest(arm=arm):
                operation = baselines[arm]["operation_id"]
                arm_calls = [
                    (binding, deadline)
                    for binding, deadline in harness.await_calls
                    if binding["arm"] == arm
                ]
                self.assertEqual(
                    [binding["action"] for binding, _ in arm_calls],
                    ["change", "restore"],
                )
                for binding, _ in arm_calls:
                    self.assertEqual(binding["operation_id"], operation)
                    self.assertEqual(binding["run_nonce"], REVOCATION_RUN_NONCE)
                    self.assertEqual(binding["actor"], suite.SYNTHETIC_CLINICIAN)
                    self.assertEqual(
                        binding["baseline_roles"], REVOCATION_ROLES
                    )
                modes = [
                    document["mode"]
                    for document in harness.probe_calls
                    if document["arm"] == arm
                ]
                self.assertEqual(
                    modes,
                    [
                        "baseline",
                        "status_denial",
                        "replay_denial",
                        "verify",
                        "restored",
                    ],
                )
                self.assertEqual(
                    len(modes), len(set(modes)), "one call per probe mode"
                )
                arm_invocations = [
                    document["invocation_nonce"]
                    for document in harness.probe_calls
                    if document["arm"] == arm
                ]
                self.assertEqual(len(arm_invocations), 5)
                self.assertEqual(
                    len(arm_invocations),
                    len(set(arm_invocations)),
                    "every probe invocation needs a fresh nonce",
                )
                self.assertNotIn(
                    REVOCATION_RUN_NONCE, arm_invocations
                )

        # Each arm has its own baseline value; the second arm never reuses the first
        # arm's denial, restoration or action identity.
        first_baseline = _revocation_baseline("arm:disabled")
        second_baseline = _revocation_baseline("arm:role_removed")
        self.assertNotEqual(
            probe.baseline_digest(first_baseline),
            probe.baseline_digest(second_baseline),
        )
        self.assertEqual(
            [binding["baseline_digest"] for binding, _ in harness.await_calls],
            [
                probe.baseline_digest(first_baseline),
                probe.baseline_digest(first_baseline),
                probe.baseline_digest(second_baseline),
                probe.baseline_digest(second_baseline),
            ],
        )


class RevocationSourcePins(unittest.TestCase):
    """Supplemental pins: source coverage and forbidden orchestration shortcuts."""

    def _helper_sources(self):
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        sources = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name.startswith(
                ("_run_revocation", "_await_revocation", "_emit_revocation",
                 "_read_private_baseline", "_validate_control")
            ):
                sources[node.name] = ast.unparse(node)
        class_node = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PublicationNativeCase"
        )
        for child in class_node.body:
            if isinstance(child, ast.FunctionDef) and child.name in {
                "run_revocation_probe",
                "await_revocation_action",
                "exercise_revocation_arm",
                "_restore_revocation_arm",
                "revocation_source_digest",
                "_revocation_document",
            }:
                sources[child.name] = ast.unparse(child)
        return sources

    def test_source_digest_covers_the_revocation_probe_module(self):
        entries = runner.source_inputs(APP_ROOT)
        by_name = {name: digest for name, digest in entries}
        relative = "clinic_photos/tests/revocation_probe.py"
        self.assertIn(relative, by_name)
        self.assertEqual(
            by_name[relative],
            hashlib.sha256((APP_ROOT / relative).read_bytes()).hexdigest(),
        )
        digest = runner.source_digest(APP_ROOT)
        self.assertEqual(
            digest,
            hashlib.sha256(json.dumps(entries, sort_keys=True).encode("utf-8")).hexdigest(),
        )
        without = [entry for entry in entries if entry[0] != relative]
        self.assertNotEqual(
            hashlib.sha256(json.dumps(without, sort_keys=True).encode("utf-8")).hexdigest(),
            digest,
        )

    def test_no_helper_writes_users_or_mutates_operator_allowlist(self):
        sources = self._helper_sources()
        self.assertIn("_run_revocation_probe", sources)
        self.assertIn("exercise_revocation_arm", sources)
        for name, source in sources.items():
            with self.subTest(helper=name):
                for forbidden in (
                    "set_value",
                    "add_roles",
                    "remove_roles",
                    "delete_doc",
                    "ignore_permissions",
                    "RECOVERY_OPERATOR",
                    "recovery_operator",
                    "db.commit",
                    "db.rollback",
                    "delete_doc",
                ):
                    self.assertNotIn(forbidden, source)
                # No raw traceback or child diagnostic is formatted into a message.
                self.assertNotIn("stderr.decode", source)
                self.assertNotIn("stdout.decode", source)

    def test_probe_child_is_the_inert_revocation_module_not_publication(self):
        sources = self._helper_sources()
        source = sources["_run_revocation_probe"]
        self.assertIn("REVOCATION_PROBE_MODULE", source)
        self.assertNotIn("PROCESS_MODULE", source)
        self.assertNotIn("clinic_photos.publication.process", source)

    def test_both_arms_run_and_restoration_is_in_a_finally_path(self):
        node = _native_body(
            "PublicationAccessNativeCases",
            "test_disabled_or_revoked_actor_cannot_replay",
        )
        body = ast.unparse(node)
        self.assertIn("exercise_revocation_arm('disabled')", body)
        self.assertIn("exercise_revocation_arm('role_removed')", body)
        self.assertLess(
            body.index("exercise_revocation_arm('disabled')"),
            body.index("exercise_revocation_arm('role_removed')"),
        )
        self.assertNotIn("subTest", body)
        self.assertIn("exercise_revocation_arm", body)
        self.assertNotIn("self.fail(", body)
        self.assertNotIn("skip", _decorator_names(node))

        exercise = ast.parse(
            ast.unparse(
                next(
                    child
                    for child in next(
                        block
                        for block in ast.parse(MODULE_PATH.read_text(encoding="utf-8")).body
                        if isinstance(block, ast.ClassDef)
                        and block.name == "PublicationNativeCase"
                    ).body
                    if isinstance(child, ast.FunctionDef)
                    and child.name == "exercise_revocation_arm"
                )
            )
        )
        tries = [node for node in ast.walk(exercise) if isinstance(node, ast.Try)]
        self.assertTrue(any(candidate.finalbody for candidate in tries))
        self.assertIn("SAFE_RESTORE_INCOMPLETE", ast.unparse(exercise))


if __name__ == "__main__":
    unittest.main()
