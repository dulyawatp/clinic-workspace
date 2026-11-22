"""Frappe runtime adapters for the framework-independent protection policy.

No Frappe import happens at module import time. Every function that touches the
framework imports it lazily, so importing this module never initialises a site.

Freshness: actor status and role membership are read from the database per call
(``User.enabled`` and the ``Has Role`` child table), not from role grants cached on
the session. Administrator never qualifies as an ordinary clinician here.

Identity resolution: a protected File is identified from the caller's current URL,
the **persisted** File row and authoritative feature references resolved by both
storage key and referenced File name. Any disagreement is denied.

Publication/transaction contract (enforced here, not by prose alone):

1. New first content only ever initialises a canonical **reserved** storage key. An
   existing registered non-reserved File keeps its normal read/update/delete
   protection but can never receive new first content.
2. A protected publication requires a registered **transaction-owning caller**
   (``owned_publication_transaction``). This app ships no such caller, so an unowned
   execution context is denied before any side effect: the integration contract is
   unsupported and fail-closed, not implicitly allowed.
3. A failed protected operation aborts publication for the **whole execution**, not one
   key: after a failure on key K every other key, every publication context entry or
   rebind, registration, write (including a chain continuation), receipt finalisation
   and ordinary cleanup is denied, even if the caller catches the error. Registration
   and finalisation failures count, not only byte failures. The aborted state is never
   cleared by a lexical exit, a savepoint rollback or re-registering an owner; only a
   new request/execution starts clean.
4. The per-key lock is transaction-owned. A denial after acquisition never releases a
   lock that may already guard earlier uncommitted work in the same transaction; only
   a transaction outcome (commit/rollback callback) or a *confirmed* release drops
   ownership.
5. ``RELEASE_LOCK`` has one confirmed outcome (integer ``1``). A ``0``/``NULL``/
   empty/malformed result or a raised release is **unconfirmed** ownership, which is
   retained and denies later reuse. An exception is not evidence that the connection
   is dead.

The installed Frappe lifecycle (request ``ClosingIterator`` callbacks, worker
``after_job`` before ``destroy``) does not prove universal connection disposal, and a
cleared native filesystem compensation cannot be reconstructed here. Nor can this seam
observe an arbitrary native ``commit``/``rollback`` failure: only the owning caller can
report one, through ``abort_publication``. Those remain native and parent-caller
acceptance gates; this module stays fail-closed instead of guessing.
"""

import hashlib
import errno
import os
import re
from contextlib import contextmanager
from datetime import datetime

from clinic_photos.access import storage
from clinic_photos.access.lifecycle import (
    LifecycleError,
    PublicationContext,
    PublicationContextError,
    assert_original_deletable,
    assert_original_record_immutable,
    assert_pending_file_cleanup,
    assert_pending_file_content_allowed,
    assert_version_deletable,
    assert_version_record_immutable,
    require_version_insert,
)
from clinic_photos.access.policy import Actor, NON_CLINICAL_SESSION_IDENTITIES
from clinic_photos.access.storage import (
    ProtectionRef,
    ProtectedAccessDenied,
    canonical_private_key,
    is_reserved_key,
    reserved_namespace_name,
    require_protected_read as _require_protected_read_policy,
)


CANDIDATE_CLINICIAN_ROLES = frozenset({"Physician", "Nursing User"})
PUBLICATION_LOCAL_ATTR = "clinic_photos_publication_context"
SAFE_DENIAL = "Not permitted for a protected clinical file"
SAFE_VALIDATION = "Protected clinical record identity or state is inconsistent"
LOCK_NAME_PREFIX = "clinic_photos:"
LOCK_TIMEOUT_SECONDS = 10
KEY_LOCK_ATTR = "clinic_photos_key_locks"
CONSUMED_WRITE_ATTR = "clinic_photos_consumed_first_writes"
IN_FLIGHT_WRITE_ATTR = "clinic_photos_in_flight_writes"
COMPLETED_WRITE_ATTR = "clinic_photos_completed_writes"
PUBLICATION_FAILURE_ATTR = "clinic_photos_failed_publications"
TRANSACTION_OWNER_ATTR = "clinic_photos_publication_owner"
PUBLICATION_ABORTED_ATTR = "clinic_photos_publication_aborted"

FILE_IDENTITY_FIELDS = (
    "file_url",
    "file_name",
    "content_hash",
    "file_size",
    "is_private",
    "owner",
    "attached_to_doctype",
    "attached_to_name",
    "attached_to_field",
)

VERSION_STATE_FIELDS = (
    "draft",
    "version_number",
    "annotation_file",
    "storage_key",
    "state",
)


# ---------------------------------------------------------------------------
# Fresh actor and native authorisation
# ---------------------------------------------------------------------------
def current_actor(user: str | None = None) -> Actor:
    """Return a fresh actor from the database; never trusts session role grants."""
    import frappe

    session_user = user if user is not None else (frappe.session.user or "")
    if not session_user or session_user in NON_CLINICAL_SESSION_IDENTITIES:
        return Actor(user=session_user or "", enabled=False, clinician=False)

    enabled = frappe.db.get_value("User", session_user, "enabled")
    roles = frappe.db.get_all(
        "Has Role",
        filters={"parent": session_user, "parenttype": "User"},
        pluck="role",
        limit=0,
    )
    return Actor(
        user=session_user,
        enabled=bool(int(enabled or 0)),
        clinician=bool(CANDIDATE_CLINICIAN_ROLES.intersection(roles or ())),
    )


def native_file_read(file_doc) -> bool:
    """Native File read decision, evaluated after the feature restriction."""
    import frappe

    return bool(frappe.has_permission("File", "read", doc=file_doc))


def native_document_read(doctype: str, name: str) -> bool:
    import frappe

    return bool(frappe.has_permission(doctype, "read", doc=name))


def authorize_protected_read(record, file_doc) -> bool:
    """Both native File read and native read of every referenced feature record."""
    if not record.registered or record.inconsistent or not record.references:
        return False
    if not native_file_read(file_doc):
        return False
    for reference in record.references:
        doctype = getattr(reference, "doctype", "")
        name = getattr(reference, "name", "")
        if not doctype or not name or not native_document_read(doctype, name):
            return False
    return True


def current_site_context() -> tuple[str, str]:
    """Server-derived site identity and shared sites volume path.

    Both values come from the native local context that the runtime established for
    this process; a request payload, header, job argument or client field is never a
    source here. Validation stays with the caller: this seam only reports what the
    framework already bound.
    """
    import frappe

    site = getattr(frappe.local, "site", "") or ""
    sites_path = getattr(frappe.local, "sites_path", "") or ""
    return (
        site if isinstance(site, str) else "",
        sites_path if isinstance(sites_path, str) else "",
    )


# ---------------------------------------------------------------------------
# Persisted state and protection lookup
# ---------------------------------------------------------------------------
SAVE_OPERATION_DOCTYPE = "Clinic Photo Save Operation"
VERSION_DOCTYPE = "Clinic Photo Annotation Version"
SAVE_OPERATION_FIELDS = (
    "name",
    "site",
    "actor",
    "kind",
    "request_key",
    "identity_digest",
    "payload_digest",
    "state",
    "draft",
    "expected_version",
    "original",
    "synthetic_patient",
    "annotation_json",
    "visit_binding_json",
    "staged_key",
    "staged_sha256",
    "staged_size",
    "staged_format",
    "image_base64",
    "reservation_key",
    "generation",
    "expected_sha256",
    "expected_size",
    "published_file",
    "published_domain_doctype",
    "published_domain_name",
    "published_version",
    "creation_identity",
)
RESERVATION_REFERENCE_KIND = storage.RESERVATION_REFERENCE_KIND


def get_save_operation(operation_id: str, *, for_update: bool = False) -> dict | None:
    """Narrow internal read of one operation row by opaque server-owned ID."""
    import frappe

    if not isinstance(operation_id, str) or not operation_id:
        return None
    return (
        frappe.db.get_value(
            SAVE_OPERATION_DOCTYPE,
            operation_id,
            list(SAVE_OPERATION_FIELDS),
            as_dict=True,
            for_update=for_update,
        )
        or None
    )


def get_save_operation_by_identity(
    identity_digest: str, *, for_update: bool = False
) -> dict | None:
    """Narrow internal read by the unique duplicate-detection digest."""
    import frappe

    if not isinstance(identity_digest, str) or not identity_digest:
        return None
    return (
        frappe.db.get_value(
            SAVE_OPERATION_DOCTYPE,
            {"identity_digest": identity_digest},
            list(SAVE_OPERATION_FIELDS),
            as_dict=True,
            for_update=for_update,
        )
        or None
    )


def prepared_key_conflicts(reservation_key: str) -> bool:
    """Whether a derived canonical key is already claimed or has byte identity.

    Preparation allocates a fresh non-reusable key; a key that already has bytes, a
    File row, a feature reference or another reservation is refused instead of being
    reused. Rows are only read here.
    """
    import frappe

    if not isinstance(reservation_key, str) or not reservation_key:
        return True
    if _confined_target_exists(reservation_key):
        return True
    if lookup_protection(reservation_key, ""):
        return True
    try:
        key = canonical_private_key(reservation_key)
    except storage.InvalidStoragePath:
        return True
    for row in frappe.db.get_all("File", fields=["name", "file_url"], limit=0):
        raw = row.get("file_url") or ""
        if not raw:
            continue
        try:
            if canonical_private_key(raw) == key:
                return True
        except storage.InvalidStoragePath:
            continue
    return False


def _file_row(file_doc) -> dict | None:
    """Persisted File identity for a named row, or ``None`` before insertion."""
    import frappe

    name = storage.file_name_of(file_doc)
    if not name:
        return None
    row = frappe.db.get_value(
        "File", name, ["name", *FILE_IDENTITY_FIELDS], as_dict=True
    )
    return row or None


def _draft_author(draft) -> str:
    import frappe

    if not draft:
        return ""
    return frappe.db.get_value("Clinic Photo Draft", draft, "author") or ""


def _synthetic_patient_operation(context):
    """Authoritative server-derived operation for a synthetic-patient context.

    The operation identity comes from the bound context, never from a caller field, and
    it is confirmed against this package's single PREPARED verifier
    (``journal._prepared_operation``): exactly one persisted row for the context key,
    that row's own name equal to the context operation identity, kind
    ``synthetic_original_create``, no draft and no original, the same actor, the local
    site, the same synthetic patient and a still-live exclusion owner. The synthetic
    patient row must exist. Nothing here reads a draft, version or original placeholder.
    """
    import frappe

    from clinic_photos.publication import exclusion, journal

    operation_id = getattr(context, "operation_id", None)
    patient = getattr(context, "synthetic_patient", None)
    if not operation_id or not patient:
        raise PublicationContextError(
            "synthetic publication context requires an operation identity"
        )

    rows = frappe.db.get_all(
        SAVE_OPERATION_DOCTYPE,
        filters={"reservation_key": context.storage_key},
        fields=["name", "reservation_key"],
        limit=0,
    )
    if len(rows) != 1:
        raise PublicationContextError(
            "synthetic publication binding requires one prepared operation"
        )
    row_name = rows[0].get("name")
    if row_name != operation_id:
        raise PublicationContextError(
            "synthetic publication binding names another operation"
        )

    row = get_save_operation(operation_id, for_update=True)
    if row is None:
        raise PublicationContextError(
            "synthetic publication binding has no persisted operation"
        )
    try:
        operation = journal._prepared_operation(row)
    except journal.JournalError as error:
        raise PublicationContextError(
            "synthetic publication binding is not a prepared operation"
        ) from error

    if (
        operation.id != operation_id
        or operation.key != context.storage_key
        or operation.actor != context.actor
        or operation.site != (getattr(frappe.local, "site", "") or "")
        or operation.kind != "synthetic_original_create"
        or operation.synthetic_patient != patient
        or (operation.draft or "")
    ):
        raise PublicationContextError(
            "synthetic publication binding disagrees with its prepared operation"
        )

    try:
        exclusion.require_locked_owner(operation.id).require_bindings(
            actor=operation.actor,
            site=operation.site,
            target_identity=operation.target_identity,
        )
    except exclusion.ExclusionError as error:
        raise PublicationContextError(
            "synthetic publication binding requires its live owner"
        ) from error

    if not frappe.db.exists("Patient", patient):
        raise PublicationContextError(
            "synthetic publication binding requires an existing patient"
        )
    return operation


