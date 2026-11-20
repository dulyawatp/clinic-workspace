"""``GET /api/method/clinic_ui.api.session.bootstrap`` implementation.

Three distinct outcomes, never merged:

- **anonymous/expired** (no session, guest session id, guest or blank user):
  a fixed ``{"code": "AUTH_EXPIRED"}`` body with HTTP 401, so the client can
  reach sign-in. No CSRF token, site configuration, epoch or identity is read.
- **authenticated but unauthorized** (administrator, disabled user, wrong or
  ambiguous role, unavailable configuration): the framework permission error
  (403) with a generic message.
- **authorized**: live session description, opaque epoch, capability map and
  the server-generated session CSRF token. The map is read-only except that the
  two patient write capabilities may be enabled for a DOC/NUR session after the
  full reviewed local write gate passes.

The raw session identifier, the cookie and the epoch secret never enter a
payload and are never logged.
"""

from clinic_ui.core import dto
from clinic_ui.core import finance_contract
from clinic_ui.core import write_fence
from clinic_ui.core.capabilities import bootstrap_capabilities
from clinic_ui.core.epoch import session_epoch
from clinic_ui.core.policy import (
    ADMIN_ROLE_CODES,
    ADJUST_ROLE_CODES,
    APPOINTMENT_WRITE_ROLE_CODES,
    ENCOUNTER_COMPLETE_ROLE_CODES,
    ENCOUNTER_ROLE_CODES,
    INVOICE_ROLE_CODES,
    PATIENT_WRITE_ROLE_CODES,
    PAYMENT_ROLE_CODES,
    PO_ROLE_CODES,
    PROCEDURE_ROLE_CODES,
    STOCK_ROLE_CODES,
    SUPPORTED_ROLE_CODES,
)
from clinic_ui.endpoints import common

def _patient_write_flags(gateway, settings, role):
    """Return ``(create, update)`` after the converted native write gate.

    The converted development lane relies on Frappe/Marley permissions instead
    of the historical deployment marker and persistent fence readiness. All
    remaining conditions must pass: the exact-site write configuration,
    Healthcare Settings without automatic side effects, and the caller's native
    Patient permission. Any false result, non-boolean result or raised error
    fails both capabilities closed. No patient record is read.
    """

    if role not in PATIENT_WRITE_ROLE_CODES:
        return False, False
    try:
        if not settings.write_enabled:
            return False, False
        if gateway.healthcare_settings_side_effects_unsafe() is not False:
            return False, False
        return (
            gateway.native_patient_can_create() is True,
            gateway.native_patient_can_write() is True,
        )
    except Exception:
        return False, False


def _appointment_write_flags(gateway, settings, role):
    """Return ``(book, reschedule, cancel, no_show, checkin)`` after the gate.

    The gate is independent of the patient write flag and of the historical
    deployment marker and fence store. It requires the exact approved site, the
    appointment flag, safe appointment settings and the caller's own native
    Patient Appointment and linked-doctype reads. Per-record targets are decided
    by the write endpoints; these flags only advertise that the surface exists.
    """

    disabled = (False, False, False, False, False)
    if role not in APPOINTMENT_WRITE_ROLE_CODES:
        return disabled
    try:
        if gateway.native_appointment_boundary_problem() is not None:
            return disabled
        if not settings.appointment_write_enabled:
            return disabled
        if settings.appointment_medical_department is None:
            return disabled
        if settings.appointment_type is None:
            return disabled
        if settings.appointment_company is None:
            return disabled
        if gateway.healthcare_appointment_side_effects_unsafe() is not False:
            return disabled
        if gateway.native_appointment_can_create() is not True:
            return disabled
        if gateway.native_appointment_can_write() is not True:
            return disabled
        for doctype, doc in (
            ("Company", settings.appointment_company),
            ("Appointment Type", settings.appointment_type),
            ("Medical Department", settings.appointment_medical_department),
            ("Gender", None),
        ):
            if gateway.native_doctype_can_read(doctype, doc) is not True:
                return disabled
        type_contract = gateway.native_appointment_type_contract(
            settings.appointment_type
        )
        if (
            type_contract is None
            or type_contract["allow_booking_for"] != "Department"
        ):
            return disabled
        return True, True, True, True, True
    except Exception:
        return disabled


