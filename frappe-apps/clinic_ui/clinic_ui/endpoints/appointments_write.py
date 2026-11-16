"""Local synthetic appointment booking, rescheduling and same-day check-in.

Order is fixed and test-asserted:

1. shared read gate (no-store, session, site configuration, clinic role),
2. explicit same-session CSRF token check (ignores framework bypasses),
3. separate default-off appointment-write capability for the approved site,
4. private deployment marker bound to the fence store,
5. strict body validation; every server-owned field is derived, never accepted,
6. the current user's own native reads (Patient, Department, Appointment Type,
   Company) and native Patient Appointment create/write authority **before** any
   fence or receipt access,
7. Healthcare settings and target-field side-effect checks,
8. durable target resolution: only the actor's own committed booking receipt is
   writable; the static read allowlist never is,
9. durable budget check from committed receipts (two bookings, one reschedule,
   one check-in), re-checked inside the fence,
10. the persistent site-wide fence, then the native row lock, fresh version,
    date, status and permission check, and the native save in one transaction,
11. one explicit site DB commit, then the terminal receipt in the fence,
12. success DTO construction; any post-commit failure latches the deployment.

A post-dispatch failure of any kind is uncertain: the key stays locked and the
response is ``409 {"code": "WRITE_LOCKED"}``. There is no automatic retry and no
inferred no-commit. A rejected target or exhausted budget returns the same
locked code without any native mutation.

Two documented cases follow from that rule. An authorization denial or unsafe
side effect raised **inside** a fence-held request (``frappe.PermissionError``
from ``gateway.deny()``, or ``UnsafeSideEffect``) is post-dispatch: the operation
terminates ``UNKNOWN`` and the site-wide fence blocks every new key for every
actor until an operator reconciles the ledger. A ``modified`` bump by the
nightly healthcare scheduler between the preflight read and the write instead
fails cleanly at the pre-fence ``modified`` check with
``409 {"code": "STALE_VERSION"}``; that check runs before the durable budget
check and before the fence is entered, so no budget key is consumed and no
latch is set. Only a bump that lands after the fence is held and the row is
locked converts to ``UNKNOWN`` and a manual-stop latch.

Cancellation, status changes other than the native same-day check-in, and
practitioner assignment are not implemented anywhere in this module.
"""

from clinic_ui.core import appointment_contract, appointment_write, dto, patient_write


class _FenceDateRefused(Exception):
    """A destination that went past after the fence was already held."""
from clinic_ui.core.policy import APPOINTMENT_WRITE_ROLE_CODES
from clinic_ui.endpoints import appointments, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"
STALE_CODE = "STALE_VERSION"


