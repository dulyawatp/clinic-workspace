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
from clinic_photos.access.draft_identity import is_reserved_visit_identity
from clinic_photos.access.policy import Actor, may_read
from urllib.parse import unquote

from clinic_photos.access.storage import (
    InvalidStoragePath, PENDING_RECORD_KIND, VISIT_BASENAME_PREFIX,
    VISIT_ANNOTATION_BASENAME_PREFIX, canonical_private_key, is_canonical_visit_key,
    identity_of,
)


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
def _is_builtin_administrator(user):
    if user is None:
        import frappe

        user = frappe.session.user
    return user == "Administrator"


def draft_permission_query_conditions(user=None) -> str:
    if _is_builtin_administrator(user):
        return ""
    return (
        "LOWER(IFNULL(`tabClinic Photo Draft`.`name`, '')) NOT LIKE "
        "CONCAT('clinic-visit-annotation-draft-', CHAR(37))"
    )


def version_permission_query_conditions(user=None) -> str:
    if _is_builtin_administrator(user):
        return ""
    return (
        "LOWER(IFNULL(`tabClinic Photo Annotation Version`.`name`, '')) NOT LIKE "
        "CONCAT('clinic-visit-annotation-draft-', CHAR(37)) AND "
        "LOWER(IFNULL(`tabClinic Photo Annotation Version`.`draft`, '')) NOT LIKE "
        "CONCAT('clinic-visit-annotation-draft-', CHAR(37))"
    )


def draft_has_permission(doc=None, user=None, ptype=None, **kwargs) -> bool:
    if doc is None:
        return True
    name = getattr(doc, "name", None)
    if is_reserved_visit_identity(name):
        return False
    if name:
        import frappe

        return not is_reserved_visit_identity(
            frappe.db.get_value("Clinic Photo Draft", name, "name")
        )
    return True


def version_has_permission(doc=None, user=None, ptype=None, **kwargs) -> bool:
    if doc is None:
        return True
    name = getattr(doc, "name", None)
    if is_reserved_visit_identity(name) or is_reserved_visit_identity(getattr(doc, "draft", None)):
        return False
    if name:
        import frappe

        row = frappe.db.get_value(
            "Clinic Photo Annotation Version", name, ["name", "draft"], as_dict=True
        ) or {}
        return not (is_reserved_visit_identity(row.get("name")) or
                    is_reserved_visit_identity(row.get("draft")))
    return True


def file_permission_query_conditions(user=None) -> str:
    """Exclude visit names for every actor; other File policy remains unchanged."""
    import frappe

    from clinic_photos.access import frappe_context

    if _is_builtin_administrator(user):
        return file_query_condition(
            frappe_context.current_actor(user), frappe_context.protected_file_names()
        )

    versions = frappe.db.get_all(
        "Clinic Photo Annotation Version",
        fields=["name", "draft", "annotation_file", "storage_key"], limit=0,
    )
    managed = [row for row in versions if is_reserved_visit_identity(row.get("name"))
               or is_reserved_visit_identity(row.get("draft"))]
    version_names = {row.get("name") for row in managed}
    file_names = {row.get("annotation_file") for row in managed}
    keys = {identity_of({"file_url": row.get("storage_key")})[0] for row in managed}
    keys.discard("")
    names = []
    canonical_aliases = []
    for row in frappe.db.get_all(
        "File", fields=["name", "file_url", "attached_to_doctype", "attached_to_name"],
        limit=0,
    ):
        key = identity_of(row)[0]
        if key and key != row.get("file_url") and row.get("name"):
            canonical_aliases.append((row["name"], key))
        attached_type, attached_name = row.get("attached_to_doctype"), row.get("attached_to_name")
        if (row.get("name") in file_names or key in keys or
            (attached_type == "Clinic Photo Draft" and is_reserved_visit_identity(attached_name)) or
            (attached_type == "Clinic Photo Annotation Version" and
             (attached_name in version_names or is_reserved_visit_identity(attached_name))) or
            (key and key.rsplit("/", 1)[-1].lower().startswith(VISIT_ANNOTATION_BASENAME_PREFIX))):
            names.append(row.get("name"))
            continue
        if _private_visit_marker(
            row.get("file_url"), (VISIT_BASENAME_PREFIX, VISIT_ANNOTATION_BASENAME_PREFIX)
        ):
            names.append(row.get("name"))

    actor = frappe_context.current_actor(user)
    condition = file_query_condition(
        actor, () if _is_current_enabled_clinician(actor) else frappe_context.protected_file_names()
    )
    managed_version = (
        "(LOWER(IFNULL(v.`name`, '')) LIKE CONCAT('clinic-visit-annotation-draft-', CHAR(37))"
        " OR LOWER(IFNULL(v.`draft`, '')) LIKE CONCAT('clinic-visit-annotation-draft-', CHAR(37)))"
    )
    version_match = (
        "EXISTS (SELECT 1 FROM `tabClinic Photo Annotation Version` v WHERE "
        + managed_version + " AND ("
        "v.`annotation_file` = `tabFile`.`name` OR "
        "v.`storage_key` = `tabFile`.`file_url` OR "
        "(`tabFile`.`attached_to_doctype` = 'Clinic Photo Annotation Version' "
        "AND v.`name` = `tabFile`.`attached_to_name`)))"
    )
    draft_match = (
        "EXISTS (SELECT 1 FROM `tabClinic Photo Draft` d WHERE "
        "`tabFile`.`attached_to_doctype` = 'Clinic Photo Draft' "
        "AND d.`name` = `tabFile`.`attached_to_name` AND "
        "LOWER(IFNULL(d.`name`, '')) LIKE CONCAT('clinic-visit-annotation-draft-', CHAR(37)))"
    )
    visit_condition = (
        "LOWER(IFNULL(`tabFile`.`file_url`, '')) NOT LIKE "
        "CONCAT('/private/files/clinic-visit-photo-', CHAR(37))"
        " AND LOWER(IFNULL(`tabFile`.`file_url`, '')) NOT LIKE "
        "CONCAT('/private/files/clinic-visit-annotation-', CHAR(37))"
        " AND NOT " + version_match
        + " AND NOT " + draft_match
    )
    if any(row.get("storage_key") for row in managed):
        excluded = set(names)
        candidates = [(name, key) for name, key in canonical_aliases if name not in excluded]
        for offset in range(0, len(candidates), 100):
            chunk = candidates[offset:offset + 100]
            derived = " UNION ALL ".join(
                "SELECT %s AS file_name, %s AS canonical_key" for _ in chunk
            )
            values = tuple(value for pair in chunk for value in pair)
            matches = frappe.db.sql(
                "SELECT DISTINCT aliases.file_name FROM (" + derived + ") aliases "
                "JOIN `tabClinic Photo Annotation Version` v ON v.`storage_key` = aliases.canonical_key "
                "WHERE " + managed_version,
                values,
            )
            names.extend(row[0] for row in matches)
    if names:
        literals = ", ".join(hex_name_literal(name) for name in dict.fromkeys(names))
        visit_condition += " AND HEX(`tabFile`.`name`) NOT IN (" + literals + ")"
    return f"{condition} and ({visit_condition})" if condition else visit_condition


