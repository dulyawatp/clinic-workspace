"""Framework-independent lifecycle guards for protected feature records.

The native controllers and File guards delegate server-owned identity decisions
here so the rules stay testable with the standard library. An original photo
record is immutable after registration, and a committed annotation version is
immutable once committed. A shared storage key is governed by its most restrictive
reference: any immutable original or committed version prevents cleanup.

``ProtectedRecord.committed`` has the same meaning: immutable original or immutable
*committed* annotation version, never "draft saved privately".

``PublicationContext`` is the only server-owned way to admit pending File/version
registration, first content consumption and cleanup. It is a request-local value
created by the runtime adapter, never from an HTTP parameter or a generic
permission flag.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from clinic_photos.access.policy import NON_CLINICAL_SESSION_IDENTITIES
from clinic_photos.access.storage import (
    InvalidStoragePath,
    canonical_private_key,
    is_reserved_key,
)


ORIGINAL_IMMUTABLE_FIELDS = (
    "original_file",
    "storage_key",
    "original_sha256",
    "original_width",
    "original_height",
)

VERSION_IDENTITY_FIELDS = (
    "draft",
    "version_number",
    "annotation_file",
    "storage_key",
)

PUBLICATION_OPERATIONS = frozenset({"pending_insert", "pending_cleanup", "commit"})


class LifecycleError(PermissionError):
    """Raised when a mutation would rewrite protected or committed state."""


class PublicationContextError(LifecycleError):
    """Raised when a publication context is malformed or does not match."""


@dataclass(frozen=True)
class PublicationContext:
    """Request-local binding of actor, draft **or** synthetic patient, operation and key.

    Exactly one target binding exists per context. Draft-bound contexts cover annotation
    work and require the drafting clinician; synthetic-patient-bound contexts cover the
    operation kind that has no draft at all, and they carry the server-derived operation
    identity whose persisted prepared row must confirm the patient, actor, site, state,
    key and byte evidence through the runtime adapter. A context never invents a draft,
    version or original placeholder to satisfy a draft-shaped check, and this value
    object on its own is not native authority.
    """

    actor: str
    draft: str
    operation: str
    storage_key: str
    synthetic_patient: str | None = None
    operation_id: str | None = None
    visit_operation: bool = False

    def __post_init__(self):
        if not isinstance(self.actor, str) or not self.actor.strip():
            raise PublicationContextError("publication context requires an actor")
        if self.actor in NON_CLINICAL_SESSION_IDENTITIES:
            raise PublicationContextError(
                "publication context requires an ordinary authenticated actor"
            )
        if self.operation not in PUBLICATION_OPERATIONS:
            raise PublicationContextError("unknown publication operation")

        draft = self.draft.strip() if isinstance(self.draft, str) else ""
        synthetic = self.synthetic_patient
        if synthetic is not None and (
            not isinstance(synthetic, str) or not synthetic.strip()
        ):
            raise PublicationContextError(
                "publication context requires a patient reference"
            )
        if type(self.visit_operation) is not bool or sum(
            (bool(draft), bool(synthetic), self.visit_operation)
        ) != 1:
            raise PublicationContextError(
                "publication context requires exactly one target binding"
            )
        if self.visit_operation and self.operation != "pending_insert":
            raise PublicationContextError("visit context allows first insertion only")
        object.__setattr__(self, "draft", draft)

        operation_id = self.operation_id
        if synthetic or self.visit_operation:
            if not isinstance(operation_id, str) or not operation_id.strip():
                raise PublicationContextError(
                    "synthetic publication context requires an operation identity"
                )
            object.__setattr__(self, "operation_id", operation_id.strip())
        elif operation_id:
            raise PublicationContextError(
                "draft publication context cannot carry an operation identity"
            )

        try:
            key = canonical_private_key(self.storage_key)
        except InvalidStoragePath as error:
            raise PublicationContextError(
                "publication context requires a canonical private storage key"
            ) from error
        if not is_reserved_key(key):
            raise PublicationContextError(
                "publication context requires a reserved clinic storage key"
            )
        object.__setattr__(self, "storage_key", key)


def normalize_link(value: Any) -> str:
    """Normalise an absent Link/Data value to an empty string."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise LifecycleError("protected identity fields must be text")
    return value.strip()


def normalize_version_number(value: Any) -> int:
    """Return an integral, non-negative version number; never coerce invalid input."""
    if value is None:
        return 0
    if isinstance(value, bool):
        raise LifecycleError("version numbers must be integers")
    if isinstance(value, int):
        if value < 0:
            raise LifecycleError("version numbers cannot be negative")
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    raise LifecycleError("version numbers must be non-negative integers")


