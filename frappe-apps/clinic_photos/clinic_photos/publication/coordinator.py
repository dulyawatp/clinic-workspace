"""Two-phase publication coordinator for one committed SUBMITTED operation.

Only the fixed fresh child (``python -m clinic_photos.publication.process`` from the
absolute bench ``sites`` directory) may call :func:`publish_submission`; the child
registers itself through :func:`child_process_publication` after its bootstrap, gate,
clean-context, actor-adoption and target-authority checks. An ordinary request or RQ
job callback that reaches this module is refused before any durable mutation, so a
web/queue path cannot publish clinical photos or burn somebody's pending operation.

The coordinator owns both publication transactions and the whole attempt, never a
helper, the writer or a native hook:

1. **Preparation.** Inside one ``hold_exclusion`` scope for exactly this operation and
   its server-derived target, the committed row is re-read ``for_update`` (still
   ``SUBMITTED``), the actor/site/gate are re-checked, current native draft/patient
   permissions, authorship, the live expected draft version and the original binding
   are re-authorized, and ``journal.prepare`` stages the canonical key, generation and
   exact expected byte evidence. That transaction is committed here, before any
   protected byte exists.
2. **Publication.** The *same* live owner continues over its own committed ``PREPARED``
   evidence. Every binding is re-authorized again, then the exact unsaved File object
   enters the real protected writer (ordinary ``File.insert()``, native permissions,
   full hooks, one receipt), the immutable domain record and the server-owned draft
   pointer are created inside the bound commit context, the File is attached to that
   exact domain record, the receipt is consumed once
   against the exact expected linkage, ``journal.publish`` stages ``PUBLISHED``, and one
   commit makes File + version/original + draft + PUBLISHED durable together.

Outcomes are truthful and never optimistic:

* a *definite* rejection or draft conflict of still-``SUBMITTED`` work records
  ``ABORTED`` (no key, no bytes) and returns ``rejected``/``conflict``;
* any failure after the preparation commit — revocation, stale version, original
  substitution, native File veto, linkage mismatch, lost acknowledgement — rolls back
  only this transaction, keeps the committed ``PREPARED`` floor and reports ``unknown``.
  It never writes a second object, never appends a second version, never unlinks,
  truncates or reuses the reservation, and a retry never re-enters the writer;
* ``saved`` is returned only after a known commit *and* a coherent readback of the
  operation row, the persisted File, the immutable domain linkage and the actual bytes.

Not claimed here: installed Frappe/MariaDB transactions, native File/DocPerm behaviour,
the effective hook map, MariaDB isolation for the locking reads, or the real image
library. Those remain native acceptance evidence, and a green local run is not native
publication acceptance. No commit, rollback, service identity, permission bypass, gate
change or deployment action is performed by this module beyond the two coordinator-owned
commits and this attempt's own rollback.
"""

import contextlib
import hashlib
import os
from dataclasses import replace

from clinic_photos.access import frappe_context
from clinic_photos.publication import (
    exclusion,
    inspection,
    journal,
    process,
    submission,
    validation,
    writer,
)
from clinic_photos.publication.contracts import (
    ANNOTATION_APPEND,
    PreparedOperation,
    SaveOutcome,
    SubmittedOperation,
    VISIT_ORIGINAL_CREATE,
    visit_target_identity,
)

CHILD_MARKER_ATTR = "clinic_photos_child_publication"

# Process-bound child entry capabilities. A token exists only through
# ``child_process_publication`` in this process, and the coordinator re-verifies the
# registry identity, the PID and the clean execution frame at entry, so an ambient
# boolean or a copied value is not a publication capability.
_ENTRY_TOKENS: dict = {}


class _ChildEntry:
    """Opaque child-entry identity; construction is refused outside the factory."""

    __slots__ = ("_pid", "_site", "_sites_path", "_mode")

    def __init__(self, *args, **kwargs):
        raise TypeError("child publication entry is issued only by the child scope")


