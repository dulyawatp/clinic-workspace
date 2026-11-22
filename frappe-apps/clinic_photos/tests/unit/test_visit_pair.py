import importlib
import sys
import types
from dataclasses import replace
from unittest.mock import Mock, patch

import pytest

from clinic_photos.publication.contracts import (
    PreparedOperation, PublicationValidationError, VisitBinding,
)


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(site="site")
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.PermissionError = PermissionError
    frappe.whitelist = lambda **kwargs: lambda fn: fn
    frappe.throw = lambda msg, exc=ValueError: (_ for _ in ()).throw(exc(msg))
    frappe.has_permission = Mock(return_value=True)
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac")
    patient = types.SimpleNamespace(name="P", creation="pc")
    binding = VisitBinding(
        appointment="A", patient="P", slot="Before", encounter=None,
        body_area="Arm", viewpoint="Front", appointment_creation="ac",
        patient_creation="pc", encounter_creation=None, grant_audit="old-revoked-grant",
    )
    slot = types.SimpleNamespace(
        name="key", slot_key="key", appointment="A", patient="P", slot="Before",
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
        id="op", state="PUBLISHED", kind="visit_original_create", site="site",
        request=types.SimpleNamespace(visit_binding=binding), published=outcome,
    )
    docs = {"Patient Appointment": appointment, "Patient": patient,
            "Clinic Visit Photo Slot": slot, "Clinic Photo": photo, "File": file}
    frappe.get_doc = Mock(side_effect=lambda dt, name, **kwargs: docs[dt])
    frappe.db = types.SimpleNamespace(transaction_writes=0, get_value=Mock(side_effect=lambda dt, filters, *a, **kw:
        "key" if filters["slot"] == "Before" else None))
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.pair", None)
        from clinic_photos.publication import journal
        try:
            pair = importlib.import_module("clinic_photos.visit.pair")
        except ModuleNotFoundError as error:
            if error.name != "clinic_photos.visit.pair":
                raise
            pair = None
        with patch.object(journal, "load_verified_operation", return_value=operation) as load:
            yield types.SimpleNamespace(frappe=frappe, pair=pair, appointment=appointment,
                patient=patient, binding=binding, slot=slot, photo=photo, file=file,
                operation=operation, load=load)


def test_history_reads_published_metadata_without_consent_or_writes(env):
    assert env.pair is not None
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None
    )
    result = env.pair.read_pair("A")
    assert result == {"appointment": "A", "patient": "P", "before": {
        "photo": "photo", "file": "file", "body_area": "Arm", "viewpoint": "Front",
    }, "after": None}
    assert all(call.args[1] == "read" for call in env.frappe.has_permission.call_args_list)
    assert all(not call.kwargs.get("for_update") for call in env.frappe.get_doc.call_args_list)
    assert all(not call.kwargs.get("for_update") for call in env.frappe.db.get_value.call_args_list)
    assert not env.load.call_args.kwargs.get("for_update")


def test_slot_structure_checks_links_without_native_permission_hooks(env):
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = None
    env.frappe.db.get_value.return_value = env.slot.name
    env.frappe.has_permission.side_effect = AssertionError("unexpected permission hook")
    assert env.pair._read_slot_structure(env.appointment, env.patient, "Before") == (
        env.slot, env.photo, env.file,
    )
    env.frappe.has_permission.assert_not_called()


def test_locked_slot_structure_locks_only_slot_photo_and_file(env):
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = None
    env.frappe.db.get_value.return_value = env.slot.name
    env.pair._read_slot_structure(env.appointment, env.patient, "Before", for_update=True)
    assert env.frappe.db.get_value.call_args.kwargs["for_update"] is True
    assert all(call.kwargs["for_update"] is True for call in env.frappe.get_doc.call_args_list)
    assert not env.load.call_args.kwargs.get("for_update")


