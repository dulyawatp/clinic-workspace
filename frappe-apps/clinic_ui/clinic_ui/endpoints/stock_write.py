"""Designed stock receive writes and the converted transfer / adjust lanes.

``receive`` is unchanged and stays on the historical fence:

1. shared read gate plus the explicit same-session CSRF and stock-write
   deployment gates, then the pinned boundary and installed-field preflight;
   either drift denies before validation,
2. strict body validation; the client sends masters, a quantity and (for a new
   batch) its expiry — never a docstatus, posting date, valuation, ledger row or
   native docname,
3. the native create authority on the document this operation would submit,
4. every pre-fence refusal with a named line: unknown item/supplier/warehouse,
   a missing, foreign or expired batch, insufficient stock at the source
   warehouse (the installed ``Insufficient Stock`` / ``Insufficient Stock for
   Batch`` titles), a missing or equal target warehouse, over-receipt against a
   referenced purchase order and the durable budget,
5. the pre-fence Stock Ledger / Bin baseline used by the delta re-read,
6. the persistent site-wide fence, then the same decisions again against
   current rows,
7. one mutation unit through the installed submittable document, a closed-world
   re-read against the EXPECTED side-effect list, the encoded terminal receipt,
   one explicit commit and its append,
8. the one-shot acknowledgement token.

``transfer`` and ``adjust`` are the **converted lanes**. The client sends the
native document fields themselves — ``company``, ``item_code``, ``qty``,
``source_warehouse``, ``target_warehouse`` and an optional batch for the
``Material Transfer``; ``purpose``, ``company``, ``item_code``, ``warehouse``,
``qty``, ``valuation_rate``, ``expense_account`` and an optional batch for the
``Stock Reconciliation`` — and each endpoint inserts and submits exactly one
document through the installed controller. Frappe/Marley permissions, Select
and quantity validation, posting-time rules, lifecycle and the transaction do
the deciding; no value is guessed, substituted or checked again here. Both
still require the authenticated session, the same-session CSRF token, the exact
approved site, the stock flag, an existing clinic stock role code, the pinned
boundary and the caller's own native create and submit authority (which grants
nothing). The success envelope returns the native identifier and docstatus. A
site with no reviewed reason field means the reason is simply not part of the
payload and nothing claims it was stored.

After a dispatch the endpoint rolls the partial transaction back first. A
failed rollback raises :class:`RollbackFailed` to Frappe cleanup instead of
returning a response, since a normal response could let the framework's
request-end commit persist the partial write. Once the rollback succeeded the
installed framework's own refusal keeps its meaning: its ``ValidationError``
family (including ``MandatoryError`` and ``LinkValidationError``) answers
``422 {"code": "VALIDATION"}`` and its ``PermissionError`` is a 403 denial.
Any other post-dispatch failure is uncertain and answers
``409 {"code": "WRITE_UNCERTAIN"}`` with no retry.
"""

from clinic_ui.core import patient_write, stock_contract
from clinic_ui.core.policy import ADJUST_ROLE_CODES, STOCK_ROLE_CODES
from clinic_ui.endpoints import common

HTTP_OK = 200
HTTP_REFUSED = stock_contract.HTTP_REFUSED
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"

RECEIVED_RESULT = "received"
TRANSFERRED_RESULT = "transferred"
ADJUSTED_RESULT = "adjusted"


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class ManualInvestigationRequired(RuntimeError):
    """Neither the site latch nor the operation fallback could be persisted."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _locked_response(gateway):
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def _refusal(gateway, problems):
    """Return the first named pre-fence refusal as the fixed safe envelope."""

    if not problems:
        return None
    return _respond_error(gateway, problems[0][0], VALIDATION_CODE)


def _mark_unknown(claim, reason):
    try:
        claim.unknown(reason)
    except Exception:
        return


class RollbackFailed(RuntimeError):
    """The failed write could not be rolled back; the request must fail loudly.

    Returning a normal 409 would let Frappe's end-of-request commit for an
    unsafe method persist a partial native write. The caller propagates this to
    Frappe cleanup instead. The fence key is left as the failing path left it —
    ``UNKNOWN`` on the generic path, otherwise ``DISPATCHED``, or already
    ``COMMITTED`` if the terminal append had persisted before the error. An
    unresolved ``UNKNOWN``/``DISPATCHED`` key blocks new operations until it is
    reconciled; a persisted ``COMMITTED`` key keeps its one-shot acknowledgement
    available under the recovery conditions the ack endpoint checks. It derives
    from ``RuntimeError`` alone, so neither the ``WriteUncertain`` handler nor
    the fence's ``FenceError`` translation turns it into a response.
    """


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape.

    Do not swallow this: a normal error response could trigger Frappe's implicit
    request-end commit. The generic handler marks the operation ``UNKNOWN``
    (best effort) before calling this; the named handlers mark nothing, so their
    key stays ``DISPATCHED``. Either way the fence is retained when the rollback
    itself fails, and this raises :class:`RollbackFailed` instead of responding.
    """

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native stock write rollback failed"
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

    limit = stock_contract.budget(kind)
    try:
        records = gateway.list_committed_stock_receipts(limit, kind=kind)
    except stock_contract.StockBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise stock_contract.StockBudgetExhausted()