DRAFT_DOCTYPE = "Clinic Photo Draft"
PHOTO_DOCTYPE = "Clinic Photo"
VERSION_DOCTYPE = "Clinic Photo Annotation Version"
FILE_DOCTYPE = "File"

SAFE_COORDINATOR_DENIED = "Clinical photo publication is unavailable for this operation"
SAFE_COORDINATOR_CONFLICT = "Clinical photo publication conflicts with the current draft"
SAFE_COORDINATOR_UNKNOWN = "Clinical photo publication outcome is not known"


class CoordinatorError(Exception):
    """Fixed-message coordinator refusal; identifiers and payload are never included."""


class CoordinatorUnavailable(CoordinatorError):
    """This execution is not the fixed child entry; no durable mutation happened."""


class CoordinatorDenied(CoordinatorError):
    """A definite authorization/binding refusal of still-SUBMITTED work."""


class CoordinatorConflict(CoordinatorError):
    """A definite draft version conflict of still-SUBMITTED work."""


class CoordinatorUnknown(CoordinatorError):
    """The attempt's outcome is uncertain (commit, linkage or evidence)."""


@contextlib.contextmanager
def child_process_publication(*, mode="legacy"):
    """Mint this process's child publication capability for one verified scope.

    Called only by ``clinic_photos.publication.process`` after its bootstrap, site,
    enabled-gate, clean-context, persisted-actor and target-authority checks. The minted
    value is a process-bound identity (registry identity + PID + the site/sites path it
    bootstrapped), not a client capability and not a boolean: a request or job callback
    that never ran those checks holds nothing, and a copied or hand-made value is
    refused. The coordinator re-checks the clean execution frame at entry as well, so
    inheriting somebody's frame is not a way in.
    """
    import frappe

    if mode not in ("legacy", "visit"):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    existing = getattr(frappe.local, CHILD_MARKER_ATTR, None)
    if type(existing) is _ChildEntry and _ENTRY_TOKENS.get(id(existing)) is existing:
        if existing._mode != mode:
            raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
        yield existing
        return

    token = object.__new__(_ChildEntry)
    object.__setattr__(token, "_pid", os.getpid())
    object.__setattr__(token, "_mode", mode)
    object.__setattr__(
        token, "_site", getattr(frappe.local, "site", "") or ""
    )
    object.__setattr__(
        token, "_sites_path", getattr(frappe.local, "sites_path", None)
    )
    _ENTRY_TOKENS[id(token)] = token
    setattr(frappe.local, CHILD_MARKER_ATTR, token)
    try:
        yield token
    finally:
        _ENTRY_TOKENS.pop(id(token), None)
        try:
            setattr(frappe.local, CHILD_MARKER_ATTR, None)
        except BaseException:
            pass


def _require_child_entry(frappe, *, mode="legacy") -> None:
    """Refuse anything that is not this process's verified child entry, clean frame.

    The capability alone is not enough: a request, a queued job, an inherited
    callback queue or uncommitted writes mean this execution is not the fresh child the
    bootstrap verified, so nothing is staged.
    """
    token = getattr(frappe.local, CHILD_MARKER_ATTR, None)
    if type(token) is not _ChildEntry or _ENTRY_TOKENS.get(id(token)) is not token:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if token._mode != mode:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if token._pid != os.getpid():
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if token._site != (getattr(frappe.local, "site", "") or ""):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if token._sites_path != getattr(frappe.local, "sites_path", None):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    _require_clean_frame(frappe)


def _require_clean_frame(frappe) -> None:
    """No request, job, inherited callback or uncommitted write at this entry."""
    if getattr(frappe.local, "request", None) is not None:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if getattr(frappe.local, "job", None) is not None:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if not process._context_is_clean(frappe):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)


