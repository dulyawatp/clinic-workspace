import importlib
from dataclasses import replace
from unittest.mock import Mock

import pytest

from clinic_photos.api.photo_save import require_post, require_native_session
from test_visit_photos_api import env
from test_visit_annotation_staging import intent


@pytest.fixture
def setup(env, monkeypatch):
    from types import SimpleNamespace
    env.frappe.flags = SimpleNamespace(mute_messages=False)
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test",
                           clinic_visit_photo_annotations_enabled=True)
    env.frappe.local.conf = env.frappe.conf
    api = importlib.import_module("clinic_photos.api.visit_annotations")
    from clinic_photos.visit import annotation_authority
    from clinic_photos.publication import visit_annotation_submission
    item = intent()
    binding = replace(item.binding, site="site.test", actor="doctor")
    resolver = Mock(return_value=binding)
    stage = Mock(return_value="private-operation-id")
    monkeypatch.setattr(annotation_authority, "resolve_write_binding", resolver)
    monkeypatch.setattr(visit_annotation_submission, "stage_visit_annotation_submission", stage)
    payload = dict(appointment=binding.appointment, encounter=binding.encounter, slot=binding.slot,
                   request_key=item.request_key, expected_version=0,
                   annotation={"schema_version": 2, "coordinate_space": "normalized", "shapes": []})
    return env, api, payload, resolver, stage


def test_submit_uses_binding_and_returns_only_pending(setup):
    env, api, payload, resolver, stage = setup
    assert api.submit(payload) == {"state": "pending"}
    resolver.assert_called_once_with(payload["appointment"], payload["encounter"], payload["slot"])
    submitted = stage.call_args.args[0]
    assert submitted.binding == resolver.return_value
    assert submitted.request_key == payload["request_key"] and submitted.expected_version == 0
    assert submitted.canonical_visit_annotation == '{"coordinate_space":"normalized","schema_version":2,"shapes":[]}'
    env.post.assert_called_once()
    env.session.assert_called_once()


def test_annotation_routes_register_only_authenticated_post(setup, monkeypatch):
    env, api, *_ = setup
    registrations = []

    def whitelist(**options):
        def register(fn):
            registrations.append((fn.__name__, options))
            return fn
        return register

    monkeypatch.setattr(env.frappe, "whitelist", whitelist)
    api._register_site_contract()
    assert registrations == [(name, {"methods": ["POST"]}) for name in ("read", "status", "submit")]


@pytest.mark.parametrize("change", ["extra", "missing", "site", "actor", "photo", "path", "bad_version",
                                    "bool_version", "key", "annotation", "slot", "encounter", "cmd"])
def test_invalid_payload_rejected_before_authority_or_stage(setup, change):
    _, api, payload, resolver, stage = setup
    if change in ("extra", "site", "actor", "photo", "path"):
        payload[change] = "bad"
    elif change == "missing":
        del payload["slot"]
    elif change in ("bad_version", "bool_version"):
        payload["expected_version"] = -1 if change == "bad_version" else True
    elif change == "key":
        payload["request_key"] = "bad"
    elif change == "annotation":
        payload["annotation"] = {"shapes": []}
    elif change == "slot":
        payload["slot"] = "other"
    elif change == "encounter":
        payload["encounter"] = " invalid "
    if change == "cmd":
        result = api.submit(payload, cmd="bad")
    else:
        result = api.submit(payload)
    assert result == {"state": "rejected"}
    resolver.assert_not_called()
    stage.assert_not_called()


@pytest.mark.parametrize("value", [None, False, 1, "true"])
def test_annotation_gate_exact_boolean(setup, value):
    env, api, payload, resolver, stage = setup
    env.frappe.conf["clinic_visit_photo_annotations_enabled"] = value
    with pytest.raises(PermissionError):
        api.submit(payload)
    resolver.assert_not_called()
    stage.assert_not_called()


def test_stale_is_safe_but_uncertain_raises_for_rollback(setup):
    _, api, payload, _, stage = setup
    from clinic_photos.publication import visit_annotation_submission
    stage.side_effect = visit_annotation_submission.StaleVisitAnnotation()
    assert api.submit(payload) == {"state": "stale"}
    stage.side_effect = visit_annotation_submission.PendingVisitAnnotation()
    assert api.submit(payload) == {"state": "rejected"}
    stage.side_effect = visit_annotation_submission.SubmissionUncertain()
    with pytest.raises(Exception) as error:
        api.submit(payload)
    assert "private-operation-id" not in str(error.value)


