"""Read native identity under transaction locks; caller locks appointment first."""

def current_identity(appointment, *, skip_denied=False):
    import frappe

    if not appointment.name or not appointment.patient or not appointment.creation:
        frappe.throw("Incomplete visit identity", frappe.ValidationError)
    if not frappe.has_permission("Patient", "read", doc=appointment.patient, throw=False):
        if skip_denied:
            return None
        frappe.throw("Not permitted", frappe.PermissionError)
    patient = frappe.get_doc("Patient", appointment.patient, for_update=True)
    if patient.name != appointment.patient or not patient.creation:
        frappe.throw("Inconsistent visit identity", frappe.ValidationError)
    if not frappe.has_permission("Patient", "read", doc=patient, throw=False):
        if skip_denied:
            return None
        frappe.throw("Not permitted", frappe.PermissionError)
    return str(appointment.creation), str(patient.creation)
