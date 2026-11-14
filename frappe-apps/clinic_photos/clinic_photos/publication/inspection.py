"""Read-only current-access status, replay resolution and recovery inspection.

Two callers need evidence about one durable operation and neither may mutate it:

* the ordinary actor-only status/replay path, which resolves the originating
  clinician's own operation after freshly re-checking the current actor, current site,
  current native draft/photo/Patient authority, the fresh persisted File document with
  its protected-reference authorization and the effective ``get_content`` chain, and
* the separately allowlisted recovery inspection, which reports operation-bound audit,
  digest and consistency for one opaque ID and never grants patient or File rights.
  It is a Python-internal read-only audit (no added endpoint), gated by the same exact
  ``True`` publication flag and by operation-site equality, and it labels coherent
  byte-verified publication evidence ``published_verified`` — a neutral audit fact,
  not the ordinary clinical ``saved`` claim.

Nothing here writes: no ``set_value``, commit, rollback, filesystem mutation,
protected-key allocation, publication-state change, owner minting, lock or abort latch
happens on these paths, and no recovery, resume or purge capability is exposed. Every
decision is re-derived from the current server context on each call; a client cannot
supply an actor, capability or allowlist bypass.

The byte proof itself is the writer's single side-effect-free
``verify_committed_object``: it accepts only the durable ``PublishedOutcome`` this
package verified and returns a digest. The publication attempt keeps its own
owner-bound wrapper (live exclusion owner, poisoning, exact-current-head assertion), so
completing the read-only path does not weaken Task 8.

Historical replay: a published version ``N`` stays ``N`` after a legitimate ``N+1``.
The current head must be a coherent committed version of the same draft at or after
``N``; equality requires the identical published version. Corrupt or mismatched head
evidence yields ``unknown``. Repeated reads may share one REPEATABLE-READ snapshot, so
this is not a claim of atomic cross-store authorization or of detecting a concurrent
revocation in a later transaction without native evidence.

Read-only means read-only: an ``ABORTED`` row is reported ``unknown`` because the
durable record does not record *which* definite refusal it was, and ``PREPARED`` /
``QUARANTINED`` rows stay ``unknown`` with no resumption, repair or deletion.
"""

import re

from clinic_photos.access import frappe_context, storage
from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES, may_read
from clinic_photos.publication import journal, submission, writer
from clinic_photos.publication.contracts import ANNOTATION_APPEND, SaveOutcome

# Explicit, default-empty site allowlist of enabled clinicians who may inspect
# recovery evidence for one operation. A missing, empty, non-sequence or non-string
# value denies; an Administrator session is never exempt.
RECOVERY_OPERATOR_CONFIG_KEY = "clinic_photos_recovery_operators"
# The same exact-True server publication gate the ordinary entries read. Kept local
# so this read-only module does not depend on the API layer.
PUBLICATION_GATE_CONFIG_KEY = "clinic_photos_publication_enabled"

FILE_DOCTYPE = "File"
DRAFT_DOCTYPE = "Clinic Photo Draft"
VERSION_DOCTYPE = "Clinic Photo Annotation Version"
PHOTO_DOCTYPE = "Clinic Photo"
FILE_FIELDS = ("name", "file_url", "file_name", "is_private", "owner", "file_size")

SAFE_INSPECTION_DENIED = "Durable photo-save operation is unavailable"
SAFE_INSPECTION_UNAVAILABLE = "Read-only publication evidence is unavailable"

_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


class InspectionError(Exception):
    """Fixed-message read-only evidence refusal; identifiers are never included."""


class InspectionDenied(InspectionError):
    """Unknown, foreign or no-longer-authorised operation; one opaque refusal."""


class InspectionUnavailable(InspectionError):
    """The evidence for an authorised operation could not be read coherently."""


def _frappe():
    import frappe

    return frappe


def _valid_operation_id(value) -> bool:
    return isinstance(value, str) and _OPERATION_ID.fullmatch(value) is not None


