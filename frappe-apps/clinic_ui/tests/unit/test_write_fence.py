"""Unit tests for the fail-closed write-fence ledger.

Everything runs against a private directory created by
``tempfile.TemporaryDirectory``: no site, Docker, database or runtime path is
touched. Interprocess behaviour is exercised with real child processes so the
``flock`` ownership and crash-release semantics are proved, not simulated.
"""

import errno
import os
import select
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from clinic_ui.core import write_fence
from clinic_ui.core.write_fence import (
    Anchor,
    EnumerationOverflow,
    FenceReleased,
    InvalidOperation,
    Ledger,
    LockUnavailable,
    OperationConflict,
    OperationLocked,
    RestoredStoreDetected,
    StoreCorrupt,
    StoreNotInitialized,
    StorePermissionError,
    StoreReplaced,
    SiteLocked,
    StoreUnavailable,
    TerminalAppendFailed,
    UnterminatedFence,
    _connect,
    initialize,
    payload_digest,
)

APP_ROOT = Path(__file__).resolve().parents[2]

RACE_CHILD = """
import sys
import time

from clinic_ui.core.write_fence import Anchor, Ledger

path, dep, epoch, seq, actor, kind, key, digest, ready, hold = sys.argv[1:11]
ledger = Ledger.open(path, anchor=Anchor(dep, int(epoch), int(seq)))
try:
    with ledger.fence(
        actor=actor, kind=kind, operation_key=key, payload_digest=digest
    ) as claim:
        if ready != "-":
            with open(ready, "w", encoding="utf-8") as handle:
                handle.write("ready")
        time.sleep(float(hold))
        claim.commit("native-receipt")
except Exception as exc:
    print(type(exc).__name__)
    raise SystemExit(3 if type(exc).__name__ == "LockUnavailable" else 4)
"""

CRASH_CHILD = """
import os
import sys

from clinic_ui.core.write_fence import Anchor, Ledger

path, dep, epoch, seq, actor, kind, key, digest = sys.argv[1:9]
ledger = Ledger.open(path, anchor=Anchor(dep, int(epoch), int(seq)))
with ledger.fence(
    actor=actor, kind=kind, operation_key=key, payload_digest=digest
):
    os._exit(17)
"""

# The body stands in for a native transaction that has already committed: the
# process dies before the terminal receipt is appended to the fence.
NATIVE_COMMIT_CRASH_CHILD = """
import os
import sys

from clinic_ui.core.write_fence import Anchor, Ledger

path, dep, epoch, seq, actor, kind, key, digest = sys.argv[1:9]
ledger = Ledger.open(path, anchor=Anchor(dep, int(epoch), int(seq)))
with ledger.fence(
    actor=actor, kind=kind, operation_key=key, payload_digest=digest
):
    os._exit(23)
"""

# Holder side of the deterministic cross-kind contention handshake. It reports
# readiness only once it is inside the fence, then blocks on stdin until the
# parent releases it.
HOLD_CHILD = """
import sys

from clinic_ui.core.write_fence import Anchor, Ledger

path, dep, epoch, seq, actor, kind, key, digest = sys.argv[1:9]
ledger = Ledger.open(path, anchor=Anchor(dep, int(epoch), int(seq)))
with ledger.fence(
    actor=actor, kind=kind, operation_key=key, payload_digest=digest
) as claim:
    print("ready", flush=True)
    sys.stdin.readline()
    claim.commit("native-receipt")
"""

# Contender side: one fence attempt against a lock the parent already knows is
# held. It never retries and exits 3 only for LockUnavailable.
CONTEND_CHILD = """
import sys

from clinic_ui.core.write_fence import Anchor, Ledger

path, dep, epoch, seq, actor, kind, key, digest = sys.argv[1:9]
ledger = Ledger.open(path, anchor=Anchor(dep, int(epoch), int(seq)))
try:
    with ledger.fence(
        actor=actor, kind=kind, operation_key=key, payload_digest=digest
    ):
        print("Acquired", flush=True)
        raise SystemExit(4)
except Exception as exc:
    print(type(exc).__name__)
    raise SystemExit(3 if type(exc).__name__ == "LockUnavailable" else 4)
"""


class FenceTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.info = initialize(self.directory)
        self.anchor = Anchor(
            self.info.deployment_id, self.info.epoch, self.info.sequence
        )
        self.db_path = self.directory / "write-fence.sqlite3"
        self.lock_path = self.directory / "write-fence.lock"

    def ledger(self, anchor=None):
        return Ledger.open(self.directory, anchor=anchor or self.anchor)

    def operation_key(self, number=1):
        return f"op-key-{number:04d}-0123456789"

    def digest(self, number=1):
        return payload_digest({"first_name": f"Synthetic{number}", "sex": "Female"})

    def claim_and_commit(
        self,
        ledger,
        number=1,
        receipt="native-receipt-1",
        actor="doc@example.test",
        kind="create",
    ):
        key = self.operation_key(number)
        digest = self.digest(number)
        with ledger.fence(
            actor=actor, kind=kind, operation_key=key, payload_digest=digest
        ) as claim:
            claim.commit(receipt)
        return key, claim

    def new_key(self, ledger, *, actor="doc@example.test", number=2):
        with ledger.fence(
            actor=actor,
            kind="create",
            operation_key=self.operation_key(number),
            payload_digest=self.digest(number),
        ) as claim:
            claim.commit("native-receipt-2")

    def acknowledge(
        self,
        ledger,
        number=1,
        actor="doc@example.test",
        kind="create",
        receipt="ack:test",
    ):
        return ledger.acknowledge_barrier(
            actor=actor,
            kind=kind,
            operation_key=self.operation_key(number),
            ack_receipt=receipt,
        )

    def events(self):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(
                "SELECT sequence, actor, kind, operation_key, state, receipt, reason"
                " FROM events ORDER BY sequence"
            ).fetchall()
        finally:
            connection.close()

    def run_child(self, script, *arguments, expect=None):
        script_path = self.directory / f"child-{len(list(self.directory.iterdir()))}.py"
        script_path.write_text(script, encoding="utf-8")
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(APP_ROOT)
        completed = subprocess.run(
            [sys.executable, str(script_path), *arguments],
            cwd=APP_ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if expect is not None:
            self.assertEqual(
                completed.returncode,
                expect,
                msg=f"stdout={completed.stdout!r} stderr={completed.stderr!r}",
            )
        return completed

    def make_store(self, name="nested"):
        path = Path(self._tmp.name) / name
        info = initialize(path)
        anchor = Anchor(info.deployment_id, info.epoch, info.sequence)
        return path, Ledger.open(path, anchor=anchor), info

    def run_fence_child(
        self,
        key,
        digest,
        *,
        actor="doc@example.test",
        kind="create",
        ready="-",
        hold="0",
        expect=0,
    ):
        return self.run_child(
            RACE_CHILD,
            str(self.directory),
            self.info.deployment_id,
            str(self.info.epoch),
            str(self.info.sequence),
            actor,
            kind,
            key,
            digest,
            ready,
            hold,
            expect=expect,
        )


class InitializationTests(FenceTestCase):
    def test_initialize_creates_private_store(self):
        self.assertEqual(self.info.deployment_id, self.info.deployment_id.lower())
        self.assertEqual(len(self.info.deployment_id), 32)
        int(self.info.deployment_id, 16)
        self.assertEqual(self.info.epoch, 1)
        self.assertEqual(self.info.sequence, 0)
        self.assertTrue(self.db_path.is_file())
        self.assertTrue(self.lock_path.is_file())
        self.assertEqual(stat.S_IMODE(os.stat(self.db_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.lock_path).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.directory).st_mode), 0o700)

    def test_open_never_initializes_a_missing_store(self):
        empty = Path(self._tmp.name) / "empty"
        empty.mkdir(mode=0o700)
        with self.assertRaises(StoreNotInitialized):
            Ledger.open(empty, anchor=self.anchor)
        self.assertEqual(list(empty.iterdir()), [])

    def test_second_initialize_refuses_and_preserves_the_store(self):
        self.claim_and_commit(self.ledger())
        before = self.events()
        with self.assertRaises(StoreUnavailable):
            initialize(self.directory)
        self.assertEqual(self.events(), before)
        self.assertTrue(self.db_path.is_file())

    def test_initialize_refuses_a_non_private_directory(self):
        directory = Path(self._tmp.name) / "loose"
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o755)
        self.addCleanup(os.chmod, directory, 0o700)
        with self.assertRaises(StorePermissionError):
            initialize(directory)
        self.assertEqual(list(directory.iterdir()), [])

    def test_open_refuses_a_non_private_directory(self):
        os.chmod(self.directory, 0o755)
        self.addCleanup(os.chmod, self.directory, 0o700)
        with self.assertRaises(StorePermissionError):
            self.ledger()

    def test_open_refuses_a_non_private_ledger_file(self):
        os.chmod(self.db_path, 0o644)
        self.addCleanup(os.chmod, self.db_path, 0o600)
        with self.assertRaises(StorePermissionError):
            self.ledger()

    def test_open_refuses_a_missing_lock_file(self):
        self.lock_path.unlink()
        with self.assertRaises(StoreNotInitialized):
            self.ledger()
        self.assertFalse(self.lock_path.exists())

    def test_open_rejects_an_invalid_anchor(self):
        for anchor in (
            Anchor("not-hex", 1, 0),
            Anchor(self.info.deployment_id, 0, 0),
            Anchor(self.info.deployment_id, 1, -1),
            Anchor(self.info.deployment_id, True, 0),
        ):
            with self.subTest(anchor=anchor):
                with self.assertRaises(InvalidOperation):
                    Ledger.open(self.directory, anchor=anchor)


