"""Unit tests for the server-owned alias + operation receipt groundwork.

No Frappe import, no site and no database: every SQL statement is captured by a
fake adapter that fails the test if a helper commits, rolls back or opens its own
transaction, and that returns an empty tuple for DML exactly as Frappe's
``Database.sql`` does. These tests prove the statement shapes, the
``ROW_COUNT()`` follow-up, validation, schema verification and transaction
ownership only. They do **not** prove MariaDB constraint enforcement, DDL
execution, ``information_schema`` text or concurrency; that is reported as
unverified until it runs on an approved disposable site.
"""

import ast
import datetime
import sys
import unittest
from pathlib import Path
from unittest import mock

from clinic_ui.core import patient_write_ledger as ledger

APP_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime.datetime(2026, 11, 14, 12, 0, 0)
DIGEST = "a" * 64
KEY = "operation-key-000000000001"
ALIAS = "WS-0000000000000000000000000001"


class FakeDb:
    """Records every statement; refuses any transaction control."""

    def __init__(self, rows=()):
        self.calls = []
        self.ddl = []
        self.rows = list(rows)

    def sql(self, query, values=None):
        # Frappe's Database.sql returns fetched rows for a SELECT and an empty
        # tuple for DML: an UPDATE result is never a row count.
        self.calls.append((query, values))
        if query.lstrip().upper().startswith("SELECT"):
            return self.rows.pop(0) if self.rows else []
        return ()

    def sql_ddl(self, query):
        self.ddl.append(query)

    def commit(self):
        raise AssertionError("helper must not commit")

    def rollback(self, *args, **kwargs):
        raise AssertionError("helper must not roll back")


def declared_schema_rows():
    """Result sets the module expects, in verify_schema query order."""

    version = [("10.6.16-MariaDB-0ubuntu0.22.04.1",)]
    tables = [
        (table, "BASE TABLE", "InnoDB", ledger.EXPECTED_TABLE_COLLATION)
        for table in ledger.SCHEMA_TABLES
    ]
    columns = [
        (table, name, data_type, length, precision, nullable,
         ledger.EXPECTED_COLLATIONS[table][name],
         None if nullable == "NO" else "NULL", "", "NEVER")
        for table, specs in ledger.EXPECTED_COLUMNS.items()
        for name, data_type, length, precision, nullable in specs
    ]
    indexes = [
        (table, name, 0 if unique else 1, position, column, None)
        for table, specs in ledger.EXPECTED_INDEXES.items()
        for name, (unique, ordered) in specs.items()
        for position, column in enumerate(ordered, start=1)
    ]
    checks = [
        (table, name, clause)
        for table, specs in ledger.EXPECTED_CHECKS.items()
        for name, clause in specs.items()
    ]
    return [version, tables, columns, indexes, checks, []]


def schema_rows_with(**overrides):
    """Declared result sets with specific sections replaced by ``overrides``."""

    rows = declared_schema_rows()
    names = ("version", "tables", "columns", "indexes", "checks", "triggers")
    for name, value in overrides.items():
        rows[names.index(name)] = value
    return rows


def claim(db, **overrides):
    values = {
        "site": "sit.local",
        "actor": "doc@example.test",
        "kind": "create",
        "operation_key": KEY,
        "payload_digest": DIGEST,
        "now": NOW,
    }
    values.update(overrides)
    return ledger.claim_operation(db, **values)


