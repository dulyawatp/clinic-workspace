"""Server-bound submission boundary for the default-off photo-save entry.

The authenticated API passes a validated immutable :class:`SaveRequest` and the
current session actor here. This module freshly re-reads the current actor, the
actual site and the native target authority, then lets the journal stage one
SUBMITTED row in the caller's transaction. The journal stages and fsyncs the accepted
image bytes into the bound site's private staging area before that row exists, so the
row itself is metadata only. This module never commits, rolls back, writes File
content or allocates a protected key.

Journal identity/digest equality is never treated as domain authority: a
different actor, a revoked or disabled author, a draft whose immutable author
changed, an original bound to another draft/patient, a missing synthetic Patient,
a synthetic Patient that an additional site has not named explicitly in server
configuration, or any native permission veto denies before staging.

The after-commit notification is registered once per staged submission request.
It enqueues an opaque operation identity through
``clinic_photos.publication.launcher.enqueue_submission`` (Task 4). While that
module is absent, the notification is a safe no-op: the committed row stays
SUBMITTED and the existing scheduler rediscovery owns eventual repair. Queue or
import failure is never allowed to raise out of the after-commit callback, and
no publication outcome is claimed here.
"""

import importlib
import re

from clinic_photos.access import frappe_context
from clinic_photos.access.policy import (
    NON_CLINICAL_SESSION_IDENTITIES,
    Actor,
    may_edit_draft,
    may_read,
)
from clinic_photos.publication import journal, sites
from clinic_photos.publication.contracts import (
    ANNOTATION_APPEND,
    SYNTHETIC_ORIGINAL_CREATE,
    SaveOutcome,
    SaveRequest,
)

SYNTHETIC_SITE = sites.LAB_SITE
SYNTHETIC_MODE_CONFIG_KEY = "clinic_photos_synthetic_mode"
# Explicit, default-empty Patient allow-list for a synthetic site other than the
# disposable lab. A missing, empty, non-sequence or non-string value denies.
SYNTHETIC_PATIENTS_CONFIG_KEY = "clinic_photos_synthetic_patients"

LAUNCHER_MODULE = "clinic_photos.publication.launcher"
LAUNCHER_ENQUEUE = "enqueue_submission"
NOTIFICATION_ATTR = "clinic_photos_submission_notifications"

SAFE_SUBMISSION_UNAVAILABLE = "Durable photo-save submission is unavailable"
SAFE_SUBMISSION_CONFLICT = "Durable photo-save request conflicts with an existing operation"

_OPERATION_ID = re.compile(r"[0-9a-f]{32}")


class SubmissionError(Exception):
    """Fixed-message submission refusal; payload contents are never included."""


class SubmissionAuthorityDenied(SubmissionError):
    pass


class SubmissionTargetDenied(SubmissionError):
    pass


class SubmissionUnavailable(SubmissionError):
    pass


class SubmissionConflictUnverified(SubmissionError):
    """A conflict without a verified original operation identity."""


def _frappe():
    import frappe

    return frappe


def _clean(value) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


def _valid_operation_id(value) -> bool:
    return isinstance(value, str) and _OPERATION_ID.fullmatch(value) is not None


def _actual_site(frappe) -> str:
    site = getattr(frappe.local, "site", "")
    if not _clean(site):
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE)
    return site


def _row(frappe, doctype: str, name: str, fields) -> dict | None:
    return (
        frappe.db.get_value(doctype, name, list(fields), as_dict=True) or None
    )


def _quiet_has_permission(frappe, doctype: str, ptype: str, doc: str) -> bool:
    """Quiet native permission decision for one named document.

    ``frappe.has_permission`` accepts no ``print_logs`` argument; it forwards
    ``print_logs=throw`` to ``frappe.permissions.has_permission``. Passing
    ``throw=False`` therefore requests the same silent decision, while the
    native implementation still resolves the docname, role, owner, share and
    user permissions for the current session actor.
    """
    return bool(frappe.has_permission(doctype, ptype, doc=doc, throw=False))