class AnchorTests(FenceTestCase):
    def test_store_older_than_the_external_anchor_is_refused(self):
        self.claim_and_commit(self.ledger())
        future = Anchor(self.info.deployment_id, 1, 50)
        with self.assertRaises(RestoredStoreDetected):
            Ledger.open(self.directory, anchor=future)

    def test_deployment_or_epoch_mismatch_is_refused(self):
        for anchor in (
            Anchor("ab" * 16, 1, 0),
            Anchor(self.info.deployment_id, 2, 0),
        ):
            with self.subTest(anchor=anchor):
                with self.assertRaises(RestoredStoreDetected):
                    Ledger.open(self.directory, anchor=anchor)

    def test_store_newer_than_the_anchor_is_allowed(self):
        first = self.ledger()
        self.claim_and_commit(first, number=1)
        self.acknowledge(first, number=1)
        older = Anchor(self.info.deployment_id, 1, 0)
        second = Ledger.open(self.directory, anchor=older)
        key, _ = self.claim_and_commit(second, number=2)
        self.assertEqual(second.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        ).state, "COMMITTED")

    def test_missing_meta_sequence_progression_is_corrupt(self):
        self.claim_and_commit(self.ledger())
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE meta SET sequence = 0")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()


class CorruptionTests(FenceTestCase):
    def test_garbage_database_fails_closed(self):
        self.db_path.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_missing_meta_row_fails_closed(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DELETE FROM meta")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_sequence_gap_fails_closed(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO events (sequence, deployment_id, actor, kind,"
                " operation_key, payload_digest, state, receipt, reason,"
                " recorded_at) VALUES (99, ?, 'a@b.test', 'create', ?, ?,"
                " 'DISPATCHED', NULL, NULL, '2026-11-14T00:00:00Z')",
                (self.info.deployment_id, self.operation_key(9), self.digest(9)),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_events_are_append_only(self):
        self.claim_and_commit(self.ledger())
        connection = sqlite3.connect(self.db_path)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("UPDATE events SET state = 'UNKNOWN'")
            connection.rollback()
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DELETE FROM events")
            connection.rollback()
        finally:
            connection.close()
        self.assertEqual(self.events()[-1][4], "COMMITTED")

    def test_committed_event_requires_a_receipt(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO events (sequence, deployment_id, actor, kind,"
                " operation_key, payload_digest, state, receipt, reason,"
                " recorded_at) VALUES (1, ?, 'a@b.test', 'create', ?, ?,"
                " 'DISPATCHED', NULL, NULL, '2026-11-14T00:00:00Z')",
                (self.info.deployment_id, self.operation_key(1), self.digest(1)),
            )
            connection.commit()
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO events (sequence, deployment_id, actor, kind,"
                    " operation_key, payload_digest, state, receipt, reason,"
                    " recorded_at) VALUES (2, ?, 'a@b.test', 'create', ?, ?,"
                    " 'COMMITTED', NULL, NULL, '2026-11-14T00:00:00Z')",
                    (self.info.deployment_id, self.operation_key(1), self.digest(1)),
                )
            connection.rollback()
        finally:
            connection.close()


class ClaimLifecycleTests(FenceTestCase):
    def test_claim_then_commit_is_durable_and_monotonic(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        with ledger.fence(
            actor="doc@example.test", kind="create", operation_key=key,
            payload_digest=digest,
        ) as claim:
            self.assertEqual(claim.state, "DISPATCHED")
            self.assertEqual(claim.dispatch_sequence, 1)
            self.assertIsNone(claim.terminal_sequence)
            self.assertEqual(
                ledger.lookup(
                    actor="doc@example.test", kind="create", operation_key=key
                ).state,
                "DISPATCHED",
            )
            claim.commit("native-receipt-1")
            self.assertEqual(claim.state, "COMMITTED")
            self.assertEqual(claim.terminal_sequence, 3)
            raw = sqlite3.connect(self.db_path)
            try:
                state = raw.execute(
                    "SELECT state FROM events ORDER BY sequence DESC LIMIT 1"
                ).fetchone()[0]
            finally:
                raw.close()
            self.assertEqual(state, "COMMITTED")
        self.assertEqual(ledger.info().sequence, 3)
        record = ledger.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(record.sequence, 3)
        self.assertEqual(record.payload_digest, digest)
        self.assertEqual(record.receipt, "native-receipt-1")
        self.assertIsNone(record.reason)

    def test_pragmas_are_durable(self):
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute("PRAGMA synchronous").fetchone()[0], 2)
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "delete",
            )
        finally:
            connection.close()

    def test_duplicate_key_and_payload_is_locked_without_replay(self):
        ledger = self.ledger()
        key, first = self.claim_and_commit(ledger, number=1)
        with self.assertRaises(OperationLocked) as caught:
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(1),
            ):
                self.fail("duplicate must not reach the mutation body")
        self.assertEqual(caught.exception.state, "COMMITTED")
        self.assertEqual(caught.exception.sequence, 3)
        self.assertIsNotNone(first)
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED", "COMMITTED"],
        )

    def test_duplicate_key_with_a_different_payload_conflicts(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(ledger, number=1)
        with self.assertRaises(OperationConflict):
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(2),
            ):
                self.fail("conflict must not reach the mutation body")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED", "COMMITTED"],
        )

    def test_keys_are_scoped_by_actor_and_kind(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        self.claim_and_commit(ledger, number=1)
        self.acknowledge(ledger, number=1)
        with ledger.fence(
            actor="nurse@example.test", kind="create", operation_key=key,
            payload_digest=digest,
        ) as claim:
            claim.commit("native-receipt-2")
        self.acknowledge(ledger, number=1, actor="nurse@example.test")
        with ledger.fence(
            actor="doc@example.test", kind="update", operation_key=key,
            payload_digest=digest,
        ) as claim:
            claim.commit("native-receipt-3")
        self.assertEqual(ledger.info().sequence, 11)
        self.assertEqual(len(self.events()), 11)

    def test_commit_requires_a_receipt(self):
        ledger = self.ledger()
        with self.assertRaises(InvalidOperation):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ) as claim:
                claim.commit("")
        record = ledger.lookup(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
        )
        self.assertEqual(record.state, "DISPATCHED")

    def test_claim_cannot_append_after_the_fence_is_released(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(1),
            ) as claim:
                pass
        with self.assertRaises(FenceReleased):
            claim.commit("too-late")
        with self.assertRaises(FenceReleased):
            claim.unknown("too-late")

    def test_clean_exit_without_terminal_leaves_dispatched_and_raises(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        record = ledger.lookup(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
        )
        self.assertEqual(record.state, "DISPATCHED")

    def test_body_error_leaves_dispatched_and_never_appends_unknown(self):
        ledger = self.ledger()
        with self.assertRaises(RuntimeError):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                raise RuntimeError("native transaction failed")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED"],
        )
        with self.assertRaises(OperationLocked):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                self.fail("unknown/dispatched must stay locked")

    def test_unknown_is_terminal_and_locked(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        with ledger.fence(
            actor="doc@example.test", kind="create", operation_key=key,
            payload_digest=self.digest(1),
        ) as claim:
            claim.unknown("native commit status not observed")
        record = ledger.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(record.state, "UNKNOWN")
        self.assertEqual(record.reason, "native commit status not observed")
        self.assertIsNone(record.receipt)
        with self.assertRaises(OperationLocked) as caught:
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(1),
            ):
                self.fail("UNKNOWN must never be replayed")
        self.assertEqual(caught.exception.state, "UNKNOWN")

    def test_committed_state_survives_a_later_body_error(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        with self.assertRaises(RuntimeError):
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(1),
            ) as claim:
                claim.commit("native-receipt-1")
                raise RuntimeError("response failed after commit")
        record = ledger.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(record.receipt, "native-receipt-1")

    def test_terminal_append_failure_before_commit_is_unknown(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        original = write_fence._append
        calls = {"count": 0}

        def flaky(connection, leader, **kwargs):
            calls["count"] += 1
            if calls["count"] == 3:
                raise sqlite3.OperationalError("disk I/O error")
            return original(connection, leader, **kwargs)

        with mock.patch.object(write_fence, "_append", flaky):
            with self.assertRaises(TerminalAppendFailed):
                with ledger.fence(
                    actor="doc@example.test", kind="create", operation_key=key,
                    payload_digest=digest,
                ) as claim:
                    self.claim = claim
                    claim.commit("native-receipt-1")
        self.assertEqual(self.claim.state, "UNKNOWN")
        self.assertIsNone(self.claim.terminal_sequence)

        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED"],
        )
        with self.assertRaises(OperationLocked):
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=digest,
            ):
                self.fail("failed terminal append must never replay")

    def test_terminal_append_error_after_commit_is_unknown_not_dispatched(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        original = write_fence._append
        calls = {"count": 0}

        def committed_then_raised(connection, leader, **kwargs):
            calls["count"] += 1
            result = original(connection, leader, **kwargs)
            if kwargs["state"] == "COMMITTED":
                raise sqlite3.OperationalError("raised after durable commit")
            return result

        with mock.patch.object(write_fence, "_append", committed_then_raised):
            with self.assertRaises(TerminalAppendFailed):
                with ledger.fence(
                    actor="doc@example.test", kind="create", operation_key=key,
                    payload_digest=digest,
                ) as claim:
                    self.claim = claim
                    claim.commit("native-receipt-1")
        self.assertEqual(self.claim.state, "UNKNOWN")
        self.assertIsNone(self.claim.terminal_sequence)

        record = ledger.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(record.receipt, "native-receipt-1")
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="create", operation_key=key
            ).state,
            "COMMITTED",
        )
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=digest,
            ):
                self.fail("a post-commit append error must never replay")

    def test_unknown_append_failure_is_reported(self):
        ledger = self.ledger()
        original = write_fence._append
        calls = {"count": 0}

        def flaky(connection, leader, **kwargs):
            calls["count"] += 1
            if calls["count"] == 3:
                raise sqlite3.OperationalError("disk I/O error")
            return original(connection, leader, **kwargs)

        with mock.patch.object(write_fence, "_append", flaky):
            with self.assertRaises(TerminalAppendFailed):
                with ledger.fence(
                    actor="doc@example.test", kind="create",
                    operation_key=self.operation_key(1),
                    payload_digest=self.digest(1),
                ) as claim:
                    self.claim = claim
                    claim.unknown("ambiguous")
        self.assertEqual(self.claim.state, "UNKNOWN")
        self.assertEqual(self.events()[-1][4], "DISPATCHED")

    def test_lookup_of_an_unclaimed_key_is_none(self):
        self.assertIsNone(
            self.ledger().lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            )
        )

    def test_invalid_inputs_are_refused_before_any_write(self):
        ledger = self.ledger()
        valid = dict(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        )
        cases = (
            {"actor": ""},
            {"actor": " doc@example.test"},
            {"actor": "a" * 141},
            {"kind": "delete"},
            {"kind": "Create"},
            {"operation_key": "short"},
            {"operation_key": "k" * 129},
            {"payload_digest": "abc"},
            {"payload_digest": self.digest(1).upper()},
            {"payload_digest": "g" * 64},
        )
        for override in cases:
            with self.subTest(override=override):
                arguments = dict(valid, **override)
                with self.assertRaises(InvalidOperation):
                    with ledger.fence(**arguments):
                        self.fail("invalid input must not be claimed")
        self.assertEqual(self.events(), [])

    def test_payload_digest_is_canonical_and_stable(self):
        first = payload_digest({"b": 2, "a": 1})
        second = payload_digest({"a": 1, "b": 2})
        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)
        self.assertNotEqual(first, payload_digest({"a": 1, "b": 3}))
        for bad in ([], "payload", None, {"a": float("nan")}):
            with self.subTest(payload=bad):
                with self.assertRaises(InvalidOperation):
                    payload_digest(bad)


