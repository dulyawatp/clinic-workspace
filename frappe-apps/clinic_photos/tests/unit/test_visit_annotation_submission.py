from dataclasses import replace
import hashlib
import types

import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import journal, staging, validation
from test_visit_annotation_insert_guard import runtime
from test_visit_annotation_staging import intent


@pytest.fixture
def env(runtime, monkeypatch):
    frappe, controller = runtime
    item = intent()
    frappe.local.site = item.binding.site
    frappe.session.user = item.binding.actor
    frappe.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
    frappe.DuplicateEntryError = type("DuplicateEntryError", (Exception,), {})
    from clinic_photos.visit import annotation_authority

    events = []
    rows = {}
    contents = {}
    drafts = {}
    versions = {}
    sql_calls = []
    def sql(query, values, *, as_dict):
        sql_calls.append((query, values, as_dict))
        site, actor, draft, kind = values
        return [row.copy() for row in rows.values() if
                row.get("site", "").lower() == site.lower() and
                row.get("actor", "").lower() == actor.lower() and
                row.get("draft", "").lower() == draft.lower() and
                row.get("kind", "").lower() == kind.lower() and
                row.get("state", "").upper() in ("SUBMITTED", "PREPARED")][:2]
    def get_value(doctype, name, fields, *, as_dict, for_update):
        events.append(("draft_read" if doctype == "Clinic Photo Draft" else "version_read", name, for_update))
        row = (drafts if doctype == "Clinic Photo Draft" else versions).get(name)
        return row.copy() if row is not None else None
    frappe.db = types.SimpleNamespace(
        get_value=get_value, sql=sql, after_commit=types.SimpleNamespace(add=lambda callback: None))
    def authority(appointment, encounter, slot, *, expected):
        events.append(("authority", appointment, encounter, slot, expected))
        return expected
    monkeypatch.setattr(annotation_authority, "resolve_write_binding", authority)
    def stage(value):
        events.append(("stage",))
        data = value.canonical_visit_annotation.encode()
        ref = staging.StagedVisitAnnotation(
            "a" * 32 + ".json", hashlib.sha256(data).hexdigest(), len(data),
            "VISIT_ANNOTATION_JSON")
        contents[ref.key] = data
        return ref
    monkeypatch.setattr(staging, "stage_visit_annotation", stage)
    monkeypatch.setattr(staging, "read_staged_visit_annotation", lambda ref: contents[ref.key])
    def by_identity(identity, *, for_update=False):
        events.append(("identity", identity, for_update))
        return next((r.copy() for r in rows.values() if r["identity_digest"] == identity), None)
    def by_id(name, *, for_update=False):
        events.append(("read", name, for_update))
        row = rows.get(name)
        return row.copy() if row else None
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", by_identity)
    monkeypatch.setattr(frappe_context, "get_save_operation", by_id)

    class Doc(controller.ClinicPhotoSaveOperation):
        def __init__(self, data):
            self.data = dict(data)
            self.name = "1" * 32
        @property
        def kind(self):
            return self.data["kind"]
        def get(self, key):
            return self.data.get(key)
        def as_dict(self):
            return dict(self.data)
        def is_new(self):
            return True
        def insert(self, *, ignore_permissions):
            assert ignore_permissions is True
            events.append(("insert",))
            self.before_insert()
            self.validate()
            rows[self.name] = {"name": self.name, **{k: v for k, v in self.data.items() if k != "doctype"}}
            return self
    frappe.get_doc = Doc
    return types.SimpleNamespace(frappe=frappe, item=item, events=events, rows=rows, Doc=Doc, contents=contents, drafts=drafts, versions=versions, sql_calls=sql_calls)


@pytest.mark.parametrize("state,alias", [("SUBMITTED", False), ("PREPARED", False),
                                        ("SUBMITTED", True)])
