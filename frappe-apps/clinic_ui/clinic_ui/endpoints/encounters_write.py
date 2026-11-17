"""Designed Patient Encounter draft, complete and amendment writes (default off).

**Deferred.** ``save_draft``, ``complete`` and ``amend`` refuse every new
mutation immediately after the shared gate: the encounter draft, completion,
amendment and lab-ordering surfaces are deferred pending the appointment
lifecycle provenance and authorization review (design:
``2026-11-18-encounter-deferral``). The refusal takes no fence lease and makes no
native mutation. The one request that still answers is an exact replay of the
caller's own committed operation: same actor, kind, operation key, payload
digest and alias, verified against the durable receipt, answered from that
receipt plus a native read so a lost response is recoverable without a new
ledger event; this actor's own ``DISPATCHED``/``UNKNOWN`` key keeps the eager
fence's ``409 WRITE_LOCKED`` answer, and every other unmatched key is refused.
``acknowledge`` is untouched, so work committed before the deferral stays
recoverable. The order below describes the retained implementation
(``_*_undeferred``), which the deferred-contract tests still assert so a
reviewed re-enable is evidence-backed rather than a rewrite.

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
7. the volatile decision, taken under a held fence lease: the site snapshot is
   restarted, the target rows are locked ``FOR UPDATE`` for the rest of this
   transaction, and the decisions above are taken again against the locked rows
   (the encounter, then its linked booking when both exist, and the persisted
   medication rows). The authorization, CSRF, deployment, configuration and role
   gates are re-read here too. The durable budget is re-read here too,
8. ``Lease.dispatch``: the single call that appends ``DISPATCHED``, taken
   immediately before the designed native operation (draft save, submit with the
   expected cascade, or a new Clinical Note addendum). The lease and the row
   locks stay held across the write, one explicit commit and the terminal
   receipt,
9. the one-shot acknowledgement token.

``Ledger.lease`` appends nothing until ``dispatch``, so a refusal taken under the
held lease (an authorization, CSRF, deployment or settings change, a booking or
medication race, an exhausted budget or a lease/dispatch failure) writes no
event, drops the row locks it took, and lets the next operation run. Once
``dispatch`` has appended the claim there is no refusal left: an exception there
means a native write may be in flight, which is exactly when the key must stay
``DISPATCHED`` (the operation becomes ``UNKNOWN``/locked, the response is
``409 {"code": "WRITE_LOCKED"}`` with no retry).

Authority changes made outside this process are not serialisable: the gate is
re-read under the held lease, which narrows the window to the gap between that
re-read and the native call, it does not close it. Two parts of that re-read are
platform-dependent and are stated here rather than implied:

* the site configuration is genuinely reloaded (``refresh_site_config``), but the
  role set comes from ``frappe.get_roles``, which the pinned Frappe caches per
  request. The adapter deliberately does not purge or reimplement that cache: no
  reviewed fresh path exists for it, and a guessed invalidation would be an
  authorization change, not a fix. The cached role is therefore only the role
  the request may use, and a deny-only guard re-reads the ``Has Role`` rows
  directly under the lease and refuses unless they resolve to that same role.
  The guard cannot grant: a fresh DB role the cached gate did not authorise is
  a refusal, and a role map satisfiable only by an automatic framework role
  (``All``, ``Desk User``, ``Guest``, ``Administrator``) is refused because the
  table cannot establish it. It reads no raw SQL and touches no session id.
* an epoch-secret rotation under the lease is refused before the dispatch
  (``configured epoch secret changed``). The alias, digest and acknowledgement
  token are all derived from that secret, so a rotation cannot be reconciled
  safely: the response would carry a token the production acknowledgement path
  rejects, leaving the barrier ``DISPATCHED`` and the site locked.

The fresh-role read narrows the authority window; it does not make revocation
atomic. A ``Has Role`` change committed after that read and before the native
call still lands, and the native DocPerm check (with its own framework cache) is
a separate authority this guard neither replaces nor re-implements.
"""

import hmac

