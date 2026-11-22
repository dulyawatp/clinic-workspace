"""Private transaction-bound PUBLISHED metadata staging; never commits."""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from clinic_photos.access import frappe_context
from clinic_photos.publication import journal, visit_annotation_domain, visit_annotation_prepare
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication import visit_file_scope, visit_transaction
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationIntent


class PublishDenied(RuntimeError):
    pass


@dataclass(frozen=True, init=False)
class _Completion:
    guard: object
    intent: object
    operation: str
    file: str
    version: str
    creation_identity: str

    def __init__(self, *args, **kwargs):
        raise PublishDenied("visit publication unavailable")


def _stage(guard):
    """Stage exact outcome on original locked operation; leave commit to owner."""
    try:
        if type(guard) is not visit_transaction._VisitTransaction:
            raise ValueError
        proof = visit_file_scope._completed_file(guard)
        owner = guard._exclusion_owner
        intent, reservation, doc = owner.intent, proof.reservation, proof.file

        def check():
            guard._check()
            current = visit_file_scope._completed_file(guard)
            if (current is not proof or guard._phase != "publish" or
                    guard.publication_aborted() or guard._exclusion_owner is not owner or
                    owner.intent is not intent or guard._domain_stage_started is not True):
                raise ValueError

        if (type(intent) is not VisitAnnotationIntent or
                type(reservation) is not visit_annotation_prepare._ContinuedReservation or
                reservation.intent != intent or proof.intent != intent or
                reservation.operation != owner.operation_id or proof.operation != owner.operation_id or
                reservation.generation != 1 or intent.binding.site != guard._site or
                intent.binding.actor != guard._actor or proof.name != doc.name or
                type(proof.device) is not int or proof.device < 0 or
                type(proof.inode) is not int or proof.inode < 0 or
                type(doc.content) is not bytes or
                doc.content != intent.canonical_visit_annotation.encode("utf-8") or
                proof.size != reservation.size or proof.sha256 != reservation.digest or
                len(doc.content) != reservation.size or
                hashlib.sha256(doc.content).hexdigest() != reservation.digest or
                getattr(guard, "_visit_published_stage_started", False)):
            raise ValueError
        guard._visit_published_stage_started = True
        check()
        number = intent.expected_version + 1
        version = f"{intent.managed_draft}-v{number}"
        file_expected = {
            "name": proof.name, "file_url": reservation.key,
            "file_name": reservation.key.rsplit("/", 1)[-1], "owner": guard._actor,
            "is_private": 1, "file_size": reservation.size,
            "content_hash": hashlib.md5(doc.content, usedforsecurity=False).hexdigest(),
            "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None,
        }
        draft_expected = {
            "name": intent.managed_draft, "author": guard._actor, "owner": guard._actor,
            "photo": intent.binding.original_photo, "current_version_number": number,
            "current_version": version,
        }
        version_expected = {
            "name": version, "draft": intent.managed_draft, "version_number": number,
            "annotation_file": proof.name, "storage_key": reservation.key, "state": "committed",
        }
        for doctype, name, fields, expected in (
            ("File", proof.name, visit_annotation_domain._FILE_FIELDS, file_expected),
            (visit_annotation_domain._DRAFT, intent.managed_draft, visit_annotation_domain._DRAFT_FIELDS, draft_expected),
            (visit_annotation_domain._VERSION, version, visit_annotation_domain._VERSION_FIELDS, version_expected),
        ):
            row = visit_annotation_domain._row(guard._db, doctype, name, fields)
            check()
            if row is None or any(type(row.get(k)) is not type(v) or row.get(k) != v
                                  for k, v in expected.items()):
                raise ValueError
        row = frappe_context.get_save_operation(owner.operation_id, for_update=True)
        check()
        if not isinstance(row, Mapping):
            raise ValueError
        row = dict(row)
        row.setdefault("doctype", journal.OPERATION_DOCTYPE)
        projection = dict(owner.submitted_projection)
        prepared = owner._visit_prepared_values
        if (type(prepared) is not dict or prepared != {
                "state": journal.PREPARED_STATE, "reservation_key": reservation.key,
                "generation": 1, "expected_sha256": reservation.digest,
                "expected_size": reservation.size}):
            raise ValueError
        expected = {**projection, **prepared}
        if (set(row) != set(expected) or
                any(type(row[k]) is not type(v) or row[k] != v for k, v in expected.items())):
            raise ValueError
        if (any(journal._row_present(row, field) for field in
                ("published_file", "published_domain_doctype", "published_domain_name", "creation_identity"))
                or not journal._row_absent_int(row, "published_version")):
            raise ValueError
        restored = dict(row)
        for field in prepared:
            if field in projection:
                restored[field] = projection[field]
            else:
                restored.pop(field)
        if codec.load_submitted(restored, site=guard._site, actor=guard._actor) != intent:
            raise ValueError
        check()
        identity = f"{proof.device}:{proof.inode}"
        values = {
            "state": journal.PUBLISHED_STATE, "published_file": proof.name,
            "published_domain_doctype": visit_annotation_domain._VERSION,
            "published_domain_name": version, "published_version": number,
            "creation_identity": identity,
        }
        guard._db.sql(
            "UPDATE `tabClinic Photo Save Operation` SET `state` = %s, `published_file` = %s, "
            "`published_domain_doctype` = %s, `published_domain_name` = %s, "
            "`published_version` = %s, `creation_identity` = %s "
            "WHERE `name` = %s AND `state` = %s",
            (values["state"], values["published_file"], values["published_domain_doctype"],
             values["published_domain_name"], values["published_version"],
             values["creation_identity"], owner.operation_id, journal.PREPARED_STATE),
        )
        check()
        if guard._db._cursor.rowcount != 1:
            raise ValueError
        updated = frappe_context.get_save_operation(owner.operation_id, for_update=True)
        check()
        if not isinstance(updated, Mapping):
            raise ValueError
        updated = dict(updated)
        updated.setdefault("doctype", journal.OPERATION_DOCTYPE)
        expected = {**row, **values}
        if (set(updated) != set(expected) or
                any(type(updated[k]) is not type(v) or updated[k] != v for k, v in expected.items())):
            raise ValueError
        # Empty payload columns may be present in the native projection, but never
        # pass them to the strict metadata-only decoder.
        published = {k: v for k, v in updated.items() if k not in
                     ("annotation_json", "synthetic_patient", "image_base64")}
        if codec.load_published(
                published, intent=intent, operation=owner.operation_id,
                published_file=proof.name, creation_identity=identity,
                committed_bytes=doc.content) != intent:
            raise ValueError
        check()
        completion = object.__new__(_Completion)
        for field, value in (("guard", guard), ("intent", intent), ("operation", owner.operation_id),
                             ("file", proof.name), ("version", version), ("creation_identity", identity)):
            object.__setattr__(completion, field, value)
        return completion
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_annotation_publish")
        raise PublishDenied("visit publication unavailable") from exc
