"""Unit tests for the local HTTP acceptance harness support.

These tests are standard-library only and never perform a network request. They use
a narrow fake opener to drive response verification, and they exercise manifest
validation, target/transport refusal, redirect/proxy/cookie hardening, body caps,
ZIP inspection and session redaction. No installed Frappe/nginx/TLS behaviour is
claimed here; Task 4 owns the separately authorized actual HTTP run.
"""

import contextlib
import email.message
import hashlib
import http.client
import importlib
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.parse
import urllib.request
import zipfile
from unittest import mock

from clinic_photos.tests import http_harness

APP_ROOT = pathlib.Path(__file__).resolve().parents[2]
SAMPLE_PATH = APP_ROOT / "clinic_photos" / "tests" / "http_manifest.sample.json"

SECRET_PREFIX = "SYNTHETIC-SESSION-SECRET-"
ORIGINAL_BYTES = b"SYNTHETIC-PHOTO-ORIGINAL-BYTES-DO-NOT-USE\n"
ANNOTATION_BYTES = (
    b'{"annotation_marker":"SYNTHETIC-ANNOTATION-NOT-REAL","strokes":[]}\n'
)
UNRELATED_BYTES = b"UNRELATED-FINANCE-STATEMENT-SYNTHETIC\n"


def session_env_names(manifest):
    return [
        actor["session_env"]
        for actor in manifest["actors"].values()
        if actor["session_env"]
    ]


def valid_manifest():
    """A structurally valid runtime manifest derived from the schema sample."""
    manifest = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
    manifest["target"]["base_url"] = "https://disposable-photos.internal.test"
    manifest["target"]["approved_target"] = "https://disposable-photos.internal.test"
    manifest["target"]["approval_reference"] = "APPROVED-DISPOSABLE-TARGET-REFERENCE"
    manifest["provisioner_attestation"]["attested_by"] = "provisioner@example.test"
    return manifest


class ManifestFile:
    """Write a manifest with restricted mode in an isolated temporary directory."""

    def __init__(self, testcase, manifest=None, mode=0o600, name="manifest.json"):
        self.testcase = testcase
        self.directory = tempfile.TemporaryDirectory()
        testcase.addCleanup(self.directory.cleanup)
        self.path = pathlib.Path(self.directory.name) / name
        self.path.write_text(
            json.dumps(manifest if manifest is not None else valid_manifest()),
            encoding="utf-8",
        )
        os.chmod(self.path, mode)

    def environ(self, base_url="https://disposable-photos.internal.test"):
        manifest = json.loads(self.path.read_text(encoding="utf-8"))
        environ = {
            http_harness.BASE_URL_ENV: base_url,
            http_harness.MANIFEST_ENV: str(self.path),
        }
        for index, name in enumerate(session_env_names(manifest)):
            environ[name] = f"sid={SECRET_PREFIX}{index}"
        return environ


def config_for(testcase, manifest=None, mode=0o600):
    holder = ManifestFile(testcase, manifest=manifest, mode=mode)
    return http_harness.load_config(holder.environ()), holder


def case_for(manifest, case_id):
    return next(case for case in manifest["cases"] if case["id"] == case_id)


def asset_bytes(name):
    return {
        "original": ORIGINAL_BYTES,
        "annotation": ANNOTATION_BYTES,
        "unrelated": UNRELATED_BYTES,
    }[name]


