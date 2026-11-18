"""Explicit, site-owned configuration gate.

Every value is read from the site's own ``site_config.json`` through a caller
supplied getter. Nothing is defaulted, inferred or repaired: a missing,
malformed or ambiguous value makes the whole feature unavailable
(``load_settings`` returns ``None``) so the endpoints fail closed.

Keys
----
``clinic_ui_enabled``
    Truthy flag. Only ``True``/``1`` or ``"1"``/``"true"``/``"yes"`` enable it.
``clinic_ui_synthetic_site``
    The one site name this feature is allowed to run on. It must equal the
    current site exactly.
``clinic_ui_epoch_secret``
    At least 32 characters of server-owned secret entropy. Used only to derive
    the opaque session epoch; never returned, logged or stored elsewhere.
``clinic_ui_role_map``
    Mapping of clinic role code (``DOC``/``REC``/``NUR``/``PHA``/``CAS``/``MGR``/
    ``ADM``) to a non-empty list of native Frappe role names. Codes outside the
    supported set, duplicate role names and a role name shared by two codes are
    refused.
``clinic_ui_synthetic_patients``
    Server-owned allowlist of ``{"workspace_patient_id", "native_patient_id"}``
    objects. Order is preserved. Duplicates and extra keys are refused.
``clinic_ui_synthetic_appointments``
    Optional server-owned allowlist of
    ``{"workspace_appointment_id", "native_appointment_id"}`` objects. Missing
    or empty means an empty appointment list. Order is preserved and duplicates
    are refused. The workspace id is the only appointment identifier returned to
    the client; the native docname is never exposed.
``clinic_ui_native_patient_write_enabled``
    Separate, default-off capability flag for native Patient create/edit. It is
    not implied by ``clinic_ui_enabled``: both must be explicitly enabled.
``clinic_ui_native_patient_write_site``
    The one approved site on which native writes may run: exactly
    ``marley-sit.local``. It must equal the current resolved site; any other
    value (including the existing ``marley.local`` lab site) is refused. The
    runtime additionally requires a private deployment marker outside site
    configuration, so a copied config or hostname alone cannot enable writes.
``clinic_ui_native_appointment_write_enabled``
    Separate, default-off capability flag for native appointment booking,
    rescheduling and check-in. It is not implied by either other write flag.
``clinic_ui_native_appointment_write_site``
    The one approved site for appointment writes, exactly ``marley-sit.local``.
``clinic_ui_appointment_medical_department`` / ``clinic_ui_appointment_type`` /
``clinic_ui_appointment_company``
    Server-owned linked targets set on a new booking. None is ever supplied by
    the client; each must be a valid identifier and is re-checked against the
    current user's own native document read permission at request time. The
    department names a native ``Medical Department`` document, not a generic
    ``Department``.
``clinic_ui_appointment_checkin_patient``
    Workspace patient id of the dedicated same-day fixture. The check-in target
    itself is discovered from a committed booking receipt for that patient on the
    site's current date; this value is not authorization.
``clinic_ui_native_encounter_write_enabled``
    Separate, default-off capability flag for the Patient Encounter draft,
    complete and amend family. Not implied by the patient or appointment flags.
``clinic_ui_native_encounter_write_site``
    The one approved site for encounter writes, exactly ``marley-sit.local``.
``clinic_ui_encounter_practitioner`` / ``clinic_ui_encounter_company``
    Server-owned linked targets set on a new draft. Neither is ever supplied by
    the client; each must be a valid identifier and is re-checked against the
    current user's native document read permission at request time.
"""

from dataclasses import dataclass
from typing import Any

from clinic_ui.core.policy import SUPPORTED_ROLE_CODES
from clinic_ui.core.text import is_valid_identifier, normalize_identifier

