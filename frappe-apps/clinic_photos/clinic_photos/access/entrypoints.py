"""External raw-reader guard and response no-store hook.

Two boundaries are closed here without a core patch, replacement streaming or a
second storage layer:

1. **Narrow raw-reader override.** The installed ERPNext bank-import helper reads a
   path through ``frappe.utils.file_manager.get_file``, bypassing File methods and
   their permissions. The override refuses protected canonical keys, reserved-orphan
   spellings (including single-encoded aliases) and inputs whose File-row matches
   include protected bytes. Classification uses the authoritative feature-record/
   storage identity from :mod:`clinic_photos.access.storage`, never the filename or a
   content hash.

   Installed ``get_file_path`` runs **one** OR query over
   ``(File.name == value) | (File.file_name == value)`` and takes the first row's
   ``file_url``; it does not prefer docname over filename and row order is
   unspecified. This adapter does not reproduce that arbitrary selection. By approved
   contract amendment, a value matching both protected and unrelated rows (including
   duplicate basenames and conflicting docname/filename matches) is refused; an
   unambiguous unrelated input is delegated unchanged.

2. **ZIP marker propagation.** Installed ``File.zip_files`` skips an entry at
   module-level ``has_permission`` **before** ``get_content``, so a protected entry
   can be skipped without reaching the File read guard. The whitelisted
   ``frappe.core.api.file.zip_files`` method is wrapped to mark protected entries
   before that prefilter and then delegate unchanged to the installed endpoint: no
   replacement authorization, denial, streaming or response construction happens
   here, and unrelated archives keep native behaviour.

3. **Response no-store closure.** ``after_request`` adds ``Cache-Control: no-store``,
   ``Pragma: no-cache`` and ``Expires: 0`` and removes contradictory freshness
   headers, but only when a *server-owned per-request marker* is set. The marker is
   set before authorization by the File read guards, by refused/ambiguous bank input,
   by protected ZIP entries (before native prefilter), and by a narrow direct-path
   classification in ``before_request`` (the one supported seam that runs before the
   response exists). The header hook performs no storage I/O and no authorization;
   Frappe swallows its exceptions, so it is never the byte boundary. Direct URL/API/
   ZIP/accelerated responses all carry the same marker; nginx and real HTTP behaviour
   remain Task 4 gates.

Importing this module never requires a site or database: Frappe is imported lazily.
The only import-time framework interaction is the optional whitelist registration
performed when the ``frappe`` package is importable, so the installed dispatch keeps
the original POST-only contract. Without Frappe the module imports and is fully
testable with the narrow fake runtime.
"""

import importlib
import json
from collections.abc import Mapping
from urllib.parse import unquote

from clinic_photos.access import storage


RAW_READER_MODULE = (
    "erpnext.accounts.doctype.bank_statement_import.bank_statement_import"
)
RAW_READER_METHOD = "convert_mt940_to_csv"
RAW_READER_PATH = RAW_READER_MODULE + "." + RAW_READER_METHOD

NATIVE_ZIP_MODULE = "frappe.core.api.file"
NATIVE_ZIP_METHOD = "zip_files"
NATIVE_ZIP_PATH = NATIVE_ZIP_MODULE + "." + NATIVE_ZIP_METHOD

WHITELISTED_METHODS = ("POST",)

# Server-owned request marker. Never read from form data, query arguments or
# headers; only server code in this app sets it.
PROTECTED_REQUEST_ATTR = "clinic_photos_protected_response"

NO_STORE_HEADERS = (
    ("Cache-Control", "no-store"),
    ("Pragma", "no-cache"),
    ("Expires", "0"),
)
# Freshness directives that contradict no-store, including accelerated-delivery
# and intermediary hints.
OBSOLETE_FRESHNESS_HEADERS = ("Age", "Surrogate-Control", "CDN-Cache-Control")

SAFE_DENIAL = "Not permitted for a protected clinical file"

FILE_LOOKUP_FIELDS = ("name", "file_url", "file_name")
FILE_LOOKUP_FIELDS_ORDER = ("name", "file_name")


def mark_protected_request() -> None:
    """Set the server-owned marker for this request/execution."""
    import frappe

    setattr(frappe.local, PROTECTED_REQUEST_ATTR, True)


def protected_request_marked() -> bool:
    import frappe

    return bool(getattr(frappe.local, PROTECTED_REQUEST_ATTR, False))


def _classify(file_doc):
    from clinic_photos.access import frappe_context

    return frappe_context.classify_file(file_doc)


def _is_protected_file(file_doc):
    from clinic_photos.access import frappe_context

    persisted = frappe_context._file_row(file_doc)
    record = frappe_context.classify_file(file_doc, persisted=persisted)
    return record is not None or frappe_context.is_managed_annotation_file(
        file_doc, persisted=persisted, record=record
    )


