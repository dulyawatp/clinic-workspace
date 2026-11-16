"""Designed finance (invoice submit + payment receive) writes.

Order is fixed and test-asserted for every operation, exactly like the
purchasing family:

1. shared read gate plus the explicit same-session CSRF and finance-write
   deployment gates, then the pinned boundary and installed-field preflight;
   either drift denies before validation,
2. strict body validation; the client sends at most a customer, a selling price
   list, an explicit due date and itemized ``(item, quantity)`` lines — never a
   rate, a total, a currency, a company, a docstatus, a ledger account, a tax
   row or a card number,
3. the native write authority on the document this operation would create,
4. every pre-fence refusal with a named line: unknown customer, missing or
   non-selling price list, a due date before the posting date, a missing
   receivable/income account or cost center, an item without a selling price or
   income account, a duplicate charge, a discount that is not enabled, a
   draft/cancelled/paid invoice, an over-allocation, an allocation sum that
   differs from the paid amount, a refused payment mode (``Bank Transfer`` is
   named), a mode without a company account, a repeated payment, a card payment
   without a terminal reference, native authority drift and the durable budget,
5. the pre-fence GL / Payment-Ledger / Stock-Ledger baseline used by the
   closed-world delta re-read,
6. the persistent site-wide fence, then the same decisions again against
   current rows,
7. one mutation unit through the installed submittable document (insert, then
   submit), a closed-world re-read against the EXPECTED side-effect list, one
   explicit commit and the terminal receipt,
8. the one-shot acknowledgement token.

``save_invoice_draft`` is the server-side **preview**: it runs the same
validation and pre-fence reads and returns the resolved document the single
submit would post, but it writes nothing (no fence claim, no budget). The
approved write budget has one invoice unit, so the designed draft→submit flow is
one fenced mutation unit — exactly like a batch master plus receipt.

A payment is one ``Payment Entry`` of type ``Receive``; the reference allocation
is server-built and must sum to the paid amount. No PAN/CVV is ever accepted,
prepared, logged or echoed.

Every designed refusal happens **before** the fence claim. A refusal inside a
held fence would terminate the operation ``UNKNOWN`` and latch the whole site.
A post-dispatch failure is uncertain: the key stays locked and the response is
``409 {"code": "WRITE_LOCKED"}`` with no retry.
"""

from clinic_ui.core import finance_contract, patient_write
from clinic_ui.core.policy import INVOICE_ROLE_CODES, PAYMENT_ROLE_CODES
from clinic_ui.endpoints import common

HTTP_OK = 200
HTTP_REFUSED = finance_contract.HTTP_REFUSED
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"

PREVIEW_RESULT = "preview"
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
    """Return the first named pre-fence refusal as the fixed safe envelope.

    The envelope carries only the fixed code and the refusal's field label
    (never a record value), so an operator or an audit line can name the
    decision while the body stays free of record detail.
    """

    if not problems:
        return None
    status, label = problems[0][0], problems[0][1]
    return gateway.respond_write(status, {"code": VALIDATION_CODE, "problem": label})


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
            "native finance write rollback failed"
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

    limit = finance_contract.budget(kind)
    try:
        records = gateway.list_committed_finance_receipts(limit, kind=kind)
    except finance_contract.FinanceBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise finance_contract.FinanceBudgetExhausted()


def _shared_gate(gateway, csrf_token, allowed_codes):
    settings, view, role = common.authorize_finance_write(
        gateway, allowed_codes, csrf_token
    )
    if role not in allowed_codes:
        gateway.deny()
    if gateway.native_finance_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_finance_install_problem() is not None:
        gateway.deny()
    return settings, view, role


