"""Private SIT deployment proof and transaction-local one-payment slot."""

import hmac
import os

from clinic_ui.core import deployment_marker, finance_contract
from clinic_ui.core.text import is_valid_text

SITE = "marley-sit.local"
DEPLOYMENT_ID = "clinic-shared-sit-payment-v1"
PROOF_DIRECTORY = "clinic_ui_sit_payment_proof"
TABLE = "`tabClinic UI SIT Payment Slot`"
PROFILE_SETTING = "clinic_ui_payment_deployment_profile"


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


def payment_lane(gateway):
    """Select from operator-owned site_config.json, never request input.

    The trusted operator must protect the site configuration from untrusted
    modification independently of the private deployment proof.
    """
    if gateway.site != SITE:
        return "legacy"
    try:
        profile = gateway.setting(PROFILE_SETTING)
        if profile == "legacy":
            return "legacy"
        if (
            profile == "shared_slot_v1"
            and shared_sit_payment_identity(gateway) == DEPLOYMENT_ID
        ):
            return "shared_slot_v1"
    except Exception:
        pass
    return "blocked"


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
    row = rows[0]
    metadata = (row["actor"], row["operation_key"], row["receipt"])
    if row["state"] == "spent" and any(value is not None for value in metadata):
        _validate_metadata(*metadata)
    return row


def _validate_metadata(actor, operation_key, receipt):
    try:
        if not is_valid_text(actor, finance_contract.MAX_ACTOR_LENGTH):
            raise finance_contract.FinanceContractError("invalid actor")
        finance_contract.require_operation_key(operation_key)
        if finance_contract.decode_receipt(receipt).kind != finance_contract.PAYMENT_KIND:
            raise finance_contract.FinanceContractError("invalid payment receipt")
    except (finance_contract.FinanceContractError, TypeError, ValueError) as exc:
        raise SlotUnavailable("payment slot metadata is invalid") from exc


def read_slot(gateway, deployment_id):
    return _read(gateway, deployment_id, False)


def verify_slot_ack(gateway, deployment_id, secret, actor, operation_key, token):
    """Return acked, invalid, or not_owned; unreadable slot raises SlotUnavailable.

    Only not_owned permits a separately authorized historical lookup. A match
    by operation key is owned even when its actor or receipt is invalid.
    """
    try:
        row = read_slot(gateway, deployment_id)
    except SlotUnavailable:
        raise
    except Exception as exc:
        raise SlotUnavailable("payment slot is unreadable") from exc
    if row["state"] != "spent" or row["operation_key"] != operation_key:
        return "not_owned"
    if row["actor"] != actor or not finance_contract.is_valid_ack_token(token):
        return "invalid"
    try:
        receipt = row["receipt"]
        if finance_contract.decode_receipt(receipt).kind != finance_contract.PAYMENT_KIND:
            return "invalid"
        expected = finance_contract.ack_token(
            secret, gateway.site, actor, finance_contract.PAYMENT_KIND,
            operation_key, receipt,
        )
    except (finance_contract.FinanceContractError, TypeError, ValueError):
        return "invalid"
    return "acked" if hmac.compare_digest(expected, token) else "invalid"


def lock_slot(gateway, deployment_id):
    row = _read(gateway, deployment_id, True)
    if row["state"] != "available":
        raise SlotUnavailable("payment slot is spent")
    return row


def consume_slot(gateway, deployment_id, actor, operation_key, receipt):
    # Caller must acquire this lock before invoice/ledger locks and posting.
    # Reacquire on the same connection to ensure the update itself is locked.
    lock_slot(gateway, deployment_id)
    _validate_metadata(actor, operation_key, receipt)
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
    stored = read_slot(gateway, deployment_id)
    if (
        stored["state"] != "spent"
        or stored["actor"] != actor
        or stored["operation_key"] != operation_key
        or stored["receipt"] != receipt
    ):
        raise SlotUnavailable("payment slot did not retain exact receipt")
