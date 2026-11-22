from collections import deque
from types import SimpleNamespace

import pytest

from test_visit_annotation_submission import env
from test_visit_annotation_insert_guard import runtime


class Hooks:
    def __init__(self):
        self._functions = deque()

    def add(self, callback):
        self._functions.append(callback)

    def run(self):
        while self._functions:
            self._functions.popleft()()

    def clear(self):
        self._functions.clear()


@pytest.fixture
def handoff(env, monkeypatch):
    env.frappe.db.after_commit = Hooks()
    env.frappe.conf = {"clinic_visit_photo_annotations_enabled": True}
    jobs = []
    env.frappe.enqueue = lambda *args, **kwargs: jobs.append((args, kwargs))
    from clinic_photos.publication import launcher
    monkeypatch.setattr(launcher, "_visit_server_site", lambda frappe: frappe.local.site)
    return env, jobs


def test_only_new_verified_row_registers_one_after_commit(handoff):
    from clinic_photos.publication import visit_annotation_submission as submission
    env, jobs = handoff
    name = submission.stage_visit_annotation_submission(env.item)
    assert not jobs and len(env.frappe.db.after_commit._functions) == 1
    assert submission.stage_visit_annotation_submission(env.item) == name
    assert len(env.frappe.db.after_commit._functions) == 1
    callback = env.frappe.db.after_commit._functions[0]
    env.frappe.db.after_commit.run()
    callback()
    assert len(jobs) == 1
    assert jobs[0][0] == ("clinic_photos.publication.visit_annotation_dispatch._run",)
    assert jobs[0][1]["operation_id"] == name and jobs[0][1]["queue"] == "short"


def test_rollback_discards_handoff(handoff):
    from clinic_photos.publication import visit_annotation_submission as submission
    env, jobs = handoff
    submission.stage_visit_annotation_submission(env.item)
    env.frappe.db.after_commit.clear()
    assert jobs == []


def test_collision_has_no_handoff(handoff, monkeypatch):
    from clinic_photos.publication import visit_annotation_submission as submission
    env, jobs = handoff
    def fail(self, **kwargs):
        raise env.frappe.DuplicateEntryError()
    monkeypatch.setattr(env.Doc, "insert", fail)
    with pytest.raises(submission.SubmissionUncertain):
        submission.stage_visit_annotation_submission(env.item)
    assert not env.frappe.db.after_commit._functions and not jobs


def test_failed_callback_registration_cannot_schedule(handoff):
    from clinic_photos.publication import visit_annotation_submission as submission
    env, jobs = handoff
    def broken_add(callback):
        env.frappe.db.after_commit._functions.append(callback)
        raise RuntimeError("ambiguous registration")
    env.frappe.db.after_commit.add = broken_add
    with pytest.raises(submission.SubmissionUncertain):
        submission.stage_visit_annotation_submission(env.item)
    env.frappe.db.after_commit.run()
    assert not jobs


def test_failed_enqueue_never_retried(handoff):
    from clinic_photos.publication import visit_annotation_submission as submission
    env, jobs = handoff
    def fail(*args, **kwargs):
        jobs.append("attempt")
        raise RuntimeError("queue unavailable")
    env.frappe.enqueue = fail
    submission.stage_visit_annotation_submission(env.item)
    callback = env.frappe.db.after_commit._functions[0]
    env.frappe.db.after_commit.run()
    callback()
    assert jobs == ["attempt"]


@pytest.mark.parametrize("change", ["ok", "site", "gate", "kind", "state", "dirty"])
def test_worker_rechecks_exact_submission_and_clean_frame(handoff, monkeypatch, change):
    from clinic_photos.publication import visit_annotation_submission as submission
    from clinic_photos.publication import visit_annotation_dispatch as dispatch
    from clinic_photos.publication import visit_annotation_exec
    env, jobs = handoff
    name = submission.stage_visit_annotation_submission(env.item)
    env.frappe.db.after_commit.clear()
    for attr in ("before_commit", "before_rollback", "after_rollback"):
        setattr(env.frappe.db, attr, Hooks())
    env.frappe.db.transaction_writes = 0
    env.frappe.db._conn = object()
    env.frappe.local.db = env.frappe.db
    called = []
    monkeypatch.setattr(visit_annotation_exec, "_launch", lambda operation: called.append(operation))
    if change == "site":
        env.frappe.local.site = "other"
    elif change == "gate":
        env.frappe.conf["clinic_visit_photo_annotations_enabled"] = False
    elif change == "kind":
        env.rows[name]["kind"] = "annotation_append"
    elif change == "state":
        env.rows[name]["state"] = "PREPARED"
    elif change == "dirty":
        env.frappe.db.before_commit.add(lambda: None)
    assert dispatch._run(name) is None
    assert called == ([name] if change == "ok" else [])
