"""Local synthetic Patient create/edit orchestration (converted lane).

This is the converted development lane: it relies on Frappe/Marley document
permissions, validation, controller lifecycle and native transactions instead
of the historical operation fence, deployment marker, write budget and
acknowledgement barrier. Unresolved entries in that historical ledger are never
read, changed or used to block a converted write.

Order is fixed and test-asserted:

1. shared read gate (no-store, session, site configuration, clinic role),
2. explicit same-session CSRF token check (ignores framework bypasses),
3. default-off native-write capability for the exact approved site,
4. strict body validation,
5. native Patient create/write authority,
6. live Gender check and healthcare side-effect/target checks,
7. create-only: construct the Patient, apply controller defaults, and reject
   any customer/contact/address/email/phone/user/invite trigger before insert,
8. native ``Patient`` insert/save with no ``ignore_permissions``,
9. one explicit site DB commit,
10. the canonical ``{"status", "patient"}`` result, read back under the
    current user's native read authority.

A post-dispatch failure of any kind is uncertain: the endpoint rolls back the
partial transaction (a rollback failure raises ``RollbackFailed`` so Frappe
cleanup handles it) and returns ``409 {"code": "WRITE_UNCERTAIN"}``. Nothing
after a native save or insert is ever reported as no-change, because a rollback
cannot establish that a controller hook caused no external side effect. The one
definite pre-dispatch refusal recognised is the framework's own permission type
during create preparation, which returns 403. The endpoint never retries
automatically and never claims success it has not read back.
"""

from clinic_ui.core import dto, patient_write
from clinic_ui.core.policy import PATIENT_WRITE_ROLE_CODES
from clinic_ui.endpoints import aliases, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
STALE_CODE = "STALE_VERSION"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


class RollbackFailed(RuntimeError):
    """The failed native write could not be rolled back; fail loudly.

    Returning a normal response would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead.
    """


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape."""

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native write rollback failed after a post-dispatch error"
        ) from exc


def _permission_denial(gateway, exc):
    """True only for a definite refusal raised before any native mutation.

    Only the create-preparation step uses this: it constructs the document and
    applies controller defaults without touching the database. The runtime
    adapter owns the framework-identity check
    (``Gateway.is_native_permission_denial``), so a class that merely shares
    the name is not accepted. When the adapter cannot answer, the caller does
    not claim a denial.
    """

    try:
        check = getattr(gateway, "is_native_permission_denial", None)
        if not callable(check):
            return False
        return check(exc) is True
    except Exception:
        return False


def _success(gateway, status, workspace_patient_id, native_patient_id):
    """Read the committed record back and build the canonical result."""

    try:
        detail = gateway.native_patient_view(native_patient_id)
        if detail is None:
            raise ValueError("native detail is missing after commit")
        payload = dto.patient_write_result(status, workspace_patient_id, detail)
    except Exception:
        # The native write already committed; its outcome is not established.
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(HTTP_OK, payload)


def create(gateway, body, csrf_token=None):
    _settings, _view, _role = common.authorize_patient_write(
        gateway, PATIENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = patient_write.validate_create(body)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if not gateway.native_patient_can_create():
        gateway.deny()
    if not gateway.native_gender_exists(payload["fields"]["sex"]):
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    if gateway.healthcare_settings_side_effects_unsafe():
        gateway.deny()

    try:
        prepared = gateway.prepare_native_patient_create(payload["fields"])
    except patient_write.UnsafeSideEffect:
        gateway.deny()
    except Exception as exc:
        # Preparation performs no native mutation, so the framework's own
        # permission refusal is a definite 403. Any other failure keeps its
        # existing pre-mutation failure rather than being reported as a write.
        if _permission_denial(gateway, exc):
            gateway.deny()
        raise

    try:
        native_patient_id = gateway.insert_native_patient(prepared)
    except patient_write.UnsafeSideEffect:
        _rollback_or_raise(gateway)
        gateway.deny()
    except Exception:
        # The insert may have reached the controller; never claim no change.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    try:
        gateway.commit()
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # A newly created synthetic patient is addressed by its native docname.
    return _success(gateway, "created", native_patient_id, native_patient_id)


def update(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_patient_write(
        gateway, PATIENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = patient_write.validate_update(body)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if not gateway.native_patient_can_write():
        gateway.deny()

    native_patient_id = aliases.resolve(
        gateway, settings, view.user, payload["workspace_patient_id"]
    )

    if not gateway.native_patient_can_write(native_patient_id):
        gateway.deny()
    if "sex" in payload["fields"] and not gateway.native_gender_exists(
        payload["fields"]["sex"]
    ):
        # Same pre-mutation refusal as create: never a post-dispatch 409.
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    if gateway.healthcare_settings_side_effects_unsafe():
        gateway.deny()
    if gateway.native_patient_target_unsafe(native_patient_id):
        gateway.deny()

    current_modified = gateway.native_patient_modified(native_patient_id)
    if current_modified is None:
        gateway.deny()
    if not patient_write.modified_matches(
        current_modified, payload["base_modified"]
    ):
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)

    try:
        gateway.native_patient_update(
            native_patient_id,
            payload["fields"],
            payload["base_modified"],
        )
    except patient_write.StaleVersion:
        # Raised under the row lock before any field is applied.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except Exception:
        # The update may already have reached ``doc.save`` and its hooks. A
        # rollback discards the site transaction but cannot establish that no
        # external side effect happened, so every failure here stays uncertain.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    try:
        gateway.commit()
    except Exception:
        # The native write was dispatched; the commit outcome is unknown.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _success(
        gateway, "updated", payload["workspace_patient_id"], native_patient_id
    )
