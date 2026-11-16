"""In-memory gateway stub for endpoint tests.

The stub records calls and never performs I/O. It is a local fixture, not a
Frappe simulation: no session store, permission engine, HTTP layer or site
database is claimed. Endpoint tests only prove gate ordering, DTO shape and
fail-closed decisions.
"""

import hmac

from clinic_ui.core import appointment_contract, dto, patient_write
from clinic_ui.core.policy import SessionView


class Denied(Exception):
    """Stand-in for the framework permission error raised by the gateway."""


class FakeCreateReceipt:
    """One committed create row as the gateway would enumerate it."""

    def __init__(self, actor, operation_key, receipt):
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


class FakeAppointmentReceipt:
    """One committed appointment row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


class FakeLockedState:
    """Stand-in for ``AppointmentLockedState`` in endpoint tests."""

    def __init__(self, **values):
        for key, value in values.items():
            setattr(self, key, value)


class FakeClaim:
    def __init__(self):
        self.committed = []
        self.unknowns = []

    def commit(self, receipt):
        self.committed.append(receipt)

    def unknown(self, reason):
        self.unknowns.append(reason)


class FakeFence:
    def __init__(self, claim, error=None):
        self.claim = claim
        self.error = error

    def __enter__(self):
        if self.error is not None:
            raise self.error
        return self.claim

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeGateway:
    def __init__(
        self,
        *,
        site="marley.test",
        settings=None,
        current_session=None,
        csrf_token="csrf-token-value",
    ):
        self.site = site
        self._settings = dict(settings or {})
        self.current_session = current_session
        self._csrf_token = csrf_token
        self._session_csrf_token = csrf_token
        self.no_store_calls = 0
        self.traceback_suppressions = 0
        self.denials = []
        self.anonymous_calls = 0
        self.csrf_calls = 0
        self.patient_reads = []
        self.patient_authority = {}
        self.patient_read_error = None
        self.patient_views = []
        self.patient_view_detail = {}
        self.patient_view_error = None
        self.appointment_views = []
        self.appointment_view_detail = {}
        self.appointment_view_error = None

        self.write_responses = []
        self.deployment_approved = True
        self.fence_ready = True
        self.side_effects_unsafe = False
        self.target_unsafe = set()
        self.strip_calls = 0
        self.csrf_supplied = []
        self.can_create = True
        self.can_write = True
        self.genders = {"Female"}
        self.patient_modified = {}
        self.created_native_id = "PAT-NEW"
        self.prepare_calls = []
        self.create_calls = []
        self.prepare_error = None
        self.create_error = None
        self.default_side_effect = None
        self.latch_calls = []
        self.operation_latch_calls = []
        self.latch_error = None
        self.operation_latch_error = None
        self.ack_calls = []
        self.ack_status = "acked"
        self.ack_error = None
        self.update_calls = []
        self.update_error = None
        self.commit_calls = 0
        self.rollback_calls = 0
        self.fence_calls = []
        self.fence_claim = FakeClaim()
        self.fence_error = None
        self.create_receipts = {}
        self.created_receipts = []
        self.receipt_list_calls = []
        self.receipt_list_error = None
        self.write_authority = None

        # -- appointment write surface ------------------------------------
        self.appointment_detail_map = {}
        self.appointment_detail_error = None
        self.patient_can_read = True
        self.patient_read_calls = []
        self.doctype_reads = {}
        self.doctype_read_calls = []
        self.appointment_can_create = True
        self.appointment_can_write = True
        self.appointment_write_authority = None
        self.appointment_side_effects_unsafe = False
        self.appointment_modified = {}
        self.appointment_type_duration = 15
        self.appointment_type_contract = {
            "appointment_type": "APPT-TYPE-1",
            "default_duration": 15,
            "allow_booking_for": "Department",
        }
        self.appointment_boundary_problem = None
        self.appointment_boundary_problem_sequence = []
        self.appointment_sources = {
            "patient_name": "Patient One",
            "patient_sex": "Female",
            "inpatient_record": None,
            "emergency_record": None,
            "duration": 15,
            "allow_booking_for": "Department",
        }
        self.appointment_sources_error = None
        self.appointment_source_calls = []
        self.appointment_insert_verify_calls = []
        self.appointment_insert_verify_error = None
        self.appointment_target_problems = {}
        self.appointment_target_problem_calls = []
        self.appointment_locked = {}
        self.appointment_lock_calls = []
        self.appointment_lock_error = None
        self.save_reschedule_calls = []
        self.save_reschedule_error = None
        self.checkin_state_calls = []
        self.prepare_appointment_calls = []
        self.prepare_appointment_error = None
        self.prepared_appointment = {"doctype": "Patient Appointment"}
        self.insert_appointment_calls = []
        self.insert_appointment_error = None
        self.created_appointment_id = "NATIVE-APPT-NEW"
        self.reschedule_error = None
        self.checkin_calls = []
        self.checkin_error = None
        self.site_today_value = "2026-11-14"
        self.site_today_values = []
        self.site_today_error = None
        self.appointment_receipts = {}
        self.appointment_receipt_records = []
        self.appointment_receipt_calls = []
        self.appointment_receipt_list_error = None
        self.appointment_ack_calls = []
        self.appointment_ack_status = "acked"
        self.appointment_ack_error = None
        self.appointment_fence_calls = []
        self.appointment_fence_claim = FakeClaim()
        self.appointment_fence_error = None
        self.snapshot_resets = 0
        self.insert_expected_calls = []
        self.setting_sequences = {}
        self.call_order = []

    def setting(self, key):
        sequence = self.setting_sequences.get(key)
        if sequence:
            return sequence.pop(0)
        return self._settings.get(key)

    def csrf_token(self):
        self.csrf_calls += 1
        return self._csrf_token

    def csrf_token_valid(self, supplied=None):
        self.csrf_calls += 1
        self.csrf_supplied.append(supplied)
        if not isinstance(supplied, str) or not supplied:
            return False
        if not isinstance(self._session_csrf_token, str) or not self._session_csrf_token:
            return False
        return hmac.compare_digest(self._session_csrf_token, supplied)

    def mark_no_store(self):
        self.no_store_calls += 1

    def suppress_traceback(self):
        self.traceback_suppressions += 1

    def respond_anonymous(self):
        self.anonymous_calls += 1
        return dto.build_anonymous()

    def deny(self, code="denied"):
        self.denials.append(code)
        raise Denied(code)

    def native_patient_display_name(self, native_patient_id):
        self.patient_reads.append(native_patient_id)
        if self.patient_read_error is not None:
            raise self.patient_read_error
        return self.patient_authority.get(native_patient_id)

    def native_patient_view(self, native_patient_id):
        self.patient_views.append(native_patient_id)
        if self.patient_view_error is not None:
            raise self.patient_view_error
        return self.patient_view_detail.get(native_patient_id)

    def native_appointment_view(self, native_appointment_id):
        self.appointment_views.append(native_appointment_id)
        if self.appointment_view_error is not None:
            raise self.appointment_view_error
        return self.appointment_view_detail.get(native_appointment_id)

    # -- local synthetic write path -------------------------------------

    def respond_write(self, status, payload):
        self.write_responses.append((status, payload))
        return payload

    def write_deployment_approved(self):
        return self.deployment_approved

    def write_fence_ready(self):
        return self.fence_ready

    def healthcare_settings_side_effects_unsafe(self):
        return self.side_effects_unsafe

    def native_patient_target_unsafe(self, native_patient_id):
        return native_patient_id in self.target_unsafe

    def strip_message_channels(self):
        self.strip_calls += 1

    def native_patient_can_create(self):
        return self.can_create

    def native_patient_can_write(self, native_patient_id=None):
        if self.write_authority is not None:
            return native_patient_id in self.write_authority
        return self.can_write

    def native_gender_exists(self, sex):
        return sex in self.genders

    def native_patient_modified(self, native_patient_id):
        return self.patient_modified.get(native_patient_id)

    def prepare_native_patient_create(self, fields):
        self.prepare_calls.append(dict(fields))
        if self.prepare_error is not None:
            raise self.prepare_error
        if self.default_side_effect is not None:
            raise patient_write.UnsafeSideEffect(self.default_side_effect)
        return dict(fields)

    def insert_native_patient(self, prepared):
        self.create_calls.append(dict(prepared))
        if self.create_error is not None:
            raise self.create_error
        return self.created_native_id

    def native_patient_create(self, fields):
        return self.insert_native_patient(
            self.prepare_native_patient_create(fields)
        )

    def acknowledge_write(self, secret, actor, kind, operation_key, ack_token):
        self.ack_calls.append((actor, kind, operation_key, ack_token))
        if self.ack_error is not None:
            raise self.ack_error
        return self.ack_status

    def latch_manual_investigation(self, reason):
        self.latch_calls.append(reason)
        if self.latch_error is not None:
            raise self.latch_error

    def latch_operation_uncertain(self, actor, kind, operation_key, reason):
        self.operation_latch_calls.append((actor, kind, operation_key, reason))
        if self.operation_latch_error is not None:
            raise self.operation_latch_error

    def native_patient_update(self, native_patient_id, fields, base_modified):
        self.update_calls.append(
            (native_patient_id, dict(fields), base_modified)
        )
        if self.update_error is not None:
            raise self.update_error

    def commit(self):
        self.commit_calls += 1

    def rollback(self):
        self.rollback_calls += 1

    def lookup_create_receipt(self, actor, operation_key):
        value = self.create_receipts.get((actor, operation_key))
        if isinstance(value, Exception):
            raise value
        return value

    def list_committed_create_receipts(self, limit, operation_key=None):
        self.receipt_list_calls.append((limit, operation_key))
        if self.receipt_list_error is not None:
            raise self.receipt_list_error
        records = [
            record
            for record in self.created_receipts
            if operation_key is None or record.operation_key == operation_key
        ]
        if len(records) > limit:
            raise patient_write.WriteUncertain()
        return records

    def patient_write_fence(self, actor, kind, operation_key, payload_digest):
        self.fence_calls.append((actor, kind, operation_key, payload_digest))
        return FakeFence(self.fence_claim, self.fence_error)

    # -- local synthetic appointment write path -------------------------

    def native_patient_can_read(self, native_patient_id):
        self.patient_read_calls.append(native_patient_id)
        return self.patient_can_read

    def native_doctype_can_read(self, doctype, doc=None):
        self.doctype_read_calls.append((doctype, doc))
        return self.doctype_reads.get(doctype, True)

    def native_appointment_can_create(self):
        return self.appointment_can_create

    def native_appointment_can_write(self, native_appointment_id=None):
        if self.appointment_write_authority is not None:
            return native_appointment_id in self.appointment_write_authority
        return self.appointment_can_write

    def healthcare_appointment_side_effects_unsafe(self):
        return self.appointment_side_effects_unsafe

    def native_appointment_detail(self, native_appointment_id):
        self.appointment_detail_calls = getattr(
            self, "appointment_detail_calls", []
        )
        self.appointment_detail_calls.append(native_appointment_id)
        if self.appointment_detail_error is not None:
            raise self.appointment_detail_error
        value = self.appointment_detail_map.get(native_appointment_id)
        return dict(value) if value is not None else None

    def native_appointment_modified(self, native_appointment_id):
        return self.appointment_modified.get(native_appointment_id)

    def native_appointment_boundary_problem(self):
        if self.appointment_boundary_problem_sequence:
            return self.appointment_boundary_problem_sequence.pop(0)
        return self.appointment_boundary_problem

    def native_appointment_type_contract(self, appointment_type):
        contract = self.appointment_type_contract
        if contract is None:
            return None
        duration = self.appointment_type_duration
        if isinstance(duration, bool) or not isinstance(duration, int):
            return None
        if duration <= 0:
            return None
        return {
            "appointment_type": contract.get("appointment_type"),
            "default_duration": duration,
            "allow_booking_for": contract.get("allow_booking_for"),
        }

    def native_appointment_type_duration(self, appointment_type):
        contract = self.native_appointment_type_contract(appointment_type)
        return None if contract is None else contract["default_duration"]

    def native_appointment_sources(self, patient, appointment_type, *, lock=True):
        self.call_order.append("sources")
        self.appointment_source_calls.append((patient, appointment_type, lock))
        if self.appointment_sources_error is not None:
            raise self.appointment_sources_error
        return dict(self.appointment_sources)

    def native_appointment_target_problem(
        self, native_appointment_id, *, expected=None
    ):
        self.appointment_target_problem_calls.append(
            (native_appointment_id, expected)
        )
        return self.appointment_target_problems.get(native_appointment_id)

    def lock_appointment_target(
        self, native_appointment_id, *, base_modified, expected=None
    ):
        self.appointment_lock_calls.append(
            (native_appointment_id, base_modified, expected)
        )
        if self.appointment_lock_error is not None:
            raise self.appointment_lock_error
        values = {
            "doc": None,
            "patient": "PAT-1",
            "company": "COMP-1",
            "department": "MEDDEP-1",
            "appointment_type": "APPT-TYPE-1",
            "appointment_for": "Department",
            "duration": 15,
            "status": "Open",
            "appointment_date": self.site_today_value,
            "appointment_time": "08:00:00",
            "modified": base_modified,
        }
        values.update(self.appointment_locked.get(native_appointment_id, {}))
        return FakeLockedState(**values)

    def save_appointment_reschedule(self, state, fields, today=None):
        self.save_reschedule_calls.append((state, dict(fields), today))
        if self.save_reschedule_error is not None:
            raise self.save_reschedule_error

    def prepare_native_appointment_create(self, fields, *, expected=None):
        self.prepare_appointment_calls.append(dict(fields))
        if self.prepare_appointment_error is not None:
            raise self.prepare_appointment_error
        prepared = dict(self.prepared_appointment)
        # frappe.new_doc pre-fills the reviewed Select static default, so the
        # prepared fixture carries the pinned naming series like the real doc.
        prepared.setdefault(
            "naming_series", appointment_contract.PINNED_NAMING_SERIES
        )
        return prepared

    def insert_native_appointment(self, prepared, *, expected=None, today=None):
        self.call_order.append("insert")
        self.insert_appointment_calls.append(prepared)
        self.insert_expected_calls.append(expected)
        if self.insert_appointment_error is not None:
            raise self.insert_appointment_error
        return self.created_appointment_id

    def verify_native_appointment_insert(
        self, native_appointment_id, expected, sources, today
    ):
        self.appointment_insert_verify_calls.append(
            (native_appointment_id, dict(expected), dict(sources), today)
        )
        if self.appointment_insert_verify_error is not None:
            raise self.appointment_insert_verify_error

    def reset_native_read_snapshot(self):
        self.call_order.append("snapshot_reset")
        self.snapshot_resets += 1

    def native_appointment_checkin(self, state, today):
        self.checkin_calls.append((state, today))
        if self.checkin_error is not None:
            raise self.checkin_error

    def site_today(self):
        if self.site_today_error is not None:
            raise self.site_today_error
        if self.site_today_values:
            return self.site_today_values.pop(0)
        return self.site_today_value

    def lookup_appointment_receipt(self, actor, kind, operation_key):
        value = self.appointment_receipts.get((actor, kind, operation_key))
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        for record in self.appointment_receipt_records:
            if (
                record.actor == actor
                and record.kind == kind
                and record.operation_key == operation_key
            ):
                return record.receipt
        return None

    def list_committed_appointment_receipts(
        self, limit, kind=None, operation_key=None
    ):
        self.appointment_receipt_calls.append((limit, kind, operation_key))
        if self.appointment_receipt_records and len(
            [
                record
                for record in self.appointment_receipt_records
                if (kind is None or record.kind == kind)
                and (
                    operation_key is None
                    or record.operation_key == operation_key
                )
            ]
        ) > limit:
            from clinic_ui.core import appointment_write

            raise appointment_write.AppointmentBudgetExhausted()
        if self.appointment_receipt_list_error is not None:
            raise self.appointment_receipt_list_error
        return [
            record
            for record in self.appointment_receipt_records
            if (kind is None or record.kind == kind)
            and (
                operation_key is None or record.operation_key == operation_key
            )
        ]

    def acknowledge_appointment_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.appointment_ack_calls.append(
            (actor, kind, operation_key, ack_token)
        )
        if self.appointment_ack_error is not None:
            raise self.appointment_ack_error
        return self.appointment_ack_status

    def appointment_write_fence(
        self, actor, kind, operation_key, payload_digest
    ):
        self.call_order.append("fence")
        self.appointment_fence_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        return FakeFence(
            self.appointment_fence_claim, self.appointment_fence_error
        )



def detail():
    return {
        "patient_name": "Patient One",
        "first_name": "Patient",
        "last_name": "One",
        "sex": "Female",
        "dob": "1990-01-02",
        "status": "Active",
        "modified": "2026-11-14T09:30:00.123456",
    }


def session_view(
    *,
    sid="sid-abc123",
    user="nurse@example.test",
    full_name="Nurse Example",
    enabled=True,
    roles=("Nurse",),
):
    return SessionView(
        sid=sid,
        user=user,
        full_name=full_name,
        enabled=enabled,
        roles=tuple(roles),
    )


SECRET = "0123456789abcdef0123456789abcdef"


def settings(**overrides):
    """Valid default site settings for the fictional synthetic lab site."""

    values = {
        "clinic_ui_enabled": 1,
        "clinic_ui_synthetic_site": "marley.test",
        "clinic_ui_epoch_secret": SECRET,
        "clinic_ui_role_map": {
            "DOC": ["Physician"],
            "REC": ["Clinic Reception"],
            "NUR": ["Nurse"],
        },
        "clinic_ui_synthetic_patients": [
            {
                "workspace_patient_id": "WS-1",
                "native_patient_id": "PAT-1",
            }
        ],
        "clinic_ui_native_patient_write_enabled": 1,
        "clinic_ui_native_patient_write_site": "marley.test",
    }
    values.update(overrides)
    return values
