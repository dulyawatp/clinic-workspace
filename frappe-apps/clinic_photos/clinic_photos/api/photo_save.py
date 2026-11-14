"""Default-off authenticated entry for durable photo save.

The public methods are registered for POST only and stay disabled unless the
server site configuration explicitly enables publication. A current enabled
clinician's native ``sid`` session plus a nonempty stored CSRF token and the
matching explicit ``X-Frappe-CSRF-Token`` header are required. ``Guest`` and
``Administrator`` never qualify as ordinary clinicians. An alternate
``Authorization`` transport is rejected even when a valid session cookie is
present, and no client field can switch the gate or supply publication
authority.

Submission stages one SUBMITTED row in the framework's request transaction; the
framework alone commits or rolls back. No File/content write and no protected
key reservation happens here. Status/replay is actor-only, read-only and resolves
through the inspection module; it reports pending/unknown and never claims saved
without the full coherent publication evidence.

Importing this module never requires a site: Frappe is imported only inside the
entry calls.
"""

import hmac

from clinic_photos.publication import sites
from clinic_photos.publication.submission import (
    SYNTHETIC_MODE_CONFIG_KEY,
    SubmissionAuthorityDenied,
    SubmissionConflictUnverified,
    SubmissionTargetDenied,
)

GATE_CONFIG_KEY = "clinic_photos_publication_enabled"

CSRF_HEADER = "X-Frappe-CSRF-Token"
SESSION_COOKIE = "sid"
GUEST_SESSION = "Guest"

SAFE_DENIAL = "Not permitted to save a protected clinical photo"
SAFE_DISABLED = "Durable photo publication is not enabled on this site"
SAFE_SYNTHETIC_DENIED = (
    "Synthetic original publication is restricted to the approved disposable site "
    "and mode"
)
SAFE_VALIDATION = "Publication request is not a valid durable photo save"
SAFE_IMAGE_UNAVAILABLE = "Native image inspection is unavailable"
SAFE_STAGE_UNAVAILABLE = "Durable photo-save submission is unavailable"
SAFE_STATUS_DENIED = "Durable photo-save operation is unavailable"
SAFE_STATUS_UNAVAILABLE = "Durable photo-save operation status is unavailable"


def _frappe():
    import frappe

    return frappe


def request_method() -> str:
    frappe = _frappe()
    request = getattr(frappe.local, "request", None)
    method = getattr(request, "method", "") if request is not None else ""
    return method if isinstance(method, str) else ""


def require_post() -> None:
    if request_method().upper() != "POST":
        raise _frappe().PermissionError(SAFE_DENIAL)


def _request_cookies(frappe):
    request = getattr(frappe.local, "request", None)
    cookies = getattr(request, "cookies", None)
    return cookies if callable(getattr(cookies, "get", None)) else None


def _stored_csrf_token(frappe):
    session = getattr(frappe, "session", None)
    data = getattr(session, "data", None)
    if data is None:
        return None
    getter = getattr(data, "get", None)
    if callable(getter):
        return getter("csrf_token")
    return getattr(data, "csrf_token", None)


def require_native_session() -> None:
    """Require the native cookie session and its explicit CSRF token.

    Installed ``HTTPRequest`` resumes the session from the ``sid`` cookie (a
    request-body ``sid`` can override it) and validates CSRF unless an exemption
    applies. This guard is additional and independent: the cookie identity must
    be the resumed session, the stored token must be nonempty and the explicit
    header must match it. ``Authorization``-based OAuth/API-key identity is
    refused outright, so a second identity transport cannot borrow the cookie
    session. The native checks stay active; nothing is disabled or cleared here.
    """
    frappe = _frappe()

    authorization = frappe.get_request_header("Authorization")
    if not (authorization is None or authorization == ""):
        raise frappe.PermissionError(SAFE_DENIAL)

    cookies = _request_cookies(frappe)
    cookie_sid = cookies.get(SESSION_COOKIE) if cookies is not None else None
    session = getattr(frappe, "session", None)
    session_sid = getattr(session, "sid", None)
    if (
        not isinstance(cookie_sid, str)
        or not cookie_sid
        or cookie_sid == GUEST_SESSION
        or not isinstance(session_sid, str)
        or session_sid != cookie_sid
    ):
        raise frappe.PermissionError(SAFE_DENIAL)

    stored = _stored_csrf_token(frappe)
    if not isinstance(stored, str) or not stored or not stored.isascii():
        raise frappe.PermissionError(SAFE_DENIAL)

    header = frappe.get_request_header(CSRF_HEADER)
    if not isinstance(header, str) or not header or not header.isascii():
        raise frappe.PermissionError(SAFE_DENIAL)
    if not hmac.compare_digest(header, stored):
        raise frappe.PermissionError(SAFE_DENIAL)


