from types import SimpleNamespace

import pytest

from clinic_photos.publication import visit_transaction
from test_visit_transaction_guard import env


@pytest.fixture
def visit_file_scope(env):
    env[0].PermissionError = PermissionError
    from clinic_photos.publication import visit_file_scope
    return visit_file_scope


def file_doc():
    return SimpleNamespace(
        name="", file_url="/private/files/clinic-visit-annotation-test.json",
        file_name="clinic-visit-annotation-test.json", is_private=1, owner="doctor",
        content=b"data", attached_to_doctype=None, attached_to_name=None,
        attached_to_field=None, is_new=lambda: True,
    )


def setup_scope(env, monkeypatch):
    import hashlib
    from clinic_photos.publication import visit_annotation_prepare

    _, owner, _ = env
    object.__setattr__(owner, "_operation_id", "operation")
    object.__setattr__(owner, "_intent", object())
    object.__setattr__(owner, "_visit_prepared_values", {
        "reservation_key": "/private/files/clinic-visit-annotation-test.json",
        "expected_sha256": hashlib.sha256(b"data").hexdigest(),
        "expected_size": 4,
    })
    handle = object()
    calls = []
    reservation = visit_annotation_prepare._ContinuedReservation(
        owner.intent, owner.operation_id, 1,
        owner._visit_prepared_values["reservation_key"],
        owner._visit_prepared_values["expected_sha256"], 4,
    )
    def continued(actual, witness):
        calls.append((actual, witness))
        return reservation
    monkeypatch.setattr(visit_annotation_prepare, "continue_after_commit", continued)
    return owner, handle, calls


def test_scope_consumes_continuation_inline_and_exact_file_once(env, monkeypatch, visit_file_scope):
    owner, handle, calls = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            assert calls == [(owner, handle)]
            scope._claim(doc)
            with pytest.raises(visit_file_scope.FileScopeDenied):
                scope._claim(doc)


@pytest.mark.parametrize("change", ["object", "content", "url", "actor", "attachment",
                                    "decode", "overwrite", "ignore_existing_file_check"])
def test_scope_refuses_swaps_and_forced_modes(env, monkeypatch, change, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            candidate = doc
            kwargs = {}
            if change == "object":
                candidate = file_doc()
            elif change == "content":
                doc.content = b"evil"
            elif change == "url":
                doc.file_url = "/private/files/other"
            elif change == "actor":
                env[0].session.user = "other"
            elif change == "attachment":
                doc.attached_to_name = "foreign"
            else:
                kwargs[change] = True
            with pytest.raises(visit_file_scope.FileScopeDenied):
                scope._claim(candidate, **kwargs)
            assert guard.publication_aborted()


def test_scope_requires_publish_phase_before_continuation(env, monkeypatch, visit_file_scope):
    owner, handle, calls = setup_scope(env, monkeypatch)
    with visit_transaction._visit_transaction(owner) as guard:
        with pytest.raises(visit_file_scope.FileScopeDenied):
            with visit_file_scope._file_scope(guard, handle, file_doc()):
                pass
        assert calls == []
        assert guard.publication_aborted()


def test_scope_cannot_be_claimed_after_exit(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            with visit_file_scope._file_scope(guard, handle, doc) as scope:
                pass
        with pytest.raises(visit_file_scope.FileScopeDenied):
            scope._claim(doc)


def test_normal_unclaimed_exit_aborts(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with pytest.raises(visit_file_scope.FileScopeDenied):
            with visit_file_scope._file_scope(guard, handle, file_doc()):
                pass
        assert guard.publication_aborted()


def test_claim_checks_actual_save_input_and_returns_checked_bytes(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            checked = scope._claim(doc, content=b"data")
            doc.content = b"evil"
            assert checked == b"data"


def test_claim_rejects_substituted_save_input(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            with pytest.raises(visit_file_scope.FileScopeDenied):
                scope._claim(doc, content=b"evil")


def test_detached_constructed_scope_cannot_claim(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        forged = object.__new__(visit_file_scope._FileScope)
        with pytest.raises(visit_file_scope.FileScopeDenied):
            forged._claim(doc)


def test_reservation_snapshot_and_scope_are_not_writable(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            owner._visit_prepared_values["expected_sha256"] = "forged"
            with pytest.raises(AttributeError):
                scope._key = "forged"
            assert scope._claim(doc) == b"data"


def test_other_scope_cannot_claim_while_active(env, monkeypatch, visit_file_scope):
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = file_doc()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc) as scope:
            forged = object.__new__(visit_file_scope._FileScope)
            with pytest.raises(visit_file_scope.FileScopeDenied):
                forged._claim(doc)
            assert scope._claim(doc) == b"data"
