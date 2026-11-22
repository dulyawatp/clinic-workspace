import hashlib
import os
import sys
from types import SimpleNamespace

import pytest

from clinic_photos.publication import visit_transaction, writer
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile


def configure(doc, tmp_path, monkeypatch, body):
    from clinic_photos.publication import visit_native_save

    db = sys.modules["frappe"].local.db
    if not hasattr(db, "sql"):
        def sql(query, params=None):
            query = query.lower()
            if "get_lock" in query or "release_lock" in query:
                return ((1,),)
            if "innodb_snapshot_isolation" in query:
                return ((0,),)
            if "lock in share mode" in query:
                from clinic_photos.access import frappe_context
                rows = getattr(db, "visit_rows", [])
                if callable(rows):
                    rows = rows()
                if "select name, file_url" in query:
                    return [(row["name"], row["file_url"]) for row in rows]
                if "where name" in query:
                    fields = ("name", *frappe_context.FILE_IDENTITY_FIELDS)
                    return [tuple(row[field] for field in fields)
                            for row in rows if row["name"] == params[0]]
            raise AssertionError(query)
        monkeypatch.setattr(db, "sql", sql, raising=False)
    doc.is_folder = doc.is_remote_file = False
    for name in ("set_folder_name", "set_is_private", "set_file_name",
                 "validate_attachment_limit", "set_file_type",
                 "validate_file_extension", "validate_private_file_access",
                 "validate_file_path"):
        monkeypatch.setattr(doc, name, lambda: None, raising=False)
    monkeypatch.setattr(doc, "check_max_file_size", lambda: 4, raising=False)
    monkeypatch.setattr(File, "check_permission", lambda self, kind: None, raising=False)
    monkeypatch.setitem(sys.modules, "frappe.utils",
                        SimpleNamespace(get_hook_method=lambda name: None))
    monkeypatch.setattr(writer, "_open_visit_files_directory",
                        lambda owner: os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY))
    monkeypatch.setattr(NativeFile, "insert", lambda self: self.before_insert())
    original = visit_native_save._create_reserved_bytes
    monkeypatch.setattr(visit_native_save, "_create_reserved_bytes",
                        lambda self: body(SimpleNamespace(_create_reserved_bytes=original), self))


def test_exact_reserved_bytes_exclusive_and_one_use(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    def body(module, file_doc):
        evidence = module._create_reserved_bytes(file_doc)
        assert evidence.size == 4
        assert evidence.sha256 == hashlib.sha256(b"data").hexdigest()
        assert evidence.inode == (tmp_path / doc.file_name).stat().st_ino
        with pytest.raises(visit_file_scope.FileScopeDenied):
            module._create_reserved_bytes(file_doc)
        raise RuntimeError("test rollback")
    configure(doc, tmp_path, monkeypatch, body)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="test rollback"):
                doc.insert()
            assert guard.publication_aborted()
            assert (tmp_path / doc.file_name).read_bytes() == b"data"


def test_existing_target_never_overwritten(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    target = tmp_path / doc.file_name
    target.write_bytes(b"original")
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(FileExistsError):
                doc.insert()
            assert guard.publication_aborted()
            assert target.read_bytes() == b"original"


def test_direct_call_cannot_create(tmp_path, env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._create_reserved_bytes(doc)
            assert guard.publication_aborted()
            assert not list(tmp_path.iterdir())


def test_replaced_root_after_write_poisons_and_retains_residue(
        tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    first, other = tmp_path / "first", tmp_path / "other"
    first.mkdir()
    other.mkdir()
    configure(doc, first, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    calls = iter((first, first, other))
    monkeypatch.setattr(writer, "_open_visit_files_directory",
                        lambda owner: os.open(next(calls), os.O_RDONLY | os.O_DIRECTORY))
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_file_scope.FileScopeDenied):
                doc.insert()
            assert guard.publication_aborted()
            assert (first / doc.file_name).read_bytes() == b"data"


def test_partial_write_failure_keeps_residue_without_retry(
        tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    def partial(fd, data):
        os.write(fd, data[:1])
        raise OSError("partial")
    monkeypatch.setattr(writer, "_write_all", partial)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(OSError, match="partial"):
                doc.insert()
            assert guard.publication_aborted()
            assert (tmp_path / doc.file_name).read_bytes() == b"d"


def test_native_create_permission_denial_prevents_byte_creation(
        tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    def deny(self, kind):
        raise PermissionError("create denied")
    monkeypatch.setattr(File, "check_permission", deny, raising=False)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(PermissionError, match="create denied"):
                doc.insert()
            assert guard.publication_aborted()
            assert not list(tmp_path.iterdir())


def test_validator_reentry_refuses_before_any_write(tmp_path, env, monkeypatch, visit_file_scope):
    from clinic_photos.publication import visit_native_save

    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    calls = []
    def reenter():
        calls.append(True)
        with pytest.raises(visit_file_scope.FileScopeDenied):
            visit_native_save._create_reserved_bytes(doc)
    monkeypatch.setattr(doc, "set_folder_name", reenter)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(visit_transaction.TransactionDenied):
                doc.insert()
            assert calls == [True]
            assert guard.publication_aborted()
            assert not list(tmp_path.iterdir())


def test_native_path_veto_precedes_creation(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    def deny():
        raise ValueError("native path veto")
    monkeypatch.setattr(doc, "validate_file_path", deny)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(ValueError, match="native path veto"):
                doc.insert()
            assert guard.publication_aborted()
            assert not list(tmp_path.iterdir())


def test_write_error_survives_close_error(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    configure(doc, tmp_path, monkeypatch, lambda module, doc: module._create_reserved_bytes(doc))
    original_close = os.close
    created_fd = []
    def fail_write(fd, data):
        created_fd.append(fd)
        raise ValueError("primary write error")
    def fail_close(fd):
        original_close(fd)
        if fd in created_fd:
            raise OSError("cleanup error")
    monkeypatch.setattr(writer, "_write_all", fail_write)
    monkeypatch.setattr(os, "close", fail_close)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(ValueError, match="primary write error"):
                doc.insert()
            assert guard.publication_aborted()


def test_exclusive_file_descriptor_is_read_write_once(tmp_path, env, monkeypatch, visit_file_scope):
    _, owner, _ = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    file_opens = []
    original_open = os.open
    def observe(path, flags, *args, **kwargs):
        if path == doc.file_name:
            file_opens.append(flags)
        return original_open(path, flags, *args, **kwargs)
    configure(doc, tmp_path, monkeypatch,
              lambda module, doc: (module._create_reserved_bytes(doc),
                                   (_ for _ in ()).throw(RuntimeError("stop"))))
    monkeypatch.setattr(os, "open", observe)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop"):
                doc.insert()
            assert len(file_opens) == 1
            assert file_opens[0] & os.O_ACCMODE == os.O_RDWR
