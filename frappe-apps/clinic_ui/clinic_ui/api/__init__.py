"""Whitelisted read-only endpoints (``clinic_ui.api``).

Each entry module imports Frappe at module scope and stays GET-only. The
framework-free decisions live in ``clinic_ui.endpoints``; the seam that reads
the current site and session lives in ``clinic_ui.runtime.frappe_gateway``.
"""
