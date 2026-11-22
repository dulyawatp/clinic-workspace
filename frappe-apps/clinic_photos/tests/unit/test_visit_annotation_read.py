import importlib
import hashlib
import json
import sys
import types
from unittest.mock import Mock, patch

import pytest


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
    appointment = types.SimpleNamespace(name="A", patient="P", creation="ac")
    patient = types.SimpleNamespace(name="P", creation="pc")
    record = types.SimpleNamespace(name="slot", operation="op")
    photo = types.SimpleNamespace(name="photo")
    file = types.SimpleNamespace(name="file")
    frappe.get_doc = Mock(side_effect=lambda dt, name: {
        "Patient Appointment": appointment, "Patient": patient,
    }[dt])
    frappe.has_permission = Mock(return_value=True)
    frappe.db = types.SimpleNamespace(transaction_writes=0, get_value=Mock())
    with patch.dict(sys.modules, {"frappe": frappe}):
        sys.modules.pop("clinic_photos.visit.annotation_read", None)
        module = importlib.import_module("clinic_photos.visit.annotation_read")
        draft = module.visit_draft_name("site", "A", "Before", "photo", "doctor")
        rows = {
            "Clinic Photo Draft": dict(name=draft, author="doctor", owner="doctor",
                photo="photo", current_version_number=2, current_version=f"{draft}-v2"),
            "Clinic Photo Annotation Version": dict(name=f"{draft}-v2", draft=draft,
                version_number=2, annotation_file="drawing", storage_key="key", state="committed"),
        }
        frappe.db.get_value.side_effect = lambda dt, name, fields, **kw: rows[dt] if rows[dt]["name"] == name else None
        with patch.object(module.actor, "require_visit_actor", return_value="doctor"), \
             patch.object(module.pair, "_read_slot_structure", return_value=(record, photo, file)), \
             patch.object(module.pair, "_require_native_read_for_verified_slot") as native:
            yield types.SimpleNamespace(module=module, frappe=frappe, rows=rows,
                                        photo=photo, native=native)


def test_authority_selects_exact_committed_version_without_write_grant(env):
    env.frappe.has_permission.side_effect = lambda dt, action, **kw: action == "read"
    result = env.module.resolve_read_metadata("A", "Before")
    assert result.version_number == 2
    assert result.version_name == env.rows["Clinic Photo Annotation Version"]["name"]
    env.native.assert_called_once()
    assert all(call.args[1] == "read" for call in env.frappe.has_permission.call_args_list)


@pytest.mark.parametrize("mutation", [
    lambda e: e.rows["Clinic Photo Draft"].update(author="other"),
    lambda e: e.rows["Clinic Photo Draft"].update(owner="Administrator"),
    lambda e: e.rows["Clinic Photo Draft"].update(photo="other"),
    lambda e: e.rows["Clinic Photo Draft"].update(current_version="wrong"),
    lambda e: e.rows["Clinic Photo Annotation Version"].update(state="pending"),
    lambda e: setattr(e.photo, "name", "other"),
    lambda e: setattr(e.frappe.session, "user", "Administrator"),
])
def test_rejects_wrong_identity_or_uncommitted_version(env, mutation):
    mutation(env)
    with pytest.raises(PermissionError):
        env.module.resolve_read_metadata("A", "Before", version=2)


def test_wrong_slot_or_version_denied(env):
    with pytest.raises(PermissionError):
        env.module.resolve_read_metadata("A", "After", version=2)
    with pytest.raises(PermissionError):
        env.module.resolve_read_metadata("A", "Before", version=1)


