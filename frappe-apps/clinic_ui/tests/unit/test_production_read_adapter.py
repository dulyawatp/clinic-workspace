import os
import base64
import sys
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.core import production_enrollment, production_patient_ref
from clinic_ui.endpoints import appointments, directory, patients, session
from clinic_ui.runtime import production_read_gateway
from .fake_gateway import Denied, FakeGateway, SECRET, session_view, settings
from .test_endpoints_patient_view import DETAIL

REF_KEY = bytes(range(64))
CREATION = datetime(2025, 1, 1, 12, 0, 0, 123456)


def patient_ref(name):
    return production_patient_ref.encode(REF_KEY, "client.example", name, CREATION)


def gateway(**changes):
    values = {
        "clinic_ui_production_read_enabled": True,
        "clinic_ui_production_site": "client.example",
        "clinic_ui_production_marker_sha256": "a" * 64,
        "clinic_ui_production_epoch_secret": SECRET,
        "clinic_ui_production_role_map": {"NUR": ["Nurse"]},
        "clinic_ui_production_patient_ref_key": base64.urlsafe_b64encode(REF_KEY).rstrip(b"=").decode("ascii"),
    }
    values.update(changes)
    fake = FakeGateway(site="client.example", settings=values,
                       current_session=session_view(roles=("Nurse",)),
                       csrf_token="csrf-token-value")
    fake.frappe = SimpleNamespace(
        local=SimpleNamespace(site="client.example", request=SimpleNamespace(headers={})),
        get_site_path=lambda *args: "sites/client.example/private",
    )
    fake.patient_authority = {"PAT-1": "Patient One"}
    fake.readable_patients = ["PAT-1"]
    def get_list(doctype, *, filters, fields, limit_page_length):
        assert fields == ["name"] and limit_page_length == 2
        names = fake.readable_patients if doctype == "Patient" else fake.readable_appointments
        return [{"name": name} for name in names if name == filters["name"]][:2]
    fake.frappe.get_list = get_list
    fake.frappe.db = SimpleNamespace(get_value=lambda *args: CREATION)
    return fake