def test_authority_denial_never_stages(setup):
    _, api, payload, resolver, stage = setup
    resolver.side_effect = PermissionError("sensitive")
    assert api.submit(payload) == {"state": "rejected"}
    stage.assert_not_called()


def test_rpc_cmd_is_exact_and_module_is_not_whitelisted(setup):
    env, api, payload, _, _ = setup
    cmd = "clinic_photos.api.visit_annotations.submit"
    env.frappe.local.form_dict = {"cmd": cmd}
    assert api.submit(payload, cmd=cmd) == {"state": "pending"}
    assert api.submit(payload, cmd=cmd + "wrong") == {"state": "rejected"}
    assert not getattr(api.submit, "is_whitelisted", False)


@pytest.mark.parametrize("bad", ["get", "csrf", "authorization", "cookie", "guest"])
def test_real_native_request_gate_rejects_before_authority(setup, monkeypatch, bad):
    from types import SimpleNamespace
    env, api, payload, resolver, stage = setup
    monkeypatch.setattr(api.photo_save, "require_post", require_post)
    monkeypatch.setattr(api.photo_save, "require_native_session", require_native_session)
    env.frappe.local.request = SimpleNamespace(method="GET" if bad == "get" else "POST",
                                               cookies={"sid": "wrong" if bad == "cookie" else "session"})
    env.frappe.session.sid = "session"
    env.frappe.session.data = {"csrf_token": "token"}
    if bad == "guest":
        env.frappe.session.user = "Guest"
    env.frappe.get_request_header = lambda name: (
        "token" if name == "Authorization" and bad == "authorization" else
        ("wrong" if bad == "csrf" else "token") if name == "X-Frappe-CSRF-Token" else None)
    with pytest.raises(PermissionError):
        api.submit(payload)
    resolver.assert_not_called()
    stage.assert_not_called()


@pytest.fixture
def read_setup(setup, monkeypatch):
    from types import SimpleNamespace
    from clinic_photos.access.draft_identity import visit_draft_name
    from clinic_photos.visit import annotation_read
    from clinic_photos.visit import annotation_authority
    env, api, payload, resolver, stage = setup
    binding = resolver.return_value
    monkeypatch.setattr(annotation_authority, "preview_write_binding", resolver, raising=False)
    target = SimpleNamespace(
        **vars(binding), draft_name=visit_draft_name(
            binding.site, binding.appointment, binding.slot, binding.original_photo, binding.actor),
        version_number=0,
    )
    monkeypatch.setattr(annotation_read, "resolve_read_target", lambda *a: target, raising=False)
    monkeypatch.setattr(annotation_read, "read_committed",
                        Mock(return_value=SimpleNamespace(version_number=0, annotation=None)))
    env.frappe.db = SimpleNamespace(
        get_all=lambda *a, **kw: [], sql=lambda *a, **kw: [(binding.encounter,)])
    return env, api, payload, resolver, target, annotation_read


def test_blank_read_is_authorized_and_can_write_from_live_binding(read_setup):
    _, api, payload, resolver, _, _ = read_setup
    assert api.read({"appointment": payload["appointment"], "slot": payload["slot"]}) == {
        "version": 0, "annotation": None, "can_write": True, "pending_request_key": None,
    }
    resolver.assert_called_once_with(payload["appointment"], payload["encounter"], payload["slot"])


def test_revoked_read_stays_available_without_write(read_setup):
    _, api, payload, resolver, _, _ = read_setup
    resolver.side_effect = PermissionError("revoked")
    assert api.read({"appointment": payload["appointment"], "slot": payload["slot"]}) == {
        "version": 0, "annotation": None, "can_write": False, "pending_request_key": None,
    }


def test_read_rejects_extra_client_authority(read_setup):
    _, api, payload, _, _, _ = read_setup
    with pytest.raises((PermissionError, ValueError)):
        api.read({"appointment": payload["appointment"], "slot": payload["slot"], "actor": "other"})


def test_read_version_passes_exact_positive_version(read_setup):
    from types import SimpleNamespace
    _, api, payload, _, _, annotation_read = read_setup
    annotation_read.read_committed.return_value = SimpleNamespace(version_number=2, annotation={"shapes": []})
    result = api.read({"appointment": payload["appointment"], "slot": payload["slot"], "version": 2})
    assert result["version"] == 2
    annotation_read.read_committed.assert_called_once_with(payload["appointment"], payload["slot"], version=2)


