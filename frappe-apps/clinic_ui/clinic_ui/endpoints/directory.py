"""``GET /api/method/clinic_ui.api.directory.synthetic_patients``.

Lists two disjoint sources and nothing else:

* the server-owned configured allowlist, and
* terminal ``COMMITTED`` create receipts discovered read-only in the external
  write fence.

The endpoint accepts no client input: no workspace id, native id, filter, limit
or user parameter. The created source is a hard-bounded enumeration; each stored
receipt is decoded and its alias is re-derived from the recorded creator, site
and operation key. An inconsistent, malformed or ambiguous record is omitted.
Records that collide with the static allowlist or with each other are omitted.

Every record still passes the *current* user's native read check before its
display name is read. A denied, missing or malformed record is omitted without
description. A missing or corrupt fence leaves the static directory intact and
never falls back to an unfiltered Patient list. A full enumeration page, or a
created slice that would push the combined response over the client's 1000-item
limit, omits the whole created slice so no partial collision set is trusted. The
native docname never appears in the response.
"""

from collections import Counter

from clinic_ui.core import dto, patient_alias, patient_write
from clinic_ui.core.policy import DIRECTORY_ROLE_CODES
from clinic_ui.endpoints import aliases, common


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
    return items


def synthetic_patients(gateway):
    settings, _session, _role = common.authorize(gateway, DIRECTORY_ROLE_CODES)

    items, static_aliases, static_natives = _static_items(gateway, settings)
    available = dto.MAX_DIRECTORY_ITEMS - len(items)
    if available > 0:
        created = _created_items(
            gateway, settings, static_aliases, static_natives
        )
        if created is not None and len(created) <= available:
            items.extend(created)

    try:
        return dto.build_directory(items)
    except ValueError:
        gateway.deny()
