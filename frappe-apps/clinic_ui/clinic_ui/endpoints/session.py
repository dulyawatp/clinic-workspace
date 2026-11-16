"""``GET /api/method/clinic_ui.api.session.bootstrap`` implementation.

Three distinct outcomes, never merged:

- **anonymous/expired** (no session, guest session id, guest or blank user):
  a fixed ``{"code": "AUTH_EXPIRED"}`` body with HTTP 401, so the client can
  reach sign-in. No CSRF token, site configuration, epoch or identity is read.
- **authenticated but unauthorized** (administrator, disabled user, wrong or
  ambiguous role, unavailable configuration): the framework permission error
  (403) with a generic message.
- **authorized**: live session description, opaque epoch, capability map and
  the server-generated session CSRF token. The map is read-only except that the
  two patient write capabilities may be enabled for a DOC/NUR session after the
  full reviewed local write gate passes.

The raw session identifier, the cookie and the epoch secret never enter a
payload and are never logged.
"""

from clinic_ui.core import dto
from clinic_ui.core.capabilities import bootstrap_capabilities
from clinic_ui.core.epoch import session_epoch
from clinic_ui.core.policy import (
    ADMIN_ROLE_CODES,
    ADJUST_ROLE_CODES,
    APPOINTMENT_WRITE_ROLE_CODES,
    ENCOUNTER_COMPLETE_ROLE_CODES,
    ENCOUNTER_ROLE_CODES,
    PATIENT_WRITE_ROLE_CODES,
    PROCEDURE_ROLE_CODES,
    STOCK_ROLE_CODES,
    SUPPORTED_ROLE_CODES,
)
from clinic_ui.endpoints import common

def _patient_write_flags(gateway, settings, role):
    """Return ``(create, update)`` after the full reviewed local write gate.

    All conditions must pass: the exact-site write configuration, the private
    deployment marker, a safely initialized fence store, Healthcare Settings
    without automatic side effects, and the caller's native Patient permission.
    Any false result, non-boolean result or raised error fails both capabilities
    closed. No patient record is read.
    """

    if role not in PATIENT_WRITE_ROLE_CODES:
        return False, False
    try:
        if not settings.write_enabled:
            return False, False
        if gateway.write_deployment_approved() is not True:
            return False, False
        if gateway.write_fence_ready() is not True:
            return False, False
        if gateway.healthcare_settings_side_effects_unsafe() is not False:
            return False, False
        return (
            gateway.native_patient_can_create() is True,
            gateway.native_patient_can_write() is True,
        )
    except Exception:
        return False, False


def _appointment_write_flags(gateway, settings, role):
    """Return ``(book, reschedule, cancel, no_show, checkin)`` after the gate.

    The gate is independent of the patient write flag: the exact approved site,
    the appointment flag, the private deployment marker, a ready fence store,
    safe appointment settings and the caller's own native Patient Appointment
    and linked-doctype reads. Per-record targets and budgets are decided by the
    write endpoints; these flags only advertise that the surface exists.
    """

    disabled = (False, False, False, False, False)
    if role not in APPOINTMENT_WRITE_ROLE_CODES:
        return disabled
    try:
        if gateway.native_appointment_boundary_problem() is not None:
            return disabled
        if not settings.appointment_write_enabled:
            return disabled
        if settings.appointment_medical_department is None:
            return disabled
        if settings.appointment_type is None:
            return disabled
        if settings.appointment_company is None:
            return disabled
        if gateway.write_deployment_approved() is not True:
            return disabled
        if gateway.write_fence_ready() is not True:
            return disabled
        if gateway.healthcare_appointment_side_effects_unsafe() is not False:
            return disabled
        if gateway.native_appointment_can_create() is not True:
            return disabled
        if gateway.native_appointment_can_write() is not True:
            return disabled
        for doctype, doc in (
            ("Company", settings.appointment_company),
            ("Appointment Type", settings.appointment_type),
            ("Medical Department", settings.appointment_medical_department),
            ("Gender", None),
        ):
            if gateway.native_doctype_can_read(doctype, doc) is not True:
                return disabled
        type_contract = gateway.native_appointment_type_contract(
            settings.appointment_type
        )
        if (
            type_contract is None
            or type_contract["allow_booking_for"] != "Department"
        ):
            return disabled
        return True, True, True, True, True
    except Exception:
        return disabled


