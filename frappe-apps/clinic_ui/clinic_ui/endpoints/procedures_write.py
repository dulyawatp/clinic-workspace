"""Designed Clinical Procedure completion write (default off).

Order is fixed and test-asserted:

1. shared read gate plus the explicit same-session CSRF and procedure-write
   deployment gates,
2. pinned boundary check and the installed-field preflight; either drift denies
   before validation,
3. strict body validation; the client sends only the workspace procedure id and
   an operation key — never a native docname, docstatus, status, price list, item
   row or quantity,
4. the server-owned procedure allowlist resolves the one approved native target;
   an unknown workspace id is refused before the fence,
5. pre-fence target decisions: a draft, a completed or a cancelled procedure is
   refused here (exactly-once: a second completion would submit a second Stock
   Entry), and the price list, per-row batch/stock validity and the linked
   customer are decided with a named line each (417),
6. the durable budget must remain,
7. the persistent site-wide fence, then the native row lock and the same
   pre-fence decisions again against current rows,
8. the installed ``complete_procedure`` document method, a closed-world re-read
   against the EXPECTED side-effect list, one explicit commit and the terminal
   receipt,
9. the one-shot acknowledgement token.

Every designed refusal in 3-6 happens **before** the fence claim. A refusal
inside a held fence would terminate the operation ``UNKNOWN`` and latch the whole
site. A post-dispatch failure is uncertain: the key stays locked and the response
is ``409 {"code": "WRITE_LOCKED"}`` with no retry.
"""

from clinic_ui.core import (
    patient_write,
    procedure_contract,
    procedure_write,
)
from clinic_ui.core.policy import PROCEDURE_ROLE_CODES
from clinic_ui.endpoints import common

HTTP_OK = 200
HTTP_REFUSED = procedure_contract.HTTP_REFUSED
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"

