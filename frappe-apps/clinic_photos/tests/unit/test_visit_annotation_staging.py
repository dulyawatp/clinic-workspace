from dataclasses import FrozenInstanceError, replace
import hashlib
import os
import re

import pytest

from clinic_photos.publication import staging, validation
from test_async_publication_staging import BenchSites, PNG_BYTES
from test_visit_annotation_intent import intent as make_intent


def intent():
    item = make_intent()
    return replace(item, binding=replace(item.binding, site="bound-site.local"))


@pytest.fixture
def site():
    site = BenchSites()
    yield site
    site.close()


def test_roundtrip_private_metadata_and_kind_isolation(site):
    item = intent()
    staged = staging.stage_visit_annotation(item)
    data = item.canonical_visit_annotation.encode("utf-8")
    assert re.fullmatch(r"[0-9a-f]{32}\.json", staged.key)
    assert staged.format == "VISIT_ANNOTATION_JSON"
    assert staged.size == len(data)
    assert staged.sha256 == hashlib.sha256(data).hexdigest()
    assert data.decode() not in repr(staged)
    assert staging.read_staged_visit_annotation(staged) == data
    assert os.stat(os.path.join(site.staging_dir, staged.key)).st_mode & 0o777 == 0o600
    with pytest.raises(FrozenInstanceError):
        staged.size = 1
    with pytest.raises(staging.StagedImageRefused):
        staging.StagedImage(staged.key, staged.sha256, staged.size, "PNG")
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_image(staged)
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(staging.stage_image(PNG_BYTES))


@pytest.mark.parametrize("value", ["../x.json", "a" * 32 + ".png", "a" * 32 + ".json/.."])
def test_bad_metadata_refused(value):
    with pytest.raises(staging.StagedImageRefused):
        staging.StagedVisitAnnotation(value, "a" * 64, 1, "VISIT_ANNOTATION_JSON")


def test_untrusted_intent_and_metadata_refused(site):
    with pytest.raises(staging.StagingRefused):
        staging.stage_visit_annotation("clinical secret")
    item = intent()
    object.__setattr__(item, "canonical_visit_annotation", '{"secret":"clinical secret"}')
    with pytest.raises(staging.StagingRefused) as error:
        staging.stage_visit_annotation(item)
    assert "clinical secret" not in str(error.value)
    assert not site.files()
    for size in (0, True, validation.MAX_ANNOTATION_BYTES + 1):
        with pytest.raises(staging.StagedImageRefused):
            staging.StagedVisitAnnotation("a" * 32 + ".json", "a" * 64, size, "VISIT_ANNOTATION_JSON")


@pytest.mark.parametrize("bad", [b"\xff", b'{"schema_version":2}', b'{"shapes":[],"schema_version":2,"coordinate_space":"normalized"}'])
def test_actual_verified_bytes_must_be_canonical_v2(site, bad):
    staged = staging.stage_visit_annotation(intent())
    path = os.path.join(site.staging_dir, staged.key)
    with open(path, "wb") as file:
        file.write(bad)
    forged = replace(staged, size=len(bad), sha256=hashlib.sha256(bad).hexdigest())
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(forged)


def test_tamper_and_permissions_refused(site):
    staged = staging.stage_visit_annotation(intent())
    path = os.path.join(site.staging_dir, staged.key)
    os.chmod(path, 0o644)
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(staged)
    os.chmod(path, 0o600)
    with open(path, "ab") as file:
        file.write(b"x")
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(staged)


def test_no_overwrite_or_replay_on_collision(site, monkeypatch):
    monkeypatch.setattr(staging.secrets, "token_hex", lambda _: "a" * 32)
    staged = staging.stage_visit_annotation(intent())
    with pytest.raises(staging.StagingRefused):
        staging.stage_visit_annotation(intent())
    assert site.files() == [staged.key]
    assert staging.read_staged_visit_annotation(staged)


def test_symlink_and_same_size_tamper_refused(site):
    staged = staging.stage_visit_annotation(intent())
    path = os.path.join(site.staging_dir, staged.key)
    with open(path, "rb") as file:
        data = file.read()
    with open(path, "wb") as file:
        file.write(b"x" + data[1:])
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(staged)
    os.unlink(path)
    os.symlink(os.path.join(site.site, "private"), path)
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_visit_annotation(staged)


def test_different_intent_site_refused_before_open_or_write(site, monkeypatch):
    item = make_intent()
    def unexpected_open(*args, **kwargs):
        pytest.fail("must not open staging directory")
    monkeypatch.setattr(staging.os, "open", unexpected_open)
    with pytest.raises(staging.StagingRefused):
        staging.stage_visit_annotation(item)
    assert not os.path.exists(site.staging_dir)


def test_forged_image_carrier_cannot_read_annotation(site):
    staged = staging.stage_visit_annotation(intent())
    forged = object.__new__(staging.StagedImage)
    for name in ("key", "sha256", "size", "format"):
        object.__setattr__(forged, name, getattr(staged, name))
    with pytest.raises(staging.StagedImageRefused):
        staging.read_staged_image(forged)
