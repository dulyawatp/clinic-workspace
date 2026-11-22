"""Authenticated native POST endpoints for private visit annotations."""

import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import contextmanager

from clinic_photos.api import photo_save, visit_photos
from clinic_photos.access.entrypoints import mark_protected_request
from clinic_photos.publication import validation
from clinic_photos.publication.visit_annotation_intent import VisitAnnotationBinding, VisitAnnotationIntent

_KEYS = frozenset(("appointment", "encounter", "slot", "request_key", "expected_version", "annotation"))
_SAFE_FAILURE = "Visit annotation submission unavailable"


@contextmanager
def _mute_permission_messages(frappe):
    """Mute only caught permission probes, restoring the original request flag."""
    flags = getattr(frappe, "flags", None)
    if flags is None:
        raise frappe.PermissionError(_SAFE_FAILURE)
    mapping = isinstance(flags, dict)
    exists = "mute_messages" in flags if mapping else hasattr(flags, "mute_messages")
    previous = (flags.get("mute_messages") if mapping else getattr(flags, "mute_messages", None))
    try:
        if mapping:
            flags["mute_messages"] = True
        else:
            flags.mute_messages = True
    except Exception:
        raise frappe.PermissionError(_SAFE_FAILURE) from None
    try:
        yield
    finally:
        if mapping:
            if exists:
                flags["mute_messages"] = previous
            else:
                flags.pop("mute_messages", None)
        elif exists:
            flags.mute_messages = previous
        else:
            delattr(flags, "mute_messages")


def submit(payload=None, **extra):
    """Stage one authenticated request; transaction and rollback belong to Frappe."""
    mark_protected_request()
    visit_photos.require_visit_request()
    frappe = photo_save._frappe()
    if getattr(frappe, "conf", {}).get("clinic_visit_photo_annotations_enabled") is not True:
        raise frappe.PermissionError(_SAFE_FAILURE)
    try:
        appointment = visit_photos._payload(payload, _KEYS, extra, "visit_annotations.submit")
        encounter = validation.require_reference(payload["encounter"])
        if not encounter.isprintable() or payload["slot"] not in ("Before", "After"):
            raise ValueError
        validation.require_request_key(payload["request_key"])
        if type(payload["expected_version"]) is not int or payload["expected_version"] < 0:
            raise ValueError
        content = validation.canonical_visit_annotation(payload["annotation"])
    except (ValueError, TypeError, KeyError, RecursionError, frappe.ValidationError):
        return {"state": "rejected"}

    from clinic_photos.visit import annotation_authority
    from clinic_photos.publication import visit_annotation_submission

    try:
        with _mute_permission_messages(frappe):
            binding = annotation_authority.resolve_write_binding(appointment, encounter, payload["slot"])
        if (type(binding) is not VisitAnnotationBinding or binding.appointment != appointment or
                binding.encounter != encounter or binding.slot != payload["slot"] or
                binding.site != getattr(frappe.local, "site", None) or
                binding.actor != getattr(frappe.session, "user", None)):
            raise frappe.PermissionError(_SAFE_FAILURE)
        intent = VisitAnnotationIntent(
            request_key=payload["request_key"], expected_version=payload["expected_version"],
            binding=binding, canonical_visit_annotation=content,
        )
    except (frappe.PermissionError, validation.PublicationValidationError):
        return {"state": "rejected"}
    except Exception:
        raise frappe.PermissionError(_SAFE_FAILURE) from None

    try:
        visit_annotation_submission.stage_visit_annotation_submission(intent)
    except visit_annotation_submission.StaleVisitAnnotation:
        # The submission checks the live head before staging bytes or inserting.
        return {"state": "stale"}
    except visit_annotation_submission.PendingVisitAnnotation:
        # The new key was refused before staging; the original save is not replayed.
        return {"state": "rejected"}
    except Exception:
        # Returning normally could commit a partial insertion or callback.
        raise frappe.PermissionError(_SAFE_FAILURE) from None
    return {"state": "pending"}


def _read_request(payload, extra, method, keys):
    visit_photos.require_visit_request()
    frappe = photo_save._frappe()
    if getattr(frappe, "conf", {}).get("clinic_visit_photo_annotations_enabled") is not True:
        raise frappe.PermissionError(_SAFE_FAILURE)
    appointment = visit_photos._payload(payload, keys, extra, method)
    if payload["slot"] not in ("Before", "After"):
        raise frappe.ValidationError(visit_photos.SAFE_VALIDATION)
    return appointment


