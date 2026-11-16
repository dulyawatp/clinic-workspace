"""Server-owned Patient alias + operation receipt storage (groundwork only).

This module is the narrow internal data access a future synthetic Patient
create/edit path will use. Nothing here is wired to an HTTP method, no
capability is enabled, and no migration runs from this module: the two tables
are created once by ``clinic_ui.patches.v0_0_1_patient_write_ledger``. Until
that patch has run on an approved disposable site and the assessment's write
tests pass, patient writes stay disabled.

Storage is two raw MariaDB tables, never a DocType:

* ``clinic_ui_patient_alias`` — immutable ``workspace_patient_id`` ↔ native
  ``Patient`` docname mapping, unique per site for both columns.
* ``clinic_ui_operation`` — keyed idempotency record and committed receipt,
  primary key ``(site, actor, kind, operation_key)``.

Because the names do not start with ``tab``, no generic DocType path
(``/api/resource``, ``frappe.client``, ``frappe.qb``) can reach them: those
paths resolve meta first or build ``tab<name>``. Only a parameterized
``frappe.db.sql`` statement on the literal table name does, which is what this
module issues. There is no ``ignore_permissions`` and no DocPerm involved here;
that bypass is scoped to these two tables and never applies to ``Patient``.

Caller contract
---------------
Transaction ownership belongs to the caller, not to these helpers:

* The caller opens the one native Frappe transaction and calls these helpers
  inside it. No helper commits, rolls back, creates a savepoint or begins a
  transaction.
* The caller must check Patient authority first — ``frappe.has_permission(
  "Patient", "create")`` for create and ``frappe.has_permission("Patient",
  "write", doc=patient)`` for update — before any alias or operation access.
* ``site``, ``actor``, the operation key, the workspace alias and every
  timestamp are server-derived; no helper accepts a client-supplied actor,
  state, receipt or native id override. ``payload_digest`` is supplied by the
  caller and must be the keyed digest of the canonical whitelisted payload
  defined in §8.2.1; this module only validates its shape because the key
  source is an open decision (§8.7).
* Pre-dispatch validation contract: ``validate_operation_key`` is the shared
  rule a caller must run **before** calling any external fence. The external
  fence's own key check accepts surrounding whitespace while this store rejects
  it, so the fence alone is not sufficient. ``PRE_DISPATCH_CONTRACT`` records
  that divergence. This module does not integrate with the fence, does not call
  it and makes no claim that the two validations are one check.
* ``claim_operation`` inserts ``state='pending'``. A duplicate key raises the
  adapter's own integrity error and the caller must roll back to its savepoint,
  then ``load_operation`` and either replay the committed receipt (same
  ``payload_digest``), return a conflict (different digest) or a locked state
  (``pending``/``unknown``).
* ``commit_operation`` and ``abandon_operation`` only advance a ``pending``
  row whose ``(site, actor, kind, operation_key, payload_digest)`` match. The
  UPDATE result is never trusted: Frappe's ``Database.sql`` returns an empty
  tuple for DML, so each helper immediately reads ``SELECT ROW_COUNT()`` on the
  same connection and requires exactly one affected row. Anything else raises
  ``OperationNotClaimed`` and the caller rolls back.
* ``insert_alias`` has no update or delete counterpart. The alias row is written
  in the same transaction as the Patient and the committed receipt, so a
  rollback removes all three.

DDL, commits and authorization
-------------------------------
``apply_schema`` calls ``frappe.db.sql_ddl`` once per table. Frappe's
``sql_ddl`` executes the statement and commits the open transaction itself, so
DDL is **not** inside the caller's patient transaction and cannot be rolled back
with it. There are no data writes in the migration.

Partial failure: if the first ``CREATE TABLE IF NOT EXISTS`` succeeds and the
second fails, the first table survives its implicit commit. Re-running
``bench migrate`` re-enters the patch, the ``IF NOT EXISTS`` clauses make the
already-created table a no-op, and ``verify_schema`` then fails closed on any
pre-existing table whose engine, columns, collations, keys, CHECK clauses or
triggers differ from this declaration. A table is therefore never silently
adopted, and no data is written by the patch.

Migration is a separate authorization: this module and its patch only describe
and verify the schema. Running ``bench migrate`` or the patch function against
any site — live, restored or disposable — is an operator action that needs its
own approval and evidence. Nothing in this change runs it, and no write
capability is enabled by it.

MariaDB verification status
---------------------------
``SCHEMA_STATEMENTS``, the CHECK constraints and ``verify_schema`` are written
against the MariaDB surface described in the assessment but have **not** been
executed against a server here (no bench, site or database on this host).
``verify_schema`` first asserts that ``VERSION()`` names MariaDB at 10.6 or
later, because it reads ``information_schema`` metadata (``CHECK_CONSTRAINTS``,
``STATISTICS.SUB_PART``, ``COLUMNS.IS_GENERATED``/``EXTRA``, ``TRIGGERS``) that
depends on that surface. On a server whose metadata or CHECK text differs, the
patch fails closed and must be re-verified rather than relaxed.

``utf8mb4_bin`` is deliberate: it is a PAD SPACE collation (like every
``utf8mb4`` collation), so values that differ only by trailing spaces compare
equal and would collapse onto one unique key. Every helper therefore rejects any
identity value with leading or trailing whitespace — the same rule as the
external write fence's actor check — instead of trimming it. Comparison is not
byte-exact for Unicode normalization forms: this module performs no NFC/NFD
folding or other canonicalization, and no claim is made that visually identical
strings in different normalization forms are distinct. ``utf8mb4_bin`` compares
the stored code points with PAD SPACE semantics; case stays distinct.

Code-level immutability is the absence of an alias mutation helper plus the
identity columns in every ``WHERE``; the site DB user can still issue raw DML,
which is outside the ordinary-user CRUD claim.
"""