class ProductionReadTests(unittest.TestCase):
    def test_reception_appointment_detail_only_includes_readable_patient_name(self):
        fake = gateway(clinic_ui_production_role_map={"REC": ["Reception"]})
        fake.current_session = session_view(roles=("Reception",))
        fake.readable_appointments = ["APT-1"]
        fake.appointment_detail_map = {"APT-1": {
            "patient": "PAT-1", "patient_name": "Stale Appointment Name",
            "appointment_date": "2026-11-22", "appointment_time": "09:30:00",
            "status": "Open", "modified": "2026-11-22T09:00:00",
        }}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True), \
             patch.object(fake, "native_patient_display_name", return_value="Patient One") as name:
            result = appointments.appointment_detail(fake, "APT-1")
            self.assertEqual(result["patient_display_name"], "Patient One")
            name.assert_called_once_with("PAT-1")
            for value in (None, "", "  ", 123, "x" * 141, "PAT-1"):
                name.return_value = value
                self.assertNotIn("patient_display_name", appointments.appointment_detail(fake, "APT-1"))
            fake._settings["clinic_ui_production_role_map"] = {"DOC": ["Reception"]}
            name.reset_mock()
            self.assertNotIn("patient_display_name", appointments.appointment_detail(fake, "APT-1"))
            name.assert_not_called()

    def test_checkin_probe_rejects_unknown_or_unsafe_evidence(self):
        fake = gateway()
        fake.frappe.conf = {}
        fake.frappe.session = SimpleNamespace(user="staff")
        fake.frappe.get_doc = lambda *_: SimpleNamespace(name="APT-1")
        fake.frappe.has_permission = lambda *args, **kwargs: True
        fake.native_appointment_boundary_problem = lambda: None
        fake.healthcare_appointment_side_effects_unsafe = lambda: False
        fake._checkin_profile_snapshot = lambda: {
            "clinic_photos_installed": True,
            "checkin_whitelisted_override": ("clinic_photos.visit.checkin.check_in_appointment",),
        }
        photos = SimpleNamespace(enabled_for_site=lambda *_: True)
        with patch.dict(sys.modules, {"clinic_photos": SimpleNamespace(),
                                     "clinic_photos.visit": SimpleNamespace(),
                                     "clinic_photos.visit.runtime": photos}):
            self.assertTrue(production_read_gateway.production_checkin_ready(fake, "APT-1"))
            for name, replacement in (
                ("has_permission", lambda *a, **k: None),
                ("has_permission", lambda *a, **k: False),
            ):
                with patch.object(fake.frappe, name, replacement):
                    self.assertFalse(production_read_gateway.production_checkin_ready(fake, "APT-1"))
            for name, result in (("native_appointment_boundary_problem", "drift"),
                                 ("healthcare_appointment_side_effects_unsafe", None),
                                 ("healthcare_appointment_side_effects_unsafe", True),
                                 ("_checkin_profile_snapshot", {"clinic_photos_installed": False, "checkin_whitelisted_override": ()}),
                                 ("_checkin_profile_snapshot", {"clinic_photos_installed": True, "checkin_whitelisted_override": ("other",)})):
                with patch.object(fake, name, return_value=result):
                    self.assertFalse(production_read_gateway.production_checkin_ready(fake, "APT-1"))
            photos.enabled_for_site = lambda *_: None
            self.assertFalse(production_read_gateway.production_checkin_ready(fake, "APT-1"))
            photos.enabled_for_site = lambda *_: True
            with patch.object(fake.frappe, "get_doc", side_effect=RuntimeError("unavailable")):
                self.assertFalse(production_read_gateway.production_checkin_ready(fake, "APT-1"))
            with patch.object(fake.frappe, "get_doc", return_value=SimpleNamespace(name="OTHER")):
                self.assertFalse(production_read_gateway.production_checkin_ready(fake, "APT-1"))

    def test_production_checkin_requires_independent_reception_gate_and_safe_probes(self):
        fake = gateway(clinic_ui_production_checkin_enabled=True,
                       clinic_ui_production_role_map={"REC": ["Reception"]})
        fake.current_session = session_view(roles=("Reception",))
        fake.readable_appointments = ["APT-1"]
        row = {"patient": "PAT-1", "appointment_date": "2026-11-22",
               "appointment_time": "09:30:00", "status": "Open",
               "modified": "2026-11-22T09:00:00"}
        fake.appointment_detail_map = {"APT-1": row}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True), \
             patch.object(production_read_gateway, "production_checkin_ready", return_value=True, create=True):
            self.assertIs(appointments.appointment_detail(fake, "APT-1")["native_checkin_enabled"], True)
            fake._settings["clinic_ui_production_checkin_enabled"] = 1
            self.assertIs(appointments.appointment_detail(fake, "APT-1")["native_checkin_enabled"], False)
            fake._settings["clinic_ui_production_checkin_enabled"] = True
            fake._settings["clinic_ui_production_booking_enabled"] = False
            self.assertIs(appointments.appointment_detail(fake, "APT-1")["native_checkin_enabled"], True)
            fake._settings["clinic_ui_production_role_map"] = {"DOC": ["Reception"]}
            self.assertIs(appointments.appointment_detail(fake, "APT-1")["native_checkin_enabled"], False)

    def test_production_booking_only_advertised_for_opted_in_reception_with_safe_probes(self):
        fake = gateway(clinic_ui_production_booking_enabled=True,
                       clinic_ui_production_role_map={"REC": ["Reception"]})
        fake.current_session = session_view(roles=("Reception",))
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True), \
             patch.object(fake, "native_appointment_boundary_problem", return_value=None), \
             patch.object(fake, "healthcare_appointment_side_effects_unsafe", return_value=False), \
             patch.object(fake, "native_appointment_can_create", return_value=True), \
             patch.object(fake, "native_doctype_can_read", return_value=True):
            enabled = session.bootstrap(fake)["capabilities"]
            fake._settings["clinic_ui_production_booking_enabled"] = False
            disabled = session.bootstrap(fake)["capabilities"]
        self.assertEqual(enabled["write.appointment.book"], "enabled")
        self.assertEqual(disabled["write.appointment.book"], "read_only")
        self.assertEqual({k: v for k, v in enabled.items() if k != "write.appointment.book"},
                         {k: v for k, v in disabled.items() if k != "write.appointment.book"})

    def test_production_booking_fails_closed_on_config_role_and_probe_failures(self):
        cases = (
            ("config", 1), ("config", None), ("role", "NUR"),
            ("boundary", "unknown"), ("effects", None), ("effects", True),
            ("create", False), ("create", None), ("read", False), ("read", None),
            ("create", RuntimeError("probe unavailable")),
        )
        for kind, value in cases:
            with self.subTest(kind=kind, value=value):
                role = value if kind == "role" else "REC"
                fake = gateway(
                    clinic_ui_production_booking_enabled=value if kind == "config" else True,
                    clinic_ui_production_role_map={role: ["Staff"]},
                )
                fake.current_session = session_view(roles=("Staff",))
                results = {"boundary": None, "effects": False, "create": True, "read": True}
                if kind in results:
                    results[kind] = value
                def probe(name):
                    result = results[name]
                    if isinstance(result, Exception):
                        raise result
                    return result
                with patch.object(production_enrollment, "verify_production_enrollment", return_value=True), \
                     patch.object(fake, "native_appointment_boundary_problem", side_effect=lambda: probe("boundary")), \
                     patch.object(fake, "healthcare_appointment_side_effects_unsafe", side_effect=lambda: probe("effects")), \
                     patch.object(fake, "native_appointment_can_create", side_effect=lambda: probe("create")), \
                     patch.object(fake, "native_doctype_can_read", side_effect=lambda *_: probe("read")):
                    self.assertEqual(session.bootstrap(fake)["capabilities"]["write.appointment.book"], "read_only")

    def test_bootstrap_and_directory_use_enrollment_without_synthetic_settings(self):
        fake = gateway()
        with patch.dict(os.environ, {"CLINIC_UI_PRODUCTION_MARKER_PATH": "/proof/marker"}), \
             patch.object(production_enrollment, "verify_production_enrollment", return_value=True) as verify, \
             patch.object(session, "_patient_write_flags", side_effect=AssertionError("write probe")):
            payload = session.bootstrap(fake)
            listing = directory.synthetic_patients(fake)
        self.assertEqual(set(payload["capabilities"].values()), {"read_only"})
        self.assertEqual(listing["items"][0]["workspace_patient_id"], patient_ref("PAT-1"))
        self.assertEqual(verify.call_args.args[1], os.path.abspath("sites/client.example/private"))

    def test_invalid_enrollment_does_not_fall_back(self):
        fake = gateway()
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=False):
            with self.assertRaises(Denied):
                session.bootstrap(fake)
            with self.assertRaises(Denied):
                directory.synthetic_patients(fake)

    def test_missing_marker_is_denied_by_real_verifier(self):
        fake = gateway()
        with patch.dict(os.environ, {"CLINIC_UI_PRODUCTION_MARKER_PATH": "/nonexistent/clinic-ui-proof"}):
            with self.assertRaises(Denied):
                session.bootstrap(fake)

    def test_production_opt_in_requires_literal_boolean(self):
        fake = gateway(clinic_ui_production_read_enabled=1)
        with self.assertRaises(Denied):
            session.bootstrap(fake)

    def test_invalid_production_intent_does_not_fall_back_to_valid_legacy_site(self):
        fake = FakeGateway(
            site="marley.test",
            settings=settings(clinic_ui_production_read_enabled=1),
            current_session=session_view(),
            csrf_token="csrf-token-value",
        )
        with self.assertRaises(Denied):
            session.bootstrap(fake)
        with self.assertRaises(Denied):
            directory.synthetic_patients(fake)

    def test_wrong_resolved_site_denies_even_if_verifier_would_accept(self):
        fake = gateway()
        fake.frappe.local.site = "other.example"
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            with self.assertRaises(Denied):
                directory.synthetic_patients(fake)

    def test_directory_omits_denied_native_display_fields(self):
        fake = gateway()
        fake.patient_authority = {}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            self.assertEqual(directory.synthetic_patients(fake)["items"], [])

    def test_patient_profile_requires_discovery_and_native_field_read(self):
        fake = gateway()
        fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            self.assertFalse(patients.view(fake, patient_ref("PAT-1"))["editable"])
            with self.assertRaises(Denied):
                patients.view(fake, patient_ref("PAT-2"))
            self.assertNotIn("PAT-2", fake.patient_views)
            fake.readable_patients = ["PAT-1", "PAT-1"]
            with self.assertRaises(Denied):
                patients.view(fake, patient_ref("PAT-1"))
            fake.readable_patients = ["PAT-1"]
            fake.patient_view_detail = {}
            with self.assertRaises(Denied):
                patients.view(fake, patient_ref("PAT-1"))

    def test_appointments_discovery_and_detail_are_read_only(self):
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(roles=("Physician",))
        fake.readable_appointments = ["APT-1"]
        row = {"patient": "PAT-1", "appointment_date": "2026-11-22",
               "appointment_time": "09:30:00", "status": "Open"}
        fake.appointment_view_detail = {"APT-1": row}
        fake.appointment_detail_map = {"APT-1": dict(row, modified="2026-11-22T09:00:00")}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            listing = appointments.synthetic_appointments(fake)
            detail = appointments.appointment_detail(fake, "APT-1")
            self.assertEqual(len(listing["items"]), 1)
            self.assertEqual(detail["native_appointment_id"], "APT-1")
            self.assertFalse(detail["is_fixture"])
            self.assertIs(detail["native_checkin_enabled"], False)
            self.assertNotIn("native_checkin_enabled", listing["items"][0])
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "APT-2")
            self.assertNotIn("APT-2", fake.appointment_detail_calls)
            fake.readable_appointments = ["APT-1", "APT-1"]
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "APT-1")
            fake.readable_appointments = ["APT-1"]
            fake.appointment_detail_map = {}
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "APT-1")
            fake.appointment_detail_map = {"APT-1": dict(row, modified="2026-11-22T09:00:00")}
            fake.patient_read_authority = set()
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "APT-1")
            self.assertEqual(appointments.synthetic_appointments(fake)["items"], [])

    def test_proof_denial_blocks_profile_and_appointments(self):
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(roles=("Physician",))
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=False):
            for call in (
                lambda: patients.view(fake, "PAT-1"),
                lambda: appointments.synthetic_appointments(fake),
                lambda: appointments.appointment_detail(fake, "APT-1"),
            ):
                with self.assertRaises(Denied):
                    call()
        self.assertEqual(fake.patient_views, [])
        self.assertEqual(fake.appointment_views, [])

    def test_production_alias_prefixes_and_unmapped_appointment_role_are_denied(self):
        fake = gateway()
        fake.readable_patients = ["wpa1_invalid"]
        fake.readable_appointments = ["wsa1_invalid"]
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            with self.assertRaises(Denied):
                patients.view(fake, "wpa1_invalid")
            with self.assertRaises(Denied):
                appointments.synthetic_appointments(fake)
            fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
            fake.current_session = session_view(roles=("Physician",))
            fake.readable_appointments = ["wsa1_invalid"]
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "wsa1_invalid")

    def test_host_marker_presence_requires_explicit_true_opt_in(self):
        for enabled in (None, False):
            for marker in ("", "/invalid"):
                fake = FakeGateway(site="marley.test", settings=settings(
                    clinic_ui_production_read_enabled=enabled
                ), current_session=session_view(), csrf_token="csrf-token-value")
                with patch.dict(os.environ, {"CLINIC_UI_PRODUCTION_MARKER_PATH": marker}):
                    for call in (lambda: session.bootstrap(fake),
                                 lambda: directory.synthetic_patients(fake),
                                 lambda: patients.view(fake, "WS-1"),
                                 lambda: appointments.synthetic_appointments(fake),
                                 lambda: appointments.appointment_detail(fake, "APT-1")):
                        with self.assertRaises(Denied):
                            call()

    def test_directory_failure_denies_but_empty_list_succeeds(self):
        fake = gateway()
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            fake.readable_patients = None
            with self.assertRaises(Denied):
                directory.synthetic_patients(fake)
            fake.readable_patients = []
            self.assertEqual(directory.synthetic_patients(fake)["items"], [])
            with patch.object(fake, "list_readable_patients", side_effect=RuntimeError("query failed")):
                with self.assertRaises(Denied):
                    directory.synthetic_patients(fake)

    def test_details_use_exact_filtered_query_not_first_page(self):
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(roles=("Physician",))
        fake.readable_patients = [f"P-{n}" for n in range(200)] + ["PAT-1"]
        fake.readable_appointments = [f"A-{n}" for n in range(200)] + ["APT-1"]
        fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
        row = {"patient": "PAT-1", "appointment_date": "2026-11-22",
               "appointment_time": "09:30:00", "status": "Open"}
        fake.appointment_detail_map = {"APT-1": dict(row, modified="2026-11-22T09:00:00")}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            self.assertFalse(patients.view(fake, patient_ref("PAT-1"))["editable"])
            self.assertEqual(appointments.appointment_detail(fake, "APT-1")["native_appointment_id"], "APT-1")

    def test_appointment_excludes_patient_denied_by_query_filter(self):
        fake = gateway(clinic_ui_production_role_map={"DOC": ["Physician"]})
        fake.current_session = session_view(roles=("Physician",))
        fake.readable_patients = []
        fake.readable_appointments = ["APT-1"]
        row = {"patient": "PAT-1", "appointment_date": "2026-11-22",
               "appointment_time": "09:30:00", "status": "Open"}
        fake.appointment_view_detail = {"APT-1": row}
        fake.appointment_detail_map = {"APT-1": dict(row, modified="2026-11-22T09:00:00")}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            self.assertEqual(appointments.synthetic_appointments(fake)["items"], [])
            with self.assertRaises(Denied):
                appointments.appointment_detail(fake, "APT-1")

    def test_automatic_framework_roles_cannot_be_production_staff_roles(self):
        for role in ("All", " Guest ", "desk user", "Administrator"):
            fake = gateway(clinic_ui_production_role_map={"DOC": [role]})
            fake.current_session = session_view(roles=(role.strip(),))
            with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
                with self.assertRaises(Denied):
                    session.bootstrap(fake)

    def test_exact_query_malformed_and_failed_results_deny(self):
        fake = gateway()
        fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            for rows in ([{"name": "pat-1"}], [{"name": "PAT-1", "extra": 1}], None):
                fake.frappe.get_list = lambda *args, **kwargs: rows
                with self.assertRaises(Denied):
                    patients.view(fake, patient_ref("PAT-1"))
            fake.frappe.get_list = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError())
            with self.assertRaises(Denied):
                patients.view(fake, patient_ref("PAT-1"))