def test_pending_other_key_current_read_refuses_before_stage(env, monkeypatch, state, alias):
    from clinic_photos.publication import visit_annotation_submission as submission
    submission.stage_visit_annotation_submission(env.item)
    env.rows["1" * 32]["state"] = state
    if alias:
        env.rows["1" * 32]["draft"] = env.item.managed_draft.upper()
    env.events.clear()
    env.contents.clear()
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", lambda *a, **k: None)
    other = replace(env.item, request_key="fedcba9876543210")
    with pytest.raises(submission.PendingVisitAnnotation):
        submission.stage_visit_annotation_submission(other)
    assert not env.contents
    assert not any(event[0] in ("stage", "insert") for event in env.events)
    query, values, as_dict = env.sql_calls[-1]
    assert "for update nowait" in " ".join(query.lower().split())
    assert values == (other.binding.site, other.binding.actor, other.managed_draft,
                      "visit_annotation_append")
    assert as_dict is True


@pytest.mark.parametrize("wrapped", [False, True])
def test_pending_lock_contention_fails_before_mutation(env, monkeypatch, wrapped):
    from clinic_photos.publication import visit_annotation_submission as submission
    class QueryTimeoutError(Exception):
        pass

    monkeypatch.setattr(env.frappe, "QueryTimeoutError", QueryTimeoutError, raising=False)

    def locked(*args, **kwargs):
        cause = RuntimeError(1205, "sensitive")
        if wrapped:
            raise QueryTimeoutError(cause) from cause
        raise cause
    monkeypatch.setattr(env.frappe.db, "sql", locked)
    with pytest.raises(submission.PendingVisitAnnotation) as caught:
        submission.stage_visit_annotation_submission(env.item)
    assert "sensitive" not in str(caught.value)
    assert not env.contents and not env.rows


def test_same_key_current_read_verifies_even_when_snapshot_is_stale(env, monkeypatch):
    from clinic_photos.publication import visit_annotation_submission as submission
    assert submission.stage_visit_annotation_submission(env.item) == "1" * 32
    env.events.clear()
    env.contents.clear()
    data = env.item.canonical_visit_annotation.encode()
    env.contents["a" * 32 + ".json"] = data
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity",
                        lambda *args, **kwargs: pytest.fail("snapshot read"))
    assert submission.stage_visit_annotation_submission(env.item) == "1" * 32
    assert [event[0] for event in env.events] == ["authority"]


def test_unexpected_sql_error_is_not_mapped_to_pending(env, monkeypatch):
    from clinic_photos.publication import visit_annotation_submission as submission
    def fail(*args, **kwargs):
        raise RuntimeError(9999, "database error")
    monkeypatch.setattr(env.frappe.db, "sql", fail)
    with pytest.raises(RuntimeError, match="database error"):
        submission.stage_visit_annotation_submission(env.item)
    assert not env.contents and not env.rows


def test_new_submission_runs_guard_and_verifies_persisted_row(env):
    from clinic_photos.publication import visit_annotation_submission as submission
    assert submission.stage_visit_annotation_submission(env.item) == "1" * 32
    assert [x[0] for x in env.events] == ["authority", "identity", "draft_read", "stage", "insert", "read"]
    row = env.rows["1" * 32]
    assert "annotation_json" not in row
    assert row["payload_digest"] == env.item.intent_digest
    assert all(not x[-1] for x in env.events if x[0] in ("identity", "read"))


def test_authority_denial_has_no_staging_or_write(env, monkeypatch):
    from clinic_photos.publication import visit_annotation_submission as submission
    from clinic_photos.visit import annotation_authority
    def deny(*args, **kwargs):
        raise env.frappe.PermissionError("denied")
    monkeypatch.setattr(annotation_authority, "resolve_write_binding", deny)
    with pytest.raises(env.frappe.PermissionError):
        submission.stage_visit_annotation_submission(env.item)
    assert env.events == []


@pytest.mark.parametrize("change", ["user", "site", "binding"])
def test_authority_result_and_session_rechecked_before_staging(env, monkeypatch, change):
    from clinic_photos.publication import visit_annotation_submission as submission
    from clinic_photos.visit import annotation_authority
    def resolve(*args, expected, **kwargs):
        if change == "user":
            env.frappe.session.user = "other"
        elif change == "site":
            env.frappe.local.site = "other"
        else:
            return replace(expected, original_photo="different")
        return expected
    monkeypatch.setattr(annotation_authority, "resolve_write_binding", resolve)
    with pytest.raises(journal.JournalAuthorityDenied):
        submission.stage_visit_annotation_submission(env.item)
    assert env.events == []