def _normalize_count(value: Any) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool):
        raise LifecycleError("dimensions must be integers")
    if isinstance(value, int):
        if value < 0:
            raise LifecycleError("dimensions cannot be negative")
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise LifecycleError("dimensions must be non-negative integers")


def _field_state(document: Mapping[str, Any] | Any) -> dict[str, Any]:
    if document is None:
        return {}
    if isinstance(document, Mapping):
        return dict(document)
    getter = getattr(document, "get", None)
    if callable(getter):
        keys = (*ORIGINAL_IMMUTABLE_FIELDS, *VERSION_IDENTITY_FIELDS, "state")
        return {key: getter(key) for key in keys}
    raise LifecycleError("document state is unavailable")


def _state_of(state: Mapping[str, Any]) -> str:
    value = state.get("state") or "pending"
    if value not in ("pending", "committed"):
        raise LifecycleError("unknown annotation version state")
    return value


def context_matches(
    context: PublicationContext | None,
    *,
    operation: str,
    draft: str = "",
    storage_key: str = "",
    synthetic_patient: str = "",
) -> bool:
    """Whether a bound context exactly covers the requested operation/identity."""
    if context is None:
        return False
    if getattr(context, "operation", None) != operation:
        return False

    expected_draft = normalize_link(draft)
    if expected_draft and normalize_link(getattr(context, "draft", None)) != expected_draft:
        return False

    expected_patient = normalize_link(synthetic_patient)
    if (
        expected_patient
        and normalize_link(getattr(context, "synthetic_patient", None)) != expected_patient
    ):
        return False

    expected_key = normalize_link(storage_key)
    if expected_key and normalize_link(getattr(context, "storage_key", None)) != expected_key:
        return False
    return True


# ---------------------------------------------------------------------------
# Original photo record
# ---------------------------------------------------------------------------
def assert_original_record_immutable(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    context: PublicationContext | None = None,
) -> None:
    """Reject changes to server-owned original reference fields.

    Empty-to-set registration values (``original_file``, hash, dimensions) are
    allowed exactly once inside a matching commit context. Once set they never
    change; ``storage_key`` is immutable from registration.
    """
    before = _field_state(previous)
    after = _field_state(current)
    if not before or not after:
        raise LifecycleError("persisted photo state is unavailable; refusing the update")

    previous_key = normalize_link(before.get("storage_key"))
    if previous_key != normalize_link(after.get("storage_key")):
        raise LifecycleError("photo storage_key is server-owned and cannot change")

    commit_bound = context_matches(
        context, operation="commit", storage_key=after.get("storage_key") or ""
    )

    for fieldname in ORIGINAL_IMMUTABLE_FIELDS:
        if fieldname == "storage_key":
            continue

        if fieldname in ("original_width", "original_height"):
            previous_value: Any = _normalize_count(before.get(fieldname))
            current_value: Any = _normalize_count(after.get(fieldname))
        else:
            previous_value = normalize_link(before.get(fieldname))
            current_value = normalize_link(after.get(fieldname))

        if previous_value == current_value:
            continue
        if not previous_value and current_value and commit_bound:
            # Initial registration of the File reference/hash/dimensions.
            continue
        raise LifecycleError(f"photo {fieldname} is server-owned and cannot change")


def assert_original_deletable(previous: Mapping[str, Any] | None) -> None:
    """Original photo records are never deletable through ordinary operations."""
    raise LifecycleError("original photo records are immutable and cannot be deleted")


# ---------------------------------------------------------------------------
# Annotation version record
# ---------------------------------------------------------------------------
def _version_identity(state: Mapping[str, Any]) -> tuple[str, int, str]:
    return (
        normalize_link(state.get("draft")),
        normalize_version_number(state.get("version_number")),
        normalize_link(state.get("storage_key")),
    )


