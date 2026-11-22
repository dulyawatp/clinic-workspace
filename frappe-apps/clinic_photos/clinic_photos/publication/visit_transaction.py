"""Unwired visit-only checks; clean queues do not establish fresh-child provenance."""

import contextvars
import os
from collections import deque
from contextlib import contextmanager
from types import MethodType

from clinic_photos.access import frappe_context
from clinic_photos.publication import exclusion

_NATIVE_ROLLBACK_FILE = contextvars.ContextVar("visit_native_rollback_file", default=None)


def _suppress_native_file_rollback(file_doc):
    """Exact admitted File only, during the original guarded rollback call."""
    return _NATIVE_ROLLBACK_FILE.get() is file_doc


class TransactionDenied(RuntimeError):
    pass


class TransactionUncertain(RuntimeError):
    pass


@contextmanager
def _visit_transaction(exclusion_owner):
    """Register this private adapter, independently from visit exclusion evidence."""
    guard = _VisitTransaction(exclusion_owner)
    with frappe_context.owned_publication_transaction(guard):
        guard._start()
        yield guard


class _VisitTransaction:
    def __init__(self, exclusion_owner):
        import frappe

        self._exclusion_owner = exclusion_owner
        self._pid = os.getpid()
        self._site = getattr(frappe.local, "site", None)
        self._actor = getattr(frappe.session, "user", None)
        self._db = getattr(frappe.local, "db", None)
        self._connection = getattr(self._db, "_conn", None)
        self._aborted = False
        self._phase = "prepare"
        self._permitted = False
        self._verified = False
        self._acknowledged = False
        self._witness = None
        self._rollback_attempted = False
        self._rollback_hooks_cleared = False
        self._native_file = None
        self._native_queue = None
        self._native_callback = None
        self._native_settled = False
        self._file_lock = None
        self._file_lock_uncertain = False
        self._visit_completion = None
        self._completion_file = None
        self._visit_publish_acknowledged = False

    def publication_aborted(self):
        return self._aborted

    def abort_publication(self, reason):
        # Never notify whatever owner replaced us. The native scope latches this
        # adapter's status; its own abort path may call us recursively.
        self._aborted = True

    def _identity(self):
        import frappe

        return (os.getpid() == self._pid and self._site and self._actor and
                self._connection is not None and
                getattr(frappe.local, "site", None) == self._site and
                getattr(frappe.session, "user", None) == self._actor and
                getattr(frappe.local, "db", None) is self._db and
                getattr(self._db, "_conn", None) is self._connection and
                exclusion._VISIT_OWNER_CONTEXT.get() is self._exclusion_owner and
                exclusion._OWNERS.get(id(self._exclusion_owner)) is self._exclusion_owner and
                frappe_context.current_publication_owner() is self)

    def _check(self):
        try:
            if (not self._identity() or self._aborted or
                    not frappe_context.publication_transaction_live() or
                    type(self._exclusion_owner) is not exclusion._VisitAnnotationOwner or
                    self._exclusion_owner.site != self._site or
                    self._exclusion_owner.actor != self._actor):
                raise TransactionDenied("visit transaction unavailable")
            exclusion.require_visit_annotation_owner(self._exclusion_owner)
        except BaseException as exc:
            self.abort_publication("visit_transaction_identity")
            raise TransactionDenied("visit transaction unavailable") from exc

    def _queue(self, name):
        pending = getattr(getattr(self._db, name, None), "_functions", None)
        if type(pending) is not deque:
            raise TransactionDenied("unsupported native callback queue")
        return pending

    def _start(self):
        try:
            self._check()
            if (type(getattr(self._db, "transaction_writes", None)) is not int or
                    self._db.transaction_writes != 0):
                raise TransactionDenied("unclean visit transaction")
            self._arm()
        except BaseException:
            self.abort_publication("visit_transaction_setup")
            raise

    def _arm(self):
        for name in ("before_commit", "after_commit", "before_rollback", "after_rollback"):
            if self._queue(name):
                raise TransactionDenied("visit transaction callbacks not clean")
        self._db.before_commit.add(self._before_commit)
        self._rollback_callback = self._on_rollback
        self._db.before_rollback.add(self._rollback_callback)
        self._rollback_hooks_cleared = False
        if self._phase == "publish":
            self._db.after_commit.add(self._ack_callback)

    @staticmethod
    def _same(actual, expected):
        return len(actual) == len(expected) and all(a is b for a, b in zip(actual, expected))

    def _before_commit(self):
        # Native commit clears rollback hooks before invoking before_commit.
        self._rollback_hooks_cleared = True
        try:
            self._check()
            if not self._permitted or self._phase not in ("prepare_ready", "publish_ready"):
                raise TransactionDenied("visit commit not authorized")
            if self._phase == "publish_ready" and self._native_file is not None:
                self._require_completion()
            expected = ([self._witness.committed, self._ack_callback]
                        if self._phase == "prepare_ready" else [self._ack_callback])
            if (self._queue("before_commit") or
                    not self._same(self._queue("after_commit"), expected)):
                raise TransactionDenied("visit commit not authorized")
            self._verified = True
        except BaseException:
            self.abort_publication("visit_transaction_commit_veto")
            self._db.before_commit.add(self._before_commit)
            raise

    def _after_commit(self):
        self._acknowledged = (self._verified and
                              (self._phase != "prepare_ready" or
                               self._exclusion_owner._visit_prepare_phase == "committed"))

    def _on_rollback(self):
        self.abort_publication("visit_transaction_rollback")

    def _prepare_ready(self, handle):
        try:
            self._check()
            if (self._phase != "prepare" or
                    getattr(self._exclusion_owner, "_visit_prepare_handle", None) is not handle):
                raise TransactionDenied("invalid visit preparation witness")
            from clinic_photos.publication import visit_annotation_prepare
            if (type(handle) is not visit_annotation_prepare._PreparationWitness or
                    handle.owner is not self._exclusion_owner or
                    self._exclusion_owner._visit_prepare_handle is not handle or
                    self._exclusion_owner._visit_prepare_phase != "armed" or
                    not self._same(self._queue("after_commit"), [handle.committed]) or
                    not self._same(self._queue("after_rollback"), [handle.rolled_back])):
                raise TransactionDenied("invalid visit preparation witness")
            self._witness = handle
            self._ack_callback = self._after_commit
            self._db.after_commit.add(self._ack_callback)
            self._phase = "prepare_ready"
        except BaseException:
            self.abort_publication("visit_transaction_prepare")
            raise

    def _publish_ready(self):
        self._check()
        if (self._phase != "publish" or
                (self._native_file is not None and not self._native_settled)):
            self.abort_publication("visit_transaction_phase")
            raise TransactionDenied("invalid visit transaction phase")
        if self._native_file is not None:
            try:
                self._require_completion()
                from clinic_photos.publication import visit_file_scope
                if visit_file_scope._completed_file(self) is not self._completion_file:
                    raise TransactionDenied("visit completion unavailable")
            except BaseException as exc:
                self.abort_publication("visit_transaction_completion")
                raise TransactionDenied("visit completion unavailable") from exc
        self._phase = "publish_ready"

    def _stage_completion(self):
        """Only the trusted journal stage may issue the guard's completion."""
        from clinic_photos.publication import visit_annotation_publish, visit_file_scope

        self._check()
        if self._phase != "publish" or self._visit_completion is not None:
            raise TransactionDenied("visit completion unavailable")
        try:
            proof = visit_file_scope._completed_file(self)
            completion = visit_annotation_publish._stage(self)
            if visit_file_scope._completed_file(self) is not proof:
                raise TransactionDenied("visit completion unavailable")
            self._visit_completion, self._completion_file = completion, proof
            self._require_completion()
            return completion
        except BaseException:
            self.abort_publication("visit_transaction_completion")
            raise

    def _require_completion(self):
        from clinic_photos.publication import visit_annotation_publish

        completion, proof = self._visit_completion, self._completion_file
        intent = self._exclusion_owner.intent
        if (type(completion) is not visit_annotation_publish._Completion or proof is None or
                completion.guard is not self or completion.intent is not intent or
                completion.operation != self._exclusion_owner.operation_id or
                completion.file != proof.name or proof.file is not self._native_file or
                not self._native_settled or
                completion.version != f"{intent.managed_draft}-v{intent.expected_version + 1}" or
                completion.creation_identity != f"{proof.device}:{proof.inode}"):
            raise TransactionDenied("visit completion unavailable")

    @staticmethod
    def _is_native_file_rollback(callback, file_doc):
        from clinic_photos.access import file_mixin

        return (type(callback) is MethodType and
                callback.__self__ is file_doc and
                callback.__func__ is file_mixin.ProtectedFileMixin.on_rollback)

    def _begin_native_file_insert(self, file_doc):
        """Bind one guarded File insert to a verified empty native rollback queue."""
        from clinic_photos.access import file_mixin

        try:
            self._check()
            method = getattr(file_doc, "on_rollback", None)
            if (self._phase != "publish" or self._native_file is not None or
                    type(method) is not MethodType or
                    method.__self__ is not file_doc or
                    method.__func__ is not file_mixin.ProtectedFileMixin.on_rollback):
                raise TransactionDenied("visit native file insert unavailable")
            queue = self._queue("after_rollback")
            if queue:
                raise TransactionDenied("visit native file insert unavailable")
            self._native_file = file_doc
            self._native_queue = queue
            self._native_callback = None
            self._native_settled = False
        except BaseException as exc:
            self.abort_publication("visit_native_file_scope")
            raise TransactionDenied("visit native file insert unavailable") from exc

    def _check_cleanup_identity(self):
        """Original identity and exclusion token only; an abort is already latched."""
        try:
            if not self._identity():
                raise TransactionDenied("visit native file insert unavailable")
            exclusion._VisitAnnotationOwner._require_original_scope(self._exclusion_owner)
        except BaseException as exc:
            self.abort_publication("visit_native_file_identity")
            raise TransactionDenied("visit native file insert unavailable") from exc

    def _acquire_visit_file_lock(self, scope):
        """Acquire only the active continued reservation, without native callbacks."""
        from clinic_photos.publication import visit_annotation_prepare, visit_file_scope

        try:
            self._check()
            entry = visit_file_scope._ACTIVE.get()
            values = getattr(self._exclusion_owner, "_visit_prepared_values", None)
            if (self._phase != "publish" or self._file_lock is not None or
                    self._file_lock_uncertain or type(entry) is not tuple or
                    len(entry) != 5 or type(scope) is not visit_file_scope._FileScope or
                    entry[0] is not scope or entry[1] is not self or
                    type(entry[3]) is not visit_annotation_prepare._ContinuedReservation or
                    entry[3].operation != self._exclusion_owner.operation_id or
                    entry[3].intent != self._exclusion_owner.intent or entry[3].generation != 1 or
                    type(values) is not dict or entry[3].key != values.get("reservation_key") or
                    entry[3].digest != values.get("expected_sha256") or
                    entry[3].size != values.get("expected_size")):
                raise TransactionDenied("visit file lock unavailable")
            visit_file_scope._check_file(entry, entry[2])
            name = frappe_context._lock_name(entry[3].key)
            self._file_lock_uncertain = True
            result = self._db.sql("select get_lock(%s, %s)",
                                  (name, frappe_context.LOCK_TIMEOUT_SECONDS))
            if not frappe_context._release_result_confirmed(result):
                raise TransactionDenied("visit file lock unavailable")
            self._file_lock = name
            self._file_lock_uncertain = False
            self._check()
            if visit_file_scope._ACTIVE.get() is not entry:
                raise TransactionDenied("visit file lock unavailable")
            visit_file_scope._check_file(entry, entry[2])
        except BaseException as exc:
            self.abort_publication("visit_file_lock")
            raise TransactionDenied("visit file lock unavailable") from exc

    def _release_visit_file_lock(self):
        if self._file_lock is None or self._file_lock_uncertain:
            return
        self._check_cleanup_identity()
        self._file_lock_uncertain = True
        result = self._db.sql("select release_lock(%s)", (self._file_lock,))
        if not frappe_context._release_result_confirmed(result):
            raise TransactionUncertain("visit file lock release uncertain")
        self._file_lock = None

    def _settle_native_file_insert(self, file_doc, *, succeeded):
        """Admit only one exact appended native callback; never grant commit authority.

        A failed native call may unwind after the owning boundary already latched an
        abort, so failure settlement proves the original identity and exclusion token
        without requiring liveness. Success keeps the live checks.
        """
        try:
            if succeeded:
                self._check()
            else:
                self._check_cleanup_identity()
            if (self._native_file is not file_doc or self._native_settled or
                    self._queue("after_rollback") is not self._native_queue):
                raise TransactionDenied("visit native file insert unavailable")
            current = tuple(self._native_queue)
            if len(current) > 1:
                raise TransactionDenied("visit native file insert unavailable")
            if current:
                callback = current[0]
                if not self._is_native_file_rollback(callback, file_doc):
                    raise TransactionDenied("visit native file insert unavailable")
                self._native_callback = callback
            elif succeeded:
                raise TransactionDenied("visit native file insert unavailable")
            self._native_settled = True
        except BaseException as exc:
            self.abort_publication("visit_native_file_callbacks")
            raise TransactionDenied("visit native file insert unavailable") from exc
        return self._native_callback

    def _commit(self, expected, target):
        self._check()
        if self._phase != expected:
            self.abort_publication("visit_transaction_phase")
            raise TransactionDenied("invalid visit transaction phase")
        self._permitted = True
        try:
            self._db.commit()
            if not self._verified or not self._acknowledged:
                raise TransactionUncertain("visit commit not acknowledged")
            self._check()
            if target == "done" and self._native_file is not None:
                self._visit_publish_acknowledged = True
            if target == "done" and self._native_file is None:
                self._release_visit_file_lock()
            self._phase = target
            self._permitted = self._verified = self._acknowledged = False
            if target == "publish":
                self._ack_callback = self._after_commit
                self._arm()
        except BaseException as exc:
            if self._file_lock is not None:
                self._file_lock_uncertain = True
            self.abort_publication("visit_transaction_commit_uncertain")
            raise TransactionUncertain("visit commit outcome uncertain") from exc

    def _commit_prepare(self):
        self._commit("prepare_ready", "publish")

    def _commit_publish(self):
        self._commit("publish_ready", "done")

    def _rollback(self):
        """One native cleanup attempt, only in the exact original scope."""
        self.abort_publication("visit_transaction_cleanup")
        if self._rollback_attempted or not self._identity():
            raise TransactionUncertain("visit rollback scope unavailable")
        try:
            exclusion._VisitAnnotationOwner._require_original_scope(self._exclusion_owner)
            before = self._queue("before_rollback")
            after = self._queue("after_rollback")
            if self._native_file is not None and not self._native_settled:
                raise TransactionDenied("unsettled native file callbacks")
            if self._rollback_hooks_cleared:
                valid = not before and not after
            else:
                witness = (getattr(self._exclusion_owner, "_visit_prepare_handle", None)
                           if self._phase in ("prepare", "prepare_ready") else None)
                expected_after = []
                if witness is not None:
                    from clinic_photos.publication import visit_annotation_prepare
                    if (type(witness) is not visit_annotation_prepare._PreparationWitness or
                            witness.owner is not self._exclusion_owner):
                        raise TransactionDenied("invalid rollback witness")
                    expected_after = [witness.rolled_back]
                elif self._native_settled:
                    if after is not self._native_queue:
                        raise TransactionDenied("adulterated rollback queue")
                    expected_after = ([] if self._native_callback is None
                                      else [self._native_callback])
                valid = (self._same(before, [self._rollback_callback]) and
                         self._same(after, expected_after))
            if not valid:
                raise TransactionDenied("unreviewed rollback callbacks")
            self._rollback_attempted = True
            token = None
            try:
                if not self._rollback_hooks_cleared and self._native_callback is not None:
                    token = _NATIVE_ROLLBACK_FILE.set(self._native_file)
                self._db.rollback()
            finally:
                if token is not None:
                    _NATIVE_ROLLBACK_FILE.reset(token)
            self._release_visit_file_lock()
        except BaseException as exc:
            raise TransactionUncertain("visit rollback outcome uncertain") from exc
