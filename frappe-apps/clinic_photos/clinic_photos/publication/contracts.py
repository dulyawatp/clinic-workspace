"""Immutable contracts for the default-off durable photo-save entry.

No Frappe import happens here. The public request validator accepts only the two
approved operation kinds and only the fields listed in the contract. Client-supplied
owner, generation, path, permission or synthetic-mode fields are not part of the
schema and are rejected as unknown keys.

Annotation input is the bounded envelope validated in ``validation``. It is stored as
canonical JSON text so the request value is immutable and can be digested by later
tasks without a mutable dictionary. The wire carries ``image_base64``; it is decoded
once and validated against the native image library. Those exact bytes are then staged
durably by the journal and referenced by metadata only: neither the bytes nor their
base64 are ever persisted on the request row. No transformation, re-encoding,
deduplication or truncation happens here.
Direct construction enforces the same bounds as the public validator.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass

from . import validation
from .validation import (
    PublicationValidationError,
    SAFE_VALIDATION,
    canonical_annotation,
    decode_image_base64,
    require_reference,
    require_request_key,
    validate_image_bytes,
)
from clinic_photos.access import storage

ANNOTATION_APPEND = "annotation_append"
SYNTHETIC_ORIGINAL_CREATE = "synthetic_original_create"
VISIT_ORIGINAL_CREATE = "visit_original_create"
REQUEST_KINDS = frozenset({ANNOTATION_APPEND, SYNTHETIC_ORIGINAL_CREATE, VISIT_ORIGINAL_CREATE})

SAVE_STATUSES = frozenset({"pending", "saved", "conflict", "rejected", "unknown"})
DURABLE_STATES = frozenset(
    {"SUBMITTED", "PREPARED", "PUBLISHED", "ABORTED", "QUARANTINED"}
)
SUBMITTED_STATE = "SUBMITTED"

# The one domain record each operation kind links to, and the doctypes the persisted
# publication outcome may name.
_DOMAIN_DOCTYPES = {
    "Clinic Photo Annotation Version": ANNOTATION_APPEND,
    "Clinic Photo": SYNTHETIC_ORIGINAL_CREATE,
}

REQUEST_FIELDS = frozenset(
    {
        "kind",
        "request_key",
        "draft",
        "expected_version",
        "original",
        "annotation",
        "synthetic_patient",
        "image_base64",
    }
)

_SHA256 = re.compile(r"[0-9a-f]{64}")
_OPERATION_ID = re.compile(r"[0-9a-f]{32}")
_CREATION_IDENTITY = re.compile(r"([0-9]{1,20}):([0-9]{1,20})")


def _require_kind(value) -> str:
    if not isinstance(value, str) or value not in REQUEST_KINDS:
        raise PublicationValidationError(SAFE_VALIDATION)
    return value


def _require_text(value) -> str:
    if not isinstance(value, str):
        raise PublicationValidationError(SAFE_VALIDATION)
    if not value or value.strip() != value:
        raise PublicationValidationError(SAFE_VALIDATION)
    return value


def _require_positive_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PublicationValidationError(SAFE_VALIDATION)
    return value


def _require_non_negative_int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PublicationValidationError(SAFE_VALIDATION)
    return value


def _forbid(value) -> None:
    if value is not None:
        raise PublicationValidationError(SAFE_VALIDATION)


def _require_canonical_annotation(value) -> str:
    """Accept only the single canonical annotation text, never a variant spelling."""
    if not isinstance(value, str):
        raise PublicationValidationError(SAFE_VALIDATION)
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        raise PublicationValidationError(SAFE_VALIDATION) from None
    if canonical_annotation(parsed) != value:
        raise PublicationValidationError(SAFE_VALIDATION)
    return value


@dataclass(frozen=True)
class VisitBinding:
    """Server-derived ordinary-workflow snapshots, not authorization evidence."""

    appointment: str
    patient: str
    slot: str
    encounter: str | None
    body_area: str
    viewpoint: str
    appointment_creation: str
    patient_creation: str
    encounter_creation: str | None
    grant_audit: str

    def __post_init__(self) -> None:
        for value in (self.appointment, self.patient, self.body_area, self.viewpoint,
                      self.appointment_creation, self.patient_creation, self.grant_audit):
            require_reference(value)
            if not value.isprintable():
                raise PublicationValidationError(SAFE_VALIDATION)
        if self.slot == "After":
            for value in (self.encounter, self.encounter_creation):
                require_reference(value)
                if not value.isprintable():
                    raise PublicationValidationError(SAFE_VALIDATION)
        elif self.slot == "Before":
            _forbid(self.encounter)
            _forbid(self.encounter_creation)
        else:
            raise PublicationValidationError(SAFE_VALIDATION)


def visit_target_identity(binding: VisitBinding) -> str:
    """Appointment-wide opaque target, independent of slot and encounter."""
    if type(binding) is not VisitBinding:
        raise PublicationValidationError(SAFE_VALIDATION)
    encoded = binding.appointment.encode("utf-8")
    return "visit:" + hashlib.sha256(b"clinic-photos-visit-target-v1\0" + encoded).hexdigest()


@dataclass(frozen=True)
class SaveRequest:
    kind: str
    request_key: str
    draft: str | None = None
    expected_version: int | None = None
    original: str | None = None
    annotation: str | None = None
    synthetic_patient: str | None = None
    image_bytes: bytes | None = None
    visit_binding: VisitBinding | None = None

    def __post_init__(self) -> None:
        kind = _require_kind(self.kind)
        require_request_key(self.request_key)

        if kind == ANNOTATION_APPEND:
            _forbid(self.visit_binding)
            require_reference(self.draft)
            _require_non_negative_int(self.expected_version)
            require_reference(self.original)
            _require_canonical_annotation(self.annotation)
            _forbid(self.synthetic_patient)
            _forbid(self.image_bytes)
            return

        if kind == VISIT_ORIGINAL_CREATE:
            if type(self.visit_binding) is not VisitBinding:
                raise PublicationValidationError(SAFE_VALIDATION)
            _forbid(self.synthetic_patient)
        else:
            _forbid(self.visit_binding)
            require_reference(self.synthetic_patient)
        validate_image_bytes(self.image_bytes)
        _forbid(self.draft)
        _forbid(self.expected_version)
        _forbid(self.original)
        _forbid(self.annotation)


@dataclass(frozen=True)
class PreparedOperation:
    """Durable PREPARED reservation evidence for one accepted submission.

    Every value is server-derived under the publication owner's operation/target
    exclusion: the canonical private storage key, the persisted preparation
    generation, the derived target identity, the exact expected byte hash/size and
    the server-derived patient (synthetic kind only). A client supplies none of them,
    and no draft/version/original placeholder is invented for the synthetic kind.
    """

    id: str
    site: str
    actor: str
    kind: str
    payload_digest: str
    key: str
    generation: int
    draft: str | None
    expected_version: int | None
    synthetic_patient: str | None
    target_identity: str
    expected_sha256: str
    expected_size: int
    state: str
    visit_binding: VisitBinding | None = None

    def __post_init__(self) -> None:
        kind = _require_kind(self.kind)
        _require_text(self.id)
        _require_text(self.site)
        _require_text(self.actor)
        _require_text(self.payload_digest)
        _require_positive_int(self.generation)
        _require_positive_int(self.expected_size)
        if (
            not isinstance(self.expected_sha256, str)
            or _SHA256.fullmatch(self.expected_sha256) is None
        ):
            raise PublicationValidationError(SAFE_VALIDATION)
        if self.state not in DURABLE_STATES - {SUBMITTED_STATE}:
            raise PublicationValidationError(SAFE_VALIDATION)

        # Canonical private key in the reserved namespace only: a prepared
        # reservation never points at an unregistered or public destination.
        try:
            key = storage.canonical_private_key(self.key)
        except storage.InvalidStoragePath:
            raise PublicationValidationError(SAFE_VALIDATION) from None
        if key != self.key or not storage.is_reserved_key(key):
            raise PublicationValidationError(SAFE_VALIDATION)

        if kind == ANNOTATION_APPEND:
            _forbid(self.visit_binding)
            require_reference(self.draft)
            _require_non_negative_int(self.expected_version)
            _forbid(self.synthetic_patient)
            if self.target_identity != "draft:" + self.draft:
                raise PublicationValidationError(SAFE_VALIDATION)
            if not key.endswith(".json"):
                raise PublicationValidationError(SAFE_VALIDATION)
            return

        if kind == VISIT_ORIGINAL_CREATE:
            if (type(self.visit_binding) is not VisitBinding or
                self.state not in ("PREPARED", "PUBLISHED") or
                self.generation != 1 or _SHA256.fullmatch(self.payload_digest) is None or
                self.target_identity != visit_target_identity(self.visit_binding) or
                self.draft is not None or self.expected_version is not None or
                self.synthetic_patient is not None or not key.endswith((".png", ".jpg"))):
                raise PublicationValidationError(SAFE_VALIDATION)
            return
        _forbid(self.visit_binding)
        require_reference(self.synthetic_patient)
        _forbid(self.draft)
        if self.expected_version is not None:
            raise PublicationValidationError(SAFE_VALIDATION)
        if self.target_identity != "patient-creation:" + self.synthetic_patient:
            raise PublicationValidationError(SAFE_VALIDATION)
        if not key.endswith((".png", ".jpg")):
            raise PublicationValidationError(SAFE_VALIDATION)



@dataclass(frozen=True)
class SubmittedOperation:
    """Server-bound identity of an accepted SUBMITTED operation.

    The client supplies no part of these values: the opaque ID, site, actor, digest
    and request key are all server-owned, and the typed request is the immutable
    accepted value. No protected storage key or bytes exist at this state.
    """

    id: str
    site: str
    actor: str
    kind: str
    request_key: str
    payload_digest: str
    request: SaveRequest
    state: str = SUBMITTED_STATE

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or _OPERATION_ID.fullmatch(self.id) is None:
            raise PublicationValidationError(SAFE_VALIDATION)
        _require_text(self.site)
        _require_text(self.actor)
        _require_kind(self.kind)
        require_request_key(self.request_key)
        if (
            not isinstance(self.payload_digest, str)
            or _SHA256.fullmatch(self.payload_digest) is None
        ):
            raise PublicationValidationError(SAFE_VALIDATION)
        if not isinstance(self.request, SaveRequest):
            raise PublicationValidationError(SAFE_VALIDATION)
        if self.kind != self.request.kind or self.request_key != self.request.request_key:
            raise PublicationValidationError(SAFE_VALIDATION)
        if self.state != SUBMITTED_STATE:
            raise PublicationValidationError(SAFE_VALIDATION)


@dataclass(frozen=True)
class CreationReceipt:
    operation_id: str
    generation: int
    key: str
    device: int
    inode: int
    size: int
    sha256: str

    def __post_init__(self) -> None:
        _require_text(self.operation_id)
        _require_positive_int(self.generation)
        _require_text(self.key)
        _require_non_negative_int(self.device)
        _require_non_negative_int(self.inode)
        _require_non_negative_int(self.size)
        if not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None:
            raise PublicationValidationError(SAFE_VALIDATION)


@dataclass(frozen=True)
class PublishedOutcome:
    """Durable publication outcome persisted atomically with ``PUBLISHED``.

    The reservation evidence is the same immutable PREPARED evidence this attempt
    prepared; the outcome fields are the File, domain, version and filesystem creation
    identity the publication actually produced. ``creation_identity`` is ``dev:inode``
    captured on this host/volume: provenance for this object, never portable authority.
    """

    reservation: "PreparedOperation"
    file_name: str
    domain_doctype: str
    domain_name: str
    version: int | None
    creation_identity: str

    def __post_init__(self) -> None:
        if not isinstance(self.reservation, PreparedOperation):
            raise PublicationValidationError(SAFE_VALIDATION)
        _require_text(self.file_name)
        if (_DOMAIN_DOCTYPES.get(self.domain_doctype) != self.reservation.kind and
            not (self.domain_doctype == "Clinic Photo" and
                 self.reservation.kind == VISIT_ORIGINAL_CREATE)):
            raise PublicationValidationError(SAFE_VALIDATION)
        _require_text(self.domain_name)
        if self.version is not None:
            _require_positive_int(self.version)
        _require_text(self.creation_identity)
        parsed = _CREATION_IDENTITY.fullmatch(self.creation_identity)
        if parsed is None:
            raise PublicationValidationError(SAFE_VALIDATION)

    @property
    def device(self) -> int:
        return int(_CREATION_IDENTITY.fullmatch(self.creation_identity).group(1))

    @property
    def inode(self) -> int:
        return int(_CREATION_IDENTITY.fullmatch(self.creation_identity).group(2))


@dataclass(frozen=True)
class SaveOutcome:
    status: str
    operation_id: str
    version: int | None = None
    file_name: str | None = None

    def __post_init__(self) -> None:
        if self.status not in SAVE_STATUSES:
            raise PublicationValidationError(SAFE_VALIDATION)
        _require_text(self.operation_id)
        if self.version is not None:
            _require_positive_int(self.version)
        if self.file_name is not None:
            _require_text(self.file_name)


def validate_request(payload: dict | str) -> SaveRequest:
    """Alias for the shared ``validation.validate_request`` seam (dict or JSON text)."""
    return validation.validate_request(payload)


def _validate_payload(payload) -> SaveRequest:
    """Validate one parsed public request; never accept client-owned authority fields."""
    if not isinstance(payload, Mapping):
        raise PublicationValidationError(SAFE_VALIDATION)
    if any(not isinstance(key, str) for key in payload):
        raise PublicationValidationError(SAFE_VALIDATION)

    unknown = set(payload) - REQUEST_FIELDS
    if unknown:
        raise PublicationValidationError(SAFE_VALIDATION)

    kind = _require_kind(payload.get("kind"))
    if kind == VISIT_ORIGINAL_CREATE:
        raise PublicationValidationError(SAFE_VALIDATION)
    request_key = payload.get("request_key")

    if kind == ANNOTATION_APPEND:
        for fieldname in ("synthetic_patient", "image_base64"):
            if fieldname in payload:
                raise PublicationValidationError(SAFE_VALIDATION)
        return SaveRequest(
            kind=kind,
            request_key=request_key,
            draft=payload.get("draft"),
            expected_version=payload.get("expected_version"),
            original=payload.get("original"),
            annotation=canonical_annotation(payload.get("annotation")),
        )

    for fieldname in ("draft", "expected_version", "original", "annotation"):
        if fieldname in payload:
            raise PublicationValidationError(SAFE_VALIDATION)
    return SaveRequest(
        kind=kind,
        request_key=request_key,
        synthetic_patient=payload.get("synthetic_patient"),
        image_bytes=decode_image_base64(payload.get("image_base64")),
    )


def validate_request_document(document) -> SaveRequest:
    """Compatibility name for the raw-document form of the shared seam."""
    return validation.validate_request(document)
