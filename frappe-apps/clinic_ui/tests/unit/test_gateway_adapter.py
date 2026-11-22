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
    admin_contract,
    appointment_contract,
    appointment_write,
    config,
    deployment_marker,
    encounter_contract,
    fence_store,
    patient_write,
    procedure_contract,
    purchase_contract,
    write_fence,
)
from clinic_ui.runtime import frappe_gateway

# Master type -> installed DocType, for the typed-facts round-trip test.
MASTER_TYPE_DOCTYPES = {
    "uom": "UOM",
    "supplier_group": "Supplier Group",
    "warehouse": "Warehouse",
    "supplier": "Supplier",
    "item": "Item",
    "price_list": "Price List",
    "mode_of_payment": "Mode of Payment",
    "procedure_template": "Clinical Procedure Template",
    "practitioner": "Healthcare Practitioner",
}


def master_type_docname(master_type):
    return MASTER_TYPE_DOCTYPES[master_type]


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
        # Rows keyed by (doctype, parent) for the uncached ``get_all`` read.
        self.all_rows = {}
        self.get_all_calls = []
        self.get_all_error = None

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
        filters = dict(filters or {})
        self.get_all_calls.append((doctype, filters, pluck))
        if self.get_all_error is not None:
            raise self.get_all_error
        return list(self.all_rows.get((doctype, filters.get("parent")), []))

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
        self.get_list_calls = []
        self.get_list_rows = []
        self.get_list_error = None
        self.appointment_boundary_snapshot = (
            appointment_contract.reviewed_boundary_snapshot()
        )
        self.installed_apps = []
        self.hooks = {}

    def get_installed_apps(self):
        return self.installed_apps

    def get_hooks(self):
        return self.hooks

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

    def get_list(
        self,
        doctype,
        fields=None,
        order_by=None,
        limit_page_length=None,
        **kwargs,
    ):
        self.get_list_calls.append(
            (doctype, list(fields or ()), order_by, limit_page_length, kwargs)
        )
        if self.get_list_error is not None:
            raise self.get_list_error
        return list(self.get_list_rows)

    def get_roles(self, user=None):
        self.get_roles_calls = getattr(self, "get_roles_calls", [])
        self.get_roles_calls.append(user)
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


class FreshUserRolesAdapterTests(GatewayTestCase):
    """The fresh role read is a plain Has Role table read.

    It never calls ``frappe.get_roles`` (whose answer the pinned v16 memoises in
    ``frappe.cache`` under ``roles/<user>``) and never writes or purges a cache,
    so it cannot mask or manufacture a role. Only the exact parent/parenttype
    rows are read, with the pinned automatic-role exclusion.
    """

    USER = "doctor@example.test"
    FILTERS = {
        "parenttype": "User",
        "parent": "doctor@example.test",
        "role": ["not in", ["Guest", "All", "Desk User", "Administrator"]],
    }

    def test_reads_the_users_has_role_rows_uncached(self):
        stub = FakeFrappe()
        stub.local.cache = {"roles": {self.USER: ["Physician"]}}
        stub.db.all_rows[("Has Role", self.USER)] = ["Physician", "Nurse"]

        roles = self.gateway(stub).fresh_user_roles(self.USER)

        self.assertEqual(roles, ("Physician", "Nurse"))
        self.assertEqual(
            stub.db.get_all_calls, [("Has Role", self.FILTERS, "role")]
        )
        # No framework role read and no cache write or purge.
        self.assertEqual(getattr(stub, "get_roles_calls", []), [])
        self.assertEqual(stub.local.cache, {"roles": {self.USER: ["Physician"]}})

    def test_no_rows_is_an_empty_tuple_not_an_error(self):
        stub = FakeFrappe()

        self.assertEqual(self.gateway(stub).fresh_user_roles(self.USER), ())

    def test_a_failed_read_returns_none(self):
        stub = FakeFrappe()
        stub.db.get_all_error = RuntimeError("connection lost")

        self.assertIsNone(self.gateway(stub).fresh_user_roles(self.USER))

    def test_an_invalid_identity_returns_none_without_a_query(self):
        stub = FakeFrappe()
        for user in ("", "   ", None, 7):
            with self.subTest(user=user):
                self.assertIsNone(self.gateway(stub).fresh_user_roles(user))
        self.assertEqual(stub.db.get_all_calls, [])

    def test_a_malformed_row_returns_none(self):
        for rows in (["Physician", None], ["Physician", ""]):
            with self.subTest(rows=rows):
                stub = FakeFrappe()
                stub.db.all_rows[("Has Role", self.USER)] = rows
                self.assertIsNone(
                    self.gateway(stub).fresh_user_roles(self.USER)
                )


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


class ReadablePatientListAdapterTests(GatewayTestCase):
    """Bounded, permission-aware discovery for the converted patient lane."""

    def doctor(self):
        stub = FakeFrappe()
        stub.session = types.SimpleNamespace(
            user="doctor@example.test", sid="sid-abc123"
        )
        return stub

    def test_uses_the_permission_aware_native_list_with_a_hard_limit(self):
        stub = self.doctor()
        stub.get_list_rows = [{"name": "PAT-9"}, {"name": "PAT-1"}]

        names = self.gateway(stub).list_readable_patients(25)

        self.assertEqual(names, ["PAT-9", "PAT-1"])
        self.assertEqual(len(stub.get_list_calls), 1)
        doctype, fields, order_by, limit, _kwargs = stub.get_list_calls[0]
        self.assertEqual(doctype, "Patient")
        self.assertEqual(fields, ["name"])
        self.assertEqual(limit, 25)
        self.assertIn("modified", order_by)
        # ``frappe.get_all`` never appears on the discovery path.
        self.assertEqual(stub.db.get_all_calls, [])

    def test_missing_or_malformed_rows_are_dropped(self):
        stub = self.doctor()
        stub.get_list_rows = [
            {"name": "PAT-9"},
            {"name": ""},
            {"name": None},
            {},
            "PAT-2",
        ]
        names = self.gateway(stub).list_readable_patients(10)
        self.assertEqual(names, ["PAT-9"])

    def test_a_list_failure_returns_none_instead_of_raising(self):
        stub = self.doctor()
        stub.get_list_error = RuntimeError("native list unavailable")
        self.assertIsNone(self.gateway(stub).list_readable_patients(10))

    def test_an_invalid_limit_is_refused_without_a_query(self):
        stub = self.doctor()
        for limit in (0, -1, None, "10", True, 100000):
            with self.subTest(limit=limit):
                self.assertIsNone(self.gateway(stub).list_readable_patients(limit))
        self.assertEqual(stub.get_list_calls, [])


class NativePermissionDenialIdentityTests(GatewayTestCase):
    """A definite denial is recognised by type identity, never by class name."""

    def test_the_installed_permission_error_type_is_recognised(self):
        stub = FakeFrappe()
        self.assertTrue(
            self.gateway(stub).is_native_permission_denial(
                FakePermissionError("denied")
            )
        )

    def test_an_unrelated_class_with_the_same_name_is_not_recognised(self):
        class PermissionError(Exception):
            """Deliberately named like the framework exception; unrelated."""

        stub = FakeFrappe()
        self.assertFalse(
            self.gateway(stub).is_native_permission_denial(PermissionError("no"))
        )
        self.assertFalse(
            self.gateway(stub).is_native_permission_denial(RuntimeError("no"))
        )

    def test_a_missing_framework_type_fails_closed(self):
        stub = FakeFrappe()
        del stub.PermissionError
        self.assertFalse(
            self.gateway(stub).is_native_permission_denial(
                FakePermissionError("denied")
            )
        )


class ReadableAppointmentListAdapterTests(GatewayTestCase):
    """Bounded, permission-aware discovery for the converted appointment lane."""

    def doctor(self):
        stub = FakeFrappe()
        stub.session = types.SimpleNamespace(
            user="doctor@example.test", sid="sid-abc123"
        )
        return stub

    def test_uses_the_permission_aware_native_list_with_a_hard_limit(self):
        stub = self.doctor()
        stub.get_list_rows = [{"name": "APPT-9"}, {"name": "APPT-1"}]

        names = self.gateway(stub).list_readable_appointments(25)

        self.assertEqual(names, ["APPT-9", "APPT-1"])
        self.assertEqual(len(stub.get_list_calls), 1)
        doctype, fields, order_by, limit, _kwargs = stub.get_list_calls[0]
        self.assertEqual(doctype, "Patient Appointment")
        self.assertEqual(fields, ["name"])
        self.assertEqual(limit, 25)
        self.assertIn("modified", order_by)
        # ``frappe.get_all`` and raw SQL never appear on the discovery path.
        self.assertEqual(stub.db.get_all_calls, [])

    def test_missing_or_malformed_rows_are_dropped(self):
        stub = self.doctor()
        stub.get_list_rows = [
            {"name": "APPT-9"},
            {"name": ""},
            {"name": None},
            {},
            "APPT-2",
        ]
        names = self.gateway(stub).list_readable_appointments(10)
        self.assertEqual(names, ["APPT-9"])

    def test_a_list_failure_returns_none_instead_of_raising(self):
        stub = self.doctor()
        stub.get_list_error = RuntimeError("native list unavailable")
        self.assertIsNone(self.gateway(stub).list_readable_appointments(10))

    def test_an_invalid_limit_is_refused_without_a_query(self):
        stub = self.doctor()
        for limit in (0, -1, None, "10", True, 100000):
            with self.subTest(limit=limit):
                self.assertIsNone(
                    self.gateway(stub).list_readable_appointments(limit)
                )
        self.assertEqual(stub.get_list_calls, [])