def _fresh_actor(frappe, actor: str) -> Actor:
    if not _clean(actor):
        raise SubmissionAuthorityDenied
    fresh = frappe_context.current_actor()
    if (
        fresh.user != actor
        or not fresh.enabled
        or not fresh.clinician
        or actor in NON_CLINICAL_SESSION_IDENTITIES
    ):
        raise SubmissionAuthorityDenied
    return fresh


def _require_annotation_target(frappe, request: SaveRequest, actor) -> None:
    draft_row = _row(
        frappe, "Clinic Photo Draft", request.draft, ("author", "photo")
    )
    if not draft_row:
        raise SubmissionTargetDenied

    author = draft_row.get("author")
    photo = draft_row.get("photo")
    if not _clean(author) or author != actor.user:
        raise SubmissionTargetDenied
    if not _clean(photo) or photo != request.original:
        raise SubmissionTargetDenied

    draft_read = _quiet_has_permission(
        frappe, "Clinic Photo Draft", "read", request.draft
    )
    if not may_read(actor, draft_read):
        raise SubmissionTargetDenied
    draft_write = _quiet_has_permission(
        frappe, "Clinic Photo Draft", "write", request.draft
    )
    if not may_edit_draft(actor, author, draft_write):
        raise SubmissionTargetDenied

    photo_row = _row(
        frappe, "Clinic Photo", request.original, ("patient_reference",)
    )
    if not photo_row or not _clean(photo_row.get("patient_reference")):
        raise SubmissionTargetDenied
    photo_read = _quiet_has_permission(
        frappe, "Clinic Photo", "read", request.original
    )
    if not may_read(actor, photo_read):
        raise SubmissionTargetDenied


def _configured_synthetic_patients(conf) -> tuple:
    """Exact configured synthetic Patient allow-list; malformed values deny all.

    A missing, empty, non-sequence or non-string entry value yields no allowance at
    all: the additional site stays default-deny instead of guessing at intent.
    """
    getter = getattr(conf, "get", None)
    if not callable(getter):
        return ()
    try:
        value = getter(SYNTHETIC_PATIENTS_CONFIG_KEY)
    except Exception:
        return ()
    if not isinstance(value, (list, tuple)) or not value:
        return ()
    if any(not _clean(entry) for entry in value):
        return ()
    return tuple(value)


def _require_synthetic_target(frappe, request: SaveRequest, actor) -> None:
    conf = getattr(frappe, "conf", None)
    site = getattr(frappe.local, "site", "")
    if not sites.is_allowed(site, conf):
        raise SubmissionTargetDenied
    if conf is None or conf.get(SYNTHETIC_MODE_CONFIG_KEY) is not True:
        raise SubmissionTargetDenied

    patient = request.synthetic_patient
    # The lab site keeps its historical behavior. Any additional site must name
    # this exact Patient in server configuration, checked before any record is
    # read, so a rejected target is never exists-probed or permission-checked.
    if site != sites.LAB_SITE and (
        not _clean(patient) or patient not in _configured_synthetic_patients(conf)
    ):
        raise SubmissionTargetDenied

    if not frappe.db.exists("Patient", patient):
        raise SubmissionTargetDenied
    patient_read = _quiet_has_permission(
        frappe, "Patient", "read", patient
    )
    if not may_read(actor, patient_read):
        raise SubmissionTargetDenied


