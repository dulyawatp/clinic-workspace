"""Visit API contract; no installed Frappe or database implied."""

import importlib
import base64
import hashlib
import io
import sys
import types
from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def env():
    frappe = types.ModuleType("frappe")
    frappe.PermissionError = PermissionError
    frappe.ValidationError = ValueError
    frappe.conf = {}
    frappe.local = types.SimpleNamespace(site="site.test")
    frappe.session = types.SimpleNamespace(user="doctor")
    frappe.whitelist = Mock(side_effect=lambda **kwargs: lambda fn: fn)
    wrappers = types.ModuleType("werkzeug.wrappers")

    class FakeResponse:
        def __init__(self, data, content_type):
            self.data = data
            self.content_type = content_type
            self.headers = {}

    wrappers.Response = FakeResponse
    with patch.dict(sys.modules, {"frappe": frappe, "werkzeug.wrappers": wrappers}):
        sys.modules.pop("clinic_photos.api.visit_photos", None)
        api = importlib.import_module("clinic_photos.api.visit_photos")
        with patch.object(api.photo_save, "require_post") as post, \
             patch.object(api.photo_save, "require_native_session") as session:
            yield types.SimpleNamespace(frappe=frappe, api=api, post=post, session=session)
        sys.modules.pop("clinic_photos.api.visit_photos", None)


def test_disabled_and_strict_config_fail_before_private_access(env):
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    for value in (None, 1, "true", False):
        env.frappe.conf["clinic_visit_photos_enabled"] = value
        with pytest.raises(PermissionError):
            env.api.read_pair({"appointment": "A"})


def test_read_pair_filters_sensitive_private_projection(env):
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    result = {"appointment": "A", "patient": "P", "before": {
        "photo": "PHOTO", "file": "FILE", "body_area": "Arm", "viewpoint": "Front",
        "storage_key": "/private/files/secret",
    }, "after": None}
    with patch.object(env.api, "_read_pair", return_value=result) as read:
        assert env.api.read_pair({"appointment": "A"}) == {
            "appointment": "A", "before": {
                "photo": "PHOTO", "body_area": "Arm", "viewpoint": "Front",
            }, "after": None,
        }
    read.assert_called_once_with("A")
    env.post.assert_called_once()
    env.session.assert_called_once()


@pytest.mark.parametrize("flag,enabled", [(True, True), (False, False), (1, False), ("true", False), (None, False)])
def test_drawing_capability_requires_exact_boolean(env, flag, enabled):
    env.frappe.conf.update(clinic_visit_photos_enabled=True, clinic_visit_photos_site="site.test",
                           clinic_visit_photo_annotations_enabled=flag)
    with patch.object(env.api, "_read_pair", return_value={"before": None, "after": None}):
        result = env.api.read_pair({"appointment": "A"})
    assert ("drawings_enabled" in result) is enabled
    if enabled:
        assert result["drawings_enabled"] is True


def test_consent_actions_are_strict_and_return_no_evidence(env):
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    with patch.object(env.api, "_change_consent") as change:
        assert env.api.grant_consent({"appointment": "A", "attested": True,
                                      "evidence": "document-123"}) == {"ok": True}
        change.assert_called_once_with("A", True, "document-123", attested=True)
        change.reset_mock()
        assert env.api.revoke_consent({"appointment": "A"}) == {"ok": True}
        change.assert_called_once_with("A", False)
        for payload in (
            {"appointment": "A", "attested": 1, "evidence": "document-123"},
            {"appointment": " A", "attested": True, "evidence": "document-123"},
            {"appointment": "A", "attested": True, "evidence": "x" * 141},
            {"appointment": "A", "attested": True, "evidence": "ok", "extra": 1},
        ):
            with pytest.raises(ValueError):
                env.api.grant_consent(payload)


