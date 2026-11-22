"""Unwired exact-File state only. This module cannot insert or create bytes."""

import contextvars
import hashlib
import sys
from contextlib import contextmanager
from dataclasses import dataclass

from clinic_photos.publication import visit_transaction


class FileScopeDenied(RuntimeError):
    pass


_ACTIVE = contextvars.ContextVar("visit_file_scope", default=None)


@dataclass(frozen=True)
class _FileEvidence:
    device: int
    inode: int
    size: int
    sha256: str


@dataclass(frozen=True, init=False)
class _CompletedFile:
    operation: str
    intent: str
    reservation: object
    file: object
    name: str
    device: int
    inode: int
    size: int
    sha256: str

    def __init__(self, *args, **kwargs):
        raise FileScopeDenied("visit completed file unavailable")

    @property
    def file_doc(self):
        return self.file

    @property
    def evidence(self):
        return _FileEvidence(self.device, self.inode, self.size, self.sha256)


def _install_completed(entry, attempt, receipt):
    if (_ACTIVE.get() is not entry or attempt[0] is not entry or
            attempt[3] is None or attempt[3][3] is not receipt or
            entry[4][2] is not None or not entry[1]._native_settled or
            not entry[1]._is_native_file_rollback(entry[1]._native_callback, entry[2])):
        raise FileScopeDenied("visit completed file unavailable")
    reservation, doc = entry[3], entry[2]
    if (attempt[3][0] is not entry or attempt[3][1] is not doc or
            attempt[3][2] is not reservation or attempt[3][4][0] != doc.name):
        raise FileScopeDenied("visit completed file unavailable")
    proof = object.__new__(_CompletedFile)
    for field, value in (("operation", reservation.operation), ("intent", reservation.intent),
                         ("reservation", reservation), ("file", doc), ("name", doc.name),
                         ("device", receipt.device), ("inode", receipt.inode),
                         ("size", receipt.size), ("sha256", receipt.sha256)):
        object.__setattr__(proof, field, value)
    entry[0]._completed = proof
    entry[4][2] = (proof, list(attempt))
    attempt[3] = None


def _completed_file(guard):
    """Live publish-only proof; post-commit readback requires a separate future API."""
    from clinic_photos.publication import visit_file_aliases, visit_native_save, writer

    entry = _ACTIVE.get()
    try:
        if (type(guard) is not visit_transaction._VisitTransaction or
                entry is None or entry[1] is not guard or entry[4][2] is None):
            raise FileScopeDenied("visit completed file unavailable")
        guard._check()
        proof, attempt = entry[4][2]
        receipt = attempt[3][3]
        doc, reservation = entry[2], entry[3]
        if (type(proof) is not _CompletedFile or entry[0]._completed is not proof or
                attempt[0] is not entry or attempt[3][0] is not entry or
                attempt[3][1] is not doc or attempt[3][2] is not reservation or
                attempt[3][4][0] != proof.name or proof.file is not doc or
                proof.reservation is not reservation or proof.operation != reservation.operation or
                proof.intent != reservation.intent or guard._phase != "publish" or
                guard.publication_aborted() or guard._native_file is not doc or
                not guard._native_settled or
                not guard._is_native_file_rollback(guard._native_callback, doc) or
                doc.name != proof.name or doc.file_url != reservation.key or
                doc.file_name != reservation.key.rsplit("/", 1)[-1] or
                doc.owner != guard._actor or doc.is_private != 1 or
                any(getattr(doc, field, None) not in (None, "") for field in
                    ("attached_to_doctype", "attached_to_name", "attached_to_field")) or
                type(doc.content) is not bytes or hashlib.sha256(doc.content).hexdigest() != proof.sha256 or
                doc.file_size != proof.size or doc.content_hash != writer._content_hash(doc.content) or
                (proof.device, proof.inode, proof.size, proof.sha256) !=
                (receipt.device, receipt.inode, receipt.size, receipt.sha256)):
            raise FileScopeDenied("visit completed file unavailable")
        visit_file_aliases._verify_single_holder(doc)
        row = visit_file_aliases._locked_named_row(doc)
        expected = {"name": proof.name, "file_url": reservation.key, "file_name": doc.file_name,
                    "owner": guard._actor, "is_private": 1, "file_size": proof.size,
                    "content_hash": doc.content_hash, "attached_to_doctype": None,
                    "attached_to_name": None, "attached_to_field": None}
        if any(row.get(key) != value for key, value in expected.items()):
            raise FileScopeDenied("visit completed file unavailable")
        visit_native_save._verify_persisted_bytes(entry, receipt)
        if _ACTIVE.get() is not entry or entry[4][2][0] is not proof:
            raise FileScopeDenied("visit completed file unavailable")
        return proof
    except BaseException as exc:
        if type(guard) is visit_transaction._VisitTransaction:
            guard.abort_publication("visit_completed_file_refused")
        raise FileScopeDenied("visit completed file unavailable") from exc


