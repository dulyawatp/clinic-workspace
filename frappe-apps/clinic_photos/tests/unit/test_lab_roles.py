"""Offline tests for the lab role plan and verification matrix.

No Frappe import, no site, no database, no network. These pin the approved plan
shape, the fail-closed conflicts, private artifact rules and matrix evaluation.
Installed-site permission proof requires the separate read-only ``verify --live``.
"""

import io
import json
import os
import stat
import tempfile
import unittest
from contextlib import redirect_stdout

from clinic_photos.tests import lab_roles

NONCE = "0123456789abcdef"
INVENTORY = {
    "site": lab_roles.SITE,
    "roles": ["Physician", "Nursing User", "Healthcare Administrator", "Guest"],
    "users": ["someone@example.test"],
}


def control_dir():
    path = tempfile.mkdtemp(prefix="lab-roles-")
    os.chmod(path, 0o700)
    return path


class PlanTests(unittest.TestCase):
    def test_plan_is_five_roles_and_seven_single_role_users(self):
        plan = lab_roles.build_plan(INVENTORY, NONCE)
        self.assertEqual([entry["role"] for entry in plan["roles"]],
                         list(lab_roles.ROLE_NAMES))
        self.assertEqual({entry["doctype"] for entry in plan["users"]}, {"User"})
        self.assertEqual(len(plan["users"]), 7)
        emails = [entry["email"] for entry in plan["users"]]
        self.assertEqual(len(set(emails)), 7)
        self.assertTrue(all(NONCE in email for email in emails))
        for entry in plan["users"]:
            self.assertEqual(entry["payload"]["roles"], [{"role": entry["role"]}])
            self.assertEqual(entry["payload"]["send_welcome_email"], 0)
            self.assertEqual(entry["payload"]["user_type"], "System User")
            self.assertNotIn("password", entry["payload"])
            self.assertNotIn(entry["role"], lab_roles.FORBIDDEN_ROLES)
        clinicians = {entry["role"] for entry in plan["users"] if entry["clinician"]}
        self.assertEqual(clinicians, set(lab_roles.REUSED_ROLES))

    def test_scope_is_only_role_and_user_documents(self):
        plan = lab_roles.build_plan(INVENTORY, NONCE)
        self.assertEqual({entry["doctype"] for entry in plan["roles"]}, {"Role"})
        self.assertTrue(lab_roles.assert_plan(plan))

    def test_site_and_nonce_are_exact(self):
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.build_plan(dict(INVENTORY, site="clinic-photo-accept"), NONCE)
        self.assertEqual(str(caught.exception), "site_mismatch")
        for nonce in ("", "abc", "0123456789ABCDEF", "zzzzzzzzzzzzzzzz"):
            with self.assertRaises(lab_roles.LabConflict) as caught:
                lab_roles.build_plan(INVENTORY, nonce)
            self.assertEqual(str(caught.exception), "run_nonce_invalid")

    def test_existing_role_is_a_conflict_not_adoption(self):
        for role in lab_roles.ROLE_NAMES:
            inventory = dict(INVENTORY, roles=INVENTORY["roles"] + [role])
            with self.assertRaises(lab_roles.LabConflict) as caught:
                lab_roles.build_plan(inventory, NONCE)
            self.assertEqual(str(caught.exception), f"role_exists.{role}")

    def test_missing_reused_role_refuses(self):
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.build_plan(dict(INVENTORY, roles=["Physician"]), NONCE)
        self.assertEqual(str(caught.exception), "reused_role_missing.Nursing User")

    def test_existing_user_collision_refuses(self):
        plan = lab_roles.build_plan(INVENTORY, NONCE)
        inventory = dict(INVENTORY, users=INVENTORY["users"] + [plan["users"][0]["email"]])
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.build_plan(inventory, NONCE)
        self.assertEqual(str(caught.exception), "user_exists.physician")

    def test_tampered_plan_refuses_extra_doctype(self):
        plan = lab_roles.build_plan(INVENTORY, NONCE)
        plan["users"][0]["payload"]["roles"].append({"role": "System Manager"})
        with self.assertRaises(lab_roles.LabConflict):
            lab_roles.assert_plan(plan)


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.control = control_dir()
        self.plan = lab_roles.build_plan(INVENTORY, NONCE)
        self.credentials = os.path.join(self.control, "credentials.json")
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.control):
            path = os.path.join(self.control, name)
            if not os.path.islink(path):
                os.chmod(path, 0o600)
            os.unlink(path)
        os.rmdir(self.control)

    def test_credentials_are_private_and_values_are_not_in_the_plan(self):
        count = lab_roles.write_credentials(self.credentials, self.plan)
        self.assertEqual(count, 7)
        self.assertEqual(stat.S_IMODE(os.lstat(self.credentials).st_mode), 0o600)
        records = lab_roles.read_credentials(self.credentials)
        self.assertEqual({record["user"] for record in records},
                         {entry["email"] for entry in self.plan["users"]})
        self.assertNotIn(records[0]["password"], json.dumps(self.plan))

    def test_overwrite_loose_mode_and_symlink_refuse(self):
        lab_roles.write_credentials(self.credentials, self.plan)
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.write_credentials(self.credentials, self.plan)
        self.assertEqual(str(caught.exception), "artifact_exists.credentials.json")
        os.chmod(self.credentials, 0o644)
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.read_credentials(self.credentials)
        self.assertEqual(str(caught.exception), "credentials_mode")
        os.chmod(self.credentials, 0o600)
        link = os.path.join(self.control, "link.json")
        os.symlink(self.credentials, link)
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.read_credentials(link)
        self.assertEqual(str(caught.exception), "credentials_not_regular")
        os.unlink(link)

    def test_artifacts_inside_the_repository_are_refused(self):
        repo_path = lab_roles.APP_ROOT / "tests" / "unit"
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.assert_external(repo_path)
        self.assertEqual(str(caught.exception), "artifact_inside_app")
        repo_dir = lab_roles.APP_ROOT.parents[1] / "docs"
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.assert_external(repo_dir)
        self.assertEqual(str(caught.exception), "artifact_inside_repo")

    def test_control_dir_must_be_0700(self):
        os.chmod(self.control, 0o750)
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.assert_external(self.control)
        self.assertEqual(str(caught.exception), "control_dir_mode")


