import importlib
import sys
import types
from unittest.mock import Mock, patch

import pytest


def test_resolver_requires_annotation_gate():
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(site="site", conf={})
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.PermissionError = PermissionError
    frappe.throw = lambda message, exc: (_ for _ in ()).throw(exc(message))
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.annotation_authority", None)
        module = importlib.import_module("clinic_photos.visit.annotation_authority")
        with pytest.raises(PermissionError):
            module.resolve_write_binding("A", "E", "Before")


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(site="site", conf={
        "clinic_visit_photos_site": "site", "clinic_visit_photos_enabled": True,
        "clinic_visit_photo_annotations_enabled": True,
    })
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.PermissionError = PermissionError
    frappe.throw = lambda message, exc: (_ for _ in ()).throw(exc(message))
    encounter = types.SimpleNamespace(name="E", creation="ec", docstatus=0,
                                      appointment="A", patient="P", practitioner="PR")
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac", practitioner="PR")
    patient = types.SimpleNamespace(name="P", creation="pc")
    visit = types.SimpleNamespace(consent_granted=1, active_grant_audit="grant",
                                  patient_creation="pc")
    audit = types.SimpleNamespace(name="grant", appointment="A", patient="P",
                                  action="Granted", attested=1)
    record = types.SimpleNamespace(name="slot", operation="op")
    photo = types.SimpleNamespace(name="photo")
    file = types.SimpleNamespace(name="file")
    docs = {"Patient Encounter": encounter, "Patient": patient,
            "Clinic Visit Photo Consent Audit": audit}
    frappe.get_doc = Mock(side_effect=lambda dt, name, **kw: docs[dt])
    frappe.get_all = Mock(return_value=[{"name": "E"}])
    frappe.db = types.SimpleNamespace(sql=Mock(return_value=[("E",)]))
    frappe.has_permission = Mock(return_value=True)
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.annotation_authority", None)
        module = importlib.import_module("clinic_photos.visit.annotation_authority")
        with patch.object(module.actor, "require_visit_actor", return_value="doctor") as actor, \
             patch.object(module.state, "lock_visit", return_value=(appointment, visit)), \
             patch.object(module.pair, "_read_slot_structure", return_value=(record, photo, file)) as pair, \
             patch.object(module.pair, "_require_native_read_for_verified_slot") as read:
            yield types.SimpleNamespace(module=module, frappe=frappe, encounter=encounter,
                appointment=appointment, patient=patient, visit=visit, audit=audit,
                record=record, photo=photo, file=file, actor=actor, pair=pair, read=read)


def test_resolves_and_revalidates_all_binding_fields(env):
    resolve = env.module.resolve_write_binding
    binding = resolve("A", "E", "Before")
    assert (binding.actor, binding.encounter, binding.grant_audit, binding.original_photo,
            binding.original_file, binding.original_operation) == ("doctor", "E", "grant", "photo", "file", "op")
    assert resolve("A", "E", "Before", expected=binding) == binding
    env.photo.name = "different"
    with pytest.raises(PermissionError):
        resolve("A", "E", "Before", expected=binding)
    env.actor.assert_called_with(doctor=True)
    assert env.pair.call_args.kwargs == {"for_update": True}
    env.read.assert_called()
    assert all(c.kwargs.get("for_update") for c in env.frappe.get_doc.call_args_list)


@pytest.mark.parametrize("change", [
    lambda e: setattr(e.encounter, "docstatus", 1),
    lambda e: setattr(e.encounter, "appointment", "other"),
    lambda e: setattr(e.encounter, "patient", "other"),
    lambda e: setattr(e.visit, "consent_granted", 0),
    lambda e: setattr(e.audit, "attested", 0),
    lambda e: setattr(e.patient, "creation", "changed"),
    lambda e: setattr(e.frappe.db.sql, "return_value", [("E",), ("E2",)]),
    lambda e: setattr(e.pair, "return_value", None),
    lambda e: setattr(e.frappe.has_permission, "return_value", False),
])
def test_rejects_changed_authority_and_identity(env, change):
    change(env)
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before")


@pytest.mark.parametrize("site,visit,write", [
    ("other", True, True), ("site", False, True), ("site", True, False),
    ("site", True, 1),
])
def test_requires_exact_site_and_both_explicit_flags(env, site, visit, write):
    env.frappe.local.site = site
    env.frappe.local.conf["clinic_visit_photos_enabled"] = visit
    env.frappe.local.conf["clinic_visit_photo_annotations_enabled"] = write
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before")
    env.actor.assert_not_called()


