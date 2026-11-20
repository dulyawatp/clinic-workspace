"""Focused tests for operation/target exclusion and publication ownership.

The module under test is exercised at its public seams with real OS advisory locks on
a real per-test sites volume and real child processes:

* a duplicate launch refuses instead of taking over a surviving writer;
* the operation lock is taken before the target lock, and no lock name is a raw
  identifier;
* lock objects survive phase commits and are never unlinked, so the inode is stable
  across release;
* a parent that dies leaves no lock authority with its surviving child;
* authority is not a Python-side claim: forged, copied, pickled, reconstructed and
  stale tokens cannot authorize, and a live token re-verifies owner, PID, descriptor,
  path, directory tree and lock availability before every authorization;
* a live lock object or directory that is replaced, removed, hard-linked or closed
  refuses before any durable read, and a reused descriptor number is never closed
  again;
* an external unlock is detected by the independent probe without adopting the probe
  lock;
* ``PREPARED`` is refused at scope entry (before yield) and separately at the later
  locked read, with the two layers asserted apart;
* an ``original`` substitution and a malformed binding under the locks poison;
* a ``fork()`` without ``exec()`` child can neither authorize nor unlock its parent,
  and its inherited token stays refused after the parent releases and a fresh
  contender acquires.

Only the framework import boundary is faked: a narrow ``frappe`` module with the exact
signatures ``frappe_context`` calls (``db.get_value`` positional identity plus
``as_dict``/``for_update``). No locking, owner, registry or generation logic is faked.

Not proven here: installed Frappe/MariaDB, the request transaction, queue or scheduler
delivery, a coordinator that actually publishes, Linux/Docker flock or shared-volume
behaviour, or any multi-host guarantee. The independent probe is an additional loss
detector: "busy" proves *some* holder, not that this token holds the lock, and an
external unlock followed by a competitor, or a close/reopen onto the same inode, is
outside what portable ``flock`` can attribute.
"""

import contextlib
import copy
import fcntl
import os
import pickle
import queue
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from pathlib import Path
from unittest import mock

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from clinic_photos.publication import exclusion

SITE = "clinic-photo-accept.local"
ACTOR = "clinician@example.test"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
PATIENT = "SYNTHETIC-PATIENT-1"
OPERATION_ID = "0123456789abcdef0123456789abcdef"
TARGET = f"draft:{DRAFT}"
CHILD_TIMEOUT = 20.0


def _row(*, operation_id=OPERATION_ID, **overrides):
    row = {
        "name": operation_id,
        "site": SITE,
        "actor": ACTOR,
        "kind": "annotation_append",
        "request_key": "request-key-0001",
        "identity_digest": "0" * 64,
        "payload_digest": "1" * 64,
        "state": "SUBMITTED",
        "draft": DRAFT,
        "expected_version": 0,
        "original": PHOTO,
        "synthetic_patient": None,
        "annotation_json": '{"coordinate_space":"normalized","schema_version":1,"shapes":[]}',
        "image_base64": None,
        "reservation_key": None,
    }
    row.update(overrides)
    return row


class _FakeDB:
    """Strict stand-in for the only ``frappe.db`` call the seam makes."""

    def __init__(self, row):
        self.row = dict(row)
        self.reads = []
        self.commits = 0

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        as_dict=False,
        *,
        for_update=False,
    ):
        if doctype != "Clinic Photo Save Operation":
            raise AssertionError(f"unexpected doctype {doctype!r}")
        if filters != self.row["name"]:
            raise AssertionError(f"unexpected operation identity {filters!r}")
        self.reads.append(for_update)
        if as_dict:
            return {field: self.row.get(field) for field in fieldname}
        return self.row.get(fieldname)

    def commit(self):
        self.commits += 1


class _FakeFrappe(types.ModuleType):
    def __init__(self, row, sites_path):
        super().__init__("frappe")
        self.db = _FakeDB(row)
        self.local = types.SimpleNamespace(site=SITE, sites_path=str(sites_path))
        self.session = types.SimpleNamespace(user=ACTOR)
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})


@contextlib.contextmanager
def fake_runtime(row, sites_path):
    module = _FakeFrappe(row, sites_path)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous


@contextlib.contextmanager
def recording_flock():
    """Record every ``flock`` call against the real module attribute."""
    calls = []
    real_flock = fcntl.flock

    def recording(descriptor, operation):
        try:
            inode = os.fstat(descriptor).st_ino
        except OSError:
            inode = -1
        call = {"descriptor": descriptor, "operation": operation, "inode": inode}
        calls.append(call)
        try:
            return real_flock(descriptor, operation)
        except OSError as error:
            call["error"] = error.errno
            raise

    with mock.patch.object(exclusion.fcntl, "flock", recording):
        yield calls


