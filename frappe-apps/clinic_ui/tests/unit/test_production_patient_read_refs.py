import base64
import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from clinic_ui.core import production_enrollment, production_patient_ref
from clinic_ui.endpoints import appointments, directory, patients, session
from .fake_gateway import Denied, session_view
from .test_endpoints_patient_view import DETAIL
from .test_production_read_adapter import gateway

KEY = bytes(range(64))
KEY_TEXT = base64.urlsafe_b64encode(KEY).rstrip(b"=").decode("ascii")
CREATION = datetime(2025, 1, 1, 12, 0, 0, 123456)


def configured():
    fake = gateway(clinic_ui_production_patient_ref_key=KEY_TEXT,
                   clinic_ui_production_role_map={"DOC": ["Physician"]})
    fake.current_session = session_view(roles=("Physician",))
    fake.frappe.db = SimpleNamespace(get_value=lambda doctype, name, field: CREATION)
    return fake


class PatientReferenceIntegrationTests(unittest.TestCase):
    def test_bootstrap_requires_canonical_separate_key(self):
        for key in (None, "", KEY_TEXT + "=", KEY_TEXT[:-1], "x" * 86):
            fake = configured()
            fake._settings["clinic_ui_production_patient_ref_key"] = key
            with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
                with self.assertRaises(Denied):
                    session.bootstrap(fake)

    def test_directory_profile_appointments_share_token_and_reject_raw_or_stale(self):
        fake = configured()
        fake.patient_view_detail = {"PAT-1": dict(DETAIL)}
        fake.readable_appointments = ["APT-1"]
        row = {"patient": "PAT-1", "appointment_date": "2026-11-22",
               "appointment_time": "09:30:00", "status": "Open"}
        fake.appointment_view_detail = {"APT-1": row}
        fake.appointment_detail_map = {"APT-1": dict(row, modified="2026-11-22T09:00:00")}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            ref = directory.synthetic_patients(fake)["items"][0]["workspace_patient_id"]
            self.assertEqual(ref, production_patient_ref.encode(KEY, fake.site, "PAT-1", CREATION))
            self.assertEqual(patients.view(fake, ref)["workspace_patient_id"], ref)
            self.assertEqual(appointments.synthetic_appointments(fake)["items"][0]["workspace_patient_id"], ref)
            self.assertEqual(appointments.appointment_detail(fake, "APT-1")["workspace_patient_id"], ref)
            for bad in ("PAT-1", ref + "x", production_patient_ref.encode(KEY, fake.site, "PAT-1", CREATION - timedelta(seconds=1))):
                with self.assertRaises(Denied):
                    patients.view(fake, bad)
            fake.patient_read_authority = set()
            with self.assertRaises(Denied):
                patients.view(fake, ref)
            self.assertEqual(directory.synthetic_patients(fake)["items"], [])

    def test_maximum_unicode_reference_survives_directory_and_profile(self):
        fake = configured()
        name = "😀" * 140
        fake.readable_patients = [name]
        fake.patient_authority = {name: "Patient"}
        fake.patient_view_detail = {name: dict(DETAIL)}
        with patch.object(production_enrollment, "verify_production_enrollment", return_value=True):
            ref = directory.synthetic_patients(fake)["items"][0]["workspace_patient_id"]
            self.assertEqual(len(ref), 808)
            self.assertEqual(patients.view(fake, ref)["workspace_patient_id"], ref)