def _synthetic_patient_binding(context) -> str:
    """Non-raising read of the synthetic-patient binding of this context.

    The patient comes from the authoritative PREPARED operation resolved by
    :func:`_synthetic_patient_operation`, never from the caller. A draft-bound context,
    a missing/duplicated reservation, another kind, another patient or an advanced
    state yields no binding at all.
    """
    try:
        operation = _synthetic_patient_operation(context)
    except PublicationContextError:
        return ""
    return operation.synthetic_patient or ""


def _visit_operation(context):
    """Resolve exact visit PREPARED row and its continued live owner."""
    import frappe
    from clinic_photos.publication import exclusion, journal

    operation_id = getattr(context, "operation_id", None)
    rows = frappe.db.get_all(
        SAVE_OPERATION_DOCTYPE, filters={"reservation_key": context.storage_key},
        fields=["name"], limit=0,
    )
    if len(rows) != 1 or rows[0].get("name") != operation_id:
        raise PublicationContextError("visit publication reservation is not unique")
    row = get_save_operation(operation_id, for_update=True) if operation_id else None
    try:
        operation = journal._prepared_operation(row)
        if (operation.id != operation_id or operation.kind != "visit_original_create" or
            operation.key != context.storage_key or operation.actor != context.actor or
            operation.site != (getattr(frappe.local, "site", "") or "") or
            operation.visit_binding is None):
            raise PublicationContextError("visit publication binding mismatch")
        owner = exclusion.require_locked_owner(operation.id).require_bindings(
            actor=operation.actor, site=operation.site, target_identity=operation.target_identity,
        )
        if current_publication_owner() is not owner:
            raise PublicationContextError("visit publication owner mismatch")
        owner.require_continued()
        return operation
    except (journal.JournalError, exclusion.ExclusionError, TypeError, AttributeError) as error:
        raise PublicationContextError("visit publication binding is not verifiable") from error


@contextmanager
def visit_photo_insertion(operation, bound, file_doc, photo):
    """Scope one Photo insertion to this transaction's exact first-write objects."""
    import frappe
    from clinic_photos.publication import exclusion, writer

    owner = exclusion.require_locked_owner(operation.id)
    entry = writer._intent_for_document(file_doc)
    writer._require_visit_lifetime(operation, owner, entry)
    if (operation.visit_binding is None or entry.get("operation") != operation or
        entry.get("bound") is not bound or entry.get("used") or
        writer._issued().get(id(bound)) is not bound or
        getattr(frappe.local, "_clinic_visit_photo_insert", None) is not None):
        _raise_permission()
    with bound_publication(
        actor=operation.actor, operation="pending_insert", storage_key=operation.key,
        operation_id=operation.id, visit_operation=True,
    ):
        _visit_operation(current_publication())
        witness = {"operation": operation, "bound": bound, "file": file_doc,
                   "photo": photo, "validated": False}
        frappe.local._clinic_visit_photo_insert = witness
        try:
            yield
            if not witness["validated"]:
                _raise_permission()
            _require_visit_photo_row(operation, bound, photo)
            entry["visit_photo_witness"] = witness
        finally:
            frappe.local._clinic_visit_photo_insert = None


def require_visit_photo_insertion(photo):
    """Validate exact Photo object and values at its native before-insert hook."""
    import frappe
    from clinic_photos.publication import exclusion, validation, writer

    scope = getattr(frappe.local, "_clinic_visit_photo_insert", None)
    if not scope or scope["photo"] is not photo:
        _raise_permission()
    operation, bound, file_doc = scope["operation"], scope["bound"], scope["file"]
    entry = writer._intent_for_document(file_doc)
    owner = exclusion.require_locked_owner(operation.id)
    writer._require_visit_lifetime(operation, owner, entry)
    if (entry.get("operation") != operation or entry.get("bound") is not bound or
        entry.get("used") or writer._issued().get(id(bound)) is not bound or
        _visit_operation(current_publication()) != operation):
        _raise_permission()
    writer._require_persisted_file(operation, bound.receipt, bound.file_name)
    data = writer._read_exact_target(bound.receipt)
    width, height = validation.image_dimensions(data)
    if (getattr(photo, "patient_reference", None) != operation.visit_binding.patient or
        getattr(photo, "storage_key", None) != operation.key or
        getattr(photo, "original_file", None) != bound.file_name or
        getattr(photo, "original_sha256", None) != operation.expected_sha256 or
        getattr(photo, "original_width", None) != width or
        getattr(photo, "original_height", None) != height):
        _raise_permission()
    scope["validated"] = True


def _require_visit_photo_row(operation, bound, photo):
    import frappe
    from clinic_photos.publication import validation, writer

    fields = ["name", "patient_reference", "storage_key", "original_file",
              "original_sha256", "original_width", "original_height"]
    name = getattr(photo, "name", None)
    row = frappe.db.get_value("Clinic Photo", name, fields, as_dict=True, for_update=True)
    width, height = validation.image_dimensions(writer._read_exact_target(bound.receipt))
    expected = [name, operation.visit_binding.patient, operation.key, bound.file_name,
                operation.expected_sha256, width, height]
    if not name or not row or [row.get(field) for field in fields] != expected:
        _raise_permission()


@contextmanager
def visit_file_attachment(operation, bound, file_doc, photo):
    """Allow only metadata attachment of the issued File to its inserted Photo."""
    import frappe
    from clinic_photos.publication import exclusion, writer

    entry = writer._intent_for_document(file_doc)
    owner = exclusion.require_locked_owner(operation.id)
    writer._require_visit_lifetime(operation, owner, entry)
    witness = entry.get("visit_photo_witness")
    if (not witness or witness["operation"] != operation or witness["bound"] is not bound or
        witness["file"] is not file_doc or witness["photo"] is not photo or entry.get("used") or
        witness.get("attachment_spent") or
        writer._issued().get(id(bound)) is not bound or
        getattr(frappe.local, "_clinic_visit_attachment", None) is not None):
        _raise_permission()
    with bound_publication(actor=operation.actor, operation="pending_insert",
                           storage_key=operation.key, operation_id=operation.id,
                           visit_operation=True):
        if _visit_operation(current_publication()) != operation:
            _raise_permission()
        _require_visit_photo_row(operation, bound, photo)
        writer._require_persisted_file(operation, bound.receipt, bound.file_name)
        data = writer._read_exact_target(bound.receipt)
        if any(getattr(file_doc, field, None) not in (None, b"", data) for field in (
            "content", "_content"
        )):
            _raise_permission()
        attachment = frappe.db.get_value(
            "File", bound.file_name, ["attached_to_doctype", "attached_to_name", "attached_to_field"],
            as_dict=True, for_update=True,
        )
        if not attachment or any(attachment.get(field) not in (None, "") for field in attachment):
            _raise_permission()
        persisted_before = frappe.db.get_value(
            "File", bound.file_name, "*", as_dict=True, for_update=True,
        )
        if not persisted_before:
            _raise_permission()
        scope = {"witness": witness, "content": getattr(file_doc, "content", None),
                 "_content": getattr(file_doc, "_content", None)}
        frappe.local._clinic_visit_attachment = scope
        try:
            yield
            attached = frappe.db.get_value(
                "File", bound.file_name, ["attached_to_doctype", "attached_to_name", "attached_to_field"],
                as_dict=True, for_update=True,
            )
            if (not scope.get("used") or not attached or
                attached.get("attached_to_doctype") != "Clinic Photo" or
                attached.get("attached_to_name") != photo.name or
                attached.get("attached_to_field") not in (None, "")):
                _raise_permission()
            persisted_after = frappe.db.get_value(
                "File", bound.file_name, "*", as_dict=True, for_update=True,
            )
            if not persisted_after:
                _raise_permission()
            before = dict(persisted_before)
            after = dict(persisted_after)
            for field in ("attached_to_doctype", "attached_to_name", "modified", "modified_by"):
                before.pop(field, None)
                after.pop(field, None)
            if before != after:
                _raise_permission()
            _require_visit_photo_row(operation, bound, photo)
            writer._require_persisted_file(operation, bound.receipt, bound.file_name)
            writer._read_exact_target(bound.receipt)
        except BaseException:
            owner.poison("visit_attachment_uncertain")
            raise
        finally:
            witness["attachment_spent"] = True
            frappe.local._clinic_visit_attachment = None


def require_visit_file_attachment(file_doc, previous):
    import frappe
    from clinic_photos.publication import exclusion, writer

    scope = getattr(frappe.local, "_clinic_visit_attachment", None)
    if (not scope or scope.get("used") or scope["witness"]["file"] is not file_doc or
        previous is None):
        _raise_permission()
    witness = scope["witness"]
    operation, bound, photo = witness["operation"], witness["bound"], witness["photo"]
    entry = writer._intent_for_document(file_doc)
    owner = exclusion.require_locked_owner(operation.id)
    writer._require_visit_lifetime(operation, owner, entry)
    if (entry.get("visit_photo_witness") is not witness or entry.get("bound") is not bound or
        entry.get("used") or writer._issued().get(id(bound)) is not bound or
        _visit_operation(current_publication()) != operation):
        _raise_permission()
    _require_visit_photo_row(operation, bound, photo)
    writer._require_persisted_file(operation, bound.receipt, bound.file_name)
    writer._read_exact_target(bound.receipt)
    persisted_attachment = frappe.db.get_value(
        "File", bound.file_name, ["attached_to_doctype", "attached_to_name", "attached_to_field"],
        as_dict=True, for_update=True,
    )
    if not persisted_attachment or any(
        persisted_attachment.get(field) not in (None, "") for field in persisted_attachment
    ):
        _raise_permission()
    before = dict(previous if isinstance(previous, dict) else previous.as_dict())
    if any(before.get(field) not in (None, "") for field in (
        "attached_to_doctype", "attached_to_name", "attached_to_field"
    )):
        _raise_permission()
    current = dict(file_doc.as_dict())
    for field in ("attached_to_doctype", "attached_to_name", "modified", "modified_by"):
        before.pop(field, None)
        current.pop(field, None)
    old_creation, new_creation = before.get("creation"), current.get("creation")
    if (isinstance(old_creation, datetime) and old_creation.tzinfo is None and
        isinstance(new_creation, str) and
        re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d{1,6})?", new_creation)):
        try:
            current["creation"] = datetime.fromisoformat(new_creation)
        except ValueError:
            pass
    if (before != current or getattr(file_doc, "attached_to_doctype", None) != "Clinic Photo" or
        getattr(file_doc, "attached_to_name", None) != photo.name or
        getattr(file_doc, "attached_to_field", None) not in (None, "") or
        getattr(file_doc, "content", None) != scope["content"] or
        getattr(file_doc, "_content", None) != scope["_content"]):
        _raise_permission()



