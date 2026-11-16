"""Adapter tests for ``clinic_ui.runtime.frappe_gateway``.

A minimal fake Frappe module is injected, so these tests exercise the real
adapter code paths without an installed Frappe, a site or the network. They
prove the dispatch mechanics (401 status, no-store header, generic denial,
site-config source and per-patient authority) — not real permission semantics.
"""

import json
import sys
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from clinic_ui.core import (
    appointment_contract,
    appointment_write,
    config,
    deployment_marker,
    fence_store,
    patient_write,
    write_fence,
)
from clinic_ui.runtime import frappe_gateway


class FakePermissionError(Exception):
    pass


class FakeDB:
    def __init__(self, values=None):
        self.values = dict(values or {})
        self.calls = []
        self.existing = set()
        self.sql_rows = []
        self.sql_calls = []
        self.read_calls = []
        self.commits = 0
        self.rollbacks = 0
        self.single_values = {}

    def get_value(
        self,
        doctype,
        name,
        fieldname=None,
        as_dict=False,
        for_update=False,
        cache=False,
    ):
        self.calls.append((doctype, name, fieldname))
        self.read_calls.append(
            (doctype, name, fieldname, for_update, cache)
        )
        if isinstance(fieldname, (list, tuple)):
            row = {
                field: self.values.get((doctype, name, field))
                for field in fieldname
            }
            if as_dict:
                return row
            return tuple(row[field] for field in fieldname)
        key = (doctype, name, fieldname)
        return self.values.get(key)

    def get_single_value(self, doctype, fieldname):
        return self.single_values.get((doctype, fieldname))

    def set_single_value(self, doctype, fieldname, value):
        self.single_values[(doctype, fieldname)] = value

    def exists(self, doctype, name):
        return (doctype, name) in self.existing

    def sql(self, query, values=None, as_dict=False):
        self.sql_calls.append((query, values, as_dict))
        return list(self.sql_rows)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeField:
    def __init__(
        self, permlevel, fieldname=None, fieldtype="Data", options=None
    ):
        self.permlevel = permlevel
        self.fieldname = fieldname
        self.fieldtype = fieldtype
        self.options = options


class FakeMeta:
    """Minimal stand-in for ``frappe.model.meta.Meta`` field-level checks.

    Mirrors the verified v16 API: ``Meta.get_permlevel_access(permission_type,
    parenttype=None, *, user=None)`` returns the permlevels the user's roles can
    read, and ``Meta.get_field(fieldname).permlevel`` carries the field level.
    """

    def __init__(self):
        # Actual Frappe Meta.fields contains DocFields only; ``modified`` is a
        # standard default field and must not be present here.
        self.permlevels = {
            field: 0
            for field in (
                frappe_gateway.PATIENT_DOCFIELD_FIELDS
                + frappe_gateway.APPOINTMENT_CONTRACT_FIELDS
                + (
                    "default_duration",
                    "allow_booking_for",
                )
            )
        }
        # Actual DocField metadata for the appointment safety reads: the patient,
        # company, medical department, appointment type and patient-derived
        # Gender links are real Link fields; the rest are plain columns.
        self.link_options = {
            "patient": "Patient",
            "company": "Company",
            "department": "Medical Department",
            "appointment_type": "Appointment Type",
            "patient_sex": "Gender",
        }
        self.fieldtypes = {"default_duration": "Int"}
        self.accessible = [0]
        self.permlevel_calls = []
        self.field_calls = []
        self.masked = []
        self.masked_calls = 0

    def get_permlevel_access(
        self, permission_type="read", parenttype=None, *, user=None
    ):
        self.permlevel_calls.append((permission_type, parenttype, user))
        return list(self.accessible)

    def get_masked_fields(self, parenttype=None):
        # Actual v16 API returns field-definition copies for the current
        # session user (frappe/model/meta.py:195-214).
        self.masked_calls += 1
        return [
            FakeField(permlevel=0, fieldname=name) for name in self.masked
        ]

    def get_field(self, fieldname):
        self.field_calls.append(fieldname)
        if fieldname not in self.permlevels:
            return None
        options = self.link_options.get(fieldname)
        return FakeField(
            self.permlevels[fieldname],
            fieldname=fieldname,
            fieldtype="Link" if options else self.fieldtypes.get(
                fieldname, "Data"
            ),
            options=options,
        )


class FakeDocument:
    def __init__(self, doctype, name=None, before_insert_defaults=None):
        self.doctype = doctype
        self.name = name
        self.inserted = False
        self.saved = False
        self.before_insert_calls = 0
        self.before_insert_defaults = dict(before_insert_defaults or {})

    def before_insert(self):
        self.before_insert_calls += 1
        for field, value in self.before_insert_defaults.items():
            setattr(self, field, value)

    def insert(self):
        self.inserted = True
        if self.name is None:
            self.name = "PAT-NEW"

    def save(self):
        self.saved = True


class FakeFrappe(types.SimpleNamespace):
    def __init__(self, site="marley.test", site_path=None, session=None):
        super().__init__()
        self.local = types.SimpleNamespace(
            site=site,
            session={},
            response={},
            response_headers={},
            flags=types.SimpleNamespace(),
        )
        self.session = session or types.SimpleNamespace(user="Guest", sid="Guest")
        self.db = FakeDB()
        self.meta = FakeMeta()
        self.meta_doctypes = []
        self.PermissionError = FakePermissionError
        self.permission_calls = []
        self._site_path = site_path
        self.roles = ()
        self.new_docs = []
        self.docs = {}
        self.new_doc_defaults = {}
        self.before_insert_defaults = {}
        self.appointment_boundary_snapshot = (
            appointment_contract.reviewed_boundary_snapshot()
        )

    def get_meta(self, doctype):
        self.meta_doctypes.append(doctype)
        return self.meta

    def get_site_path(self, *parts):
        if self._site_path is None:
            raise FileNotFoundError("no site path")
        return str(Path(self._site_path).joinpath(*parts))

    def new_doc(self, doctype):
        doc = FakeDocument(
            doctype, before_insert_defaults=self.before_insert_defaults
        )
        for field, value in self.new_doc_defaults.items():
            setattr(doc, field, value)
        self.new_docs.append(doc)
        return doc

    def get_doc(self, doctype, name, **kwargs):
        self.get_doc_calls = getattr(self, "get_doc_calls", [])
        self.get_doc_calls.append((doctype, name, kwargs))
        return self.docs[(doctype, name)]

    def get_roles(self, user=None):
        return list(self.roles)

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        self.permission_calls.append((doctype, ptype, doc, user))
        key = getattr(doc, "name", doc)
        return bool(self.db.values.get(("__perm__", key, ptype), False))


class GatewayTestCase(unittest.TestCase):
    def gateway(self, stub):
        return frappe_gateway.Gateway(frappe_module=stub)


class AnonymousResponseAdapterTests(GatewayTestCase):
    def test_respond_anonymous_sets_401_and_returns_a_fixed_code(self):
        stub = FakeFrappe()
        gateway = self.gateway(stub)

        payload = gateway.respond_anonymous()

        self.assertEqual(payload, {"code": "AUTH_EXPIRED"})
        self.assertEqual(stub.local.response["http_status_code"], 401)
        self.assertEqual(list(stub.local.response), ["http_status_code"])

    def test_mark_no_store_sets_the_header(self):
        stub = FakeFrappe()
        self.gateway(stub).mark_no_store()
        self.assertEqual(stub.local.response_headers["Cache-Control"], "no-store")

    def test_deny_raises_a_generic_permission_error(self):
        stub = FakeFrappe()
        with self.assertRaises(FakePermissionError) as raised:
            self.gateway(stub).deny()
        self.assertEqual(str(raised.exception), "Not permitted")

    def test_deny_sets_flag_before_raising(self):
        stub = FakeFrappe()
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).deny()
        self.assertTrue(stub.local.flags.disable_traceback)

    def test_deny_clears_error_message_channels_before_raising(self):
        stub = FakeFrappe()
        stub.local.message_log = ["sensitive throw message"]
        stub.local.flags.error_message = "sensitive error message"
        stub.local.response = {
            "_server_messages": '["sensitive"]',
            "_error_message": "sensitive",
            "exc": "traceback",
        }
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).deny()
        self.assertEqual(stub.local.message_log, [])
        self.assertIsNone(stub.local.flags.error_message)
        self.assertEqual(stub.local.response, {})

    def test_suppress_traceback_sets_the_frappe_flag(self):
        stub = FakeFrappe()
        self.gateway(stub).suppress_traceback()
        self.assertTrue(stub.local.flags.disable_traceback)

    def test_suppress_traceback_without_flags_or_frappe_is_safe(self):
        stub = FakeFrappe()
        del stub.local.flags
        self.gateway(stub).suppress_traceback()

        gateway = frappe_gateway.Gateway(frappe_module=types.SimpleNamespace())
        gateway.suppress_traceback()

    def test_guest_session_is_reported_without_identity_or_db_reads(self):
        stub = FakeFrappe()
        view = self.gateway(stub).current_session
        self.assertEqual(view.user, "Guest")
        self.assertEqual(view.full_name, "")
        self.assertFalse(view.enabled)
        self.assertEqual(view.roles, ())
        self.assertEqual(stub.db.calls, [])


class NativePatientViewAdapterTests(GatewayTestCase):
    FIELDS = (
        "patient_name",
        "first_name",
        "last_name",
        "sex",
        "dob",
        "status",
        "modified",
    )

    def stub_with_patient(self, permitted):
        import datetime

        stub = FakeFrappe(
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            )
        )
        stub.db.values[("__perm__", "PAT-1", "read")] = permitted
        stub.db.values[("Patient", "PAT-1", "patient_name")] = "Patient One"
        stub.db.values[("Patient", "PAT-1", "first_name")] = "Patient"
        stub.db.values[("Patient", "PAT-1", "last_name")] = "One"
        stub.db.values[("Patient", "PAT-1", "sex")] = "Female"
        stub.db.values[("Patient", "PAT-1", "dob")] = datetime.date(1990, 1, 2)
        stub.db.values[("Patient", "PAT-1", "status")] = "Active"
        stub.db.values[("Patient", "PAT-1", "modified")] = (
            "2026-11-14T09:30:00.123456"
        )
        return stub

    def test_authorized_patient_returns_only_allowlisted_fields(self):
        stub = self.stub_with_patient(True)
        detail = self.gateway(stub).native_patient_view("PAT-1")
        self.assertEqual(
            detail,
            {
                "patient_name": "Patient One",
                "first_name": "Patient",
                "last_name": "One",
                "sex": "Female",
                "dob": "1990-01-02",
                "status": "Active",
                "modified": "2026-11-14T09:30:00.123456",
            },
        )
        self.assertEqual(
            stub.permission_calls, [("Patient", "read", "PAT-1", "doctor@example.test")]
        )
        self.assertEqual(stub.meta_doctypes, ["Patient"])
        self.assertEqual(
            stub.meta.permlevel_calls,
            [("read", None, "doctor@example.test")],
        )
        self.assertEqual(stub.meta.masked_calls, 1)
        self.assertEqual(
            stub.db.calls,
            [("Patient", "PAT-1", field) for field in self.FIELDS],
        )

    def test_masked_field_fails_closed_before_any_field_read(self):
        stub = self.stub_with_patient(True)
        stub.meta.masked = ["sex"]

        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))

        self.assertEqual(stub.meta.masked_calls, 1)
        self.assertEqual(stub.db.calls, [])

    def test_mask_read_failure_fails_closed_before_any_field_read(self):
        stub = self.stub_with_patient(True)

        def boom(*args, **kwargs):
            raise RuntimeError("masked fields unavailable")

        stub.meta.get_masked_fields = boom
        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_restricted_docfield_fails_closed_before_any_field_read(self):
        stub = self.stub_with_patient(True)
        stub.meta.permlevels["sex"] = 1

        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))

        self.assertEqual(stub.db.calls, [])
        self.assertEqual(
            stub.permission_calls,
            [("Patient", "read", "PAT-1", "doctor@example.test")],
        )

    def test_modified_is_a_standard_field_with_document_permission_only(self):
        stub = self.stub_with_patient(True)
        detail = self.gateway(stub).native_patient_view("PAT-1")

        self.assertEqual(detail["modified"], "2026-11-14T09:30:00.123456")
        self.assertNotIn("modified", stub.meta.permlevels)
        self.assertNotIn("modified", stub.meta.field_calls)
        self.assertIn(("Patient", "PAT-1", "modified"), stub.db.calls)

    def test_no_accessible_permlevel_fails_closed_before_any_field_read(self):
        stub = self.stub_with_patient(True)
        stub.meta.accessible = []

        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_missing_meta_field_fails_closed_before_any_field_read(self):
        stub = self.stub_with_patient(True)
        del stub.meta.permlevels["dob"]

        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_meta_failure_fails_closed(self):
        stub = self.stub_with_patient(True)

        def boom(doctype):
            raise RuntimeError("meta unavailable")

        stub.get_meta = boom
        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_denied_patient_returns_none_without_reading_fields(self):
        stub = self.stub_with_patient(False)
        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_authority_failure_fails_closed(self):
        stub = self.stub_with_patient(True)

        def boom(*args, **kwargs):
            raise RuntimeError("permission engine unavailable")

        stub.has_permission = boom
        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))

    def test_field_read_failure_fails_closed(self):
        stub = self.stub_with_patient(True)

        def boom(*args, **kwargs):
            raise RuntimeError("database unavailable")

        stub.db.get_value = boom
        self.assertIsNone(self.gateway(stub).native_patient_view("PAT-1"))

    def test_blank_optional_field_is_reported_as_none(self):
        stub = self.stub_with_patient(True)
        stub.db.values[("Patient", "PAT-1", "last_name")] = ""
        stub.db.values[("Patient", "PAT-1", "sex")] = None
        detail = self.gateway(stub).native_patient_view("PAT-1")
        self.assertIsNone(detail["last_name"])
        self.assertIsNone(detail["sex"])
        self.assertEqual(detail["patient_name"], "Patient One")


