"""Pure tests for the explicit site-configuration gate.

Every malformed or ambiguous value must yield ``None`` (fail closed) and must
never be repaired by inference. No Frappe import, no I/O.
"""

import unittest

from clinic_ui.core import config

SITE = "marley.test"
SECRET = "0123456789abcdef0123456789abcdef"


def getter(values):
    return values.get


def load(values, site=SITE):
    return config.load_settings(getter(values), site)


def base(**overrides):
    values = {
        config.ENABLED_KEY: 1,
        config.SITE_KEY: SITE,
        config.EPOCH_SECRET_KEY: SECRET,
        config.ROLE_MAP_KEY: {
            "DOC": ["Physician"],
            "REC": ["Clinic Reception"],
            "NUR": ["Nurse"],
        },
        config.ALLOWLIST_KEY: [
            {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"}
        ],
    }
    values.update(overrides)
    return values


class DisabledAndSiteGateTests(unittest.TestCase):
    def test_absent_configuration_is_disabled(self):
        self.assertIsNone(load({}))

    def test_falsey_enabled_flag_is_disabled(self):
        for value in (0, "0", "false", "no", "", None, "yes please"):
            with self.subTest(value=value):
                self.assertIsNone(load(base(**{config.ENABLED_KEY: value})))

    def test_other_site_is_refused_even_when_enabled(self):
        self.assertIsNone(load(base(**{config.SITE_KEY: "lab.test"})))

    def test_missing_or_blank_site_is_refused(self):
        self.assertIsNone(load(base(**{config.SITE_KEY: ""})))
        self.assertIsNone(load(base(**{config.SITE_KEY: None})))

    def test_current_site_must_be_known(self):
        self.assertIsNone(load(base(), site=""))
        self.assertIsNone(load(base(), site=None))


class ValidSettingsTests(unittest.TestCase):
    def test_exact_configuration_parses(self):
        settings = load(base())
        self.assertIsNotNone(settings)
        self.assertEqual(settings.epoch_secret, SECRET.encode())
        self.assertEqual(
            settings.allowlist, (("WS-1", "PAT-1"),)
        )
        self.assertEqual(
            settings.as_role_map(),
            {
                "DOC": ("Physician",),
                "REC": ("Clinic Reception",),
                "NUR": ("Nurse",),
            },
        )

    def test_empty_allowlist_is_valid_and_empty(self):
        settings = load(base(**{config.ALLOWLIST_KEY: []}))
        self.assertIsNotNone(settings)
        self.assertEqual(settings.allowlist, ())

    def test_role_map_subset_is_valid(self):
        settings = load(base(**{config.ROLE_MAP_KEY: {"DOC": ["Physician"]}}))
        self.assertIsNotNone(settings)
        self.assertEqual(set(settings.as_role_map()), {"DOC"})


class RoleMapValidationTests(unittest.TestCase):
    def test_invalid_role_maps_are_refused(self):
        cases = {
            "not a mapping": ["DOC"],
            "empty": {},
            "unknown code": {"XYZ": ["Pharmacist"]},
            "lowercase code": {"doc": ["Physician"]},
            "value not a sequence": {"DOC": "Physician"},
            "empty sequence": {"DOC": []},
            "blank role name": {"DOC": ["  "]},
            "non-string role name": {"DOC": [1]},
            "duplicate role name": {"DOC": ["Physician", "Physician"]},
            "shared role across codes": {
                "DOC": ["Clinician"],
                "NUR": ["Clinician"],
            },
            "oversized role name": {"DOC": ["x" * 141]},
        }
        for name, role_map in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(load(base(**{config.ROLE_MAP_KEY: role_map})))


class SecretValidationTests(unittest.TestCase):
    def test_absent_short_or_non_string_secret_is_refused(self):
        for value in (None, "", "short", b"0123456789abcdef0123456789abcdef"):
            with self.subTest(value=value):
                self.assertIsNone(load(base(**{config.EPOCH_SECRET_KEY: value})))

    def test_minimum_length_secret_is_accepted(self):
        self.assertIsNotNone(
            load(base(**{config.EPOCH_SECRET_KEY: "s" * config.MIN_EPOCH_SECRET_LENGTH}))
        )


class NativeWriteGateTests(unittest.TestCase):
    APPROVED = config.APPROVED_WRITE_SITE

    def approved_base(self, **overrides):
        values = {
            config.SITE_KEY: self.APPROVED,
            config.WRITE_ENABLED_KEY: 1,
            config.WRITE_SITE_KEY: self.APPROVED,
        }
        values.update(overrides)
        return base(**values)

    def test_write_capability_is_off_by_default(self):
        settings = load(
            base(**{config.SITE_KEY: self.APPROVED}), site=self.APPROVED
        )
        self.assertFalse(settings.write_enabled)

    def test_write_capability_requires_its_own_flag_and_exact_site(self):
        settings = load(self.approved_base(), site=self.APPROVED)
        self.assertTrue(settings.write_enabled)

    def test_read_enabled_does_not_enable_writes(self):
        for value in (0, "0", "false", "no", "", None):
            with self.subTest(value=value):
                settings = load(
                    self.approved_base(**{config.WRITE_ENABLED_KEY: value}),
                    site=self.APPROVED,
                )
                self.assertFalse(settings.write_enabled)

    def test_write_site_must_match_the_approved_site_exactly(self):
        for value in (
            None,
            "",
            "marley.local",
            "other.local",
            "marley-sit.local ",
            5,
        ):
            with self.subTest(value=value):
                settings = load(
                    self.approved_base(**{config.WRITE_SITE_KEY: value}),
                    site=self.APPROVED,
                )
                self.assertFalse(settings.write_enabled)

    def test_a_non_approved_current_site_is_refused_even_with_matching_keys(self):
        for current in ("marley.test", "marley.local", "other.local"):
            with self.subTest(current=current):
                settings = load(
                    base(
                        **{
                            config.SITE_KEY: current,
                            config.WRITE_ENABLED_KEY: 1,
                            config.WRITE_SITE_KEY: current,
                        }
                    ),
                    site=current,
                )
                self.assertIsNotNone(settings)
                self.assertFalse(settings.write_enabled)

    def test_approved_name_on_a_different_current_site_is_refused(self):
        settings = load(
            self.approved_base(),
            site="marley.local",
        )
        self.assertIsNone(settings)

class AllowlistValidationTests(unittest.TestCase):
    def test_malformed_allowlists_are_refused(self):
        cases = {
            "not a sequence": {"WS-1": "PAT-1"},
            "entry not a mapping": ["WS-1"],
            "missing native id": [{"workspace_patient_id": "WS-1"}],
            "extra field": [
                {
                    "workspace_patient_id": "WS-1",
                    "native_patient_id": "PAT-1",
                    "display_name": "Injected",
                }
            ],
            "blank workspace id": [
                {"workspace_patient_id": "  ", "native_patient_id": "PAT-1"}
            ],
            "blank native id": [
                {"workspace_patient_id": "WS-1", "native_patient_id": ""}
            ],
            "control character": [
                {"workspace_patient_id": "WS\x001", "native_patient_id": "PAT-1"}
            ],
            "oversized identifier": [
                {
                    "workspace_patient_id": "W" * 257,
                    "native_patient_id": "PAT-1",
                }
            ],
            "duplicate workspace id": [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-2"},
            ],
            "duplicate native id": [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-1"},
            ],
            "workspace equals native": [
                {"workspace_patient_id": "SAME", "native_patient_id": "SAME"}
            ],
            "workspace matches another native id": [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                {"workspace_patient_id": "PAT-1", "native_patient_id": "PAT-2"},
            ],
            "native matches another workspace id": [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                {"workspace_patient_id": "WS-2", "native_patient_id": "WS-1"},
            ],
            "too many entries": [
                {
                    "workspace_patient_id": f"WS-{index}",
                    "native_patient_id": f"PAT-{index}",
                }
                for index in range(config.MAX_ALLOWLIST_ENTRIES + 1)
            ],
        }
        for name, allowlist in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(load(base(**{config.ALLOWLIST_KEY: allowlist})))

    def test_allowlist_is_trimmed_and_order_preserved(self):
        settings = load(
            base(
                **{
                    config.ALLOWLIST_KEY: [
                        {
                            "workspace_patient_id": " WS-2 ",
                            "native_patient_id": " PAT-2",
                        },
                        {
                            "workspace_patient_id": "WS-1",
                            "native_patient_id": "PAT-1 ",
                        },
                    ]
                }
            )
        )
        self.assertEqual(settings.allowlist, (("WS-2", "PAT-2"), ("WS-1", "PAT-1")))

    def test_allowlist_is_immutable(self):
        settings = load(base())
        with self.assertRaises((AttributeError, TypeError)):
            settings.allowlist[0] = ("WS-9", "PAT-9")

    def test_appointment_allowlist_defaults_to_empty(self):
        settings = load(base())
        self.assertEqual(settings.appointment_allowlist, ())

    def test_appointment_allowlist_is_parsed_and_order_preserved(self):
        settings = load(
            base(
                **{
                    config.APPOINTMENTS_KEY: [
                        {
                            "workspace_appointment_id": " APPT-2 ",
                            "native_appointment_id": " NATIVE-2",
                        },
                        {
                            "workspace_appointment_id": "APPT-1",
                            "native_appointment_id": "NATIVE-1 ",
                        },
                    ]
                }
            )
        )
        self.assertEqual(
            settings.appointment_allowlist,
            (("APPT-2", "NATIVE-2"), ("APPT-1", "NATIVE-1")),
        )

    def test_malformed_appointment_allowlist_fails_closed(self):
        cases = {
            "not a sequence": {"APPT-1": "NATIVE-1"},
            "entry not a mapping": ["APPT-1"],
            "missing native id": [{"workspace_appointment_id": "APPT-1"}],
            "extra field": [
                {
                    "workspace_appointment_id": "APPT-1",
                    "native_appointment_id": "NATIVE-1",
                    "patient": "PAT-1",
                }
            ],
            "blank workspace id": [
                {"workspace_appointment_id": "  ", "native_appointment_id": "NATIVE-1"}
            ],
            "blank native id": [
                {"workspace_appointment_id": "APPT-1", "native_appointment_id": ""}
            ],
            "duplicate workspace id": [
                {"workspace_appointment_id": "APPT-1", "native_appointment_id": "NATIVE-1"},
                {"workspace_appointment_id": "APPT-1", "native_appointment_id": "NATIVE-2"},
            ],
            "duplicate native id": [
                {"workspace_appointment_id": "APPT-1", "native_appointment_id": "NATIVE-1"},
                {"workspace_appointment_id": "APPT-2", "native_appointment_id": "NATIVE-1"},
            ],
            "workspace equals native": [
                {
                    "workspace_appointment_id": "SAME",
                    "native_appointment_id": "SAME",
                }
            ],
            "workspace matches another native id": [
                {
                    "workspace_appointment_id": "APPT-1",
                    "native_appointment_id": "NATIVE-1",
                },
                {
                    "workspace_appointment_id": "NATIVE-1",
                    "native_appointment_id": "NATIVE-2",
                },
            ],
            "native matches another workspace id": [
                {
                    "workspace_appointment_id": "APPT-1",
                    "native_appointment_id": "NATIVE-1",
                },
                {
                    "workspace_appointment_id": "APPT-2",
                    "native_appointment_id": "APPT-1",
                },
            ],
            "too many entries": [
                {
                    "workspace_appointment_id": f"APPT-{index}",
                    "native_appointment_id": f"NATIVE-{index}",
                }
                for index in range(config.MAX_ALLOWLIST_ENTRIES + 1)
            ],
        }
        for name, allowlist in cases.items():
            with self.subTest(case=name):
                self.assertIsNone(
                    load(base(**{config.APPOINTMENTS_KEY: allowlist}))
                )


class CombinedNamespaceTests(unittest.TestCase):
    PATIENTS = [
        {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
    ]

    def load_combined(self, patients, appointments):
        return load(
            base(
                **{
                    config.ALLOWLIST_KEY: patients,
                    config.APPOINTMENTS_KEY: appointments,
                }
            )
        )

    def test_disjoint_patient_and_appointment_namespaces_parse(self):
        settings = self.load_combined(
            self.PATIENTS,
            [
                {
                    "workspace_appointment_id": "APPT-1",
                    "native_appointment_id": "NATIVE-APPT-1",
                }
            ],
        )
        self.assertIsNotNone(settings)
        self.assertEqual(settings.allowlist, (("WS-1", "PAT-1"),))
        self.assertEqual(
            settings.appointment_allowlist,
            (("APPT-1", "NATIVE-APPT-1"),),
        )

    def test_patient_workspace_matching_appointment_native_is_refused(self):
        settings = self.load_combined(
            [
                {
                    "workspace_patient_id": "SHARED",
                    "native_patient_id": "PAT-1",
                }
            ],
            [
                {
                    "workspace_appointment_id": "APPT-1",
                    "native_appointment_id": "SHARED",
                }
            ],
        )
        self.assertIsNone(settings)

    def test_appointment_workspace_matching_patient_native_is_refused(self):
        settings = self.load_combined(
            [
                {
                    "workspace_patient_id": "WS-1",
                    "native_patient_id": "SHARED",
                }
            ],
            [
                {
                    "workspace_appointment_id": "SHARED",
                    "native_appointment_id": "NATIVE-APPT-1",
                }
            ],
        )
        self.assertIsNone(settings)


class AppointmentWriteSettingsTests(unittest.TestCase):
    APPROVED = config.APPROVED_APPOINTMENT_WRITE_SITE

    def values(self, **overrides):
        values = {
            config.ENABLED_KEY: 1,
            config.SITE_KEY: self.APPROVED,
            config.EPOCH_SECRET_KEY: SECRET,
            config.ROLE_MAP_KEY: {"REC": ["Clinic Reception"]},
            config.ALLOWLIST_KEY: [
                {"workspace_patient_id": "WS-1", "native_patient_id": "PAT-1"},
                {"workspace_patient_id": "WS-2", "native_patient_id": "PAT-2"},
            ],
            config.APPOINTMENT_WRITE_ENABLED_KEY: 1,
            config.APPOINTMENT_WRITE_SITE_KEY: self.APPROVED,
            config.APPOINTMENT_MEDICAL_DEPARTMENT_KEY: "MEDDEP-1",
            config.APPOINTMENT_TYPE_KEY: "APPT-TYPE-1",
            config.APPOINTMENT_COMPANY_KEY: "COMP-1",
            config.APPOINTMENT_CHECKIN_PATIENT_KEY: "WS-2",
        }
        values.update(overrides)
        return values

    def load_appointment(self, **overrides):
        return load(self.values(**overrides), site=self.APPROVED)

    def test_appointment_writes_are_off_by_default(self):
        settings = load(
            base(**{config.SITE_KEY: self.APPROVED}), site=self.APPROVED
        )
        self.assertIsNotNone(settings)
        self.assertFalse(settings.appointment_write_enabled)
        self.assertIsNone(settings.appointment_medical_department)
        self.assertIsNone(settings.appointment_type)
        self.assertIsNone(settings.appointment_checkin_patient)

    def test_exact_appointment_configuration_parses(self):
        settings = self.load_appointment()
        self.assertTrue(settings.appointment_write_enabled)
        self.assertEqual(settings.appointment_medical_department, "MEDDEP-1")
        self.assertEqual(settings.appointment_company, "COMP-1")
        self.assertEqual(settings.appointment_type, "APPT-TYPE-1")
        self.assertEqual(settings.appointment_checkin_patient, "WS-2")
        # The patient-read capability intent is untouched.
        self.assertFalse(settings.write_enabled)

    def test_requires_its_own_flag_and_exact_site(self):
        for value in (0, "0", "false", "", None):
            with self.subTest(value=value):
                settings = self.load_appointment(
                    **{config.APPOINTMENT_WRITE_ENABLED_KEY: value}
                )
                self.assertFalse(settings.appointment_write_enabled)
        for value in (None, "", "marley.local", "other.local", self.APPROVED + " "):
            with self.subTest(value=value):
                settings = self.load_appointment(
                    **{config.APPOINTMENT_WRITE_SITE_KEY: value}
                )
                self.assertFalse(settings.appointment_write_enabled)

    def test_non_approved_current_site_never_enables_appointment_writes(self):
        values = self.values(
            **{
                config.SITE_KEY: "marley.test",
                config.APPOINTMENT_WRITE_SITE_KEY: "marley.test",
            }
        )
        settings = load(values, site="marley.test")
        self.assertIsNotNone(settings)
        self.assertFalse(settings.appointment_write_enabled)

    def test_missing_or_malformed_target_configuration_fails_closed(self):
        for key in (
            config.APPOINTMENT_MEDICAL_DEPARTMENT_KEY,
            config.APPOINTMENT_TYPE_KEY,
            config.APPOINTMENT_COMPANY_KEY,
            config.APPOINTMENT_CHECKIN_PATIENT_KEY,
        ):
            for value in (None, "", "  ", "bad\x01name", "x" * 300):
                with self.subTest(key=key, value=value):
                    settings = self.load_appointment(**{key: value})
                    self.assertIsNotNone(settings)
                    self.assertFalse(settings.appointment_write_enabled)

    def test_checkin_patient_must_be_an_allowlisted_workspace_patient(self):
        for value in ("WS-9", "PAT-2", "wsa1_abc"):
            with self.subTest(value=value):
                settings = self.load_appointment(
                    **{config.APPOINTMENT_CHECKIN_PATIENT_KEY: value}
                )
                self.assertFalse(settings.appointment_write_enabled)

    def test_target_values_are_trimmed(self):
        settings = self.load_appointment(
            **{
                config.APPOINTMENT_MEDICAL_DEPARTMENT_KEY: " MEDDEP-1 ",
                config.APPOINTMENT_TYPE_KEY: " APPT-TYPE-1 ",
                config.APPOINTMENT_COMPANY_KEY: " COMP-1 ",
                config.APPOINTMENT_CHECKIN_PATIENT_KEY: " WS-2 ",
            }
        )
        self.assertEqual(settings.appointment_medical_department, "MEDDEP-1")
        self.assertEqual(settings.appointment_company, "COMP-1")
        self.assertEqual(settings.appointment_type, "APPT-TYPE-1")
        self.assertEqual(settings.appointment_checkin_patient, "WS-2")


if __name__ == "__main__":
    unittest.main()
