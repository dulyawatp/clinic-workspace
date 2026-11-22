from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from clinic_photos.publication import launcher
from clinic_photos.publication import visit_annotation_exec as entry
from clinic_photos.publication import visit_annotation_process as child
from test_visit_annotation_child import env


OPERATION = "a" * 32


@pytest.fixture
def parent(monkeypatch):
    frappe = SimpleNamespace(
        local=SimpleNamespace(site="clinic.local"),
        conf={"clinic_visit_photos_enabled": True,
              "clinic_visit_photos_site": "clinic.local",
              "clinic_visit_photo_annotations_enabled": True},
    )
    monkeypatch.setattr(launcher, "_frappe", lambda: frappe)
    calls = []
    monkeypatch.setattr(launcher, "_execute_child", lambda *a, **kw: calls.append((a, kw)))
    return frappe, calls


def test_fixed_private_exec_uses_server_site_and_discards_output(parent):
    frappe, calls = parent
    assert entry._launch(OPERATION) is None
    assert calls == [(([launcher.PYTHON_EXECUTABLE, "-m",
                        "clinic_photos.publication.visit_annotation_process",
                        "--site", "clinic.local", "--operation-id", OPERATION],), {})]


@pytest.mark.parametrize("output", [
    (0, False, b"status=completed", b"secret"),
    (0, False, b"saved", b""),
    (None, True, b"", b""),
])
def test_any_child_result_cannot_report_saved(parent, monkeypatch, capsys, output):
    monkeypatch.setattr(launcher, "_execute_child", lambda *a, **kw: output)
    assert entry._launch(OPERATION) is None
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("operation", ["", "A" * 32, "x" * 32, None, "a" * 33])
def test_bad_operation_never_executes(parent, operation):
    assert entry._launch(operation) is None
    assert parent[1] == []


def test_parent_annotation_gate_refuses(parent):
    parent[0].conf["clinic_visit_photo_annotations_enabled"] = False
    assert entry._launch(OPERATION) is None
    assert parent[1] == []


@pytest.mark.parametrize("args", [
    [], ["--site", "clinic.local", "--operation-id"], ["--operation-id", OPERATION, "--site", "clinic.local"],
    ["--site", "../bad", "--operation-id", OPERATION],
    ["--site", "clinic.local", "--operation-id", "A" * 32],
    ["--site", "clinic.local", "--operation-id", OPERATION, "--actor", "doctor"],
])
def test_child_invalid_argv_never_bootstraps(monkeypatch, args, capsys):
    monkeypatch.setattr(child.visit_annotation_child, "_bootstrap",
                        lambda *a, **kw: pytest.fail("bootstrap"))
    assert child.main(args) == child.EXIT_REFUSED
    assert capsys.readouterr() == ("", "")


def test_child_bootstraps_once_and_refuses_after_cleanup(monkeypatch, capsys):
    events = []
    @contextmanager
    def bootstrap(operation_id, *, site):
        events.append(("enter", operation_id, site))
        try:
            yield object()
        finally:
            events.append(("cleanup",))
    monkeypatch.setattr(child.visit_annotation_child, "_bootstrap", bootstrap)
    assert child.main(["--site", "clinic.local", "--operation-id", OPERATION]) == child.EXIT_UNAVAILABLE
    assert events == [("enter", OPERATION, "clinic.local"), ("cleanup",)]
    assert capsys.readouterr() == ("", "")


def test_child_failure_suppresses_diagnostics(monkeypatch, capsys):
    @contextmanager
    def bootstrap(*args, **kwargs):
        raise RuntimeError("private payload")
        yield
    monkeypatch.setattr(child.visit_annotation_child, "_bootstrap", bootstrap)
    assert child.main(["--site", "clinic.local", "--operation-id", OPERATION]) == child.EXIT_UNAVAILABLE
    assert capsys.readouterr() == ("", "")


def test_child_real_bootstrap_stub_lifecycle_cleans(env, capsys):
    frappe, db, events, row = env
    assert child.main(["--site", "clinic", "--operation-id", OPERATION]) == child.EXIT_UNAVAILABLE
    assert events == ["init", "connect", "verify", "set_user", "destroy"]
    assert capsys.readouterr() == ("", "")
