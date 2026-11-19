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


class NativePermissionDenied(Exception):
    """Stands in for the framework's own permission exception *type*.

    Endpoints must recognise a definite denial by this type identity, never by
    the class name: an unrelated exception named ``PermissionError`` must stay
    an uncertain outcome.
    """


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

    def as_dict(self):
        return {
            key: value
            for key, value in vars(self).items()
            if not key.startswith("_")
        }


class FakeClaim:
    def __init__(self, call_order=None, label=None):
        self.committed = []
        self.unknowns = []
        self._call_order = call_order
        self._label = label

    def commit(self, receipt):
        if self._call_order is not None:
            self._call_order.append(f"{self._label}_claim_commit")
        self.committed.append(receipt)

    def unknown(self, reason):
        self.unknowns.append(reason)


class FakeLease:
    """Stand-in for ``write_fence.Lease``: one dispatch attempt, no records."""

    def __init__(self, gateway):
        self.gateway = gateway

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def dispatch(self):
        self.gateway.call_order.append("encounter_dispatch")
        self.gateway.encounter_lease_dispatches.append(
            self.gateway.encounter_lease_calls[-1]
        )
        if self.gateway.encounter_dispatch_error is not None:
            raise self.gateway.encounter_dispatch_error
        return self.gateway.encounter_lease_claim


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
        self.gender_options = ["Female"]
        self.gender_option_calls = []
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
        self.readable_patients = []
        self.readable_list_calls = []
        self.readable_appointments = []
        self.readable_appointment_calls = []

        # -- appointment write surface ------------------------------------
        self.appointment_detail_map = {}
        self.appointment_detail_error = None
        self.patient_can_read = True
        self.patient_read_authority = None
        self.patient_read_sequence = []
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
        self.appointment_action_details = {}
        self.appointment_action_detail_calls = []
        self.appointment_action_detail_error = None
        self.cancel_calls = []
        self.cancel_error = None
        self.no_show_calls = []
        self.no_show_error = None
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

        # -- encounter write surface --------------------------------------
        self.encounter_boundary_problem = None
        self.encounter_install_problem = None
        self.encounter_can_create = True
        self.encounter_can_write = True
        self.clinical_note_can_create = True
        self.encounter_appointment_type = "APPT-TYPE-1"
        self.locked_appointment = {
            "patient": "PAT-1",
            "status": "Open",
            "appointment_type": "APPT-TYPE-1",
        }
        self.locked_appointment_calls = []
        self.read_locked_appointment_calls = []
        self.encounter_side_effects_unsafe = False
        self.encounter_details = {}
        self.encounter_action_detail_calls = []
        self.encounter_locked = {}
        self.encounter_lock_calls = []
        self.encounter_lock_error = None
        self.created_encounter_id = "NATIVE-ENC-NEW"
        self.create_encounter_calls = []
        self.create_encounter_error = None
        self.save_encounter_calls = []
        self.save_encounter_error = None
        self.submit_encounter_calls = []
        self.submit_encounter_error = None
        self.service_request_baseline = 0
        self.submitted_service_requests = 0
        self.cascade_detail = {}
        self.encounter_detail_map = {}
        self.addendum_id = "NATIVE-NOTE-ADDENDUM"
        self.addendum_calls = []
        self.addendum_error = None
        self.amend_facts = {}
        self.amend_facts_calls = []
        self.encounter_receipts = {}
        self.encounter_receipt_records = []
        self.encounter_operation_calls = []
        self.encounter_operations = {}
        self.encounter_receipt_calls = []
        self.encounter_receipt_list_error = None
        self.encounter_ack_calls = []
        self.encounter_ack_status = "acked"
        self.encounter_ack_error = None
        self.encounter_lease_calls = []
        self.encounter_lease_dispatches = []
        self.encounter_lease_claim = FakeClaim(call_order=self.call_order, label="encounter")
        self.encounter_lease_error = None
        self.encounter_dispatch_error = None
        self.on_encounter_lease_entered = None
        # Optional hook: lets a test change the site between the two gate reads
        # of one request (the replay authority re-read restarts the snapshot).
        self.on_snapshot_reset = None
        self.config_refresh_calls = 0
        self.stale_read_snapshot = False
        self.read_snapshot = None
        # Fresh role read (uncached DB mirror). ``fresh_roles=None`` mirrors the
        # current session; set a tuple to pin the answer, or the two failure
        # flags to model an unreadable table.
        self.fresh_role_calls = []
        self.fresh_roles = None
        self.fresh_roles_unreadable = False
        self.fresh_roles_error = None

        # -- procedure write surface --------------------------------------
        self.procedure_boundary_problem = None
        self.procedure_install_problem = None
        self.procedure_can_write = True
        self.procedure_can_read = True
        self.procedure_read_detail = True
        self.procedure_read_status = {}
        self.procedure_read_detail_calls = []
        self.procedure_view_detail = {}
        self.procedure_view_calls = []
        self.procedure_view_error = None
        self.procedure_view_authority = None
        self.readable_procedures = {}
        self.procedure_list_calls = []
        self.procedure_list_error = None
        self.procedure_details = {}
        self.procedure_action_detail_calls = []
        self.procedure_locked = {}
        self.procedure_lock_calls = []
        self.procedure_lock_error = None
        self.stock_baseline = {
            "stock_ledger_rows": 0,
            "gl_rows": 0,
            "bins": {},
        }
        self.stock_baseline_calls = []
        self.complete_procedure_calls = []
        self.complete_procedure_error = None
        self.created_stock_entry = "NATIVE-SE-1"
        self.procedure_facts = {}
        self.procedure_facts_calls = []
        self.procedure_receipts = {}
        self.procedure_receipt_records = []
        self.procedure_receipt_calls = []
        self.procedure_receipt_list_error = None
        self.procedure_ack_calls = []
        self.procedure_ack_status = "acked"
        self.procedure_ack_error = None
        self.procedure_fence_calls = []
        self.procedure_fence_claim = FakeClaim()
        self.procedure_fence_error = None

        # -- procedure creation from a submitted Service Request -----------
        self.procedure_can_create = True
        self.service_request_states = {}
        self.service_request_target_calls = []
        self.service_request_lock_calls = []
        self.service_request_lock_error = None
        self.create_procedure_calls = []
        self.create_procedure_error = None
        self.created_procedure_id = "HLC-CPR-NEW"
        self.procedure_create_facts = {}
        self.procedure_create_facts_calls = []

        # -- admin user/master write surface -------------------------------
        self.admin_install_problem = None
        self.admin_can_write = True
        self.admin_can_create = {}
        self.admin_can_create_user_permission = True
        self.admin_user_permission_calls = []
        self.admin_missing_roles = []
        self.admin_users = set()
        self.admin_usernames = set()
        self.admin_practitioners = set()
        self.admin_practitioner_linked = set()
        self.admin_practitioner_can_write = {}
        self.admin_email_problem = None
        self.admin_masters = set()
        self.admin_item_prices = set()
        self.admin_user_exists_sequence = []
        self.admin_user_exists_calls = []
        self.admin_practitioner_exists_calls = []
        self.admin_master_exists_calls = []
        self.admin_item_price_exists_calls = []
        self.admin_user_create_calls = []
        self.admin_user_create_error = None
        self.created_admin_user = None
        self.admin_master_create_calls = []
        self.admin_master_create_error = None
        self.created_admin_master = None
        self.admin_user_facts = {}
        self.admin_master_facts = {}
        self.admin_user_facts_calls = []
        self.admin_master_facts_calls = []
        self.admin_prerequisites = {
            "company": "SIT Synthetic Company",
            "uom_rows": 1,
            "supplier_group": "All Supplier Groups",
            "supplier_group_rows": 1,
            # Amendment A1.1: mandatory Link row counts on Healthcare
            # Practitioner, read from installed meta. The probe found none
            # mandatory on this stack, so the fixture is empty.
            "practitioner_links": {},
        }
        self.admin_practitioner_users = {}
        self.admin_enabled_users = set()
        self.admin_practitioner_for_user_calls = []
        self.admin_user_enabled_calls = []
        self.admin_prerequisites_calls = []
        self.admin_receipts = {}
        self.admin_receipt_records = []
        self.admin_receipt_calls = []
        self.admin_receipt_list_error = None
        self.admin_ack_calls = []
        self.admin_ack_status = "acked"
        self.admin_ack_error = None
        self.admin_fence_calls = []
        self.admin_fence_claim = FakeClaim()
        self.admin_fence_error = None

        # -- stock receive/transfer/adjust surface -------------------------
        self.stock_boundary_problem = None
        self.stock_install_problem = None
        self.stock_create_authority = {}
        self.stock_receive_detail = None
        self.stock_transfer_detail = None
        self.stock_adjust_detail = None
        self.stock_detail_calls = []
        self.stock_detail_sequence = []
        self.stock_baseline_value = {
            "stock_ledger_rows": 0,
            "gl_rows": 0,
            "bins": {},
        }
        self.stock_baseline_calls = []
        self.stock_receipt_id = "PR-1"
        self.stock_transfer_id = "SE-1"
        self.stock_reconciliation_id = "SR-1"
        self.stock_receipt_calls = []
        self.stock_transfer_calls = []
        self.stock_reconciliation_calls = []
        self.stock_mutation_error = None
        self.stock_facts = {}
        self.stock_facts_calls = []
        self.stock_receipt_records = []
        self.stock_receipt_list_calls = []
        self.stock_receipt_list_error = None
        self.stock_ack_calls = []
        self.stock_ack_status = "acked"
        self.stock_ack_error = None
        self.stock_fence_calls = []
        self.stock_fence_claim = FakeClaim()
        self.stock_fence_error = None

        # -- purchasing (PO draft/submit + receipt) surface -----------------
        self.purchase_boundary_problem = None
        self.purchase_install_problem = None
        self.purchase_draft_install_problem = None
        self.purchase_create_authority = {}
        self.purchase_draft_detail = None
        self.purchase_submit_detail = None
        self.purchase_receipt_detail = None
        self.purchase_detail_calls = []
        self.purchase_detail_sequence = []
        self.purchase_baseline_value = {
            "stock_ledger_rows": 0,
            "gl_rows": 0,
            "bins": {},
            "po_received": 0.0,
        }
        self.purchase_baseline_calls = []
        self.purchase_draft_id = "PO-DRAFT-1"
        self.purchase_submit_id = "PO-SUBMITTED-1"
        self.purchase_receipt_id = "PR-1"
        self.purchase_draft_calls = []
        self.purchase_submit_order_calls = []
        self.purchase_receipt_calls = []
        self.purchase_mutation_error = None
        self.purchase_facts = {}
        self.purchase_facts_calls = []
        self.purchase_receipt_records = []
        self.purchase_receipt_list_calls = []
        self.purchase_receipt_list_error = None
        self.purchase_ack_calls = []
        self.purchase_ack_status = "acked"
        self.purchase_ack_error = None
        self.purchase_fence_calls = []
        self.purchase_fence_claim = FakeClaim()
        self.purchase_fence_error = None
        self.purchase_order_view_value = None
        self.purchase_order_view_missing = False
        self.purchase_order_view_calls = []
        self.purchase_draft_names = []
        self.purchase_order_drafts_calls = []
        self.purchase_order_drafts_error = None

        # -- finance (invoice submit + payment receive) surface ------------
        self.finance_boundary_problem = None
        self.finance_install_problem = None
        self.finance_create_authority = {}
        self.finance_submit_authority = {}
        self.finance_invoice_detail = None
        self.finance_payment_detail = None
        self.finance_detail_calls = []
        self.finance_detail_sequence = []
        self.finance_baseline_value = {
            "gl_rows": 0,
            "ple_rows": 0,
            "sle_rows": 0,
            "invoice_outstanding": None,
            "invoice_status": None,
        }
        self.finance_baseline_calls = []
        self.finance_invoice_id = "SINV-1"
        self.finance_payment_id = "PE-1"
        self.finance_invoice_calls = []
        self.finance_payment_calls = []
        self.finance_mutation_error = None
        self.finance_facts = {}
        self.finance_facts_calls = []
        self.finance_receipt_records = []
        self.finance_receipt_list_calls = []
        self.finance_receipt_list_error = None
        self.finance_ack_calls = []
        self.finance_ack_status = "acked"
        self.finance_ack_error = None
        self.finance_fence_calls = []
        self.finance_fence_claim = FakeClaim()
        self.finance_fence_error = None
        self.finance_invoice_view_value = None
        self.finance_invoice_view_missing = False
        self.finance_invoice_view_calls = []
        self.finance_invoice_names = []
        self.finance_invoice_list_calls = []
        self.finance_invoice_list_error = None
        self.finance_posted_facts = None
        self.finance_posted_facts_calls = []
        self.finance_posted_facts_error = None

    def setting(self, key):
        sequence = self.setting_sequences.get(key)
        if sequence:
            return sequence.pop(0)
        if self.stale_read_snapshot:
            if self.read_snapshot is None:
                self.read_snapshot = dict(self._settings)
            return self.read_snapshot.get(key)
        return self._settings.get(key)

    def refresh_site_config(self):
        self.call_order.append("config_refresh")
        self.config_refresh_calls += 1

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

    def is_native_permission_denial(self, exc):
        """Identity-based recognition, mirroring the real adapter."""

        return isinstance(exc, NativePermissionDenied)

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

    def native_gender_options(self, limit):
        self.gender_option_calls.append(limit)
        if not isinstance(self.gender_options, (list, tuple)):
            # ``None`` simulates a refused/failed read; anything else simulates
            # a malformed adapter response.
            return self.gender_options
        return list(self.gender_options)[:limit]

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
        self.call_order.append("commit")
        self.commit_calls += 1

    def rollback(self):
        self.call_order.append("rollback")
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

    def list_readable_patients(self, limit):
        self.readable_list_calls.append(limit)
        if self.readable_patients is None:
            return None
        return list(self.readable_patients)[:limit]

    def list_readable_appointments(self, limit):
        self.readable_appointment_calls.append(limit)
        if self.readable_appointments is None:
            return None
        return list(self.readable_appointments)[:limit]

    # -- local synthetic appointment write path -------------------------

    def native_patient_can_read(self, native_patient_id):
        self.patient_read_calls.append(native_patient_id)
        if self.patient_read_sequence:
            return self.patient_read_sequence.pop(0)
        if self.patient_read_authority is not None:
            return native_patient_id in self.patient_read_authority
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
            "notes": None,
            "practitioner": None,
            "event": None,
            "invoiced": 0,
            "ref_sales_invoice": None,
            "paid_amount": 0,
            "insurance_coverage": None,
            "service_request": None,
            "mode_of_payment": None,
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
        # A restarted snapshot drops the reads this request already made.
        self.read_snapshot = None
        if self.on_snapshot_reset is not None:
            self.on_snapshot_reset(self)

    def fresh_user_roles(self, user):
        """Mirror the adapter's uncached ``Has Role`` read."""

        self.call_order.append("fresh_roles_read")
        self.fresh_role_calls.append(user)
        if self.fresh_roles_error is not None:
            raise self.fresh_roles_error
        if self.fresh_roles_unreadable:
            return None
        if self.fresh_roles is not None:
            return tuple(self.fresh_roles)
        view = self.current_session
        if view is None or view.user != user:
            return ()
        return tuple(view.roles)

    def native_appointment_checkin(self, state, today):
        self.checkin_calls.append((state, today))
        if self.checkin_error is not None:
            raise self.checkin_error

    def native_appointment_action_detail(self, native_appointment_id):
        self.appointment_action_detail_calls.append(native_appointment_id)
        if self.appointment_action_detail_error is not None:
            raise self.appointment_action_detail_error
        value = self.appointment_action_details.get(native_appointment_id)
        return dict(value) if value is not None else None

    def cancel_native_appointment(self, state, reason, actor):
        self.cancel_calls.append((state, reason, actor))
        if self.cancel_error is not None:
            raise self.cancel_error

    def no_show_native_appointment(self, state, reason, actor):
        self.no_show_calls.append((state, reason, actor))
        if self.no_show_error is not None:
            raise self.no_show_error

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



    # -- local encounter write path -------------------------------------

    def native_encounter_boundary_problem(self):
        self.call_order.append("gate_read")
        return self.encounter_boundary_problem

    def native_encounter_install_problem(self):
        return self.encounter_install_problem

    def native_encounter_can_create(self):
        return self.encounter_can_create

    def native_encounter_can_write(self, native_encounter_id=None):
        return self.encounter_can_write

    def native_clinical_note_can_create(self):
        return self.clinical_note_can_create

    def native_appointment_encounter_type(self, native_appointment_id):
        self.encounter_appointment_type_calls = getattr(
            self, "encounter_appointment_type_calls", []
        )
        self.encounter_appointment_type_calls.append(native_appointment_id)
        return self.encounter_appointment_type

    def lock_linked_appointment(self, native_appointment_id):
        """Stand-in for the FOR UPDATE read: returns the current row or None."""

        self.call_order.append("appointment_lock")
        self.locked_appointment_calls.append(native_appointment_id)
        if self.locked_appointment is None:
            return None
        return dict(self.locked_appointment)

    def lock_linked_appointment_for_read(self, native_appointment_id):
        """Stand-in for the read-authority FOR UPDATE lock of the booking."""

        self.call_order.append("appointment_lock")
        self.read_locked_appointment_calls.append(native_appointment_id)
        if self.locked_appointment is None:
            return None
        return dict(self.locked_appointment)

    def healthcare_encounter_side_effects_unsafe(self):
        return self.encounter_side_effects_unsafe

    def native_submitted_service_request_count(self):
        return self.service_request_baseline

    def native_encounter_action_detail(self, native_encounter_id):
        self.encounter_action_detail_calls.append(native_encounter_id)
        value = self.encounter_details.get(native_encounter_id)
        return dict(value) if value is not None else None

    def lock_encounter_target(self, native_encounter_id, base_modified=None):
        self.call_order.append("encounter_lock")
        self.encounter_lock_calls.append((native_encounter_id, base_modified))
        if self.encounter_lock_error is not None:
            raise self.encounter_lock_error
        values = dict(self.encounter_details.get(native_encounter_id, {}))
        values.update(self.encounter_locked.get(native_encounter_id, {}))
        values.setdefault("doc", None)
        return FakeLockedState(**values)

    def create_native_encounter_draft(self, payload):
        self.call_order.append("encounter_create")
        self.create_encounter_calls.append(dict(payload))
        if self.create_encounter_error is not None:
            raise self.create_encounter_error
        return self.created_encounter_id

    def save_native_encounter_draft(self, state, payload):
        self.call_order.append("encounter_save")
        self.save_encounter_calls.append((state, dict(payload)))
        if self.save_encounter_error is not None:
            raise self.save_encounter_error
        return getattr(state, "native_id", self.created_encounter_id)

    def submit_native_encounter(self, state):
        self.call_order.append("encounter_submit")
        self.submit_encounter_calls.append(state)
        if self.submit_encounter_error is not None:
            raise self.submit_encounter_error

    def native_encounter_cascade_detail(self, native_encounter_id, baseline):
        if self.cascade_detail:
            return dict(self.cascade_detail)
        return {
            "docstatus": 1,
            "status": "Completed",
            "appointment_status": "Closed",
            "submitted_service_requests": self.submitted_service_requests,
        }

    def native_encounter_detail(self, native_encounter_id):
        value = self.encounter_detail_map.get(native_encounter_id)
        if value is not None:
            return dict(value)
        return {
            "patient": "PAT-1",
            "docstatus": 0,
            "status": "Draft",
            "modified": "2026-11-14 09:30:00.123456",
        }

    def create_native_clinical_note_addendum(self, state, note_text, actor):
        self.call_order.append("encounter_addendum")
        self.addendum_calls.append((state, note_text, actor))
        if self.addendum_error is not None:
            raise self.addendum_error
        return self.addendum_id

    def native_encounter_amend_facts(
        self,
        native_encounter_id,
        addendum_id,
        expected_note,
        original_note,
        original_note_modified,
    ):
        self.amend_facts_calls.append(
            (
                native_encounter_id,
                addendum_id,
                expected_note,
                original_note,
                original_note_modified,
            )
        )
        values = {
            "addendum_created": True,
            "addendum_note": expected_note,
            "encounter_modified": "2026-11-14 09:30:00.123456",
            "clinical_note_modified": original_note_modified,
        }
        values.update(self.amend_facts)
        return values

    def lookup_encounter_receipt(self, actor, kind, operation_key):
        value = self.encounter_receipts.get((actor, kind, operation_key))
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        for record in self.encounter_receipt_records:
            if (
                record.actor == actor
                and record.kind == kind
                and record.operation_key == operation_key
            ):
                return record.receipt
        return None

    def lookup_encounter_operation(self, actor, kind, operation_key):
        self.encounter_operation_calls.append((actor, kind, operation_key))
        value = self.encounter_operations.get((actor, kind, operation_key))
        if isinstance(value, Exception):
            raise value
        return value

    def list_committed_encounter_receipts(
        self, limit, kind=None, operation_key=None
    ):
        self.encounter_receipt_calls.append((limit, kind, operation_key))
        if self.encounter_receipt_list_error is not None:
            raise self.encounter_receipt_list_error
        from clinic_ui.core import encounter_write

        records = [
            record
            for record in self.encounter_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise encounter_write.EncounterBudgetExhausted()
        return records

    def acknowledge_encounter_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.encounter_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.encounter_ack_error is not None:
            raise self.encounter_ack_error
        return self.encounter_ack_status

    def encounter_write_lease(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("encounter_lease")
        self.encounter_lease_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        if self.on_encounter_lease_entered is not None:
            self.on_encounter_lease_entered(self)
        if self.encounter_lease_error is not None:
            raise self.encounter_lease_error
        return FakeLease(self)

    def receipt_native_encounter_id(self, receipt):
        from clinic_ui.core import encounter_write

        return encounter_write.decode_receipt(receipt).native_encounter_id

    # -- local procedure write path -------------------------------------

    def native_procedure_boundary_problem(self):
        return self.procedure_boundary_problem

    def native_procedure_install_problem(self):
        return self.procedure_install_problem

    def native_procedure_can_write(self, native_procedure_id=None):
        return self.procedure_can_write

    def native_procedure_can_read(self, native_procedure_id):
        return self.procedure_can_read

    def native_procedure_read_detail(self, native_procedure_id):
        """The committed status under the current user's read authority."""

        self.procedure_read_detail_calls.append(native_procedure_id)
        if self.procedure_read_detail is False:
            return None
        if self.procedure_can_read is not True:
            return None
        # The read-back models the committed state, which is independent of the
        # pre-mutation detail the endpoint reads before dispatch.
        values = {
            "procedure_id": native_procedure_id,
            "status": "Completed",
            "docstatus": 1,
        }
        if (
            native_procedure_id == self.created_procedure_id
            and native_procedure_id not in self.procedure_read_status
        ):
            # A row created by the converted lane is submitted and pending.
            values["status"] = "Pending"
        values.update(self.procedure_read_status.get(native_procedure_id) or {})
        return values

    def native_procedure_view(self, native_procedure_id):
        """The three read fields under the current user's read authority."""

        self.procedure_view_calls.append(native_procedure_id)
        if self.procedure_view_error is not None:
            raise self.procedure_view_error
        if self.procedure_can_read is not True:
            return None
        if (
            self.procedure_view_authority is not None
            and native_procedure_id not in self.procedure_view_authority
        ):
            return None
        values = {
            "procedure_id": native_procedure_id,
            "status": "Completed",
            "docstatus": 1,
        }
        values.update(self.procedure_read_status.get(native_procedure_id) or {})
        values.update(self.procedure_view_detail.get(native_procedure_id) or {})
        return dict(values)

    def native_procedures_for_service_request(self, service_request, limit):
        """Permission-filtered linked procedure names, bounded by ``limit``."""

        self.procedure_list_calls.append((service_request, limit))
        if self.procedure_list_error is not None:
            raise self.procedure_list_error
        names = self.readable_procedures
        if isinstance(names, dict):
            names = names.get(service_request) or ()
        names = list(names)
        if len(names) > limit:
            return None
        return names

    def native_procedure_action_detail(self, native_procedure_id):
        self.procedure_action_detail_calls.append(native_procedure_id)
        value = self.procedure_details.get(native_procedure_id)
        return dict(value) if value is not None else None

    def lock_procedure_target(self, native_procedure_id):
        self.procedure_lock_calls.append(native_procedure_id)
        if self.procedure_lock_error is not None:
            raise self.procedure_lock_error
        values = dict(self.procedure_details.get(native_procedure_id, {}))
        values.update(self.procedure_locked.get(native_procedure_id, {}))
        values.setdefault("doc", None)
        return FakeLockedState(**values)

    def native_stock_ledger_baseline(self, items, warehouse):
        self.stock_baseline_calls.append((items, warehouse))
        return dict(self.stock_baseline)

    def complete_native_procedure(self, state):
        self.complete_procedure_calls.append(state)
        if self.complete_procedure_error is not None:
            raise self.complete_procedure_error
        return self.created_stock_entry

    def native_procedure_complete_facts(
        self, native_procedure_id, stock_entry, baseline
    ):
        self.procedure_facts_calls.append(
            (native_procedure_id, stock_entry, baseline)
        )
        values = {
            "stock_entry": stock_entry,
            "stock_entry_docstatus": 1,
            "stock_entry_type": "Material Issue",
            "stock_entry_company": "COMP-1",
            "procedure_company": "COMP-1",
            "status": "Completed",
            "actual_end_datetime": "2026-11-16 10:00:00",
            "stock_ledger_rows": 1,
            "gl_rows": 2,
            "bin_delta": -1.0,
            "bin_warehouse": "WH-1",
            "nursing_task_rows": 0,
        }
        values.update(self.procedure_facts)
        return values

    def lookup_procedure_receipt(self, actor, kind, operation_key):
        value = self.procedure_receipts.get((actor, kind, operation_key))
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        for record in self.procedure_receipt_records:
            if (
                record.actor == actor
                and record.kind == kind
                and record.operation_key == operation_key
            ):
                return record.receipt
        return None

    def list_committed_procedure_receipts(
        self, limit, kind=None, operation_key=None
    ):
        self.procedure_receipt_calls.append((limit, kind, operation_key))
        if self.procedure_receipt_list_error is not None:
            raise self.procedure_receipt_list_error
        from clinic_ui.core import procedure_write

        records = [
            record
            for record in self.procedure_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise procedure_write.ProcedureBudgetExhausted()
        return records

    def acknowledge_procedure_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.procedure_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.procedure_ack_error is not None:
            raise self.procedure_ack_error
        return self.procedure_ack_status

    def procedure_write_fence(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("procedure_fence")
        self.procedure_fence_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        return FakeFence(self.procedure_fence_claim, self.procedure_fence_error)

    def receipt_stock_entry(self, receipt):
        from clinic_ui.core import procedure_write

        return procedure_write.decode_receipt(receipt).stock_entry

    # -- procedure creation from a submitted Service Request ---------------

    def native_procedure_can_create(self):
        return self.procedure_can_create

    def native_service_request_target(self, service_request):
        self.service_request_target_calls.append(service_request)
        state = self.service_request_states.get(service_request)
        if state is None:
            return {
                "exists": False,
                "docstatus": None,
                "template_dt": None,
                "template_dn": None,
                "patient": None,
                "procedure_template": None,
                "procedure_exists": False,
                "consume_stock": False,
            }
        return dict(state)

    def lock_service_request_target(self, service_request):
        self.service_request_lock_calls.append(service_request)
        if self.service_request_lock_error is not None:
            raise self.service_request_lock_error
        state = dict(self.service_request_states.get(service_request, {}))
        values = dict(state)
        values.setdefault("doc", None)
        return FakeLockedState(**values)

    def create_native_procedure(self, state):
        self.create_procedure_calls.append(state)
        if self.create_procedure_error is not None:
            raise self.create_procedure_error
        return self.created_procedure_id

    def native_procedure_create_facts(self, native_procedure_id):
        self.procedure_create_facts_calls.append(native_procedure_id)
        state = (
            self.create_procedure_calls[-1] if self.create_procedure_calls else None
        )
        values = {
            "name": native_procedure_id,
            "patient": getattr(state, "patient", None),
            "procedure_template": getattr(state, "procedure_template", None),
            "service_request": getattr(state, "service_request", None),
            "docstatus": 1,
            "status": "Pending",
        }
        values.update(self.procedure_create_facts)
        return values

    def native_procedure_from_create_receipt(self, actor, operation_key):
        from clinic_ui.core import procedure_write

        for record in self.procedure_receipt_records:
            if (
                record.actor == actor
                and record.kind == "procedure_create"
                and record.operation_key == operation_key
            ):
                return procedure_write.decode_receipt(
                    record.receipt
                ).native_procedure_id
        receipt = self.procedure_receipts.get(
            (actor, "procedure_create", operation_key)
        )
        if isinstance(receipt, str):
            return procedure_write.decode_receipt(
                receipt
            ).native_procedure_id
        return None

    # -- local admin user/master write path -----------------------------

    def native_admin_install_problem(self):
        return self.admin_install_problem

    def native_admin_can_write(self):
        return self.admin_can_write

    def native_admin_can_create(self, doctype):
        return self.admin_can_create.get(doctype, True)

    def native_admin_missing_roles(self, roles):
        return list(self.admin_missing_roles)

    def native_admin_user_exists(self, identifier):
        self.admin_user_exists_calls.append(identifier)
        if self.admin_user_exists_sequence:
            return self.admin_user_exists_sequence.pop(0)
        return identifier in self.admin_users

    def native_admin_username_exists(self, username):
        """Accurate duplicate check against the installed ``username`` field."""

        return bool(username) and username.lower() in self.admin_usernames

    def native_admin_practitioner_is_linked(self, name):
        """Whether the practitioner already points at any user."""

        return name in self.admin_practitioner_linked

    def native_admin_can_write_practitioner(self, name):
        return self.admin_practitioner_can_write.get(name, True)

    def native_admin_can_create_user_permission(self):
        self.admin_user_permission_calls.append(True)
        return self.admin_can_create_user_permission

    def native_admin_email_problem(self, email):
        return self.admin_email_problem

    def native_admin_practitioner_exists(self, name):
        self.admin_practitioner_exists_calls.append(name)
        return name in self.admin_practitioners

    def native_admin_master_exists(self, master_type, identifier):
        self.admin_master_exists_calls.append((master_type, identifier))
        return (master_type, identifier) in self.admin_masters

    def native_admin_practitioner_for_user(self, user):
        self.admin_practitioner_for_user_calls.append(user)
        return self.admin_practitioner_users.get(user)

    def native_admin_user_enabled(self, user):
        self.admin_user_enabled_calls.append(user)
        return user in self.admin_enabled_users

    def native_admin_item_price_exists(self, item_code, price_list):
        self.admin_item_price_exists_calls.append((item_code, price_list))
        return (item_code, price_list) in self.admin_item_prices

    def native_admin_prerequisites(self):
        self.admin_prerequisites_calls.append(dict(self.admin_prerequisites))
        return dict(self.admin_prerequisites)

    def create_native_admin_user(self, payload):
        self.admin_user_create_calls.append(dict(payload))
        if self.admin_user_create_error is not None:
            raise self.admin_user_create_error
        name = self.created_admin_user or payload.get("email")
        # Controller-like bookkeeping: the installed User is named by email and
        # the username/link become taken once the mutation succeeds.
        self.admin_users.add(name)
        if payload.get("username"):
            self.admin_usernames.add(payload["username"].lower())
        if payload.get("practitioner"):
            self.admin_practitioner_linked.add(payload["practitioner"])
        return name

    def create_native_admin_master(self, master_type, payload):
        self.admin_master_create_calls.append((master_type, dict(payload)))
        if self.admin_master_create_error is not None:
            raise self.admin_master_create_error
        if self.created_admin_master:
            return self.created_admin_master
        if master_type == "warehouse":
            return payload["warehouse_name"]
        if master_type == "supplier":
            return payload["supplier_name"]
        if master_type == "item":
            return payload["item_code"]
        if master_type == "price_list":
            return payload["price_list_name"]
        if master_type == "mode_of_payment":
            return payload["mode_of_payment"]
        if master_type == "supplier_group":
            return payload["supplier_group_name"]
        if master_type == "uom":
            return payload["uom_name"]
        if master_type == "practitioner":
            # The installed row is autonamed; the fake returns a stable docname.
            return "HLC-PRAC-2026-00001"
        return payload["template_name"]

    def native_admin_user_facts(self, user):
        self.admin_user_facts_calls.append(user)
        if self.admin_user_facts:
            return dict(self.admin_user_facts)
        payload = self.admin_user_create_calls[-1]
        return {
            "user": user,
            "enabled": 1,
            "roles": list(payload["roles"]),
            "practitioner": payload.get("practitioner"),
        }

    def native_admin_master_facts(self, master_type, name):
        self.admin_master_facts_calls.append((master_type, name))
        if self.admin_master_facts:
            return dict(self.admin_master_facts)
        _master_type, payload = self.admin_master_create_calls[-1]
        if master_type == "warehouse":
            return {"name": name, "company": "SIT Synthetic Company"}
        if master_type == "supplier":
            return {"name": name, "supplier_name": payload["supplier_name"]}
        if master_type == "item":
            return {
                "name": name,
                "item_code": payload["item_code"],
                "price_list": payload["price_list"],
                "price_list_rate": payload["price_list_rate"],
            }
        if master_type == "price_list":
            return {"name": name, "currency": "USD", "selling": 1}
        if master_type == "mode_of_payment":
            return {"name": name, "account": payload["account"]}
        if master_type == "supplier_group":
            return {"name": name}
        if master_type == "uom":
            return {"name": name}
        if master_type == "practitioner":
            return {
                "name": name,
                "practitioner_name": payload["practitioner_name"],
                "user_id": payload["user"],
            }
        return {
            "name": name,
            "item_code": payload["item_code"],
            "item": payload["item_code"],
            "template": payload["template_name"],
            "description": payload["template_name"],
        }

    def lookup_admin_receipt(self, actor, kind, operation_key):
        value = self.admin_receipts.get((actor, kind, operation_key))
        if isinstance(value, Exception):
            raise value
        if value is not None:
            return value
        for record in self.admin_receipt_records:
            if (
                record.actor == actor
                and record.kind == kind
                and record.operation_key == operation_key
            ):
                return record.receipt
        return None

    def list_committed_admin_receipts(self, limit, kind=None, operation_key=None):
        self.admin_receipt_calls.append((limit, kind, operation_key))
        if self.admin_receipt_list_error is not None:
            raise self.admin_receipt_list_error
        from clinic_ui.core import admin_contract

        records = [
            record
            for record in self.admin_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise admin_contract.AdminBudgetExhausted()
        return records

    def acknowledge_admin_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.admin_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.admin_ack_error is not None:
            raise self.admin_ack_error
        return self.admin_ack_status

    def admin_write_fence(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("admin_fence")
        self.admin_fence_calls.append((actor, kind, operation_key, payload_digest))
        return FakeFence(self.admin_fence_claim, self.admin_fence_error)

    # -- stock receive/transfer/adjust surface -----------------------------

    def native_stock_boundary_problem(self):
        return self.stock_boundary_problem

    def native_stock_install_problem(self):
        return self.stock_install_problem

    def native_stock_can_create(self, doctype):
        return self.stock_create_authority.get(doctype, True)

    def _stock_detail(self, detail, payload):
        self.stock_detail_calls.append(dict(payload))
        if self.stock_detail_sequence:
            return dict(self.stock_detail_sequence.pop(0))
        return None if detail is None else dict(detail)

    def native_stock_receive_detail(self, payload):
        return self._stock_detail(self.stock_receive_detail, payload)

    def native_stock_transfer_detail(self, payload):
        return self._stock_detail(self.stock_transfer_detail, payload)

    def native_stock_adjust_detail(self, payload):
        return self._stock_detail(self.stock_adjust_detail, payload)

    def native_stock_baseline(self, kind, warehouses, item_code):
        self.stock_baseline_calls.append((kind, tuple(warehouses), item_code))
        return dict(self.stock_baseline_value)

    def _stock_mutation(self, kind, calls, prepared, name):
        """Validate the prepared body exactly as the real gateway reads it.

        The real creators read ``prepared["company"]`` (server-resolved), the
        pinned posting fields and the kind's own route; a missing key there is a
        KeyError inside the fence. Asserting the same contract here keeps the
        fake from hiding that class of drift.
        """

        from clinic_ui.core import stock_contract

        problem = stock_contract.prepared_problem(kind, prepared)
        if problem is not None:
            raise AssertionError(problem)
        if not isinstance(prepared.get("company"), str) or not prepared[
            "company"
        ].strip():
            raise AssertionError("prepared body has no company")
        if self.stock_mutation_error is not None:
            raise self.stock_mutation_error
        calls.append(dict(prepared))
        return name

    def create_native_stock_receipt(self, prepared):
        return self._stock_mutation(
            "stock_receive",
            self.stock_receipt_calls,
            prepared,
            self.stock_receipt_id,
        )

    def create_native_stock_transfer(self, prepared):
        return self._stock_mutation(
            "stock_transfer",
            self.stock_transfer_calls,
            prepared,
            self.stock_transfer_id,
        )

    def create_native_stock_reconciliation(self, prepared):
        return self._stock_mutation(
            "stock_adjust",
            self.stock_reconciliation_calls,
            prepared,
            self.stock_reconciliation_id,
        )

    def _stock_side_effect_facts(
        self, reference, expected, baseline, doctype
    ):
        self.stock_facts_calls.append((reference, expected, baseline))
        if self.stock_facts:
            return dict(self.stock_facts)
        return {
            "docstatus": 1,
            "doctype": doctype,
            "sle_rows": expected["sle_rows"],
            "gl_rows": expected["gl_rows_max"],
            "bins": dict(expected["bins"]),
        }

    def native_stock_receipt_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(
            reference, expected, baseline, "Purchase Receipt"
        )

    def native_stock_transfer_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(
            reference, expected, baseline, "Stock Entry"
        )

    def native_stock_adjust_facts(self, reference, expected, baseline):
        return self._stock_side_effect_facts(
            reference, expected, baseline, "Stock Reconciliation"
        )

    def list_committed_stock_receipts(self, limit, kind=None, operation_key=None):
        self.stock_receipt_list_calls.append((limit, kind, operation_key))
        if self.stock_receipt_list_error is not None:
            raise self.stock_receipt_list_error
        from clinic_ui.core import stock_contract

        records = [
            record
            for record in self.stock_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise stock_contract.StockBudgetExhausted()
        return records

    def acknowledge_stock_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.stock_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.stock_ack_error is not None:
            raise self.stock_ack_error
        return self.stock_ack_status

    def stock_write_fence(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("stock_fence")
        self.stock_fence_calls.append((actor, kind, operation_key, payload_digest))
        return FakeFence(self.stock_fence_claim, self.stock_fence_error)

    # -- purchasing (PO draft/submit + receipt) surface --------------------

    def native_purchase_boundary_problem(self):
        return self.purchase_boundary_problem

    def native_purchase_install_problem(self):
        return self.purchase_install_problem

    def native_purchase_draft_install_problem(self):
        return self.purchase_draft_install_problem

    def native_purchase_can_create(self, doctype):
        return self.purchase_create_authority.get(doctype, True)

    def _purchase_detail(self, detail, payload):
        self.purchase_detail_calls.append(dict(payload))
        if self.purchase_detail_sequence:
            return dict(self.purchase_detail_sequence.pop(0))
        return None if detail is None else dict(detail)

    def native_purchase_order_draft_detail(self, payload):
        return self._purchase_detail(self.purchase_draft_detail, payload)

    def native_purchase_order_view(self, purchase_order_id):
        self.purchase_order_view_calls.append(purchase_order_id)
        if self.purchase_order_view_missing:
            return None
        if self.purchase_order_view_value is not None:
            return dict(self.purchase_order_view_value)
        return {
            "purchase_order_id": purchase_order_id,
            "status": "Draft",
            "docstatus": 0,
        }

    def native_purchase_order_drafts(self, supplier, limit):
        self.purchase_order_drafts_calls.append((supplier, limit))
        if self.purchase_order_drafts_error is not None:
            raise self.purchase_order_drafts_error
        if self.purchase_draft_names is None:
            return None
        return list(self.purchase_draft_names)


    def native_purchase_order_submit_detail(self, payload):
        return self._purchase_detail(self.purchase_submit_detail, payload)

    def native_purchase_receipt_detail(self, payload):
        return self._purchase_detail(self.purchase_receipt_detail, payload)

    def native_purchase_baseline(
        self, kind, warehouses, item_code, purchase_order=None
    ):
        self.purchase_baseline_calls.append(
            (kind, tuple(warehouses), item_code, purchase_order)
        )
        return dict(self.purchase_baseline_value)
    def _purchase_mutation(self, kind, calls, prepared, name):
        """Validate the prepared body exactly as the real gateway reads it.

        The real creators read ``prepared["company"]`` and
        ``prepared["currency"]`` (both server-resolved) plus the kind's own
        route; a missing key there is a KeyError inside the fence. Asserting the
        same contract here keeps the fake from hiding that class of drift.
        """

        from clinic_ui.core import purchase_contract

        problem = purchase_contract.prepared_problem(kind, prepared)
        if problem is not None:
            raise AssertionError(problem)
        for field in ("company", "currency"):
            if not isinstance(prepared.get(field), str) or not prepared[
                field
            ].strip():
                raise AssertionError(f"prepared body has no {field}")
        if kind == "purchase_order_draft" and prepared.get("docstatus") is not None:
            raise AssertionError("a draft may not carry a docstatus")
        if self.purchase_mutation_error is not None:
            raise self.purchase_mutation_error
        calls.append(dict(prepared))
        return name

    def create_native_purchase_order_draft(self, prepared):
        return self._purchase_mutation(
            "purchase_order_draft",
            self.purchase_draft_calls,
            prepared,
            self.purchase_draft_id,
        )

    def submit_native_purchase_order(self, prepared):
        return self._purchase_mutation(
            "purchase_order_submit",
            self.purchase_submit_order_calls,
            prepared,
            self.purchase_submit_id,
        )

    def create_native_purchase_receipt(self, prepared):
        return self._purchase_mutation(
            "purchase_receipt",
            self.purchase_receipt_calls,
            prepared,
            self.purchase_receipt_id,
        )

    def _purchase_side_effect_facts(
        self, reference, expected, baseline, doctype
    ):
        self.purchase_facts_calls.append((reference, expected, baseline))
        if self.purchase_facts:
            return dict(self.purchase_facts)
        return {
            "docstatus": expected["docstatus"],
            "doctype": doctype,
            "status": expected.get("status"),
            "sle_rows": expected["sle_rows"],
            "gl_rows": expected["gl_rows_max"],
            "bins": dict(expected["bins"]),
            "po_received_delta": expected.get("po_received_delta"),
            "po_status": expected.get("po_status"),
        }

    def native_purchase_order_facts(self, reference, expected, baseline):
        return self._purchase_side_effect_facts(
            reference, expected, baseline, "Purchase Order"
        )

    def native_purchase_receipt_facts(self, reference, expected, baseline):
        return self._purchase_side_effect_facts(
            reference, expected, baseline, "Purchase Receipt"
        )

    def list_committed_purchase_receipts(
        self, limit, kind=None, operation_key=None
    ):
        self.purchase_receipt_list_calls.append((limit, kind, operation_key))
        if self.purchase_receipt_list_error is not None:
            raise self.purchase_receipt_list_error
        from clinic_ui.core import purchase_contract

        records = [
            record
            for record in self.purchase_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise purchase_contract.PurchaseBudgetExhausted()
        return records

    def acknowledge_purchase_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.purchase_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.purchase_ack_error is not None:
            raise self.purchase_ack_error
        return self.purchase_ack_status

    def purchase_write_fence(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("purchase_fence")
        self.purchase_fence_calls.append(
            (actor, kind, operation_key, payload_digest)
        )
        return FakeFence(
            self.purchase_fence_claim, self.purchase_fence_error
        )

    # -- finance (invoice submit + payment receive) surface -----------------
    def native_finance_boundary_problem(self):
        return self.finance_boundary_problem

    def native_finance_install_problem(self):
        return self.finance_install_problem

    def native_finance_can_write(self, doctype):
        return self.finance_create_authority.get(doctype, True)

    def native_finance_can_submit(self, doctype):
        return self.finance_submit_authority.get(doctype, True)

    def native_finance_invoice_view(self, invoice_id):
        self.finance_invoice_view_calls.append(invoice_id)
        if self.finance_invoice_view_missing:
            return None
        if self.finance_invoice_view_value is not None:
            return dict(self.finance_invoice_view_value)
        return {
            "invoice_id": invoice_id,
            "status": "Unpaid",
            "docstatus": 1,
        }

    def native_finance_invoice_posted_facts(self, reference, expected):
        self.finance_posted_facts_calls.append((reference, dict(expected)))
        if self.finance_posted_facts_error is not None:
            raise self.finance_posted_facts_error
        if self.finance_posted_facts is not None:
            facts = dict(self.finance_posted_facts)
        else:
            facts = {
                "doctype": "Sales Invoice",
                "docstatus": 1,
                "status": "Unpaid",
                "currency": expected.get("currency"),
                "grand_total": expected.get("grand_total"),
                "net_total": expected.get("net_total"),
                "outstanding_amount": expected.get("grand_total"),
                "gl_rows": expected.get("gl_rows"),
                "debit_total": expected.get("debit_total"),
                "credit_total": expected.get("credit_total"),
                "unexpected_gl_rows": expected.get("unexpected_gl_rows"),
                "sle_rows": None,
                "sle_verified": False,
                "update_stock": 0,
                "ple_rows": 1,
            }
        facts.setdefault(
            "gl_accounts",
            {
                "debit": {
                    expected.get("debit_account"): expected.get("grand_total")
                },
                "credit": dict(expected.get("credit_by_account") or {}),
            },
        )
        return facts

    def native_finance_invoice_names(self, customer, limit):
        self.finance_invoice_list_calls.append((customer, limit))
        if self.finance_invoice_list_error is not None:
            raise self.finance_invoice_list_error
        if self.finance_invoice_names is None:
            return None
        return list(self.finance_invoice_names)

    def finance_invoice_detail_default(self):
        return {
            "today": "2026-11-16",
            "company": "SIT Synthetic Company USD",
            "currency": "USD",
            "customer_exists": True,
            "price_list": {"exists": True, "selling": True, "enabled": True},
            "prices": {"ITEM-1": 10.0},
            "items": {
                "ITEM-1": {
                    "exists": True,
                    "is_sales_item": True,
                    "stock_uom": "Unit",
                }
            },
            "receivable_account": "Accounts Receivable - SITCO",
            "income_accounts": {"ITEM-1": "Sales - SITCO"},
            "cost_center": "Main - SITCO",
        }

    def _finance_detail(self, detail, payload):
        self.finance_detail_calls.append(dict(payload))
        if self.finance_detail_sequence:
            return dict(self.finance_detail_sequence.pop(0))
        return None if detail is None else dict(detail)

    def native_finance_invoice_detail(self, payload, discount_enabled=False):
        detail = self.finance_invoice_detail
        if detail is None and not self.finance_detail_sequence:
            detail = self.finance_invoice_detail_default()
        detail = self._finance_detail(detail, payload)
        if detail is None:
            return None
        detail["discount_available"] = False
        return detail

    def native_finance_payment_detail(self, payload):
        return self._finance_detail(self.finance_payment_detail, payload)

    def native_finance_baseline(self, kind, invoice=None):
        self.finance_baseline_calls.append((kind, invoice))
        return dict(self.finance_baseline_value)

    def _finance_mutation(self, kind, calls, prepared, name):
        """Validate the prepared body exactly as the real gateway reads it.

        The real creators read ``prepared["company"]``, ``prepared["currency"]``,
        the pinned ``update_stock`` and the kind's own route; a missing key there
        is a KeyError inside the fence. Asserting the same contract here keeps
        the fake from hiding that class of drift (the task-6 B1 class).
        """
        from clinic_ui.core import finance_contract

        problem = finance_contract.prepared_problem(kind, prepared)
        if problem is not None:
            raise AssertionError(problem)
        for field in ("company", "currency"):
            if not isinstance(prepared.get(field), str) or not prepared[
                field
            ].strip():
                raise AssertionError(f"prepared body has no {field}")
        if self.finance_mutation_error is not None:
            raise self.finance_mutation_error
        calls.append(dict(prepared))
        return name

    def create_and_submit_native_sales_invoice(self, prepared):
        return self._finance_mutation(
            "invoice_submit",
            self.finance_invoice_calls,
            prepared,
            self.finance_invoice_id,
        )

    def create_native_payment_entry(self, prepared):
        return self._finance_mutation(
            "payment_receive",
            self.finance_payment_calls,
            prepared,
            self.finance_payment_id,
        )

    def _finance_facts_from_expected(self, doctype, expected):
        """Derive facts the way the real readers classify them.

        The real gateway classifies every posted GL row by account against the
        EXPECTED's server-resolved receivable/income (invoice) or
        paid_to/paid_from (payment) accounts. Echoing EXPECTED here would let an
        account-key drift pass the whole suite while the real re-read marks
        every row unexpected (the task-6 B1 class, facts direction), so the fake
        builds the rows the controller posts and classifies them through the
        contract's own rule.
        """

        from clinic_ui.core import finance_contract

        if doctype == finance_contract.INVOICE_DOCTYPE:
            debit_account = expected.get("debit_account")
            credit_accounts = list(expected.get("credit_accounts") or ())
            if not isinstance(debit_account, str) or not debit_account.strip():
                raise AssertionError(
                    "finance facts expected a server-resolved debit account"
                )
            if not credit_accounts:
                raise AssertionError(
                    "finance facts expected server-resolved credit accounts"
                )
            rows = [
                {
                    "account": debit_account,
                    "debit": expected["debit_total"],
                    "credit": 0.0,
                }
            ]
            rows_left = int(expected["gl_rows"]) - 1
            for index in range(rows_left):
                rows.append(
                    {
                        "account": credit_accounts[
                            index % len(credit_accounts)
                        ],
                        "debit": 0.0,
                        "credit": (
                            expected["credit_total"]
                            if index == rows_left - 1
                            else 0.0
                        ),
                    }
                )
            debit_total, credit_total, unexpected = (
                finance_contract.invoice_gl_sums(rows, expected)
            )
            if (
                unexpected != expected["unexpected_gl_rows"]
                or debit_total != expected["debit_total"]
                or credit_total != expected["credit_total"]
                or len(rows) != expected["gl_rows"]
            ):
                raise AssertionError(
                    "finance facts drift on the invoice GL accounts"
                )
            return {
                "doctype": doctype,
                "docstatus": expected["docstatus"],
                "status": expected["status"],
                "currency": expected["currency"],
                "grand_total": expected["grand_total"],
                "net_total": expected["net_total"],
                "gl_rows": len(rows),
                "debit_total": debit_total,
                "credit_total": credit_total,
                "sle_rows": expected["sle_rows"],
                "ple_rows": expected["ple_rows"],
                "unexpected_gl_rows": unexpected,
                "outstanding_amount": expected["grand_total"],
            }

        paid_to = expected.get("paid_to_account")
        paid_from = expected.get("paid_from_account")
        for field, account in (
            ("paid_to", paid_to),
            ("paid_from", paid_from),
        ):
            if not isinstance(account, str) or not account.strip():
                raise AssertionError(
                    f"finance facts expected a server-resolved {field} account"
                )
        rows = [
            {
                "account": paid_to,
                "debit": expected["allocated"],
                "credit": 0.0,
            },
            {
                "account": paid_from,
                "debit": 0.0,
                "credit": expected["allocated"],
            },
        ]
        if (
            len(rows) != expected["gl_rows"]
            or finance_contract.payment_gl_unexpected(rows, expected)
            != expected["unexpected_gl_rows"]
        ):
            raise AssertionError(
                "finance facts drift on the payment GL accounts"
            )
        return {
            "doctype": doctype,
            "docstatus": expected["docstatus"],
            "gl_rows": len(rows),
            "ple_rows": expected["ple_rows"],
            "unexpected_gl_rows": expected["unexpected_gl_rows"],
            "allocated": expected["allocated"],
            "outstanding_delta": expected["outstanding_delta"],
            "outstanding_after": expected["outstanding_after"],
            "invoice_status": expected["status"]
            or expected.get("unchanged_status"),
        }

    def _finance_side_effect_facts(self, reference, expected, baseline, doctype):
        self.finance_facts_calls.append((reference, expected, baseline))
        if self.finance_facts:
            return dict(self.finance_facts)
        return self._finance_facts_from_expected(doctype, expected)

    def native_finance_invoice_facts(self, reference, expected, baseline):
        return self._finance_side_effect_facts(
            reference, expected, baseline, "Sales Invoice"
        )

    def native_finance_payment_facts(self, reference, expected, baseline):
        return self._finance_side_effect_facts(
            reference, expected, baseline, "Payment Entry"
        )

    def list_committed_finance_receipts(
        self, limit, kind=None, operation_key=None
    ):
        self.finance_receipt_list_calls.append((limit, kind, operation_key))
        if self.finance_receipt_list_error is not None:
            raise self.finance_receipt_list_error
        from clinic_ui.core import finance_contract

        records = [
            record
            for record in self.finance_receipt_records
            if (kind is None or record.kind == kind)
            and (operation_key is None or record.operation_key == operation_key)
        ]
        if len(records) > limit:
            raise finance_contract.FinanceBudgetExhausted()
        return records

    def acknowledge_finance_write(
        self, secret, actor, kind, operation_key, ack_token
    ):
        self.finance_ack_calls.append((actor, kind, operation_key, ack_token))
        if self.finance_ack_error is not None:
            raise self.finance_ack_error
        return self.finance_ack_status

    def finance_write_fence(self, actor, kind, operation_key, payload_digest):
        self.call_order.append("finance_fence")
        self.finance_fence_calls.append((actor, kind, operation_key, payload_digest))
        return FakeFence(self.finance_fence_claim, self.finance_fence_error)


class FakeFinanceReceipt:
    """One committed finance row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt



class FakePurchaseReceipt:
    """One committed purchasing row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


class FakeStockReceipt:
    """One committed stock row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


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

class FakeEncounterReceipt:
    """One committed encounter row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


class FakeEncounterOperation:
    """One operation's latest durable row: state, digest and receipt."""

    def __init__(self, state, payload_digest, receipt=None):
        self.state = state
        self.payload_digest = payload_digest
        self.receipt = receipt


class FakeProcedureReceipt:
    """One committed procedure row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt


class FakeAdminReceipt:
    """One committed admin row as the gateway would enumerate it."""

    def __init__(self, kind, actor, operation_key, receipt):
        self.kind = kind
        self.actor = actor
        self.operation_key = operation_key
        self.receipt = receipt