def require_current_clinician(actor: str):
    """Fresh current server actor; a client-supplied capability grants nothing."""
    if (
        not isinstance(actor, str)
        or not actor
        or actor in NON_CLINICAL_SESSION_IDENTITIES
    ):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    fresh = frappe_context.current_actor()
    if fresh.user != actor or not fresh.enabled or not fresh.clinician:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    return fresh


def require_operator(actor: str) -> None:
    """The explicit default-empty operator allowlist; exact membership only."""
    frappe = _frappe()
    conf = getattr(frappe, "conf", None)
    allowlist = conf.get(RECOVERY_OPERATOR_CONFIG_KEY) if conf is not None else None
    if not isinstance(allowlist, (list, tuple)) or not allowlist:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    if any(not isinstance(entry, str) for entry in allowlist):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    if not any(entry == actor for entry in allowlist):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)


def _require_publication_enabled() -> None:
    """The server-owned publication gate, exact boolean ``True`` only."""
    frappe = _frappe()
    conf = getattr(frappe, "conf", None)
    if conf is None or conf.get(PUBLICATION_GATE_CONFIG_KEY) is not True:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)


def _require_current_site(verified) -> None:
    """The durable operation's site must still be the current server site."""
    frappe = _frappe()
    site = getattr(getattr(frappe, "local", None), "site", "")
    if not isinstance(site, str) or not site or site != verified.site:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)


def _durable_state(value) -> str:
    if isinstance(value, str) and value in journal.DURABLE_STATES:
        return value
    return "UNKNOWN"


# ---------------------------------------------------------------------------
# Immutable document and domain linkage (shared with the publication attempt)
# ---------------------------------------------------------------------------
def _require_annotation_linkage(published) -> None:
    """The immutable committed version row this outcome names."""
    import frappe

    reservation = published.reservation
    row = (
        frappe.db.get_value(
            VERSION_DOCTYPE,
            published.domain_name,
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
        row is None
        or (row.get("name") or "") != published.domain_name
        or (row.get("draft") or "") != reservation.draft
        or row.get("version_number") != published.version
        or (row.get("annotation_file") or "") != published.file_name
        or (row.get("storage_key") or "") != reservation.key
        or row.get("state") != "committed"
    ):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)


def _require_original_linkage(published) -> None:
    """Exactly one immutable original row for this key, bound to this object."""
    import frappe

    reservation = published.reservation
    rows = frappe.db.get_all(
        PHOTO_DOCTYPE,
        filters={"storage_key": reservation.key},
        fields=[
            "name",
            "storage_key",
            "original_file",
            "original_sha256",
            "patient_reference",
        ],
        limit=0,
    )
    if len(rows or ()) != 1:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    row = rows[0]
    if (
        (row.get("name") or "") != published.domain_name
        or (row.get("storage_key") or "") != reservation.key
        or (row.get("original_file") or "") != published.file_name
        or (row.get("original_sha256") or "") != reservation.expected_sha256
        or (row.get("patient_reference") or "")
        != reservation.synthetic_patient
    ):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)


def require_coherent_documents(published, *, for_update: bool = False) -> None:
    """The persisted File and the immutable domain linkage of a published outcome.

    One derivation shared by the publication attempt's post-commit readback and by
    read-only status. It reads only and never re-derives a missing value from a request
    or from the current draft pointer (that check is mode-specific, see below).
    """
    import frappe

    reservation = published.reservation
    row = (
        frappe.db.get_value(
            FILE_DOCTYPE,
            published.file_name,
            list(FILE_FIELDS),
            as_dict=True,
            for_update=for_update,
        )
        or None
    )
    if row is None or (row.get("name") or "") != published.file_name:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)

    raw = row.get("file_url") or ""
    try:
        key = storage.canonical_private_key(raw)
    except storage.InvalidStoragePath:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE) from None
    if raw != key or key != reservation.key or not storage.is_reserved_key(key):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    if (row.get("file_name") or "") != key.rsplit("/", 1)[-1]:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    if not int(row.get("is_private") or 0):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    if (row.get("owner") or "") != reservation.actor:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    try:
        persisted_size = int(row.get("file_size") or 0)
    except (TypeError, ValueError):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE) from None
    if persisted_size != reservation.expected_size:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)

    if reservation.kind == ANNOTATION_APPEND:
        _require_annotation_linkage(published)
        return
    _require_original_linkage(published)