def test_visit_file_metadata_requires_exact_pair_scope_and_native_permission(env):
    from clinic_photos.access import frappe_context, permissions

    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    env.photo.storage_key = env.file.file_url = env.operation.published.reservation.key = key
    persisted_file = dict(vars(env.file))
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        persisted_file.copy() if dt == "File" and filters == env.file.name else
        env.slot.name if dt == "Clinic Visit Photo Slot" and filters["slot"] == "Before" else None
    )
    env.frappe.session = types.SimpleNamespace(user="doctor")
    record = types.SimpleNamespace(storage_key=key, registered=True, inconsistent=False, references=())
    actor = types.SimpleNamespace(user="doctor", enabled=True, clinician=True)
    with patch.object(frappe_context, "classify_file", return_value=record), \
         patch.object(frappe_context, "current_actor", return_value=actor), \
         patch.object(frappe_context, "current_publication", return_value=None):
        assert not permissions.file_has_permission(env.file, user="doctor", ptype="read")

        def native(doctype, ptype, doc):
            if doctype == "File":
                assert permissions.file_has_permission(doc, user="doctor", ptype=ptype)
                assert not permissions.file_has_permission(doc, user="other", ptype=ptype)
                other = types.SimpleNamespace(**vars(doc))
                assert not permissions.file_has_permission(other, user="doctor", ptype=ptype)
                env.frappe.local.site = "other"
                assert not permissions.file_has_permission(doc, user="doctor", ptype=ptype)
                env.frappe.local.site = "site"
                doc.file_url = key.replace("clinic", "%63linic")
                assert not permissions.file_has_permission(doc, user="doctor", ptype=ptype)
                doc.file_url = key
            return True

        env.frappe.has_permission.side_effect = native
        assert env.pair.read_pair("A")["before"]["file"] == "file"
        assert not permissions.file_has_permission(env.file, user="doctor", ptype="read")

        def denied(doctype, ptype, doc):
            if doctype == "File":
                assert permissions.file_has_permission(doc, user="doctor", ptype=ptype)
                return False
            return True

        env.frappe.has_permission.side_effect = denied
        with pytest.raises(PermissionError):
            env.pair.read_pair("A")
        assert not permissions.file_has_permission(env.file, user="doctor", ptype="read")

        record.kind = "pending"
        with patch.object(frappe_context, "_pending_content_matches",
                          side_effect=lambda _record, doc: doc is env.file):
            assert permissions.file_has_permission(env.file, user="doctor", ptype="read")
            assert not permissions.file_has_permission(env.file, user="other", ptype="read")
            assert not permissions.file_has_permission(
                types.SimpleNamespace(**vars(env.file)), user="doctor", ptype="read"
            )


def test_visit_photo_metadata_requires_exact_pair_scope(env):
    from clinic_photos.access import permissions

    key = "/private/files/clinic-visit-photo-" + "b" * 32 + ".png"
    env.photo.storage_key = env.file.file_url = env.operation.published.reservation.key = key
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None
    )
    assert not permissions.photo_has_permission(env.photo, user="doctor", ptype="read")

    def native(doctype, ptype, doc):
        if doctype == "Clinic Photo":
            assert permissions.photo_has_permission(doc, user="doctor", ptype=ptype)
            assert not permissions.photo_has_permission(doc, user="other", ptype=ptype)
            other = types.SimpleNamespace(**vars(doc))
            other.name = "other"
            assert not permissions.photo_has_permission(other, user="doctor", ptype=ptype)
        if doctype == "File":
            reloaded = types.SimpleNamespace(**vars(env.photo))
            assert permissions.photo_has_permission(reloaded, user="doctor", ptype="read")
            reloaded.original_file = "different"
            assert not permissions.photo_has_permission(reloaded, user="doctor", ptype="read")
        return True

    env.frappe.has_permission.side_effect = native
    assert env.pair.read_pair("A")["before"]["photo"] == "photo"
    assert not permissions.photo_has_permission(env.photo, user="doctor", ptype="read")
    env.operation.state = "PREPARED"
    with pytest.raises(PermissionError):
        env.pair.read_pair("A")
    assert not permissions.photo_has_permission(env.photo, user="doctor", ptype="read")


