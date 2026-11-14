"""Durable SUBMITTED/PREPARED operation journal.

A SUBMITTED row is payload acceptance only. It carries the server-bound identity and
the bounded request content, never a protected storage key, generation, File link or
receipt. Accepted image bytes are staged and fsynced by ``publication.staging`` before
the row is inserted, and the row references them by metadata only (staged key,
SHA-256, size and format): neither the bytes nor their base64 are ever persisted. The
repository never commits or rolls back: the caller owns the transaction, so an
accepted row exists only if that transaction commits.

``prepare`` is the durable reservation step. It runs only while the caller holds the
operation/target exclusion for exactly this operation, re-reads the row ``for_update``
under those locks, derives the canonical private key, the persisted preparation
generation, the target identity and the exact expected byte hash/size from the
validated server-side request, and stages one ``PREPARED`` update in the caller's
transaction. It never allocates a key, generation or path from client input, never
writes File/domain rows and never commits; the committed PREPARED row is what protects
the key, including after a later publication rollback.

Duplicate detection is by one collision-safe digest of ``(site, actor, kind,
request_key)`` stored under a unique index. The payload digest is derived from the
canonical annotation text or the exact decoded image bytes, so wire spacing cannot
change it. The pre-insert lookup is a plain read; the unique index is the arbiter, and
only the recovery read locks. A repeated tuple with the same payload returns the
existing operation ID while the row is still SUBMITTED, and resolves the original ID
with an explicit state refusal after it advances; a different payload on the same
tuple is a fixed-message conflict and cannot replace the stored request.

An image row is loaded back only through the staged reference it holds: the metadata is
revalidated, the object is re-opened inside the bound site's staging area and its bytes
are authenticated by SHA-256, size and format before the typed request is rebuilt. A
legacy row that stores ``image_base64`` and a row with no staged reference both refuse
with the fixed integrity message: they are never read back, migrated, re-staged or
published.

No Frappe import happens at module import time. Mutation is gated here by a fresh
enabled-clinician session and site check; the DocType itself declares no client
permissions. ``publish`` stages ``PUBLISHED`` together with the durable File, domain,
version and creation-identity evidence of this exact attempt's *consumed* first object,
under the live owner that prepared it and continued over its own committed PREPARED
evidence; it never commits and never writes a File or domain record, so the coordinator
can commit the coherent outcome atomically.
"""

import hashlib
from dataclasses import dataclass

from clinic_photos.access import frappe_context, storage
from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES
from clinic_photos.publication import exclusion, staging
from clinic_photos.publication.contracts import (
    ANNOTATION_APPEND,
    DURABLE_STATES,
    SUBMITTED_STATE,
    SYNTHETIC_ORIGINAL_CREATE,
    PreparedOperation,
    PublicationValidationError,
    PublishedOutcome,
    SaveRequest,
    SubmittedOperation,
)

OPERATION_DOCTYPE = "Clinic Photo Save Operation"
PREPARED_STATE = "PREPARED"
PUBLISHED_STATE = "PUBLISHED"
ABORTED_STATE = "ABORTED"
QUARANTINED_STATE = "QUARANTINED"

DOMAIN_DOCTYPES = {
    ANNOTATION_APPEND: "Clinic Photo Annotation Version",
    SYNTHETIC_ORIGINAL_CREATE: "Clinic Photo",
}

# Bounded identity registry of the exact ``PreparedOperation`` values this process
# issued from a verified PREPARED row. Continuation accepts only a registered
# instance, so a field-equal dataclass a caller constructs itself can never continue
# an attempt. Entries are held strongly (identity, not ``id()`` reuse) and evicted
# oldest-first; an evicted handle simply refuses, which fails closed.
_ISSUED_PREPARATION_LIMIT = 32
_ISSUED_PREPARATIONS: dict = {}

SAFE_JOURNAL_DENIAL = "Durable photo-save operation is unavailable"
SAFE_JOURNAL_CONFLICT = "Durable photo-save request conflicts with an existing operation"
SAFE_JOURNAL_INTEGRITY = "Durable photo-save operation is inconsistent"
SAFE_JOURNAL_MISSING = "Durable photo-save operation was not found"
SAFE_JOURNAL_STATE = "Durable photo-save operation is not in the SUBMITTED state"

_IDENTITY_DOMAIN = b"clinic-photos-submission-identity-v1"
_PAYLOAD_DOMAIN = b"clinic-photos-submission-payload-v1"
_RESERVATION_KEY_DOMAIN = b"clinic-photos-prepared-reservation-key-v1"