class ReadableGenderListAdapterTests(GatewayTestCase):
    """Bounded, permission-aware Gender master read for the patient form."""

    def doctor(self):
        stub = FakeFrappe()
        stub.session = types.SimpleNamespace(
            user="doctor@example.test", sid="sid-abc123"
        )
        stub.db.values[("__perm__", None, "read")] = True
        return stub

    def test_uses_the_permission_aware_native_list_with_a_hard_limit(self):
        stub = self.doctor()
        stub.get_list_rows = [{"name": "Female"}, {"name": "Male"}]

        names = self.gateway(stub).native_gender_options(100)

        self.assertEqual(names, ["Female", "Male"])
        self.assertEqual(len(stub.get_list_calls), 1)
        doctype, fields, order_by, limit, _kwargs = stub.get_list_calls[0]
        self.assertEqual(doctype, "Gender")
        self.assertEqual(fields, ["name"])
        self.assertEqual(limit, 100)
        self.assertIn("name", order_by)
        self.assertIn(
            ("Gender", "read", None, "doctor@example.test"),
            stub.permission_calls,
        )
        # ``frappe.get_all`` and raw SQL never appear on this path.
        self.assertEqual(stub.db.get_all_calls, [])

    def test_without_gender_read_permission_nothing_is_queried(self):
        stub = self.doctor()
        stub.db.values[("__perm__", None, "read")] = False
        stub.get_list_rows = [{"name": "Female"}]
        self.assertIsNone(self.gateway(stub).native_gender_options(100))
        self.assertEqual(stub.get_list_calls, [])

    def test_missing_or_malformed_rows_are_dropped(self):
        stub = self.doctor()
        stub.get_list_rows = [
            {"name": "Female"},
            {"name": ""},
            {"name": None},
            {},
            "Male",
            {"name": 5},
            {"name": "bad\x00name"},
        ]
        self.assertEqual(
            self.gateway(stub).native_gender_options(100), ["Female"]
        )

    def test_a_list_failure_returns_none_instead_of_raising(self):
        stub = self.doctor()
        stub.get_list_error = RuntimeError("native list unavailable")
        self.assertIsNone(self.gateway(stub).native_gender_options(10))

    def test_an_invalid_limit_is_refused_without_a_query(self):
        stub = self.doctor()
        for limit in (0, -1, None, "10", True, 100000):
            with self.subTest(limit=limit):
                self.assertIsNone(self.gateway(stub).native_gender_options(limit))
        self.assertEqual(stub.get_list_calls, [])


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

    def test_checkin_photo_profile_dispatch_and_drift(self):
        target = "clinic_photos.visit.checkin.check_in_appointment"
        hook_key = f"{frappe_gateway.NATIVE_CHECKIN_MODULE}.check_in_appointment"
        for installed, overrides, expected in (
            (True, [target], "photo"),
            (False, [], "stock"),
            (True, [], None),
            (False, [target], None),
            (True, ["unknown.path"], None),
            (True, [target, target], None),
            (True, [target, "unknown.path"], None),
        ):
            with self.subTest(installed=installed, overrides=overrides):
                stub, doc = self.locked_stub(status="Open")
                state = self.locked_state(doc)
                stub.installed_apps = ["clinic_photos"] if installed else []
                stub.hooks = {"override_whitelisted_methods": {hook_key: overrides}}
                calls = []
                def invoke(kind):
                    def call(appointment_id):
                        calls.append((kind, appointment_id))
                        doc.status = "Checked In"
                        doc.position_in_queue = 1
                    return call
                self.inject_checkin(invoke("stock"))
                photo = types.ModuleType("clinic_photos.visit.checkin")
                photo.check_in_appointment = invoke("photo")
                with mock.patch.dict(sys.modules, {"clinic_photos.visit.checkin": photo}):
                    if expected is None:
                        with self.assertRaises(patient_write.WriteUncertain):
                            self.gateway(stub).native_appointment_checkin(state, self.TODAY)
                        self.assertEqual(calls, [])
                    else:
                        self.gateway(stub).native_appointment_checkin(state, self.TODAY)
                        self.assertEqual(calls, [(expected, self.NATIVE)])

    def test_photo_wrapper_failure_never_retries_stock(self):
        stub, doc = self.locked_stub(status="Open")
        stub.installed_apps = ["clinic_photos"]
        key = f"{frappe_gateway.NATIVE_CHECKIN_MODULE}.check_in_appointment"
        stub.hooks = {"override_whitelisted_methods": {
            key: ["clinic_photos.visit.checkin.check_in_appointment"]
        }}
        stock = mock.Mock()
        self.inject_checkin(stock)
        photo = types.ModuleType("clinic_photos.visit.checkin")
        photo.check_in_appointment = mock.Mock(side_effect=RuntimeError("uncertain"))
        with mock.patch.dict(sys.modules, {"clinic_photos.visit.checkin": photo}):
            with self.assertRaises(patient_write.WriteUncertain):
                self.gateway(stub).native_appointment_checkin(self.locked_state(doc), self.TODAY)
        photo.check_in_appointment.assert_called_once_with(self.NATIVE)
        stock.assert_not_called()

    def test_checkin_fails_closed_when_installed_app_evidence_unreadable(self):
        stub, doc = self.locked_stub(status="Open")
        stub.get_installed_apps = lambda: None
        stock = mock.Mock()
        self.inject_checkin(stock)
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).native_appointment_checkin(self.locked_state(doc), self.TODAY)
        stock.assert_not_called()

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

    # -- site clock and note postimage -------------------------------------

    def test_site_now_returns_a_whole_second_site_timestamp(self):
        stub, _doc = self.transition_stub()

        self.assertEqual(
            self.gateway(stub)._site_now(), "2026-11-14 09:30:00"
        )

    def test_site_now_accepts_the_installed_microsecond_clock(self):
        # Frappe 16.54 ``frappe.utils.now()`` is
        # ``now_datetime().strftime("%Y-%m-%d %H:%M:%S.%f")``.
        stub, _doc = self.transition_stub()
        stub.utils.now = lambda: "2026-11-14 09:30:00.123456"

        self.assertEqual(
            self.gateway(stub)._site_now(), "2026-11-14 09:30:00.123456"
        )

    def test_site_now_rejects_values_outside_the_pinned_shapes(self):
        for value in (
            "2026-11-14T09:30:00",  # ISO separator
            "2026-11-14 09:30:00.1234567",  # seven fractional digits
            "2026-11-14 09:30:00.",  # empty fraction
            "2026-11-14 09:30",  # missing seconds
            "2026-11-14 09:30:00 trailing",  # trailing text
            "14/11/2026 09:30:00",  # non-ISO date
            "2026-13-14 09:30:00",  # impossible month
            "",  # empty
            None,  # not a string
            20261114093000,  # not a string
        ):
            with self.subTest(value=value):
                stub, _doc = self.transition_stub()
                stub.utils.now = lambda value=value: value
                with self.assertRaises(patient_write.WriteUncertain):
                    self.gateway(stub)._site_now()

    def test_site_now_fails_closed_when_the_clock_raises(self):
        stub, _doc = self.transition_stub()

        def broken_now():
            raise RuntimeError("clock unavailable")

        stub.utils.now = broken_now
        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub)._site_now()

    def test_append_note_records_the_microsecond_clock_verbatim(self):
        stub, doc = self.transition_stub()
        stub.utils.now = lambda: "2026-11-14 09:30:00.123456"
        state = types.SimpleNamespace(notes="Existing note.")

        postimage = self.gateway(stub)._append_note(
            state, appointment_contract.CANCEL_KIND, "Patient called.", "doc@example.test"
        )

        self.assertEqual(
            postimage,
            "Existing note.\n[2026-11-14 09:30:00.123456] appointment_cancel "
            "by doc@example.test: Patient called.",
        )
        # The downstream contract compares the postimage verbatim, so the site
        # value must survive unchanged through ``note_line``/``note_postimage``.
        self.assertIsNone(
            appointment_contract.cancel_postcondition_problem(
                {"status": "Cancelled", "notes": postimage}, postimage
            )
        )
        self.assertIsNone(
            appointment_contract.no_show_postcondition_problem(
                {"status": "No Show", "notes": postimage}, postimage
            )
        )

    def test_cancel_saves_with_the_installed_microsecond_clock(self):
        stub, doc = self.transition_stub()
        stub.utils.now = lambda: "2026-11-14 09:30:00.123456"
        self.inject_cancel(lambda appointment_id: None)

        self.gateway(stub).cancel_native_appointment(
            self.locked_state(doc), "Patient called.", "reception@example.test"
        )

        self.assertTrue(doc.saved)
        self.assertEqual(
            doc.notes,
            "[2026-11-14 09:30:00.123456] appointment_cancel by "
            "reception@example.test: Patient called.",
        )

    def test_no_show_saves_with_the_installed_microsecond_clock(self):
        stub, doc = self.transition_stub(status="Scheduled")
        stub.utils.now = lambda: "2026-11-14 09:30:00.123456"

        self.gateway(stub).no_show_native_appointment(
            self.locked_state(doc), "Patient did not arrive.", "doc@example.test"
        )

        self.assertTrue(doc.saved)
        self.assertEqual(
            doc.notes,
            "[2026-11-14 09:30:00.123456] appointment_no_show by "
            "doc@example.test: Patient did not arrive.",
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

    def get_installed_apps(self):
        return []

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


class AmendmentDB:
    """Read-only fake for the Amendment A1 paths (company, counts, targets)."""

    def __init__(self):
        self.single_values = {}
        self.values = {}
        self.defaults = {}
        self.existing = set()
        self.counts = {}

    def get_single_value(self, doctype, fieldname):
        return self.single_values.get((doctype, fieldname))

    def get_default(self, key, *args, **kwargs):
        return self.defaults.get(key)

    def get_value(self, doctype, name, fieldname=None, as_dict=False, **kwargs):
        if isinstance(name, dict):
            # Filter lookup: ``(doctype, "filter", ((field, value), ...))``.
            return self.values.get(
                (doctype, "filter", tuple(sorted(name.items())))
            )
        if isinstance(fieldname, (list, tuple)):
            row = {field: self.values.get((doctype, name, field)) for field in fieldname}
            return row if as_dict else tuple(row[field] for field in fieldname)
        return self.values.get((doctype, name, fieldname))

    def exists(self, doctype, key):
        if isinstance(key, dict):
            return (doctype, "filter", tuple(sorted(key.items()))) in self.existing
        return (doctype, key) in self.existing

    def count(self, doctype, filters=None):
        if filters is not None:
            raise AssertionError("unexpected filtered count")
        return self.counts.get(doctype, 0)


class AmendmentDoc:
    def __init__(self, doctype, values):
        self.doctype = doctype
        self.name = None
        self.inserted = False
        self.submitted = False
        self.docstatus = 0
        for field, value in values.items():
            setattr(self, field, value)
        self.inserted_before_submit = None

    def insert(self):
        self.inserted = True
        if self.name is None:
            self.name = f"{self.doctype.upper()}-NEW-1"

    def submit(self):
        self.inserted_before_submit = self.inserted
        self.submitted = True
        self.docstatus = 1


class AmendmentStub(types.SimpleNamespace):
    """Fake Frappe module for the meta, company and creation paths."""

    def __init__(self, *, user="doctor@example.test"):
        super().__init__()
        self.session = types.SimpleNamespace(user=user, sid="sid-amendment")
        self.local = types.SimpleNamespace(
            site="marley.test",
            session={},
            response={},
            response_headers={},
            flags=types.SimpleNamespace(),
        )
        self.db = AmendmentDB()
        self.meta_fields = {}
        self.default_fields = set()
        self.valid_columns = {}
        self.permissions = set()
        self.docs = {}
        self.dict_docs = []
        self.created_docs = []
        self.all_rows = {}

    def get_meta(self, doctype):
        names = self.meta_fields.get(doctype, ())
        return types.SimpleNamespace(
            fields=[types.SimpleNamespace(fieldname=name) for name in names],
            # Real frappe Meta: the default columns (name/owner/creation/
            # modified/modified_by/docstatus/idx) are NOT DocField rows.
            default_fields=list(self.default_fields),
        )

    def get_meta_columns(self, doctype):
        return set(self.valid_columns.get(doctype, ()))

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            self.dict_docs.append(dict(doctype))
            document = AmendmentDoc(doctype["doctype"], doctype)
            self.created_docs.append(document)
            return document
        return self.docs[(doctype, name)]

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        return (doctype, ptype) in self.permissions

    def get_all(self, doctype, filters=None, limit=None, pluck=None, **kwargs):
        return list(self.all_rows.get(doctype, []))


class AmendmentMetaFieldTests(GatewayTestCase):
    """D1: the install preflight resolves meta fieldnames, not DB columns."""

    def stub_with_meta(self):
        stub = AmendmentStub()
        from clinic_ui.core import admin_contract, procedure_contract

        for doctype, fields in admin_contract.REQUIRED_FIELDS.items():
            names = set()
            for candidates in fields.values():
                names.add(candidates[0])
            stub.meta_fields[doctype] = sorted(
                set(stub.meta_fields.get(doctype, ())) | names
            )
            stub.valid_columns[doctype] = {
                name for name in names if name not in ("roles", "accounts")
            }
        for doctype, fields in procedure_contract.REQUIRED_FIELDS.items():
            names = {candidates[0] for candidates in fields.values()}
            stub.meta_fields[doctype] = sorted(
                set(stub.meta_fields.get(doctype, ())) | names
            )
            if doctype not in stub.valid_columns:
                stub.valid_columns[doctype] = {
                    name for name in names if name != "items"
                }
        return stub

    def test_user_roles_passes_on_an_install_that_has_the_table_field(self):
        stub = self.stub_with_meta()
        gateway = self.gateway(stub)

        self.assertIsNone(gateway.native_admin_install_problem())
        self.assertIn("roles", gateway._admin_meta_fields()["User"])

    def test_the_database_column_resolution_is_the_defect(self):
        from clinic_ui.core import admin_contract

        stub = self.stub_with_meta()
        columns = {
            doctype: stub.get_meta_columns(doctype)
            for doctype in admin_contract.REQUIRED_FIELDS
        }

        self.assertEqual(
            admin_contract.install_problem(columns),
            "missing field User.roles",
        )

    def test_procedure_items_passes_on_an_install_that_has_the_table_field(self):
        stub = self.stub_with_meta()
        gateway = self.gateway(stub)

        self.assertIsNone(gateway.native_procedure_install_problem())
        self.assertIn(
            "items", gateway._procedure_meta_fields()["Clinical Procedure"]
        )

    def test_a_genuinely_missing_field_is_still_refused(self):
        stub = self.stub_with_meta()
        stub.meta_fields["UOM"] = []

        self.assertEqual(
            self.gateway(stub).native_admin_install_problem(),
            "missing field UOM.uom_name",
        )


class AdminInstalledFieldMapTests(GatewayTestCase):
    """Review round 2: the admin field maps follow this stack's installed meta.

    On the deployed image ``Item Price`` has ``price_list_rate`` (required) and no
    ``rate``, and ``Clinical Procedure Template`` has no ``company``. The legacy
    maps therefore denied the whole admin family pre-fence, which is what this
    stub models.
    """

    INSTALLED_SHAPES = {
        "Item Price": [
            "currency",
            "item_code",
            "price_list",
            "price_list_rate",
            "uom",
        ],
        "Clinical Procedure Template": [
            "description",
            "item",
            "item_code",
            "item_group",
            "link_existing_item",
            "rate",
            "template",
        ],
    }

    LEGACY_SHAPES = {
        "Item Price": {
            "item_code": ("item_code",),
            "price_list": ("price_list",),
            "rate": ("rate",),
            "currency": ("currency",),
        },
        "Clinical Procedure Template": {
            "template": ("template",),
            "item_code": ("item_code",),
            "rate": ("rate",),
            "company": ("company",),
        },
    }

    def stub_with_installed_meta(self):
        from clinic_ui.core import admin_contract

        stub = AmendmentStub()
        for doctype, fields in admin_contract.REQUIRED_FIELDS.items():
            stub.meta_fields[doctype] = sorted(
                {candidates[0] for candidates in fields.values()}
            )
        for doctype, names in self.INSTALLED_SHAPES.items():
            stub.meta_fields[doctype] = list(names)
        return stub

    def installed_meta(self, stub):
        from clinic_ui.core import admin_contract

        return {
            doctype: set(stub.meta_fields[doctype])
            for doctype in admin_contract.REQUIRED_FIELDS
        }

    def legacy_required_fields(self):
        from clinic_ui.core import admin_contract

        legacy = dict(admin_contract.REQUIRED_FIELDS)
        legacy.update(self.LEGACY_SHAPES)
        return legacy

    def test_the_legacy_rate_map_is_the_defect(self):
        from clinic_ui.core import admin_contract

        stub = self.stub_with_installed_meta()
        legacy = self.legacy_required_fields()
        legacy["Clinical Procedure Template"] = {
            name: candidates
            for name, candidates in legacy["Clinical Procedure Template"].items()
            if name != "company"
        }
        with mock.patch.object(admin_contract, "REQUIRED_FIELDS", legacy):
            self.assertEqual(
                admin_contract.install_problem(self.installed_meta(stub)),
                "missing field Item Price.rate",
            )

    def test_the_legacy_company_map_is_the_second_defect(self):
        from clinic_ui.core import admin_contract

        stub = self.stub_with_installed_meta()
        legacy = self.legacy_required_fields()
        legacy["Item Price"] = {
            name: candidates
            for name, candidates in legacy["Item Price"].items()
            if name != "rate"
        }
        with mock.patch.object(admin_contract, "REQUIRED_FIELDS", legacy):
            self.assertEqual(
                admin_contract.install_problem(self.installed_meta(stub)),
                "missing field Clinical Procedure Template.company",
            )

    def test_the_gateway_preflight_passes_on_the_installed_shape(self):
        stub = self.stub_with_installed_meta()

        self.assertIsNone(self.gateway(stub).native_admin_install_problem())

    def test_a_removed_genuine_field_still_refuses(self):
        stub = self.stub_with_installed_meta()
        stub.meta_fields["Item Price"] = [
            name
            for name in stub.meta_fields["Item Price"]
            if name != "price_list_rate"
        ]

        self.assertEqual(
            self.gateway(stub).native_admin_install_problem(),
            "missing field Item Price.price_list_rate",
        )

    def test_the_item_price_insert_uses_the_installed_fields(self):
        stub = self.stub_with_installed_meta()
        stub.all_rows["Company"] = ["SIT Synthetic Company"]
        stub.db.values[
            ("Company", "SIT Synthetic Company", "default_currency")
        ] = "USD"

        self.gateway(stub).create_native_admin_master(
            "item",
            {
                "item_code": "SIT-ITEM-1",
                "item_name": "SIT Item One",
                "item_group": "All Item Groups",
                "stock_uom": "Nos",
                "price_list": "Standard Selling",
                "price_list_rate": 12.5,
            },
        )

        price = stub.created_docs[-1]
        self.assertEqual(price.doctype, "Item Price")
        self.assertEqual(price.price_list_rate, 12.5)
        self.assertEqual(price.uom, "Nos")
        # The installed Item Price has no ``rate`` column.
        self.assertFalse(hasattr(price, "rate"))

    def test_the_template_insert_links_the_existing_item(self):
        stub = self.stub_with_installed_meta()
        stub.all_rows["Company"] = ["SIT Synthetic Company"]
        stub.db.values[("Item", "SIT-ITEM-1", "item_group")] = "All Item Groups"

        self.gateway(stub).create_native_admin_master(
            "procedure_template",
            {
                "template_name": "SIT Dressing",
                "item_code": "SIT-ITEM-1",
                "rate": 25.0,
            },
        )

        doc = stub.created_docs[-1]
        self.assertEqual(doc.doctype, "Clinical Procedure Template")
        self.assertEqual(doc.template, "SIT Dressing")
        self.assertEqual(doc.description, "SIT Dressing")
        self.assertEqual(doc.item, "SIT-ITEM-1")
        self.assertEqual(doc.link_existing_item, 1)
        self.assertEqual(doc.item_group, "All Item Groups")
        # The installed template has no ``company`` column.
        self.assertFalse(hasattr(doc, "company"))


class AdminTypedFactsTests(GatewayTestCase):
    """Review round 3: admin facts keep the installed types.

    The observed failure: a Price List insert succeeded, then the fact reader
    pushed the Check ``selling`` through the text normaliser, which returns
    ``None`` for non-strings, so the post-claim compare said "not a selling
    list", raised ``WriteUncertain`` after the claim and latched the site.
    """

    def price_list_gateway(self, selling):
        stub = AmendmentStub()
        for field, value in (
            ("name", "Standard Selling"),
            ("currency", "USD"),
            ("selling", selling),
        ):
            stub.db.values[("Price List", "Standard Selling", field)] = value
        return self.gateway(stub)

    def price_list_expected(self):
        return {
            "identifier": "Standard Selling",
            "payload": {"price_list_name": "Standard Selling"},
        }

    def test_the_observed_price_list_failure_is_fixed(self):
        facts = self.price_list_gateway(1).native_admin_master_facts(
            "price_list", "Standard Selling"
        )

        self.assertEqual(facts["selling"], 1)
        self.assertIsNotNone(facts["selling"])
        self.assertIsNone(
            admin_contract.master_facts_problem(
                "price_list", facts, self.price_list_expected()
            )
        )

    def test_a_real_bool_is_accepted_identically(self):
        facts = self.price_list_gateway(True).native_admin_master_facts(
            "price_list", "Standard Selling"
        )

        self.assertIs(facts["selling"], True)
        self.assertIsNone(
            admin_contract.master_facts_problem(
                "price_list", facts, self.price_list_expected()
            )
        )

    def test_a_non_selling_list_is_still_refused(self):
        facts = self.price_list_gateway(0).native_admin_master_facts(
            "price_list", "Standard Selling"
        )

        self.assertEqual(
            admin_contract.master_facts_problem(
                "price_list", facts, self.price_list_expected()
            ),
            "created price list is not a selling list",
        )

    def test_every_master_type_round_trips_its_installed_types(self):
        cases = (
            ("uom", "Nos", (), {}, {"name": "Nos"}),
            ("supplier_group", "SIT Suppliers", (), {}, {"name": "SIT Suppliers"}),
            (
                "warehouse",
                "SIT Pharmacy",
                (("company", "SIT Synthetic Company"),),
                {},
                {"name": "SIT Pharmacy", "company": "SIT Synthetic Company"},
            ),
            (
                "supplier",
                "SIT Supplier",
                (("supplier_name", "SIT Supplier"),),
                {},
                {"name": "SIT Supplier", "supplier_name": "SIT Supplier"},
            ),
            (
                "item",
                "SIT-ITEM-1",
                (("item_code", "SIT-ITEM-1"),),
                {
                    "Item Price": [
                        {
                            "price_list": "Standard Selling",
                            "price_list_rate": 12.5,
                        }
                    ]
                },
                {
                    "name": "SIT-ITEM-1",
                    "item_code": "SIT-ITEM-1",
                    "price_list": "Standard Selling",
                    "price_list_rate": 12.5,
                },
            ),
            (
                "price_list",
                "Standard Selling",
                (("currency", "USD"), ("selling", 1)),
                {},
                {
                    "name": "Standard Selling",
                    "currency": "USD",
                    "selling": 1,
                },
            ),
            (
                "mode_of_payment",
                "Cash",
                (),
                {"Mode of Payment Account": [{"default_account": "Cash - SITCO"}]},
                {"name": "Cash", "account": "Cash - SITCO"},
            ),
            (
                "procedure_template",
                "HLC-PROC-1",
                (
                    ("template", "SIT Dressing"),
                    ("item_code", "SIT-ITEM-1"),
                    ("item", "SIT-ITEM-1"),
                    ("description", "SIT Dressing"),
                ),
                {},
                {
                    "name": "HLC-PROC-1",
                    "template": "SIT Dressing",
                    "item_code": "SIT-ITEM-1",
                    "item": "SIT-ITEM-1",
                    "description": "SIT Dressing",
                },
            ),
            (
                "practitioner",
                "HLC-PRAC-2026-00001",
                (
                    ("practitioner_name", "SIT Doctor"),
                    ("user_id", "sit.doc@example.invalid"),
                ),
                {},
                {
                    "name": "HLC-PRAC-2026-00001",
                    "practitioner_name": "SIT Doctor",
                    "user_id": "sit.doc@example.invalid",
                },
            ),
        )
        for master_type, name, values, tables, expected in cases:
            with self.subTest(master_type=master_type):
                stub = AmendmentStub()
                stub.db.values[(master_type_docname(master_type), name, "name")] = name
                for field, value in values:
                    stub.db.values[
                        (master_type_docname(master_type), name, field)
                    ] = value
                for doctype, rows in tables.items():
                    stub.all_rows[doctype] = [dict(row) for row in rows]

                facts = self.gateway(stub).native_admin_master_facts(
                    master_type, name
                )

                self.assertEqual(facts, expected)
                for field, value in expected.items():
                    self.assertIs(type(facts[field]), type(value), field)

    def test_an_unset_typed_column_stays_none(self):
        stub = AmendmentStub()
        stub.db.values[("Price List", "Standard Selling", "name")] = (
            "Standard Selling"
        )
        stub.db.values[("Price List", "Standard Selling", "currency")] = "USD"
        stub.db.values[("Price List", "Standard Selling", "selling")] = None

        facts = self.gateway(stub).native_admin_master_facts(
            "price_list", "Standard Selling"
        )

        self.assertIsNone(facts["selling"])

    def test_the_user_enabled_check_stays_an_int(self):
        stub = AmendmentStub()
        stub.db.values[("User", "sit.doc@example.invalid", "name")] = (
            "sit.doc@example.invalid"
        )
        stub.db.values[("User", "sit.doc@example.invalid", "enabled")] = 1

        facts = self.gateway(stub).native_admin_user_facts(
            "sit.doc@example.invalid"
        )

        self.assertEqual(facts["enabled"], 1)


class AdminRoleTableStub(AmendmentStub):
    """Models the installed User-roles child table (``Has Role``).

    On the site ``User.roles`` is a Table whose options are ``Has Role``, and
    every installed User role row carries parenttype ``User`` / parentfield
    ``roles``. The core ``User Role`` child doctype is *also* installed and
    holds zero rows, so a reader pointed at it returns ``[]`` without raising.
    """

    def __init__(self, *, user="sit.doc@example.invalid", roles=(), extra_rows=()):
        super().__init__(user=user)
        self.queries = []
        self.missing_doctypes = set()
        self.db.values[(admin_contract.USER_DOCTYPE, user, "name")] = user
        self.db.values[(admin_contract.USER_DOCTYPE, user, "enabled")] = 1
        self.child_rows = {
            admin_contract.USER_ROLES_DOCTYPE: [
                {
                    "name": f"{user}-role-{index}",
                    "parent": user,
                    "parenttype": admin_contract.USER_ROLES_PARENTTYPE,
                    "parentfield": admin_contract.USER_ROLES_PARENTFIELD,
                    "role": role,
                }
                for index, role in enumerate(roles)
            ]
            + [dict(row) for row in extra_rows],
            # Installed on the site and empty: the wrong table reads as no roles.
            "User Role": [],
        }

    def get_all(self, doctype, filters=None, limit=None, pluck=None, **kwargs):
        if doctype in self.missing_doctypes:
            raise KeyError(f"DocType {doctype} is not installed")
        filters = dict(filters or {})
        self.queries.append((doctype, filters))
        rows = [
            row
            for row in self.child_rows.get(doctype, [])
            if all(row.get(field) == value for field, value in filters.items())
        ]
        if pluck:
            return [row.get(pluck) for row in rows]
        return rows


class AdminUserRoleFactsTests(GatewayTestCase):
    """Review round 4: the user facts read the installed roles child table.

    The observed site failure: the reader asked ``User Role`` for the roles,
    which is installed and empty, so the post-claim compare saw ``roles: []``,
    said "created user roles differ from the request", raised ``WriteUncertain``
    after the claim and latched the site. Same class as the round-3 typed-facts
    bug, different root cause.
    """

    USER = "sit.doc@example.invalid"
    ROLES = ("Item Manager", "Laboratory User")

    def stub_and_gateway(self, roles=None, extra_rows=()):
        stub = AdminRoleTableStub(
            user=self.USER,
            roles=self.ROLES if roles is None else roles,
            extra_rows=extra_rows,
        )
        return stub, self.gateway(stub)

    def expected(self, roles=None):
        return {
            "identifier": self.USER,
            "roles": tuple(self.ROLES if roles is None else roles),
            "practitioner": None,
        }

    def test_the_reader_queries_the_installed_child_table(self):
        stub, gateway = self.stub_and_gateway()

        gateway.native_admin_user_facts(self.USER)

        self.assertEqual(
            stub.queries,
            [
                (
                    admin_contract.USER_ROLES_DOCTYPE,
                    {
                        "parent": self.USER,
                        "parenttype": admin_contract.USER_ROLES_PARENTTYPE,
                        "parentfield": admin_contract.USER_ROLES_PARENTFIELD,
                    },
                )
            ],
        )

    def test_a_roles_round_trip_passes_the_contract(self):
        stub, gateway = self.stub_and_gateway()

        facts = gateway.native_admin_user_facts(self.USER)

        self.assertEqual(facts["roles"], list(self.ROLES))
        self.assertIsNone(
            admin_contract.user_facts_problem(facts, self.expected())
        )

    def test_a_row_for_another_parenttype_is_not_read(self):
        stub, gateway = self.stub_and_gateway(
            extra_rows=(
                {
                    "name": "role-profile-row",
                    "parent": self.USER,
                    "parenttype": "Role Profile",
                    "parentfield": "roles",
                    "role": "System Manager",
                },
            )
        )

        facts = gateway.native_admin_user_facts(self.USER)

        self.assertEqual(facts["roles"], list(self.ROLES))
        self.assertIsNone(
            admin_contract.user_facts_problem(facts, self.expected())
        )

    def test_the_empty_core_user_role_table_is_the_observed_defect(self):
        stub, gateway = self.stub_and_gateway()

        # Exactly what the site shows: the wrong table exists and is empty.
        self.assertEqual(
            stub.get_all("User Role", filters={"parent": self.USER}, pluck="role"),
            [],
        )
        facts = dict(
            gateway.native_admin_user_facts(self.USER), roles=[]
        )
        self.assertEqual(
            admin_contract.user_facts_problem(facts, self.expected()),
            "created user roles differ from the request",
        )

    def test_a_missing_child_doctype_still_fails_closed(self):
        stub, gateway = self.stub_and_gateway()
        stub.missing_doctypes.add(admin_contract.USER_ROLES_DOCTYPE)

        with self.assertRaises(patient_write.WriteUncertain):
            gateway.native_admin_user_facts(self.USER)


class EncounterAmendFactsTypingTests(GatewayTestCase):
    """Review round 3 sweep: the encounter facts keep dates as ISO text.

    ``modified`` columns come back from frappe as ``datetime`` objects on both
    sides, and ``_text_value`` renders them with ``isoformat()``, so the compare
    is type-consistent (no ``None`` from a non-string).
    """

    def test_datetime_facts_are_iso_on_both_sides(self):
        moment = datetime(2026, 11, 16, 9, 30, 15, 123456)
        stub = AmendmentStub()
        stub.db.values[
            ("Clinical Note", "HLC-NOTE-ADD-1", "note")
        ] = "Addendum text"
        stub.db.values[("Patient Encounter", "HLC-ENC-1", "modified")] = moment
        stub.db.values[
            ("Clinical Note", "HLC-NOTE-1", "modified")
        ] = moment

        facts = self.gateway(stub).native_encounter_amend_facts(
            "HLC-ENC-1",
            "HLC-NOTE-ADD-1",
            "Addendum text",
            "HLC-NOTE-1",
            # The endpoint hands over the detail value, which the reader
            # normalises with ``str()`` (space-separated), not isoformat().
            str(moment),
        )

        self.assertEqual(facts["encounter_modified"], moment.isoformat())
        self.assertEqual(
            facts["clinical_note_modified"], str(moment)
        )
        self.assertIs(facts["addendum_created"], True)
        # The reader echoes the preimage it was handed instead of re-reading it;
        # the contract compares the re-read ``clinical_note_modified`` against
        # that preimage, which is the check that matters here.
        self.assertEqual(
            facts["original_note_modified"], str(moment)
        )
        self.assertEqual(
            encounter_contract.amend_postcondition_problem(
                encounter_modified=facts["encounter_modified"],
                expected_encounter_modified=moment.isoformat(),
                clinical_note_modified=facts["clinical_note_modified"],
                original_note_modified=facts["original_note_modified"],
                addendum_created=facts["addendum_created"],
                expected_note=facts["addendum_note"],
            ),
            None,
        )


class ProcedureDefaultFieldTests(GatewayTestCase):
    """Real frappe: ``docstatus`` is a default column, not a DocField.

    The pre-D1-resolution implementation read ``get_meta(doctype).fields`` only,
    which is what this stub models, so it reports
    ``missing field Stock Entry.docstatus`` and denies the whole procedure family
    on a real site. The gateway resolver also unions the installed
    ``default_fields``, so it passes without weakening the closed-world check.
    """

    DEFAULT_FIELDS = (
        "name",
        "owner",
        "creation",
        "modified",
        "modified_by",
        "docstatus",
        "idx",
    )

    def stub_with_real_meta(self):
        from clinic_ui.core import procedure_contract

        stub = AmendmentStub()
        stub.default_fields = set(self.DEFAULT_FIELDS)
        for doctype, fields in procedure_contract.REQUIRED_FIELDS.items():
            names = {candidates[0] for candidates in fields.values()}
            stub.meta_fields[doctype] = sorted(
                names - set(self.DEFAULT_FIELDS)
            )
        return stub

    def test_the_meta_only_resolution_is_the_defect(self):
        from clinic_ui.core import procedure_contract

        stub = self.stub_with_real_meta()
        meta_only = {
            doctype: set(stub.meta_fields[doctype])
            for doctype in procedure_contract.REQUIRED_FIELDS
        }

        self.assertEqual(
            procedure_contract.install_problem(meta_only),
            "missing field Stock Entry.docstatus",
        )
        self.assertNotIn(
            "docstatus", meta_only["Stock Entry"]
        )

    def test_the_gateway_preflight_admits_the_default_column(self):
        stub = self.stub_with_real_meta()
        gateway = self.gateway(stub)

        self.assertIsNone(gateway.native_procedure_install_problem())
        self.assertIn(
            "docstatus", gateway._procedure_meta_fields()["Stock Entry"]
        )
        # The Table field is still admitted through the DocField rows.
        self.assertIn(
            "items", gateway._procedure_meta_fields()["Clinical Procedure"]
        )

    def test_a_default_column_does_not_admit_an_unknown_field(self):
        stub = self.stub_with_real_meta()
        stub.meta_fields["Stock Entry"] = [
            name
            for name in stub.meta_fields["Stock Entry"]
            if name != "stock_entry_type"
        ]

        self.assertEqual(
            self.gateway(stub).native_procedure_install_problem(),
            "missing field Stock Entry.stock_entry_type",
        )


class CompanyResolutionTests(GatewayTestCase):
    """D2-i: the company resolver uses frappe-16 APIs and fails closed."""

    def test_global_defaults_wins(self):
        stub = AmendmentStub()
        stub.db.single_values[("Global Defaults", "default_company")] = "GD Company"
        stub.all_rows["Company"] = ["Other Company"]

        self.assertEqual(
            self.gateway(stub).native_admin_company(), "GD Company"
        )

    def test_the_database_default_is_the_frappe_16_fallback(self):
        stub = AmendmentStub()
        stub.db.defaults["company"] = "Defaults Company"

        self.assertEqual(
            self.gateway(stub).native_admin_company(), "Defaults Company"
        )

    def test_exactly_one_company_row_is_the_deterministic_fallback(self):
        stub = AmendmentStub()
        stub.all_rows["Company"] = ["SIT Synthetic Company"]

        self.assertEqual(
            self.gateway(stub).native_admin_company(), "SIT Synthetic Company"
        )

    def test_two_company_rows_without_a_default_do_not_resolve(self):
        stub = AmendmentStub()
        stub.all_rows["Company"] = ["One", "Two"]

        self.assertIsNone(self.gateway(stub).native_admin_company())

    def test_nothing_resolvable_is_none_and_the_mutation_refuses(self):
        stub = AmendmentStub()
        gateway = self.gateway(stub)

        self.assertIsNone(gateway.native_admin_company())
        with self.assertRaises(patient_write.WriteUncertain):
            gateway._admin_company()

    def test_the_prerequisites_carry_the_resolved_state(self):
        stub = AmendmentStub()
        stub.all_rows["Company"] = ["SIT Synthetic Company"]
        stub.all_rows["UOM"] = ["Nos"]
        stub.all_rows["Supplier Group"] = ["All Supplier Groups"]
        stub.db.counts = {"UOM": 1, "Supplier Group": 1}

        self.assertEqual(
            self.gateway(stub).native_admin_prerequisites(),
            {
                "company": "SIT Synthetic Company",
                "uom_rows": 1,
                "supplier_group": "All Supplier Groups",
                "supplier_group_rows": 1,
                "practitioner_links": {},
            },
        )


class PractitionerMasterTests(GatewayTestCase):
    """Amendment A1.1: the practitioner master kind."""

    def stub_with_practitioner_meta(self, **counts):
        session_stub = AmendmentStub()
        session_stub.all_rows["Company"] = ["SIT Synthetic Company"]
        meta = [
            ("first_name", "Data", 1, None),
            ("status", "Select", 1, None),
            ("practitioner_type", "Select", 0, None),
            ("gender", "Link", 0, "Gender"),
            ("user_id", "Link", 0, "User"),
        ]
        session_stub.get_meta = lambda doctype: types.SimpleNamespace(
            fields=[
                types.SimpleNamespace(
                    fieldname=fieldname,
                    fieldtype=fieldtype,
                    reqd=reqd,
                    options=options,
                )
                for fieldname, fieldtype, reqd, options in meta
            ]
        )
        session_stub.db.counts.update(counts)
        return session_stub

    def test_no_mandatory_link_fields_means_no_link_prerequisite(self):
        stub = self.stub_with_practitioner_meta()
        self.assertEqual(
            self.gateway(stub).native_admin_mandatory_link_rows(
                "Healthcare Practitioner"
            ),
            {},
        )

    def test_a_mandatory_link_count_is_read_from_the_installed_meta(self):
        stub = self.stub_with_practitioner_meta(Gender=1)
        stub.get_meta = lambda doctype: types.SimpleNamespace(
            fields=[
                types.SimpleNamespace(
                    fieldname="gender",
                    fieldtype="Link",
                    reqd=1,
                    options="Gender",
                )
            ]
        )
        self.assertEqual(
            self.gateway(stub).native_admin_mandatory_link_rows(
                "Healthcare Practitioner"
            ),
            {"gender": 1},
        )

    def test_the_practitioner_insert_carries_the_supplied_name_and_user(self):
        stub = self.stub_with_practitioner_meta()
        gateway = self.gateway(stub)

        name = gateway.create_native_admin_master(
            "practitioner",
            {"practitioner_name": "SIT Doctor", "user": "sit.doc@example.invalid"},
        )

        created = stub.dict_docs[-1]
        self.assertEqual(created["doctype"], "Healthcare Practitioner")
        self.assertEqual(created["first_name"], "SIT Doctor")
        self.assertEqual(created["user_id"], "sit.doc@example.invalid")
        self.assertNotIn("status", created)
        self.assertEqual(name, "HEALTHCARE PRACTITIONER-NEW-1")

    def test_the_practitioner_insert_omits_the_user_link_when_empty(self):
        stub = self.stub_with_practitioner_meta()
        gateway = self.gateway(stub)

        gateway.create_native_admin_master(
            "practitioner", {"practitioner_name": "SIT Doctor", "user": None}
        )

        created = stub.dict_docs[-1]
        self.assertNotIn("user_id", created)

    def test_the_linked_user_is_read_for_the_exactly_once_check(self):
        stub = self.stub_with_practitioner_meta()
        lookup = (
            "Healthcare Practitioner",
            "filter",
            (("user_id", "sit.doc@example.invalid"),),
        )
        stub.db.values[lookup] = None
        gateway = self.gateway(stub)
        self.assertIsNone(
            gateway.native_admin_practitioner_for_user("sit.doc@example.invalid")
        )

        stub.db.values[lookup] = "HLC-PRAC-2026-00001"
        self.assertEqual(
            gateway.native_admin_practitioner_for_user("sit.doc@example.invalid"),
            "HLC-PRAC-2026-00001",
        )
        self.assertEqual(
            gateway.native_admin_practitioner_for_user("sit.doc@example.invalid"),
            "HLC-PRAC-2026-00001",
        )

    def test_the_user_enabled_flag_is_read_for_the_link(self):
        stub = self.stub_with_practitioner_meta()
        stub.db.values[("User", "sit.doc@example.invalid", "enabled")] = 1
        gateway = self.gateway(stub)
        self.assertTrue(
            gateway.native_admin_user_enabled("sit.doc@example.invalid")
        )
        self.assertFalse(gateway.native_admin_user_enabled("nobody@example.invalid"))


class SupplierGroupResolutionTests(GatewayTestCase):
    def test_the_installed_root_group_is_preferred(self):
        stub = AmendmentStub()
        stub.db.existing.add(("Supplier Group", "All Supplier Groups"))
        stub.all_rows["Supplier Group"] = ["All Supplier Groups", "Local"]

        self.assertEqual(
            self.gateway(stub).native_admin_supplier_group(),
            "All Supplier Groups",
        )

    def test_exactly_one_group_is_the_deterministic_answer(self):
        stub = AmendmentStub()
        stub.all_rows["Supplier Group"] = ["SIT Suppliers"]

        self.assertEqual(
            self.gateway(stub).native_admin_supplier_group(), "SIT Suppliers"
        )

    def test_ambiguity_and_absence_do_not_resolve(self):
        stub = AmendmentStub()
        stub.all_rows["Supplier Group"] = ["One", "Two"]

        self.assertIsNone(self.gateway(stub).native_admin_supplier_group())

        stub.all_rows["Supplier Group"] = []
        self.assertIsNone(self.gateway(stub).native_admin_supplier_group())


class AdminMasterCreateTests(GatewayTestCase):
    def stub(self):
        stub = AmendmentStub()
        stub.db.single_values[("Global Defaults", "default_company")] = "COMP-1"
        stub.db.values[("Company", "COMP-1", "default_currency")] = "USD"
        return stub

    def test_a_uom_row_carries_only_its_name(self):
        stub = self.stub()

        name = self.gateway(stub).create_native_admin_master(
            "uom", {"uom_name": "Nos"}
        )

        self.assertEqual(name, "UOM-NEW-1")
        self.assertEqual(
            stub.dict_docs[0],
            {"doctype": "UOM", "uom_name": "Nos"},
        )

    def test_a_supplier_group_row_carries_only_its_name(self):
        stub = self.stub()

        name = self.gateway(stub).create_native_admin_master(
            "supplier_group", {"supplier_group_name": "SIT Suppliers"}
        )

        self.assertEqual(name, "SUPPLIER GROUP-NEW-1")
        self.assertEqual(
            stub.dict_docs[0],
            {
                "doctype": "Supplier Group",
                "supplier_group_name": "SIT Suppliers",
            },
        )

    def test_a_supplier_row_links_the_resolved_group(self):
        stub = self.stub()
        stub.all_rows["Supplier Group"] = ["SIT Suppliers"]

        self.gateway(stub).create_native_admin_master(
            "supplier", {"supplier_name": "SIT Supplier"}
        )

        self.assertEqual(
            stub.dict_docs[0]["supplier_group"], "SIT Suppliers"
        )

    def test_a_price_list_still_reads_the_company_currency(self):
        stub = self.stub()

        self.gateway(stub).create_native_admin_master(
            "price_list", {"price_list_name": "Standard Selling"}
        )

        self.assertEqual(stub.dict_docs[0]["currency"], "USD")

    def test_the_new_kinds_need_no_currency(self):
        stub = self.stub()
        stub.db.values.pop(("Company", "COMP-1", "default_currency"))

        self.gateway(stub).create_native_admin_master(
            "uom", {"uom_name": "Nos"}
        )

        self.assertEqual(len(stub.dict_docs), 1)

    def test_the_new_kind_facts_are_the_row_name(self):
        stub = self.stub()
        stub.db.values[("UOM", "Nos", "name")] = "Nos"
        stub.db.values[("Supplier Group", "SIT", "name")] = "SIT"

        gateway = self.gateway(stub)
        self.assertEqual(
            gateway.native_admin_master_facts("uom", "Nos"), {"name": "Nos"}
        )
        self.assertEqual(
            gateway.native_admin_master_facts("supplier_group", "SIT"),
            {"name": "SIT"},
        )


class InstalledUserDoc:
    """Controller-like ``User``: named by email, unique username, insert hooks."""

    def __init__(self, values, stub):
        self._stub = stub
        self.name = None
        self.doctype = values["doctype"]
        self.flags = types.SimpleNamespace()
        for field, value in values.items():
            setattr(self, field, value)

    def insert(self):
        self._stub.controller_calls.append("user_insert")
        email = getattr(self, "email", "")
        if "@" not in email or " " in email:
            raise ValueError("invalid email")
        if email in self._stub.existing_emails:
            raise ValueError("duplicate user")
        username = getattr(self, "username", None)
        if username and username in self._stub.existing_usernames:
            raise ValueError("duplicate username")
        self.name = email
        self._stub.existing_emails.add(email)
        if username:
            self._stub.existing_usernames.add(username)


class InstalledPractitionerDoc:
    """Controller-like ``Healthcare Practitioner`` with a native save."""

    def __init__(self, *, user_id="", can_write=True, events=None):
        self.name = "HLC-PRAC-1"
        self.user_id = user_id
        self.can_write = can_write
        self.events = events if events is not None else []
        self.saved = False

    def check_permission(self, ptype):
        self.events.append(("check_permission", ptype))
        if not self.can_write:
            raise FakePermissionError("Not permitted")

    def save(self):
        self.events.append("practitioner_save")
        self.saved = True


class AdminUserControllerStub(types.SimpleNamespace):
    """Fake Frappe for the real admin user-create adapter.

    ``db.set_value`` raises, so any write that bypasses the document save
    lifecycle fails the test rather than passing silently.
    """

    def __init__(self, *, practitioner_user="", can_write=True):
        super().__init__()
        self.session = types.SimpleNamespace(user="adm@example.test")
        self.local = types.SimpleNamespace(
            site="marley.test",
            session={},
            response={},
            response_headers={},
            flags=types.SimpleNamespace(),
        )
        self.PermissionError = FakePermissionError
        self.controller_calls = []
        self.dict_docs = []
        self.created_docs = []
        self.existing_emails = set()
        self.existing_usernames = set()
        self.permissions = set()
        self.events = []
        self.practitioner = InstalledPractitionerDoc(
            user_id=practitioner_user,
            can_write=can_write,
            events=self.events,
        )
        self.db = self

    def get_doc(self, doctype, name=None, **kwargs):
        if isinstance(doctype, dict):
            self.dict_docs.append(dict(doctype))
            document = InstalledUserDoc(doctype, self)
            self.created_docs.append(document)
            return document
        self.controller_calls.append(("get_doc", doctype, name))
        return self.practitioner

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        self.controller_calls.append(("has_permission", doctype, ptype))
        return (doctype, ptype) in self.permissions

    def set_value(self, *args, **kwargs):
        raise AssertionError("the practitioner link must use the document save")

    def get_value(self, doctype, name, fieldname=None, **kwargs):
        self.controller_calls.append(("get_value", doctype, name, fieldname))
        if fieldname == "user_id":
            return self.practitioner.user_id or None
        return None


class AdminUserCreateAdapterTests(GatewayTestCase):
    def payload(self, **overrides):
        values = {
            "email": "sit.pha@example.invalid",
            "username": None,
            "full_name": "SIT Pharmacist",
            "roles": ["Stock Manager"],
            "practitioner": None,
            "password": "caller-supplied-secret-value",
        }
        values.update(overrides)
        return values

    def test_the_user_is_inserted_with_the_installed_naming_and_fields(self):
        stub = AdminUserControllerStub()

        name = self.gateway(stub).create_native_admin_user(self.payload())

        self.assertEqual(name, "sit.pha@example.invalid")
        self.assertIn("user_insert", stub.controller_calls)
        sent = stub.dict_docs[0]
        self.assertEqual(sent["email"], "sit.pha@example.invalid")
        self.assertEqual(sent["full_name"], "SIT Pharmacist")
        self.assertEqual(sent["new_password"], "caller-supplied-secret-value")
        self.assertEqual(sent["send_welcome_email"], 0)
        self.assertEqual(sent["roles"], [{"role": "Stock Manager"}])
        self.assertNotIn("username", sent)
        self.assertNotIn("ignore_permissions", sent)

    def test_a_distinct_username_is_an_installed_field_not_an_email(self):
        stub = AdminUserControllerStub()

        name = self.gateway(stub).create_native_admin_user(
            self.payload(username="sit.pha")
        )

        self.assertEqual(name, "sit.pha@example.invalid")
        self.assertEqual(stub.dict_docs[0]["username"], "sit.pha")
        self.assertEqual(stub.dict_docs[0]["email"], "sit.pha@example.invalid")

    def test_a_duplicate_username_fails_in_the_controller(self):
        stub = AdminUserControllerStub()
        stub.existing_usernames.add("sit.pha")

        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).create_native_admin_user(
                self.payload(username="sit.pha")
            )

    def test_the_practitioner_link_uses_native_permission_and_save(self):
        stub = AdminUserControllerStub()

        self.gateway(stub).create_native_admin_user(
            self.payload(practitioner="HLC-PRAC-1")
        )

        self.assertEqual(
            stub.events,
            [("check_permission", "write"), "practitioner_save"],
        )
        self.assertEqual(stub.practitioner.user_id, "sit.pha@example.invalid")
        self.assertTrue(stub.practitioner.saved)

    def test_an_already_linked_practitioner_is_never_overwritten(self):
        stub = AdminUserControllerStub(
            practitioner_user="sit.doc@example.invalid"
        )

        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).link_native_admin_practitioner(
                "HLC-PRAC-1", "sit.pha@example.invalid"
            )

        self.assertEqual(
            stub.practitioner.user_id, "sit.doc@example.invalid"
        )
        self.assertNotIn("practitioner_save", stub.events)

    def test_a_practitioner_write_denial_is_raised_before_the_save(self):
        stub = AdminUserControllerStub(can_write=False)

        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).link_native_admin_practitioner(
                "HLC-PRAC-1", "sit.pha@example.invalid"
            )

        self.assertNotIn("practitioner_save", stub.events)
        self.assertFalse(stub.practitioner.saved)

    def test_the_practitioner_write_authority_is_read_before_the_mutation(self):
        stub = AdminUserControllerStub(can_write=False)

        self.assertFalse(
            self.gateway(stub).native_admin_can_write_practitioner("HLC-PRAC-1")
        )

    def test_an_already_linked_practitioner_is_detected_without_the_user(self):
        stub = AdminUserControllerStub(
            practitioner_user="sit.doc@example.invalid"
        )

        self.assertTrue(
            self.gateway(stub).native_admin_practitioner_is_linked("HLC-PRAC-1")
        )
        self.assertNotIn("sit.doc@example.invalid", str(stub.events))

    def test_the_username_duplicate_check_reads_the_installed_field(self):
        stub = AdminUserControllerStub()
        stub.get_value = lambda doctype, filters, fieldname=None, **kw: (
            "sit.pha" if filters == {"username": "sit.pha"} else None
        )

        self.assertTrue(self.gateway(stub).native_admin_username_exists("sit.pha"))
        self.assertFalse(self.gateway(stub).native_admin_username_exists("sit.nur"))

    def test_an_unreadable_username_check_fails_closed(self):
        stub = AdminUserControllerStub()

        def boom(*args, **kwargs):
            raise RuntimeError("db down")

        stub.get_value = boom

        self.assertTrue(self.gateway(stub).native_admin_username_exists("sit.pha"))

    def test_the_installed_email_policy_returns_an_empty_string_for_invalid(self):
        """Real Frappe shape: ``''`` invalid, the email string when valid."""

        stub = AdminUserControllerStub()
        stub.utils = types.SimpleNamespace(
            validate_email_address=lambda email: email if "@" in email else ""
        )

        gateway = self.gateway(stub)
        self.assertEqual(
            gateway.native_admin_email_problem("not-an-email"),
            "email is invalid",
        )
        self.assertIsNone(
            gateway.native_admin_email_problem("sit.pha@example.invalid")
        )

    def test_an_installed_validator_that_raises_is_a_preflight_problem(self):
        stub = AdminUserControllerStub()
        stub.utils = types.SimpleNamespace(
            validate_email_address=lambda email: (_ for _ in ()).throw(
                ValueError("invalid")
            )
        )

        self.assertEqual(
            self.gateway(stub).native_admin_email_problem("bad@"),
            "email is invalid",
        )

    def test_a_missing_installed_email_policy_is_not_provable(self):
        stub = AdminUserControllerStub()

        self.assertIsNone(
            self.gateway(stub).native_admin_email_problem("sit.pha@example.invalid")
        )

    def test_the_user_permission_create_authority_is_read_natively(self):
        stub = AdminUserControllerStub()
        stub.permissions.add(("User Permission", "create"))

        self.assertTrue(self.gateway(stub).native_admin_can_create_user_permission())

        stub.permissions.discard(("User Permission", "create"))
        self.assertFalse(
            self.gateway(stub).native_admin_can_create_user_permission()
        )