def _shared_gate(gateway, csrf_token, allowed_codes):
    settings, view, role = common.authorize_stock_write(
        gateway, allowed_codes, csrf_token
    )
    if role not in allowed_codes:
        gateway.deny()
    if gateway.native_stock_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_stock_install_problem() is not None:
        gateway.deny()
    return settings, view, role


ADJUST_ROLES = STOCK_ROLE_CODES | ADJUST_ROLE_CODES


def _native_gate(gateway, csrf_token, allowed_codes):
    """Converted stock-write gate: session, CSRF, exact site, flag, role code.

    The clinic gate accepts the existing stock or adjustment role code, so the
    pharmacy Stock Manager reaches the endpoint exactly as Frappe already
    permits; this grants nothing. The caller's own native create and submit
    authority on the document is the real permission. No operation fence, write
    budget, receipt, acknowledgement token or deployment marker is read. The
    pinned boundary stays. The fenced receive lane keeps ``_shared_gate``
    unchanged.
    """

    settings, view, role = common.authorize_native_stock_write(
        gateway, allowed_codes, csrf_token
    )
    if role not in allowed_codes:
        gateway.deny()
    if gateway.native_stock_boundary_problem() is not None:
        gateway.deny()
    return settings, view, role


DOCUMENTS = {
    stock_contract.RECEIVE_KIND: stock_contract.PURCHASE_RECEIPT_DOCTYPE,
    stock_contract.TRANSFER_KIND: stock_contract.STOCK_ENTRY_DOCTYPE,
    stock_contract.ADJUST_KIND: stock_contract.STOCK_RECONCILIATION_DOCTYPE,
}

RESULT_STATUS = {
    stock_contract.RECEIVE_KIND: RECEIVED_RESULT,
    stock_contract.TRANSFER_KIND: TRANSFERRED_RESULT,
    stock_contract.ADJUST_KIND: ADJUSTED_RESULT,
}


def _digest_fields(payload):
    fields = {}
    for name, value in payload.items():
        if name == "operation_key":
            continue
        if isinstance(value, float):
            fields[name] = format(value, ".6f")
        else:
            fields[name] = "" if value is None else str(value)
    return fields


def _prepare_receive(detail, payload):
    item = detail.get("item") or {}
    batch_id = payload.get("batch_id")
    create_batch = bool(
        item.get("has_batch_no")
        and batch_id
        and not (detail.get("batch") or {}).get("exists")
    )
    return {
        "item_code": payload["item_code"],
        "qty": payload["qty"],
        "uom": item.get("stock_uom"),
        "warehouse": payload["warehouse"],
        "supplier": payload["supplier"],
        "purchase_order": payload.get("purchase_order"),
        "po_item": detail.get("po_item"),
        "batch_no": batch_id,
        "batch_expiry": payload.get("batch_expiry"),
        "create_batch": create_batch,
        "posting_date": detail.get("today"),
        "set_posting_time": True,
        "company": detail.get("company"),
    }


def _movement(payload):
    kind = payload["kind"]
    if kind == stock_contract.RECEIVE_KIND:
        return {
            "kind": kind,
            "item_code": payload["item_code"],
            "warehouse": payload["warehouse"],
            "qty": payload["qty"],
            "batch": payload.get("batch_id"),
        }
    if kind == stock_contract.TRANSFER_KIND:
        return {
            "kind": kind,
            "item_code": payload["item_code"],
            "warehouse": payload["source_warehouse"],
            "target_warehouse": payload["target_warehouse"],
            "qty": payload["qty"],
            "batch": payload.get("batch_id"),
        }
    return {
        "kind": kind,
        "item_code": payload["item_code"],
        "warehouse": payload["warehouse"],
        "qty": payload["qty"],
        "reason": payload["reason"],
    }


def _receipt_warehouse(payload):
    if payload["kind"] == stock_contract.TRANSFER_KIND:
        return payload["source_warehouse"]
    return payload["warehouse"]