def test_role_revocation_and_native_file_create_denial(env):
    binding = env.module.resolve_write_binding("A", "E", "Before")
    env.actor.side_effect = PermissionError("role revoked")
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before", expected=binding)
    env.actor.side_effect = None
    env.frappe.has_permission.side_effect = lambda dt, action, **kw: not (dt == "File" and action == "create")
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before")


def test_lock_order_and_no_mutation(env):
    calls = []
    env.actor.side_effect = lambda **kw: calls.append("actor") or "doctor"
    env.frappe.db.sql.side_effect = lambda query, values: calls.append("encounter set") or [("E",)]
    env.module.state.lock_visit.side_effect = lambda name: calls.append("visit") or (env.appointment, env.visit)
    docs = {"Patient Encounter": env.encounter, "Patient": env.patient,
            "Clinic Visit Photo Consent Audit": env.audit}
    env.frappe.get_doc.side_effect = lambda dt, name, **kw: calls.append(dt) or docs[dt]
    env.pair.side_effect = lambda *a, **kw: calls.append("slot") or (env.record, env.photo, env.file)
    env.module.resolve_write_binding("A", "E", "Before")
    assert calls == ["actor", "encounter set", "Patient Encounter", "visit",
                     "Clinic Visit Photo Consent Audit", "Patient", "slot"]
    assert all(call.kwargs.get("for_update") is True for call in env.frappe.get_doc.call_args_list)
    sql, values = env.frappe.db.sql.call_args.args
    normalized = " ".join(sql.lower().split())
    assert "where appointment = %s" in normalized
    assert "docstatus != 2" in normalized
    assert "order by name" in normalized
    assert "limit 2 for update" in normalized
    assert values == ("A",)
    env.frappe.get_all.assert_not_called()


@pytest.mark.parametrize("appointment_practitioner,encounter_practitioner", [
    (None, "PR"), ("PR", None), ("", "PR"), ("PR", ""), ("PR", "OTHER"),
])
def test_rejects_missing_or_mismatched_record_practitioners(env, appointment_practitioner, encounter_practitioner):
    env.appointment.practitioner = appointment_practitioner
    env.encounter.practitioner = encounter_practitioner
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before")


@pytest.mark.parametrize("rows", [[], [("other",)], [("E",), ("other",)]])
def test_rejects_ambiguous_current_encounter_set_before_document_lock(env, rows):
    env.frappe.db.sql.return_value = rows
    with pytest.raises(PermissionError):
        env.module.resolve_write_binding("A", "E", "Before")
    env.frappe.get_doc.assert_not_called()
    env.module.state.lock_visit.assert_not_called()


@pytest.mark.parametrize("appointment,encounter,slot", [
    ("", "E", "Before"), (" A", "E", "Before"), ("A", "E ", "Before"),
    ("A", "E", "before"),
])
def test_rejects_invalid_inputs(env, appointment, encounter, slot):
    with pytest.raises(Exception):
        env.module.resolve_write_binding(appointment, encounter, slot)
    env.actor.assert_not_called()


def test_preview_matches_writer_without_locks_and_enforces_revocation(env):
    binding = env.module.resolve_write_binding("A", "E", "Before")
    env.frappe.get_doc.reset_mock()
    env.frappe.db.sql.reset_mock()
    env.actor.reset_mock()
    with patch.object(env.module.state, "read_visit", return_value=(env.appointment, env.visit), create=True):
        assert env.module.preview_write_binding("A", "E", "Before", expected=binding) == binding
        env.module.state.lock_visit.assert_called_once()
        env.actor.assert_called_once_with(doctor=True, for_update=False)
        assert "for update" not in env.frappe.db.sql.call_args.args[0].lower()
        assert all(not c.kwargs.get("for_update") for c in env.frappe.get_doc.call_args_list)
        assert env.pair.call_args.kwargs == {"for_update": False}
        env.visit.consent_granted = 0
        with pytest.raises(PermissionError):
            env.module.preview_write_binding("A", "E", "Before")
        with pytest.raises(PermissionError):
            env.module.resolve_write_binding("A", "E", "Before")


@pytest.mark.parametrize("change", [
    lambda e: setattr(e.encounter, "docstatus", 1),
    lambda e: setattr(e.encounter, "patient", "wrong"),
    lambda e: setattr(e.audit, "attested", 0),
    lambda e: setattr(e.patient, "creation", "changed"),
    lambda e: setattr(e.frappe.db.sql, "return_value", [("E",), ("other",)]),
    lambda e: setattr(e.frappe.has_permission, "return_value", False),
    lambda e: setattr(e.pair, "return_value", None),
])
def test_preview_rejects_same_authority_changes(env, change):
    with patch.object(env.module.state, "read_visit", return_value=(env.appointment, env.visit)):
        change(env)
        with pytest.raises(PermissionError):
            env.module.preview_write_binding("A", "E", "Before")
