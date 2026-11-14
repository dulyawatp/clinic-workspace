"""Framework-independent access policy for protected clinic photos.

This module deliberately imports no Frappe code. Callers supply the current
authenticated identity, the current enabled/clinician status and the result of
native document authorization. The policy can only narrow native access; it
never grants access that native permissions denied.

``Actor`` represents an authenticated end-user *session* principal only. It is
not an internal or service identity. Infrastructure administrators remain
trusted outside this module, and a bound server/publication context (added by a
later task) must be a distinct, non-client-enableable path, not an ``Actor``
carrying a magic role or user name. Administrator sessions are therefore denied
ordinary clinical decisions here even though setup, migration, cleanup and
database/storage operation may act with Administrator privileges outside this
policy.
"""

from dataclasses import dataclass


# Session identities that must never become ordinary clinical principals, even
# if a caller accidentally reports clinician membership for them. This is a
# session-principal deny list, not a statement that trusted setup paths are
# unauthorized outside this module.
NON_CLINICAL_SESSION_IDENTITIES = frozenset({"", "Guest", "Administrator"})


@dataclass(frozen=True)
class Actor:
    user: str
    enabled: bool
    clinician: bool


@dataclass(frozen=True)
class ProtectedRecord:
    """A protected read/mutation subject.

    ``committed`` is the immutable annotation semantic from the review ledger: an
    immutable original or a *committed* annotation version. It is not "draft saved
    privately"; pending versions are protected but not committed.

    ``kind`` is the lifecycle kind: ``original``, ``annotation``, ``mixed``,
    ``unregistered`` (reserved path with no authoritative reference), ``pending``
    (registered only by a durable PREPARED reservation, with no domain reference yet)
    or ``invalid`` (private-looking path that cannot be canonicalised). ``registered``
    is false for the fail-closed cases. ``inconsistent`` is true when caller,
    persisted and authoritative reference identities disagree; every guard denies
    those records. ``references`` holds the authoritative feature records used for
    native document authorisation and shared-byte lifecycle decisions.
    """

    author: str
    committed: bool
    kind: str = "original"
    storage_key: str = ""
    registered: bool = True
    draft: str = ""
    references: tuple = ()
    inconsistent: bool = False


def _is_current_enabled_clinician(actor: Actor) -> bool:
    return bool(
        actor.enabled
        and actor.clinician
        and actor.user
        and actor.user not in NON_CLINICAL_SESSION_IDENTITIES
    )


def may_read(actor: Actor, native_read: bool) -> bool:
    """Allow a read only for a current enabled clinician native read allows."""
    return bool(native_read) and _is_current_enabled_clinician(actor)


def may_edit_draft(actor: Actor, author: str, native_write: bool) -> bool:
    """Allow a draft edit only for its current enabled clinician author."""
    if not (native_write and _is_current_enabled_clinician(actor)):
        return False
    return bool(author) and author == actor.user
