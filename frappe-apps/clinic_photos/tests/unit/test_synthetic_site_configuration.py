"""Focused tests for the explicit synthetic publication-site configuration.

The disposable lab site keeps its historical unconditional allowance. One
additional site is allowed only when the server site configuration names that
exact site; every other site, and every malformed or differently shaped
configuration value, stays denied. These tests drive the real modules through a
narrow fake Frappe module at the framework boundary. They do not prove installed
site configuration, installed HTTP or native permission behavior.
"""

import contextlib
import io
import types
import unittest
from unittest import mock

from fake_frappe import fake_frappe

from clinic_photos.api import photo_save
from clinic_photos.publication import (
    inspection,
    journal,
    launcher,
    process,
    sites,
    submission,
    validation,
)


AUTHOR = "clinician@example.test"
PATIENT = "SYNTHETIC-PATIENT-1"
OTHER_PATIENT = "SYNTHETIC-PATIENT-2"
DRAFT = "DRAFT-1"
PHOTO = "PHOTO-1"
LAB = "clinic-photo-accept.local"
MARLEY = "marley.local"
OTHER = "live.example"
ADDITIONAL_KEY = sites.ADDITIONAL_SITE_CONFIG_KEY
MODE_KEY = submission.SYNTHETIC_MODE_CONFIG_KEY
PATIENTS_KEY = "clinic_photos_synthetic_patients"
GATE = process.GATE_CONFIG_KEY
OPERATION_ID = "a" * 32

PNG_1X1_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGM4wcUFAAJ6AN1n"
    "J0J9AAAAAElFTkSuQmCC"
)


def docs(user=AUTHOR, enabled=1, role="Physician"):
    return {
        "User": {user: {"name": user, "enabled": enabled}},
        "Has Role": {
            f"has-role-{user}": {
                "name": f"has-role-{user}",
                "parent": user,
                "parenttype": "User",
                "role": role,
            }
        },
        "Patient": {PATIENT: {"name": PATIENT}},
        "Clinic Photo Draft": {
            DRAFT: {"name": DRAFT, "author": AUTHOR, "photo": PHOTO}
        },
        "Clinic Photo": {PHOTO: {"name": PHOTO, "patient_reference": PATIENT}},
    }


def allow_all_permissions(doctype, ptype="read", doc=None, user=None, **_kwargs):
    """Stand-in for the installed keyword-only permission facade."""
    return True


@contextlib.contextmanager
def runtime(site=LAB, conf=None):
    with fake_frappe(docs=docs(), user=AUTHOR) as frappe:
        frappe.conf = dict(conf or {})
        frappe.local.site = site
        frappe.has_permission = allow_all_permissions
        yield frappe


def synthetic_request():
    return validation.validate_request(
        {
            "kind": "synthetic_original_create",
            "request_key": "request-key-0002",
            "synthetic_patient": PATIENT,
            "image_base64": PNG_1X1_BASE64,
        }
    )


def annotation_request():
    return validation.validate_request(
        {
            "kind": "annotation_append",
            "request_key": "request-key-0001",
            "draft": DRAFT,
            "expected_version": 1,
            "original": PHOTO,
            "annotation": {
                "schema_version": 1,
                "coordinate_space": "normalized",
                "shapes": [],
            },
        }
    )


