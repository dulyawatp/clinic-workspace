"""Harness tests for the isolated native-case matrix, its runner and its evidence.

Framework-independent: these tests parse the integration module's source, the case
manifest and the runner, and they exercise the runner's parser/aggregator against
crafted raw output. They prove the matrix is complete, that acceptance can only come
from execution evidence emitted by the selected test, and that every known
false-positive path is rejected. They do NOT execute bench or a site.
"""

import ast
import contextlib
import hashlib
import importlib
import inspect
import io
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock

from clinic_photos.tests import run_isolated_cases as runner

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SITE_SUITE = APP_ROOT / "clinic_photos" / "tests" / "test_file_policy.py"
PUBLICATION_SUITE = APP_ROOT / "clinic_photos" / "tests" / "test_publication.py"
MANIFEST_PATH = APP_ROOT / "clinic_photos" / "tests" / "case_manifest.json"
RUNNER_PATH = APP_ROOT / "clinic_photos" / "tests" / "run_isolated_cases.py"

MODULE = "clinic_photos.tests.test_file_policy"
CLASS_NAME = "ProtectedFileBoundaryTests"
CLASS_ID = f"{MODULE}.{CLASS_NAME}"
PUBLICATION_MODULE = "clinic_photos.tests.test_publication"
NONCE = "0123456789abcdef0123456789abcdef"

SUITE_FILES = {
    MODULE: SITE_SUITE,
    PUBLICATION_MODULE: PUBLICATION_SUITE,
}

def case_id(method):
    return f"{MODULE}.{CLASS_NAME}.{method}"


def case_source(case, manifest_object=None):
    """Return (source, class_name) for a manifest case's declaring suite."""
    manifest_object = manifest_object or manifest()
    module = runner.case_module(manifest_object, case)
    path = SUITE_FILES[module]
    return path.read_text(encoding="utf-8"), runner.case_class(manifest_object, case)


def _marker(marker, payload):
    return f"{marker} {json.dumps(payload, sort_keys=True)}"


class FakeStream:
    """Minimal writable stream that records what the emitter wrote and flushed."""

    def __init__(self, *, writable=True, write_error=None, flush_error=None):
        self.closed = False
        self.written = []
        self.flushed = 0
        self._writable = writable
        self._write_error = write_error
        self._flush_error = flush_error

    def writable(self):
        return self._writable

    def write(self, text):
        if self._write_error is not None:
            raise self._write_error
        self.written.append(text)
        return len(text)

    def flush(self):
        if self._flush_error is not None:
            raise self._flush_error
        self.flushed += 1


@contextlib.contextmanager
def marker_streams(original):
    """Replace ``sys.stdout`` with a capture and ``sys.__stdout__`` with a fake.

    Test-only patching: this is the seam Frappe's buffered result uses (it swaps
    ``sys.stdout``), so the emitter must keep reaching the original stream.
    """
    previous_stdout = sys.stdout
    previous_original = sys.__stdout__
    sys.stdout = io.StringIO()
    sys.__stdout__ = original
    try:
        yield sys.stdout
    finally:
        sys.stdout = previous_stdout
        sys.__stdout__ = previous_original


@contextlib.contextmanager
def case_nonce(value):
    previous = os.environ.get(runner.CASE_NONCE_ENV)
    if value is None:
        os.environ.pop(runner.CASE_NONCE_ENV, None)
    else:
        os.environ[runner.CASE_NONCE_ENV] = value
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(runner.CASE_NONCE_ENV, None)
        else:
            os.environ[runner.CASE_NONCE_ENV] = previous


def marker_lines(stream):
    """Parse every marker line written to a fake stream, in write order."""
    parsed = []
    for chunk in stream.written:
        for line in chunk.splitlines():
            marker, _, payload = line.partition(" ")
            if marker in (
                runner.BEGIN_MARKER,
                runner.END_MARKER,
                runner.RESIDUE_MARKER,
            ):
                parsed.append((marker, json.loads(payload)))
    return parsed


class FakeCase:
    """Stand-in for ``unittest.TestCase`` at the decorator seam."""

    def __init__(self, identity):
        self._identity = identity
        self.failures = []

    def id(self):
        return self._identity

    def fail(self, message):
        self.failures.append(message)
        raise AssertionError(message)


@contextlib.contextmanager
def site_suite_module():
    """Import the native suite module with a minimal Frappe import stub.

    Only the module-level imports need a placeholder so the marker producer can be
    exercised without a site. This is a fixture, not Frappe behaviour: no MRO,
    transaction, permission or HTTP semantics are claimed.
    """
    frappe = types.ModuleType("frappe")
    frappe.set_user = lambda user: None
    frappe.db = types.SimpleNamespace(exists=lambda *args, **kwargs: False)

    tests_package = types.ModuleType("frappe.tests")
    tests_utils = types.ModuleType("frappe.tests.utils")

    class FrappeTestCase(unittest.TestCase):
        super_teardowns = []

        @classmethod
        def tearDownClass(cls):
            cls.super_teardowns.append(cls.__name__)

    tests_utils.FrappeTestCase = FrappeTestCase
    tests_package.utils = tests_utils
    frappe.tests = tests_package

    installed = {
        name: sys.modules.get(name)
        for name in ("frappe", "frappe.tests", "frappe.tests.utils", MODULE)
    }
    sys.modules["frappe"] = frappe
    sys.modules["frappe.tests"] = tests_package
    sys.modules["frappe.tests.utils"] = tests_utils
    sys.modules.pop(MODULE, None)
    try:
        yield importlib.import_module(MODULE), FrappeTestCase
    finally:
        for name, previous in installed.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


def evidence_output(
    method,
    *,
    nonce=NONCE,
    identity=None,
    end_identity=None,
    end_status="pass",
    count=1,
    terminal="OK",
    begin=True,
    end=True,
    residue=True,
    residue_id=None,
    residue_nonce=None,
    residue_rows=(("File", "FILE-1"),),
    storage_keys=("/private/files/clinic-photo-fixture.png",),
    skip=False,
    extra_lines=(),
):
    """Build one case's raw output, optionally with a specific defect."""
    identity = identity or case_id(method)
    end_identity = end_identity or identity
    residue_nonce = nonce if residue_nonce is None else residue_nonce
    residue_id = residue_id or identity.rsplit(".", 1)[0]
    lines = []
    if begin:
        lines.append(_marker(runner.BEGIN_MARKER, {"id": identity, "nonce": nonce}))
    if end:
        lines.append(
            _marker(
                runner.END_MARKER,
                {"id": end_identity, "nonce": nonce, "status": end_status},
            )
        )
    if residue:
        lines.append(
            _marker(
                runner.RESIDUE_MARKER,
                {
                    "id": residue_id,
                    "nonce": residue_nonce,
                    "rows": [list(row) for row in residue_rows],
                    "storage_keys": list(storage_keys),
                },
            )
        )
    lines.extend(extra_lines)
    lines.extend(
        [
            ".",
            "-" * 70,
            f"Ran {count} test{'s' if count != 1 else ''} in 0.001s",
            "",
            terminal,
            "",
        ]
    )
    if skip:
        lines = [line for line in lines]
        lines[-2] = "OK (skipped=1)"
    return "\n".join(lines)


def manifest():
    return runner.load_manifest(MANIFEST_PATH)


def executable_cases(manifest_object=None):
    return runner.executable_cases(manifest_object or manifest())


def blocked_cases(manifest_object=None):
    return runner.blocked_cases(manifest_object or manifest())


def nonce_for(case):
    """Deterministic per-case nonce: one runner invocation per case, never shared."""
    return hashlib.sha256(case["id"].encode("utf-8")).hexdigest()[:32]


def accepted_results(cases, *, rows=("File", "FILE-1"), keys=()):
    manifest_object = manifest()
    return [
        {
            "case": case["id"],
            "nonce": nonce_for(case),
            "exit_code": 0,
            "output": evidence_output(
                case["method"],
                nonce=nonce_for(case),
                identity=runner.expected_case_id(manifest_object, case),
                residue_rows=(rows,),
                storage_keys=keys,
            ),
        }
        for case in cases
    ]


RECORDED_AT = "2026-11-08T10:00:00Z"


SITE = "site.test"


def run_evidence(results, *, site=SITE, root=None, manifest_path=None, manifest_object=None):
    return runner.run_evidence(
        manifest_object or manifest(),
        site,
        results,
        source_root=str(root or APP_ROOT),
        manifest_path=str(manifest_path or MANIFEST_PATH),
        recorded_at=RECORDED_AT,
    )


def aggregate(
    manifest_object,
    results,
    confirmation,
    run,
    *,
    root=None,
    manifest_path=None,
    site=SITE,
):
    return runner.aggregate(
        manifest_object,
        results,
        confirmation,
        run=run,
        source_root=str(root or APP_ROOT),
        manifest_path=str(manifest_path or MANIFEST_PATH),
        expected_site=site,
    )


def _after_run(run):
    """A disposal timestamp that is provably after the run's own record."""
    import datetime

    text_value = run["recorded_at"].replace("Z", "+00:00")
    moment = datetime.datetime.fromisoformat(text_value) + datetime.timedelta(hours=1)
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def disposal_for(run, rows, keys):
    """Build the versioned, identity-complete disposal record for a run."""
    return {
        "schema": runner.EVIDENCE_SCHEMA,
        "run_id": run["run_id"],
        "site": run["site"],
        "project": run["project"],
        "source_root": run["source_root"],
        "source_digest": run["source_digest"],
        "contract_digest": run["contract_digest"],
        "recorded_at": run["recorded_at"],
        "case_nonces": {entry["case"]: entry["nonce"] for entry in run["cases"]},
        "raw_output_sha256": {
            entry["case"]: entry["raw_output_sha256"] for entry in run["cases"]
        },
        "residue_rows": [list(row) for row in rows],
        "storage_keys": list(keys),
        "disposal": {
            "observed": True,
            "disposed_by": "operator@example.test",
            "method": "destroy the disposable site after the isolated run",
            "observed_at": _after_run(run),
            "verified_absent": [list(row) for row in rows] + list(keys),
        },
    }


def cleanup_for(results, run=None, *, rows=None, keys=None):
    """Build the versioned disposal record the runner requires for acceptance."""
    run = run or run_evidence(results)
    run_rows, run_keys = runner.run_residue(run)
    return disposal_for(
        run,
        list(run_rows if rows is None else rows),
        list(run_keys if keys is None else keys),
    )


def disposable_tree(edit=None):
    """Copy the app source tree to a temp dir, optionally editing one file."""
    import shutil

    tmp = tempfile.mkdtemp(prefix="task10-src-")
    target = pathlib.Path(tmp) / "app"
    shutil.copytree(APP_ROOT, target, ignore=shutil.ignore_patterns("__pycache__"))
    if edit:
        edit(target)
    return target


def case_id_from_result(result):
    for case in manifest()["cases"]:
        if case["id"] == result["case"]:
            return runner.expected_case_id(manifest(), case)
    raise AssertionError(result["case"])


def _class_node(source, class_name=CLASS_NAME):
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return node
    raise AssertionError(f"case class {class_name!r} not found")


def _methods(source, class_name=CLASS_NAME):
    return [
        child.name
        for child in _class_node(source, class_name).body
        if isinstance(child, ast.FunctionDef) and child.name.startswith("test_")
    ]


def _method_source(source, name, class_name=CLASS_NAME):
    for child in _class_node(source, class_name).body:
        if isinstance(child, ast.FunctionDef) and child.name == name:
            return ast.get_source_segment(source, child) or ""
    raise AssertionError(f"method {name!r} not found")


def _case_method_source(case, manifest_object=None):
    manifest_object = manifest_object or manifest()
    source, class_name = case_source(case, manifest_object)
    return _method_source(source, case["method"], class_name)


def _case_decorators(case, manifest_object=None):
    manifest_object = manifest_object or manifest()
    source, class_name = case_source(case, manifest_object)
    node = _class_node(source, class_name)
    for child in node.body:
        if isinstance(child, ast.FunctionDef) and child.name == case["method"]:
            names = []
            for decorator in child.decorator_list:
                target = decorator.func if isinstance(decorator, ast.Call) else decorator
                if isinstance(target, ast.Attribute):
                    names.append(target.attr)
                elif isinstance(target, ast.Name):
                    names.append(target.id)
            return names
    raise AssertionError(f"method {case['method']!r} not found")


