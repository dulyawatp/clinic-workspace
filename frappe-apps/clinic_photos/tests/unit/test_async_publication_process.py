"""Focused tests for the fresh publication process, launcher and rediscovery.

The tests exercise the real ``clinic_photos.publication.process`` and
``clinic_photos.publication.launcher`` modules at their public seams. A narrow
in-memory Frappe stand-in is installed only at the framework import boundary, with
the exact installed signatures for the called symbols (``init``, ``connect``,
``destroy``, ``set_user``, ``db.get_value``/``get_values``/``get_all``,
``has_permission``, ``cache`` and ``enqueue``). A permissive ``**kwargs`` fake could
hide an unsupported argument, so each fake method refuses unknown arguments.

What these tests do NOT prove: installed Frappe/MariaDB/Redis execution, the request
transaction, real queue delivery, real scheduler synchronization, OS lock ownership
or publication. The coordinator module does not exist in Task 4, so every reachable
child refuses before bytes. Timeout and exit codes never establish a saved outcome.
"""

import contextlib
import inspect
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import types
import uuid
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.publication import contracts, journal, launcher, process, submission

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
PATIENT = "SYNTHETIC-PATIENT-1"
REQUEST_KEY = "request-key-0001"
OPERATION_ID = "a" * 32
ANNOTATION = (
    '{"coordinate_space":"normalized","schema_version":1,"shapes":[]}'
)
GATE = launcher.GATE_CONFIG_KEY

OPERATION_ID_PATTERN = re.compile(r"[0-9a-f]{32}")


def _row(operation_id=OPERATION_ID, *, state="SUBMITTED", site=SITE):
    request = contracts.SaveRequest(
        kind=contracts.ANNOTATION_APPEND,
        request_key=REQUEST_KEY,
        draft=DRAFT,
        expected_version=0,
        original=PHOTO,
        annotation=ANNOTATION,
    )
    return {
        "name": operation_id,
        "site": site,
        "actor": ACTOR,
        "kind": request.kind,
        "request_key": request.request_key,
        "identity_digest": journal.identity_digest(
            site, ACTOR, request.kind, request.request_key
        ),
        "payload_digest": journal.payload_digest(request),
        "state": state,
        "draft": request.draft,
        "expected_version": request.expected_version,
        "original": request.original,
        "synthetic_patient": None,
        "annotation_json": request.annotation,
        "image_base64": None,
        "reservation_key": None,
    }


def _target_docs(user=ACTOR, enabled=1, role="Physician"):
    return {
        "Clinic Photo Save Operation": [_row()],
        "Clinic Photo Draft": [
            {
                "name": DRAFT,
                "author": ACTOR,
                "photo": PHOTO,
                "current_version_number": 1,
                "current_version": None,
            }
        ],
        "Clinic Photo": [
            {
                "name": PHOTO,
                "patient_reference": PATIENT,
                "original_file": "FILE-1",
                "storage_key": "",
            }
        ],
        "Patient": [{"name": PATIENT}],
        "User": [{"name": user, "enabled": enabled}],
        "Has Role": [
            {
                "name": f"has-role-{user}",
                "parent": user,
                "parenttype": "User",
                "role": role,
            }
        ],
    }


def _pad_space(value):
    """Mirror a PAD SPACE collation: trailing spaces do not make strings unequal."""
    return value.rstrip(" ") if isinstance(value, str) else value


def _match(row, filters):
    if not filters:
        return True
    for fieldname, condition in filters.items():
        if isinstance(condition, tuple):
            operator, expected = condition
            if operator == "=":
                if _pad_space(row.get(fieldname)) != _pad_space(expected):
                    return False
            elif operator == ">":
                if not row.get(fieldname) > expected:
                    return False
            elif operator == "!=":
                # SQL semantics: a NULL column never matches a non-equality filter, and
                # the shipped collation is PAD SPACE, so "   " = "" compares equal.
                value = row.get(fieldname)
                if value is None or _pad_space(value) == _pad_space(expected):
                    return False
            elif operator == "in":
                if row.get(fieldname) not in expected:
                    return False
            elif operator == "is":
                # Real Frappe 16 semantics: ("is", "set") does NOT compile to
                # IS NOT NULL. For text columns it becomes `!= ''`, evaluated under the
                # shipped PAD SPACE collation, so a whitespace-only payload compares
                # equal to '' and is MISSED. This fake keeps that trap observable.
                if expected == "set":
                    value = row.get(fieldname)
                    if value is None or _pad_space(value) == "":
                        return False
                elif expected == "not set":
                    value = row.get(fieldname)
                    if not (value is None or _pad_space(value) == ""):
                        return False
                else:  # pragma: no cover - unsupported filter is a test bug
                    raise AssertionError(f"unsupported 'is' operand {expected!r}")
            else:  # pragma: no cover - unsupported filter is a test bug
                raise AssertionError(f"unsupported filter operator {operator!r}")
        elif row.get(fieldname) != condition:
            return False
    return True


def _order(rows, order_by):
    if not order_by:
        return rows
    clauses = [clause.strip() for clause in order_by.split(",")]
    for clause in reversed(clauses):
        parts = clause.split()
        fieldname = parts[0]
        reverse = len(parts) > 1 and parts[1].lower() == "desc"
        rows = sorted(rows, key=lambda row: row.get(fieldname), reverse=reverse)
    return rows


class FakeCallbacks:
    __slots__ = ("_functions",)

    def __init__(self):
        self._functions = deque()

    def add(self, callback):
        self._functions.append(callback)

    def run(self):
        while self._functions:
            self._functions.popleft()()

    def reset(self):
        self._functions.clear()


class FakeCache:
    def __init__(self):
        self.values = {}
        self.set_calls = []
        self.delete_calls = []

    def get_value(
        self, key, generator=None, user=None, expires=False, shared=False, *, use_local_cache=True
    ):
        return self.values.get(key)

    def set_value(self, key, val, user=None, expires_in_sec=None, shared=False):
        self.set_calls.append((key, val, expires_in_sec))
        self.values[key] = val
        return 0

    def delete_value(self, keys, user=None, make_keys=True, shared=False):
        if not isinstance(keys, (list, tuple)):
            keys = (keys,)
        for key in keys:
            self.values.pop(key, None)
            self.delete_calls.append(key)