def _bound_publication_author(context) -> str:
    """Drafting clinician for a draft-bound context, operation actor for the synthetic one."""
    if context is None:
        return ""
    if getattr(context, "synthetic_patient", None):
        return context.actor if _synthetic_patient_binding(context) else ""
    return _draft_author(getattr(context, "draft", ""))


def require_draft_pointer_advance(
    *,
    draft: str,
    context,
    target_version: int,
    target_version_name: str,
) -> None:
    """Authorize the single publication advance of one draft's version pointer.

    Called by the draft controller (where database access belongs) and only after the
    pure identity checks passed. Every part of the ruling is re-derived here, never
    taken from the caller: the context must be the ``commit`` binding of this draft held
    by its own author inside a live publication transaction; exactly one persisted
    operation must reserve this context's storage key; that operation must be the
    ``PREPARED`` row for this draft, actor, generation and *next* version with its live
    exclusion owner; the target version row must exist, be committed, name this draft
    and key, reference this File; and that File row must be the exact private object at
    that key. Anything else refuses, so a pointer can never name a version that does not
    exist.
    """
    import frappe

    from clinic_photos.publication import exclusion, journal

    if context is None or not _context_matches(
        context, operation="commit", draft=draft
    ):
        raise PublicationContextError(
            "draft version pointer requires its own publication commit context"
        )
    actor = frappe.session.user or ""
    if (getattr(context, "actor", None) or "") != actor:
        raise PublicationContextError(
            "draft version pointer requires the acting clinician"
        )
    if not publication_transaction_live():
        raise PublicationContextError(
            "draft version pointer requires a live publication transaction"
        )

    rows = frappe.db.get_all(
        SAVE_OPERATION_DOCTYPE,
        filters={"reservation_key": context.storage_key},
        fields=["name", "reservation_key"],
        limit=0,
    )
    if len(rows or ()) != 1:
        raise PublicationContextError(
            "draft version pointer requires one prepared operation for this key"
        )
    operation_id = rows[0].get("name")
    row = get_save_operation(operation_id, for_update=True)
    if row is None:
        raise PublicationContextError(
            "draft version pointer requires its prepared operation"
        )
    try:
        operation = journal._prepared_operation(row)
    except journal.JournalError:
        raise PublicationContextError(
            "draft version pointer requires a prepared operation"
        ) from None

    if (
        operation.id != operation_id
        or operation.key != context.storage_key
        or operation.draft != draft
        or operation.actor != actor
        or operation.generation != journal.PREPARED_GENERATION
        or (operation.expected_version or 0) + 1 != target_version
        or normalized_version_name(draft, target_version) != target_version_name
    ):
        raise PublicationContextError(
            "draft version pointer disagrees with its prepared operation"
        )

    try:
        owner = exclusion.require_locked_owner(operation.id)
        owner.require_bindings(
            actor=operation.actor,
            site=operation.site,
            target_identity=operation.target_identity,
        )
        owner.require_continued()
    except exclusion.ExclusionError:
        raise PublicationContextError(
            "draft version pointer requires its live publication owner"
        ) from None

    version = (
        frappe.db.get_value(
            VERSION_DOCTYPE,
            target_version_name,
            [
                "name",
                "draft",
                "version_number",
                "annotation_file",
                "storage_key",
                "state",
            ],
            as_dict=True,
        )
        or None
    )
    if (
        version is None
        or (version.get("name") or "") != target_version_name
        or (version.get("draft") or "") != draft
        or version.get("version_number") != target_version
        or version.get("state") != "committed"
        or (version.get("storage_key") or "") != context.storage_key
    ):
        raise PublicationContextError(
            "draft version pointer requires its committed version record"
        )
    annotation_file = version.get("annotation_file") or ""
    if not annotation_file:
        raise PublicationContextError(
            "draft version pointer requires the committed annotation file"
        )
    file_row = (
        frappe.db.get_value(
            "File",
            annotation_file,
            ["name", *FILE_IDENTITY_FIELDS],
            as_dict=True,
        )
        or None
    )
    if file_row is None or (file_row.get("name") or "") != annotation_file:
        raise PublicationContextError(
            "draft version pointer requires the persisted annotation file"
        )
    raw = file_row.get("file_url") or ""
    try:
        key = canonical_private_key(raw)
    except storage.InvalidStoragePath:
        raise PublicationContextError(
            "draft version pointer requires a canonical stored file"
        ) from None
    if key != context.storage_key or raw != key:
        raise PublicationContextError(
            "draft version pointer file does not match the operation key"
        )
    if (file_row.get("file_name") or "") != key.rsplit("/", 1)[-1]:
        raise PublicationContextError(
            "draft version pointer file name does not match the operation key"
        )
    if not int(file_row.get("is_private") or 0):
        raise PublicationContextError(
            "draft version pointer requires a private stored file"
        )


def normalized_version_name(draft: str, version: int) -> str:
    """The one server-derived annotation version name (``<draft>-v<n>``)."""
    from clinic_photos.access.lifecycle import normalize_link, normalize_version_number

    return f"{normalize_link(draft)}-v{normalize_version_number(version)}"


def lookup_protection(
    storage_key: str | None, file_name: str = ""
) -> tuple[ProtectionRef, ...]:
    """Authoritative references by canonical key and by referenced File name.

    Scoped to one key/name pair and returns value objects only; results are never
    serialised to a client. ``frappe.db.get_all`` is a privileged internal read,
    not a substitute for document permission.
    """
    import frappe

    references: list[ProtectionRef] = []

    def add_photo(row):
        references.append(
            ProtectionRef(
                kind="original",
                author="",
                committed=True,
                doctype="Clinic Photo",
                name=row["name"],
                storage_key=row.get("storage_key") or "",
            )
        )

    def add_version(row):
        draft = row.get("draft") or ""
        references.append(
            ProtectionRef(
                kind="annotation",
                author=_draft_author(draft) if draft else "",
                committed=(row.get("state") == "committed"),
                doctype="Clinic Photo Annotation Version",
                name=row["name"],
                draft=draft,
                storage_key=row.get("storage_key") or "",
            )
        )

    if storage_key:
        for row in frappe.db.get_all(
            "Clinic Photo",
            filters={"storage_key": storage_key},
            fields=["name", "storage_key"],
            limit=0,
        ):
            add_photo(row)
        for row in frappe.db.get_all(
            "Clinic Photo Annotation Version",
            filters={"storage_key": storage_key},
            fields=["name", "draft", "state", "storage_key"],
            limit=0,
        ):
            add_version(row)
        # The committed PREPARED operation row is the authoritative pending key
        # registration, so protection survives a publication rollback that erases
        # every File/domain row. A reservation grants no author or lifecycle right.
        for row in frappe.db.get_all(
            SAVE_OPERATION_DOCTYPE,
            filters={"reservation_key": storage_key},
            fields=["name", "draft", "state", "reservation_key"],
            limit=0,
        ):
            references.append(
                ProtectionRef(
                    kind=RESERVATION_REFERENCE_KIND,
                    author="",
                    committed=False,
                    doctype=SAVE_OPERATION_DOCTYPE,
                    name=row["name"],
                    storage_key=row.get("reservation_key") or "",
                )
            )

    if file_name:
        for row in frappe.db.get_all(
            "Clinic Photo",
            filters={"original_file": file_name},
            fields=["name", "storage_key"],
            limit=0,
        ):
            add_photo(row)
        for row in frappe.db.get_all(
            "Clinic Photo Annotation Version",
            filters={"annotation_file": file_name},
            fields=["name", "draft", "state", "storage_key"],
            limit=0,
        ):
            add_version(row)

    seen = set()
    unique = []
    for reference in references:
        marker = (reference.doctype, reference.name)
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(reference)
    return tuple(unique)


def classify_file(file_doc, *, persisted=storage._UNSET):
    """Classify against persisted identity and authoritative references."""
    if persisted is storage._UNSET:
        persisted = _file_row(file_doc)
    return storage.classify_file(
        file_doc,
        lookup=lookup_protection,
        persisted=persisted,
    )


def is_managed_annotation_file(file_doc, *, persisted=storage._UNSET, record=storage._UNSET):
    """Common managed File predicate for byte guards and future metadata checks."""
    import frappe
    from clinic_photos.access.draft_identity import is_reserved_visit_identity

    if persisted is storage._UNSET:
        persisted = _file_row(file_doc)
    if storage.is_managed_annotation_file(
        file_doc, lookup=lookup_protection, persisted=persisted, record=record
    ):
        return True
    seen = set()
    for doc in (file_doc, persisted):
        if not doc:
            continue
        get = doc.get if isinstance(doc, dict) else lambda key: getattr(doc, key, None)
        doctype, name = get("attached_to_doctype"), get("attached_to_name")
        if (doctype, name) in seen:
            continue
        seen.add((doctype, name))
        if doctype == "Clinic Photo Draft" and name:
            if (is_reserved_visit_identity(name) or is_reserved_visit_identity(
                frappe.db.get_value(doctype, name, "name")
            )):
                return True
        if doctype == "Clinic Photo Annotation Version" and name:
            if is_reserved_visit_identity(name):
                return True
            version = frappe.db.get_value(doctype, name, ["name", "draft"], as_dict=True)
            if version and (is_reserved_visit_identity(version.get("name")) or
                            is_reserved_visit_identity(version.get("draft"))):
                return True
    return False


def _key_registered(storage_key: str) -> bool:
    return bool(lookup_protection(storage_key, ""))


def _reserved_target(file_doc) -> tuple[str | None, bool]:
    """Expected canonical key (when known) and reserved-namespace membership."""
    raw = storage.file_url_of(file_doc)
    if raw:
        try:
            key = canonical_private_key(raw)
        except storage.InvalidStoragePath:
            return None, reserved_namespace_name(raw)
        return key, is_reserved_key(key)

    file_name = getattr(file_doc, "file_name", "") or ""
    return None, reserved_namespace_name(file_name)


# ---------------------------------------------------------------------------
# Transaction-owning caller (owner boundary)
# ---------------------------------------------------------------------------
@contextmanager
def owned_publication_transaction(owner):
    """Register the server-owned transaction-owning caller for this execution.

    This app has **no** supported caller that owns the database transaction, so every
    protected publication entry point is denied unless one registers here. The owner
    is a narrow object (not a flag, payload or boolean) that must be able to signal an
    abortive outcome explicitly::

        def publication_aborted(self) -> bool:
            \"\"\"True once this caller's transaction outcome is abortive/uncertain.\"\"\"

        def abort_publication(self, reason: str) -> None:
            \"\"\"Called when a protected operation failed; stop the transaction.\"\"\"

    The registered object is the credential: registration is server-side code, and
    ``publication_aborted()`` can only deny (``True``) — it never authorizes. Native
    commit/rollback failure cannot be observed from this seam, so the owning caller is
    the only party that can report it. Exiting this scope drops the binding only; the
    aborted state is **never** cleared by a lexical exit or savepoint.

    Any exception escaping this scope (including a nested same-owner scope) latches the
    execution abort before propagating, so a caller that catches it cannot publish
    again under a fresh owner.
    """
    import frappe

    if not callable(getattr(owner, "publication_aborted", None)) or not callable(
        getattr(owner, "abort_publication", None)
    ):
        raise PublicationContextError(
            "publication transaction owner must signal failure explicitly"
        )
    if getattr(frappe.local, TRANSACTION_OWNER_ATTR, None) is not None:
        if getattr(frappe.local, TRANSACTION_OWNER_ATTR) is not owner:
            raise PublicationContextError(
                "a different publication transaction owner is already registered"
            )
        # Nested scope of the same owning transaction: do not re-bind or clear it, but
        # an escaping failure still aborts the whole execution.
        try:
            yield owner
        except BaseException:
            _abort_publication("owner_scope_exception")
            raise
        return

    setattr(frappe.local, TRANSACTION_OWNER_ATTR, owner)
    try:
        yield owner
    except BaseException:
        _abort_publication("owner_scope_exception")
        raise
    finally:
        _latch_owner_abort(owner)
        try:
            delattr(frappe.local, TRANSACTION_OWNER_ATTR)
        except AttributeError:
            pass


