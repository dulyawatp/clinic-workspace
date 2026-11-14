"""Framework-free package init for the read-only clinic endpoints."""

from clinic_ui.endpoints.directory import synthetic_patients
from clinic_ui.endpoints.session import bootstrap

__all__ = ["bootstrap", "synthetic_patients"]
