"""Designed purchasing (PO draft/submit + PO-referenced receipt) writes.

Order is fixed and test-asserted for all three operations, exactly like the
stock family:

1. shared read gate plus the explicit same-session CSRF and purchasing-write
   deployment gates, then the pinned boundary and installed-field preflight;
   either drift denies before validation,
2. strict body validation; the client sends at most a supplier, itemized lines
   (item, quantity, rate) or a purchase-order reference with a quantity — never
   a docstatus, submit flag, currency, posting date, valuation, ledger row or
   native batch name,
3. the native create authority on the document this operation would create or
   submit,
4. every pre-fence refusal with a named line: unknown supplier/item/warehouse,
   a line without item/quantity/unit/rate, an empty itemization, a submit target
   that is not a draft, a receipt against an unknown or non-submitted purchase
   order, over-receipt against the pending quantity, the batch rules and the
   durable budget,
5. the pre-fence Stock Ledger / Bin baseline used by the delta re-read,
6. the persistent site-wide fence, then the same decisions again against
   current rows,
7. one mutation unit through the installed submittable document (for the
   PO-referenced receipt: the batch master creation first, then the submitted
   ``Purchase Receipt``), a closed-world re-read against the EXPECTED
   side-effect list, one explicit commit and the terminal receipt,
8. the one-shot acknowledgement token.

A draft purchase order never submits itself: there is no submit flag anywhere in
the draft body or the prepared document. Closing a purchase order is designed
but unwired this wave (no close kind, no close budget line).

Every designed refusal happens **before** the fence claim. A refusal inside a
held fence would terminate the operation ``UNKNOWN`` and latch the whole site.
A post-dispatch failure is uncertain: the key stays locked and the response is
``409 {"code": "WRITE_LOCKED"}`` with no retry.
"""

from clinic_ui.core import patient_write, purchase_contract
from clinic_ui.core.policy import PO_ROLE_CODES
from clinic_ui.endpoints import common

HTTP_OK = 200
HTTP_REFUSED = purchase_contract.HTTP_REFUSED
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"

DRAFTED_RESULT = "drafted"
SUBMITTED_RESULT = "submitted"
RECEIVED_RESULT = "received"


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


