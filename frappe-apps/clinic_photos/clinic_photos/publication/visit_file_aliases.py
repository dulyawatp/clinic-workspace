"""Current File namespace proof for the exact locked visit reservation.

The full primary-key scan takes broad InnoDB next-key locks. This disposable
gate intentionally caps its dataset budget; a full last page fails closed.
"""

import hashlib

from clinic_photos.access import frappe_context, storage
from clinic_photos.publication import visit_file_scope, visit_transaction

_PAGE_SIZE = 100
_MAX_PAGES = 100


class AliasProofDenied(RuntimeError):
    pass


def _scope(entry, file_doc, *, completion=None):
    from clinic_photos.publication import visit_annotation_prepare

    if (type(entry) is not tuple or len(entry) != 5 or
            type(entry[0]) is not visit_file_scope._FileScope or
            type(entry[1]) is not visit_transaction._VisitTransaction or
            entry[2] is not file_doc or visit_file_scope._ACTIVE.get() is not entry):
        raise AliasProofDenied("visit alias proof unavailable")
    guard, reservation = entry[1], entry[3]
    guard._check()
    values = getattr(guard._exclusion_owner, "_visit_prepared_values", None)
    if completion is None:
        phase_ok = guard._phase == "publish"
    else:
        from clinic_photos.publication import visit_annotation_publish
        phase_ok = (guard._phase == "done" and
                    type(completion) is visit_annotation_publish._Completion and
                    getattr(guard, "_visit_completion", None) is completion and
                    completion.guard is guard and
                    getattr(guard, "_visit_publish_acknowledged", False) is True)
    if (not phase_ok or
            type(reservation) is not visit_annotation_prepare._ContinuedReservation or
            reservation.operation != guard._exclusion_owner.operation_id or
            reservation.intent != guard._exclusion_owner.intent or reservation.generation != 1 or
            type(values) is not dict or reservation.key != values.get("reservation_key") or
            reservation.digest != values.get("expected_sha256") or
            reservation.size != values.get("expected_size") or
            storage.canonical_private_key(reservation.key) != reservation.key or
            guard._file_lock_uncertain or
            guard._file_lock != frappe_context._lock_name(reservation.key) or
            getattr(file_doc, "file_url", None) != reservation.key or
            getattr(file_doc, "file_name", None) != reservation.key.rsplit("/", 1)[-1] or
            getattr(file_doc, "owner", None) != guard._actor or
            getattr(file_doc, "is_private", None) != 1):
        raise AliasProofDenied("visit alias proof unavailable")
    data = getattr(file_doc, "content", None)
    if (type(data) is not bytes or len(data) != reservation.size or
            hashlib.sha256(data).hexdigest() != reservation.digest):
        raise AliasProofDenied("visit alias proof unavailable")
    return guard, reservation


def _verify_no_holders(file_doc):
    _verify(file_doc, post=False)


def _verify_single_holder(file_doc):
    _verify(file_doc, post=True)


def _verify_single_holder_done(entry, proof, completion):
    """Repeat singleton current-read proof inside acknowledged original scope."""
    try:
        if (visit_file_scope._ACTIVE.get() is not entry or type(entry) is not tuple or
                len(entry) != 5 or type(proof) is not visit_file_scope._CompletedFile or
                entry[4][2][0] is not proof or entry[0]._completed is not proof or
                entry[2] is not proof.file or entry[3] is not proof.reservation):
            raise AliasProofDenied("visit alias proof unavailable")
        _verify(proof.file, post=True, completion=completion)
    except BaseException as exc:
        if type(entry) is tuple and len(entry) == 5 and type(entry[1]) is visit_transaction._VisitTransaction:
            entry[1].abort_publication("visit_alias_proof")
        raise AliasProofDenied("visit alias proof unavailable") from exc


def _locked_named_row(file_doc):
    """Read the exact File row on the original locked transaction connection."""
    entry = visit_file_scope._ACTIVE.get()
    guard = entry[1] if type(entry) is tuple and len(entry) == 5 else None
    try:
        guard, _ = _scope(entry, file_doc)
        name = getattr(file_doc, "name", None)
        if type(name) is not str or not name:
            raise AliasProofDenied("visit row proof unavailable")
        fields = ("name", *frappe_context.FILE_IDENTITY_FIELDS)
        projection = ", ".join(f"`{field}`" for field in fields)
        rows = guard._db.sql(
            f"SELECT {projection} FROM `tabFile` WHERE name = %s LOCK IN SHARE MODE",
            (name,),
        )
        _scope(entry, file_doc)
        if (getattr(file_doc, "name", None) != name or
                type(rows) not in (list, tuple) or len(rows) != 1 or
                type(rows[0]) not in (list, tuple) or len(rows[0]) != len(fields) or
                rows[0][0] != name):
            raise AliasProofDenied("visit row proof unavailable")
        return dict(zip(fields, rows[0]))
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_locked_row_proof")
        raise AliasProofDenied("visit row proof unavailable") from exc


def _verify(file_doc, *, post, completion=None):
    entry = visit_file_scope._ACTIVE.get()
    guard = entry[1] if type(entry) is tuple and len(entry) == 5 else None
    try:
        guard, reservation = _scope(entry, file_doc, completion=completion)
        expected_name = getattr(file_doc, "name", None)
        if post and (type(expected_name) is not str or not expected_name):
            raise AliasProofDenied("visit alias proof unavailable")
        result = guard._db.sql("select @@session.innodb_snapshot_isolation")
        _scope(entry, file_doc, completion=completion)
        if (type(result) not in (list, tuple) or len(result) != 1 or
                type(result[0]) not in (list, tuple) or len(result[0]) != 1 or
                type(result[0][0]) is not int or result[0][0] != 0):
            raise AliasProofDenied("visit alias proof unavailable")
        seen = set()
        holders = []
        last = None
        for _ in range(_MAX_PAGES):
            where = "" if last is None else " WHERE name > %s"
            params = (_PAGE_SIZE,) if last is None else (last, _PAGE_SIZE)
            rows = guard._db.sql("SELECT name, file_url FROM `tabFile`" + where +
                                 " ORDER BY name LIMIT %s LOCK IN SHARE MODE", params)
            _scope(entry, file_doc, completion=completion)
            if type(rows) not in (list, tuple) or len(rows) > _PAGE_SIZE:
                raise AliasProofDenied("visit alias proof unavailable")
            for row in rows:
                if (type(row) not in (list, tuple) or len(row) != 2 or
                        type(row[0]) is not str or not row[0] or row[0] in seen or
                        (row[1] is not None and type(row[1]) is not str)):
                    raise AliasProofDenied("visit alias proof unavailable")
                name, raw = row
                seen.add(name)
                last = name
                if not raw:
                    continue
                try:
                    key = storage.canonical_private_key(raw)
                except storage.InvalidStoragePath:
                    continue
                if key == reservation.key:
                    holders.append(name)
            if len(rows) < _PAGE_SIZE:
                _scope(entry, file_doc, completion=completion)
                if (getattr(file_doc, "name", None) != expected_name or
                        holders != ([expected_name] if post else [])):
                    raise AliasProofDenied("visit alias proof unavailable")
                return
        raise AliasProofDenied("visit alias proof budget exhausted")
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_alias_proof")
        raise AliasProofDenied("visit alias proof unavailable") from exc
