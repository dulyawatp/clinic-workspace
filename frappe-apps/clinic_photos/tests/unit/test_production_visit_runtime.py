"""Production visit activation uses the shared host enrollment proof."""

import hashlib
import json
import sys
import types
from unittest.mock import patch

import pytest

from clinic_photos.visit.runtime import enabled_for_site


@pytest.fixture
def enrollment(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    private = root / "sites" / "site.test" / "private"
    private.mkdir(parents=True)
    marker = root / "marker"
    raw = json.dumps({"version": 1, "site": "site.test", "private_path": str(private),
                      "installation_id": "a" * 64}).encode()
    marker.write_bytes(raw)
    marker.chmod(0o600)
    monkeypatch.setenv("CLINIC_UI_PRODUCTION_MARKER_PATH", str(marker))
    frappe = types.SimpleNamespace(local=types.SimpleNamespace(site="site.test"),
                                   get_site_path=lambda part: str(private))
    conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test",
            "clinic_ui_production_read_enabled": True, "clinic_ui_production_site": "site.test",
            "clinic_ui_production_marker_sha256": hashlib.sha256(raw).hexdigest()}
    with patch.dict(sys.modules, {"frappe": frappe}):
        yield conf, frappe, marker


def test_production_requires_valid_proof_without_actor(enrollment):
    conf, frappe, marker = enrollment
    assert enabled_for_site("site.test", conf)
    marker.write_bytes(b"invalid")
    assert not enabled_for_site("site.test", conf)


@pytest.mark.parametrize("change", [
    {"clinic_ui_production_read_enabled": False},
    {"clinic_ui_production_read_enabled": 1},
    {"clinic_ui_production_site": "other.test"},
    {"clinic_ui_production_marker_sha256": "0" * 64},
])
def test_production_rejects_invalid_settings(enrollment, change):
    conf, _, _ = enrollment
    conf.update(change)
    assert not enabled_for_site("site.test", conf)


def test_production_rejects_resolved_site_mismatch(enrollment):
    conf, frappe, _ = enrollment
    frappe.local.site = "other.test"
    assert not enabled_for_site("site.test", conf)


def test_production_rejects_wrong_private_path(enrollment, tmp_path):
    conf, frappe, _ = enrollment
    frappe.get_site_path = lambda part: str(tmp_path / "other" / "site.test" / "private")
    assert not enabled_for_site("site.test", conf)


def test_worker_gate_uses_production_proof_without_session(enrollment):
    from clinic_photos.publication.process import _visit_gate_enabled

    conf, _, marker = enrollment
    assert _visit_gate_enabled("site.test", conf)
    marker.unlink()
    assert not _visit_gate_enabled("site.test", conf)


def test_missing_ui_adapter_fails_closed_only_for_production(monkeypatch):
    monkeypatch.delenv("CLINIC_UI_PRODUCTION_MARKER_PATH", raising=False)
    conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    with patch.dict(sys.modules, {"clinic_ui.runtime.production_site": None}):
        assert enabled_for_site("site.test", conf)
        conf["clinic_ui_production_read_enabled"] = True
        assert not enabled_for_site("site.test", conf)


def test_blank_marker_selects_production_and_legacy_remains_available(monkeypatch):
    conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test"}
    monkeypatch.delenv("CLINIC_UI_PRODUCTION_MARKER_PATH", raising=False)
    assert enabled_for_site("site.test", conf)
    monkeypatch.setenv("CLINIC_UI_PRODUCTION_MARKER_PATH", "")
    assert not enabled_for_site("site.test", conf)


def test_explicit_production_without_marker_fails_closed(monkeypatch):
    monkeypatch.delenv("CLINIC_UI_PRODUCTION_MARKER_PATH", raising=False)
    conf = {"clinic_visit_photos_enabled": True, "clinic_visit_photos_site": "site.test",
            "clinic_ui_production_read_enabled": True}
    assert not enabled_for_site("site.test", conf)
