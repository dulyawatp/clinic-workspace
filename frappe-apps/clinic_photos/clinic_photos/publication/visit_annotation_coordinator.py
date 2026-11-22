"""Unwired private coordinator; no route or saved response is authorized here.

The yielded internal acknowledgment keeps the File scope and pins open. Native
readback and verified release are a separate future gate; exiting this context
without that gate does not authorize public activation.
"""

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass

from clinic_photos.access import frappe_context, storage
from clinic_photos.publication import visit_annotation_domain, visit_annotation_prepare
from clinic_photos.publication import visit_file_scope, visit_transaction


@dataclass(frozen=True)
class _Acknowledged:
    guard: object
    completion: object
    file_proof: object


@dataclass(frozen=True)
class _Verified:
    operation: str
    file: str
    version: str


def _publish_verified(owner):
    """Read back while pinned, release once, return only after clean scope exit."""
    from clinic_photos.publication import visit_annotation_readback

    acknowledged = False
    try:
        with _run(owner) as outcome:
            acknowledged = True
            guard, completion, proof = outcome.guard, outcome.completion, outcome.file_proof
            key = guard._exclusion_owner._visit_prepared_values["reservation_key"]
            name = frappe_context._lock_name(key)
            guard._check()
            if (guard._phase != "done" or guard._visit_publish_acknowledged is not True or
                    guard._visit_completion is not completion or guard._completion_file is not proof or
                    guard._file_lock != name or guard._file_lock_uncertain):
                raise ValueError("visit verified release unavailable")
            if visit_annotation_readback._verify(guard, completion, proof) is not True:
                raise ValueError("visit readback not confirmed")
            guard._check()
            if (guard._phase != "done" or guard._visit_completion is not completion or
                    guard._completion_file is not proof or guard._file_lock != name or
                    guard._file_lock_uncertain):
                raise ValueError("visit verified release unavailable")
            guard._release_visit_file_lock()
            guard._check()
            if guard._file_lock is not None:
                raise ValueError("visit verified release unavailable")
            result = _Verified(completion.operation, completion.file, completion.version)
        return result
    except BaseException as exc:
        if acknowledged:
            raise visit_annotation_readback.ReadbackUnknown("visit committed outcome unknown") from exc
        raise


@contextmanager
def _run(owner):
    """Prepare, stage, commit once, and hold scope for future native readback."""
    import frappe

    with visit_transaction._visit_transaction(owner) as guard:
        cleanup_attempted = False
        def cleanup(error):
            nonlocal cleanup_attempted
            if (not isinstance(error, visit_transaction.TransactionUncertain) and
                    guard._phase != "done" and not cleanup_attempted):
                cleanup_attempted = True
                guard._rollback()

        try:
            handle = visit_annotation_prepare.prepare(owner)
            guard._prepare_ready(handle)
            guard._commit_prepare()
            guard._check()
            intent = owner.intent
            values = owner._visit_prepared_values
            key = values["reservation_key"]
            data = intent.canonical_visit_annotation.encode("utf-8")
            if (storage.canonical_private_key(key) != key or
                    values["generation"] != 1 or len(data) != values["expected_size"] or
                    hashlib.sha256(data).hexdigest() != values["expected_sha256"]):
                raise visit_transaction.TransactionDenied("visit coordinator unavailable")
            doc = frappe.get_doc({"doctype": "File", "file_url": key,
                                  "file_name": key.rsplit("/", 1)[-1],
                                  "is_private": 1, "__islocal": 1})
            doc.owner = guard._actor
            doc.content = data
            with visit_file_scope._file_scope(guard, handle, doc):
                try:
                    doc.insert()
                    proof = visit_file_scope._completed_file(guard)
                    visit_annotation_domain._stage(guard)
                    completion = guard._stage_completion()
                    if guard._completion_file is not proof:
                        raise visit_transaction.TransactionDenied("visit coordinator unavailable")
                    guard._publish_ready()
                    guard._commit_publish()
                    yield _Acknowledged(guard, completion, proof)
                except BaseException as exc:
                    cleanup(exc)
                    raise
        except BaseException as exc:
            cleanup(exc)
            raise
