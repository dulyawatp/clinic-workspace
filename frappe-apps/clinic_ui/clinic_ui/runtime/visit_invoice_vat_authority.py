"""Server-only No VAT authority for one appointment-backed visit invoice.

The caller supplies only the appointment identifier. The site, the actor and the
appointment classification are server state: the exact synthetic site, the
current session actor matched against the one configured user, and an explicit
literal allowlist. Visit eligibility is re-read through the existing pure
preflight. Any fault, ambiguity or missing positive evidence denies the mode.
"""

from clinic_ui.core.text import is_valid_text
from clinic_ui.runtime.visit_invoice import resolve_visit_invoice

NO_VAT_SITE = "clinic-visit-invoice.local"
NO_VAT_USER_KEY = "clinic_ui_visit_invoice_no_vat_user"
NO_VAT_APPOINTMENTS_KEY = "clinic_ui_visit_invoice_no_vat_appointments"
MAX_NO_VAT_APPOINTMENTS = 64
MAX_APPOINTMENT_ID_LENGTH = 140


def _session_actor(gateway):
    view = gateway.current_session
    actor, sid = view.user, view.sid
    if (type(actor) is not str or not actor.strip() or actor != actor.strip()
            or actor in ("Administrator", "Guest")
            or type(sid) is not str or not sid.strip() or sid != sid.strip()):
        raise ValueError("invalid session")
    return actor


def _classified(config, appointment_id):
    values = config.get(NO_VAT_APPOINTMENTS_KEY)
    if type(values) not in (list, tuple) or not values:
        raise ValueError("invalid appointment classification")
    if len(values) > MAX_NO_VAT_APPOINTMENTS:
        raise ValueError("invalid appointment classification")
    if len(set(values)) != len(values):
        raise ValueError("invalid appointment classification")
    for value in values:
        if (type(value) is not str or value != value.strip() or "*" in value
                or not is_valid_text(value, MAX_APPOINTMENT_ID_LENGTH)):
            raise ValueError("invalid appointment classification")
    return appointment_id in values


def no_vat_allowed(gateway, appointment_id):
    """True only for the configured doctor on one classified, eligible visit."""

    try:
        if (type(appointment_id) is not str
                or appointment_id != appointment_id.strip()
                or not is_valid_text(appointment_id, MAX_APPOINTMENT_ID_LENGTH)):
            return False
        if gateway.site != NO_VAT_SITE:
            return False
        config = gateway._configuration()
        if not isinstance(config, dict):
            return False
        configured = config.get(NO_VAT_USER_KEY)
        if (type(configured) is not str or not configured.strip()
                or configured != configured.strip()
                or configured in ("Administrator", "Guest")
                or configured != _session_actor(gateway)):
            return False
        if not _classified(config, appointment_id):
            return False
        visit = resolve_visit_invoice(gateway, appointment_id)
        return visit is not None and visit.get("appointment_id") == appointment_id
    except Exception:
        return False