# One SUBMITTED operation can transition to PREPARED exactly once, so the durable
# preparation generation of that transition is fixed. The exclusion owner's
# ``attempt_generation`` is a process-local ordering counter and is deliberately not
# persisted as authority.
PREPARED_GENERATION = 1

_RESERVATION_BASENAMES = {
    ANNOTATION_APPEND: "clinic-annotation-",
    SYNTHETIC_ORIGINAL_CREATE: "clinic-photo-",
}
# The metadata-only staged reference persisted for an accepted image. The row names
# the object in the bound site's private staging area; it never carries the bytes.
# ``staged_size`` is an ``Int`` column, so an installed database materialises an unset
# value as ``0`` rather than ``NULL``: presence for it must use absent-int semantics.
_STAGED_TEXT_FIELDS = ("staged_key", "staged_sha256", "staged_format")
_STAGED_FIELDS = ("staged_key", "staged_sha256", "staged_size", "staged_format")
_IMAGE_SIGNATURES = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
)


class JournalError(Exception):
    """Fixed-message durable-journal refusal."""


class JournalAuthorityDenied(JournalError):
    pass


class JournalConflict(JournalError):
    """Same identity tuple with a different payload; the stored request wins.

    ``operation_id`` is the existing opaque ID so a later submission boundary can
    return a conflict outcome without guessing or mutating the stored request.
    """

    def __init__(self, message: str = SAFE_JOURNAL_CONFLICT, *, operation_id=None):
        super().__init__(message)
        self.operation_id = operation_id


class JournalIntegrityError(JournalError):
    pass


class JournalStagingUnavailable(JournalError):
    """The accepted image cannot be staged durably; nothing was persisted."""


class JournalOperationMissing(JournalError):
    pass


class JournalStateError(JournalError):
    """A verified existing operation is not SUBMITTED; no payload is disclosed."""

    def __init__(
        self, message: str = SAFE_JOURNAL_STATE, *, operation_id=None, state=None
    ):
        super().__init__(message)
        self.operation_id = operation_id
        self.state = state


def _framed(value: bytes) -> bytes:
    return len(value).to_bytes(8, "big") + value


def _digest(domain: bytes, parts) -> str:
    """Collision-safe encoding: each present/absent part is length-prefixed."""
    digest = hashlib.sha256()
    digest.update(_framed(domain))
    for part in parts:
        if part is None:
            digest.update(b"\x00")
        else:
            digest.update(b"\x01")
            digest.update(_framed(part))
    return digest.hexdigest()


def _utf8(value) -> bytes:
    if not isinstance(value, str):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    try:
        return value.encode("utf-8")
    except UnicodeEncodeError:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def _optional_text(value):
    return None if value is None else _utf8(value)


def _optional_int(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return b"i" + str(value).encode("ascii")


def identity_digest(site: str, actor: str, kind: str, request_key: str) -> str:
    """Digest of the unique duplicate-detection tuple, including its domain."""
    return _digest(
        _IDENTITY_DOMAIN,
        [_utf8(site), _utf8(actor), _utf8(kind), _utf8(request_key)],
    )


def payload_digest(request: SaveRequest) -> str:
    """Semantic digest of one typed request; wire spelling cannot change it."""
    if not isinstance(request, SaveRequest):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    parts = [_utf8(request.kind), _utf8(request.request_key)]
    if request.kind == ANNOTATION_APPEND:
        parts.extend(
            (
                _optional_text(request.draft),
                _optional_int(request.expected_version),
                _optional_text(request.original),
                _optional_text(request.annotation),
            )
        )
    elif request.kind == SYNTHETIC_ORIGINAL_CREATE:
        parts.extend(
            (
                _optional_text(request.synthetic_patient),
                request.image_bytes,
            )
        )
    else:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return _digest(_PAYLOAD_DOMAIN, parts)


def _clean_text(value) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and value.strip() == value
    )


def require_service_authorization(actor: str, site: str) -> None:
    """Fresh server-side check; client flags, owners and capabilities grant nothing.

    The operation controller reuses this on ``before_insert`` so a native
    Administrator bypass cannot create a forged submission outside the repository.
    """
    import frappe

    if not _clean_text(actor) or not _clean_text(site):
        raise JournalAuthorityDenied(SAFE_JOURNAL_DENIAL)
    if (getattr(frappe.local, "site", "") or "") != site:
        raise JournalAuthorityDenied(SAFE_JOURNAL_DENIAL)

    fresh = frappe_context.current_actor()
    if (
        fresh.user != actor
        or not fresh.enabled
        or not fresh.clinician
        or actor in NON_CLINICAL_SESSION_IDENTITIES
    ):
        raise JournalAuthorityDenied(SAFE_JOURNAL_DENIAL)