class FakeDB:
    def __init__(self, rows=None):
        self.rows = {}
        for doctype, doctype_rows in (rows or {}).items():
            self.rows[doctype] = [dict(row) for row in doctype_rows]
        self.sql_calls = []
        self.before_commit = FakeCallbacks()
        self.after_commit = FakeCallbacks()
        self.before_rollback = FakeCallbacks()
        self.after_rollback = FakeCallbacks()
        self.transaction_writes = 0
        self.commit_calls = 0
        self.rollback_calls = 0

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        ignore=False,
        as_dict=False,
        debug=False,
        order_by=None,
        cache=False,
        for_update=False,
        *,
        run=True,
        pluck=False,
        distinct=False,
        skip_locked=False,
        wait=True,
    ):
        rows = [row for row in self.rows.get(doctype, []) if _match(row, {"name": filters} if isinstance(filters, str) else filters)]
        if not rows:
            return None
        row = rows[0]
        if as_dict:
            return {field: row.get(field) for field in fieldname}
        if isinstance(fieldname, (list, tuple)):
            return [row.get(field) for field in fieldname]
        return row.get(fieldname)

    def get_all(
        self,
        doctype,
        filters=None,
        fields="*",
        order_by=None,
        limit=None,
        offset=0,
        *,
        pluck=None,
    ):
        rows = [row for row in self.rows.get(doctype, []) if _match(row, filters)]
        rows = _order(rows, order_by)
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        return [dict(row) for row in rows]

    def get_values(
        self,
        doctype,
        filters=None,
        fieldname="name",
        ignore=False,
        as_dict=False,
        debug=False,
        order_by=None,
        cache=False,
        for_update=False,
        *,
        run=True,
        pluck=False,
        distinct=False,
        limit=None,
        skip_locked=False,
        wait=True,
    ):
        rows = [row for row in self.rows.get(doctype, []) if _match(row, filters)]
        rows = _order(rows, order_by)
        if limit:
            rows = rows[:limit]
        if as_dict:
            fields = [fieldname] if isinstance(fieldname, str) else list(fieldname)
            return [{field: row.get(field) for field in fields} for row in rows]
        return [row.get(fieldname) for row in rows]

    def sql(self, query, values=(), as_dict=False, **_kwargs):
        """Model the one parameterised presence query the launcher issues.

        The fake never parses SQL for values: it records the query text and applies the
        documented ``IS NOT NULL`` presence test to the rows whose identities were
        bound in ``values``, so an interpolated identity would simply not match.
        """
        self.sql_calls.append((query, values, as_dict))
        normalized = " ".join(str(query).split()).lower()
        if "`image_base64` is not null" not in normalized:
            raise AssertionError(f"unexpected fake SQL: {query!r}")
        bound = values if isinstance(values, (list, tuple)) else (values,)
        names = {value for value in bound if isinstance(value, str)}
        rows = [
            row
            for row in self.rows.get("Clinic Photo Save Operation", [])
            if row.get("name") in names and row.get("image_base64") is not None
        ]
        if as_dict:
            return [{"name": row["name"]} for row in rows]
        return [(row["name"],) for row in rows]

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1


class FakeFrappe(types.ModuleType):
    """Strict stand-in for the exact installed symbols this feature calls."""

    def __init__(
        self,
        rows=None,
        *,
        actual_site=SITE,
        conf=None,
        user="Guest",
        permissions=None,
    ):
        super().__init__("frappe")
        self.db = FakeDB(rows)
        self.cache = FakeCache()
        self.local = types.SimpleNamespace(site="")
        self.session = types.SimpleNamespace(user=user)
        self.conf = dict(conf if conf is not None else {GATE: True})
        self.actual_site = actual_site
        self.permissions = set(permissions or ())
        self.init_calls = []
        self.connect_calls = []
        self.set_user_calls = []
        self.destroy_calls = 0
        self.permission_calls = []
        self.enqueue_calls = []
        self.enqueue_failure = None
        self.enqueue_fail_on_call = None

    def init(self, site, sites_path=".", new_site=False, force=False, *, is_request=False, is_job=False):
        self.init_calls.append((site, sites_path))
        self.local.site = self.actual_site
        self.local.initialised = True
        self.session.user = "Guest"

    def connect(self, site=None, db_name=None, set_admin_as_user=True):
        self.connect_calls.append(set_admin_as_user)
        if set_admin_as_user:
            self.session.user = "Administrator"

    def set_user(self, username):
        self.set_user_calls.append(username)
        self.session.user = username

    def destroy(self):
        self.destroy_calls += 1
        self.local.site = ""

    def has_permission(
        self,
        doctype=None,
        ptype="read",
        doc=None,
        user=None,
        throw=False,
        *,
        parent_doctype=None,
        debug=False,
        ignore_share_permissions=False,
    ):
        name = getattr(doc, "name", doc)
        self.permission_calls.append((doctype, ptype, name, throw))
        return (doctype, ptype, name) in self.permissions

    def enqueue(
        self,
        method,
        queue="default",
        timeout=None,
        event=None,
        is_async=True,
        job_name=None,
        now=False,
        enqueue_after_commit=False,
        *,
        on_success=None,
        on_failure=None,
        at_front=False,
        job_id=None,
        deduplicate=False,
        at_front_when_starved=False,
        **kwargs,
    ):
        call = {
            "method": method,
            "queue": queue,
            "timeout": timeout,
            "event": event,
            "is_async": is_async,
            "job_name": job_name,
            "now": now,
            "enqueue_after_commit": enqueue_after_commit,
            "kwargs": dict(kwargs),
        }
        self.enqueue_calls.append(call)
        if self.enqueue_failure is not None:
            raise self.enqueue_failure
        if self.enqueue_fail_on_call == len(self.enqueue_calls):
            raise ConnectionError("redis unavailable")
        return None


@contextlib.contextmanager
def fake_runtime(rows=None, **kwargs):
    module = FakeFrappe(rows, **kwargs)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


def _all_permissions():
    return {
        ("Clinic Photo Draft", "read", DRAFT),
        ("Clinic Photo Draft", "write", DRAFT),
        ("Clinic Photo", "read", PHOTO),
        ("Patient", "read", PATIENT),
    }