def _private_visit_marker(value, prefixes):
    if not isinstance(value, str):
        return False
    while True:
        if (value.lower().startswith("/private/files/") and
                value.rsplit("/", 1)[-1].lower().startswith(prefixes)):
            return True
        decoded = unquote(value)
        if decoded == value:
            return False
        value = decoded


def _contains_visit_marker(value):
    return _private_visit_marker(value, (VISIT_BASENAME_PREFIX,))


def photo_permission_query_conditions(user=None) -> str:
    """Hide managed visit Photos from generic lists for every actor."""
    import frappe

    visit_files = {
        row.get("name") for row in frappe.db.get_all(
            "File", fields=["name", "file_url"], limit=0
        ) if _contains_visit_marker(row.get("file_url"))
    }
    names = {
        name for name in frappe.db.get_all(
            "Clinic Visit Photo Slot", pluck="photo", limit=0
        ) if name
    }
    for row in frappe.db.get_all(
        "Clinic Photo", fields=["name", "storage_key", "original_file"], limit=0
    ):
        if (_contains_visit_marker(row.get("storage_key")) or
            row.get("original_file") in visit_files):
            names.add(row.get("name"))
    condition = (
        "LOWER(IFNULL(`tabClinic Photo`.`storage_key`, '')) NOT LIKE "
        "CONCAT('/private/files/clinic-visit-photo-', CHAR(37))"
    )
    if names:
        literals = ", ".join(hex_name_literal(name) for name in sorted(names))
        condition += " AND HEX(`tabClinic Photo`.`name`) NOT IN (" + literals + ")"
    return condition


def photo_has_permission(doc=None, user=None, ptype=None, **kwargs) -> bool:
    """Deny managed visit metadata outside the verified pair's native check."""
    if (ptype or "").lower() not in READ_PERMISSION_TYPES or doc is None:
        return True
    from clinic_photos.visit.pair import _photo_read_in_scope

    if _photo_read_in_scope(doc, user):
        return True
    if _contains_visit_marker(getattr(doc, "storage_key", None)):
        return False
    import frappe

    name = getattr(doc, "name", None)
    if not name:
        return True
    row = frappe.db.get_value(
        "Clinic Photo", name, ["storage_key", "original_file"], as_dict=True
    ) or {}
    if _contains_visit_marker(row.get("storage_key")):
        return False
    file_name = row.get("original_file")
    if file_name and _contains_visit_marker(
        frappe.db.get_value("File", file_name, "file_url")
    ):
        return False
    return not bool(frappe.db.get_all(
        "Clinic Visit Photo Slot", filters={"photo": name}, pluck="name", limit=1
    ))