@pytest.fixture
def published(env, tmp_path):
    from clinic_photos.publication import staging
    from clinic_photos.publication import visit_annotation_journal_codec as codec
    from test_visit_annotation_intent import binding, intent

    item = intent(expected_version=1, binding=binding(
        appointment="A", patient="P", published_slot="slot", original_operation="op"))
    content = item.canonical_visit_annotation.encode()
    staged = staging.StagedVisitAnnotation("a" * 32 + ".json", hashlib.sha256(content).hexdigest(),
                                           len(content), staging.VISIT_ANNOTATION_JSON)
    row = codec._metadata_values(item, staged)
    material = json.dumps(["clinic-visit-annotation-prepared-v1", "site", "doctor",
        "operation-1", item.target_identity, item.intent_digest, 1],
        ensure_ascii=True, separators=(",", ":")).encode()
    key = "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json"
    directory = tmp_path / "site" / "private" / "files"
    directory.mkdir(parents=True)
    path = directory / key.rsplit("/", 1)[-1]
    path.write_bytes(content)
    info = path.stat()
    row.update(name="operation-1", state="PUBLISHED", generation=1, reservation_key=key,
        expected_sha256=staged.sha256, expected_size=staged.size, published_file="drawing",
        published_domain_doctype="Clinic Photo Annotation Version",
        published_domain_name=f"{item.managed_draft}-v2", published_version=2,
        creation_identity=f"{info.st_dev}:{info.st_ino}")
    file_row = dict(name="drawing", file_url=key, file_name=path.name, owner="doctor",
        is_private=1, file_size=len(content),
        content_hash=hashlib.md5(content, usedforsecurity=False).hexdigest(),
        attached_to_doctype=None, attached_to_name=None, attached_to_field=None)
    env.rows["Clinic Photo Annotation Version"]["storage_key"] = key
    env.frappe.get_site_path = lambda *parts: str(tmp_path / "site" / "/".join(parts))
    previous = env.frappe.db.get_value.side_effect
    env.frappe.db.get_value.side_effect = lambda dt, name, fields, **kw: (
        file_row if dt == "File" and name == "drawing" else previous(dt, name, fields, **kw))
    env.frappe.db.get_all = Mock(side_effect=lambda dt, **kw: [row] if dt == "Clinic Photo Save Operation" else [file_row])
    with patch("clinic_photos.publication.exclusion._site_binding", return_value=("site", str(tmp_path))):
        yield types.SimpleNamespace(env=env, row=row, file=file_row, path=path, item=item)


@pytest.mark.parametrize("suffix,data", [
    (".png", b"\x89PNG\r\n\x1a\npayload"),
    (".jpg", b"\xff\xd8\xffpayload"),
])
def test_verified_original_private_bytes(published, suffix, data):
    path = published.path.with_name("original" + suffix)
    path.write_bytes(data)
    path.chmod(0o600)
    info = path.stat()
    key = "/private/files/" + path.name
    read = published.env.module.read_verified_original_bytes
    args = ("site", key, len(data), f"{info.st_dev}:{info.st_ino}", hashlib.sha256(data).hexdigest())
    assert read(*args) == data
    with pytest.raises(ValueError):
        read(*args[:-1], "0" * 64)
    with pytest.raises(ValueError):
        read(*args[:3], f"{info.st_dev}:{info.st_ino + 1}", args[4])
    path.chmod(0o644)
    with pytest.raises(published.env.module.writer.FirstObjectUncertain):
        read(*args)
    path.chmod(0o600)
    link = path.with_name("hardlink")
    link.hardlink_to(path)
    with pytest.raises(published.env.module.writer.FirstObjectUncertain):
        read(*args)
    link.unlink()
    path.unlink()
    path.symlink_to(published.path)
    with pytest.raises((OSError, ValueError)):
        read(*args)


def test_verified_original_rechecks_private_mode_after_read(published):
    path = published.path.with_name("original.png")
    data = b"\x89PNG\r\n\x1a\npayload"
    path.write_bytes(data)
    path.chmod(0o600)
    info = path.stat()
    module = published.env.module
    original_read = module.os.read

    def change_mode(fd, size):
        result = original_read(fd, size)
        path.chmod(0o644)
        return result

    with patch.object(module.os, "read", side_effect=change_mode):
        with pytest.raises(module.writer.FirstObjectUncertain):
            module.read_verified_original_bytes(
                "site", "/private/files/original.png", len(data),
                f"{info.st_dev}:{info.st_ino}", hashlib.sha256(data).hexdigest())


@pytest.mark.parametrize("key,data,size,digest", [
    ("/private/files/a.jpg", b"\x89PNG\r\n\x1a\nx", 9, hashlib.sha256(b"\x89PNG\r\n\x1a\nx").hexdigest()),
    ("/private/files/a.jpeg", b"\xff\xd8\xffx", 4, "0" * 64),
    ("/private/files/a%2epng", b"x", 1, "0" * 64),
    ("/private/files/a.png", b"x", 8 * 1024 * 1024 + 1, "0" * 64),
])
def test_verified_original_rejects_invalid_evidence(published, key, data, size, digest):
    path = published.path.with_name(key.rsplit("/", 1)[-1])
    path.write_bytes(data)
    path.chmod(0o600)
    info = path.stat()
    with pytest.raises(ValueError):
        published.env.module.read_verified_original_bytes(
            "site", key, size, f"{info.st_dev}:{info.st_ino}", digest)


def test_read_committed_author_drawing_after_write_grant_revocation(published):
    e = published.env
    e.frappe.has_permission.side_effect = lambda dt, action, **kw: action == "read"
    result = e.module.read_committed("A", "Before")
    assert result.version_number == 2
    assert result.annotation == json.loads(published.item.canonical_visit_annotation)


@pytest.mark.parametrize("field,value", [
    ("expected_sha256", "0" * 64), ("creation_identity", "1:2"),
    ("actor", "other"), ("published_domain_name", "other"),
])
def test_committed_journal_tampering_denied(published, field, value):
    published.row[field] = value
    with pytest.raises(PermissionError):
        published.env.module.read_committed("A", "Before")