class RollbackFailed(RuntimeError):
    """The failed write could not be rolled back; the request must fail loudly."""


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class ManualInvestigationRequired(RuntimeError):
    """Both the site latch and the operation fallback could not be persisted."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _mark_unknown(claim, reason):
    try:
        claim.unknown(reason)
    except Exception:
        return


def _rollback_or_raise(gateway):
    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native appointment write rollback failed after a post-dispatch error"
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
    """Return whether the durable budget still admits one more operation.

    The count is read from committed fence receipts, so it survives restarts and
    cannot be reset in memory. An over-full enumeration is exhausted.
    """

    limit = appointment_write.budget(kind)
    try:
        records = gateway.list_committed_appointment_receipts(limit, kind=kind)
    except appointment_write.AppointmentBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    """Authoritative in-fence budget check; raises without native mutation.

    The request is already fenced, so this only differs from the advisory
    pre-check under a concurrent writer race. Raising here appends nothing and
    performs no native write, but the fenced key stays ``DISPATCHED``: the
    site-wide unresolved barrier then blocks further writes until an operator
    reconciles it. That is deliberately the stop condition, not a retry.
    """

    if not _budget_remaining(gateway, kind):
        raise appointment_write.AppointmentBudgetExhausted()


def _success(gateway, settings, status, target_alias, native_appointment_id, ack_token):
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
            appointment_id=target_alias,
            appointment_date=detail["appointment_date"],
            appointment_time=detail["appointment_time"],
            status=detail["status"],
            modified=detail["modified"],
        )
        payload = dto.appointment_write_result(
            status, payload_detail, ack_token
        )
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _digest_fields(payload):
    fields = dict(payload["fields"])
    if "workspace_appointment_id" in payload:
        fields["workspace_appointment_id"] = payload["workspace_appointment_id"]
    if "base_modified" in payload:
        fields["base_modified"] = payload["base_modified"]
    return fields


def _finish_fence(gateway, claim, kind, alias, native_appointment_id):
    """Commit the native transaction then append the terminal receipt."""

    gateway.commit()
    receipt = appointment_write.encode_receipt(
        kind=kind, alias=alias, native_appointment_id=native_appointment_id
    )
    claim.commit(receipt)
    return receipt


def _locked_response(gateway):
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def book(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_appointment_write(
        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
    )
    if gateway.native_appointment_boundary_problem() is not None:
        gateway.deny()

    try:
        payload = appointment_write.validate_book(body)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    native_patient_id = appointments.native_patient_for(
        settings, payload["workspace_patient_id"]
    )
    if native_patient_id is None:
        # Only the explicit synthetic patient allowlist may be booked; a created
        # patient alias is intentionally not accepted on this path.
        gateway.deny()

    # Cheap preflight refusals. Every one of these is repeated after the fence
    # against current rows; none of them may stand in for the post-fence check.
    today = gateway.site_today()
    if today is None:
        gateway.deny()
    if payload["fields"]["appointment_date"] < today:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    if gateway.native_patient_can_read(native_patient_id) is not True:
        gateway.deny()
    if gateway.native_appointment_can_create() is not True:
        gateway.deny()
    for doctype, doc in (
        ("Company", settings.appointment_company),
        ("Appointment Type", settings.appointment_type),
        ("Medical Department", settings.appointment_medical_department),
        ("Gender", None),
    ):
        if gateway.native_doctype_can_read(doctype, doc) is not True:
            gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    type_contract = gateway.native_appointment_type_contract(
        settings.appointment_type
    )
    if type_contract is None or type_contract["allow_booking_for"] != "Department":
        gateway.deny()

    try:
        alias = appointment_write.build_alias(
            settings.epoch_secret, gateway.site, actor, payload["operation_key"]
        )
        digest = appointment_write.operation_digest(
            settings.epoch_secret,
            kind="book",
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if alias in {
        workspace for workspace, _native in settings.appointment_allowlist
    }:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)

    try:
        book_budget = _budget_remaining(gateway, "book")
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if not book_budget:
        return _locked_response(gateway)

    native_appointment_id = None
    try:
        with gateway.appointment_write_fence(
            actor, "book", payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, "book")
                # The fence is now held. Nothing below may reuse a value read
                # before it: restart the native transaction, then redo the
                # gate, site configuration, executable boundary, site date,
                # permissions and dependency locks against current rows.
                gateway.reset_native_read_snapshot()
                fence_settings, fence_view, _fence_role = (
                    common.authorize_appointment_write(
                        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
                    )
                )
                if fence_view.user != actor:
                    raise patient_write.WriteUncertain()
                if gateway.native_appointment_boundary_problem() is not None:
                    raise patient_write.UnsafeSideEffect("boundary drift")
                settings = fence_settings
                native_patient_id = appointments.native_patient_for(
                    settings, payload["workspace_patient_id"]
                )
                if native_patient_id is None:
                    raise patient_write.UnsafeSideEffect(
                        "configured patient changed"
                    )

                today = gateway.site_today()
                if today is None:
                    raise patient_write.WriteUncertain()
                if payload["fields"]["appointment_date"] < today:
                    raise _FenceDateRefused()

                if gateway.native_patient_can_read(native_patient_id) is not True:
                    gateway.deny()
                if gateway.native_appointment_can_create() is not True:
                    gateway.deny()
                # Every linked target is a real document read for the current
                # user after the fence: the Medical Department (not a generic
                # Department), Appointment Type, Company and the patient-derived
                # Gender link. Configuration alone is not authority.
                for doctype, doc in (
                    ("Company", settings.appointment_company),
                    ("Appointment Type", settings.appointment_type),
                    ("Medical Department", settings.appointment_medical_department),
                    ("Gender", None),
                ):
                    if gateway.native_doctype_can_read(doctype, doc) is not True:
                        gateway.deny()
                if gateway.healthcare_appointment_side_effects_unsafe() is not False:
                    raise patient_write.UnsafeSideEffect(
                        "appointment settings are unsafe"
                    )

                type_contract = gateway.native_appointment_type_contract(
                    settings.appointment_type
                )
                if (
                    type_contract is None
                    or type_contract["allow_booking_for"] != "Department"
                ):
                    # The reviewed boundary requires a positive duration and
                    # department booking mode; anything else is unreviewed.
                    raise patient_write.UnsafeSideEffect(
                        "unreviewed appointment type mode"
                    )

                # Deterministic dependency locks after the fence and after the
                # transaction restart, in Patient then Appointment Type order.
                sources = gateway.native_appointment_sources(
                    native_patient_id, settings.appointment_type, lock=True
                )

                native_fields = {
                    "patient": native_patient_id,
                    "company": settings.appointment_company,
                    "department": settings.appointment_medical_department,
                    "appointment_type": settings.appointment_type,
                    "duration": sources["duration"],
                    "appointment_date": payload["fields"]["appointment_date"],
                    "appointment_time": payload["fields"]["appointment_time"],
                }
                # Freeze the exact expected values before any native insert can
                # mutate the prepared document.
                expected_snapshot = {**native_fields, **sources}
                prepared = gateway.prepare_native_appointment_create(
                    native_fields, expected=expected_snapshot
                )
                native_appointment_id = gateway.insert_native_appointment(
                    prepared, expected=expected_snapshot, today=today
                )
                try:
                    # Revalidate the inserted document before commit. This is a
                    # second line of defence after preflight, never a substitute
                    # for it: any unreviewed delta is uncertain and rolls back.
                    gateway.verify_native_appointment_insert(
                        native_appointment_id,
                        expected_snapshot,
                        sources,
                        today,
                    )
                except Exception:
                    _mark_unknown(
                        claim,
                        "inserted Patient Appointment failed closed-world revalidation",
                    )
                    raise patient_write.WriteUncertain() from None
                receipt = _finish_fence(
                    gateway, claim, "book", alias, native_appointment_id
                )
            except appointment_write.AppointmentBudgetExhausted:
                _rollback_or_raise(gateway)
                raise
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.StaleVersion,
                patient_write.WriteUncertain,
                patient_write.UnsafeSideEffect,
                _FenceDateRefused,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(
                    claim,
                    "native Patient Appointment create outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
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
    except _FenceDateRefused:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except patient_write.UnsafeSideEffect:
        gateway.deny()

    try:
        ack_token = appointment_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            "book",
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway, settings, "booked", alias, native_appointment_id, ack_token
        )
    except (SuccessUncertain, appointment_write.AppointmentWriteError):
        _latch_or_raise(
            gateway,
            actor,
            "book",
            payload["operation_key"],
            "post-commit booked response construction failed",
        )
        return _locked_response(gateway)


def reschedule(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_appointment_write(
        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
    )
    if gateway.native_appointment_boundary_problem() is not None:
        gateway.deny()

    try:
        payload = appointment_write.validate_reschedule(body)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    try:
        target = appointments.own_booking(
            gateway, settings, actor, payload["workspace_appointment_id"]
        )
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        appointment_write.AppointmentBudgetExhausted,
    ):
        return _locked_response(gateway)

    if target is None:
        return _locked_response(gateway)
    native_appointment_id = target.native_appointment_id

    today = gateway.site_today()
    if today is None:
        gateway.deny()
    if payload["fields"]["appointment_date"] < today:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if gateway.native_appointment_can_write(native_appointment_id) is not True:
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    reschedule_identity = appointments.appointment_config_identity(settings)
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=reschedule_identity
        )
        is not None
    ):
        gateway.deny()

    current_modified = gateway.native_appointment_modified(native_appointment_id)
    if current_modified is None:
        gateway.deny()
    if not patient_write.modified_matches(
        current_modified, payload["base_modified"]
    ):
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)

    try:
        digest = appointment_write.operation_digest(
            settings.epoch_secret,
            kind="reschedule",
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    try:
        reschedule_budget = _budget_remaining(gateway, "reschedule")
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if not reschedule_budget:
        return _locked_response(gateway)

    try:
        with gateway.appointment_write_fence(
            actor, "reschedule", payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, "reschedule")
                # Authoritative fresh lock after the pre-dispatch checks: the
                # adapter restarts the snapshot, locks the row, rechecks every
                # field/link permission, settings and side-effect field, then
                # applies date/time and verifies the controller-derived result.
                state = gateway.lock_appointment_target(
                    native_appointment_id,
                    base_modified=payload["base_modified"],
                    expected=reschedule_identity,
                )
                locked_today = gateway.site_today()
                if locked_today is None:
                    raise patient_write.WriteUncertain()
                if payload["fields"]["appointment_date"] < locked_today:
                    raise appointment_write.AppointmentTargetNotEligible()
                checkin_patient = appointments.checkin_patient_native_id(settings)
                if not appointment_contract.reschedule_target_eligible(
                    status=state.status,
                    appointment_date=state.appointment_date,
                    patient=state.patient,
                    today=locked_today,
                    dedicated_patient=checkin_patient,
                    has_receipt=False,
                ):
                    # The dedicated same-day record is the check-in target,
                    # never a reschedule target.
                    raise appointment_write.AppointmentTargetNotEligible()
                gateway.save_appointment_reschedule(
                    state, payload["fields"], locked_today
                )
                receipt = _finish_fence(
                    gateway,
                    claim,
                    "reschedule",
                    payload["workspace_appointment_id"],
                    native_appointment_id,
                )
            except appointment_write.AppointmentTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except appointment_write.AppointmentBudgetExhausted:
                _rollback_or_raise(gateway)
                raise
            except patient_write.StaleVersion:
                _mark_unknown(claim, "stale version at commit")
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.WriteUncertain,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(claim, "native Patient Appointment reschedule outcome not established")
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

    try:
        ack_token = appointment_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            "reschedule",
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway,
            settings,
            "rescheduled",
            payload["workspace_appointment_id"],
            native_appointment_id,
            ack_token,
        )
    except (SuccessUncertain, appointment_write.AppointmentWriteError):
        _latch_or_raise(
            gateway,
            actor,
            "reschedule",
            payload["operation_key"],
            "post-commit reschedule response construction failed",
        )
        return _locked_response(gateway)


def checkin(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_appointment_write(
        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
    )
    if gateway.native_appointment_boundary_problem() is not None:
        gateway.deny()

    try:
        payload = appointment_write.validate_checkin(body)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    try:
        target = appointments.own_booking(
            gateway, settings, actor, payload["workspace_appointment_id"]
        )
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        appointment_write.AppointmentBudgetExhausted,
    ):
        return _locked_response(gateway)

    if target is None:
        return _locked_response(gateway)
    native_appointment_id = target.native_appointment_id

    today = gateway.site_today()
    if today is None:
        gateway.deny()
    if appointments.checkin_patient_native_id(settings) is None:
        gateway.deny()

    if gateway.native_appointment_can_write(native_appointment_id) is not True:
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    checkin_identity = appointments.appointment_config_identity(
        settings, patient=appointments.checkin_patient_native_id(settings)
    )
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=checkin_identity
        )
        is not None
    ):
        gateway.deny()

    # Check-in independently enforces Patient read, not just appointment
    # authority; the configured patient identifier is never permission.
    detail = gateway.native_appointment_detail(native_appointment_id)
    if detail is None:
        gateway.deny()
    try:
        patient = detail["patient"]
    except (KeyError, TypeError):
        gateway.deny()
    checkin_patient = appointments.checkin_patient_native_id(settings)
    if checkin_patient is None or patient != checkin_patient:
        return _locked_response(gateway)
    if gateway.native_patient_can_read(patient) is not True:
        gateway.deny()

    # Pre-dispatch uniqueness: exactly one booked same-day record for the
    # dedicated synthetic patient. The locked identity is rechecked below.
    try:
        eligible = appointments.eligible_checkin_targets(gateway, settings, actor)
    except appointment_write.AppointmentWriteError:
        gateway.deny()
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        appointment_write.AppointmentBudgetExhausted,
    ):
        return _locked_response(gateway)
    if eligible != [native_appointment_id]:
        # Exactly one dedicated same-day target may be checked in; a duplicate
        # or a different record fails closed.
        return _locked_response(gateway)

    current_modified = gateway.native_appointment_modified(native_appointment_id)
    if current_modified is None:
        gateway.deny()
    if not patient_write.modified_matches(
        current_modified, payload["base_modified"]
    ):
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)

    try:
        digest = appointment_write.operation_digest(
            settings.epoch_secret,
            kind="checkin",
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    try:
        checkin_budget = _budget_remaining(gateway, "checkin")
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if not checkin_budget:
        return _locked_response(gateway)

    try:
        with gateway.appointment_write_fence(
            actor, "checkin", payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, "checkin")
                # Fresh authoritative lock before the native save: snapshot
                # restarted, row locked, every field/link permission, settings
                # guard and side-effect field rechecked by the adapter.
                state = gateway.lock_appointment_target(
                    native_appointment_id,
                    base_modified=payload["base_modified"],
                    expected=checkin_identity,
                )
                locked_today = gateway.site_today()
                if locked_today is None:
                    raise patient_write.WriteUncertain()
                if state.status not in ("Open", "Confirmed"):
                    raise appointment_write.AppointmentTargetNotEligible()
                if state.appointment_date != locked_today:
                    raise appointment_write.AppointmentTargetNotEligible()
                if not appointment_contract.checkin_target_eligible(
                    status=state.status,
                    appointment_date=state.appointment_date,
                    today=locked_today,
                    patient=state.patient,
                    dedicated_patient=checkin_patient,
                    has_receipt=False,
                    is_unique_eligible=True,
                ):
                    raise appointment_write.AppointmentTargetNotEligible()
                if gateway.native_patient_can_read(state.patient) is not True:
                    gateway.deny()
                # The real installed module-level check-in entry point runs
                # inside this transaction, after the lock and rechecks.
                gateway.native_appointment_checkin(state, locked_today)
                receipt = _finish_fence(
                    gateway,
                    claim,
                    "checkin",
                    payload["workspace_appointment_id"],
                    native_appointment_id,
                )
            except appointment_write.AppointmentTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except appointment_write.AppointmentBudgetExhausted:
                _rollback_or_raise(gateway)
                raise
            except patient_write.StaleVersion:
                _mark_unknown(claim, "stale version at check-in")
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.WriteUncertain,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(claim, "native Patient Appointment check-in outcome not established")
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

    try:
        ack_token = appointment_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            "checkin",
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway,
            settings,
            "checked_in",
            payload["workspace_appointment_id"],
            native_appointment_id,
            ack_token,
        )
    except (SuccessUncertain, appointment_write.AppointmentWriteError):
        _latch_or_raise(
            gateway,
            actor,
            "checkin",
            payload["operation_key"],
            "post-commit check-in response construction failed",
        )
        return _locked_response(gateway)


def ack(gateway, body, csrf_token=None):
    """Explicitly acknowledge confirmed success and release the barrier."""

    settings, view, _role = common.authorize_appointment_write(
        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = appointment_write.validate_ack(body)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    try:
        status = gateway.acknowledge_appointment_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)

    if status in ("acked", "already"):
        return gateway.respond_write(HTTP_OK, {"acknowledged": True})
    return _locked_response(gateway)
