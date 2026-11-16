"""One-time DDL for ``clinic_ui_patient_alias`` and ``clinic_ui_operation``.

Groundwork only: this patch is not run by any test, endpoint or deploy step in
this change, and no write capability is enabled by it. It creates the two raw
tables (§8.2.1) with ``frappe.db.sql_ddl`` and then verifies their server
version, engine, columns, collations, unique keys, CHECK clauses and trigger
absence with ``information_schema``.

``sql_ddl`` executes each statement and commits the open transaction itself, so
this DDL is outside any patient transaction and cannot be rolled back with one.
If the first ``CREATE TABLE IF NOT EXISTS`` succeeds and the second fails, the
first table survives its implicit commit; re-running ``bench migrate`` re-enters
the patch, the existing table is a no-op and ``verify_schema`` fails closed on
any shape that differs from this declaration. No data is written, migrated or
seeded here, and no DocType is created.

Running this patch — on a live site, a restored copy or a disposable target — is
a separately authorized operator action. Nothing in this change executes it; the
function exists so that later migration review has one bounded entry point.
"""


def execute():
    import frappe

    from clinic_ui.core.patient_write_ledger import apply_schema, verify_schema

    apply_schema(frappe.db)
    verify_schema(frappe.db)
