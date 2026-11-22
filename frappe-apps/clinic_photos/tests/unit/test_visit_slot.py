import importlib
import json
import sys
import types
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, call as unittest_call, patch

import pytest

from clinic_photos.publication.contracts import VisitBinding


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.local = types.SimpleNamespace(site="site")
    frappe.PermissionError = PermissionError
    frappe.throw = lambda msg, exc=ValueError: (_ for _ in ()).throw(exc(msg))
    frappe.has_permission = Mock(return_value=True)
    frappe.db = types.SimpleNamespace(get_value=Mock(return_value=None))
    binding = VisitBinding(
        appointment="A", patient="P", slot="Before", encounter=None,
        body_area="Arm", viewpoint="Front", appointment_creation="ac",
        patient_creation="pc", encounter_creation=None, grant_audit="G1",
    )
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.capture", None)
        capture = importlib.import_module("clinic_photos.visit.capture")
        pair = importlib.import_module("clinic_photos.visit.pair")
        with patch.object(pair, "frappe", frappe), patch.object(capture, "check_visit_authority"):
            yield types.SimpleNamespace(frappe=frappe, capture=capture, binding=binding)


def test_slot_schema_has_unique_key_and_photo():
    path = (Path(__file__).resolve().parents[2] / "clinic_photos/clinic_photos/doctype/"
            "clinic_visit_photo_slot/clinic_visit_photo_slot.json")
    assert path.exists()
    schema = json.loads(path.read_text())
    fields = {field["fieldname"]: field for field in schema["fields"]}
    assert fields["slot_key"]["unique"] == 1
    assert fields["photo"]["unique"] == 1
    assert schema["permissions"] == []


def test_before_can_be_first_but_duplicate_fails(env):
    env.capture.check_slot_availability(env.binding)
    env.frappe.db.get_value.return_value = {"name": "existing"}
    with pytest.raises(PermissionError):
        env.capture.check_slot_availability(env.binding)


def test_after_requires_coherent_matching_before(env):
    after = replace(env.binding, slot="After", encounter="E", encounter_creation="ec")
    with pytest.raises(PermissionError):
        env.capture.check_slot_availability(after)


def install_before(env):
    from clinic_photos.publication import journal

    b = env.binding
    key = env.capture.slot_key(b.appointment, b.slot)
    record = types.SimpleNamespace(
        name=key, slot_key=key, appointment="A", patient="P", slot="Before",
        appointment_creation="ac", patient_creation="pc", body_area="Arm",
        viewpoint="Front", operation="op", photo="photo",
    )
    photo = types.SimpleNamespace(
        name="photo", patient_reference="P", original_file="file",
        storage_key="/private/files/p.png", original_sha256="sha",
    )
    file = types.SimpleNamespace(
        name="file", is_private=1, file_url=photo.storage_key,
        attached_to_doctype="Clinic Photo", attached_to_name="photo",
    )
    outcome = types.SimpleNamespace(
        domain_doctype="Clinic Photo", domain_name="photo", file_name="file",
        reservation=types.SimpleNamespace(key=photo.storage_key, expected_sha256="sha"),
    )
    operation = types.SimpleNamespace(
        state="PUBLISHED", kind="visit_original_create", site="site", actor="doctor",
        request=types.SimpleNamespace(visit_binding=b), published=outcome,
    )
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        dict(vars(file)) if dt == "File" and filters == file.name else
        key if dt == "Clinic Visit Photo Slot" and filters["slot_key"] == key else None
    )
    env.frappe.get_doc = Mock(side_effect=lambda dt, name, **kw: {
        "Clinic Visit Photo Slot": record, "Clinic Photo": photo, "File": file,
    }[dt])
    return record, photo, file, operation, patch.object(
        journal, "load_verified_operation", return_value=operation
    )


def test_after_matching_before_and_mismatches(env):
    record, photo, file, operation, op_patch = install_before(env)
    after = replace(env.binding, slot="After", encounter="E", encounter_creation="ec")
    with op_patch:
        env.capture.check_slot_availability(after)
        for obj, field, value in (
            (record, "body_area", "Leg"), (record, "viewpoint", "Side"),
            (record, "patient", "OTHER"), (photo, "patient_reference", "OTHER"),
            (file, "attached_to_name", "OTHER"), (operation, "state", "SUBMITTED"),
        ):
            old = getattr(obj, field)
            setattr(obj, field, value)
            with pytest.raises(PermissionError):
                env.capture.check_slot_availability(after)
            setattr(obj, field, old)


