"""Read and discovery helpers for the synthetic appointment surface.

The list keeps its existing five-field contract and the static, site-owned
allowlist. Newly booked records are added only through *verified* committed
``book`` receipts in the external fence: each alias is re-derived from the
recorded actor, site and operation key, the receipt is decoded with the
appointment codec (which refuses every patient receipt) and the native row is
then read with the current user's own document and field-level permission
checks. The native appointment docname and native patient id never leave the
server.

Discovery is bounded by the approved booking budget. An over-full page, a
duplicate alias/native record, a tampered receipt or a patient outside the
explicit synthetic allowlist fails the whole response instead of returning a
partial or ambiguous list.

The separate detail contract adds only the authoritative ``modified`` version
and explicit action eligibility. A readable static fixture is never writable;
only the creator's own committed booking can be rescheduled, and only the
dedicated same-day booking for the configured synthetic patient can be checked
in.
"""

from dataclasses import dataclass

from clinic_ui.core import appointment_contract, appointment_write, dto, patient_write
from clinic_ui.core.policy import APPOINTMENT_ROLE_CODES
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import common

SOURCE_STATIC = "static"
SOURCE_BOOKED = "booked"

OPEN_STATUSES = frozenset({"Open", "Confirmed"})
RESCHEDULE_STATUSES = frozenset({"Scheduled", "Open", "Confirmed"})
MAX_BOOK_RECEIPTS = appointment_write.budget("book")


class _ReceiptRecord:
    """Adapter-shaped record for one already-fetched receipt."""

    __slots__ = ("actor", "operation_key", "receipt")

    def __init__(self, *, actor, operation_key, receipt):
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


@dataclass(frozen=True)
class ResolvedAppointment:
    """One resolved appointment; ``creator`` is ``None`` for the static list."""

    native_appointment_id: str
    workspace_appointment_id: str
    source: str
    creator: str | None = None


def _workspace_patient_for(settings, native_patient_id):
    for workspace_patient_id, allowed_native_id in settings.allowlist:
        if allowed_native_id == native_patient_id:
            return workspace_patient_id
    return None


def native_patient_for(settings, workspace_patient_id):
    """Return the allowlisted native patient id, or ``None``."""

    for allowed_workspace_id, native_patient_id in settings.allowlist:
        if allowed_workspace_id == workspace_patient_id:
            return native_patient_id
    return None


def workspace_patient_for(settings, native_patient_id):
    """Public wrapper: the allowlisted workspace id, or ``None``."""

    return _workspace_patient_for(settings, native_patient_id)


def _static_lookup(settings, workspace_appointment_id):
    for workspace_id, native_appointment_id in settings.appointment_allowlist:
        if workspace_id == workspace_appointment_id:
            return native_appointment_id
    return None


def _verify_receipt(settings, gateway, record, *, expected_alias):
    """Return the verified booked target or ``None``.

    The alias must be exactly the one this site derives for the recorded actor
    and operation key, and the decoded receipt must be a ``book`` receipt for
    that same alias. A patient receipt, a reschedule/check-in receipt, a
    mismatch or any malformed value returns ``None``.
    """

    try:
        alias = appointment_write.build_alias(
            settings.epoch_secret,
            gateway.site,
            record.actor,
            record.operation_key,
        )
        receipt = appointment_write.decode_receipt(record.receipt)
    except appointment_write.AppointmentWriteError:
        return None
    if receipt.kind != "book":
        return None
    if receipt.alias != alias:
        return None
    if expected_alias is not None and alias != expected_alias:
        return None
    return ResolvedAppointment(
        native_appointment_id=receipt.native_appointment_id,
        workspace_appointment_id=alias,
        source=SOURCE_BOOKED,
        creator=record.actor,
    )


def discover_booked(gateway, settings):
    """Return the verified booked records, bounded and read-only.

    Raises ``appointment_write.AppointmentBudgetExhausted`` on an over-full
    page and ``patient_write.WriteUncertain``/``StoreUnavailable`` on an
    unusable ledger, so the caller can fail the whole read closed.
    """

    records = gateway.list_committed_appointment_receipts(
        MAX_BOOK_RECEIPTS, kind="book"
    )
    resolved = []
    aliases = set()
    native_ids = set()
    for record in records:
        target = _verify_receipt(settings, gateway, record, expected_alias=None)
        if target is None:
            raise appointment_write.AppointmentWriteError("unverifiable receipt")
        if (
            target.workspace_appointment_id in aliases
            or target.native_appointment_id in native_ids
        ):
            raise appointment_write.AppointmentWriteError("ambiguous receipts")
        aliases.add(target.workspace_appointment_id)
        native_ids.add(target.native_appointment_id)
        resolved.append(target)
    return resolved