def _row_get(row, fieldname):
    getter = getattr(row, "get", None)
    if callable(getter):
        value = getter(fieldname)
        if value is not None:
            return value
    return getattr(row, fieldname, None)


def _row_text(row, fieldname) -> str:
    value = _row_get(row, fieldname)
    if not _clean_text(value):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return value


def _row_present(row, fieldname) -> bool:
    value = _row_get(row, fieldname)
    return value not in (None, "")


def _row_absent_int(row, fieldname) -> bool:
    """Absent integer evidence: ``None``, ``0`` or empty, but never ``True``."""
    value = _row_get(row, fieldname)
    if isinstance(value, bool):
        return False
    return value in (None, 0, "")


def _request_from_row(row, kind: str, request_key: str) -> SaveRequest:
    if kind == ANNOTATION_APPEND:
        if (
            _row_present(row, "image_base64")
            or _row_present(row, "synthetic_patient")
            or any(_row_present(row, fieldname) for fieldname in _STAGED_TEXT_FIELDS)
            or not _row_absent_int(row, "staged_size")
        ):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        expected_version = _row_get(row, "expected_version")
        if (
            isinstance(expected_version, bool)
            or not isinstance(expected_version, int)
            or expected_version < 0
        ):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        return SaveRequest(
            kind=kind,
            request_key=request_key,
            draft=_row_text(row, "draft"),
            expected_version=expected_version,
            original=_row_text(row, "original"),
            annotation=_row_text(row, "annotation_json"),
        )

    if kind == SYNTHETIC_ORIGINAL_CREATE:
        if _row_present(row, "image_base64"):
            # A legacy row that stored the image bytes in the database. It is refused,
            # never read back, migrated or republished by this module.
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if (
            _row_present(row, "annotation_json")
            or _row_present(row, "draft")
            or _row_present(row, "original")
        ):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if _row_get(row, "expected_version") not in (None, 0):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        return SaveRequest(
            kind=kind,
            request_key=request_key,
            synthetic_patient=_row_text(row, "synthetic_patient"),
            image_bytes=_staged_payload(_staged_from_row(row)),
        )

    raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)


