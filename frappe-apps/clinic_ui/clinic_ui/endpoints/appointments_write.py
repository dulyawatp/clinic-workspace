"""Local synthetic appointment booking, rescheduling and same-day check-in.

This is the converted development lane: it relies on Frappe/Marley document
permissions, validation, controller lifecycle and native transactions instead of
the historical operation fence, deployment marker, write budget and
acknowledgement barrier. Unresolved entries in that historical ledger are never
read, changed or used to block a converted write; a historical sealed alias
still resolves read-only through its committed booking receipt so existing
links keep working.

Order is fixed and test-asserted:

1. shared read gate (no-store, session, site configuration, clinic role),
2. explicit same-session CSRF token check (ignores framework bypasses),
3. default-off appointment-write capability for the exact approved site,
4. strict body validation; every server-owned field is derived, never accepted,
5. patient and target resolution: the static allowlist, a read-only historical
   receipt, or a native docname under the current user's own native permission,
6. the current user's own native Patient read and native Patient Appointment
   create/write authority, linked-document reads, Healthcare settings and
   appointment-type mode checks,
7. the native row lock for the target lanes, the native save or insert, and the
   post-insert closed-world revalidation,
8. one explicit site DB commit,
9. the canonical ``{"status", "appointment"}`` result, read back under the
   current user's native read authority.

A post-dispatch failure of any kind is uncertain: the endpoint rolls back the
partial transaction (a rollback failure raises ``RollbackFailed`` so Frappe
cleanup handles it) and returns ``409 {"code": "WRITE_UNCERTAIN"}``. It never
retries automatically and never claims success it has not read back.

A designed refusal — a strict-body error, a destination in the past, a stale
``base_modified`` or a terminal/ambiguous target — happens before any native
mutation and is reported as ``422``, ``409 STALE_VERSION`` or
``409 OPERATION_CONFLICT``. Cancellation and manual no-show live in
:mod:`clinic_ui.endpoints.appointments_cancel_write`; generic status setters and
practitioner assignment are not implemented anywhere.
"""

from clinic_ui.core import appointment_contract, appointment_write, dto, patient_write
from clinic_ui.core.policy import APPOINTMENT_WRITE_ROLE_CODES
from clinic_ui.endpoints import appointments, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
CONFLICT_CODE = "OPERATION_CONFLICT"
STALE_CODE = "STALE_VERSION"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"


class RollbackFailed(RuntimeError):
    """The failed native write could not be rolled back; fail loudly.

    Returning a normal response would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead.
    """


class _PatientReadDenied(Exception):
    """The locked target's Patient is not readable: a definite denial."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape."""

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native appointment write rollback failed after a post-dispatch error"
        ) from exc


