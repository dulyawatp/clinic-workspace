"""Opaque, server-derived workspace aliases for locally created Patients.

No new table or column stores the alias. The alias is a deterministic,
site- and actor-bound wrapper around the create operation key::

    wpa1_<32 hex actor tag>_<16 hex key tag>_<operation key>

The tags are HMACs over the server-owned epoch secret, site and session user.
The client never supplies the final alias; it supplies the operation key and
the server returns the wrapper. Update resolves the wrapper by checking both
tags and then reading the committed create receipt from the external write
fence. A native docname is never embedded, accepted or returned.

This is not an allowlist replacement. The static server-owned allowlist remains
the only source for the seeded fixture. A locally created Patient is eligible
only when the current actor's create receipt is terminal ``COMMITTED`` in the
fence; a missing, dispatched or unknown receipt is refused or locked.
"""

from __future__ import annotations

import hashlib
import hmac
import re

from clinic_ui.core.text import is_valid_text

PREFIX = "wpa1_"
ACTOR_TAG_LENGTH = 32
KEY_TAG_LENGTH = 16
ALIAS_MAX_LENGTH = 192
ALIAS_RE = re.compile(
    r"^wpa1_([0-9a-f]{32})_([0-9a-f]{16})_([A-Za-z0-9_-]{16,128})$"
)
OPERATION_KEY_RE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class AliasError(ValueError):
    """The alias inputs or value are outside the accepted shape."""


def _require_secret(secret):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise AliasError("alias secret is invalid")


def _require_context(site, actor):
    if not is_valid_text(site, 140) or not is_valid_text(actor, 140):
        raise AliasError("alias context is invalid")


def actor_tag(secret, site, actor):
    """Return the stable 16-hex actor/site tag used in an alias."""

    _require_secret(secret)
    _require_context(site, actor)
    message = b"clinic_ui_alias_actor|" + site.strip().encode(
        "utf-8"
    ) + b"|" + actor.strip().encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:ACTOR_TAG_LENGTH]


def _key_tag(secret, site, actor, operation_key):
    message = (
        b"clinic_ui_alias_key|"
        + site.strip().encode("utf-8")
        + b"|"
        + actor.strip().encode("utf-8")
        + b"|"
        + operation_key.encode("utf-8")
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()[:KEY_TAG_LENGTH]


def build_alias(secret, site, actor, operation_key):
    """Return the server-derived alias for one create operation key."""

    if not isinstance(operation_key, str) or OPERATION_KEY_RE.fullmatch(
        operation_key
    ) is None:
        raise AliasError("operation key is invalid")
    alias = (
        f"{PREFIX}{actor_tag(secret, site, actor)}_"
        f"{_key_tag(secret, site, actor, operation_key)}_{operation_key}"
    )
    if len(alias) > ALIAS_MAX_LENGTH:
        raise AliasError("alias is too long")
    return alias


def parse_alias(secret, site, actor, alias):
    """Return the create operation key, or ``None`` when the alias is not ours.

    A wrong actor tag, wrong site, malformed value or a native docname returns
    ``None``. The caller must then resolve the create receipt; a valid tag alone
    is never patient eligibility.
    """

    if not isinstance(alias, str) or len(alias) > ALIAS_MAX_LENGTH:
        return None
    match = ALIAS_RE.fullmatch(alias)
    if match is None:
        return None
    operation_key = match.group(3)
    expected = actor_tag(secret, site, actor)
    if not hmac.compare_digest(expected, match.group(1)):
        return None
    if not hmac.compare_digest(
        _key_tag(secret, site, actor, operation_key), match.group(2)
    ):
        return None
    return operation_key


def alias_operation_key(alias):
    """Return the structural operation key carried by a well-shaped alias.

    This trusts nothing: the caller must still re-derive and verify the full
    alias against the recorded creator, site and operation key. The value is
    only used to bound a read-only fence lookup to candidate rows.
    """

    if not isinstance(alias, str) or len(alias) > ALIAS_MAX_LENGTH:
        return None
    match = ALIAS_RE.fullmatch(alias)
    if match is None:
        return None
    return match.group(3)