@pytest.mark.parametrize("version", [None, 0, -1, True, 1.5, "2"])
def test_read_rejects_invalid_explicit_version(read_setup, version):
    _, api, payload, _, _, annotation_read = read_setup
    with pytest.raises((PermissionError, ValueError)):
        api.read({"appointment": payload["appointment"], "slot": payload["slot"], "version": version})
    annotation_read.read_committed.assert_not_called()


@pytest.mark.parametrize("method", ["read", "status", "submit"])
@pytest.mark.parametrize("denied", [False, True])
def test_json_responses_and_denials_are_no_store(read_setup, method, denied):
    from types import SimpleNamespace
    from clinic_photos.access import entrypoints
    env, api, payload, _, _, _ = read_setup
    if denied:
        env.frappe.conf["clinic_visit_photo_annotations_enabled"] = False
    arguments = (payload if method == "submit" else
                 {"appointment": payload["appointment"], "slot": payload["slot"],
                  **({"request_key": payload["request_key"]} if method == "status" else {})})
    if denied:
        with pytest.raises(PermissionError):
            getattr(api, method)(arguments)
    else:
        getattr(api, method)(arguments)
    response = SimpleNamespace(headers={"Cache-Control": "public", "Age": "10"})
    entrypoints.after_request(None, response)
    assert response.headers == {"Cache-Control": "no-store", "Pragma": "no-cache", "Expires": "0"}


@pytest.mark.parametrize("prior", [False, True])
@pytest.mark.parametrize("method", ["read", "submit"])
def test_caught_authority_denial_mutes_framework_messages_and_restores_flag(read_setup, prior, method):
    env, api, payload, resolver, _, _ = read_setup
    env.frappe.flags.mute_messages = prior
    messages = []
    def deny(*args, **kwargs):
        if not env.frappe.flags.mute_messages:
            messages.append("sensitive")
        raise PermissionError("denied")
    resolver.side_effect = deny
    result = (api.read({"appointment": payload["appointment"], "slot": payload["slot"]})
              if method == "read" else api.submit(payload))
    assert result["can_write"] is False if method == "read" else result == {"state": "rejected"}
    assert messages == [] and env.frappe.flags.mute_messages is prior


def test_missing_message_flag_fails_closed(read_setup):
    env, api, payload, resolver, _, _ = read_setup
    del env.frappe.flags
    assert api.submit(payload) == {"state": "rejected"}
    assert api.read({"appointment": payload["appointment"], "slot": payload["slot"]})["can_write"] is False
    resolver.assert_not_called()


def test_status_caught_target_denial_does_not_pollute_success_envelope(read_setup, monkeypatch):
    env, api, payload, _, _, _ = read_setup
    messages = []
    def deny(*args):
        if not env.frappe.flags.mute_messages:
            messages.append("sensitive")
        raise PermissionError("denied")
    monkeypatch.setattr(api, "_target", deny)
    assert api.status({"appointment": payload["appointment"], "slot": payload["slot"],
                       "request_key": payload["request_key"]}) == {"state": "uncertain"}
    assert messages == [] and env.frappe.flags.mute_messages is False


def test_status_unknown_key_never_claims_committed(read_setup, monkeypatch):
    from clinic_photos.access import frappe_context
    _, api, payload, _, _, _ = read_setup
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", lambda *a, **kw: None)
    assert api.status({"appointment": payload["appointment"], "slot": payload["slot"],
                       "request_key": payload["request_key"]}) == {"state": "uncertain"}


@pytest.fixture
def pending_row(read_setup, monkeypatch, tmp_path):
    import os
    from clinic_photos.access import frappe_context
    from clinic_photos.publication import staging
    from clinic_photos.publication import visit_annotation_journal_codec as codec
    env, _, _, resolver, target, _ = read_setup
    root = os.path.realpath(tmp_path)
    site = os.path.join(root, "site.test")
    os.makedirs(os.path.join(site, "private"))
    env.frappe.get_site_path = lambda *parts: os.path.join(site, *parts)
    item = replace(intent(), binding=resolver.return_value)
    staged = staging.stage_visit_annotation(item)
    row = {**codec.submitted_values(item, staged), "name": "a" * 32}
    monkeypatch.setattr(frappe_context, "get_save_operation", lambda *a, **kw: dict(row))
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", lambda *a, **kw: dict(row))
    env.frappe.db.get_all = lambda *a, **kw: [{"name": row["name"]}]
    return read_setup, row, item