def _latch_owner_abort(owner) -> None:
    """Latch an owner-reported abort or status-query failure; never the reverse."""
    import frappe

    try:
        aborted = bool(owner.publication_aborted())
    except BaseException:
        # A status query that cannot answer is uncertainty, not health.
        aborted = True
    if aborted:
        setattr(frappe.local, PUBLICATION_ABORTED_ATTR, True)


def current_publication_owner():
    """The registered transaction owner, or ``None`` for an unsupported execution."""
    return _transaction_owner()


def _transaction_owner():
    import frappe

    return getattr(frappe.local, TRANSACTION_OWNER_ATTR, None)


def publication_transaction_live() -> bool:
    """Whether a supported, non-aborted transaction-owning caller is active.

    False is the default: with no registered owner the integration contract is
    unsupported and every protected publication entry point denies before any side
    effect. The result is sticky for the execution: an owner that reports aborted, or
    whose status query raises, latches the central abort immediately, so a later
    healthy answer cannot re-open publication.
    """
    import frappe

    if getattr(frappe.local, PUBLICATION_ABORTED_ATTR, False):
        return False
    owner = _transaction_owner()
    if owner is None:
        return False
    try:
        aborted = bool(owner.publication_aborted())
    except BaseException:
        setattr(frappe.local, PUBLICATION_ABORTED_ATTR, True)
        return False
    if aborted:
        setattr(frappe.local, PUBLICATION_ABORTED_ATTR, True)
        return False
    return True


def _abort_publication(reason: str) -> None:
    """Mark this whole execution's publication aborted and notify its owner.

    Central on purpose: a failure on one key forbids every other key and every
    publication context for the rest of the execution, because the outcome of the
    owning transaction is unknown (native commit/rollback failure cannot be observed
    here). The mark is not cleared by leaving a context, a savepoint rollback or a
    lexical exit. The mark is set *before* the owner is notified, so a raising or
    uncooperative notifier cannot clear it or replace the original error.
    """
    import frappe

    setattr(frappe.local, PUBLICATION_ABORTED_ATTR, True)
    owner = _transaction_owner()
    if owner is None:
        return
    try:
        owner.abort_publication(reason)
    except BaseException:
        # The abort mark already stands; a failing notifier cannot re-open it and must
        # not mask the exception that triggered it.
        return


@contextmanager
def _publication_boundary(operation: str):
    """Deny unless a live owner transaction exists; abort it on any failure.

    The liveness check runs *inside* the guarded region: an uncertainty it raises is
    latched like any other boundary failure rather than escaping unlatched.
    """
    try:
        if not publication_transaction_live():
            _raise_permission()
        yield
    except BaseException:
        _abort_publication(operation)
        raise


# ---------------------------------------------------------------------------
# Publication context
# ---------------------------------------------------------------------------
def _local_stack():
    import frappe

    stack = getattr(frappe.local, PUBLICATION_LOCAL_ATTR, None)
    if stack is None:
        stack = []
        setattr(frappe.local, PUBLICATION_LOCAL_ATTR, stack)
    return stack


def current_publication() -> PublicationContext | None:
    import frappe

    stack = getattr(frappe.local, PUBLICATION_LOCAL_ATTR, None)
    return stack[-1] if stack else None


@contextmanager
def bound_publication(
    *,
    actor: str,
    draft: str = "",
    operation: str,
    storage_key: str,
    synthetic_patient: str | None = None,
    operation_id: str | None = None,
    visit_operation: bool = False,
):
    """Bind actor/target/operation/expected key for this request only.

    Exactly one target binding is supplied: a draft for annotation work, or the
    server-derived synthetic patient of the operation kind that has no draft. A caller
    cannot invent a draft placeholder to satisfy a draft-shaped check, and the synthetic
    branch carries the server-derived operation identity and is verified at entry: the
    owner must still be live, the persisted operation row must be the authoritative
    ``PREPARED`` evidence for this exact key, actor, site, patient, generation and
    payload, and the synthetic patient must exist. Any disagreement refuses entry with
    ``PublicationContextError``.

    Only server code can enter this context. It cannot be created from an HTTP
    parameter or an ``ignore_permissions`` flag, and the actor must equal the
    current session user. Entry *and* re-entry are denied unless a live
    transaction-owning caller is registered (see ``owned_publication_transaction``):
    with no owner this execution context is unsupported and fail-closed. Any exception
    escaping the bound body latches the execution abort before propagating, so a caller
    that catches it cannot rebind and continue publishing.
    """
    import frappe

    if actor != (frappe.session.user or ""):
        raise PublicationContextError(
            "publication context actor must be the current session user"
        )
    if not publication_transaction_live():
        raise PublicationContextError(
            "protected publication requires a live publication transaction owner"
        )

    context = PublicationContext(
        actor=actor,
        draft=draft,
        operation=operation,
        storage_key=canonical_private_key(storage_key),
        synthetic_patient=synthetic_patient,
        operation_id=operation_id,
        visit_operation=visit_operation,
    )
    if context.visit_operation:
        _visit_operation(context)
    if context.synthetic_patient:
        # The synthetic branch has no draft to confirm; the persisted prepared
        # operation is the authority, so it is resolved (and re-verified) at entry
        # instead of trusting the caller's context fields.
        try:
            _synthetic_patient_operation(context)
        except PublicationContextError:
            raise
        except BaseException as error:
            raise PublicationContextError(
                "synthetic publication binding is not verifiable"
            ) from error

    stack = _local_stack()
    stack.append(context)
    try:
        yield context
    except BaseException:
        _abort_publication("publication_context_exception")
        raise
    finally:
        stack.pop()
        if not stack:
            try:
                delattr(frappe.local, PUBLICATION_LOCAL_ATTR)
            except AttributeError:
                pass


def _context_matches(context, **kwargs):
    from clinic_photos.access.lifecycle import context_matches

    return context_matches(context, **kwargs)


def _raise_permission(message: str = SAFE_DENIAL) -> None:
    import frappe

    raise frappe.PermissionError(message)


def _raise_validation(message: str = SAFE_VALIDATION) -> None:
    import frappe

    raise frappe.ValidationError(message)


def _require_bound_context(operation: str, storage_key: str) -> PublicationContext:
    import frappe

    context = current_publication()
    if context is None or context.actor != (frappe.session.user or ""):
        _raise_permission()
    if not _context_matches(context, operation=operation, storage_key=storage_key):
        _raise_permission()
    return context


# ---------------------------------------------------------------------------
# Canonical identity and registration invariants (R5)
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Per-canonical-key serialization (advisory lock candidate)
# ---------------------------------------------------------------------------
def _lock_name(key: str) -> str:
    return LOCK_NAME_PREFIX + hashlib.sha256(key.encode("utf-8")).hexdigest()[:48]


def _lock_registry() -> dict:
    import frappe

    registry = getattr(frappe.local, KEY_LOCK_ATTR, None)
    if registry is None:
        registry = {}
        setattr(frappe.local, KEY_LOCK_ATTR, registry)
    return registry


def _arm_rollback_release(name: str) -> None:
    """Re-register the rollback release after native cleared rollback callbacks.

    Installed ``Database.commit`` resets ``before_rollback``/``after_rollback`` *before*
    running ``before_commit``, so a failing ``before_commit`` callback would otherwise
    lose both releases. Arming again from ``before_commit`` keeps a release registered
    for the rollback that a failed commit leads to.
    """
    import frappe

    registry = getattr(frappe.local, KEY_LOCK_ATTR, None) or {}
    entry = registry.get(name)
    if entry is None or entry.get("state") == "released":
        return
    entry["after_rollback_registered"] = False
    try:
        frappe.db.after_rollback.add(lambda n=name: _release_key_lock(n))
        entry["after_rollback_registered"] = True
    except Exception:
        entry["state"] = "uncertain"


def _register_lock_release(key: str, *, defer_rollback: bool) -> None:
    """Attach explicit release callbacks; the rollback one can be deferred.

    ``defer_rollback`` is used for the content-write path so the rollback release is
    appended only after native ``File.write_file`` has registered its own
    ``on_rollback`` compensation; callbacks run in registration order, so the lock is
    then released **after** the filesystem compensation.
    """
    import frappe

    entry = _lock_registry().get(_lock_name(key))
    if entry is None:
        return
    name = entry["name"]
    if not entry.get("after_commit_registered"):
        frappe.db.after_commit.add(lambda n=name: _release_key_lock(n))
        entry["after_commit_registered"] = True
    if not entry.get("before_commit_registered"):
        callback = lambda n=name: _arm_rollback_release(n)
        entry["before_commit_callback"] = callback
        frappe.db.before_commit.add(callback)
        entry["before_commit_registered"] = True
    if not defer_rollback and not entry.get("after_rollback_registered"):
        frappe.db.after_rollback.add(lambda n=name: _release_key_lock(n))
        entry["after_rollback_registered"] = True


def _release_key_lock(name: str) -> bool:
    """Release once; forget ownership only for a *confirmed* release.

    Documented ``RELEASE_LOCK`` outcomes (MariaDB/MySQL):

    * integer ``1`` — this connection owned and released the lock: confirmed, the
      registry entry is dropped;
    * integer ``0`` — the named lock is not owned by this connection: unexpected,
      because ownership was recorded from a successful ``GET_LOCK``, so ownership is
      now unconfirmed and retained;
    * ``NULL`` — the named lock does not exist in this connection: unconfirmed;
    * no rows, an empty/malformed value or a raised release: unconfirmed. An
      exception is not proof that the connection is dead.

    Unconfirmed ownership keeps the entry with ``state == "uncertain"`` so later
    acquisition of that key fails closed instead of assuming the lock is free. True
    is returned only when no ownership is recorded here or the release is confirmed.
    """
    import frappe

    registry = getattr(frappe.local, KEY_LOCK_ATTR, None)
    if not registry:
        return True
    entry = registry.get(name)
    if entry is None or entry.get("state") == "released":
        return True

    try:
        result = frappe.db.sql("select release_lock(%s)", (name,))
    except Exception:
        entry["state"] = "uncertain"
        return False

    if not _release_result_confirmed(result):
        entry["state"] = "uncertain"
        return False

    entry["state"] = "released"
    registry.pop(name, None)
    return True


def _release_result_confirmed(result) -> bool:
    """Only an explicit integer ``1`` confirms this connection released the lock."""
    try:
        value = result[0][0]
    except (TypeError, IndexError, KeyError):
        return False
    if isinstance(value, bool) or not isinstance(value, int):
        # Strings, bytes and decimals are not the documented integer outcome.
        return False
    return value == 1


