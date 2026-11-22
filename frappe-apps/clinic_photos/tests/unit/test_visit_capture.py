import importlib
import importlib.util
import sys
import types
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from clinic_photos.publication.contracts import VisitBinding


def test_private_capture_module_exists():
    assert importlib.util.find_spec("clinic_photos.visit.capture") is not None


@pytest.fixture
def visit():
    frappe = types.ModuleType("frappe")
    frappe.session = types.SimpleNamespace(user="doctor@example.test")
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.throw = lambda msg, exc=ValueError: (_ for _ in ()).throw(exc(msg))
    frappe.get_roles = Mock(return_value=["Physician"])
    frappe.db = types.SimpleNamespace(
        get_value=Mock(return_value=1),
        sql=Mock(side_effect=lambda *a, **kw: [(role,) for role in frappe.get_roles(frappe.session.user)]),
    )
    frappe.has_permission = Mock(return_value=True)
    encounter = types.SimpleNamespace(
        name="E", appointment="A", patient="P", practitioner="DR",
        docstatus=0, creation="ec",
    )
    appointment = types.SimpleNamespace(name="A", patient="P", practitioner="DR", creation="ac")
    state = types.SimpleNamespace(
        consent_granted=1, active_grant_audit="G1",
        appointment_creation="ac", patient_creation="pc",
    )
    audit = types.SimpleNamespace(name="G1", appointment="A", patient="P", action="Granted", attested=1)
    frappe.get_doc = Mock(side_effect=lambda dt, name, **kw: {
        "Patient Encounter": encounter, "Clinic Visit Photo Consent Audit": audit,
    }[dt])
    binding = VisitBinding(
        appointment="A", patient="P", slot="After", encounter="E",
        body_area="Arm", viewpoint="Front", appointment_creation="ac",
        patient_creation="pc", encounter_creation="ec", grant_audit="G1",
    )
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.capture", None)
        capture = importlib.import_module("clinic_photos.visit.capture")
        with patch.object(capture.state, "lock_visit", return_value=(appointment, state)), \
             patch.object(capture.state, "has_checkin_witness", return_value=True):
            yield types.SimpleNamespace(frappe=frappe, capture=capture, binding=binding,
                                        encounter=encounter, appointment=appointment,
                                        state=state, audit=audit)


def test_after_locks_encounter_before_visit_and_checks_witness(visit):
    order = []
    original = visit.frappe.get_doc.side_effect
    visit.frappe.get_doc.side_effect = lambda dt, name, **kw: (
        order.append((dt, kw.get("for_update"))) or original(dt, name, **kw)
    )
    visit.capture.state.lock_visit.side_effect = lambda name: (
        order.append(("visit", True)) or (visit.appointment, visit.state)
    )
    visit.capture.check_visit_authority(visit.binding)
    assert order[:2] == [("Patient Encounter", True), ("visit", True)]
    visit.capture.state.has_checkin_witness.assert_called_once_with("A", "P")


@pytest.mark.parametrize("field,value", [
    ("docstatus", 1), ("appointment", "OTHER"), ("patient", "OTHER"),
    ("practitioner", "OTHER"), ("creation", "different"),
])
def test_after_rejects_wrong_or_submitted_encounter(visit, field, value):
    setattr(visit.encounter, field, value)
    with pytest.raises((PermissionError, ValueError)):
        visit.capture.check_visit_authority(visit.binding)


def test_after_needs_doctor_and_witness_not_status(visit):
    visit.frappe.get_roles.return_value = ["Healthcare Administrator"]
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)
    visit.frappe.get_roles.return_value = ["Physician"]
    visit.capture.state.has_checkin_witness.return_value = False
    with pytest.raises((PermissionError, ValueError)):
        visit.capture.check_visit_authority(visit.binding)


def test_stale_grant_and_native_permission_denials_fail_closed(visit):
    visit.state.active_grant_audit = "G2"
    with pytest.raises((PermissionError, ValueError)):
        visit.capture.check_visit_authority(visit.binding)
    visit.state.active_grant_audit = "G1"
    visit.frappe.has_permission.side_effect = lambda doctype, ptype, **kw: doctype != "File"
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)