def test_pending_read_and_status_verify_real_submitted_codec(pending_row):
    (env, api, payload, _, _, _), row, item = pending_row
    request = {"appointment": payload["appointment"], "slot": payload["slot"]}
    assert api.read(request)["pending_request_key"] == item.request_key
    assert api.status({**request, "request_key": item.request_key}) == {"state": "pending"}
    row["payload_digest"] = "corrupt"
    assert api.status({**request, "request_key": item.request_key}) == {"state": "uncertain"}
    with pytest.raises(PermissionError):
        api.read(request)


def test_pending_read_and_status_use_nonlocking_snapshot_and_preview(pending_row, monkeypatch):
    from clinic_photos.access import frappe_context
    from clinic_photos.visit import annotation_authority
    (_, api, payload, resolver, _, _), row, item = pending_row
    calls = []
    def read_row(*args, **kwargs):
        calls.append(kwargs.get("for_update", False))
        return dict(row)
    preview = Mock(return_value=resolver.return_value)
    monkeypatch.setattr(annotation_authority, "preview_write_binding", preview, raising=False)
    monkeypatch.setattr(annotation_authority, "resolve_write_binding",
                        lambda *args, **kwargs: pytest.fail("locking write resolver used"))
    monkeypatch.setattr(frappe_context, "get_save_operation", read_row)
    monkeypatch.setattr(frappe_context, "get_save_operation_by_identity", read_row)
    request = {"appointment": payload["appointment"], "slot": payload["slot"]}
    assert api.read(request)["can_write"] is True
    assert api.status({**request, "request_key": item.request_key}) == {"state": "pending"}
    assert calls == [False, False]
    preview.assert_called_once_with(payload["appointment"], payload["encounter"], payload["slot"])


def test_ambiguous_pending_is_not_exposed(pending_row):
    (env, api, payload, _, _, _), row, _ = pending_row
    env.frappe.db.get_all = lambda *a, **kw: [{"name": row["name"]}, {"name": "b" * 32}]
    with pytest.raises(PermissionError):
        api.read({"appointment": payload["appointment"], "slot": payload["slot"]})


def test_status_committed_requires_exact_reader_request_and_operation(pending_row):
    from types import SimpleNamespace
    (_, api, payload, _, _, reader), row, item = pending_row
    row.update(state="PUBLISHED", published_version=1)
    reader.read_committed.return_value = SimpleNamespace(version_number=1, annotation={"shapes": []})
    request = {"appointment": payload["appointment"], "slot": payload["slot"], "request_key": item.request_key}
    assert api.status(request) == {"state": "committed", "request_key": item.request_key, "version": 1}
    reader.read_committed.assert_called_once_with(payload["appointment"], payload["slot"],
                                                 version=1, operation=row["name"], request_key=item.request_key)
    reader.read_committed.side_effect = PermissionError("bad evidence")
    assert api.status(request) == {"state": "uncertain"}


def test_prepared_requires_exact_reservation_evidence(pending_row):
    import hashlib
    import json
    (_, api, payload, _, _, _), row, item = pending_row
    material = json.dumps(
        ["clinic-visit-annotation-prepared-v1", item.binding.site, item.binding.actor,
         row["name"], item.target_identity, item.intent_digest, 1],
        ensure_ascii=True, separators=(",", ":"),
    ).encode()
    row.update(state="PREPARED", generation=1,
               reservation_key="/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json",
               expected_sha256=row["staged_sha256"], expected_size=row["staged_size"])
    request = {"appointment": payload["appointment"], "slot": payload["slot"], "request_key": item.request_key}
    assert api.status(request) == {"state": "pending"}
    row["reservation_key"] += "x"
    assert api.status(request) == {"state": "uncertain"}


def test_status_substituted_binding_never_reports_pending(pending_row):
    (_, api, payload, _, _, _), row, item = pending_row
    row["original"] = "other"
    assert api.status({"appointment": payload["appointment"], "slot": payload["slot"],
                       "request_key": item.request_key}) == {"state": "uncertain"}
