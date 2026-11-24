"""Explicit, private Bench entry point for provisioning a named Clinic Owner."""

import frappe
from clinic_ui.runtime.frappe_gateway import Gateway


ROLE = "Clinic Owner Practitioner Editor"
CREATOR_ROLE = "Clinic Owner Practitioner Creator"
PRACTITIONER = "Healthcare Practitioner"
OWNER_ROLES = {"System Manager", "Healthcare Administrator", ROLE}
PROVISIONED_ROLES = OWNER_ROLES | {CREATOR_ROLE}


def _refuse(message):
    raise ValueError(message)


def _role_references(role_name, existing):
    """Permit only the exact permission and compatible User assignments."""
    for metadata in ("DocField", "Custom Field"):
        fields = frappe.get_all(
            metadata,
            filters={"fieldtype": ["in", ["Link", "Dynamic Link"]]},
            fields=["parent" if metadata == "DocField" else "dt", "fieldname", "fieldtype", "options"],
        )
        for field in fields:
            parent = field.parent if metadata == "DocField" else field.dt
            if field.fieldtype == "Link" and field.options != "Role":
                continue
            if field.fieldtype == "Dynamic Link" and not field.options:
                _refuse("Cannot inspect incomplete Dynamic Link metadata")
            if not frappe.db.table_exists(parent):
                continue
            if not frappe.db.has_column(parent, field.fieldname):
                _refuse("Cannot inspect role reference metadata")
            filters = {field.fieldname: role_name}
            if field.fieldtype == "Dynamic Link":
                if not frappe.db.has_column(parent, field.options):
                    _refuse("Cannot inspect Dynamic Link metadata")
                filters[field.options] = "Role"
            reference_fields = ["name"]
            if parent == "Has Role":
                reference_fields += ["parent", "parenttype", "parentfield"]
            elif parent == "Custom DocPerm":
                reference_fields += ["parent"]
            references = frappe.get_all(parent, filters=filters, fields=reference_fields)
            if not references:
                continue
            if existing and parent == "Custom DocPerm" and field.fieldname == "role":
                if all(row.parent == PRACTITIONER for row in references):
                    continue
            if existing and parent == "Has Role" and field.fieldname == "role":
                for row in references:
                    if row.parenttype != "User" or row.parentfield != "roles":
                        _refuse("Reserved role has a foreign assignment")
                    assigned = frappe.get_doc("User", row.parent)
                    if (not assigned.enabled or assigned.user_type != "System User"
                            or assigned.get("role_profile_name") or assigned.get("role_profiles")
                            or not OWNER_ROLES.issubset({r.role for r in assigned.roles})):
                        _refuse("Reserved role has an incompatible User assignment")
                continue
            _refuse("Reserved role has foreign references")


def _permission_fields(source):
    standard = frappe.get_meta("DocPerm")
    custom = frappe.get_meta("Custom DocPerm")
    standard_checks = {field.fieldname for field in standard.fields if field.fieldtype == "Check"}
    custom_checks = {field.fieldname for field in custom.fields if field.fieldtype == "Check"}
    if not standard_checks.issubset(custom_checks):
        _refuse("Custom permissions cannot preserve all stock permission flags")
    # Custom DocPerm is standalone: parenttype and parentfield do not exist.
    return sorted(
        {"role", "permlevel", "if_owner", "parent"}
        | (custom_checks if source == "Custom DocPerm" else standard_checks)
    )


def _snapshot(doctype, fields):
    rows = frappe.get_all(doctype, filters={"parent": PRACTITIONER}, fields=fields)
    return sorted(tuple(str(row.get(field) or "") for field in fields) for row in rows)