def test_after_counterpart_uses_actual_feature_hooks_without_bypassing_native(env):
    from clinic_photos.access import frappe_context, permissions
    from clinic_photos.visit import pair

    record, photo, file, operation, op_patch = install_before(env)
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    photo.storage_key = file.file_url = operation.published.reservation.key = key
    after = replace(env.binding, slot="After", encounter="E", encounter_creation="ec")
    protected = types.SimpleNamespace(storage_key=key, registered=True, inconsistent=False, references=())
    actor = types.SimpleNamespace(user="doctor", enabled=True, clinician=True)
    denied = set()

    def native(doctype, ptype, doc):
        if doctype == "Clinic Photo":
            return doctype not in denied and permissions.photo_has_permission(doc, user="doctor", ptype=ptype)
        if doctype == "File":
            return doctype not in denied and permissions.file_has_permission(doc, user="doctor", ptype=ptype)
        return doctype not in denied

    env.frappe.has_permission.side_effect = native
    with op_patch, patch.object(pair, "frappe", env.frappe), \
         patch.object(frappe_context, "classify_file", return_value=protected), \
         patch.object(frappe_context, "current_actor", return_value=actor), \
         patch.object(frappe_context, "current_publication", return_value=None):
        env.capture.check_slot_availability(after)
        assert not permissions.photo_has_permission(photo, user="doctor", ptype="read")
        assert not permissions.file_has_permission(file, user="doctor", ptype="read")
        assert any(call.args == ("Clinic Photo", "photo") and call.kwargs == {"for_update": True}
                   for call in env.frappe.get_doc.call_args_list)
        assert any(call.args == ("File", "file") and call.kwargs == {"for_update": True}
                   for call in env.frappe.get_doc.call_args_list)
        for doctype in ("Clinic Photo", "File"):
            denied.add(doctype)
            with pytest.raises(PermissionError):
                env.capture.check_slot_availability(after)
            denied.clear()
        file.attached_to_name = "wrong"
        with pytest.raises(PermissionError):
            env.capture.check_slot_availability(after)
        file.attached_to_name = "photo"
        operation.state = "PREPARED"
        with pytest.raises(PermissionError):
            env.capture.check_slot_availability(after)


def test_after_rejects_inconsistent_operation_binding(env):
    record, photo, file, operation, op_patch = install_before(env)
    after = replace(env.binding, slot="After", encounter="E", encounter_creation="ec")
    operation.request.visit_binding = replace(env.binding, body_area="Leg")
    with op_patch, pytest.raises(PermissionError):
        env.capture.check_slot_availability(after)


def test_obsolete_published_slot_insertion_is_not_available(env):
    assert not hasattr(env.capture, "insert_slot_link")


def test_slot_key_separates_appointments_and_slots(env):
    keys = {env.capture.slot_key(appointment, slot)
            for appointment in ("A", "B") for slot in ("Before", "After")}
    assert len(keys) == 4


def test_prepared_slot_insert_uses_exact_consumed_evidence(env):
    from clinic_photos.publication import writer
    prepared = types.SimpleNamespace(id="op", visit_binding=env.binding)
    bound = object()
    linkage = types.SimpleNamespace(doctype="Clinic Photo", name="photo")
    created = types.SimpleNamespace(
        doctype="Clinic Visit Photo Slot", name="slot", flags=types.SimpleNamespace(),
        insert=Mock(),
    )
    env.frappe.get_doc = Mock(return_value=created)
    with patch.object(env.capture, "check_slot_availability") as available, \
         patch.object(writer, "require_visit_consumed_object", return_value=(bound, linkage)) as verify:
        assert env.capture.insert_prepared_slot_link(prepared, bound) == "slot"
    verify.assert_called_once_with(prepared, bound)
    available.assert_called_once_with(env.binding)
    values = env.frappe.get_doc.call_args.args[0]
    assert values["operation"] == "op" and values["photo"] == "photo"


def test_vacancy_uses_indexed_key_locking_reads(env):
    env.capture.check_slot_availability(env.binding)
    assert env.frappe.db.get_value.call_args_list == [
        unittest_call(env.capture.SLOT, {"slot_key": env.capture.slot_key("A", slot)},
                      "name", for_update=True)
        for slot in ("Before", "After")
    ]
