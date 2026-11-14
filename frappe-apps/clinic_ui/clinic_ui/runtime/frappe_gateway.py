"""The single Frappe seam for the read-only clinic endpoints.

Everything here is resolved from the current request and the current site; no
value is accepted from the client. Verified against the Frappe ``version-16``
source (not against an installed lab build; see the README blockers):

- ``frappe.sessions.get_csrf_token()`` lazily generates the per-session token
  stored in ``frappe.local.session.data.csrf_token``.
- ``frappe.session.sid`` / ``frappe.session.user`` are the request session.
- ``frappe.has_permission(doctype, ptype, doc, user)`` accepts a document name
  and loads it lazily for the permission check.
- ``frappe.local.response_headers`` is merged into the HTTP response, so
  ``Cache-Control: no-store`` can be set for both the success and the denial
  path.

The site configuration is read from that site's own ``site_config.json`` so a
``common_site_config`` value can never enable the feature implicitly.
"""

import json

PATIENT_DOCTYPE = "Patient"
DISPLAY_NAME_FIELD = "patient_name"
NO_STORE_HEADER = "Cache-Control"
NO_STORE_VALUE = "no-store"
DENIAL_MESSAGE = "Not permitted"
ANONYMOUS_STATUS = 401


class Gateway:
    """Adapter used by the whitelisted entry points."""

    def __init__(self, frappe_module=None):
        self._frappe = frappe_module
        self._site_config = None

    @property
    def frappe(self):
        if self._frappe is None:
            import frappe

            self._frappe = frappe
        return self._frappe

    @property
    def site(self):
        return getattr(self.frappe.local, "site", None)

    def _configuration(self):
        if self._site_config is None:
            try:
                path = self.frappe.get_site_path("site_config.json")
                with open(path, encoding="utf-8") as handle:
                    loaded = json.load(handle)
            except Exception:
                loaded = {}
            self._site_config = loaded if isinstance(loaded, dict) else {}
        return self._site_config

    def setting(self, key):
        return self._configuration().get(key)

    @property
    def current_session(self):
        from clinic_ui.core.policy import SessionView

        try:
            frappe = self.frappe
            local = getattr(frappe, "local", None)
            if local is None or getattr(local, "session", None) is None:
                return None
            user = frappe.session.user
            sid = frappe.session.sid
            if not isinstance(user, str) or user == "Guest":
                return SessionView(
                    sid=sid if isinstance(sid, str) else "",
                    user=user if isinstance(user, str) else "",
                    full_name="",
                    enabled=False,
                    roles=(),
                )
            record_full_name = frappe.db.get_value("User", user, "full_name")
            record_enabled = frappe.db.get_value("User", user, "enabled")
            roles = frappe.get_roles(user)
        except Exception:
            return None

        return SessionView(
            sid=sid if isinstance(sid, str) else "",
            user=user,
            full_name=(
                record_full_name
                if isinstance(record_full_name, str) and record_full_name.strip()
                else user
            ),
            enabled=bool(record_enabled),
            roles=tuple(role for role in (roles or ()) if isinstance(role, str)),
        )

    def csrf_token(self):
        try:
            from frappe.sessions import get_csrf_token

            token = get_csrf_token()
        except Exception:
            return None
        if not isinstance(token, str) or not token.strip():
            return None
        return token

    def mark_no_store(self):
        headers = getattr(self.frappe.local, "response_headers", None)
        if headers is None:
            headers = {}
            self.frappe.local.response_headers = headers
        headers[NO_STORE_HEADER] = NO_STORE_VALUE

    def deny(self):
        raise self.frappe.PermissionError(DENIAL_MESSAGE)

    def respond_anonymous(self):
        """Return the fixed anonymous body with an explicit 401 status.

        Frappe reads ``frappe.local.response["http_status_code"]`` for the status
        and removes it from the JSON body, so the client receives
        ``{"message": {"code": "AUTH_EXPIRED"}}`` with HTTP 401. No identity,
        role, epoch, CSRF token or configuration value is included.
        """

        from clinic_ui.core import dto

        self.frappe.local.response["http_status_code"] = ANONYMOUS_STATUS
        return dto.build_anonymous()

    def native_patient_display_name(self, native_patient_id):
        """Current-user native read authority plus the display-only field.

        Returns ``None`` for a missing record, a denied user, a malformed value
        or any adapter failure; the caller then omits the entry. Record fields
        other than the display name are never read into the response.
        """

        try:
            frappe = self.frappe
            user = frappe.session.user
            if not frappe.has_permission(
                PATIENT_DOCTYPE, ptype="read", doc=native_patient_id, user=user
            ):
                return None
            display_name = frappe.db.get_value(
                PATIENT_DOCTYPE, native_patient_id, DISPLAY_NAME_FIELD
            )
        except Exception:
            return None

        if not isinstance(display_name, str) or not display_name.strip():
            return None
        return display_name.strip()
