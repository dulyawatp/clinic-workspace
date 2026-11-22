"""Receptionist original-read proof, using isolated Frappe fixture adapters."""
import importlib
import hashlib
import sys
import types
from unittest.mock import Mock

import pytest

from clinic_photos.publication.contracts import VisitBinding, VISIT_ORIGINAL_CREATE
from test_async_publication_staging import PNG_BYTES


@pytest.fixture
def env(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = PermissionError
    frappe.session = types.SimpleNamespace(user="reception")
    frappe.local = types.SimpleNamespace(site="site")
    frappe.conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site"}
    frappe.db = types.SimpleNamespace(transaction_writes=0)
    frappe.throw = lambda msg, exc=ValueError: (_ for _ in ()).throw(exc(msg))
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    from clinic_photos.visit import capture
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    binding = VisitBinding(appointment="A", patient="P", slot="Before", encounter=None,
                           body_area="Arm", viewpoint="Front", appointment_creation="ac",
                           patient_creation="pc", encounter_creation=None, grant_audit="G")
    slot_name = capture.slot_key("A", "Before")
    slot = types.SimpleNamespace(name=slot_name, slot_key=slot_name, appointment="A",
        patient="P", slot="Before", appointment_creation="ac", patient_creation="pc",
        body_area="Arm", viewpoint="Front", operation="OP", photo="PHOTO",
        photo_creation="2026-11-22 12:00:00.123456", file_name="FILE",
        file_creation="2026-11-22 12:00:01.123456")
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac")
    patient = types.SimpleNamespace(name="P", creation="pc")
    photo = types.SimpleNamespace(name="PHOTO", patient_reference="P", original_file="FILE",
        storage_key=key, original_sha256=hashlib.sha256(PNG_BYTES).hexdigest(), creation=slot.photo_creation)
    file = types.SimpleNamespace(name="FILE", file_url=key, is_private=1,
        attached_to_doctype="Clinic Photo", attached_to_name="PHOTO", attached_to_field=None,
        creation=slot.file_creation, file_size=len(PNG_BYTES), file_name=key.rsplit("/", 1)[-1], owner="reception")
    reservation = types.SimpleNamespace(key=key, expected_sha256=photo.original_sha256, expected_size=len(PNG_BYTES),
                                         visit_binding=binding, actor="reception", site="site")
    published = types.SimpleNamespace(domain_doctype="Clinic Photo", domain_name="PHOTO",
        file_name="FILE", creation_identity="1:2", reservation=reservation)
    operation = types.SimpleNamespace(id="OP", actor="reception", site="site",
        kind=VISIT_ORIGINAL_CREATE, state="PUBLISHED",
        request=types.SimpleNamespace(visit_binding=binding), published=published)
    docs = {("Patient Appointment", "A"): appointment, ("Patient", "P"): patient,
            (capture.SLOT, slot_name): slot, ("Clinic Photo", "PHOTO"): photo, ("File", "FILE"): file}
    frappe.get_doc = Mock(side_effect=lambda dt, name: docs[(dt, name)])
    frappe.get_list = Mock(side_effect=lambda dt, **kw: [{"name": kw["filters"]["name"]}])
    frappe.has_permission = Mock(return_value=True)
    user = {"name": "reception", "enabled": 1, "user_type": "System User"}
    row = {"name": "OP", "actor": "reception", "site": "site",
           "kind": VISIT_ORIGINAL_CREATE, "state": "PUBLISHED"}
    frappe.db.get_value = Mock(side_effect=lambda dt, name, fields, **kw:
        user if dt == "User" else row if dt == "Clinic Photo Save Operation" else None)
    frappe.db.sql = Mock(return_value=[("Receptionist",)])
    module = importlib.import_module("clinic_photos.visit.reception_read")
    verify = Mock(return_value=operation)
    monkeypatch.setattr(module.journal, "load_verified_operation", verify)
    annotation_read = importlib.import_module("clinic_photos.visit.annotation_read")
    read = Mock(return_value=PNG_BYTES)
    monkeypatch.setattr(annotation_read, "read_verified_original_bytes", read)
    monkeypatch.setattr(module, "frappe", frappe)
    return types.SimpleNamespace(module=module, frappe=frappe, slot=slot, photo=photo,
        file=file, row=row, user=user, operation=operation, verify=verify, read=read)


def test_own_before_resolves_exact_proof_and_rechecks(env):
    assert env.module.read_own_before("A") == (PNG_BYTES, "image/png")
    assert env.verify.call_count == 2
    env.read.assert_called_once()


@pytest.mark.parametrize("failure", ["other", "role", "disabled", "query", "doc",
                                     "old", "photo_generation", "file_generation",
                                     "pending", "tamper"])
def test_denies_ineligible_or_tampered_read_before_bytes(env, failure):
    if failure == "other": env.row["actor"] = "other"
    if failure == "role": env.frappe.db.sql.return_value = [("Physician",)]
    if failure == "disabled": env.user["enabled"] = 0
    if failure == "query": env.frappe.get_list.side_effect = lambda *args, **kwargs: []
    if failure == "doc": env.frappe.has_permission.return_value = False
    if failure == "old": env.slot.photo_creation = ""
    if failure == "photo_generation": env.photo.creation = "2026-11-22 12:00:00"
    if failure == "file_generation": env.file.creation = "2026-11-22 12:00:00"
    if failure == "pending": env.row["state"] = "PREPARED"
    if failure == "tamper": env.file.attached_to_name = "OTHER"
    with pytest.raises(Exception):
        env.module.read_own_before("A")
    env.read.assert_not_called()
    if failure in ("other", "pending"):
        env.verify.assert_not_called()


def test_denies_changed_authority_after_bytes(env):
    def read(*args):
        env.row["actor"] = "other"
        return PNG_BYTES
    env.read.side_effect = read
    with pytest.raises(Exception):
        env.module.read_own_before("A")


def test_denies_tampered_bytes(env):
    env.read.return_value = b"x" * len(PNG_BYTES)
    with pytest.raises(Exception):
        env.module.read_own_before("A")


def test_denies_unavailable_journal_or_private_bytes(env):
    env.verify.side_effect = ValueError("missing staging")
    with pytest.raises(Exception):
        env.module.read_own_before("A")
    env.read.assert_not_called()
    env.verify.side_effect = None
    env.read.side_effect = ValueError("missing bytes")
    with pytest.raises(Exception):
        env.module.read_own_before("A")


def test_patient_query_and_native_read_are_required(env):
    env.frappe.get_list.side_effect = lambda dt, **kw: (
        [] if dt == "Patient" else [{"name": kw["filters"]["name"]}]
    )
    with pytest.raises(Exception):
        env.module.read_own_before("A")
    env.read.assert_not_called()