class NativeAppointmentViewAdapterTests(GatewayTestCase):
    FIELDS = ("patient", "appointment_date", "appointment_time", "status")

    def stub_with_appointment(self, permitted):
        import datetime

        stub = FakeFrappe(
            session=types.SimpleNamespace(
                user="reception@example.test", sid="sid-abc123"
            )
        )
        stub.meta.permlevels = {
            field: 0 for field in frappe_gateway.APPOINTMENT_DOCFIELD_FIELDS
        }
        stub.db.values[("__perm__", "NATIVE-APPT-1", "read")] = permitted
        stub.db.values[("Patient Appointment", "NATIVE-APPT-1", "patient")] = (
            "PAT-1"
        )
        stub.db.values[
            ("Patient Appointment", "NATIVE-APPT-1", "appointment_date")
        ] = datetime.date(2026, 11, 20)
        stub.db.values[
            ("Patient Appointment", "NATIVE-APPT-1", "appointment_time")
        ] = datetime.timedelta(hours=9, minutes=30)
        stub.db.values[
            ("Patient Appointment", "NATIVE-APPT-1", "status")
        ] = "Open"
        return stub

    def test_authorized_appointment_returns_only_internal_fields(self):
        stub = self.stub_with_appointment(True)
        detail = self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        self.assertEqual(
            detail,
            {
                "patient": "PAT-1",
                "appointment_date": "2026-11-20",
                "appointment_time": "09:30:00",
                "status": "Open",
            },
        )
        self.assertEqual(
            stub.permission_calls,
            [
                (
                    "Patient Appointment",
                    "read",
                    "NATIVE-APPT-1",
                    "reception@example.test",
                )
            ],
        )
        self.assertEqual(stub.meta_doctypes, ["Patient Appointment"])
        self.assertEqual(
            stub.meta.permlevel_calls,
            [("read", None, "reception@example.test")],
        )
        self.assertEqual(stub.meta.masked_calls, 1)
        self.assertEqual(
            stub.db.calls,
            [
                ("Patient Appointment", "NATIVE-APPT-1", field)
                for field in self.FIELDS
            ],
        )

    def test_time_accepts_a_string_or_time_value(self):
        import datetime

        for value, expected in (
            ("09:30:00", "09:30:00"),
            (datetime.time(9, 30), "09:30:00"),
            (datetime.timedelta(hours=9, minutes=30), "09:30:00"),
        ):
            with self.subTest(value=value):
                stub = self.stub_with_appointment(True)
                stub.db.values[
                    ("Patient Appointment", "NATIVE-APPT-1", "appointment_time")
                ] = value
                detail = self.gateway(stub).native_appointment_view(
                    "NATIVE-APPT-1"
                )
                self.assertEqual(detail["appointment_time"], expected)

    def test_denied_document_returns_none_without_reading_fields(self):
        stub = self.stub_with_appointment(False)
        self.assertIsNone(
            self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        )
        self.assertEqual(stub.db.calls, [])

    def test_restricted_permlevel_fails_closed_before_any_field_read(self):
        stub = self.stub_with_appointment(True)
        stub.meta.permlevels["patient"] = 1
        self.assertIsNone(
            self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        )
        self.assertEqual(stub.db.calls, [])

    def test_masked_field_fails_closed_before_any_field_read(self):
        stub = self.stub_with_appointment(True)
        stub.meta.masked = ["status"]
        self.assertIsNone(
            self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        )
        self.assertEqual(stub.meta.masked_calls, 1)
        self.assertEqual(stub.db.calls, [])

    def test_missing_meta_field_fails_closed_before_any_field_read(self):
        stub = self.stub_with_appointment(True)
        del stub.meta.permlevels["appointment_time"]
        self.assertIsNone(
            self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        )
        self.assertEqual(stub.db.calls, [])

    def test_missing_or_malformed_time_fails_closed(self):
        import datetime

        for value in (None, "", "bad", datetime.timedelta(days=1)):
            with self.subTest(value=value):
                stub = self.stub_with_appointment(True)
                stub.db.values[
                    ("Patient Appointment", "NATIVE-APPT-1", "appointment_time")
                ] = value
                self.assertIsNone(
                    self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
                )

    def test_field_read_failure_fails_closed(self):
        stub = self.stub_with_appointment(True)

        def boom(*args, **kwargs):
            raise RuntimeError("database unavailable")

        stub.db.get_value = boom
        self.assertIsNone(
            self.gateway(stub).native_appointment_view("NATIVE-APPT-1")
        )


class SessionAdapterTests(GatewayTestCase):
    def test_live_session_is_mapped(self):
        stub = FakeFrappe(
            session=types.SimpleNamespace(user="nurse@example.test", sid="sid-abc123")
        )
        stub.db.values[("User", "nurse@example.test", "full_name")] = "Nurse Example"
        stub.db.values[("User", "nurse@example.test", "enabled")] = 1
        stub.roles = ("Nurse", "All")

        view = self.gateway(stub).current_session

        self.assertEqual(view.sid, "sid-abc123")
        self.assertEqual(view.user, "nurse@example.test")
        self.assertEqual(view.full_name, "Nurse Example")
        self.assertTrue(view.enabled)
        self.assertEqual(view.roles, ("Nurse", "All"))

    def test_missing_local_session_is_none(self):
        stub = FakeFrappe()
        stub.local.session = None
        self.assertIsNone(self.gateway(stub).current_session)


class SiteConfigSourceTests(GatewayTestCase):
    def test_values_come_from_the_sites_own_site_config(self):
        with TemporaryDirectory() as directory:
            Path(directory, "site_config.json").write_text(
                json.dumps({"clinic_ui_enabled": 1}), encoding="utf-8"
            )
            stub = FakeFrappe(site_path=directory)
            gateway = self.gateway(stub)
            self.assertEqual(gateway.setting("clinic_ui_enabled"), 1)
            self.assertIsNone(gateway.setting("absent_key"))

    def test_unreadable_configuration_yields_no_settings(self):
        stub = FakeFrappe(site_path=None)
        self.assertIsNone(self.gateway(stub).setting("clinic_ui_enabled"))


class CsrfAdapterTests(GatewayTestCase):
    def install_sessions(self, function):
        sessions = types.ModuleType("frappe.sessions")
        sessions.get_csrf_token = function
        stub = FakeFrappe()
        stub.sessions = sessions
        previous = sys.modules.get("frappe.sessions")
        previous_parent = sys.modules.get("frappe")
        sys.modules["frappe"] = stub
        sys.modules["frappe.sessions"] = sessions
        return stub, previous, previous_parent

    def restore(self, previous, previous_parent):
        for name, value in (
            ("frappe.sessions", previous),
            ("frappe", previous_parent),
        ):
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def test_token_is_returned(self):
        stub, previous, previous_parent = self.install_sessions(
            lambda: "csrf-token-value"
        )
        try:
            self.assertEqual(self.gateway(stub).csrf_token(), "csrf-token-value")
        finally:
            self.restore(previous, previous_parent)

    def test_absent_or_blank_token_fails_closed(self):
        for function in (lambda: None, lambda: "   ", lambda: 5):
            stub, previous, previous_parent = self.install_sessions(function)
            try:
                with self.subTest(function=function):
                    self.assertIsNone(self.gateway(stub).csrf_token())
            finally:
                self.restore(previous, previous_parent)

    def test_generation_failure_fails_closed(self):
        def boom():
            raise RuntimeError("session store unavailable")

        stub, previous, previous_parent = self.install_sessions(boom)
        try:
            self.assertIsNone(self.gateway(stub).csrf_token())
        finally:
            self.restore(previous, previous_parent)


class NativePatientAuthorityTests(GatewayTestCase):
    def stub_with_patient(self, permitted, display_name):
        stub = FakeFrappe(
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            )
        )
        stub.db.values[("__perm__", "PAT-1", "read")] = permitted
        stub.db.values[("Patient", "PAT-1", "patient_name")] = display_name
        return stub

    def test_authorized_patient_returns_display_name(self):
        stub = self.stub_with_patient(True, "Patient One")
        gateway = self.gateway(stub)
        self.assertEqual(gateway.native_patient_display_name("PAT-1"), "Patient One")
        self.assertEqual(
            stub.permission_calls, [("Patient", "read", "PAT-1", "doctor@example.test")]
        )
        self.assertEqual(
            stub.db.calls, [("Patient", "PAT-1", "patient_name")]
        )

    def test_denied_patient_returns_none_without_reading_the_record(self):
        stub = self.stub_with_patient(False, "Patient One")
        gateway = self.gateway(stub)
        self.assertIsNone(gateway.native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_readable_document_but_restricted_display_field_reads_no_value(self):
        stub = self.stub_with_patient(True, "Patient One")
        stub.meta.permlevels["patient_name"] = 1
        stub.meta.accessible = [0]
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])
        self.assertIn("patient_name", stub.meta.field_calls)

    def test_readable_document_but_masked_display_field_reads_no_value(self):
        stub = self.stub_with_patient(True, "Patient One")
        stub.meta.masked = ["patient_name"]
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_missing_display_field_metadata_fails_closed(self):
        stub = self.stub_with_patient(True, "Patient One")
        del stub.meta.permlevels["patient_name"]
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_unresolvable_mask_list_fails_closed(self):
        stub = self.stub_with_patient(True, "Patient One")

        def boom(*args, **kwargs):
            raise RuntimeError("mask list unavailable")

        stub.meta.get_masked_fields = boom
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))
        self.assertEqual(stub.db.calls, [])

    def test_authority_failure_fails_closed(self):
        stub = self.stub_with_patient(True, "Patient One")

        def boom(*args, **kwargs):
            raise RuntimeError("permission engine unavailable")

        stub.has_permission = boom
        self.assertIsNone(self.gateway(stub).native_patient_display_name("PAT-1"))


