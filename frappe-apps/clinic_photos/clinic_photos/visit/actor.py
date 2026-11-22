"""Fresh native role checks solely for visit-photo actions."""


def require_visit_actor(*, doctor=False, for_update=True):
    """Check fresh native roles; writers lock User then Has Role rows."""
    import frappe

    user = frappe.session.user
    if not isinstance(user, str) or not user or user in ("Guest", "Administrator"):
        frappe.throw("Not permitted", frappe.PermissionError)
    enabled = frappe.db.get_value("User", user, "enabled", for_update=for_update)
    if enabled != 1:
        frappe.throw("Not permitted", frappe.PermissionError)
    rows = frappe.db.sql(
        """select role from `tabHas Role`
           where parent = %s and parenttype = 'User'
           order by name""" + (" for update" if for_update else ""),
        (user,),
    )
    roles = {row[0] for row in rows}
    allowed = {"Physician"} if doctor else {"Physician", "Healthcare Administrator", "Receptionist"}
    if not roles.intersection(allowed):
        frappe.throw("Not permitted", frappe.PermissionError)
    return user