def _require_head(draft_row, published) -> None:
    import frappe

    reservation = published.reservation
    head = draft_row.get("current_version_number")
    if isinstance(head, bool) or not isinstance(head, int) or head < 1:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    if head < published.version:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    current = draft_row.get("current_version") or ""
    if head == published.version:
        # Equality must name the identical published version, not just the number.
        if current != published.domain_name:
            raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
        return
    row = (
        frappe.db.get_value(
            VERSION_DOCTYPE,
            current,
            ["name", "draft", "version_number", "state"],
            as_dict=True,
        )
        or None
    )
    if (
        row is None
        or not current
        or (row.get("name") or "") != current
        or (row.get("draft") or "") != reservation.draft
        or row.get("version_number") != head
        or row.get("state") != "committed"
    ):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)


def _draft_head(published):
    import frappe

    return (
        frappe.db.get_value(
            DRAFT_DOCTYPE,
            published.reservation.draft,
            ["current_version_number", "current_version"],
            as_dict=True,
        )
        or None
    )


def require_exact_current_head(published) -> None:
    """Publication-attempt mode: the draft pointer is exactly this version.

    Task 8's assertion, unchanged. Only the attempt that just produced this outcome
    may require the pointer to still be at it.
    """
    if published.reservation.kind != ANNOTATION_APPEND:
        return
    draft_row = _draft_head(published)
    if (
        draft_row is None
        or draft_row.get("current_version_number") != published.version
        or (draft_row.get("current_version") or "") != published.domain_name
    ):
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)


def require_coherent_head(published) -> None:
    """Read-only mode: a coherent committed head at or after this version."""
    if published.reservation.kind != ANNOTATION_APPEND:
        return
    draft_row = _draft_head(published)
    if draft_row is None:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    _require_head(draft_row, published)


# ---------------------------------------------------------------------------
# Durable outcome binding and current authorization
# ---------------------------------------------------------------------------
def _bound_outcome(verified):
    """The durable outcome derived from this same verified operation row, or ``None``.

    ``journal.load_verified_operation`` derives the immutable ``PublishedOutcome`` from
    the exact row it verified and carries it on ``verified.published``, so status and
    audit evidence are one derivation rather than fields mixed from a separately read
    raw row. The full reserved and outcome evidence is bound here — key, generation,
    draft, expected version, patient, target identity, byte hash/size, File, domain,
    version and creation identity — not a five-field identity subset.
    """
    published = verified.published
    if published is None:
        return None
    reservation = published.reservation
    if (
        reservation.id != verified.id
        or reservation.site != verified.site
        or reservation.actor != verified.actor
        or reservation.kind != verified.kind
        or reservation.payload_digest != verified.payload_digest
        or reservation.draft != verified.request.draft
        or reservation.expected_version != verified.request.expected_version
        or reservation.synthetic_patient != verified.request.synthetic_patient
        or reservation.state != journal.PUBLISHED_STATE
        or published.domain_doctype
        != journal.DOMAIN_DOCTYPES[reservation.kind]
        or not published.file_name
        or not published.creation_identity
    ):
        return None
    return published


def _require_patient_read(fresh, verified) -> None:
    """The annotation authority does not check Patient read; require it here.

    This is a narrow addition to the resolution path, not a blanket domain permission
    bypass: the synthetic kind already checks its own Patient read in the submission
    authority.
    """
    import frappe

    if verified.kind != ANNOTATION_APPEND:
        return
    patient = frappe.db.get_value(
        PHOTO_DOCTYPE, verified.request.original, "patient_reference"
    )
    if not isinstance(patient, str) or not patient or patient.strip() != patient:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    native_read = bool(
        frappe.has_permission("Patient", "read", doc=patient, throw=False)
    )
    if not may_read(fresh, native_read):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)


