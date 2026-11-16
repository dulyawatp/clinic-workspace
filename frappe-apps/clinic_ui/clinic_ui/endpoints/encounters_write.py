"""Designed Patient Encounter draft, complete and amendment writes (default off).

Order is fixed and test-asserted:

1. shared read gate plus the explicit same-session CSRF and encounter-write
   deployment gates,
2. pinned boundary check and the installed-field preflight; either drift denies
   before validation,
3. strict body validation; server-owned fields (docstatus, status, practitioner,
   company, native identifiers) are refused,
4. per-section role rule: DOC writes any section, NUR writes vitals only,
5. the actor's own committed draft is resolved from the durable fence receipt;
   the static directory and another actor's encounter are never writable,
6. pre-fence target decisions: the encounter must still be a draft (or a
   finalized, completed encounter for an amendment), the linked appointment must
   be `Open`/`Checked In` for the completion cascade, the required-before-complete
   field set must be present with a per-field problem, and the durable budget
   must remain,
7. the persistent site-wide fence, then the native row lock and the same
   pre-fence decisions again against current rows,
8. the designed native operation (draft save, submit with the expected cascade,
   or a new Clinical Note addendum), a closed-world re-read, one explicit commit
   and the terminal receipt,
9. the one-shot acknowledgement token.

Every designed refusal in 3-6 happens **before** the fence claim. A refusal
inside a held fence would terminate the operation ``UNKNOWN`` and latch the whole
site. A post-dispatch failure is uncertain: the key stays locked and the response
is ``409 {"code": "WRITE_LOCKED"}`` with no retry.
"""

from clinic_ui.core import (
    dto,
    encounter_contract,
    encounter_write,
    patient_write,
)
from clinic_ui.core.policy import (
    ENCOUNTER_AMEND_ROLE_CODES,
    ENCOUNTER_COMPLETE_ROLE_CODES,
    ENCOUNTER_ROLE_CODES,
)
from clinic_ui.endpoints import appointments, common

HTTP_OK = 200
HTTP_UNPROCESSABLE = 422
HTTP_CONFLICT = 409

VALIDATION_CODE = "VALIDATION"
LOCKED_CODE = "WRITE_LOCKED"
CONFLICT_CODE = "OPERATION_CONFLICT"
STALE_CODE = "STALE_VERSION"

DRAFT_RESULT = "draft_saved"
COMPLETE_RESULT = "completed"
AMEND_RESULT = "amended"


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
            "native encounter write rollback failed"
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

    limit = encounter_write.budget(kind)
    try:
        records = gateway.list_committed_encounter_receipts(limit, kind=kind)
    except encounter_write.EncounterBudgetExhausted:
        return False
    return len(records) < limit


def _require_budget(gateway, kind):
    if not _budget_remaining(gateway, kind):
        raise encounter_write.EncounterBudgetExhausted()


def _values(state):
    return state.as_dict()


def _alias_for(settings, gateway, actor, operation_key):
    return encounter_write.build_alias(
        settings.epoch_secret, gateway.site, actor, operation_key
    )


def _resolve_target(gateway, settings, actor, alias, *, allow_cross_actor=False):
    """The actor's own committed draft, or ``None`` (nothing was written).

    ``allow_cross_actor`` is used only by the NUR vitals path: the visit draft is
    normally opened by the doctor, so a nurse must be able to reach that same
    draft. The alias must still match a durable committed ``encounter_draft``
    receipt byte for byte, and the caller's native Patient Encounter write check
    still runs before the fence. No other actor may use it.
    """

    operation_key = encounter_write.parse_alias(
        settings.epoch_secret, gateway.site, actor, alias
    )
    if operation_key is not None:
        receipt = gateway.lookup_encounter_receipt(
            actor, encounter_contract.ENCOUNTER_DRAFT_KIND, operation_key
        )
        if receipt is not None:
            decoded = encounter_write.decode_receipt(receipt)
            if decoded.kind == encounter_contract.ENCOUNTER_DRAFT_KIND:
                if decoded.alias == alias:
                    return decoded.native_encounter_id
    if not allow_cross_actor:
        return None
    try:
        records = gateway.list_committed_encounter_receipts(
            encounter_contract.MAX_ROWS, kind=encounter_contract.ENCOUNTER_DRAFT_KIND
        )
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        raise
    for record in records:
        try:
            decoded = encounter_write.decode_receipt(record.receipt)
        except encounter_write.EncounterWriteError:
            continue
        if decoded.alias == alias:
            return decoded.native_encounter_id
    return None