def zip_bytes(members, date_time=(2026, 11, 7, 12, 0, 0), comment=b""):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            info = zipfile.ZipInfo(name, date_time=date_time)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)
        archive.comment = comment
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"", declared=None):
        self.status = status
        self.headers = dict(headers or {})
        if declared is not None:
            self.headers["Content-Length"] = str(declared)
        self._body = body
        self._offset = 0
        self.closed = False

    def read(self, size=-1):
        if size is None or size < 0:
            chunk = self._body[self._offset :]
        else:
            chunk = self._body[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


class ParserFaultResponse:
    """A response whose body read raises a parser exception carrying server text."""

    def __init__(self, status=200, headers=None, error=None):
        self.status = status
        self.headers = dict(headers or {})
        self.error = error
        self.closed = False

    def read(self, size=-1):
        raise self.error

    def getcode(self):
        return self.status

    def close(self):
        self.closed = True


class FakeOpener:
    """Narrow transport seam: records the request, returns one prepared response."""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def open(self, request, timeout=None):
        self.calls.append((request, timeout))
        if self.error is not None:
            raise self.error
        return self.response


def result_for(manifest, case, body, *, status=None, headers=None):
    expect = case["expect"]
    if "asset" in expect:
        content_type = manifest["assets"][expect["asset"]]["content_type"]
    elif expect["body"].startswith("zip_"):
        content_type = "application/zip"
    else:
        content_type = "application/octet-stream"
    actual_headers = {
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "Expires": "0",
        "Content-Type": content_type,
    }
    actual_headers.update(headers or {})
    return http_harness.HttpResult(
        status=expect["status"] if status is None else status,
        headers=actual_headers,
        body=body,
    )


class ImportAndSampleTests(unittest.TestCase):
    def test_module_import_is_side_effect_free_and_framework_free(self):
        with mock.patch("socket.create_connection", side_effect=AssertionError):
            with mock.patch(
                "urllib.request.build_opener", side_effect=AssertionError
            ):
                reloaded = importlib.reload(http_harness)
                self.assertTrue(hasattr(reloaded, "ConfigError"))
                self.assertNotIn("frappe", reloaded.__dict__)
                self.assertEqual(reloaded.BASE_URL_ENV, "CLINIC_PHOTO_TEST_BASE_URL")
                self.assertEqual(
                    reloaded.MANIFEST_ENV, "CLINIC_PHOTO_TEST_MANIFEST"
                )

    def test_sample_manifest_is_structurally_valid(self):
        manifest = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(http_harness.validate_manifest(manifest), [])
        self.assertEqual(
            {case["id"] for case in manifest["cases"]},
            set(http_harness.REQUIRED_CASE_IDS),
        )

    def test_sample_carries_env_references_not_session_values(self):
        manifest = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        raw = SAMPLE_PATH.read_text(encoding="utf-8")
        for forbidden in ("sid=", "Cookie:", "Authorization:"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, raw)
        for actor in manifest["actors"].values():
            value = actor["session_env"]
            if value is not None:
                self.assertRegex(value, r"^CLINIC_PHOTO_TEST_SESSION_[A-Z_]+$")

    def test_download_route_matches_the_supplied_native_contract(self):
        self.assertEqual(
            http_harness.ROUTE_CONTRACT["native_download"]["path"],
            "/api/method/frappe.handler.download_file",
        )
        self.assertEqual(
            http_harness.ROUTE_CONTRACT["native_download"]["allowed_query"],
            ("file_url",),
        )
        paths = {
            contract["path"]
            for contract in http_harness.ROUTE_CONTRACT.values()
            if contract.get("path")
        }
        self.assertNotIn("frappe.core.api.file.download_file", paths)
        self.assertNotIn("download_alias_fid", http_harness.REQUIRED_ROUTE_SPEC)

    def test_shared_alias_uses_the_original_path_and_distinct_file_id(self):
        manifest = valid_manifest()
        original = manifest["files"]["original"]
        alias = manifest["files"]["alias"]
        self.assertEqual(alias["path"], original["path"])
        self.assertEqual(alias["asset"], "original")
        self.assertNotEqual(alias["fid"], original["fid"])

    def test_required_specs_keep_protected_assets_and_denial_status_policy(self):
        for case_id, spec in http_harness.REQUIRED_CASE_SPEC.items():
            with self.subTest(case=case_id):
                if spec["body"] == "denied" and spec["route"] != "internal_protected":
                    self.assertIn("original", spec["forbidden"])
                    self.assertIn("annotation", spec["forbidden"])
                    self.assertEqual(spec["status_kind"], "auth-denied")
                if spec["body"] in ("allowed_bytes", "zip_allowed"):
                    expected = set(spec["assets"])
                    if spec["asset"]:
                        expected.add(spec["asset"])
                    self.assertTrue(expected & {"original", "annotation"})
        self.assertEqual(
            http_harness.REQUIRED_CASE_SPEC["guest.internal_protected.denied"][
                "status_kind"
            ],
            "internal-not-found",
        )

    def test_unknown_top_level_and_case_keys_are_rejected(self):
        manifest = valid_manifest()
        manifest["unexpected"] = True
        self.assertTrue(
            any("unexpected" in problem for problem in http_harness.validate_manifest(manifest))
        )
        manifest = valid_manifest()
        case = case_for(manifest, http_harness.REQUIRED_CASE_IDS[0])
        case["expect"]["allow_redirects"] = True
        self.assertTrue(
            any(
                "allow_redirects" in problem
                for problem in http_harness.validate_manifest(manifest)
            )
        )
        manifest = valid_manifest()
        case = case_for(manifest, http_harness.REQUIRED_CASE_IDS[0])
        case["method"] = "POST"
        self.assertTrue(
            any("method" in problem for problem in http_harness.validate_manifest(manifest))
        )

    def test_redirect_status_is_not_accepted_as_denial_evidence(self):
        manifest = valid_manifest()
        case_for(manifest, "guest.direct.original.denied")["expect"]["status"] = 302
        self.assertTrue(
            any(
                "redirect" in problem.lower()
                for problem in http_harness.validate_manifest(manifest)
            )
        )


class ManifestTamperingTests(unittest.TestCase):
    def problems(self, mutate):
        manifest = valid_manifest()
        mutate(manifest)
        return http_harness.validate_manifest(manifest)

    def assert_problem(self, problems, fragment):
        self.assertTrue(
            any(fragment.lower() in problem.lower() for problem in problems),
            problems,
        )

    def test_case_actor_and_route_labels_cannot_be_reassigned(self):
        def actor(manifest):
            case_for(manifest, "guest.direct.original.denied")["actor"] = "author"

        def route(manifest):
            case_for(manifest, "guest.direct.original.denied")["route"] = (
                "download_original"
            )

        self.assert_problem(self.problems(actor), "actor must be")
        self.assert_problem(self.problems(route), "route must be")

    def test_body_mode_and_allowed_or_forbidden_members_cannot_be_weakened(self):
        def body(manifest):
            case_for(manifest, "author.direct.original.allowed")["expect"]["body"] = (
                "denied"
            )

        def omit_allowed(manifest):
            case_for(manifest, "author.zip.protected.allowed")["expect"]["assets"] = [
                "original"
            ]

        def omit_forbidden(manifest):
            case_for(manifest, "guest.direct.original.denied")["expect"][
                "forbidden_assets"
            ] = ["original"]

        def substitute_unrelated(manifest):
            case = case_for(manifest, "author.zip.protected.allowed")
            case["expect"]["assets"] = ["unrelated"]

        self.assert_problem(self.problems(body), "body mode")
        self.assert_problem(self.problems(omit_allowed), "allowed asset")
        self.assert_problem(self.problems(omit_forbidden), "forbidden asset")
        self.assert_problem(self.problems(substitute_unrelated), "allowed asset")

    def test_status_must_be_an_evidence_backed_auth_outcome(self):
        for case_id, status, fragment in (
            ("guest.direct.original.denied", 500, "401 or 403"),
            ("guest.direct.original.denied", 404, "401 or 403"),
            ("author.direct.original.allowed", 201, "exactly status 200"),
            ("guest.internal_protected.denied", 403, "exactly 404"),
        ):
            with self.subTest(case=case_id, status=status):
                def mutate(manifest, case_id=case_id, status=status):
                    case_for(manifest, case_id)["expect"]["status"] = status

                self.assert_problem(self.problems(mutate), fragment)

    def test_explicit_401_is_accepted_as_a_provisioned_auth_denial(self):
        manifest = valid_manifest()
        case_for(manifest, "guest.direct.original.denied")["expect"]["status"] = 401
        self.assertEqual(http_harness.validate_manifest(manifest), [])

    def test_named_route_semantics_cannot_be_repointed(self):
        def direct(manifest):
            manifest["routes"]["direct_original"]["file"] = "unrelated"

        def zip_route(manifest):
            manifest["routes"]["zip_protected"]["files"] = ["original"]

        def internal(manifest):
            manifest["routes"]["internal_protected"]["path"] = (
                "/protected/private/files/not-the-original.png"
            )

        self.assert_problem(self.problems(direct), "route file must be")
        self.assert_problem(self.problems(zip_route), "native ZIP file")
        self.assert_problem(self.problems(internal), "internal path must name")

    def test_file_role_assets_cannot_be_substituted(self):
        def mutate(manifest):
            manifest["files"]["original"]["asset"] = "unrelated"

        self.assert_problem(self.problems(mutate), "asset must be")

    def test_required_route_set_is_exact(self):
        def mutate(manifest):
            manifest["routes"]["unexpected_route"] = {
                "kind": "direct_private",
                "file": "original",
            }

        self.assert_problem(self.problems(mutate), "unexpected route")

    def test_download_route_cannot_accept_a_fid_argument(self):
        def mutate(manifest):
            manifest["routes"]["download_original"]["query"]["fid"] = (
                manifest["files"]["alias"]["fid"]
            )

        problems = self.problems(mutate)
        self.assertTrue(
            any("query key 'fid'" in problem for problem in problems), problems
        )

    def test_alias_route_must_name_the_alias_file_id(self):
        def mutate(manifest):
            manifest["routes"]["direct_alias"]["query"]["fid"] = (
                manifest["files"]["original"]["fid"]
            )

        problems = self.problems(mutate)
        self.assertTrue(
            any("route query" in problem for problem in problems), problems
        )


class SharedStorageAliasTests(unittest.TestCase):
    def problems(self, mutate):
        manifest = valid_manifest()
        mutate(manifest)
        return http_harness.validate_manifest(manifest)

    def test_alias_with_a_different_path_is_refused_even_with_equal_hash(self):
        def mutate(manifest):
            manifest["files"]["alias"]["path"] = (
                "/private/files/hash-equal-substitute.png"
            )

        problems = self.problems(mutate)
        self.assertTrue(
            any("shared-storage alias" in problem for problem in problems), problems
        )

    def test_alias_must_have_a_distinct_file_id(self):
        def mutate(manifest):
            manifest["files"]["alias"]["fid"] = manifest["files"]["original"]["fid"]

        problems = self.problems(mutate)
        self.assertTrue(any("distinct File ID" in problem for problem in problems), problems)

    def test_unintended_duplicate_storage_path_is_refused(self):
        def mutate(manifest):
            manifest["files"]["unrelated"]["path"] = manifest["files"]["original"]["path"]

        problems = self.problems(mutate)
        self.assertTrue(
            any("unintended duplicate" in problem for problem in problems), problems
        )

    def test_hash_equal_different_asset_substitute_is_refused(self):
        def mutate(manifest):
            manifest["assets"]["unrelated"]["sha256"] = manifest["assets"][
                "original"
            ]["sha256"]

        problems = self.problems(mutate)
        self.assertTrue(any("share a hash" in problem for problem in problems), problems)


class PathCanonicalizationTests(unittest.TestCase):
    def test_encoded_traversal_and_separators_are_refused_before_transport(self):
        for path in (
            "/private/files/%2e%2e/escape.png",
            "/private/files/%2E%2E/escape.png",
            "/private/files/a/%2f/b.png",
            "/private/files/a%5Cb.png",
            "/private/files/a%00b.png",
            "/private/files/%252e%252e.png",
            "/private/files/../escape.png",
            "/private/files/a//b.png",
            "/private/files/./x.png",
            "/private/files/a\\b.png",
            "/private/files/a\nb.png",
            "/private/files/a?x=1.png",
            "/private/files/a#fragment.png",
            "/private/files/trailing/",
        ):
            with self.subTest(path=path):
                manifest = valid_manifest()
                manifest["files"]["original"]["path"] = path
                manifest["files"]["alias"]["path"] = path
                self.assertTrue(http_harness.validate_manifest(manifest))

    def test_internal_path_encoding_is_refused(self):
        manifest = valid_manifest()
        manifest["routes"]["internal_protected"]["path"] = (
            "/protected/private/files/%2e%2e/escape.png"
        )
        self.assertTrue(http_harness.validate_manifest(manifest))

    def test_mutated_path_is_refused_at_request_build_without_transport(self):
        for case_id, mutate in (
            (
                "author.direct.original.allowed",
                lambda manifest: manifest["files"]["original"].__setitem__(
                    "path", "/private/files/%2e%2e/escape.png"
                ),
            ),
            (
                "guest.download.original.denied",
                lambda manifest: manifest["routes"]["download_original"][
                    "query"
                ].__setitem__("file_url", "/private/files/%2e%2e/escape.png"),
            ),
        ):
            with self.subTest(case=case_id):
                config, _ = config_for(self)
                mutate(config.manifest)
                opener = FakeOpener(FakeResponse(body=ORIGINAL_BYTES))
                with self.assertRaises(http_harness.ConfigError):
                    http_harness.execute_case(config, case_id, opener=opener)
                self.assertEqual(opener.calls, [])


class ConfigurationTests(unittest.TestCase):
    def test_missing_environment_is_a_configuration_error(self):
        with self.assertRaises(http_harness.ConfigError) as raised:
            http_harness.load_config({})
        message = str(raised.exception)
        self.assertIn(http_harness.BASE_URL_ENV, message)
        self.assertIn(http_harness.MANIFEST_ENV, message)

    def test_missing_manifest_file_is_refused(self):
        with self.assertRaises(http_harness.ConfigError):
            http_harness.load_config(
                {
                    http_harness.BASE_URL_ENV: "https://disposable.internal.test",
                    http_harness.MANIFEST_ENV: "/nonexistent/photos-manifest.json",
                }
            )

    def test_world_readable_manifest_is_refused(self):
        holder = ManifestFile(self, mode=0o644)
        with self.assertRaises(http_harness.ConfigError) as raised:
            http_harness.load_config(holder.environ())
        self.assertIn("access-restricted", str(raised.exception))

    def test_symlinked_manifest_is_refused(self):
        holder = ManifestFile(self)
        link = pathlib.Path(holder.directory.name) / "link.json"
        link.symlink_to(holder.path)
        environ = holder.environ()
        environ[http_harness.MANIFEST_ENV] = str(link)
        with self.assertRaises(http_harness.ConfigError):
            http_harness.load_config(environ)

    def test_sample_manifest_is_never_a_runtime_manifest(self):
        holder = ManifestFile(self, name="http_manifest.sample.json")
        with self.assertRaises(http_harness.ConfigError) as raised:
            http_harness.load_config(holder.environ())
        self.assertIn("sample", str(raised.exception))

    def test_unapproved_target_is_refused_before_transport_construction(self):
        holder = ManifestFile(self)
        with mock.patch.object(
            http_harness,
            "build_opener",
            side_effect=AssertionError("transport built before validation"),
        ):
            with self.assertRaises(http_harness.ConfigError):
                http_harness.load_config(
                    holder.environ(base_url="https://some-other-host.internal.test")
                )

    def test_evaluation_http_target_is_always_refused(self):
        manifest = valid_manifest()
        manifest["target"]["base_url"] = "http://10.0.100.10:8080"
        manifest["target"]["approved_target"] = "http://10.0.100.10:8080"
        holder = ManifestFile(self, manifest=manifest)
        with self.assertRaises(http_harness.ConfigError):
            http_harness.load_config(
                holder.environ(base_url="http://10.0.100.10:8080")
            )

    def test_plain_http_non_loopback_is_refused(self):
        manifest = valid_manifest()
        manifest["target"]["base_url"] = "http://disposable-photos.internal.test"
        manifest["target"]["approved_target"] = "http://disposable-photos.internal.test"
        holder = ManifestFile(self, manifest=manifest)
        with self.assertRaises(http_harness.ConfigError):
            http_harness.load_config(
                holder.environ(base_url="http://disposable-photos.internal.test")
            )

    def test_ssh_loopback_tunnel_requires_an_explicit_contract(self):
        manifest = valid_manifest()
        manifest["target"]["base_url"] = "http://127.0.0.1:9443"
        manifest["target"]["approved_target"] = "http://127.0.0.1:9443"
        manifest["target"]["transport"] = "ssh-tunnel"
        holder = ManifestFile(self, manifest=manifest)
        with self.assertRaises(http_harness.ConfigError):
            http_harness.load_config(holder.environ(base_url="http://127.0.0.1:9443"))

        manifest["target"]["tunnel_contract"] = {
            "local_host": "127.0.0.1",
            "local_port": 9443,
            "remote_approval_reference": "APPROVED-SSH-LOOPBACK-TUNNEL",
        }
        holder = ManifestFile(self, manifest=manifest)
        config = http_harness.load_config(holder.environ(base_url="http://127.0.0.1:9443"))
        self.assertEqual(config.base_url, "http://127.0.0.1:9443")

    def test_guest_cannot_have_a_session_reference(self):
        manifest = valid_manifest()
        manifest["actors"]["guest"]["session_env"] = "CLINIC_PHOTO_TEST_SESSION_GUEST"
        self.assertTrue(
            any(
                "guest" in problem.lower()
                for problem in http_harness.validate_manifest(manifest)
            )
        )

    def test_missing_actor_route_or_case_is_a_configuration_error(self):
        manifest = valid_manifest()
        del manifest["actors"]["disabled"]
        self.assertTrue(http_harness.validate_manifest(manifest))

        manifest = valid_manifest()
        del manifest["routes"]["zip_mixed"]
        self.assertTrue(http_harness.validate_manifest(manifest))

        manifest = valid_manifest()
        manifest["cases"].pop()
        self.assertTrue(http_harness.validate_manifest(manifest))

    def test_guessed_endpoint_or_unknown_route_kind_is_rejected(self):
        manifest = valid_manifest()
        manifest["routes"]["download_original"]["kind"] = "native_download_guess"
        self.assertTrue(http_harness.validate_manifest(manifest))

        manifest = valid_manifest()
        manifest["routes"]["download_original"]["query"]["path"] = (
            "/api/method/guessed.endpoint"
        )
        self.assertTrue(http_harness.validate_manifest(manifest))

    def test_missing_session_variable_is_refused_without_printing_values(self):
        holder = ManifestFile(self)
        environ = holder.environ()
        environ.pop("CLINIC_PHOTO_TEST_SESSION_AUTHOR")
        with self.assertRaises(http_harness.ConfigError) as raised:
            http_harness.load_config(environ)
        self.assertIn("CLINIC_PHOTO_TEST_SESSION_AUTHOR", str(raised.exception))

    def test_no_env_command_returns_configuration_failure(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = http_harness.main(["--check"], {})
        self.assertEqual(code, 3)
        output = buffer.getvalue()
        self.assertIn(http_harness.BASE_URL_ENV, output)
        self.assertIn(http_harness.MANIFEST_ENV, output)

    def test_check_flag_is_required_and_never_enables_a_request(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = http_harness.main([], {})
        self.assertEqual(code, 2)
        self.assertIn("pass --check", buffer.getvalue())

    def test_test_module_configuration_error_is_not_a_skip(self):
        from clinic_photos.tests import test_http_photo_access

        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(
                http_harness, "build_opener", side_effect=AssertionError
            ):
                with self.assertRaises(RuntimeError) as raised:
                    test_http_photo_access.setUpModule()
        self.assertNotIsInstance(raised.exception, unittest.SkipTest)
        self.assertIn("prerequisites", str(raised.exception))


class TransportHardeningTests(unittest.TestCase):
    def setUp(self):
        self.config, self.holder = config_for(self)

    def test_requests_are_get_only_and_redirects_are_disabled(self):
        request = http_harness.build_request(self.config, "author.direct.original.allowed")
        self.assertEqual(request.get_method(), "GET")
        opener = http_harness.build_opener()
        self.assertTrue(
            any(
                isinstance(handler, http_harness.NoRedirectHandler)
                for handler in opener.handlers
            )
        )
        self.assertFalse(
            any(
                isinstance(handler, urllib.request.HTTPCookieProcessor)
                for handler in opener.handlers
            )
        )
        # ProxyHandler is intentionally constructed with an empty map; urllib does
        # not register it as a normal handler, and no ambient proxy map is present.
        self.assertFalse(getattr(opener, "proxies", None))
        self.assertFalse(
            [
                handler.proxies
                for handler in opener.handlers
                if isinstance(handler, urllib.request.ProxyHandler) and handler.proxies
            ]
        )

    def test_redirect_response_is_not_followed(self):
        self.assertIsNone(
            http_harness.NoRedirectHandler().redirect_request(
                None, None, 302, "Found", {}, "https://elsewhere.invalid/"
            )
        )

    def test_session_cookie_is_per_request_and_guest_sends_none(self):
        author = http_harness.build_request(
            self.config, "author.direct.original.allowed"
        )
        headers = dict(author.header_items())
        self.assertEqual(headers["Cookie"], self.holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"])
        self.assertNotIn("Authorization", headers)

        guest = http_harness.build_request(
            self.config, "guest.direct.original.denied"
        )
        guest_headers = {key.lower() for key in dict(guest.header_items())}
        self.assertNotIn("cookie", guest_headers)
        self.assertNotIn("authorization", guest_headers)

    def test_guest_never_inherits_an_existing_session_environment(self):
        guest_case = "guest.direct.original.denied"
        environ = self.holder.environ()
        environ["CLINIC_PHOTO_TEST_SESSION_AUTHOR"] = "sid=LEAKED"
        config = http_harness.load_config(environ)
        request = http_harness.build_request(config, guest_case)
        self.assertNotIn("Cookie", dict(request.header_items()))

    def test_request_urls_are_exact_recorded_routes(self):
        manifest = self.config.manifest
        request = http_harness.build_request(self.config, "author.download.original.allowed")
        self.assertEqual(
            request.full_url,
            self.config.base_url
            + "/api/method/frappe.handler.download_file"
            + "?"
            + urllib.parse.urlencode(
                {"file_url": manifest["files"]["original"]["path"]}
            ),
        )
        zip_request = http_harness.build_request(
            self.config, "author.zip.protected.allowed"
        )
        self.assertIn("/api/method/frappe.core.api.file.zip_files?", zip_request.full_url)
        self.assertIn("files=", zip_request.full_url)

    def test_timeout_is_tight_and_capped(self):
        self.assertGreaterEqual(self.config.timeout, 5)
        self.assertLessEqual(self.config.timeout, 30)
        opener = FakeOpener(
            FakeResponse(
                body=ORIGINAL_BYTES,
                headers={
                    "Cache-Control": "no-store",
                    "Pragma": "no-cache",
                    "Expires": "0",
                    "Content-Type": "image/png",
                },
            )
        )
        http_harness.execute_case(
            self.config, "author.direct.original.allowed", opener=opener
        )
        self.assertEqual(opener.calls[0][1], self.config.timeout)


class BodyAndHeaderTests(unittest.TestCase):
    def setUp(self):
        self.config, self.holder = config_for(self)
        self.manifest = self.config.manifest

    def verify(self, case_id, body, **kwargs):
        case = case_for(self.manifest, case_id)
        return http_harness.verify_response(
            self.manifest, case, result_for(self.manifest, case, body, **kwargs)
        )

    def test_allowed_body_must_match_hash_and_size_exactly(self):
        evidence = self.verify("author.direct.original.allowed", ORIGINAL_BYTES)
        self.assertEqual(evidence["body_sha256"], hashlib.sha256(ORIGINAL_BYTES).hexdigest())
        with self.assertRaises(http_harness.ProbeError):
            self.verify("author.direct.original.allowed", ORIGINAL_BYTES + b"x")
        with self.assertRaises(http_harness.ProbeError):
            self.verify("author.direct.original.allowed", b"")
        with self.assertRaises(http_harness.ProbeError):
            self.verify("author.direct.original.allowed", ANNOTATION_BYTES)

    def test_cache_trio_is_exact_on_allowed_and_denied_responses(self):
        self.verify("author.direct.original.allowed", ORIGINAL_BYTES)
        self.verify("guest.direct.original.denied", b'{"exc_type":"PermissionError"}')
        for header, value in (
            ("Cache-Control", "private,max-age=3600"),
            ("Pragma", "cache"),
            ("Expires", "Wed, 21 Oct 2026 07:28:00 GMT"),
        ):
            with self.subTest(header=header):
                with self.assertRaises(http_harness.ProbeError):
                    self.verify(
                        "author.direct.original.allowed",
                        ORIGINAL_BYTES,
                        headers={header: value},
                    )

        duplicate = email.message.Message()
        duplicate["Cache-Control"] = "no-store"
        duplicate["Cache-Control"] = "private"
        duplicate["Pragma"] = "no-cache"
        duplicate["Expires"] = "0"
        duplicate["Content-Type"] = "image/png"
        case = case_for(self.manifest, "author.direct.original.allowed")
        with self.assertRaises(http_harness.ProbeError):
            http_harness.verify_response(
                self.manifest,
                case,
                http_harness.HttpResult(
                    status=200, headers=duplicate, body=ORIGINAL_BYTES
                ),
            )

    def test_denial_body_must_not_contain_protected_fragments(self):
        self.verify("guest.direct.original.denied", b'{"exc_type":"PermissionError"}')
        for body in (
            ORIGINAL_BYTES,
            b"prefix " + ORIGINAL_BYTES,
            b'{"marker":"SYNTHETIC-ANNOTATION-NOT-REAL"}',
            b'{"file":"/private/files/clinic-photo-11111111-1111-1111-1111-111111111111.png"}',
            b'{"file":"FILE-SYNTHETIC-ORIGINAL-1111111111111111"}',
        ):
            with self.subTest(body=body):
                with self.assertRaises(http_harness.ProbeError):
                    self.verify("guest.direct.original.denied", body)

    def test_denial_must_not_hand_back_an_internal_protected_redirect(self):
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "guest.direct.original.denied",
                b'{"exc_type":"PermissionError"}',
                headers={"X-Accel-Redirect": "/protected/private/files/secret.png"},
            )

    def test_read_cap_rejects_oversize_and_truncated_bodies(self):
        with self.assertRaises(http_harness.ProbeError):
            http_harness.read_capped(
                FakeResponse(body=b"a" * 10), max_bytes=4
            )
        with self.assertRaises(http_harness.ProbeError):
            http_harness.read_capped(
                FakeResponse(body=b"abc", declared=10), max_bytes=16
            )
        self.assertEqual(
            http_harness.read_capped(FakeResponse(body=b"abc", declared=3), max_bytes=16),
            b"abc",
        )

    def test_body_cap_prevents_false_acceptance_of_truncated_protected_data(self):
        with mock.patch.object(http_harness, "MAX_BODY_BYTES", 8):
            response = FakeResponse(body=ORIGINAL_BYTES)
            opener = FakeOpener(response)
            with self.assertRaises(http_harness.ProbeError):
                http_harness.execute_case(
                    self.config,
                    "author.direct.original.allowed",
                    opener=opener,
                )
        self.assertEqual(len(opener.calls), 1)

    def test_http_error_status_is_measured_not_followed_as_success(self):
        case = case_for(self.manifest, "author.direct.original.allowed")
        with self.assertRaises(http_harness.ProbeError):
            http_harness.verify_response(
                self.manifest, case, result_for(self.manifest, case, ORIGINAL_BYTES, status=403)
            )


class ZipInspectionTests(unittest.TestCase):
    def setUp(self):
        self.config, self.holder = config_for(self)
        self.manifest = self.config.manifest

    def verify(self, case_id, body, **kwargs):
        case = case_for(self.manifest, case_id)
        headers = dict(kwargs.pop("headers", {}))
        if zipfile.is_zipfile(io.BytesIO(body)):
            headers.setdefault("Content-Type", "application/zip")
        return http_harness.verify_response(
            self.manifest,
            case,
            result_for(
                self.manifest,
                case,
                body,
                headers=headers,
                **kwargs,
            ),
        )

    def allowed_members(self):
        manifest = self.manifest
        return [
            (manifest["assets"][name]["file_name"], asset_bytes(name))
            for name in ("original", "annotation")
        ]

    def test_allowed_zip_members_match_expected_bytes(self):
        evidence = self.verify(
            "author.zip.protected.allowed", zip_bytes(self.allowed_members())
        )
        self.assertEqual(evidence["zip_members"], 2)

    def test_allowed_zip_does_not_compare_archive_hash(self):
        first = zip_bytes(self.allowed_members(), date_time=(2026, 11, 7, 1, 0, 0))
        second = zip_bytes(self.allowed_members(), date_time=(2020, 1, 1, 1, 0, 0))
        self.assertNotEqual(hashlib.sha256(first).hexdigest(), hashlib.sha256(second).hexdigest())
        self.verify("author.zip.protected.allowed", first)
        self.verify("author.zip.protected.allowed", second)

    def test_allowed_zip_rejects_wrong_member_bytes_and_extra_members(self):
        members = self.allowed_members()
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "author.zip.protected.allowed",
                zip_bytes([members[0], (members[1][0], b"wrong")]),
            )
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "author.zip.protected.allowed",
                zip_bytes(members + [("extra.txt", b"extra")]),
            )

    def test_denied_zip_must_not_contain_protected_members_or_bytes(self):
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "guest.zip.protected.denied",
                zip_bytes([self.allowed_members()[0]]),
            )
        with self.assertRaises(http_harness.ProbeError):
            # Protected bytes under an unrelated member name are still protected.
            self.verify(
                "guest.zip.protected.denied",
                zip_bytes([("error.txt", ORIGINAL_BYTES)]),
            )
        self.verify("guest.zip.protected.denied", b'{"exc_type":"PermissionError"}')

    def test_mixed_zip_ships_only_the_shared_unrelated_member(self):
        unrelated = (
            self.manifest["assets"]["unrelated"]["file_name"],
            UNRELATED_BYTES,
        )
        self.verify("shared_nonclinician.zip.mixed.denied_protected", zip_bytes([unrelated]))
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "shared_nonclinician.zip.mixed.denied_protected",
                zip_bytes([unrelated, self.allowed_members()[0]]),
            )
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "shared_nonclinician.zip.mixed.denied_protected",
                zip_bytes([("unrelated-copy.png", ORIGINAL_BYTES), unrelated]),
            )

    def test_zip_member_traversal_and_encryption_are_rejected(self):
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "author.zip.protected.allowed",
                zip_bytes(self.allowed_members() + [("../escape.txt", b"x")]),
            )

    def test_prefixed_valid_zip_with_renamed_protected_payload_is_denied(self):
        archive = zip_bytes([("renamed.bin", ORIGINAL_BYTES)])
        prefixed = b"PREFIX-DATA-BEFORE-ZIP\x00" + archive
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(prefixed)))
        with self.assertRaises(http_harness.ProbeError):
            self.verify("guest.zip.protected.denied", prefixed)

    def test_denial_body_equal_to_protected_expected_bytes_is_denied(self):
        with self.assertRaises(http_harness.ProbeError):
            self.verify("guest.zip.protected.denied", ORIGINAL_BYTES)

    def test_declared_zip_that_cannot_be_inspected_fails_closed(self):
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "guest.zip.protected.denied",
                b'{"exc_type":"PermissionError"}',
                headers={"Content-Type": "application/zip"},
            )

    def test_zip_member_cap_remains_enforced(self):
        too_many = zip_bytes([(f"member-{index}.bin", b"") for index in range(33)])
        with self.assertRaises(http_harness.ProbeError):
            self.verify("author.zip.protected.allowed", too_many)

    def test_full_protected_prefix_before_a_valid_empty_zip_is_denied(self):
        archive = zip_bytes([])
        with zipfile.ZipFile(io.BytesIO(archive)) as empty:
            self.assertEqual(empty.infolist(), [])
        body = ORIGINAL_BYTES + archive
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
        self.assertNotEqual(
            hashlib.sha256(body).hexdigest(),
            self.manifest["assets"]["original"]["sha256"],
        )
        with self.assertRaises(http_harness.ProbeError):
            self.verify("guest.zip.protected.denied", body)

    def test_zip_archive_comment_carrying_protected_fragments_is_denied(self):
        # A valid archive whose only protected payload lives in the archive comment.
        body = zip_bytes([], comment=ORIGINAL_BYTES)
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(body)))
        with self.assertRaises(http_harness.ProbeError):
            self.verify("guest.zip.protected.denied", body)

    def test_mixed_zip_archive_level_leak_outside_allowed_member_is_denied(self):
        unrelated = (
            self.manifest["assets"]["unrelated"]["file_name"],
            UNRELATED_BYTES,
        )
        preamble = b"ARCHIVE-PREAMBLE-LEAK:" + ORIGINAL_BYTES + zip_bytes([unrelated])
        self.assertTrue(zipfile.is_zipfile(io.BytesIO(preamble)))
        with self.assertRaises(http_harness.ProbeError):
            self.verify("shared_nonclinician.zip.mixed.denied_protected", preamble)
        commented = zip_bytes([unrelated], comment=ORIGINAL_BYTES)
        with self.assertRaises(http_harness.ProbeError):
            self.verify(
                "shared_nonclinician.zip.mixed.denied_protected", commented
            )

    def test_allowed_protected_zip_is_not_judged_by_forbidden_needles(self):
        # Authorized archives legitimately carry protected member data; only the
        # explicitly forbidden assets of a denial/mixed case are scanned for.
        body = zip_bytes(self.allowed_members())
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            self.assertEqual(
                archive.read(self.manifest["assets"]["original"]["file_name"]),
                ORIGINAL_BYTES,
            )
        evidence = self.verify("author.zip.protected.allowed", body)
        self.assertEqual(evidence["zip_members"], 2)

    def mixed_body(self):
        return zip_bytes(
            [(self.manifest["assets"]["unrelated"]["file_name"], UNRELATED_BYTES)]
        )

    def test_mixed_zip_must_not_leak_forbidden_protected_data_in_headers(self):
        original = self.manifest["assets"]["original"]
        original_file = self.manifest["files"]["original"]
        body = self.mixed_body()
        # Control: the same clean archive with neutral delivery headers verifies.
        self.verify(
            "shared_nonclinician.zip.mixed.denied_protected",
            body,
            headers={"X-Request-Id": "synthetic-request-id"},
        )
        leaked = (
            ("X-Leaked-Photo", ORIGINAL_BYTES.decode("utf-8").strip()),
            ("X-Leaked-File-Name", original["file_name"]),
            ("X-Leaked-Path", original_file["path"]),
            ("X-Leaked-Fid", original_file["fid"]),
            ("X-Leaked-Hash", original["sha256"]),
            ("X-Accel-Redirect", "/protected" + original_file["path"]),
        )
        for name, value in leaked:
            with self.subTest(header=name):
                with self.assertRaises(http_harness.ProbeError):
                    self.verify(
                        "shared_nonclinician.zip.mixed.denied_protected",
                        body,
                        headers={name: value},
                    )

    def test_mixed_zip_keeps_the_native_archive_delivery_redirect(self):
        # A mixed archive response is the delivery itself; a redirect naming that
        # archive is not protected-file disclosure and must not be refused blindly.
        self.verify(
            "shared_nonclinician.zip.mixed.denied_protected",
            self.mixed_body(),
            headers={"X-Accel-Redirect": "/protected/clinic-photo-mixed-archive.zip"},
        )

    def test_denied_body_must_not_leak_annotation_fragment_in_headers(self):
        body = b'{"exc_type":"PermissionError"}'
        self.verify("guest.direct.original.denied", body)
        for name, value in (
            ("X-Leaked-Annotation", ANNOTATION_BYTES.decode("utf-8").strip()),
            ("X-Leaked-Annotation-Path", self.manifest["files"]["annotation"]["path"]),
        ):
            with self.subTest(header=name):
                with self.assertRaises(http_harness.ProbeError):
                    self.verify(
                        "guest.direct.original.denied", body, headers={name: value}
                    )


