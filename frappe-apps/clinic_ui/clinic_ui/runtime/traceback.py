"""Never return stack traces, source hints or server messages from clinic API errors.

Frappe v16 error responses can carry more than a status:

- ``frappe.utils.response.is_traceback_allowed()`` (``response.py:68-79``)
  enables the full formatted traceback for a dev server, or for a System User
  when the site sets ``allow_error_traceback``. ``_make_logs_v1`` then adds
  ``exc`` (traceback), ``_exc_source`` and ``_debug_messages``; ``report_error``
  adds the last traceback line as ``exception``.
- Message channels are serialized *independently* of that flag:
  ``_server_messages`` and ``_error_message`` (v1), and ``messages`` plus
  ``errors[].exception`` (v2). A caught ``frappe.throw("...")`` therefore can
  reach the client even with tracebacks disabled.

The clinic app cannot change site settings, roles or framework code. Two narrow
hooks cover its own API surface:

- ``before_request`` sets ``frappe.local.flags.disable_traceback`` for clinic
  API requests. This removes traceback enrichment on the installed gunicorn
  site; Frappe's ``frappe._dev_server`` branch ignores the flag, so the
  ``after_request`` sanitiser is the guarantee for that case too.
- ``after_request`` replaces the whole serialized body of a clinic API error
  (status >= 400) with the fixed safe envelope ``{"code": "<status code>"}``.
  This does not parse or trust Frappe's body, so dev-server tracebacks, v1
  ``_server_messages``/``_error_message``, v2 ``errors[].message``/``title``/
  ``exception``, unknown keys, malformed JSON, HTML and empty bodies cannot
  survive. The HTTP status is preserved and the classification is derived only
  from it.

Scope and limits
----------------
Coverage is the supported dispatch forms for this app: ``/api/method/``,
``/api/v1/method/``, ``/api/v2/method/`` and legacy ``cmd=clinic_ui.`` dispatch.
A request that fails before Frappe initialises the request (bad host, site
resolution, database connection) never reaches either hook and remains
framework-owned; this module makes no claim about that phase. The app also
cannot suppress framework logging, only the client response.
"""

CLINIC_PATH_PREFIXES = (
    "/api/method/clinic_ui.",
    "/api/v1/method/clinic_ui.",
    "/api/v2/method/clinic_ui.",
)
CLINIC_CMD_PREFIX = "clinic_ui."

# Keys that can carry tracebacks, source hints or arbitrary server text.
UNSAFE_V1_KEYS = (
    "exc",
    "_exc_source",
    "_debug_messages",
    "_server_messages",
    "_error_message",
    "exception",
    "message_log",
)


def is_clinic_api_request(path=None, cmd=None):
    """True for a supported clinic API dispatch path or legacy ``cmd``."""

    if isinstance(path, str) and path.startswith(CLINIC_PATH_PREFIXES):
        return True
    return isinstance(cmd, str) and cmd.startswith(CLINIC_CMD_PREFIX)


def _request_facts():
    import frappe

    local = getattr(frappe, "local", None)
    request = getattr(local, "request", None)
    path = getattr(request, "path", "") if request is not None else ""
    form_dict = getattr(frappe, "form_dict", None) or getattr(
        local, "form_dict", None
    )
    cmd = getattr(form_dict, "cmd", None) if form_dict is not None else None
    return local, path, cmd


def suppress_api_tracebacks():
    """Set Frappe's traceback-suppression flag for clinic API requests only.

    Failure to set the flag must never break the request: the response still
    carries the framework's status, and any exception here is deliberately
    swallowed so this hook cannot become a new failure path.
    """

    try:
        local, path, cmd = _request_facts()
        if is_clinic_api_request(path, cmd) and local is not None:
            local.flags.disable_traceback = True
    except Exception:
        return


# Safe, non-sensitive classification derived only from the HTTP status. The
# client already maps these codes; no server message, exception text or record
# field is carried.
# Server-set 409 codes for the local write path. The sanitizer still emits only
# a fixed allowlist and only when the flag was set by server code in this
# request; a client cannot set a flag on ``frappe.local``.
WRITE_ERROR_CODES = frozenset(
    {"WRITE_LOCKED", "OPERATION_CONFLICT", "STALE_VERSION"}
)

STATUS_CODES = {
    400: "VALIDATION",
    401: "AUTH_EXPIRED",
    403: "FORBIDDEN",
    404: "UNSUPPORTED",
    405: "UNSUPPORTED",
    409: "CONFLICT_STALE",
    413: "FILE_TOO_LARGE",
    415: "UNSUPPORTED",
    417: "VALIDATION",
    422: "VALIDATION",
    429: "RATE_LIMITED",
}


def generic_error_code(status):
    """Map an error status to the client's fixed safe code."""

    if status in STATUS_CODES:
        return STATUS_CODES[status]
    return "UPSTREAM_UNAVAILABLE" if status >= 500 else "UNKNOWN"


def sanitize_api_error_response(response=None, request=None):
    """Replace a recognized clinic API error body with a fixed safe envelope.

    Frappe has already serialized the error at this point. Instead of parsing
    and trusting that body (a denylist leaves unknown fields and v2
    ``errors[].message``/``title`` behind), the whole body is replaced by
    ``{"code": <status classification>}``. Malformed JSON, HTML and unknown
    keys therefore cannot survive. The status is preserved; the content type is
    set to JSON when the response is mutable. Runs only for an initialized
    request (``frappe.local.initialised``), matching
    ``run_after_request_hooks``; pre-init failures remain framework-owned.
    """

    try:
        import json

        local, path, cmd = _request_facts()
        if local is None or not getattr(local, "initialised", False):
            return
        if request is not None:
            path = getattr(request, "path", path)
        if not is_clinic_api_request(path, cmd):
            return

        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or status < 400 or response is None:
            return

        code = None
        if status == 409:
            flags = getattr(local, "flags", None)
            candidate = getattr(flags, "clinic_ui_write_error_code", None)
            if isinstance(candidate, str) and candidate in WRITE_ERROR_CODES:
                code = candidate
            try:
                flags.clinic_ui_write_error_code = None
            except Exception:
                pass
        if code is None:
            code = generic_error_code(status)

        payload = json.dumps({"code": code})
        try:
            response.set_data(payload)
            response.mimetype = "application/json"
        except Exception:
            return
    except Exception:
        return