def _appointment_target(gateway, settings, actor, workspace_appointment_id):
    """Resolve the actor's own booking for the appointment linkage check."""

    try:
        target = appointments.own_booking(
            gateway, settings, actor, workspace_appointment_id
        )
    except patient_write.WriteLocked:
        raise
    if target is None:
        return None
    return target.native_appointment_id


def _appointment_detail(gateway, native_appointment_id):
    detail = gateway.native_appointment_action_detail(native_appointment_id)
    if detail is None:
        return None
    return detail


def _digest_fields(payload):
    fields = {
        "workspace_patient_id": payload.get("workspace_patient_id"),
        "encounter": payload.get("encounter"),
        "appointment": payload.get("appointment"),
    }
    if payload.get("fields"):
        fields["fields"] = payload["fields"]
    if payload.get("addendum"):
        fields["addendum"] = payload["addendum"]
    if "allergy_reviewed" in payload:
        fields["allergy_reviewed"] = payload["allergy_reviewed"]
    return fields


def _finish_fence(gateway, claim, kind, alias, native_encounter_id):
    gateway.commit()
    receipt = encounter_write.encode_receipt(
        kind=kind, alias=alias, native_encounter_id=native_encounter_id
    )
    claim.commit(receipt)
    return receipt


def _success(
    gateway,
    settings,
    result_status,
    alias,
    native_encounter_id,
    ack_token,
    *,
    addendum=None,
):
    try:
        detail = gateway.native_encounter_detail(native_encounter_id)
        if detail is None:
            raise ValueError("native encounter detail is missing after commit")
        workspace_patient_id = appointments.workspace_patient_for(
            settings, detail["patient"]
        )
        if workspace_patient_id is None:
            raise ValueError("native patient is outside the allowlist")
        payload_detail = dto.encounter_detail(
            encounter_id=alias,
            workspace_patient_id=workspace_patient_id,
            state=(
                "completed" if detail["docstatus"] == FINAL_DOCSTATUS else "draft"
            ),
            status=detail["status"],
            modified=detail["modified"],
        )
        payload = dto.encounter_write_result(
            result_status, payload_detail, ack_token, addendum=addendum
        )
        gateway.strip_message_channels()
        return gateway.respond_write(HTTP_OK, payload)
    except Exception as exc:
        raise SuccessUncertain() from exc


FINAL_DOCSTATUS = encounter_contract.FINAL_DOCSTATUS
DRAFT_DOCSTATUS = encounter_contract.DRAFT_DOCSTATUS


def _shared_gate(gateway, csrf_token):
    settings, view, role = common.authorize_encounter_write(
        gateway, ENCOUNTER_ROLE_CODES, csrf_token
    )
    if gateway.native_encounter_boundary_problem() is not None:
        gateway.deny()
    if gateway.native_encounter_install_problem() is not None:
        gateway.deny()
    return settings, view, role


def _resolve_patient_or_deny(gateway, settings, workspace_patient_id):
    native_patient_id = appointments.native_patient_for(
        settings, workspace_patient_id
    )
    if native_patient_id is None:
        gateway.deny()
    return native_patient_id


def _linked_appointment(
    gateway, settings, actor, workspace_patient_id, workspace_appointment_id
):
    """Return ``(native_id, detail)`` for the actor's own linked booking."""

    native_appointment_id = _appointment_target(
        gateway, settings, actor, workspace_appointment_id
    )
    if native_appointment_id is None:
        return None, None
    detail = _appointment_detail(gateway, native_appointment_id)
    if detail is None:
        return None, None
    if detail.get("patient") != native_patient_id_for(
        settings, workspace_patient_id
    ):
        return None, None
    return native_appointment_id, detail