class SessionRedactionTests(unittest.TestCase):
    def test_redact_removes_all_session_values(self):
        environ = {
            "CLINIC_PHOTO_TEST_SESSION_AUTHOR": "sid=AUTHOR-SECRET",
            "CLINIC_PHOTO_TEST_SESSION_OTHER_CLINICIAN": "sid=OTHER-SECRET",
        }
        text = "failed with sid=AUTHOR-SECRET and sid=OTHER-SECRET"
        redacted = http_harness.redact(text, environ)
        self.assertNotIn("AUTHOR-SECRET", redacted)
        self.assertNotIn("OTHER-SECRET", redacted)
        self.assertEqual(redacted.count("[redacted]"), 2)

    def test_probe_errors_do_not_carry_session_values(self):
        holder = ManifestFile(self)
        config = http_harness.load_config(holder.environ())
        secret = holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"]
        opener = FakeOpener(FakeResponse(status=500, body=b"boom"))
        case = case_for(config.manifest, "author.direct.original.allowed")
        with self.assertRaises(http_harness.ProbeError) as raised:
            http_harness.verify_response(
                config.manifest,
                case,
                http_harness.HttpResult(status=500, headers={}, body=b"boom"),
            )
        self.assertNotIn(secret, str(raised.exception))
        self.assertNotIn("boom", str(raised.exception))

    def test_actual_unittest_failure_traceback_never_prints_reflected_session(self):
        holder = ManifestFile(self)
        config = http_harness.load_config(holder.environ())
        secret = holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"]
        response = FakeResponse(
            status=200,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Expires": "0",
                "Content-Type": "application/octet-stream; reflected=" + secret,
                "X-Reflected-Cookie": "Cookie: " + secret,
            },
            body=ORIGINAL_BYTES,
        )

        class ReflectedFailure(unittest.TestCase):
            def test_reflected_header(self):
                http_harness.execute_case(
                    config,
                    "author.direct.original.allowed",
                    opener=FakeOpener(response),
                )

        result = unittest.TestResult()
        ReflectedFailure("test_reflected_header").run(result)
        traces = [trace for _, trace in result.failures + result.errors]
        self.assertEqual(len(traces), 1, (result.failures, result.errors))
        self.assertNotIn(secret, traces[0])
        self.assertIn("Content-Type does not match the recorded value", traces[0])

    def test_bad_status_line_during_open_is_a_fixed_probe_error(self):
        holder = ManifestFile(self)
        config = http_harness.load_config(holder.environ())
        secret = holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"]
        fault = http.client.BadStatusLine('Bad status line "Cookie: ' + secret + '"')
        with self.assertRaises(http_harness.ProbeError) as raised:
            http_harness.execute_case(
                config,
                "author.direct.original.allowed",
                opener=FakeOpener(error=fault),
            )
        message = str(raised.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("Cookie", message)
        self.assertIn("BadStatusLine", message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)

    def test_bad_status_line_during_body_read_is_a_fixed_probe_error(self):
        holder = ManifestFile(self)
        config = http_harness.load_config(holder.environ())
        secret = holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"]
        response = ParserFaultResponse(
            status=200,
            headers={
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Expires": "0",
                "Content-Type": "image/png",
            },
            error=http.client.BadStatusLine("status text: Cookie: " + secret),
        )
        with self.assertRaises(http_harness.ProbeError) as raised:
            http_harness.execute_case(
                config,
                "author.direct.original.allowed",
                opener=FakeOpener(response),
            )
        message = str(raised.exception)
        self.assertNotIn(secret, message)
        self.assertNotIn("Cookie", message)
        self.assertIn("BadStatusLine", message)
        self.assertIsNone(raised.exception.__cause__)
        self.assertTrue(raised.exception.__suppress_context__)
        self.assertTrue(response.closed)

    def test_nested_unittest_bad_status_line_traceback_has_no_session(self):
        holder = ManifestFile(self)
        config = http_harness.load_config(holder.environ())
        secret = holder.environ()["CLINIC_PHOTO_TEST_SESSION_AUTHOR"]
        opener = FakeOpener(
            error=http.client.BadStatusLine('Bad status line "Cookie: ' + secret + '"')
        )

        class ParserFault(unittest.TestCase):
            def test_parser_fault(self):
                http_harness.execute_case(
                    config, "author.direct.original.allowed", opener=opener
                )

        result = unittest.TestResult()
        ParserFault("test_parser_fault").run(result)
        traces = [trace for _, trace in result.failures + result.errors]
        self.assertEqual(len(traces), 1, (result.failures, result.errors))
        self.assertNotIn(secret, traces[0])
        self.assertNotIn("Cookie", traces[0])
        self.assertIn("BadStatusLine", traces[0])


if __name__ == "__main__":
    unittest.main()