def _finish_fence(gateway, claim, payload, reference):
    """Encode the receipt, commit the native write, then append the terminal.

    The fence is held across all three steps, but the two stores are never one
    transaction, so the commit-to-terminal gap is not atomic.

    The receipt is encoded (and so validated by the fence codec) before the one
    site commit, so an encoder refusal is deterministic and lands while the
    native transaction is still uncommitted. That error is not one of the
    family's named refusals, so it takes the generic failure path: the caller
    marks the key ``UNKNOWN`` best effort — if that append fails the key stays
    ``DISPATCHED`` — and then attempts ``gateway.rollback()``. A rollback that
    fails raises :class:`RollbackFailed` instead of any response, because a
    normal envelope would let Frappe's request-end commit persist the partial
    native write; the escape leaves the native transaction's state no longer
    established.

    The named handlers (ineligible target, exhausted budget, locked,
    conflicting, unavailable, uncertain, unsafe side effect) mark nothing: they
    attempt the same rollback and re-raise, so their key stays ``DISPATCHED``
    unless its terminal append had already persisted ``COMMITTED`` before the
    error. A failed native commit leaves the commit outcome uncertain — it may
    or may not have landed. An unresolved ``UNKNOWN`` or ``DISPATCHED`` key
    blocks all new operations site-wide until it is reconciled, and nothing here
    retries or reconciles it. A key whose terminal append did persist
    ``COMMITTED`` is not unresolved: it keeps its one-shot acknowledgement
    available under the recovery conditions the ack endpoint checks.
    """

    receipt = stock_contract.encode_receipt(
        kind=payload["kind"],
        reference=reference,
        item_code=payload["item_code"],
        warehouse=_receipt_warehouse(payload),
        qty=payload["qty"],
        reason=payload.get("reason") or "",
    )
    gateway.commit()
    claim.commit(receipt)
    return receipt