# ---------------------------------------------------------------------------
# Response cache closure (supported after_request / before_request hooks)
# ---------------------------------------------------------------------------
def mark_direct_private_request() -> None:
    """``before_request`` hook: classify a direct private route before authorization.

    Installed dispatch serves ``/private/files/<path>`` without a File method call in
    every case (for example when ``find_file_by_url`` finds no matching File row), so
    the marker cannot always be set from a read guard. This narrow classification is
    the supported pre-response seam: key/feature identity only, no authorization and
    no byte access. Unrelated private files stay unmarked.
    """
    import frappe

    request = getattr(frappe.local, "request", None)
    path = getattr(request, "path", "") if request is not None else ""
    if not isinstance(path, str):
        return

    try:
        key = storage.canonical_private_key(path)
    except storage.InvalidStoragePath:
        # A private-root spelling that cannot be canonicalised is refused elsewhere;
        # mark it here too so a denial cannot carry cacheable headers.
        if _private_files_spelling(path):
            mark_protected_request()
        return

    if _classify({"file_url": key}) is not None:
        mark_protected_request()


def _private_files_spelling(value: str) -> bool:
    """Whether text spells the direct private route before/after one decoding."""
    prefix = storage.PRIVATE_URL_PREFIX
    if value.lower().startswith(prefix):
        return True
    return unquote(value).lower().startswith(prefix)


def after_request(request, response) -> None:
    """``after_request`` hook: force no-store on a marked protected response.

    Header-only. It never decides whether bytes may be delivered; the marker is set
    before authorization and denials keep it, so protected denials are no-store too.
    """
    if not protected_request_marked():
        return

    headers = response.headers
    for name, value in NO_STORE_HEADERS:
        headers[name] = value
    for name in OBSOLETE_FRESHNESS_HEADERS:
        try:
            del headers[name]
        except KeyError:
            pass


# ---------------------------------------------------------------------------
# Bank Statement Import raw-reader guard
# ---------------------------------------------------------------------------
def _protected_path(value: str) -> bool:
    """Whether a path spelling reaches protected private bytes.

    Canonical spellings are classified by authoritative storage identity. A
    private-root spelling that cannot be canonicalised is refused rather than
    normalised into a different file identity. Non-private spellings are unrelated
    here; the native helper only reads the private or public files roots.
    """
    try:
        key = storage.canonical_private_key(value)
    except storage.InvalidStoragePath:
        return value.startswith(storage.PRIVATE_RAW_PREFIX)

    if storage.is_reserved_key(key):
        return True
    if _classify({"file_url": key}) is not None:
        return True
    import frappe

    for path in dict.fromkeys((value, key)):
        rows = frappe.db.get_all(
            "File", filters={"file_url": path}, fields=list(FILE_LOOKUP_FIELDS), limit=0
        )
        if any(_is_protected_file(row) for row in rows or ()):
            return True
    # Only encoded URLs can canonicalise to this key without already matching it.
    rows = frappe.db.sql(
        "SELECT `name`, `file_url`, `file_name` FROM `tabFile` "
        "WHERE LOCATE(CHAR(37), `file_url`) > 0",
        as_dict=True,
    )
    for row in rows:
        try:
            candidate = storage.canonical_private_key(row.get("file_url"))
        except storage.InvalidStoragePath:
            continue
        if candidate == key and _is_protected_file(row):
            return True
    return False


def _matching_file_rows(value: str) -> list:
    """Every File row the installed single OR query could select for ``value``.

    Installed ``get_file_path`` (task-3-native-excerpts.txt, file_manager.py:348-387)
    runs ONE query filtering ``(File.name == value) | (File.file_name == value)`` and
    takes ``f[0][0]``; it does not prefer ``name`` over ``file_name`` and row order is
    unspecified. Two lookups unioned here produce the same **match set** (order is
    deliberately not used): the approved policy refuses an ambiguous mixed match
    instead of reproducing arbitrary first-row selection.
    """
    import frappe

    rows = []
    seen = set()
    for fieldname in FILE_LOOKUP_FIELDS_ORDER:
        found = frappe.db.get_all(
            "File",
            filters={fieldname: value},
            fields=list(FILE_LOOKUP_FIELDS),
            limit=0,
        )
        for row in found or ():
            name = row.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            rows.append(row)
    return rows


def _matching_rows_include_protected(value: str) -> bool:
    """Whether any File-row match for ``value`` resolves to protected bytes.

    The supplied value is only the native helper's lookup key. Protection comes from
    classifying each resolved File row's storage identity and authoritative feature
    references. A mixed protected/unrelated match is refused by approved contract; an
    all-unrelated match set is delegated unchanged.
    """
    return any(_is_protected_file(row) for row in _matching_file_rows(value))


