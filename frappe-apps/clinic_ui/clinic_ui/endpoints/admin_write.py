"""Designed admin users + masters writes (default off).

Order is fixed and test-asserted for both operations:

1. shared read gate plus the explicit same-session CSRF and admin-write
   deployment gates,
2. the installed-field preflight and the caller's native authority,
3. strict body validation,
4. every pre-fence refusal with a named line: the assignable-role boundary
   (System Manager / Healthcare Administrator may never be assigned), missing
   installed roles, duplicate user/practitioner/master identifiers, an
   unknown master variant and an exhausted budget,
5. the persistent site-wide fence, then the same decisions again against
   current rows,
6. one native mutation (a User with its roles and optional practitioner link,
   or one master row with its bundled price/account child) and one closed-world
   re-read,
7. one explicit commit and the terminal receipt that carries the created name
   only — never a credential,
8. the one-shot acknowledgement token.

Every designed refusal happens **before** the fence claim. A post-claim
refusal is a bug: it rolls back and locks the operation rather than leaving the
site latched. A post-dispatch failure is uncertain: the key stays locked and
the response is ``409 {"code": "WRITE_LOCKED"}`` with no retry.
"""

from clinic_ui.core import admin_contract, patient_write
from clinic_ui.core.policy import ADMIN_ROLE_CODES
from clinic_ui.endpoints import common

HTTP_OK = 200
HTTP_REFUSED = 417
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"

CREATED_RESULT = "created"


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class ManualInvestigationRequired(RuntimeError):
    """Neither the site latch nor the operation fallback could be persisted."""


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _locked_response(gateway):
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def _refusal(gateway, problem):
    """Return one named pre-fence refusal as the fixed safe envelope."""

    if not problem:
        return None
    return _respond_error(gateway, HTTP_REFUSED, VALIDATION_CODE)


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
            "native admin write rollback failed"
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

    limit = admin_contract.budget(kind)
    try:
        records = gateway.list_committed_admin_receipts(limit, kind=kind)
    except admin_contract.AdminBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise admin_contract.AdminBudgetExhausted()


def _shared_gate(gateway, csrf_token):
    settings, view, role = common.authorize_admin_write(
        gateway, ADMIN_ROLE_CODES, csrf_token
    )
    if role not in ADMIN_ROLE_CODES:
        gateway.deny()
    if gateway.native_admin_install_problem() is not None:
        gateway.deny()
    if gateway.native_admin_can_write() is not True:
        gateway.deny()
    return settings, view, role


def _user_native_payload(payload, roles):
    """The one-mutation user payload; the password is caller-supplied."""

    return {
        "email": payload["email"],
        "username": payload["username"],
        "full_name": payload["full_name"],
        "roles": list(roles),
        "practitioner": payload["practitioner"],
        "password": payload["password"],
    }


def _user_problems(gateway, settings, payload):
    """Every pre-fence user decision, in operator order."""

    problem = admin_contract.role_problem(
        settings.as_role_map(), payload["clinic_role_code"]
    )
    if problem:
        return problem
    roles = admin_contract.assignable_roles(
        settings.as_role_map(), payload["clinic_role_code"]
    )
    if not roles:
        return "no native role for the clinic code"
    if gateway.native_admin_missing_roles(roles):
        return "an assignable role is not installed"
    identifier = admin_contract.user_identifier(payload)
    if gateway.native_admin_user_exists(identifier):
        return "user already exists"
    username = payload["username"]
    if username and username != identifier and gateway.native_admin_user_exists(
        username
    ):
        return "username already exists"
    practitioner = payload["practitioner"]
    if practitioner and not gateway.native_admin_practitioner_exists(practitioner):
        return "practitioner does not exist"
    return None


def _master_problems(gateway, payload):
    """Every pre-fence master decision for the requested variant."""

    master_type = payload["master_type"]
    body = payload["payload"]
    # Prerequisites first (Amendment A1): the install has no UOM and no Supplier
    # Group rows, so these are decided here — never after the fence claim.
    problem = admin_contract.prerequisite_problem(
        master_type, gateway.native_admin_prerequisites()
    )
    if problem:
        return problem
    identifier = admin_contract.identifier_for(master_type, body)
    if master_type == "practitioner":
        # Amendment A1.1: the installed row is autonamed, so the supplied name is
        # not a key. Exactly-once is per linked user (checked here) plus the
        # one-shot fence receipt.
        user = body["user"]
        if user:
            if not gateway.native_admin_user_exists(user):
                return "practitioner user does not exist"
            if not gateway.native_admin_user_enabled(user):
                return "practitioner user is disabled"
            if gateway.native_admin_practitioner_for_user(user):
                return "practitioner already exists for the user"
    elif gateway.native_admin_master_exists(master_type, identifier):
        return "master already exists"
    if master_type == "item":
        if not gateway.native_admin_master_exists("uom", body["stock_uom"]):
            return "stock uom is not installed"
        if gateway.native_admin_item_price_exists(
            body["item_code"], body["price_list"]
        ):
            return "item price already exists"
    return None


def _user_digest_fields(payload):
    return {
        "identifier": admin_contract.user_identifier(payload),
        "clinic_role_code": payload["clinic_role_code"],
        "practitioner": payload["practitioner"] or "",
    }


def _master_digest_fields(payload):
    return {
        "identifier": admin_contract.identifier_for(
            payload["master_type"], payload["payload"]
        ),
        "master_type": payload["master_type"],
    }


