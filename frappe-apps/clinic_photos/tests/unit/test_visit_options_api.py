"""Read-only visit options, with local framework/transaction fakes."""
import importlib
import sys
import types
from unittest.mock import Mock, patch

import pytest

from clinic_photos.visit.actor import require_visit_actor


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.local = types.SimpleNamespace(site="site.test")
    frappe.conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    frappe.whitelist = Mock(side_effect=lambda **kwargs: lambda fn: fn)
    appointment = types.SimpleNamespace(name="APT", patient="PAT", creation="ac", practitioner="DR")
    patient = types.SimpleNamespace(name="PAT", creation="pc")
    visit = types.SimpleNamespace(appointment="APT", patient="PAT", appointment_snapshot="APT",
        patient_snapshot="PAT", appointment_creation="ac", patient_creation="pc",
        consent_granted=1, active_grant_audit="GRANT")
    audit = types.SimpleNamespace(name="GRANT", appointment="APT", patient="PAT",
                                  action="Granted", attested=1)
    encounter = types.SimpleNamespace(name="ENC", appointment="APT", patient="PAT",
                                      creation="ec", practitioner="DR", docstatus=0)
    docs = {"Patient Appointment": appointment, "Patient": patient,
            "Clinic Visit Photo State": visit, "Clinic Visit Photo Consent Audit": audit,
            "Patient Encounter": encounter}
    frappe.get_doc = Mock(side_effect=lambda doctype, name, **kwargs: docs[doctype])
    frappe.has_permission = Mock(return_value=True)
    frappe.db = types.SimpleNamespace(transaction_writes=0, get_value=Mock(return_value="STATE"))
    projection = {"appointment": "APT", "patient": "PAT", "before": None, "after": None}
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.api.visit_options", None)
        api = importlib.import_module("clinic_photos.api.visit_options")
        pair = importlib.import_module("clinic_photos.visit.pair")
        def structure(appointment, patient, slot):
            item = projection[slot.lower()]
            return (types.SimpleNamespace(**item), types.SimpleNamespace(name=item["photo"]),
                    types.SimpleNamespace(name=item["file"])) if item else None
        with patch.object(api.photo_save, "require_post"), \
             patch.object(api.photo_save, "require_native_session"), \
             patch.object(api.photo_save, "_frappe", return_value=frappe), \
             patch.object(api.capture, "frappe", frappe), \
             patch.object(pair, "_read_slot_structure", side_effect=structure), \
             patch.object(api, "_read_pair", return_value=projection), \
             patch.object(api.actor, "require_visit_actor", return_value="doctor"), \
             patch.object(api.state, "lock_visit", return_value=(appointment, visit)), \
             patch.object(api.state, "has_checkin_witness", return_value=True):
            yield types.SimpleNamespace(api=api, frappe=frappe, projection=projection,
                                        visit=visit, encounter=encounter)
        sys.modules.pop("clinic_photos.api.visit_options", None)


def test_options_before_with_active_grant(env):
    assert env.api.capture_options({"appointment": "APT"}) == {
        "has_active_consent": True, "can_grant": False, "can_revoke": True,
        "can_capture_before": True, "can_capture_after": False,
    }


def test_receptionist_options_respect_file_create_and_doctor_boundary(env):
    env.frappe.session.user = "reception@example.test"
    env.frappe.throw = lambda message, exc: (_ for _ in ()).throw(exc(message))
    env.frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: (
        1 if doctype == "User" else "STATE"
    )
    env.frappe.db.sql = Mock(return_value=[("Receptionist",)])
    env.api.actor.require_visit_actor.side_effect = require_visit_actor
    with patch.dict(sys.modules, {"frappe": env.frappe}):
        result = env.api.capture_options({"appointment": "APT"})
        assert result["can_capture_before"] and result["can_revoke"]
        env.frappe.has_permission.side_effect = lambda dt, perm, **kw: not (dt == "File" and perm == "create")
        assert not env.api.capture_options({"appointment": "APT"})["can_capture_before"]
        env.frappe.has_permission.side_effect = None
        env.projection["before"] = {"photo": "PHOTO", "file": "FILE",
                                    "body_area": "Arm", "viewpoint": "Front"}
        assert not env.api.capture_options({"appointment": "APT", "encounter": "ENC"})["can_capture_after"]
        env.visit.consent_granted = 0
        env.visit.active_grant_audit = None
        assert env.api.capture_options({"appointment": "APT"})["can_grant"]


def test_options_v1_dispatch_cmd(env):
    cmd = "clinic_photos.api.visit_options.capture_options"
    env.frappe.local.form_dict = {"cmd": cmd}
    payload = {"appointment": "APT"}
    assert env.api.capture_options(payload, cmd=cmd)["can_capture_before"]
    with pytest.raises(ValueError):
        env.api.capture_options(payload, cmd=cmd + "_wrong")
    with pytest.raises(ValueError):
        env.api.capture_options(payload, cmd=cmd, other="x")
    env.frappe.local.form_dict = {"cmd": "other"}
    with pytest.raises(ValueError):
        env.api.capture_options(payload, cmd=cmd)


