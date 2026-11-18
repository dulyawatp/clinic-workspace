"""Designed appointment cancel and manual no-show (default off).

Order is fixed and test-asserted:

1. shared read gate plus the explicit same-session CSRF and appointment-write
   deployment gates (the same gate every appointment write uses),
2. pinned boundary check; any drift denies before validation,
3. strict body validation (patient, server-derived alias, operation key,
   reason); server-owned fields are refused,
4. the actor's own committed booking resolved from the durable fence receipt;
   the static read allowlist and another actor's booking are never writable,
5. pre-fence target decisions: the appointment status must be a designed
   source status, the patient must be the requested allowlisted patient, and a
   target with an unreviewed linked-record branch (invoice, insurance, Service
   Request, mode of payment) is refused here,
6. durable budget check from committed receipts (two cancels, one no-show),
7. the persistent site-wide fence, then the native row lock and the same
   pre-fence decisions again against current rows,
8. the designed transition (pinned ``cancel_appointment`` plus a terminal
   status save, or the native ``No Show`` save), a closed-world re-read of the
   returned state, the receipt encoded by the ``appointment_write`` receipt
   codec, one explicit commit and the terminal append,
9. the one-shot acknowledgement token; the ack endpoint is unchanged.

Every designed refusal in 3-6 happens **before** the fence claim. The claim
itself is appended when the fence is taken, so every refusal after that point
blocks the site and none is retried or cleared here. The two designed in-fence
refusals (a target that went terminal under the lock, a budget that filled
before the re-read there) raise without marking anything: the key stays
``DISPATCHED`` and no site-wide latch is written. A failure that marks an
unestablished native outcome leaves the key ``UNKNOWN``; a process interrupt
leaves it ``DISPATCHED``. There is no ``ABORTED`` terminal and no auto-clear,
and neither ``DISPATCHED`` nor ``UNKNOWN`` can be acknowledged: no call in this
endpoint resolves those keys.

``COMMITTED`` is a different state. Every claim also appends a reserved barrier
key, and the terminal append commits only the operation key, so a completed
cancel still blocks new operations. The unchanged ``appointments_write.ack``
acknowledgement of that receipt commits the barrier; the site unblocks when the
client confirms the receipt it was given. The response for a refused operation
is ``409 {"code": "WRITE_LOCKED"}`` with no retry. The endpoint rolls back the
partial transaction on a post-dispatch failure; a rollback failure raises
``RollbackFailed`` to Frappe cleanup instead of returning a normal error, since
Frappe's implicit end-of-request commit for an unsafe method could otherwise
persist a partial native write. Status is never written to match a client
badge; the client never sends a status.
"""

from clinic_ui.core import (
    appointment_contract,
    appointment_write,
    dto,
    patient_write,
)
from clinic_ui.core.policy import (
    APPOINTMENT_CANCEL_ROLE_CODES,
    APPOINTMENT_NOSHOW_ROLE_CODES,
)
from clinic_ui.endpoints import appointments, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"

# Success DTO statuses for the two transitions.
CANCEL_RESULT = "cancelled"
NO_SHOW_RESULT = "no_show"


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class RollbackFailed(RuntimeError):
    """The failed write could not be rolled back; the request must fail loudly.

    Returning a normal 409 would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead; the fence key stays ``UNKNOWN``/``DISPATCHED``.
    """


class ManualInvestigationRequired(RuntimeError):
    """Neither the site latch nor the operation fallback could be persisted."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _locked_response(gateway):
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def _mark_unknown(claim, reason):
    try:
        claim.unknown(reason)
    except Exception:
        return


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape.

    Do not swallow this into ``WriteUncertain``: a normal 409 could let Frappe's
    implicit request-end commit persist the partial native write. The dispatched
    key is left ``UNKNOWN`` or ``DISPATCHED``, so it still blocks the site when
    the rollback itself fails.
    """

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native appointment transition rollback failed"
        ) from exc


