"""Pure preflight over trusted, permission-checked gateway evidence.

No native gateway adapter or invoice write path exists here.
"""

from decimal import Decimal


def visit_invoice_write_enabled(gateway=None, actor=None):
    try:
        config = gateway._configuration()
        enabled = config.get("clinic_ui_visit_invoice_write_enabled")
        mysql = config.get("use_mysqlclient")
        configured = config.get("clinic_ui_visit_invoice_write_user")
        key = gateway.visit_invoice_token_key()
        return (gateway.site == "clinic-visit-invoice.local"
                and type(enabled) in (int, str) and enabled in (1, "1")
                and type(mysql) in (int, str) and mysql in (0, "0")
                and type(actor) is str and bool(actor) and actor == actor.strip()
                and actor not in ("Administrator", "Guest")
                and type(configured) is str and configured == actor
                and type(key) is bytes and len(key) >= 32)
    except Exception:
        return False


def resolve_visit_invoice(gateway, appointment_id):
    if not isinstance(appointment_id, str) or not appointment_id.strip():
        return None
    try:
        evidence = gateway.visit_invoice_evidence(appointment_id)
        if not isinstance(evidence, dict):
            return None
        if any(evidence.get(key) is not True for key in (
            "metadata_verified", "record_fields_readable", "billing_history_complete"
        )):
            return None
        if evidence.get("prior_billing") is not False:
            return None
        if evidence.get("appointment_id") != appointment_id:
            return None
        if (not evidence.get("practitioner")
                or evidence.get("practitioner") != evidence.get("actor_practitioner")):
            return None
        if (evidence.get("status") not in ("Open", "Closed")
                or type(evidence.get("docstatus")) is not int or evidence["docstatus"] != 0):
            return None
        encounter_id = evidence.get("encounter_id")
        if evidence["status"] == "Closed":
            if not isinstance(encounter_id, str) or not encounter_id.strip():
                return None
        elif encounter_id is not None:
            return None
        if (type(evidence.get("invoiced")) is not int or evidence["invoiced"] != 0
                or "ref_sales_invoice" not in evidence
                or evidence["ref_sales_invoice"] not in (None, "")):
            return None
        keys = ("appointment_id", "practitioner", "patient", "customer", "company", "currency", "service")
        if any(not isinstance(evidence.get(key), str) or not evidence[key].strip() for key in keys):
            return None
        price = evidence.get("price")
        if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
            return None
        result = {**{key: evidence[key] for key in keys}, "price": price}
        if encounter_id:
            result["encounter_id"] = encounter_id
        return result
    except Exception:
        return None