def _require_current_authority(verified, actor) -> None:
    """Fresh actor, site and native target authority for this exact request."""
    fresh = require_current_clinician(actor)
    try:
        submission.require_target_authority(verified.request, actor, verified.site)
    except submission.SubmissionUnavailable:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE) from None
    except submission.SubmissionError:
        raise InspectionDenied(SAFE_INSPECTION_DENIED) from None
    _require_patient_read(fresh, verified)


def _require_current_file_access(published) -> None:
    """Fresh persisted File: native + reference read, then the effective content chain.

    The document is loaded fresh and must carry no truthy cached content, so the
    shipped ``ProtectedFileMixin.get_content`` chain (authorization, then content)
    really runs; its output is discarded. The predicate mirrors the pinned native
    ``File.__init__`` default (``content = self.get("content") or b""``) and native
    ``get_content`` truthiness: ``b""``/``None`` mean "read from disk", while supplied
    bytes/strings would short-circuit the chain and are refused. A denial or exception
    anywhere in that chain withholds ``saved`` and is never bypassed by the good
    on-disk bytes.
    """
    import frappe

    try:
        file_doc = frappe.get_doc(FILE_DOCTYPE, published.file_name)
    except Exception:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE) from None
    if (getattr(file_doc, "name", "") or "") != published.file_name:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    if file_doc.get("content"):
        # Pre-supplied truthy content would short-circuit the effective chain.
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
    try:
        frappe_context.require_protected_read(file_doc)
        file_doc.get_content()
    except Exception:
        raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE) from None


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------
def _saved_outcome(verified, actor) -> SaveOutcome:
    """Known immutable version, or ``unknown`` when any proof step cannot hold."""
    published = _bound_outcome(verified)
    if published is None:
        return SaveOutcome("unknown", verified.id)
    try:
        # Bracket the byte proof with coherent document/outcome and current
        # authorization checks; any change on either side withholds the claim.
        require_coherent_documents(published)
        require_coherent_head(published)
        _require_current_file_access(published)
        writer.verify_committed_object(published)
        # Re-bind the full durable outcome after the byte proof and require equality
        # with the initial derivation; a same-payload row change observed here is not
        # a saved claim. Then re-run the document, head, File/protected-reference and
        # current-authority checks.
        if journal.load_published(verified.id) != published:
            raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
        require_coherent_documents(published)
        require_coherent_head(published)
        _require_current_file_access(published)
        _require_current_authority(verified, actor)
    except (InspectionError, writer.FirstObjectError, journal.JournalError):
        return SaveOutcome("unknown", verified.id)
    return SaveOutcome(
        "saved",
        verified.id,
        version=published.version,
        file_name=published.file_name,
    )


def resolve_operation(operation_id: str, *, actor: str) -> SaveOutcome:
    """Actor-only known-outcome resolution for one operation; read-only.

    The stored operation must belong to the current actor and the current target
    authority (including Patient read for an annotation) must still hold. Unknown,
    malformed, foreign and no-longer-authorised identities share one opaque refusal,
    and no state, version, payload or path is disclosed by it. No commit, rollback,
    write, unlink, lock or key allocation happens.
    """
    if not _valid_operation_id(operation_id):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    require_current_clinician(actor)

    try:
        verified = journal.load_verified_operation(operation_id)
    except journal.JournalError:
        raise InspectionDenied(SAFE_INSPECTION_DENIED) from None
    if verified.actor != actor:
        raise InspectionDenied(SAFE_INSPECTION_DENIED)

    _require_current_authority(verified, actor)

    if verified.state == journal.SUBMITTED_STATE:
        return SaveOutcome("pending", verified.id)
    if verified.state == journal.PUBLISHED_STATE:
        return _saved_outcome(verified, actor)
    # PREPARED, ABORTED and QUARANTINED are non-resumable here and none of them
    # carries a durable external outcome this module may claim.
    return SaveOutcome("unknown", verified.id)


