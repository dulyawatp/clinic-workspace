"""Private-data-minimal visit reconciliation API tests; framework is faked."""
import importlib
import sys
import types
from unittest.mock import Mock, patch

import pytest

from clinic_photos.publication import contracts, journal


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.local = types.SimpleNamespace(site="site.test")
    frappe.conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    frappe.db = types.SimpleNamespace(transaction_writes=0, after_commit=types.SimpleNamespace(add=Mock()))
    frappe.whitelist = Mock(side_effect=lambda **kwargs: lambda fn: fn)
    appointment = types.SimpleNamespace(name="APT", patient="PAT", creation="ac")
    patient = types.SimpleNamespace(name="PAT", creation="pc")
    frappe.get_doc = Mock(side_effect=lambda doctype, name: {
        "Patient Appointment": appointment, "Patient": patient,
    }[doctype])
    frappe.has_permission = Mock(return_value=True)
    binding = contracts.VisitBinding(
        appointment="APT", patient="PAT", slot="Before", encounter=None,
        body_area="Arm", viewpoint="Front", appointment_creation="ac",
        patient_creation="pc", encounter_creation=None, grant_audit="GRANT",
    )
    operation = types.SimpleNamespace(
        id="a" * 32, site="site.test", actor="doctor",
        kind=contracts.VISIT_ORIGINAL_CREATE, request_key="visit-request-key-01",
        state="SUBMITTED", request=types.SimpleNamespace(visit_binding=binding), published=None,
    )
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.api.visit_upload", None)
        api = importlib.import_module("clinic_photos.api.visit_upload")
        with patch.object(api.photo_save, "require_post"), \
             patch.object(api.photo_save, "require_native_session"), \
             patch.object(api.frappe_context, "get_save_operation_by_identity",
                          return_value={"name": operation.id}) as lookup, \
             patch.object(journal, "load_verified_operation", return_value=operation):
            yield types.SimpleNamespace(frappe=frappe, api=api, operation=operation,
                                        binding=binding, lookup=lookup)
        sys.modules.pop("clinic_photos.api.visit_upload", None)


def test_status_response_is_bounded_and_bound_to_server_identity(env):
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    assert env.api.capture_status(payload) == {"state": "pending"}
    env.lookup.assert_called_once_with(journal.identity_digest(
        "site.test", "doctor", contracts.VISIT_ORIGINAL_CREATE, payload["request_key"]))
    env.operation.state = "PREPARED"
    assert env.api.capture_status(payload) == {"state": "unknown"}
    env.operation.actor = "other"
    assert env.api.capture_status(payload) == {"state": "not_found"}


@pytest.mark.parametrize("method,payload", [
    ("capture", {"appointment": "APT", "slot": "Before", "body_area": "Arm",
                 "viewpoint": "Front", "request_key": "visit-request-key-01", "image_base64": "encoded"}),
    ("capture_status", {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}),
])
def test_upload_v1_dispatch_cmd(env, method, payload):
    cmd = f"clinic_photos.api.visit_upload.{method}"
    env.frappe.local.form_dict = {"cmd": cmd}
    endpoint = getattr(env.api, method)
    with patch.object(env.api, "_stage_visit_upload"):
        assert endpoint(payload, cmd=cmd) == {"state": "pending"}
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd + "_wrong")
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd, other="x")
        env.frappe.local.form_dict = {"cmd": "other"}
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd)


def test_status_saved_requires_exact_verified_pair(env):
    pair = importlib.import_module("clinic_photos.visit.pair")
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    env.operation.state = "PUBLISHED"
    env.operation.published = types.SimpleNamespace(domain_doctype="Clinic Photo",
                                                    domain_name="PHOTO", file_name="FILE")
    with patch.object(pair, "_read_slot_structure", return_value=(
        types.SimpleNamespace(operation=env.operation.id),
        types.SimpleNamespace(name="PHOTO"), types.SimpleNamespace(name="FILE"),
    )):
        assert env.api.capture_status(payload) == {"state": "saved"}
        env.operation.published.file_name = "OTHER"
        assert env.api.capture_status(payload) == {"state": "unknown"}


def test_own_published_status_needs_no_photo_read(env):
    pair = importlib.import_module("clinic_photos.visit.pair")
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    env.operation.state = "PUBLISHED"
    env.operation.published = types.SimpleNamespace(domain_doctype="Clinic Photo",
                                                    domain_name="PHOTO", file_name="FILE")
    record = types.SimpleNamespace(operation=env.operation.id)
    photo = types.SimpleNamespace(name="PHOTO")
    file = types.SimpleNamespace(name="FILE")
    env.frappe.has_permission.side_effect = lambda dt, perm, **kw: dt not in ("Clinic Photo", "File")
    with patch.object(pair, "_read_slot_structure", return_value=(record, photo, file)), \
         patch.object(env.api, "_read_pair", side_effect=PermissionError):
        assert env.api.capture_status(payload) == {"state": "saved"}
        record.operation = "other"
        assert env.api.capture_status(payload) == {"state": "unknown"}


def test_status_denies_native_read_and_wrong_input(env):
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    env.frappe.has_permission.return_value = False
    with pytest.raises(PermissionError):
        env.api.capture_status(payload)
    env.lookup.assert_not_called()
    env.frappe.has_permission.return_value = True
    with pytest.raises(ValueError):
        env.api.capture_status({**payload, "file": "forged"})


@pytest.mark.parametrize("doctype", ["Patient Appointment", "Patient"])
def test_status_requires_each_native_read(env, doctype):
    env.frappe.has_permission.side_effect = lambda dt, permission, **kwargs: dt != doctype
    with pytest.raises(PermissionError):
        env.api.capture_status({"appointment": "APT", "slot": "Before",
                                "request_key": "visit-request-key-01"})
    env.lookup.assert_not_called()