def assert_version_record_immutable(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any] | None,
    context: PublicationContext | None = None,
    *,
    annotation_file_allowed: str | None = None,
) -> None:
    """Reject identity rewrites and any change to a committed version.

    The only ordinary transitions are pending -> pending and pending -> committed.
    A pending registration may leave ``annotation_file`` empty; committing fills it
    exactly once with the File whose persisted key matches ``storage_key``
    (``annotation_file_allowed``, proven by the runtime adapter) inside the bound
    commit context.
    """
    before = _field_state(previous)
    after = _field_state(current)
    if not before or not after:
        raise LifecycleError("persisted version state is unavailable; refusing the update")

    if _version_identity(before) != _version_identity(after):
        raise LifecycleError("annotation version identity is server-owned and cannot change")

    previous_file = normalize_link(before.get("annotation_file"))
    current_file = normalize_link(after.get("annotation_file"))
    previous_state = _state_of(before)
    current_state = _state_of(after)

    commit_bound = context_matches(
        context,
        operation="commit",
        draft=before.get("draft") or "",
        storage_key=before.get("storage_key") or "",
    )

    if previous_file != current_file:
        allowed = normalize_link(annotation_file_allowed)
        if previous_file or not current_file or not allowed or current_file != allowed:
            raise LifecycleError(
                "annotation version File reference can only be registered once"
            )
        if not (previous_state == "pending" and current_state == "committed" and commit_bound):
            raise LifecycleError(
                "linking the annotation File requires the server commit context"
            )

    if previous_state == "committed":
        if current_state != "committed":
            raise LifecycleError("a committed annotation version cannot change state")
        return

    if current_state == "pending":
        return

    if not current_file:
        raise LifecycleError("a committed annotation version requires its File reference")
    if not commit_bound:
        raise LifecycleError(
            "committing an annotation version requires the server publication context"
        )


def assert_version_deletable(
    previous: Mapping[str, Any] | None,
    context: PublicationContext | None = None,
    *,
    draft_author: str = "",
) -> None:
    """Committed versions are never deletable; pending cleanup is author/context-bound."""
    before = _field_state(previous)
    if not before:
        raise LifecycleError("persisted version state is unavailable; refusing the delete")

    if _state_of(before) == "committed":
        raise LifecycleError("a committed annotation version cannot be deleted")

    author = normalize_link(draft_author)
    if not author or not context_matches(
        context,
        operation="pending_cleanup",
        draft=normalize_link(before.get("draft")),
        storage_key=normalize_link(before.get("storage_key")),
    ):
        raise LifecycleError(
            "pending annotation cleanup requires the server publication context"
        )
    if normalize_link(getattr(context, "actor", None)) != author:
        raise LifecycleError(
            "pending annotation cleanup requires its drafting clinician"
        )


def require_version_insert(
    record: Mapping[str, Any] | Any,
    context: PublicationContext | None = None,
    *,
    draft_author: str = "",
) -> None:
    """A new version may only be registered pending inside the server context.

    ``annotation_file`` may be empty at registration; the runtime adapter verifies
    any supplied File reference. Commit later proves the matching persisted
    File/key (see ``assert_version_record_immutable``).
    """
    state = _field_state(record)
    if _state_of(state) != "pending":
        raise LifecycleError("annotation versions can only be registered pending")

    draft = normalize_link(state.get("draft"))
    storage_key = normalize_link(state.get("storage_key"))
    if not draft or not storage_key:
        raise LifecycleError("version registration requires draft and storage identity")
    try:
        canonical_key = canonical_private_key(storage_key)
    except InvalidStoragePath as error:
        raise LifecycleError(
            "version registration requires a canonical private storage key"
        ) from error
    if not is_reserved_key(canonical_key):
        raise LifecycleError("version registration requires a reserved clinic storage key")

    author = normalize_link(draft_author)
    if not author or not context_matches(
        context,
        operation="pending_insert",
        draft=draft,
        storage_key=canonical_key,
    ):
        raise LifecycleError(
            "version registration requires the server publication context"
        )
    if normalize_link(getattr(context, "actor", None)) != author:
        raise LifecycleError(
            "version registration requires the drafting clinician"
        )


