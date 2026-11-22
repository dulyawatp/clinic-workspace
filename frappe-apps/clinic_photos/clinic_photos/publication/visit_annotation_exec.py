"""Unwired private launcher for the nonpublishing drawing child."""

from clinic_photos.publication import launcher

_CHILD_MODULE = "clinic_photos.publication.visit_annotation_process"


def _launch(operation_id):
    """Use only server-selected identity and fixed runner configuration."""
    if not launcher._valid_operation_id(operation_id):
        return None
    try:
        frappe = launcher._frappe()
        site = launcher._visit_server_site(frappe)
        if frappe.conf.get("clinic_visit_photo_annotations_enabled") is not True:
            return None
        launcher._execute_child([
            launcher.PYTHON_EXECUTABLE, "-m", _CHILD_MODULE,
            "--site", site, "--operation-id", operation_id,
        ])
    except Exception:
        return None
    # Exit status and bounded output are never evidence of a saved result.
    return None