@pytest.mark.parametrize("field,value", [
    ("id", "b" * 32), ("actor", "other"), ("site", "other.site"),
    ("kind", contracts.ANNOTATION_APPEND), ("request_key", "another-request-key"),
])
def test_status_never_discloses_foreign_or_wrong_identity(env, field, value):
    setattr(env.operation, field, value)
    assert env.api.capture_status({"appointment": "APT", "slot": "Before",
                                   "request_key": "visit-request-key-01"}) == {"state": "not_found"}


@pytest.mark.parametrize("field,value", [
    ("appointment", "OTHER"), ("patient", "OTHER"), ("slot", "After"),
    ("appointment_creation", "other"), ("patient_creation", "other"),
])
def test_status_refuses_wrong_visit_binding(env, field, value):
    from dataclasses import replace
    env.operation.request.visit_binding = replace(env.binding, **{
        **{field: value},
        **({"encounter": "ENC", "encounter_creation": "ec"} if field == "slot" else {}),
    })
    assert env.api.capture_status({"appointment": "APT", "slot": "Before",
                                   "request_key": "visit-request-key-01"}) == {"state": "not_found"}


def test_status_gate_native_transport_and_uncommitted_reads_fail_closed(env):
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    env.frappe.conf["clinic_visit_photos_enabled"] = False
    with pytest.raises(PermissionError):
        env.api.capture_status(payload)
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    with patch.object(env.api.photo_save, "require_post", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            env.api.capture_status(payload)
    with patch.object(env.api.photo_save, "require_native_session", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            env.api.capture_status(payload)
    env.frappe.db.transaction_writes = 1
    with pytest.raises(PermissionError):
        env.api.capture_status(payload)
    env.lookup.assert_not_called()


def test_status_unknown_on_corrupt_journal_and_no_row_is_not_found(env):
    payload = {"appointment": "APT", "slot": "Before", "request_key": "visit-request-key-01"}
    env.lookup.return_value = None
    assert env.api.capture_status(payload) == {"state": "not_found"}
    env.lookup.return_value = {"name": "private"}
    with patch.object(journal, "load_verified_operation", side_effect=RuntimeError("secret")):
        assert env.api.capture_status(payload) == {"state": "unknown"}


def test_capture_registers_created_once_after_commit_and_never_exposes_operation(env):
    operation = types.SimpleNamespace(id="a" * 32)
    payload = {"appointment": "APT", "slot": "Before", "body_area": "Arm",
               "viewpoint": "Front", "request_key": "visit-request-key-01", "image_base64": "encoded"}
    def stage(given, *, on_created):
        assert given is payload
        on_created(operation)
        return operation
    with patch.object(env.api, "_stage_visit_upload", side_effect=stage), \
         patch.object(env.api, "_enqueue_visit") as enqueue:
        assert env.api.capture(payload) == {"state": "pending"}
        enqueue.assert_not_called()
        callback = env.frappe.db.after_commit.add.call_args.args[0]
        callback()
        enqueue.assert_called_once_with(operation.id)


def test_capture_replay_does_not_register_and_callback_setup_failure_propagates(env):
    payload = {"appointment": "APT", "slot": "Before", "body_area": "Arm",
               "viewpoint": "Front", "request_key": "visit-request-key-01", "image_base64": "encoded"}
    operation = types.SimpleNamespace(id="a" * 32)
    with patch.object(env.api, "_stage_visit_upload", return_value=operation):
        assert env.api.capture(payload) == {"state": "pending"}
        env.frappe.db.after_commit.add.assert_not_called()
    def stage(given, *, on_created):
        on_created(operation)
        return operation
    env.frappe.db.after_commit.add.side_effect = RuntimeError("registration error")
    with patch.object(env.api, "_stage_visit_upload", side_effect=stage):
        with pytest.raises(Exception):
            env.api.capture(payload)


def test_capture_rollback_drops_notification_and_enqueue_failure_never_retries(env):
    payload = {"appointment": "APT", "slot": "Before", "body_area": "Arm",
               "viewpoint": "Front", "request_key": "visit-request-key-01", "image_base64": "encoded"}
    operation = types.SimpleNamespace(id="a" * 32)
    callbacks = []
    env.frappe.db.after_commit.add.side_effect = callbacks.append
    def stage(given, *, on_created):
        on_created(operation)
        return operation
    with patch.object(env.api, "_stage_visit_upload", side_effect=stage), \
         patch.object(env.api, "_enqueue_visit", side_effect=RuntimeError("queue offline")) as enqueue:
        assert env.api.capture(payload) == {"state": "pending"}
        callbacks.clear()  # fake full rollback discards native after-commit hooks
        enqueue.assert_not_called()
        assert env.api.capture(payload) == {"state": "pending"}
        callbacks[0]()
        callbacks[0]()
        enqueue.assert_called_once_with(operation.id)


def test_capture_rejects_extra_fields_and_auth_before_staging(env):
    payload = {"appointment": "APT", "slot": "Before", "body_area": "Arm",
               "viewpoint": "Front", "request_key": "visit-request-key-01", "image_base64": "encoded"}
    with patch.object(env.api, "_stage_visit_upload") as stage:
        for field in ("file_name", "patient", "storage_key", "grant_audit"):
            with pytest.raises(ValueError):
                env.api.capture({**payload, field: "forged"})
        with patch.object(env.api.photo_save, "require_native_session", side_effect=PermissionError):
            with pytest.raises(PermissionError):
                env.api.capture(payload)
        env.frappe.conf["clinic_visit_photos_enabled"] = False
        with pytest.raises(PermissionError):
            env.api.capture(payload)
        stage.assert_not_called()