def _acquire_key_lock(key: str) -> None:
    """Acquire one advisory lock per canonical key, held until commit/rollback."""
    import frappe

    if not key:
        return
    name = _lock_name(key)
    registry = _lock_registry()
    entry = registry.get(name)
    if entry is not None:
        if entry.get("state") == "uncertain":
            # Ownership could not be confirmed; never proceed on an ambiguous lock.
            raise frappe.ValidationError(SAFE_VALIDATION)
        return

    try:
        result = frappe.db.sql(
            "select get_lock(%s, %s)", (name, LOCK_TIMEOUT_SECONDS)
        )
    except Exception as error:
        raise frappe.ValidationError(SAFE_VALIDATION) from error
    if not result or int(result[0][0]) != 1:
        raise frappe.ValidationError(SAFE_VALIDATION)

    registry[name] = {"name": name, "state": "held"}
    try:
        _register_lock_release(key, defer_rollback=True)
    except Exception as error:
        _release_key_lock(name)
        raise frappe.ValidationError(SAFE_VALIDATION) from error


def _private_identity_keys(file_doc) -> list[str]:
    keys = []
    raw = storage.file_url_of(file_doc)
    if raw:
        try:
            keys.append(canonical_private_key(raw))
        except storage.InvalidStoragePath:
            if isinstance(raw, str) and raw.startswith(storage.PRIVATE_RAW_PREFIX):
                keys.append(raw)
    persisted = _file_row(file_doc)
    if persisted:
        persisted_raw = persisted.get("file_url") or ""
        if persisted_raw:
            try:
                keys.append(canonical_private_key(persisted_raw))
            except storage.InvalidStoragePath:
                keys.append(persisted_raw)
    return list(dict.fromkeys(keys))


def _acquire_identity_locks(file_doc) -> None:
    for key in _private_identity_keys(file_doc):
        _acquire_key_lock(key)


# ---------------------------------------------------------------------------
# Key-wide first-write eligibility
# ---------------------------------------------------------------------------
def _consumed_writes() -> set:
    import frappe

    consumed = getattr(frappe.local, CONSUMED_WRITE_ATTR, None)
    if consumed is None:
        consumed = set()
        setattr(frappe.local, CONSUMED_WRITE_ATTR, consumed)
    return consumed


def _consumed(key: str) -> bool:
    return bool(key) and key in _consumed_writes()


def _mark_consumed(key: str) -> None:
    if key:
        _consumed_writes().add(key)


def _failed_publications() -> set:
    import frappe

    failed = getattr(frappe.local, PUBLICATION_FAILURE_ATTR, None)
    if failed is None:
        failed = set()
        setattr(frappe.local, PUBLICATION_FAILURE_ATTR, failed)
    return failed


def _publication_failed(key: str) -> bool:
    return bool(key) and key in _failed_publications()


def _mark_publication_failed(key: str) -> None:
    """Poison one key for the rest of this uncertain transaction."""
    if key:
        _failed_publications().add(key)


def _in_flight_writes() -> dict:
    import frappe

    in_flight = getattr(frappe.local, IN_FLIGHT_WRITE_ATTR, None)
    if in_flight is None:
        in_flight = {}
        setattr(frappe.local, IN_FLIGHT_WRITE_ATTR, in_flight)
    return in_flight


# Installed 16.53 order: File.save_file -> File.save_file_on_filesystem ->
# File.write_file (read-only source excerpt). A write chain may follow exactly those
# transitions, and only the final ``write_file`` step touches bytes.
WRITE_CHAIN_TRANSITIONS = {
    "save_file": frozenset({"save_file_on_filesystem"}),
    "save_file_on_filesystem": frozenset({"write_file"}),
    "write_file": frozenset(),
}


def _context_token():
    """Exact publication binding a write chain is authorized under."""
    context = current_publication()
    if context is None:
        return None
    return (
        context.actor,
        context.draft,
        context.operation,
        context.storage_key,
        getattr(context, "synthetic_patient", None),
        getattr(context, "operation_id", None),
        getattr(context, "visit_operation", False),
    )


def _chain_for(file_doc):
    import frappe

    doc_id = id(file_doc)
    for chain in _in_flight_writes().values():
        if chain.get("doc_id") == doc_id:
            return chain
    return None


def _completed_writes() -> dict:
    import frappe

    registry = getattr(frappe.local, COMPLETED_WRITE_ATTR, None)
    if registry is None:
        registry = {}
        setattr(frappe.local, COMPLETED_WRITE_ATTR, registry)
    return registry


def _record_completed_write(chain, file_doc) -> None:
    """Server-owned receipt for the one metadata save that may follow the bytes."""

    def read(fieldname):
        getter = getattr(file_doc, "get", None)
        if callable(getter):
            return getter(fieldname)
        return getattr(file_doc, fieldname, None)

    _completed_writes()[chain["key"]] = {
        "doc_id": chain["doc_id"],
        "actor": chain["actor"],
        "context": chain["context"],
        "storage_key": chain["key"],
        "content_hash": (read("content_hash") or ""),
        "file_size": int(read("file_size") or 0),
        "used": False,
    }


def _consume_completed_write(record, file_doc, persisted) -> bool:
    """Allow exactly one metadata finalization for a completed first write.

    Bound to the exact object, actor, publication context and recorded hash/size.
    Consuming the receipt does not re-arm byte writes and does not relax identity.
    """
    import frappe

    if not publication_transaction_live():
        # Receipt finalization requires a live owning transaction; an aborted or
        # unowned execution keeps every receipt unusable.
        return False

    receipt = _completed_writes().get(record.storage_key)
    if not receipt or receipt.get("used"):
        return False
    if _publication_failed(record.storage_key):
        # No metadata finalisation for a key whose protected operation failed in this
        # transaction, even if the caller caught the denial.
        return False
    if receipt.get("doc_id") != id(file_doc):
        return False
    if receipt.get("context") != _context_token():
        return False
    if receipt.get("actor") != (frappe.session.user or ""):
        return False

    context = current_publication()
    if context is None:
        return False
    if getattr(context, "visit_operation", False):
        try:
            author = _visit_operation(context).actor
        except PublicationContextError:
            return False
    else:
        author = _bound_publication_author(context)
    if author != receipt["actor"]:
        return False

    current = _identity(file_doc)
    before = _identity(persisted)
    if current["file_url"] != record.storage_key:
        return False
    if before["file_url"] != record.storage_key:
        return False
    for fieldname in ("file_name", "is_private", "owner"):
        if current[fieldname] != before[fieldname]:
            return False
    expected = (receipt["content_hash"], int(receipt["file_size"] or 0))
    if (current["content_hash"], int(current["file_size"] or 0)) != expected:
        return False

    receipt["used"] = True
    return True


def end_content_write(file_doc, method: str = "", failed: bool = False) -> None:
    """Close one guarded native step of the write chain.

    ``failed`` discards the chain without minting a receipt, so a partial or failed
    write stays denied (the key remains consumed) until authorized cleanup. A
    successful entry-method return is the only place a completed-write receipt is
    created.
    """
    chain = _chain_for(file_doc)
    if chain is None:
        return
    key = chain["key"]
    registry = _in_flight_writes()

    if failed:
        registry.pop(key, None)
        # Bytes may be partially written: the transaction is now uncertain for this
        # key, so retries, metadata finalisation and cleanup reuse are denied until a
        # separate authorized reconciliation.
        _mark_publication_failed(key)
        _abort_publication("content_write_failed")
        _register_lock_release(key, defer_rollback=False)
        return

    if method == "write_file":
        chain["physical_writes"] = int(chain.get("physical_writes", 0)) + 1
    if method != chain["entry"]:
        return

    registry.pop(key, None)
    # Append the rollback release now: native ``on_rollback`` compensation was
    # registered during ``write_file`` and must run first.
    _register_lock_release(key, defer_rollback=False)
    if int(chain.get("physical_writes", 0)) > 0:
        _record_completed_write(chain, file_doc)


def _enter_write_chain(record, file_doc, method: str) -> str:
    """Authorize exactly one physical write for this object/context/transition."""
    import frappe

    key = record.storage_key
    if not key:
        _raise_permission()

    token = _context_token()
    if token is None:
        _raise_permission()
    actor = frappe.session.user or ""

    chain = _in_flight_writes().get(key)
    if chain is not None:
        # Only the same object, actor, context and the installed method transition may
        # continue. Recursive saves, sibling/alias objects, changed contexts and a
        # repeated physical write are denied while the outer call is still active.
        if chain.get("doc_id") != id(file_doc):
            _raise_permission()
        if chain.get("actor") != actor or chain.get("context") != token:
            _raise_permission()
        if method not in WRITE_CHAIN_TRANSITIONS.get(chain.get("stage"), frozenset()):
            _raise_permission()
        chain["stage"] = method
        return key

    if _publication_failed(key):
        # A failed protected operation already poisoned this key: no second
        # publication/retry inside the same uncertain transaction.
        _raise_permission()

    if _consumed(key):
        _raise_permission()

    _acquire_key_lock(key)
    if not _first_content_allowed(
        record, current_publication(), _all_file_rows(), file_doc
    ):
        # Eligibility denial is itself a failed protected operation. The lock may
        # already guard earlier uncommitted registration/identity work in this
        # transaction, so it is neither released nor forgotten here: ownership is
        # retained until the transaction outcome. Nothing was written yet, so the
        # rollback release is appended now (it runs after any earlier-registered
        # filesystem compensation).
        _mark_publication_failed(key)
        _register_lock_release(key, defer_rollback=False)
        _raise_permission()

    _mark_consumed(key)
    _in_flight_writes()[key] = {
        "key": key,
        "doc_id": id(file_doc),
        "actor": actor,
        "context": token,
        "stage": method,
        "entry": method,
        "physical_writes": 0,
    }
    return key


def confirm_pending_cleanup(file_doc) -> None:
    """Clear the consumed-first-write residue only after exact authorized cleanup."""
    record = classify_file(file_doc)
    key = record.storage_key if record is not None else ""
    context = current_publication()
    if not key or context is None:
        return
    if not publication_transaction_live():
        # No live owning transaction, or the execution's publication is aborted.
        return
    if _publication_failed(key):
        # Residue from a failed protected operation is not recyclable inside the same
        # uncertain transaction; authorized cleanup must run in its own transaction.
        return
    if not _context_matches(
        context, operation="pending_cleanup", storage_key=key
    ):
        return
    _consumed_writes().discard(key)
    _in_flight_writes().pop(key, None)
    _completed_writes().pop(key, None)


def _all_file_rows() -> list[dict]:
    import frappe

    return frappe.db.get_all(
        "File",
        fields=["name", "file_url", "content_hash", "file_size"],
        limit=0,
    )


def _confined_target_exists(key: str) -> bool:
    """Actual target existence inside the private files root (symlink-aware)."""
    import frappe

    if not key.startswith(storage.PRIVATE_URL_PREFIX):
        return True
    root = os.path.realpath(frappe.get_site_path("private", "files"))
    parts = key[len(storage.PRIVATE_URL_PREFIX) :].split("/")
    entry = os.path.join(root, *parts)
    target = os.path.realpath(entry)
    if target != root and not target.startswith(root + os.sep):
        return True  # symlink/escape: fail closed
    try:
        os.lstat(entry)
    except OSError as error:
        return error.errno != errno.ENOENT
    return True