def input_reaches_protected_bytes(value) -> bool:
    """Whether the supplied value must be refused before the raw reader.

    Refused: canonical/noncanonical protected paths, reserved-orphan spellings, and
    any input whose File-row matches include a protected row. The last rule is the
    approved ambiguity exception: conflicting docname/filename matches, duplicate
    basenames and a supplied path whose matching row points elsewhere are refused
    rather than resolved by arbitrary native row order. Unknown or all-unrelated
    inputs return False so native behaviour is unchanged.
    """
    if not isinstance(value, str) or not value:
        return False
    if storage.reserved_namespace_name(value):
        return True
    if _protected_path(value):
        return True
    return _matching_rows_include_protected(value)


def convert_mt940_to_csv(data_import, mt940_file_path):
    """Guarded override of the installed ERPNext raw reader (POST-only contract).

    Protected input is refused **before** the ERPNext module is imported or the raw
    filesystem helper is reached. Unrelated input delegates to the installed callable
    with identical arguments; its result and exceptions are unchanged.
    """
    if input_reaches_protected_bytes(mt940_file_path):
        mark_protected_request()
        import frappe

        raise frappe.PermissionError(SAFE_DENIAL)

    reader = importlib.import_module(RAW_READER_MODULE)
    return getattr(reader, RAW_READER_METHOD)(data_import, mt940_file_path)


# ---------------------------------------------------------------------------
# ZIP marker propagation (supported whitelisted-method override)
# ---------------------------------------------------------------------------
def _zip_entries(files) -> tuple:
    """Iteration view native ``File.zip_files`` would use, for reusable forms only.

    Installed ``frappe.core.api.file.zip_files`` calls ``frappe.parse_json(files)``
    and passes the parsed value straight to ``File.zip_files``, which iterates it:
    a mapping yields its keys, a string yields its characters, a list/tuple yields
    its entries. Those reusable forms are mirrored here because JSON objects are a
    native way to name File docnames.

    Everything else is left untouched for native: malformed JSON, non-iterable
    scalars and one-shot iterators are not consumed or replaced, so native errors and
    responses are unchanged. Marking must not exhaust an iterator before native
    iteration.
    """
    if isinstance(files, str):
        try:
            parsed = json.loads(files)
        except (TypeError, ValueError):
            return ()
    else:
        parsed = files
    if isinstance(parsed, Mapping):
        return tuple(parsed.keys())
    if isinstance(parsed, (list, tuple, str)):
        return tuple(parsed)
    return ()


def _zip_entry_rows(name: str) -> list:
    """File row(s) for a ZIP entry: native resolves strings by File docname only."""
    import frappe

    return (
        frappe.db.get_all(
            "File",
            filters={"name": name},
            fields=list(FILE_LOOKUP_FIELDS),
            limit=0,
        )
        or []
    )


def mark_protected_zip_entries(files) -> None:
    """Set the response marker if any requested ZIP entry is protected.

    Runs before the native ``has_permission`` prefilter, so an entry that native will
    skip still produces the required no-store trio. It never authorizes, denies,
    reads bytes or writes response fields.
    """
    for entry in _zip_entries(files):
        if not isinstance(entry, str):
            continue
        if any(_is_protected_file(row) for row in _zip_entry_rows(entry)):
            mark_protected_request()
            return


def zip_files(files):
    """Mark protected ZIP entries, then delegate unchanged to the native endpoint.

    Installed ``core/api/file.py:131-135`` parses the argument and delegates to
    ``File.zip_files``; installed ``File.zip_files`` skips an entry at module-level
    ``has_permission`` before ``get_content``. Marking happens before that prefilter.
    This wrapper adds no authorization, denial, response field or streaming: it
    returns the native callable's result and exceptions unchanged.
    """
    mark_protected_zip_entries(files)
    module = importlib.import_module(NATIVE_ZIP_MODULE)
    return getattr(module, NATIVE_ZIP_METHOD)(files)


def _register_site_contract() -> None:
    """Keep the installed POST-only whitelist contract on the override.

    ``frappe.override_whitelisted_methods`` resolves the replacement callable and
    still requires it to be whitelisted for the original HTTP method. Applying the
    installed decorator here mirrors the replaced function without importing Frappe
    (or failing) outside a site process.
    """
    try:
        import frappe
    except ModuleNotFoundError:
        return

    global convert_mt940_to_csv, zip_files
    convert_mt940_to_csv = frappe.whitelist(methods=list(WHITELISTED_METHODS))(
        convert_mt940_to_csv
    )
    zip_files = frappe.whitelist()(zip_files)


_register_site_contract()
