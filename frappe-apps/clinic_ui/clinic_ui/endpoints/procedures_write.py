"""Converted development lane: Clinical Procedure create + completion writes.

This is the converted lane: it relies on Frappe/Marley document permissions,
validation, controller lifecycle and native transactions instead of the
historical operation fence, deployment marker, write budget and acknowledgement
barrier. Unresolved entries in that historical ledger are never read, changed or
used to block a converted write; a historical sealed creation alias still
resolves read-only through its committed creation receipt so existing links keep
working.

Order is fixed and test-asserted:

1. shared read gate (no-store, session, site configuration, clinic role),
2. explicit same-session CSRF token check (ignores framework bypasses),
3. default-off procedure-write capability for the exact approved site,
4. pinned boundary check and the installed-field preflight,
5. strict body validation; the creation body carries only the selected Service
   Request and an operation key, the completion body only the workspace
   procedure id and an operation key — never a docstatus, status, price list,
   item row or quantity,
6. target resolution: the server-owned allowlist, a historical sealed alias
   through its committed creation receipt (read-only), or the native docname
   itself under the current user's own native Clinical Procedure permission,
7. the current user's own native Clinical Procedure authority and the designed
   pre-mutation refusals (draft/completed/cancelled target, a stock-consuming
   procedure, missing price list, per-row batch/stock, linked customer, nursing
   checklist; absent, unsubmitted, non-procedure or already-linked Service
   Request),
8. the native row lock and the same decisions re-read against current rows,
9. the native mutation (the installed ``complete_procedure`` document method, or
   one insert-and-submit of the Clinical Procedure for the locked request), the
   closed-world re-read against the EXPECTED list and one explicit site commit,
10. the canonical result read back under the resolved native docname and the
    current user's own read authority, echoing only the identifier the caller
    supplied (or the native docname this lane created). A newly created
    procedure is addressed by that native docname.

There is one minimal, read-only inspection surface: ``view`` returns one
procedure's status from a workspace id in site configuration, a historical
creation alias the caller can verify, or a readable native docname, and
``by_service_request`` lists the procedures linked to one Service Request.
Neither resolves write authority nor reads a linked patient, practitioner or
template identifier, and discovery is bounded: an over-full, malformed or
ambiguous page is refused rather than truncated. It does not claim end-to-end or
reconciliation coverage.

Completion is restricted to procedures whose ``consume_stock`` flag is exactly
``False``: this lane never drives the installed stock-consuming lifecycle, so a
consuming procedure (or an unreadable flag) is refused before any mutation, both
before the row lock and again against the locked row. The ``write.stock.consume``
capability therefore stays ``read_only`` even while the procedure gate is open.

The remaining closed-world check asserts that the driven operation posted
nothing: it compares the zero Stock Ledger Entry / GL Entry delta against a
global baseline captured before dispatch. Under concurrent stock activity that
comparison can only over-count, which is reported as an uncertain outcome and
rolled back inside the same transaction, so it never turns an unverified
outcome into success. It is a verification aid, not a ledger reconciliation, and
it is not transaction-isolated against unrelated concurrent postings.

A post-dispatch failure of any kind is uncertain: the endpoint rolls back the
partial transaction (a rollback failure raises :class:`RollbackFailed` so Frappe
cleanup handles it) and returns ``409 {"code": "WRITE_UNCERTAIN"}``. It never
retries automatically and never claims success it has not read back. A designed
refusal — strict body, unknown target, ineligible state, missing master — happens
before any native mutation and is reported as ``422``, ``417`` or
``409 OPERATION_CONFLICT``.
"""

from clinic_ui.core import patient_write, procedure_contract, procedure_write
from clinic_ui.core.policy import PROCEDURE_ROLE_CODES, PROCEDURE_VIEW_ROLE_CODES
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import aliases, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
CONFLICT_CODE = "OPERATION_CONFLICT"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"

COMPLETE_RESULT = "completed"
CREATE_RESULT = "created"
PENDING_STATE = "pending"
COMPLETED_STATE = "completed"


class RollbackFailed(RuntimeError):
    """The failed native write could not be rolled back; fail loudly.

    Returning a normal response would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead.
    """


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape."""

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native procedure write rollback failed after a post-dispatch error"
        ) from exc


def _permission_denial(gateway, exc):
    """True only for the framework's own permission refusal type."""

    try:
        check = getattr(gateway, "is_native_permission_denial", None)
        if not callable(check):
            return False
        return check(exc) is True
    except Exception:
        return False


def _shared_gate(gateway, csrf_token):
    settings, view, role = common.authorize_procedure_write(
        gateway, PROCEDURE_ROLE_CODES, csrf_token
    )
    if gateway.native_procedure_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_procedure_install_problem() is not None:
        gateway.deny()
    return settings, view, role