def _finish_fence(gateway, claim, *, kind, identifier, master_type=""):
    gateway.commit()
    receipt = admin_contract.encode_receipt(
        kind=kind, identifier=identifier, master_type=master_type
    )
    claim.commit(receipt)
    return receipt


def _success_user(gateway, user, roles, practitioner, ack_token):
    try:
        payload = {
            "status": CREATED_RESULT,
            "user": {
                "user_id": user,
                "roles": list(roles),
                "practitioner": practitioner,
            },
            "ackToken": ack_token,
        }
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _success_master(gateway, master_type, name, ack_token):
    try:
        payload = {
            "status": CREATED_RESULT,
            "master": {
                "master_type": master_type,
                "master_id": name,
            },
            "ackToken": ack_token,
        }
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def create_user(gateway, body, csrf_token=None):
    """Create one user with its roles and optional practitioner link."""

    settings, view, _role = _shared_gate(gateway, csrf_token)
    try:
        payload = admin_contract.validate_create_user(body)
    except admin_contract.AdminContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    refused = _refusal(gateway, _user_problems(gateway, settings, payload))
    if refused is not None:
        return refused

    kind = admin_contract.ADMIN_USER_KIND
    try:
        digest = admin_contract.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_user_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
    except admin_contract.AdminContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    user = None
    roles = ()
    try:
        with gateway.admin_write_fence(
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
                if fence_role not in ADMIN_ROLE_CODES:
                    gateway.deny()
                if _user_problems(gateway, fence_settings, payload) is not None:
                    raise admin_contract.AdminTargetNotEligible()
                roles = admin_contract.assignable_roles(
                    fence_settings.as_role_map(),
                    payload["clinic_role_code"],
                )
                if not roles:
                    raise admin_contract.AdminTargetNotEligible()
                user = gateway.create_native_admin_user(
                    _user_native_payload(payload, roles)
                )
                facts = gateway.native_admin_user_facts(user)
                expected = {
                    "identifier": user,
                    "roles": roles,
                    "practitioner": payload["practitioner"],
                }
                if admin_contract.user_facts_problem(facts, expected) is not None:
                    raise patient_write.WriteUncertain()
                _finish_fence(
                    gateway, claim, kind=kind, identifier=user
                )
            except (
                admin_contract.AdminTargetNotEligible,
                admin_contract.AdminBudgetExhausted,
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
                    "native admin user create outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except admin_contract.AdminTargetNotEligible:
        return _locked_response(gateway)
    except admin_contract.AdminBudgetExhausted:
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
        ack_token = admin_contract.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            admin_contract.encode_receipt(kind=kind, identifier=user),
        )
        return _success_user(
            gateway, user, roles, payload["practitioner"], ack_token
        )
    except (SuccessUncertain, admin_contract.AdminContractError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit admin user response construction failed",
        )
        return _locked_response(gateway)


def edit_master(gateway, body, csrf_token=None):
    """Create one master row for its designed DocType variant."""

    settings, view, _role = _shared_gate(gateway, csrf_token)
    try:
        payload = admin_contract.validate_edit_master(body)
    except admin_contract.AdminContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    master_type = payload["master_type"]
    if (
        gateway.native_admin_can_create(
            admin_contract.MASTER_DOCTYPES[master_type]
        )
        is not True
    ):
        gateway.deny()
    refused = _refusal(gateway, _master_problems(gateway, payload))
    if refused is not None:
        return refused

    kind = admin_contract.ADMIN_MASTER_KIND
    try:
        digest = admin_contract.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_master_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
    except admin_contract.AdminContractError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
        patient_write.WriteLocked,
    ):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    name = None
    try:
        with gateway.admin_write_fence(
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
                if fence_role not in ADMIN_ROLE_CODES:
                    gateway.deny()
                if (
                    gateway.native_admin_can_create(
                        admin_contract.MASTER_DOCTYPES[master_type]
                    )
                    is not True
                ):
                    gateway.deny()
                if _master_problems(gateway, payload) is not None:
                    raise admin_contract.AdminTargetNotEligible()
                name = gateway.create_native_admin_master(
                    master_type, dict(payload["payload"])
                )
                facts = gateway.native_admin_master_facts(master_type, name)
                expected = {
                    "identifier": name,
                    "payload": payload["payload"],
                }
                if (
                    admin_contract.master_facts_problem(
                        master_type, facts, expected
                    )
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                _finish_fence(
                    gateway,
                    claim,
                    kind=kind,
                    identifier=name,
                    master_type=master_type,
                )
            except (
                admin_contract.AdminTargetNotEligible,
                admin_contract.AdminBudgetExhausted,
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
                    "native admin master create outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except admin_contract.AdminTargetNotEligible:
        return _locked_response(gateway)
    except admin_contract.AdminBudgetExhausted:
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
        receipt = admin_contract.encode_receipt(
            kind=kind, identifier=name, master_type=master_type
        )
        ack_token = admin_contract.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success_master(gateway, master_type, name, ack_token)
    except (SuccessUncertain, admin_contract.AdminContractError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit admin master response construction failed",
        )
        return _locked_response(gateway)


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed admin operation."""

    settings, view, _role = common.authorize_admin_write(
        gateway, ADMIN_ROLE_CODES, csrf_token
    )
    try:
        payload = admin_contract.validate_ack(body)
        status = gateway.acknowledge_admin_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except admin_contract.AdminContractError:
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