def _staged_from_row(row) -> staging.StagedImage:
    """The metadata-only staged reference stored on one synthetic row.

    A row without a complete, well-formed reference (an image-reference-less row) is
    inconsistent evidence, not something to re-derive or re-stage.
    """
    size = _row_get(row, "staged_size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    try:
        return staging.StagedImage(
            key=_row_text(row, "staged_key"),
            sha256=_row_text(row, "staged_sha256"),
            size=size,
            format=_row_text(row, "staged_format"),
        )
    except staging.StagedImageRefused:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def _staged_payload(staged: staging.StagedImage) -> bytes:
    """Read back the authenticated staged bytes this row's metadata names."""
    try:
        return staging.read_staged_image(staged)
    except staging.StagedImageRefused:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


@dataclass(frozen=True)
class _VerifiedOperation:
    id: str
    site: str
    actor: str
    kind: str
    request_key: str
    payload_digest: str
    request: SaveRequest
    state: str
    reservation_key: str | None
    row: object


def _verified_operation(row) -> _VerifiedOperation:
    """Verify immutable identity and payload independently of stage state.

    This deliberately accepts any named durable state and an allocated reservation
    key: a retry of an advanced row must still resolve its original identity and
    payload. Only ``_submitted_operation`` applies the SUBMITTED-only shape.
    """
    try:
        name = _row_text(row, "name")
        site = _row_text(row, "site")
        actor = _row_text(row, "actor")
        kind = _row_text(row, "kind")
        request_key = _row_text(row, "request_key")
        state = _row_text(row, "state")
        stored_identity = _row_text(row, "identity_digest")
        stored_payload = _row_text(row, "payload_digest")

        if state not in DURABLE_STATES:
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if stored_identity != identity_digest(site, actor, kind, request_key):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

        request = _request_from_row(row, kind, request_key)
        if payload_digest(request) != stored_payload:
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

        reservation_key = _row_get(row, "reservation_key")
        if reservation_key in (None, ""):
            reservation_key = None
        elif not _clean_text(reservation_key):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

        return _VerifiedOperation(
            id=name,
            site=site,
            actor=actor,
            kind=kind,
            request_key=request_key,
            payload_digest=stored_payload,
            request=request,
            state=state,
            reservation_key=reservation_key,
            row=row,
        )
    except (JournalIntegrityError, JournalStateError):
        raise
    except (PublicationValidationError, TypeError, ValueError, UnicodeError):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def _submitted_operation(verified: _VerifiedOperation) -> SubmittedOperation:
    """Apply the SUBMITTED-only shape after identity and payload verification."""
    if verified.state != SUBMITTED_STATE:
        raise JournalStateError(
            SAFE_JOURNAL_STATE,
            operation_id=verified.id,
            state=verified.state,
        )
    if verified.reservation_key is not None:
        # A protected reservation may exist only after this SUBMITTED stage.
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if (
        _row_present(verified.row, "expected_sha256")
        or not _row_absent_int(verified.row, "generation")
        or not _row_absent_int(verified.row, "expected_size")
    ):
        # Prepared byte/generation evidence on a SUBMITTED row is inconsistent.
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    try:
        return SubmittedOperation(
            id=verified.id,
            site=verified.site,
            actor=verified.actor,
            kind=verified.kind,
            request_key=verified.request_key,
            payload_digest=verified.payload_digest,
            request=verified.request,
            state=verified.state,
        )
    except PublicationValidationError:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def _submission_from_row(row) -> SubmittedOperation:
    """Strict SUBMITTED load used by the worker after identity/payload verification."""
    return _submitted_operation(_verified_operation(row))


def assert_submission_row(row) -> None:
    """Controller-side integrity check for the exact stored SUBMITTED shape."""
    _submission_from_row(row)


def _resolve_existing(row, expected_payload_digest: str) -> SubmittedOperation:
    """Resolve a duplicate tuple by verified payload first, then stage state."""
    verified = _verified_operation(row)
    if verified.payload_digest != expected_payload_digest:
        raise JournalConflict(
            SAFE_JOURNAL_CONFLICT, operation_id=verified.id
        )
    return _submitted_operation(verified)


def _document_values(
    request: SaveRequest,
    *,
    actor: str,
    site: str,
    identity: str,
    payload: str,
    staged,
) -> dict:
    values = {
        "doctype": OPERATION_DOCTYPE,
        "site": site,
        "actor": actor,
        "kind": request.kind,
        "request_key": request.request_key,
        "identity_digest": identity,
        "payload_digest": payload,
        "state": SUBMITTED_STATE,
        "draft": request.draft,
        "expected_version": request.expected_version,
        "original": request.original,
        "synthetic_patient": request.synthetic_patient,
        "annotation_json": request.annotation,
        "reservation_key": None,
    }
    values.update(_staged_values(staged))
    return values


def _staged_values(staged) -> dict:
    """Metadata reference only: the accepted bytes stay in staging, never in the row."""
    if staged is None:
        return {fieldname: None for fieldname in _STAGED_FIELDS}
    return {
        "staged_key": staged.key,
        "staged_sha256": staged.sha256,
        "staged_size": staged.size,
        "staged_format": staged.format,
    }


def _stage_accepted_image(request: SaveRequest):
    """Stage the accepted image once, durably, before any metadata row exists."""
    if request.kind == ANNOTATION_APPEND:
        return None
    if request.kind != SYNTHETIC_ORIGINAL_CREATE:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    try:
        return staging.stage_image(request.image_bytes)
    except staging.StagingError:
        raise JournalStagingUnavailable(SAFE_JOURNAL_DENIAL) from None


def stage_submission(
    request: SaveRequest, *, actor: str, site: str
) -> SubmittedOperation:
    """Persist one SUBMITTED row in the caller's transaction.

    A repeated tuple with the same payload returns the existing operation ID while
    the row is SUBMITTED; after it advances, the verified original ID is returned in
    a fixed state refusal. A different payload for the same tuple raises a fixed
    conflict and leaves the row untouched. An accepted image is staged and fsynced
    before the row exists, and the row names it by metadata only. No commit,
    rollback, File write or key reservation happens here.
    """
    if not isinstance(request, SaveRequest):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    require_service_authorization(actor, site)

    identity = identity_digest(site, actor, request.kind, request.request_key)
    payload = payload_digest(request)

    existing = frappe_context.get_save_operation_by_identity(identity)
    if existing is not None:
        return _resolve_existing(existing, payload)

    # The accepted image is staged and fsynced before this row exists, so the row can
    # name it by metadata only. A refusal here persists nothing.
    staged = _stage_accepted_image(request)

    import frappe

    try:
        operation = frappe.get_doc(
            _document_values(
                request,
                actor=actor,
                site=site,
                identity=identity,
                payload=payload,
                staged=staged,
            )
        )
        operation.insert(ignore_permissions=True)
    except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
        # The unique identity index is the arbiter. A locking recovery read sees the
        # committed winner even under REPEATABLE READ; never overwrite or blindly retry.
        existing = frappe_context.get_save_operation_by_identity(
            identity, for_update=True
        )
        if existing is None:
            raise JournalConflict(SAFE_JOURNAL_CONFLICT) from None
        return _resolve_existing(existing, payload)

    return _submission_from_row(operation)


def load_submission(
    operation_id: str, *, for_update: bool = False
) -> SubmittedOperation:
    """Load and verify one SUBMITTED operation; never mutates or commits."""
    if not _clean_text(operation_id):
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    row = frappe_context.get_save_operation(operation_id, for_update=for_update)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    return _submission_from_row(row)


# ---------------------------------------------------------------------------
# Read-only verified identity for any durable state (status/replay/inspection)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class VerifiedOperation:
    """Server-verified identity of one operation in any durable state.

    ``request`` exists for internal server use only (it carries the accepted payload);
    a client response must never serialize it. The remaining values are the
    operation-bound audit evidence: opaque identity, kind and the two digests.

    ``published`` is the immutable outcome derived from this same verified row for a
    ``PUBLISHED`` operation (``None`` otherwise). Status and audit evidence use this
    single derivation instead of re-reading a raw row; it is never a mutable row
    reference and never a saved claim on its own.
    """

    id: str
    site: str
    actor: str
    kind: str
    request_key: str
    payload_digest: str
    state: str
    request: SaveRequest
    published: PublishedOutcome | None = None


def _aborted_operation(verified: _VerifiedOperation) -> None:
    """An ``ABORTED`` row carries no key, byte evidence or published outcome."""
    row = verified.row
    if verified.reservation_key is not None:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if (
        _row_present(row, "expected_sha256")
        or not _row_absent_int(row, "generation")
        or not _row_absent_int(row, "expected_size")
        or not _row_absent_int(row, "published_version")
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    for fieldname in (
        "published_file",
        "published_domain_doctype",
        "published_domain_name",
        "creation_identity",
    ):
        if _row_present(row, fieldname):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)


def _verified_for_state(verified: _VerifiedOperation) -> PublishedOutcome | None:
    """Enforce the strict durable shape of this row's own state.

    Returns the immutable published outcome derived from this same row (only for
    ``PUBLISHED``); every other state returns ``None``.
    """
    if verified.state == SUBMITTED_STATE:
        _submitted_operation(verified)
    elif verified.state == PREPARED_STATE:
        _prepared_operation(verified.row)
    elif verified.state == PUBLISHED_STATE:
        return _published_outcome(verified.row)
    elif verified.state == ABORTED_STATE:
        _aborted_operation(verified)
    elif verified.state == QUARANTINED_STATE:
        # Quarantine is a read-only holding state: identity and payload are verified
        # above, and no key/byte/outcome shape is inferred or repaired here.
        return None
    else:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return None


def load_verified_operation(
    operation_id: str, *, for_update: bool = False
) -> VerifiedOperation:
    """Read the verified identity of one operation in any durable state.

    Never mutates, commits or rolls back. Each state's strict shape is enforced, so a
    tampered or incomplete advanced row is integrity evidence rather than a weaker
    claim: a PREPARED row without this identity's derived key/byte evidence, or an
    ABORTED row carrying a reservation, refuses instead of reporting a state.
    """
    if not _clean_text(operation_id):
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    row = frappe_context.get_save_operation(operation_id, for_update=for_update)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    verified = _verified_operation(row)
    published = _verified_for_state(verified)
    return VerifiedOperation(
        id=verified.id,
        site=verified.site,
        actor=verified.actor,
        kind=verified.kind,
        request_key=verified.request_key,
        payload_digest=verified.payload_digest,
        state=verified.state,
        request=verified.request,
        published=published,
    )


# ---------------------------------------------------------------------------
# PREPARED reservation (durable key identity before any File/domain row)
# ---------------------------------------------------------------------------
def _image_suffix(data: bytes) -> str:
    for signature, suffix in _IMAGE_SIGNATURES:
        if data.startswith(signature):
            return suffix
    # The typed request already passed native image validation; an unrecognised
    # signature here is inconsistent evidence, not a new client failure.
    raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)