class ProcedureCreateAdapterTests(GatewayTestCase):
    REQUEST = "HLC-SR-1"

    def stub(self, *, permitted=True):
        stub = AmendmentStub()
        if permitted:
            stub.permissions.add(("Service Request", "read"))
            stub.permissions.add(("Clinical Procedure", "create"))
            stub.permissions.add(("Clinical Procedure", "submit"))
        doc = AmendmentDoc(
            "Service Request",
            {
                "name": self.REQUEST,
                "docstatus": 1,
                "template_dt": "Clinical Procedure Template",
                "template_dn": "TPL-1",
                "patient": "PAT-1",
                "patient_name": "Patient One",
                "patient_gender": "Female",
                "patient_age_data": "36Y",
                "practitioner": "HLC-PRAC-1",
                "company": "COMP-1",
                "billing_status": "Not Billed",
                "occurrence_date": "2026-11-16",
                "occurrence_time": "09:00:00",
                "inpatient_record": None,
            },
        )
        stub.db.existing.add(("Service Request", self.REQUEST))
        stub.docs[("Service Request", self.REQUEST)] = doc
        stub.db.values[
            ("Clinical Procedure Template", "TPL-1", "consume_stock")
        ] = 0
        return stub

    def test_the_pre_fence_reader_reports_an_absent_request(self):
        stub = self.stub()

        self.assertEqual(
            self.gateway(stub).native_service_request_target("MISSING"),
            {
                "exists": False,
                "docstatus": None,
                "template_dt": None,
                "template_dn": None,
                "patient": None,
                "procedure_template": None,
                "procedure_exists": False,
                "consume_stock": False,
            },
        )

    def test_the_pre_fence_reader_denies_unreadable_requests(self):
        stub = self.stub(permitted=False)

        self.assertIsNone(
            self.gateway(stub).native_service_request_target(self.REQUEST)
        )

    def test_the_pre_fence_reader_returns_the_decisions(self):
        stub = self.stub()

        values = self.gateway(stub).native_service_request_target(self.REQUEST)

        self.assertTrue(values["exists"])
        self.assertEqual(values["docstatus"], 1)
        self.assertEqual(values["patient"], "PAT-1")
        self.assertEqual(values["procedure_template"], "TPL-1")
        self.assertFalse(values["procedure_exists"])
        self.assertFalse(values["consume_stock"])

    def test_an_existing_procedure_for_the_request_is_reported(self):
        stub = self.stub()
        stub.db.existing.add(
            (
                "Clinical Procedure",
                "filter",
                (("service_request", self.REQUEST),),
            )
        )

        values = self.gateway(stub).native_service_request_target(self.REQUEST)

        self.assertTrue(values["procedure_exists"])

    def test_the_lock_reads_the_request_for_update(self):
        stub = self.stub()

        state = self.gateway(stub).lock_service_request_target(self.REQUEST)

        self.assertIs(state.doc, stub.docs[("Service Request", self.REQUEST)])
        self.assertEqual(state.procedure_template, "TPL-1")

    def test_the_creation_inserts_and_submits_the_mapped_row(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        state = gateway.lock_service_request_target(self.REQUEST)

        created = gateway.create_native_procedure(state)

        self.assertEqual(created, "CLINICAL PROCEDURE-NEW-1")
        body = stub.dict_docs[0]
        self.assertEqual(body["doctype"], "Clinical Procedure")
        self.assertEqual(body["patient"], "PAT-1")
        self.assertEqual(body["procedure_template"], "TPL-1")
        self.assertEqual(body["service_request"], self.REQUEST)
        self.assertEqual(body["company"], "COMP-1")
        self.assertEqual(body["patient_sex"], "Female")
        self.assertEqual(body["patient_age"], "36Y")
        self.assertEqual(body["practitioner"], "HLC-PRAC-1")
        self.assertEqual(body["start_date"], "2026-11-16")
        self.assertEqual(body["start_time"], "09:00:00")
        self.assertEqual(body["consume_stock"], 0)
        self.assertEqual(body["invoiced"], 0)

    def test_the_creation_submits_after_the_insert(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        state = gateway.lock_service_request_target(self.REQUEST)

        gateway.create_native_procedure(state)

        self.assertEqual(len(stub.created_docs), 1)
        document = stub.created_docs[0]
        self.assertTrue(document.inserted)
        self.assertTrue(document.submitted)
        self.assertIs(document.inserted_before_submit, True)
        self.assertEqual(document.docstatus, 1)

    def test_the_creation_facts_are_read_back(self):
        stub = self.stub()
        stub.db.values[("Clinical Procedure", "HLC-CPR-1", "name")] = "HLC-CPR-1"
        stub.db.values[("Clinical Procedure", "HLC-CPR-1", "patient")] = "PAT-1"
        stub.db.values[
            ("Clinical Procedure", "HLC-CPR-1", "procedure_template")
        ] = "TPL-1"
        stub.db.values[
            ("Clinical Procedure", "HLC-CPR-1", "service_request")
        ] = self.REQUEST
        stub.db.values[("Clinical Procedure", "HLC-CPR-1", "docstatus")] = 1
        stub.db.values[("Clinical Procedure", "HLC-CPR-1", "status")] = "Pending"

        self.assertEqual(
            self.gateway(stub).native_procedure_create_facts("HLC-CPR-1"),
            {
                "name": "HLC-CPR-1",
                "patient": "PAT-1",
                "procedure_template": "TPL-1",
                "service_request": self.REQUEST,
                "docstatus": 1,
                "status": "Pending",
            },
        )

    def test_a_creation_insert_failure_is_uncertain(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        state = gateway.lock_service_request_target(self.REQUEST)

        def boom():
            raise RuntimeError("insert rejected")

        stub.get_doc = boom
        with self.assertRaises(patient_write.WriteUncertain):
            gateway.create_native_procedure(state)


class GatewayAdditiveSurfaceTests(unittest.TestCase):
    """The amendment adds method names; nothing on either family was removed."""

    ADMIN_SURFACE = frozenset(
        {
            "native_admin_install_problem",
            "native_admin_can_write",
            "native_admin_can_create",
            "native_admin_missing_roles",
            "native_admin_user_exists",
            "native_admin_username_exists",
            "native_admin_email_problem",
            "native_admin_practitioner_exists",
            "native_admin_practitioner_is_linked",
            "native_admin_can_write_practitioner",
            "link_native_admin_practitioner",
            "native_admin_master_exists",
            "native_admin_item_price_exists",
            "native_admin_prerequisites",
            "native_admin_company",
            "native_admin_supplier_group",
            "native_admin_master_rows",
            "create_native_admin_user",
            "create_native_admin_master",
            "native_admin_user_facts",
            "native_admin_master_facts",
            "lookup_admin_receipt",
            "list_committed_admin_receipts",
            "acknowledge_admin_write",
            "admin_write_fence",
        }
    )
    PROCEDURE_SURFACE = frozenset(
        {
            "native_procedure_boundary_problem",
            "native_procedure_install_problem",
            "native_procedure_can_write",
            "native_procedure_can_create",
            "native_procedure_action_detail",
            "native_procedure_create_facts",
            "native_procedure_from_create_receipt",
            "native_service_request_target",
            "lock_service_request_target",
            "lock_procedure_target",
            "create_native_procedure",
            "complete_native_procedure",
            "native_procedure_complete_facts",
            "native_stock_ledger_baseline",
            "lookup_procedure_receipt",
            "list_committed_procedure_receipts",
            "acknowledge_procedure_write",
            "procedure_write_fence",
            "receipt_stock_entry",
        }
    )

    def test_the_admin_family_surface_is_the_reviewed_set(self):
        for name in self.ADMIN_SURFACE:
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(frappe_gateway.Gateway, name, None))
                )

    def test_the_procedure_family_surface_is_the_reviewed_set(self):
        for name in self.PROCEDURE_SURFACE:
            with self.subTest(name=name):
                self.assertTrue(
                    callable(getattr(frappe_gateway.Gateway, name, None))
                )

class EncounterShapeMeta:
    """The installed ``Patient Encounter`` family metadata.

    ``fields`` is what ``frappe.get_meta(doctype).fields`` carries (DocFields
    only, with the installed fieldtype and options). ``get_valid_columns()``
    returns the scalar database columns, which is the defect: it drops every
    Table field, so ``symptoms`` can never satisfy a column-only preflight.
    """

    def __init__(self, shapes, default_fields=(), restricted=False):
        self.shapes = dict(shapes)
        self.restricted = restricted
        self.accessible = [0]
        self.permlevels = {}
        self.masked = []
        self.fields = [
            types.SimpleNamespace(
                fieldname=name, fieldtype=shape[0], options=shape[1]
            )
            for name, shape in self.shapes.items()
        ]
        self.default_fields = list(default_fields)
        self.field_calls = []

    def get_valid_columns(self):
        return {
            name
            for name, (fieldtype, _options) in self.shapes.items()
            if fieldtype not in ("Table", "Table MultiSelect")
        }

    def get_permlevel_access(self, permission_type="read", parenttype=None, *, user=None):
        return list(self.accessible)

    def get_masked_fields(self, parenttype=None):
        # Actual v16 API returns field-definition copies for the session user.
        return [
            types.SimpleNamespace(fieldname=name) for name in self.masked
        ]

    def get_field(self, fieldname):
        self.field_calls.append(fieldname)
        if self.restricted:
            return None
        shape = self.shapes.get(fieldname)
        if shape is None:
            return None
        return types.SimpleNamespace(
            permlevel=self.permlevels.get(fieldname, 0),
            fieldname=fieldname,
            fieldtype=shape[0] or "Data",
            options=shape[1],
        )


class EncounterDB:
    """Read side of the encounter adapter: values, counts, note filters."""

    def __init__(self):
        self.values = {}
        self.counts = {}
        self.all_rows = {}
        self.all_calls = []
        self.all_field_calls = []
        self.value_calls = []
        self.all_errors = {}
        self.value_errors = {}
        self.value_overrides = {}
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1

    def get_value(self, doctype, name, fieldname=None, as_dict=False, **kwargs):
        self.value_calls.append((doctype, name, fieldname))
        error = self.value_errors.get((doctype, name))
        if error is not None:
            raise error
        if (doctype, name) in self.value_overrides:
            return self.value_overrides[(doctype, name)]
        if isinstance(fieldname, (list, tuple)):
            row = {
                field: self.values.get((doctype, name, field))
                for field in fieldname
            }
            return row if as_dict else tuple(row[field] for field in fieldname)
        return self.values.get((doctype, name, fieldname))

    def count(self, doctype, filters=None):
        return self.counts.get(doctype, 0)

    def get_all(self, doctype, filters=None, fields=None, pluck=None, **kwargs):
        self.all_calls.append((doctype, dict(filters or {})))
        self.all_field_calls.append((doctype, tuple(fields or ())))
        error = self.all_errors.get(doctype)
        if error is not None:
            raise error
        rows = list(self.all_rows.get(doctype, []))
        if pluck is not None:
            return [row.get(pluck) for row in rows]
        return rows


class EncounterDoc:
    def __init__(self, doctype, name=None):
        self.doctype = doctype
        self.name = name
        self.inserted = False
        self.children = []

    def append(self, fieldname, values):
        self.children.append((fieldname, dict(values)))

    def set(self, fieldname, value):
        setattr(self, fieldname, value)

    def insert(self):
        self.inserted = True
        if self.name is None:
            self.name = f"{self.doctype.upper()}-NEW-1"

    def save(self):
        self.saved = True

    def submit(self):
        self.docstatus = 1


class EncounterStub(types.SimpleNamespace):
    """Fake Frappe module carrying the installed encounter metadata."""

    DEFAULT_FIELDS = ("name", "owner", "creation", "modified", "modified_by", "docstatus", "idx")

    def __init__(self, shapes=None):
        super().__init__()
        self.shapes = shapes or encounter_shapes()
        self.metadata = {}
        self.session = types.SimpleNamespace(user="doctor@example.test", sid="sid")
        self.local = types.SimpleNamespace(site="marley.test", session={}, flags=types.SimpleNamespace())
        self.db = EncounterDB()
        self.new_docs = []
        self.docs = {}
        self.get_doc_calls = []
        self.permissions = set()
        self.permission_calls = []
        self.denied_documents = set()
        self.restricted_meta_fields = False
        self.utils = types.SimpleNamespace(
            nowdate=lambda: "2026-11-17", nowtime=lambda: "09:30:00"
        )

    def get_meta(self, doctype):
        if doctype not in self.metadata:
            shapes = self.shapes.get(doctype)
            if shapes is None:
                raise KeyError(doctype)
            self.metadata[doctype] = EncounterShapeMeta(
                shapes,
                default_fields=self.DEFAULT_FIELDS,
                restricted=doctype == "Patient Appointment"
                and self.restricted_meta_fields,
            )
        return self.metadata[doctype]

    def get_all(self, doctype, **kwargs):
        return self.db.get_all(doctype, **kwargs)

    def new_doc(self, doctype):
        doc = EncounterDoc(doctype)
        self.new_docs.append(doc)
        return doc

    def get_doc(self, doctype, name, **kwargs):
        self.get_doc_calls.append((doctype, name, dict(kwargs)))
        return self.docs.get((doctype, name))

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        self.permission_calls.append((doctype, ptype, doc, user))
        if (doctype, ptype) not in self.permissions:
            return False
        return (doctype, doc) not in self.denied_documents


def encounter_shapes():
    """Upstream healthcare v16.6.1 metadata, fieldname -> (fieldtype, options)."""

    return {
        "Patient Encounter": {
            "patient": ("Link", "Patient"),
            "practitioner": ("Link", "Healthcare Practitioner"),
            "encounter_date": ("Date", None),
            "encounter_time": ("Time", None),
            "appointment": ("Link", "Patient Appointment"),
            "appointment_type": ("Link", "Appointment Type"),
            "company": ("Link", "Company"),
            "symptoms": ("Table MultiSelect", "Patient Encounter Symptom"),
            "diagnosis": ("Table MultiSelect", "Patient Encounter Diagnosis"),
            "drug_prescription": ("Table", "Drug Prescription"),
            "lab_test_prescription": ("Table", "Lab Prescription"),
            "procedure_prescription": ("Table", "Procedure Prescription"),
            "status": ("Select", "\nOpen\nOrdered\nCompleted\nCancelled"),
        },
        "Clinical Note": {
            "patient": ("Link", "Patient"),
            "practitioner": ("Link", "Healthcare Practitioner"),
            "note": ("Text Editor", None),
            "reference_doc": ("Link", "DocType"),
            "reference_name": ("Dynamic Link", "reference_doc"),
        },
        "Vital Signs": {
            "patient": ("Link", "Patient"),
            "temperature": ("Float", None),
            "pulse": ("Int", None),
            "respiratory_rate": ("Int", None),
            "bp_systolic": ("Int", None),
            "bp_diastolic": ("Int", None),
            "height": ("Float", None),
            "weight": ("Float", None),
        },
        "Service Request": {"patient": ("Link", "Patient"), "status": ("Select", None)},
        "Patient Encounter Symptom": {"complaint": ("Link", "Complaint")},
        "Patient Encounter Diagnosis": {"diagnosis": ("Link", "Diagnosis")},
        "Drug Prescription": {
            "drug_code": ("Link", "Item"),
            "dosage": ("Link", "Prescription Dosage"),
            "period": ("Link", "Prescription Duration"),
        },
        "Lab Prescription": {"lab_test_code": ("Link", "Lab Test Template")},
        "Procedure Prescription": {
            "procedure": ("Link", "Clinical Procedure Template")
        },
        # Fields the linked-appointment read needs (cancel/no-show checks).
        "Patient Appointment": {
            "patient": ("Link", "Patient"),
            "appointment_date": ("Date", None),
            "appointment_time": ("Time", None),
            "status": ("Select", None),
            "appointment_type": ("Link", "Appointment Type"),
        },
    }


class EncounterInstalledMetadataTests(GatewayTestCase):
    """D2: the encounter preflight reads installed metadata, not DB columns.

    On the deployed image ``Patient Encounter.symptoms`` is a Table MultiSelect
    and ``Clinical Note`` links through ``reference_doc``/``reference_name``.
    ``get_valid_columns()`` drops both Table fields, so the column-only
    resolution denies the whole encounter family and the note writes set two
    attributes that are not installed.
    """

    def stub(self):
        return EncounterStub()

    def test_the_column_only_resolution_is_the_defect(self):
        stub = self.stub()
        columns = {
            doctype: stub.get_meta(doctype).get_valid_columns()
            for doctype in encounter_contract.REQUIRED_FIELDS
        }

        self.assertEqual(
            encounter_contract.install_problem(columns),
            "missing field Patient Encounter.symptoms",
        )

    def test_the_gateway_preflight_reads_the_installed_shapes(self):
        gateway = self.gateway(self.stub())

        self.assertIsNone(gateway.native_encounter_install_problem())
        fields = gateway._encounter_meta_fields()["Patient Encounter"]
        self.assertEqual(
            fields["symptoms"], ("Table MultiSelect", "Patient Encounter Symptom")
        )
        self.assertEqual(
            fields["drug_prescription"], ("Table", "Drug Prescription")
        )
        # The default columns are unioned in: ``modified`` has no DocField row.
        self.assertIn("modified", fields)

    def test_a_scalar_field_with_the_table_name_still_refuses(self):
        shapes = encounter_shapes()
        shapes["Patient Encounter"]["symptoms"] = ("Data", None)

        self.assertEqual(
            self.gateway(EncounterStub(shapes)).native_encounter_install_problem(),
            "field Patient Encounter.symptoms is not a table field",
        )

    def test_a_mistyped_note_reference_field_refuses_the_family(self):
        shapes = encounter_shapes()
        shapes["Clinical Note"]["reference_name"] = ("Data", None)

        self.assertEqual(
            self.gateway(EncounterStub(shapes)).native_encounter_install_problem(),
            "field Clinical Note.reference_name is not a dynamic link to "
            "reference_doc",
        )

    def test_a_uniformed_note_pair_refuses_the_family(self):
        shapes = encounter_shapes()
        shapes["Clinical Note"]["reference_name"] = (
            "Dynamic Link",
            "reference_doctype",
        )

        self.assertEqual(
            self.gateway(EncounterStub(shapes)).native_encounter_install_problem(),
            "field Clinical Note.reference_name is not a dynamic link to "
            "reference_doc",
        )

    def note_doc(self):
        return types.SimpleNamespace(
            name="HLC-ENC-1",
            patient="PAT-1",
            docstatus=0,
            status="Draft",
            appointment="APPT-1",
            modified="2026-11-17 09:00:00",
        )

    def readable_note_stub(self):
        """An empty stub whose user may read the linked Clinical Note."""

        stub = self.stub()
        stub.permissions.add(("Clinical Note", "read"))
        stub.db.all_rows["Clinical Note"] = [
            {
                "name": "HLC-NOTE-1",
                "modified": "2026-11-17 08:00:00",
                "note": "Assessment: Acne",
            }
        ]
        stub.db.values[("Clinical Note", "HLC-NOTE-1", "note")] = (
            "Assessment: Acne"
        )
        stub.db.values[("Clinical Note", "HLC-NOTE-1", "modified")] = (
            "2026-11-17 08:00:00"
        )
        return stub

    def note_queries(self, stub):
        return [
            call for call in stub.db.all_field_calls if call[0] == "Clinical Note"
        ]

    def note_content_reads(self, stub):
        return [
            call for call in stub.db.value_calls if call[0] == "Clinical Note"
        ]

    def test_the_note_read_filters_on_the_installed_reference_pair(self):
        stub = self.readable_note_stub()

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        note_calls = [
            call for call in stub.db.all_calls if call[0] == "Clinical Note"
        ]
        self.assertEqual(
            note_calls,
            [("Clinical Note", {"reference_doc": "Patient Encounter", "reference_name": "HLC-ENC-1"})],
        )
        self.assertEqual(values["clinical_note"], "HLC-NOTE-1")
        self.assertEqual(values["assessment"], "Acne")

    def test_the_note_content_is_query_only_after_the_read_checks(self):
        """The content column is never part of the query that finds the note."""

        stub = self.readable_note_stub()

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertEqual(
            self.note_queries(stub),
            [("Clinical Note", ("name",))],
        )
        self.assertEqual(
            self.note_content_reads(stub),
            [("Clinical Note", "HLC-NOTE-1", ["note", "modified"])],
        )
        self.assertEqual(values["clinical_note_modified"], "2026-11-17 08:00:00")
        permission_doctypes = [call[0] for call in stub.permission_calls]
        self.assertIn("Clinical Note", permission_doctypes)
        self.assertIn(
            ("Clinical Note", "read", "HLC-NOTE-1", "doctor@example.test"),
            stub.permission_calls,
        )

    def test_a_denied_note_read_queries_nothing_and_blocks_completion(self):
        """No Clinical Note read authority: no name read, no content read."""

        stub = self.stub()
        stub.db.all_rows["Clinical Note"] = [
            {
                "name": "HLC-NOTE-1",
                "modified": "2026-11-17 08:00:00",
                "note": "Assessment: Acne",
            }
        ]

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["clinical_note_modified"])
        self.assertIsNone(values["assessment"])
        self.assertIsNone(values["plan"])
        self.assertIsNone(values["followup"])
        self.assertEqual(self.note_queries(stub), [])
        self.assertEqual(self.note_content_reads(stub), [])

    def test_a_masked_note_field_blocks_the_content_read(self):
        stub = self.readable_note_stub()
        stub.get_meta("Clinical Note").masked = ["note"]

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["assessment"])
        self.assertEqual(self.note_queries(stub), [])
        self.assertEqual(self.note_content_reads(stub), [])

    def test_a_note_permlevel_above_the_granted_level_blocks_the_content(self):
        stub = self.readable_note_stub()
        meta = stub.get_meta("Clinical Note")
        meta.permlevels["note"] = 1
        meta.accessible = [0]

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["assessment"])
        self.assertEqual(self.note_queries(stub), [])
        self.assertEqual(self.note_content_reads(stub), [])

    def test_a_document_level_note_denial_blocks_the_content_read(self):
        stub = self.readable_note_stub()
        stub.denied_documents.add(("Clinical Note", "HLC-NOTE-1"))

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["assessment"])
        # The name lookup happened, the content read did not.
        self.assertEqual(
            self.note_queries(stub),
            [("Clinical Note", ("name",))],
        )
        self.assertEqual(self.note_content_reads(stub), [])

    def test_an_unreadable_note_row_read_fails_closed(self):
        stub = self.readable_note_stub()
        stub.db.all_errors["Clinical Note"] = RuntimeError("note table unreadable")

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["assessment"])
        self.assertEqual(self.note_content_reads(stub), [])

    def test_a_missing_note_field_definition_fails_closed(self):
        """No installed ``note`` field: the content can never be authorised."""

        stub = self.readable_note_stub()
        stub.get_meta("Clinical Note").shapes.pop("note")

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["assessment"])
        self.assertEqual(self.note_content_reads(stub), [])

    def test_a_failed_note_content_read_fails_closed(self):
        stub = self.readable_note_stub()
        stub.db.value_errors[("Clinical Note", "HLC-NOTE-1")] = RuntimeError(
            "note content unreadable"
        )

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["clinical_note_modified"])
        self.assertIsNone(values["assessment"])
        self.assertEqual(
            self.note_content_reads(stub),
            [("Clinical Note", "HLC-NOTE-1", ["note", "modified"])],
        )

    def test_a_malformed_note_content_result_fails_closed(self):
        """A missing row is not an empty note: the note is reported unreadable."""

        stub = self.readable_note_stub()
        stub.db.value_overrides[("Clinical Note", "HLC-NOTE-1")] = None

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["clinical_note_modified"])
        self.assertIsNone(values["assessment"])

    def test_a_blank_note_modified_fails_closed(self):
        """The amendment preimage cannot be re-verified, so the note is denied."""

        stub = self.readable_note_stub()
        stub.db.values[("Clinical Note", "HLC-NOTE-1", "modified")] = None

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertIsNone(values["clinical_note"])
        self.assertIsNone(values["clinical_note_modified"])
        self.assertIsNone(values["assessment"])

    def test_the_note_preimage_keeps_the_persisted_modified_representation(self):
        """A datetime column must not flip to the ISO ``T`` form.

        The amendment re-reads ``modified`` as ``str(...)`` and compares it byte
        for byte with this preimage. An ISO rendering would differ from the
        postcondition's space-separated value and latch the site as
        ``UNKNOWN``, so the preimage keeps the persisted representation.
        """

        modified = datetime(2026, 11, 17, 8, 0, 0, 123456)
        encounter_modified = "2026-11-17 09:00:00"
        stub = self.readable_note_stub()
        stub.db.values[("Clinical Note", "HLC-NOTE-1", "modified")] = modified
        stub.db.values[("Patient Encounter", "HLC-ENC-1", "modified")] = (
            encounter_modified
        )

        values = self.gateway(stub)._encounter_state_values(self.note_doc())

        self.assertEqual(values["clinical_note_modified"], str(modified))
        self.assertNotIn("T", values["clinical_note_modified"])

        facts = self.gateway(stub).native_encounter_amend_facts(
            "HLC-ENC-1",
            "HLC-NOTE-ADD-1",
            "Addendum: corrected",
            values["clinical_note"],
            values["clinical_note_modified"],
        )
        self.assertIsNone(
            encounter_contract.amend_postcondition_problem(
                encounter_modified=facts["encounter_modified"],
                expected_encounter_modified=encounter_modified,
                clinical_note_modified=facts["clinical_note_modified"],
                original_note_modified=values["clinical_note_modified"],
                addendum_created=facts["addendum_created"],
                expected_note="Addendum: corrected",
            )
        )


    def test_the_symptom_count_reads_the_installed_child_table(self):
        stub = self.stub()
        doc = types.SimpleNamespace(
            name="HLC-ENC-1",
            patient="PAT-1",
            docstatus=0,
            status="Draft",
            appointment="APPT-1",
            modified="2026-11-17 09:00:00",
        )
        counts = []
        stub.db.count = lambda doctype, filters=None: counts.append(doctype) or 0

        self.gateway(stub)._encounter_state_values(doc)

        self.assertIn("Patient Encounter Symptom", counts)
        self.assertNotIn("Complaint Detail", counts)

    def test_the_note_write_sets_the_installed_reference_pair(self):
        stub = self.stub()
        gateway = self.gateway(stub)

        with mock.patch.object(gateway, "setting", return_value="PRAC-1"):
            gateway._write_encounter_note(
                "HLC-ENC-1", "PAT-1", {"assessment": "Acne"}
            )

        note = stub.new_docs[0]
        self.assertTrue(note.inserted)
        self.assertEqual(note.reference_doc, "Patient Encounter")
        self.assertEqual(note.reference_name, "HLC-ENC-1")
        self.assertFalse(hasattr(note, "reference_doctype"))
        self.assertIn("Assessment: Acne", note.note)

    def test_the_draft_derives_the_appointment_type_and_symptom_child(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        payload = {
            "_native_patient_id": "PAT-1",
            "_native_appointment_id": "APPT-1",
            "_appointment_type": "APPT-TYPE-1",
            "fields": {
                "complaint": "Itchy rash",
                "diagnoses": [],
                "medications": [],
                "lab_tests": [],
                "procedures": [],
                "vitals": {},
                "note": None,
            },
        }

        with mock.patch.object(gateway, "setting", return_value="PRAC-1"):
            created = gateway.create_native_encounter_draft(payload)

        doc = stub.new_docs[0]
        self.assertEqual(created, doc.name)
        self.assertEqual(doc.appointment_type, "APPT-TYPE-1")
        self.assertEqual(
            doc.children, [("symptoms", {"complaint": "Itchy rash"})]
        )

    def test_the_state_read_counts_the_persisted_medication_rows(self):
        """A blank drug code is still a row: the count decides, not the codes."""

        stub = self.stub()
        stub.db.counts["Drug Prescription"] = 2
        stub.db.all_rows["Drug Prescription"] = [{"drug_code": ""}, {"drug_code": None}]
        doc = types.SimpleNamespace(
            name="HLC-ENC-1",
            patient="PAT-1",
            docstatus=0,
            status="Draft",
            appointment="APPT-1",
            modified="2026-11-17 09:00:00",
        )

        values = self.gateway(stub)._encounter_state_values(doc)

        self.assertEqual(values["medication_count"], 2)
        self.assertEqual(values["medication_codes"], ("", None))

    def test_the_linked_booking_is_locked_for_update(self):
        """``FOR UPDATE`` is what makes the committed row the one revalidated."""

        stub = self.stub()
        stub.permissions.add(("Patient Appointment", "write"))
        stub.docs[("Patient Appointment", "APPT-1")] = types.SimpleNamespace(
            patient="PAT-1", status="Checked In", appointment_type="APPT-TYPE-1"
        )
        gateway = self.gateway(stub)

        locked = gateway.lock_linked_appointment("APPT-1")

        self.assertEqual(
            locked,
            {
                "patient": "PAT-1",
                "status": "Checked In",
                "appointment_type": "APPT-TYPE-1",
            },
        )
        self.assertEqual(
            stub.get_doc_calls, [("Patient Appointment", "APPT-1", {"for_update": True})]
        )
        # The caller's snapshot restart is the only rollback on this path.
        self.assertEqual(stub.db.rollbacks, 0)

    def test_a_denied_booking_lock_returns_none(self):
        stub = self.stub()
        stub.docs[("Patient Appointment", "APPT-1")] = types.SimpleNamespace(
            patient="PAT-1", status="Open", appointment_type="APPT-TYPE-1"
        )

        self.assertIsNone(self.gateway(stub).lock_linked_appointment("APPT-1"))

    def test_a_missing_booking_lock_returns_none(self):
        stub = self.stub()
        stub.permissions.add(("Patient Appointment", "write"))

        self.assertIsNone(self.gateway(stub).lock_linked_appointment("APPT-1"))

    def test_a_restricted_booking_field_returns_none(self):
        stub = self.stub()
        stub.permissions.add(("Patient Appointment", "write"))
        stub.docs[("Patient Appointment", "APPT-1")] = types.SimpleNamespace(
            patient="PAT-1", status="Open", appointment_type="APPT-TYPE-1"
        )
        stub.restricted_meta_fields = True

        self.assertIsNone(self.gateway(stub).lock_linked_appointment("APPT-1"))

    def test_the_completion_lock_needs_only_read_on_the_booking(self):
        """HEAD completes with encounter rights; Appointment.write is not one."""

        stub = self.stub()
        stub.permissions.add(("Patient Appointment", "read"))
        stub.docs[("Patient Appointment", "APPT-1")] = types.SimpleNamespace(
            patient="PAT-1", status="Checked In", appointment_type="APPT-TYPE-1"
        )
        gateway = self.gateway(stub)

        locked = gateway.lock_linked_appointment_for_read("APPT-1")

        self.assertEqual(
            locked,
            {
                "patient": "PAT-1",
                "status": "Checked In",
                "appointment_type": "APPT-TYPE-1",
            },
        )
        self.assertEqual(
            stub.get_doc_calls, [("Patient Appointment", "APPT-1", {"for_update": True})]
        )

    def test_the_completion_lock_still_needs_read_on_the_booking(self):
        stub = self.stub()
        stub.docs[("Patient Appointment", "APPT-1")] = types.SimpleNamespace(
            patient="PAT-1", status="Open", appointment_type="APPT-TYPE-1"
        )

        self.assertIsNone(
            self.gateway(stub).lock_linked_appointment_for_read("APPT-1")
        )

    def test_the_linked_appointment_type_is_read_for_the_create_decision(self):
        stub = self.stub()
        stub.permissions.add(("Patient Appointment", "read"))
        stub.db.values[("Patient Appointment", "APPT-1", "patient")] = "PAT-1"
        stub.db.values[("Patient Appointment", "APPT-1", "appointment_date")] = "2026-11-17"
        stub.db.values[("Patient Appointment", "APPT-1", "appointment_time")] = "09:30:00"
        stub.db.values[("Patient Appointment", "APPT-1", "status")] = "Open"
        stub.db.values[("Patient Appointment", "APPT-1", "appointment_type")] = "APPT-TYPE-1"
        gateway = self.gateway(stub)

        self.assertEqual(
            gateway.native_appointment_encounter_type("APPT-1"), "APPT-TYPE-1"
        )
        del stub.db.values[("Patient Appointment", "APPT-1", "appointment_type")]
        self.assertIsNone(gateway.native_appointment_encounter_type("APPT-1"))


class EncounterWriteLeaseAdapterTests(GatewayTestCase):
    """The encounter lease appends nothing until the caller dispatches.

    These use the production ledger in a temporary site private directory so the
    adapter, the lease and the durable records are all real.
    """

    KIND = "encounter_draft"
    KEY = "enc-draft-key-0001-0123"
    DIGEST = "ab" * 32

    def setUp(self):
        self._temp = TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        self.root = Path(self._temp.name)
        private = self.root / "private"
        private.mkdir(mode=0o700)
        fence_store.initialize_store(
            private / fence_store.STORE_DIRNAME,
            private / fence_store.ANCHOR_FILENAME,
        )
        self.ledger = fence_store.open_store(
            private / fence_store.STORE_DIRNAME,
            private / fence_store.ANCHOR_FILENAME,
        )

    def lease_gateway(self):
        return self.gateway(FakeFrappe(site_path=self.root))

    def test_a_refusal_before_dispatch_appends_nothing(self):
        gateway = self.lease_gateway()

        with gateway.encounter_write_lease(
            actor="doctor@example.test",
            kind=self.KIND,
            operation_key=self.KEY,
            payload_digest=self.DIGEST,
        ):
            pass

        self.assertIsNone(
            self.ledger.lookup(
                actor="doctor@example.test", kind=self.KIND, operation_key=self.KEY
            )
        )

    def test_dispatch_claims_the_key_once_and_a_duplicate_is_write_locked(self):
        gateway = self.lease_gateway()

        with gateway.encounter_write_lease(
            actor="doctor@example.test",
            kind=self.KIND,
            operation_key=self.KEY,
            payload_digest=self.DIGEST,
        ) as lease:
            claim = lease.dispatch()
            self.assertEqual(claim.state, write_fence.DISPATCHED)
            claim.commit("receipt-1")

        self.assertEqual(
            self.ledger.lookup(
                actor="doctor@example.test", kind=self.KIND, operation_key=self.KEY
            ).state,
            write_fence.COMMITTED,
        )

        with self.assertRaises(patient_write.WriteLocked):
            with gateway.encounter_write_lease(
                actor="doctor@example.test",
                kind=self.KIND,
                operation_key=self.KEY,
                payload_digest=self.DIGEST,
            ) as lease:
                lease.dispatch()

    def test_a_digest_mismatch_is_a_write_conflict(self):
        gateway = self.lease_gateway()

        with gateway.encounter_write_lease(
            actor="doctor@example.test",
            kind=self.KIND,
            operation_key=self.KEY,
            payload_digest=self.DIGEST,
        ) as lease:
            lease.dispatch().commit("receipt-1")

        with self.assertRaises(patient_write.WriteConflict):
            with gateway.encounter_write_lease(
                actor="doctor@example.test",
                kind=self.KIND,
                operation_key=self.KEY,
                payload_digest="cd" * 32,
            ) as lease:
                lease.dispatch()

    def test_a_lease_is_single_use(self):
        gateway = self.lease_gateway()

        with self.assertRaises(patient_write.WriteUncertain):
            with gateway.encounter_write_lease(
                actor="doctor@example.test",
                kind=self.KIND,
                operation_key=self.KEY,
                payload_digest=self.DIGEST,
            ) as lease:
                lease.dispatch()
                lease.dispatch()

    def test_refresh_site_config_rereads_the_site_config_file(self):
        with TemporaryDirectory() as root:
            site_config = Path(root) / "site_config.json"
            site_config.write_text(
                json.dumps({"clinic_ui_native_encounter_write_enabled": 1}),
                encoding="utf-8",
            )
            gateway = self.gateway(FakeFrappe(site_path=root))
            self.assertEqual(
                gateway.setting("clinic_ui_native_encounter_write_enabled"), 1
            )

            site_config.write_text(
                json.dumps({"clinic_ui_native_encounter_write_enabled": 0}),
                encoding="utf-8",
            )
            self.assertEqual(
                gateway.setting("clinic_ui_native_encounter_write_enabled"), 1
            )

            gateway.refresh_site_config()

            self.assertEqual(
                gateway.setting("clinic_ui_native_encounter_write_enabled"), 0
            )

    def test_refresh_site_config_denies_when_the_file_becomes_unreadable(self):
        with TemporaryDirectory() as root:
            site_config = Path(root) / "site_config.json"
            site_config.write_text(
                json.dumps({"clinic_ui_native_encounter_write_enabled": 1}),
                encoding="utf-8",
            )
            gateway = self.gateway(FakeFrappe(site_path=root))
            site_config.write_text("{not json", encoding="utf-8")

            gateway.refresh_site_config()

            self.assertIsNone(gateway.setting("clinic_ui_native_encounter_write_enabled"))


class EncounterOperationLookupTests(GatewayTestCase):
    """``lookup_encounter_operation`` reads one key and never changes it.

    The replay path needs the durable state *and* the stored payload digest, not
    just the receipt, so it can refuse an uncertain key with the eager fence's
    ``WRITE_LOCKED`` answer and refuse a different payload outright. The read is
    plain ``Ledger.lookup``: no claim, no latch, no unlock, no event.
    """

    KEY = "enc-draft-key-0001-0123"
    RECEIPT = "encounter-receipt"

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
        self.addCleanup(
            lambda: __import__("shutil").rmtree(root, ignore_errors=True)
        )
        private = Path(root) / "private"
        private.mkdir(mode=0o700)
        fence_store.initialize_store(
            private / fence_store.STORE_DIRNAME,
            private / fence_store.ANCHOR_FILENAME,
        )
        return root

    def committed(self, gateway):
        with gateway.encounter_write_lease(
            "doctor@example.test", "encounter_draft", self.KEY, "a" * 64
        ) as lease:
            claim = lease.dispatch()
            claim.commit(self.RECEIPT)

    def test_it_returns_the_committed_record_with_its_digest(self):
        gateway = self.gateway_with_store(self.initialized_root())
        self.committed(gateway)
        sequence = gateway._ledger().info().sequence

        record = gateway.lookup_encounter_operation(
            "doctor@example.test", "encounter_draft", self.KEY
        )

        self.assertEqual(record.state, write_fence.COMMITTED)
        self.assertEqual(record.payload_digest, "a" * 64)
        self.assertEqual(record.receipt, self.RECEIPT)
        self.assertEqual(gateway._ledger().info().sequence, sequence)

    def test_it_returns_an_non_terminal_record_instead_of_locking(self):
        gateway = self.gateway_with_store(self.initialized_root())
        with self.assertRaises(patient_write.WriteUncertain):
            with gateway.encounter_write_lease(
                "doctor@example.test", "encounter_draft", self.KEY, "b" * 64
            ) as lease:
                lease.dispatch()
        sequence = gateway._ledger().info().sequence

        record = gateway.lookup_encounter_operation(
            "doctor@example.test", "encounter_draft", self.KEY
        )

        self.assertEqual(record.state, write_fence.DISPATCHED)
        self.assertEqual(record.payload_digest, "b" * 64)
        self.assertEqual(gateway._ledger().info().sequence, sequence)

    def test_another_actors_key_is_no_record(self):
        gateway = self.gateway_with_store(self.initialized_root())
        self.committed(gateway)

        self.assertIsNone(
            gateway.lookup_encounter_operation(
                "other@example.test", "encounter_draft", self.KEY
            )
        )

    def test_a_foreign_kind_is_refused_before_the_ledger_is_read(self):
        gateway = self.gateway_with_store(self.initialized_root())
        gateway._ledger = lambda: self.fail("the ledger must not be read")

        with self.assertRaises(patient_write.WriteUncertain):
            gateway.lookup_encounter_operation(
                "doctor@example.test", "create", self.KEY
            )

    def test_an_unreadable_store_maps_to_store_unavailable(self):
        gateway = self.gateway_with_store(self.initialized_root())

        class FailingLedger:
            def lookup(self, **kwargs):
                raise write_fence.StoreUnavailable("store failed")

        gateway._ledger = lambda: FailingLedger()
        try:
            with self.assertRaises(patient_write.StoreUnavailable):
                gateway.lookup_encounter_operation(
                    "doctor@example.test", "encounter_draft", self.KEY
                )
        finally:
            del gateway._ledger

    def test_a_fence_error_maps_to_write_uncertain(self):
        gateway = self.gateway_with_store(self.initialized_root())

        class FailingLedger:
            def lookup(self, **kwargs):
                raise write_fence.FenceError("ledger error")

        gateway._ledger = lambda: FailingLedger()
        try:
            with self.assertRaises(patient_write.WriteUncertain):
                gateway.lookup_encounter_operation(
                    "doctor@example.test", "encounter_draft", self.KEY
                )
        finally:
            del gateway._ledger


class NativeProcedureViewAdapterTests(GatewayTestCase):
    """The three-field read applies document and field-level read authority."""

    NATIVE = "HLC-CPR-1"

    def stub(self, *, permitted=True):
        stub = FakeFrappe(
            site="marley-sit.local",
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            ),
        )
        stub.db.values[("__perm__", self.NATIVE, "read")] = permitted
        stub.db.values[("Clinical Procedure", self.NATIVE, "name")] = self.NATIVE
        stub.db.values[("Clinical Procedure", self.NATIVE, "status")] = "Pending"
        stub.db.values[("Clinical Procedure", self.NATIVE, "docstatus")] = 1
        return stub

    def test_reads_only_the_three_contract_fields(self):
        stub = self.stub()

        detail = self.gateway(stub).native_procedure_view(self.NATIVE)

        self.assertEqual(
            detail,
            {
                "procedure_id": self.NATIVE,
                "status": "Pending",
                "docstatus": 1,
            },
        )
        self.assertEqual(
            stub.permission_calls,
            [
                (
                    "Clinical Procedure",
                    "read",
                    self.NATIVE,
                    "doctor@example.test",
                )
            ],
        )
        self.assertEqual(
            stub.db.calls,
            [
                (
                    "Clinical Procedure",
                    self.NATIVE,
                    ["name", "status", "docstatus"],
                )
            ],
        )
        self.assertEqual(stub.meta.masked_calls, 1)

    def test_a_denied_document_reads_nothing(self):
        stub = self.stub(permitted=False)

        self.assertIsNone(
            self.gateway(stub).native_procedure_view(self.NATIVE)
        )
        self.assertEqual(stub.db.calls, [])
        self.assertEqual(stub.meta.masked_calls, 0)

    def test_a_restricted_status_permlevel_fails_closed_before_any_read(self):
        stub = self.stub()
        stub.meta.permlevels["status"] = 1

        self.assertIsNone(
            self.gateway(stub).native_procedure_view(self.NATIVE)
        )
        self.assertEqual(stub.db.calls, [])

    def test_a_masked_status_fails_closed_before_any_read(self):
        stub = self.stub()
        stub.meta.masked = ["status"]

        self.assertIsNone(
            self.gateway(stub).native_procedure_view(self.NATIVE)
        )
        self.assertEqual(stub.meta.masked_calls, 1)
        self.assertEqual(stub.db.calls, [])

    def test_a_missing_status_field_fails_closed_before_any_read(self):
        stub = self.stub()
        del stub.meta.permlevels["status"]

        self.assertIsNone(
            self.gateway(stub).native_procedure_view(self.NATIVE)
        )
        self.assertEqual(stub.db.calls, [])

    def test_a_malformed_row_fails_closed(self):
        for field, value in (
            ("status", ""),
            ("status", None),
            ("name", ""),
            ("docstatus", "1"),
            ("docstatus", True),
            ("docstatus", 7),
            ("docstatus", None),
        ):
            with self.subTest(field=field, value=value):
                stub = self.stub()
                stub.db.values[("Clinical Procedure", self.NATIVE, field)] = value
                self.assertIsNone(
                    self.gateway(stub).native_procedure_view(self.NATIVE)
                )

    def test_a_read_failure_fails_closed(self):
        stub = self.stub()

        def boom(*args, **kwargs):
            raise RuntimeError("database unavailable")

        stub.db.get_value = boom
        self.assertIsNone(
            self.gateway(stub).native_procedure_view(self.NATIVE)
        )

    def test_the_write_read_back_uses_the_same_field_level_checks(self):
        masked = self.stub()
        masked.meta.masked = ["status"]
        self.assertIsNone(
            self.gateway(masked).native_procedure_read_detail(self.NATIVE)
        )
        self.assertEqual(masked.db.calls, [])

        restricted = self.stub()
        restricted.meta.permlevels["status"] = 1
        self.assertIsNone(
            self.gateway(restricted).native_procedure_read_detail(self.NATIVE)
        )
        self.assertEqual(restricted.db.calls, [])

        readable = self.stub()
        self.assertEqual(
            self.gateway(readable).native_procedure_read_detail(self.NATIVE),
            {
                "procedure_id": self.NATIVE,
                "status": "Pending",
                "docstatus": 1,
            },
        )

    def test_a_malformed_id_is_refused_before_any_permission_check(self):
        for value in (None, "", " ", 7):
            with self.subTest(value=value):
                stub = self.stub()
                self.assertIsNone(
                    self.gateway(stub).native_procedure_view(value)
                )
                self.assertEqual(stub.permission_calls, [])


