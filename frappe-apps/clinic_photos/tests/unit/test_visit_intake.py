"""Private visit intake contracts with narrow native-document fakes."""

import base64
import importlib
import sys
import types
from unittest.mock import Mock, patch

import pytest
from clinic_photos.publication.contracts import PublicationValidationError

PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.local = types.SimpleNamespace(site="site.test")
    frappe.conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac")
    visit = types.SimpleNamespace(appointment_creation="ac", patient_creation="pc",
                                  active_grant_audit="grant", consent_granted=1)
    encounter = types.SimpleNamespace(name="E", creation="ec")
    frappe.get_doc = Mock(return_value=encounter)
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.intake", None)
        intake = importlib.import_module("clinic_photos.visit.intake")
        with patch.object(intake.actor, "require_visit_actor", return_value="doctor") as actor, \
             patch.object(intake.state, "lock_visit", return_value=(appointment, visit)) as lock, \
             patch.object(intake.capture, "check_slot_availability") as check, \
             patch.object(intake.journal, "stage_submission", return_value="submitted") as stage:
            yield types.SimpleNamespace(intake=intake, frappe=frappe, appointment=appointment,
                visit=visit, encounter=encounter, actor=actor, lock=lock, check=check, stage=stage)
        sys.modules.pop("clinic_photos.visit.intake", None)


def payload(**changes):
    return {"appointment": "A", "slot": "Before", "body_area": "Arm",
            "viewpoint": "Front", "request_key": "request-key-0001",
            "image_base64": PNG, **changes}


def test_before_derives_authority_and_stages_typed_request(env):
    assert env.intake.stage_visit_upload(payload()) == "submitted"
    request = env.stage.call_args.args[0]
    binding = request.visit_binding
    assert (binding.patient, binding.grant_audit) == ("P", "grant")
    assert (binding.appointment_creation, binding.patient_creation) == ("ac", "pc")
    assert binding.encounter is None
    assert request.image_bytes == base64.b64decode(PNG)
    env.check.assert_called_once_with(binding)
    assert env.stage.call_args.kwargs == {"actor": "doctor", "site": "site.test"}


def test_private_intake_passes_optional_created_callback(env):
    callback = Mock()
    env.intake.stage_visit_upload(payload(), on_created=callback)
    assert env.stage.call_args.kwargs == {"actor": "doctor", "site": "site.test", "on_created": callback}


def test_after_locks_encounter_before_visit_and_uses_native_creation(env):
    order = []
    env.actor.side_effect = lambda **kw: order.append("actor") or "doctor"
    env.frappe.get_doc.side_effect = lambda *a, **kw: order.append("encounter") or env.encounter
    env.lock.side_effect = lambda *a: order.append("visit") or (env.appointment, env.visit)
    env.intake.stage_visit_upload(payload(slot="After", encounter="E"))
    assert order == ["actor", "encounter", "visit"]
    assert env.stage.call_args.args[0].visit_binding.encounter_creation == "ec"
    env.actor.assert_called_once_with(doctor=True)


@pytest.mark.parametrize("change", [
    {"patient": "attacker"}, {"grant_audit": "attacker"}, {"storage_key": "bad"},
    {"filename": "patient.jpg"}, {"slot": "Before", "encounter": "E"},
    {"slot": "After"}, {"appointment": " A"}, {"image_base64": "!!!"},
])
def test_rejects_untrusted_shape_without_staging(env, change):
    with pytest.raises(PublicationValidationError):
        env.intake.stage_visit_upload(payload(**change))
    env.stage.assert_not_called()


def test_occupied_slot_and_missing_grant_never_stage(env):
    env.check.side_effect = PermissionError("occupied")
    with pytest.raises(PermissionError):
        env.intake.stage_visit_upload(payload())
    env.check.assert_called_once()
    env.stage.assert_not_called()
    env.check.side_effect = None
    env.visit.active_grant_audit = None
    with pytest.raises(PermissionError):
        env.intake.stage_visit_upload(payload())
    env.stage.assert_not_called()


def test_retry_delegates_conflict_to_journal_only_while_slot_vacant(env):
    conflict = RuntimeError("journal conflict")
    env.stage.side_effect = ["same-id", "same-id", conflict]
    assert env.intake.stage_visit_upload(payload()) == "same-id"
    assert env.intake.stage_visit_upload(payload()) == "same-id"
    with pytest.raises(RuntimeError, match="journal conflict"):
        env.intake.stage_visit_upload(payload(body_area="Leg"))
    assert env.check.call_count == 3


def test_gate_and_actor_mismatch_deny_before_staging(env):
    env.frappe.conf["clinic_visit_photos_site"] = "another.test"
    with pytest.raises(PermissionError):
        env.intake.stage_visit_upload(payload())
    env.actor.assert_not_called()
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    env.actor.return_value = "another-user"
    with pytest.raises(PermissionError):
        env.intake.stage_visit_upload(payload())
    env.stage.assert_not_called()
