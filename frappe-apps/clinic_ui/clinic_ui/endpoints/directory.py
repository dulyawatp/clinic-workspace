"""``GET /api/method/clinic_ui.api.directory.synthetic_patients``.

Lists three disjoint sources and nothing else:

* the server-owned configured allowlist,
* terminal ``COMMITTED`` historical create receipts discovered read-only in the
  old external write fence (never claimed, appended, acknowledged or latched),
* a bounded, permission-aware native ``Patient`` list for the current user, so
  patients created by the converted lane are discoverable by their native
  docname.

The endpoint accepts no client input: no workspace id, native id, filter, limit
or user parameter. The created source is a hard-bounded enumeration; each stored
receipt is decoded and its alias is re-derived from the recorded creator, site
and operation key. An inconsistent, malformed or ambiguous record is omitted.

The native source exists only on the exact approved development site and is
independent of the patient write flag, so disabling writes does not hide
records that already exist. It uses ``frappe.get_list`` (permission-aware),
never ``get_all`` or raw SQL, applies a hard item limit, and then reads each
display name under the same document and field-level native checks as every
other read. A denied, missing or malformed record is omitted without
description. A record already represented by a static alias or a historical
created alias is not listed twice, so historical links keep their stable alias.

The native slice is a single first page ordered by ``modified desc`` and capped
at ``MAX_NATIVE_PATIENTS`` (or the remaining response capacity). There is no
pagination cursor and no "more" signal in the client contract, so a site with
more readable Patients than that cap would silently omit the older records.
That is a bounded truncation, not pagination; it is acceptable only for the
isolated synthetic development site.

Every record still passes the *current* user's native read check before its
display name is read. A missing or corrupt fence leaves the other sources
intact and never falls back to an unfiltered Patient list. A created slice that
would push the combined response over the client's 1000-item limit is omitted
so no partial collision set is trusted.
"""

from collections import Counter

from clinic_ui.core import dto, patient_alias, patient_write
from clinic_ui.core.policy import DIRECTORY_ROLE_CODES
from clinic_ui.endpoints import aliases, common

# Hard bound on the permission-aware native slice. The combined response is
# further clamped to ``dto.MAX_DIRECTORY_ITEMS``.
MAX_NATIVE_PATIENTS = 200


def _static_items(gateway, settings):
    items = []
    aliases_seen = set()
    natives_seen = set()
    for workspace_patient_id, native_patient_id in settings.allowlist:
        aliases_seen.add(workspace_patient_id)
        natives_seen.add(native_patient_id)
        display_name = gateway.native_patient_display_name(native_patient_id)
        if display_name is None:
            continue
        try:
            items.append(dto.patient_item(workspace_patient_id, display_name))
        except ValueError:
            continue
    return items, aliases_seen, natives_seen


def _created_items(gateway, settings, static_aliases, static_natives):
    """Return ``(items, aliases, natives)`` for the historical slice, or ``None``."""

    try:
        records = gateway.list_committed_create_receipts(
            aliases.MAX_CREATED_RECEIPTS
        )
    except (patient_write.StoreUnavailable, patient_write.WriteUncertain):
        # Missing, corrupt or over-full enumeration: omit the whole created
        # slice rather than trust a truncated or partial candidate set.
        return None

    candidates = []
    for record in records:
        try:
            expected_alias = patient_alias.build_alias(
                settings.epoch_secret,
                gateway.site,
                record.actor,
                record.operation_key,
            )
            receipt_alias, native_patient_id = patient_write.decode_create_receipt(
                record.receipt
            )
        except (patient_alias.AliasError, patient_write.WriteInputError):
            continue
        if expected_alias != receipt_alias:
            continue
        if (
            receipt_alias in static_aliases
            or native_patient_id in static_natives
        ):
            continue
        candidates.append((receipt_alias, native_patient_id))

    alias_counts = Counter(alias for alias, _native in candidates)
    native_counts = Counter(native for _alias, native in candidates)

    items = []
    accepted_aliases = set()
    accepted_natives = set()
    for workspace_patient_id, native_patient_id in candidates:
        if alias_counts[workspace_patient_id] != 1:
            continue
        if native_counts[native_patient_id] != 1:
            continue
        display_name = gateway.native_patient_display_name(native_patient_id)
        if display_name is None:
            continue
        try:
            items.append(dto.patient_item(workspace_patient_id, display_name))
        except ValueError:
            continue
        accepted_aliases.add(workspace_patient_id)
        accepted_natives.add(native_patient_id)
    return items, accepted_aliases, accepted_natives


def _native_items(gateway, seen_aliases, seen_natives, available, names=None):
    """Bounded, permission-aware native discovery for the converted lane."""

    limit = min(MAX_NATIVE_PATIENTS, available)
    if limit <= 0:
        return []

    if names is None:
        names = gateway.list_readable_patients(limit)
    if not isinstance(names, (list, tuple)):
        return []

    items = []
    seen = set(seen_aliases) | set(seen_natives)
    for native_patient_id in names:
        if not isinstance(native_patient_id, str) or not native_patient_id.strip():
            continue
        if native_patient_id in seen:
            continue
        display_name = gateway.native_patient_display_name(native_patient_id)
        if display_name is None:
            continue
        try:
            items.append(dto.patient_item(native_patient_id, display_name))
        except ValueError:
            continue
        seen.add(native_patient_id)
    return items


def synthetic_patients(gateway):
    from clinic_ui.endpoints import production_read

    production = production_read.authorize_if_selected(gateway, DIRECTORY_ROLE_CODES)
    if production is not None:
        from clinic_ui.core import production_patient_ref
        from clinic_ui.runtime.production_read_gateway import exact_readable, patient_creation

        try:
            names = gateway.list_readable_patients(
                min(MAX_NATIVE_PATIENTS, dto.MAX_DIRECTORY_ITEMS)
            )
        except Exception:
            gateway.deny()
        if not isinstance(names, (list, tuple)) or len(names) > MAX_NATIVE_PATIENTS:
            gateway.deny()
        items = []
        seen = set()
        for name in names:
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            if not exact_readable(gateway, "Patient", name) or gateway.native_patient_can_read(name) is not True:
                continue
            display = gateway.native_patient_display_name(name)
            creation = patient_creation(gateway, name)
            if display is None or creation is None:
                continue
            try:
                ref = production_patient_ref.encode(
                    production_read.patient_ref_key(gateway), gateway.site, name, creation
                )
                items.append(dto.patient_item(ref, display))
            except ValueError:
                continue
        try:
            return dto.build_directory(items)
        except ValueError:
            gateway.deny()
    settings, _session, _role = common.authorize(gateway, DIRECTORY_ROLE_CODES)

    items, seen_aliases, seen_natives = _static_items(gateway, settings)
    available = dto.MAX_DIRECTORY_ITEMS - len(items)
    if available > 0:
        created = _created_items(
            gateway, settings, seen_aliases, seen_natives
        )
        if created is not None:
            created_items, created_aliases, created_natives = created
            if len(created_items) <= available:
                items.extend(created_items)
                seen_aliases.update(created_aliases)
                seen_natives.update(created_natives)
                available = dto.MAX_DIRECTORY_ITEMS - len(items)

    if available > 0 and aliases.native_read_scope_enabled(gateway):
        items.extend(
            _native_items(gateway, seen_aliases, seen_natives, available)
        )

    try:
        return dto.build_directory(items)
    except ValueError:
        gateway.deny()