class NativeProcedureListAdapterTests(GatewayTestCase):
    """Linked discovery uses permission-filtered ``get_list`` only."""

    REQUEST = "HLC-SR-1"

    def stub(self, rows=None):
        stub = FakeFrappe(
            site="marley-sit.local",
            session=types.SimpleNamespace(
                user="doctor@example.test", sid="sid-abc123"
            ),
        )
        stub.meta.fields = [
            FakeField(0, fieldname=name)
            for name in ("name", "status", "docstatus", "service_request")
        ]
        stub.meta.default_fields = ["name", "docstatus", "modified"]
        stub.get_list_rows = (
            [{"name": "HLC-CPR-2"}, {"name": "HLC-CPR-1"}]
            if rows is None
            else rows
        )
        return stub

    def test_uses_the_installed_filter_field_and_one_row_past_the_bound(self):
        stub = self.stub()

        names = self.gateway(stub).native_procedures_for_service_request(
            self.REQUEST, 2
        )

        self.assertEqual(names, ["HLC-CPR-2", "HLC-CPR-1"])
        self.assertEqual(
            stub.get_list_calls,
            [
                (
                    "Clinical Procedure",
                    ["name"],
                    "modified desc, name asc",
                    3,
                    {"filters": {"service_request": self.REQUEST}},
                )
            ],
        )

    def test_an_over_full_page_is_refused_not_truncated(self):
        stub = self.stub([{"name": f"HLC-CPR-{index}"} for index in range(3)])

        self.assertIsNone(
            self.gateway(stub).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )

    def test_a_missing_installed_filter_field_refuses(self):
        stub = self.stub()
        stub.meta.fields = [FakeField(0, fieldname="name")]

        self.assertIsNone(
            self.gateway(stub).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )
        self.assertEqual(stub.get_list_calls, [])

    def test_a_masked_or_restricted_filter_field_refuses_before_the_query(self):
        masked = self.stub()
        masked.meta.masked = ["service_request"]
        self.assertIsNone(
            self.gateway(masked).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )
        self.assertEqual(masked.get_list_calls, [])

        restricted = self.stub()
        restricted.meta.permlevels["service_request"] = 1
        self.assertIsNone(
            self.gateway(restricted).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )
        self.assertEqual(restricted.get_list_calls, [])

    def test_an_unreadable_row_or_list_fails_closed(self):
        malformed = self.stub([{"name": ""}])
        self.assertIsNone(
            self.gateway(malformed).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )

        failing = self.stub()
        failing.get_list_error = RuntimeError("database unavailable")
        self.assertIsNone(
            self.gateway(failing).native_procedures_for_service_request(
                self.REQUEST, 2
            )
        )

    def test_an_invalid_bound_is_refused(self):
        for limit in (0, -1, 1001, True, "2"):
            with self.subTest(limit=limit):
                stub = self.stub()
                self.assertIsNone(
                    self.gateway(stub).native_procedures_for_service_request(
                        self.REQUEST, limit
                    )
                )
                self.assertEqual(stub.get_list_calls, [])


