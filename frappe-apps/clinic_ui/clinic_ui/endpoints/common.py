"""Shared gate steps for the read-only endpoints.

Order matters and is asserted by tests:

1. mark the response no-store (also on denial and on the anonymous response),
2. classify the request as anonymous/expired or as an authenticated identity,
3. validate the authenticated session (guest, administrator and disabled
   identities are refused),
4. load and validate the explicit site configuration,
5. resolve exactly one clinic role that is allowed for this surface.

Only the bootstrap endpoint dispatches anonymous callers: it returns a fixed
401 so the client can reach sign-in. Every other refusal — including an
authenticated but unauthorized caller — is the framework's own permission error
(403) with a generic message. No configuration state, session identifier,
secret, role or record detail is included in either failure.
"""

from clinic_ui.core import config, policy


def begin(gateway):
    """Mark the response no-store before any decision is taken."""

    gateway.mark_no_store()


def live_view(gateway):
    """Return the session facts, or ``None`` when the caller is anonymous."""

    view = gateway.current_session
    return None if policy.is_anonymous(view) else view


def require_authorized_session(gateway, view):
    """Refuse an authenticated but unusable session (administrator, disabled)."""

    if not policy.is_valid_session(view):
        gateway.deny()


def settings_or_deny(gateway):
    settings = config.load_settings(gateway.setting, gateway.site)
    if settings is None:
        gateway.deny()
    return settings


def role_or_deny(gateway, view, settings, allowed_codes):
    role = policy.authorize(view, settings.as_role_map(), allowed_codes)
    if role is None:
        gateway.deny()
    return role


def authorize(gateway, allowed_codes):
    """Strict gate for non-guest surfaces: every refusal is a 403."""

    begin(gateway)

    view = gateway.current_session
    require_authorized_session(gateway, view)

    settings = settings_or_deny(gateway)
    role = role_or_deny(gateway, view, settings, allowed_codes)
    return settings, view, role
