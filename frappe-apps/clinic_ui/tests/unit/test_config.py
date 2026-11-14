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
            "unknown code": {"PHA": ["Pharmacist"]},
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


if __name__ == "__main__":
    unittest.main()