def _require_runtime(frappe, site: str) -> None:
    """The exact development site allowlist and the enabled feature gate, re-read."""
    if site not in process.ALLOWED_SITES:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    actual_site, sites_path = frappe_context.current_site_context()
    if actual_site != site:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if not isinstance(sites_path, str) or not os.path.isabs(sites_path):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    if os.path.realpath(sites_path) != sites_path:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)


def _require_gate(frappe) -> None:
    conf = getattr(frappe, "conf", None)
    if conf is None or conf.get(process.GATE_CONFIG_KEY) is not True:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)


def _commit() -> None:
    """The coordinator-owned commit boundary; helpers never commit."""
    import frappe

    frappe.db.commit()


def _rollback() -> None:
    """Discard only this attempt's staged transaction; the caller owns the attempt."""
    import frappe

    frappe.db.rollback()


def _reauthorize(fresh: SubmittedOperation) -> None:
    """Fresh domain/native target authority for this typed request; grants nothing."""
    try:
        submission.require_target_authority(fresh.request, fresh.actor, fresh.site)
    except submission.SubmissionUnavailable as error:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from error
    except submission.SubmissionError as error:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED) from error


def _live_draft(fresh: SubmittedOperation, *, for_update: bool):
    """The locked live draft row this attempt must still agree with."""
    import frappe

    if fresh.kind != ANNOTATION_APPEND:
        return None
    row = (
        frappe.db.get_value(
            DRAFT_DOCTYPE,
            fresh.request.draft,
            ["name", "author", "photo", "current_version_number"],
            as_dict=True,
            for_update=for_update,
        )
        or None
    )
    if row is None or (row.get("name") or "") != fresh.request.draft:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if (row.get("author") or "") != fresh.actor:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if (row.get("photo") or "") != fresh.request.original:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    live = row.get("current_version_number")
    if isinstance(live, bool) or not isinstance(live, int) or live < 0:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if live != fresh.request.expected_version:
        raise CoordinatorConflict(SAFE_COORDINATOR_CONFLICT)
    return row


def _require_prepared_bindings(prepared, fresh: SubmittedOperation) -> None:
    """The persisted reservation must be exactly this typed request's derivation."""
    if (
        prepared.payload_digest != fresh.payload_digest
        or prepared.kind != fresh.kind
        or prepared.draft != fresh.request.draft
        or prepared.expected_version != fresh.request.expected_version
        or prepared.synthetic_patient != fresh.request.synthetic_patient
        or prepared.generation != journal.PREPARED_GENERATION
    ):
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if prepared.expected_sha256 != journal._prepared_evidence(fresh.request)[0]:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if prepared.expected_size != journal._prepared_evidence(fresh.request)[1]:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)


def _payload_bytes(fresh: SubmittedOperation) -> bytes:
    if fresh.kind == ANNOTATION_APPEND:
        return fresh.request.annotation.encode("utf-8")
    return fresh.request.image_bytes


def _exact_file_document(prepared, data: bytes):
    """The exact unsaved File object this attempt may write: canonical, private, own."""
    import frappe

    file_doc = frappe.get_doc(
        {
            "doctype": "File",
            "file_url": prepared.key,
            "file_name": prepared.key.rsplit("/", 1)[-1],
            "is_private": 1,
            # A dict-built document is fetched-shaped by default: without this marker
            # native ``File.is_new()`` is False and the writer must refuse it.
            "__islocal": 1,
        }
    )
    file_doc.owner = prepared.actor
    file_doc.is_private = 1
    file_doc.content = data
    return file_doc


