import os

import pytest

from clinic_photos.publication import exclusion, writer
from test_visit_annotation_prepare import prepared_env
from test_visit_annotation_staging import site


@pytest.fixture
def visit_owner(prepared_env):
    with exclusion.hold_visit_annotation_exclusion(prepared_env.operation) as owner:
        yield prepared_env.site, owner


def test_mechanical_failure_callback_cannot_return_success(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "_confined_root", lambda: str(tmp_path))
    (tmp_path / "existing").write_bytes(b"original")
    with pytest.raises(writer.FirstObjectDenied):
        writer._create_confined_bytes("/private/files/existing", b"new", lambda *_: None)
    assert (tmp_path / "existing").read_bytes() == b"original"


def test_visit_directory_descriptor_and_fd_creation(visit_owner):
    site, owner = visit_owner
    files = os.path.join(site.site, "private", "files")
    os.mkdir(files)
    descriptor = writer._open_visit_files_directory(owner)
    try:
        info = writer._create_bytes_at_directory(
            descriptor, "item.json", b"test", lambda *_: pytest.fail("unexpected refusal")
        )
        assert os.stat(os.path.join(files, "item.json")).st_ino == info.st_ino
        with open(os.path.join(files, "item.json"), "rb") as file:
            assert file.read() == b"test"
        with pytest.raises(writer.FirstObjectDenied):
            writer._create_bytes_at_directory(descriptor, "item.json", b"changed", lambda *_: None)
        with open(os.path.join(files, "item.json"), "rb") as file:
            assert file.read() == b"test"
        os.fstat(descriptor)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize("change", ["site", "missing", "private_symlink", "files_symlink"])
def test_visit_directory_refuses_wrong_site_missing_and_symlinks(visit_owner, tmp_path, change):
    site, owner = visit_owner
    private = os.path.join(site.site, "private")
    files = os.path.join(private, "files")
    os.mkdir(files)
    if change == "private_symlink":
        os.rename(private, private + "-original")
        os.symlink(tmp_path, private)
    elif change == "files_symlink":
        os.rmdir(files)
        os.symlink(tmp_path, files)
    elif change == "missing":
        os.rmdir(files)
    elif change == "site":
        site.frappe.local.site = "other-site"
    with pytest.raises((writer.FirstObjectDenied, exclusion.ExclusionError)):
        writer._open_visit_files_directory(owner)
    assert list(tmp_path.iterdir()) == []


def test_visit_directory_refuses_changed_entry_identity(visit_owner, monkeypatch):
    site, owner = visit_owner
    os.mkdir(os.path.join(site.site, "private", "files"))
    original = writer.os.stat

    def replaced(path, *args, **kwargs):
        if path == "files" and kwargs.get("dir_fd") is not None:
            return original(site.site)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(writer.os, "stat", replaced)
    with pytest.raises(writer.FirstObjectDenied):
        writer._open_visit_files_directory(owner)


def test_visit_directory_rejects_same_named_site_in_other_volume(visit_owner, tmp_path, monkeypatch):
    site, owner = visit_owner
    decoy = tmp_path / "other-sites" / site.bound_site / "private" / "files"
    decoy.mkdir(parents=True)
    monkeypatch.setattr(site.frappe, "get_site_path", lambda *parts: str(decoy))
    monkeypatch.setattr(writer.frappe_context, "current_site_context",
                        lambda: (site.bound_site, site.sites))
    with pytest.raises(writer.FirstObjectDenied):
        writer._open_visit_files_directory(owner)


@pytest.mark.parametrize("name", ["../escape", "nested/file", "/absolute", "a\\b", ".", ""])
def test_fd_mechanics_reject_non_flat_name_before_open(tmp_path, monkeypatch, name):
    directory = os.open(tmp_path, os.O_RDONLY)
    def unexpected_open(*args, **kwargs):
        pytest.fail("invalid name reached open")
    monkeypatch.setattr(writer.os, "open", unexpected_open)
    try:
        with pytest.raises(writer.FirstObjectDenied):
            writer._create_bytes_at_directory(directory, name, b"data", lambda *_: None)
    finally:
        os.close(directory)