class RestartTests(FenceTestCase):
    def test_state_survives_reopen_with_the_original_anchor(self):
        first = self.ledger()
        key, _ = self.claim_and_commit(first, number=1)
        second = Ledger.open(self.directory, anchor=self.anchor)
        record = second.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(record.receipt, "native-receipt-1")
        self.assertEqual(second.info().deployment_id, self.info.deployment_id)
        self.assertEqual(second.info().epoch, self.info.epoch)
        self.acknowledge(second, number=1)
        with second.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(2),
            payload_digest=self.digest(2),
        ) as claim:
            self.assertEqual(claim.dispatch_sequence, 5)
            claim.commit("native-receipt-2")
        self.assertEqual(second.info().sequence, 7)

    def test_reopen_does_not_release_a_dispatched_key(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                self.fail("a restart must not unlock a dispatched key")


class LockTests(FenceTestCase):
    def test_a_second_ledger_in_the_same_process_cannot_hold_the_fence(self):
        first = self.ledger()
        second = self.ledger()
        with first.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as claim:
            with self.assertRaises(LockUnavailable):
                with second.fence(
                    actor="doc@example.test", kind="create",
                    operation_key=self.operation_key(2),
                    payload_digest=self.digest(2),
                ):
                    self.fail("a second process lock must be refused")
            claim.commit("native-receipt-1")
        self.acknowledge(first, number=1)
        with second.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(2),
            payload_digest=self.digest(2),
        ) as claim:
            claim.commit("native-receipt-2")

    def test_concurrent_processes_allow_exactly_one_mutation(self):
        ready = self.directory / "winner-ready"
        winner = subprocess.Popen(
            [
                sys.executable,
                "-c",
                RACE_CHILD,
                str(self.directory),
                self.info.deployment_id,
                str(self.info.epoch),
                str(self.info.sequence),
                "doc@example.test",
                "create",
                self.operation_key(1),
                self.digest(1),
                str(ready),
                "1.0",
            ],
            cwd=APP_ROOT,
            env=dict(os.environ, PYTHONPATH=str(APP_ROOT)),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            for _ in range(200):
                if ready.exists():
                    break
                if winner.poll() is not None:
                    break
                time.sleep(0.01)
            self.assertTrue(ready.exists(), msg="winner never claimed the fence")
            loser = self.run_fence_child(
                self.operation_key(1), self.digest(1), expect=3
            )
            self.assertEqual(loser.stdout.strip(), "LockUnavailable")
        finally:
            stdout, stderr = winner.communicate(timeout=30)
        self.assertEqual(winner.returncode, 0, msg=f"{stdout!r} {stderr!r}")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED", "COMMITTED"],
        )
        self.assertEqual(
            self.ledger().lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            ).receipt,
            "native-receipt",
        )

    def test_crash_releases_the_lock_but_keeps_the_claim_locked(self):
        completed = self.run_child(
            CRASH_CHILD,
            str(self.directory),
            self.info.deployment_id,
            str(self.info.epoch),
            str(self.info.sequence),
            "doc@example.test",
            "create",
            self.operation_key(1),
            self.digest(1),
            expect=17,
        )
        self.assertEqual(completed.returncode, 17)
        ledger = self.ledger()
        self.assertEqual(
            ledger.lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            ).state,
            "DISPATCHED",
        )
        with self.assertRaises(OperationLocked):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                self.fail("a crashed claim must stay locked")
        # The lock was released by the crash, but the unresolved site-wide
        # claim now blocks every fresh key, so no new dispatch can be recorded.
        with self.assertRaises(SiteLocked):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(2),
                payload_digest=self.digest(2),
            ):
                self.fail("a site with an unresolved key must not dispatch")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED"],
        )


class ReplacementTests(FenceTestCase):
    def test_ledger_file_replacement_is_rejected(self):
        ledger = self.ledger()
        os.rename(self.db_path, self.directory / "original-db")
        self.db_path.write_bytes(b"replacement")
        os.chmod(self.db_path, 0o600)
        with self.assertRaises(StoreReplaced):
            ledger.lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            )

    def test_lock_file_replacement_is_rejected_before_flock(self):
        ledger = self.ledger()
        os.rename(self.lock_path, self.directory / "original-lock")
        self.lock_path.write_bytes(b"replacement")
        os.chmod(self.lock_path, 0o600)
        with self.assertRaises(StoreReplaced):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                self.fail("a replaced lock must not be acquired")

    def test_directory_replacement_is_rejected(self):
        path, ledger, _ = self.make_store("nested")
        moved = Path(self._tmp.name) / "nested-moved"
        os.rename(path, moved)
        path.mkdir(mode=0o700)
        with self.assertRaises(StoreReplaced):
            ledger.lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            )

    def test_symlinked_ledger_file_is_rejected_after_open(self):
        ledger = self.ledger()
        os.rename(self.db_path, self.directory / "original-db")
        os.symlink(self.directory / "original-db", self.db_path)
        with self.assertRaises(StoreReplaced):
            ledger.lookup(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
            )

    def test_symlinked_ledger_file_is_rejected_at_open(self):
        real, _, info = self.make_store("real")
        fake = Path(self._tmp.name) / "fake"
        fake.mkdir(mode=0o700)
        os.symlink(real / "write-fence.sqlite3", fake / "write-fence.sqlite3")
        (fake / "write-fence.lock").write_bytes(b"")
        os.chmod(fake / "write-fence.lock", 0o600)
        anchor = Anchor(info.deployment_id, info.epoch, info.sequence)
        with self.assertRaises(StoreReplaced):
            Ledger.open(fake, anchor=anchor)

    def test_replacement_inside_the_fence_is_rejected_before_terminal(self):
        ledger = self.ledger()
        with self.assertRaises(StoreReplaced):
            with ledger.fence(
                actor="doc@example.test", kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ) as claim:
                os.rename(self.db_path, self.directory / "original-db")
                self.db_path.write_bytes(b"replacement")
                os.chmod(self.db_path, 0o600)
                claim.commit("native-receipt-1")
                self.fail("a replaced ledger must not accept a terminal record")
        self.assertEqual(claim.state, "UNKNOWN")