def _latch_or_raise(gateway, actor, kind, operation_key, reason):
    try:
        gateway.latch_manual_investigation(reason)
        return
    except Exception:
        try:
            gateway.latch_operation_uncertain(actor, kind, operation_key, reason)
            return
        except Exception as exc:
            raise ManualInvestigationRequired(reason) from exc


def _budget_remaining(gateway, kind):
    """Durable budget from committed receipts; an over-full page is exhausted."""

    limit = appointment_write.budget(kind)
    try:
        records = gateway.list_committed_appointment_receipts(limit, kind=kind)
    except appointment_write.AppointmentBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise appointment_write.AppointmentBudgetExhausted()


def _success(gateway, settings, result_status, alias, native_appointment_id, ack_token):
    try:
        detail = gateway.native_appointment_detail(native_appointment_id)
        if detail is None:
            raise ValueError("native detail is missing after commit")
        workspace_patient_id = appointments.workspace_patient_for(
            settings, detail["patient"]
        )
        if workspace_patient_id is None:
            raise ValueError("native patient is outside the allowlist")
        payload_detail = dto.appointment_detail(
            workspace_patient_id=workspace_patient_id,
            appointment_id=alias,
            appointment_date=detail["appointment_date"],
            appointment_time=detail["appointment_time"],
            status=detail["status"],
            modified=detail["modified"],
        )
        payload = dto.appointment_write_result(result_status, payload_detail, ack_token)
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _digest_fields(payload):
    return {
        "workspace_patient_id": payload["workspace_patient_id"],
        "workspace_appointment_id": payload["workspace_appointment_id"],
        "reason": payload["reason"],
    }


def _finish_fence(gateway, claim, kind, alias, native_appointment_id):
    """Encode the receipt, commit the native write, then append the terminal.

    The receipt is encoded and validated by the ``appointment_write`` receipt
    codec before the one site commit, so a rejected alias raises while the
    native transaction is still uncommitted; the caller's existing
    dispatch-failure path then discards that transaction with
    ``gateway.rollback()`` and the key stays locked. Encoding after the commit
    would instead land a native write whose only durable record is ``UNKNOWN``.

    The append is a separate durable step from the site commit, so the two
    stores are never one transaction:

    * the append never applies: the native write may be durable and the key
      stays ``DISPATCHED`` — or ``UNKNOWN`` when the caller marked an
      unestablished outcome before the append;
    * the append applies but the call fails, or the process dies before the
      response leaves: the key is ``COMMITTED`` with its receipt and the success
      response is lost.

    A successful append does not by itself unblock the site. Every claim also
    appends a reserved barrier key, and only the unchanged
    ``appointments_write.ack`` acknowledgement commits that barrier, so a
    completed cancel blocks new operations until the client acknowledges its
    receipt. ``DISPATCHED`` and ``UNKNOWN`` keys accept no acknowledgement, and
    the restore/quarantine protocol is not implemented: those states have no
    path out of this endpoint.
    """

    receipt = appointment_write.encode_receipt(
        kind=kind, alias=alias, native_appointment_id=native_appointment_id
    )
    gateway.commit()
    claim.commit(receipt)
    return receipt