class SiteAllowlistTests(unittest.TestCase):
    def test_lab_site_stays_allowed_without_any_configuration(self):
        for conf in (
            None,
            {},
            {ADDITIONAL_KEY: None},
            {ADDITIONAL_KEY: OTHER},
            {GATE: True},
            {MODE_KEY: True},
        ):
            with self.subTest(conf=conf):
                self.assertTrue(sites.is_allowed(LAB, conf))

    def test_additional_site_is_denied_by_default(self):
        for conf in (
            None,
            {},
            {ADDITIONAL_KEY: ""},
            {ADDITIONAL_KEY: True},
            {ADDITIONAL_KEY: 1},
            {ADDITIONAL_KEY: [MARLEY]},
            {ADDITIONAL_KEY: (MARLEY,)},
            {ADDITIONAL_KEY: {MARLEY}},
            {ADDITIONAL_KEY: "Marley.local"},
            {ADDITIONAL_KEY: "MARLEY.LOCAL"},
            {ADDITIONAL_KEY: "marley.local "},
            {ADDITIONAL_KEY: " marley.local"},
            {ADDITIONAL_KEY: "marley.local."},
            {ADDITIONAL_KEY: OTHER},
            {MODE_KEY: True},
            {GATE: True},
        ):
            with self.subTest(conf=conf):
                self.assertFalse(sites.is_allowed(MARLEY, conf))

    def test_other_sites_stay_denied_whatever_is_configured(self):
        for conf in ({}, {ADDITIONAL_KEY: MARLEY}, {ADDITIONAL_KEY: OTHER}):
            for site in (
                OTHER,
                "other.local",
                "marley.local.example",
                "example.marley.local",
                "",
                None,
                True,
                ["marley.local"],
            ):
                with self.subTest(site=site, conf=conf):
                    self.assertFalse(sites.is_allowed(site, conf))

    def test_exactly_configured_marley_is_allowed(self):
        self.assertTrue(sites.is_allowed(MARLEY, {ADDITIONAL_KEY: MARLEY}))
        self.assertEqual(sites.candidate_sites(), frozenset({LAB, MARLEY}))


class EntrySyntheticModeTests(unittest.TestCase):
    def test_configured_marley_passes_the_entry_synthetic_gate(self):
        with runtime(site=MARLEY, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: True}):
            photo_save.require_synthetic_mode()

    def test_marley_is_refused_without_the_explicit_site_configuration(self):
        for conf in (
            {MODE_KEY: True},
            {ADDITIONAL_KEY: None, MODE_KEY: True},
            {ADDITIONAL_KEY: OTHER, MODE_KEY: True},
            {ADDITIONAL_KEY: [MARLEY], MODE_KEY: True},
        ):
            with self.subTest(conf=conf):
                with runtime(site=MARLEY, conf=conf) as frappe:
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.require_synthetic_mode()
                self.assertEqual(str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED)

    def test_configured_marley_still_requires_the_exact_synthetic_mode_flag(self):
        for mode in (False, None, 1, "true"):
            with self.subTest(mode=mode):
                with runtime(
                    site=MARLEY, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: mode}
                ) as frappe:
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.require_synthetic_mode()
                self.assertEqual(str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED)

    def test_unconfigured_lab_site_keeps_its_historical_allowance(self):
        with runtime(site=LAB, conf={MODE_KEY: True}):
            photo_save.require_synthetic_mode()

    def test_other_sites_are_refused_even_with_a_recognised_mode_flag(self):
        for site in (OTHER, "", None):
            with self.subTest(site=site):
                with runtime(
                    site=site, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: True}
                ) as frappe:
                    with self.assertRaises(frappe.PermissionError) as caught:
                        photo_save.require_synthetic_mode()
                self.assertEqual(str(caught.exception), photo_save.SAFE_SYNTHETIC_DENIED)


class SubmissionSyntheticTargetTests(unittest.TestCase):
    def test_configured_marley_target_passes_fresh_authority(self):
        with runtime(
            site=MARLEY,
            conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: True, PATIENTS_KEY: [PATIENT]},
        ):
            submission.require_target_authority(
                synthetic_request(), AUTHOR, MARLEY
            )

    def test_unconfigured_marley_target_is_denied(self):
        request = synthetic_request()
        for conf in ({MODE_KEY: True}, {ADDITIONAL_KEY: OTHER, MODE_KEY: True}):
            with self.subTest(conf=conf):
                with runtime(site=MARLEY, conf=conf):
                    with self.assertRaises(submission.SubmissionTargetDenied):
                        submission.require_target_authority(request, AUTHOR, MARLEY)

    def test_configured_marley_target_still_requires_the_synthetic_mode_flag(self):
        request = synthetic_request()
        for mode in (False, None, 1):
            with self.subTest(mode=mode):
                with runtime(
                    site=MARLEY, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: mode}
                ):
                    with self.assertRaises(submission.SubmissionTargetDenied):
                        submission.require_target_authority(request, AUTHOR, MARLEY)

    def test_other_sites_targets_are_denied(self):
        request = synthetic_request()
        with runtime(site=OTHER, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: True}):
            with self.assertRaises(submission.SubmissionTargetDenied):
                submission.require_target_authority(request, AUTHOR, OTHER)

    def test_lab_target_keeps_its_historical_authority(self):
        with runtime(site=LAB, conf={MODE_KEY: True}):
            submission.require_target_authority(synthetic_request(), AUTHOR, LAB)