def test_guest_and_private_exception_fail_safely(env):
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    env.frappe.session.user = "Guest"
    with pytest.raises(PermissionError):
        env.api.read_pair({"appointment": "A"})
    env.frappe.session.user = "doctor"
    with patch.object(env.api, "_read_pair", side_effect=RuntimeError("secret")):
        with pytest.raises(PermissionError, match="Visit photo request unavailable"):
            env.api.read_pair({"appointment": "A"})


def test_rejects_extra_rpc_args(env):
    env.frappe.conf["clinic_visit_photos_enabled"] = True
    env.frappe.conf["clinic_visit_photos_site"] = "site.test"
    with pytest.raises(ValueError):
        env.api.read_pair({"appointment": "A"}, extra="x")


@pytest.mark.parametrize("method,payload", [
    ("read_pair", {"appointment": "A"}),
    ("read_image", {"appointment": "A", "slot": "Before"}),
    ("grant_consent", {"appointment": "A", "attested": True, "evidence": "document-123"}),
    ("revoke_consent", {"appointment": "A"}),
])
def test_v1_dispatch_cmd_exact_and_extra_rejected(env, method, payload):
    env.frappe.conf.update(clinic_visit_photos_enabled=True, clinic_visit_photos_site="site.test")
    cmd = f"clinic_photos.api.visit_photos.{method}"
    env.frappe.local.form_dict = {"cmd": cmd}
    endpoint = getattr(env.api, method)
    with patch.object(env.api, "_read_pair", side_effect=RuntimeError), \
         patch.object(env.api, "_change_consent", side_effect=RuntimeError):
        with pytest.raises(PermissionError):
            endpoint(payload, cmd=cmd)
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd + "_wrong")
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd, other="x")
        env.frappe.local.form_dict = {"cmd": "other"}
        with pytest.raises(ValueError):
            endpoint(payload, cmd=cmd)


def test_registered_post_only_and_native_guards_are_required(env):
    assert env.frappe.whitelist.call_count >= 4
    assert all(call.kwargs == {"methods": ["POST"]}
               for call in env.frappe.whitelist.call_args_list[-4:])
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    with patch.object(env.api, "_read_pair") as read:
        env.post.side_effect = PermissionError("POST required")
        with pytest.raises(PermissionError):
            env.api.read_pair({"appointment": "A"})
        env.post.side_effect = None
        env.session.side_effect = PermissionError("session required")
        with pytest.raises(PermissionError):
            env.api.read_pair({"appointment": "A"})
        read.assert_not_called()


def test_runtime_gate_requires_exact_server_site(env):
    from clinic_photos.visit.runtime import enabled_for_site

    config = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    assert enabled_for_site("site.test", config)
    assert not enabled_for_site("other.test", config)
    assert not enabled_for_site("", config)
    assert not enabled_for_site("site.test", {"clinic_visit_photos_enabled": True})
    assert not enabled_for_site("site.test", {**config, "clinic_visit_photos_enabled": 1})
    env.frappe.conf.update(config)
    env.frappe.local.site = "other.test"
    with pytest.raises(PermissionError):
        env.api.read_pair({"appointment": "A"})


def test_image_response_uses_pair_and_protected_file_content(env):
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
        "J0J9AAAAAElFTkSuQmCC"
    )
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    photo = types.SimpleNamespace(name="PHOTO", original_file="FILE", storage_key=key,
                                  original_sha256=hashlib.sha256(data).hexdigest())
    file = types.SimpleNamespace(name="FILE", file_url=key, is_private=1,
                                 attached_to_doctype="Clinic Photo", attached_to_name="PHOTO",
                                 file_size=len(data), get_content=Mock(return_value=data))
    env.frappe.get_doc = Mock(side_effect=lambda dt, name: {"Clinic Photo": photo, "File": file}[dt])
    projection = {"appointment": "A", "before": {"photo": "PHOTO", "file": "FILE"}, "after": None}
    with patch.object(env.api, "_read_pair", return_value=projection):
        response = env.api.read_image({"appointment": "A", "slot": "Before"})
    assert response.data == data
    assert response.content_type == "image/png"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "Location" not in response.headers
    assert "FILE" not in str(response.headers) and key not in str(response.headers)
    file.get_content.assert_called_once_with()