class IntegrityTests(FenceTestCase):
    def test_non_contiguous_sequence_with_matching_meta_is_corrupt(self):
        self.claim_and_commit(self.ledger())
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO events (sequence, deployment_id, actor, kind,"
                " operation_key, payload_digest, state, receipt, reason,"
                " recorded_at) VALUES (5, ?, 'doc@example.test', 'create', ?, ?,"
                " 'DISPATCHED', NULL, NULL, '2026-11-14T00:00:00Z')",
                (self.info.deployment_id, self.operation_key(2), self.digest(2)),
            )
            connection.execute("UPDATE meta SET sequence = 5")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_terminal_digest_must_match_its_dispatch(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test", kind="create", operation_key=key,
                payload_digest=self.digest(1),
            ):
                pass
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO events (sequence, deployment_id, actor, kind,"
                " operation_key, payload_digest, state, receipt, reason,"
                " recorded_at) VALUES (3, ?, 'doc@example.test', 'create', ?, ?,"
                " 'COMMITTED', 'receipt', NULL, '2026-11-14T00:00:00Z')",
                (self.info.deployment_id, key, self.digest(2)),
            )
            connection.execute("UPDATE meta SET sequence = 3")
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_missing_required_triggers_are_corrupt(self):
        names = (
            "events_append_only_update",
            "events_append_only_delete",
            "events_terminal_after_dispatch",
        )
        for name in names:
            with self.subTest(trigger=name):
                path = Path(self._tmp.name) / name
                info = initialize(path)
                connection = sqlite3.connect(path / "write-fence.sqlite3")
                try:
                    connection.execute(f"DROP TRIGGER {name}")
                    connection.commit()
                finally:
                    connection.close()
                anchor = Anchor(info.deployment_id, info.epoch, info.sequence)
                with self.assertRaises(StoreCorrupt):
                    Ledger.open(path, anchor=anchor)

    def test_same_name_no_op_trigger_is_corrupt(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER events_append_only_update")
            connection.execute(
                "CREATE TRIGGER events_append_only_update"
                " BEFORE UPDATE ON events BEGIN SELECT 1; END"
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_altered_critical_constraint_is_corrupt(self):
        connection = sqlite3.connect(self.db_path)
        try:
            triggers = [
                row[0]
                for row in connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'trigger'"
                )
            ]
            connection.execute("DROP TABLE events")
            connection.execute(
                "CREATE TABLE events ("
                " sequence INTEGER NOT NULL PRIMARY KEY,"
                " deployment_id TEXT NOT NULL,"
                " actor TEXT NOT NULL,"
                " kind TEXT NOT NULL,"
                " operation_key TEXT NOT NULL,"
                " payload_digest TEXT NOT NULL,"
                " state TEXT NOT NULL,"
                " receipt TEXT,"
                " reason TEXT,"
                " recorded_at TEXT NOT NULL,"
                " UNIQUE (deployment_id, actor, kind, operation_key, state)"
                ")"
            )
            for sql in triggers:
                connection.execute(sql)
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(StoreCorrupt):
            self.ledger()

    def test_equivalent_schema_formatting_does_not_false_positive(self):
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("DROP TRIGGER events_append_only_update")
            connection.execute(
                "create trigger events_append_only_update"
                "\n  before update on events begin"
                "\n    select raise ( abort , 'write fence events are append-only' ) ;"
                "\n  end"
            )
            connection.commit()
        finally:
            connection.close()
        ledger = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(ledger.info().sequence, 0)
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute(
                "INSERT INTO events (sequence, deployment_id, actor, kind,"
                " operation_key, payload_digest, state, receipt, reason,"
                " recorded_at) VALUES (1, ?, 'a@b.test', 'create', ?, ?,"
                " 'DISPATCHED', NULL, NULL, '2026-11-14T00:00:00Z')",
                (self.info.deployment_id, self.operation_key(1), self.digest(1)),
            )
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE events SET state = 'UNKNOWN'")
            connection.rollback()
        finally:
            connection.close()

    def test_wal_journal_mode_is_rejected(self):
        path, _, info = self.make_store("wal")
        connection = sqlite3.connect(path / "write-fence.sqlite3")
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.commit()
        finally:
            connection.close()
        anchor = Anchor(info.deployment_id, info.epoch, info.sequence)
        with self.assertRaises(StoreCorrupt):
            Ledger.open(path, anchor=anchor)

    def test_the_actual_connection_is_durable_and_private(self):
        connection = _connect(self.db_path)
        try:
            self.assertEqual(
                connection.execute("PRAGMA synchronous").fetchone()[0], 2
            )
            self.assertEqual(
                connection.execute("PRAGMA busy_timeout").fetchone()[0], 1000
            )
            self.assertEqual(
                connection.execute("PRAGMA journal_mode").fetchone()[0].lower(),
                "delete",
            )
        finally:
            connection.close()

    def test_required_directory_fsync_failure_fails_initialization(self):
        path = Path(self._tmp.name) / "fsync-fail"
        with mock.patch.object(
            write_fence.os, "fsync", side_effect=OSError(errno.EIO, "fsync failed")
        ):
            with self.assertRaises(StoreUnavailable):
                initialize(path)
        self.assertTrue((path / "write-fence.sqlite3").exists())
        self.assertTrue((path / "write-fence.lock").exists())


class ClaimImmutabilityTests(FenceTestCase):
    def test_claim_identity_and_state_are_read_only(self):
        ledger = self.ledger()
        with ledger.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as claim:
            for field in (
                "actor", "kind", "operation_key", "payload_digest", "state",
                "dispatch_sequence", "terminal_sequence", "receipt", "reason",
            ):
                with self.subTest(field=field):
                    with self.assertRaises(AttributeError):
                        setattr(claim, field, "mutated")
            claim.commit("native-receipt-1")
        record = ledger.lookup(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
        )
        self.assertEqual(record.payload_digest, self.digest(1))
        self.assertEqual(record.state, "COMMITTED")

    def test_mutation_attempts_cannot_change_the_terminal_binding(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        with ledger.fence(
            actor="doc@example.test", kind="create", operation_key=key,
            payload_digest=digest,
        ) as claim:
            for field in ("payload_digest", "operation_key", "actor", "kind"):
                with self.assertRaises(AttributeError):
                    setattr(claim, field, "mutated")
            claim.commit("native-receipt-1")
        raw = sqlite3.connect(self.db_path)
        try:
            rows = raw.execute(
                "SELECT payload_digest, state FROM events ORDER BY sequence"
            ).fetchall()
        finally:
            raw.close()
        barrier = write_fence.barrier_digest(
            "doc@example.test", "create", key
        )
        self.assertEqual(
            rows,
            [
                (digest, "DISPATCHED"),
                (barrier, "DISPATCHED"),
                (digest, "COMMITTED"),
            ],
        )

    def test_completion_state_rejects_a_second_terminal(self):
        ledger = self.ledger()
        with ledger.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as committed:
            committed.commit("native-receipt-1")
            with self.assertRaises(FenceReleased):
                committed.commit("native-receipt-2")
            with self.assertRaises(FenceReleased):
                committed.unknown("second terminal")
        self.acknowledge(ledger, number=1)
        with ledger.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(2),
            payload_digest=self.digest(2),
        ) as unknown:
            unknown.unknown("not observed")
            with self.assertRaises(FenceReleased):
                unknown.commit("native-receipt-2")
            with self.assertRaises(FenceReleased):
                unknown.unknown("second terminal")


class LockOrderingTests(FenceTestCase):
    def test_lock_stays_held_after_commit_until_the_block_exits(self):
        ledger = self.ledger()
        with ledger.fence(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as claim:
            claim.commit("native-receipt-1")
            loser = self.run_fence_child(
                self.operation_key(2), self.digest(2), expect=3
            )
            self.assertEqual(loser.stdout.strip(), "LockUnavailable")
        self.acknowledge(ledger, number=1)
        winner = self.run_fence_child(
            self.operation_key(2), self.digest(2), expect=0
        )
        self.assertEqual(winner.stdout.strip(), "")
        record = ledger.lookup(
            actor="doc@example.test", kind="create",
            operation_key=self.operation_key(2),
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(ledger.info().sequence, 7)



class SiteWideUnresolvedTests(FenceTestCase):
    """One unresolved key must block every new key for every actor."""

    def leave_dispatched(self, ledger, *, actor="doc@example.test", number=1):
        with self.assertRaises(RuntimeError):
            with ledger.fence(
                actor=actor,
                kind="create",
                operation_key=self.operation_key(number),
                payload_digest=self.digest(number),
            ):
                raise RuntimeError("process lost before terminal")
        record = ledger.lookup(
            actor=actor, kind="create", operation_key=self.operation_key(number)
        )
        self.assertEqual(record.state, "DISPATCHED")

    def leave_unknown(self, ledger, *, actor="doc@example.test", number=1):
        with ledger.fence(
            actor=actor,
            kind="create",
            operation_key=self.operation_key(number),
            payload_digest=self.digest(number),
        ) as claim:
            claim.unknown("native outcome not established")
        record = ledger.lookup(
            actor=actor, kind="create", operation_key=self.operation_key(number)
        )
        self.assertEqual(record.state, "UNKNOWN")

    def new_key(self, ledger, *, actor="doc@example.test", number=2):
        with ledger.fence(
            actor=actor,
            kind="create",
            operation_key=self.operation_key(number),
            payload_digest=self.digest(number),
        ) as claim:
            claim.commit("native-receipt-2")

    def test_dispatched_key_blocks_a_fresh_key_for_the_same_actor(self):
        ledger = self.ledger()
        self.leave_dispatched(ledger)
        with self.assertRaises(SiteLocked):
            self.new_key(ledger)
        self.assertIsNone(
            ledger.lookup(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(2),
            )
        )

    def test_dispatched_key_blocks_a_different_actor(self):
        ledger = self.ledger()
        self.leave_dispatched(ledger, actor="doc@example.test")
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, actor="other@example.test")
        self.assertIsNone(
            ledger.lookup(
                actor="other@example.test",
                kind="create",
                operation_key=self.operation_key(2),
            )
        )

    def test_unknown_key_blocks_a_fresh_key(self):
        ledger = self.ledger()
        self.leave_unknown(ledger)
        with self.assertRaises(SiteLocked):
            self.new_key(ledger)

    def test_lock_survives_reopening_the_store(self):
        self.leave_dispatched(self.ledger())
        reopened = self.ledger()
        with self.assertRaises(SiteLocked):
            self.new_key(reopened)

    def test_lock_survives_a_real_child_that_dies_after_dispatch(self):
        ledger = self.ledger()
        completed = self.run_child(
            CRASH_CHILD,
            str(self.directory),
            self.info.deployment_id,
            str(self.info.epoch),
            str(self.info.sequence),
            "doc@example.test",
            "create",
            self.operation_key(1),
            self.digest(1),
            expect=17,
        )
        self.assertEqual(completed.returncode, 17)
        self.assertEqual(
            ledger.lookup(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(1),
            ).state,
            "DISPATCHED",
        )
        with self.assertRaises(SiteLocked):
            self.new_key(ledger)

    def test_committed_key_blocks_a_fresh_key_until_acknowledged(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)
        self.assertIsNone(
            ledger.lookup(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(2),
            )
        )
        self.assertEqual(self.acknowledge(ledger, number=1), "acked")
        self.new_key(ledger, number=2)
        record = ledger.lookup(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(2),
        )
        self.assertEqual(record.state, "COMMITTED")

    def test_an_existing_resolved_key_still_raises_lock_before_site_check(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.acknowledge(ledger, number=1)
        self.leave_dispatched(ledger, number=2)
        with self.assertRaises(OperationLocked):
            with ledger.fence(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=3)


class ReadinessTests(FenceTestCase):
    """``ready`` is an advisory, non-mutating claim-time-equivalent check."""

    def test_ready_true_on_a_fresh_store(self):
        self.assertTrue(self.ledger().ready())

    def test_ready_false_when_a_key_is_dispatched(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        self.assertFalse(ledger.ready())

    def test_ready_false_for_a_committed_but_unacknowledged_write(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.assertFalse(ledger.ready())
        self.assertEqual(self.acknowledge(ledger, number=1), "acked")
        self.assertTrue(ledger.ready())

    def test_ready_false_when_an_unknown_terminal_exists(self):
        ledger = self.ledger()
        with ledger.fence(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as claim:
            claim.unknown("not observed")
        self.assertFalse(ledger.ready())

    def test_ready_false_when_the_manual_latch_exists(self):
        ledger = self.ledger()
        self.assertTrue(ledger.latch("manual investigation"))
        self.assertFalse(ledger.ready())

    def test_ready_false_for_the_latch_event_without_the_latch_file(self):
        ledger = self.ledger()
        self.assertTrue(ledger.latch("manual investigation"))
        (self.directory / write_fence.LATCH_FILENAME).unlink()
        self.assertFalse(ledger.ready())

    def test_ready_false_while_another_ledger_holds_the_fence(self):
        first = self.ledger()
        second = self.ledger()
        with first.fence(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(1),
            payload_digest=self.digest(1),
        ) as claim:
            self.assertFalse(second.ready())
            claim.commit("native-receipt-1")

    def test_ready_never_appends_and_never_changes_the_sequence(self):
        ledger = self.ledger()
        before = ledger.info().sequence
        for _ in range(3):
            self.assertTrue(ledger.ready())
        self.assertEqual(ledger.info().sequence, before)
        self.assertEqual(self.events(), [])


class CommittedCreateEnumerationTests(FenceTestCase):
    def commit_and_ack(self, ledger, number, receipt):
        key, _ = self.claim_and_commit(ledger, number, receipt=receipt)
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="create",
                operation_key=key,
                ack_receipt=f"ack:{number}",
            ),
            "acked",
        )
        return key

    def test_lists_only_committed_create_receipts_and_excludes_barriers(self):
        ledger = self.ledger()
        first = self.commit_and_ack(ledger, 1, "receipt-one")
        second = self.commit_and_ack(ledger, 2, "receipt-two")

        with ledger.fence(
            actor="doc@example.test",
            kind="update",
            operation_key=self.operation_key(3),
            payload_digest=self.digest(3),
        ) as claim:
            claim.commit("receipt-update")
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="update",
                operation_key=self.operation_key(3),
                ack_receipt="ack:update",
            ),
            "acked",
        )

        with ledger.fence(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(4),
            payload_digest=self.digest(4),
        ) as claim:
            claim.unknown("uncertain native outcome")

        records = ledger.committed_create_receipts(limit=10)
        self.assertEqual(
            [(record.actor, record.operation_key, record.receipt) for record in records],
            [
                ("doc@example.test", first, "receipt-one"),
                ("doc@example.test", second, "receipt-two"),
            ],
        )

    def test_excludes_dispatched_and_unknown_operations(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        self.assertEqual(ledger.committed_create_receipts(limit=10), [])

    def test_enumeration_is_bounded_and_rejects_invalid_limits(self):
        ledger = self.ledger()
        keys = [
            self.commit_and_ack(ledger, number, f"receipt-{number}")
            for number in (1, 2, 3)
        ]

        self.assertEqual(len(ledger.committed_create_receipts(limit=3)), 3)
        self.assertEqual(len(ledger.committed_create_receipts(limit=4)), 3)
        with self.assertRaises(EnumerationOverflow):
            ledger.committed_create_receipts(limit=2)
        self.assertEqual(
            [record.operation_key for record in ledger.committed_create_receipts(
                limit=3, operation_key=keys[1]
            )],
            [keys[1]],
        )

        for limit in (0, -1, True, 1.0, "1", write_fence.MAX_RECEIPT_ENUMERATION + 1):
            with self.subTest(limit=limit):
                with self.assertRaises(InvalidOperation):
                    ledger.committed_create_receipts(limit=limit)

    def test_exact_limit_is_not_overflow_but_one_more_is(self):
        ledger = self.ledger()
        for number in (1, 2):
            self.commit_and_ack(ledger, number, f"receipt-{number}")
        self.assertEqual(len(ledger.committed_create_receipts(limit=2)), 2)
        self.commit_and_ack(ledger, 3, "receipt-3")
        with self.assertRaises(EnumerationOverflow):
            ledger.committed_create_receipts(limit=2)

    def test_enumeration_never_appends_or_changes_the_sequence(self):
        ledger = self.ledger()
        self.commit_and_ack(ledger, 1, "receipt-one")
        before = ledger.info()
        ledger.committed_create_receipts(limit=10)
        after = ledger.info()
        self.assertEqual(before, after)

    def test_corrupt_store_fails_closed_on_enumeration(self):
        ledger = self.ledger()
        self.commit_and_ack(ledger, 1, "receipt-one")
        self.db_path.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(StoreUnavailable):
            ledger.committed_create_receipts(limit=10)


class BarrierAckTests(FenceTestCase):
    """The committed write stays site-wide barred until an explicit ack."""

    def test_committed_operation_requires_ack_before_another_key(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)
        self.assertEqual(self.acknowledge(ledger, number=1), "acked")
        self.new_key(ledger, number=2)
        self.assertEqual(
            ledger.lookup(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(2),
            ).state,
            "COMMITTED",
        )

    def test_ack_survives_a_restart_and_admits_a_new_key(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.assertEqual(self.acknowledge(ledger, number=1), "acked")
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        with reopened.fence(
            actor="doc@example.test",
            kind="create",
            operation_key=self.operation_key(2),
            payload_digest=self.digest(2),
        ) as claim:
            claim.commit("native-receipt-2")
        self.assertEqual(reopened.info().sequence, 7)

    def test_malformed_ack_receipt_is_refused_and_barrier_remains(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        for bad in ("", "x" * 2000):
            with self.subTest(receipt=bad[:8]):
                with self.assertRaises(InvalidOperation):
                    ledger.acknowledge_barrier(
                        actor="doc@example.test",
                        kind="create",
                        operation_key=self.operation_key(1),
                        ack_receipt=bad,
                    )
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)

    def test_ack_before_commit_is_invalid_and_barrier_remains(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test",
                kind="create",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        self.assertEqual(self.acknowledge(ledger, number=1), "invalid")
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)

    def test_ack_for_an_unknown_operation_is_invalid(self):
        self.assertEqual(self.acknowledge(self.ledger(), number=9), "invalid")

    def test_wrong_actor_or_kind_ack_is_invalid(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="other@example.test",
                kind="create",
                operation_key=self.operation_key(1),
                ack_receipt="ack:test",
            ),
            "invalid",
        )
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="update",
                operation_key=self.operation_key(1),
                ack_receipt="ack:test",
            ),
            "invalid",
        )
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)

    def test_repeat_ack_is_idempotent_for_the_same_receipt(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.assertEqual(self.acknowledge(ledger, number=1, receipt="ack:one"), "acked")
        sequence = ledger.info().sequence
        self.assertEqual(
            self.acknowledge(ledger, number=1, receipt="ack:one"), "already"
        )
        self.assertEqual(ledger.info().sequence, sequence)
        self.assertEqual(
            self.acknowledge(ledger, number=1, receipt="ack:different"), "invalid"
        )
        self.assertEqual(ledger.info().sequence, sequence)

    def test_ack_records_one_barrier_terminal(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, number=1)
        self.acknowledge(ledger, number=1, receipt="ack:one")
        barrier = write_fence.barrier_key(
            "doc@example.test", "create", self.operation_key(1)
        )
        terminals = [
            event
            for event in self.events()
            if event[3] == barrier and event[4] == "COMMITTED"
        ]
        self.assertEqual(len(terminals), 1)
        self.assertEqual(terminals[0][5], "ack:one")


class AppointmentKindTests(FenceTestCase):
    """The appointment kinds are additive; the patient kinds are untouched."""

    def test_patient_kinds_are_unchanged(self):
        self.assertEqual(
            write_fence.PATIENT_OPERATION_KINDS, frozenset({"create", "update"})
        )
        self.assertEqual(
            write_fence.APPOINTMENT_OPERATION_KINDS,
            frozenset(
                {
                    "book",
                    "reschedule",
                    "checkin",
                    "appointment_cancel",
                    "appointment_no_show",
                }
            ),
        )
        self.assertEqual(
            write_fence.OPERATION_KINDS,
            frozenset(
                {
                    "create",
                    "update",
                    "book",
                    "reschedule",
                    "checkin",
                    "appointment_cancel",
                    "appointment_no_show",
                }
            ),
        )

    def test_each_appointment_kind_claims_and_commits(self):
        ledger = self.ledger()
        for number, kind in enumerate(("book", "reschedule", "checkin"), start=1):
            with self.subTest(kind=kind):
                key, claim = self.claim_and_commit(
                    ledger, number, receipt=f"receipt-{kind}", kind=kind
                )
                self.assertEqual(claim.kind, kind)
                self.assertEqual(
                    ledger.acknowledge_barrier(
                        actor="doc@example.test",
                        kind=kind,
                        operation_key=key,
                        ack_receipt=f"ack:{kind}",
                    ),
                    "acked",
                )
                record = ledger.lookup(
                    actor="doc@example.test", kind=kind, operation_key=key
                )
                self.assertEqual(record.state, "COMMITTED")
                self.assertEqual(record.receipt, f"receipt-{kind}")

    def test_unsupported_kinds_are_refused_before_any_write(self):
        ledger = self.ledger()
        for kind in ("cancel", "delete", "Book", "CHECKIN", "appointment", ""):
            with self.subTest(kind=kind):
                with self.assertRaises(InvalidOperation):
                    with ledger.fence(
                        actor="doc@example.test",
                        kind=kind,
                        operation_key=self.operation_key(1),
                        payload_digest=self.digest(1),
                    ):
                        self.fail("unsupported kind must not be claimed")
        self.assertEqual(self.events(), [])

    def test_one_operation_key_is_independent_per_kind(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        barriers = set()
        for kind in ("create", "book", "reschedule", "checkin"):
            with self.subTest(kind=kind):
                digest = payload_digest({"kind": kind, "operation_key": key})
                with ledger.fence(
                    actor="doc@example.test",
                    kind=kind,
                    operation_key=key,
                    payload_digest=digest,
                ) as claim:
                    claim.commit(f"receipt-{kind}")
                with self.assertRaises(SiteLocked):
                    self.new_key(ledger, number=2)
                self.assertEqual(
                    ledger.acknowledge_barrier(
                        actor="doc@example.test",
                        kind=kind,
                        operation_key=key,
                        ack_receipt=f"ack:{kind}",
                    ),
                    "acked",
                )
                barriers.add(write_fence.barrier_key("doc@example.test", kind, key))
        self.assertEqual(len(barriers), 4)
        for kind in ("create", "book", "reschedule", "checkin"):
            record = ledger.lookup(
                actor="doc@example.test", kind=kind, operation_key=key
            )
            self.assertEqual(record.state, "COMMITTED")
            self.assertEqual(record.receipt, f"receipt-{kind}")


class AppointmentReceiptEnumerationTests(FenceTestCase):
    def commit_appointment(self, ledger, number, kind, receipt):
        key, _ = self.claim_and_commit(
            ledger, number, receipt=receipt, kind=kind
        )
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind=kind,
                operation_key=key,
                ack_receipt=f"ack:{number}",
            ),
            "acked",
        )
        return key

    def test_enumerates_only_appointment_receipts(self):
        ledger = self.ledger()
        patient_key, _ = self.claim_and_commit(
            ledger, 1, receipt="patient-receipt", kind="create"
        )
        self.acknowledge(ledger, 1)
        book = self.commit_appointment(ledger, 2, "book", "book-receipt")
        reschedule = self.commit_appointment(
            ledger, 3, "reschedule", "reschedule-receipt"
        )
        checkin = self.commit_appointment(ledger, 4, "checkin", "checkin-receipt")
        records = ledger.committed_appointment_receipts(limit=10)
        self.assertEqual(
            [
                (record.kind, record.actor, record.operation_key, record.receipt)
                for record in records
            ],
            [
                ("book", "doc@example.test", book, "book-receipt"),
                ("reschedule", "doc@example.test", reschedule, "reschedule-receipt"),
                ("checkin", "doc@example.test", checkin, "checkin-receipt"),
            ],
        )
        self.assertEqual(
            [
                record.operation_key
                for record in ledger.committed_create_receipts(limit=10)
            ],
            [patient_key],
        )

    def test_does_not_return_latch_or_barrier_rows(self):
        ledger = self.ledger()
        self.commit_appointment(ledger, 1, "book", "book-receipt")
        actors = {
            event[1] for event in self.events() if event[2] == "book"
        }
        self.assertEqual(actors, {"doc@example.test", write_fence.LATCH_ACTOR})
        self.assertEqual(len(ledger.committed_appointment_receipts(limit=10)), 1)

    def test_narrows_by_appointment_kind_and_key(self):
        ledger = self.ledger()
        book = self.commit_appointment(ledger, 1, "book", "book-receipt")
        checkin = self.commit_appointment(ledger, 2, "checkin", "checkin-receipt")
        self.assertEqual(
            [
                record.operation_key
                for record in ledger.committed_appointment_receipts(
                    kind="checkin", limit=10
                )
            ],
            [checkin],
        )
        self.assertEqual(
            [
                record.operation_key
                for record in ledger.committed_appointment_receipts(
                    limit=10, operation_key=book
                )
            ],
            [book],
        )

    def test_excludes_dispatched_and_unknown_appointment_operations(self):
        ledger = self.ledger()
        with self.assertRaises(UnterminatedFence):
            with ledger.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=self.operation_key(1),
                payload_digest=self.digest(1),
            ):
                pass
        self.assertEqual(ledger.committed_appointment_receipts(limit=10), [])

    def test_enumeration_is_bounded_and_rejects_invalid_input(self):
        ledger = self.ledger()
        keys = [
            self.commit_appointment(ledger, number, "book", f"receipt-{number}")
            for number in (1, 2, 3)
        ]
        self.assertEqual(len(ledger.committed_appointment_receipts(limit=3)), 3)
        self.assertEqual(len(ledger.committed_appointment_receipts(limit=4)), 3)
        with self.assertRaises(EnumerationOverflow):
            ledger.committed_appointment_receipts(limit=2)
        for limit in (0, -1, True, 1.0, "1", write_fence.MAX_RECEIPT_ENUMERATION + 1):
            with self.subTest(limit=limit):
                with self.assertRaises(InvalidOperation):
                    ledger.committed_appointment_receipts(limit=limit)
        for kind in ("create", "update", "cancel", "Book", ""):
            with self.subTest(kind=kind):
                with self.assertRaises(InvalidOperation):
                    ledger.committed_appointment_receipts(kind=kind, limit=10)
        with self.assertRaises(InvalidOperation):
            ledger.committed_appointment_receipts(limit=10, operation_key="short")
        self.assertEqual(
            [record.operation_key for record in ledger.committed_appointment_receipts(
                limit=3, operation_key=keys[1]
            )],
            [keys[1]],
        )

    def test_enumeration_never_appends_or_changes_the_sequence(self):
        ledger = self.ledger()
        self.commit_appointment(ledger, 1, "book", "book-receipt")
        before = ledger.info()
        ledger.committed_appointment_receipts(limit=10)
        self.assertEqual(before, ledger.info())

    def test_corrupt_store_fails_closed_on_appointment_enumeration(self):
        ledger = self.ledger()
        self.commit_appointment(ledger, 1, "book", "book-receipt")
        self.db_path.write_bytes(b"this is not a sqlite database")
        with self.assertRaises(StoreUnavailable):
            ledger.committed_appointment_receipts(limit=10)