def _link_annotation(prepared, fresh, file_name):
    """Create the next immutable version and advance the draft, both bound."""
    import frappe

    next_version = (fresh.request.expected_version or 0) + 1
    with frappe_context.bound_publication(
        actor=prepared.actor,
        draft=prepared.draft,
        operation="pending_insert",
        storage_key=prepared.key,
    ):
        version = frappe.get_doc(
            {
                "doctype": VERSION_DOCTYPE,
                "draft": prepared.draft,
                "version_number": next_version,
                "annotation_file": "",
                "storage_key": prepared.key,
                "state": "pending",
            }
        )
        version.insert(ignore_permissions=True)
    version_name = version.name

    with frappe_context.bound_publication(
        actor=prepared.actor,
        draft=prepared.draft,
        operation="commit",
        storage_key=prepared.key,
    ):
        version.state = "committed"
        version.annotation_file = file_name
        version.save(ignore_permissions=True)

        draft = frappe.get_doc(DRAFT_DOCTYPE, prepared.draft)
        draft.current_version_number = next_version
        draft.current_version = version_name
        draft.save(ignore_permissions=True)

    return (
        writer.domain_linkage(prepared, VERSION_DOCTYPE, version_name, file_name),
        next_version,
    )


def _link_original(prepared, data, file_name):
    """Create the isolated synthetic original record for this exact object."""
    import frappe

    with frappe_context.bound_publication(
        actor=prepared.actor,
        operation="pending_insert",
        storage_key=prepared.key,
        synthetic_patient=prepared.synthetic_patient,
        operation_id=prepared.id,
    ):
        photo = frappe.get_doc(
            {
                "doctype": PHOTO_DOCTYPE,
                "patient_reference": prepared.synthetic_patient,
                "storage_key": prepared.key,
            }
        )
        photo.insert(ignore_permissions=True)
    photo_name = photo.name

    width, height = validation.image_dimensions(data)
    with frappe_context.bound_publication(
        actor=prepared.actor,
        operation="commit",
        storage_key=prepared.key,
        synthetic_patient=prepared.synthetic_patient,
        operation_id=prepared.id,
    ):
        photo.original_file = file_name
        photo.original_sha256 = prepared.expected_sha256
        photo.original_width = width
        photo.original_height = height
        photo.save(ignore_permissions=True)

    return writer.domain_linkage(prepared, PHOTO_DOCTYPE, photo_name, file_name), None


def _attach_domain_record(prepared, file_doc, file_name, linkage) -> None:
    """Attach the protected File to the exact domain record this transaction created.

    Native private ``File`` read is granted to a non-owner only through
    ``attached_to_doctype``/``attached_to_name``; an unattached private File stays
    owner-only even when a clinician may read the domain row. The target is the
    server-derived ``linkage`` of this publication (never a caller-supplied or
    pre-existing record), it is set on the same File object the writer inserted, and
    it is written inside this attempt's transaction after the domain row exists and
    before the receipt is consumed, so the ordinary File update guard runs with an
    unchanged canonical identity and an unconsumed first-write intent (no native
    duplicate retargeting is reached). The persisted row is then read back in the same
    transaction: a refused or silently partial metadata write discards this
    transaction instead of publishing a detached object.
    """
    import frappe

    with frappe_context.bound_publication(
        actor=prepared.actor,
        draft=prepared.draft or "",
        operation="pending_insert",
        operation_id=prepared.id if prepared.synthetic_patient else None,
        storage_key=prepared.key,
        synthetic_patient=prepared.synthetic_patient,
    ):
        file_doc.attached_to_doctype = linkage.doctype
        file_doc.attached_to_name = linkage.name
        file_doc.save(ignore_permissions=True)

    row = (
        frappe.db.get_value(
            FILE_DOCTYPE,
            file_name,
            ["attached_to_doctype", "attached_to_name"],
            as_dict=True,
        )
        or None
    )
    if (
        row is None
        or (row.get("attached_to_doctype") or "") != linkage.doctype
        or (row.get("attached_to_name") or "") != linkage.name
    ):
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)


def _same_reservation(left, right) -> bool:
    """Field equality of the same reservation evidence across PREPARED/PUBLISHED."""
    return (
        left.id,
        left.site,
        left.actor,
        left.kind,
        left.payload_digest,
        left.key,
        left.generation,
        left.draft,
        left.expected_version,
        left.synthetic_patient,
        left.target_identity,
        left.expected_sha256,
        left.expected_size,
    ) == (
        right.id,
        right.site,
        right.actor,
        right.kind,
        right.payload_digest,
        right.key,
        right.generation,
        right.draft,
        right.expected_version,
        right.synthetic_patient,
        right.target_identity,
        right.expected_sha256,
        right.expected_size,
    )