class MatrixTests(unittest.TestCase):
    def observations(self):
        return {(actor, check): value
                for actor, checks in lab_roles.expected().items()
                for check, value in checks.items()}

    def test_approved_matrix_passes(self):
        self.assertEqual(lab_roles.evaluate(self.observations()), ())

    def test_guest_has_no_login_observation(self):
        self.assertEqual(set(lab_roles.expected()[lab_roles.GUEST]),
                         {"read_photo", "read_private", "list_photo", "write_photo"})

    def test_non_clinician_and_guest_reads_fail(self):
        observations = self.observations()
        observations[("clinic_lab_cashier", "read_photo")] = True
        observations[("clinic_lab_manager", "read_private")] = True
        observations[(lab_roles.GUEST, "list_photo")] = True
        failures = lab_roles.evaluate(observations)
        self.assertIn("mismatch.clinic_lab_cashier.read_photo", failures)
        self.assertIn("mismatch.clinic_lab_manager.read_private", failures)
        self.assertIn("mismatch.guest.list_photo", failures)

    def test_clinician_write_fails(self):
        observations = self.observations()
        observations[("physician", "write_photo")] = True
        self.assertIn("mismatch.physician.write_photo",
                      lab_roles.evaluate(observations))

    def test_missing_and_invariant_failures_fail_closed(self):
        observations = self.observations()
        del observations[("nursing_user", "login")]
        failures = lab_roles.evaluate(observations, ("docperm.cashier",))
        self.assertIn("missing.nursing_user.login", failures)
        self.assertIn("docperm.cashier", failures)


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.control = control_dir()
        self.inventory = os.path.join(self.control, "inventory.json")
        with open(self.inventory, "w", encoding="utf-8") as handle:
            json.dump(INVENTORY, handle)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for name in os.listdir(self.control):
            path = os.path.join(self.control, name)
            if not os.path.islink(path):
                os.chmod(path, 0o600)
            os.unlink(path)
        os.rmdir(self.control)

    def test_plan_requires_live_flag_and_writes_no_secret_to_stdout(self):
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.main(["plan", "--inventory", self.inventory,
                            "--control-dir", self.control, "--run-nonce", NONCE])
        self.assertEqual(str(caught.exception), "live_flag_required")
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = lab_roles.main(["plan", "--inventory", self.inventory,
                                   "--control-dir", self.control,
                                   "--run-nonce", NONCE, "--live"])
        self.assertEqual(code, 0)
        output = stream.getvalue()
        self.assertIn("plan ok roles=5 users=7", output)
        self.assertNotIn("password", output.lower())
        credentials = lab_roles.read_credentials(
            os.path.join(self.control, "credentials.json"))
        self.assertNotIn(credentials[0]["password"], output)
        self.assertEqual(
            stat.S_IMODE(os.lstat(os.path.join(self.control, "plan.json")).st_mode), 0o600)

    def test_verify_requires_live_flag(self):
        with self.assertRaises(lab_roles.LabConflict) as caught:
            lab_roles.main(["verify", "--control-dir", self.control,
                            "--photo-file", "F1", "--private-file", "F2"])
        self.assertEqual(str(caught.exception), "live_flag_required")