ENABLED_KEY = "clinic_ui_enabled"
SITE_KEY = "clinic_ui_synthetic_site"
EPOCH_SECRET_KEY = "clinic_ui_epoch_secret"
ROLE_MAP_KEY = "clinic_ui_role_map"
ALLOWLIST_KEY = "clinic_ui_synthetic_patients"
APPOINTMENTS_KEY = "clinic_ui_synthetic_appointments"
WRITE_ENABLED_KEY = "clinic_ui_native_patient_write_enabled"
WRITE_SITE_KEY = "clinic_ui_native_patient_write_site"
# Separate, default-off appointment-write gate. It is independent of the patient
# write flag: booking, rescheduling and check-in each need this flag, the exact
# approved site and the server-owned target configuration below.
APPOINTMENT_WRITE_ENABLED_KEY = "clinic_ui_native_appointment_write_enabled"
APPOINTMENT_WRITE_SITE_KEY = "clinic_ui_native_appointment_write_site"
APPOINTMENT_MEDICAL_DEPARTMENT_KEY = "clinic_ui_appointment_medical_department"
APPOINTMENT_COMPANY_KEY = "clinic_ui_appointment_company"
APPOINTMENT_TYPE_KEY = "clinic_ui_appointment_type"
# The dedicated same-day fixture for check-in, named by its workspace patient id.
# The check-in target itself is discovered from a committed booking receipt; this
# only says which synthetic patient the dedicated record belongs to.
APPOINTMENT_CHECKIN_PATIENT_KEY = "clinic_ui_appointment_checkin_patient"
# Separate, default-off encounter-write gate. Independent of the patient and
# appointment flags: draft, complete and amend each need this flag, the exact
# approved site and the server-owned practitioner/company targets below.
ENCOUNTER_WRITE_ENABLED_KEY = "clinic_ui_native_encounter_write_enabled"
ENCOUNTER_WRITE_SITE_KEY = "clinic_ui_native_encounter_write_site"
ENCOUNTER_PRACTITIONER_KEY = "clinic_ui_encounter_practitioner"
ENCOUNTER_COMPANY_KEY = "clinic_ui_encounter_company"
# Separate, default-off procedure-completion gate. Independent of the other
# write flags: completion needs this flag, the exact approved site and the
# server-owned procedure allowlist below.
PROCEDURE_WRITE_ENABLED_KEY = "clinic_ui_native_procedure_write_enabled"
PROCEDURE_WRITE_SITE_KEY = "clinic_ui_native_procedure_write_site"
# Server-owned allowlist of the workspace procedure targets this surface may
# complete. The workspace id is the only procedure identifier returned to the
# client; the native docname is never exposed.
PROCEDURES_KEY = "clinic_ui_synthetic_procedures"
# Separate, default-off admin users + masters gate. Independent of every other
# write flag: creating a user or a master needs this flag and the exact
# approved site. The role allowlist, duplicates and native authority are decided
# per request by the admin contract and endpoint.
ADMIN_WRITE_ENABLED_KEY = "clinic_ui_native_admin_write_enabled"
ADMIN_WRITE_SITE_KEY = "clinic_ui_native_admin_write_site"
# Separate, default-off stock receive/transfer/adjust gate. Independent of every
# other write flag: each movement needs this flag and the exact approved site.
# Masters, batches and native authority are decided per request.
STOCK_WRITE_ENABLED_KEY = "clinic_ui_native_stock_write_enabled"
STOCK_WRITE_SITE_KEY = "clinic_ui_native_stock_write_site"
# Separate, default-off purchasing (draft/submit + PO-referenced receipt) gate.
# Independent of the stock and clinical flags: every purchasing operation needs
# this flag and the exact approved site. Masters, the PO target and native
# authority are decided per request.
PURCHASE_WRITE_ENABLED_KEY = "clinic_ui_native_purchase_write_enabled"
PURCHASE_WRITE_SITE_KEY = "clinic_ui_native_purchase_write_site"
# Separate, default-off finance (invoice submit + payment receive) gate.
# Independent of the purchasing and clinical flags: both finance operations need
# this flag and the exact approved site. Customers, price lists, accounts, modes
# of payment and every amount are decided per request.
FINANCE_WRITE_ENABLED_KEY = "clinic_ui_native_finance_write_enabled"
FINANCE_WRITE_SITE_KEY = "clinic_ui_native_finance_write_site"
# The discount sub-capability's permission condition (CSV:123's CAS +
# permission qualifier, folded to configuration): the discount path exists only
# when the installed child is present AND this flag is on. No rate is ever
# configured or invented here.
INVOICE_DISCOUNT_ENABLED_KEY = "clinic_ui_invoice_discount_enabled"