class PurchaseMetaField:
    def __init__(self, fieldname, permlevel=0):
        self.fieldname = fieldname
        self.permlevel = permlevel


class PurchaseMeta:
    """Installed Meta stand-in: Table fields exist but are not DB columns.

    frappe 16.54.0 ``Meta.get_valid_columns()`` omits Table fields, which is why
    the legacy purchase preflight reported ``missing field Purchase
    Order.items`` on a healthy install. ``fields`` carries the real DocFields.
    """

    def __init__(self, fieldnames, table_fields=(), default_fields=()):
        self.fields = [PurchaseMetaField(name) for name in fieldnames]
        self.table_fields = set(table_fields)
        self.default_fields = list(default_fields)
        self.accessible = [0]
        self.masked = []

    def get_valid_columns(self):
        return [
            field.fieldname
            for field in self.fields
            if field.fieldname not in self.table_fields
        ] + list(self.default_fields)

    def get_permlevel_access(
        self, permission_type="read", parenttype=None, *, user=None
    ):
        return list(self.accessible)

    def get_field(self, fieldname):
        for field in self.fields:
            if field.fieldname == fieldname:
                return field
        return None

    def get_masked_fields(self, parenttype=None):
        return [PurchaseMetaField(name) for name in self.masked]


class PurchaseDocument:
    """Controller-like stand-in for the installed Purchase Order insert.

    Models the verified 16.52.1 lifecycle: ``insert`` runs the controller
    validation, whose ``set_missing_values(for_validate=True)`` resolves the
    line values the client never sends (``item_name`` from the Item master and
    the default target ``warehouse`` from the company default) before the
    mandatory checks.
    """

    def __init__(self, doctype, name=None, stub=None):
        self.doctype = doctype
        self.name = name
        self.inserted = False
        self.submitted = False
        self.docstatus = 0
        self.payload = {}
        self.stub = stub

    def insert(self):
        items = self.payload.get("items") or []
        if not items:
            raise RuntimeError("MandatoryError: items")
        for row in items:
            if self.stub is not None:
                if not row.get("item_name"):
                    row["item_name"] = self.stub.item_names.get(
                        row.get("item_code")
                    )
                if not row.get("warehouse"):
                    row["warehouse"] = self.stub.default_warehouse
            if not row.get("item_name"):
                raise RuntimeError("MandatoryError: item_name")
            if not row.get("warehouse"):
                raise RuntimeError("MandatoryError: warehouse")
        self.inserted = True
        if self.name is None:
            self.name = "PO-NEW"

    def submit(self):
        self.submitted = True
        self.docstatus = 1