from clinic_ui.core import (
    dto,
    encounter_contract,
    encounter_write,
    patient_write,
    policy,
    write_fence,
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

# The result status each deferred kind reports on a committed replay. The status
# is a property of the operation kind, not of the replay, so a replayed operation
# answers with the result it originally returned.
REPLAY_RESULTS = {
    encounter_contract.ENCOUNTER_DRAFT_KIND: DRAFT_RESULT,
    encounter_contract.ENCOUNTER_COMPLETE_KIND: COMPLETE_RESULT,
    encounter_contract.ENCOUNTER_AMEND_KIND: AMEND_RESULT,
}


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


def _require_fresh_role(gateway, settings, view, role):
    """Refuse unless the stored role rows still grant the gated role.

    ``_shared_gate`` re-reads ``frappe.get_roles``, which the pinned Frappe
    serves from its request/Redis cache, so a role revoked since the session was
    read can still pass. This reads the ``Has Role`` rows directly and refuses
    unless they resolve to exactly the role the gate already authorised. It is
    deny-only: it never re-resolves a different role and never grants one. An
    unreadable table read and a role map that can only be satisfied by an
    automatic framework role both refuse.
    """

    try:
        fresh_roles = gateway.fresh_user_roles(view.user)
    except Exception:
        fresh_roles = None
    problem = policy.fresh_role_problem(
        fresh_roles, settings.as_role_map(), role
    )
    if problem is not None:
        gateway.deny()


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


def _release_read_locks(gateway):
    """Release the pre-dispatch row locks after a refusal.

    Nothing has been written at this point, so a rollback only drops the locks
    the volatile decision took. A failure here is not a write uncertainty: the
    transaction stays read-only and ends without a commit.
    """

    try:
        gateway.rollback()
    except Exception:
        return


def _dispatch(lease, gateway):
    """Take the claim immediately before the native write.

    The lease holds the fence and the transaction holds the row locks at this
    point, so a refused dispatch must drop the locks before it leaves.
    """

    try:
        return lease.dispatch()
    except BaseException:
        _release_read_locks(gateway)
        raise


def _draft_volatile_decision(
    gateway,
    payload,
    native_patient_id,
    *,
    view,
    is_create,
    native_appointment_id,
    kind,
    csrf_token,
    expected_secret,
    cross_actor,
):
    """Decide the draft save under row locks, before the dispatch.

    ``Lease.dispatch`` is what appends ``DISPATCHED``, so every decision a
    concurrent native writer (or an operator changing authority) can invalidate
    is taken here: the site configuration is reloaded and the full gate is
    re-read against a restarted snapshot, the target rows are locked with
    ``FOR UPDATE`` for the rest of this transaction, and the native authority the
    write needs is re-checked on the locked rows. The dispatch is only taken once
    nothing can refuse. Nothing may roll back after the locks are taken unless
    the dispatch itself refuses.

    ``expected_secret`` is the epoch secret this request's alias, digest and ack
    token are built from. A rotation under the lease cannot be reconciled: the
    response token would be unverifiable by the production acknowledgement path
    and the barrier would stay ``DISPATCHED``, so the difference is refused
    before the dispatch instead.

    ``cross_actor`` records that the target was resolved under the nurse-only
    cross-actor rule. If the in-lease role is no longer ``NUR`` that privilege no
    longer applies, so the operation refuses rather than writing another actor's
    draft as a doctor.

    Returns the locked encounter state to save (``None`` for a create).
    """

    # The configuration gate must see the file as it is now, and the reads below
    # must see committed rows: resetting first is what makes the second gate and
    # the locks honest instead of replaying this request's earlier snapshot.
    gateway.refresh_site_config()
    gateway.reset_native_read_snapshot()
    fence_settings, fence_view, fence_role = _shared_gate(gateway, csrf_token)
    if fence_view.user != view.user:
        raise patient_write.WriteUncertain()
    _require_fresh_role(gateway, fence_settings, fence_view, fence_role)
    if fence_settings.epoch_secret != expected_secret:
        raise patient_write.UnsafeSideEffect("configured epoch secret changed")
    if cross_actor and fence_role != "NUR":
        raise patient_write.UnsafeSideEffect("nurse cross-actor privilege changed")
    if (
        encounter_contract.section_problem(fence_role, payload["fields"])
        is not None
    ):
        raise patient_write.UnsafeSideEffect("section role changed")
    if encounter_contract.build_note_text(
        payload["fields"].get("note")
    ) and gateway.native_clinical_note_can_create() is not True:
        raise patient_write.UnsafeSideEffect(
            "clinical note create authority changed"
        )
    if (
        native_patient_id_for(fence_settings, payload["workspace_patient_id"])
        != native_patient_id
    ):
        raise patient_write.UnsafeSideEffect("configured patient changed")
    if gateway.healthcare_encounter_side_effects_unsafe() is not False:
        gateway.deny()

    try:
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
                raise patient_write.UnsafeSideEffect("linked booking changed")
            # The row lock, not a plain read: the fence serialises this
            # contract's own operations only, and another native writer can edit
            # the booking at any moment.
            locked = gateway.lock_linked_appointment(native_appointment_id)
            if locked is None:
                raise patient_write.UnsafeSideEffect(
                    "linked booking is not readable under the lock"
                )
            if locked.get("patient") != native_patient_id:
                raise patient_write.UnsafeSideEffect("linked booking changed")
            if (
                locked.get("status")
                not in encounter_contract.CASCADE_SOURCE_STATUSES
            ):
                raise encounter_write.EncounterTargetNotEligible()
            # The type is captured pre-fence and revalidated on the locked row.
            # An unset capture is refused too, so ``None == None`` is not a way
            # past this check.
            captured_type = payload.get("_appointment_type")
            if not captured_type or (
                locked.get("appointment_type") != captured_type
            ):
                raise patient_write.UnsafeSideEffect(
                    "linked booking appointment type changed"
                )
            # Native authority is re-read under the lease: a create right revoked
            # after the first gate must refuse before the dispatch, not inside the
            # write.
            if gateway.native_appointment_can_write(native_appointment_id) is not True:
                gateway.deny()
            if gateway.native_encounter_can_create() is not True:
                gateway.deny()
            if gateway.native_patient_can_read(native_patient_id) is not True:
                gateway.deny()
            _require_budget(gateway, kind)
            return None

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
        if encounter_contract.persisted_medication_problem(values) is not None:
            raise encounter_write.EncounterTargetNotEligible()
        if gateway.native_encounter_can_write(payload["_native_encounter_id"]) is not True:
            gateway.deny()
        if gateway.native_patient_can_read(native_patient_id) is not True:
            gateway.deny()
        _require_budget(gateway, kind)
    except BaseException:
        _release_read_locks(gateway)
        raise
    return state


def _draft_native_write(
    gateway,
    payload,
    *,
    alias,
    is_create,
    kind,
    locked_state,
    claim,
):
    """Run the already-dispatched draft write under the held lease.

    Every refusal has happened before the dispatch, so an exception here means a
    native write may be in flight — exactly when the key must stay DISPATCHED.
    """

    try:
        if is_create:
            native_id = gateway.create_native_encounter_draft(payload)
        else:
            gateway.save_native_encounter_draft(locked_state, payload)
            native_id = payload["_native_encounter_id"]
        receipt = _finish_fence(gateway, claim, kind, alias, native_id)
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
    except BaseException:
        # An interrupt is not an established outcome: attempt the native rollback
        # while the lease is still held, then let it propagate. The durable key
        # stays DISPATCHED — no auto-unlock, no retry, no terminal rewrite.
        _release_read_locks(gateway)
        raise
    return receipt


def _refuse_encounter_write(gateway):
    """Refuse an encounter mutation that is not an exact committed replay.

    Encounter draft, completion, amendment and the native lab ordering that
    rides the draft payload are deferred pending the appointment lifecycle
    provenance and authorization review: a pinned native draft save closes its
    appointment, while the completion contract requires that appointment to stay
    open or checked in, and note authorization plus the other cascades are
    unreviewed. Every caller of this helper has already passed the read gate, the
    explicit same-session CSRF check, the encounter deployment gate, and the
    pinned boundary and installed-field preflight.

    A refusal here takes no fence lease, appends no ``DISPATCHED`` claim, appends
    no event, makes no native mutation, latches nothing, unlocks nothing and
    retries nothing, so there is no new receipt, no new acknowledgement token and
    nothing to reconcile. A ``DISPATCHED``/``UNKNOWN`` key is *not* refused
    through this helper - its outcome is uncertain rather than unavailable, so
    the caller answers ``409 WRITE_LOCKED`` instead.

    The retained implementation below (``_*_undeferred``) is unreachable from the
    API until a reviewed re-enable removes the deferral.
    """

    gateway.deny()


def _committed_operation(gateway, actor, kind, operation_key, digest, alias):
    """Return the receipt of this actor's own committed operation, or ``None``.

    Strictly read-only: one single-key ledger lookup and no more. The key must
    resolve to a terminal ``COMMITTED`` record owned by this actor and bound to
    the same payload digest and alias, and the stored receipt must decode to the
    same kind and alias.

    ``None`` has exactly one meaning: this actor's own key exists with an
    established-but-not-committed state (``DISPATCHED``/``UNKNOWN``). The caller
    answers ``409 WRITE_LOCKED`` for it, which is the answer the eager fence
    already gave for a repeated key, and takes no latch, unlock, retry or event.

    Every other unmatched key is refused by :func:`_refuse_encounter_write`:

    * no record: there is nothing to replay, and a new mutation is deferred;
    * a different digest or alias on the same key: the key belongs to another
      payload, so replaying the stored receipt would report a write that never
      happened;
    * another actor's key: the lookup is actor-bound, so that is no record;
    * an undecodable receipt, an unknown state or an unreadable store: a
      refusal, never a guess.

    ``DISPATCHED`` and ``UNKNOWN`` are not routed through this refusal: they
    return ``None`` and the caller answers ``409 WRITE_LOCKED``, because the key
    is the caller's own and only its outcome is unknown.

    The record is read, not reconciled: an unresolved barrier or a site latch is
    neither consulted nor cleared here, and a committed operation is still
    answerable while one exists.
    """

    try:
        record = gateway.lookup_encounter_operation(
            actor, kind, operation_key
        )
        if record is None:
            _refuse_encounter_write(gateway)
        if record.state in (write_fence.DISPATCHED, write_fence.UNKNOWN):
            return None
        if record.state != write_fence.COMMITTED:
            _refuse_encounter_write(gateway)
        stored = record.payload_digest
        if not isinstance(stored, str) or not isinstance(digest, str):
            _refuse_encounter_write(gateway)
        if not hmac.compare_digest(stored, digest):
            _refuse_encounter_write(gateway)
        decoded = encounter_write.decode_receipt(record.receipt)
    except (
        patient_write.WriteLocked,
        patient_write.StoreUnavailable,
        patient_write.WriteUncertain,
    ):
        _refuse_encounter_write(gateway)
    except encounter_write.EncounterWriteError:
        _refuse_encounter_write(gateway)
    if decoded.kind != kind or decoded.alias != alias:
        _refuse_encounter_write(gateway)
    return record.receipt


def _replay_gate(gateway, settings, view, role, csrf_token):
    """Re-take the decision gates for a replay; deny-only, read-only.

    The retained write path re-runs this decision after the snapshot restart and
    under the held lease. A replay writes nothing, so no lease is taken and none
    is needed, but the same authority is re-established before any answer is
    returned: the site configuration is refreshed, the native read snapshot is
    restarted, the shared gate runs again (read gate, explicit same-session CSRF,
    encounter deployment gate, pinned boundary and installed-field preflight),
    the session user and the gated role must be unchanged, the fresh ``Has Role``
    rows must still resolve to that role, and the epoch secret must be unchanged.

    The snapshot restart runs first so the fresh-role and native reads below see
    committed rows. It is safe here because this path has written nothing: the
    rollback can only discard this request's own reads, exactly as the in-lease
    restart does.

    No framework cache is cleared or reimplemented. ``refresh_site_config`` drops
    this gateway's own configuration object, and the fresh-role read goes
    straight to the ``Has Role`` rows, so a replay is never answered on a cached
    role the stored rows no longer grant. A refusal latches nothing and appends
    nothing.

    Returns the re-read settings so the response is built from them: the patient
    allowlist and every other configured value that shapes the body are the ones
    the second gate just authorised, not the request's first read.
    """

    gateway.refresh_site_config()
    gateway.reset_native_read_snapshot()
    fence_settings, fence_view, fence_role = _shared_gate(gateway, csrf_token)
    if fence_view.user != view.user:
        gateway.deny()
    if fence_role != role:
        _refuse_encounter_write(gateway)
    _require_fresh_role(gateway, fence_settings, fence_view, fence_role)
    if fence_settings.epoch_secret != settings.epoch_secret:
        _refuse_encounter_write(gateway)
    return fence_settings


def _replay_result(gateway, settings, actor, kind, operation_key, receipt):
    """Rebuild the body a committed operation already returned.

    ``settings`` is the value :func:`_replay_gate` re-read, so the patient
    allowlist and the epoch secret that shape this body are the ones the second
    gate just authorised, not this request's first read.

    The acknowledgement token is recomputed from the stored receipt with the
    server secret, which is exactly what ``acknowledge`` verifies, so a lost
    response is recoverable without a new ledger event. The encounter detail is a
    fresh native read, never a fabricated one; a failed read or an unexpected
    decode refuses. Nothing is dispatched, latched, unlocked or retried, so a
    refusal here must not latch the site either.
    """

    try:
        decoded = encounter_write.decode_receipt(receipt)
        ack_token = encounter_write.ack_token(
            settings.epoch_secret,
            gateway.site,
            actor,
            kind,
            operation_key,
            receipt,
        )
        addendum = None
        if kind == encounter_contract.ENCOUNTER_AMEND_KIND:
            addendum = f"addendum_{operation_key}"
        return _success(
            gateway,
            settings,
            REPLAY_RESULTS[kind],
            decoded.alias,
            decoded.native_encounter_id,
            ack_token,
            addendum=addendum,
        )
    except (SuccessUncertain, encounter_write.EncounterWriteError):
        _refuse_encounter_write(gateway)


def save_draft(gateway, body, csrf_token=None):
    """Refuse a new draft and replay the caller's own committed draft.

    A new create or update is deferred. The one request that still answers is an
    exact replay of this actor's own committed draft operation: the stored
    receipt, its kind, alias and payload digest must all match. No lease is
    taken and no native mutation is made.
    """

    settings, view, role = _shared_gate(gateway, csrf_token)
    try:
        payload = encounter_write.validate_save_draft(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    kind = encounter_contract.ENCOUNTER_DRAFT_KIND
    operation_key = payload["operation_key"]
    alias = payload["encounter"]
    if alias is None:
        alias = _alias_for(settings, gateway, view.user, operation_key)
    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=operation_key,
            fields=_digest_fields(payload),
        )
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    receipt = _committed_operation(
        gateway, view.user, kind, operation_key, digest, alias
    )
    replay_settings = _replay_gate(
        gateway, settings, view, role, csrf_token
    )
    if receipt is None:
        return _locked_response(gateway)
    return _replay_result(
        gateway, replay_settings, view.user, kind, operation_key, receipt
    )


def _save_draft_undeferred(gateway, body, csrf_token=None):
    """Create or update a draft encounter (roles DOC; NUR vitals only).

    Retained for the deferred contract review; not reachable from the API.
    """

    settings, view, role = _shared_gate(gateway, csrf_token)
    try:
        payload = encounter_write.validate_save_draft(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    problem = encounter_contract.section_problem(role, payload["fields"])
    if problem is not None:
        gateway.deny()

    # Medication rows stay outside the reviewed contract: the installed
    # ``Drug Prescription.dosage_form`` is required and the payload cannot supply
    # it. Refusing here keeps a dispatch that would fail inside the fence from
    # latching the site. Emptying the section is not a way in either: the
    # normaliser has already produced a sequence, and anything non-empty refuses.
    if (
        encounter_contract.medication_problem(payload["fields"])
        is not None
    ):
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    # A requested note is a separate Clinical Note insert after the encounter
    # write. Without the actor's own Clinical Note create authority that insert
    # would fail inside the fence and latch the site, so the refusal is here.
    if encounter_contract.build_note_text(payload["fields"].get("note")):
        if gateway.native_clinical_note_can_create() is not True:
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
    appointment_type = None

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
        # ``Patient Encounter.appointment_type`` is installed required and is
        # derived from the linked booking, never from the client.
        appointment_type = gateway.native_appointment_encounter_type(
            native_appointment_id
        )
        if (
            encounter_contract.create_target_problem(
                {
                    "appointment": native_appointment_id,
                    "appointment_type": appointment_type,
                }
            )
            is not None
        ):
            return _locked_response(gateway)
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
        if (
            encounter_contract.persisted_medication_problem(detail)
            is not None
        ):
            # Rows a native user added are neither rewritable nor deletable by
            # this contract, so the target is refused instead of dropping them.
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
    payload["_appointment_type"] = appointment_type
    if not is_create:
        payload["_native_encounter_id"] = native_encounter_id

    kind = encounter_contract.ENCOUNTER_DRAFT_KIND
    try:
        with gateway.encounter_write_lease(
            actor, kind, payload["operation_key"], digest
        ) as lease:
            locked_state = _draft_volatile_decision(
                gateway,
                payload,
                native_patient_id,
                view=view,
                is_create=is_create,
                native_appointment_id=native_appointment_id,
                kind=kind,
                csrf_token=csrf_token,
                expected_secret=settings.epoch_secret,
                cross_actor=role == "NUR" and not is_create,
            )
            claim = _dispatch(lease, gateway)
            receipt = _draft_native_write(
                gateway,
                payload,
                alias=alias,
                is_create=is_create,
                kind=kind,
                locked_state=locked_state,
                claim=claim,
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


def _completion_volatile_decision(
    gateway,
    payload,
    native_encounter_id,
    *,
    view,
    kind,
    csrf_token,
    expected_patient,
    expected_secret,
):
    """Decide the completion under row locks, before the dispatch.

    Same reason as :func:`_draft_volatile_decision`: ``Lease.dispatch`` is what
    records ``DISPATCHED``, so a booking or medication race detected after it
    would block the whole site without a single native write. The site
    configuration is reloaded and the full gate re-read against a restarted
    snapshot, the encounter row and then its linked booking are locked ``FOR
    UPDATE`` and revalidated here, and the write and read authority the submit
    needs is re-checked on the locked row. The status that decides the cascade
    comes from the locked booking, never from the pre-lease read.
    """

    gateway.refresh_site_config()
    gateway.reset_native_read_snapshot()
    fence_settings, fence_view, fence_role = _shared_gate(gateway, csrf_token)
    if fence_view.user != view.user:
        raise patient_write.WriteUncertain()
    _require_fresh_role(gateway, fence_settings, fence_view, fence_role)
    if fence_settings.epoch_secret != expected_secret:
        raise patient_write.UnsafeSideEffect("configured epoch secret changed")
    if fence_role not in ENCOUNTER_COMPLETE_ROLE_CODES:
        gateway.deny()
    if gateway.healthcare_encounter_side_effects_unsafe() is not False:
        gateway.deny()

    try:
        state = gateway.lock_encounter_target(native_encounter_id, None)
        values = _values(state)
        # The identity the request resolved is not authority: a concurrent
        # rebind of the encounter to another patient changes what would be
        # submitted, so it is refused rather than completed.
        if values["patient"] != expected_patient:
            raise patient_write.UnsafeSideEffect("target patient changed")
        if encounter_contract.persisted_medication_problem(values) is not None:
            raise encounter_write.EncounterTargetNotEligible()
        if encounter_contract.complete_target_problem(values) is not None:
            raise encounter_write.EncounterTargetNotEligible()
        booking = gateway.lock_linked_appointment_for_read(values.get("appointment"))
        if booking is None:
            raise patient_write.UnsafeSideEffect(
                "linked booking is not readable under the lock"
            )
        if booking.get("patient") != values["patient"]:
            raise patient_write.UnsafeSideEffect("linked booking patient changed")
        locked_values = {
            **values,
            "appointment_status": booking.get("status"),
        }
        if encounter_contract.complete_target_problem(locked_values) is not None:
            raise encounter_write.EncounterTargetNotEligible()
        if (
            encounter_contract.completion_problems(
                {**values, "allergy_reviewed": payload["allergy_reviewed"]}
            )
        ):
            raise encounter_write.EncounterTargetNotEligible()
        # The cascade expectation is a decision, not a postcondition: an
        # unreadable row count must refuse here instead of becoming an
        # uncertainty after the submit.
        if encounter_contract.expected_service_requests(values) is None:
            raise encounter_write.EncounterTargetNotEligible()
        if gateway.native_encounter_can_write(native_encounter_id) is not True:
            gateway.deny()
        if gateway.native_patient_can_read(values["patient"]) is not True:
            gateway.deny()
        _require_budget(gateway, kind)
    except BaseException:
        _release_read_locks(gateway)
        raise
    return state


def _complete_native_write(
    gateway,
    native_encounter_id,
    *,
    alias,
    kind,
    locked_state,
    baseline,
    claim,
):
    """Run the already-dispatched submit and its closed-world cascade check."""

    try:
        values = _values(locked_state)
        expected = encounter_contract.expected_service_requests(values)
        gateway.submit_native_encounter(locked_state)
        cascade = gateway.native_encounter_cascade_detail(
            native_encounter_id, baseline
        )
        if encounter_contract.cascade_problem(cascade, expected) is not None:
            raise patient_write.WriteUncertain()
        receipt = _finish_fence(gateway, claim, kind, alias, native_encounter_id)
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
    except BaseException:
        # An interrupt is not an established outcome: attempt the native rollback
        # while the lease is still held, then let it propagate. The durable key
        # stays DISPATCHED — no auto-unlock, no retry, no terminal rewrite.
        _release_read_locks(gateway)
        raise
    return receipt


def complete(gateway, body, csrf_token=None):
    """Refuse a new completion and replay the caller's own committed one.

    The DOC-only narrowing is kept ahead of the replay decision. A new completion
    is deferred; only an exact replay of this actor's own committed completion
    answers, from the stored receipt, with no lease and no native mutation.
    """

    settings, view, role = _shared_gate(gateway, csrf_token)
    if role not in ENCOUNTER_COMPLETE_ROLE_CODES:
        gateway.deny()
    try:
        payload = encounter_write.validate_complete(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    kind = encounter_contract.ENCOUNTER_COMPLETE_KIND
    operation_key = payload["operation_key"]
    alias = payload["encounter"]
    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=operation_key,
            fields=_digest_fields(payload),
        )
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    receipt = _committed_operation(
        gateway, view.user, kind, operation_key, digest, alias
    )
    replay_settings = _replay_gate(
        gateway, settings, view, role, csrf_token
    )
    if receipt is None:
        return _locked_response(gateway)
    return _replay_result(
        gateway, replay_settings, view.user, kind, operation_key, receipt
    )


def _complete_undeferred(gateway, body, csrf_token=None):
    """Submit a draft encounter and verify the expected native cascade (DOC).

    Retained for the deferred contract review; not reachable from the API.
    """

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
    if encounter_contract.persisted_medication_problem(detail) is not None:
        # A submitted encounter would carry medication rows this contract can
        # neither complete nor remove. Refuse the target, never delete the rows.
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
        with gateway.encounter_write_lease(
            actor, kind, payload["operation_key"], digest
        ) as lease:
            locked_state = _completion_volatile_decision(
                gateway,
                payload,
                native_encounter_id,
                view=view,
                kind=kind,
                csrf_token=csrf_token,
                expected_patient=detail.get("patient"),
                expected_secret=settings.epoch_secret,
            )
            claim = _dispatch(lease, gateway)
            receipt = _complete_native_write(
                gateway,
                native_encounter_id,
                alias=alias,
                kind=kind,
                locked_state=locked_state,
                baseline=baseline,
                claim=claim,
            )
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


def _amend_volatile_decision(
    gateway,
    payload,
    native_encounter_id,
    *,
    view,
    kind,
    csrf_token,
    expected_patient,
    expected_secret,
):
    """Decide the amendment under the encounter row lock, before the dispatch.

    The addendum is a separate Clinical Note insert, so the target decision is
    the only fallible step. It is taken here for the same reason as the draft and
    completion paths: a race detected after ``dispatch`` would leave the key
    ``DISPATCHED`` and lock the site with no native write behind it. The site
    configuration is reloaded and the full gate re-read against a restarted
    snapshot, and the write, read and Clinical Note create authority the addendum
    needs is re-checked under the held lease.
    """

    gateway.refresh_site_config()
    gateway.reset_native_read_snapshot()
    fence_settings, fence_view, fence_role = _shared_gate(
        gateway, csrf_token
    )
    if fence_view.user != view.user:
        raise patient_write.WriteUncertain()
    _require_fresh_role(gateway, fence_settings, fence_view, fence_role)
    if fence_settings.epoch_secret != expected_secret:
        raise patient_write.UnsafeSideEffect("configured epoch secret changed")
    if fence_role not in ENCOUNTER_AMEND_ROLE_CODES:
        gateway.deny()
    if gateway.healthcare_encounter_side_effects_unsafe() is not False:
        gateway.deny()

    try:
        state = gateway.lock_encounter_target(native_encounter_id, None)
        values = _values(state)
        if values["patient"] != expected_patient:
            raise patient_write.UnsafeSideEffect("target patient changed")
        if encounter_contract.amend_target_problem(values) is not None:
            raise encounter_write.EncounterTargetNotEligible()
        if gateway.native_encounter_can_write(native_encounter_id) is not True:
            gateway.deny()
        if gateway.native_patient_can_read(values.get("patient")) is not True:
            gateway.deny()
        if gateway.native_clinical_note_can_create() is not True:
            gateway.deny()
        _require_budget(gateway, kind)
    except BaseException:
        _release_read_locks(gateway)
        raise
    return state


def _amend_native_write(
    gateway,
    native_encounter_id,
    *,
    actor,
    alias,
    kind,
    expected_note,
    locked_state,
    claim,
):
    """Insert the dispatched addendum and re-verify the never-rewrite preimages.

    The preimages come from the locked row, not from the pre-lease read: an
    ordinary concurrent edit before this operation took the lock must not be
    reported as an uncertainty after dispatch.
    """

    locked_values = _values(locked_state)
    encounter_modified = locked_values["modified"]
    original_note_modified = locked_values["clinical_note_modified"]
    try:
        addendum_id = gateway.create_native_clinical_note_addendum(
            locked_state, expected_note, actor
        )
        facts = gateway.native_encounter_amend_facts(
            native_encounter_id,
            addendum_id,
            expected_note,
            locked_values["clinical_note"],
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
        receipt = _finish_fence(gateway, claim, kind, alias, native_encounter_id)
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
    except BaseException:
        # An interrupt is not an established outcome: attempt the native rollback
        # while the lease is still held, then let it propagate. The durable key
        # stays DISPATCHED — no auto-unlock, no retry, no terminal rewrite.
        _release_read_locks(gateway)
        raise
    return receipt


def amend(gateway, body, csrf_token=None):
    """Refuse a new amendment and replay the caller's own committed one.

    The DOC-only narrowing is kept ahead of the replay decision. A new amendment
    is deferred; only an exact replay of this actor's own committed amendment
    answers, from the stored receipt, with no lease and no native mutation.
    """

    settings, view, role = _shared_gate(gateway, csrf_token)
    if role not in ENCOUNTER_AMEND_ROLE_CODES:
        gateway.deny()
    try:
        payload = encounter_write.validate_amend(body)
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    kind = encounter_contract.ENCOUNTER_AMEND_KIND
    operation_key = payload["operation_key"]
    alias = payload["encounter"]
    try:
        digest = encounter_write.operation_digest(
            settings.epoch_secret,
            kind=kind,
            operation_key=operation_key,
            fields=_digest_fields(payload),
        )
    except encounter_write.EncounterWriteError:
        return _respond_error(gateway, HTTP_UNPROCESSABLE, VALIDATION_CODE)

    receipt = _committed_operation(
        gateway, view.user, kind, operation_key, digest, alias
    )
    replay_settings = _replay_gate(
        gateway, settings, view, role, csrf_token
    )
    if receipt is None:
        return _locked_response(gateway)
    return _replay_result(
        gateway, replay_settings, view.user, kind, operation_key, receipt
    )


def _amend_undeferred(gateway, body, csrf_token=None):
    """Add a Clinical Note addendum to a finalized encounter (DOC).

    Retained for the deferred contract review; not reachable from the API.
    """

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

    try:
        with gateway.encounter_write_lease(
            actor, kind, payload["operation_key"], digest
        ) as lease:
            locked_state = _amend_volatile_decision(
                gateway,
                payload,
                native_encounter_id,
                view=view,
                kind=kind,
                csrf_token=csrf_token,
                expected_patient=detail.get("patient"),
                expected_secret=settings.epoch_secret,
            )
            claim = _dispatch(lease, gateway)
            receipt = _amend_native_write(
                gateway,
                native_encounter_id,
                actor=actor,
                alias=alias,
                kind=kind,
                expected_note=expected_note,
                locked_state=locked_state,
                claim=claim,
            )
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
