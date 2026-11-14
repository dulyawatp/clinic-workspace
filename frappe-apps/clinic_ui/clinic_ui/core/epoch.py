"""Opaque per-session epoch derived with a server-owned secret.

``HMAC-SHA256(secret, site || 0x00 || sid)`` is deterministic for one session,
differs for another session, site or secret, and is not reversible. It is a
correlation value for client cache identity only: not a credential, and never a
substitute for the session cookie or CSRF token.
"""

import hashlib
import hmac


def session_epoch(secret, site, sid):
    """Derive the epoch, or raise ``ValueError`` on any absent input."""

    if not isinstance(secret, bytes) or not secret:
        raise ValueError("epoch secret is required")
    if not isinstance(site, str) or not site:
        raise ValueError("site is required")
    if not isinstance(sid, str) or not sid:
        raise ValueError("session id is required")

    message = f"{site}\x00{sid}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()
