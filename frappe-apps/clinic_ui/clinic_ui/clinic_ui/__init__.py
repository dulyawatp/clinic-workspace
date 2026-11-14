"""Frappe module package for the ``Clinic UI`` module declared in modules.txt.

Frappe reads ``modules.txt``, scrubs each line (``Clinic UI`` -> ``clinic_ui``)
and then imports ``<app>.<module>`` — for example in
``frappe.model.sync.sync_for``, which does::

    folder = os.path.dirname(frappe.get_module(app_name + "." + module_name).__file__)

That import needs this package to exist as a real package (``__init__.py``), not
a namespace package whose ``__file__`` is ``None``. Without it, install and
migrate fail before any of this app's code runs.

This slice ships no DocType, page, report or workspace, so the package is
intentionally empty: it is the module anchor only. Feature code lives in
``clinic_ui.core``, ``clinic_ui.endpoints``, ``clinic_ui.api``, ``clinic_ui.runtime``
and ``clinic_ui.www``; doctype folders belong here when the module gains one.
"""
