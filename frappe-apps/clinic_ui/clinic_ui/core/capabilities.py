"""The capability map published by the bootstrap endpoint.

Names and order mirror ``contracts/capabilities.json`` exactly; the parity test
fails if the client contract and this list drift. The default map is
``read_only``. Only the reviewed patient create/edit, the five appointment
writes (book, reschedule, cancel, no-show, check-in) and the deployment-gated
writes added by the full write-contract wave may be promoted to ``enabled``,
and only after the bootstrap endpoint has verified the whole reviewed local
write gate for that surface. Every other capability stays ``read_only``.

The encounter draft, completion and amendment family and the native lab
ordering that rides the draft payload are **deferred**: appointment lifecycle
provenance and authorization are under review (a pinned native draft save
closes its appointment, while completion requires it to stay open or checked
in), so nothing here promotes them and no gate value can. The ids stay in the
enableable set because that set is the client's frozen enableable list and the
lockstep parity test asserts the two are identical; the refusal is in the
promotion decision below and in the endpoint boundary.
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
    "write.procedure.create",
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
    "write.visit_invoice.submit",
    "write.visit_invoice.discount",
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
        "write.procedure.create",
        "write.invoice.void_amend",
        "write.stock.consume",
        "write.stock.receive",
        "write.stock.transfer",
        "write.stock.adjust",
        "write.purchase_order.draft",
        "write.purchase_order.submit",
        "write.invoice.submit",
        "write.visit_invoice.submit",
        "write.visit_invoice.discount",
        "write.payment.receive",
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
    write_procedure_create=False,
    write_stock_receive=False,
    write_stock_transfer=False,
    write_stock_adjust=False,
    write_purchase_order_draft=False,
    write_purchase_order_submit=False,
    write_invoice_submit=False,
    write_visit_invoice_submit=False,
    write_visit_invoice_discount=False,
    write_payment_receive=False,
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
    # The encounter draft, completion and amendment family, and the native lab
    # ordering that rides the draft payload, are deferred pending the
    # appointment lifecycle provenance and authorization review: a pinned native
    # draft save closes its appointment while completion requires it to stay
    # open or checked in. ``write.encounter.draft``, ``write.encounter.complete``,
    # ``write.encounter.amend`` and ``write.lab.order`` therefore stay
    # ``read_only`` whatever the gate says. The flags are still accepted so
    # bootstrap keeps reporting the reviewed gate state; they are not consulted.
    #
    # Medication ordering is not advertised either: the draft contract refuses
    # any non-empty ``medications`` section before the fence until a reviewed
    # medication contract exists (the installed ``Drug Prescription.dosage_form``
    # is required and unsupplied).
    #
    # The converted procedure lane refuses stock-consuming procedures, so the
    # procedure gate enables completion only. ``write.stock.consume`` stays
    # ``read_only``: no reviewed workflow drives it from this surface.
    if write_procedure_complete is True:
        capabilities["write.procedure.complete"] = ENABLED_STATE
    # Creating the Clinical Procedure is the family's one creation write
    # (Amendment A1); it rides the same site-config gate as completion.
    if write_procedure_create is True:
        capabilities["write.procedure.create"] = ENABLED_STATE
    # Stock receive/transfer/adjust are three contract surfaces under one
    # site-config gate; bootstrap passes each only after the gate passes.
    if write_stock_receive is True:
        capabilities["write.stock.receive"] = ENABLED_STATE
    if write_stock_transfer is True:
        capabilities["write.stock.transfer"] = ENABLED_STATE
    if write_stock_adjust is True:
        capabilities["write.stock.adjust"] = ENABLED_STATE
    # Purchase-order draft and submit are two contract surfaces under one
    # site-config gate; the PO-referenced receipt is the stock receipt surface
    # (the one receipt write), advertised through ``write.stock.receive``.
    if write_purchase_order_draft is True:
        capabilities["write.purchase_order.draft"] = ENABLED_STATE
    if write_purchase_order_submit is True:
        capabilities["write.purchase_order.submit"] = ENABLED_STATE
    # Invoice submit and payment receive are two independent contract surfaces
    # that share one site-config finance gate; bootstrap passes each only after
    # that gate passes. Void/amend and refund stay read_only (both cut).
    if write_invoice_submit is True:
        capabilities["write.invoice.submit"] = ENABLED_STATE
    if write_visit_invoice_submit is True:
        capabilities["write.visit_invoice.submit"] = ENABLED_STATE
        if write_visit_invoice_discount is True:
            capabilities["write.visit_invoice.discount"] = ENABLED_STATE
    if write_payment_receive is True:
        capabilities["write.payment.receive"] = ENABLED_STATE
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