import re
from datetime import datetime
from typing import Mapping

from clinic_ui.core.text import is_valid_text
ALIAS_TABLE = "clinic_ui_patient_alias"
OPERATION_TABLE = "clinic_ui_operation"
SCHEMA_TABLES = (ALIAS_TABLE, OPERATION_TABLE)

KINDS = frozenset({"create", "update"})
STATES = frozenset({"pending", "committed", "conflict", "unknown"})
FINAL_STATES = frozenset({"committed", "conflict", "unknown"})
PENDING = "pending"
COMMITTED = "committed"

# Documentation-only contract constant. Callers must validate ``actor`` (no
# surrounding whitespace) and ``operation_key`` with this module before calling
# any external fence: the fence accepts surrounding whitespace on the operation
# key while this store rejects it (PAD SPACE could collapse the padded key onto
# another). No code in this app performs or claims that integration.
PRE_DISPATCH_CONTRACT = (
    "Validate actor and operation_key with this module before dispatching to "
    "any external fence: the external fence accepts surrounding whitespace on "
    "the operation key while this store rejects it."
)

MIN_OPERATION_KEY_LENGTH = 16
MIN_ALIAS_LENGTH = 8
MAX_OPERATION_KEY_LENGTH = 128
MAX_SITE_LENGTH = 140
MAX_ACTOR_LENGTH = 140
MAX_ALIAS_LENGTH = 64
MAX_PATIENT_ID_LENGTH = 140
DIGEST_LENGTH = 64
_HEX = frozenset("0123456789abcdef")

OPERATION_COLUMNS = (
    "site",
    "actor",
    "kind",
    "operation_key",
    "payload_digest",
    "base_modified",
    "state",
    "workspace_patient_id",
    "native_patient_id",
    "receipt_modified",
    "creation",
    "modified",
)

ALIAS_COLUMNS = (
    "workspace_patient_id",
    "site",
    "native_patient_id",
    "operation_key",
    "created_by",
    "creation",
)