def require_target_authority(
    request: SaveRequest, actor: str, site: str
) -> None:
    """Fresh current target authority for one typed request; grants nothing.

    Used both before staging and by the actor-only status wrapper after it has
    bound the stored operation to the current actor. It only reads documents and
    native permission decisions. An additional (non-lab) site is synthetic-only:
    the annotation kind is refused there before any draft/original read.
    """
    if not isinstance(request, SaveRequest) or not _clean(site):
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE)

    frappe = _frappe()
    if _actual_site(frappe) != site:
        raise SubmissionAuthorityDenied
    fresh = _fresh_actor(frappe, actor)

    if request.kind == ANNOTATION_APPEND:
        # An additional site accepts only the synthetic kind, so this refusal
        # happens before any draft/original row is read. The lab site keeps its
        # historical annotation authority.
        if site != sites.LAB_SITE:
            raise SubmissionTargetDenied
        _require_annotation_target(frappe, request, fresh)
        return
    if request.kind == SYNTHETIC_ORIGINAL_CREATE:
        _require_synthetic_target(frappe, request, fresh)
        return
    raise SubmissionTargetDenied


def _require_notification_seam(frappe) -> None:
    manager = getattr(frappe.db, "after_commit", None)
    if manager is None or not callable(getattr(manager, "add", None)):
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE)


def _advanced_replay(operation_id: str, actor: str) -> SaveOutcome:
    """Resolve one advanced same-tuple replay, read-only, after current authorization.

    Reached only when the stored identity tuple and payload digest already matched the
    presented request (a changed digest raises a conflict before this point) and the
    current target authority was just re-checked. A published outcome resolves its
    immutable version; every other advanced state stays ``unknown``. Nothing is
    written, re-keyed or re-published, and no new authority is created.
    """
    from clinic_photos.publication import inspection

    try:
        return inspection.resolve_operation(operation_id, actor=actor)
    except inspection.InspectionError:
        return SaveOutcome("unknown", operation_id)


def stage_submission(request: SaveRequest, actor: str) -> SaveOutcome:
    """Stage one SUBMITTED row and return the truthful pending/conflict outcome.

    No commit, rollback, File/content write or protected-key reservation happens
    here. A same-tuple/same-payload replay resolves the existing operation ID while
    the row is still SUBMITTED, and resolves a published outcome's immutable version
    once the current authorization holds. A changed payload maps the verified original
    ID to ``conflict``. Any other advanced row maps its verified ID to ``unknown``.
    """
    if not isinstance(request, SaveRequest):
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE)

    frappe = _frappe()
    site = _actual_site(frappe)
    require_target_authority(request, actor, site)
    _require_notification_seam(frappe)

    try:
        staged = journal.stage_submission(request, actor=actor, site=site)
    except journal.JournalConflict as error:
        if not _valid_operation_id(error.operation_id):
            raise SubmissionConflictUnverified(SAFE_SUBMISSION_CONFLICT) from None
        return SaveOutcome("conflict", error.operation_id)
    except journal.JournalStateError as error:
        if not _valid_operation_id(error.operation_id):
            raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE) from None
        return _advanced_replay(error.operation_id, actor)
    except journal.JournalError:
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE) from None

    return SaveOutcome("pending", staged.id)


def _enqueue_committed_submission(operation_id: str) -> None:
    """After-commit notification: opaque identity only, failure stays pending."""
    try:
        launcher = importlib.import_module(LAUNCHER_MODULE)
        enqueue = getattr(launcher, LAUNCHER_ENQUEUE, None)
        if not callable(enqueue):
            return
        enqueue(operation_id)
    except Exception:
        # Task 4 owns the real queue seam. An absent module, unavailable queue or
        # launcher error must not turn a committed submission into a request
        # failure, and must not fabricate a publication outcome.
        return


def register_enqueue_after_commit(operation_id: str) -> None:
    """Register the native after-commit notification once for this request."""
    if not _valid_operation_id(operation_id):
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE)

    frappe = _frappe()
    _require_notification_seam(frappe)

    registered = getattr(frappe.local, NOTIFICATION_ATTR, None)
    if registered is None:
        registered = set()
        setattr(frappe.local, NOTIFICATION_ATTR, registered)
    if operation_id in registered:
        return

    try:
        frappe.db.after_commit.add(
            lambda: _enqueue_committed_submission(operation_id)
        )
    except Exception:
        raise SubmissionUnavailable(SAFE_SUBMISSION_UNAVAILABLE) from None
    registered.add(operation_id)