def _target(appointment, slot):
    from clinic_photos.visit import annotation_read

    frappe = photo_save._frappe()
    target = annotation_read.resolve_read_target(appointment, slot)
    if (target.site != frappe.local.site or target.actor != frappe.session.user or
            target.appointment != appointment or target.slot != slot):
        raise ValueError
    return target


def _binding_matches(binding, target):
    return all(getattr(binding, field) == getattr(target, field) for field in (
        "site", "actor", "appointment", "patient", "slot", "appointment_creation",
        "patient_creation", "published_slot", "original_photo", "original_file", "original_operation",
    ))


def _can_write(target):
    from clinic_photos.access.draft_identity import visit_draft_name
    from clinic_photos.visit import annotation_authority

    frappe = photo_save._frappe()
    try:
        rows = frappe.db.sql(
            "SELECT name FROM `tabPatient Encounter` WHERE appointment = %s AND patient = %s "
            "AND docstatus = 0 ORDER BY name LIMIT 2",
            (target.appointment, target.patient),
        )
        if (type(rows) not in (list, tuple) or len(rows) != 1 or
                type(rows[0]) not in (list, tuple) or len(rows[0]) != 1 or
                type(rows[0][0]) is not str or not rows[0][0]):
            return False
        with _mute_permission_messages(frappe):
            binding = annotation_authority.preview_write_binding(target.appointment, rows[0][0], target.slot)
        return (type(binding) is VisitAnnotationBinding and binding.encounter == rows[0][0] and
                _binding_matches(binding, target) and
                visit_draft_name(binding.site, binding.appointment, binding.slot,
                                 binding.original_photo, binding.actor) == target.draft_name)
    except Exception:
        return False


def _pending(row, target):
    from clinic_photos.publication import journal
    from clinic_photos.publication import visit_annotation_journal_codec as codec

    if not isinstance(row, Mapping):
        raise ValueError
    row = dict(row)
    row.setdefault("doctype", journal.OPERATION_DOCTYPE)
    name = row.get("name")
    if type(name) is not str or re.fullmatch(r"[0-9a-f]{32}", name) is None:
        raise ValueError
    if row.get("state") == journal.PREPARED_STATE:
        if any(journal._row_present(row, field) for field in (
                "published_file", "published_domain_doctype", "published_domain_name", "creation_identity"
        )) or not journal._row_absent_int(row, "published_version"):
            raise ValueError
        generation, key = row.get("generation"), row.get("reservation_key")
        if (type(generation) is not int or generation != 1 or
                row.get("expected_sha256") != row.get("staged_sha256") or
                type(row.get("expected_size")) is not int or
                row.get("expected_size") != row.get("staged_size")):
            raise ValueError
        restored = dict(row)
        restored.update(state=journal.SUBMITTED_STATE, reservation_key=None,
                        expected_sha256=None, expected_size=0, generation=0)
        item = codec.load_submitted(restored, site=target.site, actor=target.actor)
        material = json.dumps(
            ["clinic-visit-annotation-prepared-v1", target.site, target.actor,
             name, item.target_identity, item.intent_digest, 1],
            ensure_ascii=True, separators=(",", ":"),
        ).encode("utf-8")
        if key != "/private/files/clinic-visit-annotation-" + hashlib.sha256(material).hexdigest() + ".json":
            raise ValueError
    elif row.get("state") == journal.SUBMITTED_STATE:
        item = codec.load_submitted(row, site=target.site, actor=target.actor)
    else:
        raise ValueError
    if item.managed_draft != target.draft_name or not _binding_matches(item.binding, target):
        raise ValueError
    return item


