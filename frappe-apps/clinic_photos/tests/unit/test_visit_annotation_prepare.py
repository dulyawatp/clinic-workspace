import contextvars
import os
import types

import pytest

from clinic_photos.publication import exclusion, journal, staging
from clinic_photos.publication import visit_annotation_journal_codec as codec
from test_visit_annotation_staging import intent, site

_REAL_CONFLICT_CHECK = exclusion.frappe_context.prepared_key_conflicts


class Callbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, fn):
        self.callbacks.append(fn)

    def run(self):
        callbacks, self.callbacks = self.callbacks, []
        for fn in callbacks:
            fn()

    def reset(self):
        self.callbacks.clear()


class LocalProxy:
    def __init__(self, local):
        object.__setattr__(self, "_local", local)

    def __getattr__(self, name):
        return getattr(self._local.db, name)

    def __setattr__(self, name, value):
        setattr(self._local.db, name, value)


@pytest.fixture
def prepared_env(site, monkeypatch):
    item = intent()
    frappe = site.frappe
    frappe.session = types.SimpleNamespace(user=item.binding.actor)
    operation = "1" * 32
    staged = staging.stage_visit_annotation(item)
    row = {**codec.submitted_values(item, staged), "name": operation}
    writes = []
    monkeypatch.setattr(exclusion.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    monkeypatch.setattr(exclusion, "_read_operation", lambda *a, **kw: row.copy())
    monkeypatch.setattr(exclusion.frappe_context, "get_save_operation",
                        lambda name, **kw: row.copy())
    monkeypatch.setattr(exclusion.frappe_context, "prepared_key_conflicts", lambda key: False)
    from clinic_photos.visit import annotation_authority
    monkeypatch.setattr(annotation_authority, "resolve_write_binding",
                        lambda *a, expected: expected)
    def set_value(doctype, name, values, *, update_modified):
        assert update_modified is False
        frappe.db.after_commit.add(lambda: None)
        frappe.db.after_rollback.add(lambda: None)
        writes.append(values.copy())
        row.update(values)
    def sql(query, args):
        assert query.startswith("UPDATE `tabClinic Photo Save Operation` SET ")
        assert "WHERE `name` = %s AND `state` = %s" in query
        state, key, generation, digest, size, name, old_state = args
        frappe.db._cursor.rowcount = int(name == operation and row["state"] == old_state)
        if frappe.db._cursor.rowcount:
            values = dict(state=state, reservation_key=key, generation=generation,
                          expected_sha256=digest, expected_size=size)
            writes.append(values.copy())
            row.update(values)
    frappe.local.db = types.SimpleNamespace(
        get_value=lambda *a, **kw: None, set_value=set_value, sql=sql,
        _cursor=types.SimpleNamespace(rowcount=0), _conn=object(),
        after_commit=Callbacks(), after_rollback=Callbacks(),
    )
    frappe.db = LocalProxy(frappe.local)
    return types.SimpleNamespace(item=item, operation=operation, row=row, writes=writes,
                                 site=site, staged=staged, frappe=frappe)


def test_continuation_requires_commit_and_is_one_shot(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        env.frappe.db.after_commit.run()
        reservation = prepare.continue_after_commit(owner, handle)
        assert reservation.intent == env.item
        assert (reservation.operation, reservation.generation, reservation.key,
                reservation.digest, reservation.size) == (
                    env.operation, 1, env.row["reservation_key"],
                    env.row["expected_sha256"], env.row["expected_size"])
        with pytest.raises(AttributeError):
            reservation.key = "forged"
        with pytest.raises((journal.JournalError, exclusion.ExclusionDenied)):
            prepare.continue_after_commit(owner, handle)


def test_continuation_preserves_owner_intent_identity_for_file_permission(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        decoded = codec.load_submitted(env.row.copy(), site=owner.site, actor=owner.actor)
        assert decoded == owner.intent
        assert decoded is not owner.intent
        handle = prepare.prepare(owner)
        env.frappe.db.after_commit.run()
        reservation = prepare.continue_after_commit(owner, handle)
        # Native File create permission requires this exact owner identity.
        assert reservation.intent is owner.intent


@pytest.mark.parametrize("change", ["precommit", "forged", "actor", "site", "row",
                                    "bytes", "binding", "head"])
def test_continuation_refuses_invalid_evidence(prepared_env, monkeypatch, change):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    from clinic_photos.visit import annotation_authority
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        if change != "precommit":
            env.frappe.db.after_commit.run()
        if change == "forged":
            handle = object()
        elif change == "actor":
            env.frappe.session.user = "other"
        elif change == "site":
            env.frappe.local.site = "other"
        elif change == "row":
            env.row["generation"] = 2
        elif change == "bytes":
            with open(os.path.join(env.site.staging_dir, env.staged.key), "ab") as file:
                file.write(b"x")
        elif change == "binding":
            monkeypatch.setattr(annotation_authority, "resolve_write_binding",
                                lambda *a, expected: None)
        elif change == "head":
            env.frappe.db.get_value = lambda *a, **kw: {
                "name": env.item.managed_draft, "author": env.item.binding.actor,
                "photo": env.item.binding.original_photo, "current_version_number": 1,
                "current_version": "v",
            }
        with pytest.raises((journal.JournalError, exclusion.ExclusionDenied)):
            prepare.continue_after_commit(owner, handle)


def test_new_owner_cannot_use_old_handle(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    before = env.row.copy()
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        env.frappe.db.after_commit.run()
    env.row.clear()
    env.row.update(before)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as new_owner:
        with pytest.raises((journal.JournalError, exclusion.ExclusionDenied)):
            prepare.continue_after_commit(new_owner, handle)


def test_precommit_refusal_burns_handle(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        with pytest.raises(journal.JournalError):
            prepare.continue_after_commit(owner, handle)
        env.frappe.db.after_commit.run()
        with pytest.raises(journal.JournalError):
            prepare.continue_after_commit(owner, handle)


def test_other_context_burns_handle(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        env.frappe.db.after_commit.run()
        with pytest.raises(exclusion.ExclusionDenied):
            contextvars.copy_context().run(prepare.continue_after_commit, owner, handle)
        with pytest.raises(journal.JournalError):
            prepare.continue_after_commit(owner, handle)


def test_new_owner_cannot_enter_prepared(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        prepare.prepare(owner)
        env.frappe.db.after_commit.run()
    with pytest.raises(exclusion.ExclusionDenied):
        with exclusion.hold_visit_annotation_exclusion(env.operation):
            pass


@pytest.mark.parametrize("savepoint", [True, False])
def test_rollback_then_unrelated_commit_never_continues_or_reprepares(prepared_env, savepoint):
    from clinic_photos.publication import visit_annotation_prepare as prepare
    env = prepared_env
    before = env.row.copy()
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        handle = prepare.prepare(owner)
        env.row.clear()
        env.row.update(before)
        if not savepoint:
            env.frappe.db.after_commit.reset()
            env.frappe.db.after_rollback.run()
        with pytest.raises((journal.JournalError, exclusion.ExclusionDenied)):
            prepare.prepare(owner)
        env.frappe.db.after_commit.run()
        with pytest.raises((journal.JournalError, exclusion.ExclusionDenied)):
            prepare.continue_after_commit(owner, handle)


def test_prepare_only_stages_exact_metadata_and_caller_owns_rollback(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    before = env.row.copy()
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        visit_annotation_prepare.prepare(owner)
        assert len(env.frappe.db.after_commit.callbacks) == 1
        assert len(env.frappe.db.after_rollback.callbacks) == 1
        assert env.row["state"] == "PREPARED"
        assert env.row["generation"] == 1
        assert env.row["expected_sha256"] == env.staged.sha256
        assert env.row["expected_size"] == env.staged.size
        assert env.row["reservation_key"].startswith("/private/files/clinic-visit-annotation-")
        assert env.row["reservation_key"].endswith(".json")
        assert set(env.writes[0]) == {"state", "reservation_key", "generation", "expected_sha256", "expected_size"}
        with pytest.raises(journal.JournalError):
            visit_annotation_prepare.prepare(owner)
    env.row.clear()
    env.row.update(before)  # caller rollback simulation
    assert env.row["state"] == "SUBMITTED"


def test_forged_owner_never_authorizes(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare
    class Forged:
        def require_live(self):
            return self
    with pytest.raises(exclusion.ExclusionDenied):
        visit_annotation_prepare.prepare(Forged())
    assert not prepared_env.writes


def test_prepare_conditional_update_must_match_one_row(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    env.frappe.db.sql = lambda *args: setattr(env.frappe.db._cursor, "rowcount", 0)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with pytest.raises(journal.JournalIntegrityError):
            visit_annotation_prepare.prepare(owner)
    assert not env.writes
    assert not env.frappe.db.after_commit.callbacks
    assert not env.frappe.db.after_rollback.callbacks


@pytest.mark.parametrize("change", ["actor", "site", "row", "bytes", "head", "binding", "conflict"])
def test_prepare_fails_closed_before_mutation(prepared_env, monkeypatch, change):
    from clinic_photos.publication import visit_annotation_prepare
    from clinic_photos.visit import annotation_authority
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        if change == "actor":
            env.frappe.session.user = "other"
        elif change == "site":
            env.frappe.local.site = "other"
        elif change == "row":
            env.row["payload_digest"] = "0" * 64
        elif change == "bytes":
            with open(os.path.join(env.site.staging_dir, env.staged.key), "ab") as file:
                file.write(b"x")
        elif change == "head":
            env.frappe.db.get_value = lambda *a, **kw: {
                "name": env.item.managed_draft, "author": env.item.binding.actor,
                "photo": env.item.binding.original_photo, "current_version_number": 1,
                "current_version": "v",
            }
        elif change == "binding":
            monkeypatch.setattr(annotation_authority, "resolve_write_binding",
                                lambda *a, expected: None)
        else:
            monkeypatch.setattr(exclusion.frappe_context, "prepared_key_conflicts", lambda key: True)
        error = {
            "actor": journal.JournalAuthorityDenied,
            "site": (journal.JournalAuthorityDenied, exclusion.ExclusionDenied),
            "row": journal.JournalIntegrityError,
            "bytes": journal.JournalIntegrityError,
            "head": journal.JournalIntegrityError,
            "binding": journal.JournalAuthorityDenied,
            "conflict": journal.JournalIntegrityError,
        }[change]
        with pytest.raises(error):
            visit_annotation_prepare.prepare(owner)
        assert not env.writes


def test_expired_owner_denied(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare
    with exclusion.hold_visit_annotation_exclusion(prepared_env.operation) as owner:
        pass
    with pytest.raises(exclusion.ExclusionDenied):
        visit_annotation_prepare.prepare(owner)
    assert not prepared_env.writes


def test_readback_tamper_requires_caller_rollback(prepared_env):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    original = env.frappe.db.sql
    def corrupt(*args):
        original(*args)
        env.row["expected_size"] += 1
    env.frappe.db.sql = corrupt
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with pytest.raises(journal.JournalIntegrityError):
            visit_annotation_prepare.prepare(owner)
    assert len(env.writes) == 1


@pytest.mark.parametrize("change", ["actor", "owner"])
def test_live_context_and_owner_rechecked_after_conflict_check(prepared_env, monkeypatch, change):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        def check(key):
            if change == "actor":
                env.frappe.session.user = "other"
            else:
                object.__setattr__(owner, "_poisoned", True)
            return False
        monkeypatch.setattr(exclusion.frappe_context, "prepared_key_conflicts", check)
        error = journal.JournalAuthorityDenied if change == "actor" else exclusion.ExclusionPoisoned
        with pytest.raises(error):
            visit_annotation_prepare.prepare(owner)
        assert not env.writes


@pytest.mark.parametrize("when", ["update", "readback"])
@pytest.mark.parametrize("change", ["actor", "owner"])
def test_readback_never_decodes_after_live_context_or_owner_drift(prepared_env, monkeypatch, when, change):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    reads = []
    original_read = staging.read_staged_visit_annotation
    monkeypatch.setattr(staging, "read_staged_visit_annotation",
                        lambda ref: reads.append(ref) or original_read(ref))
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        reads.clear()
        def drift():
            if change == "actor":
                env.frappe.session.user = "other"
            else:
                object.__setattr__(owner, "_poisoned", True)
        original_update = env.frappe.db.sql
        def update(*args, **kwargs):
            original_update(*args, **kwargs)
            if when == "update":
                drift()
        env.frappe.db.sql = update
        original_lookup = exclusion.frappe_context.get_save_operation
        def lookup(*args, **kwargs):
            result = original_lookup(*args, **kwargs)
            if when == "readback" and env.writes:
                drift()
            return result
        monkeypatch.setattr(exclusion.frappe_context, "get_save_operation", lookup)
        error = journal.JournalAuthorityDenied if change == "actor" else exclusion.ExclusionPoisoned
        with pytest.raises(error):
            visit_annotation_prepare.prepare(owner)
        assert len(reads) == 1  # initial SUBMITTED authentication only
        assert len(env.writes) == 1  # caller must roll back


@pytest.mark.parametrize("entry", ["file", "dangling", "directory"])
def test_confined_target_recognizes_occupied_final_entry(site, entry):
    directory = os.path.join(site.site, "private", "files")
    os.makedirs(directory)
    path = os.path.join(directory, "clinic-visit-annotation-test.json")
    if entry == "file":
        with open(path, "wb") as file:
            file.write(b"occupied")
    elif entry == "dangling":
        os.symlink("missing.json", path)
    else:
        os.mkdir(path)
    assert exclusion.frappe_context._confined_target_exists(
        "/private/files/clinic-visit-annotation-test.json")


def test_confined_target_lstat_failure_closed(site, monkeypatch):
    directory = os.path.join(site.site, "private", "files")
    os.makedirs(directory)
    monkeypatch.setattr(exclusion.frappe_context.os, "lstat",
                        lambda path: (_ for _ in ()).throw(PermissionError("denied")))
    assert exclusion.frappe_context._confined_target_exists(
        "/private/files/clinic-visit-annotation-test.json")


def test_confined_target_missing_entry_is_free(site):
    os.makedirs(os.path.join(site.site, "private", "files"))
    assert not exclusion.frappe_context._confined_target_exists(
        "/private/files/clinic-visit-annotation-test.json")


@pytest.mark.parametrize("kind", ["bytes", "file", "reservation", "version"])
def test_json_reservation_conflicts_use_real_checker(prepared_env, monkeypatch, kind):
    from clinic_photos.publication import visit_annotation_prepare
    env = prepared_env
    def check(key):
        if kind == "bytes":
            directory = os.path.join(env.site.site, "private", "files")
            os.makedirs(directory, exist_ok=True)
            with open(os.path.join(directory, key.rsplit("/", 1)[-1]), "wb") as file:
                file.write(b"existing")
        def get_all(doctype, *, fields, limit, filters=None):
            if kind == "file" and doctype == "File":
                return [{"name": "F", "file_url": key}]
            if kind == "reservation" and doctype == journal.OPERATION_DOCTYPE:
                return [{"name": "other", "draft": "", "state": "PREPARED", "reservation_key": key}]
            if kind == "version" and doctype == "Clinic Photo Annotation Version":
                return [{"name": "other", "draft": "", "state": "committed", "storage_key": key}]
            return []
        env.frappe.db.get_all = get_all
        return _REAL_CONFLICT_CHECK(key)
    monkeypatch.setattr(exclusion.frappe_context, "prepared_key_conflicts", check)
    with exclusion.hold_visit_annotation_exclusion(env.operation) as owner:
        with pytest.raises(journal.JournalIntegrityError):
            visit_annotation_prepare.prepare(owner)
    assert not env.writes