_OPERATION_DDL = """
CREATE TABLE IF NOT EXISTS clinic_ui_operation (
    site VARCHAR(140) NOT NULL,
    actor VARCHAR(140) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    payload_digest CHAR(64) NOT NULL,
    base_modified DATETIME(6) NULL,
    state VARCHAR(16) NOT NULL,
    workspace_patient_id VARCHAR(64) NULL,
    native_patient_id VARCHAR(140) NULL,
    receipt_modified DATETIME(6) NULL,
    creation DATETIME(6) NOT NULL,
    modified DATETIME(6) NOT NULL,
    PRIMARY KEY (site, actor, kind, operation_key),
    CONSTRAINT ck_operation_kind CHECK (kind IN ('create', 'update')),
    CONSTRAINT ck_operation_state CHECK (
        state IN ('pending', 'committed', 'conflict', 'unknown')
    ),
    CONSTRAINT ck_operation_digest_length CHECK (CHAR_LENGTH(payload_digest) = 64),
    CONSTRAINT ck_operation_key_length CHECK (CHAR_LENGTH(operation_key) >= 16),
    CONSTRAINT ck_operation_committed_receipt CHECK (
        state <> 'committed' OR (
            native_patient_id IS NOT NULL
            AND workspace_patient_id IS NOT NULL
            AND receipt_modified IS NOT NULL
        )
    ),
    CONSTRAINT ck_operation_pending_blank CHECK (
        state <> 'pending' OR (
            native_patient_id IS NULL
            AND workspace_patient_id IS NULL
            AND receipt_modified IS NULL
        )
    ),
    CONSTRAINT ck_operation_update_base CHECK (
        kind <> 'update' OR base_modified IS NOT NULL
    ),
    CONSTRAINT ck_operation_create_base CHECK (
        kind <> 'create' OR base_modified IS NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""

_ALIAS_DDL = """
CREATE TABLE IF NOT EXISTS clinic_ui_patient_alias (
    workspace_patient_id VARCHAR(64) NOT NULL,
    site VARCHAR(140) NOT NULL,
    native_patient_id VARCHAR(140) NOT NULL,
    operation_key VARCHAR(128) NOT NULL,
    created_by VARCHAR(140) NOT NULL,
    creation DATETIME(6) NOT NULL,
    PRIMARY KEY (workspace_patient_id),
    UNIQUE KEY uq_alias_site_native (site, native_patient_id),
    UNIQUE KEY uq_alias_site_operation (site, operation_key),
    CONSTRAINT ck_alias_workspace_id_length CHECK (
        CHAR_LENGTH(workspace_patient_id) >= 8
    ),
    CONSTRAINT ck_alias_native_id_length CHECK (CHAR_LENGTH(native_patient_id) >= 1)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""

# Alias first: the mapping table is the durable identity surface.
SCHEMA_STATEMENTS = (_ALIAS_DDL, _OPERATION_DDL)

EXPECTED_ENGINE = "InnoDB"
EXPECTED_TABLE_COLLATION = "utf8mb4_bin"
MINIMUM_SERVER_VERSION = (10, 6)

# (column, data_type, character_maximum_length, datetime_precision, is_nullable)
EXPECTED_COLUMNS = {
    ALIAS_TABLE: (
        ("workspace_patient_id", "varchar", 64, None, "NO"),
        ("site", "varchar", 140, None, "NO"),
        ("native_patient_id", "varchar", 140, None, "NO"),
        ("operation_key", "varchar", 128, None, "NO"),
        ("created_by", "varchar", 140, None, "NO"),
        ("creation", "datetime", None, 6, "NO"),
    ),
    OPERATION_TABLE: (
        ("site", "varchar", 140, None, "NO"),
        ("actor", "varchar", 140, None, "NO"),
        ("kind", "varchar", 16, None, "NO"),
        ("operation_key", "varchar", 128, None, "NO"),
        ("payload_digest", "char", 64, None, "NO"),
        ("base_modified", "datetime", None, 6, "YES"),
        ("state", "varchar", 16, None, "NO"),
        ("workspace_patient_id", "varchar", 64, None, "YES"),
        ("native_patient_id", "varchar", 140, None, "YES"),
        ("receipt_modified", "datetime", None, 6, "YES"),
        ("creation", "datetime", None, 6, "NO"),
        ("modified", "datetime", None, 6, "NO"),
    ),
}

# column -> collation; None for the temporal columns.
EXPECTED_COLLATIONS = {
    ALIAS_TABLE: {
        "workspace_patient_id": EXPECTED_TABLE_COLLATION,
        "site": EXPECTED_TABLE_COLLATION,
        "native_patient_id": EXPECTED_TABLE_COLLATION,
        "operation_key": EXPECTED_TABLE_COLLATION,
        "created_by": EXPECTED_TABLE_COLLATION,
        "creation": None,
    },
    OPERATION_TABLE: {
        "site": EXPECTED_TABLE_COLLATION,
        "actor": EXPECTED_TABLE_COLLATION,
        "kind": EXPECTED_TABLE_COLLATION,
        "operation_key": EXPECTED_TABLE_COLLATION,
        "payload_digest": EXPECTED_TABLE_COLLATION,
        "base_modified": None,
        "state": EXPECTED_TABLE_COLLATION,
        "workspace_patient_id": EXPECTED_TABLE_COLLATION,
        "native_patient_id": EXPECTED_TABLE_COLLATION,
        "receipt_modified": None,
        "creation": None,
        "modified": None,
    },
}

# index name -> (unique, ordered columns); no prefix parts are allowed.
EXPECTED_INDEXES = {
    ALIAS_TABLE: {
        "PRIMARY": (True, ("workspace_patient_id",)),
        "uq_alias_site_native": (True, ("site", "native_patient_id")),
        "uq_alias_site_operation": (True, ("site", "operation_key")),
    },
    OPERATION_TABLE: {
        "PRIMARY": (True, ("site", "actor", "kind", "operation_key")),
    },
}

# constraint name -> the clause this module's DDL declares. Comparison
# normalizes case, whitespace and backticks only; the expression content,
# operators and parentheses must match exactly.
EXPECTED_CHECKS = {
    ALIAS_TABLE: {
        "ck_alias_workspace_id_length": "CHAR_LENGTH(workspace_patient_id) >= 8",
        "ck_alias_native_id_length": "CHAR_LENGTH(native_patient_id) >= 1",
    },
    OPERATION_TABLE: {
        "ck_operation_kind": "kind IN ('create', 'update')",
        "ck_operation_state": (
            "state IN ('pending', 'committed', 'conflict', 'unknown')"
        ),
        "ck_operation_digest_length": "CHAR_LENGTH(payload_digest) = 64",
        "ck_operation_key_length": "CHAR_LENGTH(operation_key) >= 16",
        "ck_operation_committed_receipt": (
            "state <> 'committed' OR (native_patient_id IS NOT NULL"
            " AND workspace_patient_id IS NOT NULL"
            " AND receipt_modified IS NOT NULL)"
        ),
        "ck_operation_pending_blank": (
            "state <> 'pending' OR (native_patient_id IS NULL"
            " AND workspace_patient_id IS NULL"
            " AND receipt_modified IS NULL)"
        ),
        "ck_operation_update_base": (
            "kind <> 'update' OR base_modified IS NOT NULL"
        ),
        "ck_operation_create_base": "kind <> 'create' OR base_modified IS NULL",
    },
}

_VERSION_SQL = "SELECT VERSION()"

_TABLES_SQL = """
SELECT TABLE_NAME, TABLE_TYPE, ENGINE, TABLE_COLLATION
FROM information_schema.TABLES
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)
ORDER BY TABLE_NAME
"""

_COLUMNS_SQL = """
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH,
       DATETIME_PRECISION, IS_NULLABLE, COLLATION_NAME, COLUMN_DEFAULT,
       EXTRA, IS_GENERATED
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)
ORDER BY TABLE_NAME, ORDINAL_POSITION
"""

_INDEXES_SQL = """
SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE, SEQ_IN_INDEX, COLUMN_NAME, SUB_PART
FROM information_schema.STATISTICS
WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)
ORDER BY TABLE_NAME, INDEX_NAME, SEQ_IN_INDEX
"""

_CHECKS_SQL = """
SELECT TABLE_NAME, CONSTRAINT_NAME, CHECK_CLAUSE
FROM information_schema.CHECK_CONSTRAINTS
WHERE CONSTRAINT_SCHEMA = DATABASE() AND TABLE_NAME IN (%s, %s)
ORDER BY TABLE_NAME, CONSTRAINT_NAME
"""

_TRIGGERS_SQL = """
SELECT EVENT_OBJECT_TABLE, TRIGGER_NAME
FROM information_schema.TRIGGERS
WHERE TRIGGER_SCHEMA = DATABASE() AND EVENT_OBJECT_TABLE IN (%s, %s)
ORDER BY EVENT_OBJECT_TABLE, TRIGGER_NAME
"""

_ROW_COUNT_SQL = "SELECT ROW_COUNT()"


class LedgerError(Exception):
    """Base error for the server-owned alias/receipt store."""


class InvalidLedgerInput(LedgerError):
    """A value failed validation before any SQL was issued."""


class OperationNotClaimed(LedgerError):
    """No single pending row matched the bound identity; fail closed."""


class SchemaMismatch(LedgerError):
    """The installed tables do not match the declared shape."""


__all__ = [
    "ALIAS_COLUMNS",
    "ALIAS_TABLE",
    "COMMITTED",
    "DIGEST_LENGTH",
    "EXPECTED_CHECKS",
    "EXPECTED_COLLATIONS",
    "EXPECTED_COLUMNS",
    "EXPECTED_ENGINE",
    "EXPECTED_INDEXES",
    "EXPECTED_TABLE_COLLATION",
    "FINAL_STATES",
    "InvalidLedgerInput",
    "KINDS",
    "LedgerError",
    "MAX_ACTOR_LENGTH",
    "MAX_ALIAS_LENGTH",
    "MAX_OPERATION_KEY_LENGTH",
    "MAX_PATIENT_ID_LENGTH",
    "MAX_SITE_LENGTH",
    "MINIMUM_SERVER_VERSION",
    "MIN_ALIAS_LENGTH",
    "MIN_OPERATION_KEY_LENGTH",
    "OPERATION_COLUMNS",
    "OPERATION_TABLE",
    "OperationNotClaimed",
    "PENDING",
    "PRE_DISPATCH_CONTRACT",
    "SCHEMA_STATEMENTS",
    "SCHEMA_TABLES",
    "STATES",
    "SchemaMismatch",
    "abandon_operation",
    "apply_schema",
    "claim_operation",
    "commit_operation",
    "insert_alias",
    "load_alias",
    "load_alias_by_native",
    "load_operation",
    "validate_operation_key",
    "verify_schema",
]


def apply_schema(db) -> None:
    """Create the two raw tables once. DDL only; no data and no request path.

    ``frappe.db.sql_ddl`` commits each statement itself, so this runs outside
    any caller transaction and a partial success survives. ``verify_schema`` is
    the fail-closed follow-up; re-running the patch is the recovery path.
    """

    for statement in SCHEMA_STATEMENTS:
        db.sql_ddl(statement)


def verify_schema(db) -> None:
    """Fail closed unless the installed tables match the declared shape.

    Checks the server identity/version, base-table engine and collation, every
    column type/nullability/collation/default/generated state, the exact unique
    keys with no prefix parts, every named CHECK clause and the absence of any
    trigger on the two tables.
    """

    values = (ALIAS_TABLE, OPERATION_TABLE)
    _verify_server(db)
    _verify_tables(db, values)
    _verify_columns(db, values)
    _verify_indexes(db, values)
    _verify_checks(db, values)
    _verify_triggers(db, values)


def _verify_server(db):
    rows = db.sql(_VERSION_SQL)
    version = rows[0][0] if rows and len(rows[0]) == 1 else None
    if not isinstance(version, str) or "mariadb" not in version.lower():
        raise SchemaMismatch("clinic ledger requires a MariaDB server")
    # MariaDB reports either "10.6.16-MariaDB-..." or a MySQL compatibility
    # prefix such as "5.5.5-10.6.16-MariaDB"; the version that counts is the one
    # immediately before the MariaDB marker, never a later distro suffix.
    marker = version.lower().find("mariadb")
    prefix = version[:marker] if marker > 0 else version
    triples = re.findall(r"(\d+)\.(\d+)\.(\d+)", prefix) or re.findall(
        r"(\d+)\.(\d+)\.(\d+)", version
    )
    if not triples:
        raise SchemaMismatch("clinic ledger cannot read the server version")
    major, minor, _patch = (int(part) for part in triples[-1])
    if (major, minor) < MINIMUM_SERVER_VERSION:
        raise SchemaMismatch("clinic ledger requires MariaDB 10.6 or later")


def _verify_tables(db, values):
    found = {}
    for table, table_type, engine, collation in db.sql(_TABLES_SQL, values):
        found[table] = (table_type, engine, collation)
    expected = {
        table: ("BASE TABLE", EXPECTED_ENGINE, EXPECTED_TABLE_COLLATION)
        for table in SCHEMA_TABLES
    }
    if found != expected:
        raise SchemaMismatch("clinic ledger tables are not InnoDB base tables")


def _verify_columns(db, values):
    found = {}
    for row in db.sql(_COLUMNS_SQL, values):
        table = row[0]
        found.setdefault(table, []).append(tuple(row[1:]))
    expected = {}
    for table, spec in EXPECTED_COLUMNS.items():
        expected[table] = [
            (
                name,
                data_type,
                length,
                precision,
                nullable,
                EXPECTED_COLLATIONS[table][name],
            )
            for name, data_type, length, precision, nullable in spec
        ]
    if {
        table: [row[:6] for row in rows] for table, rows in found.items()
    } != expected:
        raise SchemaMismatch("clinic ledger columns do not match the declared shape")

    for table, rows in found.items():
        for row in rows:
            _name, _type, _length, _precision, nullable, _collation = row[:6]
            default, extra, generated = row[6:9]
            if not _is_no_default(default, nullable):
                raise SchemaMismatch(
                    "clinic ledger columns must not have defaults"
                )
            if extra:
                raise SchemaMismatch("clinic ledger columns must not be generated")
            if str(generated or "").upper() not in ("", "NEVER"):
                raise SchemaMismatch("clinic ledger columns must not be generated")


def _is_no_default(value, nullable):
    """Whether ``COLUMN_DEFAULT`` means "no default" for this column.

    MariaDB reports the SQL ``NULL`` either as a NULL value or as the unquoted
    metadata text ``NULL``; a quoted ``'NULL'`` is the string literal and is a
    real default. An explicit ``DEFAULT NULL`` is only legal on a nullable
    column, so the unquoted text is accepted there and rejected on a NOT NULL
    column.
    """

    if value is None:
        return True
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or text[0] in "'\"":
        return False
    if text.upper() != "NULL":
        return False
    return nullable == "YES"


def _verify_indexes(db, values):
    found = {}
    for table, name, non_unique, _position, column, sub_part in db.sql(
        _INDEXES_SQL, values
    ):
        if sub_part is not None:
            raise SchemaMismatch("clinic ledger keys must not use prefix parts")
        unique, ordered = found.get((table, name), (not bool(non_unique), []))
        found[(table, name)] = (unique, [*ordered, column])
    declared = {
        (table, name): (unique, list(ordered))
        for table, spec in EXPECTED_INDEXES.items()
        for name, (unique, ordered) in spec.items()
    }
    if found != declared:
        raise SchemaMismatch("clinic ledger keys do not match the declared shape")


def _verify_checks(db, values):
    found = {}
    for table, name, clause in db.sql(_CHECKS_SQL, values):
        found.setdefault(table, {})[name] = _normalize_check(clause)
    declared = {
        table: {
            name: _normalize_check(clause) for name, clause in spec.items()
        }
        for table, spec in EXPECTED_CHECKS.items()
    }
    if found != declared:
        raise SchemaMismatch("clinic ledger checks do not match the declared shape")


def _verify_triggers(db, values):
    if db.sql(_TRIGGERS_SQL, values):
        raise SchemaMismatch("clinic ledger tables must not have triggers")


def _normalize_check(clause):
    """Normalize code only; SQL string literals keep their case and contents."""

    if not isinstance(clause, str):
        raise SchemaMismatch("clinic ledger check clause is unreadable")
    segments = _strip_outer_parens(_split_literals(clause))
    normalized = []
    for is_literal, text in segments:
        if is_literal:
            content = text.replace("''", "'").replace("\\'", "'")
            normalized.append("'" + content + "'")
        else:
            code = text.replace("`", "").replace("!=", "<>").lower()
            code = " ".join(code.split())
            normalized.append(re.sub(r"\s*([(),])\s*", r"\1", code))
    return "".join(normalized)


def _split_literals(clause):
    """Split into ``(is_literal, text)`` segments, handling both quote escapes."""

    segments = []
    code = ""
    index = 0
    while index < len(clause):
        character = clause[index]
        if character != "'":
            code += character
            index += 1
            continue
        if code:
            segments.append((False, code))
            code = ""
        index += 1
        literal = ""
        while index < len(clause):
            character = clause[index]
            if character == "\\" and index + 1 < len(clause):
                literal += clause[index:index + 2]
                index += 2
                continue
            if character == "'":
                if index + 1 < len(clause) and clause[index + 1] == "'":
                    literal += "'"
                    index += 2
                    continue
                break
            literal += character
            index += 1
        segments.append((True, literal))
        index += 1
    if code:
        segments.append((False, code))
    return segments


def _strip_outer_parens(segments):
    while segments:
        if segments[0][0] or segments[-1][0]:
            return segments
        if len(segments) == 1:
            text = segments[0][1].strip()
            if not text.startswith("(") or not text.endswith(")"):
                return segments
            candidate = [(False, text[1:-1])]
            if _code_balance(candidate) != 0:
                return segments
            segments = candidate
            continue
        first = segments[0][1].lstrip()
        last = segments[-1][1].rstrip()
        if not first.startswith("(") or not last.endswith(")"):
            return segments
        candidate = list(segments)
        candidate[0] = (False, first[1:])
        candidate[-1] = (False, last[:-1])
        if _code_balance(candidate) != 0:
            return segments
        segments = candidate
    return segments


def _code_balance(segments):
    depth = 0
    for is_literal, text in segments:
        if is_literal:
            continue
        for character in text:
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth < 0:
                    return depth
    return depth


def claim_operation(
    db,
    *,
    site,
    actor,
    kind,
    operation_key,
    payload_digest,
    now,
    base_modified=None,
) -> None:
    """Insert the ``pending`` claim for one actor-scoped operation key.

    The caller wraps this in its own transaction; a duplicate key surfaces as the
    adapter's integrity error and must be handled at the caller's savepoint.
    """

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    actor = _require_identifier(actor, "actor", MAX_ACTOR_LENGTH)
    kind = _require_kind(kind)
    operation_key = validate_operation_key(operation_key)
    payload_digest = _require_digest(payload_digest)
    now = _require_datetime(now, "now")
    if kind == "create":
        if base_modified is not None:
            raise InvalidLedgerInput("create must not carry base_modified")
    else:
        base_modified = _require_datetime(base_modified, "base_modified")

    db.sql(
        """
        INSERT INTO clinic_ui_operation
            (site, actor, kind, operation_key, payload_digest, base_modified,
             state, creation, modified)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            site,
            actor,
            kind,
            operation_key,
            payload_digest,
            base_modified,
            PENDING,
            now,
            now,
        ),
    )


def load_operation(db, *, site, actor, kind, operation_key):
    """Read only this actor's operation row. Another actor never sees it."""

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    actor = _require_identifier(actor, "actor", MAX_ACTOR_LENGTH)
    kind = _require_kind(kind)
    operation_key = validate_operation_key(operation_key)

    rows = db.sql(
        """
        SELECT site, actor, kind, operation_key, payload_digest, base_modified,
               state, workspace_patient_id, native_patient_id, receipt_modified,
               creation, modified
        FROM clinic_ui_operation
        WHERE site = %s AND actor = %s AND kind = %s AND operation_key = %s
        """,
        (site, actor, kind, operation_key),
    )
    return _mapping(OPERATION_COLUMNS, rows[0]) if rows else None


def commit_operation(
    db,
    *,
    site,
    actor,
    kind,
    operation_key,
    payload_digest,
    workspace_patient_id,
    native_patient_id,
    receipt_modified,
    now,
) -> None:
    """Advance the matching ``pending`` row to ``committed`` with its receipt."""

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    actor = _require_identifier(actor, "actor", MAX_ACTOR_LENGTH)
    kind = _require_kind(kind)
    operation_key = validate_operation_key(operation_key)
    payload_digest = _require_digest(payload_digest)
    workspace_patient_id = _require_identifier(
        workspace_patient_id, "workspace_patient_id", MAX_ALIAS_LENGTH,
        MIN_ALIAS_LENGTH,
    )
    native_patient_id = _require_identifier(
        native_patient_id, "native_patient_id", MAX_PATIENT_ID_LENGTH
    )
    receipt_modified = _require_datetime(receipt_modified, "receipt_modified")
    now = _require_datetime(now, "now")

    db.sql(
        """
        UPDATE clinic_ui_operation
        SET state = %s, workspace_patient_id = %s, native_patient_id = %s,
            receipt_modified = %s, modified = %s
        WHERE site = %s AND actor = %s AND kind = %s AND operation_key = %s
          AND state = %s AND payload_digest = %s
        """,
        (
            COMMITTED,
            workspace_patient_id,
            native_patient_id,
            receipt_modified,
            now,
            site,
            actor,
            kind,
            operation_key,
            PENDING,
            payload_digest,
        ),
    )
    _require_affected_one(db)


def abandon_operation(
    db,
    *,
    site,
    actor,
    kind,
    operation_key,
    payload_digest,
    state,
    now,
) -> None:
    """Advance the matching ``pending`` row to ``conflict`` or ``unknown``."""

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    actor = _require_identifier(actor, "actor", MAX_ACTOR_LENGTH)
    kind = _require_kind(kind)
    operation_key = validate_operation_key(operation_key)
    payload_digest = _require_digest(payload_digest)
    if state not in ("conflict", "unknown"):
        raise InvalidLedgerInput("abandon state must be conflict or unknown")
    now = _require_datetime(now, "now")

    db.sql(
        """
        UPDATE clinic_ui_operation
        SET state = %s, modified = %s
        WHERE site = %s AND actor = %s AND kind = %s AND operation_key = %s
          AND state = %s AND payload_digest = %s
        """,
        (state, now, site, actor, kind, operation_key, PENDING, payload_digest),
    )
    _require_affected_one(db)


def insert_alias(
    db,
    *,
    workspace_patient_id,
    site,
    native_patient_id,
    operation_key,
    created_by,
    creation,
) -> None:
    """Insert one immutable alias row. There is no update or delete helper."""

    workspace_patient_id = _require_identifier(
        workspace_patient_id, "workspace_patient_id", MAX_ALIAS_LENGTH,
        MIN_ALIAS_LENGTH,
    )
    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    native_patient_id = _require_identifier(
        native_patient_id, "native_patient_id", MAX_PATIENT_ID_LENGTH
    )
    operation_key = validate_operation_key(operation_key)
    created_by = _require_identifier(created_by, "created_by", MAX_ACTOR_LENGTH)
    creation = _require_datetime(creation, "creation")

    db.sql(
        """
        INSERT INTO clinic_ui_patient_alias
            (workspace_patient_id, site, native_patient_id, operation_key,
             created_by, creation)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            workspace_patient_id,
            site,
            native_patient_id,
            operation_key,
            created_by,
            creation,
        ),
    )


def load_alias(db, *, site, workspace_patient_id):
    """Resolve a server-issued workspace alias on one site."""

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    workspace_patient_id = _require_identifier(
        workspace_patient_id, "workspace_patient_id", MAX_ALIAS_LENGTH,
        MIN_ALIAS_LENGTH,
    )
    rows = db.sql(
        """
        SELECT workspace_patient_id, site, native_patient_id, operation_key,
               created_by, creation
        FROM clinic_ui_patient_alias
        WHERE site = %s AND workspace_patient_id = %s
        """,
        (site, workspace_patient_id),
    )
    return _mapping(ALIAS_COLUMNS, rows[0]) if rows else None


def load_alias_by_native(db, *, site, native_patient_id):
    """Reverse lookup used by the seed/reconciliation step, never by a client."""

    site = _require_identifier(site, "site", MAX_SITE_LENGTH)
    native_patient_id = _require_identifier(
        native_patient_id, "native_patient_id", MAX_PATIENT_ID_LENGTH
    )
    rows = db.sql(
        """
        SELECT workspace_patient_id, site, native_patient_id, operation_key,
               created_by, creation
        FROM clinic_ui_patient_alias
        WHERE site = %s AND native_patient_id = %s
        """,
        (site, native_patient_id),
    )
    return _mapping(ALIAS_COLUMNS, rows[0]) if rows else None


def _mapping(columns, row):
    if isinstance(row, Mapping):
        return {column: row[column] for column in columns}
    return dict(zip(columns, row))


def _require_identifier(value, field, max_length, minimum=1):
    if not is_valid_text(value, max_length) or len(value) < minimum:
        raise InvalidLedgerInput("%s is not a bounded identifier" % field)
    if value != value.strip():
        raise InvalidLedgerInput("%s must not have surrounding whitespace" % field)
    return value


def _require_kind(kind):
    if kind not in KINDS:
        raise InvalidLedgerInput("kind must be create or update")
    return kind


def validate_operation_key(operation_key):
    """Pre-dispatch key rule; stricter than the external fence's key check.

    The fence only bounds length and rejects control characters, so it accepts a
    key with surrounding whitespace. This store rejects that key: ``utf8mb4_bin``
    is PAD SPACE, so a padded replay could collapse onto the committed row. A
    caller that uses the fence must run this validator before dispatch; nothing
    here integrates the two components or claims they share one check.
    """

    if (
        not is_valid_text(operation_key, MAX_OPERATION_KEY_LENGTH)
        or len(operation_key) < MIN_OPERATION_KEY_LENGTH
        or operation_key != operation_key.strip()
    ):
        raise InvalidLedgerInput("operation_key is not a bounded identifier")
    return operation_key


def _require_digest(payload_digest):
    if (
        not isinstance(payload_digest, str)
        or len(payload_digest) != DIGEST_LENGTH
        or any(char not in _HEX for char in payload_digest)
    ):
        raise InvalidLedgerInput("payload_digest must be 64 lowercase hex characters")
    return payload_digest


def _require_datetime(value, field):
    if not isinstance(value, datetime):
        raise InvalidLedgerInput("%s must be a datetime" % field)
    return value


def _require_affected_one(db):
    # Frappe's Database.sql returns an empty tuple for DML, so ROW_COUNT() is the
    # only trustworthy count. It reflects the statement this helper just ran on
    # the caller's connection, and the caller must not interleave anything.
    rows = db.sql(_ROW_COUNT_SQL)
    if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not int:
        raise OperationNotClaimed("the affected row count is unreadable")
    if rows[0][0] != 1:
        raise OperationNotClaimed("no single pending row matched the bound identity")
