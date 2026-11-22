import pytest

from clinic_photos.access import frappe_context
from clinic_photos.publication import visit_native_save, visit_transaction
from test_visit_file_scope import env, setup_scope, visit_file_scope
from test_visit_native_insert_gate import File, NativeFile
from test_visit_native_reserved_bytes import configure


@pytest.mark.parametrize("case", ["ok", "wrong", "missing", "duplicate", "short"])
def test_named_row_uses_current_locking_sql_not_snapshot(
        tmp_path, env, monkeypatch, visit_file_scope, case):
    _, owner, db = env
    owner, handle, _ = setup_scope(env, monkeypatch)
    doc, rows, calls = File(), [], []
    fields = ("name", *frappe_context.FILE_IDENTITY_FIELDS)

    def sql(query, params=None):
        text = query.lower()
        if "get_lock" in text or "release_lock" in text:
            return ((1,),)
        if "innodb_snapshot_isolation" in text:
            return ((0,),)
        if "select name, file_url" in text and "lock in share mode" in text:
            calls.append("scan")
            return [(row["name"], row["file_url"]) for row in rows]
        if "from `tabfile`" in text and "where name" in text and "lock in share mode" in text:
            calls.append("row")
            assert params == ("FILE-1",)
            assert all(f"`{field}`" in query for field in fields)
            found = [tuple(row[field] for field in fields) for row in rows if row["name"] == params[0]]
            if case == "missing":
                return []
            if case == "duplicate":
                return found * 2
            if case == "short":
                return [found[0][:-1]]
            return found
        raise AssertionError(query)

    monkeypatch.setattr(db, "sql", sql, raising=False)
    monkeypatch.setattr(frappe_context, "_all_file_rows",
                        lambda: pytest.fail("snapshot scan forbidden"))
    monkeypatch.setattr(frappe_context, "_file_row",
                        lambda doc: pytest.fail("snapshot row forbidden"))
    configure(doc, tmp_path, monkeypatch,
              lambda module, file_doc: module._create_reserved_bytes(file_doc))

    def native_insert(self):
        self.before_insert()
        self.name = "FILE-1"
        self.flags.in_insert = True
        visit_native_save._verify_pre_row(self)
        rows.append({
            "name": self.name, "file_url": self.file_url, "file_name": self.file_name,
            "owner": "other" if case == "wrong" else self.owner, "is_private": 1,
            "file_size": self.file_size, "content_hash": self.content_hash,
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        })
        self.flags.in_insert = False
        if case != "ok":
            with pytest.raises(visit_file_scope.FileScopeDenied):
                visit_native_save._verify_post_row(self)
        else:
            assert visit_native_save._verify_post_row(self).size == 4
        raise RuntimeError("stop")

    monkeypatch.setattr(NativeFile, "insert", native_insert)
    with visit_transaction._visit_transaction(owner) as guard:
        guard._phase = "publish"
        with visit_file_scope._file_scope(guard, handle, doc):
            with pytest.raises(RuntimeError, match="stop"):
                doc.insert()
            assert calls.count("scan") >= 3
            assert "row" in calls
            guard._rollback()
