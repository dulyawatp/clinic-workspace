"""Shared text rules for untrusted or configured strings.

A value is usable only when it is a non-empty string without C0 control
characters, DEL or surrounding-only whitespace and within the caller's length
limit. Callers decide whether an unusable value is denied or omitted.
"""

MAX_TEXT_LENGTH = 256


def is_valid_text(value, max_length=MAX_TEXT_LENGTH):
    if not isinstance(value, str):
        return False
    if not value.strip() or len(value) > max_length:
        return False
    for char in value:
        code = ord(char)
        if code < 32 or code == 127:
            return False
    return True


def is_valid_identifier(value):
    """Trimmed, control-free, bounded identifier used by configuration values."""

    return is_valid_text(value)


def normalize_identifier(value):
    return value.strip() if isinstance(value, str) else value
