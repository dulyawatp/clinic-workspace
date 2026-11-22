import sqlite3
import types
import unittest

from fake_frappe import fake_frappe
from clinic_photos import hooks
from clinic_photos.access import permissions


PREFIX = "clinic-visit-annotation-draft-"


class VisitMetadataPermissionsTests(unittest.TestCase):
    def test_only_builtin_administrator_defers_managed_metadata_lists(self):
        with fake_frappe({}, user="Administrator") as frappe:
            for hook in (permissions.draft_permission_query_conditions,
                         permissions.version_permission_query_conditions):
                self.assertEqual(hook("Administrator"), "")
                self.assertEqual(hook(), "")
                self.assertNotEqual(hook("administrator"), "")
                self.assertNotEqual(hook("manager@example.test"), "")
            frappe.session.user = "manager@example.test"
            self.assertNotEqual(permissions.draft_permission_query_conditions(), "")
            draft = types.SimpleNamespace(name=PREFIX + "x")
            version = types.SimpleNamespace(name=PREFIX + "v", draft=PREFIX + "x")
            self.assertFalse(permissions.draft_has_permission(draft, user="Administrator", ptype="read"))
            self.assertFalse(permissions.version_has_permission(version, user="Administrator", ptype="read"))

    def test_native_hooks_registered(self):
        for doctype, stem in (("Clinic Photo Draft", "draft"),
                              ("Clinic Photo Annotation Version", "version")):
            self.assertEqual(hooks.permission_query_conditions.get(doctype),
                             f"clinic_photos.access.permissions.{stem}_permission_query_conditions")
            self.assertEqual(hooks.has_permission.get(doctype),
                             f"clinic_photos.access.permissions.{stem}_has_permission")

    def test_query_conditions_hide_reserved_names_and_links_preserve_null(self):
        for table, columns, rows, expected, condition in (
            ("Clinic Photo Draft", "name TEXT", [(PREFIX + "x",), (PREFIX.upper() + "x",), ("legacy",)],
             [("legacy",)], lambda: permissions.draft_permission_query_conditions("author@example.test")),
            ("Clinic Photo Annotation Version", "name TEXT, draft TEXT",
             [(PREFIX + "v", "legacy"), ("v2", PREFIX.upper() + "x"),
              ("v3", None), ("v4", "legacy")], [("v3",), ("v4",)],
              lambda: permissions.version_permission_query_conditions("author@example.test")),
        ):
            with self.subTest(table=table), sqlite3.connect(":memory:") as db:
                db.create_function("CONCAT", -1, lambda *values: "".join(values))
                db.execute(f'CREATE TABLE "tab{table}" ({columns})')
                db.executemany(f'INSERT INTO "tab{table}" VALUES ({",".join("?" for _ in rows[0])})', rows)
                sql = condition()
                query = (f'SELECT name FROM "tab{table}" WHERE {sql} AND name != %(name)s') % {"name": "'missing'"}
                self.assertEqual(db.execute(query).fetchall(), expected)

    def test_document_denials_include_persisted_version_link(self):
        with fake_frappe({}) as frappe:
            frappe.db.add("Clinic Photo Annotation Version", "persisted", draft=PREFIX + "x")
            for ptype in ("read", "select", "export", "report", "print", "email", "share"):
                with self.subTest(ptype=ptype):
                    self.assertFalse(permissions.draft_has_permission(types.SimpleNamespace(name=PREFIX.upper() + "x"), ptype=ptype))
                    for doc in (types.SimpleNamespace(name=PREFIX + "v", draft=None),
                                types.SimpleNamespace(name="other", draft=PREFIX + "x"),
                                types.SimpleNamespace(name="persisted", draft="legacy")):
                        self.assertFalse(permissions.version_has_permission(doc, ptype=ptype))
                    self.assertTrue(permissions.draft_has_permission(types.SimpleNamespace(name="legacy"), ptype=ptype))
                    self.assertTrue(permissions.version_has_permission(types.SimpleNamespace(name="legacy", draft=None), ptype=ptype))