@contextmanager
def _file_scope(guard, handle, file_doc):
    from clinic_photos.publication import visit_annotation_prepare

    if type(guard) is not visit_transaction._VisitTransaction:
        raise FileScopeDenied("visit file scope unavailable")
    token = None
    try:
        guard._check()
        if guard._phase != "publish" or _ACTIVE.get() is not None:
            raise FileScopeDenied("visit file scope unavailable")
        owner = guard._exclusion_owner
        reservation = visit_annotation_prepare.continue_after_commit(owner, handle)
        if (type(reservation) is not visit_annotation_prepare._ContinuedReservation
                or reservation.operation != owner.operation_id
                or reservation.intent != owner.intent
                or reservation.generation != 1):
            raise FileScopeDenied("visit file scope unavailable")
        scope = object.__new__(_FileScope)
        scope._completed = None
        state = [False, False, None]
        entry = (scope, guard, file_doc, reservation, state)
        token = _ACTIVE.set(entry)
        _check_file(entry, file_doc)
        yield scope
        if not state[0] and not guard.publication_aborted():
            raise FileScopeDenied("visit file scope unclaimed")
    except BaseException:
        guard.abort_publication("visit_file_scope_refused")
        raise
    finally:
        if token is not None:
            primary = sys.exc_info()[0] is not None
            try:
                if state[2] is not None:
                    from clinic_photos.publication import visit_native_save
                    _, attempt = state[2]
                    state[2] = None
                    visit_native_save._close_attempt_receipt(attempt)
            except BaseException:
                guard.abort_publication("visit_completed_file_close_failed")
                if not primary:
                    raise
            finally:
                _ACTIVE.reset(token)


def _check_file(entry, file_doc):
    scope, guard, expected_file, reservation, state = entry
    guard._check()
    data = getattr(file_doc, "content", None)
    key = reservation.key
    if (guard._phase != "publish" or file_doc is not expected_file
            or not callable(getattr(file_doc, "is_new", None)) or not file_doc.is_new()
            or getattr(file_doc, "name", None)
            or getattr(file_doc, "file_url", None) != key
            or getattr(file_doc, "file_name", None) != key.rsplit("/", 1)[-1]
            or getattr(file_doc, "is_private", None) != 1
            or getattr(file_doc, "owner", None) != guard._actor
            or any(getattr(file_doc, field, None) not in (None, "") for field in
                   ("attached_to_doctype", "attached_to_name", "attached_to_field"))
            or type(data) is not bytes or not data or len(data) != reservation.size
            or hashlib.sha256(data).hexdigest() != reservation.digest):
        raise FileScopeDenied("visit file scope unavailable")
    return data


def _check_insert_modes(file_doc):
    flags = getattr(file_doc, "flags", None)
    if (flags is None or getattr(file_doc, "decode", None) or
            any(getattr(flags, field, None) for field in (
                "ignore_permissions", "ignore_links", "ignore_mandatory",
                "ignore_duplicate_entry_error", "ignore_validate",
                "ignore_validate_update_after_submit", "in_insert",
                "in_print", "copy_from_existing_file"))):
        raise FileScopeDenied("visit file insert unavailable")


def _before_native_insert(file_doc, args, kwargs):
    """Refuse unsafe insertion before native create permission; never grant access."""
    entry = _ACTIVE.get()
    if entry is None:
        return False
    _, guard, _, _, state = entry
    try:
        _check_file(entry, file_doc)
        _check_insert_modes(file_doc)
        if args or kwargs or state[1]:
            raise FileScopeDenied("visit file insert unavailable")
        state[1] = True
        guard._begin_native_file_insert(file_doc)
        return entry
    except BaseException as exc:
        guard.abort_publication("visit_file_insert_refused")
        raise FileScopeDenied("visit file insert unavailable") from exc


def _entry_guard(entry):
    if (type(entry) is not tuple or len(entry) != 5 or
            type(entry[1]) is not visit_transaction._VisitTransaction):
        return None
    return entry[1]


def _native_insert_failed(file_doc, entry):
    """Settle a failed native insert without masking its own exception."""
    guard = _entry_guard(entry)
    if guard is None:
        return
    if _ACTIVE.get() is not entry or entry[2] is not file_doc:
        guard.abort_publication("visit_file_native_scope_lost")
        return
    try:
        guard._settle_native_file_insert(file_doc, succeeded=False)
    except BaseException:
        # Refusal stays sticky: the insert remains unsettled and cleanup refuses.
        pass
    finally:
        guard.abort_publication("visit_file_native_insert_failed")


def _native_insert_persisted(file_doc, entry):
    """Require the exact scope captured at entry before admitting success."""
    guard = _entry_guard(entry)
    if guard is None:
        raise FileScopeDenied("visit file insert unavailable")
    if _ACTIVE.get() is not entry or entry[2] is not file_doc:
        guard.abort_publication("visit_file_native_scope_lost")
        raise FileScopeDenied("visit file insert unavailable")
    try:
        guard._settle_native_file_insert(file_doc, succeeded=True)
    except BaseException as exc:
        raise FileScopeDenied("visit file insert unavailable") from exc


class _FileScope:
    __slots__ = ("_completed",)

    def __init__(self, *args, **kwargs):
        raise FileScopeDenied("visit file scope unavailable")

    def _claim(self, file_doc, *, content=None, decode=False, overwrite=False,
               ignore_existing_file_check=False):
        entry = _ACTIVE.get()
        if entry is None or entry[0] is not self:
            raise FileScopeDenied("visit file scope unavailable")
        _, guard, _, _, state = entry
        try:
            if state[0] or decode or overwrite or ignore_existing_file_check:
                raise FileScopeDenied("visit file scope unavailable")
            data = _check_file(entry, file_doc)
            if content is not None and (type(content) is not bytes or content != data):
                raise FileScopeDenied("visit file scope unavailable")
            state[0] = True
            return data
        except BaseException as exc:
            guard.abort_publication("visit_file_claim_refused")
            raise FileScopeDenied("visit file scope unavailable") from exc
