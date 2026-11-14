"""``GET /api/method/clinic_ui.api.session.bootstrap`` implementation.

Three distinct outcomes, never merged:

- **anonymous/expired** (no session, guest session id, guest or blank user):
  a fixed ``{"code": "AUTH_EXPIRED"}`` body with HTTP 401, so the client can
  reach sign-in. No CSRF token, site configuration, epoch or identity is read.
- **authenticated but unauthorized** (administrator, disabled user, wrong or
  ambiguous role, unavailable configuration): the framework permission error
  (403) with a generic message.
- **authorized**: live session description, opaque epoch, read-only capability
  map and the server-generated session CSRF token.

The raw session identifier, the cookie and the epoch secret never enter a
payload and are never logged.
"""

from clinic_ui.core import dto
from clinic_ui.core.epoch import session_epoch
from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.endpoints import common


def bootstrap(gateway):
    common.begin(gateway)

    view = common.live_view(gateway)
    if view is None:
        return gateway.respond_anonymous()

    common.require_authorized_session(gateway, view)
    settings = common.settings_or_deny(gateway)
    role = common.role_or_deny(gateway, view, settings, SUPPORTED_ROLE_CODES)

    csrf_token = gateway.csrf_token()
    if not dto.is_valid_text(csrf_token, dto.MAX_CSRF_TOKEN_LENGTH):
        gateway.deny()

    try:
        epoch = session_epoch(settings.epoch_secret, gateway.site, view.sid)
        return dto.build_bootstrap(
            session_epoch=epoch,
            user_id=view.user,
            display_name=view.full_name,
            role=role,
            csrf_token=csrf_token,
        )
    except ValueError:
        gateway.deny()
