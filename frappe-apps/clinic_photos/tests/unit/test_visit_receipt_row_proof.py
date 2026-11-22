import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_native_save, visit_transaction, writer
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


@pytest.mark.parametrize("case", ["ok", "folder", "duplicate", "encoded_alias", "post_duplicate",
                                  "wrong_row", "missing_field", "query_error", "flag",
                                  "pre_flag", "post_without_pre"])
def test_receipt_pre_and_post_row_proof(tmp_path, env, monkeypatch, visit_file_scope, case):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc = File()
    rows = []
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))
    db.visit_rows = rows
    monkeypatch.setattr(frappe_context, "_all_file_rows",
                        lambda: pytest.fail("snapshot scan forbidden"))
    monkeypatch.setattr(frappe_context, "_file_row",
                        lambda doc: pytest.fail("snapshot row forbidden"))

    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        self.file_size = 4
        self.content_hash = writer._content_hash(b"data")
        if case == "folder":
            rows.extend([{"name": "FOLDER-1", "file_url": None},
                         {"name": "FOLDER-2", "file_url": ""}])
        if case in ("duplicate", "encoded_alias"):
            url = self.file_url.replace("clinic", "%63linic") if case == "encoded_alias" else self.file_url
            rows.append({"name": "OTHER", "file_url": url})
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_pre_row(self)
            raise RuntimeError("stop before commit")
        if case == "pre_flag":
            self.flags.in_insert = False
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_pre_row(self)
            raise RuntimeError("stop before commit")
        if case == "post_without_pre":
            self.flags.in_insert = False
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_post_row(self)
            raise RuntimeError("stop before commit")
        receipt = visit_native_save._verify_pre_row(self)
        rows.append({"name": self.name, "file_url": self.file_url,
                     "file_name": self.file_name, "owner": self.owner,
                     "is_private": 1, "content_hash": self.content_hash,
                     "file_size": self.file_size, "attached_to_doctype": None,
                     "attached_to_name": None, "attached_to_field": None})
        self.flags.in_insert = case == "flag"
        if case == "wrong_row":
            rows[0]["owner"] = "someone-else"
        elif case == "missing_field":
            del rows[0]["attached_to_field"]
        elif case == "post_duplicate":
            rows.append({"name": "OTHER", "file_url": self.file_url})
        elif case == "query_error":
            def fail(*args, **kwargs):
                raise RuntimeError("query unavailable")
            monkeypatch.setattr(db, "sql", fail)
        if case in ("ok", "folder"):
            assert visit_native_save._verify_post_row(self) is receipt
        else:
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_post_row(self)
        raise RuntimeError("stop before commit")

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop before"):
                doc.insert()
            assert guard.publication_aborted()