def _audit_evidence(verified) -> dict:
    """Audit fields derived from the same verified row; bracketed outcome evidence.

    The immutable outcome comes from ``verified.published`` — the same row
    verification — not from a separately read raw row. After the byte proof the full
    durable outcome is re-loaded and must equal the initial derivation, and the
    documents must still be coherent; a change observed during the proof withholds
    publication verification instead of reporting a verified label.
    """
    if verified.state == journal.SUBMITTED_STATE:
        return {
            "outcome": "pending",
            "consistent": True,
            "expected_sha256": None,
            "bytes_verified": None,
        }
    if verified.state != journal.PUBLISHED_STATE:
        return {
            "outcome": "unknown",
            # QUARANTINED carries no verified key/byte/outcome shape, so consistency
            # is conservatively unknown rather than asserted.
            "consistent": (
                None if verified.state == journal.QUARANTINED_STATE else True
            ),
            "expected_sha256": None,
            "bytes_verified": None,
        }

    published = _bound_outcome(verified)
    if published is None:
        return {
            "outcome": "unknown",
            "consistent": False,
            "expected_sha256": None,
            "bytes_verified": None,
        }
    expected_sha256 = published.reservation.expected_sha256
    try:
        require_coherent_documents(published)
        require_coherent_head(published)
    except InspectionUnavailable:
        return {
            "outcome": "unknown",
            "consistent": False,
            "expected_sha256": expected_sha256,
            "bytes_verified": None,
        }
    try:
        writer.verify_committed_object(published)
    except writer.FirstObjectError:
        return {
            "outcome": "unknown",
            "consistent": True,
            "expected_sha256": expected_sha256,
            "bytes_verified": False,
        }
    try:
        if journal.load_published(verified.id) != published:
            raise InspectionUnavailable(SAFE_INSPECTION_UNAVAILABLE)
        require_coherent_documents(published)
        require_coherent_head(published)
    except (InspectionUnavailable, journal.JournalError):
        return {
            "outcome": "unknown",
            "consistent": False,
            "expected_sha256": expected_sha256,
            "bytes_verified": None,
        }
    return {
        # A neutral audit outcome label: byte-verified publication evidence, never the
        # ordinary clinical `saved` claim (no Patient/File read rights were exercised).
        "outcome": "published_verified",
        "consistent": True,
        "expected_sha256": expected_sha256,
        "bytes_verified": True,
    }


def inspect_operation(operation_id: str, *, actor: str) -> dict:
    """Operation-bound read-only audit for one allowlisted operator.

    Requires the current enabled clinician, exact allowlist membership, the exact
    ``True`` server publication gate and the durable operation's own site. Returns only
    the opaque operation identity, its durable state, kind, payload digest, expected
    SHA-256, conservative audit outcome, consistency flag and byte-proof status. The
    coherent byte-verified label is ``published_verified``, never the ordinary clinical
    ``saved`` claim. It never returns annotation content, image payload, storage paths,
    file names, patient/draft/original identifiers, credentials or another user's
    identity, never performs a write, and never routes through the ordinary replay or
    its identifiers. A cross-actor audit may hash the object without Patient or File
    read rights; that grants no such right and no domain disclosure.
    """
    if not _valid_operation_id(operation_id):
        raise InspectionDenied(SAFE_INSPECTION_DENIED)
    require_current_clinician(actor)
    require_operator(actor)
    _require_publication_enabled()

    try:
        verified = journal.load_verified_operation(operation_id)
    except journal.JournalOperationMissing:
        raise InspectionDenied(SAFE_INSPECTION_DENIED) from None
    except journal.JournalError:
        return {
            "operation_id": operation_id,
            "state": "UNKNOWN",
            "kind": None,
            "payload_digest": None,
            "expected_sha256": None,
            "outcome": "unknown",
            "consistent": False,
            "bytes_verified": None,
        }
    _require_current_site(verified)

    evidence = _audit_evidence(verified)

    # Bracket the audit evidence with the current gate, site and operator authority;
    # no Patient/File read right is added or required by this audit path.
    require_current_clinician(actor)
    require_operator(actor)
    _require_publication_enabled()
    _require_current_site(verified)

    return {
        "operation_id": verified.id,
        "state": verified.state,
        "kind": verified.kind,
        "payload_digest": verified.payload_digest,
        "expected_sha256": evidence["expected_sha256"],
        "outcome": evidence["outcome"],
        "consistent": evidence["consistent"],
        "bytes_verified": evidence["bytes_verified"],
    }