def _key_has_bytes(key: str, rows: list[dict] | None = None) -> bool:
    if not key:
        return True
    if _confined_target_exists(key):
        return True
    for row in rows if rows is not None else _all_file_rows():
        try:
            alias_key = canonical_private_key(row.get("file_url") or "")
        except storage.InvalidStoragePath:
            continue
        if alias_key == key and (row.get("content_hash") or row.get("file_size")):
            return True
    return False


def _reference_file_linked(reference) -> bool:
    import frappe

    if getattr(reference, "kind", "") == "original":
        return bool(
            frappe.db.get_value("Clinic Photo", reference.name, "original_file")
        )
    if getattr(reference, "kind", "") == "annotation":
        return bool(
            frappe.db.get_value(
                "Clinic Photo Annotation Version",
                reference.name,
                "annotation_file",
            )
        )
    return True


def _key_initialized(record, key: str, rows: list[dict]) -> bool:
    """Key-wide initialized state: target bytes, aliases or linked references."""
    if not key:
        return True
    if _key_has_bytes(key, rows):
        return True
    for reference in record.references:
        if _reference_file_linked(reference):
            return True
    return False


def _draft_photo(draft: str) -> str:
    import frappe

    if not draft:
        return ""
    return frappe.db.get_value("Clinic Photo Draft", draft, "photo") or ""


def _original_photo_bound(record, context) -> bool:
    photo = _draft_photo(getattr(context, "draft", ""))
    originals = [
        reference
        for reference in record.references
        if getattr(reference, "kind", "") == "original"
    ]
    if not originals:
        return True
    return bool(photo) and all(reference.name == photo for reference in originals)


def _first_content_allowed(record, context, rows, file_doc=None):
    """Full first-content decision: key-wide init state plus the exact binding."""
    import frappe

    if context is None or context.operation != "pending_insert":
        return False
    if not publication_transaction_live():
        # No supported owner transaction, or its publication outcome is uncertain.
        return False
    actor = current_actor()
    if actor.user != (frappe.session.user or ""):
        return False
    key = record.storage_key

    # New first content may only initialise the canonical reserved namespace. An
    # existing registered non-reserved original keeps its normal protection but can
    # never receive new bytes.
    if not key or not is_reserved_key(key):
        return False
    if _publication_failed(key):
        return False

    # A consumed first write is never repeatable, even before metadata is persisted
    # and regardless of the caller's own empty row.
    if _consumed(key):
        return False

    if record.kind == storage.PENDING_RECORD_KIND:
        # Reservation-only identity: the single authorized first content is the one the
        # server-owned writer registered for exactly this object, after verifying the
        # owner, the authoritative PREPARED evidence and the exact byte evidence.
        # Anything else pointed at this key creates no bytes.
        from clinic_photos.publication import writer

        return file_doc is not None and writer.first_write_intent_authorized(
            file_doc, key, context
        )

    # Anything with existing byte/alias evidence outside the reservation-only kind is
    # an overwrite attempt.
    if _key_initialized(record, key, rows):
        return False

    synthetic_patient = getattr(context, "synthetic_patient", None) or ""
    try:
        assert_pending_file_content_allowed(
            record,
            context,
            actor_user=actor.user,
            draft_author=_bound_publication_author(context),
            actor_enabled=actor.enabled,
            actor_clinician=actor.clinician,
            has_existing_bytes=False,
            original_photo_allowed=_original_photo_bound(record, context),
            synthetic_patient=synthetic_patient,
            synthetic_bound=False,
        )
    except LifecycleError:
        return False
    return True


def _content_context_binding(record, context=None, file_doc=None):
    import frappe

    context = context or current_publication()
    if context is None or context.operation != "pending_insert":
        return None
    rows = _all_file_rows()
    if not _first_content_allowed(record, context, rows, file_doc):
        return None
    return context


def require_canonical_protected_url(file_doc) -> None:
    """Protected keys must be stored in canonical spelling.

    Unrelated files are untouched: only reserved-namespace keys and keys already
    registered by a feature record are checked.
    """
    raw = storage.file_url_of(file_doc)
    if not raw:
        return

    try:
        key = canonical_private_key(raw)
    except storage.InvalidStoragePath:
        if isinstance(raw, str) and raw.startswith(storage.PRIVATE_RAW_PREFIX):
            if reserved_namespace_name(raw):
                _raise_permission()
        return

    if raw == key:
        return
    if is_reserved_key(key) or _key_registered(key):
        _raise_permission()


def protected_file_names() -> tuple[str, ...]:
    """Complete canonicalised scan of File metadata for list-query exclusions.

    Returns File **names only** (never row metadata) covering: files referenced by a
    feature record, files whose canonical key is a registered storage key, and
    reserved-namespace orphans in any accepted spelling (including encoded ones).
    Unpaginated by construction; unrelated rows are not returned.
    """
    import frappe

    referenced = set()
    for doctype, fieldname in (
        ("Clinic Photo", "original_file"),
        ("Clinic Photo Annotation Version", "annotation_file"),
    ):
        for value in frappe.db.get_all(doctype, pluck=fieldname, limit=0) or ():
            if value:
                referenced.add(value)

    registered_keys = set()
    for row in _feature_storage_key_rows():
        stored = row.get("storage_key") or ""
        if not stored:
            continue
        try:
            registered_keys.add(canonical_private_key(stored))
        except storage.InvalidStoragePath:
            registered_keys.add(stored)
    for stored in _reserved_operation_keys():
        registered_keys.add(stored)

    names = set(referenced)
    for row in frappe.db.get_all(
        "File", fields=["name", "file_url"], limit=0
    ):
        name = row.get("name") or ""
        if not name:
            continue
        raw = row.get("file_url") or ""
        if name in referenced:
            names.add(name)
            continue
        if not raw:
            continue
        try:
            key = canonical_private_key(raw)
        except storage.InvalidStoragePath:
            if (
                isinstance(raw, str)
                and raw.startswith(storage.PRIVATE_RAW_PREFIX)
                and reserved_namespace_name(raw)
            ):
                names.add(name)
            continue
        if key in registered_keys or is_reserved_key(key):
            names.add(name)
    return tuple(sorted(names))


def _feature_storage_key_rows() -> list[dict]:
    import frappe

    rows = []
    for doctype in ("Clinic Photo", "Clinic Photo Annotation Version"):
        for row in frappe.db.get_all(
            doctype,
            fields=["name", "storage_key"],
            limit=0,
        ):
            rows.append({"doctype": doctype, **row})
    return rows


def _reserved_operation_keys() -> list[str]:
    """Canonical keys claimed by durable operation reservations."""
    import frappe

    keys = []
    for row in frappe.db.get_all(
        SAVE_OPERATION_DOCTYPE,
        fields=["reservation_key"],
        limit=0,
    ):
        stored = row.get("reservation_key") or ""
        if not stored:
            continue
        try:
            keys.append(canonical_private_key(stored))
        except storage.InvalidStoragePath:
            keys.append(stored)
    return keys



def require_registration_identities_consistent(storage_key: str) -> None:
    """Reject non-canonical stored keys and any pre-existing alias spelling.

    Registered identity must be stored verbatim as its own canonical key. Registration
    then canonicalises **every** File URL and every feature ``storage_key`` in one
    complete (unpaginated) scan, so an encoded or otherwise re-spelled alias of the
    same identity blocks registration. Rows are never migrated, rewritten or deleted
    here, and unrelated rows are untouched.
    """
    import frappe

    if not isinstance(storage_key, str) or not storage_key:
        _raise_validation()
    if storage_key != storage_key.strip():
        # Reject surrounding/embedded edge whitespace instead of validating a
        # stripped copy while the raw value is what gets stored.
        _raise_validation()
    try:
        key = canonical_private_key(storage_key)
    except storage.InvalidStoragePath:
        _raise_validation()
    if storage_key != key:
        # Stored keys are identity, not a spelling convention.
        _raise_validation()

    _acquire_key_lock(key)

    # Complete scan: SQL cannot percent-decode, so canonicalise in Python.
    for row in frappe.db.get_all(
        "File",
        fields=["name", "file_url"],
        limit=0,
    ):
        raw_url = row.get("file_url") or ""
        if not raw_url:
            continue
        try:
            alias_key = canonical_private_key(raw_url)
        except storage.InvalidStoragePath:
            if (
                isinstance(raw_url, str)
                and raw_url.startswith(storage.PRIVATE_RAW_PREFIX)
                and reserved_namespace_name(raw_url)
            ):
                _raise_validation()
            continue

        reference_keys = {
            reference.storage_key
            for reference in lookup_protection(None, row.get("name") or "")
            if reference.storage_key
        }
        if alias_key == key:
            if raw_url != key:
                _raise_validation()
            if reference_keys - {key}:
                _raise_validation()
        elif key in reference_keys:
            # A feature record registers this key but references another identity.
            _raise_validation()

    for row in _feature_storage_key_rows():
        stored = row.get("storage_key") or ""
        if not stored:
            continue
        try:
            stored_key = canonical_private_key(stored)
        except storage.InvalidStoragePath:
            if stored.startswith(storage.PRIVATE_RAW_PREFIX):
                _raise_validation()
            continue
        if stored != stored_key:
            # Feature records also store identity verbatim; no alias spellings.
            _raise_validation()


# ---------------------------------------------------------------------------
# Read and pending content consumption
# ---------------------------------------------------------------------------
def _pending_content_matches(record, file_doc) -> bool:
    """Whether this File's key is still eligible for its one first content write."""
    return _content_context_binding(record, file_doc=file_doc) is not None


def _visit_protected_read(record, file_doc):
    """Return True for an exact published visit pair, None for unrelated records."""
    import frappe
    from clinic_photos.publication import journal
    from clinic_photos.publication.contracts import VISIT_ORIGINAL_CREATE, VisitBinding

    rows = frappe.db.get_all(
        SAVE_OPERATION_DOCTYPE, filters={"reservation_key": record.storage_key},
        fields=["name", "kind"], limit=0,
    )
    slots = []
    for reference in record.references:
        if reference.doctype == "Clinic Photo":
            slots.extend(frappe.db.get_all(
                "Clinic Visit Photo Slot", filters={"photo": reference.name},
                fields=["name"], limit=0,
            ))
    visit_rows = [row for row in rows if row.get("kind") == VISIT_ORIGINAL_CREATE]
    if not visit_rows and not slots and not storage.is_canonical_visit_key(record.storage_key):
        return None
    if len(rows) != 1 or len(visit_rows) != 1 or len(slots) != 1:
        _raise_permission()
    try:
        operation = journal.load_verified_operation(visit_rows[0]["name"])
        binding = operation.request.visit_binding
        if (operation.state != "PUBLISHED" or operation.kind != VISIT_ORIGINAL_CREATE or
            operation.site != frappe.local.site or type(binding) is not VisitBinding or
            not operation.published or operation.published.file_name != storage.file_name_of(file_doc) or
            operation.published.reservation.key != record.storage_key):
            _raise_permission()
        from clinic_photos.visit import pair
        projection = pair.read_pair(binding.appointment)
        item = projection[binding.slot.lower()]
        if (not item or item["photo"] != operation.published.domain_name or
            item["file"] != operation.published.file_name):
            _raise_permission()
        return True
    except Exception:
        _raise_permission()