def _encounter_write_flags(gateway, settings, role):
    """Return ``(draft, complete, amend)`` after the encounter write gate.

    Draft (and the native lab ordering that rides it) admits DOC and NUR;
    completion and amendment are DOC-only. Medication ordering is not advertised:
    the draft contract refuses any non-empty ``medications`` section before the
    fence until a reviewed medication contract exists. The gate is independent of the other
    write flags: exact approved site, encounter flag, deployment marker, ready
    fence store, safe Healthcare Settings, installed-field preflight, no
    boundary drift and the caller's own native authority.
    """

    disabled = (False, False, False)
    if role not in ENCOUNTER_ROLE_CODES:
        return disabled
    try:
        if gateway.native_encounter_boundary_problem() is not None:
            return disabled
        if gateway.native_encounter_install_problem() is not None:
            return disabled
        if not settings.encounter_write_enabled:
            return disabled
        if settings.encounter_practitioner is None:
            return disabled
        if settings.encounter_company is None:
            return disabled
        if gateway.write_deployment_approved() is not True:
            return disabled
        if gateway.write_fence_ready() is not True:
            return disabled
        if gateway.healthcare_encounter_side_effects_unsafe() is not False:
            return disabled
        if gateway.native_patient_can_read is None:
            return disabled
        for doctype, doc in (
            ("Healthcare Practitioner", settings.encounter_practitioner),
            ("Company", settings.encounter_company),
        ):
            if gateway.native_doctype_can_read(doctype, doc) is not True:
                return disabled
        draft_ok = gateway.native_encounter_can_create() is True
        complete_ok = (
            role in ENCOUNTER_COMPLETE_ROLE_CODES
            and gateway.native_encounter_can_write() is True
            and gateway.native_clinical_note_can_create() is True
        )
        return draft_ok, complete_ok, complete_ok
    except Exception:
        return disabled


def _procedure_write_flags(gateway, settings, role):
    """Return ``(complete, create)`` when the procedure surface is open.

    Independent of the other write flags: exact approved site, boundary and
    installed-field preflight, the procedure flag and the caller's own native
    Clinical Procedure authority. The historical deployment marker and fence
    store no longer gate the converted lane. The target, its price list, batch
    and stock state are decided per request; these flags only advertise that the
    surface exists.
    """

    disabled = (False, False)
    if role not in PROCEDURE_ROLE_CODES:
        return disabled
    try:
        if gateway.native_procedure_boundary_problem() is not None:
            return disabled
        if gateway.native_procedure_install_problem() is not None:
            return disabled
        if not settings.procedure_write_enabled:
            return disabled
        create_ok = gateway.native_procedure_can_create() is True
        complete_ok = gateway.native_procedure_can_write() is True
        return complete_ok, create_ok
    except Exception:
        return disabled


def _stock_write_flags(gateway, settings, role):
    """Return ``(receive, transfer, adjust)`` when each stock surface is open.

    Independent of every other write flag: exact approved site and boundary for
    all three, the stock flag, and the caller's own native authority on the
    document it would submit. Receive stays on the historical fence, so it still
    needs the shared installed-field preflight, the deployment marker and a
    ready fence store. The converted adjustment and transfer lanes use the same
    boundary check as their endpoints but not that legacy preflight (their
    endpoints deliberately rely on the controller instead), so only native
    create and submit authority decide whether they are advertised. Masters,
    quantities, warehouses, batch and every other native document field are
    decided per request; these flags only advertise that the surface exists.
    """

    disabled = (False, False, False)
    try:
        if gateway.native_stock_boundary_problem() is not None:
            return disabled
        if not settings.stock_write_enabled:
            return disabled
        legacy_preflight_ok = gateway.native_stock_install_problem() is None
        legacy_ready = (
            legacy_preflight_ok
            and gateway.write_deployment_approved() is True
            and gateway.write_fence_ready() is True
        )
        receive_ok = (
            legacy_ready
            and role in STOCK_ROLE_CODES
            and gateway.native_stock_can_create("Purchase Receipt") is True
        )
        transfer_ok = (
            role in STOCK_ROLE_CODES
            and gateway.native_stock_can_create("Stock Entry") is True
            and gateway.native_stock_can_submit("Stock Entry") is True
        )
        adjust_ok = (
            role in (STOCK_ROLE_CODES | ADJUST_ROLE_CODES)
            and gateway.native_stock_can_create("Stock Reconciliation") is True
            and gateway.native_stock_can_submit("Stock Reconciliation") is True
        )
        return receive_ok, transfer_ok, adjust_ok
    except Exception:
        return disabled