def _resolve_target(gateway, settings, actor, payload):
    """The actor's own committed booking, or ``None`` (nothing was written)."""

    return appointments.own_booking(
        gateway, settings, actor, payload["workspace_appointment_id"]
    )


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
    """Shared cancel/no-show request: gate, pre-fence refusals, fence, ack."""

    settings, view, _role = common.authorize_appointment_write(
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
    native_patient_id = appointments.native_patient_for(
        settings, payload["workspace_patient_id"]
    )
    if native_patient_id is None:
        gateway.deny()

    try:
        target = _resolve_target(gateway, settings, actor, payload)
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        appointment_write.AppointmentBudgetExhausted,
        appointment_write.AppointmentWriteError,
    ):
        return _locked_response(gateway)
    if target is None:
        # The static allowlist and another actor's booking are never writable.
        return _locked_response(gateway)
    native_appointment_id = target.native_appointment_id
    alias = payload["workspace_appointment_id"]

    detail = gateway.native_appointment_action_detail(native_appointment_id)
    if detail is None:
        gateway.deny()
    if detail.get("patient") != native_patient_id:
        return _locked_response(gateway)
    if gateway.native_patient_can_read(native_patient_id) is not True:
        gateway.deny()
    if gateway.native_appointment_can_write(native_appointment_id) is not True:
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    identity = appointments.appointment_config_identity(settings)
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=identity
        )
        is not None
    ):
        gateway.deny()

    # Pre-fence designed refusals: a terminal target or an unreviewed linked
    # record branch never claims the fence, so nothing is latched.
    if target_problem(detail) is not None:
        return _locked_response(gateway)

    try:
        digest = appointment_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    try:
        with gateway.appointment_write_fence(
            actor, kind, payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, kind)
                # The fence is now held. Restart the snapshot, then redo the
                # gate, boundary, configuration and target decisions against
                # current rows under the native row lock.
                gateway.reset_native_read_snapshot()
                fence_settings, fence_view, _fence_role = (
                    common.authorize_appointment_write(
                        gateway, role_codes, csrf_token
                    )
                )
                if fence_view.user != actor:
                    raise patient_write.WriteUncertain()
                if gateway.native_appointment_boundary_problem() is not None:
                    raise patient_write.UnsafeSideEffect("boundary drift")
                settings = fence_settings
                fence_patient_id = appointments.native_patient_for(
                    settings, payload["workspace_patient_id"]
                )
                if fence_patient_id != native_patient_id:
                    raise patient_write.UnsafeSideEffect(
                        "configured patient changed"
                    )
                identity = appointments.appointment_config_identity(settings)
                state = gateway.lock_appointment_target(
                    native_appointment_id, base_modified=None, expected=identity
                )
                if state.patient != native_patient_id:
                    raise patient_write.UnsafeSideEffect("target patient changed")
                if target_problem(state) is not None:
                    # The claim is already DISPATCHED here. This is an
                    # eligibility refusal rather than an unestablished native
                    # outcome, so nothing is marked and no latch is written:
                    # the key stays DISPATCHED and blocks the site, and no call
                    # in this endpoint resolves it.
                    raise appointment_write.AppointmentTargetNotEligible()
                if gateway.native_patient_can_read(state.patient) is not True:
                    gateway.deny()
                getattr(gateway, method_name)(state, payload["reason"], actor)
                receipt = _finish_fence(
                    gateway, claim, kind, alias, native_appointment_id
                )
            except appointment_write.AppointmentTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except appointment_write.AppointmentBudgetExhausted:
                _rollback_or_raise(gateway)
                raise
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.WriteUncertain,
                patient_write.UnsafeSideEffect,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(
                    claim,
                    f"native Patient Appointment {kind} outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except appointment_write.AppointmentTargetNotEligible:
        return _locked_response(gateway)
    except appointment_write.AppointmentBudgetExhausted:
        return _locked_response(gateway)
    except patient_write.WriteLocked:
        return _locked_response(gateway)
    except patient_write.WriteConflict:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StoreUnavailable:
        return _locked_response(gateway)
    except patient_write.WriteUncertain:
        return _locked_response(gateway)
    except patient_write.UnsafeSideEffect:
        gateway.deny()

    try:
        ack_token = appointment_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway, settings, result_status, alias, native_appointment_id, ack_token
        )
    except (SuccessUncertain, appointment_write.AppointmentWriteError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            f"post-commit {kind} response construction failed",
        )
        return _locked_response(gateway)


def cancel_appointment(gateway, body, csrf_token=None):
    """Cancel one of the actor's own booked appointments (roles DOC, REC)."""

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
    """Record a manual no-show on a booked appointment (roles DOC, REC)."""

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
