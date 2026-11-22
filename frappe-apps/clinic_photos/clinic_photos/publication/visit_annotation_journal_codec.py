"""Private, non-dispatched SUBMITTED metadata codec; not an authorization seam."""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict

from clinic_photos.publication import journal, staging
from clinic_photos.publication.visit_annotation_intent import (
    VISIT_ANNOTATION_APPEND,
    VisitAnnotationBinding,
    VisitAnnotationIntent,
)

JournalIntegrityError = journal.JournalIntegrityError

_FORBIDDEN_TEXT = (
    "annotation_json", "synthetic_patient", "image_base64", "reservation_key",
    "expected_sha256", "published_file", "published_domain_doctype",
    "published_domain_name", "creation_identity",
)
_FORBIDDEN_INT = ("generation", "expected_size", "published_version")
_METADATA_FIELDS = frozenset((
    "doctype", "site", "actor", "kind", "request_key", "identity_digest",
    "payload_digest", "state", "draft", "expected_version", "original",
    "visit_binding_json", "staged_key", "staged_sha256", "staged_size",
    "staged_format",
))
_NATIVE_ENVELOPE_FIELDS = frozenset((
    "name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
))
_ALLOWED_FIELDS = _METADATA_FIELDS | _NATIVE_ENVELOPE_FIELDS | frozenset(_FORBIDDEN_TEXT + _FORBIDDEN_INT)


def _binding_json(binding):
    return json.dumps(asdict(binding), ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _metadata_values(intent, staged):
    binding = intent.binding
    return {
        "doctype": journal.OPERATION_DOCTYPE,
        "site": binding.site,
        "actor": binding.actor,
        "kind": VISIT_ANNOTATION_APPEND,
        "request_key": intent.request_key,
        "identity_digest": journal.identity_digest(binding.site, binding.actor, VISIT_ANNOTATION_APPEND, intent.request_key),
        "payload_digest": intent.intent_digest,
        "state": journal.SUBMITTED_STATE,
        "draft": intent.managed_draft,
        "expected_version": intent.expected_version,
        "original": binding.original_photo,
        "visit_binding_json": _binding_json(binding),
        "staged_key": staged.key,
        "staged_sha256": staged.sha256,
        "staged_size": staged.size,
        "staged_format": staged.format,
    }


def submitted_values(intent, staged):
    """Encode only metadata, after checking the exact staged bytes."""
    import frappe

    try:
        if type(intent) is not VisitAnnotationIntent or type(staged) is not staging.StagedVisitAnnotation:
            raise ValueError
        if getattr(frappe.local, "site", None) != intent.binding.site:
            raise ValueError
        if staging.read_staged_visit_annotation(staged) != intent.canonical_visit_annotation.encode("utf-8"):
            raise ValueError
        return _metadata_values(intent, staged)
    except (ValueError, TypeError, UnicodeError, staging.StagingError):
        raise JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY) from None


def load_submitted(row, *, site, actor):
    """Authenticate a mapping projection and private bytes, not live grant authority.

    Accept only metadata fields, explicitly enumerated native envelope fields, and
    empty legacy/prepared fields. Frappe documents must be projected to a mapping.
    """
    import frappe

    try:
        if not isinstance(row, Mapping) or not set(row).issubset(_ALLOWED_FIELDS):
            raise ValueError
        if (row.get("doctype") != journal.OPERATION_DOCTYPE
                or row.get("kind") != VISIT_ANNOTATION_APPEND
                or row.get("state") != journal.SUBMITTED_STATE):
            raise ValueError
        if not site or not actor or getattr(frappe.local, "site", None) != site:
            raise ValueError
        if any(journal._row_present(row, name) for name in _FORBIDDEN_TEXT):
            raise ValueError
        if any(not journal._row_absent_int(row, name) for name in _FORBIDDEN_INT):
            raise ValueError
        encoded = journal._row_text(row, "visit_binding_json")
        if len(encoded) > 20000:
            raise ValueError
        binding = VisitAnnotationBinding(**json.loads(encoded))
        if _binding_json(binding) != encoded or binding.site != site or binding.actor != actor:
            raise ValueError
        staged = staging.StagedVisitAnnotation(
            journal._row_text(row, "staged_key"), journal._row_text(row, "staged_sha256"),
            journal._row_get(row, "staged_size"), journal._row_text(row, "staged_format"),
        )
        content = staging.read_staged_visit_annotation(staged).decode("utf-8")
        intent = VisitAnnotationIntent(
            request_key=journal._row_text(row, "request_key"),
            expected_version=journal._row_get(row, "expected_version"),
            binding=binding, canonical_visit_annotation=content,
        )
        expected = _metadata_values(intent, staged)
        if any(journal._row_get(row, key) != value for key, value in expected.items()):
            raise ValueError
        return intent
    except (JournalIntegrityError, ValueError, TypeError, UnicodeError, RecursionError, staging.StagingError):
        raise JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY) from None


def load_published(row, *, intent, operation, published_file, creation_identity, committed_bytes):
    """Verify private committed metadata and bytes; does not grant read authority."""
    try:
        if (not isinstance(row, Mapping) or not set(row).issubset(_ALLOWED_FIELDS)
                or type(intent) is not VisitAnnotationIntent or type(committed_bytes) is not bytes):
            raise ValueError
        for value in (operation, published_file):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError
        if (not isinstance(creation_identity, str)
                or re.fullmatch(r"[0-9]+:[0-9]+", creation_identity) is None):
            raise ValueError
        if any(name in row for name in
               ("annotation_json", "synthetic_patient", "image_base64")):
            raise ValueError
        binding_text = journal._row_text(row, "visit_binding_json")
        if len(binding_text) > 20000:
            raise ValueError
        binding = VisitAnnotationBinding(**json.loads(binding_text))
        content = committed_bytes.decode("utf-8")
        reconstructed = VisitAnnotationIntent(
            request_key=journal._row_text(row, "request_key"),
            expected_version=journal._row_get(row, "expected_version"),
            binding=binding, canonical_visit_annotation=content,
        )
        if reconstructed != intent or _binding_json(binding) != binding_text:
            raise ValueError
        key = journal._row_text(row, "staged_key")
        if staging._ANNOTATION_KEY_PATTERN.fullmatch(key) is None:
            raise ValueError
        staged = staging.StagedVisitAnnotation(
            key, hashlib.sha256(committed_bytes).hexdigest(), len(committed_bytes),
            staging.VISIT_ANNOTATION_JSON,
        )
        material = json.dumps(
            ["clinic-visit-annotation-prepared-v1", binding.site, binding.actor,
             operation, intent.target_identity, intent.intent_digest, 1],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
        expected = {
            **_metadata_values(intent, staged),
            "name": operation,
            "state": journal.PUBLISHED_STATE,
            "generation": 1,
            "reservation_key": "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json",
            "expected_sha256": staged.sha256,
            "expected_size": staged.size,
            "published_file": published_file,
            "published_domain_doctype": "Clinic Photo Annotation Version",
            "published_domain_name": f"{intent.managed_draft}-v{intent.expected_version + 1}",
            "published_version": intent.expected_version + 1,
            "creation_identity": creation_identity,
        }
        if any(type(journal._row_get(row, key)) is not type(value)
               or journal._row_get(row, key) != value for key, value in expected.items()):
            raise ValueError
        return reconstructed
    except (JournalIntegrityError, ValueError, TypeError, UnicodeError, RecursionError):
        raise JournalIntegrityError(journal.SAFE_JOURNAL_INTEGRITY) from None