def _verify_saved(prepared, outcome) -> SaveOutcome:
    """Coherent post-commit readback; a failure is unknown, never saved.

    The persisted outcome — File, domain, version and creation identity — is compared
    to what this attempt actually consumed, then re-checked against the committed File
    row, the domain row, the draft pointer and the real bytes. Nothing is re-derived
    from the request when the persisted evidence is missing.
    """
    try:
        published = journal.load_published(prepared.id)
    except journal.JournalError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    if not _same_reservation(published.reservation, prepared):
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
    if published != outcome:
        # The durable outcome is not the one this attempt staged.
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)

    version = published.version
    if published.reservation.kind == ANNOTATION_APPEND:
        if version is None:
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
    elif version is not None:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)

    # One canonical File/domain/version coherence derivation, shared with read-only
    # status, so this attempt never carries a stronger private copy of the same proof.
    try:
        inspection.require_coherent_documents(published, for_update=True)
        # The attempt mode keeps Task 8's exact-current-head assertion: the pointer
        # must still be at this version. Read-only replay uses the historical mode.
        inspection.require_exact_current_head(published)
    except inspection.InspectionUnavailable:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

    try:
        writer.verify_published_object(
            published.reservation,
            published.file_name,
            creation_identity=published.creation_identity,
        )
    except writer.FirstObjectError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    except exclusion.ExclusionError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

    return SaveOutcome(
        "saved",
        published.reservation.id,
        version=version,
        file_name=published.file_name,
    )


def _abort_submitted(fresh: SubmittedOperation, error) -> SaveOutcome:
    """Record a definite rejection of still-SUBMITTED work; never writes bytes."""
    status = (
        "conflict" if isinstance(error, CoordinatorConflict) else "rejected"
    )
    try:
        journal.abort(fresh)
    except journal.JournalStateError:
        # The row advanced while this attempt was deciding: never rewrite it.
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    except journal.JournalError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    try:
        _commit()
    except BaseException:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    return SaveOutcome(status, fresh.id)


def _prepare_phase(fresh: SubmittedOperation):
    """Phase 1 under both locks: fresh authority, then one staged reservation."""
    import frappe

    try:
        _require_gate(frappe)
    except CoordinatorUnavailable as error:
        # The feature was disabled before this attempt entered its transaction: a
        # definite refusal of still-SUBMITTED work, not an uncertain outcome.
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED) from error
    _reauthorize(fresh)
    _live_draft(fresh, for_update=True)
    prepared = journal.prepare(fresh)
    _require_prepared_bindings(prepared, fresh)
    return prepared


def _publish_phase(owner, prepared, fresh: SubmittedOperation) -> SaveOutcome:
    """Phase 2 under the same live owner: bytes, linkage, attachment, one commit."""
    import frappe

    try:
        owner.continue_after_own_preparation(prepared)
        _require_gate(frappe)
        _reauthorize(fresh)
        _live_draft(fresh, for_update=True)
        _require_prepared_bindings(prepared, fresh)

        data = _payload_bytes(fresh)
        if (
            len(data) != prepared.expected_size
            or hashlib.sha256(data).hexdigest() != prepared.expected_sha256
        ):
            raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)

        with frappe_context.owned_publication_transaction(owner):
            file_doc = _exact_file_document(prepared, data)
            bound = writer.write_first_object(prepared, file_doc)
            if prepared.kind == ANNOTATION_APPEND:
                linkage, version = _link_annotation(prepared, fresh, bound.file_name)
            else:
                linkage, version = _link_original(prepared, data, bound.file_name)
            _attach_domain_record(prepared, file_doc, bound.file_name, linkage)
            file_name = writer.consume_persisted_object(
                prepared, bound, domain_linkage=linkage
            )
            published = journal.publish(prepared, bound, file_name, version)
    except BaseException:
        # Any failure after the preparation commit — revocation, stale version,
        # original substitution, a native File/domain veto, a linkage mismatch or an
        # uncertain helper failure — discards only this transaction. The committed
        # PREPARED floor, any bytes already created and the owner poison all stay.
        _discard_transaction()
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

    try:
        _commit()
    except BaseException:
        # The commit may or may not have taken effect: unknown until an authorized
        # readback, never a blind retry or a rollback-delete.
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

    return _verify_saved(prepared, published)