class SchemaTests(unittest.TestCase):
    def test_exactly_the_two_raw_tables_are_declared_without_tab_prefix(self):
        names = ledger.SCHEMA_TABLES
        self.assertEqual(names, ("clinic_ui_patient_alias", "clinic_ui_operation"))
        for statement, name in zip(ledger.SCHEMA_STATEMENTS, names):
            self.assertIn("CREATE TABLE IF NOT EXISTS %s" % name, statement)
            self.assertNotIn("tab%s" % name, statement)

    def test_ddl_is_engine_inno_db_and_binary_collated(self):
        for statement in ledger.SCHEMA_STATEMENTS:
            self.assertIn("ENGINE=InnoDB", statement)
            self.assertIn("COLLATE=utf8mb4_bin", statement)

    def test_ddl_declares_the_expected_keys_and_checks(self):
        alias, operation = ledger.SCHEMA_STATEMENTS
        self.assertIn("PRIMARY KEY (workspace_patient_id)", alias)
        self.assertIn("UNIQUE KEY uq_alias_site_native (site, native_patient_id)", alias)
        self.assertIn("UNIQUE KEY uq_alias_site_operation (site, operation_key)", alias)
        self.assertIn("PRIMARY KEY (site, actor, kind, operation_key)", operation)
        for check in (
            "ck_operation_kind",
            "ck_operation_state",
            "ck_operation_digest_length",
            "ck_operation_key_length",
            "ck_operation_committed_receipt",
            "ck_operation_update_base",
            "ck_operation_create_base",
        ):
            self.assertIn(check, operation)
        for check in ("ck_alias_workspace_id_length", "ck_alias_native_id_length"):
            self.assertIn(check, alias)

    def test_apply_schema_issues_ddl_only(self):
        db = FakeDb()
        ledger.apply_schema(db)
        self.assertEqual(db.ddl, list(ledger.SCHEMA_STATEMENTS))
        self.assertEqual(db.calls, [])

    def test_verify_schema_accepts_the_declared_shape(self):
        db = FakeDb(rows=declared_schema_rows())
        self.assertIsNone(ledger.verify_schema(db))

    def test_verify_schema_accepts_the_mysql_compatibility_version_prefix(self):
        db = FakeDb(rows=schema_rows_with(version=[("5.5.5-10.6.16-MariaDB",)]))
        self.assertIsNone(ledger.verify_schema(db))

    def test_verify_schema_normalizes_check_case_whitespace_and_backticks(self):
        def messy(clause):
            return (
                clause.replace("CHAR_LENGTH", "`char_length`")
                .replace(" IN ", " in ")
                .replace(" OR ", " or ")
                .replace(" AND ", " and ")
                .replace(" IS NOT NULL", " is not null")
                .replace(" IS NULL", " is null")
                .replace("<>", "!=")
                .replace("(", "(  ")
                .replace(")", "  )")
                .replace(",", " , ")
            )

        checks = [
            (table, name, messy(clause))
            for table, name, clause in declared_schema_rows()[4]
        ]
        db = FakeDb(rows=schema_rows_with(checks=checks))
        self.assertIsNone(ledger.verify_schema(db))

    def test_verify_schema_checks_the_server_identity_and_version(self):
        for version in (
            [("8.0.36",)],
            [("10.5.24-MariaDB",)],
            [("10.6.16",)],
            [],
        ):
            with self.subTest(version=version):
                db = FakeDb(rows=schema_rows_with(version=version))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_requires_inno_db_base_tables(self):
        cases = {
            "myisam": [("clinic_ui_patient_alias", "BASE TABLE", "MyISAM", "utf8mb4_bin")],
            "view": [("clinic_ui_operation", "VIEW", None, None)],
            "wrong_collation": [
                (
                    "clinic_ui_patient_alias",
                    "BASE TABLE",
                    "InnoDB",
                    "utf8mb4_unicode_ci",
                )
            ],
        }
        for label, tables in cases.items():
            with self.subTest(case=label):
                db = FakeDb(rows=schema_rows_with(tables=tables))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_fails_closed_on_a_missing_column(self):
        columns = [
            row
            for row in declared_schema_rows()[2]
            if (row[0], row[1]) != ("clinic_ui_operation", "payload_digest")
        ]
        db = FakeDb(rows=schema_rows_with(columns=columns))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)

    def test_verify_schema_rejects_collation_default_and_generated_drift(self):
        for index, replacement in (
            (6, "utf8mb4_unicode_ci"),
            (7, "'x'"),
            (8, "VIRTUAL GENERATED"),
            (9, "ALWAYS"),
        ):
            with self.subTest(column=index, replacement=replacement):
                columns = [list(row) for row in declared_schema_rows()[2]]
                columns[0][index] = replacement
                db = FakeDb(rows=schema_rows_with(columns=[tuple(r) for r in columns]))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_accepts_null_default_metadata_for_nullable_columns(self):
        for representation in (None, "NULL", "null"):
            with self.subTest(default=representation):
                columns = [list(row) for row in declared_schema_rows()[2]]
                for row in columns:
                    if row[5] == "YES":
                        row[7] = representation
                db = FakeDb(rows=schema_rows_with(columns=[tuple(r) for r in columns]))
                self.assertIsNone(ledger.verify_schema(db))

    def test_verify_schema_rejects_quoted_and_expression_defaults(self):
        for replacement in ("'NULL'", '"NULL"', "'x'", "0", "CURRENT_TIMESTAMP"):
            with self.subTest(default=replacement):
                columns = [list(row) for row in declared_schema_rows()[2]]
                nullable = next(row for row in columns if row[5] == "YES")
                nullable[7] = replacement
                db = FakeDb(rows=schema_rows_with(columns=[tuple(r) for r in columns]))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_rejects_a_null_default_on_a_not_null_column(self):
        columns = [list(row) for row in declared_schema_rows()[2]]
        columns[0][7] = "NULL"
        db = FakeDb(rows=schema_rows_with(columns=[tuple(r) for r in columns]))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)

    def test_verify_schema_rejects_literal_case_and_space_drift(self):
        for replacement in ("'CREATE'", "'create '", "'Create'"):
            with self.subTest(replacement=replacement):
                checks = [list(row) for row in declared_schema_rows()[4]]
                target = next(row for row in checks if row[1] == "ck_operation_kind")
                target[2] = target[2].replace("'create'", replacement)
                db = FakeDb(rows=schema_rows_with(checks=[tuple(r) for r in checks]))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_rejects_a_weakened_unique_key(self):
        indexes = [list(row) for row in declared_schema_rows()[3]]
        for row in indexes:
            if row[1] == "uq_alias_site_native":
                row[2] = 1
        db = FakeDb(rows=schema_rows_with(indexes=[tuple(r) for r in indexes]))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)

    def test_verify_schema_rejects_a_prefix_index(self):
        indexes = [list(row) for row in declared_schema_rows()[3]]
        indexes[0][5] = 8
        db = FakeDb(rows=schema_rows_with(indexes=[tuple(r) for r in indexes]))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)

    def test_verify_schema_rejects_an_altered_check_with_the_same_name(self):
        checks = [list(row) for row in declared_schema_rows()[4]]
        checks[0][2] = "char_length(workspace_patient_id) >= 1"
        db = FakeDb(rows=schema_rows_with(checks=[tuple(r) for r in checks]))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)

    def test_verify_schema_rejects_a_missing_or_extra_check(self):
        checks = declared_schema_rows()[4]
        for replacement in (checks[:-1], [*checks, (checks[0][0], "ck_extra", "1 = 1")]):
            with self.subTest(count=len(replacement)):
                db = FakeDb(rows=schema_rows_with(checks=replacement))
                with self.assertRaises(ledger.SchemaMismatch):
                    ledger.verify_schema(db)

    def test_verify_schema_rejects_any_trigger_on_the_ledger_tables(self):
        trigger = ("clinic_ui_operation", "trg_unexpected")
        db = FakeDb(rows=schema_rows_with(triggers=[trigger]))
        with self.assertRaises(ledger.SchemaMismatch):
            ledger.verify_schema(db)