def _prepared_evidence(request: SaveRequest) -> tuple[str, int]:
    """Exact expected byte evidence of the immutable request content."""
    if request.kind == ANNOTATION_APPEND:
        data = _utf8(request.annotation)
    elif request.kind == SYNTHETIC_ORIGINAL_CREATE:
        data = request.image_bytes
        if not isinstance(data, bytes) or not data:
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    else:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return hashlib.sha256(data).hexdigest(), len(data)


def _reservation_key(
    request: SaveRequest,
    *,
    site: str,
    operation_id: str,
    generation: int,
    payload: str,
) -> str:
    """Deterministic canonical private key derived from server-owned identity only."""
    digest = _digest(
        _RESERVATION_KEY_DOMAIN,
        [
            _utf8(site),
            _utf8(operation_id),
            _optional_int(generation),
            _utf8(request.kind),
            _utf8(payload),
        ],
    )
    suffix = (
        ".json"
        if request.kind == ANNOTATION_APPEND
        else _image_suffix(request.image_bytes)
    )
    return (
        storage.PRIVATE_URL_PREFIX
        + _RESERVATION_BASENAMES[request.kind]
        + digest[:32]
        + suffix
    )


def target_identity(request: SaveRequest) -> str:
    """Server-derived exclusion target of one typed request."""
    if request.kind == ANNOTATION_APPEND:
        return "draft:" + request.draft
    if request.kind == SYNTHETIC_ORIGINAL_CREATE:
        return "patient-creation:" + request.synthetic_patient
    raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)