def test_committed_file_tampering_denied(published):
    published.path.write_bytes(b"x" * published.path.stat().st_size)
    with pytest.raises(PermissionError):
        published.env.module.read_committed("A", "Before")


def test_file_symlink_and_duplicate_journal_denied(published):
    published.path.unlink()
    published.path.symlink_to("missing")
    with pytest.raises(PermissionError):
        published.env.module.read_committed("A", "Before")
    published.env.frappe.db.get_all.side_effect = lambda dt, **kw: [published.row, published.row]
    with pytest.raises(PermissionError):
        published.env.module.read_committed("A", "Before")


def test_duplicate_file_holder_denied(published):
    alias = dict(published.file, name="other")
    published.env.frappe.db.get_all.side_effect = lambda dt, **kw: (
        [published.row] if dt == "Clinic Photo Save Operation" else [published.file, alias])
    with pytest.raises(PermissionError):
        published.env.module.read_committed("A", "Before")


def test_resolve_target_without_draft(env):
    env.frappe.db.get_value.side_effect = lambda *args, **kwargs: None
    target = env.module.resolve_read_target("A", "Before")
    assert (target.appointment, target.original_photo, target.actor) == ("A", "photo", "doctor")
    assert "photo" not in repr(target) and "doctor" not in repr(target)
    result = env.module.read_committed("A", "Before")
    assert result.version_number == 0 and result.annotation is None
    with pytest.raises(PermissionError):
        env.module.read_committed("A", "Before", version=1)
    with pytest.raises(PermissionError):
        env.module.read_committed("A", "Before", operation="op")


def test_valid_zero_head_is_blank_but_invalid_zero_head_denied(env):
    draft = env.rows["Clinic Photo Draft"]
    draft.update(current_version_number=0, current_version=None)
    assert env.module.read_committed("A", "Before").annotation is None
    with pytest.raises(PermissionError):
        env.module.read_committed("A", "Before", version=1)
    draft["current_version"] = "unexpected"
    with pytest.raises(PermissionError):
        env.module.read_committed("A", "Before")
    draft["current_version"] = None
    draft["author"] = "other"
    with pytest.raises(PermissionError):
        env.module.read_committed("A", "Before")


def test_receipt_identifiers_must_match(published):
    read = published.env.module.read_committed
    assert read("A", "Before", operation="operation-1",
                request_key=published.item.request_key).version_number == 2
    for kwargs in ({"operation": "other"}, {"request_key": "other"},
                   {"operation": ""}, {"request_key": ""}):
        with pytest.raises(PermissionError):
            read("A", "Before", **kwargs)


def test_author_read_does_not_lock_role_or_metadata(published):
    e = published.env
    assert e.module.read_committed("A", "Before").version_number == 2
    e.module.actor.require_visit_actor.assert_called_with(doctor=True, for_update=False)
    assert e.frappe.db.get_value.call_count >= 3
    assert all(not call.kwargs.get("for_update") for call in e.frappe.db.get_value.call_args_list)


def test_http_relative_site_root_reads_committed(published, monkeypatch):
    e = published.env
    sites = published.path.parents[3]
    monkeypatch.chdir(sites)
    e.frappe.local.sites_path = "."
    e.frappe.get_site_path = lambda *parts: "./site/" + "/".join(parts)
    # A request must not depend on worker-only absolute sites_path.
    with patch("clinic_photos.publication.exclusion._site_binding", side_effect=AssertionError):
        assert e.module.read_committed("A", "Before").version_number == 2


@pytest.mark.parametrize("path", [
    "./other/private/files", "./site/private/../private/files",
    "/wrong/site/private/files", "/wrong/site/private/../private/files",
])
def test_http_relative_site_root_rejects_wrong_or_traversal_files(published, monkeypatch, path):
    e = published.env
    monkeypatch.chdir(published.path.parents[3])
    e.frappe.local.sites_path = "."
    e.frappe.get_site_path = lambda *parts: path if parts[-1] == "files" else "./site/" + "/".join(parts)
    with pytest.raises(PermissionError):
        e.module.read_committed("A", "Before")


def test_http_relative_site_root_rejects_symlinked_site(published, monkeypatch):
    e = published.env
    sites = published.path.parents[3]
    (sites / "alias").symlink_to(sites / "site", target_is_directory=True)
    monkeypatch.chdir(sites)
    e.frappe.local.site = "alias"
    e.frappe.local.sites_path = "."
    e.frappe.get_site_path = lambda *parts: "./alias/" + "/".join(parts)
    with pytest.raises(Exception):
        e.module._read_file_bytes("alias", published.row["reservation_key"],
                                  published.row["expected_size"], published.row["creation_identity"])