def _publish_prepared_visit(owner, prepared, data: bytes):
    """Private final transaction for an already-continued visit reservation.

    Returned journal outcome only means commit returned; it is not a saved/readback
    claim. No external dispatcher invokes this internal function.
    """
    import frappe
    from clinic_photos.visit import capture

    if (not isinstance(prepared, PreparedOperation) or
        prepared.kind != VISIT_ORIGINAL_CREATE or prepared.state != "PREPARED" or
        type(data) is not bytes or len(data) != prepared.expected_size or
        hashlib.sha256(data).hexdigest() != prepared.expected_sha256):
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
    try:
        if writer._live_owner(prepared) is not owner:
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
        owner.require_continued()
        with frappe_context.owned_publication_transaction(owner):
            db = frappe.local.db
            bound = None
            try:
                writer._require_persisted_prepared(prepared)
                capture.check_slot_availability(prepared.visit_binding)
                file_doc = _exact_file_document(prepared, data)
                bound = writer.write_first_object(prepared, file_doc)
                width, height = validation.image_dimensions(data)
                photo = frappe.get_doc({
                    "doctype": PHOTO_DOCTYPE,
                    "patient_reference": prepared.visit_binding.patient,
                    "storage_key": prepared.key,
                    "original_file": bound.file_name,
                    "original_sha256": prepared.expected_sha256,
                    "original_width": width,
                    "original_height": height,
                })
                with frappe_context.visit_photo_insertion(prepared, bound, file_doc, photo):
                    photo.insert(ignore_permissions=True)
                linkage = writer.domain_linkage(prepared, PHOTO_DOCTYPE, photo.name, bound.file_name)
                with frappe_context.visit_file_attachment(prepared, bound, file_doc, photo):
                    file_doc.attached_to_doctype = PHOTO_DOCTYPE
                    file_doc.attached_to_name = photo.name
                    previous_ignore = getattr(file_doc.flags, "ignore_permissions", None)
                    if previous_ignore:
                        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
                    try:
                        file_doc.save(ignore_permissions=True)
                    finally:
                        file_doc.flags.ignore_permissions = previous_ignore
                file_name = writer.consume_persisted_object(prepared, bound, domain_linkage=linkage)
                capture.insert_prepared_slot_link(prepared, bound)
                published = journal.publish(prepared, bound, file_name, None)
                writer.arm_visit_final_commit(prepared, bound)
            except BaseException:
                try:
                    writer.rollback_visit_transaction(prepared, owner, db)
                except BaseException:
                    pass
                owner.poison("visit_publication_uncertain")
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
            try:
                writer.finish_visit_transaction(prepared, owner, "commit")
            except BaseException:
                owner.poison("visit_commit_uncertain")
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
            return published
    except CoordinatorUnknown:
        raise
    except BaseException:
        owner.poison("visit_publication_uncertain")
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None