CHILD_SOURCE = '''\
import os
import subprocess
import sys
import time
import types

sys.path.insert(0, {app_root!r})

OPERATION_ID = {operation_id!r}
TARGET = {target!r}
SITE = {site!r}
ROW = dict({row!r})

frappe = types.ModuleType("frappe")
frappe.local = types.SimpleNamespace(site=SITE, sites_path={sites!r})
frappe.session = types.SimpleNamespace(user={actor!r})
frappe.PermissionError = type("PermissionError", (Exception,), {{}})
frappe.ValidationError = type("ValidationError", (Exception,), {{}})


def get_value(
    doctype, filters=None, fieldname="name", as_dict=False, *, for_update=False
):
    if doctype != "Clinic Photo Save Operation":
        raise AssertionError("unexpected doctype")
    if filters != OPERATION_ID:
        raise AssertionError("unexpected operation identity")
    if as_dict:
        return {{field: ROW.get(field) for field in fieldname}}
    return ROW.get(fieldname)


frappe.db = types.SimpleNamespace(get_value=get_value)
sys.modules["frappe"] = frappe

from clinic_photos.publication import exclusion


def report(*parts):
    print(*parts, flush=True)


mode = sys.argv[1]

if mode == "hold":
    with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
        report("locked", owner.attempt_generation)
        sys.stdin.readline()
    report("released")
elif mode == "hold_then_die":
    with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
        survivor = subprocess.Popen(
            [sys.executable, "-c", {survivor_code!r}],
            stdin=subprocess.DEVNULL,
        )
        open({pidfile!r}, "w").write(str(survivor.pid))
        report("locked", owner.attempt_generation, survivor.pid)
        os._exit(9)
    report("unreachable")
elif mode == "try":
    try:
        with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
            report("acquired", owner.attempt_generation)
    except exclusion.ExclusionBusy:
        report("busy")
elif mode == "scope_entry":
    entered = False
    try:
        with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
            entered = True
            report("scope-acquired", owner.attempt_generation)
    except exclusion.ExclusionError as error:
        report("scope-refused", type(error).__name__, "entered=" + str(entered))
elif mode == "fork_inherit":
    read_fd = write_fd = -1
    child_pid = -1
    with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
        read_fd, write_fd = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(write_fd)
            try:
                exclusion.reread_locked_operation(owner)
                report("child-authorized")
            except exclusion.ExclusionError as error:
                report("child-refused", type(error).__name__)
            os.read(read_fd, 1)
            # Falling out of the body runs the inherited scope cleanup in the child.
        else:
            os.close(read_fd)
            open({pidfile!r}, "w").write(str(child_pid))
            report("parent-holding", child_pid)
            os.read(0, 1)
            os.write(write_fd, b"1")
            os.waitpid(child_pid, 0)
            report("parent-authorizing")
            owner.require_bindings(actor={actor!r}, site=SITE, target_identity=TARGET)
            report("parent-authorized")
    if child_pid == 0:
        report("child-cleanup-done")
        os._exit(0)
    report("parent-scope-exited")
elif mode == "fork_survivor":
    read_fd = write_fd = -1
    child_pid = -1
    with exclusion.hold_exclusion(OPERATION_ID, TARGET) as owner:
        read_fd, write_fd = os.pipe()
        child_pid = os.fork()
        if child_pid == 0:
            os.close(write_fd)
            report("child-forked", os.getpid())
            os.read(read_fd, 1)
            try:
                exclusion.reread_locked_operation(owner)
                report("child-authorized")
            except exclusion.ExclusionError as error:
                report("child-refused", type(error).__name__)
            os._exit(0)
        os.close(read_fd)
        open({pidfile!r}, "w").write(str(child_pid))
        report("parent-holding", child_pid)
        # Leaving the body releases; the forked child stays blocked, not exiting.
    report("parent-released")
    os.read(0, 1)
    os.write(write_fd, b"1")
    os.waitpid(child_pid, 0)
    report("driver-exited")
elif mode not in ("hold", "hold_then_die", "try", "scope_entry", "fork_inherit"):
    raise SystemExit("unknown mode")
'''


