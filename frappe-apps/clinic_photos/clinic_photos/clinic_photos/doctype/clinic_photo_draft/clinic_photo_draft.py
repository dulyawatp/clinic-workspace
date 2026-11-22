import frappe
from frappe.model.document import Document

from clinic_photos.access.draft_identity import (
    SERVER_OWNED_DRAFT_FIELDS,
    DraftIdentityError,
    assert_draft_update_allowed,
    initialize_new_draft,
    is_reserved_visit_identity,
)
from clinic_photos.access.frappe_context import (
    current_publication,
    require_draft_pointer_advance,
)
from clinic_photos.access.lifecycle import (
    LifecycleError,
    normalize_version_number,
)


SAFE_SERVER_OWNED = "Draft identity is server-owned."


def _server_owned_state(document):
    return {
        fieldname: document.get(fieldname)
        for fieldname in SERVER_OWNED_DRAFT_FIELDS
    }


def _pointer_advanced(previous, current) -> bool:
    """Whether this save changes the persisted version pointer at all."""
    if not previous:
        return False
    try:
        return normalize_version_number(
            previous.get("current_version_number")
        ) != normalize_version_number(current.get("current_version_number"))
    except LifecycleError:
        # An invalid pointer value is refused by the identity guard below.
        return False


class ClinicPhotoDraft(Document):
    """Manual annotation draft owned by its immutable author."""

    def before_insert(self):
        if is_reserved_visit_identity(getattr(self, "name", None)):
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)
        try:
            prepared = initialize_new_draft(
                _server_owned_state(self), frappe.session.user
            )
        except DraftIdentityError:
            frappe.throw(
                SAFE_SERVER_OWNED,
                frappe.PermissionError,
            )
        for fieldname in SERVER_OWNED_DRAFT_FIELDS:
            setattr(self, fieldname, prepared[fieldname])

    def validate(self):
        if is_reserved_visit_identity(getattr(self, "name", None)):
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)
        if self.is_new():
            return

        previous = self.get_doc_before_save()
        previous_state = (
            _server_owned_state(previous) if previous is not None else None
        )
        current_state = _server_owned_state(self)
        context = current_publication()
        try:
            assert_draft_update_allowed(
                previous_state,
                current_state,
                draft_name=self.name,
                context=context,
            )
        except DraftIdentityError:
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)

        if not _pointer_advanced(previous_state, current_state):
            return

        # The one allowed advance still has to be the publication of a real, committed
        # version record for this exact draft, key, File and live prepared operation.
        try:
            require_draft_pointer_advance(
                draft=self.name,
                context=context,
                target_version=normalize_version_number(
                    current_state.get("current_version_number")
                ),
                target_version_name=current_state.get("current_version") or "",
            )
        except (DraftIdentityError, LifecycleError):
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)

    def before_rename(self, old, new, merge=False):
        if is_reserved_visit_identity(old) or is_reserved_visit_identity(new):
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)

    def on_trash(self):
        if is_reserved_visit_identity(getattr(self, "name", None)):
            frappe.throw(SAFE_SERVER_OWNED, frappe.PermissionError)