def _readback_visit(fresh, prepared, staged):
    """Verify committed visit identity in a clean read transaction, without public reads."""
    import frappe

    if (type(getattr(frappe.db, "transaction_writes", None)) is not int or
        frappe.db.transaction_writes != 0 or
        (frappe.session.user or "") != fresh.actor or
        (getattr(frappe.local, "site", "") or "") != fresh.site):
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
    try:
        verified = journal.load_verified_operation(fresh.id)
        published = verified.published
        if (verified.state != "PUBLISHED" or verified.kind != VISIT_ORIGINAL_CREATE or
            verified.id != fresh.id or verified.request_key != fresh.request_key or
            verified.actor != fresh.actor or verified.site != fresh.site or
            verified.request != fresh.request or verified.payload_digest != fresh.payload_digest or
            not published or published != staged or
            published.reservation != replace(prepared, state="PUBLISHED") or
            published.domain_doctype != PHOTO_DOCTYPE):
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
        photos = frappe.db.get_all(
            PHOTO_DOCTYPE, filters={"storage_key": prepared.key},
            fields=["name", "storage_key", "patient_reference", "original_file", "original_sha256"],
            limit=0,
        )
        if (len(photos) != 1 or photos[0].get("name") != published.domain_name or
            photos[0].get("storage_key") != prepared.key or
            photos[0].get("patient_reference") != prepared.visit_binding.patient or
            photos[0].get("original_file") != published.file_name or
            photos[0].get("original_sha256") != prepared.expected_sha256):
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
        file_row = frappe.db.get_value(
            FILE_DOCTYPE, published.file_name,
            ["name", "file_url", "file_name", "is_private", "owner", "file_size",
             "attached_to_doctype", "attached_to_name", "attached_to_field"], as_dict=True,
        )
        if (not file_row or file_row.get("name") != published.file_name or
            file_row.get("file_url") != prepared.key or
            file_row.get("file_name") != prepared.key.rsplit("/", 1)[-1] or
            file_row.get("is_private") != 1 or file_row.get("owner") != prepared.actor or
            file_row.get("file_size") != prepared.expected_size or
            file_row.get("attached_to_doctype") != PHOTO_DOCTYPE or
            file_row.get("attached_to_name") != published.domain_name or
            file_row.get("attached_to_field") not in (None, "")):
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
        writer.verify_published_object(
            published.reservation, published.file_name, creation_identity=published.creation_identity,
        )
        return SaveOutcome("saved", prepared.id, file_name=published.file_name)
    except BaseException:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None


def _execute_private_visit(submission):
    """Private attempt; future dispatch must separately authorize its entry."""
    import frappe
    from clinic_photos.visit import capture

    if (not isinstance(submission, SubmittedOperation) or
        submission.kind != VISIT_ORIGINAL_CREATE or
        type(getattr(frappe.db, "transaction_writes", None)) is not int or
        frappe.db.transaction_writes != 0 or
        (frappe.session.user or "") != submission.actor or
        (getattr(frappe.local, "site", "") or "") != submission.site):
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
    try:
        target = exclusion.target_identity_for_operation(submission.id)
        if target != visit_target_identity(submission.request.visit_binding):
            raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
        with exclusion.hold_exclusion(submission.id, target) as owner:
            db = frappe.local.db
            rejected = False
            try:
                fresh = journal.load_submission(submission.id, for_update=True)
                if fresh != submission:
                    raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
                try:
                    capture.check_slot_availability(fresh.request.visit_binding)
                except capture.VisitEligibilityRejected:
                    if (getattr(frappe.local, "db", None) is not db or
                        type(getattr(frappe.db, "transaction_writes", None)) is not int or
                        frappe.db.transaction_writes != 0 or
                        (frappe.session.user or "") != fresh.actor or
                        (getattr(frappe.local, "site", "") or "") != fresh.site):
                        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
                    journal.abort(fresh)
                    rejected = True
                if not rejected:
                    prepared = journal.prepare(fresh)
                    _require_prepared_bindings(prepared, fresh)
                    if (prepared.id != fresh.id or prepared.actor != fresh.actor or
                        prepared.site != fresh.site or prepared.visit_binding != fresh.request.visit_binding or
                        prepared.target_identity != target):
                        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
            except BaseException:
                try:
                    _rollback()
                except BaseException:
                    pass
                owner.poison("visit_preparation_uncertain")
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
            if rejected:
                try:
                    _commit()
                except BaseException:
                    owner.poison("visit_abort_commit_uncertain")
                    raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
                return SaveOutcome("rejected", fresh.id)
            try:
                _commit()
                owner.continue_after_own_preparation(prepared)
            except BaseException:
                owner.poison("visit_preparation_commit_uncertain")
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
            staged = _publish_prepared_visit(owner, prepared, fresh.request.image_bytes)
            if getattr(frappe.local, "db", None) is not db:
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN)
            return _readback_visit(fresh, prepared, staged)
    except BaseException:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None