def _prepared_operation(row) -> PreparedOperation:
    """Verify one persisted PREPARED row and return its immutable evidence value."""
    return _reservation_operation(row, PREPARED_STATE)


def _creation_identity(row) -> tuple[str, int, int]:
    """Verified ``dev:inode`` provenance of the persisted publication outcome."""
    value = _row_text(row, "creation_identity")
    parts = value.split(":")
    if len(parts) != 2:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    try:
        device, inode = int(parts[0]), int(parts[1])
    except ValueError:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None
    if device < 0 or inode < 0:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return value, device, inode


def _published_outcome(row) -> PublishedOutcome:
    """Verify one persisted PUBLISHED row, including its durable outcome evidence.

    A committed PUBLISHED row must carry the File, domain, version and creation
    identity the publication produced; a missing or inconsistent one is integrity
    evidence, never something to re-derive silently.
    """
    reservation = _reservation_operation(row, PUBLISHED_STATE)

    file_name = _row_text(row, "published_file")
    domain_doctype = _row_text(row, "published_domain_doctype")
    if domain_doctype != DOMAIN_DOCTYPES[reservation.kind]:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    domain_name = _row_text(row, "published_domain_name")

    if reservation.kind == ANNOTATION_APPEND:
        version = _row_get(row, "published_version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if version != (reservation.expected_version or 0) + 1:
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if domain_name != f"{reservation.draft}-v{version}":
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    else:
        if not _row_absent_int(row, "published_version"):
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        version = None

    identity, _device, _inode = _creation_identity(row)
    try:
        return PublishedOutcome(
            reservation=reservation,
            file_name=file_name,
            domain_doctype=domain_doctype,
            domain_name=domain_name,
            version=version,
            creation_identity=identity,
        )
    except PublicationValidationError:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def _register_issued_preparation(prepared: PreparedOperation) -> None:
    """Register this exact issued value; ``id()`` reuse cannot alias it."""
    _ISSUED_PREPARATIONS[id(prepared)] = prepared
    while len(_ISSUED_PREPARATIONS) > _ISSUED_PREPARATION_LIMIT:
        _ISSUED_PREPARATIONS.pop(next(iter(_ISSUED_PREPARATIONS)))


def _is_issued_preparation(prepared) -> bool:
    """Whether this exact object was issued by ``prepare`` for a verified row."""
    return type(prepared) is PreparedOperation and (
        _ISSUED_PREPARATIONS.get(id(prepared)) is prepared
    )


def _reservation_operation(row, expected_state: str) -> PreparedOperation:
    """Verify one persisted reservation row in exactly ``expected_state``."""
    verified = _verified_operation(row)
    if verified.state != expected_state:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    generation = _row_get(row, "generation")
    if (
        isinstance(generation, bool)
        or not isinstance(generation, int)
        or generation < 1
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    size = _row_get(row, "expected_size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 1:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    key = _row_text(row, "reservation_key")
    sha256 = _row_text(row, "expected_sha256")

    expected_sha256, expected_size = _prepared_evidence(verified.request)
    if (sha256, size) != (expected_sha256, expected_size):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if key != _reservation_key(
        verified.request,
        site=verified.site,
        operation_id=verified.id,
        generation=generation,
        payload=verified.payload_digest,
    ):
        # The stored key is not the derivation of this exact identity and generation.
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    try:
        return PreparedOperation(
            id=verified.id,
            site=verified.site,
            actor=verified.actor,
            kind=verified.kind,
            payload_digest=verified.payload_digest,
            key=key,
            generation=generation,
            draft=verified.request.draft,
            expected_version=verified.request.expected_version,
            synthetic_patient=verified.request.synthetic_patient,
            target_identity=target_identity(verified.request),
            expected_sha256=sha256,
            expected_size=size,
            state=verified.state,
        )
    except PublicationValidationError:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from None


def prepare(submission: SubmittedOperation) -> PreparedOperation:
    """Stage one durable PREPARED reservation for this exact locked operation.

    Preconditions, all enforced here rather than assumed:

    * the caller already holds operation/target exclusion for this exact operation
      (``exclusion.require_locked_owner``); this helper never acquires or invents one;
    * the locked row re-read ``for_update`` is still ``SUBMITTED`` and every
      server-derived binding agrees with the presented submission;
    * a fresh enabled-clinician session and site check passes;
    * the persisted row's identity and payload digest still match the submission.

    The canonical key, durable preparation generation, target identity and expected
    byte evidence are all derived from that verified state. The single prepared update
    is staged in the caller's transaction; this function never commits or rolls back
    and never creates a File, original, draft or version placeholder.

    This is the durable *staging* step, not the whole preparation authorization. The
    coordinator must reauthorize, inside this same live owner scope, the native
    permission, the drafting clinician, the live expected draft version and the
    original reference **before** calling ``prepare`` and again **before**
    publication. The fresh clinician/site check below is one layer of that domain
    authorization, not a substitute for it, and no later task may read ``prepare`` as
    the complete preparation transaction. The raw evidence write is therefore inlined
    here, after every owner/state/derivation check, instead of being a separately
    callable staging seam.
    """
    if not isinstance(submission, SubmittedOperation):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    owner = exclusion.require_locked_owner(submission.id)
    locked = exclusion.reread_locked_operation(owner)

    derived_target = target_identity(submission.request)
    owner.require_bindings(
        actor=submission.actor,
        site=submission.site,
        target_identity=derived_target,
    )
    if (
        locked.kind != submission.kind
        or locked.draft != submission.request.draft
        or locked.original != submission.request.original
        or locked.synthetic_patient != submission.request.synthetic_patient
    ):
        owner.poison("submission_binding_mismatch")
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    require_service_authorization(submission.actor, submission.site)

    row = frappe_context.get_save_operation(submission.id, for_update=True)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    verified = _verified_operation(row)
    if verified.state != SUBMITTED_STATE:
        raise JournalStateError(
            SAFE_JOURNAL_STATE,
            operation_id=verified.id,
            state=verified.state,
        )
    if (
        verified.payload_digest != submission.payload_digest
        or verified.request != submission.request
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if verified.reservation_key is not None or not _row_absent_int(
        row, "generation"
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    expected_sha256, expected_size = _prepared_evidence(verified.request)
    key = _reservation_key(
        verified.request,
        site=verified.site,
        operation_id=verified.id,
        generation=PREPARED_GENERATION,
        payload=verified.payload_digest,
    )
    if frappe_context.prepared_key_conflicts(key):
        # The derivation must not collide with an existing row, byte identity or
        # another reservation; a key is allocated once and never reused.
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    # The write is here, after every owner, locked-state and derivation check above:
    # there is no separately callable staging seam that could persist caller-supplied
    # key/generation/hash/size evidence. ``frappe.db.set_value`` is the documented
    # no-ORM-trigger update API, so the DocType controller keeps refusing every
    # document-API update. The caller owns the transaction; nothing here commits.
    import frappe

    frappe.db.set_value(
        OPERATION_DOCTYPE,
        verified.id,
        {
            "state": PREPARED_STATE,
            "reservation_key": key,
            "generation": PREPARED_GENERATION,
            "expected_sha256": expected_sha256,
            "expected_size": expected_size,
        },
    )
    updated = frappe_context.get_save_operation(verified.id, for_update=True)
    if updated is None:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    prepared = _prepared_operation(updated)
    # Issue this exact value and arm the one-shot commit marker on the owner that will
    # commit this transaction. Neither the issued handle nor the marker is durable
    # authority on its own: continuation needs both.
    _register_issued_preparation(prepared)
    owner.arm_preparation_commit(prepared)
    return prepared


def publish(
    operation: PreparedOperation,
    consumed,
    file_name: str,
    version: int | None,
) -> PublishedOutcome:
    """Stage one PUBLISHED outcome for this exact prepared and consumed object.

    Preconditions, all enforced here rather than assumed:

    * this attempt's live owner still holds the operation/target exclusion;
    * the same owner continued over *its own* committed ``PREPARED`` evidence through
      this process's issued handle (``LockedOwner.continue_after_own_preparation``);
      a fresh attempt, a duplicate launch and a field-equal forged handle are refused;
    * the locked row re-read ``for_update`` is still exactly the presented
      ``PREPARED`` reservation (identity, payload digest, derived key, persisted
      generation, exact byte evidence);
    * ``consumed`` is the exact object binding this attempt consumed, re-verified by
      the writer against the persisted File row, the actual bytes and the one
      server-issued ``DomainLinkage``; a receipt, a dataclass or any field-equal value
      is not evidence of a consumed first write;
    * the supplied persisted ``file_name`` equals that consumed binding, and ``version``
      is the kind's derivation.

    The single state update stages ``PUBLISHED`` together with the durable File,
    domain, version and creation-identity evidence in the caller's transaction. This
    helper never commits or rolls back and never touches a File or domain record.
    """
    if not isinstance(operation, PreparedOperation):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    owner = exclusion.require_locked_owner(operation.id)
    owner.require_bindings(
        actor=operation.actor,
        site=operation.site,
        target_identity=operation.target_identity,
    )
    # Only the live owner that already continued over *its own committed* preparation
    # may publish; a staged-but-uncommitted PREPARED row can never become durable.
    owner.require_continued()

    if not _clean_text(file_name):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if version is not None and (
        isinstance(version, bool) or not isinstance(version, int) or version < 1
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    row = frappe_context.get_save_operation(operation.id, for_update=True)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    verified = _prepared_operation(row)
    if verified != operation:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    from clinic_photos.publication import writer

    try:
        consumed_file_name, linkage, receipt = writer.require_consumed_object(
            operation, consumed
        )
    except writer.FirstObjectError as error:
        # Not this publication's consumed first object, or its persisted File, bytes or
        # domain linkage no longer agree: never stage PUBLISHED.
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY) from error

    if consumed_file_name != file_name:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if linkage.doctype != DOMAIN_DOCTYPES[operation.kind]:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if operation.kind == ANNOTATION_APPEND:
        if version != (operation.expected_version or 0) + 1:
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
        if linkage.name != f"{operation.draft}-v{version}":
            raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    elif version is not None:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if (
        receipt.size != operation.expected_size
        or receipt.sha256 != operation.expected_sha256
        or receipt.key != operation.key
    ):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    import frappe

    # ``published_version`` is a NOT NULL integer column defaulting to 0, so the
    # synthetic original kind stages that absent-evidence value instead of ``None``;
    # the annotation kind stages its derived positive version.
    frappe.db.set_value(
        OPERATION_DOCTYPE,
        operation.id,
        {
            "state": PUBLISHED_STATE,
            "published_file": consumed_file_name,
            "published_domain_doctype": linkage.doctype,
            "published_domain_name": linkage.name,
            "published_version": 0 if version is None else version,
            "creation_identity": f"{receipt.device}:{receipt.inode}",
        },
    )
    updated = frappe_context.get_save_operation(operation.id, for_update=True)
    if updated is None:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    return _published_outcome(updated)


def abort(submission: SubmittedOperation) -> None:
    """Stage one ``ABORTED`` outcome for verified still-``SUBMITTED`` work.

    This is the only ABORT seam. It re-reads the locked row itself and refuses unless
    the durable row is still this exact ``SUBMITTED`` payload with no reservation key
    and no generation, so an advanced row can never be rewritten by a losing caller.
    No key, byte, File or domain record is created here, and nothing commits.
    """
    if not isinstance(submission, SubmittedOperation):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    row = frappe_context.get_save_operation(submission.id, for_update=True)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    verified = _verified_operation(row)
    if verified.state != SUBMITTED_STATE:
        raise JournalStateError(
            SAFE_JOURNAL_STATE,
            operation_id=verified.id,
            state=verified.state,
        )
    if verified.payload_digest != submission.payload_digest:
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)
    if verified.reservation_key is not None or not _row_absent_int(row, "generation"):
        raise JournalIntegrityError(SAFE_JOURNAL_INTEGRITY)

    import frappe

    frappe.db.set_value(OPERATION_DOCTYPE, submission.id, {"state": ABORTED_STATE})


def load_published(operation_id: str) -> PublishedOutcome:
    """Load and verify one PUBLISHED outcome; never mutates or commits.

    The returned evidence is the same immutable reservation evidence (server-derived
    key, persisted generation, expected byte hash/size, target identity) plus the File,
    domain, version and creation identity the publication persisted, verified in its
    committed ``PUBLISHED`` state. It is not a saved claim on its own: the caller must
    still resolve the actual byte evidence.
    """
    if not _clean_text(operation_id):
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    row = frappe_context.get_save_operation(operation_id)
    if row is None:
        raise JournalOperationMissing(SAFE_JOURNAL_MISSING)
    return _published_outcome(row)