def test_options_after_requires_visible_before_and_exact_draft_encounter(env):
    env.projection["before"] = {"photo": "PHOTO", "file": "FILE",
                                "body_area": "Arm", "viewpoint": "Front"}
    assert env.api.capture_options({"appointment": "APT", "encounter": "ENC"})["can_capture_after"]
    env.encounter.docstatus = 1
    assert not env.api.capture_options({"appointment": "APT", "encounter": "ENC"})["can_capture_after"]


def test_options_no_grant_and_missing_read_fail_closed(env):
    env.visit.consent_granted = 0
    env.visit.active_grant_audit = None
    result = env.api.capture_options({"appointment": "APT"})
    assert not result["has_active_consent"] and result["can_grant"]
    assert not result["can_capture_before"] and not result["can_capture_after"]
    env.frappe.has_permission.side_effect = lambda dt, permission, **kw: dt != "Patient"
    with pytest.raises(PermissionError):
        env.api.capture_options({"appointment": "APT"})


@pytest.mark.parametrize("blocked", ["witness", "physician", "file_create", "encounter_write"])
def test_after_capability_tracks_authoritative_guards(env, blocked):
    env.projection["before"] = {"photo": "PHOTO", "file": "FILE",
                                "body_area": "Arm", "viewpoint": "Front"}
    if blocked == "witness":
        env.api.state.has_checkin_witness.return_value = False
    elif blocked == "physician":
        env.api.actor.require_visit_actor.side_effect = lambda **kw: (
            (_ for _ in ()).throw(PermissionError()) if kw.get("doctor") else "doctor"
        )
    elif blocked == "file_create":
        env.frappe.has_permission.side_effect = lambda dt, perm, **kw: not (dt == "File" and perm == "create")
    else:
        env.frappe.has_permission.side_effect = lambda dt, perm, **kw: not (dt == "Patient Encounter" and perm == "write")
    result = env.api.capture_options({"appointment": "APT", "encounter": "ENC"})
    assert result["can_capture_after"] is False
    assert set(result) == {"has_active_consent", "can_grant", "can_revoke",
                           "can_capture_before", "can_capture_after"}


def test_options_refuses_invisible_pair_or_encounter(env):
    pair = importlib.import_module("clinic_photos.visit.pair")
    with patch.object(pair, "_read_slot_structure", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            env.api.capture_options({"appointment": "APT"})
    env.frappe.has_permission.side_effect = lambda dt, perm, **kw: dt != "Patient Encounter"
    with pytest.raises(PermissionError):
        env.api.capture_options({"appointment": "APT", "encounter": "ENC"})


def test_options_disabled_invalid_and_uncommitted_refuse(env):
    env.frappe.conf["clinic_visit_photos_enabled"] = False
    with pytest.raises(PermissionError):
        env.api.capture_options({"appointment": "APT"})
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    with pytest.raises(ValueError):
        env.api.capture_options({"appointment": "APT", "patient": "PAT"})
    env.frappe.db.transaction_writes = 1
    with pytest.raises(PermissionError):
        env.api.capture_options({"appointment": "APT"})


def test_options_requires_post_session_and_appointment_write(env):
    with patch.object(env.api.photo_save, "require_post", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            env.api.capture_options({"appointment": "APT"})
    with patch.object(env.api.photo_save, "require_native_session", side_effect=PermissionError):
        with pytest.raises(PermissionError):
            env.api.capture_options({"appointment": "APT"})
    env.api.state.lock_visit.side_effect = PermissionError
    result = env.api.capture_options({"appointment": "APT"})
    assert not result["can_capture_before"]
    assert not result["can_grant"] and not result["can_revoke"]


def test_options_after_occupied_or_before_missing_never_capture(env):
    assert not env.api.capture_options({"appointment": "APT", "encounter": "ENC"})["can_capture_after"]
    env.projection["before"] = {"photo": "PHOTO", "file": "FILE",
                                "body_area": "Arm", "viewpoint": "Front"}
    env.projection["after"] = {"photo": "OTHER", "file": "OTHER",
                               "body_area": "Arm", "viewpoint": "Front"}
    result = env.api.capture_options({"appointment": "APT", "encounter": "ENC"})
    assert not result["can_capture_before"] and not result["can_capture_after"]


def test_reception_options_with_occupied_slot_and_no_photo_read(env):
    from clinic_photos.visit import pair
    occupied = (object(), object(), object())
    env.frappe.has_permission.side_effect = lambda dt, perm, **kw: dt not in ("Clinic Photo", "File") or perm != "read"
    with patch.object(pair, "_read_slot_structure", side_effect=lambda a, p, slot: occupied if slot == "Before" else None), \
         patch.object(env.api, "_read_pair", side_effect=PermissionError):
        assert env.api.capture_options({"appointment": "APT", "encounter": "ENC"}) == {
            "has_active_consent": True, "can_grant": False, "can_revoke": True,
            "can_capture_before": False, "can_capture_after": False,
        }
