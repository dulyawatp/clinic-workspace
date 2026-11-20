"""Unwired visit-only slot claim. No publication or byte storage authority."""

from contextvars import ContextVar
from hashlib import sha256

from clinic_photos.access.visit_contract import slot_identity

_binding = ContextVar("clinic_visit_photo_claim", default=None)
_FIELDS = ("visit_slot_key", "site", "actor", "appointment", "patient",
           "consent", "photo_type", "request_key", "payload_sha256")
_MESSAGE = "Visit photo claim denied."


def _deny():
    import frappe
    frappe.throw(_MESSAGE, frappe.PermissionError)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 140


def _authorize(appointment, slot):
    import frappe

    actor = frappe.session.user
    site = getattr(frappe.local, "site", None)
    if not _text(actor) or actor in ("Guest", "Administrator") or not _text(site):
        _deny()
    if not frappe.db.get_value("User", actor, "enabled"):
        _deny()
    role_map = frappe.conf.get("clinic_ui_role_map")
    if not isinstance(role_map, dict) or not role_map:
        _deny()
    roles = set(frappe.get_all("Has Role", filters={"parent": actor, "parenttype": "User"},
                               pluck="role"))
    matches = []
    seen = set()
    for code, mapped in role_map.items():
        if (code not in {"DOC", "REC", "NUR", "PHA", "CAS", "MGR", "ADM"}
                or not isinstance(mapped, (list, tuple)) or not mapped
                or any(not _text(role) or role in {"All", "Guest", "Desk User", "System User"}
                       or role in seen for role in mapped)):
            _deny()
        seen.update(mapped)
        if roles.intersection(mapped):
            matches.append(code)
    if matches not in (["DOC"], ["REC"]) or slot != "Before":
        _deny()

    visit = frappe.get_doc("Patient Appointment", appointment)
    if (not frappe.has_permission("Patient Appointment", "read", doc=visit)
            or not frappe.has_permission("Patient Appointment", "write", doc=visit)
            or not _text(visit.get("patient"))):
        _deny()
    patient = frappe.get_doc("Patient", visit.get("patient"))
    if not frappe.has_permission("Patient", "read", doc=patient):
        _deny()
    consent_name = frappe.db.get_value("Clinic Visit Photo Consent", {"appointment": appointment}, "name")
    if not _text(consent_name):
        _deny()
    consent = frappe.get_doc("Clinic Visit Photo Consent", consent_name)
    if (not frappe.has_permission("Clinic Visit Photo Consent", "read", doc=consent)
            or consent.get("appointment") != appointment
            or consent.get("patient") != patient.name
            or consent.get("revoked")
            or consent.get("revoked_at")
            or not all(consent.get(k) for k in ("granted_by", "granted_at", "evidence"))):
        _deny()
    return actor, site, patient.name, consent.name


def require_bound_insert(doc):
    """Controller gate: only the exact live document and server-derived row."""
    import frappe
    binding = _binding.get()
    if (binding is None or binding[0] is not doc
            or doc.get("doctype") != "Clinic Visit Photo Slot Claim"
            or frappe.session.user != binding[1]["actor"]
            or getattr(frappe.local, "site", None) != binding[1]["site"]
            or any(doc.get(field) != binding[1][field] for field in _FIELDS)):
        _deny()


def claim(appointment, slot, request_key, image_bytes):
    """Insert one immutable claim under the current actor's native permissions.

    Caller owns transaction and must commit before any subsequent byte write.
    A duplicate raises the native unique error; no replay/status API is exposed.
    """
    import frappe
    if (not _text(appointment) or not _text(request_key)
            or not isinstance(image_bytes, bytes) or not image_bytes):
        _deny()
    actor, site, patient, consent = _authorize(appointment, slot)
    values = dict(visit_slot_key=slot_identity(appointment, slot), site=site, actor=actor,
                  appointment=appointment, patient=patient, consent=consent,
                  photo_type=slot, request_key=request_key,
                  payload_sha256=sha256(image_bytes).hexdigest())
    doc = frappe.get_doc({"doctype": "Clinic Visit Photo Slot Claim", **values})
    if _binding.get() is not None:
        _deny()
    token = _binding.set((doc, values))
    try:
        doc.insert()
    finally:
        _binding.reset(token)