class ValidationTests(unittest.TestCase):
    def test_kind_must_be_create_or_update(self):
        for kind in ("delete", "CREATE", "", None):
            with self.subTest(kind=kind):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), kind=kind)

    def test_operation_key_length_and_controls_are_bounded(self):
        for key in ("short", "k" * 129, "key-with-a-control-\x00-char", "", None):
            with self.subTest(key=key):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), operation_key=key)

    def test_payload_digest_must_be_64_lowercase_hex(self):
        for digest in ("a" * 63, "A" * 64, "g" * 64, "a" * 65, None):
            with self.subTest(digest=digest):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), payload_digest=digest)

    def test_site_and_actor_are_validated(self):
        for field in ("site", "actor"):
            with self.subTest(field=field):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), **{field: "  "})
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), **{field: "x" * 141})

    def test_identity_values_reject_surrounding_whitespace(self):
        for field in ("site", "actor", "operation_key"):
            value = " padded-value-0000000 "
            with self.subTest(field=field):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), **{field: value})
        for field, value in (
            ("workspace_patient_id", " WS-0000000000000001 "),
            ("operation_key", " operation-key-00000001 "),
            ("native_patient_id", " PAT-0001 "),
            ("created_by", " doc@example.test "),
            ("site", " sit.local "),
        ):
            with self.subTest(field=field):
                arguments = {
                    "workspace_patient_id": ALIAS,
                    "site": "sit.local",
                    "native_patient_id": "PAT-0001",
                    "operation_key": KEY,
                    "created_by": "doc@example.test",
                    "creation": NOW,
                }
                arguments[field] = value
                with self.assertRaises(ledger.InvalidLedgerInput):
                    ledger.insert_alias(FakeDb(), **arguments)

    def test_create_must_not_carry_base_modified(self):
        with self.assertRaises(ledger.InvalidLedgerInput):
            claim(FakeDb(), kind="create", base_modified=NOW)

    def test_update_requires_a_datetime_base_modified(self):
        for base in (None, "2026-11-14 12:00:00", 0):
            with self.subTest(base=base):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), kind="update", base_modified=base)

    def test_now_must_be_a_datetime(self):
        for now in (None, "2026-11-14", 0):
            with self.subTest(now=now):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    claim(FakeDb(), now=now)

    def test_final_states_are_bounded_and_exclude_pending(self):
        self.assertEqual(
            ledger.FINAL_STATES, frozenset({"committed", "conflict", "unknown"})
        )
        db = FakeDb()
        with self.assertRaises(ledger.InvalidLedgerInput):
            ledger.abandon_operation(
                db,
                site="sit.local",
                actor="doc@example.test",
                kind="create",
                operation_key=KEY,
                payload_digest=DIGEST,
                state="pending",
                now=NOW,
            )

    def test_alias_identifiers_are_bounded(self):
        for field, value in (
            ("workspace_patient_id", "w" * 65),
            ("workspace_patient_id", "w" * 7),
            ("workspace_patient_id", ""),
            ("native_patient_id", "n" * 141),
            ("created_by", ""),
        ):
            with self.subTest(field=field):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    ledger.insert_alias(
                        FakeDb(),
                        workspace_patient_id=(
                            value if field == "workspace_patient_id" else ALIAS
                        ),
                        site="sit.local",
                        native_patient_id=(
                            value if field == "native_patient_id" else "PAT-0001"
                        ),
                        operation_key=KEY,
                        created_by=(
                            value if field == "created_by" else "doc@example.test"
                        ),
                        creation=NOW,
                    )