def _rollback_or_raise(gateway):
    try:
        gateway.rollback()
    except BaseException as exc:
        raise patient_write.WriteUncertain(
            "native purchasing write rollback failed"
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

    limit = purchase_contract.budget(kind)
    try:
        records = gateway.list_committed_purchase_receipts(limit, kind=kind)
    except purchase_contract.PurchaseBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise purchase_contract.PurchaseBudgetExhausted()


def _shared_gate(gateway, csrf_token, allowed_codes):
    settings, view, role = common.authorize_purchase_write(
        gateway, allowed_codes, csrf_token
    )
    if role not in allowed_codes:
        gateway.deny()
    if gateway.native_purchase_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_purchase_install_problem() is not None:
        gateway.deny()
    return settings, view, role


DOCUMENTS = {
    purchase_contract.DRAFT_KIND: purchase_contract.PURCHASE_ORDER_DOCTYPE,
    purchase_contract.SUBMIT_KIND: purchase_contract.PURCHASE_ORDER_DOCTYPE,
    purchase_contract.RECEIPT_KIND: purchase_contract.PURCHASE_RECEIPT_DOCTYPE,
}


def _digest_fields(payload):
    fields = {}
    for name, value in payload.items():
        if name == "operation_key" or name == "kind":
            continue
        if isinstance(value, float):
            fields[name] = format(value, ".6f")
        elif isinstance(value, list):
            fields[name] = "[" + ";".join(
                "|".join(
                    f"{key}={format(item[key], '.6f')}"
                    if isinstance(item.get(key), float)
                    else f"{key}={item.get(key)}"
                    for key in sorted(item)
                )
                for item in value
            ) + "]"
        else:
            fields[name] = "" if value is None else str(value)
    return fields


def _prepare_draft(detail, payload):
    company = detail.get("company")
    currency = detail.get("currency")
    today = detail.get("today")
    items = detail.get("items") or {}
    lines = []
    for line in payload["items"]:
        item = items.get(line["item_code"]) or {}
        unit = item.get("stock_uom")
        lines.append(
            {
                "item_code": line["item_code"],
                "qty": line["qty"],
                "rate": line["rate"],
                "uom": unit,
                "stock_uom": unit,
                "conversion_factor": 1.0,
                "schedule_date": today,
            }
        )
    return {
        "kind": purchase_contract.DRAFT_KIND,
        "supplier": payload["supplier"],
        "company": company,
        "currency": currency,
        "conversion_rate": 1.0,
        "transaction_date": today,
        "items": lines,
    }


def _prepare_submit(detail, payload):
    order = detail.get("purchase_order") or {}
    return {
        "kind": purchase_contract.SUBMIT_KIND,
        "purchase_order": payload["purchase_order"],
        "company": detail.get("company"),
        "currency": detail.get("currency"),
        "supplier": order.get("supplier"),
        "docstatus": order.get("docstatus"),
    }


def _prepare_receipt(detail, payload):
    item = detail.get("item") or {}
    batch_id = payload.get("batch_id")
    create_batch = bool(
        item.get("has_batch_no")
        and batch_id
        and not (detail.get("batch") or {}).get("exists")
    )
    return {
        "kind": purchase_contract.RECEIPT_KIND,
        "purchase_order": payload["purchase_order"],
        "po_item": detail.get("po_item"),
        "supplier": detail.get("supplier"),
        "item_code": payload["item_code"],
        "qty": payload["qty"],
        "uom": item.get("stock_uom"),
        "warehouse": payload["warehouse"],
        "batch_no": batch_id,
        "batch_expiry": payload.get("batch_expiry"),
        "create_batch": create_batch,
        "posting_date": detail.get("today"),
        "set_posting_time": True,
        "company": detail.get("company"),
        "currency": detail.get("currency"),
    }


def _movement(payload):
    return {
        "kind": purchase_contract.RECEIPT_KIND,
        "purchase_order": payload["purchase_order"],
        "item_code": payload["item_code"],
        "warehouse": payload["warehouse"],
        "qty": payload["qty"],
        "batch": payload.get("batch_id"),
    }


def _baseline_warehouses(payload):
    if payload["kind"] == purchase_contract.RECEIPT_KIND:
        return (payload["warehouse"],)
    return ()


def _baseline_item(payload):
    if payload["kind"] == purchase_contract.RECEIPT_KIND:
        return payload["item_code"]
    lines = payload.get("items") or ()
    if lines:
        return lines[0]["item_code"]
    return ""


def _receipt_kwargs(payload, reference):
    if payload["kind"] == purchase_contract.RECEIPT_KIND:
        return {
            "kind": purchase_contract.RECEIPT_KIND,
            "reference": reference,
            "purchase_order": payload["purchase_order"],
            "item_code": payload["item_code"],
            "warehouse": payload["warehouse"],
            "qty": payload["qty"],
        }
    return {
        "kind": payload["kind"],
        "reference": reference,
        "purchase_order": reference,
    }


def _finish_fence(gateway, claim, payload, reference):
    gateway.commit()
    receipt = purchase_contract.encode_receipt(
        **_receipt_kwargs(payload, reference)
    )
    claim.commit(receipt)
    return receipt


def _success(gateway, payload, reference, ack_token):
    try:
        if payload["kind"] == purchase_contract.RECEIPT_KIND:
            body = {
                "status": RECEIVED_RESULT,
                "movement": _movement(payload),
                "ackToken": ack_token,
            }
        else:
            body = {
                "status": (
                    DRAFTED_RESULT
                    if payload["kind"] == purchase_contract.DRAFT_KIND
                    else SUBMITTED_RESULT
                ),
                "purchase_order": reference,
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
    """Run one purchasing operation with the shared gate/fence/re-read order."""

    settings, view, role = _shared_gate(gateway, csrf_token, allowed_codes)
    try:
        payload = validate(body)
    except purchase_contract.PurchaseContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    if gateway.native_purchase_can_create(DOCUMENTS[kind]) is not True:
        gateway.deny()

    try:
        detail = read_detail(gateway, payload)
    except (patient_write.WriteLocked, patient_write.StoreUnavailable):
        return _locked_response(gateway)
    if detail is None:
        return _locked_response(gateway)

    prepared = prepare(detail, payload)
    if prepared.get("create_batch"):
        if (
            gateway.native_purchase_can_create(purchase_contract.BATCH_DOCTYPE)
            is not True
        ):
            gateway.deny()

    refused = _refusal(gateway, problems(detail, payload))
    if refused is not None:
        return refused
    if purchase_contract.prepared_problem(kind, prepared) is not None:
        return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)

    expected_effects = expected(detail, payload)
    try:
        digest = purchase_contract.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
        if budget_ok:
            baseline = gateway.native_purchase_baseline(
                kind,
                _baseline_warehouses(payload),
                _baseline_item(payload),
                payload.get("purchase_order"),
            )
    except purchase_contract.PurchaseContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    reference = None
    try:
        with gateway.purchase_write_fence(
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
                    raise purchase_contract.PurchaseTargetNotEligible()
                expected_effects = expected(current, payload)
                prepared = prepare(current, payload)
                if purchase_contract.prepared_problem(kind, prepared) is not None:
                    raise purchase_contract.PurchaseTargetNotEligible()
                reference = mutate(gateway, prepared)
                facts = read_facts(gateway, reference, expected_effects, baseline)
                if (
                    purchase_contract.side_effect_problem(
                        facts, expected_effects
                    )
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                _finish_fence(gateway, claim, payload, reference)
            except (
                purchase_contract.PurchaseTargetNotEligible,
                purchase_contract.PurchaseBudgetExhausted,
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
                    "native purchasing write outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except purchase_contract.PurchaseTargetNotEligible:
        return _locked_response(gateway)
    except purchase_contract.PurchaseBudgetExhausted:
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
        receipt = purchase_contract.encode_receipt(
            **_receipt_kwargs(payload, reference)
        )
        ack_token = purchase_contract.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(gateway, payload, reference, ack_token)
    except (SuccessUncertain, purchase_contract.PurchaseContractError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit purchasing write response construction failed",
        )
        return _locked_response(gateway)


def save_po_draft(gateway, body, csrf_token=None):
    """Insert one draft purchase order (never submits it)."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=purchase_contract.DRAFT_KIND,
        allowed_codes=PO_ROLE_CODES,
        validate=purchase_contract.validate_draft,
        read_detail=lambda gw, payload: gw.native_purchase_order_draft_detail(
            payload
        ),
        problems=purchase_contract.draft_problems,
        expected=purchase_contract.expected_draft,
        prepare=_prepare_draft,
        mutate=lambda gw, prepared: gw.create_native_purchase_order_draft(
            prepared
        ),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_purchase_order_facts(reference, exp, baseline)
        ),
    )


def submit_po(gateway, body, csrf_token=None):
    """Submit one existing draft purchase order."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=purchase_contract.SUBMIT_KIND,
        allowed_codes=PO_ROLE_CODES,
        validate=purchase_contract.validate_submit,
        read_detail=lambda gw, payload: gw.native_purchase_order_submit_detail(
            payload
        ),
        problems=purchase_contract.submit_problems,
        expected=purchase_contract.expected_submit,
        prepare=_prepare_submit,
        mutate=lambda gw, prepared: gw.submit_native_purchase_order(prepared),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_purchase_order_facts(reference, exp, baseline)
        ),
    )


def receive(gateway, body, csrf_token=None):
    """Receive one line against a submitted purchase order."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=purchase_contract.RECEIPT_KIND,
        allowed_codes=PO_ROLE_CODES,
        validate=purchase_contract.validate_receipt,
        read_detail=lambda gw, payload: gw.native_purchase_receipt_detail(
            payload
        ),
        problems=purchase_contract.receipt_problems,
        expected=purchase_contract.expected_receipt,
        prepare=_prepare_receipt,
        mutate=lambda gw, prepared: gw.create_native_purchase_receipt(prepared),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_purchase_receipt_facts(reference, exp, baseline)
        ),
    )


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed purchasing operation."""

    settings, view, _role = common.authorize_purchase_write(
        gateway, PO_ROLE_CODES, csrf_token
    )
    try:
        payload = purchase_contract.validate_ack(body)
        status = gateway.acknowledge_purchase_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except purchase_contract.PurchaseContractError:
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
