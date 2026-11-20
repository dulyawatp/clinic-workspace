"""Read and discovery helpers for the synthetic appointment surface.

The list keeps its existing five-field contract. It has three disjoint sources:

* the static, site-owned allowlist (unchanged historical aliases),
* verified committed ``book`` receipts in the external fence, read only, so a
  historical workspace alias keeps resolving without any ledger mutation, and
* a bounded, permission-aware native ``Patient Appointment`` list for the
  current user on the exact approved development site, so appointments created
  by the converted lane are discoverable by their native docname.

Every native row is read with the current user's own document and field-level
permission checks. A native patient outside the static allowlist is published
by its native ``Patient`` docname only after the current user's own native
Patient read check succeeds.

A duplicate native record is listed once, preferring its historical alias so
existing links stay stable. A row whose patient the current user cannot read is
omitted from the list and fails the detail read closed. An unavailable
historical ledger omits that source; a malformed or ambiguous receipt still
fails the read closed, as before.

The separate detail contract adds only the authoritative ``modified`` version
and explicit action eligibility. A readable static fixture is never writable; a
historical booking is writable by its creator; a native record is writable when
the current user holds the native ``Patient Appointment`` write permission.
"""

from dataclasses import dataclass

from clinic_ui.core import appointment_contract, appointment_write, dto, patient_write
from clinic_ui.core.policy import APPOINTMENT_ROLE_CODES
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import aliases, common
from clinic_ui.runtime.visit_invoice import resolve_visit_invoice
from clinic_ui.runtime.visit_invoice_lookup import lookup_visit_invoice
from clinic_ui.runtime.visit_invoice_vat_authority import no_vat_allowed

SOURCE_STATIC = "static"
SOURCE_BOOKED = "booked"
SOURCE_NATIVE = "native"

# The dedicated visit-invoice SIT. Its own appointment docnames resolve for the
# configured doctor only; no other site or role gains that read.
VISIT_INVOICE_SITE = "clinic-visit-invoice.local"
VISIT_INVOICE_WRITE_USER_KEY = "clinic_ui_visit_invoice_write_user"
VISIT_INVOICE_READ_APPOINTMENTS_KEY = "clinic_ui_visit_invoice_read_appointments"
MAX_VISIT_INVOICE_READ_APPOINTMENTS = 64

OPEN_STATUSES = frozenset({"Open", "Confirmed"})
RESCHEDULE_STATUSES = frozenset({"Scheduled", "Open", "Confirmed"})
# Read-only enumeration cap for the historical ledger. It is not a write budget:
# the converted lane never consumes or checks one.
MAX_BOOK_RECEIPTS = appointment_write.budget("book")
# Hard bound on the permission-aware native discovery page.
MAX_NATIVE_APPOINTMENTS = 200


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


def patient_reference(gateway, settings, native_patient_id):
    """The client-facing patient identifier, or ``None`` without denying.

    A patient reference is published only after the current user's own native
    ``Patient`` read permission succeeds: a configured workspace alias is never
    permission to read its patient. A static allowlist patient then keeps its
    stable workspace alias; any other patient is published by its native
    ``Patient`` docname, and only on the approved development read scope.
    """

    if not is_valid_text(
        native_patient_id, appointment_write.MAX_APPOINTMENT_ID_LENGTH
    ):
        return None
    if gateway.native_patient_can_read(native_patient_id) is not True:
        return None
    workspace_patient_id = _workspace_patient_for(settings, native_patient_id)
    if workspace_patient_id is not None:
        return workspace_patient_id
    if aliases.native_read_scope_enabled(gateway):
        return native_patient_id
    return None