def require_current_clinician():
    from clinic_photos.access import frappe_context
    from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES

    actor = frappe_context.current_actor()
    if (
        not actor.user
        or not actor.enabled
        or not actor.clinician
        or actor.user in NON_CLINICAL_SESSION_IDENTITIES
    ):
        raise _frappe().PermissionError(SAFE_DENIAL)
    return actor


def publication_enabled() -> bool:
    """Server-owned gate; a caller flag or payload field can never enable it."""
    frappe = _frappe()
    conf = getattr(frappe, "conf", None)
    if conf is None:
        return False
    return conf.get(GATE_CONFIG_KEY) is True


def _current_site() -> str:
    frappe = _frappe()
    site = getattr(frappe.local, "site", "")
    return site if isinstance(site, str) else ""


def _synthetic_mode_enabled() -> bool:
    frappe = _frappe()
    conf = getattr(frappe, "conf", None)
    if conf is None:
        return False
    return conf.get(SYNTHETIC_MODE_CONFIG_KEY) is True


def require_synthetic_mode() -> None:
    """Restrict synthetic originals to the exact disposable site and server mode.

    The lab site is allowed unconditionally; an additional site is allowed only
    when the server configuration names that exact site.
    """
    frappe = _frappe()
    if not sites.is_allowed(_current_site(), getattr(frappe, "conf", None)):
        raise frappe.PermissionError(SAFE_SYNTHETIC_DENIED)
    if not _synthetic_mode_enabled():
        raise frappe.PermissionError(SAFE_SYNTHETIC_DENIED)


def _outcome_response(outcome) -> dict:
    response = {
        "status": outcome.status,
        "operation_id": outcome.operation_id,
    }
    if outcome.version is not None:
        response["version"] = outcome.version
    if outcome.file_name is not None:
        response["file_name"] = outcome.file_name
    return response


def _submission_failure(frappe, error, message: str):
    if isinstance(error, (SubmissionAuthorityDenied, SubmissionTargetDenied)):
        raise frappe.PermissionError(SAFE_DENIAL) from None
    if isinstance(error, SubmissionConflictUnverified):
        from clinic_photos.publication.journal import SAFE_JOURNAL_CONFLICT

        raise frappe.ValidationError(SAFE_JOURNAL_CONFLICT) from None
    raise frappe.ValidationError(message) from None


def save(payload: dict | str) -> dict:
    """Authenticate, validate and stage one pending submission.

    Returns ``pending``/``conflict``/``unknown`` with the verified opaque operation
    identity. It never returns ``saved`` and never commits or rolls back.
    """
    frappe = _frappe()
    require_post()
    require_native_session()
    actor = require_current_clinician()
    if not publication_enabled():
        raise frappe.PermissionError(SAFE_DISABLED)

    from clinic_photos.publication import contracts, validation
    from clinic_photos.publication import submission as submission_boundary

    try:
        request = validation.validate_request(payload)
    except validation.PublicationValidationError:
        raise frappe.ValidationError(SAFE_VALIDATION) from None
    except validation.ImageInspectionUnavailable:
        raise frappe.ValidationError(SAFE_IMAGE_UNAVAILABLE) from None

    if request.kind == contracts.SYNTHETIC_ORIGINAL_CREATE:
        require_synthetic_mode()

    try:
        outcome = submission_boundary.stage_submission(request, actor.user)
        if outcome.status == "pending":
            submission_boundary.register_enqueue_after_commit(outcome.operation_id)
    except submission_boundary.SubmissionError as error:
        _submission_failure(frappe, error, SAFE_STAGE_UNAVAILABLE)

    return _outcome_response(outcome)


def status(operation_id: str) -> dict:
    """Actor-only read-only status/replay for one operation.

    The stored operation must belong to the current actor and the current target
    authority must still hold. Unknown, malformed and other-user identities share one
    opaque refusal and disclose no patient, payload, path or state. A verified own
    ``SUBMITTED`` operation reports ``pending``; any state whose known outcome is not
    provable here reports ``unknown`` and never ``saved``. This wrapper performs no
    write, no File/content readback of its own and no state transition.
    """
    frappe = _frappe()
    require_post()
    require_native_session()
    actor = require_current_clinician()
    if not publication_enabled():
        raise frappe.PermissionError(SAFE_DISABLED)

    from clinic_photos.publication import inspection

    try:
        outcome = inspection.resolve_operation(operation_id, actor=actor.user)
    except inspection.InspectionDenied:
        raise frappe.PermissionError(SAFE_STATUS_DENIED) from None
    except inspection.InspectionError:
        raise frappe.ValidationError(SAFE_STATUS_UNAVAILABLE) from None

    return _outcome_response(outcome)


def _register_site_contract() -> None:
    """Keep the installed POST-only whitelist contract on the public methods."""
    try:
        import frappe
    except ModuleNotFoundError:
        return

    global save, status
    save = frappe.whitelist(methods=["POST"])(save)
    status = frappe.whitelist(methods=["POST"])(status)


_register_site_contract()