def _resolve_target(gateway, settings, actor, workspace_id):
    """Resolve one workspace id to its native procedure, or ``None``.

    Three disjoint sources: the server-owned allowlist, a historical sealed
    alias whose current actor holds a committed creation receipt (read-only,
    never mutated), then the native docname itself — but only after the current
    user's own native Clinical Procedure read and write checks succeed. Any value
    in the ``wsp1_`` namespace, valid or malformed, is resolved only through the
    alias path and is never reinterpreted as a native docname.
    """

    native_procedure_id = settings.native_procedure_for(workspace_id)
    if native_procedure_id is not None:
        return native_procedure_id

    if procedure_write.claims_alias_namespace(workspace_id):
        operation_key = procedure_write.parse_alias(
            settings.epoch_secret, gateway.site, actor, workspace_id
        )
        if operation_key is None:
            return None
        try:
            return gateway.native_procedure_from_create_receipt(
                actor, operation_key
            )
        except Exception:
            # Read-only historical resolution never blocks a converted write.
            return None

    if gateway.native_procedure_can_read(workspace_id) is not True:
        return None
    if gateway.native_procedure_can_write(workspace_id) is not True:
        return None
    return workspace_id


def _success(
    gateway,
    result,
    report_id,
    native_procedure_id,
    state,
    *,
    stock_entry=None,
    completed=False,
    with_stock_entry=False,
):
    """Read the committed record back and build the canonical result.

    The read-back is always addressed by the resolved native docname — never by
    the workspace id, which may be a static allowlist id or a historical sealed
    alias. Only the identifier the caller itself supplied (or the native docname
    this lane created) is echoed back. A read-back that does not show the state
    this operation claimed is reported as an uncertain outcome, never as success.
    """

    try:
        detail = gateway.native_procedure_read_detail(native_procedure_id)
        if not isinstance(detail, dict):
            raise ValueError("native detail is missing after commit")
        status = detail.get("status")
        if not isinstance(status, str) or not status.strip():
            raise ValueError("native status is unreadable after commit")
        if detail.get("docstatus") != procedure_contract.FINAL_DOCSTATUS:
            raise ValueError("native row is not submitted after commit")
        if completed and status.strip() != procedure_contract.COMPLETED_STATUS:
            raise ValueError("native row is not completed after commit")
        if not completed and status.strip() in (
            procedure_contract.COMPLETED_STATUS,
            procedure_contract.CANCELLED_STATUS,
        ):
            raise ValueError("native row is already terminal after create")
        payload = {
            "status": result,
            "procedure": {
                "procedure_id": report_id,
                "state": state,
                "status": status,
            },
        }
        # The completion result always carries the boolean, including the
        # non-consuming case where no Stock Entry exists at all.
        if with_stock_entry:
            payload["stockEntryCreated"] = bool(stock_entry)
    except Exception:
        # The native write already committed; its outcome is not established.
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(HTTP_OK, payload)


def complete(gateway, body, csrf_token=None):
    """Complete one resolved procedure and verify the native side effects."""

    settings, view, role = _shared_gate(gateway, csrf_token)
    if role not in PROCEDURE_ROLE_CODES:
        gateway.deny()
    try:
        payload = procedure_write.validate_complete(body)
    except procedure_write.ProcedureWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    workspace_id = payload["procedure"]
    native_procedure_id = _resolve_target(
        gateway, settings, actor, workspace_id
    )
    if native_procedure_id is None:
        gateway.deny()

    if gateway.native_procedure_can_write(native_procedure_id) is not True:
        gateway.deny()
    detail = gateway.native_procedure_action_detail(native_procedure_id)
    if not isinstance(detail, dict):
        gateway.deny()

    if procedure_contract.target_problem(detail) is not None:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    # This lane posts no stock: a consuming procedure is a different workflow
    # and is refused before any mutation. A missing or ambiguous flag fails
    # closed rather than guessing.
    if detail.get("consume_stock") is not False:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    problems = procedure_contract.completion_problems(detail)
    if problems:
        return _respond_error(gateway, problems[0][0], VALIDATION_CODE)
    expected = procedure_contract.expected_side_effects(detail)
    if expected is None:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    # A baseline the lane cannot read leaves the side effects unverifiable, so
    # nothing is dispatched: this is a pre-mutation refusal, not an uncertain
    # write.
    try:
        baseline = gateway.native_stock_ledger_baseline(
            detail.get("items"), detail.get("warehouse")
        )
    except Exception:
        gateway.deny()

    # Phase one: the native row lock and its permission checks. Nothing has
    # been mutated yet, so a verified native permission denial is a 403.
    try:
        state = gateway.lock_procedure_target(native_procedure_id)
    except procedure_write.ProcedureTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception as exc:
        _rollback_or_raise(gateway)
        if _permission_denial(gateway, exc):
            gateway.deny()
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase two: the same designed decisions against the locked row, still
    # before any mutation.
    try:
        values = state.as_dict()
        if procedure_contract.target_problem(values) is not None:
            raise procedure_write.ProcedureTargetNotEligible()
        if values.get("consume_stock") is not False:
            raise procedure_write.ProcedureTargetNotEligible()
        if procedure_contract.completion_problems(values):
            raise procedure_write.ProcedureTargetNotEligible()
        locked_expected = procedure_contract.expected_side_effects(values)
        if locked_expected is None:
            raise procedure_write.ProcedureTargetNotEligible()
    except procedure_write.ProcedureTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase three: the native completion and the one commit. From here the stock
    # may already have moved, so every failure is uncertain.
    try:
        stock_entry = gateway.complete_native_procedure(state)
        facts = gateway.native_procedure_complete_facts(
            native_procedure_id, stock_entry, baseline
        )
        if (
            procedure_contract.side_effect_problem(facts, locked_expected)
            is not None
        ):
            raise patient_write.WriteUncertain()
        gateway.commit()
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _success(
        gateway,
        COMPLETE_RESULT,
        workspace_id,
        native_procedure_id,
        COMPLETED_STATE,
        stock_entry=stock_entry,
        completed=True,
        with_stock_entry=True,
    )


