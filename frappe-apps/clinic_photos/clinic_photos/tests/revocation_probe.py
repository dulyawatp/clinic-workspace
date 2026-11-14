"""Strict private protocol for the synthetic-actor revocation probes.

Test-only and inert on import: no Frappe import, no process launch and no I/O.
Task 1 provides only the pure input/request/response/baseline functions; Task 2
adds the fresh native probe bodies and Task 3 owns the operator handoff.

The input and response documents are exact-key, bounded UTF-8 JSON. Digests use
canonical JSON (sorted keys, compact separators, UTF-8) so one private baseline
always binds to one value. Role assignments are sorted with multiplicity kept, so
restoration equality never depends on child-row IDs or display order. These
digests bind private structures; they are not authenticated physical observations.
"""

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import sys
from collections.abc import Mapping

from clinic_photos.access import frappe_context
from clinic_photos.access.frappe_context import CANDIDATE_CLINICIAN_ROLES
from clinic_photos.publication import (
    contracts,
    inspection,
    journal,
    submission,
    validation,
    writer,
)
from clinic_photos.publication.contracts import SYNTHETIC_ORIGINAL_CREATE, SaveRequest

APPROVED_SITE = "clinic-photo-accept.local"
SCHEMA_VERSION = 1

INPUT_KEYS = frozenset(
    {
        "schema",
        "run_nonce",
        "invocation_nonce",
        "site",
        "source_digest",
        "operation_id",
        "actor",
        "verifier",
        "arm",
        "mode",
        "baseline",
        "request",
    }
)
OUTPUT_KEYS = frozenset(
    {
        "schema",
        "run_nonce",
        "invocation_nonce",
        "site_digest",
        "source_digest",
        "operation_id",
        "arm",
        "mode",
        "result",
        "enabled",
        "clinician",
        "baseline_digest",
        "evidence_digest",
        "counts",
        "clean",
    }
)
BASELINE_KEYS = frozenset(
    {
        "enabled",
        "roles",
        "actor_digest",
        "operation_digest",
        "evidence_digest",
        "counts",
    }
)
ROLE_KEYS = frozenset({"role", "parenttype", "parentfield"})
COUNT_FIELDS = ("operations", "files", "photos", "versions")
COUNT_KEYS = frozenset(COUNT_FIELDS)

ARMS = frozenset({"disabled", "role_removed"})
MODES = frozenset({"baseline", "status_denial", "replay_denial", "verify", "restored"})
RESULTS = frozenset({"observed", "refused", "failed", "unavailable"})

EXPECTED_BINDINGS = (
    "run_nonce",
    "invocation_nonce",
    "site_digest",
    "source_digest",
    "operation_id",
    "arm",
    "mode",
)
EXPECTED_DIGESTS = ("baseline_digest", "evidence_digest")
EXPECTED_KEYS = frozenset(EXPECTED_BINDINGS + EXPECTED_DIGESTS)

MAX_INPUT_BYTES = validation.MAX_REQUEST_BYTES + 65536
MAX_RESPONSE_BYTES = 16384
MAX_USER_NAME = 140

SAFE_INPUT = "Revocation probe input refused"
SAFE_RESPONSE = "Revocation probe response refused"

_NONCE = re.compile(r"[0-9a-f]{32}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SITE_DOMAIN = b"clinic-photos-revocation-site-v1"


class RevocationProtocolError(ValueError):
    """Fixed-message private-protocol refusal; never includes input contents."""


def _refuse(message):
    raise RevocationProtocolError(message)


def _require_hex(value, pattern, message):
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        _refuse(message)
    return value


def _require_bool(value, message):
    if not isinstance(value, bool):
        _refuse(message)
    return value


def _require_count(value, message):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _refuse(message)
    return value


def _require_text(value, message):
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > MAX_USER_NAME
    ):
        _refuse(message)
    return value


def _canonical_json(value, message) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        _refuse(message)


def site_digest(site: str) -> str:
    """Public binding for the exact approved disposable site, never its name."""
    if site != APPROVED_SITE:
        _refuse(SAFE_INPUT)
    return hashlib.sha256(
        _SITE_DOMAIN + b"\x00" + site.encode("utf-8")
    ).hexdigest()


def _validate_roles(roles, message):
    if not isinstance(roles, list):
        _refuse(message)
    normalized = []
    for role in roles:
        if not isinstance(role, Mapping) or set(role) != ROLE_KEYS:
            _refuse(message)
        normalized.append(
            {
                "role": _require_text(role["role"], message),
                "parenttype": _require_text(role["parenttype"], message),
                "parentfield": _require_text(role["parentfield"], message),
            }
        )
    return sorted(
        normalized,
        key=lambda item: (item["role"], item["parenttype"], item["parentfield"]),
    )


def _validate_counts(counts, message):
    if not isinstance(counts, Mapping) or set(counts) != COUNT_KEYS:
        _refuse(message)
    return {name: _require_count(counts[name], message) for name in COUNT_FIELDS}


def _validate_baseline(baseline, message):
    if not isinstance(baseline, Mapping) or set(baseline) != BASELINE_KEYS:
        _refuse(message)
    return {
        "enabled": _require_bool(baseline["enabled"], message),
        "roles": _validate_roles(baseline["roles"], message),
        "actor_digest": _require_hex(baseline["actor_digest"], _SHA256, message),
        "operation_digest": _require_hex(
            baseline["operation_digest"], _SHA256, message
        ),
        "evidence_digest": _require_hex(
            baseline["evidence_digest"], _SHA256, message
        ),
        "counts": _validate_counts(baseline["counts"], message),
    }


def baseline_digest(baseline: dict) -> str:
    """Canonical SHA256 of the complete private baseline, roles included."""
    normalized = _validate_baseline(baseline, SAFE_INPUT)
    return hashlib.sha256(_canonical_json(normalized, SAFE_INPUT)).hexdigest()