class _Child:
    """A child process whose stdout/stderr are read through event-coordinated queues."""

    def __init__(self, argv, *, marker):
        self.marker = marker
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=self._environment(),
        )
        self.lines = queue.Queue()
        self.errors = []
        self.stdout_done = threading.Event()
        self.stderr_done = threading.Event()
        threading.Thread(target=self._pump_stdout, daemon=True).start()
        threading.Thread(target=self._pump_stderr, daemon=True).start()

    @staticmethod
    def _environment():
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        environment["PYTHONUNBUFFERED"] = "1"
        return environment

    def _pump_stdout(self):
        try:
            for line in self.process.stdout:
                self.lines.put(line.rstrip("\n"))
        finally:
            self.stdout_done.set()
            self.lines.put(None)

    def _pump_stderr(self):
        try:
            for line in self.process.stderr:
                self.errors.append(line.rstrip("\n"))
        finally:
            self.stderr_done.set()

    def read(self, *, timeout=CHILD_TIMEOUT):
        try:
            line = self.lines.get(timeout=timeout)
        except queue.Empty:
            self.close()
            raise AssertionError(
                f"child {self.marker!r} did not report within {timeout}s; "
                f"stderr={self.stderr_text()!r}"
            ) from None
        if line is None:
            raise AssertionError(
                f"child {self.marker!r} exited before reporting; "
                f"exit={self.process.poll()!r} stderr={self.stderr_text()!r}"
            )
        return line

    def collect(self, count, *, timeout=CHILD_TIMEOUT):
        return [self.read(timeout=timeout) for _ in range(count)]

    def stderr_text(self):
        return "\n".join(self.errors)

    def send(self, line):
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()

    def wait_exit(self, *, timeout=CHILD_TIMEOUT):
        try:
            return self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.close()
            raise AssertionError(
                f"child {self.marker!r} did not exit within {timeout}s; "
                f"stderr={self.stderr_text()!r}"
            ) from None

    def close(self):
        process = self.process
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        # A pump thread still blocked in ``readline`` holds the stream lock; closing the
        # stream here would deadlock the suite. Those threads are daemons and the streams
        # are left to the interpreter once the child's last writer exits.
        for stream, done in (
            (process.stdout, self.stdout_done),
            (process.stderr, self.stderr_done),
        ):
            if stream is not None and done.is_set():
                try:
                    stream.close()
                except OSError:
                    pass


