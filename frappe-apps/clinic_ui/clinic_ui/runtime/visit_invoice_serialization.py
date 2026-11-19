"""Unwired appointment row-lock lane for the native visit invoice endpoint.

Gateway integration: create one VisitInvoiceLane(db, appointment_id) per request;
return it from visit_invoice_serialized, and delegate commit_visit_invoice and
rollback_visit_invoice to that same instance's commit and rollback methods.
Enter only after read-only authorization, with no preceding writes or pending
transaction callbacks: entry explicitly rolls back the ambient transaction.
The endpoint must repeat authoritative eligibility checks after entry.

Only pinned Frappe v16.54.0 / PyMySQL 1.1.2 behavior is supported. All code and
lifecycle callbacks within the lane must avoid commits, rollbacks, DDL, connection
replacement, and other transaction-control operations except through this adapter.
These installation and lifecycle assumptions require independent verification.
This locks an appointment row, not all possible Desk invoice creation paths.
Commit errors are uncertain, never retry; propagate all cleanup failures.
"""

from importlib.metadata import PackageNotFoundError, version


class SerializationError(RuntimeError):
    """Transaction or serialization cannot be confirmed."""


def _supported_connection(connection):
    try:
        import pymysql
        return (version("PyMySQL") == "1.1.2"
                and type(connection) is pymysql.connections.Connection)
    except (ImportError, PackageNotFoundError):
        return False


class VisitInvoiceLane:
    def __init__(self, db, appointment_id):
        self.db = db
        self.appointment_id = appointment_id
        self.connection = None
        self.identity = None
        self.active = False
        self.finished = False

    def _check(self):
        try:
            valid = (self.db.db_type == "mariadb"
                     and self.db._disable_transaction_control == 0
                     and self.db._conn is self.connection
                     and self.connection.open is True
                     and self.connection.get_autocommit() is False
                     and self.connection.thread_id() == self.identity)
        except Exception as error:
            raise SerializationError("connection continuity unavailable") from error
        if not valid:
            raise SerializationError("connection or transaction control changed")

    def _finish(self, operation):
        self._check()
        try:
            getattr(self.db, operation)()
            self._check()
        except Exception as error:
            raise SerializationError(f"{operation} unconfirmed") from error

    def __enter__(self):
        if (self.active or self.finished or type(self.appointment_id) is not str
                or not self.appointment_id or len(self.appointment_id) > 140
                or self.appointment_id != self.appointment_id.strip()):
            raise SerializationError("invalid lane or appointment")
        self.connection = getattr(self.db, "_conn", None)
        if not _supported_connection(self.connection):
            raise SerializationError("unsupported connection")
        try:
            self.identity = self.connection.thread_id()
            if type(self.identity) is not int or self.identity <= 0:
                raise SerializationError("invalid connection identity")
            self._finish("rollback")
            self.active = True
            rows = self.db.sql(
                "SELECT name, invoiced, ref_sales_invoice FROM `tabPatient Appointment` "
                "WHERE name = %s AND BINARY name = BINARY %s FOR UPDATE NOWAIT",
                (self.appointment_id, self.appointment_id),
            )
            self._check()
            if (not isinstance(rows, (tuple, list)) or len(rows) != 1
                    or not isinstance(rows[0], (tuple, list)) or len(rows[0]) != 3
                    or rows[0][0] != self.appointment_id
                    or type(rows[0][1]) is not int or rows[0][1] != 0
                    or rows[0][2] not in (None, "")):
                raise SerializationError("appointment unavailable")
        except Exception as error:
            if self.active:
                self.rollback()
            if isinstance(error, SerializationError):
                raise
            raise SerializationError("appointment lock unavailable") from error
        return True

    def commit(self):
        if not self.active or self.finished:
            raise SerializationError("lane not active")
        self._finish("commit")
        self.finished = True
        self.active = False

    def rollback(self):
        if not self.active or self.finished:
            raise SerializationError("lane not active")
        self._finish("rollback")
        self.finished = True
        self.active = False

    def __exit__(self, exc_type, exc_value, traceback):
        if self.active:
            self.rollback()
        return False