def _pending_key(target):
    from clinic_photos.access import frappe_context
    from clinic_photos.publication import journal
    from clinic_photos.publication.visit_annotation_intent import VISIT_ANNOTATION_APPEND

    frappe = photo_save._frappe()
    rows = frappe.db.get_all(
        journal.OPERATION_DOCTYPE,
        filters={"site": target.site, "actor": target.actor, "draft": target.draft_name,
                 "kind": VISIT_ANNOTATION_APPEND, "state": ["in", ["SUBMITTED", "PREPARED"]]},
        fields=["name"], limit_page_length=2,
    )
    if type(rows) not in (list, tuple) or len(rows) > 1:
        raise ValueError
    if not rows:
        return None
    if not isinstance(rows[0], Mapping):
        raise ValueError
    name = rows[0].get("name")
    row = frappe_context.get_save_operation(name, for_update=False)
    if not row or row.get("name") != name:
        raise ValueError
    return _pending(row, target).request_key


def read(payload=None, **extra):
    """Read one current author drawing; no public route or authority grant."""
    mark_protected_request()
    has_version = type(payload) is dict and "version" in payload
    keys = {"appointment", "slot", "version"} if has_version else {"appointment", "slot"}
    appointment = _read_request(payload, extra, "visit_annotations.read", keys)
    frappe = photo_save._frappe()
    if has_version and (type(payload["version"]) is not int or payload["version"] < 1):
        raise frappe.ValidationError(visit_photos.SAFE_VALIDATION)
    try:
        from clinic_photos.visit import annotation_read
        target = _target(appointment, payload["slot"])
        result = (annotation_read.read_committed(appointment, payload["slot"], version=payload["version"])
                  if has_version else annotation_read.read_committed(appointment, payload["slot"]))
        if (type(result.version_number) is not int or result.version_number < 0 or
                (has_version and result.version_number != payload["version"]) or
                (result.version_number == 0 and result.annotation is not None) or
                (result.version_number > 0 and type(result.annotation) is not dict)):
            raise ValueError
        pending = _pending_key(target)
        writable = _can_write(target)
        return {"version": result.version_number, "annotation": result.annotation,
                "can_write": writable, "pending_request_key": pending}
    except Exception:
        raise frappe.PermissionError(_SAFE_FAILURE) from None


def status(payload=None, **extra):
    """Read actor-bound status; committed requires exact authorized byte readback."""
    mark_protected_request()
    appointment = _read_request(payload, extra, "visit_annotations.status",
                                {"appointment", "slot", "request_key"})
    frappe = photo_save._frappe()
    try:
        from clinic_photos.access import frappe_context
        from clinic_photos.publication import journal
        from clinic_photos.publication.visit_annotation_intent import VISIT_ANNOTATION_APPEND
        from clinic_photos.visit import annotation_read

        key = validation.require_request_key(payload["request_key"])
        with _mute_permission_messages(frappe):
            target = _target(appointment, payload["slot"])
        digest = journal.identity_digest(target.site, target.actor, VISIT_ANNOTATION_APPEND, key)
        row = frappe_context.get_save_operation_by_identity(digest, for_update=False)
        if row is None:
            return {"state": "uncertain"}
        if (not isinstance(row, Mapping) or row.get("identity_digest") != digest or
                row.get("site") != target.site or row.get("actor") != target.actor or
                row.get("request_key") != key or row.get("kind") != VISIT_ANNOTATION_APPEND or
                row.get("draft") != target.draft_name or row.get("original") != target.original_photo):
            raise ValueError
        if row.get("state") in (journal.SUBMITTED_STATE, journal.PREPARED_STATE):
            if _pending(row, target).request_key != key:
                raise ValueError
            return {"state": "pending"}
        number, operation = row.get("published_version"), row.get("name")
        if (row.get("state") != journal.PUBLISHED_STATE or type(number) is not int or number < 1 or
                type(operation) is not str or re.fullmatch(r"[0-9a-f]{32}", operation) is None):
            raise ValueError
        with _mute_permission_messages(frappe):
            result = annotation_read.read_committed(appointment, payload["slot"], version=number,
                                                    operation=operation, request_key=key)
        if result.version_number != number or type(result.annotation) is not dict:
            raise ValueError
        return {"state": "committed", "request_key": key, "version": number}
    except Exception:
        return {"state": "uncertain"}


def _register_site_contract():
    try:
        import frappe
    except ModuleNotFoundError:
        return
    global read, status, submit
    read = frappe.whitelist(methods=["POST"])(read)
    status = frappe.whitelist(methods=["POST"])(status)
    submit = frappe.whitelist(methods=["POST"])(submit)


_register_site_contract()
