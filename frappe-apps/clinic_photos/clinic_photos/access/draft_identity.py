"""Framework-independent guards for draft identity and server-owned references.

``Clinic Photo Draft``'s native controller delegates to these functions. They
contain no Frappe import so the identity/reference contract can be tested with
the standard library. The tests for these guards are local stand-ins for
controller decisions; they do not prove installed Frappe enforcement.
"""

from collections.abc import Mapping
from typing import Any

from clinic_photos.access.lifecycle import (
    LifecycleError,
    context_matches,
    normalize_link,
    normalize_version_number,
)
from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES


# Fields an ordinary clinical client may not choose or change. Native owner
# write exists for later manual-save behavior, so these must be compared against
# the persisted document rather than trusted as ``read_only`` metadata.
SERVER_OWNED_DRAFT_FIELDS = (
    "author",
    "owner",
    "photo",
    "current_version_number",
    "current_version",
)


class DraftIdentityError(ValueError):
    """Raised when a draft insert/update would forge a server-owned value."""


def initialize_new_draft(
    values: Mapping[str, Any], session_user: str | None
) -> dict[str, Any]:
    """Return insert values with identity and version pointers server-bound.

    The authenticated session user becomes both ``author`` and Frappe's native
    ``owner`` so the owner-write DocPerm applies to the real author. A supplied
    author/owner that names another user is rejected; blank values are rebound.
    Version pointers always start empty. The input mapping is not mutated.
    """
    if not session_user or session_user in NON_CLINICAL_SESSION_IDENTITIES:
        raise DraftIdentityError(
            "draft creation requires an authenticated non-Administrator session"
        )

    prepared = dict(values)
    for fieldname in ("author", "owner"):
        supplied = prepared.get(fieldname)
        if supplied not in (None, "", session_user):
            raise DraftIdentityError(
                f"draft {fieldname} must equal the authenticated session user"
            )

    prepared["author"] = session_user
    prepared["owner"] = session_user
    prepared["current_version_number"] = 0
    prepared["current_version"] = None
    return prepared


def assert_draft_update_allowed(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    *,
    draft_name: str = "",
    context: Any = None,
) -> None:
    """Reject ordinary updates that change a server-owned draft field.

    ``previous`` must be the persisted document state. Missing/empty state
    fails closed. The caller keeps the JSON read-only metadata as defence in
    depth; this comparison is the contract.

    The only server-owned transition is the publication commit of one annotation
    version for this exact draft: inside a ``commit`` publication context bound to the
    same draft and held by its drafting clinician, ``current_version_number`` may
    advance by exactly one and ``current_version`` may become that version record's
    server-derived name (``<draft>-v<number>``). Author, owner and photo remain
    immutable, and a caller cannot supply the number or name it wants written.
    """
    if not previous or not current:
        raise DraftIdentityError(
            "persisted draft state is unavailable; refusing the update"
        )

    author = normalize_link(previous.get("author"))
    commit_bound = bool(author) and context_matches(
        context, operation="commit", draft=normalize_link(draft_name)
    )
    if commit_bound and normalize_link(getattr(context, "actor", None)) != author:
        commit_bound = False

    for fieldname in SERVER_OWNED_DRAFT_FIELDS:
        try:
            if fieldname == "current_version_number":
                unchanged = normalize_version_number(
                    previous.get(fieldname)
                ) == normalize_version_number(current.get(fieldname))
            else:
                unchanged = normalize_link(previous.get(fieldname)) == normalize_link(
                    current.get(fieldname)
                )
        except LifecycleError as error:
            raise DraftIdentityError(
                f"draft {fieldname} has an invalid server-owned value"
            ) from error

        if unchanged:
            continue
        if commit_bound and fieldname in (
            "current_version_number",
            "current_version",
        ):
            try:
                expected = normalize_version_number(
                    previous.get("current_version_number")
                ) + 1
                observed = normalize_version_number(
                    current.get("current_version_number")
                )
            except LifecycleError as error:
                raise DraftIdentityError(
                    f"draft {fieldname} has an invalid server-owned value"
                ) from error
            name = normalize_link(draft_name)
            if (
                name
                and observed == expected
                and normalize_link(current.get("current_version"))
                == f"{name}-v{expected}"
            ):
                continue
        raise DraftIdentityError(
            f"draft {fieldname} is server-owned and cannot change "
            "in an ordinary update"
        )