def _discard_transaction() -> None:
    """Discard this attempt's staged rows; never unlinks protected bytes."""
    try:
        _rollback()
    except BaseException:
        # A failed rollback is additional uncertainty, not a reason to mask it.
        return


def _execute(fresh: SubmittedOperation) -> SaveOutcome:
    try:
        target = exclusion.target_identity_for_operation(fresh.id)
    except exclusion.ExclusionError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

    try:
        with exclusion.hold_exclusion(fresh.id, target) as owner:
            try:
                prepared = _prepare_phase(fresh)
            except (CoordinatorDenied, CoordinatorConflict) as denial:
                return _abort_submitted(fresh, denial)

            try:
                _commit()
            except BaseException:
                raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None

            return _publish_phase(owner, prepared, fresh)
    except exclusion.ExclusionNoRetry:
        # A definite advanced state spends this attempt; never retried here.
        return SaveOutcome("unknown", fresh.id)
    except exclusion.ExclusionBusy:
        # Another writer holds the objects; takeover is never attempted.
        return SaveOutcome("unknown", fresh.id)
    except exclusion.ExclusionError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None


def publish_visit_submission(submission_operation: SubmittedOperation) -> SaveOutcome:
    """Visit child only; fresh gate, actor and durable submission are rechecked."""
    import frappe

    _require_child_entry(frappe, mode="visit")
    if (not isinstance(submission_operation, SubmittedOperation) or
        submission_operation.kind != VISIT_ORIGINAL_CREATE or
        not process._valid_visit_site(submission_operation.site)):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    site, sites_path = frappe_context.current_site_context()
    if (site != submission_operation.site or
        (frappe.session.user or "") != submission_operation.actor or
        not isinstance(sites_path, str) or not os.path.isabs(sites_path) or
        os.path.realpath(sites_path) != sites_path or
        not process._visit_gate_enabled(site, getattr(frappe, "conf", None))):
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    try:
        fresh = journal.load_submission(submission_operation.id)
    except journal.JournalError:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED) from None
    if fresh != submission_operation:
        raise CoordinatorUnavailable(SAFE_COORDINATOR_DENIED)
    _require_clean_frame(frappe)
    return _execute_private_visit(fresh)


def publish_submission(submission_operation: SubmittedOperation) -> SaveOutcome:
    """Publish one committed SUBMITTED operation; the sole publication entry."""
    import frappe

    if not isinstance(submission_operation, SubmittedOperation):
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    if submission_operation.kind == VISIT_ORIGINAL_CREATE:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)
    _require_child_entry(frappe)
    _require_runtime(frappe, submission_operation.site)
    _require_gate(frappe)

    try:
        fresh = journal.load_submission(submission_operation.id)
    except journal.JournalStateError:
        # The operation already advanced (or is terminal): this execution may not
        # resume it, and the durable outcome is not known to this caller.
        return SaveOutcome("unknown", submission_operation.id)
    except journal.JournalError:
        raise CoordinatorUnknown(SAFE_COORDINATOR_UNKNOWN) from None
    if fresh != submission_operation:
        raise CoordinatorDenied(SAFE_COORDINATOR_DENIED)

    try:
        return _execute(fresh)
    except CoordinatorUnknown:
        return SaveOutcome("unknown", fresh.id)
