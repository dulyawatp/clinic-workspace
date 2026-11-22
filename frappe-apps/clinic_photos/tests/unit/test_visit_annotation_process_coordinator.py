import sys
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from clinic_photos.publication import exclusion
from clinic_photos.publication import visit_annotation_process as child

OPERATION = "a" * 32
ARGS = ["--site", "clinic.local", "--operation-id", OPERATION]


@pytest.fixture
def scoped(monkeypatch):
    events = []
    proof = object()
    owner = SimpleNamespace(site="clinic.local", actor="doctor", operation_id=OPERATION)
    frappe = SimpleNamespace(local=SimpleNamespace(site="clinic.local"),
                             session=SimpleNamespace(user="doctor"), PermissionError=PermissionError)
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    from clinic_photos.publication import visit_annotation_coordinator
    active = []

    @contextmanager
    def bootstrap(operation, *, site):
        events.append("bootstrap")
        active.append("bootstrap")
        try:
            yield proof
        finally:
            active.pop()
            events.append("bootstrap_exit")

    def consume(value, *, site, actor, operation_id):
        assert active == ["bootstrap"] and value is proof
        assert (site, actor, operation_id) == ("clinic.local", "doctor", OPERATION)
        events.append("consume")

    @contextmanager
    def hold(operation):
        assert active == ["bootstrap"] and operation == OPERATION
        active.append("exclusion")
        events.append("exclusion")
        try:
            yield owner
        finally:
            active.pop()
            events.append("exclusion_exit")

    def publish(actual):
        assert active == ["bootstrap", "exclusion"] and actual is owner
        events.append("publish")
        return visit_annotation_coordinator._Verified(OPERATION, "file", "version")

    monkeypatch.setattr(child.visit_annotation_child, "_bootstrap", bootstrap)
    monkeypatch.setattr(child.visit_annotation_child, "_consume", consume)
    monkeypatch.setattr(exclusion, "hold_visit_annotation_exclusion", hold)
    monkeypatch.setattr(visit_annotation_coordinator, "_publish_verified", publish)
    return events, owner, frappe


def test_verified_exit_only_after_consumption_and_scope_cleanup(scoped, capsys):
    events, _, _ = scoped
    assert child.main(ARGS) == 0
    assert events == ["bootstrap", "consume", "exclusion", "publish", "exclusion_exit", "bootstrap_exit"]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("failure", ["consume", "owner", "publish", "result", "cleanup"])
def test_refused_or_unknown_never_returns_verified(scoped, monkeypatch, failure, capsys):
    from clinic_photos.publication import visit_annotation_coordinator
    events, owner, _ = scoped
    def fail(*args, **kwargs):
        raise RuntimeError("private clinical content")
    if failure == "consume":
        monkeypatch.setattr(child.visit_annotation_child, "_consume", fail)
    elif failure == "owner":
        owner.actor = "other"
    elif failure == "publish":
        monkeypatch.setattr(visit_annotation_coordinator, "_publish_verified", fail)
    elif failure == "result":
        monkeypatch.setattr(visit_annotation_coordinator, "_publish_verified", lambda _: object())
    else:
        @contextmanager
        def hold(operation):
            yield owner
            raise RuntimeError("cleanup")
        monkeypatch.setattr(exclusion, "hold_visit_annotation_exclusion", hold)
    assert child.main(ARGS) == child.EXIT_UNAVAILABLE
    assert events.count("consume") <= 1 and events.count("publish") <= 1
    assert capsys.readouterr() == ("", "")