def require_protected_read(file_doc) -> None:
    persisted = _file_row(file_doc)
    record = storage.classify_file(file_doc, lookup=lookup_protection, persisted=persisted)
    managed = is_managed_annotation_file(file_doc, persisted=persisted, record=record)
    if managed:
        from clinic_photos.access.entrypoints import mark_protected_request
        mark_protected_request()
        _raise_permission()
    if record is None:
        return

    # Server-owned response marker, set before authorization: an allowed protected
    # read and a denial both keep no-store headers. The header hook never authorizes.
    from clinic_photos.access.entrypoints import mark_protected_request

    mark_protected_request()

    if not record.registered or record.inconsistent:
        _raise_permission()

    if (storage.is_canonical_visit_key(record.storage_key) and
        storage.file_url_of(file_doc) != record.storage_key):
        _raise_permission()

    # Internal first-insert content access is bound to the exact native File object,
    # not an external byte-read capability.
    if _pending_content_matches(record, file_doc):
        return

    actor = current_actor()
    visit_read = _visit_protected_read(record, file_doc)
    try:
        _require_protected_read_policy(
            file_doc,
            actor=actor,
            native_read=(True if visit_read is True else authorize_protected_read(record, file_doc)),
            lookup=lookup_protection,
            persisted=persisted,
            record=record,
        )
    except ProtectedAccessDenied:
        if visit_read is True or not _pending_content_matches(record, file_doc):
            _raise_permission()


def require_content_write(file_doc, method: str) -> None:
    """Guard one step of a content write chain for the exact File object.

    Every step of the chain — including a continuation of the same object — is denied
    once the execution's publication transaction is aborted or unowned. Unrelated
    Files are untouched: the gate only applies to rows this app protects.
    """
    _, reserved = _reserved_target(file_doc)
    record = classify_file(file_doc)

    if record is None and not reserved:
        return

    with _publication_boundary("content_write"):
        require_canonical_protected_url(file_doc)
        if record is None:
            # Reserved-looking row without a resolvable registered identity.
            _raise_permission()
        if not record.registered or record.inconsistent or not record.storage_key:
            _raise_permission()

        _enter_write_chain(record, file_doc, method)


# ---------------------------------------------------------------------------
# Trusted destructive destinations and retained rollback poison
# ---------------------------------------------------------------------------
ROLLBACK_POISON_ATTR = "clinic_photos_rollback_poisoned"
DESTINATION_PROTECTED = "protected"
DESTINATION_UNKNOWN = "unknown"
DESTINATION_UNRELATED = "unrelated"
PUBLIC_URL_PREFIX = "/files/"


def _rollback_poison() -> dict:
    import frappe

    registry = getattr(frappe.local, ROLLBACK_POISON_ATTR, None)
    if registry is None:
        registry = {"objects": {}, "keys": set()}
        setattr(frappe.local, ROLLBACK_POISON_ATTR, registry)
    return registry


def _poison_destination(file_doc, keys=()) -> None:
    """Retain in-memory uncertainty per object and per known key.

    Native rollback pops its own bookkeeping flags; this record is separate and is
    never cleared by them. It is evidence for this execution only and is not a
    durable journal write (the DB may already be unusable inside a rollback).
    """
    registry = _rollback_poison()
    registry["objects"][id(file_doc)] = file_doc
    for key in keys:
        if key:
            registry["keys"].add(key)


def destination_poisoned(file_doc, keys=()) -> bool:
    import frappe

    registry = getattr(frappe.local, ROLLBACK_POISON_ATTR, None)
    if not registry:
        return False
    if registry["objects"].get(id(file_doc)) is file_doc:
        return True
    return any(bool(key) and key in registry["keys"] for key in keys)


def _site_file_roots():
    """Real private/public files roots of this site, or ``None`` when unknown."""
    import frappe

    roots = []
    for kind, prefix in (
        ("private", storage.PRIVATE_URL_PREFIX),
        ("public", PUBLIC_URL_PREFIX),
    ):
        try:
            path = frappe.get_site_path(kind, "files")
        except Exception:
            return None
        if not isinstance(path, str) or not path:
            return None
        try:
            roots.append((os.path.realpath(path), prefix))
        except OSError:
            return None
    return tuple(roots)


def canonical_key_for_path(value) -> tuple[str, bool]:
    """Map one filesystem path to ``(canonical key, known)`` using trusted roots.

    ``known`` False means the path could not be resolved inside this site's private or
    public files root, so it must be treated as an unknown destination rather than as
    proved unrelated. ``realpath`` resolves symlinks before the prefix comparison, so a
    path cannot borrow a protected spelling from outside the root.
    """
    if value is None:
        return "", True
    try:
        text = os.fspath(value)
    except TypeError:
        return "", False
    if not isinstance(text, str) or not text or "\x00" in text:
        return "", False
    roots = _site_file_roots()
    if roots is None:
        return "", False
    try:
        resolved = os.path.realpath(text)
    except OSError:
        return "", False
    for root, prefix in roots:
        if not root:
            continue
        if resolved == root:
            return "", False
        if resolved.startswith(root + os.sep):
            relative = resolved[len(root) + 1 :].replace(os.sep, "/")
            return prefix + relative, True
    return "", False


def _destination_keys(file_doc, extra_paths=()) -> tuple[list[str], bool]:
    """Known canonical destination keys plus whether any destination was unknown."""
    keys = []
    unknown = False

    raw = storage.file_url_of(file_doc)
    if raw:
        try:
            keys.append(storage.canonical_private_key(raw))
        except storage.InvalidStoragePath:
            if isinstance(raw, str) and raw.startswith(storage.PRIVATE_RAW_PREFIX):
                # Private-looking but not canonicalisable: fail closed.
                unknown = True

    persisted = _file_row(file_doc)
    if persisted:
        persisted_raw = persisted.get("file_url") or ""
        if persisted_raw:
            try:
                keys.append(storage.canonical_private_key(persisted_raw))
            except storage.InvalidStoragePath:
                unknown = True

    for value in extra_paths:
        key, known = canonical_key_for_path(value)
        if not known:
            unknown = True
        elif key:
            keys.append(key)

    return [key for key in dict.fromkeys(keys) if key], unknown


def destination_state(file_doc, extra_paths=()) -> str:
    """Classify a native destructive destination for this app's protection.

    Protected, reserved, previously poisoned and unclassifiable destinations all
    refuse mutation. Only a destination proved unrelated may keep native behaviour.
    """
    keys, unknown = _destination_keys(file_doc, extra_paths)

    if classify_file(file_doc) is not None:
        return DESTINATION_PROTECTED
    for key in keys:
        if storage.is_reserved_key(key) or lookup_protection(key, ""):
            return DESTINATION_PROTECTED
    if destination_poisoned(file_doc, keys):
        return DESTINATION_PROTECTED
    if unknown:
        return DESTINATION_UNKNOWN
    return DESTINATION_UNRELATED


def require_protected_rollback_boundary(file_doc) -> bool:
    """Decide native rollback compensation: True delegates, False preserves residue.

    This deliberately never raises: a denial raised from a routine rollback callback
    would suppress the remaining callback queue, including this app's own lock
    releases. Refusal records poison and aborts the execution's publication instead.
    """
    try:
        flags = getattr(file_doc, "flags", None)
        original_path = getattr(flags, "original_path", None) if flags is not None else None
        extra = []
        if isinstance(original_path, dict):
            extra.extend((original_path.get("old"), original_path.get("new")))
        state = destination_state(file_doc, tuple(extra))
        if state == DESTINATION_UNRELATED:
            return True
        keys, _unknown = _destination_keys(file_doc, tuple(extra))
        _poison_destination(file_doc, keys)
        for key in keys:
            _mark_publication_failed(key)
        _abort_publication("protected_file_rollback")
    except BaseException:
        _poison_destination(file_doc, ())
        _abort_publication("protected_file_rollback")
    return False


def require_file_rollback(file_doc) -> bool:
    """Return whether native rollback compensation may run for this destination."""
    return require_protected_rollback_boundary(file_doc)


# ---------------------------------------------------------------------------
# File insert / update / delete / derivatives
# ---------------------------------------------------------------------------
def _may_be_protected(file_doc) -> bool:
    """Whether this File could carry a protected destination at all.

    Public/remote names and ordinary file names keep their prior path with no
    protection-lookup dependency; private-looking URLs and reserved-namespace names
    are classified (and therefore depend on the operation reservation table, the
    approved availability tradeoff).
    """
    raw = storage.file_url_of(file_doc)
    if isinstance(raw, str) and raw.startswith(storage.PRIVATE_RAW_PREFIX):
        return True
    file_name = getattr(file_doc, "file_name", "") or ""
    return storage.reserved_namespace_name(file_name)


def require_file_insert(file_doc) -> None:
    """Register File identity inside the bound pending context, canonical only.

    A protected destination is never an ordinary upload. A reserved-namespace
    registration, a registered non-reserved key and an unclassifiable
    private-looking destination all take the same publication boundary: a live
    owner, the exact bound ``pending_insert`` context for this key, and the drafting
    clinician. This is the pre-``db_insert`` reclassification after native duplicate
    retargeting. Only a destination proved unrelated keeps native behaviour.
    """
    reserved = _reserved_target(file_doc)[1]
    record = None

    if not reserved:
        if not _may_be_protected(file_doc):
            _acquire_identity_locks(file_doc)
            require_canonical_protected_url(file_doc)
            return
        record = classify_file(file_doc)
        if record is None:
            _acquire_identity_locks(file_doc)
            require_canonical_protected_url(file_doc)
            return

    with _publication_boundary("file_insert"):
        _acquire_identity_locks(file_doc)
        require_canonical_protected_url(file_doc)
        if record is None:
            record = classify_file(file_doc)
        if (
            record is None
            or not record.registered
            or record.inconsistent
            or not record.storage_key
        ):
            _raise_permission()
        key = record.storage_key
        if _publication_failed(key) or destination_poisoned(file_doc, (key,)):
            # No second publication (registration) for a key whose protected operation
            # failed in this transaction.
            _raise_permission()

        context = _require_bound_context("pending_insert", key)
        if getattr(context, "visit_operation", False):
            from clinic_photos.publication import writer
            operation = _visit_operation(context)
            intent = writer._intent_for_document(file_doc)
            if (intent is None or intent.get("used") or
                intent.get("operation") != operation or intent.get("key") != key or
                intent.get("context") != writer._context_pair(context)):
                _raise_permission()
            author = operation.actor
        else:
            author = _bound_publication_author(context)
        if not author or context.actor != author:
            _raise_permission()


def require_file_persisted_identity(file_doc) -> None:
    """Post-complete-insert verification of a protected File's persisted identity.

    Native callbacks and duplicate validation can still change the destination after a
    successful row insertion. This check therefore re-resolves the persisted row and
    the authoritative references; a violation poisons the publication and raises, so
    the caller must roll back. It never claims that no row was staged, and it does not
    replace native permission/validation vetoes.
    """
    record = classify_file(file_doc)
    if record is None:
        # ``classify_file`` already resolved the persisted row by name, so this is a
        # proved-unrelated destination in memory and in storage alike.
        return

    with _publication_boundary("file_insert_verify"):
        if (
            record is None
            or not record.registered
            or record.inconsistent
            or not record.storage_key
        ):
            _raise_permission()
        key = record.storage_key
        if _publication_failed(key) or destination_poisoned(file_doc, (key,)):
            _raise_permission()
        _require_bound_context("pending_insert", key)

        persisted = _file_row(file_doc)
        if persisted is None:
            _raise_permission()
        persisted_record = storage.classify_file(
            persisted, lookup=lookup_protection, persisted=persisted
        )
        if (
            persisted_record is None
            or not persisted_record.registered
            or persisted_record.inconsistent
            or persisted_record.storage_key != key
        ):
            _raise_permission()

        current = _identity(file_doc)
        before = _identity(persisted)
        for fieldname in ("file_url", "file_name", "is_private", "owner"):
            if current[fieldname] != before[fieldname]:
                _raise_permission()

        holders = set()
        for candidate in _all_file_rows():
            try:
                candidate_key = canonical_private_key(candidate.get("file_url") or "")
            except storage.InvalidStoragePath:
                continue
            if candidate_key == key:
                holders.add(candidate.get("name") or "")
        if holders != {storage.file_name_of(file_doc)}:
            # A duplicate alias of the same protected identity was created or reused.
            _raise_permission()