class ClaimTests(unittest.TestCase):
    def test_claim_is_one_parameterized_pending_insert(self):
        db = FakeDb()
        self.assertIsNone(claim(db))
        self.assertEqual(len(db.calls), 1)
        query, values = db.calls[0]
        self.assertTrue(query.lstrip().upper().startswith("INSERT INTO CLINIC_UI_OPERATION"))
        for value in ("sit.local", "doc@example.test", "create", KEY, DIGEST):
            self.assertNotIn(value, query)
        self.assertEqual(query.count("%s"), len(values))
        self.assertEqual(values[:6], ("sit.local", "doc@example.test", "create", KEY, DIGEST, None))
        self.assertIn("pending", values)
        self.assertEqual(values[-2:], (NOW, NOW))

    def test_update_claim_carries_the_base_modified(self):
        db = FakeDb()
        claim(db, kind="update", base_modified=NOW)
        query, values = db.calls[0]
        self.assertNotIn("2026", query)
        self.assertIn(NOW, values)


class OperationBindingTests(unittest.TestCase):
    def test_commit_binds_every_identity_column_and_guards_pending(self):
        db = FakeDb(rows=[[(1,)]])
        ledger.commit_operation(
            db,
            site="sit.local",
            actor="doc@example.test",
            kind="create",
            operation_key=KEY,
            payload_digest=DIGEST,
            workspace_patient_id=ALIAS,
            native_patient_id="PAT-0001",
            receipt_modified=NOW,
            now=NOW,
        )
        query, values = db.calls[0]
        self.assertTrue(query.lstrip().upper().startswith("UPDATE CLINIC_UI_OPERATION"))
        where = query.split("WHERE", 1)[1]
        for column in ("site", "actor", "kind", "operation_key", "state", "payload_digest"):
            self.assertIn(column, where)
        self.assertIn("committed", values)
        self.assertIn("pending", values)
        self.assertNotIn("sit.local", query)
        self.assertEqual(query.count("%s"), len(values))

    def test_commit_uses_row_count_not_the_update_result(self):
        db = FakeDb(rows=[[(1,)]])
        ledger.commit_operation(
            db,
            site="sit.local",
            actor="doc@example.test",
            kind="create",
            operation_key=KEY,
            payload_digest=DIGEST,
            workspace_patient_id=ALIAS,
            native_patient_id="PAT-0001",
            receipt_modified=NOW,
            now=NOW,
        )
        self.assertEqual(len(db.calls), 2)
        self.assertIn("ROW_COUNT()", db.calls[1][0].upper())
        self.assertIsNone(db.calls[1][1])

    def test_commit_fails_closed_on_any_other_row_count(self):
        for rows in (
            [],
            [[(0,)]],
            [[(2,)]],
            [[("1",)]],
            [[(1,), (1,)]],
        ):
            with self.subTest(rows=rows):
                with self.assertRaises(ledger.OperationNotClaimed):
                    ledger.commit_operation(
                        FakeDb(rows=rows),
                        site="sit.local",
                        actor="doc@example.test",
                        kind="create",
                        operation_key=KEY,
                        payload_digest=DIGEST,
                        workspace_patient_id=ALIAS,
                        native_patient_id="PAT-0001",
                        receipt_modified=NOW,
                        now=NOW,
                    )

    def test_commit_requires_both_server_owned_ids(self):
        with self.assertRaises(ledger.InvalidLedgerInput):
            ledger.commit_operation(
                FakeDb(),
                site="sit.local",
                actor="doc@example.test",
                kind="create",
                operation_key=KEY,
                payload_digest=DIGEST,
                workspace_patient_id=ALIAS,
                native_patient_id="",
                receipt_modified=NOW,
                now=NOW,
            )

    def test_abandon_only_accepts_conflict_or_unknown_without_ids(self):
        db = FakeDb(rows=[[(1,)]])
        ledger.abandon_operation(
            db,
            site="sit.local",
            actor="doc@example.test",
            kind="create",
            operation_key=KEY,
            payload_digest=DIGEST,
            state="unknown",
            now=NOW,
        )
        query, values = db.calls[0]
        self.assertIn("state", query.split("WHERE", 1)[1])
        self.assertIn("pending", values)
        self.assertIn("unknown", values)
        self.assertIn("ROW_COUNT()", db.calls[1][0].upper())

    def test_abandon_fails_closed_when_no_pending_row_matched(self):
        db = FakeDb(rows=[[(0,)]])
        with self.assertRaises(ledger.OperationNotClaimed):
            ledger.abandon_operation(
                db,
                site="sit.local",
                actor="doc@example.test",
                kind="create",
                operation_key=KEY,
                payload_digest=DIGEST,
                state="conflict",
                now=NOW,
            )

    def test_load_operation_filters_on_the_actor_scoped_key(self):
        db = FakeDb(rows=[[(
            "sit.local", "doc@example.test", "create", KEY, DIGEST, None,
            "committed", ALIAS, "PAT-0001", NOW, NOW, NOW,
        )]])
        record = ledger.load_operation(
            db,
            site="sit.local",
            actor="doc@example.test",
            kind="create",
            operation_key=KEY,
        )
        self.assertEqual(record["state"], "committed")
        self.assertEqual(record["workspace_patient_id"], ALIAS)
        query, values = db.calls[0]
        where = query.split("WHERE", 1)[1]
        for column in ("site", "actor", "kind", "operation_key"):
            self.assertIn(column, where)
        self.assertEqual(values, ("sit.local", "doc@example.test", "create", KEY))

    def test_load_operation_returns_none_for_another_actor(self):
        self.assertIsNone(
            ledger.load_operation(
                FakeDb(rows=[]),
                site="sit.local",
                actor="nur@example.test",
                kind="create",
                operation_key=KEY,
            )
        )