# The one and only site identity on which this local experiment may run. Exact
# string equality against the resolved current site is required; the configured
# name alone, a copied site_config.json or a matching hostname is not enough
# because the runtime additionally requires a deployment-bound private marker
# that matches the initialized fence anchor (see ``deployment_marker``).
APPROVED_WRITE_SITE = "marley-sit.local"
# Appointment writes share the same single approved site. A separate constant
# keeps the two gates independent to configure and to test.
APPROVED_APPOINTMENT_WRITE_SITE = APPROVED_WRITE_SITE

# Refused by construction even if the approved-name check were misread. The
# existing lab site must never inherit writes from a copied configuration.
DISALLOWED_WRITE_SITES = frozenset({"marley.local"})

MIN_EPOCH_SECRET_LENGTH = 32
MAX_ROLE_NAME_LENGTH = 140
MAX_ALLOWLIST_ENTRIES = 1000

TRUTHY = frozenset({"1", "true", "yes"})

ALLOWLIST_KEYS = frozenset({"workspace_patient_id", "native_patient_id"})
APPOINTMENT_ALLOWLIST_KEYS = frozenset(
    {"workspace_appointment_id", "native_appointment_id"}
)
PROCEDURE_ALLOWLIST_KEYS = frozenset(
    {"workspace_procedure_id", "native_procedure_id"}
)


@dataclass(frozen=True)
class Settings:
    role_map: tuple
    allowlist: tuple
    epoch_secret: bytes
    write_enabled: bool = False
    appointment_allowlist: tuple = ()
    appointment_write_enabled: bool = False
    appointment_medical_department: str | None = None
    appointment_type: str | None = None
    appointment_company: str | None = None
    appointment_checkin_patient: str | None = None
    encounter_write_enabled: bool = False
    encounter_practitioner: str | None = None
    encounter_company: str | None = None
    procedure_write_enabled: bool = False
    procedure_allowlist: tuple = ()
    admin_write_enabled: bool = False
    stock_write_enabled: bool = False
    purchase_write_enabled: bool = False
    finance_write_enabled: bool = False
    invoice_discount_enabled: bool = False

    def as_role_map(self):
        return {code: tuple(roles) for code, roles in self.role_map}

    def native_procedure_for(self, workspace_procedure_id):
        """Return the native docname for one approved workspace procedure id."""

        for workspace, native in self.procedure_allowlist:
            if workspace == workspace_procedure_id:
                return native
        return None


def _enabled(raw: Any) -> bool:
    if raw is True:
        return True
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw == 1
    if isinstance(raw, str):
        return raw.strip().lower() in TRUTHY
    return False


def _role_map(raw: Any):
    if not isinstance(raw, dict) or not raw:
        return None

    parsed = {}
    seen_role_names = set()
    for code, roles in raw.items():
        if code not in SUPPORTED_ROLE_CODES:
            return None
        if not isinstance(roles, (list, tuple)) or not roles:
            return None
        names = []
        for role in roles:
            if not isinstance(role, str):
                return None
            name = role.strip()
            if not is_valid_identifier(name) or len(name) > MAX_ROLE_NAME_LENGTH:
                return None
            if name in names or name in seen_role_names:
                return None
            names.append(name)
            seen_role_names.add(name)
        parsed[code] = tuple(names)

    return tuple(sorted(parsed.items()))


