"""Canonical protected storage identity and File classification.

This module imports no Frappe code and never consults a content hash as
protection evidence. Classification resolves, in order:

1. the canonical key of the caller's in-memory ``file_url``;
2. the canonical key of the persisted File row (supplied by the runtime adapter);
3. authoritative feature references by storage key **and** by referenced File
   name (``original_file`` / ``annotation_file``).

Any disagreement between those identities is returned as a protected,
``inconsistent`` record so every read/mutation guard fails closed. Unrelated files
return ``None`` so native behaviour is untouched.

Storage identity is the canonical site-relative private path. Feature records and
File rows are expected to store ``canonical_private_key(file_url)`` verbatim; the
runtime adapter rejects non-canonical spellings of protected keys at creation,
update and registration instead of treating them as a spelling convention.
"""

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import unquote, urlsplit

from clinic_photos.access.policy import Actor, ProtectedRecord, may_read


PRIVATE_URL_PREFIX = "/private/files/"
PRIVATE_RAW_PREFIX = "/private/"
VISIT_BASENAME_PREFIX = "clinic-visit-photo-"
VISIT_ANNOTATION_BASENAME_PREFIX = "clinic-visit-annotation-"
RESERVED_BASENAME_PREFIXES = (
    "clinic-photo-",
    "clinic-annotation-",
    VISIT_BASENAME_PREFIX,
)
FORBIDDEN_URL_TOKENS = ("\\", "\x00")
ENCODED_SEPARATORS = ("%2f", "%5c", "%00")

# A durable operation reservation is a registration reference, not a domain record.
# It protects a canonical key before any File/version/original row exists and grants
# no author, lifecycle or read right of its own.
RESERVATION_REFERENCE_KIND = "reservation"
PENDING_RECORD_KIND = "pending"
_UNSET = object()


class InvalidStoragePath(ValueError):
    """Raised when a file URL is not a canonical site-relative private path."""


class ProtectedAccessDenied(PermissionError):
    """Raised when protected bytes lack both current clinician and native access."""


class ProtectionRef(Mapping):
    """One authoritative feature record referencing a protected storage key."""

    __slots__ = (
        "kind",
        "author",
        "committed",
        "doctype",
        "name",
        "draft",
        "storage_key",
        "_values",
    )

    def __init__(
        self,
        kind: str,
        *,
        author: str = "",
        committed: bool = False,
        doctype: str = "",
        name: str = "",
        draft: str = "",
        storage_key: str = "",
    ):
        self.kind = kind
        self.author = author
        self.committed = committed
        self.doctype = doctype
        self.name = name
        self.draft = draft
        self.storage_key = storage_key
        self._values = {
            "kind": kind,
            "author": author,
            "committed": committed,
            "doctype": doctype,
            "name": name,
            "draft": draft,
            "storage_key": storage_key,
        }

    def __getitem__(self, key):
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):  # pragma: no cover - diagnostics only
        return (
            "ProtectionRef(kind={0.kind!r}, doctype={0.doctype!r}, "
            "name={0.name!r}, draft={0.draft!r}, committed={0.committed!r})".format(
                self
            )
        )


def canonical_private_key(file_url: str) -> str:
    """Return the canonical site-relative private path for ``file_url``.

    Percent-encoding is decoded exactly once. Encoded separators, null bytes,
    traversal/empty segments, queries, fragments, absolute URLs and non-private
    roots are rejected instead of normalised into a different file identity.
    """
    if not isinstance(file_url, str):
        raise InvalidStoragePath("file URL must be text")
    if not file_url:
        raise InvalidStoragePath("file URL is empty")
    if file_url != file_url.strip():
        raise InvalidStoragePath("file URL has surrounding whitespace")
    if any(token in file_url for token in FORBIDDEN_URL_TOKENS):
        raise InvalidStoragePath("file URL contains a forbidden character")
    if any(ord(character) < 32 for character in file_url):
        raise InvalidStoragePath("file URL contains a control character")

    lowered = file_url.lower()
    if any(token in lowered for token in ENCODED_SEPARATORS):
        raise InvalidStoragePath("file URL encodes a path separator")

    parts = urlsplit(file_url)
    if parts.scheme or parts.netloc:
        raise InvalidStoragePath("file URL must be site-relative")
    if parts.query or parts.fragment:
        raise InvalidStoragePath("file URL must not carry a query or fragment")

    path = unquote(parts.path)
    if "%" in path:
        raise InvalidStoragePath("file URL is not singly percent-encoded")
    if not path.startswith(PRIVATE_URL_PREFIX):
        raise InvalidStoragePath("file URL is not a private storage path")

    remainder = path[len(PRIVATE_URL_PREFIX) :]
    segments = remainder.split("/")
    if not remainder or any(
        segment in ("", ".", "..") or segment.startswith("~")
        for segment in segments
    ):
        raise InvalidStoragePath("file URL escapes the private storage root")

    return PRIVATE_URL_PREFIX + "/".join(segments)