def encode_request(request: SaveRequest) -> dict:
    """Reconstruct the original API wire object from the typed request."""
    if not isinstance(request, SaveRequest):
        _refuse(SAFE_INPUT)
    payload = {"kind": request.kind, "request_key": request.request_key}
    if request.kind == contracts.ANNOTATION_APPEND:
        payload.update(
            {
                "draft": request.draft,
                "expected_version": request.expected_version,
                "original": request.original,
                "annotation": json.loads(request.annotation),
            }
        )
    else:
        payload.update(
            {
                "synthetic_patient": request.synthetic_patient,
                "image_base64": base64.b64encode(request.image_bytes).decode("ascii"),
            }
        )
    return payload


def _no_duplicate_keys(pairs):
    document = {}
    for key, value in pairs:
        if key in document:
            _refuse(SAFE_INPUT)
        document[key] = value
    return document


def _reject_constant(value):
    _refuse(SAFE_INPUT)


def _require_user_name(value, message):
    name = _require_text(value, message)
    if name in {"Guest", "Administrator"}:
        _refuse(message)
    return name


def decode_input(raw: bytes) -> dict:
    """Decode one bounded private input document; every ambiguity is refused.

    ``validation.ImageInspectionUnavailable`` from the shared image seam is not a
    protocol refusal; it propagates so Task 2 can report ``unavailable``.
    """
    if not isinstance(raw, bytes) or len(raw) > MAX_INPUT_BYTES:
        _refuse(SAFE_INPUT)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        _refuse(SAFE_INPUT)
    try:
        document = json.loads(
            text,
            object_pairs_hook=_no_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except RevocationProtocolError:
        raise
    except (TypeError, ValueError, RecursionError):
        _refuse(SAFE_INPUT)

    if validation.json_depth(document) > validation.MAX_JSON_CONTAINERS:
        _refuse(SAFE_INPUT)
    if not isinstance(document, dict) or set(document) != INPUT_KEYS:
        _refuse(SAFE_INPUT)

    schema = document["schema"]
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema != SCHEMA_VERSION
    ):
        _refuse(SAFE_INPUT)
    run_nonce = _require_hex(document["run_nonce"], _NONCE, SAFE_INPUT)
    invocation_nonce = _require_hex(
        document["invocation_nonce"], _NONCE, SAFE_INPUT
    )
    if run_nonce == invocation_nonce:
        _refuse(SAFE_INPUT)
    operation_id = _require_hex(document["operation_id"], _NONCE, SAFE_INPUT)
    source_digest = _require_hex(document["source_digest"], _SHA256, SAFE_INPUT)
    site = document["site"]
    if site != APPROVED_SITE:
        _refuse(SAFE_INPUT)
    actor = _require_user_name(document["actor"], SAFE_INPUT)
    verifier = _require_user_name(document["verifier"], SAFE_INPUT)
    if actor == verifier:
        _refuse(SAFE_INPUT)
    arm = document["arm"]
    if not isinstance(arm, str) or arm not in ARMS:
        _refuse(SAFE_INPUT)
    mode = document["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        _refuse(SAFE_INPUT)

    baseline = document["baseline"]
    if mode == "baseline":
        if baseline is not None:
            _refuse(SAFE_INPUT)
        normalized_baseline = None
    else:
        if baseline is None:
            _refuse(SAFE_INPUT)
        normalized_baseline = _validate_baseline(baseline, SAFE_INPUT)

    request = document["request"]
    if mode != "replay_denial":
        if request is not None:
            _refuse(SAFE_INPUT)
        typed_request = None
    else:
        if not isinstance(request, Mapping):
            _refuse(SAFE_INPUT)
        if (
            len(_canonical_json(dict(request), SAFE_INPUT))
            > validation.MAX_REQUEST_BYTES
        ):
            _refuse(SAFE_INPUT)
        try:
            typed_request = contracts.validate_request(request)
        except validation.PublicationValidationError:
            _refuse(SAFE_INPUT)
        if typed_request.kind != SYNTHETIC_ORIGINAL_CREATE:
            _refuse(SAFE_INPUT)

    return {
        "schema": schema,
        "run_nonce": run_nonce,
        "invocation_nonce": invocation_nonce,
        "site": site,
        "source_digest": source_digest,
        "operation_id": operation_id,
        "actor": actor,
        "verifier": verifier,
        "arm": arm,
        "mode": mode,
        "baseline": normalized_baseline,
        "request": typed_request,
    }


def _validate_expected(expected):
    if not isinstance(expected, Mapping) or set(expected) - EXPECTED_KEYS:
        _refuse(SAFE_RESPONSE)
    if not set(EXPECTED_BINDINGS) <= set(expected):
        _refuse(SAFE_RESPONSE)

    run_nonce = _require_hex(expected["run_nonce"], _NONCE, SAFE_RESPONSE)
    invocation_nonce = _require_hex(
        expected["invocation_nonce"], _NONCE, SAFE_RESPONSE
    )
    if run_nonce == invocation_nonce:
        _refuse(SAFE_RESPONSE)
    arm = expected["arm"]
    if not isinstance(arm, str) or arm not in ARMS:
        _refuse(SAFE_RESPONSE)
    mode = expected["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        _refuse(SAFE_RESPONSE)

    normalized = {
        "run_nonce": run_nonce,
        "invocation_nonce": invocation_nonce,
        "site_digest": _require_hex(expected["site_digest"], _SHA256, SAFE_RESPONSE),
        "source_digest": _require_hex(
            expected["source_digest"], _SHA256, SAFE_RESPONSE
        ),
        "operation_id": _require_hex(
            expected["operation_id"], _NONCE, SAFE_RESPONSE
        ),
        "arm": arm,
        "mode": mode,
    }
    for name in EXPECTED_DIGESTS:
        if name in expected:
            normalized[name] = _require_hex(expected[name], _SHA256, SAFE_RESPONSE)
    return normalized


def validate_response(document: dict, expected: dict) -> dict:
    """Validate one bounded public response and its exact run bindings."""
    if not isinstance(document, Mapping) or set(document) != OUTPUT_KEYS:
        _refuse(SAFE_RESPONSE)
    if len(_canonical_json(dict(document), SAFE_RESPONSE)) > MAX_RESPONSE_BYTES:
        _refuse(SAFE_RESPONSE)

    schema = document["schema"]
    if (
        isinstance(schema, bool)
        or not isinstance(schema, int)
        or schema != SCHEMA_VERSION
    ):
        _refuse(SAFE_RESPONSE)
    run_nonce = _require_hex(document["run_nonce"], _NONCE, SAFE_RESPONSE)
    invocation_nonce = _require_hex(
        document["invocation_nonce"], _NONCE, SAFE_RESPONSE
    )
    if run_nonce == invocation_nonce:
        _refuse(SAFE_RESPONSE)
    arm = document["arm"]
    if not isinstance(arm, str) or arm not in ARMS:
        _refuse(SAFE_RESPONSE)
    mode = document["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        _refuse(SAFE_RESPONSE)
    result = document["result"]
    if not isinstance(result, str) or result not in RESULTS:
        _refuse(SAFE_RESPONSE)

    clean = _require_bool(document["clean"], SAFE_RESPONSE)
    enabled = document["enabled"]
    clinician = document["clinician"]
    for value in (enabled, clinician):
        if value is not None and not isinstance(value, bool):
            _refuse(SAFE_RESPONSE)
    for name in EXPECTED_DIGESTS:
        value = document[name]
        if value is not None:
            _require_hex(value, _SHA256, SAFE_RESPONSE)
    counts = document["counts"]
    if counts is not None:
        _validate_counts(counts, SAFE_RESPONSE)

    if result == "observed":
        if not isinstance(enabled, bool) or not isinstance(clinician, bool):
            _refuse(SAFE_RESPONSE)
        if (
            document["baseline_digest"] is None
            or document["evidence_digest"] is None
            or counts is None
        ):
            _refuse(SAFE_RESPONSE)

    binding = _validate_expected(expected)
    for name in EXPECTED_BINDINGS:
        if binding[name] != document[name]:
            _refuse(SAFE_RESPONSE)
    for name in EXPECTED_DIGESTS:
        if name in binding:
            value = document[name]
            if value is None:
                if result == "observed":
                    _refuse(SAFE_RESPONSE)
            elif value != binding[name]:
                _refuse(SAFE_RESPONSE)

    return {
        "schema": schema,
        "run_nonce": run_nonce,
        "invocation_nonce": invocation_nonce,
        "site_digest": document["site_digest"],
        "source_digest": document["source_digest"],
        "operation_id": document["operation_id"],
        "arm": arm,
        "mode": mode,
        "result": result,
        "enabled": enabled,
        "clinician": clinician,
        "baseline_digest": document["baseline_digest"],
        "evidence_digest": document["evidence_digest"],
        "counts": dict(counts) if counts is not None else None,
        "clean": clean,
    }


# ---------------------------------------------------------------------------
# Task 2: fresh native baseline, denial, verification and restoration probes
# ---------------------------------------------------------------------------
EXIT_CODES = {"observed": 0, "refused": 2, "failed": 3, "unavailable": 4}
BASELINE_FD_ENV = "CLINIC_PHOTOS_REVOCATION_BASELINE_FD"
SITES_PATH = "/home/frappe/frappe-bench/sites"
PUBLICATION_GATE_CONFIG_KEY = "clinic_photos_publication_enabled"
SYNTHETIC_MODE_CONFIG_KEY = "clinic_photos_synthetic_mode"
MAX_BASELINE_BYTES = 65536

_CALLBACK_MANAGERS = (
    "before_commit",
    "after_commit",
    "before_rollback",
    "after_rollback",
)
_PHOTO_EVIDENCE_FIELDS = (
    "name",
    "storage_key",
    "original_file",
    "original_sha256",
    "patient_reference",
)
_OPERATION_DOMAIN = b"clinic-photos-revocation-operation-v1"
_EVIDENCE_DOMAIN = b"clinic-photos-revocation-evidence-v1"
_ACTOR_DOMAIN = b"clinic-photos-revocation-actor-v1"


class _ProbeRefused(Exception):
    """Bootstrap, binding or configuration refusal; no target denial is credited."""


class _ProbeUnavailable(Exception):
    """Native image inspection or evidence could not be read coherently."""


class _ProbeFailed(Exception):
    """A denial, write, callback or committed-evidence control did not hold."""


def _frappe():
    import frappe

    return frappe


def _digest(domain: bytes, payload) -> str:
    return hashlib.sha256(
        domain + b"\x00" + _canonical_json(payload, SAFE_RESPONSE)
    ).hexdigest()


def _require_normalized(document) -> dict:
    """Pin the intended internal interface: ``decode_input`` output only."""
    if not isinstance(document, Mapping) or set(document) != INPUT_KEYS:
        _refuse(SAFE_INPUT)
    schema = document["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int):
        _refuse(SAFE_INPUT)
    if schema != SCHEMA_VERSION:
        _refuse(SAFE_INPUT)

    run_nonce = _require_hex(document["run_nonce"], _NONCE, SAFE_INPUT)
    invocation_nonce = _require_hex(
        document["invocation_nonce"], _NONCE, SAFE_INPUT
    )
    if run_nonce == invocation_nonce:
        _refuse(SAFE_INPUT)
    operation_id = _require_hex(document["operation_id"], _NONCE, SAFE_INPUT)
    source_digest = _require_hex(document["source_digest"], _SHA256, SAFE_INPUT)
    site = document["site"]
    if site != APPROVED_SITE:
        _refuse(SAFE_INPUT)
    actor = _require_user_name(document["actor"], SAFE_INPUT)
    verifier = _require_user_name(document["verifier"], SAFE_INPUT)
    if actor == verifier:
        _refuse(SAFE_INPUT)
    arm = document["arm"]
    if not isinstance(arm, str) or arm not in ARMS:
        _refuse(SAFE_INPUT)
    mode = document["mode"]
    if not isinstance(mode, str) or mode not in MODES:
        _refuse(SAFE_INPUT)

    baseline = document["baseline"]
    if mode == "baseline":
        if baseline is not None:
            _refuse(SAFE_INPUT)
        normalized_baseline = None
    else:
        normalized_baseline = _validate_baseline(baseline, SAFE_INPUT)

    request = document["request"]
    if mode == "replay_denial":
        if not isinstance(request, SaveRequest):
            _refuse(SAFE_INPUT)
        if request.kind != SYNTHETIC_ORIGINAL_CREATE:
            _refuse(SAFE_INPUT)
    else:
        if request is not None:
            _refuse(SAFE_INPUT)
        request = None

    return {
        "schema": schema,
        "run_nonce": run_nonce,
        "invocation_nonce": invocation_nonce,
        "site": site,
        "source_digest": source_digest,
        "operation_id": operation_id,
        "actor": actor,
        "verifier": verifier,
        "arm": arm,
        "mode": mode,
        "baseline": normalized_baseline,
        "request": request,
    }


def _callback_state(frappe):
    """Read-only snapshot of every callback manager and the write counter.

    ``None`` means the installed shape is unknown; an unknown shape is never
    treated as clean or unchanged.
    """
    database = getattr(frappe, "db", None)
    if database is None:
        return None
    managers = []
    for name in _CALLBACK_MANAGERS:
        manager = getattr(database, name, None)
        if manager is None:
            return None
        pending = getattr(manager, "_functions", None)
        if pending is None:
            return None
        try:
            managers.append((len(pending), tuple(pending)))
        except TypeError:
            return None
    writes = getattr(database, "transaction_writes", None)
    if isinstance(writes, bool) or not isinstance(writes, int) or writes < 0:
        return None
    return (tuple(managers), writes)


def _require_clean(frappe):
    state = _callback_state(frappe)
    if state is None:
        raise _ProbeRefused
    managers, writes = state
    if writes != 0:
        raise _ProbeRefused
    if any(length for length, _pending in managers):
        raise _ProbeRefused
    return state


def _require_gates(frappe):
    conf = getattr(frappe, "conf", None)
    if conf is None:
        raise _ProbeRefused
    if conf.get(PUBLICATION_GATE_CONFIG_KEY) is not True:
        raise _ProbeRefused
    if conf.get(SYNTHETIC_MODE_CONFIG_KEY) is not True:
        raise _ProbeRefused


def _canonical_user(frappe, name):
    try:
        stored = frappe.db.get_value("User", name, "name")
    except Exception:
        raise _ProbeRefused from None
    if not isinstance(stored, str) or stored != name:
        raise _ProbeRefused


def _roles(frappe, user):
    try:
        rows = frappe.db.get_all(
            "Has Role",
            filters={"parent": user, "parenttype": "User"},
            fields=["role", "parenttype", "parentfield"],
            limit=0,
        )
    except Exception:
        raise _ProbeRefused from None
    try:
        return _validate_roles(rows, SAFE_INPUT)
    except RevocationProtocolError:
        raise _ProbeRefused from None


def _require_facts_match(fresh, roles) -> None:
    if not isinstance(fresh.enabled, bool):
        raise _ProbeRefused
    expected = bool(
        CANDIDATE_CLINICIAN_ROLES.intersection(role["role"] for role in roles)
    )
    if fresh.clinician is not expected:
        raise _ProbeRefused


def _adopt(frappe, user):
    try:
        frappe.set_user(user)
    except Exception:
        raise _ProbeRefused from None
    session = getattr(frappe, "session", None)
    if getattr(session, "user", None) != user:
        raise _ProbeRefused
    try:
        fresh = frappe_context.current_actor()
    except Exception:
        raise _ProbeRefused from None
    if fresh.user != user:
        raise _ProbeRefused
    return fresh


def _require_verifier(frappe, verifier):
    try:
        fresh = frappe_context.current_actor(verifier)
    except Exception:
        raise _ProbeRefused from None
    if fresh.user != verifier or not fresh.enabled or not fresh.clinician:
        raise _ProbeRefused
    return fresh


def _require_evidence_value(value):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise _ProbeFailed
    return value


def _domain_evidence(frappe, published):
    key = published.reservation.key
    if published.domain_doctype != inspection.PHOTO_DOCTYPE:
        # This case is synthetic_original_create only; an annotation case would need
        # its own version evidence branch, which is not implemented or claimed here.
        raise _ProbeFailed
    try:
        rows = frappe.db.get_all(
            inspection.PHOTO_DOCTYPE,
            filters={"storage_key": key},
            fields=list(_PHOTO_EVIDENCE_FIELDS),
            limit=0,
        )
    except Exception:
        raise _ProbeFailed from None
    if not isinstance(rows, list) or len(rows) != 1:
        raise _ProbeFailed
    row = rows[0]
    if not isinstance(row, Mapping) or (row.get("name") or "") != published.domain_name:
        raise _ProbeFailed
    return {
        field: _require_evidence_value(row.get(field))
        for field in _PHOTO_EVIDENCE_FIELDS
    }


def _counts(frappe, *, actor, key):
    try:
        raw = {
            "operations": frappe.db.count(
                journal.OPERATION_DOCTYPE, {"actor": actor}
            ),
            "files": frappe.db.count("File", {"file_url": key}),
            "photos": frappe.db.count(
                inspection.PHOTO_DOCTYPE, {"storage_key": key}
            ),
            "versions": frappe.db.count(
                inspection.VERSION_DOCTYPE, {"storage_key": key}
            ),
        }
    except Exception:
        raise _ProbeFailed from None
    try:
        return _validate_counts(raw, SAFE_RESPONSE)
    except RevocationProtocolError:
        raise _ProbeFailed from None


def _evidence(frappe, *, site, actor, operation_id):
    """Immutable committed operation/File/domain/byte/count evidence."""
    try:
        verified = journal.load_verified_operation(operation_id)
        if verified.site != site or verified.actor != actor:
            raise _ProbeRefused
        if verified.state != journal.PUBLISHED_STATE:
            raise _ProbeRefused
        published = verified.published
        if published is None:
            raise _ProbeFailed
        inspection.require_coherent_documents(published)
        inspection.require_coherent_head(published)
        byte_digest = writer.verify_committed_object(published)
        if byte_digest != published.reservation.expected_sha256:
            raise _ProbeFailed
        if journal.load_published(operation_id) != published:
            raise _ProbeFailed
        file_row = frappe.db.get_value(
            "File",
            published.file_name,
            list(inspection.FILE_FIELDS),
            as_dict=True,
        )
        if not isinstance(file_row, Mapping) or set(file_row) != set(
            inspection.FILE_FIELDS
        ):
            raise _ProbeFailed
        file_payload = {
            name: _require_evidence_value(file_row[name])
            for name in inspection.FILE_FIELDS
        }
        domain_payload = _domain_evidence(frappe, published)
        counts = _counts(
            frappe, actor=actor, key=published.reservation.key
        )
    except validation.ImageInspectionUnavailable:
        raise _ProbeUnavailable from None
    except (journal.JournalError, inspection.InspectionError, writer.FirstObjectError):
        raise _ProbeFailed from None

    operation_payload = {
        "site": verified.site,
        "actor": verified.actor,
        "kind": verified.kind,
        "request_key": verified.request_key,
        "payload_digest": verified.payload_digest,
        "state": verified.state,
        "generation": published.reservation.generation,
        "reservation_key": published.reservation.key,
        "target_identity": published.reservation.target_identity,
        "expected_sha256": published.reservation.expected_sha256,
        "expected_size": published.reservation.expected_size,
        "published_file": published.file_name,
        "domain_doctype": published.domain_doctype,
        "domain_name": published.domain_name,
        "version": published.version,
        "creation_identity": published.creation_identity,
    }
    operation_digest = _digest(_OPERATION_DOMAIN, operation_payload)
    evidence_digest = _digest(
        _EVIDENCE_DOMAIN,
        {
            "operation_digest": operation_digest,
            "file": file_payload,
            "domain": domain_payload,
            "bytes": {
                "sha256": byte_digest,
                "size": published.reservation.expected_size,
                "device": published.device,
                "inode": published.inode,
            },
        },
    )
    return {
        "operation_digest": operation_digest,
        "evidence_digest": evidence_digest,
        "counts": counts,
    }


def _evidence_as_verifier(frappe, *, verifier, site, actor, operation_id):
    fresh = _adopt(frappe, verifier)
    if not fresh.enabled or not fresh.clinician:
        raise _ProbeRefused
    return fresh, _evidence(
        frappe, site=site, actor=actor, operation_id=operation_id
    )


def _require_saved(operation_id, actor):
    try:
        outcome = inspection.resolve_operation(operation_id, actor=actor)
    except inspection.InspectionError:
        raise _ProbeRefused from None
    except validation.ImageInspectionUnavailable:
        raise _ProbeUnavailable from None
    if (
        not isinstance(outcome, contracts.SaveOutcome)
        or outcome.status != "saved"
        or outcome.operation_id != operation_id
    ):
        raise _ProbeRefused


def _bind_request(*, site, actor, operation_id, request):
    if not isinstance(request, SaveRequest):
        raise _ProbeRefused
    try:
        verified = journal.load_verified_operation(operation_id)
    except journal.JournalError:
        raise _ProbeRefused from None
    if verified.state != journal.PUBLISHED_STATE:
        raise _ProbeRefused
    if verified.site != site or verified.actor != actor:
        raise _ProbeRefused
    if verified.kind != request.kind or verified.request_key != request.request_key:
        raise _ProbeRefused
    if verified.payload_digest != journal.payload_digest(request):
        raise _ProbeRefused


def _deny(mode, *, operation_id, request, actor):
    """One target call; only the exact authority denial earns credit."""
    if mode == "status_denial":
        try:
            inspection.resolve_operation(operation_id, actor=actor)
        except inspection.InspectionDenied:
            return
        except inspection.InspectionError:
            raise _ProbeFailed from None
        except validation.ImageInspectionUnavailable:
            raise _ProbeUnavailable from None
        # saved/pending/unknown and every non-denial outcome fail.
        raise _ProbeFailed
    try:
        submission.stage_submission(request, actor)
    except submission.SubmissionAuthorityDenied:
        return
    except submission.SubmissionError:
        raise _ProbeFailed from None
    except validation.ImageInspectionUnavailable:
        raise _ProbeUnavailable from None
    raise _ProbeFailed


def _require_changed(arm, fresh, roles, baseline):
    baseline_roles = baseline["roles"]
    baseline_clinician = bool(
        CANDIDATE_CLINICIAN_ROLES.intersection(
            role["role"] for role in baseline_roles
        )
    )
    if not baseline_clinician:
        raise _ProbeRefused
    if arm == "disabled":
        if fresh.enabled is not False:
            raise _ProbeRefused
        if fresh.clinician is not True:
            raise _ProbeRefused
        if roles != baseline_roles:
            raise _ProbeRefused
        return
    expected = [
        role for role in baseline_roles
        if role["role"] not in CANDIDATE_CLINICIAN_ROLES
    ]
    if fresh.enabled is not True:
        raise _ProbeRefused
    if fresh.clinician is not False:
        raise _ProbeRefused
    if any(role["role"] in CANDIDATE_CLINICIAN_ROLES for role in roles):
        raise _ProbeRefused
    if roles != expected:
        raise _ProbeRefused


def _require_matches(baseline, evidence):
    if evidence["operation_digest"] != baseline["operation_digest"]:
        raise _ProbeFailed
    if evidence["evidence_digest"] != baseline["evidence_digest"]:
        raise _ProbeFailed
    if evidence["counts"] != baseline["counts"]:
        raise _ProbeFailed


def _private_baseline(*, site, actor, enabled, roles, evidence):
    baseline = {
        "enabled": enabled,
        "roles": roles,
        "actor_digest": _digest(_ACTOR_DOMAIN, {"site": site, "actor": actor}),
        "operation_digest": evidence["operation_digest"],
        "evidence_digest": evidence["evidence_digest"],
        "counts": evidence["counts"],
    }
    return _validate_baseline(baseline, SAFE_INPUT)


def _execute(frappe, document):
    """Run one fresh mode and return its observed facts, or raise a fixed class."""
    site = document["site"]
    actor = document["actor"]
    verifier = document["verifier"]
    arm = document["arm"]
    mode = document["mode"]
    operation_id = document["operation_id"]
    baseline = document["baseline"]
    request = document["request"]

    _require_gates(frappe)
    local = getattr(frappe, "local", None)
    if getattr(local, "site", None) != site:
        raise _ProbeRefused
    initial = _require_clean(frappe)
    _canonical_user(frappe, actor)
    _canonical_user(frappe, verifier)
    _require_verifier(frappe, verifier)

    private = None
    if mode == "verify":
        # Read-only internal evidence as the separate enabled verifier. The actor's
        # clinical status and operator audit are never consulted in this mode, and
        # the response's enabled/clinician fields are the verifier's facts.
        if baseline is None:
            raise _ProbeRefused
        fresh_verifier, evidence = _evidence_as_verifier(
            frappe, verifier=verifier, site=site, actor=actor,
            operation_id=operation_id,
        )
        _require_matches(baseline, evidence)
        result = (
            fresh_verifier.enabled,
            fresh_verifier.clinician,
            evidence,
            None,
        )
        if _callback_state(frappe) != initial:
            raise _ProbeFailed
        return result

    fresh = _adopt(frappe, actor)
    roles = _roles(frappe, actor)
    _require_facts_match(fresh, roles)

    if mode == "baseline":
        if fresh.enabled is not True or fresh.clinician is not True:
            raise _ProbeRefused
        _require_saved(operation_id, actor)
        fresh_verifier, evidence = _evidence_as_verifier(
            frappe, verifier=verifier, site=site, actor=actor,
            operation_id=operation_id,
        )
        private = _private_baseline(
            site=site,
            actor=actor,
            enabled=fresh.enabled,
            roles=roles,
            evidence=evidence,
        )
        result = (fresh.enabled, fresh.clinician, evidence, private)
    elif mode == "restored":
        if baseline is None:
            raise _ProbeRefused
        if fresh.enabled is not True:
            raise _ProbeRefused
        if roles != baseline["roles"]:
            raise _ProbeRefused
        if fresh.clinician is not True:
            raise _ProbeRefused
        _require_saved(operation_id, actor)
        _fresh_verifier, evidence = _evidence_as_verifier(
            frappe, verifier=verifier, site=site, actor=actor,
            operation_id=operation_id,
        )
        private = _private_baseline(
            site=site,
            actor=actor,
            enabled=fresh.enabled,
            roles=roles,
            evidence=evidence,
        )
        if baseline_digest(private) != baseline_digest(baseline):
            raise _ProbeFailed
        result = (fresh.enabled, fresh.clinician, evidence, private)
    elif mode in ("status_denial", "replay_denial"):
        if baseline is None:
            raise _ProbeRefused
        _require_changed(arm, fresh, roles, baseline)
        if mode == "replay_denial":
            _bind_request(
                site=site, actor=actor, operation_id=operation_id,
                request=request,
            )
        before = _callback_state(frappe)
        if before is None:
            raise _ProbeFailed
        _deny(mode, operation_id=operation_id, request=request, actor=actor)
        if _callback_state(frappe) != before:
            raise _ProbeFailed
        try:
            post = frappe_context.current_actor()
        except Exception:
            raise _ProbeFailed from None
        if post != fresh:
            raise _ProbeFailed
        if _roles(frappe, actor) != roles:
            raise _ProbeFailed
        _fresh_verifier, evidence = _evidence_as_verifier(
            frappe, verifier=verifier, site=site, actor=actor,
            operation_id=operation_id,
        )
        _require_matches(baseline, evidence)
        result = (fresh.enabled, fresh.clinician, evidence, None)
    else:  # pragma: no cover - decode_input and _require_normalized pin the enum
        raise _ProbeRefused

    if _callback_state(frappe) != initial:
        raise _ProbeFailed
    return result


def _expected_bindings(document):
    return {
        "run_nonce": document["run_nonce"],
        "invocation_nonce": document["invocation_nonce"],
        "site_digest": site_digest(document["site"]),
        "source_digest": document["source_digest"],
        "operation_id": document["operation_id"],
        "arm": document["arm"],
        "mode": document["mode"],
    }


def _observed_response(document, enabled, clinician, evidence, private):
    baseline_value = private if private is not None else document["baseline"]
    response = {
        "schema": SCHEMA_VERSION,
        "run_nonce": document["run_nonce"],
        "invocation_nonce": document["invocation_nonce"],
        "site_digest": site_digest(document["site"]),
        "source_digest": document["source_digest"],
        "operation_id": document["operation_id"],
        "arm": document["arm"],
        "mode": document["mode"],
        "result": "observed",
        "enabled": enabled,
        "clinician": clinician,
        "baseline_digest": (
            baseline_digest(baseline_value) if baseline_value is not None else None
        ),
        "evidence_digest": evidence["evidence_digest"],
        "counts": evidence["counts"],
        "clean": True,
    }
    validate_response(response, _expected_bindings(document))
    return response


def _failure_response(document, result):
    return {
        "schema": SCHEMA_VERSION,
        "run_nonce": document["run_nonce"],
        "invocation_nonce": document["invocation_nonce"],
        "site_digest": site_digest(document["site"]),
        "source_digest": document["source_digest"],
        "operation_id": document["operation_id"],
        "arm": document["arm"],
        "mode": document["mode"],
        "result": result,
        "enabled": None,
        "clinician": None,
        "baseline_digest": None,
        "evidence_digest": None,
        "counts": None,
        "clean": False,
    }


def run_probe(document: dict) -> tuple[int, dict, dict | None]:
    """Fresh native probe for one decoded mode document.

    Returns ``(exit code, public response, private baseline)``. The private
    baseline exists only for ``baseline`` and ``restored`` and is never part of the
    public response. Bootstrap/configuration failure is a refusal, never a denial.
    """
    document = _require_normalized(document)
    frappe = None
    destroyed = True
    status = "failed"
    response = _failure_response(document, "failed")
    private = None
    try:
        frappe = _frappe()
        try:
            frappe.init(site=document["site"], sites_path=SITES_PATH)
            frappe.connect(set_admin_as_user=False)
        except Exception:
            # Native construction failure is a refusal, never an application denial.
            raise _ProbeRefused from None
        enabled, clinician, evidence, private = _execute(frappe, document)
        response = _observed_response(
            document, enabled, clinician, evidence, private
        )
        status = "observed"
    except _ProbeRefused:
        status, response, private = "refused", _failure_response(
            document, "refused"
        ), None
    except (_ProbeUnavailable, validation.ImageInspectionUnavailable):
        status, response, private = "unavailable", _failure_response(
            document, "unavailable"
        ), None
    except Exception:
        # No raw framework diagnostic, traceback or payload reaches the response.
        status, response, private = "failed", _failure_response(
            document, "failed"
        ), None
    finally:
        if frappe is not None:
            try:
                frappe.destroy()
            except Exception:
                # Destruction failure invalidates any observed success.
                destroyed = False
    if not destroyed:
        status = "failed"
        response = _failure_response(document, "failed")
        private = None
    return EXIT_CODES[status], response, private


def _baseline_descriptor():
    """Validate the parent-supplied anonymous private descriptor before native work.

    It must be an empty, offset-0, owner-only regular file opened writable. Nothing
    is truncated, created, read back or repaired here; a rejection is a refusal.
    """
    value = os.environ.get(BASELINE_FD_ENV)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.isascii()
        or not value.isdigit()
        or len(value) > 10
    ):
        raise _ProbeRefused
    descriptor = int(value)
    if str(descriptor) != value:
        raise _ProbeRefused
    if descriptor in (0, 1, 2):
        raise _ProbeRefused
    try:
        info = os.fstat(descriptor)
    except OSError:
        raise _ProbeRefused from None
    if not stat.S_ISREG(info.st_mode):
        raise _ProbeRefused
    if info.st_uid != os.geteuid():
        raise _ProbeRefused
    mode = stat.S_IMODE(info.st_mode)
    if mode & 0o077:
        raise _ProbeRefused
    if not mode & stat.S_IWUSR:
        raise _ProbeRefused
    if info.st_size != 0:
        raise _ProbeRefused
    try:
        offset = os.lseek(descriptor, 0, os.SEEK_CUR)
    except OSError:
        raise _ProbeRefused from None
    if offset != 0:
        raise _ProbeRefused
    try:
        flags = fcntl.fcntl(descriptor, fcntl.F_GETFL)
    except OSError:
        raise _ProbeRefused from None
    if flags & os.O_ACCMODE == os.O_RDONLY:
        raise _ProbeRefused
    return descriptor


def _write_private_baseline(descriptor, private):
    payload = _canonical_json(private, SAFE_RESPONSE)
    if len(payload) > MAX_BASELINE_BYTES:
        raise _ProbeFailed
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0:
            raise _ProbeFailed
        offset += written


def _emit_simple(result):
    payload = _canonical_json(
        {"schema": SCHEMA_VERSION, "result": result}, SAFE_RESPONSE
    )
    print(payload.decode("utf-8"), flush=True)
    return EXIT_CODES[result]


def _response_bytes(response):
    try:
        payload = _canonical_json(response, SAFE_RESPONSE)
    except RevocationProtocolError:
        return None
    if len(payload) > MAX_RESPONSE_BYTES:
        return None
    return payload


def _write_public(payload):
    try:
        print(payload.decode("utf-8"), flush=True)
    except Exception:
        return False
    return True


def _emit_document(document, result):
    """Post-decode refusal/failure keeps the exact 15-key output contract."""
    payload = _response_bytes(_failure_response(document, result))
    if payload is None or not _write_public(payload):
        return EXIT_CODES["failed"]
    return EXIT_CODES[result]


class _SuppressedOutput:
    """Narrow stdlib guard that discards native diagnostics without accumulation.

    Both the Python ``sys.stdout``/``sys.stderr`` objects and the underlying
    descriptors are redirected to ``os.devnull`` around native work, so direct fd
    writes and ``print`` noise cannot reach the public response path. Restoration and
    descriptor cleanup failures are reported to the caller; they never earn credit.
    """

    def __init__(self):
        self._stdout = None
        self._stderr = None
        self._stdout_fd = None
        self._stderr_fd = None
        self._sink = None

    def open(self):
        self._stdout = sys.stdout
        self._stderr = sys.stderr
        self._stdout_fd = os.dup(1)
        self._stderr_fd = os.dup(2)
        self._sink = open(os.devnull, "w")
        sys.stdout = self._sink
        sys.stderr = self._sink
        os.dup2(self._sink.fileno(), 1)
        os.dup2(self._sink.fileno(), 2)

    def close(self):
        """Restore Python streams and descriptors; keep saved fds for fallback.

        The saved descriptors stay open until ``release`` so a restore failure can
        still emit exactly one failure document through the real saved stdout.
        """
        restored = True
        try:
            if self._stdout is not None:
                sys.stdout = self._stdout
            if self._stderr is not None:
                sys.stderr = self._stderr
        except Exception:
            restored = False
        for target, saved in ((1, self._stdout_fd), (2, self._stderr_fd)):
            if saved is None:
                # Never captured, so the stream was never redirected: nothing to
                # restore and not a failure.
                continue
            try:
                os.dup2(saved, target)
            except OSError:
                restored = False
        return restored

    def release(self):
        """Close the saved descriptors and sink after the fallback has run."""
        released = True
        for attribute in ("_stdout_fd", "_stderr_fd"):
            descriptor = getattr(self, attribute)
            setattr(self, attribute, None)
            if descriptor is None:
                continue
            try:
                os.close(descriptor)
            except OSError:
                released = False
        if self._sink is not None:
            sink, self._sink = self._sink, None
            try:
                sink.close()
            except Exception:
                released = False
        return released

    def write_saved(self, payload):
        if self._stdout_fd is None:
            return False
        try:
            os.write(self._stdout_fd, payload + b"\n")
        except OSError:
            return False
        return True


def _abort_suppression(guard, document):
    """Failure-path cleanup: restore, emit one fixed document, then release."""
    restored = False
    try:
        restored = guard.close()
    except Exception:
        restored = False
    payload = _response_bytes(_failure_response(document, "failed"))
    if payload is not None:
        try:
            if restored:
                _write_public(payload)
            else:
                guard.write_saved(payload)
        except Exception:
            pass
    try:
        guard.release()
    except Exception:
        pass
    return EXIT_CODES["failed"]


def _finish(document, exit_code, response):
    payload = _response_bytes(response)
    if payload is None:
        exit_code = EXIT_CODES["failed"]
        payload = _response_bytes(_failure_response(document, "failed"))
        if payload is None:
            return EXIT_CODES["failed"]
    if not _write_public(payload):
        return EXIT_CODES["failed"]
    return exit_code


def main(argv: list[str] | None = None) -> int:
    """Bounded CLI: ``--site SITE --operation-id ID --mode MODE`` plus stdin JSON."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if (
        len(arguments) != 6
        or arguments[0] != "--site"
        or arguments[2] != "--operation-id"
        or arguments[4] != "--mode"
    ):
        return _emit_simple("refused")
    site, operation_id, mode = arguments[1], arguments[3], arguments[5]
    if site != APPROVED_SITE or mode not in MODES:
        return _emit_simple("refused")
    if _NONCE.fullmatch(operation_id) is None:
        return _emit_simple("refused")

    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return _emit_simple("refused")
    try:
        raw_input = stream.read(MAX_INPUT_BYTES + 1)
    except Exception:
        return _emit_simple("refused")
    if not isinstance(raw_input, bytes) or len(raw_input) > MAX_INPUT_BYTES:
        return _emit_simple("refused")

    try:
        document = decode_input(raw_input)
    except validation.ImageInspectionUnavailable:
        return _emit_simple("unavailable")
    except Exception:
        return _emit_simple("refused")
    if (
        document["site"] != site
        or document["operation_id"] != operation_id
        or document["mode"] != mode
    ):
        return _emit_document(document, "refused")

    try:
        descriptor = _baseline_descriptor()
    except _ProbeRefused:
        return _emit_document(document, "refused")
    requires_baseline = mode in {"baseline", "restored"}
    if requires_baseline != (descriptor is not None):
        return _emit_document(document, "refused")

    guard = _SuppressedOutput()
    try:
        guard.open()
    except Exception:
        return _abort_suppression(guard, document)

    try:
        exit_code, response, private = run_probe(document)
    except BaseException:
        try:
            guard.close()
        except Exception:
            pass
        try:
            guard.release()
        except Exception:
            pass
        raise

    try:
        restored = guard.close()
    except Exception:
        restored = False
    if not restored:
        # The saved stdout descriptor is still live, so the failure document is
        # emitted through it exactly once before the descriptor is released.
        payload = _response_bytes(_failure_response(document, "failed"))
        if payload is not None:
            try:
                guard.write_saved(payload)
            except Exception:
                pass
        try:
            guard.release()
        except Exception:
            pass
        return EXIT_CODES["failed"]

    released = True
    try:
        released = guard.release()
    except Exception:
        released = False
    if not released:
        # Cleanup failed after native work; the result cannot earn credit.
        return _emit_document(document, "failed")

    if response["result"] == "observed" and requires_baseline:
        try:
            if private is None:
                raise _ProbeFailed
            if baseline_digest(private) != response["baseline_digest"]:
                raise _ProbeFailed
            _write_private_baseline(descriptor, private)
        except Exception:
            exit_code = EXIT_CODES["failed"]
            response = _failure_response(document, "failed")

    return _finish(document, exit_code, response)


if __name__ == "__main__":
    sys.exit(main())