class PurchaseDB:
    def __init__(self):
        self.values = {}
        self.existing = set()
        self.commits = 0
        self.rollbacks = 0

    def get_value(self, doctype, name, fieldname=None, as_dict=False, **kwargs):
        if isinstance(fieldname, (list, tuple)):
            row = {
                field: self.values.get((doctype, name, field))
                for field in fieldname
            }
            return row if as_dict else tuple(row[field] for field in fieldname)
        return self.values.get((doctype, name, fieldname))

    def get_single_value(self, doctype, fieldname):
        return self.values.get((doctype, "__single__", fieldname))

    def exists(self, doctype, name):
        return (doctype, name) in self.existing

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class PurchaseStub(types.SimpleNamespace):
    """Fake Frappe module for the converted purchase draft/read paths."""

    def __init__(self, *, user="purchasing@example.test"):
        super().__init__()
        self.session = types.SimpleNamespace(user=user, sid="sid-purchase")
        self.local = types.SimpleNamespace(
            site="marley.test",
            session={},
            response={},
            response_headers={},
            flags=types.SimpleNamespace(),
        )
        self.db = PurchaseDB()
        self.defaults = types.SimpleNamespace(get_user_default=lambda key: None)
        self.utils = types.SimpleNamespace(nowdate=lambda: "2026-11-19")
        self.permissions = {}
        self.metas = {}
        for doctype in dict.fromkeys(
            (
                *purchase_contract.INSTALLED_CANDIDATES,
                *purchase_contract.DRAFT_INSTALLED_CANDIDATES,
            )
        ):
            names = sorted(
                {
                    candidates[0]
                    for mapping in (
                        purchase_contract.INSTALLED_CANDIDATES,
                        purchase_contract.DRAFT_INSTALLED_CANDIDATES,
                    )
                    for candidates in (mapping.get(doctype) or {}).values()
                }
            )
            self.metas[doctype] = PurchaseMeta(
                names,
                table_fields=("items",),
                default_fields=(
                    "name",
                    "owner",
                    "creation",
                    "modified",
                    "modified_by",
                    "docstatus",
                    "idx",
                ),
            )
        self.get_list_calls = []
        self.get_list_rows = []
        self.created_docs = []
        self.item_names = {"ITEM-1": "Synthetic Item"}
        self.default_warehouse = "WH-1"

    def get_meta(self, doctype):
        return self.metas[doctype]

    def get_doc(self, doctype, name=None, **kwargs):
        document = PurchaseDocument(
            doctype if isinstance(doctype, str) else doctype["doctype"],
            stub=self,
        )
        document.payload = dict(doctype) if isinstance(doctype, dict) else {}
        self.created_docs.append(document)
        return document

    def has_permission(self, doctype, ptype="read", doc=None, user=None):
        key = getattr(doc, "name", doc)
        return bool(
            self.permissions.get(
                (doctype, ptype, key),
                self.permissions.get((doctype, ptype), False),
            )
        )

    def get_list(
        self,
        doctype,
        fields=None,
        order_by=None,
        limit_page_length=None,
        **kwargs,
    ):
        self.get_list_calls.append(
            (
                doctype,
                list(fields or ()),
                order_by,
                limit_page_length,
                kwargs,
            )
        )
        return list(self.get_list_rows)