class CaseManifestTests(unittest.TestCase):
    def setUp(self):
        self.source = SITE_SUITE.read_text(encoding="utf-8")
        self.manifest = manifest()

    def test_manifest_is_structurally_valid(self):
        self.assertEqual(runner.validate_manifest(self.manifest), [])

    def test_manifest_identifies_this_module_and_class(self):
        # The manifest-level module/class stay the default for cases that declare no
        # override; per-case overrides are the Task 10 addition.
        self.assertEqual(self.manifest["module"], MODULE)
        self.assertEqual(self.manifest["case_class"], _class_node(self.source).name)
        self.assertEqual(runner.case_module(self.manifest, {"id": "x"}), MODULE)
        self.assertEqual(
            runner.case_class(self.manifest, {"id": "x"}), CLASS_NAME
        )

    def test_every_case_maps_to_exactly_one_test_method(self):
        # Identity is (module, class, method): two suites may reuse a method name.
        identities = []
        for case in self.manifest["cases"]:
            source, class_name = case_source(case, self.manifest)
            self.assertIn(case["method"], _methods(source, class_name))
            identities.append(
                (runner.case_module(self.manifest, case), class_name, case["method"])
            )
        self.assertEqual(len(identities), len(set(identities)))
        for module, class_name, _ in set(identities):
            source = SUITE_FILES[module].read_text(encoding="utf-8")
            expected = {
                (module, class_name, method) for method in _methods(source, class_name)
            }
            declared = {identity for identity in identities if identity[:2] == (module, class_name)}
            self.assertEqual(declared, expected, module)

    def test_publication_cases_are_declared_per_case(self):
        publication = [
            case
            for case in self.manifest["cases"]
            if runner.case_module(self.manifest, case) == PUBLICATION_MODULE
        ]
        self.assertTrue(publication, "no native publication case is manifested")
        for case in publication:
            self.assertIn("case_class", case)
            self.assertIn("readiness", case)

    def test_every_case_maps_to_exactly_one_test_method_legacy(self):
        # A schema-1 manifest has no per-case overrides; the fallback must still work.
        legacy = {
            "schema": 1,
            "app": "clinic_photos",
            "module": MODULE,
            "case_class": CLASS_NAME,
            "cases": [
                {
                    key: case[key]
                    for key in (
                        "id",
                        "method",
                        "owner",
                        "expects_denial",
                        "latches_abort",
                        "expected_exception",
                        "expected_owner_reasons",
                        "roles",
                        "guard",
                        "description",
                    )
                }
                for case in self.manifest["cases"]
                if runner.case_module(self.manifest, case) == MODULE
            ],
        }
        self.assertEqual(runner.validate_manifest(legacy), [])
        self.assertEqual(
            runner.executable_cases(legacy), legacy["cases"]
        )
        self.assertEqual(runner.blocked_cases(legacy), [])

    def test_denial_and_abort_semantics_are_declared_separately(self):
        for case in self.manifest["cases"]:
            with self.subTest(case=case["id"]):
                self.assertNotIn("aborting", case)
                self.assertIn("expects_denial", case)
                self.assertIn("latches_abort", case)
                if not case["expects_denial"]:
                    self.assertFalse(case["latches_abort"])
                    self.assertIsNone(case["expected_exception"])

    def test_pre_boundary_denials_do_not_claim_an_abort_latch(self):
        pre_boundary = {
            case["id"]: case
            for case in self.manifest["cases"]
            if case["expects_denial"] and not case["latches_abort"]
        }
        self.assertEqual(
            set(pre_boundary),
            {
                "mutate.encoded_url_denied",
                "derivative.thumbnail_denied",
                "derivative.optimize_denied",
            },
        )
        for case_id_, case in pre_boundary.items():
            with self.subTest(case=case_id_):
                self.assertEqual(case["expected_owner_reasons"], [])
                body = _case_method_source(case, self.manifest)
                # The case itself must prove nothing was notified or latched.
                self.assertIn("None,", body)

    def test_latched_denials_name_the_notifying_boundary(self):
        for case in self.manifest["cases"]:
            if not case["latches_abort"]:
                continue
            with self.subTest(case=case["id"]):
                body = _case_method_source(case, self.manifest)
                if case["owner"] == "fresh":
                    self.assertIn("self._owner_scope()", body)
                    for reason in case["expected_owner_reasons"]:
                        self.assertIn(f'"{reason}"', body)
                else:
                    self.assertIn("_assert_owner_gate(", body)

    def test_matrix_covers_both_clinician_roles(self):
        roles = {role for case in self.manifest["cases"] for role in case["roles"]}
        self.assertIn("Physician", roles)
        self.assertIn("Nursing User", roles)
        self.assertTrue(
            [
                case
                for case in self.manifest["cases"]
                if "Nursing User" in case["roles"] and case["expects_denial"]
            ]
        )

    def test_each_denial_case_reaches_only_one_guard(self):
        mutations = {
            case["id"]
            for case in self.manifest["cases"]
            if case["id"].startswith(("mutate.", "delete.", "content.", "derivative."))
        }
        self.assertGreaterEqual(len(mutations), 12)
        for required in (
            "mutate.owner_change_denied",
            "mutate.privacy_flip_denied",
            "mutate.url_move_denied",
            "mutate.encoded_url_denied",
            "content.replace_denied",
            "derivative.thumbnail_denied",
            "derivative.optimize_denied",
            "delete.version_denied",
            "delete.version_denied_nursing",
            "delete.annotation_file_denied",
            "delete.original_denied",
            "mutate.original_reference_denied",
        ):
            self.assertIn(required, mutations)


class HarnessSourceTests(unittest.TestCase):
    def setUp(self):
        self.source = SITE_SUITE.read_text(encoding="utf-8")

    def test_abort_no_longer_skips_the_case(self):
        self.assertEqual(self.source.count("SkipTest"), 1)
        self.assertIn("candidate role", self.source)
        setUp_body = _method_source(self.source, "setUp")
        self.assertNotIn("SkipTest", setUp_body)
        self.assertIn("raise AssertionError", setUp_body)

    def test_stale_execution_is_a_failure_not_a_skip(self):
        setUp_body = _method_source(self.source, "setUp")
        for marker in (
            "publication abort is already latched",
            "publication owner is still registered",
            "publication context is still bound",
        ):
            self.assertIn(marker, setUp_body)

    def test_fixture_never_clears_the_production_latch(self):
        for forbidden in (
            "setattr(frappe.local",
            "delattr(frappe.local",
            "_abort_publication",
            "clinic_photos_publication_aborted =",
        ):
            self.assertNotIn(forbidden, self.source)
        reads = self.source.count("getattr(frappe.local, ABORT_ATTR")
        self.assertGreaterEqual(reads, 4)
        self.assertEqual(self.source.count("ABORT_ATTR"), reads + 1)

    def test_every_case_emits_its_own_evidence(self):
        self.assertEqual(self.source.count("    @_case\n    def test_"), len(_methods(self.source)))
        wrapper = _method_source(self.source, "setUp")  # sanity: helper defined at module level
        self.assertNotIn("@_case", wrapper)
        self.assertIn("self.id()", self.source)
        self.assertIn("BEGIN_MARKER", self.source)
        self.assertIn("END_MARKER", self.source)
        self.assertIn("CASE_NONCE_ENV", self.source)

    def test_case_owner_is_created_per_case_and_setup_is_separate(self):
        setUp_body = _method_source(self.source, "setUp")
        self.assertIn("self.owner = _PendingOwner()", setUp_body)
        setup_class = _method_source(self.source, "setUpClass")
        self.assertIn("cls.setup_owner = _PendingOwner()", setup_class)
        self.assertNotIn("cls.owner", self.source)

    def test_original_registration_is_owner_scoped_before_the_insert(self):
        factory = _method_source(self.source, "_create_set")
        self.assertLess(
            factory.index("owned_publication_transaction("),
            factory.index("_create_set_in_owner_scope("),
        )
        in_scope = _method_source(self.source, "_create_set_in_owner_scope")
        self.assertLess(
            in_scope.index('"doctype": "Clinic Photo"'),
            in_scope.index("photo.insert("),
        )
        self.assertEqual(self.source.count("_create_set_in_owner_scope("), 2)

    def test_encoded_spelling_case_uses_the_stored_url(self):
        body = _method_source(self.source, "test_non_author_encoded_url_spelling_denied")
        self.assertIn("original_url = file_doc.file_url", body)
        self.assertIn("encoded = original_url.replace(", body)
        self.assertIn("self.assertNotEqual(encoded, original_url)", body)
        self.assertIn("canonical_private_key(encoded), original_url", body)
        self.assertNotIn('values["original_file"].replace', body)

    def test_teardown_never_deletes_protected_rows(self):
        teardown = _method_source(self.source, "tearDownClass")
        self.assertIn("RESIDUE_MARKER", teardown)
        self.assertIn("finally:", teardown)
        self.assertIn("super().tearDownClass()", teardown)
        self.assertNotIn('delete_doc("File"', teardown)
        self.assertNotIn('delete_doc("Clinic Photo"', teardown)
        cleanup = _method_source(self.source, "_remove_non_protected_identities")
        self.assertIn('"User"', cleanup)
        self.assertNotIn('"File"', cleanup)

    def test_runner_documents_that_it_does_not_execute_by_default(self):
        source = RUNNER_PATH.read_text(encoding="utf-8")
        self.assertIn("does **not** claim any native result", source)
        self.assertIn("fail-closed", source)


class ParserEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.method = "test_reserved_test_keys_stay_in_the_orphan_namespace"
        self.expected = case_id(self.method)

    def parse(self, output, exit_code=0, nonce=NONCE, expected=None):
        return runner.parse_case_output(
            exit_code, output, expected_id=expected or self.expected, nonce=nonce
        )

    def test_valid_evidence_passes(self):
        parsed = self.parse(evidence_output(self.method))
        self.assertEqual(parsed["status"], "passed")
        self.assertEqual(parsed["identity"], self.expected)
        self.assertEqual(parsed["residue"], [["File", "FILE-1"]])

    def test_absent_markers_are_rejected(self):
        parsed = self.parse(evidence_output(self.method, begin=False, end=False))
        self.assertEqual(parsed["status"], "no-evidence")

    def test_single_marker_pair_is_required(self):
        doubled = evidence_output(self.method) + evidence_output(self.method)
        self.assertEqual(self.parse(doubled)["status"], "no-evidence")

    def test_wrong_method_identity_is_rejected(self):
        wrong = evidence_output(self.method, identity=case_id("test_something_else"))
        parsed = self.parse(wrong)
        self.assertEqual(parsed["status"], "wrong-identity")
        self.assertIn("test_something_else", parsed["detail"])

    def test_end_identity_mismatch_is_rejected(self):
        wrong = evidence_output(
            self.method, end_identity=case_id("test_something_else")
        )
        self.assertEqual(self.parse(wrong)["status"], "wrong-identity")

    def test_stale_invocation_nonce_is_rejected(self):
        stale = evidence_output(self.method, nonce="f" * 32)
        self.assertEqual(self.parse(stale)["status"], "wrong-identity")

    def test_failed_end_marker_is_rejected(self):
        parsed = self.parse(evidence_output(self.method, end_status="fail"))
        self.assertEqual(parsed["status"], "failed")

    def test_missing_summary_is_rejected(self):
        broken = "\n".join(
            line
            for line in evidence_output(self.method).splitlines()
            if not line.startswith("Ran ")
        )
        self.assertEqual(self.parse(broken)["status"], "missing")

    def test_two_summaries_are_rejected(self):
        doubled = evidence_output(self.method).replace(
            "Ran 1 test in 0.001s", "Ran 1 test in 0.001s\nRan 1 test in 0.002s"
        )
        self.assertEqual(self.parse(doubled)["status"], "multiple-summaries")

    def test_zero_selected_tests_is_rejected(self):
        parsed = self.parse(evidence_output(self.method, count=0))
        self.assertEqual(parsed["status"], "wrong-cardinality")

    def test_multiple_selected_tests_are_rejected(self):
        parsed = self.parse(evidence_output(self.method, count=2))
        self.assertEqual(parsed["status"], "wrong-cardinality")

    def test_skip_is_rejected(self):
        parsed = self.parse(evidence_output(self.method, skip=True))
        self.assertEqual(parsed["status"], "skipped")

    def test_nonzero_exit_is_rejected(self):
        parsed = self.parse(evidence_output(self.method), exit_code=1)
        self.assertEqual(parsed["status"], "failed")

    def test_failure_token_is_rejected(self):
        broken = evidence_output(self.method, extra_lines=["FAILED (failures=1)"])
        self.assertEqual(self.parse(broken)["status"], "failed")

    def test_arbitrary_ok_in_a_log_line_is_not_a_summary(self):
        broken = evidence_output(
            self.method, terminal="done", extra_lines=["OK", "log: OK"]
        )
        self.assertEqual(self.parse(broken)["status"], "unknown")

    def test_ok_suffix_is_not_an_exact_success(self):
        parsed = self.parse(evidence_output(self.method, terminal="OK (skipped=0)"))
        self.assertEqual(parsed["status"], "skipped")

    def test_missing_residue_evidence_is_rejected(self):
        parsed = self.parse(evidence_output(self.method, residue=False))
        self.assertEqual(parsed["status"], "no-cleanup-evidence")

    def test_residue_nonce_mismatch_is_rejected(self):
        parsed = self.parse(evidence_output(self.method, residue_nonce="f" * 32))
        self.assertEqual(parsed["status"], "no-cleanup-evidence")

    def test_residue_marker_must_belong_to_the_case_class(self):
        parsed = self.parse(
            evidence_output(self.method, residue_id=case_id("test_other"))
        )
        self.assertEqual(parsed["status"], "no-cleanup-evidence")


class AggregationTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        self.cases = executable_cases(self.manifest)

    def _accept(self, results, run, confirmation=None):
        return aggregate(
            self.manifest, results, confirmation, run
        )

    def test_evidence_backed_matrix_with_confirmed_cleanup_is_accepted(self):
        results = accepted_results(self.cases)
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        report = self._accept(results, run, confirmation)
        self.assertTrue(report["executable_subset_accepted"], report["problems"])
        self.assertTrue(report["cleanup_confirmed"])
        self.assertEqual(
            report["cleanup_digest"],
            runner.residue_digest(
                runner.run_residue(run)[0], runner.run_residue(run)[1]
            ),
        )
        # Whole-matrix acceptance stays false while required publication cases are blocked.
        self.assertFalse(report["accepted"])
        self.assertFalse(report["publication_complete"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        self.assertEqual(report["native_status"], "ACCEPTED_EXECUTABLE_SUBSET")
        self.assertEqual(
            report["coverage"]["publication_required_blocked"],
            len(blocked_cases(self.manifest)),
        )
        self.assertEqual(
            report["coverage"]["blocked_by_module"],
            {"clinic_photos.tests.test_publication": 16},
        )
        self.assertTrue(report["incomplete_reasons"])

    def test_missing_cleanup_confirmation_blocks_subset_acceptance(self):
        results = accepted_results(self.cases)
        run = run_evidence(results)
        report = self._accept(results, run, None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("cleanup disposition", " ".join(report["problems"]))
        self.assertTrue(report["cleanup_required"])
        self.assertEqual(report["native_status"], "UNACCEPTED")

    def test_cleanup_confirmation_must_match_the_residue(self):
        results = accepted_results(self.cases)
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        confirmation["residue_rows"] = [["File", "FILE-OTHER"]]
        report = self._accept(results, run, confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("do not match", " ".join(report["problems"]))

    def test_cleanup_confirmation_needs_operator_and_method(self):
        results = accepted_results(self.cases)
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        confirmation["disposal"] = {"observed": True}
        report = self._accept(results, run, confirmation)
        self.assertFalse(report["executable_subset_accepted"])

    def test_supplied_status_passed_without_evidence_is_rejected(self):
        results = [
            {"case": case["id"], "status": "passed"} for case in self.cases
        ]
        report = self._accept(results, None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertTrue(any("no execution evidence" in p for p in report["problems"]))

    def test_supplied_status_passed_with_skip_output_is_contradictory(self):
        results = accepted_results(self.cases)
        results[0] = {
            "case": results[0]["case"],
            "nonce": results[0]["nonce"],
            "exit_code": 0,
            "output": evidence_output(
                self.cases[0]["method"],
                nonce=results[0]["nonce"],
                identity=runner.expected_case_id(self.manifest, self.cases[0]),
                skip=True,
            ),
            "status": "passed",
        }
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        joined = " ".join(report["problems"])
        self.assertIn("contradicts evidence", joined)

    def test_supplied_status_passed_with_failure_output_is_contradictory(self):
        results = accepted_results(self.cases)
        results[0] = {
            "case": results[0]["case"],
            "nonce": results[0]["nonce"],
            "exit_code": 1,
            "output": evidence_output(
                self.cases[0]["method"],
                nonce=results[0]["nonce"],
                identity=runner.expected_case_id(self.manifest, self.cases[0]),
            ),
            "status": "passed",
        }
        run = run_evidence(results)
        report = self._accept(results, run, None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("contradicts evidence", " ".join(report["problems"]))

    def test_missing_case_fails_acceptance(self):
        results = accepted_results(self.cases[1:])
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("missing result", " ".join(report["problems"]))

    def test_duplicate_result_fails_acceptance(self):
        results = accepted_results(self.cases)
        results.append(dict(results[0]))
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("duplicate result", " ".join(report["problems"]))

    def test_unexpected_case_result_fails_acceptance(self):
        results = accepted_results(self.cases)
        results.append({"case": "unexpected.case", "status": "passed"})
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("unexpected case", " ".join(report["problems"]))

    def test_wrong_executed_method_fails_acceptance(self):
        results = accepted_results(self.cases)
        results[2] = {
            "case": results[2]["case"],
            "nonce": results[2]["nonce"],
            "exit_code": 0,
            "output": evidence_output(
                self.cases[2]["method"],
                nonce=results[2]["nonce"],
                identity=runner.expected_case_id(self.manifest, self.cases[3]),
            ),
        }
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("wrong-identity", " ".join(report["problems"]))

    def test_blocked_cases_are_not_required_and_never_passed(self):
        results = accepted_results(self.cases)
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertTrue(report["executable_subset_accepted"], report["problems"])
        blocked = blocked_cases(self.manifest)
        self.assertTrue(blocked)
        self.assertEqual(
            {row["case"] for row in report["blocked_cases"]},
            {case["id"] for case in blocked},
        )
        for row in report["blocked_cases"]:
            self.assertEqual(row["native_status"], "UNRUN")
            self.assertTrue(row["reason"])

    def test_blocked_case_result_is_rejected_even_when_it_looks_green(self):
        blocked = blocked_cases(self.manifest)[0]
        results = accepted_results(self.cases)
        results.append(
            {
                "case": blocked["id"],
                "nonce": nonce_for(blocked),
                "exit_code": 0,
                "output": evidence_output(
                    blocked["method"],
                    nonce=nonce_for(blocked),
                    identity=runner.expected_case_id(self.manifest, blocked),
                ),
            }
        )
        run = run_evidence(results)
        report = self._accept(results, run, cleanup_for(results, run))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("blocked case", " ".join(report["problems"]))

    def test_empty_residue_does_not_prove_filesystem_cleanup(self):
        # Empty rows/keys are not evidence of absence: acceptance still needs an
        # observed disposal. Nothing is inferred from an empty list.
        results = [
            {
                "case": case["id"],
                "nonce": nonce_for(case),
                "exit_code": 0,
                "output": evidence_output(
                    case["method"],
                    nonce=nonce_for(case),
                    identity=runner.expected_case_id(self.manifest, case),
                    residue_rows=(),
                    storage_keys=(),
                ),
            }
            for case in self.cases
        ]
        run = run_evidence(results)
        report = self._accept(results, run, None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("cleanup disposition", " ".join(report["problems"]))
        self.assertEqual(report["cleanup_digest"], runner.residue_digest([], []))
        confirmed = self._accept(results, run, cleanup_for(results, run))
        self.assertTrue(confirmed["executable_subset_accepted"], confirmed["problems"])


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        self.cases = executable_cases(self.manifest)

    def test_dry_run_lists_one_command_and_expected_identity_per_case(self):
        report = runner.build_dry_run(self.manifest, "site.test")
        self.assertFalse(report["executed"])
        self.assertEqual(report["evidence_schema"], runner.EVIDENCE_SCHEMA)
        self.assertEqual(len(report["cases"]), len(self.manifest["cases"]))
        rows = {row["case"]: row for row in report["cases"]}
        for case in self.manifest["cases"]:
            row = rows[case["id"]]
            self.assertEqual(
                row["expected_id"], runner.expected_case_id(self.manifest, case)
            )
            self.assertEqual(row["nonce_env"], runner.CASE_NONCE_ENV)
            self.assertEqual(row["native_status"], "UNRUN")
        for case in self.cases:
            self.assertIsNotNone(rows[case["id"]]["command"])
        for case in blocked_cases(self.manifest):
            self.assertIsNone(rows[case["id"]]["command"])
            self.assertTrue(rows[case["id"]]["blocked_reason"])

    def test_commands_select_exactly_one_class_and_bare_method(self):
        for case in self.cases:
            command = runner.build_command(self.manifest, case, "site.test")
            with self.subTest(case=case["id"]):
                self.assertEqual(command[0], "bench")
                self.assertIn("run-tests", command)
                # Native discovery compares ``--test`` against ``test._testMethodName``,
                # i.e. a bare method name; the class is a separate ``--case``
                # selection. ``Class.method`` selects nothing and still exits 0.
                self.assertEqual(command.count("--case"), 1)
                self.assertEqual(command.count("--test"), 1)
                expected_class = runner.case_class(self.manifest, case)
                self.assertEqual(
                    command[command.index("--case") + 1], expected_class
                )
                method = command[command.index("--test") + 1]
                self.assertEqual(method, case["method"])
                self.assertNotIn(".", method)
                self.assertNotIn(expected_class, method)
                self.assertEqual(
                    command[command.index("--module") + 1],
                    runner.case_module(self.manifest, case),
                )
                self.assertEqual(command[command.index("--app") + 1], "clinic_photos")
                self.assertEqual(command[command.index("--site") + 1], "site.test")

    def test_commands_use_no_option_outside_the_declared_contract(self):
        allowed = {"--site", "--app", "--module", "--case", "--test"}
        for case in self.cases:
            command = runner.build_command(self.manifest, case, "site.test")
            self.assertLessEqual({p for p in command if p.startswith("--")}, allowed)
            self.assertEqual(command[3], "run-tests")

    def test_contract_is_declared_unverified(self):
        template = runner.CLI_CONTRACT["template"]
        self.assertEqual(runner.CLI_CONTRACT["status"], "unverified-candidate")
        self.assertIn("--case", template)
        self.assertIn("--test", template)
        self.assertIn("{case_class}", template)
        self.assertEqual(template[template.index("--case") + 1], "{case_class}")
        self.assertEqual(template[template.index("--test") + 1], "{method}")
        requirement = runner.CLI_CONTRACT["verification_requirement"]
        self.assertIn("--case", requirement)
        self.assertIn("confirm", requirement)
        self.assertIn("not assumed", requirement)
        self.assertIn("marker lines", requirement)


class RunnerCliTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        self.by_method = {case["method"]: case for case in self.manifest["cases"]}

    def _run(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runner.main(argv)
        return code, buffer.getvalue()

    def _temp_json(self, payload):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump(payload, handle)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        return handle.name

    def _verification_record(self, **overrides):
        record = {
            "contract_digest": runner.contract_digest(),
            "supports": ["--app", "--module", "--case", "--test"],
            "exit_code": 0,
            "evidence": "recorded `bench run-tests --help` output (native gate)",
        }
        record.update(overrides)
        return self._temp_json(record)

    def test_default_mode_is_a_dry_run(self):
        code, output = self._run([])
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertTrue(report["dry_run"])
        self.assertFalse(report["executed"])

    def test_execute_refuses_without_a_verified_cli_contract(self):
        def explode(*args, **kwargs):
            raise AssertionError("the runner executed an unverified contract")

        original = runner.execute_case
        runner.execute_case = explode
        try:
            code, output = self._run(["--execute", "--site", "site.test"])
        finally:
            runner.execute_case = original
        self.assertEqual(code, 2)
        self.assertIn("unverified", json.loads(output)["error"])

    def test_mismatched_verification_digest_fails_closed(self):
        path = self._verification_record(contract_digest="0" * 64)
        code, _ = self._run(["--execute", "--site", "s", "--cli-verified", path])
        self.assertEqual(code, 2)

    def test_pre_repair_verification_record_is_rejected(self):
        # Digest recorded by the native gate for the candidate template that used
        # ``--test Class.method``; the repaired contract must not reuse it.
        path = self._verification_record(
            contract_digest=(
                "9306497b92fb85e9811ec45a440105a224d92e83b22210a1164dceb65e07de25"
            )
        )
        code, output = self._run(["--execute", "--site", "s", "--cli-verified", path])
        self.assertEqual(code, 2)
        self.assertIn("does not match", json.loads(output)["error"])

    def test_verification_must_confirm_the_case_selector(self):
        path = self._verification_record(supports=["--app", "--module", "--test"])
        code, output = self._run(["--execute", "--site", "s", "--cli-verified", path])
        self.assertEqual(code, 2)
        self.assertIn("--case", json.loads(output)["error"])

    def _fake_execute_for(self, manifest_object, *, skip_first=False):
        state = {"first": skip_first}

        def fake_execute(command, nonce, timeout=1800):
            method = command[command.index("--test") + 1]
            case_class = command[command.index("--case") + 1]
            module = command[command.index("--module") + 1]
            skip = state["first"]
            state["first"] = False
            return {
                "exit_code": 0,
                "output": evidence_output(
                    method,
                    nonce=nonce,
                    identity=f"{module}.{case_class}.{method}",
                    skip=skip,
                ),
            }

        return fake_execute

    def test_verified_execution_uses_a_distinct_nonce_per_case_and_requires_cleanup(self):
        path = self._verification_record()
        nonces = []
        cases = executable_cases(self.manifest)
        fake = self._fake_execute_for(self.manifest)
        report_path = str(pathlib.Path(self._temp_json({})).with_suffix(".report.json"))
        root_args = ["--source-root", str(APP_ROOT)]
        self.addCleanup(pathlib.Path(report_path).unlink, True)

        def fake_execute(command, nonce, timeout=1800):
            nonces.append(nonce)
            return fake(command, nonce)

        original = runner.execute_case
        runner.execute_case = fake_execute
        try:
            code, output = self._run(
                [
                    "--execute",
                    "--site",
                    "site.test",
                    "--cli-verified",
                    path,
                    "--json-out",
                    report_path,
                    *root_args,
                ]
            )
        finally:
            runner.execute_case = original
        self.assertEqual(code, 1)
        report = json.loads(output)
        self.assertFalse(report["accepted"])
        self.assertFalse(report["executable_subset_accepted"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        self.assertTrue(report["cleanup_required"])
        self.assertEqual(len(nonces), len(cases))
        self.assertEqual(len(set(nonces)), len(nonces))
        self.assertTrue(report["run"]["run_id"])
        self.assertEqual(report["run"]["site"], "site.test")
        self.assertTrue(report["run"]["source_digest"])
        self.assertTrue(
            all("no cleanup disposition" in problem for problem in report["problems"]),
            report["problems"],
        )

        confirmation = self._temp_json(cleanup_for(report["results"], report["run"]))
        code, output = self._run(
            [
                "--aggregate",
                report_path,
                "--site",
                "site.test",
                "--cleanup-confirmed",
                confirmation,
                *root_args,
            ]
        )
        # Subset accepted, whole matrix still incomplete: exit 1, honest field set.
        self.assertEqual(code, 1, output)
        final = json.loads(output)
        self.assertTrue(final["executable_subset_accepted"], final["problems"])
        self.assertFalse(final["accepted"])
        self.assertEqual(final["native_status"], "ACCEPTED_EXECUTABLE_SUBSET")

    def test_verified_execution_with_one_skip_fails_acceptance(self):
        path = self._verification_record()
        original = runner.execute_case
        runner.execute_case = self._fake_execute_for(self.manifest, skip_first=True)
        try:
            code, output = self._run(
                [
                    "--execute",
                    "--site",
                    "site.test",
                    "--cli-verified",
                    path,
                    "--source-root",
                    str(APP_ROOT),
                ]
            )
        finally:
            runner.execute_case = original
        self.assertEqual(code, 1)
        self.assertIn("skipped", " ".join(json.loads(output)["problems"]))

    def test_aggregate_from_a_results_file(self):
        results = accepted_results(executable_cases(self.manifest))
        run = run_evidence(results)
        results_path = self._temp_json({"run": run, "results": results})
        confirmation = self._temp_json(cleanup_for(results, run))
        code, output = self._run(
            [
                "--aggregate",
                results_path,
                "--site",
                "site.test",
                "--cleanup-confirmed",
                confirmation,
                "--source-root",
                str(APP_ROOT),
            ]
        )
        self.assertEqual(code, 1, output)
        report = json.loads(output)
        self.assertTrue(report["executable_subset_accepted"], report["problems"])
        self.assertFalse(report["accepted"])

    def test_aggregate_from_a_results_file_without_run_binding_rejects_cleanup(self):
        results = accepted_results(executable_cases(self.manifest))
        results_path = self._temp_json({"results": results})
        confirmation = self._temp_json(cleanup_for(results, run_evidence(results)))
        code, output = self._run(
            [
                "--aggregate",
                results_path,
                "--site",
                "site.test",
                "--cleanup-confirmed",
                confirmation,
                "--source-root",
                str(APP_ROOT),
            ]
        )
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["executable_subset_accepted"])

    def test_aggregate_requires_a_trusted_source_root(self):
        results = accepted_results(executable_cases(self.manifest))
        run = run_evidence(results)
        results_path = self._temp_json({"run": run, "results": results})
        confirmation = self._temp_json(cleanup_for(results, run))
        code, output = self._run(
            ["--aggregate", results_path, "--cleanup-confirmed", confirmation]
        )
        self.assertEqual(code, 1)
        report = json.loads(output)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("trusted source root", " ".join(report["problems"]))

    def test_aggregate_rejects_status_only_results(self):
        results_path = self._temp_json(
            {
                "results": [
                    {"case": case["id"], "status": "passed"}
                    for case in self.manifest["cases"]
                ]
            }
        )
        code, output = self._run(["--aggregate", results_path])
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output)["accepted"])

    def test_unreadable_manifest_fails_closed(self):
        code, output = self._run(["--manifest", "/nonexistent/manifest.json"])
        self.assertEqual(code, 2)
        self.assertIn("cannot load manifest", json.loads(output)["error"])


class MarkerChannelTests(unittest.TestCase):
    PAYLOAD = {"id": "module.Class.test_case", "nonce": NONCE}

    def test_marker_reaches_the_original_stream_while_stdout_is_captured(self):
        stream = FakeStream()
        with marker_streams(stream) as captured:
            runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
            self.assertEqual(captured.getvalue(), "")
        self.assertEqual(len(stream.written), 1)
        self.assertEqual(stream.flushed, 1)
        text = stream.written[0]
        self.assertTrue(text.startswith("\n"), "leading newline frames the marker")
        self.assertTrue(text.endswith("\n"))
        marker, _, rendered = text.strip().partition(" ")
        self.assertEqual(marker, runner.BEGIN_MARKER)
        self.assertEqual(json.loads(rendered), self.PAYLOAD)

    def test_absent_original_stream_fails_closed_without_touching_capture(self):
        with marker_streams(None) as captured:
            with self.assertRaises(RuntimeError) as caught:
                runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
            self.assertEqual(captured.getvalue(), "")
        self.assertIn("stdout", str(caught.exception))

    def test_closed_original_stream_fails_closed(self):
        stream = FakeStream()
        stream.closed = True
        with marker_streams(stream):
            with self.assertRaises(RuntimeError):
                runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
        self.assertEqual(stream.written, [])
        self.assertEqual(stream.flushed, 0)

    def test_unwritable_original_stream_fails_closed(self):
        stream = FakeStream(writable=False)
        with marker_streams(stream):
            with self.assertRaises(RuntimeError):
                runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
        self.assertEqual(stream.written, [])

    def test_write_failure_is_not_swallowed(self):
        stream = FakeStream(write_error=OSError("broken pipe"))
        with marker_streams(stream):
            with self.assertRaises(RuntimeError) as caught:
                runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
        self.assertIn("broken pipe", str(caught.exception))
        self.assertEqual(stream.flushed, 0)

    def test_flush_failure_is_not_swallowed(self):
        stream = FakeStream(flush_error=ValueError("closed by teardown"))
        with marker_streams(stream):
            with self.assertRaises(RuntimeError) as caught:
                runner.emit_marker(runner.BEGIN_MARKER, self.PAYLOAD)
        self.assertIn("closed by teardown", str(caught.exception))

    def test_buffered_stdout_capture_swallows_print_but_not_the_marker(self):
        # End-to-end channel proof in a real child process: a print during a
        # replaced sys.stdout lands in the capture and is discarded, while the
        # marker still reaches the captured process output.
        script = (
            "import io, sys\n"
            "capture = io.StringIO()\n"
            "sys.stdout = capture\n"
            "print('CLINIC_PHOTOS_CASE_BEGIN swallowed-style', flush=True)\n"
            "from clinic_photos.tests import run_isolated_cases as runner\n"
            "runner.emit_marker(runner.END_MARKER, "
            "{'id': 'module.Class.test_x', 'nonce': 'n'})\n"
            "assert 'swallowed-style' in capture.getvalue()\n"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertNotIn("swallowed-style", completed.stdout)
        self.assertIn(runner.END_MARKER, completed.stdout)


class ProducerMarkerTests(unittest.TestCase):
    """The native suite's marker producer must use the same original-stream channel."""

    def test_producer_emit_uses_the_original_stream(self):
        with site_suite_module() as (suite, _):
            stream = FakeStream()
            with marker_streams(stream) as captured:
                suite._emit(
                    runner.RESIDUE_MARKER,
                    {"id": CLASS_ID, "nonce": NONCE, "rows": [], "storage_keys": []},
                )
            self.assertEqual(captured.getvalue(), "")
        markers = marker_lines(stream)
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0][0], runner.RESIDUE_MARKER)
        self.assertEqual(markers[0][1]["nonce"], NONCE)

    def test_case_decorator_emits_begin_and_passing_end(self):
        identity = case_id("test_decorated_case")
        executed = []
        with site_suite_module() as (suite, _):
            stream = FakeStream()

            @suite._case
            def case(self):
                executed.append(self.id())

            with marker_streams(stream), case_nonce(NONCE):
                case(FakeCase(identity))

        markers = marker_lines(stream)
        self.assertEqual([marker for marker, _ in markers], [
            runner.BEGIN_MARKER,
            runner.END_MARKER,
        ])
        self.assertEqual(markers[0][1], {"id": identity, "nonce": NONCE})
        self.assertEqual(markers[1][1]["status"], "pass")
        self.assertEqual(markers[1][1]["id"], identity)
        self.assertEqual(executed, [identity])

    def test_case_decorator_keeps_the_failure_path_and_reports_fail(self):
        identity = case_id("test_decorated_failure")
        with site_suite_module() as (suite, _):
            stream = FakeStream()

            @suite._case
            def case(self):
                raise ValueError("case failed")

            with marker_streams(stream), case_nonce(NONCE):
                with self.assertRaises(ValueError):
                    case(FakeCase(identity))

        markers = marker_lines(stream)
        self.assertEqual(markers[-1][0], runner.END_MARKER)
        self.assertEqual(markers[-1][1]["status"], "fail")

    def test_case_decorator_refuses_to_emit_without_runner_nonce(self):
        fake = FakeCase(case_id("test_decorated_nonce"))
        with site_suite_module() as (suite, _):
            stream = FakeStream()

            @suite._case
            def case(self):
                raise AssertionError("case body must not run without a nonce")

            with marker_streams(stream), case_nonce(None):
                with self.assertRaises(AssertionError):
                    case(fake)
        self.assertEqual(len(fake.failures), 1)
        self.assertIn(runner.CASE_NONCE_ENV, fake.failures[0])
        self.assertEqual(stream.written, [])

    def test_teardown_class_reports_residue_on_the_original_stream(self):
        with site_suite_module() as (suite, base):
            case_class = getattr(suite, CLASS_NAME)
            case_class.created = [("File", "FILE-1")]
            case_class.storage_keys = ["/private/files/clinic-photo-fixture.png"]
            stream = FakeStream()
            with marker_streams(stream), case_nonce(NONCE):
                case_class.tearDownClass()

        markers = marker_lines(stream)
        self.assertEqual(len(markers), 1)
        marker, payload = markers[0]
        self.assertEqual(marker, runner.RESIDUE_MARKER)
        self.assertEqual(payload["id"], f"{MODULE}.{CLASS_NAME}")
        self.assertEqual(payload["nonce"], NONCE)
        self.assertEqual(payload["rows"], [["File", "FILE-1"]])
        self.assertEqual(
            payload["storage_keys"], ["/private/files/clinic-photo-fixture.png"]
        )
        self.assertEqual(base.super_teardowns.count(CLASS_NAME), 1)

    def test_teardown_class_still_cleans_up_when_residue_channel_fails(self):
        with site_suite_module() as (suite, base):
            case_class = getattr(suite, CLASS_NAME)
            case_class.created = []
            case_class.storage_keys = []
            with marker_streams(None), case_nonce(NONCE):
                with self.assertRaises(RuntimeError):
                    case_class.tearDownClass()
        self.assertEqual(base.super_teardowns.count(CLASS_NAME), 1)


class ExecuteCaseCaptureTests(unittest.TestCase):
    def test_interleaved_streams_keep_their_raw_order(self):
        script = (
            "import sys\n"
            "sys.stdout.write('out-1\\n'); sys.stdout.flush()\n"
            "sys.stderr.write('err-1\\n'); sys.stderr.flush()\n"
            "sys.stdout.write('out-2\\n'); sys.stdout.flush()\n"
        )
        outcome = runner.execute_case([sys.executable, "-c", script], NONCE)
        self.assertEqual(outcome["exit_code"], 0)
        self.assertEqual(
            outcome["output"].splitlines(), ["out-1", "err-1", "out-2"]
        )


class FailedResidueEvidenceTests(unittest.TestCase):
    """A failing case must still surrender the residue it created."""

    METHOD = "test_reserved_test_keys_stay_in_the_orphan_namespace"

    def parse(self, output, exit_code=1):
        return runner.parse_case_output(
            exit_code, output, expected_id=case_id(self.METHOD), nonce=NONCE
        )

    def test_failed_end_preserves_residue_without_passing(self):
        # RED (Task 10): a failed END marker previously discarded the residue marker,
        # so a failing execution that created protected rows/bytes lost its cleanup
        # obligation. The verdict must stay failed AND the residue must survive.
        parsed = self.parse(evidence_output(self.METHOD, end_status="fail"))
        self.assertEqual(parsed["status"], "failed")
        self.assertEqual(parsed["residue"], [["File", "FILE-1"]])
        self.assertEqual(
            parsed["storage_keys"], ["/private/files/clinic-photo-fixture.png"]
        )

    def test_failed_residue_is_required_for_disposal(self):
        results = [
            {
                "case": "setup.reserved_namespace",
                "nonce": NONCE,
                "exit_code": 1,
                "output": evidence_output(self.METHOD, end_status="fail"),
            }
        ]
        report = aggregate(manifest(), results, None, None)
        self.assertFalse(report["accepted"])
        self.assertEqual(report["cleanup_required"], [["File", "FILE-1"]])
        joined = " ".join(report["problems"])
        self.assertIn("failed", joined)
        self.assertIn("cleanup disposition", joined)

    def test_residue_row_must_carry_a_resource_identity(self):
        for rows in ((["File"],), (["File", ""],), (["", "FILE-1"],), ("File",)):
            with self.subTest(rows=rows):
                parsed = self.parse(
                    evidence_output(self.METHOD, residue_rows=rows, end_status="fail")
                )
                self.assertEqual(parsed["status"], "no-cleanup-evidence")
                self.assertIn("identity", parsed["detail"])

    def test_residue_storage_key_must_be_a_canonical_private_key(self):
        for keys in (("",), ("/public/files/x.png",), ("private/files/x.png",), (7,)):
            with self.subTest(keys=keys):
                parsed = self.parse(
                    evidence_output(
                        self.METHOD, storage_keys=keys, end_status="pass"
                    )
                )
                self.assertEqual(parsed["status"], "no-cleanup-evidence")

    def test_duplicate_residue_rows_are_not_double_counted(self):
        parsed = self.parse(
            evidence_output(
                self.METHOD,
                residue_rows=(("File", "FILE-1"), ("File", "FILE-1")),
                end_status="fail",
            )
        )
        self.assertEqual(parsed["residue"], [["File", "FILE-1"]])


class CleanupBindingTests(unittest.TestCase):
    """Acceptance needs a versioned, resource-complete disposal record."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(
            executable_cases(self.manifest),
            keys=("/private/files/clinic-photo-fixture.png",),
        )
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _aggregate(self, confirmation, run=None):
        return aggregate(
            self.manifest, self.results, confirmation, run or self.run
        )

    def test_complete_record_is_accepted(self):
        report = self._aggregate(self.confirmation)
        self.assertTrue(report["executable_subset_accepted"], report["problems"])
        self.assertFalse(report["accepted"])

    def test_every_required_binding_is_enforced(self):
        mutations = {
            "schema": lambda c: c.pop("schema"),
            "run_id": lambda c: c.update(run_id="0" * 32),
            "site": lambda c: c.update(site="other.test"),
            "project": lambda c: c.update(project="other-project"),
            "source_digest": lambda c: c.update(source_digest="0" * 64),
            "contract_digest": lambda c: c.update(contract_digest="0" * 64),
            "case_nonces": lambda c: c["case_nonces"].pop(sorted(c["case_nonces"])[0]),
            "raw_output_sha256": lambda c: c["raw_output_sha256"].update(
                {sorted(c["raw_output_sha256"])[0]: "0" * 64}
            ),
            "residue_rows": lambda c: c.update(residue_rows=[]),
            "storage_keys": lambda c: c.update(storage_keys=[]),
            "disposal": lambda c: c.pop("disposal"),
        }
        for name, mutate in mutations.items():
            with self.subTest(binding=name):
                confirmation = json.loads(json.dumps(self.confirmation))
                mutate(confirmation)
                report = self._aggregate(confirmation)
                self.assertFalse(report["accepted"], name)
                self.assertIn(
                    "cleanup confirmation", " ".join(report["problems"]), name
                )

    def test_disposal_must_be_observed_and_complete(self):
        cases = {
            "not-observed": lambda d: d.update(observed=False),
            "no-operator": lambda d: d.pop("disposed_by"),
            "no-method": lambda d: d.pop("method"),
            "no-timestamp": lambda d: d.pop("observed_at"),
            "missing-resource": lambda d: d.update(verified_absent=[]),
        }
        for name, mutate in cases.items():
            with self.subTest(disposal=name):
                confirmation = json.loads(json.dumps(self.confirmation))
                mutate(confirmation["disposal"])
                self.assertFalse(self._aggregate(confirmation)["accepted"], name)

    def test_storage_keys_are_part_of_the_residue_obligation(self):
        results = accepted_results(
            executable_cases(self.manifest)[:1],
            keys=(
                "/private/files/clinic-photo-fixture.png",
                "/private/files/clinic-annotation-fixture.json",
            ),
        )
        run = run_evidence(results)
        rows, keys = runner.run_residue(run)
        self.assertEqual(
            keys,
            (
                "/private/files/clinic-annotation-fixture.json",
                "/private/files/clinic-photo-fixture.png",
            ),
        )
        report = aggregate(self.manifest, results, None, run)
        self.assertIn(
            "/private/files/clinic-annotation-fixture.json",
            json.dumps(report["cleanup_required_storage_keys"]),
        )

    def test_tampered_raw_output_breaks_the_binding(self):
        results = accepted_results(executable_cases(self.manifest))
        run = run_evidence(results)
        results[0] = dict(
            results[0],
            output=results[0]["output"] + "\nforged trailing line\n",
        )
        report = aggregate(self.manifest, results, cleanup_for(results, run), run)
        self.assertFalse(report["accepted"])
        self.assertIn("raw output", " ".join(report["problems"]).lower())


class PublicationCaseSourceTests(unittest.TestCase):
    """The publication cases must be real, selectable and honest about readiness."""

    def setUp(self):
        self.manifest = manifest()
        self.publication = [
            case
            for case in self.manifest["cases"]
            if runner.case_module(self.manifest, case) == PUBLICATION_MODULE
        ]
        self.source = PUBLICATION_SUITE.read_text(encoding="utf-8")

    def test_every_publication_case_is_a_real_test_method(self):
        self.assertTrue(self.publication)
        for case in self.publication:
            class_name = case["case_class"]
            with self.subTest(case=case["id"]):
                self.assertIn(case["method"], _methods(self.source, class_name))
                self.assertEqual(
                    runner.expected_case_id(self.manifest, case),
                    f"{PUBLICATION_MODULE}.{class_name}.{case['method']}",
                )

    def test_blocked_cases_are_skipped_and_cannot_pass(self):
        blocked = [
            case for case in self.publication if case["readiness"] == "blocked"
        ]
        self.assertTrue(blocked)
        for case in blocked:
            with self.subTest(case=case["id"]):
                self.assertTrue(case["blocked_reason"])
                decorators = _case_decorators(case, self.manifest)
                self.assertIn("skip", decorators)
                self.assertIn("self.fail(", _case_method_source(case, self.manifest))

    def test_blocked_skip_text_is_the_manifest_reason_verbatim(self):
        # One authority for one fact: the manifest's canonical blocked_reason and the
        # decorator text cannot drift.
        manifest_object = manifest()
        for case in manifest_object["cases"]:
            if case.get("readiness") != "blocked":
                continue
            with self.subTest(case=case["id"]):
                source, class_name = case_source(case, manifest_object)
                node = _class_node(source, class_name)
                message = None
                for child in node.body:
                    if not isinstance(child, ast.FunctionDef) or child.name != case["method"]:
                        continue
                    for decorator in child.decorator_list:
                        if isinstance(decorator, ast.Call) and getattr(
                            decorator.func, "attr", ""
                        ) == "skip":
                            message = ast.literal_eval(decorator.args[0])
                self.assertIsNotNone(message, case["method"])
                self.assertEqual(message, case["blocked_reason"])

    def test_blocked_reasons_are_canonical(self):
        allowed = {
            "task6-schema-sync",
            "synthetic-clinician-patient",
            "writer-transaction",
            "prepared-reservation",
            "published-object",
            "two-phase-fixtures",
            "native-user-permission",
            "fault-seam",
            "operator-allowlist",
        }
        for case in blocked_cases(self.manifest):
            with self.subTest(case=case["id"]):
                reason = case["blocked_reason"]
                self.assertRegex(reason, r"^\[[a-z0-9-]+\] .+$")
                code = reason[1 : reason.index("]")]
                self.assertIn(code, allowed)

    def test_executable_cases_are_not_pre_skipped(self):
        executable = [
            case for case in self.publication if case["readiness"] == "executable"
        ]
        self.assertTrue(executable)
        for case in executable:
            with self.subTest(case=case["id"]):
                self.assertNotIn("skip", _case_decorators(case, self.manifest))
                body = _case_method_source(case, self.manifest)
                self.assertNotIn("self.fail(", body)

    def test_executable_cases_emit_the_proven_marker_protocol(self):
        executable = [
            case for case in self.publication if case["readiness"] == "executable"
        ]
        for case in executable:
            with self.subTest(case=case["id"]):
                self.assertIn("_case", _case_decorators(case, self.manifest))
        self.assertIn("emit_marker", self.source)
        self.assertIn("CASE_NONCE_ENV", self.source)
        # Residue is reported by the class teardown of the same execution.
        self.assertIn("RESIDUE_MARKER", self.source)
        self.assertIn("storage_keys", self.source)

    def test_publication_entry_is_the_fixed_child_module(self):
        self.assertIn("clinic_photos.publication.process", self.source)
        self.assertIn('"--operation-id"', self.source)
        self.assertIn('"--site"', self.source)
        self.assertIn('"/home/frappe/frappe-bench/sites"', self.source)
        # A positive publication result may never be produced by calling the
        # coordinator or the queue in-process.
        for forbidden in ("publish_submission(", "enqueue_submission(", "hold_exclusion("):
            self.assertNotIn(forbidden, self.source)

    def test_publication_module_is_importable_without_a_site(self):
        # Inert import: the native suite must not require Frappe at import time, or
        # the runner's static/manifest checks and dry run cannot see it.
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", f"import {PUBLICATION_MODULE}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            cwd=str(APP_ROOT),
            env=environment,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_changed_paths_compile(self):
        for path in (RUNNER_PATH, MANIFEST_PATH, PUBLICATION_SUITE):
            with self.subTest(path=path.name):
                if path.suffix == ".py":
                    compile(path.read_text(encoding="utf-8"), str(path), "exec")
                else:
                    json.loads(path.read_text(encoding="utf-8"))


class SourceIdentityTests(unittest.TestCase):
    """The consumer recomputes source identity; agreeing strings are not proof."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _report(self, run=None, confirmation=None, *, source_root=APP_ROOT, manifest_path=None):
        return runner.aggregate(
            self.manifest,
            self.results,
            confirmation if confirmation is not None else self.confirmation,
            run=run if run is not None else self.run,
            source_root=str(source_root) if source_root else None,
            manifest_path=str(manifest_path or MANIFEST_PATH),
            expected_site="site.test",
        )

    def test_trusted_source_root_is_required(self):
        report = self._report(source_root=None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("trusted source root", " ".join(report["problems"]))

    def test_source_root_must_exist_and_look_like_the_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = pathlib.Path(tmp) / "empty"
            empty.mkdir()
            for root in (empty, pathlib.Path(tmp) / "missing"):
                with self.subTest(root=str(root)):
                    report = self._report(source_root=root)
                    self.assertFalse(report["executable_subset_accepted"])
                    self.assertIn("source root", " ".join(report["problems"]))

    def test_forged_source_digest_string_is_rejected(self):
        forged = "fabricated-not-a-digest"
        report = self._report(
            run=dict(self.run, source_digest=forged),
            confirmation=dict(self.confirmation, source_digest=forged),
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("source identity", " ".join(report["problems"]))

    def test_digest_from_a_different_real_tree_is_rejected(self):
        other = disposable_tree(
            lambda root: (root / "clinic_photos" / "hooks.py").write_text(
                "# altered\n", encoding="utf-8"
            )
        )
        self.addCleanup(__import__("shutil").rmtree, other.parent, True)
        other_digest = runner.source_digest(other, MANIFEST_PATH)
        self.assertNotEqual(other_digest, self.run["source_digest"])
        report = self._report(
            run=dict(self.run, source_digest=other_digest, source_root=str(APP_ROOT)),
            confirmation=dict(self.confirmation, source_digest=other_digest),
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("source identity", " ".join(report["problems"]))

    def test_run_source_root_must_be_the_trusted_root(self):
        other = disposable_tree()
        self.addCleanup(__import__("shutil").rmtree, other.parent, True)
        report = self._report(run=dict(self.run, source_root=str(other)))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("different source root", " ".join(report["problems"]))

    def test_confirmation_source_root_must_match(self):
        other = disposable_tree()
        self.addCleanup(__import__("shutil").rmtree, other.parent, True)
        report = self._report(
            confirmation=dict(self.confirmation, source_root=str(other))
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("source root", " ".join(report["problems"]))

    def test_doctype_json_and_manifest_are_in_the_source_identity(self):
        root = disposable_tree()
        self.addCleanup(__import__("shutil").rmtree, root.parent, True)
        case_manifest = root / "clinic_photos" / "tests" / "case_manifest.json"
        doctype = (
            root
            / "clinic_photos"
            / "clinic_photos"
            / "doctype"
            / "clinic_photo_save_operation"
            / "clinic_photo_save_operation.json"
        )
        before = runner.source_digest(root, case_manifest)
        doctype.write_text(doctype.read_text(encoding="utf-8") + " ", encoding="utf-8")
        after_doctype = runner.source_digest(root, case_manifest)
        self.assertNotEqual(before, after_doctype)
        case_manifest.write_text(
            case_manifest.read_text(encoding="utf-8").replace(
                '"app": "clinic_photos"', '"app": "clinic_photos" '
            ),
            encoding="utf-8",
        )
        after_manifest = runner.source_digest(root, case_manifest)
        self.assertNotEqual(after_doctype, after_manifest)

    def test_manifest_outside_the_package_tree_is_bound_by_content(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="task10-manifest-"))
        self.addCleanup(__import__("shutil").rmtree, outside, True)
        copy = outside / "case_manifest.json"
        copy.write_bytes(MANIFEST_PATH.read_bytes())
        first = runner.source_digest(APP_ROOT, copy)
        copy.write_text(
            copy.read_text(encoding="utf-8").replace('"app": "clinic_photos"', '"app": "clinic_photos" '),
            encoding="utf-8",
        )
        second = runner.source_digest(APP_ROOT, copy)
        self.assertNotEqual(first, second)
        entries = runner.source_inputs(APP_ROOT, copy)
        self.assertIn(
            "manifest::case_manifest.json", {entry[0] for entry in entries}
        )

    def test_outside_manifest_path_is_recorded_in_the_run(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="task10-manifest-"))
        self.addCleanup(__import__("shutil").rmtree, outside, True)
        copy = outside / "case_manifest.json"
        copy.write_bytes(MANIFEST_PATH.read_bytes())
        results = accepted_results(executable_cases(self.manifest))
        run = run_evidence(results, manifest_path=copy)
        self.assertEqual(run["manifest_path"], str(copy.resolve()))
        confirmation = disposal_for(run, *runner.run_residue(run))
        self.assertTrue(
            aggregate(
                self.manifest, results, confirmation, run, manifest_path=copy
            )["executable_subset_accepted"]
        )
        mismatched = aggregate(
            self.manifest, results, confirmation, run, manifest_path=MANIFEST_PATH
        )
        self.assertFalse(mismatched["executable_subset_accepted"])

    def test_run_manifest_path_must_be_the_supplied_manifest(self):
        outside = pathlib.Path(tempfile.mkdtemp(prefix="task10-manifest-"))
        self.addCleanup(__import__("shutil").rmtree, outside, True)
        copy = outside / "case_manifest.json"
        copy.write_bytes(MANIFEST_PATH.read_bytes())
        report = self._report(run=dict(self.run, manifest_path=str(copy)))
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("manifest", " ".join(report["problems"]))

    def test_evidence_schema_is_version_three(self):
        self.assertEqual(runner.EVIDENCE_SCHEMA, 3)
        self.assertEqual(self.run["schema"], 3)
        self.assertEqual(self.confirmation["schema"], 3)


class RunBindingTests(unittest.TestCase):
    """Producer record must be complete, versioned and a bijection with results."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _report(self, run, results=None, confirmation=None):
        return aggregate(
            self.manifest,
            self.results if results is None else results,
            confirmation if confirmation is not None else self.confirmation,
            run,
        )

    def _rejects(self, report, needle):
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn(needle, " ".join(report["problems"]).lower())

    def test_run_schema_is_version_checked(self):
        report = self._report(dict(self.run, schema=1))
        self._rejects(report, "run evidence schema")

    def test_run_schema_is_required(self):
        run = {key: value for key, value in self.run.items() if key != "schema"}
        report = self._report(run)
        self._rejects(report, "run evidence schema")

    def test_run_contract_digest_is_recomputed(self):
        report = self._report(dict(self.run, contract_digest="0" * 64))
        self._rejects(report, "command contract")

    def test_empty_run_case_list_is_rejected(self):
        run = {key: value for key, value in self.run.items() if key != "cases"}
        confirmation = dict(self.confirmation, case_nonces={}, raw_output_sha256={})
        report = self._report(run, confirmation=confirmation)
        self._rejects(report, "case list is empty")

    def test_subset_run_is_rejected(self):
        first = self.run["cases"][0]
        run = dict(self.run, cases=[first])
        confirmation = dict(
            self.confirmation,
            case_nonces={first["case"]: first["nonce"]},
            raw_output_sha256={first["case"]: first["raw_output_sha256"]},
        )
        report = self._report(run, confirmation=confirmation)
        self._rejects(report, "omits executable case")

    def test_subset_run_cannot_hide_unrecorded_raw_output(self):
        first = self.run["cases"][0]
        run = dict(self.run, cases=[first])
        confirmation = dict(
            self.confirmation,
            case_nonces={first["case"]: first["nonce"]},
            raw_output_sha256={first["case"]: first["raw_output_sha256"]},
        )
        tampered = list(self.results)
        victim = tampered[7]
        tampered[7] = dict(victim, output="FORGED PREPEND\n" + victim["output"])
        report = self._report(run, results=tampered, confirmation=confirmation)
        self._rejects(report, "omits executable case")

    def test_extra_run_case_is_rejected(self):
        entries = list(self.run["cases"]) + [
            dict(self.run["cases"][0], case="extra.case")
        ]
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "non-executable case")

    def test_duplicate_run_case_entry_is_rejected(self):
        entries = list(self.run["cases"]) + [dict(self.run["cases"][0])]
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "repeats a case entry")

    def test_decorative_expected_id_is_rejected(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["expected_id"] = "not.a.real.identity"
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "expected_id")

    def test_recorded_nonce_must_equal_the_result_nonce(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["nonce"] = "f" * 32
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "nonce")

    def test_recorded_raw_hash_must_equal_the_result_output(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["raw_output_sha256"] = "0" * 64
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "raw output")

    def test_recorded_residue_must_equal_the_derived_residue(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["residue_rows"] = []
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "residue")

    def test_recorded_storage_keys_must_equal_the_derived_keys(self):
        results = accepted_results(
            executable_cases(self.manifest),
            keys=("/private/files/clinic-photo-fixture.png",),
        )
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        entries = [dict(entry) for entry in run["cases"]]
        entries[0]["storage_keys"] = []
        report = aggregate(
            self.manifest, results, confirmation, dict(run, cases=entries)
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("storage keys", " ".join(report["problems"]))

    def test_recorded_status_must_equal_the_derived_status(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["derived_status"] = "skipped"
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "derived status")

    def test_recorded_orphan_residue_must_match(self):
        entries = [dict(entry) for entry in self.run["cases"]]
        entries[0]["orphan_residue_rows"] = [["File", "FILE-ORPHAN"]]
        report = self._report(dict(self.run, cases=entries))
        self._rejects(report, "orphan residue")

    def test_duplicate_nonces_across_results_are_rejected(self):
        results = [dict(result) for result in self.results]
        results[1] = dict(results[1], nonce=results[0]["nonce"])
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        report = aggregate(self.manifest, results, confirmation, run)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("duplicate case nonce", " ".join(report["problems"]))

    def test_reused_invocation_evidence_from_another_case_is_rejected(self):
        # A single nonce across every case means the outputs were not produced by
        # separate invocations; it must never be accepted as a matrix.
        results = [
            dict(result, nonce=NONCE) for result in self.results
        ]
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        report = aggregate(self.manifest, results, confirmation, run)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("duplicate case nonce", " ".join(report["problems"]))

    def test_producer_always_emits_every_executable_case(self):
        run = run_evidence(self.results[1:])
        recorded = {entry["case"] for entry in run["cases"]}
        self.assertEqual(
            recorded, {case["id"] for case in executable_cases(self.manifest)}
        )
        missing = [
            entry for entry in run["cases"] if entry["derived_status"] == "missing"
        ]
        self.assertEqual(len(missing), 1)
        report = aggregate(self.manifest, self.results[1:], None, run)
        self.assertFalse(report["executable_subset_accepted"])


class CoverageScopeTests(unittest.TestCase):
    """Subset success must never read as whole-matrix acceptance."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.report = aggregate(
            self.manifest, self.results, cleanup_for(self.results, self.run), self.run
        )

    def test_subset_success_is_labelled_not_laundered(self):
        self.assertTrue(self.report["executable_subset_accepted"])
        self.assertFalse(self.report["accepted"])
        self.assertFalse(self.report["publication_complete"])
        self.assertEqual(self.report["acceptance_scope"], "executable_subset_only")
        self.assertEqual(self.report["native_status"], "ACCEPTED_EXECUTABLE_SUBSET")
        self.assertTrue(self.report["incomplete_reasons"])
        self.assertNotEqual(self.report["accepted"], True)

    def test_coverage_counts_are_explicit(self):
        coverage = self.report["coverage"]
        self.assertEqual(coverage["executable"], 40)
        self.assertEqual(coverage["blocked_unrun"], 16)
        self.assertEqual(
            coverage["publication_required_blocked"], 16
        )
        self.assertFalse(coverage["publication_complete"])
        self.assertEqual(
            coverage["blocked_by_module"],
            {"clinic_photos.tests.test_publication": 16},
        )

    def test_dry_run_reports_readiness_not_acceptance(self):
        report = runner.build_dry_run(self.manifest, "site.test")
        self.assertFalse(report["accepted"])
        self.assertFalse(report["executable_subset_accepted"])
        self.assertFalse(report["publication_complete"])
        self.assertFalse(report["matrix_complete"])
        self.assertTrue(report["readiness_only"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        self.assertEqual(report["native_status"], "UNRUN")
        # Readiness mirrors the executed coverage except for the evidence axis: a dry
        # run has verified nothing, so every executable required case is unverified.
        for key in (
            "required_publication",
            "executable",
            "blocked_unrun",
            "blocked_by_module",
            "publication_required_missing",
            "publication_required_blocked",
            "publication_required_executable",
        ):
            with self.subTest(key=key):
                self.assertEqual(report["coverage"][key], self.report["coverage"][key])
        self.assertEqual(report["coverage"]["publication_required_verified"], 0)
        self.assertEqual(report["coverage"]["publication_required_unverified"], 9)
        self.assertTrue(all(row["native_status"] == "UNRUN" for row in report["cases"]))

    def test_unaccepted_subset_never_reports_a_success_status(self):
        report = aggregate(self.manifest, self.results, None, self.run)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["native_status"], "UNACCEPTED")


class ProducerRoundTripTests(unittest.TestCase):
    """The documented producer -> consumer re-aggregation must stay bound."""

    def test_report_run_block_can_be_reaggregated(self):
        manifest_object = manifest()
        results = accepted_results(executable_cases(manifest_object))
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        first = aggregate(manifest_object, results, confirmation, run)
        self.assertTrue(first["executable_subset_accepted"], first["problems"])
        reused = first["run"]
        self.assertTrue(reused["cases"])
        self.assertEqual(len(reused["cases"]), len(executable_cases(manifest_object)))
        self.assertEqual(reused["source_root"], str(APP_ROOT))
        self.assertTrue(reused["recorded_at"])
        second = aggregate(manifest_object, results, confirmation, reused)
        self.assertEqual(
            second["executable_subset_accepted"], first["executable_subset_accepted"]
        )
        self.assertEqual(second["problems"], first["problems"])

    def test_reused_report_run_block_without_cases_is_rejected(self):
        manifest_object = manifest()
        results = accepted_results(executable_cases(manifest_object))
        run = run_evidence(results)
        confirmation = cleanup_for(results, run)
        stripped = {key: value for key, value in run.items() if key != "cases"}
        report = aggregate(
            manifest_object,
            results,
            dict(confirmation, case_nonces={}, raw_output_sha256={}),
            stripped,
        )
        self.assertFalse(report["executable_subset_accepted"])


class DisposalTimeTests(unittest.TestCase):
    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _report(self, confirmation):
        return aggregate(self.manifest, self.results, confirmation, self.run)

    def test_disposal_before_the_run_is_rejected(self):
        confirmation = json.loads(json.dumps(self.confirmation))
        confirmation["disposal"]["observed_at"] = "2000-01-01T00:00:00Z"
        report = self._report(confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("before the run", " ".join(report["problems"]))

    def test_disposal_timestamp_must_be_iso_8601(self):
        confirmation = json.loads(json.dumps(self.confirmation))
        confirmation["disposal"]["observed_at"] = "yesterday"
        report = self._report(confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("ISO-8601", " ".join(report["problems"]))

    def test_run_record_must_carry_recorded_at(self):
        run = {key: value for key, value in self.run.items() if key != "recorded_at"}
        report = aggregate(self.manifest, self.results, self.confirmation, run)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("recorded_at", " ".join(report["problems"]))

    def test_confirmation_must_match_the_run_recorded_at(self):
        confirmation = json.loads(json.dumps(self.confirmation))
        confirmation["recorded_at"] = "2020-01-01T00:00:00Z"
        report = self._report(confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("record", " ".join(report["problems"]))


class OrphanResidueTests(unittest.TestCase):
    """Residue from an uncredited execution still reaches the operator."""

    METHOD = "test_reserved_test_keys_stay_in_the_orphan_namespace"

    def test_wrong_identity_output_surfaces_its_residue(self):
        output = evidence_output(
            self.METHOD, identity=case_id("test_some_other_case")
        )
        parsed = runner.parse_case_output(
            0, output, expected_id=case_id(self.METHOD), nonce=NONCE
        )
        self.assertEqual(parsed["status"], "wrong-identity")
        self.assertEqual(parsed["orphan_residue"], [["File", "FILE-1"]])
        self.assertEqual(
            parsed["orphan_storage_keys"],
            ["/private/files/clinic-photo-fixture.png"],
        )

    def test_orphan_residue_is_reported_and_blocks_acceptance(self):
        manifest_object = manifest()
        cases = executable_cases(manifest_object)
        results = accepted_results(cases)
        victim = cases[3]
        results[3] = {
            "case": victim["id"],
            "nonce": results[3]["nonce"],
            "exit_code": 0,
            "output": evidence_output(
                victim["method"],
                nonce=results[3]["nonce"],
                identity=case_id("test_other_case"),
            ),
        }
        run = run_evidence(results)
        report = aggregate(
            manifest_object, results, cleanup_for(results, run), run
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertEqual(report["orphan_residue"], [["File", "FILE-1"]])
        self.assertIn("uncredited execution", " ".join(report["problems"]))

    def test_missing_markers_do_not_invent_residue(self):
        output = "\n".join(
            line
            for line in evidence_output(self.METHOD).splitlines()
            if not line.startswith(runner.BEGIN_MARKER)
        )
        parsed = runner.parse_case_output(
            0, output, expected_id=case_id(self.METHOD), nonce=NONCE
        )
        self.assertEqual(parsed["status"], "no-evidence")
        self.assertEqual(parsed["orphan_residue"], [["File", "FILE-1"]])


class ExecuteTimeoutTests(unittest.TestCase):
    def test_timeout_is_recorded_not_raised(self):
        script = "import time; time.sleep(5)"
        outcome = runner.execute_case(
            [sys.executable, "-c", script], NONCE, timeout=1
        )
        self.assertEqual(outcome["exit_code"], -1)
        self.assertTrue(outcome.get("timed_out"))
        self.assertIn("timed out", outcome["output"])

    def test_timed_out_case_is_not_accepted(self):
        manifest_object = manifest()
        cases = executable_cases(manifest_object)
        results = accepted_results(cases)
        results[0] = dict(
            results[0], exit_code=-1, output="case timed out; no verdict evidence\n"
        )
        run = run_evidence(results)
        report = aggregate(
            manifest_object, results, cleanup_for(results, run), run
        )
        self.assertFalse(report["executable_subset_accepted"])


class CleanupReadErrorTests(unittest.TestCase):
    def test_unreadable_confirmation_reports_the_read_error(self):
        record, error = runner._load_cleanup("/nonexistent/cleanup.json")
        self.assertIsNone(record)
        self.assertIn("cannot read cleanup confirmation", error)

    def test_cli_reports_the_read_error(self):
        manifest_object = manifest()
        results = accepted_results(executable_cases(manifest_object))
        run = run_evidence(results)
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"run": run, "results": results}, handle)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runner.main(
                [
                    "--aggregate",
                    handle.name,
                    "--site",
                    "site.test",
                    "--cleanup-confirmed",
                    "/nonexistent/cleanup.json",
                    "--source-root",
                    str(APP_ROOT),
                ]
            )
        self.assertEqual(code, 1)
        report = json.loads(buffer.getvalue())
        self.assertIn("cannot read cleanup confirmation", report["cleanup_read_error"])
        self.assertFalse(report["accepted"])


class ExecutableCasePinTests(unittest.TestCase):
    """Per-case source pins: the body really runs the child and asserts its status."""

    CHILD_CASES = {
        "pub.process.unknown_operation_refused": "STATUS_REFUSED",
        "pub.process.site_binding_enforced": "STATUS_REFUSED",
        "pub.process.argv_shape_fixed": "status=refused",
        "pub.process.output_bounded_no_saved_claim": "STATUS_REFUSED",
    }

    # Fixture cases publish real objects. Their bodies stage through the real submission
    # boundary and launch the same fixed child through ``publish``; the helper itself is
    # pinned below to require a definite ``completed`` status before evidence is read.
    FIXTURE_CASES = {
        "pub.original.first_jpeg_mro_and_get_content": "self.publish(",
        "pub.access.replay_after_newer_version": "self.publish(",
        "pub.access.lost_response_replay": "self.publish(",
        "pub.access.allowlist_cross_actor_audit": "self.publish(",
    }

    # The two-arm revocation case drives its own orchestration helper instead of the
    # single-child helpers: both arms are explicit sequential calls from independent
    # baselines, not a subTest loop.
    REVOCATION_CASES = {
        "pub.access.disabled_or_revoked_actor": "self.exercise_revocation_arm(",
    }

    def setUp(self):
        self.manifest = manifest()
        self.cases = {
            case["id"]: case
            for case in self.manifest["cases"]
            if case.get("readiness") == "executable"
            and runner.case_module(self.manifest, case) == PUBLICATION_MODULE
        }

    def test_every_executable_publication_case_is_pinned(self):
        self.assertEqual(
            set(self.cases),
            set(self.CHILD_CASES)
            | set(self.FIXTURE_CASES)
            | set(self.REVOCATION_CASES),
        )

    def test_the_two_arm_revocation_case_pins_both_arms(self):
        body = _case_method_source(
            self.cases["pub.access.disabled_or_revoked_actor"], self.manifest
        )
        self.assertIn("REVOCATION_CASE_TIMEOUT", body)
        self.assertEqual(body.count("self.exercise_revocation_arm("), 2)
        self.assertLess(
            body.index('self.exercise_revocation_arm("disabled")'),
            body.index('self.exercise_revocation_arm("role_removed")'),
        )
        self.assertFalse(
            any(
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "subTest"
                for node in ast.walk(ast.parse(body))
            )
        )

    def test_each_fixture_case_stages_through_the_real_boundary_and_publishes(self):
        source = PUBLICATION_SUITE.read_text(encoding="utf-8")
        publish = _method_source(source, "publish", "PublicationNativeCase")
        self.assertIn("self.run_child(APPROVED_SITE, operation_id)", publish)
        self.assertIn("STATUS_COMPLETED", publish)
        self.assertIn("record_residue", publish)
        for case_id_, expected in self.FIXTURE_CASES.items():
            with self.subTest(case=case_id_):
                body = _case_method_source(self.cases[case_id_], self.manifest)
                self.assertIn("self.require_native_prerequisites()", body)
                self.assertIn("self.stage_and_commit(", body)
                self.assertIn(expected, body)
                self.assertIn("assertEqual", body)

    def test_each_case_invokes_the_fixed_child_and_asserts_a_definite_status(self):
        for case_id_, expected in self.CHILD_CASES.items():
            with self.subTest(case=case_id_):
                body = _case_method_source(self.cases[case_id_], self.manifest)
                if case_id_ == "pub.process.argv_shape_fixed":
                    self.assertIn("subprocess.run(", body)
                    self.assertIn("PROCESS_MODULE", body)
                else:
                    self.assertIn("self.run_child(", body)
                self.assertIn(expected, body)
                # A definite assertion, not a membership in a set of statuses.
                self.assertIn("assertEqual", body)

    def test_refusal_cases_record_the_observed_gate_posture(self):
        for case_id_ in (
            "pub.process.unknown_operation_refused",
            "pub.process.output_bounded_no_saved_claim",
        ):
            with self.subTest(case=case_id_):
                body = _case_method_source(self.cases[case_id_], self.manifest)
                self.assertIn("self.observe_gate()", body)

    def test_argv_case_checks_the_bench_environment_first(self):
        body = _case_method_source(
            self.cases["pub.process.argv_shape_fixed"], self.manifest
        )
        self.assertIn("self.require_bench_environment()", body)
        self.assertLess(
            body.index("self.require_bench_environment()"),
            body.index("subprocess.run("),
        )

    def test_module_header_does_not_claim_placeholder_provenance(self):
        source = PUBLICATION_SUITE.read_text(encoding="utf-8")
        header = source.split('"""', 2)[1]
        self.assertNotIn("authored definition", header)
        self.assertNotIn("Both kinds emit", header)
        self.assertIn("no provenance", header)
        self.assertIn("writes no BEGIN/END", header)

    def test_base_class_pins_the_child_contract(self):
        source = PUBLICATION_SUITE.read_text(encoding="utf-8")
        for required in (
            'SITES_PATH = "/home/frappe/frappe-bench/sites"',
            'BENCH_ENV = "/home/frappe/frappe-bench/env"',
            '"--operation-id"',
            "cwd=SITES_PATH",
            "require_bench_environment",
            "MAX_CHILD_OUTPUT_LINES",
            "MAX_CHILD_OUTPUT_BYTES",
            "HARNESS_OBSERVATION_PREFIX",
        ):
            self.assertIn(required, source)
        run_child = _method_source(
            source, "run_child", "PublicationNativeCase"
        )
        # The child entry itself stays mode-agnostic; only the fixture prerequisite
        # check reads the synthetic-mode gate.
        self.assertNotIn("SYNTHETIC_MODE", run_child)
        self.assertIn(
            "SYNTHETIC_MODE",
            _method_source(
                source, "require_native_prerequisites", "PublicationNativeCase"
            ),
        )
        self.assertIn("self.require_bench_environment()", run_child)
        self.assertIn("sys.executable", run_child)
        self.assertEqual(run_child.count("PROCESS_MODULE"), 1)


# The required publication inventory, written out independently here. This is the
# pin: it must not be derived from the runner constant or from the manifest.
REQUIRED_INVENTORY = (
    "pub.process.unknown_operation_refused",
    "pub.process.site_binding_enforced",
    "pub.process.argv_shape_fixed",
    "pub.process.output_bounded_no_saved_claim",
    "pub.schema.reservation_null_and_unique",
    "pub.schema.set_value_no_hooks_no_commit",
    "pub.original.first_jpeg_mro_and_get_content",
    "pub.original.hooks_and_rollback_residue",
    "pub.reservation.survives_rollback",
    "pub.reservation.retarget_reserved_refused",
    "pub.reservation.retarget_registered_refused",
    "pub.reservation.post_insert_retarget_requires_rollback",
    "pub.reservation.original_path_rollback_guarded",
    "pub.reservation.unsaved_file_rollback_guarded",
    "pub.reservation.administrator_delete_refused",
    "pub.atomicity.two_phase_atomic",
    "pub.atomicity.same_owner_after_commit",
    "pub.atomicity.revocation_between_phases",
    "pub.atomicity.unknown_commit_not_retried",
    "pub.atomicity.duplicate_launch_no_resume",
    "pub.access.replay_after_newer_version",
    "pub.access.lost_response_replay",
    "pub.access.allowlist_cross_actor_audit",
    "pub.access.disabled_or_revoked_actor",
    "pub.access.native_file_veto",
)


# The required id -> (module, class, method) identities, written out independently here
# as well. This is the identity pin: neither the runner's mapping nor the manifest is
# used to build it, so a drift in either direction fails.
REQUIRED_IDENTITIES = {
    "pub.process.unknown_operation_refused": (
        "clinic_photos.tests.test_publication",
        "PublicationProcessEntryCases",
        "test_child_refuses_unknown_operation_without_residue",
    ),
    "pub.process.site_binding_enforced": (
        "clinic_photos.tests.test_publication",
        "PublicationProcessEntryCases",
        "test_child_binds_the_approved_site_and_ignores_no_other",
    ),
    "pub.process.argv_shape_fixed": (
        "clinic_photos.tests.test_publication",
        "PublicationProcessEntryCases",
        "test_child_rejects_client_supplied_argv_shapes",
    ),
    "pub.process.output_bounded_no_saved_claim": (
        "clinic_photos.tests.test_publication",
        "PublicationProcessEntryCases",
        "test_child_output_is_phifree_and_never_claims_saved",
    ),
    "pub.schema.reservation_null_and_unique": (
        "clinic_photos.tests.test_publication",
        "PublicationSchemaNativeCases",
        "test_reservation_null_semantics_and_unique_index",
    ),
    "pub.schema.set_value_no_hooks_no_commit": (
        "clinic_photos.tests.test_publication",
        "PublicationSchemaNativeCases",
        "test_set_value_has_no_hooks_and_no_commit",
    ),
    "pub.original.first_jpeg_mro_and_get_content": (
        "clinic_photos.tests.test_publication",
        "PublicationOriginalNativeCases",
        "test_first_jpeg_original_uses_protected_mro_and_get_content",
    ),
    "pub.original.hooks_and_rollback_residue": (
        "clinic_photos.tests.test_publication",
        "PublicationOriginalNativeCases",
        "test_native_hooks_and_rollback_residue_observed",
    ),
    "pub.reservation.survives_rollback": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_prepared_reservation_survives_rollback",
    ),
    "pub.reservation.retarget_reserved_refused": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_dedup_retarget_reserved_refused",
    ),
    "pub.reservation.retarget_registered_refused": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_dedup_retarget_registered_refused",
    ),
    "pub.reservation.post_insert_retarget_requires_rollback": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_post_insert_retarget_requires_rollback",
    ),
    "pub.reservation.original_path_rollback_guarded": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_original_path_rollback_guarded",
    ),
    "pub.reservation.unsaved_file_rollback_guarded": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_unsaved_file_rollback_guarded",
    ),
    "pub.reservation.administrator_delete_refused": (
        "clinic_photos.tests.test_publication",
        "PublicationReservationNativeCases",
        "test_administrator_delete_refused",
    ),
    "pub.atomicity.two_phase_atomic": (
        "clinic_photos.tests.test_publication",
        "PublicationAtomicityNativeCases",
        "test_prepared_commit_then_publication_is_atomic",
    ),
    "pub.atomicity.same_owner_after_commit": (
        "clinic_photos.tests.test_publication",
        "PublicationAtomicityNativeCases",
        "test_same_owner_rechecks_after_commit",
    ),
    "pub.atomicity.revocation_between_phases": (
        "clinic_photos.tests.test_publication",
        "PublicationAtomicityNativeCases",
        "test_revocation_between_phases_blocks_publication",
    ),
    "pub.atomicity.unknown_commit_not_retried": (
        "clinic_photos.tests.test_publication",
        "PublicationAtomicityNativeCases",
        "test_unknown_commit_outcome_is_not_retried",
    ),
    "pub.atomicity.duplicate_launch_no_resume": (
        "clinic_photos.tests.test_publication",
        "PublicationAtomicityNativeCases",
        "test_duplicate_launch_does_not_resume_prepared",
    ),
    "pub.access.replay_after_newer_version": (
        "clinic_photos.tests.test_publication",
        "PublicationAccessNativeCases",
        "test_replay_after_newer_version_returns_immutable_version",
    ),
    "pub.access.lost_response_replay": (
        "clinic_photos.tests.test_publication",
        "PublicationAccessNativeCases",
        "test_lost_response_replay_resolves_original_tuple",
    ),
    "pub.access.allowlist_cross_actor_audit": (
        "clinic_photos.tests.test_publication",
        "PublicationAccessNativeCases",
        "test_operator_allowlist_cross_actor_audit_is_denied",
    ),
    "pub.access.disabled_or_revoked_actor": (
        "clinic_photos.tests.test_publication",
        "PublicationAccessNativeCases",
        "test_disabled_or_revoked_actor_cannot_replay",
    ),
    "pub.access.native_file_veto": (
        "clinic_photos.tests.test_publication",
        "PublicationAccessNativeCases",
        "test_native_file_veto_denies_replay",
    ),
}

# The one required id whose identity the rebound tests substitute, and the storage key
# used by the timeout residue test.
REBOUND_ID = "pub.schema.reservation_null_and_unique"
RESIDUE_STORAGE_KEY = "/private/files/clinic-photo-fixture.png"


def write_manifest(test_case, payload):
    """Write a synthetic manifest to its own file and register its cleanup."""
    import shutil

    directory = pathlib.Path(tempfile.mkdtemp(prefix="task10-manifest-"))
    test_case.addCleanup(shutil.rmtree, directory, True)
    path = directory / "case_manifest.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def synthetic_matrix(test_case, edit):
    """Write an edited manifest and build a fully passing matrix against that file."""
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    edit(payload)
    path = write_manifest(test_case, payload)
    manifest_object = json.loads(path.read_text(encoding="utf-8"))
    results = accepted_results(executable_cases(manifest_object))
    run = run_evidence(results, manifest_object=manifest_object, manifest_path=path)
    confirmation = cleanup_for(results, run)
    return manifest_object, results, run, confirmation, path


def drop_required_cases(payload):
    payload["cases"] = [
        case for case in payload["cases"] if case["id"] not in REQUIRED_INVENTORY
    ]


def repoint_required_modules(payload):
    for case in payload["cases"]:
        if case["id"] in REQUIRED_INVENTORY:
            case["module"] = "clinic_photos.tests.test_file_policy"


def publish_all_required(payload):
    for case in payload["cases"]:
        if case["id"] in REQUIRED_INVENTORY:
            case["readiness"] = "executable"
            case.pop("blocked_reason", None)


def block_first_legacy_case(payload):
    for case in payload["cases"]:
        if case["id"] in REQUIRED_INVENTORY:
            continue
        if runner.case_readiness(case) == "executable":
            case["readiness"] = "blocked"
            case["blocked_reason"] = "[fault-seam] synthetic test: required legacy case"
            return
    raise AssertionError("no executable legacy case to block")


def rebound_identity(axis):
    """A unique test identity one substitution axis away from the required one."""
    module, case_class, method = REQUIRED_IDENTITIES[REBOUND_ID]
    if axis == "module":
        return ("clinic_photos.tests.test_publication_synthetic", case_class, method)
    if axis == "case_class":
        return (module, "PublicationReboundSyntheticCases", method)
    if axis == "method":
        return (module, case_class, "test_rebound_required_identity_synthetic")
    raise AssertionError(axis)


def rebind_one_required_case(payload, axis):
    module, case_class, method = rebound_identity(axis)
    for case in payload["cases"]:
        if case["id"] == REBOUND_ID:
            case["module"] = module
            case["case_class"] = case_class
            case["method"] = method
            return
    raise AssertionError("required case absent from the shipped manifest")


def rebound_matrix(test_case, axis):
    """A full-evidence matrix whose only difference is one re-bound required id.

    Every required case is executable, every supplied output carries the markers of the
    identity that manifest declares (so the evidence is coherent with the changed
    identity), the run binds that identity, and the manifest object *is* the manifest
    file the run's digest points at. The identity substitution is the only defect.
    """
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    publish_all_required(payload)
    rebind_one_required_case(payload, axis)
    path = write_manifest(test_case, payload)
    manifest_object = json.loads(path.read_text(encoding="utf-8"))
    results = [
        {
            "case": case["id"],
            "nonce": nonce_for(case),
            "exit_code": 0,
            "output": evidence_output(
                case["method"],
                nonce=nonce_for(case),
                identity=runner.expected_case_id(manifest_object, case),
            ),
        }
        for case in executable_cases(manifest_object)
    ]
    run = run_evidence(results, manifest_object=manifest_object, manifest_path=path)
    return manifest_object, results, run, cleanup_for(results, run), path


class RequiredInventoryTests(unittest.TestCase):
    """The publication obligation is a frozen inventory, not a manifest-derived label."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def test_inventory_is_the_required_25_ids(self):
        self.assertEqual(len(REQUIRED_INVENTORY), 25)
        self.assertEqual(len(set(REQUIRED_INVENTORY)), 25)
        self.assertEqual(set(runner.REQUIRED_PUBLICATION_CASE_IDS), set(REQUIRED_INVENTORY))
        self.assertIsInstance(runner.REQUIRED_PUBLICATION_CASE_IDS, frozenset)

    def test_shipped_manifest_contains_every_required_case(self):
        shipped_ids = {case["id"] for case in self.manifest["cases"]}
        self.assertLessEqual(set(REQUIRED_INVENTORY), shipped_ids)
        readiness = {
            case["id"]: runner.case_readiness(case)
            for case in self.manifest["cases"]
            if case["id"] in REQUIRED_INVENTORY
        }
        self.assertEqual(
            sum(1 for value in readiness.values() if value == "blocked"), 16
        )
        self.assertEqual(
            sum(1 for value in readiness.values() if value == "executable"), 9
        )

    def test_the_inventory_cannot_be_waived_at_runtime(self):
        for function in (runner.aggregate, runner.build_dry_run):
            with self.subTest(function=function.__name__):
                parameters = inspect.signature(function).parameters
                self.assertNotIn("required_publication_ids", parameters)
                self.assertNotIn("skip_publication_inventory", parameters)

    def test_external_manifest_removal_keeps_the_obligation(self):
        manifest_object, results, run, confirmation, path = synthetic_matrix(
            self, drop_required_cases
        )
        report = aggregate(
            manifest_object, results, confirmation, run, manifest_path=path
        )
        coverage = report["coverage"]
        self.assertEqual(
            coverage["publication_required_missing"], len(REQUIRED_INVENTORY)
        )
        self.assertFalse(coverage["publication_complete"])
        self.assertFalse(report["publication_complete"])
        self.assertFalse(report["matrix_complete"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        # The verified subset itself is still honestly reportable.
        self.assertTrue(report["executable_subset_accepted"], report["problems"])

    def test_repointing_the_module_keeps_the_obligation(self):
        manifest_object, results, run, confirmation, path = synthetic_matrix(
            self, repoint_required_modules
        )
        report = aggregate(
            manifest_object, results, confirmation, run, manifest_path=path
        )
        coverage = report["coverage"]
        self.assertEqual(coverage["publication_required_blocked"], 16)
        # A module re-point is also an identity re-bind: the id keeps its obligation and
        # the substituted identity is reported instead of being credited.
        self.assertEqual(coverage["publication_required_rebound"], 25)
        self.assertEqual(
            coverage["publication_required_rebound_ids"],
            sorted(REQUIRED_INVENTORY),
        )
        self.assertFalse(coverage["publication_complete"])
        self.assertFalse(report["publication_complete"])
        self.assertFalse(report["accepted"])
        self.assertFalse(report["matrix_complete"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        # The executable subset is no longer creditable: its identities are rebound.
        self.assertFalse(report["executable_subset_accepted"])
        self.assertEqual(report["native_status"], "UNACCEPTED")
        self.assertIn(
            "not its required identity", " ".join(report["problems"])
        )

    def test_blocked_legacy_case_blocks_the_whole_matrix_only(self):
        manifest_object, results, run, confirmation, path = synthetic_matrix(
            self,
            lambda payload: (
                publish_all_required(payload),
                block_first_legacy_case(payload),
            ),
        )
        report = aggregate(
            manifest_object, results, confirmation, run, manifest_path=path
        )
        coverage = report["coverage"]
        self.assertEqual(coverage["publication_required_blocked"], 0)
        self.assertEqual(coverage["publication_required_unverified"], 0)
        self.assertTrue(coverage["publication_complete"])
        self.assertTrue(report["publication_complete"])
        self.assertEqual(coverage["blocked_unrun"], 1)
        self.assertFalse(report["matrix_complete"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["acceptance_scope"], "executable_subset_only")
        self.assertEqual(report["native_status"], "ACCEPTED_EXECUTABLE_SUBSET")
        self.assertTrue(
            any("blocked" in reason for reason in report["incomplete_reasons"])
        )

    def test_executable_required_cases_need_passing_evidence(self):
        manifest_object, _, _, _, _ = synthetic_matrix(self, publish_all_required)
        unverified = runner._coverage(manifest_object)
        self.assertEqual(unverified["publication_required_unverified"], 25)
        self.assertFalse(unverified["publication_complete"])
        verified = runner._coverage(
            manifest_object,
            {case["id"] for case in executable_cases(manifest_object)},
        )
        self.assertEqual(verified["publication_required_verified"], 25)
        self.assertEqual(verified["publication_required_unverified"], 0)
        self.assertTrue(verified["publication_complete"])

    def test_dry_run_reports_readiness_without_acceptance(self):
        report = runner.build_dry_run(self.manifest, "site.test")
        self.assertTrue(report["readiness_only"])
        self.assertFalse(report["publication_complete"])
        self.assertFalse(report["matrix_complete"])
        self.assertEqual(report["coverage"]["publication_required_blocked"], 16)
        self.assertEqual(report["coverage"]["publication_required_unverified"], 9)


class RequiredIdentityBindingTests(unittest.TestCase):
    """A required id is bound to its own test identity, not merely to a case row."""

    def setUp(self):
        self.manifest = manifest()

    def test_the_requirement_mapping_is_the_independent_literal(self):
        self.assertEqual(len(REQUIRED_IDENTITIES), 25)
        self.assertEqual(dict(runner.REQUIRED_PUBLICATION_CASES), REQUIRED_IDENTITIES)
        self.assertIsInstance(runner.REQUIRED_PUBLICATION_CASES, types.MappingProxyType)
        with self.assertRaises(TypeError):
            runner.REQUIRED_PUBLICATION_CASES[REBOUND_ID] = (
                "clinic_photos.tests.test_file_policy",
                "ProtectedFileBoundaryTests",
                "test_reserved_test_keys_stay_in_the_orphan_namespace",
            )
        with self.assertRaises(TypeError):
            del runner.REQUIRED_PUBLICATION_CASES[REBOUND_ID]

    def test_the_id_set_is_derived_from_the_mapping(self):
        self.assertEqual(
            set(runner.REQUIRED_PUBLICATION_CASE_IDS), set(REQUIRED_IDENTITIES)
        )
        self.assertEqual(len(runner.REQUIRED_PUBLICATION_CASE_IDS), 25)
        self.assertEqual(
            runner.REQUIRED_PUBLICATION_CASE_IDS,
            frozenset(runner.REQUIRED_PUBLICATION_CASES),
        )

    def test_the_mapping_has_no_runtime_override(self):
        for function in (runner.aggregate, runner.build_dry_run, runner._coverage):
            with self.subTest(function=function.__name__):
                parameters = inspect.signature(function).parameters
                self.assertNotIn("required_publication_cases", parameters)
                self.assertNotIn("required_publication_ids", parameters)

    def test_the_shipped_manifest_matches_every_required_identity(self):
        seen = set()
        for case in self.manifest["cases"]:
            expected = REQUIRED_IDENTITIES.get(case["id"])
            if expected is None:
                continue
            seen.add(case["id"])
            self.assertEqual(
                (
                    runner.case_module(self.manifest, case),
                    runner.case_class(self.manifest, case),
                    case["method"],
                ),
                expected,
                case["id"],
            )
        self.assertEqual(seen, set(REQUIRED_IDENTITIES))

    def test_each_substitution_axis_withholds_publication_completion(self):
        published, _, _, _, _ = synthetic_matrix(self, publish_all_required)
        for axis in ("module", "case_class", "method"):
            with self.subTest(axis=axis):
                doctored = json.loads(json.dumps(published))
                rebind_one_required_case(doctored, axis)
                verified = {case["id"] for case in executable_cases(doctored)}
                coverage = runner._coverage(doctored, verified)
                self.assertEqual(coverage["publication_required_rebound"], 1)
                self.assertEqual(
                    coverage["publication_required_rebound_ids"], [REBOUND_ID]
                )
                self.assertFalse(coverage["publication_complete"])

    def test_a_rebound_required_case_is_never_credited_as_verified(self):
        published, _, _, _, _ = synthetic_matrix(self, publish_all_required)
        doctored = json.loads(json.dumps(published))
        rebind_one_required_case(doctored, "method")
        verified = {case["id"] for case in executable_cases(doctored)}
        coverage = runner._coverage(doctored, verified)
        self.assertEqual(coverage["publication_required_executable"], 25)
        self.assertEqual(coverage["publication_required_verified"], 24)
        self.assertEqual(coverage["publication_required_unverified"], 0)
        self.assertEqual(coverage["publication_required_missing"], 0)
        self.assertEqual(coverage["publication_required_blocked"], 0)
        self.assertEqual(coverage["publication_required_rebound"], 1)
        self.assertFalse(coverage["publication_complete"])

    def test_the_shipped_matrix_is_unaffected(self):
        verified = {case["id"] for case in executable_cases(self.manifest)}
        coverage = runner._coverage(self.manifest, verified)
        self.assertEqual(coverage["publication_required_rebound"], 0)
        self.assertEqual(coverage["publication_required_rebound_ids"], [])
        self.assertEqual(coverage["publication_required_blocked"], 16)
        self.assertEqual(coverage["publication_required_verified"], 9)
        self.assertEqual(coverage["publication_required_unverified"], 0)
        self.assertFalse(coverage["publication_complete"])

    def test_dry_run_reports_a_rebound_requirement_without_acceptance(self):
        doctored = json.loads(json.dumps(self.manifest))
        rebind_one_required_case(doctored, "case_class")
        report = runner.build_dry_run(doctored, "site.test")
        self.assertTrue(report["readiness_only"])
        self.assertFalse(report["accepted"])
        self.assertFalse(report["executable_subset_accepted"])
        self.assertFalse(report["matrix_complete"])
        self.assertFalse(report["publication_complete"])
        self.assertEqual(report["coverage"]["publication_required_rebound"], 1)
        self.assertEqual(
            report["coverage"]["publication_required_rebound_ids"], [REBOUND_ID]
        )

    def test_a_full_evidence_baseline_is_still_accepted(self):
        """The control for the rebound tests: identical construction, no substitution."""
        payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        publish_all_required(payload)
        path = write_manifest(self, payload)
        manifest_object = json.loads(path.read_text(encoding="utf-8"))
        results = [
            {
                "case": case["id"],
                "nonce": nonce_for(case),
                "exit_code": 0,
                "output": evidence_output(
                    case["method"],
                    nonce=nonce_for(case),
                    identity=runner.expected_case_id(manifest_object, case),
                ),
            }
            for case in executable_cases(manifest_object)
        ]
        run = run_evidence(results, manifest_object=manifest_object, manifest_path=path)
        report = aggregate(
            manifest_object,
            results,
            cleanup_for(results, run),
            run,
            manifest_path=path,
        )
        self.assertTrue(report["accepted"], report["problems"])
        self.assertTrue(report["publication_complete"])
        self.assertTrue(report["matrix_complete"])
        self.assertEqual(report["coverage"]["publication_required_verified"], 25)
        self.assertEqual(report["coverage"]["publication_required_rebound"], 0)

    def test_the_acceptance_chain_rejects_a_real_identity_substitution(self):
        for axis in ("module", "case_class", "method"):
            with self.subTest(axis=axis):
                manifest_object, results, run, confirmation, path = rebound_matrix(
                    self, axis
                )
                # Everything else is coherent: the manifest validates, all 25 required
                # ids are present and executable, and every case's supplied markers
                # match the identity this manifest declares.
                self.assertEqual(runner.validate_manifest(manifest_object), [])
                self.assertEqual(
                    sum(
                        1
                        for case in manifest_object["cases"]
                        if case["id"] in REQUIRED_IDENTITIES
                    ),
                    25,
                )
                self.assertTrue(
                    all(entry["derived_status"] == "passed" for entry in run["cases"])
                )
                report = aggregate(
                    manifest_object,
                    results,
                    confirmation,
                    run,
                    manifest_path=path,
                )
                self.assertFalse(report["accepted"])
                self.assertFalse(report["publication_complete"])
                self.assertFalse(report["matrix_complete"])
                self.assertEqual(
                    report["coverage"]["publication_required_rebound"], 1
                )
                self.assertEqual(
                    report["coverage"]["publication_required_rebound_ids"],
                    [REBOUND_ID],
                )
                self.assertIn(
                    "not its required identity", " ".join(report["problems"])
                )
                self.assertTrue(
                    any(
                        "different test identity" in reason
                        for reason in report["incomplete_reasons"]
                    )
                )


class ManifestFileBindingTests(unittest.TestCase):
    """The manifest *object* must be the file the run's digest points at."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def test_doctored_object_with_the_real_path_is_rejected(self):
        doctored = json.loads(json.dumps(self.manifest))
        doctored["acceptance"] = "a tampered acceptance paragraph"
        report = aggregate(
            doctored, self.results, self.confirmation, self.run
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn(
            "manifest object is not the manifest file",
            " ".join(report["problems"]),
        )

    def test_doctored_required_rows_with_the_real_path_are_rejected(self):
        doctored = json.loads(json.dumps(self.manifest))
        repoint_required_modules(doctored)
        report = aggregate(
            doctored, self.results, self.confirmation, self.run
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertFalse(report["publication_complete"])
        self.assertIn(
            "manifest object is not the manifest file",
            " ".join(report["problems"]),
        )

    def test_an_unreadable_manifest_file_is_rejected(self):
        report = runner.aggregate(
            self.manifest,
            self.results,
            self.confirmation,
            run=self.run,
            source_root=str(APP_ROOT),
            manifest_path="/nonexistent/case_manifest.json",
            expected_site=SITE,
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("manifest", " ".join(report["problems"]))
        self.assertIn("unreadable", " ".join(report["problems"]))


class SiteBindingTests(unittest.TestCase):
    """The invocation site and the manifest app are independent inputs."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _report(self, run=None, confirmation=None, *, site="site.test"):
        return runner.aggregate(
            self.manifest,
            self.results,
            confirmation if confirmation is not None else self.confirmation,
            run=run if run is not None else self.run,
            source_root=str(APP_ROOT),
            manifest_path=str(MANIFEST_PATH),
            expected_site=site,
        )

    def test_a_fabricated_site_in_both_records_is_rejected(self):
        run = dict(self.run, site="other-bench-site.local")
        confirmation = dict(self.confirmation, site="other-bench-site.local")
        report = self._report(run, confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn(
            "not the trusted invocation site", " ".join(report["problems"])
        )

    def test_a_fabricated_project_in_both_records_is_rejected(self):
        run = dict(self.run, project="other-app")
        confirmation = dict(self.confirmation, project="other-app")
        report = self._report(run, confirmation)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("project is not the manifest app", " ".join(report["problems"]))

    def test_a_missing_trusted_site_refuses_acceptance(self):
        report = self._report(site=None)
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("trusted invocation site", " ".join(report["problems"]))

    def test_an_empty_trusted_site_refuses_acceptance(self):
        report = self._report(site="   ")
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("trusted invocation site", " ".join(report["problems"]))

    def test_the_cli_compares_against_the_supplied_site(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"run": self.run, "results": self.results}, handle)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runner.main(
                [
                    "--aggregate",
                    handle.name,
                    "--site",
                    "other-bench-site.local",
                    "--source-root",
                    str(APP_ROOT),
                    "--cleanup-confirmed",
                    str(MANIFEST_PATH),
                ]
            )
        self.assertEqual(code, 1)
        report = json.loads(buffer.getvalue())
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn(
            "not the trusted invocation site", " ".join(report["problems"])
        )


class TimeoutEvidenceTests(unittest.TestCase):
    """A timed-out execution is rejected even with complete passing markers."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def test_timed_out_with_passing_output_is_rejected(self):
        results = [dict(entry) for entry in self.results]
        results[0] = dict(results[0], timed_out=True)
        run = run_evidence(results)
        report = aggregate(
            self.manifest, results, cleanup_for(results, run), run
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn("timed out", " ".join(report["problems"]))

    def test_timed_out_case_still_surrenders_its_residue(self):
        results = [
            dict(entry)
            for entry in accepted_results(
                executable_cases(self.manifest), keys=(RESIDUE_STORAGE_KEY,)
            )
        ]
        results[0] = dict(results[0], timed_out=True)
        run = run_evidence(results)
        report = aggregate(
            self.manifest, results, cleanup_for(results, run), run
        )
        self.assertFalse(report["executable_subset_accepted"])
        self.assertIn(["File", "FILE-1"], report["cleanup_required"])
        self.assertEqual(
            report["cleanup_required_storage_keys"], [RESIDUE_STORAGE_KEY]
        )
        self.assertTrue(report["cleanup_digest"])

    def test_timed_out_false_is_still_accepted(self):
        results = [dict(entry, timed_out=False) for entry in self.results]
        run = run_evidence(results)
        report = aggregate(
            self.manifest, results, cleanup_for(results, run), run
        )
        self.assertTrue(report["executable_subset_accepted"], report["problems"])


class CleanupStatusConsistencyTests(unittest.TestCase):
    """A post-aggregate refusal must never leave a success machine status."""

    def setUp(self):
        self.manifest = manifest()
        self.results = accepted_results(executable_cases(self.manifest))
        self.run = run_evidence(self.results)
        self.confirmation = cleanup_for(self.results, self.run)

    def _assert_consistent(self, report):
        self.assertFalse(report["executable_subset_accepted"])
        self.assertFalse(report["accepted"])
        self.assertNotIn(
            report["native_status"], ("ACCEPTED", "ACCEPTED_EXECUTABLE_SUBSET")
        )
        self.assertEqual(report["native_status"], "UNACCEPTED")

    def test_aggregate_branch_recomputes_native_status(self):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"run": self.run, "results": self.results}, handle)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        error = "cannot read cleanup confirmation: simulated read error"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = runner.main(
                [
                    "--aggregate",
                    handle.name,
                    "--site",
                    "site.test",
                    "--source-root",
                    str(APP_ROOT),
                    "--cleanup-confirmed",
                    "/nonexistent/cleanup.json",
                ]
            )
        self.assertEqual(code, 1)
        report = json.loads(buffer.getvalue())
        self.assertIn("cannot read cleanup confirmation", report["cleanup_read_error"])
        self._assert_consistent(report)

    def test_aggregate_branch_with_a_valid_record_but_a_read_error(self):
        # Focused double: the confirmation is valid, but the read is reported as having
        # failed. Acceptance is withdrawn and the machine status must follow.
        error = "cannot read cleanup confirmation: simulated read error"
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"run": self.run, "results": self.results}, handle)
        handle.close()
        self.addCleanup(pathlib.Path(handle.name).unlink)
        buffer = io.StringIO()
        with mock.patch.object(
            runner, "_load_cleanup", lambda path: (self.confirmation, error)
        ):
            with contextlib.redirect_stdout(buffer):
                code = runner.main(
                    [
                        "--aggregate",
                        handle.name,
                        "--site",
                        "site.test",
                        "--source-root",
                        str(APP_ROOT),
                        "--cleanup-confirmed",
                        "double",
                    ]
                )
        self.assertEqual(code, 1)
        report = json.loads(buffer.getvalue())
        self._assert_consistent(report)
        self.assertIn(error, report["problems"])

    def test_execute_branch_with_a_valid_record_but_a_read_error(self):
        error = "cannot read cleanup confirmation: simulated read error"
        method_to_case = {
            case["method"]: case for case in self.manifest["cases"]
        }
        captured = {}
        real_run_evidence = runner.run_evidence

        def spy_run_evidence(*args, **kwargs):
            built = real_run_evidence(*args, **kwargs)
            captured["run"] = built
            return built

        def fake_execute(command, nonce, timeout=1800):
            method = command[command.index("--test") + 1]
            case = method_to_case[method]
            return {
                "exit_code": 0,
                "output": evidence_output(
                    method,
                    nonce=nonce,
                    identity=runner.expected_case_id(self.manifest, case),
                ),
            }

        def fake_load(path):
            run = captured["run"]
            return (
                disposal_for(run, *runner.run_residue(run)),
                error,
            )

        buffer = io.StringIO()
        with mock.patch.object(runner, "run_evidence", spy_run_evidence), \
                mock.patch.object(
                    runner, "check_verification", lambda path, contract=None: (True, "ok")
                ), \
                mock.patch.object(runner, "execute_case", fake_execute), \
                mock.patch.object(runner, "_load_cleanup", fake_load):
            with contextlib.redirect_stdout(buffer):
                code = runner.main(
                    [
                        "--execute",
                        "--site",
                        "site.test",
                        "--source-root",
                        str(APP_ROOT),
                        "--cli-verified",
                        "double",
                        "--cleanup-confirmed",
                        "double",
                    ]
                )
        self.assertEqual(code, 1)
        report = json.loads(buffer.getvalue())
        self._assert_consistent(report)
        self.assertIn(error, report["cleanup_read_error"])


class ManifestConsistencyTests(unittest.TestCase):
    def test_manifest_evidence_schema_matches_the_runner(self):
        shipped = manifest()
        self.assertEqual(shipped.get("evidence_schema"), 3)
        self.assertEqual(shipped["evidence_schema"], runner.EVIDENCE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