# ---------------------------------------------------------------------------
# Shared-byte content and cleanup
# ---------------------------------------------------------------------------
def assert_pending_file_content_allowed(
    record: Any,
    context: PublicationContext | None,
    *,
    actor_user: str,
    draft_author: str,
    actor_enabled: bool,
    actor_clinician: bool,
    has_existing_bytes: bool,
    original_photo_allowed: bool = False,
    synthetic_patient: str = "",
    synthetic_bound: bool = False,
) -> None:
    """Admit first content consumption only for the bound pending target.

    Context presence alone is never sufficient: the key must be registered pending,
    every annotation reference must belong to the same draft/key/author, no
    annotation reference may be committed, and the *key* must not already hold bytes.

    ``has_existing_bytes`` is key-wide evidence supplied by the runtime adapter
    (persisted rows for every alias spelling plus the confined target path), not the
    caller's own row. When the key is referenced by an original photo, initialization
    is admitted only if the bound draft's ``photo`` is that same original record
    (``original_photo_allowed``); the default is fail-closed.

    A draft-bound context must carry its drafting clinician. A synthetic-patient-bound
    context has no draft and no draft author: ``synthetic_patient`` is the patient the
    runtime adapter derived from the persisted prepared operation and ``synthetic_bound``
    reports that the adapter re-verified it. Neither binding may be substituted for the
    other, and neither admits a mixed target.
    """
    if record is None or not record.registered or record.inconsistent:
        raise LifecycleError("pending content requires a consistent registered File")
    if record.kind == "mixed":
        raise LifecycleError("shared mixed identity cannot receive pending content")
    if record.committed and any(
        getattr(reference, "kind", "") == "annotation" and reference.committed
        for reference in record.references
    ):
        raise LifecycleError("committed annotation bytes cannot receive content")
    if has_existing_bytes:
        raise LifecycleError("protected File content cannot be overwritten")

    user = normalize_link(actor_user)
    if not user or not actor_enabled or not actor_clinician:
        raise LifecycleError("pending content requires a current enabled clinician")
    if not context_matches(
        context,
        operation="pending_insert",
        storage_key=record.storage_key,
    ):
        raise LifecycleError("pending content requires the bound publication context")
    if normalize_link(getattr(context, "actor", None)) != user:
        raise LifecycleError("pending content requires the bound actor")

    bound_draft = normalize_link(getattr(context, "draft", None))
    bound_patient = normalize_link(getattr(context, "synthetic_patient", None))
    if bound_patient:
        if bound_draft:
            raise LifecycleError("synthetic content cannot bind a draft")
        if normalize_link(synthetic_patient) != bound_patient or not synthetic_bound:
            raise LifecycleError(
                "pending content requires the server-bound synthetic patient"
            )
    else:
        if normalize_link(synthetic_patient) or synthetic_bound:
            raise LifecycleError("draft content cannot bind a synthetic patient")
        if not bound_draft:
            raise LifecycleError("pending content requires a draft binding")
        if normalize_link(draft_author) != user:
            raise LifecycleError("pending content requires the drafting clinician")

    if record.draft and bound_draft != record.draft:
        raise LifecycleError("pending content draft does not match the registration")

    for reference in record.references:
        kind = getattr(reference, "kind", "")
        if getattr(reference, "storage_key", "") != record.storage_key:
            raise LifecycleError("shared-byte reference storage keys disagree")
        if kind == "original":
            if not original_photo_allowed:
                raise LifecycleError(
                    "original storage bytes may only be initialized by their own draft"
                )
            continue
        if kind != "annotation":
            raise LifecycleError("unknown protected reference kind")
        if reference.committed:
            raise LifecycleError("committed annotation bytes cannot receive content")
        if normalize_link(getattr(reference, "draft", "")) != bound_draft:
            raise LifecycleError("shared-byte draft references disagree")
        if normalize_link(getattr(reference, "author", "")) != user:
            raise LifecycleError("pending content requires the drafting author")


def assert_pending_file_cleanup(
    record: Any,
    context: PublicationContext | None,
    *,
    actor_user: str,
    draft_author: str,
    actor_enabled: bool,
    actor_clinician: bool,
) -> None:
    """Cleanup is allowed only when every reference is pending for the bound actor."""
    if record is None or not record.registered or record.inconsistent:
        raise LifecycleError("cleanup requires a consistent registered File")
    if record.kind == "mixed":
        raise LifecycleError("mixed shared identity cannot be cleaned up")
    # Any immutable original/committed reference makes the bytes immutable.
    if record.committed:
        raise LifecycleError("immutable protected bytes cannot be deleted")
    if not record.references:
        raise LifecycleError("registered protected bytes require references")

    user = normalize_link(actor_user)
    if not user or not actor_enabled or not actor_clinician:
        raise LifecycleError("cleanup requires a current enabled clinician")
    if not context_matches(
        context,
        operation="pending_cleanup",
        storage_key=record.storage_key,
    ):
        raise LifecycleError("cleanup requires the bound cleanup context")
    if normalize_link(getattr(context, "actor", None)) != user:
        raise LifecycleError("cleanup requires the bound actor")
    if normalize_link(draft_author) != user:
        raise LifecycleError("cleanup requires the drafting clinician")

    bound_draft = normalize_link(getattr(context, "draft", None))
    for reference in record.references:
        if getattr(reference, "kind", "") != "annotation":
            raise LifecycleError("cleanup only applies to pending annotation bytes")
        if reference.committed:
            raise LifecycleError("immutable protected bytes cannot be deleted")
        if getattr(reference, "storage_key", "") != record.storage_key:
            raise LifecycleError("shared-byte reference storage keys disagree")
        if normalize_link(getattr(reference, "draft", "")) != bound_draft:
            raise LifecycleError("cleanup reference belongs to another draft")
        if normalize_link(getattr(reference, "author", "")) != user:
            raise LifecycleError("cleanup reference belongs to another author")