def test_only_coherent_revoked_or_replaced_grant_has_typed_rejection(visit):
    original = visit.frappe.get_doc.side_effect
    visit.frappe.get_doc.side_effect = lambda dt, name, **kw: (
        types.SimpleNamespace(name="G2", appointment="A", patient="P",
                              action="Granted", attested=1)
        if dt == "Clinic Visit Photo Consent Audit" and name == "G2"
        else original(dt, name, **kw)
    )
    for consent, grant in ((0, None), (1, "G2")):
        visit.state.consent_granted = consent
        visit.state.active_grant_audit = grant
        with pytest.raises(visit.capture.VisitEligibilityRejected):
            visit.capture.check_visit_authority(visit.binding)
    visit.state.consent_granted = 0
    visit.state.active_grant_audit = "unexpected"
    with pytest.raises(PermissionError) as error:
        visit.capture.check_visit_authority(visit.binding)
    assert not isinstance(error.value, visit.capture.VisitEligibilityRejected)
    visit.state.consent_granted = 1
    visit.state.active_grant_audit = "G2"
    visit.frappe.get_doc.side_effect = original
    with pytest.raises(PermissionError) as error:
        visit.capture.check_visit_authority(visit.binding)
    assert not isinstance(error.value, visit.capture.VisitEligibilityRejected)
    visit.state.consent_granted = 0
    visit.state.active_grant_audit = None
    visit.encounter.patient = "wrong"
    with pytest.raises(PermissionError) as error:
        visit.capture.check_visit_authority(visit.binding)
    assert not isinstance(error.value, visit.capture.VisitEligibilityRejected)


def test_regrant_does_not_revive_binding_to_old_grant(visit):
    visit.state.consent_granted = 0
    visit.state.active_grant_audit = None
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)
    visit.state.consent_granted = 1
    visit.state.active_grant_audit = "G2"
    visit.audit.name = "G2"
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)
    visit.capture.check_visit_authority(replace(visit.binding, grant_audit="G2"))


@pytest.mark.parametrize("doctype,ptype", [
    ("Patient Encounter", "read"), ("Patient Encounter", "write"),
    ("File", "create"),
])
def test_capture_refuses_missing_native_permission(visit, doctype, ptype):
    visit.frappe.has_permission.side_effect = lambda dt, pt, **kw: (dt, pt) != (doctype, ptype)
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)


def test_before_allows_reception_and_has_no_encounter(visit):
    visit.frappe.get_roles.return_value = ["Healthcare Administrator"]
    before = replace(visit.binding, slot="Before", encounter=None, encounter_creation=None)
    visit.capture.check_visit_authority(before)
    assert all(call.args[0] != "Patient Encounter" for call in visit.frappe.get_doc.call_args_list)


def test_capture_does_not_require_clinic_photo_create(visit):
    visit.frappe.has_permission.side_effect = lambda dt, pt, **kw: (dt, pt) != ("Clinic Photo", "create")
    visit.capture.check_visit_authority(visit.binding)
    assert all(call.args[:2] != ("Clinic Photo", "create")
               for call in visit.frappe.has_permission.call_args_list)


def test_disabled_user_is_denied_despite_cached_physician_role(visit):
    visit.frappe.db = types.SimpleNamespace(
        get_value=Mock(return_value=0), sql=Mock(return_value=[("Physician",)]),
    )
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)


def test_current_db_role_revocation_overrides_cached_roles(visit):
    visit.frappe.db.sql.side_effect = None
    visit.frappe.db.sql.return_value = []
    with pytest.raises(PermissionError):
        visit.capture.check_visit_authority(visit.binding)
    visit.frappe.db.get_value.assert_called_once_with(
        "User", "doctor@example.test", "enabled", for_update=True
    )
    sql_call = visit.frappe.db.sql.call_args
    assert "for update" in sql_call.args[0].lower()
    assert sql_call.args[1] == ("doctor@example.test",)
