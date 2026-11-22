import importlib
import contextvars
import sys
import types

import pytest

from clinic_photos.publication import journal


@pytest.fixture
def runtime(monkeypatch):
    frappe = types.ModuleType("frappe")
    frappe.local = types.SimpleNamespace(site="site")
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.PermissionError = type("PermissionError", (Exception,), {})
    def throw(message, error):
        raise error(message)
    frappe.throw = throw
    model = types.ModuleType("frappe.model")
    document = types.ModuleType("frappe.model.document")
    document.Document = type("Document", (), {})
    monkeypatch.setitem(sys.modules, "frappe", frappe)
    monkeypatch.setitem(sys.modules, "frappe.model", model)
    monkeypatch.setitem(sys.modules, "frappe.model.document", document)
    name = "clinic_photos.clinic_photos.doctype.clinic_photo_save_operation.clinic_photo_save_operation"
    monkeypatch.delitem(sys.modules, name, raising=False)
    controller = importlib.import_module(name)
    yield frappe, controller
    sys.modules.pop(name, None)


@pytest.fixture
def values():
    return dict(doctype=journal.OPERATION_DOCTYPE, site="site", actor="doctor",
                kind="visit_annotation_append", request_key="key",
                identity_digest="a" * 64, payload_digest="b" * 64,
                state="SUBMITTED", draft="draft", expected_version=0,
                original="photo", visit_binding_json="{}", staged_key="key.json",
                staged_sha256="c" * 64, staged_size=1, staged_format="VISIT_ANNOTATION_JSON")


def make_doc(controller, values):
    class Doc(controller.ClinicPhotoSaveOperation):
        def __init__(self):
            self.data = dict(values)
        def get(self, key):
            return self.data.get(key)
        def as_dict(self):
            return dict(self.data)
        def is_new(self):
            return True
        @property
        def kind(self):
            return self.data["kind"]
    return Doc()


def test_trusted_scope_permits_each_phase_once(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with guard._insertion_scope(doc, values):
        doc.before_insert()
        doc.validate()
    with pytest.raises(frappe.PermissionError):
        doc.before_insert()


@pytest.mark.parametrize("user", ["doctor", "Administrator"])
def test_direct_native_hooks_denied(runtime, values, user):
    frappe, controller = runtime
    frappe.session.user = user
    doc = make_doc(controller, values)
    with pytest.raises(frappe.PermissionError):
        doc.before_insert()
    with pytest.raises(frappe.PermissionError):
        doc.validate()


@pytest.mark.parametrize("mutation", ["field", "kind", "extra", "other", "user", "site", "order", "nested"])
def test_scope_refuses_mutation_and_wrong_phase(runtime, values, mutation):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied), guard._insertion_scope(doc, values):
        if mutation == "field":
            doc.data["payload_digest"] = "tampered"
        elif mutation == "kind":
            doc.data["kind"] = "annotation_append"
        elif mutation == "extra":
            doc.data["annotation_json"] = "secret"
        elif mutation == "user":
            frappe.session.user = "Administrator"
        elif mutation == "site":
            frappe.local.site = "other"
        if mutation == "nested":
            with pytest.raises(journal.JournalAuthorityDenied):
                with guard._insertion_scope(doc, values):
                    pass
        elif mutation == "other":
            with pytest.raises(frappe.PermissionError):
                make_doc(controller, values).before_insert()
        elif mutation == "order":
            with pytest.raises(frappe.PermissionError):
                doc.validate()
        else:
            with pytest.raises(frappe.PermissionError):
                doc.before_insert()


def test_recheck_and_cleanup_after_exception(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(RuntimeError):
        with guard._insertion_scope(doc, values):
            doc.before_insert()
            doc.data["original"] = "other"
            with pytest.raises(frappe.PermissionError):
                doc.validate()
            raise RuntimeError()
    with pytest.raises(frappe.PermissionError):
        doc.before_insert()


def test_invalid_scope_metadata_and_updates_denied(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied):
        with guard._insertion_scope(doc, {**values, "annotation_json": "secret"}):
            pass
    doc.is_new = lambda: False
    with pytest.raises(frappe.PermissionError):
        doc.validate()


def test_metadata_type_change_is_not_exact(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied), guard._insertion_scope(doc, values):
        doc.data["expected_version"] = False
        with pytest.raises(frappe.PermissionError):
            doc.before_insert()


def test_repeated_before_insert_and_failure_poison_scope(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied), guard._insertion_scope(doc, values):
        doc.before_insert()
        with pytest.raises(frappe.PermissionError):
            doc.before_insert()
        with pytest.raises(frappe.PermissionError):
            doc.validate()


@pytest.mark.parametrize("change", ["user", "site", "unknown", "values"])
def test_validate_rechecks_context_and_snapshot(runtime, values, change):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied), guard._insertion_scope(doc, values):
        doc.before_insert()
        if change == "user":
            frappe.session.user = "other"
        elif change == "site":
            frappe.local.site = "other"
        elif change == "unknown":
            doc.data["unknown_clinical_data"] = "secret"
        else:
            values["draft"] = "other"
            doc.data["draft"] = "other"
        with pytest.raises(frappe.PermissionError):
            doc.validate()


@pytest.mark.parametrize("before", [False, True])
def test_scope_requires_both_phases_on_exit(runtime, values, before):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    _, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied):
        with guard._insertion_scope(doc, values):
            if before:
                doc.before_insert()


def test_caught_refusal_cannot_exit_successfully(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied):
        with guard._insertion_scope(doc, values):
            with pytest.raises(frappe.PermissionError):
                doc.validate()


@pytest.mark.parametrize("change", ["field", "user", "site"])
def test_scope_rechecks_after_validate(runtime, values, change):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied):
        with guard._insertion_scope(doc, values):
            doc.before_insert()
            doc.validate()
            if change == "field":
                doc.data["original"] = "other"
            elif change == "user":
                frappe.session.user = "other"
            else:
                frappe.local.site = "other"


def test_copied_context_cannot_reuse_scope_after_exit(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    frappe, controller = runtime
    doc = make_doc(controller, values)
    with pytest.raises(journal.JournalAuthorityDenied):
        with guard._insertion_scope(doc, values):
            copied = contextvars.copy_context()
    with pytest.raises(frappe.PermissionError):
        copied.run(doc.before_insert)
    assert copied.run(guard._scope.get)["phase"] == "refused"


def test_successful_scope_invalidates_copied_context(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    _, controller = runtime
    doc = make_doc(controller, values)
    with guard._insertion_scope(doc, values):
        copied = contextvars.copy_context()
        doc.before_insert()
        doc.validate()
    assert copied.run(guard._scope.get)["phase"] == "refused"


def test_propagating_exception_preserved(runtime, values):
    from clinic_photos.publication import visit_annotation_insert_guard as guard
    _, controller = runtime
    doc = make_doc(controller, values)
    original = RuntimeError("original")
    with pytest.raises(RuntimeError) as caught:
        with guard._insertion_scope(doc, values):
            doc.data["original"] = "other"
            raise original
    assert caught.value is original