DOCUMENTS = {
    finance_contract.INVOICE_KIND: finance_contract.INVOICE_DOCTYPE,
    finance_contract.PAYMENT_KIND: finance_contract.PAYMENT_DOCTYPE,
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


def _prepare_invoice(detail, payload):
    """The server-resolved invoice body: rates, accounts and totals are ours."""

    totals = finance_contract.invoice_totals(detail, payload)
    income_accounts = detail.get("income_accounts") or {}
    items = detail.get("items") or {}
    lines = []
    for priced in totals["lines"]:
        code = priced["item_code"]
        unit = (items.get(code) or {}).get("stock_uom")
        lines.append(
            {
                "item_code": code,
                "qty": priced["qty"],
                "rate": priced["rate"],
                "amount": priced["amount"],
                "price_list_rate": priced["rate"],
                "uom": unit,
                "stock_uom": unit,
                "income_account": income_accounts.get(code),
                "cost_center": detail.get("cost_center"),
            }
        )
    deductions = []
    if totals["discount"] > 0:
        # The discount rides the child row only; the account is the invoice's
        # own income account, never an invented write-off account.
        deductions.append(
            {
                "account": income_accounts.get(payload["items"][0]["item_code"]),
                "cost_center": detail.get("cost_center"),
                "amount": totals["discount"],
            }
        )
    return {
        "kind": finance_contract.INVOICE_KIND,
        "customer": payload["customer"],
        "company": detail.get("company"),
        "currency": detail.get("currency"),
        "conversion_rate": 1.0,
        "selling_price_list": payload["selling_price_list"],
        "due_date": payload["due_date"],
        "posting_date": detail.get("today"),
        "update_stock": finance_contract.PINNED_UPDATE_STOCK,
        "debit_to": detail.get("receivable_account"),
        "cost_center": detail.get("cost_center"),
        "taxes": [],
        "discount": totals["discount"],
        "deductions": deductions,
        "items": lines,
    }


def _prepare_payment(detail, payload):
    invoice = detail.get("invoice") or {}
    mode = detail.get("mode") or {}
    return {
        "kind": finance_contract.PAYMENT_KIND,
        "payment_type": "Receive",
        "party_type": "Customer",
        "party": invoice.get("customer"),
        "company": detail.get("company"),
        "currency": detail.get("currency"),
        "paid_from": detail.get("receivable_account"),
        "paid_to": mode.get("account"),
        "paid_amount": payload["amount"],
        "received_amount": payload["amount"],
        "source_exchange_rate": 1.0,
        "target_exchange_rate": 1.0,
        "posting_date": detail.get("today"),
        "mode_of_payment": payload["mode_of_payment"],
        "reference_no": payload.get("reference_no"),
        "reference_date": detail.get("today"),
        "references": [
            {
                "reference_doctype": finance_contract.INVOICE_DOCTYPE,
                "reference_name": entry["invoice"],
                "allocated_amount": entry["amount"],
                "outstanding_amount": invoice.get("outstanding_amount"),
                "total_amount": invoice.get("grand_total"),
            }
            for entry in payload["allocations"]
        ],
    }


def _receipt_kwargs(payload, reference, expected, prepared):
    if payload["kind"] == finance_contract.PAYMENT_KIND:
        return {
            "kind": finance_contract.PAYMENT_KIND,
            "reference": reference,
            "invoice": payload["invoice"],
            "customer": (prepared or {}).get("party") or "",
            "amount": payload["amount"],
            "mode_of_payment": payload["mode_of_payment"],
            "status": expected.get("status")
            or expected.get("unchanged_status")
            or "",
        }
    return {
        "kind": finance_contract.INVOICE_KIND,
        "reference": reference,
        "invoice": reference,
        "customer": payload["customer"],
        "amount": expected["grand_total"],
        "status": expected["status"],
    }


def _finish_fence(gateway, claim, payload, reference, expected, prepared):
    gateway.commit()
    receipt = finance_contract.encode_receipt(
        **_receipt_kwargs(payload, reference, expected, prepared)
    )
    claim.commit(receipt)
    return receipt


def _success(gateway, payload, reference, ack_token, expected):
    try:
        if payload["kind"] == finance_contract.PAYMENT_KIND:
            body = {
                "status": RECEIVED_RESULT,
                "payment": reference,
                "invoice": payload["invoice"],
                "outstanding": expected["outstanding_after"],
                "ackToken": ack_token,
            }
        else:
            body = {
                "status": SUBMITTED_RESULT,
                "invoice": reference,
                "total": expected["grand_total"],
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
    """Run one finance operation with the shared gate/fence/re-read order."""

    settings, view, role = _shared_gate(gateway, csrf_token, allowed_codes)
    try:
        payload = validate(body)
    except finance_contract.FinanceContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    if gateway.native_finance_can_write(DOCUMENTS[kind]) is not True:
        gateway.deny()

    try:
        detail = read_detail(gateway, payload, settings)
    except (patient_write.WriteLocked, patient_write.StoreUnavailable):
        return _locked_response(gateway)
    if detail is None:
        return _locked_response(gateway)

    prepared = prepare(detail, payload)
    refused = _refusal(gateway, problems(detail, payload))
    if refused is not None:
        return refused
    if finance_contract.prepared_problem(kind, prepared) is not None:
        return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)

    expected_effects = expected(detail, payload)
    try:
        digest = finance_contract.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
        if budget_ok:
            baseline = gateway.native_finance_baseline(
                kind, payload.get("invoice")
            )
    except finance_contract.FinanceContractError:
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
        with gateway.finance_write_fence(
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
                current = read_detail(gateway, payload, fence_settings)
                if current is None or problems(current, payload):
                    raise finance_contract.FinanceTargetNotEligible()
                expected_effects = expected(current, payload)
                prepared = prepare(current, payload)
                if finance_contract.prepared_problem(kind, prepared) is not None:
                    raise finance_contract.FinanceTargetNotEligible()
                reference = mutate(gateway, prepared)
                facts = read_facts(gateway, reference, expected_effects, baseline)
                if (
                    finance_contract.side_effect_problem(facts, expected_effects)
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                _finish_fence(
                    gateway,
                    claim,
                    payload,
                    reference,
                    expected_effects,
                    prepared,
                )
            except (
                finance_contract.FinanceTargetNotEligible,
                finance_contract.FinanceBudgetExhausted,
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
                    "native finance write outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except finance_contract.FinanceTargetNotEligible:
        return _locked_response(gateway)
    except finance_contract.FinanceBudgetExhausted:
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
        receipt = finance_contract.encode_receipt(
            **_receipt_kwargs(payload, reference, expected_effects, prepared)
        )
        ack_token = finance_contract.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(gateway, payload, reference, ack_token, expected_effects)
    except (SuccessUncertain, finance_contract.FinanceContractError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit finance write response construction failed",
        )
        return _locked_response(gateway)


def save_invoice_draft(gateway, body, csrf_token=None):
    """Return the server-resolved invoice preview; nothing is persisted."""

    settings, _view, _role = _shared_gate(
        gateway, csrf_token, INVOICE_ROLE_CODES
    )
    try:
        payload = finance_contract.validate_invoice(body)
    except finance_contract.FinanceContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if gateway.native_finance_can_write(finance_contract.INVOICE_DOCTYPE) is not True:
        gateway.deny()

    try:
        detail = gateway.native_finance_invoice_detail(
            payload, settings.invoice_discount_enabled
        )
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if detail is None:
        return _locked_response(gateway)

    refused = _refusal(
        gateway, finance_contract.invoice_problems(detail, payload)
    )
    if refused is not None:
        return refused
    prepared = _prepare_invoice(detail, payload)
    if (
        finance_contract.prepared_problem(
            finance_contract.INVOICE_KIND, prepared
        )
        is not None
    ):
        return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)

    totals = finance_contract.invoice_totals(detail, payload)
    return gateway.respond_write(
        HTTP_OK,
        {
            "status": PREVIEW_RESULT,
            "customer": payload["customer"],
            "currency": detail.get("currency"),
            "company": detail.get("company"),
            "selling_price_list": payload["selling_price_list"],
            "due_date": payload["due_date"],
            "lines": totals["lines"],
            "net_total": totals["net_total"],
            "discount": totals["discount"],
            "discount_available": detail.get("discount_available") is True,
            "total_taxes": totals["total_taxes"],
            "total": totals["grand_total"],
        },
    )


def submit_invoice(gateway, body, csrf_token=None):
    """Create and submit exactly one sales invoice."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=finance_contract.INVOICE_KIND,
        allowed_codes=INVOICE_ROLE_CODES,
        validate=finance_contract.validate_invoice,
        read_detail=lambda gw, payload, settings: (
            gw.native_finance_invoice_detail(
                payload, settings.invoice_discount_enabled
            )
        ),
        problems=finance_contract.invoice_problems,
        expected=finance_contract.expected_invoice,
        prepare=_prepare_invoice,
        mutate=lambda gw, prepared: gw.create_and_submit_native_sales_invoice(
            prepared
        ),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_finance_invoice_facts(reference, exp, baseline)
        ),
    )


def receive_payment(gateway, body, csrf_token=None):
    """Receive one payment against one submitted invoice."""

    return _perform(
        gateway,
        body,
        csrf_token,
        kind=finance_contract.PAYMENT_KIND,
        allowed_codes=PAYMENT_ROLE_CODES,
        validate=finance_contract.validate_payment,
        read_detail=lambda gw, payload, settings: (
            gw.native_finance_payment_detail(payload)
        ),
        problems=finance_contract.payment_problems,
        expected=finance_contract.expected_payment,
        prepare=_prepare_payment,
        mutate=lambda gw, prepared: gw.create_native_payment_entry(prepared),
        read_facts=lambda gw, reference, exp, baseline: (
            gw.native_finance_payment_facts(reference, exp, baseline)
        ),
    )


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed finance operation."""

    settings, view, _role = common.authorize_finance_write(
        gateway, INVOICE_ROLE_CODES | PAYMENT_ROLE_CODES, csrf_token
    )
    try:
        payload = finance_contract.validate_ack(body)
        status = gateway.acknowledge_finance_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except finance_contract.FinanceContractError:
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
