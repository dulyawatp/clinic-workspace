"""Unwired visit-photo state contract; inputs require trusted native provenance.

This module is not an authorization boundary or an observation mechanism.
"""

from dataclasses import dataclass, replace
from hashlib import sha256
import json


def _required(*values):
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("Missing visit identity or evidence")


@dataclass(frozen=True)
class Consent:
    appointment: str
    patient: str
    evidence: str
    granted_by: str
    granted_at: str
    revoked_by: str | None = None
    revoked_at: str | None = None


@dataclass(frozen=True)
class Observation:
    appointment: str
    patient: str


@dataclass(frozen=True)
class Encounter:
    name: str
    appointment: str
    patient: str
    docstatus: int


def grant(appointment, patient, evidence, actor, timestamp):
    _required(appointment, patient, evidence, actor, timestamp)
    return Consent(appointment, patient, evidence, actor, timestamp)


def revoke(consent, actor, timestamp):
    _required(actor, timestamp)
    if consent.revoked_at is not None:
        raise ValueError("Consent already revoked")
    return replace(consent, revoked_by=actor, revoked_at=timestamp)


def observe_check_in(appointment, patient, native_status):
    _required(appointment, patient)
    if native_status != "Checked In":
        raise ValueError("No native check-in observation")
    return Observation(appointment, patient)


def slot_identity(appointment, slot):
    _required(appointment)
    if slot not in ("Before", "After"):
        raise ValueError("Invalid slot")
    payload = json.dumps([appointment, slot], ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def require_capture(consent, role, slot, appointment, patient, observation=None, encounters=()):
    _required(appointment, patient)
    slot_identity(appointment, slot)
    if consent.revoked_at is not None or (consent.appointment, consent.patient) != (appointment, patient):
        raise ValueError("No active consent for visit")
    if role not in (("REC", "DOC") if slot == "Before" else ("DOC",)):
        raise ValueError("Role cannot capture slot")
    if slot == "After":
        if observation != Observation(appointment, patient) or len(encounters) != 1:
            raise ValueError("Missing check-in or unique encounter")
        encounter = encounters[0]
        if not encounter.name or (encounter.appointment, encounter.patient, encounter.docstatus) != (appointment, patient, 0):
            raise ValueError("Encounter is not the linked draft")