def _identity(file_doc) -> dict:
    raw_url = storage.file_url_of(file_doc)
    try:
        key = canonical_private_key(raw_url) if raw_url else ""
    except storage.InvalidStoragePath:
        key = raw_url or ""

    def read(fieldname):
        if isinstance(file_doc, dict):
            return file_doc.get(fieldname)
        return getattr(file_doc, fieldname, None)

    def is_private(value):
        return bool(int(value or 0))

    return {
        "file_url": key,
        "file_name": (read("file_name") or "").strip(),
        "content_hash": (read("content_hash") or "").strip(),
        "file_size": read("file_size") or 0,
        "is_private": is_private(read("is_private")),
        "owner": (read("owner") or "").strip(),
    }


def _content_only_identity_change(persisted, file_doc) -> bool:
    """Only the first-content hash/size may vary inside the bound pending context."""
    before = _identity(persisted)
    after = _identity(file_doc)
    for fieldname in ("file_url", "file_name", "is_private", "owner"):
        if before[fieldname] != after[fieldname]:
            return False
    return True


def require_file_update(file_doc, previous, *, inserting: bool = False) -> None:
    """Compare against the persisted row, never a stale caller snapshot."""
    _acquire_identity_locks(file_doc)
    require_canonical_protected_url(file_doc)

    if inserting:
        require_file_insert(file_doc)
        return

    persisted = _file_row(file_doc)
    if persisted is None:
        # A named persisted row must exist; an unnamed non-insert is not a valid
        # File update path.
        if storage.file_name_of(file_doc) or storage.file_url_of(file_doc):
            _raise_permission()
        return

    current_record = classify_file(file_doc)
    persisted_record = storage.classify_file(
        persisted, lookup=lookup_protection, persisted=persisted
    )
    if current_record is None and persisted_record is None:
        return

    with _publication_boundary("file_finalize"):
        if (
            current_record is None
            or persisted_record is None
            or not current_record.registered
            or not persisted_record.registered
            or current_record.inconsistent
            or persisted_record.inconsistent
        ):
            _raise_permission()

        from clinic_photos.publication import writer
        intent = writer._intent_for_key(current_record.storage_key)
        if intent and intent["operation"].visit_binding is not None:
            require_visit_file_attachment(file_doc, previous)
            import frappe
            frappe.local._clinic_visit_attachment["used"] = True
            return

        # One metadata finalization is allowed for a completed first write, bound to the
        # exact object/context/result; it does not re-arm byte writes.
        if _consume_completed_write(current_record, file_doc, persisted):
            return

        # First content consumption inside the exact pending binding may change only
        # the content hash/size; owner, file name, canonical URL and privacy must still
        # match.
        if _pending_content_matches(current_record, file_doc):
            if not _content_only_identity_change(persisted, file_doc):
                _raise_permission()
            return

        if _identity(persisted) != _identity(file_doc):
            _raise_permission()


def require_private_transition(file_doc) -> None:
    """Guard the actual privacy transition (``handle_is_private_changed``)."""
    record = classify_file(file_doc)
    if record is None:
        _acquire_identity_locks(file_doc)
        require_canonical_protected_url(file_doc)
        return

    with _publication_boundary("privacy_transition"):
        _acquire_identity_locks(file_doc)
        require_canonical_protected_url(file_doc)

        raw = storage.file_url_of(file_doc)
        is_private = bool(int(getattr(file_doc, "is_private", 0) or 0))
        if not is_private or not isinstance(raw, str) or not raw.startswith(
            storage.PRIVATE_URL_PREFIX
        ):
            _raise_permission()
        if _file_row(file_doc) is not None:
            # Existing protected bytes never move between private/public storage.
            _raise_permission()
        if not _pending_content_matches(record, file_doc):
            _raise_permission()


def require_derivative(file_doc) -> None:
    """Thumbnails and optimization are denied for protected bytes in this phase."""
    if classify_file(file_doc) is None:
        return
    _raise_permission()


def require_file_delete(file_doc, previous) -> None:
    """Any immutable reference prevents deletion; pending cleanup is fully bound.

    The preflight is itself part of the aborting publication boundary: a denied or
    failing protected cleanup aborts the execution, not just this call. Administrator
    identity is not protected disposal authority: exact-stack teardown is an external
    operation and this app ships no in-app protected purge.
    """
    record = classify_file(file_doc)
    if record is None and previous is not None:
        record = classify_file(previous)
    if record is None:
        return

    with _publication_boundary("protected_cleanup"):
        if destination_poisoned(
            file_doc, (record.storage_key,) if record.storage_key else ()
        ):
            _raise_permission()
        if record.storage_key and _publication_failed(record.storage_key):
            # Cleanup reuse after a failed protected operation in the same uncertain
            # transaction is denied; the residue stays denied/immutable.
            _raise_permission()

        _acquire_identity_locks(file_doc)

        if not record.registered:
            if not record.storage_key:
                _raise_permission()
            _require_bound_context("pending_cleanup", record.storage_key)
            return

        context = current_publication()
        actor = current_actor()
        draft_author = _draft_author(getattr(context, "draft", "")) if context else ""
        try:
            assert_pending_file_cleanup(
                record,
                context,
                actor_user=actor.user,
                draft_author=draft_author,
                actor_enabled=actor.enabled,
                actor_clinician=actor.clinician,
            )
        except LifecycleError:
            _raise_permission()


def require_file_delete_on_disk(file_doc) -> None:
    """Refuse native on-disk deletion/restoration of a protected destination.

    This is the boundary for the native ``_delete_file_on_disk`` route (and for any
    native compensation that dispatches through it). A protected, reserved, poisoned
    or unclassifiable destination is refused for every user, Administrator included;
    only a proved-unrelated destination keeps native behaviour.
    """
    if destination_state(file_doc) == DESTINATION_UNRELATED:
        return
    with _publication_boundary("protected_cleanup_effect"):
        _acquire_identity_locks(file_doc)
        keys, _unknown = _destination_keys(file_doc)
        _poison_destination(file_doc, keys)
        for key in keys:
            _mark_publication_failed(key)
        _raise_permission()


@contextmanager
def protected_cleanup_execution(file_doc, previous=None):
    """Encompass one protected native cleanup effect (delete / trash).

    Unrelated Files are untouched: no owner requirement and no latching. For a
    protected File the actual native effect runs inside the publication boundary, so a
    partially applied deletion or ``on_trash`` failure aborts the execution even when
    the caller catches the exception. There is no bypass and no DB monkeypatch, and
    there is no Administrator shortcut.
    """
    record = classify_file(file_doc)
    if record is None and previous is not None:
        record = classify_file(previous)
    if record is None:
        yield
        return
    with _publication_boundary("protected_cleanup_effect"):
        yield


# ---------------------------------------------------------------------------
# Feature controllers
# ---------------------------------------------------------------------------
def _version_state(document) -> dict:
    return {fieldname: document.get(fieldname) for fieldname in VERSION_STATE_FIELDS}


def _original_state(document) -> dict:
    from clinic_photos.access.lifecycle import ORIGINAL_IMMUTABLE_FIELDS

    return {fieldname: document.get(fieldname) for fieldname in ORIGINAL_IMMUTABLE_FIELDS}


def _file_reference_state(file_name: str, storage_key: str) -> bool:
    """Whether a persisted File row matches one canonical key and is registered."""
    if not file_name:
        return False
    row = _file_row({"name": file_name, "file_url": None})
    if not row:
        return False
    try:
        row_key = canonical_private_key(row.get("file_url") or "")
    except storage.InvalidStoragePath:
        return False
    if row_key != storage_key:
        return False
    record = storage.classify_file(
        row, lookup=lookup_protection, persisted=row
    )
    return bool(
        record
        and record.registered
        and not record.inconsistent
        and record.kind == "annotation"
        and not record.committed
    )


def require_original_registration(storage_key) -> None:
    """Register a protected original's storage identity inside a live publication.

    Original registration is a feature-registration step of the publication boundary:
    it denies for an unowned/aborted execution and aborts the transaction when its
    identity checks fail. The stored value is passed untouched.
    """
    with _publication_boundary("original_registration"):
        require_registration_identities_consistent(storage_key)


def require_version_registration(document) -> None:
    with _publication_boundary("version_registration"):
        context = current_publication()
        state = _version_state(document)
        try:
            require_version_insert(
                state,
                context,
                draft_author=_draft_author(state.get("draft")),
            )
        except LifecycleError:
            _raise_validation()

        # Untouched stored value: whitespace or any other non-canonical spelling must
        # be rejected, not normalised into a different identity before the check.
        storage_key = state.get("storage_key")
        if not isinstance(storage_key, str) or not storage_key:
            _raise_validation()
        require_registration_identities_consistent(storage_key)

        annotation_file = (state.get("annotation_file") or "").strip()
        if annotation_file and not _file_reference_state(
            annotation_file, storage_key
        ):
            _raise_validation()


def require_version_update(document, previous) -> None:
    with _publication_boundary("version_finalize"):
        context = current_publication()
        current_state = _version_state(document)
        previous_state = (
            _version_state(previous) if previous is not None else None
        )
        if not previous_state:
            _raise_validation()

        annotation_file_allowed = None
        current_file = (current_state.get("annotation_file") or "").strip()
        previous_file = (previous_state.get("annotation_file") or "").strip()
        if current_file and current_file != previous_file:
            if not _file_reference_state(
                current_file, current_state.get("storage_key") or ""
            ):
                _raise_validation()
            annotation_file_allowed = current_file

        if (
            previous_state.get("state") != "committed"
            and current_state.get("state") == "committed"
        ):
            if not current_file or not _file_reference_state(
                current_file, current_state.get("storage_key") or ""
            ):
                _raise_validation()

        try:
            assert_version_record_immutable(
                previous_state,
                current_state,
                context,
                annotation_file_allowed=annotation_file_allowed,
            )
        except LifecycleError:
            _raise_validation()


def require_version_delete(document) -> None:
    """Refuse a committed version delete for every identity, Administrator included.

    Actor identity never substitutes for operation-bound authority: the shared
    publication boundary plus the lifecycle assertion apply unchanged to every session
    user, so no in-app purge exists for committed clinical records or their bytes.
    """
    with _publication_boundary("protected_cleanup"):
        state = _version_state(document)
        try:
            assert_version_deletable(
                state,
                current_publication(),
                draft_author=_draft_author(state.get("draft")),
            )
        except LifecycleError:
            _raise_permission()


def require_original_update(document, previous) -> None:
    with _publication_boundary("original_finalize"):
        try:
            assert_original_record_immutable(
                _original_state(previous) if previous is not None else None,
                _original_state(document),
                current_publication(),
            )
        except LifecycleError:
            _raise_validation()


def require_original_delete(document) -> None:
    """Refuse an original-record delete for every identity, Administrator included."""
    with _publication_boundary("protected_cleanup"):
        try:
            assert_original_deletable(_original_state(document))
        except LifecycleError:
            _raise_permission()