def create(gateway, body, csrf_token=None):
    """Create one Clinical Procedure from a submitted Service Request."""

    _settings, _view, role = _shared_gate(gateway, csrf_token)
    if role not in PROCEDURE_ROLE_CODES:
        gateway.deny()
    if gateway.native_procedure_can_create() is not True:
        gateway.deny()
    try:
        payload = procedure_write.validate_create(body)
    except procedure_write.ProcedureWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    service_request = payload["service_request"]
    try:
        values = gateway.native_service_request_target(service_request)
    except Exception:
        values = None
    if not isinstance(values, dict):
        gateway.deny()
    refused = procedure_contract.service_request_problem(values)
    if refused is not None:
        return _respond_error(
            gateway, procedure_contract.HTTP_REFUSED, VALIDATION_CODE
        )

    # Phase one: the row lock and the same creation decisions. Nothing has been
    # mutated yet, so a verified native permission denial is a 403.
    try:
        state = gateway.lock_service_request_target(service_request)
    except procedure_write.ProcedureTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception as exc:
        _rollback_or_raise(gateway)
        if _permission_denial(gateway, exc):
            gateway.deny()
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    try:
        if (
            procedure_contract.service_request_problem(state.as_dict())
            is not None
        ):
            raise procedure_write.ProcedureTargetNotEligible()
    except procedure_write.ProcedureTargetNotEligible:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # Phase two: one insert-and-submit, the closed-world re-read and one commit.
    try:
        procedure_id = gateway.create_native_procedure(state)
        facts = gateway.native_procedure_create_facts(procedure_id)
        expected = {
            "procedure_id": procedure_id,
            "patient": state.patient,
            "procedure_template": state.procedure_template,
            "service_request": service_request,
        }
        if (
            procedure_contract.create_facts_problem(facts, expected)
            is not None
        ):
            raise patient_write.WriteUncertain()
        gateway.commit()
    except Exception:
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    # A newly created synthetic procedure is addressed by its native docname.
    return _success(
        gateway, CREATE_RESULT, procedure_id, procedure_id, PENDING_STATE
    )


# -- read surface ----------------------------------------------------------
#
# Converted-workflow inspection only. Nothing here resolves write authority,
# locks a row, reads a linked identifier or dispatches a native mutation.

MAX_NATIVE_PROCEDURES = 200
MAX_PROCEDURE_STATUS_LENGTH = 64
PROCEDURE_DOCSTATUS_VALUES = (0, 1, 2)


def _strict_text(value, max_length):
    """Bounded, control-free text without surrounding whitespace, else ``None``."""

    if not is_valid_text(value, max_length) or value != value.strip():
        return None
    return value


def _read_gate(gateway):
    """Shared authenticated/no-store gate for the procedure read surface.

    The read lane is independent of the procedure write flag: turning writes off
    must not hide a record that already exists. It requires the authenticated
    session, the clinic role code (DOC/NUR) and the exact approved development
    site. Every record read afterwards still needs the current user's own native
    Clinical Procedure read permission.
    """

    settings, view, _role = common.authorize(gateway, PROCEDURE_VIEW_ROLE_CODES)
    if aliases.native_read_scope_enabled(gateway) is not True:
        gateway.deny()
    return settings, view