def _allowlist(raw: Any):
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) > MAX_ALLOWLIST_ENTRIES:
        return None

    entries = []
    identifiers = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != ALLOWLIST_KEYS:
            return None
        workspace_id = normalize_identifier(entry["workspace_patient_id"])
        native_id = normalize_identifier(entry["native_patient_id"])
        if not is_valid_identifier(workspace_id) or not is_valid_identifier(native_id):
            return None
        # The workspace and native namespaces must stay disjoint: a client value
        # that equals any native docname could otherwise be mistaken for an
        # alias and leak record identity through a resolution attempt.
        if (
            workspace_id == native_id
            or workspace_id in identifiers
            or native_id in identifiers
        ):
            return None
        identifiers.update((workspace_id, native_id))
        entries.append((workspace_id, native_id))

    return tuple(entries)


def _appointment_allowlist(raw: Any):
    """Parse the optional appointment allowlist; absent means empty."""

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) > MAX_ALLOWLIST_ENTRIES:
        return None

    entries = []
    identifiers = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != APPOINTMENT_ALLOWLIST_KEYS:
            return None
        workspace_id = normalize_identifier(entry["workspace_appointment_id"])
        native_id = normalize_identifier(entry["native_appointment_id"])
        if not is_valid_identifier(workspace_id) or not is_valid_identifier(native_id):
            return None
        # A workspace appointment reference must not collide with any native
        # appointment docname, including across entries, so the published id
        # can never be read back as a native identifier.
        if (
            workspace_id == native_id
            or workspace_id in identifiers
            or native_id in identifiers
        ):
            return None
        identifiers.update((workspace_id, native_id))
        entries.append((workspace_id, native_id))

    return tuple(entries)


def _procedure_allowlist(raw: Any):
    """Parse the optional procedure allowlist; absent means empty.

    Completion targets are server-owned: a client value selects one entry and
    never names a native document. The same workspace/native disjointness rule
    as the patient and appointment lists applies.
    """

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) > MAX_ALLOWLIST_ENTRIES:
        return None

    entries = []
    identifiers = set()
    for entry in raw:
        if not isinstance(entry, dict) or set(entry) != PROCEDURE_ALLOWLIST_KEYS:
            return None
        workspace_id = normalize_identifier(entry["workspace_procedure_id"])
        native_id = normalize_identifier(entry["native_procedure_id"])
        if not is_valid_identifier(workspace_id) or not is_valid_identifier(native_id):
            return None
        if (
            workspace_id == native_id
            or workspace_id in identifiers
            or native_id in identifiers
        ):
            return None
        identifiers.update((workspace_id, native_id))
        entries.append((workspace_id, native_id))

    return tuple(entries)


def _procedure_namespaces_disjoint(
    patient_allowlist, appointment_allowlist, procedure_allowlist
):
    """True when no public id of any family equals any native id of any family.

    The procedure list joins the one client-visible public namespace and the one
    server-owned native namespace. A collision in either cross-type direction
    would let a client value be mistaken for a native identifier.
    """

    lists = (patient_allowlist, appointment_allowlist, procedure_allowlist)
    public_ids = set()
    native_ids = set()
    for entries in lists:
        public_ids.update(workspace for workspace, _native in entries)
        native_ids.update(native for _workspace, native in entries)
    return public_ids.isdisjoint(native_ids)


def _public_native_namespaces_disjoint(patient_allowlist, appointment_allowlist):
    """True when no public workspace id equals any native docname.

    Patient and appointment ids share one client-visible public namespace and
    one server-owned native namespace. A collision in either cross-type
    direction would let a client value be mistaken for a native identifier.
    """

    public_ids = {workspace for workspace, _native in patient_allowlist}
    public_ids.update(workspace for workspace, _native in appointment_allowlist)
    native_ids = {native for _workspace, native in patient_allowlist}
    native_ids.update(native for _workspace, native in appointment_allowlist)
    return public_ids.isdisjoint(native_ids)