def native_patient_id_for(settings, workspace_patient_id):
    return appointments.native_patient_for(settings, workspace_patient_id)


def _draft_fence_body(
    gateway,
    settings,
    view,
    payload,
    native_patient_id,
    *,
    alias,
    is_create,
    native_appointment_id,
    kind,
    csrf_token,
):
    """Run one draft save inside the held fence and return its receipt."""

    with gateway.encounter_write_fence(
        view.user, kind, payload["operation_key"], payload["_digest"]
    ) as claim:
        try:
            _require_budget(gateway, kind)
            gateway.reset_native_read_snapshot()
            fence_settings, fence_view, fence_role = _shared_gate(
                gateway, csrf_token
            )
            if fence_view.user != view.user:
                raise patient_write.WriteUncertain()
            if encounter_contract.section_problem(
                fence_role, payload["fields"]
            ) is not None:
                raise patient_write.UnsafeSideEffect("section role changed")
            if (
                native_patient_id_for(
                    fence_settings, payload["workspace_patient_id"]
                )
                != native_patient_id
            ):
                raise patient_write.UnsafeSideEffect("configured patient changed")
            if is_create:
                try:
                    booking = appointments.own_booking(
                        gateway,
                        fence_settings,
                        fence_view.user,
                        payload["appointment"],
                    )
                except patient_write.WriteLocked:
                    raise encounter_write.EncounterTargetNotEligible() from None
                if (
                    booking is None
                    or booking.native_appointment_id != native_appointment_id
                ):
                    raise patient_write.UnsafeSideEffect(
                        "linked booking changed"
                    )
                fresh = gateway.native_appointment_action_detail(
                    native_appointment_id
                )
                if fresh is None or fresh.get("patient") != native_patient_id:
                    raise patient_write.UnsafeSideEffect("linked booking changed")
                if (
                    fresh.get("status")
                    not in encounter_contract.CASCADE_SOURCE_STATUSES
                ):
                    raise encounter_write.EncounterTargetNotEligible()
                if (
                    gateway.native_appointment_can_write(native_appointment_id)
                    is not True
                ):
                    gateway.deny()
            else:
                state = gateway.lock_encounter_target(
                    payload["_native_encounter_id"], payload["base_modified"]
                )
                values = _values(state)
                if values["patient"] != native_patient_id:
                    raise patient_write.UnsafeSideEffect("target patient changed")
                if values["modified"] != payload["base_modified"]:
                    raise patient_write.WriteConflict()
                if encounter_contract.draft_target_problem(values) is not None:
                    raise encounter_write.EncounterTargetNotEligible()
                if gateway.native_patient_can_read(native_patient_id) is not True:
                    gateway.deny()
                gateway.save_native_encounter_draft(state, payload)
                native_id = payload["_native_encounter_id"]
            if is_create:
                native_id = gateway.create_native_encounter_draft(payload)
            receipt = _finish_fence(
                gateway, claim, kind, alias, native_id
            )
        except encounter_write.EncounterTargetNotEligible:
            _rollback_or_raise(gateway)
            raise
        except encounter_write.EncounterBudgetExhausted:
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
                claim, "native Patient Encounter draft outcome not established"
            )
            _rollback_or_raise(gateway)
            raise patient_write.WriteUncertain() from None
    return receipt