def is_canonical_visit_key(storage_key: str) -> bool:
    """Identify canonical keys in the visit namespace, including orphan names."""
    try:
        return (
            canonical_private_key(storage_key) == storage_key
            and storage_key.rsplit("/", 1)[-1].startswith(VISIT_BASENAME_PREFIX)
        )
    except InvalidStoragePath:
        return False


def reserved_namespace_name(value: str) -> bool:
    """Whether a path or bare filename is in the reserved orphan namespace."""
    if not isinstance(value, str) or not value:
        return False
    basename = value.rstrip("/").rsplit("/", 1)[-1]
    return (basename.startswith(RESERVED_BASENAME_PREFIXES) or
            basename.lower().startswith(VISIT_ANNOTATION_BASENAME_PREFIX))


def is_reserved_key(storage_key: str) -> bool:
    """Whether a canonical key's basename is in the reserved orphan namespace."""
    return reserved_namespace_name(storage_key)


def file_url_of(file_doc: Any) -> Any:
    """Read ``file_url`` from a mapping or a document-like object."""
    if file_doc is None:
        return None
    if isinstance(file_doc, Mapping):
        return file_doc.get("file_url")
    return getattr(file_doc, "file_url", None)


def file_name_of(file_doc: Any) -> str:
    if file_doc is None:
        return ""
    if isinstance(file_doc, Mapping):
        value = file_doc.get("name")
    else:
        value = getattr(file_doc, "name", None)
    return value if isinstance(value, str) else ""


def identity_of(file_doc: Any) -> tuple[str, bool]:
    """Return ``(canonical_key, invalid_private_looking)`` for one document."""
    raw = file_url_of(file_doc)
    if not raw:
        return "", False
    try:
        return canonical_private_key(raw), False
    except InvalidStoragePath:
        return "", isinstance(raw, str) and raw.startswith(PRIVATE_RAW_PREFIX)


def _ref_value(ref: Any, fieldname: str, default: Any = "") -> Any:
    if isinstance(ref, Mapping):
        return ref.get(fieldname, default)
    return getattr(ref, fieldname, default)


def _protections(lookup: Any, storage_key: str | None, file_name: str) -> tuple:
    if lookup is None:
        from clinic_photos.access.frappe_context import lookup_protection

        lookup = lookup_protection

    finder = lookup if callable(lookup) else getattr(lookup, "find_protection")
    found = finder(storage_key, file_name) or ()
    return tuple(found)


def _dedupe_refs(references: Sequence[Any]) -> tuple:
    seen = set()
    unique = []
    for reference in references:
        marker = (
            _ref_value(reference, "doctype"),
            _ref_value(reference, "name"),
        )
        if marker in seen:
            continue
        seen.add(marker)
        unique.append(reference)
    return tuple(unique)


def is_managed_annotation_file(
    file_doc: Any, *, lookup: Any = None, persisted: Any = None, record: Any = _UNSET
) -> bool:
    """Classify reserved drawing keys and authoritative managed version references."""
    from clinic_photos.access.draft_identity import is_reserved_visit_identity

    keys = [identity_of(doc)[0] for doc in (file_doc, persisted)]
    if any(key.rsplit("/", 1)[-1].lower().startswith(VISIT_ANNOTATION_BASENAME_PREFIX)
           for key in keys if key):
        return True
    if record is _UNSET:
        record = classify_file(file_doc, lookup=lookup, persisted=persisted)
    return bool(record and any(
        _ref_value(ref, "doctype") == "Clinic Photo Annotation Version"
        and (is_reserved_visit_identity(_ref_value(ref, "name"))
             or is_reserved_visit_identity(_ref_value(ref, "draft")))
        for ref in record.references
    ))


def _build_record(
    references: Sequence[Any], storage_key: str, inconsistent: bool
) -> ProtectedRecord:
    kinds = {_ref_value(reference, "kind") for reference in references}
    if kinds == {"original"}:
        kind = "original"
    elif kinds == {"annotation"}:
        kind = "annotation"
    else:
        kind = "mixed"
        inconsistent = True

    authors = {_ref_value(reference, "author") for reference in references} - {""}
    drafts = {_ref_value(reference, "draft") for reference in references} - {""}
    reference_keys = {
        _ref_value(reference, "storage_key") for reference in references
    } - {""}

    if len(authors) > 1 or len(drafts) > 1 or len(reference_keys) > 1:
        inconsistent = True
    if kind == "mixed":
        # Ambiguous identity grants no author-attributed right.
        author = ""
    else:
        author = next(iter(authors)) if len(authors) == 1 else ""

    return ProtectedRecord(
        author=author,
        # Most restrictive reference wins: any immutable original/committed
        # version makes the shared bytes immutable.
        committed=any(
            bool(_ref_value(reference, "committed")) for reference in references
        ),
        kind=kind,
        storage_key=storage_key,
        registered=True,
        draft=next(iter(drafts)) if len(drafts) == 1 else "",
        references=tuple(references),
        inconsistent=inconsistent,
    )


