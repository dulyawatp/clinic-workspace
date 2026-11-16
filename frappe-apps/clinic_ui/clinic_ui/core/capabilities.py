"""The capability map published by the bootstrap endpoint.

Names and order mirror ``contracts/capabilities.json`` exactly; the parity test
fails if the client contract and this list drift. The default map is
``read_only``. Only the reviewed patient create/edit, the five appointment
writes (book, reschedule, cancel, no-show, check-in) and the deployment-gated
writes added by the full write-contract wave may be promoted to ``enabled``,
and only after the bootstrap endpoint has verified the whole reviewed local
write gate for that surface. Every other capability stays ``read_only``.
"""

CAPABILITY_NAMES = (
    "write.patient.create",
    "write.patient.update",
    "write.appointment.book",
    "write.appointment.reschedule",
    "write.appointment.cancel",
    "write.appointment.no_show",
    "write.appointment.checkin",
    "write.encounter.draft",
    "write.encounter.complete",
    "write.encounter.amend",
    "write.medication.order",
    "write.procedure.complete",
    "write.lab.order",
    "write.lab.review",
    "write.photo.consent",
    "write.photo.upload",
    "write.photo.link",
    "write.photo.annotate",
    "write.stock.receive",
    "write.stock.transfer",
    "write.stock.adjust",
    "write.stock.consume",
    "write.purchase_order.draft",
    "write.purchase_order.submit",
    "write.invoice.submit",
    "write.invoice.void_amend",
    "write.payment.receive",
    "write.payment.refund",
    "write.admin.user",
    "write.admin.master",
    "read.patient",
    "read.appointment",
    "read.encounter",
    "read.lab",
    "read.photo",
    "read.inventory",
    "read.billing",
    "read.report",
    "read.admin",
)

READ_ONLY_STATE = "read_only"
ENABLED_STATE = "enabled"

# Bootstrap may only publish these two states. ``denied``,
# ``blocked_unverified`` and ``error_upstream`` are read-time outcomes, not
# capability declarations.
BOOTSTRAP_STATES = frozenset({READ_ONLY_STATE, ENABLED_STATE})

PATIENT_WRITE_NAMES = frozenset(
    {"write.patient.create", "write.patient.update"}
)
# The reviewed local appointment surface. The designed cancel transition and
# the manual no-show ride the same reviewed appointment gate as booking,
# rescheduling and check-in: the exact approved site, the appointment flag, the
# private deployment marker, a ready store, safe controller settings and the
# caller's own native authority. Only the generic status setter stays absent.
APPOINTMENT_WRITE_NAMES = frozenset(
    {
        "write.appointment.book",
        "write.appointment.reschedule",
        "write.appointment.cancel",
        "write.appointment.no_show",
        "write.appointment.checkin",
    }
)
# The full write-contract wave: every new write id may be promoted by its own
# site-config deployment gate exactly like the reviewed writes above. Bootstrap
# publishes them read-only until that gate is wired.
DEPLOYMENT_GATED_WRITE_NAMES = frozenset(
    {
        "write.encounter.draft",
        "write.encounter.complete",
        "write.encounter.amend",
        "write.medication.order",
        "write.lab.order",
        "write.procedure.complete",
        "write.invoice.void_amend",
        "write.stock.consume",
        "write.admin.user",
        "write.admin.master",
    }
)
ENABLEABLE_NAMES = (
    PATIENT_WRITE_NAMES | APPOINTMENT_WRITE_NAMES | DEPLOYMENT_GATED_WRITE_NAMES
)


def read_only_capabilities():
    """Return a fresh mapping of every capability to ``read_only``."""

    return {name: READ_ONLY_STATE for name in CAPABILITY_NAMES}


def bootstrap_capabilities(
    *,
    write_patient_create=False,
    write_patient_update=False,
    write_appointment_book=False,
    write_appointment_reschedule=False,
    write_appointment_cancel=False,
    write_appointment_no_show=False,
    write_appointment_checkin=False,
    write_encounter_draft=False,
    write_encounter_complete=False,
    write_encounter_amend=False,
    write_procedure_complete=False,
    write_admin_user=False,
    write_admin_master=False,
):
    """Return the default read-only map with reviewed writes enabled.

    Callers must verify the full local write gate before passing ``True``. The
    function itself only expresses intent; it is the bootstrap endpoint that
    decides. Every other capability remains ``read_only``.
    """

    capabilities = read_only_capabilities()
    if write_patient_create is True:
        capabilities["write.patient.create"] = ENABLED_STATE
    if write_patient_update is True:
        capabilities["write.patient.update"] = ENABLED_STATE
    if write_appointment_book is True:
        capabilities["write.appointment.book"] = ENABLED_STATE
    if write_appointment_reschedule is True:
        capabilities["write.appointment.reschedule"] = ENABLED_STATE
    if write_appointment_cancel is True:
        capabilities["write.appointment.cancel"] = ENABLED_STATE
    if write_appointment_no_show is True:
        capabilities["write.appointment.no_show"] = ENABLED_STATE
    if write_appointment_checkin is True:
        capabilities["write.appointment.checkin"] = ENABLED_STATE
    # Medication ordering and native lab ordering ride the encounter draft
    # payload; there is no separate endpoint, so they share its gate.
    if write_encounter_draft is True:
        capabilities["write.encounter.draft"] = ENABLED_STATE
        capabilities["write.medication.order"] = ENABLED_STATE
        capabilities["write.lab.order"] = ENABLED_STATE
    if write_encounter_complete is True:
        capabilities["write.encounter.complete"] = ENABLED_STATE
    if write_encounter_amend is True:
        capabilities["write.encounter.amend"] = ENABLED_STATE
    # Completing a procedure is the stock-consumption write (SPEC:185-220): the
    # pair below is enabled by the one procedure gate, because the stock move is
    # never a separate client operation.
    if write_procedure_complete is True:
        capabilities["write.procedure.complete"] = ENABLED_STATE
        capabilities["write.stock.consume"] = ENABLED_STATE
    # Admin users and masters are two independent contract surfaces that share
    # one site-config gate; bootstrap passes each only after that gate passes.
    if write_admin_user is True:
        capabilities["write.admin.user"] = ENABLED_STATE
    if write_admin_master is True:
        capabilities["write.admin.master"] = ENABLED_STATE
    return capabilities


def validate_capabilities(capabilities):
    """Return a normalized copy or raise ``ValueError``.

    Exactly the contract names are required, in contract order, and every state
    must be a bootstrap state. ``enabled`` is accepted only for the reviewed
    patient/appointment writes and the deployment-gated wave writes. Anything
    else fails closed at the DTO boundary.
    """

    if not isinstance(capabilities, dict):
        raise ValueError("invalid capability map")
    if set(capabilities) != set(CAPABILITY_NAMES):
        raise ValueError("invalid capability names")
    for name in CAPABILITY_NAMES:
        state = capabilities[name]
        if state not in BOOTSTRAP_STATES:
            raise ValueError("invalid capability state")
        if state == ENABLED_STATE and name not in ENABLEABLE_NAMES:
            raise ValueError("capability may not be enabled")
    return {name: capabilities[name] for name in CAPABILITY_NAMES}
