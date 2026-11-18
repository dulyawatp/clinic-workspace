"""Resolve a workspace patient identifier for the converted native lane.

Three disjoint sources are accepted:

* the unchanged server-owned allowlist from site configuration,
* a sealed historical alias whose current actor has a terminal ``COMMITTED``
  create receipt in the old external fence (read-only lookup; the ledger is
  never written, acknowledged, latched or made ready), and
* the native ``Patient`` docname itself, but only on the exact approved
  development site, and only after the current user passes the matching native
  document read or write permission check.

The native-docname read source is scoped by site only, so disabling patient
writes never hides a record that already exists. The native-docname write source
additionally requires the development write configuration. Neither is an
unrestricted query or alias guess: a single supplied value is checked against
Frappe's own document permission for the current user. An alias-shaped value is
never reinterpreted as a native docname.

The write path keeps the actor-bound resolution below. The read path adds one
narrow exception: a DOC or NUR caller may resolve another clinician's historical
alias, but only through a terminal committed create receipt that re-derives to
the exact alias, and only after the caller's own native Patient read permission
is checked by the gateway.
"""

from dataclasses import dataclass

from clinic_ui.core import config, patient_alias, patient_write, write_fence
from clinic_ui.core.policy import CROSS_ACTOR_READ_ROLE_CODES

MAX_CREATED_RECEIPTS = write_fence.MAX_RECEIPT_ENUMERATION

SOURCE_STATIC = "static"
SOURCE_CREATED = "created"
SOURCE_NATIVE = "native"


def native_read_scope_enabled(gateway):
    """True only on the exact approved development site.

    Native-docname reads and permission-aware discovery exist for
    ``marley-sit.local`` so a created record stays reachable by its native
    docname. This is deliberately independent of the patient write flag: turning
    writes off must not hide records that already exist. Each read is still
    gated by the current user's own native read permission. Any error keeps the
    legacy behavior.
    """

    try:
        return gateway.site == config.APPROVED_WRITE_SITE
    except Exception:
        return False


def native_write_scope_enabled(gateway, settings):
    """True only for the development write configuration on the approved site.

    Native-docname resolution for a write additionally requires the development
    write settings, so a site with writes disabled cannot address records by
    native docname. Every write still needs the current user's native write
    permission. Any error keeps the legacy behavior.
    """

    try:
        return (
            gateway.site == config.APPROVED_WRITE_SITE
            and settings.write_enabled is True
        )
    except Exception:
        return False


@dataclass(frozen=True)
class ResolvedPatient:
    """One resolved record; ``creator`` is ``None`` outside a created receipt."""

    native_patient_id: str
    source: str
    creator: str | None = None


def _static_lookup(settings, alias):
    for allowed_alias, native_patient_id in settings.allowlist:
        if allowed_alias == alias:
            return native_patient_id
    return None


def _is_alias_shaped(alias):
    """True when the value is one of our sealed aliases, valid or not."""

    return patient_alias.alias_operation_key(alias) is not None


def _receipt_native(settings, gateway, actor, alias):
    """Read-only historical resolution through a committed create receipt.

    Any fence unavailability, unresolved record or malformed receipt simply
    resolves nothing; it never locks a converted write and never mutates the
    ledger.
    """

    operation_key = patient_alias.parse_alias(
        settings.epoch_secret, gateway.site, actor, alias
    )
    if operation_key is None:
        return None

    try:
        receipt = gateway.lookup_create_receipt(actor, operation_key)
    except (
        patient_write.WriteLocked,
        patient_write.WriteConflict,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return None
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


def resolve_write(gateway, settings, actor, alias):
    """Return the actor-bound resolution, or ``None`` without denying.

    This is the exact resolution the write path uses: static allowlist first,
    then the actor's own committed historical create receipt, then a native
    docname the actor may write. It is exposed separately so the read-only
    ``editable`` computation can default to ``False`` on any uncertainty
    instead of turning a successful read into a 403.
    """

    native_patient_id = _static_lookup(settings, alias)
    if native_patient_id is not None:
        return native_patient_id

    native_patient_id = _receipt_native(settings, gateway, actor, alias)
    if native_patient_id is not None:
        return native_patient_id

    if _is_alias_shaped(alias):
        return None
    if native_write_scope_enabled(gateway, settings) and (
        gateway.native_patient_can_write(alias) is True
    ):
        return alias
    return None


def resolve(gateway, settings, actor, alias):
    """Return the resolved native docname for a write, or deny."""

    native_patient_id = resolve_write(gateway, settings, actor, alias)
    if native_patient_id is None:
        gateway.deny()
    return native_patient_id


def _creator_receipt(gateway, settings, actor, alias):
    native_patient_id = _receipt_native(settings, gateway, actor, alias)
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

    Static aliases resolve as before. A historical created alias resolves for
    its creator through the actor-bound receipt, or for a different DOC/NUR
    clinician only through the verified committed create receipt. A value that
    is not alias-shaped may resolve as a native docname, but only after the
    current user's native Patient read check succeeds.
    """

    native_patient_id = _static_lookup(settings, alias)
    if native_patient_id is not None:
        return ResolvedPatient(native_patient_id, SOURCE_STATIC)

    resolved = _creator_receipt(gateway, settings, actor, alias)
    if resolved is not None:
        return resolved

    if (
        native_read_scope_enabled(gateway)
        and not _is_alias_shaped(alias)
        and gateway.native_patient_can_read(alias) is True
    ):
        return ResolvedPatient(alias, SOURCE_NATIVE)

    return _cross_actor_receipt(gateway, settings, role, alias)