class SyntheticPatientAllowlistTests(unittest.TestCase):
    """The additional site needs an exact configured Patient allowlist."""

    def marley_conf(self, patients):
        return {
            ADDITIONAL_KEY: MARLEY,
            MODE_KEY: True,
            PATIENTS_KEY: patients,
        }

    def test_exact_configured_patient_passes_and_is_read_after_membership(self):
        with runtime(site=MARLEY, conf=self.marley_conf([PATIENT])) as frappe:
            with mock.patch.object(
                frappe.db, "exists", wraps=frappe.db.exists
            ) as exists:
                submission.require_target_authority(
                    synthetic_request(), AUTHOR, MARLEY
                )
            exists.assert_called_once_with("Patient", PATIENT)

    def test_missing_or_unlisted_patient_is_denied_without_record_reads(self):
        request = synthetic_request()
        for patients in (None, [], [OTHER_PATIENT], ["SYNTHETIC-PATIENT-10"]):
            conf = self.marley_conf([PATIENT])
            if patients is None:
                conf.pop(PATIENTS_KEY)
            else:
                conf[PATIENTS_KEY] = patients
            with self.subTest(patients=patients):
                with runtime(site=MARLEY, conf=conf) as frappe:
                    with mock.patch.object(frappe, "has_permission") as permission:
                        with mock.patch.object(frappe.db, "exists") as exists:
                            with self.assertRaises(
                                submission.SubmissionTargetDenied
                            ):
                                submission.require_target_authority(
                                    request, AUTHOR, MARLEY
                                )
                    exists.assert_not_called()
                    permission.assert_not_called()

    def test_malformed_allowlists_fail_closed_without_record_reads(self):
        request = synthetic_request()
        for patients in (
            PATIENT,
            PATIENT + " ",
            {"patient": PATIENT},
            [PATIENT, 1],
            [PATIENT, None],
            [PATIENT, ""],
            [PATIENT, {"name": PATIENT}],
            (PATIENT, 7),
            [PATIENT + "\n"],
            [PATIENT, OTHER_PATIENT + " "],
            True,
            1,
            {"allow": [PATIENT]},
        ):
            with self.subTest(patients=patients):
                with runtime(
                    site=MARLEY, conf=self.marley_conf(patients)
                ) as frappe:
                    with mock.patch.object(frappe, "has_permission") as permission:
                        with mock.patch.object(frappe.db, "exists") as exists:
                            with self.assertRaises(
                                submission.SubmissionTargetDenied
                            ):
                                submission.require_target_authority(
                                    request, AUTHOR, MARLEY
                                )
                    exists.assert_not_called()
                    permission.assert_not_called()

    def test_lab_site_does_not_require_the_allowlist(self):
        for conf in (
            {MODE_KEY: True},
            {MODE_KEY: True, PATIENTS_KEY: [OTHER_PATIENT]},
            {MODE_KEY: True, PATIENTS_KEY: [PATIENT, 7]},
        ):
            with self.subTest(conf=conf):
                with runtime(site=LAB, conf=conf):
                    submission.require_target_authority(
                        synthetic_request(), AUTHOR, LAB
                    )

    def test_status_resolution_applies_the_same_allowlist(self):
        from clinic_photos.publication import inspection
        def verified(site):
            return types.SimpleNamespace(
                id=OPERATION_ID,
                actor=AUTHOR,
                site=site,
                state="SUBMITTED",
                kind="synthetic_original_create",
                request=synthetic_request(),
            )

        with runtime(site=MARLEY, conf=self.marley_conf([PATIENT])) as frappe:
            with mock.patch.object(
                inspection.journal,
                "load_verified_operation",
                return_value=verified(MARLEY),
            ):
                outcome = inspection.resolve_operation(OPERATION_ID, actor=AUTHOR)
        self.assertEqual(outcome.status, "pending")

        with runtime(site=MARLEY, conf=self.marley_conf([OTHER_PATIENT])) as frappe:
            with mock.patch.object(
                inspection.journal,
                "load_verified_operation",
                return_value=verified(MARLEY),
            ):
                with mock.patch.object(frappe.db, "exists") as exists:
                    with self.assertRaises(inspection.InspectionDenied):
                        inspection.resolve_operation(OPERATION_ID, actor=AUTHOR)
            exists.assert_not_called()