def save_draft(gateway, body, csrf_token=None):
    """Create or update a draft encounter (roles DOC; NUR vitals only)."""

    settings, view, role = _shared_gate(gateway, csrf_token)
    try:
        payload = encounter_write.validate_save_draft(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    problem = encounter_contract.section_problem(role, payload["fields"])
    if problem is not None:
        gateway.deny()

    actor = view.user
    native_patient_id = _resolve_patient_or_deny(
        gateway, settings, payload["workspace_patient_id"]
    )

    alias = payload["encounter"]
    is_create = alias is None
    native_encounter_id = None
    native_appointment_id = None
    appointment_detail = None

    if is_create:
        try:
            native_appointment_id, appointment_detail = _linked_appointment(
                gateway,
                settings,
                actor,
                payload["workspace_patient_id"],
                payload["appointment"],
            )
        except (patient_write.WriteLocked, patient_write.StoreUnavailable):
            return _locked_response(gateway)
        if native_appointment_id is None:
            return _locked_response(gateway)
        alias = _alias_for(settings, gateway, actor, payload["operation_key"])
        if appointment_detail.get("status") not in (
            encounter_contract.CASCADE_SOURCE_STATUSES
        ):
            # A draft is only opened on an appointment the completion cascade
            # can later close.
            return _locked_response(gateway)
        if gateway.native_appointment_can_write(native_appointment_id) is not True:
            gateway.deny()
    else:
        try:
            native_encounter_id = _resolve_target(
                gateway,
                settings,
                actor,
                alias,
                allow_cross_actor=role == "NUR",
            )
        except (
            patient_write.WriteLocked,
            patient_write.StoreUnavailable,
            patient_write.WriteUncertain,
        ):
            return _locked_response(gateway)
        if native_encounter_id is None:
            return _locked_response(gateway)

    if gateway.native_patient_can_read(native_patient_id) is not True:
        gateway.deny()
    if gateway.healthcare_encounter_side_effects_unsafe() is not False:
        gateway.deny()

    detail = None
    if is_create:
        if gateway.native_encounter_can_create() is not True:
            gateway.deny()
    else:
        detail = gateway.native_encounter_action_detail(native_encounter_id)
        if detail is None:
            gateway.deny()
        if detail.get("patient") != native_patient_id:
            return _locked_response(gateway)
        if encounter_contract.draft_target_problem(detail) is not None:
            return _locked_response(gateway)
        if gateway.native_encounter_can_write(native_encounter_id) is not True:
            gateway.deny()

    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=encounter_contract.ENCOUNTER_DRAFT_KIND,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(
            gateway, encounter_contract.ENCOUNTER_DRAFT_KIND
        )
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (patient_write.StoreUnavailable, patient_write.WriteUncertain):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    payload["_digest"] = digest
    payload["_native_patient_id"] = native_patient_id
    payload["_native_appointment_id"] = native_appointment_id
    if not is_create:
        payload["_native_encounter_id"] = native_encounter_id

    try:
        receipt = _draft_fence_body(
            gateway,
            settings,
            view,
            payload,
            native_patient_id,
            alias=alias,
            is_create=is_create,
            native_appointment_id=native_appointment_id,
            kind=encounter_contract.ENCOUNTER_DRAFT_KIND,
            csrf_token=csrf_token,
        )
    except encounter_write.EncounterTargetNotEligible:
        return _locked_response(gateway)
    except encounter_write.EncounterBudgetExhausted:
        return _locked_response(gateway)
    except patient_write.WriteLocked:
        return _locked_response(gateway)
    except patient_write.WriteConflict:
        return _respond_error(gateway, HTTP_CONFLICT, STALE_CODE)
    except patient_write.StoreUnavailable:
        return _locked_response(gateway)
    except patient_write.WriteUncertain:
        return _locked_response(gateway)
    except patient_write.UnsafeSideEffect:
        gateway.deny()

    if is_create:
        native_encounter_id = gateway.receipt_native_encounter_id(receipt)

    try:
        ack_token = encounter_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            encounter_contract.ENCOUNTER_DRAFT_KIND,
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway, settings, DRAFT_RESULT, alias, native_encounter_id, ack_token
        )
    except (SuccessUncertain, encounter_write.EncounterWriteError):
        _latch_or_raise(
            gateway,
            actor,
            encounter_contract.ENCOUNTER_DRAFT_KIND,
            payload["operation_key"],
            "post-commit encounter draft response construction failed",
        )
        return _locked_response(gateway)


