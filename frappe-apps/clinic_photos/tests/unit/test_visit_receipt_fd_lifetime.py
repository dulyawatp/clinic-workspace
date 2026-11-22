import os
from contextlib import nullcontext

import pytest

from clinic_photos.access import file_mixin
from clinic_photos.publication import visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


@pytest.mark.parametrize("outcome", ["success", "native_error", "replace",
                                     "close_success", "close_primary", "forged"])
def test_created_and_directory_fds_stay_pinned_until_insert_exits(
        tmp_path, env, monkeypatch, visit_file_scope, outcome):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, rows, fds, path_opens = File(), [], [], []
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    db.visit_rows = rows
    monkeypatch.setattr(NativeFile, "validate", lambda self: None, raising=False)

    def insert(self):
        self.before_insert()
        receipt = file_mixin._VISIT_INSERT.get()[3][3]
        fds.extend([receipt.directory_fd, receipt.file_fd])
        assert os.fstat(receipt.file_fd).st_ino == (tmp_path / self.file_name).stat().st_ino
        os.fstat(receipt.directory_fd)
        original_open = os.open
        def track_open(path, flags, *args, **kwargs):
            if path == self.file_name:
                path_opens.append(path)
            return original_open(path, flags, *args, **kwargs)
        monkeypatch.setattr(os, "open", track_open)
        if outcome in ("close_success", "close_primary"):
            original_close = os.close
            def fail_close(fd):
                original_close(fd)
                if fd == receipt.file_fd:
                    raise OSError("close failed")
            monkeypatch.setattr(os, "close", fail_close)
        if outcome in ("native_error", "close_primary"):
            raise RuntimeError("native hook failed")
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.validate()
        rows.append({"name": self.name, "file_url": self.file_url, "file_name": self.file_name,
                     "owner": self.owner, "is_private": 1, "file_size": self.file_size,
                     "content_hash": self.content_hash, "attached_to_doctype": None,
                     "attached_to_name": None, "attached_to_field": None})
        self.flags.in_insert = False
        if outcome == "replace":
            target = tmp_path / self.file_name
            target.unlink()
            target.write_bytes(b"data")
            target.chmod(0o600)
        return self

    monkeypatch.setattr(NativeFile, "insert", insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        exit_check = pytest.raises(visit_file_scope.FileScopeDenied) if outcome == "close_success" else nullcontext()
        with exit_check, visit_file_scope._file_scope(guard, handle, doc):
            if outcome == "native_error":
                with pytest.raises(visit_file_scope.FileScopeDenied):
                    visit_file_scope._CompletedFile()
            if outcome in ("success", "close_success", "forged"):
                assert doc.insert() is doc
                if outcome == "success":
                    proof = visit_file_scope._completed_file(guard)
                    assert proof.file is doc
                    assert proof.file_doc is doc
                    assert proof.name == "FILE-1"
                    assert proof.size == 4
                    assert proof.evidence.size == 4
                    assert proof.evidence.sha256 == proof.sha256
                    other = visit_transaction._VisitTransaction(owner)
                    other._phase = "publish"
                    with pytest.raises(visit_file_scope.FileScopeDenied):
                        visit_file_scope._completed_file(other)
                    assert other.publication_aborted()
                if outcome == "forged":
                    entry = visit_file_scope._ACTIVE.get()
                    proof, attempt = entry[4][2]
                    forged = object.__new__(type(proof))
                    for field in ("operation", "intent", "reservation", "file", "name",
                                  "device", "inode", "size", "sha256"):
                        object.__setattr__(forged, field, getattr(proof, field))
                    entry[4][2] = (forged, attempt)
                    with pytest.raises(visit_file_scope.FileScopeDenied):
                        visit_file_scope._completed_file(guard)
                    assert guard.publication_aborted()
                    entry[4][2] = (proof, attempt)
                for fd in fds:
                    os.fstat(fd)
                if outcome == "success":
                    with pytest.raises(visit_file_scope.FileScopeDenied):
                        visit_file_scope._completed_file(object())
                guard._rollback()
            elif outcome in ("native_error", "close_primary"):
                with pytest.raises(RuntimeError, match="native hook failed"):
                    doc.insert()
                with pytest.raises(visit_file_scope.FileScopeDenied):
                    visit_file_scope._completed_file(guard)
            else:
                with pytest.raises(visit_file_scope.FileScopeDenied):
                    doc.insert()
            if outcome == "close_primary":
                assert guard.publication_aborted()
                assert guard._native_settled
                assert guard._native_callback is not None
            assert len(fds) == 2
            assert path_opens == []
            if outcome not in ("success", "close_success", "forged"):
                for fd in fds:
                    with pytest.raises(OSError):
                        os.fstat(fd)
                guard._rollback()
                assert db.rollbacks == 1
        for fd in fds:
            with pytest.raises(OSError):
                os.fstat(fd)
        if outcome == "success":
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_file_scope._completed_file(guard)
