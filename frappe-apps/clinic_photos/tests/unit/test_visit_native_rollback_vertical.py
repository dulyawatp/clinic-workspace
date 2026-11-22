"""Native-order rollback-only fixture; intentionally no commit or live site."""

import hashlib
import os

import pytest

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_transaction, writer
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


class _GenericProtectedReadDenied(PermissionError):
    pass


def test_native_insert_stages_exact_file_then_rollback_retains_bytes(
        tmp_path, env, monkeypatch, visit_file_scope):
    frappe, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    events, rows = [], {}
    path = tmp_path / doc.file_name

    # Fixture supplies native authorization and transaction mechanics only.
    # Real mixin, visit guard and writer remain in use. A denied protected
    # read represents the required generic policy without a Frappe database.
    monkeypatch.setattr(File, "check_permission",
                        lambda self, action: events.append(("permission", action)),
                        raising=False)
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    monkeypatch.setattr(File, "check_permission",
                        lambda self, action: events.append(("permission", action)),
                        raising=False)
    for name in ("set_folder_name", "set_is_private", "set_file_name",
                 "validate_attachment_limit", "set_file_type",
                 "validate_file_extension", "validate_private_file_access"):
        monkeypatch.setattr(doc, name, lambda name=name: events.append(name))
    db.visit_rows = lambda: list(rows.values())
    def deny_read(file_doc):
        raise _GenericProtectedReadDenied("generic protected read denied")

    monkeypatch.setattr(file_mixin, "require_protected_read", deny_read)
    native_rollback = db.rollback

    def rollback():
        rows.clear()
        native_rollback()

    monkeypatch.setattr(db, "rollback", rollback)

    # Pinned File.before_insert prefix and local branch. The production visit
    # specialization must replace this call, never bypass generic get_content.
    def native_before_insert(self):
        for name in ("set_folder_name", "set_is_private", "set_file_name",
                     "validate_attachment_limit", "set_file_type",
                     "validate_file_extension", "validate_private_file_access"):
            events.append(name)
        self.save_file(content=self.get_content())
        self.flags.new_file = True
        frappe.db.after_rollback.add(self.on_rollback)
        events.append("duplicate_check")

    def native_insert(self):
        self.check_permission("create")
        events.append("validate_links")
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        events.extend(["later_validate", "before_save", "_validate"])
        self.flags.in_insert = False
        self.db_insert()
        events.append("after_insert")
        return self

    def native_db_insert(self):
        rows["FILE-1"] = {
            "name": self.name, "file_url": self.file_url, "file_name": self.file_name,
            "owner": self.owner, "is_private": self.is_private,
            "file_size": self.file_size, "content_hash": self.content_hash,
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        }

    monkeypatch.setattr(NativeFile, "before_insert", native_before_insert, raising=False)
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)
    monkeypatch.setattr(NativeFile, "db_insert", native_db_insert, raising=False)
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            assert doc.insert() is doc
            assert rows["FILE-1"]["file_url"] == doc.file_url
            assert rows["FILE-1"]["owner"] == "doctor"
            assert path.read_bytes() == b"data"
            assert hashlib.sha256(path.read_bytes()).hexdigest() == (
                "3a6eb0790f39ac87c94f3856b2dd2c5d110e6811602261a9a923d3bb23adc8b7")
            assert db.after_rollback._functions[-1] is guard._native_callback
            assert events[:3] == [("permission", "create"), "validate_links",
                                  ("permission", "create")]
            guard._rollback()
            assert rows == {}
            assert db.rollbacks == 1
            assert path.read_bytes() == b"data"


