"""Stable opaque patient references; not an authorization or eligibility check.

Use a separately provisioned 64-byte key, never the epoch key. The dedicated
reference cap is 808 ASCII characters: 5 prefix characters plus unpadded
base64url of a 16-byte SIV, 26-byte creation timestamp and up to 560 UTF-8
bytes (140 four-byte characters). Creation is a naive datetime, serialized as
YYYY-MM-DDTHH:MM:SS.ffffff, with exactly six fractional digits. Consumers must
compare decoded creation against freshly authorized record data.
"""

import base64
import binascii
import re
from datetime import datetime

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESSIV

from clinic_ui.core.text import is_valid_text

PREFIX = "cpr1_"
REF_MAX_LENGTH = 808
_PAYLOAD = re.compile(r"[A-Za-z0-9_-]+")
_PURPOSE = b"clinic_ui.production_patient_ref.cpr1"


def _context(key, site):
    if not isinstance(key, bytes) or len(key) != 64 or not is_valid_text(site, 140):
        raise ValueError("invalid patient reference input")
    try:
        site_bytes = site.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid patient reference input") from None
    return AESSIV(key), [_PURPOSE, site_bytes]


def _name_bytes(name):
    if not is_valid_text(name, 140):
        raise ValueError("invalid patient reference input")
    try:
        return name.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("invalid patient reference input") from None


def encode(key, site, name, creation):
    """Encode a native name and naive creation datetime for this exact site."""
    cipher, associated_data = _context(key, site)
    if not isinstance(creation, datetime) or creation.tzinfo is not None:
        raise ValueError("invalid patient reference input")
    timestamp = creation.isoformat(timespec="microseconds").encode("ascii")
    ciphertext = cipher.encrypt(timestamp + _name_bytes(name), associated_data)
    return PREFIX + base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii")


def decode(key, site, ref):
    """Return (native name, creation datetime), or None for an invalid ref."""
    cipher, associated_data = _context(key, site)
    if not isinstance(ref, str) or len(ref) > REF_MAX_LENGTH or not ref.startswith(PREFIX):
        return None
    payload = ref[len(PREFIX):]
    if not _PAYLOAD.fullmatch(payload):
        return None
    try:
        ciphertext = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        if base64.urlsafe_b64encode(ciphertext).rstrip(b"=").decode("ascii") != payload:
            return None
        plaintext = cipher.decrypt(ciphertext, associated_data)
        timestamp = plaintext[:26].decode("ascii")
        creation = datetime.fromisoformat(timestamp)
        if creation.tzinfo is not None or creation.isoformat(timespec="microseconds") != timestamp:
            return None
        name = plaintext[26:].decode("utf-8")
        _name_bytes(name)
    except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError):
        return None
    return name, creation