def _success(gateway, payload, ack_token):
    try:
        body = {
            "status": RESULT_STATUS[payload["kind"]],
            "movement": _movement(payload),
            "ackToken": ack_token,
        }
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, body)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _perform(
    gateway,
    body,
    csrf_token,
    *,
    kind,
    allowed_codes,
    validate,
    read_detail,
    problems,
    expected,
    prepare,
    mutate,
    read_facts,
):
    """Run one stock operation with the shared gate/fence/re-read order."""

    settings, view, role = _shared_gate(gateway, csrf_token, allowed_codes)
    try:
        payload = validate(body)
    except stock_contract.StockContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    if gateway.native_stock_can_create(DOCUMENTS[kind]) is not True:
        gateway.deny()

    try:
        detail = read_detail(gateway, payload)
    except (patient_write.WriteLocked, patient_write.StoreUnavailable):
        return _locked_response(gateway)
    if detail is None:
        return _locked_response(gateway)

    prepared = prepare(detail, payload)
    if prepared["create_batch"]:
        if gateway.native_stock_can_create(stock_contract.BATCH_DOCTYPE) is not True:
            gateway.deny()

    refused = _refusal(gateway, problems(detail, payload))
    if refused is not None:
        return refused
    if stock_contract.prepared_problem(kind, prepared) is not None:
        return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)

    expected_effects = expected(detail, payload)
    try:
        digest = stock_contract.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
        if budget_ok:
            baseline = gateway.native_stock_baseline(
                kind, tuple(expected_effects["bins"]), payload["item_code"]
            )
    except stock_contract.StockContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    receipt = None
    try:
        with gateway.stock_write_fence(
            actor, kind, payload["operation_key"], digest
        ) as claim:
            try:
                _require_budget(gateway, kind)
                gateway.reset_native_read_snapshot()
                fence_settings, fence_view, fence_role = _shared_gate(
                    gateway, csrf_token, allowed_codes
                )
                if fence_view.user != actor:
                    raise patient_write.WriteUncertain()
                if fence_role not in allowed_codes:
                    gateway.deny()
                current = read_detail(gateway, payload)
                if current is None or problems(current, payload):
                    raise stock_contract.StockTargetNotEligible()
                expected_effects = expected(current, payload)
                prepared = prepare(current, payload)
                if stock_contract.prepared_problem(kind, prepared) is not None:
                    raise stock_contract.StockTargetNotEligible()
                reference = mutate(gateway, prepared)
                facts = read_facts(gateway, reference, expected_effects, baseline)
                if (
                    stock_contract.side_effect_problem(facts, expected_effects)
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                receipt = _finish_fence(gateway, claim, payload, reference)
            except (
                stock_contract.StockTargetNotEligible,
                stock_contract.StockBudgetExhausted,
            ):
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
                    "native stock write outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except stock_contract.StockTargetNotEligible:
        return _locked_response(gateway)
    except stock_contract.StockBudgetExhausted:
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
        ack_token = stock_contract.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(gateway, payload, ack_token)
    except (SuccessUncertain, stock_contract.StockContractError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit stock write response construction failed",
        )
        return _locked_response(gateway)


def receive(gateway, body, csrf_token=None):
    """Receive stock: the batch master (where required) then the receipt."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=stock_contract.RECEIVE_KIND,
        allowed_codes=STOCK_ROLE_CODES,
        validate=stock_contract.validate_receive,
        read_detail=lambda gw, payload: gw.native_stock_receive_detail(payload),
        problems=stock_contract.receive_problems,
        expected=stock_contract.expected_receive,
        prepare=_prepare_receive,
        mutate=lambda gw, prepared: gw.create_native_stock_receipt(prepared),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_stock_receipt_facts(reference, exp, baseline)
        ),
    )


def transfer(gateway, body, csrf_token=None):
    """Create and submit one Material Transfer from the native document fields.

    The body carries the document's own fields — ``company``, ``item_code``,
    ``qty``, ``source_warehouse``, ``target_warehouse`` and an optional batch —
    and this endpoint inserts and submits exactly one ``Stock Entry`` of type
    ``Material Transfer`` through the installed controller. Frappe/Marley
    permissions, warehouses, batch validity, available stock, quantities,
    posting and the submit lifecycle do the deciding; no value is guessed,
    substituted or checked again here. The historical ``operation_key`` is
    accepted and ignored so an older caller is not refused. Session, CSRF,
    exact site, the stock flag, the pinned boundary and the caller's own native
    create and submit authority stay required; nothing is granted.
    """

    _native_gate(gateway, csrf_token, STOCK_ROLE_CODES)
    try:
        payload = stock_contract.validate_transfer(body)
    except stock_contract.StockContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    doctype = stock_contract.STOCK_ENTRY_DOCTYPE
    if gateway.native_stock_can_create(doctype) is not True:
        gateway.deny()
    if gateway.native_stock_can_submit(doctype) is not True:
        gateway.deny()

    try:
        created = gateway.create_and_submit_native_stock_transfer(payload)
        reference = created.get("name") if isinstance(created, dict) else None
        docstatus = (
            created.get("docstatus") if isinstance(created, dict) else None
        )
        if not isinstance(reference, str) or not reference.strip():
            raise patient_write.WriteUncertain()
        if not isinstance(docstatus, int) or isinstance(docstatus, bool):
            raise patient_write.WriteUncertain()
        gateway.commit()
    except Exception as exc:
        # Roll the partial transaction back first; a rollback failure escapes
        # (RollbackFailed), so no envelope can be followed by a request-end
        # commit. Once the rollback succeeded the request holds no partial
        # write, so an installed refusal may be reported as the definite
        # outcome it is; anything else stays uncertain and is never retried.
        _rollback_or_raise(gateway)
        if gateway.is_native_permission_denial(exc):
            gateway.deny()
        if gateway.is_native_validation_error(exc):
            return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(
        HTTP_OK,
        {
            "status": TRANSFERRED_RESULT,
            "stock_entry": {
                "stock_entry_id": reference,
                "docstatus": docstatus,
            },
        },
    )


def adjust(gateway, body, csrf_token=None):
    """Create and submit one reconciliation from the native document fields.

    Input shape and the installed controller are the only validation: the body
    carries the document's own fields, and Frappe decides permissions, Select
    values, quantity, posting and the submit lifecycle.
    """

    _native_gate(gateway, csrf_token, ADJUST_ROLES)
    try:
        payload = stock_contract.validate_adjust(body)
    except stock_contract.StockContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    doctype = stock_contract.STOCK_RECONCILIATION_DOCTYPE
    if gateway.native_stock_can_create(doctype) is not True:
        gateway.deny()
    if gateway.native_stock_can_submit(doctype) is not True:
        gateway.deny()

    try:
        created = gateway.create_and_submit_native_stock_reconciliation(payload)
        reference = created.get("name") if isinstance(created, dict) else None
        docstatus = (
            created.get("docstatus") if isinstance(created, dict) else None
        )
        if not isinstance(reference, str) or not reference.strip():
            raise patient_write.WriteUncertain()
        if not isinstance(docstatus, int) or isinstance(docstatus, bool):
            raise patient_write.WriteUncertain()
        gateway.commit()
    except Exception as exc:
        # Roll the partial transaction back first; a rollback failure escapes
        # (RollbackFailed), so no envelope can be followed by a request-end
        # commit. Once the rollback succeeded the request holds no partial
        # write, so an installed refusal may be reported as the definite
        # outcome it is; anything else stays uncertain.
        _rollback_or_raise(gateway)
        if gateway.is_native_permission_denial(exc):
            gateway.deny()
        if gateway.is_native_validation_error(exc):
            return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(
        HTTP_OK,
        {
            "status": ADJUSTED_RESULT,
            "reconciliation": {
                "reconciliation_id": reference,
                "docstatus": docstatus,
            },
        },
    )


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed stock movement."""

    settings, view, _role = common.authorize_stock_write(
        gateway, (STOCK_ROLE_CODES | ADJUST_ROLE_CODES), csrf_token
    )
    try:
        payload = stock_contract.validate_ack(body)
        status = gateway.acknowledge_stock_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except stock_contract.StockContractError:
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