class AliasTests(unittest.TestCase):
    def test_alias_insert_is_one_parameterized_statement(self):
        db = FakeDb()
        ledger.insert_alias(
            db,
            workspace_patient_id=ALIAS,
            site="sit.local",
            native_patient_id="PAT-0001",
            operation_key=KEY,
            created_by="doc@example.test",
            creation=NOW,
        )
        query, values = db.calls[0]
        self.assertTrue(
            query.lstrip().upper().startswith("INSERT INTO CLINIC_UI_PATIENT_ALIAS")
        )
        for value in (ALIAS, "sit.local", "PAT-0001", KEY, "doc@example.test"):
            self.assertNotIn(value, query)
        self.assertEqual(query.count("%s"), len(values))

    def test_alias_lookup_is_site_scoped(self):
        db = FakeDb(rows=[[(ALIAS, "sit.local", "PAT-0001", KEY, "doc@example.test", NOW)]])
        record = ledger.load_alias(db, site="sit.local", workspace_patient_id=ALIAS)
        self.assertEqual(record["native_patient_id"], "PAT-0001")
        query, values = db.calls[0]
        self.assertIn("site", query.split("WHERE", 1)[1])
        self.assertEqual(values, ("sit.local", ALIAS))

    def test_alias_lookup_by_native_id_is_site_scoped(self):
        db = FakeDb(rows=[[(ALIAS, "sit.local", "PAT-0001", KEY, "doc@example.test", NOW)]])
        record = ledger.load_alias_by_native(
            db, site="sit.local", native_patient_id="PAT-0001"
        )
        self.assertEqual(record["workspace_patient_id"], ALIAS)
        query, values = db.calls[0]
        self.assertIn("site", query.split("WHERE", 1)[1])
        self.assertEqual(values, ("sit.local", "PAT-0001"))

    def test_module_exposes_no_alias_mutation_helper(self):
        for name in dir(ledger):
            if name.startswith("_"):
                continue
            self.assertNotIn("update_alias", name)
            self.assertNotIn("delete_alias", name)