def test_protected_visit_bytes_require_published_exact_pair(env):
    from clinic_photos.access import frappe_context

    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None
    )
    env.frappe.db.get_all = Mock(side_effect=lambda dt, **kwargs: (
        [{"name": "op", "kind": "visit_original_create"}]
        if dt == frappe_context.SAVE_OPERATION_DOCTYPE else [{"name": env.slot.name}]
    ))
    record = types.SimpleNamespace(
        storage_key=env.photo.storage_key,
        references=[types.SimpleNamespace(doctype="Clinic Photo", name="photo")],
    )
    assert frappe_context._visit_protected_read(record, env.file) is True
    env.operation.state = "PREPARED"
    with pytest.raises(PermissionError):
        frappe_context._visit_protected_read(record, env.file)


def test_visit_marker_denies_even_when_operation_and_slot_are_missing(env):
    from clinic_photos.access import frappe_context

    key = "/private/files/clinic-visit-photo-0123456789abcdef0123456789abcdef.png"
    env.frappe.db.get_all = Mock(return_value=[])
    record = types.SimpleNamespace(
        storage_key=key,
        references=[types.SimpleNamespace(doctype="Clinic Photo", name="photo")],
    )
    with pytest.raises(PermissionError):
        frappe_context._visit_protected_read(record, env.file)


def test_visit_noncanonical_alias_denied_before_legacy_policy(env):
    from clinic_photos.access import frappe_context

    key = "/private/files/clinic-visit-photo-0123456789abcdef0123456789abcdef.png"
    env.file.file_url = key.replace("clinic", "%63linic")
    persisted_file = dict(vars(env.file))
    env.frappe.db.get_all = Mock(return_value=[])
    env.frappe.db.get_value.side_effect = lambda dt, name, *a, **kw: (
        persisted_file.copy() if dt == "File" and name == env.file.name else None
    )
    with patch.object(frappe_context, "_pending_content_matches", return_value=False), \
         patch.object(frappe_context, "current_actor"), \
         patch.object(frappe_context, "_visit_protected_read", return_value=True), \
         patch.object(frappe_context, "_require_protected_read_policy"), \
         patch("clinic_photos.access.entrypoints.mark_protected_request"):
        with pytest.raises(PermissionError):
            frappe_context.require_protected_read(env.file)


@pytest.mark.parametrize("case", ["missing_operation", "missing_slot", "wrong_file", "native_denial", "dirty"])
def test_protected_visit_byte_read_fails_closed(env, case):
    from clinic_photos.access import frappe_context

    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None
    )
    env.frappe.db.get_all = Mock(side_effect=lambda dt, **kwargs: (
        ([] if case == "missing_operation" else [{"name": "op", "kind": "visit_original_create"}])
        if dt == frappe_context.SAVE_OPERATION_DOCTYPE else
        ([] if case == "missing_slot" else [{"name": env.slot.name}])
    ))
    if case == "wrong_file":
        env.file.name = "other"
    if case == "native_denial":
        env.frappe.has_permission.side_effect = lambda dt, ptype, **kw: dt != "Patient"
    if case == "dirty":
        env.frappe.db.transaction_writes = 1
    record = types.SimpleNamespace(
        storage_key=env.photo.storage_key,
        references=[types.SimpleNamespace(doctype="Clinic Photo", name="photo")],
    )
    with pytest.raises(PermissionError):
        frappe_context._visit_protected_read(record, env.file)


@pytest.mark.parametrize("doctype", ["Patient Appointment", "Patient", "Clinic Photo", "File"])
def test_denies_missing_native_read(env, doctype):
    assert env.pair is not None
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None)
    env.frappe.has_permission.side_effect = lambda dt, ptype, **kw: dt != doctype
    with pytest.raises(PermissionError):
        env.pair.read_pair("A")