def native_write_scope_enabled(gateway, settings):
    """True only for the appointment write configuration on the approved site."""

    try:
        return (
            aliases.native_read_scope_enabled(gateway)
            and settings.appointment_write_enabled is True
        )
    except Exception:
        return False


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

    An unavailable, locked or uncertain historical ledger resolves nothing: the
    caller denies, no 500 escapes and the ledger is never written or repaired.
    A non-alias value returns before any ledger access at all.
    """

    operation_key = appointment_write.parse_alias(
        settings.epoch_secret, gateway.site, actor, workspace_appointment_id
    )
    if operation_key is None:
        return None
    try:
        receipt = gateway.lookup_appointment_receipt(
            actor, "book", operation_key
        )
    except (
        patient_write.WriteLocked,
        patient_write.WriteConflict,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return None
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


def resolve_write(gateway, settings, actor, workspace_appointment_id):
    """The native appointment this request may write, or ``None``.

    A static allowlist id — and therefore its readable fixture — is never
    writable. A historical sealed alias resolves only through the actor's own
    committed booking receipt, read-only. A value that is not alias-shaped may
    resolve as a native ``Patient Appointment`` docname, but only on the approved
    development site with the appointment write configuration and the current
    user's native document write permission. ``None`` means the caller denies;
    nothing is written and no historical ledger state is read for a native value.
    """

    if _static_lookup(settings, workspace_appointment_id) is not None:
        return None

    own = own_booking(gateway, settings, actor, workspace_appointment_id)
    if own is not None:
        return own

    if appointment_write.is_alias_shaped(workspace_appointment_id):
        # An alias-shaped value is never reinterpreted as a native docname.
        return None
    if not native_write_scope_enabled(gateway, settings):
        return None
    if gateway.native_appointment_can_write(workspace_appointment_id) is not True:
        return None
    return ResolvedAppointment(
        native_appointment_id=workspace_appointment_id,
        workspace_appointment_id=workspace_appointment_id,
        source=SOURCE_NATIVE,
    )


def resolve_patient(gateway, settings, actor, role, workspace_patient_id):
    """The native patient this request may name, or ``None``.

    Resolution is the converted lane's own: the static allowlist, a historical
    sealed create alias through its read-only committed receipt, or a native
    ``Patient`` docname the current user may read. Booking additionally requires
    the current user's native ``Patient Appointment`` create authority, which the
    endpoint checks separately.
    """

    try:
        resolved = aliases.resolve_read(
            gateway, settings, actor, role, workspace_patient_id
        )
    except (
        appointment_write.AppointmentWriteError,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return None
    return resolved.native_patient_id


def resolve_read(gateway, settings, actor, workspace_appointment_id):
    """Resolve one readable appointment without granting write authority.

    The static allowlist resolves as before. A booked alias resolves for its
    creator through the actor-bound receipt, or for a different clinician only
    through exactly one verified committed booking receipt. On the approved
    development site a native docname resolves too; the read itself still
    requires the current user's document and field-level permission. ``None``
    means the value is unknown; the caller denies.
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

    if not appointment_write.is_alias_shaped(workspace_appointment_id):
        if aliases.native_read_scope_enabled(gateway):
            return ResolvedAppointment(
                native_appointment_id=workspace_appointment_id,
                workspace_appointment_id=workspace_appointment_id,
                source=SOURCE_NATIVE,
            )
        return None

    matches = [
        record
        for record in discover_booked(gateway, settings)
        if record.workspace_appointment_id == workspace_appointment_id
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _dedicated_visit_invoice_native(gateway, actor, role, appointment_id):
    """Resolve one raw native appointment docname on the dedicated site only.

    The dedicated visit-invoice SIT keeps its own appointment reachable by
    docname for the configured doctor even with writes off, so a posted invoice
    stays reconcilable. The server-owned allowlist is a provisioning assertion
    that each ID names a native, non-booked synthetic visit; the dedicated site
    has no booking ingress. Admission still requires independently verified eligible
    visit ownership or a readable posted linkage, and the verified reads are
    returned so the projection does not repeat them. A static row, a booked
    alias or any other site resolves earlier and never reaches here.
    """

    try:
        if role != "DOC" or gateway.site != VISIT_INVOICE_SITE:
            return None
        if (not is_valid_text(
                appointment_id, appointment_write.MAX_APPOINTMENT_ID_LENGTH)
                or appointment_id != appointment_id.strip()
                or appointment_write.is_alias_shaped(appointment_id)):
            return None
        if (type(actor) is not str or not actor.strip() or actor != actor.strip()
                or actor in ("Guest", "Administrator")):
            return None
        configured = gateway._configuration().get(VISIT_INVOICE_WRITE_USER_KEY)
        if (type(configured) is not str or not configured.strip()
                or configured != configured.strip()
                or configured in ("Guest", "Administrator")
                or configured != actor):
            return None
        allowed = gateway._configuration().get(VISIT_INVOICE_READ_APPOINTMENTS_KEY)
        if (type(allowed) is not list
                or not 0 < len(allowed) <= MAX_VISIT_INVOICE_READ_APPOINTMENTS
                or any(type(item) is not str
                       or not is_valid_text(item, appointment_write.MAX_APPOINTMENT_ID_LENGTH)
                       or item != item.strip() or "*" in item
                       or appointment_write.is_alias_shaped(item)
                       for item in allowed)
                or len(set(allowed)) != len(allowed)
                or appointment_id not in allowed):
            return None
        resolved = ResolvedAppointment(
            native_appointment_id=appointment_id,
            workspace_appointment_id=appointment_id,
            source=SOURCE_NATIVE,
        )
        visit = resolve_visit_invoice(gateway, appointment_id)
        if visit is not None and visit.get("appointment_id") == appointment_id:
            return resolved, visit, None
        linked = lookup_visit_invoice(gateway, appointment_id)
        if (type(linked) is dict and set(linked) == {"invoice_id"}
                and is_valid_text(linked["invoice_id"], 140)
                and linked["invoice_id"] == linked["invoice_id"].strip()):
            return resolved, None, linked
        return None
    except Exception:
        return None


def checkin_patient_native_id(settings):
    """Return the configured dedicated check-in patient's native id."""

    if settings.appointment_checkin_patient is None:
        return None
    return native_patient_for(settings, settings.appointment_checkin_patient)


def _native_target_ids(gateway):
    """Bounded, permission-aware native appointment ids, or ``None``."""

    if not aliases.native_read_scope_enabled(gateway):
        return None
    names = gateway.list_readable_appointments(MAX_NATIVE_APPOINTMENTS)
    if not isinstance(names, (list, tuple)):
        return None
    return names


def write_gate_open(gateway, settings, native_appointment_id):
    """True only when the converted appointment write gate is open.

    The detail action flags must intersect this gate: the appointment write
    configuration, the executable native boundary, safe controller settings, the
    current user's document write authority and the linked-document reads. No
    historical fence or deployment marker is consulted. Any failure or exception
    is ``False``.
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
    """Expected configured identity for one resolved target.

    With an exact ``patient`` — the resolved target's own native id — only that
    patient is accepted. Without one, the configured synthetic allowlist set is
    accepted, which is the read-only detail default. Company, appointment type
    and medical department are always the exact configured values.
    """

    identity = {
        "company": settings.appointment_company,
        "appointment_type": settings.appointment_type,
        "department": settings.appointment_medical_department,
    }
    if patient is None:
        identity["patients"] = tuple(
            native for _workspace, native in settings.allowlist
        )
    else:
        identity["patient"] = patient
    return identity


def _action_flags(gateway, settings, actor, resolved, detail):
    """Return ``(can_reschedule, can_checkin)`` for one detail read.

    Every ``True`` intersects the converted gate, the non-mutating closed-world
    target check and the same shared eligibility rules the write endpoints use
    under the row lock. A native record is eligible for the caller who holds its
    native write permission; a historical booking is eligible for its creator.
    """

    if resolved.source == SOURCE_STATIC:
        return False, False
    if resolved.source == SOURCE_BOOKED and resolved.creator != actor:
        return False, False
    if not write_gate_open(gateway, settings, resolved.native_appointment_id):
        return False, False
    if (
        gateway.native_appointment_target_problem(
            resolved.native_appointment_id,
            expected=appointment_config_identity(settings, patient=detail["patient"]),
        )
        is not None
    ):
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
        has_receipt=False,
    )
    can_checkin = False
    if can_reschedule is False and detail["status"] in OPEN_STATUSES:
        # Target-scoped only: there is no cross-record uniqueness scan, because
        # a bounded page cannot establish uniqueness and native
        # ``check_in_appointment`` has no such rule. The locked status check in
        # the adapter is what refuses a second check-in of the same record.
        can_checkin = appointment_contract.checkin_target_eligible(
            status=detail["status"],
            appointment_date=detail["appointment_date"],
            today=today,
            patient=detail["patient"],
            dedicated_patient=native_patient_id,
            has_receipt=False,
        )
    return can_reschedule, can_checkin


def _item_for(settings, gateway, workspace_appointment_id, native_appointment_id):
    """Read one native row for the list; ``None`` omits an unmappable patient."""

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

    workspace_patient_id = patient_reference(gateway, settings, patient)
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
        # The historical ledger is unavailable, so no booked record can be
        # verified. The converted native slice and the static read contract are
        # preserved without inventing rows.
        booked = []

    seen_native = {
        native_appointment_id
        for _workspace, native_appointment_id in settings.appointment_allowlist
    }
    for target in booked:
        if target.native_appointment_id in seen_native:
            # Ambiguous historical evidence fails the whole read closed.
            gateway.deny()
        seen_native.add(target.native_appointment_id)
        item = _item_for(
            settings,
            gateway,
            target.workspace_appointment_id,
            target.native_appointment_id,
        )
        if item is None:
            # The current user cannot read the patient behind this booking; an
            # inaccessible appointment is omitted rather than described.
            continue
        items.append(item)

    available = dto.MAX_APPOINTMENT_ITEMS - len(items)
    native_ids = _native_target_ids(gateway) if available > 0 else None
    if native_ids is not None:
        for native_appointment_id in native_ids[:available]:
            if not is_valid_text(
                native_appointment_id, appointment_write.MAX_APPOINTMENT_ID_LENGTH
            ):
                continue
            if native_appointment_id in seen_native:
                # A historical alias or a static row already represents it; the
                # native docname is not listed a second time.
                continue
            item = _item_for(
                settings, gateway, native_appointment_id, native_appointment_id
            )
            if item is None:
                continue
            seen_native.add(native_appointment_id)
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

    dedicated = None
    if resolved is None:
        # Do not turn a configured static target into a raw native reference.
        if any(native == workspace_appointment_id
               for _, native in settings.appointment_allowlist):
            gateway.deny()
        dedicated = _dedicated_visit_invoice_native(
            gateway, view.user, role, workspace_appointment_id
        )
        if dedicated is None:
            gateway.deny()
        resolved, dedicated_visit, dedicated_linked = dedicated

    detail = gateway.native_appointment_detail(resolved.native_appointment_id)
    if detail is None:
        gateway.deny()
    try:
        patient = detail["patient"]
    except (KeyError, TypeError):
        gateway.deny()

    if dedicated is not None and dedicated_visit is not None:
        if dedicated_visit.get("patient") != patient:
            gateway.deny()

    # Booking, detail and check-in each enforce the current user's own Patient
    # read authority; configuration alone is never permission to read a patient.
    if gateway.native_patient_can_read(patient) is not True:
        gateway.deny()

    workspace_patient_id = patient_reference(gateway, settings, patient)
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

    can_reschedule, can_checkin = (
        (False, False) if dedicated is not None else
        _action_flags(gateway, settings, view.user, resolved, detail)
    )
    try:
        result = dto.appointment_read(
            payload,
            can_reschedule=can_reschedule,
            can_checkin=can_checkin,
        )
    except ValueError:
        gateway.deny()

    # Never turn an alias (including a static target addressed by its native
    # name) into a disclosed native identifier. Both evidence paths perform
    # their own current-user, active-practitioner and linkage checks.
    if (role == "DOC" and resolved.source == SOURCE_NATIVE
            and resolved.workspace_appointment_id == resolved.native_appointment_id
            and all(native != resolved.native_appointment_id
                    for _, native in settings.appointment_allowlist)):
        native_id = resolved.native_appointment_id
        try:
            if dedicated is not None:
                visit, linked = dedicated_visit, dedicated_linked
            else:
                visit = resolve_visit_invoice(gateway, native_id)
                linked = lookup_visit_invoice(gateway, native_id)
            if visit is not None and visit.get("patient") == patient:
                result["visit_invoice_appointment_id"] = native_id
                # The No VAT choice exists only beside a verified eligible visit
                # and only for the configured doctor on the dedicated site.
                if no_vat_allowed(gateway, native_id) is True:
                    result["visit_invoice_no_vat_allowed"] = True
            elif (type(linked) is dict and set(linked) == {"invoice_id"}
                    and is_valid_text(linked["invoice_id"], 140)):
                result["visit_invoice_appointment_id"] = native_id
        except Exception:
            pass
    return result
