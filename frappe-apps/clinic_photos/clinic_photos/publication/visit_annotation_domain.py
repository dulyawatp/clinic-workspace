"""Unwired, transaction-bound visit annotation append.

Receipt issuance belongs to the verified native File bridge. This module does
not mint receipts, commit, roll back, or authorize a caller.
"""

import hashlib
from collections.abc import Mapping

from clinic_photos.access import frappe_context
from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES
from clinic_photos.publication import visit_file_scope, visit_transaction
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationIntent

_DRAFT = "Clinic Photo Draft"
_VERSION = "Clinic Photo Annotation Version"
_FILE_FIELDS = ("name", "file_url", "file_name", "owner", "is_private", "file_size",
                "content_hash", "attached_to_doctype", "attached_to_name", "attached_to_field")
_DRAFT_FIELDS = ("name", "author", "owner", "photo", "current_version_number", "current_version")
_VERSION_FIELDS = ("name", "draft", "version_number", "annotation_file", "storage_key", "state")


class DomainDenied(RuntimeError):
    pass


def _scope(guard):
    from clinic_photos.publication import visit_annotation_prepare

    if type(guard) is not visit_transaction._VisitTransaction:
        raise DomainDenied("visit domain unavailable")
    receipt = visit_file_scope._completed_file(guard)
    entry = visit_file_scope._ACTIVE.get()
    file_doc = receipt.file_doc
    if (type(entry) is not tuple or len(entry) != 5 or
            entry[1] is not guard or entry[2] is not file_doc or
            receipt.reservation is not entry[3]):
        raise DomainDenied("visit domain unavailable")
    guard._check()
    reservation, owner = entry[3], guard._exclusion_owner
    intent = owner.intent
    values = getattr(owner, "_visit_prepared_values", None)
    if (guard._phase != "publish" or type(intent) is not VisitAnnotationIntent or
            type(reservation) is not visit_annotation_prepare._ContinuedReservation or
            reservation.intent != intent or reservation.operation != owner.operation_id or
            reservation.generation != 1 or type(values) is not dict or
            values.get("reservation_key") != reservation.key or
            values.get("expected_sha256") != reservation.digest or
            values.get("expected_size") != reservation.size or
            receipt.operation != owner.operation_id or
            receipt.name != getattr(file_doc, "name", None) or
            type(receipt.name) is not str or not receipt.name or
            guard._native_file is not file_doc or not guard._native_settled or
            guard._native_callback is None or
            guard._queue("after_rollback") is not guard._native_queue or
            not guard._same(guard._native_queue, [guard._native_callback]) or
            not guard._is_native_file_rollback(guard._native_callback, file_doc) or
            guard._file_lock_uncertain or
            guard._file_lock != frappe_context._lock_name(reservation.key) or
            intent.binding.site != guard._site or intent.binding.actor != guard._actor or
            guard._actor in NON_CLINICAL_SESSION_IDENTITIES):
        raise DomainDenied("visit domain unavailable")
    data = getattr(file_doc, "content", None)
    if (type(data) is not bytes or data != intent.canonical_visit_annotation.encode("utf-8") or
            len(data) != reservation.size or hashlib.sha256(data).hexdigest() != reservation.digest):
        raise DomainDenied("visit domain unavailable")
    if (receipt.evidence.size != reservation.size or
            receipt.evidence.sha256 != reservation.digest):
        raise DomainDenied("visit domain unavailable")
    return guard, intent, reservation, file_doc, receipt.name


def _row(db, doctype, name, fields):
    row = db.get_value(doctype, name, list(fields), as_dict=True, for_update=True)
    if row is not None and not isinstance(row, Mapping):
        raise DomainDenied("visit domain unavailable")
    return row