def _visit_create_no_objection(doc, user, record, actor, persisted):
    """Defer only the exact pre-write visit insert; never grant native access."""
    import frappe
    from clinic_photos.access import file_mixin, frappe_context
    from clinic_photos.access.policy import ProtectedRecord
    from clinic_photos.publication import journal, visit_annotation_prepare, visit_file_scope, visit_transaction

    entry = visit_file_scope._ACTIVE.get()
    attempt = file_mixin._VISIT_INSERT.get()
    try:
        if (type(entry) is not tuple or len(entry) != 5 or
                type(entry[0]) is not visit_file_scope._FileScope or
                type(entry[1]) is not visit_transaction._VisitTransaction or
                type(attempt) is not list or len(attempt) != 4 or attempt[0] is not entry or
                attempt[1] is not False or type(attempt[2]) is not bool or attempt[3] is not None or
                entry[2] is not doc or persisted is not None or
                entry[4][0] is not False or entry[4][1] is not True):
            return False
        guard, reservation = entry[1], entry[3]
        owner = guard._exclusion_owner
        if (type(reservation) is not visit_annotation_prepare._ContinuedReservation or
                reservation.intent is not owner.intent or reservation.operation != owner.operation_id or
                reservation.generation != 1 or guard._native_file is not doc or guard._native_settled or
                guard._phase != "publish" or
                not _is_current_enabled_clinician(actor) or actor.user != guard._actor or
                frappe.session.user != guard._actor or (user is not None and user != guard._actor) or
                getattr(frappe.local, "site", None) != guard._site or
                type(record) is not ProtectedRecord or record != ProtectedRecord(
                    author="", committed=False, kind=PENDING_RECORD_KIND,
                    storage_key=reservation.key, registered=True, inconsistent=False) or
                getattr(doc, "file_url", None) != reservation.key or
                canonical_private_key(reservation.key) != reservation.key):
            return False
        visit_file_scope._check_file(entry, doc)
        visit_file_scope._check_insert_modes(doc)
        values = owner._visit_prepared_values
        if (type(values) is not dict or values != {
                "state": journal.PREPARED_STATE, "generation": 1,
                "reservation_key": reservation.key, "expected_sha256": reservation.digest,
                "expected_size": reservation.size}):
            return False
        row = frappe_context.get_save_operation(owner.operation_id, for_update=True)
        guard._check()
        if row is None:
            return False
        row = dict(row)
        row.setdefault("doctype", journal.OPERATION_DOCTYPE)
        expected = {**dict(owner.submitted_projection), **values}
        visit_file_scope._check_file(entry, doc)
        visit_file_scope._check_insert_modes(doc)
        return (visit_file_scope._ACTIVE.get() is entry and file_mixin._VISIT_INSERT.get() is attempt and
                attempt[1] is False and attempt[3] is None and
                entry[4][0] is False and entry[4][1] is True and
                guard._native_file is doc and not guard._native_settled and
                frappe.session.user == guard._actor and
                set(row) == set(expected) and
                all(type(row[k]) is type(v) and row[k] == v for k, v in expected.items()))
    except Exception:
        return False


def file_has_permission(doc=None, user=None, ptype=None, **kwargs) -> bool:
    from clinic_photos.access import frappe_context

    persisted = frappe_context._file_row(doc)
    record = frappe_context.classify_file(doc, persisted=persisted)
    if ((ptype or "").lower() in READ_PERMISSION_TYPES and
        frappe_context.is_managed_annotation_file(
            doc, persisted=persisted, record=record
        )):
        return False
    actor = frappe_context.current_actor(user)
    context = frappe_context.current_publication()

    if (ptype or "").lower() == "create":
        from clinic_photos.access import file_mixin
        from clinic_photos.publication import visit_file_scope
        if visit_file_scope._ACTIVE.get() is not None or file_mixin._VISIT_INSERT.get() is not None:
            return _visit_create_no_objection(doc, user, record, actor, persisted)

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
    if ((ptype or "").lower() in READ_PERMISSION_TYPES and record is not None
        and is_canonical_visit_key(record.storage_key)):
        from clinic_photos.visit.pair import _file_read_in_scope

        import frappe

        pending = (
            getattr(record, "kind", "") == PENDING_RECORD_KIND
            and raw_url == record.storage_key
            and (user is None or user == frappe.session.user)
            and frappe_context._pending_content_matches(record, doc)
        )
        if not pending and not _file_read_in_scope(doc, user):
            return False
    # Installed has_controller_permissions denies on any falsy hook return. A policy
    # deferral therefore reaches it as a truthy "no objection"; native role/user
    # permissions and the native File hook still decide. Any other value is denied.
    return decision is None