COMPLETE_RESULT = "completed"


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class ManualInvestigationRequired(RuntimeError):
    """Neither the site latch nor the operation fallback could be persisted."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _locked_response(gateway):
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def _mark_unknown(claim, reason):
    try:
        claim.unknown(reason)
    except Exception:
        return


def _rollback_or_raise(gateway):
    try:
        gateway.rollback()
    except BaseException as exc:
        raise patient_write.WriteUncertain(
            "native procedure write rollback failed"
        ) from exc


def _latch_or_raise(gateway, actor, kind, operation_key, reason):
    try:
        gateway.latch_manual_investigation(reason)
        return
    except Exception:
        try:
            gateway.latch_operation_uncertain(actor, kind, operation_key, reason)
            return
        except Exception as exc:
            raise ManualInvestigationRequired(reason) from exc


def _budget_remaining(gateway, kind):
    """Durable budget from committed receipts; an over-full page is exhausted."""

    limit = procedure_write.budget(kind)
    try:
        records = gateway.list_committed_procedure_receipts(limit, kind=kind)
    except procedure_write.ProcedureBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise procedure_write.ProcedureBudgetExhausted()


def _shared_gate(gateway, csrf_token):
    settings, view, role = common.authorize_procedure_write(
        gateway, PROCEDURE_ROLE_CODES, csrf_token
    )
    if gateway.native_procedure_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_procedure_install_problem() is not None:
        gateway.deny()
    return settings, view, role


def _digest_fields(payload):
    return {"procedure": payload["procedure"]}


def _finish_fence(gateway, claim, alias, native_procedure_id, stock_entry):
    gateway.commit()
    receipt = procedure_write.encode_receipt(
        kind=procedure_contract.PROCEDURE_COMPLETE_KIND,
        alias=alias,
        native_procedure_id=native_procedure_id,
        stock_entry=stock_entry,
    )
    claim.commit(receipt)
    return receipt


def _success(gateway, alias, stock_entry, ack_token):
    try:
        payload = {
            "status": COMPLETE_RESULT,
            "procedure": {
                "procedure_id": alias,
                "state": "completed",
                "status": procedure_contract.COMPLETED_STATUS,
            },
            "ackToken": ack_token,
            "stockEntryCreated": bool(stock_entry),
        }
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _refusal(gateway, problems):
    """Return the first named pre-fence refusal as the fixed safe envelope.

    The installed refusal text is not echoed: the response carries the one safe
    code for an unprocessable request and the contract keeps the named line.
    """

    if not problems:
        return None
    return _respond_error(gateway, problems[0][0], VALIDATION_CODE)


def complete(gateway, body, csrf_token=None):
    """Complete one approved procedure and verify the native side effects."""

    settings, view, role = _shared_gate(gateway, csrf_token)
    if role not in PROCEDURE_ROLE_CODES:
        # The role map already admitted this caller to a clinical surface;
        # completion is a narrower DOC-only decision taken pre-fence.
        gateway.deny()
    try:
        payload = procedure_write.validate_complete(body)
    except procedure_write.ProcedureWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    alias = payload["procedure"]
    native_procedure_id = settings.native_procedure_for(alias)
    if native_procedure_id is None:
        return _locked_response(gateway)

    try:
        detail = gateway.native_procedure_action_detail(native_procedure_id)
    except (patient_write.WriteLocked, patient_write.StoreUnavailable):
        return _locked_response(gateway)
    if detail is None:
        gateway.deny()
    if procedure_contract.target_problem(detail) is not None:
        return _locked_response(gateway)
    if gateway.native_procedure_can_write(native_procedure_id) is not True:
        gateway.deny()

    refused = _refusal(gateway, procedure_contract.completion_problems(detail))
    if refused is not None:
        return refused

    expected = procedure_contract.expected_side_effects(detail)
    if expected is None:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    kind = procedure_contract.PROCEDURE_COMPLETE_KIND
    try:
        digest = procedure_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
        if budget_ok:
            baseline = gateway.native_stock_ledger_baseline(
                detail.get("items"), detail.get("warehouse")
            )
    except procedure_write.ProcedureWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    stock_entry = None
    try:
        with gateway.procedure_write_fence(
            actor, kind, payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, kind)
                gateway.reset_native_read_snapshot()
                fence_settings, fence_view, fence_role = _shared_gate(
                    gateway, csrf_token
                )
                if fence_view.user != actor:
                    raise patient_write.WriteUncertain()
                if fence_role not in PROCEDURE_ROLE_CODES:
                    gateway.deny()
                if (
                    fence_settings.native_procedure_for(alias)
                    != native_procedure_id
                ):
                    raise patient_write.UnsafeSideEffect(
                        "configured procedure target changed"
                    )
                state = gateway.lock_procedure_target(native_procedure_id)
                values = state.as_dict()
                if procedure_contract.target_problem(values) is not None:
                    raise procedure_write.ProcedureTargetNotEligible()
                if procedure_contract.completion_problems(values):
                    raise procedure_write.ProcedureTargetNotEligible()
                expected = procedure_contract.expected_side_effects(values)
                if expected is None:
                    raise patient_write.WriteUncertain()
                stock_entry = gateway.complete_native_procedure(state)
                facts = gateway.native_procedure_complete_facts(
                    native_procedure_id, stock_entry, baseline
                )
                if (
                    procedure_contract.side_effect_problem(facts, expected)
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                receipt = _finish_fence(
                    gateway, claim, alias, native_procedure_id, stock_entry
                )
            except procedure_write.ProcedureTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except procedure_write.ProcedureBudgetExhausted:
                _rollback_or_raise(gateway)
                raise
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.WriteUncertain,
                patient_write.UnsafeSideEffect,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(
                    claim,
                    "native Clinical Procedure completion outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except procedure_write.ProcedureTargetNotEligible:
        return _locked_response(gateway)
    except procedure_write.ProcedureBudgetExhausted:
        return _locked_response(gateway)
    except patient_write.WriteLocked:
        return _locked_response(gateway)
    except patient_write.WriteConflict:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StoreUnavailable:
        return _locked_response(gateway)
    except patient_write.WriteUncertain:
        return _locked_response(gateway)
    except patient_write.UnsafeSideEffect:
        gateway.deny()

    try:
        ack_token = procedure_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(gateway, alias, stock_entry, ack_token)
    except (SuccessUncertain, procedure_write.ProcedureWriteError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit procedure completion response construction failed",
        )
        return _locked_response(gateway)


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed procedure completion."""

    settings, view, _role = common.authorize_procedure_write(
        gateway, PROCEDURE_ROLE_CODES, csrf_token
    )
    try:
        payload = procedure_write.validate_ack(body)
        status = gateway.acknowledge_procedure_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except procedure_write.ProcedureWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if status != "acked":
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    return gateway.respond_write(HTTP_OK, {"acknowledged": True})
