"""Permission-filtered exact visit invoice reference; never evidence of absence."""

from clinic_ui.core.text import is_valid_text
from clinic_ui.runtime.visit_invoice_native import _schema


_FIELDS = {
    "Patient Appointment": {
        "practitioner": ("Link", "Healthcare Practitioner"),
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "invoiced": ("Check", None), "ref_sales_invoice": ("Link", "Sales Invoice"),
    },
    "Healthcare Practitioner": {"user_id": ("Link", "User"), "status": ("Select", None)},
    "Sales Invoice": {
        "patient": ("Link", "Patient"), "company": ("Link", "Company"),
        "items": ("Table", "Sales Invoice Item"),
    },
    "Sales Invoice Item": {
        "reference_dt": ("Link", "DocType"), "reference_dn": ("Dynamic Link", "reference_dt"),
    },
}


def _text(value):
    return is_valid_text(value, 140) and value == value.strip()


def _read(gateway, doctype, name, user):
    if not _text(name):
        return None
    frappe = gateway.frappe
    meta = frappe.get_meta(doctype)
    fields = _FIELDS[doctype]
    if (not _schema(meta, fields) or not gateway._fields_readable(meta, fields)
            or not frappe.has_permission(doctype, ptype="read", doc=name, user=user)):
        return None
    doc = frappe.get_doc(doctype, name)
    return doc if doc.name == name else None


def _items_readable(frappe, user):
    meta = frappe.get_meta("Sales Invoice Item")
    fields = _FIELDS["Sales Invoice Item"]
    if not _schema(meta, fields):
        return False
    levels = set(meta.get_permlevel_access("read", parenttype="Sales Invoice", user=user))
    masked = {field.fieldname for field in meta.get_masked_fields(parenttype="Sales Invoice")}
    return all(meta.get_field(name).permlevel in levels and name not in masked for name in fields)


def lookup_visit_invoice(gateway, appointment_id, expected_invoice_id=None):
    """Read only the exact submitted link; any unavailable evidence yields None."""
    try:
        frappe = gateway.frappe
        user = frappe.session.user
        if not _text(user) or user in ("Guest", "Administrator"):
            return None
        if expected_invoice_id is not None and not _text(expected_invoice_id):
            return None
        appointment = _read(gateway, "Patient Appointment", appointment_id, user)
        if (appointment is None or type(appointment.invoiced) is not int
                or appointment.invoiced != 1 or not _text(appointment.ref_sales_invoice)
                or not _text(appointment.patient) or not _text(appointment.company)
                or (expected_invoice_id is not None
                    and appointment.ref_sales_invoice != expected_invoice_id)):
            return None
        practitioner = _read(gateway, "Healthcare Practitioner", appointment.practitioner, user)
        if practitioner is None or practitioner.user_id != user or practitioner.status != "Active":
            return None
        identities = frappe.get_all(
            "Healthcare Practitioner", filters={"user_id": user, "status": "Active"},
            pluck="name", limit_page_length=2,
        )
        if identities != [appointment.practitioner]:
            return None
        invoice = _read(gateway, "Sales Invoice", appointment.ref_sales_invoice, user)
        if (invoice is None or not isinstance(invoice.docstatus, int)
                or isinstance(invoice.docstatus, bool) or invoice.docstatus != 1
                or invoice.patient != appointment.patient or invoice.company != appointment.company
                or not _items_readable(frappe, user) or len(invoice.items) != 1):
            return None
        item = invoice.items[0]
        if (item.parent != invoice.name or item.parenttype != "Sales Invoice"
                or item.parentfield != "items" or item.reference_dt != "Patient Appointment"
                or item.reference_dn != appointment_id):
            return None
        return {"invoice_id": invoice.name}
    except Exception:
        return None