def _encounter_write_flags(gateway, settings, role):
    """Return ``(draft, complete, amend)`` after the encounter write gate.

    Draft (and the medication/lab ordering that rides it) admits DOC and NUR;
    completion and amendment are DOC-only. The gate is independent of the other
    write flags: exact approved site, encounter flag, deployment marker, ready
    fence store, safe Healthcare Settings, installed-field preflight, no
    boundary drift and the caller's own native authority.
    """

    disabled = (False, False, False)
    if role not in ENCOUNTER_ROLE_CODES:
        return disabled
    try:
        if gateway.native_encounter_boundary_problem() is not None:
            return disabled
        if gateway.native_encounter_install_problem() is not None:
            return disabled
        if not settings.encounter_write_enabled:
            return disabled
        if settings.encounter_practitioner is None:
            return disabled
        if settings.encounter_company is None:
            return disabled
        if gateway.write_deployment_approved() is not True:
            return disabled
        if gateway.write_fence_ready() is not True:
            return disabled
        if gateway.healthcare_encounter_side_effects_unsafe() is not False:
            return disabled
        if gateway.native_patient_can_read is None:
            return disabled
        for doctype, doc in (
            ("Healthcare Practitioner", settings.encounter_practitioner),
            ("Company", settings.encounter_company),
        ):
            if gateway.native_doctype_can_read(doctype, doc) is not True:
                return disabled
        draft_ok = gateway.native_encounter_can_create() is True
        complete_ok = (
            role in ENCOUNTER_COMPLETE_ROLE_CODES
            and gateway.native_encounter_can_write() is True
            and gateway.native_clinical_note_can_create() is True
        )
        return draft_ok, complete_ok, complete_ok
    except Exception:
        return disabled


def _procedure_write_flags(gateway, settings, role):
    """Return ``True`` when the procedure-completion surface is open.

    Independent of the other write flags: exact approved site, boundary and
    installed-field preflight, the procedure flag, the deployment marker, a ready
    fence store and the caller's own native Clinical Procedure write authority.
    The target and its price list / batch / stock state are decided per request;
    this flag only advertises that the surface exists.
    """

    if role not in PROCEDURE_ROLE_CODES:
        return False
    try:
        if gateway.native_procedure_boundary_problem() is not None:
            return False
        if gateway.native_procedure_install_problem() is not None:
            return False
        if not settings.procedure_write_enabled:
            return False
        if not settings.procedure_allowlist:
            return False
        if gateway.write_deployment_approved() is not True:
            return False
        if gateway.write_fence_ready() is not True:
            return False
        return gateway.native_procedure_can_write() is True
    except Exception:
        return False


def _stock_write_flags(gateway, settings, role):
    """Return ``(receive, transfer, adjust)`` when the stock surface is open.

    Independent of every other write flag: exact approved site, boundary and
    installed-field preflight, the stock flag, the deployment marker, a ready
    fence store and the caller's own native authority on the document it would
    submit. Masters, batches and quantities are decided per request; these
    flags only advertise that the surface exists. Reconciliation is the ADM
    surface, receive/transfer the inventory one.
    """

    disabled = (False, False, False)
    try:
        if gateway.native_stock_boundary_problem() is not None:
            return disabled
        if gateway.native_stock_install_problem() is not None:
            return disabled
        if not settings.stock_write_enabled:
            return disabled
        if gateway.write_deployment_approved() is not True:
            return disabled
        if gateway.write_fence_ready() is not True:
            return disabled
        receive_ok = (
            role in STOCK_ROLE_CODES
            and gateway.native_stock_can_create("Purchase Receipt") is True
        )
        transfer_ok = (
            role in STOCK_ROLE_CODES
            and gateway.native_stock_can_create("Stock Entry") is True
        )
        adjust_ok = (
            role in ADJUST_ROLE_CODES
            and gateway.native_stock_can_create("Stock Reconciliation") is True
        )
        return receive_ok, transfer_ok, adjust_ok
    except Exception:
        return disabled