class PurchaseDraftAndReadTests(GatewayTestCase):
    def stub(self):
        stub = PurchaseStub()
        stub.db.values[("Company", "SITCO", "default_currency")] = "USD"
        stub.db.existing.add(("Supplier", "SUP-1"))
        stub.db.existing.add(("Item", "ITEM-1"))
        stub.db.values[("Item", "ITEM-1", "has_batch_no")] = 0
        stub.db.values[("Item", "ITEM-1", "has_expiry_date")] = 0
        stub.db.values[("Item", "ITEM-1", "is_stock_item")] = 1
        stub.db.values[("Item", "ITEM-1", "stock_uom")] = "Unit"
        stub.db.values[("Item", "ITEM-1", "item_name")] = "Synthetic Item"
        stub.permissions[("Supplier", "read", "SUP-1")] = True
        stub.permissions[("Item", "read", "ITEM-1")] = True
        return stub

    def draft(self, stub):
        return {
            "supplier": "SUP-1",
            "items": [{"item_code": "ITEM-1", "qty": 5.0, "rate": 2.5}],
        }

    def test_items_table_field_resolves_from_meta_not_valid_columns(self):
        stub = self.stub()
        gateway = self.gateway(stub)

        self.assertNotIn(
            "items",
            set(stub.get_meta("Purchase Order").get_valid_columns()),
        )
        self.assertIn(
            "items", gateway._purchase_meta_fields()["Purchase Order"]
        )
        self.assertIsNone(gateway.native_purchase_install_problem())

    def test_a_genuinely_missing_field_is_still_refused(self):
        stub = self.stub()
        stub.metas["Purchase Order"].fields = [
            field
            for field in stub.metas["Purchase Order"].fields
            if field.fieldname != "currency"
        ]

        self.assertEqual(
            self.gateway(stub).native_purchase_install_problem(),
            "missing field Purchase Order.currency",
        )

    def test_the_draft_company_uses_the_framework_default_resolution(self):
        # frappe.defaults.get_user_default already merges the framework's
        # __default values; this lane adds no fallback and reads no single
        # value of its own, so the resolved company is used verbatim.
        stub = self.stub()
        stub.defaults = types.SimpleNamespace(
            get_user_default=lambda key: "SITCO"
        )
        stub.db.values[
            ("Global Defaults", "__single__", "default_company")
        ] = "OTHER"

        detail = self.gateway(stub).native_purchase_order_draft_detail(
            self.draft(stub)
        )

        self.assertEqual(detail["company"], "SITCO")
        self.assertEqual(detail["currency"], "USD")

    def test_an_empty_framework_company_default_is_refused_not_invented(self):
        stub = self.stub()
        stub.defaults = types.SimpleNamespace(
            get_user_default=lambda key: None
        )
        stub.db.values[
            ("Global Defaults", "__single__", "default_company")
        ] = "OTHER"

        detail = self.gateway(stub).native_purchase_order_draft_detail(
            self.draft(stub)
        )

        self.assertEqual(detail["company"], "")
        self.assertEqual(detail["currency"], "")

    def test_a_real_erpnext_16_shape_passes_the_draft_preflight_only(self):
        stub = self.stub()
        # Installed ERPNext 16.52.1 has no Purchase Order Item.purchase_order
        # and no Purchase Order Item.received_qty: both live elsewhere.
        order_item = stub.metas["Purchase Order Item"]
        order_item.fields = [
            field
            for field in order_item.fields
            if field.fieldname not in ("purchase_order", "received_qty")
        ]
        gateway = self.gateway(stub)

        self.assertIsNone(gateway.native_purchase_draft_install_problem())
        self.assertEqual(
            gateway.native_purchase_install_problem(),
            "missing field Purchase Order Item.received_qty",
        )

        only_order = self.stub()
        order_item = only_order.metas["Purchase Order Item"]
        order_item.fields = [
            field
            for field in order_item.fields
            if field.fieldname != "purchase_order"
        ]
        self.assertEqual(
            self.gateway(only_order).native_purchase_install_problem(),
            "missing field Purchase Order Item.purchase_order",
        )
        self.assertIsNone(
            self.gateway(only_order).native_purchase_draft_install_problem()
        )

    def test_a_missing_field_the_draft_needs_is_refused(self):
        for doctype, fieldname, message in (
            (
                "Purchase Order Item",
                "rate",
                "missing field Purchase Order Item.rate",
            ),
            (
                "Purchase Order",
                "currency",
                "missing field Purchase Order.currency",
            ),
            ("Item", "stock_uom", "missing field Item.stock_uom"),
            (
                "Company",
                "default_currency",
                "missing field Company.default_currency",
            ),
        ):
            with self.subTest(doctype=doctype, fieldname=fieldname):
                stub = self.stub()
                meta = stub.metas[doctype]
                meta.fields = [
                    field
                    for field in meta.fields
                    if field.fieldname != fieldname
                ]
                self.assertEqual(
                    self.gateway(stub).native_purchase_draft_install_problem(),
                    message,
                )

    def test_the_draft_create_inserts_once_without_submitting(self):
        stub = self.stub()
        gateway = self.gateway(stub)
        prepared = {
            "supplier": "SUP-1",
            "company": "SITCO",
            "currency": "USD",
            "conversion_rate": 1.0,
            "transaction_date": "2026-11-19",
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 5.0,
                    "rate": 2.5,
                    "uom": "Unit",
                    "stock_uom": "Unit",
                    "conversion_factor": 1.0,
                    "schedule_date": "2026-11-19",
                }
            ],
        }

        name = gateway.create_native_purchase_order_draft(prepared)

        self.assertEqual(name, "PO-NEW")
        (document,) = stub.created_docs
        self.assertTrue(document.inserted)
        self.assertFalse(document.submitted)
        self.assertEqual(document.docstatus, 0)
        self.assertEqual(document.payload["doctype"], "Purchase Order")
        self.assertNotIn("docstatus", document.payload)
        self.assertNotIn("ignore_permissions", document.payload)

    def prepared(self):
        return {
            "supplier": "SUP-1",
            "company": "SITCO",
            "currency": "USD",
            "conversion_rate": 1.0,
            "transaction_date": "2026-11-19",
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 5.0,
                    "rate": 2.5,
                    "uom": "Unit",
                    "stock_uom": "Unit",
                    "conversion_factor": 1.0,
                    "schedule_date": "2026-11-19",
                }
            ],
        }

    def test_the_native_controller_fills_item_name_and_default_warehouse(self):
        stub = self.stub()
        gateway = self.gateway(stub)

        name = gateway.create_native_purchase_order_draft(self.prepared())

        self.assertEqual(name, "PO-NEW")
        (document,) = stub.created_docs
        self.assertTrue(document.inserted)
        row = document.payload["items"][0]
        self.assertEqual(row["item_name"], "Synthetic Item")
        self.assertEqual(row["warehouse"], "WH-1")

    def test_the_insert_fails_when_the_controller_has_no_default_warehouse(self):
        stub = self.stub()
        stub.default_warehouse = ""

        with self.assertRaises(patient_write.WriteUncertain):
            self.gateway(stub).create_native_purchase_order_draft(
                self.prepared()
            )
        self.assertFalse(stub.created_docs[0].inserted)

    def test_the_draft_detail_reads_masters_under_native_permission(self):
        stub = self.stub()

        detail = self.gateway(stub).native_purchase_order_draft_detail(
            self.draft(stub)
        )

        self.assertTrue(detail["supplier_readable"])
        self.assertTrue(detail["supplier_exists"])
        self.assertFalse(detail["supplier_denied"])
        entry = detail["items"]["ITEM-1"]
        self.assertTrue(entry["readable"])
        self.assertEqual(entry["stock_uom"], "Unit")

    def test_a_denied_item_loads_no_field_and_reports_denied(self):
        stub = self.stub()
        stub.permissions[("Item", "read", "ITEM-1")] = False

        entry = self.gateway(stub).native_purchase_order_draft_detail(
            self.draft(stub)
        )["items"]["ITEM-1"]

        self.assertTrue(entry["denied"])
        self.assertFalse(entry["readable"])
        self.assertFalse(entry["exists"])
        self.assertNotIn("stock_uom", entry)

    def test_a_denied_supplier_reports_denied(self):
        stub = self.stub()
        stub.permissions[("Supplier", "read", "SUP-1")] = False

        detail = self.gateway(stub).native_purchase_order_draft_detail(
            self.draft(stub)
        )

        self.assertTrue(detail["supplier_denied"])
        self.assertFalse(detail["supplier_exists"])

    def order_row(self, stub, name):
        stub.db.values[("Purchase Order", name, "name")] = name
        stub.db.values[("Purchase Order", name, "status")] = "Draft"
        stub.db.values[("Purchase Order", name, "docstatus")] = 0
        stub.permissions[("Purchase Order", "read", name)] = True

    def test_the_view_returns_three_fields_under_native_read(self):
        stub = self.stub()
        self.order_row(stub, "PO-1")

        self.assertEqual(
            self.gateway(stub).native_purchase_order_view("PO-1"),
            {
                "purchase_order_id": "PO-1",
                "status": "Draft",
                "docstatus": 0,
            },
        )

    def test_the_view_refuses_a_denied_masked_or_restricted_status(self):
        denied = self.stub()
        self.order_row(denied, "PO-1")
        denied.permissions[("Purchase Order", "read", "PO-1")] = False
        self.assertIsNone(
            self.gateway(denied).native_purchase_order_view("PO-1")
        )

        masked = self.stub()
        self.order_row(masked, "PO-1")
        masked.metas["Purchase Order"].masked = ["status"]
        self.assertIsNone(
            self.gateway(masked).native_purchase_order_view("PO-1")
        )

        restricted = self.stub()
        self.order_row(restricted, "PO-1")
        restricted.metas["Purchase Order"].accessible = []
        self.assertIsNone(
            self.gateway(restricted).native_purchase_order_view("PO-1")
        )

    def test_drafts_are_bounded_and_permission_filtered(self):
        stub = self.stub()
        stub.get_list_rows = [{"name": "PO-1"}, {"name": "PO-2"}]

        names = self.gateway(stub).native_purchase_order_drafts("SUP-1", 2)

        self.assertEqual(names, ["PO-1", "PO-2"])
        doctype, fields, order_by, limit, kwargs = stub.get_list_calls[0]
        self.assertEqual(doctype, "Purchase Order")
        self.assertEqual(fields, ["name"])
        self.assertEqual(limit, 3)
        self.assertEqual(kwargs.get("filters"), {
            "supplier": "SUP-1",
            "docstatus": 0,
        })

    def test_an_over_full_or_unreadable_discovery_page_is_refused(self):
        over_full = self.stub()
        over_full.get_list_rows = [{"name": "PO-1"}, {"name": "PO-2"}]
        self.assertIsNone(
            self.gateway(over_full).native_purchase_order_drafts("SUP-1", 1)
        )

        masked = self.stub()
        masked.metas["Purchase Order"].masked = ["supplier"]
        self.assertIsNone(
            self.gateway(masked).native_purchase_order_drafts("SUP-1", 2)
        )

        for limit in (0, -1, 1001, True, "2"):
            with self.subTest(limit=limit):
                stub = self.stub()
                self.assertIsNone(
                    self.gateway(stub).native_purchase_order_drafts(
                        "SUP-1", limit
                    )
                )
                self.assertEqual(stub.get_list_calls, [])


