"""Local synthetic Patient create/edit orchestration.

Order is fixed and test-asserted:

1. shared read gate (no-store, session, site configuration, clinic role),
2. explicit same-session CSRF token check (ignores framework bypasses),
3. separate default-off native-write capability for the approved site,
4. private deployment marker bound to the fence store,
5. strict body validation,
6. native Patient create/write authority **before** any fence or alias access,
7. live Gender check and healthcare side-effect/target checks,
8. create-only: construct the Patient, apply controller defaults, and reject
   any customer/contact/address/email/phone/user/invite trigger before insert,
9. persistent operation fence (site-wide unresolved block),
10. native ``Patient`` insert/save with no ``ignore_permissions``,
11. one explicit site DB commit,
12. terminal receipt appended to the fence,
13. success DTO construction; if it fails after the committed terminal, write a
    durable site-wide manual-investigation latch under the same fence.

A post-dispatch failure of any kind is uncertain: the key stays locked and the
response is ``409 {"code": "WRITE_LOCKED"}``. There is no automatic retry, no
inferred no-commit and no success claim after a failed receipt. The endpoint
rolls back the partial transaction; a rollback failure raises
``RollbackFailed`` instead of returning a normal error. A post-commit success
construction failure latches the whole deployment; if neither latch nor its
fallback can be recorded, ``ManualInvestigationRequired`` is raised to Frappe
cleanup rather than returning a normal response.
"""

from clinic_ui.core import dto, patient_alias, patient_write
from clinic_ui.core.policy import PATIENT_WRITE_ROLE_CODES
from clinic_ui.endpoints import aliases, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"
STALE_CODE = "STALE_VERSION"


def _respond_error(gateway, status, code):
    return gateway.respond_write(status, {"code": code})