def own_booking(gateway, settings, actor, workspace_appointment_id):
    """Resolve the current actor's own committed booking, or ``None``.

    Only an alias this site derives from the actor and the parsed operation key,
    backed by that actor's terminal ``book`` receipt, resolves. The static
    allowlist and every other actor's booking are deliberately absent, so a
    readable fixture or another clinician's record can never become writable.
    """

    operation_key = appointment_write.parse_alias(
        settings.epoch_secret, gateway.site, actor, workspace_appointment_id
    )
    if operation_key is None:
        return None
    receipt = gateway.lookup_appointment_receipt(actor, "book", operation_key)
    if receipt is None:
        return None
    record = _ReceiptRecord(actor=actor, operation_key=operation_key, receipt=receipt)
    target = _verify_receipt(
        settings, gateway, record, expected_alias=workspace_appointment_id
    )
    if target is None:
        return None
    if target.workspace_appointment_id != workspace_appointment_id:
        return None
    return target


def resolve_read(gateway, settings, actor, workspace_appointment_id):
    """Resolve one readable appointment without granting write authority.

    The static allowlist resolves as before. A booked alias resolves for its
    creator through the actor-bound receipt, or for a different clinician only
    through exactly one verified committed booking receipt. ``None`` means the
    value is unknown; the caller denies.
    """

    native_appointment_id = _static_lookup(settings, workspace_appointment_id)
    if native_appointment_id is not None:
        return ResolvedAppointment(
            native_appointment_id=native_appointment_id,
            workspace_appointment_id=workspace_appointment_id,
            source=SOURCE_STATIC,
        )

    own = own_booking(gateway, settings, actor, workspace_appointment_id)
    if own is not None:
        return own

    matches = [
        record
        for record in discover_booked(gateway, settings)
        if record.workspace_appointment_id == workspace_appointment_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def checkin_patient_native_id(settings):
    """Return the configured dedicated check-in patient's native id."""

    if settings.appointment_checkin_patient is None:
        return None
    return native_patient_for(settings, settings.appointment_checkin_patient)


def eligible_checkin_targets(gateway, settings, actor):
    """Return the actor's booked records that are valid same-day check-ins.

    Eligibility is re-read from the native record for the current user: the
    configured dedicated patient, the site's current date and an Open/Confirmed
    status. The caller must require exactly one match equal to the requested
    target. Any read or ledger failure propagates so the caller fails closed.
    """

    native_patient_id = checkin_patient_native_id(settings)
    if native_patient_id is None:
        return []
    today = gateway.site_today()
    if today is None:
        return []

    eligible = []
    for target in discover_booked(gateway, settings):
        if target.creator != actor:
            continue
        detail = gateway.native_appointment_view(target.native_appointment_id)
        if detail is None:
            raise appointment_write.AppointmentWriteError("unreadable target")
        if (
            detail["patient"] == native_patient_id
            and detail["appointment_date"] == today
            and detail["status"] in OPEN_STATUSES
        ):
            eligible.append(target.native_appointment_id)
    return eligible


def write_gate_open(gateway, settings, native_appointment_id):
    """True only when the whole effective appointment write gate is open.

    The detail action flags must intersect this gate: configuration, the private
    deployment marker, a ready fence store, safe controller settings, the
    current user's document write authority and the linked-document reads. Any
    failure or exception is ``False``.
    """

    try:
        if gateway.native_appointment_boundary_problem() is not None:
            return False
        if not settings.appointment_write_enabled:
            return False
        if settings.appointment_medical_department is None:
            return False
        if settings.appointment_type is None:
            return False
        if gateway.write_deployment_approved() is not True:
            return False
        if gateway.write_fence_ready() is not True:
            return False
        if gateway.healthcare_appointment_side_effects_unsafe() is not False:
            return False
        if (
            gateway.native_appointment_can_write(native_appointment_id)
            is not True
        ):
            return False
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
        if (
            type_contract is None
            or type_contract["allow_booking_for"] != "Department"
        ):
            return False
        return True
    except Exception:
        return False


def appointment_config_identity(settings, patient=None):
    """Exact configured synthetic identity enforced on locked detail/targets."""

    return {
        "patient": patient,
        "patients": tuple(native for _workspace, native in settings.allowlist),
        "company": settings.appointment_company,
        "appointment_type": settings.appointment_type,
        "department": settings.appointment_medical_department,
    }


def _action_flags(gateway, settings, actor, resolved, detail):
    """Return ``(can_reschedule, can_checkin)`` for one detail read.

    Every ``True`` intersects the whole effective gate, the non-mutating
    closed-world target check and the same shared eligibility rules the write
    endpoints use under the row lock.
    """

    if resolved.source != SOURCE_BOOKED or resolved.creator != actor:
        return False, False
    if not write_gate_open(gateway, settings, resolved.native_appointment_id):
        return False, False
    if (
        gateway.native_appointment_target_problem(
            resolved.native_appointment_id,
            expected=appointment_config_identity(settings),
        )
        is not None
    ):
        return False, False

    try:
        reschedules = gateway.list_committed_appointment_receipts(
            appointment_write.budget("reschedule"), kind="reschedule"
        )
        checkins = gateway.list_committed_appointment_receipts(
            appointment_write.budget("checkin"), kind="checkin"
        )
    except appointment_write.AppointmentBudgetExhausted:
        return False, False

    today = gateway.site_today()
    if today is None:
        return False, False
    native_patient_id = checkin_patient_native_id(settings)
    can_reschedule = appointment_contract.reschedule_target_eligible(
        status=detail["status"],
        appointment_date=detail["appointment_date"],
        patient=detail["patient"],
        today=today,
        dedicated_patient=native_patient_id,
        has_receipt=bool(reschedules),
    )
    can_checkin = False
    if can_reschedule is False and detail["status"] in OPEN_STATUSES:
        try:
            eligible = eligible_checkin_targets(gateway, settings, actor)
        except Exception:
            return can_reschedule, False
        can_checkin = appointment_contract.checkin_target_eligible(
            status=detail["status"],
            appointment_date=detail["appointment_date"],
            today=today,
            patient=detail["patient"],
            dedicated_patient=native_patient_id,
            has_receipt=bool(checkins),
            is_unique_eligible=(
                len(eligible) == 1
                and eligible[0] == resolved.native_appointment_id
            ),
        )
    return can_reschedule, can_checkin


def _item_for(settings, gateway, workspace_appointment_id, native_appointment_id):
    """Read one native row for the list; ``None`` fails the whole response."""

    detail = gateway.native_appointment_view(native_appointment_id)
    if detail is None:
        gateway.deny()
    try:
        patient = detail["patient"]
        appointment_date = detail["appointment_date"]
        appointment_time = detail["appointment_time"]
        status = detail["status"]
    except (KeyError, TypeError):
        gateway.deny()

    workspace_patient_id = _workspace_patient_for(settings, patient)
    if workspace_patient_id is None:
        return None
    try:
        return dto.appointment_item(
            workspace_patient_id=workspace_patient_id,
            appointment_id=workspace_appointment_id,
            appointment_date=appointment_date,
            appointment_time=appointment_time,
            status=status,
        )
    except ValueError:
        gateway.deny()


def synthetic_appointments(gateway):
    settings, _session, _role = common.authorize(gateway, APPOINTMENT_ROLE_CODES)

    items = []
    for workspace_appointment_id, native_appointment_id in (
        settings.appointment_allowlist
    ):
        item = _item_for(
            settings, gateway, workspace_appointment_id, native_appointment_id
        )
        if item is not None:
            items.append(item)
        else:
            # A static row whose patient is outside the allowlist is omitted,
            # preserving the existing read contract.
            continue

    try:
        booked = discover_booked(gateway, settings)
    except (
        appointment_write.AppointmentWriteError,
        appointment_write.AppointmentBudgetExhausted,
    ):
        # An over-full page or an unverifiable/ambiguous receipt fails closed;
        # a truncated list must never look complete.
        gateway.deny()
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        # The fence is unavailable, so no booked record can be verified. The
        # existing static read contract is preserved without inventing rows.
        booked = []

    seen_native = set()
    for target in booked:
        if target.native_appointment_id in seen_native:
            gateway.deny()
        seen_native.add(target.native_appointment_id)
        item = _item_for(
            settings,
            gateway,
            target.workspace_appointment_id,
            target.native_appointment_id,
        )
        if item is None:
            gateway.deny()
        items.append(item)

    try:
        return dto.build_appointments(items)
    except ValueError:
        gateway.deny()


def appointment_detail(gateway, workspace_appointment_id=None):
    """Return one versioned appointment with explicit action eligibility."""

    settings, view, role = common.authorize(gateway, APPOINTMENT_ROLE_CODES)

    if not is_valid_text(
        workspace_appointment_id, appointment_write.MAX_WORKSPACE_ID_LENGTH
    ):
        gateway.deny()

    try:
        resolved = resolve_read(
            gateway, settings, view.user, workspace_appointment_id
        )
    except (
        appointment_write.AppointmentWriteError,
        appointment_write.AppointmentBudgetExhausted,
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        gateway.deny()

    if resolved is None:
        gateway.deny()

    detail = gateway.native_appointment_detail(resolved.native_appointment_id)
    if detail is None:
        gateway.deny()
    try:
        patient = detail["patient"]
    except (KeyError, TypeError):
        gateway.deny()

    # Booking, detail and check-in each enforce the current user's own Patient
    # read authority; configuration alone is never permission to read a patient.
    if gateway.native_patient_can_read(patient) is not True:
        gateway.deny()

    workspace_patient_id = _workspace_patient_for(settings, patient)
    if workspace_patient_id is None:
        gateway.deny()

    try:
        payload = dto.appointment_detail(
            workspace_patient_id=workspace_patient_id,
            appointment_id=resolved.workspace_appointment_id,
            appointment_date=detail["appointment_date"],
            appointment_time=detail["appointment_time"],
            status=detail["status"],
            modified=detail["modified"],
        )
    except (KeyError, TypeError, ValueError):
        gateway.deny()

    can_reschedule, can_checkin = _action_flags(
        gateway, settings, view.user, resolved, detail
    )
    try:
        return dto.appointment_read(
            payload,
            can_reschedule=can_reschedule,
            can_checkin=can_checkin,
        )
    except ValueError:
        gateway.deny()