class StockEntryDocument:
    """Stand-in for the installed Stock Entry controller during one transfer."""

    def __init__(self, payload):
        self.payload = payload
        self.name = None
        self.docstatus = 0
        self.inserted = False
        self.submitted = False

    def insert(self):
        self.inserted = True
        self.name = "MAT-STE-2026-00001"

    def submit(self):
        self.submitted = True
        self.docstatus = 1


class StockTransferStub(types.SimpleNamespace):
    """Fake Frappe module for the converted material transfer path."""

    def __init__(self, error=None):
        super().__init__()
        self.error = error
        self.created = []

    def get_doc(self, document):
        if self.error is not None:
            raise self.error
        entry = StockEntryDocument(document)
        self.created.append(entry)
        return entry


class NativeStockTransferAdapterTests(GatewayTestCase):
    def transfer(self, **overrides):
        values = {
            "company": "SITCO",
            "item_code": "ITEM-1",
            "qty": 4.0,
            "source_warehouse": "WH-1",
            "target_warehouse": "WH-2",
            "batch_id": "BATCH-1",
        }
        values.update(overrides)
        return values

    def test_one_material_transfer_is_inserted_and_submitted(self):
        stub = StockTransferStub()

        created = self.gateway(
            stub
        ).create_and_submit_native_stock_transfer(self.transfer())

        self.assertEqual(
            created, {"name": "MAT-STE-2026-00001", "docstatus": 1}
        )
        (entry,) = stub.created
        self.assertTrue(entry.inserted)
        self.assertTrue(entry.submitted)
        self.assertEqual(entry.payload["doctype"], "Stock Entry")
        self.assertEqual(entry.payload["stock_entry_type"], "Material Transfer")
        self.assertEqual(entry.payload["company"], "SITCO")
        self.assertNotIn("docstatus", entry.payload)
        self.assertNotIn("purpose", entry.payload)
        (row,) = entry.payload["items"]
        self.assertEqual(row["s_warehouse"], "WH-1")
        self.assertEqual(row["t_warehouse"], "WH-2")
        self.assertEqual(row["qty"], 4.0)
        self.assertEqual(row["batch_no"], "BATCH-1")
        self.assertEqual(row["use_serial_batch_fields"], 1)
        self.assertNotIn("uom", row)

    def test_a_non_batch_transfer_sends_no_batch_row(self):
        stub = StockTransferStub()

        self.gateway(stub).create_and_submit_native_stock_transfer(
            self.transfer(batch_id=None)
        )

        (row,) = stub.created[0].payload["items"]
        self.assertNotIn("batch_no", row)
        self.assertNotIn("use_serial_batch_fields", row)

    def test_a_native_refusal_propagates_unchanged(self):
        error = TypeError("LinkValidationError: target warehouse")

        with self.assertRaises(TypeError):
            self.gateway(
                StockTransferStub(error)
            ).create_and_submit_native_stock_transfer(self.transfer())


if __name__ == "__main__":
    unittest.main()