def provision_owner(user):
    """Run explicitly with ``bench --site SITE execute ... --args '["user"]'``."""
    gateway = Gateway(frappe)
    if frappe.session.user != "Administrator":
        _refuse("This private deployment command requires the Bench Administrator session")
    if not isinstance(user, str) or not user or user != user.strip():
        _refuse("Provide an explicit existing User name")
    if user.lower() in {"guest", "administrator"} or not frappe.db.exists("User", user):
        _refuse("Owner must be an existing named User")
    owner = frappe.get_doc("User", user)
    if not owner.enabled or owner.user_type != "System User":
        _refuse("Owner must be an enabled System User")
    if owner.get("role_profile_name") or owner.get("role_profiles"):
        _refuse("User role profiles would replace independently assigned roles")
    original_roles = {row.role for row in owner.roles}
    for role in OWNER_ROLES - {ROLE}:
        if not frappe.db.exists("Role", role) or frappe.db.get_value("Role", role, "disabled"):
            _refuse("Required native role missing or disabled: " + role)
    custom_exists = frappe.db.exists("Custom DocPerm", {"parent": PRACTITIONER})
    source = "Custom DocPerm" if custom_exists else "DocPerm"
    fields = _permission_fields(source)
    if not custom_exists:
        stock_rows = frappe.get_all(
            "DocPerm", filters={"parent": PRACTITIONER}, fields=["parenttype", "parentfield"],
        )
        if any(row.parenttype != "DocType" or row.parentfield != "permissions" for row in stock_rows):
            _refuse("Stock practitioner permission rows have incompatible ownership")
    before = _snapshot(source, fields)
    if not before:
        _refuse("No existing practitioner permissions to preserve")

    empty_checks = {
        field.fieldname: 0
        for field in frappe.get_meta("Custom DocPerm").fields
        if field.fieldtype == "Check"
    }
    expected = {
        ROLE: {**empty_checks, "read": 1, "write": 1},
        CREATOR_ROLE: {**empty_checks, "create": 1},
    }
    existing = {}
    for role_name, checks in expected.items():
        existing[role_name] = bool(frappe.db.exists("Role", role_name))
        _role_references(role_name, existing[role_name])
        if not existing[role_name]:
            continue
        role = frappe.get_doc("Role", role_name)
        if (role.role_name != role_name or not role.is_custom or role.disabled or role.desk_access
                or role.home_page or role.restrict_to_domain or role.two_factor_auth):
            _refuse("Reserved role metadata conflicts with deployment")
        if any(role.get(field.fieldname) for field in role.meta.fields if field.get("is_custom_field")):
            _refuse("Reserved role has custom deployment metadata")
        rows = frappe.get_all(
            "Custom DocPerm", filters={"role": role_name},
            fields=sorted(set(fields) | set(checks)),
        )
        if (len(rows) != 1 or rows[0].parent != PRACTITIONER
                or int(rows[0].permlevel or 0) != 0
                or any(int(rows[0].get(key) or 0) != value for key, value in checks.items())):
            _refuse("Reserved role permissions conflict with deployment")
        custom_fields = [
            field.fieldname for field in frappe.get_meta("Custom DocPerm").fields
            if field.get("is_custom_field")
        ]
        if custom_fields and any(
            frappe.get_doc("Custom DocPerm", frappe.db.get_value(
                "Custom DocPerm", {"role": role_name}, "name"
            )).get(field) for field in custom_fields
        ):
            _refuse("Reserved permission has custom deployment metadata")
    if all(existing.values()) and PROVISIONED_ROLES.issubset(original_roles):
        return {"status": "unchanged"}

    try:
        for role_name, checks in expected.items():
            if existing[role_name]:
                continue
            gateway.owner_deployment_insert_role(role_name)
            if not custom_exists:
                gateway.owner_deployment_copy_permissions(PRACTITIONER)
                custom_exists = True
            if _snapshot("Custom DocPerm", fields) != before:
                _refuse("Native permission copy did not preserve practitioner grants")
            gateway.owner_deployment_insert_permission(PRACTITIONER, role_name, checks)
            # Validate effective custom rows without the stock DocType validator's db_update.
            gateway.owner_deployment_validate_permissions(PRACTITIONER)
            rows = frappe.get_all(
                "Custom DocPerm", filters={"parent": PRACTITIONER, "role": role_name},
                fields=sorted(set(fields) | set(checks)),
            )
            if len(rows) != 1 or any(
                int(rows[0].get(key) or 0) != value for key, value in checks.items()
            ):
                _refuse("Dedicated permission row differs from expected grants")
            after = _snapshot("Custom DocPerm", fields)
            added = tuple(str(rows[0].get(field) or "") for field in fields)
            after.remove(added)
            if after != before:
                _refuse("Existing practitioner permissions changed")
            before = _snapshot("Custom DocPerm", fields)
            gateway.owner_deployment_clear_permissions_cache(PRACTITIONER)

        gateway.owner_deployment_save_roles(owner, sorted(PROVISIONED_ROLES - original_roles))
        owner.reload()
        if (not owner.enabled or owner.user_type != "System User"
                or owner.get("role_profile_name") or owner.get("role_profiles")
                or {row.role for row in owner.roles} != original_roles | PROVISIONED_ROLES):
            _refuse("Native User save changed or failed to persist roles")
        return {"status": "provisioned"}
    except Exception:
        frappe.db.rollback()
        frappe.clear_cache()
        raise