def _success(
    gateway, settings, status, workspace_appointment_id, native_appointment_id
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
        payload = dto.appointment_write_result(status, payload_detail)
    except Exception:
        # The native write already committed; its outcome is not established.
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(HTTP_OK, payload)


def _linked_reads_ok(gateway, settings):
    for doctype, doc in (
        ("Company", settings.appointment_company),
        ("Appointment Type", settings.appointment_type),
        ("Medical Department", settings.appointment_medical_department),
        ("Gender", None),
    ):
        if gateway.native_doctype_can_read(doctype, doc) is not True:
            return False
    type_contract = gateway.native_appointment_type_contract(
        settings.appointment_type
    )
    return (
        type_contract is not None
        and type_contract["allow_booking_for"] == "Department"
    )


def book(gateway, body, csrf_token=None):
    settings, view, role = common.authorize_appointment_write(
        gateway, APPOINTMENT_WRITE_ROLE_CODES, csrf_token
    )
    if gateway.native_appointment_boundary_problem() is not None:
        gateway.deny()

    try:
        payload = appointment_write.validate_book(body)
    except appointment_write.AppointmentWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    workspace_patient_id = payload["workspace_patient_id"]
    native_patient_id = appointments.resolve_patient(
        gateway, settings, actor, role, workspace_patient_id
    )
    if native_patient_id is None:
        gateway.deny()

    today = gateway.site_today()
    if today is None:
        gateway.deny()
    if payload["fields"]["appointment_date"] < today:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if gateway.native_patient_can_read(native_patient_id) is not True:
        gateway.deny()
    if gateway.native_appointment_can_create() is not True:
        gateway.deny()
    if not _linked_reads_ok(gateway, settings):
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()

    # No native mutation has happened yet, so a refused dependency lock is a
    # plain denial rather than an uncertain outcome.
    try:
        sources = gateway.native_appointment_sources(
            native_patient_id, settings.appointment_type, lock=True
        )
    except patient_write.UnsafeSideEffect:
        gateway.deny()
    except Exception:
        gateway.deny()

    native_fields = {
        "patient": native_patient_id,
        "company": settings.appointment_company,
        "department": settings.appointment_medical_department,
        "appointment_type": settings.appointment_type,
        "duration": sources["duration"],
        "appointment_date": payload["fields"]["appointment_date"],
        "appointment_time": payload["fields"]["appointment_time"],
    }
    # Freeze the exact expected values before any native insert can mutate the
    # prepared document.
    expected_snapshot = {**native_fields, **sources}

    # The document is vetted before any insert, so an unsafe prepared document
    # is a definite refusal with nothing written.
    try:
        prepared = gateway.prepare_native_appointment_create(
            native_fields, expected=expected_snapshot
        )
    except patient_write.UnsafeSideEffect:
        gateway.deny()
    except Exception:
        gateway.deny()

    try:
        native_appointment_id = gateway.insert_native_appointment(
            prepared, expected=expected_snapshot, today=today
        )
        # Revalidate the inserted document before commit. Any unreviewed delta
        # rolls back and reports an uncertain outcome.
        gateway.verify_native_appointment_insert(
            native_appointment_id, expected_snapshot, sources, today
        )
        gateway.commit()
    except Exception:
        # From the insert onward the mutation may already be applied; the
        # outcome is uncertain whatever the exception type is.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # A newly booked synthetic appointment is addressed by its native docname.
    return _success(
        gateway, settings, "booked", native_appointment_id, native_appointment_id
    )


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
    target = appointments.resolve_write(
        gateway, settings, actor, payload["workspace_appointment_id"]
    )
    if target is None:
        gateway.deny()
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

    detail = gateway.native_appointment_detail(native_appointment_id)
    if detail is None:
        gateway.deny()
    try:
        patient = detail["patient"]
    except (KeyError, TypeError):
        gateway.deny()
    if gateway.native_patient_can_read(patient) is not True:
        gateway.deny()

    identity = appointments.appointment_config_identity(settings, patient=patient)
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=identity
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

    # Phase one: the lock and its native permission checks. Nothing has been
    # mutated yet, so a verified native permission denial is a definite 403.
    try:
        state = gateway.lock_appointment_target(
            native_appointment_id,
            base_modified=payload["base_modified"],
            expected=identity,
        )
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except patient_write.UnsafeSideEffect:
        _rollback_or_raise(gateway)
        gateway.deny()
    except Exception as exc:
        _rollback_or_raise(gateway)
        if gateway.is_native_permission_denial(exc):
            gateway.deny()
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase two: post-lock eligibility, still before any mutation.
    try:
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
            # The dedicated same-day record is the check-in target, never a
            # reschedule target.
            raise appointment_write.AppointmentTargetNotEligible()
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase three: the native save and the one commit. From here a mutation may
    # already be applied, so every unexpected failure is uncertain regardless of
    # its exception type.
    try:
        gateway.save_appointment_reschedule(
            state, payload["fields"], locked_today
        )
        gateway.commit()
    except appointment_write.AppointmentTargetNotEligible:
        # The adapter raises this before it applies the change.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _success(
        gateway,
        settings,
        "rescheduled",
        target.workspace_appointment_id,
        native_appointment_id,
    )


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
    target = appointments.resolve_write(
        gateway, settings, actor, payload["workspace_appointment_id"]
    )
    if target is None:
        gateway.deny()
    native_appointment_id = target.native_appointment_id

    today = gateway.site_today()
    if today is None:
        gateway.deny()
    checkin_patient = appointments.checkin_patient_native_id(settings)
    if checkin_patient is None:
        gateway.deny()

    if gateway.native_appointment_can_write(native_appointment_id) is not True:
        gateway.deny()
    if gateway.healthcare_appointment_side_effects_unsafe() is not False:
        gateway.deny()
    identity = appointments.appointment_config_identity(
        settings, patient=checkin_patient
    )
    if (
        gateway.native_appointment_target_problem(
            native_appointment_id, expected=identity
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
    if patient != checkin_patient:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    if gateway.native_patient_can_read(patient) is not True:
        gateway.deny()

    # Target-scoped: the configured dedicated synthetic patient, the site's
    # current date and an Open/Confirmed status. No cross-record uniqueness scan
    # is made or claimed; the native status lifecycle refuses a repeat of this
    # record, and the adapter rechecks it under the row lock.
    if not appointment_contract.checkin_target_eligible(
        status=detail["status"],
        appointment_date=detail["appointment_date"],
        today=today,
        patient=patient,
        dedicated_patient=checkin_patient,
        has_receipt=False,
    ):
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)

    current_modified = gateway.native_appointment_modified(native_appointment_id)
    if current_modified is None:
        gateway.deny()
    if not patient_write.modified_matches(
        current_modified, payload["base_modified"]
    ):
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)

    # Phase one: the lock and its native permission checks, before any mutation.
    try:
        state = gateway.lock_appointment_target(
            native_appointment_id,
            base_modified=payload["base_modified"],
            expected=identity,
        )
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StaleVersion:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except patient_write.UnsafeSideEffect:
        _rollback_or_raise(gateway)
        gateway.deny()
    except Exception as exc:
        _rollback_or_raise(gateway)
        if gateway.is_native_permission_denial(exc):
            gateway.deny()
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase two: post-lock eligibility and the authoritative Patient read,
    # still before any mutation.
    try:
        locked_today = gateway.site_today()
        if locked_today is None:
            raise patient_write.WriteUncertain()
        if not appointment_contract.checkin_target_eligible(
            status=state.status,
            appointment_date=state.appointment_date,
            today=locked_today,
            patient=state.patient,
            dedicated_patient=checkin_patient,
            has_receipt=False,
        ):
            raise appointment_write.AppointmentTargetNotEligible()
        if gateway.native_patient_can_read(state.patient) is not True:
            # A definite denial under the row lock is not an uncertain outcome:
            # discard the transaction and return the framework permission error.
            raise _PatientReadDenied()
    except _PatientReadDenied:
        _rollback_or_raise(gateway)
        gateway.deny()
    except appointment_write.AppointmentTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase three: the native check-in and the one commit. From here a mutation
    # may already be applied, so every unexpected failure is uncertain.
    try:
        gateway.native_appointment_checkin(state, locked_today)
        gateway.commit()
    except appointment_write.AppointmentTargetNotEligible:
        # The adapter rechecks status/date before it calls the native entry point.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _success(
        gateway,
        settings,
        "checked_in",
        target.workspace_appointment_id,
        native_appointment_id,
    )
