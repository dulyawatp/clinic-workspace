"""Immediate private committed-bundle proof; not a read authorization API."""

import hashlib
from collections.abc import Mapping

from clinic_photos.access import frappe_context
from clinic_photos.publication import journal, visit_annotation_domain, visit_annotation_prepare
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication import visit_annotation_publish, visit_file_aliases
from clinic_photos.publication import visit_file_scope, visit_native_save, visit_transaction
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationIntent


class ReadbackUnknown(RuntimeError):
    """Commit outcome cannot be confirmed; do not replay the operation."""


def _verify(guard, completion, file_proof):
    """Verify same committed bundle under original retained key lock; no commit."""
    try:
        if (type(guard) is not visit_transaction._VisitTransaction or
                type(completion) is not visit_annotation_publish._Completion or
                type(file_proof) is not visit_file_scope._CompletedFile):
            raise ValueError
        entry = visit_file_scope._ACTIVE.get()
        if (type(entry) is not tuple or len(entry) != 5 or
                type(entry[0]) is not visit_file_scope._FileScope or entry[1] is not guard or
                entry[0]._completed is not file_proof or entry[2] is not file_proof.file or
                entry[3] is not file_proof.reservation or entry[4][2][0] is not file_proof):
            raise ValueError
        owner = guard._exclusion_owner
        intent, reservation, doc = owner.intent, file_proof.reservation, file_proof.file
        attempt = entry[4][2][1]
        receipt = attempt[3][3]

        def check():
            guard._check()
            if (guard._phase != "done" or
                    getattr(guard, "_visit_publish_acknowledged", False) is not True or
                    getattr(guard, "_visit_completion", None) is not completion or
                    completion.guard is not guard or guard._exclusion_owner is not owner or
                    owner.intent is not intent or visit_file_scope._ACTIVE.get() is not entry or
                    entry[0]._completed is not file_proof or entry[4][2][0] is not file_proof or
                    entry[4][2][1] is not attempt or attempt[3][3] is not receipt or
                    guard._file_lock_uncertain or
                    guard._file_lock != frappe_context._lock_name(reservation.key)):
                raise ValueError

        check()
        if (type(intent) is not VisitAnnotationIntent or
                type(reservation) is not visit_annotation_prepare._ContinuedReservation or
                attempt[0] is not entry or attempt[3][0] is not entry or
                attempt[3][1] is not doc or attempt[3][2] is not reservation or
                attempt[3][4][0] != file_proof.name or
                reservation.intent is not intent or file_proof.intent is not intent or
                reservation.operation != owner.operation_id or file_proof.operation != owner.operation_id or
                completion.operation != owner.operation_id or completion.intent is not intent or
                intent.binding.site != guard._site or intent.binding.actor != guard._actor or
                completion.file != file_proof.name or doc.name != file_proof.name or
                type(file_proof.device) is not int or file_proof.device < 0 or
                type(file_proof.inode) is not int or file_proof.inode < 0 or
                (file_proof.device, file_proof.inode, file_proof.size, file_proof.sha256) !=
                (receipt.device, receipt.inode, receipt.size, receipt.sha256) or
                file_proof.size != reservation.size or file_proof.sha256 != reservation.digest or
                type(doc.content) is not bytes or doc.content != intent.canonical_visit_annotation.encode("utf-8") or
                len(doc.content) != reservation.size or hashlib.sha256(doc.content).hexdigest() != reservation.digest):
            raise ValueError
        identity = f"{file_proof.device}:{file_proof.inode}"
        number = intent.expected_version + 1
        version = f"{intent.managed_draft}-v{number}"
        if completion.creation_identity != identity or completion.version != version:
            raise ValueError
        visit_file_aliases._verify_single_holder_done(entry, file_proof, completion)
        check()
        expected_rows = (
            ("File", file_proof.name, visit_annotation_domain._FILE_FIELDS, {
                "name": file_proof.name, "file_url": reservation.key,
                "file_name": reservation.key.rsplit("/", 1)[-1], "owner": guard._actor,
                "is_private": 1, "file_size": reservation.size,
                "content_hash": hashlib.md5(doc.content, usedforsecurity=False).hexdigest(),
                "attached_to_doctype": None, "attached_to_name": None, "attached_to_field": None}),
            (visit_annotation_domain._DRAFT, intent.managed_draft, visit_annotation_domain._DRAFT_FIELDS, {
                "name": intent.managed_draft, "author": guard._actor, "owner": guard._actor,
                "photo": intent.binding.original_photo, "current_version_number": number,
                "current_version": version}),
            (visit_annotation_domain._VERSION, version, visit_annotation_domain._VERSION_FIELDS, {
                "name": version, "draft": intent.managed_draft, "version_number": number,
                "annotation_file": file_proof.name, "storage_key": reservation.key, "state": "committed"}),
        )
        for doctype, name, fields, expected in expected_rows:
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
        expected = {
            **dict(owner.submitted_projection), **owner._visit_prepared_values,
            "state": journal.PUBLISHED_STATE, "published_file": file_proof.name,
            "published_domain_doctype": visit_annotation_domain._VERSION,
            "published_domain_name": version, "published_version": number,
            "creation_identity": identity,
        }
        if (set(row) != set(expected) or
                any(type(row[k]) is not type(v) or row[k] != v for k, v in expected.items())):
            raise ValueError
        projected = {k: v for k, v in row.items() if k not in
                     ("annotation_json", "synthetic_patient", "image_base64")}
        if codec.load_published(projected, intent=intent, operation=owner.operation_id,
                                published_file=file_proof.name, creation_identity=identity,
                                committed_bytes=doc.content) != intent:
            raise ValueError
        visit_native_save._verify_persisted_bytes(entry, receipt)
        check()
        return True
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_annotation_readback_unknown")
        raise ReadbackUnknown("visit committed outcome unknown") from exc