def _resolve_read(gateway, settings, actor, procedure_id):
    """Resolve one value to a readable native procedure, or ``None``.

    Three disjoint sources, in write-lane order: the server-owned allowlist, a
    historical sealed alias through its committed creation receipt (read-only),
    then the native docname itself under the current user's own native read
    permission. A value in our ``wsp1_`` namespace is never reinterpreted as a
    native docname, valid or malformed.

    A historical alias stays creator-bound: it resolves only for the actor that
    created it, because the alias is derived from that actor and the receipt is
    looked up for the caller. This read surface deliberately adds no cross-actor
    alias resolution, so another clinician's alias is refused rather than
    broadened; a record the caller may read is addressed by its native docname.
    """

    native_procedure_id = settings.native_procedure_for(procedure_id)
    if native_procedure_id is not None:
        return native_procedure_id

    if procedure_write.claims_alias_namespace(procedure_id):
        operation_key = procedure_write.parse_alias(
            settings.epoch_secret, gateway.site, actor, procedure_id
        )
        if operation_key is None:
            return None
        try:
            return gateway.native_procedure_from_create_receipt(
                actor, operation_key
            )
        except Exception:
            return None

    if gateway.native_procedure_can_read(procedure_id) is not True:
        return None
    return procedure_id


def _procedure_item(gateway, native_procedure_id):
    """Return the three-field item for one readable procedure, or ``None``."""

    try:
        detail = gateway.native_procedure_view(native_procedure_id)
    except Exception:
        return None
    if not isinstance(detail, dict):
        return None
    procedure_id = detail.get("procedure_id")
    status = detail.get("status")
    docstatus = detail.get("docstatus")
    if procedure_id is None or status is None:
        return None
    if (
        _strict_text(procedure_id, procedure_write.MAX_PROCEDURE_ID_LENGTH)
        is None
    ):
        return None
    if _strict_text(status, MAX_PROCEDURE_STATUS_LENGTH) is None:
        return None
    if (
        not isinstance(docstatus, int)
        or isinstance(docstatus, bool)
        or docstatus not in PROCEDURE_DOCSTATUS_VALUES
    ):
        return None
    return {
        "procedure_id": procedure_id,
        "status": status,
        "docstatus": docstatus,
    }


def view(gateway, procedure_id=None):
    """Return one readable procedure's status.

    The caller supplies a workspace id, a historical creation alias or a native
    docname; only the identifier it supplied is echoed back. Only the three read
    fields are returned — never a patient, practitioner, template or Service
    Request value. A malformed, unknown or unreadable value is a 403.
    """

    settings, session = _read_gate(gateway)
    if (
        _strict_text(procedure_id, procedure_write.MAX_WORKSPACE_ID_LENGTH)
        is None
    ):
        gateway.deny()
    try:
        native_procedure_id = _resolve_read(
            gateway, settings, session.user, procedure_id
        )
    except Exception:
        gateway.deny()
    if native_procedure_id is None:
        gateway.deny()

    item = _procedure_item(gateway, native_procedure_id)
    if item is None:
        gateway.deny()
    return {"procedure": {**item, "procedure_id": procedure_id}}


def by_service_request(gateway, service_request=None):
    """Return the readable procedures linked to one Service Request.

    The current user's native Service Request read permission is required
    first. The list is then read through the framework's permission-filtered
    ``get_list``; each row is re-checked with the current user's own native
    Clinical Procedure document and field-level read authority. An unreadable,
    malformed or duplicated row, or a page over the bound, refuses the whole
    read: a partial list must never look complete.
    """

    _read_gate(gateway)
    if (
        _strict_text(service_request, procedure_write.MAX_SERVICE_REQUEST_LENGTH)
        is None
    ):
        gateway.deny()
    if (
        gateway.native_doctype_can_read(
            procedure_contract.SERVICE_REQUEST_DOCTYPE, service_request
        )
        is not True
    ):
        gateway.deny()

    try:
        names = gateway.native_procedures_for_service_request(
            service_request, MAX_NATIVE_PROCEDURES
        )
    except Exception:
        gateway.deny()
    if not isinstance(names, (list, tuple)):
        gateway.deny()

    items = []
    seen = set()
    for name in names:
        if _strict_text(name, procedure_write.MAX_PROCEDURE_ID_LENGTH) is None:
            gateway.deny()
        if name in seen:
            gateway.deny()
        seen.add(name)
        item = _procedure_item(gateway, name)
        if item is None:
            gateway.deny()
        items.append(item)
    return {"items": items}