def test_session_change_during_identity_lookup_denies_before_staging(env, monkeypatch):
    from clinic_photos.publication import visit_annotation_submission as submission
    def lookup(identity, *, for_update=False):
        env.frappe.session.user = "other"
        return None
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", lookup)
    with pytest.raises(journal.JournalAuthorityDenied):
        submission.stage_visit_annotation_submission(env.item)
    assert [event[0] for event in env.events] == ["authority"]


@pytest.mark.parametrize("change", ["same", "head", "target", "content", "advanced", "corrupt"])
def test_existing_rows_are_verified_without_restaging(env, change):
    from clinic_photos.publication import visit_annotation_submission as submission
    submission.stage_visit_annotation_submission(env.item)
    env.events.clear()
    item = env.item
    if change == "head":
        item = replace(item, expected_version=1)
    elif change == "target":
        item = replace(item, binding=replace(item.binding, original_photo="different"))
    elif change == "content":
        content = validation.canonical_visit_annotation({"schema_version": 2, "coordinate_space": "normalized", "shapes": [
            {"id": "x", "type": "text", "color": "#123456", "x": 0, "y": 0, "text": "x", "font_size": 0.1}]})
        item = replace(item, canonical_visit_annotation=content)
    elif change == "advanced":
        env.rows["1" * 32]["state"] = "PREPARED"
    elif change == "corrupt":
        env.rows["1" * 32]["payload_digest"] = "corrupt"
    if change == "same":
        assert submission.stage_visit_annotation_submission(item) == "1" * 32
    else:
        expected = journal.JournalConflict if change in ("head", "target", "content") else journal.JournalIntegrityError
        with pytest.raises(expected):
            submission.stage_visit_annotation_submission(item)
    assert [x[0] for x in env.events] == (["authority", "identity"] if change == "target" else ["authority"])


@pytest.mark.parametrize("exception", ["UniqueValidationError", "DuplicateEntryError"])
def test_duplicate_insert_is_uncertain_without_recovery(env, monkeypatch, exception):
    from clinic_photos.publication import visit_annotation_submission as submission
    def insert(doc, *, ignore_permissions):
        env.events.append(("insert",))
        raise getattr(env.frappe, exception)("sensitive")
    monkeypatch.setattr(env.Doc, "insert", insert)
    with pytest.raises(submission.SubmissionUncertain) as caught:
        submission.stage_visit_annotation_submission(env.item)
    assert "sensitive" not in str(caught.value)
    assert [x[0] for x in env.events] == ["authority", "identity", "draft_read", "stage", "insert"]


@pytest.mark.parametrize("case", ["absent_nonzero", "stale", "alias", "author", "photo",
                                  "negative", "boolean", "string", "zero_pointer", "missing_pointer",
                                  "missing_version", "version_alias", "version_draft", "version_number",
                                  "zero_pointer_mismatch", "version_draft_mismatch"])
def test_invalid_live_head_refuses_before_stage(env, case):
    from clinic_photos.publication import visit_annotation_submission as submission
    item = replace(env.item, expected_version=1)
    name = item.managed_draft
    row = {"name": name, "author": item.binding.actor, "photo": item.binding.original_photo,
           "current_version_number": 1, "current_version": "version-1"}
    version = {"name": "version-1", "draft": name, "version_number": 1}
    if case != "absent_nonzero":
        env.drafts[name] = row
        env.versions["version-1"] = version
    if case == "stale": item = replace(item, expected_version=2)
    if case == "alias": row["name"] = name.upper()
    if case == "author": row["author"] = "wrong"
    if case == "photo": row["photo"] = "wrong"
    if case == "negative": row["current_version_number"] = -1
    if case == "boolean": row["current_version_number"] = True
    if case == "string": row["current_version_number"] = "1"
    if case in ("zero_pointer", "zero_pointer_mismatch"):
        row["current_version_number"] = 0
        if case == "zero_pointer": item = replace(item, expected_version=0)
    if case == "missing_pointer": row["current_version"] = None
    if case == "missing_version": env.versions.clear()
    if case == "version_alias": version["name"] = "VERSION-1"
    if case in ("version_draft", "version_draft_mismatch"):
        version["draft"] = "wrong"
        if case == "version_draft_mismatch": item = replace(item, expected_version=2)
    if case == "version_number": version["version_number"] = 2
    error = submission.StaleVisitAnnotation if case in ("absent_nonzero", "stale") else journal.JournalIntegrityError
    with pytest.raises(error) as caught:
        submission.stage_visit_annotation_submission(item)
    assert str(caught.value) == ("Visit photo annotation is stale" if case in ("absent_nonzero", "stale")
                                 else journal.SAFE_JOURNAL_INTEGRITY)
    if case in ("stale", "version_draft_mismatch"):
        assert ("version_read", "version-1", True) in env.events
    assert not any(event[0] in ("stage", "insert") for event in env.events)
    assert not env.rows


