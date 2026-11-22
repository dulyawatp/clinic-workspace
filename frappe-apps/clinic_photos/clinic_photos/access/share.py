"""Deny new DocShare access to visit metadata; deletion remains native."""

from clinic_photos.access.permissions import _contains_visit_marker
from clinic_photos.access.draft_identity import is_reserved_visit_identity


def _managed_photo(name):
    import frappe

    if not name:
        return False
    row = frappe.db.get_value(
        "Clinic Photo", name, ["storage_key", "original_file"], as_dict=True
    ) or {}
    if _contains_visit_marker(row.get("storage_key")):
        return True
    file_name = row.get("original_file")
    if file_name and _contains_visit_marker(frappe.db.get_value("File", file_name, "file_url")):
        return True
    return bool(frappe.db.get_all(
        "Clinic Visit Photo Slot", filters={"photo": name}, pluck="name", limit=1
    ))


def _managed_target(doctype, name):
    import frappe

    if doctype == "Clinic Photo":
        return _managed_photo(name)
    if doctype == "Clinic Photo Draft":
        return bool(is_reserved_visit_identity(name) or
                    (name and is_reserved_visit_identity(
                        frappe.db.get_value(doctype, name, "name"))))
    if doctype == "Clinic Photo Annotation Version":
        if is_reserved_visit_identity(name):
            return True
        row = frappe.db.get_value(doctype, name, ["name", "draft"], as_dict=True) if name else None
        return bool(row and (is_reserved_visit_identity(row.get("name")) or
                             is_reserved_visit_identity(row.get("draft"))))
    if doctype != "File" or not name:
        return False
    from clinic_photos.access.frappe_context import is_managed_annotation_file

    row = frappe.db.get_value(
        "File", name, ["name", "file_url", "attached_to_doctype", "attached_to_name"], as_dict=True
    ) or {}
    if is_managed_annotation_file({"name": name}, persisted=row):
        return True
    if _contains_visit_marker(row.get("file_url")):
        return True
    if (row.get("attached_to_doctype") == "Clinic Photo" and
        _managed_photo(row.get("attached_to_name"))):
        return True
    return any(_managed_photo(photo) for photo in frappe.db.get_all(
        "Clinic Photo", filters={"original_file": name}, pluck="name", limit=0
    ))


def _is_reduction(doc, previous):
    if previous is None:
        return False
    old, new = previous.as_dict(), doc.as_dict()
    rights = ("read", "write", "submit", "share")
    reduced = False
    for right in rights:
        before, after = old.pop(right, 0) or 0, new.pop(right, 0) or 0
        if before not in (0, 1) or after not in (0, 1) or after > before:
            return False
        reduced |= after < before
    for key in ("modified", "modified_by"):
        old.pop(key, None)
        new.pop(key, None)
    return reduced and old == new


def validate_docshare(doc, method=None):
    """Reject grants and edits; allow strictly reduced rights on existing shares."""
    import frappe

    previous = doc.get_doc_before_save()
    if (_managed_target(doc.share_doctype, doc.share_name) or
        (previous is not None and _managed_target(previous.share_doctype, previous.share_name))):
        if not _is_reduction(doc, previous):
            raise frappe.PermissionError("Sharing visit photo metadata is not permitted")


def assert_no_managed_shares():
    """Preactivation check; raises if any existing managed share needs review."""
    import frappe

    for row in frappe.db.get_all(
        "DocShare", filters={"share_doctype": ["in", [
            "File", "Clinic Photo", "Clinic Photo Draft", "Clinic Photo Annotation Version"
        ]]},
        fields=["share_doctype", "share_name"], limit=0,
    ):
        if _managed_target(row.get("share_doctype"), row.get("share_name")):
            raise frappe.PermissionError("Managed visit DocShare exists; activation blocked")