class ProcessEntryTests(unittest.TestCase):
    def run_child(self, frappe, operation_id=OPERATION_ID, site=SITE):
        output = io.StringIO()
        with mock.patch.dict(
            sys.modules, {"clinic_photos.publication.coordinator": None}
        ):
            with contextlib.redirect_stdout(output):
                code = process.run(operation_id, site=site)
        return code, output.getvalue()

    def test_process_uses_fresh_sites_context_and_persisted_actor(self):
        with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
            code, output = self.run_child(frappe)

        self.assertEqual(code, process.EXIT_UNAVAILABLE)
        self.assertEqual(frappe.init_calls, [(SITE, process.SITES_PATH)])
        self.assertEqual(frappe.connect_calls, [False])
        self.assertEqual(frappe.set_user_calls, [ACTOR])
        self.assertNotIn("Administrator", frappe.set_user_calls)
        self.assertEqual(frappe.destroy_calls, 1)
        self.assertEqual(frappe.db.commit_calls, 0)
        self.assertEqual(frappe.db.rollback_calls, 0)

        # Authority rechecks ran after adopting the stored actor.
        self.assertIn(("Clinic Photo Draft", "read", DRAFT, False), frappe.permission_calls)
        self.assertIn(("Clinic Photo Draft", "write", DRAFT, False), frappe.permission_calls)
        self.assertIn(("Clinic Photo", "read", PHOTO, False), frappe.permission_calls)

        self.assertEqual(output, f"status=unavailable operation_id={OPERATION_ID}\n")
        for secret in (ACTOR, DRAFT, PHOTO, PATIENT, "annotation", "Administrator"):
            self.assertNotIn(secret, output)

    def test_invalid_identity_or_site_refuses_before_native_context(self):
        for operation_id, site in (
            ("not-an-operation-id", SITE),
            ("A" * 32, SITE),
            (OPERATION_ID, "other.example"),
            (OPERATION_ID, ""),
            (None, SITE),
        ):
            with self.subTest(operation_id=operation_id, site=site):
                with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
                    code, output = self.run_child(frappe, operation_id, site)
                self.assertEqual(code, process.EXIT_REFUSED)
                self.assertEqual(frappe.init_calls, [])
                self.assertEqual(frappe.connect_calls, [])
                self.assertNotIn("other.example", output)
                if operation_id == OPERATION_ID:
                    self.assertEqual(
                        output,
                        f"status=refused operation_id={OPERATION_ID}\n",
                    )
                else:
                    self.assertEqual(output, "status=refused\n")

    def test_disabled_gate_refuses_after_connect_and_before_actor_adoption(self):
        with fake_runtime(
            _target_docs(), conf={GATE: False}, permissions=_all_permissions()
        ) as frappe:
            code, output = self.run_child(frappe)

        self.assertEqual(code, process.EXIT_REFUSED)
        self.assertEqual(frappe.init_calls, [(SITE, process.SITES_PATH)])
        self.assertEqual(frappe.connect_calls, [False])
        self.assertEqual(frappe.set_user_calls, [])
        self.assertEqual(frappe.destroy_calls, 1)
        self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")

    def test_stored_site_mismatch_refuses_before_actor_adoption(self):
        rows = _target_docs()
        rows["Clinic Photo Save Operation"] = [_row(site="another-site.local")]
        with fake_runtime(rows, permissions=_all_permissions()) as frappe:
            code, _output = self.run_child(frappe)

        self.assertEqual(code, process.EXIT_REFUSED)
        self.assertEqual(frappe.set_user_calls, [])
        self.assertEqual(frappe.db.commit_calls, 0)
        self.assertEqual(frappe.db.rollback_calls, 0)

    def test_advanced_state_is_never_retried_or_adopted(self):
        for state in ("PREPARED", "PUBLISHED", "ABORTED", "QUARANTINED"):
            with self.subTest(state=state):
                rows = _target_docs()
                rows["Clinic Photo Save Operation"] = [_row(state=state)]
                with fake_runtime(rows, permissions=_all_permissions()) as frappe:
                    code, output = self.run_child(frappe)
                self.assertEqual(code, process.EXIT_REFUSED)
                self.assertEqual(frappe.set_user_calls, [])
                self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")

    def test_revoked_actor_is_reauthorized_after_adoption(self):
        with fake_runtime(
            _target_docs(enabled=0), permissions=_all_permissions()
        ) as frappe:
            code, output = self.run_child(frappe)

        self.assertEqual(code, process.EXIT_REFUSED)
        self.assertEqual(frappe.set_user_calls, [ACTOR])
        self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")
        self.assertEqual(frappe.db.commit_calls, 0)

    def test_unknown_bootstrap_callbacks_or_writes_refuse_without_clearing(self):
        with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
            marker = lambda: None  # noqa: E731 - identity is irrelevant
            frappe.db.after_commit.add(marker)
            code, _output = self.run_child(frappe)
            self.assertEqual(code, process.EXIT_REFUSED)
            self.assertEqual(frappe.set_user_calls, [])
            self.assertEqual(list(frappe.db.after_commit._functions), [marker])

        with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
            frappe.db.transaction_writes = 1
            code, _output = self.run_child(frappe)
            self.assertEqual(code, process.EXIT_REFUSED)
            self.assertEqual(frappe.set_user_calls, [])

    def test_duplicate_launch_cannot_advance_or_mutate_the_operation(self):
        with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
            first, _ = self.run_child(frappe)
            second, _ = self.run_child(frappe)
            rows = frappe.db.rows["Clinic Photo Save Operation"]

        self.assertEqual((first, second), (process.EXIT_UNAVAILABLE, process.EXIT_UNAVAILABLE))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "SUBMITTED")
        self.assertEqual(frappe.db.commit_calls, 0)
        self.assertEqual(frappe.db.rollback_calls, 0)
        self.assertNotIn("File", frappe.db.rows)

    def test_module_guard_refuses_invalid_argv_without_native_context(self):
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        commands = (
            [
                sys.executable,
                "-m",
                "clinic_photos.publication.process",
                "--site",
                SITE,
            ],
            [
                sys.executable,
                "-m",
                "clinic_photos.publication.process",
                "--operation-id",
                OPERATION_ID,
                "--site",
                SITE,
            ],
            [
                sys.executable,
                "-m",
                "clinic_photos.publication.process",
                "--site",
                "other.example",
                "--operation-id",
                OPERATION_ID,
            ],
            [
                sys.executable,
                "-m",
                "clinic_photos.publication.process",
                "--site",
                SITE,
                "--operation-id",
                "not-an-operation-id",
            ],
        )
        for command in commands:
            with self.subTest(command=command[3:]):
                completed = subprocess.run(
                    command,
                    cwd=str(APP_ROOT),
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(completed.returncode, process.EXIT_REFUSED)
                if "not-an-operation-id" in command:
                    expected = "status=refused\n"
                elif "other.example" in command:
                    expected = f"status=refused operation_id={OPERATION_ID}\n"
                else:
                    expected = "status=refused\n"
                self.assertEqual(completed.stdout, expected)
                self.assertEqual(completed.stderr, "")


class FakeCoordinator(types.ModuleType):
    """Strict stand-in for the two coordinator seams ``process.run`` consults."""

    def __init__(self, outcome=None, *, error=None):
        super().__init__("clinic_photos.publication.coordinator")
        self.outcome = outcome
        self.error = error
        self.submissions = []
        self.scopes = 0

    @contextlib.contextmanager
    def child_process_publication(self):
        self.scopes += 1
        yield object()

    def publish_submission(self, submission):
        self.submissions.append(submission)
        if self.error is not None:
            raise self.error
        return self.outcome


class ProcessOutcomeTests(unittest.TestCase):
    """The child marker is derived from the coordinator's returned outcome.

    Only a ``saved`` outcome that names this exact operation is completion. Every other
    status, a foreign identity and a malformed return value stay non-completed: the
    child never retries, never re-derives saved, and never prints payload or actor.
    """

    def run_child(self, coordinator, operation_id=OPERATION_ID):
        output = io.StringIO()
        with fake_runtime(_target_docs(), permissions=_all_permissions()) as frappe:
            with mock.patch.dict(
                sys.modules, {"clinic_photos.publication.coordinator": coordinator}
            ):
                with contextlib.redirect_stdout(output):
                    code = process.run(operation_id, site=SITE)
        return code, output.getvalue(), frappe

    def test_saved_outcome_for_this_operation_is_the_only_completion(self):
        coordinator = FakeCoordinator(
            contracts.SaveOutcome("saved", OPERATION_ID, version=2, file_name="FILE-2")
        )
        code, output, frappe = self.run_child(coordinator)

        self.assertEqual(code, process.EXIT_COMPLETED)
        self.assertEqual(output, f"status=completed operation_id={OPERATION_ID}\n")
        self.assertEqual(coordinator.scopes, 1)
        self.assertEqual([item.id for item in coordinator.submissions], [OPERATION_ID])
        # The coordinator owns commit/rollback; the child still does neither.
        self.assertEqual(frappe.db.commit_calls, 0)
        self.assertEqual(frappe.db.rollback_calls, 0)
        self.assertNotIn("File", frappe.db.rows)
        for secret in (ACTOR, DRAFT, PHOTO, PATIENT, ANNOTATION, "FILE-2"):
            self.assertNotIn(secret, output)

    def test_indeterminate_outcomes_never_report_completion(self):
        for status in ("pending", "unknown"):
            with self.subTest(status=status):
                coordinator = FakeCoordinator(contracts.SaveOutcome(status, OPERATION_ID))
                code, output, frappe = self.run_child(coordinator)

                self.assertEqual(code, process.EXIT_UNAVAILABLE)
                self.assertEqual(
                    output, f"status=unavailable operation_id={OPERATION_ID}\n"
                )
                self.assertNotIn("completed", output)
                self.assertEqual(len(coordinator.submissions), 1)
                self.assertEqual(frappe.db.commit_calls, 0)
                self.assertEqual(frappe.db.rollback_calls, 0)

    def test_definite_rejection_of_submitted_work_reports_refused(self):
        for status in ("rejected", "conflict"):
            with self.subTest(status=status):
                coordinator = FakeCoordinator(contracts.SaveOutcome(status, OPERATION_ID))
                code, output, _frappe = self.run_child(coordinator)

                self.assertEqual(code, process.EXIT_REFUSED)
                self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")
                self.assertEqual(len(coordinator.submissions), 1)

    def test_saved_outcome_for_another_operation_is_not_completion(self):
        other = "b" * 32
        coordinator = FakeCoordinator(contracts.SaveOutcome("saved", other))
        code, output, _frappe = self.run_child(coordinator)

        self.assertEqual(code, process.EXIT_UNAVAILABLE)
        self.assertEqual(output, f"status=unavailable operation_id={OPERATION_ID}\n")
        self.assertNotIn("completed", output)
        self.assertNotIn(other, output)

    def test_malformed_outcome_is_never_completion(self):
        for outcome in (
            None,
            object(),
            {"status": "saved", "operation_id": OPERATION_ID},
            "saved",
            types.SimpleNamespace(status="saved"),
        ):
            with self.subTest(outcome=outcome):
                coordinator = FakeCoordinator(outcome)
                code, output, _frappe = self.run_child(coordinator)

                self.assertEqual(code, process.EXIT_UNAVAILABLE)
                self.assertEqual(
                    output, f"status=unavailable operation_id={OPERATION_ID}\n"
                )
                self.assertNotIn("completed", output)

    def test_coordinator_failure_is_unavailable_and_is_not_retried(self):
        coordinator = FakeCoordinator(error=RuntimeError("payload-bearing detail"))
        code, output, frappe = self.run_child(coordinator)

        self.assertEqual(code, process.EXIT_UNAVAILABLE)
        self.assertEqual(output, f"status=unavailable operation_id={OPERATION_ID}\n")
        self.assertEqual(len(coordinator.submissions), 1)
        self.assertNotIn("payload-bearing detail", output)
        self.assertEqual(frappe.destroy_calls, 1)
        self.assertEqual(frappe.db.commit_calls, 0)
        self.assertEqual(frappe.db.rollback_calls, 0)


class LauncherAdapterTests(unittest.TestCase):
    def test_real_launcher_exposes_the_task_three_notification_adapter(self):
        self.assertEqual(launcher.__name__, submission.LAUNCHER_MODULE)
        self.assertEqual(submission.LAUNCHER_ENQUEUE, "enqueue_submission")
        self.assertIs(
            getattr(launcher, submission.LAUNCHER_ENQUEUE),
            launcher.enqueue_submission,
        )
        self.assertIsNot(launcher.enqueue_submission, launcher.launch_submission)

        enqueue_signature = inspect.signature(launcher.enqueue_submission)
        launch_signature = inspect.signature(launcher.launch_submission)
        for signature in (enqueue_signature, launch_signature):
            parameters = list(signature.parameters.values())
            self.assertEqual([parameter.name for parameter in parameters], ["operation_id"])
            self.assertEqual(parameters[0].annotation, str)
            self.assertEqual(signature.return_annotation, None)

    def test_launcher_import_is_inert(self):
        script = (
            "import sys; sys.modules['frappe'] = None; "
            "import clinic_photos.publication.launcher as launcher; "
            "print(launcher.__name__)"
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
        self.assertEqual(completed.stdout.strip(), submission.LAUNCHER_MODULE)
        self.assertEqual(completed.stderr, "")

    def test_enqueue_submission_queues_only_the_opaque_identity(self):
        with fake_runtime(conf={GATE: True}) as frappe:
            frappe.local.site = SITE
            launcher.enqueue_submission(OPERATION_ID)

        self.assertEqual(len(frappe.enqueue_calls), 1)
        call = frappe.enqueue_calls[0]
        self.assertEqual(
            call["method"], "clinic_photos.publication.launcher.launch_submission"
        )
        self.assertEqual(call["queue"], launcher.QUEUE)
        self.assertEqual(call["timeout"], launcher.LAUNCHER_JOB_TIMEOUT_SECONDS)
        self.assertEqual(call["kwargs"], {"operation_id": OPERATION_ID})
        self.assertFalse(call["enqueue_after_commit"])

    def test_enqueue_submission_refuses_untrusted_identity_or_gate(self):
        with fake_runtime(conf={GATE: True}) as frappe:
            with self.assertRaises(launcher.LauncherUnavailable):
                launcher.enqueue_submission("not-an-operation-id")
            with self.assertRaises(launcher.LauncherUnavailable):
                launcher.enqueue_submission("A" * 32)
            self.assertEqual(frappe.enqueue_calls, [])

        with fake_runtime(conf={GATE: False}) as frappe:
            with self.assertRaises(launcher.LauncherUnavailable):
                launcher.enqueue_submission(OPERATION_ID)
            self.assertEqual(frappe.enqueue_calls, [])

    def test_launch_uses_fixed_argv_environment_and_cwd(self):
        recorded = {}

        class FakeProcess:
            def __init__(self, argv, **kwargs):
                recorded["argv"] = argv
                recorded["kwargs"] = kwargs
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.returncode = 0

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):  # pragma: no cover - not reached
                raise AssertionError("terminate called")

            def kill(self):  # pragma: no cover - not reached
                raise AssertionError("kill called")

        with fake_runtime(conf={GATE: True}) as frappe:
            frappe.local.site = SITE
            output = io.StringIO()
            with mock.patch.object(launcher.subprocess, "Popen", FakeProcess):
                with contextlib.redirect_stdout(output):
                    launcher.launch_submission(OPERATION_ID)

        self.assertEqual(output.getvalue(), "")
        self.assertEqual(
            recorded["argv"],
            [
                launcher.PYTHON_EXECUTABLE,
                "-m",
                "clinic_photos.publication.process",
                "--site",
                SITE,
                "--operation-id",
                OPERATION_ID,
            ],
        )
        self.assertEqual(recorded["kwargs"]["cwd"], launcher.SITES_PATH)
        self.assertEqual(recorded["kwargs"]["env"], launcher.child_environment())
        self.assertFalse(recorded["kwargs"]["shell"])
        self.assertIs(recorded["kwargs"]["stdin"], subprocess.DEVNULL)
        for secret in (ACTOR, DRAFT, PHOTO, PATIENT, "annotation"):
            self.assertNotIn(secret, recorded["argv"])
            self.assertNotIn(secret, " ".join(recorded["kwargs"]["env"].values()))

    def test_launch_refuses_untrusted_site_or_gate_before_spawning(self):
        class ForbiddenPopen:
            def __init__(self, *args, **kwargs):  # pragma: no cover - must not run
                raise AssertionError("child started")

        with fake_runtime(conf={GATE: True}) as frappe:
            frappe.local.site = "other.example"
            with mock.patch.object(launcher.subprocess, "Popen", ForbiddenPopen):
                launcher.launch_submission(OPERATION_ID)

        with fake_runtime(conf={GATE: False}) as frappe:
            frappe.local.site = SITE
            with mock.patch.object(launcher.subprocess, "Popen", ForbiddenPopen):
                launcher.launch_submission(OPERATION_ID)

    def test_child_output_capture_is_bounded_and_timeout_is_unknown(self):
        exit_code, timed_out, stdout, stderr = launcher._execute_child(
            [
                sys.executable,
                "-c",
                "import sys; sys.stdout.write('x' * 200000); "
                "sys.stderr.write('y' * 200000)",
            ],
            cwd=str(APP_ROOT),
            deadline=30,
            grace=1,
        )
        self.assertEqual(exit_code, 0)
        self.assertFalse(timed_out)
        self.assertEqual(len(stdout), launcher.OUTPUT_LIMIT_BYTES)
        self.assertEqual(len(stderr), launcher.OUTPUT_LIMIT_BYTES)

        exit_code, timed_out, _stdout, _stderr = launcher._execute_child(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=str(APP_ROOT),
            deadline=0.2,
            grace=0.2,
        )
        self.assertIsNone(exit_code)
        self.assertTrue(timed_out)

    @staticmethod
    def _command_line(pid):
        try:
            completed = subprocess.run(
                ["ps", "-o", "command=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            return ""
        return completed.stdout

    def _kill_owned(self, pid, marker):
        """Kill one harness process only after proving its command line is ours."""
        if not pid or marker not in self._command_line(pid):
            return False
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.05)
        return False

    def _run_descendant_driver(self, *, parent_mode, marker):
        """Run ``_execute_child`` in a disposable driver with an independent watchdog.

        The driver lives in its own process so a blocked helper cannot hang the suite.
        The outer watchdog reports the driver's ``faulthandler`` stacks, which
        distinguishes a blocked buffered close from blocked pipe reads.
        """
        workspace = Path(tempfile.mkdtemp(prefix=marker + "-"))
        self.addCleanup(lambda: shutil.rmtree(workspace, ignore_errors=True))
        pidfile = workspace / "descendant.pid"
        direct_pidfile = workspace / "direct.pid"
        holder = workspace / "holder.py"
        parent = workspace / "parent.py"
        driver = workspace / "driver.py"
        holder.write_text(
            "import os, signal, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
            f"open({str(pidfile)!r}, 'w').write(str(os.getpid()))\n"
            "end = time.time() + 30\n"
            "while time.time() < end:\n"
            "    time.sleep(0.2)\n"
        )
        parent.write_text(
            "import os, subprocess, sys, time\n"
            f"subprocess.Popen([sys.executable, {str(holder)!r}])\n"
            f"open({str(direct_pidfile)!r}, 'w').write(str(os.getpid()))\n"
            + ("time.sleep(60)\n" if parent_mode == "sleep" else "sys.exit(0)\n")
        )
        driver.write_text(
            "import faulthandler, json, os, sys, time\n"
            f"sys.path.insert(0, {str(APP_ROOT)!r})\n"
            "from clinic_photos.publication import launcher\n"
            "faulthandler.dump_traceback_later(3, repeat=False)\n"
            "start = time.monotonic()\n"
            f"exit_code, timed_out, out, err = launcher._execute_child(\n"
            f"    [sys.executable, {str(parent)!r}],\n"
            f"    cwd={str(APP_ROOT)!r}, deadline=1.5, grace=0.5)\n"
            "elapsed = time.monotonic() - start\n"
            f"end = time.monotonic() + 2\n"
            f"while not os.path.exists({str(pidfile)!r}) and time.monotonic() < end:\n"
            "    time.sleep(0.05)\n"
            f"pid = int(open({str(pidfile)!r}).read())\n"
            "try:\n"
            "    os.kill(pid, 0)\n"
            "    alive = True\n"
            "except OSError:\n"
            "    alive = False\n"
            "print(json.dumps({'elapsed': elapsed, 'exit': exit_code,\n"
            "                  'timed_out': timed_out, 'descendant_alive': alive,\n"
            "                  'stdout_bytes': len(out), 'stderr_bytes': len(err)}))\n"
        )
        bound = 1.5 + (2 * 0.5) + 1.0
        try:
            completed = subprocess.run(
                [sys.executable, str(driver)],
                capture_output=True,
                text=True,
                timeout=bound + 1.5,
            )
        except subprocess.TimeoutExpired as expired:
            stderr = expired.stderr or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            self.fail(
                "helper did not return within the derived bound "
                f"{bound}s; watchdog stderr:\n{(stderr or '')[-4000:]}"
            )
        finally:
            for candidate in (direct_pidfile, pidfile):
                if candidate.exists():
                    try:
                        pid = int(candidate.read_text().strip())
                    except ValueError:
                        continue
                    self._kill_owned(pid, marker)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout.strip().splitlines()[-1]), bound

    def test_execute_child_returns_within_bound_while_descendant_holds_pipes(self):
        marker = f"task4-descendant-{os.getpid()}-{uuid.uuid4().hex}"
        result, bound = self._run_descendant_driver(parent_mode="sleep", marker=marker)
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit"])
        self.assertLessEqual(result["elapsed"], bound)
        self.assertTrue(result["descendant_alive"])
        self.assertGreaterEqual(result["stdout_bytes"], 0)

    def test_execute_child_returns_after_normal_exit_with_descendant_pipes(self):
        marker = f"task4-descendant-{os.getpid()}-{uuid.uuid4().hex}"
        result, bound = self._run_descendant_driver(parent_mode="exit", marker=marker)
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["exit"], 0)
        self.assertLessEqual(result["elapsed"], bound)
        self.assertTrue(result["descendant_alive"])

    def test_execute_child_kills_sigterm_resistant_child_within_grace(self):
        started = time.monotonic()
        exit_code, timed_out, stdout, stderr = launcher._execute_child(
            [
                sys.executable,
                "-c",
                "import signal, time; "
                "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
                "time.sleep(30)",
            ],
            cwd=str(APP_ROOT),
            deadline=0.5,
            grace=0.5,
        )
        elapsed = time.monotonic() - started
        self.assertTrue(timed_out)
        self.assertIsNone(exit_code)
        self.assertLessEqual(elapsed, 0.5 + (2 * 0.5) + 1.0)
        self.assertEqual(stdout, b"")
        self.assertEqual(stderr, b"")

    def test_execute_child_caps_sustained_stdout_and_stderr_independently(self):
        script = (
            "import sys, time\n"
            "for _ in range(24):\n"
            "    sys.stdout.write('x' * 8192)\n"
            "    sys.stdout.flush()\n"
            "    sys.stderr.write('y' * 8192)\n"
            "    sys.stderr.flush()\n"
            "    time.sleep(0.01)\n"
        )
        started = time.monotonic()
        exit_code, timed_out, stdout, stderr = launcher._execute_child(
            [sys.executable, "-c", script],
            cwd=str(APP_ROOT),
            deadline=10,
            grace=1,
        )
        elapsed = time.monotonic() - started
        self.assertEqual(exit_code, 0)
        self.assertFalse(timed_out)
        self.assertEqual(len(stdout), launcher.OUTPUT_LIMIT_BYTES)
        self.assertEqual(len(stderr), launcher.OUTPUT_LIMIT_BYTES)
        self.assertLess(elapsed, 5)

    def test_execute_child_leaves_no_reader_threads_or_descriptors(self):
        before_threads = threading.active_count()
        before_descriptors = len(os.listdir("/dev/fd"))
        for _ in range(5):
            exit_code, timed_out, _stdout, _stderr = launcher._execute_child(
                [sys.executable, "-c", "print('ok')"],
                cwd=str(APP_ROOT),
                deadline=10,
                grace=1,
            )
            self.assertEqual(exit_code, 0)
            self.assertFalse(timed_out)
        self.assertEqual(threading.active_count(), before_threads)
        self.assertEqual(len(os.listdir("/dev/fd")), before_descriptors)

    def test_execute_child_read_budget_yields_under_always_ready_pipes(self):
        """An always-ready descriptor must not starve poll/deadline or one stream.

        Only the syscall boundary is replaced: ``os.read`` returns nonempty bytes
        forever, the selector always reports both descriptors ready, and ``poll``
        reports exit after a few passes. A collector that loops until ``EAGAIN`` can
        never observe the deadline or the other stream; the outer watchdog turns that
        into a bounded failure instead of a hung suite.
        """
        workspace = Path(tempfile.mkdtemp(prefix=f"task4-ready-{os.getpid()}-"))
        self.addCleanup(lambda: shutil.rmtree(workspace, ignore_errors=True))
        driver = workspace / "driver.py"
        driver.write_text(
            "import faulthandler, json, os, sys, time, types\n"
            f"sys.path.insert(0, {str(APP_ROOT)!r})\n"
            "from unittest import mock\n"
            "from clinic_photos.publication import launcher\n"
            "calls = {'101': 0, '102': 0}\n"
            "class FakeStream:\n"
            "    def __init__(self, fd): self._fd = fd\n"
            "    def fileno(self): return self._fd\n"
            "    def close(self): pass\n"
            "class FakeProcess:\n"
            "    def __init__(self, *args, **kwargs):\n"
            "        self.stdout = FakeStream(101)\n"
            "        self.stderr = FakeStream(102)\n"
            "        self.returncode = 0\n"
            "        self.polls = 0\n"
            "    def poll(self):\n"
            "        self.polls += 1\n"
            "        return None if self.polls < 4 else 0\n"
            "    def wait(self, timeout=None): raise AssertionError('wait used')\n"
            "    def terminate(self): raise AssertionError('terminate used')\n"
            "    def kill(self): raise AssertionError('kill used')\n"
            "class FakeSelector:\n"
            "    def __init__(self): self.fds = []\n"
            "    def register(self, fd, mask): self.fds.append(fd)\n"
            "    def unregister(self, fd):\n"
            "        if fd in self.fds: self.fds.remove(fd)\n"
            "    def select(self, timeout=None):\n"
            "        return [(types.SimpleNamespace(fd=fd), 0) for fd in list(self.fds)]\n"
            "    def close(self): pass\n"
            "def fake_read(fd, size):\n"
            "    calls[str(fd)] += 1\n"
            "    return b'x' * size\n"
            "faulthandler.dump_traceback_later(2, repeat=False)\n"
            "start = time.monotonic()\n"
            "with mock.patch.object(launcher.subprocess, 'Popen', FakeProcess), \\\n"
            "     mock.patch.object(launcher.os, 'set_blocking', lambda fd, flag: None), \\\n"
            "     mock.patch.object(launcher.os, 'read', fake_read), \\\n"
            "     mock.patch.object(launcher.selectors, 'DefaultSelector', FakeSelector):\n"
            "    exit_code, timed_out, stdout, stderr = launcher._execute_child(\n"
            "        [sys.executable, 'unused'], deadline=5, grace=0.2)\n"
            "elapsed = time.monotonic() - start\n"
            "faulthandler.cancel_dump_traceback_later()\n"
            "print(json.dumps({'exit': exit_code, 'timed_out': timed_out,\n"
            "                  'elapsed': elapsed, 'calls': calls,\n"
            "                  'stdout_bytes': len(stdout), 'stderr_bytes': len(stderr)}))\n"
        )
        try:
            completed = subprocess.run(
                [sys.executable, str(driver)],
                capture_output=True,
                text=True,
                timeout=3.0,
            )
        except subprocess.TimeoutExpired as expired:
            stderr = expired.stderr or b""
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
            self.fail(
                "collector starved poll/deadline under always-ready reads; "
                f"watchdog stderr:\n{(stderr or '')[-4000:]}"
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(result["exit"], 0)
        self.assertFalse(result["timed_out"])
        self.assertLess(result["elapsed"], 2.0)
        self.assertGreaterEqual(result["calls"]["101"], 16)
        self.assertGreaterEqual(result["calls"]["102"], 16)
        self.assertLessEqual(result["calls"]["101"] + result["calls"]["102"], 16 * 2 * 6)
        self.assertEqual(result["stdout_bytes"], launcher.OUTPUT_LIMIT_BYTES)
        self.assertEqual(result["stderr_bytes"], launcher.OUTPUT_LIMIT_BYTES)


class RediscoveryTests(unittest.TestCase):
    def _rows(self, count=30, *, site=SITE, state="SUBMITTED", kind=None):
        return [
            {
                "name": f"{index:032x}",
                "site": site,
                "state": state,
                "creation": index,
                "kind": launcher.ANNOTATION_APPEND if kind is None else kind,
            }
            for index in range(1, count + 1)
        ]

    def _operations(self, count=30, **kwargs):
        return {"Clinic Photo Save Operation": self._rows(count, **kwargs)}

    def test_minute_rediscovery_reuses_cursor_wraps_and_expires_after_one_day(self):
        with fake_runtime(self._operations(), conf={GATE: True}) as frappe:
            frappe.local.site = SITE
            first = launcher.rediscover_submissions()
            first_ids = [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]
            self.assertEqual(first, 25)
            self.assertEqual(first_ids, [row["name"] for row in self._rows()[:25]])
            self.assertEqual(len(frappe.cache.set_calls), 1)
            key, cursor, ttl = frappe.cache.set_calls[0]
            self.assertIn(SITE, key)
            self.assertEqual(cursor, (25, self._rows()[24]["name"]))
            self.assertEqual(ttl, launcher.CURSOR_TTL_SECONDS)

            frappe.enqueue_calls.clear()
            second = launcher.rediscover_submissions()
            second_ids = [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]
            self.assertEqual(second, 5)
            self.assertEqual(second_ids, [row["name"] for row in self._rows()[25:]])
            self.assert_resets_cursor(frappe)

            frappe.enqueue_calls.clear()
            third = launcher.rediscover_submissions()
            self.assertEqual(third, 25)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls],
                first_ids,
            )

    def assert_resets_cursor(self, frappe):
        self.assertTrue(frappe.cache.delete_calls)
        self.assertIn(SITE, frappe.cache.delete_calls[-1])

    def test_only_submitted_rows_of_this_server_site_are_queued(self):
        rows = self._rows(3)
        rows += self._rows(2, state="PREPARED")
        rows += self._rows(2, state="PUBLISHED")
        rows += self._rows(2, state="ABORTED")
        rows += self._rows(2, state="QUARANTINED")
        rows += self._rows(2, site="another-site.local")
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            count = launcher.rediscover_submissions()

        queued = [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]
        self.assertEqual(count, 3)
        self.assertEqual(queued, [row["name"] for row in rows[:3]])
        for call in frappe.enqueue_calls:
            self.assertEqual(set(call["kwargs"]), {"operation_id"})
            self.assertRegex(call["kwargs"]["operation_id"], OPERATION_ID_PATTERN)

    def test_disabled_gate_queues_nothing_and_reads_no_cursor(self):
        with fake_runtime(self._operations(), conf={GATE: False}) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 0)

        self.assertEqual(frappe.enqueue_calls, [])
        self.assertEqual(frappe.cache.set_calls, [])

    def _malformed(self, index):
        # Valid kind, unusable identity: exercises the in-loop identity guard, which
        # only sees rows that already passed the metadata eligibility check.
        return {
            "name": f"LEGACY-{index:03d}",
            "site": SITE,
            "state": "SUBMITTED",
            "creation": index,
            "kind": launcher.ANNOTATION_APPEND,
        }

    def test_malformed_oldest_identity_is_skipped_without_starving_valid_work(self):
        rows = [self._malformed(0)] + self._rows(2)
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            first = launcher.rediscover_submissions()
            first_ids = [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]
            self.assertEqual(first, 2)
            self.assertEqual(first_ids, [rows[1]["name"], rows[2]["name"]])
            self.assertNotIn(rows[0]["name"], first_ids)

            frappe.enqueue_calls.clear()
            second = launcher.rediscover_submissions()
            self.assertEqual(second, 2)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls],
                first_ids,
            )

    def test_malformed_last_of_full_window_advances_to_next_page(self):
        rows = self._rows(24) + [self._malformed(25), self._rows(26)[25]]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            first = launcher.rediscover_submissions()
            first_ids = [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]
            self.assertEqual(first, 24)
            self.assertEqual(first_ids, [row["name"] for row in rows[:24]])
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertEqual(cursor, (25, rows[24]["name"]))

            frappe.enqueue_calls.clear()
            second = launcher.rediscover_submissions()
            self.assertEqual(second, 1)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls],
                [rows[25]["name"]],
            )

    def test_invalid_identity_before_queue_failure_keeps_failed_valid_row_retryable(self):
        rows = [self._rows(1)[0], self._malformed(2), self._rows(3)[2], self._rows(4)[3]]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            frappe.enqueue_fail_on_call = 2
            first = launcher.rediscover_submissions()

            self.assertEqual(first, 1)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls],
                [rows[0]["name"], rows[2]["name"]],
            )
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertEqual(cursor, (2, rows[1]["name"]))

            frappe.enqueue_fail_on_call = None
            frappe.enqueue_calls.clear()
            retried = launcher.rediscover_submissions()
            self.assertEqual(retried, 2)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls],
                [rows[2]["name"], rows[3]["name"]],
            )

    def test_all_invalid_page_advances_cursor(self):
        rows = [self._malformed(index) for index in range(1, 31)]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            first = launcher.rediscover_submissions()
            self.assertEqual(first, 0)
            self.assertEqual(frappe.enqueue_calls, [])
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertEqual(cursor, (25, rows[24]["name"]))

            second = launcher.rediscover_submissions()
            self.assertEqual(second, 0)
            self.assertEqual(frappe.enqueue_calls, [])
            self.assert_resets_cursor(frappe)

            third = launcher.rediscover_submissions()
            self.assertEqual(third, 0)
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertEqual(cursor, (25, rows[24]["name"]))

    def test_queue_outage_leaves_submitted_work_pending_and_cursor_retryable(self):
        with fake_runtime(self._operations(), conf={GATE: True}) as frappe:
            frappe.local.site = SITE
            frappe.enqueue_fail_on_call = 3
            count = launcher.rediscover_submissions()

            self.assertEqual(count, 2)
            self.assertEqual(len(frappe.enqueue_calls), 3)
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertEqual(cursor, (2, self._rows()[1]["name"]))
            self.assertTrue(
                all(
                    row["state"] == "SUBMITTED"
                    for row in frappe.db.rows["Clinic Photo Save Operation"]
                )
            )

            frappe.enqueue_fail_on_call = None
            frappe.enqueue_calls.clear()
            retried = launcher.rediscover_submissions()
            self.assertEqual(retried, 25)
            self.assertEqual(
                [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls][:1],
                [self._rows()[2]["name"]],
            )