@pytest.mark.parametrize("number", [0, 1])
def test_matching_existing_head_allows_stage(env, number):
    from clinic_photos.publication import visit_annotation_submission as submission
    item = replace(env.item, expected_version=number)
    name = item.managed_draft
    env.drafts[name] = {"name": name, "author": item.binding.actor,
                        "photo": item.binding.original_photo,
                        "current_version_number": number,
                        "current_version": "version-1" if number else None}
    if number:
        env.versions["version-1"] = {"name": "version-1", "draft": name, "version_number": 1}
    assert submission.stage_visit_annotation_submission(item) == "1" * 32
    assert ("draft_read", name, True) in env.events


def test_same_key_reconciliation_never_rechecks_live_head(env):
    from clinic_photos.publication import visit_annotation_submission as submission
    submission.stage_visit_annotation_submission(env.item)
    env.events.clear()
    env.drafts[env.item.managed_draft] = {"name": "wrong"}
    assert submission.stage_visit_annotation_submission(env.item) == "1" * 32
    assert [event[0] for event in env.events] == ["authority"]


@pytest.mark.parametrize("field", ["name", "payload_digest", "staged_key"])
def test_persisted_row_mismatch_fails_closed(env, monkeypatch, field):
    from clinic_photos.publication import visit_annotation_submission as submission
    original = frappe_context.get_save_operation
    def bad(name, *, for_update=False):
        row = original(name, for_update=for_update)
        row[field] = "wrong"
        return row
    monkeypatch.setattr(frappe_context, "get_save_operation", bad)
    with pytest.raises(journal.JournalIntegrityError):
        submission.stage_visit_annotation_submission(env.item)


@pytest.mark.parametrize("context", ["user", "site"])
@pytest.mark.parametrize("phase", ["insert_lookup", "insert_decode", "replay_decode", "conflict_decode"])
def test_live_context_survives_authenticated_readback(env, monkeypatch, context, phase):
    from clinic_photos.publication import visit_annotation_submission as submission
    from clinic_photos.publication import visit_annotation_journal_codec as codec

    if phase.startswith(("replay", "conflict")):
        submission.stage_visit_annotation_submission(env.item)
        env.events.clear()
    item = replace(env.item, expected_version=1) if phase == "conflict_decode" else env.item

    def change_context():
        if context == "user":
            env.frappe.session.user = "other"
        else:
            env.frappe.local.site = "other"

    if phase == "insert_lookup":
        original = frappe_context.get_save_operation
        def lookup(name, *, for_update=False):
            row = original(name, for_update=for_update)
            change_context()
            return row
        monkeypatch.setattr(frappe_context, "get_save_operation", lookup)
        monkeypatch.setattr(codec, "load_submitted", lambda *a, **k: pytest.fail("decoded under wrong context"))
    else:
        original = codec.load_submitted
        def decode(*args, **kwargs):
            result = original(*args, **kwargs)
            change_context()
            return result
        monkeypatch.setattr(codec, "load_submitted", decode)
    with pytest.raises(journal.JournalAuthorityDenied):
        submission.stage_visit_annotation_submission(item)