def _purchase_write_flags(gateway, settings, role):
    """Return ``(draft, submit, receipt)`` for the purchasing surfaces.

    The converted draft lane is advertised by the exact approved site, the
    purchasing flag, the draft-specific installed-field preflight and the
    caller's own native Purchase Order create permission; it no longer reads the
    deployment marker or the fence store. Submit and the PO-referenced receipt
    stay on the historical fence: they are advertised only while the strict
    fenced-family preflight, the deployment marker and a ready fence store all
    hold (no latch, no unresolved key or barrier), so an unresolved ledger keeps
    both surfaces disabled.

    Masters, the purchase-order target and the quantities are decided per
    request; these flags only advertise that the surface exists. The
    PO-referenced receipt is part of the purchase family, so its session
    authorization uses the purchase role set rather than the inventory one; its
    capability id stays ``write.stock.receive`` because there is one receipt
    write.
    """

    disabled = (False, False, False)
    if role not in PO_ROLE_CODES:
        return disabled
    try:
        if gateway.native_purchase_boundary_problem() is not None:
            return disabled
        if not settings.purchase_write_enabled:
            return disabled
        draft_ok = (
            gateway.native_purchase_draft_install_problem() is None
            and gateway.native_purchase_can_create("Purchase Order") is True
        )
        fenced_ok = (
            gateway.native_purchase_install_problem() is None
            and gateway.write_deployment_approved() is True
            and gateway.write_fence_ready() is True
        )
        submit_ok = draft_ok and fenced_ok
        receipt_ok = (
            fenced_ok
            and gateway.native_purchase_can_create("Purchase Receipt") is True
        )
        return draft_ok, submit_ok, receipt_ok
    except Exception:
        return disabled


def _finance_write_flags(gateway, settings, role, actor):
    """Return ``(invoice, payment)`` when each finance surface is open.

    Independent of the purchasing and clinical flags: exact approved site,
    boundary and installed-field preflight, the finance flag and the caller's
    own native authority on the document it would write. The converted invoice
    lane needs create and submit authority and does not need the historical
    deployment marker or fence store. The fenced payment lane still needs both.
    Customers, price lists, accounts, modes of payment and the discount
    permission are decided per request; these flags only advertise that the
    surface exists.
    """

    disabled = (False, False)
    if role not in (INVOICE_ROLE_CODES | PAYMENT_ROLE_CODES):
        return disabled
    try:
        if gateway.native_finance_boundary_problem() is not None:
            return disabled
        if not settings.finance_write_enabled:
            return disabled
        invoice_ok = (
            gateway.native_finance_invoice_install_problem() is None
            and gateway.native_finance_can_write("Sales Invoice") is True
            and gateway.native_finance_can_submit("Sales Invoice") is True
            and all(
                gateway.native_doctype_can_read(doctype) is True
                for doctype in (
                    finance_contract.CUSTOMER_DOCTYPE,
                    finance_contract.ITEM_DOCTYPE,
                    finance_contract.COMPANY_DOCTYPE,
                    finance_contract.INVOICE_DOCTYPE,
                    finance_contract.GL_ENTRY_DOCTYPE,
                    finance_contract.PAYMENT_LEDGER_DOCTYPE,
                )
            )
        )
    except Exception:
        return disabled
    try:
        payment_ok = (
            role in PAYMENT_ROLE_CODES
            and actor != write_fence.LATCH_ACTOR
            and gateway.native_finance_settlement_verified() is True
            and gateway.native_finance_payment_install_problem() is None
            and gateway.write_deployment_approved() is True
            and gateway.write_fence_ready() is True
            and gateway.native_finance_can_write("Payment Entry") is True
            and gateway.native_finance_can_submit("Payment Entry") is True
            and gateway.native_doctype_can_read("Sales Invoice") is True
        )
        if payment_ok:
            limit = finance_contract.budget(finance_contract.PAYMENT_KIND)
            records = gateway.list_committed_finance_receipts(
                limit, kind=finance_contract.PAYMENT_KIND
            )
            payment_ok = len(records) < limit
    except Exception:
        payment_ok = False
    return invoice_ok, payment_ok