class WriteGateAdapterTests(GatewayTestCase):
    CSRF = "session-csrf-token"

    def stub(self, site=config.APPROVED_WRITE_SITE, site_path=None):
        stub = FakeFrappe(
            site=site,
            site_path=site_path,
            session=types.SimpleNamespace(
                user="doctor@example.test",
                sid="sid-abc123",
                data={"csrf_token": self.CSRF},
            ),
        )
        return stub

    def test_csrf_requires_the_initialized_session_token(self):
        gateway = self.gateway(self.stub())
        self.assertTrue(gateway.csrf_token_valid(self.CSRF))
        for supplied in (None, "", "wrong-token", 5):
            with self.subTest(supplied=supplied):
                self.assertFalse(gateway.csrf_token_valid(supplied))

    def test_csrf_missing_or_blank_session_token_fails_closed(self):
        for data in (None, {}, {"csrf_token": ""}, {"csrf_token": None}):
            with self.subTest(data=data):
                stub = self.stub()
                stub.session.data = data
                self.assertFalse(
                    self.gateway(stub).csrf_token_valid(self.CSRF)
                )

    def test_framework_ignore_csrf_and_allowed_referrer_do_not_bypass(self):
        stub = self.stub()
        stub.conf = types.SimpleNamespace(ignore_csrf=True)
        stub.is_allowed_referrer = lambda: True
        gateway = self.gateway(stub)
        self.assertFalse(gateway.csrf_token_valid("wrong-token"))
        self.assertFalse(gateway.csrf_token_valid(None))
        self.assertTrue(stub.conf.ignore_csrf)

    def test_healthcare_settings_side_effects_fail_closed(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        self.assertFalse(gateway.healthcare_settings_side_effects_unsafe())
        for field in (
            "collect_registration_fee",
            "send_registration_msg",
            "link_customer_to_patient",
        ):
            with self.subTest(field=field):
                stub = self.stub()
                stub.db.set_single_value("Healthcare Settings", field, 1)
                self.assertTrue(
                    self.gateway(stub).healthcare_settings_side_effects_unsafe()
                )

    def test_healthcare_settings_read_failure_is_unsafe(self):
        stub = self.stub()
        stub.db.get_single_value = lambda *args: (_ for _ in ()).throw(
            RuntimeError("settings unavailable")
        )
        self.assertTrue(
            self.gateway(stub).healthcare_settings_side_effects_unsafe()
        )

    def test_target_contact_customer_or_user_state_is_unsafe(self):
        for field in ("invite_user", "email", "mobile", "phone", "customer", "user_id"):
            with self.subTest(field=field):
                stub = self.stub()
                stub.db.values[("Patient", "PAT-1", field)] = "value"
                self.assertTrue(
                    self.gateway(stub).native_patient_target_unsafe("PAT-1")
                )
        stub = self.stub()
        for field in ("invite_user", "email", "mobile", "phone", "customer", "user_id"):
            stub.db.values[("Patient", "PAT-1", field)] = None
        self.assertFalse(
            self.gateway(stub).native_patient_target_unsafe("PAT-1")
        )

    def test_target_read_failure_or_missing_row_is_unsafe(self):
        stub = self.stub()
        stub.db.get_value = lambda *args, **kwargs: None
        self.assertTrue(
            self.gateway(stub).native_patient_target_unsafe("PAT-1")
        )
        stub = self.stub()
        stub.db.get_value = lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("database unavailable")
        )
        self.assertTrue(
            self.gateway(stub).native_patient_target_unsafe("PAT-1")
        )

    def test_write_deployment_requires_marker_bound_to_fence_anchor(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            info = fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            stub = self.stub(site_path=root)
            gateway = self.gateway(stub)
            self.assertFalse(gateway.write_deployment_approved())

            deployment_marker.write_marker(
                private, config.APPROVED_WRITE_SITE, info.deployment_id
            )
            self.assertTrue(gateway.write_deployment_approved())

    def test_write_deployment_refuses_a_wrong_deployment_or_site(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            stub = self.stub(site_path=root)
            gateway = self.gateway(stub)
            deployment_marker.write_marker(
                private, config.APPROVED_WRITE_SITE, "wrong-deployment"
            )
            self.assertFalse(gateway.write_deployment_approved())

        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            info = fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            stub = self.stub(site="other.local", site_path=root)
            deployment_marker.write_marker(
                private, config.APPROVED_WRITE_SITE, info.deployment_id
            )
            self.assertFalse(
                self.gateway(stub).write_deployment_approved()
            )

    def test_write_deployment_refuses_a_world_readable_marker(self):
        import os

        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            info = fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            stub = self.stub(site_path=root)
            deployment_marker.write_marker(
                private, config.APPROVED_WRITE_SITE, info.deployment_id
            )
            marker = Path(
                deployment_marker.marker_path(
                    private, config.APPROVED_WRITE_SITE
                )
            )
            os.chmod(marker, 0o644)
            self.assertFalse(
                self.gateway(stub).write_deployment_approved()
            )

    def test_write_fence_ready_requires_an_initialized_verified_store(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            stub = self.stub(site_path=root)
            gateway = self.gateway(stub)
            self.assertFalse(gateway.write_fence_ready())

            fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            self.assertTrue(self.gateway(stub).write_fence_ready())

    def test_write_fence_ready_fails_closed_on_a_corrupt_store(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            database = (
                private
                / fence_store.STORE_DIRNAME
                / "write-fence.sqlite3"
            )
            database.write_bytes(b"corrupt")
            stub = self.stub(site_path=root)
            self.assertFalse(self.gateway(stub).write_fence_ready())

    def _write_gateway_with_fence(self, root):
        private = Path(root) / "private"
        private.mkdir(mode=0o700)
        fence_store.initialize_store(
            private / fence_store.STORE_DIRNAME,
            private / fence_store.ANCHOR_FILENAME,
        )
        return self.gateway(self.stub(site_path=root))

    def test_write_fence_ready_false_when_a_key_is_dispatched(self):
        with TemporaryDirectory() as root:
            gateway = self._write_gateway_with_fence(root)
            ledger = gateway._ledger()
            with self.assertRaises(write_fence.UnterminatedFence):
                with ledger.fence(
                    actor="doctor@example.test",
                    kind="create",
                    operation_key="op-0123456789abcdef",
                    payload_digest="a" * 64,
                ):
                    pass
            self.assertFalse(self.gateway(self.stub(site_path=root)).write_fence_ready())

    def test_write_fence_ready_false_until_a_committed_write_is_acknowledged(self):
        with TemporaryDirectory() as root:
            gateway = self._write_gateway_with_fence(root)
            ledger = gateway._ledger()
            with ledger.fence(
                actor="doctor@example.test",
                kind="create",
                operation_key="op-0123456789abcdef",
                payload_digest="a" * 64,
            ) as claim:
                claim.commit("native-receipt")
            self.assertFalse(self.gateway(self.stub(site_path=root)).write_fence_ready())
            ledger.acknowledge_barrier(
                actor="doctor@example.test",
                kind="create",
                operation_key="op-0123456789abcdef",
                ack_receipt="ack:test",
            )
            self.assertTrue(self.gateway(self.stub(site_path=root)).write_fence_ready())

    def test_write_fence_ready_false_when_the_manual_latch_exists(self):
        with TemporaryDirectory() as root:
            gateway = self._write_gateway_with_fence(root)
            gateway._ledger().latch("manual investigation")
            self.assertFalse(self.gateway(self.stub(site_path=root)).write_fence_ready())

    def test_write_fence_ready_false_when_an_unknown_terminal_exists(self):
        with TemporaryDirectory() as root:
            gateway = self._write_gateway_with_fence(root)
            ledger = gateway._ledger()
            with ledger.fence(
                actor="doctor@example.test",
                kind="create",
                operation_key="op-0123456789abcdef",
                payload_digest="a" * 64,
            ) as claim:
                claim.unknown("not observed")
            self.assertFalse(self.gateway(self.stub(site_path=root)).write_fence_ready())

    def test_strip_message_channels_clears_success_channels(self):
        stub = self.stub()
        stub.local.message_log = ["queued"]
        stub.local.response.update(
            {
                "_server_messages": "[]",
                "_error_message": "secret",
                "messages": ["secret"],
                "errors": [{"message": "secret"}],
                "message": "keep",
            }
        )
        self.gateway(stub).strip_message_channels()
        self.assertEqual(stub.local.message_log, [])
        self.assertEqual(stub.local.response, {"message": "keep"})


class NativePatientWriteAdapterTests(GatewayTestCase):
    def doctor(self, site_path=None):
        stub = FakeFrappe(
            site_path=site_path,
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            ),
        )
        stub.db.values[("__perm__", None, "create")] = True
        stub.db.values[("__perm__", None, "write")] = True
        stub.db.values[("__perm__", None, "read")] = True
        stub.db.existing.add(("Gender", "Female"))
        return stub

    def test_create_sets_only_strict_fields_and_server_invite_flag(self):
        stub = self.doctor()
        gateway = self.gateway(stub)
        self.assertTrue(gateway.native_patient_create(
            {
                "first_name": "Patient",
                "last_name": "One",
                "sex": "Female",
                "dob": "1990-01-02",
            }
        ))
        doc = stub.new_docs[0]
        self.assertTrue(doc.inserted)
        self.assertEqual(doc.first_name, "Patient")
        self.assertEqual(doc.last_name, "One")
        self.assertEqual(doc.sex, "Female")
        self.assertEqual(doc.dob, "1990-01-02")
        self.assertEqual(doc.invite_user, 0)
        self.assertFalse(hasattr(doc, "status"))
        self.assertFalse(hasattr(doc, "patient_name"))
        self.assertFalse(hasattr(doc, "email"))

    def test_gender_check_requires_live_master_data(self):
        stub = self.doctor()
        gateway = self.gateway(stub)
        self.assertTrue(gateway.native_gender_exists("Female"))
        self.assertFalse(gateway.native_gender_exists("Male"))
        stub.db.values[("__perm__", None, "read")] = False
        self.assertFalse(gateway.native_gender_exists("Female"))

    def test_update_locks_and_compares_modified_before_applying_fields(self):
        stub = self.doctor()
        stub.db.values[("__perm__", "PAT-1", "write")] = True
        doc = FakeDocument("Patient", "PAT-1")
        stub.docs[("Patient", "PAT-1")] = doc
        stub.db.sql_rows = [{"modified": "2026-11-14 09:30:00.123456"}]
        gateway = self.gateway(stub)

        gateway.native_patient_update(
            "PAT-1", {"last_name": None}, "2026-11-14T09:30:00.123456"
        )

        self.assertTrue(doc.saved)
        self.assertIsNone(doc.last_name)
        self.assertEqual(doc.invite_user, 0)
        self.assertEqual(len(stub.db.sql_calls), 1)
        self.assertIn("FOR UPDATE", stub.db.sql_calls[0][0])

    def test_stale_update_changes_nothing(self):
        stub = self.doctor()
        stub.db.values[("__perm__", "PAT-1", "write")] = True
        doc = FakeDocument("Patient", "PAT-1")
        stub.docs[("Patient", "PAT-1")] = doc
        stub.db.sql_rows = [{"modified": "2026-11-14 09:30:00.123456"}]
        gateway = self.gateway(stub)

        with self.assertRaises(patient_write.StaleVersion):
            gateway.native_patient_update(
                "PAT-1", {"first_name": "Changed"}, "2026-11-14T09:30:01"
            )
        self.assertFalse(doc.saved)
        self.assertFalse(hasattr(doc, "first_name"))

    def test_exact_native_write_denial_is_a_permission_error(self):
        stub = self.doctor()
        stub.db.values[("__perm__", "PAT-1", "write")] = False
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).native_patient_update(
                "PAT-1", {"first_name": "Changed"}, "2026-11-14T09:30:00"
            )

    def test_respond_write_sets_status_and_returns_payload(self):
        stub = self.doctor()
        gateway = self.gateway(stub)
        payload = gateway.respond_write(409, {"code": "WRITE_LOCKED"})
        self.assertEqual(payload, {"code": "WRITE_LOCKED"})
        self.assertEqual(stub.local.response["http_status_code"], 409)
        self.assertEqual(
            stub.local.flags.clinic_ui_write_error_code, "WRITE_LOCKED"
        )
        gateway.respond_write(200, {"ok": True})
        self.assertEqual(stub.local.response["http_status_code"], 409)

    def test_commit_and_rollback_use_the_site_database(self):
        stub = self.doctor()
        gateway = self.gateway(stub)
        gateway.commit()
        gateway.rollback()
        self.assertEqual(stub.db.commits, 1)
        self.assertEqual(stub.db.rollbacks, 1)

    def test_fence_and_committed_receipt_round_trip(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            stub = self.doctor(site_path=root)
            gateway = self.gateway(stub)
            digest = "a" * 64
            key = "op-0123456789abcdef"

            with gateway.patient_write_fence(
                "doctor@example.test", "create", key, digest
            ) as claim:
                claim.commit("receipt-value")

            self.assertEqual(
                gateway.lookup_create_receipt("doctor@example.test", key),
                "receipt-value",
            )
            with self.assertRaises(patient_write.WriteLocked):
                with gateway.patient_write_fence(
                    "doctor@example.test", "create", key, digest
                ) as claim:
                    claim.commit("second")

    def test_site_wide_unresolved_claim_maps_to_write_locked(self):
        with TemporaryDirectory() as root:
            private = Path(root) / "private"
            private.mkdir(mode=0o700)
            fence_store.initialize_store(
                private / fence_store.STORE_DIRNAME,
                private / fence_store.ANCHOR_FILENAME,
            )
            gateway = self.gateway(self.doctor(site_path=root))
            ledger = gateway._ledger()
            with self.assertRaises(RuntimeError):
                with ledger.fence(
                    actor="doctor@example.test",
                    kind="create",
                    operation_key="op-0123456789abcdef",
                    payload_digest="a" * 64,
                ):
                    raise RuntimeError("lost before terminal")
            with self.assertRaises(patient_write.WriteLocked):
                with gateway.patient_write_fence(
                    "doctor@example.test",
                    "create",
                    "op-fedcba9876543210",
                    "b" * 64,
                ):
                    pass

    def test_prepare_rejects_default_trigger_fields_before_insert(self):
        fields = {
            "first_name": "Patient",
            "last_name": "One",
            "sex": "Female",
            "dob": "1990-01-02",
        }
        for field in frappe_gateway.PATIENT_CREATE_TRIGGER_FIELDS:
            with self.subTest(field=field):
                stub = self.doctor()
                stub.new_doc_defaults = {field: "injected"}
                with self.assertRaises(patient_write.UnsafeSideEffect):
                    self.gateway(stub).prepare_native_patient_create(fields)
                self.assertFalse(stub.new_docs[0].inserted)
                self.assertEqual(stub.db.commits, 0)

    def test_before_insert_default_trigger_is_rejected(self):
        fields = {
            "first_name": "Patient",
            "sex": "Female",
        }
        for field, value in (
            ("email", "x@example.test"),
            ("address_line1", "1 Default Street"),
            ("invite_user", 1),
        ):
            with self.subTest(field=field):
                stub = self.doctor()
                stub.before_insert_defaults = {field: value}
                with self.assertRaises(patient_write.UnsafeSideEffect):
                    self.gateway(stub).prepare_native_patient_create(fields)
                self.assertEqual(stub.new_docs[0].before_insert_calls, 1)
                self.assertFalse(stub.new_docs[0].inserted)

    def test_frappe_selling_and_currency_defaults_are_allowed(self):
        stub = self.doctor()
        stub.before_insert_defaults = {
            "customer_group": "Individual",
            "territory": "All Territories",
            "default_price_list": "Standard Selling",
            "default_currency": "USD",
            "language": "en",
        }
        fields = {"first_name": "Patient", "sex": "Female"}
        doc = self.gateway(stub).prepare_native_patient_create(fields)
        self.assertFalse(doc.inserted)
        self.assertEqual(doc.customer_group, "Individual")
        self.gateway(stub).insert_native_patient(doc)
        self.assertTrue(doc.inserted)
        self.assertEqual(stub.db.commits, 0)

    def test_prepare_never_inserts_and_insert_happens_once(self):
        stub = self.doctor()
        gateway = self.gateway(stub)
        doc = gateway.prepare_native_patient_create(
            {"first_name": "Patient", "sex": "Female"}
        )
        self.assertEqual(len(stub.new_docs), 1)
        self.assertFalse(doc.inserted)
        gateway.insert_native_patient(doc)
        self.assertTrue(doc.inserted)
        self.assertEqual(len(stub.new_docs), 1)
        self.assertEqual(doc.before_insert_calls, 2)

    def test_missing_store_fails_closed(self):
        with TemporaryDirectory() as root:
            stub = self.doctor(site_path=root)
            with self.assertRaises(patient_write.StoreUnavailable):
                self.gateway(stub).lookup_create_receipt(
                    "doctor@example.test", "op-0123456789abcdef"
                )


class ManualInvestigationLatchTests(GatewayTestCase):
    KEY = "op-0123456789abcdef"
    OTHER_KEY = "op-fedcba9876543210"

    def doctor(self, site_path=None):
        stub = FakeFrappe(
            site_path=site_path,
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            ),
        )
        stub.db.values[("__perm__", None, "create")] = True
        stub.db.values[("__perm__", None, "write")] = True
        stub.db.values[("__perm__", None, "read")] = True
        stub.db.existing.add(("Gender", "Female"))
        return stub

    def gateway_with_store(self, root):
        return self.gateway(self.doctor(site_path=root))

    def initialized_root(self):
        import tempfile

        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        private = Path(root) / "private"
        private.mkdir(mode=0o700)
        fence_store.initialize_store(
            private / fence_store.STORE_DIRNAME,
            private / fence_store.ANCHOR_FILENAME,
        )
        return root

    def commit_one(self, gateway, *, actor="doctor@example.test", key=KEY):
        with gateway.patient_write_fence(actor, "create", key, "a" * 64) as claim:
            claim.commit("native-receipt")

    def test_latch_blocks_a_new_actor_and_new_key_after_committed_work(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        self.assertTrue(
            gateway.latch_manual_investigation("post-commit response failed")
        )

        restarted = self.gateway_with_store(root)
        with self.assertRaises(patient_write.WriteLocked):
            with restarted.patient_write_fence(
                "other@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass
        self.assertIsNone(
            restarted._ledger().lookup(
                actor="other@example.test",
                kind="create",
                operation_key=self.OTHER_KEY,
            )
        )

    def test_latch_is_idempotent_and_survives_another_restart(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        self.assertTrue(gateway.latch_manual_investigation("first"))
        self.assertFalse(gateway.latch_manual_investigation("second"))

        for _ in range(2):
            restarted = self.gateway_with_store(root)
            with self.assertRaises(patient_write.WriteLocked):
                with restarted.patient_write_fence(
                    "doctor@example.test", "create", self.OTHER_KEY, "b" * 64
                ):
                    pass

    def test_operation_fallback_latch_preserves_receipt_and_blocks_new_key(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        self.assertTrue(
            gateway.latch_operation_uncertain(
                "doctor@example.test",
                "create",
                self.KEY,
                "reserved latch unavailable",
            )
        )
        self.assertFalse(
            gateway.latch_operation_uncertain(
                "doctor@example.test",
                "create",
                self.KEY,
                "duplicate fallback",
            )
        )
        self.assertEqual(
            gateway._ledger().lookup(
                actor="doctor@example.test",
                kind="create",
                operation_key=self.KEY,
            ).state,
            "COMMITTED",
        )
        with self.assertRaises(patient_write.WriteLocked):
            with gateway.patient_write_fence(
                "doctor@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass

    def test_latch_file_blocks_new_dispatch_when_the_database_is_corrupt(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        database = (
            Path(root)
            / "private"
            / fence_store.STORE_DIRNAME
            / "write-fence.sqlite3"
        )
        database.write_bytes(b"corrupt")
        self.assertTrue(gateway.latch_manual_investigation("cannot persist"))
        with self.assertRaises(patient_write.WriteLocked):
            with gateway.patient_write_fence(
                "other@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass


    SECRET = b"k" * 32

    def valid_token(self, gateway, *, actor="doctor@example.test", kind="create",
                    key=KEY, receipt="native-receipt"):
        return patient_write.ack_token(
            self.SECRET, gateway.site, actor, kind, key, receipt
        )

    def barrier_state(self, gateway):
        return gateway._ledger().lookup(
            actor=write_fence.LATCH_ACTOR,
            kind="create",
            operation_key=write_fence.barrier_key(
                "doctor@example.test", "create", self.KEY
            ),
        ).state

    def test_commit_creates_a_barrier_that_blocks_a_new_key(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        self.assertEqual(self.barrier_state(gateway), "DISPATCHED")
        with self.assertRaises(patient_write.WriteLocked):
            with gateway.patient_write_fence(
                "other@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass
        self.assertIsNone(
            gateway._ledger().lookup(
                actor="other@example.test",
                kind="create",
                operation_key=self.OTHER_KEY,
            )
        )

    def test_valid_ack_releases_the_barrier_and_admits_a_new_key(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        token = self.valid_token(gateway)
        self.assertEqual(
            gateway.acknowledge_write(
                self.SECRET, "doctor@example.test", "create", self.KEY, token
            ),
            "acked",
        )
        self.assertEqual(self.barrier_state(gateway), "COMMITTED")
        with gateway.patient_write_fence(
            "doctor@example.test", "create", self.OTHER_KEY, "b" * 64
        ) as claim:
            claim.commit("native-receipt-2")

    def test_repeat_valid_ack_is_already_without_a_duplicate_terminal(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        token = self.valid_token(gateway)
        self.assertEqual(
            gateway.acknowledge_write(
                self.SECRET, "doctor@example.test", "create", self.KEY, token
            ),
            "acked",
        )
        sequence = gateway._ledger().info().sequence
        self.assertEqual(
            gateway.acknowledge_write(
                self.SECRET, "doctor@example.test", "create", self.KEY, token
            ),
            "already",
        )
        self.assertEqual(gateway._ledger().info().sequence, sequence)

    def test_invalid_token_leaves_the_barrier_in_place(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        for token in ("0" * 64, self.valid_token(gateway, receipt="other-receipt")):
            with self.subTest(token=token[:8]):
                self.assertEqual(
                    gateway.acknowledge_write(
                        self.SECRET, "doctor@example.test", "create", self.KEY, token
                    ),
                    "invalid",
                )
        self.assertEqual(self.barrier_state(gateway), "DISPATCHED")
        with self.assertRaises(patient_write.WriteLocked):
            with gateway.patient_write_fence(
                "doctor@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass

    def test_wrong_actor_ack_is_invalid(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        token = self.valid_token(gateway, actor="other@example.test")
        self.assertEqual(
            gateway.acknowledge_write(
                self.SECRET, "other@example.test", "create", self.KEY, token
            ),
            "invalid",
        )
        self.assertEqual(self.barrier_state(gateway), "DISPATCHED")

    def test_ack_for_a_non_committed_operation_is_invalid(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        with self.assertRaises(patient_write.WriteUncertain):
            with gateway.patient_write_fence(
                "doctor@example.test", "create", self.KEY, "a" * 64
            ):
                pass
        token = self.valid_token(gateway, receipt="native-receipt")
        self.assertEqual(
            gateway.acknowledge_write(
                self.SECRET, "doctor@example.test", "create", self.KEY, token
            ),
            "invalid",
        )
        self.assertEqual(self.barrier_state(gateway), "DISPATCHED")

    def test_ack_persistence_failure_leaves_the_barrier_and_blocks(self):
        root = self.initialized_root()
        gateway = self.gateway_with_store(root)
        self.commit_one(gateway)
        real_ledger = gateway._ledger()

        class FailingLedger:
            def lookup(self, **kwargs):
                return real_ledger.lookup(**kwargs)

            def acknowledge_barrier(self, **kwargs):
                raise write_fence.StoreUnavailable("ack store failed")

        gateway._ledger = lambda: FailingLedger()
        try:
            token = self.valid_token(gateway)
            with self.assertRaises(patient_write.StoreUnavailable):
                gateway.acknowledge_write(
                    self.SECRET, "doctor@example.test", "create", self.KEY, token
                )
        finally:
            del gateway._ledger
        self.assertEqual(self.barrier_state(gateway), "DISPATCHED")
        with self.assertRaises(patient_write.WriteLocked):
            with gateway.patient_write_fence(
                "doctor@example.test", "create", self.OTHER_KEY, "b" * 64
            ):
                pass


class AppointmentWriteAdapterTests(GatewayTestCase):
    """Adapter checks against the pinned healthcare v16.6.1 contract.

    The fake Frappe mirrors the verified shapes: ``check_in_appointment`` is a
    module-level function, ``validate.set_status`` derives the status from the
    date, and ``get_doc(..., for_update=True)`` is a locking read. These tests
    prove the adapter calls those real entry points and fails closed; they do not
    claim to reproduce MySQL locking semantics.
    """

    TODAY = "2026-11-14"
    MODIFIED = "2026-11-14 09:30:00.123456"
    KEY = "appt-book-key-0001-012345"
    NATIVE = "NATIVE-APPT-2"
    IDENTITY = {
        "patients": ("PAT-2",),
        "company": "COMP-1",
        "appointment_type": "APPT-TYPE-1",
        "department": "MEDDEP-1",
    }

    def stub(self, *, permission=True, nowdate=TODAY):
        stub = FakeFrappe(
            site=config.APPROVED_APPOINTMENT_WRITE_SITE,
            session=types.SimpleNamespace(
                user="reception@example.test", sid="sid-abc123"
            ),
        )
        stub.utils = types.SimpleNamespace(nowdate=lambda: nowdate)
        stub.db.values[("__perm__", None, "create")] = permission
        stub.db.values[("__perm__", None, "write")] = permission
        stub.db.values[("__perm__", self.NATIVE, "write")] = permission
        stub.db.values[("Appointment Type", "APPT-TYPE-1", "default_duration")] = 15
        stub.db.values[("Appointment Type", "APPT-TYPE-1", "allow_booking_for")] = "Department"
        stub.db.values[("Appointment Type", "APPT-TYPE-1", "appointment_type")] = "APPT-TYPE-1"
        stub.db.values[("Patient", "PAT-2", "patient_name")] = "Patient Two"
        stub.db.values[("Patient", "PAT-2", "sex")] = "Female"
        stub.db.values[("Patient", "PAT-2", "inpatient_record")] = None
        stub.db.values[("Patient", "PAT-2", "emergency_record")] = None
        stub.db.sql_rows = []
        return stub

    def grant_reads(self, stub, *names):
        for name in names:
            stub.db.values[("__perm__", name, "read")] = True

    def appointment_doc(
        self,
        stub,
        *,
        status="Open",
        appointment_date=None,
        patient="PAT-2",
        company="COMP-1",
        department="MEDDEP-1",
        appointment_type="APPT-TYPE-1",
        duration=15,
        modified=None,
        derive_status=True,
    ):
        """A Patient Appointment document with the controller's own derivation."""

        date = appointment_date or self.TODAY
        doc = FakeDocument("Patient Appointment", self.NATIVE)
        doc.status = status
        doc.appointment_date = date
        doc.appointment_time = "08:00:00"
        doc.patient = patient
        doc.company = company
        doc.department = department
        doc.appointment_type = appointment_type
        doc.appointment_for = "Department"
        doc.duration = duration
        doc.patient_name = "Patient Two"
        doc.patient_sex = "Female"
        doc.patient_age = None
        doc.naming_series = "HLC-APP-.YYYY.-"
        doc.title = f"Patient Two at {department}"
        doc.appointment_datetime = f"{date} 08:00:00"
        doc.appointment_end_datetime = f"{date} 08:15:00"
        doc.appointment_based_on_check_in = True
        doc.invoiced = 0
        doc.reminded = 0
        doc.add_video_conferencing = 0
        doc.modified = modified or self.MODIFIED
        if derive_status:

            def controller_save():
                # patient_appointment.py:133-149
                if doc.status in (
                    "Checked In",
                    "Checked Out",
                    "Closed",
                    "Cancelled",
                    "No Show",
                ):
                    pass
                elif doc.appointment_date == self.TODAY:
                    if doc.status not in ("Open", "Confirmed"):
                        doc.status = "Open"
                elif doc.appointment_date > self.TODAY:
                    if doc.status not in ("Scheduled", "Confirmed"):
                        doc.status = "Scheduled"
                else:
                    doc.status = "No Show"
                start = datetime.strptime(
                    f"{doc.appointment_date} {doc.appointment_time}",
                    "%Y-%m-%d %H:%M:%S",
                )
                end = start + timedelta(minutes=doc.duration)
                doc.appointment_datetime = start.strftime("%Y-%m-%d %H:%M:%S")
                doc.appointment_end_datetime = end.strftime("%Y-%m-%d %H:%M:%S")
                doc.title = f"{doc.patient_name or doc.patient} at {doc.department}"
                doc.saved = True

            doc.save = controller_save
        stub.docs[("Patient Appointment", self.NATIVE)] = doc
        return doc

    def locked_state(self, doc):
        return frappe_gateway.AppointmentLockedState(
            doc=doc,
            patient=doc.patient,
            company=doc.company,
            department=doc.department,
            appointment_type=doc.appointment_type,
            appointment_for=doc.appointment_for,
            duration=doc.duration,
            status=doc.status,
            appointment_date=doc.appointment_date,
            appointment_time=doc.appointment_time,
            modified=doc.modified,
            patient_name=doc.patient_name,
            patient_sex=doc.patient_sex,
            notes=getattr(doc, "notes", None),
            practitioner=getattr(doc, "practitioner", None),
            event=getattr(doc, "event", None),
            invoiced=getattr(doc, "invoiced", None),
            ref_sales_invoice=getattr(doc, "ref_sales_invoice", None),
            paid_amount=getattr(doc, "paid_amount", None),
            insurance_coverage=getattr(doc, "insurance_coverage", None),
            service_request=getattr(doc, "service_request", None),
            mode_of_payment=getattr(doc, "mode_of_payment", None),
        )

    def locked_stub(self, **kwargs):
        """A stub whose target is a fully readable, writable locked record."""

        stub = self.stub()
        doc = self.appointment_doc(stub, **kwargs)
        self.grant_reads(
            stub, "PAT-2", "COMP-1", "MEDDEP-1", "APPT-TYPE-1", "Female"
        )
        return stub, doc

    # -- settings and authority ------------------------------------------

    def test_pinned_contract_constants_match_the_installed_module(self):
        self.assertEqual(
            frappe_gateway.NATIVE_CHECKIN_MODULE,
            "healthcare.healthcare.doctype.patient_appointment.patient_appointment",
        )
        self.assertEqual(
            frappe_gateway.NATIVE_CHECKIN_NAME, "check_in_appointment"
        )
        self.assertEqual(
            set(frappe_gateway.APPOINTMENT_UNSAFE_SETTINGS),
            {
                "show_payment_popup",
                "send_appointment_confirmation",
                "send_appointment_reminder",
                "enable_free_follow_ups",
                "apply_free_follow_ups_on_encounters",
            },
        )
        self.assertEqual(
            tuple(frappe_gateway.APPOINTMENT_UNSAFE_SETTING_LINKS),
            ("default_google_calendar",),
        )
        self.assertEqual(
            frappe_gateway.APPOINTMENT_RESCHEDULE_STATUSES,
            ("Scheduled", "Open", "Confirmed"),
        )
        self.assertEqual(
            frappe_gateway.APPOINTMENT_CHECKIN_STATUSES, ("Open", "Confirmed")
        )

    def test_every_controller_side_effect_setting_fails_closed(self):
        for setting in frappe_gateway.APPOINTMENT_UNSAFE_SETTINGS:
            with self.subTest(setting=setting):
                stub = self.stub()
                stub.db.single_values[("Healthcare Settings", setting)] = 1
                self.assertTrue(
                    self.gateway(stub).healthcare_appointment_side_effects_unsafe()
                )

    def test_a_configured_default_calendar_fails_closed(self):
        for link in frappe_gateway.APPOINTMENT_UNSAFE_SETTING_LINKS:
            with self.subTest(link=link):
                stub = self.stub()
                stub.db.single_values[("Healthcare Settings", link)] = "CAL-1"
                self.assertTrue(
                    self.gateway(stub).healthcare_appointment_side_effects_unsafe()
                )

    def test_safe_or_unreadable_settings(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        self.assertFalse(gateway.healthcare_appointment_side_effects_unsafe())
        for setting in frappe_gateway.APPOINTMENT_UNSAFE_SETTINGS:
            stub.db.single_values[("Healthcare Settings", setting)] = 0
        self.assertFalse(gateway.healthcare_appointment_side_effects_unsafe())
        stub.db.get_single_value = lambda *args: (_ for _ in ()).throw(
            RuntimeError("settings unavailable")
        )
        self.assertTrue(gateway.healthcare_appointment_side_effects_unsafe())

    def test_patient_and_linked_reads_are_document_checked(self):
        stub = self.stub()
        stub.db.values[("__perm__", "PAT-2", "read")] = True
        stub.db.values[("__perm__", "MEDDEP-1", "read")] = True
        stub.db.values[("__perm__", None, "read")] = True
        stub.db.values[("__perm__", "COMP-1", "read")] = True
        gateway = self.gateway(stub)
        self.assertTrue(gateway.native_patient_can_read("PAT-2"))
        self.assertTrue(
            gateway.native_doctype_can_read("Medical Department", "MEDDEP-1")
        )
        self.assertTrue(gateway.native_doctype_can_read("Company", "COMP-1"))
        self.assertEqual(
            stub.permission_calls,
            [
                ("Patient", "read", "PAT-2", "reception@example.test"),
                (
                    "Medical Department",
                    "read",
                    "MEDDEP-1",
                    "reception@example.test",
                ),
                ("Company", "read", "COMP-1", "reception@example.test"),
            ],
        )

    def test_create_and_write_authority_are_native_permission_checks(self):
        stub = self.stub(permission=False)
        gateway = self.gateway(stub)
        self.assertFalse(gateway.native_appointment_can_create())
        self.assertFalse(gateway.native_appointment_can_write())
        stub.db.values[("__perm__", None, "create")] = True
        stub.db.values[("__perm__", self.NATIVE, "write")] = True
        self.assertTrue(gateway.native_appointment_can_create())
        self.assertTrue(gateway.native_appointment_can_write(self.NATIVE))

    def test_site_today_is_the_site_local_nowdate(self):
        self.assertEqual(self.gateway(self.stub()).site_today(), self.TODAY)
        for value in (None, "2026-02-30", "14-11-2026", 5):
            with self.subTest(value=value):
                self.assertIsNone(
                    self.gateway(self.stub(nowdate=value)).site_today()
                )

    # -- create -----------------------------------------------------------

    def test_booking_sets_only_the_whitelisted_native_fields(self):
        stub = self.stub()
        prepared = self.gateway(stub).prepare_native_appointment_create(
            {
                "patient": "PAT-1",
                "company": "COMP-1",
                "department": "MEDDEP-1",
                "appointment_type": "APPT-TYPE-1",
                "duration": 15,
                "appointment_date": "2026-11-20",
                "appointment_time": "09:30:00",
            }
        )
        self.assertEqual(prepared.doctype, "Patient Appointment")
        self.assertEqual(prepared.patient, "PAT-1")
        self.assertEqual(prepared.company, "COMP-1")
        self.assertEqual(prepared.department, "MEDDEP-1")
        self.assertEqual(prepared.appointment_type, "APPT-TYPE-1")
        self.assertEqual(prepared.appointment_for, "Department")
        self.assertEqual(prepared.duration, 15)
        self.assertEqual(prepared.appointment_date, "2026-11-20")
        self.assertEqual(prepared.appointment_time, "09:30:00")
        self.assertFalse(getattr(prepared, "practitioner", None))
        self.assertFalse(getattr(prepared, "service_unit", None))
        self.assertFalse(getattr(prepared, "status", None))
        self.assertEqual(stub.new_docs[-1], prepared)

    def test_booking_rejects_every_default_injected_side_effect_field(self):
        for field in (
            "practitioner",
            "service_unit",
            "event",
            "ref_sales_invoice",
            "procedure_prescription",
            "service_request",
            "insurance_policy",
            "insurance_coverage",
            "inpatient_record",
            "emergency_record",
        ):
            with self.subTest(field=field):
                stub = self.stub()
                stub.new_doc_defaults = {field: "X-1"}
                with self.assertRaises(appointment_write.UnsafeSideEffect):
                    self.gateway(stub).prepare_native_appointment_create(
                        {
                            "patient": "PAT-1",
                            "company": "COMP-1",
                            "department": "MEDDEP-1",
                            "appointment_type": "APPT-TYPE-1",
                            "duration": 15,
                            "appointment_date": "2026-11-20",
                            "appointment_time": "09:30:00",
                        }
                    )

    def test_booking_accepts_the_framework_naming_series_select_default(self):
        # frappe.new_doc pre-fills naming_series from the DocType Select
        # static default before the prepare-time contract check.
        stub = self.stub()
        stub.new_doc_defaults = {
            "naming_series": appointment_contract.PINNED_NAMING_SERIES
        }
        gateway = self.gateway(stub)
        prepared = gateway.prepare_native_appointment_create(
            {
                "patient": "PAT-1",
                "company": "COMP-1",
                "department": "MEDDEP-1",
                "appointment_type": "APPT-TYPE-1",
                "duration": 15,
                "appointment_date": "2026-11-20",
                "appointment_time": "09:30:00",
            }
        )
        self.assertEqual(
            prepared.naming_series, appointment_contract.PINNED_NAMING_SERIES
        )
        self.assertFalse(prepared.inserted)
        self.assertEqual(
            gateway.insert_native_appointment(prepared), "PAT-NEW"
        )

    def test_booking_rejects_a_tampered_naming_series_default(self):
        stub = self.stub()
        stub.new_doc_defaults = {"naming_series": "HLC-APP-.YY.-"}
        with self.assertRaises(appointment_write.UnsafeSideEffect):
            self.gateway(stub).prepare_native_appointment_create(
                {
                    "patient": "PAT-1",
                    "company": "COMP-1",
                    "department": "MEDDEP-1",
                    "appointment_type": "APPT-TYPE-1",
                    "duration": 15,
                    "appointment_date": "2026-11-20",
                    "appointment_time": "09:30:00",
                }
            )
        self.assertFalse(stub.new_docs[-1].inserted)

    def test_insert_returns_the_native_name_or_fails_uncertain(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        doc = gateway.prepare_native_appointment_create(
            {
                "patient": "PAT-1",
                "company": "COMP-1",
                "department": "MEDDEP-1",
                "appointment_type": "APPT-TYPE-1",
                "duration": 15,
                "appointment_date": "2026-11-20",
                "appointment_time": "09:30:00",
            }
        )
        self.assertEqual(gateway.insert_native_appointment(doc), "PAT-NEW")
        self.assertTrue(doc.inserted)

    def test_target_side_effect_fields_fail_closed(self):
        for field, value in (
            ("practitioner", "PRACT-1"),
            ("invoiced", 1),
            ("ref_sales_invoice", "SINV-1"),
            ("insurance_policy", "POL-1"),
        ):
            with self.subTest(field=field):
                stub, doc = self.locked_stub()
                setattr(doc, field, value)
                self.assertIsNotNone(
                    self.gateway(stub).native_appointment_target_problem(
                        self.NATIVE, expected=self.IDENTITY
                    )
                )

    def test_a_missing_target_is_unsafe(self):
        self.assertIsNotNone(
            self.gateway(self.stub()).native_appointment_target_problem(
                self.NATIVE, expected=self.IDENTITY
            )
        )

    def test_type_duration_requires_a_readable_positive_integer(self):
        stub = self.stub()
        stub.db.values[("__perm__", "APPT-TYPE-1", "read")] = True
        gateway = self.gateway(stub)
        self.assertEqual(gateway.native_appointment_type_duration("APPT-TYPE-1"), 15)

        stub.db.values[("Appointment Type", "APPT-TYPE-1", "default_duration")] = 0
        self.assertIsNone(gateway.native_appointment_type_duration("APPT-TYPE-1"))
        stub.db.values[("Appointment Type", "APPT-TYPE-1", "default_duration")] = "15"
        self.assertIsNone(gateway.native_appointment_type_duration("APPT-TYPE-1"))
        stub.db.values[("Appointment Type", "APPT-TYPE-1", "default_duration")] = 15
        stub.meta.permlevels["default_duration"] = 1
        self.assertIsNone(gateway.native_appointment_type_duration("APPT-TYPE-1"))
        stub.meta.permlevels["default_duration"] = 0
        stub.db.values[("__perm__", "APPT-TYPE-1", "read")] = False
        self.assertIsNone(gateway.native_appointment_type_duration("APPT-TYPE-1"))

    # -- locking and rescheduling -----------------------------------------

    def test_lock_restarts_the_snapshot_then_rereads_for_update(self):
        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        state = self.gateway(stub).lock_appointment_target(
             self.NATIVE,
             base_modified=self.MODIFIED,
             expected=self.IDENTITY,
        )
        self.assertEqual(stub.db.rollbacks, 1)
        self.assertEqual(
            stub.get_doc_calls, [("Patient Appointment", self.NATIVE, {"for_update": True})]
        )
        self.assertIs(state.doc, doc)
        self.assertEqual(state.patient, "PAT-2")
        self.assertEqual(state.company, "COMP-1")
        self.assertEqual(state.department, "MEDDEP-1")
        self.assertEqual(state.appointment_type, "APPT-TYPE-1")
        self.assertEqual(state.appointment_for, "Department")
        self.assertEqual(state.duration, 15)
        self.assertEqual(state.appointment_date, "2026-11-20")
        self.assertEqual(state.status, "Open")
        self.assertEqual(state.modified, self.MODIFIED)

    def test_lock_enforces_the_configured_target_identity(self):
        for field, value in (
            ("patient", "PAT-9"),
            ("company", "COMP-9"),
            ("appointment_type", "TYPE-9"),
            ("department", "DEP-9"),
        ):
            with self.subTest(field=field):
                stub, _doc = self.locked_stub()
                with self.assertRaises(patient_write.UnsafeSideEffect):
                    self.gateway(stub).lock_appointment_target(
                        self.NATIVE,
                        base_modified=self.MODIFIED,
                        expected={**self.IDENTITY, field: value},
                    )

    def test_lock_reads_dependencies_with_the_value_cache_disabled(self):
        stub, _doc = self.locked_stub()
        self.gateway(stub).lock_appointment_target(
            self.NATIVE,
            base_modified=self.MODIFIED,
            expected=self.IDENTITY,
        )
        self.assertTrue(stub.db.read_calls)
        self.assertTrue(
            all(cache is False for _d, _n, _f, _u, cache in stub.db.read_calls)
        )
        locked = [
            (doctype, name)
            for doctype, name, _field, for_update, _cache in stub.db.read_calls
            if for_update
        ]
        self.assertEqual(
            locked,
            [("Patient", "PAT-2"), ("Appointment Type", "APPT-TYPE-1")],
        )

    def test_lock_rechecks_settings_and_carried_side_effect_fields(self):
        stub, _doc = self.locked_stub()
        stub.db.single_values[("Healthcare Settings", "show_payment_popup")] = 1
        with self.assertRaises(appointment_write.UnsafeSideEffect):
            self.gateway(stub).lock_appointment_target(
                 self.NATIVE,
                 base_modified=self.MODIFIED,
                 expected=self.IDENTITY,
            )

        for field in ("ref_sales_invoice", "procedure_prescription", "service_request"):
            with self.subTest(field=field):
                stub, doc = self.locked_stub()
                setattr(doc, field, "X-1")
                with self.assertRaises(appointment_write.UnsafeSideEffect):
                    self.gateway(stub).lock_appointment_target(
                         self.NATIVE,
                         base_modified=self.MODIFIED,
                         expected=self.IDENTITY,
                    )

    def test_lock_rechecks_field_level_and_link_readability(self):
        stub, _doc = self.locked_stub()
        stub.meta.permlevels["insurance_policy"] = 1
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).lock_appointment_target(
                 self.NATIVE,
                 base_modified=self.MODIFIED,
                 expected=self.IDENTITY,
            )

        stub, _doc = self.locked_stub()
        stub.meta.masked = ["patient_sex"]
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).lock_appointment_target(
                 self.NATIVE,
                 base_modified=self.MODIFIED,
                 expected=self.IDENTITY,
            )

        stub, doc = self.locked_stub()
        doc.patient_sex = "GENDER-1"
        stub.db.values[("__perm__", "GENDER-1", "read")] = False
        with self.assertRaises(FakePermissionError):
            self.gateway(stub).lock_appointment_target(
                 self.NATIVE,
                 base_modified=self.MODIFIED,
                 expected=self.IDENTITY,
            )

    def test_lock_refuses_a_stale_version_after_locking(self):
        stub, _doc = self.locked_stub(modified="2026-11-14 10:00:00.000001")
        with self.assertRaises(patient_write.StaleVersion):
            self.gateway(stub).lock_appointment_target(
                 self.NATIVE,
                 base_modified=self.MODIFIED,
                 expected=self.IDENTITY,
            )

    def test_reschedule_accepts_controller_derived_future_and_today_statuses(self):
        for original, new_date, expected in (
            ("Open", "2026-11-20", "Scheduled"),
            ("Scheduled", "2026-11-21", "Scheduled"),
            ("Confirmed", "2026-11-20", "Confirmed"),
            ("Scheduled", self.TODAY, "Open"),
            ("Confirmed", self.TODAY, "Confirmed"),
        ):
            with self.subTest(original=original, new_date=new_date):
                stub, doc = self.locked_stub(
                    status=original, appointment_date=self.TODAY
                )
                gateway = self.gateway(stub)
                state = gateway.lock_appointment_target(
                     self.NATIVE,
                     base_modified=self.MODIFIED,
                     expected=self.IDENTITY,
                )
                gateway.save_appointment_reschedule(
                    state,
                    {"appointment_date": new_date, "appointment_time": "10:15:00"},
                )
                self.assertTrue(doc.saved)
                self.assertEqual(doc.appointment_date, new_date)
                self.assertEqual(doc.appointment_time, "10:15:00")
                self.assertEqual(doc.status, expected)

    def test_reschedule_refuses_a_terminal_status(self):
        for status in ("Checked In", "Checked Out", "Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                stub, doc = self.locked_stub(status=status)
                with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
                    self.gateway(stub).save_appointment_reschedule(
                        self.locked_state(doc),
                        {
                            "appointment_date": "2026-11-20",
                            "appointment_time": "10:15:00",
                        },
                    )
                self.assertFalse(doc.saved)

    def test_reschedule_refuses_an_unexpected_controller_status_or_date(self):
        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        state = self.locked_state(doc)

        def wrong_status():
            doc.status = "No Show"
            doc.appointment_date = "2026-11-20"
            doc.saved = True

        doc.save = wrong_status
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).save_appointment_reschedule(
                state,
                {"appointment_date": "2026-11-20", "appointment_time": "10:15:00"},
            )

        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        state = self.locked_state(doc)

        def wrong_date():
            doc.status = "Scheduled"
            doc.appointment_date = "2026-11-25"
            doc.saved = True

        doc.save = wrong_date
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).save_appointment_reschedule(
                state,
                {"appointment_date": "2026-11-20", "appointment_time": "10:15:00"},
            )

    def test_reschedule_accepts_typed_controller_datetimes(self):
        import datetime

        stub, doc = self.locked_stub(appointment_date=self.TODAY)
        state = self.locked_state(doc)

        def controller_save():
            doc.status = "Scheduled"
            doc.appointment_date = "2026-11-20"
            start = datetime.datetime(2026, 11, 20, 10, 15)
            doc.appointment_datetime = start
            doc.appointment_end_datetime = start + datetime.timedelta(
                minutes=doc.duration
            )
            doc.title = f"{doc.patient_name} at {doc.department}"
            doc.saved = True

        doc.save = controller_save
        self.gateway(stub).save_appointment_reschedule(
            state,
            {"appointment_date": "2026-11-20", "appointment_time": "10:15:00"},
        )
        self.assertTrue(doc.saved)

    def test_reschedule_refuses_typed_controller_datetime_drift(self):
        import datetime

        stub, doc = self.locked_stub(appointment_date=self.TODAY)
        state = self.locked_state(doc)

        def controller_save():
            doc.status = "Scheduled"
            doc.appointment_date = "2026-11-20"
            doc.appointment_datetime = datetime.datetime(2026, 11, 20, 10, 15)
            doc.appointment_end_datetime = datetime.datetime(2026, 11, 20, 10, 45)
            doc.title = f"{doc.patient_name} at {doc.department}"
            doc.saved = True

        doc.save = controller_save
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).save_appointment_reschedule(
                state,
                {"appointment_date": "2026-11-20", "appointment_time": "10:15:00"},
            )

    def test_reschedule_refuses_identity_drift(self):
        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        state = self.locked_state(doc)
        real_save = doc.save

        def drift():
            real_save()
            doc.department = "MEDDEP-9"

        doc.save = drift
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).save_appointment_reschedule(
                state,
                {"appointment_date": "2026-11-20", "appointment_time": "10:15:00"},
            )

    # -- check-in ---------------------------------------------------------

    def inject_checkin(self, function):
        module = types.ModuleType(frappe_gateway.NATIVE_CHECKIN_MODULE)
        module.check_in_appointment = function
        sys.modules[frappe_gateway.NATIVE_CHECKIN_MODULE] = module
        self.addCleanup(sys.modules.pop, frappe_gateway.NATIVE_CHECKIN_MODULE, None)
        return module

    def test_checkin_calls_the_pinned_module_level_function(self):
        stub, doc = self.locked_stub(status="Confirmed")
        calls = []

        def check_in_appointment(appointment_id, practitioner=None, service_unit=None):
            calls.append((appointment_id, practitioner, service_unit))
            doc.status = "Checked In"
            doc.position_in_queue = 1
            doc.saved = True
            stub.db.values[("Patient Appointment", self.NATIVE, "status")] = "Checked In"

        self.inject_checkin(check_in_appointment)
        state = self.locked_state(doc)
        self.gateway(stub).native_appointment_checkin(state, self.TODAY)
        self.assertEqual(calls, [(self.NATIVE, None, None)])
        self.assertTrue(doc.saved)
        self.assertEqual(doc.status, "Checked In")

    def test_checkin_refuses_terminal_status_and_another_date(self):
        stub, doc = self.locked_stub(status="Checked In")
        with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
            self.gateway(stub).native_appointment_checkin(
                self.locked_state(doc), self.TODAY
            )

        stub, doc = self.locked_stub(status="Open", appointment_date="2026-11-15")
        with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
            self.gateway(stub).native_appointment_checkin(
                self.locked_state(doc), self.TODAY
            )

    def test_checkin_fails_closed_without_the_native_function(self):
        stub, doc = self.locked_stub(status="Open")
        state = self.locked_state(doc)
        sys.modules.pop(frappe_gateway.NATIVE_CHECKIN_MODULE, None)
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).native_appointment_checkin(state, self.TODAY)
        module = types.ModuleType(frappe_gateway.NATIVE_CHECKIN_MODULE)
        sys.modules[frappe_gateway.NATIVE_CHECKIN_MODULE] = module
        self.addCleanup(sys.modules.pop, frappe_gateway.NATIVE_CHECKIN_MODULE, None)
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).native_appointment_checkin(state, self.TODAY)

    def test_checkin_verifies_the_native_saved_status(self):
        stub, doc = self.locked_stub(status="Open")
        state = self.locked_state(doc)

        def check_in_appointment(appointment_id, practitioner=None, service_unit=None):
            doc.saved = True

        self.inject_checkin(check_in_appointment)
        stub.db.values[("Patient Appointment", self.NATIVE, "status")] = "Open"
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).native_appointment_checkin(state, self.TODAY)

    # -- cancel and manual no-show ----------------------------------------

    def inject_cancel(self, function):
        module = types.ModuleType(frappe_gateway.NATIVE_CHECKIN_MODULE)
        module.cancel_appointment = function
        sys.modules[frappe_gateway.NATIVE_CHECKIN_MODULE] = module
        self.addCleanup(sys.modules.pop, frappe_gateway.NATIVE_CHECKIN_MODULE, None)
        return module

    def transition_stub(self, *, status="Scheduled", **doc_kwargs):
        stub, doc = self.locked_stub(status=status, **doc_kwargs)
        stub.utils = types.SimpleNamespace(
            nowdate=lambda: self.TODAY, now=lambda: "2026-11-14 09:30:00"
        )
        doc.notes = None
        doc.practitioner = None
        doc.event = None
        doc.ref_sales_invoice = None
        doc.paid_amount = 0
        doc.insurance_coverage = None
        doc.service_request = None
        doc.mode_of_payment = None
        return stub, doc

    def test_cancel_drives_the_pinned_handler_then_the_terminal_save(self):
        stub, doc = self.transition_stub()
        calls = []

        def cancel_appointment(appointment_id):
            calls.append(appointment_id)

        self.inject_cancel(cancel_appointment)
        gateway = self.gateway(stub)
        result = gateway.cancel_native_appointment(
            self.locked_state(doc), "Patient called.", "reception@example.test"
        )

        self.assertEqual(calls, [self.NATIVE])
        self.assertTrue(doc.saved)
        self.assertEqual(doc.status, "Cancelled")
        self.assertEqual(
            doc.notes,
            "[2026-11-14 09:30:00] appointment_cancel by reception@example.test: "
            "Patient called.",
        )
        self.assertIs(result, doc)

    def test_cancel_preserves_the_exact_notes_preimage(self):
        stub, doc = self.transition_stub()
        doc.notes = "Existing note."
        self.inject_cancel(lambda appointment_id: None)

        self.gateway(stub).cancel_native_appointment(
            self.locked_state(doc), "Cancelled at the desk.", "doc@example.test"
        )

        self.assertEqual(
            doc.notes,
            "Existing note.\n[2026-11-14 09:30:00] appointment_cancel by "
            "doc@example.test: Cancelled at the desk.",
        )

    def test_cancel_refuses_terminal_and_linked_targets_before_mutation(self):
        stub, doc = self.transition_stub(status="Cancelled")
        with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
            self.gateway(stub).cancel_native_appointment(
                self.locked_state(doc), "reason", "reception@example.test"
            )
        self.assertFalse(doc.saved)

        stub, doc = self.transition_stub()
        doc.ref_sales_invoice = "SINV-1"
        with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
            self.gateway(stub).cancel_native_appointment(
                self.locked_state(doc), "reason", "reception@example.test"
            )
        self.assertFalse(doc.saved)

    def test_cancel_fails_closed_without_the_pinned_handler(self):
        stub, doc = self.transition_stub()
        sys.modules.pop(frappe_gateway.NATIVE_CHECKIN_MODULE, None)
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).cancel_native_appointment(
                self.locked_state(doc), "reason", "reception@example.test"
            )
        self.assertFalse(doc.saved)

    def test_cancel_fails_closed_when_the_status_did_not_stick(self):
        stub, doc = self.transition_stub()
        self.inject_cancel(lambda appointment_id: None)

        def rejected_save():
            doc.saved = True
            doc.status = "Open"

        doc.save = rejected_save
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).cancel_native_appointment(
                self.locked_state(doc), "reason", "reception@example.test"
            )

    def test_cancel_verifies_the_practitioner_event_branch(self):
        # The department booking surface never sets a practitioner, and the
        # pinned controller only creates/cancels a calendar Event on that
        # branch. A practitioner-populated target is therefore outside the
        # reviewed classification and is refused rather than half-handled.
        stub, doc = self.transition_stub(status="Confirmed")
        doc.practitioner = "PRAC-1"
        doc.event = "EVENT-1"
        calls = []
        self.inject_cancel(lambda appointment_id: calls.append(appointment_id))
        with self.assertRaises(appointment_write.AppointmentTargetNotEligible):
            self.gateway(stub).cancel_native_appointment(
                self.locked_state(doc), "reason", "reception@example.test"
            )
        self.assertEqual(calls, [])
        self.assertFalse(doc.saved)

    def test_no_show_writes_the_installed_terminal_status_and_note(self):
        stub, doc = self.transition_stub(status="Scheduled")
        result = self.gateway(stub).no_show_native_appointment(
            self.locked_state(doc), "Patient did not arrive.", "doc@example.test"
        )

        self.assertTrue(doc.saved)
        self.assertEqual(doc.status, "No Show")
        self.assertEqual(
            doc.notes,
            "[2026-11-14 09:30:00] appointment_no_show by doc@example.test: "
            "Patient did not arrive.",
        )
        self.assertIs(result, doc)

    def test_no_show_refuses_seen_or_terminal_statuses(self):
        for status in ("Checked In", "Closed", "Cancelled", "No Show"):
            with self.subTest(status=status):
                stub, doc = self.transition_stub(status=status)
                with self.assertRaises(
                    appointment_write.AppointmentTargetNotEligible
                ):
                    self.gateway(stub).no_show_native_appointment(
                        self.locked_state(doc), "reason", "doc@example.test"
                    )
                self.assertFalse(doc.saved)

    def test_no_show_fails_closed_when_the_controller_rejected_the_status(self):
        stub, doc = self.transition_stub(status="Scheduled")

        def rejected_save():
            doc.saved = True
            doc.status = "Scheduled"

        doc.save = rejected_save
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).no_show_native_appointment(
                self.locked_state(doc), "reason", "doc@example.test"
            )

    def test_a_malformed_site_timestamp_fails_closed(self):
        stub, doc = self.transition_stub()
        stub.utils = types.SimpleNamespace(
            nowdate=lambda: self.TODAY, now=lambda: "14/11/2026 09:30"
        )
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).no_show_native_appointment(
                self.locked_state(doc), "reason", "doc@example.test"
            )
        self.assertFalse(doc.saved)

    def test_action_detail_reads_the_reviewed_decision_fields(self):
        stub, doc = self.transition_stub()
        doc.notes = "Existing note."
        self.grant_reads(stub, self.NATIVE, "PAT-2")
        for field in ("patient", "appointment_date", "appointment_time", "status"):
            stub.db.values[("Patient Appointment", self.NATIVE, field)] = getattr(
                doc, field
            )
        for field in doc.__dict__:
            if field in frappe_gateway.APPOINTMENT_ACTION_FIELDS:
                stub.db.values[("Patient Appointment", self.NATIVE, field)] = (
                    getattr(doc, field)
                )
        detail = self.gateway(stub).native_appointment_action_detail(self.NATIVE)

        self.assertEqual(detail["status"], "Scheduled")
        self.assertEqual(detail["notes"], "Existing note.")
        self.assertEqual(detail["invoiced"], 0)
        for field in frappe_gateway.APPOINTMENT_ACTION_FIELDS:
            self.assertIn(field, detail)

    def test_action_detail_fails_closed_on_a_masked_or_missing_field(self):
        stub, doc = self.transition_stub()
        self.grant_reads(stub, "PAT-2")
        stub.meta.masked = ["notes"]
        self.assertIsNone(
            self.gateway(stub).native_appointment_action_detail(self.NATIVE)
        )
        stub.meta.masked = []
        stub.meta.permlevels.pop("notes")
        self.assertIsNone(
            self.gateway(stub).native_appointment_action_detail(self.NATIVE)
        )

    # -- pinned boundary, fetch sources and closed-world targets ----------

    def test_boundary_snapshot_drift_fails_closed(self):
        cases = {
            "version": lambda snapshot: snapshot["versions"].update(
                {"healthcare": "16.7.0"}
            ),
            "hash": lambda snapshot: snapshot["source_sha256"].update(
                {"fee_validity": "0" * 64}
            ),
            "hook": lambda snapshot: snapshot.update(
                {"doc_events_patient_appointment": ("x.y",)}
            ),
            "override": lambda snapshot: snapshot.update(
                {"override_doctype_class": ("x.Y",)}
            ),
            "server script": lambda snapshot: snapshot.update(
                {"enabled_server_scripts": 1}
            ),
            "metadata": lambda snapshot: snapshot["appointment_metadata"].update(
                {
                    "notes": tuple(snapshot["appointment_metadata"]["notes"][:4])
                    + ("x",)
                    + tuple(snapshot["appointment_metadata"]["notes"][5:])
                }
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(case=name):
                stub = self.stub()
                snapshot = {
                    key: value
                    if not isinstance(value, dict)
                    else json.loads(json.dumps(value))
                    for key, value in stub.appointment_boundary_snapshot.items()
                }
                mutate(snapshot)
                stub.appointment_boundary_snapshot = snapshot
                self.assertIsNotNone(
                    self.gateway(stub).native_appointment_boundary_problem()
                )

    def test_sources_lock_patient_then_type_and_verify_fetch_values(self):
        stub = self.stub()
        self.grant_reads(stub, "PAT-2", "APPT-TYPE-1", "Female")
        sources = self.gateway(stub).native_appointment_sources(
            "PAT-2", "APPT-TYPE-1", lock=True
        )
        self.assertEqual(
            sources,
            {
                "patient_name": "Patient Two",
                "patient_sex": "Female",
                "inpatient_record": None,
                "emergency_record": None,
                "duration": 15,
                "allow_booking_for": "Department",
            },
        )
        locked = [
            (doctype, name)
            for doctype, name, _field, for_update, _cache in stub.db.read_calls
            if for_update
        ]
        self.assertEqual(
            locked,
            [("Patient", "PAT-2"), ("Appointment Type", "APPT-TYPE-1")],
        )

    def test_sources_reject_unreviewed_patient_links_and_type_mode(self):
        cases = {
            "inpatient": {("Patient", "PAT-2", "inpatient_record"): "IP-1"},
            "emergency": {("Patient", "PAT-2", "emergency_record"): "ER-1"},
            "sex empty": {("Patient", "PAT-2", "sex"): None},
            "mode": {
                ("Appointment Type", "APPT-TYPE-1", "allow_booking_for"): "Practitioner"
            },
            "duration": {
                ("Appointment Type", "APPT-TYPE-1", "default_duration"): 0
            },
        }
        for name, values in cases.items():
            with self.subTest(case=name):
                stub = self.stub()
                self.grant_reads(stub, "PAT-2", "APPT-TYPE-1", "Female")
                stub.db.values.update(values)
                with self.assertRaises(patient_write.UnsafeSideEffect):
                    self.gateway(stub).native_appointment_sources(
                        "PAT-2", "APPT-TYPE-1", lock=True
                    )

    def test_lock_rejects_populated_closed_world_fields_and_fetch_drift(self):
        for mutate in (
            lambda doc: setattr(doc, "notes", "free text"),
            lambda doc: setattr(doc, "referring_practitioner", "PRACT-1"),
            lambda doc: setattr(doc, "patient_sex", "Male"),
            lambda doc: setattr(doc, "duration", 30),
            lambda doc: setattr(doc, "appointment_for", "Practitioner"),
        ):
            with self.subTest(mutate=mutate):
                stub, doc = self.locked_stub(appointment_date="2026-11-20")
                mutate(doc)
                with self.assertRaises(
                    (patient_write.UnsafeSideEffect, FakePermissionError)
                ):
                    self.gateway(stub).lock_appointment_target(
                         self.NATIVE,
                         base_modified=self.MODIFIED,
                         expected=self.IDENTITY,
                    )

    def test_target_problem_is_non_mutating_and_uses_effective_metadata(self):
        stub, doc = self.locked_stub()
        gateway = self.gateway(stub)
        self.assertIsNone(
            gateway.native_appointment_target_problem(
                self.NATIVE, expected=self.IDENTITY
            )
        )
        doc.notes = "free text"
        self.assertIsNotNone(
            gateway.native_appointment_target_problem(
                self.NATIVE, expected=self.IDENTITY
            )
        )
        self.assertEqual(stub.db.rollbacks, 0)
        self.assertEqual(
            stub.get_doc_calls,
            [
                ("Patient Appointment", self.NATIVE, {}),
                ("Patient Appointment", self.NATIVE, {}),
            ],
        )

    def test_inserted_revalidation_accepts_reviewed_deltas_only(self):
        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        doc.status = "Scheduled"
        sources = {
            "patient_name": "Patient Two",
            "patient_sex": "Female",
            "inpatient_record": None,
            "emergency_record": None,
            "duration": 15,
            "allow_booking_for": "Department",
        }
        prepared = {
            "patient": "PAT-2",
            "company": "COMP-1",
            "appointment_type": "APPT-TYPE-1",
            "department": "MEDDEP-1",
            "duration": 15,
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
        }
        gateway = self.gateway(stub)
        self.assertIs(
            gateway.verify_native_appointment_insert(
                self.NATIVE, prepared, sources, self.TODAY
            ),
            doc,
        )
        for mutate in (
            lambda: setattr(doc, "notes", "free text"),
            lambda: setattr(doc, "patient_name", "Other Patient"),
            lambda: setattr(doc, "source", "WEB"),
            lambda: setattr(doc, "invoiced", 1),
        ):
            with self.subTest(mutate=mutate):
                mutate()
                with self.assertRaises(patient_write.WriteUncertain):
                    gateway.verify_native_appointment_insert(
                        self.NATIVE, prepared, sources, self.TODAY
                    )

    def test_inserted_revalidation_accepts_typed_database_read_back(self):
        import datetime

        stub, doc = self.locked_stub(appointment_date="2026-11-20")
        doc.status = "Scheduled"
        doc.appointment_date = datetime.date(2026, 11, 20)
        doc.appointment_time = datetime.timedelta(hours=8)
        doc.appointment_datetime = datetime.datetime(2026, 11, 20, 8, 0)
        doc.appointment_end_datetime = datetime.datetime(2026, 11, 20, 8, 15)
        sources = {
            "patient_name": "Patient Two",
            "patient_sex": "Female",
            "inpatient_record": None,
            "emergency_record": None,
            "duration": 15,
            "allow_booking_for": "Department",
        }
        prepared = {
            "patient": "PAT-2",
            "company": "COMP-1",
            "appointment_type": "APPT-TYPE-1",
            "department": "MEDDEP-1",
            "duration": 15,
            "appointment_date": "2026-11-20",
            "appointment_time": "08:00:00",
        }
        self.assertIs(
            self.gateway(stub).verify_native_appointment_insert(
                self.NATIVE, prepared, sources, self.TODAY
            ),
            doc,
        )
        doc.appointment_datetime = datetime.datetime(2026, 11, 20, 9, 0)
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).verify_native_appointment_insert(
                self.NATIVE, prepared, sources, self.TODAY
            )

    # -- receipts ---------------------------------------------------------

    def test_lookup_appointment_receipt_maps_states(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        ledger = types.SimpleNamespace(
            lookup=lambda **kwargs: write_fence.OperationRecord(
                state=write_fence.COMMITTED,
                sequence=1,
                payload_digest="a" * 64,
                receipt="receipt",
            )
        )
        gateway._ledger = lambda: ledger
        self.assertEqual(
            gateway.lookup_appointment_receipt(
                "reception@example.test", "book", self.KEY
            ),
            "receipt",
        )
        ledger.lookup = lambda **kwargs: write_fence.OperationRecord(
            state=write_fence.DISPATCHED, sequence=1, payload_digest="a" * 64
        )
        with self.assertRaises(patient_write.WriteLocked):
            gateway.lookup_appointment_receipt(
                "reception@example.test", "book", self.KEY
            )

    def test_enumeration_overflow_is_a_budget_exhaustion(self):
        stub = self.stub()
        gateway = self.gateway(stub)

        class OverflowLedger:
            def committed_appointment_receipts(self, **kwargs):
                raise write_fence.EnumerationOverflow("too many")

        gateway._ledger = lambda: OverflowLedger()
        with self.assertRaises(appointment_write.AppointmentBudgetExhausted):
            gateway.list_committed_appointment_receipts(2, kind="book")

    def test_acknowledgement_rejects_patient_kinds(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        self.assertEqual(
            gateway.acknowledge_appointment_write(
                b"appointment-secret-that-is-at-least-32-bytes",
                "reception@example.test",
                "create",
                self.KEY,
                "a" * 64,
            ),
            "invalid",
        )


class AppointmentBarrierAdapterTests(ManualInvestigationLatchTests):
    """The site-wide barrier is resolved only by a bound appointment ack."""

    SECRET_BYTES = b"appointment-secret-that-is-at-least-32-bytes"

    def commit_book(self, gateway, *, actor="doctor@example.test"):
        receipt = appointment_write.encode_receipt(
            kind="book",
            alias="wsa1_" + "a" * 32 + "_" + "b" * 16 + "_" + self.KEY,
            native_appointment_id="NATIVE-APPT-1",
        )
        with gateway.appointment_write_fence(
            actor, "book", self.KEY, "a" * 64
        ) as claim:
            claim.commit(receipt)
        return receipt

    def test_acknowledges_once_and_is_idempotent(self):
        gateway = self.gateway_with_store(self.initialized_root())
        receipt = self.commit_book(gateway)
        token = appointment_write.ack_token(
            self.SECRET_BYTES,
            gateway.site,
            "doctor@example.test",
            "book",
            self.KEY,
            receipt,
        )
        self.assertIs(gateway.write_fence_ready(), False)
        self.assertEqual(
            gateway.acknowledge_appointment_write(
                self.SECRET_BYTES, "doctor@example.test", "book", self.KEY, token
            ),
            "acked",
        )
        self.assertEqual(
            gateway.acknowledge_appointment_write(
                self.SECRET_BYTES, "doctor@example.test", "book", self.KEY, token
            ),
            "already",
        )
        self.assertIs(gateway.write_fence_ready(), True)

    def test_a_patient_token_or_kind_cannot_resolve_an_appointment_barrier(self):
        gateway = self.gateway_with_store(self.initialized_root())
        self.commit_book(gateway)
        self.assertEqual(
            gateway.acknowledge_appointment_write(
                self.SECRET_BYTES, "doctor@example.test", "create", self.KEY, "a" * 64
            ),
            "invalid",
        )
        patient_token = patient_write.ack_token(
            self.SECRET_BYTES,
            gateway.site,
            "doctor@example.test",
            "create",
            self.KEY,
            "native-receipt",
        )
        self.assertEqual(
            gateway.acknowledge_appointment_write(
                self.SECRET_BYTES,
                "doctor@example.test",
                "book",
                self.KEY,
                patient_token,
            ),
            "invalid",
        )
        self.assertIs(gateway.write_fence_ready(), False)

    def test_ack_persistence_failure_leaves_the_barrier(self):
        gateway = self.gateway_with_store(self.initialized_root())
        receipt = self.commit_book(gateway)
        token = appointment_write.ack_token(
            self.SECRET_BYTES,
            gateway.site,
            "doctor@example.test",
            "book",
            self.KEY,
            receipt,
        )
        real_ledger = gateway._ledger()

        class FailingLedger:
            def lookup(self, **kwargs):
                return real_ledger.lookup(**kwargs)

            def acknowledge_barrier(self, **kwargs):
                raise write_fence.StoreUnavailable("ack store failed")

        gateway._ledger = lambda: FailingLedger()
        try:
            with self.assertRaises(patient_write.StoreUnavailable):
                gateway.acknowledge_appointment_write(
                    self.SECRET_BYTES,
                    "doctor@example.test",
                    "book",
                    self.KEY,
                    token,
                )
        finally:
            del gateway._ledger
        self.assertIs(gateway.write_fence_ready(), False)


MILESTONE_EVALUATOR = (
    "frappe.automation.doctype.milestone_tracker.milestone_tracker."
    "evaluate_milestone"
)


class CollectorMeta:
    """Minimal Meta for the boundary collector: behavior reads and fields."""

    def __init__(self):
        self.fields = []

    def get(self, name):
        return None


class CollectorStub(types.SimpleNamespace):
    """A fake Frappe module that also serves the live boundary collector."""

    def __init__(self, app_path, on_change=(MILESTONE_EVALUATOR,), milestones=()):
        super().__init__()
        self.__version__ = "16.54.0"
        self._app_path = Path(app_path)
        self._on_change = tuple(on_change)
        self._milestones = tuple(milestones)
        self.get_all_calls = []

    def get_app_path(self, app, *parts):
        return str(self._app_path.joinpath(parts[-1]))

    def get_hooks(self):
        return {
            "doc_events": {"*": {"on_change": list(self._on_change)}},
            "override_doctype_class": {},
            "override_whitelisted_methods": {},
            "scheduler_events": {"all": [], "daily": []},
            "extend_doctype_class": {},
        }

    def get_all(self, doctype, filters=None, limit=None, pluck=None):
        self.get_all_calls.append((doctype, dict(filters or {})))
        if doctype == "Milestone Tracker":
            return list(self._milestones)
        return []

    def get_meta(self, doctype):
        return CollectorMeta()

    def get_attr(self, path):
        name = path.rsplit(".", 1)[-1]
        module = path.rsplit(".", 1)[0]
        if name[0].isupper():
            holder = type(name, (), {})
        else:
            def holder(*args, **kwargs):
                return None
        holder.__module__ = module
        holder.__name__ = name
        return holder


class BoundaryCollectorTests(GatewayTestCase):
    def collect(self, **kwargs):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        (base / "patient_appointment.py").write_bytes(b"pinned")
        (base / "fee_validity.py").write_bytes(b"pinned")
        stub = CollectorStub(base, **kwargs)
        modules = {
            "erpnext": types.SimpleNamespace(__version__="16.52.1"),
            "healthcare": types.SimpleNamespace(__version__="16.6.1"),
        }
        with mock.patch.dict(sys.modules, modules):
            snapshot = self.gateway(stub)._collect_appointment_boundary_snapshot()
        return stub, snapshot

    def test_collector_samples_the_live_wildcard_on_change(self):
        _stub, snapshot = self.collect()
        self.assertEqual(snapshot["wildcard_on_change"], (MILESTONE_EVALUATOR,))

        _stub, drifted = self.collect(on_change=("x.drift",))
        self.assertEqual(drifted["wildcard_on_change"], ("x.drift",))

    def test_collector_counts_enabled_milestone_trackers_for_appointments(self):
        stub, snapshot = self.collect()
        self.assertEqual(snapshot["enabled_milestone_trackers"], 0)
        self.assertIn(
            (
                "Milestone Tracker",
                {"disabled": 0, "document_type": "Patient Appointment"},
            ),
            stub.get_all_calls,
        )

        _stub, drifted = self.collect(milestones=("Patient Appointment-Status",))
        self.assertEqual(drifted["enabled_milestone_trackers"], 1)


if __name__ == "__main__":
    unittest.main()