def _write_enabled(getter, site):
    """Native writes are off unless every write key names the approved site.

    The site must be exactly ``APPROVED_WRITE_SITE`` and the configured name
    must equal it as well. A malformed, absent or copied configuration never
    disables the read-only surfaces; it only leaves ``write_enabled`` false.
    This is configuration intent only: every request still performs its own
    native permission checks. The converted patient lane no longer requires the
    historical private deployment marker or fence anchor.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False
    if not _enabled(getter(WRITE_ENABLED_KEY)):
        return False
    configured = getter(WRITE_SITE_KEY)
    if not isinstance(configured, str):
        return False
    if configured != APPROVED_WRITE_SITE:
        return False
    return True


def _appointment_write_settings(getter, site, allowlist):
    """Return ``(enabled, medical department, type, company, checkin patient)``.

    Appointment writes are off unless the flag, the exact approved site and all
    three server-owned targets are present and valid. The dedicated check-in
    patient must name a workspace patient from the explicit synthetic allowlist,
    so a copied configuration cannot point check-in at an unknown record. Any
    malformed value leaves ``enabled`` false and returns no target values; the
    read surfaces are unaffected.
    """

    disabled = (False, None, None, None, None)
    if site != APPROVED_APPOINTMENT_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return disabled
    if not _enabled(getter(APPOINTMENT_WRITE_ENABLED_KEY)):
        return disabled
    configured = getter(APPOINTMENT_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_APPOINTMENT_WRITE_SITE:
        return disabled

    department = normalize_identifier(getter(APPOINTMENT_MEDICAL_DEPARTMENT_KEY))
    appointment_type = normalize_identifier(getter(APPOINTMENT_TYPE_KEY))
    company = normalize_identifier(getter(APPOINTMENT_COMPANY_KEY))
    checkin_patient = normalize_identifier(getter(APPOINTMENT_CHECKIN_PATIENT_KEY))
    for value in (department, appointment_type, company, checkin_patient):
        if not is_valid_identifier(value) or len(value) > MAX_ROLE_NAME_LENGTH:
            return disabled
    if checkin_patient not in {workspace for workspace, _native in allowlist}:
        return disabled
    return (
        True,
        department.strip(),
        appointment_type.strip(),
        company.strip(),
        checkin_patient.strip(),
    )


def _encounter_write_settings(getter, site):
    """Return ``(enabled, practitioner, company)`` for the encounter family.

    The encounter gate needs the exact approved site, its own flag and both
    server-owned targets. Any malformed value leaves ``enabled`` false and
    returns no targets; the read surfaces are unaffected.
    """

    disabled = (False, None, None)
    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return disabled
    if not _enabled(getter(ENCOUNTER_WRITE_ENABLED_KEY)):
        return disabled
    configured = getter(ENCOUNTER_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return disabled
    practitioner = normalize_identifier(getter(ENCOUNTER_PRACTITIONER_KEY))
    company = normalize_identifier(getter(ENCOUNTER_COMPANY_KEY))
    for value in (practitioner, company):
        if not is_valid_identifier(value) or len(value) > MAX_ROLE_NAME_LENGTH:
            return disabled
    return True, practitioner.strip(), company.strip()


def _procedure_write_settings(getter, site):
    """Return ``True`` when the procedure-completion gate is open.

    The gate needs the exact approved site and its own flag. The completion
    target itself is decided per request from the server-owned procedure
    allowlist, never from configuration alone.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False
    if not _enabled(getter(PROCEDURE_WRITE_ENABLED_KEY)):
        return False
    configured = getter(PROCEDURE_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return False
    return True


def _admin_write_settings(getter, site):
    """Return ``True`` when the admin user/master gate is open.

    The gate needs the exact approved site and its own flag. The role
    allowlist, duplicates and native authority are decided per request by the
    admin contract and endpoint, never from configuration alone.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False
    if not _enabled(getter(ADMIN_WRITE_ENABLED_KEY)):
        return False
    configured = getter(ADMIN_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return False
    return True


def _stock_write_settings(getter, site):
    """Return ``True`` when the stock receive/transfer/adjust gate is open.

    The gate needs the exact approved site and its own flag. Masters, batches,
    quantities and native authority are decided per request by the stock
    contract and endpoint, never from configuration alone.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False
    if not _enabled(getter(STOCK_WRITE_ENABLED_KEY)):
        return False
    configured = getter(STOCK_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return False
    return True


def _purchase_write_settings(getter, site):
    """Return ``True`` when the purchasing draft/submit/receipt gate is open.

    The gate needs the exact approved site and its own flag. Masters, the
    purchase-order target, quantities and native authority are decided per
    request by the purchase contract and endpoint, never from configuration
    alone.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False
    if not _enabled(getter(PURCHASE_WRITE_ENABLED_KEY)):
        return False
    configured = getter(PURCHASE_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return False
    return True


def _finance_write_settings(getter, site):
    """Return ``(enabled, discount_enabled)`` for the finance family.

    The gate needs the exact approved site and its own flag. The discount path
    needs that gate plus its own permission flag; the installed ``deductions``
    child is checked separately from metadata, so a site without the child still
    lands invoices (the recorded fallback cut) and only loses the discount.
    """

    if site != APPROVED_WRITE_SITE or site in DISALLOWED_WRITE_SITES:
        return False, False
    if not _enabled(getter(FINANCE_WRITE_ENABLED_KEY)):
        return False, False
    configured = getter(FINANCE_WRITE_SITE_KEY)
    if not isinstance(configured, str) or configured != APPROVED_WRITE_SITE:
        return False, False
    return True, _enabled(getter(INVOICE_DISCOUNT_ENABLED_KEY))


def load_settings(getter, site):
    """Return validated settings, or ``None`` when the feature is unavailable."""

    if not callable(getter):
        return None
    if not isinstance(site, str) or not site.strip():
        return None

    if not _enabled(getter(ENABLED_KEY)):
        return None

    configured_site = getter(SITE_KEY)
    if not isinstance(configured_site, str) or not configured_site.strip():
        return None
    if configured_site.strip() != site:
        return None

    secret = getter(EPOCH_SECRET_KEY)
    if not isinstance(secret, str) or len(secret) < MIN_EPOCH_SECRET_LENGTH:
        return None

    role_map = _role_map(getter(ROLE_MAP_KEY))
    if role_map is None:
        return None

    allowlist = _allowlist(getter(ALLOWLIST_KEY))
    if allowlist is None:
        return None

    appointment_allowlist = _appointment_allowlist(getter(APPOINTMENTS_KEY))
    if appointment_allowlist is None:
        return None
    if not _public_native_namespaces_disjoint(allowlist, appointment_allowlist):
        return None

    (
        appointment_write_enabled,
        appointment_medical_department,
        appointment_type,
        appointment_company,
        appointment_checkin_patient,
    ) = _appointment_write_settings(getter, site, allowlist)

    (
        encounter_write_enabled,
        encounter_practitioner,
        encounter_company,
    ) = _encounter_write_settings(getter, site)

    procedure_allowlist = _procedure_allowlist(getter(PROCEDURES_KEY))
    if procedure_allowlist is None:
        return None
    if not _procedure_namespaces_disjoint(
        allowlist, appointment_allowlist, procedure_allowlist
    ):
        return None

    finance_write_enabled, invoice_discount_enabled = _finance_write_settings(
        getter, site
    )

    return Settings(
        role_map=role_map,
        allowlist=allowlist,
        epoch_secret=secret.encode("utf-8"),
        write_enabled=_write_enabled(getter, site),
        appointment_allowlist=appointment_allowlist,
        appointment_write_enabled=appointment_write_enabled,
        appointment_medical_department=appointment_medical_department,
        appointment_type=appointment_type,
        appointment_company=appointment_company,
        appointment_checkin_patient=appointment_checkin_patient,
        encounter_write_enabled=encounter_write_enabled,
        encounter_practitioner=encounter_practitioner,
        encounter_company=encounter_company,
        procedure_write_enabled=_procedure_write_settings(getter, site),
        procedure_allowlist=procedure_allowlist,
        admin_write_enabled=_admin_write_settings(getter, site),
        stock_write_enabled=_stock_write_settings(getter, site),
        purchase_write_enabled=_purchase_write_settings(getter, site),
        finance_write_enabled=finance_write_enabled,
        invoice_discount_enabled=invoice_discount_enabled,
    )