def _admin_write_flags(gateway, settings, role):
    """Return ``(user, master)`` when each admin surface is open.

    The converted user lane is native: the exact approved site, the installed
    preflight, the admin flag, the ADM role code and the caller's own native
    ``User`` create authority. It does not need the historical deployment
    marker or fence store.

    The master lane (and its acknowledgement) remains on the fenced path and
    additionally needs the private deployment marker and a ready fence store.
    The role allowlist and duplicate checks are decided per request; these
    flags only advertise that each surface exists.
    """

    disabled = (False, False)
    if role not in ADMIN_ROLE_CODES:
        return disabled
    try:
        if gateway.native_admin_install_problem() is not None:
            return disabled
        if not settings.admin_write_enabled:
            return disabled
        if gateway.native_admin_can_write() is not True:
            return disabled
    except Exception:
        return disabled

    try:
        if gateway.write_deployment_approved() is not True:
            return True, False
        if gateway.write_fence_ready() is not True:
            return True, False
    except Exception:
        return True, False
    return True, True


def bootstrap(gateway):
    common.begin(gateway)

    view = common.live_view(gateway)
    if view is None:
        return gateway.respond_anonymous()

    common.require_authorized_session(gateway, view)
    settings = common.settings_or_deny(gateway)
    role = common.role_or_deny(gateway, view, settings, SUPPORTED_ROLE_CODES)

    csrf_token = gateway.csrf_token()
    if not dto.is_valid_text(csrf_token, dto.MAX_CSRF_TOKEN_LENGTH):
        gateway.deny()

    try:
        epoch = session_epoch(settings.epoch_secret, gateway.site, view.sid)
        write_create, write_update = _patient_write_flags(gateway, settings, role)
        (
            write_appointment_book,
            write_appointment_reschedule,
            write_appointment_cancel,
            write_appointment_no_show,
            write_appointment_checkin,
        ) = _appointment_write_flags(gateway, settings, role)
        (
            write_encounter_draft,
            write_encounter_complete,
            write_encounter_amend,
        ) = _encounter_write_flags(gateway, settings, role)
        write_procedure_complete, write_procedure_create = _procedure_write_flags(
            gateway, settings, role
        )
        (
            write_stock_receive,
            write_stock_transfer,
            write_stock_adjust,
        ) = _stock_write_flags(gateway, settings, role)
        write_admin_user, write_admin_master = _admin_write_flags(
            gateway, settings, role
        )
        (
            write_purchase_order_draft,
            write_purchase_order_submit,
            write_purchase_receipt,
        ) = _purchase_write_flags(gateway, settings, role)
        write_invoice_submit, write_payment_receive = _finance_write_flags(
            gateway, settings, role, view.user
        )
        capabilities = bootstrap_capabilities(
            write_patient_create=write_create,
            write_patient_update=write_update,
            write_appointment_book=write_appointment_book,
            write_appointment_reschedule=write_appointment_reschedule,
            write_appointment_cancel=write_appointment_cancel,
            write_appointment_no_show=write_appointment_no_show,
            write_appointment_checkin=write_appointment_checkin,
            write_encounter_draft=write_encounter_draft,
            write_encounter_complete=write_encounter_complete,
            write_encounter_amend=write_encounter_amend,
            write_procedure_complete=write_procedure_complete,
            write_procedure_create=write_procedure_create,
            write_stock_receive=(
                write_stock_receive or write_purchase_receipt
            ),
            write_stock_transfer=write_stock_transfer,
            write_stock_adjust=write_stock_adjust,
            write_purchase_order_draft=write_purchase_order_draft,
            write_purchase_order_submit=write_purchase_order_submit,
            write_invoice_submit=write_invoice_submit,
            write_payment_receive=write_payment_receive,
            write_admin_user=write_admin_user,
            write_admin_master=write_admin_master,
        )
        result = dto.build_bootstrap(
            session_epoch=epoch,
            user_id=view.user,
            display_name=view.full_name,
            role=role,
            csrf_token=csrf_token,
            capabilities=capabilities,
        )
        if write_payment_receive:
            result["paymentSettlement"] = "full_balance_v1"
        return result
    except ValueError:
        gateway.deny()