class MixedKindBarrierTests(FenceTestCase):
    """The barrier stays site-wide across patient and appointment kinds."""

    def test_appointment_commit_blocks_a_patient_create_until_acked(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, 1, receipt="book-receipt", kind="book")
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)
        self.assertEqual(self.acknowledge(ledger, 1, kind="book"), "acked")
        self.new_key(ledger, number=2)

    def test_patient_commit_blocks_an_appointment_book_until_acked(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, 1, receipt="patient-receipt", kind="create")
        with self.assertRaises(SiteLocked):
            with ledger.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=self.operation_key(2),
                payload_digest=self.digest(2),
            ):
                self.fail("a patient barrier must block an appointment claim")
        self.assertEqual(self.acknowledge(ledger, 1), "acked")
        self.claim_and_commit(ledger, 2, receipt="book-receipt", kind="book")

    def test_cross_kind_ack_does_not_resolve_the_appointment_barrier(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(ledger, 1, receipt="book-receipt", kind="book")
        for kind in ("create", "update", "reschedule", "checkin"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    ledger.acknowledge_barrier(
                        actor="doc@example.test",
                        kind=kind,
                        operation_key=key,
                        ack_receipt="ack:wrong-kind",
                    ),
                    "invalid",
                )
        with self.assertRaises(SiteLocked):
            self.new_key(ledger, number=2)
        self.assertEqual(self.acknowledge(ledger, 1, kind="book"), "acked")
        self.new_key(ledger, number=2)

    def test_unresolved_patient_key_blocks_a_checkin_but_not_enumeration(self):
        ledger = self.ledger()
        self.claim_and_commit(ledger, 1, receipt="patient-receipt", kind="create")
        self.assertEqual(ledger.committed_appointment_receipts(limit=10), [])
        with self.assertRaises(SiteLocked):
            with ledger.fence(
                actor="doc@example.test",
                kind="checkin",
                operation_key=self.operation_key(2),
                payload_digest=self.digest(2),
            ):
                self.fail("an unresolved patient key must block a check-in")


class AppointmentRestartTests(FenceTestCase):
    def test_crash_after_dispatch_keeps_the_appointment_barrier_unresolved(self):
        key = self.operation_key(1)
        digest = self.digest(1)
        self.run_child(
            CRASH_CHILD,
            str(self.directory),
            self.info.deployment_id,
            str(self.info.epoch),
            str(self.info.sequence),
            "doc@example.test",
            "book",
            key,
            digest,
            expect=17,
        )
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="book", operation_key=key
            ).state,
            "DISPATCHED",
        )
        barrier = write_fence.barrier_key("doc@example.test", "book", key)
        self.assertEqual(
            reopened.lookup(
                actor=write_fence.LATCH_ACTOR, kind="book", operation_key=barrier
            ).state,
            "DISPATCHED",
        )
        self.assertFalse(reopened.ready())
        self.assertEqual(reopened.committed_appointment_receipts(limit=10), [])
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                payload_digest=digest,
            ):
                self.fail("a crashed appointment dispatch must never replay")

    def test_committed_book_without_ack_stays_barred_across_restart(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(
            ledger, 1, receipt="book-receipt", kind="book"
        )
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertFalse(reopened.ready())
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="book", operation_key=key
            ).state,
            "COMMITTED",
        )
        self.assertEqual(
            reopened.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:book",
            ),
            "acked",
        )
        self.assertTrue(reopened.ready())

    def test_body_error_after_a_book_commit_leaves_it_committed_and_barred(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        with self.assertRaises(RuntimeError):
            with ledger.fence(
                actor="doc@example.test",
                kind="checkin",
                operation_key=key,
                payload_digest=self.digest(1),
            ) as claim:
                claim.commit("checkin-receipt")
                raise RuntimeError("response preparation failed after commit")
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        record = reopened.lookup(
            actor="doc@example.test", kind="checkin", operation_key=key
        )
        self.assertEqual(record.state, "COMMITTED")
        self.assertEqual(record.receipt, "checkin-receipt")
        self.assertFalse(reopened.ready())

    def test_receipt_append_failure_for_a_book_is_unknown_and_never_replayed(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        original = write_fence._append
        calls = {"count": 0}

        def flaky(connection, leader, **kwargs):
            calls["count"] += 1
            if calls["count"] == 3:
                raise sqlite3.OperationalError("disk I/O error")
            return original(connection, leader, **kwargs)

        with mock.patch.object(write_fence, "_append", flaky):
            with self.assertRaises(TerminalAppendFailed):
                with ledger.fence(
                    actor="doc@example.test",
                    kind="book",
                    operation_key=key,
                    payload_digest=digest,
                ) as claim:
                    self.claim = claim
                    claim.commit("book-receipt")
        self.assertEqual(self.claim.state, "UNKNOWN")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED"],
        )
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertFalse(reopened.ready())
        with self.assertRaises(SiteLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=self.operation_key(2),
                payload_digest=self.digest(2),
            ):
                self.fail("an unknown appointment receipt must block new work")


class AppointmentAckFailureTests(FenceTestCase):
    def test_ack_append_failure_keeps_the_barrier_unresolved(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(
            ledger, 1, receipt="book-receipt", kind="book"
        )
        with mock.patch.object(
            write_fence,
            "_append",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(StoreUnavailable):
                self.acknowledge(ledger, 1, kind="book")
        barrier = write_fence.barrier_key("doc@example.test", "book", key)
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor=write_fence.LATCH_ACTOR, kind="book", operation_key=barrier
            ).state,
            "DISPATCHED",
        )
        self.assertFalse(reopened.ready())
        self.assertEqual(self.acknowledge(ledger, 1, kind="book"), "acked")
        self.assertTrue(ledger.ready())

    def test_ack_receipt_must_match_the_recorded_acknowledgement(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(
            ledger, 1, receipt="book-receipt", kind="book"
        )
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:one",
            ),
            "acked",
        )
        sequence = ledger.info().sequence
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:one",
            ),
            "already",
        )
        self.assertEqual(
            ledger.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:different",
            ),
            "invalid",
        )
        self.assertEqual(ledger.info().sequence, sequence)


