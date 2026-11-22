import types
from unittest.mock import Mock, patch

import pytest

from clinic_photos.visit import actor


def test_actor_read_has_same_role_policy_without_locks():
    frappe = types.SimpleNamespace(
        session=types.SimpleNamespace(user="doctor"), PermissionError=PermissionError,
        throw=lambda message, exc: (_ for _ in ()).throw(exc(message)),
        db=types.SimpleNamespace(get_value=Mock(return_value=1), sql=Mock(return_value=[("Physician",)])),
    )
    with patch.dict("sys.modules", {"frappe": frappe}):
        assert actor.require_visit_actor(doctor=True, for_update=False) == "doctor"
        assert not frappe.db.get_value.call_args.kwargs.get("for_update")
        assert "for update" not in frappe.db.sql.call_args.args[0].lower()
        frappe.db.sql.return_value = []
        with pytest.raises(PermissionError):
            actor.require_visit_actor(doctor=True, for_update=False)
        frappe.db.sql.return_value = [("Physician",)]
        frappe.db.get_value.return_value = 0
        with pytest.raises(PermissionError):
            actor.require_visit_actor(doctor=True, for_update=False)
        frappe.db.get_value.return_value = 1
        assert actor.require_visit_actor(doctor=True) == "doctor"
        assert frappe.db.get_value.call_args.kwargs["for_update"] is True
        assert "for update" in frappe.db.sql.call_args.args[0].lower()


def test_read_visit_checks_patient_identity_and_permissions_without_locks():
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac")
    patient = types.SimpleNamespace(name="P", creation="pc")
    visit = types.SimpleNamespace(appointment="A", patient="P", appointment_snapshot="A",
                                  patient_snapshot="P", appointment_creation="ac", patient_creation="pc")
    docs = {"Patient Appointment": appointment, "Patient": patient, "Clinic Visit Photo State": visit}
    frappe = types.SimpleNamespace(
        PermissionError=PermissionError, ValidationError=ValueError,
        throw=lambda message, exc: (_ for _ in ()).throw(exc(message)),
        get_doc=Mock(side_effect=lambda dt, name, **kw: docs[dt]),
        has_permission=Mock(return_value=True),
        db=types.SimpleNamespace(get_value=Mock(return_value="state")),
    )
    with patch.dict("sys.modules", {"frappe": frappe}):
        from clinic_photos.visit import state
    with patch.object(state, "frappe", frappe), patch.object(state, "current_identity") as locked_identity:
        assert state.read_visit("A") == (appointment, visit)
        locked_identity.assert_not_called()
        assert all(not call.kwargs.get("for_update") for call in frappe.get_doc.call_args_list)
        assert not frappe.db.get_value.call_args.kwargs.get("for_update")
        visit.patient_creation = "other"
        with pytest.raises(ValueError):
            state.read_visit("A")
        visit.patient_creation = "pc"
        frappe.has_permission.return_value = False
        with pytest.raises(PermissionError):
            state.read_visit("A")
