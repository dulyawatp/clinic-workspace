"""Restrictive File metadata policy and native hook adapters.

The pure policy returns ``False`` to deny and ``None`` to defer; it never grants.
The native ``File.has_permission`` adapter translates that deferral to ``True``
("no objection"), because installed Frappe denies on any falsy hook return and then
still applies role/user permissions and the native File controller: a truthy adapter
return cannot grant by itself. Any other policy value is denied. ``permission_query_conditions``
covers ``frappe.get_list``. Privileged ``frappe.get_all`` bypasses query conditions
by design, so internal feature lookups stay scoped to one storage key and never
return their rows to a client.

The list condition relies on the storage convention from
:mod:`clinic_photos.access.storage`: feature records store
``canonical_private_key(file_url)`` verbatim. Natively deduplicated aliases reuse
the identical ``file_url`` string. A private-looking row that is neither referenced
nor in the reserved namespace is not provably protected; the per-document hook
still canonicalises it in Python, so bytes remain denied even if a list row is not
filtered.
"""

from clinic_photos.access.lifecycle import context_matches
from clinic_photos.access.policy import Actor, may_read
from clinic_photos.access.storage import InvalidStoragePath, canonical_private_key


READ_PERMISSION_TYPES = frozenset({"read", "select"})

# Exact reference/storage-key clauses: complete because SQL equality matches every
# stored row, independent of any Python-side scan.
_REFERENCE_SQL_CLAUSES = (
    "`tabFile`.`name` IN (SELECT `original_file` FROM `tabClinic Photo` "
    "WHERE `original_file` IS NOT NULL AND `original_file` != '')",
    "`tabFile`.`name` IN (SELECT `annotation_file` "
    "FROM `tabClinic Photo Annotation Version` "
    "WHERE `annotation_file` IS NOT NULL AND `annotation_file` != '')",
    "IFNULL(`tabFile`.`file_url`, '') IN (SELECT `storage_key` "
    "FROM `tabClinic Photo` WHERE `storage_key` IS NOT NULL AND `storage_key` != '')",
    "IFNULL(`tabFile`.`file_url`, '') IN (SELECT `storage_key` "
    "FROM `tabClinic Photo Annotation Version` "
    "WHERE `storage_key` IS NOT NULL AND `storage_key` != '')",
)

UNSAFE_SQL_LITERAL = "File name cannot be represented safely in a query condition"


def hex_name_literal(value: str) -> str:
    """Represent one File name as an ASCII hex literal of its UTF-8 bytes.

    The emitted literal contains only the SQL quote delimiters and ``[0-9A-F]``, so
    neither quote doubling nor backslash escapes are involved: the result does not
    depend on ``NO_BACKSLASH_ESCAPES`` or any other SQL mode. Comparison happens on
    ``HEX(name)``, which MariaDB and SQLite both return uppercase for.
    """
    if not isinstance(value, str) or not value:
        raise ValueError(UNSAFE_SQL_LITERAL)
    encoded = value.encode("utf-8")
    return "'" + encoded.hex().upper() + "'"


def protected_file_condition(file_names=()) -> str:
    """Static reference clauses plus exact, escaped exclusions for scanned names."""
    clauses = list(_REFERENCE_SQL_CLAUSES)
    literals = [hex_name_literal(name) for name in dict.fromkeys(file_names or ())]
    if literals:
        clauses.append("HEX(`tabFile`.`name`) IN (" + ", ".join(literals) + ")")
    return "(" + " OR ".join(clauses) + ")"


PROTECTED_FILE_SQL_CONDITION = protected_file_condition()


def _is_current_enabled_clinician(actor: Actor) -> bool:
    # ``may_read(actor, True)`` is the identity/status half of the read policy;
    # native permission is applied separately by the document hook.
    return may_read(actor, True)


def file_metadata_allowed(record, actor: Actor) -> bool:
    """Protected metadata requires a current enabled clinician; unrelated defers."""
    if record is None:
        return True
    if not record.registered:
        return False
    return _is_current_enabled_clinician(actor)


def file_query_condition(actor: Actor, file_names=(), extra_condition: str = "") -> str:
    """SQL appended to File list queries; empty means native conditions only.

    ``file_names`` must come from a complete canonicalised scan of existing File
    metadata (including encoded spellings of reserved orphans and of registered
    keys). Names are escaped individually; an unrepresentable name raises instead of
    producing a partially-correct filter. Unrelated rows are preserved.
    """
    if _is_current_enabled_clinician(actor):
        return extra_condition or ""
    condition = f"not {protected_file_condition(file_names)}"
    if extra_condition:
        return f"({extra_condition}) and {condition}"
    return condition


def file_has_permission_for(
    record,
    actor: Actor,
    permission_type: str | None,
    *,
    context=None,
    storage_key: str = "",
) -> bool | None:
    """Pure document-hook decision: ``False`` denies, ``None`` defers to native."""
    permission = (permission_type or "").lower()

    if record is None:
        return None

    if permission == "create":
        if context_matches(
            context, operation="pending_insert", storage_key=storage_key
        ):
            return None
        return False

    if permission == "delete" and context_matches(
        context, operation="pending_cleanup", storage_key=storage_key
    ):
        # Native delete permission and the controller's on_trash guard still apply.
        return None

    if not file_metadata_allowed(record, actor):
        return False

    if permission in READ_PERMISSION_TYPES:
        return None

    # Reads never become writes: every other permission stays denied.
    return False


# ---------------------------------------------------------------------------
# Native hook adapters (lazy Frappe import)
# ---------------------------------------------------------------------------
def file_permission_query_conditions(user=None) -> str:
    from clinic_photos.access import frappe_context

    return file_query_condition(
        frappe_context.current_actor(user),
        frappe_context.protected_file_names(),
    )


def file_has_permission(doc=None, user=None, ptype=None, **kwargs) -> bool:
    from clinic_photos.access import frappe_context

    record = frappe_context.classify_file(doc)
    actor = frappe_context.current_actor(user)
    context = frappe_context.current_publication()

    storage_key = ""
    raw_url = getattr(doc, "file_url", None) if doc is not None else None
    if raw_url:
        try:
            storage_key = canonical_private_key(raw_url)
        except InvalidStoragePath:
            storage_key = ""

    decision = file_has_permission_for(
        record,
        actor,
        ptype,
        context=context,
        storage_key=storage_key,
    )
    # Installed has_controller_permissions denies on any falsy hook return. A policy
    # deferral therefore reaches it as a truthy "no objection"; native role/user
    # permissions and the native File hook still decide. Any other value is denied.
    return decision is None
