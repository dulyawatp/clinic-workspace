"""Isolated server-side visit annotation evidence; not a public save request."""

import hashlib
import json
from dataclasses import asdict, dataclass, field

from clinic_photos.access.draft_identity import visit_draft_name

from .validation import (
    MAX_ANNOTATION_BYTES,
    PublicationValidationError,
    SAFE_VALIDATION,
    canonical_visit_annotation,
    require_reference,
    require_request_key,
)

VISIT_ANNOTATION_APPEND = "visit_annotation_append"


def _digest(domain: bytes, value) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    return hashlib.sha256(domain + b"\0" + encoded).hexdigest()


@dataclass(frozen=True)
class VisitAnnotationBinding:
    """Exact server-resolved evidence, not an authorization decision."""

    site: str
    actor: str
    appointment: str
    patient: str
    slot: str
    encounter: str
    appointment_creation: str
    patient_creation: str
    encounter_creation: str
    grant_audit: str
    published_slot: str
    original_photo: str
    original_file: str
    original_operation: str

    def __post_init__(self):
        for value in asdict(self).values():
            require_reference(value)
            if not value.isprintable():
                raise PublicationValidationError(SAFE_VALIDATION)
        if self.slot not in ("Before", "After"):
            raise PublicationValidationError(SAFE_VALIDATION)


@dataclass(frozen=True)
class VisitAnnotationIntent:
    """Validated in-memory content; persist only staged bytes and metadata."""

    request_key: str
    expected_version: int
    binding: VisitAnnotationBinding
    canonical_visit_annotation: str = field(repr=False)

    def __post_init__(self):
        require_request_key(self.request_key)
        if type(self.expected_version) is not int or self.expected_version < 0:
            raise PublicationValidationError(SAFE_VALIDATION)
        if type(self.binding) is not VisitAnnotationBinding:
            raise PublicationValidationError(SAFE_VALIDATION)
        if not isinstance(self.canonical_visit_annotation, str):
            raise PublicationValidationError(SAFE_VALIDATION)
        if len(self.canonical_visit_annotation) > MAX_ANNOTATION_BYTES:
            raise PublicationValidationError(SAFE_VALIDATION)
        try:
            size = len(self.canonical_visit_annotation.encode("utf-8"))
        except UnicodeEncodeError:
            raise PublicationValidationError(SAFE_VALIDATION) from None
        if size > MAX_ANNOTATION_BYTES:
            raise PublicationValidationError(SAFE_VALIDATION)
        try:
            parsed = json.loads(self.canonical_visit_annotation)
        except (ValueError, RecursionError):
            raise PublicationValidationError(SAFE_VALIDATION) from None
        if canonical_visit_annotation(parsed) != self.canonical_visit_annotation:
            raise PublicationValidationError(SAFE_VALIDATION)

    @property
    def managed_draft(self) -> str:
        b = self.binding
        return visit_draft_name(b.site, b.appointment, b.slot, b.original_photo, b.actor)

    @property
    def target_identity(self) -> str:
        return "draft:" + self.managed_draft

    @property
    def content_sha256(self) -> str:
        return hashlib.sha256(self.canonical_visit_annotation.encode("utf-8")).hexdigest()

    @property
    def binding_digest(self) -> str:
        return _digest(b"clinic-photos-visit-annotation-binding-v1", asdict(self.binding))

    @property
    def intent_digest(self) -> str:
        return _digest(b"clinic-photos-visit-annotation-intent-v1", {
            "kind": VISIT_ANNOTATION_APPEND,
            "binding_digest": self.binding_digest,
            "target_identity": self.target_identity,
            "actor": self.binding.actor,
            "request_key": self.request_key,
            "expected_version": self.expected_version,
            "content_sha256": self.content_sha256,
        })