@pytest.mark.parametrize("change", ["name", "url", "size", "hash", "alias", "file"])
def test_late_hook_retarget_vetoes_native_db_insert(
        tmp_path, env, monkeypatch, visit_file_scope, change):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, rows, events = File(), [], []
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    db.visit_rows = rows
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)
    monkeypatch.setattr(NativeFile, "db_insert",
                        lambda self: events.append("db_insert"), raising=False)

    def native_insert(self):
        self.check_permission("create")
        events.append("validate_links")
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        events.extend(["later_validate", "before_save", "_validate"])
        if change == "name":
            self.name = "FILE-2"
        elif change == "url":
            self.file_url = "/private/files/other"
        elif change == "size":
            self.file_size = 99
        elif change == "hash":
            self.content_hash = "foreign"
        elif change == "alias":
            rows.append({"name": "OTHER", "file_url": self.file_url})
        self.flags.in_insert = False
        (File() if change == "file" else self).db_insert()
        events.append("after_insert")
        return self

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert "db_insert" not in events and "after_insert" not in events
            assert guard.publication_aborted()
            guard._rollback()
            assert db.rollbacks == 1
            assert (tmp_path / doc.file_name).read_bytes() == b"data"


def test_unscoped_db_insert_passes_native_arguments(monkeypatch):
    doc, seen = File(), []
    monkeypatch.setattr(NativeFile, "db_insert",
                        lambda self, *args, **kwargs: seen.append((args, kwargs)), raising=False)
    doc.db_insert("native", ignore_if_duplicate=True)
    assert seen == [(("native",), {"ignore_if_duplicate": True})]


def test_ambient_scope_cannot_call_native_db_insert(env, monkeypatch, visit_file_scope):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, seen = File(), []
    monkeypatch.setattr(NativeFile, "db_insert", lambda self: seen.append(True), raising=False)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.db_insert()
            assert guard.publication_aborted()
            assert seen == []


@pytest.mark.parametrize("failure", ["permission", "before_callback", "after_callback"])
def test_native_failures_rollback_without_removing_residue(
        tmp_path, env, monkeypatch, visit_file_scope, failure):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    if failure == "permission":
        def deny(self, action):
            raise PermissionError("create denied")
        monkeypatch.setattr(File, "check_permission", deny, raising=False)
    elif failure == "before_callback":
        def partial(fd, data):
            os.write(fd, data[:1])
            raise OSError("partial write")
        monkeypatch.setattr(writer, "_write_all", partial)
    def native_insert(self):
        self.before_insert()
        if failure == "after_callback":
            self.file_url = "/private/files/retargeted"
            raise RuntimeError("after callback")
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            error = {"permission": PermissionError, "before_callback": OSError,
                     "after_callback": RuntimeError}[failure]
            with pytest.raises(error):
                doc.insert()
            assert guard.publication_aborted()
            assert guard._native_settled
            assert bool(guard._native_callback) == (failure == "after_callback")
            guard._rollback()
            assert db.rollbacks == 1
            target = tmp_path / doc.file_name
            if failure == "permission":
                assert not target.exists()
            else:
                assert target.read_bytes() == (b"d" if failure == "before_callback" else b"data")


@pytest.mark.parametrize("mutation", ["same_inode", "fifo", "read_close"])
def test_post_hook_byte_substitution_must_refuse(tmp_path, env, monkeypatch, visit_file_scope, mutation):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, rows = File(), []
    closed = []
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    db.visit_rows = rows
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)
    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        rows.append({
            "name": self.name, "file_url": self.file_url, "file_name": self.file_name,
            "owner": self.owner, "is_private": self.is_private, "file_size": self.file_size,
            "content_hash": self.content_hash, "attached_to_doctype": None,
            "attached_to_name": None, "attached_to_field": None,
        })
        self.flags.in_insert = False
        target = tmp_path / self.file_name
        if mutation == "same_inode":
            target.write_bytes(b"evil")
        elif mutation == "fifo":
            target.unlink()
            os.mkfifo(target)
        else:
            def fail_read(fd, size):
                raise ValueError("primary read failure")
            original_close = os.close
            def fail_close(fd):
                closed.append(fd)
                original_close(fd)
                raise OSError("close failure")
            monkeypatch.setattr(writer, "_read_all", fail_read)
            monkeypatch.setattr(os, "close", fail_close)
        return self
    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied) as error:
                doc.insert()
            assert guard.publication_aborted()
            if mutation == "read_close":
                assert isinstance(error.value.__cause__, ValueError)
                assert str(error.value.__cause__) == "primary read failure"
                assert len(closed) == 2