class TransactionOwnershipTests(unittest.TestCase):
    """The module must not own transactions; the caller's site transaction does."""

    def test_source_contains_no_commit_or_rollback_call(self):
        source = Path(ledger.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"commit", "rollback", "savepoint", "begin"}:
                    offenders.append(node.func.attr)
        self.assertEqual(offenders, [])

    def test_helpers_never_touch_the_fake_transaction_hooks(self):
        db = FakeDb()
        claim(db)
        ledger.load_operation(
            db,
            site="sit.local",
            actor="doc@example.test",
            kind="create",
            operation_key=KEY,
        )
        ledger.insert_alias(
            db,
            workspace_patient_id=ALIAS,
            site="sit.local",
            native_patient_id="PAT-0001",
            operation_key=KEY,
            created_by="doc@example.test",
            creation=NOW,
        )


class CheckNormalizationTests(unittest.TestCase):
    """Only code is normalized; SQL string literals keep case and contents."""

    def normalize(self, clause):
        return ledger._normalize_check(clause)

    def test_literal_case_and_contents_are_preserved(self):
        self.assertNotEqual(self.normalize("x = 'A'"), self.normalize("x = 'a'"))
        self.assertNotEqual(
            self.normalize("x = 'a b'"), self.normalize("x = 'a  b'")
        )
        self.assertNotEqual(self.normalize("x = 'a '"), self.normalize("x = 'a'"))
        self.assertNotEqual(
            self.normalize("x IN ('create')"), self.normalize("x IN ('Create')")
        )

    def test_keywords_whitespace_backticks_and_operator_style_are_normalized(self):
        self.assertEqual(
            self.normalize("KIND  IN  ('create',  'update')"),
            self.normalize("kind in ('create','update')"),
        )
        self.assertEqual(
            self.normalize("`kind` <> 'create'"),
            self.normalize("kind != 'create'"),
        )
        self.assertEqual(
            self.normalize(" ( state <> 'x' OR a IS NULL ) "),
            self.normalize("state <> 'x' OR a IS NULL"),
        )

    def test_quote_escape_styles_compare_equal(self):
        self.assertEqual(
            self.normalize("x = 'it''s'"), self.normalize("x = 'it\\'s'")
        )

    def test_fully_wrapped_expressions_match_unwrapped(self):
        self.assertEqual(self.normalize("(x = 1)"), self.normalize("x = 1"))
        self.assertEqual(self.normalize("((x = 1))"), self.normalize("x = 1"))
        self.assertEqual(
            self.normalize("  ( x = 1 )  "), self.normalize("x = 1")
        )
        self.assertEqual(
            self.normalize("(CHAR_LENGTH(a) = 64)"),
            self.normalize("char_length(a) = 64"),
        )

    def test_fully_wrapped_expressions_preserve_literals(self):
        self.assertEqual(
            self.normalize("(x = 'A')"), self.normalize("x = 'A'")
        )
        self.assertEqual(
            self.normalize("((x IN ('create','update')))"),
            self.normalize("x IN ('create','update')"),
        )
        self.assertNotEqual(
            self.normalize("(x = 'A')"), self.normalize("(x = 'a')")
        )

    def test_nested_parens_that_are_not_outer_wrappers_are_kept(self):
        self.assertEqual(
            self.normalize("(a = 1 OR (b = 2 AND c = 3))"),
            self.normalize("a = 1 OR (b = 2 AND c = 3)"),
        )
        self.assertNotEqual(
            self.normalize("(a = 1 OR b = 2) AND c = 3"),
            self.normalize("a = 1 OR (b = 2 AND c = 3)"),
        )

    def test_literal_that_looks_like_a_keyword_is_not_lowercased(self):
        self.assertNotEqual(
            self.normalize("x = 'NULL'"), self.normalize("x = 'null'")
        )


