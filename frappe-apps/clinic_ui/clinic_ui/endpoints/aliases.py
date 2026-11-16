"""Resolve a workspace alias without widening the seeded allowlist.

Two disjoint sources are accepted:

* the unchanged server-owned allowlist from site configuration, and
* a sealed alias whose current actor has a terminal ``COMMITTED`` create
  receipt in the external fence.

The second source is never a native docname lookup. A missing, dispatched,
unknown or malformed receipt denies (read) or locks (write). No Patient becomes
eligible merely because it exists in the database.

The write path keeps the actor-bound resolution below. The read path adds one
narrow exception: a DOC or NUR caller may resolve another clinician's created
alias, but only through a terminal committed create receipt that re-derives to
the exact alias from the recorded creator, site and operation key, and only
after the caller's own native Patient read permission is checked by the gateway.
"""

from dataclasses import dataclass

from clinic_ui.core import patient_alias, patient_write, write_fence
from clinic_ui.core.policy import CROSS_ACTOR_READ_ROLE_CODES

MAX_CREATED_RECEIPTS = write_fence.MAX_RECEIPT_ENUMERATION

SOURCE_STATIC = "static"
SOURCE_CREATED = "created"


@dataclass(frozen=True)
class ResolvedPatient:
    """One resolved record; ``creator`` is ``None`` for the static allowlist."""

    native_patient_id: str
    source: str
    creator: str | None = None


def _static_lookup(settings, alias):
    for allowed_alias, native_patient_id in settings.allowlist:
        if allowed_alias == alias:
            return native_patient_id
    return None


def resolve_write(gateway, settings, actor, alias):
    """Return the actor-bound write resolution, or ``None`` without denying.

    This is the exact resolution the write path uses: static allowlist first,
    then the current actor's own committed create receipt. It is exposed
    separately so the read-only ``editable`` computation can default to
    ``False`` on any uncertainty instead of turning a successful read into a
    403.
    """

    native_patient_id = _static_lookup(settings, alias)
    if native_patient_id is not None:
        return native_patient_id

    operation_key = patient_alias.parse_alias(
        settings.epoch_secret, gateway.site, actor, alias
    )
    if operation_key is None:
        return None

    receipt = gateway.lookup_create_receipt(actor, operation_key)
    if receipt is None:
        return None
    try:
        receipt_alias, native_patient_id = patient_write.decode_create_receipt(
            receipt
        )
    except patient_write.WriteInputError:
        return None
    if receipt_alias != alias:
        return None
    return native_patient_id


def resolve(gateway, settings, actor, alias):
    """Return the native docname for the actor-bound write resolution.

    Static aliases and the current actor's own committed create receipt are
    accepted. Another clinician's created alias is never resolved here; the
    write endpoint deliberately keeps its original actor-bound behaviour.
    """

    native_patient_id = resolve_write(gateway, settings, actor, alias)
    if native_patient_id is None:
        gateway.deny()
    return native_patient_id


def _creator_receipt(gateway, settings, actor, alias):
    native_patient_id = resolve_write(gateway, settings, actor, alias)
    if native_patient_id is None:
        return None
    return ResolvedPatient(native_patient_id, SOURCE_CREATED, actor)


def _cross_actor_receipt(gateway, settings, role, alias):
    if role not in CROSS_ACTOR_READ_ROLE_CODES:
        gateway.deny()

    operation_key = patient_alias.alias_operation_key(alias)
    if operation_key is None:
        gateway.deny()

    records = gateway.list_committed_create_receipts(
        MAX_CREATED_RECEIPTS, operation_key
    )

    matches = []
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
        if expected_alias != alias or receipt_alias != alias:
            continue
        matches.append((record.actor, native_patient_id))

    if len(matches) != 1:
        gateway.deny()

    creator, native_patient_id = matches[0]
    return ResolvedPatient(native_patient_id, SOURCE_CREATED, creator)


def resolve_read(gateway, settings, actor, role, alias):
    """Return a read resolution without widening write authority.

    Static aliases resolve as before. A created alias resolves for its creator
    through the actor-bound receipt, or for a different DOC/NUR clinician only
    through the verified committed create receipt. Exactly one matching receipt
    is required; duplicate or inconsistent records deny.
    """

    native_patient_id = _static_lookup(settings, alias)
    if native_patient_id is not None:
        return ResolvedPatient(native_patient_id, SOURCE_STATIC)

    resolved = _creator_receipt(gateway, settings, actor, alias)
    if resolved is not None:
        return resolved

    return _cross_actor_receipt(gateway, settings, role, alias)
