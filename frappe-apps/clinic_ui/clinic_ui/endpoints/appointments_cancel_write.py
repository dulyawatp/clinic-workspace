"""Local synthetic appointment cancellation and manual no-show.

This is the converted development lane: it relies on Frappe/Marley document
permissions, validation, controller lifecycle and native transactions instead of
the historical operation fence, deployment marker, write budget and
acknowledgement barrier. Unresolved entries in that historical ledger are never
read, changed or used to block a converted transition; a historical sealed alias
still resolves read-only through its committed booking receipt so existing
links keep working.

Order is fixed and test-asserted:

1. shared read gate, explicit same-session CSRF token and the default-off
   appointment-write capability for the exact approved site,
2. strict body validation (target reference plus a bounded reason),
3. patient and target resolution: the static allowlist, a read-only historical
   receipt, or a native docname under the current user's own native permission,
4. the current user's native Patient read and Patient Appointment write
   authority, the closed-world target contract and Healthcare settings checks,
5. the native row lock, the transition contract recheck and the native
   controller operation,
6. one explicit site DB commit,
7. the canonical ``{"status", "appointment"}`` result, read back under the
   current user's native read authority.

A post-dispatch failure of any kind is uncertain: the endpoint rolls back the
partial transaction (a rollback failure raises ``RollbackFailed``) and returns
``409 {"code": "WRITE_UNCERTAIN"}``. Nothing is retried automatically. A
designed refusal happens before any native mutation and is reported as ``422``
or ``409 OPERATION_CONFLICT``.
"""

from clinic_ui.core import appointment_contract, appointment_write, dto, patient_write
from clinic_ui.core.policy import (
    APPOINTMENT_CANCEL_ROLE_CODES,
    APPOINTMENT_NOSHOW_ROLE_CODES,
)
from clinic_ui.endpoints import appointments, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
CONFLICT_CODE = "OPERATION_CONFLICT"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"

# Success DTO statuses for the two transitions.
CANCEL_RESULT = "cancelled"
NO_SHOW_RESULT = "no_show"


class _PatientReadDenied(Exception):
    """The locked target's Patient is not readable: a definite denial."""


class RollbackFailed(RuntimeError):
    """The failed native write could not be rolled back; the request must fail loudly.

    Returning a normal 409 would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead.
    """


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape."""

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native appointment transition rollback failed"
        ) from exc


def _success(
    gateway, settings, result_status, workspace_appointment_id, native_appointment_id
):
    """Read the committed record back and build the canonical result."""

    try:
        detail = gateway.native_appointment_detail(native_appointment_id)
        if detail is None:
            raise ValueError("native detail is missing after commit")
        workspace_patient_id = appointments.patient_reference(
            gateway, settings, detail["patient"]
        )
        if workspace_patient_id is None:
            raise ValueError("native patient is not readable")
        payload_detail = dto.appointment_detail(
            workspace_patient_id=workspace_patient_id,
            appointment_id=workspace_appointment_id,
            appointment_date=detail["appointment_date"],
            appointment_time=detail["appointment_time"],
            status=detail["status"],
            modified=detail["modified"],
        )
        payload = dto.appointment_write_result(result_status, payload_detail)
    except Exception:
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(HTTP_OK, payload)


def _transition(
    gateway,
    body,
    csrf_token,
    *,
    kind,
    role_codes,
    target_problem,
    result_status,
    method_name,
):
    """Shared cancel/no-show request: gate, resolution, lock and native call."""

    settings, view, role = common.authorize_appointment_write(
        gateway, role_codes, csrf_token
    )
    if gateway.native_appointment_boundary_problem() is not None:
        gateway.deny()

    try:
        payload = (
            appointment_write.validate_cancel(body)
            if kind == appointment_contract.CANCEL_KIND
            else appointment_write.validate_no_show(body)
        )
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    native_patient_id = appointments.resolve_patient(
        gateway, settings, actor, role, payload["workspace_patient_id"]
    )
    if native_patient_id is None:
        gateway.deny()

    target = appointments.resolve_write(
        gateway, settings, actor, payload["workspace_appointment_id"]
    )
    if target is None:
        # The static allowlist, another actor's booking and every unknown value
        # are never writable.
        gateway.deny()
    native_appointment_id = target.native_appointment_id
    alias = target.workspace_appointment_id

    detail = gateway.native_appointment_action_detail(native_appointment_id)
    if detail is None:
        gateway.deny()
    if detail.get("patient") != native_patient_id:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    if gateway.native_patient_can_read(native_patient_id) is not True:
        gateway.deny()
    if gateway.native_appointment_can_write(native_appointment_id) is not True:
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    # The resolved target's own patient is the expected identity, so a patient
    # created through the converted lane is not refused by a static allowlist.
    identity = appointments.appointment_config_identity(
        settings, patient=native_patient_id
    )
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=identity
        )
        is not None
    ):
        gateway.deny()

    # A terminal target or an unreviewed linked record is a designed refusal:
    # nothing has been mutated at this point.
    if target_problem(detail) is not None:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)

    # Phase one: the row lock and its native permission checks, before any
    # mutation. A verified native permission denial here is a definite 403.
    try:
        state = gateway.lock_appointment_target(
            native_appointment_id, base_modified=None, expected=identity
        )
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, "STALE_VERSION")
    except patient_write.UnsafeSideEffect:
        _rollback_or_raise(gateway)
        gateway.deny()
    except Exception as exc:
        _rollback_or_raise(gateway)
        if gateway.is_native_permission_denial(exc):
            gateway.deny()
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase two: post-lock eligibility and authority, before any mutation.
    try:
        if state.patient != native_patient_id:
            raise patient_write.UnsafeSideEffect("target patient changed")
        if target_problem(state) is not None:
            raise appointment_write.AppointmentTargetNotEligible()
        if gateway.native_patient_can_read(state.patient) is not True:
            # A definite denial under the row lock is not an uncertain outcome.
            raise _PatientReadDenied()
    except _PatientReadDenied:
        _rollback_or_raise(gateway)
        gateway.deny()
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.UnsafeSideEffect:
        _rollback_or_raise(gateway)
        gateway.deny()
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase three: the native transition and the one commit. From here a
    # mutation may already be applied, so every unexpected failure is uncertain
    # regardless of its exception type.
    try:
        getattr(gateway, method_name)(state, payload["reason"], actor)
        gateway.commit()
    except appointment_write.AppointmentTargetNotEligible:
        # The adapter raises this before it changes the document.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, "STALE_VERSION")
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _success(
        gateway, settings, result_status, alias, native_appointment_id
    )


def cancel_appointment(gateway, body, csrf_token=None):
    """Cancel one writable appointment (roles DOC, REC)."""

    return _transition(
        gateway,
        body,
        csrf_token,
        kind=appointment_contract.CANCEL_KIND,
        role_codes=APPOINTMENT_CANCEL_ROLE_CODES,
        target_problem=appointment_contract.cancel_target_problem,
        result_status=CANCEL_RESULT,
        method_name="cancel_native_appointment",
    )


def record_no_show(gateway, body, csrf_token=None):
    """Record a manual no-show on a writable appointment (roles DOC, REC)."""

    return _transition(
        gateway,
        body,
        csrf_token,
        kind=appointment_contract.NO_SHOW_KIND,
        role_codes=APPOINTMENT_NOSHOW_ROLE_CODES,
        target_problem=appointment_contract.no_show_target_problem,
        result_status=NO_SHOW_RESULT,
        method_name="no_show_native_appointment",
    )
