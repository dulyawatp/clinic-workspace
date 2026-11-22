"""Server-only SUBMITTED insertion; caller owns transaction and reconciliation."""

import re
from collections.abc import Mapping

from clinic_photos.access import frappe_context
from clinic_photos.publication import journal, staging
from clinic_photos.publication import visit_annotation_insert_guard as guard
from clinic_photos.publication import visit_annotation_journal_codec as codec
from clinic_photos.publication.visit_annotation_intent import (
    VISIT_ANNOTATION_APPEND,
    VisitAnnotationIntent,
)


class SubmissionUncertain(journal.JournalError):
    """Rollback and reconcile the original identity in a fresh transaction."""

    def __init__(self):
        super().__init__("Durable photo-save submission outcome is uncertain")


class StaleVisitAnnotation(journal.JournalConflict):
    """Live draft or head does not match the requested visit annotation."""

    def __init__(self):
        super().__init__("Visit photo annotation is stale")


class PendingVisitAnnotation(journal.JournalConflict):
    """No new submission was staged because this target has unresolved work."""

    def __init__(self):
        super().__init__("Visit photo annotation submission is unavailable")


def _pending_rows(intent):
    """Current locking read; never wait while holding encounter/visit locks."""
    import frappe

    try:
        rows = frappe.db.sql(
            """select name, site, actor, kind, request_key, identity_digest,
                      payload_digest, state, draft, expected_version, original,
                      synthetic_patient, annotation_json, visit_binding_json,
                      staged_key, staged_sha256, staged_size, staged_format, image_base64,
                      reservation_key, generation, expected_sha256, expected_size,
                      published_file, published_domain_doctype, published_domain_name,
                      published_version, creation_identity
               from `tabClinic Photo Save Operation`
               where site = %s and actor = %s and draft = %s and kind = %s
                 and state in ('SUBMITTED', 'PREPARED')
               order by name limit 2 for update nowait""",
            (intent.binding.site, intent.binding.actor, intent.managed_draft,
             VISIT_ANNOTATION_APPEND), as_dict=True,
        )
    except Exception as exc:
        timeout = getattr(frappe, "QueryTimeoutError", None)
        driver_error = (
            exc.__cause__ if isinstance(timeout, type) and isinstance(exc, timeout) else exc
        )
        if (isinstance(driver_error, Exception) and driver_error.args
                and type(driver_error.args[0]) is int and driver_error.args[0] in (1205, 3572)):
            raise PendingVisitAnnotation() from None
        raise
    if not isinstance(rows, (list, tuple)) or len(rows) > 1:
        raise PendingVisitAnnotation()
    return rows


def _require_context(site, actor):
    import frappe

    if (getattr(frappe.local, "site", None) != site
            or getattr(frappe.session, "user", None) != actor):
        raise journal.JournalAuthorityDenied(journal.SAFE_JOURNAL_DENIAL)


def _verified_row(row, *, site, actor):
    _require_context(site, actor)
    if not isinstance(row, Mapping):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    projection = dict(row)
    if "doctype" in projection and projection["doctype"] != journal.OPERATION_DOCTYPE:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    projection["doctype"] = journal.OPERATION_DOCTYPE
    name = projection.get("name")
    if not isinstance(name, str) or re.fullmatch(r"[0-9a-f]{32}", name) is None:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    intent = codec.load_submitted(projection, site=site, actor=actor)
    _require_context(site, actor)
    return name, intent, projection


def _require_live_head(intent):
    import frappe

    name = intent.managed_draft
    row = frappe.db.get_value(
        "Clinic Photo Draft", name,
        ["name", "author", "photo", "current_version_number", "current_version"],
        as_dict=True, for_update=True,
    )
    if row is None:
        if intent.expected_version != 0:
            raise StaleVisitAnnotation()
        return
    number = row.get("current_version_number")
    pointer = row.get("current_version")
    if (row.get("name") != name or row.get("author") != intent.binding.actor
            or row.get("photo") != intent.binding.original_photo
            or type(number) is not int or number < 0):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    if number == 0:
        if pointer not in (None, ""):
            raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    else:
        if not isinstance(pointer, str) or not pointer:
            raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
        version = frappe.db.get_value(
            "Clinic Photo Annotation Version", pointer,
            ["name", "draft", "version_number"], as_dict=True, for_update=True,
        )
        if (not version or version.get("name") != pointer or version.get("draft") != name
                or type(version.get("version_number")) is not int
                or version.get("version_number") != number):
            raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    if number != intent.expected_version:
        raise StaleVisitAnnotation()


def stage_visit_annotation_submission(intent: VisitAnnotationIntent) -> str:
    """Insert or verify SUBMITTED metadata; notify only after a new insert commits."""
    import frappe
    from clinic_photos.visit import annotation_authority

    if type(intent) is not VisitAnnotationIntent:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    binding = intent.binding
    _require_context(binding.site, binding.actor)
    resolved = annotation_authority.resolve_write_binding(
        binding.appointment, binding.encounter, binding.slot, expected=binding,
    )
    if resolved != binding:
        raise journal.JournalAuthorityDenied(journal.SAFE_JOURNAL_DENIAL)
    _require_context(binding.site, binding.actor)
    identity = journal.identity_digest(
        binding.site, binding.actor, VISIT_ANNOTATION_APPEND, intent.request_key,
    )
    pending = _pending_rows(intent)
    _require_context(binding.site, binding.actor)
    if pending:
        row = pending[0]
        if (not isinstance(row, Mapping) or row.get("site") != binding.site or
                row.get("actor") != binding.actor or row.get("draft") != intent.managed_draft or
                row.get("kind") != VISIT_ANNOTATION_APPEND or
                row.get("request_key") != intent.request_key or
                row.get("identity_digest") != identity):
            raise PendingVisitAnnotation()
        name, stored, _ = _verified_row(row, site=binding.site, actor=binding.actor)
        if stored != intent:
            raise journal.JournalConflict()
        return name
    existing = frappe_context.get_save_operation_by_identity(identity)
    _require_context(binding.site, binding.actor)
    if existing is not None:
        name, stored, row = _verified_row(existing, site=binding.site, actor=binding.actor)
        if row["identity_digest"] != identity:
            raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
        if stored != intent:
            raise journal.JournalConflict()
        return name

    _require_live_head(intent)
    _require_context(binding.site, binding.actor)
    staged = staging.stage_visit_annotation(intent)
    values = codec.submitted_values(intent, staged)
    doc = frappe.get_doc(values)
    try:
        with guard._insertion_scope(doc, values):
            doc.insert(ignore_permissions=True)
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        raise SubmissionUncertain() from None

    name = getattr(doc, "name", None)
    if not isinstance(name, str) or re.fullmatch(r"[0-9a-f]{32}", name) is None:
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    persisted = frappe_context.get_save_operation(name)
    stored_name, stored, row = _verified_row(persisted, site=binding.site, actor=binding.actor)
    if (stored_name != name or stored != intent
            or any(type(row.get(key)) is not type(value) or row.get(key) != value
                   for key, value in values.items())):
        raise journal.JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY)
    from clinic_photos.publication import visit_annotation_dispatch

    attempted = False
    armed = False
    def notify():
        nonlocal attempted
        if not armed or attempted:
            return
        attempted = True
        visit_annotation_dispatch._enqueue(name, binding.site)
    try:
        frappe.db.after_commit.add(notify)
        armed = True
    except Exception:
        raise SubmissionUncertain() from None
    return name