class FakeChildFrappe:
    """Only the attributes ``process.run`` touches before actor adoption."""

    def __init__(self, conf):
        self.conf = dict(conf)
        self.local = types.SimpleNamespace(site="")
        self.init_calls = []
        self.connect_calls = []
        self.set_user_calls = []
        self.destroy_calls = 0

    def init(self, site="", sites_path=".", **_kwargs):
        self.init_calls.append((site, sites_path))
        self.local.site = site

    def connect(self, set_admin_as_user=True):
        self.connect_calls.append(set_admin_as_user)

    def set_user(self, user):
        self.set_user_calls.append(user)

    def destroy(self):
        self.destroy_calls += 1


class ChildProcessSiteTests(unittest.TestCase):
    def test_child_accepts_only_candidate_sites_before_bootstrap(self):
        self.assertTrue(process._valid_site(LAB))
        self.assertTrue(process._valid_site(MARLEY))
        for site in (OTHER, "", None, True, "MARLEY.LOCAL", "marley.local "):
            with self.subTest(site=site):
                self.assertFalse(process._valid_site(site))
        self.assertEqual(process.ALLOWED_SITES, frozenset({LAB, MARLEY}))

    def run_child(self, conf):
        child = FakeChildFrappe(conf)
        clean = mock.Mock(return_value=False)
        output = io.StringIO()
        with mock.patch.object(process, "_frappe", return_value=child):
            with mock.patch.object(process, "_context_is_clean", clean):
                with contextlib.redirect_stdout(output):
                    code = process.run(OPERATION_ID, site=MARLEY)
        return code, output.getvalue(), child, clean

    def test_child_refuses_unconfigured_marley_before_the_next_gate(self):
        code, output, child, clean = self.run_child({GATE: True})

        self.assertEqual(code, process.EXIT_REFUSED)
        self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")
        self.assertEqual(child.init_calls, [(MARLEY, process.SITES_PATH)])
        self.assertEqual(child.connect_calls, [False])
        self.assertEqual(child.set_user_calls, [])
        self.assertEqual(child.destroy_calls, 1)
        clean.assert_not_called()

    def test_child_passes_the_site_gate_for_configured_marley(self):
        code, output, child, clean = self.run_child(
            {GATE: True, ADDITIONAL_KEY: MARLEY}
        )

        self.assertEqual(code, process.EXIT_REFUSED)
        self.assertEqual(output, f"status=refused operation_id={OPERATION_ID}\n")
        self.assertEqual(child.set_user_calls, [])
        clean.assert_called()


class LauncherSiteTests(unittest.TestCase):
    def fake(self, site, conf):
        return types.SimpleNamespace(
            local=types.SimpleNamespace(site=site), conf=dict(conf)
        )

    def test_launcher_accepts_the_lab_site_without_configuration(self):
        self.assertEqual(launcher._server_site(self.fake(LAB, {})), LAB)
        self.assertEqual(launcher.ALLOWED_SITES, frozenset({LAB, MARLEY}))

    def test_launcher_refuses_unconfigured_marley(self):
        for conf in ({}, {GATE: True}, {ADDITIONAL_KEY: OTHER}):
            with self.subTest(conf=conf):
                with self.assertRaises(launcher.LauncherUnavailable):
                    launcher._server_site(self.fake(MARLEY, conf))

    def test_launcher_accepts_exactly_configured_marley(self):
        self.assertEqual(
            launcher._server_site(self.fake(MARLEY, {ADDITIONAL_KEY: MARLEY})),
            MARLEY,
        )

    def test_launcher_refuses_other_sites(self):
        for site in (OTHER, "", None, "marley.local.example"):
            with self.subTest(site=site):
                with self.assertRaises(launcher.LauncherUnavailable):
                    launcher._server_site(
                        self.fake(site, {ADDITIONAL_KEY: MARLEY, GATE: True})
                    )