def classify_file(
    file_doc: Any,
    *,
    lookup: Any = None,
    persisted: Any = None,
) -> ProtectedRecord | None:
    """Classify a File row from caller identity, persisted identity and references.

    ``persisted`` is the stored File row (mapping or document) when the runtime
    has one. A caller-mutated in-memory URL therefore cannot downgrade a stored
    protected file, and a File referenced by a feature record stays protected even
    if its URL is changed.
    """
    file_name = file_name_of(persisted) or file_name_of(file_doc)
    current_key, current_invalid = identity_of(file_doc)
    persisted_key, persisted_invalid = identity_of(persisted)

    keys = []
    for key in (current_key, persisted_key):
        if key and key not in keys:
            keys.append(key)

    references = []
    for key in keys:
        references.extend(_protections(lookup, key, file_name))
    if not keys and file_name:
        references.extend(_protections(lookup, None, file_name))
    references = _dedupe_refs(references)

    # Durable PREPARED reservations are split out of the domain references: they
    # register a key without granting any lifecycle right, and no lifecycle decision
    # may treat them as an original/annotation record.
    reservations = tuple(
        reference
        for reference in references
        if _ref_value(reference, "kind") == RESERVATION_REFERENCE_KIND
    )
    references = tuple(
        reference
        for reference in references
        if _ref_value(reference, "kind") != RESERVATION_REFERENCE_KIND
    )

    inconsistent = bool(current_invalid or persisted_invalid)
    if current_key and persisted_key and current_key != persisted_key:
        inconsistent = True

    reference_keys = {
        _ref_value(reference, "storage_key") for reference in references
    } - {""}
    if len(reference_keys) > 1:
        inconsistent = True
    if reference_keys and not reference_keys.issubset(set(keys)):
        inconsistent = True

    if references:
        if len(reference_keys) == 1:
            storage_key = next(iter(reference_keys))
        else:
            storage_key = keys[0] if len(keys) == 1 else ""
        return _build_record(references, storage_key, inconsistent)

    if reservations and keys:
        # Registered by a durable PREPARED reservation only. No File row, domain
        # reference or byte identity is asserted here, so `references` stays empty and
        # every read path keeps denying until a real domain record exists.
        reservation_keys = {
            _ref_value(reference, "storage_key") for reference in reservations
        } - {""}
        if reservation_keys != {keys[0]}:
            inconsistent = True
        return ProtectedRecord(
            author="",
            committed=False,
            kind=PENDING_RECORD_KIND,
            storage_key=keys[0],
            registered=True,
            inconsistent=inconsistent,
        )

    if any(is_reserved_key(key) for key in keys):
        return ProtectedRecord(
            author="",
            committed=False,
            kind="unregistered",
            storage_key=keys[0],
            registered=False,
            inconsistent=inconsistent,
        )

    if current_invalid or persisted_invalid:
        # A private-looking path that cannot be canonicalised is not provably
        # unrelated. Deny rather than silently fall back to native access.
        return ProtectedRecord(
            author="",
            committed=False,
            kind="invalid",
            storage_key="",
            registered=False,
            inconsistent=True,
        )

    return None


def require_protected_read(
    file_doc: Any,
    *,
    actor: Actor | None = None,
    native_read: bool | None = None,
    lookup: Any = None,
    persisted: Any = None,
    record: Any = _UNSET,
) -> None:
    """Raise unless protected bytes have a current enabled clinician and native read.

    Unrelated files return immediately so native behaviour is untouched. The
    runtime defaults resolve a fresh actor and native File/feature read decisions
    through :mod:`clinic_photos.access.frappe_context`; pure callers inject them.
    """
    if record is _UNSET:
        record = classify_file(file_doc, lookup=lookup, persisted=persisted)
    if is_managed_annotation_file(file_doc, lookup=lookup, persisted=persisted, record=record):
        raise ProtectedAccessDenied("managed annotation bytes are unavailable")
    if record is None:
        return
    if not record.registered or record.inconsistent:
        raise ProtectedAccessDenied("protected file identity is not consistent")

    adapter = None
    if actor is None or native_read is None:
        from clinic_photos.access import frappe_context

        adapter = frappe_context

    if actor is None:
        actor = adapter.current_actor()
    if native_read is None:
        native_read = adapter.authorize_protected_read(record, file_doc)

    if not may_read(actor, native_read):
        raise ProtectedAccessDenied(
            "protected file requires current clinician and native read access"
        )
