"""Visit-only preparation and same-owner continuation; no transaction control."""

import hashlib
import json
from collections import namedtuple

from clinic_photos.access import frappe_context
from clinic_photos.publication import exclusion, journal
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication import visit_annotation_submission as submission
from clinic_photos.visit import annotation_authority


class _PreparationWitness(namedtuple("_PreparationWitnessBase", "owner committed rolled_back")):
    """Exact private callbacks registered by this preparation attempt."""

    __slots__ = ()


class _ContinuedReservation(namedtuple(
        "_ContinuedReservationBase", "intent operation generation key digest size")):
    """Verified immutable projection of the exact consumed preparation."""

    __slots__ = ()


def prepare(owner):
    """Stage one reservation in caller's transaction; never commit or publish."""
    import frappe

    exclusion.require_visit_annotation_owner(owner)
    db = getattr(frappe.local, "db", None)
    connection = getattr(db, "_conn", None)
    transaction = frappe_context.current_publication_owner()

    def check_connection():
        if (db is None or getattr(frappe.local, "db", None) is not db or
                getattr(db, "_conn", None) is not connection or
                frappe_context.current_publication_owner() is not transaction):
            raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
        if transaction is not None:
            from clinic_photos.publication import visit_transaction
            if (type(transaction) is not visit_transaction._VisitTransaction or
                    transaction._exclusion_owner is not owner or transaction._db is not db or
                    transaction._connection is not connection or transaction._phase != "prepare"):
                raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
            transaction._check()

    check_connection()
    if owner._visit_prepare_phase != "new":
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    # Burn even failed attempts: savepoint rollback cannot recreate this attempt.
    object.__setattr__(owner, "_visit_prepare_phase", "burned")
    submission._require_context(owner.site, owner.actor)
    row = frappe_context.get_save_operation(owner.operation_id, for_update=True)
    name, intent, projection = submission._verified_row(row, site=owner.site, actor=owner.actor)
    if (name != owner.operation_id or projection != dict(owner.submitted_projection)
            or intent != owner.intent or intent.target_identity != owner.target_identity):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    binding = intent.binding
    resolved = annotation_authority.resolve_write_binding(
        binding.appointment, binding.encounter, binding.slot, expected=binding,
    )
    if resolved != binding:
        raise journal.JournalAuthorityDenied(journal.SAFE_JOURNAL_DENIAL)
    submission._require_live_head(intent)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    material = json.dumps(
        ["clinic-visit-annotation-prepared-v1", owner.site, owner.actor,
         owner.operation_id, intent.target_identity, intent.intent_digest, 1],
        ensure_ascii=True, separators=(",", ":"),
    ).encode("utf-8")
    key = "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json"
    if frappe_context.prepared_key_conflicts(key):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    values = {
        "state": journal.PREPARED_STATE,
        "reservation_key": key,
        "generation": 1,
        "expected_sha256": projection["staged_sha256"],
        "expected_size": projection["staged_size"],
    }
    check_connection()
    db.sql(
        "UPDATE `tabClinic Photo Save Operation` SET `state` = %s, `reservation_key` = %s, "
        "`generation` = %s, `expected_sha256` = %s, `expected_size` = %s "
        "WHERE `name` = %s AND `state` = %s",
        (values["state"], values["reservation_key"], values["generation"],
         values["expected_sha256"], values["expected_size"], owner.operation_id,
         journal.SUBMITTED_STATE),
    )
    check_connection()
    if db._cursor.rowcount != 1:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    updated = frappe_context.get_save_operation(owner.operation_id, for_update=True)
    check_connection()
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    if updated is None:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    updated = dict(updated)
    updated.setdefault("doctype", journal.OPERATION_DOCTYPE)
    expected = {**projection, **values}
    if (set(updated) != set(expected)
            or any(type(updated[k]) is not type(v) or updated[k] != v for k, v in expected.items())):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    # Reauthenticate staged bytes and original submission after the write.
    restored = {**updated}
    for field in values:
        if field in projection:
            restored[field] = projection[field]
        else:
            restored.pop(field)
    if codec.load_submitted(restored, site=owner.site, actor=owner.actor) != intent:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)

    object.__setattr__(owner, "_visit_prepared_values", values.copy())
    object.__setattr__(owner, "_visit_prepare_phase", "armed")

    def committed():
        try:
            exclusion.require_visit_annotation_owner(owner)
            submission._require_context(owner.site, owner.actor)
            if owner._visit_prepare_phase != "armed":
                raise ValueError
            object.__setattr__(owner, "_visit_prepare_phase", "committed")
        except BaseException:
            object.__setattr__(owner, "_visit_prepare_phase", "burned")

    def rolled_back():
        object.__setattr__(owner, "_visit_prepare_phase", "burned")

    handle = _PreparationWitness(owner, committed, rolled_back)
    object.__setattr__(owner, "_visit_prepare_handle", handle)
    try:
        check_connection()
        db.after_rollback.add(rolled_back)
        db.after_commit.add(committed)
    except BaseException:
        object.__setattr__(owner, "_visit_prepare_phase", "burned")
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY) from None
    return handle


def continue_after_commit(owner, handle):
    """Consume this owner's exact committed attempt after fresh locked checks."""
    try:
        exclusion.require_visit_annotation_owner(owner)
    except BaseException:
        if type(owner) is exclusion._VisitAnnotationOwner:
            object.__setattr__(owner, "_visit_prepare_phase", "burned")
        raise
    if (owner._visit_prepare_phase != "committed"
            or handle is not owner._visit_prepare_handle):
        object.__setattr__(owner, "_visit_prepare_phase", "burned")
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    object.__setattr__(owner, "_visit_prepare_phase", "burned")
    submission._require_context(owner.site, owner.actor)
    row = frappe_context.get_save_operation(owner.operation_id, for_update=True)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    if row is None:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    row = dict(row)
    row.setdefault("doctype", journal.OPERATION_DOCTYPE)
    projection = dict(owner.submitted_projection)
    values = owner._visit_prepared_values
    expected = {**projection, **values}
    if (set(row) != set(expected)
            or any(type(row[k]) is not type(v) or row[k] != v for k, v in expected.items())):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    restored = dict(row)
    for field in values:
        if field in projection:
            restored[field] = projection[field]
        else:
            restored.pop(field)
    intent = codec.load_submitted(restored, site=owner.site, actor=owner.actor)
    if intent != owner.intent:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    binding = intent.binding
    if annotation_authority.resolve_write_binding(
            binding.appointment, binding.encounter, binding.slot, expected=binding) != binding:
        raise journal.JournalAuthorityDenied(journal.SAFE_JOURNAL_DENIAL)
    submission._require_live_head(intent)
    exclusion.require_visit_annotation_owner(owner)
    submission._require_context(owner.site, owner.actor)
    return _ContinuedReservation(
        owner.intent, owner.operation_id, row["generation"], row["reservation_key"],
        row["expected_sha256"], row["expected_size"],
    )