class AnnotationKindSiteTests(unittest.TestCase):
    """The additional site accepts only the synthetic kind; the lab keeps both."""

    def marley_conf(self):
        return {ADDITIONAL_KEY: MARLEY, MODE_KEY: True, PATIENTS_KEY: [PATIENT]}

    def test_marley_annotation_append_is_denied_before_target_reads(self):
        request = annotation_request()
        for conf in (
            self.marley_conf(),
            {ADDITIONAL_KEY: MARLEY, MODE_KEY: True},
            {ADDITIONAL_KEY: MARLEY, MODE_KEY: True, PATIENTS_KEY: [OTHER_PATIENT]},
            {ADDITIONAL_KEY: MARLEY, MODE_KEY: False},
        ):
            with self.subTest(conf=conf):
                with runtime(site=MARLEY, conf=conf) as frappe:
                    with mock.patch.object(
                        frappe.db, "get_value", wraps=frappe.db.get_value
                    ) as get_value:
                        with mock.patch.object(
                            frappe, "has_permission"
                        ) as has_permission:
                            with self.assertRaises(
                                submission.SubmissionTargetDenied
                            ):
                                submission.require_target_authority(
                                    request, AUTHOR, MARLEY
                                )
                    # Only the fresh actor lookup runs; no draft/original row is read.
                    self.assertTrue(get_value.call_args_list)
                    for call in get_value.call_args_list:
                        self.assertEqual(call.args[0], "User")
                    has_permission.assert_not_called()

    def test_marley_annotation_staging_is_denied_before_the_journal(self):
        with runtime(site=MARLEY, conf=self.marley_conf()) as frappe:
            with mock.patch.object(journal, "stage_submission") as stage:
                with self.assertRaises(submission.SubmissionTargetDenied):
                    submission.stage_submission(annotation_request(), AUTHOR)
            stage.assert_not_called()

    def test_marley_annotation_status_is_denied_without_record_reads(self):
        verified = types.SimpleNamespace(
            id=OPERATION_ID,
            actor=AUTHOR,
            site=MARLEY,
            state="SUBMITTED",
            kind="annotation_append",
            request=annotation_request(),
        )
        with runtime(site=MARLEY, conf=self.marley_conf()) as frappe:
            with mock.patch.object(
                inspection.journal, "load_verified_operation", return_value=verified
            ):
                with mock.patch.object(frappe.db, "get_value") as get_value:
                    with self.assertRaises(inspection.InspectionDenied):
                        inspection.resolve_operation(OPERATION_ID, actor=AUTHOR)
            for call in get_value.call_args_list:
                self.assertEqual(call.args[0], "User")

    def test_lab_annotation_append_keeps_its_historical_authority(self):
        with runtime(site=LAB, conf={}) as frappe:
            submission.require_target_authority(annotation_request(), AUTHOR, LAB)

    def test_lab_annotation_append_is_unaffected_by_the_additional_site_config(self):
        with runtime(
            site=LAB,
            conf={
                ADDITIONAL_KEY: MARLEY,
                MODE_KEY: True,
                PATIENTS_KEY: [OTHER_PATIENT],
            },
        ):
            submission.require_target_authority(annotation_request(), AUTHOR, LAB)

    def test_other_sites_still_deny_annotation_append(self):
        with runtime(site=OTHER, conf={ADDITIONAL_KEY: MARLEY, MODE_KEY: True}):
            with self.assertRaises(submission.SubmissionTargetDenied):
                submission.require_target_authority(
                    annotation_request(), AUTHOR, OTHER
                )


if __name__ == "__main__":
    unittest.main()
