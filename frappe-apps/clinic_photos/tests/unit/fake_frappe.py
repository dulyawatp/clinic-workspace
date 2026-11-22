"""Narrow in-memory Frappe stub for runtime adapter tests.

Implements only the attributes the clinic_photos runtime adapter calls. It is a
local fixture, not a Frappe simulation: no installed MRO, DocPerm, MariaDB,
transaction or HTTP semantics are claimed.
"""

import contextlib
import re
import sys
import types


class FakeCallbacks:
    def __init__(self):
        self.callbacks = []

    def add(self, callback):
        self.callbacks.append(callback)

    def run(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()

    def reset(self):
        self.callbacks = []


class FakeDB:
    def __init__(self, docs=None, site_path=None):
        self.docs = {}
        for doctype, rows in (docs or {}).items():
            self.docs[doctype] = {name: dict(row) for name, row in rows.items()}
        self.before_commit = FakeCallbacks()
        self.after_commit = FakeCallbacks()
        self.before_rollback = FakeCallbacks()
        self.after_rollback = FakeCallbacks()
        self.site_path = site_path
        self.lock_denied = False
        self.lock_error = None
        self.release_error = None
        self.release_result = None
        self.event_log = []
        self.held_locks = set()
        self.lock_calls = []
        self.lock_timeouts = []
        self.release_calls = []

    def add(self, doctype, name, **values):
        self.docs.setdefault(doctype, {})[name] = dict(values, name=name)
        return self.docs[doctype][name]

    def set_value(self, doctype, name, **values):
        row = self.docs.setdefault(doctype, {}).setdefault(name, {"name": name})
        row.update(values)
        return row

    def exists(self, doctype, name):
        return name in self.docs.get(doctype, {})

    def escape(self, value):
        return "'" + str(value).replace("'", "''") + "'"

    def sql(self, query, values=(), **kwargs):
        normalized = " ".join(str(query).split()).lower()
        if "get_lock" in normalized:
            name, _timeout = values
            self.lock_calls.append(name)
            self.lock_timeouts.append(_timeout)
            if self.lock_error is not None:
                raise self.lock_error
            if self.lock_denied:
                return [(0,)]
            self.held_locks.add(name)
            return [(1,)]
        if "release_lock" in normalized:
            name = values[0]
            self.release_calls.append(name)
            self.event_log.append(("release", name))
            if self.release_error is not None:
                raise self.release_error
            if self.release_result is not None:
                return self.release_result
            if name not in self.held_locks:
                return [(None,)]
            self.held_locks.discard(name)
            return [(1,)]
        raise AssertionError(f"unexpected fake SQL: {query!r}")

    def commit(self):
        """Mirror installed Database.commit callback ordering."""
        self.before_rollback.reset()
        self.after_rollback.reset()
        self.before_commit.run()
        self.after_commit.run()

    def rollback(self):
        """Mirror installed Database.rollback callback ordering."""
        self.before_commit.reset()
        self.after_commit.reset()
        self.before_rollback.run()
        self.after_rollback.run()

    def _select(self, doctype, filters):
        rows = list(self.docs.get(doctype, {}).values())
        if not filters:
            return rows
        for fieldname, expected in filters.items():
            rows = [row for row in rows if _matches(row.get(fieldname), expected)]
        return rows

    def get_value(
        self,
        doctype,
        filters=None,
        fieldname="name",
        as_dict=False,
        **_kwargs,
    ):
        if isinstance(filters, str):
            row = self.docs.get(doctype, {}).get(filters)
            rows = [row] if row else []
        else:
            rows = self._select(doctype, filters)
        if not rows:
            return None
        row = rows[0]
        if as_dict:
            fields = fieldname if isinstance(fieldname, (list, tuple)) else [fieldname]
            return {field: row.get(field) for field in fields}
        if isinstance(fieldname, (list, tuple)):
            return [row.get(field) for field in fieldname]
        return row.get(fieldname)

    def get_all(
        self,
        doctype,
        filters=None,
        fields=None,
        pluck=None,
        limit=None,
        **_kwargs,
    ):
        rows = self._select(doctype, filters)
        if limit:
            rows = rows[:limit]
        if pluck:
            return [row.get(pluck) for row in rows]
        if fields:
            return [{field: row.get(field) for field in fields} for row in rows]
        return [dict(row) for row in rows]


def _matches(value, expected):
    if isinstance(expected, (list, tuple)) and len(expected) == 2:
        operator, operand = expected
        if operator == "like":
            pattern = re.escape(str(operand)).replace("%", ".*")
            return re.fullmatch(pattern, str(value or "")) is not None
        if operator == "in":
            return value in operand
        if operator == "!=":
            return value != operand
    return value == expected


class FakeSession:
    def __init__(self, user):
        self.user = user


class FakePublicationOwner:
    """Narrow transaction-owner stub with explicit failure signalling.

    It is a test fixture, not a shipped bypass: the app has no owner implementation and
    denies every protected publication without one. ``status_error`` and
    ``notify_error`` model an owner that cannot answer or cannot be notified.
    """

    def __init__(self):
        self.aborted = False
        self.reasons = []
        self.status_error = None
        self.notify_error = None

    def publication_aborted(self):
        if self.status_error is not None:
            raise self.status_error
        return self.aborted

    def abort_publication(self, reason):
        self.reasons.append(reason)
        self.aborted = True
        if self.notify_error is not None:
            raise self.notify_error


class FakeFrappe(types.ModuleType):
    def __init__(self, docs=None, user="author@example.test", site_path=None):
        super().__init__("frappe")
        self.db = FakeDB(docs, site_path=site_path)
        self.session = FakeSession(user)
        self.local = types.SimpleNamespace()
        self.form_dict = {}
        self.permissions = {}
        self.site_path = site_path
        self.PermissionError = type("PermissionError", (Exception,), {})
        self.ValidationError = type("ValidationError", (Exception,), {})

    def get_site_path(self, *parts):
        from pathlib import Path

        base = Path(self.site_path or "/tmp/clinic-photos-fake-site")
        return str(base.joinpath(*parts))

    def has_permission(self, doctype, permission_type="read", doc=None):
        name = getattr(doc, "name", doc)
        return bool(self.permissions.get((doctype, permission_type, name), False))

    def whitelist(self, **_kwargs):
        return lambda function: function


@contextlib.contextmanager
def fake_frappe(docs=None, user="author@example.test", site_path=None):
    module = FakeFrappe(docs=docs, user=user, site_path=site_path)
    previous = sys.modules.get("frappe")
    sys.modules["frappe"] = module
    try:
        yield module
    finally:
        if previous is None:
            sys.modules.pop("frappe", None)
        else:
            sys.modules["frappe"] = previous
