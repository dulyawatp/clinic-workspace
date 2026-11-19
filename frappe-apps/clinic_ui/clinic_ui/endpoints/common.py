"""Shared gate steps for the native read endpoints and the local write path.

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

from clinic_ui.core import config, policy, write_fence


def begin(gateway):
    """Mark the response no-store and traceback-free before any decision."""

    gateway.suppress_traceback()
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


def authorize_patient_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the converted patient-write gate.

    The converted development lane relies on Frappe/Marley permissions,
    validation and transactions instead of the historical operation fence,
    deployment marker and acknowledgement barrier. It still requires the
    authenticated session, a valid same-session CSRF token, the exact approved
    site configuration and the clinic role code, and it refuses the historical
    fence's reserved synthetic identity. Native Patient create/write authority
    is checked per request by the endpoint. No fence state is read.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.write_enabled:
        gateway.deny()
    return settings, view, role


def authorize_appointment_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the converted appointment-write gate.

    The converted development lane relies on Frappe/Marley permissions,
    validation and transactions instead of the historical operation fence,
    deployment marker and acknowledgement barrier. It still requires the
    authenticated session, a valid same-session CSRF token, the exact approved
    site configuration, the appointment flag and the clinic role code. Target
    configuration is never authority: every request still performs its own
    native document permission checks.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.appointment_write_enabled:
        gateway.deny()
    return settings, view, role


def authorize_encounter_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the separate encounter-write gate.

    The encounter gate is independent of the patient and appointment flags. It
    requires the exact approved site, the encounter flag and the same private
    deployment marker bound to the fence store. Target configuration is never
    authority: every request still performs its own native permission checks.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.encounter_write_enabled:
        gateway.deny()
    if not gateway.write_deployment_approved():
        gateway.deny()
    return settings, view, role


def authorize_procedure_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the converted procedure-write gate.

    The converted development lane relies on Frappe/Marley permissions,
    validation and transactions instead of the historical operation fence,
    deployment marker and acknowledgement barrier. It still requires the
    authenticated session, a valid same-session CSRF token, the exact approved
    site configuration and the clinic role code. The target is decided per
    request: the server-owned allowlist, a read-only historical creation
    receipt, or a native docname under the caller's own native permission.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.procedure_write_enabled:
        gateway.deny()
    return settings, view, role


def authorize_stock_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the separate stock-write gate.

    The stock gate is independent of every clinical and admin flag. It requires
    the exact approved site, the stock flag and the same private deployment
    marker bound to the fence store. Masters, batches and native authority are
    decided per request; no configuration value is authority on its own.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.stock_write_enabled:
        gateway.deny()
    if not gateway.write_deployment_approved():
        gateway.deny()
    return settings, view, role


def authorize_purchase_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the separate purchasing-write gate.

    The purchasing gate is independent of the stock, clinical and admin flags.
    It requires the exact approved site, the purchasing flag and the same
    private deployment marker bound to the fence store. Masters, the purchase
    order target and native authority are decided per request; no configuration
    value is authority on its own.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.purchase_write_enabled:
        gateway.deny()
    if not gateway.write_deployment_approved():
        gateway.deny()
    return settings, view, role


def authorize_purchase_draft_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the converted purchase-draft gate.

    The converted draft lane relies on Frappe/Marley permissions, validation,
    controller lifecycle and native transactions instead of the historical
    operation fence, write budget, deployment marker and acknowledgement
    barrier. It still requires the authenticated session, a valid same-session
    CSRF token, the exact approved site configuration, the purchasing flag and
    the clinic role code. The native Purchase Order create permission and every
    Supplier/Item read are checked per request; no configuration value is
    authority on its own.

    Submit and the PO-referenced receipt deliberately keep the historical gate
    above, including the deployment marker and the fence store.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.purchase_write_enabled:
        gateway.deny()
    return settings, view, role


def authorize_finance_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the separate finance-write gate.

    The finance gate is independent of the purchasing and clinical flags. It
    requires the exact approved site, the finance flag and the same private
    deployment marker bound to the fence store. Customers, price lists, ledger
    accounts, payment modes and native authority are decided per request; no
    configuration value is authority on its own.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.finance_write_enabled:
        gateway.deny()
    if not gateway.write_deployment_approved():
        gateway.deny()
    return settings, view, role


def authorize_admin_user_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the converted admin user-create gate.

    The converted user lane relies on Frappe/Marley permissions, validation,
    controller lifecycle and transactions instead of the historical admin
    operation fence, deployment marker, write budget and acknowledgement
    barrier. It still requires the authenticated session, a valid same-session
    CSRF token, the exact approved site configuration, the admin flag and the
    clinic role code. Native ``User`` create authority is checked per request by
    the endpoint. No fence state is read.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.admin_write_enabled:
        gateway.deny()
    return settings, view, role


def authorize_admin_write(gateway, allowed_codes, csrf_token=None):
    """Read gate, explicit CSRF, and the separate admin-write gate.

    The admin gate is independent of every clinical write flag. It requires the
    exact approved site, the admin flag and the same private deployment marker
    bound to the fence store. The role allowlist and native authority are
    decided per request; no configuration value is authority on its own.
    """

    settings, view, role = authorize(gateway, allowed_codes)
    if view.user == write_fence.LATCH_ACTOR:
        gateway.deny()
    if not gateway.csrf_token_valid(csrf_token):
        gateway.deny()
    if not settings.admin_write_enabled:
        gateway.deny()
    if not gateway.write_deployment_approved():
        gateway.deny()
    return settings, view, role