def _mark_unknown(claim, reason):
    """Append an UNKNOWN terminal when possible; never raise from this helper."""

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
    request-end commit. The caller has already marked the operation ``UNKNOWN``,
    so the lock is retained even when rollback itself fails.
    """

    try:
        gateway.rollback()
    except BaseException as exc:
        raise RollbackFailed(
            "native write rollback failed after a post-dispatch error"
        ) from exc


class SuccessUncertain(Exception):
    """The native write committed but the success response could not be built."""


class ManualInvestigationRequired(RuntimeError):
    """Both the site latch and the operation fallback could not be persisted.

    Raised to Frappe cleanup instead of returning a response that could be
    mistaken for success or a retryable conflict. The write is already
    committed; an operator must inspect the deployment.
    """


def _success(gateway, status, alias, native_patient_id, ack_token):
    """Build the success response; any failure is post-commit uncertainty."""

    try:
        detail = gateway.native_patient_view(native_patient_id)
        if detail is None:
            raise ValueError("native detail is missing after commit")
        payload = dto.patient_write_result(status, alias, detail, ack_token)
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


def _latch_or_raise(gateway, actor, kind, operation_key, reason):
    """Persist a site-wide latch; fall back to marking the operation UNKNOWN.

    The reserved latch is the primary durable block. If it cannot be written,
    a derived reserved latch key is written under the same lock, which also
    makes the deployment-wide unresolved check block every new key. If both
    records fail, raise to Frappe cleanup: no success is returned.
    """

    try:
        gateway.latch_manual_investigation(reason)
        return
    except Exception:
        try:
            gateway.latch_operation_uncertain(
                actor, kind, operation_key, reason
            )
            return
        except Exception as exc:
            raise ManualInvestigationRequired(reason) from exc


def create(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_write(
        gateway, PATIENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = patient_write.validate_create(body)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    if not gateway.native_patient_can_create():
        gateway.deny()
    if not gateway.native_gender_exists(payload["fields"]["sex"]):
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    if gateway.healthcare_settings_side_effects_unsafe():
        gateway.deny()

    try:
        prepared = gateway.prepare_native_patient_create(payload["fields"])
    except patient_write.UnsafeSideEffect:
        gateway.deny()

    try:
        alias = patient_alias.build_alias(
            settings.epoch_secret,
            gateway.site,
            actor,
            payload["operation_key"],
        )
        digest = patient_write.operation_digest(settings.epoch_secret, payload)
    except (patient_alias.AliasError, patient_write.WriteInputError):
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    if any(alias == allowed for allowed, _native in settings.allowlist):
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)

    native_patient_id = None
    try:
        with gateway.patient_write_fence(
            actor, "create", payload["operation_key"], digest
        ) as claim:
            try:
                native_patient_id = gateway.insert_native_patient(prepared)
                gateway.commit()
                receipt = patient_write.encode_receipt(
                    kind="create",
                    alias=alias,
                    native_patient_id=native_patient_id,
                )
                claim.commit(receipt)
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.StaleVersion,
                patient_write.WriteUncertain,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(claim, "native Patient create outcome not established")
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except patient_write.WriteLocked:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)
    except patient_write.WriteConflict:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StoreUnavailable:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)
    except patient_write.WriteUncertain:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)

    try:
        ack_token = patient_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            "create",
            payload["operation_key"],
            receipt,
        )
        return _success(gateway, "created", alias, native_patient_id, ack_token)
    except (SuccessUncertain, patient_write.WriteInputError):
        _latch_or_raise(
            gateway,
            actor,
            "create",
            payload["operation_key"],
            "post-commit create response construction failed",
        )
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def update(gateway, body, csrf_token=None):
    settings, view, _role = common.authorize_write(
        gateway, PATIENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = patient_write.validate_update(body)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    if not gateway.native_patient_can_write():
        gateway.deny()

    try:
        native_patient_id = aliases.resolve(
            gateway, settings, actor, payload["workspace_patient_id"]
        )
    except (
        patient_write.WriteLocked,
        patient_write.WriteConflict,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)

    if not gateway.native_patient_can_write(native_patient_id):
        gateway.deny()
    if gateway.healthcare_settings_side_effects_unsafe():
        gateway.deny()
    if gateway.native_patient_target_unsafe(native_patient_id):
        gateway.deny()

    current_modified = gateway.native_patient_modified(native_patient_id)
    if current_modified is None:
        gateway.deny()
    if not patient_write.modified_matches(
        current_modified, payload["base_modified"]
    ):
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)

    try:
        digest = patient_write.operation_digest(settings.epoch_secret, payload)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    try:
        with gateway.patient_write_fence(
            actor, "update", payload["operation_key"], digest
        ) as claim:
            try:
                gateway.native_patient_update(
                    native_patient_id,
                    payload["fields"],
                    payload["base_modified"],
                )
                gateway.commit()
                receipt = patient_write.encode_receipt(
                    kind="update",
                    alias=payload["workspace_patient_id"],
                    native_patient_id=native_patient_id,
                )
                claim.commit(receipt)
            except patient_write.StaleVersion:
                _mark_unknown(claim, "stale version at commit")
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
            except (
                patient_write.WriteLocked,
                patient_write.WriteConflict,
                patient_write.StoreUnavailable,
                patient_write.WriteUncertain,
            ):
                _rollback_or_raise(gateway)
                raise
            except Exception:
                _mark_unknown(claim, "native Patient update outcome not established")
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except patient_write.WriteLocked:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)
    except patient_write.WriteConflict:
        return _respond_error(gateway, HTTP_CONFLICT, CONFLICT_CODE)
    except patient_write.StoreUnavailable:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)
    except patient_write.WriteUncertain:
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)

    try:
        ack_token = patient_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            "update",
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway,
            "updated",
            payload["workspace_patient_id"],
            native_patient_id,
            ack_token,
        )
    except (SuccessUncertain, patient_write.WriteInputError):
        _latch_or_raise(
            gateway,
            actor,
            "update",
            payload["operation_key"],
            "post-commit update response construction failed",
        )
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)


def ack(gateway, body, csrf_token=None):
    """Explicitly acknowledge confirmed success and release the write barrier.

    The actor comes from the session, and the token is verified against the
    durable committed receipt for that actor/kind/key. A missing, malformed,
    wrong-actor or non-committed acknowledgement returns ``409 WRITE_LOCKED``
    and leaves the barrier in place. This endpoint never retries itself.
    """

    settings, view, _role = common.authorize_write(
        gateway, PATIENT_WRITE_ROLE_CODES, csrf_token
    )

    try:
        payload = patient_write.validate_ack(body)
    except patient_write.WriteInputError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    try:
        status = gateway.acknowledge_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except (
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)

    if status in ("acked", "already"):
        return gateway.respond_write(HTTP_OK, {"acknowledged": True})
    return _respond_error(gateway, HTTP_CONFLICT, LOCKED_CODE)