class ExclusionTests(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="task5-exclusion-"))
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.sites = self.workspace / "sites"
        self.sites.mkdir()
        self.marker = f"task5-exclusion-{os.getpid()}-{uuid.uuid4().hex}"
        # A distinct operation per test keeps the local attempt generation at 1 for a
        # first attempt without weakening its ordering.
        self.operation_id = uuid.uuid4().hex
        self.addCleanup(self._cleanup_owned_pids)

    # -- helpers ---------------------------------------------------------
    def _survivor_code(self):
        marker = self.workspace / "survivor.marker"
        return (
            "import os, time\n"
            f"# {self.marker}\n"
            f"marker = {str(marker)!r}\n"
            "deadline = time.time() + 120\n"
            "while not os.path.exists(marker) and time.time() < deadline:\n"
            "    time.sleep(0.02)\n"
        )

    def _row(self, **overrides):
        return _row(operation_id=self.operation_id, **overrides)

    def _hold(self, *, row=None):
        return fake_runtime(row or self._row(), self.sites)

    def _start(self, mode, *, row=None):
        script = self.workspace / f"{self.marker}-{uuid.uuid4().hex}.py"
        script.write_text(
            CHILD_SOURCE.format(
                app_root=str(APP_ROOT),
                operation_id=self.operation_id,
                target=TARGET,
                site=SITE,
                actor=ACTOR,
                row=row or self._row(),
                sites=str(self.sites),
                survivor_code=self._survivor_code(),
                pidfile=str(self.workspace / f"{self.marker}-child.pid"),
            )
        )
        child = _Child([sys.executable, str(script), mode], marker=mode)
        self.addCleanup(child.close)
        return child

    def _site_dir(self):
        root = self.sites / exclusion.LOCK_ROOT_NAME
        entries = [entry for entry in root.iterdir() if entry.is_dir()]
        self.assertEqual(len(entries), 1)
        return entries[0]

    def _probe(self, *, row):
        """One shot contender that reports whether it could acquire exclusion."""
        child = self._start("try", row=row)
        result = child.read()
        child.close()
        return result

    def _original_descriptors(self, calls):
        acquired = [
            call
            for call in calls
            if call["operation"] != fcntl.LOCK_UN and "error" not in call
        ]
        self.assertGreaterEqual(len(acquired), 2)
        return acquired[0]["descriptor"], acquired[1]["descriptor"]

    def _cleanup_owned_pids(self):
        """Kill only PID files this test wrote inside its own workspace."""
        for path in sorted(self.workspace.glob("*.pid")):
            try:
                pid = int(path.read_text().strip())
            except (OSError, ValueError):
                continue
            self._kill_owned(pid, self.marker)

    def _kill_owned(self, pid, marker):
        """Kill only a child we started, after verifying its command line."""
        completed = subprocess.run(
            ["ps", "-o", "command=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if marker not in completed.stdout:
            return False
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            return False
        return True

    def _wait_gone(self, pid, *, timeout=CHILD_TIMEOUT):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.02)
        return False

    # -- inert import and layout ----------------------------------------
    def test_exclusion_import_is_inert_and_creates_no_lock_root(self):
        script = (
            "import sys; sys.modules['frappe'] = None; "
            "import clinic_photos.publication.exclusion as exclusion; "
            "print(exclusion.__name__)"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(self.sites),
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(
            completed.stdout.strip(), "clinic_photos.publication.exclusion"
        )
        self.assertEqual(completed.stderr, "")
        self.assertEqual(sorted(os.listdir(self.sites)), [])

    def test_operation_lock_precedes_target_lock_and_names_are_hashed(self):
        with self._hold() as frappe:
            with recording_flock() as calls:
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass
            self.assertEqual(frappe.db.reads, [False, True])

        acquired = [call for call in calls if call["operation"] != fcntl.LOCK_UN]
        site_dir = self._site_dir()
        operation_path, target_path = exclusion._lock_paths(
            site_dir, self.operation_id, TARGET
        )
        self.assertEqual(
            [call["inode"] for call in acquired],
            [os.stat(operation_path).st_ino, os.stat(target_path).st_ino],
        )
        self.assertEqual(
            {call["inode"] for call in calls}, {call["inode"] for call in acquired}
        )

        names = [os.path.basename(operation_path), os.path.basename(target_path)]
        self.assertEqual(
            sorted(entry.name for entry in site_dir.iterdir()), sorted(names)
        )
        for name in names:
            self.assertNotIn(self.operation_id, name)
            self.assertNotIn(DRAFT, name)
            self.assertNotIn("draft:", name)
            self.assertEqual(len(name), 64)
            info = (site_dir / name).stat()
            self.assertTrue(stat.S_ISREG(info.st_mode))
            self.assertEqual(stat.S_IMODE(info.st_mode) & 0o077, 0)
        root = self.sites / exclusion.LOCK_ROOT_NAME
        self.assertEqual(stat.S_IMODE(root.stat().st_mode) & 0o077, 0)

    # -- cross-process exclusion ----------------------------------------
    def test_duplicate_launch_cannot_take_over_surviving_writer(self):
        holder = self._start("hold")
        self.assertTrue(holder.read().startswith("locked "))
        self.assertIsNone(holder.process.poll())

        contender = self._start("try")
        self.assertEqual(contender.read(), "busy")
        self.assertEqual(contender.wait_exit(), 0)
        self.assertIsNone(holder.process.poll())

        holder.send("stop")
        self.assertEqual(holder.read(), "released")
        self.assertEqual(holder.wait_exit(), 0)

        after = self._start("try")
        self.assertTrue(after.read().startswith("acquired "))
        self.assertEqual(after.wait_exit(), 0)

    def test_scope_refuses_prepared_before_yield_without_creating_locks(self):
        entered = False
        with self._hold(row=self._row(state="PREPARED", reservation_key="reserved")):
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    entered = True
        self.assertFalse(entered)
        self.assertFalse((self.sites / exclusion.LOCK_ROOT_NAME).exists())

        # The same refusal in a separate process, reporting the entry layer explicitly.
        child = self._start(
            "scope_entry", row=self._row(state="PREPARED", reservation_key="reserved")
        )
        self.assertEqual(
            child.read(), "scope-refused ExclusionNoRetry entered=False"
        )
        self.assertEqual(child.wait_exit(), 0)
        self.assertFalse((self.sites / exclusion.LOCK_ROOT_NAME).exists())

    def test_locked_reread_refuses_prepared_after_entry(self):
        with self._hold() as frappe:
            with exclusion.hold_exclusion(self.operation_id, TARGET) as owner:
                frappe.db.row["state"] = "PREPARED"
                with self.assertRaises(exclusion.ExclusionDenied):
                    exclusion.reread_locked_operation(owner)

                # A definite durable state is a refusal, not uncertainty: the token is
                # not poisoned, but its authority is spent permanently.
                self.assertFalse(owner.poisoned)
                consumers = (
                    lambda: owner.require_bindings(
                        actor=ACTOR, site=SITE, target_identity=TARGET
                    ),
                    lambda: owner.require_attempt_generation(1),
                    owner.advance_attempt_generation,
                    lambda: exclusion.reread_locked_operation(owner),
                )
                for consumer in consumers:
                    with self.assertRaises(exclusion.ExclusionNoRetry):
                        consumer()

                # Restoring the row to SUBMITTED cannot revive the spent token.
                frappe.db.row["state"] = "SUBMITTED"
                for consumer in consumers:
                    with self.assertRaises(exclusion.ExclusionNoRetry):
                        consumer()
                self.assertFalse(owner.poisoned)

        # The scope still released its OS locks; only the token's authority is spent.
        self.assertTrue(self._probe(row=self._row()).startswith("acquired "))

    def test_unknown_durable_state_is_malformed_not_a_definite_refusal(self):
        entered = False
        with self._hold(row=self._row(state="ARCHIVED")):
            with self.assertRaises(exclusion.ExclusionDenied) as caught:
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    entered = True
        self.assertIs(type(caught.exception), exclusion.ExclusionDenied)
        self.assertFalse(entered)
        self.assertFalse((self.sites / exclusion.LOCK_ROOT_NAME).exists())

        with self._hold() as frappe:
            with exclusion.hold_exclusion(self.operation_id, TARGET) as owner:
                frappe.db.row["state"] = "ARCHIVED"
                with self.assertRaises(exclusion.ExclusionDenied) as caught:
                    exclusion.reread_locked_operation(owner)
                self.assertIs(type(caught.exception), exclusion.ExclusionDenied)
                # Unlike a known refusal, a malformed state is lost certainty.
                self.assertTrue(owner.poisoned)
                with self.assertRaises(exclusion.ExclusionPoisoned):
                    owner.require_bindings(
                        actor=ACTOR, site=SITE, target_identity=TARGET
                    )

    def test_lock_objects_survive_phase_commits_and_are_never_unlinked(self):
        with self._hold() as frappe:
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as owner:
                site_dir = self._site_dir()
                before = {
                    entry.name: entry.stat().st_ino for entry in site_dir.iterdir()
                }
                self.assertEqual(len(before), 2)

                for _ in range(3):
                    frappe.db.commit()
                    self.assertEqual(self._probe(row=self._row()), "busy")

                after_commit = {
                    entry.name: entry.stat().st_ino for entry in site_dir.iterdir()
                }
                self.assertEqual(before, after_commit)
                self.assertEqual(owner.attempt_generation, 1)

            after_release = {
                entry.name: entry.stat().st_ino for entry in site_dir.iterdir()
            }
            self.assertEqual(before, after_release)

            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as reacquired:
                self.assertEqual(reacquired.attempt_generation, 2)
                self.assertEqual(
                    {entry.name: entry.stat().st_ino for entry in site_dir.iterdir()},
                    before,
                )
            self.assertEqual(
                {entry.name: entry.stat().st_ino for entry in site_dir.iterdir()},
                before,
            )

    def test_parent_death_leaves_no_lock_authority_with_surviving_child(self):
        holder = self._start("hold_then_die")
        reported = holder.read().split()
        self.assertEqual(reported[0], "locked")
        survivor_pid = int(reported[2])
        self.addCleanup(self._kill_owned, survivor_pid, self.marker)

        self.assertEqual(holder.wait_exit(), 9)
        try:
            os.kill(survivor_pid, 0)
            survivor_alive = True
        except OSError:
            survivor_alive = False
        self.assertTrue(survivor_alive, "surviving child exited before the probe")

        # The exec'd survivor holds no descriptor of its own; this is an exec-path case,
        # not evidence that close_fds proves O_CLOEXEC.
        self.assertTrue(self._probe(row=self._row()).startswith("acquired "))

        (self.workspace / "survivor.marker").write_text("stop")
        self.assertTrue(self._wait_gone(survivor_pid))

    # -- token identity -------------------------------------------------
    def test_public_construction_copy_pickle_and_reconstruction_cannot_authorize(self):
        with self.assertRaises(TypeError):
            exclusion.LockedOwner(
                operation_id=self.operation_id,
                site=SITE,
                actor=ACTOR,
                kind="annotation_append",
                target_identity=TARGET,
                attempt_generation=99,
            )

        with self._hold():
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as owner:
                for copier in (copy.copy, copy.deepcopy):
                    with self.assertRaises(TypeError):
                        copier(owner)
                with self.assertRaises((TypeError, pickle.PicklingError)):
                    pickle.dumps(owner)

                reconstructed = object.__new__(exclusion.LockedOwner)
                with self.assertRaises(exclusion.ExclusionDenied):
                    exclusion.reread_locked_operation(reconstructed)

                # The forgery attempts changed nothing for the real token.
                owner.require_bindings(
                    actor=ACTOR, site=SITE, target_identity=TARGET
                )

    def test_owner_replay_substitution_and_completion_poison_irreversibly(self):
        with self._hold():
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as owner:
                self.assertEqual(owner.operation_id, self.operation_id)
                self.assertEqual(owner.site, SITE)
                self.assertEqual(owner.actor, ACTOR)
                self.assertEqual(owner.kind, "annotation_append")
                self.assertEqual(owner.target_identity, TARGET)
                self.assertEqual(owner.original, PHOTO)
                state = exclusion.reread_locked_operation(owner)
                self.assertEqual(state.state, "SUBMITTED")
                self.assertTrue(state.fresh_attempt_permitted)
                self.assertEqual(state.attempt_generation, 1)

                first = owner.advance_attempt_generation()
                self.assertEqual(first, 2)
                owner.require_attempt_generation(2)
                with self.assertRaises(exclusion.ExclusionDenied):
                    owner.require_attempt_generation(1)
                with self.assertRaises(exclusion.ExclusionPoisoned):
                    owner.require_attempt_generation(2)

        with self.assertRaises(exclusion.ExclusionLost):
            owner.advance_attempt_generation()

        with self.assertRaises(
            (exclusion.ExclusionPoisoned, exclusion.ExclusionLost)
        ):
            exclusion.reread_locked_operation(owner)

        # A substituted persisted actor poisons a fresh owner and refuses the row.
        with self._hold(row=self._row(actor="other@example.test")):
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as replacement:
                self.assertEqual(replacement.actor, "other@example.test")
                with self.assertRaises(exclusion.ExclusionDenied):
                    replacement.require_bindings(
                        actor=ACTOR, site=SITE, target_identity=TARGET
                    )
                with self.assertRaises(exclusion.ExclusionPoisoned):
                    replacement.require_bindings(
                        actor="other@example.test",
                        site=SITE,
                        target_identity=TARGET,
                    )

    def test_original_substitution_and_malformed_reread_poison(self):
        with self._hold() as frappe:
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as owner:
                self.assertEqual(owner.original, PHOTO)
                frappe.db.row["original"] = "PHOTO-9-OTHER"
                with self.assertRaises(exclusion.ExclusionDenied):
                    exclusion.reread_locked_operation(owner)
                self.assertTrue(owner.poisoned)
                with self.assertRaises(exclusion.ExclusionPoisoned):
                    owner.require_bindings(
                        actor=ACTOR, site=SITE, target_identity=TARGET
                    )

        with self._hold() as frappe:
            with exclusion.hold_exclusion(
                self.operation_id, TARGET
            ) as owner:
                frappe.db.row["synthetic_patient"] = 7
                with self.assertRaises(exclusion.ExclusionDenied):
                    exclusion.reread_locked_operation(owner)
                self.assertTrue(owner.poisoned)

    # -- live object and tree identity ----------------------------------
    def test_authorization_refuses_replaced_missing_linked_or_closed_objects(self):
        with self._hold() as frappe:
            for mutation in (
                "replaced",
                "missing",
                "linked",
                "closed",
                "directory-replaced",
            ):
                with self.subTest(mutation=mutation):
                    cleanup = []
                    try:
                        with recording_flock() as calls:
                            # The scope exit cannot confirm the object either, so the
                            # release refusal is part of the observed behaviour.
                            with self.assertRaises(exclusion.ExclusionLost):
                                with exclusion.hold_exclusion(
                                    self.operation_id, TARGET
                                ) as owner:
                                    site_dir = self._site_dir()
                                    _operation_path, target_path = (
                                        Path(path)
                                        for path in exclusion._lock_paths(
                                            site_dir, self.operation_id, TARGET
                                        )
                                    )
                                    target_fd = self._original_descriptors(calls)[1]
                                    reads = len(frappe.db.reads)
                                    if mutation == "replaced":
                                        target_path.unlink()
                                        target_path.touch(mode=0o600)
                                    elif mutation == "missing":
                                        target_path.unlink()
                                    elif mutation == "linked":
                                        linked = (
                                            self.workspace / f"link-{uuid.uuid4().hex}"
                                        )
                                        os.link(target_path, linked)
                                        cleanup.append(linked)
                                    elif mutation == "closed":
                                        os.close(target_fd)
                                    else:
                                        aside = (
                                            self.workspace / f"aside-{uuid.uuid4().hex}"
                                        )
                                        os.rename(site_dir, aside)
                                        site_dir.mkdir(mode=0o700)
                                        cleanup.append(aside)

                                    with self.assertRaises(exclusion.ExclusionLost):
                                        owner.require_bindings(
                                            actor=ACTOR,
                                            site=SITE,
                                            target_identity=TARGET,
                                        )
                                    self.assertTrue(owner.poisoned)
                                    # Authorization refused before any durable read.
                                    self.assertEqual(len(frappe.db.reads), reads)
                    finally:
                        for path in cleanup:
                            if path.is_dir():
                                shutil.rmtree(path, ignore_errors=True)
                            else:
                                path.unlink(missing_ok=True)

    def test_closed_descriptor_with_a_reused_number_is_not_closed_again(self):
        replacement = self.workspace / "reused-descriptor.bin"
        replacement.write_text("unrelated bytes")
        reused = None
        with self._hold():
            with self.assertRaises(exclusion.ExclusionLost):
                with recording_flock() as calls:
                    with exclusion.hold_exclusion(
                        self.operation_id, TARGET
                    ) as owner:
                        target_fd = self._original_descriptors(calls)[1]
                        os.close(target_fd)
                        descriptor = os.open(replacement, os.O_RDWR)
                        if descriptor != target_fd:
                            os.dup2(descriptor, target_fd)
                            os.close(descriptor)
                        reused = target_fd
                        with self.assertRaises(exclusion.ExclusionLost):
                            owner.require_bindings(
                                actor=ACTOR, site=SITE, target_identity=TARGET
                            )
        self.assertIsNotNone(reused)
        # The scope's release must not close a descriptor it no longer owns.
        info = os.fstat(reused)
        self.assertEqual(info.st_ino, os.stat(replacement).st_ino)
        os.close(reused)

    def test_external_unlock_is_detected_without_adopting_the_probe_lock(self):
        with self._hold() as frappe:
            with recording_flock() as calls:
                with exclusion.hold_exclusion(
                    self.operation_id, TARGET
                ) as owner:
                    operation_fd = self._original_descriptors(calls)[0]
                    reads = len(frappe.db.reads)
                    # Trusted protocol violation: an external unlock on the same
                    # descriptor. The probe must notice and never adopt.
                    fcntl.flock(operation_fd, fcntl.LOCK_UN)
                    with self.assertRaises(exclusion.ExclusionLost):
                        owner.require_bindings(
                            actor=ACTOR, site=SITE, target_identity=TARGET
                        )
                    self.assertTrue(owner.poisoned)
                    self.assertEqual(len(frappe.db.reads), reads)
            self.assertTrue(
                self._probe(row=self._row()).startswith("acquired "),
                "the probe lock must have been closed, not adopted",
            )

    # -- untrusted input -------------------------------------------------
    def test_hold_exclusion_rejects_untrusted_identity_site_and_target(self):
        with self._hold() as frappe:
            for operation_id in (
                "",
                "0123456789ABCDEF0123456789ABCDEF",
                "0123456789abcdef0123456789abcde",
                "0123456789abcdef0123456789abcdef0",
                "../" + self.operation_id,
                self.operation_id + "\x00",
            ):
                with self.subTest(operation_id=operation_id):
                    with self.assertRaises(exclusion.ExclusionDenied):
                        with exclusion.hold_exclusion(operation_id, TARGET):
                            pass

            for target in ("", "draft:", f"patient:{PATIENT}", f"draft: {DRAFT}"):
                with self.subTest(target=target):
                    with self.assertRaises(exclusion.ExclusionDenied):
                        with exclusion.hold_exclusion(self.operation_id, target):
                            pass

            self.assertEqual(frappe.db.reads, [])
            self.assertFalse((self.sites / exclusion.LOCK_ROOT_NAME).exists())

            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(
                    self.operation_id, f"draft:{PHOTO}"
                ):
                    pass
            self.assertNotIn(True, frappe.db.reads)
            self.assertFalse((self.sites / exclusion.LOCK_ROOT_NAME).exists())

    def test_server_bound_site_and_kind_are_never_replaced_by_the_caller(self):
        synthetic = self._row(
            kind="synthetic_original_create",
            draft=None,
            original=None,
            expected_version=None,
            synthetic_patient=PATIENT,
            annotation_json=None,
            image_base64=None,
        )

        with self._hold(row=self._row(site="other.example")):
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass

        with self._hold(row=synthetic):
            self.assertEqual(
                exclusion.target_identity_for_operation(self.operation_id),
                f"patient-creation:{PATIENT}",
            )
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass
            with exclusion.hold_exclusion(
                self.operation_id, f"patient-creation:{PATIENT}"
            ) as owner:
                self.assertEqual(owner.kind, "synthetic_original_create")
                self.assertIsNone(owner.draft)
                self.assertEqual(owner.synthetic_patient, PATIENT)

        # Symlinked lock root: never followed.
        root = self.sites / exclusion.LOCK_ROOT_NAME
        shutil.rmtree(root)
        outside = self.workspace / "outside"
        outside.mkdir()
        os.symlink(outside, root)
        with self._hold():
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass
        self.assertEqual(sorted(entry.name for entry in outside.iterdir()), [])
        os.unlink(root)

    def test_symlinked_or_hardlinked_lock_objects_are_refused(self):
        with self._hold():
            with exclusion.hold_exclusion(self.operation_id, TARGET):
                pass
            site_dir = self._site_dir()
            operation_path, _target_path = exclusion._lock_paths(
                site_dir, self.operation_id, TARGET
            )

            os.unlink(operation_path)
            os.symlink(self.workspace / "elsewhere", operation_path)
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass

            os.unlink(operation_path)
            linked = self.workspace / "linked.bin"
            linked.touch(mode=0o600)
            os.link(linked, operation_path)
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass

    def test_body_exception_and_unsafe_objects_never_retain_silent_authority(self):
        with self._hold() as frappe:
            owner = None
            with self.assertRaises(RuntimeError):
                with exclusion.hold_exclusion(
                    self.operation_id, TARGET
                ) as owner:
                    raise RuntimeError("phase failed")
            self.assertIsNotNone(owner)
            with self.assertRaises(exclusion.ExclusionLost):
                owner.require_attempt_generation(owner.attempt_generation)
            # The lock was released by the scope's finally, not by a callback.
            self.assertTrue(self._probe(row=self._row()).startswith("acquired "))

            site_dir = self._site_dir()
            for entry in site_dir.iterdir():
                entry.chmod(0o666)
            with self.assertRaises(exclusion.ExclusionDenied):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    pass
            for entry in site_dir.iterdir():
                entry.chmod(0o600)

            with self.assertRaises(exclusion.ExclusionDenied):
                with mock.patch.object(
                    exclusion.os, "geteuid", return_value=os.geteuid() + 1
                ):
                    with exclusion.hold_exclusion(self.operation_id, TARGET):
                        pass

    def test_release_verifies_entry_identity_and_refuses_lost_certainty(self):
        with self._hold():
            with self.assertRaises(exclusion.ExclusionLost):
                with exclusion.hold_exclusion(self.operation_id, TARGET):
                    site_dir = self._site_dir()
                    for entry in site_dir.iterdir():
                        entry.unlink()
                        entry.touch(mode=0o600)
        site_dir = self._site_dir()
        self.assertEqual(len(list(site_dir.iterdir())), 2)

    # -- fork without exec ----------------------------------------------
    def test_fork_child_cannot_authorize_or_unlock_its_parent(self):
        driver = self._start("fork_inherit")
        holding = driver.read().split()
        self.assertEqual(holding[0], "parent-holding")
        child_pid = int(holding[1])
        self.addCleanup(self._kill_owned, child_pid, self.marker)

        self.assertEqual(driver.read(), "child-refused ExclusionLost")
        driver.send("go")
        observed = set(driver.collect(4))
        self.assertEqual(
            observed,
            {
                "parent-authorizing",
                "parent-authorized",
                "child-cleanup-done",
                "parent-scope-exited",
            },
        )
        self.assertNotIn("child-authorized", observed)
        self.assertEqual(driver.wait_exit(), 0)

    def test_fork_child_token_stays_refused_after_parent_release(self):
        driver = self._start("fork_survivor")
        opening = {
            parts[0]: parts for parts in (line.split() for line in driver.collect(3))
        }
        self.assertEqual(
            sorted(opening), ["child-forked", "parent-holding", "parent-released"]
        )
        child_pid = int(opening["child-forked"][1])
        self.assertEqual(int(opening["parent-holding"][1]), child_pid)
        self.addCleanup(self._kill_owned, child_pid, self.marker)

        # A fresh process legitimately acquires while the forked child still runs.
        self.assertTrue(self._probe(row=self._row()).startswith("acquired "))

        driver.send("go")
        observed = set(driver.collect(2))
        self.assertEqual(observed, {"child-refused ExclusionLost", "driver-exited"})
        self.assertNotIn("child-authorized", observed)
        self.assertEqual(driver.wait_exit(), 0)


if __name__ == "__main__":
    unittest.main()