class PreDispatchContractTests(unittest.TestCase):
    """The site validator is stricter than the external fence's key rule.

    The external fence accepts an operation key with surrounding whitespace; this
    store rejects it (PAD SPACE would collapse it onto another key). Callers must
    therefore run this module's validator before dispatching to any fence. No
    code here claims the fence and the site validation are integrated.
    """

    def test_operation_key_validator_accepts_a_clean_key(self):
        self.assertEqual(ledger.validate_operation_key(KEY), KEY)

    def test_operation_key_validator_rejects_padded_and_invalid_keys(self):
        for bad in (
            " %s" % KEY,
            "%s " % KEY,
            "\t%s" % KEY,
            "%s\n" % KEY,
            "short",
            "k" * 129,
            "key-with-control-\x00-char",
            "",
            None,
        ):
            with self.subTest(key=bad):
                with self.assertRaises(ledger.InvalidLedgerInput):
                    ledger.validate_operation_key(bad)

    def test_padded_key_is_rejected_before_any_statement(self):
        db = FakeDb()
        with self.assertRaises(ledger.InvalidLedgerInput):
            claim(db, operation_key=" %s " % KEY)
        self.assertEqual(db.calls, [])

    def test_contract_is_documented_with_the_fence_divergence(self):
        contract = ledger.PRE_DISPATCH_CONTRACT
        self.assertIn("surrounding whitespace", contract)
        self.assertIn("external fence", contract)


class MigrationPatchTests(unittest.TestCase):
    def test_patch_applies_and_verifies_the_declared_schema(self):
        from clinic_ui.patches import v0_0_1_patient_write_ledger as patch

        db = mock.MagicMock()
        db.sql.side_effect = declared_schema_rows()
        fake_frappe = mock.MagicMock(db=db)
        with mock.patch.dict(sys.modules, {"frappe": fake_frappe}):
            patch.execute()
        self.assertEqual(db.sql_ddl.call_count, len(ledger.SCHEMA_STATEMENTS))
        self.assertTrue(db.sql.called)

    def test_patch_is_intentionally_not_registered(self):
        # patches.txt is intentionally empty: this local experiment adds no
        # MariaDB tables and no migration. The untracked schema groundwork must
        # not be registered just to satisfy a test.
        patch_file = APP_ROOT / "clinic_ui" / "patches.txt"
        lines = [
            line.strip()
            for line in patch_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.assertEqual(lines, [])
        self.assertNotIn(
            "clinic_ui.patches.v0_0_1_patient_write_ledger", lines
        )


if __name__ == "__main__":
    unittest.main()
