"""The capability map published by the bootstrap endpoint.

Names and order mirror ``contracts/capabilities.json`` exactly; the parity test
fails if the client contract and this list drift. Every state is ``read_only``:
the first slice enables no write and no blocked/denied claim is implied.
"""

CAPABILITY_NAMES = (
    "write.patient.create",
    "write.patient.update",
    "write.appointment.book",
    "write.appointment.reschedule",
    "write.appointment.cancel",
    "write.appointment.checkin",
    "write.encounter.draft",
    "write.encounter.complete",
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
    "write.purchase_order.draft",
    "write.purchase_order.submit",
    "write.invoice.submit",
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


def read_only_capabilities():
    """Return a fresh mapping of every capability to ``read_only``."""

    return {name: READ_ONLY_STATE for name in CAPABILITY_NAMES}