def _admin_write_flags(gateway, settings, role):
    """Return ``(user, master)`` when the admin surface is open.

    Independent of every clinical write flag: exact approved site, installed
    preflight, the admin flag, the deployment marker, a ready fence store and
    the caller's own native authority. The role allowlist and duplicate checks
    are decided per request; these flags only advertise that the surface
    exists.
    """

    disabled = (False, False)
    if role not in ADMIN_ROLE_CODES:
        return disabled
    try:
        if gateway.native_admin_install_problem() is not None:
            return disabled
        if not settings.admin_write_enabled:
            return disabled
        if gateway.write_deployment_approved() is not True:
            return disabled
        if gateway.write_fence_ready() is not True:
            return disabled
        if gateway.native_admin_can_write() is not True:
            return disabled
        return True, True
    except Exception:
        return disabled


def bootstrap(gateway):
    common.begin(gateway)

    view = common.live_view(gateway)
    if view is None:
        return gateway.respond_anonymous()

    common.require_authorized_session(gateway, view)
    settings = common.settings_or_deny(gateway)
    role = common.role_or_deny(gateway, view, settings, SUPPORTED_ROLE_CODES)

    csrf_token = gateway.csrf_token()
    if not dto.is_valid_text(csrf_token, dto.MAX_CSRF_TOKEN_LENGTH):
        gateway.deny()

    try:
        epoch = session_epoch(settings.epoch_secret, gateway.site, view.sid)
        write_create, write_update = _patient_write_flags(gateway, settings, role)
        (
            write_appointment_book,
            write_appointment_reschedule,
            write_appointment_cancel,
            write_appointment_no_show,
            write_appointment_checkin,
        ) = _appointment_write_flags(gateway, settings, role)
        (
            write_encounter_draft,
            write_encounter_complete,
            write_encounter_amend,
        ) = _encounter_write_flags(gateway, settings, role)
        write_procedure_complete = _procedure_write_flags(
            gateway, settings, role
        )
        (
            write_stock_receive,
            write_stock_transfer,
            write_stock_adjust,
        ) = _stock_write_flags(gateway, settings, role)
        write_admin_user, write_admin_master = _admin_write_flags(
            gateway, settings, role
        )
        capabilities = bootstrap_capabilities(
            write_patient_create=write_create,
            write_patient_update=write_update,
            write_appointment_book=write_appointment_book,
            write_appointment_reschedule=write_appointment_reschedule,
            write_appointment_cancel=write_appointment_cancel,
            write_appointment_no_show=write_appointment_no_show,
            write_appointment_checkin=write_appointment_checkin,
            write_encounter_draft=write_encounter_draft,
            write_encounter_complete=write_encounter_complete,
            write_encounter_amend=write_encounter_amend,
            write_procedure_complete=write_procedure_complete,
            write_stock_receive=write_stock_receive,
            write_stock_transfer=write_stock_transfer,
            write_stock_adjust=write_stock_adjust,
            write_admin_user=write_admin_user,
            write_admin_master=write_admin_master,
        )
        return dto.build_bootstrap(
            session_epoch=epoch,
            user_id=view.user,
            display_name=view.full_name,
            role=role,
            csrf_token=csrf_token,
            capabilities=capabilities,
        )
    except ValueError:
        gateway.deny()