def complete(gateway, body, csrf_token=None):
    """Submit a draft encounter and verify the expected native cascade (DOC)."""

    settings, view, role = _shared_gate(gateway, csrf_token)
    if role not in ENCOUNTER_COMPLETE_ROLE_CODES:
        # The role map already admitted this caller to the encounter surface;
        # completion is a narrower DOC-only decision taken pre-fence.
        gateway.deny()
    try:
        payload = encounter_write.validate_complete(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    alias = payload["encounter"]
    try:
        native_encounter_id = _resolve_target(gateway, settings, actor, alias)
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if native_encounter_id is None:
        return _locked_response(gateway)

    detail = gateway.native_encounter_action_detail(native_encounter_id)
    if detail is None:
        gateway.deny()
    if encounter_contract.complete_target_problem(detail) is not None:
        return _locked_response(gateway)
    if gateway.native_encounter_can_write(native_encounter_id) is not True:
        gateway.deny()
    if gateway.native_patient_can_read(detail.get("patient")) is not True:
        gateway.deny()
    if gateway.healthcare_encounter_side_effects_unsafe() is not False:
        gateway.deny()

    # Required-before-complete: per-field problem text, read from the persisted
    # encounter/note rows, plus the explicit allergy-review acknowledgement the
    # designed completion payload carries (no native field exists for it).
    problems = list(
        encounter_contract.completion_problems(
            {**detail, "allergy_reviewed": payload["allergy_reviewed"]}
        )
    )
    if problems:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    expected = encounter_contract.expected_service_requests(detail)
    try:
        baseline = gateway.native_submitted_service_request_count()
    except patient_write.WriteUncertain:
        return _locked_response(gateway)
    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=encounter_contract.ENCOUNTER_COMPLETE_KIND,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(
            gateway, encounter_contract.ENCOUNTER_COMPLETE_KIND
        )
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (patient_write.StoreUnavailable, patient_write.WriteUncertain):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    kind = encounter_contract.ENCOUNTER_COMPLETE_KIND
    try:
        with gateway.encounter_write_fence(
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
                if fence_role not in ENCOUNTER_COMPLETE_ROLE_CODES:
                    gateway.deny()
                state = gateway.lock_encounter_target(
                    native_encounter_id, None
                )
                values = _values(state)
                if values["patient"] != detail.get("patient"):
                    raise patient_write.UnsafeSideEffect("target patient changed")
                if encounter_contract.complete_target_problem(values) is not None:
                    raise encounter_write.EncounterTargetNotEligible()
                problems = encounter_contract.completion_problems(
                    {**values, "allergy_reviewed": payload["allergy_reviewed"]}
                )
                if problems:
                    raise encounter_write.EncounterTargetNotEligible()
                expected = encounter_contract.expected_service_requests(values)
                gateway.submit_native_encounter(state)
                cascade = gateway.native_encounter_cascade_detail(
                    native_encounter_id, baseline
                )
                if (
                    encounter_contract.cascade_problem(cascade, expected)
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                receipt = _finish_fence(
                    gateway, claim, kind, alias, native_encounter_id
                )
            except encounter_write.EncounterTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except encounter_write.EncounterBudgetExhausted:
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
                    "native Patient Encounter complete outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except encounter_write.EncounterTargetNotEligible:
        return _locked_response(gateway)
    except encounter_write.EncounterBudgetExhausted:
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
        ack_token = encounter_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway, settings, COMPLETE_RESULT, alias, native_encounter_id, ack_token
        )
    except (SuccessUncertain, encounter_write.EncounterWriteError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit encounter completion response construction failed",
        )
        return _locked_response(gateway)


def amend(gateway, body, csrf_token=None):
    """Add a Clinical Note addendum to a finalized encounter (DOC)."""

    settings, view, role = _shared_gate(gateway, csrf_token)
    complete_roles = ENCOUNTER_AMEND_ROLE_CODES
    if role not in complete_roles:
        gateway.deny()
    try:
        payload = encounter_write.validate_amend(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    actor = view.user
    alias = payload["encounter"]
    try:
        native_encounter_id = _resolve_target(gateway, settings, actor, alias)
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        return _locked_response(gateway)
    if native_encounter_id is None:
        return _locked_response(gateway)

    detail = gateway.native_encounter_action_detail(native_encounter_id)
    if detail is None:
        gateway.deny()
    if encounter_contract.amend_target_problem(detail) is not None:
        return _locked_response(gateway)
    if gateway.native_encounter_can_write(native_encounter_id) is not True:
        gateway.deny()
    if gateway.native_patient_can_read(detail.get("patient")) is not True:
        gateway.deny()
    if gateway.native_clinical_note_can_create() is not True:
        gateway.deny()

    kind = encounter_contract.ENCOUNTER_AMEND_KIND
    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=payload["operation_key"],
            fields=_digest_fields(payload),
        )
        budget_ok = _budget_remaining(gateway, kind)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)
    except (patient_write.StoreUnavailable, patient_write.WriteUncertain):
        return _locked_response(gateway)
    if not budget_ok:
        return _locked_response(gateway)

    expected_note = encounter_contract.addendum_note(actor, payload["addendum"])
    original_note_modified = detail.get("clinical_note_modified")
    encounter_modified = detail.get("modified")

    try:
        with gateway.encounter_write_fence(
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
                if fence_role not in complete_roles:
                    gateway.deny()
                state = gateway.lock_encounter_target(native_encounter_id, None)
                values = _values(state)
                if encounter_contract.amend_target_problem(values) is not None:
                    raise encounter_write.EncounterTargetNotEligible()
                addendum_id = gateway.create_native_clinical_note_addendum(
                    state, expected_note, actor
                )
                facts = gateway.native_encounter_amend_facts(
                    native_encounter_id,
                    addendum_id,
                    expected_note,
                    detail.get("clinical_note"),
                    original_note_modified,
                )
                if (
                    encounter_contract.amend_postcondition_problem(
                        encounter_modified=facts.get("encounter_modified"),
                        expected_encounter_modified=encounter_modified,
                        clinical_note_modified=facts.get("clinical_note_modified"),
                        original_note_modified=original_note_modified,
                        addendum_created=facts.get("addendum_created"),
                        expected_note=facts.get("addendum_note"),
                    )
                    is not None
                ):
                    raise patient_write.WriteUncertain()
                receipt = _finish_fence(
                    gateway, claim, kind, alias, native_encounter_id
                )
            except encounter_write.EncounterTargetNotEligible:
                _rollback_or_raise(gateway)
                raise
            except encounter_write.EncounterBudgetExhausted:
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
                    "native Clinical Note addendum outcome not established",
                )
                _rollback_or_raise(gateway)
                raise patient_write.WriteUncertain() from None
    except encounter_write.EncounterTargetNotEligible:
        return _locked_response(gateway)
    except encounter_write.EncounterBudgetExhausted:
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
        ack_token = encounter_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            payload["operation_key"],
            receipt,
        )
        return _success(
            gateway,
            settings,
            AMEND_RESULT,
            alias,
            native_encounter_id,
            ack_token,
            addendum=f"addendum_{payload['operation_key']}",
        )
    except (SuccessUncertain, encounter_write.EncounterWriteError):
        _latch_or_raise(
            gateway,
            actor,
            kind,
            payload["operation_key"],
            "post-commit encounter amendment response construction failed",
        )
        return _locked_response(gateway)


def acknowledge(gateway, body, csrf_token=None):
    """One-shot acknowledgement of a committed encounter operation."""

    settings, view, _role = common.authorize_encounter_write(
        gateway, ENCOUNTER_ROLE_CODES, csrf_token
    )
    try:
        payload = encounter_write.validate_ack(body)
        status = gateway.acknowledge_encounter_write(
            settings.epoch_secret,
            view.user,
            payload["kind"],
            payload["operation_key"],
            payload["ack_token"],
        )
    except encounter_write.EncounterWriteError:
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
