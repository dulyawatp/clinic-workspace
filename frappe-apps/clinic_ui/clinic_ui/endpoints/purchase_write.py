"""Designed purchasing (PO draft/submit + PO-referenced receipt) writes.

The family now spans two lanes:

- the **converted draft lane** (``save_po_draft``) relies on Frappe/Marley
  document permissions, controller validation and native transactions instead
  of the historical operation fence, write budget, deployment marker and
  acknowledgement barrier. It inserts one draft purchase order, never submits
  it, posts no stock or ledger row, and reads the committed row back under the
  current user's own native read authority. The installed controller resolves
  the line-facing values the client never sends (``item_name`` and the default
  warehouse) during its own insert lifecycle. ``view`` and ``drafts`` are its
  bounded, permission-aware read/discovery surface for an unknown outcome;
- the **fenced submit/receipt lane** is unchanged and stays closed: submit and
  the PO-referenced receipt still need the pinned boundary, the deployment
  marker, a ready fence store, the durable budget, the shared fence and the
  one-shot acknowledgement. An unresolved ledger entry keeps both surfaces
  disabled and blocks every fenced operation on the site.

Fenced order is fixed and test-asserted for both remaining operations, exactly
like the stock family:

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
   side-effect list, the expected receipt encoded before that commit, one
   explicit commit and then the terminal append,
8. the one-shot acknowledgement token.

A draft purchase order never submits itself: there is no submit flag anywhere in
the draft body or the prepared document. Closing a purchase order is designed
but unwired this wave (no close kind, no close budget line).

Every designed refusal happens **before** the fence claim. A refusal inside a
held fence would leave the operation unresolved — a best-effort ``UNKNOWN``
marker, otherwise ``DISPATCHED`` — and latch the whole site.
A post-dispatch failure is uncertain: the key stays locked and the response is
``409 {"code": "WRITE_LOCKED"}`` with no retry. The endpoint rolls back the
partial transaction; a rollback failure raises ``RollbackFailed`` instead of
returning a normal error.
"""

from clinic_ui.core import patient_write, purchase_contract
from clinic_ui.core.policy import PO_ROLE_CODES
from clinic_ui.core.text import is_valid_text
from clinic_ui.endpoints import aliases, common

HTTP_OK = 200
HTTP_REFUSED = purchase_contract.HTTP_REFUSED
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"
UNCERTAIN_CODE = "WRITE_UNCERTAIN"

CREATED_RESULT = "created"
DRAFTED_RESULT = "drafted"
SUBMITTED_RESULT = "submitted"
RECEIVED_RESULT = "received"