class AppointmentFaultBoundaryTests(FenceTestCase):
    """Persistence and crash boundaries around an appointment claim.

    The native database and this SQLite ledger are not atomic. These tests pin
    what the fence guarantees at each boundary: an unresolved barrier, no
    fabricated terminal and no automatic retry.
    """

    def barrier_state(self, ledger, kind, key):
        barrier = write_fence.barrier_key("doc@example.test", kind, key)
        return ledger.lookup(
            actor=write_fence.LATCH_ACTOR, kind=kind, operation_key=barrier
        )

    def test_dispatch_append_failure_leaves_no_claim(self):
        ledger = self.ledger()
        with mock.patch.object(
            write_fence,
            "_append",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            with self.assertRaises(StoreUnavailable):
                with ledger.fence(
                    actor="doc@example.test",
                    kind="book",
                    operation_key=self.operation_key(1),
                    payload_digest=self.digest(1),
                ):
                    self.fail("a dispatch that was not persisted must not yield")
        self.assertEqual(self.events(), [])
        self.assertIsNone(
            ledger.lookup(
                actor="doc@example.test",
                kind="book",
                operation_key=self.operation_key(1),
            )
        )
        self.assertTrue(ledger.ready())

    def test_barrier_append_failure_keeps_the_dispatch_locked(self):
        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        original = write_fence._append
        calls = {"count": 0}

        def failing_barrier(connection, leader, **kwargs):
            calls["count"] += 1
            if calls["count"] == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return original(connection, leader, **kwargs)

        with mock.patch.object(write_fence, "_append", failing_barrier):
            with self.assertRaises(StoreUnavailable):
                with ledger.fence(
                    actor="doc@example.test",
                    kind="book",
                    operation_key=key,
                    payload_digest=digest,
                ):
                    self.fail("a dispatch without its barrier must not yield")
        # The dispatch is durable, the barrier is not. The unresolved key still
        # blocks every actor, so the missing barrier cannot unblock the site.
        self.assertEqual([event[4] for event in self.events()], ["DISPATCHED"])
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="book", operation_key=key
            ).state,
            "DISPATCHED",
        )
        self.assertIsNone(self.barrier_state(reopened, "book", key))
        self.assertFalse(reopened.ready())
        self.assertEqual(reopened.committed_appointment_receipts(limit=10), [])
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                payload_digest=digest,
            ):
                self.fail("a persisted dispatch must never replay")
        with self.assertRaises(SiteLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=self.operation_key(2),
                payload_digest=self.digest(2),
            ):
                self.fail("the unresolved dispatch must block a new key")

    def test_native_commit_then_crash_before_receipt_stays_unresolved(self):
        key = self.operation_key(1)
        digest = self.digest(1)
        self.run_child(
            NATIVE_COMMIT_CRASH_CHILD,
            str(self.directory),
            self.info.deployment_id,
            str(self.info.epoch),
            str(self.info.sequence),
            "doc@example.test",
            "book",
            key,
            digest,
            expect=23,
        )
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="book", operation_key=key
            ).state,
            "DISPATCHED",
        )
        self.assertEqual(
            self.barrier_state(reopened, "book", key).state, "DISPATCHED"
        )
        self.assertFalse(reopened.ready())
        self.assertEqual(reopened.committed_appointment_receipts(limit=10), [])
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                payload_digest=digest,
            ):
                self.fail("a commit without a receipt must not replay")

    def test_rollback_failure_leaves_the_claim_dispatched_without_a_terminal(self):
        """The native transaction and its rollback belong to the task 3 layer.

        The fence cannot roll a native transaction back and must not pretend to:
        a body that raises, including a native rollback failure, keeps the key
        ``DISPATCHED`` and appends no terminal record.
        """

        class SimulatedRollbackError(RuntimeError):
            pass

        ledger = self.ledger()
        key = self.operation_key(1)
        digest = self.digest(1)
        with self.assertRaises(SimulatedRollbackError):
            with ledger.fence(
                actor="doc@example.test",
                kind="reschedule",
                operation_key=key,
                payload_digest=digest,
            ):
                raise SimulatedRollbackError("native rollback failed")
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED"],
        )
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        self.assertEqual(
            reopened.lookup(
                actor="doc@example.test", kind="reschedule", operation_key=key
            ).state,
            "DISPATCHED",
        )
        self.assertFalse(reopened.ready())
        with self.assertRaises(OperationLocked):
            with reopened.fence(
                actor="doc@example.test",
                kind="reschedule",
                operation_key=key,
                payload_digest=digest,
            ):
                self.fail("a failed body must never be marked or replayed")

    def test_ack_append_succeeds_then_raises_and_the_client_must_stop(self):
        ledger = self.ledger()
        key, _ = self.claim_and_commit(
            ledger, 1, receipt="book-receipt", kind="book"
        )
        original = write_fence._append

        def appended_then_raised(connection, leader, **kwargs):
            result = original(connection, leader, **kwargs)
            if kwargs["state"] == "COMMITTED":
                raise sqlite3.OperationalError("raised after durable append")
            return result

        with mock.patch.object(write_fence, "_append", appended_then_raised):
            with self.assertRaises(StoreUnavailable):
                self.acknowledge(ledger, 1, kind="book", receipt="ack:book")
        # The barrier is durably resolved even though the caller saw a failure.
        # That is exactly why the client must stop and not retry blindly.
        reopened = Ledger.open(self.directory, anchor=self.anchor)
        barrier = self.barrier_state(reopened, "book", key)
        self.assertEqual(barrier.state, "COMMITTED")
        self.assertEqual(barrier.receipt, "ack:book")
        self.assertTrue(reopened.ready())
        self.assertEqual(
            reopened.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:book",
            ),
            "already",
        )
        self.assertEqual(
            reopened.acknowledge_barrier(
                actor="doc@example.test",
                kind="book",
                operation_key=key,
                ack_receipt="ack:other",
            ),
            "invalid",
        )


