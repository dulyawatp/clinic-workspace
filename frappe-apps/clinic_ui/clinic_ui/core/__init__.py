"""Framework-independent policy, configuration and DTO layer.

Nothing in this package imports Frappe. The adapter in
``clinic_ui.runtime.frappe_gateway`` supplies the session facts, the site
configuration getter, the CSRF token and the native per-patient read check.
"""