# Discovery is bounded, and a page over the bound refuses rather than truncates.
MAX_NATIVE_PURCHASE_ORDERS = 50
MAX_PURCHASE_STATUS_LENGTH = 64


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
    Frappe cleanup instead; the fence key stays ``UNKNOWN``/``DISPATCHED``.
    """


def _rollback_or_raise(gateway):
    """Discard the partial transaction; a rollback failure must escape.

    Do not swallow this: a normal error response could trigger Frappe's implicit
    request-end commit. The key stays unresolved — ``UNKNOWN`` when the caller
    could mark it, otherwise ``DISPATCHED`` — so the lock is retained even when
    rollback itself fails.
    """

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
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


def _draft_gate(gateway, csrf_token):
    """Converted draft gate: session, CSRF, exact site, flag, role, preflight.

    No operation fence, write budget, receipt, acknowledgement or deployment
    marker is read. The pinned boundary stays, and the installed-field preflight
    is the draft-specific one: the draft is inserted through the installed
    Purchase Order controller and reads only the header, line, Item and Company
    currency fields it actually uses. The fenced submit/receipt preflight is
    untouched and remains strict. The native create permission and every
    Supplier/Item read are checked per request.
    """

    settings, view, role = common.authorize_purchase_draft_write(
        gateway, PO_ROLE_CODES, csrf_token
    )
    if role not in PO_ROLE_CODES:
        gateway.deny()
    if gateway.native_purchase_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_purchase_draft_install_problem() is not None:
        gateway.deny()
    return settings, view, role


DOCUMENTS = {
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
    """Encode the receipt, commit the native write, then append the terminal.

    The receipt is encoded by the contract codec before the site commit.
    On encoder refusal the caller attempts to mark the key ``UNKNOWN`` and
    then attempts rollback. If rollback fails, ``RollbackFailed`` escapes;
    the native transaction outcome is not established.

    The fence spans all three steps; the ``FOR UPDATE`` row locks do not, so the
    native commit and the terminal append are never one transaction, and a
    failure after the commit is uncertain rather than retryable. A native commit
    failure is itself uncertain: the commit may have applied before it raised,
    and the caller's fallback ``UNKNOWN`` marker is a best-effort append that can
    fail or be interrupted. A terminal append failure leaves the outcome
    unresolved as well: the append may never have landed, leaving the key
    ``DISPATCHED``, or it may have been durable before the error surfaced,
    leaving ``COMMITTED`` with the receipt. There is no ``ABORTED`` terminal, no
    automatic recovery and no unlock: an unresolved ``DISPATCHED`` or
    ``UNKNOWN`` row locks the whole site for every actor until an operator
    reconciles it.
    """

    receipt = purchase_contract.encode_receipt(
        **_receipt_kwargs(payload, reference)
    )
    gateway.commit()
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


def _draft_success(gateway, native_purchase_order_id):
    """Read the committed draft back under the caller's native read authority.

    Only the native docname, the native status and ``docstatus`` are returned.
    A read-back that is missing, masked, names another document or shows a
    submitted row is an uncertain outcome, never a claimed success.
    """

    try:
        detail = gateway.native_purchase_order_view(native_purchase_order_id)
        if not isinstance(detail, dict):
            raise ValueError("native detail is missing after commit")
        if detail.get("purchase_order_id") != native_purchase_order_id:
            raise ValueError("native detail names another document")
        status = detail.get("status")
        if (
            not isinstance(status, str)
            or not is_valid_text(status, MAX_PURCHASE_STATUS_LENGTH)
            or status != status.strip()
        ):
            raise ValueError("native status is unreadable after commit")
        if detail.get("docstatus") != purchase_contract.DRAFT_DOCSTATUS:
            raise ValueError("native draft is not unsubmitted after commit")
        body = {
            "status": CREATED_RESULT,
            "purchase_order": {
                "purchase_order_id": native_purchase_order_id,
                "status": status,
                "docstatus": purchase_contract.DRAFT_DOCSTATUS,
            },
        }
    except Exception:
        # The native insert already committed; its outcome is not established.
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    gateway.strip_message_channels()
    return gateway.respond_write(HTTP_OK, body)


def _deny_unreadable_masters(gateway, detail, payload):
    """Refuse a master the caller's own native read permission does not cover.

    The gateway reports ``supplier_denied``/``denied`` only for a document that
    exists but is not readable, and loads no field in that case. The refusal is
    a permission denial (403), never a validation message that would disclose
    batch, stock or price state.
    """

    if not isinstance(detail, dict):
        gateway.deny()
    if detail.get("supplier_denied") is True:
        gateway.deny()
    items = detail.get("items")
    if not isinstance(items, dict):
        gateway.deny()
    for line in payload["items"]:
        entry = items.get(line["item_code"])
        if not isinstance(entry, dict):
            gateway.deny()
        if entry.get("denied") is True:
            gateway.deny()


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
                receipt = _finish_fence(gateway, claim, payload, reference)
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
    """Insert one draft purchase order and read its native state back.

    Converted lane: no operation fence, write budget, receipt, acknowledgement
    token or deployment marker. The insert never submits; the closed-world
    re-read proves the draft posted no Stock Ledger or GL row and the final
    permission-aware read-back proves ``docstatus`` is still 0.
    """

    _settings, _view, role = _draft_gate(gateway, csrf_token)
    if role not in PO_ROLE_CODES:
        gateway.deny()
    try:
        payload = purchase_contract.validate_draft(body)
    except purchase_contract.PurchaseContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if gateway.native_purchase_can_create(
        purchase_contract.PURCHASE_ORDER_DOCTYPE
    ) is not True:
        gateway.deny()

    try:
        detail = gateway.native_purchase_order_draft_detail(payload)
    except Exception:
        # Nothing has been dispatched yet; an unreadable target is a refusal.
        gateway.deny()
    _deny_unreadable_masters(gateway, detail, payload)

    prepared = _prepare_draft(detail, payload)
    refused = _refusal(
        gateway, purchase_contract.draft_problems(detail, payload)
    )
    if refused is not None:
        return refused
    if purchase_contract.prepared_problem(
        purchase_contract.DRAFT_KIND, prepared
    ) is not None:
        return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)

    expected_effects = purchase_contract.expected_draft(detail, payload)
    try:
        baseline = gateway.native_purchase_baseline(
            purchase_contract.DRAFT_KIND,
            _baseline_warehouses(payload),
            _baseline_item(payload),
            payload.get("purchase_order"),
        )
    except Exception:
        # Still before any mutation: without a baseline the "no stock effect"
        # claim is unverifiable, so nothing is dispatched.
        gateway.deny()

    try:
        reference = gateway.create_native_purchase_order_draft(prepared)
        facts = gateway.native_purchase_order_facts(
            reference, expected_effects, baseline
        )
        if (
            purchase_contract.side_effect_problem(facts, expected_effects)
            is not None
        ):
            raise patient_write.WriteUncertain()
        gateway.commit()
    except Exception:
        # The insert may have reached the installed controller; never claim
        # that nothing changed.
        _rollback_or_raise(gateway)
        return _respond_error(gateway, HTTP_CONFLICT, UNCERTAIN_CODE)

    return _draft_success(gateway, reference)


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


# -- read surface ----------------------------------------------------------
#
# Converted-workflow inspection only. Nothing here resolves write authority,
# reads a supplier/item/quantity/amount value or dispatches a native mutation.
# The read lane is independent of the purchasing write flag: turning writes off
# must not hide a purchase order that already exists.


def _strict_text(value, max_length):
    """Bounded, control-free text without surrounding whitespace, else ``None``."""

    if not is_valid_text(value, max_length) or value != value.strip():
        return None
    return value


def _read_gate(gateway):
    """Authenticated/no-store gate for the purchase-order read surface.

    It requires the authenticated session, a purchasing clinic role and the
    exact approved development site. Every record read afterwards still needs
    the current user's own native Purchase Order read permission.
    """

    settings, view, _role = common.authorize(gateway, PO_ROLE_CODES)
    if aliases.native_read_scope_enabled(gateway) is not True:
        gateway.deny()
    return settings, view


def _purchase_order_item(gateway, native_purchase_order_id):
    """Return the three-field item for one readable order, or ``None``."""

    try:
        detail = gateway.native_purchase_order_view(native_purchase_order_id)
    except Exception:
        return None
    if not isinstance(detail, dict):
        return None
    purchase_order_id = detail.get("purchase_order_id")
    status = detail.get("status")
    docstatus = detail.get("docstatus")
    if purchase_order_id is None or status is None:
        return None
    if (
        _strict_text(purchase_order_id, purchase_contract.MAX_NAME_LENGTH)
        is None
    ):
        return None
    if purchase_order_id != native_purchase_order_id:
        return None
    if _strict_text(status, MAX_PURCHASE_STATUS_LENGTH) is None:
        return None
    if (
        not isinstance(docstatus, int)
        or isinstance(docstatus, bool)
        or docstatus not in (0, 1, 2)
    ):
        return None
    return {
        "purchase_order_id": purchase_order_id,
        "status": status,
        "docstatus": docstatus,
    }


def view(gateway, purchase_order_id=None):
    """Return one readable purchase order's native id, status and docstatus.

    The caller supplies a native docname; only the identifier it supplied is
    echoed back. No supplier, item, quantity or amount is returned. A
    malformed, unknown or unreadable value is a 403.
    """

    _read_gate(gateway)
    if (
        _strict_text(purchase_order_id, purchase_contract.MAX_NAME_LENGTH)
        is None
    ):
        gateway.deny()
    item = _purchase_order_item(gateway, purchase_order_id)
    if item is None:
        gateway.deny()
    return {"purchase_order": item}


def drafts(gateway, supplier=None):
    """Return the readable draft purchase orders for one Supplier.

    Discovery is bounded and permission-filtered: the caller's own native
    Supplier read authority is required before the query, the list is read
    through the framework's permission-filtered ``get_list``, each row is
    re-checked under the caller's own native Purchase Order read authority, and
    an over-full, malformed or duplicated page refuses the whole read rather
    than returning a partial list. This is enough to inspect an unknown draft
    outcome without a fence receipt.
    """

    _read_gate(gateway)
    if _strict_text(supplier, purchase_contract.MAX_TEXT_LENGTH) is None:
        gateway.deny()
    if gateway.native_doctype_can_read(
        purchase_contract.SUPPLIER_DOCTYPE, supplier
    ) is not True:
        gateway.deny()
    try:
        names = gateway.native_purchase_order_drafts(
            supplier, MAX_NATIVE_PURCHASE_ORDERS
        )
    except Exception:
        gateway.deny()
    if not isinstance(names, (list, tuple)):
        gateway.deny()

    items = []
    seen = set()
    for name in names:
        if _strict_text(name, purchase_contract.MAX_NAME_LENGTH) is None:
            gateway.deny()
        if name in seen:
            gateway.deny()
        seen.add(name)
        item = _purchase_order_item(gateway, name)
        if item is None:
            gateway.deny()
        items.append(item)
    return {"items": items}
