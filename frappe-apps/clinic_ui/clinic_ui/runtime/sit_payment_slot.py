"""Private SIT deployment proof and transaction-local one-payment slot."""

import os

from clinic_ui.core import deployment_marker

SITE = "marley-sit.local"
DEPLOYMENT_ID = "clinic-shared-sit-payment-v1"
PROOF_DIRECTORY = "clinic_ui_sit_payment_proof"
TABLE = "`tabClinic UI SIT Payment Slot`"


class SlotUnavailable(Exception):
    """The deployment, slot, or reconciliation cannot safely admit payment."""


def shared_sit_payment_identity(gateway):
    if gateway.site != SITE:
        return None
    try:
        private = gateway.frappe.get_site_path("private")
        proof = os.path.join(private, PROOF_DIRECTORY)
        deployment_marker.read_marker(proof, SITE, DEPLOYMENT_ID)
    except (deployment_marker.MarkerError, OSError, TypeError):
        return None
    return DEPLOYMENT_ID


def initial_state(history):
    """Translate an operator-confirmed reconciliation outcome; never infer it."""
    if history == "confirmed_payment":
        return "spent"
    if history == "confirmed_empty":
        return "available"
    raise SlotUnavailable("legacy payment history is unresolved")


def _read(gateway, deployment_id, lock):
    if deployment_id != DEPLOYMENT_ID or shared_sit_payment_identity(gateway) != deployment_id:
        raise SlotUnavailable("deployment identity is not approved")
    rows = gateway.frappe.db.sql(
        f"SELECT deployment_id, state, actor, operation_key, receipt FROM {TABLE} "
        "WHERE BINARY deployment_id = BINARY %s" + (" FOR UPDATE" if lock else ""),
        (deployment_id,),
        as_dict=True,
    )
    if (
        len(rows) != 1
        or rows[0]["deployment_id"] != deployment_id
        or rows[0]["state"] not in ("available", "spent")
        or (rows[0]["state"] == "available" and any(
            rows[0][field] is not None for field in ("actor", "operation_key", "receipt")
        ))
    ):
        raise SlotUnavailable("payment slot is missing or invalid")
    return rows[0]


def read_slot(gateway, deployment_id):
    return _read(gateway, deployment_id, False)


def lock_slot(gateway, deployment_id):
    row = _read(gateway, deployment_id, True)
    if row["state"] != "available":
        raise SlotUnavailable("payment slot is spent")
    return row


def consume_slot(gateway, deployment_id, actor, operation_key, receipt):
    # Caller must acquire this lock before invoice/ledger locks and posting.
    # Reacquire on the same connection to ensure the update itself is locked.
    lock_slot(gateway, deployment_id)
    if not all(isinstance(value, str) and value.strip() for value in (actor, operation_key, receipt)):
        raise SlotUnavailable("payment slot receipt is invalid")
    db = gateway.frappe.db
    db.sql(
        f"UPDATE {TABLE} SET state = 'spent', actor = %s, operation_key = %s, receipt = %s "
        "WHERE BINARY deployment_id = BINARY %s AND state = 'available' "
        "AND actor IS NULL AND operation_key IS NULL AND receipt IS NULL",
        (actor, operation_key, receipt, deployment_id),
    )
    rows = db.sql("SELECT ROW_COUNT()")
    if len(rows) != 1 or len(rows[0]) != 1 or type(rows[0][0]) is not int or rows[0][0] != 1:
        raise SlotUnavailable("payment slot was not consumed")