def test_image_rejects_missing_slot_and_mismatched_file_before_content(env):
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    with patch.object(env.api, "_read_pair", return_value={"appointment": "A", "before": None}):
        with pytest.raises(PermissionError):
            env.api.read_image({"appointment": "A", "slot": "Before"})
    with pytest.raises(ValueError):
        env.api.read_image({"appointment": "A", "slot": "other"})
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    photo = types.SimpleNamespace(name="PHOTO", original_file="FILE", storage_key=key)
    file = types.SimpleNamespace(name="WRONG", file_url=key, is_private=1,
                                 attached_to_doctype="Clinic Photo", attached_to_name="PHOTO",
                                 file_size=3, get_content=Mock())
    env.frappe.get_doc = Mock(side_effect=lambda dt, name: {"Clinic Photo": photo, "File": file}[dt])
    projection = {"appointment": "A", "before": {"photo": "PHOTO", "file": "FILE"}}
    with patch.object(env.api, "_read_pair", return_value=projection):
        with pytest.raises(PermissionError):
            env.api.read_image({"appointment": "A", "slot": "Before"})
    file.get_content.assert_not_called()


def test_image_denies_failed_pair_before_any_file_access(env):
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    env.frappe.get_doc = Mock()
    with patch.object(env.api, "_read_pair", side_effect=PermissionError("private")):
        with pytest.raises(PermissionError, match="Visit photo request unavailable"):
            env.api.read_image({"appointment": "A", "slot": "After"})
    env.frappe.get_doc.assert_not_called()


def test_image_enforces_protected_content_and_digest(env):
    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
        "J0J9AAAAAElFTkSuQmCC"
    )
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".png"
    photo = types.SimpleNamespace(name="PHOTO", original_file="FILE", storage_key=key,
                                  original_sha256="0" * 64)
    file = types.SimpleNamespace(name="FILE", file_url=key, is_private=1,
                                 attached_to_doctype="Clinic Photo", attached_to_name="PHOTO",
                                 file_size=len(data), get_content=Mock(return_value=data))
    env.frappe.get_doc = Mock(side_effect=lambda dt, name: {"Clinic Photo": photo, "File": file}[dt])
    projection = {"appointment": "A", "before": {"photo": "PHOTO", "file": "FILE"}}
    with patch.object(env.api, "_read_pair", return_value=projection):
        with pytest.raises(PermissionError):
            env.api.read_image({"appointment": "A", "slot": "Before"})
        photo.original_sha256 = hashlib.sha256(data).hexdigest()
        file.get_content.side_effect = PermissionError("protected read denied")
        with pytest.raises(PermissionError):
            env.api.read_image({"appointment": "A", "slot": "Before"})


def test_image_accepts_verified_jpeg(env):
    from PIL import Image

    env.frappe.conf.update(clinic_visit_photos_enabled=True,
                           clinic_visit_photos_site="site.test")
    output = io.BytesIO()
    Image.new("RGB", (1, 1)).save(output, format="JPEG")
    data = output.getvalue()
    key = "/private/files/clinic-visit-photo-" + "a" * 32 + ".jpg"
    photo = types.SimpleNamespace(name="PHOTO", original_file="FILE", storage_key=key,
                                  original_sha256=hashlib.sha256(data).hexdigest())
    file = types.SimpleNamespace(name="FILE", file_url=key, is_private=1,
                                 attached_to_doctype="Clinic Photo", attached_to_name="PHOTO",
                                 file_size=len(data), get_content=Mock(return_value=data))
    env.frappe.get_doc = Mock(side_effect=lambda dt, name: {"Clinic Photo": photo, "File": file}[dt])
    with patch.object(env.api, "_read_pair", return_value={
        "appointment": "A", "after": {"photo": "PHOTO", "file": "FILE"}
    }):
        response = env.api.read_image({"appointment": "A", "slot": "After"})
    assert response.data == data and response.content_type == "image/jpeg"