class CrossKindConcurrencyTests(FenceTestCase):
    """Deterministic parent-controlled cross-kind contention.

    The parent starts a holder child of one kind. The holder acquires the
    interprocess fence, prints ``ready`` and blocks on its stdin. Only after
    the parent reads that line does it start a contender of the other kind, so
    the contender always meets a held lock; the parent then releases the holder
    with one stdin line. No sleep is used as synchronization and every wait is
    bounded, with both children killed if a step fails.
    """

    def start_claim_child(self, script, kind, key, digest, *, stdin):
        return subprocess.Popen(
            [
                sys.executable,
                "-c",
                script,
                str(self.directory),
                self.info.deployment_id,
                str(self.info.epoch),
                str(self.info.sequence),
                "doc@example.test",
                kind,
                key,
                digest,
            ],
            cwd=APP_ROOT,
            env=dict(os.environ, PYTHONPATH=str(APP_ROOT)),
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_simultaneous_patient_and_appointment_claims_admit_exactly_one(self):
        key = self.operation_key(1)
        digest = self.digest(1)
        holder = self.start_claim_child(
            HOLD_CHILD, "create", key, digest, stdin=subprocess.PIPE
        )
        contender = None
        try:
            readable, _, _ = select.select([holder.stdout], [], [], 30)
            self.assertTrue(readable, msg="holder never reported readiness")
            self.assertEqual(holder.stdout.readline().strip(), "ready")
            # The holder is inside the fence and blocked on stdin, so it holds
            # the interprocess lock for the whole contender attempt.
            contender = self.start_claim_child(
                CONTEND_CHILD, "book", key, digest, stdin=subprocess.DEVNULL
            )
            contender_output, contender_error = contender.communicate(timeout=30)
            self.assertEqual(
                contender.returncode,
                3,
                msg=f"stdout={contender_output!r} stderr={contender_error!r}",
            )
            self.assertEqual(contender_output.strip(), "LockUnavailable")
        finally:
            if holder.poll() is None:
                try:
                    holder.stdin.write("commit\n")
                    holder.stdin.flush()
                except (BrokenPipeError, ValueError, OSError):
                    pass
            try:
                holder_output, holder_error = holder.communicate(timeout=30)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder_output, holder_error = holder.communicate()
                self.fail(
                    f"holder hung: stdout={holder_output!r}"
                    f" stderr={holder_error!r}"
                )
            if contender is not None and contender.poll() is None:
                contender.kill()
                contender.communicate()
        self.assertEqual(
            holder.returncode,
            0,
            msg=f"stdout={holder_output!r} stderr={holder_error!r}",
        )
        self.assertEqual(
            [event[4] for event in self.events()],
            ["DISPATCHED", "DISPATCHED", "COMMITTED"],
        )
        ledger = self.ledger()
        winner = ledger.lookup(
            actor="doc@example.test", kind="create", operation_key=key
        )
        self.assertEqual(winner.state, "COMMITTED")
        self.assertEqual(winner.receipt, "native-receipt")
        self.assertIsNone(
            ledger.lookup(actor="doc@example.test", kind="book", operation_key=key)
        )


if __name__ == "__main__":
    unittest.main()