def _stage(guard):
    """Stage only; any refusal latches abort and leaves rollback to guard."""
    import frappe

    try:
        guard, intent, reservation, file_doc, name = _scope(guard)
        def check():
            current = _scope(guard)
            if (current[0] is not guard or current[1] is not intent or
                    current[2] is not reservation or current[3] is not file_doc or
                    current[4] != name):
                raise DomainDenied("visit domain unavailable")
        if getattr(guard, "_domain_stage_started", False):
            raise DomainDenied("visit domain unavailable")
        guard._domain_stage_started = True
        db = guard._db
        expected_file = {
            "name": name, "file_url": reservation.key,
            "file_name": reservation.key.rsplit("/", 1)[-1], "owner": guard._actor,
            "is_private": 1, "file_size": reservation.size,
            "content_hash": hashlib.md5(file_doc.content, usedforsecurity=False).hexdigest(),
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        }
        file_row = _row(db, "File", name, _FILE_FIELDS)
        check()
        if (file_row is None or any(file_row.get(k) != v for k, v in expected_file.items()) or
                type(file_row.get("is_private")) is not int or
                type(file_row.get("file_size")) is not int):
            raise DomainDenied("visit domain unavailable")
        draft_name = intent.managed_draft
        draft = _row(db, _DRAFT, draft_name, _DRAFT_FIELDS)
        check()
        if draft is None:
            if intent.expected_version != 0:
                raise DomainDenied("visit domain unavailable")
            draft = dict(name=draft_name, author=guard._actor, owner=guard._actor,
                         photo=intent.binding.original_photo, current_version_number=0,
                         current_version=None)
            frappe.get_doc({"doctype": _DRAFT, **draft}).db_insert(ignore_if_duplicate=False)
            check()
            draft = _row(db, _DRAFT, draft_name, _DRAFT_FIELDS)
        if (draft is None or draft.get("name") != draft_name or
                draft.get("author") != guard._actor or draft.get("owner") != guard._actor or
                draft.get("photo") != intent.binding.original_photo or
                type(draft.get("current_version_number")) is not int or
                draft["current_version_number"] != intent.expected_version):
            raise DomainDenied("visit domain unavailable")
        old_pointer = draft.get("current_version")
        if intent.expected_version == 0:
            if old_pointer not in (None, ""):
                raise DomainDenied("visit domain unavailable")
        else:
            if type(old_pointer) is not str or not old_pointer:
                raise DomainDenied("visit domain unavailable")
            head = _row(db, _VERSION, old_pointer, _VERSION_FIELDS)
            check()
            if (head is None or head.get("name") != old_pointer or
                    head.get("draft") != draft_name or
                    type(head.get("version_number")) is not int or
                    head["version_number"] != intent.expected_version or
                    head.get("state") != "committed"):
                raise DomainDenied("visit domain unavailable")
        number = intent.expected_version + 1
        version_name = f"{draft_name}-v{number}"
        version = dict(name=version_name, draft=draft_name, version_number=number,
                       annotation_file=name, storage_key=reservation.key, state="committed")
        check()
        frappe.get_doc({"doctype": _VERSION, "owner": guard._actor, **version}).db_insert(
            ignore_if_duplicate=False)
        check()
        stored = _row(db, _VERSION, version_name, _VERSION_FIELDS)
        if stored is None or any(stored.get(k) != v for k, v in version.items()):
            raise DomainDenied("visit domain unavailable")
        check()
        db.sql("UPDATE `tabClinic Photo Draft` SET current_version_number = %s, "
               "current_version = %s WHERE name = %s AND current_version_number = %s "
               "AND current_version <=> %s",
               (number, version_name, draft_name, intent.expected_version, old_pointer))
        if db._cursor.rowcount != 1:
            raise DomainDenied("visit domain unavailable")
        check()
        final = _row(db, _DRAFT, draft_name, _DRAFT_FIELDS)
        check()
        if (final is None or any(final.get(k) != v for k, v in {
                "name": draft_name, "author": guard._actor, "owner": guard._actor,
                "photo": intent.binding.original_photo, "current_version_number": number,
                "current_version": version_name}.items())):
            raise DomainDenied("visit domain unavailable")
        return version_name
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_annotation_domain")
        raise DomainDenied("visit domain unavailable") from exc