class RediscoveryEligibilityTests(unittest.TestCase):
    """Rediscovery must only notify rows the journal can still verify.

    A legacy image-bearing row and an image-reference-less row both fail journal
    verification, so enqueueing them can only burn the same identity every minute and
    never publish. Eligibility is decided from metadata the scan already reads; the
    image payload is never retrieved.
    """

    IMAGE_SENTINEL = "aGVsbG8taW1hZ2UtcGF5bG9hZA=="

    def _row(self, index, **overrides):
        row = {
            "name": f"{index:032x}",
            "site": SITE,
            "state": "SUBMITTED",
            "creation": index,
            "kind": launcher.ANNOTATION_APPEND,
        }
        row.update(overrides)
        return row

    def _staged_row(self, index, **overrides):
        row = self._row(
            index,
            kind=launcher.SYNTHETIC_ORIGINAL_CREATE,
            staged_key=f"{index:032x}.png",
            staged_sha256=f"{index:064x}",
            staged_size=1024 + index,
            staged_format="PNG",
        )
        row.update(overrides)
        return row

    def _legacy_image_row(self, index, **overrides):
        """The pre-staging shape: image bytes base64 in the row, no staged reference."""
        row = self._row(index, kind=launcher.SYNTHETIC_ORIGINAL_CREATE)
        row["image_base64"] = self.IMAGE_SENTINEL
        row.update(overrides)
        return row

    def _reference_less_row(self, index, **overrides):
        row = self._row(index, kind=launcher.SYNTHETIC_ORIGINAL_CREATE)
        row.update(overrides)
        return row

    def _queued(self, frappe):
        return [call["kwargs"]["operation_id"] for call in frappe.enqueue_calls]

    def test_legacy_image_bearing_rows_are_never_notified(self):
        rows = [
            self._legacy_image_row(1),
            self._legacy_image_row(2),
            self._row(3),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            first = launcher.rediscover_submissions()
            self.assertEqual(first, 1)
            self.assertEqual(self._queued(frappe), [rows[2]["name"]])

            # A repeat tick must not resurrect the refused identities either.
            frappe.enqueue_calls.clear()
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[2]["name"]])

        for call in frappe.enqueue_calls:
            self.assertEqual(set(call["kwargs"]), {"operation_id"})
            self.assertNotIn(self.IMAGE_SENTINEL, str(call))

    def test_image_reference_less_synthetic_rows_are_never_notified(self):
        rows = [self._reference_less_row(1), self._staged_row(2)]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[1]["name"]])

            frappe.enqueue_calls.clear()
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[1]["name"]])

    def test_valid_staged_synthetic_rows_and_annotations_are_eligible(self):
        rows = [
            self._row(1),
            self._staged_row(2),
            self._staged_row(3, staged_key=f"{3:032x}.jpg", staged_format="JPEG"),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 3)
            self.assertEqual(self._queued(frappe), [row["name"] for row in rows])

    def test_malformed_or_unknown_kinds_fail_closed(self):
        rows = [
            self._row(1, kind="unknown_kind"),
            self._row(2, **{"kind": None}),
            self._staged_row(3, staged_key="not-a-key.png"),
            self._staged_row(4, staged_sha256="XYZ"),
            self._staged_row(5, staged_size=0),
            self._staged_row(6, staged_format="JPEG"),
            self._row(7, staged_key=f"{7:032x}.png"),
            self._row(8, kind=launcher.SYNTHETIC_ORIGINAL_CREATE, staged_key="k.png"),
            self._staged_row(9),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[8]["name"]])

    def test_excluded_rows_beyond_one_batch_do_not_starve_valid_work(self):
        legacy = [self._legacy_image_row(index) for index in range(1, 31)]
        valid = [self._row(31), self._staged_row(32)]
        with fake_runtime(
            {"Clinic Photo Save Operation": legacy + valid}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            with mock.patch.object(
                frappe.db, "get_values", wraps=frappe.db.get_values
            ) as scan:
                count = launcher.rediscover_submissions()

            self.assertEqual(count, 2)
            self.assertEqual(self._queued(frappe), [row["name"] for row in valid])
            # Bounded catch-up: the scan never examines more than its page budget.
            self.assertLessEqual(
                len(scan.call_args_list),
                launcher.SCAN_MAX_PAGES + 1 + launcher.SCAN_MAX_PAGES,
            )
            for call in scan.call_args_list:
                fieldnames = call.kwargs.get("fieldname")
                self.assertNotIn(
                    "image_base64",
                    fieldnames if isinstance(fieldnames, (list, tuple)) else [fieldnames],
                )
                self.assertNotIn("image_base64", call.kwargs.get("fields") or [])

    def test_annotation_rows_with_the_int_column_default_are_eligible(self):
        # An installed Int column returns 0 for a staged_size that was never set.
        rows = [
            self._row(1, staged_size=0),
            self._row(2, staged_size=None),
            self._row(3, staged_size=""),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 3)
            self.assertEqual(self._queued(frappe), [row["name"] for row in rows])

    def test_annotation_rows_with_staged_evidence_fail_closed(self):
        rows = [
            self._row(1, staged_key=f"{1:032x}.png"),
            self._row(2, staged_sha256="b" * 64),
            self._row(3, staged_format="PNG"),
            self._row(4, staged_size=1024),
            self._row(5, staged_size=True),
            self._row(6, staged_size=False),
            self._row(7),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[6]["name"]])

    def test_padded_or_empty_legacy_payloads_are_never_notified(self):
        # PAD SPACE collations make "   " = "" true, so a "!= ''" presence test can miss
        # a whitespace-only legacy payload. Any non-NULL value is treated as legacy.
        rows = [
            self._legacy_image_row(1, image_base64="   "),
            self._legacy_image_row(2, image_base64=""),
            self._legacy_image_row(3),
            # The contradictory shape: valid staged metadata plus a legacy payload.
            self._staged_row(4, image_base64="   "),
            self._staged_row(5, image_base64=""),
            self._row(6),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[5]["name"]])

            frappe.enqueue_calls.clear()
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[5]["name"]])

    def test_legacy_presence_uses_parameterised_sql_and_selects_only_names(self):
        rows = [
            self._legacy_image_row(1),
            self._legacy_image_row(2, image_base64="   "),
            self._row(3),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            launcher.rediscover_submissions()

            legacy = launcher._legacy_rows(
                frappe, [row["name"] for row in rows]
            )

        self.assertEqual(legacy, {rows[0]["name"], rows[1]["name"]})
        self.assertTrue(frappe.db.sql_calls)
        for query, values, as_dict in frappe.db.sql_calls:
            normalized = " ".join(query.split()).lower()
            self.assertTrue(as_dict)
            self.assertIn(
                "select `name` from `tabclinic photo save operation`", normalized
            )
            self.assertIn("`image_base64` is not null", normalized)
            self.assertIn("in (%s, %s, %s)", normalized)
            # Values are bound as parameters, never spliced into the statement.
            self.assertEqual(set(values), {row["name"] for row in rows})
            for name in values:
                self.assertNotIn(name, query)

    def test_previous_is_set_presence_would_miss_a_padded_payload(self):
        # Pinned Frappe 16: ("is", "set") on a Long Text compiles to `!= ''`, evaluated
        # under the PAD SPACE collation, so a whitespace-only payload is invisible.
        rows = [self._legacy_image_row(1, image_base64="   ")]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            via_filter = frappe.db.get_values(
                launcher.OPERATION_DOCTYPE,
                filters={
                    "name": ("in", [rows[0]["name"]]),
                    "image_base64": ("is", "set"),
                },
                fieldname="name",
                as_dict=True,
            )
            via_presence = launcher._legacy_rows(frappe, [rows[0]["name"]])

        self.assertEqual(via_filter, [])
        self.assertEqual(via_presence, {rows[0]["name"]})

    def test_legacy_presence_is_injection_safe_for_hostile_identities(self):
        hostile = "x' or '1'='1"
        rows = [
            self._legacy_image_row(1),
            self._legacy_image_row(2, name=hostile, image_base64=None),
            self._staged_row(3, name=f"{3:032x}", image_base64="   "),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            legacy = launcher._legacy_rows(frappe, [row["name"] for row in rows])

            # A hostile identity is bound, never interpolated, and never notified.
            self.assertEqual(legacy, {rows[0]["name"], rows[2]["name"]})
            for query, values, _as_dict in frappe.db.sql_calls:
                self.assertNotIn(hostile, query)
                self.assertIn(hostile, values)

            frappe.enqueue_calls.clear()
            self.assertEqual(launcher.rediscover_submissions(), 0)

        self.assertEqual(self._queued(frappe), [])

    def test_page_of_only_excluded_rows_still_advances_the_cursor(self):
        # More excluded rows than one scan budget: each tick must move forward.
        rows = [self._legacy_image_row(index) for index in range(1, 211)]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 0)
            self.assertEqual(frappe.enqueue_calls, [])
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            self.assertGreater(cursor[0], launcher.REDISCOVERY_BATCH)
            self.assertIn(
                cursor, [(row["creation"], row["name"]) for row in rows]
            )

            self.assertEqual(launcher.rediscover_submissions(), 0)
            self.assertEqual(frappe.enqueue_calls, [])
            # The following tick drains the remainder and wraps.
            self.assertTrue(frappe.cache.delete_calls)
            self.assertIn(SITE, frappe.cache.delete_calls[-1])

    def test_row_without_an_ordering_key_is_never_notified(self):
        # A raw-restored row with no usable (creation, name) position cannot be
        # described by the cursor: it is refused and the scan stops before it.
        valid = {
            "name": "1" * 32,
            "creation": 1,
            "kind": launcher.ANNOTATION_APPEND,
        }
        broken = {
            "name": "2" * 32,
            "creation": None,
            "kind": launcher.ANNOTATION_APPEND,
        }

        class StubDB:
            """One page: a valid row followed by a row with no ordering key."""

            def get_values(self, doctype, filters=None, fieldname=None, **_kwargs):
                return [valid, broken]

            def sql(self, query, values=(), as_dict=False, **_kwargs):
                return []

        stub = types.SimpleNamespace(db=StubDB())

        rows, more, position = launcher._scan_submitted(stub, SITE, None)

        self.assertEqual(rows, [valid])
        self.assertTrue(more)
        self.assertEqual(position, valid)

    def test_exhausted_excluded_window_wraps_without_notifying(self):
        rows = [self._legacy_image_row(index) for index in range(1, 31)]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            self.assertEqual(launcher.rediscover_submissions(), 0)
            self.assertEqual(frappe.enqueue_calls, [])
            self.assertTrue(frappe.cache.delete_calls)
            self.assertIn(SITE, frappe.cache.delete_calls[-1])

            self.assertEqual(launcher.rediscover_submissions(), 0)
            self.assertEqual(frappe.enqueue_calls, [])

    def test_successful_scan_past_a_failed_queue_call_keeps_rows_retryable(self):
        rows = [
            self._legacy_image_row(1),
            self._row(2),
            self._reference_less_row(3),
            self._row(4),
        ]
        with fake_runtime(
            {"Clinic Photo Save Operation": rows}, conf={GATE: True}
        ) as frappe:
            frappe.local.site = SITE
            frappe.enqueue_fail_on_call = 2
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[1]["name"], rows[3]["name"]])
            _key, cursor, _ttl = frappe.cache.set_calls[-1]
            # The cursor stops at the last successful notification, never past it.
            self.assertEqual(cursor, (2, rows[1]["name"]))

            frappe.enqueue_fail_on_call = None
            frappe.enqueue_calls.clear()
            self.assertEqual(launcher.rediscover_submissions(), 1)
            self.assertEqual(self._queued(frappe), [rows[3]["name"]])


if __name__ == "__main__":
    unittest.main()