class FakeResponse:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self.payload = payload
        self.content = content

    def json(self):
        if self.payload is None:
            raise ValueError("no json")
        return self.payload


class FakeSession:
    """Records requests; responses resolved by the probe's test router."""

    def __init__(self, owner):
        self.owner = owner
        self.calls = []
        self.cookies = {}

    def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        self.owner.calls.append(("POST", url, kwargs))
        self.cookies["sid"] = "sid-value"
        return self.owner.login_response()

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        self.owner.calls.append(("GET", url, kwargs))
        return self.owner.router(url, kwargs)


class HttpProbeTests(unittest.TestCase):
    INFO = {
        "photo_doc": "PDOC",
        "file_url": "/private/files/photo.png",
        "file_name": "PHOTO-FILE",
        "private_url": "/private/files/private.png",
        "private_name": "PRIVATE-FILE",
        "private_size": 5,
    }

    def probe(self, router, login_response=None):
        probe = lab_roles.HttpProbe("http://frontend:8080", "site.test",
                                    session_factory=lambda: FakeSession(probe))
        probe.calls = []
        probe.router = router
        probe.login_response = login_response or (
            lambda: FakeResponse(200, {"message": "Logged In"}))
        probe.bind({"users": [{"email": "u@test", "key": "actor"}]})
        probe.prepare(self.INFO)
        return probe

    def test_private_bytes_fetch_the_private_url_not_the_photo_url(self):
        def router(url, _kwargs):
            if url.endswith(self.INFO["private_url"]):
                return FakeResponse(200, content=b"12345")
            if "/api/resource/Clinic Photo/" in url:
                return FakeResponse(200, {"data": {"name": "PDOC"}})
            return FakeResponse(200, {"data": [{"name": "PHOTO-FILE"}]})

        probe = self.probe(router)
        self.assertTrue(probe.login("u@test", "pw"))
        self.assertTrue(probe.read_photo("u@test"))
        self.assertTrue(probe.read_private("u@test"))
        self.assertTrue(probe.list_photo("u@test"))
        fetched = [url for (method, url, _kwargs) in probe.calls if method == "GET"]
        self.assertTrue(any(url.endswith(self.INFO["private_url"]) for url in fetched))
        self.assertFalse(any(url.endswith(self.INFO["file_url"]) for url in fetched))
        self.assertEqual(probe.failures, [])

    def test_private_size_is_compared_against_private_not_photo(self):
        def router(url, _kwargs):
            if url.endswith(self.INFO["private_url"]):
                return FakeResponse(200, content=b"abc")
            return FakeResponse(200, {"data": []})

        probe = self.probe(router)
        probe.login("u@test", "pw")
        self.assertFalse(probe.read_private("u@test"))
        self.assertEqual(probe.failures, ["bytes_mismatch.actor.read_private"])

    def test_transport_exception_is_a_failure_not_a_traceback(self):
        def router(_url, _kwargs):
            raise RuntimeError("boom")

        probe = self.probe(router)
        self.assertFalse(probe.read_private("u@test"))
        self.assertEqual(probe.failures, ["transport.actor.read_private.RuntimeError"])

    def test_login_transport_exception_is_recorded(self):
        def login_response():
            raise RuntimeError("offline")

        def router(_url, _kwargs):
            return FakeResponse(200, {"data": []})

        probe = self.probe(router, login_response=login_response)
        self.assertFalse(probe.login("u@test", "pw"))
        self.assertEqual(probe.failures, ["transport.actor.login.RuntimeError"])

    def test_guest_denial_uses_fresh_session_and_is_not_a_failure(self):
        def router(_url, _kwargs):
            return FakeResponse(403, {"exc": "PermissionError"})

        probe = self.probe(router)
        self.assertFalse(probe.list_photo(lab_roles.GUEST))
        self.assertFalse(probe.read_private(lab_roles.GUEST))
        self.assertEqual(probe.failures, [])

    def test_unexpected_status_is_recorded(self):
        def router(_url, _kwargs):
            return FakeResponse(500)

        probe = self.probe(router)
        self.assertFalse(probe.read_private("u@test"))
        self.assertEqual(probe.failures, ["http_status.actor.read_private.500"])


if __name__ == "__main__":
    unittest.main()