@pytest.mark.parametrize("target,field,value", [
    ("slot", "patient", "other"), ("slot", "body_area", "other"),
    ("photo", "original_file", "other"), ("file", "attached_to_name", "other"),
    ("operation", "state", "SUBMITTED"),
])
def test_rejects_tampered_links(env, target, field, value):
    assert env.pair is not None
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None)
    setattr(getattr(env, target), field, value)
    with pytest.raises(PermissionError):
        env.pair.read_pair("A")


def test_unavailable_journal_does_not_return_metadata(env):
    assert env.pair is not None
    env.slot.name = env.slot.slot_key = env.pair.slot_key("A", "Before")
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        env.slot.name if filters["slot"] == "Before" else None)
    env.load.side_effect = RuntimeError("visit publication not supported")
    with pytest.raises(RuntimeError):
        env.pair.read_pair("A")


@pytest.mark.parametrize("writes", [1, None, True])
def test_refuses_non_readonly_or_unknown_transaction(env, writes):
    assert env.pair is not None
    env.frappe.db.transaction_writes = writes
    with pytest.raises(PermissionError):
        env.pair.read_pair("A")
    env.frappe.get_doc.assert_not_called()


def test_contract_rejects_invalid_published_visit_reservation():
    with pytest.raises(PublicationValidationError):
        PreparedOperation(
            id="op", site="site", actor="doctor", kind="visit_original_create",
            payload_digest="digest", key="/private/files/p.png", generation=1,
            draft=None, expected_version=None, synthetic_patient=None,
            target_identity="visit:A", expected_sha256="0" * 64, expected_size=1,
            state="PUBLISHED",
        )


def test_pair_returns_both_and_rejects_mismatched_after(env):
    assert env.pair is not None
    before_key = env.pair.slot_key("A", "Before")
    after_key = env.pair.slot_key("A", "After")
    env.slot.name = env.slot.slot_key = before_key
    after_binding = replace(env.binding, slot="After", encounter="E", encounter_creation="ec")
    after_slot = types.SimpleNamespace(**vars(env.slot))
    after_slot.name = after_slot.slot_key = after_key
    after_slot.slot, after_slot.operation, after_slot.photo = "After", "op2", "photo2"
    after_photo = types.SimpleNamespace(**vars(env.photo))
    after_photo.name, after_photo.original_file = "photo2", "file2"
    after_file = types.SimpleNamespace(**vars(env.file))
    after_file.name, after_file.attached_to_name = "file2", "photo2"
    after_outcome = types.SimpleNamespace(**vars(env.operation.published))
    after_outcome.domain_name, after_outcome.file_name = "photo2", "file2"
    after_op = types.SimpleNamespace(**vars(env.operation))
    after_op.id, after_op.request, after_op.published = (
        "op2", types.SimpleNamespace(visit_binding=after_binding), after_outcome
    )
    env.frappe.db.get_value.side_effect = lambda dt, filters, *a, **kw: (
        before_key if filters["slot"] == "Before" else after_key)
    get_doc = env.frappe.get_doc.side_effect
    env.frappe.get_doc.side_effect = lambda dt, name, **kw: (
        after_slot if name == after_key else after_photo if name == "photo2"
        else after_file if name == "file2" else get_doc(dt, name, **kw))
    env.load.side_effect = lambda name: after_op if name == "op2" else env.operation
    result = env.pair.read_pair("A")
    assert result["after"] == {"photo": "photo2", "file": "file2",
                               "body_area": "Arm", "viewpoint": "Front"}
    after_slot.viewpoint = "Side"
    after_op.request.visit_binding = replace(after_binding, viewpoint="Side")
    with pytest.raises(PermissionError):
        env.pair.read_pair("A")
